"""Exact FL26 clip placement edits in a new project, preserving opaque state."""

import hashlib
import math
import os
import struct
from pathlib import Path

from fl_studio_mcp.utils.project_bundle import _inspect, _parse, _vlq

STRIDE = 88
# Native FL26.1.5 full, untrimmed pattern clip, independently opened/saved in FL.
# Offsets are sentinels: FL derives duration from the pattern when loading.
PATTERN_CLIP = bytes.fromhex(
    "000000000050015000030000f30100007800400040648080ffffffffffffffff01000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000f03f00000000ffffffff0000000000000000"
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def _channel_details(info, events):
    """Identify saved channel clips without trusting PyFLP's old enum labels.

    Actual FL26 files use type 4 plus a sample-path event for Audio Clips, type
    5 plus event234 for automation. PyFLP 2.2.1 labels type4 Instrument and does
    not understand the FL26 88-byte extension; it is not a serialization oracle.
    """
    channels = {c["id"]: dict(c, type=None, automation_data=False, sample_paths=[])
                for c in info["channels"]}
    current = None
    for event in events:
        if event.id == 64:
            current = int.from_bytes(event.payload, "little")
        elif event.id in (65, 99, 236, 147):
            current = None
        elif current in channels:
            if event.id == 21:
                value = event.payload[0]
                previous = channels[current]["type"]
                if previous is not None and previous != value:
                    raise ValueError("Conflicting saved channel types")
                channels[current]["type"] = value
            elif event.id == 234:
                channels[current]["automation_data"] = True
    for reference in info["sample_references"]:
        if reference["channel"] in channels:
            channels[reference["channel"]]["sample_paths"].append(reference["path"])
    for channel in channels.values():
        channel["kind"] = (
            "audio" if channel["type"] == 4 and len(channel["sample_paths"]) == 1
            else "automation" if channel["type"] == 5 and channel["automation_data"]
            else "unknown_channel"
        )
    return channels


def _decode(data):
    info = _inspect(data)
    if info["version"].split(".")[0] != "26":
        raise ValueError("Playlist writing is restricted to FL26's verified 88-byte layout")
    chunks, events, _ = _parse(data)
    channels = _channel_details(info, events)
    arrangements = []
    for event_index, event in enumerate(events):
        if event.id != 233:
            continue
        clips = []
        for index, offset in enumerate(range(0, len(event.payload), STRIDE)):
            raw = event.payload[offset : offset + STRIDE]
            start, base, item, length, track = struct.unpack_from("<IHHIH", raw)
            pattern = item - base if item > base else None
            channel = channels.get(item) if pattern is None else None
            start_offset, end_offset = struct.unpack_from("<ff", raw, 24)
            clips.append(
                dict(
                    clip=index,
                    sha256=_sha(raw),
                    position_ticks=start,
                    length_ticks=length,
                    track=500 - track,
                    pattern=pattern,
                    pattern_base=base,
                    kind="pattern" if pattern is not None else channel["kind"] if channel
                    else "unknown_channel",
                    channel=item if pattern is None else None,
                    channel_name=channel["name"] if channel else None,
                    channel_type=channel["type"] if channel else None,
                    offset_bytes=raw[24:32].hex(),
                    source_offsets=dict(
                        start=start_offset if math.isfinite(start_offset) else None,
                        end=end_offset if math.isfinite(end_offset) else None,
                        units="unverified; raw bytes preserved",
                    ),
                )
            )
        arrangements.append(dict(event_index=event_index, clips=clips))
    return info, chunks, events, arrangements


def inspect_playlist(source):
    source = Path(source).resolve(strict=True)
    data = source.read_bytes()
    info, _, events, arrangements = _decode(data)
    return dict(
        source=str(source),
        sha256=_sha(data),
        version=info["version"],
        ppq=info["ppq"],
        time_signature=info["time_signature"],
        patterns=[dict(id=p["id"], name=p["name"]) for p in info["patterns"]],
        channels=list(_channel_details(info, events).values()),
        arrangements=arrangements,
        saved_file_only=True,
        limitations=[
            "Full pattern clips can be added using the FL26.1 native template.",
            "Existing pattern/audio/automation clips can move, clone or delete; "
            "their source identities, length, offsets, fades and opaque bytes are preserved.",
            "Added clips require actual full-pattern length; verify after FL reload/save.",
            "First audio/automation placement requires an existing clip template; "
            "creating channels, trims/splits, stretching and new arrangements are unsupported.",
            "Positions and lengths are integer ticks; Playlist tracks are one-based.",
            "Each event_index identifies one exact saved Playlist event.",
        ],
    )


def edit_playlist(source, output, expected_sha256, edits, dry_run=True):
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be boolean")
    source, output = Path(source).resolve(strict=True), Path(output)
    if not output.is_absolute() or output.suffix.lower() != ".flp":
        raise ValueError("Output must be an absolute new .flp path")
    output = output.resolve()
    if output == source or output.exists():
        raise ValueError("Output must not exist; source is never overwritten")
    data = source.read_bytes()
    if _sha(data) != expected_sha256:
        raise ValueError("Source hash changed; inspect the saved Playlist again")
    info, chunks, events, arrangements = _decode(data)
    if not isinstance(edits, list) or not 1 <= len(edits) <= 4096:
        raise ValueError("Provide 1..4096 Playlist edits")
    patterns = {p["id"]: p["name"] for p in info["patterns"]}
    lookup = {a["event_index"]: a for a in arrangements}
    modified, appended, seen = {}, {}, set()
    changes = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) - {
            "operation",
            "event_index",
            "clip",
            "expected_clip_sha256",
            "position_ticks",
            "length_ticks",
            "track",
            "pattern",
            "expected_pattern_name",
            "expected_length_ticks",
            "expected_channel_name",
        }:
            raise ValueError("Unknown Playlist edit fields")
        operation = edit.get("operation")
        if operation not in ("add", "clone", "update", "delete"):
            raise ValueError("operation must be add, clone, update or delete")
        event_index = _integer(edit.get("event_index"), 0, len(events) - 1, "event_index")
        if event_index not in lookup:
            raise ValueError("event_index does not identify a Playlist event")
        arrangement = lookup[event_index]
        if operation == "add":
            required = {
                "operation",
                "event_index",
                "pattern",
                "expected_pattern_name",
                "expected_length_ticks",
                "position_ticks",
                "track",
            }
            if set(edit) != required or not info["version"].startswith("26.1."):
                raise ValueError(
                    "Add requires FL26.1 and exact pattern name/full length, "
                    "position, track and event_index; no clip identity fields"
                )
            pattern = _integer(edit["pattern"], 1, 65535 - 20480, "pattern")
            if pattern not in patterns or edit["expected_pattern_name"] != patterns[pattern]:
                raise ValueError("Saved pattern index/name does not match")
            length = _integer(edit["expected_length_ticks"], 1, 2**31 - 1, "expected_length_ticks")
            start = _integer(edit["position_ticks"], 0, 2**31 - 1 - length, "position_ticks")
            track = _integer(edit["track"], 1, 500, "track")
            raw = bytearray(PATTERN_CLIP)
            if len(raw) != STRIDE:
                raise RuntimeError("Native pattern template has invalid length")
            struct.pack_into("<IHHIH", raw, 0, start, 20480, 20480 + pattern, length, 500 - track)
            appended.setdefault(event_index, []).append(bytes(raw))
            changes.append(
                dict(
                    operation="add",
                    requested=edit,
                    duration="Full pattern; expected length is a cache, not resizing",
                )
            )
            continue
        if "expected_length_ticks" in edit:
            raise ValueError("expected_length_ticks is only valid for add")
        index = _integer(edit.get("clip"), 0, len(arrangement["clips"]) - 1, "clip")
        before = arrangement["clips"][index]
        if edit.get("expected_clip_sha256") != before["sha256"]:
            raise ValueError("Clip identity changed")
        if before["kind"] not in ("pattern", "audio", "automation"):
            raise ValueError(
                "Unsupported channel clip; require verified saved audio/automation data"
            )
        if "expected_channel_name" in edit:
            if (before["channel"] is None
                    or edit["expected_channel_name"] != before["channel_name"]):
                raise ValueError("Saved channel name does not match the clip target")
        if before["kind"] != "pattern" and ("pattern" in edit or "expected_pattern_name" in edit):
            raise ValueError("Pattern fields cannot substitute an audio/automation clip target")
        key = (event_index, index)
        if operation != "clone":
            if key in seen:
                raise ValueError("A source clip may be updated/deleted only once")
            seen.add(key)
        fields = set(edit) & {"position_ticks", "length_ticks", "track", "pattern"}
        if operation == "delete":
            if fields or "expected_pattern_name" in edit:
                raise ValueError("Delete accepts only clip identity fields")
            modified[key] = None
        else:
            if not fields:
                raise ValueError("Provide at least one changed clip field")
            if operation == "clone" and "position_ticks" not in edit:
                raise ValueError("Cloning requires an explicit destination position_ticks")
            raw = bytearray(events[event_index].payload[index * STRIDE : (index + 1) * STRIDE])
            start = _integer(
                edit.get("position_ticks", before["position_ticks"]), 0, 2**31 - 1, "position_ticks"
            )
            length = _integer(
                edit.get("length_ticks", before["length_ticks"]), 1, 2**31 - 1, "length_ticks"
            )
            # FL recomputes sentinel pattern lengths when loading. Editing the
            # cached length alone passes byte tests but does not survive FL.
            if length != before["length_ticks"]:
                raise ValueError("Clip resizing is not supported; repeat existing clips instead")
            if start + length > 2**31 - 1:
                raise ValueError("Clip end exceeds supported tick range")
            track = _integer(edit.get("track", before["track"]), 1, 500, "track")
            struct.pack_into("<I", raw, 0, start)
            struct.pack_into("<I", raw, 8, length)
            struct.pack_into("<H", raw, 12, 500 - track)
            if "pattern" in edit:
                pattern = _integer(edit["pattern"], 1, 65535 - before["pattern_base"], "pattern")
                if (
                    pattern not in patterns
                    or edit.get("expected_pattern_name") != patterns[pattern]
                ):
                    raise ValueError("Requested saved pattern ID/name does not match")
                if pattern != before["pattern"]:
                    raise ValueError(
                        "Use a template clip of the desired pattern; substitution "
                        "can change the reloaded clip length"
                    )
                if raw[24:32] != b"\xff" * 8:
                    raise ValueError("Pattern substitution needs an untrimmed sentinel template")
                struct.pack_into("<H", raw, 6, before["pattern_base"] + pattern)
            elif "expected_pattern_name" in edit:
                raise ValueError("expected_pattern_name needs an explicit pattern")
            if operation == "clone":
                appended.setdefault(event_index, []).append(bytes(raw))
            else:
                modified[key] = bytes(raw)
        changes.append(dict(operation=operation, source_clip=before, requested=edit))
    replacements = {}
    for arrangement in arrangements:
        event_index = arrangement["event_index"]
        if not any(e == event_index for e, _ in modified) and event_index not in appended:
            continue
        original = events[event_index].payload
        records = [
            modified.get((event_index, i), original[i * STRIDE : (i + 1) * STRIDE])
            for i in range(len(arrangement["clips"]))
        ]
        payload = b"".join(r for r in records if r is not None) + b"".join(
            appended.get(event_index, [])
        )
        replacements[event_index] = bytes([233]) + _vlq(len(payload)) + payload
    body = b"".join(replacements.get(i, e.encoded) for i, e in enumerate(events))
    result = b"".join(
        name
        + struct.pack("<I", len(body if name == b"FLdt" else raw))
        + (body if name == b"FLdt" else raw)
        for name, raw in chunks
    )
    _, parsed, _ = _parse(result)
    if len(parsed) != len(events) or any(
        after.encoded != before.encoded
        for i, (before, after) in enumerate(zip(events, parsed))
        if i not in replacements
    ):
        raise RuntimeError("Untouched FLP event preservation failed")
    after_info, _, _, after_arrangements = _decode(result)
    if {k: v for k, v in info.items() if k != "clips"} != {
        k: v for k, v in after_info.items() if k != "clips"
    }:
        raise RuntimeError("Non-Playlist project state changed")
    report = dict(
        success=True,
        dry_run=dry_run,
        source=str(source),
        source_sha256=_sha(data),
        output=str(output),
        output_sha256=_sha(result),
        ppq=info["ppq"],
        changes=changes,
        arrangements=after_arrangements,
        loaded_in_fl=False,
        unchanged_events_preserved=True,
    )
    if not dry_run:
        if source.read_bytes() != data:
            raise ValueError("Source changed before publication")
        with output.open("xb") as stream:
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        if output.read_bytes() != result:
            raise RuntimeError("Output byte verification failed")
    return report
