"""Background save contract: one dispatch, independent host evidence, no input."""

import ast
import copy
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import project_native

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Lead.flp - FL Studio 2026"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = SimpleNamespace(dirty=1, playing=False, recording=False, safe=True,
                            writes=[], calls=[], handled=8, save_error=False,
                            remain_dirty=False, change_after=False)
    rows = [dict(handle="123", pid=os.getpid(), title=TITLE,
                 enabled=True, **{"class": "TFruityLoopsMainForm"})]
    monkeypatch.setattr(project_native.native_windows, "windows", lambda: copy.deepcopy(rows))

    def save(*args):
        state.writes.append(args)
        if not state.remain_dirty:
            state.dirty = 0
        if state.change_after:
            ns["production_on_project_load"](0)
        if state.save_error:
            raise TimeoutError("Lost response after save")
        return state.handled

    modules = dict(
        channels=SimpleNamespace(channelCount=lambda *a: 0, getShuffle=lambda: 0),
        general=SimpleNamespace(getVersion=lambda: 45, getRecPPQ=lambda: 96,
                                getChangedFlag=lambda: state.dirty,
                                safeToEdit=lambda: state.safe),
        mixer=SimpleNamespace(getCurrentTempo=lambda *a: 140000),
        patterns=SimpleNamespace(patternNumber=lambda: 1, getPatternName=lambda *a: "Beat"),
        transport=SimpleNamespace(isPlaying=lambda: state.playing,
                                  isRecording=lambda: state.recording, globalTransport=save),
        ui=SimpleNamespace(getProgTitle=lambda: "FL Studio 2026"),
        midi=SimpleNamespace(FPT_Save=92, GT_Global=8),
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    ns = dict(SCRIPT_DIR=ROOT / "fl_controller", extended_dispatch=lambda *a: {},
              ACTIVE_MIDI_FLAGS=38)
    source = ROOT / "fl_controller/mcp_production.py"
    exec(compile(source.read_text(), str(source), "exec"), ns)

    def send(action, params, timeout):
        state.calls.append((action, copy.deepcopy(params)))
        try:
            return {"success": True, **ns["extended_dispatch"](action, params)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    state.ns, state.modules, state.rows = ns, modules, rows
    state.conn = SimpleNamespace(_hardware_dir=tmp_path, send_command=send)
    state.path = tmp_path / "save-receipt.json"
    return state


def inspect(rig):
    return project_native.inspect_native_project(rig.conn)


def save(rig, before=None, apply=True):
    before = before or inspect(rig)
    return project_native.save_native_project(before["revision"], before["window"]["title"],
                                             apply, str(rig.path), rig.conn)


def test_inspection_and_preview_never_save(rig):
    before = inspect(rig)
    assert before["ready"] and before["named"]
    assert before["absolute_path_unknown"] and not before["disk_verified"]
    assert before["qualification"]["qualification"] == "host_state_only"
    result = save(rig, before, apply=False)
    assert result["success"] and result["status"] == "preflighted"
    assert not rig.writes and not rig.path.exists()


def test_saves_as_one_shot_switch_using_real_event_flags_then_two_clean_callbacks(rig):
    result = save(rig)
    assert result["success"] and result["status"] == "saved_current_host"
    assert rig.writes == [(92, 1, 38, 8)]
    assert len(result["readbacks"]) == 2
    assert result["absolute_path_unknown"] and not result["disk_verified"]
    assert result["desktop_input"] is False
    assert result["transport_return"] == 8
    assert result["dispatch_ack"] == "recognized_global"
    assert json.loads(rig.path.read_text())["status"] == "saved_current_host"


@pytest.mark.parametrize("handled,ack", [
    (-1, "recognized_cannot"), (0, "recognized_none"), (1, "recognized_plugin"),
    (2, "recognized_form"), (4, "recognized_menu"), (5, "recognized_delayed"),
    (15, "recognized_all"), (194829808, "unrecognized"), (None, "unrecognized"),
    (True, "unrecognized"),
])
def test_return_diagnostics_do_not_replace_dirty_to_clean_evidence(rig, handled, ack):
    rig.handled = handled
    result = save(rig)
    assert result["success"] and result["status"] == "saved_current_host"
    assert result["transport_return"] == handled
    assert result["dispatch_ack"] == ack
    assert result["response"]["handled"] == handled
    assert "error" not in result["response"]
    assert len(result["readbacks"]) == 2
    assert not result["disk_verified"] and result["absolute_path_unknown"]
    persisted = json.loads(rig.path.read_text())
    assert persisted["transport_return"] == handled and persisted["dispatch_ack"] == ack
    assert len(rig.writes) == 1


def test_already_clean_is_a_receipted_noop_without_event_permissions(rig):
    rig.dirty = 0
    rig.ns["ACTIVE_MIDI_FLAGS"] = None
    result = save(rig)
    assert result["success"] and result["status"] == "already_clean"
    assert result["dispatched"] is False and result["dispatch_ack"] == "not_dispatched"
    assert not rig.writes
    assert not any(action == "project.save_current" and not params["dry_run"]
                   for action, params in rig.calls)
    assert json.loads(rig.path.read_text())["status"] == "already_clean"
    assert "transport_return" not in result and "readbacks" not in result


def test_already_clean_preview_has_no_receipt_or_save_claim(rig):
    rig.dirty = 0
    result = save(rig, apply=False)
    assert result["success"] and result["status"] == "already_clean"
    assert not result["dispatched"] and not rig.writes and not rig.path.exists()


def test_controller_clean_race_does_not_dispatch_or_claim_a_save(rig):
    send = rig.conn.send_command

    def clean_before_dispatch(action, params, wait):
        if action == "project.save_current" and not params["dry_run"]:
            rig.dirty = 0
        return send(action, params, wait)

    rig.conn.send_command = clean_before_dispatch
    result = save(rig)
    assert result["success"] and result["status"] == "already_clean"
    assert result["dispatched"] is False and not rig.writes
    assert len(result["readbacks"]) == 2


@pytest.mark.parametrize("initial,latest", [(0, 1), (1, 0)])
def test_noop_uses_fresh_preview_changed_flag(rig, initial, latest):
    rig.dirty = initial
    send = rig.conn.send_command

    def dirty_before_preview(action, params, wait):
        if action == "project.save_current" and params["dry_run"]:
            rig.dirty = latest
        return send(action, params, wait)

    rig.conn.send_command = dirty_before_preview
    result = save(rig)
    assert result["success"]
    assert result["status"] == ("already_clean" if latest == 0 else "saved_current_host")
    assert len(rig.writes) == latest


def test_autosave_clean_is_still_project_dirty_and_requires_save(rig):
    rig.dirty = 2
    result = save(rig)
    assert result["success"] and result["status"] == "saved_current_host"
    assert result["before"]["native"]["dirty"] == 2 and len(rig.writes) == 1


@pytest.mark.parametrize("dirty", [True, None, -1, 3])
def test_unknown_changed_flag_cannot_dispatch(rig, dirty):
    rig.dirty = dirty
    with pytest.raises(RuntimeError, match="recognized host changed flag"):
        save(rig)
    assert not rig.writes and not rig.path.exists()


@pytest.mark.parametrize("flags", [None, True, -1, 256, "38"])
def test_missing_or_invalid_actual_event_flags_prevent_save(rig, flags):
    rig.ns["ACTIVE_MIDI_FLAGS"] = flags
    result = save(rig)
    assert not result["success"]
    assert not rig.writes
    assert "event permissions" in result["response"]["error"]


def test_stale_revision_stops_before_receipt_or_dispatch(rig):
    before = inspect(rig)
    rig.ns["production_on_project_load"](0)
    with pytest.raises(ValueError, match="revision changed"):
        save(rig, before)
    assert not rig.writes and not rig.path.exists()


@pytest.mark.parametrize("kind", ["modal", "disabled", "multiple", "wrong_pid"])
def test_window_ambiguity_or_modal_prevents_any_save(rig, kind):
    if kind == "modal":
        rig.rows.append(dict(handle="124", pid=os.getpid(), title="Save As",
                             enabled=True, **{"class": "#32770"}))
    elif kind == "disabled":
        rig.rows[0]["enabled"] = False
    elif kind == "multiple":
        rig.rows.append({**rig.rows[0], "handle": "456"})
    else:
        rig.rows[0]["pid"] += 1
    with pytest.raises(RuntimeError):
        inspect(rig)
    assert not rig.writes


def test_unrelated_process_dialog_is_ignored(rig):
    rig.rows.append(dict(handle="124", pid=os.getpid() + 1, title="Other app",
                         enabled=True, **{"class": "#32770"}))
    assert save(rig)["success"]


def test_unnamed_inspection_reports_not_ready(rig):
    rig.rows[0]["title"] = "Untitled - FL Studio 2026"
    before = inspect(rig)
    assert not before["named"] and not before["ready"]
    with pytest.raises(ValueError, match="named .flp"):
        save(rig, before)
    assert not rig.writes


@pytest.mark.parametrize("state", ["playing", "recording"])
def test_transport_prevents_save(rig, state):
    setattr(rig, state, True)
    with pytest.raises(RuntimeError, match="stopped transport"):
        save(rig)
    assert not rig.writes


def test_controller_independently_checks_revision_transport_and_fields(rig):
    before = inspect(rig)
    params = dict(expected_revision=before["revision"], expected_window_title=TITLE, dry_run=False)
    for changes in ({"expected_revision": "0" * 64}, {"dry_run": 0},
                    {"expected_window_title": "Untitled"}, {"pmeflags": 38}):
        with pytest.raises(ValueError):
            rig.ns["project_save_current"]({**params, **changes})
    rig.playing = True
    with pytest.raises(ValueError, match="Stop transport"):
        rig.ns["project_save_current"](params)
    assert not rig.writes


def test_existing_receipt_blocks_save(rig):
    rig.path.write_text("preserve")
    with pytest.raises(FileExistsError):
        save(rig)
    assert rig.path.read_text() == "preserve" and not rig.writes


def test_exception_after_host_call_is_never_retried(rig):
    rig.save_error = True
    result = save(rig)
    assert not result["success"]
    assert result["status"] == "observed_after_error"
    assert result["dispatch_ack"] == "call_exception"
    assert len(result["readbacks"]) == 2
    assert len(rig.writes) == 1


def test_transport_timeout_is_unknown_and_never_retried(rig):
    send = rig.conn.send_command

    def timeout(action, params, wait):
        result = send(action, params, wait)
        if action == "project.save_current" and not params["dry_run"]:
            raise TimeoutError("No correlated response")
        return result

    rig.conn.send_command = timeout
    result = save(rig)
    assert not result["success"] and result["status"] == "unknown"
    assert len(rig.writes) == 1


def test_dirty_readbacks_do_not_settle(rig, monkeypatch):
    monkeypatch.setattr(project_native.time, "sleep", lambda _: None)
    rig.remain_dirty = True
    result = save(rig)
    assert not result["success"] and result["status"] == "verification_failed"
    assert len(result["readbacks"]) == 5 and len(rig.writes) == 1


def test_unrecognized_return_cannot_succeed_without_clean_transition(rig, monkeypatch):
    monkeypatch.setattr(project_native.time, "sleep", lambda _: None)
    rig.handled = 194829808
    rig.remain_dirty = True
    result = save(rig)
    assert not result["success"] and result["status"] == "verification_failed"
    assert result["transport_return"] == 194829808
    assert result["dispatch_ack"] == "unrecognized" and len(rig.writes) == 1


def test_two_clean_readbacks_must_be_consecutive(rig, monkeypatch):
    monkeypatch.setattr(project_native.time, "sleep", lambda _: None)
    send = rig.conn.send_command
    states = iter([0, 1, 0, 0])

    def read_sequence(action, params, wait):
        if action == "production.inspect" and rig.writes:
            rig.dirty = next(states)
        return send(action, params, wait)

    rig.conn.send_command = read_sequence
    result = save(rig)
    assert result["success"] and result["status"] == "saved_current_host"
    assert [item["native"]["dirty"] for item in result["readbacks"]] == [0, 1, 0, 0]


def test_response_identity_mismatch_cannot_claim_save_even_when_clean(rig):
    send = rig.conn.send_command

    def wrong_identity(action, params, wait):
        result = send(action, params, wait)
        if action == "project.save_current" and not params["dry_run"]:
            result["before"]["revision"] = "0" * 64
        return result

    rig.conn.send_command = wrong_identity
    result = save(rig)
    assert not result["success"] and result["status"] == "observed_after_error"
    assert len(rig.writes) == 1


def test_project_change_after_save_is_unknown(rig):
    rig.change_after = True
    result = save(rig)
    assert not result["success"] and result["status"] == "unknown"
    assert len(rig.writes) == 1


def test_receipt_persistence_failure_never_repeats_save(rig, monkeypatch):
    monkeypatch.setattr(project_native, "atomic_json",
                        lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    result = save(rig)
    assert not result["success"] and result["status"] == "unknown"
    assert result["receipt_persistence_error"] == "disk full"
    assert len(rig.writes) == 1


@pytest.mark.parametrize("event", [SimpleNamespace(pmeFlags=38), SimpleNamespace(pmeflags=6),
                                   None, SimpleNamespace(pmeFlags=True),
                                   SimpleNamespace(pmeFlags=256)])
def test_entrypoint_event_flags_are_actual_bounded_and_cleared(tmp_path, event):
    source = ROOT / "fl_controller/mcp_extended.py"
    tree = ast.parse(source.read_text())
    names = {"validate_command", "execute_pending_command", "write_response"}
    code = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                           and node.name in names], type_ignores=[])
    captured = []
    ns = dict(json=json, math=math, time=time, COMMAND_FILE=tmp_path / "command.json",
              RESPONSE_FILE=tmp_path / "response.json", RECEIPTS={}, RECEIPT_COMMANDS={},
              MODULES={"general": SimpleNamespace(safeToEdit=lambda: True)})
    ns["extended_dispatch"] = lambda *a: captured.append(ns["ACTIVE_MIDI_FLAGS"]) or {}
    exec(compile(code, str(source), "exec"), ns)
    ns["COMMAND_FILE"].write_text(json.dumps(dict(
        protocol=2, id="save-test", action="project.save_current", params={},
        expires_at=time.time() + 20)))
    ns["execute_pending_command"](event)
    raw = getattr(event, "pmeFlags", getattr(event, "pmeflags", None))
    assert captured == [raw if type(raw) is int and 0 <= raw <= 255 else None]
    assert ns["ACTIVE_MIDI_FLAGS"] is None


def test_capability_is_typed_and_arbitrary_transport_remains_unavailable(rig):
    # Supply the previous dispatch's capability shape for the production extension.
    rig.ns["_previous_dispatch"] = lambda *a: {"typed": []}
    capabilities = rig.ns["extended_dispatch"]("bridge.capabilities", {})
    assert "project.save_current" in capabilities["typed"]
    assert capabilities["project_native"]["protocol"] == 1
    catalog = json.loads((ROOT / "fl_controller/native_catalog.json").read_text())
    assert "transport.globalTransport" not in catalog


def test_restricted_os_process_identity_disables_save_without_breaking_ping(rig, monkeypatch):
    def unavailable():
        raise RuntimeError("Function is disabled in FL Python")

    monkeypatch.setattr(os, "getpid", unavailable)
    result = rig.ns["extended_dispatch"]("bridge.ping", {})
    assert result["project_native"]["available"] is False
    assert result["project_native"]["controller_pid"] is None
    assert not rig.writes
