"""Pure note contract shared with FL's embedded Python (no host dependencies)."""

import hashlib
import json
import math

PROTOCOL = 3
FLOAT_FIELDS = ("velocity", "pan", "release", "fcut", "fres")
BOOL_FIELDS = ("slide", "porta", "selected", "muted")
INT_FIELDS = {
    "color": (0, 15),
    "pitchofs": (-120, 120),
    "group": (0, 2147483647),
    "repeats": (0, 14),
}
NOTE_FIELDS = ("number", "time", "length") + FLOAT_FIELDS + BOOL_FIELDS + tuple(INT_FIELDS)


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} must be numeric")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} outside {low}..{high}")
    return value


def ticks(note, ppq, length=False):
    key, beat_key = ("length_ticks", "duration") if length else ("time_ticks", "time")
    if key in note:
        return integer(note[key], 1 if length else 0, 4096 * ppq, key)
    value = number(note.get(beat_key, None if length else 0), 0, 4096, beat_key)
    if length and value <= 0:
        raise ValueError("Duration must be positive")
    return max(1 if length else 0, round(value * ppq))


def normalize_note(note, ppq, delete=False):
    if not isinstance(note, dict):
        raise ValueError("Note must be an object")
    allowed = {"midi", "time", "time_ticks"}
    if not delete:
        allowed |= {"duration", "length_ticks"} | set(FLOAT_FIELDS + BOOL_FIELDS) | set(INT_FIELDS)
    if set(note) - allowed:
        raise ValueError("Unsupported note fields: " + str(sorted(set(note) - allowed)))
    result = {"midi": integer(note.get("midi"), 0, 127, "midi"), "time_ticks": ticks(note, ppq)}
    if delete:
        return result
    result["length_ticks"] = ticks(note, ppq, True)
    result["velocity"] = number(note.get("velocity", 0.8), 0, 1, "velocity")
    for key in FLOAT_FIELDS[1:]:
        if key in note:
            result[key] = number(note[key], 0, 1, key)
    for key in BOOL_FIELDS:
        if key in note:
            if type(note[key]) is not bool:
                raise ValueError(key + " must be boolean")
            result[key] = note[key]
    for key, bounds in INT_FIELDS.items():
        if key in note:
            result[key] = integer(note[key], *bounds, key)
    return result


def normalize_requests(requests, ppq):
    integer(ppq, 1, 96000, "PPQ")
    if not isinstance(requests, list) or len(requests) > 256:
        raise ValueError("Requests must be a list of at most 256 actions")
    result = []
    count = 0
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("Action must be an object")
        action = request.get("action")
        if action == "clear":
            if set(request) != {"action"}:
                raise ValueError("Unknown clear fields")
            result.append({"action": "clear"})
            continue
        if action not in ("add_notes", "delete_notes", "add_chord"):
            raise ValueError("Unknown note action: " + str(action))
        allowed = {"action", "notes"} | ({"time", "duration"} if action == "add_chord" else set())
        if set(request) - allowed:
            raise ValueError("Unknown action fields")
        notes = request.get("notes")
        if not isinstance(notes, list) or not notes:
            raise ValueError("Action requires a nonempty notes list")
        count += len(notes)
        if count > 65536:
            raise ValueError("Too many notes in one transaction")
        converted = []
        for note in notes:
            if action == "add_chord":
                if not isinstance(note, dict):
                    raise ValueError("Note must be an object")
                note = dict(note)
                note["time"] = number(request.get("time", 0), 0, 4096, "chord time") + number(
                    note.pop("offset", 0), 0, 4096, "offset"
                )
                note.setdefault("duration", request.get("duration", 1))
            converted.append(normalize_note(note, ppq, action == "delete_notes"))
        result.append(
            {"action": "add_notes" if action == "add_chord" else action, "notes": converted}
        )
    return result


def note_data(note):
    return {key: getattr(note, key) for key in NOTE_FIELDS if hasattr(note, key)}


def canonical(notes):
    # FL commits note controls on discrete grids after the script callback.
    rows = [
        {
            k: round(v * (255 if k in ("fcut", "fres") else 128)) if k in FLOAT_FIELDS else v
            for k, v in n.items()
        }
        for n in notes
    ]
    return sorted(json.dumps(n, sort_keys=True, allow_nan=False) for n in rows)


def revision(notes, ppq):
    return hashlib.sha256(json.dumps([ppq, canonical(notes)]).encode()).hexdigest()


def request_digest(envelope):
    return hashlib.sha256(
        json.dumps(envelope, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
