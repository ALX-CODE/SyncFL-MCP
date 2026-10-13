# Stock plugin guide

All 36 requested stock plugins were loaded and inspected in FL Studio 26.1.4
on October 12, 2026: 26 effects and 10 instruments, exposing 2,911
named controls. 156 numeric and 117 menu mappings passed
independent qualification. The pass ran 3,644 host-display checks
without synthetic keyboard or mouse input.

Each profile binds the exact native name, parameter count and full layout hash.
The packaged catalog exposes every named key, parameter index, calibrated
range, unit, menu choice, condition and limitation through
`fl_plugin_adapter_catalog`; use `fl_plugin_controls_inspect` to bind a live
instance before changing it.

| Plugin | Host parameters | Numeric | Menu | Normalized only |
|---|---:|---:|---:|---:|
| Fruity Blood Overdrive | 6 | 5 | 1 | 0 |
| Fruity Chorus | 12 | 4 | 5 | 3 |
| Fruity Compressor | 6 | 2 | 1 | 3 |
| Fruity Convolver | 4 | 0 | 0 | 4 |
| Fruity Delay | 6 | 0 | 0 | 6 |
| Fruity Delay 2 | 8 | 0 | 1 | 7 |
| Fruity Delay 3 | 26 | 4 | 2 | 20 |
| Fruity Delay Bank | 131 | 0 | 0 | 131 |
| Fruity Fast Dist | 5 | 0 | 1 | 4 |
| Fruity Flanger | 12 | 1 | 2 | 9 |
| Fruity Flangus | 8 | 2 | 1 | 5 |
| Fruity Limiter | 18 | 4 | 2 | 12 |
| Fruity Love Philter | 359 | 3 | 0 | 356 |
| Fruity Multiband Compressor | 28 | 12 | 5 | 11 |
| Fruity Parametric EQ 2 | 36 | 15 | 14 | 7 |
| Fruity Phaser | 9 | 3 | 2 | 4 |
| Fruity Reeverb | 10 | 0 | 0 | 10 |
| Fruity Reeverb 2 | 15 | 11 | 0 | 4 |
| Fruity Soft Clipper | 2 | 0 | 0 | 2 |
| Fruity Stereo Enhancer | 6 | 1 | 2 | 3 |
| Fruity Stereo Shaper | 6 | 0 | 0 | 6 |
| Maximus | 59 | 12 | 18 | 29 |
| Soundgoodizer | 0 | 0 | 0 | 0 |
| Transient Processor | 9 | 0 | 3 | 6 |
| Gross Beat | 9 | 0 | 0 | 9 |
| Tuner | 3 | 2 | 1 | 0 |
| 3x Osc | 114 | 44 | 11 | 49 |
| FL Keys | 15 | 9 | 0 | 6 |
| FL Slayer | 29 | 0 | 0 | 26 |
| Morphine | 252 | 0 | 25 | 227 |
| Ogun | 89 | 22 | 4 | 63 |
| PoiZone | 203 | 0 | 16 | 187 |
| Sakura | 207 | 0 | 0 | 207 |
| Sawer | 202 | 0 | 0 | 202 |
| Sytrus | 597 | 0 | 0 | 597 |
| Toxic Biohazard | 423 | 0 | 0 | 423 |

Host parameter counts include unnamed parameters that have no named adapter key.
Numeric and menu columns contain retained qualified mappings. Normalized-only
controls expose the host value, with no claimed physical conversion.

**Known limits:** Soundgoodizer exposes zero host parameters, so its profile
provides detection only. Fruity Delay, Fruity Reeverb and FL Slayer expose
named parameters but no display strings in these builds; their controls remain
normalized only. Structured editors, MIDI inputs and hidden modes were excluded
from blind calibration. Sakura and Sawer retain named normalized controls only:
a host exit interrupted the lab, and its crash log identified Sawer's audio engine.
Their physical calibration is explicitly withheld; the log does not establish
which earlier parameter change caused the failure. Other measurements were saved
externally and the recovered exposed values and displays matched the baselines.
A successful host write does not prove audible DSP behavior.

Sytrus exposes 597 named normalized controls. Its physical mappings are withheld: manual settings changes invalidated the test baseline during the run. The 2,327 interrupted checks are preserved externally and excluded from the qualification totals. Exact restoration is not claimed; physical qualification requires a fresh baseline and complete rerun.

Morphine and Toxic Biohazard quantize host writes more coarsely than saved
preset values. Their tests use separately captured write-reachable baselines.
Restoration evidence refers to those test baselines, not exact recovery of the
original preset precision. The original snapshots and recovery project were preserved.

The [manual source list](stock-plugin-manuals.json) explains control meanings.
The [qualification evidence](plugin-calibration-evidence.json) records measured
counts and artifact hashes; per-profile binary/version provenance is in the
packaged catalog. Manual descriptions below are guidance, not extra mapped APIs.

## Qualification rules

- Bind every plugin to a fresh runtime identity: native name, parameter count, layout hash, current values and host displays when available.
- Keep repeated labels section-qualified. Delay plugins, multiband dynamics, Love Philter, Delay Bank, Maximus, Sytrus and Toxic Biohazard all reuse labels across sections or operators.
- Change mode selectors separately, inspect again, then change dependent controls. Examples include EQ band type, delay tempo sync, filter family, sidechain target, selected band, operator routing and arpeggiator state.
- Treat editors, file operations, randomizers, analyze/import buttons, envelope graphs and preset commands as guarded actions, not normal numeric sweeps.
- Use stopped transport, muted monitoring or controlled low-level sources for feedback, drive, resonance, lookahead, stereo cancellation and long-tail effects.

## Effects

### Fruity Blood Overdrive

Manual role: compact distortion with pre-filtering, preamp drive, optional x100 drive, post filtering and post gain.

Qualification guidance: keep drive and output compensation separate. The x100
switch should be qualified as a distinct high-gain condition, not as a normal
continuation of the drive knob. The manual's filter wording is not enough to
infer exact filter math or units.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Blood%20Overdrive.htm)

### Fruity Chorus

Manual role: chorus with short delay, modulation depth, stereo phase offset, three LFO sections, crossover filtering and wet-only send mode.

Qualification guidance: Delay is documented in milliseconds, but the audible
modulation interval also depends on Depth. Stereo is a phase relationship, not a
width percentage. Cross Cutoff depends on Cross Type, so qualify that selector
before promoting physical frequency labels.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Chorus.htm)

### Fruity Compressor

Manual role: classic compressor with threshold, ratio, gain, attack, release, compression type and sidechain input.

Qualification guidance: ratios below 1:1 expand rather than compress. Type
changes both tone and release behavior, and sidechain targets depend on mixer
routing. Do not ship fixed sidechain enum labels from one lab session as
universal labels.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Compressor.htm)

### Fruity Convolver

Manual role: convolution processor with dry/wet mix, stereo controls, impulse editing, EQ and impulse file workflows.

Qualification guidance: the impulse response is part of the sound identity.
Delay, Stretch, Self-convolution and EQ can rebuild or reshape the convolution
path, so qualify them with state capture. File loading, recording and envelope
editing need guarded action APIs if SyncFL exposes them later.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Convolver.htm)

### Fruity Delay

Manual role: legacy delay with input, feedback, cutoff, tempo, step spacing and stereo mode.

Qualification guidance: Tempo and Steps jointly determine echo time. Tempo
automation can break up repeats in this legacy engine. The current lab baseline
exposes named writable parameters, but no display text, so keep physical claims
conservative.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Delay.htm)

### Fruity Delay 2

Manual role: tempo-synced delay with input, feedback, offset, dry level and routing mode sections.

Qualification guidance: TIME uses FL's steps:ticks display rather than plain milliseconds. Preserve the three separate VOL controls by section. OFS is directional and mode-dependent, so read it with the current stereo routing mode.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Delay%202.htm)

### Fruity Delay 3

Manual role: modern delay with tempo sync, pitch behavior, stereo model, feedback filtering, modulation, diffusion, distortion and output tone.

Qualification guidance: Tempo Sync changes Time units. In Ping Pong mode, Offset
behaves like pan; in other modes it scales timing. Feedback can exceed safe
unity-like behavior when resonance is high, so keep feedback, filter resonance
and drive sweeps guarded.

Observed limit: changing Delay type renamed an exposed offset parameter to a pan
parameter. That selector remains normalized-only. Inspect the layout again after
changing it; the previous profile binding may no longer apply.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Delay%203.htm)

### Fruity Delay Bank

Manual role: eight-bank multi-delay with repeated per-bank input, filter, feedback, grain and output sections.

Qualification guidance: every repeated label needs bank and section context.
NEXT routing is unavailable on bank 8 and can invert phase when negative. Tempo
mode changes TIME semantics, and feedback filters can apply before or after
delay depending on section state.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Delay%20Bank.htm)

### Fruity Fast Dist

Manual role: lightweight distortion with input drive, threshold/waveshaping, algorithm selection, mix and post output.

Qualification guidance: PRE and THRES both affect distortion, while POST is output compensation. Treat A/B as a selector and avoid using loud distortion sweeps as simple pass/fail tests.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Fast%20Dist.htm)

### Fruity Flanger

Manual role: flanger with delay, depth, rate, stereo phase, damping, shape, feedback, polarity switches and dry/wet/cross gains.

Qualification guidance: Delay and Depth define the modulation range together. Dry, Wet and Cross include negative-infinity style gain endpoints, so adapters need sentinel handling. Cross is the channel-swapped wet path, not a generic blend.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Flanger.htm)

### Fruity Flangus

Manual role: stacked flanger effect with order, modulation depth, speed, delay, spread and polarity-capable cross/dry/wet controls.

Qualification guidance: negative cross, dry and wet values reverse polarity. ORD and SPRD change the ensemble structure, while DEPTH, SPD and DEL lack stable physical units in the manual, so promote only after host display qualification.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Flangus.htm)

### Fruity Limiter

Manual role: combined compressor, limiter and gate with sidechain support and lookahead limiting.

Qualification guidance: repeated labels across compressor, limiter and gate must
remain section-qualified. Limiter attack adds lookahead latency; compressor
attack does not. Sidechain input is routing-dependent and should not be a fixed
public enum.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Limiter.htm)

### Fruity Love Philter

Manual role: eight-bank filter, waveshaper and modulation system with pattern, LFO, XY and input-follower articulation.

Qualification guidance: bank selection, filter family, ALT mode and articulator
target all change meaning. Pattern editing, focused note input,
randomization-like editor flows and envelope graph operations need guarded APIs
before public automation.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Love%20Philter.htm)

### Fruity Multiband Compressor

Manual role: three-band compressor with optional limiter and IIR/FIR band splitting.

Qualification guidance: LOW, MID and HIGH repeat the same control families and need band-qualified names. Crossover controls interact and can exclude frequency ranges, so sweep one cutoff at a time and restore it before moving to another.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Multiband%20Compressor.htm)

### Fruity Parametric EQ 2

Manual role: seven-band EQ with filter types, slope/order choices, optional linear-phase mode, HQ modes and analyzer options.

Qualification guidance: band type controls whether gain is active. LIN, HQ and
precision options affect latency or filter behavior, and monitor modes are
analysis state rather than sound shaping. Compare/spare/key-menu flows should be
treated as commands.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Parametric%20EQ%202.htm)

### Fruity Phaser

Manual role: phaser with LFO frequency range, min/max depth, stereo phase, stages, feedback, dry/wet and output gain.

Qualification guidance: Sweep Frequency depends on the Frequency Range selector. Min and Max Depth form a coupled range, and feedback/output gain can raise level, so qualify them with monitoring guarded.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Phaser.htm)

### Fruity Reeverb

Manual role: legacy reverb with filters, predelay, room size, diffusion, color, decay, damping and dry/reverb levels.

Qualification guidance: Image-Line recommends Reeverb 2 for newer work. Low Cut and High Damping have bypass-style boundary states, and the current lab baseline does not provide host display text.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Reeverb.htm)

### Fruity Reeverb 2

Manual role: algorithmic reverb with mid/side handling, tempo predelay, size, diffusion, modulation, bass multiplier, damping, wet/dry and stereo separation.

Qualification guidance: SIDE mode needs stereo input to matter. DELAY and TEMPO are coupled, DAMP has a bypass-style endpoint, and long decay/tail settings should be tested muted or with controlled silence.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%2520Reeverb%25202.htm)

### Fruity Soft Clipper

Manual role: soft-knee clipping/limiting with threshold, post gain and a levels display.

Qualification guidance: THRES and POST jointly affect loudness and clipping. The levels display can edit threshold in the GUI, but it should not be documented as a separate host control unless runtime exposes it.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Soft%20Clipper.htm)

### Fruity Stereo Enhancer

Manual role: stereo image processor with separation, phase delay, pre/post order, channel inversion, pan and volume.

Qualification guidance: use stereo test material, because separation has no effect on mono sources. Phase Offset is signed by left/right direction and Pre/Post changes processing order.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Stereo%20Enhancer.htm)

### Fruity Stereo Shaper

Manual role: stereo matrix, panning, phase inversion, mid/side routing, stereo reduction/enhancement and mono stereoizing.

Qualification guidance: matrix sliders can add, subtract or cancel content.
In/Out Difference sidechain selection depends on available routing and uses
send-to-track order, not mixer track numbers. Avoid blind sidechain sweeps in
real projects.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Fruity%20Stereo%20Shaper.htm)

### Maximus

Manual role: multiband compressor, limiter, maximizer, gate, expander, ducker, de-esser and saturator with Low, Mid, High and Master sections.

Qualification guidance: selected band context changes what the panel controls
affect. Envelope editors are not simple knobs. Master attack and LMH delay add
latency, and saturation mode changes can click while processing, so sweep
stopped or muted.

Manuals:
- [Image-Line manual 1](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Maximus.htm)
- [Image-Line manual 2](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Maximus_Controls.htm)

### Soundgoodizer

Manual role: one-knob Maximus-based enhancer with four modes.

Qualification guidance: the manual identifies A/B/C/D modes and a main amount
control, but the current lab baseline exposes zero host parameters. Document the
plugin as load-detected and manual-understood until a usable parameter path
exists.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Soundgoodizer.htm)

### Transient Processor

Manual role: transient shaper with split frequency/balance, attack and release shaping, output gain and drive.

Qualification guidance: Split Frequency and Split Balance determine what reaches
the transient process. Attack can raise peaks sharply, Drive changes saturation,
and the manual describes trial/save limitations, so avoid project-persistence
claims without license confirmation.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Transient%20Processor.htm)

### Gross Beat

Manual role: time and volume manipulation using a two-bar rolling buffer, 36 time slots, 36 volume slots and editable mapping envelopes.

Qualification guidance: this is the most stateful stock effect in the requested
set. Time/volume slots, envelope editors, MIDI triggering, hold modes, sync modes
and buffer state must be qualified separately. The manual has inconsistent slot
MIDI note references, so verify note helpers live before publishing them.

Manuals:
- [Image-Line manual 1](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Gross%20Beat.htm)
- [Image-Line manual 2](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/GrossBeat_MainOptions.htm)

### Tuner

Manual role: monophonic pitch detector and visual tuning display, not pitch correction.

Qualification guidance: use controlled monophonic input for behavior tests. Reference, Reactivity, Bass and Visual mode affect detection or display; they should not be described as audio processing.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Tuner.htm)

## Generators

### 3x Osc

Manual role: three-oscillator subtractive generator with waveform, phase, invert, coarse/fine pitch, pan, AM, HQ and hierarchical mix controls.

Qualification guidance: oscillator 2 mixes against oscillator 1, and oscillator 3 mixes against the first two together. Phase and invert can cancel identical signals, so use oscillator-state-aware tests.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning-content/fl-studio-online-manual/html/plugins/3x%20Osc.htm)

### FL Keys

Manual role: sample-based keyboard generator with instrument selection, decay, release, pan/autopan, stereo spread, velocity, tone, overdrive, LFO and stretch controls.

Qualification guidance: instrument selection changes the response surface. Pan and LFO frequency together create autopan behavior. Stretch affects high notes from C#5 upward, so pitch tests need a known note range.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/FL%20Keys.htm)

### FL Slayer

Manual role: legacy guitar-model generator with guitar, pickup, amp, cabinet, damping, delay and effects controls.

Qualification guidance: Image-Line marks it as deprecated and no longer included in FL Studio 12 or later. Treat missing availability as expected for modern installs, not as a SyncFL failure.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/FL%20Slayer.htm)

### Morphine

Manual role: additive synthesizer with four generators, morphing, velocity/key maps, interpolation, resynthesis and import/edit tools.

Qualification guidance: morph mode changes what generator data is blended. Resynthesis, image/sample analysis, load/save, randomize and harmonic editors should be guarded actions rather than blind automation sweeps.

Manuals:
- [Image-Line manual 1](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Morphine.htm)
- [Image-Line manual 2](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Morphine_General.htm)

### Ogun

Manual role: additive metallic synthesizer with harmonic masking, timbre impulse processing, filtering, LFO, ADSR, unison, effects and XY modulation.

Qualification guidance: harmonic masking, timbre impulse controls, resynthesis and XY modulation all affect the same timbral surface. Dense unison and output-level changes need muted or controlled qualification.

Manual: [Image-Line manual](https://ww2.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Ogun.htm)

### PoiZone

Manual role: subtractive synthesizer with oscillator mix/tuning/shape, filters, ADSR envelopes, LFO target amounts, arpeggiator, chorus, delay, unison, portamento and polyphony.

Qualification guidance: LFO amount depends on selected target. Arpeggiator mode, rate and range alter note scheduling, and demo noise can be mistaken for parameter failure if license state is not known.

Manuals:
- [Image-Line manual 1](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/PoiZone.htm)
- [Image-Line manual 2](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/PoiZone_Controls.htm)

### Sakura

Manual role: physical-model string instrument with excitation, string, vibration, damping, pickup, body, modulation, effects and keyboard controls.

Qualification guidance: physical-model parameters interact strongly. Avoid joint random sweeps across excitation, damping, body and pickup, because believable output depends on their relationships.

Manual: [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Sakura.htm)

### Sawer

Manual role: synthesizer with oscillator controls, filter, envelope, LFO, effects, unison and arpeggiator sections.

Qualification guidance: unison voice count, detune, spread and mix affect gain and CPU. Sync, bend and pulse width interact with oscillator shape. The available source pages are beta manual URLs, so flag them if a stable page replaces them.

Manuals:
- [Image-Line manual 1](https://www.image-line.com/fl-studio-learning/fl-studio-beta-online-manual/html/plugins/Sawer_General.htm)
- [Image-Line manual 2](https://www.image-line.com/fl-studio-learning/fl-studio-beta-online-manual/html/plugins/Sawer_OSC.htm)

### Sytrus

Manual role: six-operator FM/RM/additive synthesizer with operator settings, envelopes, filters, effects and a modulation matrix.

Qualification guidance: operators can be carriers or modulators depending on matrix routing. Matrix amounts, harmonic editing, envelopes and mapping pages are powerful editor/state surfaces and should be guarded.

Manuals:
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Sytrus.htm)
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Sytrus_opsettings.htm)
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Sytrus_modmatrix.htm)

### Toxic Biohazard

Manual role: six-operator FM synthesizer with oscillator/operator controls, FM matrix, filters, envelopes, LFOs, sequencer, effects and master controls.

Qualification guidance: FM matrix routing and sequencer steps are structural state. Operator tuning plus high modulation amounts, filter resonance and effects feedback should be guarded, especially when license/demo state is unknown.

Manuals:
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-beta-online-manual/html/plugins/Toxic%20Biohazard.htm)
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/ToxicBio_oscillators.htm)
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/ToxicBio_filter.htm)
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/ToxicBio_FMmatrix.htm)
- [Image-Line manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/ToxicBio_sequencer.htm)
