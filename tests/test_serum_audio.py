"""Synthetic measurements: no audio device or running FL Studio required."""

import math
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")
pytest.importorskip("scipy")

from fl_studio_mcp.utils import serum_audio  # noqa: E402


def write_tone(tmp_path, name="tone.wav", hz=55, gain=0.8, seconds=1, stereo=False):
    rate = 24000
    t = np.arange(int(rate * seconds)) / rate
    x = gain * np.sin(2 * np.pi * hz * t)
    if stereo:
        x = np.column_stack((x, -x))
    path = tmp_path / name
    sf.write(path, x, rate, subtype="FLOAT")
    return path


def test_periodic_bass_and_harmonics(tmp_path):
    result = serum_audio.analyze_audio(write_tone(tmp_path))["audio"]
    assert result["pitch_median_hz"] == pytest.approx(55, abs=0.1)
    assert result["rms"] == pytest.approx(0.8 / math.sqrt(2), abs=1e-5)
    assert result["tail_harmonics"]["amplitude_ratios"][2] < 0.01
    assert result["clipped_fraction"] == 0


def test_antiphase_stereo_does_not_cancel(tmp_path):
    result = serum_audio.analyze_audio(write_tone(tmp_path, stereo=True))["audio"]
    assert result["channels"] == 2
    assert result["pitch_median_hz"] == pytest.approx(55, abs=0.1)
    assert not result["silent"]


def test_gain_comparison_preserves_quietness(tmp_path):
    loud = write_tone(tmp_path, "loud.wav")
    quiet = write_tone(tmp_path, "quiet.wav", gain=0.08)
    result = serum_audio.analyze_audio(quiet, loud)
    assert result["comparison"]["rms_gain_db_to_match_reference"] == pytest.approx(20, abs=0.001)
    assert result["comparison"]["gain_applied"] is False
    assert result["audio"]["rms"] < result["reference"]["rms"] / 9
    assert result["comparison"]["pitch_difference_cents"] == pytest.approx(0, abs=0.01)


def test_gain_comparison_ignores_different_silence_padding(tmp_path):
    original = write_tone(tmp_path, seconds=0.4)
    audio, rate = sf.read(original)
    padded = tmp_path / "padded.wav"
    sf.write(padded, np.r_[np.zeros(rate // 4), audio, np.zeros(rate)], rate, subtype="FLOAT")
    result = serum_audio.analyze_audio(padded, original)
    assert result["audio"]["rms"] < result["reference"]["rms"] * 0.6
    assert result["audio"]["active_rms"] == pytest.approx(result["reference"]["active_rms"])
    assert result["audio"]["active_rms_dbfs"] == pytest.approx(
        result["reference"]["active_rms_dbfs"]
    )
    assert result["comparison"]["rms_gain_db_to_match_reference"] == pytest.approx(0, abs=1e-8)
    assert result["comparison"]["rms_gain_basis"].startswith("active_rms")


def test_silence_has_no_pitch_or_fake_gain_match(tmp_path):
    path = write_tone(tmp_path, gain=0)
    result = serum_audio.analyze_audio(path, path)
    assert result["audio"]["silent"]
    assert result["audio"]["pitch_median_hz"] is None
    assert result["audio"]["tail_harmonics"] is None
    assert result["audio"]["active_duration_seconds"] == 0
    assert result["comparison"]["rms_gain_db_to_match_reference"] is None
    assert not result["comparison"]["comparable_non_silent"]


def test_short_audio_is_valid_but_not_a_reliable_bass_pitch(tmp_path):
    result = serum_audio.analyze_audio(write_tone(tmp_path, seconds=0.001))["audio"]
    assert result["pitch_median_hz"] is None
    assert result["duration_seconds"] == pytest.approx(0.001)


def test_activity_excludes_padding_and_clipping_is_counted(tmp_path):
    wave = np.clip(4 * np.sin(2 * np.pi * 100 * np.arange(12000) / 24000), -1, 1)
    path = tmp_path / "clipped.wav"
    sf.write(path, np.r_[np.zeros(2400), wave, np.zeros(4800)], 24000, subtype="FLOAT")
    result = serum_audio.analyze_audio(path)["audio"]
    assert result["active_start_seconds"] == pytest.approx(0.1, abs=0.001)
    assert result["active_duration_seconds"] == pytest.approx(0.5, abs=0.001)
    assert result["clipped_fraction"] > 0.4
    assert result["tail_harmonics"]["amplitude_ratios"][2] > 0.15


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", True),
        ("channel", -1),
        ("note", 128),
        ("note", 3.2),
        ("velocity", 0),
        ("velocity", 128),
        ("hold_seconds", math.nan),
        ("hold_seconds", 8),
        ("hold_seconds", True),
    ],
)
def test_bad_probe_arguments_fail_before_connection(tmp_path, monkeypatch, field, value):
    def forbidden():
        pytest.fail("Invalid input must not connect to FL")

    monkeypatch.setattr(serum_audio, "get_connection", forbidden)
    params = dict(channel=3, output_path=tmp_path / "probe.wav")
    params[field] = value
    with pytest.raises(ValueError):
        serum_audio.capture_serum_probe(**params)


def test_existing_destination_is_never_overwritten(tmp_path):
    path = write_tone(tmp_path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        serum_audio.capture_serum_probe(3, path)
    assert path.read_bytes() == before


def test_nonfinite_audio_rejected(tmp_path):
    path = tmp_path / "bad.wav"
    sf.write(path, np.array([0.0, np.nan]), 24000, subtype="FLOAT")
    with pytest.raises(ValueError, match="finite"):
        serum_audio.analyze_audio(path)


@pytest.mark.parametrize("fail_off", [False, True])
def test_note_on_failure_still_sends_note_off_and_removes_output(tmp_path, monkeypatch, fail_off):
    calls = []

    def command(action, params, timeout):
        if action == "transport.getStatus":
            return {"success": True, "is_playing": False, "is_recording": False}
        if action == "native.call":
            return {"success": True, "value": "Serum 2"}
        calls.append(params["velocity"])
        if params["velocity"] or fail_off:
            raise TimeoutError("simulated unknown delivery")
        return {"success": True}

    class Stream:
        def is_active(self):
            return True

        def stop_stream(self):
            pass

        def close(self):
            pass

    class Audio:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_default_wasapi_loopback(self):
            return {"name": "fake", "defaultSampleRate": 24000, "maxInputChannels": 2, "index": 0}

        def open(self, **kwargs):
            return Stream()

    monkeypatch.setattr(serum_audio, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(serum_audio, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(serum_audio, "transaction_lock", lambda *_: nullcontext())
    monkeypatch.setattr(
        serum_audio,
        "get_connection",
        lambda: SimpleNamespace(_hardware_dir=tmp_path, send_command=command),
    )
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", SimpleNamespace(PyAudio=Audio, paFloat32=1))
    path = tmp_path / "failed.wav"
    exception, message = (RuntimeError, "Note-off") if fail_off else (TimeoutError, "simulated")
    with pytest.raises(exception, match=message):
        serum_audio.capture_serum_probe(3, path)
    assert calls == [100, 0]
    assert not path.exists()
