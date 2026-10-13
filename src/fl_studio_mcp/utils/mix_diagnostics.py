"""Explain measured mix constraints without guessing causes or changing audio."""

from __future__ import annotations

import json
import math
from pathlib import Path

from fl_studio_mcp.utils.audio_files import file_hash
from fl_studio_mcp.utils.mix_analysis import compare_audio, measure_loudness

_LOUDNESS_TOLERANCE_LU = 0.1


def _number(value, name, lower, upper):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite in {lower}..{upper}")


def _options(target_lufs, true_peak_ceiling_dbtp, activity_dbfs, timeout_seconds, dual_mono):
    if target_lufs is not None:
        _number(target_lufs, "target_lufs", -70, 0)
    _number(true_peak_ceiling_dbtp, "true_peak_ceiling_dbtp", -24, 0)
    _number(activity_dbfs, "activity_dbfs", -120, -6)
    _number(timeout_seconds, "timeout_seconds", 1, 300)
    if type(dual_mono) is not bool:
        raise ValueError("dual_mono must be a boolean")


def _path(value, name):
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\0" in str(value):
        raise ValueError(f"{name} must be an absolute local audio file path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute local audio file path")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{name} must be a local regular file")
    return path


def _validate_measurement(measurement):
    if measurement.get("success") is not True or measurement.get("source_unchanged") is not True:
        raise RuntimeError("Diagnosis requires a successful, source-verified audio measurement")
    try:
        json.dumps(measurement, allow_nan=False)
        loudness = measurement["loudness"]
        if loudness["status"] not in {"measured", "digital_silence", "too_short", "below_gate"}:
            raise ValueError("Unknown loudness status")
        for key in ("integrated_lufs", "true_peak_dbtp"):
            value = loudness[key]
            if value is not None:
                _number(value, key, -1000, 1000)
        counts = measurement["full_scale_samples_per_channel"]
        if not isinstance(counts, list) or len(counts) != measurement["channels"]:
            raise ValueError("Invalid per-channel sample counts")
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("Invalid full-scale sample count")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Audio measurement is incomplete or non-finite") from exc


def diagnose_measurements(candidate, *, reference=None, target_lufs=None,
                          true_peak_ceiling_dbtp=-1.0):
    """Pure plan from measured audio. No track-role, spectral or audibility inference."""
    _options(target_lufs, true_peak_ceiling_dbtp, -60, 120, False)
    if reference is not None and target_lufs is not None:
        raise ValueError("Use reference_path or target_lufs, not both")
    _validate_measurement(candidate)
    if reference is not None:
        _validate_measurement(reference)

    loudness = candidate["loudness"]
    current_lufs = loudness["integrated_lufs"]
    peak = loudness["true_peak_dbtp"]
    desired_lufs = reference["loudness"]["integrated_lufs"] if reference else target_lufs
    findings, proposals = [], []

    def finding(rule, severity, message, evidence):
        findings.append(dict(rule=rule, severity=severity, message=message, evidence=evidence))

    if loudness["status"] != "measured":
        finding(
            "loudness_unavailable", "warning",
            "Integrated loudness is unavailable; no loudness-matching gain can be inferred.",
            dict(status=loudness["status"], reasons=loudness.get("reasons", [])),
        )
    if reference is not None and desired_lufs is None:
        finding(
            "reference_loudness_unavailable", "warning",
            "The reference has no measurable integrated loudness; "
            "choose measurable programme audio.",
            dict(status=reference["loudness"]["status"], path=reference["path"]),
        )

    full_scale = candidate["full_scale_samples_per_channel"]
    if any(full_scale):
        finding(
            "full_scale_samples", "warning",
            "Samples reach or exceed digital full scale. Inspect for unintended distortion; "
            "these counts alone do not prove audible clipping.",
            dict(samples_per_channel=full_scale, sample_peak_dbfs=candidate["peak_dbfs"]),
        )

    peak_budget = true_peak_ceiling_dbtp - peak if peak is not None else None
    if peak_budget is not None and peak_budget < -1e-9:
        finding(
            "true_peak_above_ceiling", "warning",
            "Measured true peak exceeds the explicitly requested ceiling.",
            dict(true_peak_dbtp=peak, ceiling_dbtp=true_peak_ceiling_dbtp,
                 excess_db=round(-peak_budget, 6)),
        )

    required_gain = (
        desired_lufs - current_lufs
        if desired_lufs is not None and current_lufs is not None else None
    )
    if required_gain is not None and abs(required_gain) > _LOUDNESS_TOLERANCE_LU:
        finding(
            "loudness_target_difference", "info",
            "Measured integrated loudness differs from the requested target.",
            dict(integrated_lufs=current_lufs, target_lufs=desired_lufs,
                 target_minus_measured_lu=round(required_gain, 6),
                 tolerance_lu=_LOUDNESS_TOLERANCE_LU),
        )

    # A post-render constant-gain estimate is meaningful; a diagnosis cannot
    # identify which mixer fader or processor produced the measured waveform.
    target_needs_gain = required_gain is not None and abs(required_gain) > _LOUDNESS_TOLERANCE_LU
    if peak_budget is not None and (target_needs_gain or peak_budget < -1e-9):
        gain = min(required_gain if required_gain is not None else 0.0, peak_budget)
        predicted_lufs = current_lufs + gain if current_lufs is not None else None
        residual = desired_lufs - predicted_lufs if (
            desired_lufs is not None and predicted_lufs is not None
        ) else None
        proposals.append(dict(
            action="constant_gain_on_output_copy",
            gain_db=round(gain, 6),
            requested_gain_db=round(required_gain, 6) if required_gain is not None else None,
            predicted_integrated_lufs=predicted_lufs,
            predicted_true_peak_dbtp=peak + gain,
            residual_to_target_lu=residual,
            applied=False,
            requires_remeasurement=True,
            instruction="Audition gain on a new output copy and remeasure the result. "
                        "No mixer fader or plugin target is inferred.",
        ))
        if residual is not None and residual > _LOUDNESS_TOLERANCE_LU:
            finding(
                "target_exceeds_peak_budget", "warning",
                "Constant gain cannot reach the loudness target while respecting the peak ceiling.",
                dict(required_gain_db=required_gain, available_gain_db=peak_budget,
                     peak_limited_integrated_lufs=predicted_lufs, residual_to_target_lu=residual),
            )
            proposals.append(dict(
                action="review_peak_to_loudness_ratio",
                instruction="Choose a lower loudness target or audition intentional mix/dynamics "
                            "changes, then render and remeasure. No processing is prescribed.",
                minimum_peak_reduction_for_target_db=residual,
                applied=False,
                requires_remeasurement=True,
            ))

    return dict(
        success=True,
        measurement=candidate,
        reference=reference,
        constraints=dict(
            target_lufs=desired_lufs,
            target_source="reference" if reference is not None else (
                "explicit" if target_lufs is not None else "none"
            ),
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            loudness_tolerance_lu=_LOUDNESS_TOLERANCE_LU,
        ),
        findings=findings,
        proposals=proposals,
        summary=dict(
            warning_count=sum(item["severity"] == "warning" for item in findings),
            information_count=sum(item["severity"] == "info" for item in findings),
            within_peak_ceiling=peak_budget >= -1e-9 if peak_budget is not None else None,
            within_loudness_tolerance=abs(required_gain) <= _LOUDNESS_TOLERANCE_LU
            if required_gain is not None else None,
        ),
        limitations=[
            "File measurements describe the rendered audio, not individual mixer tracks or causes.",
            "No EQ, compression, genre, track role, spectral balance or artistic quality "
            "is inferred.",
            "The peak ceiling and loudness target are explicit constraints, "
            "not delivery standards.",
            "Constant-gain predictions can change EBU R128 gate participation; "
            "remeasure processed audio.",
            "Full-scale samples and true-peak estimates alone do not establish audible distortion.",
        ],
    )


def diagnose_audio(path, reference_path=None, target_lufs=None, true_peak_ceiling_dbtp=-1.0,
                   activity_dbfs=-60.0, timeout_seconds=120.0, dual_mono=False):
    """Read bounded local files and produce a source-verified diagnosis and plan."""
    _options(target_lufs, true_peak_ceiling_dbtp, activity_dbfs, timeout_seconds, dual_mono)
    if reference_path is not None and target_lufs is not None:
        raise ValueError("Use reference_path or target_lufs, not both")
    source = _path(path, "path")
    reference_source = (
        _path(reference_path, "reference_path") if reference_path is not None else None
    )
    comparison = None
    if reference_source is not None:
        comparison = compare_audio(
            source, reference_source, true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            activity_dbfs=activity_dbfs, timeout_seconds=timeout_seconds, dual_mono=dual_mono,
        )
        candidate, reference = comparison["candidate"], comparison["reference"]
    else:
        candidate = measure_loudness(source, activity_dbfs, timeout_seconds, dual_mono)
        reference = None
    report = diagnose_measurements(
        candidate, reference=reference, target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
    )
    if comparison is not None:
        report["comparison"] = dict(
            deltas=comparison["deltas"], delta_convention=comparison["delta_convention"],
            warnings=comparison["warnings"],
        )
        report["limitations"].append(
            "Reference measurements are whole-file comparisons without time alignment."
        )
    for measured, expected_path in ((candidate, source), (reference, reference_source)):
        if measured is not None and (
            Path(measured["path"]).resolve() != expected_path
            or file_hash(expected_path) != measured["sha256"]
        ):
            raise RuntimeError("Audio changed during diagnosis; discard this report")
    report["source_unchanged"] = True
    json.dumps(report, allow_nan=False)
    return report
