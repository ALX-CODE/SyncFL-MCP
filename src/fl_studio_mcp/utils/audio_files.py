"""Streaming song measurements and frame-exact, non-destructive sample edits."""

import hashlib
import importlib.util
import math
import os
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _deps():
    import numpy as np
    import soundfile as sf

    return np, sf


def initialize_audio_runtime():
    """Load installed native audio libraries on the server's main thread.

    A cold NumPy extension import from FastMCP's Windows worker thread stalled
    inside DLL initialization on the validated laptop. Keep audio optional, but
    initialize it before starting the worker pool when the extra is installed.
    """
    if importlib.util.find_spec("numpy") is None:
        return False
    importlib.import_module("numpy")
    if importlib.util.find_spec("soundfile") is not None:
        importlib.import_module("soundfile")
    return True


def _db(value):
    return 20 * math.log10(value) if value > 0 else None


def inspect_audio(path, activity_dbfs=-60.0):
    """Whole-file measurements in one-second blocks, including long songs."""
    np, sf = _deps()
    path = Path(path).resolve(strict=True)
    if type(activity_dbfs) not in (int, float) or not -120 <= activity_dbfs <= -6:
        raise ValueError("activity_dbfs must be finite in -120..-6")
    before = path.stat()
    digest = file_hash(path)
    threshold = 10 ** (activity_dbfs / 20)
    with sf.SoundFile(path) as audio:
        if (
            not 1 <= audio.channels <= 8
            or not 1 <= len(audio)
            or audio.samplerate <= 0
            or len(audio) / audio.samplerate > 7200
        ):
            raise ValueError("Audio must contain 1..8 channels and at most two hours")
        channels, rate, count = audio.channels, audio.samplerate, len(audio)
        info = dict(format=audio.format, subtype=audio.subtype)
        energy = np.zeros(channels)
        sums = np.zeros(channels)
        peaks = np.zeros(channels)
        clipped = np.zeros(channels, dtype="int64")
        cross = 0.0
        active_start = active_end = None
        offset = 0
        timeline = []
        for block in audio.blocks(blocksize=rate, dtype="float64", always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError("Audio contains NaN or infinite samples")
            absolute = np.abs(block)
            energy += np.sum(block * block, axis=0)
            sums += np.sum(block, axis=0)
            peaks = np.maximum(peaks, absolute.max(axis=0))
            clipped += np.sum(absolute >= 1.0, axis=0)
            if channels == 2:
                cross += float(np.sum(block[:, 0] * block[:, 1]))
            active = np.flatnonzero(absolute.max(axis=1) > threshold)
            if len(active):
                if active_start is None:
                    active_start = offset + int(active[0])
                active_end = offset + int(active[-1]) + 1
            timeline.append(
                dict(
                    start_frame=offset,
                    frames=len(block),
                    peak_dbfs=_db(float(absolute.max())),
                    rms_dbfs=_db(float(np.sqrt(np.mean(block * block)))),
                )
            )
            offset += len(block)
    if offset != count:
        raise RuntimeError("Decoded frame count differs from the audio header")
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size) or file_hash(
        path
    ) != digest:
        raise RuntimeError("Audio changed while being measured")
    covariance = cross - sums[0] * sums[1] / count if channels == 2 else None
    variance = energy - sums * sums / count
    denominator = math.sqrt(max(0, float(variance[0] * variance[1]))) if channels == 2 else 0
    return dict(
        path=str(path),
        sha256=digest,
        bytes=after.st_size,
        **info,
        sample_rate=rate,
        channels=channels,
        frames=count,
        duration_seconds=count / rate,
        peak_dbfs=_db(float(peaks.max())),
        rms_dbfs=_db(float(np.sqrt(energy.sum() / (count * channels)))),
        peak_per_channel=peaks.tolist(),
        rms_per_channel=np.sqrt(energy / count).tolist(),
        dc_per_channel=(sums / count).tolist(),
        full_scale_samples_per_channel=clipped.tolist(),
        stereo_correlation=(
            max(-1.0, min(1.0, covariance / denominator)) if denominator > 1e-20 else None
        ),
        silent=active_start is None,
        activity_threshold_dbfs=activity_dbfs,
        active_start_frame=active_start,
        active_end_frame=active_end,
        timeline=timeline,
        limitations=[
            "Sample peaks are not oversampled true peaks or LUFS.",
            "Full-scale samples do not prove audible clipping.",
            "Silence uses the explicit absolute activity threshold.",
        ],
    )


def edit_audio(
    source,
    output,
    expected_sha256,
    start_frame=0,
    end_frame=None,
    gain_db=0.0,
    fade_in_frames=0,
    fade_out_frames=0,
    reverse=False,
    normalize_peak_dbfs=None,
    subtype="PCM_24",
    dry_run=True,
):
    np, sf = _deps()
    source, output = Path(source).resolve(strict=True), Path(output)
    if not output.is_absolute() or output.suffix.lower() != ".wav":
        raise ValueError("Output must be an absolute NEW .wav path")
    output = output.resolve()
    if output == source or output.exists():
        raise ValueError("Output must not exist; original audio is never overwritten")
    if type(reverse) is not bool or type(dry_run) is not bool:
        raise ValueError("reverse and dry_run must be booleans")
    if subtype not in ("PCM_16", "PCM_24", "FLOAT"):
        raise ValueError("subtype must be PCM_16, PCM_24 or FLOAT")
    if file_hash(source) != expected_sha256:
        raise ValueError("Source hash changed; inspect the audio again")
    if type(gain_db) not in (int, float) or not math.isfinite(gain_db) or not -120 <= gain_db <= 36:
        raise ValueError("gain_db must be finite in -120..36")
    with sf.SoundFile(source) as audio:
        end_frame = len(audio) if end_frame is None else end_frame
        if (
            type(start_frame) is not int
            or type(end_frame) is not int
            or not 0 <= start_frame < end_frame <= len(audio)
        ):
            raise ValueError("Trim uses exact frames: 0 <= start_frame < end_frame <= file frames")
        count, rate = end_frame - start_frame, audio.samplerate
        if count * audio.channels > 48_000_000 or not 1 <= audio.channels <= 8:
            raise ValueError("Select at most 48 million interleaved samples / eight channels")
        for value in (fade_in_frames, fade_out_frames):
            if type(value) is not int or not 0 <= value <= count or value == 1:
                raise ValueError("Fade lengths must be zero or 2..selected frame count")
        if fade_in_frames + fade_out_frames > count:
            raise ValueError("Fades must not overlap")
        audio.seek(start_frame)
        data = audio.read(count, dtype="float64", always_2d=True)
    if len(data) != count or not np.isfinite(data).all():
        raise ValueError("Selected audio must decode completely to finite samples")
    if reverse:
        data = data[::-1].copy()
    if fade_in_frames:
        data[:fade_in_frames] *= np.linspace(0, 1, fade_in_frames)[:, None]
    if fade_out_frames:
        data[-fade_out_frames:] *= np.linspace(1, 0, fade_out_frames)[:, None]
    peak = float(np.max(np.abs(data)))
    if normalize_peak_dbfs is not None:
        if (
            type(normalize_peak_dbfs) not in (int, float)
            or not math.isfinite(normalize_peak_dbfs)
            or not -60 <= normalize_peak_dbfs <= 0
            or gain_db != 0
        ):
            raise ValueError("Normalization target must be -60..0 dBFS with gain_db=0")
        if peak == 0:
            raise ValueError("Cannot peak-normalize digital silence")
        gain_db = normalize_peak_dbfs - _db(peak)
    data *= 10 ** (gain_db / 20)
    peak = float(np.max(np.abs(data)))
    if not np.isfinite(data).all():
        raise ValueError("Edited audio exceeds finite sample range")
    if peak > 1.0 and subtype != "FLOAT":
        raise ValueError("PCM output would clip; lower gain or explicitly choose FLOAT")
    report = dict(
        success=True,
        dry_run=dry_run,
        source=str(source),
        source_sha256=expected_sha256,
        output=str(output),
        frames=count,
        sample_rate=rate,
        channels=data.shape[1],
        subtype=subtype,
        start_frame=start_frame,
        end_frame=end_frame,
        reverse=reverse,
        fade_in_frames=fade_in_frames,
        fade_out_frames=fade_out_frames,
        applied_gain_db=gain_db,
        predicted_peak_dbfs=_db(peak),
        order="trim, reverse, linear fades, gain/peak normalization",
        metadata="Writes sample data; source loops/cues/tags are not copied",
    )
    if not dry_run:
        if file_hash(source) != expected_sha256:
            raise ValueError("Source changed before output publication")
        with output.open("xb") as stream:
            sf.write(stream, data, rate, format="WAV", subtype=subtype)
            stream.flush()
            os.fsync(stream.fileno())
        # Compare every decoded sample, allowing only the chosen PCM quantization.
        decoded, actual_rate = sf.read(output, dtype="float64", always_2d=True)
        tolerance = {"PCM_16": 2**-15, "PCM_24": 2**-23, "FLOAT": 2**-23}[subtype]
        if (
            actual_rate != rate
            or decoded.shape != data.shape
            or not np.allclose(decoded, data, rtol=2**-23, atol=tolerance)
        ):
            raise RuntimeError("Output sample verification failed; inspect the new WAV")
        report["audio"] = inspect_audio(output)
        report["sample_readback_verified"] = True
    return report
