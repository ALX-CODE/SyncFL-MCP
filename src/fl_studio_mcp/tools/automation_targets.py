"""Native automation identity discovery and guarded saved-link retargeting."""


def register_automation_target_tools(mcp):
    @mcp.tool()
    def fl_automation_target_inspect(target: dict) -> dict:
        """Resolve a precise FL26.1 automation target through independent host readbacks.

        target requires kind and index (GLOBAL channel or zero-based mixer track).
        Kinds: channel_volume, channel_pan, mixer_volume, mixer_pan, mixer_send
        (also destination), generator_parameter (also parameter), effect_parameter
        (also parameter and zero-based slot_index 0..9). Send destination 0..127
        must already be enabled. Unresolved native event labels are rejected.
        Returns actual names, native full/short event labels, event ID, project
        context and revision. Review the label and parameter name before retargeting.
        This only reads the host; it neither creates a clip nor moves a control.
        """
        from fl_studio_mcp.utils.automation_targets import inspect_target

        return inspect_target(target)

    @mcp.tool()
    def fl_automation_links_inspect(
        source: str, channel: int | None = None, event_id: int | None = None,
        offset: int = 0, limit: int = 100,
    ) -> dict:
        """Read exact saved FL26.1 automation links and compact curve identities.

        source is an absolute FLP path (<=256 MiB). Optional channel/event_id
        filter, limit 1..500. Reports source SHA256, native event IDs, raw link
        bytes and matching curve names/hashes. Unsupported link layouts fail
        explicitly. Never opens FL or returns giant point arrays.
        """
        from fl_studio_mcp.utils.automation_targets import inspect_links

        return inspect_links(source, channel, event_id, offset, limit)

    @mcp.tool()
    def fl_automation_file_retarget(
        source: str, output: str, expected_sha256: str, automation_channel: int,
        expected_name: str, expected_event_id: int, target: dict,
        expected_target_revision: str, expected_event_name: str, apply: bool = False,
    ) -> dict:
        """Preview or redirect one existing automation link into a NEW saved FLP.

        Inspect both the saved links and native target first. Supply the exact
        source hash, automation channel/name/old event ID, target revision and
        native full event label. Requires stopped FL, matching live/saved channel
        roster, one controller-zero link with tested flags, and an unlinked
        destination. Plugin short label must match its exposed parameter name.
        Use a fresh saved project copy: roster equality is not proof of matching
        plugin contents. Only four event-ID bytes change; curves/placement and all
        other bytes remain intact. Apply writes output plus a new adjacent
        .automation-retarget.json receipt containing inspected target context.
        It never edits the source or live DAW. Reopen/save the output and inspect
        links, then audition/render to verify automation in FL.
        """
        from fl_studio_mcp.utils.automation_targets import retarget_automation

        return retarget_automation(
            source, output, expected_sha256, automation_channel, expected_name,
            expected_event_id, target, expected_target_revision, expected_event_name, apply,
        )
