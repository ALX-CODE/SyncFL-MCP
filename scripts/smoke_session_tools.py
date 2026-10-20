"""Live smoke test for the new session tools (run with the repo venv).

Read-only except for one deliberately idempotent write: it re-sets an effect
slot's wet level to the value it already has, which proves the guarded write
path without changing the mix.
"""

import json
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("FL_USER_DATA_FOLDER",
                      r"C:\Users\alexh\Documents\MUSIC SOFTWARE FOLDERS\Image-Line")
os.environ.setdefault("FL_MCP_MIDI_PORT", "ALX FL Bridge (Send)")
os.environ.setdefault("FL_MCP_INTERACTION_POLICY", "background")
os.environ.setdefault("FL_MCP_TOOL_MODE", "full")
os.environ.setdefault("FL_MCP_ACCESS", "full")

from fl_studio_mcp.utils import session  # noqa: E402


def show(label, value):
    print(f"\n--- {label}")
    print(json.dumps(value, indent=1, default=str)[:1100])


def main():
    show("undo_status", session.undo_status())
    show("plugin_mix read (track 1 slot 6 = bloomCK)",
         session.plugin_mix(1, 6))
    show("track_slots read (track 1)", session.track_slots(1))
    show("clear_dialog", session.clear_dialog())
    # Insert 1 volume event, resolved earlier by fl_automation_target_inspect.
    show("event_inspect (Insert 1 - Volume, 0x70401FC0)",
         session.event_inspect(0x70401FC0))
    show("automate_event preview (no write)",
         session.automate_event(0x70401FC0, value_normalized=0.8))

    # One idempotent write: re-assert the slot's current wet level.
    current = session.plugin_mix(1, 6)["mix"]
    if isinstance(current, (int, float)):
        show(f"plugin_mix idempotent write (re-set to {current})",
             session.plugin_mix(1, 6, current))
    else:
        print("\n--- skipped idempotent write: unreadable mix level", current)


if __name__ == "__main__":
    sys.exit(main())
