"""Stable executor entry points; orchestration remains outside the DAW server."""

from fl_studio_mcp.utils.operator_contract import inspect_session, workflow_catalog


def register_operator_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def fl_workflow_catalog() -> dict:
        """Discover production coverage, backends, read-tool policy and verification limits.

        Offline catalog, not evidence the live DAW/dependencies are ready. Unknown
        tools default to writes; UI/file delivery alone never proves a musical result.
        """
        return workflow_catalog()

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def fl_session_inspect() -> dict:
        """Observe controller, current project/revision, transport and installed dependencies.

        Does not open Save As, move focus, save or modify the project. Ready means
        ready for workflow-specific preflight, not permission or guaranteed success.
        Dependency presence is separate from runtime verification.
        """
        return inspect_session()
