# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SyncFL contributors (ALX-CODE and nobibasedd)
"""Global major/minor key estimates using only NumPy and the standard library.

``detect_key(path)`` or ``detect_key(float_samples, sample_rate)`` returns the
five best candidates. Pitch classes use C=0, C#/Db=1, ..., B=11. Confidence is
a softmax share across ALL 24 keys, not a calibrated probability of correctness;
the returned subset need not sum to one. Silence/flat chroma returns no keys.

PCM WAVs use :mod:`wave`; a small RIFF reader also accepts IEEE float WAVs.
Audio is processed in bounded blocks, including each channel independently.
Run this file (or ``python -m fl_studio_mcp.utils.key_analysis``) for self-tests.
"""

from __future__ import annotations

import math
import os
import struct
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np

FRAME_SIZE = 4096
HOP_SIZE = 1024
BLOCK_SIZE = 65536
PITCH_CLASSES = ("C", "C#/Db", "D", "D#/Eb", "E", "F", "F#/Gb", "G", "G#/Ab", "A", "A#/Bb", "B")

# Krumhansl, C. L. (1990), Cognitive Foundations of Musical Pitch, OUP, ch. 4.
# The probe-tone profiles were published by Krumhansl & Kessler (1982),
# Psychological Review 89(4), 334-368, doi:10.1037/0033-295X.89.4.334.
# These numerical profiles are research data; this implementation is original.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _dimensions(rate, channels, frames):
    if isinstance(rate, (bool, np.bool_)) or not isinstance(rate, (int, np.integer)) or rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    if not 1 <= channels <= 8 or frames <= 0 or frames / rate > 7200:
        raise ValueError("Audio must contain 1..8 channels, nonempty and at most two hours")


def _float_header(stream):
    """Read only RIFF metadata; never allocate a buffer using a chunk's size."""
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise ValueError("Expected a little-endian RIFF WAV file")
    end = 8 + struct.unpack_from("<I", header, 4)[0]
    if end > os.fstat(stream.fileno()).st_size:
        raise ValueError("Truncated RIFF WAV file")
    fmt = data = None
    while stream.tell() + 8 <= end:
        tag, size = struct.unpack("<4sI", stream.read(8))
        offset = stream.tell()
        if offset + size > end:
            raise ValueError("Truncated WAV chunk")
        if tag == b"fmt ":
            if fmt is not None:
                raise ValueError("Duplicate WAV format chunk")
            fmt = stream.read(min(size, 40))
        elif tag == b"data":
            if data is not None:
                raise ValueError("Multiple WAV data chunks are unsupported")
            data = (offset, size)
        stream.seek(offset + size + (size % 2))
    if fmt is None or len(fmt) < 16 or data is None:
        raise ValueError("WAV is missing its format or audio data")
    kind, channels, rate, byte_rate, alignment, bits = struct.unpack_from("<HHIIHH", fmt)
    if kind == 0xFFFE and len(fmt) >= 40:
        extra, valid_bits = struct.unpack_from("<HH", fmt, 16)
        float_guid = bytes.fromhex("0300000000001000800000aa00389b71")
        if extra >= 22 and valid_bits == bits and fmt[24:40] == float_guid:
            kind = 3
    if kind != 3 or bits not in (32, 64):
        raise ValueError("WAV must be uncompressed 8/16/24/32-bit PCM or 32/64-bit IEEE float")
    width = bits // 8
    if alignment != channels * width or byte_rate != rate * alignment or not alignment:
        raise ValueError("Inconsistent WAV sample layout")
    if data[1] % alignment:
        raise ValueError("WAV data ends in a partial sample frame")
    return rate, channels, width, data[1] // alignment, data[0]


def _decode_pcm(raw, width, channels):
    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128
    elif width in (2, 4):
        samples = np.frombuffer(raw, dtype=f"<i{width}").astype(np.float64) / 2**(8 * width - 1)
    elif width == 3:
        octets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        packed = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
        samples = ((packed ^ 0x800000) - 0x800000).astype(np.float64) / 8388608
    else:
        raise ValueError("Unsupported PCM sample width")
    return samples.reshape(-1, channels)


@contextmanager
def _wav_blocks(path):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    with path.open("rb") as stream:
        reader = None
        try:
            reader = wave.open(stream, "rb")
        except (wave.Error, EOFError):
            rate, channels, width, frames, offset = _float_header(stream)
            stream.seek(offset)

            def read(count):
                return stream.read(count * width * channels)

            def decode(raw):
                return np.frombuffer(raw, dtype=f"<f{width}").reshape(-1, channels)
        else:
            rate, channels, width, frames = (
                reader.getframerate(), reader.getnchannels(),
                reader.getsampwidth(), reader.getnframes(),
            )
            read = reader.readframes

            def decode(raw):
                return _decode_pcm(raw, width, channels)
        try:
            _dimensions(rate, channels, frames)

            def blocks():
                remaining = frames
                while remaining:
                    count = min(BLOCK_SIZE, remaining)
                    raw = read(count)
                    if len(raw) != count * width * channels:
                        raise ValueError("Truncated WAV audio data")
                    yield decode(raw)
                    remaining -= count

            yield blocks(), rate
        finally:
            if reader is not None:
                reader.close()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise RuntimeError("WAV changed during key analysis")


def _chroma(blocks, rate):
    frequencies = np.fft.rfftfreq(FRAME_SIZE, 1 / rate)
    selected = (frequencies >= 55) & (frequencies <= 5000)
    pitches = np.floor(69 + 12 * np.log2(frequencies[selected] / 440) + 0.5).astype(int) % 12
    window = np.hanning(FRAME_SIZE)
    chroma = np.zeros(12, dtype=np.float64)
    pending = None
    processed = False
    scale = 0.0

    def accumulate(frames, valid_samples=FRAME_SIZE):
        nonlocal scale
        peak = float(np.max(np.abs(frames)))
        if not peak:
            return
        # A running common scale avoids FFT overflow/underflow without changing
        # the relative contribution of quiet and loud frames.
        if peak > scale:
            chroma[:] *= scale / peak
            scale = peak
        scaled = frames / scale
        # Remove DC before windowing so a constant offset cannot suggest a key.
        scaled = scaled - scaled[..., :valid_samples].mean(axis=-1, keepdims=True)
        scaled[..., valid_samples:] = 0
        magnitude = np.abs(np.fft.rfft(scaled * window, axis=-1)).sum(axis=(0, 1))
        chroma[:] += np.bincount(pitches, weights=magnitude[selected], minlength=12)

    for block in blocks:
        block = np.asarray(block, dtype=np.float64)
        if not np.isfinite(block).all():
            raise ValueError("Audio contains NaN or infinite samples")
        pending = block if pending is None else np.concatenate((pending, block), axis=0)
        if len(pending) >= FRAME_SIZE:
            frames = np.lib.stride_tricks.sliding_window_view(pending, FRAME_SIZE, axis=0)
            frames = frames[::HOP_SIZE]
            accumulate(frames)
            pending = pending[len(frames) * HOP_SIZE:]
            processed = True
    # Include a partial final frame exactly once, but not an overlap-only tail.
    if pending is not None and len(pending) and (
        not processed or len(pending) > FRAME_SIZE - HOP_SIZE
    ):
        padded = np.pad(pending, ((0, FRAME_SIZE - len(pending)), (0, 0)))
        accumulate(padded.T[np.newaxis, ...], len(pending))
    total = float(chroma.sum())
    return chroma / total if total > 0 else chroma


def _ranking_options(top_n, temperature):
    if type(top_n) is not int or not 1 <= top_n <= 24:
        raise ValueError("top_n must be an integer in 1..24")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not (
        math.isfinite(temperature) and temperature > 0
    ):
        raise ValueError("temperature must be a finite positive number")


def rank_chroma(chroma, *, top_n=5, temperature=0.15):
    """Pearson-rank a nonnegative 12-bin chroma; confidence includes all 24 keys."""
    _ranking_options(top_n, temperature)
    if np.iscomplexobj(chroma):
        raise ValueError("chroma must contain real values")
    values = np.asarray(chroma, dtype=np.float64)
    if values.shape != (12,) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("chroma must contain 12 finite nonnegative values")
    peak = float(values.max())
    if not peak:
        return []
    values = values / peak
    centered = values - values.mean()
    norm = np.linalg.norm(centered)
    if norm <= 1e-12:
        return []
    profiles = np.array([
        np.roll(profile, tonic)
        for profile in (MAJOR_PROFILE, MINOR_PROFILE) for tonic in range(12)
    ])
    profiles -= profiles.mean(axis=1, keepdims=True)
    correlations = np.clip(profiles @ (centered / norm) / np.linalg.norm(profiles, axis=1), -1, 1)
    with np.errstate(over="ignore", under="ignore"):
        weights = np.exp((correlations - correlations.max()) / temperature)
    confidence = weights / weights.sum()
    order = np.argsort(-correlations, kind="stable")[:top_n]
    return [dict(
        key=f"{PITCH_CLASSES[index % 12]} {'major' if index < 12 else 'minor'}",
        pitch_class=int(index % 12), mode="major" if index < 12 else "minor",
        confidence=float(confidence[index]), correlation=float(correlations[index]),
    ) for index in order]


def detect_key(audio, sample_rate=None, *, top_n=5, temperature=0.15):
    """Rank keys of a WAV path or real float array (frames or frames x channels).

    Uses a 4096-sample Hann STFT, hop 1024, A440 tuning and bins from 55..5000 Hz.
    Stereo magnitudes are pooled without phase cancellation. PCM and IEEE float
    RIFF WAVs are supported; compressed, RF64 and big-endian WAVs are rejected.
    Input is never modified. Global key estimates cannot track modulations and
    may confuse relative keys, drums, detuning or short/ambiguous excerpts.
    """
    _ranking_options(top_n, temperature)
    if isinstance(audio, (str, os.PathLike)):
        if sample_rate is not None:
            raise ValueError("sample_rate must be omitted for a WAV path")
        with _wav_blocks(audio) as (blocks, rate):
            chroma = _chroma(blocks, rate)
    else:
        samples = np.asarray(audio)
        if samples.ndim not in (1, 2) or samples.dtype.kind != "f":
            raise ValueError("Audio must be a real floating-point array, frames x channels")
        if samples.ndim == 1:
            samples = samples[:, np.newaxis]
        _dimensions(sample_rate, samples.shape[1], len(samples))
        blocks = (samples[start:start + BLOCK_SIZE] for start in range(0, len(samples), BLOCK_SIZE))
        chroma = _chroma(blocks, int(sample_rate))
    return rank_chroma(chroma, top_n=top_n, temperature=temperature)


def _progression(chords, *, sample_rate=22050):
    """Deterministic test fixture: triads with a short attack/release, no files."""
    duration = 0.6
    t = np.arange(round(sample_rate * duration)) / sample_rate
    envelope = np.minimum(1, t / 0.015) * np.minimum(1, (duration - t) / 0.04)
    return np.concatenate([
        sum(np.sin(2 * np.pi * (440 * 2**((note - 69) / 12)) * t) for note in chord)
        * envelope / len(chord) for chord in chords
    ])


def _self_test():
    progressions = {
        "C major": [(60, 64, 67), (65, 69, 72), (67, 71, 74), (60, 64, 67)] * 2,
        "A minor": [(57, 60, 64), (62, 65, 69), (64, 68, 71), (57, 60, 64)] * 2,
    }
    for expected, chords in progressions.items():
        candidates = detect_key(_progression(chords), 22050)
        if not candidates or candidates[0]["key"] != expected:
            raise AssertionError(f"Expected {expected}, got {candidates}")
        print(f"{expected}: PASS (correlation={candidates[0]['correlation']:.3f})")


if __name__ == "__main__":
    _self_test()
