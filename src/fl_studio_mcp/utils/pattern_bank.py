"""Append-only, verified phrase banks using the live MIDI and piano-roll bridge.

No FLP rewriting, preset changes, or fixed post-trigger sleep. The shared bridge
opens the selected channel's roll and selects ComposeWithLLM automatically on Windows.
"""

from __future__ import annotations

import time
from pathlib import Path

from fl_studio_mcp.utils.connection import get_connection
from fl_studio_mcp.utils.note_protocol import normalize_requests
from fl_studio_mcp.utils.piano_bridge import piano_session
from fl_studio_mcp.utils.protocol import atomic_json


def validate_bank(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 64:
        raise ValueError("Provide 1..64 patterns")
    names = set()
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or name in names:
            raise ValueError("Pattern names must be unique, nonempty, at most 100 characters")
        names.add(name)
        notes = item.get("notes")
        if not isinstance(notes, list) or not 1 <= len(notes) <= 4096:
            raise ValueError("Each pattern requires 1..4096 notes")
        normalize_requests([{"action": "add_notes", "notes": notes}], 96000)


def quantize_notes(notes, ppq):
    normalized = normalize_requests([{"action": "add_notes", "notes": notes}], ppq)[0]["notes"]
    return [
        dict(n, time=n["time_ticks"] / ppq, duration=n["length_ticks"] / ppq) for n in normalized
    ]


def verify_notes(state, notes):
    want = sorted(
        (n["midi"], n["time_ticks"], n["length_ticks"], round(n["velocity"] * 128)) for n in notes
    )
    got = sorted(
        (n["midi"], n["time_ticks"], n["length_ticks"], round(n["velocity"] * 128))
        for n in state["notes"]
    )
    if want != got:
        raise RuntimeError(
            "Piano-roll readback does not match requested notes; stopped without retry"
        )


def write_pattern_bank(items, channel, expected_channel, receipt_file, timeout=5):
    """Append new patterns only; on failure keep a receipt and never replay notes."""
    validate_bank(items)
    if type(channel) is not int or channel < 0 or not expected_channel:
        raise ValueError("Require channel index and exact channel name")
    receipt_path = Path(receipt_file).resolve()
    if receipt_path.exists():
        raise ValueError("Receipt already exists: inspect it instead of replaying this bank")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()

    def command(action, params):
        result = conn.send_command(action, params)
        if not result.get("success", False):
            raise RuntimeError(str(result))
        return result

    def native(function, args=(), write=False):
        return command(
            "native.call", dict(function=function, args=list(args), allow_write=write)
        ).get("value")

    receipt = dict(success=False, patterns=[], started_at=time.time())
    start = time.monotonic()
    with piano_session() as bridge:
        bridge.timeout = timeout
        try:
            if native("channels.getChannelName", [channel, True]) != expected_channel:
                raise RuntimeError("Channel identity changed")
            if native("transport.isPlaying"):
                raise RuntimeError("Stop playback before writing a bank")
            native("channels.selectOneChannel", [channel, True], True)
            # Read fresh state first: automatic setup does not change notes.
            ppq = bridge.exchange()["state"]["ppq"]
            first = native("patterns.patternCount") + 1
            capacity = native("patterns.patternMax", [], True)
            if first + len(items) - 1 > capacity:
                raise ValueError("Pattern limit exceeded")
            receipt["first_pattern"] = first
            atomic_json(receipt_path, receipt)
            for offset, item in enumerate(items):
                pattern = first + offset
                row = dict(pattern=pattern, name=item["name"], status="selecting")
                receipt["patterns"].append(row)
                atomic_json(receipt_path, receipt)
                tick = time.monotonic()
                native("patterns.jumpToPattern", [pattern], True)
                if native("patterns.patternNumber", [], True) != pattern:
                    raise RuntimeError("Pattern selection did not settle")
                # Refresh proves the new roll is empty and prevents overwriting user notes.
                empty_state = bridge.exchange()["state"]
                if empty_state["noteCount"]:
                    raise RuntimeError("Target pattern is not empty; stopped")
                notes = quantize_notes(item["notes"], ppq)
                row["status"] = "writing"
                atomic_json(receipt_path, receipt)
                state = bridge.exchange([dict(action="add_notes", notes=notes)], empty_state)[
                    "state"
                ]
                verify_notes(state, notes)
                native("patterns.setPatternName", [pattern, item["name"]], True)
                if native("patterns.getPatternName", [pattern]) != item["name"]:
                    raise RuntimeError("Pattern name did not settle")
                row.update(
                    status="verified",
                    notes=len(notes),
                    elapsed_ms=round((time.monotonic() - tick) * 1000),
                )
                atomic_json(receipt_path, receipt)
            native("patterns.jumpToPattern", [first], True)
            receipt["success"] = True
        except Exception as exc:
            receipt["error"] = str(exc)
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - start, 3)
            atomic_json(receipt_path, receipt)
    return receipt
