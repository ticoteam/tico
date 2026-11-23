//! One provider request per model tunnel. Only the exit runner handles the plan credential.
use crate::{
    browser_exit::{
        connect, read_head, valid_ticket, valid_tunnel_id, Socket, HEAD_BYTES, TUNNELS,
    },
    client::Client,
    gateway_relay::{find, peer_is, same, turn_token},
};
use futures_util::{Sink, SinkExt, Stream, StreamExt};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{io, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::oneshot,
    task::{JoinHandle, JoinSet},
};
use tokio_tungstenite::tungstenite::{Bytes, Error as WsError, Message};

pub const CAPABILITY: &str = "model_exit_v1";
const TIME: Duration = Duration::from_secs(25);
const BODY: usize = 32 * 1024 * 1024;
const FRAME: usize = 64 * 1024;
const CLAUDE: &str = "api.anthropic.com";
const CODEX: &str = "chatgpt.com";
const BAD_REQUEST: &str = "model_exit_bad_request";
const UNAVAILABLE: &str = "model_exit_unavailable";

#[derive(Clone, Copy)]
pub enum Provider {
    Claude,
    Codex,
}

impl Provider {
    fn host(self) -> &'static str {
        match self {
            Self::Claude => CLAUDE,
            Self::Codex => CODEX,
        }
    }

    fn url(self, path: &str) -> Option<&'static str> {
        match (self, path) {
            (Self::Claude, "/v1/messages") => Some("https://api.anthropic.com/v1/messages"),
            (Self::Claude, "/v1/messages/count_tokens") => {
                Some("https://api.anthropic.com/v1/messages/count_tokens")
            }
            (Self::Claude, "/v1/messages?beta=true") => {
                Some("https://api.anthropic.com/v1/messages?beta=true")
            }
            (Self::Claude, "/v1/messages/count_tokens?beta=true") => {
                Some("https://api.anthropic.com/v1/messages/count_tokens?beta=true")
            }
            (Self::Codex, "/backend-api/codex/responses") => {
                Some("https://chatgpt.com/backend-api/codex/responses")
            }
            _ => None,
        }
    }
}

// The next step owns this handle for the duration of the turn.
#[allow(dead_code)] // The per-turn loopback is dark until harness activation.
pub struct Local {
    pub url: String,
    pub token: String,
    shutdown: Option<oneshot::Sender<()>>,
    task: JoinHandle<()>,
}

impl Local {
    #[allow(dead_code)] // The turn can await cleanup before returning its isolated user.
    pub async fn stop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
        let _ = (&mut self.task).await;
    }
}

#[allow(dead_code)] // The next step starts the loopback after the hub selects a model exit.
pub async fn start(
    client: Client,
    attempt_id: String,
    generation: u64,
    provider: Provider,
    uid: Option<u32>,
) -> io::Result<Local> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).await?;
    let authority = listener.local_addr()?.to_string();
    let url = format!("http://{authority}");
    let token = turn_token();
    let turn = std::sync::Arc::new(Turn {
        client,
        attempt_id,
        generation,
        provider,
        authority,
        token: token.clone(),
    });
    let (shutdown, mut stopping) = oneshot::channel();
    let task = tokio::spawn(async move {
        let mut calls = JoinSet::new();
        loop {
            tokio::select! {
                _ = &mut stopping => break,
                accepted = listener.accept(), if calls.len() < 256 => {
                    let Ok((tcp, _)) = accepted else { break };
                    if uid.is_none_or(|uid| peer_is(&tcp, uid)) {
                        let turn = turn.clone();
                        calls.spawn(async move { let _ = serve_local(tcp, &turn).await; });
                    }
                }
                Some(_) = calls.join_next(), if !calls.is_empty() => {}
            }
        }
        drop(listener);
        calls.shutdown().await;
    });
    Ok(Local {
        url,
        token,
        shutdown: Some(shutdown),
        task,
    })
}

struct Turn {
    client: Client,
    attempt_id: String,
    generation: u64,
    provider: Provider,
    authority: String,
    token: String,
}

struct Request<'a> {
    path: &'a str,
    upstream: &'static str,
    headers: HeaderMap,
    length: usize,
}

impl<'a> Request<'a> {
    fn parse(head: &'a [u8], provider: Provider, authority: &str) -> Option<Self> {
        if head.len() > HEAD_BYTES {
            return None;
        }
        let mut lines = std::str::from_utf8(head).ok()?.split("\r\n");
        let path = lines
            .next()?
            .strip_prefix("POST ")?
            .strip_suffix(" HTTP/1.1")?;
        let upstream = provider.url(path)?;
        let mut headers = HeaderMap::new();
        for line in lines {
            let (name, value) = line.split_once(':')?;
            let name = HeaderName::from_bytes(name.as_bytes()).ok()?;
            let value = HeaderValue::from_str(value.trim_matches([' ', '\t'])).ok()?;
            if headers.insert(name, value).is_some() {
                return None;
            }
        }
        if headers.get("host")?.as_bytes() != authority.as_bytes()
            || [
                "transfer-encoding",
                "content-encoding",
                "expect",
                "trailer",
                "upgrade",
            ]
            .iter()
            .any(|h| headers.contains_key(*h))
            || headers.get("connection").is_some_and(|v| {
                !["close", "keep-alive"]
                    .iter()
                    .any(|s| v.as_bytes().eq_ignore_ascii_case(s.as_bytes()))
            })
        {
            return None;
        }
        let length = headers.get("content-length")?.to_str().ok()?;
        if length.is_empty() || !length.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let length = length.parse().ok().filter(|n| *n <= BODY)?;
        Some(Self {
            path,
            upstream,
            headers,
            length,
        })
    }

    fn protocol_headers(&self, provider: Provider) -> HeaderMap {
        let names: &[&str] = match provider {
            Provider::Claude => &["anthropic-version", "anthropic-beta"],
            Provider::Codex => &["openai-beta", "originator", "session_id", "conversation_id"],
        };
        ["content-type", "accept"]
            .into_iter()
            .chain(names.iter().copied())
            .filter_map(|name| {
                Some((
                    HeaderName::from_static(name),
                    self.headers.get(name)?.clone(),
                ))
            })
            .collect()
    }

    fn canonical(&self, provider: Provider, body: Vec<u8>) -> Vec<u8> {
        let mut wire = format!(
            "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Length: {}\r\n",
            self.path,
            provider.host(),
            self.length
        )
        .into_bytes();
        for (name, value) in self.protocol_headers(provider) {
            let Some(name) = name else { continue };
            wire.extend_from_slice(name.as_str().as_bytes());
            wire.extend_from_slice(b": ");
            wire.extend_from_slice(value.as_bytes());
            wire.extend_from_slice(b"\r\n");
        }
        wire.extend_from_slice(b"\r\n");
        wire.extend(body);
        wire
    }
}

async fn serve_local(mut tcp: TcpStream, turn: &Turn) -> io::Result<()> {
    let Some(wire) = tokio::time::timeout(TIME, async {
        let (head, mut body) = read_head(&mut tcp).await?;
        let req = Request::parse(&head, turn.provider, &turn.authority)?;
        let auth = req
            .headers
            .get("authorization")?
            .to_str()
            .ok()?
            .strip_prefix("Bearer ")?;
        if !same(auth, &turn.token) || body.len() > req.length {
            return None;
        }
        let read = body.len();
        body.resize(req.length, 0);
        tcp.read_exact(&mut body[read..]).await.ok()?;
        Some(req.canonical(turn.provider, body))
    })
    .await
    .ok()
    .flatten() else {
        return local_error(&mut tcp, 400, BAD_REQUEST).await;
    };
    let (mut read, mut write) = tcp.split();
    let mut extra = [0];
    tokio::select! {
        biased;
        _ = read.read(&mut extra) => Ok(()),
        result = async {
            let mut ws = match open(turn).await {
                Ok(ws) => ws,
                Err(code) => return local_error(&mut write, 502, code).await,
            };
            for part in wire.chunks(FRAME).chain(std::iter::once(&[][..])) {
                if ws.send(Message::Binary(part.to_vec().into())).await.is_err() {
                    return local_error(&mut write, 502, UNAVAILABLE).await;
                }
            }
            let mut started = false;
            while let Some(Ok(message)) = ws.next().await {
                match message {
                    Message::Binary(bytes) => {
                        started = true;
                        write.write_all(&bytes).await?;
                    }
                    Message::Text(text) if !started => return local_error(&mut write, 502, refusal(&text)).await,
                    Message::Close(Some(frame)) if !started => return local_error(&mut write, 502, error_code(&frame.reason)).await,
                    Message::Close(_) => break,
                    _ => {}
                }
            }
            if !started {
                return local_error(&mut write, 502, UNAVAILABLE).await;
            }
            write.shutdown().await
        } => result,
    }
}

fn error_code(code: &str) -> &'static str {
    match code {
        "model_exit_offline" => "model_exit_offline",
        "model_exit_stale" => "model_exit_stale",
        "model_exit_busy" => "model_exit_busy",
        "model_exit_redirect" => "model_exit_redirect",
        BAD_REQUEST => BAD_REQUEST,
        _ => UNAVAILABLE,
    }
}

fn refusal(text: &str) -> &'static str {
    let value: Value = serde_json::from_str(text).unwrap_or_default();
    error_code(value["error"].as_str().unwrap_or_default())
}

async fn local_error(mut out: impl AsyncWrite + Unpin, status: u16, code: &str) -> io::Result<()> {
    let body = json!({"error": {"code": code}}).to_string();
    let reason = if status == 400 {
        "Bad Request"
    } else {
        "Bad Gateway"
    };
    let head = format!("HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
    out.write_all(head.as_bytes()).await?;
    out.write_all(body.as_bytes()).await?;
    out.shutdown().await
}

async fn open(turn: &Turn) -> Result<Socket, &'static str> {
    let mut once = turn.client.clone();
    once.retries = 0;
    let grant = once
        .post(
            TUNNELS,
            &json!({
                "kind": "model", "attempt_id": turn.attempt_id, "generation": turn.generation,
            }),
            None,
        )
        .await
        .map_err(|e| error_code(&e.code))?;
    let id = grant["tunnel_id"]
        .as_str()
        .filter(|s| valid_tunnel_id(s))
        .ok_or(UNAVAILABLE)?;
    let ticket = grant["ticket"]
        .as_str()
        .filter(|s| valid_ticket(s))
        .ok_or(UNAVAILABLE)?;
    let mut ws = connect(
        &turn.client,
        &format!("/api/v2/{TUNNELS}/{id}/ws?ticket={ticket}"),
        false,
    )
    .await
    .map_err(|_| UNAVAILABLE)?;
    confirm_open(&mut ws).await?;
    Ok(ws)
}

async fn confirm_open(
    ws: &mut (impl Stream<Item = Result<Message, WsError>> + Unpin),
) -> Result<(), &'static str> {
    match tokio::time::timeout(TIME, next_data(ws)).await {
        Ok(Some(Message::Text(text))) => {
            if serde_json::from_str::<Value>(&text).is_ok_and(|v| v["ok"] == true) {
                Ok(())
            } else {
                Err(refusal(&text))
            }
        }
        Ok(Some(Message::Close(Some(frame)))) => Err(error_code(&frame.reason)),
        _ => Err(UNAVAILABLE),
    }
}

async fn next_data(
    ws: &mut (impl Stream<Item = Result<Message, WsError>> + Unpin),
) -> Option<Message> {
    while let Some(Ok(message)) = ws.next().await {
        if !matches!(message, Message::Ping(_) | Message::Pong(_)) {
            return Some(message);
        }
    }
    None
}

#[derive(Deserialize)]
struct Credential {
    provider: String,
    access_token: String,
    account_id: Option<String>,
}

fn http_builder(builder: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
    builder
        .https_only(true)
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .connect_timeout(TIME)
}

pub(crate) async fn serve_exit(client: &Client, ws: Socket, id: &str, ticket: &str, room: bool) {
    let http = http_builder(crate::tls::http_builder()).build();
    serve_exit_with(client, ws, id, ticket, room, http).await;
}

async fn serve_exit_with(
    client: &Client,
    mut ws: Socket,
    id: &str,
    ticket: &str,
    room: bool,
    http: Result<reqwest::Client, reqwest::Error>,
) {
    let result = async {
        if !room {
            return Err("model_exit_busy");
        }
        let http = http.map_err(|_| UNAVAILABLE)?;
        let mut once = client.clone();
        once.retries = 0;
        let value = once
            .post(
                &format!("{TUNNELS}/redeem"),
                &json!({"tunnel_id": id, "ticket": ticket}),
                None,
            )
            .await
            .map_err(|e| error_code(&e.code))?;
        let credential: Credential = serde_json::from_value(value).map_err(|_| UNAVAILABLE)?;
        ws.send(Message::Text(r#"{"ok":true}"#.into()))
            .await
            .map_err(|_| UNAVAILABLE)?;
        let wire = tokio::time::timeout(TIME, read_wire(&mut ws))
            .await
            .map_err(|_| BAD_REQUEST)??;
        let (mut send, mut receive) = (&mut ws).split();
        tokio::select! {
            biased;
            _ = async {
                while let Some(Ok(Message::Ping(_) | Message::Pong(_))) = receive.next().await {}
            } => Ok(()),
            result = forward(&mut send, &http, &credential, wire) => result,
        }
    }
    .await;
    if let Err(code) = result {
        let _ = ws
            .send(Message::Text(
                json!({"ok": false, "error": code}).to_string().into(),
            ))
            .await;
    }
    let _ = ws.close(None).await;
}

// The sender terminates the request with an empty binary frame, independently of HTTP framing.
async fn read_wire(
    ws: &mut (impl Stream<Item = Result<Message, WsError>> + Unpin),
) -> Result<Vec<u8>, &'static str> {
    let mut wire = Vec::new();
    while let Some(message) = next_data(ws).await {
        match message {
            Message::Binary(bytes) if bytes.is_empty() => return Ok(wire),
            Message::Binary(bytes) if wire.len() + bytes.len() <= HEAD_BYTES + BODY + 4 => {
                wire.extend_from_slice(&bytes)
            }
            _ => break,
        }
    }
    Err(BAD_REQUEST)
}

fn upstream_request(
    http: &reqwest::Client,
    credential: &Credential,
    wire: Vec<u8>,
) -> Result<reqwest::Request, &'static str> {
    let provider = match credential.provider.as_str() {
        "claude" => Provider::Claude,
        "codex" => Provider::Codex,
        _ => return Err(UNAVAILABLE),
    };
    let end = find(&wire, b"\r\n\r\n").ok_or(BAD_REQUEST)?;
    let req = Request::parse(&wire[..end], provider, provider.host()).ok_or(BAD_REQUEST)?;
    if wire.len() - end - 4 != req.length {
        return Err(BAD_REQUEST);
    }
    let mut request = http
        .post(req.upstream)
        .headers(req.protocol_headers(provider))
        .bearer_auth(&credential.access_token)
        .body(Bytes::from(wire).slice(end + 4..));
    if matches!(provider, Provider::Codex) {
        let account = credential
            .account_id
            .as_deref()
            .filter(|s| !s.is_empty())
            .ok_or(UNAVAILABLE)?;
        request = request.header("chatgpt-account-id", account);
    }
    request.build().map_err(|_| UNAVAILABLE)
}

async fn forward(
    send: &mut (impl Sink<Message> + Unpin),
    http: &reqwest::Client,
    credential: &Credential,
    wire: Vec<u8>,
) -> Result<(), &'static str> {
    let request = upstream_request(http, credential, wire)?;
    let mut upstream = http.execute(request).await.map_err(|_| UNAVAILABLE)?;
    let status = upstream.status();
    if status.is_redirection() {
        return Err("model_exit_redirect");
    }
    let head = response_head(status, upstream.headers());
    send.send(Message::Binary(head.into_bytes().into()))
        .await
        .map_err(|_| UNAVAILABLE)?;
    if status == reqwest::StatusCode::NO_CONTENT {
        return Ok(());
    }
    while let Some(chunk) = upstream.chunk().await.map_err(|_| UNAVAILABLE)? {
        for part in chunk.chunks(FRAME) {
            let mut frame = format!("{:X}\r\n", part.len()).into_bytes();
            frame.extend_from_slice(part);
            frame.extend_from_slice(b"\r\n");
            send.send(Message::Binary(frame.into()))
                .await
                .map_err(|_| UNAVAILABLE)?;
        }
    }
    send.send(Message::Binary(b"0\r\n\r\n".to_vec().into()))
        .await
        .map_err(|_| UNAVAILABLE)
}

fn response_head(status: reqwest::StatusCode, headers: &HeaderMap) -> String {
    use std::fmt::Write as _;

    let content_type = headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("application/octet-stream");
    let mut head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {content_type}\r\n",
        status.as_u16(),
        status.canonical_reason().unwrap_or(""),
    );
    for (name, value) in headers {
        let name = name.as_str();
        if matches!(
            name,
            "retry-after"
                | "x-should-retry"
                | "request-id"
                | "x-request-id"
                | "openai-request-id"
                | "x-openai-request-id"
        ) || name.starts_with("anthropic-ratelimit-unified-")
            || name.starts_with("x-codex-")
        {
            if let Ok(value) = value.to_str() {
                let _ = write!(head, "{name}: {value}\r\n");
            }
        }
    }
    if status != reqwest::StatusCode::NO_CONTENT {
        head.push_str("Transfer-Encoding: chunked\r\n");
    }
    head.push_str("Connection: close\r\n\r\n");
    head
}

#[cfg(test)]
mod tests;
