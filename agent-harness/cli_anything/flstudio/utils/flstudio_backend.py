from fl_studio_mcp.utils.midi_connection import get_connection


def call(action, params=None, timeout=20):
    return get_connection().send_command(action, params or {}, timeout)
