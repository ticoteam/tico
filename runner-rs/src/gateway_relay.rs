//! The Tico AI Gateway's key stays with this runner: a turn on a Tico model is given a relay instead.
//!
//! The key is the company's, shared by every bot on a Tico model, and a turn's own commands run beside the harness that
//! makes its model calls: whatever the harness's environment holds, they can read (pi hands its bash tool its whole
//! environment, and `/proc/<pid>/environ` keeps what a process started with). So the server hands the key to this
//! runner alone (`GET runner-gateway-credentials`, with the runner's own token, which a turn never holds) and the turn
//! gets a relay on 127.0.0.1 with a token good for that turn only, in the same two variables (TICOAI_GATEWAY_URL /
//! TICOAI_GATEWAY_KEY). The relay checks the turn's token, then sends the request to the Gateway with the key, and
//! streams the answer back. A redirect is never followed or passed on, and a plain-http Gateway (only ever this
//! computer, `safe_upstream`) is reached directly, never through a proxy.
//!
//! Under isolation (crate::isolation) the turn runs as another user and cannot read this process at all. Without it,
//! the turn's user could read this process and the runner's registration, so a runner without isolation hands a turn
//! no relay: a turn on a Tico model is refused there, and a Tico fallback is left out. The relay passes on one call
//! only, a model call (`POST /chat/completions`): the key opens nothing else at the Gateway for a turn.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::client::Client;
use crate::env::{Env, Vault};
use crate::util::text_of;

pub use crate::hosts::pi::{GATEWAY_DIR_ENV as DIR_ENV, GATEWAY_KEY_ENV as KEY_ENV, GATEWAY_URL_ENV as URL_ENV};

/// The relay's path; what follows it goes after the Gateway's base URL.
const PREFIX: &str = "/gateway";
/// The one Gateway route a turn may reach: an OpenAI-chat model call.
const MODEL_CALL: &str = "/chat/completions";
/// Why a turn on a Tico model is refused on a runner that does not run bot code as another user, ending with the code
/// the server reads it by (clients/gateway.py `not_isolated`): the run is not retried, since it would be refused again.
pub const NOT_ISOLATED: &str = "Tico models need a runner that runs bots as their own user (Tico's Docker runner). This \
computer can't, so pick another model or move the bot (not_isolated)";
/// A turn on a Tico model whose Gateway key the server did not hand this runner: it cannot start here either.
pub const NO_KEY: &str = "This computer has no Tico AI Gateway key for this turn (not_isolated)";
const MAX_HEAD: usize = 64 * 1024;
const MAX_BODY: usize = 32 * 1024 * 1024;
/// HQ keeps a rotated key for 24 hours. End this attempt before then, leaving one model call's 90-second budget.
const MAX_RUN_SECONDS: u64 = 24 * 60 * 60 - 90;
/// The bot's opaque id for the Gateway's ledger (clients/gateway.py BOT_HEADER): the relay's, never the turn's.
const BOT_HEADER: &str = "x-tico-bot";
/// The server hands the bot id under this name, beside the key and URL (backend/gateway_connection.py RUN_BOT_ENV).
const BOT_ENV: &str = "TICOAI_GATEWAY_BOT";
/// Request headers never passed on: the turn's token, the bot id it could name itself, and what belongs to this
/// connection only, and the harness's user agent (the relay sends Tico's own).
const DROP_REQUEST: &[&str] = &[
    "authorization", BOT_HEADER, "host", "connection", "content-length", "transfer-encoding", "proxy-authorization",
    "proxy-connection", "keep-alive", "te", "upgrade", "expect", "trailer", "user-agent",
];
/// Response headers never passed back: the relay's answer ends when it closes the connection.
const DROP_RESPONSE: &[&str] =
    &["connection", "keep-alive", "transfer-encoding", "content-length", "proxy-authenticate", "trailer", "upgrade", "te"];

/// A running relay for one turn. Dropping it (with the turn's vault) stops it and every call still open on it, and
/// removes the turn's own pi configuration directory (`dir`).
pub struct Relay {
    pub url: String,
    pub token: String,
    pub dir: Option<std::path::PathBuf>,
    deadline: Instant,
    retry_at: Arc<AtomicU64>,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    task: tokio::task::JoinHandle<()>,
}

impl Relay {
    pub async fn stop(&mut self) {
        if let Some(stop) = self.shutdown.take() { let _ = stop.send(()); }
        let _ = (&mut self.task).await;
    }
    /// The Gateway's Retry-After, including a reset that passed while the harness was finishing.
    pub fn limit_reset(&self) -> Option<String> {
        let at = self.retry_at.load(Ordering::SeqCst);
        (at != 0).then(|| text_of(&crate::util::iso(&serde_json::json!((at as f64).max(crate::util::epoch_s() + 1.0)))))
    }

    /// Every harness in this attempt shares the deadline from when its key was pinned. A fallback never resets it.
    pub fn limit(&self, requested: Instant) -> Instant {
        requested.min(self.deadline)
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.task.abort();
        if let Some(dir) = &self.dir {
            let _ = std::fs::remove_dir_all(dir);
        }
    }
}

/// Where the turn of attempt `aid` keeps the pi configuration that names its relay: one directory per attempt, since
/// every turn of every bot shares the workspace and two turns starting together would otherwise overwrite each other's
/// relay URL.
/// The fallback `hop`, unless it is a Tico model and this turn has no relay (a runner without isolation, or no key):
/// such a hop could only fail, and the primary's own outcome is the truer one.
pub fn admit(hop: Option<crate::fallback::Hop>, relayed: bool) -> Option<crate::fallback::Hop> {
    hop.filter(|h| relayed || !crate::hosts::pi::is_gateway(Some(&h.model)))
}

struct Shared {
    client: reqwest::Client,
    base: String,
    key: String,
    token: String,
    bot: Option<String>,
    retry_at: Arc<AtomicU64>,
}

/// `url` without a trailing slash, when the key may go there: https, or plain http only to this computer named
/// exactly, with no user, password, query or fragment. The server checks the same (clients/gateway.py `safe_base`).
pub fn safe_upstream(url: &str) -> Option<String> {
    let base = url.trim().trim_end_matches('/');
    if base.chars().any(|c| c.is_whitespace() || c.is_control() || matches!(c, '\\' | '@' | '?' | '#')) {
        return None;
    }
    let parsed = url::Url::parse(base).ok()?;
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return None;
    }
    let host = parsed.host_str().unwrap_or_default().to_ascii_lowercase();
    let allowed = match parsed.scheme() {
        "https" => !host.is_empty(),
        "http" => matches!(host.as_str(), "localhost" | "127.0.0.1" | "[::1]"),
        _ => false,
    };
    allowed.then(|| base.to_string())
}

/// The Gateway's key, URL and the bot's opaque id (when the server has one) for attempt `aid`, from the server with the
/// runner's token, or None when the run gets none.
pub async fn credentials(client: &Client, aid: &str) -> Result<Option<(String, String, Option<String>)>, String> {
    let got = client
        .get_query("runner-gateway-credentials", &[("attempt", aid.to_string())])
        .await
        .map_err(|e| format!("runner-gateway-credentials: {}", e.detail))?;
    let items = got["credentials"].as_array().cloned().unwrap_or_default();
    let of = |env: &str| items.iter().find(|i| i["env"].as_str() == Some(env)).map(|i| text_of(&i["value"]));
    let bot = of(BOT_ENV).filter(|b| b.starts_with("gb_") && b.len() <= 64 && b.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'));
    Ok(match (of(KEY_ENV), of(URL_ENV)) {
        (Some(key), Some(url)) if !key.is_empty() && !url.is_empty() => Some((key, url, bot)),
        _ => None,
    })
}

/// Whether a claimed attempt may call the Gateway (the server's `gateway` flag on the claim).
pub fn wanted(attempt: &Value) -> bool {
    crate::util::truthy(attempt.get("gateway"))
}

/// Give a turn on a Tico model its relay: the relay's URL and the turn's own token in `env`, in place of anything else
/// by those names; the key itself only in `vault` (to scrub from what the turn says, and to stop the relay with it).
/// `isolated` is whether bot code runs as another user here (crate::isolation::enabled): without it there is no relay,
/// so a turn whose own model is a Tico model is refused, and one with only a Tico fallback runs without it.
pub async fn hand_to_turn(
    client: &Client,
    attempt: &Value,
    env: &mut Env,
    vault: &mut Vault,
    isolated: bool,
) -> Result<(), String> {
    if !wanted(attempt) {
        return Ok(());
    }
    env.remove(KEY_ENV);
    env.remove(URL_ENV);
    env.remove(DIR_ENV);
    if !isolated {
        if crate::hosts::pi::is_gateway(attempt["config"]["model"].as_str()) {
            return Err(NOT_ISOLATED.into());
        }
        crate::util::log(&format!("{}: no Tico fallback here: {NOT_ISOLATED}", text_of(&attempt["id"])));
        return Ok(());
    }
    // The key first: a Gateway turn without one is settled unstarted (`not_isolated`, from its error event), and
    // its leased user goes back to the pool unused.
    // A lease taken for a Gateway turn goes back early; one taken because the harness runs as its own user
    // (`isolated_user`) stays with the turn, which returns it at cleanup.
    let gateway = crate::hosts::pi::is_gateway(attempt["config"]["model"].as_str());
    let release = |vault: &mut Vault| if gateway { if let Some(mut user) = vault.turn_user.take() { user.permit_reuse(); } };
    let pinned = match credentials(client, &text_of(&attempt["id"])).await {
        Ok(pinned) => pinned,
        Err(e) => { release(vault); return Err(e); }
    };
    let Some((key, url, bot)) = pinned else {
        release(vault);
        return if crate::hosts::pi::is_gateway(attempt["config"]["model"].as_str()) { Err(NO_KEY.into()) } else { Ok(()) };
    };
    let mut user = match vault.turn_user.take().map(Ok).unwrap_or_else(crate::turn_users::Lease::take) {
        Ok(user) => user,
        Err(e) if !crate::hosts::pi::is_gateway(attempt["config"]["model"].as_str()) => {
            crate::util::log(&format!("Tico runner: no Tico fallback for this attempt: {e}"));
            return Ok(());
        }
        Err(e) => return Err(e),
    };
    vault.values.push(key.clone());
    let relay = match start_for_user(&url, key, bot, Some(user.uid)).await {
        Ok(relay) => relay,
        Err(e) => { user.permit_reuse(); return Err(e); }
    };
    attach(relay, user.dir.join("pi"), env, vault);
    vault.turn_user = Some(user);
    Ok(())
}

fn attach(relay: Relay, dir: std::path::PathBuf, env: &mut Env, vault: &mut Vault) {
    env.insert(URL_ENV.into(), relay.url.clone());
    env.insert(KEY_ENV.into(), relay.token.clone());
    env.insert(DIR_ENV.into(), dir.display().to_string());
    vault.values.push(relay.token.clone());
    vault.relay = Some(relay);
}

/// The agent every call to the Gateway carries: Cloudflare in front of Tico HQ refuses a library's default agent
/// before the call reaches the Gateway, and a harness's own agent (pi's) is not one Tico controls.
pub fn user_agent() -> String {
    format!("Tico-Client/{}", crate::runner::RUNNER_VERSION)
}

/// Start a relay to the Gateway at `upstream` with `key`, naming the turn's bot by `bot` on every call.
#[cfg(test)]
pub async fn start(upstream: &str, key: String, bot: Option<String>) -> Result<Relay, String> {
    start_for_user(upstream, key, bot, None).await
}

async fn start_for_user(upstream: &str, key: String, bot: Option<String>, uid: Option<u32>) -> Result<Relay, String> {
    let deadline = Instant::now() + Duration::from_secs(MAX_RUN_SECONDS);
    let base = safe_upstream(upstream).ok_or("the Gateway URL is neither https nor this computer")?;
    let local = base.starts_with("http://");
    let mut builder = crate::tls::http_builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(30))
        .user_agent(user_agent());
    if local {
        builder = builder.no_proxy();
    }
    let client = builder.build().map_err(|e| e.to_string())?;
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let token = format!("tico-relay-{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
    let retry_at = Arc::new(AtomicU64::new(0));
    let shared = Arc::new(Shared { client, base, key, token: token.clone(), bot, retry_at: retry_at.clone() });
    let (shutdown, mut stopping) = tokio::sync::oneshot::channel();
    let task = tokio::spawn(async move {
        let mut calls = tokio::task::JoinSet::new();
        loop {
            tokio::select! {
                _ = &mut stopping => break,
                accepted = listener.accept() => match accepted {
                    Ok((socket, _)) => {
                        if let Some(uid) = uid {
                            if !peer_is(&socket, uid) { continue; }
                        }
                        let shared = shared.clone();
                        calls.spawn(async move { let _ = serve(socket, &shared).await; });
                    }
                    Err(_) => tokio::time::sleep(Duration::from_millis(50)).await,
                },
                Some(_) = calls.join_next(), if !calls.is_empty() => {}
            }
        }
        drop(listener);
        calls.abort_all();
        while calls.join_next().await.is_some() {}
    });
    Ok(Relay { url: format!("http://127.0.0.1:{port}{PREFIX}"), token, dir: None, deadline, retry_at, shutdown: Some(shutdown), task })
}

/// Match the client's reverse four-tuple, rather than the accepted (supervisor-owned) socket.
/// Failure to read or match /proc denies access before any bearer token is consumed.
pub(crate) fn peer_is(socket: &TcpStream, uid: u32) -> bool {
    let (Ok(local), Ok(peer)) = (socket.local_addr(), socket.peer_addr()) else { return false };
    let encode = |address: std::net::SocketAddr| -> Option<String> {
        let std::net::IpAddr::V4(ip) = address.ip() else { return None };
        Some(format!("{:08X}:{:04X}", u32::from_le_bytes(ip.octets()), address.port()))
    };
    let (Some(local), Some(peer)) = (encode(local), encode(peer)) else { return false };
    std::fs::read_to_string("/proc/net/tcp").ok().is_some_and(|table| peer_in_table(&table, &peer, &local, uid))
}

fn peer_in_table(table: &str, peer: &str, local: &str, uid: u32) -> bool {
    table.lines().skip(1).any(|line| {
        let fields: Vec<_> = line.split_whitespace().collect();
        fields.len() > 7 && fields[1] == peer && fields[2] == local && fields[3] == "01" && fields[7].parse::<u32>() == Ok(uid)
    })
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// The same answer however much of `given` matches `expected`.
fn same(given: &str, expected: &str) -> bool {
    let (a, b) = (given.as_bytes(), expected.as_bytes());
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

async fn answer(socket: &mut TcpStream, status: u16, message: &str) -> std::io::Result<()> {
    let body = serde_json::json!({"error": {"message": message, "type": "tico_relay"}}).to_string();
    let reason = reqwest::StatusCode::from_u16(status).ok().and_then(|s| s.canonical_reason()).unwrap_or("");
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    socket.write_all(head.as_bytes()).await?;
    socket.write_all(body.as_bytes()).await?;
    socket.shutdown().await
}

/// One call: read the turn's request, check its token, send it on with the key, and stream the answer back.
async fn serve(mut socket: TcpStream, s: &Shared) -> std::io::Result<()> {
    let mut buf: Vec<u8> = Vec::with_capacity(8192);
    let mut chunk = [0u8; 8192];
    let end = loop {
        if let Some(i) = find(&buf, b"\r\n\r\n") {
            break i;
        }
        if buf.len() > MAX_HEAD {
            return answer(&mut socket, 431, "Request head too large").await;
        }
        let n = socket.read(&mut chunk).await?;
        if n == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    let head = String::from_utf8_lossy(&buf[..end]).to_string();
    let mut lines = head.split("\r\n");
    let mut first = lines.next().unwrap_or_default().split(' ');
    let (method, target) = (first.next().unwrap_or_default().to_string(), first.next().unwrap_or_default().to_string());
    let headers: Vec<(String, String)> =
        lines.filter_map(|l| l.split_once(':')).map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string())).collect();
    let header = |name: &str| headers.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str());
    let given = header("authorization").and_then(|v| v.strip_prefix("Bearer ")).unwrap_or_default();
    if !same(given, &s.token) {
        return answer(&mut socket, 401, "This relay serves its own turn only").await;
    }
    if target.strip_prefix(PREFIX) != Some(MODEL_CALL) {
        return answer(&mut socket, 404, "This relay passes on model calls only").await;
    }
    if method != "POST" {
        return answer(&mut socket, 405, "Model calls are POST").await;
    }
    if header("transfer-encoding").is_some() {
        return answer(&mut socket, 411, "Send the body with a Content-Length").await;
    }
    let Ok(length) = header("content-length").unwrap_or("0").parse::<usize>() else {
        return answer(&mut socket, 400, "Bad Content-Length").await;
    };
    if length > MAX_BODY {
        return answer(&mut socket, 413, "Request too large").await;
    }
    let mut body = buf[end + 4..].to_vec();
    while body.len() < length {
        let n = socket.read(&mut chunk).await?;
        if n == 0 {
            return Ok(());
        }
        body.extend_from_slice(&chunk[..n]);
    }
    body.truncate(length);
    let mut request = s.client.post(format!("{}{MODEL_CALL}", s.base)).bearer_auth(&s.key).body(body);
    for (k, v) in &headers {
        if !DROP_REQUEST.contains(&k.as_str()) {
            request = request.header(k.as_str(), v.as_str());
        }
    }
    if let Some(bot) = &s.bot {
        request = request.header(BOT_HEADER, bot.as_str());
    }
    let mut response = match request.send().await {
        Ok(r) => r,
        Err(_) => return answer(&mut socket, 502, "The Tico AI Gateway did not answer").await,
    };
    let status = response.status();
    let wait = response.headers().get(reqwest::header::RETRY_AFTER).and_then(|v| v.to_str().ok())
        .and_then(|v| v.trim().parse::<u64>().ok()).filter(|s| *s <= 7 * 24 * 60 * 60);
    let at = if status.as_u16() == 429 {
        wait.map(|s| (crate::util::epoch_s() + s.max(1) as f64).ceil() as u64).unwrap_or(0)
    } else { 0 };
    s.retry_at.store(at, Ordering::SeqCst);
    if status.is_redirection() {
        // The key's request went only where it was meant to; a redirect is not followed, and not handed on either.
        return answer(&mut socket, 502, "The Tico AI Gateway answered with a redirect").await;
    }
    let mut head = format!("HTTP/1.1 {} {}\r\n", status.as_u16(), status.canonical_reason().unwrap_or(""));
    for (k, v) in response.headers() {
        if !DROP_RESPONSE.contains(&k.as_str()) {
            if let Ok(v) = v.to_str() {
                head.push_str(&format!("{k}: {v}\r\n"));
            }
        }
    }
    head.push_str("Connection: close\r\n\r\n");
    socket.write_all(head.as_bytes()).await?;
    while let Ok(Some(bytes)) = response.chunk().await {
        socket.write_all(&bytes).await?;
        socket.flush().await?;
    }
    socket.shutdown().await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[tokio::test]
    async fn primary_and_fallback_share_one_deadline_inside_the_rotation_grace() {
        let before = Instant::now();
        let relay = start("http://127.0.0.1:9/v1/gateway", "synthetic-key".into(), None).await.unwrap();
        let contract: Value = serde_json::from_str(include_str!("../../backend/tests/fixtures/gateway_enroll_contract.json")).unwrap();
        let grace = contract["grace_seconds"].as_u64().unwrap();
        assert_eq!(grace, 24 * 60 * 60);
        let ordinary = before + Duration::from_secs(3600);
        assert_eq!(relay.limit(ordinary), ordinary, "a normal turn keeps its own shorter limit");
        let requested = before + Duration::from_secs(2 * grace);
        let primary = relay.limit(requested);
        assert!(primary < before + Duration::from_secs(grace));
        assert!(primary >= before + Duration::from_secs(grace - 90));
        // A later harness asks for a fresh run limit; it still cannot move the key's original deadline.
        assert_eq!(relay.limit(requested + Duration::from_secs(3600)), primary);
    }

    /// A fake Gateway on 127.0.0.1: keeps each request's head and body, answers with `reply` (raw bytes).
    async fn upstream(reply: Vec<u8>) -> (String, Arc<Mutex<Vec<String>>>) {
        upstream_with(Arc::new(move |_: &str| reply.clone())).await
    }

    /// The Gateway behind its edge, which answers 403 to any user agent but Tico's before the Gateway sees the call.
    async fn behind_the_edge(reply: Vec<u8>) -> (String, Arc<Mutex<Vec<String>>>) {
        upstream_with(Arc::new(move |request: &str| {
            let tico = request.lines().any(|l| l.to_ascii_lowercase().starts_with("user-agent: tico-client/"));
            if tico { reply.clone() } else { b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n".to_vec() }
        }))
        .await
    }

    /// `upstream`, answering each request with `reply_to(request)`.
    async fn upstream_with(reply_to: Arc<dyn Fn(&str) -> Vec<u8> + Send + Sync>) -> (String, Arc<Mutex<Vec<String>>>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let seen = Arc::new(Mutex::new(vec![]));
        let kept = seen.clone();
        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { return };
                let mut buf = vec![0u8; 1 << 20];
                let mut got = 0;
                loop {
                    let n = socket.read(&mut buf[got..]).await.unwrap_or(0);
                    got += n;
                    let text = String::from_utf8_lossy(&buf[..got]).to_string();
                    let complete = text.find("\r\n\r\n").is_some_and(|i| {
                        let len = text[..i].lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
                        got >= i + 4 + len
                    });
                    if n == 0 || complete {
                        break;
                    }
                }
                let request = String::from_utf8_lossy(&buf[..got]).to_string();
                let reply = reply_to(&request);
                kept.lock().unwrap().push(request);
                let _ = socket.write_all(&reply).await;
                let _ = socket.shutdown().await;
            }
        });
        (format!("http://127.0.0.1:{port}/v1/gateway"), seen)
    }

    async fn call(relay: &Relay, token: &str, path: &str, body: &str) -> String {
        send(relay, "POST", token, path, body).await
    }

    async fn send(relay: &Relay, method: &str, token: &str, path: &str, body: &str) -> String {
        let mut socket = TcpStream::connect(relay.url.trim_start_matches("http://").split('/').next().unwrap()).await.unwrap();
        let request = format!(
            "{method} {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nUser-Agent: OpenAI/JS 5.8.2\r\nX-Kept: yes\r\nX-Tico-Bot: gb_forged\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        socket.write_all(request.as_bytes()).await.unwrap();
        let mut out = String::new();
        socket.read_to_string(&mut out).await.unwrap();
        out
    }

    const KEY: &str = "tgw-synthetic-fixture-key-0042";
    const SSE: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n10\r\ndata: {\"a\": 1}\n\n\r\n0\r\n\r\n";

    #[tokio::test]
    async fn gateway_retry_after_reaches_the_turn_without_inventing_an_hour() {
        for (status, header, seconds) in [(429, "42", Some(42)), (429, "3600", Some(3600)),
                                         (429, "99999999999999999999", None), (200, "42", None)] {
            let body = r#"{"error":{"code":"rate_limited","message":"Too many requests."}}"#;
            let reply = format!("HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nRetry-After: {header}\r\nContent-Length: {}\r\n\r\n{body}", body.len());
            let (url, _) = upstream(reply.into_bytes()).await;
            let relay = start(&url, KEY.into(), None).await.unwrap();
            assert_eq!(relay.limit_reset(), None);
            let before = crate::util::epoch_s();
            let out = call(&relay, &relay.token, "/gateway/chat/completions", "{}").await;
            assert!(out.contains(&format!("retry-after: {header}")), "the response is still passed through");
            match seconds {
                Some(seconds) => {
                    let reset = crate::util::parse_iso(&relay.limit_reset().unwrap());
                    assert!(reset >= before + seconds as f64 && reset <= crate::util::epoch_s() + seconds as f64 + 1.0);
                }
                None => assert_eq!(relay.limit_reset(), None),
            }
        }
    }

    #[tokio::test]
    async fn a_turn_reaches_the_gateway_with_the_key_it_never_holds() {
        let (url, seen) = upstream(SSE.to_vec()).await;
        let relay = start(&url, KEY.into(), Some("gb_0123456789abcdef".into())).await.unwrap();
        assert!(relay.url.starts_with("http://127.0.0.1:") && relay.url.ends_with("/gateway"));
        assert!(!relay.token.contains(KEY));
        let big = format!("{{\"pad\": \"{}\"}}", "x".repeat(200_000));            // more than one read
        let out = call(&relay, &relay.token, "/gateway/chat/completions", &big).await;
        assert!(out.starts_with("HTTP/1.1 200 OK\r\n"), "{out}");
        assert!(out.contains("content-type: text/event-stream") && out.ends_with("data: {\"a\": 1}\n\n"), "{out}");
        let request = seen.lock().unwrap()[0].clone();
        assert!(request.starts_with("POST /v1/gateway/chat/completions HTTP/1.1\r\n"), "{}", &request[..80]);
        let lower = request.to_ascii_lowercase();
        assert!(lower.contains(&format!("authorization: bearer {}", KEY.to_ascii_lowercase())));
        assert!(!request.contains(&relay.token) && lower.contains("x-kept: yes") && request.ends_with(&big));
        // The bot id is the server's, never one the turn names itself.
        assert!(lower.contains("x-tico-bot: gb_0123456789abcdef") && !lower.contains("gb_forged"), "{request}");
    }

    #[tokio::test]
    async fn a_turn_gets_past_an_edge_that_refuses_any_agent_but_ticos() {
        let (url, seen) = behind_the_edge(SSE.to_vec()).await;
        let relay = start(&url, KEY.into(), None).await.unwrap();
        let out = call(&relay, &relay.token, "/gateway/chat/completions", "{}").await;
        assert!(out.starts_with("HTTP/1.1 200 OK\r\n"), "{out}");
        let request = seen.lock().unwrap()[0].to_ascii_lowercase();
        let agents: Vec<&str> = request.lines().filter(|l| l.starts_with("user-agent:")).collect();
        assert_eq!(agents, [format!("user-agent: tico-client/{}", crate::runner::RUNNER_VERSION)], "{request}");
    }

    #[tokio::test]
    async fn another_token_or_path_gets_nothing_sent() {
        let (url, seen) = upstream(SSE.to_vec()).await;
        let relay = start(&url, KEY.into(), None).await.unwrap();
        for (token, path, status) in [
            ("", "/gateway/chat/completions", "401"),
            ("tico-relay-wrong", "/gateway/chat/completions", "401"),
            (KEY, "/gateway/chat/completions", "401"),
            (relay.token.as_str(), "/other/chat/completions", "404"),
            (relay.token.as_str(), "/gateway/../key", "404"),
            (relay.token.as_str(), "/gateway/%2E%2e/key", "404"),
            (relay.token.as_str(), "/gatewayx/chat/completions", "404"),
            (relay.token.as_str(), "/gateway/decisions", "404"),           // the Gateway's other routes
            (relay.token.as_str(), "/gateway/key", "404"),
            (relay.token.as_str(), "/gateway/chat/completions/../../decisions", "404"),
            (relay.token.as_str(), "/gateway/chat/completions?x=1", "404"),
        ] {
            let out = call(&relay, token, path, "{}").await;
            assert!(out.starts_with(&format!("HTTP/1.1 {status}")), "{token} {path}: {out}");
            assert!(!out.contains(KEY));
        }
        let out = send(&relay, "GET", &relay.token, "/gateway/chat/completions", "").await;
        assert!(out.starts_with("HTTP/1.1 405"), "{out}");
        assert!(seen.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_redirect_is_neither_followed_nor_handed_on() {
        let (elsewhere, other) = upstream(SSE.to_vec()).await;
        let reply = format!("HTTP/1.1 307 Temporary Redirect\r\nLocation: {elsewhere}/chat/completions\r\nContent-Length: 0\r\n\r\n");
        let (url, seen) = upstream(reply.into_bytes()).await;
        let relay = start(&url, KEY.into(), None).await.unwrap();
        let out = call(&relay, &relay.token, "/gateway/chat/completions", "{}").await;
        assert!(out.starts_with("HTTP/1.1 502") && !out.to_ascii_lowercase().contains("location:"), "{out}");
        assert_eq!(seen.lock().unwrap().len(), 1);
        assert!(other.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_stopped_relay_takes_no_more_calls() {
        let (url, _) = upstream(SSE.to_vec()).await;
        let relay = start(&url, KEY.into(), None).await.unwrap();
        let address = relay.url.trim_start_matches("http://").split('/').next().unwrap().to_string();
        drop(relay);
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(TcpStream::connect(address).await.is_err());
    }

    #[tokio::test]
    async fn a_turn_holds_the_relay_and_its_own_token_never_the_key() {
        let (gateway, seen) = upstream(SSE.to_vec()).await;
        let answer = serde_json::json!({"credentials": [
            {"id": "gateway-key", "env": KEY_ENV, "kind": "api_key", "value": KEY},
            {"id": "gateway-url", "env": URL_ENV, "kind": "text", "value": gateway},
            {"id": "gateway-bot", "env": BOT_ENV, "kind": "text", "value": "gb_00112233aabbccdd"}]}).to_string();
        let (hub, asked) = upstream(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{answer}", answer.len()).into_bytes()).await;
        let hub = hub.trim_end_matches("/v1/gateway").to_string();
        let client = Client::new(&hub, "runner-token", 5, 0).unwrap();
        let (mut env, mut vault) = (Env::new(), Vault::default());
        env.insert(KEY_ENV.into(), KEY.into());                    // from anywhere else: never kept
        env.insert("PATH".into(), "/usr/bin".into());
        hand_to_turn(&client, &serde_json::json!({"id": "att-1", "gateway": false}), &mut env, &mut vault, true).await.unwrap();
        assert!(asked.lock().unwrap().is_empty() && vault.relay.is_none());
        let (key, url, bot) = credentials(&client, "att-1").await.unwrap().unwrap();
        vault.values.push(key.clone());
        let relay = start(&url, key, bot).await.unwrap();
        attach(relay, std::env::temp_dir().join("unused-pi-config"), &mut env, &mut vault);
        let request = asked.lock().unwrap()[0].clone();
        assert!(request.starts_with("GET /api/v2/runner-gateway-credentials?attempt=att-1 "), "{request}");
        assert!(request.contains("Bearer runner-token"));
        assert!(env.values().all(|v| !v.contains(KEY)), "{env:?}");
        let relay = vault.relay.as_ref().unwrap();
        assert_eq!((env[URL_ENV].as_str(), env[KEY_ENV].as_str()), (relay.url.as_str(), relay.token.as_str()));
        assert_eq!(vault.values[0], KEY);
        assert!(vault.values.contains(&vault.relay.as_ref().unwrap().token));
        assert!(!env.contains_key(BOT_ENV), "the bot id is the relay's, not the turn's: {env:?}");
        let out = call(relay, &env[KEY_ENV], "/gateway/chat/completions", "{}").await;
        assert!(out.starts_with("HTTP/1.1 200") && seen.lock().unwrap().len() == 1, "{out}");
        // The bot id the server handed with the key reaches the Gateway; the one the turn sent does not.
        let sent = seen.lock().unwrap()[0].to_ascii_lowercase();
        assert!(sent.contains("x-tico-bot: gb_00112233aabbccdd") && !sent.contains("gb_forged"), "{sent}");
    }

    #[tokio::test]
    async fn a_runner_without_isolation_hands_a_turn_no_relay() {
        let (hub, asked) = upstream(b"HTTP/1.1 500 X\r\nContent-Length: 0\r\n\r\n".to_vec()).await;
        let client = Client::new(hub.trim_end_matches("/v1/gateway"), "runner-token", 5, 0).unwrap();
        for (config, refused) in [(serde_json::json!({"model": "tico-mid-1.0"}), true),
                                  (serde_json::json!({"model": "opus-5-5", "fallback": {"model": "tico-mid-1.0"}}), false)] {
            let (mut env, mut vault) = (Env::new(), Vault::default());
            env.insert(KEY_ENV.into(), KEY.into());
            env.insert(URL_ENV.into(), "https://gw.example/v1/gateway".into());
            let attempt = serde_json::json!({"id": "att-1", "gateway": true, "config": config});
            let got = hand_to_turn(&client, &attempt, &mut env, &mut vault, false).await;
            assert_eq!(got.is_err(), refused, "{config}");
            if refused {
                assert_eq!(got.unwrap_err(), NOT_ISOLATED);
            }
            assert!(!env.contains_key(KEY_ENV) && !env.contains_key(URL_ENV) && vault.relay.is_none() && vault.values.is_empty());
        }
        assert!(asked.lock().unwrap().is_empty());                // the key is never even fetched
    }

    #[tokio::test]
    async fn a_tico_model_turn_without_a_gateway_key_fails_as_not_isolated() {
        let answer = r#"{"credentials": []}"#;
        let (hub, asked) = upstream(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{answer}", answer.len()).into_bytes()).await;
        let client = Client::new(hub.trim_end_matches("/v1/gateway"), "runner-token", 5, 0).unwrap();
        let (mut env, mut vault) = (Env::new(), Vault::default());
        let attempt = serde_json::json!({"id": "att-1", "gateway": true, "config": {"model": "tico-mid-1.0"}});
        // execute.rs records this as the turn's error event and reports failed, which the server settles unstarted.
        let got = hand_to_turn(&client, &attempt, &mut env, &mut vault, true).await.unwrap_err();
        assert!(got.ends_with("(not_isolated)"), "{got}");
        assert_eq!(asked.lock().unwrap().len(), 1);
        assert!(vault.relay.is_none() && vault.turn_user.is_none() && !env.contains_key(KEY_ENV));
    }

    /// The hub's answer to the runner's credential request: the Gateway at `gateway` with KEY.
    async fn hub_for(gateway: &str) -> (Client, Arc<Mutex<Vec<String>>>) {
        let answer = serde_json::json!({"credentials": [
            {"id": "gateway-key", "env": KEY_ENV, "kind": "api_key", "value": KEY},
            {"id": "gateway-url", "env": URL_ENV, "kind": "text", "value": gateway}]}).to_string();
        let (hub, asked) = upstream(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{answer}", answer.len()).into_bytes()).await;
        (Client::new(hub.trim_end_matches("/v1/gateway"), "runner-token", 5, 0).unwrap(), asked)
    }

    #[tokio::test]
    async fn two_turns_starting_together_each_reach_their_own_relay_and_leave_no_configuration_behind() {
        let ws = std::env::temp_dir().join(format!("relay-overlap-{}", std::process::id()));
        let mut turns = vec![];
        for aid in ["att-a", "att-b"] {
            let (gateway, seen) = upstream(SSE.to_vec()).await;
            let (client, _) = hub_for(&gateway).await;
            let (mut env, mut vault) = (Env::new(), Vault::default());
            env.insert("HUB_WORKSPACE".into(), ws.display().to_string());
            let (key, url, bot) = credentials(&client, aid).await.unwrap().unwrap();
            let mut relay = start(&url, key, bot).await.unwrap();
            let dir = ws.join("runtime/pi-gateway").join(aid);
            relay.dir = Some(dir.clone());
            attach(relay, dir, &mut env, &mut vault);
            turns.push((env, vault, seen));
        }
        // Both turns' pi configurations are written before either turn calls.
        let settings = crate::hosts::Settings::default();
        let dirs: Vec<_> = turns.iter().map(|(env, ..)| crate::hosts::pi::gateway_agent_dir(&settings, env).unwrap()).collect();
        assert_ne!(dirs[0], dirs[1]);
        let (a, b) = (turns[0].1.relay.as_ref().unwrap(), turns[1].1.relay.as_ref().unwrap());
        assert!(a.url != b.url && a.token != b.token);
        for ((env, vault, seen), dir) in turns.iter().zip(&dirs) {
            assert!(dir.starts_with(ws.join("runtime/pi-gateway")));
            let relay = vault.relay.as_ref().unwrap();
            let models: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("models.json")).unwrap()).unwrap();
            assert_eq!(models["providers"]["tico"]["baseUrl"], relay.url.as_str());
            let out = call(relay, &env[KEY_ENV], "/gateway/chat/completions", "{}").await;
            assert!(out.starts_with("HTTP/1.1 200"), "{out}");
            assert_eq!(seen.lock().unwrap().len(), 1);              // its own Gateway, not the other turn's
        }
        drop(turns);
        assert!(dirs.iter().all(|d| !d.exists()), "{dirs:?}");
        let _ = std::fs::remove_dir_all(&ws);
    }

    #[tokio::test]
    async fn a_runner_without_isolation_skips_a_tico_fallback_and_keeps_the_primarys_outcome() {
        // What execute.rs does for a Claude turn that hit its limit: hand the turn the Gateway, then admit its fallback.
        let (hub, asked) = upstream(b"HTTP/1.1 500 X\r\nContent-Length: 0\r\n\r\n".to_vec()).await;
        let client = Client::new(hub.trim_end_matches("/v1/gateway"), "runner-token", 5, 0).unwrap();
        let config = serde_json::json!({"runtime": "claude", "model": "opus-5-5", "fallback": {"harness": "pi", "model": "tico-mid-1.0"}});
        let (mut env, mut vault) = (Env::new(), Vault::default());
        hand_to_turn(&client, &serde_json::json!({"id": "att-1", "gateway": true, "config": config}), &mut env, &mut vault, false).await.unwrap();
        assert!(vault.relay.is_none() && asked.lock().unwrap().is_empty());   // no Gateway credential fetched
        let hop = crate::fallback::configured(&config);
        assert_eq!(hop.as_ref().unwrap().model, "tico-mid-1.0");
        assert_eq!(admit(hop, vault.relay.is_some()), None);                  // no hop: the primary's limit stands
        // The company's default row too; any other fallback still hops, and a Tico one does with a relay.
        let tico = crate::fallback::Hop { harness: "pi".into(), runtime: "pi".into(), model: "tico-mid-1.0".into(), reasoning_effort: None, default: true };
        let codex = crate::fallback::Hop { harness: "codex".into(), runtime: "codex".into(), model: "gpt-5".into(), reasoning_effort: None, default: true };
        assert_eq!(admit(Some(tico.clone()), false), None);
        assert_eq!(admit(Some(codex.clone()), false), Some(codex));
        assert_eq!(admit(Some(tico.clone()), true), Some(tico));
    }

    #[test]
    fn the_refusal_says_why_and_what_to_do_with_the_code_the_server_reads() {
        assert!(NOT_ISOLATED.starts_with("Tico models need a runner that runs bots as their own user (Tico's Docker runner)."));
        assert!(NOT_ISOLATED.contains("pick another model or move the bot") && NOT_ISOLATED.ends_with("(not_isolated)"));
    }

    #[test]
    fn the_key_goes_only_to_https_or_this_computer() {
        for good in ["https://gw.example/v1/gateway/", "https://GW.example:8443/v1/gateway", "http://localhost:8080/v1/gateway",
                     "http://127.0.0.1:9/v1/gateway", "http://[::1]:9/v1/gateway", "HTTP://LOCALHOST/v1/gateway"] {
            assert!(safe_upstream(good).is_some(), "{good}");
        }
        for bad in ["http://localhost.gw.example/v1/gateway", "http://127.0.0.1.gw.example/v1/gateway", "http://gw.example/v1",
                    "http://localhost@gw.example/v1", "https://user:pw@gw.example/v1", "https://gw.example/v1?x=1",
                    "https://gw.example/v1#x", "ftp://localhost/v1", "localhost/v1", "http://localhost\\.gw.example/v1", "",
                    "https://gw.example /v1", "http://10.0.0.5/v1"] {
            assert!(safe_upstream(bad).is_none(), "{bad}");
        }
    }
    #[test]
    fn peer_matching_uses_the_client_tuple_and_uid_and_fails_closed() {
        let table = "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid\n 0: 0100007F:A001 0100007F:B002 01 00000000:00000000 00:00000000 00000000 10100 0 42\n";
        assert!(peer_in_table(table, "0100007F:A001", "0100007F:B002", 10100));
        assert!(!peer_in_table(table, "0100007F:A001", "0100007F:B002", 10003));
        assert!(!peer_in_table(table, "0100007F:B002", "0100007F:A001", 10100));
        assert!(!peer_in_table(&table.replace(" 01 ", " 06 "), "0100007F:A001", "0100007F:B002", 10100));
        assert!(!peer_in_table("", "0100007F:A001", "0100007F:B002", 10100));
    }

    #[cfg(target_os = "linux")] // the owner check reads /proc/net/tcp
    #[tokio::test]
    async fn the_real_socket_owner_is_checked_even_with_a_valid_token() {
        let (gateway, seen) = upstream(SSE.to_vec()).await;
        let uid = unsafe { libc::geteuid() };
        let mut denied = start_for_user(&gateway, KEY.into(), None, Some(uid + 1)).await.unwrap();
        let url = url::Url::parse(&denied.url).unwrap();
        let mut socket = TcpStream::connect(("127.0.0.1", url.port().unwrap())).await.unwrap();
        let request = format!("POST /gateway/chat/completions HTTP/1.1\r\nAuthorization: Bearer {}\r\nContent-Length: 2\r\n\r\n{{}}", denied.token);
        let _ = socket.write_all(request.as_bytes()).await;
        let mut out = Vec::new();
        let _ = tokio::time::timeout(Duration::from_secs(2), socket.read_to_end(&mut out)).await.unwrap();
        assert!(!String::from_utf8_lossy(&out).starts_with("HTTP/1.1 200"));
        assert!(seen.lock().unwrap().is_empty());
        denied.stop().await;
        let accepted = start_for_user(&gateway, KEY.into(), None, Some(uid)).await.unwrap();
        assert!(call(&accepted, &accepted.token, "/gateway/chat/completions", "{}").await.starts_with("HTTP/1.1 200"));
        assert_eq!(seen.lock().unwrap().len(), 1);
    }

    /// Run only in a disposable Linux container, with the production five capabilities and no real credentials.
    #[cfg(target_os = "linux")]
    #[tokio::test]
    #[ignore = "requires disposable Linux root and the five Docker runner capabilities"]
    async fn docker_turn_user_blocks_siblings_and_cleans_before_reuse() {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        use tokio::io::{AsyncBufReadExt, BufReader};
        assert_eq!(std::env::var("TICO_TEST_DISPOSABLE").as_deref(), Ok("1"));
        if unsafe { libc::geteuid() } == 0 {
            let output = std::process::Command::new(crate::isolation::SETPRIV)
                .args(["--reuid=10002", "--regid=10002", "--clear-groups",
                       "--inh-caps=+chown,+dac_override,+kill,+setgid,+setuid",
                       "--ambient-caps=+chown,+dac_override,+kill,+setgid,+setuid"])
                .arg(std::env::current_exe().unwrap())
                .args(["--ignored", "--exact", "gateway_relay::tests::docker_turn_user_blocks_siblings_and_cleans_before_reuse", "--nocapture", "--test-threads=1"])
                .env_clear().env("TICO_TEST_DISPOSABLE", "1").env("PATH", "/usr/local/bin:/usr/bin:/bin")
                // Inherited output: a failed probe's leftover turn processes must not hold a pipe open and hide it.
                .status().unwrap();
            assert!(output.success(), "the supervisor-side probe failed; its output is above");
            return;
        }
        assert_eq!(unsafe { libc::geteuid() }, 10002, "actual Docker supervisor identity");
        let status = std::fs::read_to_string("/proc/self/status").unwrap();
        let caps = status.lines().find_map(|line| line.strip_prefix("CapEff:")).unwrap().trim();
        assert_eq!(u64::from_str_radix(caps, 16).unwrap(), 0xe3, "exactly the production five capabilities");
        let image_build = std::process::Command::new("/usr/local/bin/tico-runner").arg("--build-id").env_clear().output().unwrap();
        assert!(image_build.status.success());
        assert_eq!(String::from_utf8_lossy(&image_build.stdout).trim(), crate::BUILD_ID, "probe and candidate must have the same source fingerprint");
        for uid in 10100..=10163 {
            let user = std::process::Command::new("/usr/bin/getent").args(["passwd", &uid.to_string()]).env_clear().output().unwrap();
            assert!(user.status.success(), "candidate image lacks reserved user {uid}");
        }
        let root = std::env::temp_dir().join(format!("tico-turn-fixture-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o755)).unwrap();
        std::env::set_var("HOME", &root);
        std::env::set_var(crate::isolation::UID_ENV, "10003");
        std::env::set_var(crate::isolation::GID_ENV, "10002");
        std::env::set_var(crate::turn_users::POOL_ENV, "10100-10101");
        std::fs::create_dir_all("/run/tico-runner").unwrap();
        std::fs::create_dir_all("/run/tico-runner/turns/10100").unwrap();
        std::os::unix::fs::lchown("/run/tico-runner/turns/10100", Some(10100), Some(10002)).unwrap();
        let workspace = root.join("workspace");
        std::fs::create_dir(&workspace).unwrap();
        let old = workspace.join("owner-only");
        std::fs::write(&old, "previous turn").unwrap();
        std::fs::set_permissions(&old, std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(&workspace, std::fs::Permissions::from_mode(0o700)).unwrap();
        for path in [&old, &workspace] { std::os::unix::fs::lchown(path, Some(10003), Some(10002)).unwrap(); }
        // A crashed runner's user and temp file must be swept before the first allocation.
        let stale = root.join("crash-file");
        std::fs::write(&stale, "stale").unwrap();
        std::os::unix::fs::lchown(&stale, Some(10100), Some(10002)).unwrap();
        let mut orphan = crate::isolation::command_as(Some((10100, 10002)), "/bin/sleep")
            .arg("300").env_clear().spawn().unwrap();
        // Only once it really runs as the slot user (the identity switch happens after spawn) is it the sweep's to kill.
        let orphan_pid = orphan.id().unwrap();
        for _ in 0..500 {
            if std::fs::metadata(format!("/proc/{orphan_pid}")).map(|m| m.uid()).unwrap_or(0) == 10100 { break; }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        eprintln!("probe: crash leftovers in place");
        let user = crate::turn_users::Lease::take().unwrap();
        let other = crate::turn_users::Lease::take().unwrap();
        assert_ne!(user.uid, other.uid);
        assert!(crate::turn_users::Lease::take().is_err(), "pool exhaustion must not reuse a live UID");
        assert!(!orphan.wait().await.unwrap().success());
        eprintln!("probe: pool swept the crashed slot");
        assert_eq!(stale.metadata().unwrap().uid(), 10003);
        // The bot user (or a sibling, through the shared group) plants programs git would run: a hook and fsmonitor.
        let (hook_mark, fsmonitor_mark) = (format!("/tmp/tico-hostile-hook-{}", uuid::Uuid::new_v4().simple()), format!("/tmp/tico-hostile-fsmonitor-{}", uuid::Uuid::new_v4().simple()));
        // ... and in the checkout's own config, which every bot can write: a filter, an ssh command and a proxy.
        let (filter_mark, ssh_mark) = (format!("/tmp/tico-hostile-filter-{}", uuid::Uuid::new_v4().simple()), format!("/tmp/tico-hostile-ssh-{}", uuid::Uuid::new_v4().simple()));
        let plant = r#"set -e; git init -q "$1"
printf '#!/bin/sh\ntouch "%s"\n' "$2" > "$1/.git/hooks/pre-commit"; printf '#!/bin/sh\ntouch "%s"\n' "$3" > "$1/.git/fsmonitor"
chmod 755 "$1/.git/hooks/pre-commit" "$1/.git/fsmonitor"; git -C "$1" config core.fsmonitor "$1/.git/fsmonitor"
echo '* filter=sibling' > "$1/.gitattributes"; git -C "$1" config filter.sibling.clean "touch $4"; git -C "$1" config filter.sibling.smudge "touch $4"
git -C "$1" config core.sshCommand "touch $5"; git -C "$1" config http.proxy http://127.0.0.1:9; git -C "$1" config http.https://github.com/.proxy http://127.0.0.1:9
git -C "$1" config credential.helper "!touch $4-credential"; git -C "$1" config credential.https://github.com.helper "!touch $4-credential""#;
        let planted = crate::isolation::command_as(Some((10003, 10002)), "/bin/sh")
            .args(["-c", plant, "plant", &workspace.display().to_string(), &hook_mark, &fsmonitor_mark, &filter_mark, &ssh_mark])
            .env_clear().env("PATH", "/usr/bin:/bin").output().await.unwrap();
        assert!(planted.status.success(), "{}", String::from_utf8_lossy(&planted.stderr));
        crate::turn_users::share_workspace(vec![workspace.clone()]).await.unwrap();
        let (gateway, seen) = upstream(SSE.to_vec()).await;
        let relay = start_for_user(&gateway, KEY.into(), None, Some(user.uid)).await.unwrap();
        let mut grant_vault = Vault::default();
        grant_vault.turn_user = Some(user);
        let ctx = crate::env::EnvContext { url: "http://127.0.0.1:9", projects_dir: &workspace, state_dir: &root };
        let mut grant_env = crate::env::environment_with(&ctx,
            &serde_json::json!({"bot": "fixture", "token": "SYNTHETIC-HUB-TOKEN"}), &mut grant_vault,
            Some(serde_json::json!({"credentials": [{"id": "fixture", "env": "SYNTHETIC_FILE", "kind": "file", "value": "SYNTHETIC-GRANTED"}]}))).await.unwrap();
        let private_dir = grant_vault.turn_user.as_ref().unwrap().dir.clone();
        let grant_path = std::path::PathBuf::from(&grant_env["SYNTHETIC_FILE"]);
        assert_eq!(grant_path.parent(), Some(private_dir.as_path()));
        assert_eq!(grant_path.metadata().unwrap().uid(), grant_vault.turn_user.as_ref().unwrap().uid);
        attach(relay, private_dir.join("pi"), &mut grant_env, &mut grant_vault);
        let canonical = grant_env.clone();
        // A subscription primary/fallback keeps the bot identity; the Gateway hop gets a private copy again.
        assert_eq!(grant_vault.for_model(&mut grant_env, Some("opus-5-5"), false).unwrap(), None);
        assert_eq!(std::fs::metadata(&grant_env["SYNTHETIC_FILE"]).unwrap().uid(), 10003);
        assert!(!grant_env.contains_key(KEY_ENV));
        grant_env = canonical;
        assert!(grant_vault.for_model(&mut grant_env, Some("tico-mid-1.0"), false).unwrap().is_some());
        let mut user = grant_vault.turn_user.take().unwrap();
        let mut relay = grant_vault.relay.take().unwrap();
        let private = user.dir.join("private");
        std::fs::write(&private, "SYNTHETIC-PRIVATE").unwrap();
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
        std::os::unix::fs::lchown(&private, Some(user.uid), Some(10002)).unwrap();
        let written = workspace.join("next-turn");
        let tmp = std::path::Path::new("/tmp").join(format!("tico-turn-owned-{}", uuid::Uuid::new_v4().simple()));
        let shm = std::path::Path::new("/dev/shm").join(format!("tico-turn-owned-{}", uuid::Uuid::new_v4().simple()));
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = crate::hosts::pi::PiHost::new("fixture", vec![], tx);
        let mut settings = crate::hosts::Settings { identity: Some(user.identity()), cwd: workspace.display().to_string(), model: Some("tico-mid-1.0".into()), ..Default::default() };
        settings.env = grant_env;
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        settings.env.insert("SYNTHETIC_ENV".into(), "SYNTHETIC-TURN-SECRET".into());
        settings.env.insert(KEY_ENV.into(), relay.token.clone());
        settings.env.insert(URL_ENV.into(), relay.url.clone());
        settings.env.insert(DIR_ENV.into(), user.dir.join("pi").display().to_string());
        // The turn's real git settings, as execute.rs turn_git gives them: exactly its checkout trusted (the image's
        // `safe.directory = *` unread), no repository-named programs.
        // A sibling bot can write the shared HOME: its proxy and CA bundle must not reach the turn's git and token.
        let sibling_config = std::path::Path::new("/home/runner/.gitconfig");
        let planted = crate::isolation::command_as(Some((10003, 10002)), "/bin/sh")
            .args(["-c", "umask 002; printf '[http]\\n\\tproxy = http://127.0.0.1:9\\n\\tsslCAInfo = /home/runner/sibling-ca.pem\\n' > \"$1\"", "fixture", &sibling_config.display().to_string()])
            .env_clear().output().await.unwrap();
        assert!(planted.status.success(), "{}", String::from_utf8_lossy(&planted.stderr));
        // The turn's HOME is its own (turn_users::Lease::private_home), and Python reads no user site at all.
        assert_eq!(settings.env["HOME"], user.dir.join("home").display().to_string());
        assert_eq!(std::fs::symlink_metadata(user.dir.join("home")).unwrap().uid(), user.uid);
        assert_eq!(settings.env["PYTHONNOUSERSITE"], "1");
        // A sibling plants Python code in the shared HOME's user site, which any Python as the turn user would run.
        let (site_mark, pth_mark) = (format!("/tmp/tico-hostile-site-{}", uuid::Uuid::new_v4().simple()), format!("/tmp/tico-hostile-pth-{}", uuid::Uuid::new_v4().simple()));
        let shared_python = |id: (u32, u32), code: &str| crate::isolation::command_as(Some(id), crate::env::python())
            .args(["-c", code]).env_clear().env("HOME", "/home/runner").output();
        let site = shared_python((10003, 10002), "import site; print(site.getusersitepackages())").await.unwrap();
        let site = std::path::PathBuf::from(String::from_utf8_lossy(&site.stdout).trim());
        assert!(site.starts_with("/home/runner"), "{}", site.display());
        let planted_site = crate::isolation::command_as(Some((10003, 10002)), "/bin/sh")
            .args(["-c", "umask 002; mkdir -p \"$1\" && printf \"open('%s','w')\\n\" \"$2\" > \"$1/usercustomize.py\" && printf \"import os; open('%s','w')\\n\" \"$3\" > \"$1/sibling.pth\"",
                   "fixture", &site.display().to_string(), &site_mark, &pth_mark])
            .env_clear().env("PATH", "/usr/bin:/bin").output().await.unwrap();
        assert!(planted_site.status.success(), "{}", String::from_utf8_lossy(&planted_site.stderr));
        // The turn's GitHub helper, as gitcred::apply sets it before turn_git, must still answer afterwards.
        crate::gitcred::git_environment(&mut settings.env, "SYNTHETIC-GH-TOKEN", "!printf 'username=x-access-token\\npassword=SYNTHETIC-GH-TOKEN\\n'");
        crate::safegit::for_turn_user(&mut settings.env, &[workspace.clone()], &user.dir.join("gitconfig"), Some(user.identity())).unwrap();
        let turn_config = std::fs::read_to_string(user.dir.join("gitconfig")).unwrap().to_lowercase();
        assert!(!turn_config.contains("proxy") && !turn_config.contains("sslcainfo"), "{turn_config}");
        let mut argv = vec![crate::env::python().display().to_string()];
        argv.extend(["-u", "-c", r#"import os,pathlib,subprocess,sys,time
assert os.getegid()==10002
assert os.environ['SYNTHETIC_ENV']=='SYNTHETIC-TURN-SECRET'
assert pathlib.Path(os.environ['SYNTHETIC_FILE']).read_text()=='SYNTHETIC-GRANTED'
assert 'CapEff:\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
assert pathlib.Path(os.environ['PI_CODING_AGENT_DIR'],'models.json').stat().st_uid==os.geteuid()
f=open(sys.argv[1]); assert f.read()=='SYNTHETIC-PRIVATE'
assert pathlib.Path(sys.argv[2]).read_text()=='previous turn'
for p in sys.argv[3:]:
 pathlib.Path(p).write_text('from isolated turn'); os.chmod(p,0o600)
subprocess.run(['git','init','-q',str(pathlib.Path(sys.argv[3]).parent)],check=True)
subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'status','--short'],check=True,stdout=subprocess.DEVNULL)
assert '127.0.0.1' not in subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'config','--global','--get-regexp','^http\\.'],capture_output=True,text=True).stdout, 'a sibling HOME http setting reached the turn'
assert subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'config','--get-urlmatch','http.proxy','https://github.com/o/r.git/info/refs'],capture_output=True,text=True).stdout.strip()=='', 'a sibling proxy reached the turn'
filled=subprocess.run(['git','-C',sys.argv[2].rsplit('/',1)[0],'credential','fill'],input='protocol=https\nhost=github.com\npath=o/r.git\n\n',capture_output=True,text=True)
assert 'password=SYNTHETIC-GH-TOKEN' in filled.stdout and 'planted' not in filled.stdout, 'the turn helper did not answer: '+filled.stdout+filled.stderr
subprocess.run(['git','-C',sys.argv[2].rsplit('/',1)[0],'credential','fill'],input='protocol=https\nhost=example.invalid\n\n',capture_output=True,text=True)
subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'ls-remote','ssh://example.invalid/x'],capture_output=True)
subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'-c','user.name=fixture','-c','user.email=fixture@example.invalid','add','.'],check=True)
subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','synthetic'],check=True)
remote=str(pathlib.Path(sys.argv[3]).parent/'.git'/'fixture-remote.git')
subprocess.run(['git','init','--bare','-q',remote],check=True)
subprocess.run(['git','-C',str(pathlib.Path(sys.argv[3]).parent),'push','-q',remote,'HEAD:refs/heads/main'],check=True)
print(os.getpid(),flush=True)
time.sleep(300)
"#, &private.display().to_string(), &old.display().to_string(), &written.display().to_string(), &tmp.display().to_string(), &shm.display().to_string()].map(String::from));
        // Exercise the actual OneShot/Pi process-launch boundary, including Settings.identity.
        let (mut child, stderr) = host.launch(&argv, &settings).unwrap();
        eprintln!("probe: launching the isolated turn");
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap()).read_line(&mut line).await.unwrap();
        if line.trim().is_empty() {
            let _ = child.wait().await;
            stderr.drained().await;
            panic!("the isolated turn failed: {}", stderr.tail());
        }
        let pid: u32 = line.trim().parse().unwrap();
        eprintln!("probe: turn finished its git work");
        assert!(!std::path::Path::new(&site_mark).exists() && !std::path::Path::new(&pth_mark).exists(), "a sibling's Python user site ran as the turn user");
        // Control: the same Python with the shared HOME runs both, so the check is not vacuous.
        assert!(shared_python(user.identity(), "pass").await.unwrap().status.success());
        assert!(std::path::Path::new(&site_mark).exists() && std::path::Path::new(&pth_mark).exists(), "control: the planted user site never runs");
        for mark in [&site_mark, &pth_mark] { let _ = std::fs::remove_file(mark); }
        for name in ["usercustomize.py", "sibling.pth"] { std::fs::remove_file(site.join(name)).unwrap(); }
        std::fs::remove_file(sibling_config).unwrap();
        assert!(!std::path::Path::new(&hook_mark).exists() && !std::path::Path::new(&fsmonitor_mark).exists(), "a planted program ran as the leased user");
        assert!(!std::path::Path::new(&filter_mark).exists() && !std::path::Path::new(&ssh_mark).exists(), "the checkout's config ran a program as the leased user");
        assert!(!std::path::Path::new(&format!("{filter_mark}-credential")).exists(), "the checkout's credential helper ran as the leased user");
        // Control: the same git run by the repository's owner with its own config does run them, so the check is not vacuous.
        let control = crate::isolation::command_as(Some((10003, 10002)), "/usr/bin/git")
            .args(["-C", &workspace.display().to_string(), "-c", "user.name=c", "-c", "user.email=c@example.invalid", "commit", "-q", "--allow-empty", "-m", "control"])
            .env_clear().env("PATH", "/usr/bin:/bin").output().await.unwrap();
        assert!(control.status.success(), "{}", String::from_utf8_lossy(&control.stderr));
        assert!(std::path::Path::new(&hook_mark).exists() && std::path::Path::new(&fsmonitor_mark).exists(), "control: the planted programs never run");
        for mark in [&hook_mark, &fsmonitor_mark] { let _ = std::fs::remove_file(mark); }
        assert_eq!(std::fs::metadata(format!("/proc/{pid}")).unwrap().uid(), user.uid);
        let probe = r#"import json,os,pathlib,signal,sys,urllib.request
pid,private,url,token=sys.argv[1:]
r={}
for name,path in [('environment','/proc/'+pid+'/environ'),('fd','/proc/'+pid+'/fd'),('open_fd','/proc/'+pid+'/fd/3'),('private',private)]:
 try:
  if name=='fd': os.listdir(path)
  else: pathlib.Path(path).read_bytes()
  r[name]=True
 except OSError: r[name]=False
try: os.kill(int(pid),signal.SIGTERM); r['signal']=True
except PermissionError: r['signal']=False
for name,bearer in [('relay_without','guessed'),('relay_with',token)]:
 try:
  req=urllib.request.Request(url+'/chat/completions',data=b'{}',headers={'Authorization':'Bearer '+bearer})
  with urllib.request.urlopen(req,timeout=3) as response: r[name]=response.status==200
 except Exception: r[name]=False
print(json.dumps(r))
"#;
        // An original shared-user fixture makes the same probe succeed. This prevents an environmental
        // /proc restriction or a broken HTTP probe from turning all of the denials into vacuous passes.
        let (control_gateway, control_seen) = upstream(SSE.to_vec()).await;
        let mut control_relay = start(&control_gateway, KEY.into(), None).await.unwrap();
        let mut control = crate::isolation::command(crate::env::python())
            .args(["-u", "-c", "import os,sys,time; f=open(sys.argv[1]); print(os.getpid(),flush=True); time.sleep(300)", &old.display().to_string()])
            .env_clear().env("SYNTHETIC_ENV", "SYNTHETIC-CONTROL").stdout(std::process::Stdio::piped()).spawn().unwrap();
        let mut control_pid = String::new();
        BufReader::new(control.stdout.take().unwrap()).read_line(&mut control_pid).await.unwrap();
        let control_result = crate::isolation::command(crate::env::python())
            .args(["-c", probe, control_pid.trim(), &old.display().to_string(), &control_relay.url, &control_relay.token])
            .env_clear().output().await.unwrap();
        assert!(control_result.status.success());
        let baseline: Value = serde_json::from_slice(&control_result.stdout).unwrap();
        for key in ["environment", "fd", "open_fd", "private", "signal", "relay_with"] {
            assert_eq!(baseline[key], true, "original shared-user control: {baseline}");
        }
        assert_eq!(control_seen.lock().unwrap().len(), 1);
        control_relay.stop().await;
        let _ = control.wait().await;
        for id in [(10003, 10002), other.identity()] {
            let result = crate::isolation::command_as(Some(id), crate::env::python())
                .args(["-c", probe, &pid.to_string(), &private.display().to_string(), &relay.url, &relay.token])
                .env_clear().output().await.unwrap();
            assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
            let r: Value = serde_json::from_slice(&result.stdout).unwrap();
            assert!(r.as_object().unwrap().values().all(|v| v == false), "sibling {id:?}: {r}");
        }
        // The supervisor hands the leased user its own credential socket; the bot user and a sibling turn cannot reach it.
        std::env::set_var("TICO_RUNNER_CRED_SOCKET", "/run/tico-runner/probe-credential.sock");
        let client = crate::client::Client::new("http://127.0.0.1:9", "synthetic", 5, 0).unwrap();
        let shared_socket = crate::gitcred::CredentialSocket::serve(client, std::sync::Arc::new(crate::gitcred::TokenCache::new())).unwrap();
        let mut socket_env = crate::env::Env::new();
        socket_env.insert(crate::gitcred::SOCKET_ENV.into(), shared_socket.path.display().to_string());
        let turn_socket = shared_socket.serve_for(&mut socket_env, &user.dir.join("git-credential.sock"), user.identity()).unwrap();
        assert_eq!(std::fs::symlink_metadata(&turn_socket.path).unwrap().uid(), user.uid);
        let ask = "import socket,sys\ns=socket.socket(socket.AF_UNIX)\ntry: s.connect(sys.argv[1])\nexcept PermissionError: print('denied'); sys.exit(0)\ns.sendall(b'probe\\n'); print(s.makefile().readline().strip())";
        for (id, expected) in [(user.identity(), "bad request"), ((10003, 10002), "denied"), (other.identity(), "denied")] {
            let asked = crate::isolation::command_as(Some(id), crate::env::python())
                .args(["-c", ask, &socket_env[crate::gitcred::SOCKET_ENV]]).env_clear().output().await.unwrap();
            assert!(asked.status.success(), "{id:?}: {}", String::from_utf8_lossy(&asked.stderr));
            assert!(String::from_utf8_lossy(&asked.stdout).contains(expected), "{id:?}: {}", String::from_utf8_lossy(&asked.stdout));
        }
        let socket_path = turn_socket.path.clone();
        user.hold_socket(turn_socket); // as execute does: the lease closes it before the UID returns
        let allowed = crate::isolation::command_as(Some(user.identity()), crate::env::python())
            .args(["-c", "import sys,urllib.request; r=urllib.request.Request(sys.argv[1]+'/chat/completions',data=b'{}',headers={'Authorization':'Bearer '+sys.argv[2]}); print(urllib.request.urlopen(r,timeout=3).status)", &relay.url, &relay.token])
            .env_clear().output().await.unwrap();
        assert!(allowed.status.success(), "{}", String::from_utf8_lossy(&allowed.stderr));
        assert_eq!(String::from_utf8_lossy(&allowed.stdout).trim(), "200");
        assert_eq!(seen.lock().unwrap().len(), 1);
        let dir = user.dir.clone();
        let uid = user.uid;
        eprintln!("probe: siblings denied; cleaning up");
        relay.stop().await;
        grant_vault.remove_files();
        user.permit_reuse();
        drop(user);
        assert!(!socket_path.exists(), "the turn's credential socket outlives its lease");
        assert!(!child.wait().await.unwrap().success(), "detached process survives cleanup");
        let survivors: Vec<_> = std::fs::read_dir("/proc").unwrap().flatten()
            .filter(|e| e.file_name().to_string_lossy().parse::<u32>().is_ok() && e.metadata().is_ok_and(|m| m.uid() == uid))
            .filter(|e| !std::fs::read_to_string(e.path().join("stat")).unwrap_or_default().rsplit(')').next().unwrap_or_default().trim_start().starts_with('Z'))
            .map(|e| e.file_name()).collect();
        assert!(survivors.is_empty(), "turn user {uid} still runs {survivors:?}");
        if let Ok(left) = std::fs::read_dir(&dir) {
            for entry in left.flatten() { eprintln!("probe: left in the private directory: {:?} {:?}", entry.file_name(), entry.metadata().map(|m| (m.uid(), m.mode()))); }
        }
        assert!(!dir.exists() && !tmp.exists() && !shm.exists());
        assert_eq!(written.metadata().unwrap().uid(), 10003);
        let next = crate::isolation::command("/bin/sh").args(["-c", "cat \"$1\"; echo next >> \"$1\"", "fixture", &written.display().to_string()])
            .env_clear().output().await.unwrap();
        assert!(next.status.success(), "next bot turn must edit an owner-only file");
        let mut reused = crate::turn_users::Lease::take().unwrap();
        assert_eq!(reused.uid, uid, "a turn that left a process behind must leave its user swept and reusable, not quarantined");
        // Even the next borrower of the same UID cannot use a revoked relay.
        assert!(TcpStream::connect(("127.0.0.1", url::Url::parse(&relay.url).unwrap().port().unwrap())).await.is_err());
        // The next borrower binds the same fixed socket path, and keeps it until its own lease ends.
        let again = shared_socket.serve_for(&mut crate::env::Env::new(), &socket_path, reused.identity()).unwrap();
        reused.hold_socket(again);
        let asked = crate::isolation::command_as(Some(reused.identity()), crate::env::python())
            .args(["-c", ask, &socket_path.display().to_string()]).env_clear().output().await.unwrap();
        assert!(String::from_utf8_lossy(&asked.stdout).contains("bad request"), "reused: {}{}", String::from_utf8_lossy(&asked.stdout), String::from_utf8_lossy(&asked.stderr));
        reused.permit_reuse(); drop(reused);
        assert!(!socket_path.exists());
        drop(shared_socket);
        // A second name for a turn-owned temp file must not survive readable, nor return its UID to the pool.
        let (first, second) = (format!("/tmp/tico-turn-link-{}", uuid::Uuid::new_v4().simple()), format!("/tmp/tico-turn-link-{}", uuid::Uuid::new_v4().simple()));
        let linked = crate::isolation::command_as(Some(other.identity()), "/bin/sh")
            .args(["-c", "umask 002; echo SYNTHETIC-LINKED > \"$1\"; ln \"$1\" \"$2\"", "fixture", &first, &second])
            .env_clear().output().await.unwrap();
        assert!(linked.status.success(), "{}", String::from_utf8_lossy(&linked.stderr));
        let quarantined = other.uid;
        let mut other = other; other.permit_reuse(); drop(other);
        for name in [&first, &second] {
            if let Ok(info) = std::fs::symlink_metadata(name) {
                assert!(info.uid() != quarantined && info.mode() & 0o777 == 0, "{name} survives readable: {:o}", info.mode());
                std::fs::remove_file(name).unwrap();
            }
        }
        let mut next = crate::turn_users::Lease::take().unwrap();
        assert_ne!(next.uid, quarantined, "a UID with leftover names must stay quarantined");
        assert!(crate::turn_users::Lease::take().is_err());
        next.permit_reuse(); drop(next);
        std::fs::remove_dir_all(root).unwrap();
    }

}
