"""Exact automation edits on a NEW saved FLP, never on the live project file.

FL stores automation times as delta beats, not ticks. Mode belongs to the
incoming segment. Opaque events, headers, flags and point tails stay intact.
"""

from __future__ import annotations

import hashlib
import math
import struct
from bisect import bisect_left, bisect_right
from pathlib import Path


def _events(data):
    if len(data) < 22 or data[:4] != b"FLhd":
        raise ValueError("Not an FLP")
    header_size = struct.unpack_from("<I", data, 4)[0]
    offset = 8 + header_size
    if header_size != 6 or data[offset : offset + 4] != b"FLdt":
        raise ValueError("Unsupported FLP chunk layout")
    size = struct.unpack_from("<I", data, offset + 4)[0]
    start, end = offset + 8, offset + 8 + size
    if end != len(data):
        raise ValueError("Truncated FLdt or unsupported trailing chunks")
    ppq = struct.unpack_from("<H", data, 12)[0]
    if ppq == 0:
        raise ValueError("Invalid PPQ")
    from fl_studio_mcp.utils.project_bundle import _events as parse_events

    events = [(e.id, e.payload, e.encoded) for e in parse_events(data[start:end])]
    return ppq, start, events


def _encode(eid, payload):
    n, length = len(payload), bytearray()
    while n >= 128:
        length.append((n & 127) | 128)
        n >>= 7
    length.append(n)
    return bytes([eid]) + bytes(length) + payload


def _points(payload):
    if len(payload) < 21:
        raise ValueError("Truncated automation header")
    count = struct.unpack_from("<I", payload, 17)[0]
    if not 1 <= count <= 100000 or 21 + 24 * count > len(payload):
        raise ValueError("Invalid automation point count")
    points, beat = [], 0.0
    for i in range(count):
        delta, value, tension, mode = struct.unpack_from("<ddfI", payload, 21 + 24 * i)
        if not all(math.isfinite(x) for x in (delta, value, tension)) or delta < 0:
            raise ValueError("Invalid existing automation point")
        beat += delta
        points.append(dict(beat=beat, value=value, tension=tension, mode=mode))
    return points, payload[21 + 24 * count :]


def inspect_automation(source):
    data = Path(source).read_bytes()
    ppq, _, events = _events(data)
    channel, name, clips = None, "", []
    for eid, raw, _ in events:
        if eid == 64:
            channel, name = int.from_bytes(raw, "little"), ""
        elif eid == 203:
            name = raw.decode("utf-16le").rstrip("\0")
        elif eid == 234:
            points, _ = _points(raw)
            clips.append(
                dict(
                    channel=channel,
                    name=name,
                    points=[
                        dict(
                            tick=p["beat"] * ppq,
                            value=p["value"],
                            tension=p["tension"],
                            mode=p["mode"] & 255,
                        )
                        for p in points
                    ],
                )
            )
    return dict(success=True, sha256=hashlib.sha256(data).hexdigest(), ppq=ppq, automation=clips)


def edit_automation(source, output, expected_sha256, edits, dry_run=True):
    """Upsert/replace exact local ticks on existing identity-checked automation.

    Each edit: channel, expected_name, points [{tick,value,mode?,tension?}],
    operation ('upsert' or 'replace'). mode is 'linear' (FL Single curve,
    tension zero) or 'hold'. Values are normalized 0..1, NOT dB. Existing
    fractional-tick and duplicate positions are preserved on upsert. A target
    matching multiple existing points is refused. This does not load FL.
    """
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be boolean")
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if source == output or output.exists():
        raise ValueError("Output must be a new file, separate from the source")
    if output.suffix.lower() != ".flp":
        raise ValueError("Output must end in .flp")
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise ValueError("Source revision changed; inspect again")
    ppq, start, events = _events(data)
    if not isinstance(edits, list) or not 1 <= len(edits) <= 128:
        raise ValueError("Expected 1..128 automation edits")
    requested = {}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) - {
            "channel",
            "expected_name",
            "points",
            "operation",
        }:
            raise ValueError("Unknown automation edit fields")
        channel = edit["channel"]
        if type(channel) is not int or channel < 0 or channel in requested:
            raise ValueError("Invalid or duplicate channel")
        if not isinstance(edit.get("expected_name"), str) or not edit["expected_name"]:
            raise ValueError("Exact automation channel name required")
        if edit.get("operation", "upsert") not in ("upsert", "replace"):
            raise ValueError("Operation must be upsert or replace")
        points = edit["points"]
        if not isinstance(points, list) or not 1 <= len(points) <= 10000:
            raise ValueError("Expected 1..10000 points")
        ticks = set()
        for point in points:
            if not isinstance(point, dict) or set(point) - {"tick", "value", "mode", "tension"}:
                raise ValueError("Unknown automation point fields")
            tick = point["tick"]
            if type(tick) is not int or not 0 <= tick <= 2**31 or tick in ticks:
                raise ValueError("Point tick must be a unique nonnegative integer")
            ticks.add(tick)
            for key, low, high in [("value", 0, 1), ("tension", -1, 1)]:
                v = point.get(key, 0)
                if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
                    raise ValueError("Invalid point " + key)
            if "value" not in point or point.get("mode", "linear") not in ("linear", "hold"):
                raise ValueError("Each point requires value and a supported mode")
            if point.get("mode", "linear") == "linear" and point.get("tension", 0) != 0:
                raise ValueError("Linear segments require zero tension")
        requested[channel] = edit
    channel, name, changed, result = None, "", [], []
    for eid, raw, original in events:
        if eid == 64:
            channel, name = int.from_bytes(raw, "little"), ""
        elif eid == 203:
            name = raw.decode("utf-16le").rstrip("\0")
        if eid == 234 and channel in requested:
            if channel in changed or name != requested[channel]["expected_name"]:
                raise ValueError("Ambiguous channel or automation identity mismatch")
            edit = requested[channel]
            previous, tail = _points(raw)
            merged = list(previous) if edit.get("operation", "upsert") == "upsert" else []
            # Keep coincident points in their original order. Match against the
            # original sorted ticks, avoiding both dictionary deduplication and
            # exact-float misses after FL's delta-beat accumulation.
            original_ticks = [p["beat"] * ppq for p in merged]
            for point in edit["points"]:
                tick = point["tick"]
                tolerance = max(1e-8, 8 * math.ulp(float(tick)))
                left = bisect_left(original_ticks, tick - tolerance)
                right = bisect_right(original_ticks, tick + tolerance)
                if right - left > 1:
                    raise ValueError(f"Ambiguous automation point at tick {tick}")
                existing = right - left == 1
                old_mode = merged[left]["mode"] if existing else 0
                mode = (old_mode & ~255) | (2 if point.get("mode") == "hold" else 0)
                updated = dict(
                    beat=tick / ppq,
                    value=point["value"],
                    tension=point.get("tension", 0),
                    mode=mode,
                )
                if existing:
                    merged[left] = updated
                else:
                    merged.append(updated)
            ordered = sorted(merged, key=lambda p: p["beat"])
            if ordered[0]["beat"] != 0:
                raise ValueError("Automation must retain a point at tick zero")
            payload = raw[:17] + struct.pack("<I", len(ordered))
            last = 0.0
            for point in ordered:
                payload += struct.pack(
                    "<ddfI", point["beat"] - last, point["value"], point["tension"], point["mode"]
                )
                last = point["beat"]
            result.append(_encode(eid, payload + tail))
            changed.append(channel)
        else:
            result.append(original)
    if set(changed) != set(requested):
        raise ValueError("Requested automation channel not found")
    body = b"".join(result)
    output_data = data[: start - 4] + struct.pack("<I", len(body)) + body
    _events(output_data)
    report = dict(
        success=True,
        dry_run=dry_run,
        source_sha256=digest,
        output_sha256=hashlib.sha256(output_data).hexdigest(),
        channels=changed,
        output=str(output),
        ppq=ppq,
        loaded_in_fl=False,
    )
    if not dry_run:
        if source.read_bytes() != data:
            raise ValueError("Source changed before write")
        with output.open("xb") as stream:
            stream.write(output_data)
        if hashlib.sha256(output.read_bytes()).hexdigest() != report["output_sha256"]:
            raise RuntimeError("Output verification failed")
    return report
