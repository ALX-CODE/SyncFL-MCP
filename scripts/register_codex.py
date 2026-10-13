"""Check/apply Codex registration without exposing private config values."""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _python(root):
    relative = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    return Path(root).resolve() / relative


def _value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_value(key)} = {_value(item)}" for key, item in value.items()
        ) + " }"
    raise ValueError(f"Unsupported TOML value type: {type(value).__name__}")


def _header_path(line):
    """Parse quoted/dotted table names using TOML rather than splitting dots."""
    if not re.match(r"^\s*\[(?!\[)", line):
        return None
    try:
        node = tomllib.loads(line + "\n__fl_registration_marker__ = true\n")
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(node, dict) and len(node) == 1:
        key, node = next(iter(node.items()))
        if key == "__fl_registration_marker__":
            return path
        path.append(key)
    return None


def _merge_missing(destination, source):
    for key, value in source.items():
        if key not in destination:
            destination[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(destination[key], dict):
            _merge_missing(destination[key], value)


def render_registration(text, root, overrides=None):
    """Return new text and safe status; semantic validation guards unusual TOML."""
    parsed = tomllib.loads(text)
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict) or any(not isinstance(v, dict) for v in servers.values()):
        raise ValueError("mcp_servers must contain server tables")
    matches = [name for name, value in servers.items() if (
        name.casefold() in {
            "syncfl", "syncfl-mcp", "syncfl_mcp",
            "fl_studio", "fl-studio", "fl_studio_mcp", "fl-studio-mcp",
        }
        or any(marker in str(value.get("command", "")).casefold()
               or marker in str(value.get("args", "")).casefold()
               for marker in ("fl_studio_mcp", "fl-studio-mcp", "syncfl-mcp"))
    )]
    name = "fl_studio" if "fl_studio" in matches else (matches[0] if matches else "fl_studio")
    settings = copy.deepcopy(servers.get(name, {}))
    for duplicate in matches:
        _merge_missing(settings, servers[duplicate])
    root = Path(root).resolve()
    settings.update(command=str(_python(root)),
                    args=["-m", "fl_studio_mcp.server"], cwd=str(root))
    settings.setdefault("startup_timeout_sec", 30)
    settings.setdefault("tool_timeout_sec", 120)
    environment = settings.setdefault("env", {})
    if not isinstance(environment, dict):
        raise ValueError("SyncFL environment must be a table")
    if overrides:
        environment.update(overrides)
    environment.pop("FL_MCP_INTERACTION_POLICY", None)
    expected = copy.deepcopy(parsed)
    expected_servers = expected.setdefault("mcp_servers", {})
    for old in matches:
        expected_servers.pop(old)
    expected_servers[name] = settings
    if parsed == expected:
        return text, {"changed": False, "server": name, "removed_duplicates": 0}

    # Preserve unrelated text exactly. Refuse exotic inline/array/multiline table
    # layouts when the final semantic comparison cannot prove preservation.
    kept = []
    removing = False
    for line in text.splitlines(keepends=True):
        header = _header_path(line)
        if header is not None:
            removing = len(header) >= 2 and header[0] == "mcp_servers" and header[1] in matches
        elif re.match(r"^\s*\[\[", line):
            removing = False
        if not removing:
            kept.append(line)
    addition = "\n[mcp_servers." + _value(name) + "]\n"
    addition += "".join(f"{_value(key)} = {_value(value)}\n" for key, value in settings.items())
    candidate = "".join(kept).rstrip("\r\n") + "\n" + addition
    try:
        valid = tomllib.loads(candidate) == expected
    except tomllib.TOMLDecodeError:
        valid = False
    if not valid:
        raise ValueError("Nonstandard TOML layout: config untouched; normalize FL tables first")
    return candidate, {"changed": True, "server": name,
                       "removed_duplicates": max(0, len(matches) - 1)}


def register(config, root=ROOT, overrides=None, apply=False):
    config = Path(config).expanduser().resolve()
    original = config.read_bytes() if config.exists() else b""
    text = original.decode("utf-8-sig")
    updated, status = render_registration(text, root, overrides)
    status.update(applied=False, config=str(config))
    if apply and status["changed"]:
        executable = _python(root)
        if not executable.is_file():
            raise FileNotFoundError("Repository Python missing; run the installer first")
        config.parent.mkdir(parents=True, exist_ok=True)
        if (config.read_bytes() if config.exists() else b"") != original:
            raise RuntimeError("Codex config changed during registration; retry")
        if config.exists():
            backup = config.with_name(f"{config.name}.before-fl-mcp-{time.time_ns()}")
            shutil.copy2(config, backup)
            status["backup"] = str(backup)
        fd, temporary = tempfile.mkstemp(prefix=".fl-register-", dir=config.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(updated)
            if (config.read_bytes() if config.exists() else b"") != original:
                raise RuntimeError("Codex config changed during registration; retry")
            os.replace(temporary, config)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        status["applied"] = True
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--config", type=Path, default=(
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    ))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--fl-user-data", help="Image-Line root, not its Settings folder")
    parser.add_argument("--midi-port", help="Exact dedicated MIDI output port name")
    parser.add_argument("--windows-server", help="Optional Windows MCP executable")
    parser.add_argument("--tool-mode", choices=("discovery", "full"))
    args = parser.parse_args()
    overrides = {key: value for key, value in {
        "FL_USER_DATA_FOLDER": args.fl_user_data,
        "FL_MCP_MIDI_PORT": args.midi_port,
        "FL_MCP_WINDOWS_SERVER": args.windows_server,
        "FL_MCP_TOOL_MODE": args.tool_mode,
    }.items() if value is not None}
    try:
        result = register(args.config, args.root, overrides, args.apply)
    except (OSError, ValueError, RuntimeError) as exc:
        # TOML decoder errors can contain private source fragments; never print them.
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "message": "Registration failed; private config was not replaced."}))
        return 2
    print(json.dumps(dict(success=not result["changed"] or args.apply, **result), indent=2))
    return int(args.check and result["changed"])


if __name__ == "__main__":
    raise SystemExit(main())
