//! Server selection: process environment, build-time URL, saved address, then local setup.
use std::path::Path;
use url::Url;

pub const GENERIC_UPDATER: &str = "https://github.com/ticoteam/tico/releases/latest/download/latest.json";

#[derive(Clone, Debug)]
pub struct Config {
    pub hub: Url,
    pub local_token_file: Option<String>,
}

pub fn app_name() -> &'static str {
    option_env!("TICO_APP_NAME").filter(|s| !s.is_empty()).unwrap_or("Tico")
}

pub fn is_generic() -> bool {
    option_env!("TICO_HUB_URL").filter(|s| !s.trim().is_empty()).is_none()
}

pub fn resolve(env: Option<&str>, baked: Option<&str>, saved: Option<&str>) -> Result<Option<Url>, String> {
    env.into_iter().chain(baked).chain(saved).find(|s| !s.trim().is_empty()).map(configured_url).transpose()
}

pub fn validate_url(raw: &str) -> Result<Url, String> {
    let hub = configured_url(raw)?;
    let local = matches!(hub.host_str(), Some("localhost") | Some("127.0.0.1"));
    if hub.scheme() == "http" && !local {
        return Err("Use HTTPS (HTTP is allowed only for localhost or 127.0.0.1).".into());
    }
    Ok(hub)
}

fn configured_url(raw: &str) -> Result<Url, String> {
    let mut hub = Url::parse(raw.trim()).map_err(|_| "Enter a full server address, such as https://tico.example.com.")?;
    if hub.host_str().is_none() || !matches!(hub.scheme(), "https" | "http") {
        return Err("Use an HTTP or HTTPS server address.".into());
    }
    if !hub.username().is_empty() || hub.password().is_some() || hub.query().is_some() || hub.fragment().is_some() {
        return Err("Use a server address without a login, query or fragment.".into());
    }
    if !hub.path().ends_with('/') {
        hub.set_path(&format!("{}/", hub.path()));
    }
    Ok(hub)
}

pub async fn validate_server(raw: &str) -> Result<Url, String> {
    let hub = validate_url(raw)?;
    // No credentials, redirects or cookies: a health check must not leave the chosen server.
    let http = reqwest::Client::builder().timeout(std::time::Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none()).no_proxy().build().map_err(|_| "Could not connect. Try again.")?;
    let response = http.get(hub.join("healthz").unwrap()).send().await
        .map_err(|_| "Could not reach the server. Check the address and try again.")?;
    if !response.status().is_success() {
        return Err("The server did not answer its health check. Check the address.".into());
    }
    let body: serde_json::Value = response.json().await.map_err(|_| "This address did not answer as a Tico server.")?;
    if body.get("service").and_then(serde_json::Value::as_str) != Some("tico") {
        return Err("This address did not answer as a Tico server.".into());
    }
    Ok(hub)
}

impl Config {
    pub fn load(path: &Path) -> Result<Option<Config>, String> {
        let env = std::env::var("HUB_URL").ok();
        let saved = std::fs::read_to_string(path).ok();
        Self::load_selected(path, env.as_deref(), option_env!("TICO_HUB_URL"), saved.as_deref())
    }

    fn load_selected(path: &Path, env: Option<&str>, baked: Option<&str>, saved: Option<&str>) -> Result<Option<Config>, String> {
        let hub = resolve(env, baked, saved)?;
        if saved.is_none() && env.filter(|s| !s.trim().is_empty()).is_none()
            && baked.filter(|s| !s.trim().is_empty()).is_some() {
            if let Some(hub) = &hub {
                std::fs::create_dir_all(path.parent().unwrap())
                    .and_then(|_| std::fs::write(path, hub.as_str()))
                    .map_err(|_| "Could not save the server address. Check free disk space and folder permissions.")?;
            }
        }
        Ok(hub.map(Self::new))
    }

    pub fn new(hub: Url) -> Config {
        Config { hub, local_token_file: option_env!("TICO_LOCAL_TOKEN_FILE")
            .map(str::to_string).filter(|s| !s.is_empty()) }
    }

    pub fn is_local(&self) -> bool {
        matches!(self.hub.host_str(), Some("localhost") | Some("127.0.0.1"))
    }

    pub fn host(&self) -> String {
        self.hub.host_str().unwrap_or("").to_string()
    }

    pub fn api(&self, path: &str) -> Url {
        self.hub.join(&format!("api/{}", path.trim_start_matches('/'))).unwrap()
    }

    /// Read an optional local owner token on each load so rotations take effect without a rebuild.
    pub fn start_url(&self) -> Url {
        if !self.is_local() { return self.hub.clone(); }
        let Some(file) = &self.local_token_file else { return self.hub.clone() };
        let Ok(contents) = std::fs::read_to_string(file) else { return self.hub.clone() };
        let token = contents.trim();
        if token.is_empty() { return self.hub.clone(); }
        let mut url = self.hub.join("api/v2/local-signin").unwrap();
        url.query_pairs_mut().append_pair("token", token).append_pair("next", "/");
        url
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolution_order_and_first_run() {
        let env = "https://env.example.com";
        let baked = "https://baked.example.com";
        let saved = "https://saved.example.com";
        assert_eq!(resolve(Some(env), Some(baked), Some(saved)).unwrap().unwrap().host_str(), Some("env.example.com"));
        assert_eq!(resolve(Some(" "), Some(baked), Some(saved)).unwrap().unwrap().host_str(), Some("baked.example.com"));
        assert_eq!(resolve(None, Some(""), Some(saved)).unwrap().unwrap().host_str(), Some("saved.example.com"));
        assert!(resolve(None, None, None).unwrap().is_none());
        assert!(resolve(Some("invalid"), Some(baked), Some(saved)).is_err());
    }

    #[test]
    fn built_in_server_survives_a_later_generic_build() {
        let directory = std::env::temp_dir().join(format!("tico-config-{}", std::process::id()));
        let path = directory.join("server.txt");
        let _ = std::fs::remove_dir_all(&directory);
        Config::load_selected(&path, None, Some("https://team.example.com"), None).unwrap();
        let saved = std::fs::read_to_string(&path).unwrap();
        assert_eq!(Config::load_selected(&path, None, None, Some(&saved)).unwrap().unwrap().host(), "team.example.com");
        Config::load_selected(&path, None, Some("https://other.example.com"), Some(&saved)).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), saved);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn configured_http_servers_keep_working() {
        for values in [(Some("http://team.example.com"), None, None),
                       (None, Some("http://team.example.com"), None),
                       (None, None, Some("http://team.example.com"))] {
            assert_eq!(resolve(values.0, values.1, values.2).unwrap().unwrap().as_str(), "http://team.example.com/");
        }
    }

    #[test]
    fn only_secure_or_loopback_addresses() {
        for raw in ["https://tico.example.com", "http://localhost:8765", "http://127.0.0.1:8765/"] {
            assert!(validate_url(raw).is_ok(), "{raw}");
        }
        for raw in ["http://tico.example.com", "http://localhost.example.com", "ftp://localhost", "file:///tmp/a",
                    "https://ana:secret@example.com", "https://example.com?token=x", "https://example.com/#x", "example.com"] {
            assert!(validate_url(raw).is_err(), "{raw}");
        }
        assert_eq!(validate_url(" https://example.com/tico ").unwrap().as_str(), "https://example.com/tico/");
    }
}

#[cfg(test)]
mod health_tests {
    use super::*;
    use std::io::{Read, Write};

    async fn check(status: &str, body: &str) -> Result<Url, String> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = format!("http://{}", listener.local_addr().unwrap());
        let response = format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(std::time::Duration::from_secs(3))).unwrap();
            let mut request = [0; 4096];
            let count = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..count]).starts_with("GET /healthz HTTP/1.1"));
            stream.write_all(response.as_bytes()).unwrap();
        });
        let result = validate_server(&address).await;
        server.join().unwrap();
        result
    }

    #[tokio::test]
    async fn connect_requires_a_successful_tico_health_response() {
        assert!(check("200 OK", r#"{"service":"tico"}"#).await.is_ok());
        assert!(check("200 OK", r#"{"service":"other"}"#).await.is_err());
        assert!(check("200 OK", "invalid").await.is_err());
        assert!(check("503 Unavailable", r#"{"service":"tico"}"#).await.is_err());
        assert!(check("302 Found", "{}").await.is_err());
    }
}
