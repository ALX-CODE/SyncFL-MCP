"""No live DAW calls: validate one-dispatch, evidence and native runtime guards."""

import copy
import hashlib
import json
import struct
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import background_loading as loader

TITLE = "Background lab.flp - FL Studio 2026"
DISPATCH = loader._dispatch


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = SimpleNamespace(calls=[], drops=[], busy=0, change=None, exposed=True)
    state.window = dict(
        main=dict(
            handle="100", pid=123, title=TITLE, bounds=[0, 0, 1920, 1080], dpi=96, enabled=True
        ),
        mixer=dict(
            handle="200",
            pid=123,
            title="Mixer - Insert 1",
            bounds=[700, 200, 1000, 600],
            dpi=96,
            enabled=True,
        ),
        point=[1500, 300],
        process=dict(image=r"C:\FL\FL64.exe", created=123456),
    )
    state.project = dict(
        success=True,
        context=dict(
            session="session-a",
            project_generation=0,
            channel_generation=0,
            project_title="lab",
            channels=[],
        ),
        revision="a" * 64,
        is_playing=False,
        is_recording=False,
    )
    state.tracks = [
        dict(index=i, name=name, slots=[dict(slot=s, occupied=False) for s in range(10)])
        for i, name in enumerate(("Master", "Insert 1"))
    ]
    state.selected, state.popup, state.safe = 1, False, True

    def send(action, params, timeout):
        state.calls.append((action, copy.deepcopy(params), timeout))
        if state.busy:
            state.busy -= 1
            raise TimeoutError("FL busy while loading")
        if action == "bridge.ping":
            return dict(
                success=True, project_native=dict(controller_pid=123), safe_to_edit=state.safe
            )
        if action == "production.inspect":
            return copy.deepcopy(state.project)
        if action == "mixer.inspect":
            assert 1 <= len(params["tracks"]) <= 4
            return dict(
                success=True, tracks=[copy.deepcopy(state.tracks[i]) for i in params["tracks"]]
            )
        if action == "native.call":
            assert params == dict(function="ui.showWindow", args=[0], allow_write=True)
            if getattr(state, "expose_on_show", False):
                state.exposed = True
            return dict(success=True, value=None)
        assert action == "bridge.batch"
        assert all(
            step["action"] != "native.call" or "allow_write" not in step["params"]
            for step in params["steps"]
        )
        values = {
            "mixer.getTrackInfo": len(state.tracks) - 1,
            "mixer.trackNumber": state.selected,
            "ui.isInPopupMenu": state.popup,
            "transport.isPlaying": state.project["is_playing"],
            "transport.isRecording": state.project["is_recording"],
            "general.safeToEdit": state.safe,
        }
        return dict(
            success=True,
            results=[
                dict(tracks=[copy.deepcopy(state.tracks[i]) for i in step["params"]["tracks"]])
                if step["action"] == "mixer.inspect"
                else dict(value=values[step["params"]["function"]])
                for step in params["steps"]
            ],
        )

    state.conn = SimpleNamespace(_hardware_dir=tmp_path, send_command=send)
    state.path = tmp_path / "effect.fst"
    state.path.write_bytes(b"FLhd" + struct.pack("<IH", 6, 0x30) + b"\0" * 4 + b"FLdt\0")
    state.receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(loader, "_window_state", lambda slot: copy.deepcopy(state.window))
    monkeypatch.setattr(loader, "_drop_point_exposed", lambda window: state.exposed)
    monkeypatch.setattr(loader, "verify_runtime", lambda: tmp_path / "helper.exe")
    monkeypatch.setattr(loader, "_input_snapshot", lambda: dict(available=True, cursor=[2, 3]))
    monkeypatch.setattr(loader.native_windows, "expose_fl_window", lambda _: None)
    monkeypatch.setattr(loader.time, "sleep", lambda _: None)

    def dispatch(executable, before, path):
        durable = json.loads(state.receipt.read_text())
        assert durable["status"] == "dispatching" and durable["asset_sha256"]
        state.drops.append((executable, before, path))
        state.tracks[1]["slots"][3] = dict(
            slot=3, occupied=True, name="Pro-C 3", user_name="Pro-C 3"
        )
        if state.change:
            state.change()
        return dict(
            returncode=0,
            result=dict(
                experimental=True,
                state="dropped",
                error=0,
                memory_valid=True,
                query_hr=0,
                enter_hr=0,
                over_hr=0,
                drop_hr=0,
                effect=1,
            ),
        )

    monkeypatch.setattr(loader, "_dispatch", dispatch)
    return state


def inspect(rig):
    return loader.inspect_background_effect(1, 3, rig.conn)


def apply(rig, before=None, enabled=True, **kwargs):
    before = before or inspect(rig)
    return loader.load_background_effect(
        1,
        3,
        str(rig.path),
        "Pro-C 3",
        before["revision"],
        TITLE,
        enabled,
        str(rig.receipt),
        rig.conn,
        **kwargs,
    )


def test_inspection_and_preview_do_not_dispatch_or_select(rig):
    before = inspect(rig)
    assert before["ready"] and len(before["mixer_tracks"]) == 2
    assert len(before["mixer_tracks"][1]["slots"]) == 10
    result = apply(rig, before, False)
    assert result["status"] == "preflighted" and result["success"]
    assert not rig.drops and not rig.receipt.exists()


def test_covered_mixer_is_exposed_natively_before_one_dispatch(rig):
    rig.exposed = False
    rig.expose_on_show = True
    before = inspect(rig)
    assert not before["ready"]
    result = apply(rig, before)
    assert result["success"] and len(rig.drops) == 1
    assert sum(action == "native.call" for action, _, _ in rig.calls) == 1


def test_observed_integer_safe_to_edit_and_bounded_all_track_scan(rig):
    rig.safe = 1
    for i in range(2, 11):
        rig.tracks.append(
            dict(
                index=i, name=f"Insert {i}", slots=[dict(slot=s, occupied=False) for s in range(10)]
            )
        )
    result = inspect(rig)
    assert result["ready"] and len(result["mixer_tracks"]) == 11
    chunks = [params["tracks"] for action, params, _ in rig.calls if action == "mixer.inspect"]
    assert chunks == [list(range(4)), list(range(4, 8)), list(range(8, 11))]


@pytest.mark.parametrize("unsafe_shape", ["1", 1.0, 2, None])
def test_unknown_safe_to_edit_representation_is_rejected(rig, unsafe_shape):
    rig.safe = unsafe_shape
    with pytest.raises(ValueError, match="edit safety"):
        inspect(rig)


def test_applies_once_then_two_native_readbacks_and_durable_outcome(rig):
    result = apply(rig)
    assert result["status"] == "verified" and result["success"]
    assert len(rig.drops) == 1 and len(result["readbacks"]) == 2
    assert result["track_selection_changed"] is False and result["desktop_input"] is False
    assert json.loads(rig.receipt.read_text())["cli"]["result"]["over_hr"] == 0


def test_busy_native_callbacks_retry_reads_only(rig):
    rig.change = lambda: setattr(rig, "busy", 2)
    result = apply(rig)
    assert result["success"] and len(rig.drops) == 1
    assert sum("unavailable" in item for item in result["readbacks"]) == 2


def test_diagnostic_failure_cannot_discard_durable_cli_outcome(rig, monkeypatch):
    count = 0

    def unavailable():
        nonlocal count
        count += 1
        if count > 1:
            assert "cli" in json.loads(rig.receipt.read_text())
            raise OSError("diagnostic unavailable")
        return dict(available=True)

    monkeypatch.setattr(loader, "_input_snapshot", unavailable)
    result = apply(rig)
    saved = json.loads(rig.receipt.read_text())
    assert result["success"] and saved["status"] == "verified"
    assert saved["cli"]["result"]["state"] == "dropped"
    assert saved["input_after"]["error"] == "diagnostic unavailable"
    assert len(rig.drops) == 1


def test_target_change_during_later_scan_chunks_is_rechecked_before_drop(rig):
    for i in range(2, 11):
        rig.tracks.append(
            dict(
                index=i, name=f"Insert {i}", slots=[dict(slot=s, occupied=False) for s in range(10)]
            )
        )
    before = inspect(rig)
    original = rig.conn.send_command
    chunks = 0

    def user_edit(action, params, timeout):
        nonlocal chunks
        result = original(action, params, timeout)
        if action == "mixer.inspect":
            chunks += 1
            if chunks == 6:  # Last chunk of the fresh full scan, after target was read.
                rig.tracks[1]["slots"][3] = dict(
                    slot=3, occupied=True, name="User effect", user_name="User effect"
                )
        return result

    rig.conn.send_command = user_edit
    result = apply(rig, before)
    assert result["status"] == "unverified" and not rig.drops
    assert "Target slots changed immediately" in result["error"]
    assert rig.tracks[1]["slots"][3]["name"] == "User effect"


@pytest.mark.parametrize(
    "change", ["selected", "playing", "recording", "popup", "window", "covered"]
)
def test_changes_after_journal_flush_are_checked_before_dispatch(rig, monkeypatch, change):
    original = loader._persist

    def changing(path, report):
        original(path, report)
        if report["status"] != "dispatching":
            return
        if change == "selected":
            rig.selected = 0
        elif change in {"playing", "recording"}:
            rig.project["is_" + change] = True
        elif change == "popup":
            rig.popup = True
        elif change == "covered":
            rig.exposed = False
        else:
            rig.window["mixer"]["bounds"][0] += 20

    monkeypatch.setattr(loader, "_persist", changing)
    result = apply(rig)
    assert result["status"] == "unverified" and not rig.drops


@pytest.mark.parametrize(
    "kind", ["other_track", "other_slot", "project", "selection", "window", "wrong_plugin"]
)
def test_unrelated_changes_or_wrong_target_cannot_verify(rig, kind):
    def change():
        if kind in {"other_track", "other_slot"}:
            rig.tracks[0 if kind == "other_track" else 1]["slots"][0] = dict(
                slot=0, occupied=True, name="Wrong", user_name="Wrong"
            )
        elif kind == "project":
            rig.project["context"]["project_generation"] += 1
        elif kind == "selection":
            rig.selected = 0
        elif kind == "window":
            rig.window["main"]["title"] = "Other.flp - FL Studio 2026"
        else:
            rig.tracks[1]["slots"][3]["name"] = "Wrong plugin"

    rig.change = change
    result = apply(rig)
    assert not result["success"] and result["status"] == "unverified"
    assert len(rig.drops) == 1


def test_editor_covering_slot_after_drop_does_not_block_verification(rig):
    rig.change = lambda: setattr(rig, "exposed", False)
    result = apply(rig)
    assert result["status"] == "verified" and result["success"] and len(rig.drops) == 1


def test_unknown_cli_never_promoted_by_matching_native_state(rig, monkeypatch):
    original = loader._dispatch

    def lost(*args):
        original(*args)
        return dict(state="unknown", error="lost helper response")

    monkeypatch.setattr(loader, "_dispatch", lost)
    result = apply(rig)
    assert result["status"] == "unverified" and not result["success"] and len(rig.drops) == 1


@pytest.mark.parametrize(
    "kind",
    ["selected", "title", "playing", "recording", "popup", "unsafe", "occupied", "covered"],
)
def test_preflight_rejects_unsafe_target_without_mutation(rig, kind):
    if kind == "selected":
        rig.selected = 0
    elif kind == "title":
        rig.window["mixer"]["title"] = "Mixer - Master"
    elif kind in {"playing", "recording"}:
        rig.project["is_" + kind] = True
    elif kind == "popup":
        rig.popup = True
    elif kind == "unsafe":
        rig.safe = False
    elif kind == "covered":
        rig.exposed = False
    else:
        rig.tracks[1]["slots"][3] = dict(
            slot=3, occupied=True, name="Existing", user_name="Existing"
        )
    before = inspect(rig)
    assert not before["ready"]
    with pytest.raises(RuntimeError):
        apply(rig, before)
    assert not rig.drops and not rig.receipt.exists()


def test_stale_revision_and_existing_receipt_prevent_dispatch(rig):
    before = inspect(rig)
    rig.tracks[0]["name"] = "Renamed master"
    with pytest.raises(ValueError, match="revision changed"):
        apply(rig, before)
    rig.receipt.write_text("keep")
    with pytest.raises(FileExistsError):
        apply(rig)
    assert rig.receipt.read_text() == "keep" and not rig.drops


def test_change_after_receipt_reservation_stops_before_helper(rig, monkeypatch):
    before = inspect(rig)
    original = loader._inspect
    count = 0

    def changing(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            rig.selected = 0
        return original(*args, **kwargs)

    monkeypatch.setattr(loader, "_inspect", changing)
    result = apply(rig, before)
    assert result["status"] == "unverified" and not rig.drops


def test_persist_failure_before_dispatch_never_calls_helper(rig, monkeypatch):
    monkeypatch.setattr(loader, "_persist", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    result = apply(rig)
    assert result["status"] == "unverified" and not rig.drops
    assert result["receipt_persistence_error"] == "disk full"


@pytest.mark.parametrize("track,slot", [(True, 0), (1, True), (-1, 0), (256, 0), (1, 10)])
def test_strict_target_bounds(rig, track, slot):
    with pytest.raises(ValueError):
        loader.inspect_background_effect(track, slot, rig.conn)
    assert not rig.calls


def test_invalid_fst_does_not_contact_fl(rig):
    rig.path.write_bytes(b"not a plugin preset")
    with pytest.raises(ValueError, match="plugin .fst"):
        loader.load_background_effect(
            1, 3, str(rig.path), "Pro-C 3", "a" * 64, TITLE, connection=rig.conn
        )
    assert not rig.calls


def test_runtime_requires_pinned_manifest_and_exact_binary_hashes(tmp_path, monkeypatch):
    entries = {}
    for name in loader.RUNTIME_FILES:
        data = (name + " content").encode()
        (tmp_path / name).write_bytes(data)
        entries[name] = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest = json.dumps(
        dict(schema_version=1, architecture="x64", experimental=True, files=entries)
    ).encode()
    (tmp_path / "manifest.json").write_bytes(manifest)
    monkeypatch.setattr(loader, "MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest())
    assert loader.verify_runtime(tmp_path) == tmp_path / loader.RUNTIME_FILES[0]
    (tmp_path / loader.RUNTIME_FILES[1]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash/size"):
        loader.verify_runtime(tmp_path)
    (tmp_path / "manifest.json").write_bytes(manifest + b" ")
    with pytest.raises(ValueError, match="manifest differs"):
        loader.verify_runtime(tmp_path)


def test_dispatch_uses_mixer_hwnd_and_no_window_process(rig, monkeypatch):
    calls = []
    payload = dict(
        experimental=True,
        state="dropped",
        error=0,
        memory_valid=True,
        query_hr=0,
        enter_hr=0,
        over_hr=0,
        drop_hr=0,
        effect=1,
    )
    monkeypatch.setattr(
        loader.subprocess,
        "run",
        lambda args, **kw: (
            calls.append((args, kw))
            or SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        ),
    )
    before = inspect(rig)
    result = DISPATCH(rig.path.parent / "helper.exe", before, rig.path)
    args, kwargs = calls[0]
    assert args[args.index("--hwnd") + 1] == "200"
    assert args[args.index("--expected-exe") + 1] == rig.window["process"]["image"]
    assert kwargs["timeout"] == 7 and kwargs["capture_output"]
    assert kwargs["creationflags"] == getattr(loader.subprocess, "CREATE_NO_WINDOW", 0)
    assert result["returncode"] == 0
    assert loader._confirmed(dict(returncode=0, result=payload))
    payload["over_hr"] = -1
    assert not loader._confirmed(dict(returncode=0, result=payload))


def test_drop_point_must_resolve_to_the_mixer(monkeypatch):
    window = dict(point=[1487, 240], mixer=dict(handle="200"))
    seen = []

    def owner(point, parent):
        seen.append((point, parent))
        return parent == "200" and point == [1487, 240]

    monkeypatch.setattr(loader.native_windows, "point_belongs_to", owner)
    assert loader._drop_point_exposed(window)
    window["mixer"]["handle"] = "300"
    assert not loader._drop_point_exposed(window)
    assert seen == [([1487, 240], "200"), ([1487, 240], "300")]


def test_occlusion_reason_includes_other_applications(rig):
    rig.exposed = False
    result = inspect(rig)
    assert not result["ready"]
    assert any("other applications" in reason for reason in result["reasons"])
    assert not rig.drops
