"""Explicit opt-in live checks; never silently mutate a user's open project."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fl_studio_mcp.utils.midi_connection import MIDIConnection

pytestmark = pytest.mark.skipif(
    os.environ.get("FL_MCP_LIVE_TEST") != "1",
    reason="Set FL_MCP_LIVE_TEST=1 on a working project copy",
)


def test_installed_cli_handshake():
    executable = Path(sys.executable).with_name("cli-anything-flstudio.exe")
    result = subprocess.run(
        [str(executable), "--json", "health"], capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    assert data["success"] and data["protocol"] == 2 and 20 < data["tempo"] < 999


def test_live_mixer_batch_and_restore():
    c = MIDIConnection()
    before = c.send_command("mixer.inspect", {"tracks": [89], "routes": True})
    assert before["success"]
    original = before["tracks"][0]
    assert not any(s["occupied"] for s in original["slots"])
    assert original["name"] == "Insert 89", "Scratch track no longer empty"
    try:
        result = c.send_command(
            "bridge.batch",
            {
                "steps": [
                    {
                        "action": "mixer.configure",
                        "params": {"track": 89, "name": "MCP TEST", "volume": 0.7},
                    },
                    {
                        "action": "mixer.route",
                        "params": {"source": 89, "destination": 88, "level": 0.25},
                    },
                ]
            },
            15,
        )
        assert result["success"] and result["applied"] == 2
        after = c.send_command("mixer.inspect", {"tracks": [89], "routes": True})["tracks"][0]
        assert after["name"] == "MCP TEST" and abs(after["volume"] - 0.7) < 0.0001
        assert any(
            r["destination"] == 88 and abs(r["level"] - 0.25) < 0.0001 for r in after["routes"]
        )
    finally:
        restore = c.send_command(
            "bridge.batch",
            {
                "steps": [
                    {
                        "action": "mixer.route",
                        "params": {"source": 89, "destination": 88, "enabled": False},
                    },
                    {
                        "action": "mixer.configure",
                        "params": {
                            "track": 89,
                            "name": original["name"],
                            "volume": original["volume"],
                        },
                    },
                ]
            },
            15,
        )
        assert restore["success"]
        c.disconnect()
