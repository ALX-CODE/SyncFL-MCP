"""Export a new source-only folder without private media or Git history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOTS = {".git", "projects", "references", "assets"}
MEDIA_SUFFIXES = {
    ".wav",
    ".mp3",
    ".mp4",
    ".flac",
    ".ogg",
    ".flp",
    ".mid",
    ".midi",
    ".fst",
    ".serumpreset",
    ".fxp",
    ".pickle",
    ".pkl",
}
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{40,}"),
    re.compile(
        r"https?://[^\s\"'<>]+[?&](?:token|access_token|api_key|signature)=[^\s\"'<>]+", re.I
    ),
)
HOME_PATH = re.compile(r"(?:[a-z]:/[u]sers/|/[u]sers/|/[h]ome/)(?!\$?\{)([^/\s\x00\"'<>{}]+)", re.I)
SYSTEM_PROFILES = {"public", "default", "all users", "%username%", "$user", "$username"}


def content_texts(data: bytes):
    """Inspect UTF-8 and Windows wide strings even in an approved binary."""
    yield data.decode("utf-8", errors="replace")
    if b"\0" in data:
        for pattern, encoding in (
            (rb"(?:[\x20-\x7e]\x00){6,}", "utf-16-le"),
            (rb"(?:\x00[\x20-\x7e]){6,}", "utf-16-be"),
        ):
            yield "\n".join(value.decode(encoding) for value in re.findall(pattern, data))


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=30, check=True)
    return result.stdout.decode("utf-8")


def safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe release path: {value!r}")
    if path.parts[0] in PRIVATE_ROOTS or ":" in value:
        raise ValueError(f"Private or absolute release path: {value!r}")
    return path.as_posix()


def selected_paths(root: Path) -> list[str]:
    policy = json.loads((root / "public-release.json").read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported public release policy")
    directories = [safe_relative(p) for p in policy["include_directories"]]
    explicit = {safe_relative(p) for p in policy["include_files"]}
    reviewed = {
        safe_relative(p): entry for p, entry in policy.get("reviewed_binary_files", {}).items()
    }
    tracked = set(git(root, "ls-files", "-z").rstrip("\0").split("\0"))
    missing = explicit - tracked
    if missing:
        raise ValueError("Release files must exist in the Git index: " + ", ".join(sorted(missing)))
    paths = sorted(
        p
        for p in tracked
        if p in explicit or any(p.startswith(directory + "/") for directory in directories)
    )
    for name in paths:
        safe_relative(name)
        path = root / name
        if (
            path.suffix.lower() in MEDIA_SUFFIXES
            and name not in reviewed
            or path.name.startswith(".env")
        ):
            raise ValueError(f"Private media/config is not publishable: {name}")
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError(f"Release input must be an in-tree regular file: {name}")
        if name in reviewed:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != reviewed[name]["sha256"]:
                raise ValueError(f"Reviewed binary changed; inspect its provenance again: {name}")
    return paths


def scan_content(
    name: str, data: bytes, *, home: Path | None = None, reviewed_binary: bool = False
) -> None:
    home_text = str(home or Path.home()).replace("\\", "/").rstrip("/").lower()
    for text in content_texts(data):
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Credential-like content requires review: {name}")
        normalized = text.replace("\\\\", "\\").replace("\\", "/").lower()
        if len(home_text) > 5 and home_text in normalized:
            raise ValueError(f"Current user's absolute home path requires redaction: {name}")
        if any(match[1] not in SYSTEM_PROFILES for match in HOME_PATH.finditer(normalized)):
            raise ValueError(f"Personal home path requires redaction: {name}")
    if b"\0" in data:
        if reviewed_binary:
            return
        if (
            name.startswith("native/")
            and "/runtime/" in name
            and Path(name).suffix in {".exe", ".dll", ".pri"}
        ):
            return
        raise ValueError(f"Unexpected binary content requires review: {name}")


def check_runtime_files(root: Path, files: dict[str, bytes]) -> None:
    for bridge in ("midi_bridge", "background_bridge", "midi_transport", "audio_tempo"):
        prefix = f"native/{bridge}/runtime/"
        manifest_name = prefix + "manifest.json"
        if (root / manifest_name).exists() and manifest_name not in files:
            raise ValueError(f"Runtime manifest was excluded: {manifest_name}")
        if manifest_name not in files:
            continue
        manifest = json.loads(files[manifest_name])
        for relative, metadata in manifest["files"].items():
            name = prefix + safe_relative(relative)
            data = files.get(name)
            if data is None or len(data) != metadata["size"]:
                raise ValueError(f"Missing or resized runtime file: {name}")
            if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                raise ValueError(f"Runtime hash mismatch: {name}")


def publish_directory(staging: Path, output: Path) -> None:
    # Windows scanners can briefly hold a newly written executable or directory.
    # Keep publication atomic and bounded; never replace a competing destination.
    for attempt in range(8):
        if output.exists():
            raise ValueError("Output was created by another operation")
        try:
            os.rename(staging, output)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == 7:
                raise
            time.sleep(0.25)


def export(
    root: Path, output: Path, archive: Path | None = None, allow_dirty: bool = False
) -> dict:
    root = root.resolve()
    output = output.resolve()
    archive = archive.resolve() if archive else None
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Output must be new and outside the source repository")
    if archive and (
        archive.exists() or archive.is_relative_to(root) or archive.is_relative_to(output)
    ):
        raise ValueError("Archive must be new and outside source/output folders")
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=no").strip())
    if dirty and not allow_dirty:
        raise ValueError("Commit tracked changes first, or explicitly use --allow-dirty for review")
    paths = selected_paths(root)
    reviewed = json.loads((root / "public-release.json").read_text(encoding="utf-8")).get(
        "reviewed_binary_files", {}
    )
    files = {name: (root / name).read_bytes() for name in paths}
    for name, data in files.items():
        scan_content(name, data, reviewed_binary=name in reviewed)
    check_runtime_files(root, files)
    manifest = {
        "schema_version": 1,
        "project_name": "SyncFL",
        "repository_name": "SyncFL-MCP",
        "source_commit": git(root, "rev-parse", "HEAD").strip(),
        "source_dirty": dirty,
        "git_history_included": False,
        "scope": "Allowlisted source only; no private projects, references or media",
        "content_check": (
            "Known credentials, tokenized URLs and personal home paths, "
            "including binary strings; not exhaustive"
        ),
        "reviewed_binary_files": reviewed,
        "files": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ],
    }
    files["PUBLIC-MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fl-public-", dir=output.parent) as temporary:
        staging = Path(temporary) / "source"
        staging.mkdir()
        for name, data in files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        publish_directory(staging, output)
    if archive:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in files.items():
                bundle.writestr("SyncFL-MCP/" + name, data)
    return {
        "success": True,
        "output": str(output),
        "archive": str(archive) if archive else None,
        "files": len(paths),
        "bytes": sum(len(data) for data in files.values()),
        "source_commit": manifest["source_commit"],
        "source_dirty": dirty,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    try:
        result = export(ROOT, args.output, args.archive, args.allow_dirty)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
