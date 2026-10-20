"""Backend primitives the controller advertises but no typed tool exposed.

Everything here is native controller work: no desktop input, no focus changes.
Each setter preflights its advertised signature, dispatches once and then reads
the result back. Uncertain writes are never retried and are never silently
reported as successes.

Value scale note: FL's native event values are 30-bit fixed point
(``1.0 == 1 << 30``). That was measured, not assumed: the Insert 1 volume event
reads 858993459, which is exactly ``2**30 * 0.8`` and 0.8 is FL's unity fader
value. Callers may still pass ``value_raw`` and always see requested-versus-actual
in the receipt.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.plugin_state import (
    _call,
    _native,
)
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

EVENT_SCALE = 1 << 30
# getEventValue returns 30-bit fixed point, but getAutoSmoothEventValue reports a
# different, 14-bit scale (16384 == 1.0). They are not interchangeable.
AUTO_SMOOTH_SCALE = 16384

_SIGNATURES = {
    "general.undo": ([], "write"),
    "general.getUndoHistoryCount": ([], "read"),
    "general.getUndoHistoryPos": ([], "read"),
    "general.getUndoHistoryLast": ([], "read"),
    "ui.closeActivePopupMenu": ([], "write"),
    "ui.isInPopupMenu": ([], "read"),
    "mixer.getPluginMixLevel": (["index", "plugIndex"], "read"),
    "mixer.setPluginMixLevel": (["index", "plugIndex", "value", "pickupMode"], "write"),
    "mixer.isTrackSlotsEnabled": (["index"], "read"),
    "mixer.enableTrackSlots": (["index", "value"], "write"),
    "mixer.getEventValue": (["index", "value", "smoothTarget"], "read"),
    "mixer.getAutoSmoothEventValue": (["index", "locked"], "read"),
    "mixer.getEventIDName": (["index", "shortname"], "read"),
    "mixer.automateEvent": (["index", "value", "flags", "speed", "isIncrement", "res"], "write"),
    "channels.getChannelName": (["index", "useGlobalIndex"], "read"),
    "channels.setChannelName": (["index", "name", "useGlobalIndex"], "write"),
    "patterns.getPatternName": (["index"], "read"),
    "patterns.setPatternName": (["index", "name"], "write"),
    "patterns.getPatternColor": (["index"], "read"),
    "patterns.setPatternColor": (["index", "color"], "write"),
}


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------
def _integer(value, label, high=0xFFFFFFFF):
    if type(value) is not int or not 0 <= value <= high:
        raise ValueError(f"{label} must be an integer in 0..{high}")
    return value


def _text(value, label, limit=255):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    return value


def _number(value, label, low=0.0, high=1.0):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number in {low}..{high}")
    return float(value)


def _flag(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _receipt_path(receipt_file, apply):
    if not apply and receipt_file is None:
        return None
    if not isinstance(receipt_file, str):
        raise ValueError("Apply requires a new absolute JSON receipt_file")
    path = Path(receipt_file)
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise ValueError("receipt_file must be an absolute JSON path")
    if path.exists():
        raise ValueError("receipt_file must not already exist")
    return path


def _write_receipt(path, payload):
    if path is None:
        return
    payload = dict(payload, receipt=str(path), recorded_at=time.time())
    atomic_json(path, payload)


# ---------------------------------------------------------------------------
# capability preflight
# ---------------------------------------------------------------------------
def _capabilities(conn, needed):
    native = _call(conn, "bridge.capabilities", {}).get("native", {})
    if not isinstance(native, dict):
        raise RuntimeError("Controller omits its native capability catalog")
    for name in needed:
        arguments, kind = _SIGNATURES[name]
        spec = native.get(name, {})
        if (
            not isinstance(spec, dict)
            or list(spec.get("arguments") or []) != arguments
            or spec.get("kind") != kind
        ):
            raise RuntimeError(f"Missing or unsupported advertised native signature: {name}")


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------
def undo_status(connection=None):
    """Read FL's undo counters without changing anything."""
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        _capabilities(
            conn,
            ["general.getUndoHistoryCount", "general.getUndoHistoryPos",
             "general.getUndoHistoryLast"],
        )
        count, position, last = _native(
            conn,
            [
                ("general.getUndoHistoryCount", []),
                ("general.getUndoHistoryPos", []),
                ("general.getUndoHistoryLast", []),
            ],
        )
    return dict(
        success=True,
        status="inspected",
        undo_count=count if type(count) is int else None,
        undo_position=position if type(position) is int else None,
        last_action=last if isinstance(last, str) else None,
        last_action_raw=None if isinstance(last, str) else last,
        can_undo=bool(type(position) is int and position >= 0),
    )


def undo(connection=None):
    """Step FL's own undo stack back once and report the new position.

    Uses general.undo, which is in the ordinary (non-full-access) native set.
    """
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 10):
        _capabilities(conn, ["general.undo", "general.getUndoHistoryPos"])
        before = undo_status(connection=conn)
        response = _call(conn, "native.call", {"function": "general.undo", "args": [],
                                              "allow_write": True})
        after_count, after_position, after_last = _native(
            conn,
            [
                ("general.getUndoHistoryCount", []),
                ("general.getUndoHistoryPos", []),
                ("general.getUndoHistoryLast", []),
            ],
        )
    return dict(
        success=True,
        status="undo_sent",
        before=before,
        undo_count=after_count if type(after_count) is int else None,
        undo_position=after_position if type(after_position) is int else None,
        last_action=after_last if isinstance(after_last, str) else None,
        last_action_raw=None if isinstance(after_last, str) else after_last,
        transport=response.get("transport"),
        note="FL re-renders the undone state; inspect the target before the next write.",
    )


# ---------------------------------------------------------------------------
# dialog recovery
# ---------------------------------------------------------------------------
def clear_dialog(connection=None):
    """Close FL's own popup menu / modal menu without synthesizing desktop input.

    ui.closeActivePopupMenu is a focus-adjacent write, so it is only advertised
    when the server runs with FL_MCP_ACCESS=full. It never presses keys or moves
    the mouse, and it reports the observed popup state before and after.
    """
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        _capabilities(conn, ["ui.isInPopupMenu", "ui.closeActivePopupMenu"])
        (before,) = _native(conn, [("ui.isInPopupMenu", [])])
        response = _call(
            conn,
            "native.call",
            {"function": "ui.closeActivePopupMenu", "args": [], "allow_write": True},
        )
        (after,) = _native(conn, [("ui.isInPopupMenu", [])])
    was_open = before is True or (type(before) is int and before == 1)
    still_open = after is True or (type(after) is int and after == 1)
    return dict(
        success=True,
        status="cleared" if was_open and not still_open else "no_change",
        popup_before=was_open,
        popup_after=still_open,
        cleared=was_open and not still_open,
        transport=response.get("transport"),
    )


# ---------------------------------------------------------------------------
# per-slot plugin wet/dry and slot rack enable
# ---------------------------------------------------------------------------
def plugin_mix(track, slot, mix=None, connection=None):
    """Read (mix=None) or set one effect slot's wet/dry level in 0..1."""
    _integer(track, "track", 100000)
    if type(slot) is not int or not 0 <= slot <= 9:
        raise ValueError("slot must be 0..9 for a mixer effect slot")
    if mix is not None:
        _number(mix, "mix")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 10):
        needed = ["mixer.getPluginMixLevel"]
        if mix is not None:
            needed.append("mixer.setPluginMixLevel")
        _capabilities(conn, needed)
        before = _native(conn, [("mixer.getPluginMixLevel", [track, slot])])[0]
        if mix is None:
            return dict(success=True, status="inspected", track=track, slot=slot,
                        mix=before, changed=False)
        response = _call(
            conn,
            "native.call",
            {
                "function": "mixer.setPluginMixLevel",
                "args": [track, slot, mix, 0],
                "allow_write": True,
            },
        )
        after = _native(conn, [("mixer.getPluginMixLevel", [track, slot])])[0]
    return dict(
        success=True,
        status="set_verified" if after == mix else "set_unconfirmed",
        track=track,
        slot=slot,
        requested=mix,
        before=before,
        mix=after,
        changed=after != before,
        transport=response.get("transport"),
    )


def track_slots(track, enabled=None, connection=None):
    """Read (enabled=None) or set a mixer strip's whole effect rack on/off."""
    _integer(track, "track", 100000)
    if enabled is not None:
        _flag(enabled, "enabled")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 10):
        needed = ["mixer.isTrackSlotsEnabled"]
        if enabled is not None:
            needed.append("mixer.enableTrackSlots")
        _capabilities(conn, needed)
        before = _native(conn, [("mixer.isTrackSlotsEnabled", [track])])[0]
        if enabled is None:
            return dict(success=True, status="inspected", track=track,
                        enabled=bool(before), changed=False)
        response = _call(
            conn,
            "native.call",
            {"function": "mixer.enableTrackSlots", "args": [track, enabled],
             "allow_write": True},
        )
        after = _native(conn, [("mixer.isTrackSlotsEnabled", [track])])[0]
    return dict(
        success=True,
        status="set_verified" if bool(after) == enabled else "set_unconfirmed",
        track=track,
        requested=enabled,
        before=bool(before),
        enabled=bool(after),
        changed=bool(after) != bool(before),
        transport=response.get("transport"),
    )


# ---------------------------------------------------------------------------
# automation events
# ---------------------------------------------------------------------------
def _decode_event(raw, scale=EVENT_SCALE):
    if type(raw) is not int:
        return None
    return round(raw / scale, 9)


def event_inspect(event_id, connection=None):
    """Read one native automation event: its labels and its current value."""
    _integer(event_id, "event_id")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        _capabilities(
            conn,
            ["mixer.getEventIDName", "mixer.getEventValue", "mixer.getAutoSmoothEventValue"],
        )
        full, short, raw, smooth = _native(
            conn,
            [
                ("mixer.getEventIDName", [event_id, 0]),
                ("mixer.getEventIDName", [event_id, 1]),
                ("mixer.getEventValue", [event_id, 0x7FFFFFFF, 1]),
                ("mixer.getAutoSmoothEventValue", [event_id, 1]),
            ],
        )
    resolved = isinstance(full, str) and not full.startswith("Unknown event")
    return dict(
        success=True,
        status="inspected",
        event_id=event_id,
        event_id_hex=hex(event_id),
        event_name=full if isinstance(full, str) else None,
        event_short_name=short if isinstance(short, str) else None,
        resolved=resolved,
        value_raw=raw if type(raw) is int else None,
        value_normalized=_decode_event(raw),
        auto_smooth_raw=smooth if type(smooth) is int else None,
        auto_smooth_normalized=_decode_event(smooth, AUTO_SMOOTH_SCALE),
        auto_smooth_scale_note="getAutoSmoothEventValue reports 16384 == 1.0, not 2**30.",
        value_scale_note=(
            "value_normalized is raw / 2**30 (measured on the Insert 1 volume event). "
            "Confirm with a readback before relying on the physical meaning."
        ) if resolved else "Unresolved event id; do not automate it.",
    )


def automate_event(
    event_id,
    value_normalized=None,
    value_raw=None,
    flags=0,
    speed=0,
    is_increment=0,
    apply=False,
    receipt_file=None,
    connection=None,
):
    """Preview/apply one native automation event write, then read it back.

    ``mixer.automateEvent`` is catalogued as a full-access function, so the
    controller only accepts it when the server runs with FL_MCP_ACCESS=full.
    Supply exactly one of value_normalized (0..1) or value_raw (native units).
    Preview is the default; apply needs a new absolute JSON receipt. The write is
    dispatched once, then read back, and requested-versus-actual is recorded.
    """
    _integer(event_id, "event_id")
    if (value_normalized is None) == (value_raw is None):
        raise ValueError("Supply exactly one of value_normalized or value_raw")
    _integer(flags, "flags", 0xFFFFFFFF)
    _integer(speed, "speed", 0xFFFFFFFF)
    _integer(is_increment, "is_increment", 1)
    _flag(apply, "apply")
    path = _receipt_path(receipt_file, apply)
    raw = (
        int(round(_number(value_normalized, "value_normalized") * EVENT_SCALE))
        if value_normalized is not None
        else _integer(value_raw, "value_raw", 0xFFFFFFFF)
    )

    conn = connection if connection is not None else get_connection()
    lock_seconds = 15 if apply else 5
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", lock_seconds):
        _capabilities(conn, ["mixer.getEventIDName", "mixer.getEventValue"])
        if apply:
            _capabilities(conn, ["mixer.automateEvent"])
        full, short, before = _native(
            conn,
            [
                ("mixer.getEventIDName", [event_id, 0]),
                ("mixer.getEventIDName", [event_id, 1]),
                ("mixer.getEventValue", [event_id, 0x7FFFFFFF, 1]),
            ],
        )
        if not isinstance(full, str) or full.startswith("Unknown event"):
            raise ValueError(
                f"Event {hex(event_id)} is not a resolved host event; refusing to automate it"
            )
        payload = dict(
            action="automate_event",
            event_id=event_id,
            event_id_hex=hex(event_id),
            event_name=full,
            event_short_name=short if isinstance(short, str) else None,
            value_raw_requested=raw,
            value_normalized_requested=round(raw / EVENT_SCALE, 9),
            value_raw_before=before if type(before) is int else None,
            flags=flags,
            speed=speed,
            is_increment=is_increment,
        )
        if not apply:
            return dict(
                success=True,
                status="preview",
                applied=False,
                **payload,
                note="Preview only. Re-run with apply=True and a new receipt_file.",
            )
        response = _call(
            conn,
            "native.call",
            {
                "function": "mixer.automateEvent",
                "args": [event_id, raw, flags, speed, is_increment],
                "allow_write": True,
            },
        )
        after = _native(conn, [("mixer.getEventValue", [event_id, 0x7FFFFFFF, 1])])[0]
    verified = type(after) is int and after == raw
    payload.update(
        value_raw_after=after if type(after) is int else None,
        value_normalized_after=_decode_event(after),
        verified=verified,
        transport=response.get("transport"),
    )
    _write_receipt(path, dict(payload, outcome="verified" if verified else "unconfirmed"))
    return dict(
        success=True,
        status="verified" if verified else "applied_unconfirmed",
        applied=True,
        **payload,
        note=(
            "Readback matched the requested raw value."
            if verified
            else "Readback did not match. FL may quantize or ignore this event; "
            "inspect the receipt and the live control before writing again."
        ),
    )


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------
def _rename(get_fn, set_fn, index, name, args_read, args_write, label, high, connection):
    _integer(index, label + " index", high)
    _text(name, label + " name")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 10):
        _capabilities(conn, [get_fn, set_fn])
        before = _native(conn, [(get_fn, args_read(index))])[0]
        response = _call(
            conn,
            "native.call",
            {"function": set_fn, "args": args_write(index, name), "allow_write": True},
        )
        after = _native(conn, [(get_fn, args_read(index))])[0]
    return dict(
        success=True,
        status="renamed" if after == name else "rename_unconfirmed",
        index=index,
        requested=name,
        before=before if isinstance(before, str) else None,
        name=after if isinstance(after, str) else None,
        changed=before != after,
        transport=response.get("transport"),
    )


def rename_channel(index, name, connection=None):
    """Rename a Channel Rack channel through the native setter."""
    return _rename(
        "channels.getChannelName",
        "channels.setChannelName",
        index,
        name,
        lambda i: [i, True],
        lambda i, n: [i, n, True],
        "channel",
        100000,
        connection,
    )


def rename_pattern(index, name, connection=None):
    """Rename a pattern through the native setter."""
    return _rename(
        "patterns.getPatternName",
        "patterns.setPatternName",
        index,
        name,
        lambda i: [i],
        lambda i, n: [i, n],
        "pattern",
        9999,
        connection,
    )


def pattern_color(index, color=None, connection=None):
    """Read (color=None) or set a pattern's native color word."""
    _integer(index, "pattern index", 9999)
    if color is not None:
        _integer(color, "color")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 10):
        needed = ["patterns.getPatternColor"]
        if color is not None:
            needed.append("patterns.setPatternColor")
        _capabilities(conn, needed)
        before = _native(conn, [("patterns.getPatternColor", [index])])[0]
        if color is None:
            return dict(success=True, status="inspected", index=index, color=before,
                        changed=False)
        response = _call(
            conn,
            "native.call",
            {"function": "patterns.setPatternColor", "args": [index, color],
             "allow_write": True},
        )
        after = _native(conn, [("patterns.getPatternColor", [index])])[0]
    return dict(
        success=True,
        status="set_verified" if after == color else "set_unconfirmed",
        index=index,
        requested=color,
        before=before,
        color=after,
        changed=after != before,
        transport=response.get("transport"),
    )
