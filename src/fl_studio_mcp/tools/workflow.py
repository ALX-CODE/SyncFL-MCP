"""Project-level production workflows distilled from the remake session."""

from fl_studio_mcp.utils.build_info import LOADED_BUILD


def register_workflow_tools(mcp):
    @mcp.tool()
    async def fl_automation_create_channel(
        channel: int,
        expected_name: str,
        parameter: str,
        expected_revision: str,
        directory: str,
        apply: bool = False,
    ) -> dict:
        """Unavailable in backend-only SyncFL. Creating a new automation link has no backend
        implementation. Use automation inspection and saved-FLP editing for existing
        automation.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        # Use the regular shared GUI lock with the file-loading rack guard.
        return await run_workflow(
            "create_channel_automation",
            channel=channel,
            expected_name=expected_name,
            parameter=parameter,
            expected_revision=expected_revision,
            directory=directory,
            apply=apply,
        )

    @mcp.tool()
    def fl_playlist_file_inspect(source: str) -> dict:
        """Inspect exact saved FL26 Playlist clip identities, ticks, tracks and patterns.

        Save first to include current edits. Returns source/clip hashes needed by
        fl_playlist_file_edit. Each event_index scopes one saved Playlist event.
        """
        from fl_studio_mcp.utils.playlist_file import inspect_playlist

        return inspect_playlist(source)

    @mcp.tool()
    def fl_playlist_file_edit(
        source: str,
        output: str,
        expected_sha256: str,
        edits: list[dict],
        dry_run: bool = True,
    ) -> dict:
        """Edit exact pattern/audio/automation placements into a NEW FL26 FLP.

        Each edit: operation, event_index, clip, expected_clip_sha256; optional
        position_ticks and track (one-based). length_ticks and pattern, if provided,
        must match the template; length changes and pattern substitution are rejected.
        Add (FL26.1): operation='add', event_index, pattern, expected_pattern_name,
        position_ticks, track, expected_length_ticks. Adds a full untrimmed pattern
        clip, including to an empty Playlist. Expected length is the actual whole
        pattern length, not a requested resize; FL recomputes it when loading.
        On tested FL26 getPatternLength returns STEPS: length_ticks=steps*PPQ/4.
        Save/reinspect after opening to verify FL's derived duration.
        Clone requires position_ticks. All identities refer to the original source,
        so multiple clones of one template are supported. Existing audio/automation
        clips support clone/update/delete with position_ticks and track only;
        optional expected_channel_name guards the exact channel. Source offsets,
        clip length and opaque state stay unchanged. No trimming, stretching, new
        audio/automation channels or new pattern data. Repeat templates as needed.
        Opaque events/clip bytes are preserved. Output is not
        automatically opened; original and unsaved live state remain unchanged.
        """
        from fl_studio_mcp.utils.playlist_file import edit_playlist

        return edit_playlist(source, output, expected_sha256, edits, dry_run)

    @mcp.tool()
    def fl_build_info() -> dict:
        """Identify the loaded server version and production workflow capabilities."""
        from fl_studio_mcp import __version__
        from fl_studio_mcp.utils.interaction_policy import policy_metadata

        return dict(
            version=__version__,
            workflow_revision=15,
            **LOADED_BUILD,
            interaction_policy=policy_metadata(),
            guarded_production="BPM, channel controls, scoped steps; optional native shuffle",
            native_mixer_controls="built-in EQ, effects enable, polarity and stereo swap",
            playlist="saved-FLP full pattern placement; existing pattern/audio/automation edits",
            automation="native target/link inspection; saved link retargeting and curve edits",
            project_open="revision/hash guarded open with a new verified recovery FLP",
            render="persistent native WAV master/stem jobs with decoded audio verification",
            audio=("Rust BPM and major/minor key candidates; levels/silence/stereo analysis; "
                   "exact trim/reverse/fade/gain to new WAVs"),
            project_bundle="FLP + referenced channel samples + MIDI + exact note data",
            live_mixer="identity-checked scenes with receipts and readback",
            operator="workflow catalog, compact session readiness and complete-call process lease",
            routing="revision/name-guarded send, bus and sidechain scenes with later readbacks",
            plugin_scenes="revision-guarded generator/effect parameters with later host readbacks",
            plugin_controls=("exact-layout named controls; qualified units and enums; "
                             "explicit normalized fallback"),
            background_loading="experimental selected-visible-track empty-slot FST loading",
            background_piano="FL26 score import and verified pattern/sound/mixer selection",
            native_save=("typed active named-project save; host readback, "
                         "absolute disk path unknown"),
            ui="FL-scoped UIA/OCR/screenshots for observation only; no desktop input",
            mix_analysis="FFmpeg integrated loudness, true peaks and reference level comparisons",
            recording="native arm setup; bounded recording and decoded take evidence",
            limitations=[
                "Backend only; desktop input and mode switching are unavailable",
                "Basic FL26 scores use the backend; advanced expression and SDK notes "
                "remain unqualified",
                "Opaque plugin-internal external assets require inspection",
                "Native WAV/FST/FLP loading wrappers are experimental and not yet live-qualified",
                "Render start/cancel, new automation links and Save As are unavailable",
                "Render sample rate uses FL audio settings; loudness tools need optional FFmpeg",
                "Recording filename/input changes, comping and stretching are unavailable",
                "New Playlist audio-channel creation has no backend implementation",
                "Tool lease covers updated MCP clients, not legacy clients or direct CLI calls",
                "New tool registration requires MCP host reconnect",
            ],
        )

    @mcp.tool()
    def fl_automation_file_inspect(source: str) -> dict:
        """Read saved FLP automation names, local ticks, normalized values and SHA256.

        Saved-file state only. Call fl_project_save first to include unsaved edits.
        Local ticks start at each clip's start, not the song's start.
        """
        from fl_studio_mcp.utils.automation_file import inspect_automation

        return inspect_automation(source)

    @mcp.tool()
    def fl_automation_file_edit(
        source: str,
        output: str,
        expected_sha256: str,
        edits: list[dict],
        dry_run: bool = True,
    ) -> dict:
        """Edit existing automation precisely in a NEW FLP; original/live project untouched.

        edits: [{channel,expected_name,operation:'upsert'|'replace',points:
        [{tick:integer,value:0..1,mode:'linear'|'hold'}]}]. Mode controls the
        incoming segment. Values are normalized, never dB. Output must not exist.
        Inspect/review the result, then explicitly open that FLP to use it in FL.
        """
        from fl_studio_mcp.utils.automation_file import edit_automation

        return edit_automation(source, output, expected_sha256, edits, dry_run)

    @mcp.tool()
    def fl_mixer_scene(
        items: list[dict],
        apply: bool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preflight/apply a stopped-transport mix scene with names and readback.

        Items: track,expected_name, optional volume (normalized) OR volume_db,
        pan (-1..1), stereo (-1..1). Requires a new receipt_file for apply.
        Automation can override faders during playback; edit its curve separately.
        """
        from fl_studio_mcp.utils.mixer_scene import apply_mixer_scene

        return apply_mixer_scene(items, apply, receipt_file)

    @mcp.tool()
    def fl_project_file_inspect(source: str) -> dict:
        """Inspect a saved FLP's patterns, notes, playlist and external sample references."""
        from fl_studio_mcp.utils.project_bundle import inspect_project

        return inspect_project(source)

    @mcp.tool()
    def fl_project_package(
        source: str,
        destination: str,
        path_variables: dict[str, str] | None = None,
    ) -> dict:
        """Package a SAVED project into a new folder with samples, MIDIs and hashes.

        Does not save or mutate FL. Save once first for current edits. Copies only
        referenced assets; retains original FLP plus a portable copy. Plugin
        binaries/licenses are not included. Review missing/internal asset report.
        """
        from fl_studio_mcp.utils.project_bundle import pack_project

        return pack_project(source, destination, path_variables=path_variables)
