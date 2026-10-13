"""Block focus-dependent native UI commands before publishing a MIDI request.

Model changes (transport, notes, routing and parameter setters) remain background
operations. Native UI actions can still activate an editor or act on the current
focused control even though they do not synthesize Windows input.
"""

from __future__ import annotations

from fl_studio_mcp.utils.interaction_policy import interaction_mode, require_desktop_input

MAX_BATCH_DEPTH = 8
MAX_BATCH_STEPS = 256

# Explicit observations keep future unknown ui.* additions from silently gaining
# permission to operate the foreground. These match the packaged native catalog.
BACKGROUND_UI_FUNCTIONS = frozenset({
    "ui.getFocusedNodeCaption", "ui.getFocusedNodeFileType", "ui.isBrowserAutoHide",
    "ui.isInPopupMenu", "ui.isClosing", "ui.isMetronomeEnabled", "ui.isStartOnInputEnabled",
    "ui.isPrecountEnabled", "ui.isLoopRecEnabled", "ui.getSnapMode", "ui.getTimeDispMin",
    "ui.getHintMsg", "ui.getHintValue", "ui.getProgTitle", "ui.getVersion", "ui.getStepEditMode",
    "ui.getVisible", "ui.getFocused", "ui.getFocusedFormCaption", "ui.getFocusedFormID",
    "ui.getFocusedPluginName",
    # These set explicit preferences or passive annotations, not the focused control.
    "ui.setBrowserAutoHide", "ui.crDisplayRect", "ui.miDisplayRect", "ui.miDisplayDockRect",
    "ui.snapMode", "ui.setSnapMode", "ui.snapOnOff", "ui.setTimeDispMin", "ui.setHintMsg",
    "ui.setStepEditMode",
})

DESKTOP_NATIVE_FUNCTIONS = frozenset({
    "channels.showGraphEditor", "channels.closeGraphEditor", "channels.showEditor",
    "channels.focusEditor", "channels.showCSForm", "mixer.focusEditor",
    # Unrestricted global command IDs include dialogs; use typed project.save_current.
    "transport.globalTransport",
})


GLOBAL_ONLY_TRANSPORT_FUNCTIONS = frozenset({
    "transport.markerJumpJog", "transport.markerSelJog",
    "transport.rewind", "transport.fastForward",
})
GT_GLOBAL = 8


def _explicit_panel_command(action, params):
    """FL can expose an addressed panel without synthesizing desktop input.

    Keep focus-routed UI commands gated. These two forms name an exact panel
    or piano-roll event editor instead of operating on whichever control owns
    keyboard focus. Backend callers must still inspect the resulting target.
    """
    if action != "native.call" or set(params) - {"function", "args", "allow_write"}:
        return False
    args = params.get("args")
    if not isinstance(args, list):
        return False
    if params.get("function") == "ui.showWindow":
        return len(args) == 1 and type(args[0]) is int and 0 <= args[0] <= 4
    if params.get("function") == "ui.openEventEditor":
        return (len(args) == 3 and type(args[0]) is int and 0 <= args[0] <= 0x7fffffff
                and type(args[1]) is int and args[1] == 1 and args[2] is False)
    return False


def _explicit_global_transport(action, params):
    # The controller dispatches native.call as fn(*args) and ignores kwargs.
    # These functions have no direct typed aliases. Do not accept an apparently
    # global keyword flag that would execute the focus-routing GT_All default.
    if action != "native.call" or set(params) - {"function", "args", "allow_write"}:
        return False
    args = params.get("args")
    return (
        isinstance(args, list) and len(args) == 2
        and type(args[0]) is int and type(args[1]) is int
        and args[1] == GT_GLOBAL
    )


def check_native_command(action, params=None):
    """Validate a bounded command tree and enforce the current desktop policy.

    Every nested step is checked, including preview batches; no queue or MIDI
    operation occurs here. Unknown non-UI commands retain controller validation.
    """
    interaction_mode()  # Invalid settings must not silently enable native UI calls.
    pending = [(action, {} if params is None else params, 0)]
    visited_steps = 0
    while pending:
        current_action, current_params, depth = pending.pop()
        if (not isinstance(current_action, str) or not current_action
                or not isinstance(current_params, dict)):
            raise ValueError("Command action must be text and params an object")
        if depth > MAX_BATCH_DEPTH:
            raise ValueError("Native command batch nesting exceeds its depth limit")
        if depth:
            visited_steps += 1
            if visited_steps > MAX_BATCH_STEPS:
                raise ValueError("Native command tree exceeds 256 total batch steps")
        if current_action == "bridge.batch":
            steps = current_params.get("steps")
            if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_BATCH_STEPS:
                raise ValueError("Native command batches require 1..256 steps")
            for step in reversed(steps):
                if not isinstance(step, dict) or "action" not in step:
                    raise ValueError("Each native batch step requires an action and params object")
                pending.append((step["action"], step.get("params", {}), depth + 1))
            continue
        function = current_params.get("function") if current_action == "native.call" else (
            current_action
        )
        if not isinstance(function, str) or not function:
            raise ValueError("native.call requires a nonempty function name")
        if function in GLOBAL_ONLY_TRANSPORT_FUNCTIONS and not _explicit_global_transport(
            current_action, current_params,
        ):
            require_desktop_input(
                "Focus-routed " + function + "; background requires native.call "
                "with two positional integers and flags=GT_Global (8)"
            )
        if _explicit_panel_command(current_action, current_params):
            continue
        if (function in DESKTOP_NATIVE_FUNCTIONS
                or function.startswith("ui.") and function not in BACKGROUND_UI_FUNCTIONS):
            require_desktop_input("Native " + function)
