"""Evidence-bound named plugin controls over the guarded plugin-state workflow.

Physical writes require a finite current display with a compatible calibrated
unit. Sentinel displays such as -inf must first transition through an explicit
normalized request. Unit checks cannot distinguish modes sharing a unit; those
dependencies must be declared with ``requires``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from fl_studio_mcp.utils import plugin_state
from fl_studio_mcp.utils.plugin_display import parse_display_number

REGISTRY_PATH = Path(__file__).with_name("plugin_adapters.json")
MAX_REGISTRY_BYTES = 4 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _text(value, label, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise ValueError(f"{label} must be nonempty text of at most {maximum} characters")
    return value


def _number(value, label, low=-1e12, high=1e12):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(f"{label} must be finite within {low}..{high}")
    return value


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer within {low}..{high}")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError(f"Nonfinite JSON: {value}")


def _annotations(value):
    for field in ("notes", "cautions"):
        entries = value.get(field, [])
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError(f"{field} must contain at most 100 strings")
        for entry in entries:
            _text(entry, field, 4000)
    if "caution" in value:
        _text(value["caution"], "caution", 4000)


def _curve(control):
    curve = control.get("curve")
    return curve if isinstance(curve, dict) and curve.get("kind") != "table" else None


def _validate_control(control, count):
    allowed = {
        "key", "parameter", "name", "kind", "unit", "minimum", "maximum", "tolerance",
        "curve", "points", "choices", "notes", "caution", "cautions", "display_unit", "requires",
    }
    if not isinstance(control, dict) or set(control) - allowed:
        raise ValueError("Invalid adapter control fields")
    _text(control.get("key"), "control key", 100)
    _text(control.get("name"), "parameter name")
    _integer(control.get("parameter"), "parameter", 0, count - 1)
    kind = control.get("kind")
    if not isinstance(kind, str) or kind not in {"numeric", "enum", "normalized"}:
        raise ValueError("Control kind must be numeric, enum or normalized")
    _annotations(control)
    for key in ("minimum", "maximum", "tolerance"):
        if key in control:
            _number(control[key], key, 0 if key == "tolerance" else -1e12)
    if "minimum" in control and "maximum" in control:
        if control["minimum"] >= control["maximum"]:
            raise ValueError("Control minimum must be less than maximum")
    for field in ("unit", "display_unit"):
        if field in control:
            unit = control[field]
            if not isinstance(unit, str) or len(unit) > 32 or "\0" in unit:
                raise ValueError(f"{field} must be text of at most 32 characters; empty is allowed")
    if "display_unit" in control and kind != "numeric":
        raise ValueError("display_unit is only supported on numeric controls")
    requirements = control.get("requires", [])
    if not isinstance(requirements, list) or len(requirements) > 32:
        raise ValueError("requires must be a list of at most 32 conditions")
    selectors = set()
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) != {"control", "one_of"}:
            raise ValueError("Each condition requires exactly control and one_of")
        selector = _text(requirement["control"], "condition control", 100)
        if selector in selectors or selector == control["key"]:
            raise ValueError("Conditions must reference distinct other controls")
        selectors.add(selector)
        labels = requirement["one_of"]
        if not isinstance(labels, list) or not 1 <= len(labels) <= 2048:
            raise ValueError("Condition one_of must contain 1..2048 choice labels")
        for label in labels:
            _text(label, "condition label")
        if len(labels) != len(set(labels)):
            raise ValueError("Condition choice labels must be unique")
    if "curve" in control:
        if kind != "numeric" or not isinstance(control["curve"], dict):
            raise ValueError("curve metadata is only supported on numeric controls")
        curve = control["curve"]
        curve_kind = curve.get("kind")
        if not isinstance(curve_kind, str) or curve_kind not in {
            "linear", "log", "power", "exp", "table",
        }:
            raise ValueError("Unrecognized curve kind")
        fields = {"kind"} if curve_kind == "table" else {"kind", "minimum", "maximum"}
        fields |= {"exponent"} if curve_kind == "power" else {"k"} if curve_kind == "exp" else set()
        if set(curve) != fields:
            raise ValueError("Curve fields do not match its declared kind")
        if curve_kind != "table":
            low = _number(curve["minimum"], "curve minimum")
            high = _number(curve["maximum"], "curve maximum")
            if low == high:
                raise ValueError("Curve endpoints must differ")
            if curve_kind == "log" and min(low, high) <= 0:
                raise ValueError("Log curve endpoints must be positive")
            if curve_kind == "log" and math.log(low) == math.log(high):
                raise ValueError("Log curve endpoints are too close to resolve")
            if curve_kind == "power" and _number(curve["exponent"], "exponent", 0) <= 0:
                raise ValueError("Power exponent must be positive")
            if curve_kind == "exp" and _number(curve["k"], "k") == 0:
                raise ValueError("Exponential k must be nonzero")
    if "points" in control:
        points = control["points"]
        if kind != "numeric" or not isinstance(points, list) or not 2 <= len(points) <= 2048:
            raise ValueError("Numeric points must contain 2..2048 [normalized, physical] pairs")
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Each calibration point must be [normalized, physical]")
            _number(point[0], "normalized calibration point", 0, 1)
            _number(point[1], "physical calibration point")
        if any(b[0] <= a[0] for a, b in zip(points, points[1:])):
            raise ValueError("Calibration normalized positions must strictly increase")
        differences = [b[1] - a[1] for a, b in zip(points, points[1:])]
        if not (all(delta > 0 for delta in differences) or all(delta < 0 for delta in differences)):
            raise ValueError("Physical calibration values must be strictly monotonic")
    if "points" in control or _curve(control) is not None:
        if "unit" not in control or "tolerance" not in control:
            raise ValueError("Calibrated numeric controls require an explicit unit and tolerance")
    if kind == "enum":
        choices = control.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= 2048:
            raise ValueError("Enum controls require 1..2048 choices")
        labels, values, displays = set(), set(), set()
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) not in (
                {"label", "value"}, {"label", "value", "display"},
            ):
                raise ValueError("Enum choice requires label/value and optional literal display")
            label = _text(choice["label"], "choice label")
            if label != label.strip():
                raise ValueError("Choice labels must be trimmed; use display for literal spaces")
            display = choice.get("display", label)
            if not isinstance(display, str) or len(display) > 256 or "\0" in display:
                raise ValueError("Choice display must be literal text of at most 256 characters")
            value = _number(choice["value"], "choice value", 0, 1)
            if label in labels or value in values or display in displays:
                raise ValueError("Enum choice labels, literal displays and values must be unique")
            labels.add(label)
            values.add(value)
            displays.add(display)
    elif "choices" in control:
        raise ValueError("choices are only supported on enum controls")


def _load_registry():
    try:
        with REGISTRY_PATH.open("rb") as stream:
            data = stream.read(MAX_REGISTRY_BYTES + 1)
    except OSError as exc:
        raise RuntimeError("Packaged plugin adapter registry is unavailable") from exc
    if len(data) > MAX_REGISTRY_BYTES:
        raise ValueError("Plugin adapter registry exceeds its size limit")
    try:
        registry = json.loads(
            data, object_pairs_hook=_unique_object,
            parse_constant=_nonfinite,
        )
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid plugin adapter registry JSON") from exc
    if not isinstance(registry, dict) or set(registry) != {"schema_version", "adapters"}:
        raise ValueError("Registry requires exactly schema_version and adapters")
    if type(registry["schema_version"]) is not int or registry["schema_version"] != 1:
        raise ValueError("Unsupported plugin adapter registry schema")
    adapters = registry["adapters"]
    if not isinstance(adapters, list) or len(adapters) > 128:
        raise ValueError("Registry must contain at most 128 adapters")
    ids, bindings = set(), set()
    for adapter in adapters:
        required = {
            "id", "title", "plugin_names", "layout_sha256", "parameter_count",
            "evidence", "controls",
        }
        if not isinstance(adapter, dict) or required - set(adapter) or set(adapter) - (
            required | {"notes", "caution", "cautions"}
        ):
            raise ValueError("Invalid adapter fields")
        _annotations(adapter)
        identifier = _text(adapter["id"], "adapter id", 100)
        _text(adapter["title"], "adapter title")
        if identifier in ids:
            raise ValueError("Adapter ids must be unique")
        ids.add(identifier)
        if not isinstance(adapter["layout_sha256"], str) or not _SHA.fullmatch(
            adapter["layout_sha256"],
        ):
            raise ValueError("Adapter layout_sha256 must be a lowercase SHA-256")
        count = _integer(adapter["parameter_count"], "parameter_count", 0, 10_000)
        if not isinstance(adapter["evidence"], dict) or not adapter["evidence"]:
            raise ValueError("Adapter evidence must be a nonempty object")
        names = adapter["plugin_names"]
        if not isinstance(names, list) or not 1 <= len(names) <= 100:
            raise ValueError("plugin_names must contain 1..100 unique exact names")
        if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError("plugin_names must contain unique strings")
        for name in names:
            _text(name, "native plugin name")
            binding = (name, adapter["layout_sha256"], count)
            if binding in bindings:
                raise ValueError("Ambiguous plugin/layout adapter binding")
            bindings.add(binding)
        controls = adapter["controls"]
        if not isinstance(controls, list) or len(controls) > count:
            raise ValueError("Adapter controls must be a list no longer than parameter_count")
        keys, parameters = set(), set()
        for control in controls:
            _validate_control(control, count)
            if control["key"] in keys or control["parameter"] in parameters:
                raise ValueError("Control keys and parameter references must be unique")
            keys.add(control["key"])
            parameters.add(control["parameter"])
        by_key = {control["key"]: control for control in controls}
        for control in controls:
            for requirement in control.get("requires", []):
                selector = by_key.get(requirement["control"])
                if selector is None or selector["kind"] != "enum":
                    raise ValueError("Conditions must reference an existing enum control")
                labels = {choice["label"] for choice in selector["choices"]}
                if not set(requirement["one_of"]) <= labels:
                    raise ValueError("Condition labels must be declared by the enum selector")
    return registry, hashlib.sha256(data).hexdigest()


def _calibrated_range(control):
    curve = _curve(control)
    if curve:
        values = [curve[key] for key in ("minimum", "maximum")]
    elif "points" in control:
        values = [point[1] for point in control["points"]]
    else:
        return None
    minimum = max(min(values), control.get("minimum", -1e12))
    maximum = min(max(values), control.get("maximum", 1e12))
    if minimum > maximum:
        raise ValueError("Declared limits do not intersect the calibrated range")
    return [minimum, maximum]


def _control_metadata(control):
    result = dict(control)
    result["calibration_status"] = (
        "curve" if _curve(control) else "points" if "points" in control else "calibration_required"
    ) if control["kind"] == "numeric" else "not_applicable"
    calibrated_range = _calibrated_range(control)
    if calibrated_range is not None:
        result["calibrated_range"] = calibrated_range
    return result


def _query(query):
    if not isinstance(query, str) or len(query) > 256 or "\0" in query:
        raise ValueError("query must be text of at most 256 characters without NUL")
    return query.casefold()


def catalog_adapters(query=""):
    """Read the packaged adapter evidence and coverage without contacting FL."""
    needle = _query(query)
    registry, digest = _load_registry()
    adapters = []
    for adapter in registry["adapters"]:
        searchable = [adapter["id"], adapter["title"], *adapter["plugin_names"]]
        searchable.extend(text for c in adapter["controls"] for text in (c["key"], c["name"]))
        if needle not in " ".join(searchable).casefold():
            continue
        adapters.append({
            **adapter, "controls": [_control_metadata(c) for c in adapter["controls"]],
        })
    return {"schema_version": 1, "registry_sha256": digest, "adapters": adapters,
            "matched": len(adapters), "total_adapters": len(registry["adapters"]),
            "limitations": [
                "Exact native plugin name and complete layout must match before use.",
                "Names/layout cannot detect a different plugin build exposing the same controls; "
                "review the recorded version evidence.",
                "Calibration metadata is evidence, not a guarantee of sound or DSP state.",
            ]}


def _snapshot(index, slot_index, connection):
    """Collect one revision-consistent complete named catalog, retaining its full-layout hash."""
    _integer(index, "index", 0, 100_000)
    _integer(slot_index, "slot_index", -1, 9)
    first = None
    parameters = []
    offset = 0
    while True:
        page = plugin_state.inspect_plugin(
            index, slot_index=slot_index, query="", offset=offset, limit=500, connection=connection,
        )
        if first is None:
            first = page
        elif any(page.get(key) != first.get(key) for key in (
            "revision", "layout_sha256", "target", "project", "matched", "transport",
        )):
            raise ValueError("Plugin changed during adapter inspection; inspect again")
        rows = page.get("parameters")
        if not isinstance(rows, list) or page.get("offset") != offset:
            raise RuntimeError("Incomplete plugin inspection page")
        parameters.extend(rows)
        following = page.get("next_offset")
        if following is None:
            break
        if type(following) is not int or following <= offset or following > 10_000:
            raise RuntimeError("Invalid plugin inspection pagination")
        offset = following
    if len(parameters) != first.get("matched"):
        raise RuntimeError("Incomplete named plugin parameter snapshot")
    if len({row["parameter"] for row in parameters}) != len(parameters):
        raise RuntimeError("Duplicate plugin parameter identity")
    return {**first, "parameters": parameters}


def _bind(registry, state):
    target = state["target"]
    matches = [adapter for adapter in registry["adapters"] if (
        target["plugin"] in adapter["plugin_names"]
        and adapter["layout_sha256"] == state["layout_sha256"]
        and adapter["parameter_count"] == target["parameter_count"]
    )]
    if len(matches) != 1:
        raise ValueError("No unique adapter matches the exact native plugin and complete layout")
    adapter = matches[0]
    by_parameter = {row["parameter"]: row for row in state["parameters"]}
    for control in adapter["controls"]:
        row = by_parameter.get(control["parameter"])
        if row is None or row["name"] != control["name"]:
            raise ValueError("Adapter parameter identity does not match the inspected catalog")
    return adapter, by_parameter


def _requirement_status(control, by_key, parameters):
    status = []
    for requirement in control.get("requires", []):
        selector = by_key[requirement["control"]]
        display = parameters[selector["parameter"]]["display"]
        current = next((choice["label"] for choice in selector["choices"]
                        if choice.get("display", choice["label"]) == display), None)
        status.append({**requirement, "current_label": current, "current_display": display,
                       "satisfied": current in requirement["one_of"]})
    return status


def inspect_controls(index, slot_index=-1, query="", offset=0, limit=100, connection=None):
    needle = _query(query)
    _integer(offset, "offset", 0, 10_000)
    _integer(limit, "limit", 1, 500)
    registry, digest = _load_registry()
    state = _snapshot(index, slot_index, connection)
    adapter, parameters = _bind(registry, state)
    by_key = {control["key"]: control for control in adapter["controls"]}
    controls = []
    for control in adapter["controls"]:
        searchable = (control["key"], control["name"], *control.get("notes", []))
        if needle not in " ".join(searchable).casefold():
            continue
        row = parameters[control["parameter"]]
        requirements = _requirement_status(control, by_key, parameters)
        controls.append({**_control_metadata(control), "normalized": row["value"],
                         "display": row["display"],
                         "requirements_met": all(item["satisfied"] for item in requirements),
                         "requirement_status": requirements,
                         "writable_normalized": row["writable_normalized"]})
    return {"success": True, "status": "inspected", "target": state["target"],
            "revision": state["revision"], "layout_sha256": state["layout_sha256"],
            "registry_sha256": digest, "adapter_id": adapter["id"], "evidence": adapter["evidence"],
            "adapter_metadata": {key: value for key, value in adapter.items() if key != "controls"},
            "controls": controls[offset:offset + limit], "matched": len(controls), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(controls) else None,
            "verification_scope": "Host values and literal displays; no sound or DSP guarantee",
            "limitations": [
                "An identical name/layout cannot prove the plugin binary/build version.",
                "Physical writes require a finite current display with a compatible unit. "
                "Use an explicit normalized request to leave an infinity/sentinel endpoint, "
                "then inspect again. Same-unit mode dependencies need declared requirements."]}


def _physical_to_normalized(control, value):
    _number(value, "physical value")
    points = control.get("points")
    curve = _curve(control)
    calibrated_range = _calibrated_range(control)
    if calibrated_range is None:
        raise ValueError(f"{control['key']}: calibration_required; use explicit normalized only")
    minimum, maximum = calibrated_range
    if not minimum <= value <= maximum:
        raise ValueError(f"Physical value is outside calibrated range {minimum}..{maximum}")
    if curve is not None:
        low, high = curve["minimum"], curve["maximum"]
        ratio = (value - low) / (high - low)
        if value == low:
            return 0.0
        if value == high:
            return 1.0
        kind = curve["kind"]
        if kind == "linear":
            normalized = ratio
        elif kind == "log":
            normalized = (math.log(value) - math.log(low)) / (math.log(high) - math.log(low))
        elif kind == "power":
            normalized = math.exp(math.log(ratio) / curve["exponent"])
        else:
            k = curve["k"]
            if abs(k) < 1e-8:
                # Limit expansion avoids subnormal expm1 multiplication underflow.
                normalized = ratio + 0.5 * k * ratio * (1 - ratio)
            else:
                normalized = (
                    (k + math.log(ratio + (1 - ratio) * math.exp(-k))) / k if k > 50
                    else math.log1p(ratio * math.expm1(k)) / k
                )
        return _number(normalized, "inverse curve result", 0, 1)
    for left, right in zip(points, points[1:]):
        if min(left[1], right[1]) <= value <= max(left[1], right[1]):
            return left[0] + (right[0] - left[0]) * (value - left[1]) / (right[1] - left[1])
    raise ValueError("No calibrated segment for physical value")


def compile_changes(adapter, changes):
    """Compile validated-registry controls to explicit guarded native changes."""
    if not isinstance(changes, list) or not 1 <= len(changes) <= 256:
        raise ValueError("changes must contain 1..256 named controls")
    controls = {control["key"]: control for control in adapter["controls"]}
    seen = set()
    compiled = []
    for change in changes:
        if not isinstance(change, dict) or set(change) not in (
            {"control", "value"}, {"control", "normalized"},
        ):
            raise ValueError("Each change needs control and exactly one of value or normalized")
        key = _text(change["control"], "control key", 100)
        if key in seen or key not in controls:
            raise ValueError("Control keys must exist and be distinct")
        seen.add(key)
        control = controls[key]
        result = {"parameter": control["parameter"], "expected_name": control["name"]}
        if "normalized" in change:
            result["value"] = _number(change["normalized"], "normalized value", 0, 1)
        elif control["kind"] == "numeric":
            value = change["value"]
            result["value"] = _physical_to_normalized(control, value)
            result["expected_numeric"] = {
                "value": value, "unit": control.get("display_unit", control["unit"]),
                "tolerance": control["tolerance"],
            }
        elif control["kind"] == "enum":
            value = change["value"]
            choices = [choice for choice in control["choices"] if choice["label"] == value]
            if not isinstance(value, str) or len(choices) != 1:
                raise ValueError("Enum value must be an exact declared choice label")
            result.update(value=choices[0]["value"],
                          expected_display=choices[0].get("display", value))
        else:
            raise ValueError("Normalized controls require the explicit normalized field")
        compiled.append(result)
    return compiled


def _compact_write_result(result, parameters):
    """Keep requested observations in MCP output; the receipt retains full snapshots."""
    def compact(state):
        if not isinstance(state, dict) or not isinstance(state.get("parameters"), list):
            return state
        rows = state["parameters"]
        return {
            **state,
            "parameters": [row for row in rows if row.get("parameter") in parameters],
            "parameters_total": len(rows),
            "parameters_scope": "requested_controls",
        }

    output = dict(result)
    if "before" in output:
        output["before"] = compact(output["before"])
    if "readbacks" in output:
        output["readbacks"] = [compact(state) for state in output["readbacks"]]
    return output


def set_controls(
    index, expected_plugin, expected_revision, changes, slot_index=-1,
    apply=False, receipt_file=None, connection=None,
):
    _text(expected_plugin, "expected_plugin")
    if not isinstance(expected_revision, str) or not _SHA.fullmatch(expected_revision):
        raise ValueError("Use the fresh adapter inspection revision")
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if apply and receipt_file is None:
        raise ValueError("Apply requires a new absolute JSON receipt_file")
    registry, digest = _load_registry()
    state = _snapshot(index, slot_index, connection)
    if state["target"]["plugin"] != expected_plugin or state["revision"] != expected_revision:
        raise ValueError("Plugin identity or revision changed; inspect again")
    adapter, parameters = _bind(registry, state)
    compiled = compile_changes(adapter, changes)
    by_key = {control["key"]: control for control in adapter["controls"]}
    changing = {change["control"] for change in changes}
    for key in changing:
        for status in _requirement_status(by_key[key], by_key, parameters):
            if status["control"] in changing:
                raise ValueError("Change a condition selector separately, then inspect again")
            if not status["satisfied"]:
                raise ValueError(f"Control {key} is unavailable for its current condition selector")
    if any(not parameters[change["parameter"]]["writable_normalized"] for change in compiled):
        raise ValueError("A selected parameter has no writable normalized baseline")
    for change in compiled:
        if "expected_numeric" in change:
            unit = change["expected_numeric"]["unit"]
            try:
                parse_display_number(parameters[change["parameter"]]["display"], unit)
            except ValueError as exc:
                raise ValueError(
                    f"{change['expected_name']}: current display cannot establish the calibrated "
                    f"unit {unit!r}: {exc}. Inspect the mode or use an explicit normalized "
                    "request to leave an infinity/sentinel endpoint, then inspect again."
                ) from exc
    result = plugin_state.apply_plugin(
        index, expected_plugin, expected_revision, compiled, slot_index=slot_index,
        apply=apply, receipt_file=receipt_file, connection=connection,
    )
    result = _compact_write_result(result, {change["parameter"] for change in compiled})
    return {**result, "adapter_id": adapter["id"], "registry_sha256": digest,
            "adapter_metadata": {key: value for key, value in adapter.items() if key != "controls"},
            "adapter_changes": changes,
            "adapter_verification_scope": (
                "Physical requests require displayed numeric value/unit/tolerance checks; "
                "enum requests require exact display labels. Explicit normalized requests only "
                "verify host normalized values. None establishes DSP state or sound quality."
            )}
