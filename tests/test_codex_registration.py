"""Portable registration keeps private settings intact; no real config or MIDI."""

import importlib.util
from pathlib import Path

import pytest
import tomllib

SPEC = importlib.util.spec_from_file_location(
    "register_codex", Path(__file__).resolve().parents[1] / "scripts/register_codex.py"
)
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)


def test_fresh_and_idempotent(tmp_path):
    text, result = registration.render_registration("", tmp_path, {"FL_MCP_MIDI_PORT": "dedicated"})
    assert result["changed"]
    server = tomllib.loads(text)["mcp_servers"]["fl_studio"]
    assert server["command"] == str(tmp_path / ".venv/Scripts/python.exe")
    assert server["env"]["FL_MCP_MIDI_PORT"] == "dedicated"
    again, result = registration.render_registration(text, tmp_path)
    assert again == text
    assert not result["changed"]


def test_updates_quoted_name_merges_duplicates_and_preserves_unrelated(tmp_path):
    original = '''model = "gpt-test"
# private settings retained
[mcp_servers.other]
command = "other"
[mcp_servers.other.env]
TOKEN = "do-not-print"
[mcp_servers."fl.legacy"]
command = "C:/old/fl-studio-mcp/.venv/Scripts/python.exe"
args = ["-m", "fl_studio_mcp.server"]
enabled = true
tool_timeout_sec = 240
[mcp_servers."fl.legacy".env]
CUSTOM = "retained"
[mcp_servers.fl_studio]
command = "old"
args = ["-m", "fl_studio_mcp.server"]
[mcp_servers.fl_studio.env]
FL_MCP_MIDI_PORT = "old port"
[projects."C:/another"]
trust_level = "trusted"
'''
    text, result = registration.render_registration(
        original, tmp_path, {"FL_MCP_MIDI_PORT": "new port"}
    )
    new = tomllib.loads(text)
    old = tomllib.loads(original)
    assert new["mcp_servers"]["other"] == old["mcp_servers"]["other"]
    assert original.split('[mcp_servers."fl.legacy"]')[0] in text
    assert new["projects"] == old["projects"]
    assert set(new["mcp_servers"]) == {"other", "fl_studio"}
    assert new["mcp_servers"]["fl_studio"]["env"] == {
        "CUSTOM": "retained", "FL_MCP_MIDI_PORT": "new port"
    }
    assert new["mcp_servers"]["fl_studio"]["tool_timeout_sec"] == 240
    assert result["removed_duplicates"] == 1
    assert "do-not-print" not in str(result)


def test_check_never_writes_and_apply_retains_exact_backup(tmp_path):
    root = tmp_path / "repo"
    python = root / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    config = tmp_path / "private/config.toml"
    config.parent.mkdir()
    original = b'model = "example"\r\n[mcp_servers.other]\r\ncommand = "unchanged"\r\n'
    config.write_bytes(original)
    status = registration.register(config, root)
    assert status["changed"] and not status["applied"]
    assert config.read_bytes() == original
    status = registration.register(config, root, apply=True)
    assert Path(status["backup"]).read_bytes() == original
    assert status["applied"]
    once = config.read_bytes()
    status = registration.register(config, root, apply=True)
    assert not status["changed"]
    assert config.read_bytes() == once
    assert len(list(config.parent.glob("*.before-fl-mcp-*"))) == 1


def test_fresh_config_and_missing_python(tmp_path):
    config = tmp_path / "private/config.toml"
    with pytest.raises(FileNotFoundError):
        registration.register(config, tmp_path, apply=True)
    assert not config.exists()
    python = tmp_path / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    assert registration.register(config, tmp_path, apply=True)["applied"]


def test_inline_fl_table_refuses_without_loss(tmp_path):
    original = 'mcp_servers = { fl_studio = { command = "old" }, other = { command = "safe" } }\n'
    config = tmp_path / "config.toml"
    config.write_text(original)
    with pytest.raises(ValueError, match="Nonstandard TOML"):
        registration.register(config, tmp_path, apply=True)
    assert config.read_text() == original


def test_unusual_multiline_toml_cannot_corrupt_unrelated_config(tmp_path):
    original = '''note = """line one
[mcp_servers.fl_studio]
this is inside a string
"""
[mcp_servers.fl_studio]
command = "old"
'''
    with pytest.raises(ValueError, match="Nonstandard TOML"):
        registration.render_registration(original, tmp_path)


def test_corrupt_config_is_untouched(tmp_path):
    config = tmp_path / "config.toml"
    original = b'private = "unclosed'
    config.write_bytes(original)
    with pytest.raises(tomllib.TOMLDecodeError):
        registration.register(config, tmp_path, apply=True)
    assert config.read_bytes() == original
