"""Save the active named project without desktop input or disk-path claims."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from fl_studio_mcp.utils import native_windows
from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.production import _read
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

_NAMED_TITLE = re.compile(r"[^\r\n/\\]+\.flp - FL Studio(?: [^\r\n]*)?", re.IGNORECASE)


def _window_context():
    rows = native_windows.windows()
    mains = [row for row in rows if row["class"] == "TFruityLoopsMainForm"]
    if len(mains) != 1:
        raise RuntimeError("Expected exactly one visible FL Studio main window")
    main = mains[0]
    if main.get("enabled") is not True:
        raise RuntimeError("FL has a modal operation or its main window is disabled")
    if any(row["pid"] == main["pid"] and row["class"] == "#32770" for row in rows):
        raise RuntimeError("Resolve the active FL dialog before saving")
    return {key: main[key] for key in ("handle", "pid", "title")}


def _inspect(conn):
    window = _window_context()
    ping = conn.send_command("bridge.ping", {}, 5)
    if ping.get("success") is not True:
        raise RuntimeError(f"Controller inspection failed: {ping}")
    capability = ping.get("project_native")
    if not isinstance(capability, dict) or capability.get("protocol") != 1:
        raise RuntimeError("Reload the controller with the typed native-save protocol")
    if type(capability.get("controller_pid")) is not int:
        raise RuntimeError("The controller could not establish its process identity")
    if capability.get("controller_pid") != window["pid"]:
        raise RuntimeError("The MIDI controller belongs to a different FL process")
    native = _read(conn, {"scope": "project"})
    if _window_context() != window:
        raise RuntimeError("FL project window changed during inspection")
    named = _NAMED_TITLE.fullmatch(window["title"]) is not None
    return dict(
        success=True, window=window, named=named, revision=native["revision"], native=native,
        qualification=capability, absolute_path_unknown=True, disk_verified=False,
        ready=(named and capability.get("available") is True
               and not native["is_playing"] and not native["is_recording"]),
    )


def inspect_native_project(connection=None):
    """Observe window identity and native state without activating any window."""
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        return _inspect(conn)


def save_native_project(expected_revision, expected_window_title, apply=False, receipt_file=None,
                        connection=None):
    """Dispatch once; verify a host dirty-to-clean transition or report a clean no-op."""
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if (not isinstance(expected_revision, str)
            or not re.fullmatch("[a-f0-9]{64}", expected_revision)):
        raise ValueError("Use the revision from fl_project_native_inspect")
    if (not isinstance(expected_window_title, str) or len(expected_window_title) > 2048
            or _NAMED_TITLE.fullmatch(expected_window_title) is None):
        raise ValueError("Provide the exact named .flp window title from inspection")
    path = Path(receipt_file) if receipt_file else None
    if apply and (path is None or not path.is_absolute()):
        raise ValueError("Apply requires a new absolute receipt_file")
    conn = connection if connection is not None else get_connection()
    params = dict(expected_revision=expected_revision, expected_window_title=expected_window_title)
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        before = _inspect(conn)
        if before["revision"] != expected_revision:
            raise ValueError("Project revision changed; inspect again before saving")
        if before["window"]["title"] != expected_window_title:
            raise ValueError("Project window title changed; inspect again before saving")
        if not before["ready"]:
            raise RuntimeError(
                "Native save requires a named project, supported API and stopped transport")
        dirty_before = before["native"]["dirty"]
        if type(dirty_before) is not int or dirty_before not in (0, 1, 2):
            raise RuntimeError("Native save requires a recognized host changed flag")
        preview = conn.send_command("project.save_current", {**params, "dry_run": True}, 5)
        if preview.get("success") is not True:
            return dict(success=False, status="not_applied", preview=preview)
        if (preview.get("before", {}).get("revision") != expected_revision
                or preview.get("capability", {}).get("controller_pid") != before["window"]["pid"]):
            raise RuntimeError("Controller save preview did not match the inspected project")
        preview_dirty = preview["before"].get("dirty")
        if type(preview_dirty) is not int or preview_dirty not in (0, 1, 2):
            raise RuntimeError("Controller preview returned an unrecognized host changed flag")
        report = dict(
            success=True, status="preflighted", operation_id=uuid.uuid4().hex, before=before,
            preview=preview, absolute_path_unknown=True, disk_verified=False, desktop_input=False,
            qualification=before["qualification"],
            dispatched=False,
            evidence_scope=(
                "Host changed-flag state only; absolute path and disk bytes are unknown."),
        )
        already_clean = preview_dirty == 0
        if already_clean:
            report.update(status="already_clean", dispatch_ack="not_dispatched")
        if not apply:
            return report
        if _window_context() != before["window"]:
            raise RuntimeError("FL window changed before dispatch; no save sent")
        report.update(success=already_clean,
                      status="already_clean" if already_clean else "dispatching",
                      receipt_file=str(path))
        # Reserve the receipt before the mutation; an existing file can never replay it.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if already_clean:
            return report
        try:
            if _window_context() != before["window"]:
                raise RuntimeError("FL window changed after receipt reservation; no save sent")
            response = conn.send_command("project.save_current", {**params, "dry_run": False}, 10)
            report.update(response=response, status="awaiting_readback", readbacks=[],
                          dispatched=response.get("dispatched") is True,
                          dispatch_ack=response.get("dispatch_ack", "unrecognized"))
            if "transport_return" in response:
                report["transport_return"] = response["transport_return"]
            atomic_json(path, report)
            returned = (response.get("success") is True and response.get("dispatched") is True
                        and not response.get("error"))
            clean_noop = (response.get("success") is True
                          and response.get("dispatched") is False
                          and response.get("status") == "already_clean")
            response_before = response.get("before", {})
            if (response_before.get("revision") != expected_revision
                    or response.get("capability", {}).get("controller_pid")
                    != before["window"]["pid"]):
                returned = clean_noop = False
            native_dirty = response_before.get("dirty")
            transition = (returned and type(native_dirty) is int and native_dirty in (1, 2))
            clean_noop = clean_noop and type(native_dirty) is int and native_dirty == 0
            consecutive = 0
            for attempt in range(5):
                after = _inspect(conn)
                report["readbacks"].append(after)
                if after["window"] != before["window"] or after["revision"] != expected_revision:
                    report.update(status="unknown", error="Project identity changed after dispatch")
                    break
                if after["native"]["is_playing"] or after["native"]["is_recording"]:
                    report.update(status="unknown", error="Transport changed after dispatch")
                    break
                clean = (type(after["native"]["dirty"]) is int
                         and after["native"]["dirty"] == 0)
                consecutive = consecutive + 1 if clean else 0
                if consecutive == 2:
                    report.update(
                        success=transition or clean_noop,
                        status=("saved_current_host" if transition else "already_clean"
                                if clean_noop else "observed_after_error"))
                    break
                if attempt < 4:
                    time.sleep(0.05)
            else:
                report.update(status="verification_failed" if returned else "unknown",
                              error="Two independent clean-state readbacks did not settle")
        except Exception as exc:
            report.update(success=False, status="unknown", error=str(exc))
        try:
            atomic_json(path, report)
        except Exception as exc:
            report.update(success=False, status="unknown", receipt_persistence_error=str(exc))
        return report
