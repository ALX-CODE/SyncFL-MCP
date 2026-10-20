"""Guarded plugin tests use a deterministic controller double; never open FL."""

import copy
import json
import math
from pathlib import Path

import pytest

from fl_studio_mcp.utils import plugin_state as state
from fl_studio_mcp.utils.plugin_display import parse_display_number


class Connection:
    def __init__(self, directory):
        self._hardware_dir = directory
        self.catalog = json.loads(
            (Path(__file__).parents[1] / "fl_controller/native_catalog.json").read_text()
        )
        self.context = dict(
            session="test-session",
            project_generation=1,
            channel_generation=1,
            project_title="Song",
            channels=[dict(index=0, name="Lead", type=2)],
        )
        self.plugin = "Test Synth"
        self.user_name = "Custom synth name"
        self.track_name = "Lead FX"
        self.params = [
            dict(index=0, name="Volume", value=0.25, value_string="25%"),
            dict(index=1, name="Attack", value=0.2, value_string="120 ms"),
            dict(index=2, name="", value=0, value_string=""),
            dict(index=3, name="Release", value=0.3, value_string="500 ms"),
        ]
        self.valid = True
        self.playing = False
        self.recording = False
        self.calls = []
        self.writes = []
        self.ignore_write = False
        self.failure = None
        self.after_preflight = None
        self.after_write = None
        self.receipt = None
        self.catalog_mode = None
        self.read_count = 0

    def native(self, name, args):
        if name == "mixer.getTrackInfo":
            assert args == [2]
            return 10
        if name == "channels.getChannelName":
            assert args == [0, True]
            return self.context["channels"][0]["name"]
        if name == "mixer.getTrackName":
            assert args == [3]
            return self.track_name
        assert args[-1] is True
        if name == "plugins.isValid":
            return self.valid
        if name == "plugins.getPluginName":
            assert len(args) == 4
            return self.user_name if args[2] else self.plugin
        if name == "plugins.getParamCount":
            return len(self.params)
        raise AssertionError(name)

    def send_command(self, action, params, timeout):
        self.calls.append((action, copy.deepcopy(params)))
        if action == "bridge.capabilities":
            return dict(success=True, native=copy.deepcopy(self.catalog))
        if action == "production.inspect":
            return dict(
                success=True,
                context=copy.deepcopy(self.context),
                is_playing=self.playing,
                is_recording=self.recording,
            )
        if action == "plugins.getParams":
            assert params["use_global"] is True
            assert params["max_params"] == len(self.params)
            self.read_count += 1
            result = copy.deepcopy(self.params)
            if self.catalog_mode == "missing":
                result.pop()
            elif self.catalog_mode == "duplicate":
                result[1]["index"] = 0
            return dict(success=True, params=result)
        assert action == "bridge.batch"
        steps = params["steps"]
        is_write = any(s["params"]["function"] == "plugins.setParamValue" for s in steps)
        if not is_write:
            return dict(
                success=True,
                applied=len(steps),
                results=[
                    dict(value=self.native(step["params"]["function"], step["params"]["args"]))
                    for step in steps
                ],
            )
        if params["dry_run"]:
            if self.after_preflight:
                self.after_preflight(self)
            return dict(success=True, validated=len(steps), applied=0)
        if self.receipt:
            assert json.loads(self.receipt.read_text())["status"] == "dispatching"
        self.writes.append(copy.deepcopy(params))
        for n, step in enumerate(steps):
            p = step["params"]
            assert p["allow_write"] is True
            value, parameter, index, slot, pickup, global_index = p["args"]
            assert pickup == 0 and global_index is True
            assert (index == 0 and slot == -1) or (index == 3 and slot == 2)
            if not self.ignore_write:
                self.params[parameter]["value"] = value
                self.params[parameter]["value_string"] = f"{value * 100:g}%"
            if self.failure == "partial":
                return dict(
                    success=False,
                    error="Partial",
                    applied=n,
                    failed_step=n,
                    partial=True,
                    results=[],
                )
        if self.after_write:
            self.after_write(self)
        if self.failure == "timeout":
            raise TimeoutError("simulated after-dispatch timeout")
        if self.failure == "error":
            return dict(success=False, error="ack lost", applied=0, results=[])
        if self.failure == "malformed_ack":
            return dict(success=True, applied=len(steps), results=[dict(error="failed")])
        return dict(success=True, applied=len(steps), results=[dict(value=None)] * len(steps))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(state.time, "sleep", lambda _: None)
    return Connection(tmp_path)


def change(**kwargs):
    result = dict(parameter=0, expected_name="Volume", value=0.6)
    result.update(kwargs)
    return result


def inspect(conn, **kwargs):
    return state.inspect_plugin(0, connection=conn, **kwargs)


def apply(conn, tmp_path, changes=None, **kwargs):
    revision = inspect(conn)["revision"]
    conn.receipt = tmp_path / "receipt.json"
    return state.apply_plugin(
        0,
        conn.plugin,
        revision,
        changes or [change()],
        apply=True,
        receipt_file=conn.receipt,
        connection=conn,
        **kwargs,
    )


def test_generator_inspect_true_identity_query_and_global_index(conn):
    result = inspect(conn, query="e", limit=1)
    assert result["target"]["plugin"] == "Test Synth"
    assert result["target"]["user_name"] == "Custom synth name"
    assert result["target"]["kind"] == "generator"
    assert result["parameters"][0]["parameter"] == 0
    assert result["matched"] == 2 and result["next_offset"] == 1
    assert result["project"]["session"] == "test-session"
    second = inspect(conn, query="e", offset=1, limit=1)
    assert second["parameters"][0]["parameter"] == 3
    assert second["revision"] == result["revision"]
    assert not conn.writes


@pytest.mark.parametrize("valid", [True, 1])
def test_native_integer_or_boolean_plugin_validity_is_accepted(conn, valid):
    conn.valid = valid
    result = inspect(conn)
    assert result["success"] and result["target"]["plugin"] == conn.plugin


@pytest.mark.parametrize("valid", [False, 0, 2, -1, 1.0, "1", "true", None, [], {}])
def test_invalid_or_ambiguous_native_plugin_validity_is_rejected(conn, valid):
    conn.valid = valid
    with pytest.raises(ValueError, match="No valid plugin"):
        inspect(conn)
    assert not conn.writes


@pytest.mark.parametrize("field", ["playing", "recording"])
def test_native_validity_normalization_does_not_weaken_transport_booleans(conn, field):
    conn.valid = 1
    setattr(conn, field, 0)
    with pytest.raises(RuntimeError, match="Incomplete transport"):
        inspect(conn)


def test_effect_inspect_and_apply_use_exact_mixer_slot(conn, tmp_path):
    initial = state.inspect_plugin(3, slot_index=2, connection=conn)
    assert initial["target"]["name"] == "Lead FX"
    assert initial["target"]["kind"] == "effect"
    result = state.apply_plugin(
        3,
        conn.plugin,
        initial["revision"],
        [change()],
        slot_index=2,
        apply=True,
        receipt_file=tmp_path / "effect.json",
        connection=conn,
    )
    assert result["status"] == "verified" and len(conn.writes) == 1
    assert conn.writes[0]["steps"][0]["params"]["args"] == [0.6, 0, 3, 2, 0, True]


def test_preview_does_not_mutate_or_create_receipt(conn, tmp_path):
    initial = inspect(conn)
    receipt = tmp_path / "preview.json"
    result = state.apply_plugin(
        0, conn.plugin, initial["revision"], [change()], receipt_file=receipt, connection=conn
    )
    assert result["success"] and result["status"] == "preflighted"
    assert result["previous_host_values"][0]["value"] == 0.25
    assert not conn.writes and not receipt.exists()


def test_apply_persists_one_dispatch_and_two_independent_readbacks(conn, tmp_path):
    result = apply(
        conn,
        tmp_path,
        [change(expected_display="60%"), change(parameter=1, expected_name="Attack", value=0.7)],
    )
    assert result["success"] and result["status"] == "verified"
    assert len(conn.writes) == 1 and len(result["readbacks"]) == 2
    assert conn.read_count == 5  # inspect, preflight, final guard, two later callbacks
    assert json.loads(conn.receipt.read_text())["status"] == "verified"
    assert result["verification_scope"].startswith("host normalized")


@pytest.mark.parametrize(
    "kwargs",
    [dict(index=True), dict(slot_index=-2), dict(limit=0), dict(offset=-1), dict(query=42)],
)
def test_invalid_inspect_arguments_do_not_contact_host(conn, kwargs):
    options = dict(index=0, connection=conn)
    options.update(kwargs)
    with pytest.raises(ValueError):
        state.inspect_plugin(**options)
    assert not conn.calls


@pytest.mark.parametrize(
    "changes",
    [
        [],
        [change(parameter=True)],
        [change(parameter=-1)],
        [change(value=True)],
        [change(value=math.nan)],
        [change(value=1.1)],
        [change(expected_name="")],
        [change(unknown=3)],
        [change(expected_display=1)],
        [change(), change()],
    ],
)
def test_invalid_changes_do_not_contact_host(conn, changes):
    with pytest.raises(ValueError):
        state.apply_plugin(0, conn.plugin, "a" * 64, changes, connection=conn)
    assert not conn.calls


@pytest.mark.parametrize("field", ["plugins.getParamName", "plugins.setParamValue"])
def test_unadvertised_or_changed_signature_rejected(conn, field):
    initial = inspect(conn)
    conn.catalog[field]["arguments"] = ["wrong"]
    with pytest.raises(RuntimeError, match="signature"):
        state.apply_plugin(0, conn.plugin, initial["revision"], [change()], connection=conn)
    assert not conn.writes


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_incomplete_or_duplicate_parameter_catalog_rejected(conn, mode):
    conn.catalog_mode = mode
    with pytest.raises(RuntimeError, match="Incomplete"):
        inspect(conn)


@pytest.mark.parametrize("plugin", ["", "   ", None])
def test_ambiguous_plugin_identity_rejected(conn, plugin):
    conn.plugin = plugin
    with pytest.raises(RuntimeError, match="identity"):
        inspect(conn)


def test_duplicate_parameter_names_remain_unambiguous_by_index(conn, tmp_path):
    conn.params[1]["name"] = "Volume"
    result = apply(conn, tmp_path, [change(parameter=1)])
    assert result["success"] and conn.params[0]["value"] == 0.25
    assert conn.params[1]["value"] == 0.6


def test_non_normalized_parameter_is_visible_but_not_writable(conn):
    conn.params[0]["value"] = -1
    initial = inspect(conn)
    assert not initial["parameters"][0]["writable_normalized"]
    with pytest.raises(ValueError, match="normalized baseline"):
        state.apply_plugin(0, conn.plugin, initial["revision"], [change()], connection=conn)
    assert not conn.writes


@pytest.mark.parametrize("mutation", ["value", "name", "session", "generation"])
def test_revision_guards_full_catalog_and_project_identity(conn, mutation):
    revision = inspect(conn, limit=1)["revision"]
    if mutation in ("value", "name"):
        conn.params[3][mutation] = 0.9 if mutation == "value" else "Other release"
    elif mutation == "session":
        conn.context["session"] = "reloaded-controller"
    else:
        conn.context["project_generation"] += 1
    with pytest.raises(ValueError, match="revision changed"):
        state.apply_plugin(0, conn.plugin, revision, [change()], connection=conn)
    assert not conn.writes


def test_expected_names_are_exact(conn):
    revision = inspect(conn)["revision"]
    with pytest.raises(ValueError, match="expected_plugin"):
        state.apply_plugin(0, "test synth", revision, [change()], connection=conn)
    with pytest.raises(ValueError, match="expected_name"):
        state.apply_plugin(
            0, conn.plugin, revision, [change(expected_name="volume")], connection=conn
        )


@pytest.mark.parametrize("field", ["playing", "recording"])
def test_stopped_transport_required(conn, field):
    setattr(conn, field, True)
    revision = inspect(conn)["revision"]
    with pytest.raises(ValueError, match="stopped"):
        state.apply_plugin(0, conn.plugin, revision, [change()], connection=conn)
    assert not conn.writes


def test_change_after_preflight_stops_before_dispatch(conn, tmp_path):
    conn.after_preflight = lambda c: c.params[0].update(value=0.9)
    result = apply(conn, tmp_path)
    assert result["status"] == "not_applied" and not result["success"]
    assert not conn.writes
    assert json.loads(conn.receipt.read_text())["status"] == "not_applied"


@pytest.mark.parametrize("mode", ["plugin", "layout", "project"])
def test_post_write_identity_or_layout_change_is_unknown(conn, tmp_path, mode):
    def mutate(c):
        if mode == "plugin":
            c.plugin = "Different Synth"
        elif mode == "layout":
            c.params[3]["name"] = "Different parameter"
        else:
            c.context["project_generation"] += 1

    conn.after_write = mutate
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and not result["success"]
    assert len(conn.writes) == 1


def test_ignored_writes_do_not_pass_or_get_retried(conn, tmp_path):
    conn.ignore_write = True
    result = apply(conn, tmp_path)
    assert result["status"] == "verification_failed" and not result["success"]
    assert len(conn.writes) == 1 and len(result["readbacks"]) == 5


def test_optional_display_detects_value_cache_only_match(conn, tmp_path):
    result = apply(conn, tmp_path, [change(expected_display="different actual setting")])
    assert result["status"] == "verification_failed" and len(conn.writes) == 1


@pytest.mark.parametrize("tolerance", [True, False, None, "0.001", math.nan, math.inf,
                                       -math.inf, -0.001, 0.010001, 1])
def test_invalid_normalized_tolerance_rejected_before_host_calls(conn, tolerance):
    with pytest.raises(ValueError, match="normalized_tolerance"):
        state.apply_plugin(
            0, conn.plugin, "a" * 64, [change(normalized_tolerance=tolerance)], connection=conn
        )
    assert not conn.calls


@pytest.mark.parametrize("tolerance", [None, 0, 0.001, 0.00125, 0.002, 0.01])
def test_explicit_tolerance_controls_quantized_parameter_readback(conn, tmp_path, tolerance):
    conn.after_write = lambda c: c.params[0].update(value=0.46875, value_string="6% left")
    requested = change(value=0.47, expected_display="6% left")
    if tolerance is not None:
        requested["normalized_tolerance"] = tolerance
    original = copy.deepcopy(requested)
    result = apply(conn, tmp_path, [requested])
    effective = state.TOLERANCE if tolerance is None else tolerance
    matches = abs(0.46875 - 0.47) <= effective
    assert result["success"] is matches
    assert result["status"] == ("verified" if matches else "verification_failed")
    assert len(conn.writes) == 1
    assert len(result["readbacks"]) == (2 if matches else 5)
    assert requested == original
    assert result["changes"][0]["normalized_tolerance"] == effective
    for evidence in result["readback_verification"]:
        check = evidence["changes"][0]
        assert check["requested_value"] == 0.47 and check["actual_value"] == 0.46875
        assert check["delta"] == 0.46875 - 0.47
        assert check["normalized_tolerance"] == effective
        assert check["normalized_matches"] is matches
        assert check["display_matches"] is True and check["actual_display"] == "6% left"
    assert any("quantize" in limitation for limitation in result["limitations"])
    assert json.loads(conn.receipt.read_text()) == result


def test_zero_normalized_tolerance_accepts_an_exact_value(conn, tmp_path):
    result = apply(conn, tmp_path, [change(normalized_tolerance=0)])
    assert result["success"]
    assert result["readback_verification"][-1]["changes"][0]["delta"] == 0


def test_explicit_tolerance_does_not_relax_exact_display(conn, tmp_path):
    conn.after_write = lambda c: c.params[0].update(value=0.46875, value_string="6% left")
    result = apply(
        conn, tmp_path,
        [change(value=0.47, normalized_tolerance=0.002, expected_display="Centered")],
    )
    assert result["status"] == "verification_failed" and not result["success"]
    check = result["readback_verification"][-1]["changes"][0]
    assert check["normalized_matches"] and check["display_matches"] is False
    assert len(conn.writes) == 1


def test_normalized_tolerance_is_isolated_per_change(conn, tmp_path):
    def quantize(c):
        c.params[0].update(value=0.46875, value_string="6% left")
        c.params[1].update(value=0.46875, value_string="6% left")

    conn.after_write = quantize
    result = apply(
        conn, tmp_path,
        [change(value=0.47, normalized_tolerance=0.002),
         change(parameter=1, expected_name="Attack", value=0.47)],
    )
    assert not result["success"] and result["status"] == "verification_failed"
    first, second = result["readback_verification"][-1]["changes"]
    assert first["matches"] and not second["matches"]
    assert first["normalized_tolerance"] == 0.002
    assert second["normalized_tolerance"] == state.TOLERANCE
    assert len(conn.writes) == 1


def test_partial_batch_preserves_evidence_without_retry_or_rollback(conn, tmp_path):
    conn.failure = "partial"
    result = apply(
        conn, tmp_path, [change(), change(parameter=1, expected_name="Attack", value=0.7)]
    )
    assert result["status"] == "unknown" and not result["success"]
    assert result["response"]["partial"]
    assert conn.params[0]["value"] == 0.6 and conn.params[1]["value"] == 0.2
    assert len(conn.writes) == 1


@pytest.mark.parametrize("failure", ["error", "malformed_ack"])
def test_failed_ack_never_reported_as_success_even_when_values_match(conn, tmp_path, failure):
    conn.failure = failure
    result = apply(conn, tmp_path)
    assert result["status"] == "observed_after_error" and not result["success"]
    assert len(conn.writes) == 1


def test_timeout_never_retries_a_possible_mutation(conn, tmp_path):
    conn.failure = "timeout"
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and not result["success"]
    assert conn.params[0]["value"] == 0.6 and len(conn.writes) == 1
    assert json.loads(conn.receipt.read_text())["status"] == "unknown"


def test_occupied_receipt_rejects_before_mutation(conn, tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text("original receipt")
    with pytest.raises(FileExistsError):
        apply(conn, tmp_path)
    assert not conn.writes and receipt.read_text() == "original receipt"


def test_receipt_persistence_failure_before_dispatch_stops_safely(conn, tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("storage failure")

    monkeypatch.setattr(state, "atomic_json", fail)
    result = apply(conn, tmp_path)
    assert result["status"] == "not_applied" and not conn.writes


def test_receipt_failure_after_dispatch_is_unknown_without_retry(conn, tmp_path, monkeypatch):
    original = state.atomic_json

    def fail_after_write(path, report):
        if conn.writes:
            raise OSError("storage failure")
        original(path, report)

    monkeypatch.setattr(state, "atomic_json", fail_after_write)
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and len(conn.writes) == 1
    assert "receipt_persistence_error" in result


def test_tool_registration_has_both_workflows():
    from fl_studio_mcp.tools.plugin_state import register_plugin_state_tools

    class Recorder:
        def __init__(self):
            self.names = []

        def tool(self, **kwargs):
            # Stands in for FastMCP's decorator, which accepts annotations=.
            def register(function):
                self.names.append(function.__name__)
                return function

            return register

    recorder = Recorder()
    register_plugin_state_tools(recorder)
    assert recorder.names == ["fl_plugin_inspect", "fl_plugin_apply"]


@pytest.mark.parametrize(
    "display,unit,expected",
    [
        ("-12.50 dB", "dB", -12.5),
        (" +.5 dBTP ", "dBTP", 0.5),
        ("-0.3 DBTP", "dbtp", -0.3),
        ("+60.0%", "%", 60),
        ("120 ms", "ms", 120),
        ("120ms", "s", 0.12),
        ("1.25 s", "ms", 1250),
        ("1.25kHz", "Hz", 1250),
        ("20.00k Hz", "Hz", 20000),
        ("250 Hz", "kHz", 0.25),
        ("4.00:1", "ratio", 4),
        (" +4.00 : 1.0 ", "ratio", 4),
        ("-1.25", "", -1.25),
        ("1e2 ms", "s", 0.1),
    ],
)
def test_literal_numeric_display_parser(display, unit, expected):
    assert parse_display_number(display, unit) == expected


@pytest.mark.parametrize(
    "display,unit",
    [
        (None, "dB"), (True, "dB"), (12, "dB"), ("", "dB"), (" ", "dB"),
        ("NaN dB", "dB"), ("Infinity dB", "dB"), ("-inf dB", "dB"),
        ("1e999 dB", "dB"), ("1e999999999999999999999 dB", "dB"),
        ("1e999999 kHz", "Hz"), ("1e-999 dB", "dB"), ("1e-999999 Hz", "kHz"),
        ("-12 dBTP", "dB"), ("-12 dB", "dBTP"), ("12%", ""), ("12", "%"),
        ("1 ms", "Hz"), ("1 kHz", "ms"), ("1 second", "s"),
        ("12 dB / 1 dB", "dB"), ("12 dB (peak)", "dB"), ("12,5 dB", "dB"),
        ("4.00", "ratio"), ("4.00:2", "ratio"), ("4:0", "ratio"),
        ("4:1", ""), ("NaN:1", "ratio"), ("inf:1", "ratio"),
        ("1", None), ("1", True), ("1", " "), ("1", "LUFS"),
    ],
)
def test_numeric_parser_rejects_missing_ambiguous_nonfinite_or_wrong_units(display, unit):
    with pytest.raises(ValueError):
        parse_display_number(display, unit)


@pytest.mark.parametrize(
    "numeric",
    [
        None, True, [], {}, {"value": 1, "unit": "dB"},
        {"value": 1, "unit": "dB", "tolerance": 0, "other": 1},
        {"value": True, "unit": "dB", "tolerance": 0},
        {"value": "1", "unit": "dB", "tolerance": 0},
        {"value": math.nan, "unit": "dB", "tolerance": 0},
        {"value": math.inf, "unit": "dB", "tolerance": 0},
        {"value": -math.inf, "unit": "dB", "tolerance": 0},
        {"value": 10**400, "unit": "dB", "tolerance": 0},
        {"value": 1, "unit": "dB", "tolerance": True},
        {"value": 1, "unit": "dB", "tolerance": "1"},
        {"value": 1, "unit": "dB", "tolerance": -1},
        {"value": 1, "unit": "dB", "tolerance": math.nan},
        {"value": 1, "unit": "dB", "tolerance": math.inf},
        {"value": 1, "unit": "dB", "tolerance": 10**400},
        {"value": 1, "unit": None, "tolerance": 0},
        {"value": 1, "unit": True, "tolerance": 0},
        {"value": 1, "unit": " ", "tolerance": 0},
        {"value": 1, "unit": "LUFS", "tolerance": 0},
    ],
)
def test_invalid_numeric_expectations_rejected_before_host_calls(conn, numeric):
    with pytest.raises(ValueError):
        state.apply_plugin(
            0, conn.plugin, "a" * 64, [change(expected_numeric=numeric)], connection=conn
        )
    assert not conn.calls


@pytest.mark.parametrize(
    "display,expected,unit,tolerance,actual,delta,matches",
    [
        ("-12.50 dB", -12.4, "dB", 0.1, -12.5, -0.1, True),
        ("-12.50 dB", -12.4, "dB", 0.099, -12.5, -0.1, False),
        ("1250ms", 1.25, "s", 0, 1.25, 0, True),
        ("1.25kHz", 1250, "Hz", 0, 1250, 0, True),
        ("4.00:1", 4, "ratio", 0, 4, 0, True),
        ("60%", 60, "%", 0, 60, 0, True),
        ("0.125", 0.125, "", 0, 0.125, 0, True),
    ],
)
def test_numeric_readbacks_and_receipt_include_independent_display_evidence(
    conn, tmp_path, display, expected, unit, tolerance, actual, delta, matches
):
    conn.after_write = lambda c: c.params[0].update(value_string=display)
    requested = change(expected_numeric=dict(value=expected, unit=unit, tolerance=tolerance))
    original = copy.deepcopy(requested)
    result = apply(conn, tmp_path, [requested])
    assert result["success"] is matches
    assert result["status"] == ("verified" if matches else "verification_failed")
    assert len(result["readbacks"]) == (2 if matches else 5)
    assert len(conn.writes) == 1 and requested == original
    for readback in result["readback_verification"]:
        evidence = readback["changes"][0]
        assert evidence["normalized_matches"] is True
        assert evidence["expected_numeric"] == requested["expected_numeric"]
        assert evidence["actual_numeric"] == actual
        assert evidence["numeric_delta"] == delta
        assert evidence["numeric_matches"] is matches
        assert evidence["numeric_error"] is None
        assert evidence["matches"] is matches
    assert json.loads(conn.receipt.read_text()) == result


@pytest.mark.parametrize("display", ["-12 dBTP", "-12", "-12 dB / -6 dB", "", "NaN dB"])
def test_failed_numeric_parse_is_explicit_readback_failure_without_retry(conn, tmp_path, display):
    conn.after_write = lambda c: c.params[0].update(value_string=display)
    result = apply(
        conn, tmp_path, [change(expected_numeric=dict(value=-12, unit="dB", tolerance=1))]
    )
    assert not result["success"] and result["status"] == "verification_failed"
    assert len(conn.writes) == 1 and len(result["readbacks"]) == 5
    for readback in result["readback_verification"]:
        evidence = readback["changes"][0]
        assert evidence["normalized_matches"] is True
        assert evidence["actual_numeric"] is None and evidence["numeric_delta"] is None
        assert evidence["numeric_matches"] is False and evidence["numeric_error"]
    assert json.loads(conn.receipt.read_text()) == result


@pytest.mark.parametrize("failure", ["normalized", "exact_display"])
def test_numeric_match_does_not_override_other_failed_checks(conn, tmp_path, failure):
    conn.after_write = lambda c: c.params[0].update(
        value=0.5 if failure == "normalized" else 0.6, value_string="-12 dB"
    )
    requested = change(expected_numeric=dict(value=-12, unit="dB", tolerance=0))
    if failure == "exact_display":
        requested["expected_display"] = "-12.00 dB"
    result = apply(conn, tmp_path, [requested])
    evidence = result["readback_verification"][-1]["changes"][0]
    assert evidence["numeric_matches"] is True and evidence["matches"] is False
    assert not result["success"] and result["status"] == "verification_failed"
    assert len(conn.writes) == 1


def test_numeric_difference_overflow_remains_finite_json_evidence(conn, tmp_path):
    conn.after_write = lambda c: c.params[0].update(value_string="-1e308 dB")
    result = apply(
        conn, tmp_path, [change(expected_numeric=dict(value=1e308, unit="dB", tolerance=0))]
    )
    evidence = result["readback_verification"][-1]["changes"][0]
    assert not result["success"] and evidence["actual_numeric"] == -1e308
    assert evidence["numeric_delta"] is None and evidence["numeric_error"]
    assert json.loads(conn.receipt.read_text()) == result


def test_numeric_preview_preserves_requested_expectation_without_mutation(conn):
    initial = inspect(conn)
    requested = change(expected_numeric=dict(value=0.6, unit="", tolerance=0))
    result = state.apply_plugin(0, conn.plugin, initial["revision"], [requested], connection=conn)
    assert result["success"] and result["dry_run"] and not conn.writes
    assert result["changes"][0]["expected_numeric"] == requested["expected_numeric"]


def test_numeric_expectation_is_copied_before_host_callbacks(conn, tmp_path):
    requested = change(expected_numeric=dict(value=-12, unit="dB", tolerance=0))
    conn.after_preflight = lambda _: requested["expected_numeric"].update(tolerance=100)
    conn.after_write = lambda c: c.params[0].update(value_string="-6 dB")
    result = apply(conn, tmp_path, [requested])
    assert not result["success"]
    assert result["changes"][0]["expected_numeric"]["tolerance"] == 0
    assert result["readback_verification"][-1]["changes"][0]["numeric_matches"] is False
    assert len(conn.writes) == 1


def test_numeric_match_requires_two_consecutive_independent_readbacks(conn, tmp_path):
    send = conn.send_command
    displays = iter(["-12 dB", "-9 dB", "-12 dB", "-12 dB"])

    def send_with_changed_displays(action, params, timeout):
        if action == "plugins.getParams" and conn.writes:
            conn.params[0]["value_string"] = next(displays)
        return send(action, params, timeout)

    conn.send_command = send_with_changed_displays
    result = apply(
        conn, tmp_path, [change(expected_numeric=dict(value=-12, unit="dB", tolerance=0))]
    )
    assert result["success"] and len(conn.writes) == 1
    assert [
        readback["changes"][0]["numeric_matches"] for readback in result["readback_verification"]
    ] == [True, False, True, True]
    assert json.loads(conn.receipt.read_text()) == result


def test_display_readbacks_wait_for_initial_settle_then_independent_spacing(
    conn, tmp_path, monkeypatch,
):
    now = 0.0
    events = []
    send = conn.send_command

    def sleep(seconds):
        nonlocal now
        events.append(("sleep", seconds))
        now += seconds

    def delayed_display(action, params, timeout):
        if action == "plugins.getParams" and conn.writes:
            events.append(("read", now))
            conn.params[0]["value_string"] = "-6 dB" if now >= 0.4 else "0 dB"
        return send(action, params, timeout)

    conn.after_write = lambda _: events.append(("dispatch", now))
    conn.send_command = delayed_display
    monkeypatch.setattr(state.time, "sleep", sleep)
    requested = change(expected_numeric=dict(value=-6, unit="dB", tolerance=0))
    result = apply(conn, tmp_path, [requested])

    assert result["success"] and result["status"] == "verified"
    assert len(conn.writes) == 1 and len(result["readbacks"]) == 2
    assert [event for event, _ in events] == ["dispatch", "sleep", "read", "sleep", "read"]
    assert [value for event, value in events if event == "sleep"] == [0.4, 0.2]
    assert [value for event, value in events if event == "read"] == pytest.approx([0.4, 0.6])
    assert result["changes"][0]["normalized_tolerance"] == state.TOLERANCE
    assert result["changes"][0]["expected_numeric"] == requested["expected_numeric"]
    assert json.loads(conn.receipt.read_text()) == result


def test_settling_keeps_failed_readback_schedule_bounded_without_rewriting(
    conn, tmp_path, monkeypatch,
):
    sleeps = []
    monkeypatch.setattr(state.time, "sleep", sleeps.append)
    conn.ignore_write = True
    result = apply(conn, tmp_path)
    assert not result["success"] and result["status"] == "verification_failed"
    assert len(conn.writes) == 1 and len(result["readbacks"]) == 5
    assert sleeps == [0.4, 0.2, 0.2, 0.2, 0.2]
    assert sum(sleeps) == pytest.approx(1.2)


def test_preview_does_not_wait_for_post_dispatch_readbacks(conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(state.time, "sleep", sleeps.append)
    revision = inspect(conn)["revision"]
    result = state.apply_plugin(0, conn.plugin, revision, [change()], connection=conn)
    assert result["success"] and result["dry_run"] and not conn.writes
    assert sleeps == []
