"""Exact-path FL project saving through the standard Windows Save As dialog.

Mixin for ``WindowsFL(ProjectWindows)``. The caller owns the GUI
workflow lock. Save As inspection always cancels the dialog it opened; it never
accepts a filename merely because the FL title has the same basename.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path


def _window_state(handle):
    """Read HWND identity/ownership without sending messages or desktop input."""
    if os.name != "nt":
        raise RuntimeError("Project GUI workflows require Windows")
    import ctypes as c
    from ctypes import wintypes as w

    user = c.WinDLL("user32", use_last_error=True)
    for name, args, result in (
        ("IsWindow", [w.HWND], w.BOOL),
        ("IsWindowEnabled", [w.HWND], w.BOOL),
        ("IsWindowVisible", [w.HWND], w.BOOL),
        ("GetAncestor", [w.HWND, w.UINT], w.HWND),
        ("GetWindowThreadProcessId", [w.HWND, c.POINTER(w.DWORD)], w.DWORD),
    ):
        fn = getattr(user, name)
        fn.argtypes, fn.restype = args, result
    hwnd = int(handle)
    pid = w.DWORD()
    user.GetWindowThreadProcessId(hwnd, c.byref(pid))
    return {
        "exists": bool(user.IsWindow(hwnd)),
        "enabled": bool(user.IsWindowEnabled(hwnd)),
        "visible": bool(user.IsWindowVisible(hwnd)),
        "root_owner": str(user.GetAncestor(hwnd, 3) or 0),
        "pid": pid.value,
    }


def _nodes(items):
    for item in items:
        yield item
        yield from _nodes(item.get("children") or [])


def _path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".flp":
        raise ValueError("Provide an absolute .flp project path")
    if any(char in str(path) for char in ("\x00", "\r", "\n", '"', "*", "?")):
        raise ValueError("Project path contains an invalid filename character")
    return path.resolve()


def _same_path(left, right):
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _signature(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def _flp_evidence(path):
    before = path.stat()
    with path.open("rb") as stream:
        if stream.read(4) != b"FLhd":
            raise ValueError("Saved file does not contain the FLhd project header")
        stream.seek(0)
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise OSError("FLP changed while verifying its contents")
    return {
        "bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest.hexdigest(),
        "header": "FLhd",
    }


def _backup(path):
    """Copy an occupied destination exclusively, retaining the original bytes."""
    backup = path.with_name(path.name + "." + uuid.uuid4().hex + ".bak")
    before = _signature(path)
    with path.open("rb") as source, backup.open("xb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if _signature(path) != before:
        raise RuntimeError(f"Destination changed while backing it up; inspect {backup}")
    return backup


def _project_path_readback(nodes, title):
    """Validate one complete Save As observation without sending input."""
    filenames = [
        node.get("value")
        for node in nodes
        if node.get("type") == "Edit"
        and node.get("name") == "File name:"
        and isinstance(node.get("value"), str)
    ]
    addresses = [
        node["name"][len("Address: ") :].strip()
        for node in nodes
        if node.get("type") == "ToolBar" and node.get("name", "").startswith("Address: ")
    ]
    if len(filenames) != 1 or len(addresses) != 1 or not filenames[0].strip():
        raise RuntimeError("Save As does not expose one exact filename and directory")
    name = filenames[0].strip()
    if Path(name).name != name or name in (".", ".."):
        raise RuntimeError("Unexpected Save As filename; exact active path unverified")
    if not name.lower().endswith(".flp"):
        name += ".flp"
    path = _path(Path(addresses[0]) / name)
    named = title.startswith(path.name + " - ")
    if ".flp - " in title.lower() and not named:
        raise RuntimeError("Save As filename does not match the active FL project title")
    return path, named


class ProjectWindows:
    """Project persistence with owned-dialog, path and file-content evidence."""

    project_save_timeout = 15.0
    project_dialog_close_timeout = 5.0
    project_info_settle_timeout = 5.0
    project_poll_interval = 0.1

    async def open_project(self, **kwargs):
        from .project_lifecycle import open_project

        return await open_project(self, **kwargs)

    async def render_start(self, **kwargs):
        from .render_windows import render_start

        return await render_start(self, **kwargs)

    async def render_status(self, **kwargs):
        from .render_windows import render_status

        return await render_status(self, **kwargs)

    async def render_cancel(self, **kwargs):
        from .render_windows import render_cancel

        return await render_cancel(self, **kwargs)

    async def create_channel_automation(self, **kwargs):
        from .automation_windows import create_channel_automation

        return await create_channel_automation(self, **kwargs)

    async def _skip_new_project_dialog(self, dialog):
        """FL26's first-save wrapper: choose only the observed local Skip button."""
        from fl_studio_mcp.utils.native_windows import point_belongs_to
        from fl_studio_mcp.utils.screen_ocr import read_region

        _, main = await self._project_guard(allow_dialog=dialog["handle"])
        owned = _window_state(dialog["handle"])
        if (dialog.get("className") != "TNewProjForm" or dialog.get("title") != "Save as"
                or owned["pid"] != main["pid"] or owned["root_owner"] != str(self.handle)
                or not owned["visible"] or not owned["enabled"]):
            raise RuntimeError("First-save dialog identity is unverified")
        await self.win("window_management", action="activate", handle=dialog["handle"])
        result = await read_region(self, dialog["bounds"])
        words = [w for line in result["lines"] for w in line["words"] if w["text"] == "Skip"]
        if len(words) != 1:
            raise RuntimeError("Cannot uniquely locate first-save Skip; no input sent")
        x, y, width, height = words[0]["bounds"]
        point = round(x + width / 2), round(y + height / 2)
        current = [w for w in await self._project_windows()
                   if str(w["handle"]) == str(dialog["handle"])]
        if (len(current) != 1 or current[0].get("bounds") != dialog["bounds"]
                or current[0].get("title") != "Save as"
                or not point_belongs_to(point, dialog["handle"])):
            raise RuntimeError("First-save dialog moved or became obscured")
        await self._project_guard(allow_dialog=dialog["handle"])
        await self.win(
            "ui_batch", windowHandle=dialog["handle"], stopOnError=True,
            operationId="fl-save-skip-" + uuid.uuid4().hex,
            steps=json.dumps([
                dict(action="mouse", mouseAction="click",
                     x=point[0] - dialog["bounds"][0], y=point[1] - dialog["bounds"][1]),
                dict(action="wait_window", windowTitle="Save As", windowHandle=self.handle),
            ]),
        )

    async def _save_project_dialog(self, path, export_wav=False):
        """Type through the dialog input queue and wait for stable filename readback."""
        await self.key("r" if export_wav else "s",
                       modifiers="ctrl" if export_wav else "ctrl,shift")
        dialog = await self._project_dialog()
        nodes = await self._project_dialog_snapshot(dialog)
        filenames = [n for n in nodes if n.get("type") == "Edit"
                     and n.get("name") == "File name:" and n.get("id")]
        if len(filenames) != 1:
            raise RuntimeError("Cannot uniquely locate project filename field")
        field = filenames[0]["id"]
        # FL's common dialog accepts ValuePattern readback while retaining its
        # old internal filename. Real input events are required before Enter.
        # The standard dialog initially focuses this edit. Verify focus in the
        # same input operation; a focus change fails before any text is cleared.
        await self.win("ui_batch", windowHandle=dialog["handle"], stopOnError=True,
                       steps=json.dumps([dict(action="key", key="a", modifiers="ctrl",
                                              expectedElementId=field)]))
        await self.win("keyboard_control", windowHandle=dialog["handle"],
                       action="type", text=str(path))
        await self._project_guard(allow_dialog=dialog["handle"])
        if path.exists():
            raise FileExistsError("Destination appeared before confirmation; no save confirmed")
        await self.win(
            "ui_batch", windowHandle=dialog["handle"], stopOnError=True,
            operationId="fl-save-confirm-" + uuid.uuid4().hex,
            steps=json.dumps([
                dict(action="wait_text", elementId=field, text=str(path), match="exact",
                     timeoutMs=5000),
                dict(action="key", key="enter", expectedElementId=field),
            ]),
        )

    async def _project_focus(self):
        """Focus Playlist: FL's Save As shortcut otherwise saves mixer/plugin presets."""
        from fl_studio_mcp.utils.windows_workflows import native

        await self._project_guard()
        for function in ("ui.showWindow", "ui.setFocused"):
            native("native.call", {"function": function, "args": [2], "allow_write": True})
        focused = native("native.call", {"function": "ui.getFocused", "args": [2]})
        if not focused.get("value"):
            raise RuntimeError("Playlist focus was not verified; no project shortcut dispatched")
        await self._project_guard()

    async def _project_windows(self):
        result = await self.win("window_management", action="find", processName="FL64")
        if result.get("truncated") or result.get("partial"):
            raise RuntimeError("Incomplete FL window listing; no project action dispatched")
        return result.get("windows", [])

    async def _project_guard(self, expected_title=None, allow_dialog=None):
        windows = await self._project_windows()
        mains = [
            window
            for window in windows
            if str(window.get("handle")) == str(self.handle)
            and window.get("className") == "TFruityLoopsMainForm"
        ]
        if len(mains) != 1:
            raise RuntimeError("FL main window identity changed")
        main = mains[0]
        expected_title = self.project_title if expected_title is None else expected_title
        if main.get("title") != expected_title:
            raise RuntimeError("Project changed during file workflow; no further input sent")
        state = _window_state(self.handle)
        if not state["exists"] or not state["visible"]:
            raise RuntimeError("FL main window is unavailable")
        if not state["enabled"] and allow_dialog is None:
            raise RuntimeError("FL has an active modal dialog; resolve it before saving")
        for window in windows:
            if window.get("className") != "#32770":
                continue
            current = _window_state(window["handle"])
            if current["visible"] and current["pid"] == state["pid"]:
                if str(window["handle"]) != str(allow_dialog):
                    raise RuntimeError("Unexpected FL dialog; no further project input sent")
        return main, state

    async def _project_dialog(self):
        dialog = await self.dialog("Save As")
        main, state = await self._project_guard(allow_dialog=dialog["handle"])
        owned = _window_state(dialog["handle"])
        if (
            dialog.get("className") != "#32770"
            or not owned["exists"]
            or not owned["visible"]
            or not owned["enabled"]
            or owned["pid"] != state["pid"]
            or owned["root_owner"] != str(main["handle"])
        ):
            raise RuntimeError("Save As dialog is not the owned standard FL project dialog")
        return dialog

    async def _project_dialog_snapshot(self, dialog):
        snapshot = await self.win(
            "ui_snapshot", windowHandle=dialog["handle"], mode="auto", maxDepth=12
        )
        if not snapshot.get("tree"):
            snapshot = await self.win(
                "ui_snapshot", windowHandle=dialog["handle"], mode="full", maxDepth=12
            )
        if snapshot.get("truncated") or snapshot.get("partial"):
            raise RuntimeError("Incomplete Save As controls; project path is unverified")
        return list(_nodes(snapshot.get("tree", [])))

    async def _cancel_project_dialog(self, dialog):
        # Only touch the exact owned dialog this workflow opened. A new prompt or
        # project change stops cleanup instead of sending Escape into another UI.
        await self._project_guard(allow_dialog=dialog["handle"])
        state = _window_state(dialog["handle"])
        if not state["exists"] or state["root_owner"] != str(self.handle):
            raise RuntimeError("Inspected Save As dialog changed before cancellation")
        nodes = await self._project_dialog_snapshot(dialog)
        cancel = [
            node
            for node in nodes
            if node.get("type") == "Button"
            and node.get("name") == "Cancel"
            and node.get("enabled", True)
            and node.get("id")
        ]
        if len(cancel) != 1:
            raise RuntimeError("Cannot uniquely identify Save As Cancel button")
        click_error = None
        try:
            await self.win("ui_click", windowHandle=dialog["handle"], elementId=cancel[0]["id"])
        except Exception as exc:
            # UIA Invoke can close the dialog and then time out in provider cleanup.
            # Observe that exact HWND before classifying the outcome; never resend.
            click_error = exc
        deadline = time.monotonic() + self.project_dialog_close_timeout
        while time.monotonic() < deadline:
            if not _window_state(dialog["handle"])["exists"]:
                await self._project_guard()
                return
            await asyncio.sleep(self.project_poll_interval)
        raise RuntimeError(
            "Save As cancellation not verified; inspect FL before continuing"
        ) from click_error

    async def project_info(self):
        """Inspect the actual project path via Save As and cancel without saving.

        Unsaved projects return path=None and their observed suggested_path;
        matching a title alone never proves a project was saved at that location.
        """
        await self._project_guard()
        await self.win("window_management", action="activate", handle=self.handle)
        await self._project_focus()
        await self.key("s", modifiers="ctrl,shift")
        dialog = await self._project_dialog()
        try:
            deadline = time.monotonic() + self.project_info_settle_timeout
            previous = None
            last_error = RuntimeError("Save As filename and directory did not settle")
            while True:
                # FL can first expose the filename from its preceding recording
                # dialog. Observe two agreeing valid path frames; never reopen
                # the dialog or replay a shortcut to repair a stale first frame.
                _, main = await self._project_guard(allow_dialog=dialog["handle"])
                owned = _window_state(dialog["handle"])
                if (not owned["exists"] or not owned["visible"] or not owned["enabled"]
                        or owned["pid"] != main["pid"]
                        or owned["root_owner"] != str(self.handle)):
                    raise RuntimeError("Save As dialog identity changed during path inspection")
                nodes = await self._project_dialog_snapshot(dialog)
                await self._project_guard(allow_dialog=dialog["handle"])
                try:
                    current = _project_path_readback(nodes, self.project_title)
                except (ValueError, RuntimeError) as exc:
                    last_error = exc
                    previous = None
                else:
                    if current == previous:
                        path, named = current
                        break
                    previous = current
                    last_error = RuntimeError("Save As filename and directory did not settle")
                if time.monotonic() >= deadline:
                    raise last_error
                await asyncio.sleep(self.project_poll_interval)
            exists = path.is_file()
            return {
                "success": True,
                "project_title": self.project_title,
                "path": str(path) if named else None,
                "suggested_path": str(path),
                "saved": named and exists,
                "exists": exists if named else False,
                "path_source": "owned_save_as_dialog",
                "inspection_only": True,
            }
        finally:
            await self._cancel_project_dialog(dialog)

    async def _wait_project_file(self, path, before):
        deadline = time.monotonic() + self.project_save_timeout
        previous = None
        while time.monotonic() < deadline:
            try:
                current = _signature(path)
                if current is not None and current != before and current == previous:
                    evidence = _flp_evidence(path)
                    if (evidence["mtime_ns"], evidence["bytes"]) == current:
                        return evidence
                previous = current
            except (OSError, ValueError):
                previous = None
            await asyncio.sleep(self.project_poll_interval)
        raise RuntimeError("FLP save not verified; inspect destination and dialogs, do not retry")

    async def save_project(self, expected_path=None):
        """Discover and guard the active exact path, then save once with Ctrl+S."""
        expected = _path(expected_path) if expected_path is not None else None
        if expected is not None:
            # A supplied identity guard names an existing project. Reject typos
            # before opening an inspection dialog or changing FL focus.
            expected = expected.resolve(strict=True)
            if not expected.is_file():
                raise ValueError("Expected project path is not a regular file")
        info = await self.project_info()
        if info["path"] is None or not info["saved"]:
            raise ValueError("Active project has no verified saved file; use project Save As")
        path = _path(info["path"])
        if expected is not None and not _same_path(expected, path):
            raise ValueError("Expected project path differs from the actual active project path")
        before = _signature(path)
        await self._project_focus()
        await self.key("s", modifiers="ctrl")
        evidence = await self._wait_project_file(path, before)
        await self._project_guard()
        after = await self.project_info()
        if after["path"] is None or not _same_path(_path(after["path"]), path):
            raise RuntimeError("Active project path changed while saving; inspect FL")
        return {
            "success": True,
            "operation": "save",
            "path": str(path),
            **evidence,
            "verification": {"exact_active_path": True, "changed": True, "stable": True},
        }

    async def save_project_as(self, path, overwrite=False):
        """Save to an absolute FLP path; occupied paths require explicit overwrite.

        The Windows helper owns standard path-entry/readback and owned overwrite
        confirmation. This wrapper adds FL identity, original-file backup and
        independently verified final active path and project file contents.
        """
        if not isinstance(overwrite, bool):
            raise ValueError("overwrite must be a boolean")
        path = _path(path)
        if path.exists() and not path.is_file():
            raise ValueError("Project destination is not a regular file")
        if path.exists() and not overwrite:
            raise FileExistsError("Project destination exists; choose a new path or overwrite=True")
        before_info = await self.project_info()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Check again after UI inspection, before authorizing the helper to write.
        if path.exists() and not overwrite:
            raise FileExistsError("Project destination appeared during inspection; save rejected")
        backup = _backup(path) if path.exists() else None
        before = _signature(path)
        await self._project_focus()
        # The generic Windows saver cannot traverse FL26's first-save wrapper.
        # Keep its established overwrite handling for occupied destinations;
        # new destinations use the FL-specific owned-dialog flow.
        if _signature(path) != before:
            raise RuntimeError("Destination changed during Save As; no save dispatched")
        if before is not None:
            await self.win("file_save", windowHandle=self.handle, filePath=str(path))
        else:
            await self._save_project_dialog(path)
        evidence = await self._wait_project_file(path, before)
        windows = await self._project_windows()
        main = [w for w in windows if str(w.get("handle")) == str(self.handle)]
        if len(main) != 1 or not main[0].get("title", "").startswith(path.name + " - "):
            raise RuntimeError("Save As final FL project title does not match destination")
        self.project_title = main[0]["title"]
        await self._project_guard()
        after = await self.project_info()
        if after["path"] is None or not _same_path(_path(after["path"]), path):
            raise RuntimeError("Save As wrote a file but the final active path differs; inspect FL")
        return {
            "success": True,
            "operation": "save_as",
            "path": str(path),
            **evidence,
            "previous_path": before_info["path"],
            "backup_path": str(backup) if backup else None,
            "verification": {"exact_active_path": True, "changed": True, "stable": True},
        }
