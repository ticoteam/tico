//! Full Claude logins on this computer (backend/claude_login.py): what a plan signed in with `claude auth login`
//! needs from the computers that run its turns.
//!
//! - Tokens: each running turn holds only a short-lived access token in its own `.credentials.json`
//!   (model_subscriptions.rs). When the plan was refreshed elsewhere the server hands the new one, and every home of
//!   that plan here is rewritten at once (`model_subscriptions::rewrite`).
//! - Refreshes: the server leases one refresh per plan to one computer when less than an hour is left. The refresh
//!   runs `claude auth status --json`, as the runner (never a bot's user), on a copy of the login in a folder of the
//!   lease's own in the runner's private state (`<sid>/work-<lease>`: refresh tokens are single-use, and a spent one
//!   blanks the file it is in). It keeps the outcome there as `pending-<lease>.json` (0600) before anything is cleaned
//!   up, puts the new access token in this computer's own turns at once (the old one is revoked), and sends the outcome
//!   until the server answers: the server keeps one result per lease, so a resend is harmless. Only the server's answer
//!   says which revision the turns now hold (`model_subscriptions::rewrite`), or that it kept another login (`stale`:
//!   they take the server's on the poll that follows). A workspace a crash left behind is read on the next poll: a
//!   login rotated there is kept and sent like any other.
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
        recover(&dir);
        // An answer may name a newer login than the turns here hold: asked for now, not on the next tick.
        let answered = flush(runner, &dir).await?;
        let live = crate::model_subscriptions::live_logins();
        if !(ask || answered) || self.missing.is_some_and(|at| at.elapsed() < MISSING_FOR) {
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
            refresh(&dir, job).await;
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

/// The lease's own workspace: the job as handed (`job.json`) and the copy `claude` refreshes (`.claude/`).
fn work_path(dir: &Path, job: &Value) -> Option<PathBuf> {
    let (sid, lease) = (text_of(&job["subscription"]), text_of(&job["lease"]));
    (safe(&sid) && safe(&lease)).then(|| dir.join(&sid).join(format!("work-{lease}")))
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

/// Send every refresh outcome still on disk. The server answers each lease once and the same way again after
/// (backend/claude_login.py refresh_result), so any answer, kept or not, ends it; a network failure keeps it. A kept
/// login's revision is now this computer's turns' too; one not kept leaves them to take the server's. Returns whether
/// the server answered any.
async fn flush(runner: &Runner, dir: &Path) -> Result<bool, ApiError> {
    let mut answered = false;
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
                Ok(answer) => {
                    answered = true;
                    acknowledged(&sid, &body, &answer);
                    let _ = std::fs::remove_file(entry.path());
                }
                Err(e) if !e.retryable && e.status != 0 && e.status != 429 && e.status < 500 => {
                    answered = true;
                    acknowledged(&sid, &body, &json!({"stored": false}));
                    let _ = std::fs::remove_file(entry.path());
                }
                Err(e) => return Err(e),
            }
        }
    }
    Ok(answered)
}

/// What the server's answer to a refresh means for this computer's turns on the plan.
fn acknowledged(sid: &str, sent: &Value, answer: &Value) {
    if sent["state"] != "succeeded" {
        return; // nothing was written into a turn
    }
    let file = serde_json::from_str::<Value>(&text_of(&sent["value"]))
        .ok()
        .and_then(|v| turn_file(&v));
    match (answer["stored"] == true, answer["revision"].as_i64(), file) {
        (true, Some(revision), Some(file)) => {
            crate::model_subscriptions::rewrite(sid, revision, &file);
        }
        _ => crate::model_subscriptions::stale(sid),
    }
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

/// One leased refresh, in the lease's workspace: the job kept there first, then the login copied with its access
/// token marked expired so `claude auth status` refreshes it (`run_refresh`), then the outcome kept (`settle`).
async fn refresh(dir: &Path, job: &Value) {
    let (Some(pending), Some(work)) = (pending_path(dir, job), work_path(dir, job)) else {
        return;
    };
    let old = serde_json::from_str::<Value>(&text_of(&job["value"]))
        .ok()
        .filter(|v| v["claudeAiOauth"]["refreshToken"].is_string());
    let outcome = match old {
        Some(old) => run_refresh(&work, job, &old).await.map(|new| (old, new)),
        None => Err("failed"),
    };
    settle(&work, &pending, job, outcome.map(|(_, new)| new));
}

/// Keep a refresh's outcome as `pending-<lease>.json`, and only then remove the lease's workspace (a crash in between
/// leaves both: the pending file wins). A refreshed login goes into this computer's own turns at once.
fn settle(work: &Path, pending: &Path, job: &Value, outcome: Result<Value, &'static str>) {
    let sid = text_of(&job["subscription"]);
    let mut body = json!({"lease": job["lease"], "revision": job["revision"]});
    match &outcome {
        Ok(new) => {
            let text = new.to_string();
            for secret in crate::model_subscriptions::claude_secrets(&text) {
                crate::redact::hide_everywhere(&secret, false);
            }
            body["state"] = json!("succeeded");
            body["value"] = json!(text);
        }
        Err(state) => body["state"] = json!(state),
    }
    if let Err(e) = keep(pending, &body.to_string()) {
        // The workspace stays: the next poll finds the rotated login there (`recover`).
        crate::util::diagnostic(&format!(
            "Tico runner: could not keep a refreshed Claude plan: {e}"
        ));
        return;
    }
    if let Some(file) = outcome.ok().as_ref().and_then(turn_file) {
        // This computer's turns first: their old token stopped working when the refresh went through.
        crate::model_subscriptions::renew(&sid, &file);
    }
    let _ = std::fs::remove_dir_all(work);
}

/// Workspaces a stopped runner left: the outcome each holds is kept and sent like any other. A login rotated there is
/// the only copy of the plan's login, so it is never thrown away unsent.
fn recover(dir: &Path) {
    for sub in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        for entry in std::fs::read_dir(sub.path())
            .into_iter()
            .flatten()
            .flatten()
        {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("work-") {
                continue;
            }
            let work = entry.path();
            let job = std::fs::read_to_string(work.join("job.json"))
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok());
            let Some((job, pending)) = job.and_then(|j| pending_path(dir, &j).map(|p| (j, p)))
            else {
                let _ = std::fs::remove_dir_all(&work); // the job was never kept: nothing was refreshed
                continue;
            };
            if pending.exists() {
                let _ = std::fs::remove_dir_all(&work);
                continue;
            }
            let old = serde_json::from_str::<Value>(&text_of(&job["value"])).unwrap_or_default();
            let config = work.join(".claude");
            let creds = std::fs::read_to_string(config.join(".credentials.json")).ok();
            let settings = std::fs::read_to_string(config.join(".claude.json")).ok();
            let outcome = refreshed(&old, creds.as_deref(), settings.as_deref(), None);
            settle(&work, &pending, &job, outcome);
        }
    }
}

async fn run_refresh(work: &Path, job: &Value, old: &Value) -> Result<Value, &'static str> {
    use std::os::unix::fs::DirBuilderExt;
    let exe =
        crate::env::which("claude", &std::env::var("PATH").unwrap_or_default()).ok_or("failed")?;
    let mut oauth = old["claudeAiOauth"].as_object().cloned().ok_or("failed")?;
    oauth.insert("expiresAt".into(), json!(1000));
    let config = work.join(".claude");
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(&config)
        .map_err(|_| "failed")?;
    // The job first: a crash from here on leaves what `recover` needs to tell what happened.
    keep(&work.join("job.json"), &job.to_string()).map_err(|_| "failed")?;
    keep(
        &config.join(".credentials.json"),
        &json!({"claudeAiOauth": oauth}).to_string(),
    )
    .map_err(|_| "failed")?;
    keep(
        &config.join(".claude.json"),
        &json!({"oauthAccount": old["oauthAccount"], "hasCompletedOnboarding": true}).to_string(),
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
    env.insert("HOME".into(), work.display().to_string());
    env.insert("CLAUDE_CONFIG_DIR".into(), config.display().to_string());
    // As the runner itself: the copy holds a refresh token, which no bot's turn may read or spend.
    let mut cmd = tokio::process::Command::new(exe);
    cmd.args(["auth", "status", "--json"])
        .env_clear()
        .envs(&env)
        .current_dir(work)
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

    fn scratch() -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "tico-claude-login-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A turn here on plan `sid` at `revision`, holding `token`; its config directory.
    fn turn_on(
        state: &Path,
        sid: &str,
        revision: i64,
        token: &str,
    ) -> (Vec<crate::model_subscriptions::Delivered>, PathBuf) {
        let file = json!({"claudeAiOauth": {"accessToken": token}}).to_string();
        let item = json!([{"id": sid, "runtime": "claude", "kind": "claude_login", "file": ".credentials.json",
                           "home_env": "CLAUDE_CONFIG_DIR", "value": file, "revision": revision,
                           "billing": "subscription"}]);
        let mut env: crate::env::Env =
            [("HOME".into(), state.join("bot").display().to_string())].into();
        let mut got = crate::model_subscriptions::apply(
            state,
            &item,
            &mut env,
            &mut vec![],
            &mut Default::default(),
        );
        crate::model_subscriptions::start(&mut got, "claude", &mut env);
        let home = PathBuf::from(&env["CLAUDE_CONFIG_DIR"]);
        (got, home)
    }

    fn revision_of(sid: &str) -> Option<i64> {
        crate::model_subscriptions::live_logins()
            .into_iter()
            .find(|(p, _)| p == sid)
            .map(|(_, r)| r)
    }

    #[test]
    fn a_refresh_rewrites_the_turns_at_once_and_their_revision_only_when_the_server_answers() {
        let (state, dir) = (scratch(), scratch());
        let sid = format!("cl{}", uuid::Uuid::new_v4().simple());
        let (mut turn, home) = turn_on(&state, &sid, 4, "sk-ant-oat01-old-synthetic");
        let job = json!({"lease": "lease-a", "subscription": sid, "revision": 4,
                         "value": login("sk-ant-oat01-old-synthetic", "r1").to_string()});
        let (work, pending) = (
            work_path(&dir, &job).unwrap(),
            pending_path(&dir, &job).unwrap(),
        );
        std::fs::create_dir_all(&work).unwrap();
        settle(
            &work,
            &pending,
            &job,
            Ok(login("sk-ant-oat01-new-synthetic", "r2")),
        );
        // Kept before the workspace went; the turn has the new token, still recorded at the old revision.
        let sent: Value =
            serde_json::from_str(&std::fs::read_to_string(&pending).unwrap()).unwrap();
        assert_eq!(sent["state"], "succeeded");
        assert!(!work.exists());
        assert!(std::fs::read_to_string(home.join(".credentials.json"))
            .unwrap()
            .contains("new-synthetic"));
        assert_eq!(revision_of(&sid), Some(4));
        // Kept by the server as revision 5: the turn is at 5.
        acknowledged(&sid, &sent, &json!({"stored": true, "revision": 5}));
        assert_eq!(revision_of(&sid), Some(5));
        // A Reconnect took the plan first: the turn asks for the server's login, whatever it had.
        acknowledged(&sid, &sent, &json!({"stored": false}));
        assert_eq!(revision_of(&sid), Some(0));
        crate::model_subscriptions::finish(&mut turn);
        let _ = std::fs::remove_dir_all(&state);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_workspace_left_by_a_crash_is_kept_and_sent_never_lost() {
        let dir = scratch();
        let old = login("sk-ant-oat01-old-synthetic", "r1");
        let job = |lease: &str| json!({"lease": lease, "subscription": "sub-1", "revision": 2, "value": old.to_string()});
        // Rotated before the crash: the only copy of the login, kept as this lease's outcome.
        let rotated = work_path(&dir, &job("lease-r")).unwrap();
        std::fs::create_dir_all(rotated.join(".claude")).unwrap();
        keep(&rotated.join("job.json"), &job("lease-r").to_string()).unwrap();
        keep(
            &rotated.join(".claude/.credentials.json"),
            &login("sk-ant-oat01-new-synthetic", "r2").to_string(),
        )
        .unwrap();
        // Never ran: nothing rotated, a failure the server tries again.
        let idle = work_path(&dir, &job("lease-i")).unwrap();
        std::fs::create_dir_all(idle.join(".claude")).unwrap();
        keep(&idle.join("job.json"), &job("lease-i").to_string()).unwrap();
        keep(&idle.join(".claude/.credentials.json"), &old.to_string()).unwrap();
        recover(&dir);
        let read = |lease: &str| -> Value {
            serde_json::from_str(
                &std::fs::read_to_string(pending_path(&dir, &job(lease)).unwrap()).unwrap(),
            )
            .unwrap()
        };
        assert_eq!(read("lease-r")["state"], "succeeded");
        assert!(text_of(&read("lease-r")["value"]).contains("new-synthetic"));
        assert_eq!(read("lease-i")["state"], "failed");
        assert!(!rotated.exists() && !idle.exists());
        let _ = std::fs::remove_dir_all(&dir);
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
