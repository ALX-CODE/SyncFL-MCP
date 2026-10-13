import json
import os
from pathlib import Path


def session_path():
    return Path(os.environ.get("FL_MCP_SESSION", str(Path.home() / ".flstudio-mcp-session.json")))


def load():
    path = session_path()
    return json.loads(path.read_text()) if path.exists() else {"track": 1, "slot": 0}


def save(value):
    from fl_studio_mcp.utils.protocol import atomic_json

    atomic_json(session_path(), value)
