"""Cold-process regression: audio must work before any other tool imports NumPy."""

import asyncio
import os
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from fl_studio_mcp.utils import audio_files
from fl_studio_mcp.utils.key_analysis import _progression


def test_optional_audio_absence_does_not_prevent_midi_server_startup(monkeypatch):
    monkeypatch.setattr(audio_files.importlib.util, "find_spec", lambda name: None)
    assert audio_files.initialize_audio_runtime() is False


def test_numpy_only_extra_is_initialized_before_mcp_worker_threads(monkeypatch):
    imported = []
    monkeypatch.setattr(audio_files.importlib.util, "find_spec",
                        lambda name: object() if name == "numpy" else None)
    monkeypatch.setattr(audio_files.importlib, "import_module", imported.append)
    assert audio_files.initialize_audio_runtime() is True
    assert imported == ["numpy"]


def test_cold_stdio_audio_inspection_completes_without_fl_or_midi(tmp_path):
    source = tmp_path / "antiphase.wav"
    sf.write(source, np.tile([0.25, -0.25], (8000, 1)), 8000, subtype="PCM_24")
    root = Path(__file__).resolve().parents[1]

    async def run():
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "fl_studio_mcp.server"],
            cwd=str(root),
            env=dict(os.environ),
            keep_alive=False,
        )
        async with Client(transport, timeout=15, init_timeout=30) as client:
            result = (await client.call_tool("fl_audio_inspect", dict(path=str(source)))).data
            assert result["frames"] == 8000 and result["silent"] is False
            assert result["peak_per_channel"] == [0.25, 0.25]
            assert result["rms_per_channel"] == [0.25, 0.25]

    asyncio.run(asyncio.wait_for(run(), 45))


def test_cold_stdio_key_analysis_completes_without_fl_or_midi(tmp_path):
    source = tmp_path / "c-major.wav"
    samples = _progression([(60, 64, 67), (65, 69, 72), (67, 71, 74), (60, 64, 67)] * 2)
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        output.writeframes(np.rint(samples * 32767).astype("<i2").tobytes())
    before = source.read_bytes()
    root = Path(__file__).resolve().parents[1]

    async def run():
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "fl_studio_mcp.server"],
            cwd=str(root),
            env=dict(os.environ),
            keep_alive=False,
        )
        async with Client(transport, timeout=15, init_timeout=30) as client:
            result = (await client.call_tool("fl_audio_key", dict(path=str(source)))).data
            assert result["candidates"][0]["key"] == "C major"
            assert len(result["candidates"]) == 5
            assert result["no_tonal_evidence"] is False
            assert result["confidence_basis"] == (
                "relative_softmax_over_all_24_keys_not_calibrated_probability"
            )
            assert (await client.call_tool("fl_build_info", {})).data["workflow_revision"] == 12

    asyncio.run(asyncio.wait_for(run(), 45))
    assert source.read_bytes() == before
