"""Small, explicit FL26 score codec for the background OLE note backend."""

import math
import struct

from fl_studio_mcp.utils.project_bundle import _vlq


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def _number(value, low, high, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{label} must be finite in {low}..{high}")
    return value


def encode_notes(notes, ppq):
    """Encode only qualified fields; never silently drop unsupported properties."""
    _integer(ppq, 1, 32767, "PPQ")
    if not isinstance(notes, list) or not 1 <= len(notes) <= 16384:
        raise ValueError("Provide 1..16384 notes")
    result = []
    allowed = {"midi", "time", "duration", "time_ticks", "length_ticks", "velocity",
               "pan", "release", "color", "slide"}
    for note in notes:
        if not isinstance(note, dict) or set(note) - allowed:
            raise ValueError("Unsupported note fields; use basic notes, pan, release, color, slide")
        if "time_ticks" in note and "time" in note or "length_ticks" in note and "duration" in note:
            raise ValueError("Use either ticks or quarter notes for each time field")
        start = (note["time_ticks"] if "time_ticks" in note else
                 round(_number(note.get("time", 0), 0, 0x7fffffff / ppq, "time") * ppq))
        length = (note["length_ticks"] if "length_ticks" in note else
                  round(_number(note.get("duration", 1), 0, 0x7fffffff / ppq, "duration") * ppq))
        _integer(start, 0, 0x7fffffff, "time_ticks")
        _integer(length, 1, 0x7fffffff - start, "length_ticks")
        key = _integer(note.get("midi"), 0, 127, "midi")
        color = _integer(note.get("color", 0), 0, 15, "color")
        slide = note.get("slide", False)
        if type(slide) is not bool:
            raise ValueError("slide must be boolean")
        volume = round(_number(note.get("velocity", .8), 0, 1, "velocity") * 128)
        pan = round(_number(note.get("pan", .5), 0, 1, "pan") * 128)
        release = round(_number(note.get("release", .5), 0, 1, "release") * 128)
        result.append(struct.pack("<IHHIHH8B", start, 8 if slide else 0, 0, length,
                                  key, 0, 120, 0, release, color, pan, volume, 128, 128))
    return b"".join(result)


def score_file(raw_notes, ppq, version):
    if not version.startswith("26.") or len(raw_notes) % 24:
        raise ValueError("Background score loading is qualified only for FL26 24-byte notes")
    version_bytes = version.encode("ascii") + b"\0"
    body = (b"\xc7" + _vlq(len(version_bytes)) + version_bytes + b"\x41\0\0"
            + b"\xe0" + _vlq(len(raw_notes)) + raw_notes)
    return (b"FLhd" + struct.pack("<IHHH", 6, 0x10, 1, ppq)
            + b"FLdt" + struct.pack("<I", len(body)) + body)


def canonical_note(note, *, target=False):
    """FL marks imported notes selected in color bit 7; that is editor state."""
    result = dict(note)
    result["midi_channel"] &= 0x7f
    if target:
        result.pop("channel", None)
    return tuple(sorted(result.items()))
