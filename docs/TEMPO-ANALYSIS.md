# Rust tempo analysis

`fl_audio_bpm` estimates a WAV's tempo using SyncFL's original Rust core. It returns a decimal BPM estimate, ranked alternatives and half/double-time relationships. It runs offline and never sets FL Studio's tempo, stretches audio, loads a plugin or uses keyboard/mouse input.

## Call the tool

```json
{"path": "D:/Audio/example.wav", "min_bpm": 40, "max_bpm": 240, "top_n": 5}
```

The search range is explicit: `20 <= min_bpm < max_bpm <= 400`. `top_n` accepts 1–10. Defaults are 40–240 BPM and five candidates. Narrowing the range can disambiguate a known musical context; it does not create evidence for a tempo outside the recording.

| Result | Meaning |
|---|---|
| `status` | `ok`, `no_periodic_pulse` or `insufficient_audio` |
| `bpm` | Highest-ranked candidate, or `null` when evidence is insufficient |
| `candidates[].bpm` | Decimal BPM of an alternative pulse |
| `score` | Combined ranking strength in 0–1; not accuracy probability |
| `periodicity` | Strength of repetition in the onset envelope, 0–1 |
| `window_consistency` | Agreement across overlapping analysis windows, 0–1 |
| `confidence` | Relative softmax share among **returned candidates**, summing to one |
| `octave_relation` | `primary`, `half`, `double` or `other`, relative to the first candidate |
| `duration_seconds`, `analyzed_duration_seconds`, `truncated` | Source length and actual coverage |
| `method`, `schema_version` | Versioned analysis method and response contract |

The `confidence` field is not calibrated correctness. A single returned candidate necessarily receives a share of one, so inspect its periodicity, consistency and the audio. Identical pulses can be counted at multiple musical rates; the algorithm cannot infer your intended time signature or subdivision with certainty.

The key analyzer's confidence has a different denominator: [`fl_audio_key`](KEY-ANALYSIS.md) retains shares across all 24 keys, including omitted candidates.

## Runtime and method

The Windows x64 executable is bundled with the source distribution and Python wheel. You need no Rust compiler or additional Python audio packages to run it. The wrapper checks the executable's size and SHA-256 against its manifest, launches it without a console window, validates its response, and detects ordinary source-file changes during analysis. Missing or damaged artifacts fail explicitly; there is no automatic download, compile or alternate backend.

The Rust crate uses the standard library only and forbids unsafe Rust. It decodes samples in bounded blocks, builds a spectral-flux onset envelope, estimates repeating pulse intervals through normalized autocorrelation, refines peak positions below the envelope frame interval and checks consistency across overlapping windows. It preserves alternative tempi instead of forcing an integer or a preferred mainstream range.

Spectral flux measures positive changes in spectral magnitude. See Simon Dixon's [*Onset Detection Revisited*, DAFx 2006](https://www.dafx.de/paper-archive/2006/papers/p_133.pdf) for the onset-feature background. SyncFL's decoder, FFT, scoring, interpolation and CLI are original implementations; the paper does not establish this implementation's accuracy.

## Formats and limits

- Little-endian RIFF WAV: PCM 8/16/24/32-bit or IEEE float 32/64-bit, including supported extensible headers. Compressed, RF64 and big-endian WAV are unsupported.
- One to eight channels; 4–384 kHz sample rate. Channel magnitudes are pooled so antiphase stereo does not erase the pulse.
- At most the **first 180 seconds** are analyzed. Longer sources report `truncated: true`; a long intro or a later tempo change may make that coverage unrepresentative.
- Inputs shorter than three seconds return `insufficient_audio`. Silence and audio without sufficiently repetitive onset evidence can return `no_periodic_pulse`.
- This is a fixed-tempo estimator, not a tempo map, beat-position tracker, time-signature detector or audio-to-MIDI transcriber. Swing, rubato, sparse percussion, fills, polyrhythms and half-time arrangements can remain ambiguous.
- The Python wrapper bounds each process to 60 seconds. It never writes the WAV. No assertion of accuracy on arbitrary commercial mixes is made.

For a stem with sparse drums, compare a full mix and a drum stem when available. Audition the result against a metronome before applying a project tempo.

## Develop and verify

The crate and CLI contract are documented in [native/audio_tempo](../native/audio_tempo/README.md). From the repository root:

```powershell
cargo test --locked --release --manifest-path native/audio_tempo/Cargo.toml
cargo clippy --locked --manifest-path native/audio_tempo/Cargo.toml --all-targets -- -D warnings
python -m pytest tests/test_tempo_analysis.py -q
```

Tests use known-tempo synthesized audio, codecs, nonperiodic signals and malformed files. Independent Python-generated clicks exercise the compiled runtime and a fresh MCP process. Passing these establishes the covered cases, not universal tempo-detection accuracy.
