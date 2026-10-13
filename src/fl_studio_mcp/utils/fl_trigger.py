"""Explicitly permitted desktop shortcut dispatch for the piano script."""

from __future__ import annotations

import math
import platform
import subprocess
import time
from contextlib import ExitStack

from fl_studio_mcp.utils.interaction_policy import require_desktop_input

TRIGGER_DELAY = 0.0


class FLStudioTrigger:
    def __init__(self) -> None:
        self._system = platform.system()
        self._trigger_func = {"Darwin": self._trigger_macos, "Windows": self._trigger_windows}.get(
            self._system
        )

    def _trigger_macos(self) -> bool:
        require_desktop_input("Legacy piano trigger")
        script = (
            'tell application "FL Studio" to activate\n'
            'tell application "System Events"\n'
            'tell process "FL Studio"\n'
            'keystroke "y" using {option down, command down}\n'
            "end tell\nend tell"
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script], timeout=10, capture_output=True, check=False
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _trigger_windows(self) -> bool:
        require_desktop_input("Legacy piano trigger")
        if not self._focus_fl_studio_windows():
            return False
        try:
            from pynput.keyboard import Controller, Key

            require_desktop_input("Legacy piano trigger")
            keyboard = Controller()
            with ExitStack() as held:
                for modifier in (Key.ctrl, Key.alt):
                    held.enter_context(keyboard.pressed(modifier))
                keyboard.tap("y")
            return True
        except Exception:
            return False

    def trigger(self, delay: float = TRIGGER_DELAY) -> bool:
        require_desktop_input("Legacy piano trigger")
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
            raise ValueError("Trigger delay must be finite and nonnegative")
        operation = self._trigger_func
        sent = bool(operation()) if operation is not None else False
        if sent and delay:
            time.sleep(delay)
        return sent

    @property
    def is_supported(self) -> bool:
        return callable(self._trigger_func)

    @property
    def platform(self) -> str:
        return str(self._system)

    @property
    def keystroke(self) -> str:
        return {"Windows": "Ctrl+Alt+Y", "Darwin": "Cmd+Opt+Y"}.get(self._system, "Unknown")

    def _focus_fl_studio_windows(self) -> bool:
        """Identify one real FL main-window class and verify foreground ownership."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetClassNameW.restype = ctypes.c_int
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.GetForegroundWindow.argtypes = []
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            found = []

            def visit(hwnd, _):
                name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, name, 256)
                if name.value == "TFruityLoopsMainForm" and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                return True

            user32.EnumWindows(callback_type(visit), 0)
            if len(found) != 1:
                return False
            hwnd = found[0]
            if user32.GetForegroundWindow() == hwnd:
                return True
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if user32.GetForegroundWindow() == hwnd:
                    return True
                time.sleep(0.01)
            return False
        except Exception:
            return False


_trigger: FLStudioTrigger | None = None


def get_trigger() -> FLStudioTrigger:
    global _trigger
    instance = _trigger
    if instance is None:
        instance = FLStudioTrigger()
        _trigger = instance
    return instance


def trigger_fl_studio(delay: float = TRIGGER_DELAY) -> bool:
    instance = get_trigger()
    return instance.trigger(delay=delay)
