# Musical key analysis

`fl_audio_key` estimates the global major/minor key of a WAV and returns five ranked candidates by default. It runs offline, needs only NumPy, and does not touch FL Studio, the keyboard, the mouse or any tuning plugin. The same implementation is a standalone [Python module](../src/fl_studio_mcp/utils/key_analysis.py).

## Use through MCP

Ask: “Analyze this WAV and show its five strongest key candidates, correlations and relative confidences.” The tool arguments are:

```json
{"path": "D:/Audio/example.wav", "top_n": 5, "temperature": 0.15}
```

The response contains `candidates`, `analysis: "global_major_minor"`, `tuning_reference_hz: 440`, `confidence_basis` and `no_tonal_evidence`. Each candidate has:

| Field | Meaning |
|---|---|
| `key` | Readable key, such as `C major`, `A minor` or `C#/Db minor` |
| `pitch_class` | Tonic as an integer: C=0, C#/Db=1, …, B=11 |
| `mode` | `major` or `minor` |
| `correlation` | Pearson correlation with the rotated profile, from −1 to 1 |
| `confidence` | Softmax share across **all 24** keys; not a calibrated probability of correctness |

`top_n` accepts 1–24. The five returned confidences normally sum to less than one because the omitted candidates retain their shares. `temperature` must be finite and positive; smaller values sharpen the shares without changing the correlation ranking. Silence or flat chroma returns `candidates: []` and `no_tonal_evidence: true`.

Reconnect the MCP client after upgrading so it discovers the new tool. `fl_build_info` reports workflow revision 12; `fl_workflow_catalog` includes the offline `key_analysis` workflow.

## Use from Python

For an existing SyncFL checkout, the lightweight optional dependency group is:

```powershell
uv sync --locked --python 3.13 --extra key
```

The full `audio` extra already includes NumPy. Keep your existing extras when synchronizing an environment used for other workflows.

```python
from fl_studio_mcp.utils.key_analysis import detect_key

candidates = detect_key("D:/Audio/example.wav")
for candidate in candidates:
    print(candidate)
```

Arrays must contain real floating-point samples, shaped `(frames,)` or `(frames, channels)`, with an explicit integer sample rate:

```python
import numpy as np
from fl_studio_mcp.utils.key_analysis import detect_key

sample_rate = 44100
samples = np.zeros((sample_rate, 2), dtype=np.float32)
candidates = detect_key(samples, sample_rate, top_n=5)
assert candidates == []  # This example is silence.
```

To use it outside SyncFL, copy `key_analysis.py`, install NumPy, and import `detect_key` from that file. It has no package-relative imports and makes no use of SciPy, librosa, soundfile, FFmpeg or an online service. Existing dependency notices still apply to NumPy; the module's original source is covered by the repository's [MIT license](../LICENSE).

## Method

1. Read audio in bounded blocks. Pool each channel's magnitude spectrum, avoiding cancellation from opposite-phase stereo channels.
2. Remove each frame's DC component; apply a 4096-sample Hann window with a 1024-sample hop. Include one zero-padded final frame when needed.
3. Keep bins from 55 to 5000 Hz. Map each bin to the nearest equal-tempered pitch class with A4=440 Hz, accumulate magnitudes, and normalize the resulting 12-bin chroma.
4. Pearson-correlate chroma against every rotation of the major and minor Krumhansl–Kessler profiles. Sort the 24 candidates by correlation.
5. Apply numerically stable softmax across the 24 correlations using the requested temperature, then return `top_n` candidates.

The profile values come from Krumhansl and Kessler's 1982 study, [*Tracing the dynamic changes in perceived tonal organization in a spatial representation of musical keys*](https://doi.org/10.1037/0033-295X.89.4.334). The method is discussed in Carol L. Krumhansl's 1990 book, [*Cognitive Foundations of Musical Pitch*](https://academic.oup.com/book/40395), chapter 4. The implementation cites both sources in comments.

## Inputs and limits

- PCM WAV: 8-, 16-, 24- or 32-bit integer samples, loaded with Python's `wave` module. IEEE float WAV: 32- or 64-bit samples, read by a bounded RIFF parser, including the float extensible format.
- Little-endian RIFF only. Compressed WAV, RF64 and big-endian RIFX are unsupported. Export a supported WAV first.
- One to eight channels; nonempty audio; at most two hours. Processing time grows with duration. File processing uses blocks rather than loading the complete recording. Array callers already hold the source in memory.
- Invalid layout, truncated sample data and nonfinite samples are rejected. File metadata is checked before and after analysis to detect ordinary concurrent changes. Source files and input arrays are not modified.
- One global key is estimated for the whole input. Relative major/minor pairs, modulations, modes, sparse excerpts, prominent drums and detuned recordings can confuse it. This is chroma-profile matching, not note transcription, a tuning detector or a trained key classifier.
- No score threshold proves a key is correct. Compare alternatives and listen before configuring Auto-Tune or MetaTune. The tool makes no plugin changes.

## Verification

```powershell
python -m fl_studio_mcp.utils.key_analysis
python -m pytest tests/test_key_analysis.py tests/test_audio_stdio.py -q
```

The module's self-test synthesizes C-major and A-minor progressions and requires each expected key to rank first. Regression tests cover all 24 profile rotations, multiple sample rates, independent STFT calculations, mono/antiphase stereo, PCM/float decoding, malformed input, silence, scaling and the standalone dependency boundary. A fresh stdio MCP process also analyzes a generated WAV without connecting to FL Studio.

These are algorithm and integration checks on generated material, not a measured accuracy benchmark on commercial mixes.
