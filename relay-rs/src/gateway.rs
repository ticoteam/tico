//! Keep the public origin unchanged without another proxy container.
use super::{browser, runner, App};
use axum::{
    body::Body,
    extract::{ConnectInfo, Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
    Router,
};
use std::net::{IpAddr, SocketAddr};

#[derive(Clone)]
pub(super) struct Network { address: IpAddr, prefix: u8 }

/// An IPv4-mapped IPv6 address (`::ffff:a.b.c.d`, a dual-stack socket's IPv4 peer) as the IPv4 address it is.
fn canonical(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        IpAddr::V4(_) => ip,
    }
}

impl Network {
    fn contains(&self, ip: IpAddr) -> bool {
        match (self.address, canonical(ip)) {
            (IpAddr::V4(net), IpAddr::V4(ip)) => {
                let mask = u32::MAX.checked_shl(32 - self.prefix as u32).unwrap_or(0);
                u32::from(net) & mask == u32::from(ip) & mask
            }
            (IpAddr::V6(net), IpAddr::V6(ip)) => {
                let mask = u128::MAX.checked_shl(128 - self.prefix as u32).unwrap_or(0);
                u128::from(net) & mask == u128::from(ip) & mask
            }
            _ => false,
        }
    }
}

pub(super) fn trusted_proxies(value: &str) -> Result<Vec<Network>, String> {
    value.split(',').map(str::trim).filter(|v| !v.is_empty()).map(|v| {
        let (address, prefix) = v.split_once('/').map(|(a, p)| (a, Some(p))).unwrap_or((v, None));
        let parsed: IpAddr = address.parse().map_err(|_| "Invalid TICO_TRUSTED_PROXIES")?;
        let max = if parsed.is_ipv4() { 32 } else { 128 };
        let prefix = prefix.map(|p| p.parse::<u8>()).transpose().map_err(|_| "Invalid proxy prefix")?.unwrap_or(max);
        if prefix > max { return Err("Invalid proxy prefix".into()); }
        // A mapped range (::ffff:a.b.c.d/96 or longer) is the IPv4 range it maps, as peers are compared unwrapped.
        let address = canonical(parsed);
        let prefix = if address != parsed {
            if prefix < 96 { return Ok(Network { address: parsed, prefix }); }
            prefix - 96
        } else { prefix };
        Ok(Network { address, prefix })
    }).collect()
}

fn client_headers(headers: &mut HeaderMap, peer: IpAddr, trusted: &[Network]) {
    let peer = canonical(peer);
    if trusted.iter().any(|network| network.contains(peer)) {
        let chain = headers.get("x-forwarded-for").and_then(|h| h.to_str().ok()).unwrap_or("");
        let forwarded = if chain.is_empty() { peer.to_string() } else { format!("{chain}, {peer}") };
        if let Ok(value) = forwarded.parse() { headers.insert("x-forwarded-for", value); }
    } else {
        for name in ["forwarded", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip", "cf-connecting-ip"] {
            headers.remove(name);
        }
        headers.insert("x-forwarded-for", peer.to_string().parse().unwrap());
        headers.insert("x-forwarded-proto", "http".parse().unwrap());
    }
}


fn strip_hop_headers(headers: &mut HeaderMap) {
    let connection = headers
        .get("connection")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    for name in connection.split(',').map(str::trim) {
        headers.remove(name);
    }
    for name in ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"] {
        headers.remove(name);
    }
}

async fn forward(
    State(app): State<App>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    request: Request,
) -> Response {
    let path = request.uri().path();
    if path == "/control" || path.starts_with("/control/") || path == "/_tico" || path.starts_with("/_tico/") {
        return StatusCode::FORBIDDEN.into_response();
    }
    let (mut parts, body) = request.into_parts();
    strip_hop_headers(&mut parts.headers);
    client_headers(&mut parts.headers, peer.ip(), &app.trusted_proxies);
    let target = format!("{}{}", app.python, parts.uri.path_and_query().map(|p| p.as_str()).unwrap_or("/"));
    let response = app.proxy.request(parts.method, target)
        .headers(parts.headers)
        .body(reqwest::Body::wrap_stream(body.into_data_stream()))
        .send().await;
    match response {
        Ok(response) => {
            let status = response.status();
            let mut headers = response.headers().clone();
            strip_hop_headers(&mut headers);
            let mut output = Response::new(Body::from_stream(response.bytes_stream()));
            *output.status_mut() = status;
            *output.headers_mut() = headers;
            output
        }
        Err(_) => StatusCode::BAD_GATEWAY.into_response(),
    }
}

pub(super) fn routes(app: App) -> Router {
    Router::new()
        .route("/api/v2/runners/{rid}/screen/ws", get(browser))
        .route("/api/v2/runners/me/screen/{sid}/ws", get(runner))
        .fallback(forward)
        .with_state(app)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_configured_proxies_preserve_client_headers() {
        let trusted = trusted_proxies("127.0.0.1/32,172.20.0.2/32").unwrap();
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-for", "198.51.100.9".parse().unwrap());
        headers.insert("cf-connecting-ip", "198.51.100.9".parse().unwrap());
        client_headers(&mut headers, "172.20.0.2".parse().unwrap(), &trusted);
        assert_eq!(headers["x-forwarded-for"], "198.51.100.9, 172.20.0.2");
        assert!(headers.contains_key("cf-connecting-ip"));
        client_headers(&mut headers, "203.0.113.8".parse().unwrap(), &trusted);
        assert_eq!(headers["x-forwarded-for"], "203.0.113.8");
        assert!(!headers.contains_key("cf-connecting-ip"));
    }

    #[test]
    fn ipv4_mapped_peers_are_their_ipv4_addresses() {
        let trusted = trusted_proxies("172.20.0.2/32,::ffff:10.1.0.0/112").unwrap();
        assert!(trusted[0].contains("::ffff:172.20.0.2".parse().unwrap()));
        assert!(!trusted[0].contains("::ffff:172.20.0.3".parse().unwrap()));
        assert!(trusted[1].contains("10.1.200.7".parse().unwrap()));
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-for", "198.51.100.9".parse().unwrap());
        client_headers(&mut headers, "::ffff:172.20.0.2".parse().unwrap(), &trusted);
        assert_eq!(headers["x-forwarded-for"], "198.51.100.9, 172.20.0.2");
        client_headers(&mut headers, "::ffff:203.0.113.8".parse().unwrap(), &trusted);
        assert_eq!(headers["x-forwarded-for"], "203.0.113.8");
    }
}
