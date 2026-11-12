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
use tokio::sync::Notify;

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
/// How often a running turn asks for its inputs and goal with the stream up and no notice for its bot: the backstop
/// for a notice that went missing. Each wait is jittered by TURN_RECOVERY_JITTER so turns do not line up.
pub const TURN_RECOVERY_S: f64 = 15.0;
pub const TURN_RECOVERY_JITTER: f64 = 0.2;
/// Every channel the runner reads on its own clock. A channel's bit in `Events::pending` is its index here.
pub const CHANNELS: [&str; 9] = [
    "claim",
    "sync",
    "config",
    "heartbeat",
    "logins",
    "harness",
    "imports",
    "refresh",
    "keys",
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
];

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
        "subscription_refresh" => &["refresh"],
        "screen_open" => &[], // crate::screen polls its sessions at once
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
}

pub struct Events {
    /// The channels the server asked for since each was last taken, one bit per channel (`index`).
    pending: AtomicU16,
    state: Mutex<Stream>,
    last_id: Mutex<Option<String>>,
    /// When the stream was last said to be lost or unavailable, until it is said to be back.
    down_since: Mutex<Option<Instant>>,
    pub release: Mutex<Option<Value>>,
    /// Woken whenever the server asks for something, so the loop acts on it at once.
    pub wake: Notify,
    stop: AtomicBool,
    /// Notices for running turns: (for every bot, per bot). `heard` adds the two, so a turn sees either move.
    notices: Mutex<(u64, HashMap<String, u64>)>,
    /// TURN_RECOVERY_S in milliseconds; tests shorten it.
    recovery_ms: AtomicU64,
}

impl Events {
    pub fn new() -> Arc<Events> {
        Arc::new(Events {
            pending: AtomicU16::new(0),
            state: Mutex::new(Stream::Down),
            last_id: Mutex::new(None),
            down_since: Mutex::new(None),
            release: Mutex::new(None),
            wake: Notify::new(),
            stop: AtomicBool::new(false),
            notices: Mutex::new((0, HashMap::new())),
            recovery_ms: AtomicU64::new((TURN_RECOVERY_S * 1000.0) as u64),
        })
    }

    /// How many notices a running turn of `bot` has had: one that differs from the count at its last ask means ask
    /// again now. Take it before the request, so a notice that lands during the request is not lost.
    pub fn heard(&self, bot: &str) -> u64 {
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
        let bit = bit(channel);
        self.pending.fetch_and(!bit, Ordering::SeqCst) & bit != 0
    }

    pub fn ask(&self, channels: &[&'static str]) {
        let asked = channels.iter().fold(0, |set, channel| set | bit(channel));
        self.pending.fetch_or(asked, Ordering::SeqCst);
        self.wake.notify_one();
    }

    pub fn close(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub async fn run(self: Arc<Self>, client: Client) {
        let mut delay = 1.0f64;
        let mut legacy_until = Instant::now();
        let mut said_down = false;
        while !self.stop.load(Ordering::SeqCst) {
            if self.state() == Stream::Legacy && Instant::now() < legacy_until {
                tokio::time::sleep((legacy_until - Instant::now()).min(Duration::from_secs(60)))
                    .await;
                continue;
            }
            let started = Instant::now();
            let clean = match self.read_once(&client).await {
                Ok(clean) => clean,
                Err(Failure::NotSupported) => {
                    if self.state() != Stream::Legacy {
                        log("Tico runner: this server has no event stream; polling as before");
                    }
                    *self.state.lock().unwrap() = Stream::Legacy;
                    legacy_until = Instant::now() + Duration::from_secs_f64(LEGACY_RETRY_S);
                    continue;
                }
                Err(Failure::Broken(why)) => {
                    if self.state() == Stream::Up {
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
                    false
                }
            };
            if clean {
                delay = 1.0;
                continue; // the server's own lifetime ran out: reconnect at once
            }
            *self.state.lock().unwrap() = Stream::Down;
            tokio::time::sleep(Duration::from_secs_f64(delay)).await;
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
            .map_err(|e| Failure::Broken(format!("URLError: {e}")))?;
        let status = response.status().as_u16();
        if status == 404 || status == 405 {
            return Err(Failure::NotSupported);
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
                    // keepalive
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
                // Back after an outage or on a new server release: one full pass catches up on anything unsaid.
                if recovering || changed {
                    self.ask(ALL);
                    self.notice(&[]);
                }
                *self.release.lock().unwrap() = Some(release);
                *self.state.lock().unwrap() = Stream::Up;
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
            "reset" => {
                self.ask(ALL);
                self.notice(&[]);
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
        for kind in ["logins", "config", "credentials", "screen_open"] {
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
