"""One native WAV import, exact saved placement, and independent FL reload proof.

The FL26/96 DPI Playlist profile comes from the native window tree and screenshot
captured on 2026-09-29. Only a wholly empty, visible, default-named Playlist row
can receive the drop. Pixel/tick conversion and invented FLP source data are not
used. The public runner owns the shared GUI lock; inner helpers never reacquire it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from collections import Counter
from pathlib import Path

from .audio_files import file_hash, inspect_audio
from .file_transfer import transfer_file
from .native_windows import point_belongs_to, unique_control, windows
from .playlist_file import _channel_details, _decode, edit_playlist
from .project_bundle import _resolve_reference
from .project_lifecycle import _state, _stopped, open_project
from .protocol import atomic_json, transaction_lock
from .screen_ocr import read_region
from .windows_workflows import WindowsFL, _workflow_lock, native


def _int(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def _native(function, *args):
    return native("native.call", {"function": function, "args": list(args)})["value"]


def _false(value):
    return value is False or type(value) is int and value == 0


def _surface(fl):
    """Recognize the observed Playlist form using independently discovered anchors."""
    form = unique_control(fl.handle, "TEventEditForm")
    x, y, width, height = form["bounds"]
    if (form["dpi"] != 96 or not form["enabled"]
            or not form["title"].startswith("Playlist") or width < 800 or height < 300):
        raise RuntimeError("Playlist requires the verified FL26/96 DPI visible layout")
    expected = {
        "header": ("TWPControl", [x + 161, y + 24, 112, 41]),
        "horizontal": ("TQuickScroller", [x + 272, y + 24, width - 292, 21]),
        "vertical": ("TNumBar", [x + width - 21, y + 44, 21, height - 44]),
        "picker": ("TNumBar", [x + 140, y + 79, 16, height - 99]),
    }
    children = windows(form["handle"])
    anchors = {}
    for name, (cls, bounds) in expected.items():
        matches = [w for w in children if w["class"] == cls and w["bounds"] == bounds
                   and w["parent"] == str(form["handle"]) and w["pid"] == form["pid"]
                   and w["dpi"] == 96]
        if len(matches) != 1:
            raise RuntimeError(f"Playlist {name} geometry is outside the verified profile")
        anchors[name] = matches[0]
    return dict(form=form, anchors=anchors,
                header_bounds=[x + 161, y + 65, 112, height - 85],
                grid_left=x + 273)


def _pixels(bounds):
    """Read only the FL track-header strip, including scroll/row-state changes."""
    from PIL import ImageGrab

    x, y, width, height = bounds
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).convert("RGB")
    if image.size != (width, height):
        raise RuntimeError("Playlist header capture dimensions changed")
    return hashlib.sha256(image.tobytes()).hexdigest()


def _snapshot(path):
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    info, _, events, arrangements = _decode(data)
    if not info["version"].startswith("26.1."):
        raise ValueError("Audio import qualification currently requires FL26.1")
    records = [
        [events[a["event_index"]].payload[i:i + 88]
         for i in range(0, len(events[a["event_index"]].payload), 88)]
        for a in arrangements
    ]
    return dict(path=path, sha256=hashlib.sha256(data).hexdigest(), info=info,
                channels=_channel_details(info, events), arrangements=arrangements,
                records=records,
                automation=[e.payload for e in events if e.id == 234],
                automation_links=[e.payload for e in events if e.id == 227],
                plugin_payloads=[e.payload for e in events if e.id == 213],
                nonplaylist=[(e.id, e.payload) for e in events if e.id != 233])


def _placements(saved):
    keys = ("kind", "channel", "pattern", "position_ticks", "length_ticks", "track", "offset_bytes")
    return [Counter(tuple(c[k] for k in keys) for c in a["clips"])
            for a in saved["arrangements"]]


def _content_matches(before, after):
    for key in ("ppq", "tempo", "time_signature", "patterns"):
        if before["info"][key] != after["info"][key]:
            raise RuntimeError(f"Saved {key} changed during audio import")
    if before["automation"] != after["automation"]:
        raise RuntimeError("Existing saved automation curves changed during audio import")
    if before["automation_links"] != after["automation_links"]:
        raise RuntimeError("Existing saved automation links changed during audio import")
    if before["plugin_payloads"] != after["plugin_payloads"]:
        raise RuntimeError("Existing saved plugin payloads changed during audio import")


def _canonical_clip(raw, kind):
    """Mask only the exact normalization fields observed on native FL26 reload."""
    result = bytearray(raw)
    result[32:36] = bytes(4)
    if kind == "pattern":
        result[19] &= 0x7F
    return bytes(result)


def _clip_readback(before, after, *, allow_added=False):
    """Match every musical/opaque byte except two explicitly reported observations.

    Qualified 2026-09-29 native reload replaces u32 at32 with unique values and
    toggles pattern byte19 bit7. Their semantics are not asserted here. Audio
    source offsets, lengths, fades and every other opaque byte remain exact.
    """
    if len(before["records"]) != len(after["records"]):
        raise RuntimeError("Playlist arrangement count changed during import")
    added, changes = [], []
    for ai, (original, current) in enumerate(zip(before["records"], after["records"])):
        pending = list(enumerate(current))
        for ci, raw in enumerate(original):
            clip = before["arrangements"][ai]["clips"][ci]
            key = _canonical_clip(raw, clip["kind"])
            matching = [(index, actual) for index, actual in pending
                        if _canonical_clip(actual, clip["kind"]) == key]
            if not matching:
                raise RuntimeError("Existing clip changed beyond qualified native normalization")
            # Prefer byte-identical pairs when duplicate placements exist.
            index, actual = next((pair for pair in matching if pair[1] == raw), matching[0])
            pending.remove((index, actual))
            if actual != raw:
                changes.append(dict(
                    arrangement=ai, original_clip=ci, saved_clip=index,
                    kind=clip["kind"], channel=clip["channel"],
                    changed_byte_offsets=[i for i in range(88) if raw[i] != actual[i]],
                    u32_at32_before=int.from_bytes(raw[32:36], "little"),
                    u32_at32_after=int.from_bytes(actual[32:36], "little"),
                    byte19_before=raw[19], byte19_after=actual[19],
                ))
        added.extend((ai, after["arrangements"][ai]["clips"][index]) for index, _ in pending)
    if added and not allow_added:
        raise RuntimeError("Reloaded Playlist contains unexpected new clips")
    if any(c["u32_at32_before"] != c["u32_at32_after"] for c in changes):
        values = [int.from_bytes(raw[32:36], "little") for rows in after["records"]
                  for raw in rows]
        if len(set(values)) != len(values):
            raise RuntimeError("Native u32-at32 replacements are not unique per instance")
    return added, changes


def _imported_clip(before, after, asset, expected_audio_sha256):
    """Prove exactly one native audio source/instance appeared, preserving originals."""
    _content_matches(before, after)
    if len(before["arrangements"]) != len(after["arrangements"]):
        raise RuntimeError("Playlist arrangement count changed during import")
    old, new = before["channels"], after["channels"]
    added = set(new) - set(old)
    if len(added) != 1 or set(old) - set(new) or any(new[i] != c for i, c in old.items()):
        raise RuntimeError("Import did not preserve existing saved channel identities")
    channel = new[added.pop()]
    if channel["kind"] != "audio" or channel["type"] != 4:
        raise RuntimeError("New saved channel is not a native Audio Clip")
    source = _resolve_reference(channel["sample_paths"][0], after["path"].parent, [], {})
    if os.path.normcase(str(source)) != os.path.normcase(str(asset)):
        raise RuntimeError("Saved event196 sample path differs from the requested audio")
    if file_hash(source) != expected_audio_sha256:
        raise RuntimeError("Audio source changed during import")
    matches, _ = _clip_readback(before, after, allow_added=True)
    if len(matches) != 1 or matches[0][1]["channel"] != channel["id"]:
        raise RuntimeError("Import did not create exactly one clip for the new audio channel")
    arrangement, clip = matches[0]
    if clip["offset_bytes"] != "000080bf000080bf" or clip["length_ticks"] <= 0:
        raise RuntimeError("Imported Audio Clip is not a full untrimmed native instance")
    return arrangement, clip


def _roundtrip(expected, actual):
    _content_matches(expected, actual)
    if expected["channels"] != actual["channels"]:
        raise RuntimeError("Reloaded audio source/channel identities differ from placed project")
    if _placements(expected) != _placements(actual):
        raise RuntimeError("Reloaded Playlist placements differ from requested arrangement")
    _, clips = _clip_readback(expected, actual)
    before_events, after_events = expected["nonplaylist"], actual["nonplaylist"]
    if [eid for eid, _ in before_events] != [eid for eid, _ in after_events]:
        raise RuntimeError("Native reload changed non-Playlist event layout")
    events = []
    for index, ((eid, before), (_, after)) in enumerate(zip(before_events, after_events)):
        if before == after:
            continue
        allowed = (eid == 143 and before == bytes.fromhex("0a000000")
                   and after == bytes.fromhex("02000000")) or (
            eid == 237 and len(before) == len(after) == 16 and before[:8] == after[:8]
        )
        if not allowed:
            raise RuntimeError(f"Native reload changed unqualified event{eid} payload")
        events.append(dict(nonplaylist_index=index, event_id=eid,
                           changed_byte_offsets=[i for i in range(len(before))
                                                 if before[i] != after[i]],
                           before_hex=before.hex(), after_hex=after.hex()))
    return dict(clips=clips, nonplaylist=events,
                limitation="Observed normalization fields; their opaque semantics are unproven.")


async def _empty_target(fl, saved):
    surface = _surface(fl)
    bounds = surface["header_bounds"]
    header_hash = _pixels(bounds)
    observed = await read_region(fl, bounds, scale=3)
    if _surface(fl) != surface or _pixels(bounds) != header_hash:
        raise RuntimeError("Playlist rows moved while inspecting the drop target")
    occupied = {c["track"] for a in saved["arrangements"] for c in a["clips"]}
    rows = {}
    for line in observed["lines"]:
        match = re.fullmatch(r"Track\s+(\d+)", line["text"].strip())
        if match:
            track = int(match[1])
            rows.setdefault(track, []).append(line["bounds"])
    for track, labels in sorted(rows.items()):
        if not 1 <= track <= 500 or track in occupied or len(labels) != 1:
            continue
        label_x, label_y, label_w, label_h = labels[0]
        if (label_x < bounds[0] or label_x + label_w > bounds[0] + bounds[2]
                or label_y < bounds[1] or label_y + label_h > bounds[1] + bounds[3]
                or label_h <= 0):
            continue
        if (_native("playlist.getTrackName", track) != f"Track {track}"
                or not _false(_native("playlist.isTrackMuted", track))):
            continue
        point = (surface["grid_left"] + 16, round(label_y + label_h / 2))
        if point_belongs_to(point, surface["form"]["handle"]):
            return dict(surface=surface, track=track, point=point, header_sha256=header_hash)
    raise RuntimeError("No visible unmuted default-named empty Playlist row was verified")


def _channel_values():
    # Selection follows FL's newly created channel and is not project content.
    return [{k: v for k, v in c.items() if k != "is_selected"}
            for c in native("channels.getAll")["channels"]]


def _context_except_channels(state):
    return {k: v for k, v in state["context"].items()
            if k not in ("channels", "channel_generation")}


async def audio_clip_add(fl, path, expected_audio_sha256, expected_revision, directory,
                         position_ticks, track, apply=False):
    """Import one WAV once, then verify an exact saved Playlist placement."""
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    position_ticks = _int(position_ticks, 0, 2**31 - 1, "position_ticks")
    track = _int(track, 1, 500, "track")
    asset = Path(path)
    if not asset.is_absolute() or asset.suffix.lower() != ".wav":
        raise ValueError("First audio-clip import requires an absolute WAV path")
    asset = asset.resolve(strict=True)
    if not isinstance(expected_audio_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_audio_sha256
    ):
        raise ValueError("expected_audio_sha256 must be a lowercase SHA-256")
    audio = inspect_audio(asset)
    if audio["sha256"] != expected_audio_sha256:
        raise ValueError("Audio hash changed; inspect the audio again")
    directory = Path(directory)
    if not directory.is_absolute() or directory.exists():
        raise ValueError("Import directory must be an absolute NEW directory")
    directory = directory.resolve()
    before = _state()
    _stopped(before)
    if before["revision"] != expected_revision:
        raise ValueError("Project revision changed; inspect production state again")
    await fl._project_guard()
    surface = _surface(fl)
    report = dict(
        schema="fl-audio-clip-import-v1", operation_id=uuid.uuid4().hex,
        success=not apply, verified=False, status="preflighted", source=str(asset),
        audio=audio, directory=str(directory), before=before,
        requested=dict(position_ticks=position_ticks, track=track),
        playlist_surface=surface,
        limitations=[
            "WAV import and FL26.1/96 DPI Playlist profile only.",
            "Requires a visible default-named unmuted row empty in every saved arrangement.",
            "Native import creates source data; exact placement uses a new FLP and reload.",
            "Source data and placements are verified; audible output is not measured here.",
            "Host save may normalize captured clip bookkeeping fields; exact changes are reported.",
            "Cooperative GUI lock does not prevent user or unrelated-client edits.",
        ],
    )
    if not apply:
        return report
    directory.mkdir()
    receipt = directory / "audio-clip-import.json"

    def stage(status, **fields):
        report.update(fields, status=status)
        atomic_json(receipt, report)

    stage("saving_recovery", receipt_file=str(receipt))
    try:
        recheck = _state()
        _stopped(recheck)
        if recheck["revision"] != expected_revision or file_hash(asset) != expected_audio_sha256:
            raise RuntimeError("Project/audio changed before recovery save; no import dispatched")
        report["recovery"] = await fl.save_project_as(directory / "before.flp")
        checkpoint = _state()
        _stopped(checkpoint)
        if (checkpoint["context"] != before["context"] or checkpoint["dirty"]
                or checkpoint["values"] != before["values"]):
            raise RuntimeError("Project changed during recovery save; no import dispatched")
        baseline = _snapshot(directory / "before.flp")
        prior_channels = _channel_values()
        await fl._project_focus()
        target = await _empty_target(fl, baseline)
        stage("ready_to_import", drop_target=target)

        def guard_drop():
            current = _state()
            _stopped(current)
            if current["revision"] != checkpoint["revision"] or current["dirty"]:
                raise RuntimeError("Project changed before drop; no import dispatched")
            if _channel_values() != prior_channels or _surface(fl) != target["surface"]:
                raise RuntimeError("Channels or Playlist geometry changed before drop")
            if _pixels(target["surface"]["header_bounds"]) != target["header_sha256"]:
                raise RuntimeError("Playlist row view changed before drop")
            if file_hash(asset) != expected_audio_sha256:
                raise RuntimeError("Audio source changed before drop")

        guard_drop()
        stage("importing")  # Durable uncertain state before the only OLE dispatch.
        delivery = await transfer_file(fl, asset, target["point"],
                                       target["surface"]["form"]["handle"], guard_drop)
        stage("observing_import", delivery=delivery)
        if delivery.get("delivery_confirmed") is not True:
            raise RuntimeError("OLE import delivery unconfirmed; inspect before retrying")
        deadline = time.monotonic() + 15
        while True:
            current = _state()
            _stopped(current)
            if (_context_except_channels(current) != _context_except_channels(checkpoint)
                    or current["values"] != checkpoint["values"]):
                raise RuntimeError("Project context changed during native import")
            after_channels = _channel_values()
            if after_channels == prior_channels:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Native channel creation unverified; do not repeat the drop")
                await asyncio.sleep(0.1)
                continue
            if (len(after_channels) != len(prior_channels) + 1
                    or after_channels[:-1] != prior_channels):
                raise RuntimeError("Native import changed existing channels; inspect recovery")
            added = after_channels[-1]
            if _native("channels.getChannelType", added["index"], True) != 4:
                raise RuntimeError("New native channel is not Audio Clip type4")
            break
        stage("saving_import", native_channel=added)
        report["import_save"] = await fl.save_project_as(directory / "imported.flp")
        import_state = _state()
        _stopped(import_state)
        if (import_state["context"] != current["context"] or import_state["dirty"]
                or import_state["values"] != current["values"]
                or _channel_values() != after_channels):
            raise RuntimeError("Project changed while saving imported audio; no reload dispatched")
        imported = _snapshot(directory / "imported.flp")
        arrangement, clip = _imported_clip(baseline, imported, asset, expected_audio_sha256)
        report["import_clip_normalizations"] = _clip_readback(
            baseline, imported, allow_added=True,
        )[1]
        if clip["channel_name"] != added["name"]:
            raise RuntimeError("Saved and native new Audio Clip names differ")
        stage("placing", imported_clip=clip)
        placed_path = directory / "placed.flp"
        edited = edit_playlist(
            imported["path"], placed_path, imported["sha256"], [dict(
                operation="update",
                event_index=imported["arrangements"][arrangement]["event_index"],
                clip=clip["clip"], expected_clip_sha256=clip["sha256"],
                expected_channel_name=clip["channel_name"],
                position_ticks=position_ticks, track=track,
            )], dry_run=False,
        )
        stage("opening_placed", file_edit=edited)
        current = _state()
        _stopped(current)
        if (current["revision"] != import_state["revision"] or current["dirty"]
                or _channel_values() != after_channels):
            raise RuntimeError("Project changed before placed-project reload; open not dispatched")
        opened = await open_project(
            fl, placed_path, edited["output_sha256"], current["revision"],
            directory / "import-recovery.flp", directory / "open-placed.json", apply=True,
        )
        report["open"] = opened
        if opened.get("success") is not True or opened.get("status") != "verified":
            raise RuntimeError("Placed project reload unverified; inspect open receipt")
        reloaded = _state()
        _stopped(reloaded)
        stage("verifying_reload")
        report["final_save"] = await fl.save_project_as(directory / "verified.flp")
        final = _snapshot(directory / "verified.flp")
        normalizations = _roundtrip(_snapshot(placed_path), final)
        if file_hash(asset) != expected_audio_sha256:
            raise RuntimeError("Audio source changed before final verification")
        after = _state()
        _stopped(after)
        if (after["context"] != reloaded["context"] or after["dirty"]
                or after["values"] != reloaded["values"]):
            raise RuntimeError("Project changed during final save; active project is uncertain")
        final_matches = [c for a in final["arrangements"] for c in a["clips"]
                         if c["channel"] == clip["channel"]]
        if len(final_matches) != 1:
            raise RuntimeError("Reloaded source does not have exactly one verified placement")
        stage("verified", success=True, verified=True, after=after,
              success_scope="audio_clip_placement", native_opaque_normalizations=normalizations,
              active_project=str(final["path"]), output_sha256=final["sha256"],
              clip=final_matches[0], verification=dict(
                  native_audio_channel=True, exact_source_path_and_hash=True,
                  existing_clips_preserved=True, placement_survived_reload=True,
                  original_project_retained=str(directory / "before.flp"),
              ))
    except Exception as exc:
        stage("needs_inspection", success=False, verified=False, error=str(exc),
              recovery_instruction="Inspect receipt and before.flp; import/open is not retried.")
    return report


async def run_audio_clip_add(**kwargs):
    from .midi_connection import get_connection

    async with _workflow_lock:
        with transaction_lock(get_connection()._hardware_dir / "mcp-gui.lock", 5):
            async with WindowsFL() as fl:
                return await audio_clip_add(fl, **kwargs)
