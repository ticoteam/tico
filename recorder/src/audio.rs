use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::io::Cursor;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Channel {
    Microphone,
    Meeting,
}

impl Channel {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Microphone => "me",
            Self::Meeting => "them",
        }
    }
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "me" => Ok(Self::Microphone),
            "them" => Ok(Self::Meeting),
            _ => anyhow::bail!("Unknown audio channel"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Signal {
    pub samples: usize,
    pub nonzero: usize,
    pub peak: f32,
    pub rms: f64,
    pub duration_ms: u64,
    pub sample_rate: u32,
}

impl Signal {
    pub fn silent(&self) -> bool {
        self.nonzero == 0
    }
}

pub fn inspect_wav(bytes: &[u8]) -> Result<Signal> {
    let mut reader = hound::WavReader::new(Cursor::new(bytes)).context("Invalid WAV audio")?;
    let spec = reader.spec();
    ensure!(
        spec.bits_per_sample == 16 && spec.sample_format == hound::SampleFormat::Int,
        "Expected 16-bit PCM WAV"
    );
    ensure!(
        spec.channels > 0 && spec.sample_rate > 0,
        "Invalid WAV format"
    );
    let mut signal = Signal {
        samples: 0,
        nonzero: 0,
        peak: 0.,
        rms: 0.,
        duration_ms: 0,
        sample_rate: spec.sample_rate,
    };
    for value in reader.samples::<i16>() {
        let value = value?;
        signal.samples += 1;
        signal.nonzero += usize::from(value != 0);
        let sample = value as f32 / 32768.;
        signal.peak = signal.peak.max(sample.abs());
        signal.rms += f64::from(sample).powi(2);
    }
    if signal.samples > 0 {
        signal.rms = (signal.rms / signal.samples as f64).sqrt();
    }
    signal.duration_ms =
        signal.samples as u64 * 1000 / u64::from(spec.channels) / u64::from(spec.sample_rate);
    Ok(signal)
}

pub fn encode_wav(samples: &[f32], sample_rate: u32) -> Result<Vec<u8>> {
    ensure!(
        sample_rate > 0 && !samples.is_empty(),
        "No audio samples to save"
    );
    let mut bytes = Cursor::new(Vec::new());
    {
        let mut writer = hound::WavWriter::new(
            &mut bytes,
            hound::WavSpec {
                channels: 1,
                sample_rate,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )?;
        for &sample in samples {
            let value = if sample.is_finite() {
                sample.clamp(-1., 1.)
            } else {
                0.
            };
            writer.write_sample((value * 32767.).round() as i16)?;
        }
        writer.finalize()?;
    }
    Ok(bytes.into_inner())
}

/// A continuous linear resampler. State is retained across native callbacks; chunk boundaries
/// do not restart its phase or accumulate per-buffer rounding drift.
pub struct Resampler {
    step: f64,
    next: f64,
    index: u64,
    previous: Option<f32>,
    filter: Vec<f32>,
    history: Vec<f32>,
    cursor: usize,
}

impl Resampler {
    pub fn new(input_rate: u32, output_rate: u32) -> Result<Self> {
        ensure!(input_rate > 0 && output_rate > 0, "Invalid sample rate");
        let mut filter = vec![];
        if input_rate > output_rate {
            let cutoff = 0.45 * output_rate as f64 / input_rate as f64;
            for n in 0..63 {
                let x = n as f64 - 31.;
                let sinc = if x == 0. {
                    2. * cutoff
                } else {
                    (2. * std::f64::consts::PI * cutoff * x).sin() / (std::f64::consts::PI * x)
                };
                let window = 0.54 - 0.46 * (2. * std::f64::consts::PI * n as f64 / 62.).cos();
                filter.push((sinc * window) as f32);
            }
            let sum = filter.iter().sum::<f32>();
            for value in &mut filter {
                *value /= sum;
            }
        }
        let history = vec![0.; filter.len()];
        Ok(Self {
            step: input_rate as f64 / output_rate as f64,
            next: 0.,
            index: 0,
            previous: None,
            filter,
            history,
            cursor: 0,
        })
    }
    pub fn push(&mut self, samples: &[f32], output: &mut Vec<f32>) {
        for &input in samples {
            let current = if self.filter.is_empty() {
                input
            } else {
                self.history[self.cursor] = input;
                let mut value = 0.;
                for (offset, coefficient) in self.filter.iter().enumerate() {
                    value += coefficient
                        * self.history
                            [(self.cursor + self.history.len() - offset) % self.history.len()];
                }
                self.cursor = (self.cursor + 1) % self.history.len();
                value
            };
            if self.index == 0 {
                output.push(current);
                self.next += self.step;
            } else if let Some(previous) = self.previous {
                while self.next <= self.index as f64 {
                    let fraction = (self.next - (self.index - 1) as f64).clamp(0., 1.) as f32;
                    output.push(previous + (current - previous) * fraction);
                    self.next += self.step;
                }
            }
            self.previous = Some(current);
            self.index += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn digital_silence_is_distinct_from_quiet_audio() {
        assert!(
            inspect_wav(&encode_wav(&vec![0.; 16000], 16000).unwrap())
                .unwrap()
                .silent()
        );
        let signal = inspect_wav(&encode_wav(&vec![0.0001; 16000], 16000).unwrap()).unwrap();
        assert!(!signal.silent());
        assert_eq!(signal.duration_ms, 1000);
    }
    #[test]
    fn resampling_does_not_drift_at_callback_boundaries() {
        for rate in [44100, 48000] {
            let input: Vec<f32> = (0..rate * 2)
                .map(|n| (n as f32 * 440. * std::f32::consts::TAU / rate as f32).sin())
                .collect();
            let mut whole = Vec::new();
            Resampler::new(rate, 16000)
                .unwrap()
                .push(&input, &mut whole);
            let mut split = Vec::new();
            let mut resampler = Resampler::new(rate, 16000).unwrap();
            for part in input.chunks(127) {
                resampler.push(part, &mut split);
            }
            assert_eq!(whole, split);
            assert!((whole.len() as i64 - 32000).abs() <= 1);
        }
    }
    #[test]
    fn downsampling_removes_out_of_band_audio_instead_of_aliasing_it_into_speech() {
        let level = |frequency: f32| {
            let input: Vec<_> = (0..48000)
                .map(|n| (n as f32 * frequency * std::f32::consts::TAU / 48000.).sin())
                .collect();
            let mut output = vec![];
            Resampler::new(48000, 16000)
                .unwrap()
                .push(&input, &mut output);
            (output[100..].iter().map(|x| x * x).sum::<f32>() / (output.len() - 100) as f32).sqrt()
        };
        assert!(level(440.) > 0.65);
        assert!(level(12000.) < 0.01);
    }
}
