use crate::{Candidate, Options};

fn correlation(values: &[f64], lag: usize) -> f64 {
    if lag == 0 || lag >= values.len() {
        return 0.0;
    }
    let mut cross = 0.0;
    let mut left = 0.0;
    let mut right = 0.0;
    for index in lag..values.len() {
        cross += values[index] * values[index - lag];
        left += values[index] * values[index];
        right += values[index - lag] * values[index - lag];
    }
    if left <= 0.0 || right <= 0.0 {
        0.0
    } else {
        (cross / (left * right).sqrt()).clamp(-1.0, 1.0)
    }
}

fn interpolate(values: &[f64], index: f64) -> f64 {
    let floor = index.floor() as usize;
    if floor + 1 >= values.len() {
        return values.get(floor).copied().unwrap_or(0.0);
    }
    values[floor] * (1.0 - index.fract()) + values[floor + 1] * index.fract()
}

fn peak_position(values: &[f64], index: usize) -> f64 {
    if index == 0 || index + 1 >= values.len() {
        return index as f64;
    }
    let curvature = values[index - 1] - 2.0 * values[index] + values[index + 1];
    if curvature >= -1e-12 {
        return index as f64;
    }
    index as f64 + (0.5 * (values[index - 1] - values[index + 1]) / curvature).clamp(-0.5, 0.5)
}

fn centered_envelope(input: &[f64], rate: f64) -> Vec<f64> {
    let peak = input.iter().copied().fold(0.0, f64::max);
    if peak <= 0.0 || !peak.is_finite() {
        return vec![0.0; input.len()];
    }
    let normalized: Vec<_> = input.iter().map(|value| value / peak).collect();
    let radius = (rate * 0.20).ceil() as usize;
    let mut sum = vec![0.0; input.len() + 1];
    for (index, value) in normalized.iter().enumerate() {
        sum[index + 1] = sum[index] + value;
    }
    let mut filtered: Vec<_> = normalized
        .iter()
        .enumerate()
        .map(|(index, &value)| {
            let left = index.saturating_sub(radius);
            let right = (index + radius + 1).min(input.len());
            (value - (sum[right] - sum[left]) / (right - left) as f64)
                .max(0.0)
                .sqrt()
        })
        .collect();
    let mean = filtered.iter().sum::<f64>() / filtered.len().max(1) as f64;
    for value in &mut filtered {
        *value -= mean;
    }
    filtered
}

/// Evaluate independent, overlapping windows as a guard against a single lucky
/// whole-file autocorrelation peak. No trained prior or preferred BPM is used.
fn consistency(values: &[f64], period: f64) -> f64 {
    let width = (values.len() / 2)
        .max((period * 6.0).ceil() as usize)
        .min(values.len());
    if width < (period * 3.0) as usize {
        return 0.0;
    }
    let starts = [0, (values.len() - width) / 2, values.len() - width];
    let lag = period.round() as usize;
    let mut sum = 0.0;
    for start in starts {
        let window = &values[start..start + width];
        let best = lag.saturating_sub(1)..=lag + 1;
        sum += best
            .map(|offset| correlation(window, offset))
            .fold(0.0, f64::max);
    }
    sum / 3.0
}

fn refined_period(acf: &[f64], initial: f64) -> f64 {
    // Multiple pulse separations reduce the integer-lag quantization error.
    let mut weighted_period = 0.0;
    let mut weights = 0.0;
    for multiple in 1..=8 {
        let predicted = initial * multiple as f64;
        if predicted + 3.0 >= acf.len() as f64 {
            break;
        }
        let center = predicted.round() as usize;
        let radius = (multiple as f64 * 0.6).ceil() as usize + 1;
        let left = center.saturating_sub(radius).max(1);
        let right = (center + radius).min(acf.len() - 2);
        let peak = (left..=right)
            .max_by(|&a, &b| acf[a].total_cmp(&acf[b]))
            .unwrap_or(center);
        let period = peak_position(acf, peak) / multiple as f64;
        if (period / initial - 1.0).abs() > 0.04 {
            continue;
        }
        let weight = acf[peak].max(0.0).powi(2) * multiple as f64;
        weighted_period += period * weight;
        weights += weight;
    }
    if weights > 0.0 {
        weighted_period / weights
    } else {
        initial
    }
}

pub(crate) fn estimate(input: &[f64], rate: f64, options: Options) -> Vec<Candidate> {
    if input.len() < 8 {
        return Vec::new();
    }
    let values = centered_envelope(input, rate);
    let energy = values.iter().map(|value| value * value).sum::<f64>();
    if energy < 1e-12 {
        return Vec::new();
    }
    let min_lag = (60.0 * rate / options.max_bpm).floor().max(2.0) as usize;
    let max_lag = (60.0 * rate / options.min_bpm).ceil() as usize;
    let acf_max = (max_lag * 8 + 3).min(values.len() / 2);
    if acf_max <= min_lag + 1 {
        return Vec::new();
    }
    let acf: Vec<_> = (0..=acf_max).map(|lag| correlation(&values, lag)).collect();
    let mut candidates = Vec::new();
    for lag in min_lag..=max_lag.min(acf.len() - 2) {
        if acf[lag] < 0.20 || acf[lag] < acf[lag - 1] || acf[lag] < acf[lag + 1] {
            continue;
        }
        let initial = peak_position(&acf, lag);
        let period = refined_period(&acf, initial);
        let estimated_bpm = 60.0 * rate / period;
        // Sampling may put a boundary tempo a fraction of a BPM outside the
        // requested interval. Keep that estimate at the boundary, not its alias.
        if estimated_bpm < options.min_bpm - 0.1
            || estimated_bpm > options.max_bpm + 0.1
            || values.len() as f64 / period < 3.0
        {
            continue;
        }
        let bpm = estimated_bpm.clamp(options.min_bpm, options.max_bpm);
        let periodicity = interpolate(&acf, period).clamp(0.0, 1.0);
        let window_consistency = consistency(&values, period).clamp(0.0, 1.0);
        if periodicity < 0.28 || window_consistency < 0.25 {
            continue;
        }
        let twice = interpolate(&acf, period * 2.0).max(0.0);
        let thrice = interpolate(&acf, period * 3.0).max(0.0);
        // A slower interpretation is retained but downweighted when there is a
        // strong, regular pulse halfway/thirdway through every claimed beat.
        let subdivision = 0.22 * interpolate(&acf, period / 2.0).max(0.0)
            + 0.22 * interpolate(&acf, period / 3.0).max(0.0)
            + 0.18 * interpolate(&acf, period / 4.0).max(0.0)
            + 0.14 * interpolate(&acf, period / 5.0).max(0.0)
            + 0.12 * interpolate(&acf, period / 6.0).max(0.0)
            + 0.10 * interpolate(&acf, period / 7.0).max(0.0)
            + 0.10 * interpolate(&acf, period / 8.0).max(0.0);
        let score = (0.62 * periodicity + 0.22 * window_consistency + 0.10 * twice + 0.06 * thrice
            - subdivision)
            .clamp(0.0, 1.0);
        candidates.push(Candidate {
            bpm,
            score,
            confidence: 0.0,
            periodicity,
            window_consistency,
            octave_relation: "other",
        });
    }
    candidates.sort_by(|a, b| b.score.total_cmp(&a.score).then(a.bpm.total_cmp(&b.bpm)));
    // Suppress duplicate estimates of the same broad correlation peak.
    let mut unique: Vec<Candidate> = Vec::new();
    for candidate in candidates {
        if unique
            .iter()
            .any(|accepted| (accepted.bpm / candidate.bpm - 1.0).abs() < 0.015)
        {
            continue;
        }
        unique.push(candidate);
        if unique.len() == options.top_n {
            break;
        }
    }
    let Some(best) = unique.first() else {
        return unique;
    };
    if best.score < 0.30 {
        return Vec::new();
    }
    let best_score = best.score;
    let best_bpm = best.bpm;
    let denominator: f64 = unique
        .iter()
        .map(|candidate| ((candidate.score - best_score) / 0.12).exp())
        .sum();
    for (index, candidate) in unique.iter_mut().enumerate() {
        candidate.confidence = ((candidate.score - best_score) / 0.12).exp() / denominator;
        candidate.octave_relation = if index == 0 {
            "primary"
        } else if (candidate.bpm / best_bpm - 0.5).abs() < 0.025 {
            "half"
        } else if (candidate.bpm / best_bpm - 2.0).abs() < 0.10 {
            "double"
        } else {
            "other"
        };
    }
    unique
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_envelope_pulses() {
        let rate = 100.0;
        let mut pulses = vec![0.0; 3000];
        for index in (20..pulses.len()).step_by(50) {
            pulses[index] = 1.0;
        }
        let candidates = estimate(&pulses, rate, Options::default());
        assert!((candidates[0].bpm - 120.0).abs() < 0.01, "{candidates:?}");
    }
}
