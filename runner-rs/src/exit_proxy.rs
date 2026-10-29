//! Exit for bots, the turn's side (backend/exits.py): a turn whose bot lists sites with an exit computer gets a proxy
//! on 127.0.0.1 for as long as it runs. Listed sites go through the exit (crate::exit_link); everything else goes
//! out from this computer as it would without the proxy.
//!
//! The claim hands the bot's whole site list as `exit_routes` (direct entries too). The routing rule is the server's
//! (exits.route): an exact name first, else the longest `*.` wildcard covering it (names under it, not the name
//! itself). The proxy takes `CONNECT host:port` (HTTPS, websockets) and absolute-form plain HTTP
//! (`GET http://host/path`, sent on as origin-form with `Connection: close`), and serves a PAC file at `/proxy.pac`
//! that sends only listed sites to it. The turn gets HTTPS_PROXY / HTTP_PROXY (both cases) pointing here, NO_PROXY set
//! to loopback only, TICO_EXIT_PROXY and TICO_EXIT_PAC_URL. Unlisted traffic leaves exactly as before: straight out,
//! or through the proxy this computer already had (its NO_PROXY still honored).
//!
//! A listed site never goes anywhere but its exit: when the exit is offline the proxy answers 503 with
//! `X-Tico-Exit: exit_offline` and "waiting for exit computer <name>" (the server sets the task waiting on its exit
//! from the relay's report); any other refusal is 403 or 502 with its code. Under isolation only the turn's own user
//! (by the connection's owner in /proc/net/tcp) may use the proxy; elsewhere every bot runs as one user anyway, so
//! any local client may.
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::env::{Env, Vault};
use crate::exit_link::Refusal;
use crate::util::text_of;

pub const PROXY_ENV: &str = "TICO_EXIT_PROXY";
pub const PAC_ENV: &str = "TICO_EXIT_PAC_URL";
const PROXY_VARS: &[&str] = &["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"];
const NO_PROXY_VARS: &[&str] = &["NO_PROXY", "no_proxy"];
const LOOPBACK: &str = "localhost,127.0.0.1,::1";
const MAX_HEAD: usize = 16 * 1024;
const HEAD_WAIT: Duration = Duration::from_secs(30);
const CONNECT: Duration = Duration::from_secs(15);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Route {
    pub host: String,
    pub exit: String,
    pub name: String,
}

/// The claim's `exit_routes`, or nothing.
pub fn routes_of(attempt: &Value) -> Vec<Route> {
    attempt["exit_routes"].as_array().into_iter().flatten().filter_map(|r| {
        let host = text_of(&r["host"]).to_ascii_lowercase();
        (!host.is_empty()).then(|| Route { host, exit: text_of(&r["exit"]), name: text_of(&r["name"]) })
    }).collect()
}

fn is_direct(route: &Route) -> bool {
    route.exit.is_empty() || route.exit == "direct"
}

/// A request's host as the site list names it: lowercase, no trailing dot. Address literals are never listed.
pub fn normalize(host: &str) -> Option<String> {
    let host = host.trim().trim_end_matches('.').to_ascii_lowercase();
    if host.is_empty() || host.starts_with('[') || host.parse::<std::net::IpAddr>().is_ok() {
        return None;
    }
    Some(host)
}

/// The entry that decides `host`: the exact name, else the longest wildcard over it.
pub fn route<'a>(routes: &'a [Route], host: &str) -> Option<&'a Route> {
    let host = normalize(host)?;
    routes.iter().find(|r| r.host == host).or_else(|| {
        routes.iter()
            .filter(|r| r.host.strip_prefix('*').is_some_and(|suffix| host.ends_with(suffix) && host.len() > suffix.len()))
            .max_by_key(|r| r.host.len())
    })
}

/// The exit entry for `host`, or None when it goes out directly.
pub fn exit_for<'a>(routes: &'a [Route], host: &str) -> Option<&'a Route> {
    route(routes, host).filter(|r| !is_direct(r))
}

fn js(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| "\"\"".into())
}

/// A PAC file that sends only listed sites to the proxy at `address`; direct entries stay direct, so an exact direct
/// name under a wildcard is checked first, as the server does.
pub fn pac(routes: &[Route], address: &str) -> String {
    let mut exact: Vec<&Route> = routes.iter().filter(|r| !r.host.starts_with("*.")).collect();
    exact.sort_by(|a, b| a.host.cmp(&b.host));
    let mut wild: Vec<&Route> = routes.iter().filter(|r| r.host.starts_with("*.")).collect();
    wild.sort_by(|a, b| b.host.len().cmp(&a.host.len()).then(a.host.cmp(&b.host)));
    let answer = |r: &Route| if is_direct(r) { "\"DIRECT\"".to_string() } else { js(&format!("PROXY {address}")) };
    let mut out = String::from("function FindProxyForURL(url, host) {\n  host = host.toLowerCase();\n");
    for r in exact {
        out.push_str(&format!("  if (host == {}) return {};\n", js(&r.host), answer(r)));
    }
    for r in wild {
        out.push_str(&format!("  if (dnsDomainIs(host, {})) return {};\n", js(&r.host[1..]), answer(r)));
    }
    out.push_str("  return \"DIRECT\";\n}\n");
    out
}

// ------------------------------------------------------------------------------------------------ the computer's own proxy
/// The proxy this computer already had for unlisted traffic (plain http proxies only), and its bypass list.
#[derive(Clone, Debug, Default)]
pub struct Upstream {
    https: Option<(String, u16, Option<String>)>,
    http: Option<(String, u16, Option<String>)>,
    bypass: Vec<String>,
}

fn proxy_of(value: &str) -> Option<(String, u16, Option<String>)> {
    let url = url::Url::parse(value.trim()).ok().or_else(|| url::Url::parse(&format!("http://{}", value.trim())).ok())?;
    if url.scheme() != "http" {
        return None;
    }
    let host = url.host_str()?.trim_start_matches('[').trim_end_matches(']').to_string();
    let auth = (!url.username().is_empty()).then(|| {
        use base64::Engine;
        let user = urldecode(url.username());
        let pass = urldecode(url.password().unwrap_or(""));
        format!("Basic {}", base64::engine::general_purpose::STANDARD.encode(format!("{user}:{pass}")))
    });
    Some((host, url.port().unwrap_or(80), auth))
}

/// `%XX` escapes decoded, as a proxy URL's user and password carry them.
fn urldecode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = bytes.get(i + 1..i + 3).and_then(|h| std::str::from_utf8(h).ok()).and_then(|h| u8::from_str_radix(h, 16).ok());
        match (bytes[i], hex) {
            (b'%', Some(b)) => { out.push(b); i += 3; }
            (b, _) => { out.push(b); i += 1; }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

impl Upstream {
    pub fn from_env(env: &Env) -> Self {
        let get = |names: &[&str]| names.iter().find_map(|n| env.get(*n).filter(|v| !v.trim().is_empty()).cloned());
        let bypass = get(NO_PROXY_VARS).unwrap_or_default().split(',').map(|e| e.trim().to_ascii_lowercase())
            .filter(|e| !e.is_empty()).collect();
        Upstream { https: get(&["HTTPS_PROXY", "https_proxy"]).and_then(|v| proxy_of(&v)),
                   http: get(&["HTTP_PROXY", "http_proxy"]).and_then(|v| proxy_of(&v)), bypass }
    }

    fn bypassed(&self, host: &str) -> bool {
        let host = host.to_ascii_lowercase();
        self.bypass.iter().any(|e| {
            let e = e.split(':').next().unwrap_or("").trim_start_matches("*.").trim_start_matches('.');
            e == "*" || host == e || host.ends_with(&format!(".{e}"))
        })
    }

    fn for_connect(&self, host: &str) -> Option<&(String, u16, Option<String>)> {
        self.https.as_ref().filter(|_| !self.bypassed(host))
    }

    fn for_http(&self, host: &str) -> Option<&(String, u16, Option<String>)> {
        self.http.as_ref().filter(|_| !self.bypassed(host))
    }
}

// ------------------------------------------------------------------------------------------------ the proxy
struct Ctx {
    attempt: String,
    routes: Vec<Route>,
    uids: Vec<u32>,
    upstream: Upstream,
    pac: String,
    said_offline: AtomicBool,
}

/// A running proxy for one turn; dropping it (with the turn's vault) stops it and every connection on it.
pub struct Proxy {
    pub url: String,
    task: tokio::task::JoinHandle<()>,
    _hold: crate::exit_link::Hold,
}

impl Drop for Proxy {
    fn drop(&mut self) {
        self.task.abort();
    }
}

/// Start the proxy for run `attempt`. `uids` empty: any local client; else only connections owned by one of them.
pub async fn start(attempt: &str, routes: Vec<Route>, uids: Vec<u32>, upstream: Upstream) -> Result<Proxy, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| e.to_string())?;
    let address = format!("127.0.0.1:{}", listener.local_addr().map_err(|e| e.to_string())?.port());
    let ctx = Arc::new(Ctx { attempt: attempt.into(), pac: pac(&routes, &address), routes, uids, upstream,
                             said_offline: AtomicBool::new(false) });
    let task = tokio::spawn(async move {
        let mut connections = tokio::task::JoinSet::new();
        loop {
            tokio::select! {
                accepted = listener.accept() => match accepted {
                    Ok((socket, _)) => {
                        if !ctx.uids.is_empty() && !ctx.uids.iter().any(|uid| crate::gateway_relay::peer_is(&socket, *uid)) {
                            continue;
                        }
                        let ctx = ctx.clone();
                        connections.spawn(async move { serve(socket, &ctx).await });
                    }
                    Err(_) => tokio::time::sleep(Duration::from_millis(50)).await,
                },
                Some(_) = connections.join_next(), if !connections.is_empty() => {}
            }
        }
    });
    Ok(Proxy { url: format!("http://{address}"), task, _hold: crate::exit_link::hold() })
}

/// Give a turn whose bot sends sites through an exit its proxy, in `env`; the proxy lives in `vault`. Under isolation
/// (`isolated`) only the turn's own user, or the shared bot user a non-isolated harness runs as, may connect.
pub async fn hand_to_turn(attempt: &Value, env: &mut Env, vault: &mut Vault, isolated: bool) -> Result<(), String> {
    env.remove(PROXY_ENV);
    env.remove(PAC_ENV);
    let routes = routes_of(attempt);
    if !routes.iter().any(|r| !is_direct(r)) {
        return Ok(());
    }
    let mut uids = Vec::new();
    if isolated {
        uids.extend(vault.turn_user.as_ref().map(|u| u.uid));
        uids.extend(crate::isolation::identity().map(|(uid, _)| uid));
    }
    let upstream = Upstream::from_env(env);
    let proxy = start(&text_of(&attempt["id"]), routes, uids, upstream).await.map_err(|e| format!("exit proxy: {e}"))?;
    for name in PROXY_VARS {
        env.insert((*name).into(), proxy.url.clone());
    }
    for name in NO_PROXY_VARS {
        env.insert((*name).into(), LOOPBACK.into());
    }
    env.insert(PROXY_ENV.into(), proxy.url.clone());
    env.insert(PAC_ENV.into(), format!("{}/proxy.pac", proxy.url));
    vault.exit = Some(proxy);
    Ok(())
}

/// One request's head: (method, target, version, header lines, bytes after the head).
type Head = (String, String, String, Vec<String>, Vec<u8>);

async fn read_head(socket: &mut TcpStream) -> Option<Head> {
    let mut buf = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];
    let end = loop {
        if let Some(at) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break at;
        }
        if buf.len() > MAX_HEAD {
            return None;
        }
        let n = tokio::time::timeout(HEAD_WAIT, socket.read(&mut chunk)).await.ok()?.ok()?;
        if n == 0 {
            return None;
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    let text = std::str::from_utf8(&buf[..end]).ok()?;
    let mut lines = text.split("\r\n");
    let mut first = lines.next()?.split(' ');
    let (method, target, version) = (first.next()?.to_string(), first.next()?.to_string(), first.next()?.to_string());
    Some((method, target, version, lines.map(str::to_owned).collect(), buf[end + 4..].to_vec()))
}

/// `host:port` (with `[v6]:port`) as (host, port).
fn authority(text: &str, default: u16) -> Option<(String, u16)> {
    if let Some(rest) = text.strip_prefix('[') {
        let (host, after) = rest.split_once(']')?;
        let port = after.strip_prefix(':').map(|p| p.parse().ok()).unwrap_or(Some(default))?;
        return Some((host.to_string(), port));
    }
    match text.rsplit_once(':') {
        Some((host, port)) => Some((host.to_string(), port.parse().ok()?)),
        None => Some((text.to_string(), default)),
    }
}

fn answer(status: &str, code: Option<&str>, body: &str) -> Vec<u8> {
    let header = code.map(|c| format!("X-Tico-Exit: {c}\r\n")).unwrap_or_default();
    format!("HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n{header}Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()).into_bytes()
}

/// A refusal as the browser sees it: 503 while the exit is away (or unreachable), 403 when it is not allowed.
pub fn refusal_answer(refusal: &Refusal) -> Vec<u8> {
    let status = match refusal.code.as_str() {
        "exit_offline" | "no_link" | "link_lost" | "timeout" | "busy" | "unavailable" | "exit_off" => "503 Service Unavailable",
        "not_listed" | "forbidden" | "exit_not_allowed" | "port" | "blocked" | "invalid" | "stale_lease" => "403 Forbidden",
        _ => "502 Bad Gateway",
    };
    let detail = if refusal.detail.is_empty() { "the exit refused this connection" } else { &refusal.detail };
    answer(status, Some(&refusal.code), &format!("Tico exit: {detail}\n"))
}

/// Rewrite an absolute-form request to origin-form for the site itself, ending the connection after it.
fn origin_form(method: &str, path: &str, version: &str, headers: &[String], authority: &str) -> Vec<u8> {
    let mut out = format!("{method} {path} {version}\r\n");
    let mut host = false;
    for line in headers {
        let name = line.split(':').next().unwrap_or("").trim().to_ascii_lowercase();
        if name.starts_with("proxy-") || matches!(name.as_str(), "connection" | "keep-alive") {
            continue;
        }
        host |= name == "host";
        out.push_str(line);
        out.push_str("\r\n");
    }
    if !host {
        out.push_str(&format!("Host: {authority}\r\n"));
    }
    out.push_str("Connection: close\r\n\r\n");
    out.into_bytes()
}

async fn serve(mut socket: TcpStream, ctx: &Ctx) {
    let _ = socket.set_nodelay(true);
    let Some((method, target, version, headers, rest)) = read_head(&mut socket).await else { return };
    if method == "CONNECT" {
        let Some((host, port)) = authority(&target, 443) else {
            let _ = socket.write_all(&answer("400 Bad Request", None, "bad CONNECT target\n")).await;
            return;
        };
        match exit_for(&ctx.routes, &host) {
            Some(r) => through_exit(socket, ctx, r, &host, port, Some(rest)).await,
            None => direct(socket, ctx, &host, port, None, rest).await,
        }
        return;
    }
    if method == "GET" && target == "/proxy.pac" {
        let head = format!("HTTP/1.1 200 OK\r\nContent-Type: application/x-ns-proxy-autoconfig\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                           ctx.pac.len());
        let _ = socket.write_all(head.as_bytes()).await;
        let _ = socket.write_all(ctx.pac.as_bytes()).await;
        return;
    }
    let Ok(url) = url::Url::parse(&target).map_err(|_| ()).and_then(|u| if u.scheme() == "http" { Ok(u) } else { Err(()) }) else {
        let _ = socket.write_all(&answer("400 Bad Request", None, "this proxy takes CONNECT and http:// requests\n")).await;
        return;
    };
    let host = url.host_str().unwrap_or("").trim_start_matches('[').trim_end_matches(']').to_string();
    let port = url.port().unwrap_or(80);
    let path = match url.query() {
        Some(q) => format!("{}?{q}", url.path()),
        None => url.path().to_string(),
    };
    let shown = if port == 80 { host.clone() } else { format!("{host}:{port}") };
    let mut first = origin_form(&method, &path, &version, &headers, &shown);
    match exit_for(&ctx.routes, &host) {
        Some(r) => {
            first.extend_from_slice(&rest);
            through_exit_with(socket, ctx, r, &host, port, first).await
        }
        None => {
            // Through the computer's own proxy the request stays absolute-form, as that proxy expects.
            let raw = if ctx.upstream.for_http(&host).is_some() {
                let mut raw = format!("{method} {target} {version}\r\n");
                for line in &headers {
                    let name = line.split(':').next().unwrap_or("").trim().to_ascii_lowercase();
                    if !name.starts_with("proxy-") { raw.push_str(line); raw.push_str("\r\n"); }
                }
                raw.push_str("\r\n");
                let mut raw = raw.into_bytes();
                raw.extend_from_slice(&rest);
                raw
            } else {
                first.extend_from_slice(&rest);
                first
            };
            direct(socket, ctx, &host, port, Some(raw), Vec::new()).await
        }
    }
}

async fn through_exit(mut socket: TcpStream, ctx: &Ctx, r: &Route, host: &str, port: u16, rest: Option<Vec<u8>>) {
    match crate::exit_link::open(&ctx.attempt, host, port).await {
        Ok(tunnel) => {
            if socket.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n").await.is_ok() {
                tunnel.pump(socket, rest.unwrap_or_default()).await;
            }
        }
        Err(refusal) => refuse(socket, ctx, r, refusal).await,
    }
}

async fn through_exit_with(socket: TcpStream, ctx: &Ctx, r: &Route, host: &str, port: u16, first: Vec<u8>) {
    match crate::exit_link::open(&ctx.attempt, host, port).await {
        Ok(tunnel) => tunnel.pump(socket, first).await,
        Err(refusal) => refuse(socket, ctx, r, refusal).await,
    }
}

async fn refuse(mut socket: TcpStream, ctx: &Ctx, r: &Route, refusal: Refusal) {
    if refusal.code == "exit_offline" && !ctx.said_offline.swap(true, Ordering::SeqCst) {
        crate::util::log(&format!("{}: waiting for exit computer {}", ctx.attempt, if r.name.is_empty() { &r.exit } else { &r.name }));
    }
    let _ = socket.write_all(&refusal_answer(&refusal)).await;
}

/// Unlisted traffic: straight out, or through the computer's own proxy. `raw` is a plain-http request to send on;
/// without it this answers a CONNECT.
async fn direct(mut socket: TcpStream, ctx: &Ctx, host: &str, port: u16, raw: Option<Vec<u8>>, rest: Vec<u8>) {
    let via = if raw.is_some() { ctx.upstream.for_http(host) } else { ctx.upstream.for_connect(host) };
    let target = via.map(|(h, p, _)| (h.clone(), *p)).unwrap_or_else(|| (host.to_string(), port));
    let mut out = match tokio::time::timeout(CONNECT, TcpStream::connect((target.0.as_str(), target.1))).await {
        Ok(Ok(out)) => out,
        _ => {
            let _ = socket.write_all(&answer("502 Bad Gateway", None, &format!("could not reach {host}\n"))).await;
            return;
        }
    };
    let _ = out.set_nodelay(true);
    match raw {
        Some(mut raw) => {
            if let Some((_, _, Some(auth))) = via {
                let at = raw.windows(2).position(|w| w == b"\r\n").map(|i| i + 2).unwrap_or(0);
                raw.splice(at..at, format!("Proxy-Authorization: {auth}\r\n").into_bytes());
            }
            if out.write_all(&raw).await.is_err() { return; }
        }
        None => {
            if let Some((_, _, auth)) = via {
                let auth = auth.as_ref().map(|a| format!("Proxy-Authorization: {a}\r\n")).unwrap_or_default();
                let ask = format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n{auth}\r\n");
                if out.write_all(ask.as_bytes()).await.is_err() { return; }
                // Pass the computer's proxy's answer on as it is: its 200 opens the tunnel, anything else ends it.
                let Some((_, status, _, _, extra)) = read_head(&mut out).await else { return };
                if status != "200" {
                    let _ = socket.write_all(&answer("502 Bad Gateway", None, &format!("this computer's proxy refused {host}\n"))).await;
                    return;
                }
                if !extra.is_empty() && socket.write_all(&extra).await.is_err() { return; }
            }
            if socket.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n").await.is_err() { return; }
        }
    }
    if !rest.is_empty() && out.write_all(&rest).await.is_err() {
        return;
    }
    let _ = tokio::io::copy_bidirectional(&mut socket, &mut out).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn routes() -> Vec<Route> {
        let r = |host: &str, exit: &str| Route { host: host.into(), exit: exit.into(), name: if exit == "direct" { "".into() } else { "Home Mac".into() } };
        vec![r("*.acme.example", "computer:a"), r("*.shop.acme.example", "computer:b"), r("shop.acme.example", "direct"),
             r("orders.example", "computer:a")]
    }

    #[test]
    fn routing_matches_the_servers_rule() {
        let routes = routes();
        assert_eq!(exit_for(&routes, "shop.acme.example"), None);                       // exact direct wins
        assert_eq!(exit_for(&routes, "eu.shop.acme.example").unwrap().exit, "computer:b"); // longest wildcard
        assert_eq!(exit_for(&routes, "WWW.Acme.Example.").unwrap().exit, "computer:a");
        assert_eq!(exit_for(&routes, "acme.example"), None);                             // not the name itself
        assert_eq!(exit_for(&routes, "evilacme.example"), None);
        assert_eq!(exit_for(&routes, "orders.example").unwrap().exit, "computer:a");
        assert_eq!(exit_for(&routes, "93.184.216.34"), None);                            // addresses are never listed
        assert_eq!(exit_for(&routes, "[::1]"), None);
    }

    #[test]
    fn the_claim_hands_the_routes() {
        let attempt = serde_json::json!({"exit_routes": [{"host": "Orders.example", "exit": "computer:a", "name": "Home Mac"},
                                                         {"host": "", "exit": "computer:a", "name": ""}]});
        assert_eq!(routes_of(&attempt), vec![Route { host: "orders.example".into(), exit: "computer:a".into(), name: "Home Mac".into() }]);
        assert!(routes_of(&serde_json::json!({})).is_empty());
    }

    #[test]
    fn the_pac_file_sends_only_listed_sites_to_the_proxy() {
        let pac = pac(&routes(), "127.0.0.1:4000");
        let exact_direct = pac.find("(host == \"shop.acme.example\") return \"DIRECT\"").unwrap();
        let longest = pac.find("dnsDomainIs(host, \".shop.acme.example\")) return \"PROXY 127.0.0.1:4000\"").unwrap();
        let shorter = pac.find("dnsDomainIs(host, \".acme.example\")").unwrap();
        assert!(exact_direct < longest && longest < shorter);
        assert!(pac.contains("host == \"orders.example\") return \"PROXY 127.0.0.1:4000\""));
        assert!(pac.trim_end().ends_with("return \"DIRECT\";\n}"));
    }

    #[test]
    fn refusals_are_clear_and_an_offline_exit_is_a_503() {
        let offline = String::from_utf8(refusal_answer(&Refusal::new("exit_offline", "waiting for exit computer Home Mac"))).unwrap();
        assert!(offline.starts_with("HTTP/1.1 503 "));
        assert!(offline.contains("X-Tico-Exit: exit_offline\r\n"));
        assert!(offline.ends_with("Tico exit: waiting for exit computer Home Mac\n"));
        assert!(String::from_utf8(refusal_answer(&Refusal::new("blocked", "x"))).unwrap().starts_with("HTTP/1.1 403 "));
    }

    #[test]
    fn plain_http_goes_on_in_origin_form_and_closes() {
        let head = origin_form("GET", "/a?b=1", "HTTP/1.1", &["Host: orders.example".into(), "Proxy-Authorization: x".into(),
                                                             "Connection: keep-alive".into(), "Accept: */*".into()], "orders.example");
        assert_eq!(String::from_utf8(head).unwrap(),
                   "GET /a?b=1 HTTP/1.1\r\nHost: orders.example\r\nAccept: */*\r\nConnection: close\r\n\r\n");
        assert_eq!(authority("[2001:db8::1]:8443", 443), Some(("2001:db8::1".into(), 8443)));
        assert_eq!(authority("orders.example", 443), Some(("orders.example".into(), 443)));
    }

    #[test]
    fn the_computers_own_proxy_carries_unlisted_traffic_and_its_bypass_list_holds() {
        let mut env = Env::new();
        env.insert("HTTPS_PROXY".into(), "http://user:p%40ss@proxy.acme.example:3128".into());
        env.insert("no_proxy".into(), ".internal.example, localhost".into());
        let up = Upstream::from_env(&env);
        let (host, port, auth) = up.for_connect("news.example").unwrap();
        assert_eq!((host.as_str(), *port), ("proxy.acme.example", 3128));
        use base64::Engine;
        assert_eq!(auth.as_deref().unwrap(), format!("Basic {}", base64::engine::general_purpose::STANDARD.encode("user:p@ss")));
        assert!(up.for_connect("git.internal.example").is_none() && up.for_connect("localhost").is_none());
        assert!(up.for_http("news.example").is_none()); // no HTTP_PROXY was set
    }

    async fn ask(proxy: &str, request: &str) -> String {
        let mut socket = TcpStream::connect(proxy.trim_start_matches("http://")).await.unwrap();
        socket.write_all(request.as_bytes()).await.unwrap();
        let mut got = Vec::new();
        let _ = tokio::time::timeout(Duration::from_secs(20), socket.read_to_end(&mut got)).await;
        String::from_utf8_lossy(&got).into_owned()
    }

    #[tokio::test]
    async fn a_listed_site_is_refused_when_its_exit_cannot_be_reached_and_never_goes_direct() {
        // A local "site" that would answer if the proxy ever went there directly.
        let site = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let site_port = site.local_addr().unwrap().port();
        let reached = Arc::new(AtomicBool::new(false));
        let flag = reached.clone();
        tokio::spawn(async move {
            while let Ok((mut s, _)) = site.accept().await {
                flag.store(true, Ordering::SeqCst);
                let _ = s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi").await;
            }
        });
        // `localhost` stands for a listed name; the link to the relay never comes up here.
        let listed = vec![Route { host: "localhost.acme.example".into(), exit: "computer:a".into(), name: "Home Mac".into() }];
        let proxy = start("a1", listed, vec![], Upstream::default()).await.unwrap();
        let refused = ask(&proxy.url, "CONNECT localhost.acme.example:443 HTTP/1.1\r\nHost: x\r\n\r\n").await;
        assert!(refused.starts_with("HTTP/1.1 503 ") && refused.contains("X-Tico-Exit: no_link"), "{refused}");
        let refused = ask(&proxy.url, "GET http://localhost.acme.example/ HTTP/1.1\r\nHost: x\r\n\r\n").await;
        assert!(refused.starts_with("HTTP/1.1 503 "), "{refused}");
        assert!(!reached.load(Ordering::SeqCst));
        // An unlisted host goes straight out; the PAC file is served too.
        let direct = ask(&proxy.url, &format!("GET http://127.0.0.1:{site_port}/ HTTP/1.1\r\nHost: x\r\n\r\n")).await;
        assert!(direct.ends_with("hi") && reached.load(Ordering::SeqCst), "{direct}");
        let pac = ask(&proxy.url, "GET /proxy.pac HTTP/1.1\r\nHost: x\r\n\r\n").await;
        assert!(pac.contains("FindProxyForURL") && pac.contains("localhost.acme.example"));
    }
}
