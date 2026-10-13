# Background control audit

**Current policy:** SyncFL is backend-only. The older mode comparisons below are historical audit evidence, not configuration instructions. WAV/FST loading and guarded FLP loading now route through the native bridge. Their new wrappers have unit coverage; they still need dedicated live qualification. Unmigrated desktop workflows are unavailable.

Source audit: 2026-10-12. This describes implemented routes, not a claim that every
advertised FL API or third-party plugin has been qualified live. Source review
was followed by explicitly scoped mutation tests in a disposable lab project.

`FL_MCP_INTERACTION_POLICY` defaults to `background`. Desktop input requires the
explicit value `interactive`; invalid values fail closed. Background mode does
not imply an invisible FL window or suppression of redraws caused by model edits.
It blocks desktop-input and focus-dependent calls through the audited routes.
This does not guarantee that launching FL or loading third-party code preserves
focus. During the October 12 controller-refresh lab, FL activated itself on
startup despite a no-activate launch flag. No further restart was used for the
subsequent native tests, which retained the foreground window.

## Capability matrix

| Capability | Current background route | Boundary / remaining requirement |
|---|---|---|
| Transport, tempo, channel settings, steps, mixer settings and routing | MIDI-triggered controller API; no synthetic keyboard/mouse | Actual controller capability and normal identity/revision checks still apply. |
| Built-in mixer EQ, effects enable, polarity and stereo swap | `fl_mixer_controls_inspect/set`; native APIs, no strip selection | Normalized writes, exact name/revision, stopped transport, durable receipt and two independent readbacks. Native Hz/dB are observations. |
| Plugin parameter inspection, guarded writes and named adapters | Native plugin API through `plugin_state` | Host values/displays are evidence; adapter layout/name binding cannot identify a different binary with an identical layout. |
| Native meters, recording arm state and recording start/stop | Native controller calls / recording worker | Recording destination must already be configured; setting its filename currently needs the desktop dialog. |
| MIDI reference search, transpose/quantize, saved-FLP inspection/edit/package, audio-file analysis | Offline files | Notes/FLP edits are proposals or new files; offline completion does not apply them to the live project. |
| Window metadata, UI snapshots, screenshots and OCR | `WindowsFL(read_only=True)` and hidden OCR subprocess | Observation only; a screenshot may be occluded and is not a background renderer. |
| Save the current named project | Typed `project.save_current` / `project_native` | Three disposable FL26 lab saves passed; qualification remains host-state-only. Host clean-state readback is not general absolute-path/disk-byte proof. |
| Persistent piano-roll note transactions | ComposeWithLLM script selection | Current activation opens/focuses the roll and selects its script; interactive mode required. Cached score reads and offline transforms stay available. |
| Add samples/instruments/effects or load `.fst`/Serum presets | Guarded Windows workflow and OLE drag | Interactive mode required; no automatic background fallback. |
| Project Open / Save As / exact-path discovery / GUI export / automation creation | Owned dialogs and guarded window controls | Interactive mode required. |
| Background effect loading | Experimental `fl_background_effect_inspect/load` with `native/background_bridge` | Only an empty slot on the already selected, visibly matching mixer track whose drop point is not covered by another window; new controller PID capability, exact revision, pinned binaries, durable receipt and all-track readback required. No track change or rollback. |

## Audited boundaries and fixes

- `WindowsFL.__aenter__` and `.win` gate interactive workflows and each Windows
  helper dispatch; read-only contexts only permit known observations.
- `fl_trigger` gates keyboard activation before platform-specific input/focus
  code. `piano_ready.prepare_piano` now checks before selecting a channel or
  opening a piano request session, avoiding partial preparation before rejection.
- `file_transfer.transfer_file` now checks before monitor inspection, compilation
  or launching its topmost WinForms OLE source. `FileTransfer.cs` also rejects
  standalone launch without explicit interactive mode, before creating a form.
- `MIDIConnection.send_command` now snapshots caller parameters and checks native
  UI commands before connection, before queue publication and before MIDI trigger.
  This covers `fl_native_call`, `fl_batch`, utilities and CLI callers sharing that
  transport. Nested batches have a maximum depth of 8 and 256 aggregate steps.
  Preview batches are inspected too. No blocked command is converted to keyboard
  or mouse input.
- `native_windows` only observes HWNDs, ownership, geometry and DPI. The OCR
  PowerShell subprocess has no visible console and processes an image. Audio
  analysis/recording helpers and the MIDI service do not synthesize desktop input.
  `scripts/inspect_picker.py` enters `WindowsFL`, so its picker is also gated.

### Native commands requiring interactive mode

All `ui.*` functions are gated **except** these explicit background functions:

- Observations: `getFocusedNodeCaption`, `getFocusedNodeFileType`,
  `isBrowserAutoHide`, `isInPopupMenu`, `isClosing`, `isMetronomeEnabled`,
  `isStartOnInputEnabled`, `isPrecountEnabled`, `isLoopRecEnabled`, `getSnapMode`,
  `getTimeDispMin`, `getHintMsg`, `getHintValue`, `getProgTitle`, `getVersion`,
  `getStepEditMode`, `getVisible`, `getFocused`, `getFocusedFormCaption`,
  `getFocusedFormID`, `getFocusedPluginName`.
- Non-focus preferences/passive annotations: `setBrowserAutoHide`,
  `crDisplayRect`, `miDisplayRect`, `miDisplayDockRect`, `snapMode`, `setSnapMode`,
  `snapOnOff`, `setTimeDispMin`, `setHintMsg`, `setStepEditMode`.

Also gated: `channels.showGraphEditor`, `channels.closeGraphEditor`,
`channels.showEditor`, `channels.focusEditor`, `channels.showCSForm`,
`mixer.focusEditor`, and raw `transport.globalTransport`. Direct command aliases
are checked as well as `native.call`. The typed native-save command remains
available because it fixes and guards its operation instead of accepting arbitrary
global command IDs. Non-focus model setters and audio transport remain available.

`transport.markerJumpJog`, `markerSelJog`, `rewind` and `fastForward` default to
focus-dependent routing in FL. Background mode accepts them only through
`native.call` with exactly two positional integers and `flags=8` (`GT_Global`).
Keyword flags and their unsupported direct aliases are rejected before dispatch.

This explicit list prevents a newly advertised `ui.*` method from becoming a
background focus bypass without review. It is not a sandbox against arbitrary
third-party code, modified controller scripts or direct external MIDI clients.

## SDK directions: research, not shipped capability

1. Use documented controller APIs for additional model operations when a precise,
   identity-bound operation can replace a dialog. The official API describes
   editor-opening, picker and current-focus commands; calling them natively does
   not make them background-safe. [Image-Line MIDI scripting reference](https://www.image-line.com/fl-studio-learning-content/fl-studio-online-manual/html/midi_scripting.htm)
2. Piano scripting offers score access once FL invokes the script. The current
   production trigger still needs its script menu; an independent background
   invocation path must be demonstrated before replacing that boundary.
   [Image-Line piano-roll scripting API](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/pianoroll_scripting_api.htm)
3. Investigate a native FL plugin bridge using the official SDK before relying on
   private host interfaces. The public SDK announcement describes native plugin
   creation, not a supported general-purpose FL host automation API. No unproven
   sample/plugin-loading callback is claimed here.
   [Image-Line FL Plugin SDK](https://forum.image-line.com/viewtopic.php?t=12092)
4. The background OLE implementation uses an undocumented target-interface property.
   Disposable FL tests loaded effects on the selected track without changing the
   foreground window. A cross-track test routed to the wrong track, so the registered
   route rejects track changes. Python preflight and readback cannot make FL routing
   atomic with manual edits. A later fresh-session empty-slot test was declined
   with no slot changes and unchanged foreground. Earlier successful loads do not
   establish reliable general loading. See its [limitations](../native/background_bridge/README.md).
5. The original [native SDK prototype](../native/fl_host_bridge/README.md) implements
   an idle-callback IPC path and passed fake-host tests. It has not been loaded in FL;
   conflicting vendor note-velocity definitions keep note writes disabled.

## Verification

Focused tests cover blocked native functions, all packaged native reads, safe
setters, nested/oversized/cyclic batches, a policy change while waiting, mutation
of caller-owned parameters, early piano rejection, and OLE launch rejection.
The C# helper was compiled locally; unset/background/invalid policies exited with
code 3 before a form, while interactive mode reached argument validation (code 2).
No desktop interaction or live FL mutation was needed.

### Live extension: October 12, 2026

The FL Studio 2026 lab passed 16 native change/readback/restore cases spanning
channel and mixer controls, steps, built-in EQ and mixer switches. A fresh stdio
server then exposed 141 tools and passed a six-setting EQ/switch mutation,
two later readbacks and exact state restoration through the new guarded tools.
The native sweep and the MCP qualification each retained their original
foreground window. Save As, native focus activation and wrong-track-name writes
were rejected before mutation. This is bounded lab evidence, not a guarantee
about every plugin, startup path or concurrent manual edit.

A closed RtMidi handle was also reproduced terminating the Python process.
Connection checks now reject known-closed handles before dispatch; send errors
invalidate the connection without replaying a potentially applied command. A new
read-only request successfully reopened the live endpoint. Offline regression
tests cover closed handles, errors and the no-replay boundary.
