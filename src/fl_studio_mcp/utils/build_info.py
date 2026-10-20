"""Fingerprint captured at server startup, so later disk edits cannot impersonate a reload.

Import cost matters here: this module is pulled in by every tool module, so it runs
in every MCP process and in every test worker. It therefore reads git metadata
straight from ``.git`` files instead of spawning git, and defers the one genuinely
expensive query (is the working tree dirty?) to :func:`build_metadata`, which only
tool responses call.
"""

import hashlib
import os
import subprocess
from pathlib import Path

_cached_working_tree: dict = {}


def _git_head(repo: Path) -> str | None:
    """Resolve HEAD by reading refs, never by spawning git."""
    gitdir = repo / ".git"
    try:
        head = (gitdir / "HEAD").read_text("utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return head or None
    ref_name = head[5:].strip()
    try:
        return (gitdir / ref_name).read_text("utf-8").strip() or None
    except OSError:
        pass
    try:
        for line in (gitdir / "packed-refs").read_text("utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref_name:
                return parts[0]
    except OSError:
        pass
    return None


def working_tree_changed(repo: Path | None = None) -> bool | None:
    """Whether the checkout has uncommitted or untracked changes, or None.

    Cached for the process, and deliberately NOT part of the default tool payload:
    this shells out to git, and in a server spawned with piped stdio a helper
    process (git's fsmonitor daemon) can hold the inherited handles and hang the
    call. So: stdin and stderr are detached, fsmonitor is disabled for the
    invocation, the child is killed on timeout, and pipes are drained defensively.
    """
    if "value" in _cached_working_tree:
        return _cached_working_tree["value"]
    if repo is None:
        repo = Path(__file__).resolve().parents[3]
    value = None
    if (repo / ".git").exists():
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                ["git", "-c", "core.fsmonitor=false", "status", "--porcelain",
                 "--untracked-files=normal"],
                cwd=repo,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=flags,
            )
        except OSError:
            proc = None
        if proc is not None:
            try:
                out, _ = proc.communicate(timeout=5)
                value = bool((out or "").strip()) if proc.returncode == 0 else None
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                value = None
    _cached_working_tree["value"] = value
    return value


def fingerprint() -> dict:
    package = Path(__file__).resolve().parents[1]
    repo = package.parents[1]
    digest = hashlib.sha256()
    # Maps are executable control contracts too; a changed calibration must
    # change the loaded-build fingerprint even when Python sources are stable.
    sources = list(package.rglob("*.py")) + list(package.rglob("*.json"))
    for path in sorted(sources):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    info = {"host_source_sha256": digest.hexdigest(), "source_package": str(package)}
    head = _git_head(repo) if (repo / ".git").exists() else None
    if head:
        info["base_commit"] = head
    elif (repo / ".git").exists():
        info["git_metadata_available"] = False
    lock = repo / "uv.lock"
    if lock.is_file():
        info["dependency_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
    info["captured_at"] = "server module import; reconnect after changing source"
    return info


LOADED_BUILD = fingerprint()


def build_metadata(deep: bool = False) -> dict:
    """LOADED_BUILD, plus the working-tree state only when explicitly requested.

    Tool responses use this. ``deep`` is opt-in because it shells out to git; the
    default keeps every response free of subprocesses.
    """
    return dict(LOADED_BUILD, working_tree_changed=working_tree_changed() if deep else None)
