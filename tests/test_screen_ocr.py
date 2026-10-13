"""Screen coordinate and cleanup tests; the native fixture never touches the desktop."""

import asyncio
import base64
import copy
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fl_studio_mcp.utils import screen_ocr as so

OCR = {
    "success": True,
    "engine": "Windows.Media.Ocr",
    "language": "en-US",
    "width": 400,
    "height": 200,
    "text": "ComposeWithLLM",
    "lines": [
        {
            "text": "ComposeWithLLM",
            "bounds": [10, 20, 150, 24],
            "words": [{"text": "ComposeWithLLM", "bounds": [10, 20, 150, 24]}],
        }
    ],
}


class FakeFL:
    def __init__(self):
        self.calls = []
        self.path = None
        self.method = "screen_copy"
        self.write_file = True
        self.bounds = {"x": -1000, "y": 60, "width": 800, "height": 400}

    async def win(self, name, **kwargs):
        self.calls.append((name, kwargs))
        self.path = Path(kwargs["outputPath"])
        if self.write_file:
            self.path.write_bytes(b"fake screenshot; OCR itself is mocked")
        return {
            "success": True,
            "width": 400,
            "height": 200,
            "filePath": str(self.path),
            "capture": {"method": self.method, "bounds": self.bounds, "freshness": "unknown"},
        }


def fake_ocr(monkeypatch, result=None):
    async def read(path, timeout=15):
        assert Path(path).is_file()
        return copy.deepcopy(OCR if result is None else result)

    monkeypatch.setattr(so, "_ocr_file", read)


def test_region_capture_scales_ocr_to_physical_desktop_and_deletes_pixels(monkeypatch):
    fake_ocr(monkeypatch)
    fl = FakeFL()
    result = asyncio.run(so.read_region(fl, [-1000, 60, 800, 400]))
    assert result["lines"][0]["bounds"] == [-980, 100, 300, 48]
    assert result["lines"][0]["words"][0]["bounds"] == [-980, 100, 300, 48]
    assert result["text"] == "ComposeWithLLM"
    assert result["coordinate_space"] == "physical_desktop"
    assert result["capture"]["freshness"] == "unknown"
    assert not fl.path.parent.exists()
    name, call = fl.calls[0]
    assert name == "screenshot_control"
    assert call["target"] == "region"
    assert call["outputMode"] == "file"
    assert call["imageFormat"] == "png"
    assert call["includeImage"] is False
    assert call["annotate"] is False
    assert "windowHandle" not in call


def test_rectangle_dictionary_is_supported(monkeypatch):
    fake_ocr(monkeypatch)
    fl = FakeFL()
    result = asyncio.run(so.read_region(fl, fl.bounds))
    assert result["bounds"] == [-1000, 60, 800, 400]


def test_enlarged_ocr_retains_physical_click_coordinates(monkeypatch):
    from PIL import Image

    class PixelsFL(FakeFL):
        async def win(self, name, **kwargs):
            response = await super().win(name, **kwargs)
            Image.new("RGB", (400, 200), "white").save(self.path)
            return response

    async def read(path):
        with Image.open(path) as img:
            assert img.size == (1600, 800)
        result = copy.deepcopy(OCR)
        result.update(width=1600, height=800)
        for line in result["lines"]:
            line["bounds"] = [v * 4 for v in line["bounds"]]
            for word in line["words"]:
                word["bounds"] = [v * 4 for v in word["bounds"]]
        return result

    monkeypatch.setattr(so, "_ocr_file", read)
    fl = PixelsFL()
    result = asyncio.run(so.read_region(fl, fl.bounds, scale=4))
    assert result["lines"][0]["bounds"] == [-980, 100, 300, 48]
    assert not fl.path.parent.exists()


@pytest.mark.parametrize("bounds", [[0, 0, 0, 20], [0, 0, 10_001, 20], [1.5, 0, 20, 20], []])
def test_invalid_capture_bounds_fail_before_screenshot(bounds):
    fl = FakeFL()
    with pytest.raises(ValueError):
        asyncio.run(so.read_region(fl, bounds))
    assert not fl.calls


@pytest.mark.parametrize("case", ["printwindow", "missing_file", "wrong_bounds"])
def test_incomplete_capture_cannot_supply_click_coordinates(monkeypatch, case):
    fake_ocr(monkeypatch)
    fl = FakeFL()
    if case == "printwindow":
        fl.method = "print_window"
    elif case == "missing_file":
        fl.write_file = False
    else:
        fl.bounds["width"] = 799
    with pytest.raises(RuntimeError):
        asyncio.run(so.read_region(fl, [-1000, 60, 800, 400]))
    assert not fl.path.parent.exists()


def test_ocr_failure_deletes_captured_pixels(monkeypatch):
    async def failure(path):
        raise RuntimeError("English OCR unavailable")

    monkeypatch.setattr(so, "_ocr_file", failure)
    fl = FakeFL()
    with pytest.raises(RuntimeError, match="English OCR unavailable"):
        asyncio.run(so.read_region(fl, fl.bounds))
    assert not fl.path.parent.exists()


def test_image_dimension_mismatch_is_rejected(monkeypatch):
    result = copy.deepcopy(OCR)
    result["width"] = 399
    fake_ocr(monkeypatch, result)
    fl = FakeFL()
    with pytest.raises(RuntimeError, match="dimensions differ"):
        asyncio.run(so.read_region(fl, fl.bounds))
    assert not fl.path.parent.exists()


@pytest.mark.parametrize("rect", [[0, 0, 0, 10], [0, 0, float("nan"), 10], [399, 20, 10, 10]])
def test_invalid_ocr_rectangles_are_rejected(monkeypatch, rect):
    result = copy.deepcopy(OCR)
    result["lines"][0]["bounds"] = rect
    fake_ocr(monkeypatch, result)
    fl = FakeFL()
    with pytest.raises(RuntimeError, match="rectangle"):
        asyncio.run(so.read_region(fl, fl.bounds))


@pytest.mark.skipif(os.name != "nt", reason="Windows subprocess launch contract")
def test_ocr_helper_timeout_terminates_hidden_process(tmp_path, monkeypatch):
    image = tmp_path / "fixture.png"
    image.write_bytes(b"mock image")

    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            if not self.killed:
                await asyncio.sleep(60)
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -1

    process = Process()
    launched = []

    async def create(*args, **kwargs):
        launched.append((args, kwargs))
        return process

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(so.shutil, "which", lambda _: "powershell.exe")
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(so._ocr_file(image, timeout=0.01))
    assert process.killed
    assert launched[0][1]["creationflags"] == 0x08000000
    assert launched[0][0][-1] == str(image)


@pytest.mark.skipif(os.name != "nt", reason="Windows.Media.Ocr requires Windows")
def test_native_windows_ocr_reads_generated_fixture_without_screen_capture(tmp_path):
    executable = shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Windows PowerShell is unavailable")
    image = tmp_path / "menu fixture.png"
    literal = str(image).replace("'", "''")
    script = """
Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap 700,120
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$font = New-Object System.Drawing.Font 'Segoe UI',28
try {
    $graphics.Clear([System.Drawing.Color]::White)
    $graphics.DrawString('ComposeWithLLM',$font,[System.Drawing.Brushes]::Black,20,25)
    $bitmap.Save('IMAGE_PATH',[System.Drawing.Imaging.ImageFormat]::Png)
} finally { $font.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
""".replace("IMAGE_PATH", literal)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # Cold PowerShell/WinRT startup on CI is not the behavior this test measures.
    # Keep a bounded test budget; production and timeout/cleanup tests stay unchanged.
    subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True,
        capture_output=True,
        timeout=30,
        creationflags=0x08000000,
    )
    result = asyncio.run(so._ocr_file(image, timeout=30))
    assert result["text"] == "ComposeWithLLM"
    assert result["language"] == "en-US"
    assert (result["width"], result["height"]) == (700, 120)
    line = result["lines"][0]
    assert line["words"][0]["text"] == "ComposeWithLLM"
    x, y, width, height = line["bounds"]
    assert 15 <= x <= 40 and 20 <= y <= 50
    assert 200 < width < 400 and 20 < height < 70
