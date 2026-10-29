//! Exit for bots: a bot's browser traffic to a listed site leaves through another computer (backend/exits.py).
//!
//! Every runner keeps at most one link here, `GET /api/v2/runners/me/exit/ws` with its runner bearer, and one link
//! carries both roles: streams a bot on that computer opens (odd ids, chosen by the runner) and streams this relay
//! opens on it as an exit (even ids, chosen here). Binary frames only: 1 byte kind, 4 byte stream id (big endian),
//! payload.
//!
//! - OPEN    runner→relay `{"attempt","host","port"}`; relay→exit `{"host","port"}`.
//! - OPENED  exit→relay→runner once the exit has connected to the vetted address.
//! - DATA    at most 32 KiB, never more than the receiver has granted (256 KiB a direction to start).
//! - WINDOW  4 byte credit the receiver grants back after it has written data on.
//! - CLOSE   `{"code","detail"}`, either side; the stream is gone afterwards.
//! - FIN     the sender is done writing (half-close).
//! - SERVING stream 0, 1 byte: the computer now serves (1) or stops serving (0) as an exit.
//!
//! Python decides each stream (`open`: the run, the bot's site list, the exit's own bot list); this relay only adds
//! what Python cannot see: whether the exit's link is open. An exit with no open, serving link refuses the stream as
//! `exit_offline` ("waiting for exit computer <name>") and reports it, which makes the task wait; nothing here ever
//! connects anywhere itself, so there is no direct path to fall back to. The relay enforces each stream's credit
//! (a sender that overruns it loses the stream), the per-bot stream limit Python returns, a per-exit and a global cap,
//! and a one-hour stream lifetime. Usage is summed per (run, host, port, exit) and reported every two seconds.
use super::App;
use axum::{
    body::Bytes,
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::{mpsc, Notify};

pub(super) const PATH: &str = "/api/v2/runners/me/exit/ws";
const AUTHORIZE: &str = "/_tico/exit/v1/authorize";
pub(super) const WINDOW: u32 = 256 * 1024;
pub(super) const CHUNK: usize = 32 * 1024;
const FRAME_BYTES: usize = CHUNK + 1024;
const EXIT_STREAMS: usize = 256;
const ALL_STREAMS: usize = 4096;
const LINKS: usize = 4096;
const PENDING: usize = 64;
const LIFETIME: Duration = Duration::from_secs(3600);
const PING: Duration = Duration::from_secs(20);
const IDLE: Duration = Duration::from_secs(60);
const WRITE: Duration = Duration::from_secs(5);
const QUEUE: usize = 512;
const USAGE_ROWS: usize = 1024;
const OFFLINE_ROWS: usize = 256;
pub(super) const FLUSH: Duration = Duration::from_secs(2);

pub(super) mod frame {
    pub const OPEN: u8 = 1;
    pub const OPENED: u8 = 2;
    pub const DATA: u8 = 3;
    pub const WINDOW: u8 = 4;
    pub const CLOSE: u8 = 5;
    pub const FIN: u8 = 6;
    pub const SERVING: u8 = 7;

    pub fn encode(kind: u8, id: u32, payload: &[u8]) -> Vec<u8> {
        let mut out = Vec::with_capacity(5 + payload.len());
        out.push(kind);
        out.extend_from_slice(&id.to_be_bytes());
        out.extend_from_slice(payload);
        out
    }

    pub fn decode(data: &[u8]) -> Option<(u8, u32, &[u8])> {
        if data.len() < 5 || !(OPEN..=SERVING).contains(&data[0]) {
            return None;
        }
        Some((data[0], u32::from_be_bytes([data[1], data[2], data[3], data[4]]), &data[5..]))
    }

    pub fn close(id: u32, code: &str, detail: &str) -> Vec<u8> {
        encode(CLOSE, id, serde_json::json!({"code": code, "detail": detail}).to_string().as_bytes())
    }

    pub fn window(id: u32, credit: u32) -> Vec<u8> {
        encode(WINDOW, id, &credit.to_be_bytes())
    }
}

/// A frame for a link: (link, bytes).
pub(super) type Out = Vec<(u64, Vec<u8>)>;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct Key {
    attempt: String,
    host: String,
    port: u16,
    exit: String,
}

#[derive(Default, Debug)]
struct Usage {
    opened: u64,
    open: u64,
    up: u64,
    down: u64,
    dirty: bool,
}

struct Link {
    rid: String,
    name: String,
    serving: bool,
    token: String,
    tx: Option<mpsc::Sender<Message>>,
    kick: Arc<Notify>,
    next_id: u32,
    pending: HashSet<u32>,
}

struct Stream {
    origin: (u64, u32),
    exit: (u64, u32),
    bot: String,
    key: Key,
    /// Bytes the origin may still send toward the exit, and the exit toward the origin.
    up: u32,
    down: u32,
    opened: bool,
    started: Instant,
}

/// What a runner asked to open, waiting for Python's verdict.
#[derive(Clone, Debug)]
pub(super) struct Ask {
    pub rid: String,
    pub attempt: String,
    pub host: String,
    pub port: u16,
}

#[derive(Default)]
pub(super) struct Hub {
    next_conn: u64,
    next_stream: u64,
    links: HashMap<u64, Link>,
    runners: HashMap<String, u64>,
    streams: HashMap<u64, Stream>,
    ends: HashMap<(u64, u32), u64>,
    per_bot: HashMap<String, usize>,
    per_exit: HashMap<u64, usize>,
    usage: HashMap<Key, Usage>,
    offline: Vec<Value>,
}

impl Hub {
    /// A runner's link; a newer link for the same runner replaces the older one and its streams.
    fn attach(&mut self, rid: &str, name: &str, serving: bool, token: String, tx: Option<mpsc::Sender<Message>>)
              -> (u64, Arc<Notify>, Out) {
        let mut out = Vec::new();
        if let Some(old) = self.runners.get(rid).copied() {
            if let Some(link) = self.links.get(&old) {
                link.kick.notify_one();
            }
            out = self.detach(old);
        }
        self.next_conn += 1;
        let conn = self.next_conn;
        let kick = Arc::new(Notify::new());
        self.links.insert(conn, Link {
            rid: rid.into(), name: name.into(), serving, token, tx, kick: kick.clone(), next_id: 2,
            pending: HashSet::new(),
        });
        self.runners.insert(rid.into(), conn);
        (conn, kick, out)
    }

    fn full(&self) -> bool {
        self.links.len() >= LINKS
    }

    /// The link is gone: every stream through it closes on its other side.
    fn detach(&mut self, conn: u64) -> Out {
        let Some(link) = self.links.remove(&conn) else { return Vec::new() };
        if self.runners.get(&link.rid) == Some(&conn) {
            self.runners.remove(&link.rid);
        }
        let ids: Vec<u64> = self.streams.iter()
            .filter(|(_, s)| s.origin.0 == conn || s.exit.0 == conn)
            .map(|(id, _)| *id).collect();
        let mut out = Vec::new();
        for id in ids {
            let stream = self.remove(id);
            if stream.origin.0 == conn {
                out.push((stream.exit.0, frame::close(stream.exit.1, "closed", "the bot's computer went away")));
            } else {
                let detail = format!("exit computer {} went offline", link.name);
                out.push((stream.origin.0, frame::close(stream.origin.1, "exit_offline", &detail)));
            }
        }
        out
    }

    /// The computer starts or stops serving as an exit; stopping ends the streams it carries.
    fn set_serving(&mut self, conn: u64, serving: bool) -> Out {
        let Some(link) = self.links.get_mut(&conn) else { return Vec::new() };
        link.serving = serving;
        if serving {
            return Vec::new();
        }
        let name = link.name.clone();
        let ids: Vec<u64> = self.streams.iter().filter(|(_, s)| s.exit.0 == conn).map(|(id, _)| *id).collect();
        let mut out = Vec::new();
        for id in ids {
            let stream = self.remove(id);
            out.push((stream.exit.0, frame::close(stream.exit.1, "exit_off", "no longer an exit")));
            let detail = format!("exit computer {name} stopped carrying traffic");
            out.push((stream.origin.0, frame::close(stream.origin.1, "exit_off", &detail)));
        }
        out
    }

    fn remove(&mut self, id: u64) -> Stream {
        let stream = self.streams.remove(&id).expect("a stream being removed exists");
        self.ends.remove(&stream.origin);
        self.ends.remove(&stream.exit);
        if let Some(n) = self.per_bot.get_mut(&stream.bot) {
            *n -= 1;
            if *n == 0 {
                self.per_bot.remove(&stream.bot);
            }
        }
        if let Some(n) = self.per_exit.get_mut(&stream.exit.0) {
            *n -= 1;
            if *n == 0 {
                self.per_exit.remove(&stream.exit.0);
            }
        }
        if let Some(u) = self.usage.get_mut(&stream.key) {
            u.open = u.open.saturating_sub(1);
            u.dirty = true;
        }
        stream
    }

    /// An OPEN from a runner: what to ask Python, or the refusal to send back at once.
    fn begin_open(&mut self, conn: u64, id: u32, payload: &[u8]) -> Result<Ask, Out> {
        let refuse = |code: &str, detail: &str| Err(vec![(conn, frame::close(id, code, detail))]);
        let Some(link) = self.links.get_mut(&conn) else { return Err(Vec::new()) };
        if id.is_multiple_of(2) || self.ends.contains_key(&(conn, id)) || link.pending.contains(&id) {
            return refuse("invalid", "stream ids from a computer are odd and new");
        }
        if link.pending.len() >= PENDING {
            return refuse("busy", "too many connections opening at once");
        }
        let body: Value = serde_json::from_slice(payload).unwrap_or(Value::Null);
        let text = |k: &str| body[k].as_str().filter(|v| !v.is_empty() && v.len() <= 255).map(str::to_owned);
        let (Some(attempt), Some(host), Some(port)) = (text("attempt"), text("host"),
                                                        body["port"].as_u64().and_then(|p| u16::try_from(p).ok()))
        else {
            return refuse("invalid", "an open names the run, host and port");
        };
        link.pending.insert(id);
        Ok(Ask { rid: link.rid.clone(), attempt, host, port })
    }

    /// Python's verdict on an ask: refuse, refuse as offline (and report it), or open the stream on the exit.
    fn finish_open(&mut self, conn: u64, id: u32, ask: &Ask, verdict: &Value, at: &str) -> Out {
        let Some(link) = self.links.get_mut(&conn) else { return Vec::new() };
        if !link.pending.remove(&id) {
            return Vec::new(); // the runner closed it while Python was deciding
        }
        let refuse = |code: &str, detail: &str| vec![(conn, frame::close(id, code, detail))];
        if verdict["allowed"] != Value::Bool(true) {
            let code = verdict["code"].as_str().unwrap_or("forbidden");
            return refuse(code, verdict["detail"].as_str().unwrap_or("not allowed through an exit"));
        }
        let text = |k: &str| verdict[k].as_str().unwrap_or("").to_owned();
        let (bot, host, exit_ref, exit_rid, name) =
            (text("bot"), text("host"), text("exit"), text("exit_runner"), text("name"));
        let limit = verdict["limit"].as_u64().unwrap_or(32) as usize;
        let exit_conn = self.runners.get(&exit_rid).copied().filter(|c| self.links.get(c).is_some_and(|l| l.serving));
        let Some(exit_conn) = exit_conn else {
            if self.offline.len() < OFFLINE_ROWS {
                self.offline.push(json!({"attempt": ask.attempt, "host": host, "port": ask.port, "exit": exit_ref,
                                         "at": at, "refused": 1}));
            }
            return refuse("exit_offline", &format!("waiting for exit computer {name}"));
        };
        if self.per_bot.get(&bot).copied().unwrap_or(0) >= limit {
            return refuse("busy", &format!("this bot already has {limit} connections through exits"));
        }
        if self.per_exit.get(&exit_conn).copied().unwrap_or(0) >= EXIT_STREAMS || self.streams.len() >= ALL_STREAMS {
            return refuse("busy", &format!("exit computer {name} is carrying all it can"));
        }
        let key = Key { attempt: ask.attempt.clone(), host: host.clone(), port: ask.port, exit: exit_ref };
        if !self.usage.contains_key(&key) && self.usage.len() >= USAGE_ROWS {
            return refuse("busy", "too many sites through exits at once");
        }
        let exit_link = self.links.get_mut(&exit_conn).expect("checked above");
        let exit_id = exit_link.next_id;
        exit_link.next_id = exit_link.next_id.wrapping_add(2).max(2);
        self.next_stream += 1;
        let sid = self.next_stream;
        self.ends.insert((conn, id), sid);
        self.ends.insert((exit_conn, exit_id), sid);
        *self.per_bot.entry(bot.clone()).or_default() += 1;
        *self.per_exit.entry(exit_conn).or_default() += 1;
        let usage = self.usage.entry(key.clone()).or_default();
        usage.opened += 1;
        usage.open += 1;
        usage.dirty = true;
        self.streams.insert(sid, Stream {
            origin: (conn, id), exit: (exit_conn, exit_id), bot, key, up: WINDOW, down: WINDOW, opened: false,
            started: Instant::now(),
        });
        let open = json!({"host": host, "port": ask.port}).to_string();
        vec![(exit_conn, frame::encode(frame::OPEN, exit_id, open.as_bytes()))]
    }

    /// Any frame but OPEN and SERVING, from link `conn`.
    fn on_frame(&mut self, conn: u64, kind: u8, id: u32, payload: &[u8]) -> Out {
        let Some(&sid) = self.ends.get(&(conn, id)) else {
            if kind == frame::CLOSE {
                if let Some(link) = self.links.get_mut(&conn) {
                    link.pending.remove(&id);
                }
            }
            return Vec::new(); // a frame racing its stream's close
        };
        let stream = self.streams.get_mut(&sid).expect("every end names a stream");
        let from_origin = stream.origin == (conn, id);
        let other = if from_origin { stream.exit } else { stream.origin };
        match kind {
            frame::DATA => {
                let credit = if from_origin { &mut stream.up } else { &mut stream.down };
                if payload.len() > CHUNK || payload.len() as u64 > *credit as u64 || (from_origin && !stream.opened) {
                    return self.kill(sid, "window", "sent more than the other side asked for");
                }
                *credit -= payload.len() as u32;
                let n = payload.len() as u64;
                let key = stream.key.clone();
                let usage = self.usage.entry(key).or_default();
                if from_origin { usage.up += n } else { usage.down += n }
                usage.dirty = true;
                vec![(other.0, frame::encode(frame::DATA, other.1, payload))]
            }
            frame::WINDOW => {
                let Ok(raw) = <[u8; 4]>::try_from(payload) else { return self.kill(sid, "invalid", "bad window") };
                let grant = u32::from_be_bytes(raw);
                // The receiver of one direction grants credit to its sender: the exit's grant is for uploads.
                let credit = if from_origin { &mut stream.down } else { &mut stream.up };
                *credit = credit.saturating_add(grant).min(WINDOW);
                vec![(other.0, frame::window(other.1, grant))]
            }
            frame::OPENED if !from_origin && !stream.opened => {
                stream.opened = true;
                vec![(other.0, frame::encode(frame::OPENED, other.1, &[]))]
            }
            frame::FIN => vec![(other.0, frame::encode(frame::FIN, other.1, &[]))],
            frame::CLOSE => {
                let body = if payload.len() <= 1024 { payload.to_vec() } else { Vec::new() };
                self.remove(sid);
                vec![(other.0, frame::encode(frame::CLOSE, other.1, &body))]
            }
            _ => self.kill(sid, "invalid", "unexpected frame"),
        }
    }

    fn kill(&mut self, sid: u64, code: &str, detail: &str) -> Out {
        let stream = self.remove(sid);
        vec![(stream.origin.0, frame::close(stream.origin.1, code, detail)),
             (stream.exit.0, frame::close(stream.exit.1, code, detail))]
    }

    /// Streams past their lifetime close on both sides.
    fn sweep(&mut self, now: Instant) -> Out {
        let old: Vec<u64> = self.streams.iter().filter(|(_, s)| now.duration_since(s.started) >= LIFETIME)
            .map(|(id, _)| *id).collect();
        old.into_iter().flat_map(|sid| self.kill(sid, "time_limit", "connections through an exit last an hour")).collect()
    }

    /// Everything to report since the last report; `restore` puts it back when Python did not take it.
    fn take_report(&mut self) -> Option<Value> {
        let mut usage = Vec::new();
        for (key, u) in self.usage.iter_mut().filter(|(_, u)| u.dirty) {
            usage.push(json!({"attempt": key.attempt, "host": key.host, "port": key.port, "exit": key.exit,
                              "opened": u.opened, "open": u.open, "up": u.up, "down": u.down}));
            (u.opened, u.up, u.down, u.dirty) = (0, 0, 0, false);
        }
        self.usage.retain(|_, u| u.open > 0);
        let offline = std::mem::take(&mut self.offline);
        (!usage.is_empty() || !offline.is_empty()).then(|| json!({"kind": "report", "usage": usage, "offline": offline}))
    }

    fn restore(&mut self, report: &Value) {
        for row in report["usage"].as_array().into_iter().flatten() {
            let key = Key { attempt: row["attempt"].as_str().unwrap_or("").into(), host: row["host"].as_str().unwrap_or("").into(),
                            port: row["port"].as_u64().unwrap_or(0) as u16, exit: row["exit"].as_str().unwrap_or("").into() };
            if !self.usage.contains_key(&key) && self.usage.len() >= USAGE_ROWS {
                continue;
            }
            let u = self.usage.entry(key).or_default();
            u.opened += row["opened"].as_u64().unwrap_or(0);
            u.up += row["up"].as_u64().unwrap_or(0);
            u.down += row["down"].as_u64().unwrap_or(0);
            u.dirty = true;
        }
        for row in report["offline"].as_array().into_iter().flatten() {
            if self.offline.len() < OFFLINE_ROWS {
                self.offline.push(row.clone());
            }
        }
    }

    /// Each frame's link sender, taken under the lock so the sends happen outside it.
    fn senders(&self, out: Out) -> Vec<(mpsc::Sender<Message>, Arc<Notify>, Vec<u8>)> {
        out.into_iter().filter_map(|(conn, bytes)| {
            let link = self.links.get(&conn)?;
            Some((link.tx.clone()?, link.kick.clone(), bytes))
        }).collect()
    }
}

pub(super) type Shared = Arc<Mutex<Hub>>;

async fn deliver(app: &App, out: Out) {
    if out.is_empty() {
        return;
    }
    let sends = app.exits.lock().unwrap().senders(out);
    for (tx, kick, bytes) in sends {
        // Credit bounds what a link may be owed, so a full queue means a stalled computer: drop its link.
        if !matches!(tokio::time::timeout(WRITE, tx.send(Message::Binary(Bytes::from(bytes)))).await, Ok(Ok(()))) {
            kick.notify_one();
        }
    }
}

async fn ask(app: &App, body: Value) -> Option<Value> {
    let response = app.http.post(format!("{}{AUTHORIZE}", app.python)).bearer_auth(app.key.as_str())
        .json(&body).send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    response.json().await.ok()
}

pub(super) async fn link(State(app): State<App>, headers: HeaderMap, ws: WebSocketUpgrade) -> Response {
    let bearer = headers.get("authorization").and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
    if bearer.is_empty() || bearer.len() > 8192 || app.exits.lock().unwrap().full() {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Some(who) = ask(&app, json!({"kind": "attach", "authorization": bearer})).await else {
        return StatusCode::FORBIDDEN.into_response();
    };
    let (Some(rid), name, serving) = (who["runner_id"].as_str().map(str::to_owned),
                                      who["name"].as_str().unwrap_or("").to_owned(), who["serving"] == Value::Bool(true))
    else {
        return StatusCode::FORBIDDEN.into_response();
    };
    ws.max_message_size(FRAME_BYTES).max_frame_size(FRAME_BYTES)
        .on_upgrade(move |socket| serve(app, socket, rid, name, serving, bearer))
}

async fn serve(app: App, socket: WebSocket, rid: String, name: String, serving: bool, token: String) {
    let (mut sink, mut source) = socket.split();
    let (tx, mut rx) = mpsc::channel::<Message>(QUEUE);
    let (conn, kick, out) = app.exits.lock().unwrap().attach(&rid, &name, serving, token, Some(tx));
    deliver(&app, out).await;
    let writer = tokio::spawn(async move {
        let mut ping = tokio::time::interval(PING);
        loop {
            let message = tokio::select! {
                m = rx.recv() => match m { Some(m) => m, None => break },
                _ = ping.tick() => Message::Ping(Bytes::new()),
            };
            if !matches!(tokio::time::timeout(WRITE, sink.send(message)).await, Ok(Ok(()))) {
                break;
            }
        }
        let _ = sink.close().await;
    });
    loop {
        let message = tokio::select! {
            _ = kick.notified() => break,
            m = tokio::time::timeout(IDLE, source.next()) => match m {
                Ok(Some(Ok(m))) => m,
                _ => break,
            },
        };
        let data = match message {
            Message::Binary(data) => data,
            Message::Ping(_) | Message::Pong(_) => continue,
            _ => break,
        };
        let Some((kind, id, payload)) = frame::decode(&data) else { break };
        match kind {
            frame::OPEN => {
                let asked = app.exits.lock().unwrap().begin_open(conn, id, payload);
                match asked {
                    Err(out) => deliver(&app, out).await,
                    Ok(question) => {
                        let app = app.clone();
                        tokio::spawn(async move {
                            let verdict = ask(&app, json!({"kind": "open", "runner_id": question.rid,
                                                           "attempt": question.attempt, "host": question.host,
                                                           "port": question.port})).await
                                .unwrap_or_else(|| json!({"allowed": false, "code": "unavailable",
                                                           "detail": "Tico did not answer; try again"}));
                            let at = now_iso();
                            let out = app.exits.lock().unwrap().finish_open(conn, id, &question, &verdict, &at);
                            deliver(&app, out).await;
                        });
                    }
                }
            }
            frame::SERVING if id == 0 => {
                if payload.first() == Some(&1) {
                    // Python is the authority: asking again also wakes the tasks that were waiting for this exit.
                    let app = app.clone();
                    tokio::spawn(async move {
                        let token = app.exits.lock().unwrap().links.get(&conn).map(|l| l.token.clone());
                        let Some(token) = token else { return };
                        let on = ask(&app, json!({"kind": "attach", "authorization": token})).await
                            .is_some_and(|w| w["serving"] == Value::Bool(true));
                        let out = app.exits.lock().unwrap().set_serving(conn, on);
                        deliver(&app, out).await;
                    });
                } else {
                    let out = app.exits.lock().unwrap().set_serving(conn, false);
                    deliver(&app, out).await;
                }
            }
            _ => {
                let out = app.exits.lock().unwrap().on_frame(conn, kind, id, payload);
                deliver(&app, out).await;
            }
        }
    }
    let out = app.exits.lock().unwrap().detach(conn);
    writer.abort();
    deliver(&app, out).await;
}

fn now_iso() -> String {
    // RFC 3339 UTC from the system clock, without a date crate: days to civil date (Howard Hinnant's algorithm).
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let (secs, micros) = (since.as_secs() as i64, since.subsec_micros());
    let (days, rem) = (secs.div_euclid(86400), secs.rem_euclid(86400));
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + if month <= 2 { 1 } else { 0 };
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{micros:06}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}

/// Report usage and refusals every two seconds and end streams past their lifetime.
pub(super) async fn keep(app: App) {
    let mut tick = tokio::time::interval(FLUSH);
    loop {
        tick.tick().await;
        let out = app.exits.lock().unwrap().sweep(Instant::now());
        deliver(&app, out).await;
        let report = app.exits.lock().unwrap().take_report();
        if let Some(report) = report {
            if ask(&app, report.clone()).await.is_none() {
                app.exits.lock().unwrap().restore(&report);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn allowed(exit_rid: &str) -> Value {
        json!({"allowed": true, "bot": "ops", "task": "t1", "host": "shop.acme.example", "exit": format!("computer:{exit_rid}"),
               "exit_runner": exit_rid, "name": "Home Mac", "limit": 2})
    }

    fn open_body() -> Vec<u8> {
        br#"{"attempt":"a1","host":"shop.acme.example","port":443}"#.to_vec()
    }

    fn hub() -> (Hub, u64, u64) {
        let mut hub = Hub::default();
        let (origin, _, _) = hub.attach("box", "Bot box", false, "t".into(), None);
        let (exit, _, _) = hub.attach("mac", "Home Mac", true, "t".into(), None);
        (hub, origin, exit)
    }

    fn opened(hub: &mut Hub, origin: u64, id: u32) -> (u64, u32) {
        let ask = hub.begin_open(origin, id, &open_body()).unwrap();
        let out = hub.finish_open(origin, id, &ask, &allowed("mac"), "2026-10-29T00:00:00Z");
        let (to, bytes) = &out[0];
        let (kind, exit_id, payload) = frame::decode(bytes).unwrap();
        assert_eq!(kind, frame::OPEN);
        assert_eq!(serde_json::from_slice::<Value>(payload).unwrap(), json!({"host": "shop.acme.example", "port": 443}));
        let back = hub.on_frame(*to, frame::OPENED, exit_id, &[]);
        assert_eq!(back, vec![(origin, frame::encode(frame::OPENED, id, &[]))]);
        (*to, exit_id)
    }

    fn code(bytes: &[u8]) -> String {
        let (kind, _, payload) = frame::decode(bytes).unwrap();
        assert_eq!(kind, frame::CLOSE);
        serde_json::from_slice::<Value>(payload).unwrap()["code"].as_str().unwrap().to_owned()
    }

    #[test]
    fn frames_round_trip_and_garbage_is_refused() {
        let bytes = frame::encode(frame::DATA, 0x0102_0304, b"hi");
        assert_eq!(bytes, vec![3, 1, 2, 3, 4, b'h', b'i']);
        assert_eq!(frame::decode(&bytes), Some((frame::DATA, 0x0102_0304, &b"hi"[..])));
        assert_eq!(frame::decode(&[3, 0, 0]), None);
        assert_eq!(frame::decode(&[9, 0, 0, 0, 1]), None);
    }

    #[test]
    fn a_stream_opens_on_the_exit_and_carries_data_both_ways_within_its_window() {
        let (mut hub, origin, exit) = hub();
        let (to, exit_id) = opened(&mut hub, origin, 1);
        assert_eq!((to, exit_id), (exit, 2));
        let out = hub.on_frame(origin, frame::DATA, 1, b"GET /");
        assert_eq!(out, vec![(exit, frame::encode(frame::DATA, 2, b"GET /"))]);
        let out = hub.on_frame(exit, frame::DATA, 2, b"200 OK");
        assert_eq!(out, vec![(origin, frame::encode(frame::DATA, 1, b"200 OK"))]);
        // The origin spends its whole window; one more byte without a grant loses the stream on both sides.
        let chunk = vec![0u8; CHUNK];
        let mut left = WINDOW as usize - 5;
        while left > 0 {
            let n = left.min(CHUNK);
            assert_eq!(hub.on_frame(origin, frame::DATA, 1, &chunk[..n]).len(), 1);
            left -= n;
        }
        // The exit grants credit back and the grant reaches the origin.
        assert_eq!(hub.on_frame(exit, frame::WINDOW, 2, &10u32.to_be_bytes()), vec![(origin, frame::window(1, 10))]);
        assert_eq!(hub.on_frame(origin, frame::DATA, 1, &chunk[..10]).len(), 1);
        let killed = hub.on_frame(origin, frame::DATA, 1, b"x");
        assert_eq!(killed.iter().map(|(_, b)| code(b)).collect::<Vec<_>>(), vec!["window", "window"]);
        assert!(hub.streams.is_empty() && hub.per_bot.is_empty());
    }

    #[test]
    fn data_before_the_exit_has_connected_is_refused() {
        let (mut hub, origin, _) = hub();
        let ask = hub.begin_open(origin, 1, &open_body()).unwrap();
        hub.finish_open(origin, 1, &ask, &allowed("mac"), "now");
        let out = hub.on_frame(origin, frame::DATA, 1, b"early");
        assert_eq!(code(&out[0].1), "window");
    }

    #[test]
    fn an_offline_exit_refuses_and_is_reported_never_carried() {
        let (mut hub, origin, exit) = hub();
        hub.set_serving(exit, false);
        let ask = hub.begin_open(origin, 1, &open_body()).unwrap();
        let out = hub.finish_open(origin, 1, &ask, &allowed("mac"), "2026-10-29T00:00:00Z");
        assert_eq!(out.len(), 1);
        let (_, _, payload) = frame::decode(&out[0].1).unwrap();
        assert_eq!(serde_json::from_slice::<Value>(payload).unwrap(),
                   json!({"code": "exit_offline", "detail": "waiting for exit computer Home Mac"}));
        // A computer with no link at all is offline the same way.
        let ask = hub.begin_open(origin, 3, &open_body()).unwrap();
        let out = hub.finish_open(origin, 3, &ask, &allowed("gone"), "2026-10-29T00:00:01Z");
        assert_eq!(code(&out[0].1), "exit_offline");
        let report = hub.take_report().unwrap();
        assert_eq!(report["offline"].as_array().unwrap().len(), 2);
        assert_eq!(report["offline"][0]["exit"], "computer:mac");
        assert!(report["usage"].as_array().unwrap().is_empty() && hub.streams.is_empty());
    }

    #[test]
    fn pythons_refusal_reaches_the_runner_and_a_cancelled_open_does_nothing() {
        let (mut hub, origin, _) = hub();
        let ask = hub.begin_open(origin, 1, &open_body()).unwrap();
        let out = hub.finish_open(origin, 1, &ask, &json!({"allowed": false, "code": "not_listed", "detail": "no"}), "now");
        assert_eq!(code(&out[0].1), "not_listed");
        let ask = hub.begin_open(origin, 3, &open_body()).unwrap();
        hub.on_frame(origin, frame::CLOSE, 3, b"{}");
        assert!(hub.finish_open(origin, 3, &ask, &allowed("mac"), "now").is_empty());
        assert!(hub.streams.is_empty());
        // Even ids belong to the relay; an id already in use cannot be opened again.
        assert_eq!(code(&hub.begin_open(origin, 2, &open_body()).unwrap_err()[0].1), "invalid");
        assert_eq!(code(&hub.begin_open(origin, 5, b"{\"host\":\"x\"}").unwrap_err()[0].1), "invalid");
    }

    #[test]
    fn the_per_bot_limit_holds_and_closing_frees_a_place() {
        let (mut hub, origin, exit) = hub();
        opened(&mut hub, origin, 1);
        opened(&mut hub, origin, 3);
        let ask = hub.begin_open(origin, 5, &open_body()).unwrap();
        assert_eq!(code(&hub.finish_open(origin, 5, &ask, &allowed("mac"), "now")[0].1), "busy");
        let out = hub.on_frame(exit, frame::CLOSE, 2, br#"{"code":"done","detail":""}"#);
        assert_eq!(out, vec![(origin, frame::encode(frame::CLOSE, 1, br#"{"code":"done","detail":""}"#))]);
        opened(&mut hub, origin, 7);
    }

    #[test]
    fn usage_is_summed_per_run_host_and_exit_and_a_failed_report_is_kept() {
        let (mut hub, origin, exit) = hub();
        opened(&mut hub, origin, 1);
        opened(&mut hub, origin, 3);
        hub.on_frame(origin, frame::DATA, 1, b"abc");
        hub.on_frame(exit, frame::DATA, 4, b"hello");
        let report = hub.take_report().unwrap();
        assert_eq!(report["usage"], json!([{"attempt": "a1", "host": "shop.acme.example", "port": 443,
                                            "exit": "computer:mac", "opened": 2, "open": 2, "up": 3, "down": 5}]));
        hub.restore(&report); // Python did not answer
        hub.on_frame(origin, frame::CLOSE, 1, b"{}");
        hub.on_frame(origin, frame::CLOSE, 3, b"{}");
        let report = hub.take_report().unwrap();
        assert_eq!(report["usage"][0]["opened"], 2);
        assert_eq!(report["usage"][0]["open"], 0);
        assert_eq!(report["usage"][0]["down"], 5);
        assert!(hub.take_report().is_none() && hub.usage.is_empty());
    }

    #[test]
    fn a_lost_exit_link_closes_its_streams_as_offline_and_a_stopped_exit_ends_them() {
        let (mut hub, origin, exit) = hub();
        opened(&mut hub, origin, 1);
        let out = hub.detach(exit);
        assert_eq!(out.len(), 1);
        assert_eq!((out[0].0, code(&out[0].1)), (origin, "exit_offline".to_owned()));
        let (exit2, _, _) = hub.attach("mac", "Home Mac", true, "t".into(), None);
        opened(&mut hub, origin, 3);
        let out = hub.set_serving(exit2, false);
        assert_eq!(out.iter().map(|(c, b)| (*c, code(b))).collect::<Vec<_>>(),
                   vec![(exit2, "exit_off".into()), (origin, "exit_off".into())]);
        // A newer link for the same computer replaces the older one.
        let (exit3, _, _) = hub.attach("mac", "Home Mac", true, "t".into(), None);
        assert!(!hub.links.contains_key(&exit2) && hub.runners["mac"] == exit3);
    }

    #[test]
    fn streams_end_after_an_hour() {
        let (mut hub, origin, _) = hub();
        opened(&mut hub, origin, 1);
        assert!(hub.sweep(Instant::now()).is_empty());
        let out = hub.sweep(Instant::now() + LIFETIME);
        assert_eq!(out.iter().map(|(_, b)| code(b)).collect::<Vec<_>>(), vec!["time_limit", "time_limit"]);
    }

    #[test]
    fn the_report_time_is_rfc3339() {
        let at = now_iso();
        assert_eq!(at.len(), 27);
        assert!(at.ends_with('Z') && at.as_bytes()[10] == b'T');
    }
}
