//! The server's word on what changed for this computer (`runner/runner_events.py`): `GET runners/me/events`
//! held open, each `runner` event naming the reads it covers. While the stream is down the runner polls as
//! before; reconnects back off to RECONNECT_MAX_S and resume from the last event id. A server without the
//! endpoint (404/405) is polled as before and asked again every LEGACY_RETRY_S. A stream that comes back after it
//! was lost or unavailable says so, so a support bundle shows the outage ended.
//!
//! A running turn reads its inputs and chat goal on a notice too (drive.rs): a `work` notice naming its bot, or anything
//! that means "read everything again" (a reset, a catch-up after an outage, a kind this runner does not know). With the
//! stream up and no notice, it asks only every TURN_RECOVERY_S, jittered; with the stream down, every second as before.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::Value;
use tokio::sync::{broadcast, Notify};

use crate::client::{bearer, http_client, quote_plus, Client, CLIENT_VERSION};
use crate::util::{diagnostic, log, mark};
use crate::wire::{decode, ReadyFrame, RunnerFrame};

pub const BACKUP_POLL_S: f64 = 300.0;
pub const RECONNECT_MAX_S: f64 = 60.0;
pub const LEGACY_RETRY_S: f64 = 1800.0;
/// The server sends a keepalive every 15 s by default and at most every 50 s (backend/events.py).
pub const READ_TIMEOUT_S: u64 = 90;
pub const HEARTBEAT_LIVE_S: f64 = 60.0;
/// A stream that stayed up this long before it dropped had a fresh drop: its reconnect backoff starts over.
pub const STABLE_S: u64 = 30;
/// For this long after a stream that was up drops, while the server cannot be reached at all (a restart: the
/// connection is refused, or the proxy in front answers 502/503/504), the stream is tried again at least every
/// QUICK_RETRY_S instead of on the doubling backoff. The stream is then back within a couple of seconds of the server,
/// instead of up to half a minute later, and the runner's own fallback polls (`live` false) stop as soon as it is.
pub const QUICK_WINDOW_S: u64 = 90;
pub const QUICK_RETRY_S: f64 = 2.0;
/// A server's new release asks every computer to read everything again (`ready`). Each computer spreads that pass
/// over this many seconds, so the dozen computers that reconnect to a just-started server do not all ask at once.
pub const CATCH_UP_SPREAD_S: f64 = 10.0;
/// A read taken just before a stream dropped may have failed against the stopping server, after its hint moved the
/// stream's cursor on. A resumed stream does not replay that hint, so the channels taken since this long before the
/// drop are read once more.
pub const REASK_BEFORE_DROP_S: u64 = 60;
/// How often a running turn asks for its inputs and goal with the stream up and no notice for its bot: the backstop
/// for a notice that went missing. Each wait is jittered by TURN_RECOVERY_JITTER so turns do not line up.
pub const TURN_RECOVERY_S: f64 = 15.0;
pub const TURN_RECOVERY_JITTER: f64 = 0.2;
/// How often a resident voice session reads its inputs when nothing pushes them: what it always did.
const VOICE_POLL: Duration = Duration::from_millis(400);
/// The backstop read while the server pushes `voice_inputs` hints, for a hint that went missing.
const VOICE_BACKUP: Duration = Duration::from_secs(3);
/// A stream quiet this long may be dead without having failed yet: voice keeps its own poll until it is heard again.
/// The server sends a keepalive every 15 s by default (20 s is a common setting).
const VOICE_QUIET: Duration = Duration::from_secs(25);
/// A stream quiet this long (keepalives come every 15 s by default) is not counted on for relay hints.
const HINTS_QUIET: Duration = Duration::from_secs(35);
/// Every channel the runner reads on its own clock. A channel's bit in `Events::pending` is its index here.
pub const CHANNELS: [&str; 11] = [
    "claim",
    "sync",
    "config",
    "heartbeat",
    "logins",
    "harness",
    "imports",
    "refresh",
    "keys",
    "claude_logins",
    "importers",
];
/// What a catch-up pass reads: every channel but `keys`, which only a credential change asks for.
pub const ALL: &[&str] = &[
    "claim",
    "sync",
    "config",
    "heartbeat",
    "logins",
    "harness",
    "imports",
    "refresh",
    "claude_logins",
    "importers",
];

/// What a gap's catch-up asks for at once: queued work, and the heartbeat whose answer says how many may run.
const AT_ONCE: &[&str] = &["claim", "heartbeat"];

/// A channel's index in `CHANNELS`, or None for a name the runner does not read.
pub fn index(channel: &str) -> Option<usize> {
    CHANNELS.iter().position(|c| *c == channel)
}

fn bit(channel: &str) -> u16 {
    debug_assert!(index(channel).is_some(), "unknown event channel {channel}");
    index(channel).map_or(0, |i| 1 << i)
}

/// What each event kind asks the runner to read again.
pub fn channels(kind: &str) -> &'static [&'static str] {
    match kind {
        "work" => &["claim"],
        "assignments" | "repositories" => &["sync"],
        // A credential's value changed: the model keys from the app are asked for again at once (signin.rs).
        "credentials" => &["sync", "keys"],
        "config" => &["sync", "config"],
        "worktrees" | "restart" | "performance" | "browser_exit" => &["heartbeat"],
        "capacity" => &["heartbeat"],
        "logins" => &["logins"],
        "harness_actions" => &["harness"],
        "credential_imports" => &["imports"],
        // A plan's usage read, or a full Claude login's new token or refresh (claude_login.rs).
        "subscription_refresh" => &["refresh", "claude_logins"],
        // Each wakes its own input reader: crate::screen polls its sessions at once, a voice session reads its inputs.
        "screen_open" | "voice_inputs" => &[],
        // crate::fetch_fresh fetches the bots' checkouts at once.
        "pushed" => &[],
        // The meeting importers assigned here (crate::sidejobs); only a server that took relay_hints_v1 sends it.
        "importers" => &["importers"],
        _ => ALL,
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Stream {
    Down,
    Up,
    Legacy,
}

enum Failure {
    NotSupported,
    Broken(String),
    /// No server answered: the connection failed, or the proxy in front of a server that is down said so.
    Unreachable(String),
}

#[derive(Clone)]
enum VoiceWake {
    Input { attempt_id: String, call_id: String },
    CatchUp,
}

pub(crate) struct VoiceInputs {
    events: Arc<Events>,
    wakes: broadcast::Receiver<VoiceWake>,
    /// When the session last read its inputs; None reads at once.
    read_at: Option<tokio::time::Instant>,
}

impl VoiceInputs {
    /// Until the session's next read of its inputs: a hint for its attempt, a catch-up, or its poll. While the server
    /// pushes hints the poll is only a backstop (VOICE_BACKUP); otherwise it is the 400 ms poll it always was. Losing
    /// push (the stream drops, the server stops taking voice_inputs_v1) ends a backstop wait at once.
    pub async fn wait(&mut self, attempt: &str, call: &str) {
        if let Some(at) = self.read_at {
            let every = if self.events.voice_pushed() {
                VOICE_BACKUP
            } else {
                VOICE_POLL
            };
            let wakes = &mut self.wakes;
            tokio::select! {
                _ = tokio::time::sleep_until(at + every) => {}
                _ = async {
                    loop {
                        match wakes.recv().await {
                            Ok(VoiceWake::Input { attempt_id, call_id }) if attempt_id == attempt && call_id == call => break,
                            Ok(VoiceWake::CatchUp) | Err(broadcast::error::RecvError::Lagged(_)) => break,
                            Ok(VoiceWake::Input { .. }) => {}
                            Err(broadcast::error::RecvError::Closed) => std::future::pending().await,
                        }
                    }
                } => {}
            }
        }
        self.read_at = Some(tokio::time::Instant::now());
    }

    pub fn refresh(&mut self) {
        self.read_at = None;
    }
}

pub struct Events {
    /// The channels the server asked for since each was last taken, one bit per channel (`index`).
    pending: AtomicU16,
    state: Mutex<Stream>,
    last_id: Mutex<Option<String>>,
    /// The `after` the stream being opened asked to resume from; None when it asked for none.
    resume: Mutex<Option<i64>>,
    /// A release's catch-up pass (`ready`), due at this instant (CATCH_UP_SPREAD_S).
    catch_up_at: Mutex<Option<Instant>>,
    /// Whether the waiting pass tells running turns too (a release's); a gap's pass already told them.
    catch_up_notices: AtomicBool,
    /// When each channel (by `bit`) was last taken: read again on a resumed stream (REASK_BEFORE_DROP_S).
    taken: Mutex<HashMap<u16, Instant>>,
    /// When the stream was last said to be lost or unavailable, until it is said to be back.
    down_since: Mutex<Option<Instant>>,
    pub release: Mutex<Option<Value>>,
    /// Woken whenever the server asks for something, so the loop acts on it at once.
    pub wake: Notify,
    stop: AtomicBool,
    /// Notices for running turns: (for every bot, per bot). `heard` adds the two, so a turn sees either move.
    notices: Mutex<(u64, HashMap<String, u64>)>,
    voice: broadcast::Sender<VoiceWake>,
    /// Whether the server took voice_inputs_v1 on the last heartbeat, so it pushes voice hints to this computer.
    voice_push: AtomicBool,
    /// Whether the server took relay_hints_v1 on the last heartbeat: it tells this computer of a change to its
    /// meeting importers and to the config the harness relay reads (`relays_pushed`).
    relay_hints: AtomicBool,
    /// Woken when `importers` is asked for, so the side jobs (crate::sidejobs) read their importers at once.
    pub side: Notify,
    /// When the stream last said anything, a keepalive included.
    heard_at: Mutex<Option<tokio::time::Instant>>,
    /// TURN_RECOVERY_S in milliseconds; tests shorten it.
    recovery_ms: AtomicU64,
}

impl Events {
    pub fn new() -> Arc<Events> {
        Arc::new(Events {
            pending: AtomicU16::new(0),
            state: Mutex::new(Stream::Down),
            last_id: Mutex::new(None),
            resume: Mutex::new(None),
            catch_up_at: Mutex::new(None),
            catch_up_notices: AtomicBool::new(false),
            taken: Mutex::new(HashMap::new()),
            down_since: Mutex::new(None),
            release: Mutex::new(None),
            wake: Notify::new(),
            stop: AtomicBool::new(false),
            notices: Mutex::new((0, HashMap::new())),
            voice: broadcast::channel(32).0,
            voice_push: AtomicBool::new(false),
            relay_hints: AtomicBool::new(false),
            side: Notify::new(),
            heard_at: Mutex::new(None),
            recovery_ms: AtomicU64::new((TURN_RECOVERY_S * 1000.0) as u64),
        })
    }

    pub(crate) fn voice_inputs(self: &Arc<Self>) -> VoiceInputs {
        VoiceInputs {
            events: self.clone(),
            wakes: self.voice.subscribe(),
            read_at: None,
        }
    }

    /// What the server's heartbeat answer says: whether it took voice_inputs_v1 (runner.rs post_heartbeat).
    pub fn voice_push(&self, taken: bool) {
        if self.voice_push.swap(taken, Ordering::SeqCst) && !taken {
            self.voice_unpushed();
        }
    }

    /// What the server's heartbeat answer says: whether it took relay_hints_v1 (runner.rs post_heartbeat).
    pub fn relay_hints(&self, taken: bool) {
        self.relay_hints.store(taken, Ordering::SeqCst);
    }

    /// Whether the relays that read on a minute's clock (the harness relay's config, the side jobs' importers) can
    /// wait for the server's word instead: it took relay_hints_v1, and the stream is up and heard within HINTS_QUIET
    /// (a stalled connection can stay `Up` until READ_TIMEOUT_S). They then read on a hint and every BACKUP_POLL_S.
    pub fn relays_pushed(&self) -> bool {
        self.relay_hints.load(Ordering::SeqCst)
            && self.live()
            && self
                .heard_at
                .lock()
                .unwrap()
                .is_some_and(|at| at.elapsed() < HINTS_QUIET)
    }

    /// Whether voice hints reach this computer now: the server takes them and the stream is up and not gone quiet.
    fn voice_pushed(&self) -> bool {
        self.voice_push.load(Ordering::SeqCst)
            && self.live()
            && self
                .heard_at
                .lock()
                .unwrap()
                .is_some_and(|at| at.elapsed() < VOICE_QUIET)
    }

    /// Push just stopped: a voice session waiting on its backstop reads now, then polls as before.
    fn voice_unpushed(&self) {
        let _ = self.voice.send(VoiceWake::CatchUp);
    }

    fn stream_heard(&self) {
        *self.heard_at.lock().unwrap() = Some(tokio::time::Instant::now());
    }

    /// The stream is no longer up (dropped, or the server has none).
    fn fall_back(&self, to: Stream) {
        let was = std::mem::replace(&mut *self.state.lock().unwrap(), to);
        if was == Stream::Up {
            self.voice_unpushed();
        }
    }

    /// How many notices a running turn of `bot` has had: one that differs from the count at its last ask means ask
    /// again now. Take it before the request, so a notice that lands during the request is not lost.
    pub fn heard(&self, bot: &str) -> u64 {
        self.settle();
        let notices = self.notices.lock().unwrap();
        notices.0 + notices.1.get(bot).copied().unwrap_or(0)
    }

    /// A notice for the running turns of `bots`, or of every bot when it names none.
    fn notice(&self, bots: &[String]) {
        let mut notices = self.notices.lock().unwrap();
        if bots.is_empty() {
            notices.0 += 1;
        }
        for bot in bots {
            *notices.1.entry(bot.clone()).or_insert(0) += 1;
        }
    }

    /// The wait until a running turn's next recovery check: TURN_RECOVERY_S, give or take TURN_RECOVERY_JITTER.
    pub fn turn_recovery(&self) -> Duration {
        let base = self.recovery_ms.load(Ordering::Relaxed) as f64 / 1000.0;
        let spread = (uuid::Uuid::new_v4().as_u128() % 10_001) as f64 / 10_000.0 * 2.0 - 1.0;
        Duration::from_secs_f64(base * (1.0 + TURN_RECOVERY_JITTER * spread))
    }

    #[cfg(test)]
    pub(crate) fn set_turn_recovery(&self, every: Duration) {
        self.recovery_ms
            .store(every.as_millis() as u64, Ordering::Relaxed);
    }

    pub fn live(&self) -> bool {
        *self.state.lock().unwrap() == Stream::Up
    }

    pub fn state(&self) -> Stream {
        *self.state.lock().unwrap()
    }

    /// True once for each time the server asked for `channel` since it was last taken.
    pub fn take(&self, channel: &str) -> bool {
        self.settle();
        let bit = bit(channel);
        let taken = self.pending.fetch_and(!bit, Ordering::SeqCst) & bit != 0;
        if taken {
            self.taken.lock().unwrap().insert(bit, Instant::now());
        }
        taken
    }

    /// How long until a release's catch-up pass is due, if one waits: the run loop sleeps no longer than this.
    pub fn catch_up_in(&self) -> Option<Duration> {
        self.catch_up_at
            .lock()
            .unwrap()
            .map(|at| at.saturating_duration_since(Instant::now()))
    }

    /// Everything asked for now, every running turn notified: a release's pass still waiting is not needed any more.
    fn catch_up_now(&self) {
        *self.catch_up_at.lock().unwrap() = None;
        self.catch_up_notices.store(false, Ordering::SeqCst);
        self.ask(ALL);
        self.notice(&[]);
    }

    /// A resumed stream: the channels taken since REASK_BEFORE_DROP_S before it dropped are asked for once more.
    fn reask_since(&self, dropped: Instant) {
        let since = dropped
            .checked_sub(Duration::from_secs(REASK_BEFORE_DROP_S))
            .unwrap_or(dropped);
        let bits = self
            .taken
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, at)| **at >= since)
            .fold(0, |set, (bit, _)| set | bit);
        if bits != 0 {
            self.asked(bits);
        }
    }

    /// A release's catch-up pass whose moment came: everything is asked for, and every running turn hears it.
    fn settle(&self) {
        let due = {
            let mut at = self.catch_up_at.lock().unwrap();
            at.is_some_and(|t| Instant::now() >= t) && at.take().is_some()
        };
        if due {
            if self.catch_up_notices.swap(false, Ordering::SeqCst) {
                self.catch_up_now();
            } else {
                self.ask(ALL);
            }
        }
    }

    /// Back after a gap the stream cannot replay (a reconnect the server did not resume, or its `reset`): queued
    /// work and every running turn's inputs are asked for at once, everything else within CATCH_UP_SPREAD_S, so the
    /// computers that come back together do not all read everything in the same second.
    fn catch_up_after_gap(&self) {
        // The heartbeat too: its answer carries the turn limit a queued chat may be waiting on.
        self.ask(AT_ONCE);
        self.notice(&[]);
        self.catch_up_soon(false);
    }

    /// Everything read again within CATCH_UP_SPREAD_S, at a moment of this computer's own; `notices`: running turns
    /// are told then as well.
    fn catch_up_soon(&self, notices: bool) {
        let at = Instant::now() + Duration::from_secs_f64(CATCH_UP_SPREAD_S * unit());
        let mut due = self.catch_up_at.lock().unwrap();
        // One already waiting keeps its moment: a second release within the spread asks for the same pass.
        if due.is_none() {
            *due = Some(at);
        }
        if notices {
            self.catch_up_notices.store(true, Ordering::SeqCst);
        }
    }

    pub fn ask(&self, channels: &[&'static str]) {
        self.asked(channels.iter().fold(0, |set, channel| set | bit(channel)));
    }

    /// `bits` asked for: the run loop wakes, and the side jobs too when their importers are among them.
    fn asked(&self, bits: u16) {
        self.pending.fetch_or(bits, Ordering::SeqCst);
        self.wake.notify_one();
        if bits & bit("importers") != 0 {
            self.side.notify_one();
        }
    }

    pub fn close(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub async fn run(self: Arc<Self>, client: Client) {
        let mut delay = 1.0f64;
        let mut legacy_until = Instant::now();
        let mut said_down = false;
        // When a stream that was up last dropped (QUICK_WINDOW_S).
        let mut dropped_at: Option<Instant> = None;
        while !self.stop.load(Ordering::SeqCst) {
            if self.state() == Stream::Legacy && Instant::now() < legacy_until {
                tokio::time::sleep((legacy_until - Instant::now()).min(Duration::from_secs(60)))
                    .await;
                continue;
            }
            let started = Instant::now();
            let (why, unreachable) = match self.read_once(&client).await {
                Ok(true) => {
                    delay = 1.0;
                    dropped_at = None;
                    continue; // the server's own lifetime ran out: reconnect at once
                }
                Ok(false) => (None, false),
                Err(Failure::NotSupported) => {
                    if self.state() != Stream::Legacy {
                        log("Tico runner: this server has no event stream; polling as before");
                    }
                    self.fall_back(Stream::Legacy);
                    legacy_until = Instant::now() + Duration::from_secs_f64(LEGACY_RETRY_S);
                    continue;
                }
                Err(Failure::Broken(why)) => (Some(why), false),
                Err(Failure::Unreachable(why)) => (Some(why), true),
            };
            if let Some(why) = why {
                if self.state() == Stream::Up {
                    dropped_at = Some(Instant::now());
                    delay = backoff_after_drop(delay, started.elapsed());
                    log(&format!(
                        "Tico runner: event stream lost ({why}); polling until it is back"
                    ));
                    *self.down_since.lock().unwrap() = Some(Instant::now());
                } else if !said_down {
                    said_down = true;
                    *self.down_since.lock().unwrap() = Some(Instant::now());
                    log(&format!(
                        "Tico runner: event stream unavailable ({}); polling",
                        why.chars().take(120).collect::<String>()
                    ));
                }
            }
            self.fall_back(Stream::Down);
            let wait = jittered(retry_after(
                delay,
                unreachable,
                dropped_at.map(|at| at.elapsed()),
            ));
            tokio::time::sleep(Duration::from_secs_f64(wait)).await;
            delay = (delay * 2.0).min(RECONNECT_MAX_S);
        }
    }

    /// One connection, to its end. Ok(true) when the server closed it after its lifetime.
    async fn read_once(&self, client: &Client) -> Result<bool, Failure> {
        let recovering = self.state() != Stream::Up;
        let after = self.last_id.lock().unwrap().clone();
        let query = after
            .as_ref()
            .map(|a| format!("?after={}", quote_plus(a)))
            .unwrap_or_default();
        *self.resume.lock().unwrap() = after.as_deref().and_then(|a| a.parse().ok());
        let token = bearer(&client.token).map_err(|e| Failure::Broken(e.to_string()))?;
        let http = http_client(Some(Duration::from_secs(READ_TIMEOUT_S)));
        let mut req = http
            .get(format!("{}/api/v2/runners/me/events{query}", client.url))
            .header("Authorization", format!("Bearer {token}"))
            .header("Accept", "text/event-stream")
            .header("User-Agent", format!("Tico-Client/{CLIENT_VERSION}"));
        if let Some(a) = &after {
            req = req.header("Last-Event-ID", a.as_str());
        }
        let response = req
            .send()
            .await
            .map_err(|e| Failure::Unreachable(format!("URLError: {e}")))?;
        let status = response.status().as_u16();
        if status == 404 || status == 405 {
            return Err(Failure::NotSupported);
        }
        if [502, 503, 504].contains(&status) {
            // The proxy in front of a server that is not answering (a restart).
            return Err(Failure::Unreachable(format!("HTTPError: HTTP {status}")));
        }
        if !(200..300).contains(&status) {
            return Err(Failure::Broken(format!("HTTPError: HTTP {status}")));
        }
        let mut body = response.bytes_stream();
        let mut buffer: Vec<u8> = vec![];
        let (mut name, mut ident, mut data): (Option<String>, Option<String>, Value) =
            (None, None, Value::Null);
        loop {
            let chunk = match body.next().await {
                None => break,
                Some(Ok(c)) => c,
                Some(Err(e)) => return Err(Failure::Broken(body_error(&e))),
            };
            buffer.extend_from_slice(&chunk);
            while let Some(at) = buffer.iter().position(|b| *b == b'\n') {
                let raw: Vec<u8> = buffer.drain(..=at).collect();
                let line = String::from_utf8_lossy(&raw)
                    .trim_end_matches(['\r', '\n'])
                    .to_string();
                if line.is_empty() {
                    if let Some(event) = name.take() {
                        let payload = std::mem::replace(&mut data, Value::Null);
                        let payload = if payload.is_null() {
                            Value::Object(Default::default())
                        } else {
                            payload
                        };
                        self.dispatch(&event, ident.take(), &payload, recovering);
                        if self.stop.load(Ordering::SeqCst) {
                            return Ok(true);
                        }
                        if event == "expired" {
                            return Err(Failure::Broken("Ended".into()));
                        }
                    }
                    ident = None;
                    data = Value::Null;
                } else if line.starts_with(':') {
                    self.stream_heard(); // keepalive
                } else {
                    let (field, value) = line.split_once(':').unwrap_or((line.as_str(), ""));
                    let value = value.strip_prefix(' ').unwrap_or(value);
                    match field {
                        "data" => {
                            data = serde_json::from_str(value)
                                .unwrap_or(Value::Object(Default::default()))
                        }
                        "event" => name = Some(value.to_string()),
                        "id" => ident = Some(value.to_string()),
                        _ => {}
                    }
                }
            }
        }
        Ok(self.live())
    }

    /// A frame this runner cannot read counts as one that says nothing: a `runner` frame then asks for everything.
    pub(crate) fn dispatch(
        &self,
        event: &str,
        ident: Option<String>,
        data: &Value,
        recovering: bool,
    ) {
        self.stream_heard();
        match event {
            "ready" => {
                let frame: ReadyFrame = decode(data.clone()).unwrap_or_default();
                let release = frame.release.map(Value::String).unwrap_or(Value::Null);
                {
                    let mut last = self.last_id.lock().unwrap();
                    if last.is_none() {
                        *last = frame.seq.map(|seq| seq.to_string());
                    }
                }
                let changed = self
                    .release
                    .lock()
                    .unwrap()
                    .as_ref()
                    .is_some_and(|r| *r != release);
                // Back after an outage: one full pass catches up on anything unsaid. Not when the server took the
                // resume (it echoes our `after`): it sends every change since then on this stream, and a `reset` when
                // it no longer has them. A new server release: one full pass too, within CATCH_UP_SPREAD_S.
                // A server whose newest change is behind our `after` (a restored database) has not got them: no resume.
                let resumed = self.resume.lock().unwrap().is_some_and(|after| {
                    frame.after == Some(after) && frame.seq.is_some_and(|seq| seq >= after)
                });
                // A process's first connection has no cursor and reads everything as it starts anyway: at once, as
                // before, so the start's own reads and this pass coincide.
                let first = self.resume.lock().unwrap().is_none();
                if recovering && !resumed && first {
                    self.catch_up_now();
                } else if recovering && !resumed {
                    self.catch_up_after_gap();
                } else {
                    if recovering {
                        // A read taken just before the drop may have failed; its hint is not sent again.
                        if let Some(dropped) = *self.down_since.lock().unwrap() {
                            self.reask_since(dropped);
                        }
                    }
                    if changed {
                        self.catch_up_soon(true);
                    }
                }
                if changed {
                    // Another release may not push voice or relay hints: each polls until the heartbeat says.
                    self.voice_push(false);
                    self.relay_hints(false);
                }
                *self.release.lock().unwrap() = Some(release);
                *self.state.lock().unwrap() = Stream::Up;
                // Even a clean reconnect can have missed a hint between streams.
                let _ = self.voice.send(VoiceWake::CatchUp);
                if let Some(since) = self.down_since.lock().unwrap().take() {
                    diagnostic(&format!(
                        "Tico runner: event stream back after {} s; polling stops",
                        since.elapsed().as_secs()
                    ));
                }
                self.wake.notify_one();
            }
            "runner" => {
                let frame: RunnerFrame = decode(data.clone()).unwrap_or_default();
                let kind = frame.kind.as_deref().unwrap_or_default();
                if kind == "work" {
                    mark("heard", "-");
                }
                if kind == "screen_open" {
                    crate::screen::heard();
                }
                if kind == "pushed" {
                    crate::fetch_fresh::heard(&frame.bots);
                }
                if kind == "voice_inputs" {
                    if let (Some(attempt_id), Some(call_id)) = (frame.attempt_id, frame.call_id) {
                        let _ = self.voice.send(VoiceWake::Input {
                            attempt_id,
                            call_id,
                        });
                    }
                } else {
                    let asked = channels(kind);
                    if kind == "work" {
                        // A follow-up queued or a chat goal changed for these bots (backend/events.py): their running
                        // turns ask for inputs and goal at once.
                        self.notice(&frame.bots);
                    } else if asked == ALL {
                        self.notice(&[]);
                    }
                    self.ask(asked);
                }
            }
            "reset" => {
                self.catch_up_after_gap();
                let _ = self.voice.send(VoiceWake::CatchUp);
            }
            "screen_open" => crate::screen::heard(),
            _ => {}
        }
        if let Some(id) = ident.filter(|i| !i.is_empty()) {
            *self.last_id.lock().unwrap() = Some(id);
        }
    }
}

/// The reconnect delay after a live stream dropped. A stream that stayed up STABLE_S had a fresh drop and starts over
/// at 1 s; otherwise a proxy that resets the stream every few minutes would stretch each short reset to
/// RECONNECT_MAX_S. One that drops soon after `ready` keeps backing off, so a stream that cannot stay up does not
/// reconnect, with a full catch-up, every second.
fn backoff_after_drop(delay: f64, lasted: Duration) -> f64 {
    if lasted >= Duration::from_secs(STABLE_S) {
        1.0
    } else {
        delay
    }
}

/// How long to wait before the next try. While a server that dropped a live stream within QUICK_WINDOW_S cannot be
/// reached at all, no longer than QUICK_RETRY_S: it is most likely restarting, and every second the stream stays down
/// is a second of fallback polling against it once it is back. Anything else waits out the doubling backoff.
fn retry_after(delay: f64, unreachable: bool, since_drop: Option<Duration>) -> f64 {
    if unreachable && since_drop.is_some_and(|d| d < Duration::from_secs(QUICK_WINDOW_S)) {
        delay.min(QUICK_RETRY_S)
    } else {
        delay
    }
}

/// A reconnect wait of this computer's own, between half of `wait` and all of it: the computers a server restart
/// dropped together do not all try it again in the same instant, and keep apart on later tries.
fn jittered(wait: f64) -> f64 {
    wait * (0.5 + 0.5 * unit())
}

/// A random number in 0..=1.
fn unit() -> f64 {
    (uuid::Uuid::new_v4().as_u128() % 10_001) as f64 / 10_000.0
}

/// Why a stream's body ended early, in words: a read timeout is the server going quiet past READ_TIMEOUT_S
/// (keepalives come every 15 s), anything else is the connection, named down to its cause.
fn body_error(e: &reqwest::Error) -> String {
    if e.is_timeout() {
        return format!("TimeoutError: no data from the server for {READ_TIMEOUT_S} s");
    }
    let mut why = e.to_string();
    let mut source = std::error::Error::source(e);
    while let Some(cause) = source {
        why.push_str(": ");
        why.push_str(&cause.to_string());
        source = cause.source();
    }
    format!("ConnectionError: {why}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kinds_map_to_the_python_channels() {
        assert_eq!(channels("work"), &["claim"]);
        assert_eq!(channels("config"), &["sync", "config"]);
        assert_eq!(channels("credentials"), &["sync", "keys"]);
        assert_eq!(channels("capacity"), &["heartbeat"]);
        assert_eq!(channels("something new"), ALL);
        let e = Events::new();
        e.dispatch(
            "ready",
            Some("7".into()),
            &serde_json::json!({"seq": 7, "release": "v1"}),
            true,
        );
        assert!(e.live() && e.take("claim") && !e.take("claim"));
        e.pending.store(0, Ordering::SeqCst);
        e.dispatch(
            "runner",
            Some("8".into()),
            &serde_json::json!({"kind": "work"}),
            false,
        );
        assert!(e.take("claim") && !e.take("sync"));
        assert_eq!(e.last_id.lock().unwrap().as_deref(), Some("8"));
    }

    #[test]
    fn a_push_is_handed_to_the_checkout_fetch_and_reads_nothing_again() {
        let e = Events::new();
        let bot = format!("pushed-{}", uuid::Uuid::new_v4().simple());
        let before = e.heard(&bot);
        e.dispatch(
            "runner",
            None,
            &serde_json::json!({"kind": "pushed", "bots": [bot]}),
            false,
        );
        assert!(crate::fetch_fresh::heard_of(&bot));
        assert!(CHANNELS.iter().all(|c| !e.take(c)) && e.heard(&bot) == before);
    }

    #[test]
    fn every_channel_has_its_own_bit_and_a_catch_up_pass_leaves_keys_out() {
        for kind in [
            "work",
            "config",
            "credentials",
            "capacity",
            "logins",
            "harness_actions",
            "credential_imports",
            "subscription_refresh",
            "worktrees",
            "something new",
        ] {
            assert!(channels(kind).iter().all(|c| index(c).is_some()), "{kind}");
        }
        assert!(ALL.iter().all(|c| index(c).is_some()) && !ALL.contains(&"keys"));
        let e = Events::new();
        e.ask(&CHANNELS);
        for name in CHANNELS {
            assert!(e.take(name) && !e.take(name), "{name}");
        }
        e.ask(ALL);
        assert!(!e.take("keys") && e.take("sync") && !e.take("sync"));
        e.ask(&["keys"]);
        assert!(e.take("keys") && !e.take("keys"));
    }

    #[test]
    fn a_turn_hears_work_for_its_own_bot_and_anything_that_asks_for_everything() {
        let e = Events::new();
        let (acme, cpo) = (e.heard("acme"), e.heard("cpo"));
        e.dispatch(
            "runner",
            None,
            &serde_json::json!({"kind": "work", "bots": ["acme"]}),
            false,
        );
        assert!(e.heard("acme") > acme && e.heard("cpo") == cpo);
        for kind in [
            "logins",
            "config",
            "credentials",
            "screen_open",
            "voice_inputs",
        ] {
            let before = (e.heard("acme"), e.heard("cpo"));
            e.dispatch(
                "runner",
                None,
                &serde_json::json!({"kind": kind, "bots": ["acme"]}),
                false,
            );
            assert_eq!((e.heard("acme"), e.heard("cpo")), before, "{kind}");
        }
        let (acme, cpo) = (e.heard("acme"), e.heard("cpo"));
        // An older server's work notice names no bot; a kind this runner does not know asks for everything.
        for (event, data) in [
            ("runner", serde_json::json!({"kind": "work"})),
            ("runner", serde_json::json!({"kind": "something new"})),
            ("reset", serde_json::json!({})),
            ("ready", serde_json::json!({"seq": 9, "release": "v2"})),
        ] {
            let before = (e.heard("acme"), e.heard("cpo"));
            e.dispatch(event, None, &data, true);
            assert!(
                e.heard("acme") > before.0 && e.heard("cpo") > before.1,
                "{event} {data}"
            );
        }
        assert!(e.heard("acme") > acme && e.heard("cpo") > cpo);
        // A first `ready` on a fresh start is no gap: nothing to catch up on.
        let fresh = Events::new();
        fresh.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 1, "release": "v1"}),
            false,
        );
        assert_eq!(fresh.heard("acme"), 0);
    }

    #[tokio::test(start_paused = true)]
    async fn relays_wait_for_hints_only_from_a_server_that_took_them_on_a_stream_that_is_heard() {
        let e = Events::new();
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 1, "release": "v1"}),
            false,
        );
        assert!(!e.relays_pushed(), "no heartbeat answer yet");
        e.relay_hints(true);
        assert!(e.relays_pushed());
        // A stream gone quiet past HINTS_QUIET may be dead: the minute's clock again until it is heard.
        tokio::time::advance(HINTS_QUIET + Duration::from_secs(1)).await;
        assert!(!e.relays_pushed());
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 1, "release": "v1"}),
            false,
        );
        assert!(e.relays_pushed());
        // Another release may not send them: the clock until its heartbeat says.
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 1, "release": "v2"}),
            false,
        );
        assert!(!e.relays_pushed());
        e.relay_hints(true);
        e.fall_back(Stream::Down);
        assert!(!e.relays_pushed());
    }

    #[tokio::test]
    async fn an_importers_hint_wakes_the_side_jobs_and_asks_for_nothing_else() {
        let e = Events::new();
        e.dispatch(
            "runner",
            None,
            &serde_json::json!({"kind": "importers"}),
            false,
        );
        let woke = tokio::time::timeout(Duration::from_millis(200), e.side.notified()).await;
        assert!(woke.is_ok());
        assert!(e.take("importers") && !e.take("importers"));
        assert!(CHANNELS.iter().all(|c| !e.take(c)));
        // A kind this runner does not know reads everything, its importers too, and wakes the side jobs for it.
        e.dispatch("runner", None, &serde_json::json!({"kind": "later"}), false);
        let woke = tokio::time::timeout(Duration::from_millis(200), e.side.notified()).await;
        assert!(woke.is_ok() && e.take("importers") && e.take("config"));
    }

    /// A runner whose stream is up and whose server took voice_inputs_v1.
    fn pushing() -> Arc<Events> {
        let e = Events::new();
        e.dispatch("ready", None, &serde_json::json!({"seq": 1}), false);
        e.voice_push(true);
        e
    }

    #[tokio::test(start_paused = true)]
    async fn voice_inputs_wake_only_the_named_attempt_and_call() {
        let e = pushing();
        let mut inputs = e.voice_inputs();
        inputs.wait("attempt", "call").await;

        let prompt = Duration::from_millis(10);
        for (attempt_id, call_id) in [("other-attempt", "call"), ("attempt", "other-call")] {
            e.dispatch(
                "runner",
                None,
                &serde_json::json!({
                    "kind": "voice_inputs", "attempt_id": attempt_id, "call_id": call_id
                }),
                false,
            );
            assert!(tokio::time::timeout(prompt, inputs.wait("attempt", "call"))
                .await
                .is_err());
        }
        e.dispatch(
            "runner",
            Some("17".into()),
            &serde_json::json!({
                "kind": "voice_inputs", "attempt_id": "attempt", "call_id": "call"
            }),
            false,
        );
        tokio::time::timeout(prompt, inputs.wait("attempt", "call"))
            .await
            .expect("the notice did not wake the voice loop");
        assert_eq!(e.last_id.lock().unwrap().as_deref(), Some("17"));
        assert!(!e.take("claim"));
    }

    #[tokio::test(start_paused = true)]
    async fn a_voice_hint_wakes_the_session_before_its_backstop_from_every_server_that_sends_one() {
        let e = pushing();
        let mut inputs = e.voice_inputs();
        inputs.wait("attempt", "call").await;
        // The frame as the server's stream sends it (backend/events.py runner_read), and as the first servers to send
        // hints did, with the attempt renamed run_id (wire.rs RunnerFrame): neither waits for the backstop read.
        for key in ["attempt_id", "run_id"] {
            let mut frame = serde_json::json!({
                "seq": 17, "kind": "voice_inputs", "bots": [], "call_id": "call"
            });
            frame[key] = serde_json::json!("attempt");
            e.dispatch("runner", Some("17".into()), &frame, false);
            assert_eq!(next_read(&mut inputs).await, Duration::ZERO, "{key}");
            assert_eq!(next_read(&mut inputs).await, VOICE_BACKUP, "{key}");
        }
    }

    /// How long the next read of the voice inputs waits, given nothing else wakes it.
    async fn next_read(inputs: &mut VoiceInputs) -> Duration {
        let before = tokio::time::Instant::now();
        inputs.wait("attempt", "call").await;
        before.elapsed()
    }

    #[tokio::test(start_paused = true)]
    async fn voice_inputs_back_off_only_while_hints_are_pushed() {
        // Never pushed (an older server, or one refusing voice_inputs_v1): the 400 ms poll, as before.
        let e = Events::new();
        e.dispatch("ready", None, &serde_json::json!({"seq": 1}), false);
        let mut inputs = e.voice_inputs();
        assert_eq!(next_read(&mut inputs).await, Duration::ZERO);
        assert_eq!(next_read(&mut inputs).await, VOICE_POLL);

        // Pushed: a 3 s backstop, kept by keepalives.
        e.voice_push(true);
        assert_eq!(next_read(&mut inputs).await, VOICE_BACKUP);
        tokio::time::advance(VOICE_QUIET - VOICE_BACKUP).await;
        e.stream_heard();
        assert_eq!(next_read(&mut inputs).await, Duration::ZERO); // overdue: reads at once
        assert_eq!(next_read(&mut inputs).await, VOICE_BACKUP);

        // A stream gone quiet may be dead: 400 ms until it is heard again.
        tokio::time::advance(VOICE_QUIET).await;
        assert_eq!(next_read(&mut inputs).await, Duration::ZERO);
        assert_eq!(next_read(&mut inputs).await, VOICE_POLL);
        e.dispatch(
            "runner",
            None,
            &serde_json::json!({"kind": "logins"}),
            false,
        );
        assert_eq!(next_read(&mut inputs).await, VOICE_BACKUP);

        // The server stops taking voice_inputs_v1 during a backstop wait: read at once, then poll.
        let waiting = tokio::spawn({
            let e = e.clone();
            async move {
                tokio::time::sleep(Duration::from_millis(100)).await;
                e.voice_push(false);
            }
        });
        assert_eq!(next_read(&mut inputs).await, Duration::from_millis(100));
        waiting.await.unwrap();
        assert_eq!(next_read(&mut inputs).await, VOICE_POLL);

        // A new server release polls until its heartbeat says it takes the hints.
        e.voice_push(true);
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 2, "release": "next"}),
            false,
        );
        assert!(!e.voice_pushed());
    }

    #[tokio::test(start_paused = true)]
    async fn voice_inputs_poll_after_a_drop_and_fetch_on_reconnect() {
        let e = pushing();
        let mut inputs = e.voice_inputs();
        inputs.wait("attempt", "call").await;

        // The stream drops during a backstop wait: read at once, then the 400 ms poll.
        let dropping = tokio::spawn({
            let e = e.clone();
            async move {
                tokio::time::sleep(Duration::from_millis(100)).await;
                e.fall_back(Stream::Down);
            }
        });
        assert_eq!(next_read(&mut inputs).await, Duration::from_millis(100));
        dropping.await.unwrap();
        assert_eq!(next_read(&mut inputs).await, VOICE_POLL);

        e.dispatch("ready", None, &serde_json::json!({"seq": 1}), true);
        tokio::time::timeout(Duration::from_millis(10), inputs.wait("attempt", "call"))
            .await
            .expect("reconnecting did not wake the voice loop");
        assert_eq!(next_read(&mut inputs).await, VOICE_BACKUP);
    }

    #[test]
    fn recovery_checks_are_spread_over_15_s_give_or_take_a_fifth() {
        let e = Events::new();
        let waits: Vec<f64> = (0..500).map(|_| e.turn_recovery().as_secs_f64()).collect();
        let (low, high) = waits
            .iter()
            .fold((f64::MAX, f64::MIN), |(l, h), w| (l.min(*w), h.max(*w)));
        assert!(low >= 12.0 && high <= 18.0, "{low}..{high}");
        // Spread across the window, not bunched: many turns' checks do not land in the same second.
        assert!(low < 12.5 && high > 17.5, "{low}..{high}");
        let seconds: std::collections::HashSet<u64> = waits.iter().map(|w| *w as u64).collect();
        assert!(seconds.len() >= 6, "{seconds:?}");
    }

    #[test]
    fn a_restarting_server_is_tried_every_few_seconds_for_a_while_then_on_the_backoff() {
        let soon = Some(Duration::from_secs(5));
        assert_eq!(retry_after(16.0, true, soon), QUICK_RETRY_S);
        assert_eq!(retry_after(1.0, true, soon), 1.0);
        // An answer from the server itself, a stream that never was up, or a long outage: the backoff stands.
        assert_eq!(retry_after(16.0, false, soon), 16.0);
        assert_eq!(retry_after(16.0, true, None), 16.0);
        let late = Some(Duration::from_secs(QUICK_WINDOW_S));
        assert_eq!(retry_after(16.0, true, late), 16.0);
    }

    #[test]
    fn a_resumed_stream_reads_nothing_again_and_a_new_release_reads_everything_within_the_spread() {
        let e = Events::new();
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 5, "after": 5, "release": "v1"}),
            true,
        );
        // A fresh start (no resume): everything at once, as before.
        assert!(e.take("logins") && e.take("sync") && e.catch_up_in().is_none());
        e.pending.store(0, Ordering::SeqCst);
        let before = e.heard("acme");
        // The stream drops and comes back on the same release; the server took the resume: it sends what changed.
        *e.resume.lock().unwrap() = Some(5);
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 9, "after": 5, "release": "v1"}),
            true,
        );
        assert!(ALL.iter().all(|c| !e.take(c)) && e.heard("acme") == before);
        // A server that did not take it (it answers another `after`): queued work, the heartbeat and running turns
        // at once, then everything else within the spread, without telling the turns twice.
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 9, "after": 9, "release": "v1"}),
            true,
        );
        hold(&e);
        assert!(e.take("claim") && e.take("heartbeat") && !e.take("logins"));
        let told = e.heard("acme");
        assert!(told > before);
        due_now(&e);
        assert!(e.take("logins") && e.heard("acme") == told);
        // A new release, resumed: nothing yet, then everything once its moment within the spread comes.
        let before = e.heard("acme");
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 12, "after": 5, "release": "v2"}),
            true,
        );
        let at = e.catch_up_at.lock().unwrap().expect("a catch-up is due");
        assert!(at <= Instant::now() + Duration::from_secs_f64(CATCH_UP_SPREAD_S));
        *e.catch_up_at.lock().unwrap() = Some(Instant::now());
        assert!(e.take("harness") && e.take("imports") && !e.take("keys"));
        assert!(e.heard("acme") > before && e.catch_up_at.lock().unwrap().is_none());
    }

    #[test]
    fn a_resumed_stream_reads_again_what_it_took_just_before_the_drop_and_a_reset_drops_the_spread_pass(
    ) {
        let e = Events::new();
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 5, "after": 5, "release": "v1"}),
            true,
        );
        e.pending.store(0, Ordering::SeqCst);
        // A refresh hint taken (its read then failed against the stopping server), and the stream drops.
        e.ask(&["refresh"]);
        assert!(e.take("refresh"));
        *e.down_since.lock().unwrap() = Some(Instant::now());
        *e.resume.lock().unwrap() = Some(5);
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 9, "after": 5, "release": "v1"}),
            true,
        );
        assert!(e.take("refresh") && !e.take("logins") && !e.take("claim"));
        // A server behind our `after` (a restored database) did not take the resume: everything.
        *e.down_since.lock().unwrap() = Some(Instant::now());
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 3, "after": 5, "release": "v1"}),
            true,
        );
        hold(&e);
        assert!(e.take("claim") && !e.take("logins"));
        due_now(&e);
        assert!(e.take("logins") && e.take("sync"));
        e.pending.store(0, Ordering::SeqCst);
        // A new release schedules its pass; a `reset` then asks for queued work at once and keeps that one pass.
        e.dispatch(
            "ready",
            None,
            &serde_json::json!({"seq": 12, "after": 5, "release": "v2"}),
            false,
        );
        hold(&e);
        let at = e.catch_up_at.lock().unwrap().expect("a catch-up is due");
        let before = e.heard("acme");
        e.dispatch("reset", None, &serde_json::json!({}), false);
        assert_eq!(*e.catch_up_at.lock().unwrap(), Some(at));
        assert!(e.take("claim") && !e.take("logins") && e.heard("acme") > before);
        // The release's pass still tells running turns: theirs may need the new release's settings.
        let told = e.heard("acme");
        due_now(&e);
        assert!(e.take("logins") && e.heard("acme") > told);
    }

    /// The spread catch-up pass, held off while a test reads what was asked at once.
    fn hold(e: &Events) {
        assert!(e.catch_up_in().is_some(), "a catch-up pass waits");
        *e.catch_up_at.lock().unwrap() = Some(Instant::now() + Duration::from_secs(3600));
    }

    /// The spread catch-up pass, due now.
    fn due_now(e: &Events) {
        assert!(e.catch_up_in().is_some(), "a catch-up pass waits");
        *e.catch_up_at.lock().unwrap() = Some(Instant::now());
    }

    #[test]
    fn reconnects_wait_between_half_and_all_of_their_delay() {
        let waits: Vec<f64> = (0..200).map(|_| jittered(2.0)).collect();
        assert!(waits.iter().all(|w| (1.0..=2.0).contains(w)), "{waits:?}");
        // Spread, not one shared instant.
        let (low, high) = waits
            .iter()
            .fold((f64::MAX, f64::MIN), |(l, h), w| (l.min(*w), h.max(*w)));
        assert!(high - low > 0.5, "{low}..{high}");
    }

    #[test]
    fn a_drop_after_a_stable_stream_restarts_the_backoff_and_a_quick_one_does_not() {
        assert_eq!(backoff_after_drop(32.0, Duration::from_secs(STABLE_S)), 1.0);
        assert_eq!(
            backoff_after_drop(32.0, Duration::from_secs(STABLE_S - 1)),
            32.0
        );
    }

    #[tokio::test]
    async fn a_quiet_body_reads_as_a_timeout_and_a_cut_one_as_the_connection() {
        use tokio::io::AsyncWriteExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let at = listener.local_addr().unwrap();
        tokio::spawn(async move {
            for close in [false, true] {
                let (mut sock, _) = listener.accept().await.unwrap();
                let mut buf = [0u8; 1024];
                let _ = tokio::io::AsyncReadExt::read(&mut sock, &mut buf).await;
                sock.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 100\r\n\r\n: keepalive\n\n").await.unwrap();
                if close {
                    drop(sock);
                } else {
                    tokio::spawn(async move {
                        tokio::time::sleep(Duration::from_secs(5)).await;
                        drop(sock)
                    });
                }
            }
        });
        for expected in ["TimeoutError: no data", "ConnectionError: "] {
            let http = http_client(Some(Duration::from_millis(300)));
            let mut body = http
                .get(format!("http://{at}/"))
                .send()
                .await
                .unwrap()
                .bytes_stream();
            let err = loop {
                match body.next().await {
                    Some(Ok(_)) => continue,
                    Some(Err(e)) => break e,
                    None => panic!("the body ended without an error"),
                }
            };
            let why = body_error(&err);
            assert!(why.starts_with(expected), "{why}");
        }
    }
}
