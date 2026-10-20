# Changelog

## 2026-10-19 — session primitives, cheaper inspection, automation events

Adds ten tools for work the controller already advertised but no typed tool
exposed, and stops repeating the channel roster in every inspection.

- `fl_plugin_inspect` no longer returns the whole global channel roster. It was
  repeated on every call, which made the smallest useful inspection several
  kilobytes; `include_project=True` restores it and `fl_production_inspect` still
  returns it directly. The revision digest is unchanged, because it is still
  computed over the complete catalog.
- `fl_plugin_inspect` omits FL's numbered MIDI-CC pseudo-parameters unless
  `include_midi_cc=True`. They inflated `matched` and the reported parameter count
  (a 30-control plugin reported 4240); `target.parameter_count_exposed` now reports
  the real control count and `parameter_count_midi_cc` the stubs.
- `fl_automation_target_inspect` compacts its project block for the same reason.
  The resolver keeps the full roster internally, because saved-link retargeting
  compares the live roster against a saved project.
- Undo: `fl_undo` steps FL's own undo stack back once and `fl_undo_status` reads its
  counters and last action. `general.undo` is ordinary native access, not
  full-access only, so this works under the default policy.
- `fl_clear_dialog` closes FL's active popup menu through
  `ui.closeActivePopupMenu` with no desktop input, for recovering from a menu an
  earlier step left open. It reports the observed popup state before and after and
  needs `FL_MCP_ACCESS=full`.
- `fl_plugin_mix` reads or sets one effect slot's wet/dry level with readback, and
  `fl_track_slots` reads or sets a strip's whole effect rack. Both are the fastest
  way to A/B a chain without the plugin editor.
- `fl_event_inspect` reads a native automation event's full and short labels, its
  raw value and its smoothed value. `fl_automate_event` previews or applies one
  event write, refuses unresolved event ids, dispatches once, then reads the value
  back and receipts requested versus actual. Both document two different measured
  scales: event values are 30-bit fixed point (1.0 == 2**30, confirmed by the
  Insert 1 volume event reading 858993459 == 2**30 * 0.8) while
  `getAutoSmoothEventValue` reports 16384 == 1.0.
- `fl_rename_channel`, `fl_rename_pattern` and `fl_pattern_color` name and colour
  channels and patterns through the native setters with readback.
- `fl_plugin_inspect` and `fl_automation_target_inspect` are annotated read-only.
- Live-verified against FL 26.1.4.5589 in a real session: undo counters read back,
  the Insert 1 volume event resolves to `0x70401FC0 - "Insert 1 - Volume"` with a
  value of exactly 0.8, a slot wet level re-set to its existing value verified by
  readback, and the popup check reported `no_change` with no dialog open.
  Workflow revision 18 exposes 157 contracts.

### Test suite: 182 s → 28 s

Working on this repo meant three minutes of tests per run. Four separate causes,
all measured:

- **Parallelism.** Added `pytest-xdist` and a default of four workers
  (`--dist load`). Serial was 182 s; 4 workers 26 s; 8 workers 27 s; 16 workers
  31 s — hybrid-core contention makes high counts slower, so the count is pinned
  and documented rather than left at `auto`.
- **Import-time subprocesses.** `utils/build_info.py` ran `git status` twice and
  hashed a 2.5 MB JSON on import, in every process and every worker. HEAD is now
  read from `.git` files, and the working-tree query became an opt-in
  `build_metadata(deep=True)` behind `fl_build_info(deep=True)`. That query could
  also hang a server started on piped stdio — a helper process inheriting the
  handles — which is now detached, fsmonitor-free and killed on timeout.
- **FastMCP on the lock path.** `utils/tool_lease.py` imported FastMCP (~2.1 s)
  only to define the lease middleware, so every subprocess lease test paid it.
  The middleware moved to `utils/tool_lease_middleware.py` behind a lazy re-export;
  importing the lock primitive went from 2.4 s to 0.31 s.
- **Suite split.** Tests that spawn the server, run installer flows or hash the
  release are marked `integration` and excluded by default. CI runs `-m ""` so it
  still covers everything. Two lease tests also waited the 10 s default
  `FL_MCP_LEASE_WAIT_SECONDS`; they assert the refusal path, not the duration, so
  they now set it to 0.25 s.
- Two stale literals are gone: `fl_build_info` and `fl_workflow_catalog` each
  carried their own `workflow_revision` copy, and one test pinned 17 by hand. All
  three now read `WORKFLOW_REVISION`.

## 2026-10-13 — reliable production discovery and blank-project loading

- Advertise 32 core production APIs with complete schemas in every fresh session,
  alongside four discovery helpers. Sound/plugin loading, notes, arrangement,
  mixing and saving no longer require rediscovering hidden tool names.
- Correct the startup and build descriptions: render status is available;
  backend render start/cancel remains unimplemented. A failed operation must not
  be generalized into lack of all backend capabilities or silently replaced with
  an offline synthesized song.
- Load projects through FL's desktop drop target instead of its Playlist. Wait
  for panel layout and verify the exact native target before dispatching once.
- Force a guarded native save when initializing a named blank project: metadata
  changes can leave FL's changed flag clean. Older controllers are rejected before
  creating files. Verify the saved FL26 bytes, empty pattern and live identities.
- Live FL26 evidence: desktop load increased the project generation and changed
  the title; the updated controller saved a clean-flag project as FL 26.1.4.5589
  with one empty `Main` pattern. Full blank-project orchestration is also covered
  by regression tests; these checks do not establish musical quality.

## Unreleased — public presentation

- Made the controller round trip about 8x faster. The client checked the response file every 20 ms while FL answers a ping in about 2 ms; it now checks every 0.2 ms for the first 25 ms, then backs off to 1 ms and 5 ms. Measured on FL 2026 (API 45), 40 pings: median 22.8 ms before, 3.0 ms after. The cross-process bridge lock uses the same schedule.
- A tool that finds another FL tool running now waits up to 10 s for its turn (`FL_MCP_LEASE_WAIT_SECONDS`, 0–60) instead of returning `busy` at once. Nothing is dispatched while waiting. `fl_connection_status` no longer takes the tool lease.
- Added `FL_MCP_ACCESS=full`, an operator opt-in that lets `fl_native_call` and native batches call every catalogued FL API function: the 50 gated `ui.*`/editor/window functions, plus 11 functions that were previously left out of the controller catalog (`transport.globalTransport`, undo history, raw REC events, `channels.midiNoteOn`, logs). The 11 are catalogued as `access: "full"`; the controller refuses them unless the request envelope carries `access: "full"`, which the server sends only under this setting. The default stays `backend`. Desktop mouse/keyboard input remains unavailable in both. Reload the FL controller to pick up the new catalog.

- Added `fl_project_create` for clean unnamed Empty projects: create the named FLP from the installed template, load it through the backend, initialize Pattern 1, and native-save with file verification. Startup guidance and discovery direct assistants here before loading sounds, instead of requesting a manual first save. Existing or modified projects are preserved. Workflow revision 16 exposes 147 contracts. The new sequence has regression coverage; live end-to-end qualification remains pending.

- Added three experimental background piano tools for FL26: inspect pattern/sound/effect context, switch existing targets, and replace a score through native FSC import with saved-file verification and recovery copies. No Piano Roll script menu is required. Workflow revision 13 exposes 146 tools.
- Fixed read-only controller batches incorrectly calling `afterRoutingChanged`, which dirtied the project during inspection. Pattern selection now handles FL's dirty flag with a verified native save. Background loaders expose addressed FL panels/windows through APIs before rechecking occlusion.
- Removed interactive execution. SyncFL is backend-only; legacy mode settings cannot enable input and client registration removes them. Discovery omits unsupported desktop workflows.
- Routed WAV samples, FST instruments/effects/presets and saved FLP opening through the native bridge with target readback, one-shot delivery and receipts. These new wrappers are experimental and not yet live-qualified; FLP opening requires a named current project and a recovery copy. Workflow revision 15 retains 146 compatibility contracts.

- Added a contributor bootstrap (`Develop.cmd` / `scripts/setup_dev.ps1`) that
  clones or reuses the canonical Git checkout without resetting local work.
  Developer mode installs test tools and an editable `syncfl` CLI; offline setup
  skips FL deployment and assistant configuration by default.
- Added 36 exact-layout stock profiles covering 2,911 named controls, with 273 retained numeric/menu mappings after 3,644 independent live checks. Added per-plugin coverage, official manual references and explicit detection-only/normalized-only limits.
- Support measured stock-plugin normalized quantization for physical writes while preserving numeric display verification and strict explicit-normalized writes. Added literal degrees, cents and octave-unit parsing and clearer background loading obstruction diagnostics.

- Added a double-click Windows installer with Codex, Claude Desktop and Claude Code selection. Setup prepares Python 3.13 and locked runtime dependencies without development tools, verifies the pinned uv bootstrap, delegates guarded FL file deployment and preserves client settings with backups. The legacy laptop setup script now uses the same installation path.

- Added `fl_audio_bpm` with an original, dependency-free Rust tempo core, bundled Windows x64 runtime, hash verification and a bounded read-only Python wrapper. It reports decimal BPM, alternative pulses, half/double-time relationships and analysis coverage without changing FL tempo or sending desktop input. The server now exposes 143 tools and workflow revision 12.

- Added `fl_audio_key` and a standalone NumPy key analyzer: bounded WAV decoding, 4096/1024 Hann STFT, all 24 Krumhansl–Kessler major/minor profiles, ranked correlations and relative confidence. Supports PCM and IEEE float WAVs without desktop input or heavy audio dependencies; silence returns no candidates. Added a lightweight `key` extra, self-tests, decoding/STFT regression checks and a fresh-process MCP test. The server now exposes 142 tools and workflow revision 11.

- Qualified Auto-Tune Pro (24 controls) and Decapitator (12) and added EchoBoy, MicroShift and Little AlterBoy profiles: 14 profiles, 875 retained numeric/enum mappings after 7,059 live holdout checks. Mode-locked controls declare the selector setting they were qualified under. The display parser accepts `msec`, and `semitones`/`bpm` as non-converting units.
- Fixed background target checks to reject covering owned popups as well as unrelated windows.
- Background effect loading now reports a covered slot as not ready and rechecks exposure before dispatch. Earlier declined drops were traced to FL's Playlist covering the Mixer.
- Added a C++20 WinMM MIDI output DLL and CLI, a lazy hash-verified Python binding, exact endpoint selection and attempted-delivery error reporting. Windows output no longer uses RtMidi; MIDI-file support and non-Windows output remain intact.

- Added guarded background mixer EQ and strip-switch tools: inspect, preview, explicit apply, durable receipts and two native readbacks, without selecting a track or sending desktop input.
- Prevented calls through known-closed RtMidi handles and invalidated failed output connections without replaying uncertain commands; qualified recovery on the live bridge.
- Qualified 16 native change/restore cases and the new tools in a disposable FL Studio 2026 session; documented startup focus behavior separately from steady-state background operation.
- Updated the installation doctor to recognize the Python 3.12-3.14 CI matrix and the recommended 3.13 installer baseline.
- Independently replaced the inherited control layer, MIDI transactions, server bootstrap and client helpers; current source provenance and dependency notices are documented separately.
- Added strict native target/value checks, explicit failure reporting, RGB color ordering and regression coverage for controller loading, plugin pickup mode and input cleanup.
- Preserved public tool names and import entry points; Claude registration now previews changes and preserves configuration backups.
- Moved the recommended installer to Python 3.13, added an explicit Python-version option and a 3.12/3.13/3.14 CI matrix; documented the current 3.14 compiler and 3.15 wheel limits.
- SyncFL branding, SyncFL-MCP release naming, maintainer credits and a compatible `syncfl-mcp` entry point.
- New onboarding, capability matrix, workflows, architecture, configuration and troubleshooting guides.
- Generated reference for all registered MCP tools and their JSON schemas.
- Contribution/security guidance, issue templates and explicit attribution.
- Source-only export with public-file allowlisting, local-content checks and integrity manifest.
- Pinned background-runtime manifest bytes across Git checkout settings; bounded retry for transient Windows export locks.

## 0.2.0 — production and background-control development

The October 12, 2026 development snapshot adds the native Windows MIDI Services bridge, measured mix diagnosis, bounded metering, local MIDI-reference retrieval and note-transform proposals. Guarded production, routing, plugin, file, automation, recording and render workflows provide the broader production toolset.

The plugin/background upgrade adds 11 exact-layout named profiles, 802 retained numeric/enum mappings, default background policy and native current-project saving. The OLE loader remains experimental; the SDK note bridge remains disabled. Auto-Tune physical calibration is withheld.

See [validation](docs/VALIDATION.md) for dated evidence and [capabilities](docs/CAPABILITIES.md) for current boundaries. Version 0.2.0 is a development snapshot, not a blanket certification of every FL build or plugin combination.
