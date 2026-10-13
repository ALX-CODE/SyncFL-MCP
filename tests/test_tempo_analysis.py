"""Rust boundary validation and independent WAV-to-tempo integration tests."""

import asyncio
import hashlib
import json
import math
import struct
import subprocess
import sys
import wave

import pytest

from fl_studio_mcp.utils import tempo_analysis as tempo


def result():
    return dict(schema_version=1, method=tempo.METHOD, confidence_basis=tempo.CONFIDENCE_BASIS,
                status="ok", bpm=120.0, duration_seconds=12.0, analyzed_duration_seconds=12.0,
                truncated=False, sample_rate=16000, channels=1,
                candidates=[dict(bpm=120.0, score=0.9, confidence=1.0, periodicity=0.95,
                                 window_consistency=1.0, octave_relation="primary")])


def wav(path, bpm=120, seconds=12, silence=False):
    rate = 16000
    samples = [0.0] * (rate * seconds)
    if not silence:
        for beat in range(math.ceil(seconds * bpm / 60)):
            start = round((0.15 + beat * 60 / bpm) * rate)
            for i in range(480):
                if start + i < len(samples):
                    samples[start + i] = math.exp(-i / 65) * math.sin(i * 0.7) * 0.8
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack(f"<{len(samples)}h", *(round(s * 32767) for s in samples)))
    return path


def mixed_drums(path, bpm):
    """Independent 4/4 fixture: different kick/snare timbres plus eighth-note hats."""
    import numpy as np

    rate = 22050
    samples = np.zeros(math.ceil(32 * 60 / bpm * rate))
    rng = np.random.default_rng(128)
    t = np.arange(round(.16 * rate)) / rate
    kick = np.sin(2 * np.pi * (48 * t + 75 * .018 * (1 - np.exp(-t / .018))))
    kick *= np.exp(-t / .04)
    snare = rng.normal(size=len(t)) * np.exp(-t / .025)
    h = np.arange(round(.04 * rate)) / rate
    hat = np.diff(rng.normal(size=len(h) + 1)) * np.exp(-h / .009)

    def add(beat, signal, gain):
        start = round(beat * 60 / bpm * rate)
        count = min(len(signal), len(samples) - start)
        samples[start:start + count] += signal[:count] * gain

    for beat in range(32):
        add(beat, kick if beat % 2 == 0 else snare, .8 if beat % 2 == 0 else .4)
        add(beat, hat, .13)
        add(beat + .5, hat, .075)
        if beat % 2 == 0:
            b = np.arange(round(.8 * 60 / bpm * rate)) / rate
            root = 55 * 2 ** ((0, 5, 7, 0)[beat // 8] / 12)
            bass = np.sin(2 * np.pi * root * b) * np.minimum(b / .01, 1) * np.exp(-b / .4)
            add(beat, bass, .4)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes((samples / max(1, abs(samples).max()) * 30000).astype("<i2").tobytes())
    return path


@pytest.mark.parametrize("options", [
    {"min_bpm": True}, {"max_bpm": float("nan")}, {"min_bpm": 10}, {"max_bpm": 401},
    {"min_bpm": 200, "max_bpm": 100}, {"top_n": True}, {"top_n": 0}, {"top_n": 11},
])
def test_options_fail_before_file_or_process_access(options):
    with pytest.raises(ValueError):
        tempo.analyze_tempo("not-present.wav", **options)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(method="wrong"), lambda r: r.update(truncated=1),
    lambda r: r.update(bpm=121), lambda r: r.update(analyzed_duration_seconds=181),
    lambda r: r.update(channels=0), lambda r: r.update(status="no_periodic_pulse"),
    lambda r: r["candidates"][0].update(confidence=float("nan")),
    lambda r: r["candidates"][0].update(confidence=0.5),
    lambda r: r["candidates"][0].update(bpm=500),
    lambda r: r["candidates"][0].update(periodicity=2),
    lambda r: r["candidates"][0].update(octave_relation="unknown"),
])
def test_malformed_native_reply_is_rejected(mutate):
    reply = result()
    mutate(reply)
    with pytest.raises(ValueError):
        tempo._validate_reply(reply, 40, 240, 5)


def test_empty_result_remains_empty():
    reply = result()
    reply.update(status="no_periodic_pulse", bpm=None, candidates=[])
    assert tempo._validate_reply(reply, 40, 240, 5) is reply


def test_runtime_manifest_is_checked_before_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(tempo.sys, "platform", "win32")
    monkeypatch.setattr(tempo.platform, "machine", lambda: "AMD64")
    binary = tmp_path / tempo.EXECUTABLE
    binary.write_bytes(b"test-only executable bytes")
    manifest = dict(schema_version=1, helper_version=tempo.HELPER_VERSION, architecture="x64",
                    files={tempo.EXECUTABLE: dict(size=binary.stat().st_size,
                           sha256=hashlib.sha256(binary.read_bytes()).hexdigest())})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert tempo.verify_runtime(tmp_path) == binary
    binary.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="mismatch"):
        tempo.verify_runtime(tmp_path)
    manifest["files"] = []
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        tempo.verify_runtime(tmp_path)


def test_process_failure_timeout_and_changed_source(tmp_path, monkeypatch):
    source = wav(tmp_path / "input.wav")
    monkeypatch.setattr(tempo, "verify_runtime", lambda: tmp_path / "helper.exe")

    def fail(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="invalid audio")

    monkeypatch.setattr(tempo.subprocess, "run", fail)
    with pytest.raises(ValueError, match="invalid audio"):
        tempo.analyze_tempo(source)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)

    monkeypatch.setattr(tempo.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="60-second"):
        tempo.analyze_tempo(source)

    def change(*args, **kwargs):
        source.write_bytes(source.read_bytes() + b"changed")
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(result()), stderr="")

    monkeypatch.setattr(tempo.subprocess, "run", change)
    with pytest.raises(RuntimeError, match="changed"):
        tempo.analyze_tempo(source)


def test_process_uses_argument_list_and_validates_reply(tmp_path, monkeypatch):
    source = wav(tmp_path / "spaces & punctuation.wav")
    monkeypatch.setattr(tempo, "verify_runtime", lambda: tmp_path / "helper.exe")
    expected = result()

    def run(args, **kwargs):
        assert args[args.index("--input") + 1] == str(source.resolve())
        assert not kwargs.get("shell", False)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 60
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(expected), stderr="")

    monkeypatch.setattr(tempo.subprocess, "run", run)
    assert tempo.analyze_tempo(source) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Bundled tempo runtime is Windows x64")
@pytest.mark.parametrize("bpm", [75.0, 120.0, 123.45, 174.0])
def test_real_rust_binary_estimates_independent_click_tracks(tmp_path, bpm):
    source = wav(tmp_path / "clicks.wav", bpm=bpm)
    original = source.read_bytes()
    reply = tempo.analyze_tempo(source)
    assert reply["status"] == "ok"
    assert reply["bpm"] == pytest.approx(bpm, abs=0.25), reply
    assert source.read_bytes() == original


@pytest.mark.skipif(sys.platform != "win32", reason="Bundled tempo runtime is Windows x64")
def test_real_rust_binary_does_not_invent_bpm_for_silence(tmp_path):
    reply = tempo.analyze_tempo(wav(tmp_path / "silence.wav", silence=True))
    assert reply["bpm"] is None and reply["candidates"] == []


@pytest.mark.skipif(sys.platform != "win32", reason="Bundled tempo runtime is Windows x64")
@pytest.mark.parametrize("bpm", [96.0, 123.45])
def test_real_rust_binary_handles_changing_drum_timbres(tmp_path, bpm):
    reply = tempo.analyze_tempo(mixed_drums(tmp_path / "drums-and-bass.wav", bpm))
    assert reply["bpm"] == pytest.approx(bpm, abs=0.25), reply


@pytest.mark.skipif(sys.platform != "win32", reason="Bundled tempo runtime is Windows x64")
def test_mcp_tempo_tool_is_offline_and_returns_rust_result(tmp_path):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    source = wav(tmp_path / "tempo – unicode + space.wav", bpm=128)

    async def run():
        transport = StdioTransport(command=sys.executable, args=["-m", "fl_studio_mcp.server"],
                                   keep_alive=False)
        async with Client(transport, timeout=30, init_timeout=30) as client:
            reply = (await client.call_tool("fl_audio_bpm", {"path": str(source)})).data
        assert reply["method"] == tempo.METHOD
        assert reply["bpm"] == pytest.approx(128, abs=0.25), reply

    asyncio.run(asyncio.wait_for(run(), 60))
