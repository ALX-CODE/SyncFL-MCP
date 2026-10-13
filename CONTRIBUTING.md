# Contributing

Contributions should make a concrete FL Studio task easier while preserving exact targets, source files and clear failure outcomes. Small, reviewable changes are easier to qualify than broad rewrites.

## Start here

Download the source ZIP and run **`Develop.cmd`**, or run it inside your existing
clone. It prepares a Git checkout, editable packages, test tools and the `syncfl`
CLI. The repository is public; no collaborator invitation is needed to clone it
or run the offline tests. [Developer setup](docs/DEVELOPER-SETUP.md) covers custom
directories and optional FL integration.

For a contribution, fork [SyncFL-MCP](https://github.com/ALX-CODE/SyncFL-MCP),
prepare your checkout with `Develop.cmd`, then follow the
[fork remote setup](docs/DEVELOPER-SETUP.md#submit-from-a-fork) to push a feature
branch and open a pull request against `main`. Maintainers with write access can
use a branch in this repository.

1. Read [architecture](docs/ARCHITECTURE.md) and [development](docs/DEVELOPMENT.md).
2. For a bug, describe the smallest reproducible case and the actual evidence. Use a disposable project, synthetic MIDI/audio and redacted paths.
3. For a feature, explain the musical task, current workaround, intended input mode and verification method before a large implementation.
4. Keep unrelated user changes and existing attribution intact.

## Pull requests

- Keep pull requests focused. All required CI checks must pass before merge;
  maintainers prefer squash merging so each change has one clear history entry.
- State what behavior changes and why. Distinguish offline tests, fake-host tests and live FL evidence.
- Run targeted tests and relevant lint; run the full offline suite when changing shared transport, policy, file parsing or packaging.
- Regenerate the tool reference after schema/description changes. Update the capability and workflow guides when behavior changes.
- Add regression coverage for meaningful behavior changes. Include failure/timeout/source-preservation paths, not just successful dispatch.
- Never remove revision, identity, receipt or background-policy checks simply to make a workflow pass.
- Do not bundle commercial audio, extracted melodies, plugin binaries, licenses, credentials or private project files. Use self-created minimal fixtures.

No FL license is needed to improve documentation or run the offline tests. Live qualification requires your own licensed host/plugins and separate evidence. The maintainers may merge an implementation as experimental while live qualification remains pending.

## Reports and support

Start with [troubleshooting](docs/TROUBLESHOOTING.md) for installation and
connection issues. Use [Discussions](https://github.com/ALX-CODE/SyncFL-MCP/discussions)
for setup questions, workflow help and demos. Use the issue templates for
reproducible bugs and concrete feature proposals.

Include OS, FL build, Python/server version and input mode. Say whether the
operation was only previewed, failed before dispatch, partially applied or
returned an unknown outcome. Attach minimal redacted evidence; do not paste your
entire MCP configuration or proprietary session.

Report security vulnerabilities through
[private security reporting](https://github.com/ALX-CODE/SyncFL-MCP/security/advisories/new),
as described in [SECURITY.md](SECURITY.md).
