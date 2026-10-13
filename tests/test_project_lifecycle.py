import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest
from test_project_bundle import project

from fl_studio_mcp.utils import project_lifecycle as life
from fl_studio_mcp.utils.project_windows import _flp_evidence


@pytest.fixture
def rig(tmp_path, monkeypatch):
    source = tmp_path / "source.flp"
    source.write_bytes(project())
    state = dict(
        context=dict(session="s", project_generation=1),
        revision="fresh",
        values=dict(tempo_bpm=125, ppq=96),
        dirty=1,
        is_playing=False,
        is_recording=False,
    )
    monkeypatch.setattr(life, "_state", lambda: copy.deepcopy(state))
    monkeypatch.setattr(life, "_window_state", lambda _: dict(
        exists=True, visible=True, enabled=True, pid=42, root_owner="1",
    ))

    class FL:
        handle = "1"
        project_title = "old.flp - FL Studio 2026"
        _project_guard = AsyncMock(return_value=(dict(handle="1"), dict(pid=42)))
        _project_focus = AsyncMock()
        saves = 0
        opens = 0
        fail_open = False
        fail_save = False
        ignored_open = False
        hang_confirmation = False
        fail_typing = False
        change_source = False
        shortcut_count = 0
        confirmation_count = 0
        controls = [dict(type="Edit", name="File name:", id="filename")]
        open_dialog = dict(handle="2", className="#32770", title="Open")
        path = None
        typed_path = None

        async def save_project_as(self, path):
            self.saves += 1
            if self.fail_save:
                raise RuntimeError("save failed")
            path.write_bytes(project())
            state["dirty"] = 0
            return dict(success=True, path=str(path), **_flp_evidence(path))

        async def key(self, key, modifiers=None):
            assert key == "o" and modifiers == "ctrl"
            self.shortcut_count += 1

        async def dialog(self, title):
            assert title == "Open"
            return self.open_dialog

        async def _project_dialog_snapshot(self, dialog):
            assert dialog == self.open_dialog
            return self.controls

        async def win(self, name, **kwargs):
            assert name != "file_open"
            assert not state["dirty"]
            if name == "keyboard_control":
                assert kwargs["action"] == "type"
                if self.fail_typing:
                    raise TimeoutError("typing uncertain")
                self.typed_path = kwargs["text"]
                if self.change_source:
                    source.write_bytes(project(version="26.1.9"))
                return dict(success=True)
            assert name == "ui_batch"
            steps = json.loads(kwargs["steps"])
            assert steps[-1]["expectedElementId"] == "filename"
            if steps[-1]["key"] == "a":
                assert len(steps) == 1 and steps[0]["modifiers"] == "ctrl"
                return dict(success=True)
            assert steps == [
                dict(action="wait_text", elementId="filename", text=str(source),
                     match="exact", timeoutMs=5000),
                dict(action="key", key="enter", expectedElementId="filename"),
            ]
            assert self.typed_path == str(source)
            self.confirmation_count += 1
            if not self.ignored_open:
                self.opens += 1
                self.path = self.typed_path
                state["context"]["project_generation"] += 1
            if self.hang_confirmation:
                await asyncio.Event().wait()
            if self.fail_open:
                raise TimeoutError("open may have completed")
            return dict(success=True)

        async def _project_windows(self):
            title = source.name + " - FL Studio 2026" if self.path else self.project_title
            return [dict(handle="1", title=title)]

        async def project_info(self):
            return dict(path=self.path)

    fl = FL()
    args = dict(
        source=str(source),
        expected_sha256=_flp_evidence(source)["sha256"],
        expected_revision="fresh",
        recovery_path=str(tmp_path / "recovery.flp"),
        receipt_file=str(tmp_path / "receipt.json"),
    )
    return fl, args, state


def test_dirty_project_is_checkpointed_before_open_and_independently_verified(rig):
    fl, args, state = rig
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert result["success"] and result["status"] == "verified"
    assert fl.opens == fl.saves == 1
    assert result["verification"]["new_project_generation"]


def test_preflight_does_not_save_or_open(rig):
    fl, args, _ = rig
    assert asyncio.run(life.open_project(fl, **args))["success"]
    assert fl.opens == fl.saves == 0


@pytest.mark.parametrize("kind", ["stale", "hash", "playing", "occupied"])
def test_open_preflight_rejects_before_saving(rig, kind):
    from pathlib import Path

    fl, args, state = rig
    if kind == "stale":
        args["expected_revision"] = "old"
    elif kind == "hash":
        args["expected_sha256"] = "0" * 64
    elif kind == "playing":
        state["is_playing"] = True
    else:
        Path(args["recovery_path"]).write_bytes(b"user")
    with pytest.raises(ValueError):
        asyncio.run(life.open_project(fl, **args, apply=True))
    assert fl.opens == fl.saves == 0


def test_completed_open_with_lost_ack_is_independently_verified_without_replay(rig):
    from pathlib import Path

    fl, args, state = rig
    fl.fail_open = True
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert result["success"] and result["status"] == "verified"
    assert result["verification"]["delivery_error_reconciled"]
    assert fl.opens == 1 and Path(args["recovery_path"]).exists()
    assert fl.confirmation_count == fl.shortcut_count == 1
    saved = json.loads(Path(args["receipt_file"]).read_text())
    assert saved["open_delivery"]["error"] == "open may have completed"


def test_failed_save_never_dispatches_open(rig):
    fl, args, _ = rig
    fl.fail_save = True
    assert not asyncio.run(life.open_project(fl, **args, apply=True))["success"]
    assert fl.opens == 0


def test_title_before_controller_ready_only_retries_observations(rig, monkeypatch):
    fl, args, state = rig
    busy = 0

    def read():
        nonlocal busy
        if fl.opens and busy < 2:
            busy += 1
            raise RuntimeError("FL is unsafe to edit while loading")
        return copy.deepcopy(state)

    monkeypatch.setattr(life, "_state", read)
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert result["success"] and busy == 2
    assert fl.opens == fl.saves == 1


def test_completed_open_with_hung_ack_is_bounded_and_verified_once(rig, monkeypatch):
    fl, args, _ = rig
    fl.hang_confirmation = True
    monkeypatch.setattr(life, "OPEN_DIALOG_TIMEOUT", 0.02)
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert result["success"] and result["verification"]["delivery_error_reconciled"]
    assert fl.confirmation_count == fl.shortcut_count == fl.opens == 1


def test_failed_uncompleted_open_is_never_replayed(rig, monkeypatch):
    fl, args, _ = rig
    fl.ignored_open = fl.fail_open = True
    monkeypatch.setattr(life, "OPEN_VERIFY_TIMEOUT", 0.01)
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert not result["success"] and result["status"] == "needs_inspection"
    assert fl.confirmation_count == fl.shortcut_count == 1 and fl.opens == 0
    assert result["open_delivery"]["error"] == "open may have completed"


@pytest.mark.parametrize("field,value", [
    ("className", "CustomDialog"), ("pid", 99), ("root_owner", "999"),
    ("visible", False), ("enabled", False), ("exists", False),
])
def test_unowned_or_unavailable_open_dialog_never_types_or_confirms(rig, monkeypatch, field, value):
    fl, args, _ = rig
    if field == "className":
        fl.open_dialog = dict(fl.open_dialog, className=value)
    else:
        state = dict(exists=True, visible=True, enabled=True, pid=42, root_owner="1")
        state[field] = value
        monkeypatch.setattr(life, "_window_state", lambda _: state)
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert not result["success"] and fl.confirmation_count == fl.opens == 0
    assert fl.typed_path is None and fl.shortcut_count == 1


def test_ambiguous_filename_field_does_not_type_or_confirm(rig):
    fl, args, _ = rig
    fl.controls = fl.controls * 2
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert not result["success"] and fl.typed_path is None and fl.confirmation_count == 0


def test_typing_error_is_not_retried_or_confirmed(rig):
    fl, args, _ = rig
    fl.fail_typing = True
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert not result["success"] and fl.confirmation_count == 0
    assert result["open_delivery"]["confirmation_dispatched"] is False


def test_source_change_before_confirmation_is_rejected(rig):
    fl, args, _ = rig
    fl.change_source = True
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert not result["success"] and fl.confirmation_count == 0
    assert "Source changed before Open confirmation" in result["error"]


@pytest.mark.parametrize("bad", ["path", "tempo", "ppq", "generation", "session", "source"])
def test_delivery_error_requires_every_original_postcondition(rig, monkeypatch, bad):
    fl, args, state = rig
    fl.fail_open = True
    original = fl.win

    async def win(*a, **kw):
        try:
            return await original(*a, **kw)
        except TimeoutError:
            if bad == "path":
                fl.path = args["recovery_path"]
            elif bad == "tempo":
                state["values"]["tempo_bpm"] = 130
            elif bad == "ppq":
                state["values"]["ppq"] = 192
            elif bad == "generation":
                state["context"]["project_generation"] = 1
            elif bad == "session":
                state["context"]["session"] = "replacement"
            else:
                from pathlib import Path

                Path(args["source"]).write_bytes(project(version="26.1.8"))
            raise

    fl.win = win
    monkeypatch.setattr(life, "OPEN_VERIFY_TIMEOUT", 0.01)
    result = asyncio.run(life.open_project(fl, **args, apply=True))
    assert not result["success"] and result["status"] == "needs_inspection"
    assert fl.confirmation_count == fl.shortcut_count == 1
