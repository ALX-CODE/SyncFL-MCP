import math

import pytest

from fl_studio_mcp.utils.pattern_bank import quantize_notes, validate_bank, verify_notes


def bank(note=None):
    return [{"name": "Test", "notes": [note or {"midi": 66, "time": 0, "duration": 1}]}]


@pytest.mark.parametrize(
    "field,value",
    [("time", math.nan), ("duration", 0), ("velocity", 1.1), ("midi", True), ("midi", 128)],
)
def test_invalid_notes_rejected_before_work(field, value):
    n = {"midi": 66, "time": 0, "duration": 1, "velocity": 0.8}
    n[field] = value
    with pytest.raises(ValueError):
        validate_bank(bank(n))


def test_duplicate_names_rejected():
    with pytest.raises(ValueError):
        validate_bank(bank() + bank())


def test_triplet_rounding_and_minimum_length():
    result = quantize_notes([{"midi": 66, "time": 1.333333, "duration": 0.001}], 96)
    assert int(result[0]["time"] * 96) == 128
    assert int(result[0]["duration"] * 96) == 1


def test_readback_checks_pitch_time_length_and_velocity():
    notes = quantize_notes([{"midi": 66, "time": 1 / 3, "duration": 2 / 3}], 96)
    state = {
        "ppq": 96,
        "notes": [{"midi": 66, "time_ticks": 32, "length_ticks": 64, "velocity": 0.80000001}],
    }
    verify_notes(state, notes)
    state["notes"][0]["midi"] = 67
    with pytest.raises(RuntimeError):
        verify_notes(state, notes)


def test_missing_or_duplicated_readback_fails():
    notes = quantize_notes([{"midi": 66, "duration": 1}], 96)
    with pytest.raises(RuntimeError):
        verify_notes({"ppq": 96, "notes": []}, notes)
