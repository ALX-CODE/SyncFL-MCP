"""Read-only tempo estimation through SyncFL's hash-verified Rust executable."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import subprocess
import sys
from pathlib import Path

HELPER_VERSION = "0.1.0"
EXECUTABLE = "syncfl-tempo.exe"
METHOD = "spectral_flux_autocorrelation_v1"
CONFIDENCE_BASIS = "relative_candidate_softmax_not_calibrated_probability"
_BUNDLE = Path(__file__).resolve().parents[1] / "_native" / "audio_tempo"
RUNTIME = _BUNDLE if _BUNDLE.exists() else (
    Path(__file__).resolve().parents[3] / "native" / "audio_tempo" / "runtime"
)


def verify_runtime(directory=None):
    """Verify the exact shipped artifact; never search PATH or compile on demand."""
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("The bundled Rust tempo analyzer currently requires Windows x64")
    root = Path(directory) if directory is not None else RUNTIME
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != 1
                or manifest.get("helper_version") != HELPER_VERSION
                or manifest.get("architecture") != "x64"
                or not isinstance(manifest.get("files"), dict)):
            raise ValueError("Unsupported Rust tempo runtime manifest")
        entry = manifest.get("files", {}).get(EXECUTABLE)
        if (not isinstance(entry, dict) or type(entry.get("size")) is not int
                or not 0 < entry["size"] <= 64 * 1024 * 1024
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])):
            raise ValueError("Invalid Rust tempo runtime fingerprint")
        executable = root / EXECUTABLE
        if executable.stat().st_size != entry["size"]:
            raise ValueError("Rust tempo runtime size mismatch")
        if hashlib.sha256(executable.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Rust tempo runtime hash mismatch")
        return executable
    except OSError as exc:
        raise RuntimeError("Bundled Rust tempo analyzer is missing or unreadable") from exc


def _finite(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def _validate_reply(reply, min_bpm, max_bpm, top_n):
    if (not isinstance(reply, dict) or type(reply.get("schema_version")) is not int
            or reply["schema_version"] != 1
            or reply.get("method") != METHOD or reply.get("confidence_basis") != CONFIDENCE_BASIS
            or reply.get("status") not in {"ok", "no_periodic_pulse", "insufficient_audio"}
            or type(reply.get("truncated")) is not bool):
        raise ValueError("Invalid Rust tempo analyzer response")
    for key in ("duration_seconds", "analyzed_duration_seconds"):
        if not _finite(reply.get(key)) or reply[key] < 0:
            raise ValueError("Invalid Rust tempo duration")
    if not 0 <= reply["analyzed_duration_seconds"] <= min(reply["duration_seconds"], 180) + 1e-6:
        raise ValueError("Invalid Rust tempo analysis extent")
    if (type(reply.get("sample_rate")) is not int or not 4000 <= reply["sample_rate"] <= 384000
            or type(reply.get("channels")) is not int or not 1 <= reply["channels"] <= 8):
        raise ValueError("Invalid Rust tempo audio format")
    candidates = reply.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > top_n:
        raise ValueError("Invalid Rust tempo candidates")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Invalid Rust tempo candidate")
        if not _finite(candidate.get("bpm")) or not min_bpm <= candidate["bpm"] <= max_bpm:
            raise ValueError("Invalid Rust tempo candidate BPM")
        for key in ("score", "confidence", "periodicity", "window_consistency"):
            if not _finite(candidate.get(key)) or not 0 <= candidate[key] <= 1:
                raise ValueError("Invalid Rust tempo candidate score")
        if candidate.get("octave_relation") not in {"primary", "half", "double", "other"}:
            raise ValueError("Invalid Rust tempo octave relationship")
    if reply["status"] == "ok":
        if (not candidates or not _finite(reply.get("bpm"))
                or reply["bpm"] != candidates[0]["bpm"]
                or not math.isclose(sum(c["confidence"] for c in candidates), 1, abs_tol=1e-6)):
            raise ValueError("Inconsistent Rust tempo estimate")
    elif candidates or reply.get("bpm") is not None:
        raise ValueError("Rust tempo analyzer invented an estimate without periodic evidence")
    return reply


def analyze_tempo(path, *, min_bpm=40.0, max_bpm=240.0, top_n=5):
    """Analyze a WAV without loading FL or sending desktop input; never mutate it."""
    if (not _finite(min_bpm) or not _finite(max_bpm)
            or not 20 <= min_bpm < max_bpm <= 400):
        raise ValueError("BPM range must satisfy 20 <= min_bpm < max_bpm <= 400")
    if type(top_n) is not int or not 1 <= top_n <= 10:
        raise ValueError("top_n must be an integer in 1..10")
    source = Path(path).resolve(strict=True)
    if not source.is_file():
        raise ValueError("Tempo input must be a regular WAV file")
    before = source.stat()
    executable = verify_runtime()
    try:
        completed = subprocess.run(
            [str(executable), "analyze", "--input", str(source),
             "--min-bpm", str(min_bpm), "--max-bpm", str(max_bpm), "--top-n", str(top_n)],
            capture_output=True, encoding="utf-8", errors="strict", timeout=60,
            check=False, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Rust tempo analysis exceeded the 60-second limit") from exc
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise RuntimeError("WAV changed during tempo analysis")
    if completed.returncode:
        raise ValueError(f"Rust tempo analysis failed: {completed.stderr.strip()[:500]}")
    if len(completed.stdout) > 65536:
        raise ValueError("Rust tempo analyzer returned an oversized response")
    return _validate_reply(json.loads(completed.stdout), min_bpm, max_bpm, top_n)
