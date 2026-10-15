//! The runtime hosts: one runtime behind start/thread/turn/steer/interrupt calls and
//! one event channel. Events are the Python host's dicts, as JSON, so the outbox rows match it field for field:
//!
//! delta {text}, message {text, final}, turn_completed {status}, turn_failed {error, limit, auth_retry},
//! status {state}, tokens {input, output, total, usage{input, cached, output}}, rate_limits, error, diagnostic.

pub mod antigravity;
pub mod claude;
pub mod codex;
pub mod cursor;
pub mod gemini;
pub mod grok;
mod native;
pub mod musecode;
mod oneshot;
pub mod pi;
#[cfg(test)]
mod isolationtests;

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::sync::mpsc::UnboundedSender;

use crate::env::Env;

pub type Events = UnboundedSender<Value>;

#[derive(Debug)]
pub enum HostError {
    /// The runtime refused a call, or died while answering one.
    Failed(String),
    /// `steer` wrote nothing because the turn is over or ending: the runner gives the input a turn of its own.
    SteerRefused(String),
}

impl std::fmt::Display for HostError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HostError::Failed(m) | HostError::SteerRefused(m) => f.write_str(m),
        }
    }
}

pub fn failed(text: impl Into<String>) -> HostError {
    HostError::Failed(text.into())
}

/// One rule for a usage limit, so every host classifies it the same way (base.py LIMIT_RE).
pub fn limit_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)hit your (?:usage|session|weekly) limit|usage limit|usage_limit_reached|subscription quota exhausted|quota exceeded\. check your plan|exceeded your current quota|insufficient[_ ]quota|rate[ _]limit(?:ed|_error)?|spend(?:ing)? limit|monthly (?:usage |spend(?:ing)? )?limit|no capacity available|model is at capacity|\boverloaded\b|resource_exhausted|\b(?:code|status|http) (?:429|503)\b",
        )
        .unwrap()
    })
}

fn auth_retry_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)failed to refresh oauth token|another claude code process is refreshing|exited mid-refresh|oauth_refresh_lock|lock_busy|lock_timeout|unexpected status 401 unauthorized",
        )
        .unwrap()
    })
}

fn auth_rejected_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)incorrect api key|invalid[ _-]?(?:x-)?api[ _-]?key|api key (?:is )?(?:invalid|revoked|expired)|not logged in|please run /login|invalid authentication credentials|authentication_error|oauth session expired and could not be refreshed|missing meta credentials",
        )
        .unwrap()
    })
}

fn secretish_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\b(?:sk|pk|xai|gsk|key|ghp|github_pat)[-_][A-Za-z0-9_-]{6,}|Bearer\s+\S+|[A-Za-z0-9_-]{32,}").unwrap())
}

pub fn is_limit(text: &str) -> bool {
    limit_re().is_match(text)
}

pub fn is_auth_rejected(text: &str) -> bool {
    auth_rejected_re().is_match(text)
}

pub fn is_auth_retryable(text: &str) -> bool {
    auth_retry_re().is_match(text) && !is_auth_rejected(text)
}

/// One line of the provider's refusal for people to read; keys and tokens are never kept.
pub fn rejection_reason(text: &str) -> String {
    let line = text.lines().map(str::trim).find(|row| is_auth_rejected(row)).map(String::from).unwrap_or_default();
    let line = if line.is_empty() { text.trim().to_string() } else { line };
    secretish_re().replace_all(&line, "[redacted]").chars().take(200).collect()
}

pub const DEFAULT_COMPACT_AT: i64 = 250_000;

/// A bot's "Compact at" in tokens: its own whole number, else DEFAULT_COMPACT_AT.
pub fn compact_at(value: &Value) -> i64 {
    let n = match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    };
    match n {
        Some(t) if t > 0 => t,
        _ => DEFAULT_COMPACT_AT,
    }
}

#[derive(Clone, Debug, Default)]
pub struct Settings {
    pub cwd: String,
    pub model: Option<String>,
    pub effort: Option<String>,
    pub env: Env,
    #[allow(dead_code)] // the hub MCP server and Codex read the bot from the environment
    pub slug: String,
    pub compact_at: Value,
    /// The task's worktrees in other repos the session may work in (Claude `--add-dir`; others ignore it).
    pub add_dirs: Vec<String>,
    /// A bot with branches: Claude reads only the project's settings and MCP servers (hosts/claude.py).
    pub shared: bool,
    /// The bot's own MCP servers from bot.yaml, this harness's transports only (crate::perbot::mcp_for_run).
    pub mcp_servers: Vec<Value>,
}

const HUB_MCP_ENV: &[&str] = &["HUB_API_URL", "HUB_TOKEN", "HUB_BOT", "HUB_EMPLOYEE", "HUB_DIR"];

/// The hub as a stdio MCP server for one turn (`hub_mcp_server`), or None outside a turn.
pub fn hub_mcp_server(env: &Env) -> Option<Value> {
    if env.get("HUB_API_URL").is_none_or(|v| v.is_empty()) || env.get("HUB_TOKEN").is_none_or(|v| v.is_empty()) {
        return None;
    }
    let mut vars = Map::new();
    for k in HUB_MCP_ENV {
        if let Some(v) = env.get(*k).filter(|v| !v.is_empty()) {
            vars.insert(k.to_string(), json!(v));
        }
    }
    let script = crate::env::root().join("clients").join("hubmcp.py");
    Some(json!({"command": crate::env::python().display().to_string(), "args": [script.display().to_string()], "env": vars}))
}

/// An event dict: `kind`, `thread_id`, `turn_id`, then its own fields.
pub fn event(kind: &str, thread: Option<&str>, turn: Option<&str>, fields: Value) -> Value {
    let mut ev = Map::new();
    ev.insert("kind".into(), json!(kind));
    ev.insert("thread_id".into(), thread.map_or(Value::Null, |t| json!(t)));
    ev.insert("turn_id".into(), turn.map_or(Value::Null, |t| json!(t)));
    if let Value::Object(f) = fields {
        ev.extend(f);
    }
    Value::Object(ev)
}

/// A way to stop a child process that only one task owns and reaps (`own`). Signals go through that task, so
/// neither SIGTERM nor the later SIGKILL can reach a pid the system has already handed to another process.
#[derive(Clone)]
pub struct Stopper(tokio::sync::mpsc::UnboundedSender<std::time::Duration>);

impl Stopper {
    /// SIGTERM now, SIGKILL after `grace` if the process has not exited by then. A process already reaped is
    /// left alone.
    pub fn terminate(&self, grace: std::time::Duration) {
        let _ = self.0.send(grace);
    }
}

/// Hand `child` to a task of its own that waits for it and carries out `Stopper` requests. The receiver gets the
/// exit status. Take the child's pipes before calling this.
pub fn own(mut child: tokio::process::Child) -> (Stopper, tokio::sync::oneshot::Receiver<std::io::Result<std::process::ExitStatus>>) {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<std::time::Duration>();
    let (done, exited) = tokio::sync::oneshot::channel();
    tokio::spawn(async move {
        let mut kill_at: Option<tokio::time::Instant> = None;
        let mut asking = true;
        let status = loop {
            let escalate = async {
                match kill_at {
                    Some(at) => tokio::time::sleep_until(at).await,
                    None => std::future::pending().await,
                }
            };
            tokio::select! {
                status = child.wait() => break status,
                asked = rx.recv(), if asking => match asked {
                    // Not reaped yet: only this task waits for the child, and its wait has not returned.
                    Some(grace) => {
                        if let Some(pid) = child.id() {
                            unsafe { libc::kill(pid as i32, libc::SIGTERM); }
                        }
                        let at = tokio::time::Instant::now() + grace;
                        kill_at = Some(kill_at.map_or(at, |k| k.min(at)));
                    }
                    None => asking = false,
                },
                _ = escalate => {
                    let _ = child.start_kill();
                    kill_at = None;
                    asking = false;
                }
            }
        };
        let _ = done.send(status);
    });
    (Stopper(tx), exited)
}

/// An exit status as Python's `returncode`: the code, or minus the signal that ended it.
pub fn return_code(status: &std::io::Result<std::process::ExitStatus>) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    match status {
        Ok(s) => s.code().unwrap_or_else(|| -s.signal().unwrap_or(1)),
        Err(_) => -1,
    }
}

/// The runtimes this runner runs.
#[derive(Clone)]
pub enum Host {
    Claude(claude::ClaudeHost),
    Codex(codex::CodexHost),
    Gemini(gemini::GeminiHost),
    /// The gemini runtime's Antigravity harness, kept warm between turns (crate::warm).
    Antigravity(antigravity::AntigravityHost),
    MuseCode(musecode::MuseCodeHost),
    Pi(pi::PiHost),
    Cursor(cursor::CursorHost),
    Grok(grok::GrokHost),
}

impl Host {
    pub async fn start(&self) -> Result<(), HostError> {
        match self {
            Host::Claude(h) => h.start().await,
            Host::Codex(h) => h.start().await,
            Host::Gemini(h) => {
                h.start();
                Ok(())
            }
            Host::Antigravity(h) => h.start(),
            Host::MuseCode(h) => {
                h.start();
                Ok(())
            }
            Host::Pi(h) => {
                h.start();
                Ok(())
            }
            Host::Cursor(h) => {
                h.start();
                Ok(())
            }
            Host::Grok(h) => {
                h.start();
                Ok(())
            }
        }
    }

    pub async fn stop(&self) {
        match self {
            Host::Claude(h) => h.stop().await,
            Host::Codex(h) => h.stop().await,
            Host::Gemini(h) => h.stop().await,
            Host::Antigravity(h) => h.stop().await,
            Host::MuseCode(h) => h.stop().await,
            Host::Pi(h) => h.stop().await,
            Host::Cursor(h) => h.stop().await,
            Host::Grok(h) => h.stop().await,
        }
    }

    pub fn alive(&self) -> bool {
        match self {
            Host::Claude(h) => h.alive(),
            Host::Codex(h) => h.alive(),
            Host::Gemini(h) => h.alive(),
            Host::Antigravity(h) => h.alive(),
            Host::MuseCode(h) => h.alive(),
            Host::Pi(h) => h.alive(),
            Host::Cursor(h) => h.alive(),
            Host::Grok(h) => h.alive(),
        }
    }

    pub fn supports_steer(&self) -> bool {
        match self {
            Host::Claude(h) => h.supports_steer(),
            Host::Codex(_) => true,
            Host::Gemini(_) | Host::Antigravity(_) => false,
            Host::MuseCode(_) | Host::Pi(_) | Host::Cursor(_) | Host::Grok(_) => false,
        }
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => Ok(h.start_thread(settings)),
            Host::Codex(h) => h.start_thread(bot, settings).await,
            Host::Gemini(h) => Ok(h.start_thread(settings)),
            Host::Antigravity(h) => Ok(h.start_thread(settings)),
            Host::MuseCode(h) => Ok(h.start_thread(settings)),
            Host::Pi(h) => Ok(h.start_thread(settings)),
            Host::Cursor(h) => h.start_thread(settings).await,
            Host::Grok(h) => h.start_thread(bot, settings).await,
        }
    }

    pub async fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Codex(h) => h.resume_thread(thread_id, settings).await,
            Host::Gemini(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Antigravity(h) => h.resume_thread(thread_id, settings),
            Host::MuseCode(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Pi(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Cursor(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Grok(h) => h.resume_thread(thread_id, settings).await,
        }
    }

    pub async fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => h.start_turn(thread_id, text, effort),
            Host::Codex(h) => h.start_turn(thread_id, text, effort).await,
            Host::Gemini(h) => h.start_turn(thread_id, text, effort),
            Host::Antigravity(h) => h.start_turn(thread_id, text, effort),
            Host::MuseCode(h) => h.start_turn(thread_id, text, effort),
            Host::Pi(h) => h.start_turn(thread_id, text, effort),
            Host::Cursor(h) => h.start_turn(thread_id, text, effort),
            Host::Grok(h) => h.start_turn(thread_id, text).await,
        }
    }

    pub async fn steer(&self, thread_id: &str, turn_id: &str, text: &str) -> Result<(), HostError> {
        match self {
            Host::Claude(h) => h.steer(thread_id, turn_id, text),
            Host::Codex(h) => h.steer(thread_id, turn_id, text).await,
            Host::Gemini(_) => Err(failed("Gemini runtime cannot steer a running turn")),
            Host::Antigravity(h) => h.steer(),
            Host::MuseCode(_) => Err(HostError::SteerRefused("Muse Code cannot steer a running turn".into())),
            Host::Pi(_) => Err(failed("Pi runtime cannot steer a running turn")),
            Host::Cursor(_) => Err(failed("Cursor runtime cannot steer a running turn")),
            Host::Grok(_) => Err(failed("grok agent cannot steer a running turn")),
        }
    }

    pub async fn interrupt(&self, thread_id: &str, turn_id: &str) -> Result<(), HostError> {
        match self {
            Host::Claude(h) => {
                h.interrupt(thread_id, turn_id);
                Ok(())
            }
            Host::Codex(h) => h.interrupt(thread_id, turn_id).await,
            Host::Gemini(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
            Host::Antigravity(h) => {
                h.interrupt().await;
                Ok(())
            }
            Host::MuseCode(h) => {
                h.interrupt(thread_id, turn_id);
                Ok(())
            }
            Host::Pi(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
            Host::Cursor(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
            Host::Grok(h) => h.interrupt(thread_id).await,
        }
    }

    pub fn session_id(&self, thread_id: &str) -> String {
        match self {
            Host::Claude(h) => h.session_id(thread_id),
            _ => thread_id.to_string(),
        }
    }
}

#[cfg(test)]
pub mod fixtures {
    //! runner/tests/fixtures/<harness>/*.jsonl, replayed here.
    use std::path::PathBuf;

    use serde_json::Value;

    pub fn dir(harness: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../runner/tests/fixtures").join(harness)
    }

    /// (fixture path, the events the Python host makes of it), for every fixture of `harness`.
    pub fn cases(harness: &str) -> Vec<(PathBuf, Vec<Value>)> {
        let mut out = vec![];
        let mut paths: Vec<PathBuf> = std::fs::read_dir(dir(harness)).unwrap().map(|e| e.unwrap().path()).collect();
        paths.sort();
        for p in paths.into_iter().filter(|p| p.extension().is_some_and(|e| e == "jsonl")) {
            let expected: Value = serde_json::from_str(&std::fs::read_to_string(p.with_extension("expected.json")).unwrap()).unwrap();
            out.push((p, expected.as_array().unwrap().clone()));
        }
        assert!(!out.is_empty(), "no {harness} fixtures");
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_python_classifiers_agree() {
        assert!(is_limit("You've hit your usage limit"));
        assert!(is_limit("You've hit your session limit · resets 12:40am (UTC)"));
        assert!(is_limit("HTTP 503 overloaded"));
        assert!(!is_limit("all good"));
        assert!(is_limit("Claude AI monthly spend limit reached. Raise it in your account settings."));
        assert!(is_limit("API error 429: Subscription quota exhausted. Your usage window resets at 2026-10-19T00:00:00Z. (rate_limit_error)"));
        assert!(!is_limit("Draft the monthly spend report"));
        // An API key's quota or billing refusal, as Codex and the OpenAI API word it.
        assert!(is_limit("Quota exceeded. Check your plan and billing details."));
        assert!(is_limit("You exceeded your current quota, please check your plan and billing details. (insufficient_quota)"));
        assert!(is_limit(r#"{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}"#));
        assert!(!is_limit("Raise the storage quota for the team drive"));
        assert!(!is_limit("Quota: 40 of 100 seats used"));
        assert!(!is_limit("write /tmp/out: Disk quota exceeded (os error 122)"));
        assert!(is_auth_retryable("OAuth: failed to refresh OAuth token"));
        assert!(!is_auth_retryable("unexpected status 401 Unauthorized: Invalid API key"));
        assert!(is_auth_rejected("Invalid API key · Please run /login"));
        assert_eq!(rejection_reason("x\nInvalid API key sk-ant-abcdefghijk"), "Invalid API key [redacted]");
        assert_eq!(compact_at(&json!("120000")), 120000);
        assert_eq!(compact_at(&json!(0)), DEFAULT_COMPACT_AT);
    }
}

// Per-host answers behind a capability: generic code reads the manifest's capability and asks here, so the adapters
// stay the one place that knows a CLI by name.

/// Native goals and built-in commands of one installed harness (goals.capabilities): Claude Code by version, Codex by
/// the protocol its app-server describes; every other host has none.
pub async fn probe_goals(runtime: &str, exe: &std::path::Path, version: &str) -> (bool, Vec<Value>) {
    let (mut supported, mut commands) = (false, vec![]);
    match runtime {
        "claude" => {
            supported = crate::hosts::claude::version_tuple(version).is_some_and(|v| v >= (2, 1, 212));
            if supported {
                commands = vec![
                    crate::goals::command("compact", "Compact this conversation", "[instructions]"),
                    crate::goals::command("clear", "Clear the harness conversation", ""),
                    crate::goals::command("model", "Change the harness model", "[model]"),
                ];
            }
        }
        "codex" => {
            // The installed protocol, rather than a version guess, determines native goal support.
            let dir = std::env::temp_dir().join(format!("tico-goal-schema-{}", uuid::Uuid::new_v4().simple()));
            if std::fs::create_dir_all(&dir).is_ok() {
                let mut cmd = tokio::process::Command::new(exe);
                cmd.args(["app-server", "generate-json-schema", "--experimental", "--out"]).arg(&dir);
                cmd.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).kill_on_drop(true);
                if let Ok(Ok(status)) = tokio::time::timeout(std::time::Duration::from_secs(10), cmd.status()).await {
                    if status.success() {
                        let schema = dir.join("v2");
                        supported = schema.join("ThreadGoalSetParams.json").exists();
                        if schema.join("ThreadCompactStartParams.json").exists() {
                            commands.push(crate::goals::command("compact", "Compact this conversation", ""));
                        }
                        if schema.join("ReviewStartParams.json").exists() {
                            commands.push(crate::goals::command("review", "Review changes", "[instructions]"));
                        }
                    }
                }
                let _ = std::fs::remove_dir_all(&dir);
            }
        }
        _ => {}
    }
    (supported, commands)
}

/// `sign_in_status.check = "bot-key"`: every bot that runs this host holds its key grant.
pub fn bot_key_sign_in(runtime: &str, assignments: &[Value]) -> (&'static str, String) {
    match runtime {
        "gemini" => gemini::sign_in(assignments),
        "pi" => pi::sign_in(assignments),
        _ => ("unknown", "This harness's key cannot be checked".into()),
    }
}

/// Points `env` at the host's own config folder (`own_config_home`), so a login or status check sees the login its
/// turns use rather than the operator's. False for a host without one.
pub fn use_own_config_home(runtime: &str, profile: Option<&crate::profiles::Profile>, env: &mut Env) -> bool {
    match runtime {
        "musecode" => {
            env.remove("MUSE_AUTH_PATH");
            env.insert("XDG_CONFIG_HOME".into(), musecode::config_home(profile).display().to_string());
            true
        }
        _ => false,
    }
}

/// `sign_in_status.check = "file"`: whether the host's login file is there in `env` (None: not readable).
pub fn file_signed_in(runtime: &str, env: &Env) -> Option<bool> {
    match runtime {
        "musecode" => musecode::signed_in(&musecode::auth_file(env)),
        _ => None,
    }
}

/// `model_list = "host"`: the models the host offers (for a profile's own login where that matters).
pub fn models(runtime: &str, profile: Option<&crate::profiles::Profile>) -> Vec<String> {
    match runtime {
        "codex" => crate::readiness::codex_models(profile.and_then(|p| p.home("codex"))),
        "claude" => crate::readiness::CLAUDE_MODELS.iter().map(|s| s.to_string()).collect(),
        "gemini" => gemini::MODELS.iter().map(|s| s.to_string()).collect(),
        "musecode" => musecode::MODELS.iter().map(|s| s.to_string()).collect(),
        "pi" => pi::model_names(),
        "cursor" => cursor::model_names(),
        _ => vec![],
    }
}

/// Whether a host's stored token has expired (`serial_cold_start`), so the first turn refreshes it.
pub fn token_cold(runtime: &str, profile: Option<&crate::profiles::Profile>) -> bool {
    match runtime {
        "claude" => crate::profiles::claude_token_cold(profile),
        _ => false,
    }
}
