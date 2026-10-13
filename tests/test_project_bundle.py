import hashlib
import json
import struct
from pathlib import Path

import mido
import pytest

from fl_studio_mcp.utils.project_bundle import (
    BundleError,
    MissingSamplesError,
    _parse,
    _publish,
    _vlq,
    inspect_project,
    pack_project,
)


def event(eid, payload):
    return bytes([eid]) + (_vlq(len(payload)) if eid >= 192 else b"") + payload


def text(eid, value):
    return event(eid, (value + "\0").encode("utf-16le"))


def note(position=48, channel=0, length=96, key=60, pan=64, fine=120, flags=0,
         velocity=100, color=0):
    return struct.pack("<IHHIHH8B", position, flags, channel, length, key, 0,
                       fine, 0, 64, color, pan, velocity, 128, 128)


def project(events=b"", *, notes=None, version="26.1.2", ppq=96, playlist=None):
    raw = (event(199, version.encode() + b"\0") + event(156, struct.pack("<I", 125000))
           + event(65, struct.pack("<H", 2)) + text(193, "After the last light")
           + event(224, note() if notes is None else notes)
           + event(64, struct.pack("<H", 0)) + text(192, "Lead"))
    if playlist is not None:
        raw += event(233, playlist)
    raw += events
    return (b"FLhd" + struct.pack("<IHHH", 6, 0, 1, ppq)
            + b"FLdt" + struct.pack("<I", len(raw)) + raw)


def source(tmp_path, data):
    path = tmp_path / "remake.flp"
    path.write_bytes(data)
    return path


def test_bundle_preserves_source_plugin_bytes_and_all_notes(tmp_path):
    sample = tmp_path / "kick.wav"
    sample.write_bytes(b"RIFF sample data")
    opaque = b"FLdt\x00FLhd\xffopaque\x00" * 500
    original = project(text(196, str(sample)) + event(213, opaque)
                       + text(202, "C:\\old-machine\\project"))
    path = source(tmp_path, original)
    manifest = pack_project(path, tmp_path / "bundle")
    assert path.read_bytes() == original
    root = tmp_path / "bundle"
    assert (root / "original/remake.flp").read_bytes() == original
    _, before, _ = _parse(original)
    _, after, _ = _parse((root / "remake.flp").read_bytes())
    assert len(before) == len(after)
    assert all(a.encoded == b.encoded for a, b in zip(before, after) if a.id not in (196, 202))
    info = inspect_project(root / "remake.flp")
    relative = info["sample_references"][0]["path"]
    assert ":" not in relative and "/" not in relative and "\\" not in relative
    assert (root / relative).read_bytes() == sample.read_bytes()
    assert manifest["rewritten_data_paths"] == 1
    for item in manifest["files"]:
        assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert json.loads((root / "manifest.json").read_text())["missing_samples"] == []


def test_all_pattern_channel_midis_preserve_timing_velocity_and_pan(tmp_path):
    notes = note(48, length=101, pan=32, color=3) + note(170, channel=5, velocity=77)
    data = project(event(65, struct.pack("<H", 3)) + text(193, "Teeth")
                   + event(224, note(100, key=75)), notes=notes)
    manifest = pack_project(source(tmp_path, data), tmp_path / "bundle")
    assert len(manifest["midi_files"]) == 3
    entry = next(m for m in manifest["midi_files"] if m["pattern"] == 2 and m["channel"] == 0)
    midi = mido.MidiFile(tmp_path / "bundle" / entry["path"], charset="utf8")
    assert midi.ticks_per_beat == 96
    tick = 0
    messages = []
    for message in midi.tracks[1]:
        tick += message.time
        messages.append((tick, message))
    assert any(t == 48 and m.type == "note_on" and m.velocity == 100
               and m.note == 60 and m.channel == 3 for t, m in messages)
    assert any(t == 149 and m.type == "note_off" for t, m in messages)
    assert any(t == 48 and m.type == "control_change" and m.control == 10
               and m.value == 32 for t, m in messages)


def test_same_sample_basenames_do_not_collide_and_duplicates_deduplicate(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "kick.wav").write_bytes(b"A")
    (b / "kick.wav").write_bytes(b"B")
    data = project(text(196, str(a / "kick.wav")) + text(196, str(b / "kick.wav"))
                   + text(196, str(a / "kick.wav")))
    result = pack_project(source(tmp_path, data), tmp_path / "bundle")
    assert result["unique_samples"] == 2
    assert len(result["sample_references"]) == 3
    assert result["sample_references"][0]["path"] == result["sample_references"][2]["path"]
    assert result["sample_references"][0]["path"] != result["sample_references"][1]["path"]


@pytest.mark.parametrize("reference", ["../secret.wav", "samples/../../secret.wav",
                                       "C:secret.wav", "%Unknown%/kick.wav", "absent.wav"])
def test_invalid_or_missing_dependencies_fail_before_output(tmp_path, reference):
    path = source(tmp_path, project(text(196, reference)))
    with pytest.raises(MissingSamplesError) as error:
        pack_project(path, tmp_path / "bundle")
    assert len(error.value.missing) == 1
    assert not (tmp_path / "bundle").exists()
    assert not list(tmp_path.glob(".fl-bundle-*"))


def test_explicit_factory_variables_and_relative_files(tmp_path):
    (tmp_path / "kick.wav").write_bytes(b"audio")
    data = project(text(196, "%FLStudioFactoryData%/kick.wav")
                   + text(196, "%FLProjectPath%/kick.wav") + text(196, "kick.wav"))
    result = pack_project(source(tmp_path, data), tmp_path / "bundle",
                          path_variables={"FLStudioFactoryData": tmp_path})
    assert result["unique_samples"] == 1


def test_ambiguous_search_roots_fail(tmp_path):
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
        (root / "kick.wav").write_bytes(b"audio")
    with pytest.raises(MissingSamplesError, match="Ambiguous"):
        pack_project(source(tmp_path, project(text(196, "kick.wav"))), tmp_path / "bundle",
                     search_roots=roots)


def test_existing_output_cannot_be_overwritten(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "user.txt"
    marker.write_text("Keep me")
    with pytest.raises(BundleError, match="already exist"):
        pack_project(source(tmp_path, project()), output)
    assert marker.read_text() == "Keep me"


def test_fl26_playlist_stride_88_and_unknown_bytes_preserved(tmp_path):
    clip = bytearray(88)
    struct.pack_into("<IHHIH", clip, 0, 768, 20480, 20482, 6144, 498)
    struct.pack_into("<ff", clip, 24, 12.0, 0.0)
    info = inspect_project(source(tmp_path, project(playlist=bytes(clip))))
    assert info["clips"] == [dict(position=768, length=6144, track=2, index=20482,
                                   pattern=2, start_offset=12.0, end_offset=0.0,
                                   offset_bytes=struct.pack("<ff", 12.0, 0.0).hex())]


def test_pattern_clip_sentinels_roundtrip_bit_exact_without_nan_json(tmp_path):
    clip = bytearray(88)
    struct.pack_into("<IHHIH", clip, 0, 0, 20480, 20482, 18432, 499)
    clip[24:32] = b"\xff" * 8
    path = source(tmp_path, project(playlist=bytes(clip)))
    before = inspect_project(path)
    result = pack_project(path, tmp_path / "bundle")
    after = inspect_project(tmp_path / "bundle" / result["portable_flp"])
    assert before["clips"] == after["clips"]
    assert after["clips"][0]["start_offset"] is None
    assert after["clips"][0]["offset_bytes"] == "ffffffffffffffff"
    note_json = (tmp_path / "bundle/project-notes.json").read_text()
    assert "NaN" not in note_json and "Infinity" not in note_json
    assert json.loads(note_json)["clips"][0]["end_offset"] is None
    before_playlist = next(e for e in _parse(path.read_bytes())[1] if e.id == 233)
    after_playlist = next(e for e in _parse((tmp_path / "bundle/remake.flp").read_bytes())[1]
                          if e.id == 233)
    assert before_playlist.encoded == after_playlist.encoded


@pytest.mark.parametrize("data", [b"", b"FLhd\0", project()[:-1],
                                   project(notes=b"bad"), project(playlist=b"bad"),
                                   project(event(250, b"a"))[:-2], project(version="10.0.0")])
def test_malformed_project_rejected(tmp_path, data):
    with pytest.raises(BundleError):
        inspect_project(source(tmp_path, data))


def test_unrepresentable_note_features_are_reported_not_silently_lost(tmp_path):
    notes = (note(0, length=0, flags=8, fine=130, velocity=128)
             + note(0, key=132) + note(0, key=65, pan=0))
    result = pack_project(source(tmp_path, project(notes=notes)), tmp_path / "bundle")
    item = result["midi_files"][0]
    assert item["source_notes"] == 3 and item["exported_notes"] == 2
    issues = item["limitations"]
    assert issues["notes_outside_midi_range_omitted"] == 1
    assert issues["slide_notes_exported_as_ordinary_notes"] == 1
    assert issues["per_note_fine_pitch_not_encoded"] == 1
    assert issues["zero_length_steps_exported_as_sixteenth_notes"] == 1
    assert issues["velocity_or_release_clamped_to_127"] == 1
    assert issues["overlapping_different_pan_is_channel_wide_in_midi"] == 1


def test_fl25_three_byte_172_does_not_swallow_next_event(tmp_path):
    banner = text(192, "FL Studio 26.1.4.5589.5589")
    raw = (event(199, b"26.1.4.5589\0") + bytes([172, 1, 1, 0]) + banner
           + event(156, struct.pack("<I", 125000))
           + event(64, struct.pack("<H", 0)) + text(203, "Main Lead")
           + event(65, struct.pack("<H", 2)) + event(224, note()))
    data = b"FLhd" + struct.pack("<IHHH", 6, 0, 1, 96)
    data += b"FLdt" + struct.pack("<I", len(raw)) + raw
    info = inspect_project(source(tmp_path, data))
    assert info["tempo"] == 125
    assert info["channels"] == [dict(id=0, name="Main Lead")]
    _, events, _ = _parse(data)
    assert b"".join(e.encoded for e in events) == raw
    assert next(e for e in events if e.id == 172).payload == b"\1\1\0"


def test_non_audio_files_cannot_be_smuggled_as_sample_references(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("not an audio file")
    with pytest.raises(MissingSamplesError, match="unsupported"):
        pack_project(source(tmp_path, project(text(196, str(secret)))), tmp_path / "bundle")


def test_transient_windows_directory_lock_is_retried(tmp_path, monkeypatch):
    staging, destination = tmp_path / "stage", tmp_path / "bundle"
    staging.mkdir()
    (staging / "file").write_bytes(b"saved")
    original = Path.rename
    attempts = []

    def locked_once(self, target):
        attempts.append(target)
        if len(attempts) == 1:
            raise PermissionError("transient scanner handle")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", locked_once)
    monkeypatch.setattr("fl_studio_mcp.utils.project_bundle.time.sleep", lambda _: None)
    _publish(staging, destination)
    assert len(attempts) == 2
    assert (destination / "file").read_bytes() == b"saved"
