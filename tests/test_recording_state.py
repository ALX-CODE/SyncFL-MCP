import copy
import json
from pathlib import Path

import pytest

from fl_studio_mcp.utils import recording_state
from fl_studio_mcp.utils.recording_state import inspect_recording, set_recording_arm


class Connection:
    def __init__(self, path):
        self._hardware_dir = path
        self.native = json.loads(
            (Path(__file__).parents[1] / "fl_controller" / "native_catalog.json").read_text(
                encoding="utf-8"
            )
        )
        self.names = ["Master", "Vocal", "Beat"]
        self.armed = [False, False, False]
        self.filenames = ["", "", "beat.wav"]
        self.dynamic_filenames = False
        self.filename_reads = 0
        self.context = dict(session="session-one", project_generation=0)
        self.playing = False
        self.recording = False
        self.start_on_input = 0
        self.native_boolean = int
        self.calls = []
        self.writes = []
        self.ignore_write = False
        self.fail = None
        self.after_preflight = None
        self.after_write = None
        self.receipt = None
        self.incomplete_batch = False
        self.project_reads = 0
        self.on_project_read = None

    def value(self, name, args):
        if name == "mixer.getTrackInfo":
            assert args == [2]
            return len(self.names) - 1
        if name == "ui.isStartOnInputEnabled":
            return self.start_on_input
        track = args[0]
        if name == "mixer.getTrackName":
            return self.names[track]
        if name == "mixer.isTrackArmed":
            return self.native_boolean(self.armed[track])
        if name == "mixer.getTrackRecordingFileName":
            if self.dynamic_filenames:
                self.filename_reads += 1
                return f"proposed-timestamp-{self.filename_reads}.wav"
            return self.filenames[track]
        raise AssertionError(name)

    def send_command(self, action, params, timeout):
        self.calls.append((action, copy.deepcopy(params)))
        if action == "bridge.capabilities":
            return dict(success=True, native=copy.deepcopy(self.native), typed=["bridge.batch"])
        if action == "production.inspect":
            self.project_reads += 1
            if self.on_project_read:
                self.on_project_read(self)
            return dict(
                success=True,
                context=copy.deepcopy(self.context),
                revision=recording_state._digest(self.context),
                is_playing=self.playing,
                is_recording=self.recording,
            )
        if action == "bridge.batch":
            steps = params["steps"]
            if params["dry_run"]:
                assert steps == [
                    dict(
                        action="native.call",
                        params=dict(function="mixer.armTrack", args=[1], allow_write=True),
                    )
                ]
                if self.after_preflight:
                    self.after_preflight(self)
                return dict(success=True, validated=len(steps), applied=0)
            assert len(steps) <= 255
            results = []
            for step in steps:
                assert step["action"] == "native.call"
                p = step["params"]
                assert not p.get("allow_write")
                assert self.native[p["function"]]["kind"] == "read"
                results.append({"value": self.value(p["function"], p["args"])})
            return dict(
                success=True,
                applied=len(steps),
                results=results[:-1] if self.incomplete_batch else results,
            )
        assert action == "native.call" and params["function"] == "mixer.armTrack"
        assert params["allow_write"] is True
        if self.receipt:
            data = json.loads(self.receipt.read_text())
            assert data["status"] == "dispatching" and data["changed"] is None
        self.writes.append(copy.deepcopy(params))
        target = params["args"][0]
        if not self.ignore_write:
            self.armed[target] = not self.armed[target]
        if self.after_write:
            self.after_write(self)
        if self.fail == "timeout":
            raise TimeoutError("Arm acknowledgment timed out")
        if self.fail == "error":
            return dict(success=False, id="native-request", outcome="unknown", error="lost receipt")
        return dict(success=True, id="native-request", value=None)


@pytest.fixture
def conn(tmp_path):
    return Connection(tmp_path)


def revision(conn):
    return inspect_recording([1], conn)["tracks"][0]["revision"]


def apply(conn, tmp_path, desired=True):
    conn.receipt = tmp_path / "arm-receipt.json"
    return set_recording_arm(1, "Vocal", revision(conn), desired, True, conn.receipt, conn)


def test_inspect_exact_tracks_reports_unknown_inputs_without_take_claims(conn):
    conn.armed[1] = True
    result = inspect_recording([1], conn)
    row = result["tracks"][0]
    assert row["track"] == 1 and row["name"] == "Vocal" and row["armed"] is True
    assert row["recording_filename"] == ""
    assert row["recording_filename_status"] == "reported_proposed"
    assert len(row["revision"]) == 64 and result["armed_tracks"] == [1]
    assert result["recorded_take_verified"] is False
    assert set(result["unknown"].values()) == {None}
    assert result["start_on_input"] is False
    assert result["capabilities"]["signatures"]["mixer.armTrack"] == "index: int"
    assert not conn.writes


def test_default_inspection_is_compact_but_explicit_tracks_include_unarmed(conn):
    conn.armed[0] = True
    result = inspect_recording(connection=conn)
    assert [t["track"] for t in result["tracks"]] == [0]
    assert result["scope"] == "armed_tracks"
    assert result["inspected_track_count"] == 3 and result["armed_tracks"] == [0]
    assert inspect_recording([1], conn)["tracks"][0]["track"] == 1


@pytest.mark.parametrize("native_boolean", [int, bool])
def test_native_boolean_or_integer_flags_are_accepted(conn, native_boolean):
    conn.native_boolean = native_boolean
    conn.start_on_input = native_boolean(True)
    assert inspect_recording([1], conn)["start_on_input"] is True


@pytest.mark.parametrize("invalid", [2, -1, 1.0, "1", None])
def test_invalid_arm_flag_is_not_coerced(conn, invalid):
    conn.native_boolean = lambda value: invalid
    with pytest.raises(RuntimeError, match="arm state"):
        inspect_recording([1], conn)


@pytest.mark.parametrize("invalid", [2, -1, 0.0, "false", None])
def test_invalid_start_flag_is_not_coerced(conn, invalid):
    conn.start_on_input = invalid
    with pytest.raises(RuntimeError, match="start-on-input"):
        inspect_recording([1], conn)


def test_optional_metadata_absence_is_explicit(conn):
    for name in ("mixer.getTrackRecordingFileName", "ui.isStartOnInputEnabled", "mixer.armTrack"):
        del conn.native[name]
    result = inspect_recording([1], conn)
    assert result["tracks"][0]["recording_filename"] is None
    assert result["tracks"][0]["recording_filename_status"] == "unsupported"
    assert result["start_on_input"] is None
    assert result["capabilities"]["desired_arm_supported"] is False
    assert not conn.writes


@pytest.mark.parametrize("name", ["mixer.getTrackInfo", "mixer.getTrackName", "mixer.isTrackArmed"])
def test_required_capability_missing_fails_closed_before_state_calls(conn, name):
    del conn.native[name]
    with pytest.raises(RuntimeError, match="capability missing"):
        inspect_recording([1], conn)
    assert [action for action, _ in conn.calls] == ["bridge.capabilities"]


def test_arm_write_capability_missing_never_toggles(conn, tmp_path):
    old = revision(conn)
    del conn.native["mixer.armTrack"]
    with pytest.raises(RuntimeError, match="mixer.armTrack"):
        set_recording_arm(1, "Vocal", old, True, True, tmp_path / "new.json", conn)
    assert not conn.writes


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "write"),
        ("required", 2),
        ("arguments", []),
        ("arguments", ["track"]),
        ("module", "other"),
    ],
)
def test_incompatible_native_signature_fails_closed(conn, field, value):
    conn.native["mixer.isTrackArmed"][field] = value
    with pytest.raises(RuntimeError, match="capability missing"):
        inspect_recording([1], conn)


def test_inspection_chunks_large_track_lists_and_is_read_only(conn):
    conn.names = [f"Track {i}" for i in range(200)]
    conn.armed = [False] * 200
    conn.filenames = [""] * 200
    result = inspect_recording(connection=conn)
    assert result["inspected_track_count"] == 200 and not result["tracks"]
    assert not conn.writes


@pytest.mark.parametrize("tracks", [[], [True], [-1], [1, 1], [1.0], ["1"], "1"])
def test_bad_track_selection_rejected_before_reading(conn, tracks):
    with pytest.raises(ValueError):
        inspect_recording(tracks, conn)
    assert not conn.calls


def test_nonexistent_track_is_rejected(conn):
    with pytest.raises(ValueError, match="does not exist"):
        inspect_recording([3], conn)


def test_incomplete_native_batch_fails_closed(conn):
    conn.incomplete_batch = True
    with pytest.raises(RuntimeError, match="Incomplete"):
        inspect_recording([1], conn)


def test_project_change_during_read_rejected(conn):
    def change(c):
        if c.project_reads == 2:
            c.context["project_generation"] += 1

    conn.on_project_read = change
    with pytest.raises(RuntimeError, match="Project changed"):
        inspect_recording([1], conn)


def test_dry_run_checks_toggle_without_sending_it(conn):
    result = set_recording_arm(1, "Vocal", revision(conn), True, connection=conn)
    assert result["status"] == "preflighted" and result["needs_toggle"]
    assert result["dry_run"] and not conn.writes and not conn.armed[1]


def test_apply_arms_once_and_verifies_two_later_callbacks(conn, tmp_path):
    result = apply(conn, tmp_path)
    assert result["success"] and result["status"] == "verified" and result["changed"]
    assert conn.armed[1] and len(conn.writes) == 1 and len(result["readbacks"]) == 2
    assert result["response"]["id"] == "native-request"
    assert result["restore_state"] == dict(track=1, expected_name="Vocal", armed=False)
    assert json.loads(conn.receipt.read_text()) == result


def test_desired_disarm_is_explicit_and_toggles_once(conn, tmp_path):
    conn.armed[1] = True
    result = apply(conn, tmp_path, False)
    assert result["success"] and not conn.armed[1] and len(conn.writes) == 1


@pytest.mark.parametrize("desired", [False, True])
def test_already_desired_state_never_toggles(conn, tmp_path, desired):
    conn.armed[1] = desired
    result = apply(conn, tmp_path, desired)
    assert result["success"] and result["status"] == "already_satisfied"
    assert result["changed"] is False and not result["needs_toggle"]
    assert not conn.writes and conn.armed[1] == desired
    assert json.loads(conn.receipt.read_text()) == result


@pytest.mark.parametrize("field", ["playing", "recording"])
def test_transport_guard_prevents_toggle(conn, tmp_path, field):
    setattr(conn, field, True)
    with pytest.raises(ValueError, match="disable recording"):
        apply(conn, tmp_path)
    assert not conn.writes


def test_name_guard_prevents_wrong_target(conn, tmp_path):
    with pytest.raises(ValueError, match="identity"):
        set_recording_arm(1, "Beat", revision(conn), True, True, tmp_path / "new.json", conn)
    assert not conn.writes


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.armed.__setitem__(1, True),
        lambda c: c.context.update(project_generation=1),
    ],
)
def test_stale_revision_prevents_toggle(conn, tmp_path, change):
    old = revision(conn)
    change(conn)
    with pytest.raises(ValueError, match="revision changed"):
        set_recording_arm(1, "Vocal", old, True, True, tmp_path / "new.json", conn)
    assert not conn.writes


def test_state_change_after_preflight_stops_without_reversing_it(conn, tmp_path):
    conn.after_preflight = lambda c: c.armed.__setitem__(1, True)
    result = apply(conn, tmp_path)
    assert not result["success"] and result["status"] == "not_applied"
    assert not conn.writes and conn.armed[1]


def test_timeout_never_replays_a_toggle(conn, tmp_path):
    conn.fail = "timeout"
    result = apply(conn, tmp_path)
    assert not result["success"] and result["status"] == "unknown"
    assert result["changed"] is None and conn.armed[1] and len(conn.writes) == 1
    assert json.loads(conn.receipt.read_text()) == result


def test_lost_ack_with_observed_state_is_not_claimed_success(conn, tmp_path):
    conn.fail = "error"
    result = apply(conn, tmp_path)
    assert not result["success"] and result["status"] == "observed_after_error"
    assert result["changed"] and conn.armed[1] and len(conn.writes) == 1


def test_ignored_native_toggle_fails_readback_without_retry(conn, tmp_path):
    conn.ignore_write = True
    result = apply(conn, tmp_path)
    assert not result["success"] and result["status"] == "verification_failed"
    assert not conn.armed[1] and len(conn.writes) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.names.__setitem__(1, "Different track"),
        lambda c: c.context.update(project_generation=1),
        lambda c: setattr(c, "playing", True),
        lambda c: setattr(c, "recording", True),
    ],
)
def test_identity_or_transport_change_after_toggle_is_unknown(conn, tmp_path, change):
    conn.after_write = change
    result = apply(conn, tmp_path)
    assert not result["success"] and result["status"] == "unknown"
    assert len(conn.writes) == 1


def test_reported_filename_can_change_without_verifying_a_take(conn, tmp_path):
    conn.after_write = lambda c: c.filenames.__setitem__(1, "proposed-future-take.wav")
    result = apply(conn, tmp_path)
    assert result["success"] and result["recorded_take_verified"] is False
    assert result["readbacks"][-1]["tracks"][0]["recording_filename"] == "proposed-future-take.wav"


def test_timestamped_filename_proposals_do_not_change_revision_or_preview(conn):
    conn.dynamic_filenames = True
    old = revision(conn)
    assert revision(conn) == old
    result = set_recording_arm(1, "Vocal", old, False, connection=conn)
    assert result["success"] and not result["needs_toggle"] and not conn.writes
    assert conn.filename_reads >= 3


@pytest.mark.parametrize("desired", [False, True])
def test_timestamped_proposals_do_not_block_apply_or_noop(conn, tmp_path, desired):
    conn.dynamic_filenames = True
    result = apply(conn, tmp_path, desired)
    assert result["success"]
    assert result["status"] == ("verified" if desired else "already_satisfied")
    assert len(conn.writes) == int(desired)
    if desired:
        first, second = [state["tracks"][0] for state in result["readbacks"]]
        assert first["recording_filename"] != second["recording_filename"]
        assert first["revision"] == second["revision"]


@pytest.mark.parametrize("receipt", [None, "relative.json", "C:/bad.txt"])
def test_apply_requires_new_absolute_json_receipt(conn, receipt):
    with pytest.raises(ValueError, match="receipt"):
        set_recording_arm(1, "Vocal", "a" * 64, True, True, receipt, conn)
    assert not conn.calls


def test_receipt_is_not_overwritten(conn, tmp_path):
    path = tmp_path / "occupied.json"
    path.write_text("existing")
    with pytest.raises(FileExistsError):
        set_recording_arm(1, "Vocal", revision(conn), True, True, path, conn)
    assert path.read_text() == "existing" and not conn.writes


def test_receipt_failure_before_dispatch_never_toggles(conn, tmp_path, monkeypatch):
    def fail(path, report):
        raise OSError("Receipt disk error")

    monkeypatch.setattr(recording_state, "atomic_json", fail)
    result = apply(conn, tmp_path)
    assert result["status"] == "not_applied" and not conn.writes


def test_receipt_failure_after_toggle_stays_unknown(conn, tmp_path, monkeypatch):
    original = recording_state.atomic_json

    def fail(path, report):
        if conn.writes:
            raise OSError("Receipt disk error")
        original(path, report)

    monkeypatch.setattr(recording_state, "atomic_json", fail)
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and result["receipt_persistence_error"]
    assert len(conn.writes) == 1 and result["changed"] is None
    assert json.loads(conn.receipt.read_text())["status"] == "dispatching"


def test_registration_forwards_to_backend(monkeypatch):
    from fl_studio_mcp.tools.recording_state import register_recording_state_tools

    class MCP:
        def __init__(self):
            self.registered = {}

        def tool(self):
            def register(fn):
                self.registered[fn.__name__] = fn
                return fn

            return register

    mcp = MCP()
    register_recording_state_tools(mcp)
    monkeypatch.setattr(recording_state, "inspect_recording", lambda tracks: tracks)
    assert mcp.registered["fl_recording_inspect"]([1]) == [1]
    monkeypatch.setattr(recording_state, "set_recording_arm", lambda *args: args)
    assert mcp.registered["fl_recording_arm"](1, "Vocal", "a" * 64, True) == (
        1,
        "Vocal",
        "a" * 64,
        True,
        False,
        None,
    )
