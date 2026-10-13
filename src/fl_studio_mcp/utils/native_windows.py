"""Inspect physical HWND geometry and explicitly expose FL without desktop input."""

from __future__ import annotations

import ctypes as c
import os
from ctypes import wintypes as w


def api():
    if os.name != "nt":
        raise RuntimeError("This workflow requires Windows")
    user = c.WinDLL("user32", use_last_error=True)
    callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    for name, args, result in (
        ("EnumChildWindows", [w.HWND, callback, w.LPARAM], w.BOOL),
        ("EnumWindows", [callback, w.LPARAM], w.BOOL),
        ("GetClassNameW", [w.HWND, w.LPWSTR, c.c_int], c.c_int),
        ("GetWindowTextW", [w.HWND, w.LPWSTR, c.c_int], c.c_int),
        ("GetWindowRect", [w.HWND, c.POINTER(w.RECT)], w.BOOL),
        ("IsWindowVisible", [w.HWND], w.BOOL),
        ("IsWindowEnabled", [w.HWND], w.BOOL),
        ("GetWindowThreadProcessId", [w.HWND, c.POINTER(w.DWORD)], w.DWORD),
        ("GetAncestor", [w.HWND, w.UINT], w.HWND),
        ("GetDpiForWindow", [w.HWND], w.UINT),
        ("WindowFromPoint", [w.POINT], w.HWND),
        ("GetParent", [w.HWND], w.HWND),
        ("SetWindowPos", [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT],
         w.BOOL),
    ):
        method = getattr(user, name)
        method.argtypes, method.restype = args, result
    # Physical coordinates must agree with Windows MCP even at non-100% DPI.
    user.SetThreadDpiAwarenessContext.argtypes = [w.HANDLE]
    user.SetThreadDpiAwarenessContext.restype = w.HANDLE
    return user, callback


def windows(parent=None, pid=None):
    user, callback = api()
    prior = user.SetThreadDpiAwarenessContext(w.HANDLE(-4))
    result = []

    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        actual_pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, c.byref(actual_pid))
        if pid is not None and pid != actual_pid.value:
            return True
        cls, title = c.create_unicode_buffer(256), c.create_unicode_buffer(2048)
        rect = w.RECT()
        user.GetClassNameW(hwnd, cls, len(cls))
        user.GetWindowTextW(hwnd, title, len(title))
        user.GetWindowRect(hwnd, c.byref(rect))
        result.append(
            {
                "handle": str(hwnd),
                "class": cls.value,
                "title": title.value,
                "bounds": [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top],
                "pid": actual_pid.value,
                "parent": str(user.GetParent(hwnd) or 0),
                "enabled": bool(user.IsWindowEnabled(hwnd)),
                "dpi": user.GetDpiForWindow(hwnd),
            }
        )
        return True

    try:
        if parent is None:
            user.EnumWindows(callback(visit), 0)
        else:
            user.EnumChildWindows(int(parent), callback(visit), 0)
    finally:
        if prior:
            user.SetThreadDpiAwarenessContext(prior)
    return result


def unique_control(main, class_name, title=None):
    matches = [
        item
        for item in windows(main)
        if item["class"] == class_name and (title is None or item["title"] == title)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one visible {class_name}; found {len(matches)}")
    return matches[0]


def point_belongs_to(point, parent):
    """Accept the target or its children, excluding popups it merely owns."""
    user, _ = api()
    prior = user.SetThreadDpiAwarenessContext(w.HANDLE(-4))
    try:
        hwnd = user.WindowFromPoint(w.POINT(*point))
        while hwnd:
            if int(hwnd) == int(parent):
                return True
            # GetParent follows popup ownership, which can hide an occluding window.
            hwnd = user.GetAncestor(hwnd, 1)  # GA_PARENT: actual parent only.
        return False
    finally:
        if prior:
            user.SetThreadDpiAwarenessContext(prior)


def expose_fl_window(expected):
    """Raise an identity-checked FL window without activation, moving or resizing.

    This is an explicit write helper, never part of metadata inspection. The
    caller must still verify its actual drop target after exposing the main window.
    """
    rows = windows()
    matches = [row for row in rows if row["handle"] == expected["handle"]]
    if (len(matches) != 1 or matches[0]["class"] != "TFruityLoopsMainForm"
            or any(matches[0][key] != expected[key] for key in ("pid", "title"))
            or not matches[0]["enabled"]
            or any(row["pid"] == expected["pid"] and row["class"] == "#32770"
                   for row in rows)):
        raise RuntimeError("FL window changed or a modal operation is active")
    user, _ = api()
    # HWND_TOP; SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER.
    if not user.SetWindowPos(int(expected["handle"]), 0, 0, 0, 0, 0, 0x213):
        raise OSError("Windows could not expose the FL window")
