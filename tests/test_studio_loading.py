"""Loading invariants at native/UI/transfer boundaries; no desktop or MIDI input."""

import asyncio
import struct
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fl_studio_mcp.utils import studio_loading as loading


def channel(index, name, track=0):
    return {"index": index, "name": name, "target_fx_track": track}


def empty_slot(index):
    return {"index": index, "occupied": False, "name": "", "user_name": ""}


def preset_bytes(format_code=0x30):
    return b"FLhd" + struct.pack("<IHHH", 6, format_code, 1, 96) + b"FLdt\0\0\0\0"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    sample = tmp_path / "Kick.wav"
    sample.write_bytes(b"wave fixture")
    preset = tmp_path / "Synth.fst"
    preset.write_bytes(preset_bytes())
    state = SimpleNamespace(
        sample=sample, preset=preset,
        channels=[channel(0, "Original lead", 3), channel(1, "Original bass", 4)],
        slots=[empty_slot(index) for index in range(10)],
        calls=[], transfers=[], on_transfer=None, on_write=None, on_ui_write=None,
        before_validation=None,
        playing=False, popup=False, visible=True, plugin="Synth", channel_type=0, active_track=0,
        rename_works=True, route_works=True, delivery_confirmed=True, plugin_valid=True,
        plugin_params=[{"index": 0, "name": "Gain", "value": 0.4}], visible_channels=[0, 1],
    )

    def native(action, params=None):
        params = params or {}
        state.calls.append((action, deepcopy(params)))
        if action == "channels.getAll":
            return {"channels": deepcopy(state.channels)}
        if action == "mixer.inspect":
            return {"tracks": [{"index": params["tracks"][0], "slots": deepcopy(state.slots)}]}
        if action == "plugins.getParams":
            if not state.plugin_valid:
                raise RuntimeError("Plugin not valid")
            return {"params": deepcopy(state.plugin_params)}
        if action == "channels.setName":
            if state.rename_works:
                target = next(c for c in state.channels if c["index"] == params["index"])
                target["name"] = params["name"]
            if state.on_write:
                state.on_write(action)
            return {"success": True}
        if action == "channels.routeToMixer":
            if state.route_works:
                target = next(c for c in state.channels if c["index"] == params["channel_index"])
                target["target_fx_track"] = params["mixer_track"]
            if state.on_write:
                state.on_write(action)
            return {"success": True}
        if action == "native.call":
            function = params["function"]
            if params["allow_write"]:
                if function == "mixer.setActiveTrack":
                    state.active_track = params["args"][0]
                if state.on_ui_write:
                    state.on_ui_write(function)
                return {"value": None}
            if function == "channels.getChannelIndex":
                return {"value": state.visible_channels[params["args"][0]]}
            values = {
                "mixer.getTrackInfo": 12,
                "mixer.trackNumber": state.active_track,
                "transport.isPlaying": state.playing,
                "ui.isInPopupMenu": state.popup,
                "channels.getChannelType": state.channel_type,
                "channels.channelCount": len(state.visible_channels),
                "plugins.getPluginName": state.plugin,
                "plugins.isValid": state.plugin_valid,
            }
            assert function in values, f"Unexpected native read: {function}"
            return {"value": values[function]}
        raise AssertionError(f"Unexpected native action: {action}")

    monkeypatch.setattr(loading, "native", native)
    rack = {"handle": "200", "bounds": [100, 100, 700, 400], "dpi": 96}
    mixer = {"handle": "300", "bounds": [800, 100, 900, 600], "dpi": 96}
    monkeypatch.setattr(loading, "unique_control", lambda main, cls, title=None:
                        rack if cls == "TStepSeqForm" else mixer)
    monkeypatch.setattr(loading, "windows", lambda handle: [
        {"class": "TVectorPanel", "bounds": [105, 470, 680, 25]},
        {"class": "TVectorPanel", "bounds": [105, 105, 680, 350]},
    ] if handle == rack["handle"] else [
        {"class": "TVectorPanel", "bounds": [1350, 180, 220, 300]},
    ])
    monkeypatch.setattr(loading, "point_belongs_to", lambda point, handle: state.visible)
    fl = object.__new__(loading.StudioLoading)
    fl.handle = "100"
    fl._project_guard = AsyncMock()
    fl.win = AsyncMock(return_value={"success": True})

    async def transfer(path, control, point, validate_target=None):
        if state.before_validation:
            state.before_validation()
        if validate_target:
            validate_target()
        state.transfers.append((path, control, point))
        if state.on_transfer:
            state.on_transfer()
        return {"dispatched": True, "verified": False,
                "delivery_confirmed": state.delivery_confirmed}

    fl.transfer = AsyncMock(side_effect=transfer)
    state.fl = fl
    return state


def add_sampler(rig):
    rig.channels.append(channel(2, "Kick"))


def fill_effect(rig, slot=2, name="EQ"):
    rig.slots[slot].update(occupied=True, name=name, user_name=name)


@pytest.mark.parametrize(
    "height,slot,expected_y", [(291, 0, 240), (291, 9, 501), (300, 0, 241), (300, 9, 509)],
)
def test_mixer_drop_target_accepts_observed_slot_panel_heights(height, slot, expected_y):
    mixer = {"bounds": [600, 180, 1051, 598], "dpi": 96}
    children = [{"class": "TVectorPanel", "bounds": [1410, 225, 220, height]}]
    assert loading.mixer_drop_target(mixer, children, slot) == (1487, expected_y)


@pytest.mark.parametrize("width,height", [(209, 291), (231, 291), (220, 289), (220, 311)])
def test_mixer_drop_target_rejects_out_of_bounds_slot_panel(width, height):
    mixer = {"dpi": 96}
    children = [{"class": "TVectorPanel", "bounds": [1410, 225, width, height]}]
    with pytest.raises(RuntimeError, match="Cannot identify"):
        loading.mixer_drop_target(mixer, children, 0)


def test_mixer_drop_target_rejects_ambiguous_slot_panels():
    mixer = {"dpi": 96}
    children = [
        {"class": "TVectorPanel", "bounds": [1410, 225, 220, 291]},
        {"class": "TVectorPanel", "bounds": [1000, 225, 220, 300]},
    ]
    with pytest.raises(RuntimeError, match="Cannot identify"):
        loading.mixer_drop_target(mixer, children, 0)


def test_sample_creates_exactly_one_channel_and_verifies_rename_route(rig):
    original = deepcopy(rig.channels)
    rig.on_transfer = lambda: add_sampler(rig)
    result = asyncio.run(rig.fl.add_sample(str(rig.sample), name="New kick", mixer_track=9))
    assert result["success"]
    assert result["channel"] == channel(2, "New kick", 9)
    assert len(rig.transfers) == 1
    assert rig.channels[:-1] == original
    assert len(rig.channels) == len(original) + 1
    assert result["transfer"]["verified"] is False  # Native state, not dispatch, proves success.


@pytest.mark.parametrize(
    "change", ["extra_channel", "removed_channel", "renamed_old", "routed_old"],
)
def test_sample_rejects_unexpected_channel_count_or_existing_identity_change(rig, change):
    def dispatched():
        add_sampler(rig)
        if change == "extra_channel":
            rig.channels.append(channel(3, "Unexpected"))
        elif change == "removed_channel":
            rig.channels.clear()
        elif change == "renamed_old":
            rig.channels[0]["name"] = "Unrelated change"
        else:
            rig.channels[0]["target_fx_track"] = 8

    rig.on_transfer = dispatched
    with pytest.raises(RuntimeError, match="channels changed|channel count"):
        asyncio.run(rig.fl.add_sample(str(rig.sample)))
    assert len(rig.transfers) == 1
    assert not any(action in {"channels.setName", "channels.routeToMixer"}
                   for action, _ in rig.calls)


def test_channel_change_during_rack_activation_blocks_transfer(rig):
    def ui_changed(function):
        if function == "ui.setFocused":
            rig.channels[0]["name"] = "Changed while opening rack"

    rig.on_ui_write = ui_changed
    with pytest.raises(RuntimeError, match="Channels changed before"):
        asyncio.run(rig.fl.add_sample(str(rig.sample)))
    assert not rig.transfers


def test_channel_change_while_transfer_helper_prepares_blocks_dispatch(rig):
    rig.before_validation = lambda: rig.channels.append(channel(2, "Concurrent addition"))
    with pytest.raises(RuntimeError, match="Channels changed before"):
        asyncio.run(rig.fl.add_sample(str(rig.sample)))
    assert not rig.transfers


@pytest.mark.parametrize("wrong_identity", ["name", "type"])
def test_sampler_identity_mismatch_is_not_success(rig, wrong_identity):
    def dispatched():
        add_sampler(rig)
        if wrong_identity == "name":
            rig.channels[-1]["name"] = "Different sample"
        else:
            rig.channel_type = 2

    rig.on_transfer = dispatched
    with pytest.raises(RuntimeError, match="sampler identity"):
        asyncio.run(rig.fl.add_sample(str(rig.sample)))


@pytest.mark.parametrize("failed_write", ["rename", "route"])
def test_native_write_receipt_without_matching_readback_is_rejected(rig, failed_write):
    rig.on_transfer = lambda: add_sampler(rig)
    rig.rename_works = failed_write != "rename"
    rig.route_works = failed_write != "route"
    with pytest.raises(RuntimeError, match="readback failed"):
        asyncio.run(rig.fl.add_sample(str(rig.sample), name="New kick", mixer_track=9))


def test_channels_are_revalidated_after_rename_and_routing(rig):
    rig.on_transfer = lambda: add_sampler(rig)

    def changed_during_write(action):
        if action == "channels.routeToMixer":
            rig.channels[0]["name"] = "Concurrent old-channel change"

    rig.on_write = changed_during_write
    with pytest.raises(RuntimeError, match="changed|identity|count"):
        asyncio.run(rig.fl.add_sample(str(rig.sample), name="New kick", mixer_track=9))


def test_final_readback_cannot_return_a_concurrently_added_channel(rig):
    rig.on_transfer = lambda: add_sampler(rig)

    def changed_during_write(action):
        if action == "channels.routeToMixer":
            rig.channels.append(channel(3, "New kick", 9))

    rig.on_write = changed_during_write
    with pytest.raises(RuntimeError, match="changed|identity|count"):
        asyncio.run(rig.fl.add_sample(str(rig.sample), name="New kick", mixer_track=9))


@pytest.mark.parametrize("path_kind", ["relative", "missing", "extension", "empty", "directory"])
def test_bad_sample_path_never_dispatches(rig, tmp_path, path_kind):
    paths = {
        "relative": "Kick.wav", "missing": str(tmp_path / "Missing.wav"),
        "extension": str(rig.preset), "empty": str(tmp_path / "Empty.wav"),
        "directory": str(tmp_path / "Directory.wav"),
    }
    (tmp_path / "Empty.wav").touch()
    (tmp_path / "Directory.wav").mkdir()
    with pytest.raises((ValueError, FileNotFoundError)):
        asyncio.run(rig.fl.add_sample(paths[path_kind]))
    assert not rig.transfers and not rig.calls
    rig.fl.win.assert_not_awaited()


@pytest.mark.parametrize("track", [-1, 13, True, "2"])
def test_invalid_sample_route_never_dispatches(rig, track):
    with pytest.raises(ValueError, match="track"):
        asyncio.run(rig.fl.add_sample(str(rig.sample), mixer_track=track))
    assert not rig.transfers
    rig.fl.win.assert_not_awaited()


@pytest.mark.parametrize("state_name", ["playing", "popup"])
def test_playback_and_popup_prevent_loading(rig, state_name):
    setattr(rig, state_name, True)
    with pytest.raises(RuntimeError):
        asyncio.run(rig.fl.add_sample(str(rig.sample)))
    assert not rig.transfers


def test_obscured_rack_prevents_loading(rig):
    rig.visible = False
    with pytest.raises(RuntimeError, match="obscured"):
        asyncio.run(rig.fl.add_sample(str(rig.sample)))
    assert not rig.transfers


def test_obscured_rack_is_raised_before_loading(rig):
    rig.visible = False

    async def activate(name, **kwargs):
        if name == 'window_management' and kwargs.get('handle') == '200':
            rig.visible = True
        return {'success': True}

    rig.fl.win.side_effect = activate
    rig.on_transfer = lambda: add_sampler(rig)
    result = asyncio.run(rig.fl.add_sample(str(rig.sample)))
    assert result['success']
    assert len(rig.transfers) == 1


def test_exact_instrument_name_is_resolved_and_checked(rig, monkeypatch):
    resolutions = []

    def resolve(name, kind):
        resolutions.append((name, kind))
        return {"name": name, "path": str(rig.preset)}

    monkeypatch.setattr(loading, "resolve_plugin", resolve)
    rig.on_transfer = lambda: rig.channels.append(channel(2, "Synth"))
    result = asyncio.run(rig.fl.add_instrument(name="Synth"))
    assert result["success"] and result["plugin"] == "Synth"
    assert resolutions == [("Synth", "generator")]
    assert rig.transfers[0][0] == rig.preset


def test_instrument_identity_mismatch_is_not_success(rig):
    rig.on_transfer = lambda: rig.channels.append(channel(2, "Wrong"))
    rig.plugin = "Wrong"
    with pytest.raises(RuntimeError, match="Expected plugin"):
        asyncio.run(rig.fl.add_instrument(name="Synth", path=str(rig.preset)))


def test_effect_add_changes_only_requested_empty_slot(rig):
    fill_effect(rig, slot=0, name="Existing compressor")
    before = deepcopy(rig.slots)
    rig.on_transfer = lambda: fill_effect(rig)
    result = asyncio.run(rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))
    assert result["success"] and result["slot"]["name"] == "EQ"
    assert len(rig.transfers) == 1
    assert [value for index, value in enumerate(rig.slots) if index != 2] == [
        value for index, value in enumerate(before) if index != 2
    ]


def test_case_insensitive_resolved_effect_name_accepts_native_canonical_name(rig, monkeypatch):
    monkeypatch.setattr(loading, "resolve_plugin", lambda name, kind: {
        "name": "EQ", "path": str(rig.preset),
    })
    rig.on_transfer = lambda: fill_effect(rig)
    result = asyncio.run(rig.fl.add_effect(5, 2, name="eq"))
    assert result["success"] and result["slot"]["name"] == "EQ"


def test_effect_add_rejects_changes_to_another_slot(rig):
    def dispatched():
        fill_effect(rig)
        fill_effect(rig, slot=4, name="Unexpected effect")

    rig.on_transfer = dispatched
    with pytest.raises(RuntimeError, match="Another mixer slot changed"):
        asyncio.run(rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))
    assert len(rig.transfers) == 1


def test_effect_identity_mismatch_is_not_success(rig):
    rig.on_transfer = lambda: fill_effect(rig, name="Different effect")
    with pytest.raises(RuntimeError, match="identity differs"):
        asyncio.run(rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))


def test_occupied_mixer_slot_rejected_before_ui_or_transfer(rig):
    fill_effect(rig, name="Existing effect")
    with pytest.raises(ValueError, match="occupied"):
        asyncio.run(rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))
    assert not rig.transfers
    rig.fl.win.assert_not_awaited()


def test_mixer_occupancy_rechecked_after_focus_before_transfer(rig):
    def changed_during_focus(function):
        if function == "ui.setFocused":
            fill_effect(rig, name="Concurrently inserted")

    rig.on_ui_write = changed_during_focus
    rig.on_transfer = lambda: fill_effect(rig)
    with pytest.raises((ValueError, RuntimeError), match="occupied|changed"):
        asyncio.run(rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))
    assert not rig.transfers


def test_mixer_selection_change_while_transfer_helper_prepares_blocks_dispatch(rig):
    rig.before_validation = lambda: setattr(rig, "active_track", 8)
    with pytest.raises(RuntimeError, match="Mixer target changed"):
        asyncio.run(rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))
    assert not rig.transfers


@pytest.mark.parametrize("slot", [-1, 10, True, 2.5])
def test_invalid_effect_slot_never_dispatches(rig, slot):
    with pytest.raises(ValueError, match="slot"):
        asyncio.run(rig.fl.add_effect(5, slot, path=str(rig.preset)))
    assert not rig.transfers
    rig.fl.win.assert_not_awaited()


@pytest.mark.parametrize("track", [-1, 13, True, "2", None])
def test_invalid_effect_track_never_dispatches(rig, track):
    rig.on_transfer = lambda: fill_effect(rig)
    with pytest.raises(ValueError, match="track"):
        asyncio.run(rig.fl.add_effect(track, 2, path=str(rig.preset)))
    assert not rig.transfers
    rig.fl.win.assert_not_awaited()


@pytest.mark.parametrize("operation", ["instrument", "effect", "preset"])
def test_mixer_track_preset_cannot_be_loaded_as_a_single_plugin(rig, operation):
    rig.preset.write_bytes(preset_bytes(0x40))
    rig.on_transfer = (lambda: fill_effect(rig)) if operation == "effect" else (
        lambda: rig.channels.append(channel(2, "Synth"))
    )
    if operation == "instrument":
        call = rig.fl.add_instrument(path=str(rig.preset))
    elif operation == "effect":
        call = rig.fl.add_effect(5, 2, path=str(rig.preset))
    else:
        call = rig.fl.load_preset(str(rig.preset))
    with pytest.raises(ValueError, match="preset|format|FLhd"):
        asyncio.run(call)
    assert not rig.transfers
    rig.fl.win.assert_not_awaited()


def test_existing_channel_preset_restores_project_name_and_mixer_role(rig):
    original = deepcopy(rig.channels)

    def loaded():
        rig.channels[0].update(name="Preset-supplied name", target_fx_track=11)
        rig.plugin_params[0]["value"] = 0.9

    rig.on_transfer = loaded
    result = asyncio.run(rig.fl.load_preset(
        str(rig.preset), target="channel", channel=0, expected_name="Original lead",
    ))
    assert result["success"] and result["parameters_changed"]
    assert result["before"]["params"][0]["value"] == 0.4
    assert result["after"]["params"][0]["value"] == 0.9
    assert rig.channels == original
    assert len(rig.transfers) == 1


def test_sampler_can_be_replaced_without_reading_nonexistent_plugin_parameters(rig):
    original = deepcopy(rig.channels)
    rig.plugin_valid = False

    def loaded():
        rig.plugin_valid = True
        rig.channels[0].update(name="Preset name", target_fx_track=11)

    rig.on_transfer = loaded
    result = asyncio.run(rig.fl.load_preset(
        str(rig.preset), target="channel", channel=0, expected_name="Original lead",
    ))
    assert result["success"]
    assert result["before"] == {"name": "Original lead", "params": [], "channel_type": 0}
    assert result["after"]["params"] == rig.plugin_params
    assert rig.channels == original
    assert len(rig.transfers) == 1


def test_invalid_non_sampler_channel_is_rejected_before_transfer(rig):
    rig.plugin_valid = False
    rig.channel_type = 2
    with pytest.raises(ValueError, match="no inspectable plugin or native Sampler"):
        asyncio.run(rig.fl.load_preset(
            str(rig.preset), target="channel", channel=0, expected_name="Original lead",
        ))
    assert not rig.transfers


@pytest.mark.parametrize("target", ["channel", "mixer"])
def test_existing_preset_target_requires_current_exact_name(rig, target):
    fill_effect(rig)
    args = {"channel": 0} if target == "channel" else {"track": 5, "slot": 2}
    with pytest.raises(ValueError, match="expected_name"):
        asyncio.run(rig.fl.load_preset(
            str(rig.preset), target=target, expected_name="Stale name", **args,
        ))
    assert not rig.transfers


def test_existing_channel_preset_rejects_changed_other_channel(rig):
    rig.on_transfer = lambda: rig.channels[1].update(name="Unexpected old-channel change")
    with pytest.raises(RuntimeError, match="Unexpected channels changed"):
        asyncio.run(rig.fl.load_preset(
            str(rig.preset), target="channel", channel=0, expected_name="Original lead",
        ))
    assert len(rig.transfers) == 1


def test_existing_channel_preset_refuses_hidden_rack_group_target(rig):
    rig.visible_channels = [1]
    with pytest.raises(ValueError, match="outside the visible"):
        asyncio.run(rig.fl.load_preset(
            str(rig.preset), target="channel", channel=0, expected_name="Original lead",
        ))
    assert not rig.transfers


def test_occupied_mixer_preset_preserves_other_effects(rig):
    fill_effect(rig)
    fill_effect(rig, slot=1, name="Compressor")
    original = deepcopy(rig.slots)
    rig.plugin = "EQ"
    rig.on_transfer = lambda: rig.plugin_params[0].update(value=0.9)
    result = asyncio.run(rig.fl.load_preset(
        str(rig.preset), target="mixer", track=5, slot=2, expected_name="EQ",
    ))
    assert result["success"] and result["parameters_changed"]
    assert rig.slots == original
    assert len(rig.transfers) == 1


def test_occupied_mixer_preset_rejects_changes_to_other_effects(rig):
    fill_effect(rig)
    rig.plugin = "EQ"
    rig.on_transfer = lambda: fill_effect(rig, slot=1, name="Unexpected effect")
    with pytest.raises(RuntimeError, match="Another effect changed"):
        asyncio.run(rig.fl.load_preset(
            str(rig.preset), target="mixer", track=5, slot=2, expected_name="EQ",
        ))
    assert len(rig.transfers) == 1


@pytest.mark.parametrize("target", ["channel", "mixer"])
def test_unchanged_native_state_cannot_confirm_an_unconfirmed_preset_delivery(rig, target):
    rig.delivery_confirmed = False
    fill_effect(rig)
    args = ({"channel": 0, "expected_name": "Original lead"} if target == "channel" else
            {"track": 5, "slot": 2, "expected_name": "EQ"})
    with pytest.raises(RuntimeError, match="not confirmed"):
        asyncio.run(rig.fl.load_preset(str(rig.preset), target=target, **args))
    assert len(rig.transfers) == 1


@pytest.mark.parametrize("kind", ["sample", "effect"])
def test_delivery_without_native_state_change_times_out_without_retry(rig, monkeypatch, kind):
    ticks = iter([0, 1, 31])
    monkeypatch.setattr(loading, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(loading, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    operation = (rig.fl.add_sample(str(rig.sample)) if kind == "sample" else
                 rig.fl.add_effect(5, 2, name="EQ", path=str(rig.preset)))
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(operation)
    assert len(rig.transfers) == 1
