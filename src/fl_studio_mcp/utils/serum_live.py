"""Named Serum 2 controls, calibrated against the 2.1.2 live parameter catalog.

No UI coordinates and no guessed FX slots: every operation checks live names.
Only measured transfer functions have physical units; other controls explicitly
use normalized values. This cannot create modulation routes, change their
polarity, load wavetables, or configure FX topology. A matching name/catalog is
not a unique plugin-instance identifier; do not edit the target concurrently.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import transaction_lock


@dataclass(frozen=True)
class Control:
    index: int
    name: str
    unit: str

    @property
    def bounds(self):
        return {
            "ms": (0, 32000),
            "percent": (0, 100),
            "cents": (-100, 100),
            "signed_percent": (-100, 100),
            "normalized": (0, 1),
            "boolean": (0, 1),
        }[self.unit]

    def normalize(self, value):
        if self.unit == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{self.name} requires true or false")
            return float(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.name} requires a finite number in {self.unit}")
        low, high = self.bounds
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{self.name} must be finite in {low}..{high} {self.unit}")
        if self.unit == "ms":
            return (value / 32000) ** 0.2
        if self.unit == "percent":
            return value / 100
        if self.unit in {"cents", "signed_percent"}:
            return (value + 100) / 200
        return float(value)

    def physical(self, value):
        if self.unit == "ms":
            return 32000 * value**5
        if self.unit == "percent":
            return value * 100
        if self.unit in {"cents", "signed_percent"}:
            return value * 200 - 100
        if self.unit == "boolean":
            return value >= 0.5
        return value


# Exact names/indexes read from the user's Serum 2 channel on 2026-09-07.
# A WT Pos depends on the loaded wavetable; detune is nonlinear; sustain is not
# an amplitude percentage. Keep these normalized until independently calibrated.
CONTROLS = {
    "master.volume_percent": Control(0, "Main Vol", "percent"),
    "voice.portamento_normalized": Control(3, "Porta Time", "normalized"),
    "voice.mono": Control(9, "Mono Toggle", "boolean"),
    "voice.legato": Control(10, "Legato", "boolean"),
    "voice.portamento_always": Control(11, "Porta Always", "boolean"),
    "voice.portamento_scaled": Control(12, "Porta Scaled", "boolean"),
    "osc_a.enabled": Control(20, "A Enable", "boolean"),
    "osc_a.level_percent": Control(21, "A Level", "percent"),
    "osc_a.octave_normalized": Control(23, "A Octave", "normalized"),
    "osc_a.semitone_normalized": Control(24, "A Semi", "normalized"),
    "osc_a.fine_cents": Control(25, "A Fine", "cents"),
    "osc_a.unison_normalized": Control(44, "A Unison", "normalized"),
    "osc_a.detune_normalized": Control(46, "A Uni Detune", "normalized"),
    "osc_a.blend_normalized": Control(47, "A Uni Blend", "normalized"),
    "osc_a.width_normalized": Control(48, "A Uni Width", "normalized"),
    "osc_a.wavetable_position_normalized": Control(59, "A WT Pos", "normalized"),
    "osc_a.phase_normalized": Control(61, "A Phase", "normalized"),
    "osc_a.random_phase_normalized": Control(62, "A Rand Phase", "normalized"),
    "osc_b.enabled": Control(75, "B Enable", "boolean"),
    "osc_b.level_percent": Control(76, "B Level", "percent"),
    "osc_b.octave_normalized": Control(78, "B Octave", "normalized"),
    "osc_b.semitone_normalized": Control(79, "B Semi", "normalized"),
    "osc_b.fine_normalized": Control(80, "B Fine", "normalized"),
    "osc_b.unison_normalized": Control(99, "B Unison", "normalized"),
    "osc_b.detune_normalized": Control(101, "B Uni Detune", "normalized"),
    "osc_b.wavetable_position_normalized": Control(114, "B WT Pos", "normalized"),
    "env1.attack_ms": Control(224, "Env 1 Attack", "ms"),
    "env1.hold_ms": Control(225, "Env 1 Hold", "ms"),
    "env1.decay_ms": Control(226, "Env 1 Decay", "ms"),
    "env1.sustain_normalized": Control(227, "Env 1 Sustain", "normalized"),
    "env1.release_ms": Control(228, "Env 1 Release", "ms"),
    "env1.attack_curve_percent": Control(229, "Env 1 Atk Curve", "percent"),
    "env1.decay_curve_percent": Control(230, "Env 1 Dec Curve", "percent"),
    "env1.release_curve_percent": Control(231, "Env 1 Rel Curve", "percent"),
    "env2.attack_ms": Control(232, "Env 2 Attack", "ms"),
    "env2.hold_ms": Control(233, "Env 2 Hold", "ms"),
    "env2.decay_ms": Control(234, "Env 2 Decay", "ms"),
    "env2.sustain_normalized": Control(235, "Env 2 Sustain", "normalized"),
    "env2.release_ms": Control(236, "Env 2 Release", "ms"),
    "env2.attack_curve_percent": Control(237, "Env 2 Atk Curve", "percent"),
    "env2.decay_curve_percent": Control(238, "Env 2 Dec Curve", "percent"),
    "env2.release_curve_percent": Control(239, "Env 2 Rel Curve", "percent"),
    "mod1.amount_percent": Control(312, "Mod 1 Amount", "signed_percent"),
}
LIMITATIONS = [
    "FX topology, wavetable loading, modulation routing and polarity require a preset or UI.",
    "Mod 1 is an existing slot, not a guaranteed pitch envelope route; verify its destination.",
    "Percent means knob position, not linear loudness. Normalized controls have no physical map.",
    "Names cannot identify a replaced instance of the same plugin; avoid concurrent edits.",
    "Native preset loads can leave FL normalized values stale indefinitely. Trust parsed displays.",
    "For stale caches or unmapped displays, build a full preset with offline settings.",
]
TOLERANCE = 1e-5
SETTLE_SECONDS = 0.15


def _display_state(spec, row):
    """Parse Serum's current display separately from FL's possibly stale cache.

    The display's printed precision defines its rounding tolerance. A parsed
    number is not converted back into an exact rollback value: only a consistent
    host value supplies that precision. Unmapped displays remain unknown.
    """
    display = row.get("value_string", "").strip()
    value, rounding = None, None
    if spec.unit == "boolean" and display.casefold() in {"on", "off"}:
        value, rounding = display.casefold() == "on", 0
    else:
        number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
        patterns = {
            "ms": number + r"\s*(ms|s)",
            "percent": number + r"\s*%(?:\s*\[[^\]]*\])?",
            "signed_percent": number + r"\s*%",
            "cents": number + r"\s*cents",
        }
        match = re.fullmatch(patterns[spec.unit], display) if spec.unit in patterns else None
        if match:
            text = match.group(1)
            decimals = len(text.split(".")[1]) if "." in text else 0
            factor = 1000 if spec.unit == "ms" and match.group(2) == "s" else 1
            value = float(text) * factor
            rounding = 0.5 * 10 ** (-decimals) * factor
            low, high = spec.bounds
            if not low <= value <= high:
                value, rounding = None, None
    cached = spec.physical(row["value"])
    consistent = None
    if value is not None:
        if spec.unit == "boolean":
            consistent = row["value"] in (0, 1) and value == cached
        else:
            consistent = abs(cached - value) <= rounding + 1e-5
    return {
        "value": value,
        "display_value": value,
        "host_normalized": row["value"],
        "cache_consistent": consistent,
        "display_tolerance": rounding,
        "display": display,
    }


def _require_current_cache(spec, row):
    state = _display_state(spec, row)
    if state["cache_consistent"] is not True:
        reason = "stale host cache" if state["cache_consistent"] is False else "unmapped display"
        raise ValueError(
            f"Unsafe Serum baseline for {spec.name}: {reason}; normalized={row['value']}, "
            f"display={state['display']!r}. Build/reload a full preset with offline settings; "
            "do not write cached values to refresh the plugin."
        )
    return state


def _validate_channel(channel):
    if type(channel) is not int or channel < 0:
        raise ValueError("channel must be a nonnegative global channel index")


def _call(conn, action, params=None):
    result = conn.send_command(action, params or {}, 15)
    if not result.get("success"):
        raise RuntimeError(f"{action}: {result}")
    return result


def _native(conn, function, args, write=False):
    return _call(
        conn,
        "native.call",
        {
            "function": function,
            "args": args,
            "allow_write": write,
        },
    )["value"]


def _identity(conn, channel):
    plugin = _native(conn, "plugins.getPluginName", [channel, -1, False, True])
    if plugin not in {"Serum 2", "Serum2"}:
        raise ValueError(f"Expected Serum 2 on channel {channel}, found {plugin!r}")
    name = _native(conn, "channels.getChannelName", [channel, True])
    count = _native(conn, "plugins.getParamCount", [channel, -1, True])
    if type(count) is not int or not 313 <= count <= 10000:
        raise ValueError(f"Unexpected Serum parameter count: {count!r}")
    return {"channel": channel, "plugin": plugin, "channel_name": name, "parameter_count": count}


def _snapshot(conn, channel):
    identity = _identity(conn, channel)
    rows = _call(
        conn,
        "plugins.getParams",
        {
            "index": channel,
            "slot_index": -1,
            "use_global": True,
            "max_params": identity["parameter_count"],
        },
    )["params"]
    by_index = {r["index"]: r for r in rows}
    if len(rows) != identity["parameter_count"] or len(by_index) != len(rows):
        raise ValueError("Incomplete or duplicate Serum parameter catalog")
    for spec in CONTROLS.values():
        row = by_index.get(spec.index)
        if row is None or row["name"] != spec.name:
            raise ValueError(f"Parameter identity changed at {spec.index}: expected {spec.name!r}")
        if not isinstance(row["value"], (int, float)) or not math.isfinite(row["value"]):
            raise ValueError(f"Invalid current value for {spec.name}")
        if not 0 <= row["value"] <= 1:
            raise ValueError(f"Current value outside 0..1 for {spec.name}")
    if _identity(conn, channel) != identity:
        raise ValueError("Channel identity changed while reading Serum")
    names = [(r["index"], r["name"]) for r in sorted(rows, key=lambda r: r["index"])]
    identity["catalog_sha256"] = hashlib.sha256(json.dumps(names).encode()).hexdigest()
    return identity, by_index


def _stopped(conn):
    status = _call(conn, "transport.getStatus")
    if status.get("is_playing") or status.get("is_recording"):
        raise ValueError("Stop playback and recording before applying Serum controls")
    if "is_playing" not in status or "is_recording" not in status:
        raise ValueError("Transport status is incomplete")


def _guard(conn, identity, spec):
    actual = _identity(conn, identity["channel"])
    if actual != {k: v for k, v in identity.items() if k != "catalog_sha256"}:
        raise ValueError("Channel identity changed before write")
    name = _native(conn, "plugins.getParamName", [spec.index, identity["channel"], -1, True])
    if name != spec.name:
        raise ValueError(f"Parameter identity changed before write: {spec.name}")
    _stopped(conn)


def _read(conn, channel, spec):
    row = _call(
        conn,
        "plugins.getParamValue",
        {
            "param_index": spec.index,
            "plugin_index": channel,
            "slot_index": -1,
            "use_global": True,
        },
    )
    if row.get("name") != spec.name:
        raise ValueError(f"Parameter identity changed during readback: {spec.name}")
    value = row.get("value")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid readback for {spec.name}")
    return row


def _write(conn, channel, spec, value):
    _native(conn, "plugins.setParamValue", [value, spec.index, channel, -1, 0, True], True)


def inspect_serum(channel: int) -> dict:
    """Read current displays, separate host caches, and a full catalog fingerprint.

    value/display_value are parsed current displays, or null when the physical
    display map is unknown. host_normalized is never presented as current truth.
    """
    _validate_channel(channel)
    conn = get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-serum.lock", 5):
        return _inspect_locked(conn, channel)


def _inspect_locked(conn, channel):
    """Inspect using an existing connection while the caller holds mcp-serum.lock."""
    identity, rows = _snapshot(conn, channel)
    groups = {}
    stale, unknown = [], []
    for key, spec in CONTROLS.items():
        row = rows[spec.index]
        state = _display_state(spec, row)
        if state["cache_consistent"] is False:
            stale.append(key)
        elif state["cache_consistent"] is None:
            unknown.append(key)
        groups.setdefault(key.split(".")[0], {})[key] = {
            **state,
            "unit": spec.unit,
            "range": list(spec.bounds),
            "parameter": spec.index,
            "name": spec.name,
        }
    return {
        "success": True,
        "identity": identity,
        "groups": groups,
        "stale_controls": stale,
        "unmapped_controls": unknown,
        "calibration": "Serum 2.1.2; runtime version is not exposed by FL's parameter API",
        "limitations": LIMITATIONS,
    }


def apply_serum(channel: int, changes: dict, dry_run: bool = True) -> dict:
    """Preflight natural-unit changes; optional stopped writes with verification/rollback.

    Example: {"env1.decay_ms": 300, "osc_a.fine_cents": -32, "voice.mono": true}.
    The complete change set is validated before any mutation. Returns original
    normalized values and per-control rollback evidence on failure. No retry of
    uncertain writes; inspect the returned report before another apply. Refuses
    stale/unmapped target baselines, including dry runs; use offline preset
    settings when the host cache cannot be verified against the current display.
    """
    _validate_channel(channel)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be boolean")
    if not isinstance(changes, dict) or not 1 <= len(changes) <= len(CONTROLS):
        raise ValueError("changes requires a nonempty object of supported Serum keys")
    targets = {}
    for key, value in changes.items():
        if key not in CONTROLS:
            raise ValueError(f"Unknown Serum control {key!r}; use inspect_serum for supported keys")
        targets[key] = CONTROLS[key].normalize(value)
    conn = get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-serum.lock", 5):
        _stopped(conn)
        identity, rows = _snapshot(conn, channel)
        for key in targets:
            spec = CONTROLS[key]
            _require_current_cache(spec, rows[spec.index])
        planned = [
            {
                "key": k,
                "parameter": CONTROLS[k].index,
                "name": CONTROLS[k].name,
                "requested": changes[k],
                "unit": CONTROLS[k].unit,
                "target": v,
                "before": rows[CONTROLS[k].index]["value"],
            }
            for k, v in targets.items()
        ]
        report = {
            "success": True,
            "status": "preflighted",
            "dry_run": dry_run,
            "identity": identity,
            "changes": planned,
            "applied": [],
            "rollback": [],
            "limitations": LIMITATIONS,
        }
        if dry_run:
            return report
        attempted = []
        try:
            for item in planned:
                spec = CONTROLS[item["key"]]
                if abs(item["before"] - item["target"]) <= TOLERANCE:
                    continue
                _guard(conn, identity, spec)
                current = _read(conn, channel, spec)
                _require_current_cache(spec, current)
                if abs(current["value"] - item["before"]) > TOLERANCE:
                    raise ValueError(f"Control changed since preflight: {item['key']}")
                # Include uncertain writes in rollback even if the command raises.
                attempted.append(item)
                _write(conn, channel, spec, item["target"])
            if attempted:
                time.sleep(SETTLE_SECONDS)
            for item in attempted:
                spec = CONTROLS[item["key"]]
                _guard(conn, identity, spec)
                after = _read(conn, channel, spec)
                report["applied"].append(
                    {
                        "key": item["key"],
                        "after": after["value"],
                        "display": after.get("value_string", "").strip(),
                    }
                )
                if abs(after["value"] - item["target"]) > TOLERANCE:
                    raise RuntimeError(f"Normalized readback mismatch for {item['key']}")
                _require_current_cache(spec, after)
            report["status"] = "completed"
        except Exception as exc:
            report.update(success=False, status="failed", error=str(exc))
            for item in reversed(attempted):
                restoration = {"key": item["key"], "target": item["before"], "success": False}
                try:
                    spec = CONTROLS[item["key"]]
                    _guard(conn, identity, spec)
                    _write(conn, channel, spec, item["before"])
                    time.sleep(SETTLE_SECONDS)
                    _guard(conn, identity, spec)
                    restored = _read(conn, channel, spec)
                    restoration["after"] = restored["value"]
                    restoration["success"] = abs(restoration["after"] - item["before"]) <= TOLERANCE
                    if restoration["success"]:
                        _require_current_cache(spec, restored)
                    if not restoration["success"]:
                        restoration["error"] = "Rollback readback mismatch"
                except Exception as restore_exc:
                    restoration["success"] = False
                    restoration["error"] = str(restore_exc)
                report["rollback"].append(restoration)
            report["rollback_complete"] = all(r["success"] for r in report["rollback"])
        return report
