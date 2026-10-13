"""Backend-only execution; desktop input is never an available fallback."""

from __future__ import annotations

import os

# Retained for reading old registrations; its value no longer grants UI access.
ENVIRONMENT_VARIABLE = "FL_MCP_INTERACTION_POLICY"
DEFAULT_MODE = "background"

# Operator opt-in for FL's own window/editor API (ui.*, showEditor,
# globalTransport). These are native calls, never desktop input. Unknown values
# keep the default gating.
ACCESS_VARIABLE = "FL_MCP_ACCESS"
ACCESS_LEVELS = ("backend", "full")


def native_access():
    value = os.environ.get(ACCESS_VARIABLE, "backend").strip().lower()
    return "full" if value == "full" else "backend"


FULL_ACCESS_INSTRUCTIONS = """
Full native access is enabled (FL_MCP_ACCESS=full). fl_native_call and native
batch steps may call every catalogued FL API function, including ui.*, editor and
window commands, transport.globalTransport, undo history and raw REC events.
Mutations still need allow_write=true. Focus-routed ui.* calls (enter, copy, up,
...) act on FL's focused window: read ui.getFocusedFormCaption first. Desktop
mouse/keyboard input remains unavailable.
"""


def with_access_note(instructions):
    """Server instructions are fixed at startup, when the registration env is known."""
    return instructions + FULL_ACCESS_INSTRUCTIONS if native_access() == "full" else instructions

# Only individually known observation endpoints are safe. Combined batches and
# unknown helper methods are deliberately excluded, even if labelled as reads.
_OBSERVATION_PARAMETERS = {
    "window_management": {"action", "processName", "title", "className", "handle"},
    "ui_snapshot": {"windowHandle", "mode", "maxDepth", "timeoutMs"},
    "screenshot_control": {
        "windowHandle", "target", "regionX", "regionY", "regionWidth", "regionHeight",
        "annotate", "includeImage", "imageFormat", "outputMode", "outputPath",
    },
}


def interaction_mode():
    """Old environment settings cannot enable desktop input."""
    return DEFAULT_MODE


def require_desktop_input(operation):
    raise RuntimeError(
        f"{operation} is unsupported in background mode: SyncFL is backend-only. "
        "Desktop input and focus-dependent automation are disabled. "
        "Use a supported native/backend operation; there is no interactive mode."
    )


def check_windows_call(name, params, *, read_only=False):
    """Reject input before helper dispatch, including within observation sessions."""
    allowed = _OBSERVATION_PARAMETERS.get(name)
    observation = allowed is not None and not (set(params) - allowed)
    if name == "window_management":
        observation = observation and params.get("action") == "find"
    if observation:
        return
    if read_only:
        raise RuntimeError(
            f"Windows {name} is unsupported in a read-only session; desktop input, "
            "focus, activation and window changes are forbidden. No input was dispatched."
        )
    require_desktop_input(f"Windows {name}")


def policy_metadata():
    from fl_studio_mcp.utils.native_interaction_policy import (
        DESKTOP_NATIVE_FUNCTIONS,
        GLOBAL_ONLY_TRANSPORT_FUNCTIONS,
    )

    full = native_access() == "full"
    report = dict(
        mode=DEFAULT_MODE,
        default=DEFAULT_MODE,
        environment_variable=ENVIRONMENT_VARIABLE,
        native_access=native_access(),
        access_environment_variable=ACCESS_VARIABLE,
        native_ui_functions_allowed=full,
        desktop_input_allowed=False,
        backend_apis_available=True,
        preferred_execution="native_backend",
        backend_only=True,
        available_modes=[DEFAULT_MODE],
        ui_usage="Observation only. Desktop input is unavailable; never request a mode switch.",
        automatic_interactive_fallback=False,
        ui_action_preview_allowed=True,
        background_observations="Window metadata, UI snapshots, screenshots and OCR only",
        native_commands_affected=dict(
            explicit_functions=sorted(DESKTOP_NATIVE_FUNCTIONS),
            other_functions="ui.* outside the background UI allowlist",
            model_commands_affected=True,
            global_only_transport_functions=sorted(GLOBAL_ONLY_TRANSPORT_FUNCTIONS),
            transport_requirement="native.call with two positional integers and flags=8",
        ),
    )
    if full:
        report.update(
            native_commands_affected=dict(
                explicit_functions=[], other_functions="none",
                model_commands_affected=False, global_only_transport_functions=[],
                transport_requirement="none",
            ),
            native_ui_usage=(
                "Full native access: every advertised FL API function, including ui.* "
                "and editor/window commands, runs through native.call. Focus-routed "
                "calls (ui.enter, ui.copy, ui.up, ...) act on FL's focused window; "
                "check ui.getFocusedFormCaption first. Desktop input stays unavailable."
            ),
        )
    return report
