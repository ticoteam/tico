//! Exit for bots, this computer's link (backend/exits.py, relay-rs/src/exit.rs): one websocket to the hub,
//! `/api/v2/runners/me/exit/ws`, with this runner's own token, carrying many streams at once in both roles.
//!
//! - **As the bot's computer**, a turn's proxy (crate::exit_proxy) opens a stream (odd id) for each connection to a
//!   site its bot sends through an exit, and gets OPENED or the hub's refusal (`exit_offline`, `not_listed`, ...).
//! - **As an exit**, while the heartbeat answer says `exit: true` (the owner turned "Exit for bots" on), the relay
//!   opens streams here (even ids) naming a host and port. This computer resolves the name itself and refuses the
//!   stream if any address it resolves to is private, loopback, link-local, CGNAT, documentation, multicast or a
//!   metadata address, for IPv4 and IPv6 (embedded IPv4 checked too), then connects to a vetted address itself: the
//!   name is never resolved again, so a DNS answer that changes in between (rebinding) cannot reach this network.
//!   Only ports 80 and 443.
//!
//! Frames are binary: 1 byte kind, 4 byte stream id (big endian), payload; see relay-rs/src/exit.rs. Each direction of
//! a stream starts with 256 KiB of credit; a sender spends it with DATA and the receiver grants it back (WINDOW) after
//! writing the bytes on, so a slow side slows its sender and memory stays bounded. The link is open while this
//! computer serves as an exit or a turn's proxy holds it, and closes 30 seconds after neither does.
use std::collections::HashMap;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot, watch, Notify, Semaphore};

use crate::client::{bearer, Client, CLIENT_VERSION};

pub const CAPABILITY: &str = "exit_v1";
pub const WINDOW: u32 = 256 * 1024;
pub const CHUNK: usize = 32 * 1024;
const PORTS: [u16; 2] = [80, 443];
const INBOUND: usize = 64;
const QUEUE: usize = 256;
const PING: Duration = Duration::from_secs(20);
const IDLE: Duration = Duration::from_secs(60);
const LINGER: Duration = Duration::from_secs(30);
const CONNECT: Duration = Duration::from_secs(10);
/// How long a new stream waits for the link to come up before it is refused (never sent elsewhere).
const LINK_WAIT: Duration = if cfg!(test) { Duration::from_secs(1) } else { Duration::from_secs(10) };
const ANSWER: Duration = Duration::from_secs(20);

pub mod frame {
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
}

/// Why a stream did not open or ended: the hub's code and its words.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub code: String,
    pub detail: String,
}

impl Refusal {
    pub fn new(code: &str, detail: &str) -> Self {
        Refusal { code: code.into(), detail: detail.into() }
    }

    fn from_payload(payload: &[u8]) -> Self {
        let body: Value = serde_json::from_slice(payload).unwrap_or(Value::Null);
        Refusal::new(body["code"].as_str().unwrap_or("closed"), body["detail"].as_str().unwrap_or(""))
    }
}

// ------------------------------------------------------------------------------------------------ address checks
/// Addresses an exit never connects to: everything that is not the public internet.
pub fn blocked(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => blocked_v4(v4),
        IpAddr::V6(v6) => blocked_v6(v6),
    }
}

fn in_v4(ip: Ipv4Addr, net: [u8; 4], prefix: u32) -> bool {
    let mask = u32::MAX.checked_shl(32 - prefix).unwrap_or(0);
    u32::from(ip) & mask == u32::from(Ipv4Addr::from(net)) & mask
}

fn blocked_v4(ip: Ipv4Addr) -> bool {
    const NETS: &[([u8; 4], u32)] = &[
        ([0, 0, 0, 0], 8),         // this network
        ([10, 0, 0, 0], 8),        // private
        ([100, 64, 0, 0], 10),     // carrier-grade NAT
        ([127, 0, 0, 0], 8),       // loopback
        ([169, 254, 0, 0], 16),    // link-local, cloud metadata (169.254.169.254)
        ([172, 16, 0, 0], 12),     // private
        ([192, 0, 0, 0], 24),      // IETF protocol assignments
        ([192, 0, 2, 0], 24),      // documentation
        ([192, 88, 99, 0], 24),    // 6to4 relay anycast
        ([192, 168, 0, 0], 16),    // private
        ([198, 18, 0, 0], 15),     // benchmarking
        ([198, 51, 100, 0], 24),   // documentation
        ([203, 0, 113, 0], 24),    // documentation
        ([224, 0, 0, 0], 4),       // multicast
        ([240, 0, 0, 0], 4),       // reserved, broadcast
    ];
    NETS.iter().any(|(net, prefix)| in_v4(ip, *net, *prefix))
}

fn blocked_v6(ip: Ipv6Addr) -> bool {
    let bits = u128::from(ip);
    let within = |net: u128, prefix: u32| {
        let mask = u128::MAX.checked_shl(128 - prefix).unwrap_or(0);
        bits & mask == net & mask
    };
    let embedded = |offset: u32| Ipv4Addr::from(((bits >> offset) & 0xffff_ffff) as u32);
    if ip.is_unspecified() || ip.is_loopback() {
        return true;
    }
    // IPv4 inside IPv6: mapped (::ffff:a.b.c.d), compatible (::a.b.c.d), NAT64 (64:ff9b::/96), 6to4 (2002::/16).
    if within(0xffff_0000_0000, 96) || within(0, 96) || within(0x0064_ff9b_u128 << 96, 96) {
        return blocked_v4(embedded(0));
    }
    if within(0x2002_u128 << 112, 16) {
        return blocked_v4(embedded(80));
    }
    const NETS: &[(u128, u32)] = &[
        (0x0064_ff9b_0001_u128 << 80, 48), // local-use NAT64
        (0x0100_u128 << 112, 64),          // discard-only
        (0x2001_u128 << 112, 32),          // Teredo (its embedded address is obfuscated; refuse it all)
        (0x2001_0db8_u128 << 96, 32),      // documentation
        (0x3fff_u128 << 112, 20),          // documentation
        (0xfc00_u128 << 112, 7),           // unique local, incl. fd00:ec2::254 (cloud metadata)
        (0xfe80_u128 << 112, 10),          // link-local
        (0xfec0_u128 << 112, 10),          // site-local (deprecated)
        (0xff00_u128 << 112, 8),           // multicast
    ];
    NETS.iter().any(|(net, prefix)| within(*net, *prefix))
}

/// The addresses to try for `host:port`, once every address the name resolved to is public. Any private answer
/// refuses the whole name: a name that is partly inside is not one an exit carries.
pub fn vet(host: &str, port: u16, resolved: &[SocketAddr]) -> Result<Vec<SocketAddr>, Refusal> {
    if !PORTS.contains(&port) {
        return Err(Refusal::new("port", "exits carry web traffic only (ports 80 and 443)"));
    }
    if resolved.is_empty() {
        return Err(Refusal::new("dns", &format!("{host} did not resolve")));
    }
    if let Some(bad) = resolved.iter().find(|a| blocked(a.ip())) {
        return Err(Refusal::new("blocked", &format!("{host} resolves to {}, which is not on the internet", bad.ip())));
    }
    Ok(resolved.to_vec())
}

fn valid_host(host: &str) -> bool {
    !host.is_empty() && host.len() <= 253 && host.parse::<IpAddr>().is_err()
        && host.split('.').count() >= 2
        && host.split('.').all(|l| !l.is_empty() && l.len() <= 63 && !l.starts_with('-') && !l.ends_with('-')
            && l.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'))
}

/// Connect to `host:port` as an exit: resolve once, vet every address, connect to a vetted one.
pub async fn connect_out(host: &str, port: u16) -> Result<TcpStream, Refusal> {
    if !valid_host(host) {
        return Err(Refusal::new("invalid", "an exit connects to site names only"));
    }
    let resolved: Vec<SocketAddr> = tokio::time::timeout(CONNECT, tokio::net::lookup_host((host, port))).await
        .map_err(|_| Refusal::new("dns", &format!("{host} did not resolve in time")))?
        .map_err(|_| Refusal::new("dns", &format!("{host} did not resolve")))?
        .collect();
    let addresses = vet(host, port, &resolved)?;
    let mut last = String::new();
    for address in addresses {
        match tokio::time::timeout(CONNECT, TcpStream::connect(address)).await {
            Ok(Ok(socket)) => {
                let _ = socket.set_nodelay(true);
                return Ok(socket);
            }
            Ok(Err(e)) => last = e.to_string(),
            Err(_) => last = "timed out".into(),
        }
    }
    Err(Refusal::new("connect", &format!("could not reach {host}: {last}")))
}

// ------------------------------------------------------------------------------------------------ the link
enum Inbound {
    Data(Vec<u8>),
    Fin,
    Close,
}

struct Stream {
    inbound: mpsc::Sender<Inbound>,
    credit: Arc<Semaphore>,
    exit_role: bool,
}

struct Shared {
    client: Mutex<Option<Client>>,
    serving: AtomicBool,
    holders: AtomicUsize,
    wake: Notify,
    out: Mutex<Option<mpsc::Sender<Vec<u8>>>>,
    up: watch::Sender<bool>,
    streams: Mutex<HashMap<u32, Stream>>,
    pending: Mutex<HashMap<u32, oneshot::Sender<Result<(), Refusal>>>>,
    next_id: AtomicU32,
    started: AtomicBool,
}

fn shared() -> &'static Arc<Shared> {
    static S: OnceLock<Arc<Shared>> = OnceLock::new();
    S.get_or_init(|| Arc::new(Shared {
        client: Mutex::new(None), serving: AtomicBool::new(false), holders: AtomicUsize::new(0), wake: Notify::new(),
        out: Mutex::new(None), up: watch::channel(false).0, streams: Mutex::new(HashMap::new()),
        pending: Mutex::new(HashMap::new()), next_id: AtomicU32::new(1), started: AtomicBool::new(false),
    }))
}

impl Shared {
    fn wanted(&self) -> bool {
        self.serving.load(Ordering::SeqCst) || self.holders.load(Ordering::SeqCst) > 0
    }

    fn sender(&self) -> Option<mpsc::Sender<Vec<u8>>> {
        self.out.lock().unwrap().clone()
    }

    fn send_now(&self, bytes: Vec<u8>) {
        if let Some(tx) = self.sender() {
            let _ = tx.try_send(bytes);
        }
    }

    /// Every stream and open in flight ends with the link.
    fn drop_all(&self) {
        for (_, stream) in self.streams.lock().unwrap().drain() {
            let _ = stream.inbound.try_send(Inbound::Close);
        }
        for (_, waiter) in self.pending.lock().unwrap().drain() {
            let _ = waiter.send(Err(Refusal::new("link_lost", "the connection to Tico's exit relay dropped")));
        }
    }

    fn remove(&self, id: u32) -> Option<Stream> {
        self.streams.lock().unwrap().remove(&id)
    }

    /// One frame from the relay.
    fn dispatch(self: &Arc<Self>, kind: u8, id: u32, payload: &[u8]) {
        match kind {
            frame::OPEN if id % 2 == 0 => self.serve(id, payload),
            frame::OPENED => {
                if let Some(waiter) = self.pending.lock().unwrap().remove(&id) {
                    let _ = waiter.send(Ok(()));
                }
            }
            frame::DATA => {
                let full = self.streams.lock().unwrap().get(&id)
                    .map(|s| s.inbound.try_send(Inbound::Data(payload.to_vec())).is_err());
                if full == Some(true) {
                    // Only a sender ignoring its credit fills the queue.
                    self.remove(id);
                    self.send_now(frame::close(id, "window", "sent more than this computer asked for"));
                }
            }
            frame::WINDOW => {
                if let (Ok(raw), Some(stream)) = (<[u8; 4]>::try_from(payload), self.streams.lock().unwrap().get(&id)) {
                    let grant = u32::from_be_bytes(raw) as usize;
                    let room = (WINDOW as usize).saturating_sub(stream.credit.available_permits());
                    stream.credit.add_permits(grant.min(room));
                }
            }
            frame::FIN => {
                if let Some(stream) = self.streams.lock().unwrap().get(&id) {
                    let _ = stream.inbound.try_send(Inbound::Fin);
                }
            }
            frame::CLOSE => {
                if let Some(waiter) = self.pending.lock().unwrap().remove(&id) {
                    self.remove(id);
                    let _ = waiter.send(Err(Refusal::from_payload(payload)));
                } else if let Some(stream) = self.remove(id) {
                    let _ = stream.inbound.try_send(Inbound::Close);
                }
            }
            _ => {}
        }
    }

    /// The relay asks this computer, as an exit, to open `id`.
    fn serve(self: &Arc<Self>, id: u32, payload: &[u8]) {
        if !self.serving.load(Ordering::SeqCst) {
            self.send_now(frame::close(id, "exit_off", "this computer is not an exit now"));
            return;
        }
        let body: Value = serde_json::from_slice(payload).unwrap_or(Value::Null);
        let host = body["host"].as_str().unwrap_or("").to_ascii_lowercase();
        let port = body["port"].as_u64().and_then(|p| u16::try_from(p).ok()).unwrap_or(0);
        let Some(out) = self.sender() else { return };
        let (inbound, rx) = mpsc::channel(INBOUND);
        let credit = Arc::new(Semaphore::new(WINDOW as usize));
        self.streams.lock().unwrap().insert(id, Stream { inbound, credit: credit.clone(), exit_role: true });
        let me = self.clone();
        tokio::spawn(async move {
            match connect_out(&host, port).await {
                Ok(socket) => {
                    if !me.streams.lock().unwrap().contains_key(&id) {
                        return; // closed while connecting
                    }
                    if out.send(frame::encode(frame::OPENED, id, &[])).await.is_ok() {
                        pump(me, id, socket, Vec::new(), rx, credit, out).await;
                    }
                }
                Err(refusal) => {
                    if me.remove(id).is_some() {
                        let _ = out.send(frame::close(id, &refusal.code, &refusal.detail)).await;
                    }
                }
            }
        });
    }
}

/// Start the link's keeper once, with this runner's client.
pub fn init(client: Client) {
    let s = shared();
    *s.client.lock().unwrap() = Some(client);
    if s.started.swap(true, Ordering::SeqCst) {
        return;
    }
    tokio::spawn(keep(s.clone()));
}

/// The heartbeat answer's `exit`: serve as an exit or stop. Stopping ends the streams this computer carries.
pub fn set_serving(on: bool) {
    let s = shared();
    if s.serving.swap(on, Ordering::SeqCst) == on {
        return;
    }
    crate::util::log(&format!("Tico runner: exit for bots turned {} on this computer", if on { "on" } else { "off" }));
    if !on {
        let ids: Vec<u32> = s.streams.lock().unwrap().iter().filter(|(_, st)| st.exit_role).map(|(id, _)| *id).collect();
        for id in ids {
            if let Some(stream) = s.remove(id) {
                let _ = stream.inbound.try_send(Inbound::Close);
            }
            s.send_now(frame::close(id, "exit_off", "this computer is no longer an exit"));
        }
    }
    s.send_now(frame::encode(frame::SERVING, 0, &[on as u8]));
    s.wake.notify_one();
}

/// A turn's proxy keeps the link open while it holds this.
pub struct Hold(());

impl Drop for Hold {
    fn drop(&mut self) {
        shared().holders.fetch_sub(1, Ordering::SeqCst);
        shared().wake.notify_one();
    }
}

pub fn hold() -> Hold {
    shared().holders.fetch_add(1, Ordering::SeqCst);
    shared().wake.notify_one();
    Hold(())
}

/// An open stream through an exit, for the turn's proxy to pump.
pub struct Tunnel {
    id: u32,
    inbound: mpsc::Receiver<Inbound>,
    credit: Arc<Semaphore>,
    out: mpsc::Sender<Vec<u8>>,
}

impl Tunnel {
    /// Carry `socket` both ways until either side is done; `first` goes out before anything read from it.
    pub async fn pump(self, socket: TcpStream, first: Vec<u8>) {
        pump(shared().clone(), self.id, socket, first, self.inbound, self.credit, self.out).await
    }
}

/// Open a stream for run `attempt` to `host:port` through its bot's exit. Every failure is a refusal: the proxy
/// never sends a listed site anywhere else.
pub async fn open(attempt: &str, host: &str, port: u16) -> Result<Tunnel, Refusal> {
    let s = shared();
    let mut up = s.up.subscribe();
    if !*up.borrow() {
        s.wake.notify_one();
        let waited = tokio::time::timeout(LINK_WAIT, up.wait_for(|on| *on)).await;
        if !matches!(waited, Ok(Ok(_))) {
            return Err(Refusal::new("no_link", "Tico's exit relay is not reachable from this computer"));
        }
    }
    let out = s.sender().ok_or_else(|| Refusal::new("no_link", "Tico's exit relay is not reachable from this computer"))?;
    let id = s.next_id.fetch_add(2, Ordering::SeqCst) | 1;
    let (inbound, rx) = mpsc::channel(INBOUND);
    let credit = Arc::new(Semaphore::new(WINDOW as usize));
    let (answer, answered) = oneshot::channel();
    s.streams.lock().unwrap().insert(id, Stream { inbound, credit: credit.clone(), exit_role: false });
    s.pending.lock().unwrap().insert(id, answer);
    let body = json!({"attempt": attempt, "host": host, "port": port}).to_string();
    if out.send(frame::encode(frame::OPEN, id, body.as_bytes())).await.is_err() {
        s.pending.lock().unwrap().remove(&id);
        s.remove(id);
        return Err(Refusal::new("link_lost", "the connection to Tico's exit relay dropped"));
    }
    let result = match tokio::time::timeout(ANSWER, answered).await {
        Ok(Ok(result)) => result,
        Ok(Err(_)) => Err(Refusal::new("link_lost", "the connection to Tico's exit relay dropped")),
        Err(_) => {
            s.send_now(frame::close(id, "timeout", "no answer"));
            Err(Refusal::new("timeout", "the exit did not answer in time"))
        }
    };
    if let Err(refusal) = result {
        s.pending.lock().unwrap().remove(&id);
        s.remove(id);
        return Err(refusal);
    }
    Ok(Tunnel { id, inbound: rx, credit, out })
}

/// Carry one stream between a local socket and the link until both directions are done. The two directions run
/// apart: one waiting for credit never stops the other from writing what arrived and granting credit back, so two
/// sides sending hard at once cannot wait on each other.
async fn pump(s: Arc<Shared>, id: u32, socket: TcpStream, first: Vec<u8>, mut inbound: mpsc::Receiver<Inbound>,
              credit: Arc<Semaphore>, out: mpsc::Sender<Vec<u8>>) {
    enum End { Done, Reset, Gone }
    let (mut read, mut write) = socket.into_split();
    let up_out = out.clone();
    let up = async move {
        let mut buf = vec![0u8; CHUNK];
        let mut pending = first;
        loop {
            let data = if pending.is_empty() {
                match read.read(&mut buf).await {
                    Ok(0) => return if up_out.send(frame::encode(frame::FIN, id, &[])).await.is_ok() { End::Done } else { End::Gone },
                    Ok(n) => buf[..n].to_vec(),
                    Err(_) => return End::Reset,
                }
            } else {
                std::mem::take(&mut pending)
            };
            // Send what the credit allows now (at least a byte, waiting for that), never all-or-nothing: a window smaller
            // than one read still moves.
            let mut rest = &data[..];
            while !rest.is_empty() {
                match credit.acquire().await {
                    Ok(permit) => permit.forget(),
                    Err(_) => return End::Gone,
                }
                let want = rest.len().min(CHUNK);
                let extra = (want - 1).min(credit.available_permits());
                if extra > 0 {
                    if let Ok(permit) = credit.try_acquire_many(extra as u32) { permit.forget(); }
                }
                let n = 1 + extra;
                if up_out.send(frame::encode(frame::DATA, id, &rest[..n])).await.is_err() {
                    return End::Gone;
                }
                rest = &rest[n..];
            }
        }
    };
    let down_out = out.clone();
    let down = async move {
        loop {
            match inbound.recv().await {
                Some(Inbound::Data(data)) => {
                    if write.write_all(&data).await.is_err() {
                        return End::Reset;
                    }
                    if down_out.send(frame::encode(frame::WINDOW, id, &(data.len() as u32).to_be_bytes())).await.is_err() {
                        return End::Gone;
                    }
                }
                Some(Inbound::Fin) => {
                    let _ = write.shutdown().await;
                    return End::Done;
                }
                Some(Inbound::Close) | None => return End::Gone,
            }
        }
    };
    tokio::pin!(up);
    tokio::pin!(down);
    let (mut up_done, mut down_done) = (false, false);
    let ended = loop {
        let end = tokio::select! {
            end = &mut up, if !up_done => { up_done = true; end }
            end = &mut down, if !down_done => { down_done = true; end }
        };
        match end {
            End::Done if up_done && down_done => break Some(("done", "")),
            End::Done => {}
            End::Reset => break Some(("reset", "the connection was reset")),
            End::Gone => break None,
        }
    };
    // A stream still in the table is closed from this side; one already gone was closed by the other.
    if s.remove(id).is_some() {
        let (code, detail) = ended.unwrap_or(("closed", ""));
        let _ = out.send(frame::close(id, code, detail)).await;
    }
}

/// `wss://<hub>/api/v2/runners/me/exit/ws`.
pub fn ws_url(hub: &str) -> Result<String, String> {
    let rest = if let Some(r) = hub.strip_prefix("https://") {
        format!("wss://{r}")
    } else if let Some(r) = hub.strip_prefix("http://") {
        format!("ws://{r}")
    } else {
        return Err(format!("not an http(s) hub URL: {hub}"));
    };
    Ok(format!("{}/api/v2/runners/me/exit/ws", rest.trim_end_matches('/')))
}

/// Keep the link open while it is wanted; reconnect with backoff.
async fn keep(s: Arc<Shared>) {
    let mut backoff = Duration::from_secs(1);
    loop {
        while !s.wanted() {
            s.wake.notified().await;
        }
        let client = s.client.lock().unwrap().clone();
        let Some(client) = client else { tokio::time::sleep(backoff).await; continue };
        match run(&s, &client).await {
            Ok(()) => backoff = Duration::from_secs(1),
            Err(e) => {
                crate::util::log(&format!("Tico runner: exit link: {e}"));
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
        }
    }
}

async fn run(s: &Arc<Shared>, client: &Client) -> Result<(), String> {
    use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message};
    let token = bearer(&client.token).map_err(|e| e.detail)?;
    let mut request = ws_url(&client.url)?.into_client_request().map_err(|e| e.to_string())?;
    let headers = request.headers_mut();
    headers.insert("Authorization", format!("Bearer {token}").parse().map_err(|_| "bad token".to_string())?);
    headers.insert("User-Agent", format!("Tico-Client/{CLIENT_VERSION}").parse().unwrap());
    let (ws, _) = tokio::time::timeout(Duration::from_secs(15),
        tokio_tungstenite::connect_async_tls_with_config(request, None, false, Some(crate::tls::websocket_connector())))
        .await
        .map_err(|_| "the hub did not answer".to_string())?
        .map_err(|e| format!("websocket: {e}"))?;
    let (mut sink, mut source) = ws.split();
    let (tx, mut rx) = mpsc::channel::<Vec<u8>>(QUEUE);
    let _ = tx.try_send(frame::encode(frame::SERVING, 0, &[s.serving.load(Ordering::SeqCst) as u8]));
    *s.out.lock().unwrap() = Some(tx);
    s.up.send_replace(true);
    let writer = tokio::spawn(async move {
        let mut ping = tokio::time::interval(PING);
        loop {
            let message = tokio::select! {
                m = rx.recv() => match m { Some(m) => Message::Binary(m.into()), None => break },
                _ = ping.tick() => Message::Ping(Vec::new().into()),
            };
            if !matches!(tokio::time::timeout(Duration::from_secs(10), sink.send(message)).await, Ok(Ok(()))) {
                break;
            }
        }
        let _ = sink.close().await;
    });
    let mut idle_since: Option<tokio::time::Instant> = None;
    let result = loop {
        if s.wanted() {
            idle_since = None;
        } else if idle_since.is_none() {
            idle_since = Some(tokio::time::Instant::now());
        }
        let linger = idle_since.map(|t| t + LINGER);
        tokio::select! {
            _ = async { match linger { Some(at) => tokio::time::sleep_until(at).await, None => std::future::pending().await } },
                if s.streams.lock().unwrap().is_empty() => break Ok(()),
            _ = s.wake.notified() => continue,
            _ = tokio::time::sleep(Duration::from_secs(5)) => continue,
            message = tokio::time::timeout(IDLE, source.next()) => match message {
                Ok(Some(Ok(Message::Binary(data)))) => match frame::decode(&data) {
                    Some((kind, id, payload)) => s.dispatch(kind, id, payload),
                    None => break Err("the relay sent a frame this runner does not know".into()),
                },
                Ok(Some(Ok(Message::Close(_)))) | Ok(None) => break Err("the relay closed the link".into()),
                Ok(Some(Ok(_))) => {}
                Ok(Some(Err(e))) => break Err(format!("websocket: {e}")),
                Err(_) => break Err("the relay went quiet".into()),
            },
        }
    };
    *s.out.lock().unwrap() = None;
    s.up.send_replace(false);
    s.drop_all();
    writer.abort();
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(text: &str) -> IpAddr {
        text.parse().unwrap()
    }

    #[test]
    fn private_and_special_addresses_are_blocked_for_ipv4_and_ipv6() {
        for bad in ["0.0.0.0", "10.1.2.3", "100.64.0.1", "100.127.255.255", "127.0.0.1", "169.254.169.254",
                    "172.16.0.1", "172.31.255.255", "192.0.0.170", "192.0.2.1", "192.168.1.1", "198.18.0.1",
                    "198.51.100.7", "203.0.113.9", "224.0.0.1", "255.255.255.255", "::", "::1", "::ffff:10.0.0.1",
                    "::ffff:169.254.169.254", "::127.0.0.1", "64:ff9b::a9fe:a9fe", "2002:c0a8:0101::1", "fc00::1",
                    "fd00:ec2::254", "fe80::1", "fec0::1", "ff02::1", "2001:db8::1", "2001:0:4136:e378::1", "100::1"] {
            assert!(blocked(ip(bad)), "{bad} should be blocked");
        }
        for good in ["93.184.216.34", "1.1.1.1", "100.63.255.255", "100.128.0.1", "172.32.0.1", "8.8.8.8",
                     "::ffff:93.184.216.34", "64:ff9b::808:808", "2002:5db8:d822::1", "2606:4700:4700::1111",
                     "2a00:1450:4001:80b::200e"] {
            assert!(!blocked(ip(good)), "{good} should be allowed");
        }
    }

    #[test]
    fn a_name_is_refused_when_any_address_it_resolves_to_is_private() {
        let public: SocketAddr = "93.184.216.34:443".parse().unwrap();
        let rebound: SocketAddr = "[::ffff:127.0.0.1]:443".parse().unwrap();
        assert_eq!(vet("shop.acme.example", 443, &[public]).unwrap(), vec![public]);
        let refused = vet("shop.acme.example", 443, &[public, rebound]).unwrap_err();
        assert_eq!(refused.code, "blocked");
        assert!(refused.detail.contains("127.0.0.1"));
        assert_eq!(vet("shop.acme.example", 22, &[public]).unwrap_err().code, "port");
        assert_eq!(vet("shop.acme.example", 443, &[]).unwrap_err().code, "dns");
    }

    #[tokio::test]
    async fn the_exit_checks_what_the_name_really_resolves_to() {
        // `localhost` resolves to loopback here: the check runs after resolution, not on the name.
        assert!(!valid_host("localhost"));
        assert_eq!(connect_out("localhost", 443).await.unwrap_err().code, "invalid");
        assert_eq!(connect_out("127.0.0.1", 443).await.unwrap_err().code, "invalid");
        let resolved: Vec<SocketAddr> = tokio::net::lookup_host(("localhost", 443)).await.unwrap().collect();
        assert_eq!(vet("localhost.acme.example", 443, &resolved).unwrap_err().code, "blocked");
    }

    #[test]
    fn frames_round_trip() {
        let bytes = frame::encode(frame::WINDOW, 7, &5u32.to_be_bytes());
        assert_eq!(frame::decode(&bytes), Some((frame::WINDOW, 7, &[0, 0, 0, 5][..])));
        assert_eq!(frame::decode(&[0, 0, 0, 0, 1]), None);
        assert_eq!(Refusal::from_payload(br#"{"code":"exit_offline","detail":"waiting for exit computer Home Mac"}"#),
                   Refusal::new("exit_offline", "waiting for exit computer Home Mac"));
        assert_eq!(ws_url("https://hub.acme.example/").unwrap(), "wss://hub.acme.example/api/v2/runners/me/exit/ws");
    }

    #[tokio::test]
    async fn a_stream_pumps_both_ways_within_its_credit_and_ends_with_fin() {
        // A local socket pair stands in for the site; the channel stands in for the link.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let site = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut got = vec![0u8; 5];
            socket.read_exact(&mut got).await.unwrap();
            socket.write_all(b"reply").await.unwrap();
            socket.shutdown().await.unwrap();
            got
        });
        let socket = TcpStream::connect(address).await.unwrap();
        let s = shared().clone();
        let (inbound, rx) = mpsc::channel(INBOUND);
        let credit = Arc::new(Semaphore::new(3)); // less than the first write: the pump waits for a grant
        s.streams.lock().unwrap().insert(901, Stream { inbound: inbound.clone(), credit: credit.clone(), exit_role: true });
        let (out, mut sent) = mpsc::channel(64);
        let pumping = tokio::spawn(pump(s.clone(), 901, socket, b"hello".to_vec(), rx, credit.clone(), out));
        assert_eq!(sent.recv().await.unwrap(), frame::encode(frame::DATA, 901, b"hel"));
        credit.add_permits(2);
        assert_eq!(sent.recv().await.unwrap(), frame::encode(frame::DATA, 901, b"lo"));
        inbound.send(Inbound::Data(b"world".to_vec())).await.unwrap();
        // The bytes reach the site and their credit goes back.
        assert_eq!(sent.recv().await.unwrap(), frame::encode(frame::WINDOW, 901, &5u32.to_be_bytes()));
        assert_eq!(site.await.unwrap(), b"world");
        // The site's answer waits for credit, then goes out, then the site's end.
        credit.add_permits(5);
        assert_eq!(sent.recv().await.unwrap(), frame::encode(frame::DATA, 901, b"reply"));
        assert_eq!(sent.recv().await.unwrap(), frame::encode(frame::FIN, 901, &[]));
        inbound.send(Inbound::Fin).await.unwrap();
        assert_eq!(Refusal::from_payload(frame::decode(&sent.recv().await.unwrap()).unwrap().2).code, "done");
        pumping.await.unwrap();
        assert!(!s.streams.lock().unwrap().contains_key(&901));
    }

    #[tokio::test]
    async fn an_exit_that_is_off_refuses_and_a_close_reaches_a_waiting_open() {
        let s = shared().clone();
        let (tx, mut sent) = mpsc::channel(8);
        *s.out.lock().unwrap() = Some(tx);
        s.serving.store(false, Ordering::SeqCst);
        s.dispatch(frame::OPEN, 902, br#"{"host":"shop.acme.example","port":443}"#);
        let refused = sent.recv().await.unwrap();
        assert_eq!(frame::decode(&refused).unwrap().0, frame::CLOSE);
        assert_eq!(Refusal::from_payload(frame::decode(&refused).unwrap().2).code, "exit_off");
        // A refusal from the hub settles the open that waits for it.
        let (answer, answered) = oneshot::channel();
        s.pending.lock().unwrap().insert(903, answer);
        s.dispatch(frame::CLOSE, 903, br#"{"code":"exit_offline","detail":"waiting for exit computer Home Mac"}"#);
        assert_eq!(answered.await.unwrap().unwrap_err().code, "exit_offline");
        *s.out.lock().unwrap() = None;
    }
}
