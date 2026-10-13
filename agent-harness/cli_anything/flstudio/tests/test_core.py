import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fl_studio_mcp.utils import midi_connection
from fl_studio_mcp.utils.midi_connection import MIDIConnection
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

ROOT = Path(__file__).resolve().parents[4]


def connection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "fl_studio_mcp.utils.midi_connection._get_fl_hardware_dir", lambda: tmp_path
    )
    monkeypatch.setattr(midi_connection, "_create_backend", lambda: SimpleNamespace(
        kind="fake", list_outputs=lambda: [],
        open_output=lambda *a: pytest.fail("Unit tests must not open MIDI hardware")))
    return MIDIConnection()


def test_stale_and_partial_response_are_not_success(tmp_path, monkeypatch):
    conn = connection(tmp_path, monkeypatch)
    atomic_json(conn._response_file, {"protocol": 2, "id": "stale", "success": True})
    elapsed, polls = 0.0, 0

    def advance(seconds):
        nonlocal elapsed, polls
        elapsed += seconds
        polls += 1
        if polls == 1:
            conn._response_file.write_text('{"id":')
        elif polls == 2:
            # Even a matching ID cannot make the obsolete envelope succeed.
            atomic_json(conn._response_file, {"id": "wanted", "success": True})
        else:
            atomic_json(conn._response_file, {"protocol": 2, "id": "wanted", "success": True})

    # Publish after each poll rather than depending on a writer thread receiving
    # CPU within a real half-second deadline during the full suite.
    monkeypatch.setattr(
        midi_connection, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
    )
    result = conn._wait_for_response(0.5, "wanted")
    assert result["success"] and result["id"] == "wanted" and result["protocol"] == 2
    # Three 0.2 ms polls: the first 25 ms of a wait are checked finely.
    assert polls == 3 and elapsed == pytest.approx(0.0006)


def test_timeout_is_explicitly_unknown(tmp_path, monkeypatch):
    result = connection(tmp_path, monkeypatch)._wait_for_response(0.02, "missing")
    assert not result["success"] and result["outcome"] == "unknown"


def test_no_arbitrary_hardware_port(tmp_path, monkeypatch):
    monkeypatch.delenv("FL_MCP_MIDI_PORT", raising=False)
    conn = connection(tmp_path, monkeypatch)
    monkeypatch.setattr(midi_connection, "_create_backend", lambda: SimpleNamespace(
        kind="fake", list_outputs=lambda: ["Microsoft GS Wavetable Synth 0"],
        open_output=lambda *a: pytest.fail("Must reject unrelated hardware")))
    assert not conn.connect()


def test_lock_excludes_other_threads(tmp_path):
    failures = []

    def contender():
        try:
            with transaction_lock(tmp_path / "lock", 0.03):
                failures.append("entered")
        except TimeoutError:
            failures.append("blocked")

    with transaction_lock(tmp_path / "lock", 0.1):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()
    assert failures == ["blocked"]


def extension(monkeypatch):
    """Only unit tests use synthetic FL modules. Live tests use the actual DAW."""
    import sys

    mix = SimpleNamespace(
        getTrackInfo=lambda _: 16,
        trackCount=lambda: 18,
        getRouteSendActive=lambda a, b: b == 0,
        afterRoutingChanged=Mock(),
        setTrackName=Mock(),
        setTrackColor=Mock(),
        setTrackVolume=Mock(),
        setTrackPan=Mock(),
        setTrackStereoSep=Mock(),
        muteTrack=Mock(),
    )
    plug = SimpleNamespace(
        isValid=lambda *a: True,
        getPluginName=lambda *a: "Expected",
        getParamCount=lambda *a: 3,
        getParamName=lambda *a: "Gain",
    )
    chan = SimpleNamespace(channelCount=lambda *_: 94)
    for key, module in {"mixer": mix, "plugins": plug, "channels": chan}.items():
        monkeypatch.setitem(sys.modules, key, module)
    scope = {
        "json": json,
        "SCRIPT_DIR": ROOT / "fl_controller",
        "dispatch_command": lambda a, p: {},
        "mixer": mix,
        "plugins": plug,
        "channels": chan,
    }
    exec(compile((ROOT / "fl_controller/mcp_extended.py").read_text(), "extension", "exec"), scope)
    return scope


def test_batch_invalid_step_has_no_mutations(monkeypatch):
    scope = extension(monkeypatch)
    with pytest.raises(ValueError):
        scope["extended_dispatch"](
            "bridge.batch",
            {
                "steps": [
                    {"action": "mixer.configure", "params": {"track": 1, "name": "good"}},
                    {
                        "action": "mixer.configure",
                        "params": {"track": 17, "name": "Current is not an insert"},
                    },
                ]
            },
        )
    scope["mixer"].setTrackName.assert_not_called()


def test_routing_cycle_rejected(monkeypatch):
    scope = extension(monkeypatch)
    with pytest.raises(ValueError, match="feedback"):
        scope["routing_preflight"](
            [
                {"action": "mixer.route", "params": {"source": 1, "destination": 2}},
                {"action": "mixer.route", "params": {"source": 2, "destination": 1, "level": 0}},
            ]
        )


def test_plugin_identity_and_nan_guards(monkeypatch):
    scope = extension(monkeypatch)
    with pytest.raises(ValueError, match="identity"):
        scope["preflight"](
            "plugins.setMany", {"track": 1, "slot": 0, "expected_plugin": "Wrong", "changes": []}
        )
    with pytest.raises(ValueError, match="finite"):
        scope["preflight"]("mixer.configure", {"track": 1, "volume": float("nan")})


def test_partial_batch_reports_applied(monkeypatch):
    scope = extension(monkeypatch)
    scope["track_info"] = lambda t: {"track": t}
    scope["mixer"].setTrackName.side_effect = [None, RuntimeError("DAW rejected")]
    result = scope["extended_dispatch"](
        "bridge.batch",
        {
            "steps": [
                {"action": "mixer.configure", "params": {"track": 1, "name": "one"}},
                {"action": "mixer.configure", "params": {"track": 2, "name": "two"}},
            ]
        },
    )
    assert result["partial"] and result["applied"] == 1 and result["failed_step"] == 1
    assert scope["IN_VALIDATED_BATCH"] is False


def test_search_paginates_matches_not_vst_padding(monkeypatch):
    scope = extension(monkeypatch)
    scope["plugins"].getParamCount = lambda *args: 4240
    scope["plugins"].getParamName = lambda n, *args: "Gain" if n in (1, 4000) else ""
    scope["plugins"].getParamValue = lambda *args: 0.5
    scope["plugins"].getParamValueString = lambda *args: "0 dB"
    result = scope["extended_dispatch"](
        "plugins.parameters", {"track": 1, "slot": 0, "query": "Gain", "offset": 1, "limit": 1}
    )
    assert result["matched"] == 2 and result["next_offset"] is None
    assert result["parameters"][0]["parameter"] == 4000


def test_send_exception_does_not_claim_no_mutation(tmp_path, monkeypatch):
    conn = connection(tmp_path, monkeypatch)
    conn._connected = True
    conn._port = port = Mock(closed=False)
    port.send_wakeup.side_effect = OSError("Port failed during send")
    result = conn.send_command("mixer.configure", {"track": 1, "name": "test"})
    assert not result["success"] and result["outcome"] == "unknown"
    assert "Port failed during send" in result["error"]
    port.send_wakeup.assert_called_once()
    port.close.assert_called_once()
    assert not conn.is_connected


def test_plugin_aliases_do_not_accept_different_products():
    from fl_studio_mcp.utils.windows_workflows import matches_plugin

    assert matches_plugin({"name": "Pro-Q 4"}, "FabFilter Pro-Q 4")
    assert not matches_plugin({"name": "Little MicroShift"}, "MicroShift")
    assert not matches_plugin({"name": "Pro-Q 3"}, "FabFilter Pro-Q 4")


def test_cli_runtime_failure_is_json(monkeypatch):
    from cli_anything.flstudio import flstudio_cli
    from click.testing import CliRunner

    monkeypatch.setattr(flstudio_cli, "call", Mock(side_effect=RuntimeError("Bridge offline")))
    result = CliRunner().invoke(flstudio_cli.cli, ["health"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "Bridge offline"


def test_atomic_publication_retries_only_file_sharing_conflict(tmp_path, monkeypatch):
    import os

    real_replace = os.replace
    attempts = []

    def transient(source, destination):
        attempts.append(source)
        if len(attempts) == 1:
            raise PermissionError("Reader temporarily holds destination")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", transient)
    target = tmp_path / "command.json"
    atomic_json(target, {"id": "single_request"})
    assert len(attempts) == 2 and json.loads(target.read_text())["id"] == "single_request"


def test_display_value_requires_matching_numeric_units():
    from fl_studio_mcp.utils.parameter_values import display_number

    assert display_number(" -12.50 dB ", "dB") == -12.5
    assert display_number("200 ms", "sec") == 0.2
    assert display_number("2.5 kHz", "Hz") == 2500
    assert display_number("0.09 sec", "msec") == 90
    with pytest.raises(ValueError):
        display_number("Auto ", "ms")
    with pytest.raises(ValueError):
        display_number("100 Hz", "ms")


def test_display_calibration_restores_on_out_of_range(tmp_path, monkeypatch):
    from fl_studio_mcp.utils import parameter_values

    state = {"value": 0.5}

    def send(action, params, timeout):
        if action == "transport.getStatus":
            return {"success": True, "is_playing": False, "is_recording": False}
        if action == "plugins.parameters":
            return {
                "success": True,
                "parameters": [
                    {
                        "name": "Gain",
                        "value": state["value"],
                        "display": f"{state['value'] * 10} dB",
                    }
                ],
            }
        state["value"] = params["changes"][0]["value"]
        return {
            "success": True,
            "after": [{"value": state["value"], "display": f"{state['value'] * 10} dB"}],
        }

    monkeypatch.setattr(
        parameter_values,
        "get_connection",
        lambda: SimpleNamespace(
            _hardware_dir=tmp_path,
            send_command=send,
        ),
    )
    result = parameter_values.set_display_value(1, 0, 0, "Test", "Gain", 100, "dB")
    assert not result["success"] and result["restoration"]["success"]
    assert state["value"] == 0.5


def test_cli_negative_result_emits_only_one_json_object(monkeypatch):
    from cli_anything.flstudio import flstudio_cli
    from click.testing import CliRunner

    monkeypatch.setattr(flstudio_cli, "call", lambda *a: {"success": False, "error": "Stopped"})
    result = CliRunner().invoke(flstudio_cli.cli, ["health"])
    assert result.exit_code == 1
    assert json.loads(result.output) == {"success": False, "error": "Stopped"}


@pytest.mark.parametrize("decreasing", [False, True])
def test_display_calibration_converges_in_both_directions(tmp_path, monkeypatch, decreasing):
    from fl_studio_mcp.utils import parameter_values

    state = {"value": 0.5}

    def send(action, params, timeout):
        if action == "transport.getStatus":
            return {"success": True, "is_playing": False, "is_recording": False}
        if action == "plugins.setMany":
            state["value"] = params["changes"][0]["value"]
            return {"success": True}  # No reliance on immediate setter readback.
        value = (1 - state["value"] if decreasing else state["value"]) * 100
        return {
            "success": True,
            "parameters": [
                {
                    "name": "Time",
                    "value": state["value"],
                    "display": f"{value:.3f} ms",
                }
            ],
        }

    monkeypatch.setattr(parameter_values.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        parameter_values,
        "get_connection",
        lambda: SimpleNamespace(
            _hardware_dir=tmp_path,
            send_command=send,
        ),
    )
    result = parameter_values.set_display_value(1, 0, 0, "Test", "Time", 25, "ms")
    assert result["success"]
    assert abs(float(result["after"]["display"].split()[0]) - 25) < 0.01
