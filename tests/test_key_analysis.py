"""Key estimates, WAV decoding and dependency boundaries; no FL or real media."""

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from fl_studio_mcp.utils import key_analysis as ka

MAJOR = [(60, 64, 67), (65, 69, 72), (67, 71, 74), (60, 64, 67)] * 2
MINOR = [(57, 60, 64), (62, 65, 69), (64, 68, 71), (57, 60, 64)] * 2


def write_wav(path, audio, rate=22050, bits=16, floating=False, extensible=False):
    samples = np.asarray(audio).reshape(len(audio), -1)
    channels = samples.shape[1]
    width = bits // 8
    if floating:
        raw = samples.astype(f"<f{width}").tobytes()
    elif bits == 8:
        raw = np.clip(np.rint(samples * 128 + 128), 0, 255).astype("u1").tobytes()
    else:
        values = np.rint(samples * (2**(bits - 1) - 1)).astype("<i4")
        if bits == 24:
            raw = values.reshape(-1).view("u1").reshape(-1, 4)[:, :3].copy().tobytes()
        else:
            raw = values.astype(f"<i{width}").tobytes()
    kind = 3 if floating else 1
    fmt = struct.pack("<HHIIHH", 0xFFFE if extensible else kind, channels, rate,
                      rate * channels * width, channels * width, bits)
    if extensible:
        guid = struct.pack("<I", kind) + bytes.fromhex("00001000800000aa00389b71")
        fmt += struct.pack("<HHI", 22, bits, 0) + guid
    junk = b"JUNK" + struct.pack("<I", 3) + b"abc\0"
    body = b"WAVE" + junk + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(raw)) + raw + (b"\0" if len(raw) % 2 else b"")
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


@pytest.mark.parametrize("mode,profile", [("major", ka.MAJOR_PROFILE), ("minor", ka.MINOR_PROFILE)])
@pytest.mark.parametrize("tonic", range(12))
def test_all_profile_rotations_have_correct_tonic_and_unit_correlation(mode, profile, tonic):
    result = ka.rank_chroma(np.roll(profile, tonic), top_n=24)
    assert result[0]["pitch_class"] == tonic
    assert result[0]["mode"] == mode
    assert result[0]["correlation"] == pytest.approx(1)
    assert sum(row["confidence"] for row in result) == pytest.approx(1)
    assert [r["correlation"] for r in result] == sorted(
        (r["correlation"] for r in result), reverse=True)


@pytest.mark.parametrize("chords,expected", [(MAJOR, "C major"), (MINOR, "A minor")])
@pytest.mark.parametrize("rate", [16000, 22050, 44100])
def test_synthesized_progression_ranks_the_expected_key(chords, expected, rate):
    result = ka.detect_key(ka._progression(chords, sample_rate=rate), rate)
    assert result[0]["key"] == expected
    assert len(result) == 5 and sum(row["confidence"] for row in result) < 1
    assert set(result[0]) == {"key", "pitch_class", "mode", "confidence", "correlation"}
    json.dumps(result, allow_nan=False)


def test_stereo_antiphase_and_gain_do_not_change_candidates():
    audio = ka._progression(MAJOR)
    reference = ka.detect_key(audio, 22050)
    for transformed in (audio * 1e-200, -audio * 1e200, np.column_stack((audio, -audio))):
        result = ka.detect_key(transformed, 22050)
        assert [r["key"] for r in result] == [r["key"] for r in reference]
        assert [r["correlation"] for r in result] == pytest.approx(
            [r["correlation"] for r in reference])


@pytest.mark.parametrize("length", [1, 4095, 4096, 4097, 8192, 65536 + 777])
def test_stft_matches_independent_frame_loop_across_block_boundaries(length):
    rate = 22050
    audio = np.random.default_rng(12).normal(size=(length, 2))
    observed = ka._chroma((audio[i:i + 777] for i in range(0, length, 777)), rate)
    expected = np.zeros(12)
    freq = np.arange(2049) * rate / 4096
    keep = (freq >= 55) & (freq <= 5000)
    pitch = np.floor(69 + 12 * np.log2(freq[keep] / 440) + 0.5).astype(int) % 12
    count = max(1, 1 + int(np.ceil((length - 4096) / 1024)))
    for i in range(count):
        frame = audio[i * 1024:i * 1024 + 4096].copy()
        frame -= frame.mean(axis=0)
        frame = np.pad(frame, ((0, 4096 - len(frame)), (0, 0)))
        spectrum = np.abs(np.fft.rfft(frame * np.hanning(4096)[:, None], axis=0)).sum(axis=1)
        for pc in range(12):
            expected[pc] += spectrum[keep][pitch == pc].sum()
    if expected.sum():
        expected /= expected.sum()
    np.testing.assert_allclose(observed, expected, atol=2e-14)


@pytest.mark.parametrize("bits", [8, 16, 24, 32])
def test_pcm_wav_formats_are_read_without_changing_the_file(tmp_path, bits):
    source = write_wav(tmp_path / "progression.wav", ka._progression(MAJOR), bits=bits)
    before = hashlib.sha256(source.read_bytes()).digest()
    assert ka.detect_key(source)[0]["key"] == "C major"
    assert hashlib.sha256(source.read_bytes()).digest() == before


@pytest.mark.parametrize("bits", [32, 64])
@pytest.mark.parametrize("extensible", [False, True])
def test_float_wav_formats_match_array_input(tmp_path, bits, extensible):
    audio = ka._progression(MINOR)
    source = write_wav(tmp_path / "float.wav", audio, floating=True,
                       bits=bits, extensible=extensible)
    actual = ka.detect_key(source)
    expected = ka.detect_key(audio.astype(f"f{bits // 8}"), 22050)
    assert actual == expected


@pytest.mark.parametrize("length", [17, 4096, 8193])
def test_silence_and_constant_dc_have_no_tonal_evidence(length):
    assert ka.detect_key(np.zeros(length), 22050) == []
    assert ka.detect_key(np.full(length, 0.25), 22050) == []
    assert ka.rank_chroma(np.ones(12)) == []


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_audio_is_rejected_in_arrays_and_files(tmp_path, bad):
    audio = np.zeros(5000)
    audio[-1] = bad
    with pytest.raises(ValueError, match="NaN|infinite"):
        ka.detect_key(audio, 22050)
    path = write_wav(tmp_path / "bad.wav", audio, floating=True, bits=32)
    with pytest.raises(ValueError, match="NaN|infinite"):
        ka.detect_key(path)


@pytest.mark.parametrize("audio,rate", [
    (np.zeros(0), 22050), (np.zeros((2, 9)), 22050), (np.zeros((2, 0)), 22050),
    (np.zeros((2, 2, 2)), 22050), (np.zeros(2, dtype=int), 22050),
    (np.zeros(2, dtype=complex), 22050), (np.zeros(2), None),
    (np.zeros(2), True), (np.zeros(2), 0), (np.zeros(2), 22050.5),
])
def test_invalid_array_inputs_are_rejected(audio, rate):
    with pytest.raises(ValueError):
        ka.detect_key(audio, rate)


@pytest.mark.parametrize("options", [
    {"top_n": 0}, {"top_n": 25}, {"top_n": True}, {"top_n": 1.5},
    {"temperature": 0}, {"temperature": -1}, {"temperature": float("nan")},
    {"temperature": float("inf")}, {"temperature": True},
])
def test_invalid_ranking_options_fail_before_reading_audio(options):
    with pytest.raises(ValueError):
        ka.detect_key("nonexistent-file.wav", **options)


def test_readonly_input_and_softmax_temperature():
    audio = ka._progression(MAJOR)
    before = audio.copy()
    audio.flags.writeable = False
    ka.detect_key(audio, 22050)
    np.testing.assert_array_equal(audio, before)
    sharp = ka.rank_chroma(ka.MAJOR_PROFILE, temperature=0.01)
    soft = ka.rank_chroma(ka.MAJOR_PROFILE, temperature=1)
    assert sharp[0]["confidence"] > soft[0]["confidence"]


@pytest.mark.parametrize("payload", [b"", b"RIFF\xff\xff\xff\xffWAVE", b"not a wave file"])
def test_malformed_wavs_are_rejected(tmp_path, payload):
    path = tmp_path / "bad.wav"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        ka.detect_key(path)


def test_truncated_pcm_and_unsupported_compression_are_rejected(tmp_path):
    path = write_wav(tmp_path / "test.wav", np.zeros(5000))
    original = path.read_bytes()
    path.write_bytes(original[:-8])
    with pytest.raises(ValueError, match="Truncated"):
        ka.detect_key(path)
    damaged = bytearray(original)
    fmt_offset = damaged.index(b"fmt ") + 8
    struct.pack_into("<H", damaged, fmt_offset, 6)
    path.write_bytes(damaged)
    with pytest.raises(ValueError, match="uncompressed"):
        ka.detect_key(path)


def test_standalone_main_runs_with_heavy_audio_dependencies_forbidden():
    module = Path(ka.__file__).resolve()
    script = """
import builtins, runpy, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'scipy', 'soundfile', 'librosa', 'torch'}:
        raise AssertionError('Unexpected heavy dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", script, str(module)],
                            capture_output=True, text=True, timeout=30, check=True)
    assert "C major: PASS" in result.stdout and "A minor: PASS" in result.stdout
