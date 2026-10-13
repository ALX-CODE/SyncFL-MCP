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
    assert saved["mcpServers"]["syncfl"]["command"] == str(register._python(ROOT))
    assert saved["mcpServers"]["syncfl"]["args"] == ["-m", "fl_studio_mcp.server"]
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


@pytest.mark.parametrize("platform,relative", [
    ("win32", ".venv/Scripts/python.exe"), ("linux", ".venv/bin/python"),
    ("darwin", ".venv/bin/python"),
])
def test_preview_uses_platform_interpreter_without_startup_uv(
    tmp_path, monkeypatch, platform, relative,
):
    monkeypatch.setattr(register.sys, "platform", platform)
    result = register.configure(tmp_path / "config.json", root=tmp_path / "repo")
    assert result["entry"]["syncfl"] == {
        "command": str(tmp_path / "repo" / relative),
        "args": ["-m", "fl_studio_mcp.server"],
        "env": {"FL_MCP_INTERACTION_POLICY": "background"},
    }
    assert not (tmp_path / "config.json").exists()


def test_explicit_overrides_preserve_unspecified_environment_and_settings(tmp_path):
    target = tmp_path / "client.json"
    target.write_text(json.dumps({"mcpServers": {"syncfl": {
        "command": "uv", "args": ["run", "--directory", str(ROOT), "syncfl-mcp"],
        "timeout": 120, "env": {
            "FL_USER_DATA_FOLDER": "D:/Old/Image-Line", "FL_MCP_MIDI_PORT": "Custom port",
            "FL_MCP_INTERACTION_POLICY": "interactive", "CUSTOM": "kept",
        },
    }}}))
    register.configure(target, apply=True, overrides={
        "FL_USER_DATA_FOLDER": "D:/New/Image-Line",
        "FL_MCP_WINDOWS_SERVER": "D:/Tools/WindowsMCP.exe",
        "FL_MCP_INTERACTION_POLICY": "background",
    })
    entry = json.loads(target.read_text())["mcpServers"]["syncfl"]
    assert entry["timeout"] == 120
    assert entry["env"] == {
        "FL_USER_DATA_FOLDER": "D:/New/Image-Line", "FL_MCP_MIDI_PORT": "Custom port",
        "FL_MCP_WINDOWS_SERVER": "D:/Tools/WindowsMCP.exe",
        "FL_MCP_INTERACTION_POLICY": "background", "CUSTOM": "kept",
    }
    assert not register.configure(target, apply=True)["changed"]


def test_legacy_and_branded_duplicates_merge_into_one_preserving_settings(tmp_path):
    target = tmp_path / "client.json"
    old = {"command": "uv", "args": ["run", "--directory", str(ROOT), "fl-studio-mcp"]}
    target.write_text(json.dumps({"mcpServers": {
        "fl_studio": {**old, "timeout": 120, "env": {"FL_MCP_MIDI_PORT": "Existing port"}},
        "syncfl": {**old, "env": {"CUSTOM": "kept"}},
        "unrelated": {"command": "keep"},
    }}))
    register.configure(target, apply=True)
    servers = json.loads(target.read_text())["mcpServers"]
    assert set(servers) == {"unrelated", "syncfl"}
    assert servers["unrelated"] == {"command": "keep"}
    assert servers["syncfl"]["timeout"] == 120
    assert servers["syncfl"]["env"] == {
        "FL_MCP_MIDI_PORT": "Existing port", "CUSTOM": "kept",
        "FL_MCP_INTERACTION_POLICY": "background",
    }


def test_different_project_requires_replace_and_keeps_existing_environment(tmp_path):
    target = tmp_path / "client.json"
    original = json.dumps({"mcpServers": {"syncfl": {
        "command": "uv", "args": ["run", "--directory", str(tmp_path / "other"), "syncfl-mcp"],
        "env": {"CUSTOM": "kept"},
    }}})
    target.write_text(original)
    with pytest.raises(ValueError, match="different syncfl"):
        register.configure(target, apply=True)
    assert target.read_text() == original
    register.configure(target, apply=True, replace=True)
    entry = json.loads(target.read_text())["mcpServers"]["syncfl"]
    assert entry["command"] == str(register._python(ROOT))
    assert entry["env"]["CUSTOM"] == "kept"


def test_missing_interpreter_does_not_create_config(tmp_path):
    target = tmp_path / "absent" / "client.json"
    with pytest.raises(FileNotFoundError, match="Python missing"):
        register.configure(target, root=tmp_path / "repo", apply=True)
    assert not target.parent.exists()


@pytest.mark.parametrize("arguments,expected", [
    ([], {}),
    (["--fl-user-data", "D:/Music/Image-Line", "--midi-port", "Custom port",
      "--windows-server", "D:/Tools/WindowsMCP.exe", "--interaction-policy", "background"],
     {"FL_USER_DATA_FOLDER": "D:/Music/Image-Line", "FL_MCP_MIDI_PORT": "Custom port",
      "FL_MCP_WINDOWS_SERVER": "D:/Tools/WindowsMCP.exe",
      "FL_MCP_INTERACTION_POLICY": "background"}),
])
def test_cli_only_forwards_explicit_environment_overrides(monkeypatch, arguments, expected):
    captured = []
    monkeypatch.setattr(register.sys, "argv", ["register_claude.py", *arguments])
    monkeypatch.setattr(register, "configure", lambda *args, **kwargs: captured.append(kwargs) or {
        "success": True,
    })
    assert register.main() == 0
    assert captured[0]["overrides"] == expected
