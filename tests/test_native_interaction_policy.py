import json
from pathlib import Path

import pytest

from fl_studio_mcp.utils.native_interaction_policy import (
    BACKGROUND_UI_FUNCTIONS,
    DESKTOP_NATIVE_FUNCTIONS,
    FULL_ACCESS_ONLY_FUNCTIONS,
    GLOBAL_ONLY_TRANSPORT_FUNCTIONS,
    GT_GLOBAL,
    check_native_command,
)


@pytest.fixture(autouse=True)
def background(monkeypatch):
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)


@pytest.mark.parametrize("function", [
    *sorted(DESKTOP_NATIVE_FUNCTIONS), "ui.setFocused", "ui.showWindow", "ui.hideWindow",
    "ui.enter", "ui.copy", "ui.paste", "ui.openEventEditor", "ui.selectBrowserMenuItem",
    "ui.launchAudioEditor", "ui.futureUnreviewedAction",
])
def test_native_ui_and_editor_operations_require_opt_in(function, monkeypatch):
    with pytest.raises(RuntimeError, match="unsupported in background"):
        check_native_command("native.call", {"function": function, "allow_write": True})
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    with pytest.raises(RuntimeError, match="backend-only"):
        check_native_command("native.call", {"function": function, "allow_write": True})


@pytest.mark.parametrize("function", [
    *sorted(BACKGROUND_UI_FUNCTIONS), "mixer.setTrackVolume", "plugins.setParamValue",
    "transport.start", "transport.stop", "transport.record", "channels.setGridBit",
    "patterns.setPatternName", "general.saveUndo",
])
def test_native_observations_and_nonfocus_setters_remain_background_operations(function):
    check_native_command("native.call", {"function": function, "allow_write": True})


@pytest.mark.parametrize("function,args", [
    *((function, [panel]) for function in ("ui.showWindow", "ui.hideWindow")
      for panel in range(5)),
    ("ui.openEventEditor", [65536, 1, False]),
])
def test_addressed_panels_are_background_operations(function, args):
    check_native_command("native.call", dict(function=function, args=args, allow_write=True))


@pytest.mark.parametrize("function,args", [
    ("ui.showWindow", [True]), ("ui.showWindow", [5]), ("ui.showWindow", [0, 1]),
    ("ui.hideWindow", [True]), ("ui.hideWindow", [5]), ("ui.hideWindow", [0, 1]),
    ("ui.openEventEditor", [0, 0, False]), ("ui.openEventEditor", [0, 1, True]),
    ("ui.openEventEditor", [-1, 1, False]), ("ui.openEventEditor", [0, 1]),
])
def test_ambiguous_panel_forms_remain_gated(function, args):
    with pytest.raises(RuntimeError, match="unsupported in background"):
        check_native_command("native.call", dict(function=function, args=args, allow_write=True))


def test_every_packaged_native_ui_read_remains_allowed():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "fl_controller/native_catalog.json").read_text())
    for function, spec in catalog.items():
        if spec["kind"] == "read":
            check_native_command("native.call", {"function": function})


@pytest.mark.parametrize("action", [
    "project.save_current", "production.inspect", "production.apply", "channels.triggerNote",
    "channels.getStepSequence", "piano.inspect", "piano.apply", "bridge.ping", "plugins.setMany",
])
def test_typed_model_operations_are_not_blanket_blocked(action):
    check_native_command(action, {})


def batch(*steps):
    return {"action": "bridge.batch", "params": {"steps": list(steps)}}


def test_nested_batch_and_preview_cannot_hide_native_ui_action():
    step = batch(batch({"action": "native.call", "params": {"function": "ui.setFocused"}}))
    step["params"]["dry_run"] = True
    with pytest.raises(RuntimeError, match="ui.setFocused"):
        check_native_command(step["action"], step["params"])


def test_direct_action_alias_cannot_bypass_classification():
    with pytest.raises(RuntimeError, match="channels.showEditor"):
        check_native_command("channels.showEditor", {})


@pytest.mark.parametrize("function", sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS))
@pytest.mark.parametrize("first", [-1, 0, 1])
def test_focus_routed_transport_requires_explicit_global_positional_flags(function, first):
    check_native_command("native.call", {
        "function": function, "args": [first, GT_GLOBAL], "allow_write": True,
    })


@pytest.mark.parametrize("function", sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS))
@pytest.mark.parametrize("arguments", [
    {}, {"args": []}, {"args": [1]}, {"args": [1, 15]}, {"args": [1, 0]},
    {"args": [1, 7]}, {"args": [1, 9]}, {"args": [1, True]}, {"args": [1, 8.0]},
    {"args": [1, "8"]}, {"args": [1, None]}, {"args": [True, 8]},
    {"args": [1, 8, 0]}, {"args": {"value": 1, "flags": 8}},
    {"args": [1], "flags": 8}, {"args": [1], "kwargs": {"flags": 8}},
    {"kwargs": {"value": 1, "flags": 8}}, {"kwargs": {"startStop": 1, "flags": 8}},
    {"args": [1, 8], "kwargs": {"flags": 15}},
])
def test_ambiguous_or_focus_routed_transport_forms_are_blocked(function, arguments, monkeypatch):
    params = {"function": function, "allow_write": True, **arguments}
    with pytest.raises(RuntimeError, match="flags=GT_Global"):
        check_native_command("native.call", params)
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    with pytest.raises(RuntimeError, match="backend-only"):
        check_native_command("native.call", params)


@pytest.mark.parametrize("function", sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS))
@pytest.mark.parametrize("params", [
    {}, {"args": [1]}, {"args": [1, 8]}, {"args": [1], "kwargs": {"flags": 8}},
    {"value": 1, "flags": 8}, {"startStop": 1, "flags": 8},
    {"kwargs": {"value": 1, "flags": 8}},
])
def test_unimplemented_transport_aliases_cannot_bypass_native_dispatch(function, params):
    with pytest.raises(RuntimeError, match="background requires native.call"):
        check_native_command(function, params)


@pytest.mark.parametrize("function", sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS))
def test_nested_preview_cannot_hide_default_focus_routed_transport(function):
    step = batch(batch({"action": "native.call", "params": {
        "function": function, "args": [1], "allow_write": True,
    }}))
    step["params"]["dry_run"] = True
    with pytest.raises(RuntimeError, match="flags=GT_Global"):
        check_native_command(step["action"], step["params"])


def test_focus_routed_transport_policy_tracks_exact_packaged_signatures():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "fl_controller/native_catalog.json").read_text())
    assert GT_GLOBAL == 8
    for function in GLOBAL_ONLY_TRANSPORT_FUNCTIONS:
        spec = catalog[function]
        first = "value" if function.endswith("Jog") else "startStop"
        assert spec["arguments"] == [first, "flags"]
        assert spec["required"] == 1 and spec["kind"] == "write"
        assert spec["signature"].endswith("flags: int=midi.GT_All")


def test_nested_background_batch_passes():
    step = batch(batch({"action": "native.call", "params": {"function": "plugins.setParamValue"}}),
                 {"action": "project.save_current", "params": {"dry_run": False}})
    check_native_command(step["action"], step["params"])


@pytest.mark.parametrize("action,params", [
    ("bridge.batch", {"steps": []}), ("bridge.batch", {"steps": [{}]}),
    ("bridge.batch", {"steps": [{"action": "bridge.ping", "params": []}]}),
    ("bridge.batch", {"steps": [{"action": "bridge.ping"}] * 257}),
    ("native.call", {"function": []}), ("native.call", {}), ("bridge.ping", []),
])
def test_malformed_command_trees_fail_closed(action, params):
    with pytest.raises(ValueError):
        check_native_command(action, params)


def test_depth_total_step_and_cycle_limits_are_bounded():
    deep = {"action": "bridge.ping", "params": {}}
    for _ in range(10):
        deep = batch(deep)
    with pytest.raises(ValueError, match="depth"):
        check_native_command(deep["action"], deep["params"])
    many = batch(*[batch(*[{"action": "bridge.ping"}] * 128)] * 2)
    with pytest.raises(ValueError, match="total batch steps"):
        check_native_command(many["action"], many["params"])
    cycle = batch()
    cycle["params"]["steps"].append(cycle)
    with pytest.raises(ValueError, match="depth"):
        check_native_command(cycle["action"], cycle["params"])



def test_legacy_mode_value_leaves_native_reads_available_and_focus_calls_blocked(monkeypatch):
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "typo")
    check_native_command("bridge.ping", {})
    with pytest.raises(RuntimeError, match="backend-only"):
        check_native_command("native.call", {"function": "ui.setFocused", "args": [1]})


@pytest.mark.parametrize("function", sorted(FULL_ACCESS_ONLY_FUNCTIONS))
def test_full_access_only_functions_are_gated_by_default(function):
    with pytest.raises(RuntimeError, match="FL_MCP_ACCESS=full"):
        check_native_command(
            "native.call", {"function": function, "args": [0, 8], "allow_write": True})


FULL_ACCESS_SAMPLE = [
    *sorted(DESKTOP_NATIVE_FUNCTIONS), *sorted(FULL_ACCESS_ONLY_FUNCTIONS),
    *sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS), "ui.setFocused", "ui.enter", "ui.copy",
    "ui.paste", "ui.showWindow", "ui.openEventEditor", "ui.futureUnreviewedAction",
]


@pytest.mark.parametrize("function", FULL_ACCESS_SAMPLE)
def test_full_access_allows_every_native_function(function, monkeypatch):
    monkeypatch.setenv("FL_MCP_ACCESS", "full")
    check_native_command("native.call", {"function": function, "args": [], "allow_write": True})
    check_native_command("bridge.batch", {"steps": [
        {"action": "native.call", "params": {"function": function, "args": [1, 2, 3]}},
    ]})


@pytest.mark.parametrize("value", ["", "backend", "yes", "true", "1", "interactive", "fullest"])
def test_other_access_values_keep_default_gating(value, monkeypatch):
    monkeypatch.setenv("FL_MCP_ACCESS", value)
    with pytest.raises(RuntimeError, match="unsupported in background"):
        check_native_command("native.call", {"function": "ui.enter", "allow_write": True})


def test_full_access_keeps_structural_limits(monkeypatch):
    monkeypatch.setenv("FL_MCP_ACCESS", " FULL ")
    with pytest.raises(ValueError, match="nonempty function"):
        check_native_command("native.call", {"function": ""})
    nested = {"action": "native.call", "params": {"function": "ui.enter"}}
    for _ in range(10):
        nested = batch(nested)
    with pytest.raises(ValueError, match="depth"):
        check_native_command(nested["action"], nested["params"])


def test_catalog_marks_exactly_the_full_access_functions():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "fl_controller/native_catalog.json").read_text())
    marked = {name for name, spec in catalog.items() if spec.get("access") == "full"}
    assert marked == FULL_ACCESS_ONLY_FUNCTIONS
    assert all(catalog[name]["kind"] == "write" for name in marked)
