"""Exercise the actual embedded-controller entrypoint without a live host."""

import ast
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def controller(tmp_path):
    source = Path(__file__).parents[1] / "fl_controller" / "mcp_extended.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"validate_command", "execute_pending_command", "write_response"}
    functions = ast.Module(
        body=[node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    calls = []
    namespace = dict(
        json=json, math=math, time=time,
        COMMAND_FILE=tmp_path / "command.json", RESPONSE_FILE=tmp_path / "response.json",
        RECEIPTS={}, RECEIPT_COMMANDS={},
        MODULES={"general": SimpleNamespace(safeToEdit=lambda: True)},
        extended_dispatch=lambda action, params: calls.append((action, params)) or {"value": 42},
    )
    exec(compile(functions, str(source), "exec"), namespace)

    def run(command):
        namespace["COMMAND_FILE"].write_text(json.dumps(command), encoding="utf-8")
        namespace["execute_pending_command"]()
        return json.loads(namespace["RESPONSE_FILE"].read_text(encoding="utf-8"))

    return namespace, calls, run


def envelope(**changes):
    return dict(protocol=2, id="test-request", action="transport.start", params={},
                expires_at=time.time() + 60, **changes)


@pytest.mark.parametrize("bad", [None, [], "text", 42])
def test_nonobject_never_dispatches(controller, bad):
    _, calls, run = controller
    result = run(bad)
    assert result["success"] is False
    assert result["id"] is None
    assert not calls


@pytest.mark.parametrize("field,value", [
    ("protocol", None), ("protocol", True), ("protocol", 1), ("protocol", 2.0),
    ("id", None), ("id", ""), ("id", []), ("id", "a" * 129),
    ("action", None), ("action", ""), ("action", 2),
    ("params", []), ("params", {"value": float("nan")}),
    ("expires_at", None), ("expires_at", True), ("expires_at", float("inf")),
])
def test_malformed_envelopes_never_dispatch(controller, field, value):
    _, calls, run = controller
    command = envelope()
    command[field] = value
    assert run(command)["success"] is False
    assert not calls


def test_expired_request_caches_failure_without_dispatch(controller):
    namespace, calls, run = controller
    command = envelope()
    command["expires_at"] = time.time() - 1
    result = run(command)
    assert result["success"] is False
    assert "expired" in result["error"]
    assert run(command) == result
    assert namespace["RECEIPTS"][command["id"]] == result
    assert not calls


def test_exact_replay_dispatches_once_and_conflict_does_not_replace_receipt(controller):
    namespace, calls, run = controller
    command = envelope()
    result = run(command)
    assert result["success"] is True
    assert run(command) == result
    changed = dict(command, params={"other": 1})
    rejected = run(changed)
    assert rejected["success"] is False
    assert "reused" in rejected["error"]
    assert namespace["RECEIPTS"][command["id"]] == result
    assert len(calls) == 1


def test_busy_host_never_dispatches(controller):
    namespace, calls, run = controller
    namespace["MODULES"]["general"].safeToEdit = lambda: False
    assert run(envelope())["success"] is False
    assert not calls


def test_receipt_and_fingerprint_eviction_stay_bounded_together(controller):
    namespace, _, run = controller
    for index in range(130):
        command = envelope()
        command["id"] = str(index)
        assert run(command)["success"] is True
    assert len(namespace["RECEIPTS"]) == len(namespace["RECEIPT_COMMANDS"]) == 128
    assert namespace["RECEIPTS"].keys() == namespace["RECEIPT_COMMANDS"].keys()
    assert "0" not in namespace["RECEIPTS"]


@pytest.fixture
def native_controller(tmp_path):
    source = Path(__file__).parents[1] / "fl_controller" / "mcp_extended.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"validate_command", "execute_pending_command", "write_response", "validate_native"}
    functions = ast.Module(
        body=[node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    namespace = dict(
        json=json, math=math, time=time, ACTIVE_ACCESS=None,
        COMMAND_FILE=tmp_path / "command.json", RESPONSE_FILE=tmp_path / "response.json",
        RECEIPTS={}, RECEIPT_COMMANDS={},
        MODULES={"general": SimpleNamespace(safeToEdit=lambda: True)},
        CATALOG={
            "general.restoreUndoLevel": dict(
                module="general", function="restoreUndoLevel", arguments=["level"],
                required=1, signature="level", kind="write", access="full"),
            "mixer.setTrackVolume": dict(
                module="mixer", function="setTrackVolume", arguments=["index", "volume"],
                required=2, signature="index, volume", kind="write"),
        },
    )
    seen = []

    def dispatch(action, params):
        seen.append(namespace["ACTIVE_ACCESS"])
        namespace["validate_native"](params)
        return {"value": None}

    namespace["extended_dispatch"] = dispatch
    exec(compile(functions, str(source), "exec"), namespace)

    def run(params, **changes):
        command = dict(envelope(**changes), action="native.call", params=params,
                       id="native-%d" % len(seen))
        namespace["COMMAND_FILE"].write_text(json.dumps(command), encoding="utf-8")
        namespace["execute_pending_command"]()
        return json.loads(namespace["RESPONSE_FILE"].read_text(encoding="utf-8"))

    return namespace, seen, run


def test_full_access_functions_need_the_full_access_envelope(native_controller):
    namespace, seen, run = native_controller
    call = {"function": "general.restoreUndoLevel", "args": [1], "allow_write": True}
    refused = run(call)
    assert refused["success"] is False
    assert "FL_MCP_ACCESS=full" in refused["error"]
    assert run(call, access="full")["success"] is True
    assert run(call, access="FULL")["success"] is False
    assert seen == [None, "full", None]
    assert namespace["ACTIVE_ACCESS"] is None


def test_ordinary_native_functions_ignore_the_access_envelope(native_controller):
    _, _, run = native_controller
    call = {"function": "mixer.setTrackVolume", "args": [1, 0.5], "allow_write": True}
    assert run(call)["success"] is True
    assert run(call, access="full")["success"] is True
