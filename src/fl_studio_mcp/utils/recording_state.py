"""Recording metadata and guarded desired arm state; never starts a recording.

FL exposes armTrack as a toggle, so a write is conditional, dispatched once and
independently observed. The cooperative GUI lock cannot lock the user or legacy
clients across native callbacks. Filenames can be time-dependent proposals,
not existing files or verified takes, and never form part of the arm revision.
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

_READS = {
    "mixer.getTrackInfo": ["mode"],
    "mixer.getTrackName": ["index"],
    "mixer.isTrackArmed": ["index"],
}
_OPTIONAL = {"mixer.getTrackRecordingFileName": ["index"], "ui.isStartOnInputEnabled": []}
LIMITATIONS = [
    "Arming does not start recording or prove an input, monitoring path, latency or take.",
    "Recording filenames are native reported/proposed paths that may change with time; "
    "file existence and recorded takes are not verified.",
    "Input device/channel, buffer size, monitoring and recording/PDC latency are unknown.",
    "The GUI lock coordinates participating clients; users and legacy clients can interleave.",
    "Same-name track replacement within one project is not always detectable by the native API.",
]


def _call(conn, action, params):
    result = conn.send_command(action, params, 30)
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Recording {action} failed: {result}")
    return result


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _check_revision(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Use the target track's revision from fl_recording_inspect")


def _supported(native, name, expected_arguments, kind="read"):
    spec = native.get(name)
    if not isinstance(spec, dict):
        return False
    module, function = name.split(".")
    arguments, required = spec.get("arguments"), spec.get("required")
    return (
        spec.get("module") == module
        and spec.get("function") == function
        and spec.get("kind") == kind
        and arguments == expected_arguments
        and type(required) is int
        and required == len(expected_arguments)
    )


def _capabilities(conn, require_write=False):
    response = _call(conn, "bridge.capabilities", {})
    native = response.get("native")
    if not isinstance(native, dict) or "bridge.batch" not in response.get("typed", []):
        raise RuntimeError("Controller lacks native catalog or typed batch support")
    missing = [name for name, args in _READS.items() if not _supported(native, name, args)]
    arm_supported = _supported(native, "mixer.armTrack", ["index"], "write")
    if require_write and not arm_supported:
        missing.append("mixer.armTrack")
    if missing:
        raise RuntimeError("Recording API capability missing/incompatible: " + ", ".join(missing))
    return dict(
        desired_arm_supported=arm_supported,
        recording_filename_supported=_supported(
            native, "mixer.getTrackRecordingFileName", ["index"]
        ),
        start_on_input_supported=_supported(native, "ui.isStartOnInputEnabled", []),
        signatures={
            name: native[name]["signature"]
            for name in (*_READS, *_OPTIONAL, "mixer.armTrack")
            if isinstance(native.get(name), dict) and "signature" in native[name]
        },
    )


def _read_native(conn, calls):
    values = []
    for start in range(0, len(calls), 255):
        chunk = calls[start : start + 255]
        response = _call(
            conn,
            "bridge.batch",
            dict(
                dry_run=False,
                steps=[
                    dict(action="native.call", params=dict(function=name, args=args))
                    for name, args in chunk
                ],
            ),
        )
        results = response.get("results")
        if (
            response.get("applied") != len(chunk)
            or not isinstance(results, list)
            or len(results) != len(chunk)
            or any(
                not isinstance(r, dict)
                or "value" not in r
                or r.get("success") is False
                or r.get("error")
                for r in results
            )
        ):
            raise RuntimeError("Incomplete recording native readback")
        values.extend(r["value"] for r in results)
    return values


def _boolean(value, label):
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise RuntimeError(f"Invalid {label} readback")


def _transport(project):
    values = {key: project.get(key) for key in ("is_playing", "is_recording")}
    if any(type(value) is not bool for value in values.values()):
        raise RuntimeError("Incomplete transport readback")
    return values


def _stopped(state):
    if any(state["transport"].values()):
        raise ValueError("Stop playback and disable recording before changing track arm state")


def _indices(tracks):
    if tracks is None:
        return None
    if (
        not isinstance(tracks, list)
        or not 1 <= len(tracks) <= 256
        or any(type(t) is not int or t < 0 for t in tracks)
        or len(set(tracks)) != len(tracks)
    ):
        raise ValueError("tracks must contain 1..256 unique nonnegative integer indices")
    return sorted(tracks)


def _read_state(conn, tracks, capabilities):
    before = _call(conn, "production.inspect", {"scope": "project"})
    _check_revision(before.get("revision"))
    maximum = _read_native(conn, [("mixer.getTrackInfo", [2])])[0]
    if type(maximum) is not int or not 0 <= maximum <= 1023:
        raise RuntimeError("Invalid mixer track extent")
    selected = list(range(maximum + 1)) if tracks is None else tracks
    if any(track > maximum for track in selected):
        raise ValueError("Requested mixer track does not exist")
    filename_supported = capabilities["recording_filename_supported"]
    calls = []
    for track in selected:
        calls.extend([("mixer.getTrackName", [track]), ("mixer.isTrackArmed", [track])])
        if filename_supported:
            calls.append(("mixer.getTrackRecordingFileName", [track]))
    values = _read_native(conn, calls)
    rows = []
    width = 3 if filename_supported else 2
    for i, track in enumerate(selected):
        name, armed = values[i * width : i * width + 2]
        if not isinstance(name, str):
            raise RuntimeError("Invalid mixer track name")
        filename = values[i * width + 2] if filename_supported else None
        if filename_supported and not isinstance(filename, str):
            raise RuntimeError("Invalid recording filename readback")
        rows.append(
            dict(
                track=track,
                name=name,
                armed=_boolean(armed, "track arm state"),
                recording_filename=filename,
                recording_filename_status="reported_proposed"
                if filename_supported
                else "unsupported",
            )
        )
    start_on_input = None
    if capabilities["start_on_input_supported"]:
        start_on_input = _boolean(
            _read_native(conn, [("ui.isStartOnInputEnabled", [])])[0], "start-on-input state"
        )
    after = _call(conn, "production.inspect", {"scope": "project"})
    transport = _transport(after)
    if (
        before["revision"] != after.get("revision")
        or before.get("context") != after.get("context")
        or not isinstance(before.get("context"), dict)
    ):
        raise RuntimeError("Project changed during recording inspection; inspect again")
    if _transport(before) != transport:
        raise RuntimeError("Transport changed during recording inspection; inspect again")
    base = dict(
        project_revision=before["revision"],
        project=before["context"],
        track_count=maximum + 1,
        start_on_input=start_on_input,
    )
    for row in rows:
        # Live FL can generate a new timestamped proposal even for an unarmed
        # track on every filename read. Only stable arm-target state is guarded.
        target = {key: row[key] for key in ("track", "name", "armed")}
        row["revision"] = _digest(dict(**base, target=target))
    return dict(
        success=True,
        status="inspected",
        **base,
        transport=transport,
        tracks=rows,
        capabilities=capabilities,
    )


def inspect_recording(tracks=None, connection=None):
    """Read requested tracks, or compact armed tracks; filenames never prove a take."""
    tracks = _indices(tracks)
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        state = _read_state(conn, tracks, _capabilities(conn))
        state["inspected_track_count"] = len(state["tracks"])
        state["armed_tracks"] = [t["track"] for t in state["tracks"] if t["armed"]]
        state["scope"] = "requested_tracks" if tracks is not None else "armed_tracks"
        if tracks is None:
            state["tracks"] = [t for t in state["tracks"] if t["armed"]]
        state["unknown"] = dict(
            input_device=None,
            input_channel=None,
            monitoring=None,
            buffer_size=None,
            recording_latency=None,
            pdc=None,
        )
        state["recorded_take_verified"] = False
        state["limitations"] = list(LIMITATIONS)
        return state


def _identity_matches(before, after):
    keys = ("project_revision", "project", "track_count", "start_on_input")
    return all(before[key] == after[key] for key in keys) and all(
        before["tracks"][0][key] == after["tracks"][0][key] for key in ("track", "name")
    )


def set_recording_arm(
    track, expected_name, expected_revision, armed, apply=False, receipt_file=None, connection=None
):
    """Guard an explicit arm state around FL's native toggle; never retries or records."""
    _indices([track])
    _check_revision(expected_revision)
    if not isinstance(expected_name, str) or not expected_name:
        raise ValueError("An exact nonempty expected_name is required")
    if type(armed) is not bool or type(apply) is not bool:
        raise ValueError("armed and apply must be booleans")
    path = Path(receipt_file) if receipt_file else None
    if apply and path is None:
        raise ValueError("Apply requires a new absolute JSON receipt_file")
    if path is not None and (not path.is_absolute() or path.suffix.lower() != ".json"):
        raise ValueError("receipt_file must be an absolute JSON path")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        capabilities = _capabilities(conn, require_write=True)
        before = _read_state(conn, [track], capabilities)
        _stopped(before)
        target = before["tracks"][0]
        if target["name"] != expected_name:
            raise ValueError("Mixer track identity changed; inspect again")
        if target["revision"] != expected_revision:
            raise ValueError("Recording arm revision changed; inspect again")
        needs_toggle = target["armed"] != armed
        params = dict(function="mixer.armTrack", args=[track], allow_write=True)
        if needs_toggle:
            preview = _call(
                conn,
                "bridge.batch",
                dict(dry_run=True, steps=[dict(action="native.call", params=params)]),
            )
            if preview.get("validated") != 1 or preview.get("applied") != 0:
                raise RuntimeError("Controller did not confirm arm preflight")
        report = dict(
            success=not apply,
            status="preflighted",
            dry_run=not apply,
            operation_id=uuid.uuid4().hex,
            before=before,
            target=dict(track=track, expected_name=expected_name, armed=armed),
            needs_toggle=needs_toggle,
            changed=False,
            restore_state=dict(track=track, expected_name=expected_name, armed=target["armed"]),
            recorded_take_verified=False,
            limitations=list(LIMITATIONS),
            recovery="Inspect the target track again before recovery; use its fresh revision. "
            "Do not replay an uncertain toggle. No automatic restore is performed.",
        )
        if not apply:
            return report
        report["receipt_file"] = str(path)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        dispatched = False
        try:
            current = _read_state(conn, [track], capabilities)
            _stopped(current)
            if current["tracks"][0]["revision"] != expected_revision:
                raise RuntimeError("Arm target changed after preflight; no toggle dispatched")
            if not needs_toggle:
                report.update(success=True, status="already_satisfied", after=current)
            else:
                report.update(status="dispatching", changed=None)
                atomic_json(path, report)
                dispatched = True
                response = conn.send_command("native.call", params, 10)
                report.update(response=response, status="awaiting_readback")
                atomic_json(path, report)
                acknowledged = isinstance(response, dict) and response.get("success") is True
                report["readbacks"] = []
                settled_revision = None
                consecutive = 0
                for attempt in range(5):
                    after = _read_state(conn, [track], capabilities)
                    report["readbacks"].append(after)
                    if not _identity_matches(before, after):
                        raise RuntimeError("Project or track identity changed after arm dispatch")
                    _stopped(after)
                    actual = after["tracks"][0]
                    if actual["armed"] == armed:
                        consecutive = (
                            consecutive + 1 if actual["revision"] == settled_revision else 1
                        )
                        settled_revision = actual["revision"]
                    else:
                        consecutive, settled_revision = 0, None
                    if consecutive >= 2:
                        report.update(
                            success=acknowledged,
                            changed=True,
                            status="verified" if acknowledged else "observed_after_error",
                        )
                        break
                    if attempt < 4:
                        time.sleep(0.05)
                else:
                    report.update(
                        status="verification_failed" if acknowledged else "unknown",
                        error="Independent arm readback did not settle at requested state",
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
