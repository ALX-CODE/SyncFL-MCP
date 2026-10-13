use std::f64::consts::TAU;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use syncfl_tempo::{analyze, Options};

static NEXT: AtomicUsize = AtomicUsize::new(0);

struct Fixture(PathBuf);

impl Fixture {
    fn new(bytes: &[u8]) -> Self {
        let path = std::env::temp_dir().join(format!(
            "syncfl-tempo-{}-{}.wav",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::write(&path, bytes).unwrap();
        Self(path)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn riff(chunks: &[(&[u8; 4], Vec<u8>)]) -> Vec<u8> {
    let mut payload = Vec::from(*b"WAVE");
    for (name, bytes) in chunks {
        payload.extend(*name);
        payload.extend((bytes.len() as u32).to_le_bytes());
        payload.extend(bytes);
        if bytes.len() % 2 == 1 {
            payload.push(0);
        }
    }
    let mut result = Vec::from(*b"RIFF");
    result.extend((payload.len() as u32).to_le_bytes());
    result.extend(payload);
    result
}

fn wav(
    samples: &[f64],
    rate: u32,
    bits: u16,
    float: bool,
    channels: u16,
    extensible: bool,
) -> Vec<u8> {
    let tag = if float { 3_u16 } else { 1_u16 };
    let mut format = Vec::new();
    format.extend(if extensible { 0xfffe_u16 } else { tag }.to_le_bytes());
    format.extend(channels.to_le_bytes());
    format.extend(rate.to_le_bytes());
    format.extend((rate * u32::from(channels) * u32::from(bits / 8)).to_le_bytes());
    format.extend((channels * (bits / 8)).to_le_bytes());
    format.extend(bits.to_le_bytes());
    if extensible {
        format.extend(22_u16.to_le_bytes());
        format.extend(bits.to_le_bytes());
        format.extend(0_u32.to_le_bytes());
        format.extend(u32::from(tag).to_le_bytes());
        format.extend([0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113]);
    }
    let mut data = Vec::new();
    for sample in samples {
        match (float, bits) {
            (true, 32) => data.extend((*sample as f32).to_le_bytes()),
            (true, 64) => data.extend(sample.to_le_bytes()),
            (false, 8) => data.push((sample * 127.0 + 128.0).round().clamp(0.0, 255.0) as u8),
            (false, 16) => data
                .extend(((sample * 32767.0).round().clamp(-32768.0, 32767.0) as i16).to_le_bytes()),
            (false, 24) => data.extend_from_slice(
                &((sample * 8_388_607.0)
                    .round()
                    .clamp(-8_388_608.0, 8_388_607.0) as i32)
                    .to_le_bytes()[..3],
            ),
            (false, 32) => data.extend(
                ((sample * 2_147_483_647.0)
                    .round()
                    .clamp(-2_147_483_648.0, 2_147_483_647.0) as i32)
                    .to_le_bytes(),
            ),
            _ => panic!("Test fixture requested unsupported format"),
        }
    }
    riff(&[(b"JUNK", vec![1, 2, 3]), (b"fmt ", format), (b"data", data)])
}

fn click_train(bpm: f64, rate: u32, duration: f64, syncopated: bool) -> Vec<f64> {
    let mut result = vec![0.0; (f64::from(rate) * duration).round() as usize];
    let spacing = f64::from(rate) * 60.0 / bpm;
    let mut beat = 0;
    while 0.23 * f64::from(rate) + beat as f64 * spacing < result.len() as f64 {
        let start = (0.23 * f64::from(rate) + beat as f64 * spacing).round() as usize;
        let amplitude = if syncopated && beat % 4 != 0 {
            0.45
        } else {
            0.8
        };
        add_click(&mut result, rate, start, amplitude);
        if syncopated && beat % 4 == 1 {
            add_click(
                &mut result,
                rate,
                (start as f64 + spacing * 0.5).round() as usize,
                0.22,
            );
        }
        beat += 1;
    }
    result
}

fn add_click(result: &mut [f64], rate: u32, start: usize, amplitude: f64) {
    for offset in 0..(f64::from(rate) * 0.035) as usize {
        if start + offset >= result.len() {
            break;
        }
        let time = offset as f64 / f64::from(rate);
        result[start + offset] += amplitude
            * (-time * 155.0).exp()
            * ((TAU * 1733.0 * time).cos() * 0.65 + (TAU * 367.0 * time).cos() * 0.35);
    }
}

fn assert_tempo(samples: &[f64], rate: u32, bpm: f64) -> syncfl_tempo::Analysis {
    let file = Fixture::new(&wav(samples, rate, 32, true, 1, false));
    let result = analyze(&file.0, Options::default()).unwrap();
    assert_eq!(result.status, "ok", "{bpm}: {result:?}");
    assert!(
        (result.bpm().unwrap() - bpm).abs() < 0.5,
        "expected {bpm}: {result:?}"
    );
    result
}

#[test]
fn click_tempos_across_range_and_fractional_values() {
    for bpm in [
        40.0, 60.0, 75.0, 90.0, 120.0, 123.45, 128.0, 140.0, 174.0, 200.0, 239.0,
    ] {
        assert_tempo(&click_train(bpm, 16_000, 24.0, false), 16_000, bpm);
    }
}

#[test]
fn sample_rates_and_syncopation() {
    for rate in [8000, 22_050, 44_100, 48_000, 96_000] {
        assert_tempo(&click_train(123.45, rate, 15.0, true), rate, 123.45);
    }
}

#[test]
fn all_encodings_and_extensible_headers() {
    let samples = click_train(128.0, 8000, 15.0, false);
    for (float, bits) in [
        (false, 8),
        (false, 16),
        (false, 24),
        (false, 32),
        (true, 32),
        (true, 64),
    ] {
        for extensible in [false, true] {
            let file = Fixture::new(&wav(&samples, 8000, bits, float, 1, extensible));
            let before = fs::read(&file.0).unwrap();
            let result = analyze(&file.0, Options::default()).unwrap();
            assert!(
                (result.bpm().unwrap() - 128.0).abs() < 0.5,
                "{float}/{bits}/{extensible}: {result:?}"
            );
            assert_eq!(before, fs::read(&file.0).unwrap());
        }
    }
}

#[test]
fn opposite_phase_channels_do_not_cancel() {
    let mono = click_train(140.0, 16_000, 15.0, false);
    let stereo: Vec<_> = mono.iter().flat_map(|value| [*value, -*value]).collect();
    let file = Fixture::new(&wav(&stereo, 16_000, 32, true, 2, true));
    let result = analyze(&file.0, Options::default()).unwrap();
    assert!((result.bpm().unwrap() - 140.0).abs() < 0.5, "{result:?}");
}

#[test]
fn silence_dc_noise_and_tone_have_no_claimed_pulse() {
    let rate = 8000;
    let mut random = 123456789_u64;
    let noise: Vec<_> = (0..rate * 20)
        .map(|_| {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            (random as f64 / u64::MAX as f64 - 0.5) * 0.7
        })
        .collect();
    let tone: Vec<_> = (0..rate * 20)
        .map(|index| (TAU * 440.0 * f64::from(index) / f64::from(rate)).sin() * 0.7)
        .collect();
    for samples in [
        vec![0.0; rate as usize * 20],
        vec![0.6; rate as usize * 20],
        noise,
        tone,
    ] {
        let file = Fixture::new(&wav(&samples, rate, 32, true, 1, false));
        let result = analyze(&file.0, Options::default()).unwrap();
        assert_eq!(result.status, "no_periodic_pulse", "{result:?}");
        assert!(result.candidates.is_empty());
    }
}

#[test]
fn short_and_single_onset_are_not_tempo() {
    for (duration, expected) in [(2.0, "insufficient_audio"), (8.0, "no_periodic_pulse")] {
        let mut samples = vec![0.0; (duration * 8000.0) as usize];
        add_click(&mut samples, 8000, 4000, 0.8);
        let file = Fixture::new(&wav(&samples, 8000, 16, false, 1, false));
        let result = analyze(&file.0, Options::default()).unwrap();
        assert_eq!(result.status, expected, "{result:?}");
        assert_eq!(result.bpm(), None);
    }
}

#[test]
fn preserves_slow_alias_and_probability_contract() {
    let result = assert_tempo(&click_train(120.0, 8000, 25.0, false), 8000, 120.0);
    assert!(
        result
            .candidates
            .iter()
            .any(|item| (item.bpm - 60.0).abs() < 0.5 && item.octave_relation == "half"),
        "{result:?}"
    );
    assert!(
        (result
            .candidates
            .iter()
            .map(|item| item.confidence)
            .sum::<f64>()
            - 1.0)
            .abs()
            < 1e-12
    );
    for candidate in result.candidates {
        assert!(candidate.bpm.is_finite());
        for value in [
            candidate.score,
            candidate.confidence,
            candidate.periodicity,
            candidate.window_consistency,
        ] {
            assert!((0.0..=1.0).contains(&value));
        }
    }
}

#[test]
fn rejects_nonfinite_samples() {
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 1e101] {
        let file = Fixture::new(&wav(&[0.0, value], 8000, 64, true, 1, true));
        assert_eq!(
            analyze(&file.0, Options::default()).unwrap_err().code,
            "invalid_samples"
        );
    }
}

#[test]
fn malformed_headers_and_truncation_are_errors() {
    let valid = wav(&[0.0; 80], 8000, 16, false, 1, false);
    let format_offset = 32;
    for offset in [
        0,
        4,
        8,
        format_offset,
        format_offset + 2,
        format_offset + 4,
        format_offset + 8,
        format_offset + 12,
        format_offset + 14,
    ] {
        let mut mutated = valid.clone();
        mutated[offset] ^= 0x5f;
        let file = Fixture::new(&mutated);
        assert!(
            analyze(&file.0, Options::default()).is_err(),
            "offset {offset}"
        );
    }
    for length in [0, 1, 11, 16, valid.len() - 1] {
        let file = Fixture::new(&valid[..length]);
        assert!(analyze(&file.0, Options::default()).is_err());
    }
    let format = valid[format_offset..format_offset + 16].to_vec();
    let cases = [
        riff(&[(b"data", vec![0; 4])]),
        riff(&[(b"fmt ", format.clone())]),
        riff(&[
            (b"fmt ", format.clone()),
            (b"fmt ", format.clone()),
            (b"data", vec![0; 4]),
        ]),
        riff(&[
            (b"fmt ", format.clone()),
            (b"data", vec![0; 4]),
            (b"data", vec![0; 4]),
        ]),
        riff(&[(b"fmt ", format), (b"data", vec![0; 3])]),
    ];
    for bytes in cases {
        let file = Fixture::new(&bytes);
        assert!(analyze(&file.0, Options::default()).is_err());
    }
}

#[test]
fn long_input_reports_bounded_prefix() {
    let samples = click_train(128.0, 4000, 181.0, false);
    let file = Fixture::new(&wav(&samples, 4000, 16, false, 1, false));
    let result = analyze(&file.0, Options::default()).unwrap();
    assert_eq!(result.duration_seconds, 181.0);
    assert_eq!(result.analyzed_duration_seconds, 180.0);
    assert!(result.truncated);
    assert!((result.bpm().unwrap() - 128.0).abs() < 0.5);
}

#[test]
fn cli_emits_structured_results_and_errors() {
    let binary = env!("CARGO_BIN_EXE_syncfl-tempo");
    let version = std::process::Command::new(binary)
        .arg("--version")
        .output()
        .unwrap();
    assert!(version.status.success());
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().trim(),
        "syncfl-tempo 0.1.0"
    );
    let file = Fixture::new(&wav(
        &click_train(90.0, 8000, 12.0, false),
        8000,
        16,
        false,
        1,
        false,
    ));
    let output = std::process::Command::new(binary)
        .args(["analyze", "--input"])
        .arg(&file.0)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let json = String::from_utf8(output.stdout).unwrap();
    assert!(json.starts_with("{\"schema_version\":1,"));
    assert!(json.contains("\"status\":\"ok\""));
    assert!(!json.contains(&file.0.to_string_lossy().to_string()));
    for arguments in [
        vec!["analyze"],
        vec!["analyze", "--input", "missing.wav"],
        vec!["analyze", "--input", "missing.wav", "--top-n", "-1"],
    ] {
        let output = std::process::Command::new(binary)
            .args(arguments)
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8(output.stderr)
            .unwrap()
            .contains("\"error\":{"));
    }
}

#[test]
fn levels_and_polarity_preserve_tempo() {
    let original = click_train(123.45, 16_000, 18.0, false);
    for gain in [1e-30, -0.1, 1e30] {
        let scaled: Vec<_> = original.iter().map(|sample| sample * gain).collect();
        assert_tempo(&scaled, 16_000, 123.45);
    }
}

#[test]
fn several_stationary_tones_do_not_create_a_tempo() {
    for frequency in [20.0, 40.0, 55.0, 110.0, 1000.0, 3000.0] {
        let samples: Vec<_> = (0..8000 * 12)
            .map(|index| (TAU * frequency * index as f64 / 8000.0).sin() * 0.5)
            .collect();
        let file = Fixture::new(&wav(&samples, 8000, 32, true, 1, false));
        let result = analyze(&file.0, Options::default()).unwrap();
        assert_eq!(
            result.status, "no_periodic_pulse",
            "{frequency}: {result:?}"
        );
    }
}

#[test]
fn strong_pulses_survive_quiet_tone_and_noise() {
    let rate = 16000;
    let mut samples = click_train(174.0, rate, 20.0, true);
    let mut rng = 83470_u64;
    for (index, sample) in samples.iter_mut().enumerate() {
        rng ^= rng << 13;
        rng ^= rng >> 7;
        rng ^= rng << 17;
        *sample += 0.08 * (TAU * 220.0 * index as f64 / f64::from(rate)).sin()
            + (rng as f64 / u64::MAX as f64 - 0.5) * 0.015;
    }
    assert_tempo(&samples, rate, 174.0);
}

#[test]
fn alternating_timbres_at_constant_level_are_still_rhythmic() {
    let rate = 8000;
    let samples: Vec<_> = (0..rate * 18)
        .map(|index| {
            let frequency = if (index / (rate / 2)) % 2 == 0 {
                440.0
            } else {
                880.0
            };
            (TAU * frequency * f64::from(index) / f64::from(rate)).sin() * 0.5
        })
        .collect();
    assert_tempo(&samples, rate, 120.0);
}

#[test]
fn mixed_kick_snare_hats_and_bass_do_not_favor_bar_length() {
    for (bpm, half_time) in [(96.0, false), (123.45, false), (140.0, true), (174.0, true)] {
        let rate = 22_050;
        let mut samples = vec![0.0; (32.0 * 60.0 / bpm * f64::from(rate)).ceil() as usize];
        let mut rng = 89123_u64;
        let mut random = || {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            rng as f64 / u64::MAX as f64 * 2.0 - 1.0
        };
        let kick: Vec<_> = (0..(0.16 * f64::from(rate)) as usize)
            .map(|index| {
                let t = index as f64 / f64::from(rate);
                (TAU * (48.0 * t + 75.0 * 0.018 * (1.0 - (-t / 0.018).exp()))).sin()
                    * (-t / 0.04).exp()
                    * 0.8
            })
            .collect();
        let snare: Vec<_> = (0..kick.len())
            .map(|index| random() * (-(index as f64 / f64::from(rate)) / 0.025).exp() * 0.7)
            .collect();
        let hat: Vec<_> = (0..(0.04 * f64::from(rate)) as usize)
            .map(|index| random() * (-(index as f64 / f64::from(rate)) / 0.009).exp())
            .collect();
        let mut add = |beat: f64, sound: &[f64], gain: f64| {
            let start = (beat * 60.0 / bpm * f64::from(rate)).round() as usize;
            for (target, value) in samples.iter_mut().skip(start).zip(sound) {
                *target += value * gain;
            }
        };
        for beat in 0..32 {
            let meter = if half_time { 4 } else { 2 };
            if beat % meter == 0 {
                add(f64::from(beat), &kick, 1.0);
            }
            if beat % meter == meter / 2 {
                add(f64::from(beat), &snare, 1.0);
            }
            add(f64::from(beat), &hat, 0.13);
            add(f64::from(beat) + 0.5, &hat, 0.075);
            if half_time && beat % 4 == 3 {
                add(f64::from(beat) + 0.75, &hat, 0.06);
            }
            if beat % 2 == 0 {
                let root = 55.0 * 2_f64.powf([0.0, 5.0, 7.0, 0.0][(beat / 8) as usize] / 12.0);
                let bass: Vec<_> = (0..(0.8 * 60.0 / bpm * f64::from(rate)) as usize)
                    .map(|index| {
                        let t = index as f64 / f64::from(rate);
                        (TAU * root * t).sin() * (t / 0.01).min(1.0) * (-t / 0.4).exp()
                    })
                    .collect();
                add(f64::from(beat), &bass, 0.4);
            }
        }
        let file = Fixture::new(&wav(&samples, rate, 32, true, 1, false));
        let result = analyze(&file.0, Options::default()).unwrap();
        if half_time {
            assert!(
                result
                    .candidates
                    .iter()
                    .any(|candidate| (candidate.bpm - bpm).abs() < 0.5),
                "{bpm}: {result:?}"
            );
            // These deliberately weak hats do not uniquely identify the
            // intended metrical level. Retain it while accepting half-time as
            // primary; do not require arbitrary top-two certainty.
            assert!(
                (result.bpm().unwrap() - bpm).abs() < 0.5
                    || (result.bpm().unwrap() - bpm / 2.0).abs() < 0.5,
                "{bpm}: {result:?}"
            );
        } else {
            assert!(
                (result.bpm().unwrap() - bpm).abs() < 0.5,
                "{bpm}: {result:?}"
            );
        }
    }
}
