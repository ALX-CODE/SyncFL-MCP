"""No FL Studio writes: transaction behavior uses a stateful controller double."""

from copy import deepcopy

import pytest

from fl_studio_mcp.utils import serum_live as serum


class Connection:
    def __init__(self, directory):
        self._hardware_dir = directory
        self.rows = {
            i: {"index": i, "name": "", "value": 0.5, "value_string": "test"} for i in range(1000)
        }
        for spec in serum.CONTROLS.values():
            self.rows[spec.index]["name"] = spec.name
            if spec.unit == "boolean":
                self.rows[spec.index]["value"] = 0
            self.rows[spec.index]["value_string"] = self.display(
                spec, self.rows[spec.index]["value"]
            )
        self.plugin = "Serum 2"
        self.channel_name = "Kick"
        self.playing = False
        self.recording = False
        self.writes = []
        self.calls = []
        self.fail_write = None
        self.ignore_write = None
        self.identity_after_write = False
        self.name_after_snapshot = False

    @staticmethod
    def display(spec, value):
        physical = spec.physical(value)
        if spec.unit == "boolean":
            return "On" if physical else "Off"
        suffix = {"ms": " ms", "percent": "%", "signed_percent": "%", "cents": " cents"}
        return f"{physical:.6f}" + suffix.get(spec.unit, "")

    def send_command(self, action, params, timeout):
        self.calls.append((action, deepcopy(params)))
        if action == "transport.getStatus":
            return {"success": True, "is_playing": self.playing, "is_recording": self.recording}
        if action == "plugins.getParams":
            rows = deepcopy(list(self.rows.values()))
            if self.name_after_snapshot:
                self.rows[226]["name"] = "Moved Parameter"
            return {"success": True, "params": rows}
        if action == "plugins.getParamValue":
            return {"success": True, **self.rows[params["param_index"]]}
        assert action == "native.call"
        function, args = params["function"], params["args"]
        if function == "plugins.getPluginName":
            value = self.plugin
        elif function == "channels.getChannelName":
            value = self.channel_name
        elif function == "plugins.getParamCount":
            value = len(self.rows)
        elif function == "plugins.getParamName":
            value = self.rows[args[0]]["name"]
        elif function == "plugins.setParamValue":
            assert params["allow_write"] is True
            value, index, channel, slot, flags, use_global = args
            assert (channel, slot, flags, use_global) == (3, -1, 0, True)
            self.writes.append((index, value))
            if len(self.writes) == self.fail_write:
                raise RuntimeError("simulated write failure")
            if len(self.writes) != self.ignore_write:
                self.rows[index]["value"] = value
                spec = next(s for s in serum.CONTROLS.values() if s.index == index)
                self.rows[index]["value_string"] = self.display(spec, value)
            if self.identity_after_write:
                self.channel_name = "Different channel"
            value = None
        else:
            raise AssertionError(function)
        return {"success": True, "value": value}


@pytest.fixture
def conn(monkeypatch, tmp_path):
    connection = Connection(tmp_path)
    monkeypatch.setattr(serum, "get_connection", lambda: connection)
    monkeypatch.setattr(serum.time, "sleep", lambda _: None)
    return connection


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"missing": 1},
        {"env1.attack_ms": float("nan")},
        {"env2.decay_ms": float("inf")},
        {"env1.decay_ms": -1},
        {"env1.decay_ms": 32001},
        {"osc_a.fine_cents": 101},
        {"osc_a.fine_cents": -101},
        {"master.volume_percent": 101},
        {"mod1.amount_percent": -101},
        {"voice.mono": 1},
        {"env1.attack_ms": True},
        {"env1.attack_ms": "300"},
        {"osc_a.detune_normalized": 1.01},
    ],
)
def test_invalid_input_never_contacts_fl(conn, changes):
    with pytest.raises(ValueError):
        serum.apply_serum(3, changes, False)
    assert not conn.calls
    assert not conn.writes


@pytest.mark.parametrize("channel", [-1, True, 3.1, "3"])
def test_invalid_channel(conn, channel):
    with pytest.raises(ValueError):
        serum.inspect_serum(channel)
    assert not conn.calls


def test_physical_conversion_known_values():
    assert serum.CONTROLS["env1.decay_ms"].normalize(1000) == pytest.approx(0.5)
    assert serum.CONTROLS["env1.attack_ms"].normalize(0.1) == pytest.approx(0.07924466)
    assert serum.CONTROLS["osc_a.fine_cents"].normalize(-32) == pytest.approx(0.34)
    assert serum.CONTROLS["mod1.amount_percent"].normalize(25) == pytest.approx(0.625)
    assert serum.CONTROLS["voice.mono"].normalize(True) == 1


def test_inspect_compact_groups_and_full_fingerprint(conn):
    result = serum.inspect_serum(3)
    assert result["groups"]["env1"]["env1.decay_ms"]["value"] == 1000
    assert len(result["identity"]["catalog_sha256"]) == 64
    initial = result["identity"]["catalog_sha256"]
    conn.rows[999]["name"] = "Other parameter"
    assert serum.inspect_serum(3)["identity"]["catalog_sha256"] != initial
    assert not conn.writes


def test_locked_inspector_reuses_connection_and_does_not_relock(conn, monkeypatch):
    expected = serum.inspect_serum(3)

    def must_not_call(*args, **kwargs):
        raise AssertionError("Locked inspector must reuse its caller's connection and lock")

    monkeypatch.setattr(serum, "get_connection", must_not_call)
    monkeypatch.setattr(serum, "transaction_lock", must_not_call)
    assert serum._inspect_locked(conn, 3) == expected


def test_dry_run_default_preflights_all_changes(conn):
    report = serum.apply_serum(3, {"env1.decay_ms": 300, "osc_a.fine_cents": -32})
    assert report["success"] and report["status"] == "preflighted"
    assert report["changes"][0]["before"] == 0.5
    assert report["changes"][1]["target"] == pytest.approx(0.34)
    assert not conn.writes


@pytest.mark.parametrize("state", ["playing", "recording"])
def test_running_transport_refuses_all_writes(conn, state):
    setattr(conn, state, True)
    with pytest.raises(ValueError, match="Stop playback"):
        serum.apply_serum(3, {"env1.decay_ms": 300}, False)
    assert not conn.writes


def test_wrong_instrument_refused(conn):
    conn.plugin = "ZENOLOGY"
    with pytest.raises(ValueError, match="Expected Serum 2"):
        serum.apply_serum(3, {"env1.decay_ms": 300}, False)
    assert not conn.writes


def test_catalog_mismatch_blocks_before_first_write(conn):
    conn.rows[25]["name"] = "Moved Parameter"
    with pytest.raises(ValueError, match="Parameter identity changed"):
        serum.apply_serum(3, {"env1.decay_ms": 300, "osc_a.fine_cents": -32}, False)
    assert not conn.writes


def test_name_rechecked_before_write(conn):
    conn.name_after_snapshot = True
    result = serum.apply_serum(3, {"env1.decay_ms": 300}, False)
    assert not result["success"]
    assert "Parameter identity changed" in result["error"]
    assert not conn.writes


def test_successful_changes_and_noop_skips(conn):
    report = serum.apply_serum(
        3,
        {
            "env1.decay_ms": 300,
            "osc_a.fine_cents": -32,
            "master.volume_percent": 50,
        },
        False,
    )
    assert report["success"] and report["status"] == "completed"
    assert len(conn.writes) == 2
    assert len(report["applied"]) == 2
    assert not report["rollback"]


def test_readback_mismatch_restores_every_attempted_control(conn):
    conn.ignore_write = 2
    report = serum.apply_serum(3, {"env1.decay_ms": 300, "osc_a.fine_cents": -32}, False)
    assert not report["success"]
    assert "readback mismatch" in report["error"]
    assert report["rollback_complete"]
    assert [r["key"] for r in report["rollback"]] == ["osc_a.fine_cents", "env1.decay_ms"]
    assert conn.rows[226]["value"] == 0.5
    assert conn.rows[25]["value"] == 0.5


def test_write_failure_includes_uncertain_write_in_rollback(conn):
    conn.fail_write = 2
    report = serum.apply_serum(3, {"env1.decay_ms": 300, "osc_a.fine_cents": -32}, False)
    assert not report["success"] and report["rollback_complete"]
    assert len(report["rollback"]) == 2
    assert conn.rows[226]["value"] == conn.rows[25]["value"] == 0.5


def test_identity_change_refuses_rollback_to_other_channel(conn):
    conn.identity_after_write = True
    report = serum.apply_serum(3, {"env1.decay_ms": 300}, False)
    assert not report["success"]
    assert not report["rollback_complete"]
    assert "identity changed" in report["rollback"][0]["error"]
    assert len(conn.writes) == 1


def test_rollback_readback_failure_reported(conn):
    # The first write applies but loses its acknowledgement; rollback is ignored.
    conn.ignore_write = 2
    original_send = conn.send_command

    def fail_after_apply(action, params, timeout):
        result = original_send(action, params, timeout)
        if (
            action == "native.call"
            and params["function"] == "plugins.setParamValue"
            and len(conn.writes) == 1
        ):
            raise RuntimeError("acknowledgement lost after write")
        return result

    conn.send_command = fail_after_apply
    report = serum.apply_serum(3, {"env1.decay_ms": 300}, False)
    assert not report["rollback_complete"]
    assert report["rollback"][0]["error"] == "Rollback readback mismatch"


@pytest.mark.parametrize(
    "key,normalized,display,expected,consistent",
    [
        ("master.volume_percent", 0.25, "32% [-16.8 dB] ", 32, False),
        ("voice.mono", 0, "On ", True, False),
        ("env1.attack_ms", (0.350 / 32) ** 0.2, "0.1 ms ", 0.1, False),
        ("mod1.amount_percent", 0.5, "10% ", 10, False),
        ("env2.decay_ms", 0.49496516585350037, "951 ms ", 951, True),
        ("env2.decay_ms", 0.5, "1.00 s ", 1000, True),
        ("env2.decay_curve_percent", 0.95588237, "96 %", 96, True),
        ("osc_a.fine_cents", 0.34, "-32 cents", -32, True),
        ("voice.mono", 1, "On", True, True),
        ("voice.mono", 0.5, "On", True, False),
        ("mod1.amount_percent", 0.5, "--", None, None),
        ("osc_a.detune_normalized", 0.2, "0.04", None, None),
        ("env1.decay_ms", 0.5, "garbage", None, None),
    ],
)
def test_current_display_separate_from_stale_host(
    key,
    normalized,
    display,
    expected,
    consistent,
):
    state = serum._display_state(
        serum.CONTROLS[key],
        {
            "value": normalized,
            "value_string": display,
        },
    )
    assert state["value"] == state["display_value"] == expected
    assert state["host_normalized"] == normalized
    assert state["cache_consistent"] is consistent


def test_inspect_reports_actual_display_and_unknown_normalized_value(conn):
    conn.rows[0].update(value=0.25, value_string="32% [-16.8 dB]")
    conn.rows[9].update(value=0, value_string="On")
    result = serum.inspect_serum(3)
    volume = result["groups"]["master"]["master.volume_percent"]
    assert volume["value"] == 32
    assert volume["host_normalized"] == 0.25
    assert volume["cache_consistent"] is False
    assert "normalized" not in volume
    assert result["groups"]["osc_a"]["osc_a.detune_normalized"]["value"] is None
    assert result["stale_controls"] == ["master.volume_percent", "voice.mono"]
    assert "osc_a.detune_normalized" in result["unmapped_controls"]
    assert not conn.writes


@pytest.mark.parametrize("dry_run", [True, False])
def test_stale_baseline_refuses_complete_batch_before_writes(conn, dry_run):
    conn.rows[0].update(value=0.25, value_string="32% [-16.8 dB]")
    with pytest.raises(ValueError, match="stale host cache"):
        serum.apply_serum(3, {"env1.decay_ms": 300, "master.volume_percent": 40}, dry_run)
    assert not conn.writes


def test_unmapped_baseline_refuses_unsafe_rollback_value(conn):
    with pytest.raises(ValueError, match="unmapped display"):
        serum.apply_serum(3, {"osc_a.detune_normalized": 0.2}, False)
    assert not conn.writes


def test_host_write_ack_without_display_change_does_not_report_success(conn):
    original_send = conn.send_command

    def stale_display(action, params, timeout):
        before = conn.rows[226]["value_string"]
        result = original_send(action, params, timeout)
        if action == "native.call" and params["function"] == "plugins.setParamValue":
            conn.rows[226]["value_string"] = before
        return result

    conn.send_command = stale_display
    result = serum.apply_serum(3, {"env1.decay_ms": 300}, False)
    assert not result["success"]
    assert "stale host cache" in result["error"]
    assert result["rollback_complete"]
    assert conn.rows[226]["value"] == 0.5
