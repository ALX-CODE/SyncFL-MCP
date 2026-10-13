"""Validation and result presentation shared by the basic SyncFL tools."""

from __future__ import annotations

import json
import math
from typing import Any

from fl_studio_mcp.utils.connection import get_connection


def _validate(action: str, values: dict[str, Any]) -> None:
    integers = {
        "track",
        "index",
        "plugin_index",
        "param_index",
        "channel",
        "channel_index",
        "mixer_track",
        "position",
        "steps",
        "max_params",
        "r",
        "g",
        "b",
        "note",
        "velocity",
    }
    bounds = {
        "slot_index": (-1, 9),
        "midi_channel": (-1, 15),
        "r": (0, 255),
        "g": (0, 255),
        "b": (0, 255),
        "note": (0, 127),
        "velocity": (0, 127),
    }
    boolean_keys = {"global_count", "use_global", "include_empty", "select", "muted", "solo"}
    for key, value in values.items():
        if key in boolean_keys:
            if type(value) is not bool and not (key in {"muted", "solo"} and value is None):
                raise ValueError(f"{key} must be a boolean")
        elif key in integers or key in bounds:
            if action == "transport.setPosition" and key == "position":
                continue
            low, high = bounds.get(key, (0, None))
            if type(value) is not int or value < low or (high is not None and value > high):
                raise ValueError(f"{key} is outside its integer range")
    for key, low, high in (
        ("volume", 0, 1.25 if action.startswith("mixer.") else 1),
        ("pan", -1, 1),
        ("separation", -1, 1),
        ("value", 0, 1),
        ("speed", 0.25, 4),
    ):
        if key not in values or (key == "value" and action == "channels.setGridBit"):
            continue
        value = values[key]
        if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} must be finite in {low}..{high}")
    if action == "transport.setPosition":
        position = values["position"]
        if type(position) not in (int, float) or not math.isfinite(position) or position < 0:
            raise ValueError("position must be finite and nonnegative")
        if type(values["mode"]) is not int or values["mode"] not in (-1, 0, 1, 2):
            raise ValueError("Unsupported position mode")
        if values["mode"] == -1 and position > 1:
            raise ValueError("Normalized position must be in 0..1")
    if "pattern" in values and (
        not isinstance(values["pattern"], list)
        or len(values["pattern"]) > 65536
        or any(type(step) is not bool for step in values["pattern"])
    ):
        raise ValueError("pattern must contain boolean step states")
    if action == "transport.setLoopMode" and values["mode"] not in ("song", "pattern"):
        raise ValueError("Loop mode must be song or pattern")
    if action == "mixer.soloTrack" and (
        type(values["mode"]) is not int or values["mode"] not in (1, 2, 3, 4)
    ):
        raise ValueError("Solo mode must be an integer in 1..4")
    for key in ("steps", "max_params"):
        if values.get(key, 0) > 65536:
            raise ValueError(key + " exceeds the per-request limit of 65536")


def call(
    action: str, values: dict[str, Any], shape: str = "dict", key: str = "", label: str = ""
) -> Any:
    """Preserve legacy return types while refusing invalid or failed requests."""
    try:
        _validate(action, values)
        response = get_connection().send_command(action, values)
        if response.get("success") is not True or response.get("error"):
            detail = response.get("error") or "Controller did not acknowledge the command"
            outcome = response.get("outcome")
            raise RuntimeError(f"{detail} ({outcome})" if outcome else detail)
    except (RuntimeError, ValueError, TypeError) as exc:
        if shape == "int":
            return -1
        if shape == "bool":
            return False
        if shape == "list":
            return [{"error": str(exc)}]
        if shape in ("text", "string"):
            return "Error: " + str(exc)
        return {"error": str(exc)}
    payload = {
        name: value
        for name, value in response.items()
        if name not in {"success", "protocol", "id", "round_trip_ms"}
    }
    if shape == "int":
        return payload.get(key, -1)
    if shape == "bool":
        return payload.get(key) is True
    if shape == "list":
        return payload.get(key, [])
    if shape == "optional":
        return payload.get(key)
    if shape == "string":
        return str(payload.get(key, ""))
    if shape == "text":
        return label + ": " + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return payload
