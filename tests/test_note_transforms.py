import asyncio
import copy
import hashlib
import json

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from fl_studio_mcp.tools.note_transforms import register_note_transform_tools
from fl_studio_mcp.utils.note_protocol import normalize_note
from fl_studio_mcp.utils.note_transforms import transform_notes


def note(midi=60, time=13, length=11, **expression):
    return {"midi": midi, "time_ticks": time, "length_ticks": length, **expression}


def test_transposition_preserves_every_expression_field_and_source():
    expression = {
        "velocity": 0.937, "pan": 0.321, "release": 0.417,
        "color": 13, "slide": True, "porta": False, "pitchofs": -117,
        "fcut": 0.643, "fres": 0.275, "muted": True, "selected": False,
        "group": 2_147_483_647, "repeats": 14,
    }
    source = [note(**expression)]
    before = copy.deepcopy(source)
    result = transform_notes(source, ppq=96, transpose_semitones=7)
    assert source == before
    assert result["notes"] == [{**before[0], "midi": 67}]
    for key, value in expression.items():
        assert result["notes"][0][key] == value
        assert type(result["notes"][0][key]) is type(value)
    assert result["applied"] is False
    assert result["changed_count"] == result["note_count"] == 1
    result["notes"][0]["pan"] = 0
    assert source == before


def test_noop_normalizes_defaults_and_content_hashes_include_ppq_and_note_order():
    source = [{"midi": 60, "time": 0.5, "duration": 0.25}]
    result = transform_notes(source, 100)
    expected = [{"midi": 60, "time_ticks": 50, "length_ticks": 25, "velocity": 0.8}]
    content = json.dumps(
        {"ppq": 100, "notes": expected}, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    assert result["notes"] == expected
    assert result["source_sha256"] == result["result_sha256"]
    assert result["source_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert result["changed_count"] == 0
    assert transform_notes(expected, 101)["source_sha256"] != result["source_sha256"]
    ordered = [note(60), note(62)]
    assert transform_notes(ordered, 96)["source_sha256"] != transform_notes(
        list(reversed(ordered)), 96,
    )["source_sha256"]


def test_grid_quantization_and_optional_lengths():
    source = [note(60, 13, 14), note(59, 36, 38), note(60, 13, 14)]
    result = transform_notes(source, 96, -12, 24, 1, True)
    assert [(n["midi"], n["time_ticks"], n["length_ticks"]) for n in result["notes"]] == [
        (48, 24, 24), (47, 48, 48), (48, 24, 24),
    ]
    assert result["changed_count"] == 3
    assert len(result["notes"]) == 3  # Preserve identical notes; never silently merge them.
    positions_only = transform_notes(source, 96, grid_ticks=24)
    assert [n["length_ticks"] for n in positions_only["notes"]] == [14, 38, 14]


@pytest.mark.parametrize("time,grid,expected", [
    (4, 10, 0), (5, 10, 10), (6, 10, 10), (15, 10, 20),
    (12, 25, 0), (13, 25, 25), (37, 25, 25), (38, 25, 50),
])
def test_nearest_grid_and_half_ties_round_toward_larger_tick(time, grid, expected):
    result = transform_notes([note(time=time)], 101, grid_ticks=grid)
    assert result["notes"][0]["time_ticks"] == expected


@pytest.mark.parametrize("time,strength,expected", [
    (5, 0, 5), (5, 1, 10), (5, 0.5, 8), (4, 0.5, 2),
    (6, 0.125, 7), (6, 0.375, 8), (6, 0.1, 6),
])
def test_strength_zero_one_and_exact_fractional_ties(time, strength, expected):
    result = transform_notes([note(time=time)], 96, grid_ticks=10, strength=strength)
    assert result["notes"][0]["time_ticks"] == expected
    assert result["changed_count"] == int(expected != time)


def test_zero_strength_preserves_tiny_positive_lengths_and_all_content():
    source = [note(time=5, length=1)]
    result = transform_notes(source, 96, grid_ticks=96, strength=0, quantize_lengths=True)
    assert result["notes"] == [normalize_note(source[0], 96)]
    assert result["changed_count"] == 0
    assert result["source_sha256"] == result["result_sha256"]


def test_ppq_does_not_have_to_divide_grid_and_no_meter_is_assumed():
    source = [{"midi": 60, "time": 0.5, "duration": 1}]
    result = transform_notes(source, 101, grid_ticks=24)
    assert result["notes"][0]["time_ticks"] == 48
    assert result["notes"][0]["length_ticks"] == 101
    assert result["ppq"] == 101


def test_beat_input_uses_shared_nearest_even_policy_before_grid_transform():
    notes = [{"midi": 60, "time": 0.5, "duration": 0.5}]
    result = transform_notes(notes, 5)
    assert result["notes"][0]["time_ticks"] == 2
    assert result["notes"][0]["length_ticks"] == 2
    assert "nearest-even" in result["rounding"]["beat_input"]
    assert "larger/later" in result["rounding"]["grid_and_strength"]


@pytest.mark.parametrize("extra", [
    {"time": 0.5, "time_ticks": 48},
    {"duration": 0.5, "length_ticks": 48},
    {"time": 0.5, "time_ticks": 48, "duration": 0.5, "length_ticks": 48},
])
def test_equivalent_beat_and_tick_fields_are_accepted_without_drops(extra):
    source = [{**note(), **extra}]
    result = transform_notes(source, 96)
    assert result["notes"] == [normalize_note(source[0], 96)]
    assert "time" not in result["notes"][0] and "duration" not in result["notes"][0]


@pytest.mark.parametrize("extra", [
    {"time": 0.5, "time_ticks": 47},
    {"duration": 0.5, "length_ticks": 49},
    {"time": None}, {"duration": 0}, {"duration": float("nan")},
])
def test_conflicting_or_invalid_shadowed_beat_fields_are_rejected(extra):
    with pytest.raises(ValueError):
        transform_notes([{**note(), **extra}], 96)


@pytest.mark.parametrize("midi,shift", [(0, -1), (127, 1), (60, 127), (60, -127)])
def test_transposition_rejects_out_of_range_notes_without_changing_inputs(midi, shift):
    source = [note(60), note(midi)]
    before = copy.deepcopy(source)
    with pytest.raises(ValueError, match="transposed midi"):
        transform_notes(source, 96, transpose_semitones=shift)
    assert source == before


def test_full_transposition_bounds_are_accepted():
    assert transform_notes([note(0)], 96, 127)["notes"][0]["midi"] == 127
    assert transform_notes([note(127)], 96, -127)["notes"][0]["midi"] == 0


def test_quantization_rejects_overflow_and_zero_length_rather_than_clamping():
    with pytest.raises(ValueError, match="quantized time_ticks"):
        transform_notes([note(time=4096)], 1, grid_ticks=6)
    with pytest.raises(ValueError, match="quantized length_ticks"):
        transform_notes([note(time=0, length=4096)], 1, grid_ticks=6, quantize_lengths=True)
    with pytest.raises(ValueError, match="quantized length_ticks"):
        transform_notes([note(length=11)], 96, grid_ticks=24, quantize_lengths=True)


@pytest.mark.parametrize("arguments", [
    {"ppq": 0}, {"ppq": 96001}, {"ppq": True}, {"ppq": 96.0},
    {"transpose_semitones": True}, {"transpose_semitones": 0.5},
    {"transpose_semitones": 128}, {"transpose_semitones": -128},
    {"grid_ticks": True}, {"grid_ticks": 0}, {"grid_ticks": 1.5},
    {"grid_ticks": 4096 * 96 + 1},
    {"strength": -0.1}, {"strength": 1.1}, {"strength": True},
    {"strength": "0.5"}, {"strength": float("nan")}, {"strength": float("inf")},
    {"strength": 10**500}, {"grid_ticks": None, "strength": 0},
    {"quantize_lengths": True}, {"quantize_lengths": 1},
])
def test_invalid_transform_options(arguments):
    with pytest.raises(ValueError):
        transform_notes([note()], **{"ppq": 96, **arguments})


@pytest.mark.parametrize("source", [
    None, {}, [None], [{**note(), "unknown_expression": 1}],
    [{**note(), 123: 1}], [{**note(), "velocity": True}],
    [{**note(), "time": 10**500}], [{**note(), "midi": 128}],
    [{**note(), "length_ticks": 0}], [{**note(), "time_ticks": -1}],
    [note()] * 65_537,
])
def test_invalid_notes_and_limits(source):
    with pytest.raises(ValueError):
        transform_notes(source, 96)


def test_empty_selection_is_a_valid_empty_proposal():
    result = transform_notes([], 96)
    assert result["notes"] == [] and result["changed_count"] == 0
    assert result["applied"] is False
    assert result["source_sha256"] == result["result_sha256"]


def test_tool_schema_strict_types_and_offline_result():
    mcp = FastMCP("Offline transform tests")
    register_note_transform_tools(mcp)

    async def exercise():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            schema = tools["fl_notes_transform"].inputSchema
            assert schema["required"] == ["notes", "ppq"]
            assert schema["properties"]["ppq"]["maximum"] == 96_000
            response = await client.call_tool("fl_notes_transform", {
                "notes": [note()], "ppq": 96, "transpose_semitones": 2,
                "grid_ticks": 24, "strength": 1,
            })
            result = response.structured_content
            assert result["applied"] is False
            assert result["notes"][0]["midi"] == 62
            assert result["notes"][0]["time_ticks"] == 24
            for extra in ({"ppq": True}, {"strength": True}, {"quantize_lengths": 1}):
                with pytest.raises(ToolError):
                    await client.call_tool("fl_notes_transform", {
                        "notes": [note()], "ppq": 96, "grid_ticks": 24, **extra,
                    })

    asyncio.run(exercise())
