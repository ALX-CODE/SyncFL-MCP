"""Verify every manifest-listed byte after cloning or unpacking a project."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def verify_bundle(directory: Path) -> dict:
    root = directory.resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest["files"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest must list at least one file")
    errors = []
    seen = set()
    total_bytes = 0
    for entry in entries:
        relative = entry["path"]
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative:
            raise ValueError(f"Unsafe manifest path: {relative}")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError(f"Manifest path escapes bundle: {relative}")
        identity = str(path).casefold()
        if identity in seen:
            raise ValueError(f"Duplicate manifest path: {relative}")
        seen.add(identity)
        if not path.is_file():
            errors.append(f"Missing file: {relative}")
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            errors.append(f"Content mismatch: {relative}")
    return {"ok": not errors, "files": len(entries), "bytes": total_bytes, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        result = verify_bundle(args.directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
