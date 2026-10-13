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
    assert server["command"] == str(registration._python(tmp_path))
    assert server["env"]["FL_MCP_MIDI_PORT"] == "dedicated"
    assert server["env"]["FL_MCP_INTERACTION_POLICY"] == "background"
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
        "CUSTOM": "retained", "FL_MCP_MIDI_PORT": "new port",
        "FL_MCP_INTERACTION_POLICY": "background",
    }
    assert new["mcp_servers"]["fl_studio"]["tool_timeout_sec"] == 240
    assert result["removed_duplicates"] == 1
    assert "do-not-print" not in str(result)


def test_check_never_writes_and_apply_retains_exact_backup(tmp_path):
    root = tmp_path / "repo"
    python = registration._python(root)
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
    python = registration._python(tmp_path)
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


@pytest.mark.parametrize("name", ["syncfl", "syncfl-mcp", "SyncFL", "custom_name"])
def test_branded_registration_updates_existing_server_without_duplicate(tmp_path, name):
    original = f'[mcp_servers."{name}"]\ncommand = "syncfl-mcp"\n'
    text, result = registration.render_registration(original, tmp_path)
    servers = tomllib.loads(text)["mcp_servers"]
    assert list(servers) == [name]
    assert result["server"] == name
    assert servers[name]["args"] == ["-m", "fl_studio_mcp.server"]


def test_branded_and_legacy_duplicates_merge_without_losing_environment(tmp_path):
    original = '''[mcp_servers.syncfl]
command = "syncfl-mcp"
[mcp_servers.syncfl.env]
CUSTOM = "kept"
FL_MCP_INTERACTION_POLICY = "interactive"
[mcp_servers.fl_studio]
command = "fl-studio-mcp"
[mcp_servers.fl_studio.env]
FL_USER_DATA_FOLDER = "D:/Music/Image-Line"
FL_MCP_MIDI_PORT = "Existing port"
'''
    text, result = registration.render_registration(original, tmp_path, {
        "FL_MCP_WINDOWS_SERVER": "D:/Tools/WindowsMCP.exe",
    })
    servers = tomllib.loads(text)["mcp_servers"]
    assert list(servers) == ["fl_studio"]
    assert servers["fl_studio"]["env"] == {
        "CUSTOM": "kept", "FL_MCP_INTERACTION_POLICY": "interactive",
        "FL_USER_DATA_FOLDER": "D:/Music/Image-Line", "FL_MCP_MIDI_PORT": "Existing port",
        "FL_MCP_WINDOWS_SERVER": "D:/Tools/WindowsMCP.exe",
    }
    assert result["removed_duplicates"] == 1


def test_explicit_policy_and_midi_overrides_preserve_unspecified_environment(tmp_path):
    original = '''[mcp_servers.syncfl]
command = "syncfl-mcp"
[mcp_servers.syncfl.env]
FL_MCP_INTERACTION_POLICY = "interactive"
FL_MCP_MIDI_PORT = "Existing port"
CUSTOM = "kept"
'''
    text, _ = registration.render_registration(original, tmp_path, {
        "FL_MCP_INTERACTION_POLICY": "background", "FL_MCP_MIDI_PORT": "New port",
    })
    assert tomllib.loads(text)["mcp_servers"]["syncfl"]["env"] == {
        "FL_MCP_INTERACTION_POLICY": "background", "FL_MCP_MIDI_PORT": "New port",
        "CUSTOM": "kept",
    }


@pytest.mark.parametrize("platform,relative", [
    ("win32", ".venv/Scripts/python.exe"), ("linux", ".venv/bin/python"),
    ("darwin", ".venv/bin/python"),
])
def test_interpreter_path_matches_platform(tmp_path, monkeypatch, platform, relative):
    monkeypatch.setattr(registration.sys, "platform", platform)
    text, _ = registration.render_registration("", tmp_path)
    assert tomllib.loads(text)["mcp_servers"]["fl_studio"]["command"] == str(tmp_path / relative)


@pytest.mark.parametrize("arguments,expected", [
    ([], {}),
    (["--tool-mode", "discovery"], {"FL_MCP_TOOL_MODE": "discovery"}),
    (["--tool-mode", "full"], {"FL_MCP_TOOL_MODE": "full"}),
    (["--fl-user-data", "D:/Music/Image-Line", "--midi-port", "Custom port",
      "--windows-server", "D:/Tools/WindowsMCP.exe", "--interaction-policy", "background"],
     {"FL_USER_DATA_FOLDER": "D:/Music/Image-Line", "FL_MCP_MIDI_PORT": "Custom port",
      "FL_MCP_WINDOWS_SERVER": "D:/Tools/WindowsMCP.exe",
      "FL_MCP_INTERACTION_POLICY": "background"}),
])
def test_cli_only_forwards_explicit_environment_overrides(monkeypatch, arguments, expected):
    captured = []
    monkeypatch.setattr(registration.sys, "argv", ["register_codex.py", "--check", *arguments])
    monkeypatch.setattr(registration, "register", lambda *args: captured.append(args) or {
        "changed": False,
    })
    assert registration.main() == 0
    assert captured[0][2] == expected
