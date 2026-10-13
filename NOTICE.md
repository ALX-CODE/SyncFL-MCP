# Notices and provenance

## Project source

SyncFL is developed and maintained by ALX-CODE and nobibasedd. Its original additions and modifications are distributed under the repository's MIT source license unless explicitly identified otherwise.

The former inherited application implementation has been replaced. The current connection layer, controller handlers, basic tool registration, server bootstrap, desktop trigger and client configuration implementation are maintained as SyncFL source. Compatibility identifiers and public API signatures remain where existing clients require them. The replacement scope, checks and limitations are documented in [source provenance](docs/SOURCE-PROVENANCE.md). Historical revisions retain the notices applicable to those revisions.

## Bundled native MIDI runtime

The Windows MIDI runtime includes Microsoft components from the pinned Windows MIDI Services SDK. Its [third-party notice](native/midi_bridge/THIRD-PARTY-NOTICES.md), [runtime copy](native/midi_bridge/runtime/THIRD-PARTY-NOTICES.md), version and hash manifest travel with the distribution. Do not remove them when packaging the helper.

Python dependencies retain their respective licenses. `pyproject.toml` and `uv.lock` identify the dependency set; the root MIT license does not relicense third-party packages.

## Bundled Rust tempo runtime

The tempo-analysis crate is original SyncFL source and has no third-party Cargo
dependencies. Its executable includes Rust's standard library. The distribution
retains the Rust 1.96.0 standard-library copyright inventory and license texts in
[the runtime notices](native/audio_tempo/runtime/THIRD-PARTY-NOTICES.md). Those
dependency notices are separate from the authorship of SyncFL's analysis code.

## Media, host software and trademarks

The public source export contains no song projects, commercial audio samples, artist-derived reference MIDI, commercial plugin binaries or plugin licenses. Private production material retained in the working repository is not covered by the source-code MIT license and is excluded from public exports.

Users must supply FL Studio, plugins and content they have rights to use. Product names identify compatibility targets. This independent project is not affiliated with or endorsed by Image-Line, Antares, FabFilter, Xfer Records or other plugin vendors.

The SyncFL logo was supplied by the project maintainers and is included unchanged. See the [branding asset note](docs/assets/README.md) for its provenance and integrity check.

Three project-authored Serum recipe templates are included as hash-pinned exceptions to the release media exclusion. They contain synth settings and relative references to installed stock wavetables, not embedded audio or commercial preset packs. See their [provenance note](src/fl_studio_mcp/data/serum/README.md).
