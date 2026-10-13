"""OLE transfer orchestration tests: no desktop input or helper process launches."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from fl_studio_mcp.utils import file_transfer as transfer


class Process:
    pid = 404

    def __init__(self):
        self.returncode = None
        self.output = "FORM 100\nEFFECT Copy\n"
        self.terminated = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15

    def communicate(self, timeout):
        return self.output, ""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    path = tmp_path / "Kick with spaces.wav"
    path.write_bytes(b"wave fixture")
    executable = tmp_path / "FLMCPFileTransfer_abc.exe"
    executable.touch()
    process = Process()
    launch = Mock(return_value=process)
    compiler = Mock(return_value=executable)
    monkeypatch.setattr(transfer, "_helper_executable", compiler)
    monkeypatch.setattr(transfer.subprocess, "Popen", launch)
    monkeypatch.setattr(transfer.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(transfer, "_monitor_for", Mock(return_value={
        "bounds": (0, 0, 1920, 1080), "work": (0, 0, 1920, 1040)}))
    ownership = Mock(return_value=True)
    monkeypatch.setattr(transfer.native_windows, "point_belongs_to", ownership)
    controls = Mock(return_value=(
        {"handle": "100", "bounds": (22, 550, 270, 100)},
        {"handle": "101", "bounds": (26, 575, 262, 70)},
    ))
    monkeypatch.setattr(transfer, "_helper_controls", controls)

    async def win(name, **kwargs):
        if name == "mouse_control":
            process.returncode = 0
        return {"success": True}

    fl = SimpleNamespace(win=AsyncMock(side_effect=win))
    return SimpleNamespace(path=path, executable=executable, process=process, compiler=compiler,
                           launch=launch, ownership=ownership, controls=controls, fl=fl)


def run(setup):
    return asyncio.run(transfer.transfer_file(setup.fl, setup.path, (1175, 195), "200"))


@pytest.mark.parametrize("mode", [None, "background", "invalid"])
def test_noninteractive_mode_blocks_before_helper_or_desktop_observation(setup, monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    else:
        monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", mode)
    with pytest.raises((RuntimeError, ValueError), match="background mode|must be"):
        run(setup)
    setup.compiler.assert_not_called()
    setup.launch.assert_not_called()
    transfer._monitor_for.assert_not_called()
    setup.ownership.assert_not_called()
    setup.fl.win.assert_not_called()


def test_transfer_is_one_guarded_drag_and_requires_caller_readback(setup):
    result = run(setup)
    assert result["dispatched"] and result["delivery_confirmed"]
    assert result["verified"] is False
    assert result["effect"] == "Copy"
    calls = setup.fl.win.await_args_list
    assert len(calls) == 2
    assert calls[0].args == ("window_management",)
    assert calls[0].kwargs == {"action": "activate", "handle": "100"}
    drag = calls[1].kwargs
    assert calls[1].args == ("mouse_control",)
    assert drag["action"] == "drag" and drag["target"] == "primary_screen"
    assert (drag["x"], drag["y"], drag["endX"], drag["endY"]) == (157, 610, 1175, 195)
    assert drag["expectedProcessName"] == setup.executable.stem
    assert drag["expectedWindowTitle"].startswith("FL MCP file transfer ")
    launch_args, launch_kwargs = setup.launch.call_args
    assert launch_args[0][1] == str(setup.path)
    assert launch_args[0][2] == drag["expectedWindowTitle"]
    assert launch_kwargs["creationflags"] == transfer.subprocess.CREATE_NO_WINDOW
    assert setup.process.terminated == 0


def test_paths_are_arguments_never_interpolated_source(setup):
    new_path = setup.path.with_name("kick ' quote $().wav")
    setup.path.rename(new_path)
    setup.path = new_path
    run(setup)
    assert setup.launch.call_args.args[0][1] == str(new_path)


@pytest.mark.parametrize("point", [(1.2, 2), (True, 2), [1], "1,2"])
def test_invalid_coordinates_never_launch_or_touch_ui(setup, point):
    with pytest.raises(ValueError, match="integer physical"):
        asyncio.run(transfer.transfer_file(setup.fl, setup.path, point, 200))
    setup.launch.assert_not_called()
    setup.fl.win.assert_not_called()


def test_missing_file_never_launches(setup):
    setup.path.unlink()
    with pytest.raises(FileNotFoundError):
        run(setup)
    setup.launch.assert_not_called()


def test_relative_path_never_launches(setup):
    setup.path = Path("relative.wav")
    with pytest.raises(ValueError, match="absolute"):
        run(setup)
    setup.launch.assert_not_called()


def test_unsupported_monitor_stops_before_compile_or_launch(setup, monkeypatch):
    monkeypatch.setattr(transfer, "_monitor_for", Mock(side_effect=ValueError("primary monitor")))
    with pytest.raises(ValueError, match="primary monitor"):
        run(setup)
    setup.compiler.assert_not_called()
    setup.launch.assert_not_called()


def test_wrong_initial_target_stops_before_compile(setup):
    setup.ownership.return_value = False
    with pytest.raises(RuntimeError, match="expected FL window"):
        run(setup)
    setup.compiler.assert_not_called()
    setup.launch.assert_not_called()


def test_changed_target_during_activation_never_drags_and_reaps_only_helper(setup):
    setup.ownership.side_effect = [True, True, False]
    with pytest.raises(RuntimeError, match="destination changed"):
        run(setup)
    assert setup.fl.win.await_count == 1
    assert setup.process.terminated == 1


def test_obscured_source_never_drags(setup):
    setup.ownership.side_effect = [True, False]
    with pytest.raises(RuntimeError, match="moved or obscures"):
        run(setup)
    assert setup.fl.win.await_count == 1 and setup.process.terminated == 1


def test_helper_overlapping_target_never_drags(setup):
    setup.controls.return_value[0]["bounds"] = (1100, 160, 270, 100)
    with pytest.raises(RuntimeError, match="moved or obscures"):
        run(setup)
    assert setup.fl.win.await_count == 1 and setup.process.terminated == 1


def test_source_file_change_never_drags(setup):
    async def change_file(*args, **kwargs):
        setup.path.write_bytes(b"changed wave fixture")

    setup.fl.win.side_effect = change_file
    with pytest.raises(RuntimeError, match="File changed"):
        run(setup)
    assert setup.fl.win.await_count == 1 and setup.process.terminated == 1


def test_failed_drag_is_unknown_and_never_retried(setup):
    async def fail_drag(name, **kwargs):
        if name == "mouse_control":
            raise RuntimeError("Lost tool response")

    setup.fl.win.side_effect = fail_drag
    with pytest.raises(RuntimeError, match="outcome unknown.*Lost tool response"):
        run(setup)
    assert setup.fl.win.await_count == 2 and setup.process.terminated == 1


def test_missing_ole_receipt_is_unknown_not_success(setup):
    setup.process.output = "FORM 100\n"
    with pytest.raises(RuntimeError, match="outcome unknown.*no unique OLE receipt"):
        run(setup)
    assert setup.fl.win.await_count == 2


def test_none_effect_is_returned_for_native_verification_without_replay(setup):
    setup.process.output = "FORM 100\nEFFECT None\n"
    result = run(setup)
    assert result["dispatched"] and not result["delivery_confirmed"]
    assert not result["verified"] and result["effect"] == "None"
    assert setup.fl.win.await_count == 2


def test_primary_monitor_coordinates_share_exact_origin(setup, monkeypatch):
    monkeypatch.setattr(transfer, "_monitor_for", Mock(return_value={
        "bounds": (10, 20, 1920, 1080), "work": (10, 20, 1920, 1040)}))
    run(setup)
    drag = setup.fl.win.await_args_list[1].kwargs
    assert (drag["x"], drag["y"], drag["endX"], drag["endY"]) == (147, 590, 1165, 175)


def test_helper_placement_avoids_target_and_stays_in_work_area():
    work = (0, 40, 1920, 1000)
    target = (90, 620)
    position = transfer._helper_position(work, target)
    rectangle = (*position, *transfer.HELPER_SIZE)
    assert not transfer._contains(rectangle, target, margin=24)
    assert transfer._contains(work, position)
    assert transfer._contains(work, (position[0] + 269, position[1] + 99))


def test_tiny_monitor_is_rejected():
    with pytest.raises(RuntimeError, match="safe visible area"):
        transfer._helper_position((0, 0, 280, 150), (100, 100))


def test_helper_identity_is_scoped_by_pid_and_exact_title(monkeypatch):
    lookup = Mock(side_effect=[[
        {"handle": "100", "title": "wanted"}, {"handle": "900", "title": "other"}],
        [{"handle": "101", "class": "WindowsForms10.STATIC.any", "enabled": True}],
    ])
    monkeypatch.setattr(transfer.native_windows, "windows", lookup)
    controls = transfer._helper_controls(SimpleNamespace(pid=404), "wanted")
    assert controls[0]["handle"] == "100" and controls[1]["handle"] == "101"
    assert lookup.call_args_list[0].kwargs == {"pid": 404}
    assert lookup.call_args_list[1].args == ("100",)
    assert lookup.call_args_list[1].kwargs == {"pid": 404}
