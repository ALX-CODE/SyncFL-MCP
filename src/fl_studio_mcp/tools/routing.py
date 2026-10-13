"""Guarded send, bus and sidechain workflows within the FL MCP boundary."""


def register_routing_tools(mcp):
    @mcp.tool()
    def fl_routing_inspect() -> dict:
        """Inspect all mixer routes (including zero-level sidechains) with a fresh revision.

        Returns exact track names, active routes, channel destinations, project identity
        and transport. This is a read-only snapshot; use its revision for fl_routing_scene.
        """
        from fl_studio_mcp.utils.routing_scene import inspect_routing

        return inspect_routing()

    @mcp.tool()
    def fl_routing_scene(
        items: list[dict],
        expected_revision: str,
        apply: bool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preview/apply guarded sends, buses and sidechains; stopped transport required.

        Each item: source, destination, expected_source, expected_destination, enabled.
        Enabled routes REQUIRE level (0..1); level=0 makes a silent sidechain route.
        Disabled routes omit level. Track indices are zero-based; Master is destination 0.
        Edits affect only listed routes: explicitly disable a source's Master route when
        routing it exclusively through a bus. Receiving plugin sidechain selection is
        separate. Feedback is rejected, including cycles of zero-level routes.

        Apply requires the fresh fl_routing_inspect revision and a NEW absolute JSON
        receipt_file. One typed batch removes edges before adding edges; two later
        callbacks verify the full graph and project/track identities. Receipts preserve
        partial/uncertain outcomes and restore_scene; no automatic retry or restore.
        The shared GUI lock coordinates cooperating clients; it cannot lock FL's UI.
        """
        from fl_studio_mcp.utils.routing_scene import apply_routing_scene

        return apply_routing_scene(items, expected_revision, apply, receipt_file)
