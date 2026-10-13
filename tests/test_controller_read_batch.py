"""Run the real batch dispatcher: observations must not dirty the FL project."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def controller():
    source = Path(__file__).parents[1] / "fl_controller/mcp_extended.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name == "extended_dispatch"]
    calls = []
    mixer = SimpleNamespace(afterRoutingChanged=lambda: calls.append("dirty"),
                            getTrackInfo=lambda mode: 3)
    namespace = dict(
        IN_VALIDATED_BATCH=False, mixer=mixer,
        CATALOG={"channels.getTargetFxTrack": {"kind": "read"},
                 "channels.selectOneChannel": {"kind": "write"}},
        preflight=lambda *a: None, routing_preflight=lambda *a: None,
        validate_native=lambda p: ({"module": "channels", "function": "call"}, p["args"]),
        MODULES={"channels": SimpleNamespace(call=lambda *a: 3)},
        track_info=lambda *a: {}, plugin_target=lambda p: (0, 0, "Test"),
    )
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["extended_dispatch"], calls, namespace


def native(function):
    return dict(action="native.call", params=dict(function=function, args=[]))


def test_read_only_native_and_mixer_batch_never_notifies_or_dirties(controller):
    dispatch, calls, _ = controller
    result = dispatch("bridge.batch", dict(dry_run=False, steps=[
        native("channels.getTargetFxTrack"), dict(action="mixer.inspect", params={"tracks": [3]})]))
    assert result["applied"] == 2
    assert not calls


def test_write_batch_retains_existing_notification(controller):
    dispatch, calls, _ = controller
    result = dispatch("bridge.batch", dict(dry_run=False, steps=[
        native("channels.getTargetFxTrack"), native("channels.selectOneChannel")]))
    assert result["applied"] == 2
    assert calls == ["dirty"]


def test_dry_run_has_no_notification(controller):
    dispatch, calls, _ = controller
    assert dispatch("bridge.batch", dict(dry_run=True, steps=[
        native("channels.selectOneChannel")])) == dict(validated=1, applied=0)
    assert not calls
