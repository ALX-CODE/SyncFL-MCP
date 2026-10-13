"""Backend score codec and one-dispatch transaction regression tests; no live FL."""

import copy
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import background_piano as piano
from fl_studio_mcp.utils.automation_file import _events
from fl_studio_mcp.utils.piano_score import canonical_note, encode_notes, score_file
from fl_studio_mcp.utils.project_bundle import _inspect, _note, _vlq

SAVE_AND_READ = piano._save_and_read


def event(number, payload):
    return bytes([number]) + (_vlq(len(payload)) if number >= 192 else b"") + payload


def project(target=b"", other=None):
    other = other if other is not None else encode_notes([dict(midi=40)], 96)
    other = bytearray(other)
    for offset in range(0, len(other), 24):
        struct.pack_into("<H", other, offset + 6, 1)
    body = event(199, b"26.1.5\0") + event(65, b"\x01\0") + event(224, target + other)
    for index, name in enumerate(("Keys", "Bass")):
        body += event(64, struct.pack("<H", index)) + event(203, (name + "\0").encode("utf-16le"))
    return (b"FLhd" + struct.pack("<IHHH", 6, 0, 2, 96)
            + b"FLdt" + struct.pack("<I", len(body)) + body)


def test_score_codec_exact_ticks_extended_lengths_and_fl_header():
    raw = encode_notes([dict(midi=60, time_ticks=123, length_ticks=37, velocity=.75,
                             pan=.25, release=1, color=5, slide=True)] * 20, 960)
    note = _note(raw[:24])
    assert (note["position"], note["length"], note["key"]) == (123, 37, 60)
    assert (note["velocity"], note["pan"], note["release"]) == (96, 32, 128)
    assert note["midi_channel"] == 5 and note["slide"]
    data = score_file(raw, 960, "26.1.5")
    assert struct.unpack_from("<HHH", data, 8) == (0x10, 1, 960)
    assert _events(data)[2][-1][1] == raw


@pytest.mark.parametrize("note", [
    {}, {"midi": True}, {"midi": -1}, {"midi": 128}, {"midi": 60, "time": float("nan")},
    {"midi": 60, "velocity": float("inf")}, {"midi": 60, "velocity": 2},
    {"midi": 60, "time": -1}, {"midi": 60, "duration": 0},
    {"midi": 60, "time": 1, "time_ticks": 96}, {"midi": 60, "length_ticks": True},
    {"midi": 60, "color": 16}, {"midi": 60, "slide": 1}, {"midi": 60, "repeats": 2},
])
def test_codec_rejects_unsupported_or_ambiguous_notes(note):
    with pytest.raises(ValueError):
        encode_notes([note], 96)


def test_selected_color_bit_is_only_ignored_editor_state():
    note = _note(encode_notes([dict(midi=60, color=3)], 96))
    selected = dict(note, midi_channel=131)
    assert canonical_note(note) == canonical_note(selected)
    assert canonical_note(note) != canonical_note(dict(note, velocity=10))
    assert canonical_note(note) != canonical_note(dict(note, midi_channel=19))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = SimpleNamespace(drops=0, prepares=0, mismatch=False, unknown=False)
    state.path = tmp_path / "session.flp"
    state.path.write_bytes(project())
    state.receipt = tmp_path / "write.json"
    state.conn = SimpleNamespace(_hardware_dir=tmp_path)
    state.window = dict(handle="100", pid=123, title="session.flp - FL Studio 2026")
    state.native = dict(success=True, context=dict(session="one", project_generation=0,
        channel_generation=0, pattern=1, channels=[dict(index=0, name="Keys", type=2),
        dict(index=1, name="Bass", type=2)], pattern_name="Pattern 1"),
        values=dict(ppq=96), revision="a" * 64,
        is_playing=False, is_recording=False, dirty=0)
    monkeypatch.setattr(piano, "_window_context", lambda: dict(state.window))
    monkeypatch.setattr(piano.backend, "_project", lambda *a: copy.deepcopy(state.native))
    monkeypatch.setattr(piano.backend, "_call", lambda *a: dict(
        project_native=dict(controller_pid=123), safe_to_edit=True))
    monkeypatch.setattr(piano, "_call", lambda *a, **k: False)
    state.sound = dict(generator=dict(name="FL Keys"), mixer_track=1, effects=[])
    monkeypatch.setattr(piano, "_sound_context", lambda *a: copy.deepcopy(state.sound))
    monkeypatch.setattr(piano.backend, "verify_runtime", lambda: tmp_path / "helper.exe")
    monkeypatch.setattr(piano.backend, "_safe_input_snapshot", lambda: dict(cursor=[1, 2]))

    def prepare(*args):
        state.prepares += 1
        return dict(main=state.window, mixer=dict(handle="200"), point=[400, 400])
    monkeypatch.setattr(piano, "_prepare_roll", prepare)

    def dispatch(executable, before, path):
        assert json.loads(state.receipt.read_text())["status"] == "dispatching"
        state.drops += 1
        raw = _events(path.read_bytes())[2][-1][1]
        if state.mismatch:
            raw = encode_notes([dict(midi=90)], 96)
        state.after = project(raw)
        return dict(returncode=3 if state.unknown else 0, result=dict(
            state="unknown" if state.unknown else "dropped", memory_valid=True,
            effect=1, query_hr=0, enter_hr=0, over_hr=1, drop_hr=0))
    monkeypatch.setattr(piano.backend, "_dispatch", dispatch)
    monkeypatch.setattr(piano, "_save_and_read", lambda *a: (state.after, _inspect(state.after)))
    return state


def apply(rig, *, enabled=True, revision=None, notes=None):
    before = piano.inspect_background_piano(str(rig.path), 0, rig.conn)
    return piano.write_background_piano(str(rig.path), 0, notes or [dict(midi=60)],
        revision or before["revision"], "Keys", str(rig.receipt), enabled, rig.conn)


def test_inspection_and_preview_do_not_expose_or_dispatch(rig):
    result = apply(rig, enabled=False)
    assert result["success"] and result["status"] == "preflighted"
    assert not rig.drops and not rig.prepares and not rig.receipt.exists()


def test_one_dispatch_with_durable_recovery_and_exact_independent_readback(rig):
    result = apply(rig)
    assert result["success"] and result["status"] == "verified"
    assert rig.drops == rig.prepares == 1
    assert rig.receipt.with_suffix(".before.flp").read_bytes() == rig.path.read_bytes()
    assert rig.receipt.with_suffix(".after.flp").read_bytes() == rig.after
    assert result["after_sha256"] == hashlib.sha256(rig.after).hexdigest()


@pytest.mark.parametrize("mode", ["background", "interactive"])
def test_both_modes_use_the_same_backend_without_legacy_ui(rig, monkeypatch, mode):
    from fl_studio_mcp.utils import fl_trigger, windows_workflows
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", mode)
    def forbidden(*args, **kwargs):
        raise AssertionError("Backend score import must never launch legacy UI")
    monkeypatch.setattr(fl_trigger, "trigger_fl_studio", forbidden)
    monkeypatch.setattr(windows_workflows, "WindowsFL", forbidden)
    result = apply(rig)
    assert result["success"] and result["backend"] == "native_ole_score"
    assert result["desktop_input"] is False and rig.drops == 1


@pytest.mark.parametrize("attribute", ["mismatch", "unknown"])
def test_unverified_dispatch_is_retained_without_retry(rig, attribute):
    setattr(rig, attribute, True)
    result = apply(rig)
    assert not result["success"] and result["status"] == "unverified" and rig.drops == 1
    assert json.loads(rig.receipt.read_text())["cli"]


def test_stale_or_invalid_requests_and_existing_receipts_cannot_dispatch(rig):
    with pytest.raises(ValueError, match="target changed"):
        apply(rig, revision="b" * 64)
    with pytest.raises(ValueError):
        apply(rig, notes=[dict(midi=200)])
    rig.receipt.write_text("existing")
    with pytest.raises(FileExistsError):
        apply(rig)
    assert rig.drops == rig.prepares == 0


@pytest.mark.parametrize("field,value", [
    ("is_playing", True), ("is_recording", True), ("dirty", 1),
])
def test_unsaved_or_running_project_cannot_dispatch(rig, field, value):
    rig.native[field] = value
    with pytest.raises(RuntimeError):
        apply(rig)
    assert rig.drops == rig.prepares == 0


def test_other_channel_note_changes_fail_verification():
    raw = encode_notes([dict(midi=60)], 96)
    before = _inspect(project())
    after = _inspect(project(raw, encode_notes([dict(midi=41)], 96)))
    with pytest.raises(RuntimeError, match="Another channel"):
        piano._verify_notes(before, after, 1, 0, raw)


def test_sound_swap_or_routing_change_invalidates_old_revision(rig):
    before = piano.inspect_background_piano(str(rig.path), 0, rig.conn)
    rig.sound["generator"]["name"] = "Sytrus"
    with pytest.raises(ValueError, match="target changed"):
        apply(rig, revision=before["revision"])
    assert rig.drops == 0


def test_select_rejects_absent_pattern_before_sending_commands(rig):
    before = piano.inspect_background_piano(str(rig.path), 0, rig.conn)
    with pytest.raises(ValueError, match="not present"):
        piano.select_background_piano(str(rig.path), 0, 99, before["revision"], rig.conn)
    assert rig.prepares == 0


def test_select_current_pattern_opens_explicit_channel_without_saving(rig, monkeypatch):
    monkeypatch.setattr(piano, "_call", lambda c, fn, *a, **k: int(fn == "mixer.trackNumber"))
    before = piano.inspect_background_piano(str(rig.path), 0, rig.conn)
    result = piano.select_background_piano(str(rig.path), 0, 1, before["revision"], rig.conn)
    assert result["selected_channel"] == 0 and result["pattern"] == 1
    assert result["selection_receipt"] is None and rig.prepares == 1


def test_select_new_pattern_saves_fl_selection_and_verifies_all_notes(rig, monkeypatch):
    data = rig.path.read_bytes()
    extra = event(65, b"\x02\0") + event(193, "Hook\0".encode("utf-16le"))
    data = data[:18] + struct.pack("<I", len(data) - 22 + len(extra)) + data[22:] + extra
    rig.path.write_bytes(data)
    def call(c, fn, *args, **kwargs):
        if fn == "patterns.jumpToPattern":
            rig.native["context"].update(pattern=args[0], pattern_name="Hook")
            rig.native["dirty"] = 1
        return int(fn == "mixer.trackNumber")
    monkeypatch.setattr(piano, "_call", call)
    def save(c, state, previous):
        assert state["context"]["pattern"] == 2 and previous == data
        rig.native["dirty"] = 0
        return data, _inspect(data)
    monkeypatch.setattr(piano, "_save_and_read", save)
    before = piano.inspect_background_piano(str(rig.path), 0, rig.conn)
    result = piano.select_background_piano(str(rig.path), 0, 2, before["revision"], rig.conn)
    assert result["pattern"] == 2 and result["pattern_name"] == "Hook"
    receipt = json.loads(Path(result["selection_receipt"]).read_text())
    assert receipt["status"] == "verified"


def test_sound_context_identifies_plugin_routing_slots_and_stable_saved_state(monkeypatch):
    answers = iter([[True, 4], ["Sytrus", "Lead", 597]])
    monkeypatch.setattr(piano.backend, "_native_values", lambda *a: next(answers))
    slots = [dict(slot=i, occupied=i == 0, **({"name": "Delay", "user_name": "Space",
             "parameter_count": 26, "wet": .2} if i == 0 else {})) for i in range(10)]
    monkeypatch.setattr(piano.backend, "_call", lambda *a: dict(tracks=[
        dict(index=4, name="Lead bus", slots=slots)]))
    data = project()
    result = piano._sound_context(None, 0, data, _inspect(data), 2)
    assert result["generator"]["name"] == "Sytrus"
    assert result["generator"]["preset_name"] is None
    assert not result["generator"]["preset_name_verified"]
    assert result["mixer_track"] == 4 and result["effects"][0]["wet"] == .2


def test_unavailable_plugin_cannot_be_misidentified_as_sampler(monkeypatch):
    monkeypatch.setattr(piano.backend, "_native_values", lambda *a: [False, 4])
    data = project()
    with pytest.raises(RuntimeError, match="cannot identify"):
        piano._sound_context(None, 0, data, _inspect(data), 2)


def test_native_prepare_addresses_exact_channel_roll_and_exposes_covered_window(monkeypatch):
    calls, exposes = [], []
    def call(c, fn, *args, **kwargs):
        calls.append((fn, args, kwargs))
        return {"channels.getRecEventId": 8192, "channels.selectedChannel": 3,
                "patterns.patternNumber": 7}.get(fn)
    monkeypatch.setattr(piano, "_call", call)
    monkeypatch.setattr(piano.native_windows, "windows", lambda *a, **k: [
        {"class": "TEventEditForm", "title": "Piano roll - Lead", "enabled": True,
         "handle": "99", "bounds": [10, 20, 800, 400]}])
    exposed = iter([False, True])
    monkeypatch.setattr(piano.native_windows, "point_belongs_to", lambda *a: next(exposed))
    monkeypatch.setattr(piano.native_windows, "expose_fl_window", lambda w: exposes.append(w))
    monkeypatch.setattr(piano.backend, "_process_identity", lambda pid: {"pid": pid})
    result = piano._prepare_roll(None, dict(channel=3, pattern=7,
        window=dict(handle="1", pid=123)))
    assert ("ui.openEventEditor", (8192, 1, False), {"write": True}) in calls
    assert result["mixer"]["handle"] == "99" and len(exposes) == 1


def test_save_cannot_confirm_wrong_path_with_unchanged_bytes(rig, monkeypatch):
    before = piano.inspect_background_piano(str(rig.path), 0, rig.conn)
    monkeypatch.setattr(piano.backend, "_call", lambda *a: {"status": "saved_current_host"})
    monkeypatch.setattr(piano.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="did not update"):
        SAVE_AND_READ(rig.conn, before, rig.path.read_bytes())
