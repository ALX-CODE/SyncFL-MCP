"""Shared structured automation surface; identical backend to the CLI."""

from fl_studio_mcp.utils.connection import get_connection


def register_advanced_tools(mcp):
    @mcp.tool()
    def fl_project_create(
        path: str, expected_revision: str, receipt_file: str | None = None, apply: bool = False,
    ) -> dict:
        """Initialize a named FLP from the current clean, unnamed Empty project.

        Use this BEFORE adding sounds, notes or mixer changes when starting a beat
        from a blank project. Do not ask the user to save the blank project manually.
        Read fl_production_inspect(scope='project') for expected_revision. Preflight
        by default; apply=True needs a new absolute .flp path and .json receipt_file.
        Copies the installed Empty template, loads it through the native backend,
        initializes an empty Main pattern, then saves and verifies the FL26 file.
        Requires the updated controller's forced-save support; a clean changed flag
        does not skip this first native save.
        Rejects named, dirty or nonempty projects and never overwrites a file.
        No keyboard, mouse, Save As dialog or interactive mode. Experimental native load.
        """
        from fl_studio_mcp.utils.backend_project import create_blank_project

        return create_blank_project(path, expected_revision, receipt_file, apply)

    @mcp.tool()
    async def fl_project_open(
        source: str, expected_sha256: str, expected_revision: str, recovery_path: str,
        receipt_file: str | None = None, apply: bool = False,
        current_project_path: str | None = None,
    ) -> dict:
        """Load an FL26 project through the native backend, preserving a recovery FLP.

        Preflight by default. Use source SHA256 and fl_production_inspect project revision.
        Supply current_project_path for the already named/saved active project.
        For a clean unnamed blank session, use fl_project_create before composing.
        Apply needs new absolute JSON receipt_file and recovery_path (.flp). Native-save
        the current project before copying it to recovery; never discard dirty edits.
        Verify generation, title, channels, source hash, tempo and PPQ. Absolute loaded
        path is caller-declared, not independently exposed by FL. No input or mode switch.
        Unnamed projects are unsupported; stop on dialogs or uncertain completion.
        """
        from fl_studio_mcp.utils.backend_project import open_backend_project

        return open_backend_project(
            source=source, expected_sha256=expected_sha256,
            expected_revision=expected_revision, recovery_path=recovery_path,
            receipt_file=receipt_file, apply=apply, current_project_path=current_project_path,
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
        """Unavailable in backend-only SyncFL. Dialog-based exact project-path discovery is
        unavailable. Use fl_project_native_inspect for native project state; the
        absolute path remains caller-declared.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("project_info")

    @mcp.tool()
    async def fl_project_save(expected_path: str | None = None) -> dict:
        """Unavailable in backend-only SyncFL. Dialog-based exact-path saving is unavailable.
        Use fl_project_save_native to save the current named project.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("save_project", expected_path=expected_path)

    @mcp.tool()
    async def fl_project_save_as(path: str, overwrite: bool = False) -> dict:
        """Unavailable in backend-only SyncFL. Saving the active project under a new name has no
        backend implementation. Use fl_project_save_native for an already named
        project, or fl_project_create to initialize a clean unnamed Empty project.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("save_project_as", path=path, overwrite=overwrite)

    @mcp.tool()
    async def fl_plugin_chain(items: list[dict], receipt_file: str | None = None) -> dict:
        """Load {track,slot,name,wet?} entries through the serialized native backend.

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
        """Unavailable in backend-only SyncFL. Removing a loaded plugin has no backend
        implementation. Native controls can inspect and adjust an existing plugin.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow(
            "remove_plugin", track=track, slot=slot, expected_plugin=expected_plugin
        )

    @mcp.tool()
    async def fl_ensure_mixer_tracks(last_insert: int) -> dict:
        """Unavailable in backend-only SyncFL. Appending mixer inserts has no backend
        implementation. Use native mixer tools with existing track indices.
        """
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
