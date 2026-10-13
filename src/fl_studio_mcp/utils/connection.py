"""Public connection API backed by the single SyncFL MIDI session.

One owner avoids the stale wrapper/transport pairs created by separate caches.
"""

from __future__ import annotations

from fl_studio_mcp.utils import midi_connection

FLConnection = midi_connection.MIDIConnection


def get_connection() -> FLConnection:
    return midi_connection.get_connection()


def reset_connection() -> None:
    midi_connection.reset_connection()
