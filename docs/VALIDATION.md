# Validation and evidence

## Background score and context qualification: October 12, 2026

The FL26 live lab verified 32 score writes across eight channels and two named
patterns, plus occupied-score replacement and idempotent import. Saved-FLP
readback checked exact pitch, tick position/length, velocity, pan, release, note
color and slide. Other scores, pattern names, channel identities, project timing
and Playlist clips were preserved. FL's editor-selection bit was ignored;
other note bits remain part of the comparison.

Forty-eight completed switches covered pattern/channel selection, generator or
sample identity, routed mixer selection and loaded effects. Fresh MCP-server
earlier tests exercised two legacy modes with identical backend
behavior. Same-pattern sound switches measured about 0.8 seconds median; changing
the pattern required a verified native save and took about 1.3–1.5 seconds.
The final matrix's score writes measured about 1.6 seconds median. These timings
describe this machine and project, not a latency guarantee.

Stale pattern/effect contexts were rejected before dispatch. A live covered-Mixer
test automatically exposed the native panel and loaded a Parametric EQ 2 into
the exact empty slot; later reads confirmed the complete effect chain. API-only
main-window exposure preserved the observed foreground, cursor and capture.
The four native bridge CTests also passed, including the no-input import check.

The private OLE route remains experimental. This verifies stored notes and host
identities, not audible plugin DSP, licensing or every FL build. Preset labels
are not guessed: the context reports saved generator-state fingerprints and
sample references. Empty-score clearing and advanced expression are unqualified.
The recording remained stopped throughout this repair.

Different checks answer different questions. A passing unit test is useful, but it does not prove a particular plugin sounds correct or that another machine's FL UI matches the calibrated layout.

## Windows installation: October 12, 2026

The canonical PowerShell installer and its compatibility wrapper were exercised
with isolated command stubs for all three supported clients, custom paths with
spaces, deployment failures and dependency selection. A real exported source
snapshot was then installed into a fresh Python 3.13 environment using the local
dependency cache. The real installer deployed all six FL integration files into
a separate test user-data folder, passed the offline doctor, preserved an
unrelated Claude configuration entry and created a verified configuration backup.
A repeat install changed no controller files or client settings. The fresh
environment contained no pytest, Ruff or FL API stubs.

This test used the existing uv installation and cached dependencies. The pinned
official bootstrap script was downloaded and its SHA-256 checked separately;
the download-and-execute bootstrap path was not run on a fresh Windows machine.
No live MIDI commands, FL reloads or desktop input were part of this installation
test. Windows MIDI Services and the Visual C++ runtime were already installed.

Contributor setup was tested with isolated Git command fixtures for cloning,
reuse, failure handling, option forwarding and refusing unrelated checkouts.
A source export was also prepared as a separate local Git checkout and installed
with the real developer bootstrap using cached dependencies. All 342 exported
file hashes remained unchanged; development dependencies and the branded
`syncfl` command were installed. Its CLI help and 114 focused tests passed.
The default developer flow did not deploy FL files or register an assistant.
Private GitHub authentication and first-time network cloning were not exercised
by this local installation test.

The final integrated developer setup passed **2,765 Python tests with 2 skips**
on Python 3.13.13, including the CLI and installer suites. Targeted Ruff checks,
all 45 public guides, 424 local links, 9 examples and the generated reference for
143 tools also passed.

## Stock effects and instruments: October 12, 2026

The integrated checkout passed **2,735 Python tests with 2 skips** on Python
3.13.13, including the optional CLI and Windows installer suites. Targeted Ruff,
generated-tool documentation, all 44 public guides and the source-export privacy
scan passed. The export contains 338 allowlisted files; lab records and projects
remain outside the repository.

All 36 requested stock plugins loaded in a stopped, Master-muted disposable session: 26 effects and 10 instruments. The native API exposed 2,911 named controls. 156 numeric and 117 menu mappings survived 3,644 independent holdout checks; failed or unsupported mappings remain normalized only.

Calibration used measured host displays. Retained numeric mappings passed all eight off-grid values and retained menus passed every declared choice. Rejected candidates could stop at their first failed check. Only one control per plugin changed at a time. Checks exercised the production compiler and readback verifier. At least 400 ms elapsed before two equal later callbacks. Whole exposed parameter values and display strings, including unnamed parameters, were compared for collateral changes and final restoration. No synthetic keyboard or mouse input was sent.

Mode requirements are attached to retained mappings. MIDI inputs, sequencer/editor data and unobservable mode dependencies were excluded from blind sweeps. Soundgoodizer is detection only; Fruity Delay, Fruity Reeverb and FL Slayer are normalized only because their displays were empty. This evidence qualifies host mapping for the observed builds, not licensing, audible DSP behavior or arbitrary presets.

Sakura and Sawer retain named normalized controls without physical qualification. A host exit interrupted the initial sweep; the crash log identified Sawer in an audio-processing access violation, without establishing a triggering parameter. The measurements were retained outside FL, and recovery restored the complete exposed Sakura and Sawer baselines. No interrupted sweep is counted as qualified.

Sytrus has 597 named normalized controls but no qualified physical mappings in this release. Manual settings changes invalidated its baseline. Its 2,327 interrupted checks are excluded from all qualification totals and retained externally. Exact restoration and complete qualification are not claimed.

Morphine and Toxic Biohazard required separate write-reachable test baselines: the native setter cannot reproduce every finer value stored in the original preset. The original snapshots and recovery project remain preserved; restoration evidence for these two profiles refers specifically to the test baselines.

See the [stock coverage table](STOCK-PLUGIN-GUIDE.md) and [artifact hashes and counts](plugin-calibration-evidence.json).

A fresh stdio MCP process exposed all 143 tools and bound all 36 stock profiles
against the live host. Production apply/restore checks passed for Fruity Chorus,
Fruity Compressor, Maximus, 3x Osc and FL Slayer. The strict normalized verifier
rejected an FL Slayer String request of 0.417 when the host returned 0.5; an exact
endpoint request and restoration then passed. This does not establish a complete
menu or physical-unit mapping for that legacy control. Final read-only checks
matched the complete exposed baselines of the other 35 plugins. Sytrus identity
was checked separately, with no restoration claim. Receipts remain outside the
public source tree.

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

## Plugin qualification and background loading pass: October 12, 2026 (evening)

In the same disposable lab (Master muted), earlier declined background loads were
traced to FL's maximized Playlist covering the Mixer: FL routes the OLE drop by screen
position, and the slot's drop point resolved to the Playlist. After FL's own
`ui.showWindow(0)` brought the Mixer forward, five empty Master-slot loads verified on
their first dispatch, and the OS foreground window was unchanged. The loader now
refuses a covered slot.

Integration regression checks also reject owned popups covering a target while
accepting real child controls. The calibration registry is stored byte-for-byte
so its published evidence hash survives both LF and Windows CRLF checkout settings.

Auto-Tune Pro, Decapitator, EchoBoy, MicroShift and Little AlterBoy were then calibrated
with the final settling protocol: at least 400 ms, then two equal value/display reads
200 ms apart. They were held out at off-grid positions and promoted under the unchanged
rules: 563 checks, 557 passed, and every touched parameter restored. Calibration exposed
three host couplings, each now declared or handled:

- Decapitator Auto Gain rewrites Output Trim's host value, so Output Trim requires Auto Gain Off.
- Auto-Tune Key spelling follows Modern Scale, so Key requires Chromatic.
- EchoBoy Mode and Note values drive each other. The joint sweep mislabelled Triplet and
  Dotted; the joint holdout caught it. An isolated re-sweep and a one-control-at-a-time
  holdout corrected the labels.

EchoBoy Rhythm Time, a sampled table that failed its holdout, stays normalized.

A fresh stdio server from the updated source then applied 23 named-control writes and
restores across the five profiles through `fl_plugin_controls_set`, all `verified`. It
refused an unmet mode condition, an out-of-range value and the withheld control. A
final read found zero drift from the baselines. The offline suite passed 2,331 tests
before the registry update. As with the earlier pass, this is host parameter/display
evidence, not proof of audible DSP behavior.

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
