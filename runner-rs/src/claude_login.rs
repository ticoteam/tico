//! Full Claude logins on this computer (backend/claude_login.py): what a plan signed in with `claude auth login`
//! needs from the computers that run its turns.
//!
//! - Tokens: each running turn holds only a short-lived access token in its own `.credentials.json`
//!   (model_subscriptions.rs). When the plan was refreshed elsewhere the server hands the new one, and every home of
//!   that plan here is rewritten at once (`model_subscriptions::rewrite`).
//! - Refreshes: the server leases one refresh per plan to one computer when less than an hour is left. The refresh
//!   runs `claude auth status --json` on a copy of the login in a folder of its own (refresh tokens are single-use,
//!   and a spent one blanks the file it is in), rewrites this computer's own turns first, keeps the new login on disk
//!   (`pending-<lease>.json`, 0600, the supervisor's state) until the server acknowledged it, and sends it again until
//!   it has: the server keeps one result per lease, so a resend is harmless.
//! - Usage: the server schedules each plan's usage read (`GET /api/oauth/usage`, what Claude Code's `/usage` reads)
//!   and hands it to one computer with the plan's access token. Only fixed states and the windows' numbers travel
//!   back; a 429 carries its Retry-After, and nothing here ever marks the plan for Reconnect.
//!
//! Never logs a token, a login or the provider's text.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::client::ApiError;
use crate::runner::Runner;
use crate::util::text_of;

pub const CAPABILITY: &str = "claude_login_v1";
/// How often the relay asks while the event stream is down (relays.rs `wants`).
pub const POLL_S: f64 = 60.0;
const USAGE_URL: &str = "https://api.anthropic.com/api/oauth/usage";
const REFRESH_TIMEOUT: Duration = Duration::from_secs(60);
const USAGE_TIMEOUT: Duration = Duration::from_secs(20);
/// An older server without the route is asked again after this long.
const MISSING_FOR: Duration = Duration::from_secs(600);
/// A job done here is not done again for this long, whatever the server hands.
const REMEMBER: Duration = Duration::from_secs(30 * 60);
const MAX_JOBS: usize = 4;

#[derive(Default)]
pub struct Logins {
    /// Leases and usage requests already answered here.
    done: HashMap<String, Instant>,
    missing: Option<Instant>,
}

impl Logins {
    /// One pass: send what is waiting on disk, then (when asked, or turns here run on a full login) ask the server
    /// for new tokens and jobs and do them.
    pub async fn poll(&mut self, runner: &Arc<Runner>, ask: bool) -> Result<(), ApiError> {
        let dir = runner.config.state_dir().join("claude-logins");
        flush(runner, &dir).await?;
        let live = crate::model_subscriptions::live_logins();
        if !ask || self.missing.is_some_and(|at| at.elapsed() < MISSING_FOR) {
            return Ok(());
        }
        let body = json!({"live": live
            .iter()
            .map(|(id, revision)| json!({"subscription": id, "revision": revision}))
            .collect::<Vec<_>>()});
        let answer = match runner
            .client
            .post("runner-claude-logins", &body, None)
            .await
        {
            Ok(a) => a,
            Err(e) if [404, 405, 422].contains(&e.status) => {
                self.missing = Some(Instant::now());
                return Ok(());
            }
            Err(e) => return Err(e),
        };
        self.missing = None;
        self.done.retain(|_, at| at.elapsed() < REMEMBER);
        for token in answer["tokens"].as_array().into_iter().flatten() {
            let (id, value) = (text_of(&token["subscription"]), text_of(&token["value"]));
            if let Some(revision) = token["revision"].as_i64() {
                crate::model_subscriptions::rewrite(&id, revision, &value);
            }
        }
        for job in answer["refreshes"]
            .as_array()
            .into_iter()
            .flatten()
            .take(MAX_JOBS)
        {
            let lease = text_of(&job["lease"]);
            if !safe(&lease)
                || self.done.contains_key(&lease)
                || pending_path(&dir, job).is_some_and(|p| p.exists())
            {
                continue; // done here already: only its upload may still be owed
            }
            self.done.insert(lease, Instant::now());
            refresh(runner, &dir, job).await;
        }
        flush(runner, &dir).await?;
        for job in answer["usage"]
            .as_array()
            .into_iter()
            .flatten()
            .take(MAX_JOBS)
        {
            let id = text_of(&job["id"]);
            let sid = text_of(&job["subscription"]);
            if !safe(&id) || !safe(&sid) || self.done.contains_key(&id) {
                continue;
            }
            self.done.insert(id.clone(), Instant::now());
            let mut result = read_usage(&text_of(&job["token"])).await;
            result["id"] = json!(id);
            if let Err(e) = runner
                .client
                .post(&format!("runner-claude-logins/{sid}/usage"), &result, None)
                .await
            {
                if e.retryable {
                    return Err(e);
                }
            }
        }
        Ok(())
    }
}

fn safe(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 80
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

fn pending_path(dir: &Path, job: &Value) -> Option<PathBuf> {
    let (sid, lease) = (text_of(&job["subscription"]), text_of(&job["lease"]));
    (safe(&sid) && safe(&lease)).then(|| dir.join(&sid).join(format!("pending-{lease}.json")))
}

/// Write `text` 0600, whole or not at all, in the supervisor's own state.
fn keep(path: &Path, text: &str) -> Result<(), String> {
    use std::io::Write;
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
    let parent = path.parent().ok_or("no folder")?;
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(parent)
        .map_err(|e| e.to_string())?;
    let staged = path.with_extension(format!("tico-{}", uuid::Uuid::new_v4().simple()));
    let written = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&staged)
        .and_then(|mut f| f.write_all(text.as_bytes()).and_then(|_| f.sync_all()))
        .and_then(|_| std::fs::rename(&staged, path));
    if written.is_err() {
        let _ = std::fs::remove_file(&staged);
    }
    written.map_err(|e| e.to_string())
}

/// Send every refreshed login still on disk. The server answers each lease once and the same way again after
/// (backend/claude_login.py refresh_result), so any answer, kept or not, ends it; a network failure keeps it.
async fn flush(runner: &Runner, dir: &Path) -> Result<(), ApiError> {
    for sub in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        let sid = sub.file_name().to_string_lossy().to_string();
        for entry in std::fs::read_dir(sub.path())
            .into_iter()
            .flatten()
            .flatten()
        {
            let name = entry.file_name().to_string_lossy().to_string();
            if !(name.starts_with("pending-") && name.ends_with(".json")) || !safe(&sid) {
                continue;
            }
            let Some(body) = std::fs::read_to_string(entry.path())
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            else {
                let _ = std::fs::remove_file(entry.path());
                continue;
            };
            match runner
                .client
                .post(
                    &format!("runner-claude-logins/{sid}/refreshed"),
                    &body,
                    None,
                )
                .await
            {
                Ok(_) => {
                    let _ = std::fs::remove_file(entry.path());
                }
                Err(e) if !e.retryable && e.status != 0 && e.status != 429 && e.status < 500 => {
                    let _ = std::fs::remove_file(entry.path());
                }
                Err(e) => return Err(e),
            }
        }
    }
    Ok(())
}

/// A turn's `.credentials.json` for a login: its OAuth part without the refresh token
/// (backend/claude_login.py turn_file).
pub fn turn_file(value: &Value) -> Option<String> {
    let mut oauth = value.get("claudeAiOauth")?.as_object()?.clone();
    oauth.remove("refreshToken");
    Some(json!({"claudeAiOauth": oauth}).to_string())
}

/// What a refresh found: the new login (the old one's account kept when the CLI left none), else whether the login
/// is gone (`rejected`) or only not refreshed this time (`failed`).
pub fn refreshed(
    old: &Value,
    creds: Option<&str>,
    settings: Option<&str>,
    status: Option<&Value>,
) -> Result<Value, &'static str> {
    let creds: Option<Value> = creds.and_then(|t| serde_json::from_str(t).ok());
    let oauth = creds
        .as_ref()
        .and_then(|c| c.get("claudeAiOauth"))
        .filter(|o| o["accessToken"].is_string());
    let signed_out = status.is_some_and(|s| s["loggedIn"] == false);
    let Some(oauth) = oauth.filter(|o| o["refreshToken"].is_string()) else {
        // A spent refresh token leaves the file blank; the CLI then says it is signed out.
        return Err(if signed_out { "rejected" } else { "failed" });
    };
    let old_oauth = &old["claudeAiOauth"];
    if oauth["refreshToken"] == old_oauth["refreshToken"]
        || oauth["accessToken"] == old_oauth["accessToken"]
    {
        return Err("failed"); // nothing rotated
    }
    let account = settings
        .and_then(|t| serde_json::from_str::<Value>(t).ok())
        .and_then(|s| s.get("oauthAccount").cloned())
        .filter(|a| a["emailAddress"] == old["oauthAccount"]["emailAddress"])
        .unwrap_or_else(|| old["oauthAccount"].clone());
    Ok(json!({"claudeAiOauth": oauth, "oauthAccount": account}))
}

/// One leased refresh: the login copied into a folder of its own with its access token marked expired, so
/// `claude auth status` refreshes it. Kept on disk, put in this computer's own turns, then sent (`flush`).
async fn refresh(runner: &Runner, dir: &Path, job: &Value) {
    let sid = text_of(&job["subscription"]);
    let revision = job["revision"].as_i64().unwrap_or(0);
    let Some(pending) = pending_path(dir, job) else {
        return;
    };
    let report = |state: &str, value: Option<String>| {
        let mut body = json!({"lease": job["lease"], "revision": revision, "state": state});
        if let Some(v) = value {
            body["value"] = json!(v);
        }
        body
    };
    let outcome = match serde_json::from_str::<Value>(&text_of(&job["value"])) {
        Ok(old) => run_refresh(runner, &old).await.map(|new| (old, new)),
        Err(_) => Err("failed"),
    };
    let body = match outcome {
        Ok((_, new)) => {
            let text = new.to_string();
            for secret in crate::model_subscriptions::claude_secrets(&text) {
                crate::redact::hide_everywhere(&secret, false);
            }
            let body = report("succeeded", Some(text));
            if let Err(e) = keep(&pending, &body.to_string()) {
                crate::util::diagnostic(&format!(
                    "Tico runner: could not keep a refreshed Claude plan: {e}"
                ));
            }
            // This computer's turns first: their old token stopped working when the refresh went through.
            if let Some(file) = turn_file(&new) {
                crate::model_subscriptions::rewrite(&sid, revision + 1, &file);
            }
            body
        }
        Err(state) => report(state, None),
    };
    if !pending.exists() {
        // A failure, or a success that could not be kept: sent at once (kept for a resend when it can be).
        let _ = keep(&pending, &body.to_string());
    }
}

async fn run_refresh(runner: &Runner, old: &Value) -> Result<Value, &'static str> {
    let exe =
        crate::env::which("claude", &std::env::var("PATH").unwrap_or_default()).ok_or("failed")?;
    let mut oauth = old["claudeAiOauth"].as_object().cloned().ok_or("failed")?;
    oauth.insert("expiresAt".into(), json!(1000));
    let home = crate::isolation::bot_state(&runner.config.state_dir())
        .join("claude-logins")
        .join(format!("refresh-{}", uuid::Uuid::new_v4().simple()));
    let result = async {
        crate::isolation::mkdir(&home.join(".claude"), 0o700).map_err(|_| "failed")?;
        let config = home.join(".claude");
        crate::model_subscriptions::write_private(
            &config.join(".credentials.json"),
            &json!({"claudeAiOauth": oauth}).to_string(),
        )
        .map_err(|_| "failed")?;
        crate::model_subscriptions::write_private(
            &config.join(".claude.json"),
            &json!({"oauthAccount": old["oauthAccount"], "hasCompletedOnboarding": true})
                .to_string(),
        )
        .map_err(|_| "failed")?;
        let mut env: crate::env::Env = std::env::vars().collect();
        for name in crate::harness_tools::api_key_env("claude") {
            env.remove(&name);
        }
        for name in [
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
        ] {
            env.remove(name);
        }
        env.insert("HOME".into(), home.display().to_string());
        env.insert("CLAUDE_CONFIG_DIR".into(), config.display().to_string());
        let mut cmd = crate::isolation::command(exe);
        cmd.args(["auth", "status", "--json"])
            .env_clear()
            .envs(&env)
            .current_dir(&home)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        let out = tokio::time::timeout(REFRESH_TIMEOUT, cmd.output())
            .await
            .map_err(|_| "failed")?
            .map_err(|_| "failed")?;
        let status: Option<Value> = serde_json::from_slice(&out.stdout).ok();
        let creds = std::fs::read_to_string(config.join(".credentials.json")).ok();
        let settings = std::fs::read_to_string(config.join(".claude.json")).ok();
        refreshed(old, creds.as_deref(), settings.as_deref(), status.as_ref())
    }
    .await;
    let _ = std::fs::remove_dir_all(&home);
    result
}

/// `claude-code/<version>`, as the CLI itself says it when it reads usage.
async fn user_agent() -> String {
    static SEEN: OnceLock<Mutex<Option<String>>> = OnceLock::new();
    let seen = SEEN.get_or_init(Default::default);
    if let Some(v) = seen.lock().unwrap().clone() {
        return v;
    }
    let mut version = String::new();
    if let Some(exe) = crate::env::which("claude", &std::env::var("PATH").unwrap_or_default()) {
        let mut cmd = tokio::process::Command::new(exe);
        cmd.arg("--version")
            .stdin(std::process::Stdio::null())
            .kill_on_drop(true);
        if let Ok(Ok(out)) = tokio::time::timeout(Duration::from_secs(10), cmd.output()).await {
            let text = String::from_utf8_lossy(&out.stdout).to_string();
            version = regex::Regex::new(r"(\d+\.\d+\.\d+)")
                .ok()
                .and_then(|r| r.captures(&text).map(|c| c[1].to_string()))
                .unwrap_or_default();
        }
    }
    let agent = if version.is_empty() {
        "claude-code".to_string()
    } else {
        format!("claude-code/{version}")
    };
    if !version.is_empty() {
        *seen.lock().unwrap() = Some(agent.clone());
    }
    agent
}

/// One usage read with a plan's access token, as the server's `UsageResult` (without its id).
async fn read_usage(token: &str) -> Value {
    if token.is_empty() {
        return json!({"state": "failed"});
    }
    let client = match crate::tls::http_builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(10))
        .timeout(USAGE_TIMEOUT)
        .build()
    {
        Ok(c) => c,
        Err(_) => return json!({"state": "failed"}),
    };
    let reply = client
        .get(USAGE_URL)
        .bearer_auth(token)
        .header("anthropic-beta", "oauth-2025-04-20")
        .header("User-Agent", user_agent().await)
        .header("Accept", "application/json")
        .send()
        .await;
    let Ok(reply) = reply else {
        return json!({"state": "failed"});
    };
    let status = reply.status().as_u16();
    if status == 429 {
        let wait = reply
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.trim().parse::<u64>().ok());
        return json!({"state": "limited", "retry_after": wait});
    }
    if !(200..300).contains(&status) {
        return json!({"state": "failed"});
    }
    match reply.json::<Value>().await {
        Ok(v) => usage_of(&v),
        Err(_) => json!({"state": "failed"}),
    }
}

fn window(w: &Value) -> Option<Value> {
    let used = w["utilization"]
        .as_f64()
        .or_else(|| w["percent"].as_f64())?;
    let resets = w["resets_at"]
        .as_str()
        .filter(|r| !r.is_empty() && r.len() <= 40)?;
    used.is_finite()
        .then(|| json!({"used_percent": used.clamp(0.0, 100.0), "resets_at": resets}))
}

/// The windows of a `/api/oauth/usage` answer: the week, the 5-hour window when there is one, and each model's own
/// week (`limits[]` scoped to a model). No week: a failed read.
pub fn usage_of(v: &Value) -> Value {
    let Some(week) = window(&v["seven_day"]) else {
        return json!({"state": "failed"});
    };
    let mut models = vec![];
    for limit in v["limits"].as_array().into_iter().flatten() {
        let name = text_of(&limit["scope"]["model"]["display_name"]);
        if name.is_empty() || name.len() > 80 || limit["is_active"] == false || models.len() >= 10 {
            continue;
        }
        if let Some(mut w) = window(limit) {
            w["model"] = json!(name);
            models.push(w);
        }
    }
    json!({"state": "succeeded", "seven_day": week, "five_hour": window(&v["five_hour"]), "models": models})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn login(access: &str, refresh: &str) -> Value {
        json!({
            "claudeAiOauth": {"accessToken": access, "refreshToken": refresh, "expiresAt": 1, "scopes": ["user:inference"]},
            "oauthAccount": {"emailAddress": "ops@acme.example", "organizationUuid": "org-1"}
        })
    }

    #[test]
    fn a_turn_file_never_holds_the_refresh_token() {
        let file = turn_file(&login("sk-ant-oat01-access", "sk-ant-ort01-refresh")).unwrap();
        assert!(
            file.contains("sk-ant-oat01-access") && !file.contains("refresh"),
            "{file}"
        );
        assert!(!file.contains("ops@acme.example"));
    }

    #[test]
    fn a_refresh_counts_only_when_both_tokens_rotated() {
        let old = login("a1", "r1");
        let new = login("a2", "r2");
        let got = refreshed(
            &old,
            Some(&new.to_string()),
            None,
            Some(&json!({"loggedIn": true})),
        )
        .unwrap();
        assert_eq!(got["claudeAiOauth"]["accessToken"], "a2");
        assert_eq!(got["oauthAccount"]["emailAddress"], "ops@acme.example");
        // Nothing rotated (offline): failed, tried again later.
        assert_eq!(
            refreshed(&old, Some(&old.to_string()), None, None),
            Err("failed")
        );
        // A spent refresh token blanks the file and the CLI says signed out: the login is gone.
        assert_eq!(
            refreshed(&old, Some(""), None, Some(&json!({"loggedIn": false}))),
            Err("rejected")
        );
        // Blank but no verdict: not enough to send the plan to Reconnect.
        assert_eq!(refreshed(&old, Some(""), None, None), Err("failed"));
        // The account the CLI left is kept only when it is the same one.
        let other = json!({"oauthAccount": {"emailAddress": "else@acme.example"}}).to_string();
        let got = refreshed(&old, Some(&new.to_string()), Some(&other), None).unwrap();
        assert_eq!(got["oauthAccount"]["emailAddress"], "ops@acme.example");
    }

    #[test]
    fn usage_reads_the_week_the_five_hours_and_each_models_week() {
        let v = json!({
            "five_hour": null,
            "seven_day": {"utilization": 42.5, "resets_at": "2026-11-20T10:00:00Z"},
            "limits": [
                {"kind": "weekly", "percent": 97, "resets_at": "2026-11-20T10:00:00Z", "is_active": true,
                 "scope": {"model": {"display_name": "Opus"}}},
                {"kind": "weekly", "percent": 10, "resets_at": "2026-11-20T10:00:00Z", "is_active": false,
                 "scope": {"model": {"display_name": "Sonnet"}}},
                {"kind": "weekly", "percent": 10, "resets_at": "2026-11-20T10:00:00Z"}
            ]
        });
        let got = usage_of(&v);
        assert_eq!(got["state"], "succeeded");
        assert_eq!(got["seven_day"]["used_percent"], 42.5);
        assert!(got["five_hour"].is_null());
        assert_eq!(
            got["models"],
            json!([{"model": "Opus", "used_percent": 97.0, "resets_at": "2026-11-20T10:00:00Z"}])
        );
        assert_eq!(
            usage_of(&json!({"five_hour": {"utilization": 3}}))["state"],
            "failed"
        );
    }

    #[test]
    fn a_kept_refresh_is_whole_and_private() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!(
            "tico-claude-login-{}",
            uuid::Uuid::new_v4().simple()
        ));
        let path =
            pending_path(&dir, &json!({"subscription": "sub-1", "lease": "lease-1"})).unwrap();
        keep(&path, "{\"lease\":\"lease-1\"}").unwrap();
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "{\"lease\":\"lease-1\"}"
        );
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert!(pending_path(&dir, &json!({"subscription": "../x", "lease": "l"})).is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
