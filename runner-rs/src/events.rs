//! The server's word on what changed for this computer (`runner/runner_events.py`): `GET runners/me/events`
//! held open, each `runner` event naming the reads it covers. While the stream is down the runner polls as
//! before; reconnects back off to RECONNECT_MAX_S and resume from the last event id. A server without the
//! endpoint (404/405) is polled as before and asked again every LEGACY_RETRY_S.

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::Value;
use tokio::sync::Notify;

use crate::client::{bearer, http_client, quote_plus, Client, CLIENT_VERSION};
use crate::util::{log, mark};

pub const BACKUP_POLL_S: f64 = 300.0;
pub const RECONNECT_MAX_S: f64 = 60.0;
pub const LEGACY_RETRY_S: f64 = 1800.0;
pub const READ_TIMEOUT_S: u64 = 45;
pub const HEARTBEAT_LIVE_S: f64 = 60.0;
pub const ALL: &[&str] = &["claim", "sync", "config", "heartbeat", "logins", "harness", "imports", "refresh"];

/// What each event kind asks the runner to read again.
pub fn channels(kind: &str) -> &'static [&'static str] {
    match kind {
        "work" => &["claim"],
        "assignments" | "credentials" | "repositories" => &["sync"],
        "config" => &["sync", "config"],
        "worktrees" | "restart" => &["heartbeat"],
        "logins" => &["logins"],
        "harness_actions" => &["harness"],
        "credential_imports" => &["imports"],
        "subscription_refresh" => &["refresh"],
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
                        log(&format!("Tico runner: event stream lost ({why}); polling until it is back"));
                    } else if !said_down {
                        said_down = true;
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
                Some(Err(e)) => return Err(Failure::Broken(format!("TimeoutError: {e}"))),
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
                self.wake.notify_one();
            }
            "runner" => {
                let kind = data["kind"].as_str().unwrap_or_default();
                if kind == "work" {
                    mark("heard", "-");
                }
                self.ask(channels(kind));
            }
            "reset" => self.ask(ALL),
            _ => {}
        }
        if let Some(id) = ident.filter(|i| !i.is_empty()) {
            *self.last_id.lock().unwrap() = Some(id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kinds_map_to_the_python_channels() {
        assert_eq!(channels("work"), &["claim"]);
        assert_eq!(channels("config"), &["sync", "config"]);
        assert_eq!(channels("something new"), ALL);
        let e = Events::new();
        e.dispatch("ready", Some("7".into()), &serde_json::json!({"seq": 7, "release": "v1"}), true);
        assert!(e.live() && e.take("claim") && !e.take("claim"));
        e.pending.lock().unwrap().clear();
        e.dispatch("runner", Some("8".into()), &serde_json::json!({"kind": "work"}), false);
        assert!(e.take("claim") && !e.take("sync"));
        assert_eq!(e.last_id.lock().unwrap().as_deref(), Some("8"));
    }
}
