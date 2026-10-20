"""Revision-guarded generic generator and effect parameter workflows."""


def register_plugin_state_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def fl_plugin_inspect(
        index: int,
        slot_index: int = -1,
        query: str = "",
        offset: int = 0,
        limit: int = 100,
        include_project: bool = False,
        include_midi_cc: bool = False,
    ) -> dict:
        """Inspect an exact generator/effect, exposed controls and a complete-state revision.

        index is a GLOBAL channel when slot_index=-1, otherwise a mixer track
        with a zero-based effect slot 0..9. Reports actual and user plugin names,
        target name, project session/generations, normalized host values and
        literal displays. Query is a case-insensitive substring; offset/limit
        paginate matches, omitting blank padding. Limit 1..500, catalog <=10000.
        Revision covers all parameters, including those outside the returned page.
        FL's MIDI-CC pseudo-parameters are omitted unless include_midi_cc is set;
        target.parameter_count_exposed reports the real control count. The full
        channel roster is omitted unless include_project is set (use
        fl_production_inspect for the roster).
        Names are not unique instance IDs; host caches can be stale after preset loads.
        """
        from fl_studio_mcp.utils.plugin_state import inspect_plugin

        return inspect_plugin(
            index,
            slot_index,
            query,
            offset,
            limit,
            include_project=include_project,
            include_midi_cc=include_midi_cc,
        )

    @mcp.tool()
    def fl_plugin_apply(
        index: int,
        expected_plugin: str,
        expected_revision: str,
        changes: list[dict],
        slot_index: int = -1,
        apply: bool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preview/apply 1..256 exact indexed controls to a stopped generator/effect.

        Each change requires parameter, expected_name and value (normalized 0..1).
        Optional normalized_tolerance is a finite number 0..0.01 (default 0.00001),
        declared per change for known quantization. It is never widened automatically.
        Optional expected_display verifies an exact post-write string without
        guessing units, independently of normalized tolerance. Receipts record
        requested/actual values, signed deltas, tolerances and display checks.
        Use a fresh fl_plugin_inspect revision and actual plugin
        name. Apply needs a NEW absolute .json receipt. All changes preflight,
        dispatch once, then need two later host readbacks. Partial/uncertain
        batches are never retried or rolled back. No unit conversion or DSP-state
        guarantee: inspect literal displays and render/audition to verify sound.
        """
        from fl_studio_mcp.utils.plugin_state import apply_plugin

        return apply_plugin(
            index, expected_plugin, expected_revision, changes, slot_index, apply, receipt_file
        )
