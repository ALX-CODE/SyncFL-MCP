"""Pure note proposals; no FL connection, queuing or source mutation."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from fl_studio_mcp.utils.note_protocol import integer, normalize_note, number

MAX_NOTES = 65_536


def _normalize(note: dict, ppq: int, index: int) -> dict:
    try:
        if not isinstance(note, dict) or any(not isinstance(key, str) for key in note):
            raise ValueError("Note must be an object with string field names")
        normalized = normalize_note(note, ppq)
        for beat_key, tick_key in (("time", "time_ticks"), ("duration", "length_ticks")):
            if beat_key in note and tick_key in note:
                alternative = dict(note)
                del alternative[tick_key]
                if normalize_note(alternative, ppq)[tick_key] != normalized[tick_key]:
                    raise ValueError(f"Conflicting {beat_key} and {tick_key}")
        return normalized
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"notes[{index}]: {exc}") from exc


def _hash(notes: list[dict], ppq: int) -> str:
    content = json.dumps(
        {"ppq": ppq, "notes": notes}, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _round_half_up(value: Fraction) -> int:
    # Inputs are nonnegative. Integer arithmetic avoids binary-float tie drift.
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _quantize(tick: int, grid: int, strength: Fraction) -> int:
    target = _round_half_up(Fraction(tick, grid)) * grid
    return _round_half_up(tick + (target - tick) * strength)


def transform_notes(
    notes: list[dict],
    ppq: int,
    transpose_semitones: int = 0,
    grid_ticks: int | None = None,
    strength: float = 1.0,
    quantize_lengths: bool = False,
) -> dict:
    """Normalize and propose transposition/tick quantization without touching FL.

    Both aliases are allowed only when they normalize to the same tick value.
    Beat inputs use note_protocol's nearest-even conversion (minimum one tick
    for a positive duration). Grid ties and partial-strength ties go to the
    larger tick. Grid spacing is explicit, with no inferred meter or note value.
    Note order, duplicates and every supported expression field are preserved.
    """
    integer(ppq, 1, 96_000, "ppq")
    integer(transpose_semitones, -127, 127, "transpose_semitones")
    if type(quantize_lengths) is not bool:
        raise ValueError("quantize_lengths must be boolean")
    try:
        number(strength, 0, 1, "strength")
    except OverflowError as exc:
        raise ValueError("strength outside 0..1") from exc
    if grid_ticks is None:
        if quantize_lengths:
            raise ValueError("quantize_lengths requires grid_ticks")
        if strength != 1:
            raise ValueError("strength requires grid_ticks")
    else:
        integer(grid_ticks, 1, 4096 * ppq, "grid_ticks")
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ValueError(f"notes must be a list containing at most {MAX_NOTES} notes")

    source = [_normalize(note, ppq, index) for index, note in enumerate(notes)]
    proposed = []
    changed = 0
    weight = Fraction(str(strength))
    for index, original in enumerate(source):
        result = dict(original)
        result["midi"] = integer(
            original["midi"] + transpose_semitones, 0, 127, f"notes[{index}] transposed midi",
        )
        if grid_ticks is not None:
            result["time_ticks"] = integer(
                _quantize(original["time_ticks"], grid_ticks, weight),
                0, 4096 * ppq, f"notes[{index}] quantized time_ticks",
            )
            if quantize_lengths:
                result["length_ticks"] = integer(
                    _quantize(original["length_ticks"], grid_ticks, weight),
                    1, 4096 * ppq, f"notes[{index}] quantized length_ticks",
                )
        changed += result != original
        proposed.append(result)

    return {
        "applied": False,
        "ppq": ppq,
        "notes": proposed,
        "note_count": len(source),
        "changed_count": changed,
        "source_sha256": _hash(source, ppq),
        "result_sha256": _hash(proposed, ppq),
        "transform": {
            "transpose_semitones": transpose_semitones,
            "grid_ticks": grid_ticks,
            "strength": strength,
            "quantize_lengths": quantize_lengths,
        },
        "rounding": {
            "beat_input": (
                "Shared note protocol: nearest-even tick; positive lengths at least one tick"
            ),
            "grid_and_strength": "Nearest tick; exact halves toward the larger/later tick",
            "strength_arithmetic": "Exact rational arithmetic from the supplied decimal strength",
        },
        "hash_scope": "Normalized ordered note content plus PPQ; not a live piano-roll revision",
        "next_step": (
            "Inspect the intended channel and fresh piano-roll state, confirm its PPQ, "
            "then pass the chosen proposal through the existing guarded note workflow. "
            "This preview does not queue or apply a write."
        ),
    }
