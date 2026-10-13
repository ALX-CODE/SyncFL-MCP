"""Read-only, bounded loudness measurements and reference level comparisons.

FFmpeg's loudnorm input statistics supply EBU R128 measurements and 192 kHz
true-peak detection. Its processed output is discarded, never published.
https://ffmpeg.org/ffmpeg-filters.html#loudnorm
"""

import json
import math
import os
import re
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path

from fl_studio_mcp.utils.audio_files import file_hash, inspect_audio

_MAX_LOG_BYTES = 256 * 1024
_MAX_FILE_BYTES = 4 * 1024**3


def _number(value, name, lower, upper):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite in {lower}..{upper}")


def find_ffmpeg():
    """Use an explicit executable, PATH, or the user's existing local install."""
    configured = os.environ.get("FL_STUDIO_MCP_FFMPEG")
    if configured:
        executable = Path(configured).expanduser()
        if not executable.is_absolute() or not executable.is_file():
            raise RuntimeError("FL_STUDIO_MCP_FFMPEG must name an existing absolute executable")
        if os.name == "nt" and executable.suffix.lower() != ".exe":
            raise RuntimeError("FL_STUDIO_MCP_FFMPEG must name an .exe on Windows")
        return str(executable.resolve())
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    executable = shutil.which(name)
    if executable:
        return str(Path(executable).resolve())
    local = Path.home() / ".local" / "bin" / name
    if local.is_file():
        return str(local.resolve())
    raise RuntimeError(
        "FFmpeg is unavailable. Install FFmpeg with loudnorm or set FL_STUDIO_MCP_FFMPEG "
        "to its absolute executable path; LUFS/true peak cannot be inferred from sample levels."
    )


class _WindowsProcessJob:
    """Contain launcher descendants before any of their code can run."""

    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        class BasicLimits(c.Structure):
            _fields_ = [
                ("process_time", c.c_longlong), ("job_time", c.c_longlong),
                ("flags", w.DWORD), ("minimum", c.c_size_t), ("maximum", c.c_size_t),
                ("process_limit", w.DWORD), ("affinity", c.c_size_t),
                ("priority", w.DWORD), ("scheduling", w.DWORD),
            ]

        class ExtendedLimits(c.Structure):
            _fields_ = [
                ("basic", BasicLimits), ("io_counters", c.c_ulonglong * 6),
                ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t),
            ]

        self.c, self.w = c, w
        self.kernel = kernel = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL
            ),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, c.c_void_p], w.BOOL),
            "Thread32Next": ([w.HANDLE, c.c_void_p], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "PeekNamedPipe": (
                [w.HANDLE, c.c_void_p, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p], w.BOOL
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        c, w, kernel = self.c, self.w, self.kernel
        handle = kernel.OpenProcess(0x101, False, process.pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise c.WinError(c.get_last_error())
        try:
            if not kernel.AssignProcessToJobObject(self.handle, handle):
                raise c.WinError(c.get_last_error())
        finally:
            kernel.CloseHandle(handle)

        class ThreadEntry(c.Structure):
            _fields_ = [
                ("size", w.DWORD), ("usage", w.DWORD), ("id", w.DWORD),
                ("owner", w.DWORD), ("base_priority", w.LONG),
                ("delta_priority", w.LONG), ("flags", w.DWORD),
            ]

        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = c.sizeof(entry)
            available = kernel.Thread32First(snapshot, c.byref(entry))
            while available:
                if entry.owner == process.pid:
                    thread = kernel.OpenThread(0x2, False, entry.id)  # SUSPEND_RESUME
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                        return
                    finally:
                        kernel.CloseHandle(thread)
                available = kernel.Thread32Next(snapshot, c.byref(entry))
            raise RuntimeError("Cannot find suspended FFmpeg process thread")
        finally:
            kernel.CloseHandle(snapshot)

    def read_available(self, pipe):
        import msvcrt

        c, available = self.c, self.w.DWORD()
        if not self.kernel.PeekNamedPipe(
            msvcrt.get_osfhandle(pipe.fileno()), None, 0, None, c.byref(available), None
        ):
            error = c.get_last_error()
            if error == 109:  # ERROR_BROKEN_PIPE: all writers have closed.
                return b""
            raise c.WinError(error)
        return os.read(pipe.fileno(), min(8192, available.value)) if available.value else None

    def close(self):
        if self.handle:
            c, w = self.c, self.w

            class Accounting(c.Structure):
                _fields_ = [
                    ("user", c.c_longlong), ("kernel", c.c_longlong),
                    ("period_user", c.c_longlong), ("period_kernel", c.c_longlong),
                    ("faults", w.DWORD), ("total", w.DWORD),
                    ("active", w.DWORD), ("terminated", w.DWORD),
                ]

            try:
                if not self.kernel.TerminateJobObject(self.handle, 1):
                    raise c.WinError(c.get_last_error())
                deadline, accounting = time.monotonic() + 2, Accounting()
                while True:
                    if not self.kernel.QueryInformationJobObject(
                        self.handle, 1, c.byref(accounting), c.sizeof(accounting), None
                    ):
                        raise c.WinError(c.get_last_error())
                    if not accounting.active:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("FFmpeg process tree did not terminate within 2 seconds")
                    time.sleep(0.01)
            finally:
                self.kernel.CloseHandle(self.handle)
                self.handle = None


def _run_ffmpeg(command, timeout_seconds):
    """Bound diagnostics and the complete process tree, including inherited pipe writers."""
    deadline = time.monotonic() + timeout_seconds
    chunks, total = [], 0
    job = _WindowsProcessJob() if os.name == "nt" else None
    process = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            # Suspend before attaching: a Windows venv launcher can immediately spawn Python.
            creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if job else 0,
            start_new_session=job is None,
        )
        if job:
            job.attach_and_resume(process)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            if job:
                block = job.read_available(process.stderr)
            else:
                ready = select.select([process.stderr], [], [], 0)[0]
                block = os.read(process.stderr.fileno(), 8192) if ready else None
            if block is None:
                time.sleep(min(0.01, remaining))
            elif not block:
                process.wait(timeout=max(0, deadline - time.monotonic()))
                break
            else:
                total += len(block)
                if total > _MAX_LOG_BYTES:
                    raise RuntimeError(
                        "FFmpeg loudness analysis exceeded its diagnostic size limit"
                    )
                chunks.append(block)
        diagnostic = b"".join(chunks).decode("utf-8", errors="replace")
        if process.returncode:
            raise RuntimeError(
                f"FFmpeg loudness analysis failed ({process.returncode}): {diagnostic[-2000:]}"
            )
        return diagnostic
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"FFmpeg loudness analysis exceeded {timeout_seconds} seconds; no audio was written"
        ) from exc
    finally:
        try:
            if job:
                job.close()  # Includes descendants even if the original launcher has exited.
            elif process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if process:
                try:
                    if process.poll() is None:
                        process.kill()  # Covers failure before a Windows job was assigned.
                    process.wait(timeout=2)
                finally:
                    process.stderr.close()  # No blocked reader thread owns this raw pipe.


def _parse_loudnorm(diagnostic):
    # Anchor to the filter's own logger, not arbitrary JSON in source metadata.
    blocks = re.findall(
        r"(?m)^\[Parsed_loudnorm_\d+ @ [^\]\r\n]+\][ \t]*\r?\n(\{[^{}]*\})",
        diagnostic,
    )
    if len(blocks) != 1:
        raise RuntimeError("FFmpeg did not return one complete loudnorm measurement")
    try:
        result = json.loads(blocks[0])
        parsed = {}
        for source, destination in (
            ("input_i", "integrated_lufs"),
            ("input_lra", "loudness_range_lu"),
            ("input_tp", "true_peak_dbtp"),
            ("input_thresh", "relative_threshold_lufs"),
        ):
            value = result[source]
            if type(value) not in (str, int, float):
                raise ValueError(f"Invalid {source}")
            number = float(value)
            if number == -math.inf and source != "input_lra":
                parsed[destination] = None
            elif (
                not math.isfinite(number)
                or not -1000 <= number <= 1000
                or (source == "input_lra" and number < 0)
            ):
                raise ValueError(f"Invalid {source}")
            else:
                parsed[destination] = number
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("FFmpeg returned invalid loudnorm measurements") from exc
    return parsed


def _preflight(path):
    import soundfile as sf

    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("Audio must be a local regular file of at most 4 GiB")
    with sf.SoundFile(path) as audio:
        if audio.channels not in (1, 2):
            raise ValueError(
                "Loudness analysis supports mono/stereo; other channel layouts are unknown"
            )
        if not 8000 <= audio.samplerate <= 192000:
            raise ValueError("Loudness analysis supports sample rates from 8000 to 192000 Hz")
        if not 0 < len(audio) / audio.samplerate <= 7200:
            raise ValueError("Audio must be nonempty and at most two hours")
    return path


def measure_loudness(path, activity_dbfs=-60.0, timeout_seconds=120.0, dual_mono=False):
    """Measure the complete file without writing audio or changing its source."""
    _number(timeout_seconds, "timeout_seconds", 1, 300)
    _number(activity_dbfs, "activity_dbfs", -120, -6)
    if type(dual_mono) is not bool:
        raise ValueError("dual_mono must be a boolean")
    path = _preflight(path)
    executable = find_ffmpeg()
    summary = inspect_audio(path, activity_dbfs)
    summary.pop("timeline")
    try:
        json.dumps(summary, allow_nan=False)
    except ValueError as exc:
        raise ValueError("Audio levels exceed finite measurement limits") from exc
    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "info",
        "-max_alloc",
        "67108864",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-af",
        f"loudnorm=linear=false:dual_mono={str(dual_mono).lower()}:print_format=json",
        "-threads",
        "1",
        "-f",
        "null",
        "-",
    ]
    diagnostic = _run_ffmpeg(command, timeout_seconds)
    if file_hash(path) != summary["sha256"]:
        raise RuntimeError("Audio changed during loudness analysis; discard these measurements")
    loudness = _parse_loudnorm(diagnostic)
    reasons = []
    if summary["peak_dbfs"] is None:
        status = "digital_silence"
        loudness = dict.fromkeys(loudness)
        reasons.append("Digital silence has no finite LUFS or true peak.")
    elif summary["duration_seconds"] < 0.4:
        status = "too_short"
        loudness["integrated_lufs"] = loudness["relative_threshold_lufs"] = None
        reasons.append("Less than 400 ms cannot support an integrated loudness measurement.")
    elif loudness["integrated_lufs"] is None:
        status = "below_gate"
        loudness["relative_threshold_lufs"] = None
        reasons.append("No programme loudness was measurable above the EBU R128 gate.")
    else:
        status = "measured"
    if summary["duration_seconds"] < 3 or loudness["integrated_lufs"] is None:
        loudness["loudness_range_lu"] = None
        reasons.append("Loudness range needs measurable programme audio and at least 3 seconds.")
    loudness.update(
        status=status,
        method="FFmpeg loudnorm input statistics, EBU R128, dynamic-mode 192 kHz true peak",
        ffmpeg_path=executable,
        dual_mono=dual_mono,
        reasons=reasons,
    )
    summary.update(success=True, loudness=loudness, source_unchanged=True)
    summary["limitations"] = [
        "Whole-file measurements; no alignment, spectral similarity or artistic-quality judgment.",
        "Loudness range is descriptive; short or repetitive material may have a range of zero.",
        "Mono uses its native channel count unless dual_mono=true models stereo playback.",
        "Activity-threshold silence and EBU R128 gating are independent measurements.",
        "True peak is an oversampled estimate; remeasure any delivered or encoded output.",
    ]
    return summary


def compare_audio(
    path,
    reference_path,
    level_match=True,
    true_peak_ceiling_dbtp=-1.0,
    activity_dbfs=-60.0,
    timeout_seconds=120.0,
    dual_mono=False,
):
    """Compare complete programme measurements; estimate gain without applying it."""
    if type(level_match) is not bool:
        raise ValueError("level_match must be a boolean")
    _number(true_peak_ceiling_dbtp, "true_peak_ceiling_dbtp", -24, 0)
    candidate = measure_loudness(path, activity_dbfs, timeout_seconds, dual_mono)
    reference = measure_loudness(reference_path, activity_dbfs, timeout_seconds, dual_mono)
    for measurement in (candidate, reference):
        if file_hash(measurement["path"]) != measurement["sha256"]:
            raise RuntimeError("Audio changed during comparison; discard these measurements")

    def delta(left, right):
        return left - right if left is not None and right is not None else None

    own, target = candidate["loudness"], reference["loudness"]
    differences = {
        key: delta(own[key], target[key])
        for key in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp")
    }
    differences.update(
        peak_dbfs=delta(candidate["peak_dbfs"], reference["peak_dbfs"]),
        rms_dbfs=delta(candidate["rms_dbfs"], reference["rms_dbfs"]),
        duration_seconds=candidate["duration_seconds"] - reference["duration_seconds"],
        stereo_correlation=delta(candidate["stereo_correlation"], reference["stereo_correlation"]),
    )
    matching = None
    if level_match:
        required = delta(target["integrated_lufs"], own["integrated_lufs"])
        if required is None or own["true_peak_dbtp"] is None:
            matching = dict(
                available=False,
                reason="Both integrated loudness measurements and candidate true peak are required",
            )
        else:
            predicted_peak = own["true_peak_dbtp"] + required
            headroom = true_peak_ceiling_dbtp - predicted_peak
            safe_gain = min(required, true_peak_ceiling_dbtp - own["true_peak_dbtp"])
            matching = dict(
                available=True,
                requested_gain_db=required,
                true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
                predicted_true_peak_dbtp=predicted_peak,
                headroom_to_ceiling_db=headroom,
                requires_peak_reduction=headroom < 0,
                peak_limited_gain_db=safe_gain,
                peak_limited_predicted_lufs=own["integrated_lufs"] + safe_gain,
                peak_limited_predicted_true_peak_dbtp=own["true_peak_dbtp"] + safe_gain,
                applied=False,
            )
    warnings = []
    if candidate["channels"] != reference["channels"]:
        warnings.append(
            "Channel counts differ; the mono playback convention affects the comparison."
        )
    if abs(differences["duration_seconds"]) > 0.1:
        warnings.append("Durations differ; these whole-file measurements are not time-aligned.")
    return dict(
        success=True,
        candidate=candidate,
        reference=reference,
        delta_convention="candidate minus reference; loudness deltas are LU, level deltas are dB",
        deltas=differences,
        level_match=matching,
        warnings=warnings,
        limitations=[
            "Level matching estimates constant gain, never EQ, dynamics, or perceptual similarity.",
            "Gain changes can change absolute-gate participation; remeasure after processing.",
            "A true-peak ceiling is an explicit comparison constraint, not a delivery standard.",
        ],
    )
