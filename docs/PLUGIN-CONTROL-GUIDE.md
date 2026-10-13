# Named plugin controls

Use named controls to request a value such as a compressor threshold in dB, a
tuning speed in milliseconds, or an exact mode label. The adapter resolves the
parameter from a fresh host inspection; callers do not need remembered parameter
numbers or screen coordinates.

**Status, 2026-10-12:** 14 exact-layout profiles expose 1,961 named controls.
All 14 retain qualified mappings: 420 numeric and 455 enum after 7,059 independent
live holdout checks. Failed taper endpoints are excluded from retained ranges;
unqualified controls remain explicitly normalized. The committed
[calibration evidence](plugin-calibration-evidence.json) records counts, layout
hashes, withheld controls and restricted ranges.

An evening pass on the same date qualified Auto-Tune Pro and Decapitator with the
final settling protocol and added EchoBoy, MicroShift and Little AlterBoy profiles.
Mode-locked controls carry declared requirements (see
[Soundtoys](#soundtoys-decapitator-echoboy-microshift-little-alterboy)). Auto-Tune's
Harmony Player and MIDI options remain normalized: they were outside that sweep.
Read current metadata before each job. These checks establish host parameter
mapping, not license status, processing behavior or audible quality.

| Profile | Numeric | Enum | Normalized only |
|---|---:|---:|---:|
| Auto-Tune Pro | 14 | 10 | 68 |
| Decapitator | 6 | 6 | 0 |
| EchoBoy | 12 | 11 | 3 |
| Little AlterBoy | 4 | 3 | 0 |
| MicroShift | 5 | 2 | 0 |
| Pro-C 3 | 34 | 33 | 27 |
| Pro-DS | 8 | 7 | 4 |
| Pro-L 2 | 5 | 8 | 4 |
| Pro-Q 4 | 192 | 240 | 82 |
| Pro-R 2 | 46 | 70 | 22 |
| Saturn 2 | 62 | 28 | 860 |
| MetaTune | 6 | 14 | 1 |
| soothe2 | 21 | 17 | 14 |
| Tube-Tech CL 1B mk II | 5 | 6 | 1 |

## Inspect, choose, preview, apply

1. Use fresh channel/mixer/plugin observations to identify the intended instance.
   `index` is a global channel when `slot_index=-1`; otherwise it is a mixer track,
   with `slot_index` identifying its effect slot. Never reuse a saved slot guess.
2. `fl_plugin_adapter_catalog(query=...)` reads packaged profiles offline. It
   shows identity evidence, control keys, units, choices, ranges and calibration
   metadata. It does not establish that a plugin is loaded or licensed.
3. `fl_plugin_controls_inspect(index=..., slot_index=...)` reads the complete
   current parameter catalog before filtering/pagination. It binds an exact
   native plugin name, parameter count, full layout hash and parameter names,
   then returns the current revision, values, literal displays and conditions.
4. `fl_plugin_controls_set(..., apply=False)` preflights the complete requested
   change. `apply=True` requires stopped transport, an unchanged revision and a
   **new absolute `.json` receipt path**. One batch is sent; later host callbacks
   must agree before the result can be `verified`.

These calls use native parameter access without mouse or keyboard input. They do
not load plugins, create missing controls or silently switch to GUI automation.
An unknown or changed layout fails closed. Identical names and layouts cannot
prove which binary build is loaded; compare the profile's version/hash evidence
with the installed plugin, especially after updates.

### Change forms

| Request | Meaning and verification |
|---|---|
| `{"control": key, "value": number}` | Physical/semantic value in that control's declared `unit`. Requires calibrated points or an analytic curve, stays within the calibrated range, and checks the resulting numeric host display within the declared tolerance. |
| `{"control": key, "value": label}` | Exact enum label from `choices`. The adapter verifies its declared literal host display, including significant whitespace. |
| `{"control": key, "normalized": number}` | Explicit host value in `0..1`. Verifies normalized readback only; it makes no claim about milliseconds, Hz, dB or audible effect. |

Inspect `kind`, `calibration_status`, `calibrated_range`, `unit`, `display_unit`,
`requires`, `requirement_status` and `requirements_met`. A `normalized` control,
or a numeric control marked `calibration_required`, has no approved physical
conversion. Do not invent a linear/logarithmic mapping from the knob position.
`display_unit` can differ from the semantic unit when a plugin omits units from
its host text; that correspondence belongs to the particular profile.

Conditions apply to normalized writes too. Change a mode selector separately,
inspect again, then change dependent controls. A selector and a control that
depends on it cannot be changed in the same named-control batch.

### Example: a discovered numeric control

The following is MCP-client pseudocode. `target` comes from fresh instance
discovery; `requested_control_key` and `requested_value` are chosen after reading
the returned metadata. There are no fixed track, slot or parameter indices.

```python
observed = fl_plugin_controls_inspect(
    index=target["index"], slot_index=target["slot_index"]
)
control = next(c for c in observed["controls"]
               if c["key"] == requested_control_key)
assert control["kind"] == "numeric"
assert control["calibration_status"] in {"curve", "points"}
assert control["requirements_met"]
changes = [{"control": control["key"], "value": requested_value}]

preview = fl_plugin_controls_set(
    index=target["index"], slot_index=target["slot_index"],
    expected_plugin=observed["target"]["plugin"],
    expected_revision=observed["revision"], changes=changes, apply=False
)
assert preview["status"] == "preflighted"

# After reviewing the preview, obtain a fresh revision for the same instance.
fresh = fl_plugin_controls_inspect(
    index=target["index"], slot_index=target["slot_index"]
)
assert fresh["target"] == observed["target"]
assert fresh["layout_sha256"] == observed["layout_sha256"]
result = fl_plugin_controls_set(
    index=target["index"], slot_index=target["slot_index"],
    expected_plugin=fresh["target"]["plugin"],
    expected_revision=fresh["revision"], changes=changes,
    apply=True, receipt_file=str(new_absolute_receipt_path)
)
```

Check `success`, `status`, the receipt and both later matching readbacks. If the
result is uncertain or verification fails, inspect current state and the receipt
before recovery. Never automatically repeat the batch. If the requested control
is outside the returned page, follow `next_offset` or use a query; do not select a
different control by list position.

Write responses include observations for the requested parameters, with the
full parameter count and explicit scope. The on-disk receipt retains complete
snapshots for diagnosis; thousands of unrelated wrapper parameters are omitted
from the MCP response.

## Installed-version evidence

The local file/scanner inventory on 2026-10-12 recorded the following versions.
File presence, a database favorite and a matching catalog are separate from a
successful license check, live load or completed calibration.

| Plugin | Recorded installed version |
|---|---|
| Auto-Tune Pro | Installed-app registration **11.0.0**; the binary has no readable PE product-version resource, so this is not independent binary-version attestation. |
| MetaTune | **1.1.15.0**, build `91a84d3a94` |
| FabFilter Pro-Q 4 | **4.10** |
| FabFilter Pro-C 3 | **3.00** |
| FabFilter Pro-L 2 | **2.24** |
| FabFilter Saturn 2 | **2.11** |
| FabFilter Pro-DS | **1.30** |
| FabFilter Pro-R 2 | **2.04** |
| soothe2 | **1.1.2** |
| Tube-Tech CL 1B mk II | **2.5.9** |
| Soundtoys Decapitator, EchoBoy, MicroShift, Little AlterBoy | **5.0.1 64 bit** |

The Soundtoys x86 and x64 files coexist: select the x64 instance deliberately.
Some plugins also have duplicate favorites or misleading database categories.
Use the exact observed native plugin identity rather than a favorite filename
as proof of type or version.

## Meanings that must survive the convenience layer

### Auto-Tune Pro 11 and MetaTune

Auto-Tune Retune Speed describes correction time. Humanize relaxes correction
on sustained portions of long notes while retaining the shorter-note treatment.
Input Type, key/scale, Modern/Classic behavior and formant options are separate
decisions; a named parameter setter does not transcribe notes or edit Graph Mode.
[Auto-Tune Pro 11 manual](https://www.antarestech.com/documentation/auto-tune-pro-11),
[Antares control guidance](https://help.antarestech.com/hc/en-us/articles/42858099043092-AutoTune-Best-Practices).

MetaTune Sustain must not be treated as a renamed Humanize knob: **positive
Sustain speeds up correction on held notes; negative Sustain slows it down**.
MetaTune also permits negative Speed for harder correction. Its Note Stabilizer,
Amount and linked Groups have distinct functions. Inspect grouping before an
edit that could affect another linked instance.
[MetaTune user guide](https://docs.slatedigital.com/MetaTune/MetaTune.html).

Auto-Tune's **Key** labels change spelling with **Modern Scale** (observed: `Db`
under Major, `C#` under Minor, sharps under Chromatic). Key is qualified only with
Modern Scale = Chromatic; under another scale, set Key with an explicit normalized
value and read the literal display. Detune is the reference pitch in Hz (the host
display is unitless), not cents. Throat Length requires Formant Correction On.

### Soundtoys: Decapitator, EchoBoy, MicroShift, Little AlterBoy

Several Soundtoys controls only respond in a particular mode, and the host shows
that dependency rather than hiding it. These were qualified under the stated
condition and declare it as a requirement:

| Control | Requirement | Observed outside the condition |
|---|---|---|
| EchoBoy Echo 1 Time / Echo 2 Time | Echo 1 Mode / Echo 2 Mode = Time | Display locked to the note value at the project tempo |
| EchoBoy Rhythm Time | Rhythm Mode = Time | Display locked to the rhythm note |
| Decapitator Output Trim | Auto Gain = Off | Auto Gain rewrites the host value as Drive changes while the display keeps the written trim |

EchoBoy Tempo and BeatSync displays did not move across a full sweep in the lab
(Tempo stayed at the project's 140 bpm), so they remain normalized. EchoBoy's
`Edited Style` is a status label, not a selectable style, and is excluded from
Style. Feedback above 1.0 can build up and self-oscillate. EchoBoy Low/High Cut,
Groove and Feel, and the Decapitator and Little AlterBoy Drive knobs, are unitless
panel scales, not Hz or dB. MicroShift Detune and Delay are relative percentages
(50–200 %, log taper, 100 % at centre), not cents or milliseconds. Little AlterBoy
Pitch and Formant are in semitones; inspect Shift Mode and Formant Link first.

### Pro-Q 4

Profile keys identify fixed host band slots, not whichever band is selected or
leftmost on the graph. Before editing a band, inspect its Used/Enabled state,
shape and dynamics settings. A stored value in an unused band does not imply
audible processing.

Dynamic **Attack and Release are relative percentages**. At 50% they match the
automatic timing; they are not milliseconds. Behavior depends on the signal,
band frequency and dynamic range. The expanded dynamics controls and compatible
band/dynamics mode must actually be active for custom settings to matter.
[FabFilter dynamic EQ manual](https://www.fabfilter.com/help/pro-q/using/dynamic-eq).

### Pro-C 3

**Wet Gain and Dry Gain are independent signal-path gains**, not a generic
wet/dry percentage. Pro-C 3 also has a separate Mix control, which scales its
processing and can exceed 100%. Do not substitute one for the other.
[Time controls](https://www.fabfilter.com/help/pro-c/using/timecontrols),
[input/output options](https://www.fabfilter.com/help/pro-c/using/outputoptions).

Style changes the interpretation of other controls: Vocal chooses ratio and knee
automatically; Vari-Mu's effective ratio depends on its topology/input level;
TTM combines upward and downward multiband processing. With Auto Release enabled,
Release still adjusts the overall auto-release behavior; it is not simply an
inactive knob. Inspect Style, Auto Threshold, Auto Release and Auto Gain before
applying a conventional compressor recipe. Auto Gain is an estimate, not a
measured loudness match.
[Style and character](https://www.fabfilter.com/help/pro-c/using/styleandcharacter),
[time controls](https://www.fabfilter.com/help/pro-c/using/timecontrols).

### Tube-Tech CL 1B mk II

The observed Attack/Release values use the panel's **0..10 scale**, not ms or
seconds. Do not relabel those numbers as physical times. Softube distinguishes
the approximate panel markings from actual timing and documents Fixed, Manual
and combined modes. In combined mode, the Attack control affects when the manual
release takes over, so its role changes. Select and inspect the mode before
interpreting either control; no guessed panel-to-time conversion is supplied.
[Softube CL 1B manual](https://www.softube.com/eu/user-manuals/tube-tech-cl-1b-and-cl-1b-mk-ii-compressors).

### Saturn 2

Drive, Dynamics, band routing and modulation are different operations. Dynamics
toward the positive side compresses; toward the negative side expands/gates.
Feedback can ring, and Unrestricted Feedback can continue oscillating after
input or playback stops. A convenience setting must not enable that mode as an
incidental part of a saturation recipe.
[Band controls](https://www.fabfilter.com/help/saturn/using/bandcontrols),
[output/feedback options](https://www.fabfilter.com/help/saturn/using/inputoutput).

Inspect existing modulation before interpreting a base knob value. Named host
parameter access does not by itself construct or verify Saturn's modulation
sources, slots or routing graph.
[Modulation](https://www.fabfilter.com/help/saturn/using/modulation),
[modulation slots](https://www.fabfilter.com/help/saturn/using/modulationslots).

### Pro-L 2 and literal units

True Peak Limiting and True Peak Metering are separate options. FabFilter uses
`dBTP` for the output ceiling display when true-peak limiting is enabled. Preserve
that distinction from `dB`; a display readback is not an independent test of the
rendered signal, nor proof that the host's cached mode is current. If a mode is
not exposed by the profile, report that limitation and verify it separately.
[FabFilter true-peak manual](https://www.fabfilter.com/help/pro-l/using/truepeaklimiting).

Other observed typography includes Pro-R 2 seconds displayed as `2.500 sec`,
soothe2 frequencies displayed as `2k5 Hz` or `16k Hz`, and Soundtoys spellings
`214.3 msec`, `-12.0 semitones` and `140.0 bpm`. The literal parser accepts these
as 2.5 seconds, 2500 Hz, 16000 Hz and 214.3 ms; `semitones` and `bpm` are separate
units that never convert. It converts only compatible time/frequency units, rejects
ambiguous unitless compact notation, and does not equate dB, dBTP and percent. This parsing support is not proof that every control
using those units has a completed physical calibration.

## What a successful write proves

`verified` means the acknowledged write and two later host readbacks matched the
requested normalized values and any required literal/numeric display checks.
The receipt preserves the request, prior host values and observed outcomes.

FL/plugin parameter displays may be stale after preset loads, and identical host
readbacks can share the same stale cache. They do not establish that DSP consumed
the setting, a band is audible, modulation is inactive, or a mix sounds good.
For those claims, check the actual processing state and listen or render and
measure an appropriate passage. A previous host value is evidence for recovery,
not a promise that blind rollback will restore the audible state.

The calibration harness waited at least 400 ms before the first display read and
required two stable callbacks 200 ms apart. An isolated live Pro-Q write exposed
a stale display when read immediately; the API must report that mismatch rather
than turn normalized agreement into physical-value success. Host display updates
are an active qualification boundary even when the mapping itself passed.
