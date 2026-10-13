# Practical workflows

The JSON examples below are **tool name + arguments**, not requests to execute now. Replace example targets and revision strings with fresh inspection results. Preview an operation before setting `apply: true`; some legacy tools mutate immediately and do not have a preview flag.

## Start every live session with identity

Call `fl_build_info`, `fl_connection_status`, `fl_session_inspect`, then `fl_workflow_catalog`. Confirm the loaded controller matches disk, the intended project is open, and playback/recording state is appropriate. Tool availability alone is not readiness.

Use `fl_project_snapshot` for an overview. Read the target's detailed inspector before changing it. Index `0` means the first global channel, but mixer track `0` is Master; never reuse yesterday's index from memory.

## Preview and change tempo

First inspect `fl_production_inspect` with `scope: "project"`. Use its returned revision:

```json
{
  "tool": "fl_project_settings",
  "arguments": {
    "changes": {"tempo_bpm": 140.0},
    "expected_revision": "REPLACE_WITH_FRESH_REVISION",
    "apply": false
  }
}
```

Read the preview, then explicitly apply the same intended change with a **new absolute** `receipt_file`, for example `D:\Music\Receipts\tempo-001.json`. The transport must be stopped. If the revision changed, inspect again instead of bypassing it. Two later readbacks check accepted state.

## Mix an exact group of tracks

Discover actual mixer names/indices. A scene expresses absolute targets, not “subtract 2” from an unknown fader:

```json
{
  "tool": "fl_mixer_scene",
  "arguments": {
    "items": [{"track": 10, "expected_name": "Lead bus", "volume_db": -3.0}],
    "apply": false
  }
}
```

For a relative instruction, inspect the current level and calculate the target first. `volume_db` is dB; `volume` is normalized. Apply with a new receipt, inspect the result, and audition it. Active automation can override a fader during playback.

Use `fl_routing_inspect` / `fl_routing_scene` for sends, buses and sidechains. A silent sidechain is routing information, not audible signal; inspect routing types and targets before applying.

## Adjust the built-in mixer EQ without selecting the strip

Call `fl_mixer_controls_inspect` with the target track. It returns all three EQ bands,
native dB/Hz observations, effects enable, reverse polarity, stereo swap and a revision.
Preview using that exact track name and revision:

```json
{
  "tool": "fl_mixer_controls_set",
  "arguments": {
    "track": 10,
    "expected_name": "Lead bus",
    "expected_revision": "REPLACE_WITH_FRESH_REVISION",
    "changes": {
      "bands": [{"band": 0, "gain_normalized": 0.5}],
      "reverse_polarity": false
    },
    "apply": false
  }
}
```

EQ input values are normalized `0..1`; do not pass dB or Hz into them. Bands are
zero-based. `frequency_normalized` and `bandwidth_normalized` are also available.
Native physical readbacks report the host's actual interpretation. Effects enable,
polarity and stereo swap take explicit booleans. Writes require stopped transport
and a new absolute receipt path. The receipt includes two later readbacks and
`restore_changes`; restoring is a separate, freshly inspected operation. The tool
does not select a mixer strip, activate a window, retry a write or restore automatically.

These controls use the documented [Image-Line mixer API](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm).

## Adjust a named plugin control

1. `fl_plugin_adapter_catalog` finds supported profiles and calibration evidence.
2. `fl_plugin_controls_inspect` binds the current instance, exact layout, names, condition state and revision.
3. Preview with `fl_plugin_controls_set`; read units, ranges and any unavailable conditions.
4. Apply once with a new receipt. Inspect both readbacks and the full receipt when a mismatch is reported.

Example for an already inspected Pro-Q 4 instance and an active band:

```json
{
  "tool": "fl_plugin_controls_set",
  "arguments": {
    "index": 10,
    "slot_index": 0,
    "expected_plugin": "Pro-Q 4",
    "expected_revision": "REPLACE_WITH_FRESH_REVISION",
    "changes": [{"control": "band_1_frequency", "value": 120.0}],
    "apply": false
  }
}
```

This example uses Hz only if that exact control is available and qualified in the inspection. Change a mode/selector separately, then inspect again before changing dependent controls. Auto-Tune Pro Retune Speed is qualified in milliseconds; its Harmony Player controls are normalized-only, so do not invent a conversion for them. See the [plugin guide](PLUGIN-CONTROL-GUIDE.md).

## Transform notes without touching FL

```json
{
  "tool": "fl_notes_transform",
  "arguments": {
    "notes": [{"midi": 60, "time_ticks": 0, "length_ticks": 96, "velocity": 0.8}],
    "ppq": 96,
    "transpose_semitones": 7
  }
}
```

This returns an unapplied proposal. It preserves supported expression and reports hashes. For basic persistent FL26 notes, use the background workflow below. The script-based workflow and `fl_write_pattern_bank` still require explicit interactive mode. A transient `fl_trigger_note` does not write a piano-roll note.

### Background piano and fast pattern/sound switching

Use a named, saved FL26 project with stopped transport. Supply its exact absolute
`.flp` path. `fl_background_piano_inspect` reads the current pattern number/name,
the addressed channel, generator name or sample source, saved generator-state
fingerprint, routed mixer track, and all ten effect slots (including wet levels).
The fingerprint covers saved generator blobs, not a recovered preset label.
Preset names remain explicitly unknown when FL does not expose them.

Pass that fresh revision to `fl_background_piano_select` to select an existing
pattern and explicit sound, open its Piano Roll, and select its routed mixer
track for subsequent effect operations. It verifies native selection
and the sound/effect context. FL marks pattern changes dirty, so changed-pattern
selection saves the project, retaining recovery copies and checking all notes,
pattern names, channels, project timing and Playlist clips. Same-pattern selection
does not perform this save.

`fl_background_piano_write` previews by default. With `apply=true` and a new
absolute JSON receipt path, it **replaces the entire addressed channel score in
the current pattern**. Supply 1–16,384 notes: MIDI key, beat time/duration or exact
integer ticks, normalized velocity/pan/release, color 0–15, and slide. Do not send
unsupported fields; they are rejected rather than discarded. Empty-score clearing
and advanced expression are not yet qualified.

The backend selects and exposes the exact Piano Roll through native APIs, imports
one FSC through the experimental OLE helper, saves, and independently parses the
specified FLP. Success requires exact requested notes, unchanged other scores,
and matching generator, mixer routing and effects. The result includes a fresh
`after` context usable for the next operation. Receipt siblings preserve the
before/after FLP and imported FSC. No arrow menu, ComposeWithLLM, keyboard or mouse
input is used. An uncertain callback is never automatically replayed.

## Arrange a saved project

Inspect with `fl_project_file_inspect` and `fl_playlist_file_inspect`. Use the source SHA and clip hashes with `fl_playlist_file_edit`. The editor writes a new FLP and preserves unrelated events. Current support includes full pattern clip placement and supported clone/move/delete operations, not a general stretch/trim engine.

The output is not automatically applied to the live project. To open it, use the separate guarded interactive `fl_project_open` flow with a new recovery path and receipt. Preserve the source FLP.

## Measure, render and compare

`fl_audio_inspect` gives sample-level metrics. `fl_audio_loudness` uses FFmpeg for integrated LUFS, loudness range and oversampled true peak. `fl_audio_compare` measures both files; it does not align songs or prove perceptual similarity. `fl_mix_diagnose` produces unapplied gain findings rather than inserting processing.

`fl_audio_key` ranks five global major/minor candidates from a WAV using NumPy alone. Ask for the candidates before deciding a tuning key: confidence is relative across 24 keys, and ambiguous or modulating music needs listening. The tool never changes Auto-Tune, MetaTune or the project. See [key analysis](KEY-ANALYSIS.md) for the standalone API and limitations.

`fl_audio_bpm` runs the bundled Rust analyzer on a WAV and returns BPM candidates, pulse periodicity and half/double-time relationships. It needs neither a live FL connection nor NumPy. Set `min_bpm` and `max_bpm` when you know the musical range; inspect alternatives before changing project tempo. See [tempo analysis](TEMPO-ANALYSIS.md).

Rendering starts through `fl_render_start` in interactive mode, with a fresh revision and new job directory. Poll `fl_render_status` until verified. A delivered Start command is not a completed render. Status verifies decoded files, format/duration, project identity and non-silent content. Recording has a separate start/status/stop flow and depends on your configured audio inputs.

## Save at a checkpoint

For a background save of the current named project, inspect `fl_project_native_inspect` and use `fl_project_save_native` with the exact title/revision and new receipt. Its successful result establishes host clean state, not a general disk-path assertion.

Use the interactive `fl_project_save` / `fl_project_save_as` workflows when exact paths and file evidence matter. Save once after a verified batch rather than after every knob.

## When something fails

Read `success`, error details, partial outcomes, readbacks and the receipt. A timeout after dispatch is an unknown outcome. Inspect current state before recovery; never blindly replay a write or assume failure means nothing changed.
