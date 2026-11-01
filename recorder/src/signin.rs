//! "Sign in with Tico": Tico's agent sign-in (backend/mcp_oauth.py, OAuth 2.1 + PKCE) with a loopback
//! redirect, so nobody copies a token. The result is an ordinary personal token, refreshed before it expires;
//! it shows in the person's API tokens list and Revoke there ends it.
use crate::{
    network::{self, Tico},
    settings::{self, Destination},
};
use anyhow::{Context, Result, bail, ensure};
use base64::Engine;
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    io::{BufRead, BufReader, Write},
    net::TcpListener,
    sync::Mutex,
    time::{Duration, Instant},
};

const PREFIX: &str = "/api/v2/oauth";
const WAIT: Duration = Duration::from_secs(300);

/// What renews a signed-in connection; kept in the keychain beside its access token.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Grant {
    pub client_id: String,
    pub token_url: String,
    pub refresh_token: String,
    /// Unix seconds.
    pub expires_at: i64,
}

struct Server {
    runner: String,
    authorize: String,
    token: String,
    register: String,
}

/// The address people know (example.com) or its runner. A sign-in proxy guards the first, so try `runner.` too.
fn discover(address: &str) -> Result<Server> {
    let address = address.trim().trim_end_matches('/');
    let base = if address.contains("://") {
        address.to_owned()
    } else if address.starts_with("localhost") || address.starts_with("127.0.0.1") {
        format!("http://{address}")
    } else {
        format!("https://{address}")
    };
    let base = settings::safe_url(&base)?;
    let host = Url::parse(&base)?.host_str().unwrap_or_default().to_owned();
    let mut candidates = vec![base.clone()];
    if !host.starts_with("runner.") && host.contains('.') && base.starts_with("https://") {
        candidates.push(base.replacen(&host, &format!("runner.{host}"), 1));
    }
    let client = network::client()?;
    for candidate in candidates {
        let url = format!("{candidate}{PREFIX}/.well-known/oauth-authorization-server");
        let Ok(response) = client.get(&url).send() else {
            continue;
        };
        if !response.status().is_success() {
            continue;
        }
        let Ok(meta) = response.json::<Value>() else {
            continue;
        };
        let field = |name: &str| meta[name].as_str().unwrap_or_default().to_owned();
        let runner = field("issuer")
            .strip_suffix(PREFIX)
            .map(str::to_owned)
            .context("This Tico's sign-in address is not one the recorder understands")?;
        let runner = settings::safe_url(&runner)?;
        let server = Server {
            authorize: settings::safe_url(&field("authorization_endpoint"))?,
            token: field("token_endpoint"),
            register: field("registration_endpoint"),
            runner,
        };
        ensure!(
            server.token.starts_with(&server.runner) && server.register.starts_with(&server.runner),
            "This Tico's sign-in address is not one the recorder understands"
        );
        return Ok(server);
    }
    bail!("No Tico found at {address}. Check the address you use in the browser")
}

fn form(pairs: &[(&str, &str)]) -> Result<String> {
    Ok(Url::parse_with_params("http://form/", pairs)?
        .query()
        .unwrap_or_default()
        .to_owned())
}

fn oauth_post(url: &str, body: String, content_type: &str) -> Result<Value> {
    let response = network::client()?
        .post(url)
        .header("Content-Type", content_type)
        .body(body)
        .send()?;
    let status = response.status();
    let value: Value = response.json().unwrap_or_default();
    if !status.is_success() {
        let reason = value["error_description"]
            .as_str()
            .or(value["error"].as_str())
            .unwrap_or("request failed");
        bail!("Tico sign-in failed: {reason}");
    }
    Ok(value)
}

fn grant_from(value: &Value, client_id: &str, token_url: &str) -> Result<(String, Grant)> {
    let access = value["access_token"].as_str().unwrap_or_default();
    let refresh = value["refresh_token"].as_str().unwrap_or_default();
    ensure!(
        !access.is_empty() && !refresh.is_empty(),
        "Tico sign-in returned no token"
    );
    Ok((
        access.into(),
        Grant {
            client_id: client_id.into(),
            token_url: token_url.into(),
            refresh_token: refresh.into(),
            expires_at: chrono::Utc::now().timestamp()
                + value["expires_in"].as_i64().unwrap_or(3600),
        },
    ))
}

fn random() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

/// The one browser hop: wait for Tico to send the browser back with a code (or a refusal).
fn callback(listener: &TcpListener, state: &str, issuer: &str) -> Result<String> {
    listener.set_nonblocking(true)?;
    let started = Instant::now();
    loop {
        ensure!(
            started.elapsed() < WAIT,
            "Sign-in timed out. Try again and choose Allow in the browser"
        );
        let mut stream = match listener.accept() {
            Ok((stream, _)) => stream,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(150));
                continue;
            }
            Err(error) => return Err(error.into()),
        };
        stream.set_nonblocking(false)?;
        stream.set_read_timeout(Some(Duration::from_secs(5)))?;
        let mut line = String::new();
        BufReader::new(&stream).read_line(&mut line)?;
        let path = line.split_whitespace().nth(1).unwrap_or_default();
        let Ok(url) = Url::parse(&format!("http://127.0.0.1{path}")) else {
            continue;
        };
        if url.path() != "/callback" {
            let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
            continue;
        }
        let query: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
        let get = |name: &str| query.get(name).cloned().unwrap_or_default();
        let ok = get("state") == state && (get("iss").is_empty() || get("iss") == issuer);
        let code = get("code");
        let message = if ok && !code.is_empty() {
            "Signed in. You can close this tab and go back to Tico Recorder."
        } else {
            "Not signed in. You can close this tab."
        };
        let page = format!(
            "<!doctype html><meta charset=utf-8><title>Tico Recorder</title>\
             <body style=\"font:15px -apple-system,sans-serif;padding:48px\">{message}</body>"
        );
        let _ = write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{page}",
            page.len()
        );
        ensure!(ok, "Sign-in answer did not match this request. Try again");
        if get("error") == "access_denied" {
            bail!("Sign-in was cancelled in the browser");
        }
        ensure!(!code.is_empty(), "Tico did not sign in: {}", get("error"));
        return Ok(code);
    }
}

/// Sign in to the Tico at `address` in the browser; returns the new connection, its access token and grant.
pub fn sign_in(address: &str, open: impl FnOnce(&str)) -> Result<(Destination, String, Grant)> {
    let server = discover(address)?;
    let listener =
        TcpListener::bind("127.0.0.1:0").context("Could not listen for the sign-in answer")?;
    let redirect = format!(
        "http://127.0.0.1:{}/callback",
        listener.local_addr()?.port()
    );
    let registered = oauth_post(
        &server.register,
        json!({"client_name":"Tico Recorder","redirect_uris":[redirect],"token_endpoint_auth_method":"none"})
            .to_string(),
        "application/json",
    )?;
    let client_id = registered["client_id"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    ensure!(!client_id.is_empty(), "Tico did not register the recorder");
    let verifier = random();
    let challenge = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .encode(Sha256::digest(verifier.as_bytes()));
    let state = uuid::Uuid::new_v4().simple().to_string();
    let url = Url::parse_with_params(
        &server.authorize,
        [
            ("response_type", "code"),
            ("client_id", client_id.as_str()),
            ("redirect_uri", redirect.as_str()),
            ("code_challenge", challenge.as_str()),
            ("code_challenge_method", "S256"),
            ("state", state.as_str()),
        ],
    )?;
    open(url.as_str());
    let code = callback(&listener, &state, &format!("{}{PREFIX}", server.runner))?;
    let issued = oauth_post(
        &server.token,
        form(&[
            ("grant_type", "authorization_code"),
            ("code", &code),
            ("redirect_uri", &redirect),
            ("client_id", &client_id),
            ("code_verifier", &verifier),
        ])?,
        "application/x-www-form-urlencoded",
    )?;
    let (access, grant) = grant_from(&issued, &client_id, &server.token)?;
    let host = Url::parse(&server.authorize)?
        .host_str()
        .unwrap_or_default()
        .to_owned();
    let mut destination = Destination {
        id: uuid::Uuid::new_v4().to_string(),
        name: host,
        base_url: server.runner,
        actor: String::new(),
    };
    let who = Tico::new(destination.clone(), access.clone())?.verify()?;
    destination.actor = who["actor"].as_str().unwrap_or_default().into();
    Ok((destination, access, grant))
}

/// Keep a signed-in connection's grant beside its token.
pub fn save(id: &str, access: &str, grant: &Grant) -> Result<()> {
    settings::save_secret(&format!("tico:{id}"), access)?;
    settings::save_secret(&format!("tico-grant:{id}"), &serde_json::to_string(grant)?)
}

/// The access token for a connection, renewed first when a signed-in one is about to expire.
pub fn token(id: &str) -> Result<String> {
    static RENEWING: Mutex<()> = Mutex::new(());
    let stored = settings::secret(&format!("tico-grant:{id}"))?;
    if stored.is_empty() {
        return settings::secret(&format!("tico:{id}"));
    }
    // One renewal at a time: Tico rotates the refresh token, so a second concurrent use would be refused.
    let _guard = RENEWING.lock().unwrap_or_else(|error| error.into_inner());
    let grant: Grant = serde_json::from_str(&settings::secret(&format!("tico-grant:{id}"))?)?;
    if grant.expires_at > chrono::Utc::now().timestamp() + 300 {
        return settings::secret(&format!("tico:{id}"));
    }
    let (access, renewed) = renew(&grant)?;
    save(id, &access, &renewed)?;
    Ok(access)
}

/// Forget a connection on this Mac, and end its sign-in in Tico when it has one (best effort: the
/// connection is removed even when Tico cannot be reached; Revoke in Tico ends it then).
pub fn sign_out(destination: &Destination) -> Result<()> {
    let id = &destination.id;
    let stored = settings::secret(&format!("tico-grant:{id}"))?;
    settings::save_secret(&format!("tico:{id}"), "")?;
    settings::save_secret(&format!("tico-grant:{id}"), "")?;
    // A pasted personal token has no grant; the person revokes it in Tico.
    let Ok(grant) = serde_json::from_str::<Grant>(&stored) else {
        return Ok(());
    };
    oauth_post(
        &format!("{}{PREFIX}/revoke", destination.base_url),
        form(&[("token", &grant.refresh_token)])?,
        "application/x-www-form-urlencoded",
    )
    .context("Signed out on this Mac, but Tico could not be reached to end the sign-in")?;
    Ok(())
}
/// Trade a grant's refresh token for a new access token; Tico rotates both.
pub fn renew(grant: &Grant) -> Result<(String, Grant)> {
    let issued = oauth_post(
        &grant.token_url,
        form(&[
            ("grant_type", "refresh_token"),
            ("refresh_token", &grant.refresh_token),
            ("client_id", &grant.client_id),
        ])?,
        "application/x-www-form-urlencoded",
    )
    .context("Tico sign-in expired. Sign in again in Settings")?;
    grant_from(&issued, &grant.client_id, &grant.token_url)
}
/// Open a URL in the default browser.
pub fn open_browser(url: &str) {
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(url).spawn();
    #[cfg(target_os = "windows")]
    let _ = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", url])
        .spawn();
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let _ = std::process::Command::new("xdg-open").arg(url).spawn();
}
