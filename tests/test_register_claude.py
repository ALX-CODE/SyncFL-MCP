"""Client configuration updates must preserve unrelated data and originals."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "register_claude", ROOT / "scripts/register_claude.py"
)
register = importlib.util.module_from_spec(spec)
spec.loader.exec_module(register)


def test_preview_does_not_create_directories(tmp_path):
    target = tmp_path / "absent" / "client.json"
    result = register.configure(target)
    assert result["changed"] and not result["applied"]
    assert not target.parent.exists()


def test_apply_preserves_other_servers_and_exact_backup(tmp_path):
    target = tmp_path / "client.json"
    original = b'{"mcpServers":{"another":{"command":"keep"}},"theme":"dark"}'
    target.write_bytes(original)
    result = register.configure(target, apply=True)
    saved = json.loads(target.read_text())
    assert saved["theme"] == "dark"
    assert saved["mcpServers"]["another"] == {"command": "keep"}
    assert saved["mcpServers"]["syncfl"]["args"][-1] == "syncfl-mcp"
    assert Path(result["backup"]).read_bytes() == original
    repeated = register.configure(target, apply=True)
    assert not repeated["changed"] and repeated["backup"] is None


@pytest.mark.parametrize(
    "original",
    [b"not json", b"[]", b'{"mcpServers":[]}', b'{"mcpServers":{"syncfl":{"command":"other"}}}'],
)
def test_malformed_or_conflicting_config_is_preserved(tmp_path, original):
    target = tmp_path / "client.json"
    target.write_bytes(original)
    with pytest.raises(ValueError):
        register.configure(target, apply=True)
    assert target.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


def test_existing_environment_survives_update(tmp_path):
    target = tmp_path / "client.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "syncfl": {
                        "command": "uv",
                        "args": ["run", "--directory", str(ROOT), "fl-studio-mcp"],
                        "env": {"FL_MCP_INTERACTION_POLICY": "interactive", "CUSTOM": "keep"},
                    }
                }
            }
        )
    )
    register.configure(target, apply=True)
    env = json.loads(target.read_text())["mcpServers"]["syncfl"]["env"]
    assert env == {"FL_MCP_INTERACTION_POLICY": "interactive", "CUSTOM": "keep"}


def test_failed_atomic_publish_preserves_original(tmp_path, monkeypatch):
    target = tmp_path / "client.json"
    target.write_bytes(b"{}")
    monkeypatch.setattr(
        register.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk failure"))
    )
    with pytest.raises(OSError, match="disk failure"):
        register.configure(target, apply=True)
    assert target.read_bytes() == b"{}"
    assert not list(tmp_path.glob(".syncfl-*"))
