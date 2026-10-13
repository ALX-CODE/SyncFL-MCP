# Contributing

Contributions should make a concrete FL Studio task easier while preserving exact targets, source files and clear failure outcomes. Small, reviewable changes are easier to qualify than broad rewrites.

## Start here

1. Read [architecture](docs/ARCHITECTURE.md) and [development](docs/DEVELOPMENT.md).
2. For a bug, describe the smallest reproducible case and the actual evidence. Use a disposable project, synthetic MIDI/audio and redacted paths.
3. For a feature, explain the musical task, current workaround, intended input mode and verification method before a large implementation.
4. Keep unrelated user changes and existing attribution intact.

## Pull requests

- State what behavior changes and why. Distinguish offline tests, fake-host tests and live FL evidence.
- Run targeted tests and relevant lint; run the full offline suite when changing shared transport, policy, file parsing or packaging.
- Regenerate the tool reference after schema/description changes. Update the capability and workflow guides when behavior changes.
- Add regression coverage for meaningful behavior changes. Include failure/timeout/source-preservation paths, not just successful dispatch.
- Never remove revision, identity, receipt or background-policy checks simply to make a workflow pass.
- Do not bundle commercial audio, extracted melodies, plugin binaries, licenses, credentials or private project files. Use self-created minimal fixtures.

No FL license is needed to improve documentation or run the offline tests. Live qualification requires your own licensed host/plugins and separate evidence. The maintainers may merge an implementation as experimental while live qualification remains pending.

## Reports and support

Use the issue templates for bugs and feature proposals. Include OS, FL build, Python/server version and input mode. Say whether the operation was only previewed, failed before dispatch, partially applied or returned an unknown outcome. Attach minimal redacted evidence; do not paste your entire MCP configuration or proprietary session.

Security issues belong in a private report, as described in [SECURITY.md](SECURITY.md).
