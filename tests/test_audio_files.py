import math

import numpy as np
import pytest
import soundfile as sf

from fl_studio_mcp.utils.audio_files import edit_audio, file_hash, inspect_audio


def wav(tmp_path, data, rate=8000):
    path = tmp_path / "source.wav"
    sf.write(path, data, rate, subtype="FLOAT")
    return path


def test_stereo_antiphase_is_audible_with_known_levels(tmp_path):
    x = 0.5 * np.sin(2 * np.pi * 100 * np.arange(16000) / 8000)
    path = wav(tmp_path, np.column_stack([x, -x]))
    result = inspect_audio(path)
    assert not result["silent"]
    assert result["duration_seconds"] == 2
    assert result["rms_dbfs"] == pytest.approx(20 * math.log10(0.5 / math.sqrt(2)), abs=1e-6)
    assert result["peak_dbfs"] == pytest.approx(-6.0206, abs=1e-4)
    assert result["stereo_correlation"] == pytest.approx(-1)
    assert len(result["timeline"]) == 2


def test_streamed_long_file_and_absolute_activity_span(tmp_path):
    x = np.zeros(8000 * 121 + 7)
    x[13], x[-2] = 0.25, -0.25
    result = inspect_audio(wav(tmp_path, x))
    assert len(result["timeline"]) == 122
    assert result["active_start_frame"] == 13
    assert result["active_end_frame"] == len(x) - 1


def test_digital_silence_has_json_safe_levels(tmp_path):
    result = inspect_audio(wav(tmp_path, np.zeros((80, 2))))
    assert result["silent"] and result["peak_dbfs"] is None
    assert result["stereo_correlation"] is None


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_audio_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="NaN|infinite"):
        inspect_audio(wav(tmp_path, [0, value]))


def test_trim_reverse_fades_and_gain_have_exact_order(tmp_path):
    path = wav(tmp_path, np.arange(10) / 10)
    digest = file_hash(path)
    output = tmp_path / "edited.wav"
    report = edit_audio(
        path,
        output,
        digest,
        2,
        8,
        gain_db=-6.020599913,
        fade_in_frames=2,
        fade_out_frames=2,
        reverse=True,
        subtype="FLOAT",
        dry_run=False,
    )
    actual, rate = sf.read(output)
    np.testing.assert_allclose(actual, [0, 0.3, 0.25, 0.2, 0.15, 0], atol=1e-7)
    assert rate == 8000 and report["sample_readback_verified"]
    assert file_hash(path) == digest
    with pytest.raises(ValueError, match="must not exist"):
        edit_audio(path, output, digest, dry_run=False)


def test_preview_does_not_write_and_normalization_uses_postfade_peak(tmp_path):
    path = wav(tmp_path, [0.9, 0.2, 0.3, 0.1])
    output = tmp_path / "normalized.wav"
    report = edit_audio(
        path, output, file_hash(path), fade_in_frames=2, normalize_peak_dbfs=-6, dry_run=True
    )
    assert report["predicted_peak_dbfs"] == pytest.approx(-6)
    assert not output.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(start_frame=True),
        dict(end_frame=100),
        dict(fade_in_frames=1),
        dict(fade_in_frames=3, fade_out_frames=3),
        dict(reverse=1),
        dict(gain_db=float("nan")),
        dict(gain_db=20),
        dict(gain_db=2, normalize_peak_dbfs=-3),
        dict(subtype="PCM_32"),
    ],
)
def test_invalid_or_clipping_edits_never_create_output(tmp_path, kwargs):
    path = wav(tmp_path, [0.25] * 4)
    output = tmp_path / "bad.wav"
    with pytest.raises(ValueError):
        edit_audio(path, output, file_hash(path), dry_run=False, **kwargs)
    assert not output.exists()


def test_hash_guard_rejects_changed_source(tmp_path):
    path = wav(tmp_path, [0.25] * 4)
    with pytest.raises(ValueError, match="hash"):
        edit_audio(path, tmp_path / "out.wav", "0" * 64, dry_run=False)
