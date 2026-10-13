"""Execute the production controller with strict FL calling conventions."""

import ast
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fl_studio_mcp.tools.production import ChannelSettings, ProjectSettings
from fl_studio_mcp.utils.production import edit_production, inspect_production, matches

ROOT = Path(__file__).resolve().parents[1]


def test_unavailable_shuffle_is_preserved_during_tempo_verification():
    assert matches({'tempo_bpm': 160.0, 'shuffle': None, 'ppq': 96},
                   {'tempo_bpm': 160.0, 'shuffle': None, 'ppq': 96})
    assert not matches({'tempo_bpm': 159.0, 'shuffle': None},
                       {'tempo_bpm': 160.0, 'shuffle': None})
    assert not matches({}, {'shuffle': None})
    assert not matches({'shuffle': 0.0}, {'shuffle': None})
    assert not matches({'shuffle': None}, {'shuffle': 0.0})


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = SimpleNamespace(tempo=140000, shuffle=0.0, volume=0.78, pan=0.0, pitch=0.0,
                            muted=False, route=0, name="Kick", pattern=1, writes=[],
                            grid=[True] * 32, playing=False, safe=True, before_write=None,
                            pattern_name="Beat")

    def read(value, index, useGlobalIndex):
        assert index == 0 and useGlobalIndex is True
        return value

    def volume(index, useDb=0, useGlobalIndex=False):
        read(None, index, useGlobalIndex)
        return -6.0 if useDb else state.volume

    def pitch(index, mode=0, useGlobalIndex=False):
        read(None, index, useGlobalIndex)
        return 2 if mode == 2 else round(state.pitch * 100) if mode == 1 else state.pitch / 2

    def write(key, value):
        state.writes.append((key, copy.deepcopy(value)))
        if state.before_write:
            state.before_write(key)
        setattr(state, key, value)

    def set_volume(index, value, pickupMode=0, useGlobalIndex=False):
        read(None, index, useGlobalIndex)
        assert pickupMode == 0
        write("volume", value)

    def set_pan(index, value, pickupMode=0, useGlobalIndex=False):
        read(None, index, useGlobalIndex)
        assert pickupMode == 0
        write("pan", value)

    def set_pitch(index, value, pitchUnit=0, pickupMode=0, useGlobalIndex=False):
        read(None, index, useGlobalIndex)
        assert (pitchUnit, pickupMode) == (0, 0)
        write("pitch", value * 2)

    def grid(index, position, useGlobalIndex=False):
        return read(state.grid[position], index, useGlobalIndex)

    def set_grid(index, position, value, useGlobalIndex=False):
        read(None, index, useGlobalIndex)
        state.writes.append(("step", position))
        state.grid[position] = bool(value)

    channel_module = SimpleNamespace(
        channelCount=lambda useGlobalIndex: 1, getShuffle=lambda: state.shuffle,
        setShuffle=lambda value: write("shuffle", value),
        getChannelName=lambda i, g: read(state.name, i, g),
        getChannelType=lambda i, g: read(0, i, g),
        getChannelVolume=volume, setChannelVolume=set_volume,
        getChannelPan=lambda i, g: read(state.pan, i, g), setChannelPan=set_pan,
        getChannelPitch=pitch, setChannelPitch=set_pitch,
        isChannelMuted=lambda i, g: read(state.muted, i, g),
        muteChannel=lambda i, v, g: write("muted", bool(read(v, i, g))),
        getTargetFxTrack=lambda i, g: read(state.route, i, g),
        setTargetFxTrack=lambda i, v, g: write("route", read(v, i, g)),
        getGridBit=grid, setGridBit=set_grid, getChannelColor=lambda i, g: read(0, i, g),
        isChannelSolo=lambda i, g: read(0, i, g),
        isChannelSelected=lambda i, g: read(1, i, g),
        selectedChannel=lambda **kw: 0,
    )
    modules = dict(channels=channel_module,
                   general=SimpleNamespace(getVersion=lambda: 45, getRecPPQ=lambda: 96,
                                           getChangedFlag=lambda: 0, safeToEdit=lambda: state.safe),
                   mixer=SimpleNamespace(getCurrentTempo=lambda mode: state.tempo,
                                         setCurrentTempo=lambda v, mode: write("tempo", v),
                                         getTrackInfo=lambda mode: 16),
                   patterns=SimpleNamespace(patternNumber=lambda: state.pattern,
                                            getPatternName=lambda i: state.pattern_name,
                                            patternMax=lambda: 3),
                   transport=SimpleNamespace(isPlaying=lambda: state.playing,
                                             isRecording=lambda: False),
                   ui=SimpleNamespace(getProgTitle=lambda: "Test.flp"))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    namespace = dict(SCRIPT_DIR=ROOT / "fl_controller", extended_dispatch=lambda a, p: {})
    script = ROOT / "fl_controller/mcp_production.py"
    exec(compile(script.read_text(), str(script), "exec"), namespace)
    state.ns, state.channels = namespace, channel_module
    state.calls = []
    state.timeout = False
    state.fail_read = False

    def send(action, params, timeout):
        state.calls.append((action, copy.deepcopy(params)))
        if action == "production.inspect" and state.fail_read:
            raise TimeoutError("readback unavailable")
        try:
            result = namespace["extended_dispatch"](action, params)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        if action == "production.apply" and not params["dry_run"] and state.timeout:
            raise TimeoutError("response lost AFTER mutation")
        return {"success": True, **result}

    state.conn = SimpleNamespace(_hardware_dir=tmp_path, send_command=send)
    state.path = str(tmp_path / "receipt.json")
    return state


def inspect(rig, scope="channel", **kw):
    return inspect_production(dict(scope=scope, **kw), rig.conn)


def edit(rig, before, changes, apply=True):
    return edit_production(before["target"], changes, before["revision"], apply,
                           rig.path, rig.conn)


def test_fractional_tempo_and_shuffle_verified_in_new_callbacks(rig):
    before = inspect(rig, "project")
    assert before["values"]["tempo_bpm"] == 140
    result = edit(rig, before, {"tempo_bpm": 123.456, "shuffle": 0.37})
    assert result["status"] == "verified" and result["success"]
    assert rig.tempo == 123456
    assert len(result["readbacks"]) == 2
    assert json.loads(Path(rig.path).read_text())["status"] == "verified"
    assert len([c for c in rig.calls if c[0] == "production.apply" and not c[1]["dry_run"]]) == 1


def test_all_channel_fields_use_explicit_global_index_and_units(rig):
    before = inspect(rig, channel=0)
    result = edit(rig, before, dict(volume_normalized=0.5, pan=-0.25,
                                    pitch_semitones=1.25, muted=True, mixer_track=3))
    assert result["success"] and result["status"] == "verified"
    assert result["before"]["values"]["volume_db"] == -6.0
    assert (rig.volume, rig.pan, rig.pitch, rig.muted, rig.route) == (0.5, -0.25, 1.25, True, 3)


def test_prefix_is_cleared_only_within_explicit_extent(rig):
    before = inspect(rig, "steps", channel=0, pattern=1, total_steps=16)
    result = edit(rig, before, {"sequence": [True, False, True]})
    assert result["success"]
    assert rig.grid == [True, False, True] + [False] * 13 + [True] * 16
    assert result["restore_changes"]["sequence"] == [True] * 16


@pytest.mark.parametrize("change", ["name", "pattern", "pattern_name", "project",
                                    "replacement", "value"])
def test_stale_revision_prevents_all_writes(rig, change):
    before = inspect(rig, channel=0)
    if change == "name":
        rig.name = "Different"
    elif change == "pattern":
        rig.pattern = 2
    elif change == "pattern_name":
        rig.pattern_name = "Replacement"
    elif change == "project":
        rig.ns["production_on_project_load"](0)
    elif change == "replacement":
        rig.ns["production_on_dirty_channel"](0, 2)
    else:
        rig.pan = 0.3
    result = edit(rig, before, {"pan": 0.2})
    assert result["status"] == "not_applied" and not rig.writes
    assert not Path(rig.path).exists()


@pytest.mark.parametrize("changes", [{"pan": True}, {"pan": float("nan")},
                                    {"muted": 1}, {"mixer_track": 17},
                                    {"pan": 0.2, "bad": 1}, {"pitch_semitones": 3}, {}])
def test_complete_preflight_rejects_bad_changes_before_write(rig, changes):
    result = edit(rig, inspect(rig, channel=0), changes)
    assert result["status"] == "not_applied" and not rig.writes


def test_zero_pitch_range_rejected_before_other_writes(rig):
    rig.channels.getChannelPitch = lambda *args: 0
    result = edit(rig, inspect(rig, channel=0), {"pan": 0.2, "pitch_semitones": 0})
    assert result["status"] == "not_applied" and not rig.writes


def test_shuffle_requires_both_native_getter_and_setter(rig):
    del rig.channels.getShuffle
    result = edit(rig, inspect(rig, "project"), {"tempo_bpm": 125, "shuffle": 0.5})
    assert result["status"] == "not_applied" and not rig.writes


def test_dry_run_requires_valid_revision_but_never_creates_receipt(rig):
    result = edit(rig, inspect(rig, channel=0), {"pan": 0.2}, False)
    assert result["success"] and result["status"] == "preflighted"
    assert not rig.writes and not Path(rig.path).exists()


def test_reused_receipt_blocks_dispatch(rig):
    Path(rig.path).write_text("original")
    with pytest.raises(FileExistsError):
        edit(rig, inspect(rig, channel=0), {"pan": 0.2})
    assert not rig.writes and Path(rig.path).read_text() == "original"


def test_timeout_after_mutation_never_retries_or_restores(rig):
    rig.timeout = True
    result = edit(rig, inspect(rig, channel=0), {"pan": 0.2})
    assert result["status"] == "unknown" and not result["success"]
    assert rig.writes == [("pan", 0.2)] and rig.pan == 0.2
    assert json.loads(Path(rig.path).read_text())["status"] == "unknown"


def test_partial_failure_retains_in_flight_control_and_never_retries(rig):
    def fail(key):
        if key == "pan":
            raise RuntimeError("native setter failed")
    rig.before_write = fail
    result = edit(rig, inspect(rig, channel=0), {"volume_normalized": 0.5, "pan": 0.2})
    assert not result["success"] and result["status"] == "unknown"
    assert result["response"]["applied"] == ["volume_normalized"]
    assert result["response"]["in_flight"] == "pan"
    assert rig.volume == 0.5 and rig.pan == 0
    assert len(rig.writes) == 2


def test_identity_change_after_dispatch_cannot_verify_success(rig):
    rig.before_write = lambda key: rig.ns["production_on_dirty_channel"](0, 2)
    result = edit(rig, inspect(rig, channel=0), {"pan": 0.2})
    assert result["status"] == "unknown" and not result["success"]


def test_unrequested_control_change_prevents_success(rig):
    rig.before_write = lambda key: setattr(rig, "volume", 0.1)
    result = edit(rig, inspect(rig, channel=0), {"pan": 0.2})
    assert not result["success"] and result["status"] == "verification_failed"
    assert len(rig.writes) == 1


def test_reload_callback_does_not_recurse(rig):
    rig.ns["production_on_project_load"](0)
    rig.ns["production_on_project_load"](100)
    assert rig.ns["_PROJECT_GENERATION"] == 1


@pytest.mark.parametrize("target", [dict(scope="project", channel=0),
                                    dict(scope="channel", channel=0, pattern=1),
                                    dict(scope="steps", channel=0, pattern=2, total_steps=16)])
def test_invalid_or_unselected_target_rejected(rig, target):
    with pytest.raises(RuntimeError):
        inspect_production(target, rig.conn)
    assert not rig.writes


def test_legacy_volume_pan_and_plugin_color_use_correct_signatures(rig):
    names = {"handle_channels_get_info", "handle_channels_get_selected",
             "handle_channels_set_volume", "handle_channels_set_pan", "handle_plugins_get_color"}
    module = ast.parse((ROOT / "fl_controller/device_FLStudioMCP.py").read_text())
    code = ast.Module(body=[n for n in module.body if isinstance(n, ast.FunctionDef)
                           and n.name in names], type_ignores=[])
    color_calls = []
    plugins = SimpleNamespace(getColor=lambda *args: color_calls.append(args) or 0x123456)
    ns = dict(channels=rig.channels, plugins=plugins)
    exec(compile(code, "legacy-controller", "exec"), ns)
    assert ns["handle_channels_get_info"]({"index": 0})["volume"] == rig.volume
    assert ns["handle_channels_get_selected"]()["channel"]["volume"] == rig.volume
    ns["handle_channels_set_volume"]({"index": 0, "volume": 0.5})
    ns["handle_channels_set_pan"]({"index": 0, "pan": 0.25})
    ns["handle_plugins_get_color"]({"index": 0})
    assert color_calls == [(0, -1, 0, 0, True)]


@pytest.mark.parametrize("model,values", [(ProjectSettings, {"shuffle": True}),
                                         (ProjectSettings, {"tempo_bpm": float("inf")}),
                                         (ChannelSettings, {"pan": "0.5"}),
                                         (ChannelSettings, {"muted": 1})])
def test_tool_schemas_reject_coercion(model, values):
    with pytest.raises(ValidationError):
        model(**values)


def test_catalog_corrected_signatures_and_availability_metadata():
    catalog = json.loads((ROOT / "fl_controller/native_catalog.json").read_text())
    assert catalog["channels.soloChannel"]["arguments"] == ["index", "value", "useGlobalIndex"]
    assert catalog["channels.setShuffle"]["minimum_api_version"] == 42
    assert catalog["patterns.duplicatePatternData"]["minimum_api_version"] == 45
    assert catalog["patterns.patternNumber"]["kind"] == "read"
    assert len(catalog["plugins.getColor"]["arguments"]) == 5
