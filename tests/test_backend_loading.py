"""Backend loaders preserve targets/receipts without constructing a UI input client."""

import asyncio
import copy
import hashlib
import json
import struct
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import backend_project as project
from fl_studio_mcp.utils import backend_studio_loading as loaders
from fl_studio_mcp.utils import studio_loading as legacy


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = SimpleNamespace(
        channels=[dict(index=0, name="Original", target_fx_track=3)], calls=[], drops=[],
        unknown=False, playing=False, generation=1, mutate_existing=False,
    )
    conn = SimpleNamespace(_hardware_dir=tmp_path)
    main = dict(handle="10", pid=42, title="Song.flp - FL Studio")
    rack = dict(handle="20", pid=42, title="Channel rack", **{
        "class": "TStepSeqForm", "bounds": [0, 0, 700, 400], "dpi": 96, "enabled": True})
    monkeypatch.setattr(loaders, "get_connection", lambda: conn)
    monkeypatch.setattr(loaders, "_window_context", lambda: main)
    monkeypatch.setattr(loaders.bridge, "_project", lambda *a: dict(
        context=dict(session="s", project_generation=state.generation),
        is_playing=state.playing, is_recording=False))
    monkeypatch.setattr(loaders.bridge, "_call", lambda *a, **kw: dict(
        project_native=dict(controller_pid=42)))
    monkeypatch.setattr(loaders.bridge, "verify_runtime", lambda: tmp_path / "bridge.exe")
    monkeypatch.setattr(loaders.bridge, "_process_identity", lambda *a: dict(image="FL64.exe"))
    monkeypatch.setattr(loaders.bridge, "_safe_input_snapshot", lambda: {})
    monkeypatch.setattr(loaders.native_windows, "unique_control", lambda *a: copy.deepcopy(rack))
    monkeypatch.setattr(loaders.native_windows, "point_belongs_to", lambda *a: True)
    monkeypatch.setattr(loaders.native_windows, "windows", lambda *a: [
        dict(**{"class": "TVectorPanel", "bounds": [5, 370, 680, 25]})])
    monkeypatch.setattr(legacy.WindowsFL, "__init__", lambda *a, **kw: pytest.fail("UI client"))

    def native(action, params=None):
        params = params or {}
        state.calls.append((action, params))
        if action == "channels.getAll":
            return dict(channels=copy.deepcopy(state.channels))
        if action == "channels.setName":
            state.channels[params["index"]]["name"] = params["name"]
            return dict(success=True)
        if action == "channels.routeToMixer":
            state.channels[params["channel_index"]]["target_fx_track"] = params["mixer_track"]
            return dict(success=True)
        assert action == "native.call"
        function = params["function"]
        values = {"ui.showWindow": None, "ui.isInPopupMenu": False,
                  "mixer.getTrackInfo": 12, "plugins.getPluginName": "Synth",
                  "channels.getChannelType": 0}
        assert function in values, function
        return dict(value=values[function])

    monkeypatch.setattr(legacy, "native", native)

    def dispatch(executable, target, path):
        state.drops.append((target, path))
        if state.mutate_existing:
            state.channels[0]["name"] = "Unexpected"
        state.channels.append(dict(index=len(state.channels), name=path.stem, target_fx_track=0))
        return dict(returncode=0, result=dict(
            state="unknown" if state.unknown else "dropped", memory_valid=True, effect=1,
            query_hr=0, enter_hr=0, over_hr=0, drop_hr=0))

    monkeypatch.setattr(loaders.bridge, "_dispatch", dispatch)
    state.sample = tmp_path / "Kick.wav"
    state.sample.write_bytes(b"wave")
    state.preset = tmp_path / "Synth.fst"
    state.preset.write_bytes(b"FLhd" + struct.pack("<IHHH", 6, 0x30, 1, 96) + b"FLdt\0\0\0\0")
    return state


@pytest.mark.parametrize("kind", ["sample", "instrument", "preset"])
def test_channel_load_uses_native_transport_and_verifies_existing_channels(rig, kind, tmp_path):
    async def run():
        async with loaders.BackendStudioLoading() as fl:
            if kind == "sample":
                return await fl.add_sample(str(rig.sample), name="Kick", mixer_track=4)
            if kind == "instrument":
                return await fl.add_instrument(path=str(rig.preset), channel_name="Lead")
            return await fl.load_preset(str(rig.preset))

    result = asyncio.run(run())
    assert result["success"] and result["desktop_input"] is False
    assert len(rig.drops) == 1
    assert rig.channels[0] == dict(index=0, name="Original", target_fx_track=3)
    receipt = json.loads(next(tmp_path.glob("backend-load-*.json")).read_text())
    assert receipt["status"] == "verified" and receipt["desktop_input"] is False


@pytest.mark.parametrize("fault", ["unknown", "mutate_existing"])
def test_uncertain_or_wrong_target_load_is_journaled_without_replay(rig, fault, tmp_path):
    setattr(rig, fault, True)

    async def run():
        async with loaders.BackendStudioLoading() as fl:
            await fl.add_sample(str(rig.sample))

    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert len(rig.drops) == 1
    receipt = json.loads(next(tmp_path.glob("backend-load-*.json")).read_text())
    assert receipt["status"] == "unverified" and not receipt["success"]


def test_transport_guard_prevents_native_drop(rig):
    rig.playing = True
    with pytest.raises(RuntimeError, match="Stop transport"):
        asyncio.run(loaders.BackendStudioLoading().__aenter__())
    assert not rig.drops


def test_transport_started_during_journal_write_prevents_dispatch(rig, monkeypatch, tmp_path):
    persist = loaders.bridge._persist

    def start_transport(path, report):
        persist(path, report)
        if report["status"] == "dispatching":
            rig.playing = True

    monkeypatch.setattr(loaders.bridge, "_persist", start_transport)

    async def run():
        async with loaders.BackendStudioLoading() as fl:
            await fl.add_sample(str(rig.sample))

    with pytest.raises(RuntimeError, match="Stop transport"):
        asyncio.run(run())
    assert not rig.drops
    assert json.loads(next(tmp_path.glob("backend-load-*.json")).read_text())["success"] is False


def test_unsupported_sample_format_does_not_change_target(rig, tmp_path):
    sample = tmp_path / "Sample.mp3"
    sample.write_bytes(b"mp3")

    async def run():
        async with loaders.BackendStudioLoading() as fl:
            await fl.add_sample(str(sample))

    with pytest.raises(ValueError, match="extension"):
        asyncio.run(run())
    assert not rig.drops
    assert not any(params.get("function") == "ui.showWindow" for _, params in rig.calls)


@pytest.mark.parametrize("wrong_title", [False, True])
def test_effect_loader_uses_native_selection_and_exact_mixer_target(rig, monkeypatch, wrong_title):
    slots = [dict(index=i, occupied=False, name="", user_name="") for i in range(10)]
    track = dict(index=5, name="Lead bus", slots=slots)
    mixer = dict(handle="30", pid=42, title="Mixer - " + ("Wrong" if wrong_title else "Lead bus"),
                 **{"class": "TFXForm", "bounds": [0, 0, 900, 600], "dpi": 96, "enabled": True})
    base_native = legacy.native
    selected = []

    def native(action, params=None):
        params = params or {}
        if action == "mixer.inspect":
            assert params["tracks"] == [5]
            return dict(tracks=[copy.deepcopy(track)])
        if action == "native.call":
            if params["function"] == "mixer.setTrackNumber":
                selected.append(params["args"][0])
                return dict(value=None)
            if params["function"] == "mixer.trackNumber":
                return dict(value=selected[-1])
        return base_native(action, params)

    def dispatch(*args):
        rig.drops.append(args)
        slots[2].update(occupied=True, name="Synth", user_name="Synth")
        return dict(returncode=0, result=dict(state="dropped", memory_valid=True, effect=1,
                                            query_hr=0, enter_hr=0, over_hr=0, drop_hr=0))

    monkeypatch.setattr(legacy, "native", native)
    monkeypatch.setattr(loaders.bridge, "_dispatch", dispatch)
    monkeypatch.setattr(loaders.native_windows, "unique_control", lambda *a: copy.deepcopy(mixer))
    monkeypatch.setattr(loaders.native_windows, "windows", lambda *a: [
        dict(**{"class": "TVectorPanel", "bounds": [670, 230, 220, 300]})])

    async def run():
        async with loaders.BackendStudioLoading() as fl:
            return await fl.add_effect(5, 2, name="Synth", path=str(rig.preset))

    if wrong_title:
        with pytest.raises(RuntimeError, match="mixer selection"):
            asyncio.run(run())
        assert not rig.drops
    else:
        result = asyncio.run(run())
        assert result["success"] and result["transfer"]["desktop_input"] is False
        assert len(rig.drops) == 1
        assert all(not item["occupied"] for i, item in enumerate(slots) if i != 2)
    assert selected == [5]


@pytest.fixture
def project_rig(tmp_path, monkeypatch):
    source, active = tmp_path / "Next.flp", tmp_path / "Current.flp"
    source.write_bytes(b"new-project")
    active.write_bytes(b"old-project")
    context = dict(session="s", project_generation=1,
                   channels=[dict(index=0, name="Lead")])
    native = dict(revision="r", context=context, dirty=0, is_playing=False,
                  is_recording=False, values=dict(ppq=96, tempo_bpm=120))
    main = dict(handle="10", pid=42, title="Current.flp - FL Studio")
    info = dict(channels=[dict(id=0, name="Lead")], ppq=96, tempo=120)
    state = SimpleNamespace(drops=[], unknown=False, decline=False)
    monkeypatch.setattr(project, "_file", lambda p: (p, p.read_bytes(), info))
    monkeypatch.setattr(project, "_window_context", lambda: dict(main))
    monkeypatch.setattr(project, "_save_and_read", lambda *a: (active.read_bytes(), info))
    monkeypatch.setattr(project.bridge, "_project", lambda *a: copy.deepcopy(native))
    monkeypatch.setattr(project.bridge, "_call", lambda *a: dict(
        project_native=dict(controller_pid=42), safe_to_edit=True))
    monkeypatch.setattr(project.bridge, "verify_runtime", lambda: tmp_path / "bridge.exe")
    monkeypatch.setattr(project.bridge, "_process_identity", lambda *a: {})
    monkeypatch.setattr(project.native_windows, "point_belongs_to", lambda *a: True)
    monkeypatch.setattr(project.native_windows, "windows", lambda *a, **kw: [dict(
        handle="20", title="Playlist - Arrangement", enabled=True, **{
            "class": "TEventEditForm", "bounds": [0, 0, 900, 600]})])

    def dispatch(*args):
        state.drops.append(args)
        if not state.decline:
            native["context"]["project_generation"] = 2
            main["title"] = "Next.flp - FL Studio"
        return dict(state="unknown")

    monkeypatch.setattr(project.bridge, "_dispatch", dispatch)
    state.args = dict(source=source,
                      expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                      expected_revision="r", recovery_path=tmp_path / "Recovery.flp",
                      receipt_file=tmp_path / "open.json", current_project_path=active,
                      apply=True, connection=SimpleNamespace(_hardware_dir=tmp_path))
    return state


def test_native_project_load_preserves_recovery_and_reconciles_unknown_delivery(project_rig):
    result = project.open_backend_project(**project_rig.args)
    assert result["success"] and not result["absolute_path_verified"]
    assert len(project_rig.drops) == 1
    assert project_rig.args["recovery_path"].read_bytes() == b"old-project"
    assert project_rig.args["source"].read_bytes() == b"new-project"
    assert json.loads(project_rig.args["receipt_file"].read_text())["status"] == "verified"


@pytest.mark.parametrize("change", ["hash", "revision", "missing_active", "existing_recovery"])
def test_native_project_guards_prevent_dispatch(project_rig, change):
    args = project_rig.args
    if change == "hash":
        args["expected_sha256"] = "bad"
    elif change == "revision":
        args["expected_revision"] = "stale"
    elif change == "missing_active":
        args["current_project_path"] = None
    else:
        args["recovery_path"].write_bytes(b"preserve")
    with pytest.raises(ValueError):
        project.open_backend_project(**args)
    assert not project_rig.drops


def test_declined_project_load_keeps_recovery_and_never_retries(project_rig, monkeypatch):
    project_rig.decline = True
    ticks = iter([0, 0, 31])
    monkeypatch.setattr(project.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(project.time, "sleep", lambda _: None)
    result = project.open_backend_project(**project_rig.args)
    assert not result["success"] and result["status"] == "unverified"
    assert len(project_rig.drops) == 1
    assert project_rig.args["recovery_path"].read_bytes() == b"old-project"
