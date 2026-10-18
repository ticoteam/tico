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
//! the turn's user can read the runner's registration anyway, so this boundary is only as strong as isolation.

use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::client::Client;
use crate::env::{Env, Vault};
use crate::util::text_of;

pub use crate::hosts::pi::{GATEWAY_KEY_ENV as KEY_ENV, GATEWAY_URL_ENV as URL_ENV};

/// The relay's path; what follows it goes after the Gateway's base URL (`/chat/completions`).
const PREFIX: &str = "/gateway";
const MAX_HEAD: usize = 64 * 1024;
const MAX_BODY: usize = 32 * 1024 * 1024;
/// Request headers never passed on: the turn's token, and what belongs to this connection only.
const DROP_REQUEST: &[&str] = &[
    "authorization", "host", "connection", "content-length", "transfer-encoding", "proxy-authorization",
    "proxy-connection", "keep-alive", "te", "upgrade", "expect", "trailer",
];
/// Response headers never passed back: the relay's answer ends when it closes the connection.
const DROP_RESPONSE: &[&str] =
    &["connection", "keep-alive", "transfer-encoding", "content-length", "proxy-authenticate", "trailer", "upgrade", "te"];

/// A running relay for one turn. Dropping it (with the turn's vault) stops it and every call still open on it.
pub struct Relay {
    pub url: String,
    pub token: String,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.task.abort();
    }
}

struct Shared {
    client: reqwest::Client,
    base: String,
    key: String,
    token: String,
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

/// The Gateway's key and URL for attempt `aid`, from the server with the runner's token, or None when the run gets none.
pub async fn credentials(client: &Client, aid: &str) -> Result<Option<(String, String)>, String> {
    let got = client
        .get_query("runner-gateway-credentials", &[("attempt", aid.to_string())])
        .await
        .map_err(|e| format!("runner-gateway-credentials: {}", e.detail))?;
    let items = got["credentials"].as_array().cloned().unwrap_or_default();
    let of = |env: &str| items.iter().find(|i| i["env"].as_str() == Some(env)).map(|i| text_of(&i["value"]));
    Ok(match (of(KEY_ENV), of(URL_ENV)) {
        (Some(key), Some(url)) if !key.is_empty() && !url.is_empty() => Some((key, url)),
        _ => None,
    })
}

/// Whether a claimed attempt may call the Gateway (the server's `gateway` flag on the claim).
pub fn wanted(attempt: &Value) -> bool {
    crate::util::truthy(attempt.get("gateway"))
}

/// Give a turn on a Tico model its relay: the relay's URL and the turn's own token in `env`, in place of anything else
/// by those names; the key itself only in `vault` (to scrub from what the turn says, and to stop the relay with it).
pub async fn hand_to_turn(client: &Client, attempt: &Value, env: &mut Env, vault: &mut Vault) -> Result<(), String> {
    if !wanted(attempt) {
        return Ok(());
    }
    env.remove(KEY_ENV);
    env.remove(URL_ENV);
    if let Some((key, url)) = credentials(client, &text_of(&attempt["id"])).await? {
        vault.values.push(key.clone());
        let relay = start(&url, key).await?;
        env.insert(URL_ENV.into(), relay.url.clone());
        env.insert(KEY_ENV.into(), relay.token.clone());
        vault.relay = Some(relay);
    }
    Ok(())
}

/// Start a relay to the Gateway at `upstream` with `key`.
pub async fn start(upstream: &str, key: String) -> Result<Relay, String> {
    let base = safe_upstream(upstream).ok_or("the Gateway URL is neither https nor this computer")?;
    let local = base.starts_with("http://");
    let mut builder = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(30));
    if local {
        builder = builder.no_proxy();
    }
    let client = builder.build().map_err(|e| e.to_string())?;
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let token = format!("tico-relay-{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
    let shared = Arc::new(Shared { client, base, key, token: token.clone() });
    let task = tokio::spawn(async move {
        let mut calls = tokio::task::JoinSet::new();
        loop {
            tokio::select! {
                accepted = listener.accept() => match accepted {
                    Ok((socket, _)) => {
                        let shared = shared.clone();
                        calls.spawn(async move { let _ = serve(socket, &shared).await; });
                    }
                    Err(_) => tokio::time::sleep(Duration::from_millis(50)).await,
                },
                Some(_) = calls.join_next(), if !calls.is_empty() => {}
            }
        }
    });
    Ok(Relay { url: format!("http://127.0.0.1:{port}{PREFIX}"), token, task })
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
    let plain = |r: &&str| {
        let lower = r.to_ascii_lowercase();
        r.starts_with('/') && !lower.contains("..") && !lower.contains("%2e") && !r.contains(['#', '\\'])
    };
    let Some(rest) = target.strip_prefix(PREFIX).filter(plain) else {
        return answer(&mut socket, 404, "Not a Gateway path").await;
    };
    let method = match method.as_str() {
        "GET" => reqwest::Method::GET,
        "POST" => reqwest::Method::POST,
        _ => return answer(&mut socket, 405, "Only GET and POST").await,
    };
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
    let mut request = s.client.request(method, format!("{}{rest}", s.base)).bearer_auth(&s.key).body(body);
    for (k, v) in &headers {
        if !DROP_REQUEST.contains(&k.as_str()) {
            request = request.header(k.as_str(), v.as_str());
        }
    }
    let mut response = match request.send().await {
        Ok(r) => r,
        Err(_) => return answer(&mut socket, 502, "The Tico AI Gateway did not answer").await,
    };
    let status = response.status();
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

    /// A fake Gateway on 127.0.0.1: keeps each request's head and body, answers with `reply` (raw bytes).
    async fn upstream(reply: Vec<u8>) -> (String, Arc<Mutex<Vec<String>>>) {
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
                kept.lock().unwrap().push(String::from_utf8_lossy(&buf[..got]).to_string());
                let _ = socket.write_all(&reply).await;
                let _ = socket.shutdown().await;
            }
        });
        (format!("http://127.0.0.1:{port}/v1/gateway"), seen)
    }

    async fn call(relay: &Relay, token: &str, path: &str, body: &str) -> String {
        let mut socket = TcpStream::connect(relay.url.trim_start_matches("http://").split('/').next().unwrap()).await.unwrap();
        let request = format!(
            "POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nX-Kept: yes\r\nContent-Length: {}\r\n\r\n{body}",
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
    async fn a_turn_reaches_the_gateway_with_the_key_it_never_holds() {
        let (url, seen) = upstream(SSE.to_vec()).await;
        let relay = start(&url, KEY.into()).await.unwrap();
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
    }

    #[tokio::test]
    async fn another_token_or_path_gets_nothing_sent() {
        let (url, seen) = upstream(SSE.to_vec()).await;
        let relay = start(&url, KEY.into()).await.unwrap();
        for (token, path, status) in [
            ("", "/gateway/chat/completions", "401"),
            ("tico-relay-wrong", "/gateway/chat/completions", "401"),
            (KEY, "/gateway/chat/completions", "401"),
            (relay.token.as_str(), "/other/chat/completions", "404"),
            (relay.token.as_str(), "/gateway/../key", "404"),
            (relay.token.as_str(), "/gateway/%2E%2e/key", "404"),
            (relay.token.as_str(), "/gatewayx/chat/completions", "404"),
        ] {
            let out = call(&relay, token, path, "{}").await;
            assert!(out.starts_with(&format!("HTTP/1.1 {status}")), "{token} {path}: {out}");
            assert!(!out.contains(KEY));
        }
        assert!(seen.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_redirect_is_neither_followed_nor_handed_on() {
        let (elsewhere, other) = upstream(SSE.to_vec()).await;
        let reply = format!("HTTP/1.1 307 Temporary Redirect\r\nLocation: {elsewhere}/chat/completions\r\nContent-Length: 0\r\n\r\n");
        let (url, seen) = upstream(reply.into_bytes()).await;
        let relay = start(&url, KEY.into()).await.unwrap();
        let out = call(&relay, &relay.token, "/gateway/chat/completions", "{}").await;
        assert!(out.starts_with("HTTP/1.1 502") && !out.to_ascii_lowercase().contains("location:"), "{out}");
        assert_eq!(seen.lock().unwrap().len(), 1);
        assert!(other.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_stopped_relay_takes_no_more_calls() {
        let (url, _) = upstream(SSE.to_vec()).await;
        let relay = start(&url, KEY.into()).await.unwrap();
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
            {"id": "gateway-url", "env": URL_ENV, "kind": "text", "value": gateway}]}).to_string();
        let (hub, asked) = upstream(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{answer}", answer.len()).into_bytes()).await;
        let hub = hub.trim_end_matches("/v1/gateway").to_string();
        let client = Client::new(&hub, "runner-token", 5, 0).unwrap();
        let (mut env, mut vault) = (Env::new(), Vault::default());
        env.insert(KEY_ENV.into(), KEY.into());                    // from anywhere else: never kept
        env.insert("PATH".into(), "/usr/bin".into());
        hand_to_turn(&client, &serde_json::json!({"id": "att-1", "gateway": false}), &mut env, &mut vault).await.unwrap();
        assert!(asked.lock().unwrap().is_empty() && vault.relay.is_none());
        hand_to_turn(&client, &serde_json::json!({"id": "att-1", "gateway": true}), &mut env, &mut vault).await.unwrap();
        let request = asked.lock().unwrap()[0].clone();
        assert!(request.starts_with("GET /api/v2/runner-gateway-credentials?attempt=att-1 "), "{request}");
        assert!(request.contains("Bearer runner-token"));
        assert!(env.values().all(|v| !v.contains(KEY)), "{env:?}");
        let relay = vault.relay.as_ref().unwrap();
        assert_eq!((env[URL_ENV].as_str(), env[KEY_ENV].as_str()), (relay.url.as_str(), relay.token.as_str()));
        assert_eq!(vault.values, vec![KEY.to_string()]);
        let out = call(relay, &env[KEY_ENV], "/gateway/chat/completions", "{}").await;
        assert!(out.starts_with("HTTP/1.1 200") && seen.lock().unwrap().len() == 1, "{out}");
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
}
