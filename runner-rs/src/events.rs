//! The server's word on what changed for this computer (`runner/runner_events.py`): `GET runners/me/events`
//! held open, each `runner` event naming the reads it covers. While the stream is down the runner polls as
//! before; reconnects back off to RECONNECT_MAX_S and resume from the last event id. A server without the
//! endpoint (404/405) is polled as before and asked again every LEGACY_RETRY_S. A stream that comes back after it
//! was lost or unavailable says so, so a support bundle shows the outage ended.

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::Value;
use tokio::sync::Notify;

use crate::client::{bearer, http_client, quote_plus, Client, CLIENT_VERSION};
use crate::util::{diagnostic, log, mark};

pub const BACKUP_POLL_S: f64 = 300.0;
pub const RECONNECT_MAX_S: f64 = 60.0;
pub const LEGACY_RETRY_S: f64 = 1800.0;
/// The server sends a keepalive every 15 s by default and at most every 50 s (backend/events.py).
pub const READ_TIMEOUT_S: u64 = 90;
pub const HEARTBEAT_LIVE_S: f64 = 60.0;
/// A stream that stayed up this long before it dropped had a fresh drop: its reconnect backoff starts over.
pub const STABLE_S: u64 = 30;
pub const ALL: &[&str] = &["claim", "sync", "config", "heartbeat", "logins", "harness", "imports", "refresh"];

/// What each event kind asks the runner to read again.
pub fn channels(kind: &str) -> &'static [&'static str] {
    match kind {
        "work" => &["claim"],
        "assignments" | "repositories" => &["sync"],
        // A credential's value changed: the model keys from the app are asked for again at once (signin.rs).
        "credentials" => &["sync", "keys"],
        "config" => &["sync", "config"],
        "worktrees" | "restart" | "performance" => &["heartbeat"],
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
    pending: Mutex<HashSet<&'static str>>,
    state: Mutex<Stream>,
    last_id: Mutex<Option<String>>,
    /// When the stream was last said to be lost or unavailable, until it is said to be back.
    down_since: Mutex<Option<Instant>>,
    pub release: Mutex<Option<Value>>,
    /// Woken whenever the server asks for something, so the loop acts on it at once.
    pub wake: Notify,
    stop: AtomicBool,
}

impl Events {
    pub fn new() -> Arc<Events> {
        Arc::new(Events {
            pending: Mutex::new(HashSet::new()),
            state: Mutex::new(Stream::Down),
            last_id: Mutex::new(None),
            down_since: Mutex::new(None),
            release: Mutex::new(None),
            wake: Notify::new(),
            stop: AtomicBool::new(false),
        })
    }

    pub fn live(&self) -> bool {
        *self.state.lock().unwrap() == Stream::Up
    }

    pub fn state(&self) -> Stream {
        *self.state.lock().unwrap()
    }

    /// True once for each time the server asked for `channel` since it was last taken.
    pub fn take(&self, channel: &str) -> bool {
        let mut p = self.pending.lock().unwrap();
        let found = p.iter().find(|c| **c == channel).copied();
        found.is_some_and(|c| p.remove(c))
    }

    pub fn ask(&self, channels: &[&'static str]) {
        self.pending.lock().unwrap().extend(channels.iter().copied());
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
                tokio::time::sleep((legacy_until - Instant::now()).min(Duration::from_secs(60))).await;
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
                        log(&format!("Tico runner: event stream lost ({why}); polling until it is back"));
                        *self.down_since.lock().unwrap() = Some(Instant::now());
                    } else if !said_down {
                        said_down = true;
                        *self.down_since.lock().unwrap() = Some(Instant::now());
                        log(&format!("Tico runner: event stream unavailable ({}); polling", why.chars().take(120).collect::<String>()));
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
        let query = after.as_ref().map(|a| format!("?after={}", quote_plus(a))).unwrap_or_default();
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
        let response = req.send().await.map_err(|e| Failure::Broken(format!("URLError: {e}")))?;
        let status = response.status().as_u16();
        if status == 404 || status == 405 {
            return Err(Failure::NotSupported);
        }
        if !(200..300).contains(&status) {
            return Err(Failure::Broken(format!("HTTPError: HTTP {status}")));
        }
        let mut body = response.bytes_stream();
        let mut buffer: Vec<u8> = vec![];
        let (mut name, mut ident, mut data): (Option<String>, Option<String>, Value) = (None, None, Value::Null);
        loop {
            let chunk = match body.next().await {
                None => break,
                Some(Ok(c)) => c,
                Some(Err(e)) => return Err(Failure::Broken(body_error(&e))),
            };
            buffer.extend_from_slice(&chunk);
            while let Some(at) = buffer.iter().position(|b| *b == b'\n') {
                let raw: Vec<u8> = buffer.drain(..=at).collect();
                let line = String::from_utf8_lossy(&raw).trim_end_matches(['\r', '\n']).to_string();
                if line.is_empty() {
                    if let Some(event) = name.take() {
                        let payload = std::mem::replace(&mut data, Value::Null);
                        let payload = if payload.is_null() { Value::Object(Default::default()) } else { payload };
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
                        "data" => data = serde_json::from_str(value).unwrap_or(Value::Object(Default::default())),
                        "event" => name = Some(value.to_string()),
                        "id" => ident = Some(value.to_string()),
                        _ => {}
                    }
                }
            }
        }
        Ok(self.live())
    }

    fn dispatch(&self, event: &str, ident: Option<String>, data: &Value, recovering: bool) {
        match event {
            "ready" => {
                let release = data.get("release").cloned().unwrap_or(Value::Null);
                {
                    let mut last = self.last_id.lock().unwrap();
                    if last.is_none() {
                        let seq = crate::util::text_of(data.get("seq").unwrap_or(&Value::Null));
                        *last = (!seq.is_empty()).then_some(seq);
                    }
                }
                let changed = self.release.lock().unwrap().as_ref().is_some_and(|r| *r != release);
                // Back after an outage or on a new server release: one full pass catches up on anything unsaid.
                if recovering || changed {
                    self.ask(ALL);
                }
                *self.release.lock().unwrap() = Some(release);
                *self.state.lock().unwrap() = Stream::Up;
                if let Some(since) = self.down_since.lock().unwrap().take() {
                    diagnostic(&format!("Tico runner: event stream back after {} s; polling stops", since.elapsed().as_secs()));
                }
                self.wake.notify_one();
            }
            "runner" => {
                let kind = data["kind"].as_str().unwrap_or_default();
                if kind == "work" {
                    mark("heard", "-");
                }
                if kind == "screen_open" {
                    crate::screen::heard();
                }
                self.ask(channels(kind));
            }
            "reset" => self.ask(ALL),
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
    if lasted >= Duration::from_secs(STABLE_S) { 1.0 } else { delay }
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
        assert_eq!(channels("something new"), ALL);
        let e = Events::new();
        e.dispatch("ready", Some("7".into()), &serde_json::json!({"seq": 7, "release": "v1"}), true);
        assert!(e.live() && e.take("claim") && !e.take("claim"));
        e.pending.lock().unwrap().clear();
        e.dispatch("runner", Some("8".into()), &serde_json::json!({"kind": "work"}), false);
        assert!(e.take("claim") && !e.take("sync"));
        assert_eq!(e.last_id.lock().unwrap().as_deref(), Some("8"));
    }

    #[test]
    fn a_drop_after_a_stable_stream_restarts_the_backoff_and_a_quick_one_does_not() {
        assert_eq!(backoff_after_drop(32.0, Duration::from_secs(STABLE_S)), 1.0);
        assert_eq!(backoff_after_drop(32.0, Duration::from_secs(STABLE_S - 1)), 32.0);
    }

    #[test]
    fn a_stream_back_after_an_outage_says_so_once() {
        let e = Events::new();
        e.dispatch("ready", None, &serde_json::json!({"seq": 1, "release": "v1"}), true);
        let back = |lines: Vec<String>| lines.iter().filter(|l| l.contains("event stream back")).count();
        let before = back(crate::util::recent_errors());
        *e.down_since.lock().unwrap() = Some(Instant::now());
        e.dispatch("ready", None, &serde_json::json!({"seq": 2, "release": "v1"}), true);
        e.dispatch("ready", None, &serde_json::json!({"seq": 3, "release": "v1"}), true);
        assert_eq!(back(crate::util::recent_errors()), before + 1);
        assert!(e.down_since.lock().unwrap().is_none());
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
                    tokio::spawn(async move { tokio::time::sleep(Duration::from_secs(5)).await; drop(sock) });
                }
            }
        });
        for expected in ["TimeoutError: no data", "ConnectionError: "] {
            let http = http_client(Some(Duration::from_millis(300)));
            let mut body = http.get(format!("http://{at}/")).send().await.unwrap().bytes_stream();
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
