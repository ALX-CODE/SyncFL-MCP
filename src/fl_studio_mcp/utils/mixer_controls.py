"""Guarded background control of FL's built-in mixer EQ and strip switches."""

import copy
import math
import os
import time
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.plugin_state import _call, _digest, _native
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

_BANDS = {
    "gain_normalized": "Gain",
    "frequency_normalized": "Frequency",
    "bandwidth_normalized": "Bandwidth",
}
_SWITCHES = {
    "effects_enabled": ("isTrackSlotsEnabled", "enableTrackSlots"),
    "reverse_polarity": ("isTrackRevPolarity", "revTrackPolarity"),
    "swap_channels": ("isTrackSwapChannels", "swapTrackChannels"),
}
# FL stores these controls on a 16-bit grid. This is a readback tolerance,
# never a conversion from a normalized request to Hz, dB, or Q.
TOLERANCE = 1 / 65535 + 1e-8


def _number(value, label, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be finite in {low}..{high}")
    return value


def _identity(state):
    context = state["context"]
    return (
        context["session"],
        context["project_generation"],
        context["channel_generation"],
        context["channels"],
        state["track"],
        state["name"],
        len(state["bands"]),
    )


def _inspect(conn, track):
    if type(track) is not int or track < 0:
        raise ValueError("track must be a nonnegative integer")
    project = _call(conn, "production.inspect", {"scope": "project"})
    maximum, count = _native(conn, [("mixer.getTrackInfo", [2]), ("mixer.getEqBandCount", [])])
    if type(maximum) is not int or track > maximum:
        raise ValueError("Mixer track does not exist")
    if type(count) is not int or not 1 <= count <= 16:
        raise RuntimeError("Unsupported mixer EQ band count")
    specs = [("mixer.getTrackName", [track])]
    specs.extend(("mixer." + read, [track]) for read, _ in _SWITCHES.values())
    for band in range(count):
        specs.extend(
            [
                ("mixer.getEqGain", [track, band, 0]),
                ("mixer.getEqFrequency", [track, band, 0]),
                ("mixer.getEqBandwidth", [track, band]),
                ("mixer.getEqGain", [track, band, 1]),
                ("mixer.getEqFrequency", [track, band, 1]),
            ]
        )
    values = _native(conn, specs)
    name, switches, measurements = values[0], values[1:4], values[4:]
    if not isinstance(name, str) or any(
        type(v) not in (bool, int) or v not in (0, 1) for v in switches
    ):
        raise RuntimeError("Invalid mixer identity or switch readback")
    bands = []
    for band in range(count):
        gain, frequency, bandwidth, db, hz = measurements[band * 5 : band * 5 + 5]
        bands.append(
            dict(
                band=band,
                gain_normalized=_number(gain, "gain"),
                frequency_normalized=_number(frequency, "frequency"),
                bandwidth_normalized=_number(bandwidth, "bandwidth"),
                gain_db=_number(db, "gain dB", -1000, 1000),
                frequency_hz=_number(hz, "frequency Hz", 0, 1e9),
            )
        )
    later = _call(conn, "production.inspect", {"scope": "project"})
    if project["context"] != later["context"]:
        raise RuntimeError("Project changed during mixer inspection")
    state = dict(
        track=track,
        name=name,
        context=later["context"],
        bands=bands,
        **dict(zip(_SWITCHES, map(bool, switches))),
        is_playing=later["is_playing"],
        is_recording=later["is_recording"],
        controller_build=later["controller_build"],
    )
    state["revision"] = _digest(state)
    return state


def inspect_controls(track, connection=None):
    """Read normalized settings plus native dB/Hz without selecting any strip."""
    conn = connection if connection is not None else get_connection()
    return _inspect(conn, track)


def _changes(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - (set(_SWITCHES) | {"bands"}):
        raise ValueError("Provide only bands and/or explicit mixer switches")
    for key in set(changes) & set(_SWITCHES):
        if type(changes[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    bands = changes.get("bands", [])
    if not isinstance(bands, list) or ("bands" in changes and not 1 <= len(bands) <= 16):
        raise ValueError("bands must contain 1..16 distinct band changes")
    seen = set()
    for row in bands:
        if not isinstance(row, dict) or set(row) - (set(_BANDS) | {"band"}) or len(row) < 2:
            raise ValueError("Each band needs band and at least one normalized control")
        band = row.get("band")
        if type(band) is not int or not 0 <= band < 16 or band in seen:
            raise ValueError("Band indices must be distinct nonnegative integers below 16")
        seen.add(band)
        for key in set(row) & set(_BANDS):
            _number(row[key], key)
    return copy.deepcopy(changes)


def set_controls(
    track,
    expected_name,
    expected_revision,
    changes,
    *,
    apply=False,
    receipt_file=None,
    connection=None,
):
    """Preflight or write once, then independently verify; never replay or roll back."""
    changes = _changes(changes)
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(expected_name, str) or not expected_name:
        raise ValueError("expected_name must be exact nonempty text")
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise ValueError("Use the revision from a fresh mixer controls inspection")
    path = Path(receipt_file).resolve() if receipt_file else None
    if apply and path is None:
        raise ValueError("A new receipt_file is required to apply")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        before = _inspect(conn, track)
        if before["revision"] != expected_revision or before["name"] != expected_name:
            raise ValueError("Mixer/project revision or track name changed; inspect again")
        if before["is_playing"] or before["is_recording"]:
            raise ValueError("Stop playback and recording before changing mixer controls")
        steps, restore = [], {"bands": []}
        for key, (_, setter) in _SWITCHES.items():
            if key in changes:
                steps.append(("mixer." + setter, [track, int(changes[key])]))
                restore[key] = before[key]
        for row in changes.get("bands", []):
            band = row["band"]
            if band >= len(before["bands"]):
                raise ValueError("Band does not exist in this host")
            old = {"band": band}
            for key, suffix in _BANDS.items():
                if key in row:
                    steps.append(("mixer.setEq" + suffix, [track, band, row[key]]))
                    old[key] = before["bands"][band][key]
            restore["bands"].append(old)
        if not restore["bands"]:
            del restore["bands"]
        payload = {
            "steps": [
                dict(action="native.call", params=dict(function=name, args=args, allow_write=True))
                for name, args in steps
            ],
            "dry_run": True,
        }
        preview = _call(conn, "bridge.batch", payload)
        if preview.get("validated") != len(steps) or preview.get("applied") != 0:
            raise RuntimeError("Controller did not preflight every mixer change")
        report = dict(
            success=True,
            status="preflighted",
            dry_run=not apply,
            before=before,
            changes=changes,
            restore_changes=restore,
            tolerance=TOLERANCE,
            desktop_input=False,
            limitations=[
                "Host readback does not prove audio/DSP output.",
                "Hz/dB are native observations; writes use normalized values.",
                "The shared lock cannot exclude manual edits in FL.",
                "Guards are separate callbacks, not an atomic host transaction.",
            ],
        )
        if not apply:
            return report
        report.update(success=False, status="prepared")
        with path.open("x", encoding="utf-8") as stream:
            import json

            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        dispatched = False
        try:
            current = _inspect(conn, track)
            if current["revision"] != before["revision"]:
                raise RuntimeError("Mixer changed after preflight; no mutation dispatched")
            report["status"] = "dispatching"
            atomic_json(path, report)
            payload["dry_run"] = False
            dispatched = True
            response = conn.send_command("bridge.batch", payload, 30)
            report["response"] = response
            rows = response.get("results") if isinstance(response, dict) else None
            acknowledged = (
                isinstance(response, dict)
                and response.get("success") is True
                and response.get("applied") == len(steps)
                and isinstance(rows, list)
                and len(rows) == len(steps)
                and all(
                    isinstance(row, dict)
                    and "value" in row
                    and row.get("success") is not False
                    and not row.get("error")
                    for row in rows
                )
            )
            report["readbacks"] = []
            for _ in range(2):
                time.sleep(0.1)
                after = _inspect(conn, track)
                report["readbacks"].append(after)
                if (
                    _identity(after) != _identity(before)
                    or after["is_playing"]
                    or after["is_recording"]
                ):
                    raise RuntimeError("Mixer/project identity or transport changed")
                matches = all(after[key] == changes[key] for key in set(changes) & set(_SWITCHES))
                matches &= all(
                    abs(after["bands"][row["band"]][key] - row[key]) <= TOLERANCE
                    for row in changes.get("bands", [])
                    for key in set(row) & set(_BANDS)
                )
                if not matches:
                    raise RuntimeError("Native mixer readback did not match requested controls")
            report.update(
                success=acknowledged, status="verified" if acknowledged else "observed_after_error"
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
