"""Typed background save with explicit host-only verification."""

from pydantic import StrictBool

from fl_studio_mcp.utils.project_native import inspect_native_project, save_native_project


def register_project_native_tools(mcp):
    @mcp.tool()
    def fl_project_native_inspect() -> dict:
        """Read the named FL window and project revision without mouse, keys or activation.

        Returns qualification and readiness. The window title identifies the active host
        project but does not establish its absolute path or verify saved disk bytes.
        """
        return inspect_native_project()

    @mcp.tool()
    def fl_project_save_native(expected_revision: str, expected_window_title: str,
                               apply: StrictBool = False, receipt_file: str | None = None) -> dict:
        """Preview or save the active named project without desktop input.

        Requires the exact title/revision from fl_project_native_inspect and stopped
        transport. Apply reserves a NEW absolute receipt_file, dispatches once, then
        checks a dirty-to-clean transition with two later clean-state callbacks.
        Already-clean projects return a no-op without dispatch. saved_current_host
        confirms host state;
        the absolute project path and filesystem persistence remain unverified.
        Qualification remains host_state_only; no destination or filesystem claim is made.
        """
        return save_native_project(expected_revision, expected_window_title, apply, receipt_file)
