"""Preview or safely merge SyncFL into a Claude MCP configuration."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(path: Path, root: Path = ROOT, *, apply: bool = False, replace: bool = False) -> dict:
    """Preserve unrelated configuration; refuse invalid JSON and entry conflicts."""
    path, root = path.expanduser().absolute(), root.resolve()
    before = path.read_bytes() if path.exists() else None
    document = json.loads(before.decode("utf-8-sig")) if before is not None else {}
    if not isinstance(document, dict) or not isinstance(document.get("mcpServers", {}), dict):
        raise ValueError("Configuration and mcpServers must be JSON objects")
    updated = copy.deepcopy(document)
    servers = updated.setdefault("mcpServers", {})
    entry = {
        "command": "uv",
        "args": ["run", "--directory", str(root), "syncfl-mcp"],
        "env": {"FL_MCP_INTERACTION_POLICY": "background"},
    }
    existing = servers.get("syncfl")
    if existing is not None and existing != entry:
        if not isinstance(existing, dict):
            raise ValueError("Existing syncfl entry is not an object")
        arguments = existing.get("args", [])
        same_project = (
            isinstance(arguments, list)
            and "--directory" in arguments
            and arguments.index("--directory") + 1 < len(arguments)
            and Path(arguments[arguments.index("--directory") + 1]).resolve() == root
        )
        if not (same_project or replace):
            raise ValueError("A different syncfl entry exists; review it before using --replace")
        entry = {**existing, **entry, "env": {**entry["env"], **existing.get("env", {})}}
    servers["syncfl"] = entry
    changed = document != updated
    backup = None
    if apply and changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_bytes() if path.exists() else None
        if current != before:
            raise RuntimeError("Configuration changed while preparing the update")
        if before is not None:
            backup = path.with_name(path.name + ".syncfl-backup-" + uuid.uuid4().hex)
            shutil.copy2(path, backup)
        descriptor, temporary = tempfile.mkstemp(prefix=".syncfl-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(updated, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                os.chmod(temporary, path.stat().st_mode)
            if (path.read_bytes() if path.exists() else None) != before:
                raise RuntimeError("Configuration changed before publication")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {
        "success": True,
        "applied": apply and changed,
        "changed": changed,
        "config": str(path),
        "backup": str(backup) if backup else None,
        "entry": {"syncfl": entry},
    }


def default_config(client: str) -> Path:
    if client == "code":
        return Path.home() / ".claude.json"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if not base:
            raise ValueError("APPDATA is missing; provide --config explicitly")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    base = (
        Path.home() / "Library" / "Application Support"
        if sys.platform == "darwin"
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    )
    return base / "Claude" / "claude_desktop_config.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("desktop", "code"), default="desktop")
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="Write the reviewed entry and retain a backup"
    )
    parser.add_argument("--replace", action="store_true", help="Replace a conflicting syncfl entry")
    args = parser.parse_args()
    try:
        result = configure(
            args.config or default_config(args.client), apply=args.apply, replace=args.replace
        )
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
