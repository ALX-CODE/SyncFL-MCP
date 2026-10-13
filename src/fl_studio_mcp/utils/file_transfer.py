"""Deliver one file through a guarded OLE drag; callers verify FL's native state."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import os
import subprocess
import tempfile
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from fl_studio_mcp.utils import native_windows
from fl_studio_mcp.utils.interaction_policy import require_desktop_input

HELPER_SIZE = (270, 100)
HELPER_LIFETIME = 60


def _compiler():
    directory = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET"
    for architecture in ("Framework64", "Framework"):
        candidate = directory / architecture / "v4.0.30319" / "csc.exe"
        if candidate.is_file():
            return candidate
    raise RuntimeError("The Windows .NET Framework C# compiler was not found")


def _helper_executable():
    if os.name != "nt":
        raise RuntimeError("OLE file transfer requires Windows")
    source = Path(__file__).with_name("FileTransfer.cs")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "fl-studio-mcp" / "file-transfer" / digest
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / ("FLMCPFileTransfer_" + digest[:16] + ".exe")
    if executable.is_file() and executable.stat().st_size:
        return executable
    temporary = directory / (uuid.uuid4().hex + ".exe")
    try:
        result = subprocess.run(
            [str(_compiler()), "/nologo", "/target:exe", "/optimize+",
             "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
             "/out:" + str(temporary), str(source)],
            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode or not temporary.is_file():
            raise RuntimeError("File transfer helper compilation failed: " + result.stdout.strip())
        try:
            temporary.replace(executable)
        except PermissionError:
            # Another process may have just published and launched the same build.
            if not executable.is_file() or not executable.stat().st_size:
                raise
        return executable
    finally:
        temporary.unlink(missing_ok=True)


def _monitor_for(point):
    """Return physical monitor/work rectangles; primary screen is the supported route."""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user, _ = native_windows.api()
    user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user.MonitorFromPoint.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    prior = user.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))
    try:
        monitor = user.MonitorFromPoint(wintypes.POINT(*point), 0)
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not monitor or not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ValueError("File destination is outside every monitor")
        if not info.flags & 1:
            raise ValueError("File transfer currently requires FL on the primary monitor")

        def rectangle(value):
            return (value.left, value.top, value.right - value.left, value.bottom - value.top)

        return {"bounds": rectangle(info.monitor), "work": rectangle(info.work)}
    finally:
        if prior:
            user.SetThreadDpiAwarenessContext(prior)


def _contains(rectangle, point, margin=0):
    x, y, width, height = rectangle
    return (x - margin <= point[0] < x + width + margin
            and y - margin <= point[1] < y + height + margin)


def _helper_position(work, target):
    left, top, width, height = work
    helper_width, helper_height = HELPER_SIZE
    if width < helper_width + 44 or height < helper_height + 44:
        raise RuntimeError("No safe visible area for the file transfer helper")
    preferred_y = min(top + 550, top + height - helper_height - 22)
    candidates = [(left + 22, preferred_y), (left + width - helper_width - 22, preferred_y),
                  (left + 22, top + 22), (left + width - helper_width - 22, top + 22)]
    for x, y in candidates:
        if not _contains((x, y, helper_width, helper_height), target, margin=24):
            return x, y
    raise RuntimeError("File transfer helper would obscure the destination")


def _signature(path):
    status = path.stat()
    return status.st_size, status.st_mtime_ns


def _helper_controls(process, title):
    forms = [item for item in native_windows.windows(pid=process.pid) if item["title"] == title]
    if len(forms) > 1:
        raise RuntimeError("Ambiguous file transfer helper window")
    if not forms:
        return None
    form = forms[0]
    labels = [item for item in native_windows.windows(form["handle"], pid=process.pid)
              if item["class"].startswith("WindowsForms10.STATIC") and item["enabled"]]
    if len(labels) != 1:
        return None
    return form, labels[0]


def _stop_helper(process):
    """Only reap the subprocess owned by this invocation, never name-match processes."""
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=2)


async def transfer_file(fl, path, target_point, target_handle, validate_target=None):
    """Drag one existing absolute file once; native caller readback is still required.

    The caller owns GUI serialization and prepares the FL destination. Both points
    use physical screen coordinates. No uncertain drag is retried.
    """
    # Starting the topmost OLE source form can steal focus before fl.win is called.
    require_desktop_input("OLE file transfer")
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("File transfer requires an absolute path")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("File transfer requires a regular file")
    if (not isinstance(target_point, (tuple, list)) or len(target_point) != 2
            or any(type(value) is not int for value in target_point)):
        raise ValueError("Destination must contain two integer physical coordinates")
    target_point = tuple(target_point)
    if isinstance(target_handle, bool) or int(target_handle) <= 0:
        raise ValueError("Destination requires a valid window handle")
    target_handle = str(int(target_handle))
    monitor = _monitor_for(target_point)
    position = _helper_position(monitor["work"], target_point)
    if not native_windows.point_belongs_to(target_point, target_handle):
        raise RuntimeError("File destination is not owned by the expected FL window")
    signature = _signature(path)
    executable = _helper_executable()
    title = "FL MCP file transfer " + uuid.uuid4().hex
    process = subprocess.Popen(
        [str(executable), str(path), title, str(position[0]), str(position[1])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    dispatched = False
    deadline = time.monotonic() + HELPER_LIFETIME
    output = ""
    try:
        startup_deadline = time.monotonic() + 8
        controls = None
        while time.monotonic() < startup_deadline and process.poll() is None:
            controls = _helper_controls(process, title)
            if controls:
                break
            await asyncio.sleep(0.05)
        if not controls:
            raise RuntimeError("File transfer helper did not expose its drag source")
        form, _ = controls
        await fl.win("window_management", action="activate", handle=form["handle"])
        controls = _helper_controls(process, title)
        if not controls:
            raise RuntimeError("File transfer source disappeared before dispatch")
        form, label = controls
        x, y, width, height = label["bounds"]
        source = (x + width // 2, y + height // 2)
        if (_contains(form["bounds"], target_point)
                or not _contains(monitor["bounds"], source)
                or not native_windows.point_belongs_to(source, form["handle"])):
            raise RuntimeError("File transfer helper moved or obscures the destination")
        if signature != _signature(path):
            raise RuntimeError("File changed while preparing its transfer")
        # Last check before input: a newly opened dialog must never receive this drop.
        if not native_windows.point_belongs_to(target_point, target_handle):
            raise RuntimeError("File destination changed before dispatch")
        if validate_target is not None:
            validate_target()
        origin_x, origin_y = monitor["bounds"][:2]
        dispatched = True
        await asyncio.wait_for(
            fl.win("mouse_control", action="drag", target="primary_screen",
                   x=source[0] - origin_x, y=source[1] - origin_y,
                   endX=target_point[0] - origin_x, endY=target_point[1] - origin_y,
                   expectedProcessName=executable.stem, expectedWindowTitle=title),
            timeout=max(0.1, deadline - time.monotonic()),
        )
        while process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if process.poll() is None:
            raise TimeoutError("File transfer helper did not finish")
        output, _ = process.communicate(timeout=2)
        effects = [line[7:].strip() for line in output.splitlines() if line.startswith("EFFECT ")]
        if len(effects) != 1 or process.returncode:
            raise RuntimeError("File transfer helper returned no unique OLE receipt")
        return {"dispatched": True, "effect": effects[0],
                "delivery_confirmed": effects[0] == "Copy", "verified": False,
                "path": str(path), "target_handle": target_handle}
    except Exception as exc:
        if dispatched:
            raise RuntimeError(
                "File transfer outcome unknown; inspect FL native state before retrying: "
                + str(exc)
            ) from exc
        raise
    finally:
        _stop_helper(process)
