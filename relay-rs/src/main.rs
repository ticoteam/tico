//! The computer-screen relay and same-origin HTTP gateway. Permission policy and audit writes remain in Python.
mod gateway;
use axum::{
    extract::{
        ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade},
        DefaultBodyLimit, Path, Query, State,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashMap},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use tokio::sync::{oneshot, watch};
use uuid::Uuid;

const TICKET: Duration = Duration::from_secs(60);
const ANSWER: Duration = Duration::from_secs(30);
const SESSION: Duration = Duration::from_secs(3600);
const LEASE: Duration = Duration::from_secs(6);
const WRITE: Duration = Duration::from_secs(5);
const BYTES: usize = 1 << 20;
const MESSAGE_BYTES: usize = 16 << 20;
const LIVE_LIMIT: usize = 1024;
const REPORT_LIMIT: usize = 8192;
type Error = (StatusCode, Json<Value>);
type Result<T> = std::result::Result<T, Error>;
fn error(code: StatusCode, message: &str) -> Error {
    (code, Json(json!({"error": message})))
}
fn id() -> String {
    Uuid::new_v4().simple().to_string()
}
fn allows(mode: &str, control: bool) -> bool {
    mode == "control" || mode == "view" && !control
}

#[derive(Clone, Debug)]
struct Close {
    code: u16,
    reason: String,
}
impl Close {
    fn new(code: u16, reason: &str) -> Self {
        Self {
            code,
            reason: reason.to_owned(),
        }
    }
}
#[derive(Clone, Serialize)]
struct Report {
    id: String,
    runner_id: String,
    actor: String,
    control: bool,
    seconds: u64,
    code: u16,
    lease: String,
}
struct Screen {
    id: String,
    runner_id: String,
    actor: String,
    control: bool,
    generation: u64,
    lease: String,
    ticket: Option<String>,
    created: Instant,
    started: Option<Instant>,
    active: bool,
    browser: Option<oneshot::Sender<WebSocket>>,
    runner: Option<oneshot::Sender<WebSocket>>,
    cancel: watch::Sender<Option<Close>>,
}
struct Hub {
    boot: String,
    challenge: String,
    lease: Option<(String, Instant)>,
    sequence: u64,
    reply: Option<(Vec<u8>, Value)>,
    modes: HashMap<String, (u64, String)>,
    screens: HashMap<String, Screen>,
    reports: BTreeMap<String, Report>,
}
impl Hub {
    fn new() -> Self {
        Self {
            boot: id(),
            challenge: id(),
            lease: None,
            sequence: 0,
            reply: None,
            modes: HashMap::new(),
            screens: HashMap::new(),
            reports: BTreeMap::new(),
        }
    }
    fn valid(&self, lease: &str) -> bool {
        self.lease
            .as_ref()
            .is_some_and(|(key, at)| key == lease && at.elapsed() < LEASE)
    }
    fn close(&mut self, sid: &str, close: Close) {
        if let Some(s) = self.screens.remove(sid) {
            let _ = s.cancel.send(Some(close.clone()));
            self.reports.insert(
                s.id.clone(),
                Report {
                    id: s.id,
                    runner_id: s.runner_id,
                    actor: s.actor,
                    control: s.control,
                    seconds: s.started.map_or(0, |at| at.elapsed().as_secs()),
                    code: close.code,
                    lease: s.lease,
                },
            );
        }
    }
    fn close_all(&mut self, close: Close) {
        for sid in self.screens.keys().cloned().collect::<Vec<_>>() {
            self.close(&sid, close.clone());
        }
    }
    fn sweep(&mut self) {
        if self
            .lease
            .as_ref()
            .is_some_and(|(_, at)| at.elapsed() >= LEASE)
        {
            self.close_all(Close::new(1012, "permission lease expired"));
            self.lease = None;
        }
        let stale = self
            .screens
            .values()
            .filter(|s| s.started.is_none() && s.created.elapsed() >= TICKET)
            .map(|s| s.id.clone())
            .collect::<Vec<_>>();
        for sid in stale {
            self.close(&sid, Close::new(4404, "nobody opened the screen"));
        }
    }
}
#[derive(Clone)]
struct App {
    hub: Arc<Mutex<Hub>>,
    key: Arc<String>,
    python: Arc<String>,
    http: reqwest::Client,
    proxy: reqwest::Client,
    trusted_proxies: Arc<Vec<gateway::Network>>,
}
impl App {
    fn authorized(&self, headers: &HeaderMap) -> bool {
        let wanted = format!("Bearer {}", self.key);
        headers
            .get("authorization")
            .is_some_and(|v| bool::from(v.as_bytes().ct_eq(wanted.as_bytes())))
    }
    fn check(&self, headers: &HeaderMap) -> Result<()> {
        if self.authorized(headers) {
            Ok(())
        } else {
            Err(error(StatusCode::FORBIDDEN, "private control key required"))
        }
    }
    async fn policy(&self, body: Value) -> bool {
        self.http
            .post(format!("{}/_tico/screen/v1/authorize", self.python))
            .bearer_auth(self.key.as_str())
            .json(&body)
            .send()
            .await
            .is_ok_and(|r| r.status().is_success())
    }
}
async fn hello(State(app): State<App>, headers: HeaderMap) -> Result<Json<Value>> {
    app.check(&headers)?;
    let hub = app.hub.lock().unwrap();
    Ok(Json(
        json!({"boot": hub.boot, "challenge": hub.challenge, "lease": hub.lease.as_ref().filter(|(_, at)| at.elapsed() < LEASE).map(|(key, _)| key)}),
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Start {
    challenge: String,
    lease: String,
}
async fn start(
    State(app): State<App>,
    headers: HeaderMap,
    Json(body): Json<Start>,
) -> Result<Json<Value>> {
    app.check(&headers)?;
    let mut hub = app.hub.lock().unwrap();
    hub.sweep();
    if hub.valid(&body.lease) {
        return Ok(Json(json!({"boot": hub.boot})));
    }
    if body.challenge != hub.challenge || body.lease.len() != 32 {
        return Err(error(StatusCode::CONFLICT, "stale start challenge"));
    }
    hub.close_all(Close::new(1012, "server restarted"));
    hub.modes.clear();
    hub.sequence = 0;
    hub.reply = None;
    hub.challenge = id();
    hub.lease = Some((body.lease, Instant::now()));
    Ok(Json(json!({"boot": hub.boot})))
}
#[derive(Deserialize, Serialize)]
struct Command {
    lease: String,
    sequence: u64,
    #[serde(flatten)]
    action: Action,
}
#[derive(Deserialize, Serialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Action {
    Heartbeat {
        #[serde(default)]
        ack: Vec<String>,
    },
    Mode {
        runner_id: String,
        generation: u64,
        mode: String,
        reason: String,
    },
    Grant {
        id: String,
        runner_id: String,
        actor: String,
        control: bool,
        generation: u64,
        mode: String,
    },
    Stop,
    Activate {
        id: String,
    },
    Pending {
        runner_id: String,
    },
    End {
        id: String,
        code: u16,
        reason: String,
    },
}
async fn command(
    State(app): State<App>,
    headers: HeaderMap,
    Json(body): Json<Command>,
) -> Result<Json<Value>> {
    app.check(&headers)?;
    let mut hub = app.hub.lock().unwrap();
    hub.sweep();
    if !hub.valid(&body.lease) {
        return Err(error(StatusCode::CONFLICT, "stale permission lease"));
    }
    let digest = Sha256::digest(serde_json::to_vec(&body).unwrap()).to_vec();
    if body.sequence <= hub.sequence {
        if body.sequence == hub.sequence {
            if let Some((last, reply)) = &hub.reply {
                if last == &digest {
                    return Ok(Json(reply.clone()));
                }
            }
        }
        return Err(error(StatusCode::CONFLICT, "stale control sequence"));
    }
    let sequence = body.sequence;
    let answer = match body.action {
        Action::Stop => {
            hub.close_all(Close::new(1012, "server stopped"));
            hub.lease = None;
            json!({})
        }
        Action::Heartbeat { ack } => {
            if ack.len() > 256 {
                return Err(error(StatusCode::BAD_REQUEST, "too many acknowledgements"));
            }
            for sid in ack {
                hub.reports.remove(&sid);
            }
            hub.lease.as_mut().unwrap().1 = Instant::now();
            json!({"reports": hub.reports.values().take(256).collect::<Vec<_>>()})
        }
        Action::Mode {
            runner_id,
            generation,
            mode,
            reason,
        } => {
            if !["off", "view", "control"].contains(&mode.as_str())
                || runner_id.len() > 128
                || reason.len() > 100
            {
                return Err(error(StatusCode::BAD_REQUEST, "invalid mode"));
            }
            if hub
                .modes
                .get(&runner_id)
                .is_some_and(|(old, _)| generation <= *old)
            {
                return Err(error(StatusCode::CONFLICT, "stale mode generation"));
            }
            hub.modes
                .insert(runner_id.clone(), (generation, mode.clone()));
            let revoke = hub
                .screens
                .values()
                .filter(|s| s.runner_id == runner_id && !allows(&mode, s.control))
                .map(|s| s.id.clone())
                .collect::<Vec<_>>();
            let count = revoke.len();
            for sid in revoke {
                hub.close(&sid, Close::new(4403, &reason));
            }
            for s in hub
                .screens
                .values_mut()
                .filter(|s| s.runner_id == runner_id)
            {
                s.generation = generation;
            }
            json!({"closed": count})
        }
        Action::Grant {
            id: sid,
            runner_id,
            actor,
            control,
            generation,
            mode,
        } => {
            if sid.len() > 128
                || runner_id.len() > 128
                || actor.len() > 256
                || !allows(&mode, control)
            {
                return Err(error(StatusCode::FORBIDDEN, "screen off or view only"));
            }
            if let Some(s) = hub.screens.get(&sid) {
                if s.runner_id != runner_id
                    || s.actor != actor
                    || s.control != control
                    || s.generation != generation
                    || s.ticket.is_none()
                {
                    return Err(error(StatusCode::CONFLICT, "conflicting grant"));
                }
                let reply = json!({"boot": hub.boot, "ticket": s.ticket});
                hub.sequence = sequence;
                hub.reply = Some((digest, reply.clone()));
                return Ok(Json(reply));
            }
            if hub.reports.contains_key(&sid) {
                return Err(error(StatusCode::CONFLICT, "session ended"));
            }
            let current = hub
                .modes
                .entry(runner_id.clone())
                .or_insert((generation, mode.clone()));
            if current.0 != generation || !allows(&current.1, control) {
                return Err(error(StatusCode::FORBIDDEN, "stale grant"));
            }
            if hub.screens.len() >= LIVE_LIMIT
                || hub.reports.len() + hub.screens.len() >= REPORT_LIMIT
                || hub
                    .screens
                    .values()
                    .filter(|s| s.runner_id == runner_id)
                    .count()
                    >= 3
            {
                return Err(error(StatusCode::TOO_MANY_REQUESTS, "screen busy"));
            }
            let ticket = id() + &id();
            let (btx, brx) = oneshot::channel();
            let (rtx, rrx) = oneshot::channel();
            let (cancel, cancelled) = watch::channel(None);
            hub.screens.insert(
                sid.clone(),
                Screen {
                    id: sid.clone(),
                    runner_id,
                    actor,
                    control,
                    generation,
                    lease: body.lease.clone(),
                    ticket: Some(ticket.clone()),
                    created: Instant::now(),
                    started: None,
                    active: false,
                    browser: Some(btx),
                    runner: Some(rtx),
                    cancel,
                },
            );
            tokio::spawn(screen_task(app.clone(), sid, brx, rrx, cancelled));
            json!({"ticket": ticket})
        }
        Action::Activate { id: sid } => {
            let s = hub
                .screens
                .get_mut(&sid)
                .ok_or_else(|| error(StatusCode::CONFLICT, "session ended"))?;
            s.active = true;
            json!({})
        }
        Action::Pending { runner_id } => {
            json!({"sessions": hub.screens.values().filter(|s| s.active && s.runner_id == runner_id && s.runner.is_some())
            .map(|s| json!({"session_id": s.id, "control": s.control})).collect::<Vec<_>>()})
        }
        Action::End {
            id: sid,
            code,
            reason,
        } => {
            if reason.len() > 100 {
                return Err(error(StatusCode::BAD_REQUEST, "reason too long"));
            }
            hub.close(&sid, Close::new(code, &reason));
            json!({})
        }
    };
    let mut answer = answer;
    answer["boot"] = json!(hub.boot);
    hub.sequence = sequence;
    hub.reply = Some((digest, answer.clone()));
    Ok(Json(answer))
}
#[derive(Deserialize)]
struct TicketQuery {
    #[serde(default)]
    ticket: String,
}
async fn browser(
    State(app): State<App>,
    Path(rid): Path<String>,
    Query(query): Query<TicketQuery>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    let origin = headers.get("origin").and_then(|v| v.to_str().ok());
    if query.ticket.len() != 64
        || !app
            .policy(json!({"kind": "browser", "origin": origin}))
            .await
    {
        return StatusCode::FORBIDDEN.into_response();
    }
    let target = {
        let mut hub = app.hub.lock().unwrap();
        hub.sweep();
        let digest = Sha256::digest(query.ticket.as_bytes());
        let s = hub.screens.values_mut().find(|s| {
            s.active
                && s.runner_id == rid
                && s.ticket
                    .as_ref()
                    .is_some_and(|t| bool::from(Sha256::digest(t.as_bytes()).ct_eq(&digest)))
        });
        s.and_then(|s| {
            s.ticket = None;
            s.started = Some(Instant::now());
            s.browser.take()
        })
    };
    match target {
        Some(sender) => ws
            .protocols(["binary"])
            .max_message_size(MESSAGE_BYTES)
            .max_frame_size(MESSAGE_BYTES)
            .write_buffer_size(65536)
            .max_write_buffer_size(MESSAGE_BYTES + 65536 + 128)
            .on_upgrade(move |socket| async move {
                let _ = sender.send(socket);
            }),
        None => StatusCode::FORBIDDEN.into_response(),
    }
}
async fn runner(
    State(app): State<App>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    let identity = {
        let mut hub = app.hub.lock().unwrap();
        hub.sweep();
        hub.screens
            .get(&sid)
            .filter(|s| s.active && s.runner.is_some())
            .map(|s| (s.runner_id.clone(), s.control))
    };
    let Some((rid, control)) = identity else {
        return StatusCode::FORBIDDEN.into_response();
    };
    let bearer = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if bearer.len() > 8192
        || !app
            .policy(
                json!({"kind": "runner", "authorization": bearer, "runner_id": rid,
                                              "session_id": sid, "control": control}),
            )
            .await
    {
        return StatusCode::FORBIDDEN.into_response();
    }
    let target = {
        let mut hub = app.hub.lock().unwrap();
        hub.sweep();
        hub.screens
            .get_mut(&sid)
            .filter(|s| s.active)
            .and_then(|s| s.runner.take())
    };
    match target {
        Some(sender) => ws
            .max_message_size(MESSAGE_BYTES)
            .max_frame_size(MESSAGE_BYTES)
            .write_buffer_size(65536)
            .max_write_buffer_size(MESSAGE_BYTES + 65536 + 128)
            .on_upgrade(move |socket| async move {
                let _ = sender.send(socket);
            }),
        None => StatusCode::FORBIDDEN.into_response(),
    }
}
async fn close_socket(socket: &mut WebSocket, close: &Close) {
    let _ = tokio::time::timeout(
        Duration::from_secs(1),
        socket.send(Message::Close(Some(CloseFrame {
            code: close.code,
            reason: close.reason.clone().into(),
        }))),
    )
    .await;
}
async fn socket_message(
    socket: &mut Option<WebSocket>,
) -> Option<std::result::Result<Message, axum::Error>> {
    match socket {
        Some(s) => s.recv().await,
        None => std::future::pending().await,
    }
}
async fn screen_task(
    app: App,
    sid: String,
    mut browser_rx: oneshot::Receiver<WebSocket>,
    mut runner_rx: oneshot::Receiver<WebSocket>,
    mut cancelled: watch::Receiver<Option<Close>>,
) {
    let mut browser = None;
    let mut runner = None;
    let mut backlog = Vec::new();
    let mut bytes = 0;
    let mut deadline = Instant::now() + TICKET;
    let mut session_deadline = Instant::now() + SESSION;
    let outcome = loop {
        if let Some(close) = cancelled.borrow().clone() {
            break close;
        }
        if let (Some(browser_socket), Some(runner_socket)) = (&mut browser, &mut runner) {
            break relay(
                browser_socket,
                runner_socket,
                &mut backlog,
                &mut cancelled,
                session_deadline,
            )
            .await;
        }
        tokio::select! {
            _ = cancelled.changed() => { if let Some(close) = cancelled.borrow().clone() { break close; } },
            _ = tokio::time::sleep_until(deadline.into()) => break Close::new(4404, if browser.is_some() { "computer did not answer" } else { "nobody opened the screen" }),
            socket = &mut browser_rx, if browser.is_none() => match socket {
                Ok(s) => {
                    browser = Some(s);
                    let started = app.hub.lock().unwrap().screens.get(&sid).and_then(|s| s.started).unwrap_or_else(Instant::now);
                    deadline = started + ANSWER; session_deadline = started + SESSION;
                },
                Err(_) => break Close::new(1000, "browser closed"),
            },
            socket = &mut runner_rx, if runner.is_none() => match socket {
                Ok(s) => runner = Some(s), Err(_) => break Close::new(1000, "computer closed"),
            },
            message = socket_message(&mut runner), if browser.is_none() => match message {
                Some(Ok(msg @ (Message::Binary(_) | Message::Text(_)))) => {
                    bytes += match &msg { Message::Binary(b) => b.len(), Message::Text(t) => t.len(), _ => 0 };
                    if bytes > BYTES { break Close::new(1011, "computer sent too much before the browser came"); }
                    backlog.push(msg);
                },
                Some(Ok(Message::Ping(_) | Message::Pong(_))) => {},
                _ => break Close::new(1000, "computer closed"),
            },
        }
    };
    app.hub.lock().unwrap().close(&sid, outcome.clone());
    if let Some(s) = browser.as_mut() {
        close_socket(s, &outcome).await;
    }
    if let Some(s) = runner.as_mut() {
        close_socket(s, &outcome).await;
    }
}
async fn relay(
    browser: &mut WebSocket,
    runner: &mut WebSocket,
    backlog: &mut Vec<Message>,
    cancel: &mut watch::Receiver<Option<Close>>,
    deadline: Instant,
) -> Close {
    for message in backlog.drain(..) {
        tokio::select! {
            _ = cancel.changed() => return cancel.borrow().clone().unwrap_or_else(|| Close::new(1012, "relay stopped")),
            _ = tokio::time::sleep_until(deadline.into()) => return Close::new(4408, "time limit"),
            result = tokio::time::timeout(WRITE, browser.send(message)) => if !matches!(result, Ok(Ok(()))) { return Close::new(1011, "slow browser"); },
        }
    }
    let (mut bs, mut br) = browser.split();
    let (mut rs, mut rr) = runner.split();
    let b_to_r = async {
        while let Some(Ok(message)) = br.next().await {
            if matches!(message, Message::Close(_)) {
                return Close::new(1000, "");
            }
            if matches!(message, Message::Ping(_) | Message::Pong(_)) {
                continue;
            }
            if !matches!(
                tokio::time::timeout(WRITE, rs.send(message)).await,
                Ok(Ok(()))
            ) {
                return Close::new(1011, "slow computer");
            }
        }
        Close::new(1000, "")
    };
    let r_to_b = async {
        while let Some(Ok(message)) = rr.next().await {
            if matches!(message, Message::Close(_)) {
                return Close::new(1000, "");
            }
            if matches!(message, Message::Ping(_) | Message::Pong(_)) {
                continue;
            }
            if !matches!(
                tokio::time::timeout(WRITE, bs.send(message)).await,
                Ok(Ok(()))
            ) {
                return Close::new(1011, "slow browser");
            }
        }
        Close::new(1000, "")
    };
    tokio::select! {
        close = b_to_r => close, close = r_to_b => close,
        _ = tokio::time::sleep_until(deadline.into()) => Close::new(4408, "time limit"),
        _ = cancel.changed() => cancel.borrow().clone().unwrap_or_else(|| Close::new(1012, "relay stopped")),
    }
}
#[tokio::main]
async fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    if std::env::args().any(|arg| arg == "--build-id") {
        println!("{}", env!("TICO_SCREEN_BUILD_ID"));
        return Ok(());
    }
    let key = std::env::var("TICO_SCREEN_RELAY_KEY")?;
    if key.len() < 32 {
        return Err("TICO_SCREEN_RELAY_KEY must contain at least 32 bytes".into());
    }
    let python =
        std::env::var("TICO_SCREEN_PYTHON_URL").unwrap_or_else(|_| "http://127.0.0.1:8765".into());
    let bind = std::env::var("TICO_SCREEN_RELAY_BIND").unwrap_or_else(|_| "127.0.0.1:8766".into());
    if std::env::args().any(|arg| arg == "--healthcheck") {
        let health = std::env::var("TICO_SCREEN_RELAY_HEALTH_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8766/healthz".into());
        let response = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()?
            .get(health)
            .send()
            .await?;
        return if response.status().is_success() {
            Ok(())
        } else {
            Err("relay is unhealthy".into())
        };
    }
    let app = App {
        hub: Arc::new(Mutex::new(Hub::new())),
        key: Arc::new(key),
        python: Arc::new(python.trim_end_matches('/').into()),
        trusted_proxies: Arc::new(gateway::trusted_proxies(
            &std::env::var("TICO_TRUSTED_PROXIES").unwrap_or_else(|_| "127.0.0.1/32,::1/128".into()),
        )?),
        // The backend closes a keep-alive connection after 5 s idle (backend/server.py). Dropping ours first means no
        // request is sent on a connection the backend is closing, which would come back as a bare 502.
        proxy: reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(2))
            .pool_idle_timeout(Duration::from_secs(4))
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .build()?,
        http: reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .redirect(reqwest::redirect::Policy::none())
            .build()?,
    };
    let sweep = app.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(250));
        loop {
            interval.tick().await;
            sweep.hub.lock().unwrap().sweep();
        }
    });
    let routes = Router::new()
        .route("/healthz", get(|| async { StatusCode::OK }))
        .route("/control/v1/hello", get(hello))
        .route("/control/v1/start", post(start))
        .route("/control/v1/command", post(command))
        .route("/api/v2/runners/{rid}/screen/ws", get(browser))
        .route("/api/v2/runners/me/screen/{sid}/ws", get(runner))
        .layer(DefaultBodyLimit::max(131072))
        .with_state(app.clone());
    let listener = tokio::net::TcpListener::bind(&bind).await?;
    let public = match std::env::var("TICO_SCREEN_PUBLIC_BIND") {
        Ok(bind) => Some(tokio::net::TcpListener::bind(bind).await?),
        Err(_) => None,
    };
    println!("screen relay listening on {}", listener.local_addr()?);
    if let Some(public) = public {
        let gateway = gateway::routes(app.clone());
        tokio::spawn(async move {
            if let Err(error) = axum::serve(public, gateway.into_make_service_with_connect_info::<std::net::SocketAddr>()).await {
                eprintln!("screen gateway stopped: {error}");
                std::process::exit(1);
            }
        });
    }
    axum::serve(listener, routes)
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            app.hub
                .lock()
                .unwrap()
                .close_all(Close::new(1012, "relay stopped"));
        })
        .await?;
    Ok(())
}
