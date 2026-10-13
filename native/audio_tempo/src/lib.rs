//! Original, dependency-free WAV tempo estimation for SyncFL.
//!
//! This estimates a global periodic pulse, not beat positions or a time signature.
//! Half-/double-time interpretations remain musically ambiguous. Spectral flux is
//! a standard onset feature: see Dixon, "Onset Detection Revisited", DAFx 2006,
//! https://www.dafx.de/paper-archive/2006/papers/p_133.pdf . This implementation,
//! normalization, candidate scoring, and validation are independently written.

mod spectrum;
mod tempo;
mod wav;

use std::fmt;
use std::path::Path;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const SCHEMA_VERSION: u32 = 1;
pub const MAX_ANALYSIS_SECONDS: u32 = 180;
pub const METHOD: &str = "spectral_flux_autocorrelation_v1";
pub const CONFIDENCE_BASIS: &str = "relative_candidate_softmax_not_calibrated_probability";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub code: &'static str,
    pub message: String,
}

impl Error {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub fn to_json(&self) -> String {
        format!(
            "{{\"schema_version\":{SCHEMA_VERSION},\"error\":{{\"code\":{},\"message\":{}}}}}",
            json_string(self.code),
            json_string(&self.message)
        )
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for Error {}

#[derive(Debug, Clone, Copy)]
pub struct Options {
    pub min_bpm: f64,
    pub max_bpm: f64,
    pub top_n: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            min_bpm: 40.0,
            max_bpm: 240.0,
            top_n: 5,
        }
    }
}

impl Options {
    pub fn validate(&self) -> Result<(), Error> {
        if !self.min_bpm.is_finite()
            || !self.max_bpm.is_finite()
            || self.min_bpm < 20.0
            || self.max_bpm > 400.0
            || self.min_bpm >= self.max_bpm
        {
            return Err(Error::new(
                "invalid_options",
                "Require 20 <= min_bpm < max_bpm <= 400",
            ));
        }
        if !(1..=10).contains(&self.top_n) {
            return Err(Error::new(
                "invalid_options",
                "top_n must be between 1 and 10",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    pub bpm: f64,
    /// Ranking strength in [0, 1], not accuracy probability.
    pub score: f64,
    /// Relative softmax weight over the returned candidates.
    pub confidence: f64,
    /// Normalized onset autocorrelation in [0, 1].
    pub periodicity: f64,
    /// Agreement across overlapping analysis windows in [0, 1].
    pub window_consistency: f64,
    /// Relation to the first candidate: "primary", "half", "double", "other".
    pub octave_relation: &'static str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Analysis {
    /// "ok", "no_periodic_pulse", or "insufficient_audio".
    pub status: &'static str,
    pub candidates: Vec<Candidate>,
    pub duration_seconds: f64,
    pub analyzed_duration_seconds: f64,
    pub truncated: bool,
    pub sample_rate: u32,
    pub channels: u16,
}

impl Analysis {
    pub fn bpm(&self) -> Option<f64> {
        self.candidates.first().map(|candidate| candidate.bpm)
    }

    /// Stable schema. Numeric output is finite; no source path is emitted.
    pub fn to_json(&self) -> String {
        use std::fmt::Write;
        let mut candidates = String::new();
        for (index, candidate) in self.candidates.iter().enumerate() {
            if index != 0 {
                candidates.push(',');
            }
            let _ = write!(candidates, "{{\"bpm\":{:.8},\"score\":{:.8},\"confidence\":{:.8},\"periodicity\":{:.8},\"window_consistency\":{:.8},\"octave_relation\":{}}}", candidate.bpm, candidate.score, candidate.confidence, candidate.periodicity, candidate.window_consistency, json_string(candidate.octave_relation));
        }
        let bpm = self
            .bpm()
            .map_or_else(|| "null".to_owned(), |value| format!("{value:.8}"));
        format!("{{\"schema_version\":{SCHEMA_VERSION},\"status\":{},\"bpm\":{bpm},\"candidates\":[{candidates}],\"duration_seconds\":{:.8},\"analyzed_duration_seconds\":{:.8},\"truncated\":{},\"sample_rate\":{},\"channels\":{},\"method\":{},\"confidence_basis\":{},\"max_analysis_seconds\":{MAX_ANALYSIS_SECONDS}}}", json_string(self.status), self.duration_seconds, self.analyzed_duration_seconds, self.truncated, self.sample_rate, self.channels, json_string(METHOD), json_string(CONFIDENCE_BASIS))
    }
}

pub fn analyze(path: impl AsRef<Path>, options: Options) -> Result<Analysis, Error> {
    options.validate()?;
    let mut reader = wav::Reader::open(path.as_ref())?;
    let header = reader.header;
    let total_frames = header.data_bytes / u64::from(header.block_align);
    let frames_to_analyze =
        total_frames.min(u64::from(header.sample_rate) * u64::from(MAX_ANALYSIS_SECONDS));
    let (envelope, envelope_rate) = spectrum::onset_envelope(&mut reader, frames_to_analyze)?;
    reader.verify_unchanged()?;
    let duration = frames_to_analyze as f64 / f64::from(header.sample_rate);
    let candidates = if duration >= 3.0 {
        tempo::estimate(&envelope, envelope_rate, options)
    } else {
        Vec::new()
    };
    let status = if duration < 3.0 {
        "insufficient_audio"
    } else if candidates.is_empty() {
        "no_periodic_pulse"
    } else {
        "ok"
    };
    Ok(Analysis {
        status,
        candidates,
        duration_seconds: total_frames as f64 / f64::from(header.sample_rate),
        analyzed_duration_seconds: duration,
        truncated: frames_to_analyze < total_frames,
        sample_rate: header.sample_rate,
        channels: header.channels,
    })
}

fn json_string(value: &str) -> String {
    use std::fmt::Write;
    let mut result = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            ch if ch < '\u{20}' => {
                let _ = write!(result, "\\u{:04x}", u32::from(ch));
            }
            ch => result.push(ch),
        }
    }
    result.push('"');
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn options_are_bounded() {
        for options in [
            Options {
                min_bpm: f64::NAN,
                ..Options::default()
            },
            Options {
                max_bpm: f64::INFINITY,
                ..Options::default()
            },
            Options {
                min_bpm: 10.0,
                ..Options::default()
            },
            Options {
                min_bpm: 240.0,
                ..Options::default()
            },
            Options {
                max_bpm: 401.0,
                ..Options::default()
            },
            Options {
                top_n: 0,
                ..Options::default()
            },
            Options {
                top_n: 11,
                ..Options::default()
            },
        ] {
            assert!(options.validate().is_err());
        }
    }

    #[test]
    fn errors_escape_json() {
        assert_eq!(json_string("a\"\\\n\u{1}"), "\"a\\\"\\\\\\n\\u0001\"");
    }
}
