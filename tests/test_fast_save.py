"""Checkpoint-save regression checks after exact-path dialog inspection.

ProjectWindows' real dialog/ownership/focus integration is covered separately in
test_project_windows. Here the inspection result is mocked so file persistence
and save dispatch can be tested without desktop input or calibrated geometry.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fl_studio_mcp.utils.windows_workflows import WindowsFL


class FastSaveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "Lead.flp"
        self.path.write_bytes(b"FLhdoriginal")
        self.fl = object.__new__(WindowsFL)
        self.fl.handle = "123"
        self.fl.project_title = "Lead.flp - FL Studio"
        self.fl.project_save_timeout = 0.03
        self.fl.project_poll_interval = 0
        self.fl.win = AsyncMock(
            return_value={
                "windows": [
                    {
                        "handle": "123",
                        "className": "TFruityLoopsMainForm",
                        "title": self.fl.project_title,
                    }
                ]
            }
        )
        self.fl.project_info = AsyncMock(
            return_value={
                "success": True,
                "path": str(self.path),
                "saved": True,
                "path_source": "owned_save_as_dialog",
                "inspection_only": True,
            }
        )
        self.fl.click = AsyncMock(side_effect=AssertionError("No calibrated menu clicks allowed"))
        self.fl.prepare_mixer = AsyncMock(side_effect=AssertionError("No mixer geometry required"))
        self.fl.key = AsyncMock(side_effect=self.write_save)
        self.native = patch(
            "fl_studio_mcp.utils.windows_workflows.native",
            return_value={"success": True, "value": True},
        ).start()
        self.addCleanup(patch.stopall)
        patch(
            "fl_studio_mcp.utils.project_windows._window_state",
            return_value={
                "exists": True,
                "visible": True,
                "enabled": True,
                "root_owner": "123",
                "pid": 7,
            },
        ).start()

    async def write_save(self, *args, **kwargs):
        previous = self.path.stat().st_mtime_ns
        self.path.write_bytes(b"FLhdnew contents")
        os.utime(self.path, ns=(previous + 1_000_000, previous + 1_000_000))

    def test_shortcut_saves_without_calibrated_geometry(self):
        result = asyncio.run(self.fl.save_project(str(self.path)))
        self.assertTrue(result["success"])
        self.assertEqual(result["bytes"], len(b"FLhdnew contents"))
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(self.fl.project_info.await_count, 2)
        self.fl.key.assert_awaited_once_with("s", modifiers="ctrl")
        self.fl.click.assert_not_called()
        self.fl.prepare_mixer.assert_not_called()
        self.native.assert_any_call("native.call", {"function": "ui.getFocused", "args": [2]})

    def test_rejects_same_basename_at_another_path_without_save_input(self):
        other = self.path.parent / "Another folder" / self.path.name
        other.parent.mkdir()
        other.write_bytes(b"FLhdanother project")
        self.fl.project_info.return_value["path"] = str(other)
        with self.assertRaisesRegex(ValueError, "actual active project path"):
            asyncio.run(self.fl.save_project(str(self.path)))
        self.fl.project_info.assert_awaited_once()
        self.fl.key.assert_not_called()
        self.fl.win.assert_not_called()

    def test_rejects_project_change_after_path_inspection(self):
        self.fl.win.return_value["windows"][0]["title"] = "Other.flp - FL Studio"
        with self.assertRaisesRegex(RuntimeError, "Project changed"):
            asyncio.run(self.fl.save_project(str(self.path)))
        self.fl.key.assert_not_called()
        self.native.assert_not_called()

    def test_missing_expected_destination_does_not_open_save_as(self):
        with self.assertRaises(FileNotFoundError):
            asyncio.run(self.fl.save_project(str(self.path.with_name("Missing.flp"))))
        self.fl.project_info.assert_not_called()
        self.fl.key.assert_not_called()
        self.fl.win.assert_not_called()

    def test_changed_invalid_header_is_not_success(self):
        async def invalid_save(*args, **kwargs):
            await self.write_save()
            self.path.write_bytes(b"invalid")

        self.fl.key.side_effect = invalid_save
        with self.assertRaisesRegex(RuntimeError, "save not verified"):
            asyncio.run(self.fl.save_project(str(self.path)))
        self.fl.key.assert_awaited_once_with("s", modifiers="ctrl")
        self.assertEqual(self.fl.project_info.await_count, 1)


if __name__ == "__main__":
    unittest.main()
