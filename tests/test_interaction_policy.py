"""Desktop input is opt-in; these tests never launch a Windows helper."""

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


def test_default_is_background_and_interactive_requires_explicit_opt_in(monkeypatch):
    assert policy.interaction_mode() == "background"
    assert policy.policy_metadata()["desktop_input_allowed"] is False
    with pytest.raises(RuntimeError, match="FL_MCP_INTERACTION_POLICY=interactive"):
        policy.require_desktop_input("save project")
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    policy.require_desktop_input("save project")
    assert policy.policy_metadata()["desktop_input_allowed"] is True


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


def test_interactive_adds_only_ui_permission_to_the_shared_backend(monkeypatch):
    background = policy.policy_metadata()
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    interactive = policy.policy_metadata()
    assert interactive["preferred_execution"] == "native_backend"
    assert interactive["backend_apis_available"] and interactive["shared_backend_in_all_modes"]
    assert interactive["interactive_secondary"] and interactive["interactive_discouraged"]
    assert "user permits UI input" in interactive["ui_usage"]
    assert not interactive["automatic_interactive_fallback"]
    differences = {key for key in background if background[key] != interactive[key]}
    assert differences == {"mode", "desktop_input_allowed"}


@pytest.mark.parametrize("mode", ["", "INTERACTIVE", "interactive ", "allow", "false"])
def test_invalid_policy_fails_closed(monkeypatch, mode):
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, mode)
    with pytest.raises(ValueError, match="must be 'background' or 'interactive'"):
        policy.check_windows_call("ui_snapshot", {"windowHandle": "1"})


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
def test_invalid_policy_rejected_before_starting_helper(monkeypatch, read_only):
    fl, client = _windows_fl(monkeypatch, read_only=read_only)
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "bogus")
    with pytest.raises(ValueError, match="must be"):
        asyncio.run(fl.__aenter__())
    client.__aenter__.assert_not_awaited()
    client.call_tool.assert_not_awaited()


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
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    fl, client = _windows_fl(monkeypatch)

    async def exercise():
        async with fl:
            monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "background")
            with pytest.raises(RuntimeError, match="unsupported in background mode"):
                await fl.click(5, 6)

    asyncio.run(exercise())
    assert client.call_tool.await_count == 1


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


def test_legacy_trigger_only_runs_with_interactive_opt_in(monkeypatch):
    trigger = fl_trigger.FLStudioTrigger()
    calls = []
    trigger._trigger_func = lambda: calls.append("input") or True
    monkeypatch.setenv(policy.ENVIRONMENT_VARIABLE, "interactive")
    assert trigger.trigger() is True
    assert calls == ["input"]
