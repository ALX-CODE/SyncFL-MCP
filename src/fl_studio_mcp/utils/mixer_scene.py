"""Guarded mixer batches with measured dB calibration and durable recovery receipts.

The native volume setter accepts normalized volume, not dB. Physical targets
therefore use bounded bisection against getTrackVolume(track, 1). No fader law
is assumed. This host lock coordinates scene clients; it cannot lock FL's UI.
"""

import json
import math
import os
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

_FIELDS = {"volume", "volume_db", "pan", "stereo"}
_TOL = 0.00001
_DB_TOL = 0.02
# FL26 live readbacks can round an intermediate fader setting (for example,
# 0.41796875 -> 0.418). Permit at most half a 0.001 step plus numeric epsilon
# during calibration only. Actual native dB must still satisfy _DB_TOL.
_CALIBRATION_VOLUME_TOL = 0.000500001


class _CommandFailure(RuntimeError):
    def __init__(self, response):
        super().__init__(str(response))
        self.response = response


def _validate(items, apply, receipt_file):
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(items, list) or not 1 <= len(items) <= 32:
        raise ValueError("Scene requires 1..32 tracks")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - (_FIELDS | {"track", "expected_name"}):
            raise ValueError("Unknown scene fields")
        track = item.get("track")
        if type(track) is not int or track < 0 or track in seen:
            raise ValueError("Track must be a unique nonnegative integer")
        seen.add(track)
        if not isinstance(item.get("expected_name"), str) or not item["expected_name"]:
            raise ValueError("Exact expected_name required")
        if not _FIELDS.intersection(item) or {"volume", "volume_db"} <= item.keys():
            raise ValueError("Provide controls and use volume OR volume_db")
        for key in _FIELDS.intersection(item):
            value = item[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number, not boolean")
            if key != "volume_db" and not (0 if key == "volume" else -1) <= value <= 1:
                raise ValueError(f"{key} outside its normalized range")
    if apply and not receipt_file:
        raise ValueError("A new receipt_file is required for apply")


def apply_mixer_scene(items, apply=False, receipt_file=None, connection=None):
    """Preflight or apply exact-name guarded volume/pan/stereo changes.

    Items contain track, expected_name, and volume (0..1) OR volume_db,
    optionally pan/stereo (-1..1). Apply requires a previously unused receipt.
    Failed acknowledged writes are restored only after fresh identity/transport
    checks. An uncertain command is never retried or followed by restore writes.
    """
    _validate(items, apply, receipt_file)
    items = [dict(item) for item in items]
    conn = connection if connection is not None else get_connection()
    path = Path(receipt_file).resolve() if receipt_file else None

    def call(action, params=None):
        result = conn.send_command(action, params or {}, 20)
        if not isinstance(result, dict) or result.get("success") is not True:
            raise _CommandFailure(result)
        return result

    def native_steps(specs):
        result = call(
            "bridge.batch",
            {
                "steps": [
                    {"action": "native.call", "params": {"function": name, "args": args}}
                    for name, args in specs
                ]
            },
        )
        if len(result.get("results", [])) != len(specs):
            raise RuntimeError("Incomplete native batch readback")
        return [r["value"] for r in result["results"]]

    def guard():
        status = call("transport.getStatus")
        if status.get("is_playing") is not False or status.get("is_recording") is not False:
            raise ValueError("Transport must be stopped and recording off")
        names = native_steps([("mixer.getTrackName", [c["track"]]) for c in items])
        for item, name in zip(items, names):
            if item["expected_name"] != name:
                raise ValueError(f"Mixer identity changed on track {item['track']}: {name!r}")

    def snapshot():
        specs = []
        for c in items:
            t = c["track"]
            specs.extend(
                [
                    ("mixer.getTrackVolume", [t]),
                    ("mixer.getTrackVolume", [t, 1]),
                    ("mixer.getTrackPan", [t]),
                    ("mixer.getTrackStereoSep", [t]),
                ]
            )
        values = native_steps(specs)
        result = []
        for i, c in enumerate(items):
            volume, db, pan, stereo = values[i * 4 : i * 4 + 4]
            for key, value, low in (
                ("volume", volume, 0),
                ("pan", pan, -1),
                ("stereo", stereo, -1),
            ):
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not low <= value <= 1
                ):
                    raise RuntimeError(f"Invalid {key} readback")
            if (
                type(db) not in (int, float)
                or math.isnan(db)
                or db == math.inf
                or (db == -math.inf and volume != 0)
            ):
                raise RuntimeError("Invalid dB readback")
            result.append(
                {
                    "track": c["track"],
                    "expected_name": c["expected_name"],
                    "volume": volume,
                    "volume_db": db if math.isfinite(db) else None,
                    "pan": pan,
                    "stereo": stereo,
                }
            )
        return result

    with transaction_lock(conn._hardware_dir / "mcp-mixer-scene.lock", 5):
        maximum = native_steps([("mixer.getTrackInfo", [2])])[0]
        if any(c["track"] > maximum for c in items):
            raise ValueError("Mixer track does not exist")
        guard()
        before = snapshot()
        restore = [
            {
                "track": c["track"],
                "expected_name": c["expected_name"],
                **{
                    key: old[key]
                    for key in ("volume", "pan", "stereo")
                    if key in c or (key == "volume" and "volume_db" in c)
                },
            }
            for c, old in zip(items, before)
        ]
        report = {
            "success": not apply,
            "status": "preflighted",
            "dry_run": not apply,
            "items": items,
            "before": before,
            "restore_scene": restore,
            "batches": [],
            "calibration_measurements": [],
            "db_tolerance": _DB_TOL,
            "calibration_volume_tolerance": _CALIBRATION_VOLUME_TOL,
        }
        if not apply:
            return report
        report["status"] = "applying"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())

        uncertain = False
        wrote = False

        def calibration_snapshot(phase):
            measured = snapshot()
            report["calibration_measurements"].append({"phase": phase, "tracks": measured})
            atomic_json(path, report)
            return {state["track"]: state for state in measured}

        def configure(changes, phase):
            nonlocal uncertain, wrote
            if not changes:
                return
            guard()
            entry = {"phase": phase, "changes": changes, "status": "dispatching"}
            report["batches"].append(entry)
            atomic_json(path, report)
            wrote = True
            try:
                response = call(
                    "bridge.batch",
                    {
                        "steps": [
                            {"action": "mixer.configure", "params": change} for change in changes
                        ]
                    },
                )
            except _CommandFailure as exc:
                entry.update(status="failed", response=exc.response)
                # A correlated controller failure with a completed partial batch
                # is settled. A timeout/error without that evidence is uncertain.
                uncertain = not (
                    isinstance(exc.response, dict)
                    and exc.response.get("partial") is True
                    and "failed_step" in exc.response
                )
                raise
            except Exception:
                uncertain = True
                entry["status"] = "uncertain"
                raise
            entry.update(status="acknowledged", applied=response.get("applied"))
            atomic_json(path, report)

        try:
            configure(
                [
                    {k: v for k, v in c.items() if k in {"track", "volume", "pan", "stereo"}}
                    for c in items
                    if {"volume", "pan", "stereo"}.intersection(c)
                ],
                "normalized",
            )
            targets = {c["track"]: c["volume_db"] for c in items if "volume_db" in c}
            if targets:
                # FL can quantize tiny positive normalized values to exact zero.
                # Probe silence explicitly; native -inf is a valid lower bound.
                bounds = {t: [0.0, 1.0] for t in targets}
                endpoints = []
                for side in (0, 1):
                    configure(
                        [{"track": t, "volume": bounds[t][side]} for t in targets],
                        f"db_endpoint_{side}",
                    )
                    measured = calibration_snapshot(f"db_endpoint_{side}")
                    if any(abs(measured[t]["volume"] - bounds[t][side]) > _TOL for t in targets):
                        raise RuntimeError("Calibration endpoint normalized readback mismatch")
                    endpoints.append({t: measured[t]["volume_db"] for t in targets})
                for t, target in targets.items():
                    low, high = endpoints[0][t], endpoints[1][t]
                    # Negative infinity is a valid native silence reading, stored
                    # as JSON null. It brackets every finite target from below.
                    if high is None or (low is not None and not low < high) or not (
                        (low is None or low <= target) and target <= high
                    ):
                        raise ValueError(
                            f"dB target out of measured range or nonmonotonic: track {t}"
                        )
                pending = dict(targets)
                for iteration in range(24):
                    if not pending:
                        break
                    mids = {t: sum(bounds[t]) / 2 for t in pending}
                    configure(
                        [{"track": t, "volume": v} for t, v in mids.items()],
                        f"db_bisection_{iteration}",
                    )
                    measured = calibration_snapshot(f"db_bisection_{iteration}")
                    for t, target in list(pending.items()):
                        actual = measured[t]
                        db = actual["volume_db"]
                        if abs(actual["volume"] - mids[t]) > _CALIBRATION_VOLUME_TOL:
                            raise RuntimeError("Calibration normalized/dB readback mismatch")
                        if db is not None and abs(db - target) <= _DB_TOL:
                            del pending[t]
                        else:
                            bounds[t][0 if db is None or db < target else 1] = mids[t]
                if pending:
                    raise RuntimeError("dB target failed bounded convergence")
            guard()
            after = snapshot()
            report["after"] = after
            for c, actual in zip(items, after):
                for key in _FIELDS.intersection(c):
                    tolerance = _DB_TOL if key == "volume_db" else _TOL
                    if actual[key] is None or abs(actual[key] - c[key]) > tolerance:
                        raise RuntimeError(f"Final {key} readback mismatch on track {c['track']}")
        except Exception as exc:
            report.update(
                success=False,
                status="uncertain" if uncertain else "failed",
                error=str(exc),
                partial=wrote,
                restoration={"success": False},
            )
            if uncertain:
                report["restoration"]["reason"] = (
                    "Mutation outcome uncertain; inspect FL and receipt before recovery. "
                    "No retry sent."
                )
            elif wrote:
                try:
                    configure(
                        [{k: v for k, v in c.items() if k != "expected_name"} for c in restore],
                        "restore",
                    )
                    guard()
                    restored = snapshot()
                    for recipe, state in zip(restore, restored):
                        for key in _FIELDS.intersection(recipe):
                            if abs(recipe[key] - state[key]) > _TOL:
                                raise RuntimeError("Restore readback mismatch")
                    report["restoration"] = {"success": True, "after": restored}
                except Exception as restore_error:
                    report["restoration"]["error"] = str(restore_error)
                    if uncertain:
                        report["status"] = "uncertain"
        else:
            report.update(success=True, status="completed", partial=False)
        atomic_json(path, report)
        return report
