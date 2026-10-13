# Capabilities and limits

**143 tools, workflow revision 12.** Registration is not proof that your host API, plugin or dependency is available. Inspect `fl_workflow_catalog` and `fl_session_inspect` in the running server.

**Background** means no synthetic keyboard/mouse or activation. **Offline** operates on files without applying them to the live project. **Interactive** needs explicit opt-in and inspected Windows controls. **Experimental** has narrower evidence; inspect failures rather than replaying them.

| Area | Representative tools | Route | Main boundary |
|---|---|---|---|
| Connection/discovery | `fl_build_info`, `fl_connection_status`, `fl_capabilities`, `fl_session_inspect` | Background/offline | Open MIDI alone does not prove a responding controller |
| Transport | Transport tools, `fl_get_transport_status` | Supported native actions background | Triggering notes does not persist composition |
| Project/channel settings | `fl_production_inspect`, `fl_project_settings`, `fl_channel_configure` | Background | Fresh revisions; PPQ/meter edits not enabled |
| Step sequencing | `fl_step_sequence_edit` | Background | Selected one-based pattern, global channel, explicit extent |
| Persistent notes | `fl_piano_roll_prepare`, `fl_write_pattern_bank`, piano tools | Interactive on Windows | Script activation needs UI; one channel per pattern-bank workflow |
| Note transforms | `fl_notes_transform` | Offline | Unapplied transpose/quantize proposal with supported expression |
| MIDI references | `fl_midi_reference_search` | Offline | Your local files; bounded descriptive scores, not learned quality |
| Mixer balance | `fl_mixer_scene`, `fl_mixer_meter_watch` | Background | Native sampled peaks are not continuous coverage, RMS, LUFS or true peak |
| Built-in mixer EQ/switches | `fl_mixer_controls_inspect`, `fl_mixer_controls_set` | Background | Normalized EQ writes with native dB/Hz readback; effects enable, polarity and stereo swap. Stopped transport, exact revision/name and new receipt required for writes |
| Sends/buses/sidechains | `fl_routing_inspect`, `fl_routing_scene` | Background | Exact current routing and later readback |
| Generic plugins | `fl_plugin_inspect`, `fl_plugin_apply`, parameter tools | Background where exposed | FL-exposed controls only; host caches may be stale |
| Named plugins | `fl_plugin_adapter_catalog`, `fl_plugin_controls_inspect`, `fl_plugin_controls_set` | Background | Exact layout; qualified units/enums, explicit normalized fallback |
| Assets and loading | `fl_find_assets`, `fl_sample_add`, `fl_instrument_add`, `fl_effect_add`, `fl_preset_load` | Discovery offline; loading interactive | Exact names/paths; plugin FSTs, not complete mixer-track FSTs |
| Selected-track effect load | `fl_background_effect_inspect`, `fl_background_effect_load` | Experimental background | Empty slot on the selected track whose drop point is visibly exposed; earlier declines were traced to FL's Playlist covering the Mixer |
| Serum | `fl_serum_inspect`, `fl_serum_apply`, preset build/edit tools | Background/offline; preset load interactive | Serum 2-specific mappings/templates |
| Serum probing | `fl_serum_probe`, `fl_serum_analyze_audio` | Live note/capture or offline | Default-output capture can include other desktop audio; pitch analysis has limits |
| Saved FLP inspection | `fl_project_file_inspect`, `fl_playlist_file_inspect` | Offline | Supported saved layouts; unsaved state may differ |
| Saved arrangements | `fl_playlist_file_edit` | Offline, new FLP | Full pattern clip addition and supported clone/move/delete; no general trim/stretch |
| Automation | Target/link inspection, `fl_automation_file_edit`, `fl_automation_file_retarget` | Background/offline | Exact links and normalized curves; opening output is separate |
| New automation | `fl_automation_create_channel` | Interactive | Tested channel volume/pan creation; layout-dependent |
| Current-project save | `fl_project_native_inspect`, `fl_project_save_native` | Background | Host clean-state proof; no general exact-disk-path guarantee |
| Open/exact-path save | `fl_project_open`, `fl_project_save`, `fl_project_save_as` | Interactive | Recovery copies, supported dialogs and exact paths |
| Render jobs | `fl_render_start`, `fl_render_status`, `fl_render_cancel` | Start/cancel interactive; status observation | WAV master/stems; tested FL26/96 DPI; rate follows FL settings |
| Recording | Inspection/arm/start/status/stop tools | Mixed; file configuration interactive | Does not calibrate audio input/latency automatically |
| Audio | `fl_audio_inspect`, `fl_audio_edit`, `fl_audio_loudness`, `fl_audio_compare`, `fl_mix_diagnose` | Offline | New WAV outputs; no general time stretch; loudness requires FFmpeg |
| Musical key | `fl_audio_key` | Offline | NumPy only; PCM/float WAVs; global major/minor candidates and relative confidence; no automatic tuning changes |
| Musical tempo | `fl_audio_bpm` | Offline | Bundled Windows x64 Rust core; first 180 seconds of PCM/float WAV; fixed BPM candidates with half/double ambiguity, no project changes |
| Packaging | `fl_project_package` | Offline | Referenced samples/notes/MIDI; opaque plugin resources need inspection |
| UI | `fl_ui_inspect`, `fl_ui_action` | Observation/interactive | FL-owned windows; input delivery is not outcome proof |
| Native/batch | `fl_native_call`, `fl_batch`, native catalog | Operation-dependent | Capability listing is not individual qualification; partial application is possible |

## Indices, units and state

- Global channels and mixer tracks are zero-based; Master is track `0`. Channel Rack filtering must not change target identity.
- Effect slots `0..9` correspond to displayed slots `1..10`. In generic plugin APIs, `slot_index=-1` selects a generator/channel target.
- Patterns and Playlist tracks may be one-based. Follow each tool's schema.
- Use actual PPQ and integer ticks. Do not assume 96 PPQ or 4/4.
- Normalized gain and dB targets differ. Use documented units and qualified conversions.
- Inspect the same target immediately before preview/apply. Do not remove a revision guard to bypass stale state.

## Unfinished areas

Full background piano editing, general loading and rendering are not implemented. The native SDK note-write prototype is disabled. Some plugin controls remain normalized-only, including Auto-Tune's Harmony Player and EchoBoy Rhythm Time, and other Soundtoys plugins have no profile. There is no universally qualified perfect vocal chain, automatic mastering, complete transcription or perceptual reference matching.

Technical verification and musical acceptance differ. Listen in context and retain source projects. See [validation](VALIDATION.md) and [roadmap](ROADMAP.md).
