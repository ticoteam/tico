use crate::{
    audio::{Channel, Resampler},
    capture::{self, Source},
    settings,
    store::Store,
    stream::{self, Feed, Streams},
};
use anyhow::{Context, Result, ensure};
use serde::Serialize;
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const OUTPUT_RATE: u32 = 16000;
const CHUNK_SAMPLES: usize = OUTPUT_RATE as usize * 45;
/// Chunks end at the quietest 20 ms between 30 s and 55 s, so independent STT requests rarely
/// split a word. Every sample is kept; only the boundary moves. Long chunks give the model
/// context: on AMI meeting audio, word errors fell from 46% at 5 s to 9% at 45 s
/// (`bench/README.md`).
const MIN_CHUNK_SAMPLES: usize = OUTPUT_RATE as usize * 30;
const MAX_CHUNK_SAMPLES: usize = OUTPUT_RATE as usize * 55;
const FRAME: usize = OUTPUT_RATE as usize / 50;

/// Where to end the next chunk, or None to wait for more audio. The cut goes in the middle
/// of the longest run of near-quietest frames, away from the onset of the next word.
fn chunk_boundary(pending: &[f32]) -> Option<usize> {
    if pending.len() < CHUNK_SAMPLES {
        return None;
    }
    let window = &pending[MIN_CHUNK_SAMPLES..pending.len().min(MAX_CHUNK_SAMPLES)];
    let energy = |frame: &[f32]| frame.iter().map(|x| x * x).sum::<f32>() / frame.len() as f32;
    let frames: Vec<f32> = window.chunks_exact(FRAME).map(energy).collect();
    let quietest = frames.iter().copied().fold(f32::INFINITY, f32::min);
    let mean = energy(&pending[..pending.len().min(MAX_CHUNK_SAMPLES)]);
    if quietest > (0.003_f32 * 0.003).max(mean * 0.06) && pending.len() < MAX_CHUNK_SAMPLES {
        return None;
    }
    let limit = quietest * 2. + f32::EPSILON;
    let (mut best, mut start) = ((0, 0), None);
    for (index, value) in frames.iter().chain([&f32::INFINITY]).enumerate() {
        match (start, *value <= limit) {
            (None, true) => start = Some(index),
            (Some(first), false) => {
                if index - first >= best.1 - best.0 {
                    best = (first, index);
                }
                start = None;
            }
            _ => {}
        }
    }
    Some(MIN_CHUNK_SAMPLES + (best.0 + best.1) * FRAME / 2)
}

/// Saves already-captured 16 kHz audio as chunks, cut by the same rule as a live recording.
pub fn import_audio(
    store: &Store,
    id: &str,
    channel: Channel,
    mut pending: Vec<f32>,
) -> Result<usize> {
    let (mut at, mut saved) = (0usize, 0);
    while !pending.is_empty() {
        let len = chunk_boundary(&pending).unwrap_or(pending.len());
        let rest = pending.split_off(len);
        let start_ms = (at * 1000 / OUTPUT_RATE as usize) as u64;
        at += pending.len();
        let end_ms = ((at * 1000 / OUTPUT_RATE as usize) as u64).max(start_ms + 1);
        store.save_audio(id, channel, start_ms, end_ms, &pending, OUTPUT_RATE)?;
        pending = rest;
        saved += 1;
    }
    Ok(saved)
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct SourceHealth {
    pub channel: String,
    pub name: String,
    pub frames: u64,
    pub nonzero: u64,
    pub peak: f32,
    pub dropped_frames: u64,
    pub errors: u64,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Status {
    pub phase: String,
    pub error: String,
    pub elapsed_ms: u64,
    pub sources: Vec<SourceHealth>,
}

pub struct RecordingHandle {
    stop: mpsc::Sender<()>,
    status: Arc<Mutex<Status>>,
    worker: Option<JoinHandle<Result<()>>>,
    cancelled: Arc<AtomicBool>,
}

enum InputEvent {
    Ready(Source),
    Warning(String),
    Failed(String),
}

struct InputPublisher {
    sender: mpsc::Sender<InputEvent>,
    cancelled: Arc<AtomicBool>,
}
struct CancelOnDrop(Arc<AtomicBool>);
impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
impl InputPublisher {
    fn ready(&self, source: Source) -> Result<()> {
        ensure!(
            !self.cancelled.load(Ordering::Acquire),
            "Recording stopped during input setup"
        );
        self.sender
            .send(InputEvent::Ready(source))
            .map_err(|_| anyhow::anyhow!("Recording stopped during input setup"))
    }
    fn warning(&self, text: String) {
        if !self.cancelled.load(Ordering::Acquire) {
            let _ = self.sender.send(InputEvent::Warning(text));
        }
    }
}

fn setup_inputs(
    inputs: &InputPublisher,
    microphone: impl FnOnce() -> Result<Source>,
    meeting_audio: bool,
    meeting: impl FnOnce() -> Result<Source> + Send,
) -> Result<()> {
    // Each input connects independently: a blocked permission prompt or slow device on one
    // side never delays the other. A source finishing after Stop is dropped, which closes it.
    thread::scope(|scope| {
        let meeting = meeting_audio.then(|| {
            scope.spawn(|| -> Result<bool, String> {
                let source = meeting().map_err(|error| format!("{error:#}"))?;
                inputs.ready(source).map_err(|error| format!("{error:#}"))?;
                Ok(true)
            })
        });
        let mut failures = Vec::new();
        let mic_connected = match microphone() {
            Ok(source) => {
                inputs.ready(source)?;
                true
            }
            Err(error) => {
                failures.push(format!("Microphone unavailable: {error:#}."));
                false
            }
        };
        let mut meeting_connected = false;
        if let Some(task) = meeting {
            if !task.is_finished() {
                let connecting = if mic_connected {
                    "Meeting audio is connecting. Microphone recording continues."
                } else {
                    "Meeting audio is connecting."
                };
                inputs.warning(format!("{} {connecting}", failures.join(" ")).trim().into());
            }
            match task.join() {
                Ok(Ok(_)) => meeting_connected = true,
                Ok(Err(error)) => {
                    ensure!(
                        !inputs.cancelled.load(Ordering::Acquire),
                        "Recording stopped during input setup"
                    );
                    failures.push(format!("Meeting audio unavailable: {error}."))
                }
                Err(_) => failures.push("Meeting audio setup stopped unexpectedly.".into()),
            }
        }
        ensure!(mic_connected || meeting_connected, "{}", failures.join(" "));
        let mut warning = failures.join(" ");
        if !warning.is_empty() {
            warning.push_str(if mic_connected {
                " Recording microphone only."
            } else {
                " Recording meeting audio only."
            });
        }
        inputs.warning(warning);
        Ok(())
    })
}

impl RecordingHandle {
    pub fn start(
        store: Store,
        id: String,
        microphone: Option<String>,
        meeting_audio: bool,
    ) -> Result<Self> {
        // Live text streams when Settings allow it; the key is read on the stream's own thread.
        let plan = settings::read(store.root())
            .ok()
            .and_then(|config| stream::Plan::from_settings(&config));
        Self::with_plan(store, id, plan, move |inputs| {
            setup_inputs(
                inputs,
                || capture::start_microphone(microphone.as_deref()),
                meeting_audio,
                capture::start_meeting_audio,
            )
        })
    }
    pub fn with_sources(
        store: Store,
        id: String,
        factory: impl FnOnce() -> Result<(Vec<Source>, String)> + Send + 'static,
    ) -> Result<Self> {
        Self::with_sources_streaming(store, id, None, factory)
    }
    /// Like `with_sources`, also streaming each input for live text when `plan` is set.
    pub fn with_sources_streaming(
        store: Store,
        id: String,
        plan: Option<stream::Plan>,
        factory: impl FnOnce() -> Result<(Vec<Source>, String)> + Send + 'static,
    ) -> Result<Self> {
        Self::with_plan(store, id, plan, move |inputs| {
            let (sources, warning) = factory()?;
            for source in sources {
                inputs.ready(source)?;
            }
            inputs.warning(warning);
            Ok(())
        })
    }
    #[cfg(test)]
    fn with_setup(
        store: Store,
        id: String,
        factory: impl FnOnce(&InputPublisher) -> Result<()> + Send + 'static,
    ) -> Result<Self> {
        Self::with_plan(store, id, None, factory)
    }
    fn with_plan(
        store: Store,
        id: String,
        plan: Option<stream::Plan>,
        factory: impl FnOnce(&InputPublisher) -> Result<()> + Send + 'static,
    ) -> Result<Self> {
        let existing = store.get(&id)?;
        ensure!(
            existing.state != "recording",
            "This meeting is already recording"
        );
        ensure!(
            existing.deliveries.is_empty(),
            "Start a new recording after sending begins for this meeting"
        );
        ensure!(
            store.context(&id)?.live_state != "ended",
            "Start a new recording after the live meeting has ended"
        );
        let offset = existing
            .chunks
            .iter()
            .map(|chunk| chunk.end_ms)
            .max()
            .unwrap_or(0);
        let (stop, receiver) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let cancelled_worker = cancelled.clone();
        let status = Arc::new(Mutex::new(Status {
            phase: "starting".into(),
            ..Default::default()
        }));
        let shared = status.clone();
        let worker = thread::Builder::new()
            .name("tico-capture".into())
            .spawn(move || {
                let result = record(
                    store.clone(),
                    &id,
                    offset,
                    receiver,
                    shared.clone(),
                    factory,
                    cancelled_worker,
                    plan,
                );
                if let Err(error) = &result {
                    let message = error.to_string();
                    let _ = store.state(&id, "interrupted");
                    let _ = store.mutate(&id, |record| {
                        record.warning = message.clone();
                        Ok(())
                    });
                    if let Ok(mut status) = shared.lock() {
                        status.phase = "failed".into();
                        status.error = message;
                    }
                }
                result
            })?;
        Ok(Self {
            stop,
            status,
            worker: Some(worker),
            cancelled,
        })
    }
    pub fn status(&self) -> Status {
        self.status
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone()
    }
    pub fn stop(&self) {
        self.cancelled.store(true, Ordering::Release);
        let _ = self.stop.send(());
    }
    pub fn is_finished(&self) -> bool {
        self.worker
            .as_ref()
            .is_none_or(|worker| worker.is_finished())
    }
    pub fn finish(mut self) -> Result<()> {
        self.stop();
        if let Some(worker) = self.worker.take() {
            worker
                .join()
                .map_err(|_| anyhow::anyhow!("The audio worker stopped unexpectedly"))??;
        }
        Ok(())
    }
}
impl Drop for RecordingHandle {
    fn drop(&mut self) {
        self.stop();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

struct Writer {
    source: Source,
    seen_frames: u64,
    advanced: Instant,
    resampler: Resampler,
    pending: Vec<f32>,
    next_frame: Option<u64>,
    base_ms: f64,
    chunk_ms: f64,
    /// Live text for this input. It gets a copy of every resampled sample and never blocks.
    feed: Option<Feed>,
}
impl Writer {
    fn new(source: Source, started: Instant, offset: u64, feed: Option<Feed>) -> Result<Self> {
        let base_ms = offset as f64
            + source
                .started
                .saturating_duration_since(started)
                .as_secs_f64()
                * 1000.;
        Ok(Self {
            seen_frames: 0,
            advanced: Instant::now(),
            resampler: Resampler::new(source.sample_rate, OUTPUT_RATE)?,
            source,
            pending: Vec::with_capacity(MAX_CHUNK_SAMPLES + 4096),
            next_frame: None,
            base_ms,
            chunk_ms: base_ms,
            feed,
        })
    }
    fn pump(&mut self, store: &Store, id: &str) -> Result<()> {
        while let Ok(packet) = self.source.consumer.pop() {
            if self.next_frame.is_none()
                || self
                    .next_frame
                    .is_some_and(|frame| frame.abs_diff(packet.start_frame) > 2)
            {
                self.flush(store, id, true)?;
                self.resampler = Resampler::new(self.source.sample_rate, OUTPUT_RATE)?;
                self.chunk_ms = self.base_ms
                    + packet.start_frame as f64 * 1000. / self.source.sample_rate as f64;
            }
            self.next_frame = Some(packet.start_frame + packet.len as u64);
            let before = self.pending.len();
            self.resampler
                .push(&packet.samples[..packet.len], &mut self.pending);
            if let Some(feed) = &self.feed {
                let at_ms = self.chunk_ms + before as f64 * 1000. / OUTPUT_RATE as f64;
                feed.push(at_ms, &self.pending[before..]);
            }
            self.flush(store, id, false)?;
        }
        Ok(())
    }
    fn flush(&mut self, store: &Store, id: &str, final_buffer: bool) -> Result<()> {
        loop {
            let len = match chunk_boundary(&self.pending) {
                Some(len) => len,
                None if final_buffer && !self.pending.is_empty() => self.pending.len(),
                None => break,
            };
            let remaining = self.pending.split_off(len);
            let samples = std::mem::replace(&mut self.pending, remaining);
            let end = self.chunk_ms + samples.len() as f64 * 1000. / OUTPUT_RATE as f64;
            let start_ms = self.chunk_ms.round() as u64;
            let end_ms = (end.round() as u64).max(start_ms + 1);
            store
                .save_audio(
                    id,
                    self.source.channel,
                    start_ms,
                    end_ms,
                    &samples,
                    OUTPUT_RATE,
                )
                .context("Could not save captured audio")?;
            self.chunk_ms = end;
        }
        Ok(())
    }
    /// Seconds since the device last delivered audio, once that exceeds a few seconds.
    fn stalled(&mut self) -> Option<u64> {
        let frames = self.source.health.frames.load(Ordering::Relaxed);
        if frames != self.seen_frames {
            self.seen_frames = frames;
            self.advanced = Instant::now();
        }
        let idle = self.advanced.elapsed().as_secs();
        (idle >= 3).then_some(idle)
    }
    fn health(&self) -> SourceHealth {
        let value = &self.source.health;
        SourceHealth {
            channel: self.source.channel.as_str().into(),
            name: self.source.name.clone(),
            frames: value.frames.load(Ordering::Relaxed),
            nonzero: value.nonzero.load(Ordering::Relaxed),
            peak: value.take_peak(),
            dropped_frames: value.dropped_frames.load(Ordering::Relaxed),
            errors: value.errors.load(Ordering::Relaxed),
        }
    }
}
#[allow(clippy::too_many_arguments)]
fn record(
    store: Store,
    id: &str,
    offset: u64,
    stop: mpsc::Receiver<()>,
    status: Arc<Mutex<Status>>,
    factory: impl FnOnce(&InputPublisher) -> Result<()> + Send + 'static,
    cancelled: Arc<AtomicBool>,
    plan: Option<stream::Plan>,
) -> Result<()> {
    let started = Instant::now();
    let mut streams = plan.map(|plan| Streams::new(store.clone(), id.to_owned(), plan));
    let _cancel_on_exit = CancelOnDrop(cancelled.clone());
    store.mutate(id, |record| {
        record.state = "recording".into();
        record.microphone.clear();
        record.meeting_audio = false;
        record.warning.clear();
        Ok(())
    })?;
    let (sender, inputs) = mpsc::channel();
    let publisher = InputPublisher {
        sender,
        cancelled: cancelled.clone(),
    };
    thread::Builder::new()
        .name("tico-input-setup".into())
        .spawn(move || {
            if publisher.cancelled.load(Ordering::Acquire) {
                return;
            }
            let result =
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| factory(&publisher)));
            let error = match result {
                Ok(Ok(())) => None,
                Ok(Err(error)) => Some(format!("{error:#}")),
                Err(_) => Some("Audio input setup stopped unexpectedly".into()),
            };
            if !publisher.cancelled.load(Ordering::Acquire) {
                if let Some(error) = error {
                    let _ = publisher.sender.send(InputEvent::Failed(error));
                }
            }
        })?;
    let mut writers: Vec<Writer> = Vec::new();
    let mut initializing = true;
    let mut warning = String::new();
    let mut previous_warning = String::new();
    loop {
        loop {
            match inputs.try_recv() {
                Ok(InputEvent::Ready(source)) => {
                    let channel = source.channel;
                    let name = source.name.clone();
                    let feed = streams
                        .as_mut()
                        .and_then(|streams| streams.feed(channel.as_str()));
                    writers.push(Writer::new(source, started, offset, feed)?);
                    store.mutate(id, |record| {
                        if channel == Channel::Microphone {
                            record.microphone = name;
                        }
                        if channel == Channel::Meeting {
                            record.meeting_audio = true;
                        }
                        Ok(())
                    })?;
                }
                Ok(InputEvent::Warning(text)) => warning = text,
                Ok(InputEvent::Failed(text)) => {
                    if writers.is_empty() {
                        anyhow::bail!(text);
                    }
                    warning = format!(
                        "Audio input setup failed: {text}. Recording continues with the connected inputs."
                    );
                }
                Err(mpsc::TryRecvError::Disconnected) => {
                    initializing = false;
                    break;
                }
                Err(mpsc::TryRecvError::Empty) => break,
            }
        }
        for writer in &mut writers {
            writer.pump(&store, id)?;
        }
        let health: Vec<_> = writers.iter().map(Writer::health).collect();
        let mut current_warning = warning.clone();
        for writer in &mut writers {
            if writer.stalled().is_some() {
                if !current_warning.is_empty() {
                    current_warning.push(' ');
                }
                current_warning.push_str(if writer.source.channel == Channel::Microphone {
                    "Microphone stopped delivering audio. Check the input device."
                } else {
                    "Meeting audio stopped delivering audio."
                });
            }
        }
        for source in health
            .iter()
            .filter(|source| source.dropped_frames > 0 || source.errors > 0)
        {
            if !current_warning.is_empty() {
                current_warning.push(' ');
            }
            current_warning.push_str(&format!(
                "{}: {} input errors, {} dropped frames.",
                if source.channel == "me" {
                    "Microphone"
                } else {
                    "Meeting audio"
                },
                source.errors,
                source.dropped_frames,
            ));
        }
        if current_warning != previous_warning {
            store.mutate(id, |record| {
                record.warning = current_warning.clone();
                Ok(())
            })?;
            previous_warning = current_warning;
        }
        *status.lock().unwrap_or_else(|error| error.into_inner()) = Status {
            phase: if writers.is_empty() {
                "starting"
            } else {
                "recording"
            }
            .into(),
            elapsed_ms: started.elapsed().as_millis() as u64,
            error: String::new(),
            sources: health,
        };
        if cancelled.load(Ordering::Acquire) {
            break;
        }
        ensure!(
            initializing || !writers.is_empty(),
            "No audio input is connected"
        );
        match stop.recv_timeout(Duration::from_millis(25)) {
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            _ => break,
        }
    }
    // Cancellation drops the receiver too. A late initializer's Source is closed by RAII
    // instead of joining the next recording or keeping an audio input alive after Stop.
    cancelled.store(true, Ordering::Release);
    drop(inputs);
    let stopped_warning = if writers.is_empty() {
        "Recording stopped before audio inputs connected. No audio was captured.".to_owned()
    } else {
        previous_warning
            .replace("Meeting audio is connecting. Microphone recording continues.", "Recording stopped before meeting audio connected. Saved microphone audio is available.")
            .replace("Meeting audio is connecting.", "Recording stopped before meeting audio connected.")
    };
    if stopped_warning != previous_warning {
        store.mutate(id, |record| {
            record.warning = stopped_warning;
            Ok(())
        })?;
    }
    let mut first_error = None;
    for writer in &mut writers {
        if let Err(error) = writer.source.stream.stop() {
            first_error.get_or_insert(error);
        }
    }
    for writer in &mut writers {
        if let Err(error) = writer
            .pump(&store, id)
            .and_then(|()| writer.flush(&store, id, true))
        {
            first_error.get_or_insert(error);
        }
        // The last audio is queued; closing the feed ends the stream after its final text.
        writer.feed = None;
    }
    store.state(id, "stopped")?;
    status
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .phase = "stopped".into();
    if let Some(error) = first_error {
        return Err(error);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::{AudioSink, CaptureStream};
    struct Synthetic;
    impl CaptureStream for Synthetic {
        fn stop(&mut self) -> Result<()> {
            Ok(())
        }
    }
    fn source(channel: Channel, frequency: f32) -> Source {
        let (mut sink, consumer) = AudioSink::channel();
        let health = sink.health.clone();
        let rate = 48000;
        let samples: Vec<_> = (0..rate * 2)
            .map(|index| {
                (index as f32 * frequency * std::f32::consts::TAU / rate as f32).sin() * 0.2
            })
            .collect();
        sink.push_interleaved(&samples, 1);
        Source {
            channel,
            name: "synthetic".into(),
            sample_rate: rate,
            started: Instant::now(),
            health,
            consumer,
            stream: Box::new(Synthetic),
        }
    }
    #[test]
    fn both_sources_save_playable_audio_and_stop_flushes_the_final_partial_chunk_without_network() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Synthetic two-source capture").unwrap();
        let handle = RecordingHandle::with_sources(store.clone(), recording.id.clone(), || {
            Ok((
                vec![
                    source(Channel::Microphone, 440.),
                    source(Channel::Meeting, 660.),
                ],
                String::new(),
            ))
        })
        .unwrap();
        for _ in 0..100 {
            if handle.status().sources.len() == 2
                && handle
                    .status()
                    .sources
                    .iter()
                    .all(|source| source.frames > 0)
            {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        handle.finish().unwrap();
        let saved = store.get(&recording.id).unwrap();
        assert_eq!(saved.state, "stopped");
        assert_eq!(saved.chunks.len(), 2);
        for channel in ["me", "them"] {
            let chunk = saved
                .chunks
                .iter()
                .find(|chunk| chunk.channel == channel)
                .unwrap();
            assert!(chunk.signal.nonzero > 0);
            assert!((chunk.signal.duration_ms as i64 - 2000).abs() <= 1);
            assert!(store.audio_path(chunk).unwrap().exists());
        }
    }
    #[test]
    fn missing_meeting_source_warning_persists_after_stop() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Missing meeting audio").unwrap();
        let handle = RecordingHandle::with_sources(store.clone(), recording.id.clone(), || {
            Ok((
                vec![source(Channel::Microphone, 440.)],
                "Meeting audio unavailable: controlled fixture".into(),
            ))
        })
        .unwrap();
        for _ in 0..100 {
            if store
                .get(&recording.id)
                .unwrap()
                .warning
                .contains("controlled fixture")
            {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        handle.finish().unwrap();
        assert!(
            store
                .get(&recording.id)
                .unwrap()
                .warning
                .contains("controlled fixture")
        );
    }

    #[test]
    fn missing_microphone_still_records_meeting_audio_and_preserves_the_warning() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Missing microphone fixture").unwrap();
        let handle = RecordingHandle::with_setup(store.clone(), recording.id.clone(), |inputs| {
            setup_inputs(
                inputs,
                || anyhow::bail!("Microphone permission denied"),
                true,
                || Ok(source(Channel::Meeting, 660.)),
            )
        })
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while handle.status().sources.is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(handle.status().sources.len(), 1);
        handle.finish().unwrap();
        let saved = store.get(&recording.id).unwrap();
        assert_eq!(saved.state, "stopped");
        assert!(saved.meeting_audio);
        assert!(saved.microphone.is_empty());
        assert_eq!(saved.chunks.len(), 1);
        assert_eq!(saved.chunks[0].channel, "them");
        assert!(saved.chunks[0].signal.nonzero > 0);
        assert!(saved.warning.contains("Microphone permission denied"));
        assert!(saved.warning.contains("Recording meeting audio only"));
        assert!(!saved.warning.contains("connecting"));
    }

    #[test]
    fn failed_inputs_report_both_causes_without_claiming_a_recording() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("No inputs fixture").unwrap();
        let handle = RecordingHandle::with_setup(store.clone(), recording.id.clone(), |inputs| {
            setup_inputs(
                inputs,
                || anyhow::bail!("Microphone permission denied"),
                true,
                || anyhow::bail!("System audio permission denied"),
            )
        })
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !handle.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(handle.is_finished());
        assert!(handle.finish().is_err());
        let saved = store.get(&recording.id).unwrap();
        assert_eq!(saved.state, "interrupted");
        assert!(saved.chunks.is_empty());
        assert!(saved.warning.contains("Microphone permission denied"));
        assert!(saved.warning.contains("System audio permission denied"));
    }

    #[test]
    fn device_errors_identify_the_affected_input() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Input-specific error fixture").unwrap();
        let handle = RecordingHandle::with_sources(store.clone(), recording.id.clone(), || {
            let meeting = source(Channel::Meeting, 660.);
            meeting.health.errors.store(2, Ordering::Relaxed);
            Ok((
                vec![source(Channel::Microphone, 440.), meeting],
                String::new(),
            ))
        })
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !store
            .get(&recording.id)
            .unwrap()
            .warning
            .contains("Meeting audio: 2 input errors")
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(10));
        }
        handle.finish().unwrap();
        let saved = store.get(&recording.id).unwrap();
        assert!(saved.warning.contains("Meeting audio: 2 input errors"));
        assert!(!saved.warning.contains("Microphone:"));
        assert_eq!(saved.chunks.len(), 2);
    }

    #[test]
    fn slow_meeting_setup_does_not_block_microphone_saving_or_stop_and_late_input_is_closed() {
        struct Tracked(Arc<AtomicBool>);
        impl CaptureStream for Tracked {
            fn stop(&mut self) -> Result<()> {
                self.0.store(true, Ordering::Release);
                Ok(())
            }
        }
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store
            .create("Blocked system-audio permission fixture")
            .unwrap();
        let mic_closed = Arc::new(AtomicBool::new(false));
        let late_closed = Arc::new(AtomicBool::new(false));
        let mic_flag = mic_closed.clone();
        let late_flag = late_closed.clone();
        let (release, gate) = mpsc::channel();
        let handle =
            RecordingHandle::with_setup(store.clone(), recording.id.clone(), move |inputs| {
                let (sink, consumer) = AudioSink::channel();
                let health = sink.health.clone();
                let _ = feed(sink, vec![0.2; 16000 * 56]);
                inputs.ready(Source {
                    channel: Channel::Microphone,
                    name: "Synthetic mic".into(),
                    sample_rate: 16000,
                    started: Instant::now(),
                    health,
                    consumer,
                    stream: Box::new(Tracked(mic_flag)),
                })?;
                inputs
                    .warning("Meeting audio is connecting. Microphone recording continues.".into());
                // Long enough for a slow CI runner to cut the microphone's first chunk while this setup is held.
                gate.recv_timeout(Duration::from_secs(30))?;
                let mut late = source(Channel::Meeting, 660.);
                late.stream = Box::new(Tracked(late_flag));
                inputs.ready(late)
            })
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(25);
        while store.get(&recording.id).unwrap().chunks.is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let during_setup = store.get(&recording.id).unwrap();
        assert!(
            !during_setup.chunks.is_empty(),
            "Microphone audio was held behind the other input's setup"
        );
        assert!(!during_setup.meeting_audio);
        // Unbroken sound has no pause; at the 55 s limit it is cut mid-window.
        assert_eq!(during_setup.chunks[0].signal.duration_ms, 42500);
        let started = Instant::now();
        handle.finish().unwrap();
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "Stop waited for pending input setup"
        );
        assert!(mic_closed.load(Ordering::Acquire));
        let next = store
            .create("Next meeting while previous input setup is pending")
            .unwrap();
        release.send(()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !late_closed.load(Ordering::Acquire) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(
            late_closed.load(Ordering::Acquire),
            "The cancelled initializer left an audio source open"
        );
        let saved = store.get(&recording.id).unwrap();
        assert_eq!(saved.state, "stopped");
        assert_eq!(saved.chunks.len(), 2);
        assert_eq!(
            saved
                .chunks
                .iter()
                .map(|chunk| chunk.signal.duration_ms)
                .sum::<u64>(),
            56000
        );
        assert!(saved.chunks.iter().all(|chunk| chunk.channel == "me"));
        assert!(store.get(&next.id).unwrap().chunks.is_empty());
    }

    #[test]
    fn a_meeting_source_can_join_after_microphone_capture_has_started() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Delayed second input").unwrap();
        let (release, gate) = mpsc::channel();
        let handle =
            RecordingHandle::with_setup(store.clone(), recording.id.clone(), move |inputs| {
                inputs.ready(source(Channel::Microphone, 440.))?;
                inputs.warning("Meeting audio is connecting".into());
                gate.recv_timeout(Duration::from_secs(5))?;
                inputs.ready(source(Channel::Meeting, 660.))?;
                inputs.warning(String::new());
                Ok(())
            })
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while handle.status().sources.len() != 1 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(handle.status().sources.len(), 1);
        release.send(()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while (handle.status().sources.len() != 2
            || !store.get(&recording.id).unwrap().warning.is_empty())
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(handle.status().sources.len(), 2);
        handle.finish().unwrap();
        let record = store.get(&recording.id).unwrap();
        assert!(record.meeting_audio);
        assert!(record.warning.is_empty());
        assert_eq!(record.chunks.len(), 2);
        assert!(
            record
                .chunks
                .iter()
                .any(|chunk| chunk.channel == "them" && chunk.signal.nonzero > 0)
        );
    }

    /// Pushes audio far faster than real time, but no faster than the bounded queue drains.
    fn feed(mut sink: AudioSink, samples: Vec<f32>) -> Arc<AtomicBool> {
        let done = Arc::new(AtomicBool::new(false));
        let flag = done.clone();
        thread::spawn(move || {
            for part in samples.chunks(1024 * 32) {
                sink.push_interleaved(part, 1);
                thread::sleep(Duration::from_millis(20));
            }
            flag.store(true, Ordering::Release);
            // Keep the producer alive so the input does not look disconnected.
            thread::sleep(Duration::from_secs(30));
        });
        done
    }

    #[test]
    fn chunks_end_in_pauses_between_words_and_keep_every_sample() {
        // 1.3 s bursts with 0.3 s pauses: a fixed 45 s cut would land mid-burst.
        let rate = 16000;
        let samples: Vec<f32> = (0..rate * 140)
            .map(|n| {
                let t = n % (rate * 16 / 10);
                if t < rate * 13 / 10 {
                    (n as f32 * 300. * std::f32::consts::TAU / rate as f32).sin() * 0.3
                } else {
                    0.0005
                }
            })
            .collect();
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Pause-aware chunk fixture").unwrap();
        let fed = Arc::new(Mutex::new(None));
        let fed_flag = fed.clone();
        let handle =
            RecordingHandle::with_sources(store.clone(), recording.id.clone(), move || {
                let (sink, consumer) = AudioSink::channel();
                let health = sink.health.clone();
                *fed_flag.lock().unwrap() = Some(feed(sink, samples));
                Ok((
                    vec![Source {
                        channel: Channel::Microphone,
                        name: "synthetic".into(),
                        sample_rate: rate as u32,
                        started: Instant::now(),
                        health,
                        consumer,
                        stream: Box::new(Synthetic),
                    }],
                    String::new(),
                ))
            })
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        while !fed
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|done: &Arc<AtomicBool>| done.load(Ordering::Acquire))
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(10));
        }
        // Let the engine drain what was queued last.
        thread::sleep(Duration::from_millis(200));
        handle.finish().unwrap();
        let chunks = store.get(&recording.id).unwrap().chunks;
        assert!(chunks.len() >= 3);
        let total: u64 = chunks.iter().map(|chunk| chunk.signal.duration_ms).sum();
        assert!(
            (total as i64 - 140000).abs() <= 2,
            "samples were lost: {total}"
        );
        for chunk in &chunks[..chunks.len() - 1] {
            let phase = (chunk.end_ms - chunks[0].start_ms) % 1600;
            assert!(
                (1350..=1550).contains(&phase),
                "chunk ended mid-word at {} ms",
                chunk.end_ms
            );
            assert!((30000..=55000).contains(&chunk.signal.duration_ms));
        }
    }

    #[test]
    fn a_blocked_microphone_prompt_does_not_delay_meeting_audio() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let recording = store.create("Blocked microphone fixture").unwrap();
        let (release, gate) = mpsc::channel::<()>();
        let handle =
            RecordingHandle::with_setup(store.clone(), recording.id.clone(), move |inputs| {
                setup_inputs(
                    inputs,
                    move || {
                        gate.recv_timeout(Duration::from_secs(5))?;
                        Ok(source(Channel::Microphone, 440.))
                    },
                    true,
                    || Ok(source(Channel::Meeting, 660.)),
                )
            })
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while handle.status().sources.is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let sources = handle.status().sources;
        assert_eq!(sources.len(), 1, "meeting audio waited for the microphone");
        assert_eq!(sources[0].channel, "them");
        release.send(()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while handle.status().sources.len() < 2 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        handle.finish().unwrap();
        let saved = store.get(&recording.id).unwrap();
        assert!(saved.meeting_audio && !saved.microphone.is_empty());
        assert!(saved.warning.is_empty());
    }
}
