# Changelog

## Unreleased — public presentation

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
