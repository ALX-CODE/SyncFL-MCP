# Project-authored Serum templates

`kick.SerumPreset`, `kick_long.SerumPreset` and `pad.SerumPreset` contain the project's own synth settings used by the native recipe builder. They are not copies of commercial sound-bank presets.

The decoded payloads reference stock Serum wavetable paths; those wavetable files and their audio data are not embedded or distributed here. A licensed compatible Serum installation and its stock content are still required. The project's original preset settings are provided under the repository's MIT license; this does not relicense Serum or its stock content.

Their exact SHA-256 values are pinned in `public-release.json`. A changed template must undergo a new provenance/content review before public export. The serializer supports arbitrary user presets at runtime, but the release exporter does not include arbitrary `.SerumPreset` files.
