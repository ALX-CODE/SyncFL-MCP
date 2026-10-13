"""Synthetic sources test measurement semantics without opening FL Studio."""

import json
import math
import os
import sys
import time

import numpy as np
import pytest
import soundfile as sf

from fl_studio_mcp.utils import mix_analysis as mix
from fl_studio_mcp.utils.audio_files import file_hash


def write_tone(tmp_path, name="tone.wav", amplitude=0.1, seconds=4, rate=48000, channels=2):
    signal = amplitude * np.sin(2 * np.pi * 1000 * np.arange(int(rate * seconds)) / rate)
    data = signal if channels == 1 else np.column_stack([signal] * channels)
    path = tmp_path / name
    sf.write(path, data, rate, subtype="FLOAT")
    return path


def diagnostic(**overrides):
    values = dict(input_i="-20.05", input_tp="-20.00", input_lra="0.00", input_thresh="-30.05")
    values.update(overrides)
    return "Input metadata {ignored}\n[Parsed_loudnorm_0 @ 000001ABC] \n" + json.dumps(values)


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    monkeypatch.setattr(mix, "find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(mix, "_run_ffmpeg", lambda *_: diagnostic())


@pytest.fixture
def real_ffmpeg():
    try:
        return mix.find_ffmpeg()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_parser_uses_input_statistics_only():
    text = diagnostic(output_i="12345", output_tp="nan", target_offset="inf")
    assert mix._parse_loudnorm(text) == dict(
        integrated_lufs=-20.05,
        true_peak_dbtp=-20,
        loudness_range_lu=0,
        relative_threshold_lufs=-30.05,
    )


@pytest.mark.parametrize("bad", ["nan", "inf", "-nan", "1e308", None, True, {}, []])
def test_invalid_measurements_fail_closed(bad):
    with pytest.raises(RuntimeError, match="invalid|complete"):
        mix._parse_loudnorm(diagnostic(input_i=bad))


@pytest.mark.parametrize("bad", ["", "{}", "{broken", diagnostic() + "\n" + diagnostic()])
def test_missing_malformed_or_ambiguous_filter_summary_is_rejected(bad):
    with pytest.raises(RuntimeError, match="complete|invalid"):
        mix._parse_loudnorm(bad)


def test_negative_infinity_is_json_safe_but_negative_lra_is_rejected():
    parsed = mix._parse_loudnorm(diagnostic(input_i="-inf", input_tp="-inf"))
    assert parsed["integrated_lufs"] is None and parsed["true_peak_dbtp"] is None
    json.dumps(parsed, allow_nan=False)
    with pytest.raises(RuntimeError, match="invalid"):
        mix._parse_loudnorm(diagnostic(input_lra="-1"))


def test_missing_ffmpeg_is_explicit(monkeypatch, tmp_path):
    monkeypatch.delenv("FL_STUDIO_MCP_FFMPEG", raising=False)
    monkeypatch.setattr(mix.shutil, "which", lambda _: None)
    monkeypatch.setattr(mix.Path, "home", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="FFmpeg is unavailable"):
        mix.find_ffmpeg()


def test_invalid_explicit_ffmpeg_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("FL_STUDIO_MCP_FFMPEG", "relative-ffmpeg.exe")
    with pytest.raises(RuntimeError, match="absolute"):
        mix.find_ffmpeg()


def test_explicit_ffmpeg_path_is_used(monkeypatch, tmp_path):
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"test placeholder; never executed")
    monkeypatch.setenv("FL_STUDIO_MCP_FFMPEG", str(executable))
    assert mix.find_ffmpeg() == str(executable.resolve())


def test_bounded_process_reports_error_and_timeout():
    with pytest.raises(RuntimeError, match=r"failed \(7\).*"):
        mix._run_ffmpeg([sys.executable, "-c", "import sys;sys.exit(7)"], 5)
    with pytest.raises(RuntimeError, match="exceeded"):
        mix._run_ffmpeg([sys.executable, "-c", "import time;time.sleep(5)"], 0.05)


def test_bounded_process_rejects_excessive_diagnostics():
    with pytest.raises(RuntimeError, match="diagnostic size limit"):
        mix._run_ffmpeg([sys.executable, "-c", "import sys;sys.stderr.write('x' * 400000)"], 5)


def _process_running(pid):
    if os.name == "nt":
        import ctypes as c
        from ctypes import wintypes as w

        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            assert c.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such process.
            return False
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            assert result in (0, 258)  # WAIT_OBJECT_0 or WAIT_TIMEOUT
            return result == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # An adopted Linux child may await its init reaper after it has stopped executing.
    status = mix.Path(f"/proc/{pid}/stat")
    return not (status.exists() and status.read_text().split(")", 1)[1].split()[0] == "Z")


@pytest.mark.parametrize("parent_exits", [False, True])
def test_bounded_process_cleans_inherited_stderr_and_descendants(
    tmp_path, monkeypatch, parent_exits
):
    child_pid = tmp_path / "child.pid"
    child = (
        "import os,time;from pathlib import Path;"
        f"Path({str(child_pid)!r}).write_text(str(os.getpid()));time.sleep(30)"
    )
    command = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        + ("" if parent_exits else "time.sleep(30)")
    )
    spawned, popen = [], mix.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(mix.subprocess, "Popen", capture)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exceeded 3 seconds"):
        mix._run_ffmpeg([sys._base_executable, "-c", command], 3)
    assert time.monotonic() - started < 6
    assert spawned[0].poll() is not None and spawned[0].stderr.closed
    assert child_pid.exists(), "The test must exercise an actual inherited pipe writer"
    pid, deadline = int(child_pid.read_text()), time.monotonic() + 1
    # Windows job accounting reaches zero just before process handles become signaled.
    while _process_running(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _process_running(pid)


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended-start containment")
def test_process_is_cleaned_if_windows_job_assignment_fails(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-execute"
    spawned, popen = [], mix.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process

    def reject(self, process):
        raise RuntimeError("job assignment failed")

    monkeypatch.setattr(mix.subprocess, "Popen", capture)
    monkeypatch.setattr(mix._WindowsProcessJob, "attach_and_resume", reject)
    with pytest.raises(RuntimeError, match="job assignment failed"):
        mix._run_ffmpeg(
            [sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"], 3
        )
    assert not marker.exists()
    assert spawned[0].poll() is not None and spawned[0].stderr.closed


def test_process_keeps_bounded_error_tail_and_decodes_invalid_bytes():
    with pytest.raises(RuntimeError, match="diagnostic-end") as failure:
        mix._run_ffmpeg(
            [sys.executable, "-c",
             "import sys;sys.stderr.write('x'*4000+'diagnostic-end');sys.exit(7)"],
            5,
        )
    assert len(str(failure.value)) < 2100
    assert mix._run_ffmpeg(
        [sys.executable, "-c", "import sys;sys.stderr.buffer.write(b'valid\\xff')"], 5
    ) == "valid\ufffd"


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(timeout_seconds=0),
        dict(timeout_seconds=301),
        dict(timeout_seconds=True),
        dict(timeout_seconds=math.inf),
        dict(activity_dbfs=math.nan),
        dict(dual_mono=1),
    ],
)
def test_invalid_options_are_rejected_before_file_access(kwargs):
    with pytest.raises(ValueError):
        mix.measure_loudness("nonexistent.wav", **kwargs)


def test_unknown_channel_layout_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="channel layouts"):
        mix.measure_loudness(write_tone(tmp_path, channels=3))


def test_unsupported_sample_rate_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="sample rates"):
        mix.measure_loudness(write_tone(tmp_path, rate=4000))


def test_short_clip_retains_true_peak_but_no_lufs_or_lra(tmp_path, fake_ffmpeg):
    result = mix.measure_loudness(write_tone(tmp_path, seconds=0.1))
    assert result["loudness"]["status"] == "too_short"
    assert result["loudness"]["integrated_lufs"] is None
    assert result["loudness"]["loudness_range_lu"] is None
    assert result["loudness"]["true_peak_dbtp"] == -20


def test_below_gate_keeps_true_peak_and_omits_lra(tmp_path, fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(mix, "_run_ffmpeg", lambda *_: diagnostic(input_i="-inf"))
    result = mix.measure_loudness(write_tone(tmp_path, amplitude=1e-5))
    assert result["loudness"]["status"] == "below_gate"
    assert result["loudness"]["loudness_range_lu"] is None
    assert result["loudness"]["true_peak_dbtp"] == -20


def test_changed_source_during_ffmpeg_rejected(tmp_path, fake_ffmpeg, monkeypatch):
    source = write_tone(tmp_path)

    def change_source(*_):
        write_tone(tmp_path, amplitude=0.2)
        return diagnostic()

    monkeypatch.setattr(mix, "_run_ffmpeg", change_source)
    with pytest.raises(RuntimeError, match="Audio changed"):
        mix.measure_loudness(source)


def test_candidate_changed_while_reference_is_measured_is_rejected(
    tmp_path, fake_ffmpeg, monkeypatch
):
    candidate = write_tone(tmp_path, "candidate.wav")
    reference = write_tone(tmp_path, "reference.wav")
    calls = []

    def mutate_candidate_on_second_call(*_):
        calls.append(True)
        if len(calls) == 2:
            write_tone(tmp_path, "candidate.wav", amplitude=0.2)
        return diagnostic()

    monkeypatch.setattr(mix, "_run_ffmpeg", mutate_candidate_on_second_call)
    with pytest.raises(RuntimeError, match="Audio changed during comparison"):
        mix.compare_audio(candidate, reference)


def test_finite_samples_with_overflowing_energy_fail_closed(tmp_path, fake_ffmpeg):
    source = tmp_path / "huge.wav"
    sf.write(source, [1e200] * 48000, 48000, subtype="DOUBLE")
    with np.errstate(over="ignore", invalid="ignore"):
        with pytest.raises(ValueError, match="finite measurement"):
            mix.measure_loudness(source)


def test_comparison_warns_about_channel_and_duration_mismatch(tmp_path, fake_ffmpeg):
    candidate = write_tone(tmp_path, "candidate.wav", channels=1, seconds=1)
    reference = write_tone(tmp_path, "reference.wav")
    result = mix.compare_audio(candidate, reference)
    assert len(result["warnings"]) == 2
    assert result["candidate"]["loudness"]["loudness_range_lu"] is None
    assert result["deltas"]["duration_seconds"] == -3


def test_command_is_read_only_and_has_bounded_workers(tmp_path, fake_ffmpeg, monkeypatch):
    source = write_tone(tmp_path)
    seen = []

    def capture(command, timeout):
        seen.append((command, timeout))
        return diagnostic()

    monkeypatch.setattr(mix, "_run_ffmpeg", capture)
    result = mix.measure_loudness(source, dual_mono=True)
    command, timeout = seen[0]
    assert command[-3:] == ["-f", "null", "-"]
    assert command[command.index("-i") + 1] == str(source.resolve())
    assert command[command.index("-threads") + 1] == "1"
    assert "dual_mono=true" in command[command.index("-af") + 1]
    assert timeout == 120
    assert "timeline" not in result
    assert result["source_unchanged"]


def test_compare_unavailable_loudness_does_not_invent_gain(tmp_path, fake_ffmpeg):
    source = write_tone(tmp_path, seconds=0.1)
    result = mix.compare_audio(source, source)
    assert result["deltas"]["integrated_lufs"] is None
    assert not result["level_match"]["available"]
    assert mix.compare_audio(source, source, level_match=False)["level_match"] is None


@pytest.mark.parametrize("kwargs", [dict(level_match=1), dict(true_peak_ceiling_dbtp=math.nan)])
def test_invalid_comparison_options_prevent_work(kwargs):
    with pytest.raises(ValueError):
        mix.compare_audio("nonexistent", "nonexistent", **kwargs)


def test_measured_stereo_sine_levels_and_source_preservation(tmp_path, real_ffmpeg):
    source = write_tone(tmp_path)
    before = file_hash(source)
    files = sorted(tmp_path.iterdir())
    result = mix.measure_loudness(source)
    assert result["loudness"]["status"] == "measured"
    assert result["loudness"]["integrated_lufs"] == pytest.approx(-20.05, abs=0.15)
    assert result["loudness"]["true_peak_dbtp"] == pytest.approx(-20, abs=0.1)
    assert result["loudness"]["loudness_range_lu"] == pytest.approx(0, abs=0.1)
    assert result["rms_dbfs"] == pytest.approx(-23.0103, abs=0.0001)
    assert file_hash(source) == before and sorted(tmp_path.iterdir()) == files
    json.dumps(result, allow_nan=False)


def test_reference_gain_and_ceiling_headroom(tmp_path, real_ffmpeg):
    candidate = write_tone(tmp_path, "candidate.wav", amplitude=0.2)
    reference = write_tone(tmp_path, "reference.wav", amplitude=0.8)
    result = mix.compare_audio(candidate, reference, true_peak_ceiling_dbtp=-3)
    assert result["deltas"]["integrated_lufs"] == pytest.approx(-12.04, abs=0.05)
    match = result["level_match"]
    assert match["requested_gain_db"] == pytest.approx(12.04, abs=0.05)
    assert match["requires_peak_reduction"] and match["headroom_to_ceiling_db"] < 0
    assert match["peak_limited_predicted_true_peak_dbtp"] == pytest.approx(-3)
    assert not match["applied"] and "timeline" not in result["candidate"]
    json.dumps(result, allow_nan=False)


def test_digital_silence_is_unavailable_json_safe(tmp_path, real_ffmpeg):
    result = mix.measure_loudness(write_tone(tmp_path, amplitude=0))
    assert result["loudness"]["status"] == "digital_silence"
    for key in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp"):
        assert result["loudness"][key] is None
    json.dumps(result, allow_nan=False)


def test_dual_mono_convention_adds_three_lu(tmp_path, real_ffmpeg):
    source = write_tone(tmp_path, channels=1)
    mono = mix.measure_loudness(source)
    dual = mix.measure_loudness(source, dual_mono=True)
    assert dual["loudness"]["integrated_lufs"] - mono["loudness"]["integrated_lufs"] == (
        pytest.approx(3.01, abs=0.03)
    )


def test_intersample_peak_is_not_reported_as_sample_peak(tmp_path, real_ffmpeg):
    rate = 48000
    signal = 0.9 * np.sin(2 * np.pi * 12000 * np.arange(4 * rate) / rate + np.pi / 4)
    source = tmp_path / "intersample.wav"
    sf.write(source, signal, rate, subtype="FLOAT")
    result = mix.measure_loudness(source)
    assert result["loudness"]["true_peak_dbtp"] > result["peak_dbfs"] + 2


def test_tool_registration_exposes_callable_measurement_tools(fake_ffmpeg, tmp_path):
    from fl_studio_mcp.tools.audio import register_audio_tools

    class Recorder:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    recorder = Recorder()
    register_audio_tools(recorder)
    source = str(write_tone(tmp_path))
    assert recorder.tools["fl_audio_loudness"](source)["success"]
    assert recorder.tools["fl_audio_compare"](source, source)["success"]
