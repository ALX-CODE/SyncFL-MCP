"""Project persistence regressions using a fake desktop and temporary FLP files."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fl_studio_mcp.tools.advanced import register_advanced_tools
from fl_studio_mcp.utils import project_windows as pw
from fl_studio_mcp.utils import windows_workflows as workflows


def test_exact_save_driver_waits_for_input_queue_before_confirming(monkeypatch, tmp_path):
    driver = pw.ProjectWindows()
    driver.handle = "1"
    driver.key = AsyncMock()
    driver.win = AsyncMock()
    driver._project_guard = AsyncMock()
    driver._project_dialog = AsyncMock(return_value={"handle": "2"})
    driver._project_dialog_snapshot = AsyncMock(return_value=[
        {"id": "filename", "name": "File name:", "type": "Edit"},
    ])
    target = tmp_path / "new project.flp"
    asyncio.run(driver._save_project_dialog(target))
    calls = driver.win.call_args_list
    assert calls[0].args == ("ui_batch",)
    assert json.loads(calls[0].kwargs["steps"]) == [dict(
        action="key", key="a", modifiers="ctrl", expectedElementId="filename")]
    assert calls[1].args == ("keyboard_control",)
    assert calls[1].kwargs == dict(windowHandle="2", action="type",
                                 text=str(target))
    steps = json.loads(calls[2].kwargs["steps"])
    assert steps[0]["action"] == "wait_text" and steps[0]["timeoutMs"] == 5000
    assert "stableForMs" not in steps[0]
    assert steps[0]["text"] == str(target) and steps[0]["match"] == "exact"
    assert steps[1] == dict(action="key", key="enter", expectedElementId="filename")


def test_save_driver_rejects_destination_that_appears_during_typing(tmp_path):
    driver = pw.ProjectWindows()
    driver.handle = "1"
    driver.key = AsyncMock()
    driver._project_guard = AsyncMock()
    driver._project_dialog = AsyncMock(return_value={"handle": "2"})
    driver._project_dialog_snapshot = AsyncMock(return_value=[
        {"id": "filename", "name": "File name:", "type": "Edit"},
    ])
    target = tmp_path / "appeared.flp"

    async def win(name, **kwargs):
        if name == "keyboard_control":
            target.write_bytes(b"user data")
    driver.win = AsyncMock(side_effect=win)
    with pytest.raises(FileExistsError, match="appeared"):
        asyncio.run(driver._save_project_dialog(target))
    assert driver.win.call_count == 2
    assert target.read_bytes() == b"user data"


@pytest.mark.parametrize("ambiguous", [False, True])
def test_first_save_skip_uses_unique_ocr_and_owned_dialog(monkeypatch, ambiguous):
    from fl_studio_mcp.utils import native_windows, screen_ocr

    driver = pw.ProjectWindows()
    driver.handle = "1"
    driver.win = AsyncMock()
    driver._project_guard = AsyncMock(return_value=({}, {"pid": 42}))
    dialog = dict(handle="2", className="TNewProjForm", title="Save as",
                  bounds=[100, 200, 485, 404])
    driver._project_windows = AsyncMock(return_value=[dialog])
    monkeypatch.setattr(pw, "_window_state", lambda h: dict(pid=42, root_owner="1",
                                                            visible=True, enabled=True))
    monkeypatch.setattr(native_windows, "point_belongs_to", lambda p, h: True)
    word = dict(text="Skip", bounds=[330, 550, 30, 15])
    monkeypatch.setattr(screen_ocr, "read_region", AsyncMock(return_value={
        "lines": [{"words": [word, word] if ambiguous else [word]}],
    }))
    if ambiguous:
        with pytest.raises(RuntimeError, match="uniquely"):
            asyncio.run(driver._skip_new_project_dialog(dialog))
        assert all(c.args[0] != "ui_batch" for c in driver.win.call_args_list)
    else:
        asyncio.run(driver._skip_new_project_dialog(dialog))
        steps = json.loads(driver.win.call_args_list[-1].kwargs["steps"])
        assert steps[0]["x"] == 245 and steps[0]["y"] == 358
        assert steps[1]["windowHandle"] == "1" and steps[1]["windowTitle"] == "Save As"


class FakeFL(pw.ProjectWindows):
    project_save_timeout = 0.03
    project_dialog_close_timeout = 0.03
    project_info_settle_timeout = 0.03
    project_poll_interval = 0

    def __init__(self, path, monkeypatch, *, saved=True):
        # The fake performs no real asynchronous I/O. Advance its deadline clock
        # per poll so scheduler load cannot consume a 30ms unit-test budget.
        self.elapsed = 0.0

        async def poll(seconds):
            self.elapsed += max(seconds, 0.001)

        monkeypatch.setattr(pw, "time", SimpleNamespace(monotonic=lambda: self.elapsed))
        monkeypatch.setattr(pw, "asyncio", SimpleNamespace(sleep=poll))
        self.path = path
        self.handle = "1"
        self.project_title = path.name + " - FL Studio 26" if saved else "FL Studio 26"
        self.actual_title = self.project_title
        self.open_dialog = False
        self.unexpected_modal = False
        self.dialog_owner = "1"
        self.dialog_pid = 42
        self.calls = []
        self.cancel_count = 0
        self.cancel_error = None
        self.cancel_closes = True
        self.change_on_activate = False
        self.write_files = True
        self.write_header = b"FLhd"
        self.wrong_final_path = None
        self.omit_address = False
        self.empty_auto_snapshot = False
        self.path_frames = []
        self.snapshot_count = 0
        self.before_file_save = None
        self.playlist_focused = True
        monkeypatch.setattr(pw, "_window_state", self.window_state)
        monkeypatch.setattr(workflows, "native", self.native)

    def native(self, action, params):
        self.calls.append(("native", action, params))
        assert action == "native.call"
        assert params["args"] == [2]
        if params["function"] == "ui.getFocused":
            return {"success": True, "value": self.playlist_focused}
        assert params["function"] in ("ui.showWindow", "ui.setFocused")
        assert params["allow_write"] is True
        return {"success": True}

    def window_state(self, handle):
        main = str(handle) == self.handle
        return {
            "exists": main or self.open_dialog or self.unexpected_modal,
            "enabled": not (main and (self.open_dialog or self.unexpected_modal)),
            "visible": main or self.open_dialog or self.unexpected_modal,
            "pid": 42 if main else self.dialog_pid,
            "root_owner": self.handle if main else self.dialog_owner,
        }

    def write(self, path):
        if not self.write_files:
            return
        before = pw._signature(path)
        path.write_bytes(self.write_header + b"new project payload")
        if before:
            os.utime(path, ns=(before[0] + 1_000_000, before[0] + 1_000_000))

    async def key(self, key, handle=None, modifiers=None):
        self.calls.append(("key", key, modifiers))
        assert key == "s"
        if modifiers == "ctrl,shift":
            self.open_dialog = True
        elif modifiers == "ctrl":
            self.write(self.path)
        else:
            pytest.fail("Unexpected shortcut")

    async def dialog(self, title):
        assert title == "Save As"
        assert self.open_dialog
        return {"handle": "2", "title": title, "className": "#32770"}

    async def _save_project_dialog(self, path):
        # File-workflow tests isolate the dialog driver, which has its own
        # real-method contract test below.
        await self.win("file_save", windowHandle=self.handle, filePath=str(path))

    async def win(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name == "window_management":
            if kwargs["action"] == "activate":
                if self.change_on_activate:
                    self.actual_title = "Different.flp - FL Studio 26"
                return {"window": {"title": self.actual_title}}
            windows = [
                {
                    "handle": "1",
                    "title": self.actual_title,
                    "className": "TFruityLoopsMainForm",
                    "pid": 42,
                }
            ]
            if self.open_dialog:
                windows.append(await self.dialog("Save As"))
            if self.unexpected_modal:
                windows.append({"handle": "3", "title": "Confirm", "className": "#32770"})
            return {"windows": windows}
        if name == "ui_snapshot":
            self.snapshot_count += 1
            if kwargs["mode"] == "auto" and self.empty_auto_snapshot:
                return {"changes": []}
            filename, directory = self.path.stem, str(self.path.parent)
            if self.path_frames:
                filename, directory = self.path_frames[0]
                if len(self.path_frames) > 1:
                    self.path_frames.pop(0)
            nodes = [
                {"id": "filename", "type": "Edit", "name": "File name:", "value": filename},
                {"id": "cancel", "type": "Button", "name": "Cancel", "enabled": True},
            ]
            if not self.omit_address and directory is not None:
                nodes.append(
                    {
                        "id": "address",
                        "type": "ToolBar",
                        "name": "Address: " + directory,
                    }
                )
            return {"tree": [{"id": "root", "type": "Window", "children": nodes}]}
        if name == "ui_click":
            assert kwargs == {"windowHandle": "2", "elementId": "cancel"}
            if self.cancel_closes:
                self.open_dialog = False
            self.cancel_count += 1
            if self.cancel_error:
                raise self.cancel_error
            return {"success": True}
        if name == "file_save":
            if self.before_file_save:
                self.before_file_save()
            target = Path(kwargs["filePath"])
            self.write(target)
            self.path = self.wrong_final_path or target
            self.actual_title = self.path.name + " - FL Studio 26"
            return {"success": True}
        pytest.fail("Unexpected Windows command " + name)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "original.flp"
    path.write_bytes(b"FLhdoriginal project payload")
    return path


def test_project_info_reads_filename_without_extension_and_cancels(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    before = source.read_bytes(), pw._signature(source)
    result = asyncio.run(fl.project_info())
    assert result["path"] == str(source)
    assert result["path_source"] == "owned_save_as_dialog"
    assert result["saved"] is True
    assert result["inspection_only"] is True
    assert fl.cancel_count == 1
    assert not fl.open_dialog
    assert (source.read_bytes(), pw._signature(source)) == before
    assert not any(call[0] == "file_save" for call in fl.calls)


def test_unsaved_info_reports_suggested_path_without_claiming_saved(source, monkeypatch):
    fl = FakeFL(source, monkeypatch, saved=False)
    result = asyncio.run(fl.project_info())
    assert result["path"] is None
    assert result["suggested_path"] == str(source)
    assert result["saved"] is False


def test_cancel_timeout_after_actual_dialog_close_is_verified_success(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.cancel_error = TimeoutError("UIA provider did not return after Invoke")
    result = asyncio.run(fl.project_info())
    assert result["path"] == str(source)
    assert fl.cancel_count == 1
    assert not fl.open_dialog


def test_cancel_timeout_with_dialog_still_open_fails_without_resending(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.cancel_error = TimeoutError("UIA provider did not return")
    fl.cancel_closes = False
    with pytest.raises(RuntimeError, match="cancellation not verified") as error:
        asyncio.run(fl.project_info())
    assert error.value.__cause__ is fl.cancel_error
    assert fl.cancel_count == 1
    assert fl.open_dialog


def test_incremental_snapshot_requires_full_readback(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.empty_auto_snapshot = True
    result = asyncio.run(fl.project_info())
    assert result["path"] == str(source)
    assert any(call[0] == "ui_snapshot" and call[1]["mode"] == "full" for call in fl.calls)


def test_missing_directory_fails_and_cancels_inspection(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.omit_address = True
    with pytest.raises(RuntimeError, match="one exact filename and directory"):
        asyncio.run(fl.project_info())
    assert fl.cancel_count == 1
    assert not fl.open_dialog


def test_stale_recording_filename_settles_without_replaying_project_input(source, monkeypatch):
    # Native FL26 evidence, 2026-09-29: after recording, the first Save As frame
    # retained the recording name; reopening showed the active FLP's name/path.
    # Waiting on that same owned dialog must resolve the stale initial frame.
    fl = FakeFL(source, monkeypatch)
    fl.path_frames = [
        ("created_2026-09-29_02-00-00_Insert 1.wav", str(source.parent)),
        (source.stem, str(source.parent)),
        (source.stem, str(source.parent)),
    ]
    result = asyncio.run(fl.project_info())
    assert result["path"] == str(source) and result["saved"] is True
    assert fl.snapshot_count == 4  # Three readbacks, then the Cancel inspection.
    assert fl.calls.count(("key", "s", "ctrl,shift")) == 1
    assert fl.cancel_count == 1 and not fl.open_dialog
    assert not any(call[0] == "file_save" for call in fl.calls)


def test_directory_and_filename_must_agree_in_consecutive_complete_frames(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.path_frames = [
        (source.stem, str(source.parent / "recordings")),
        (source.stem, str(source.parent)),
        (source.stem, str(source.parent)),
    ]
    result = asyncio.run(fl.project_info())
    assert result["path"] == str(source)
    assert fl.snapshot_count == 4
    assert fl.cancel_count == 1


def test_transient_missing_address_resets_path_stability(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.path_frames = [
        (source.stem, str(source.parent)),
        (source.stem, None),
        (source.stem, str(source.parent)),
        (source.stem, str(source.parent)),
    ]
    assert asyncio.run(fl.project_info())["path"] == str(source)
    assert fl.snapshot_count == 5
    assert fl.cancel_count == 1


def test_missing_address_cannot_bridge_stability_before_deadline(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.project_info_settle_timeout = 0.002
    fl.path_frames = [
        (source.stem, str(source.parent)),
        (source.stem, None),
        (source.stem, str(source.parent)),
        (source.stem, str(source.parent)),
    ]
    with pytest.raises(RuntimeError, match="did not settle"):
        asyncio.run(fl.project_info())
    assert fl.elapsed == pytest.approx(fl.project_info_settle_timeout)
    assert fl.snapshot_count == 4  # Three observations plus the Cancel inspection.
    assert fl.cancel_count == 1 and not fl.open_dialog
    assert fl.calls.count(("key", "s", "ctrl,shift")) == 1
    assert not any(call[0] == "file_save" for call in fl.calls)


@pytest.mark.parametrize("filename,directory,error", [
    ("wrong-name", "absolute", "does not match"),
    (None, "absolute", "one exact filename"),
    ("../original.flp", "absolute", "Unexpected Save As filename"),
    ("original", "relative-directory", "absolute .flp project path"),
])
def test_unresolved_path_readbacks_fail_and_cancel_once(
    source, monkeypatch, filename, directory, error
):
    fl = FakeFL(source, monkeypatch)
    fl.path_frames = [(filename, str(source.parent) if directory == "absolute" else directory)]
    with pytest.raises((ValueError, RuntimeError), match=error):
        asyncio.run(fl.project_info())
    assert fl.snapshot_count > 2
    assert fl.cancel_count == 1 and not fl.open_dialog
    assert fl.calls.count(("key", "s", "ctrl,shift")) == 1
    assert not any(call[0] == "file_save" for call in fl.calls)


def test_project_identity_change_during_readback_stops_without_more_input(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    read = fl._project_dialog_snapshot

    async def changed_snapshot(dialog):
        nodes = await read(dialog)
        fl.actual_title = "different.flp - FL Studio 26"
        return nodes

    monkeypatch.setattr(fl, "_project_dialog_snapshot", changed_snapshot)
    with pytest.raises(RuntimeError, match="Project changed"):
        asyncio.run(fl.project_info())
    assert fl.snapshot_count == 1
    assert fl.cancel_count == 0  # Cleanup must not click after identity changed.
    assert fl.calls.count(("key", "s", "ctrl,shift")) == 1


def test_existing_modal_blocks_any_keyboard_input(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.unexpected_modal = True
    with pytest.raises(RuntimeError, match="active modal"):
        asyncio.run(fl.project_info())
    assert not any(call[0] == "key" for call in fl.calls)


@pytest.mark.parametrize("field,value", [("dialog_owner", "99"), ("dialog_pid", 99)])
def test_dialog_ownership_and_process_are_verified(source, monkeypatch, field, value):
    fl = FakeFL(source, monkeypatch)
    setattr(fl, field, value)
    with pytest.raises(RuntimeError, match="owned standard"):
        asyncio.run(fl.project_info())
    assert not any(call[0] == "ui_click" for call in fl.calls)


def test_project_switch_before_shortcut_fails_closed(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.change_on_activate = True
    with pytest.raises(RuntimeError, match="Project changed"):
        asyncio.run(fl.project_info())
    assert not any(call[0] == "key" for call in fl.calls)


def test_project_shortcuts_require_verified_playlist_context(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    fl.playlist_focused = False
    with pytest.raises(RuntimeError, match="Playlist focus was not verified"):
        asyncio.run(fl.project_info())
    assert not any(call[0] == "key" for call in fl.calls)


def test_each_project_save_shortcut_reestablishes_playlist_focus(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    asyncio.run(fl.save_project())
    asyncio.run(fl.save_project_as(source.parent / "copy.flp"))
    last_native = None
    checked = 0
    for call in fl.calls:
        if call[0] == "native":
            last_native = call[2]
        if call[0] in ("key", "file_save"):
            assert last_native == {"function": "ui.getFocused", "args": [2]}
            last_native = None
            checked += 1
    assert checked == 6  # Two inspections plus a save in each operation.


def test_same_basename_at_another_location_cannot_authorize_save(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    other = source.parent / "elsewhere" / source.name
    other.parent.mkdir()
    other.write_bytes(b"FLhdother project")
    with pytest.raises(ValueError, match="actual active project path"):
        asyncio.run(fl.save_project(other))
    assert ("key", "s", "ctrl") not in fl.calls


def test_save_discovers_active_path_and_verifies_saved_file(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    result = asyncio.run(fl.save_project())
    assert result["path"] == str(source)
    assert result["header"] == "FLhd"
    assert len(result["sha256"]) == 64
    assert result["verification"] == {"exact_active_path": True, "changed": True, "stable": True}
    assert fl.calls.count(("key", "s", "ctrl")) == 1
    assert fl.cancel_count == 2


def test_unnamed_project_requires_save_as(source, monkeypatch):
    fl = FakeFL(source, monkeypatch, saved=False)
    with pytest.raises(ValueError, match="Save As"):
        asyncio.run(fl.save_project())
    assert ("key", "s", "ctrl") not in fl.calls


def test_named_but_never_saved_project_requires_save_as(source, monkeypatch):
    missing = source.parent / "Untitled.flp"
    fl = FakeFL(missing, monkeypatch)
    info = asyncio.run(fl.project_info())
    assert info["saved"] is False
    with pytest.raises(ValueError, match="no verified saved file"):
        asyncio.run(fl.save_project())
    assert ("key", "s", "ctrl") not in fl.calls


@pytest.mark.parametrize("write_files,header", [(False, b"FLhd"), (True, b"wrong")])
def test_save_requires_changed_valid_file_and_never_retries(
    source, monkeypatch, write_files, header
):
    fl = FakeFL(source, monkeypatch)
    fl.write_files = write_files
    fl.write_header = header
    with pytest.raises(RuntimeError, match="do not retry"):
        asyncio.run(fl.save_project())
    assert fl.calls.count(("key", "s", "ctrl")) == 1


def test_save_as_creates_parent_and_leaves_source_unchanged(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    destination = source.parent / "new project folder" / "Saved copy.flp"
    before = source.read_bytes(), pw._signature(source)
    result = asyncio.run(fl.save_project_as(destination))
    assert result["path"] == str(destination)
    assert result["previous_path"] == str(source)
    assert result["backup_path"] is None
    assert result["verification"]["exact_active_path"] is True
    assert (source.read_bytes(), pw._signature(source)) == before
    assert len([call for call in fl.calls if call[0] == "file_save"]) == 1


def test_save_as_existing_destination_requires_overwrite_before_gui(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    with pytest.raises(FileExistsError, match="overwrite=True"):
        asyncio.run(fl.save_project_as(source))
    assert fl.calls == []


def test_save_as_rechecks_destination_after_dialog_inspection(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    destination = source.parent / "new.flp"
    inspect = fl.project_info

    async def inspect_then_create():
        result = await inspect()
        destination.write_bytes(b"FLhdcreated concurrently")
        return result

    monkeypatch.setattr(fl, "project_info", inspect_then_create)
    with pytest.raises(FileExistsError, match="appeared during inspection"):
        asyncio.run(fl.save_project_as(destination))
    assert destination.read_bytes() == b"FLhdcreated concurrently"
    assert not any(call[0] == "file_save" for call in fl.calls)


def test_save_as_overwrite_keeps_backup_of_exact_original(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    before = source.read_bytes()
    result = asyncio.run(fl.save_project_as(source, overwrite=True))
    assert Path(result["backup_path"]).read_bytes() == before
    assert source.read_bytes() != before


def test_save_as_requires_final_exact_active_path(source, monkeypatch):
    fl = FakeFL(source, monkeypatch)
    destination = source.parent / "new.flp"
    fl.wrong_final_path = source.parent / "other" / destination.name
    with pytest.raises(RuntimeError, match="final active path differs"):
        asyncio.run(fl.save_project_as(destination))
    assert destination.exists()
    assert len([call for call in fl.calls if call[0] == "file_save"]) == 1


@pytest.mark.parametrize("path", ["relative.flp", "C:/a.txt", "bad\nname.flp"])
def test_invalid_paths_fail_before_gui(source, monkeypatch, path):
    fl = FakeFL(source, monkeypatch)
    with pytest.raises(ValueError, match="path"):
        asyncio.run(fl.save_project_as(path))
    assert fl.calls == []


def test_windows_workflow_inherits_verified_project_methods():
    assert workflows.WindowsFL.project_info is pw.ProjectWindows.project_info
    assert workflows.WindowsFL.save_project is pw.ProjectWindows.save_project
    assert workflows.WindowsFL.save_project_as is pw.ProjectWindows.save_project_as


def test_mcp_project_tools_dispatch_to_serialized_workflow(monkeypatch):
    class Registry:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    registry = Registry()
    register_advanced_tools(registry)
    calls = []

    async def run_workflow(operation, **kwargs):
        calls.append((operation, kwargs))
        return {"success": True, "operation": operation}

    monkeypatch.setattr(workflows, "run_workflow", run_workflow)
    result = asyncio.run(registry.tools["fl_project_info"]())
    assert result["operation"] == "project_info"
    asyncio.run(registry.tools["fl_project_save"]())
    asyncio.run(registry.tools["fl_project_save"](expected_path="C:/Project/song.flp"))
    asyncio.run(registry.tools["fl_project_save_as"]("C:/Project/copy.flp"))
    asyncio.run(registry.tools["fl_project_save_as"]("C:/Project/copy.flp", overwrite=True))
    assert calls == [
        ("project_info", {}),
        ("save_project", {"expected_path": None}),
        ("save_project", {"expected_path": "C:/Project/song.flp"}),
        ("save_project_as", {"path": "C:/Project/copy.flp", "overwrite": False}),
        ("save_project_as", {"path": "C:/Project/copy.flp", "overwrite": True}),
    ]
