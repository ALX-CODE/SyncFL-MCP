import json
from copy import deepcopy

import pytest

from fl_studio_mcp.utils import mixer_controls as eq


class Host:
    def __init__(self, directory):
        self._hardware_dir = directory
        self.calls = []
        self.writes = []
        self.values = {"Gain": 0.5, "Frequency": 0.25, "Bandwidth": 0.4}
        self.switches = {"effects_enabled": True, "reverse_polarity": False, "swap_channels": False}
        self.project = {
            "success": True,
            "context": {
                "session": "test",
                "project_generation": 1,
                "channel_generation": 0,
                "channels": [],
            },
            "is_playing": False,
            "is_recording": False,
            "controller_build": "build",
        }
        self.error_after_write = False
        self.wrong_readback = False

    def send_command(self, action, params, timeout):
        self.calls.append((action, deepcopy(params)))
        if action == "production.inspect":
            return deepcopy(self.project)
        assert action == "bridge.batch"
        steps = params["steps"]
        if params.get("dry_run", False):
            return {"success": True, "validated": len(steps), "applied": 0}
        results = []
        for step in steps:
            assert step["action"] == "native.call"
            p = step["params"]
            fn, args = p["function"], p["args"]
            leaf = fn.removeprefix("mixer.")
            if leaf == "getTrackInfo":
                value = 16
            elif leaf == "getEqBandCount":
                value = 3
            elif leaf == "getTrackName":
                value = "Insert 1"
            elif leaf.startswith("getEq"):
                value = self.values[leaf[5:]]
                if leaf == "getEqGain" and len(args) == 3 and args[2] == 1:
                    value = 36 * (value - 0.5)
                elif leaf == "getEqFrequency" and len(args) == 3 and args[2] == 1:
                    value = 20 + value * 20000
            elif leaf.startswith("setEq"):
                assert p["allow_write"] is True
                self.writes.append((leaf, args))
                self.values[leaf[5:]] = args[2] if not self.wrong_readback else 0.99
                value = None
            else:
                key = next(k for k, pair in eq._SWITCHES.items() if leaf in pair)
                if leaf == eq._SWITCHES[key][1]:
                    assert p["allow_write"] is True
                    assert type(args[1]) is int  # Explicit 0/1, never host toggle default.
                    self.writes.append((leaf, args))
                    self.switches[key] = bool(args[1])
                    value = None
                else:
                    value = int(self.switches[key])
            results.append({"value": value})
        if self.error_after_write and any(s["params"].get("allow_write") for s in steps):
            return {"success": False, "outcome": "unknown", "error": "Lost acknowledgement"}
        return {"success": True, "applied": len(steps), "results": results}


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(eq.time, "sleep", lambda _: None)
    return Host(tmp_path)


def apply(host, tmp_path, changes, **kw):
    before = eq.inspect_controls(1, host)
    return eq.set_controls(
        1,
        "Insert 1",
        before["revision"],
        changes,
        connection=host,
        receipt_file=str(tmp_path / "receipt.json"),
        **kw,
    )


def test_inspection_reads_without_selecting_or_writing(host):
    state = eq.inspect_controls(1, host)
    assert state["bands"][0]["gain_db"] == 0
    assert state["bands"][0]["frequency_hz"] == 5020
    assert state["effects_enabled"] is True
    assert not host.writes
    functions = [
        s["params"]["function"] for a, p in host.calls if a == "bridge.batch" for s in p["steps"]
    ]
    assert all(f.startswith("mixer.get") or f.startswith("mixer.is") for f in functions)


def test_preview_has_no_writes_or_receipt(host, tmp_path):
    result = apply(host, tmp_path, {"bands": [{"band": 0, "gain_normalized": 0.65}]})
    assert result["success"] and result["dry_run"]
    assert not host.writes and not (tmp_path / "receipt.json").exists()


def test_apply_verifies_twice_and_records_explicit_recovery(host, tmp_path):
    result = apply(
        host,
        tmp_path,
        {"bands": [{"band": 0, "gain_normalized": 0.65}], "reverse_polarity": True},
        apply=True,
    )
    assert result["success"] and result["status"] == "verified"
    assert len(result["readbacks"]) == 2
    assert result["restore_changes"] == {
        "bands": [{"band": 0, "gain_normalized": 0.5}],
        "reverse_polarity": False,
    }
    assert len(host.writes) == 2
    assert json.loads((tmp_path / "receipt.json").read_text())["status"] == "verified"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"unknown": 1},
        {"effects_enabled": 1},
        {"bands": []},
        {"bands": [{"band": True, "gain_normalized": 0.5}]},
        {"bands": [{"band": 0, "gain_db": 3}]},
        {"bands": [{"band": 0, "gain_normalized": float("nan")}]},
        {"bands": [{"band": 0, "gain_normalized": 1.1}]},
        {"bands": [{"band": 0, "gain_normalized": 0.5}, {"band": 0, "frequency_normalized": 0.4}]},
    ],
)
def test_invalid_payload_cannot_write(host, tmp_path, changes):
    with pytest.raises(ValueError):
        apply(host, tmp_path, changes, apply=True)
    assert not host.writes


def test_nonexistent_band_does_not_partially_change_switches(host, tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        apply(
            host,
            tmp_path,
            {"effects_enabled": False, "bands": [{"band": 3, "gain_normalized": 0.5}]},
            apply=True,
        )
    assert not host.writes


def test_stale_revision_stops_before_dispatch(host, tmp_path):
    with pytest.raises(ValueError, match="revision"):
        eq.set_controls(
            1,
            "Insert 1",
            "0" * 64,
            {"effects_enabled": False},
            apply=True,
            receipt_file=str(tmp_path / "receipt.json"),
            connection=host,
        )
    assert not host.writes


def test_playing_transport_blocks_writes(host, tmp_path):
    host.project["is_playing"] = True
    with pytest.raises(ValueError, match="playback"):
        apply(host, tmp_path, {"effects_enabled": False}, apply=True)
    assert not host.writes


def test_existing_receipt_cannot_be_overwritten(host, tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text("preserve me")
    with pytest.raises(FileExistsError):
        apply(host, tmp_path, {"effects_enabled": False}, apply=True)
    assert not host.writes and path.read_text() == "preserve me"


def test_missing_acknowledgement_never_replays_or_restores(host, tmp_path):
    host.error_after_write = True
    result = apply(host, tmp_path, {"effects_enabled": False}, apply=True)
    assert not result["success"] and result["status"] == "observed_after_error"
    assert len(host.writes) == 1 and host.switches["effects_enabled"] is False


def test_wrong_readback_is_failure_without_hidden_restore(host, tmp_path):
    host.wrong_readback = True
    result = apply(host, tmp_path, {"bands": [{"band": 0, "gain_normalized": 0.65}]}, apply=True)
    assert not result["success"] and result["status"] == "unknown"
    assert len(host.writes) == 1


def test_incomplete_acknowledgement_never_becomes_verified(host, tmp_path, monkeypatch):
    send = host.send_command

    def missing_result(action, params, timeout):
        result = send(action, params, timeout)
        if action == "bridge.batch" and any(
            step["params"].get("allow_write") for step in params["steps"]
        ) and not params.get("dry_run"):
            result.pop("results")
        return result

    monkeypatch.setattr(host, "send_command", missing_result)
    result = apply(host, tmp_path, {"effects_enabled": False}, apply=True)
    assert not result["success"] and result["status"] == "observed_after_error"
    assert len(host.writes) == 1


def test_project_change_during_inspection_is_not_a_valid_snapshot(host, monkeypatch):
    send = host.send_command
    count = 0

    def changed_project(action, params, timeout):
        nonlocal count
        if action == "production.inspect":
            count += 1
            if count == 2:
                host.project["context"]["project_generation"] += 1
        return send(action, params, timeout)

    monkeypatch.setattr(host, "send_command", changed_project)
    with pytest.raises(RuntimeError, match="Project changed"):
        eq.inspect_controls(1, host)
    assert not host.writes
