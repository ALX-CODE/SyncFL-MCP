"""Guarded generator/effect parameter batches using the deployed native bridge.

Receipts and subsequent callbacks verify host values, not a plugin's audio or
the correctness of a potentially stale host cache. No automatic retry/rollback.
"""

import hashlib
import json
import math
import os
import time
import uuid
from decimal import Decimal
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.plugin_display import canonical_display_unit, parse_display_number
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

TOLERANCE = 0.00001
MAX_PARAMETERS = 10000
# FL exposes its MIDI-CC mapping as numbered pseudo-parameters ("MIDI CC #0 ...").
# They have no display text and are not the plugin's own controls, so they are
# excluded from parameter pages unless a caller asks for them.
MIDI_CC_PREFIX = "MIDI CC #"
LIMITATIONS = [
    "Normalized host values and literal plugin displays have no assumed dB/ms/Hz mapping.",
    "Plugins may quantize normalized requests. Only a caller-declared normalized_tolerance "
    "can relax the default; tolerances are never inferred or widened after a write. "
    "An accepted difference does not establish the plugin's step size or physical units.",
    "Host caches can be stale after native preset loading. Host readback does not prove DSP state; "
    "use expected_display or expected_numeric when known and audition or measure a render.",
    "Numeric display checks parse literal host text, converting only ms/s or Hz/kHz. "
    "They do not derive a normalized-to-physical mapping or prove DSP state.",
    "Names/catalogs and project generations cannot detect every same-name plugin replacement.",
    "The shared GUI lock coordinates participating clients, not manual UI or legacy clients. "
    "Project/identity guards occur before the mutation callback, not atomically inside it.",
]
_SIGNATURES = {
    "plugins.isValid": ["index", "slotIndex", "useGlobalIndex"],
    "plugins.getPluginName": ["index", "slotIndex", "userName", "useGlobalIndex"],
    "plugins.getParamCount": ["index", "slotIndex", "useGlobalIndex"],
    "plugins.getParamName": ["paramIndex", "index", "slotIndex", "useGlobalIndex"],
    "plugins.getParamValue": ["paramIndex", "index", "slotIndex", "useGlobalIndex"],
    "plugins.getParamValueString": ["paramIndex", "index", "slotIndex", "useGlobalIndex"],
    "plugins.setParamValue": [
        "value",
        "paramIndex",
        "index",
        "slotIndex",
        "pickupMode",
        "useGlobalIndex",
    ],
    "channels.getChannelName": ["index", "useGlobalIndex"],
    "mixer.getTrackName": ["index"],
    "mixer.getTrackInfo": ["mode"],
}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _call(conn, action, params):
    response = conn.send_command(action, params, 30)
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError(f"Plugin {action} failed: {response}")
    return response


def _native(conn, specs):
    result = _call(
        conn,
        "bridge.batch",
        {
            "steps": [
                dict(action="native.call", params=dict(function=name, args=args))
                for name, args in specs
            ],
            "dry_run": False,
        },
    )
    rows = result.get("results")
    if (
        not isinstance(rows, list)
        or len(rows) != len(specs)
        or any(
            not isinstance(row, dict)
            or "value" not in row
            or row.get("success") is False
            or row.get("error")
            for row in rows
        )
    ):
        raise RuntimeError("Incomplete native plugin readback")
    return [row["value"] for row in rows]


def _capabilities(conn, slot, writing=False):
    native = _call(conn, "bridge.capabilities", {}).get("native", {})
    if not isinstance(native, dict):
        raise RuntimeError("Controller omits its native capability catalog")
    needed = set(_SIGNATURES) - {"plugins.setParamValue"}
    needed -= (
        {"mixer.getTrackInfo", "mixer.getTrackName"} if slot == -1 else {"channels.getChannelName"}
    )
    if writing:
        needed.add("plugins.setParamValue")
    for name in needed:
        spec = native.get(name, {})
        expected_kind = "write" if name == "plugins.setParamValue" else "read"
        if (
            not isinstance(spec, dict)
            or spec.get("arguments") != _SIGNATURES[name]
            or spec.get("kind") != expected_kind
        ):
            raise RuntimeError(f"Missing or unsupported advertised native signature: {name}")


def _target(index, slot_index):
    if type(index) is not int or not 0 <= index <= 100000:
        raise ValueError("index must be a nonnegative global channel or mixer index")
    if type(slot_index) is not int or not -1 <= slot_index <= 9:
        raise ValueError("slot_index must be -1 for a generator or 0..9 for an effect")


def _project(conn):
    response = _call(conn, "production.inspect", {"scope": "project"})
    context = response.get("context")
    if not isinstance(context, dict) or not isinstance(context.get("session"), str):
        raise RuntimeError("Controller omits the project session identity")
    if not context["session"] or any(
        type(context.get(key)) is not int or context[key] < 0
        for key in ("project_generation", "channel_generation")
    ):
        raise RuntimeError("Controller omits project/channel generation identity")
    channels = context.get("channels")
    if not isinstance(channels, list) or len(channels) > 100000:
        raise RuntimeError("Invalid global channel catalog")
    for index, channel in enumerate(channels):
        if (
            not isinstance(channel, dict)
            or type(channel.get("index")) is not int
            or channel["index"] != index
            or not isinstance(channel.get("name"), str)
        ):
            raise RuntimeError("Incomplete or ambiguous global channel identity")
    transport = {key: response.get(key) for key in ("is_playing", "is_recording")}
    if any(type(value) is not bool for value in transport.values()):
        raise RuntimeError("Incomplete transport state")
    project = {
        key: context.get(key)
        for key in (
            "session",
            "project_generation",
            "channel_generation",
            "project_title",
            "channels",
        )
    }
    # Dirty-marker titles and selected patterns can legitimately change after writes.
    return project, transport


def _identity(conn, index, slot, project):
    if slot == -1:
        if index >= len(project["channels"]):
            raise ValueError("Global channel does not exist")
        name_call = ("channels.getChannelName", [index, True])
    else:
        maximum = _native(conn, [("mixer.getTrackInfo", [2])])[0]
        if type(maximum) is not int or not 0 <= index <= maximum:
            raise ValueError("Mixer track does not exist")
        name_call = ("mixer.getTrackName", [index])
    values = _native(
        conn,
        [
            ("plugins.isValid", [index, slot, True]),
            ("plugins.getPluginName", [index, slot, False, True]),
            ("plugins.getPluginName", [index, slot, True, True]),
            ("plugins.getParamCount", [index, slot, True]),
            name_call,
        ],
    )
    valid, plugin, user_name, count, name = values
    # FL's native isValid returns integer 1 on the qualified FL26 host, despite
    # its bool annotation. Do not extend this coercion to strings/floats or the
    # controller's explicitly bool-normalized transport fields.
    if not (valid is True or (type(valid) is int and valid == 1)):
        raise ValueError("No valid plugin at the exact target")
    if not isinstance(plugin, str) or not plugin.strip():
        raise RuntimeError("Plugin identity is empty or ambiguous")
    if not isinstance(name, str) or not isinstance(user_name, str):
        raise RuntimeError("Target name is unavailable")
    if type(count) is not int or not 0 <= count <= MAX_PARAMETERS:
        raise RuntimeError(f"Parameter catalog must contain 0..{MAX_PARAMETERS} entries")
    if slot == -1 and name != project["channels"][index]["name"]:
        raise RuntimeError("Global channel identity changed during inspection")
    return dict(
        index=index,
        slot_index=slot,
        kind="generator" if slot == -1 else "effect",
        name=name,
        plugin=plugin,
        user_name=user_name,
        parameter_count=count,
    )


def _read_state(conn, index, slot):
    project, transport = _project(conn)
    identity = _identity(conn, index, slot, project)
    # Unlike legacy getName, this handler uses the correct four-argument global
    # parameter getters and reads the complete catalog in one controller callback.
    rows = _call(
        conn,
        "plugins.getParams",
        {
            "index": index,
            "slot_index": slot,
            "use_global": True,
            "max_params": identity["parameter_count"],
        },
    ).get("params")
    if not isinstance(rows, list) or len(rows) != identity["parameter_count"]:
        raise RuntimeError("Incomplete plugin parameter catalog")
    parameters = []
    for number, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or type(row.get("index")) is not int
            or row["index"] != number
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("value_string"), str)
        ):
            raise RuntimeError("Incomplete or ambiguous parameter identity/layout")
        value = row.get("value")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise RuntimeError("Nonfinite or invalid plugin parameter value")
        parameters.append(
            dict(
                parameter=number,
                name=row["name"],
                value=value,
                display=row["value_string"],
                writable_normalized=bool(row["name"].strip()) and 0 <= value <= 1,
            )
        )
    if _identity(conn, index, slot, project) != identity:
        raise RuntimeError("Plugin identity changed while reading parameters")
    after_project, after_transport = _project(conn)
    if after_project != project or after_transport != transport:
        raise RuntimeError("Project or transport changed while reading plugin state")
    state = dict(project=project, target=identity, parameters=parameters)
    return dict(
        **state,
        revision=_digest(state),
        transport=transport,
        layout_sha256=_digest([(p["parameter"], p["name"]) for p in parameters]),
    )


def _visible_parameters(parameters):
    """Split the native catalog into real plugin controls and MIDI-CC stubs."""
    real, cc = [], 0
    for p in parameters:
        if p["name"].strip().startswith(MIDI_CC_PREFIX):
            cc += 1
        else:
            real.append(p)
    return real, cc


def _compact_project(project):
    """Project identity without the full channel roster.

    The roster is what the controller legitimately returns for identity checks, but
    repeating every channel name in every plugin response made the smallest useful
    inspection cost several kilobytes. Callers that need the roster can ask
    fl_production_inspect(scope='project') or pass include_project=True.
    """
    if not isinstance(project, dict):
        return project
    channels = project.get("channels")
    return dict(
        session=project.get("session"),
        project_generation=project.get("project_generation"),
        channel_generation=project.get("channel_generation"),
        project_title=project.get("project_title"),
        channel_count=len(channels) if isinstance(channels, list) else None,
    )


def inspect_plugin(
    index,
    slot_index=-1,
    query="",
    offset=0,
    limit=100,
    connection=None,
    include_project=False,
    include_midi_cc=False,
):
    """Return a compact page, with a revision covering the complete target state."""
    _target(index, slot_index)
    if not isinstance(query, str) or len(query) > 256:
        raise ValueError("query must be text of at most 256 characters")
    if type(offset) is not int or not 0 <= offset <= MAX_PARAMETERS:
        raise ValueError(f"offset must be 0..{MAX_PARAMETERS}")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    if type(include_project) is not bool or type(include_midi_cc) is not bool:
        raise ValueError("include_project and include_midi_cc must be boolean")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        _capabilities(conn, slot_index)
        state = _read_state(conn, index, slot_index)
    exposed, midi_cc_count = _visible_parameters(state["parameters"])
    if not include_midi_cc:
        matches = [
            p for p in exposed if query.casefold() in p["name"].casefold()
        ]
    else:
        matches = [
            p
            for p in state["parameters"]
            if p["name"].strip() and query.casefold() in p["name"].casefold()
        ]
    state["parameters"] = matches[offset : offset + limit]
    state["target"] = dict(
        state["target"],
        parameter_count_exposed=len(exposed),
        parameter_count_midi_cc=midi_cc_count,
    )
    if not include_project:
        state["project"] = _compact_project(state["project"])
    return dict(
        **state,
        success=True,
        status="inspected",
        matched=len(matches),
        offset=offset,
        next_offset=offset + limit if offset + limit < len(matches) else None,
        blank_parameters_omitted=True,
        midi_cc_omitted=not include_midi_cc,
        revision_scope="complete target catalog, values and displays",
        limitations=list(LIMITATIONS),
    )


def _validate_changes(changes, expected_plugin, expected_revision, apply, receipt_file):
    if not isinstance(expected_plugin, str) or not expected_plugin.strip():
        raise ValueError("Exact nonempty expected_plugin is required")
    if (
        not isinstance(expected_revision, str)
        or len(expected_revision) != 64
        or any(c not in "0123456789abcdef" for c in expected_revision)
    ):
        raise ValueError("Use the revision returned by fl_plugin_inspect")
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 256:
        raise ValueError("changes must contain 1..256 indexed parameters")
    seen = set()
    for change in changes:
        if (
            not isinstance(change, dict)
            or {"parameter", "expected_name", "value"} - change.keys()
            or set(change)
            - {
                "parameter", "expected_name", "value", "expected_display", "normalized_tolerance",
                "expected_numeric",
            }
        ):
            raise ValueError("Each change needs parameter, expected_name and normalized value")
        parameter = change["parameter"]
        if type(parameter) is not int or not 0 <= parameter < MAX_PARAMETERS or parameter in seen:
            raise ValueError("Parameter indices must be unique nonnegative integers")
        seen.add(parameter)
        if not isinstance(change["expected_name"], str) or not change["expected_name"].strip():
            raise ValueError("Exact nonempty expected_name is required")
        value = change["value"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Parameter value must be finite and normalized in 0..1")
        if "expected_display" in change and not isinstance(change["expected_display"], str):
            raise ValueError("expected_display must be an exact post-write display string")
        if "expected_numeric" in change:
            numeric = change["expected_numeric"]
            if not isinstance(numeric, dict) or set(numeric) != {"value", "unit", "tolerance"}:
                raise ValueError("expected_numeric needs exactly value, unit and tolerance")
            for field in ("value", "tolerance"):
                candidate = numeric[field]
                try:
                    finite = type(candidate) in (int, float) and math.isfinite(candidate)
                except OverflowError:
                    finite = False
                if not finite or (field == "tolerance" and candidate < 0):
                    raise ValueError(
                        "expected_numeric needs a finite real value and "
                        "finite nonnegative tolerance"
                    )
            canonical_display_unit(numeric["unit"])
        tolerance = change.get("normalized_tolerance", TOLERANCE)
        if (
            type(tolerance) not in (int, float)
            or not math.isfinite(tolerance)
            or not 0 <= tolerance <= 0.01
        ):
            raise ValueError("normalized_tolerance must be a finite number in 0..0.01")
    path = Path(receipt_file) if receipt_file else None
    if apply and path is None:
        raise ValueError("Apply requires a new absolute JSON receipt_file")
    if path is not None and (not path.is_absolute() or path.suffix.lower() != ".json"):
        raise ValueError("receipt_file must be an absolute JSON path")
    return path


def _stopped(state):
    if any(state["transport"].values()):
        raise ValueError("Transport must be stopped and recording off")


def _same_identity(before, after):
    return all(before[key] == after[key] for key in ("project", "target", "layout_sha256"))


def _change_results(after, changes):
    results = []
    for change in changes:
        row = after["parameters"][change["parameter"]]
        delta = row["value"] - change["value"]
        tolerance = change["normalized_tolerance"]
        normalized_matches = abs(delta) <= tolerance
        display_matches = (
            row["display"] == change["expected_display"] if "expected_display" in change else None
        )
        check = dict(
            parameter=change["parameter"],
            expected_name=change["expected_name"],
            requested_value=change["value"],
            actual_value=row["value"],
            delta=delta,
            normalized_tolerance=tolerance,
            normalized_matches=normalized_matches,
            actual_display=row["display"],
            expected_display=change.get("expected_display"),
            display_matches=display_matches,
            matches=normalized_matches and display_matches is not False,
        )
        if "expected_numeric" in change:
            expected = change["expected_numeric"]
            actual, numeric_delta, numeric_error = None, None, None
            numeric_matches = False
            try:
                actual = parse_display_number(row["display"], expected["unit"])
                # Compare the decimal values without inventing a tolerance epsilon.
                difference = Decimal(str(actual)) - Decimal(str(expected["value"]))
                numeric_delta = float(difference)
                if not math.isfinite(numeric_delta):
                    numeric_delta = None
                    raise ValueError(
                        "Numeric display difference is outside the finite number range"
                    )
                numeric_matches = abs(difference) <= Decimal(str(expected["tolerance"]))
            except ValueError as exc:
                numeric_error = str(exc)
            check.update(
                expected_numeric=dict(expected),
                actual_numeric=actual,
                numeric_delta=numeric_delta,
                numeric_matches=numeric_matches,
                numeric_error=numeric_error,
                matches=check["matches"] and numeric_matches,
            )
        results.append(check)
    return results


def apply_plugin(
    index,
    expected_plugin,
    expected_revision,
    changes,
    slot_index=-1,
    apply=False,
    receipt_file=None,
    connection=None,
):
    """Dispatch one preflighted batch; persist evidence and verify later callbacks."""
    _target(index, slot_index)
    path = _validate_changes(changes, expected_plugin, expected_revision, apply, receipt_file)
    changes = [
        {**change, "normalized_tolerance": change.get("normalized_tolerance", TOLERANCE)}
        for change in changes
    ]
    for change in changes:
        if "expected_numeric" in change:
            change["expected_numeric"] = dict(change["expected_numeric"])
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        _capabilities(conn, slot_index, writing=True)
        before = _read_state(conn, index, slot_index)
        _stopped(before)
        if before["revision"] != expected_revision:
            raise ValueError("Plugin revision changed; inspect again")
        if before["target"]["plugin"] != expected_plugin:
            raise ValueError("Plugin identity does not match expected_plugin")
        previous = []
        for change in changes:
            number = change["parameter"]
            if number >= len(before["parameters"]):
                raise ValueError("Parameter does not exist")
            row = before["parameters"][number]
            if row["name"] != change["expected_name"]:
                raise ValueError("Parameter identity does not match expected_name")
            if not row["writable_normalized"]:
                raise ValueError("Parameter does not expose a named normalized baseline")
            previous.append(
                dict(
                    parameter=number,
                    expected_name=row["name"],
                    value=row["value"],
                    display=row["display"],
                )
            )
        steps = [
            dict(
                action="native.call",
                params=dict(
                    function="plugins.setParamValue",
                    allow_write=True,
                    args=[change["value"], change["parameter"], index, slot_index, 0, True],
                ),
            )
            for change in changes
        ]
        preview = _call(conn, "bridge.batch", {"steps": steps, "dry_run": True})
        if preview.get("validated") != len(steps) or preview.get("applied") != 0:
            raise RuntimeError("Controller did not confirm complete plugin batch preflight")
        report = dict(
            success=not apply,
            status="preflighted",
            dry_run=not apply,
            operation_id=uuid.uuid4().hex,
            before=before,
            changes=changes,
            previous_host_values=previous,
            steps=steps,
            tolerance=TOLERANCE,
            tolerance_scope="default; each change records its effective normalized_tolerance",
            verification_scope="host normalized values and optional exact or numeric "
            "display checks",
            limitations=list(LIMITATIONS),
            recovery="Inspect the current plugin and receipt before any further edit. Never replay "
            "an uncertain batch. Prior host values can be stale and are not an automatic restore.",
        )
        if not apply:
            return report
        report.update(success=False, receipt_file=str(path))
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        dispatched = False
        try:
            current = _read_state(conn, index, slot_index)
            _stopped(current)
            if current["revision"] != before["revision"]:
                raise RuntimeError("Plugin changed after preflight; no mutation dispatched")
            report["status"] = "dispatching"
            atomic_json(path, report)
            dispatched = True
            response = conn.send_command("bridge.batch", {"steps": steps, "dry_run": False}, 30)
            report.update(response=response, status="awaiting_readback")
            atomic_json(path, report)
            results = response.get("results") if isinstance(response, dict) else None
            acknowledged = (
                isinstance(response, dict)
                and response.get("success") is True
                and response.get("applied") == len(steps)
                and isinstance(results, list)
                and len(results) == len(steps)
                and all(
                    isinstance(result, dict)
                    and "value" in result
                    and result.get("success") is not False
                    and not result.get("error")
                    for result in results
                )
            )
            consecutive = 0
            report["readbacks"] = []
            report["readback_verification"] = []
            # The host display cache may lag a successful parameter write. This
            # bounded pause improves readback timing; it does not establish DSP state.
            time.sleep(0.4)
            for attempt in range(5):
                after = _read_state(conn, index, slot_index)
                report["readbacks"].append(after)
                if not _same_identity(before, after):
                    report.update(
                        status="unknown", error="Plugin/project identity or layout changed"
                    )
                    break
                _stopped(after)
                checks = _change_results(after, changes)
                report["readback_verification"].append(
                    dict(readback_index=attempt, changes=checks)
                )
                consecutive = consecutive + 1 if all(row["matches"] for row in checks) else 0
                if consecutive >= 2:
                    report.update(
                        success=acknowledged,
                        status="verified" if acknowledged else "observed_after_error",
                    )
                    break
                if attempt < 4:
                    time.sleep(0.2)
            else:
                report.update(
                    status="verification_failed" if acknowledged else "unknown",
                    error="Independent host readback did not match the requested values",
                )
        except Exception as exc:
            report.update(
                success=False, status="unknown" if dispatched else "not_applied", error=str(exc)
            )
        try:
            atomic_json(path, report)
        except Exception as exc:
            report.update(
                success=False,
                status="unknown" if dispatched else "not_applied",
                receipt_persistence_error=str(exc),
            )
        return report
