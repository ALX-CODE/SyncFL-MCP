"""Revision-guarded edits, one dispatch, and separate callback readback."""

import json
import math
import os
import time
import uuid
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

TOLERANCES = {"tempo_bpm": 0.0011, "shuffle": 0.0001, "volume_normalized": 0.0001,
              "pan": 0.0001, "pitch_semitones": 0.011}


def _read(conn, target):
    result = conn.send_command("production.inspect", target, 5)
    if result.get("success") is not True:
        raise RuntimeError(f"Production inspect failed; check controller deployment: {result}")
    return result


def inspect_production(target, connection=None):
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        return _read(conn, target)


def matches(actual, requested):
    for key, value in requested.items():
        observed = actual.get(key)
        if value is None:
            if key not in actual or observed is not None:
                return False
            continue
        if key in TOLERANCES:
            if (type(observed) not in (int, float) or not math.isfinite(observed)
                    or abs(observed - value) > TOLERANCES[key]):
                return False
        elif type(observed) is not type(value) or observed != value:
            return False
    return True


def edit_production(target, changes, expected_revision, apply=False, receipt_file=None,
                    connection=None):
    """Never retry a mutation, including after timeout or receipt persistence failure."""
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise ValueError("Use the revision from fl_production_inspect")
    path = Path(receipt_file) if receipt_file else None
    if apply and (path is None or not path.is_absolute()):
        raise ValueError("Apply requires a new absolute receipt_file")
    conn = connection if connection is not None else get_connection()
    params = {**target, "changes": changes, "expected_revision": expected_revision}
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        preview = conn.send_command("production.apply", {**params, "dry_run": True}, 5)
        if preview.get("success") is not True:
            return dict(success=False, status="not_applied", preview=preview)
        before, requested = preview["before"], preview["requested"]
        expected_values = {k: v for k, v in before["values"].items()
                           if k not in {"volume_db", "pitch_normalized"}}
        expected_values.update(requested)
        report = dict(
            success=not apply, status="preflighted", operation_id=uuid.uuid4().hex,
            target=target, before=before, requested=requested, tolerance=TOLERANCES,
            expected_values=expected_values,
            restore_changes={k: before["values"][k] for k in requested},
            recovery="Inspect current state and use its fresh revision for any recovery edit.",
        )
        if not apply:
            return report
        report.update(success=False, status="dispatching", receipt_file=str(path))
        # Exclusive creation also prevents accidental replay with an old receipt.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            response = conn.send_command("production.apply", {**params, "dry_run": False}, 10)
            report["response"] = response
            report["status"] = "awaiting_readback"
            atomic_json(path, report)
            settled = response.get("success") is True
            # Two matching snapshots, each from a new FL callback, establish that
            # the edit survived the write callback. Never poll by writing again.
            consecutive = 0
            report["readbacks"] = []
            for attempt in range(5):
                after = _read(conn, target)
                report["readbacks"].append(after)
                if after["context"] != before["context"] or after["target"] != before["target"]:
                    report.update(status="unknown", error="Target identity changed after dispatch")
                    break
                if after["is_playing"] or after["is_recording"]:
                    report.update(status="unknown", error="Transport changed during verification")
                    break
                consecutive = consecutive + 1 if matches(after["values"], expected_values) else 0
                if consecutive >= 2:
                    report.update(success=settled,
                                  status="verified" if settled else "observed_after_error")
                    break
                if attempt < 4:
                    time.sleep(0.05)
            else:
                report.update(status="verification_failed" if settled else "unknown",
                              error="Independent readback did not settle at the requested values")
        except Exception as exc:
            report.update(success=False, status="unknown", error=str(exc))
        try:
            atomic_json(path, report)
        except Exception as exc:
            report.update(success=False, status="unknown", receipt_persistence_error=str(exc))
        return report
