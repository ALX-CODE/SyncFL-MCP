"""Bounded Serum 2.1.2 file-dialog workflow for the observed 1200x771 editor.

Native FL identifies the instrument; UIA was inspected but exposes only its
wrapper. Read-only Win32 geometry locates that wrapper; Windows MCP sends input.
Unfamiliar sizes fail rather than scaling remembered coordinates.
"""

import asyncio
import math
import os
import re
import time
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import transaction_lock
from fl_studio_mcp.utils.serum_live import _identity, _inspect_locked, _native, _stopped
from fl_studio_mcp.utils.serum_presets import read_preset
from fl_studio_mcp.utils.windows_workflows import WindowsFL, _workflow_lock


def find_editor(main_handle, channel_name):
    if os.name != "nt":
        raise RuntimeError("Serum GUI workflow requires Windows")
    import ctypes as c
    from ctypes import wintypes as w

    u = c.WinDLL("user32", use_last_error=True)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    for name, args, result in (
        ("EnumChildWindows", [w.HWND, callback_type, w.LPARAM], w.BOOL),
        ("GetWindowTextW", [w.HWND, w.LPWSTR, c.c_int], c.c_int),
        ("GetClassNameW", [w.HWND, w.LPWSTR, c.c_int], c.c_int),
        ("GetWindowRect", [w.HWND, c.POINTER(w.RECT)], w.BOOL),
        ("IsWindowVisible", [w.HWND], w.BOOL),
    ):
        fn = getattr(u, name)
        fn.argtypes, fn.restype = args, result
    matches = []

    @callback_type
    def visit(handle, _):
        title, cls = c.create_unicode_buffer(1024), c.create_unicode_buffer(256)
        u.GetWindowTextW(handle, title, len(title))
        u.GetClassNameW(handle, cls, len(cls))
        if (
            cls.value == "TPluginForm"
            and u.IsWindowVisible(handle)
            and title.value.startswith(channel_name + "\ue5a0")
        ):
            rect = w.RECT()
            if u.GetWindowRect(handle, c.byref(rect)):
                matches.append(
                    {
                        "handle": str(handle),
                        "title": title.value,
                        "bounds": [
                            rect.left,
                            rect.top,
                            rect.right - rect.left,
                            rect.bottom - rect.top,
                        ],
                    }
                )
        return True

    u.EnumChildWindows(int(main_handle), visit, 0)
    if len(matches) != 1:
        raise RuntimeError("Expected one visible identity-matched Serum wrapper")
    if matches[0]["bounds"][2:] != [1200, 771]:
        raise RuntimeError("Serum editor size differs from calibrated 1200x771; inspect UI")
    return matches[0]


def expected_controls(body):
    """Only observed payload transfer functions; no guessed plugin defaults."""
    controls = {}
    mappings = [
        ("Global0", "kParamMasterVolume", "master.volume_percent", lambda v: math.sqrt(v) * 100),
        ("Global0", "kParamMonoToggle", "voice.mono", bool),
        ("Oscillator0", "kParamFine", "osc_a.fine_cents", float),
        ("Oscillator0", "kParamVolume", "osc_a.level_percent", lambda v: math.sqrt(v) * 100),
        ("Oscillator0", "kParamUnison", "osc_a.unison_normalized", lambda v: (v - 1) / 15),
        ("Oscillator0", "kParamDetune", "osc_a.detune_normalized", math.sqrt),
    ]
    for i in (0, 1):
        for part in ("Attack", "Hold", "Decay", "Release"):
            mappings.append(
                (f"Env{i}", "kParam" + part, f"env{i + 1}.{part.lower()}_ms", lambda v: v * 1000)
            )
        mappings.append((f"Env{i}", "kParamSustain", f"env{i + 1}.sustain_normalized", float))
        mappings.append((f"Env{i}", "kParamCurve2", f"env{i + 1}.decay_curve_percent", float))
    if body.get("ModSlot0", {}).get("source"):
        mappings.append(("ModSlot0", "kParamAmount", "mod1.amount_percent", float))
    for module, parameter, key, convert in mappings:
        values = body.get(module, {}).get("plainParams", {})
        if parameter in values:
            controls[key] = convert(values[parameter])
    if len(controls) < 3:
        raise ValueError("Preset lacks enough mapped settings for verified loading")
    return controls


def display_matches(key, display, expected):
    """FL's normalized cache can stay stale after native Serum preset loads.

    Compare Serum's live formatted values with tolerances matching display
    precision. Never claim an exact numeric match from rounded text.
    """
    text = display.strip().casefold()
    if isinstance(expected, bool):
        return text == ("on" if expected else "off")
    match = re.match(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))", text)
    if key.endswith("sustain_normalized") and (text == "--" or "inf" in text or "∞" in text):
        return expected == 0
    if not match:
        return False
    value = float(match[1])
    tolerance = 0.51
    if key.endswith("_ms"):
        if "ms" in text:
            tolerance = 0.51 if value >= 10 else 0.051
        elif re.search(r"\bs\b", text):
            value *= 1000
            tolerance = 5.1
        else:
            return False
    elif key.endswith("sustain_normalized"):
        if "db" not in text:
            return False
        value = 10 ** (value / 40)
        tolerance = 0.003
    elif key.endswith("unison_normalized"):
        value = (value - 1) / 15
        tolerance = 0.0001
    elif key.endswith("detune_normalized"):
        # Compare in the displayed squared domain; it has only two decimals.
        expected = expected**2
        tolerance = 0.0051
    return math.isclose(value, expected, rel_tol=0, abs_tol=tolerance)


async def load_serum_preset(channel, path):
    """Load a preset with geometry/identity guards, then verify mapped native controls."""
    path = Path(path).expanduser().resolve(strict=True)
    header, body = read_preset(path)
    expected = expected_controls(body)
    conn = get_connection()
    started = time.perf_counter()
    async with _workflow_lock:
        with transaction_lock(conn._hardware_dir / "mcp-serum.lock", 5):
            with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
                _stopped(conn)
                identity = _identity(conn, channel)
                async with WindowsFL() as fl:
                    _native(conn, "channels.showEditor", [channel, 1, True], True)
                    await fl.win("window_management", action="activate", handle=fl.handle)
                    editor = find_editor(fl.handle, identity["channel_name"])
                    windows = await fl.win("window_management", action="find", processName="FL64")
                    main = next(w for w in windows["windows"] if w["handle"] == fl.handle)
                    x, y, _, _ = editor["bounds"]
                    left, top, width, height = main["bounds"]
                    # The observed menu opens right/down. Reject positions where
                    # Windows would clamp/flip it and invalidate the row coordinate.
                    if not (
                        left <= x
                        and top <= y
                        and x + 1300 <= left + width
                        and y + 630 <= top + height
                    ):
                        raise RuntimeError("Serum menu would exceed calibrated screen area")
                    if (
                        _identity(conn, channel) != identity
                        or find_editor(fl.handle, identity["channel_name"]) != editor
                    ):
                        raise RuntimeError("Serum target moved or changed before input")
                    _stopped(conn)
                    await fl.click(1066, 43, handle=editor["handle"])
                    await fl.click(1115, 224, handle=editor["handle"])
                    dialog = await fl.dialog("Load Preset")
                    await fl.win(
                        "keyboard_control",
                        action="type",
                        windowHandle=dialog["handle"],
                        text=str(path),
                        clearFirst=True,
                    )
                    await fl.key("enter", dialog["handle"])
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        state = await fl.win("window_management", action="find", processName="FL64")
                        if not any(w["handle"] == dialog["handle"] for w in state["windows"]):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise RuntimeError("Preset dialog did not close; inspect without replaying")
            # Read after FL processes the load, while the same Serum lock still
            # prevents another cooperating client from replacing it.
            snapshot = _inspect_locked(conn, channel)
    if {k: snapshot["identity"][k] for k in identity} != identity:
        raise RuntimeError("Serum identity changed after loading")
    actual = {key: row for group in snapshot["groups"].values() for key, row in group.items()}
    mismatches = {
        key: {
            "expected": value,
            "actual_display": actual.get(key, {}).get("display"),
            "host_normalized": actual.get(key, {}).get("host_normalized"),
        }
        for key, value in expected.items()
        if key not in actual or not display_matches(key, actual[key]["display"], value)
    }
    return {
        "success": not mismatches,
        "path": str(path),
        "preset_name": header["presetName"],
        "channel": channel,
        "verified_controls": len(expected) - len(mismatches),
        "mismatches": mismatches,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "verification": (
            "Serum live parameter displays at their reported precision. "
            "FL normalized caches may be stale. FX/routing are file-validated, "
            "not native-readback verified."
        ),
    }
