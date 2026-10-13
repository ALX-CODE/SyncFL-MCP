# Roadmap

These are engineering priorities, not release-date promises. Proposals should include a concrete musical task and a way to verify it.

The [October 12, 2026 landscape review](COMPARISON-2026-10-12.md#what-to-improve-next) records additional source-backed opportunities in native authoring, plugin coverage, audio analysis and offline editing, with acceptance criteria. Those comparison findings are not implemented features.

| Priority | Work | Completion evidence |
|---|---|---|
| Background composition | Persistent notes without script-menu activation | Exact committed note/expression readback while foreground/input remain unchanged |
| Background loading | Reliable channel/effect import beyond the current experimental private interface | Correct destination, exact instance verification, bounded failure and no focus/input takeover |
| Plugin precision | Qualify Auto-Tune Harmony Player, EchoBoy Rhythm Time and further Soundtoys plugins | Independent settled holdouts, version/layout provenance and rejected unsupported values |
| Host cache behavior | Resolve isolated-write display inconsistencies | Repeated fresh-host tests with independent readback; no retry-until-pass behavior |
| Platform compatibility | Qualify additional Windows MIDI builds and macOS routes | Recorded setup/probe/live tests on each supported environment |
| Public onboarding | Simplify installation and publish synthetic demonstrations | New-user setup from a clean export without private media or machine paths |
| Musical workflows | Better reference analysis and reusable production recipes | Transparent measurements plus listening review; no guessed plugin identities/settings |

The disabled SDK note bridge remains research until its ABI is resolved. Experimental code is not promoted merely because it compiles. A new feature should reduce repeated manual work without weakening the existing identity, input-policy or source-preservation checks.
