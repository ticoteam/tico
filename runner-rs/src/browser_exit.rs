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
//! that is not on the public internet or is one of this computer's own (`refused`): its interfaces, and the public
//! address the relay saw its link come from (its router's, behind NAT). The address checked is the one dialled, so a
//! name that resolves differently a second time changes nothing.

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
/// One bot's proxy takes this many connections at once (a browser opens a few dozen).
const PROXY_CONNECTIONS: usize = 256;
/// How long a browser has to send its request head.
const HEAD_TIME: Duration = Duration::from_secs(10);
const LINK_BACKOFF_MAX: Duration = Duration::from_secs(60);
/// The bot's proxy ports are picked from here first, by bot, so a browser left running finds its proxy again after
/// the runner restarts. Below the usual ephemeral ranges (Linux 32768+, macOS 49152+).
const PORT_BASE: u16 = 21000;
const PORT_SPAN: u16 = 8000;

// ------------------------------------------------------------------------------------------ addresses

/// What the special-purpose registries say about a prefix: refuse it, or look at the IPv4 address it carries.
#[derive(Clone, Copy)]
enum Rule {
    Refuse(&'static str),
    /// NAT64 (64:ff9b::/96) reaches the IPv4 address in its last 32 bits.
    Nat64,
    /// 6to4 (2002::/16) reaches the IPv4 address in bits 16 to 48.
    SixToFour,
}

/// IPv4 prefixes that are not on the public internet: the IANA IPv4 Special-Purpose Address Registry (only the
/// entries marked not globally reachable; 192.0.0.0/24 whole, as its global anycast entries are no websites) and
/// multicast. First match wins.
const V4: &[([u8; 4], u8, &str)] = &[
    ([0, 0, 0, 0], 8, "an unspecified address"),
    ([10, 0, 0, 0], 8, "a private network address"),
    ([100, 64, 0, 0], 10, "a carrier-grade NAT address"),
    ([127, 0, 0, 0], 8, "a loopback address"),
    ([169, 254, 0, 0], 16, "a link-local address"),
    ([172, 16, 0, 0], 12, "a private network address"),
    ([192, 0, 0, 0], 24, "a protocol assignment address"),
    ([192, 0, 2, 0], 24, "a documentation address"),
    ([192, 88, 99, 0], 24, "a 6to4 relay address"),
    ([192, 168, 0, 0], 16, "a private network address"),
    ([198, 18, 0, 0], 15, "a benchmarking address"),
    ([198, 51, 100, 0], 24, "a documentation address"),
    ([203, 0, 113, 0], 24, "a documentation address"),
    ([224, 0, 0, 0], 4, "a multicast address"),
    ([240, 0, 0, 0], 4, "a reserved address"),
];

/// IPv6 prefixes from the IANA IPv6 Special-Purpose Address Registry (not globally reachable ones; 2001::/23 whole,
/// as its global entries are anycast infrastructure), the transition prefixes whose IPv4 address is checked instead,
/// and everything outside 2000::/3, the only space allocated for global unicast. First match wins.
const V6: &[([u16; 8], u8, Rule)] = &[
    ([0, 0, 0, 0, 0, 0, 0, 0], 128, Rule::Refuse("an unspecified address")),
    ([0, 0, 0, 0, 0, 0, 0, 1], 128, Rule::Refuse("a loopback address")),
    ([0x64, 0xff9b, 0, 0, 0, 0, 0, 0], 96, Rule::Nat64),
    ([0x64, 0xff9b, 1, 0, 0, 0, 0, 0], 48, Rule::Refuse("a local NAT64 address")),
    ([0x100, 0, 0, 0, 0, 0, 0, 0], 64, Rule::Refuse("a discard address")),
    ([0x2001, 0, 0, 0, 0, 0, 0, 0], 32, Rule::Refuse("a Teredo address")),
    ([0x2001, 0xdb8, 0, 0, 0, 0, 0, 0], 32, Rule::Refuse("a documentation address")),
    ([0x2001, 0, 0, 0, 0, 0, 0, 0], 23, Rule::Refuse("a protocol assignment address")),
    ([0x2002, 0, 0, 0, 0, 0, 0, 0], 16, Rule::SixToFour),
    ([0x3fff, 0, 0, 0, 0, 0, 0, 0], 20, Rule::Refuse("a documentation address")),
    ([0xfc00, 0, 0, 0, 0, 0, 0, 0], 7, Rule::Refuse("a unique local address")),
    ([0xfe80, 0, 0, 0, 0, 0, 0, 0], 10, Rule::Refuse("a link-local address")),
    ([0xfec0, 0, 0, 0, 0, 0, 0, 0], 10, Rule::Refuse("a site-local address")),
    ([0xff00, 0, 0, 0, 0, 0, 0, 0], 8, Rule::Refuse("a multicast address")),
    ([0x2000, 0, 0, 0, 0, 0, 0, 0], 3, Rule::Refuse("")), // global unicast: no rule, see refused_v6
];

fn in_prefix(value: u128, base: u128, len: u8, bits: u32) -> bool {
    let mask = if len == 0 { 0 } else { u128::MAX << (bits - len as u32) };
    value & mask == base & mask
}

/// Why an exit refuses to dial `ip`, or None for an address on the public internet. `deny` is this computer's own
/// addresses: its interfaces and the public address the relay saw it connect from. A service on any of them is
/// still the computer's own (or its router's), whatever the registry says.
pub fn refused(ip: IpAddr, deny: &[IpAddr]) -> Option<&'static str> {
    let ip = canonical(ip);
    if deny.iter().any(|o| canonical(*o) == ip) {
        return Some("this computer's own address");
    }
    match ip {
        IpAddr::V4(v4) => refused_v4(v4),
        IpAddr::V6(v6) => refused_v6(v6, deny),
    }
}

fn canonical(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        IpAddr::V4(_) => ip,
    }
}

fn refused_v4(ip: Ipv4Addr) -> Option<&'static str> {
    let value = u32::from(ip) as u128;
    V4.iter().find(|(base, len, _)| in_prefix(value, u32::from(Ipv4Addr::from(*base)) as u128, *len, 32)).map(|r| r.2)
}

fn v6_rule(ip: Ipv6Addr) -> Option<&'static ([u16; 8], u8, Rule)> {
    let value = u128::from(ip);
    V6.iter().find(|(base, len, _)| in_prefix(value, u128::from(Ipv6Addr::from(*base)), *len, 128))
}

/// The IPv4 address a connection to `ip` reaches: itself, or the one an IPv4-mapped, NAT64 (64:ff9b::/96) or 6to4
/// address carries. None for an IPv6 address that reaches IPv6.
fn carried_v4(ip: IpAddr) -> Option<Ipv4Addr> {
    let v6 = match canonical(ip) {
        IpAddr::V4(v4) => return Some(v4),
        IpAddr::V6(v6) => v6,
    };
    let value = u128::from(v6);
    match v6_rule(v6).map(|r| r.2) {
        Some(Rule::Nat64) => Some(Ipv4Addr::from(value as u32)),
        Some(Rule::SixToFour) => Some(Ipv4Addr::from((value >> 80) as u32)),
        _ => None,
    }
}

fn refused_v6(ip: Ipv6Addr, deny: &[IpAddr]) -> Option<&'static str> {
    match v6_rule(ip).map(|r| (r.1, r.2)) {
        // A transition address is the IPv4 address it reaches: every IPv4 rule and this computer's own apply.
        Some((_, Rule::Nat64 | Rule::SixToFour)) => refused(IpAddr::V4(carried_v4(IpAddr::V6(ip))?), deny),
        Some((3, _)) => None,
        Some((_, Rule::Refuse(why))) => Some(why),
        None => Some("an unallocated address"),
    }
}

/// How many public addresses the relay has seen this computer connect from are kept: IPv4 and IPv6, or a laptop
/// that moves between networks.
const SEEN_KEPT: usize = 8;

fn seen() -> &'static Mutex<Vec<IpAddr>> {
    static SEEN: OnceLock<Mutex<Vec<IpAddr>>> = OnceLock::new();
    SEEN.get_or_init(|| Mutex::new(Vec::new()))
}

/// Remember the addresses the relay saw this computer's link come from, newest first. Behind NAT that is the
/// router's public address, which no interface here has: a name resolving to it would reach the router's own pages or
/// a forwarded service at home.
fn remember_seen(addresses: &[IpAddr]) {
    let mut kept = seen().lock().unwrap();
    for ip in addresses.iter().rev().map(|ip| canonical(*ip)) {
        kept.retain(|k| *k != ip);
        kept.insert(0, ip);
    }
    kept.truncate(SEEN_KEPT);
}

/// The relay's first message on the link: `{"op":"hello","seen":["203.0.113.9"]}`.
fn hello_seen(text: &str) -> Option<Vec<IpAddr>> {
    let v: Value = serde_json::from_str(text).ok()?;
    (v["op"] == "hello").then(|| {
        v["seen"].as_array().into_iter().flatten().take(4).filter_map(|ip| ip.as_str()?.parse().ok()).collect()
    })
}

/// How often the exit asks the relay again which public addresses it connects from: a laptop changes networks.
const PROBE_EVERY: Duration = Duration::from_secs(300);
const SEEN_PATH: &str = "/api/v2/runners/me/browser-exit/seen";

/// Whether IPv4 sites are refused: the relay has not told this computer a public IPv4 address it connects from (the
/// router's, behind NAT), which a name could resolve to. Refused until a probe over IPv4 learns one, whatever the routes
/// say: an IPv6-only computer can still reach IPv4 through NAT64, so it reaches no IPv4 site at all.
static V4_UNKNOWN: AtomicBool = AtomicBool::new(true);

/// Applies to every address that reaches IPv4 (carried_v4): a NAT64 or 6to4 address reaches the same router.
fn ipv4_unknown(ip: IpAddr) -> Option<&'static str> {
    (carried_v4(ip).is_some() && V4_UNKNOWN.load(Ordering::SeqCst))
        .then_some("an IPv4 address, refused while this computer's public IPv4 address is unknown (the relay has not answered over IPv4)")
}

/// Ask the relay, once over IPv4 and once over IPv6 (the hub name's A and AAAA records, dialled separately), which
/// address each connection came from, and refuse those like this computer's own. The link alone shows only the family
/// it happened to use. Without IPv6 the IPv4 answer is enough: IPv6 hosts are rarely behind NAT, and their interface
/// addresses are refused already.
async fn probe_seen(client: &Client) {
    let learned = probe_families(client).await;
    V4_UNKNOWN.store(!learned, Ordering::SeqCst);
}

/// Whether a public IPv4 address was learned.
async fn probe_families(client: &Client) -> bool {
    let Ok(url) = reqwest::Url::parse(&client.url) else { return false };
    let (Some(host), Some(port)) = (url.host_str(), url.port_or_known_default()) else { return false };
    let host = host.trim_start_matches('[').trim_end_matches(']').to_string();
    let Ok(token) = bearer(&client.token) else { return false };
    let addrs: Vec<SocketAddr> = match tokio::time::timeout(DIAL, tokio::net::lookup_host((host.as_str(), port))).await
    {
        Ok(Ok(found)) => found.collect(),
        _ => return false,
    };
    let endpoint = format!("{}{SEEN_PATH}", client.url.trim_end_matches('/'));
    let mut learned = false;
    for ipv4 in [true, false] {
        let Some(addr) = addrs.iter().find(|a| a.is_ipv4() == ipv4) else { continue };
        // Directly, as the link and the sites are dialled: through a proxy the relay would see the proxy's address.
        let Ok(http) = crate::tls::http_builder()
            .no_proxy()
            .resolve(&host, *addr)
            .timeout(DIAL)
            .redirect(reqwest::redirect::Policy::none())
            .user_agent(format!("Tico-Client/{CLIENT_VERSION}"))
            .build()
        else {
            continue;
        };
        let answer = match http.get(&endpoint).bearer_auth(&token).send().await {
            Ok(r) if r.status().is_success() => r.json::<Value>().await.unwrap_or_default(),
            _ => continue,
        };
        if let Some(ip) = answer["seen"].as_str().and_then(|ip| ip.parse::<IpAddr>().ok()) {
            remember_seen(&[ip]);
            // Only a public IPv4 address is the one a site could reach this computer at. A private one (a hub
            // reached over the home network) leaves the router's public address unknown.
            learned |= matches!(canonical(ip), IpAddr::V4(v4) if refused_v4(v4).is_none());
        }
    }
    learned
}

/// Every address this computer refuses to dial as its own: its interfaces and what the relay saw.
fn own_and_seen() -> Vec<IpAddr> {
    let mut deny = own_addresses();
    deny.extend(seen().lock().unwrap().iter().copied());
    deny
}

/// A network this computer is on: an interface's address and prefix length, or a gateway's address (full length).
#[derive(Clone, Copy, Debug, PartialEq)]
struct Net {
    addr: IpAddr,
    len: u8,
}

/// One interface address as getifaddrs reports it: the address, its netmask's length, and the far end of a
/// point-to-point link.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Iface {
    addr: IpAddr,
    len: u8,
    peer: Option<IpAddr>,
}

impl Net {
    fn exact(addr: IpAddr) -> Net {
        Net { addr, len: if addr.is_ipv4() { 32 } else { 128 } }
    }

    /// The on-link scope of an interface address. IPv4: its network when that is /16 or narrower; a wider one is a
    /// tunnel's or a default scope, so only the address itself (private home networks are refused by the registry
    /// anyway). IPv6: a global address's /64 whatever the netmask says, since a home's devices and router hold public
    /// addresses in it; any other IPv6 address only itself.
    fn interface(addr: IpAddr, len: u8) -> Net {
        match addr {
            IpAddr::V4(_) if (16..=32).contains(&len) => Net { addr, len },
            IpAddr::V6(v6) if v6.segments()[0] & 0xe000 == 0x2000 => Net { addr, len: 64 },
            _ => Net::exact(addr),
        }
    }

    fn contains(&self, ip: IpAddr) -> bool {
        match (self.addr, ip) {
            (IpAddr::V4(net), IpAddr::V4(ip)) => in_prefix(u32::from(ip) as u128, u32::from(net) as u128, self.len, 32),
            (IpAddr::V6(net), IpAddr::V6(ip)) => in_prefix(u128::from(ip), u128::from(net), self.len, 128),
            _ => false,
        }
    }
}

/// Why `ip` is refused as a neighbour: inside a network one of this computer's interfaces is on (a device at home,
/// the router's global IPv6 address), a point-to-point peer, or a default gateway. The IPv4 address a NAT64 or 6to4
/// address reaches counts too.
fn on_link(ip: IpAddr, nets: &[Net]) -> Option<&'static str> {
    let ip = canonical(ip);
    let targets = [Some(ip), carried_v4(ip).map(IpAddr::V4)];
    targets
        .into_iter()
        .flatten()
        .any(|t| nets.iter().any(|n| n.contains(t)))
        .then_some("on this computer's own network")
}

/// The networks an interface list and gateway list make: each interface's on-link scope, each point-to-point peer
/// and each gateway, exactly.
fn networks(ifaces: &[Iface], gateways: &[IpAddr]) -> Vec<Net> {
    let mut nets = Vec::new();
    for iface in ifaces {
        nets.push(Net::interface(canonical(iface.addr), iface.len));
        nets.extend(iface.peer.map(|peer| Net::exact(canonical(peer))));
    }
    nets.extend(gateways.iter().map(|gw| Net::exact(canonical(*gw))));
    nets
}

/// The networks this computer is on, read at every dial so a laptop that changes networks is never judged by the
/// old one (macOS gateways are at most a minute old).
async fn own_networks() -> Vec<Net> {
    networks(&interfaces(), &gateways().await)
}

/// The default gateways: from the kernel's routing tables on Linux, from `route` on macOS.
async fn gateways() -> Vec<IpAddr> {
    let mut out = Vec::new();
    if let Ok(table) = std::fs::read_to_string("/proc/net/route") {
        out.extend(gateways_v4(&table));
    }
    if let Ok(table) = std::fs::read_to_string("/proc/net/ipv6_route") {
        out.extend(gateways_v6(&table));
    }
    if cfg!(target_os = "macos") {
        out.extend(mac_gateways().await);
    }
    out
}

/// How long macOS gateways are kept, so a dial doesn't start a process each time.
const GATEWAYS_KEPT: Duration = Duration::from_secs(60);

fn mac_gateways_cache() -> &'static Mutex<Option<(Instant, Vec<IpAddr>)>> {
    static CACHE: OnceLock<Mutex<Option<(Instant, Vec<IpAddr>)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(None))
}

/// macOS default gateways from `/sbin/route` (absolute path, no shell, empty environment), kept for a minute.
async fn mac_gateways() -> Vec<IpAddr> {
    if let Some((at, gws)) = mac_gateways_cache().lock().unwrap().as_ref() {
        if at.elapsed() < GATEWAYS_KEPT {
            return gws.clone();
        }
    }
    let mut gws = Vec::new();
    for args in [&["-n", "get", "default"][..], &["-n", "get", "-inet6", "default"][..]] {
        let run = tokio::process::Command::new("/sbin/route").args(args).env_clear().kill_on_drop(true).output();
        if let Ok(Ok(out)) = tokio::time::timeout(Duration::from_secs(2), run).await {
            gws.extend(route_gateway(&String::from_utf8_lossy(&out.stdout)));
        }
    }
    *mac_gateways_cache().lock().unwrap() = Some((Instant::now(), gws.clone()));
    gws
}

/// The `gateway:` line of `route -n get` output, when it is an address (not `link#N`); an IPv6 zone is dropped.
fn route_gateway(out: &str) -> Option<IpAddr> {
    let value = out.lines().find_map(|line| line.trim().strip_prefix("gateway:"))?.trim();
    value.split('%').next()?.parse().ok()
}

/// /proc/net/route: `Iface Destination Gateway ...`, each address its network-order bytes printed as a native integer.
fn gateways_v4(table: &str) -> Vec<IpAddr> {
    table
        .lines()
        .skip(1)
        .filter_map(|line| u32::from_str_radix(line.split_whitespace().nth(2)?, 16).ok())
        .filter(|gw| *gw != 0)
        .map(|gw| IpAddr::V4(Ipv4Addr::from(gw.to_ne_bytes())))
        .collect()
}

/// /proc/net/ipv6_route: destination, its length, source, its length, next hop (32 hex digits each but the lengths).
fn gateways_v6(table: &str) -> Vec<IpAddr> {
    table
        .lines()
        .filter_map(|line| u128::from_str_radix(line.split_whitespace().nth(4)?, 16).ok())
        .filter(|hop| *hop != 0)
        .map(|hop| IpAddr::V6(Ipv6Addr::from(hop)))
        .collect()
}

/// This computer's interface addresses.
pub fn own_addresses() -> Vec<IpAddr> {
    interfaces().into_iter().map(|iface| iface.addr).collect()
}

/// An IPv4 or IPv6 socket address as an IP, or None for another family or a null pointer.
///
/// SAFETY: `sa` is null or points at a sockaddr getifaddrs filled in, at least as long as its family says.
unsafe fn sockaddr_ip(sa: *const libc::sockaddr) -> Option<IpAddr> {
    if sa.is_null() {
        return None;
    }
    match (*sa).sa_family as i32 {
        libc::AF_INET => {
            Some(IpAddr::V4(Ipv4Addr::from(u32::from_be((*(sa as *const libc::sockaddr_in)).sin_addr.s_addr))))
        }
        libc::AF_INET6 => Some(IpAddr::V6(Ipv6Addr::from((*(sa as *const libc::sockaddr_in6)).sin6_addr.s6_addr))),
        _ => None,
    }
}

/// Each interface address with its prefix length (from its netmask; full length when there is none) and, on a
/// point-to-point link, the peer's address.
fn interfaces() -> Vec<Iface> {
    let mut out = Vec::new();
    // SAFETY: getifaddrs hands back a list this function walks read-only and frees once.
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 {
            return out;
        }
        let mut cur = head;
        while !cur.is_null() {
            if let Some(addr) = sockaddr_ip((*cur).ifa_addr) {
                let len = match sockaddr_ip((*cur).ifa_netmask) {
                    Some(IpAddr::V4(mask)) => u32::from(mask).count_ones() as u8,
                    Some(IpAddr::V6(mask)) => u128::from(mask).count_ones() as u8,
                    None if addr.is_ipv4() => 32,
                    None => 128,
                };
                #[cfg(any(target_os = "linux", target_os = "android"))]
                let dst = (*cur).ifa_ifu;
                #[cfg(not(any(target_os = "linux", target_os = "android")))]
                let dst = (*cur).ifa_dstaddr;
                let p2p = (*cur).ifa_flags & libc::IFF_POINTOPOINT as libc::c_uint != 0;
                let peer = if p2p { sockaddr_ip(dst) } else { None };
                out.push(Iface { addr, len, peer });
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
    let (deny, nets) = (own_and_seen(), own_networks().await);
    let mut why = None;
    let allowed: Vec<SocketAddr> = resolved
        .into_iter()
        .filter(|addr| {
            match refused(addr.ip(), &deny).or_else(|| on_link(addr.ip(), &nets)).or_else(|| ipv4_unknown(addr.ip())) {
                Some(reason) => {
                    why.get_or_insert(reason);
                    false
                }
                None => true,
            }
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
        probe_seen(&client).await;
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
    let mut probe = tokio::time::interval_at(tokio::time::Instant::now() + PROBE_EVERY, PROBE_EVERY);
    loop {
        tokio::select! {
            _ = probe.tick() => {
                let client = client.clone();
                tokio::spawn(async move { probe_seen(&client).await });
            }
            frame = ws.next() => match frame {
                Some(Ok(Message::Text(text))) => {
                    if let Some(addresses) = hello_seen(&text) {
                        remember_seen(&addresses);
                        continue;
                    }
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

/// One bot's proxy: its port, what its last tunnel came to (the proxy's own page says it), and who may use it.
struct Proxy {
    port: u16,
    exit: Mutex<String>,
    last_error: Mutex<Option<String>>,
    /// Under isolation (crate::isolation), the OS users whose connections it takes: a leased turn user while a turn
    /// of this bot runs as it (counted per turn), the shared bot user for good once a turn runs as that (every bot
    /// running as it is one trust group). Without isolation every process is the runner's own user and none is
    /// checked.
    users: Mutex<HashMap<u32, usize>>,
    /// Connections open at once; the accept loop waits for room before taking another.
    room: Arc<Semaphore>,
    task: Mutex<Option<tokio::task::AbortHandle>>,
    /// When a turn last took it: the heartbeat's sweep stops only a proxy no turn has taken since that heartbeat began.
    used: Mutex<Instant>,
}

const ALWAYS: usize = usize::MAX;

impl Proxy {
    fn new(port: u16, exit: String) -> Proxy {
        Proxy {
            port,
            exit: Mutex::new(exit),
            last_error: Mutex::new(None),
            users: Mutex::new(HashMap::new()),
            room: Arc::new(Semaphore::new(PROXY_CONNECTIONS)),
            task: Mutex::new(None),
            used: Mutex::new(Instant::now()),
        }
    }

    fn note(&self, bot: &str, error: Option<String>) {
        let mut last = self.last_error.lock().unwrap();
        if let Some(e) = error.as_ref().filter(|e| last.as_ref() != Some(*e)) {
            log(&format!("Tico runner: {bot}'s browser exit refused a connection: {e}"));
        }
        *last = error;
    }

    /// Whether a connection from `peer_uid` may use this proxy.
    fn admits(&self, peer_uid: Option<u32>, isolation: bool) -> bool {
        !isolation || peer_uid.is_some_and(|uid| self.users.lock().unwrap().get(&uid).is_some_and(|n| *n > 0))
    }
}

/// A turn's hold on its bot's proxy: the URL its browser goes through, and the OS users it lets in until the turn
/// ends (env.rs keeps it in the turn's vault).
pub struct Admission {
    proxy: Arc<Proxy>,
    leased: Vec<u32>,
    pub url: String,
}

impl Admission {
    /// Let `uid`, the user this turn's processes run as, use the proxy while the turn lasts.
    pub fn allow(&mut self, uid: u32) {
        let mut users = self.proxy.users.lock().unwrap();
        let n = users.entry(uid).or_insert(0);
        if *n != ALWAYS {
            *n += 1;
            self.leased.push(uid);
        }
    }

    /// The shared bot user: every bot running as it can already reach the others' files, so it stays let in.
    pub fn allow_always(&mut self, uid: u32) {
        self.proxy.users.lock().unwrap().insert(uid, ALWAYS);
    }
}

impl Drop for Admission {
    fn drop(&mut self) {
        let mut users = self.proxy.users.lock().unwrap();
        for uid in self.leased.drain(..) {
            if let Some(n) = users.get_mut(&uid).filter(|n| **n != ALWAYS) {
                *n -= 1;
                if *n == 0 {
                    users.remove(&uid);
                }
            }
        }
    }
}

fn proxies() -> &'static Mutex<HashMap<String, Arc<Proxy>>> {
    static PROXIES: OnceLock<Mutex<HashMap<String, Arc<Proxy>>>> = OnceLock::new();
    PROXIES.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Stop a bot's proxy (`only`: if it is still that one): it has no exit any more. Connections already open end when
/// the relay closes their tunnels.
fn drop_proxy(bot: &str, only: Option<&Arc<Proxy>>) {
    let mut all = proxies().lock().unwrap();
    if only.is_some_and(|p| !all.get(bot).is_some_and(|kept| Arc::ptr_eq(kept, p))) {
        return;
    }
    if let Some(proxy) = all.remove(bot) {
        if let Some(task) = proxy.task.lock().unwrap().take() {
            task.abort();
        }
    }
}

/// Stop the proxy of every bot the heartbeat doesn't name as having an exit here: its exit was cleared, or it moved to
/// another computer, between turns. `asked` is when that heartbeat was sent: a proxy a turn has taken since then came
/// from a claim newer than the answer, and stays.
pub fn keep_proxies(bots: &[String], asked: Instant) {
    let gone = stale_proxies(&proxies().lock().unwrap(), bots, asked);
    for (bot, proxy) in gone {
        drop_proxy(&bot, Some(&proxy));
    }
}

fn stale_proxies(all: &HashMap<String, Arc<Proxy>>, bots: &[String], asked: Instant) -> Vec<(String, Arc<Proxy>)> {
    all.iter()
        .filter(|(bot, proxy)| *proxy.used.lock().unwrap() < asked && !bots.contains(bot))
        .map(|(bot, proxy)| (bot.clone(), proxy.clone()))
        .collect()
}

fn preferred_port(bot: &str) -> u16 {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(bot.as_bytes());
    PORT_BASE + u16::from_be_bytes([digest[0], digest[1]]) % PORT_SPAN
}

/// The proxy a turn's browser goes through, when the turn names a browser exit (`attempt.browser_exit`, from
/// backend/browser_exit.py): None when it names none (a claimed turn's null stops the bot's proxy). A named exit whose
/// proxy can't be set up is an error, and the turn fails: it never runs without the proxy. The proxy outlives the
/// turn: a browser the bot left open keeps using it.
pub async fn turn_proxy(bot: &str, attempt: &Value) -> Result<Option<Admission>, String> {
    let exit = match attempt.get("browser_exit") {
        Some(exit) if exit.is_object() => exit,
        // A claimed turn always says (null: none, so a proxy left from an earlier exit stops); a watcher's run says
        // nothing and leaves the proxy alone.
        Some(_) => {
            drop_proxy(bot, None);
            return Ok(None);
        }
        None => return Ok(None),
    };
    let name = Some(text_of(&exit["name"])).filter(|n| !n.is_empty()).unwrap_or_else(|| "its exit computer".into());
    let failed = |why: String| format!("This bot browses through {name}, but its browser proxy could not start: {why}");
    let client = CLIENT.get().ok_or_else(|| failed("the runner is still starting".into()))?.clone();
    let existing = proxies().lock().unwrap().get(bot).cloned();
    let proxy = match existing {
        Some(p) => p,
        None => {
            let listener = match TcpListener::bind(("127.0.0.1", preferred_port(bot))).await {
                Ok(l) => l,
                Err(_) => TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| failed(e.to_string()))?,
            };
            let port = listener.local_addr().map_err(|e| failed(e.to_string()))?.port();
            let proxy = Arc::new(Proxy::new(port, name.clone()));
            let kept = proxies().lock().unwrap().entry(bot.to_string()).or_insert_with(|| proxy.clone()).clone();
            if Arc::ptr_eq(&kept, &proxy) {
                let task = tokio::spawn(serve_proxy(listener, bot.to_string(), proxy.clone(), client));
                *proxy.task.lock().unwrap() = Some(task.abort_handle());
            }
            kept
        }
    };
    *proxy.exit.lock().unwrap() = name;
    *proxy.used.lock().unwrap() = Instant::now();
    let url = format!("http://127.0.0.1:{}", proxy.port);
    Ok(Some(Admission { proxy, leased: Vec::new(), url }))
}

async fn serve_proxy(listener: TcpListener, bot: String, proxy: Arc<Proxy>, client: Client) {
    loop {
        let Ok(permit) = proxy.room.clone().acquire_owned().await else { return };
        let Ok((conn, peer)) = listener.accept().await else {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        };
        let local = conn.local_addr().ok();
        let (bot, proxy, client) = (bot.clone(), proxy.clone(), client.clone());
        tokio::spawn(async move {
            let isolation = crate::isolation::enabled();
            let uid = if isolation { local.and_then(|local| peer_uid(local, peer)) } else { None };
            if proxy.admits(uid, isolation) {
                handle(conn, bot, proxy, client).await;
            } else {
                let mut conn = conn;
                reply(&mut conn, 403, &format!("This is {bot}'s browser exit; only its own turns may use it")).await;
            }
            drop(permit);
        });
    }
}

/// The OS user that owns the other end of a loopback TCP connection: the line in /proc/net/tcp whose local end is
/// the peer and whose remote end is this listener. TCP sockets carry no SO_PEERCRED, but the kernel lists each
/// socket's owner there. None off Linux (isolation is Linux only) or when it can't be found.
fn peer_uid(local: SocketAddr, peer: SocketAddr) -> Option<u32> {
    if !cfg!(target_os = "linux") {
        return None;
    }
    let table = std::fs::read_to_string("/proc/net/tcp").ok()?;
    uid_in_table(&table, local, peer)
}

fn uid_in_table(table: &str, local: SocketAddr, peer: SocketAddr) -> Option<u32> {
    let hex = |addr: SocketAddr| match addr.ip() {
        IpAddr::V4(v4) => Some(format!("{:08X}:{:04X}", u32::from_ne_bytes(v4.octets()), addr.port())),
        IpAddr::V6(_) => None,
    };
    let (theirs, ours) = (hex(peer)?, hex(local)?);
    table.lines().skip(1).find_map(|line| {
        let fields: Vec<&str> = line.split_whitespace().collect();
        (fields.len() > 7 && fields[1] == theirs && fields[2] == ours).then(|| fields[7].parse().ok()).flatten()
    })
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
    let Ok(Some((head, rest))) = tokio::time::timeout(HEAD_TIME, read_head(&mut conn)).await else {
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
        Err((words, gone)) => {
            proxy.note(&bot, Some(words.clone()));
            reply(&mut conn, 502, &words).await;
            if gone {
                drop_proxy(&bot, Some(&proxy)); // the hub says the bot has no exit here any more
            }
            return;
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
/// The error says, too, whether the hub said the bot has no exit on this computer any more (its proxy then stops).
async fn open_tunnel(
    client: &Client,
    bot: &str,
    host: &str,
    port: u16,
    proxy: &Proxy,
) -> Result<Socket, (String, bool)> {
    let mut once = client.clone();
    once.retries = 0; // a refusal is the answer, and the browser is waiting
    let grant = once
        .post("runners/me/browser-exit/tunnels", &json!({"bot": bot, "host": host, "port": port}), None)
        .await
        .map_err(|e| {
            let gone = e.code == "browser_exit_none" || e.detail == "This bot is not on this computer";
            // The hub's own refusals already name the exit; anything else (the hub unreachable) gets the name here.
            if e.code.starts_with("browser_exit") {
                (e.detail, gone)
            } else {
                (format!("can't reach {} through the hub: {}", proxy.exit.lock().unwrap(), e.detail), gone)
            }
        })?;
    open_granted(client, host, proxy, &grant).await.map_err(|words| (words, false))
}

async fn open_granted(client: &Client, host: &str, proxy: &Proxy, grant: &Value) -> Result<Socket, String> {
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
        // 93.184.215.7 an interface's public address, 81.2.69.160 what the relay saw (the router), one IPv6 too.
        let deny: Vec<IpAddr> =
            ["93.184.215.7", "81.2.69.160", "2a00:1450:4001::7"].iter().map(|ip| ip.parse().unwrap()).collect();
        for (ip, why) in [
            ("0.0.0.0", "an unspecified address"),
            ("0.1.2.3", "an unspecified address"),
            ("10.1.2.3", "a private network address"),
            ("100.64.0.1", "a carrier-grade NAT address"),
            ("100.127.255.255", "a carrier-grade NAT address"),
            ("127.0.0.1", "a loopback address"),
            ("169.254.169.254", "a link-local address"),
            ("172.16.0.1", "a private network address"),
            ("172.31.255.255", "a private network address"),
            ("192.0.0.9", "a protocol assignment address"),
            ("192.0.0.170", "a protocol assignment address"),
            ("192.0.2.1", "a documentation address"),
            ("192.88.99.1", "a 6to4 relay address"),
            ("192.168.1.1", "a private network address"),
            ("198.18.0.1", "a benchmarking address"),
            ("198.19.255.255", "a benchmarking address"),
            ("198.51.100.7", "a documentation address"),
            ("203.0.113.7", "a documentation address"),
            ("224.0.0.1", "a multicast address"),
            ("239.255.255.250", "a multicast address"),
            ("240.0.0.1", "a reserved address"),
            ("255.255.255.255", "a reserved address"),
            ("93.184.215.7", "this computer's own address"),
            ("81.2.69.160", "this computer's own address"),
            ("::", "an unspecified address"),
            ("::1", "a loopback address"),
            ("::a00:1", "an unallocated address"),
            ("::ffff:10.0.0.1", "a private network address"),
            ("::ffff:81.2.69.160", "this computer's own address"),
            ("64:ff9b::a00:1", "a private network address"),
            ("64:ff9b::5102:45a0", "this computer's own address"),
            ("64:ff9b:1::5db8:d70e", "a local NAT64 address"),
            ("100::1", "a discard address"),
            ("2001::1", "a Teredo address"),
            ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "a Teredo address"),
            ("2001:2::1", "a protocol assignment address"),
            ("2001:db8::1", "a documentation address"),
            ("2002:c0a8:101::1", "a private network address"),
            ("2002:5102:45a0::1", "this computer's own address"),
            ("2002:c612:1::1", "a benchmarking address"),
            ("3fff::1", "a documentation address"),
            ("2a00:1450:4001::7", "this computer's own address"),
            ("5f00::1", "an unallocated address"),
            ("fc00::1", "a unique local address"),
            ("fd00::1", "a unique local address"),
            ("fe80::1", "a link-local address"),
            ("fec0::1", "a site-local address"),
            ("ff02::1", "a multicast address"),
        ] {
            assert_eq!(refused(ip.parse().unwrap(), &deny), Some(why), "{ip}");
        }
        for ip in [
            "93.184.215.14",
            "100.128.0.1",
            "172.32.0.1",
            "192.0.1.1",
            "198.20.0.1",
            "2606:4700::1111",
            "2001:4860:4860::8888",
            "64:ff9b::5db8:d70e",
            "2002:5db8:d70e::1",
        ] {
            assert_eq!(refused(ip.parse().unwrap(), &deny), None, "{ip}");
        }
    }

    #[test]
    fn the_address_the_relay_saw_is_refused_too_newest_first_and_kept_small() {
        assert_eq!(hello_seen(r#"{"op":"open"}"#), None);
        let heard = hello_seen(r#"{"op":"hello","seen":["81.2.69.161","::ffff:81.2.69.162","nonsense"]}"#).unwrap();
        assert_eq!(heard.len(), 2);
        remember_seen(&heard);
        let deny = own_and_seen();
        assert_eq!(refused("81.2.69.161".parse().unwrap(), &deny), Some("this computer's own address"));
        assert_eq!(refused("81.2.69.162".parse().unwrap(), &deny), Some("this computer's own address"));
        for n in 0..20u8 {
            remember_seen(&[IpAddr::V4(Ipv4Addr::new(81, 2, 70, n))]);
        }
        let kept = seen().lock().unwrap().clone();
        assert_eq!(kept.len(), SEEN_KEPT);
        assert_eq!(kept[0], IpAddr::V4(Ipv4Addr::new(81, 2, 70, 19)));
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
        let proxy = Arc::new(Proxy::new(0, "Cara's MacBook".into()));
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

    #[test]
    fn under_isolation_only_the_turns_own_users_get_in_and_only_while_the_turn_lasts() {
        let proxy = Arc::new(Proxy::new(0, "Cara's MacBook".into()));
        assert!(proxy.admits(None, false), "without isolation every process is the runner's user");
        assert!(!proxy.admits(Some(10100), true));
        assert!(!proxy.admits(None, true), "an owner that can't be found is refused");
        let mut first = Admission { proxy: proxy.clone(), leased: Vec::new(), url: String::new() };
        let mut second = Admission { proxy: proxy.clone(), leased: Vec::new(), url: String::new() };
        first.allow(10100);
        second.allow(10100);
        second.allow_always(1001);
        assert!(proxy.admits(Some(10100), true) && proxy.admits(Some(1001), true));
        assert!(!proxy.admits(Some(10101), true), "another turn user is another bot's");
        drop(first);
        assert!(proxy.admits(Some(10100), true), "the second turn still runs as it");
        drop(second);
        assert!(!proxy.admits(Some(10100), true));
        assert!(proxy.admits(Some(1001), true), "the shared bot user stays");
    }

    #[test]
    fn the_peer_is_the_line_whose_local_end_is_the_browser_and_remote_end_the_proxy() {
        // /proc/net/tcp on a little-endian machine: 127.0.0.1:47100 (proxy) and 127.0.0.1:51000 (browser).
        let table = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:B7FC 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1 1 0000000000000000 100 0 0 10 0
   1: 0100007F:B7FC 0100007F:C738 01 00000000:00000000 00:00000000 00000000  1000        0 2 1 0000000000000000 20 4 30 10 -1
   2: 0100007F:C738 0100007F:B7FC 01 00000000:00000000 00:00000000 00000000 10100        0 3 1 0000000000000000 20 4 30 10 -1
";
        let proxy: SocketAddr = "127.0.0.1:47100".parse().unwrap();
        let browser: SocketAddr = "127.0.0.1:51000".parse().unwrap();
        if cfg!(target_endian = "little") {
            assert_eq!(uid_in_table(table, proxy, browser), Some(10100));
        }
        assert_eq!(uid_in_table(table, proxy, "127.0.0.1:51001".parse().unwrap()), None);
        assert_eq!(uid_in_table(table, "[::1]:47100".parse().unwrap(), "[::1]:51000".parse().unwrap()), None);
    }

    #[tokio::test]
    async fn a_claimed_turn_without_an_exit_stops_the_proxy_and_a_watchers_run_leaves_it() {
        let proxy = Arc::new(Proxy::new(0, "Cara's MacBook".into()));
        proxies().lock().unwrap().insert("ops-watch".into(), proxy.clone());
        assert!(turn_proxy("ops-watch", &json!({"kind": "watch"})).await.unwrap().is_none());
        assert!(proxies().lock().unwrap().contains_key("ops-watch"));
        drop_proxy("ops-watch", Some(&Arc::new(Proxy::new(0, "x".into()))));
        assert!(proxies().lock().unwrap().contains_key("ops-watch"), "another proxy's refusal doesn't stop this one");
        assert!(turn_proxy("ops-watch", &json!({"browser_exit": null})).await.unwrap().is_none());
        assert!(!proxies().lock().unwrap().contains_key("ops-watch"));
    }

    #[tokio::test]
    async fn the_heartbeat_stops_the_proxies_of_bots_without_an_exit_here_and_closes_their_ports() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let moved = Arc::new(Proxy::new(port, "Cara's MacBook".into()));
        let task = tokio::spawn(async move {
            loop {
                let _ = listener.accept().await;
            }
        });
        *moved.task.lock().unwrap() = Some(task.abort_handle());
        // Chosen from a map of its own: other tests' proxies share the runner's.
        let mut all = HashMap::new();
        all.insert("gc-moved".to_string(), moved.clone());
        all.insert("gc-kept".to_string(), Arc::new(Proxy::new(0, "Cara's MacBook".into())));
        let reused = Arc::new(Proxy::new(0, "Cara's MacBook".into()));
        all.insert("gc-reused".to_string(), reused.clone());
        for proxy in all.values() {
            *proxy.used.lock().unwrap() -= Duration::from_secs(1); // taken before the heartbeat went
        }
        let asked = Instant::now();
        all.insert("gc-newer".to_string(), Arc::new(Proxy::new(0, "Cara's MacBook".into())));
        // A heartbeat that left out gc-reused was slow to answer: its exit was set again and a turn took the proxy
        // (turn_proxy) before the answer came.
        *reused.used.lock().unwrap() = Instant::now();
        let gone = stale_proxies(&all, &["gc-kept".into()], asked);
        assert_eq!(gone.iter().map(|(bot, _)| bot.as_str()).collect::<Vec<_>>(), ["gc-moved"], "a newer proxy stays");
        proxies().lock().unwrap().insert("gc-moved".into(), moved);
        for (bot, proxy) in gone {
            drop_proxy(&bot, Some(&proxy));
        }
        assert!(!proxies().lock().unwrap().contains_key("gc-moved"));
        let _ = task.await;
        assert!(tokio::net::TcpStream::connect(("127.0.0.1", port)).await.is_err(), "its port is closed");
    }

    /// scripts/browser-exit run against a stand-in browser that prints the arguments it got.
    #[cfg(unix)]
    #[test]
    fn the_browser_wrapper_gives_the_bot_its_own_profile_and_refuses_a_browser_that_would_skip_the_proxy() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        use std::process::Command;
        let root = std::env::temp_dir().join(format!("tico-exit-wrapper-{}", uuid::Uuid::new_v4()));
        let (bin, home) = (root.join("bin"), root.join("home"));
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::create_dir_all(&home).unwrap();
        let fake = bin.join("google-chrome");
        std::fs::write(&fake, "#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done\n").unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
        let shims = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../scripts/browser-exit");
        let proxy = "http://127.0.0.1:21001";
        let run_with = |args: &[&str], proxy: Option<&str>, chromium_flags: &str| {
            let mut cmd = Command::new(shims.join("google-chrome"));
            cmd.args(args)
                .env("PATH", format!("{}:{}:/usr/bin:/bin", shims.display(), bin.display()))
                .env("HOME", &home)
                .env("HUB_BOT", "ops")
                .env("CHROMIUM_FLAGS", chromium_flags)
                .env_remove("TICO_BROWSER_PROXY");
            if let Some(p) = proxy {
                cmd.env("TICO_BROWSER_PROXY", p);
            }
            let out = cmd.output().unwrap();
            (out.status.success(), String::from_utf8_lossy(&out.stdout).lines().map(String::from).collect::<Vec<_>>())
        };
        let run = |args: &[&str], proxy: Option<&str>| run_with(args, proxy, "");
        let profile = home.join(".tico/browser-exit/ops");
        let own = format!("--user-data-dir={}", profile.display());
        let flag = format!("--proxy-server={proxy}");
        let webrtc = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp";

        // The enforced switches come first, so nothing the caller passes (a bare `--` included) can follow them.
        let (ok, args) = run(&["--new-window"], Some(proxy));
        assert!(ok);
        let quiet = "--disable-features=OptimizationGuideModelDownloading,OptimizationHintsFetching,OptimizationGuideOnDeviceModel";
        assert_eq!(
            args,
            ["--tico-browser-exit=ops", flag.as_str(), webrtc, "--disable-component-update", quiet, own.as_str(), "--new-window"]
        );
        // One profile, always the bot's: naming it again is fine, any other is refused.
        let (ok, args) = run(&[&own, "--proxy-bypass-list=<-loopback>", flag.as_str()], Some(proxy));
        assert!(ok && args[5] == own, "{args:?}");
        let theirs = format!("--user-data-dir={}", root.join("kept").display());
        assert!(!run(&[&theirs], Some(proxy)).0);
        assert!(!run(&["-user-data-dir=/tmp/x", "-new-window"], Some(proxy)).0, "a single-dash profile is read too");
        assert!(!run_with(&[], Some(proxy), &theirs).0, "and one in CHROMIUM_FLAGS");
        for skip in [
            " --no-proxy-server",
            "--no-proxy-server ",
            "\t-no-proxy-server",
            "\n--no-proxy-server",
            "--",
            "--no-proxy-server",
            "-no-proxy-server",
            "--NO-PROXY-SERVER",
            "--proxy-server=socks5://10.0.0.8:1080",
            "-proxy-server=socks5://10.0.0.8:1080",
            "--proxy-pac-url=http://x/p.pac",
            "-proxy-pac-url=http://x/p.pac",
            "--proxy-auto-detect",
            "--proxy-bypass-list=*",
            "-proxy-bypass-list=*",
            "--winhttp-proxy-resolver",
            "--tico-browser-exit=ops",
        ] {
            assert!(!run(&[skip, "https://www.acme.example"], Some(proxy)).0, "{skip}");
        }
        assert!(!run_with(&[], Some(proxy), "--no-proxy-server").0, "CHROMIUM_FLAGS is checked too");
        assert!(run_with(&[], Some(proxy), &format!("{flag} {webrtc}")).0, "the runner's own CHROMIUM_FLAGS pass");
        assert!(!run(&[], None).0, "no proxy, no browser");

        // A Chrome already open on the profile takes the launch: only one this wrapper started, through this proxy and
        // with nothing that bypasses it, may.
        let host = String::from_utf8(Command::new("uname").arg("-n").output().unwrap().stdout).unwrap();
        let lock = profile.join("SingletonLock");
        let mut held = Vec::new();
        let mut holds = |args: &[&str]| {
            let mut child = Command::new("sh");
            child.args(["-c", "sleep 30; :", "sh"]).args(args);
            let child = child.spawn().unwrap();
            let _ = std::fs::remove_file(&lock);
            symlink(format!("{}-{}", host.trim(), child.id()), &lock).unwrap();
            held.push(child);
        };
        holds(&[]);
        assert!(!run(&[], Some(proxy)).0, "a browser without the proxy holds the profile");
        holds(&[flag.as_str()]);
        assert!(!run(&[], Some(proxy)).0, "the proxy but not the wrapper's marker");
        holds(&["--tico-browser-exit=ops", flag.as_str(), "-no-proxy-server"]);
        assert!(!run(&[], Some(proxy)).0, "the marker and the proxy, but bypassed");
        holds(&["--tico-browser-exit=ops", flag.as_str(), "--proxy-bypass-list=*"]);
        assert!(!run(&[], Some(proxy)).0, "the marker and the proxy, but a bypass list");
        holds(&["--tico-browser-exit=ops", flag.as_str(), webrtc]);
        assert!(run(&[], Some(proxy)).0, "a browser this wrapper started holds it");
        assert!(!run(&[], Some("http://127.0.0.1:21002")).0, "but not through another bot's proxy");
        let gone = held.last().unwrap().id();
        for mut child in held {
            let _ = child.kill();
            let _ = child.wait();
        }
        std::fs::remove_file(&lock).unwrap();
        symlink(format!("{}-{}", host.trim(), gone), &lock).unwrap();
        assert!(run(&[], Some(proxy)).0, "a lock left by a browser that exited is stale");
        let _ = std::fs::remove_dir_all(&root);
    }

    /// The wrapper's launch turns off Chrome's background model and component downloads, and a caller's own
    /// `--disable-features` is folded into the one enforced switch: Chrome keeps only the last occurrence of a switch.
    #[cfg(unix)]
    #[test]
    fn the_browser_wrapper_quiets_chromes_background_downloads_and_keeps_a_callers_disable_features() {
        use std::os::unix::fs::PermissionsExt;
        use std::process::Command;
        let root = std::env::temp_dir().join(format!("tico-exit-quiet-{}", uuid::Uuid::new_v4()));
        let (bin, home) = (root.join("bin"), root.join("home"));
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::create_dir_all(&home).unwrap();
        let fake = bin.join("google-chrome");
        std::fs::write(&fake, "#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done\n").unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
        let shims = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../scripts/browser-exit");
        let out = Command::new(shims.join("google-chrome"))
            .args(["--disable-features=Foo,Bar", "--new-window"])
            .env("PATH", format!("{}:{}:/usr/bin:/bin", shims.display(), bin.display()))
            .env("HOME", &home)
            .env("HUB_BOT", "ops")
            .env("TICO_BROWSER_PROXY", "http://127.0.0.1:21001")
            .env("CHROMIUM_FLAGS", "")
            .output()
            .unwrap();
        assert!(out.status.success());
        let args: Vec<String> = String::from_utf8_lossy(&out.stdout).lines().map(String::from).collect();
        assert!(args.iter().any(|a| a == "--disable-component-update"), "{args:?}");
        assert!(args.iter().any(|a| a == "--new-window"), "{args:?}");
        let features: Vec<&str> = args.iter().filter_map(|a| a.strip_prefix("--disable-features=")).collect();
        assert_eq!(
            features,
            ["OptimizationGuideModelDownloading,OptimizationHintsFetching,OptimizationGuideOnDeviceModel,Foo,Bar"],
            "one switch, the caller's features included: {args:?}"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    /// A stand-in hub answering the relay's `seen` with `body` (an HTTP status line and JSON).
    async fn seen_hub(status: &'static str, body: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        tokio::spawn(async move {
            while let Ok((mut conn, _)) = listener.accept().await {
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
                    let authorized = String::from_utf8_lossy(&seen).contains("Bearer runner-token");
                    let (status, body) = if authorized { (status, body) } else { ("403 Forbidden", "{}") };
                    let response = format!(
                        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = conn.write_all(response.as_bytes()).await;
                });
            }
        });
        url
    }

    #[tokio::test]
    async fn the_exit_learns_its_public_ipv4_address_from_the_relay_or_refuses_ipv4_sites() {
        let hub = seen_hub("404 Not Found", "{}").await;
        probe_seen(&Client::new(&hub, "runner-token", 5, 0).unwrap()).await;
        // Whatever this computer's routes: an IPv6-only exit reaches the IPv4 internet through NAT64 too.
        let why = ipv4_unknown("93.184.215.14".parse().unwrap()).unwrap();
        assert!(why.contains("public IPv4 address is unknown"), "{why}");
        assert_eq!(ipv4_unknown("2606:4700::1111".parse().unwrap()), None, "IPv6 sites go on");
        // The IPv4 Internet reached through IPv6 is the same router: NAT64, 6to4 and IPv4-mapped addresses wait too.
        for carried in ["64:ff9b::5102:45aa", "2002:5102:45aa::1", "::ffff:81.2.69.170"] {
            assert!(ipv4_unknown(carried.parse().unwrap()).is_some(), "{carried}");
        }
        // A hub reached over the home network sees a private address: the public one is still unknown.
        let hub = seen_hub("200 OK", r#"{"seen":"192.168.1.20"}"#).await;
        probe_seen(&Client::new(&hub, "runner-token", 5, 0).unwrap()).await;
        assert!(ipv4_unknown("64:ff9b::5db8:d70e".parse().unwrap()).is_some());
        let hub = seen_hub("200 OK", r#"{"seen":"81.2.69.170"}"#).await;
        probe_seen(&Client::new(&hub, "runner-token", 5, 0).unwrap()).await;
        assert_eq!(ipv4_unknown("93.184.215.14".parse().unwrap()), None);
        assert_eq!(refused("81.2.69.170".parse().unwrap(), &own_and_seen()), Some("this computer's own address"));
        assert_eq!(
            refused("64:ff9b::5102:45aa".parse().unwrap(), &own_and_seen()),
            Some("this computer's own address")
        );
    }

    #[test]
    fn neighbours_and_the_router_on_the_exits_own_networks_are_refused() {
        let ip = |s: &str| s.parse::<IpAddr>().unwrap();
        let iface = |addr: &str, len: u8| Iface { addr: ip(addr), len, peer: None };
        // A home with a public IPv6 /64 (reported as /128, as a temporary address can be) and a public IPv4 /24, plus
        // tunnels reporting /0 and a /48 that must not widen into whole ranges of the internet.
        let ifaces = [
            iface("2a01:4f8:1:2::10", 128),
            iface("81.2.69.10", 24),
            iface("192.168.50.2", 0),
            iface("81.3.0.9", 15),
            iface("2606:4700:110::2", 0),
            iface("2a02:1:2::7", 48),
        ];
        let nets = networks(&ifaces, &[]);
        for refused in [
            "2a01:4f8:1:2::99",
            "2a01:4f8:1:2:aaaa:bbbb:cccc:dddd",
            "2a01:4f8:1:2::1",
            "81.2.69.1",
            "81.2.69.200",
            "::ffff:81.2.69.200",
            "64:ff9b::5102:4501",
            "81.3.0.9",
            "2606:4700:110::5",
            "2a02:1:2::99",
        ] {
            assert_eq!(on_link(ip(refused), &nets), Some("on this computer's own network"), "{refused}");
        }
        for allowed in [
            "2a01:4f8:1:3::99",
            "2606:4700::1111",
            "192.0.78.17",
            "81.3.0.10",
            "2a02:1:3::1",
            "81.2.70.1",
            "93.184.215.14",
            "64:ff9b::5db8:d70e",
        ] {
            assert_eq!(on_link(ip(allowed), &nets), None, "{allowed}");
        }
        // A point-to-point peer and a gateway are refused exactly, in both families.
        let ifaces = [
            Iface { addr: ip("100.96.0.2"), len: 32, peer: Some(ip("81.2.71.1")) },
            Iface { addr: ip("fd00::2"), len: 128, peer: Some(ip("2a03:1::1")) },
        ];
        let nets = networks(&ifaces, &[ip("81.2.68.1"), ip("2a04:1::1")]);
        for refused in ["81.2.71.1", "2a03:1::1", "81.2.68.1", "2a04:1::1"] {
            assert!(on_link(ip(refused), &nets).is_some(), "{refused}");
        }
        for allowed in ["81.2.71.2", "2a03:1::2", "81.2.68.2", "2a04:1::2"] {
            assert_eq!(on_link(ip(allowed), &nets), None, "{allowed}");
        }
    }

    #[tokio::test]
    async fn the_default_gateways_are_read_from_the_routing_table() {
        let v4 = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n\
                  eth0\t00000000\t0145A8C0\t0003\t0\t0\t100\t00000000\n\
                  eth0\t0045A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\n";
        let expect = IpAddr::V4(Ipv4Addr::from(u32::from_str_radix("0145A8C0", 16).unwrap().to_ne_bytes()));
        assert_eq!(gateways_v4(v4), vec![expect]);
        let v6 = "00000000000000000000000000000000 00 00000000000000000000000000000000 00 \
                  2a0104f8000100020000000000000001 00000400 00000001 00000000 00000003 eth0\n\
                  2a0104f8000100020000000000000000 40 00000000000000000000000000000000 00 \
                  00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0\n";
        assert_eq!(gateways_v6(v6), vec!["2a01:4f8:1:2::1".parse::<IpAddr>().unwrap()]);
        // macOS `route -n get default`, for IPv4, IPv6 (zone dropped) and an interface-only route.
        let mac_v4 = "   route to: default\ndestination: default\n       mask: default\n    gateway: 192.168.1.1\n  \
                      interface: en0\n      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>\n";
        assert_eq!(route_gateway(mac_v4), Some("192.168.1.1".parse().unwrap()));
        let mac_v6 = "   route to: ::\ndestination: default\n    gateway: fe80::1%en0\n  interface: en0\n";
        assert_eq!(route_gateway(mac_v6), Some("fe80::1".parse().unwrap()));
        let mac_link = "   route to: default\ndestination: default\n    gateway: link#14\n  interface: utun3\n";
        assert_eq!(route_gateway(mac_link), None);
        assert_eq!(route_gateway("route: writing to routing socket: not in table\n"), None);
        // Every interface address of this computer is one of its own networks.
        let nets = own_networks().await;
        for iface in interfaces() {
            assert!(on_link(iface.addr, &nets).is_some(), "{}", iface.addr);
        }
    }

    #[test]
    fn the_ipv4_address_an_ipv6_address_reaches_is_read_the_way_the_registry_filter_reads_it() {
        let v4 = |s: &str| carried_v4(s.parse().unwrap()).map(|a| a.to_string());
        assert_eq!(v4("81.2.69.170").as_deref(), Some("81.2.69.170"));
        assert_eq!(v4("::ffff:81.2.69.170").as_deref(), Some("81.2.69.170"));
        assert_eq!(v4("64:ff9b::5102:45aa").as_deref(), Some("81.2.69.170"));
        assert_eq!(v4("2002:5102:45aa::1").as_deref(), Some("81.2.69.170"));
        assert_eq!(v4("2606:4700::1111"), None);
        assert_eq!(refused("2002:c0a8:0101::1".parse().unwrap(), &[]), Some("a private network address"));
    }
}
