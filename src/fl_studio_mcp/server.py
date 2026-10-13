"""Compose the SyncFL server, resources and lifecycle from its tool modules."""

from __future__ import annotations

import importlib
import os
import sys

from fastmcp import FastMCP

from fl_studio_mcp.utils.connection import get_connection, reset_connection
from fl_studio_mcp.utils.tool_lease import FLToolLeaseMiddleware

INSTRUCTIONS = """
SyncFL controls FL Studio through native controller operations, guarded Windows
workflows and offline project/audio utilities. Begin with fl_connection_status
and fl_session_inspect. Consult fl_workflow_catalog for supported routes.

Both interaction modes use the same native/backend APIs and expose the same
tools. Prefer the backend in BOTH modes, including background piano and effect
loading whenever their qualified preconditions fit. Interactive is an additional
permission to use guarded UI when a task has no applicable backend route; it is
not a different engine or a reason to choose clicks instead of native calls.
Background is the default. Keyboard, mouse and focus operations require
FL_MCP_INTERACTION_POLICY=interactive. Never retry an uncertain/dispatched backend
mutation through UI. Native transport, mixer, routing, channels, plugin controls
and supported model operations do not occupy the user's keyboard or mouse.

Use inspection before writes. Exact identities, revisions, expected names and
hashes guard scene changes. A timeout after dispatch means unknown outcome:
inspect state or the receipt before retrying. Batches are not universal rollback
transactions. Preserve unsaved work and source files.

Plugin tools control loaded generators/effects. Named physical settings require
a qualified adapter and verified display mapping; normalized values alone are
not Hz, dB or milliseconds. fl_plugin_controls_inspect/set and plugin scenes
provide precise changes with readback. A host display does not prove DSP output.
Instruments, samples and presets can be loaded using their exact paths/names;
standard loading uses inspected interactive controls. Experimental background
effect loading is restricted to an empty slot on the selected, visibly matching
mixer track. It must not switch tracks. General .fst loading accepts format 0x30,
not a complete 0x40 mixer track. Sample readback cannot independently prove the
original source path. Plugin removal may need a calibrated UI layout.

Live note triggering does not create persistent notes. Piano transactions,
pattern/step workflows and offline FLP editing serve persistent composition.
Prefer fl_background_piano_inspect/write for script-free score replacement on a
saved FL26 project. It uses native panel selection and OLE import, then checks the
saved FLP. Legacy piano preparation uses interactive mode. Playlist file edits preserve the FLP;
add supports full pattern clips, with no arbitrary trimming/stretching or newly
invented audio channels. Inspect the saved project after opening to verify length.

Project open, Save As, normal loading and export can need interactive mode.
Native save only proves host clean-state evidence for the current named project;
it does not prove an exact disk path or byte contents. Keep recovery copies.
Render/record jobs use bounded start/status/stop and persistent receipts.

Offline audio inspection/editing, key estimation, loudness/reference comparison, mix diagnosis,
asset/MIDI discovery and note transforms do not require UI input. Diagnostic mix
plans are proposals until applied. Preserve originals and verify produced files.
fl_audio_key estimates global major/minor candidates with NumPy only; confidence
is a relative share across 24 keys, not calibrated correctness. It does not set tuning.
fl_audio_bpm uses the bundled Rust runtime for tempo candidates and half/double-time
alternatives. Inspect ambiguity; it never changes project tempo or stretches audio.
Use Serum presets and measurements only with an installed, usable plugin.
Capability discovery and CI counts do not prove every host/plugin combination.
"""
mcp = FastMCP(name="SyncFL", instructions=INSTRUCTIONS)


@mcp.resource("fl://status")
def get_fl_status() -> str:
    try:
        reply = get_connection().send_command("bridge.ping", timeout=5)
    except RuntimeError as exc:
        return "Controller unavailable: " + str(exc)
    if reply.get("success") is True:
        return "FL Studio controller is responding"
    return "Controller unavailable: " + str(reply.get("error", "Handshake failed"))


@mcp.resource("fl://project")
def get_project_info() -> dict:
    try:
        reply = get_connection().send_command("transport.getStatus")
        if reply.get("success") is not True:
            return {"error": reply.get("error", "No transport acknowledgement")}
        return {
            key: reply[key]
            for key in ("is_playing", "is_recording", "position", "loop_mode")
            if key in reply
        }
    except Exception as exc:
        return dict(error=str(exc))


@mcp.tool()
def fl_connect() -> str:
    """Open a fresh dedicated MIDI session and verify the live controller handshake."""
    from fl_studio_mcp.utils.native_midi import initialize_native_midi

    startup = initialize_native_midi()
    if startup.get("enabled") and not startup.get("success"):
        return "Native MIDI endpoint setup failed: " + str(startup.get("error"))
    reset_connection()
    try:
        session = get_connection()
        session.ensure_connected()
        reply = session.send_command("bridge.ping", timeout=5)
    except RuntimeError as exc:
        return "Connection failed: " + str(exc)
    if reply.get("success") is not True:
        return "MIDI opened; controller handshake failed: " + str(reply.get("error"))
    return f"FL controller protocol {reply.get('protocol')} is responding"


@mcp.tool()
def fl_connection_status() -> dict:
    """Report endpoint availability separately from a verified live controller response."""
    session = get_connection()
    try:
        session.ensure_connected()
    except RuntimeError:
        pass
    state = session.get_status()
    ping = session.send_command("bridge.ping", timeout=5) if session.is_connected else {}
    return dict(
        connected=ping.get("success") is True,
        transport_open=state.get("connected", False),
        handshake=ping,
        port_name=state.get("port_name"),
        transport_kind=state.get("transport_kind"),
        available_ports=state.get("available_ports", []),
        error=state.get("error") or ping.get("error"),
    )


REGISTRARS = [
    ("transport", "transport"),
    ("mixer", "mixer"),
    ("mixer_controls", "mixer_control"),
    ("channels", "channel"),
    ("plugins", "plugin"),
    ("piano_roll", "piano_roll"),
    ("advanced", "advanced"),
    ("serum", "serum"),
    ("workflow", "workflow"),
    ("studio_files", "studio_file"),
    ("production", "production"),
    ("audio", "audio"),
    ("automation_targets", "automation_target"),
    ("operator", "operator"),
    ("routing", "routing"),
    ("studio_ui", "studio_ui"),
    ("plugin_state", "plugin_state"),
    ("plugin_adapters", "plugin_adapter"),
    ("project_native", "project_native"),
    ("background_loading", "background_loading"),
    ("background_piano", "background_piano"),
    ("recording_state", "recording_state"),
    ("recording_jobs", "recording_job"),
    ("playlist_import", "playlist_import"),
    ("mix_diagnostics", "mix_diagnostics"),
    ("metering", "metering"),
    ("midi_reference", "midi_reference"),
    ("note_transforms", "note_transform"),
]

for _module, _name in REGISTRARS:
    _tools = importlib.import_module("fl_studio_mcp.tools." + _module)
    getattr(_tools, "register_" + _name + "_tools")(mcp)
mcp.add_middleware(FLToolLeaseMiddleware())


def main():
    """Initialize optional local runtimes before serving MCP over stdio."""
    from fl_studio_mcp.discovery import create_discovery_server
    from fl_studio_mcp.utils.audio_files import initialize_audio_runtime
    from fl_studio_mcp.utils.native_midi import initialize_native_midi

    mode = os.environ.get("FL_MCP_TOOL_MODE", "discovery").strip().lower()
    if mode not in {"discovery", "full"}:
        raise ValueError("FL_MCP_TOOL_MODE must be 'discovery' or 'full'")
    server = create_discovery_server(mcp) if mode == "discovery" else mcp
    initialize_audio_runtime()
    outcome = initialize_native_midi()
    if not outcome.get("success"):
        print("Native MIDI startup: " + str(outcome.get("error")), file=sys.stderr)
    server.run()


if __name__ == "__main__":
    main()
