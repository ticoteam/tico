use crate::audio::Channel;
use anyhow::{Result, bail};
use rtrb::{Consumer, Producer, RingBuffer};
use std::sync::{
    Arc,
    atomic::{AtomicU32, AtomicU64, Ordering},
};

pub const PACKET_SAMPLES: usize = 1024;

#[derive(Clone)]
pub struct Packet {
    pub start_frame: u64,
    pub len: usize,
    pub samples: [f32; PACKET_SAMPLES],
}

#[derive(Default)]
pub struct Health {
    pub frames: AtomicU64,
    pub nonzero: AtomicU64,
    pub dropped_frames: AtomicU64,
    pub errors: AtomicU64,
    peak: AtomicU32,
}

impl Health {
    pub fn take_peak(&self) -> f32 {
        f32::from_bits(self.peak.swap(0, Ordering::Relaxed))
    }
}

pub struct AudioSink {
    producer: Producer<Packet>,
    pub health: Arc<Health>,
    next_frame: u64,
}

impl AudioSink {
    pub fn set_frame(&mut self, frame: u64) {
        self.next_frame = self.next_frame.max(frame);
    }
    /// Allocates the bounded queue once, before native capture starts.
    pub fn channel() -> (Self, Consumer<Packet>) {
        let (producer, consumer) = RingBuffer::new(256);
        (
            Self {
                producer,
                health: Arc::new(Health::default()),
                next_frame: 0,
            },
            consumer,
        )
    }
    /// Called by the native audio thread. No heap allocation, locks, disk or network I/O.
    pub fn push_interleaved(&mut self, data: &[f32], channels: usize) {
        if channels == 0 {
            self.health.errors.fetch_add(1, Ordering::Relaxed);
            return;
        }
        let frames = data.len() / channels;
        self.push_frames(frames, |frame| {
            data[frame * channels..(frame + 1) * channels]
                .iter()
                .copied()
                .sum::<f32>()
                / channels as f32
        });
    }
    pub fn push_frames(&mut self, frames: usize, mut sample_at: impl FnMut(usize) -> f32) {
        for offset in (0..frames).step_by(PACKET_SAMPLES) {
            let len = (frames - offset).min(PACKET_SAMPLES);
            let mut packet = Packet {
                start_frame: self.next_frame,
                len,
                samples: [0.; PACKET_SAMPLES],
            };
            let mut peak = 0.0_f32;
            let mut nonzero = 0;
            for (index, sample) in packet.samples[..len].iter_mut().enumerate() {
                let value = sample_at(offset + index);
                *sample = if value.is_finite() { value } else { 0. };
                peak = peak.max(sample.abs());
                nonzero += u64::from(*sample != 0.);
            }
            self.health
                .peak
                .fetch_max(peak.to_bits(), Ordering::Relaxed);
            self.health.nonzero.fetch_add(nonzero, Ordering::Relaxed);
            self.next_frame += len as u64;
            self.health.frames.store(self.next_frame, Ordering::Relaxed);
            if self.producer.push(packet).is_err() {
                self.health
                    .dropped_frames
                    .fetch_add(len as u64, Ordering::Relaxed);
            }
        }
    }
}

pub trait CaptureStream: Send {
    fn stop(&mut self) -> Result<()>;
}

pub struct Source {
    pub channel: Channel,
    pub name: String,
    pub sample_rate: u32,
    pub started: std::time::Instant,
    pub health: Arc<Health>,
    pub consumer: Consumer<Packet>,
    pub stream: Box<dyn CaptureStream>,
}

impl Drop for Source {
    fn drop(&mut self) {
        let _ = self.stream.stop();
    }
}

#[cfg(target_os = "macos")]
#[path = "capture/macos.rs"]
mod macos;

pub fn sources() -> Result<Vec<String>> {
    use cpal::traits::{DeviceTrait, HostTrait};
    cpal::default_host()
        .input_devices()?
        .map(|device| {
            device
                .description()
                .map(|d| d.name().to_string())
                .map_err(Into::into)
        })
        .collect()
}

pub fn start_microphone(name: Option<&str>) -> Result<Source> {
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    let host = cpal::default_host();
    let device = match name {
        Some(name) => host.input_devices()?.find(|device| {
            device
                .description()
                .is_ok_and(|description| description.name() == name)
        }),
        None => host.default_input_device(),
    }
    .ok_or_else(|| {
        anyhow::anyhow!(
            "Microphone is unavailable. Check the input device and microphone permission."
        )
    })?;
    let description = device.description()?.name().to_string();
    let config = device.default_input_config()?;
    let rate = config.sample_rate();
    let channels = usize::from(config.channels());
    let (mut sink, consumer) = AudioSink::channel();
    let health = sink.health.clone();
    let failed = health.clone();
    let error = move |_error| {
        failed.errors.fetch_add(1, Ordering::Relaxed);
    };
    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => device.build_input_stream(
            config.into(),
            move |data: &[f32], _| sink.push_interleaved(data, channels),
            error,
            Some(std::time::Duration::from_secs(5)),
        )?,
        cpal::SampleFormat::I16 => device.build_input_stream(
            config.into(),
            move |data: &[i16], _| {
                sink.push_frames(data.len() / channels, |frame| {
                    data[frame * channels..(frame + 1) * channels]
                        .iter()
                        .map(|sample| *sample as f32 / 32768.)
                        .sum::<f32>()
                        / channels as f32
                })
            },
            error,
            Some(std::time::Duration::from_secs(5)),
        )?,
        format => bail!("Unsupported microphone format: {format:?}"),
    };
    let started = std::time::Instant::now();
    stream.play()?;
    struct Microphone(cpal::Stream);
    impl CaptureStream for Microphone {
        fn stop(&mut self) -> Result<()> {
            use cpal::traits::StreamTrait;
            self.0.pause()?;
            Ok(())
        }
    }
    Ok(Source {
        channel: Channel::Microphone,
        name: description,
        sample_rate: rate,
        started,
        health,
        consumer,
        stream: Box::new(Microphone(stream)),
    })
}

pub fn start_meeting_audio() -> Result<Source> {
    #[cfg(target_os = "macos")]
    {
        macos::start()
    }
    #[cfg(not(target_os = "macos"))]
    {
        bail!("Meeting audio capture is not implemented on this platform yet")
    }
}

/// A real-time test tone in place of a microphone, for profiling and demos without a device:
/// 48 kHz in 10 ms packets, a tone that pauses now and then like speech.
pub fn start_synthetic(channel: Channel) -> Source {
    let rate = 48_000;
    start_paced(channel, "Test tone", rate, move |frame| {
        let at = frame as f32 / rate as f32;
        if at % 7. < 5. {
            (at * 220. * std::f32::consts::TAU).sin() * 0.2
        } else {
            0.
        }
    })
}

/// An input that delivers `sample_at(frame)` in real time, as a device would: a test tone, or a
/// recorded file for headless tests. Only in isolated libraries.
pub fn start_paced(
    channel: Channel,
    name: &str,
    rate: u32,
    mut sample_at: impl FnMut(u64) -> f32 + Send + 'static,
) -> Source {
    use std::sync::atomic::AtomicBool;
    struct Paced(Arc<AtomicBool>);
    impl CaptureStream for Paced {
        fn stop(&mut self) -> Result<()> {
            self.0.store(true, Ordering::Relaxed);
            Ok(())
        }
    }
    let (mut sink, consumer) = AudioSink::channel();
    let health = sink.health.clone();
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = stop.clone();
    let started = std::time::Instant::now();
    std::thread::Builder::new()
        .name("tico-paced-input".into())
        .spawn(move || {
            let mut frame = 0_u64;
            while !stopped.load(Ordering::Relaxed) {
                let due = started.elapsed().as_secs_f64() * f64::from(rate);
                let frames = (due as u64).saturating_sub(frame) as usize;
                sink.push_frames(frames, |index| sample_at(frame + index as u64));
                frame += frames as u64;
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        })
        .expect("paced input");
    Source {
        channel,
        name: name.into(),
        sample_rate: rate,
        started,
        health,
        consumer,
        stream: Box::new(Paced(stop)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn overflow_is_visible_and_later_timestamps_preserve_the_gap() {
        let (mut sink, mut consumer) = AudioSink::channel();
        for _ in 0..300 {
            sink.push_interleaved(&[0.25; 1024], 1);
        }
        assert!(sink.health.dropped_frames.load(Ordering::Relaxed) > 0);
        while consumer.pop().is_ok() {}
        sink.push_interleaved(&[0.5; 128], 1);
        assert_eq!(consumer.pop().unwrap().start_frame, 300 * 1024);
    }
}
