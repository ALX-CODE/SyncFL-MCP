# Serum: fast, measured sound design

Implemented against the installed Serum **2.1.2**, FL Studio **26.1.4**, native
preset format **10**. This is a reusable workflow, not a promise that starter
recipes automatically make a finished production.

## Choose the shortest path

| Task | Tool | What it verifies |
|---|---|---|
| Understand the current synth | `fl_serum_inspect(channel)` | 43 exact parameter identities, live displays and host-cache consistency |
| Adjust an envelope or tuning | `fl_serum_apply(channel, changes, dry_run=false)` | All inputs before writing; stopped transport; readback; restoration on failure |
| Start a new sound or configure routing/FX | `fl_serum_build_preset(recipe, output_path, settings)` | Native file checksum, version, lengths and complete payload round trip |
| Load a built sound | `fl_serum_load_preset(channel, path)` | Instrument identity, editor geometry, dialog completion and mapped live displays |
| Examine a saved sound | `fl_serum_inspect_preset(path)` | Oscillator, envelope, modulation and effect structures |
| Vary an existing saved sound | `fl_serum_edit_preset(source_path, output_path, settings)` | Preserves other DSP fields; new file only, using the builder's settings |
| Compare a reference and recording | `fl_serum_analyze_audio(path, reference_path)` | Levels, active duration, reliable pitch and harmonics; no invented similarity score |
| Hear and measure a change | `fl_serum_probe(channel, output_path, note, velocity, hold_seconds)` | Temporary MIDI note, confirmed note-off, captured WAV and measurements |
| Discover available recipes/units | `fl_serum_recipes()` | Recipe descriptions, limits, base notes and articulation |

All channel indices are **global** and zero-based. Discover them live. Do not
assume yesterday's Serum channel number still identifies today's instrument.

## Default procedure

1. Check channels and transport; read the user's current sound. Save valuable
   edits before loading another preset. Work on a new channel for experiments.
2. Choose a recipe and set physical values in one build. Save to a new filename.
   Building does not touch FL, and does not launch a Save As dialog.
3. Load once. Use the result's mapped-display verification rather than a tool's
   keystroke receipt. Unknown editor geometry stops the GUI workflow.
4. Probe one meaningful note or chord. Use G#2 / MIDI 32 for the kick recipes.
   Keep other desktop audio quiet: WASAPI records the default output device.
5. For variations, change several related settings in one build and load again.
   Compare at matched active levels. Pitch and FFT measurements guide changes;
   the user's listening feedback determines whether the sound is right.
6. Write complete audition phrases with the existing pattern-bank tool. Verify
   note duration as well as pitch; short notes can truncate the patch's tail.
7. Save the FLP once after the useful batch, using `fl_project_save`.

## Recipes and musical purpose

| Recipe | Starting structure | Base note / hold |
|---|---|---|
| `kick` | Short phase-locked sine, falling pitch, hard clip | MIDI 32 / 0.6 s |
| `kick_long` | Extended pitch sweep with 1.65-second bass tail | MIDI 32 / 1.85 s |
| `808` | Mono sine bass, brief pitch transient, 1.8-second decay, lighter clipping | MIDI 32 / 2 s |
| `lead` | Three detuned saw voices, fast attack, short release, plate reverb | MIDI 60 / 0.5 s |
| `pad` | Seven detuned saw voices, 350 ms attack, 1.8 s release, plate reverb | MIDI 48 / 2 s or chord |

These use our own captured templates and stock wavetables. No source kick audio
is embedded. The long kick uses a 951 ms pitch envelope;
the shorter kick uses a 270 ms pitch fall.

For kicks, distinguish the **amplitude tail** from the **pitch fall**: increasing
pitch decay does not stop the volume envelope from closing. Mono retriggering
also cuts an older voice when the next kick arrives. Space test notes far enough
apart to hear the complete tail before deciding how they should overlap in a beat.

For leads, coordinate note gaps, sustain, release and mono/legato rather than
adding many notes. For 808s, judge the fundamental separately from the distortion
harmonics and test note-off behavior. For pads, test chords and stereo width;
the monophonic pitch estimator is not reliable for these sounds.

## Recipe settings: physical values in the native preset

Discover exact limits with `fl_serum_recipes`. Typical example:

```json
{
  "amp_hold_ms": 160,
  "amp_decay_ms": 1500,
  "amp_release_ms": 350,
  "amp_decay_curve": 45,
  "pitch_decay_ms": 270,
  "pitch_amount_percent": 25,
  "distortion_drive_percent": 100,
  "fine_cents": -32,
  "master_percent": 40
}
```

Other keys include `mono`, `level_percent`, `unison_voices`, `detune`, and both
`amp_`/`pitch_` attack, hold, decay, sustain and release controls. Pitch settings
require a template with that route. Distortion drive requires an existing
distortion. Unknown keys, nonfinite numbers, invalid units/ranges and occupied
output paths are rejected. Master is capped at 70% for new generated recipes.
Percent refers to knob position, not a percentage of perceived loudness.

Live control keys use a different explicit namespace, e.g.:

```json
{"env1.decay_ms":1500,"env1.release_ms":350,"osc_a.fine_cents":-32}
```

Use inspection to discover these keys. Controls with uncalibrated transfer laws
are explicitly labeled normalized; don't invent a physical conversion.

## Critical FL cache trap

**After Serum loads its own native preset, FL can retain old normalized values.**
We observed `Main Vol` returning cached 25% while Serum's formatted value said
32%, and `Mono Toggle` cached Off while Serum reported On. Waiting did not fix it.

The inspector distinguishes host values from Serum displays. Live writes reject
unsafe cached baselines so rollback cannot restore an unrelated old sound.
When the cache is stale, use `fl_serum_edit_preset` on the saved patch (or build
from a recipe) and load the new file;
do not write old values back to the synth to make the cache look consistent.
Loader verification uses live displayed values with their rounding precision.

## Format, performance and boundaries

Native presets contain `XferJson` + a length-prefixed JSON header, then declared
raw length and compression ID 2, followed by Zstandard-compressed **CBOR**.
The header's MD5 covers the compressed payload; the build result also gives a
SHA-256 of the complete file. Unknown payload fields survive round trips.
Only the verified Serum 2.1.2/format-10 combination is accepted. Files and
decompression are bounded; trailing frames/objects and header/body mismatches
are rejected. A file round trip does not establish successful plugin loading.

Observed initial build times: about 15 ms cold, 2 ms warm per preset. Live named
kick-envelope change plus verification: 2.14 s. Full inspection: about 1.6 s.
Loading includes Windows-MCP startup and UI/native verification; it is slower
than building. Audio probes inherently take at least their note/tail duration.
Evidence lives in `audit-2026-09-07/serum/`.

The GUI loader targets the observed 1200x771 Serum editor and checks its actual
wrapper, instrument and menu placement. New sizes, detached windows, other
versions, or menus that would flip/clamp require inspection and recalibration.
FX/routing are verified in the preset file, not through native FL parameter
readback. Building arbitrary wavetable imports, MSEGs, macros, or any possible
FX graph is intentionally outside the current mapped surface.

The audio analyzer retains whole-file and active-region RMS separately. Silence
padding must not create a false level mismatch. Chords/noise/fast sweeps can
produce no reliable pitch estimate; silence and stereo phase cancellation are
handled explicitly. Capture tail is bounded and may truncate long pad reverb;
use a longer recording for complete decay evaluation.

## Immediate CLI and installation

Optional extras keep ordinary FL control independent of audio/codec packages:

```powershell
uv pip install --python .venv\Scripts\python.exe -e '.[serum,audio]'
.\.venv\Scripts\python.exe -m fl_studio_mcp.serum recipes
.\.venv\Scripts\python.exe -m fl_studio_mcp.serum build kick_long C:\path\LongKick.SerumPreset --settings C:\path\settings.json
.\.venv\Scripts\python.exe -m fl_studio_mcp.serum load 4 C:\path\LongKick.SerumPreset
.\.venv\Scripts\python.exe -m fl_studio_mcp.serum inspect 4
.\.venv\Scripts\python.exe -m fl_studio_mcp.serum analyze C:\path\capture.wav --reference C:\path\reference.wav
```

The same Python functions back MCP and CLI. Existing MCP processes need a
reconnect/restart to discover newly registered tools; the CLI works immediately.
No FL controller-script reload is required for this addition.
