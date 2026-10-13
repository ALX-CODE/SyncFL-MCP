# MCP tool reference

**147 registered tools.** Generated from the server's actual MCP definitions.
The default [discovery interface](../TOOL-DISCOVERY.md) advertises core production tools;
this reference documents the complete underlying catalog (also available in `full` mode).
Run `python scripts/generate_tool_docs.py --check` to detect drift.

Start with [capabilities](../CAPABILITIES.md) for tasks and limitations,
or [workflows](../WORKFLOWS.md) for examples. The reference below retains
each tool's own description, input schema and defaults.
The [JSON reference](tools.json) contains the complete machine-readable schemas.

## How to read this reference

- **Offline:** the runtime catalog classifies this tool as independent of the live DAW.
- **Read policy:** the runtime explicitly permits observation; other tools are treated as
  potential writes. These labels are scheduling hints, not permission grants.
- **Backend only:** desktop input is unavailable. Legacy contracts with no backend
  route remain documented for compatibility but are not recommended by discovery.
- Parameter names, units and target indices are exact. Omitted optional parameters
  use the defaults below. Nested structures are described by the tool and JSON schema.
- Previews, receipts and revisions are tool-specific. Read the full description before
  applying a mutation; a failed or timed-out call can have a partial outcome.

## Index

- [`fl_arm_track`](#fl_arm_track)
- [`fl_audio_bpm`](#fl_audio_bpm)
- [`fl_audio_clip_add`](#fl_audio_clip_add)
- [`fl_audio_compare`](#fl_audio_compare)
- [`fl_audio_edit`](#fl_audio_edit)
- [`fl_audio_inspect`](#fl_audio_inspect)
- [`fl_audio_key`](#fl_audio_key)
- [`fl_audio_loudness`](#fl_audio_loudness)
- [`fl_automation_create_channel`](#fl_automation_create_channel)
- [`fl_automation_file_edit`](#fl_automation_file_edit)
- [`fl_automation_file_inspect`](#fl_automation_file_inspect)
- [`fl_automation_file_retarget`](#fl_automation_file_retarget)
- [`fl_automation_links_inspect`](#fl_automation_links_inspect)
- [`fl_automation_target_inspect`](#fl_automation_target_inspect)
- [`fl_background_effect_inspect`](#fl_background_effect_inspect)
- [`fl_background_effect_load`](#fl_background_effect_load)
- [`fl_background_piano_inspect`](#fl_background_piano_inspect)
- [`fl_background_piano_select`](#fl_background_piano_select)
- [`fl_background_piano_write`](#fl_background_piano_write)
- [`fl_batch`](#fl_batch)
- [`fl_build_info`](#fl_build_info)
- [`fl_capabilities`](#fl_capabilities)
- [`fl_channel_configure`](#fl_channel_configure)
- [`fl_clear_piano_roll`](#fl_clear_piano_roll)
- [`fl_clear_request_queue`](#fl_clear_request_queue)
- [`fl_connect`](#fl_connect)
- [`fl_connection_status`](#fl_connection_status)
- [`fl_delete_notes`](#fl_delete_notes)
- [`fl_effect_add`](#fl_effect_add)
- [`fl_ensure_mixer_tracks`](#fl_ensure_mixer_tracks)
- [`fl_find_assets`](#fl_find_assets)
- [`fl_get_all_channels`](#fl_get_all_channels)
- [`fl_get_all_mixer_tracks`](#fl_get_all_mixer_tracks)
- [`fl_get_channel_count`](#fl_get_channel_count)
- [`fl_get_channel_info`](#fl_get_channel_info)
- [`fl_get_grid_bit`](#fl_get_grid_bit)
- [`fl_get_mixer_track_count`](#fl_get_mixer_track_count)
- [`fl_get_mixer_track_info`](#fl_get_mixer_track_info)
- [`fl_get_piano_roll_info`](#fl_get_piano_roll_info)
- [`fl_get_piano_roll_state`](#fl_get_piano_roll_state)
- [`fl_get_plugin_color`](#fl_get_plugin_color)
- [`fl_get_plugin_name`](#fl_get_plugin_name)
- [`fl_get_plugin_param_count`](#fl_get_plugin_param_count)
- [`fl_get_plugin_param_value`](#fl_get_plugin_param_value)
- [`fl_get_plugin_params`](#fl_get_plugin_params)
- [`fl_get_preset_count`](#fl_get_preset_count)
- [`fl_get_selected_channel`](#fl_get_selected_channel)
- [`fl_get_song_length`](#fl_get_song_length)
- [`fl_get_step_sequence`](#fl_get_step_sequence)
- [`fl_get_transport_status`](#fl_get_transport_status)
- [`fl_instrument_add`](#fl_instrument_add)
- [`fl_is_plugin_valid`](#fl_is_plugin_valid)
- [`fl_midi_reference_search`](#fl_midi_reference_search)
- [`fl_mix_diagnose`](#fl_mix_diagnose)
- [`fl_mix_profile`](#fl_mix_profile)
- [`fl_mixer_controls_inspect`](#fl_mixer_controls_inspect)
- [`fl_mixer_controls_set`](#fl_mixer_controls_set)
- [`fl_mixer_meter_watch`](#fl_mixer_meter_watch)
- [`fl_mixer_scene`](#fl_mixer_scene)
- [`fl_mute_channel`](#fl_mute_channel)
- [`fl_mute_track`](#fl_mute_track)
- [`fl_native_call`](#fl_native_call)
- [`fl_next_preset`](#fl_next_preset)
- [`fl_notes_transform`](#fl_notes_transform)
- [`fl_piano_roll_prepare`](#fl_piano_roll_prepare)
- [`fl_play`](#fl_play)
- [`fl_playlist_file_edit`](#fl_playlist_file_edit)
- [`fl_playlist_file_inspect`](#fl_playlist_file_inspect)
- [`fl_plugin_adapter_catalog`](#fl_plugin_adapter_catalog)
- [`fl_plugin_add`](#fl_plugin_add)
- [`fl_plugin_apply`](#fl_plugin_apply)
- [`fl_plugin_chain`](#fl_plugin_chain)
- [`fl_plugin_controls_inspect`](#fl_plugin_controls_inspect)
- [`fl_plugin_controls_set`](#fl_plugin_controls_set)
- [`fl_plugin_inspect`](#fl_plugin_inspect)
- [`fl_plugin_parameters`](#fl_plugin_parameters)
- [`fl_plugin_remove`](#fl_plugin_remove)
- [`fl_plugin_set_display_value`](#fl_plugin_set_display_value)
- [`fl_plugin_set_many`](#fl_plugin_set_many)
- [`fl_preset_load`](#fl_preset_load)
- [`fl_prev_preset`](#fl_prev_preset)
- [`fl_production_inspect`](#fl_production_inspect)
- [`fl_project_create`](#fl_project_create)
- [`fl_project_file_inspect`](#fl_project_file_inspect)
- [`fl_project_info`](#fl_project_info)
- [`fl_project_native_inspect`](#fl_project_native_inspect)
- [`fl_project_open`](#fl_project_open)
- [`fl_project_package`](#fl_project_package)
- [`fl_project_save`](#fl_project_save)
- [`fl_project_save_as`](#fl_project_save_as)
- [`fl_project_save_native`](#fl_project_save_native)
- [`fl_project_settings`](#fl_project_settings)
- [`fl_project_snapshot`](#fl_project_snapshot)
- [`fl_receipt`](#fl_receipt)
- [`fl_record`](#fl_record)
- [`fl_recording_arm`](#fl_recording_arm)
- [`fl_recording_configure_file`](#fl_recording_configure_file)
- [`fl_recording_inspect`](#fl_recording_inspect)
- [`fl_recording_start`](#fl_recording_start)
- [`fl_recording_status`](#fl_recording_status)
- [`fl_recording_stop`](#fl_recording_stop)
- [`fl_render_cancel`](#fl_render_cancel)
- [`fl_render_start`](#fl_render_start)
- [`fl_render_status`](#fl_render_status)
- [`fl_route_channel_to_mixer`](#fl_route_channel_to_mixer)
- [`fl_routing_inspect`](#fl_routing_inspect)
- [`fl_routing_scene`](#fl_routing_scene)
- [`fl_sample_add`](#fl_sample_add)
- [`fl_select_channel`](#fl_select_channel)
- [`fl_select_one_channel`](#fl_select_one_channel)
- [`fl_send_chord`](#fl_send_chord)
- [`fl_send_notes`](#fl_send_notes)
- [`fl_serum_analyze_audio`](#fl_serum_analyze_audio)
- [`fl_serum_apply`](#fl_serum_apply)
- [`fl_serum_build_preset`](#fl_serum_build_preset)
- [`fl_serum_edit_preset`](#fl_serum_edit_preset)
- [`fl_serum_inspect`](#fl_serum_inspect)
- [`fl_serum_inspect_preset`](#fl_serum_inspect_preset)
- [`fl_serum_load_preset`](#fl_serum_load_preset)
- [`fl_serum_probe`](#fl_serum_probe)
- [`fl_serum_recipes`](#fl_serum_recipes)
- [`fl_session_inspect`](#fl_session_inspect)
- [`fl_set_channel_color`](#fl_set_channel_color)
- [`fl_set_channel_name`](#fl_set_channel_name)
- [`fl_set_channel_pan`](#fl_set_channel_pan)
- [`fl_set_channel_volume`](#fl_set_channel_volume)
- [`fl_set_grid_bit`](#fl_set_grid_bit)
- [`fl_set_loop_mode`](#fl_set_loop_mode)
- [`fl_set_playback_speed`](#fl_set_playback_speed)
- [`fl_set_plugin_param_value`](#fl_set_plugin_param_value)
- [`fl_set_song_position`](#fl_set_song_position)
- [`fl_set_step_sequence`](#fl_set_step_sequence)
- [`fl_set_stereo_separation`](#fl_set_stereo_separation)
- [`fl_set_track_color`](#fl_set_track_color)
- [`fl_set_track_name`](#fl_set_track_name)
- [`fl_set_track_pan`](#fl_set_track_pan)
- [`fl_set_track_volume`](#fl_set_track_volume)
- [`fl_solo_channel`](#fl_solo_channel)
- [`fl_solo_track`](#fl_solo_track)
- [`fl_step_sequence_edit`](#fl_step_sequence_edit)
- [`fl_stop`](#fl_stop)
- [`fl_trigger_note`](#fl_trigger_note)
- [`fl_trigger_script`](#fl_trigger_script)
- [`fl_ui_action`](#fl_ui_action)
- [`fl_ui_inspect`](#fl_ui_inspect)
- [`fl_workflow_catalog`](#fl_workflow_catalog)
- [`fl_write_pattern_bank`](#fl_write_pattern_bank)

## fl_arm_track

Live / operation-dependent · Potential write policy

Toggle recording arm on one mixer strip; use readback to determine its new state.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L84)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |

## fl_audio_bpm

Offline · Read policy

Estimate a WAV's tempo with the bundled Rust core; no FL or desktop input.

Returns BPM candidates, periodicity, window consistency and half/double
relationships. Confidence is relative among returned candidates, not a
probability of correctness. Silence/insufficient pulse produces no BPM.
Uses at most the first 180 seconds and reports the analyzed duration.
Fixed-tempo estimate only: variable tempo and sparse/syncopated music can
be ambiguous. min/max bound search (20..400 BPM), top_n is 1..10.
Supports PCM/IEEE float RIFF WAV; the bundled runtime is Windows x64.
Never changes project tempo, stretches audio or writes the input file.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `min_bpm` | number | No | 40.0 |
| `max_bpm` | number | No | 240.0 |
| `top_n` | integer | No | 5 |

## fl_audio_clip_add

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Creating a new Playlist audio channel has no
backend implementation. Saved-FLP edits can reuse existing audio channels.

[Implementation](../../src/fl_studio_mcp/tools/playlist_import.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `expected_audio_sha256` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `directory` | string | Yes | — |
| `position_ticks` | integer | Yes | — |
| `track` | integer | Yes | — |
| `apply` | boolean | No | false |

## fl_audio_compare

Offline · Read policy

Compare full mix/reference files by measured loudness, peaks, RMS and stereo metrics.

Read-only fl_audio_loudness bounds and dependencies apply to each file.
Deltas are candidate minus reference. Optional constant-gain estimate
reports matching gain, predicted true peak, ceiling headroom and gain
limited by the explicit -24..0 dBTP ceiling. It never applies gain or
claims perceptual similarity. Files are not time-aligned; duration and
channel-count mismatches are reported. Remeasure audio after processing.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L134)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `reference_path` | string | Yes | — |
| `level_match` | boolean | No | true |
| `true_peak_ceiling_dbtp` | number | No | -1.0 |
| `activity_dbfs` | number | No | -60.0 |
| `timeout_seconds` | number | No | 120.0 |
| `dual_mono` | boolean | No | false |

## fl_audio_edit

Offline · Potential write policy

Trim/reverse/fade/gain a sample to a NEW WAV with exact frame and sample readback.

Source hash from fl_audio_inspect. End frame is exclusive. Linear fades
use zero or at least two frames and cannot overlap. Normalization applies
after fades and requires gain_db=0. Formats: PCM_16/PCM_24/FLOAT. PCM clipping
is rejected. Preview by default; select <=48 million interleaved samples.
Source stays unchanged; loop/cue metadata is not copied. Use fl_sample_add
to load the result into FL. Requires the audio dependency extra.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L160)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `output` | string | Yes | — |
| `expected_sha256` | string | Yes | — |
| `start_frame` | integer | No | 0 |
| `end_frame` | integer or null | No | null |
| `gain_db` | number | No | 0.0 |
| `fade_in_frames` | integer | No | 0 |
| `fade_out_frames` | integer | No | 0 |
| `reverse` | boolean | No | false |
| `normalize_peak_dbfs` | number or null | No | null |
| `subtype` | string | No | "PCM_24" |
| `dry_run` | boolean | No | true |

## fl_audio_inspect

Offline · Read policy

Measure a WAV/FLAC/etc: hash, duration, levels, DC, silence and stereo correlation.

Streams songs up to two hours/eight channels. Includes one-second level
timeline; no LUFS/true-peak claim. All channels contribute to levels, so
anti-phase stereo is not incorrectly classified as silence.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L101)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `activity_dbfs` | number | No | -60.0 |

## fl_audio_key

Offline · Read policy

Rank global major/minor key candidates from a WAV, without FL or desktop input.

NumPy only (install the key extra). Reads PCM 8/16/24/32-bit and IEEE
float 32/64-bit RIFF WAVs, 1..8 channels, up to two hours. Uses a 4096/1024
Hann STFT, 55..5000 Hz, A440 and Krumhansl-Kessler profiles. top_n: 1..24;
temperature must be positive. Confidence is a relative softmax share of
ALL 24 candidates, not a probability of correctness. Silence/flat chroma
returns no candidates. A global estimate cannot track modulations and
can confuse relative keys, percussion, detuning or short excerpts.
Never changes audio, the project or plugin tuning settings.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L25)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `top_n` | integer | No | 5 |
| `temperature` | number | No | 0.15 |

## fl_audio_loudness

Offline · Read policy

Read a complete audio file's LUFS, loudness range and oversampled true peak.

Requires the audio extra and installed FFmpeg with loudnorm (PATH,
~/.local/bin, or FL_STUDIO_MCP_FFMPEG). Mono/stereo, 8..192 kHz, <=2 hours
and <=4 GiB. FFmpeg has a per-file 1..300-second timeout. No audio is
written; source hash is checked. Compact sample/RMS/DC/stereo metrics
accompany EBU R128 input measurements. Silence, below-gate material and
<400 ms files have unavailable LUFS; LRA is unavailable below 3 seconds.
dual_mono=true models mono playback through two speakers. Does not use FL.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L113)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `activity_dbfs` | number | No | -60.0 |
| `timeout_seconds` | number | No | 120.0 |
| `dual_mono` | boolean | No | false |

## fl_automation_create_channel

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Creating a new automation link has no backend
implementation. Use automation inspection and saved-FLP editing for existing
automation.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L8)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `expected_name` | string | Yes | — |
| `parameter` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `directory` | string | Yes | — |
| `apply` | boolean | No | false |

## fl_automation_file_edit

Offline · Potential write policy

Edit existing automation precisely in a NEW FLP; original/live project untouched.

edits: [{channel,expected_name,operation:'upsert'|'replace',points:
[{tick:integer,value:0..1,mode:'linear'|'hold'}]}]. Mode controls the
incoming segment. Values are normalized, never dB. Output must not exist.
Inspect/review the result, then explicitly open that FLP to use it in FL.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L137)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `output` | string | Yes | — |
| `expected_sha256` | string | Yes | — |
| `edits` | array of object | Yes | — |
| `dry_run` | boolean | No | true |

## fl_automation_file_inspect

Offline · Read policy

Read saved FLP automation names, local ticks, normalized values and SHA256.

Saved-file state only. Call fl_project_save first to include unsaved edits.
Local ticks start at each clip's start, not the song's start.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L126)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |

## fl_automation_file_retarget

Live / operation-dependent · Potential write policy

Preview or redirect one existing automation link into a NEW saved FLP.

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

[Implementation](../../src/fl_studio_mcp/tools/automation_targets.py#L39)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `output` | string | Yes | — |
| `expected_sha256` | string | Yes | — |
| `automation_channel` | integer | Yes | — |
| `expected_name` | string | Yes | — |
| `expected_event_id` | integer | Yes | — |
| `target` | object | Yes | — |
| `expected_target_revision` | string | Yes | — |
| `expected_event_name` | string | Yes | — |
| `apply` | boolean | No | false |

## fl_automation_links_inspect

Offline · Read policy

Read exact saved FL26.1 automation links and compact curve identities.

source is an absolute FLP path (<=256 MiB). Optional channel/event_id
filter, limit 1..500. Reports source SHA256, native event IDs, raw link
bytes and matching curve names/hashes. Unsupported link layouts fail
explicitly. Never opens FL or returns giant point arrays.

[Implementation](../../src/fl_studio_mcp/tools/automation_targets.py#L23)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `channel` | integer or null | No | null |
| `event_id` | integer or null | No | null |
| `offset` | integer | No | 0 |
| `limit` | integer | No | 100 |

## fl_automation_target_inspect

Live / operation-dependent · Read policy

Resolve a precise FL26.1 automation target through independent host readbacks.

target requires kind and index (GLOBAL channel or zero-based mixer track).
Kinds: channel_volume, channel_pan, mixer_volume, mixer_pan, mixer_send
(also destination), generator_parameter (also parameter), effect_parameter
(also parameter and zero-based slot_index 0..9). Send destination 0..127
must already be enabled. Unresolved native event labels are rejected.
Returns actual names, native full/short event labels, event ID, project
context and revision. Review the label and parameter name before retargeting.
This only reads the host; it neither creates a clip nor moves a control.

[Implementation](../../src/fl_studio_mcp/tools/automation_targets.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `target` | object | Yes | — |

## fl_background_effect_inspect

Live / operation-dependent · Read policy

Inspect an empty slot on the already selected visible mixer track.

Returns a revision binding native project identity, all mixer slot names,
selection and fresh HWND geometry. Does not select, show or focus anything.
The private OLE route is experimental and requires a verified local runtime.

[Implementation](../../src/fl_studio_mcp/tools/background_loading.py#L13)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |

## fl_background_effect_load

Live / operation-dependent · Potential write policy

Preview or load one valid .fst into an empty, already selected mixer slot.

Pass the exact native plugin name, inspection revision and main-window title.
Apply requires a NEW absolute JSON receipt, dispatches once, then checks all
mixer slot identities in later native callbacks. Busy read callbacks may be
retried; the drop never is. Does not change selection, mouse, keys or focus.
Unknown callbacks or mismatches stay unverified; inspect before any recovery.

[Implementation](../../src/fl_studio_mcp/tools/background_loading.py#L23)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `path` | string | Yes | — |
| `expected_plugin` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `expected_window_title` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_background_piano_inspect

Live / operation-dependent · Read policy

Inspect a saved FL26 score and its live native target without menus.

Supply the exact active .flp path and zero-based channel. Requires a saved,
stopped project. Returns the pattern/name, generator or sample source,
saved generator-state fingerprint, mixer routing, effect slots and revision.
This reads saved notes; the write tool verifies its exact disk path after save.

[Implementation](../../src/fl_studio_mcp/tools/background_piano.py#L12)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `project_path` | string | Yes | — |
| `channel` | integer | Yes | — |

## fl_background_piano_select

Live / operation-dependent · Potential write policy

Select an existing pattern and sound through native APIs, then verify both.

Inspect the target channel first; pass its revision. Returns the selected
pattern name, generator identity, saved preset fingerprint, mixer routing
and actual effect slots. Opens its piano roll and selects its routed
mixer track without menu clicks, so subsequent effect operations match.
FL marks pattern selection dirty, so a changed pattern is saved with
before/after recovery copies and exact verification of all existing notes.

[Implementation](../../src/fl_studio_mcp/tools/background_piano.py#L23)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `project_path` | string | Yes | — |
| `channel` | integer | Yes | — |
| `pattern` | integer | Yes | — |
| `expected_revision` | string | Yes | — |

## fl_background_piano_write

Live / operation-dependent · Potential write policy

Replace one piano-roll score through native FSC import; no ComposeWithLLM.

Uses the current pattern and explicit channel. Basic note fields: midi,
time/duration in beats or time_ticks/length_ticks, velocity, pan, release,
color and slide. Saves the active project and checks exact notes plus all
other channel/pattern notes in the supplied .flp. Keeps before/after copies.
Native panel selection is automatic. No keyboard, mouse or interactive fallback.

[Implementation](../../src/fl_studio_mcp/tools/background_piano.py#L37)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `project_path` | string | Yes | — |
| `channel` | integer | Yes | — |
| `notes` | array of object | Yes | — |
| `expected_revision` | string | Yes | — |
| `expected_channel` | string | Yes | — |
| `receipt_file` | string | Yes | — |
| `apply` | boolean | No | false |

## fl_batch

Live / operation-dependent · Potential write policy

Preflight then execute up to 256 typed operations. Defaults to dry run.

Each step is {action, params}. Slots/channels/mixer tracks are zero-based.
Stops on first failure and reports applied count; no automatic rollback.
Use fl_capabilities for actions and fl_native_call for documented API calls.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L155)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `steps` | array of object | Yes | — |
| `dry_run` | boolean | No | true |

## fl_build_info

Offline · Read policy

Identify the loaded server version and production workflow capabilities.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L77)

No input parameters.

## fl_capabilities

Live / operation-dependent · Read policy

Discover available native signatures and typed bulk commands in the live controller.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L150)

No input parameters.

## fl_channel_configure

Live / operation-dependent · Potential write policy

Configure an inspected GLOBAL channel: gain, pan, pitch, mute or routing.

Volume is normalized 0..1; pitch is semitones. Apply requires the unchanged
channel revision and a new absolute receipt. Readback runs in new callbacks.

[Implementation](../../src/fl_studio_mcp/tools/production.py#L65)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `changes` | object | Yes | — |
| `expected_revision` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_clear_piano_roll

Live / operation-dependent · Potential write policy

Clear notes from the inspected roll; preserve markers and record recovery data.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L68)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `auto_trigger` | boolean | No | true |

## fl_clear_request_queue

Live / operation-dependent · Potential write policy

Acknowledge completed work. Refuse to discard unresolved requests/recovery data.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L90)

No input parameters.

## fl_connect

Live / operation-dependent · Potential write policy

Open a fresh dedicated MIDI session and verify the live controller handshake.

[Implementation](../../src/fl_studio_mcp/server.py#L98)

No input parameters.

## fl_connection_status

Live / operation-dependent · Read policy

Report endpoint availability separately from a verified live controller response.

[Implementation](../../src/fl_studio_mcp/server.py#L118)

No input parameters.

## fl_delete_notes

Live / operation-dependent · Potential write policy

Delete all matches by midi and time/time_ticks, preserving other notes.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L63)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `notes` | array of object | Yes | — |
| `auto_trigger` | boolean | No | true |

## fl_effect_add

Live / operation-dependent · Potential write policy

Add an effect to an empty mixer slot, then verify the actual plugin.

track is zero-based; slot 0..9 corresponds to FL slots 1..10. Supply an
exact Plugin database effect name or an absolute effect .fst path. Names
prefer active-user favorites and reject ambiguity. Occupied slots are
guarded; this does not grant permission to replace another effect.
Experimental native OLE bridge with native track selection and readback.

[Implementation](../../src/fl_studio_mcp/tools/studio_files.py#L74)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `name` | string or null | No | null |
| `path` | string or null | No | null |

## fl_ensure_mixer_tracks

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Appending mixer inserts has no backend
implementation. Use native mixer tools with existing track indices.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L141)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `last_insert` | integer | Yes | — |

## fl_find_assets

Offline · Read policy

Find exact paths for samples, .fst/.serumpreset presets and installed plugins.

Read-only; FL need not be running. Query matches filenames without case
sensitivity; an empty query lists assets. Search active FL user-data and
installed stock folders, or provide absolute asset directories in roots.
Generator/effect identity comes from Plugin database directory structure.
Check scan_complete/truncated and narrow roots when incomplete. Duplicate
names remain separate paths; never choose a fuzzy plugin match to load.
limit is 1..100. Drive roots, symlinks and junction traversal are excluded.

[Implementation](../../src/fl_studio_mcp/tools/studio_files.py#L12)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `query` | string | Yes | — |
| `kind` | `all` / `sample` / `preset` / `plugin` / `generator` / `effect` | No | "all" |
| `roots` | array of string or null | No | null |
| `limit` | integer | No | 30 |

## fl_get_all_channels

Live / operation-dependent · Read policy

List every Channel Rack entry with selection, mute and mixer routing.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L34)

No input parameters.

## fl_get_all_mixer_tracks

Live / operation-dependent · Read policy

Inspect occupied mixer strips, or the whole mixer when include_empty is true.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L34)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `include_empty` | boolean | No | false |

## fl_get_channel_count

Live / operation-dependent · Read policy

Count Channel Rack entries globally, or within the current group.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L16)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `global_count` | boolean | No | true |

## fl_get_channel_info

Live / operation-dependent · Read policy

Inspect a Channel Rack entry; global indexing is the default.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L26)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `use_global_index` | boolean | No | true |

## fl_get_grid_bit

Live / operation-dependent · Read policy

Read one zero-based step of the current pattern.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L159)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `position` | integer | Yes | — |

## fl_get_mixer_track_count

Live / operation-dependent · Read policy

Read the host mixer capacity, including its master strip; -1 indicates failure.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L16)

No input parameters.

## fl_get_mixer_track_info

Live / operation-dependent · Read policy

Inspect a zero-based mixer strip, including native gain and dB readback.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L26)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |

## fl_get_piano_roll_info

Live / operation-dependent · Potential write policy

Report protocol, paths and pending work without triggering FL.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L109)

No input parameters.

## fl_get_piano_roll_state

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Legacy script-based piano inspection is
unavailable. Use fl_background_piano_inspect.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L73)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `fresh` | boolean | No | true |

## fl_get_plugin_color

Live / operation-dependent · Read policy

Read the host color assigned to an existing generator or effect.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L170)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_get_plugin_name

Live / operation-dependent · Read policy

Read the existing generator/effect name at the supplied location.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L31)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_get_plugin_param_count

Live / operation-dependent · Read policy

Count exposed controls of an existing plugin; -1 indicates failure.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L45)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_get_plugin_param_value

Live / operation-dependent · Read policy

Read one exposed plugin control, including its displayed value.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L82)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `param_index` | integer | Yes | — |
| `plugin_index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_get_plugin_params

Live / operation-dependent · Read policy

Read a bounded set of exposed controls, normalized values and displayed text.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L61)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |
| `max_params` | integer | No | 50 |

## fl_get_preset_count

Live / operation-dependent · Read policy

Read the preset count exposed by an existing generator or effect.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L128)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_get_selected_channel

Live / operation-dependent · Read policy

Inspect the selected Channel Rack entry; null means no selection.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L44)

No input parameters.

## fl_get_song_length

Live / operation-dependent · Read policy

Read the host timeline duration in ticks, seconds and milliseconds.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L64)

No input parameters.

## fl_get_step_sequence

Live / operation-dependent · Read policy

Read the requested number of current-pattern steps as boolean states.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L179)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `steps` | integer | No | 16 |

## fl_get_transport_status

Live / operation-dependent · Read policy

Read playback, recording, timeline position and song/pattern mode.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L46)

No input parameters.

## fl_instrument_add

Live / operation-dependent · Potential write policy

Add a generator as a new Channel Rack instrument and verify its identity.

Supply an exact Plugin database name or an absolute generator .fst path.
Name resolution prefers the active user's Generator favorites, rejects
ambiguity, then searches installed entries. Optional channel_name and
zero-based mixer_track name/route the new channel. Windows FL required.
Experimental native OLE bridge; no keyboard, mouse or mode switch.

[Implementation](../../src/fl_studio_mcp/tools/studio_files.py#L52)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | string or null | No | null |
| `path` | string or null | No | null |
| `channel_name` | string or null | No | null |
| `mixer_track` | integer or null | No | null |

## fl_is_plugin_valid

Live / operation-dependent · Read policy

Test an existing plugin location. Slot -1 addresses a generator; 0..9 addresses an
effect.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L16)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_midi_reference_search

Offline · Read policy

Find local .mid/.midi references by filename/path tokens or note fingerprints.

Read-only; no FL, network, database or cache required. roots must be explicit
absolute directories; omission uses repository references/lead-midi only
when present. Drive roots, symlinks/junctions and SMPTE/type-2 MIDI are
excluded. Check scan_complete, truncated and errors before treating the
results as a complete corpus search. Limits include 1 MiB/file, 32 MiB total,
100000 events/file, 256 tracks, depth 16 and a 10-second scan budget.

query_notes is an optional list of exactly {pitch: 0..127, start_beats:
nonnegative number, duration_beats: positive number}, maximum 1024 notes.
All times use quarter-note beats, independent of embedded tempo. Rankings
expose filename token coverage and histogram intersection for pitch class,
onset phase and duration. Similarity is not a sound or musical-quality
score and does not compare melodic order. Unclosed note durations are
excluded; overlapping same-pitch notes pair FIFO per track/channel.

Returns exact paths, SHA-256 hashes, per-track durations/note counts and
fingerprints. A path identifies a reference to inspect/audition; it does
not import MIDI or modify the live project. Extracted reference tempos may
be placeholders and generic MIDI does not preserve all FL articulation.

[Implementation](../../src/fl_studio_mcp/tools/midi_reference.py#L12)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `query` | string | No | "" |
| `roots` | array of string or null | No | null |
| `limit` | integer | No | 20 |
| `query_notes` | array of object or null | No | null |
| `max_files` | integer | No | 250 |

## fl_mix_diagnose

Offline · Read policy

Diagnose measured rendered audio; propose changes without applying them.

Absolute local mono/stereo files only, 8..192 kHz, <=2 hours and <=4 GiB.
Requires the audio extra and existing FFmpeg; timeout is 1..300 seconds
per file. Reports real LUFS/true peak, full-scale sample counts and source
hashes. Choose reference_path OR target_lufs (-70..0), never both.
The explicit peak ceiling is -24..0 dBTP. Optional gain proposals respect
that ceiling; predictions require audition and remeasurement. References
are measured independently without time alignment. No FL calls, writes,
automatic processing or diagnosis from track names. No audible-quality,
spectral, EQ or compression claim. dual_mono models stereo playback of mono.

[Implementation](../../src/fl_studio_mcp/tools/mix_diagnostics.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `reference_path` | string or null | No | null |
| `target_lufs` | number or null | No | null |
| `true_peak_ceiling_dbtp` | number | No | -1.0 |
| `activity_dbfs` | number | No | -60.0 |
| `timeout_seconds` | number | No | 120.0 |
| `dual_mono` | boolean | No | false |

## fl_mix_profile

Live / operation-dependent · Potential write policy

Preflight/apply named plugin controls; require a new receipt file for writes.

Profile controls: track, slot, parameter, plugin, name; either value (0..1)
or target with unit and optional tolerance. Optional display verifies a
known enum label. Stopped transport required. Receipt includes restore_profile.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L213)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `profile` | object | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_mixer_controls_inspect

Live / operation-dependent · Read policy

Read a strip's native EQ, dB/Hz, effects enable, polarity and stereo swap.

Does not select the track or change focus. Returns a revision for guarded writes.

[Implementation](../../src/fl_studio_mcp/tools/mixer_controls.py#L8)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |

## fl_mixer_controls_set

Live / operation-dependent · Potential write policy

Preflight/apply built-in EQ and mixer switches without desktop input.

changes accepts bands:[{band, gain_normalized?, frequency_normalized?,
bandwidth_normalized?}] in 0..1, plus effects_enabled, reverse_polarity,
swap_channels booleans. Band indices start at zero. Hz/dB readbacks are
observations, not input units. Requires stopped transport and exact name/revision.
Apply requires a new receipt_file. Two later reads verify requested controls.
Uncertain writes never retry or automatically restore; inspect the receipt.

[Implementation](../../src/fl_studio_mcp/tools/mixer_controls.py#L16)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `expected_name` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `changes` | object | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_mixer_meter_watch

Live / operation-dependent · Read policy

Sample native peaks on 1..32 exact mixer tracks for 0.1..10 seconds.

Zero-based tracks; optional exact expected_names must align with tracks.
Minimum interval is 50 ms, plus controller/readback overhead. Uses
a sampling window after setup; setup and the last sample can add at
most four seconds. Actual observed duration is returned explicitly.
Reports observed left/right/maximum peaks and complete sample counts; errors or
project/session/track identity changes return incomplete partial evidence.
Never starts playback, recording, or changes routing. Samples may miss
transients and are not LUFS, true peaks, waveform RMS or clipping proof.

[Implementation](../../src/fl_studio_mcp/tools/metering.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `tracks` | array of integer | Yes | — |
| `seconds` | number | No | 2.0 |
| `interval_ms` | integer | No | 100 |
| `expected_names` | array of string or null | No | null |

## fl_mixer_scene

Live / operation-dependent · Potential write policy

Preflight/apply a stopped-transport mix scene with names and readback.

Items: track,expected_name, optional volume (normalized) OR volume_db,
pan (-1..1), stereo (-1..1). Requires a new receipt_file for apply.
Automation can override faders during playback; edit its curve separately.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L156)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `items` | array of object | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_mute_channel

Live / operation-dependent · Potential write policy

Set Channel Rack mute, or toggle when muted is null.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L109)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `muted` | boolean or null | No | null |

## fl_mute_track

Live / operation-dependent · Potential write policy

Set mixer mute explicitly, or toggle with muted=null. Returns the observed state.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L64)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `muted` | boolean or null | No | null |

## fl_native_call

Live / operation-dependent · Potential write policy

Invoke one advertised FL native API function with positional args, no Python eval.

Advanced surface: caller must inspect the advertised signature and target indices.
Mutations require allow_write; typed batch operations provide stronger validation.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L167)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `function` | string | Yes | — |
| `args` | array of object | Yes | — |
| `allow_write` | boolean | No | false |

## fl_next_preset

Live / operation-dependent · Potential write policy

Advance the existing plugin preset once and acknowledge its identity.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L142)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_notes_transform

Offline · Read policy

Propose transposition/quantization offline, preserving all FL note expression.

Required ppq is explicit. notes uses fl_send_notes fields: midi, time/duration
in quarter-note beats or exact time_ticks/length_ticks; velocity, pan,
release, color, slide, porta, pitchofs, fcut, fres, muted, selected, group,
repeats are retained. Conflicting beat/tick aliases and unknown fields fail.
Velocity, pan, release, fcut and fres use normalized 0..1 values.
Up to 65536 notes; order and duplicates are preserved.

grid_ticks optionally quantizes onsets to multiples of that integer tick
spacing from zero, without assuming a time signature. strength 0..1 blends
from original to the nearest grid; quantize_lengths also quantizes lengths
using that grid/strength. Grid and blend ties round to the larger tick;
beat input conversion uses the shared nearest-even note protocol. Pitch or
timing overflow and zero quantized lengths fail, never clamp. No randomness.

Returns proposed normalized notes, changed_count, content hashes and
applied:false. Hashes describe ordered content plus PPQ, not live revisions.
Inspect the intended channel and fresh piano-roll state before explicitly
using the existing guarded note workflow. This never queues or applies notes.

[Implementation](../../src/fl_studio_mcp/tools/note_transforms.py#L12)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `notes` | array of object | Yes | — |
| `ppq` | integer | Yes | — |
| `transpose_semitones` | integer | No | 0 |
| `grid_ticks` | integer or null | No | null |
| `strength` | number | No | 1.0 |
| `quantize_lengths` | boolean | No | false |

## fl_piano_roll_prepare

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Legacy piano-script activation is unavailable.
Use fl_background_piano_inspect and fl_background_piano_select.

[Implementation](../../src/fl_studio_mcp/tools/studio_files.py#L120)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer or null | No | null |
| `expected_name` | string or null | No | null |

## fl_play

Live / operation-dependent · Potential write policy

Toggle playback and report the acknowledged play state.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L16)

No input parameters.

## fl_playlist_file_edit

Offline · Potential write policy

Edit exact pattern/audio/automation placements into a NEW FL26 FLP.

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

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L45)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `output` | string | Yes | — |
| `expected_sha256` | string | Yes | — |
| `edits` | array of object | Yes | — |
| `dry_run` | boolean | No | true |

## fl_playlist_file_inspect

Offline · Read policy

Inspect exact saved FL26 Playlist clip identities, ticks, tracks and patterns.

Save first to include current edits. Returns source/clip hashes needed by
fl_playlist_file_edit. Each event_index scopes one saved Playlist event.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L34)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |

## fl_plugin_adapter_catalog

Offline · Read policy

List packaged plugin adapter evidence, exact layouts and named control coverage.

Read-only and offline. Query matches adapter/plugin/control names. Numeric
controls without calibrated points/analytic curves say calibration_required:
physical units cannot be written. Numeric mappings require unit/tolerance;
display_unit may differ from semantic unit. Enum labels may declare an
exact display string, including whitespace. Conditions reference enum labels.
An entry does not establish that this plugin/version is currently loaded.

[Implementation](../../src/fl_studio_mcp/tools/plugin_adapters.py#L10)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `query` | string | No | "" |

## fl_plugin_add

Live / operation-dependent · Potential write policy

Load an exact Plugin database effect name into an empty mixer slot.

Uses the same guarded file loading as fl_effect_add. Indices are zero-based.
result_row must remain 0; use fl_effect_add(path=...) to resolve duplicate names.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L117)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `name` | string | Yes | — |
| `result_row` | integer | No | 0 |

## fl_plugin_apply

Live / operation-dependent · Potential write policy

Preview/apply 1..256 exact indexed controls to a stopped generator/effect.

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

[Implementation](../../src/fl_studio_mcp/tools/plugin_state.py#L24)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `expected_plugin` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `changes` | array of object | Yes | — |
| `slot_index` | integer | No | -1 |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_plugin_chain

Live / operation-dependent · Potential write policy

Load {track,slot,name,wet?} entries through the serialized native backend.

Names resolve exact Plugin database files. Target slots must be empty.
A receipt file records each verified slot for explicit inspection and resumption.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L106)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `items` | array of object | Yes | — |
| `receipt_file` | string or null | No | null |

## fl_plugin_controls_inspect

Live / operation-dependent · Read policy

Inspect named controls only after exact plugin, full-layout and parameter-name binding.

index is a global channel for slot_index=-1, otherwise a mixer track and
effect slot 0..9. Collects a complete revision-consistent named catalog
before filtering/pagination; refuses unknown or changed layouts. Returns
native normalized values, literal displays, calibration metadata and a
fresh complete-state revision and current condition availability. Host
caches may be stale after preset loads.
Identical names/layouts cannot prove a binary/build version; review evidence.
No parameter sweeps, probing or writes; no sound/DSP guarantee.

[Implementation](../../src/fl_studio_mcp/tools/plugin_adapters.py#L25)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `query` | string | No | "" |
| `offset` | integer | No | 0 |
| `limit` | integer | No | 100 |

## fl_plugin_controls_set

Live / operation-dependent · Potential write policy

Preflight/apply named plugin controls through the guarded plugin batch workflow.

Each distinct change is {control:key,value:physical_number_or_exact_enum_label}
or {control:key,normalized:0..1}. Physical values require monotonic calibrated
points or analytic curves plus unit/tolerance and numeric display checks;
no extrapolation. The current display must be finite and unit-compatible;
transition from Auto/INF using explicit normalized control, then inspect again.
Enum labels map to their declared exact literal display.
Explicit normalized writes do not claim physical-unit verification.
Conditions require matching fresh enum displays, including for normalized
writes. Change a condition selector separately, then inspect again before
changing its dependent controls; the same batch cannot contain both.

Requires fresh controls inspection and exact native expected_plugin. The
complete layout/parameter mapping must still match. Preview by default;
apply requires stopped transport and a NEW absolute JSON receipt. All
changes preflight before one batch and two later readbacks. No automatic
retries/rollback, calibration sweeps, or sonic/DSP-state guarantee.
Name/layout matching cannot distinguish different builds with identical controls.

[Implementation](../../src/fl_studio_mcp/tools/plugin_adapters.py#L48)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `expected_plugin` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `changes` | array of object | Yes | — |
| `slot_index` | integer | No | -1 |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_plugin_inspect

Live / operation-dependent · Read policy

Inspect an exact generator/effect, exposed controls and a complete-state revision.

index is a GLOBAL channel when slot_index=-1, otherwise a mixer track
with a zero-based effect slot 0..9. Reports actual and user plugin names,
target name, project session/generations, normalized host values and
literal displays. Query is a case-insensitive substring; offset/limit
paginate matches, omitting blank padding. Limit 1..500, catalog <=10000.
Revision covers all parameters, including those outside the returned page.
Names are not unique instance IDs; host caches can be stale after preset loads.

[Implementation](../../src/fl_studio_mcp/tools/plugin_state.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `query` | string | No | "" |
| `offset` | integer | No | 0 |
| `limit` | integer | No | 100 |

## fl_plugin_parameters

Live / operation-dependent · Read policy

Search exposed parameters, omitting blank VST padding. Slot 0 is FL Slot 1.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L183)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `query` | string | No | "" |
| `offset` | integer | No | 0 |
| `limit` | integer | No | 100 |

## fl_plugin_remove

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Removing a loaded plugin has no backend
implementation. Native controls can inspect and adjust an existing plugin.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L130)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `expected_plugin` | string | Yes | — |

## fl_plugin_set_display_value

Live / operation-dependent · Potential write policy

Calibrate a continuous control to its displayed dB/ms/etc value while stopped.

Probes the control briefly. Not for enumerations or non-monotonic displays.
Requires exact plugin, parameter name and unit. Reports restoration on failure.
Do not concurrently edit this control with another client.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L54)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `parameter` | integer | Yes | — |
| `expected_plugin` | string | Yes | — |
| `expected_name` | string | Yes | — |
| `target` | number | Yes | — |
| `unit` | string | Yes | — |
| `tolerance` | number | No | 0.01 |

## fl_plugin_set_many

Live / operation-dependent · Potential write policy

Set normalized parameters with plugin identity guard and before/after readback.

Each change: {parameter: zero-based index, value: 0..1, expected_name: optional}.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L194)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `slot` | integer | Yes | — |
| `expected_plugin` | string | Yes | — |
| `changes` | array of object | Yes | — |

## fl_preset_load

Live / operation-dependent · Potential write policy

Load an exact FL .fst preset into a new channel or a guarded existing target.

target selects new_channel, channel (with global channel index), or mixer
(with track and slot). Existing targets require a fresh expected_name.
All indices are zero-based; mixer slots are 0..9. This replaces the
target's sound/state, retaining an existing channel's name and mixer route.
Supports plugin .fst format 0x30; full mixer-track format 0x40 is rejected.
Use Serum tools for native .SerumPreset formats.
Inspect current identities before loading; uncertain writes are not retried.
Experimental native OLE bridge; desktop input is never used.

[Implementation](../../src/fl_studio_mcp/tools/studio_files.py#L93)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `target` | `new_channel` / `channel` / `mixer` | No | "new_channel" |
| `channel` | integer or null | No | null |
| `track` | integer or null | No | null |
| `slot` | integer or null | No | null |
| `expected_name` | string or null | No | null |

## fl_prev_preset

Live / operation-dependent · Potential write policy

Move the existing plugin preset back once and acknowledge its identity.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L156)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_production_inspect

Live / operation-dependent · Read policy

Read exact targets and a revision for guarded edits.

Channel indices are GLOBAL, zero-based. Steps need the currently selected
one-based pattern and an explicit total_steps extent (1..1024). Project
reports fractional BPM, shuffle (0..1), and recording PPQ.

[Implementation](../../src/fl_studio_mcp/tools/production.py#L34)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `scope` | `project` / `channel` / `steps` | Yes | — |
| `channel` | integer or null | No | null |
| `pattern` | integer or null | No | null |
| `total_steps` | integer or null | No | null |

## fl_project_create

Live / operation-dependent · Potential write policy

Initialize a named FLP from the current clean, unnamed Empty project.

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

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L8)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `receipt_file` | string or null | No | null |
| `apply` | boolean | No | false |

## fl_project_file_inspect

Offline · Read policy

Inspect a saved FLP's patterns, notes, playlist and external sample references.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L172)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |

## fl_project_info

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Dialog-based exact project-path discovery is
unavailable. Use fl_project_native_inspect for native project state; the
absolute path remains caller-declared.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L77)

No input parameters.

## fl_project_native_inspect

Live / operation-dependent · Read policy

Read the named FL window and project revision without mouse, keys or activation.

Returns qualification and readiness. The window title identifies the active host
project but does not establish its absolute path or verify saved disk bytes.

[Implementation](../../src/fl_studio_mcp/tools/project_native.py#L10)

No input parameters.

## fl_project_open

Live / operation-dependent · Potential write policy

Load an FL26 project through the native backend, preserving a recovery FLP.

Preflight by default. Use source SHA256 and fl_production_inspect project revision.
Supply current_project_path for the already named/saved active project.
For a clean unnamed blank session, use fl_project_create before composing.
Apply needs new absolute JSON receipt_file and recovery_path (.flp). Native-save
the current project before copying it to recovery; never discard dirty edits.
Verify generation, title, channels, source hash, tempo and PPQ. Absolute loaded
path is caller-declared, not independently exposed by FL. No input or mode switch.
Unnamed projects are unsupported; stop on dialogs or uncertain completion.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L29)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `expected_sha256` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `recovery_path` | string | Yes | — |
| `receipt_file` | string or null | No | null |
| `apply` | boolean | No | false |
| `current_project_path` | string or null | No | null |

## fl_project_package

Offline · Potential write policy

Package a SAVED project into a new folder with samples, MIDIs and hashes.

Does not save or mutate FL. Save once first for current edits. Copies only
referenced assets; retains original FLP plus a portable copy. Plugin
binaries/licenses are not included. Review missing/internal asset report.

[Implementation](../../src/fl_studio_mcp/tools/workflow.py#L179)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source` | string | Yes | — |
| `destination` | string | Yes | — |
| `path_variables` | object or null | No | null |

## fl_project_save

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Dialog-based exact-path saving is unavailable.
Use fl_project_save_native to save the current named project.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L87)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `expected_path` | string or null | No | null |

## fl_project_save_as

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Saving the active project under a new name has no
backend implementation. Use fl_project_save_native for an already named
project, or fl_project_create to initialize a clean unnamed Empty project.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L96)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `overwrite` | boolean | No | false |

## fl_project_save_native

Live / operation-dependent · Potential write policy

Preview or save the active named project without desktop input.

Requires the exact title/revision from fl_project_native_inspect and stopped
transport. Apply reserves a NEW absolute receipt_file, dispatches once, then
checks a dirty-to-clean transition with two later clean-state callbacks.
Already-clean projects return a no-op without dispatch. saved_current_host
confirms host state;
the absolute project path and filesystem persistence remain unverified.
Qualification remains host_state_only; no destination or filesystem claim is made.

[Implementation](../../src/fl_studio_mcp/tools/project_native.py#L19)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `expected_revision` | string | Yes | — |
| `expected_window_title` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_project_settings

Live / operation-dependent · Potential write policy

Preflight or set exact BPM, and shuffle where FL exposes its native API.

Inspect project first. Apply needs stopped transport, the unchanged
revision and a NEW absolute receipt_file. A null inspected shuffle means
unavailable on this build; its writes are rejected. PPQ/meter edits are not enabled.

[Implementation](../../src/fl_studio_mcp/tools/production.py#L51)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `changes` | object | Yes | — |
| `expected_revision` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_project_snapshot

Live / operation-dependent · Read policy

Read every channel, all mixer inserts, effect slots, routes and transport state.

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L178)

No input parameters.

## fl_receipt

Live / operation-dependent · Read policy

Inspect a timed-out command's receipt (last 128 requests, until controller reload).

[Implementation](../../src/fl_studio_mcp/tools/advanced.py#L208)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `request_id` | string | Yes | — |

## fl_record

Live / operation-dependent · Potential write policy

Toggle recording; inspect the returned state before sending another toggle.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L36)

No input parameters.

## fl_recording_arm

Live / operation-dependent · Potential write policy

Set an exact mixer track's desired arm state; never starts recording.

Use the target track's revision from fl_recording_inspect. Playback must be
stopped and recording off. Preview by default. Apply requires a NEW absolute
JSON receipt_file, including when the desired state is already satisfied.
Already-satisfied state dispatches no toggle. Otherwise dispatches FL's native
arm toggle ONCE, then independently verifies state in later callbacks.

Missing arm/read capability fails closed. Timeout, modal dialog, identity change
or uncertain completion retains a receipt; inspect before explicit recovery.
No automatic retry or restore. Cooperating clients share the GUI lock; user/legacy
actions may interleave. Arming does not prove a valid input or recorded take.

[Implementation](../../src/fl_studio_mcp/tools/recording_state.py#L25)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `expected_name` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `armed` | boolean | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_recording_configure_file

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Setting a recording filename has no backend
implementation. Use native recording inspection and arm controls for existing
targets.

[Implementation](../../src/fl_studio_mcp/tools/recording_jobs.py#L13)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `expected_name` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `recording_file` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_recording_inspect

Live / operation-dependent · Read policy

Read recording metadata and fresh per-track revisions without recording.

Supply exact zero-based tracks to inspect an unarmed target. Without tracks,
scans mixer tracks and returns compact armed-track entries.
Each returned track has a revision accepted by fl_recording_arm.

Native filenames are reported/proposed paths which can change with time; their
existence is not checked and they are excluded from the arm revision. They do
not prove a recorded take or configured input. Reports start-on-input when
advertised. Input device/channel, monitoring,
buffer size, recording latency and PDC remain explicitly unknown. Missing core
read capabilities fail closed; unavailable optional metadata is labeled.

[Implementation](../../src/fl_studio_mcp/tools/recording_state.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `tracks` | array of integer or null | No | null |

## fl_recording_start

Live / operation-dependent · Potential write policy

Preview/start a bounded 1..600 second native mixer recording into a NEW exact WAV.

First use fl_recording_configure_file for the filename, configure input/pickup
in FL, and arm only
the target with fl_recording_arm. Inspect its fresh per-track recording revision.
Playback and record latch must be off; precount and start-on-input must be off.
The native catalog has no input setters. Caller source descriptions remain
explicitly unverified. Use a NEW job directory separate from the WAV location.

Apply launches a persistent worker, returning queued, not a completed take.
It owns the DAW lease, revalidates everything, starts once and schedules Stop.
Poll fl_recording_status or request an early stop with fl_recording_stop.
Verified output requires stopped transport, a decoded stable WAV and requested
format/non-silence checks. Playlist placement/latency remain unverified.

[Implementation](../../src/fl_studio_mcp/tools/recording_jobs.py#L25)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `expected_name` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `recording_file` | string | Yes | — |
| `directory` | string | Yes | — |
| `duration_seconds` | number | Yes | — |
| `source_kind` | `internal` / `microphone` | Yes | — |
| `source_description` | string | Yes | — |
| `apply` | boolean | No | false |
| `expected_sample_rate` | integer or null | No | null |
| `expected_channels` | integer or null | No | null |
| `minimum_audio_seconds` | number | No | 0.1 |
| `require_non_silent` | boolean | No | true |

## fl_recording_status

Offline · Read policy

Read persistent recording status and take evidence without touching FL.

queued/recording are not completed takes. verified/stopped_early with
recorded_take_verified=true includes decoded audio/hash evidence. Stale worker
heartbeat or needs_inspection means transport may still be active; inspect
FL explicitly. Never replay a possibly delivered record/start/stop command.

[Implementation](../../src/fl_studio_mcp/tools/recording_jobs.py#L55)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `directory` | string | Yes | — |

## fl_recording_stop

Offline · Potential write policy

Request that the job's owned worker stop recording early; never toggles FL directly.

A saved request is not proof recording stopped. Poll fl_recording_status for
transport_stopped_verified and decoded take evidence. Repeated requests are
idempotent; a dead worker requires explicit host inspection/recovery.

[Implementation](../../src/fl_studio_mcp/tools/recording_jobs.py#L66)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `directory` | string | Yes | — |

## fl_render_cancel

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Cancelling a render has no backend
implementation. fl_render_status can observe an existing render.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L92)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `directory` | string | Yes | — |

## fl_render_start

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Starting a render has no backend implementation.
fl_render_status can observe an existing render.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L54)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `directory` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `options` | object or null | No | null |
| `apply` | boolean | No | false |
| `expected_sample_rate` | integer or null | No | null |
| `expected_min_seconds` | number or null | No | null |

## fl_render_status

Live / operation-dependent · Read policy

Observe a render job; verify WAVs, duration, levels and stem alignment after completion.

Returns rendering until the owned dialog closes; verified requires decoded,
stable WAVs matching options, unchanged saved project and some non-silent audio.
Running/verifying observations return success=true, verified=false. Only
status=verified has verified=true. Failed/cancelled jobs return success=false.
Job receipts persist across MCP reconnects. Never retries Start.

[Implementation](../../src/fl_studio_mcp/tools/audio.py#L78)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `directory` | string | Yes | — |

## fl_route_channel_to_mixer

Live / operation-dependent · Potential write policy

Assign a global Channel Rack entry to a zero-based mixer strip.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L149)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel_index` | integer | Yes | — |
| `mixer_track` | integer | Yes | — |

## fl_routing_inspect

Live / operation-dependent · Read policy

Inspect all mixer routes (including zero-level sidechains) with a fresh revision.

Returns exact track names, active routes, channel destinations, project identity
and transport. This is a read-only snapshot; use its revision for fl_routing_scene.

[Implementation](../../src/fl_studio_mcp/tools/routing.py#L6)

No input parameters.

## fl_routing_scene

Live / operation-dependent · Potential write policy

Preview/apply guarded sends, buses and sidechains; stopped transport required.

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

[Implementation](../../src/fl_studio_mcp/tools/routing.py#L17)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `items` | array of object | Yes | — |
| `expected_revision` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_sample_add

Live / operation-dependent · Potential write policy

Add a WAV as a new Channel Rack sampler through the native backend.

path must be an exact absolute sample path from fl_find_assets or the user.
mixer_track is a zero-based insert. OLE delivery and native readback verify
the new sampler channel, its name/type and routing; independent sample
source-path readback is unavailable. Experimental OLE bridge, no desktop input.
Requires running FL on Windows; do not retry
an uncertain result because the channel may already have been created.

[Implementation](../../src/fl_studio_mcp/tools/studio_files.py#L33)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `name` | string or null | No | null |
| `mixer_track` | integer or null | No | null |

## fl_select_channel

Live / operation-dependent · Potential write policy

Select or deselect one global Channel Rack index.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L54)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `select` | boolean | No | true |

## fl_select_one_channel

Live / operation-dependent · Potential write policy

Make one global Channel Rack entry the exclusive selection.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L64)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |

## fl_send_chord

Live / operation-dependent · Potential write policy

Write a chord through the same verified note transaction.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L41)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `midi_notes` | array of integer | Yes | — |
| `time` | number | No | 0 |
| `duration` | number | No | 1 |
| `velocity` | number | No | 0.8 |
| `auto_trigger` | boolean | No | true |

## fl_send_notes

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Legacy score delivery is unavailable. Use
fl_background_piano_write for persistent notes.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L30)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `notes` | array of object | Yes | — |
| `mode` | string | No | "add" |
| `auto_trigger` | boolean | No | true |

## fl_serum_analyze_audio

Offline · Read policy

Measure WAV/audio levels, activity, reliable pitch and harmonics, optionally compare.

No FL edits. Pitch may be unavailable for chords, noise and fast sweeps.
Measurements are not a musical quality or perceptual similarity score.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L83)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |
| `reference_path` | string or null | No | null |

## fl_serum_apply

Live / operation-dependent · Potential write policy

Apply named Serum controls in one transaction with settled readback and rollback.

Example changes: {"env1.decay_ms":300,"osc_a.fine_cents":-32,"voice.mono":true}.
Requires stopped playback. Validate every key before writing; dry_run defaults true.
Routing/polarity/FX topology come from a preset, not this live parameter adapter.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L34)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `changes` | object | Yes | — |
| `dry_run` | boolean | No | true |

## fl_serum_build_preset

Offline · Potential write policy

Build a native Serum 2.1.2 preset directly; no GUI/save dialog or FL mutation.

Recipes: kick, kick_long, 808, lead, pad. Discover settings with fl_serum_recipes.
Preserve modulation/FX topology from our verified templates. Never overwrites.
File validation does not mean the preset has been loaded or auditioned.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L46)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `recipe` | string | Yes | — |
| `output_path` | string | Yes | — |
| `settings` | object or null | No | null |
| `name` | string or null | No | null |

## fl_serum_edit_preset

Offline · Potential write policy

Edit mapped physical controls in a saved Serum preset, preserving other DSP fields.

Creates a new file, never overwrites. Useful when FL's live cache is stale.
Same settings as recipe builder; pitch keys require verified Env2-to-A pitch route.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L60)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `source_path` | string | Yes | — |
| `output_path` | string | Yes | — |
| `settings` | object | Yes | — |
| `name` | string or null | No | null |

## fl_serum_inspect

Live / operation-dependent · Potential write policy

Read 43 named Serum controls in compact groups; validate live parameter identities.

Uses the global channel index. Returns supported keys, units, ranges and displays.
No edits. Unknown FX slots and modulation destinations are not guessed.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L23)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |

## fl_serum_inspect_preset

Offline · Read policy

Read native Serum 2.1.2 preset envelopes, modulation, oscillator and FX settings.

Verifies the container/checksum/version. Absent fields mean defaults, not zero.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L73)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `path` | string | Yes | — |

## fl_serum_load_preset

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Direct live loading of .SerumPreset files has no
backend implementation. Use offline Serum preset tools, or fl_preset_load for
an existing FL .fst preset.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L6)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `path` | string | Yes | — |

## fl_serum_probe

Live / operation-dependent · Potential write policy

Record one temporary Serum note via WASAPI, send note-off, and measure the WAV.

Requires stopped transport and confirmed Serum channel. Never writes piano notes
or overwrites audio. Includes other system audio: keep other apps quiet.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L94)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `output_path` | string | Yes | — |
| `note` | integer | No | 32 |
| `velocity` | integer | No | 100 |
| `hold_seconds` | number | No | 0.6 |
| `reference_path` | string or null | No | null |

## fl_serum_recipes

Offline · Read policy

Discover kick/808/lead/pad recipes, editable physical settings and audition notes.

[Implementation](../../src/fl_studio_mcp/tools/serum.py#L16)

No input parameters.

## fl_session_inspect

Live / operation-dependent · Read policy

Observe controller, current project/revision, transport and installed dependencies.

Does not open Save As, move focus, save or modify the project. Ready means
ready for workflow-specific preflight, not permission or guaranteed success.
Dependency presence is separate from runtime verification.

[Implementation](../../src/fl_studio_mcp/tools/operator.py#L17)

No input parameters.

## fl_set_channel_color

Live / operation-dependent · Potential write policy

Set a Channel Rack label color from integer red/green/blue values in 0..255.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L139)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `red` | integer | Yes | — |
| `green` | integer | Yes | — |
| `blue` | integer | Yes | — |

## fl_set_channel_name

Live / operation-dependent · Potential write policy

Change the label of a global Channel Rack entry.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L129)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `name` | string | Yes | — |

## fl_set_channel_pan

Live / operation-dependent · Potential write policy

Set Channel Rack balance in -1..1, with 0 centered.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L99)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `pan` | number | Yes | — |

## fl_set_channel_volume

Live / operation-dependent · Potential write policy

Set normalized Channel Rack gain in 0..1.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L89)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `volume` | number | Yes | — |

## fl_set_grid_bit

Live / operation-dependent · Potential write policy

Enable or disable one zero-based step of the current pattern.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L169)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `position` | integer | Yes | — |
| `value` | boolean | Yes | — |

## fl_set_loop_mode

Live / operation-dependent · Potential write policy

Choose song or pattern playback without keyboard input.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L72)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `mode` | string | Yes | — |

## fl_set_playback_speed

Live / operation-dependent · Potential write policy

Set a finite playback multiplier between 0.25 and 4.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L82)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `speed` | number | Yes | — |

## fl_set_plugin_param_value

Live / operation-dependent · Potential write policy

Set one exposed control in 0..1 and read it back. Prefer calibrated named controls for
physical units.

Plugin locations use zero-based indices; use_global_index selects global Channel Rack
indexing.

[Implementation](../../src/fl_studio_mcp/tools/plugins.py#L101)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `param_index` | integer | Yes | — |
| `value` | number | Yes | — |
| `plugin_index` | integer | Yes | — |
| `slot_index` | integer | No | -1 |
| `use_global_index` | boolean | No | true |

## fl_set_song_position

Live / operation-dependent · Potential write policy

Seek using mode -1 (normalized), 0 (milliseconds), 1 (seconds) or 2 (ticks).

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L54)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `position` | number | Yes | — |
| `mode` | integer | No | 1 |

## fl_set_step_sequence

Live / operation-dependent · Potential write policy

Write boolean steps to the current pattern and report the affected span.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L189)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `pattern` | array of boolean | Yes | — |

## fl_set_stereo_separation

Live / operation-dependent · Potential write policy

Set mixer stereo separation in -1..1 using the native control.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L114)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `separation` | number | Yes | — |

## fl_set_track_color

Live / operation-dependent · Potential write policy

Assign an RGB mixer label color using integer components in 0..255.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L104)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `red` | integer | Yes | — |
| `green` | integer | Yes | — |
| `blue` | integer | Yes | — |

## fl_set_track_name

Live / operation-dependent · Potential write policy

Assign a mixer strip label and return the host result.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L94)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `name` | string | Yes | — |

## fl_set_track_pan

Live / operation-dependent · Potential write policy

Set mixer balance in -1..1 (left to right), with 0 centered.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L54)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `pan` | number | Yes | — |

## fl_set_track_volume

Live / operation-dependent · Potential write policy

Set normalized mixer gain in 0..1.25; unity is 0.8. Readback includes dB.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L44)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `volume` | number | Yes | — |

## fl_solo_channel

Live / operation-dependent · Potential write policy

Set Channel Rack solo, or toggle when solo is null.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L119)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `index` | integer | Yes | — |
| `solo` | boolean or null | No | null |

## fl_solo_track

Live / operation-dependent · Potential write policy

Set/toggle solo. Mode 1 includes sources, 2 sends, 3 both, and 4 only this track.

[Implementation](../../src/fl_studio_mcp/tools/mixer.py#L74)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `track` | integer | Yes | — |
| `solo` | boolean or null | No | null |
| `mode` | integer | No | 3 |

## fl_step_sequence_edit

Live / operation-dependent · Potential write policy

Replace exactly total_steps (1..1024) on a GLOBAL channel/selected pattern.

Short sequences are padded with false, clearing unused steps WITHIN the
explicit extent; steps beyond that extent are preserved. Inspect the same
target/extent first. Apply needs a fresh revision and a new absolute receipt.

[Implementation](../../src/fl_studio_mcp/tools/production.py#L79)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `pattern` | integer | Yes | — |
| `total_steps` | integer | Yes | — |
| `sequence` | array of boolean | Yes | — |
| `expected_revision` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_stop

Live / operation-dependent · Potential write policy

Stop playback and return the controller acknowledgement.

[Implementation](../../src/fl_studio_mcp/tools/transport.py#L26)

No input parameters.

## fl_trigger_note

Live / operation-dependent · Potential write policy

Send a live MIDI note (0..127); velocity 0 releases it. This does not write pattern
notes.

[Implementation](../../src/fl_studio_mcp/tools/channels.py#L74)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `channel` | integer | Yes | — |
| `note` | integer | Yes | — |
| `velocity` | integer | No | 100 |
| `midi_channel` | integer | No | -1 |

## fl_trigger_script

Live / operation-dependent · Potential write policy

Execute a prepared request, or refresh an empty queue; verify the matching response.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L103)

No input parameters.

## fl_ui_action

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Desktop input is unavailable. Use native backend
operations; fl_ui_inspect provides observation only.

[Implementation](../../src/fl_studio_mcp/tools/studio_ui.py#L38)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `window_handle` | string | Yes | — |
| `expected_revision` | string | Yes | — |
| `action` | object | Yes | — |
| `verification_plan` | string | Yes | — |
| `apply` | boolean | No | false |
| `receipt_file` | string or null | No | null |

## fl_ui_inspect

Live / operation-dependent · Read policy

Observe FL-owned windows and UIA controls; optional image for your native vision.

Read-only, including during playback. Defaults to the unique FL main HWND;
choose a child/plugin/dialog HWND from the returned windows. The 60-second
revision is specific to that HWND and is consumed by one apply. Screenshot
is mandatory before coordinate fallback actions. Those actions recapture and
require an identical image; animated windows may refuse. include_ocr uses
local Windows OCR on the same capture and returns bounded text/word boxes
in window-relative pixels. OCR alone returns no image and does not enable
coordinate actions or prove a semantic target. No model is called.

[Implementation](../../src/fl_studio_mcp/tools/studio_ui.py#L15)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `window_handle` | string or null | No | null |
| `include_screenshot` | boolean | No | false |
| `max_depth` | integer | No | 6 |
| `include_ocr` | boolean | No | false |

## fl_workflow_catalog

Offline · Read policy

Discover production coverage, backends, read-tool policy and verification limits.

Offline catalog, not evidence the live DAW/dependencies are ready. Unknown
tools default to writes; UI/file delivery alone never proves a musical result.

[Implementation](../../src/fl_studio_mcp/tools/operator.py#L8)

No input parameters.

## fl_write_pattern_bank

Live / operation-dependent · Potential write policy

Unavailable in backend-only SyncFL. Legacy script-based pattern-bank writing is
unavailable. Use native background piano tools or saved-FLP editing.

[Implementation](../../src/fl_studio_mcp/tools/piano_roll.py#L19)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `items` | array of object | Yes | — |
| `channel` | integer | Yes | — |
| `expected_channel` | string | Yes | — |
| `receipt_file` | string | Yes | — |
