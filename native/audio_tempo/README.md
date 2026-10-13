# SyncFL tempo engine

A small, original Rust library and command-line tool for offline WAV tempo
estimation. It has **zero third-party Rust crates**, uses no model downloads,
never starts FL Studio, and never sends keyboard or mouse input.

```powershell
cargo run --release --locked --manifest-path native/audio_tempo/Cargo.toml -- `
  analyze --input "drum-loop.wav" --min-bpm 40 --max-bpm 240 --top-n 5
```

For a release build with local source/toolchain paths remapped:

```powershell
./native/audio_tempo/build_runtime.ps1
```

The script builds into `build/audio_tempo` by default; `-TargetDirectory` selects
another directory. It does not install, bundle, or overwrite a runtime. The
release profile enables LTO, strips symbols, and disables unwind handling. The
MCP distribution handles runtime packaging separately.

## Input and bounds

- Little-endian RIFF WAVE: integer PCM 8/16/24/32-bit or IEEE float 32/64-bit,
  including the corresponding `WAVE_FORMAT_EXTENSIBLE` subformats.
- 1–8 channels, 4,000–384,000 Hz. Float samples must be finite with magnitude at
  most `1e100`. RF64, RIFX, compressed WAV and multiple `data` chunks are rejected.
- The first **180 seconds** are analyzed. Longer files report their full duration,
  `truncated: true`, and the analyzed duration. Later changes of tempo may therefore
  be outside the analyzed prefix.
- Audio decoding uses blocks of at most 4,096 sample frames. Only short FFT
  buffers and the low-rate onset envelope are retained; the full WAV is never
  loaded into memory. RIFF traversal is capped at 65,536 chunks.
- `20 <= min_bpm < max_bpm <= 400`; `top_n` is an integer from 1 to 10. Defaults
  are 40, 240 and 5. At least 3 seconds and 3 candidate pulse periods are needed.

## Method and meaning

The implementation applies bounded box decimation for high sample rates,
Hann-windowed magnitude FFTs, per-channel magnitude pooling, and positive
spectral differences. Local baseline subtraction produces an onset envelope.
Positive onsets are compressed to balance differing drum strengths.
Normalized autocorrelation, fractional peak interpolation across several pulse
separations, and overlapping-window consistency provide the ranked estimates.
Subdivisions help distinguish a repeated pulse from every second or third pulse;
there is no prior favoring 120 BPM or any genre.

Spectral flux is an established onset feature; see Simon Dixon,
[“Onset Detection Revisited,” DAFx 2006](https://www.dafx.de/paper-archive/2006/papers/p_133.pdf).
The code and scoring rules here are independently written. WAV subformats follow
[Microsoft's WAVEFORMATEXTENSIBLE specification](https://learn.microsoft.com/windows-hardware/drivers/ddi/ksmedia/ns-ksmedia-waveformatextensible).

This is **global tempo estimation**, not beat tracking, downbeat detection or a
time-signature classifier. A 70 BPM groove and its 140 BPM interpretation can
both be defensible. Sparse percussion, syncopation, tempo changes, unusual
meters, heavily swung passages and music without a steady pulse are harder.
Weak subdivisions can leave several metrical levels competitive, including
quarter- and half-tempo aliases among the returned alternatives.
Synthetic checks establish defined signal behavior, not accuracy on an unseen
music benchmark. Narrow the requested BPM range when the intended metrical
level is already known.

`confidence` is a softmax weight over the **returned candidates**, summing to
one. It is not a calibrated probability of being correct, and a single candidate
necessarily gets 1.0. Inspect `periodicity` and `window_consistency` too; all
three quality fields and `score` lie in `[0, 1]`. Silence, DC, stationary tones,
noise without a clear periodic pulse, and very short inputs can return no
candidates. No input automatically changes a project's tempo.

## Stable interface

```rust
use syncfl_tempo::{analyze, Options};

let result = analyze("drum-loop.wav", Options::default())?;
println!("{}", result.to_json());
# Ok::<(), syncfl_tempo::Error>(())
```

`syncfl-tempo --version` prints `syncfl-tempo 0.1.0`. Successful analysis prints
one JSON object to stdout and exits 0. Expected errors print a JSON error object
to stderr and exit 2. Neither result includes the input path.

The analysis object contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `status` | `ok`, `no_periodic_pulse`, or `insufficient_audio` |
| `bpm` | First candidate BPM, or `null` |
| `candidates` | Ranked objects containing `bpm`, `score`, `confidence`, `periodicity`, `window_consistency`, `octave_relation` |
| `octave_relation` | `primary`, `half`, `double`, or `other`, relative to the first result |
| `duration_seconds` | Duration declared by validated WAV sample data |
| `analyzed_duration_seconds` | Duration of the prefix actually analyzed |
| `truncated` | Whether the 180-second analysis limit was reached |
| `sample_rate`, `channels` | Source WAV format |
| `method` | `spectral_flux_autocorrelation_v1` |
| `confidence_basis` | `relative_candidate_softmax_not_calibrated_probability` |
| `max_analysis_seconds` | `180` |

An error has `schema_version: 1` and
`error: {"code": "...", "message": "..."}`. Error codes are `invalid_arguments`,
`invalid_options`, `invalid_wav`, `unsupported_wav`, `invalid_samples`, `io_error`,
and `input_changed`.

## Verification

```powershell
cargo fmt --manifest-path native/audio_tempo/Cargo.toml -- --check
cargo clippy --manifest-path native/audio_tempo/Cargo.toml --all-targets --locked -- -D warnings
cargo test --manifest-path native/audio_tempo/Cargo.toml --release --locked
```

Tests independently synthesize pulse trains, including fractional BPM,
syncopation, polarity-inverted stereo, codec variants, silence, DC, sustained
tones, noise and malformed files. The FFT is also checked against a direct DFT.
