//! The hub client: the same contract as `clients/tico.py` `Client`.
//!
//! HTTPS only (loopback and one-label service names may use plain http), the origin URL alone,
//! `User-Agent: Tico-Client/0.2.1` (Cloudflare refuses a default agent), a Bearer token that may be a
//! `tico-file:` reference read fresh on every request, an `Idempotency-Key` on every request with a
//! body, kept across retries, no redirects (a login redirect is an auth failure, not a destination for
//! credentials), and `{"error": {code, detail, retryable}}` errors. 429, 5xx and network failures are
//! retried with the same key after `min(2^n, 5) + rand*0.2` seconds.

use std::net::IpAddr;
use std::path::Path;
use std::time::Duration;

use reqwest::{Method, StatusCode};
use serde_json::{json, Value};

pub const CLIENT_VERSION: &str = "0.2.1";

#[derive(Debug, Clone)]
pub struct ApiError {
    pub code: String,
    pub detail: String,
    pub status: u16,
    pub retryable: bool,
    pub operation_id: Option<String>,
}

impl ApiError {
    pub fn new(code: &str, detail: impl Into<String>, status: u16, retryable: bool) -> Self {
        ApiError { code: code.into(), detail: detail.into(), status, retryable, operation_id: None }
    }
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.detail)
    }
}

impl std::error::Error for ApiError {}

/// Where plain http is accepted: loopback, or a one-label name such as a compose service.
pub fn plain_http_host(url: &reqwest::Url) -> bool {
    match url.host() {
        Some(url::Host::Ipv4(ip)) => IpAddr::V4(ip).is_loopback(),
        Some(url::Host::Ipv6(ip)) => IpAddr::V6(ip).is_loopback(),
        Some(url::Host::Domain(name)) => {
            name == "localhost"
                || (!name.contains('.') && !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
        }
        None => false,
    }
}

/// The origin, validated the way `Client.__init__` does.
pub fn check_origin(url: &str) -> Result<String, String> {
    let parsed = reqwest::Url::parse(url).map_err(|e| format!("Invalid Tico URL: {e}"))?;
    let scheme = parsed.scheme();
    if scheme != "https" && !(scheme == "http" && plain_http_host(&parsed)) {
        return Err("Tico requires HTTPS, except on loopback or a one-label service name".into());
    }
    let raw_path = url.splitn(4, '/').nth(3).map(|p| format!("/{p}")).unwrap_or_default();
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !(raw_path.is_empty() || raw_path == "/")
    {
        return Err("Use the Tico origin URL without credentials, path, query, or fragment".into());
    }
    Ok(url.trim_end_matches('/').to_string())
}

/// Percent-encoding for a query value, as `urllib.parse.urlencode` does (quote_plus).
pub fn quote_plus(text: &str) -> String {
    let mut out = String::new();
    for b in text.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(b as char),
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

#[derive(Clone)]
pub struct Client {
    pub url: String,
    pub token: String,
    pub retries: u32,
    http: reqwest::Client,
    /// Multiplies the retry backoff; tests set it near zero.
    pub backoff_scale: f64,
}

pub fn http_client(timeout: Option<Duration>) -> reqwest::Client {
    let mut builder = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(10))
        .pool_idle_timeout(Duration::from_secs(60))
        .user_agent(format!("Tico-Client/{CLIENT_VERSION}"));
    if let Some(t) = timeout {
        // Like a socket timeout: each read may take this long, not the whole response.
        builder = builder.read_timeout(t);
    }
    builder.build().expect("http client")
}

/// The bearer token to send: a `tico-file:` reference is read now, so a warm process never holds an
/// expired per-turn token.
pub fn bearer(token: &str) -> Result<String, ApiError> {
    if let Some(path) = token.strip_prefix("tico-file:") {
        let read = std::fs::read_to_string(Path::new(path)).map(|t| t.trim().to_string()).unwrap_or_default();
        if read.is_empty() {
            return Err(ApiError::new("stale_lease", "No active Hub turn; wait for a new request", 409, false));
        }
        return Ok(read);
    }
    Ok(token.to_string())
}

/// `{"error": {...}}` from a refusal, else the Cloudflare problem-detail `title`, else "HTTP N".
pub fn error_from(status: u16, body: &[u8], key: &str) -> ApiError {
    let payload: Value = serde_json::from_slice(body).unwrap_or(Value::Null);
    let payload = if payload.is_object() { payload } else { json!({}) };
    let error = payload.get("error").filter(|e| e.is_object()).cloned().unwrap_or(json!({}));
    let detail = match error.get("detail") {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(v) if !v.is_null() && v.as_str() != Some("") && v != &Value::Bool(false) => v.to_string(),
        _ => {
            let mut d = format!("HTTP {status}");
            if let Some(title) = payload.get("title").filter(|t| !t.is_null() && t.as_str() != Some("")) {
                let title = match title {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                d.push_str(": ");
                d.push_str(&title.chars().take(200).collect::<String>());
            }
            d
        }
    };
    let code = error.get("code").and_then(|c| c.as_str()).unwrap_or("http_error");
    let retryable = status == 429 || (500..600).contains(&status);
    let mut e = ApiError::new(code, detail, status, retryable);
    e.operation_id = Some(key.to_string());
    e
}

impl Client {
    pub fn new(url: &str, token: &str, timeout_s: u64, retries: u32) -> Result<Client, String> {
        let url = check_origin(url)?;
        Ok(Client {
            url,
            token: token.to_string(),
            retries,
            http: http_client(Some(Duration::from_secs(timeout_s))),
            backoff_scale: 1.0,
        })
    }

    pub async fn get(&self, path: &str) -> Result<Value, ApiError> {
        self.request(Method::GET, &format!("/api/v2/{path}"), None, None).await
    }

    #[allow(dead_code)] // the status and doctor reads with a query come in step 2
    pub async fn get_query(&self, path: &str, query: &[(&str, String)]) -> Result<Value, ApiError> {
        let suffix = if query.is_empty() {
            String::new()
        } else {
            "?".to_string()
                + &query.iter().map(|(k, v)| format!("{}={}", quote_plus(k), quote_plus(v))).collect::<Vec<_>>().join("&")
        };
        self.request(Method::GET, &format!("/api/v2/{path}{suffix}"), None, None).await
    }

    pub async fn post(&self, path: &str, body: &Value, key: Option<&str>) -> Result<Value, ApiError> {
        self.request(Method::POST, &format!("/api/v2/{path}"), Some(body), key.map(|k| k.to_string())).await
    }

    pub async fn patch(&self, path: &str, body: &Value) -> Result<Value, ApiError> {
        self.request(Method::PATCH, &format!("/api/v2/{path}"), Some(body), None).await
    }

    pub async fn request(&self, method: Method, path: &str, body: Option<&Value>, key: Option<String>) -> Result<Value, ApiError> {
        let data = body.map(|b| (serde_json::to_vec(b).unwrap_or_default(), "application/json"));
        self.send(method, path, data, key).await
    }

    /// A POST of raw bytes (`application/octet-stream`), as `Client.request(raw=...)`: a file upload.
    pub async fn post_raw(&self, path: &str, data: Vec<u8>, key: &str) -> Result<Value, ApiError> {
        self.send(Method::POST, path, Some((data, "application/octet-stream")), Some(key.to_string())).await
    }

    async fn send(&self, method: Method, path: &str, data: Option<(Vec<u8>, &'static str)>, key: Option<String>) -> Result<Value, ApiError> {
        if !path.starts_with("/api/v2/") {
            return Err(ApiError::new("protocol", "Use a versioned Tico API path", 0, false));
        }
        let key = key.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let token = bearer(&self.token)?;
        // A task number ("tasks/#18945") is in the path, not a fragment.
        let path = path.replace('#', "%23");
        let mut attempt = 0u32;
        loop {
            let mut req = self
                .http
                .request(method.clone(), format!("{}{}", self.url, path))
                .header("Authorization", format!("Bearer {token}"))
                .header("Accept", "application/json");
            if let Some((d, content_type)) = &data {
                req = req.header("Content-Type", *content_type).header("Idempotency-Key", &key).body(d.clone());
            }
            let failure = match req.send().await {
                Ok(resp) => {
                    let status = resp.status();
                    match resp.bytes().await {
                        Ok(bytes) if status.is_success() => match serde_json::from_slice::<Value>(&bytes) {
                            Ok(v) => return Ok(v),
                            Err(_) => {
                                let mut e = ApiError::new(
                                    "protocol",
                                    "Tico returned an invalid API response; check the endpoint and sign-in configuration",
                                    0,
                                    false,
                                );
                                e.operation_id = Some(key.clone());
                                e
                            }
                        },
                        Ok(bytes) => error_from(status.as_u16(), &bytes, &key),
                        Err(_) if !status.is_success() && status != StatusCode::OK => error_from(status.as_u16(), b"", &key),
                        Err(_) => unavailable(&key),
                    }
                }
                Err(_) => unavailable(&key),
            };
            if !failure.retryable || attempt >= self.retries {
                return Err(failure);
            }
            let jitter = (uuid::Uuid::new_v4().as_u128() % 1000) as f64 / 1000.0 * 0.2;
            let wait = (2f64.powi(attempt as i32)).min(5.0) + jitter;
            tokio::time::sleep(Duration::from_secs_f64(wait * self.backoff_scale)).await;
            attempt += 1;
        }
    }
}

fn unavailable(key: &str) -> ApiError {
    let mut e = ApiError::new("unavailable", "Tico could not confirm this request; retry with the same operation ID", 0, true);
    e.operation_id = Some(key.to_string());
    e
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// A one-connection-per-response HTTP server answering `replies` in order, recording each request's head.
    pub(crate) async fn serve(replies: Vec<(u16, &'static str)>) -> (String, Arc<Mutex<Vec<String>>>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let seen = Arc::new(Mutex::new(Vec::new()));
        let log = seen.clone();
        tokio::spawn(async move {
            for (status, body) in replies {
                let (mut sock, _) = listener.accept().await.unwrap();
                let mut buf = vec![0u8; 65536];
                let mut got = Vec::new();
                loop {
                    let n = sock.read(&mut buf).await.unwrap();
                    got.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    if let Some(end) = text.find("\r\n\r\n") {
                        let length = text[..end]
                            .lines()
                            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap()))
                            .unwrap_or(0);
                        if got.len() >= end + 4 + length {
                            break;
                        }
                    }
                    if n == 0 {
                        break;
                    }
                }
                log.lock().unwrap().push(String::from_utf8_lossy(&got).to_string());
                let reply = format!(
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                sock.write_all(reply.as_bytes()).await.unwrap();
                sock.shutdown().await.ok();
            }
        });
        (format!("http://127.0.0.1:{}", addr.port()), seen)
    }

    fn header(request: &str, name: &str) -> Option<String> {
        request.lines().find_map(|l| {
            let (k, v) = l.split_once(':')?;
            (k.eq_ignore_ascii_case(name)).then(|| v.trim().to_string())
        })
    }

    #[tokio::test]
    async fn a_5xx_then_success_retries_with_the_same_idempotency_key() {
        let (url, seen) = serve(vec![(503, "{}"), (429, "{}"), (200, "{\"ok\":true}")]).await;
        let mut client = Client::new(&url, "runner-token", 5, 3).unwrap();
        client.backoff_scale = 0.001;
        let got = client.post("jobs/claim", &json!({"next_run": true}), None).await.unwrap();
        assert_eq!(got, json!({"ok": true}));
        let seen = seen.lock().unwrap();
        assert_eq!(seen.len(), 3);
        let keys: Vec<_> = seen.iter().map(|r| header(r, "Idempotency-Key").unwrap()).collect();
        assert!(keys.iter().all(|k| k == &keys[0]) && keys[0].len() == 36);
        assert_eq!(header(&seen[0], "User-Agent").unwrap(), "Tico-Client/0.2.1");
        assert_eq!(header(&seen[0], "Authorization").unwrap(), "Bearer runner-token");
    }

    #[tokio::test]
    async fn a_4xx_is_not_retried_and_its_error_is_parsed() {
        let (url, seen) = serve(vec![(409, r#"{"error":{"code":"stale_lease","detail":"Lease expired","retryable":false}}"#)]).await;
        let mut client = Client::new(&url, "t", 5, 3).unwrap();
        client.backoff_scale = 0.001;
        let err = client.post("attempts/a/renew", &json!({}), Some("renew:a")).await.unwrap_err();
        assert_eq!((err.code.as_str(), err.detail.as_str(), err.status, err.retryable), ("stale_lease", "Lease expired", 409, false));
        assert_eq!(header(&seen.lock().unwrap()[0], "Idempotency-Key").unwrap(), "renew:a");
    }

    #[tokio::test]
    async fn retries_stop_after_the_budget_and_a_cloudflare_title_is_kept() {
        let (url, seen) = serve(vec![(530, r#"{"title":"Error 1033"}"#), (530, r#"{"title":"Error 1033"}"#)]).await;
        let mut client = Client::new(&url, "t", 5, 1).unwrap();
        client.backoff_scale = 0.001;
        let err = client.get("runners/assignments").await.unwrap_err();
        assert_eq!(err.detail, "HTTP 530: Error 1033");
        assert!(err.retryable);
        assert_eq!(seen.lock().unwrap().len(), 2);
        assert!(header(&seen.lock().unwrap()[0], "Idempotency-Key").is_none(), "a GET carries no body and no key");
    }

    #[tokio::test]
    async fn a_redirect_is_an_error_not_a_destination() {
        let (url, seen) = serve(vec![(302, "{}")]).await;
        let client = Client::new(&url, "t", 5, 3).unwrap();
        let err = client.get("config").await.unwrap_err();
        assert_eq!((err.status, err.retryable), (302, false));
        assert_eq!(seen.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn a_network_failure_is_retryable_and_says_so() {
        let mut client = Client::new("http://127.0.0.1:9", "t", 1, 1).unwrap();
        client.backoff_scale = 0.001;
        let err = client.post("x", &json!({}), Some("k")).await.unwrap_err();
        assert_eq!((err.code.as_str(), err.retryable), ("unavailable", true));
        assert_eq!(err.operation_id.as_deref(), Some("k"));
    }

    #[tokio::test]
    async fn an_empty_tico_file_token_is_a_stale_lease() {
        let dir = std::env::temp_dir().join(format!("tico-client-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("token");
        std::fs::write(&file, "").unwrap();
        let client = Client::new("http://127.0.0.1:9", &format!("tico-file:{}", file.display()), 1, 0).unwrap();
        let err = client.get("config").await.unwrap_err();
        assert_eq!((err.code.as_str(), err.status), ("stale_lease", 409));
        std::fs::write(&file, "fresh\n").unwrap();
        assert_eq!(bearer(&client.token).unwrap(), "fresh");
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn only_an_https_origin_or_a_local_host_is_accepted() {
        assert!(check_origin("https://tico.acme.example").is_ok());
        assert!(check_origin("http://127.0.0.1:8000").is_ok());
        assert!(check_origin("http://localhost:8000/").is_ok());
        assert!(check_origin("http://hub:8000").is_ok());
        assert!(check_origin("http://[::1]:8000").is_ok());
        assert!(check_origin("http://10.0.0.5").is_err());
        assert!(check_origin("http://tico.acme.example").is_err());
        assert!(check_origin("https://tico.acme.example/api").is_err());
        assert!(check_origin("https://u:p@tico.acme.example").is_err());
        assert!(check_origin("https://tico.acme.example?x=1").is_err());
    }
}
