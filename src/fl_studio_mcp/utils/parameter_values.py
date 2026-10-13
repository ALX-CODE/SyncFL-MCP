"""Calibrate a continuous plugin control from its own displayed value.

This briefly probes the control while transport is stopped. It is not for
enumerations or controls with non-monotonic displays. Exact plugin/parameter and
unit guards are mandatory; failures attempt restoration and report its outcome.
"""

import math
import re
import time

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import transaction_lock


def display_number(display, unit):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(.*?)\s*", display)
    if not match:
        raise ValueError(f"Expected a continuous numeric display in {unit!r}, got {display!r}")
    # Trackspacer formats kilohertz as '20.00k Hz'. Treat spacing within
    # a unit as typography, without accepting unrelated units.
    actual = re.sub(r"\s+", "", match[2].casefold())
    wanted = re.sub(r"\s+", "", unit.casefold())
    if actual == wanted:
        return float(match[1])
    scales = {
        "ms": ("time", 0.001),
        "msec": ("time", 0.001),
        "sec": ("time", 1),
        "s": ("time", 1),
        "hz": ("frequency", 1),
        "khz": ("frequency", 1000),
    }
    if actual in scales and wanted in scales and scales[actual][0] == scales[wanted][0]:
        return float(match[1]) * scales[actual][1] / scales[wanted][1]
    raise ValueError(f"Expected a continuous numeric display in {unit!r}, got {display!r}")


def set_display_value(
    track,
    slot,
    parameter,
    expected_plugin,
    expected_name,
    target,
    unit,
    tolerance=0.01,
    lower=0.00001,
    upper=0.99999,
):
    if not all(math.isfinite(v) for v in (target, tolerance, lower, upper)):
        raise ValueError("Target and calibration bounds must be finite")
    if not 0 <= lower < upper <= 1 or tolerance <= 0:
        raise ValueError("Invalid calibration bounds or tolerance")
    conn = get_connection()

    def call(action, params=None):
        result = conn.send_command(action, params or {}, 10)
        if not result.get("success"):
            raise RuntimeError(str(result))
        return result

    # Serialize calibration calls. General native commands have their own
    # per-request transport lock; callers must not edit this control concurrently.
    with transaction_lock(conn._hardware_dir / "mcp-parameter.lock", 5):
        state = call("transport.getStatus")
        if state.get("is_playing") or state.get("is_recording"):
            raise ValueError("Stop playback and recording before physical-value calibration")
        item = call(
            "plugins.parameters",
            {
                "track": track,
                "slot": slot,
                "expected_plugin": expected_plugin,
                "offset": parameter,
                "limit": 1,
            },
        )["parameters"]
        if len(item) != 1 or item[0]["name"] != expected_name:
            raise ValueError("Parameter identity changed")
        original = item[0]
        display_number(original["display"], unit)
        trace = []

        def write(value):
            call(
                "plugins.setMany",
                {
                    "track": track,
                    "slot": slot,
                    "expected_plugin": expected_plugin,
                    "changes": [
                        {"parameter": parameter, "value": value, "expected_name": expected_name}
                    ],
                },
            )
            # VST displayed strings can lag a setter within the same controller
            # callback. Read again in a subsequent round trip after FL processes it.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = call(
                    "plugins.parameters",
                    {
                        "track": track,
                        "slot": slot,
                        "expected_plugin": expected_plugin,
                        "offset": parameter,
                        "limit": 1,
                    },
                )["parameters"][0]
                if abs(result["value"] - value) < 0.00001:
                    # Native normalized readback can precede the plugin's
                    # formatted display refresh. Require a later settled sample.
                    time.sleep(0.1)
                    result = call(
                        "plugins.parameters",
                        {
                            "track": track,
                            "slot": slot,
                            "expected_plugin": expected_plugin,
                            "offset": parameter,
                            "limit": 1,
                        },
                    )["parameters"][0]
                    if abs(result["value"] - value) < 0.00001:
                        trace.append({"normalized": value, "display": result["display"]})
                        return display_number(result["display"], unit), result
                time.sleep(0.04)
            raise RuntimeError("Parameter did not settle to the requested normalized value")

        try:
            low_value, _ = write(lower)
            high_value, _ = write(upper)
            if not min(low_value, high_value) <= target <= max(low_value, high_value):
                raise ValueError("Target is outside the displayed range")
            increasing = high_value > low_value
            if high_value == low_value:
                raise ValueError("Control is not a continuous numeric parameter")
            for _ in range(24):
                middle = (lower + upper) / 2
                value, after = write(middle)
                if abs(value - target) <= tolerance:
                    return {
                        "success": True,
                        "plugin": expected_plugin,
                        "parameter": expected_name,
                        "before": original,
                        "after": after,
                    }
                if (value < target) == increasing:
                    lower = middle
                else:
                    upper = middle
            raise ValueError("Displayed target did not converge; control may be discrete")
        except Exception as exc:
            try:
                _, restored = write(original["value"])
                restore = {
                    "success": abs(restored["value"] - original["value"]) < 0.00001,
                    "after": restored,
                }
            except Exception as restore_error:
                restore = {"success": False, "error": str(restore_error)}
            return {"success": False, "error": str(exc), "restoration": restore, "trace": trace}
