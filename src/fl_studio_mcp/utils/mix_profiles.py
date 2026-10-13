"""Apply named, reviewable plugin recipes with a durable restore recipe.

Native automation displays lag writes. Verification runs after a separate FL
callback. This intentionally does not equate normalized values with dB or ms.
"""

import json
import math
import time
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.parameter_values import set_display_value
from fl_studio_mcp.utils.protocol import transaction_lock


def apply_profile(profile, apply=False, receipt_file=None):
    conn = get_connection()

    def call(action, params=None):
        result = conn.send_command(action, params or {}, 20)
        if not result.get("success"):
            raise RuntimeError(str(result))
        return result

    with transaction_lock(conn._hardware_dir / "mcp-profile.lock", 5):
        status = call("transport.getStatus")
        if status.get("is_playing") or status.get("is_recording"):
            raise ValueError("Stop transport before applying or probing a mix profile")
        controls = profile["controls"]
        if not isinstance(controls, list) or not 1 <= len(controls) <= 256:
            raise ValueError("A profile requires 1..256 controls")
        before, seen = [], set()
        for c in controls:
            identity = (c["track"], c["slot"], c["parameter"])
            if identity in seen:
                raise ValueError("Duplicate profile control")
            seen.add(identity)
            if not c.get("plugin") or not c.get("name"):
                raise ValueError("Exact plugin and parameter names required")
            if ("value" in c) == ("target" in c):
                raise ValueError("Use either normalized value or physical target")
            value = c.get("value", c.get("target"))
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Control value must be finite")
            if "value" in c and not 0 <= value <= 1:
                raise ValueError("Normalized value outside 0..1")
            if "target" in c:
                if "unit" not in c or not isinstance(c["unit"], str):
                    raise ValueError("Physical target requires an explicit unit")
                tolerance = c.get("tolerance", 0.01)
                if not math.isfinite(tolerance) or tolerance <= 0:
                    raise ValueError("Tolerance must be positive and finite")
            item = call(
                "plugins.parameters",
                {
                    "track": c["track"],
                    "slot": c["slot"],
                    "expected_plugin": c["plugin"],
                    "offset": c["parameter"],
                    "limit": 1,
                },
            )["parameters"]
            if len(item) != 1 or item[0]["name"] != c["name"]:
                raise ValueError("Parameter identity changed")
            before.append(
                {
                    "track": c["track"],
                    "slot": c["slot"],
                    "parameter": c["parameter"],
                    "plugin": c["plugin"],
                    "name": c["name"],
                    "value": item[0]["value"],
                }
            )
        report = {
            "success": not apply,
            "status": "applying" if apply else "preflighted",
            "dry_run": not apply,
            "count": len(controls),
            "restore_profile": {"controls": before},
            "applied": [],
        }
        if not apply:
            return report
        if not receipt_file:
            raise ValueError("A receipt_file is required when applying a profile")
        path = Path(receipt_file).resolve()
        # Exclusive creation prevents an accidental second run losing its undo.
        with path.open("x", encoding="utf-8") as out:
            json.dump(report, out, indent=2)
        try:
            for c in controls:
                if "target" in c:
                    result = set_display_value(
                        c["track"],
                        c["slot"],
                        c["parameter"],
                        c["plugin"],
                        c["name"],
                        c["target"],
                        c["unit"],
                        c.get("tolerance", 0.01),
                    )
                    if not result["success"]:
                        raise RuntimeError(str(result))
                else:
                    call(
                        "plugins.setMany",
                        {
                            "track": c["track"],
                            "slot": c["slot"],
                            "expected_plugin": c["plugin"],
                            "changes": [
                                {
                                    "parameter": c["parameter"],
                                    "expected_name": c["name"],
                                    "value": c["value"],
                                }
                            ],
                        },
                    )
                time.sleep(0.15)
                after = call(
                    "plugins.parameters",
                    {
                        "track": c["track"],
                        "slot": c["slot"],
                        "expected_plugin": c["plugin"],
                        "offset": c["parameter"],
                        "limit": 1,
                    },
                )["parameters"][0]
                report["applied"].append({"control": c, "after": after})
                if "value" in c and abs(after["value"] - c["value"]) > 0.00001:
                    raise RuntimeError("Normalized readback mismatch")
                if "display" in c and after["display"].strip() != c["display"].strip():
                    raise RuntimeError(f"Display mismatch: {after['display']!r}")
                path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception as exc:
            report.update(success=False, status="failed", error=str(exc), failed_control=c)
        else:
            report.update(success=True, status="completed")
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
