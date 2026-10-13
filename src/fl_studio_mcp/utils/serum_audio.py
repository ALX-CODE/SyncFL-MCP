"""Bounded audio measurements and a stopped-transport Serum 2 loopback probe.

Audio packages are optional and imported only when requested. Measurements are
descriptive, not a perceptual similarity score or a claim that two sounds match.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import transaction_lock


def _audio_dependencies():
    try:
        import numpy as np
        import soundfile as sf
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("Install fl-studio-mcp[audio] for audio measurements") from exc
    return np, sf, signal


def _pitch(samples, rate, np, signal):
    """Autocorrelation estimate, declining short/noisy/nonperiodic segments."""
    if len(samples) < 32:
        return None
    x = samples - np.mean(samples)
    if np.max(np.abs(x)) < 1e-8:
        return None
    # Downsample before correlation; the deliberately limited range covers bass
    # and most leads, but cannot resolve arbitrary chords or inharmonic patches.
    stride = max(1, int(rate / 12000))
    if stride > 1:
        x = signal.resample_poly(x, 1, stride)
        rate /= stride
    corr = signal.correlate(x, x, mode="full", method="fft")[len(x) - 1 :]
    if corr[0] <= 0:
        return None
    # Remove finite-window overlap bias, then require at least three cycles.
    corr = corr / np.arange(len(x), 0, -1)
    corr /= corr[0]
    lo, hi = max(2, int(rate / 2000)), min(len(x) // 3, int(rate / 30))
    if hi <= lo + 2:
        return None
    peaks, _ = signal.find_peaks(corr[lo : hi + 1], height=0.65)
    if not len(peaks):
        return None
    peaks += lo
    best = float(np.max(corr[peaks]))
    lag = int(next(p for p in peaks if corr[p] >= max(0.65, best * 0.9)))
    a, b, c = corr[lag - 1 : lag + 2]
    offset = float(0.5 * (a - c) / (a - 2 * b + c)) if a - 2 * b + c else 0.0
    hz = float(rate / (lag + max(-0.5, min(0.5, offset))))
    return {"hz": hz, "periodicity": float(min(1, corr[lag])), "cycles": len(x) * hz / rate}


def _measure(path):
    np, sf, signal = _audio_dependencies()
    path = Path(path).expanduser().resolve(strict=True)
    info = sf.info(str(path))
    if info.duration > 120 or info.frames * info.channels > 24_000_000:
        raise ValueError("Analyze at most 120 seconds / 24 million interleaved samples per file")
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    if not len(x) or not np.isfinite(x).all():
        raise ValueError("Audio must contain finite samples and at least one frame")
    channel_rms = np.sqrt(np.mean(x * x, axis=0))
    # Selecting the loudest channel avoids cancelling anti-phase stereo bass.
    selected = int(np.argmax(channel_rms))
    y = x[:, selected]
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    silent = peak < 1e-8
    threshold = max(1e-8, peak * 0.01)
    active = np.flatnonzero(np.max(np.abs(x), axis=1) > threshold)
    begin, end = (int(active[0]), int(active[-1]) + 1) if len(active) else (0, 0)
    duration = (end - begin) / rate
    active_rms = float(np.sqrt(np.mean(x[begin:end] ** 2))) if end > begin else 0.0
    # Six bounded regions are concise for both percussive and sustained sounds.
    edges = np.linspace(begin, end, 7).astype(int)
    regions = []
    for a, b in zip(edges[:-1], edges[1:]):
        pitch = _pitch(y[a:b], rate, np, signal) if b > a else None
        regions.append(
            {
                "start_seconds": a / rate,
                "end_seconds": b / rate,
                "pitch_hz": pitch["hz"] if pitch else None,
                "periodicity": pitch["periodicity"] if pitch else None,
            }
        )
    reliable = [r["pitch_hz"] for r in regions if r["pitch_hz"] is not None]
    # Harmonics are meaningful only in a stable tail window with enough cycles.
    tail = y[max(begin, end - int(rate * 0.25)) : end]
    pitch = _pitch(tail, rate, np, signal) if len(tail) else None
    halves = [_pitch(part, rate, np, signal) for part in np.array_split(tail, 2)]
    harmonics = None
    if pitch and all(halves) and abs(math.log2(halves[0]["hz"] / halves[1]["hz"])) < 0.04:
        fundamental = pitch["hz"]
        nfft = 2 ** int(math.ceil(math.log2(max(32, len(tail) * 4))))
        spectrum = np.abs(np.fft.rfft((tail - np.mean(tail)) * np.hanning(len(tail)), nfft))
        frequencies = np.fft.rfftfreq(nfft, 1 / rate)
        amplitudes = []
        for harmonic in range(1, 10):
            center = fundamental * harmonic
            mask = np.abs(frequencies - center) <= max(rate / len(tail), center * 0.015)
            amplitudes.append(float(np.max(spectrum[mask])) if mask.any() else 0.0)
        if amplitudes[0] > 1e-8:
            harmonics = {
                "fundamental_hz": fundamental,
                "amplitude_ratios": [v / amplitudes[0] for v in amplitudes],
            }
    return {
        "path": str(path),
        "sample_rate": rate,
        "channels": x.shape[1],
        "duration_seconds": len(x) / rate,
        "peak": peak,
        "rms": rms,
        "peak_dbfs": 20 * math.log10(peak) if peak > 0 else None,
        "rms_dbfs": 20 * math.log10(rms) if rms > 0 else None,
        "active_rms": active_rms,
        "active_rms_dbfs": 20 * math.log10(active_rms) if active_rms > 0 else None,
        "dc_per_channel": [float(v) for v in np.mean(x, axis=0)],
        "clipped_fraction": float(np.mean(np.abs(x) >= 0.999)),
        "silent": silent,
        "analysis_channel": selected,
        "active_start_seconds": begin / rate,
        "active_end_seconds": end / rate,
        "active_duration_seconds": duration,
        "activity_threshold": threshold,
        "pitch_median_hz": float(np.median(reliable)) if reliable else None,
        "pitch_regions": regions,
        "tail_harmonics": harmonics,
    }


def analyze_audio(path, reference_path=None):
    """Measure a WAV/other libsndfile-readable sound, optionally against a reference.

    Pitch is monophonic autocorrelation (30–2000 Hz, at least three cycles,
    periodicity >= .65). Null estimates are intentional, especially for short
    attacks, noise, chords and changing pitch. Gain is never silently normalized.
    """
    result = {
        "audio": _measure(path),
        "estimators": {
            "pitch": "monophonic autocorrelation, 30-2000 Hz, >=3 cycles, periodicity >=0.65",
            "harmonics": "Hann FFT amplitudes relative to fundamental; stable final <=250 ms",
            "activity": "first to last frame above 1% of global peak; includes internal gaps",
            "rms": "whole file, all channels; active_rms uses only the detected active span",
            "stereo": "levels include all channels; pitch uses loudest channel, never stereo sum",
        },
        "limitations": [
            "Pitch can have octave errors or fail on chords, noise and rapid pitch sweeps.",
            "Clipped fraction counts samples >=0.999; it cannot prove intentional distortion.",
            "Measurements are not a perceptual similarity score or source identification.",
        ],
    }
    if reference_path is not None:
        reference = _measure(reference_path)
        result["reference"] = reference
        a, b = result["audio"], reference
        comparable = a["active_rms"] > 0 and b["active_rms"] > 0
        gain_db = 20 * math.log10(b["active_rms"] / a["active_rms"]) if comparable else None
        result["comparison"] = {
            "comparable_non_silent": comparable,
            "rms_gain_db_to_match_reference": gain_db,
            "rms_gain_basis": "active_rms: first-to-last frame above 1% peak, all channels",
            "active_duration_difference_seconds": a["active_duration_seconds"]
            - b["active_duration_seconds"],
            "pitch_difference_cents": 1200 * math.log2(a["pitch_median_hz"] / b["pitch_median_hz"])
            if a["pitch_median_hz"] and b["pitch_median_hz"]
            else None,
            "gain_applied": False,
        }
    return result


def _validate_probe(channel, output_path, note, velocity, hold_seconds):
    for name, value, low, high in (
        ("channel", channel, 0, 4095),
        ("note", note, 0, 127),
        ("velocity", velocity, 1, 127),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be an integer in {low}..{high}")
    if (
        isinstance(hold_seconds, bool)
        or not isinstance(hold_seconds, (int, float))
        or not math.isfinite(hold_seconds)
        or not 0.05 <= hold_seconds <= 6.5
    ):
        raise ValueError("hold_seconds must be finite and in 0.05..6.5")
    path = Path(output_path).expanduser().resolve()
    if path.suffix.lower() != ".wav" or not path.parent.is_dir():
        raise ValueError("Output must be a .wav in an existing directory")
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    return path


def capture_serum_probe(
    channel, output_path, note=32, velocity=100, hold_seconds=0.6, reference_path=None
):
    """Capture one temporary MIDI note to a new WAV. Never edits persistent notes.

    Requires stopped playback/recording and a Serum 2 generator. WASAPI records
    the default Windows output including other apps; this does NOT isolate Serum.
    A note-off is attempted even when note-on times out or audio capture fails.
    """
    path = _validate_probe(channel, output_path, note, velocity, hold_seconds)
    np, sf, _ = _audio_dependencies()
    if reference_path is not None:
        _measure(reference_path)  # Validate all inputs before sending any notes.
    if os.name != "nt":
        raise RuntimeError("Serum loopback capture requires Windows WASAPI")
    try:
        import pyaudiowpatch as pa
    except ImportError as exc:
        raise RuntimeError("Install the Windows audio extra (PyAudioWPatch) to capture") from exc
    conn = get_connection()

    def command(action, params=None):
        response = conn.send_command(action, params or {}, timeout=2)
        if not response.get("success"):
            raise RuntimeError(f"{action} failed: {response}")
        return response

    with transaction_lock(conn._hardware_dir / "mcp-serum.lock", 5):
        status = command("transport.getStatus")
        if status.get("is_playing") is not False or status.get("is_recording") is not False:
            raise ValueError("Stop playback and recording before the Serum probe")
        plugin = command(
            "native.call",
            {
                "function": "plugins.getPluginName",
                "args": [channel, -1, False, True],
                "allow_write": False,
            },
        ).get("value")
        if str(plugin).strip().casefold() not in {"serum 2", "serum2"}:
            raise ValueError(f"Channel {channel} is not verified Serum 2: {plugin!r}")
        # Exclusive create checks actual write permission and closes the race
        # between validating the destination and emitting the first MIDI note.
        with path.open("xb") as output:
            try:
                with pa.PyAudio() as audio:
                    device = audio.get_default_wasapi_loopback()
                    rate, channels = (
                        int(device["defaultSampleRate"]),
                        int(device["maxInputChannels"]),
                    )
                    if not 8000 <= rate <= 192000 or not 1 <= channels <= 32:
                        raise ValueError("Unsupported loopback device format")
                    frames_limit = math.ceil(rate * (hold_seconds + 1.25))
                    blocks, errors = [], []
                    frame_count = 0

                    def callback(data, count, timing, flags):
                        nonlocal frame_count
                        try:
                            if flags:
                                errors.append(f"PortAudio callback status: {flags}")
                            if frame_count < frames_limit:
                                blocks.append(data)
                                frame_count += count
                            return (
                                None,
                                pa.paComplete if frame_count >= frames_limit else pa.paContinue,
                            )
                        except Exception as exc:
                            errors.append(str(exc))
                            return None, pa.paAbort

                    stream = audio.open(
                        format=pa.paFloat32,
                        channels=channels,
                        rate=rate,
                        input=True,
                        input_device_index=device["index"],
                        frames_per_buffer=512,
                        stream_callback=callback,
                    )
                    try:
                        time.sleep(0.25)
                        if not stream.is_active() or errors:
                            raise RuntimeError(f"Loopback capture failed before note-on: {errors}")
                        note_error = None
                        try:
                            command(
                                "channels.triggerNote",
                                {
                                    "channel": channel,
                                    "note": note,
                                    "velocity": velocity,
                                    "midi_channel": -1,
                                },
                            )
                            time.sleep(hold_seconds)
                        except Exception as exc:
                            note_error = exc
                        finally:
                            try:
                                command(
                                    "channels.triggerNote",
                                    {
                                        "channel": channel,
                                        "note": note,
                                        "velocity": 0,
                                        "midi_channel": -1,
                                    },
                                )
                            except Exception as exc:
                                raise RuntimeError(
                                    "Note-off could not be confirmed; stop FL voices "
                                    f"manually. Note error: {note_error}; off: {exc}"
                                ) from exc
                        if note_error:
                            raise note_error
                        deadline = time.monotonic() + 1.25
                        while stream.is_active() and time.monotonic() < deadline:
                            time.sleep(0.02)
                    finally:
                        try:
                            stream.stop_stream()
                        finally:
                            stream.close()
                    if errors or frame_count < int(rate * (hold_seconds + 0.5)):
                        raise RuntimeError(f"Incomplete/overrun loopback capture: {errors}")
                    samples = np.frombuffer(b"".join(blocks), dtype=np.float32).reshape(
                        -1, channels
                    )
                    sf.write(output, samples[:frames_limit], rate, format="WAV", subtype="FLOAT")
            except BaseException:
                # Windows cannot unlink an open handle; remove only our own file.
                output.close()
                path.unlink(missing_ok=True)
                raise
    result = analyze_audio(path, reference_path)
    result["capture"] = {
        "channel": channel,
        "plugin": plugin,
        "note": note,
        "velocity": velocity,
        "hold_seconds": hold_seconds,
        "device": device["name"],
        "note_off_confirmed": True,
        "warning": "WASAPI loopback includes all system audio; Serum is not isolated.",
    }
    return result
