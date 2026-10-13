import asyncio
import hashlib
import struct

import mido
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from fl_studio_mcp.tools.midi_reference import register_midi_reference_tools
from fl_studio_mcp.utils import midi_reference as reference


def midi_file(root, relative="lead.mid", messages=None, *, ppq=480, extra_tracks=()):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=1, ticks_per_beat=ppq)
    midi.tracks.append(mido.MidiTrack(messages if messages is not None else [
        mido.Message("note_on", note=60, velocity=100, time=0),
        mido.Message("note_off", note=60, time=480),
    ]))
    midi.tracks.extend(extra_tracks)
    midi.save(path)
    return path


def only(root, **kwargs):
    result = reference.search_midi_references(roots=[root], **kwargs)
    assert result["scan_complete"], result
    assert result["matching_files"] == 1
    return result["references"][0]


def note(pitch=60, start=0, duration=1):
    return {"pitch": pitch, "start_beats": start, "duration_beats": duration}


def test_all_event_deltas_are_counted_and_tempo_does_not_change_beats(tmp_path):
    conductor = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000),
        mido.MetaMessage("set_tempo", tempo=1_000_000, time=1920),
        mido.MetaMessage("end_of_track", time=480),
    ])
    path = midi_file(tmp_path, messages=[
        mido.MetaMessage("track_name", name="Lead"),
        mido.Message("note_on", note=60, time=120),
        mido.Message("program_change", program=17, time=120),
        mido.MetaMessage("set_tempo", tempo=750_000, time=120),
        mido.Message("note_off", note=60, time=120),
        mido.Message("note_on", note=64, time=0),
        mido.Message("note_on", note=64, velocity=0, time=240),
        mido.MetaMessage("end_of_track", time=240),
    ], extra_tracks=[conductor])
    before = path.read_bytes()
    item = only(tmp_path)
    assert item["sha256"] == hashlib.sha256(before).hexdigest()
    assert item["duration_beats"] == 5
    assert item["tracks"][0]["duration_beats"] == 2
    assert item["tracks"][1]["duration_beats"] == 5
    assert item["tracks"][0]["name"] == "Lead"
    assert item["notes_per_beat"] == 0.4
    assert item["note_count"] == 2
    assert item["pitch_range"] == [60, 64]
    assert item["embedded_tempos_us_per_beat"] == [500_000, 750_000, 1_000_000]
    assert item["fingerprint"]["onset_phase"][0] == 0.5
    assert item["fingerprint"]["onset_phase"][4] == 0.5
    assert item["fingerprint"]["duration"][3:5] == [0.5, 0.5]
    assert path.read_bytes() == before


def test_same_pitch_fifo_channel_isolation_and_unmatched_notes(tmp_path):
    midi_file(tmp_path, messages=[
        mido.Message("note_on", note=60, channel=0),
        mido.Message("note_on", note=60, channel=0, time=120),
        mido.Message("note_on", note=60, channel=1, time=120),
        mido.Message("note_off", note=60, channel=0, time=120),
        mido.Message("note_off", note=60, channel=1, time=120),
        mido.Message("note_off", note=60, channel=0, time=120),
        mido.Message("note_off", note=60, channel=0),
        mido.Message("note_on", note=67),
        mido.MetaMessage("end_of_track", time=480),
    ])
    item = only(tmp_path)
    assert item["note_count"] == 4
    assert item["tracks"][0]["overlapping_same_pitch"] == 1
    assert item["tracks"][0]["unmatched_note_offs"] == 1
    assert item["tracks"][0]["unclosed_notes"] == 1
    assert item["fingerprint"]["duration_observations"] == 3
    assert item["fingerprint"]["duration"][3:5] == pytest.approx([1 / 3, 2 / 3])
    assert item["fingerprint"]["pitch_class"][0] == 0.75


def test_empty_track_and_zero_length_notes_do_not_divide_by_zero(tmp_path):
    midi_file(tmp_path, messages=[])
    item = only(tmp_path)
    assert item["duration_beats"] == item["note_count"] == 0
    assert item["notes_per_beat"] is None
    assert item["pitch_range"] is None
    assert not any(item["fingerprint"]["pitch_class"])


def test_text_query_ranks_exact_normalized_name_and_searches_relative_directories(tmp_path):
    exact = midi_file(tmp_path, "Artist/Cold_Signal.MIDI")
    midi_file(tmp_path, "Artist/Cold Answer.mid")
    midi_file(tmp_path, "Other/Unrelated.mid")
    result = reference.search_midi_references("COLD signal", roots=[tmp_path], limit=1)
    assert result["scan_complete"] and result["truncated"]
    assert result["matching_files"] == 2
    assert result["references"][0]["path"] == str(exact)
    assert result["references"][0]["ranking"]["text_score"] == 1
    assert result["references"][0]["ranking"]["matched_query_tokens"] == ["cold", "signal"]
    artist = reference.search_midi_references("artist", roots=[tmp_path])
    assert artist["matching_files"] == 2
    assert all(item["ranking"]["text_score"] == 0.15 for item in artist["references"])


def test_note_similarity_ranking_is_transparent_and_not_melodic_order(tmp_path):
    midi_file(tmp_path, "match.mid", [
        mido.Message("note_on", note=60), mido.Message("note_off", note=60, time=240),
        mido.Message("note_on", note=64), mido.Message("note_off", note=64, time=240),
    ])
    midi_file(tmp_path, "different.mid", [
        mido.Message("note_on", note=61), mido.Message("note_off", note=61, time=120),
    ])
    result = reference.search_midi_references(
        roots=[tmp_path], query_notes=[note(60, 0, 0.5), note(64, 0.5, 0.5)],
    )
    best = result["references"][0]
    assert best["name"] == "match"
    assert best["ranking"]["score"] == 1
    assert best["ranking"]["similarity_components"] == {
        "pitch_class": 1, "onset_phase": 1, "duration": 1,
    }
    assert result["references"][1]["ranking"]["score"] < 1
    assert "melodic order" in result["ranking_definition"]["limits"]
    combined = reference.search_midi_references(
        "match", roots=[tmp_path], query_notes=[note(60, 0, 0.5), note(64, 0.5, 0.5)],
    )
    assert combined["references"][0]["ranking"]["score"] == 1


def test_roots_are_deduplicated_and_default_is_only_the_packaged_corpus(tmp_path, monkeypatch):
    midi_file(tmp_path, "nested/lead.mid")
    result = reference.search_midi_references(roots=[tmp_path / "nested", tmp_path, tmp_path])
    assert result["matching_files"] == result["scanned_files"] == 1
    assert result["roots"] == [str(tmp_path)]
    monkeypatch.setattr(reference, "_default_roots", lambda: [tmp_path])
    assert reference.search_midi_references()["matching_files"] == 1
    monkeypatch.setattr(reference, "_default_roots", lambda: [])
    with pytest.raises(ValueError, match="bundled reference directory"):
        reference.search_midi_references()


def test_scan_file_limit_does_not_claim_complete_and_exact_boundary_is_complete(tmp_path):
    midi_file(tmp_path, "one.mid")
    assert only(tmp_path, max_files=1)["note_count"] == 1
    midi_file(tmp_path, "two.mid")
    result = reference.search_midi_references(roots=[tmp_path], max_files=1)
    assert not result["scan_complete"] and result["truncated"]
    assert result["scanned_files"] == 1
    assert "max_files" in result["warnings"][0]


@pytest.mark.parametrize("setting,value", [
    ("MAX_ENTRIES", 0), ("MAX_DIRECTORIES", 0), ("MAX_SECONDS", 0),
    ("MAX_TOTAL_BYTES", 0),
])
def test_work_budgets_report_incomplete(tmp_path, monkeypatch, setting, value):
    midi_file(tmp_path)
    monkeypatch.setattr(reference, setting, value)
    result = reference.search_midi_references(roots=[tmp_path])
    assert not result["scan_complete"] and result["truncated"]
    assert result["warnings"] and not result["references"]


def test_depth_budget_does_not_silently_skip_subdirectories(tmp_path, monkeypatch):
    midi_file(tmp_path, "nested/lead.mid")
    monkeypatch.setattr(reference, "MAX_DEPTH", 0)
    result = reference.search_midi_references(roots=[tmp_path])
    assert result["truncated"] and not result["scan_complete"]
    assert "depth limit" in result["errors"][0]["error"]


def test_bad_midi_is_reported_and_does_not_prevent_other_results(tmp_path):
    (tmp_path / "bad.mid").write_bytes(b"not MIDI or executable pickle data")
    valid = midi_file(tmp_path)
    result = reference.search_midi_references(roots=[tmp_path])
    assert not result["scan_complete"] and result["truncated"]
    assert result["references"][0]["path"] == str(valid)
    assert result["error_count"] == 1
    assert result["errors"][0]["path"].endswith("bad.mid")


def test_event_budget_is_enforced_before_constructing_mido_objects(tmp_path, monkeypatch):
    midi_file(tmp_path)
    monkeypatch.setattr(reference, "MAX_EVENTS", 1)

    def not_called(*args, **kwargs):
        pytest.fail("Oversized event streams must be rejected before mido parsing")

    monkeypatch.setattr(reference.mido, "MidiFile", not_called)
    result = reference.search_midi_references(roots=[tmp_path])
    assert "event limit" in result["errors"][0]["error"]


@pytest.mark.parametrize("track", [
    b"\x80\x80\x80\x80\x00\x90\x3c\x40\x00\xff\x2f\x00",
    b"\x00\xff\x01\x80\x80\x80\x80\x00",
    b"\x00\x90\x3c\xff\x00\xff\x2f\x00",
    b"\x00\x3c\x40\x00\xff\x2f\x00",
    b"\x00\x90\x3c\x40",
    b"\x00\xff\x2f\x00\x00\x90\x3c\x40",
])
def test_malformed_track_boundaries_and_variable_lengths_are_rejected(tmp_path, track):
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    (tmp_path / "bad.mid").write_bytes(header + b"MTrk" + struct.pack(">I", len(track)) + track)
    result = reference.search_midi_references(roots=[tmp_path])
    assert not result["scan_complete"] and result["error_count"] == 1
    assert not result["references"]


@pytest.mark.parametrize("kind,division,reason", [
    (2, 480, "type 2"), (0, 0, "positive ticks"), (0, 0xe728, "SMPTE"),
])
def test_unsupported_timing_is_reported(tmp_path, kind, division, reason):
    path = midi_file(tmp_path)
    data = bytearray(path.read_bytes())
    data[8:10] = kind.to_bytes(2, "big")
    # The fixture is type 1 but has exactly one track, so changing to type 0 is valid.
    data[12:14] = division.to_bytes(2, "big")
    path.write_bytes(data)
    result = reference.search_midi_references(roots=[tmp_path])
    assert reason in result["errors"][0]["error"]


def test_file_size_bound_and_error_output_bound(tmp_path, monkeypatch):
    for index in range(4):
        midi_file(tmp_path, f"{index}.mid")
    monkeypatch.setattr(reference, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(reference, "MAX_ERRORS", 2)
    result = reference.search_midi_references(roots=[tmp_path])
    assert result["error_count"] == 4 and len(result["errors"]) == 2
    assert not result["scan_complete"]
    assert result["bytes_read"] == 0


def test_symlinks_are_skipped_and_linked_ancestors_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = midi_file(tmp_path, "outside/lead.mid")
    try:
        (root / "linked").symlink_to(outside.parent, target_is_directory=True)
        (root / "lead.mid").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    result = reference.search_midi_references(roots=[root])
    assert not result["references"] and result["skipped_links"] == 2
    with pytest.raises(ValueError, match="symlinks or junctions"):
        reference.search_midi_references(roots=[root / "linked"])
    nested = outside.parent / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="symlinks or junctions"):
        reference.search_midi_references(roots=[root / "linked" / "nested"])


@pytest.mark.parametrize("query", [None, True, "x" * 257, "bad\0query", "!!!"])
def test_invalid_query(tmp_path, query):
    with pytest.raises(ValueError, match="query"):
        reference.search_midi_references(query, roots=[tmp_path])


@pytest.mark.parametrize("field,value", [
    ("limit", True), ("limit", 0), ("limit", 101), ("limit", 1.5),
    ("max_files", False), ("max_files", 0), ("max_files", 2001),
])
def test_invalid_limits(tmp_path, field, value):
    with pytest.raises(ValueError, match=field):
        reference.search_midi_references(roots=[tmp_path], **{field: value})


@pytest.mark.parametrize("query_notes", [
    [], "notes", [None], [{}], [note(True)], [note(128)], [note(60.0)],
    [note(start=-1)], [note(start=float("nan"))], [note(duration=float("inf"))],
    [note(duration=0)], [note(duration=True)], [note(start=10**500)],
    [{**note(), "velocity": 100}],
    [note()] * 1025,
])
def test_invalid_query_notes(tmp_path, query_notes):
    with pytest.raises(ValueError):
        reference.search_midi_references(roots=[tmp_path], query_notes=query_notes)


@pytest.mark.parametrize("roots", [[], ["relative"], "C:/", [None], [""]])
def test_invalid_roots(roots):
    with pytest.raises(ValueError):
        reference.search_midi_references(roots=roots)


def test_discoverable_tool_schema_and_direct_read_only_route(tmp_path):
    midi_file(tmp_path)
    mcp = FastMCP("MIDI reference tests")
    register_midi_reference_tools(mcp)

    async def exercise():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            schema = tools["fl_midi_reference_search"].inputSchema
            assert schema["properties"]["limit"]["maximum"] == 100
            result = await client.call_tool("fl_midi_reference_search", {"roots": [str(tmp_path)]})
            assert result.structured_content["scan_complete"]
            assert result.structured_content["references"][0]["note_count"] == 1
            with pytest.raises(ToolError):
                await client.call_tool("fl_midi_reference_search", {
                    "roots": [str(tmp_path)], "limit": True,
                })

    asyncio.run(exercise())
