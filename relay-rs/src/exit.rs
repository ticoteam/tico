//! Browser exits: a bot's browser reaches the web from another Tico computer, one its owner shares (docs/computers.md,
//! "Share internet with bots"). Python decides which bot may use which exit and mints each tunnel; this module keeps
//! the live state, pairs the bot computer's socket with the exit computer's, moves their opaque bytes (TLS end to end)
//! and counts them against the exit's daily cap.
//!
//! Three sockets, all dialled out by runners:
//! - `/api/v2/runners/me/browser-exit/ws`: the exit computer's link, held while its owner shares. Its runner token is
//!   checked by Python. The relay sends one JSON `open` message per tunnel down it.
//! - `/api/v2/runners/me/browser-exit/tunnels/{id}/ws?ticket=`: the bot computer's end of one tunnel. The ticket is
//!   the one-use ticket Python's grant returned to that runner.
//! - `/api/v2/runners/me/browser-exit/tunnels/{id}/exit?ticket=`: the exit computer's end, with the ticket from `open`.
//!
//! The exit's first message on its end is a text status (`{"ok":true}`, or `{"ok":false,"error":...}` when it refused
//! the address); after that only binary frames carry bytes, both ways.
use super::{close_socket, error, id, App, Close, Error, WRITE};
use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path, Query, State,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashMap},
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use tokio::sync::{mpsc, oneshot, watch};

/// How long both ends of a granted tunnel have to connect.
const ANSWER: Duration = Duration::from_secs(15);
/// A tunnel's longest life; the browser opens a new one after.
const TUNNEL_SESSION: Duration = Duration::from_secs(3600);
/// A browser that stops reading (a paused video) holds a tunnel's writes this long before it is cut.
const TUNNEL_WRITE: Duration = Duration::from_secs(60);
const LINK_PING: Duration = Duration::from_secs(20);
const TUNNELS: usize = 4096;
const TUNNELS_PER_EXIT: usize = 256;
const REPORTS: usize = 16384;
pub(super) const FRAME: usize = 1 << 20;
pub(super) const REPORT_BATCH: usize = 256;

/// What an exit computer's owner allows: Python's durable choice, with the generation that fences older grants.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub(super) struct Policy {
    pub generation: u64,
    pub sharing: bool,
    pub bots: Vec<String>,
    pub cap: u64,
}

impl Policy {
    fn allows(&self, bot: &str) -> bool {
        self.sharing && self.bots.iter().any(|b| b == bot)
    }
}

/// One closed tunnel, for Python's per-day log of hostnames and bytes. Retried until acknowledged.
#[derive(Clone, Debug, Serialize)]
pub(super) struct TunnelReport {
    pub id: String,
    pub exit: String,
    pub runner_id: String,
    pub bot: String,
    pub host: String,
    pub day: String,
    pub bytes: u64,
    pub code: u16,
}

struct Link {
    serial: u64,
    open: mpsc::UnboundedSender<String>,
    cancel: watch::Sender<Option<Close>>,
}

struct Tunnel {
    exit: String,
    runner_id: String,
    bot: String,
    host: String,
    day: String,
    client_ticket: Option<String>,
    exit_ticket: Option<String>,
    client: Option<oneshot::Sender<WebSocket>>,
    exit_side: Option<oneshot::Sender<WebSocket>>,
    cancel: watch::Sender<Option<Close>>,
    bytes: u64,
}

#[derive(Default)]
pub(super) struct Exits {
    policies: HashMap<String, Policy>,
    links: HashMap<String, Link>,
    tunnels: HashMap<String, Tunnel>,
    /// Per exit: the UTC day and the bytes counted that day. Kept across a Python restart (it only ever counts real
    /// bytes); a fresh relay starts each exit from the bytes Python has recorded.
    budgets: HashMap<String, (String, u64)>,
    pub reports: BTreeMap<String, TunnelReport>,
    serial: u64,
}

/// A grant Python asks for: the tunnel, and the exit's policy as Python read it.
#[derive(Debug, Deserialize, Serialize)]
pub(super) struct Grant {
    pub id: String,
    pub runner_id: String,
    pub exit: String,
    pub bot: String,
    pub host: String,
    pub port: u16,
    pub day: String,
    pub used: u64,
    pub policy: Policy,
}

/// The bot computer's ticket, the two socket slots and the tunnel's cancellation.
type Admitted = (
    String,
    oneshot::Receiver<WebSocket>,
    oneshot::Receiver<WebSocket>,
    watch::Receiver<Option<Close>>,
);

#[derive(Clone, Copy, PartialEq, Debug)]
pub(super) enum Side {
    Client,
    Exit,
}

fn same(a: &str, b: &str) -> bool {
    bool::from(Sha256::digest(a.as_bytes()).ct_eq(&Sha256::digest(b.as_bytes())))
}

/// A hostname the exit may be asked to resolve: letters, digits, dots, hyphens and underscores, or an IP literal
/// (an IPv6 one in brackets is passed without them). The exit computer checks the address it resolves to.
pub(super) fn valid_host(host: &str) -> bool {
    !host.is_empty()
        && host.len() <= 253
        && (host.parse::<std::net::IpAddr>().is_ok()
            || host
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_')))
}

impl Exits {
    pub(super) fn close(&mut self, tid: &str, close: Close) {
        if let Some(t) = self.tunnels.remove(tid) {
            let _ = t.cancel.send(Some(close.clone()));
            if self.reports.len() < REPORTS {
                self.reports.insert(
                    tid.to_owned(),
                    TunnelReport {
                        id: tid.to_owned(),
                        exit: t.exit,
                        runner_id: t.runner_id,
                        bot: t.bot,
                        host: t.host,
                        day: t.day,
                        bytes: t.bytes,
                        code: close.code,
                    },
                );
            }
        }
    }

    fn close_where(&mut self, keep: impl Fn(&Tunnel) -> bool, close: Close) -> usize {
        let gone = self
            .tunnels
            .iter()
            .filter(|(_, t)| !keep(t))
            .map(|(tid, _)| tid.clone())
            .collect::<Vec<_>>();
        for tid in &gone {
            self.close(tid, close.clone());
        }
        gone.len()
    }

    fn drop_link(&mut self, exit: &str, close: Close) {
        if let Some(link) = self.links.remove(exit) {
            let _ = link.cancel.send(Some(close));
        }
    }

    /// Every tunnel and link closes (lease lost, relay stopping).
    pub(super) fn close_all(&mut self, close: Close) {
        self.close_where(|_| false, close.clone());
        for exit in self.links.keys().cloned().collect::<Vec<_>>() {
            self.drop_link(&exit, close.clone());
        }
    }

    /// A new Python process: its generations start again, so the policies it pushed before go.
    pub(super) fn restart(&mut self, close: Close) {
        self.close_all(close);
        self.policies.clear();
    }

    /// Install `policy` unless a newer one is already here, then close what it no longer allows.
    pub(super) fn set_policy(
        &mut self,
        exit: &str,
        policy: Policy,
    ) -> std::result::Result<usize, &'static str> {
        if self
            .policies
            .get(exit)
            .is_some_and(|p| p.generation > policy.generation)
        {
            return Err("stale exit policy");
        }
        let closed = self.close_where(
            |t| t.exit != exit || policy.allows(&t.bot),
            Close::new(4403, "exit_not_shared"),
        );
        if !policy.sharing {
            self.drop_link(exit, Close::new(4403, "sharing turned off"));
        }
        self.policies.insert(exit.to_owned(), policy);
        Ok(closed)
    }

    /// Close one bot's tunnels, or one exit's (a bot's exit changed, a computer was removed).
    pub(super) fn close_matching(
        &mut self,
        bot: Option<&str>,
        exit: Option<&str>,
        reason: &str,
    ) -> usize {
        self.close_where(
            |t| !(bot.is_none_or(|b| t.bot == b) && exit.is_none_or(|e| t.exit == e)),
            Close::new(4403, reason),
        )
    }

    /// Count `n` bytes against the tunnel and its exit's day. Past the cap every tunnel through that exit closes.
    pub(super) fn spend(&mut self, tid: &str, n: u64) -> std::result::Result<(), Close> {
        let Some(t) = self.tunnels.get_mut(tid) else {
            return Err(Close::new(1000, ""));
        };
        t.bytes += n;
        let exit = t.exit.clone();
        let cap = self.policies.get(&exit).map_or(0, |p| p.cap);
        let budget = self
            .budgets
            .entry(exit.clone())
            .or_insert_with(|| (t.day.clone(), 0));
        budget.1 += n;
        if budget.1 > cap {
            let close = Close::new(4429, "exit_over_cap");
            self.close_where(|t| t.exit != exit, close.clone());
            return Err(close);
        }
        Ok(())
    }

    /// Admit a tunnel: the exit shares with this bot, is connected and is under its cap. Returns the bot computer's
    /// ticket, the exit's ticket and the open message to send down the exit's link.
    fn admit(&mut self, g: &Grant) -> std::result::Result<Admitted, Error> {
        if [&g.id, &g.runner_id, &g.exit, &g.bot]
            .iter()
            .any(|v| v.is_empty() || v.len() > 128)
            || g.day.len() != 10
            || !valid_host(&g.host)
            || !matches!(g.port, 80 | 443)
        {
            return Err(error(StatusCode::BAD_REQUEST, "invalid exit grant"));
        }
        if self.tunnels.contains_key(&g.id) || self.reports.contains_key(&g.id) {
            return Err(error(StatusCode::CONFLICT, "conflicting grant"));
        }
        match self.policies.get(&g.exit) {
            Some(p) if p.generation > g.policy.generation => {
                return Err(error(StatusCode::CONFLICT, "exit_stale"))
            }
            Some(p) if p.generation == g.policy.generation => {}
            _ => {
                self.policies.insert(g.exit.clone(), g.policy.clone());
            }
        }
        let policy = &self.policies[&g.exit];
        if !policy.allows(&g.bot) {
            return Err(error(StatusCode::FORBIDDEN, "exit_not_shared"));
        }
        let cap = policy.cap;
        let Some(link) = self.links.get(&g.exit) else {
            return Err(error(StatusCode::CONFLICT, "exit_offline"));
        };
        let budget = match self.budgets.get(&g.exit) {
            Some((day, used)) if *day == g.day => (day.clone(), (*used).max(g.used)),
            _ => (g.day.clone(), g.used),
        };
        let over = budget.1 >= cap;
        self.budgets.insert(g.exit.clone(), budget);
        if over {
            return Err(error(StatusCode::TOO_MANY_REQUESTS, "exit_over_cap"));
        }
        if self.tunnels.len() >= TUNNELS
            || self.reports.len() + self.tunnels.len() >= REPORTS
            || self.tunnels.values().filter(|t| t.exit == g.exit).count() >= TUNNELS_PER_EXIT
        {
            return Err(error(StatusCode::TOO_MANY_REQUESTS, "exit_busy"));
        }
        let (client_ticket, exit_ticket) = (id() + &id(), id() + &id());
        let open = json!({"op": "open", "id": g.id, "host": g.host, "port": g.port, "ticket": exit_ticket}).to_string();
        if link.open.send(open).is_err() {
            return Err(error(StatusCode::CONFLICT, "exit_offline"));
        }
        let (ctx, crx) = oneshot::channel();
        let (etx, erx) = oneshot::channel();
        let (cancel, cancelled) = watch::channel(None);
        self.tunnels.insert(
            g.id.clone(),
            Tunnel {
                exit: g.exit.clone(),
                runner_id: g.runner_id.clone(),
                bot: g.bot.clone(),
                host: g.host.clone(),
                day: g.day.clone(),
                client_ticket: Some(client_ticket.clone()),
                exit_ticket: Some(exit_ticket),
                client: Some(ctx),
                exit_side: Some(etx),
                cancel,
                bytes: 0,
            },
        );
        Ok((client_ticket, crx, erx, cancelled))
    }

    /// The socket slot a ticket opens, once.
    fn take(&mut self, tid: &str, ticket: &str, side: Side) -> Option<oneshot::Sender<WebSocket>> {
        let t = self.tunnels.get_mut(tid)?;
        let slot = match side {
            Side::Client => &mut t.client_ticket,
            Side::Exit => &mut t.exit_ticket,
        };
        if !slot.as_ref().is_some_and(|s| same(s, ticket)) {
            return None;
        }
        *slot = None;
        match side {
            Side::Client => t.client.take(),
            Side::Exit => t.exit_side.take(),
        }
    }

    pub(super) fn acknowledge(&mut self, ids: &[String]) {
        for tid in ids {
            self.reports.remove(tid);
        }
    }

    pub(super) fn pending_reports(&self) -> Vec<TunnelReport> {
        self.reports.values().take(REPORT_BATCH).cloned().collect()
    }

    #[cfg(test)]
    fn connect_link(&mut self, exit: &str) -> mpsc::UnboundedReceiver<String> {
        let (open, rx) = mpsc::unbounded_channel();
        let (cancel, _) = watch::channel(None);
        self.serial += 1;
        self.links.insert(
            exit.to_owned(),
            Link {
                serial: self.serial,
                open,
                cancel,
            },
        );
        rx
    }
}

/// The control command: admit the tunnel and start its task. The answer carries the bot computer's ticket.
pub(super) fn grant(app: &App, exits: &mut Exits, g: Grant) -> std::result::Result<Value, Error> {
    let (ticket, client, exit, cancelled) = exits.admit(&g)?;
    tokio::spawn(tunnel_task(app.clone(), g.id, client, exit, cancelled));
    Ok(json!({"ticket": ticket}))
}

/// The exit computer's link. Python checks its runner token and that its owner shares it.
pub(super) async fn link(
    State(app): State<App>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    let bearer = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if bearer.is_empty() || bearer.len() > 8192 {
        return StatusCode::FORBIDDEN.into_response();
    }
    let answer = app
        .policy_answer(json!({"kind": "exit_link", "authorization": bearer}))
        .await;
    let Some(exit) = answer
        .as_ref()
        .and_then(|a| a["runner_id"].as_str())
        .filter(|r| !r.is_empty() && r.len() <= 128)
        .map(str::to_owned)
    else {
        return StatusCode::FORBIDDEN.into_response();
    };
    ws.on_upgrade(move |socket| link_task(app, exit, socket))
}

async fn link_task(app: App, exit: String, mut socket: WebSocket) {
    let (open, mut opens) = mpsc::unbounded_channel();
    let (cancel, mut cancelled) = watch::channel(None);
    let serial = {
        let mut hub = app.hub.lock().unwrap();
        hub.exits.serial += 1;
        let serial = hub.exits.serial;
        let old = hub.exits.links.insert(
            exit.clone(),
            Link {
                serial,
                open,
                cancel,
            },
        );
        if let Some(old) = old {
            let _ = old
                .cancel
                .send(Some(Close::new(4409, "replaced by a newer link")));
        }
        serial
    };
    let mut ping = tokio::time::interval(LINK_PING);
    let outcome = loop {
        tokio::select! {
            _ = cancelled.changed() => break cancelled.borrow().clone().unwrap_or_else(|| Close::new(1012, "relay stopped")),
            Some(text) = opens.recv() => {
                if !matches!(tokio::time::timeout(WRITE, socket.send(Message::Text(text.into()))).await, Ok(Ok(()))) {
                    break Close::new(1011, "slow exit");
                }
            },
            _ = ping.tick() => {
                if !matches!(tokio::time::timeout(WRITE, socket.send(Message::Ping(Vec::new().into()))).await, Ok(Ok(()))) {
                    break Close::new(1011, "slow exit");
                }
            },
            message = socket.recv() => match message {
                Some(Ok(Message::Close(_))) | Some(Err(_)) | None => break Close::new(1000, ""),
                _ => {}
            },
        }
    };
    {
        let mut hub = app.hub.lock().unwrap();
        if hub
            .exits
            .links
            .get(&exit)
            .is_some_and(|l| l.serial == serial)
        {
            hub.exits.links.remove(&exit);
        }
    }
    close_socket(&mut socket, &outcome).await;
}

#[derive(Deserialize)]
pub(super) struct TicketQuery {
    #[serde(default)]
    ticket: String,
}

pub(super) async fn client(
    State(app): State<App>,
    Path(tid): Path<String>,
    Query(query): Query<TicketQuery>,
    ws: WebSocketUpgrade,
) -> Response {
    attach(app, tid, query.ticket, Side::Client, ws)
}

pub(super) async fn exit_side(
    State(app): State<App>,
    Path(tid): Path<String>,
    Query(query): Query<TicketQuery>,
    ws: WebSocketUpgrade,
) -> Response {
    attach(app, tid, query.ticket, Side::Exit, ws)
}

fn attach(app: App, tid: String, ticket: String, side: Side, ws: WebSocketUpgrade) -> Response {
    if ticket.len() != 64 || tid.len() > 128 {
        return StatusCode::FORBIDDEN.into_response();
    }
    let sender = app.hub.lock().unwrap().exits.take(&tid, &ticket, side);
    match sender {
        Some(sender) => ws
            .max_message_size(2 * FRAME)
            .max_frame_size(2 * FRAME)
            .write_buffer_size(65536)
            .max_write_buffer_size(2 * FRAME + 65536)
            .on_upgrade(move |socket| async move {
                let _ = sender.send(socket);
            }),
        None => StatusCode::FORBIDDEN.into_response(),
    }
}

async fn tunnel_task(
    app: App,
    tid: String,
    mut client_rx: oneshot::Receiver<WebSocket>,
    mut exit_rx: oneshot::Receiver<WebSocket>,
    mut cancelled: watch::Receiver<Option<Close>>,
) {
    let deadline = Instant::now() + ANSWER;
    let (mut client, mut exit) = (None, None);
    let outcome = loop {
        if let Some(close) = cancelled.borrow().clone() {
            break close;
        }
        if let (Some(c), Some(e)) = (&mut client, &mut exit) {
            break pump(&app, &tid, c, e, &mut cancelled).await;
        }
        tokio::select! {
            _ = cancelled.changed() => { if let Some(close) = cancelled.borrow().clone() { break close; } },
            _ = tokio::time::sleep_until(deadline.into()) => break Close::new(4404, if client.is_none() { "bot computer did not connect" } else { "exit_no_answer" }),
            socket = &mut client_rx, if client.is_none() => match socket {
                Ok(s) => client = Some(s), Err(_) => break Close::new(1000, "closed"),
            },
            socket = &mut exit_rx, if exit.is_none() => match socket {
                Ok(s) => exit = Some(s), Err(_) => break Close::new(1000, "closed"),
            },
        }
    };
    app.hub.lock().unwrap().exits.close(&tid, outcome.clone());
    if let Some(s) = client.as_mut() {
        close_socket(s, &outcome).await;
    }
    if let Some(s) = exit.as_mut() {
        close_socket(s, &outcome).await;
    }
}

/// Bytes both ways until either end closes, the cap or the time limit passes, or Python's policy closes the tunnel.
/// The exit's own close reason (a refused address) reaches the bot computer unchanged.
async fn pump(
    app: &App,
    tid: &str,
    client: &mut WebSocket,
    exit: &mut WebSocket,
    cancel: &mut watch::Receiver<Option<Close>>,
) -> Close {
    let deadline = Instant::now() + TUNNEL_SESSION;
    let (mut cs, mut cr) = client.split();
    let (mut es, mut er) = exit.split();
    let spend = |n: usize| app.hub.lock().unwrap().exits.spend(tid, n as u64);
    let up = async {
        while let Some(Ok(message)) = cr.next().await {
            match message {
                Message::Binary(bytes) => {
                    if let Err(close) = spend(bytes.len()) {
                        return close;
                    }
                    if !matches!(
                        tokio::time::timeout(TUNNEL_WRITE, es.send(Message::Binary(bytes))).await,
                        Ok(Ok(()))
                    ) {
                        return Close::new(1011, "slow exit");
                    }
                }
                Message::Close(_) => return Close::new(1000, ""),
                _ => {}
            }
        }
        Close::new(1000, "")
    };
    let down = async {
        let mut status = false;
        while let Some(Ok(message)) = er.next().await {
            let message = match message {
                Message::Binary(bytes) => {
                    if let Err(close) = spend(bytes.len()) {
                        return close;
                    }
                    Message::Binary(bytes)
                }
                // Only the first message, the exit's status, may be text.
                Message::Text(text) if !status && text.len() <= 1024 => {
                    status = true;
                    Message::Text(text)
                }
                Message::Close(frame) => {
                    return frame.map_or(Close::new(1000, ""), |f| {
                        Close::new(f.code, &f.reason.chars().take(100).collect::<String>())
                    })
                }
                _ => continue,
            };
            if !matches!(
                tokio::time::timeout(TUNNEL_WRITE, cs.send(message)).await,
                Ok(Ok(()))
            ) {
                return Close::new(1011, "slow bot computer");
            }
        }
        Close::new(1000, "")
    };
    tokio::select! {
        close = up => close,
        close = down => close,
        _ = tokio::time::sleep_until(deadline.into()) => Close::new(4408, "time limit"),
        _ = cancel.changed() => cancel.borrow().clone().unwrap_or_else(|| Close::new(1012, "relay stopped")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy(generation: u64, bots: &[&str], cap: u64) -> Policy {
        Policy {
            generation,
            sharing: true,
            bots: bots.iter().map(|b| b.to_string()).collect(),
            cap,
        }
    }

    fn grant(tid: &str, bot: &str, generation: u64, used: u64) -> Grant {
        Grant {
            id: tid.into(),
            runner_id: "bot-computer".into(),
            exit: "laptop".into(),
            bot: bot.into(),
            host: "www.acme.example".into(),
            port: 443,
            day: "2026-11-01".into(),
            used,
            policy: policy(generation, &["ops"], 1000),
        }
    }

    fn code(result: std::result::Result<impl Sized, Error>) -> String {
        match result {
            Ok(_) => "ok".into(),
            Err((_, body)) => body.0["error"].as_str().unwrap().into(),
        }
    }

    #[test]
    fn a_tunnel_is_refused_unless_the_exit_shares_with_the_bot_is_connected_and_under_its_cap() {
        let mut exits = Exits::default();
        assert_eq!(code(exits.admit(&grant("t1", "ops", 1, 0))), "exit_offline");
        let mut opens = exits.connect_link("laptop");
        assert_eq!(
            code(exits.admit(&grant("t2", "sales", 1, 0))),
            "exit_not_shared"
        );
        let mut wrong_port = grant("t3", "ops", 1, 0);
        wrong_port.port = 22;
        assert_eq!(code(exits.admit(&wrong_port)), "invalid exit grant");
        assert_eq!(
            code(exits.admit(&grant("t4", "ops", 1, 1000))),
            "exit_over_cap"
        );
        // A later day starts from what Python recorded for it.
        let mut tomorrow = grant("t5", "ops", 1, 10);
        tomorrow.day = "2026-11-02".into();
        assert_eq!(code(exits.admit(&tomorrow)), "ok");
        let open: Value = serde_json::from_str(&opens.try_recv().unwrap()).unwrap();
        assert_eq!(
            (open["id"].as_str(), open["port"].as_u64()),
            (Some("t5"), Some(443))
        );
        assert_eq!(open["ticket"].as_str().unwrap().len(), 64);
    }

    #[test]
    fn an_older_policy_never_replaces_a_newer_one_and_a_stale_grant_is_refused() {
        let mut exits = Exits::default();
        let _opens = exits.connect_link("laptop");
        exits
            .set_policy("laptop", policy(3, &["ops"], 1000))
            .unwrap();
        assert_eq!(
            exits.set_policy("laptop", policy(2, &["ops", "sales"], 1000)),
            Err("stale exit policy")
        );
        assert_eq!(code(exits.admit(&grant("t1", "ops", 2, 0))), "exit_stale");
        assert_eq!(code(exits.admit(&grant("t2", "ops", 3, 0))), "ok");
    }

    #[test]
    fn taking_a_bot_off_or_turning_sharing_off_closes_its_tunnels_and_the_link() {
        let mut exits = Exits::default();
        let _opens = exits.connect_link("laptop");
        exits.admit(&grant("t1", "ops", 1, 0)).unwrap();
        let mut cancel = exits.tunnels["t1"].cancel.subscribe();
        assert_eq!(
            exits.set_policy("laptop", policy(2, &["sales"], 1000)),
            Ok(1)
        );
        assert_eq!(
            cancel
                .borrow_and_update()
                .as_ref()
                .map(|c| c.reason.clone()),
            Some("exit_not_shared".into())
        );
        assert_eq!(exits.reports["t1"].bot, "ops");
        let mut off = policy(3, &["sales"], 1000);
        off.sharing = false;
        exits.set_policy("laptop", off).unwrap();
        assert!(!exits.links.contains_key("laptop"));
    }

    #[test]
    fn bytes_past_the_daily_cap_close_every_tunnel_through_that_exit() {
        let mut exits = Exits::default();
        let _opens = exits.connect_link("laptop");
        exits.admit(&grant("t1", "ops", 1, 900)).unwrap();
        exits.admit(&grant("t2", "ops", 1, 0)).unwrap();
        assert!(exits.spend("t1", 60).is_ok());
        assert_eq!(exits.spend("t2", 60).unwrap_err().reason, "exit_over_cap");
        assert!(exits.tunnels.is_empty());
        assert_eq!(
            (exits.reports["t1"].bytes, exits.reports["t2"].bytes),
            (60, 60)
        );
        assert_eq!(
            code(exits.admit(&grant("t3", "ops", 1, 0))),
            "exit_over_cap"
        );
    }

    #[test]
    fn a_ticket_opens_its_own_side_once() {
        let mut exits = Exits::default();
        let _opens = exits.connect_link("laptop");
        let (ticket, ..) = exits.admit(&grant("t1", "ops", 1, 0)).unwrap();
        let exit_ticket = exits.tunnels["t1"].exit_ticket.clone().unwrap();
        assert!(exits.take("t1", &ticket, Side::Exit).is_none());
        assert!(exits.take("t1", &ticket, Side::Client).is_some());
        assert!(exits.take("t1", &ticket, Side::Client).is_none());
        assert!(exits.take("t1", &exit_ticket, Side::Exit).is_some());
    }

    #[test]
    fn a_hostname_is_a_name_or_an_address_never_a_url_or_a_header() {
        for ok in [
            "www.acme.example",
            "10.0.0.1",
            "::1",
            "xn--acme-9qa.example",
        ] {
            assert!(valid_host(ok), "{ok}");
        }
        for bad in [
            "",
            "acme.example/path",
            "acme.example:443",
            "a b",
            "acme\r\nHost: x",
            &"a".repeat(254),
        ] {
            assert!(!valid_host(bad), "{bad}");
        }
    }

    /// The whole path over real sockets: the exit's link (its token checked by a stand-in Python), a grant, both
    /// tunnel ends, the exit's status, bytes both ways, and the closed tunnel's report with its byte count.
    #[tokio::test]
    async fn bytes_flow_between_the_two_computers_and_the_closed_tunnel_is_reported() {
        use crate::{gateway, Hub};
        use axum::{routing::post, Json, Router};
        use std::sync::{Arc, Mutex};
        use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message as M};

        let python = Router::new().route(
            "/_tico/screen/v1/authorize",
            post(|Json(body): Json<Value>| async move {
                if body["kind"] == "exit_link" && body["authorization"] == "Bearer laptop-token" {
                    Ok(Json(json!({"allowed": true, "runner_id": "laptop"})))
                } else {
                    Err(StatusCode::FORBIDDEN)
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let python_url = format!("http://{}", listener.local_addr().unwrap());
        tokio::spawn(async move { axum::serve(listener, python).await.unwrap() });
        let app = App {
            hub: Arc::new(Mutex::new(Hub::new())),
            key: Arc::new("k".repeat(32)),
            python: Arc::new(python_url),
            http: reqwest::Client::new(),
            proxy: reqwest::Client::new(),
            trusted_proxies: Arc::new(gateway::trusted_proxies("127.0.0.1/32").unwrap()),
        };
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let relay = format!("ws://{}", listener.local_addr().unwrap());
        let router = gateway::routes(app.clone())
            .into_make_service_with_connect_info::<std::net::SocketAddr>();
        tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });

        let link_url = format!("{relay}/api/v2/runners/me/browser-exit/ws");
        assert!(tokio_tungstenite::connect_async(link_url.clone())
            .await
            .is_err());
        let mut request = link_url.into_client_request().unwrap();
        request
            .headers_mut()
            .insert("authorization", "Bearer laptop-token".parse().unwrap());
        let (mut link, _) = tokio_tungstenite::connect_async(request).await.unwrap();
        for _ in 0..100 {
            if app.hub.lock().unwrap().exits.links.contains_key("laptop") {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let answer = {
            let mut hub = app.hub.lock().unwrap();
            super::grant(&app, &mut hub.exits, grant("t1", "ops", 1, 0)).unwrap()
        };
        let ticket = answer["ticket"].as_str().unwrap().to_owned();
        let open = loop {
            match link.next().await.unwrap().unwrap() {
                M::Text(text) => break serde_json::from_str::<Value>(&text).unwrap(),
                _ => continue,
            }
        };
        let exit_url = format!(
            "{relay}/api/v2/runners/me/browser-exit/tunnels/t1/exit?ticket={}",
            open["ticket"].as_str().unwrap()
        );
        let (mut exit, _) = tokio_tungstenite::connect_async(exit_url).await.unwrap();
        exit.send(M::Text(r#"{"ok":true}"#.into())).await.unwrap();
        let client_url =
            format!("{relay}/api/v2/runners/me/browser-exit/tunnels/t1/ws?ticket={ticket}");
        let (mut client, _) = tokio_tungstenite::connect_async(client_url.clone())
            .await
            .unwrap();
        assert_eq!(
            client.next().await.unwrap().unwrap(),
            M::Text(r#"{"ok":true}"#.into())
        );
        client
            .send(M::Binary(b"hello".to_vec().into()))
            .await
            .unwrap();
        assert_eq!(
            exit.next().await.unwrap().unwrap(),
            M::Binary(b"hello".to_vec().into())
        );
        exit.send(M::Binary(b"world!".to_vec().into()))
            .await
            .unwrap();
        assert_eq!(
            client.next().await.unwrap().unwrap(),
            M::Binary(b"world!".to_vec().into())
        );
        // The ticket was used: nobody else joins.
        assert!(tokio_tungstenite::connect_async(client_url).await.is_err());
        client.send(M::Close(None)).await.unwrap();
        let mut report = None;
        for _ in 0..200 {
            report = app.hub.lock().unwrap().exits.reports.get("t1").cloned();
            if report.is_some() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let report = report.expect("closed tunnel reported");
        assert_eq!(
            (report.bytes, report.host.as_str(), report.bot.as_str()),
            (11, "www.acme.example", "ops")
        );
    }
}
