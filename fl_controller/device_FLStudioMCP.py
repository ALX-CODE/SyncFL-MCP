# name=FL Studio MCP Controller
# supportedDevices=ALX FL Bridge (FL Studio)
"""SyncFL's in-host command adapter for the documented FL Studio MIDI API.

Protocol validation, expiry, deduplication and receipts are installed by the
companion module before FL invokes any callback. This file only defines model
operations and the host lifecycle hooks; it never polls or injects UI input.
"""

import json
import math
import os
import sys
from pathlib import Path

import channels
import mixer
import plugins
import transport


def _get_script_dir():
    installed = globals().get("__file__")
    if installed and Path(installed).is_file():
        return Path(installed).resolve().parent
    root = os.environ.get("FL_USER_DATA_FOLDER")
    if root:
        settings = Path(root) / "FL Studio" / "Settings"
    else:
        home = Path.home()
        if sys.platform in ("win32", "darwin"):
            settings = home / "Documents" / "Image-Line" / "FL Studio" / "Settings"
        else:
            settings = home / ".fl-studio" / "Settings"
        relocated = home / "Documents" / "MUSIC SOFTWARE FOLDERS" / "Image-Line"
        if sys.platform == "win32" and (relocated / "FL Studio" / "Settings").is_dir():
            settings = relocated / "FL Studio" / "Settings"
    return settings / "Hardware" / "FLStudioMCP"


SCRIPT_DIR = _get_script_dir()
COMMAND_FILE, RESPONSE_FILE = (
    SCRIPT_DIR / name for name in ("mcp_command.json", "mcp_response.json")
)
TRIGGER_NOTE = 127


def OnInit():
    print("SyncFL controller ready (protocol 2)")


def OnDeInit():
    print("SyncFL controller stopped")


def OnMidiMsg(event):
    trigger = (
        getattr(event, "midiId", 0) == 0x90
        and getattr(event, "data1", -1) == TRIGGER_NOTE
        and getattr(event, "data2", 0) > 0
    )
    if trigger:
        event.handled = True
        execute_pending_command(event)


def OnIdle():
    return None


def OnProjectLoad(status):
    callback = globals().get("production_on_project_load")
    if callback is not None:
        callback(status)


def OnDirtyChannel(index, flag):
    callback = globals().get("production_on_dirty_channel")
    if callback is not None:
        callback(index, flag)


def _validate_basic(action, values):
    if not isinstance(values, dict):
        raise ValueError("Command parameters must be an object")
    for field in (
        "track",
        "index",
        "plugin_index",
        "param_index",
        "channel",
        "channel_index",
        "mixer_track",
        "steps",
        "max_params",
    ):
        if field in values and (type(values[field]) is not int or values[field] < 0):
            raise ValueError(field + " must be a nonnegative integer")
    limits = {
        "volume": (0, 1.25 if action.startswith("mixer.") else 1),
        "pan": (-1, 1),
        "separation": (-1, 1),
        "speed": (0.25, 4),
    }
    if action == "plugins.setParamValue":
        limits["value"] = (0, 1)
    for field, (low, high) in limits.items():
        if field in values:
            value = values[field]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(field + " is outside its finite range")
    for field, low, high in (
        ("slot_index", -1, 9),
        ("note", 0, 127),
        ("velocity", 0, 127),
        ("midi_channel", -1, 15),
        ("r", 0, 255),
        ("g", 0, 255),
        ("b", 0, 255),
    ):
        if field in values and (type(values[field]) is not int or not low <= values[field] <= high):
            raise ValueError(field + " is outside its integer range")
    for field in ("select", "muted", "solo", "include_empty", "global_count", "use_global"):
        if (
            field in values
            and type(values[field]) is not bool
            and not (field in ("muted", "solo") and values[field] is None)
        ):
            raise ValueError(field + " must be a boolean or null")
    for field in ("steps", "max_params"):
        if values.get(field, 0) > 65536:
            raise ValueError(field + " exceeds the per-request limit")
    if action == "mixer.soloTrack" and (
        type(values.get("mode", 3)) is not int or values.get("mode", 3) not in (1, 2, 3, 4)
    ):
        raise ValueError("Solo mode must be in 1..4")
    for field in ("track", "mixer_track", "index", "channel", "channel_index", "plugin_index"):
        if field not in values:
            continue
        is_mixer = field in ("track", "mixer_track") or (
            action.startswith("plugins.") and values.get("slot_index", -1) >= 0
        )
        count = (
            mixer.getTrackInfo(2) + 1
            if is_mixer
            else channels.channelCount(values.get("use_global", True))
        )
        if values[field] >= count:
            raise ValueError(field + " is outside the current host range")


def handle_transport_get_status():
    return dict(
        position=transport.getSongPosHint(),
        loop_mode=("pattern", "song")[bool(transport.getLoopMode())],
        is_recording=bool(transport.isRecording()),
        is_playing=bool(transport.isPlaying()),
    )


def _transport_command(operation, p):
    if operation in ("start", "stop", "record"):
        getattr(transport, operation)()
        return {
            "start": lambda: {"is_playing": bool(transport.isPlaying())},
            "record": lambda: {"is_recording": bool(transport.isRecording())},
            "stop": lambda: {"stopped": True},
        }[operation]()
    if operation == "getStatus":
        return handle_transport_get_status()
    if operation == "getLength":
        return {
            unit: transport.getSongLength(mode)
            for unit, mode in (("milliseconds", 0), ("seconds", 1), ("ticks", 2))
        }
    if operation == "setPosition":
        mode, position = p.get("mode", 1), p.get("position", 0)
        if type(mode) is not int or mode not in (-1, 0, 1, 2):
            raise ValueError("Unknown position mode")
        if type(position) not in (int, float) or not math.isfinite(position) or position < 0:
            raise ValueError("Position must be finite and nonnegative")
        if mode == -1 and position > 1:
            raise ValueError("Normalized position exceeds 1")
        transport.setSongPos(position, mode)
        return {"position": transport.getSongPosHint()}
    if operation == "setLoopMode":
        requested = p.get("mode", "pattern")
        if requested not in ("pattern", "song"):
            raise ValueError("Unknown loop mode")
        if bool(transport.getLoopMode()) != (requested == "song"):
            transport.setLoopMode()
        return {"mode": ("pattern", "song")[bool(transport.getLoopMode())]}
    if operation == "setPlaybackSpeed":
        transport.setPlaybackSpeed(p.get("speed", 1.0))
        return {"speed": p.get("speed", 1.0)}
    raise ValueError("Unknown transport operation: " + operation)


def handle_mixer_get_track_info(p):
    track = p.get("track", 0)
    readers = {
        "name": mixer.getTrackName,
        "volume": mixer.getTrackVolume,
        "pan": mixer.getTrackPan,
        "stereo_separation": mixer.getTrackStereoSep,
    }
    state = {key: read(track) for key, read in readers.items()}
    state.update(
        index=track, volume_db=mixer.getTrackVolume(track, 1), color=hex(mixer.getTrackColor(track))
    )
    for key, read in (
        ("is_muted", mixer.isTrackMuted),
        ("is_solo", mixer.isTrackSolo),
        ("is_armed", mixer.isTrackArmed),
    ):
        state[key] = bool(read(track))
    return state


def _rgb(p):
    red, green, blue = (p.get(component, 0) for component in ("r", "g", "b"))
    return (red << 16) | (green << 8) | blue


def _toggle(value):
    return -1 if value is None else int(value)


def _mixer_command(operation, p):
    track = p.get("track", 0)
    if operation == "getTrackCount":
        return {"count": mixer.trackCount()}
    if operation == "getTrackInfo":
        return handle_mixer_get_track_info(p)
    if operation == "getAllTracks":
        total = mixer.getTrackInfo(2) + 1
        assigned = {
            channels.getTargetFxTrack(index, True) for index in range(channels.channelCount(True))
        }
        rows = []
        for index in range(total):
            name = mixer.getTrackName(index)
            occupied = index == 0 or index in assigned or (name and not name.startswith("Insert "))
            if not p.get("include_empty") and not occupied:
                occupied = any(plugins.isValid(index, slot, True) for slot in range(10))
                occupied = occupied or any(
                    mixer.getRouteSendActive(other, index)
                    for other in range(total)
                    if other != index
                )
                occupied = occupied or any(
                    mixer.getRouteSendActive(index, other)
                    for other in range(1, total)
                    if other != index
                )
            if occupied or p.get("include_empty"):
                rows.append(handle_mixer_get_track_info({"track": index}))
        return {"tracks": rows}
    setters = {
        "setTrackVolume": ("setTrackVolume", "volume", 0.8),
        "setTrackPan": ("setTrackPan", "pan", 0),
        "setTrackName": ("setTrackName", "name", ""),
        "setStereoSep": ("setTrackStereoSep", "separation", 0),
    }
    if operation in setters:
        method, field, default = setters[operation]
        getattr(mixer, method)(track, p.get(field, default))
    elif operation == "setTrackColor":
        mixer.setTrackColor(track, _rgb(p))
    elif operation == "muteTrack":
        mixer.muteTrack(track, _toggle(p.get("muted")))
    elif operation == "soloTrack":
        mixer.soloTrack(track, _toggle(p.get("solo")), p.get("mode", 3))
    elif operation == "armTrack":
        mixer.armTrack(track)
    else:
        raise ValueError("Unknown mixer operation: " + operation)
    result = handle_mixer_get_track_info({"track": track})
    result["track_name"] = result["name"]
    return result


def handle_channels_get_info(p):
    index, global_index = p.get("index", 0), p.get("use_global", True)
    readers = {
        "name": channels.getChannelName,
        "pan": channels.getChannelPan,
        "target_fx_track": channels.getTargetFxTrack,
    }
    result = {field: read(index, global_index) for field, read in readers.items()}
    result.update(
        index=index,
        color=hex(channels.getChannelColor(index, global_index)),
        volume=channels.getChannelVolume(index, 0, global_index),
        volume_db=channels.getChannelVolume(index, 1, global_index),
        pitch=channels.getChannelPitch(index, useGlobalIndex=global_index),
    )
    result["volume_normalized"] = result["volume"]
    for field, read in (
        ("is_muted", channels.isChannelMuted),
        ("is_solo", channels.isChannelSolo),
        ("is_selected", channels.isChannelSelected),
    ):
        result[field] = bool(read(index, global_index))
    return result


def handle_channels_get_selected():
    selected = channels.selectedChannel(canBeNone=True, indexGlobal=True)
    return {
        "channel": None
        if selected is None or selected < 0
        else handle_channels_get_info({"index": selected})
    }


def handle_channels_set_volume(p):
    channel = p.get("index", 0)
    channels.setChannelVolume(channel, p.get("volume", 0.8), 0, True)
    return dict(
        channel_name=channels.getChannelName(channel, True),
        volume=channels.getChannelVolume(channel, 0, True),
    )


def handle_channels_set_pan(p):
    channel = p.get("index", 0)
    channels.setChannelPan(channel, p.get("pan", 0), 0, True)
    return dict(
        channel_name=channels.getChannelName(channel, True),
        pan=channels.getChannelPan(channel, True),
    )


def _channel_command(operation, p):
    index = p.get("index", 0)
    if operation == "getCount":
        return {"count": channels.channelCount(p.get("global_count", True))}
    if operation == "getInfo":
        return handle_channels_get_info(p)
    if operation == "getSelected":
        return handle_channels_get_selected()
    if operation == "getAll":
        return {
            "channels": [
                handle_channels_get_info({"index": number})
                for number in range(channels.channelCount(True))
            ]
        }
    if operation == "setVolume":
        return handle_channels_set_volume(p)
    if operation == "setPan":
        return handle_channels_set_pan(p)
    if operation == "triggerNote":
        note, velocity = p.get("note", 60), p.get("velocity", 100)
        channels.midiNoteOn(p.get("channel", 0), note, velocity, p.get("midi_channel", -1))
        return dict(triggered=True, note=note, velocity=velocity)
    if operation in ("getGridBit", "setGridBit", "getStepSequence", "setStepSequence"):
        channel = p.get("channel", 0)
        if operation.endswith("GridBit"):
            step = p.get("position", 0)
            if type(step) is not int or step < 0:
                raise ValueError("Step must be a nonnegative integer")
            if operation == "setGridBit":
                if type(p.get("value")) is not bool:
                    raise ValueError("Step state must be boolean")
                channels.setGridBit(channel, step, int(p["value"]), True)
            return {"value": bool(channels.getGridBit(channel, step, True))}
        if operation == "getStepSequence":
            return {
                "sequence": [
                    bool(channels.getGridBit(channel, step, True))
                    for step in range(p.get("steps", 16))
                ]
            }
        states = p.get("pattern", [])
        if (
            not isinstance(states, list)
            or len(states) > 65536
            or any(type(state) is not bool for state in states)
        ):
            raise ValueError("Pattern must be a list of boolean steps")
        for step, state in enumerate(states):
            channels.setGridBit(channel, step, int(state), True)
        return dict(
            active_steps=sum(states),
            total_steps=len(states),
            channel_name=channels.getChannelName(channel, True),
        )
    if operation == "select":
        channels.selectChannel(index, int(p.get("select", True)), True)
        return dict(
            selected=bool(channels.isChannelSelected(index, True)),
            channel_name=channels.getChannelName(index, True),
        )
    if operation == "selectOne":
        channels.selectOneChannel(index, True)
    elif operation == "mute":
        channels.muteChannel(index, _toggle(p.get("muted")), True)
    elif operation == "solo":
        channels.soloChannel(index, _toggle(p.get("solo")), True)
    elif operation == "setName":
        channels.setChannelName(index, p.get("name", ""), True)
    elif operation == "setColor":
        channels.setChannelColor(index, _rgb(p), True)
    elif operation == "routeToMixer":
        index = p.get("channel_index", 0)
        channels.setTargetFxTrack(index, p.get("mixer_track", 0), True)
    else:
        raise ValueError("Unknown channel operation: " + operation)
    result = handle_channels_get_info({"index": index})
    result["channel_name"] = result["name"]
    result["mixer_track"] = result["target_fx_track"]
    return result


def handle_plugins_get_color(p):
    index, slot = p.get("index", 0), p.get("slot_index", -1)
    global_index = slot >= 0 or p.get("use_global", True)
    return dict(color=hex(plugins.getColor(index, slot, 0, 0, global_index)))


def _plugin_command(operation, p):
    index = p.get("plugin_index", p.get("index", 0))
    slot = p.get("slot_index", -1)
    global_index = slot >= 0 or p.get("use_global", True)
    location = (index, slot, global_index)
    if operation == "isValid":
        return {"valid": bool(plugins.isValid(*location))}
    if operation == "getColor":
        return handle_plugins_get_color(p)
    if operation == "getName":
        return {"name": plugins.getPluginName(index, slot, False, global_index)}
    if operation in ("getParamCount", "getPresetCount"):
        read = plugins.getParamCount if operation == "getParamCount" else plugins.getPresetCount
        return {"count": read(*location)}
    if operation in ("nextPreset", "prevPreset"):
        getattr(plugins, operation)(*location)
        return {"plugin_name": plugins.getPluginName(index, slot, False, global_index)}

    def parameter(number):
        target = (number, *location)
        return dict(
            index=number,
            name=plugins.getParamName(*target),
            value=plugins.getParamValue(*target),
            value_string=plugins.getParamValueString(*target),
        )

    if operation == "getParams":
        return {
            "params": [
                parameter(number)
                for number in range(min(plugins.getParamCount(*location), p.get("max_params", 50)))
            ]
        }
    number = p.get("param_index", 0)
    if number >= plugins.getParamCount(*location):
        raise ValueError("Parameter index is outside this plugin's exposed controls")
    if operation == "setParamValue":
        plugins.setParamValue(p.get("value", 0), number, index, slot, 0, global_index)
    elif operation != "getParamValue":
        raise ValueError("Unknown plugin operation: " + operation)
    return parameter(number)


def dispatch_command(action, params):
    if not isinstance(action, str) or "." not in action:
        raise ValueError("Expected a namespaced command")
    namespace, operation = action.split(".", 1)
    handlers = {
        "transport": _transport_command,
        "mixer": _mixer_command,
        "channels": _channel_command,
        "plugins": _plugin_command,
    }
    if namespace not in handlers:
        return {"error": "Unsupported command: " + action}
    _validate_basic(action, params)
    return handlers[namespace](operation, params)


# Execute a fresh companion on every FL script reload; embedded import caches
# must not retain handlers from the previous installation.
_companion = SCRIPT_DIR / "mcp_extended.py"
exec(compile(_companion.read_text(encoding="utf-8"), str(_companion), "exec"), globals())
