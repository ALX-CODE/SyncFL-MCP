"""Resolve the optional Windows MCP helper without a developer-specific install."""

import os
import shutil
from pathlib import Path


def resolve_windows_server():
    override = os.environ.get("FL_MCP_WINDOWS_SERVER")
    if override:
        if not Path(override).is_file():
            raise RuntimeError("FL_MCP_WINDOWS_SERVER does not name an existing executable")
        return str(Path(override).resolve())
    # Reuse this machine's already configured Windows server, never its secrets.
    try:
        import tomllib

        config = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        settings = tomllib.loads(config.read_text(encoding="utf-8"))
        command = settings.get("mcp_servers", {}).get("windows", {}).get("command", "")
        if command and Path(command).is_file():
            return command
    except (ImportError, OSError, ValueError):
        pass
    command = shutil.which("Sbroenne.WindowsMcp.exe")
    if command:
        return command
    for candidate in (
        r"C:\Tools\WindowsMCP\local-1.3.22.1\Sbroenne.WindowsMcp.exe",
        r"C:\Tools\WindowsMCP\alx-stable-5a8aa3a\Sbroenne.WindowsMcp.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("Windows MCP helper not found; set FL_MCP_WINDOWS_SERVER")
