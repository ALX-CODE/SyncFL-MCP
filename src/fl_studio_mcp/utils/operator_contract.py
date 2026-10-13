"""Explicit workflow coverage and conservative observation policy for executors."""

from __future__ import annotations

import importlib.util

from fl_studio_mcp.utils.build_info import LOADED_BUILD
from fl_studio_mcp.utils.interaction_policy import policy_metadata

# This is an explicit allowlist, never a name-prefix heuristic. Some apparent
# reads (project_info and piano-roll reads) move focus/open dialogs.
READ_TOOLS = frozenset(
    """
fl_connection_status fl_build_info fl_capabilities fl_project_snapshot
fl_get_transport_status fl_get_song_length fl_get_channel_count
fl_get_channel_info fl_get_all_channels fl_get_selected_channel
fl_get_mixer_track_count fl_get_mixer_track_info fl_get_all_mixer_tracks
fl_get_grid_bit fl_get_step_sequence fl_get_preset_count fl_get_plugin_color
fl_get_plugin_name fl_get_plugin_param_count fl_get_plugin_params
fl_get_plugin_param_value fl_is_plugin_valid fl_plugin_parameters
fl_production_inspect fl_receipt fl_find_assets fl_audio_inspect fl_audio_key fl_audio_bpm
fl_automation_file_inspect fl_playlist_file_inspect fl_project_file_inspect
fl_serum_recipes fl_serum_inspect_preset fl_serum_analyze_audio
fl_workflow_catalog fl_session_inspect fl_routing_inspect fl_ui_inspect
fl_audio_loudness fl_audio_compare fl_plugin_inspect fl_recording_inspect fl_render_status
fl_recording_status
fl_automation_target_inspect fl_automation_links_inspect
fl_mix_diagnose fl_mixer_meter_watch fl_midi_reference_search
fl_notes_transform fl_plugin_adapter_catalog fl_plugin_controls_inspect
fl_background_effect_inspect fl_project_native_inspect
fl_mixer_controls_inspect fl_background_piano_inspect
""".split()
)

# These never access the live DAW and can run during another client's host tool.
OFFLINE_TOOLS = frozenset(
    """
fl_build_info fl_find_assets fl_audio_inspect fl_audio_edit fl_audio_loudness
fl_audio_key fl_audio_bpm
fl_audio_compare fl_automation_file_inspect fl_automation_file_edit
fl_playlist_file_inspect fl_playlist_file_edit fl_project_file_inspect
fl_project_package fl_serum_recipes fl_serum_inspect_preset
fl_serum_build_preset fl_serum_edit_preset fl_serum_analyze_audio fl_workflow_catalog
fl_recording_status fl_recording_stop
fl_automation_links_inspect
fl_mix_diagnose fl_midi_reference_search
fl_notes_transform fl_plugin_adapter_catalog
""".split()
)


# These are the current Windows-backed routes, not guesses from tool names.
# Native and saved-file operations remain independent of desktop-input policy.
DESKTOP_INPUT_TOOLS = frozenset(
    """
fl_piano_roll_prepare fl_write_pattern_bank fl_get_piano_roll_state
fl_serum_load_preset
fl_audio_clip_add fl_project_save fl_project_save_as fl_project_info
fl_automation_create_channel fl_recording_configure_file fl_render_start fl_render_cancel
fl_ui_action fl_plugin_remove fl_ensure_mixer_tracks
""".split()
)

# Legacy score delivery can prepare a file without input, but its live trigger
# depends on a retired script trigger. Do not advertise it as executable.
SECONDARY_TOOLS = DESKTOP_INPUT_TOOLS | {"fl_send_notes"}


def backend_first(names):
    """Preserve relevance order within primary and secondary execution routes."""
    names = list(dict.fromkeys(names))
    return ([name for name in names if name not in SECONDARY_TOOLS]
            + [name for name in names if name in SECONDARY_TOOLS])


def _workflow(identifier, coverage, backend, tools, verification, limitations=()):
    names = backend_first(tools.split())
    return dict(
        id=identifier,
        coverage=coverage,
        backend=backend,
        tools=[name for name in names if name not in SECONDARY_TOOLS],
        preferred_tools=[name for name in names if name not in SECONDARY_TOOLS],
        unavailable_tools=[name for name in names if name in SECONDARY_TOOLS],
        input_requirements=dict(
            backend_only=True,
        ),
        verification=verification,
        limitations=list(limitations),
    )


WORKFLOWS = [
    _workflow(
        "blank_project", "experimental", "native_blank_project_bootstrap",
        "fl_project_create fl_production_inspect fl_project_native_inspect",
        "Named FL26 file verified after native load, Pattern 1 initialization and native save",
        ["Run before adding sounds or notes: current project must be clean, unnamed and empty",
         "Uses the installed Empty template; never overwrites an existing path",
         "No manual first save, keyboard, mouse or Save As dialog"],
    ),
    _workflow(
        "background_piano", "experimental", "native_ole_score",
        "fl_background_piano_inspect fl_background_piano_select fl_background_piano_write",
        "Exact saved score and independent native save; other channel/pattern notes preserved",
        ["Saved FL26 project, explicit active .flp path and stopped transport required",
         "Basic score replacement; no ComposeWithLLM, menus, keyboard or mouse",
         "Private OLE interface; no automatic retry after an uncertain dispatch"],
    ),
    _workflow(
        "tempo_analysis", "integrated", "rust_audio_file",
        "fl_audio_bpm",
        "Ranked BPM candidates with periodicity and half/double-time alternatives",
        ["Windows x64 Rust runtime; PCM/IEEE float RIFF WAV; first 180 seconds maximum",
         "Fixed-tempo estimate; relative confidence is not calibrated correctness"],
    ),
    _workflow(
        "key_analysis", "integrated", "numpy_audio_file",
        "fl_audio_key",
        "Ranked global major/minor candidates with Pearson correlation and relative confidence",
        ["NumPy required; PCM/IEEE float RIFF WAV only; no live DAW or desktop input",
         "Global estimate only; relative confidence is not calibrated correctness"],
    ),
    _workflow(
        "native_mixer_controls", "integrated", "native",
        "fl_mixer_controls_inspect fl_mixer_controls_set",
        "Exact strip/project revision, complete preflight, two later native readbacks and receipt",
        ["Built-in EQ writes use normalized values; native Hz/dB are observations",
         "Playback and recording must be stopped; no track selection or desktop input"],
    ),
    _workflow(
        "session",
        "integrated",
        "native",
        "fl_session_inspect fl_production_inspect",
        "Fresh controller session, project generation, identities and revisions",
    ),
    _workflow(
        "composition",
        "integrated",
        "native_and_internal_windows",
        "fl_background_piano_inspect fl_background_piano_write fl_background_piano_select "
        "fl_piano_roll_prepare fl_write_pattern_bank fl_get_piano_roll_state fl_step_sequence_edit",
        "Committed note/step readback and revisions",
        [
            "Legacy piano preparation and script transactions are unavailable; "
            "prefer background_piano for supported persistent FL26 notes",
            "Pattern bank is one channel at a time; generic MIDI omits FL articulation",
        ],
    ),
    _workflow(
        "midi_references",
        "integrated",
        "midi_file",
        "fl_midi_reference_search fl_notes_transform",
        "Bounded local discovery, source hashes and parsed pitch/rhythm summaries",
        ["Similarity is descriptive; it does not establish musical quality"],
    ),
    _workflow(
        "assets_and_plugins",
        "experimental",
        "native_ole",
        "fl_find_assets fl_sample_add fl_instrument_add fl_effect_add fl_preset_load",
        "Native target identity/type/name/routing after guarded file delivery",
        [
            "Live sample source path is not independently exposed by FL",
            "General FST loading excludes complete mixer-track presets",
            "WAV samples and FST presets use the experimental native OLE bridge; no desktop input",
        ],
    ),
    _workflow(
        "background_effect_loading",
        "experimental",
        "private_ole_and_native_readback",
        "fl_background_effect_inspect fl_background_effect_load",
        "Pinned helper, exact project/selection/window revision and later all-slot identities",
        [
            "Empty effect slot on the already selected, visibly matching mixer track only",
            "Cross-track delivery routed incorrectly in a live test; no track change is sent",
            "Private unsupported host interface; no mouse, keyboard, focus or retry fallback",
            "Slot names do not prove plugin licensing, DSP state or every same-name replacement",
        ],
    ),
    _workflow(
        "mix_balance",
        "integrated",
        "native",
        "fl_mixer_scene fl_mixer_meter_watch",
        "Exact names, preflight, measured dB, readback and recovery receipt",
    ),
    _workflow(
        "buses_sends_sidechains",
        "integrated",
        "native",
        "fl_routing_inspect fl_routing_scene",
        "Revision, graph, identities and later readbacks",
        ["Zero-level routes are sidechains; receiving plugin settings are separate"],
    ),
    _workflow(
        "plugin_parameters",
        "partial",
        "native",
        "fl_plugin_inspect fl_plugin_apply fl_plugin_set_display_value "
        "fl_plugin_adapter_catalog fl_plugin_controls_inspect fl_plugin_controls_set",
        "Generator/effect identity, complete revision, receipt and two later host readbacks",
        [
            "Host caches may be stale; render/audition to verify actual DSP behavior",
            "Legacy set-many and display setter lack this full revision/receipt contract",
            "Adapter calibration is in progress; uncalibrated controls accept normalized values",
            "Names/layouts cannot distinguish plugin binaries with identical exposed controls",
            "Opaque/unexposed controls without a native API are unavailable",
        ],
    ),
    _workflow(
        "sound_design",
        "partial",
        "native_and_internal_windows",
        "fl_serum_inspect fl_serum_apply fl_serum_build_preset fl_serum_probe "
        "fl_plugin_inspect fl_plugin_apply",
        "Serum-specific maps, displayed values, preset checksums and audio probes",
        ["Serum 2 unit maps; generic generators expose normalized values and literal displays"],
    ),
    _workflow(
        "arrangement",
        "partial",
        "saved_file",
        "fl_audio_clip_add fl_playlist_file_inspect fl_playlist_file_edit fl_project_open",
        "New hashed FL26 copy, preserved opaque data; reopen/save/reinspect",
        [
            "Typed editor places full patterns and clones/moves/deletes audio/automation clips",
            "New live Playlist audio-channel creation is unavailable",
            "Audio/automation source offsets are preserved; no clip trims or stretching",
            "No keyboard, mouse or dialog fallback is available",
            "Native FLP open requires current_project_path and a new recovery copy; "
            "legacy live audio import is unavailable",
        ],
    ),
    _workflow(
        "automation",
        "partial",
        "native_and_saved_file",
        "fl_automation_create_channel fl_automation_target_inspect fl_automation_links_inspect "
        "fl_automation_file_retarget fl_automation_file_inspect fl_automation_file_edit",
        "Saved channel links and exact local tick/value curves",
        [
            "Retarget an existing saved link to a verified native event; no new link creation",
            "FL26.1 mixer/generator/effect labels must resolve; reopen/save/render checks behavior",
            "Curves support linear/hold; opaque or unresolved parameters are unavailable",
        ],
    ),
    _workflow(
        "recording_and_comping",
        "partial",
        "native",
        "fl_recording_inspect fl_recording_configure_file fl_recording_arm "
        "fl_recording_start fl_recording_status fl_recording_stop fl_ui_inspect fl_ui_action",
        "Exact new WAV, persistent bounded worker, stopped transport and decoded non-silent take",
        [
            "Recording filename setup is unavailable; use an existing verified target",
            "Input device/channel, pickup, monitoring and latency need independent inspection",
            "Take decoding does not prove alignment/comping; microphone source is caller-declared",
            "Legacy record/arm tools toggle state; a successful toggle is not a recorded take",
        ],
    ),
    _workflow(
        "sample_editing",
        "partial",
        "audio_file",
        "fl_audio_inspect fl_audio_edit",
        "Exact frame bounds, source hash and output sample readback",
        ["Trim/reverse/fade/gain only; no stretch, resample or pitch-correction workflow"],
    ),
    _workflow(
        "render_and_stems",
        "observation_only",
        "native_and_job_state",
        "fl_render_start fl_render_status fl_render_cancel",
        "Existing job status and output inspection only",
        [
            "No backend render start or cancellation is implemented",
            "GUI start/cancel are unavailable; existing job status observation remains available",
        ],
    ),
    _workflow(
        "mix_measurement",
        "integrated",
        "audio_file_and_ffmpeg",
        "fl_audio_inspect fl_audio_loudness fl_audio_compare fl_mix_diagnose",
        "File hashes, levels, stereo and FFmpeg input loudness/true-peak measurements",
        ["Optional FFmpeg and audio dependencies required; measurements are not listening"],
    ),
    _workflow(
        "save_and_delivery",
        "partial",
        "native_and_saved_file",
        "fl_project_save fl_project_save_as fl_project_package",
        "Saved-file hashes and referenced-asset manifest; native save has host-state proof",
        ["Opaque plugin-internal external resources need separate inspection"],
    ),
    _workflow(
        "native_project_save",
        "experimental",
        "typed_native_command",
        "fl_project_native_inspect fl_project_save_native",
        "Exact named window/project revision, one dispatch and two later host clean-state reads",
        [
            "Live FL26 lab verified; qualification remains host-state-only",
            "Current named project only; no Open, Save As or destination selection",
            "Host clean state does not verify absolute path or filesystem persistence",
        ],
    ),
    _workflow(
        "ui_fallback",
        "partial",
        "observation_only",
        "fl_ui_inspect fl_ui_action",
        "Read-only FL window snapshots and OCR",
        [
            "Desktop input is unavailable",
            "Screenshots are returned to caller vision; no hidden vision-model dependency",
            "Bounded Windows OCR supplies text/boxes to text-only executors, not target proof",
            "Unknown dialogs/geometry require inspection, never guessed replay",
        ],
    ),
]


def workflow_catalog():
    return dict(
        success=True,
        schema_version=1,
        workflow_revision=16,
        workflows=WORKFLOWS,
        read_tools={name: {} for name in sorted(READ_TOOLS)},
        policy=dict(
            interaction=policy_metadata(),
            unlisted_tools="write_or_ui_side_effect; inspect schema and authorize explicitly",
            mixed_tools="Treat fl_native_call, fl_batch and all preview/apply tools as writes",
            preview="Use apply=false/dry_run=true where documented; preview is not completion",
            uncertainty="Stop on unknown/partial/dispatched_unverified; inspect receipt and state",
            ui_reads="fl_ui_inspect observes only; dialog-based project inspection is unavailable",
            tool_lock="Live MCP calls are serialized across updated server processes",
            job_lock="Executor must hold its own job lease across observe/choose/act/verify",
        ),
        indices=dict(
            channel="global zero-based",
            mixer="zero-based",
            effect_slot="0..9",
            pattern="one-based",
            playlist_track="one-based",
            note_time="integer PPQ ticks",
        ),
        universal_production_complete=False,
    )


def dependency_status():
    from fl_studio_mcp.utils.mix_analysis import find_ffmpeg
    from fl_studio_mcp.utils.tempo_analysis import EXECUTABLE, RUNTIME
    from fl_studio_mcp.utils.windows_server import resolve_windows_server

    try:
        windows = resolve_windows_server()
    except RuntimeError:
        windows = None
    try:
        ffmpeg = find_ffmpeg()
    except RuntimeError:
        ffmpeg = None
    audio = all(importlib.util.find_spec(name) is not None for name in ("numpy", "soundfile"))
    return dict(
        windows_helper=dict(installed=bool(windows), path=windows, runtime_verified=False),
        audio_packages=dict(installed=audio, runtime_verified=False),
        key_analysis=dict(installed=importlib.util.find_spec("numpy") is not None,
                          runtime_verified=False),
        rust_tempo=dict(installed=(RUNTIME / EXECUTABLE).is_file()
                       and (RUNTIME / "manifest.json").is_file(), runtime_verified=False),
        ffmpeg=dict(installed=bool(ffmpeg), path=ffmpeg, runtime_verified=False),
    )


def inspect_session(connection=None):
    """Compact native observations; does not open Save As or move FL focus."""
    from fl_studio_mcp.utils.midi_connection import get_connection
    from fl_studio_mcp.utils.protocol import transaction_lock

    report = dict(
        success=False,
        status="unavailable",
        build=LOADED_BUILD,
        dependencies=dependency_status(),
        observations={},
        blockers=[],
    )
    try:
        conn = connection if connection is not None else get_connection()
        with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
            for label, action, params in (
                ("controller", "bridge.ping", {}),
                ("project", "production.inspect", {"scope": "project"}),
                ("transport", "transport.getStatus", {}),
            ):
                result = conn.send_command(action, params, 5)
                report["observations"][label] = result
                if result.get("success") is not True:
                    report["blockers"].append(
                        dict(code="observation_failed", component=label, detail=result.get("error"))
                    )
                    return report
        project = report["observations"]["project"]
        controller = report["observations"]["controller"]
        transport = report["observations"]["transport"]
        for field in ("is_playing", "is_recording"):
            if project.get(field) is not False or transport.get(field) is not False:
                report["blockers"].append(dict(code=field, detail="Stop before guarded edits"))
        if not controller.get("safe_to_edit"):
            report["blockers"].append(
                dict(code="host_busy", detail="Inspect FL dialogs/render state")
            )
        report.update(
            success=True,
            status="ready_for_preflight" if not report["blockers"] else "observation_only",
            project_revision=project.get("revision"),
            saved_path_verified=False,
            next_tools=["fl_workflow_catalog", "fl_production_inspect"],
        )
        if project.get("context", {}).get("project_title") == "":
            report["blank_project_start"] = (
                "Before adding sounds or notes, use fl_project_create to initialize a named "
                "FLP from the clean Empty project. The tool performs the first save; "
                "do not ask the user to save manually. It preflights the actual blank state."
            )
            report["next_tools"].insert(0, "fl_project_create")
    except Exception as exc:
        report["blockers"].append(dict(code="inspection_failed", detail=str(exc)))
    return report
