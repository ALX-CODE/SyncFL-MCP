"""Explicit desktop-input opt-in; native model operations remain available."""

from __future__ import annotations

import os

ENVIRONMENT_VARIABLE = "FL_MCP_INTERACTION_POLICY"
DEFAULT_MODE = "background"

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
    """Read on every boundary so an invalid or changed setting cannot fail open."""
    mode = os.environ.get(ENVIRONMENT_VARIABLE, DEFAULT_MODE)
    if mode not in ("background", "interactive"):
        raise ValueError(f"{ENVIRONMENT_VARIABLE} must be 'background' or 'interactive'")
    return mode


def require_desktop_input(operation):
    if interaction_mode() != "interactive":
        raise RuntimeError(
            f"{operation} is unsupported in background mode because it requires desktop "
            "input, focus or window activation. Use a native/background operation, or "
            f"explicitly set {ENVIRONMENT_VARIABLE}=interactive to allow desktop input. "
            "No interactive fallback was attempted."
        )


def check_windows_call(name, params, *, read_only=False):
    """Reject input before helper dispatch, including within observation sessions."""
    mode = interaction_mode()
    if mode == "interactive" and not read_only:
        return
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

    mode = interaction_mode()
    return dict(
        mode=mode,
        default=DEFAULT_MODE,
        environment_variable=ENVIRONMENT_VARIABLE,
        desktop_input_allowed=mode == "interactive",
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
