//! Realtime streaming transcription (xAI) for live text while someone talks.
//!
//! One stream per channel runs on its own thread while recording. The capture thread hands it
//! the same 16 kHz samples it saves, through a bounded queue that never blocks: when the queue
//! is full, streamed audio is dropped and recorded audio is untouched. Final results are saved
//! in `live_words`, so a crash keeps them; the changing partial text lives in memory only.
//! The chunked transcript stays the record: finished chunks replace streamed text for their
//! time range (`transcript::live_turns`). A live meeting gets each final at once, and the
//! finished chunks then correct it there (`live`).
use crate::{
    settings::{self, Destination, Settings},
    speech,
    store::{Store, Word},
    transcript::{self, LiveTurn, Partial},
};
use anyhow::{Context, Result, ensure};
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::ErrorKind,
    net::{TcpStream, ToSocketAddrs},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, Sender, TryRecvError},
    },
    thread,
    time::{Duration, Instant},
};
use tungstenite::{
    Message, WebSocket, client::IntoClientRequest, http::HeaderValue, stream::MaybeTlsStream,
};

pub const DEFAULT_URL: &str = "wss://api.x.ai/v1/stt";
pub const DEFAULT_MODEL: &str = "grok-voice-transcribe-2.0";
/// Keychain account of the streaming key (service `team.tico.recorder.native`).
pub const KEY_NAME: &str = "xai";

const RATE: u64 = 16_000;
/// 100 ms of PCM16 mono, the frame size the service is fed in.
const FRAME_BYTES: usize = 3_200;
/// Audio waiting for the stream thread, at most ten seconds. It covers the connection
/// handshake; beyond that, streamed audio is dropped (never recorded audio).
const QUEUE_SAMPLES: u64 = 10 * RATE;
/// How long the socket read waits; it also paces the thread when no events arrive, and so how
/// long audio can wait in the queue before it is sent.
const POLL: Duration = Duration::from_millis(50);
/// After the last audio, how long to wait for the service's last finals.
const CLOSING: Duration = Duration::from_secs(5);
const MAX_RETRY: Duration = Duration::from_secs(30);

/// How to stream. The key is read from the keychain on the stream's own thread, so a keychain
/// prompt never holds up capture.
#[derive(Clone)]
pub struct Plan {
    pub url: String,
    pub model: String,
    pub language: String,
    /// None reads the key from the keychain when a stream starts.
    pub key: Option<String>,
    /// Streams through this Tico's relay with the person's sign-in instead of a key ("Your
    /// Tico"); its token is read again for every connection, since it renews hourly.
    pub tico: Option<Destination>,
    /// Streaming was switched on explicitly, so a missing key is shown instead of quietly off.
    pub required: bool,
    /// First reconnect delay; it doubles up to 30 s.
    pub retry: Duration,
}
impl std::fmt::Debug for Plan {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Plan")
            .field("url", &self.url)
            .field("model", &self.model)
            .field("language", &self.language)
            .field("key", &self.key.as_ref().map(|_| "<set>"))
            .field("tico", &self.tico.as_ref().map(|row| &row.name))
            .field("required", &self.required)
            .finish()
    }
}
impl Plan {
    /// None when streaming is switched off in Settings, or speech goes through a Tico that
    /// is not connected.
    pub fn from_settings(config: &Settings) -> Option<Self> {
        if config.streaming == Some(false) {
            return None;
        }
        let tico = match speech::source(config) {
            speech::TICO => Some(speech::tico(config)?.clone()),
            _ => None,
        };
        let url = match &tico {
            Some(destination) => speech::stream_url(destination).ok()?,
            None if config.streaming_url.trim().is_empty() => DEFAULT_URL.into(),
            None => config.streaming_url.trim().into(),
        };
        Some(Self {
            url,
            model: if config.streaming_model.trim().is_empty() {
                DEFAULT_MODEL.into()
            } else {
                config.streaming_model.trim().into()
            },
            language: config.transcription_language.trim().into(),
            key: None,
            // The Tico says whether it streams; nothing to add here when it does not.
            required: config.streaming == Some(true) && tico.is_none(),
            tico,
            retry: Duration::from_secs(1),
        })
    }
    fn endpoint(&self, diarize: bool) -> Result<String> {
        let base = settings::safe_stream_url(&self.url)?;
        let mut params = vec![
            ("model", self.model.as_str()),
            ("encoding", "pcm"),
            ("sample_rate", "16000"),
            ("interim_results", "true"),
        ];
        if !self.language.is_empty() {
            params.push(("language", self.language.as_str()));
        }
        if diarize {
            params.push(("diarize", "true"));
        }
        Ok(reqwest::Url::parse_with_params(&base, &params)?.into())
    }
}

/// Whether recordings stream by default: on when switched on, or unset with an xAI key saved.
/// Through a Tico, unset is on; the Tico says whether it can.
pub fn enabled(config: &Settings) -> bool {
    match config.streaming {
        Some(on) => on,
        None if speech::source(config) == speech::TICO => true,
        None => settings::secret(KEY_NAME).is_ok_and(|key| !key.is_empty()),
    }
}

/// One channel's stream as the UI shows it. `state` is `connecting`, `live`, `retrying`,
/// `stopped` or `off`; `dropped_ms` counts streamed audio skipped (recorded audio is complete).
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct StreamStatus {
    pub channel: String,
    pub state: String,
    pub error: String,
    pub dropped_ms: u64,
    pub reconnects: u32,
}

#[derive(Default)]
struct Shared {
    status: StreamStatus,
    partial: Option<Partial>,
    /// Samples the stream skipped, counted by the capture side and the stream thread.
    dropped: Arc<AtomicU64>,
}
/// Live state of every stream this process ran, by recording and channel.
static LIVE: Mutex<BTreeMap<(String, String), Shared>> = Mutex::new(BTreeMap::new());

fn with_shared<T>(id: &str, channel: &str, change: impl FnOnce(&mut Shared) -> T) -> T {
    let mut live = LIVE.lock().unwrap_or_else(|error| error.into_inner());
    let shared = live.entry((id.into(), channel.into())).or_default();
    shared.status.channel = channel.into();
    change(shared)
}

/// Stream status per channel for a recording; empty when it never streamed in this run.
pub fn status(id: &str) -> Vec<StreamStatus> {
    LIVE.lock()
        .unwrap_or_else(|error| error.into_inner())
        .iter()
        .filter(|((recording, _), _)| recording == id)
        .map(|(_, shared)| StreamStatus {
            dropped_ms: shared.dropped.load(Ordering::Relaxed) * 1000 / RATE,
            ..shared.status.clone()
        })
        .collect()
}

/// The text still being spoken, per channel.
pub fn partials(id: &str) -> Vec<Partial> {
    LIVE.lock()
        .unwrap_or_else(|error| error.into_inner())
        .iter()
        .filter(|((recording, _), _)| recording == id)
        .filter_map(|(_, shared)| shared.partial.clone())
        .collect()
}

/// The live transcript of a recording: chunk text where chunks are done, streamed final text
/// elsewhere, and the partial tail. Poll it while recording; it is cheap.
pub fn live_turns(store: &Store, id: &str) -> Result<Vec<LiveTurn>> {
    let record = store.get(id)?;
    let finals = store.live_words(id)?;
    Ok(transcript::live_turns(&record, &finals, &partials(id)))
}

/// One block of resampled capture audio, at its position in the recording.
struct Block {
    at_ms: f64,
    pcm: Vec<i16>,
}

/// The capture side of one stream. `push` never blocks.
pub struct Feed {
    sender: Sender<Block>,
    queued: Arc<AtomicU64>,
    dropped: Arc<AtomicU64>,
}
impl Feed {
    /// Queues 16 kHz samples that start `at_ms` into the recording. A full queue drops them for
    /// the stream only; a finished stream ignores them.
    pub fn push(&self, at_ms: f64, samples: &[f32]) {
        let len = samples.len() as u64;
        if len == 0 {
            return;
        }
        if self.queued.load(Ordering::Acquire) + len > QUEUE_SAMPLES {
            self.dropped.fetch_add(len, Ordering::Relaxed);
            return;
        }
        let pcm = samples
            .iter()
            .map(|sample| (sample.clamp(-1., 1.) * 32767.) as i16)
            .collect();
        self.queued.fetch_add(len, Ordering::AcqRel);
        if self.sender.send(Block { at_ms, pcm }).is_err() {
            self.queued.fetch_sub(len, Ordering::AcqRel);
        }
    }
}

/// The streams of one recording session. Dropping every `Feed` ends them: each sends its last
/// audio, waits briefly for the final text and closes.
pub struct Streams {
    store: Store,
    id: String,
    plan: Plan,
    started: BTreeSet<String>,
}
impl Streams {
    pub fn new(store: Store, id: String, plan: Plan) -> Self {
        Self {
            store,
            id,
            plan,
            started: BTreeSet::new(),
        }
    }
    /// Starts the stream for `channel` ("me" or "them") and returns its feed.
    pub fn feed(&mut self, channel: &str) -> Option<Feed> {
        if !self.started.insert(channel.into()) {
            return None;
        }
        let (sender, receiver) = mpsc::channel();
        let (queued, dropped) = (Arc::new(AtomicU64::new(0)), Arc::new(AtomicU64::new(0)));
        with_shared(&self.id, channel, |shared| {
            shared.status = StreamStatus {
                channel: channel.into(),
                state: "connecting".into(),
                ..Default::default()
            };
            shared.partial = None;
            shared.dropped = dropped.clone();
        });
        let worker = Worker {
            store: self.store.clone(),
            id: self.id.clone(),
            channel: channel.into(),
            plan: self.plan.clone(),
            receiver,
            queued: queued.clone(),
            dropped: dropped.clone(),
            reconnects: 0,
        };
        let spawned = thread::Builder::new()
            .name(format!("tico-stream-{channel}"))
            .spawn(move || worker.run());
        if let Err(error) = spawned {
            with_shared(&self.id, channel, |shared| {
                shared.status.state = "off".into();
                shared.status.error = format!("Live text could not start: {error}");
            });
            return None;
        }
        Some(Feed {
            sender,
            queued,
            dropped,
        })
    }
}

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

/// How a connection ended.
enum End {
    /// The recording stopped and the service finished, or the wait for it ran out.
    Finished,
    /// The connection failed; reconnect.
    Lost(String),
}

struct Worker {
    store: Store,
    id: String,
    channel: String,
    plan: Plan,
    receiver: Receiver<Block>,
    queued: Arc<AtomicU64>,
    dropped: Arc<AtomicU64>,
    reconnects: u32,
}

impl Worker {
    /// The next queued block, with the queue's size updated.
    fn next(&self) -> Result<Block, TryRecvError> {
        let block = self.receiver.try_recv()?;
        self.queued
            .fetch_sub(block.pcm.len() as u64, Ordering::AcqRel);
        Ok(block)
    }
    fn set(&self, state: &str, error: &str) {
        let reconnects = self.reconnects;
        with_shared(&self.id, &self.channel, |shared| {
            shared.status.state = state.into();
            shared.status.error = error.into();
            shared.status.reconnects = reconnects;
            if state != "live" {
                shared.partial = None;
            }
        });
    }
    fn run(mut self) {
        crate::qos::utility();
        if let Some(destination) = self.plan.tico.clone() {
            // A Tico that can't stream leaves live text off; chunks are transcribed all the same.
            match speech::current(&destination) {
                Ok(services) if services.stream => {}
                Ok(_) => return self.set("off", ""),
                Err(error) => {
                    return self.set(
                        "off",
                        &format!("{:#}", speech::plain(error, "show live text")),
                    );
                }
            }
        }
        let key = match (&self.plan.key, &self.plan.tico) {
            (Some(key), _) => key.clone(),
            (None, Some(_)) => String::new(),
            (None, None) => settings::secret(KEY_NAME).unwrap_or_default(),
        };
        if key.is_empty() && self.plan.tico.is_none() {
            let error = if self.plan.required {
                "Add an xAI key in Settings to see live text"
            } else {
                ""
            };
            self.set("off", error);
            return;
        }
        let mut wait = self.plan.retry;
        loop {
            self.set(
                if self.reconnects == 0 {
                    "connecting"
                } else {
                    "retrying"
                },
                "",
            );
            let opened = Instant::now();
            // Through a Tico the person's token is read for each connection; it renews hourly.
            let key = match (&self.plan.tico, key.is_empty()) {
                (Some(destination), true) => crate::signin::token(&destination.id),
                _ => Ok(key.clone()),
            };
            let end = match key.and_then(|key| self.open(&key)) {
                Ok(socket) => {
                    self.set("live", "");
                    self.session(socket)
                }
                Err(error) => End::Lost(format!("{error:#}")),
            };
            match end {
                End::Finished => break,
                End::Lost(error) => {
                    if opened.elapsed() > Duration::from_secs(30) {
                        wait = self.plan.retry;
                    }
                    self.reconnects += 1;
                    let error = if self.plan.tico.is_some() {
                        tico_words(&error)
                    } else {
                        error
                    };
                    self.set("retrying", &short(&error));
                    if !self.idle(wait) {
                        break;
                    }
                    wait = (wait * 2).min(MAX_RETRY);
                }
            }
        }
        self.set("stopped", "");
    }
    /// Waits before reconnecting, discarding audio meanwhile. False once recording stopped.
    fn idle(&self, wait: Duration) -> bool {
        let until = Instant::now() + wait;
        loop {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return true;
            }
            match self.receiver.recv_timeout(left) {
                Ok(block) => {
                    let samples = block.pcm.len() as u64;
                    self.queued.fetch_sub(samples, Ordering::AcqRel);
                    self.dropped.fetch_add(samples, Ordering::Relaxed);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => return true,
                Err(mpsc::RecvTimeoutError::Disconnected) => return false,
            }
        }
    }
    /// Connects and waits for the service to accept audio. Capture keeps queueing meanwhile.
    fn open(&self, key: &str) -> Result<Socket> {
        let url = self.plan.endpoint(self.channel != "me")?;
        let mut request = url.as_str().into_client_request()?;
        let mut bearer = HeaderValue::from_str(&format!("Bearer {key}"))
            .context("The live text credential contains characters a header cannot hold")?;
        bearer.set_sensitive(true);
        request.headers_mut().insert("Authorization", bearer);
        let uri = request.uri().clone();
        let secure = uri.scheme_str() == Some("wss");
        let host = uri.host().context("The streaming URL has no host")?;
        let port = uri.port_u16().unwrap_or(if secure { 443 } else { 80 });
        let address = (host.trim_matches(['[', ']']), port)
            .to_socket_addrs()?
            .next()
            .context("The streaming host did not resolve")?;
        let tcp = TcpStream::connect_timeout(&address, Duration::from_secs(8))?;
        tcp.set_read_timeout(Some(Duration::from_secs(10)))?;
        tcp.set_write_timeout(Some(Duration::from_secs(10)))?;
        tcp.set_nodelay(true)?;
        let connector = if secure {
            tungstenite::Connector::Rustls(tls())
        } else {
            tungstenite::Connector::Plain
        };
        let (mut socket, _) =
            tungstenite::client_tls_with_config(request, tcp, None, Some(connector)).map_err(
                |error| match error {
                    tungstenite::HandshakeError::Failure(error) => {
                        anyhow::anyhow!(describe(&error))
                    }
                    tungstenite::HandshakeError::Interrupted(_) => {
                        anyhow::anyhow!("The streaming service did not answer")
                    }
                },
            )?;
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            ensure!(
                Instant::now() < deadline,
                "The streaming service did not start the session"
            );
            match socket.read() {
                Ok(Message::Text(text)) => {
                    let event: Value = serde_json::from_str(&text).unwrap_or_default();
                    match event["type"].as_str() {
                        Some("transcript.created") => break,
                        Some("error") => anyhow::bail!(event_error(&event)),
                        _ => {}
                    }
                }
                Ok(Message::Close(_)) => anyhow::bail!("The streaming service closed the session"),
                Ok(_) => {}
                Err(error) => anyhow::bail!(describe(&error)),
            }
        }
        let tcp = match socket.get_mut() {
            MaybeTlsStream::Plain(tcp) => tcp,
            MaybeTlsStream::Rustls(tls) => tls.get_mut(),
            _ => anyhow::bail!("Unexpected streaming transport"),
        };
        tcp.set_read_timeout(Some(POLL))?;
        Ok(socket)
    }
    /// Streams audio and reads results until the recording stops or the connection fails.
    fn session(&mut self, mut socket: Socket) -> End {
        // Service time is seconds of audio sent on this connection. Each anchor maps a sample
        // count to the recording position of that sample, so gaps and reconnects keep their
        // place: (samples streamed before, recording ms).
        let mut anchors: Vec<(u64, f64)> = vec![];
        let mut streamed: u64 = 0;
        let mut next_at: Option<f64> = None;
        let mut frame: Vec<u8> = Vec::with_capacity(FRAME_BYTES * 2);
        let mut closing: Option<Instant> = None;
        loop {
            if closing.is_none() {
                loop {
                    match self.next() {
                        Ok(block) => {
                            if next_at.is_none_or(|at| (block.at_ms - at).abs() > 2.) {
                                anchors.push((streamed, block.at_ms));
                            }
                            let samples = block.pcm.len() as u64;
                            next_at = Some(block.at_ms + (samples * 1000) as f64 / RATE as f64);
                            streamed += samples;
                            frame.extend(block.pcm.iter().flat_map(|sample| sample.to_le_bytes()));
                            while frame.len() >= FRAME_BYTES {
                                let rest = frame.split_off(FRAME_BYTES);
                                let bytes = std::mem::replace(&mut frame, rest);
                                if let Err(error) = socket.send(Message::Binary(bytes.into())) {
                                    return End::Lost(describe(&error));
                                }
                            }
                        }
                        Err(TryRecvError::Empty) => break,
                        Err(TryRecvError::Disconnected) => {
                            // Stop: the last partial frame, then the end of audio.
                            let mut sent = Ok(());
                            if !frame.is_empty() {
                                sent =
                                    socket.send(Message::Binary(std::mem::take(&mut frame).into()));
                            }
                            let sent = sent.and_then(|()| {
                                socket.send(Message::text(r#"{"type":"audio.done"}"#))
                            });
                            if sent.is_err() {
                                return End::Finished;
                            }
                            closing = Some(Instant::now());
                            break;
                        }
                    }
                }
            }
            if closing.is_some_and(|at| at.elapsed() > CLOSING) {
                let _ = socket.close(None);
                return End::Finished;
            }
            match socket.read() {
                Ok(Message::Text(text)) => match self.event(&text, &anchors) {
                    Ok(true) => {
                        let _ = socket.close(None);
                        return End::Finished;
                    }
                    Ok(false) => {}
                    Err(error) if closing.is_some() => {
                        self.set("stopped", &short(&format!("{error:#}")));
                        return End::Finished;
                    }
                    Err(error) => return End::Lost(format!("{error:#}")),
                },
                Ok(Message::Close(_)) if closing.is_some() => return End::Finished,
                Ok(Message::Close(_)) => {
                    return End::Lost("The streaming service closed the session".into());
                }
                Ok(_) => {}
                Err(tungstenite::Error::Io(error))
                    if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
                Err(_) if closing.is_some() => return End::Finished,
                Err(error) => return End::Lost(describe(&error)),
            }
        }
    }
    /// Handles one service event. True when the service finished the transcript.
    fn event(&self, text: &str, anchors: &[(u64, f64)]) -> Result<bool> {
        let event: Value =
            serde_json::from_str(text).context("The streaming service sent invalid JSON")?;
        match event["type"].as_str().unwrap_or("") {
            "transcript.done" => Ok(true),
            "error" => anyhow::bail!(event_error(&event)),
            "transcript.partial" => {
                let at = |seconds: f64| position(anchors, seconds);
                let words: Vec<Word> = event["words"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|word| {
                        let text = word["text"].as_str().or(word["word"].as_str())?.trim();
                        let start = at(word["start"].as_f64()?);
                        let end = at(word["end"].as_f64().unwrap_or(0.)).max(start);
                        (!text.is_empty()).then(|| Word {
                            start_ms: start,
                            end_ms: end,
                            text: text.into(),
                            speaker: if self.channel == "me" {
                                String::new()
                            } else {
                                speaker(&word["speaker"])
                            },
                            turn: String::new(),
                        })
                    })
                    .collect();
                let start_s = event["start"].as_f64().unwrap_or(0.);
                let end_s = start_s + event["duration"].as_f64().unwrap_or(0.);
                let text = event["text"].as_str().unwrap_or("").trim().to_owned();
                if event["is_final"].as_bool() == Some(true) {
                    // Finals within an utterance carry only their new words, while `start` is
                    // still the utterance's start: they replace the words they span. The end of
                    // the utterance (`speech_final`) restates all of it and replaces its range.
                    let span = (
                        words.iter().map(|word| word.start_ms).min(),
                        words.iter().map(|word| word.end_ms).max(),
                    );
                    let range = match (event["speech_final"].as_bool() == Some(true), span) {
                        (true, (Some(low), Some(high))) => {
                            Some((low.min(at(start_s)), high.max(at(end_s))))
                        }
                        (true, _) => Some((at(start_s), at(end_s))),
                        (false, (Some(low), Some(high))) => Some((low, high)),
                        (false, _) if !text.is_empty() => Some((at(start_s), at(end_s))),
                        (false, _) => None,
                    };
                    let words = if words.is_empty() && !text.is_empty() {
                        vec![Word {
                            start_ms: at(start_s),
                            end_ms: at(end_s),
                            text: text.clone(),
                            ..Default::default()
                        }]
                    } else {
                        words
                    };
                    if let Some((from, to)) = range {
                        self.store
                            .replace_live_words(&self.id, &self.channel, from, to, &words)?;
                    }
                    with_shared(&self.id, &self.channel, |shared| shared.partial = None);
                } else {
                    let partial = (!text.is_empty()).then(|| Partial {
                        channel: self.channel.clone(),
                        start_ms: words.first().map_or(at(start_s), |word| word.start_ms),
                        end_ms: words
                            .iter()
                            .map(|word| word.end_ms)
                            .max()
                            .unwrap_or(at(end_s)),
                        text,
                        words,
                    });
                    with_shared(&self.id, &self.channel, |shared| shared.partial = partial);
                }
                Ok(false)
            }
            _ => Ok(false),
        }
    }
}

/// Recording milliseconds of a service time (seconds of audio streamed on this connection).
fn position(anchors: &[(u64, f64)], seconds: f64) -> u64 {
    let sample = (seconds.max(0.) * RATE as f64) as u64;
    let at = anchors.partition_point(|(streamed, _)| *streamed <= sample);
    let Some(&(streamed, at_ms)) = anchors.get(at.saturating_sub(1)) else {
        return (seconds.max(0.) * 1000.).round() as u64;
    };
    (at_ms + (sample.saturating_sub(streamed) * 1000) as f64 / RATE as f64).round() as u64
}

/// Provisional far-end speaker from the service's own labels: S1, S2, …
fn speaker(value: &Value) -> String {
    match value {
        Value::Number(number) => number
            .as_u64()
            .map_or_else(String::new, |n| format!("S{}", n + 1)),
        Value::String(text) if !text.is_empty() => format!("S{text}"),
        _ => String::new(),
    }
}

fn event_error(event: &Value) -> String {
    let message = event["message"]
        .as_str()
        .or(event["error"]["message"].as_str())
        .or(event["error"].as_str())
        .unwrap_or("The streaming service reported an error");
    short(&format!("Live text: {message}"))
}

/// A connection error without anything from the request (which carries the key).
fn describe(error: &tungstenite::Error) -> String {
    match error {
        tungstenite::Error::Http(response) => match response.status().as_u16() {
            401 | 403 => "The streaming service refused the xAI key".into(),
            code => format!("The streaming service answered HTTP {code}"),
        },
        tungstenite::Error::Io(error) => format!("Live text connection: {}", error.kind()),
        tungstenite::Error::Tls(_) => "Live text: secure connection failed".into(),
        tungstenite::Error::ConnectionClosed | tungstenite::Error::AlreadyClosed => {
            "The streaming service closed the session".into()
        }
        other => short(&format!("Live text: {other}")),
    }
}

/// A connection error through the Tico relay, in its words: the Tico refused the sign-in, or
/// can't stream right now.
fn tico_words(error: &str) -> String {
    if error.contains("refused the xAI key") {
        return "Your Tico refused live text. Sign in again in Settings.".into();
    }
    match error.split_once("answered HTTP ") {
        Some((_, code)) => format!("Your Tico can't show live text right now — HTTP {code}"),
        None => error.replace("The streaming service", "Your Tico's live text"),
    }
}

fn short(text: &str) -> String {
    text.chars().take(300).collect()
}

fn tls() -> Arc<rustls::ClientConfig> {
    static CONFIG: std::sync::OnceLock<Arc<rustls::ClientConfig>> = std::sync::OnceLock::new();
    CONFIG
        .get_or_init(|| {
            let roots = rustls::RootCertStore {
                roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
            };
            let config = rustls::ClientConfig::builder_with_provider(Arc::new(
                rustls::crypto::ring::default_provider(),
            ))
            .with_safe_default_protocol_versions()
            .expect("TLS protocol versions")
            .with_root_certificates(roots)
            .with_no_client_auth();
            Arc::new(config)
        })
        .clone()
}

#[cfg(test)]
#[path = "stream_tests.rs"]
mod tests;
