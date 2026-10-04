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

pub(crate) fn no_redirects(builder: reqwest_updater::ClientBuilder) -> reqwest_updater::ClientBuilder {
    builder.redirect(reqwest_updater::redirect::Policy::none())
}

#[cfg(test)]
mod updater_auth_tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn company_feed_and_artifact_paths_get_cookie_only_on_selected_origin() {
        let hub = Url::parse("https://tico.example.test/team/").unwrap();
        let feed = hub.join("download/latest.json").unwrap();
        let artifact = hub.join("download/app/Tico.dmg.tar.gz").unwrap();
        let foreign = Url::parse("https://files.example.test/download/app.tar.gz").unwrap();
        assert_eq!(company_cookie_header(&hub, &feed, "CF_Authorization=abc").unwrap().to_str().unwrap(), "CF_Authorization=abc");
        assert_eq!(company_cookie_header(&hub, &artifact, "CF_Authorization=abc").unwrap().to_str().unwrap(), "CF_Authorization=abc");
        assert!(company_cookie_header(&hub, &foreign, "CF_Authorization=abc").is_none());
        assert!(!same_origin(&feed, &Url::parse("http://tico.example.test/team/download/latest.json").unwrap()));
        assert!(!same_origin(&feed, &Url::parse("https://user@tico.example.test/team/download/latest.json").unwrap()));
    }

    #[tokio::test]
    async fn company_updater_http_client_does_not_follow_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = [0; 2048];
            let _ = socket.read(&mut request).unwrap();
            socket.write_all(b"HTTP/1.1 302 Found\r\nLocation: https://login.example.test/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        let client = no_redirects(reqwest_updater::Client::builder()).build().unwrap();
        let response = client.get(format!("http://{address}/download/latest.json"))
            .header("Cookie", "CF_Authorization=synthetic")
            .send().await.unwrap();
        assert_eq!(response.status(), reqwest_updater::StatusCode::FOUND);
        server.join().unwrap();
    }

    #[tokio::test]
    async fn authenticated_feed_and_artifact_requests_use_selected_origin_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let mut seen = Vec::new();
            for _ in 0..2 {
                let (mut socket, _) = listener.accept().unwrap();
                let mut request = Vec::new();
                let mut chunk = [0; 1024];
                loop {
                    let n = socket.read(&mut chunk).unwrap();
                    request.extend_from_slice(&chunk[..n]);
                    if n == 0 || request.windows(4).any(|w| w == b"\r\n\r\n") { break; }
                }
                seen.push(String::from_utf8_lossy(&request).to_string());
                socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").unwrap();
            }
            seen
        });
        let base = Url::parse(&format!("http://{address}/team/")).unwrap();
        let client = no_redirects(reqwest_updater::Client::builder()).build().unwrap();
        for path in ["download/latest.json", "download/app/Tico.dmg.tar.gz"] {
            let url = base.join(path).unwrap();
            let cookie = company_cookie_header(&base, &url, "CF_Authorization=synthetic").unwrap();
            let response = client.get(url).header(reqwest_updater::header::COOKIE, cookie).send().await.unwrap();
            assert_eq!(response.status(), reqwest_updater::StatusCode::OK);
        }
        let seen = server.join().unwrap();
        assert!(seen[0].starts_with("GET /team/download/latest.json "));
        assert!(seen[1].starts_with("GET /team/download/app/Tico.dmg.tar.gz "));
        assert!(seen.iter().all(|r| r.to_ascii_lowercase().contains("cookie: cf_authorization=synthetic")));
    }
}
