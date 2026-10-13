"""Backend-only execution cannot enable desktop input or launch an input helper."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fl_studio_mcp.utils import fl_trigger
from fl_studio_mcp.utils import interaction_policy as policy
from fl_studio_mcp.utils import windows_workflows as workflows


@pytest.fixture(autouse=True)
def background_default(monkeypatch):
    monkeypatch.delenv(policy.ENVIRONMENT_VARIABLE, raising=False)



def test_legacy_environment_values_cannot_enable_desktop_input(monkeypatch):
    assert policy.interaction_mode() == "background"
    for value in ("background", "interactive", "bogus", ""):
        monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, value)
        assert not policy.policy_metadata()["desktop_input_allowed"]
        with pytest.raises(RuntimeError, match="backend-only"):
            policy.require_desktop_input("save project")


def test_native_policy_metadata_describes_selective_ui_gating():
    from fl_studio_mcp.utils.native_interaction_policy import (
        DESKTOP_NATIVE_FUNCTIONS,
        GLOBAL_ONLY_TRANSPORT_FUNCTIONS,
    )

    affected = policy.policy_metadata()["native_commands_affected"]
    assert affected["explicit_functions"] == sorted(DESKTOP_NATIVE_FUNCTIONS)
    assert affected["other_functions"] == "ui.* outside the background UI allowlist"
    assert affected["model_commands_affected"] is True
    assert affected["global_only_transport_functions"] == sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS)
    assert affected["transport_requirement"] == (
        "native.call with two positional integers and flags=8"
    )



def test_legacy_interactive_value_does_not_change_backend_metadata(monkeypatch):
    expected = policy.policy_metadata()
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    assert policy.policy_metadata() == expected
    assert expected["backend_only"]
    assert expected["available_modes"] == ["background"]
    assert "never request a mode switch" in expected["ui_usage"]


@pytest.mark.parametrize("mode", ["", "INTERACTIVE", "interactive ", "allow", "false"])

def test_invalid_policy_fails_closed(monkeypatch, mode):
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, mode)
    policy.check_windows_call("ui_snapshot", {"windowHandle": "1"})
    with pytest.raises(RuntimeError, match="backend-only"):
        policy.check_windows_call("keyboard_control", {"action": "press", "key": "F6"})


@pytest.mark.parametrize(
    "name,params",
    [
        ("window_management", {"action": "find", "processName": "FL64"}),
        ("ui_snapshot", {"windowHandle": "1", "mode": "auto", "maxDepth": 6}),
        ("screenshot_control", {"windowHandle": "1", "target": "window", "annotate": False}),
        ("screenshot_control", {"target": "region", "regionX": 0, "regionY": 0,
                                "regionWidth": 100, "regionHeight": 100,
                                "outputMode": "file", "outputPath": "capture.png"}),
    ],
)
def test_background_allows_known_observations(name, params):
    policy.check_windows_call(name, params)


@pytest.mark.parametrize(
    "name,params",
    [
        ("window_management", {"action": "activate", "handle": "1"}),
        ("window_management", {"action": "move", "handle": "1"}),
        ("window_management", {"action": "resize", "handle": "1"}),
        ("window_management", {"action": "focus", "handle": "1"}),
        ("window_management", {"action": "close", "handle": "1"}),
        ("keyboard_control", {"action": "press", "key": "F6"}),
        ("keyboard_control", {"action": "type", "text": "notes"}),
        ("mouse_control", {"action": "click", "x": 1, "y": 2}),
        ("ui_click", {"elementId": "1:1"}),
        ("ui_type", {"elementId": "1:1", "text": "notes"}),
        ("ui_batch", {"steps": '[]'}),
        ("file_save", {"path": "project.flp"}),
        ("unknown_read_method", {}),
        ("ui_snapshot", {"windowHandle": "1", "activate": True}),
        ("window_management", {"action": "find", "focus": True}),
    ],
)
def test_background_rejects_input_batches_and_unknown_parameters(name, params):
    with pytest.raises(RuntimeError, match="unsupported in background mode"):
        policy.check_windows_call(name, params)



def test_read_only_session_never_allows_input_even_in_interactive_mode(monkeypatch):
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    with pytest.raises(RuntimeError, match="backend-only"):
        policy.check_windows_call("keyboard_control", {"action": "press", "key": "F6"})
    with pytest.raises(RuntimeError, match="read-only session"):
        policy.check_windows_call("keyboard_control", {"action": "press"}, read_only=True)


def _windows_fl(monkeypatch, *, read_only=False):
    response = dict(success=True, windows=[dict(className="TFruityLoopsMainForm",
                                               handle="1", title="Song - FL Studio")])
    client = SimpleNamespace(
        __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False),
        call_tool=AsyncMock(return_value=SimpleNamespace(
            is_error=False, content=[SimpleNamespace(type="text", text=json.dumps(response))])),
    )
    monkeypatch.setattr(workflows, "resolve_windows_server", lambda: "unused-test-helper")
    monkeypatch.setattr(workflows, "Client", lambda transport: client)
    return workflows.WindowsFL(read_only=read_only), client


def test_workflow_rejected_before_starting_helper(monkeypatch):
    fl, client = _windows_fl(monkeypatch)
    with pytest.raises(RuntimeError, match="unsupported in background mode"):
        asyncio.run(fl.__aenter__())
    client.__aenter__.assert_not_awaited()
    client.call_tool.assert_not_awaited()


@pytest.mark.parametrize("read_only", [True, False])

def test_legacy_policy_values_leave_observation_available_and_input_blocked(monkeypatch, read_only):
    fl, client = _windows_fl(monkeypatch, read_only=read_only)
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "bogus")
    if read_only:
        asyncio.run(fl.__aenter__())
        client.__aenter__.assert_awaited_once()
    else:
        with pytest.raises(RuntimeError, match="backend-only"):
            asyncio.run(fl.__aenter__())
        client.__aenter__.assert_not_awaited()


def test_read_only_context_allows_metadata_and_snapshot_but_never_input(monkeypatch):
    fl, client = _windows_fl(monkeypatch, read_only=True)

    async def exercise():
        async with fl:
            await fl.win("ui_snapshot", windowHandle="1", mode="auto")
            with pytest.raises(RuntimeError, match="read-only session"):
                await fl.key("F6")

    asyncio.run(exercise())
    assert [call.args[0] for call in client.call_tool.await_args_list] == [
        "window_management", "ui_snapshot"]
    client.__aexit__.assert_awaited_once()



def test_policy_change_is_checked_again_before_each_dispatch(monkeypatch):
    fl, client = _windows_fl(monkeypatch)
    for value in ("interactive", "background"):
        monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, value)
        with pytest.raises(RuntimeError, match="backend-only"):
            asyncio.run(fl.click(5, 6))
    client.call_tool.assert_not_awaited()


@pytest.mark.parametrize("operation,expected_read_only", [("render_status", True),
                                                          ("render_cancel", False)])
def test_only_render_status_enters_read_only_workflow(monkeypatch, tmp_path, operation,
                                                     expected_read_only):
    observed = []

    class Context:
        def __init__(self, *, read_only=False):
            observed.append(read_only)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def render_status(self, **kwargs):
            return kwargs

        render_cancel = render_status

    monkeypatch.setattr(workflows, "WindowsFL", Context)
    monkeypatch.setattr(workflows, "get_connection",
                        lambda: SimpleNamespace(_hardware_dir=tmp_path))
    assert asyncio.run(workflows.run_workflow(operation, directory="job")) == {"directory": "job"}
    assert observed == [expected_read_only]


@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
def test_legacy_trigger_blocks_platform_input_before_call(monkeypatch, system):
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: system)
    trigger = fl_trigger.FLStudioTrigger()
    calls = []
    trigger._trigger_func = lambda: calls.append("input") or True
    monkeypatch.setattr(fl_trigger, "_trigger", trigger)
    with pytest.raises(RuntimeError, match="Legacy piano trigger.*background mode"):
        fl_trigger.trigger_fl_studio()
    assert calls == []



def test_legacy_trigger_stays_blocked_with_interactive_environment(monkeypatch):
    trigger = fl_trigger.FLStudioTrigger()
    calls = []
    trigger._trigger_func = lambda: calls.append("input") or True
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    with pytest.raises(RuntimeError, match="backend-only"):
        trigger.trigger()
    assert calls == []


def test_native_access_defaults_to_backend_gating():
    metadata = policy.policy_metadata()
    assert policy.native_access() == "backend"
    assert metadata["native_access"] == "backend"
    assert metadata["native_ui_functions_allowed"] is False
    assert metadata["access_environment_variable"] == "FL_MCP_ACCESS"
    assert policy.with_access_note("base") == "base"


def test_full_native_access_metadata_and_instructions(monkeypatch):
    monkeypatch.setenv(policy.ACCESS_VARIABLE, "full")
    metadata = policy.policy_metadata()
    assert metadata["native_access"] == "full"
    assert metadata["native_ui_functions_allowed"] is True
    assert metadata["native_commands_affected"]["explicit_functions"] == []
    assert metadata["native_commands_affected"]["model_commands_affected"] is False
    # Native access never grants desktop input.
    assert metadata["desktop_input_allowed"] is False
    with pytest.raises(RuntimeError, match="backend-only"):
        policy.require_desktop_input("save project")
    with pytest.raises(RuntimeError, match="backend-only"):
        policy.check_windows_call("keyboard_control", {"action": "press", "key": "F6"})
    note = policy.with_access_note("base")
    assert note.startswith("base") and "FL_MCP_ACCESS=full" in note
