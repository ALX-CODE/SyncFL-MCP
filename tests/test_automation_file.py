import hashlib
import struct

import pytest

from fl_studio_mcp.utils.automation_file import (
    _encode,
    _events,
    edit_automation,
    inspect_automation,
)


def project():
    curve = b"\0" * 17 + struct.pack("<I", 2)
    curve += struct.pack("<ddfI", 0, 0.8, 0, 0)
    curve += struct.pack("<ddfI", 16.125, 0.8, 0, 0x2000000)
    curve += b"opaque automation tail"
    body = bytes([64]) + struct.pack("<H", 19)
    body += _encode(203, "Pad volume\0".encode("utf-16le"))
    body += _encode(234, curve) + _encode(213, b"opaque plugin state FLdt")
    return (
        b"FLhd" + struct.pack("<IHHH", 6, 0, 20, 96) + b"FLdt" + struct.pack("<I", len(body)) + body
    )


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "source.flp"
    source.write_bytes(project())
    return source, tmp_path / "edited.flp", hashlib.sha256(project()).hexdigest()


def edits(**kwargs):
    return [
        dict(
            channel=19,
            expected_name="Pad volume",
            points=[dict(tick=768, value=0.5, mode="hold")],
            **kwargs,
        )
    ]


def test_exact_edit_preserves_other_events_and_fractional_positions(files):
    source, output, digest = files
    report = edit_automation(source, output, digest, edits(), dry_run=False)
    assert report["success"] and not report["loaded_in_fl"]
    assert source.read_bytes() == project()
    points = inspect_automation(output)["automation"][0]["points"]
    assert [(x["tick"], x["value"], x["mode"]) for x in points] == [
        (0, 0.8, 0),
        (768, 0.5, 2),
        (1548, 0.8, 0),
    ]
    before = [raw for eid, _, raw in _events(project())[2] if eid != 234]
    after = [raw for eid, _, raw in _events(output.read_bytes())[2] if eid != 234]
    assert before == after
    assert b"opaque automation tail" in output.read_bytes()


def test_dry_run_does_not_write(files):
    source, output, digest = files
    assert edit_automation(source, output, digest, edits())["dry_run"]
    assert not output.exists()


def test_stale_revision_identity_existing_output(files):
    source, output, digest = files
    with pytest.raises(ValueError, match="revision"):
        edit_automation(source, output, "bad", edits(), False)
    wrong = edits()
    wrong[0]["expected_name"] = "Kick"
    with pytest.raises(ValueError, match="identity"):
        edit_automation(source, output, digest, wrong, False)
    with pytest.raises(ValueError, match="separate"):
        edit_automation(source, source, digest, edits(), False)
    assert not output.exists()


@pytest.mark.parametrize(
    "point",
    [
        {"tick": True, "value": 0.5},
        {"tick": -1, "value": 0.5},
        {"tick": 4.5, "value": 0.5},
        {"tick": 4, "value": float("nan")},
        {"tick": 4, "value": True},
        {"tick": 4, "value": 1.1},
        {"tick": 4, "value": 0.5, "mode": "smooth"},
    ],
)
def test_invalid_points_no_write(files, point):
    source, output, digest = files
    edit = edits()
    edit[0]["points"] = [point]
    with pytest.raises(ValueError):
        edit_automation(source, output, digest, edit, False)
    assert not output.exists()


def test_replace_requires_zero(files):
    source, output, digest = files
    with pytest.raises(ValueError, match="zero"):
        edit_automation(source, output, digest, edits(operation="replace"), False)


@pytest.mark.parametrize("data", [b"FLhd", project()[:-1], project() + b"bad"])
def test_truncated_or_extra_chunks_rejected(data):
    with pytest.raises(ValueError):
        _events(data)


def curve_files(files, deltas):
    source, output, _ = files
    payload = b"\0" * 17 + struct.pack("<I", len(deltas))
    for i, delta in enumerate(deltas):
        payload += struct.pack("<ddfI", delta, 0.1 + i * 0.1, 0, 0x2000000)
    _, start, events = _events(project())
    body = b"".join(_encode(eid, payload) if eid == 234 else raw for eid, _, raw in events)
    data = project()[: start - 4] + struct.pack("<I", len(body)) + body
    source.write_bytes(data)
    return source, output, hashlib.sha256(data).hexdigest()


def test_upsert_preserves_untouched_duplicate_positions(files):
    source, output, digest = curve_files(files, [0, 4, 0, 4])
    before = inspect_automation(source)["automation"][0]["points"]
    edit_automation(source, output, digest, edits(), False)
    after = inspect_automation(output)["automation"][0]["points"]
    assert len(after) == len(before) == 4
    assert after[:3] == before[:3]
    assert after[3]["tick"] == 768 and after[3]["value"] == 0.5


def test_upsert_replaces_point_despite_delta_accumulation_roundoff(files):
    source, output, digest = curve_files(files, [0] + [1 / 3] * 6)
    before = inspect_automation(source)["automation"][0]["points"]
    assert before[-1]["tick"] != 192
    edit = edits()
    edit[0]["points"] = [dict(tick=192, value=0.9, mode="hold")]
    edit_automation(source, output, digest, edit, False)
    after = inspect_automation(output)["automation"][0]["points"]
    assert len(after) == len(before) == 7
    assert after[:-1] == before[:-1]
    assert after[-1]["tick"] == pytest.approx(192, abs=1e-10)
    assert after[-1]["value"] == 0.9 and after[-1]["mode"] == 2
    payload = next(payload for eid, payload, _ in _events(output.read_bytes())[2] if eid == 234)
    assert struct.unpack_from("<I", payload, 21 + 24 * 6 + 20)[0] == 0x2000002


@pytest.mark.parametrize("deltas", [[0, 4, 0, 4], [0, 4, 1e-11, 4]])
def test_upsert_refuses_ambiguous_matching_points_without_output(files, deltas):
    source, output, digest = curve_files(files, deltas)
    original = source.read_bytes()
    edit = edits()
    edit[0]["points"] = [dict(tick=384, value=0.9)]
    with pytest.raises(ValueError, match="Ambiguous automation point"):
        edit_automation(source, output, digest, edit, False)
    assert not output.exists() and source.read_bytes() == original


def test_numerical_tolerance_does_not_snap_real_fractional_tick(files):
    source, output, digest = curve_files(files, [0, (384 - 0.0001) / 96, 4])
    before = inspect_automation(source)["automation"][0]["points"]
    edit = edits()
    edit[0]["points"] = [dict(tick=384, value=0.9)]
    edit_automation(source, output, digest, edit, False)
    after = inspect_automation(output)["automation"][0]["points"]
    assert len(after) == len(before) + 1
    assert after[1] == before[1]
