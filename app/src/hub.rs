//! What the shell asks the hub itself: the status line and what needs a person. The web view and
//! this client have separate cookie stores; remote builds copy only the cookies that belong to the
//! hub into each request, so the tray shares the signed-in Cloudflare Access session without
//! persisting or exposing it elsewhere.

use serde_json::Value;
use reqwest_updater::header::HeaderValue;
use tauri::{AppHandle, Manager, Runtime};
use url::Url;

use crate::config::Config;

#[derive(Clone, Debug, Default)]
pub struct HubStatus {
    pub reachable: bool,
    pub alive: bool,
    pub cloud: bool,
    pub running: usize,
    pub needs: usize,
    pub tick_age: Option<i64>,
}

impl HubStatus {
    pub fn headline(&self) -> String {
        if !self.reachable {
            return "Hub server unreachable".into();
        }
        match (self.cloud, self.alive) {
            (true, true) => "Cloud backend connected".into(),
            (true, false) => "Cloud backend unavailable".into(),
            (false, true) => format!("Dispatcher running · tick {}s ago", self.tick_age.unwrap_or(0)),
            (false, false) => "Dispatcher STOPPED".into(),
        }
    }
}

pub struct Client {
    config: Config,
    http: reqwest::Client,
}

impl Client {
    pub fn new(config: Config) -> Client {
        let http = reqwest::Client::builder().timeout(std::time::Duration::from_secs(30)).build().unwrap();
        Client { config, http }
    }

    pub(crate) fn cookie_header<R: Runtime>(&self, app: &AppHandle<R>, url: &Url) -> Option<String> {
        if self.config.is_local() || !same_origin(&self.config.hub, url) {
            return None;
        }
        let webview = app.get_webview_window("main")?;
        let cookies = webview.cookies_for_url(url.clone()).ok()?;
        let header = cookies.iter().map(|c| format!("{}={}", c.name(), c.value())).collect::<Vec<_>>().join("; ");
        (!header.is_empty()).then_some(header)
    }

    async fn get<R: Runtime>(&self, app: &AppHandle<R>, url: Url) -> Option<Value> {
        let mut request = self.http.get(url.clone());
        if let Some(cookie) = self.cookie_header(app, &url) {
            request = request.header("Cookie", cookie);
        }
        let response = request.send().await.ok()?;
        if !response.status().is_success() {
            return None;
        }
        response.json().await.ok()
    }

    pub async fn status<R: Runtime>(&self, app: &AppHandle<R>) -> HubStatus {
        let mut status = HubStatus::default();
        if let Some(st) = self.get(app, self.config.api("status")).await {
            status.reachable = true;
            status.cloud = st.get("cloud").and_then(Value::as_bool).unwrap_or(false);
            status.alive = status.cloud || st.get("dispatcher_alive").and_then(Value::as_bool).unwrap_or(false);
            status.running = st.get("active").and_then(Value::as_array).map(Vec::len).unwrap_or(0);
            status.tick_age = st.get("tick_age_s").and_then(Value::as_i64);
        }
        // The tray shows only the number: `count=1` answers with it instead of the full list
        // (over 100 KB every 30 s). An older hub ignores the query and sends the items.
        let needs = if status.cloud { self.config.api("v2/needs-you?count=1") } else { self.config.api("issues?needs_human=1") };
        if let Some(value) = self.get(app, needs).await {
            status.needs = if status.cloud {
                value.get("count").and_then(Value::as_u64).map(|n| n as usize)
                    .or_else(|| value.get("items").and_then(Value::as_array).map(Vec::len)).unwrap_or(0)
            } else {
                value.as_array().map(Vec::len).unwrap_or(0)
            };
        }
        status
    }
}

pub(crate) fn same_origin(a: &Url, b: &Url) -> bool {
    a.scheme() == b.scheme()
        && a.host_str() == b.host_str()
        && a.port_or_known_default() == b.port_or_known_default()
        && a.username().is_empty() && a.password().is_none()
        && b.username().is_empty() && b.password().is_none()
}

pub(crate) fn company_cookie_header(hub: &Url, request: &Url, cookie: &str) -> Option<HeaderValue> {
    if cookie.is_empty() || !same_origin(hub, request) { return None; }
    cookie.parse::<HeaderValue>().ok()
}

pub(crate) fn company_update_endpoint(hub: &Url, configured: Option<&str>) -> Result<Url, url::ParseError> {
    match configured.filter(|endpoint| *endpoint != crate::config::GENERIC_UPDATER) {
        Some(endpoint) => Url::parse(endpoint),
        None => hub.join("download/latest.json"),
    }
}

#[derive(Clone)]
pub(crate) struct CompanyUpdatePolicy {
    hub: Url,
    endpoint: Url,
    cookie: Option<HeaderValue>,
}

impl CompanyUpdatePolicy {
    pub(crate) fn new(hub: Url, endpoint: Url, cookie: Option<&str>) -> CompanyUpdatePolicy {
        let cookie = cookie.and_then(|cookie| company_cookie_header(&hub, &endpoint, cookie));
        CompanyUpdatePolicy { hub, endpoint, cookie }
    }

    pub(crate) fn endpoint(&self) -> &Url { &self.endpoint }

    pub(crate) fn has_cookie(&self) -> bool { self.cookie.is_some() }

    pub(crate) fn allows_download(&self, download: &Url) -> bool {
        !same_origin(&self.hub, &self.endpoint) || same_origin(&self.hub, download)
    }

    // A hub feed may carry its Access cookie for both manifest and artifact requests. It must
    // stay on the selected hub, so the same-origin download check and disabled redirects below
    // are used together. A configured public runner feed gets no hub credentials at all.
    pub(crate) fn configure_client(&self, builder: reqwest_updater::ClientBuilder) -> reqwest_updater::ClientBuilder {
        if !same_origin(&self.hub, &self.endpoint) { return builder; }
        let builder = builder.redirect(reqwest_updater::redirect::Policy::none());
        if let Some(cookie) = &self.cookie {
            let mut headers = reqwest_updater::header::HeaderMap::new();
            headers.insert(reqwest_updater::header::COOKIE, cookie.clone());
            builder.default_headers(headers)
        } else {
            builder
        }
    }
}

#[cfg(test)]
mod updater_auth_tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn company_update_endpoint_keeps_public_runner_and_maps_default_feed_to_selected_hub() {
        let hub = Url::parse("https://tico.example.test/team/").unwrap();
        assert_eq!(company_update_endpoint(&hub, Some(crate::config::GENERIC_UPDATER)).unwrap(),
                   hub.join("download/latest.json").unwrap());
        assert_eq!(company_update_endpoint(&hub, Some("https://runner.example.test/download/latest.json")).unwrap(),
                   Url::parse("https://runner.example.test/download/latest.json").unwrap());
        assert_eq!(company_update_endpoint(&hub, None).unwrap(), hub.join("download/latest.json").unwrap());
    }

    #[tokio::test]
    async fn selected_hub_updater_client_uses_cookie_for_feed_and_artifact_and_rejects_foreign_downloads() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let mut seen = Vec::new();
            for _ in 0..2 {
                let (mut socket, _) = listener.accept().unwrap();
                seen.push(read_request(&mut socket));
                socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").unwrap();
            }
            seen
        });
        let base = Url::parse(&format!("http://{address}/team/")).unwrap();
        let feed = base.join("download/latest.json").unwrap();
        let policy = CompanyUpdatePolicy::new(base.clone(), feed.clone(), Some("CF_Authorization=synthetic"));
        let client = policy.configure_client(reqwest_updater::Client::builder()).build().unwrap();
        for path in ["download/latest.json", "download/app/Tico.dmg.tar.gz"] {
            let url = base.join(path).unwrap();
            assert!(policy.allows_download(&url));
            let response = client.get(url).send().await.unwrap();
            assert_eq!(response.status(), reqwest_updater::StatusCode::OK);
        }
        let foreign = Url::parse("https://files.example.test/download/app.tar.gz").unwrap();
        assert!(!policy.allows_download(&foreign));
        let seen = server.join().unwrap();
        assert!(seen[0].starts_with("GET /team/download/latest.json "));
        assert!(seen[1].starts_with("GET /team/download/app/Tico.dmg.tar.gz "));
        assert!(seen.iter().all(|r| r.to_ascii_lowercase().contains("cookie: cf_authorization=synthetic")));
    }

    #[tokio::test]
    async fn selected_hub_update_client_does_not_follow_access_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let request = read_request(&mut socket);
            socket.write_all(b"HTTP/1.1 302 Found\r\nLocation: https://login.example.test/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
            request
        });
        let base = Url::parse(&format!("http://{address}/team/")).unwrap();
        let feed = base.join("download/latest.json").unwrap();
        let policy = CompanyUpdatePolicy::new(base, feed.clone(), Some("CF_Authorization=synthetic"));
        let client = policy.configure_client(reqwest_updater::Client::builder()).build().unwrap();
        let response = client.get(feed).send().await.unwrap();
        assert_eq!(response.status(), reqwest_updater::StatusCode::FOUND);
        assert!(server.join().unwrap().to_ascii_lowercase().contains("cookie: cf_authorization=synthetic"));
    }

    #[tokio::test]
    async fn public_selected_hub_feed_is_checked_without_a_webview_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let mut seen = Vec::new();
            for _ in 0..2 {
                let (mut socket, _) = listener.accept().unwrap();
                seen.push(read_request(&mut socket));
                socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").unwrap();
            }
            seen
        });
        let hub = Url::parse(&format!("http://{address}/team/")).unwrap();
        let feed = hub.join("download/latest.json").unwrap();
        let policy = CompanyUpdatePolicy::new(hub.clone(), feed.clone(), None);
        assert!(!policy.has_cookie());
        let client = policy.configure_client(reqwest_updater::Client::builder()).build().unwrap();
        for url in [feed, hub.join("download/app/Tico.dmg.tar.gz").unwrap()] {
            assert!(policy.allows_download(&url));
            assert_eq!(client.get(url).send().await.unwrap().status(), reqwest_updater::StatusCode::OK);
        }
        assert!(server.join().unwrap().iter().all(|request| !request.to_ascii_lowercase().contains("cookie:")));
    }

    #[tokio::test]
    async fn configured_public_runner_feed_and_download_never_receive_hub_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let mut seen = Vec::new();
            for _ in 0..2 {
                let (mut socket, _) = listener.accept().unwrap();
                seen.push(read_request(&mut socket));
                socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").unwrap();
            }
            seen
        });
        let hub = Url::parse("https://tico.example.test/team/").unwrap();
        let endpoint = Url::parse(&format!("http://{address}/download/latest.json")).unwrap();
        let policy = CompanyUpdatePolicy::new(hub, endpoint.clone(), Some("CF_Authorization=synthetic"));
        assert!(!policy.has_cookie());
        let artifact = Url::parse(&format!("http://{address}/download/app/Tico.dmg.tar.gz")).unwrap();
        assert!(policy.allows_download(&artifact));
        let client = policy.configure_client(reqwest_updater::Client::builder()).build().unwrap();
        for url in [endpoint, artifact] {
            let response = client.get(url).send().await.unwrap();
            assert_eq!(response.status(), reqwest_updater::StatusCode::OK);
        }
        let seen = server.join().unwrap();
        assert!(seen[0].starts_with("GET /download/latest.json "));
        assert!(seen[1].starts_with("GET /download/app/Tico.dmg.tar.gz "));
        assert!(seen.iter().all(|request| !request.to_ascii_lowercase().contains("cookie:")));
    }

    fn read_request(socket: &mut std::net::TcpStream) -> String {
        let mut request = Vec::new();
        let mut chunk = [0; 1024];
        loop {
            let n = socket.read(&mut chunk).unwrap();
            request.extend_from_slice(&chunk[..n]);
            if n == 0 || request.windows(4).any(|w| w == b"\r\n\r\n") { break; }
        }
        String::from_utf8_lossy(&request).to_string()
    }
}
