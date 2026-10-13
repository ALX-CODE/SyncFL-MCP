"""Fresh visible-screen OCR for FL's owner-drawn menus, using Windows OCR.

Unlike PrintWindow, a screen-copy region includes FL popup menus. Returned line
and word bounds are physical desktop coordinates, including capture scaling and
negative monitor origins. The caller must verify foreground/target identity and
an exact unique label before acting; OCR does not establish application ownership.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import tempfile
from pathlib import Path


def _region(bounds):
    if isinstance(bounds, dict):
        bounds = [bounds.get(key) for key in ("x", "y", "width", "height")]
    if (
        not isinstance(bounds, (list, tuple))
        or len(bounds) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in bounds)
    ):
        raise ValueError("Region must be physical integer [x, y, width, height]")
    x, y, width, height = bounds
    if (
        not 0 < width <= 10000
        or not 0 < height <= 10000
        or width * height > 40_000_000
        or not -(2**31) <= x < 2**31
        or not -(2**31) <= y < 2**31
    ):
        raise ValueError("OCR region exceeds supported dimensions")
    return [x, y, width, height]


async def _ocr_file(path, timeout=15):
    """Run the bundled WinRT helper with bounded lifetime and no shell expansion."""
    if os.name != "nt":
        raise RuntimeError("Built-in screen OCR requires Windows")
    executable = shutil.which("powershell.exe")
    if not executable:
        raise RuntimeError("Windows PowerShell is unavailable for built-in OCR")
    script = Path(__file__).with_suffix(".ps1")
    process = await asyncio.create_subprocess_exec(
        executable,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
        "-ImagePath",
        str(Path(path).resolve(strict=True)),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000,  # CREATE_NO_WINDOW; no visible PowerShell helper.
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
        raise RuntimeError("Windows OCR failed: " + detail)
    if len(stdout) > 4_000_000:
        raise RuntimeError("Windows OCR response exceeds the output limit")
    try:
        result = json.loads(stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Windows OCR did not return valid JSON") from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("Windows OCR result did not report success")
    return result


def _screen_rect(rect, capture, image_size):
    if (
        not isinstance(rect, (list, tuple))
        or len(rect) != 4
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in rect)
        or not all(math.isfinite(v) for v in rect)
    ):
        raise RuntimeError("Windows OCR returned an invalid text rectangle")
    x, y, width, height = rect
    image_width, image_height = image_size
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise RuntimeError("Windows OCR returned an invalid text rectangle")
    if x + width > image_width + 1 or y + height > image_height + 1:
        raise RuntimeError("Windows OCR text rectangle falls outside the captured image")
    scale_x, scale_y = capture[2] / image_width, capture[3] / image_height
    return [
        capture[0] + x * scale_x,
        capture[1] + y * scale_y,
        width * scale_x,
        height * scale_y,
    ]


async def read_region(fl, bounds, scale=1):
    """Capture a visible region once, OCR it locally and delete temporary pixels.

    ``bounds`` accepts physical [x,y,width,height] or matching dictionary. Returns
    text, lines and words with physical ``bounds``, source capture metadata and
    OCR engine/language. No screenshot image or persistent file is returned.
    """
    requested = _region(bounds)
    if (
        type(scale) is not int
        or not 1 <= scale <= 4
        or requested[2] * requested[3] * scale**2 > 40_000_000
    ):
        raise ValueError("OCR scale must be 1..4 within the 40 million pixel limit")
    with tempfile.TemporaryDirectory(prefix="fl-mcp-ocr-") as directory:
        image = Path(directory) / "region.png"
        screenshot = await fl.win(
            "screenshot_control",
            action="capture",
            target="region",
            regionX=requested[0],
            regionY=requested[1],
            regionWidth=requested[2],
            regionHeight=requested[3],
            annotate=False,
            includeImage=False,
            imageFormat="png",
            outputMode="file",
            outputPath=str(image),
            timeoutMs=5000,
        )
        if screenshot.get("success") is False or not image.is_file():
            raise RuntimeError("Screen region capture did not produce the requested PNG file")
        capture = screenshot.get("capture") or {}
        if capture.get("method") != "screen_copy":
            raise RuntimeError("OCR requires verified screen_copy capture, not PrintWindow")
        physical = _region(capture.get("bounds"))
        if physical != requested:
            raise RuntimeError("Screen capture bounds differ from the requested physical region")
        if scale != 1:
            from PIL import Image, ImageOps

            with Image.open(image) as original:
                resized = ImageOps.autocontrast(original.convert("L")).resize(
                    (screenshot["width"] * scale, screenshot["height"] * scale),
                    Image.Resampling.LANCZOS,
                )
                resized.save(image)
        result = await _ocr_file(image)
        size = result.get("width"), result.get("height")
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in size):
            raise RuntimeError("Windows OCR did not report valid image dimensions")
        if size != (screenshot.get("width") * scale, screenshot.get("height") * scale):
            raise RuntimeError("OCR image dimensions differ from screenshot output dimensions")
        lines = []
        for line in result.get("lines", []):
            words = [
                {"text": word["text"], "bounds": _screen_rect(word["bounds"], physical, size)}
                for word in line.get("words", [])
            ]
            lines.append(
                {
                    "text": line["text"],
                    "bounds": _screen_rect(line["bounds"], physical, size),
                    "words": words,
                }
            )
        return {
            "success": True,
            "engine": result.get("engine", "Windows.Media.Ocr"),
            "language": result.get("language", "en-US"),
            "text": result.get("text", ""),
            "lines": lines,
            "bounds": physical,
            "image_size": list(size),
            "coordinate_space": "physical_desktop",
            "capture": capture,
        }
