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


def _python(root: Path) -> Path:
    relative = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    return root / relative


def _is_syncfl(name: str, entry) -> bool:
    if name.casefold() in {
        "syncfl", "syncfl-mcp", "syncfl_mcp",
        "fl_studio", "fl-studio", "fl_studio_mcp", "fl-studio-mcp",
    }:
        return True
    if not isinstance(entry, dict):
        return False
    return any(
        marker in str(entry.get("command", "")).casefold()
        or marker in str(entry.get("args", "")).casefold()
        for marker in ("fl_studio_mcp", "fl-studio-mcp", "syncfl-mcp")
    )


def _same_project(entry: dict, root: Path) -> bool:
    arguments = entry.get("args", [])
    if not isinstance(arguments, list):
        return False
    command = entry.get("command")
    if isinstance(command, str) and Path(command).resolve() == _python(root):
        return arguments == ["-m", "fl_studio_mcp.server"]
    if "--directory" in arguments:
        index = arguments.index("--directory") + 1
        if index < len(arguments) and isinstance(arguments[index], str):
            return Path(arguments[index]).resolve() == root
    return False


def _merge_missing(destination: dict, source: dict) -> None:
    for key, value in source.items():
        if key not in destination:
            destination[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(destination[key], dict):
            _merge_missing(destination[key], value)


def configure(
    path: Path, root: Path = ROOT, *, apply: bool = False, replace: bool = False,
    overrides: dict | None = None,
) -> dict:
    """Preserve unrelated configuration; refuse invalid JSON and entry conflicts."""
    path, root = path.expanduser().absolute(), root.resolve()
    before = path.read_bytes() if path.exists() else None
    document = json.loads(before.decode("utf-8-sig")) if before is not None else {}
    if not isinstance(document, dict) or not isinstance(document.get("mcpServers", {}), dict):
        raise ValueError("Configuration and mcpServers must be JSON objects")
    updated = copy.deepcopy(document)
    servers = updated.setdefault("mcpServers", {})
    matches = [name for name, entry in servers.items() if _is_syncfl(name, entry)]
    # Prefer the canonical entry when merging duplicate names, preserving its settings.
    matches.sort(key=lambda name: name != "syncfl")
    entry = {}
    for name in matches:
        existing = servers[name]
        if not isinstance(existing, dict):
            raise ValueError("Existing syncfl entry is not an object")
        if not (_same_project(existing, root) or replace):
            raise ValueError("A different syncfl entry exists; review it before using --replace")
        _merge_missing(entry, existing)
    entry.update(command=str(_python(root)), args=["-m", "fl_studio_mcp.server"])
    environment = entry.setdefault("env", {})
    if not isinstance(environment, dict):
        raise ValueError("SyncFL environment must be an object")
    if overrides:
        environment.update(overrides)
    environment.pop("FL_MCP_INTERACTION_POLICY", None)
    for name in matches:
        servers.pop(name)
    servers["syncfl"] = entry
    changed = document != updated
    backup = None
    if apply and changed:
        if not _python(root).is_file():
            raise FileNotFoundError("Repository Python missing; run the installer first")
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
    parser.add_argument("--fl-user-data", help="Image-Line root, not its Settings folder")
    parser.add_argument("--midi-port", help="Exact dedicated MIDI output port name")
    parser.add_argument("--windows-server", help="Optional Windows MCP executable")
    args = parser.parse_args()
    overrides = {key: value for key, value in {
        "FL_USER_DATA_FOLDER": args.fl_user_data,
        "FL_MCP_MIDI_PORT": args.midi_port,
        "FL_MCP_WINDOWS_SERVER": args.windows_server,
    }.items() if value is not None}
    try:
        result = configure(
            args.config or default_config(args.client), apply=args.apply, replace=args.replace,
            overrides=overrides,
        )
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
