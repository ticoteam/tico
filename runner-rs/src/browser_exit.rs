//! Browser exits (docs/computers.md, "Share internet with bots"): a bot's browser reaches the web from another Tico
//! computer, one whose owner shares its internet with that bot, instead of from this computer's (often a datacenter's)
//! address. Both runners dial out to the hub's relay (relay-rs/src/exit.rs); neither listens on the network. The
//! bytes are the browser's own TLS, end to end: neither runner nor the relay reads them.
//!
//! The bot's side: a bot whose turn names a browser exit gets a proxy on 127.0.0.1 (`TICO_BROWSER_PROXY`), and Chrome
//! or Chromium started by name goes through it (scripts/browser-exit/). Each CONNECT (and each plain-http request,
//! sent on as one tunnel to port 80) asks the hub for a tunnel, then joins it at the relay. When the hub or the exit
//! refuses (offline, not shared, over its daily cap, a refused address) the browser gets a 502 that names the exit,
//! and the connection is never made from here instead: nothing in this file dials a website.
//!
//! The exit's side: while its owner shares (the heartbeat says so) the runner holds one link to the relay and, for
//! each tunnel it is sent, resolves the hostname here and dials it, ports 80 and 443 only, refusing every address
//! that is not on the public internet or is one of this computer's own (`refused`). The address checked is the one
//! dialled, so a name that resolves differently a second time changes nothing.

use std::collections::HashMap;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{Notify, Semaphore};
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message};

use crate::client::{bearer, Client, CLIENT_VERSION};
use crate::util::{log, text_of};

type Socket = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<TcpStream>>;

/// The only ports an exit dials.
pub const PORTS: [u16; 2] = [80, 443];
const DIAL: Duration = Duration::from_secs(10);
/// How long the bot's side waits for the exit to say it reached the site.
const ANSWER: Duration = Duration::from_secs(25);
const HEAD_BYTES: usize = 16 * 1024;
const CHUNK: usize = 64 * 1024;
const EXIT_TUNNELS: usize = 256;
const LINK_BACKOFF_MAX: Duration = Duration::from_secs(60);
/// The bot's proxy ports are picked from here first, by bot, so a browser left running finds its proxy again after
/// the runner restarts. Below the usual ephemeral ranges (Linux 32768+, macOS 49152+).
const PORT_BASE: u16 = 21000;
const PORT_SPAN: u16 = 8000;

// ------------------------------------------------------------------------------------------ addresses

/// Why an exit refuses to dial `ip`, or None for an address on the public internet. `own` is this computer's
/// interface addresses: a service listening on its public address is still the computer's own.
pub fn refused(ip: IpAddr, own: &[IpAddr]) -> Option<&'static str> {
    let ip = canonical(ip);
    if own.iter().any(|o| canonical(*o) == ip) {
        return Some("this computer's own address");
    }
    match ip {
        IpAddr::V4(v4) => refused_v4(v4),
        IpAddr::V6(v6) => refused_v6(v6),
    }
}

fn canonical(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        IpAddr::V4(_) => ip,
    }
}

fn refused_v4(ip: Ipv4Addr) -> Option<&'static str> {
    let [a, b, ..] = ip.octets();
    if ip.is_unspecified() || a == 0 {
        Some("an unspecified address")
    } else if ip.is_loopback() {
        Some("a loopback address")
    } else if ip.is_private() {
        Some("a private network address")
    } else if ip.is_link_local() {
        Some("a link-local address")
    } else if a == 100 && (b & 0xc0) == 64 {
        Some("a carrier-grade NAT address")
    } else if ip.is_multicast() {
        Some("a multicast address")
    } else if a >= 240 {
        Some("a reserved address")
    } else {
        None
    }
}

fn refused_v6(ip: Ipv6Addr) -> Option<&'static str> {
    let s = ip.segments();
    let low = Ipv4Addr::new((s[6] >> 8) as u8, s[6] as u8, (s[7] >> 8) as u8, s[7] as u8);
    if ip.is_unspecified() {
        Some("an unspecified address")
    } else if ip.is_loopback() {
        Some("a loopback address")
    } else if s[0] & 0xff00 == 0xff00 {
        Some("a multicast address")
    } else if s[0] & 0xfe00 == 0xfc00 {
        Some("a unique local address")
    } else if s[0] & 0xffc0 == 0xfe80 {
        Some("a link-local address")
    } else if s[0] & 0xffc0 == 0xfec0 {
        Some("a site-local address")
    } else if s[..6] == [0; 6] {
        Some("a reserved address") // IPv4-compatible (::a.b.c.d), long deprecated
    } else if s[0] == 0x64 && s[1] == 0xff9b && s[2..6] == [0; 4] {
        refused_v4(low) // NAT64 reaches the IPv4 address it carries
    } else if s[0] == 0x2002 {
        refused_v4(Ipv4Addr::new((s[1] >> 8) as u8, s[1] as u8, (s[2] >> 8) as u8, s[2] as u8))
    // 6to4
    } else {
        None
    }
}

/// This computer's interface addresses.
pub fn own_addresses() -> Vec<IpAddr> {
    let mut out = Vec::new();
    // SAFETY: getifaddrs hands back a list this function walks read-only and frees once.
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 {
            return out;
        }
        let mut cur = head;
        while !cur.is_null() {
            let addr = (*cur).ifa_addr;
            if !addr.is_null() {
                match (*addr).sa_family as i32 {
                    libc::AF_INET => {
                        let sin = addr as *const libc::sockaddr_in;
                        out.push(IpAddr::V4(Ipv4Addr::from(u32::from_be((*sin).sin_addr.s_addr))));
                    }
                    libc::AF_INET6 => {
                        let sin6 = addr as *const libc::sockaddr_in6;
                        out.push(IpAddr::V6(Ipv6Addr::from((*sin6).sin6_addr.s6_addr)));
                    }
                    _ => {}
                }
            }
            cur = (*cur).ifa_next;
        }
        libc::freeifaddrs(head);
    }
    out
}

/// A hostname or IP literal (an IPv6 one without brackets): never a URL, a port or a header.
pub fn valid_host(host: &str) -> bool {
    !host.is_empty()
        && host.len() <= 253
        && (host.parse::<IpAddr>().is_ok()
            || host.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_')))
}

/// Resolve `host` on this computer and dial it, only on 80 or 443 and only at a public address that is not this
/// computer's own. The socket returned is to the very address that was checked.
pub async fn dial(host: &str, port: u16) -> Result<TcpStream, String> {
    if !PORTS.contains(&port) {
        return Err("only ports 80 and 443 go through a browser exit".into());
    }
    if !valid_host(host) {
        return Err("not a hostname".into());
    }
    let resolved: Vec<SocketAddr> = tokio::time::timeout(DIAL, tokio::net::lookup_host((host, port)))
        .await
        .map_err(|_| format!("{host} did not resolve in time"))?
        .map_err(|_| format!("{host} does not resolve"))?
        .collect();
    let own = own_addresses();
    let mut why = None;
    let allowed: Vec<SocketAddr> = resolved
        .into_iter()
        .filter(|addr| match refused(addr.ip(), &own) {
            Some(reason) => {
                why.get_or_insert(reason);
                false
            }
            None => true,
        })
        .collect();
    if allowed.is_empty() {
        return Err(match why {
            Some(reason) => format!("{host} is {reason}"),
            None => format!("{host} does not resolve"),
        });
    }
    for addr in allowed.into_iter().take(4) {
        if let Ok(Ok(stream)) = tokio::time::timeout(DIAL, TcpStream::connect(addr)).await {
            return Ok(stream);
        }
    }
    Err(format!("{host} did not answer"))
}

// ------------------------------------------------------------------------------------------ hub sockets

fn ws_origin(hub: &str) -> Result<String, String> {
    let rest = if let Some(r) = hub.strip_prefix("https://") {
        format!("wss://{r}")
    } else if let Some(r) = hub.strip_prefix("http://") {
        format!("ws://{r}")
    } else {
        return Err(format!("not an http(s) hub URL: {hub}"));
    };
    Ok(rest.trim_end_matches('/').to_string())
}

/// A websocket to the hub at `path`; `authorized` sends the runner's token (the exit's link), a tunnel end carries
/// its one-use ticket instead.
async fn connect(client: &Client, path: &str, authorized: bool) -> Result<Socket, String> {
    let mut request = format!("{}{path}", ws_origin(&client.url)?).into_client_request().map_err(|e| e.to_string())?;
    let headers = request.headers_mut();
    if authorized {
        let token = bearer(&client.token).map_err(|e| e.detail)?;
        headers.insert("Authorization", format!("Bearer {token}").parse().map_err(|_| "bad token".to_string())?);
    }
    headers.insert("User-Agent", format!("Tico-Client/{CLIENT_VERSION}").parse().unwrap());
    let connector = Some(crate::tls::websocket_connector());
    let (ws, _) = tokio::time::timeout(
        Duration::from_secs(15),
        tokio_tungstenite::connect_async_tls_with_config(request, None, false, connector),
    )
    .await
    .map_err(|_| "the hub did not answer".to_string())?
    .map_err(|e| match e {
        tokio_tungstenite::tungstenite::Error::Http(response) => {
            format!("the hub refused ({})", response.status())
        }
        other => format!("websocket: {other}"),
    })?;
    Ok(ws)
}

/// Bytes both ways between a TCP connection and a tunnel until either closes.
async fn pump(tcp: TcpStream, ws: Socket) {
    let (mut tcp_in, mut tcp_out) = tcp.into_split();
    let (mut to_hub, mut from_hub) = ws.split();
    let up = async {
        let mut buf = vec![0u8; CHUNK];
        loop {
            match tcp_in.read(&mut buf).await {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if to_hub.send(Message::Binary(buf[..n].to_vec().into())).await.is_err() {
                        break;
                    }
                }
            }
        }
        let _ = to_hub.send(Message::Close(None)).await;
    };
    let down = async {
        while let Some(Ok(message)) = from_hub.next().await {
            match message {
                Message::Binary(data) => {
                    if tcp_out.write_all(&data).await.is_err() {
                        break;
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
        let _ = tcp_out.shutdown().await;
    };
    tokio::select! { _ = up => {}, _ = down => {} }
}

// ------------------------------------------------------------------------------------------ the exit's side

struct Shared {
    sharing: AtomicBool,
    wake: Notify,
}

fn shared() -> &'static Shared {
    static SHARED: OnceLock<Shared> = OnceLock::new();
    SHARED.get_or_init(|| Shared { sharing: AtomicBool::new(false), wake: Notify::new() })
}

static CLIENT: OnceLock<Client> = OnceLock::new();

/// The heartbeat's word on whether this computer's owner shares its internet with bots.
pub fn set_sharing(on: bool) {
    let s = shared();
    if s.sharing.swap(on, Ordering::SeqCst) != on {
        s.wake.notify_one();
    }
}

/// Start the exit's link supervisor; the bot's proxies start with the first turn that needs one.
pub fn start(client: Client) {
    let _ = CLIENT.set(client.clone());
    tokio::spawn(link_loop(client));
}

async fn link_loop(client: Client) {
    let s = shared();
    let mut backoff = Duration::from_secs(1);
    let mut said: Option<String> = None;
    loop {
        if !s.sharing.load(Ordering::SeqCst) {
            s.wake.notified().await;
            continue;
        }
        let began = Instant::now();
        match run_link(&client).await {
            Ok(()) => said = None,
            Err(e) => {
                if said.as_ref() != Some(&e) {
                    log(&format!("Tico runner: browser exit link failed ({e}); will retry"));
                    said = Some(e);
                }
            }
        }
        if began.elapsed() >= Duration::from_secs(30) {
            backoff = Duration::from_secs(1);
        }
        tokio::select! {
            _ = tokio::time::sleep(backoff) => {}
            _ = s.wake.notified() => {}
        }
        backoff = (backoff * 2).min(LINK_BACKOFF_MAX);
    }
}

/// One tunnel the relay asks this exit for.
#[derive(Debug, PartialEq)]
struct Open {
    id: String,
    host: String,
    port: u16,
    ticket: String,
}

impl Open {
    fn parse(text: &str) -> Option<Open> {
        let v: Value = serde_json::from_str(text).ok()?;
        let id = v["id"].as_str()?.to_string();
        let ticket = v["ticket"].as_str()?.to_string();
        let ok_id = !id.is_empty()
            && id.len() <= 128
            && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
        let ok_ticket = ticket.len() == 64 && ticket.bytes().all(|b| b.is_ascii_hexdigit());
        (v["op"] == "open" && ok_id && ok_ticket).then(|| Open {
            id,
            ticket,
            host: text_of(&v["host"]),
            port: v["port"].as_u64().and_then(|p| u16::try_from(p).ok()).unwrap_or(0),
        })
    }
}

async fn run_link(client: &Client) -> Result<(), String> {
    let mut ws = connect(client, "/api/v2/runners/me/browser-exit/ws", true).await?;
    let room = Arc::new(Semaphore::new(EXIT_TUNNELS));
    let s = shared();
    loop {
        tokio::select! {
            frame = ws.next() => match frame {
                Some(Ok(Message::Text(text))) => {
                    let Some(open) = Open::parse(&text) else { continue };
                    let client = client.clone();
                    let permit = room.clone().try_acquire_owned().ok();
                    tokio::spawn(async move { serve(&client, open, permit.is_some()).await; drop(permit) });
                }
                Some(Ok(Message::Close(_))) | None => return Ok(()),
                Some(Ok(_)) => {}
                Some(Err(e)) => return Err(format!("websocket: {e}")),
            },
            _ = s.wake.notified() => {
                if !s.sharing.load(Ordering::SeqCst) {
                    let _ = ws.close(None).await;
                    return Ok(());
                }
            }
        }
    }
}

/// Dial the site and join the tunnel at the relay at once; the first message says whether the site was reached.
async fn serve(client: &Client, open: Open, room: bool) {
    let path = format!("/api/v2/runners/me/browser-exit/tunnels/{}/exit?ticket={}", open.id, open.ticket);
    let target = async {
        if room {
            dial(&open.host, open.port).await
        } else {
            Err("this computer is busy".to_string())
        }
    };
    let (target, socket) = tokio::join!(target, connect(client, &path, false));
    let Ok(mut socket) = socket else { return };
    match target {
        Ok(tcp) => {
            if socket.send(Message::Text(r#"{"ok":true}"#.into())).await.is_ok() {
                pump(tcp, socket).await;
            }
        }
        Err(why) => {
            let _ = socket.send(Message::Text(json!({"ok": false, "error": why}).to_string().into())).await;
            let _ = socket.close(None).await;
        }
    }
}

// ------------------------------------------------------------------------------------------ the bot's side

/// One bot's proxy: its port, and what its last tunnel came to (the proxy's own page says it).
struct Proxy {
    port: u16,
    exit: Mutex<String>,
    last_error: Mutex<Option<String>>,
}

impl Proxy {
    fn note(&self, bot: &str, error: Option<String>) {
        let mut last = self.last_error.lock().unwrap();
        if let Some(e) = error.as_ref().filter(|e| last.as_ref() != Some(*e)) {
            log(&format!("Tico runner: {bot}'s browser exit refused a connection: {e}"));
        }
        *last = error;
    }
}

fn proxies() -> &'static Mutex<HashMap<String, Arc<Proxy>>> {
    static PROXIES: OnceLock<Mutex<HashMap<String, Arc<Proxy>>>> = OnceLock::new();
    PROXIES.get_or_init(|| Mutex::new(HashMap::new()))
}

fn preferred_port(bot: &str) -> u16 {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(bot.as_bytes());
    PORT_BASE + u16::from_be_bytes([digest[0], digest[1]]) % PORT_SPAN
}

/// The proxy URL a turn's browser goes through, when the turn names a browser exit (`attempt.browser_exit`, from
/// backend/browser_exit.py). The proxy outlives the turn: a browser the bot left open keeps using it.
pub async fn turn_proxy(bot: &str, attempt: &Value) -> Option<String> {
    let exit = &attempt["browser_exit"];
    if !exit.is_object() {
        return None;
    }
    let name = text_of(&exit["name"]);
    let client = CLIENT.get()?.clone();
    let existing = proxies().lock().unwrap().get(bot).cloned();
    let proxy = match existing {
        Some(p) => p,
        None => {
            let listener = match TcpListener::bind(("127.0.0.1", preferred_port(bot))).await {
                Ok(l) => l,
                Err(_) => TcpListener::bind(("127.0.0.1", 0)).await.ok()?,
            };
            let port = listener.local_addr().ok()?.port();
            let proxy = Arc::new(Proxy { port, exit: Mutex::new(name.clone()), last_error: Mutex::new(None) });
            let kept = proxies().lock().unwrap().entry(bot.to_string()).or_insert_with(|| proxy.clone()).clone();
            if Arc::ptr_eq(&kept, &proxy) {
                tokio::spawn(serve_proxy(listener, bot.to_string(), proxy.clone(), client));
            }
            kept
        }
    };
    *proxy.exit.lock().unwrap() = name;
    Some(format!("http://127.0.0.1:{}", proxy.port))
}

async fn serve_proxy(listener: TcpListener, bot: String, proxy: Arc<Proxy>, client: Client) {
    loop {
        let Ok((conn, _)) = listener.accept().await else {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        };
        tokio::spawn(handle(conn, bot.clone(), proxy.clone(), client.clone()));
    }
}

#[derive(Debug, PartialEq)]
enum Request {
    /// The proxy's own page: which exit, and its last refusal.
    Status,
    Connect {
        host: String,
        port: u16,
    },
    /// An absolute-form plain-http request, sent on as `head` over a tunnel to port 80.
    Plain {
        host: String,
        port: u16,
        head: Vec<u8>,
    },
}

/// Split `host:port`, `[v6]:port` or a bare host (with `default`).
fn host_port(authority: &str, default: Option<u16>) -> Option<(String, u16)> {
    let (host, port) = if let Some(rest) = authority.strip_prefix('[') {
        let (host, after) = rest.split_once(']')?;
        match after.strip_prefix(':') {
            Some(p) => (host, p.parse().ok()?),
            None if after.is_empty() => (host, default?),
            None => return None,
        }
    } else {
        match authority.rsplit_once(':') {
            Some((h, p)) => (h, p.parse().ok()?),
            None => (authority, default?),
        }
    };
    valid_host(host).then(|| (host.to_ascii_lowercase(), port))
}

/// The request head the browser sent this proxy, or the status and words to refuse it with.
fn parse(head: &[u8]) -> Result<Request, (u16, String)> {
    let text = std::str::from_utf8(head).map_err(|_| (400, "not an HTTP request".to_string()))?;
    let mut lines = text.split("\r\n");
    let first = lines.next().unwrap_or_default();
    let mut parts = first.split(' ');
    let (method, target, version) = match (parts.next(), parts.next(), parts.next(), parts.next()) {
        (Some(m), Some(t), Some(v), None) if v.starts_with("HTTP/1.") => (m, t, v),
        _ => return Err((400, "not an HTTP request".into())),
    };
    let bad_port = || (403, "only ports 80 and 443 go through a browser exit".to_string());
    if method == "CONNECT" {
        let (host, port) = host_port(target, None).ok_or((400, "not host:port".to_string()))?;
        return if PORTS.contains(&port) { Ok(Request::Connect { host, port }) } else { Err(bad_port()) };
    }
    if target.starts_with('/') {
        return Ok(Request::Status);
    }
    let Some(rest) = target.strip_prefix("http://") else {
        return Err((400, "send https through CONNECT".into()));
    };
    let (authority, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let (host, port) = host_port(authority, Some(80)).ok_or((400, "not a host".to_string()))?;
    if port != 80 {
        return Err(bad_port());
    }
    // One request per tunnel: a browser reuses a proxy connection for any host, so this one closes after it.
    let mut out = format!("{method} {path} {version}\r\n");
    for line in lines.filter(|l| !l.is_empty()) {
        let name = line.split(':').next().unwrap_or_default().trim().to_ascii_lowercase();
        if !matches!(name.as_str(), "proxy-connection" | "proxy-authorization" | "connection" | "keep-alive") {
            out.push_str(line);
            out.push_str("\r\n");
        }
    }
    out.push_str("Connection: close\r\n\r\n");
    Ok(Request::Plain { host, port, head: out.into_bytes() })
}

/// The head (through the blank line) and whatever followed it in the same reads.
async fn read_head(conn: &mut TcpStream) -> Option<(Vec<u8>, Vec<u8>)> {
    let mut data = Vec::new();
    let mut buf = [0u8; 4096];
    loop {
        let n = conn.read(&mut buf).await.ok()?;
        if n == 0 {
            return None;
        }
        data.extend_from_slice(&buf[..n]);
        if let Some(end) = data.windows(4).position(|w| w == b"\r\n\r\n") {
            let rest = data.split_off(end + 4);
            data.truncate(end);
            return Some((data, rest));
        }
        if data.len() > HEAD_BYTES {
            return None;
        }
    }
}

async fn reply(conn: &mut TcpStream, code: u16, words: &str) {
    let reason = match code {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        _ => "Bad Gateway",
    };
    let header: String = words.chars().filter(|c| c.is_ascii() && !c.is_ascii_control()).take(200).collect();
    let body = format!("{words}\n");
    let response = format!(
        "HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\nX-Tico-Browser-Exit: {header}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = conn.write_all(response.as_bytes()).await;
    let _ = conn.shutdown().await;
}

async fn handle(mut conn: TcpStream, bot: String, proxy: Arc<Proxy>, client: Client) {
    let Ok(Some((head, rest))) = tokio::time::timeout(Duration::from_secs(30), read_head(&mut conn)).await else {
        return;
    };
    let (host, port, first) = match parse(&head) {
        Ok(Request::Status) => {
            let exit = proxy.exit.lock().unwrap().clone();
            let last = proxy.last_error.lock().unwrap().clone();
            let words = match last {
                Some(e) => format!("Browsing through {exit}. Last refusal: {e}"),
                None => format!("Browsing through {exit}."),
            };
            return reply(&mut conn, 200, &words).await;
        }
        Ok(Request::Connect { host, port }) => (host, port, rest),
        Ok(Request::Plain { host, port, head }) => (host, port, [head, rest].concat()),
        Err((code, words)) => return reply(&mut conn, code, &words).await,
    };
    let mut ws = match open_tunnel(&client, &bot, &host, port, &proxy).await {
        Ok(ws) => ws,
        Err(words) => {
            proxy.note(&bot, Some(words.clone()));
            return reply(&mut conn, 502, &words).await;
        }
    };
    proxy.note(&bot, None);
    if parse(&head).is_ok_and(|r| matches!(r, Request::Connect { .. }))
        && conn.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n").await.is_err()
    {
        return;
    }
    if !first.is_empty() && ws.send(Message::Binary(first.into())).await.is_err() {
        return;
    }
    pump(conn, ws).await;
}

/// What a tunnel the relay closed before it opened came to, in words that name the exit.
fn closed_words(exit: &str, reason: &str) -> String {
    match reason {
        "exit_no_answer" => format!("{exit} did not answer"),
        "exit_over_cap" => format!("{exit} reached its daily limit"),
        "exit_not_shared" | "sharing turned off" => {
            format!("{exit} does not share its internet with this bot")
        }
        "" => format!("{exit} closed the connection"),
        other => format!("{exit}: {other}"),
    }
}

/// Ask the hub for a tunnel to `host:port` through the bot's exit and join it. Any refusal is the error, in words
/// that name the exit; there is no other way out.
async fn open_tunnel(client: &Client, bot: &str, host: &str, port: u16, proxy: &Proxy) -> Result<Socket, String> {
    let mut once = client.clone();
    once.retries = 0; // a refusal is the answer, and the browser is waiting
    let grant = once
        .post("runners/me/browser-exit/tunnels", &json!({"bot": bot, "host": host, "port": port}), None)
        .await
        .map_err(|e| {
            // The hub's own refusals already name the exit; anything else (the hub unreachable) gets the name here.
            if e.code.starts_with("browser_exit") {
                e.detail
            } else {
                format!("can't reach {} through the hub: {}", proxy.exit.lock().unwrap(), e.detail)
            }
        })?;
    let exit = Some(text_of(&grant["exit"]["name"]))
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| proxy.exit.lock().unwrap().clone());
    *proxy.exit.lock().unwrap() = exit.clone();
    let (id, ticket) = (text_of(&grant["tunnel_id"]), text_of(&grant["ticket"]));
    if id.is_empty()
        || !id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        || !ticket.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err(format!("{exit}: the hub's answer was not a tunnel"));
    }
    let path = format!("/api/v2/runners/me/browser-exit/tunnels/{id}/ws?ticket={ticket}");
    let mut ws = connect(client, &path, false).await.map_err(|e| format!("{exit} is unreachable: {e}"))?;
    match tokio::time::timeout(ANSWER, ws.next()).await {
        Ok(Some(Ok(Message::Text(text)))) => {
            let status: Value = serde_json::from_str(&text).unwrap_or_default();
            if status["ok"] == true {
                Ok(ws)
            } else {
                Err(format!("{exit} refused {host}: {}", text_of(&status["error"])))
            }
        }
        Ok(Some(Ok(Message::Close(Some(frame))))) => Err(closed_words(&exit, &frame.reason)),
        Ok(_) => Err(closed_words(&exit, "")),
        Err(_) => Err(format!("{exit} did not answer")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_public_addresses_that_are_not_the_computers_own_are_dialled() {
        let own: Vec<IpAddr> = vec!["203.0.113.7".parse().unwrap()];
        for (ip, why) in [
            ("127.0.0.1", "a loopback address"),
            ("10.1.2.3", "a private network address"),
            ("172.16.0.1", "a private network address"),
            ("192.168.1.1", "a private network address"),
            ("169.254.169.254", "a link-local address"),
            ("100.64.0.1", "a carrier-grade NAT address"),
            ("100.127.255.255", "a carrier-grade NAT address"),
            ("224.0.0.1", "a multicast address"),
            ("0.0.0.0", "an unspecified address"),
            ("255.255.255.255", "a reserved address"),
            ("203.0.113.7", "this computer's own address"),
            ("::1", "a loopback address"),
            ("::", "an unspecified address"),
            ("fd00::1", "a unique local address"),
            ("fe80::1", "a link-local address"),
            ("ff02::1", "a multicast address"),
            ("::ffff:10.0.0.1", "a private network address"),
            ("::ffff:203.0.113.7", "this computer's own address"),
            ("64:ff9b::a00:1", "a private network address"),
            ("2002:c0a8:101::1", "a private network address"),
        ] {
            assert_eq!(refused(ip.parse().unwrap(), &own), Some(why), "{ip}");
        }
        for ip in ["93.184.215.14", "100.128.0.1", "172.32.0.1", "2606:4700::1111", "64:ff9b::5db8:d70e"] {
            assert_eq!(refused(ip.parse().unwrap(), &own), None, "{ip}");
        }
    }

    #[tokio::test]
    async fn an_exit_never_dials_another_port_or_a_name_that_resolves_to_a_private_address() {
        // Something listens on loopback; the exit must not reach it however it is named.
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(dial("127.0.0.1", port).await.unwrap_err().contains("ports 80 and 443"));
        for host in ["localhost", "127.0.0.1", "::1", "10.0.0.8", "169.254.169.254"] {
            let why = dial(host, 443).await.unwrap_err();
            assert!(why.contains("address"), "{host}: {why}");
        }
        assert!(dial("acme.example/x", 443).await.unwrap_err().contains("not a hostname"));
        let reached = tokio::time::timeout(Duration::from_millis(50), listener.accept()).await;
        assert!(reached.is_err(), "nothing reached the loopback listener");
    }

    #[test]
    fn the_proxy_reads_connect_and_plain_http_and_refuses_other_ports() {
        assert_eq!(
            parse(b"CONNECT www.acme.example:443 HTTP/1.1\r\nHost: www.acme.example:443"),
            Ok(Request::Connect { host: "www.acme.example".into(), port: 443 })
        );
        assert_eq!(
            parse(b"CONNECT [2606:4700::1111]:443 HTTP/1.1"),
            Ok(Request::Connect { host: "2606:4700::1111".into(), port: 443 })
        );
        assert_eq!(parse(b"CONNECT www.acme.example:22 HTTP/1.1").unwrap_err().0, 403);
        assert_eq!(parse(b"CONNECT www.acme.example HTTP/1.1").unwrap_err().0, 400);
        assert_eq!(parse(b"GET http://www.acme.example:8080/ HTTP/1.1").unwrap_err().0, 403);
        assert_eq!(parse(b"GET https://www.acme.example/ HTTP/1.1").unwrap_err().0, 400);
        assert_eq!(parse(b"GET / HTTP/1.1\r\nHost: 127.0.0.1"), Ok(Request::Status));
        let Ok(Request::Plain { host, port, head }) =
            parse(b"GET http://WWW.acme.example/a?b=1 HTTP/1.1\r\nHost: www.acme.example\r\nProxy-Connection: keep-alive\r\nConnection: keep-alive\r\nAccept: */*")
        else {
            panic!("plain http")
        };
        assert_eq!((host.as_str(), port), ("www.acme.example", 80));
        assert_eq!(
            String::from_utf8(head).unwrap(),
            "GET /a?b=1 HTTP/1.1\r\nHost: www.acme.example\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        );
    }

    #[test]
    fn an_open_names_a_tunnel_and_a_one_use_ticket() {
        let ticket = "a".repeat(64);
        let text =
            json!({"op": "open", "id": "t1", "host": "www.acme.example", "port": 443, "ticket": ticket}).to_string();
        assert_eq!(
            Open::parse(&text),
            Some(Open { id: "t1".into(), host: "www.acme.example".into(), port: 443, ticket: ticket.clone() })
        );
        assert_eq!(
            Open::parse(&json!({"op": "open", "id": "../x", "host": "h", "port": 443, "ticket": ticket}).to_string()),
            None
        );
        assert_eq!(
            Open::parse(&json!({"op": "open", "id": "t1", "host": "h", "port": 443, "ticket": "short"}).to_string()),
            None
        );
    }

    /// A stand-in hub that refuses every tunnel the way backend/browser_exit.py does when the exit is offline.
    async fn refusing_hub() -> String {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        tokio::spawn(async move {
            loop {
                let Ok((mut conn, _)) = listener.accept().await else {
                    return;
                };
                tokio::spawn(async move {
                    let mut buf = vec![0u8; 8192];
                    let mut seen = Vec::new();
                    while !seen.windows(4).any(|w| w == b"\r\n\r\n") {
                        let n = conn.read(&mut buf).await.unwrap_or(0);
                        if n == 0 {
                            return;
                        }
                        seen.extend_from_slice(&buf[..n]);
                    }
                    let body = r#"{"error":{"code":"browser_exit_offline","detail":"Cara's MacBook is offline","retryable":false}}"#;
                    let response = format!("HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                    let _ = conn.write_all(response.as_bytes()).await;
                });
            }
        });
        url
    }

    #[tokio::test]
    async fn when_the_exit_is_offline_the_browser_gets_a_502_naming_it_and_nothing_goes_direct() {
        let hub = refusing_hub().await;
        let client = Client::new(&hub, "runner-token", 5, 3).unwrap();
        let proxy =
            Arc::new(Proxy { port: 0, exit: Mutex::new("Cara's MacBook".into()), last_error: Mutex::new(None) });
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(serve_proxy(listener, "ops".into(), proxy.clone(), client));
        // The site the browser asks for is on this computer: a direct connection would reach it.
        let site = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let site_port = site.local_addr().unwrap().port();
        for request in [
            "CONNECT www.acme.example:443 HTTP/1.1\r\nHost: www.acme.example:443\r\n\r\n".to_string(),
            format!("GET http://localhost:{site_port}/ HTTP/1.1\r\n\r\n"),
            "GET http://www.acme.example/ HTTP/1.1\r\nHost: www.acme.example\r\n\r\n".to_string(),
        ] {
            let mut browser = TcpStream::connect(addr).await.unwrap();
            browser.write_all(request.as_bytes()).await.unwrap();
            let mut answer = String::new();
            browser.read_to_string(&mut answer).await.unwrap();
            assert!(answer.starts_with("HTTP/1.1 502 ") || answer.starts_with("HTTP/1.1 403 "), "{answer}");
            if request.contains("acme") {
                assert!(answer.contains("Cara's MacBook is offline"), "{answer}");
            }
        }
        assert_eq!(proxy.last_error.lock().unwrap().as_deref(), Some("Cara's MacBook is offline"));
        assert!(tokio::time::timeout(Duration::from_millis(50), site.accept()).await.is_err(), "nothing went direct");
        let mut status = TcpStream::connect(addr).await.unwrap();
        status.write_all(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").await.unwrap();
        let mut answer = String::new();
        status.read_to_string(&mut answer).await.unwrap();
        assert!(
            answer.contains("Browsing through Cara's MacBook. Last refusal: Cara's MacBook is offline"),
            "{answer}"
        );
    }
}
