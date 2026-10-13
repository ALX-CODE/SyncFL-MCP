"""Evidence-based diagnosis, source preservation and real audio integration."""

import json
import math

import numpy as np
import pytest
import soundfile as sf

from fl_studio_mcp.tools.mix_diagnostics import register_mix_diagnostics_tools
from fl_studio_mcp.utils import mix_diagnostics as diagnosis
from fl_studio_mcp.utils.audio_files import file_hash
from fl_studio_mcp.utils.mix_analysis import find_ffmpeg


def measurement(*, lufs=-20.0, peak=-6.0, status="measured", full_scale=None):
    return dict(
        success=True, source_unchanged=True, path="measured.wav", sha256="a" * 64,
        channels=2, peak_dbfs=-6.1,
        full_scale_samples_per_channel=full_scale if full_scale is not None else [0, 0],
        loudness=dict(status=status, integrated_lufs=lufs, true_peak_dbtp=peak, reasons=[]),
    )


def rules(report):
    return {finding["rule"] for finding in report["findings"]}


def test_satisfied_constraints_do_not_manufacture_problems():
    result = diagnosis.diagnose_measurements(measurement(), target_lufs=-20)
    assert result["findings"] == result["proposals"] == []
    assert result["summary"]["within_loudness_tolerance"] is True
    assert result["summary"]["within_peak_ceiling"] is True
    assert result["constraints"]["target_source"] == "explicit"
    json.dumps(result, allow_nan=False)


def test_peak_violation_proposes_attenuation_not_plugin_or_fader_guess():
    result = diagnosis.diagnose_measurements(measurement(peak=2))
    assert rules(result) == {"true_peak_above_ceiling"}
    assert result["summary"]["within_peak_ceiling"] is False
    assert result["summary"]["within_loudness_tolerance"] is None
    plan = result["proposals"][0]
    assert plan["action"] == "constant_gain_on_output_copy"
    assert plan["gain_db"] == -3
    assert plan["predicted_true_peak_dbtp"] == -1
    assert plan["applied"] is False and plan["requires_remeasurement"] is True
    assert "track" not in plan and "plugin" not in plan


def test_target_that_exceeds_peak_budget_reports_residual_not_fake_match():
    result = diagnosis.diagnose_measurements(measurement(), target_lufs=-14)
    assert rules(result) == {"loudness_target_difference", "target_exceeds_peak_budget"}
    plan = result["proposals"][0]
    assert plan["requested_gain_db"] == 6
    assert plan["gain_db"] == 5
    assert plan["predicted_integrated_lufs"] == -15
    assert plan["residual_to_target_lu"] == 1
    assert result["proposals"][1]["minimum_peak_reduction_for_target_db"] == 1


@pytest.mark.parametrize("target, expected_gain", [(-18, 2), (-24, -4)])
def test_gain_can_match_loudness_when_peak_budget_permits(target, expected_gain):
    result = diagnosis.diagnose_measurements(measurement(), target_lufs=target)
    assert rules(result) == {"loudness_target_difference"}
    assert result["proposals"][0]["gain_db"] == expected_gain
    assert result["proposals"][0]["residual_to_target_lu"] == 0


@pytest.mark.parametrize("status, peak", [("digital_silence", None), ("below_gate", -90),
                                         ("too_short", -6)])
def test_unavailable_loudness_is_never_used_to_invent_gain(status, peak):
    result = diagnosis.diagnose_measurements(
        measurement(lufs=None, peak=peak, status=status), target_lufs=-14,
    )
    assert rules(result) == {"loudness_unavailable"}
    assert result["proposals"] == []
    assert result["summary"]["within_loudness_tolerance"] is None
    json.dumps(result, allow_nan=False)


def test_reference_uses_measured_loudness_and_retains_unavailable_status():
    result = diagnosis.diagnose_measurements(measurement(), reference=measurement(lufs=-18))
    assert result["constraints"]["target_source"] == "reference"
    assert result["proposals"][0]["gain_db"] == 2
    result = diagnosis.diagnose_measurements(
        measurement(), reference=measurement(lufs=None, peak=None, status="digital_silence"),
    )
    assert rules(result) == {"reference_loudness_unavailable"}
    assert result["proposals"] == []


def test_full_scale_counts_are_evidence_not_audible_distortion_verdict():
    result = diagnosis.diagnose_measurements(measurement(full_scale=[3, 0]))
    finding = result["findings"][0]
    assert finding["rule"] == "full_scale_samples"
    assert finding["evidence"]["samples_per_channel"] == [3, 0]
    assert "do not prove audible clipping" in finding["message"]


@pytest.mark.parametrize("kwargs", [
    dict(target_lufs=math.nan), dict(target_lufs=True), dict(target_lufs=-71),
    dict(target_lufs=1), dict(true_peak_ceiling_dbtp=math.inf),
    dict(true_peak_ceiling_dbtp=-25), dict(true_peak_ceiling_dbtp=1),
    dict(activity_dbfs=math.nan), dict(activity_dbfs=-121),
    dict(timeout_seconds=0), dict(timeout_seconds=301), dict(timeout_seconds=True),
    dict(dual_mono=1), dict(reference_path="not-read.wav", target_lufs=-14),
])
def test_invalid_options_are_rejected_before_file_access(kwargs):
    with pytest.raises(ValueError):
        diagnosis.diagnose_audio("not-read.wav", **kwargs)


@pytest.mark.parametrize("path", ["relative.wav", "", "bad\0.wav", 123])
def test_invalid_source_path_is_rejected(path):
    with pytest.raises(ValueError, match="absolute local"):
        diagnosis.diagnose_audio(path)


def test_conflicting_targets_and_invalid_measurements_fail_closed():
    with pytest.raises(ValueError, match="not both"):
        diagnosis.diagnose_measurements(measurement(), reference=measurement(), target_lufs=-14)
    with pytest.raises(RuntimeError, match="non-finite"):
        diagnosis.diagnose_measurements(measurement(peak=math.nan))
    with pytest.raises(RuntimeError, match="source-verified"):
        diagnosis.diagnose_measurements(dict(measurement(), source_unchanged=False))


def test_file_changed_after_measurement_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    source.write_bytes(b"original")
    data = dict(measurement(), path=str(source), sha256=file_hash(source))

    def mutate(*args):
        source.write_bytes(b"changed")
        return data

    monkeypatch.setattr(diagnosis, "measure_loudness", mutate)
    with pytest.raises(RuntimeError, match="changed during diagnosis"):
        diagnosis.diagnose_audio(str(source))


def test_tool_registration_keeps_read_only_schema_and_forwards_options(monkeypatch):
    registered = {}

    class Registry:
        def tool(self, **metadata):
            def register(fn):
                registered.update(function=fn, metadata=metadata)
                return fn
            return register

    register_mix_diagnostics_tools(Registry())
    seen = []
    monkeypatch.setattr(
        diagnosis, "diagnose_audio", lambda *args: seen.append(args) or {"ok": True},
    )
    assert registered["function"]("mix.wav", target_lufs=-16, dual_mono=True) == {"ok": True}
    assert seen[0] == ("mix.wav", None, -16, -1.0, -60.0, 120.0, True)
    assert registered["metadata"]["annotations"]["readOnlyHint"] is True


@pytest.fixture
def real_ffmpeg():
    try:
        return find_ffmpeg()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def write_tone(path, *, rate=48000, amplitude=0.2, antiphase=False):
    tone = amplitude * np.sin(2 * np.pi * 1000 * np.arange(4 * rate) / rate)
    audio = np.column_stack([tone, -tone if antiphase else tone])
    sf.write(path, audio, rate, subtype="FLOAT")
    return path


@pytest.mark.parametrize("rate", [8000, 48000])
def test_real_antiphase_audio_keeps_channel_energy_and_sources(tmp_path, real_ffmpeg, rate):
    source = write_tone(tmp_path / "anti.wav", rate=rate, antiphase=True)
    before = file_hash(source)
    files = sorted(tmp_path.iterdir())
    report = diagnosis.diagnose_audio(str(source), target_lufs=-18)
    assert report["measurement"]["silent"] is False
    assert report["measurement"]["stereo_correlation"] == pytest.approx(-1)
    assert report["measurement"]["loudness"]["status"] == "measured"
    assert "loudness_unavailable" not in rules(report)
    assert report["source_unchanged"] is True
    assert file_hash(source) == before and sorted(tmp_path.iterdir()) == files
    json.dumps(report, allow_nan=False)


def test_real_reference_reports_mismatches_and_measured_target(tmp_path, real_ffmpeg):
    source = write_tone(tmp_path / "source.wav", amplitude=0.2)
    reference = write_tone(tmp_path / "reference.wav", amplitude=0.4)
    hashes = {path: file_hash(path) for path in (source, reference)}
    report = diagnosis.diagnose_audio(str(source), reference_path=str(reference))
    assert report["constraints"]["target_source"] == "reference"
    assert report["proposals"][0]["gain_db"] == pytest.approx(6.02, abs=0.05)
    assert report["comparison"]["deltas"]["integrated_lufs"] == pytest.approx(-6.02, abs=0.05)
    assert all(file_hash(path) == digest for path, digest in hashes.items())
    assert report["source_unchanged"] is True
