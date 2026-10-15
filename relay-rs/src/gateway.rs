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
use std::net::SocketAddr;

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
    // Match Uvicorn's loopback proxy trust. Direct remote callers cannot invent client identities.
    if !peer.ip().is_loopback() {
        for name in ["forwarded", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip"] {
            parts.headers.remove(name);
        }
        parts.headers.insert("x-forwarded-for", peer.ip().to_string().parse().unwrap());
        parts.headers.insert("x-forwarded-proto", "http".parse().unwrap());
    }
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
