# Changelog

## Unreleased — public presentation

- Added `fl_audio_bpm` with an original, dependency-free Rust tempo core, bundled Windows x64 runtime, hash verification and a bounded read-only Python wrapper. It reports decimal BPM, alternative pulses, half/double-time relationships and analysis coverage without changing FL tempo or sending desktop input. The server now exposes 143 tools and workflow revision 12.

- Added `fl_audio_key` and a standalone NumPy key analyzer: bounded WAV decoding, 4096/1024 Hann STFT, all 24 Krumhansl–Kessler major/minor profiles, ranked correlations and relative confidence. Supports PCM and IEEE float WAVs without desktop input or heavy audio dependencies; silence returns no candidates. Added a lightweight `key` extra, self-tests, decoding/STFT regression checks and a fresh-process MCP test. The server now exposes 142 tools and workflow revision 11.

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
