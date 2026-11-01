//! Keep the public origin unchanged without another proxy container.
use super::{browser, runner, App, BYTES};
use axum::{
    body::Body,
    extract::{
        ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade},
        ConnectInfo, Request, State,
    },
    http::{header, HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::get,
    Router,
};
use futures_util::{
    stream::{SplitSink, SplitStream},
    SinkExt, StreamExt,
};
use std::{
    net::{IpAddr, SocketAddr},
    time::Duration,
};
use tokio::net::TcpStream;
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{
        self,
        client::IntoClientRequest,
        protocol::{CloseFrame as BackendClose, WebSocketConfig},
        Message as BackendMessage,
    },
    MaybeTlsStream, WebSocketStream,
};

type Backend = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[derive(Clone)]
pub(super) struct Network {
    address: IpAddr,
    prefix: u8,
}

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

/// The request header that names the client address when the peer is trusted (TICO_CLIENT_IP_HEADER, read as
/// backend/config.py reads it).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum ClientIpHeader {
    XForwardedFor,
    CfConnectingIp,
}

pub(super) fn client_ip_header(value: &str) -> Result<ClientIpHeader, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "" | "x-forwarded-for" => Ok(ClientIpHeader::XForwardedFor),
        "cf-connecting-ip" => Ok(ClientIpHeader::CfConnectingIp),
        _ => Err("TICO_CLIENT_IP_HEADER is x-forwarded-for or cf-connecting-ip".into()),
    }
}

pub(super) fn trusted_proxies(value: &str) -> Result<Vec<Network>, String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(|v| {
            let (address, prefix) = v
                .split_once('/')
                .map(|(a, p)| (a, Some(p)))
                .unwrap_or((v, None));
            let parsed: IpAddr = address
                .parse()
                .map_err(|_| "Invalid TICO_TRUSTED_PROXIES")?;
            let max = if parsed.is_ipv4() { 32 } else { 128 };
            let prefix = prefix
                .map(|p| p.parse::<u8>())
                .transpose()
                .map_err(|_| "Invalid proxy prefix")?
                .unwrap_or(max);
            if prefix > max {
                return Err("Invalid proxy prefix".into());
            }
            // A mapped range (::ffff:a.b.c.d/96 or longer) is the IPv4 range it maps, as peers are compared unwrapped.
            let address = canonical(parsed);
            let prefix = if address != parsed {
                if prefix < 96 {
                    return Ok(Network {
                        address: parsed,
                        prefix,
                    });
                }
                prefix - 96
            } else {
                prefix
            };
            Ok(Network { address, prefix })
        })
        .collect()
}

fn client_headers(headers: &mut HeaderMap, peer: IpAddr, trusted: &[Network]) {
    let peer = canonical(peer);
    if trusted.iter().any(|network| network.contains(peer)) {
        let chain = headers
            .get("x-forwarded-for")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("");
        let forwarded = if chain.is_empty() {
            peer.to_string()
        } else {
            format!("{chain}, {peer}")
        };
        if let Ok(value) = forwarded.parse() {
            headers.insert("x-forwarded-for", value);
        }
    } else {
        for name in [
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-forwarded-host",
            "x-real-ip",
            "cf-connecting-ip",
        ] {
            headers.remove(name);
        }
        headers.insert("x-forwarded-for", peer.to_string().parse().unwrap());
        headers.insert("x-forwarded-proto", "http".parse().unwrap());
    }
}

/// The address a request came from: the peer, or, when the peer is a trusted proxy (Caddy, cloudflared), the address
/// its client header names (the nearest untrusted X-Forwarded-For hop, or CF-Connecting-IP). None when an
/// X-Forwarded-For chain cannot be read.
pub(super) fn client_ip(
    headers: &HeaderMap,
    peer: IpAddr,
    trusted: &[Network],
    mode: ClientIpHeader,
) -> Option<IpAddr> {
    let peer = canonical(peer);
    if !trusted.iter().any(|network| network.contains(peer)) {
        return Some(peer);
    }
    if mode == ClientIpHeader::CfConnectingIp {
        // As backend/client_ip.py: a missing, oversized or unreadable value is the peer.
        let single = headers
            .get("cf-connecting-ip")
            .and_then(|h| h.to_str().ok())
            .filter(|h| !h.is_empty() && h.len() <= 2048)
            .and_then(|h| h.trim().parse::<IpAddr>().ok());
        return Some(single.map(canonical).unwrap_or(peer));
    }
    let chain = headers
        .get_all("x-forwarded-for")
        .iter()
        .map(|h| h.to_str().ok())
        .collect::<Option<Vec<_>>>()?
        .join(",");
    let mut nearest = peer;
    for hop in chain
        .split(',')
        .map(str::trim)
        .filter(|h| !h.is_empty())
        .rev()
    {
        nearest = canonical(hop.parse().ok()?);
        if !trusted.iter().any(|network| network.contains(nearest)) {
            break;
        }
    }
    Some(nearest)
}

/// The exit computer's link, told the address it connects from (exit.rs `hello`).
async fn exit_link(
    State(app): State<App>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    let seen = client_ip(
        &headers,
        peer.ip(),
        &app.trusted_proxies,
        app.client_ip_header,
    );
    super::exit::link_seen(app, headers, ws, seen).await
}

/// The address an exit computer's request came from (exit.rs `seen`).
async fn exit_seen(
    State(app): State<App>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
) -> Response {
    let seen = client_ip(
        &headers,
        peer.ip(),
        &app.trusted_proxies,
        app.client_ip_header,
    );
    super::exit::seen(app, headers, seen).await
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
    for name in [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    ] {
        headers.remove(name);
    }
}

async fn forward(
    State(app): State<App>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    request: Request,
) -> Response {
    let path = request.uri().path();
    if path == "/control"
        || path.starts_with("/control/")
        || path == "/_tico"
        || path.starts_with("/_tico/")
    {
        // The refusal leaves the request body unread, so the server may drop the connection after it; saying so keeps a
        // client from sending its next request on a connection that closes without an answer.
        return (StatusCode::FORBIDDEN, [(header::CONNECTION, "close")]).into_response();
    }
    let (mut parts, body) = request.into_parts();
    strip_hop_headers(&mut parts.headers);
    client_headers(&mut parts.headers, peer.ip(), &app.trusted_proxies);
    let target = format!(
        "{}{}",
        app.python,
        parts
            .uri
            .path_and_query()
            .map(|p| p.as_str())
            .unwrap_or("/")
    );
    let response = app
        .proxy
        .request(parts.method, target)
        .headers(parts.headers)
        .body(reqwest::Body::wrap_stream(body.into_data_stream()))
        .send()
        .await;
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

/// The recorder's live speech stream, relayed by the Tico backend to the Gateway. The backend is dialled before the
/// client is accepted, so a refusal (its 403 for a bad token, or a 503) reaches the client as that HTTP status.
async fn recorder_stream(
    State(app): State<App>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    uri: Uri,
    ws: WebSocketUpgrade,
) -> Response {
    // The backend is plain HTTP on the private network, so its WebSocket is plain too.
    let Some(backend) = app.python.strip_prefix("http://") else {
        return StatusCode::BAD_GATEWAY.into_response();
    };
    let path = uri.path_and_query().map(|p| p.as_str()).unwrap_or("/");
    let Ok(mut request) = format!("ws://{backend}{path}").into_client_request() else {
        return StatusCode::BAD_GATEWAY.into_response();
    };
    // The client's headers, as forward() passes them, minus the handshake's own (host, Sec-WebSocket-*).
    let mut forwarded = HeaderMap::new();
    for (name, value) in &headers {
        if name != header::HOST && !name.as_str().starts_with("sec-websocket-") {
            forwarded.append(name.clone(), value.clone());
        }
    }
    strip_hop_headers(&mut forwarded);
    client_headers(&mut forwarded, peer.ip(), &app.trusted_proxies);
    request.headers_mut().extend(forwarded);
    let config = WebSocketConfig::default()
        .max_message_size(Some(BYTES))
        .max_frame_size(Some(BYTES));
    let connected = tokio::time::timeout(
        Duration::from_secs(10),
        connect_async_with_config(request, Some(config), false),
    )
    .await;
    match connected {
        Ok(Ok((backend, _))) => ws
            .max_message_size(BYTES)
            .max_frame_size(BYTES)
            .on_upgrade(move |client| relay(client, backend)),
        Ok(Err(tungstenite::Error::Http(response)))
            if response.status().is_client_error() || response.status().is_server_error() =>
        {
            response.status().into_response()
        }
        _ => StatusCode::BAD_GATEWAY.into_response(),
    }
}

/// Frames pass unchanged in both directions until one side closes; that side's close goes on to the other.
async fn relay(client: WebSocket, backend: Backend) {
    let (mut client_tx, mut client_rx) = client.split();
    let (mut backend_tx, mut backend_rx) = backend.split();
    tokio::select! {
        _ = client_to_backend(&mut client_rx, &mut backend_tx) => {}
        _ = backend_to_client(&mut backend_rx, &mut client_tx) => {}
    }
}

async fn client_to_backend(
    client: &mut SplitStream<WebSocket>,
    backend: &mut SplitSink<Backend, BackendMessage>,
) {
    while let Some(Ok(message)) = client.next().await {
        let forwarded = match message {
            Message::Text(text) => BackendMessage::Text((&*text).into()),
            Message::Binary(bytes) => BackendMessage::Binary(bytes),
            Message::Close(frame) => {
                let frame = frame.map(|f| BackendClose {
                    code: f.code.into(),
                    reason: (&*f.reason).into(),
                });
                let _ = backend.send(BackendMessage::Close(frame)).await;
                return;
            }
            // Pings and pongs are answered by the libraries on each connection.
            Message::Ping(_) | Message::Pong(_) => continue,
        };
        if backend.send(forwarded).await.is_err() {
            return;
        }
    }
}

async fn backend_to_client(
    backend: &mut SplitStream<Backend>,
    client: &mut SplitSink<WebSocket, Message>,
) {
    while let Some(Ok(message)) = backend.next().await {
        let forwarded = match message {
            BackendMessage::Text(text) => Message::Text((&*text).into()),
            BackendMessage::Binary(bytes) => Message::Binary(bytes),
            BackendMessage::Close(frame) => {
                let frame = frame.map(|f| CloseFrame {
                    code: f.code.into(),
                    reason: (&*f.reason).into(),
                });
                let _ = client.send(Message::Close(frame)).await;
                return;
            }
            BackendMessage::Ping(_) | BackendMessage::Pong(_) | BackendMessage::Frame(_) => {
                continue
            }
        };
        if client.send(forwarded).await.is_err() {
            return;
        }
    }
}

pub(super) fn routes(app: App) -> Router {
    Router::new()
        .route("/api/v2/runners/{rid}/screen/ws", get(browser))
        .route("/api/v2/runners/me/screen/{sid}/ws", get(runner))
        .route("/api/v2/runners/me/browser-exit/ws", get(exit_link))
        .route("/api/v2/runners/me/browser-exit/seen", get(exit_seen))
        .route(
            "/api/v2/runners/me/browser-exit/tunnels/{tid}/ws",
            get(super::exit::client),
        )
        .route(
            "/api/v2/runners/me/browser-exit/tunnels/{tid}/exit",
            get(super::exit::exit_side),
        )
        .route("/api/v2/recorder/stream", get(recorder_stream))
        .fallback(forward)
        .with_state(app)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Hub;
    use std::sync::{Arc, Mutex};
    use tokio_tungstenite::tungstenite::{
        protocol::{frame::coding::CloseCode, CloseFrame as ClientClose},
        Message as ClientMessage,
    };

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
    fn the_client_address_is_the_peer_or_the_nearest_untrusted_forwarded_hop() {
        let trusted = trusted_proxies("127.0.0.1/32,172.20.0.0/24").unwrap();
        let empty = HeaderMap::new();
        let ip = |s: &str| s.parse::<IpAddr>().unwrap();
        // An untrusted peer is the client, whatever it claims.
        let mut claims = HeaderMap::new();
        claims.insert("x-forwarded-for", "198.51.100.9".parse().unwrap());
        assert_eq!(
            client_ip(
                &claims,
                ip("81.2.69.160"),
                &trusted,
                ClientIpHeader::XForwardedFor
            ),
            Some(ip("81.2.69.160"))
        );
        // Behind Caddy and cloudflared: the hop before the trusted ones, never a spoofed first entry.
        let mut chain = HeaderMap::new();
        chain.insert(
            "x-forwarded-for",
            "10.9.9.9, 81.2.69.160, 172.20.0.3".parse().unwrap(),
        );
        assert_eq!(
            client_ip(
                &chain,
                ip("172.20.0.2"),
                &trusted,
                ClientIpHeader::XForwardedFor
            ),
            Some(ip("81.2.69.160"))
        );
        let mut mapped = HeaderMap::new();
        mapped.insert("x-forwarded-for", "::ffff:81.2.69.160".parse().unwrap());
        assert_eq!(
            client_ip(
                &mapped,
                ip("::ffff:127.0.0.1"),
                &trusted,
                ClientIpHeader::XForwardedFor
            ),
            Some(ip("81.2.69.160"))
        );
        assert_eq!(
            client_ip(
                &empty,
                ip("127.0.0.1"),
                &trusted,
                ClientIpHeader::XForwardedFor
            ),
            Some(ip("127.0.0.1"))
        );
        let mut junk = HeaderMap::new();
        junk.insert(
            "x-forwarded-for",
            "81.2.69.160, not-an-address".parse().unwrap(),
        );
        assert_eq!(
            client_ip(
                &junk,
                ip("127.0.0.1"),
                &trusted,
                ClientIpHeader::XForwardedFor
            ),
            None
        );
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
        client_headers(
            &mut headers,
            "::ffff:203.0.113.8".parse().unwrap(),
            &trusted,
        );
        assert_eq!(headers["x-forwarded-for"], "203.0.113.8");
    }

    #[test]
    fn cloudflare_mode_reads_the_single_connecting_address_from_a_trusted_peer() {
        use ClientIpHeader::{CfConnectingIp, XForwardedFor};
        let trusted = trusted_proxies("127.0.0.1/32,172.20.0.0/24").unwrap();
        let ip = |s: &str| s.parse::<IpAddr>().unwrap();
        let with = |name: &'static str, value: &str| {
            let mut headers = HeaderMap::new();
            headers.insert(name, value.parse().unwrap());
            headers
        };
        // Cloudflare tunnel to Caddy: the client is in CF-Connecting-IP, and X-Forwarded-For has only proxy hops.
        let mut both = with("cf-connecting-ip", "81.2.69.160");
        both.insert("x-forwarded-for", "172.20.0.3".parse().unwrap());
        assert_eq!(
            client_ip(&both, ip("127.0.0.1"), &trusted, CfConnectingIp),
            Some(ip("81.2.69.160"))
        );
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", " 81.2.69.160 "),
                ip("127.0.0.1"),
                &trusted,
                CfConnectingIp
            ),
            Some(ip("81.2.69.160"))
        );
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", "::ffff:81.2.69.160"),
                ip("127.0.0.1"),
                &trusted,
                CfConnectingIp
            ),
            Some(ip("81.2.69.160"))
        );
        // A missing, empty, oversized or unparseable value is the peer, as in backend/client_ip.py.
        let empty = HeaderMap::new();
        assert_eq!(
            client_ip(&empty, ip("127.0.0.1"), &trusted, CfConnectingIp),
            Some(ip("127.0.0.1"))
        );
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", ""),
                ip("127.0.0.1"),
                &trusted,
                CfConnectingIp
            ),
            Some(ip("127.0.0.1"))
        );
        let oversized = format!("81.2.69.160{}", " ".repeat(2040));
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", &oversized),
                ip("127.0.0.1"),
                &trusted,
                CfConnectingIp
            ),
            Some(ip("127.0.0.1"))
        );
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", "81.2.69.160, 10.9.9.9"),
                ip("127.0.0.1"),
                &trusted,
                CfConnectingIp
            ),
            Some(ip("127.0.0.1"))
        );
        // An untrusted peer is the client, whatever header it sends.
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", "198.51.100.9"),
                ip("203.0.113.8"),
                &trusted,
                CfConnectingIp
            ),
            Some(ip("203.0.113.8"))
        );
        // X-Forwarded-For mode ignores CF-Connecting-IP.
        assert_eq!(
            client_ip(
                &with("cf-connecting-ip", "81.2.69.160"),
                ip("127.0.0.1"),
                &trusted,
                XForwardedFor
            ),
            Some(ip("127.0.0.1"))
        );
    }

    #[test]
    fn client_ip_header_is_one_of_the_two_names_the_server_accepts() {
        use ClientIpHeader::{CfConnectingIp, XForwardedFor};
        assert_eq!(client_ip_header(""), Ok(XForwardedFor));
        assert_eq!(client_ip_header("  "), Ok(XForwardedFor));
        assert_eq!(client_ip_header(" X-Forwarded-For "), Ok(XForwardedFor));
        assert_eq!(client_ip_header("CF-Connecting-IP\n"), Ok(CfConnectingIp));
        assert!(client_ip_header("x-real-ip").is_err());
        assert!(client_ip_header("cf-connecting-ip,x-forwarded-for").is_err());
    }

    type Closes = tokio::sync::mpsc::UnboundedSender<Option<(u16, String)>>;

    /// A stand-in for the backend's stream route: refuses without a token, echoes, and closes on "close-me".
    async fn backend_stream(
        State(closes): State<Closes>,
        headers: HeaderMap,
        ws: axum::extract::WebSocketUpgrade,
    ) -> Response {
        if headers.get("authorization").and_then(|v| v.to_str().ok()) != Some("Bearer test-token") {
            return StatusCode::FORBIDDEN.into_response();
        }
        ws.on_upgrade(move |mut socket| async move {
            while let Some(Ok(message)) = socket.recv().await {
                match message {
                    Message::Text(text) if &*text == "close-me" => {
                        let close = CloseFrame {
                            code: 4100,
                            reason: "refused".into(),
                        };
                        let _ = socket.send(Message::Close(Some(close))).await;
                        return;
                    }
                    Message::Text(text) => {
                        let _ = socket.send(Message::Text(text)).await;
                    }
                    Message::Binary(bytes) => {
                        let _ = socket.send(Message::Binary(bytes)).await;
                    }
                    Message::Close(frame) => {
                        let seen = frame.map(|f| (f.code, f.reason.to_string()));
                        let _ = closes.send(seen);
                        return;
                    }
                    Message::Ping(_) | Message::Pong(_) => {}
                }
            }
        })
    }

    /// Starts the stand-in backend; its close frames arrive on the returned channel.
    async fn backend() -> (
        String,
        tokio::sync::mpsc::UnboundedReceiver<Option<(u16, String)>>,
    ) {
        let (closes, received) = tokio::sync::mpsc::unbounded_channel();
        let router = Router::new()
            .route("/api/v2/recorder/stream", get(backend_stream))
            .with_state(closes);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        (format!("http://{addr}"), received)
    }

    /// Starts the gateway routes with `python` as the backend, and returns the address they listen on.
    async fn gateway(python: String) -> SocketAddr {
        let app = App {
            hub: Arc::new(Mutex::new(Hub::new())),
            key: Arc::new(String::new()),
            python: Arc::new(python),
            http: reqwest::Client::new(),
            proxy: reqwest::Client::new(),
            trusted_proxies: Arc::new(trusted_proxies("127.0.0.1/32").unwrap()),
            client_ip_header: ClientIpHeader::XForwardedFor,
        };
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let router = routes(app).into_make_service_with_connect_info::<SocketAddr>();
        tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        addr
    }

    fn stream_request(
        gateway: SocketAddr,
        token: Option<&str>,
    ) -> tokio_tungstenite::tungstenite::handshake::client::Request {
        let url =
            format!("ws://{gateway}/api/v2/recorder/stream?encoding=linear16&sample_rate=16000");
        let mut request = url.into_client_request().unwrap();
        if let Some(token) = token {
            let value = format!("Bearer {token}").parse().unwrap();
            request.headers_mut().insert(header::AUTHORIZATION, value);
        }
        request
    }

    #[tokio::test]
    async fn recorder_stream_relays_frames_and_close() {
        let (python, mut closes) = backend().await;
        let gateway = gateway(python).await;
        let (mut client, _) =
            tokio_tungstenite::connect_async(stream_request(gateway, Some("test-token")))
                .await
                .unwrap();
        client
            .send(ClientMessage::Text("hello".into()))
            .await
            .unwrap();
        let reply = client.next().await.unwrap().unwrap();
        assert_eq!(reply, ClientMessage::Text("hello".into()));
        client
            .send(ClientMessage::Binary(vec![1, 2, 3].into()))
            .await
            .unwrap();
        let reply = client.next().await.unwrap().unwrap();
        assert_eq!(reply, ClientMessage::Binary(vec![1, 2, 3].into()));
        let close = ClientClose {
            code: CloseCode::from(4001),
            reason: "bye".into(),
        };
        client
            .send(ClientMessage::Close(Some(close)))
            .await
            .unwrap();
        let seen = closes.recv().await.unwrap();
        assert_eq!(seen, Some((4001, "bye".to_string())));
    }

    #[tokio::test]
    async fn recorder_stream_passes_backend_close_to_client() {
        let (python, _closes) = backend().await;
        let gateway = gateway(python).await;
        let (mut client, _) =
            tokio_tungstenite::connect_async(stream_request(gateway, Some("test-token")))
                .await
                .unwrap();
        client
            .send(ClientMessage::Text("close-me".into()))
            .await
            .unwrap();
        match client.next().await.unwrap().unwrap() {
            ClientMessage::Close(Some(frame)) => {
                assert_eq!(u16::from(frame.code), 4100);
                assert_eq!(&*frame.reason, "refused");
            }
            other => panic!("expected a close frame, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn recorder_stream_refused_without_token_is_403() {
        let (python, _closes) = backend().await;
        let gateway = gateway(python).await;
        match tokio_tungstenite::connect_async(stream_request(gateway, None)).await {
            Err(tokio_tungstenite::tungstenite::Error::Http(response)) => {
                assert_eq!(response.status(), StatusCode::FORBIDDEN);
            }
            other => panic!("expected an HTTP refusal, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn recorder_stream_unreachable_backend_is_502() {
        let closed = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let python = format!("http://{}", closed.local_addr().unwrap());
        drop(closed);
        let gateway = gateway(python).await;
        match tokio_tungstenite::connect_async(stream_request(gateway, Some("test-token"))).await {
            Err(tokio_tungstenite::tungstenite::Error::Http(response)) => {
                assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
            }
            other => panic!("expected an HTTP refusal, got {other:?}"),
        }
    }
}
