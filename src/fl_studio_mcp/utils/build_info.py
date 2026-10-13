"""Fingerprint captured at server startup, so later disk edits cannot impersonate a reload."""

import hashlib
import subprocess
from pathlib import Path


def fingerprint():
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
    if (repo / ".git").exists():
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                    capture_output=True, text=True, timeout=2, check=True)
            info["base_commit"] = result.stdout.strip()
            state = subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal"],
                                   cwd=repo, capture_output=True, text=True, timeout=2, check=True)
            info["working_tree_changed"] = bool(state.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            info["git_metadata_available"] = False
    lock = repo / "uv.lock"
    if lock.is_file():
        info["dependency_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
    info["captured_at"] = "server module import; reconnect after changing source"
    return info


LOADED_BUILD = fingerprint()
