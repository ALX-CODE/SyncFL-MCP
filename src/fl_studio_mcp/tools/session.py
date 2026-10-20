"""Session-level backend tools: undo, dialog recovery, slot mix, events, naming."""


def register_session_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def fl_undo_status() -> dict:
        """Read FL's own undo counters and the name of the last undoable action."""
        from fl_studio_mcp.utils.session import undo_status

        return undo_status()

    @mcp.tool()
    def fl_undo() -> dict:
        """Step FL's native undo stack back once and report the new position.

        Uses general.undo (ordinary native access, not full-access only). This is
        FL's undo, so it reverses the host's last action, which may not be the last
        tool call. Inspect the target before the next write.
        """
        from fl_studio_mcp.utils.session import undo

        return undo()

    @mcp.tool()
    def fl_clear_dialog() -> dict:
        """Close FL's active popup menu without synthesizing desktop input.

        For recovering from a menu a previous step left open. Requires the server
        to run with FL_MCP_ACCESS=full, because ui.closeActivePopupMenu is a
        focus-adjacent function. Reports the observed popup state before and after.
        """
        from fl_studio_mcp.utils.session import clear_dialog

        return clear_dialog()

    @mcp.tool()
    def fl_plugin_mix(track: int, slot: int, mix: float | None = None) -> dict:
        """Read or set one effect slot's wet/dry level in 0..1, with readback.

        Omit mix to read. slot is the zero-based effect slot 0..9 on a mixer track.
        The readback is the host value, not proof of the audible result.
        """
        from fl_studio_mcp.utils.session import plugin_mix

        return plugin_mix(track, slot, mix)

    @mcp.tool()
    def fl_track_slots(track: int, enabled: bool | None = None) -> dict:
        """Read or set a mixer strip's whole effect rack on/off, with readback.

        Omit enabled to read. This is the strip-level effects switch, not a plugin
        bypass, so leaving it off silences every slot on that strip.
        """
        from fl_studio_mcp.utils.session import track_slots

        return track_slots(track, enabled)

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def fl_event_inspect(event_id: int) -> dict:
        """Read a native automation event: its labels, raw value and smoothed value.

        Resolve event ids with fl_automation_target_inspect first. The normalized
        value is raw / 2**30 (measured on the Insert 1 volume event); treat the
        physical meaning as unverified until a readback confirms it. An unresolved
        event id is reported as such and must not be automated.
        """
        from fl_studio_mcp.utils.session import event_inspect

        return event_inspect(event_id)

    @mcp.tool()
    def fl_automate_event(
        event_id: int,
        value_normalized: float | None = None,
        value_raw: int | None = None,
        flags: int = 0,
        speed: int = 0,
        is_increment: int = 0,
        apply: bool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preview/apply one native automation event write and verify it by readback.

        Resolve the event id with fl_automation_target_inspect, then supply exactly
        one of value_normalized (0..1) or value_raw (native units, 1.0 == 2**30).
        The function refuses unresolved event ids. Preview is the default; apply
        needs a new absolute JSON receipt. mixer.automateEvent is a full-access
        function, so the controller only accepts it with FL_MCP_ACCESS=full.
        Writes once, reads back, records requested versus actual; never retries.
        """
        from fl_studio_mcp.utils.session import automate_event

        return automate_event(
            event_id,
            value_normalized=value_normalized,
            value_raw=value_raw,
            flags=flags,
            speed=speed,
            is_increment=is_increment,
            apply=apply,
            receipt_file=receipt_file,
        )

    @mcp.tool()
    def fl_rename_channel(index: int, name: str) -> dict:
        """Rename a Channel Rack channel by GLOBAL index, with readback."""
        from fl_studio_mcp.utils.session import rename_channel

        return rename_channel(index, name)

    @mcp.tool()
    def fl_rename_pattern(index: int, name: str) -> dict:
        """Rename a pattern by index, with readback."""
        from fl_studio_mcp.utils.session import rename_pattern

        return rename_pattern(index, name)

    @mcp.tool()
    def fl_pattern_color(index: int, color: int | None = None) -> dict:
        """Read or set a pattern's native color word, with readback.

        Omit color to read. The color is FL's own packed word, not an RGB tuple.
        """
        from fl_studio_mcp.utils.session import pattern_color

        return pattern_color(index, color)
