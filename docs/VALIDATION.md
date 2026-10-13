# Validation and evidence

Different checks answer different questions. A passing unit test is useful, but it does not prove a particular plugin sounds correct or that another machine's FL UI matches the calibrated layout.

## Rust tempo analysis: October 12, 2026

The original Rust core passed **21 tests**, formatting and strict Clippy checks.
The integrated checkout passed **2,451 Python tests with 2 skips** on Python
3.13.13, including the optional CLI suite.
The Windows x64 executable was built with remapped source paths; the source export
passed credential/personal-path scans, including binary strings. Its size and
SHA-256 are pinned alongside preserved Rust standard-library notices. Windows
checkout tests verify all bundled runtime files under both line-ending settings.

An independently installed wheel exposed **143 tools** at workflow revision
**12**. A fresh MCP process analyzed a generated 128 BPM WAV as **128.003 BPM**
with NumPy, SciPy, soundfile, librosa and torch absent. The source remained
byte-for-byte unchanged. No FL connection or keyboard/mouse input was used.

Independent generated drum-and-bass fixtures measured 96 as **95.983**, 123.45
as **123.411**, and 174 as **173.965 BPM**. A half-time 140 BPM arrangement
ranked **69.985** first and **139.971** second. That is a metrical ambiguity,
not a uniquely recovered project tempo. Codec, malformed-input, silence,
stationary-tone and nonperiodic-noise checks are also covered. These are
synthetic-signal checks, not an unseen commercial-music benchmark. See
[tempo analysis](TEMPO-ANALYSIS.md).

## Offline key analysis: October 12, 2026

The key-analysis update passed **2,419 Python tests with 2 skips** on Python 3.13.13,
including the optional CLI suite. A fresh stdio process registered **142 tools** at
workflow revision **11** and analyzed a generated C-major WAV. An independently
installed wheel repeated that MCP call with only the `key` extra: SciPy,
soundfile, librosa and torch were absent.

The standalone self-test ranked C major and A minor first, with correlations
approximately 0.947 and 0.891. Regression tests covered all 24 reference-profile
rotations, PCM/float WAV decoding, independent STFT calculations, antiphase stereo,
invalid inputs and source preservation. These are generated-signal checks, not
a real-music accuracy benchmark. See [key analysis](KEY-ANALYSIS.md).

## Native C++ MIDI output: October 12, 2026

A clean Windows x64 Release build passed all 43 native unit cases and all 11
CTest checks. PE inspection confirmed CFG, CET, ASLR, DEP, filename-only PDB
references and the intended DLL search flags. The transport DLL imports only
Windows WinMM and Kernel32. The packaged DLL/CLI hashes match the runtime manifest.

The integrated checkout passed **2,340 Python tests with 2 skips** on Python
3.13.13, including the optional CLI suite. Binding and connection tests cover
runtime integrity, explicit ABI widths, failed-send delivery state, concurrent
first connections and refusing a missing native runtime without fallback.

The integrated native Python connection completed 12 correlated FL handshakes,
then disconnected and reconnected successfully. The observed project context,
transport and tempo stayed unchanged. A separately installed wheel also completed
a live handshake while Python imports of both `mido` and `rtmidi` were blocked.
The live installation doctor passed all checks. No FL restart or keyboard/mouse
input was used. These tests establish the tested transport path; they do not
establish a speed improvement, plugin qualification or driver independence.

Windows MIDI Services still supplies the virtual endpoint. The C++ component
replaces the output client library. Mido remains for MIDI files, and non-Windows
output retains Mido/RtMidi. See the [native transport](../native/midi_transport/README.md).

## Live background-control pass: October 12, 2026

The updated source passed **2,317 Python tests with 2 skips** on Python 3.13.13,
including the optional CLI suite. A fresh local stdio server exposed **141 tools**
at workflow revision **10** and connected to FL Studio 2026 through the managed
Windows MIDI Services bridge. All six deployed integration files matched source;
the running controller's source digest also matched.

In a disposable project, 16 native change/readback/restore cases passed across
channel volume/pan/name/color, mixer name/color/volume/pan/stereo separation,
step state, built-in EQ gain/frequency/bandwidth, effects enable, polarity and
stereo swap. A separate fresh-server MCP test exercised the two new guarded
mixer tools: preview, six-setting apply, two later native readbacks and exact
state restoration. Bounded metering also completed. Native focus activation,
Save As and a write with the wrong target name were rejected.

Each of these steady-state mutation passes retained its original foreground
window. The earlier controller-refresh launch activated FL despite a
no-activate startup flag; this is recorded as a limitation, not a successful
background launch. No further restart was used. See the
[background-control audit](BACKGROUND-CONTROL-AUDIT.md).

A closed RtMidi output was reproduced terminating the client process. After
the fix, a new read-only request reopened the endpoint and received the live
controller response. Offline tests also cover failed-send cleanup, closed
handles and the rule against automatically replaying uncertain writes.

These results establish the tested host state transitions and connection
recovery. They do not establish audible DSP behavior or qualify the commercial
plugin profiles: this lab had no loaded effect plugins. Built-in EQ writes use
normalized values; Hz/dB are native readbacks, not a newly calibrated input API.

## Public source preparation: October 12, 2026

An isolated source export, without private project/reference directories or Git history, passed **2,244 Python tests with 2 skips** on Python 3.13.13, including the optional CLI suite and runtime-manifest checkout cases. The release suite has 19 cases covering private-file exclusion, exact file hashes, credentials/personal paths, changed preset rejection, Windows file-lock recovery and checkout settings. All 139 tool definitions and 6 configuration/API examples were also checked. The maintainer-supplied PNG logo was visually inspected and copied unchanged; its exact hash is pinned in the release policy.

These checks exercise the local dependency environment against exported source. They are not a fresh-machine installation or new live FL/plugin qualification. Before the clean-history migration, Windows CI also passed **2,232 tests with 14 skips** using locked Python 3.12 dependencies. The [SyncFL checks](https://github.com/ALX-CODE/SyncFL-MCP/actions/workflows/tests.yml) report verification of subsequent commits.

| Evidence | Establishes | Does not establish |
|---|---|---|
| Offline tests | Argument validation, state machines, fake-host behavior, codecs, recovery boundaries | Live FL or plugin behavior |
| Wheel checks | Required source/runtime files and hashes are packaged | Installed-host compatibility |
| Installation doctor | Imports, paths, deployed hashes; live mode checks loaded controller/handshake | Musical quality or all workflow behavior |
| Native MIDI loopback | Owned endpoints and cross-process MIDI delivery on that host | Every Windows version or plugin API |
| Parameter holdouts | Sampled normalized/display relationships, enums and retained ranges | All values, exact binary identity or audible DSP state |
| Native-save live lab | Recorded host transitions and independently checked known lab file | A general save API with a known absolute path |
| Render verification | Decodable output, expected format/duration, consistent project, non-silent content | A musically successful mix |
| Listening | Perceived sound in context | Structural/source integrity without separate checks |

## Recorded baseline: October 12, 2026

The plugin/background upgrade at `76985c736abeacf984f7d243fae8f861ec2d3adc` passed:

- 2,225 local Python tests, 2 skips, Python 3.13.13.
- 2,186 Windows CI Python tests, 12 skips, Python 3.12.
- 10 native tests: MIDI 3, background helper 4, SDK fake host 3.
- Fresh server registration/connection with 139 tools and workflow revision 9.
- Six deployed integration-file matches and a matching loaded-controller digest.
- Three native lab saves with independently parsed tempos 140 → 141 → 140 BPM and unchanged foreground.

The plugin registry retained 379 numeric and 423 enum mappings after 6,496 independent holdout checks. Failing endpoints were excluded or mappings withheld rather than accepted with wider tolerances. [Calibration evidence](plugin-calibration-evidence.json) records counts, ranges and registry hashes.

An isolated Pro-Q write exposed a stale display and was rejected. Added read-settling logic passed regression tests but awaits another isolated live qualification. Later selected-track OLE loading attempts failed without changing a slot. Neither result is represented as completed background loading support.

These are dated baseline results. Consult current CI and run the [development checks](DEVELOPMENT.md) for later changes. Public documentation/reference checks are separate from this historical runtime baseline.
