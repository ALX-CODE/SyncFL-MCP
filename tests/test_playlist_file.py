"""Saved Playlist identity, byte preservation and timeline edit contracts."""

import struct

import pytest
from test_project_bundle import event, project, text

from fl_studio_mcp.utils.playlist_file import edit_playlist, inspect_playlist
from fl_studio_mcp.utils.project_bundle import _parse


def test_add_first_pattern_clips_to_empty_playlist_preserves_nonplaylist_bytes(tmp_path):
    source, output = tmp_path / "empty.flp", tmp_path / "added.flp"
    source.write_bytes(project(playlist=b""))
    before = inspect_playlist(source)
    index = before["arrangements"][0]["event_index"]
    edits = [
        dict(
            operation="add",
            event_index=index,
            pattern=2,
            expected_pattern_name="After the last light",
            expected_length_ticks=384,
            position_ticks=i * 384,
            track=i + 1,
        )
        for i in range(3)
    ]
    report = edit_playlist(source, output, before["sha256"], edits, False)
    clips = inspect_playlist(output)["arrangements"][0]["clips"]
    assert [(c["position_ticks"], c["track"], c["pattern"]) for c in clips] == [
        (0, 1, 2),
        (384, 2, 2),
        (768, 3, 2),
    ]
    a, b = _parse(source.read_bytes())[1], _parse(output.read_bytes())[1]
    assert all(x.encoded == y.encoded for i, (x, y) in enumerate(zip(a, b)) if i != index)
    assert report["unchanged_events_preserved"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_pattern_name", "Wrong"),
        ("expected_length_ticks", 0),
        ("track", 0),
        ("position_ticks", True),
        ("pattern", 999),
    ],
)
def test_first_clip_rejects_unverified_identity_and_invalid_coordinates(tmp_path, field, value):
    source, output = tmp_path / "empty.flp", tmp_path / "bad.flp"
    source.write_bytes(project(playlist=b""))
    before = inspect_playlist(source)
    edit = dict(
        operation="add",
        event_index=before["arrangements"][0]["event_index"],
        pattern=2,
        expected_pattern_name="After the last light",
        expected_length_ticks=384,
        position_ticks=0,
        track=1,
    )
    edit[field] = value
    with pytest.raises(ValueError):
        edit_playlist(source, output, before["sha256"], [edit], False)
    assert not output.exists()


def clip(position=0, pattern=2, length=384, track=1):
    raw = bytearray(range(88))
    struct.pack_into("<IHHIH", raw, 0, position, 20480, 20480 + pattern, length, 500 - track)
    raw[24:32] = b"\xff" * 8
    return bytes(raw)


@pytest.fixture
def rig(tmp_path):
    original = clip()
    source = tmp_path / "source.flp"
    source.write_bytes(
        project(
            event(250, b"opaque plugin data")
            + event(65, struct.pack("<H", 3))
            + text(193, "Chorus")
            + event(233, clip(1536)),
            playlist=original,
        )
    )
    inspection = inspect_playlist(source)
    arrangement = inspection["arrangements"][0]
    edit = dict(
        operation="clone",
        event_index=arrangement["event_index"],
        clip=0,
        expected_clip_sha256=arrangement["clips"][0]["sha256"],
        position_ticks=384,
    )
    return dict(
        source=source,
        output=tmp_path / "arranged.flp",
        expected_sha256=inspection["sha256"],
        edits=[edit],
    )


def test_clone_update_delete_and_separate_arrangement_preservation(rig):
    original = rig["source"].read_bytes()
    template = rig["edits"][0]
    rig["edits"] += [
        {
            **template,
            "position_ticks": 768,
            "pattern": 2,
            "expected_pattern_name": "After the last light",
        },
        {**template, "operation": "update", "position_ticks": 96, "track": 2},
    ]
    report = edit_playlist(**rig, dry_run=False)
    assert report["success"] and not report["loaded_in_fl"]
    assert rig["source"].read_bytes() == original
    readback = inspect_playlist(rig["output"])
    assert readback["sha256"] == report["output_sha256"]
    before_events = _parse(original)[1]
    after_events = _parse(rig["output"].read_bytes())[1]
    index = template["event_index"]
    assert all(
        a.encoded == b.encoded
        for i, (a, b) in enumerate(zip(before_events, after_events))
        if i != index
    )
    assert [c["position_ticks"] for c in readback["arrangements"][0]["clips"]] == [96, 384, 768]
    assert readback["arrangements"][0]["clips"][2]["pattern"] == 2
    assert readback["arrangements"][1]["clips"][0]["position_ticks"] == 1536
    for i in range(3):
        raw = after_events[index].payload[i * 88 : (i + 1) * 88]
        # Only documented placement/item fields may differ.
        assert raw[4:6] == clip()[4:6] and raw[14:] == clip()[14:]


def test_delete_removes_only_selected_original_clip(rig):
    rig["edits"][0].pop("position_ticks")
    rig["edits"][0]["operation"] = "delete"
    result = edit_playlist(**rig, dry_run=False)
    assert result["arrangements"][0]["clips"] == []
    assert len(result["arrangements"][1]["clips"]) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("position_ticks", -1),
        ("position_ticks", True),
        ("position_ticks", 0.5),
        ("length_ticks", 0),
        ("length_ticks", 3072),
        ("track", 0),
        ("track", 501),
        ("expected_clip_sha256", "wrong"),
        ("operation", "invalid"),
        ("clip", 10),
        ("event_index", 0),
        ("pattern", 123),
        ("surprise", True),
    ],
)
def test_invalid_operations_publish_nothing(rig, field, value):
    rig["edits"][0][field] = value
    with pytest.raises(ValueError):
        edit_playlist(**rig, dry_run=False)
    assert not rig["output"].exists()


def test_source_change_or_existing_output_never_overwritten(rig):
    rig["expected_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        edit_playlist(**rig, dry_run=False)
    rig["output"].write_bytes(b"keep")
    with pytest.raises(ValueError, match="not exist"):
        edit_playlist(**rig, dry_run=False)
    assert rig["output"].read_bytes() == b"keep"


def test_dry_run_creates_no_file_and_matches_apply(rig):
    preview = edit_playlist(**rig)
    assert not rig["output"].exists()
    applied = edit_playlist(**rig, dry_run=False)
    assert applied["output_sha256"] == preview["output_sha256"]


def test_unknown_version_and_unknown_channel_clip_rejected(rig):
    rig["source"].write_bytes(project(version="25.1", playlist=clip()))
    with pytest.raises(ValueError, match="FL26"):
        inspect_playlist(rig["source"])
    rig["source"].write_bytes(project(playlist=clip(pattern=0)))
    before = inspect_playlist(rig["source"])
    rig["expected_sha256"] = before["sha256"]
    a = before["arrangements"][0]
    rig["edits"][0].update(
        event_index=a["event_index"], expected_clip_sha256=a["clips"][0]["sha256"]
    )
    with pytest.raises(ValueError, match="Unsupported channel"):
        edit_playlist(**rig, dry_run=False)


def test_64_bar_arrangement_from_original_template(rig):
    template = rig["edits"][0]
    # At 96 PPQ in 4/4, 64 one-bar template placements form a 64-bar song.
    rig["edits"] = [
        {**template, "position_ticks": section * 384, "track": 2} for section in range(64)
    ]
    rig["edits"].append(
        {k: v for k, v in dict(template, operation="delete").items() if k != "position_ticks"}
    )
    result = edit_playlist(**rig, dry_run=False)
    clips = result["arrangements"][0]["clips"]
    assert len(clips) == 64 and clips[-1]["position_ticks"] + clips[-1]["length_ticks"] == 64 * 384


def test_pattern_substitution_rejected_even_when_name_matches(rig):
    rig["edits"][0].update(pattern=3, expected_pattern_name="Chorus")
    with pytest.raises(ValueError, match="template clip"):
        edit_playlist(**rig, dry_run=False)
    assert not rig["output"].exists()


# Captured 88-byte native FL26.1.4 records; provenance/qualification boundaries
# live in docs/PLAYLIST-CHANNEL-CLIP-FORMAT.md. These are not invented templates.
NATIVE_CHANNEL_CLIPS = {
    "audio": bytes.fromhex(
        "2c0b00000050120040010000f101000078004080406480809f5c2042bb43ad44"
        "1000000000000000000000000000f043000000003c65233f0300000000000000"
        "000000000000f03f00000000ffffffff0000000000000000"
    ),
    "automation": bytes.fromhex(
        "800a000000501300803d0000e30100007800408040648080000080bf000080bf"
        "0f0000000000b44200000000000000000f0920420000803f0300000000000000"
        "000000000000f03f00000000ffffffff0000000000000000"
    ),
}


def channel_project(kind, *, channel_type=None, include_source=True, repeated_source=False):
    raw = NATIVE_CHANNEL_CLIPS[kind]
    iid = struct.unpack_from("<H", raw, 6)[0]
    if channel_type is None:
        channel_type = {"audio": 4, "automation": 5}[kind]
    metadata = (event(64, struct.pack("<H", iid)) + event(21, bytes([channel_type]))
                + text(203, f"Saved {kind}"))
    if include_source:
        if kind == "audio":
            metadata += text(196, "C:\\Samples\\vocal.wav")
            if repeated_source:
                metadata += text(196, "C:\\Samples\\another.wav")
        else:
            curve = (bytes(17) + struct.pack("<I", 2)
                     + struct.pack("<ddfI", 0, 0.25, 0, 0)
                     + struct.pack("<ddfI", 4, 0.75, 0, 0))
            metadata += event(234, curve)
    return project(metadata + event(233, raw), version="26.1.4.5589")


@pytest.fixture(params=["audio", "automation"])
def channel_rig(tmp_path, request):
    kind = request.param
    source = tmp_path / f"{kind}.flp"
    source.write_bytes(channel_project(kind))
    before = inspect_playlist(source)
    arrangement = before["arrangements"][0]
    edit = dict(
        operation="clone",
        event_index=arrangement["event_index"],
        clip=0,
        expected_clip_sha256=arrangement["clips"][0]["sha256"],
        expected_channel_name=f"Saved {kind}",
        position_ticks=19200,
        track=20,
    )
    return kind, dict(
        source=source,
        output=tmp_path / "placed.flp",
        expected_sha256=before["sha256"],
        edits=[edit],
    )


def test_native_channel_inspection_reports_identity_and_raw_offsets(channel_rig):
    kind, rig = channel_rig
    before = inspect_playlist(rig["source"])
    clip = before["arrangements"][0]["clips"][0]
    assert clip["kind"] == kind
    assert clip["pattern"] is None
    assert clip["channel"] == {"audio": 18, "automation": 19}[kind]
    assert clip["channel_name"] == f"Saved {kind}"
    assert clip["channel_type"] == {"audio": 4, "automation": 5}[kind]
    assert clip["offset_bytes"] == NATIVE_CHANNEL_CLIPS[kind][24:32].hex()
    assert clip["source_offsets"]["units"] == "unverified; raw bytes preserved"
    descriptor = next(c for c in before["channels"] if c["id"] == clip["channel"])
    assert descriptor["kind"] == kind
    assert descriptor["sample_paths"] == (["C:\\Samples\\vocal.wav"] if kind == "audio" else [])


def test_native_channel_move_and_clone_preserve_every_nonplacement_byte(channel_rig):
    kind, rig = channel_rig
    original = rig["source"].read_bytes()
    template = rig["edits"][0]
    rig["edits"].append({**template, "operation": "update", "position_ticks": 960, "track": 4})
    preview = edit_playlist(**rig)
    assert not rig["output"].exists()
    result = edit_playlist(**rig, dry_run=False)
    assert preview["output_sha256"] == result["output_sha256"]
    assert rig["source"].read_bytes() == original
    before_events = _parse(original)[1]
    after_events = _parse(rig["output"].read_bytes())[1]
    index = template["event_index"]
    assert all(a.encoded == b.encoded for i, (a, b) in
               enumerate(zip(before_events, after_events)) if i != index)
    raw = NATIVE_CHANNEL_CLIPS[kind]
    records = after_events[index].payload
    assert len(records) == 176
    for item in (records[:88], records[88:]):
        # Exactly position u32 and reverse-track u16 may change. This includes
        # bit-for-bit preservation of channel identity, source offsets, fades,
        # stretch-related fields and all unrecognized FL26 extension bytes.
        assert item[4:12] == raw[4:12]
        assert item[14:] == raw[14:]
    clips = result["arrangements"][0]["clips"]
    assert [(c["position_ticks"], c["track"], c["kind"]) for c in clips] == [
        (960, 4, kind), (19200, 20, kind),
    ]
    assert result["loaded_in_fl"] is False


def test_native_channel_delete_leaves_saved_source_channel_untouched(channel_rig):
    _, rig = channel_rig
    edit = rig["edits"][0]
    edit.update(operation="delete")
    del edit["position_ticks"], edit["track"]
    before = inspect_playlist(rig["source"])
    edit_playlist(**rig, dry_run=False)
    after = inspect_playlist(rig["output"])
    assert after["arrangements"][0]["clips"] == []
    assert after["channels"] == before["channels"]


@pytest.mark.parametrize("fields,match", [
    ({"expected_channel_name": "wrong"}, "channel name"),
    ({"pattern": 2}, "Pattern fields"),
    ({"expected_pattern_name": "Saved audio"}, "Pattern fields"),
    ({"length_ticks": 200}, "resizing"),
    ({"operation": "split"}, "operation"),
    ({"source_offset": 1}, "Unknown Playlist"),
])
def test_channel_unsupported_source_changes_are_rejected(channel_rig, fields, match):
    _, rig = channel_rig
    rig["edits"][0].update(fields)
    with pytest.raises(ValueError, match=match):
        edit_playlist(**rig, dry_run=False)
    assert not rig["output"].exists()


@pytest.mark.parametrize("kind,options", [
    ("audio", {"channel_type": 0}),
    ("audio", {"channel_type": 2}),
    ("audio", {"include_source": False}),
    ("audio", {"repeated_source": True}),
    ("automation", {"channel_type": 4}),
    ("automation", {"include_source": False}),
])
def test_ambiguous_or_unrecognized_channel_types_fail_closed(tmp_path, kind, options):
    source, output = tmp_path / "unknown.flp", tmp_path / "out.flp"
    source.write_bytes(channel_project(kind, **options))
    before = inspect_playlist(source)
    arrangement = before["arrangements"][0]
    assert arrangement["clips"][0]["kind"] == "unknown_channel"
    with pytest.raises(ValueError, match="Unsupported channel"):
        edit_playlist(source, output, before["sha256"], [dict(
            operation="clone", event_index=arrangement["event_index"], clip=0,
            expected_clip_sha256=arrangement["clips"][0]["sha256"], position_ticks=1920,
        )], False)
    assert not output.exists()
