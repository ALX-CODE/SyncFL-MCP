"""Direct FSC import with native target selection and saved-FLP verification.

No script launch, menu, shortcut, keyboard, mouse or OS focus calls. The private
OLE interface is experimental. Each write dispatches once and keeps a receipt.
"""

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path

from fl_studio_mcp.utils import background_loading as backend
from fl_studio_mcp.utils import native_windows
from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.piano_score import canonical_note, encode_notes, score_file
from fl_studio_mcp.utils.project_bundle import _inspect, _note, _parse
from fl_studio_mcp.utils.project_native import _window_context
from fl_studio_mcp.utils.protocol import transaction_lock


def _call(conn, function, *args, write=False):
    result = backend._call(conn, "native.call", dict(
        function=function, args=list(args), allow_write=write))
    return result["value"]


def _file(path):
    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() != ".flp":
        raise ValueError("Provide the absolute path of the active saved .flp")
    path = path.resolve(strict=True)
    if not 22 <= path.stat().st_size <= 512 * 1024 * 1024:
        raise ValueError("Project must be 22 bytes..512 MiB")
    data = path.read_bytes()
    info = _inspect(data)
    if not info["version"].startswith("26."):
        raise ValueError("Background piano loading is qualified only for FL26")
    return path, data, info


def _sound_context(conn, channel, data, info, channel_type):
    """Identify the addressed generator and its actual routed effect slots.

    FL does not expose a reliable current preset label. The saved opaque plugin
    state fingerprint distinguishes presets without guessing a display name or
    scanning thousands of Sytrus parameters on every switch.
    """
    valid, track = backend._native_values(conn, [
        ("plugins.isValid", [channel, -1, True]),
        ("channels.getTargetFxTrack", [channel, True]),
    ], time.monotonic() + 5)
    if type(valid) not in (int, bool) or valid not in (0, 1, False, True):
        raise RuntimeError("Native generator identity is unavailable")
    if type(track) is not int or not 0 <= track <= 255:
        raise RuntimeError("Native channel mixer routing is unavailable")
    generator = dict(kind="native_sampler", name="Sampler", display_name=None,
                     preset_name=None, preset_name_verified=False)
    if not valid and channel_type != 0:
        raise RuntimeError("Native generator is unavailable; cannot identify the sound")
    if valid:
        name, display, count = backend._native_values(conn, [
            ("plugins.getPluginName", [channel, -1, False, True]),
            ("plugins.getPluginName", [channel, -1, True, True]),
            ("plugins.getParamCount", [channel, -1, True]),
        ], time.monotonic() + 5)
        if (not isinstance(name, str) or not name or not isinstance(display, str)
                or type(count) is not int or count < 0):
            raise RuntimeError("Native generator readback is incomplete")
        generator.update(kind="plugin", name=name, display_name=display, parameter_count=count)
    channel_id, state = None, bytearray()
    for event in _parse(data)[1]:
        if event.id == 64:
            channel_id = int.from_bytes(event.payload, "little")
        elif event.id in (65, 99, 236, 147):
            channel_id = None
        if channel_id == channel and event.id in (196, 212, 213):
            state.extend(event.encoded)
    generator["saved_state_sha256"] = hashlib.sha256(state).hexdigest()
    generator["sample_sources"] = [r["path"] for r in info["sample_references"]
                                    if r["channel"] == channel]
    rows = backend._call(conn, "mixer.inspect", dict(tracks=[track], routes=False)).get("tracks")
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("index") != track:
        raise RuntimeError("Native mixer target readback is incomplete")
    strip = rows[0]
    if not isinstance(strip.get("slots"), list) or len(strip["slots"]) != 10:
        raise RuntimeError("Native effect-slot readback is incomplete")
    return dict(generator=generator, mixer_track=track, mixer_name=strip["name"],
                effects=strip["slots"])


def _inspect_target(conn, project_path, channel):
    if type(channel) is not int or not 0 <= channel <= 4095:
        raise ValueError("channel must be a zero-based integer in 0..4095")
    path, data, info = _file(project_path)
    window = _window_context()
    native = backend._project(conn, time.monotonic() + 6)
    ping = backend._call(conn, "bridge.ping", {})
    if ping.get("project_native", {}).get("controller_pid") != window["pid"]:
        raise RuntimeError("Controller and FL project window identities differ")
    if window["title"].split(" - FL Studio", 1)[0].casefold() != path.name.casefold():
        raise ValueError("Project file name differs from the active FL project")
    channels = native["context"]["channels"]
    expected_channels = [(c["index"], c["name"]) for c in channels]
    if expected_channels != [(c["id"], c["name"]) for c in info["channels"]]:
        raise ValueError("Saved project channels differ from the active project; save first")
    if channel >= len(channels):
        raise ValueError("Channel does not exist")
    if native["values"]["ppq"] != info["ppq"]:
        raise ValueError("Saved and active project PPQ differ")
    if (native["is_playing"] or native["is_recording"] or native["dirty"] != 0
            or not ping.get("safe_to_edit") or _call(conn, "ui.isInPopupMenu")):
        raise RuntimeError("Save the project and stop transport/popups before backend notes")
    pattern = native["context"]["pattern"]
    notes = next((p["notes"] for p in info["patterns"] if p["id"] == pattern), [])
    sound = _sound_context(conn, channel, data, info, channels[channel]["type"])
    later = backend._project(conn, time.monotonic() + 5)
    if (later["revision"] != native["revision"] or later["dirty"] != 0
            or later["context"] != native["context"] or _window_context() != window):
        raise RuntimeError("Project changed during piano target inspection")
    identity = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(),
                    native_revision=native["revision"], context=native["context"],
                    window=window, channel=channel, pattern=pattern, sound=sound)
    return dict(success=True, ready=True, experimental=True, **identity,
                revision=backend._digest(identity), channel_name=channels[channel]["name"],
                pattern_name=native["context"].get("pattern_name"),
                selected_channel=_call(conn, "channels.selectedChannel", True, 0, True),
                selected_mixer_track=_call(conn, "mixer.trackNumber"),
                patterns=[dict(index=p["id"], name=p["name"]) for p in info["patterns"]],
                ppq=info["ppq"], version=info["version"],
                notes=[n for n in notes if n["channel"] == channel],
                saved_file_only=True,
                limitations=["Supply the active project path; FL's native API does not expose it.",
                             "A write is confirmed only after native save updates that exact file.",
                             "Private OLE interface; basic notes only. No interactive fallback."])


def inspect_background_piano(project_path, channel, connection=None):
    conn = connection or get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        return _inspect_target(conn, project_path, channel)


def select_background_piano(project_path, channel, pattern, expected_revision, connection=None):
    """Switch existing targets and save FL's selection change without altering scores."""
    if type(pattern) is not int or not 1 <= pattern <= 999:
        raise ValueError("pattern must be an existing one-based pattern index")
    conn = connection or get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        before = _inspect_target(conn, project_path, channel)
        if before["revision"] != expected_revision:
            raise ValueError("Piano target changed; inspect again")
        patterns = {p["index"]: p["name"] for p in before["patterns"]}
        if pattern not in patterns:
            raise ValueError("Target pattern is not present in the saved project")
        selection_receipt = None
        if before["pattern"] != pattern:
            source = Path(before["path"]).read_bytes()
            selection_receipt = conn._hardware_dir / f"piano-select-{uuid.uuid4().hex}.json"
            with selection_receipt.with_suffix(".before.flp").open("xb") as stream:
                stream.write(source)
            report = dict(success=False, status="selecting", before=before, pattern=pattern)
            backend._persist(selection_receipt, report)
            try:
                _call(conn, "patterns.jumpToPattern", pattern, write=True)
                native = backend._project(conn, time.monotonic() + 5)
                expected_context = dict(before["context"], pattern=pattern,
                                        pattern_name=patterns[pattern])
                if native["context"] != expected_context or _window_context() != before["window"]:
                    raise RuntimeError("Project changed during pattern selection")
                data, info = _save_and_read(conn, dict(before, context=expected_context), source)
                _verify_untargeted(_inspect(source), info)
                with selection_receipt.with_suffix(".after.flp").open("xb") as stream:
                    stream.write(data)
                report.update(success=True, status="verified")
            except Exception as exc:
                report.update(error=str(exc), status="unverified")
                raise
            finally:
                backend._persist(selection_receipt, report)
        after = _inspect_target(conn, project_path, channel)
        if after["pattern"] != pattern or after["pattern_name"] != patterns[pattern]:
            raise RuntimeError("Native pattern selection did not match")
        _prepare_roll(conn, after)
        _call(conn, "mixer.setTrackNumber", after["sound"]["mixer_track"], write=True)
        selected = _inspect_target(conn, project_path, channel)
        if (selected["selected_channel"] != channel or selected["sound"] != after["sound"]
                or selected["selected_mixer_track"] != after["sound"]["mixer_track"]):
            raise RuntimeError("Native sound selection did not match")
        selected["desktop_input"] = False
        selected["selection_receipt"] = str(selection_receipt) if selection_receipt else None
        return selected


def _prepare_roll(conn, before):
    channel = before["channel"]
    _call(conn, "channels.selectOneChannel", channel, True, write=True)
    event = _call(conn, "channels.getRecEventId", channel, True)
    # EE_PR selects the explicit channel's piano-roll editor. The base event ID
    # is qualified against FL26; no currently focused editor command is sent.
    _call(conn, "ui.openEventEditor", event, 1, False, write=True)
    _call(conn, "ui.showWindow", 3, write=True)
    if _call(conn, "channels.selectedChannel", True, 0, True) != channel:
        raise RuntimeError("Native piano channel selection did not match")
    if _call(conn, "patterns.patternNumber") != before["pattern"]:
        raise RuntimeError("Pattern changed while preparing the piano roll")
    main = before["window"]
    matches = [w for w in native_windows.windows(main["handle"], pid=main["pid"])
               if w["class"] == "TEventEditForm" and w["title"].startswith("Piano roll")
               and w["enabled"]]
    if len(matches) != 1:
        raise RuntimeError("Cannot identify the explicit FL piano-roll window")
    roll = matches[0]
    x, y, width, height = roll["bounds"]
    point = [x + width * 3 // 4, y + height // 2]
    if not native_windows.point_belongs_to(point, roll["handle"]):
        native_windows.expose_fl_window(main)
    if width < 200 or height < 200 or not native_windows.point_belongs_to(point, roll["handle"]):
        raise RuntimeError("An external window covers the prepared piano-roll target")
    return dict(main=main, mixer=roll, point=point,
                process=backend._process_identity(main["pid"]))


def _save_and_read(conn, before, previous_data, *, force=False):
    native = backend._project(conn, time.monotonic() + 6)
    if native["context"] != before["context"] or native["is_playing"] or native["is_recording"]:
        raise RuntimeError("Project changed after import; inspect the receipt")
    params = dict(
        expected_revision=native["revision"], expected_window_title=before["window"]["title"],
        dry_run=False)
    if force:
        params["force"] = True
    response = backend._call(conn, "project.save_current", params)
    last, stable = None, 0
    for _ in range(20):
        time.sleep(.15)
        current = backend._project(conn, time.monotonic() + 4)
        if current["context"] != before["context"]:
            raise RuntimeError("Project changed during saved-score verification")
        data = Path(before["path"]).read_bytes()
        if current["dirty"] == 0 and data == last:
            stable += 1
        else:
            stable = 0
        last = data
        if stable >= 2:
            if data == previous_data and response.get("status") != "already_clean":
                raise RuntimeError("Native save did not update the supplied active project path")
            return data, _inspect(data)
    raise RuntimeError("Saved score did not settle; do not replay the import")


def _verify_notes(before_info, after_info, pattern, channel, raw):
    expected = Counter(canonical_note(_note(raw[i:i+24]), target=True)
                       for i in range(0, len(raw), 24))
    actual = Counter(canonical_note(n, target=True) for p in after_info["patterns"]
                     if p["id"] == pattern for n in p["notes"] if n["channel"] == channel)
    if expected != actual:
        raise RuntimeError("Saved target notes differ from the requested score")
    _verify_untargeted(before_info, after_info, (pattern, channel))


def _verify_untargeted(before_info, after_info, target=None):
    def others(info):
        return Counter((p["id"], canonical_note(n)) for p in info["patterns"] for n in p["notes"]
                       if (p["id"], n["channel"]) != target)
    if others(before_info) != others(after_info):
        raise RuntimeError("Another channel or pattern changed during the import")
    if (before_info["channels"] != after_info["channels"]
            or before_info["clips"] != after_info["clips"]
            or [(p["id"], p["name"]) for p in before_info["patterns"]]
            != [(p["id"], p["name"]) for p in after_info["patterns"]]):
        raise RuntimeError("Channel identities or Playlist clips changed during the import")
    if any(before_info[key] != after_info[key]
           for key in ("ppq", "tempo", "time_signature")):
        raise RuntimeError("Project timing changed during the import")


def write_background_piano(project_path, channel, notes, expected_revision, expected_channel,
                           receipt_file, apply=False, connection=None):
    """Replace one current-pattern score, preserving other channel/pattern notes."""
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    receipt = Path(receipt_file)
    if not receipt.is_absolute() or receipt.suffix.lower() != ".json":
        raise ValueError("Provide a new absolute .json receipt_file")
    conn = connection or get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        before = _inspect_target(conn, project_path, channel)
        if before["revision"] != expected_revision or before["channel_name"] != expected_channel:
            raise ValueError("Piano target changed; inspect again")
        raw = encode_notes(notes, before["ppq"])
        score = score_file(raw, before["ppq"], before["version"])
        executable = backend.verify_runtime()
        report = dict(success=True, status="preflighted", backend="native_ole_score",
                      desktop_input=False, before=before, requested_notes=len(notes),
                      receipt_file=str(receipt), limitations=before["limitations"])
        if not apply:
            return report
        report.update(success=False, status="reserved")
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            source = Path(before["path"]).read_bytes()
            with receipt.with_suffix(".before.flp").open("xb") as stream:
                stream.write(source)
            with receipt.with_suffix(".fsc").open("xb") as stream:
                stream.write(score)
            if _inspect_target(conn, project_path, channel)["revision"] != expected_revision:
                raise RuntimeError("Target changed before native panel preparation")
            report["input_before"] = backend._safe_input_snapshot()
            window = _prepare_roll(conn, before)
            if _inspect_target(conn, project_path, channel)["revision"] != expected_revision:
                raise RuntimeError("Target changed before score dispatch")
            report.update(status="dispatching", window=window)
            backend._persist(receipt, report)
            report["cli"] = backend._dispatch(
                executable, dict(window=window), receipt.with_suffix(".fsc"))
            report.update(status="awaiting_readback")
            backend._persist(receipt, report)
            if not backend._confirmed(report["cli"]):
                raise RuntimeError("Score callback was not confirmed; no retry was sent")
            after_data, after = _save_and_read(conn, before, source)
            _verify_notes(_inspect(source), after, before["pattern"], channel, raw)
            after_target = _inspect_target(conn, project_path, channel)
            if (after_target["sound"] != before["sound"]
                    or after_target["selected_channel"] != channel
                    or after_target["pattern"] != before["pattern"]):
                raise RuntimeError("Pattern, selected sound or effects changed during import")
            with receipt.with_suffix(".after.flp").open("xb") as stream:
                stream.write(after_data)
            report.update(success=True, status="verified", verified_notes=len(notes),
                          after=after_target,
                          after_sha256=hashlib.sha256(after_data).hexdigest(),
                verification="Exact saved score; other channel/pattern notes preserved")
        except Exception as exc:
            report.update(success=False, status="unverified", error=str(exc))
        report["input_after"] = backend._safe_input_snapshot()
        backend._persist(receipt, report)
        return report
