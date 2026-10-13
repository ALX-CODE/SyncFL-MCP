import asyncio
import copy
import json
import math

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from fl_studio_mcp.tools.plugin_adapters import register_plugin_adapter_tools
from fl_studio_mcp.utils import plugin_adapters as adapters

REVISION = "a" * 64
LAYOUT = "b" * 64


def adapter():
    return {
        "id": "test-synth", "title": "Evidence-backed Test Synth", "plugin_names": ["Test Synth"],
        "layout_sha256": LAYOUT, "parameter_count": 4,
        "evidence": {"source": "Synthetic fixture, not a real-plugin claim"},
        "notes": ["Inspect before writing"], "caution": "Host cache can be stale",
        "controls": [
            {"key": "gain_db", "name": "Gain", "parameter": 0, "kind": "numeric",
             "minimum": -60, "maximum": 0, "unit": "dB", "tolerance": 0.1,
             "points": [[0, -60], [0.5, -30], [1, 0]], "notes": ["Level only"]},
            {"key": "mode", "name": "Mode", "parameter": 1, "kind": "enum",
             "choices": [{"label": "Off", "value": 0}, {"label": "On", "value": 1}]},
            {"key": "speed", "name": "Speed", "parameter": 2, "kind": "numeric"},
            {"key": "mix", "name": "Mix", "parameter": 3, "kind": "normalized"},
        ],
    }


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "plugin_adapters.json"
    monkeypatch.setattr(adapters, "REGISTRY_PATH", path)

    def write(value=None):
        data = {"schema_version": 1, "adapters": [adapter()]} if value is None else value
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    write()
    return write


@pytest.fixture
def native(monkeypatch):
    state = {
        "revision": REVISION, "layout_sha256": LAYOUT, "project": {"session": "test"},
        "transport": {"is_playing": False, "is_recording": False},
        "target": {"plugin": "Test Synth", "parameter_count": 4, "index": 3, "slot_index": 2},
        "parameters": [
            {"parameter": i, "name": name, "value": 0.5, "display": display,
             "writable_normalized": True}
            for i, (name, display) in enumerate([
                ("Gain", "-30 dB"), ("Mode", "On"), ("Speed", "12"), ("Mix", "50%"),
            ])
        ],
    }
    calls = []

    def inspect(index, slot_index, query, offset, limit, connection):
        calls.append(("inspect", index, slot_index, offset, connection))
        total = len(state["parameters"])
        return {**copy.deepcopy(state), "parameters": copy.deepcopy(
            state["parameters"][offset:offset + limit]), "offset": offset, "matched": total,
            "next_offset": offset + limit if offset + limit < total else None}

    def apply(*args, **kwargs):
        calls.append(("apply", args, kwargs))
        return {"success": True, "status": "verified" if kwargs["apply"] else "preflighted"}

    monkeypatch.setattr(adapters.plugin_state, "inspect_plugin", inspect)
    monkeypatch.setattr(adapters.plugin_state, "apply_plugin", apply)
    return state, calls


def test_catalog_is_offline_searchable_and_preserves_evidence(registry, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Catalog must not contact FL")

    monkeypatch.setattr(adapters.plugin_state, "inspect_plugin", forbidden)
    result = adapters.catalog_adapters("gain")
    found = result["adapters"][0]
    assert found["notes"] == ["Inspect before writing"]
    assert found["caution"] == "Host cache can be stale"
    assert found["controls"][0]["calibration_status"] == "points"
    assert found["controls"][2]["calibration_status"] == "calibration_required"
    assert len(result["registry_sha256"]) == 64
    assert adapters.catalog_adapters("unrelated")["matched"] == 0


def test_inspect_binds_identity_and_returns_fresh_revision_and_filtered_controls(registry, native):
    _, calls = native
    result = adapters.inspect_controls(3, slot_index=2, query="gain", connection="sentinel")
    assert result["revision"] == REVISION and result["layout_sha256"] == LAYOUT
    assert result["matched"] == 1
    assert result["controls"][0]["normalized"] == 0.5
    assert result["controls"][0]["display"] == "-30 dB"
    assert result["controls"][0]["calibrated_range"] == [-60, 0]
    assert calls == [("inspect", 3, 2, 0, "sentinel")]


@pytest.mark.parametrize("calibration", [
    {"curve": {"kind": "linear", "minimum": -80, "maximum": 20}},
    {"curve": {"kind": "linear", "minimum": 20, "maximum": -80}},
    {"points": [[0, -80], [0.5, -30], [1, 20]]},
    {"points": [[0, 20], [0.5, -30], [1, -80]]},
])
def test_reported_calibrated_range_matches_effective_write_bounds(registry, native, calibration):
    value = adapter()
    control = value["controls"][0]
    control.pop("points")
    control.update(calibration, minimum=-40, maximum=-20)
    original = copy.deepcopy(control)
    registry({"schema_version": 1, "adapters": [value]})

    catalog_control = adapters.catalog_adapters()["adapters"][0]["controls"][0]
    inspected_control = adapters.inspect_controls(3)["controls"][0]
    assert catalog_control["calibrated_range"] == [-40, -20]
    assert inspected_control["calibrated_range"] == [-40, -20]
    for boundary in (-40, -20):
        change = adapters.compile_changes(value, [{"control": "gain_db", "value": boundary}])[0]
        assert 0 <= change["value"] <= 1
    for outside in (-40.0001, -19.9999):
        with pytest.raises(ValueError, match="outside calibrated range -40..-20"):
            adapters.compile_changes(value, [{"control": "gain_db", "value": outside}])
    assert control == original


def test_disjoint_declared_and_calibrated_ranges_fail_closed(registry):
    value = adapter()
    value["controls"][0].update(minimum=10, maximum=20)
    registry({"schema_version": 1, "adapters": [value]})
    with pytest.raises(ValueError, match="do not intersect"):
        adapters.catalog_adapters()
    with pytest.raises(ValueError, match="do not intersect"):
        adapters.compile_changes(value, [{"control": "gain_db", "value": 15}])


def test_numeric_enum_and_explicit_normalized_compile_to_guarded_changes(registry, native):
    _, calls = native
    result = adapters.set_controls(3, "Test Synth", REVISION, [
        {"control": "gain_db", "value": -45}, {"control": "mode", "value": "Off"},
        {"control": "speed", "normalized": 0.25}, {"control": "mix", "normalized": 0.7},
    ], slot_index=2, connection="sentinel")
    assert result["status"] == "preflighted" and result["adapter_id"] == "test-synth"
    _, args, kwargs = calls[-1]
    assert args[:3] == (3, "Test Synth", REVISION)
    assert args[3] == [
        {"parameter": 0, "expected_name": "Gain", "value": 0.25,
         "expected_numeric": {"value": -45, "unit": "dB", "tolerance": 0.1}},
        {"parameter": 1, "expected_name": "Mode", "value": 0, "expected_display": "Off"},
        {"parameter": 2, "expected_name": "Speed", "value": 0.25},
        {"parameter": 3, "expected_name": "Mix", "value": 0.7},
    ]
    assert kwargs == {"slot_index": 2, "apply": False, "receipt_file": None,
                      "connection": "sentinel"}


def test_apply_delegates_once_with_receipt_without_retries(registry, native, tmp_path):
    _, calls = native
    path = str(tmp_path / "receipt.json")
    result = adapters.set_controls(3, "Test Synth", REVISION, [
        {"control": "mode", "value": "On"},
    ], apply=True, receipt_file=path)
    assert result["status"] == "verified"
    assert sum(call[0] == "apply" for call in calls) == 1
    assert calls[-1][2]["receipt_file"] == path


@pytest.mark.parametrize("field,value", [
    ("layout_sha256", "c" * 64), ("plugin", "Renamed Synth"), ("parameter_count", 5),
    ("name", "Different Gain"),
])
def test_unmatched_layout_identity_and_parameter_names_fail_closed(registry, native, field, value):
    state, calls = native
    if field == "layout_sha256":
        state[field] = value
    elif field == "name":
        state["parameters"][0][field] = value
    else:
        state["target"][field] = value
    with pytest.raises(ValueError):
        adapters.set_controls(3, state["target"]["plugin"], REVISION,
                              [{"control": "gain_db", "value": -30}])
    assert all(call[0] != "apply" for call in calls)


@pytest.mark.parametrize("changes", [
    [], [{"control": "gain_db", "value": True}], [{"control": "gain_db", "value": -61}],
    [{"control": "gain_db", "value": float("nan")}],
    [{"control": "gain_db", "value": 10**500}],
    [{"control": "mode", "value": "on"}], [{"control": "mode", "value": True}],
    [{"control": "speed", "value": 20}], [{"control": "mix", "value": 0.2}],
    [{"control": "gain_db", "normalized": -0.1}],
    [{"control": "gain_db", "normalized": True}],
    [{"control": "missing", "value": 1}],
    [{"control": "mix", "value": 0.1, "normalized": 0.2}],
    [{"control": "mix", "normalized": 0.1}, {"control": "mix", "normalized": 0.2}],
])
def test_invalid_changes_do_not_delegate_a_mutation(registry, native, changes):
    _, calls = native
    with pytest.raises(ValueError):
        adapters.set_controls(3, "Test Synth", REVISION, changes)
    assert all(call[0] != "apply" for call in calls)


def test_uncalibrated_controls_require_explicit_normalized(registry, native):
    with pytest.raises(ValueError, match="calibration_required"):
        adapters.set_controls(3, "Test Synth", REVISION, [{"control": "speed", "value": 12}])
    assert adapters.set_controls(3, "Test Synth", REVISION, [
        {"control": "speed", "normalized": 0.2},
    ])["success"]


@pytest.mark.parametrize("curve,value,expected", [
    ({"kind": "linear", "minimum": -60, "maximum": 0}, -30, 0.5),
    ({"kind": "log", "minimum": 20, "maximum": 20000}, math.sqrt(20 * 20000), 0.5),
    ({"kind": "power", "minimum": 0, "maximum": 100, "exponent": 2}, 25, 0.5),
    ({"kind": "exp", "minimum": 0, "maximum": 100, "k": 2},
     100 * math.expm1(1) / math.expm1(2), 0.5),
    ({"kind": "exp", "minimum": 0, "maximum": 100, "k": -2},
     100 * math.expm1(-1) / math.expm1(-2), 0.5),
    ({"kind": "exp", "minimum": 0, "maximum": 100, "k": 1000},
     50, 1 + math.log(0.5) / 1000),
    ({"kind": "exp", "minimum": 0, "maximum": 100, "k": 1e-320}, 50, 0.5),
])
def test_explicit_analytic_curves_compile_inverse_without_runtime_fitting(
    registry, native, curve, value, expected,
):
    data = {"schema_version": 1, "adapters": [adapter()]}
    control = data["adapters"][0]["controls"][0]
    control.update(curve=curve, minimum=min(curve["minimum"], curve["maximum"]),
                   maximum=max(curve["minimum"], curve["maximum"]))
    control.pop("points")
    registry(data)
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "value": value}])
    assert native[1][-1][1][3][0]["value"] == pytest.approx(expected)


def test_analytic_curve_preferred_over_point_interpolation_and_endpoints(registry):
    data = {"schema_version": 1, "adapters": [adapter()]}
    control = data["adapters"][0]["controls"][0]
    control.update(minimum=0, maximum=100, points=[[0, 0], [1, 100]],
                   curve={"kind": "power", "minimum": 0, "maximum": 100, "exponent": 2})
    registry(data)
    loaded, _ = adapters._load_registry()
    actual = loaded["adapters"][0]
    for physical, normalized in [(0, 0), (25, 0.5), (100, 1)]:
        result = adapters.compile_changes(actual, [{"control": "gain_db", "value": physical}])
        assert result[0]["value"] == normalized
    metadata = adapters.catalog_adapters()["adapters"][0]["controls"][0]
    assert metadata["calibration_status"] == "curve"


def test_unitless_numeric_mapping_passes_empty_unit_to_display_verification(registry, native):
    data = {"schema_version": 1, "adapters": [adapter()]}
    data["adapters"][0]["controls"][0]["unit"] = ""
    registry(data)
    native[0]["parameters"][0]["display"] = "-30"
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "value": -30}])
    assert native[1][-1][1][3][0]["expected_numeric"] == {
        "value": -30, "unit": "", "tolerance": 0.1,
    }
    inspected = adapters.inspect_controls(3)
    assert inspected["adapter_metadata"]["caution"] == "Host cache can be stale"
    assert "binary/build" in inspected["limitations"][0]


def test_semantic_unit_can_differ_from_literal_display_unit(registry, native):
    data = {"schema_version": 1, "adapters": [adapter()]}
    data["adapters"][0]["controls"][0].update(unit="ms", display_unit="")
    registry(data)
    native[0]["parameters"][0]["display"] = "-30"
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "value": -30}])
    assert native[1][-1][1][3][0]["expected_numeric"]["unit"] == ""
    control = adapters.inspect_controls(3)["controls"][0]
    assert control["unit"] == "ms" and control["display_unit"] == ""


@pytest.mark.parametrize("display", [
    "50 %", "50 ms", "Auto", "-inf dB", "NaN dB", "", "-20 dB / -6 dB",
])
@pytest.mark.parametrize("apply", [False, True])
def test_incompatible_or_unparsable_current_display_blocks_entire_batch(
    registry, native, tmp_path, display, apply,
):
    native[0]["parameters"][0]["display"] = display
    with pytest.raises(ValueError, match="current display cannot establish the calibrated unit"):
        adapters.set_controls(3, "Test Synth", REVISION, [
            {"control": "mode", "value": "Off"}, {"control": "gain_db", "value": -20},
        ], apply=apply, receipt_file=str(tmp_path / "unused.json"))
    assert all(call[0] != "apply" for call in native[1])
    assert not (tmp_path / "unused.json").exists()


def test_finite_current_display_in_compatible_scaled_unit_allows_physical_write(registry, native):
    value = adapter()
    value["controls"][0].update(
        unit="ms", minimum=0, maximum=1000, points=[[0, 0], [1, 1000]],
    )
    registry({"schema_version": 1, "adapters": [value]})
    native[0]["parameters"][0]["display"] = "0.5 sec"
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "value": 250}])
    compiled = native[1][-1][1][3][0]
    assert compiled["value"] == 0.25
    assert compiled["expected_numeric"]["unit"] == "ms"


def test_infinite_endpoint_requires_explicit_normalized_transition(registry, native):
    native[0]["parameters"][0]["display"] = "-inf dB"
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "normalized": 0.5}])
    assert native[1][-1][0] == "apply"
    assert "expected_numeric" not in native[1][-1][1][3][0]
    limitations = adapters.inspect_controls(3)["limitations"]
    assert any("infinity/sentinel" in limitation for limitation in limitations)


def test_enum_choice_label_uses_separate_exact_literal_display(registry, native):
    data = {"schema_version": 1, "adapters": [adapter()]}
    data["adapters"][0]["controls"][1]["choices"][1]["display"] = "On "
    registry(data)
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "mode", "value": "On"}])
    assert native[1][-1][1][3][0]["expected_display"] == "On "
    with pytest.raises(ValueError, match="exact declared"):
        adapters.set_controls(3, "Test Synth", REVISION, [{"control": "mode", "value": "On "}])


@pytest.mark.parametrize("mutate", [
    lambda a: a["controls"][1]["choices"][1].update(label="On "),
    lambda a: a["controls"][1]["choices"][1].update(display=True),
    lambda a: a["controls"][1]["choices"][1].update(display="Off"),
    lambda a: a["controls"][0].update(display_unit=1),
    lambda a: a["controls"][1].update(display_unit=""),
])
def test_invalid_display_metadata_rejected(registry, mutate):
    value = adapter()
    mutate(value)
    registry({"schema_version": 1, "adapters": [value]})
    with pytest.raises(ValueError):
        adapters.catalog_adapters()


def conditional_adapter():
    value = adapter()
    value["controls"][0]["requires"] = [{"control": "mode", "one_of": ["On"]}]
    value["controls"][1]["choices"][1]["display"] = "On "
    return value


def test_conditions_use_fresh_exact_enum_display_and_are_reported(registry, native):
    registry({"schema_version": 1, "adapters": [conditional_adapter()]})
    state, calls = native
    state["parameters"][1]["display"] = "On "
    control = adapters.inspect_controls(3)["controls"][0]
    assert control["requirements_met"] is True
    assert control["requirement_status"] == [{
        "control": "mode", "one_of": ["On"], "current_label": "On",
        "current_display": "On ", "satisfied": True,
    }]
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "value": -30}])
    assert calls[-1][0] == "apply"


@pytest.mark.parametrize("display", ["Off", "On", "Unknown"])
@pytest.mark.parametrize("change", [
    {"control": "gain_db", "normalized": 0.5}, {"control": "gain_db", "value": -30},
])
def test_unmet_or_unknown_condition_fails_before_delegation(registry, native, display, change):
    registry({"schema_version": 1, "adapters": [conditional_adapter()]})
    native[0]["parameters"][1]["display"] = display
    control = adapters.inspect_controls(3)["controls"][0]
    assert control["requirements_met"] is False
    with pytest.raises(ValueError, match="unavailable"):
        adapters.set_controls(3, "Test Synth", REVISION, [change])
    assert all(call[0] != "apply" for call in native[1])


def test_selector_and_dependent_changes_require_separate_fresh_inspections(registry, native):
    registry({"schema_version": 1, "adapters": [conditional_adapter()]})
    native[0]["parameters"][1]["display"] = "On "
    with pytest.raises(ValueError, match="selector separately"):
        adapters.set_controls(3, "Test Synth", REVISION, [
            {"control": "mode", "value": "On"}, {"control": "gain_db", "value": -30},
        ])
    assert all(call[0] != "apply" for call in native[1])
    adapters.set_controls(3, "Test Synth", REVISION, [{"control": "mode", "value": "Off"}])
    assert native[1][-1][0] == "apply"


@pytest.mark.parametrize("requirements", [
    [{"control": "missing", "one_of": ["On"]}],
    [{"control": "speed", "one_of": ["On"]}],
    [{"control": "mode", "one_of": ["Unknown"]}],
    [{"control": "mode", "one_of": []}],
    [{"control": "mode", "one_of": [True]}],
    [{"control": "mode", "one_of": ["On", "On"]}],
    [{"control": "gain_db", "one_of": ["On"]}],
    [{"control": "mode", "one_of": ["On"], "ignored": True}],
])
def test_invalid_condition_schema_fails_closed(registry, requirements):
    value = adapter()
    value["controls"][0]["requires"] = requirements
    registry({"schema_version": 1, "adapters": [value]})
    with pytest.raises(ValueError):
        adapters.catalog_adapters()


def test_decreasing_points_and_partial_domain_are_supported_without_extrapolation(registry):
    data = {"schema_version": 1, "adapters": [adapter()]}
    control = data["adapters"][0]["controls"][0]
    control["points"] = [[0.2, 0], [0.5, -30], [0.8, -60]]
    registry(data)
    loaded, _ = adapters._load_registry()
    changes = adapters.compile_changes(
        loaded["adapters"][0], [{"control": "gain_db", "value": -45}],
    )
    assert changes[0]["value"] == pytest.approx(0.65)
    with pytest.raises(ValueError, match="calibrated range"):
        adapters.compile_changes(loaded["adapters"][0], [{"control": "gain_db", "value": 1}])


def test_complete_catalog_pages_must_keep_same_revision(registry, native):
    state, calls = native
    state["parameters"] += [
        {"parameter": i, "name": f"Other {i}", "value": 0.2, "display": "0.2",
         "writable_normalized": True} for i in range(4, 601)
    ]
    state["target"]["parameter_count"] = 601
    data = {"schema_version": 1, "adapters": [adapter()]}
    data["adapters"][0]["parameter_count"] = 601
    registry(data)
    assert adapters.inspect_controls(3)["matched"] == 4
    assert [call[3] for call in calls] == [0, 500]


def test_changed_page_revision_fails_before_binding(registry, native, monkeypatch):
    original = adapters.plugin_state.inspect_plugin

    def changing(*args, **kwargs):
        page = original(*args, **kwargs)
        if kwargs["offset"] == 0:
            page["next_offset"] = 2
        else:
            page["revision"] = "c" * 64
        return page

    monkeypatch.setattr(adapters.plugin_state, "inspect_plugin", changing)
    with pytest.raises(ValueError, match="changed during"):
        adapters.inspect_controls(3)


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(layout_sha256="bad"),
    lambda a: a.update(plugin_names=[{}]),
    lambda a: a.update(plugin_names=["Test Synth", "Test Synth"]),
    lambda a: a["controls"].append(copy.deepcopy(a["controls"][0])),
    lambda a: a["controls"][0].update(points=[[0, -60], [0, 0]]),
    lambda a: a["controls"][0].update(points=[[0, -60], [1, -60]]),
    lambda a: a["controls"][0].pop("tolerance"),
    lambda a: a["controls"][0].update(kind={}),
    lambda a: a["controls"][0].update(
        curve={"kind": "log", "minimum": 0, "maximum": 100}),
    lambda a: a["controls"][0].update(
        curve={"kind": "power", "minimum": 0, "maximum": 100, "exponent": 0}),
    lambda a: a["controls"][0].update(
        curve={"kind": "exp", "minimum": 0, "maximum": 100, "k": 0}),
])
def test_invalid_registry_fails_closed(registry, mutate):
    value = adapter()
    mutate(value)
    registry({"schema_version": 1, "adapters": [value]})
    with pytest.raises(ValueError):
        adapters.catalog_adapters()


def test_duplicate_binding_and_json_keys_rejected(registry):
    other = adapter()
    other["id"] = "other-id"
    registry({"schema_version": 1, "adapters": [adapter(), other]})
    with pytest.raises(ValueError, match="Ambiguous"):
        adapters.catalog_adapters()
    adapters.REGISTRY_PATH.write_text('{"schema_version":1,"schema_version":1,"adapters":[]}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        adapters.catalog_adapters()


def test_stale_revision_unwritable_parameter_and_missing_receipt_fail_closed(registry, native):
    state, calls = native
    with pytest.raises(ValueError, match="revision changed"):
        adapters.set_controls(3, "Test Synth", "c" * 64, [{"control": "mix", "normalized": 0.2}])
    with pytest.raises(ValueError, match="receipt"):
        adapters.set_controls(3, "Test Synth", REVISION, [], apply=True)
    state["parameters"][0]["writable_normalized"] = False
    with pytest.raises(ValueError, match="writable"):
        adapters.set_controls(3, "Test Synth", REVISION, [{"control": "gain_db", "value": -30}])
    assert all(call[0] != "apply" for call in calls)


def test_tools_are_discoverable_and_call_registry_or_guarded_workflow(registry, native):
    mcp = FastMCP("Plugin adapter tests")
    register_plugin_adapter_tools(mcp)

    async def exercise():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools) == {
                "fl_plugin_adapter_catalog", "fl_plugin_controls_inspect", "fl_plugin_controls_set",
            }
            result = await client.call_tool("fl_plugin_adapter_catalog", {})
            assert result.structured_content["matched"] == 1
            result = await client.call_tool("fl_plugin_controls_inspect", {"index": 3})
            assert result.structured_content["revision"] == REVISION
            result = await client.call_tool("fl_plugin_controls_set", {
                "index": 3, "expected_plugin": "Test Synth", "expected_revision": REVISION,
                "changes": [{"control": "mode", "value": "On"}],
            })
            assert result.structured_content["status"] == "preflighted"
            with pytest.raises(ToolError):
                await client.call_tool("fl_plugin_controls_set", {
                    "index": 3, "expected_plugin": "Test Synth", "expected_revision": REVISION,
                    "changes": [{"control": "mode", "value": "On"}], "apply": 1,
                })

    asyncio.run(exercise())
def test_compact_write_result_preserves_full_receipt_snapshots():
    rows = [{"parameter": i, "name": f"Parameter {i}"} for i in range(4240)]
    snapshot = {"revision": "a" * 64, "parameters": rows, "target": {"plugin": "Test"}}
    original = {"success": True, "before": snapshot, "readbacks": [snapshot, snapshot]}
    compact = adapters._compact_write_result(original, {3, 20})
    assert [r["parameter"] for r in compact["before"]["parameters"]] == [3, 20]
    assert compact["before"]["parameters_total"] == 4240
    assert compact["before"]["parameters_scope"] == "requested_controls"
    assert compact["readbacks"][1]["revision"] == "a" * 64
    assert len(original["before"]["parameters"]) == 4240
    assert all(len(state["parameters"]) == 4240 for state in original["readbacks"])
