//! The loopback listener behind every `TICO_BROWSER_URL`. The first path segment is a live run's random secret and
//! picks its bot's browser; anything else is a plain 404. A request with an `Origin` (any web page, including ones open in the
//! bot's own Chrome) or a Host other than loopback (DNS rebinding) is refused, so only local programs that were
//! handed the URL reach a browser.

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::protocol::{Role, WebSocketConfig};
use tokio_tungstenite::tungstenite::Message;

use super::Bot;

const HEAD_LIMIT: usize = 16 * 1024;
const BODY_LIMIT: usize = 64 * 1024;
/// How long `json/version` waits for a browser that is starting (a first start includes the install).
const START_WAIT: Duration = Duration::from_secs(60);

/// Equal bytes, compared in time that depends only on the lengths.
pub fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

pub fn random_secret() -> String {
    use ring::rand::SecureRandom;
    let mut bytes = [0u8; 32];
    ring::rand::SystemRandom::new().fill(&mut bytes).expect("the system random source");
    hex::encode(bytes)
}

static PORT: tokio::sync::OnceCell<u16> = tokio::sync::OnceCell::const_new();

/// The endpoint's port, listening from the first call on.
pub async fn start() -> Result<u16, String> {
    PORT.get_or_try_init(|| async {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| format!("cannot open the browser endpoint ({e})"))?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    tokio::time::sleep(Duration::from_millis(100)).await;
                    continue;
                };
                tokio::spawn(async move {
                    let _ = serve(stream, port).await;
                });
            }
        });
        Ok(port)
    })
    .await
    .copied()
}

pub struct Request {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
}

impl Request {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
    }
}

/// What the request may do before any bot is looked up: Err is the status to answer with.
pub fn admit(req: &Request, port: u16) -> Result<(), u16> {
    if req.header("origin").is_some() {
        return Err(403);
    }
    let host = req.header("host").unwrap_or_default().to_ascii_lowercase();
    let (name, at) = host.rsplit_once(':').map(|(n, p)| (n, Some(p))).unwrap_or((host.as_str(), None));
    let local = matches!(name, "127.0.0.1" | "localhost");
    if !local || at.is_some_and(|p| p != port.to_string()) {
        return Err(403);
    }
    Ok(())
}

/// The bot's secret and the rest of the path, query left off.
pub fn split(path: &str) -> Option<(&str, &str)> {
    let path = path.split('?').next().unwrap_or_default();
    let rest = path.strip_prefix('/')?;
    let (secret, tail) = rest.split_at(rest.find('/').unwrap_or(rest.len()));
    (!secret.is_empty()).then_some((secret, tail))
}

async fn read_head(io: &mut BufReader<TcpStream>) -> Option<Request> {
    let mut lines = vec![];
    let mut size = 0;
    loop {
        let mut line = String::new();
        let n = io.read_line(&mut line).await.ok()?;
        size += n;
        if n == 0 || size > HEAD_LIMIT {
            return None;
        }
        let line = line.trim_end_matches(['\r', '\n']).to_string();
        if line.is_empty() {
            break;
        }
        lines.push(line);
    }
    let first = lines.first()?;
    let mut parts = first.split(' ');
    let (method, path) = (parts.next()?.to_string(), parts.next()?.to_string());
    let headers = lines[1..].iter().filter_map(|l| l.split_once(':')).map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string())).collect();
    Some(Request { method, path, headers })
}

async fn reply(io: &mut BufReader<TcpStream>, status: u16, body: &Value) -> std::io::Result<()> {
    let text = body.to_string();
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        413 => "Payload Too Large",
        _ => "Service Unavailable",
    };
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=UTF-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        text.len()
    );
    io.get_mut().write_all(head.as_bytes()).await?;
    io.get_mut().write_all(text.as_bytes()).await?;
    io.get_mut().flush().await
}

async fn serve(stream: TcpStream, port: u16) -> std::io::Result<()> {
    let mut io = BufReader::new(stream);
    let Ok(Some(req)) = tokio::time::timeout(Duration::from_secs(10), read_head(&mut io)).await else { return Ok(()) };
    if let Err(status) = admit(&req, port) {
        return reply(&mut io, status, &json!({"error": "forbidden"})).await;
    }
    let found = split(&req.path).and_then(|(secret, tail)| super::by_secret(secret).map(|(b, aid)| (b, aid, tail.to_string())));
    let Some((bot, aid, tail)) = found else { return reply(&mut io, 404, &json!({"error": "not_found"})).await };
    let tail = tail.trim_end_matches('/');
    match (req.method.as_str(), tail) {
        ("GET", "/json/version") => {
            let Some(browser) = bot.ready(START_WAIT).await else {
                return reply(&mut io, 503, &json!({"error": "browser_unavailable", "problem": bot.status()["problem"]})).await;
            };
            let version = browser.call("Browser.getVersion", json!({}), None).await.unwrap_or_default();
            let name = if req.header("host").is_some_and(|h| h.to_ascii_lowercase().starts_with("localhost")) { "localhost" } else { "127.0.0.1" };
            let host = format!("{name}:{port}");
            let secret = split(&req.path).map(|(s, _)| s.to_string()).unwrap_or_default();
            reply(&mut io, 200, &json!({
                "Browser": version["product"],
                "Protocol-Version": version["protocolVersion"],
                "User-Agent": version["userAgent"],
                "V8-Version": version["jsVersion"],
                "WebKit-Version": version["revision"],
                "webSocketDebuggerUrl": format!("ws://{host}/{secret}/devtools/browser"),
            }))
            .await
        }
        ("GET", t) if t == "/devtools/browser" || t.starts_with("/devtools/browser/") => websocket(io, &req, bot).await,
        ("GET", "/status") => reply(&mut io, 200, &bot.status()).await,
        ("POST", "/fill") => {
            let chunked = req.header("transfer-encoding").is_some_and(|t| t.to_ascii_lowercase().contains("chunked"));
            let read = async {
                if chunked {
                    read_chunked(&mut io, BODY_LIMIT).await
                } else {
                    let length: usize = req.header("content-length").and_then(|l| l.parse().ok()).unwrap_or(0);
                    if length > BODY_LIMIT {
                        return Ok(None);
                    }
                    let mut body = vec![0u8; length];
                    io.read_exact(&mut body).await.map(|_| Some(body))
                }
            };
            let body = match tokio::time::timeout(Duration::from_secs(10), read).await {
                Ok(Ok(Some(body))) => body,
                Ok(Ok(None)) => return reply(&mut io, 413, &json!({"ok": false, "error": "bad_request"})).await,
                _ => return Ok(()),
            };
            let parsed: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
            let answer = super::fill::fill(&bot, &aid, &parsed).await;
            reply(&mut io, 200, &answer).await
        }
        ("GET" | "POST", _) => reply(&mut io, 404, &json!({"error": "not_found"})).await,
        _ => reply(&mut io, 405, &json!({"error": "method_not_allowed"})).await,
    }
}

/// A chunked request body (Node's http and fetch send one), or None once it passes `limit`.
async fn read_chunked<R: tokio::io::AsyncBufRead + Unpin>(io: &mut R, limit: usize) -> std::io::Result<Option<Vec<u8>>> {
    let bad = || std::io::Error::new(std::io::ErrorKind::InvalidData, "bad chunk");
    let mut body = Vec::new();
    loop {
        let mut line = String::new();
        if io.read_line(&mut line).await? == 0 || line.len() > 64 {
            return Err(bad());
        }
        let size = usize::from_str_radix(line.trim().split(';').next().unwrap_or("").trim(), 16).map_err(|_| bad())?;
        if size == 0 {
            // Trailers, up to the blank line.
            loop {
                line.clear();
                if io.read_line(&mut line).await? == 0 || line.len() > 1024 {
                    return Err(bad());
                }
                if line.trim().is_empty() {
                    return Ok(Some(body));
                }
            }
        }
        if body.len() + size > limit {
            return Ok(None);
        }
        let start = body.len();
        body.resize(start + size, 0);
        io.read_exact(&mut body[start..]).await?;
        let mut crlf = [0u8; 2];
        io.read_exact(&mut crlf).await?;
        if &crlf != b"\r\n" {
            return Err(bad());
        }
    }
}

/// The CDP websocket: one client of the bot's shared browser.
async fn websocket(mut io: BufReader<TcpStream>, req: &Request, bot: Arc<Bot>) -> std::io::Result<()> {
    let upgrade = req.header("upgrade").is_some_and(|u| u.eq_ignore_ascii_case("websocket"));
    let Some(key) = req.header("sec-websocket-key").filter(|_| upgrade) else {
        return reply(&mut io, 400, &json!({"error": "websocket_expected"})).await;
    };
    let Some(browser) = bot.ready(START_WAIT).await else {
        return reply(&mut io, 503, &json!({"error": "browser_unavailable"})).await;
    };
    let Ok((client, mut from_browser)) = browser.connect().await else {
        return reply(&mut io, 503, &json!({"error": "browser_unavailable"})).await;
    };
    let accept = tokio_tungstenite::tungstenite::handshake::derive_accept_key(key.as_bytes());
    let head = format!("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n");
    if io.get_mut().write_all(head.as_bytes()).await.is_err() {
        browser.disconnect(client);
        return Ok(());
    }
    let config = WebSocketConfig::default().max_message_size(Some(256 << 20)).max_frame_size(Some(256 << 20));
    let ws = tokio_tungstenite::WebSocketStream::from_raw_socket(io, Role::Server, Some(config)).await;
    let (mut sink, mut stream) = ws.split();
    loop {
        tokio::select! {
            incoming = stream.next() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    if let Err(Some(answer)) = browser.send(client, text.as_str()) {
                        if sink.send(Message::text(answer)).await.is_err() { break; }
                    }
                }
                Some(Ok(Message::Binary(data))) => {
                    if let Err(Some(answer)) = browser.send(client, &String::from_utf8_lossy(&data)) {
                        if sink.send(Message::text(answer)).await.is_err() { break; }
                    }
                }
                Some(Ok(Message::Ping(_) | Message::Pong(_) | Message::Frame(_))) => { let _ = sink.flush().await; }
                Some(Ok(Message::Close(_))) | Some(Err(_)) | None => break,
            },
            outgoing = from_browser.recv() => match outgoing {
                Some(text) => if sink.send(Message::text(text)).await.is_err() { break; },
                None => break,
            },
        }
    }
    browser.disconnect(client);
    let _ = sink.close().await;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_chunked_body_is_read_whole_and_held_to_the_limit() {
        let mut io = BufReader::new(&b"5;x=1\r\n{\"a\":\r\n3\r\n12}\r\n0\r\nX-T: 1\r\n\r\n"[..]);
        assert_eq!(read_chunked(&mut io, 64).await.unwrap().unwrap(), b"{\"a\":12}");
        let mut big = BufReader::new(&b"ff\r\n"[..]);
        assert!(read_chunked(&mut big, 16).await.unwrap().is_none());
        let mut torn = BufReader::new(&b"zz\r\n"[..]);
        assert!(read_chunked(&mut torn, 16).await.is_err());
    }

    fn req(headers: &[(&str, &str)]) -> Request {
        Request { method: "GET".into(), path: "/".into(), headers: headers.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect() }
    }

    #[test]
    fn only_local_programs_without_an_origin_are_admitted() {
        assert!(admit(&req(&[("host", "127.0.0.1:4100")]), 4100).is_ok());
        assert!(admit(&req(&[("host", "localhost:4100")]), 4100).is_ok());
        // A web page (any origin, even the bot's own Chrome) and a rebinding host name are refused.
        assert_eq!(admit(&req(&[("host", "127.0.0.1:4100"), ("origin", "https://acme.example")]), 4100), Err(403));
        assert_eq!(admit(&req(&[("host", "evil.acme.example:4100")]), 4100), Err(403));
        assert_eq!(admit(&req(&[("host", "127.0.0.1:4200")]), 4100), Err(403));
        assert_eq!(admit(&req(&[]), 4100), Err(403));
    }

    #[test]
    fn the_secret_is_the_first_segment_and_compared_whole() {
        assert_eq!(split("/abc/json/version/?x=1"), Some(("abc", "/json/version/")));
        assert_eq!(split("/abc"), Some(("abc", "")));
        assert_eq!(split("//json/version"), None);
        assert!(same(b"abcd", b"abcd") && !same(b"abcd", b"abce") && !same(b"abc", b"abcd"));
        let (a, b) = (random_secret(), random_secret());
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
    }
}
