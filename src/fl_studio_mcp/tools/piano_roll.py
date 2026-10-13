"""Persistent note tools using the shared verified piano-roll transaction backend."""

import json
import time

from fl_studio_mcp.utils.fl_trigger import get_trigger
from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.piano_bridge import edit_notes, piano_session


def _midi_to_note_name(midi):
    """FL's displayed octave convention: MIDI note 60 is C5."""
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12}"


def register_piano_roll_tools(mcp):
    @mcp.tool()
    def fl_write_pattern_bank(
        items: list[dict], channel: int, expected_channel: str, receipt_file: str
    ) -> dict:
        """Unavailable in backend-only SyncFL. Legacy script-based pattern-bank writing is
        unavailable. Use native background piano tools or saved-FLP editing.
        """
        from fl_studio_mcp.utils.pattern_bank import write_pattern_bank

        return write_pattern_bank(items, channel, expected_channel, receipt_file)

    @mcp.tool()
    def fl_send_notes(notes: list[dict], mode: str = "add", auto_trigger: bool = True) -> dict:
        """Unavailable in backend-only SyncFL. Legacy score delivery is unavailable. Use
        fl_background_piano_write for persistent notes.
        """
        if mode not in ("add", "replace"):
            raise ValueError("mode must be add or replace")
        requests = [{"action": "clear"}] if mode == "replace" else []
        requests.append({"action": "add_notes", "notes": notes})
        return edit_notes(requests, auto_trigger)

    @mcp.tool()
    def fl_send_chord(
        midi_notes: list[int],
        time: float = 0,
        duration: float = 1,
        velocity: float = 0.8,
        auto_trigger: bool = True,
    ) -> dict:
        """Write a chord through the same verified note transaction."""
        return edit_notes(
            [
                {
                    "action": "add_notes",
                    "notes": [
                        {"midi": n, "time": time, "duration": duration, "velocity": velocity}
                        for n in midi_notes
                    ],
                }
            ],
            auto_trigger,
        )

    @mcp.tool()
    def fl_delete_notes(notes: list[dict], auto_trigger: bool = True) -> dict:
        """Delete all matches by midi and time/time_ticks, preserving other notes."""
        return edit_notes([{"action": "delete_notes", "notes": notes}], auto_trigger)

    @mcp.tool()
    def fl_clear_piano_roll(auto_trigger: bool = True) -> dict:
        """Clear notes from the inspected roll; preserve markers and record recovery data."""
        return edit_notes([{"action": "clear"}], auto_trigger)

    @mcp.tool()
    def fl_get_piano_roll_state(fresh: bool = True) -> dict:
        """Unavailable in backend-only SyncFL. Legacy script-based piano inspection is
        unavailable. Use fl_background_piano_inspect.
        """
        if fresh:
            with piano_session() as bridge:
                state = bridge.exchange()["state"]
        else:
            path = get_fl_settings_dir() / "Piano roll scripts" / "piano_roll_state.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["cached"] = True
            state["age_seconds"] = time.time() - state.get("exported_at", 0)
        for note in state.get("notes", []):
            note["note_name"] = _midi_to_note_name(note["midi"])
        return state

    @mcp.tool()
    def fl_clear_request_queue() -> dict:
        """Acknowledge completed work. Refuse to discard unresolved requests/recovery data."""
        with piano_session() as bridge:
            pending = bridge.read_queue()
            if pending == []:
                return {"success": True, "status": "empty"}
            result = bridge.result(pending.get("id")) if isinstance(pending, dict) else None
            if not result or result.get("outcome") not in ("verified", "restored", "not_applied"):
                raise RuntimeError("Unresolved request retained; inspect before cancellation")
            bridge.acknowledge(result)
            return {"success": True, "status": "acknowledged", "id": result["id"]}

    @mcp.tool()
    def fl_trigger_script() -> dict:
        """Execute a prepared request, or refresh an empty queue; verify the matching response."""
        with piano_session() as bridge:
            return bridge.trigger_pending()

    @mcp.tool()
    def fl_get_piano_roll_info() -> dict:
        """Report protocol, paths and pending work without triggering FL."""
        directory = get_fl_settings_dir() / "Piano roll scripts"
        trigger = get_trigger()
        return {
            "protocol": 3,
            "platform": trigger.platform,
            "auto_trigger_supported": trigger.is_supported,
            "trigger_keystroke": None if trigger.platform == "Windows" else trigger.keystroke,
            "activation": (
                "explicit_compose_menu" if trigger.platform == "Windows" else "last_script_shortcut"
            ),
            "scripts_dir": str(directory),
            "request_file": str(directory / "mcp_request.json"),
            "state_file": str(directory / "piano_roll_state.json"),
            "verification_file": str(directory / "mcp-verification.json"),
            "verification_pending": (directory / "mcp-verification.json").exists(),
            "companion_installed": (directory / "mcp_note_protocol.py").is_file(),
            "readiness": "Use fl_piano_roll_prepare or a fresh state query to verify the bridge",
        }
