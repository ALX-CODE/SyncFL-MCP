use crate::wav::Reader;
use crate::Error;
use std::f64::consts::TAU;

#[derive(Clone, Copy, Default)]
struct Complex {
    re: f64,
    im: f64,
}

struct Fft {
    reversal: Vec<usize>,
    roots: Vec<Complex>,
}

impl Fft {
    fn new(size: usize) -> Self {
        let shift = usize::BITS - size.trailing_zeros();
        let reversal = (0..size)
            .map(|index| index.reverse_bits() >> shift)
            .collect();
        let roots = (0..size / 2)
            .map(|index| {
                let angle = -TAU * index as f64 / size as f64;
                Complex {
                    re: angle.cos(),
                    im: angle.sin(),
                }
            })
            .collect();
        Self { reversal, roots }
    }

    fn transform(&self, values: &mut [Complex]) {
        for (index, &reversed) in self.reversal.iter().enumerate() {
            if index < reversed {
                values.swap(index, reversed);
            }
        }
        let mut size = 2;
        while size <= values.len() {
            let stride = values.len() / size;
            for start in (0..values.len()).step_by(size) {
                for index in 0..size / 2 {
                    let a = values[start + index];
                    let b = values[start + index + size / 2];
                    let root = self.roots[index * stride];
                    let re = b.re * root.re - b.im * root.im;
                    let im = b.re * root.im + b.im * root.re;
                    values[start + index] = Complex {
                        re: a.re + re,
                        im: a.im + im,
                    };
                    values[start + index + size / 2] = Complex {
                        re: a.re - re,
                        im: a.im - im,
                    };
                }
            }
            size *= 2;
        }
    }
}

/// Streaming box-decimation bounds cost at high input sample rates. Tempo only
/// uses a broad onset envelope; this is not an audio resampler or output effect.
/// Each channel is transformed independently, then magnitudes are pooled so
/// opposite polarity channels cannot erase an onset.
pub(crate) fn onset_envelope(
    reader: &mut Reader,
    max_frames: u64,
) -> Result<(Vec<f64>, f64), Error> {
    let header = reader.header;
    let decimation = header.sample_rate.div_ceil(22_050) as usize;
    let analysis_rate = f64::from(header.sample_rate) / decimation as f64;
    let size = ((analysis_rate * 0.04644).ceil() as usize)
        .next_power_of_two()
        .max(256);
    let hop = size / 4;
    let channels = usize::from(header.channels);
    let fft = Fft::new(size);
    let window: Vec<_> = (0..size)
        .map(|index| 0.5 - 0.5 * (TAU * index as f64 / (size - 1) as f64).cos())
        .collect();
    let mut samples = vec![vec![0.0; size]; channels];
    let mut scratch = vec![Complex::default(); size];
    let mut previous = vec![0.0; size / 2 + 1];
    let mut current = previous.clone();
    let mut group = vec![0.0; channels];
    let mut group_count = 0;
    let mut filled = 0;
    let mut consumed = 0;
    let mut input = Vec::new();
    let mut envelope = Vec::new();
    let mut total_magnitude = 0.0;
    let mut total_flux = 0.0;
    let mut dominant_min = usize::MAX;
    let mut dominant_max = 0;
    let mut concentration_sum = 0.0;
    let mut energy_sum = vec![0.0; channels];
    let mut sample_sum = vec![0.0; channels];
    let energy_block = (analysis_rate / 10.0).round() as usize;
    let mut energy_count = 0;
    let mut energy_envelope = Vec::new();
    let low_bin = (35.0 * size as f64 / analysis_rate).ceil() as usize;
    let high_bin = ((10_000.0 * size as f64 / analysis_rate).floor() as usize).min(size / 2);
    while consumed < max_frames {
        let count = reader.read_frames((max_frames - consumed).min(4096) as usize, &mut input)?;
        if count == 0 {
            return Err(Error::new(
                "invalid_wav",
                "WAV ended before its declared data length",
            ));
        }
        consumed += count as u64;
        for frame in input.chunks_exact(channels) {
            for (sum, value) in group.iter_mut().zip(frame) {
                *sum += *value / decimation as f64;
            }
            group_count += 1;
            if group_count < decimation {
                continue;
            }
            for (channel, sum) in samples.iter_mut().zip(&mut group) {
                channel[filled] = *sum;
                *sum = 0.0;
            }
            for (index, channel) in samples.iter().enumerate() {
                let value = channel[filled];
                sample_sum[index] += value;
                energy_sum[index] += value * value;
            }
            energy_count += 1;
            if energy_count == energy_block {
                let mut variance = 0.0;
                for index in 0..channels {
                    let mean = sample_sum[index] / energy_count as f64;
                    variance += (energy_sum[index] / energy_count as f64 - mean * mean).max(0.0);
                    sample_sum[index] = 0.0;
                    energy_sum[index] = 0.0;
                }
                energy_envelope.push(variance.sqrt());
                energy_count = 0;
            }
            group_count = 0;
            filled += 1;
            if filled < size {
                continue;
            }

            current.fill(0.0);
            for channel in &samples {
                let mean = channel.iter().sum::<f64>() / size as f64;
                for (index, value) in scratch.iter_mut().enumerate() {
                    *value = Complex {
                        re: (channel[index] - mean) * window[index],
                        im: 0.0,
                    };
                }
                fft.transform(&mut scratch);
                for index in 0..=size / 2 {
                    current[index] += scratch[index].re.hypot(scratch[index].im);
                }
            }
            let dominant = (0..current.len())
                .max_by(|&a, &b| current[a].total_cmp(&current[b]))
                .unwrap_or(0);
            dominant_min = dominant_min.min(dominant);
            dominant_max = dominant_max.max(dominant);
            let total_power = current.iter().map(|value| value * value).sum::<f64>();
            if total_power > 0.0 {
                let left = dominant.saturating_sub(2);
                let right = (dominant + 3).min(current.len());
                concentration_sum += current[left..right]
                    .iter()
                    .map(|value| value * value)
                    .sum::<f64>()
                    / total_power;
            }
            let flux = current[low_bin..=high_bin]
                .iter()
                .zip(&previous[low_bin..=high_bin])
                .map(|(now, before)| (now - before).max(0.0))
                .sum::<f64>();
            if envelope.is_empty() {
                envelope.push(0.0);
            } else {
                envelope.push(flux);
                total_flux += flux;
                total_magnitude += current[low_bin..=high_bin].iter().sum::<f64>();
            }
            previous.copy_from_slice(&current);
            for channel in &mut samples {
                channel.copy_within(hop.., 0);
            }
            filled = size - hop;
        }
    }
    // Constant tones/DC have no useful transients. Avoid magnifying numerical
    // leakage into a convincing tempo when the envelope is normalized later.
    let energy_peak = energy_envelope.iter().copied().fold(0.0, f64::max);
    let energy_variation = if energy_peak > 0.0 && !energy_envelope.is_empty() {
        let mean = energy_envelope
            .iter()
            .map(|value| value / energy_peak)
            .sum::<f64>()
            / energy_envelope.len() as f64;
        let variance = energy_envelope
            .iter()
            .map(|value| (value / energy_peak - mean).powi(2))
            .sum::<f64>()
            / energy_envelope.len() as f64;
        variance.sqrt() / mean.max(1e-15)
    } else {
        0.0
    };
    let stationary_tone = energy_variation < 0.015
        && dominant_max.saturating_sub(dominant_min) <= 1
        && concentration_sum / envelope.len().max(1) as f64 > 0.9;
    if total_magnitude == 0.0 || total_flux / total_magnitude < 1e-4 || stationary_tone {
        envelope.fill(0.0);
    }
    Ok((envelope, analysis_rate / hop as f64))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fft_matches_independent_dft() {
        let size = 32;
        let input: Vec<_> = (0..size)
            .map(|index| (index as f64 * 0.37).sin() + index as f64 / 20.0)
            .collect();
        let mut actual: Vec<_> = input.iter().map(|&re| Complex { re, im: 0.0 }).collect();
        Fft::new(size).transform(&mut actual);
        for (bin, transformed) in actual.iter().enumerate() {
            let expected_re: f64 = input
                .iter()
                .enumerate()
                .map(|(index, value)| {
                    value * (-TAU * bin as f64 * index as f64 / size as f64).cos()
                })
                .sum();
            let expected_im: f64 = input
                .iter()
                .enumerate()
                .map(|(index, value)| {
                    value * (-TAU * bin as f64 * index as f64 / size as f64).sin()
                })
                .sum();
            assert!((transformed.re - expected_re).abs() < 1e-10);
            assert!((transformed.im - expected_im).abs() < 1e-10);
        }
    }
}
