//! The runtime hosts (`runner/hosts/base.py`): one runtime behind start/thread/turn/steer/interrupt calls and
//! one event channel. Events are the Python host's dicts, as JSON, so the outbox rows match it field for field:
//!
//! delta {text}, message {text, final}, turn_completed {status}, turn_failed {error, limit, auth_retry},
//! status {state}, tokens {input, output, total, usage{input, cached, output}}, rate_limits, error, diagnostic.

pub mod antigravity;
pub mod claude;
pub mod codex;
pub mod gemini;
mod native;
pub mod musecode;

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
            r"(?i)hit your usage limit|usage limit|usage_limit_reached|rate limit(?:ed)?|no capacity available|model is at capacity|\boverloaded\b|resource_exhausted|\b(?:code|status|http) (?:429|503)\b",
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
        }
    }

    pub async fn stop(&self) {
        match self {
            Host::Claude(h) => h.stop().await,
            Host::Codex(h) => h.stop().await,
            Host::Gemini(h) => h.stop().await,
            Host::Antigravity(h) => h.stop().await,
            Host::MuseCode(h) => h.stop().await,
        }
    }

    pub fn alive(&self) -> bool {
        match self {
            Host::Claude(h) => h.alive(),
            Host::Codex(h) => h.alive(),
            Host::Gemini(h) => h.alive(),
            Host::Antigravity(h) => h.alive(),
            Host::MuseCode(h) => h.alive(),
        }
    }

    pub fn supports_steer(&self) -> bool {
        match self {
            Host::Claude(h) => h.supports_steer(),
            Host::Codex(_) => true,
            Host::Gemini(_) | Host::Antigravity(_) => false,
            Host::MuseCode(_) => false,
        }
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => Ok(h.start_thread(settings)),
            Host::Codex(h) => h.start_thread(bot, settings).await,
            Host::Gemini(h) => Ok(h.start_thread(settings)),
            Host::Antigravity(h) => Ok(h.start_thread(settings)),
            Host::MuseCode(h) => Ok(h.start_thread(settings)),
        }
    }

    pub async fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Codex(h) => h.resume_thread(thread_id, settings).await,
            Host::Gemini(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Antigravity(h) => h.resume_thread(thread_id, settings),
            Host::MuseCode(h) => Ok(h.resume_thread(thread_id, settings)),
        }
    }

    pub async fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => h.start_turn(thread_id, text, effort),
            Host::Codex(h) => h.start_turn(thread_id, text, effort).await,
            Host::Gemini(h) => h.start_turn(thread_id, text, effort),
            Host::Antigravity(h) => h.start_turn(thread_id, text, effort),
            Host::MuseCode(h) => h.start_turn(thread_id, text, effort),
        }
    }

    pub async fn steer(&self, thread_id: &str, turn_id: &str, text: &str) -> Result<(), HostError> {
        match self {
            Host::Claude(h) => h.steer(thread_id, turn_id, text),
            Host::Codex(h) => h.steer(thread_id, turn_id, text).await,
            Host::Gemini(_) => Err(failed("Gemini runtime cannot steer a running turn")),
            Host::Antigravity(h) => h.steer(),
            Host::MuseCode(_) => Err(HostError::SteerRefused("Muse Code cannot steer a running turn".into())),
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
        }
    }

    pub fn session_id(&self, thread_id: &str) -> String {
        match self {
            Host::Claude(h) => h.session_id(thread_id),
            Host::Codex(_) | Host::Gemini(_) | Host::Antigravity(_) | Host::MuseCode(_) => thread_id.to_string(),
        }
    }
}

#[cfg(test)]
pub mod fixtures {
    //! runner/tests/fixtures/<harness>/*.jsonl, replayed here and by runner/tests/test_host_fixtures.py.
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
        assert!(is_limit("HTTP 503 overloaded"));
        assert!(!is_limit("all good"));
        assert!(is_auth_retryable("OAuth: failed to refresh OAuth token"));
        assert!(!is_auth_retryable("unexpected status 401 Unauthorized: Invalid API key"));
        assert!(is_auth_rejected("Invalid API key · Please run /login"));
        assert_eq!(rejection_reason("x\nInvalid API key sk-ant-abcdefghijk"), "Invalid API key [redacted]");
        assert_eq!(compact_at(&json!("120000")), 120000);
        assert_eq!(compact_at(&json!(0)), DEFAULT_COMPACT_AT);
    }
}
