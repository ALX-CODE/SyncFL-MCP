"""Guarded production operations executed inside FL's MIDI callback."""
import hashlib
import json
import math
import os
import re
import time

import channels
import general
import mixer
import midi
import patterns
import transport
import ui

_previous_dispatch = globals()["extended_dispatch"]
_SESSION = str(time.time_ns())
_PROJECT_GENERATION = 0
_CHANNEL_GENERATION = 0
_BUILD = hashlib.sha256(b"".join(
    (globals()["SCRIPT_DIR"] / name).read_bytes()
    for name in ("device_FLStudioMCP.py", "mcp_extended.py", "mcp_production.py",
                 "native_catalog.json")
)).hexdigest()


def production_on_project_load(status):
    global _PROJECT_GENERATION
    if status == 0:
        _PROJECT_GENERATION += 1


def production_on_dirty_channel(index, flag):
    global _CHANNEL_GENERATION
    if flag in (0, 1, 2):  # New, delete, replace; equal names are not equal objects.
        _CHANNEL_GENERATION += 1


def _int(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + " must be an integer in %s..%s" % (low, high))
    return value


def _num(value, low, high, label):
    import math
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(label + " must be finite in %s..%s" % (low, high))
    return value


def production_context():
    return dict(
        session=_SESSION, project_generation=_PROJECT_GENERATION,
        channel_generation=_CHANNEL_GENERATION,
        program_title=ui.getProgTitle(),
        project_title=general.getProjectTitle() if hasattr(general, "getProjectTitle") else None,
        pattern=patterns.patternNumber(),
        pattern_name=patterns.getPatternName(patterns.patternNumber()),
        channels=[dict(index=i, name=channels.getChannelName(i, True),
                       type=channels.getChannelType(i, True))
                  for i in range(channels.channelCount(True))],
    )


def production_inspect(scope, channel=None, pattern=None, total_steps=None):
    if (scope == "project" and any(v is not None for v in (channel, pattern, total_steps))
            or scope == "channel" and any(v is not None for v in (pattern, total_steps))):
        raise ValueError("Target fields do not match scope")
    context = production_context()
    target = dict(scope=scope)
    if scope == "project":
        # FL's default tempo representation is millibeats/minute, not normalized.
        values = dict(tempo_bpm=mixer.getCurrentTempo(0) / 1000.0,
                      shuffle=channels.getShuffle() if hasattr(channels, "getShuffle") else None,
                      ppq=general.getRecPPQ())
    elif scope in ("channel", "steps"):
        _int(channel, 0, len(context["channels"]) - 1, "global channel")
        target["channel"] = channel
        if scope == "channel":
            values = dict(
                name=channels.getChannelName(channel, True),
                volume_normalized=channels.getChannelVolume(channel, 0, True),
                volume_db=channels.getChannelVolume(channel, 1, True),
                pan=channels.getChannelPan(channel, True),
                # FL26 mode 1 reads integer cents despite the reference saying
                # semitones. Use normalized pitch and the channel's pitch range.
                pitch_semitones=(channels.getChannelPitch(channel, 0, True)
                                 * channels.getChannelPitch(channel, 2, True)),
                pitch_normalized=channels.getChannelPitch(channel, 0, True),
                pitch_range_semitones=channels.getChannelPitch(channel, 2, True),
                muted=bool(channels.isChannelMuted(channel, True)),
                mixer_track=channels.getTargetFxTrack(channel, True),
            )
            if not math.isfinite(values["volume_db"]):
                values["volume_db"] = None
        else:
            _int(pattern, 1, patterns.patternMax(), "pattern")
            if pattern != context["pattern"]:
                raise ValueError("Select the requested pattern before inspecting its steps")
            _int(total_steps, 1, 1024, "total_steps")
            target.update(pattern=pattern, total_steps=total_steps)
            values = dict(sequence=[bool(channels.getGridBit(channel, i, True))
                                    for i in range(total_steps)])
    else:
        raise ValueError("scope must be project, channel or steps")
    revision = hashlib.sha256(json.dumps(
        [context, target, values], sort_keys=True, allow_nan=False).encode()).hexdigest()
    return dict(context=context, target=target, values=values, revision=revision,
                controller_build=_BUILD, api_version=general.getVersion(),
                is_playing=bool(transport.isPlaying()), is_recording=bool(transport.isRecording()),
                dirty=general.getChangedFlag())


def production_prepare(p):
    if set(p) - {"scope", "channel", "pattern", "total_steps", "changes",
                 "expected_revision", "dry_run"}:
        raise ValueError("Unknown production request fields")
    target = {k: p[k] for k in ("scope", "channel", "pattern", "total_steps") if k in p}
    before = production_inspect(**target)
    if before["revision"] != p.get("expected_revision"):
        raise ValueError("Revision changed; inspect again before applying")
    if before["is_playing"] or before["is_recording"] or not general.safeToEdit():
        raise ValueError("Stop transport and resolve modal operations before editing")
    if type(p.get("dry_run", True)) is not bool:
        raise ValueError("dry_run must be boolean")
    changes = p.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Provide a nonempty changes object")
    scope = p["scope"]
    if scope == "project":
        if set(changes) - {"tempo_bpm", "shuffle"}:
            raise ValueError("Only BPM and shuffle are supported; PPQ/meter writes are not verified")
        if "tempo_bpm" in changes:
            _num(changes["tempo_bpm"], 10, 522, "tempo_bpm")
            if not hasattr(mixer, "setCurrentTempo"):
                raise ValueError("This FL API does not support setting tempo")
        if "shuffle" in changes:
            _num(changes["shuffle"], 0, 1, "shuffle")
            if not all(hasattr(channels, name) for name in ("getShuffle", "setShuffle")):
                raise ValueError("This FL API does not support setting shuffle")
    elif scope == "channel":
        if set(changes) - {"volume_normalized", "pan", "pitch_semitones", "muted", "mixer_track"}:
            raise ValueError("Unknown channel setting")
        for key, bounds in {"volume_normalized": (0, 1), "pan": (-1, 1),
                            "pitch_semitones": (-48, 48)}.items():
            if key in changes:
                _num(changes[key], *bounds, key)
        if "muted" in changes and type(changes["muted"]) is not bool:
            raise ValueError("muted must be boolean")
        if ("pitch_semitones" in changes and
                abs(changes["pitch_semitones"]) > before["values"]["pitch_range_semitones"]):
            raise ValueError("Pitch exceeds this channel's current pitch range")
        if "pitch_semitones" in changes:
            _num(before["values"]["pitch_range_semitones"], 0.00001, 96, "pitch range")
        if "mixer_track" in changes:
            _int(changes["mixer_track"], 0, mixer.getTrackInfo(2), "mixer_track")
    else:
        if set(changes) != {"sequence"}:
            raise ValueError("Provide only sequence for a scoped step replacement")
        sequence = changes["sequence"]
        if (not isinstance(sequence, list) or len(sequence) > p["total_steps"]
                or any(type(bit) is not bool for bit in sequence)):
            raise ValueError("sequence must be booleans within total_steps")
        changes = dict(sequence=sequence + [False] * (p["total_steps"] - len(sequence)))
    return before, changes


def production_apply(p):
    before, changes = production_prepare(p)
    report = dict(before=before, requested=changes, applied=[],
                  dry_run=p.get("dry_run", True), success=True, outcome="not_applied")
    if report["dry_run"]:
        return report
    try:
        scope, channel = p["scope"], p.get("channel")
        for key, value in changes.items():
            # An exception may follow a mutation; record intent before entering FL.
            report["in_flight"] = key
            if scope == "project":
                if key == "tempo_bpm":
                    mixer.setCurrentTempo(round(value * 1000), 0)
                else:
                    channels.setShuffle(value)
            elif scope == "channel":
                if key == "volume_normalized":
                    channels.setChannelVolume(channel, value, 0, True)
                elif key == "pan":
                    channels.setChannelPan(channel, value, 0, True)
                elif key == "pitch_semitones":
                    pitch_range = before["values"]["pitch_range_semitones"]
                    if pitch_range <= 0:
                        raise ValueError("Channel pitch range must be positive")
                    channels.setChannelPitch(channel, value / pitch_range, 0, 0, True)
                elif key == "muted":
                    channels.muteChannel(channel, int(value), True)
                else:
                    channels.setTargetFxTrack(channel, value, True)
            else:
                for i, bit in enumerate(value):
                    report["step_in_flight"] = i
                    channels.setGridBit(channel, i, int(bit), True)
            report["applied"].append(key)
            report.pop("in_flight", None)
            report.pop("step_in_flight", None)
        report["outcome"] = "awaiting_readback"
    except Exception as exc:
        report.update(success=False, outcome="unknown", error=str(exc))
    return report


def project_save_capability():
    try:
        controller_pid = os.getpid()
    except Exception:
        controller_pid = None  # Embedded Python may restrict an OS function.
    available = (callable(getattr(transport, "globalTransport", None))
                 and getattr(midi, "FPT_Save", None) == 92
                 and getattr(midi, "GT_Global", None) == 8
                 and type(controller_pid) is int and controller_pid > 0)
    return dict(protocol=1, available=available, force_save_supported=True,
                qualification="host_state_only",
                controller_pid=controller_pid, desktop_input=False,
                absolute_path_known=False)


def project_save_current(p):
    if set(p) - {"expected_revision", "expected_window_title", "dry_run", "force"}:
        raise ValueError("Unknown save-current request fields")
    if type(p.get("dry_run", True)) is not bool:
        raise ValueError("dry_run must be boolean")
    if type(p.get("force", False)) is not bool:
        raise ValueError("force must be boolean")
    title = p.get("expected_window_title")
    if (not isinstance(title, str) or len(title) > 2048
            or not re.fullmatch(r"[^\r\n/\\]+\.flp - FL Studio(?: [^\r\n]*)?", title,
                                flags=re.IGNORECASE)):
        raise ValueError("Provide the exact named FL project window title")
    capability = project_save_capability()
    if not capability["available"]:
        raise ValueError("This host lacks save-current API support or process identity")
    before = production_inspect("project")
    if before["revision"] != p.get("expected_revision"):
        raise ValueError("Revision changed; inspect again before saving")
    if before["is_playing"] or before["is_recording"] or not general.safeToEdit():
        raise ValueError("Stop transport and resolve modal operations before saving")
    result = dict(before=before, expected_window_title=title, capability=capability,
                  dry_run=p.get("dry_run", True), status="preflighted",
                  dispatched=False, absolute_path_unknown=True,
                  disk_verified=False)
    if type(before["dirty"]) is not int or before["dirty"] not in (0, 1, 2):
        raise ValueError("Save requires a recognized host changed flag")
    if before["dirty"] == 0 and not p.get("force", False):
        result.update(success=True, status="already_clean", dispatch_ack="not_dispatched")
        return result
    if result["dry_run"]:
        return result
    event_flags = globals().get("ACTIVE_MIDI_FLAGS")
    if type(event_flags) is not int or not 0 <= event_flags <= 255:
        raise ValueError("Save requires the actual triggering MIDI event permissions")
    # This invokes the host command; only later callbacks can establish settlement.
    result.update(status="dispatching", dispatched=True)
    try:
        # FL SDK source/c/generictransport.h (https://forum.image-line.com/viewtopic.php?t=12092):
        # Button 1 switches without a release; 2 holds and expects a later release.
        handled = transport.globalTransport(midi.FPT_Save, 1, event_flags, midi.GT_Global)
        # The MIDI manual names routing codes but does not promise FPT_Save returns 8.
        # Preserve the return; only independent changed-flag reads establish host state.
        acknowledgements = {-1: "recognized_cannot", 0: "recognized_none",
                            1: "recognized_plugin", 2: "recognized_form",
                            4: "recognized_menu", 5: "recognized_delayed",
                            8: "recognized_global", 15: "recognized_all"}
        acknowledgement = (acknowledgements.get(handled, "unrecognized")
                           if type(handled) is int else "unrecognized")
        result.update(success=True, status="dispatched", handled=handled,
                      transport_return=handled, dispatch_ack=acknowledgement)
    except Exception as exc:
        result.update(success=False, status="unknown", dispatch_ack="call_exception",
                      error=str(exc))
    return result


def extended_dispatch(action, p):
    if action == "production.inspect":
        return production_inspect(**p)
    if action == "production.apply":
        return production_apply(p)
    if action == "project.save_current":
        return project_save_current(p)
    result = _previous_dispatch(action, p)
    if action == "bridge.ping":
        result.update(controller_build=_BUILD, production_protocol=1, session=_SESSION)
        result["project_native"] = project_save_capability()
    if action == "bridge.capabilities":
        result["typed"] += ["production.inspect", "production.apply"]
        result["typed"].append("project.save_current")
        result["production_protocol"] = 1
        result["project_native"] = project_save_capability()
    return result
