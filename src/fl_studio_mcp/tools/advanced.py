"""Shared structured automation surface; identical backend to the CLI."""

from fl_studio_mcp.utils.connection import get_connection


def register_advanced_tools(mcp):
    @mcp.tool()
    async def fl_project_open(
        source: str, expected_sha256: str, expected_revision: str, recovery_path: str,
        receipt_file: str | None = None, apply: bool = False,
    ) -> dict:
        """Open/reopen an exact saved FLP, checkpointing current edits to a NEW FLP first.

        Preflight by default. Use source SHA256 and fl_production_inspect project revision.
        Apply needs a new absolute JSON receipt_file and recovery_path (.flp). Always
        saves a recovery copy, including unnamed/dirty projects; never discards edits.
        Verifies new project generation, exact path, source hash, tempo and PPQ.
        Stops on dialogs or uncertain completion; inspect the receipt before recovery.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow(
            "open_project", source=source, expected_sha256=expected_sha256,
            expected_revision=expected_revision, recovery_path=recovery_path,
            receipt_file=receipt_file, apply=apply,
        )

    @mcp.tool()
    def fl_plugin_set_display_value(
        track: int,
        slot: int,
        parameter: int,
        expected_plugin: str,
        expected_name: str,
        target: float,
        unit: str,
        tolerance: float = 0.01,
    ) -> dict:
        """Calibrate a continuous control to its displayed dB/ms/etc value while stopped.

        Probes the control briefly. Not for enumerations or non-monotonic displays.
        Requires exact plugin, parameter name and unit. Reports restoration on failure.
        Do not concurrently edit this control with another client.
        """
        from fl_studio_mcp.utils.parameter_values import set_display_value

        return set_display_value(
            track, slot, parameter, expected_plugin, expected_name, target, unit, tolerance
        )

    @mcp.tool()
    async def fl_project_info() -> dict:
        """Discover the exact active FLP path by inspecting and cancelling Save As.

        Reports the title, saved status and observed suggested path for unnamed projects.
        Does not save or accept the dialog. Resolve existing modal dialogs before use.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("project_info")

    @mcp.tool()
    async def fl_project_save(expected_path: str | None = None) -> dict:
        """Save the current FLP once with verified exact path and file persistence.

        The path is discovered automatically; expected_path adds an optional identity guard.
        Unnamed projects require fl_project_save_as. Returns file hash and stable timestamp.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("save_project", expected_path=expected_path)

    @mcp.tool()
    async def fl_project_save_as(path: str, overwrite: bool = False) -> dict:
        """Save the active project to an absolute .flp path and verify it became active.

        Creates parent folders. Existing destinations require overwrite=True and receive
        a backup before saving. Returns the saved path, hash, timestamp and backup path.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("save_project_as", path=path, overwrite=overwrite)

    @mcp.tool()
    async def fl_plugin_chain(items: list[dict], receipt_file: str | None = None) -> dict:
        """Load {track,slot,name,wet?} entries in one serialized Windows session.

        Names resolve exact Plugin database files. Target slots must be empty.
        A receipt file records each verified slot for explicit inspection and resumption.
        """
        from fl_studio_mcp.utils.studio_loading import run_file_workflow

        return await run_file_workflow("load_chain", items=items, receipt_file=receipt_file)

    @mcp.tool()
    async def fl_plugin_add(track: int, slot: int, name: str, result_row: int = 0) -> dict:
        """Load an exact Plugin database effect name into an empty mixer slot.

        Uses the same guarded file loading as fl_effect_add. Indices are zero-based.
        result_row must remain 0; use fl_effect_add(path=...) to resolve duplicate names.
        """
        if result_row != 0:
            raise ValueError("Use fl_effect_add with an exact .fst path instead of picker rows")
        from fl_studio_mcp.utils.studio_loading import run_file_workflow

        return await run_file_workflow("add_effect", track=track, slot=slot, name=name)

    @mcp.tool()
    async def fl_plugin_remove(track: int, slot: int, expected_plugin: str) -> dict:
        """Remove only the identity-checked effect from a mixer slot, then verify absence."""
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow(
            "remove_plugin", track=track, slot=slot, expected_plugin=expected_plugin
        )

    @mcp.tool()
    async def fl_ensure_mixer_tracks(last_insert: int) -> dict:
        """Append missing mixer inserts in one Add multiple operation; preserve existing indices."""
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("ensure_tracks", last_insert=last_insert)

    @mcp.tool()
    def fl_capabilities() -> dict:
        """Discover available native signatures and typed bulk commands in the live controller."""
        return get_connection().send_command("bridge.capabilities", timeout=10)

    @mcp.tool()
    def fl_batch(steps: list[dict], dry_run: bool = True) -> dict:
        """Preflight then execute up to 256 typed operations. Defaults to dry run.

        Each step is {action, params}. Slots/channels/mixer tracks are zero-based.
        Stops on first failure and reports applied count; no automatic rollback.
        Use fl_capabilities for actions and fl_native_call for documented API calls.
        """
        return get_connection().send_command(
            "bridge.batch", {"steps": steps, "dry_run": dry_run}, 30
        )

    @mcp.tool()
    def fl_native_call(function: str, args: list, allow_write: bool = False) -> dict:
        """Invoke one advertised FL native API function with positional args, no Python eval.

        Advanced surface: caller must inspect the advertised signature and target indices.
        Mutations require allow_write; typed batch operations provide stronger validation.
        """
        return get_connection().send_command(
            "native.call", {"function": function, "args": args, "allow_write": allow_write}, 10
        )

    @mcp.tool()
    def fl_project_snapshot() -> dict:
        """Read every channel, all mixer inserts, effect slots, routes and transport state."""
        return get_connection().send_command("project.snapshot", timeout=20)

    @mcp.tool()
    def fl_plugin_parameters(
        track: int, slot: int, query: str = "", offset: int = 0, limit: int = 100
    ) -> dict:
        """Search exposed parameters, omitting blank VST padding. Slot 0 is FL Slot 1."""
        return get_connection().send_command(
            "plugins.parameters",
            {"track": track, "slot": slot, "query": query, "offset": offset, "limit": limit},
            15,
        )

    @mcp.tool()
    def fl_plugin_set_many(
        track: int, slot: int, expected_plugin: str, changes: list[dict]
    ) -> dict:
        """Set normalized parameters with plugin identity guard and before/after readback.

        Each change: {parameter: zero-based index, value: 0..1, expected_name: optional}.
        """
        return get_connection().send_command(
            "plugins.setMany",
            {"track": track, "slot": slot, "expected_plugin": expected_plugin, "changes": changes},
            15,
        )

    @mcp.tool()
    def fl_receipt(request_id: str) -> dict:
        """Inspect a timed-out command's receipt (last 128 requests, until controller reload)."""
        return get_connection().send_command("bridge.receipt", {"id": request_id})

    @mcp.tool()
    def fl_mix_profile(profile: dict, apply: bool = False, receipt_file: str | None = None) -> dict:
        """Preflight/apply named plugin controls; require a new receipt file for writes.

        Profile controls: track, slot, parameter, plugin, name; either value (0..1)
        or target with unit and optional tolerance. Optional display verifies a
        known enum label. Stopped transport required. Receipt includes restore_profile.
        """
        from fl_studio_mcp.utils.mix_profiles import apply_profile

        return apply_profile(profile, apply, receipt_file)
