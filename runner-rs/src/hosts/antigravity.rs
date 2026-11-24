//! The Antigravity host: `agy acp`, speaking ACP (JSON-RPC 2.0 over stdio) as T3 Code does: `initialize`,
//! `authenticate` with the sign-in's method (`auth_method`), `session/resume` of the thread's saved conversation
//! (`session/load` on an agy without resume) or `session/new`, the model and thinking level as the session's config
//! options, then `session/prompt`, whose `session/update`s are the turn's events. Its `--gemini-dir` is the turn's home:
//! a plan's own (model_subscriptions.rs TURN_HOMES), else the bot's `~/.gemini`, where the computer's own sign-in is.
//! The conversation id is saved beside the stable thread id (`<home>/<thread>.json`, supervisor-only), so the next turn
//! resumes it on any model or effort. A failed or interrupted turn is never replayed.
//!
//! The process stays up after a turn that completed or was stopped (crate::keeper, as T3 Code keeps it), holding
//! the thread's conversation open: the thread's next turn sets only the model and thinking level it asks for and sends
//! its prompt there. A turn that would start agy differently (another home, environment, folder or MCP servers), a
//! conversation the process does not hold, or a process that has exited gets a new one, which resumes the saved
//! conversation; a kept process that is gone before the prompt went is replaced so, never after. A plan turn's is kept
//! too, for the thread's next turn on the same plan and sign-in, its home held until it goes
//! (model_subscriptions::keep_home). A stop of the turn is `session/cancel`, which keeps the process when
//! agy ends the prompt as cancelled in time. `agy` runs in a process group of its own, owned by the shared process
//! owner: a stop reaches the group, and whatever of it is left when `agy` exits goes too.
//!
//! The prompt goes only once agy has resumed the saved conversation or begun the turn's own: a turn that must continue
//! the saved one is never sent into another, and stops instead. On any other turn a conversation agy could not resume
//! gives way to a new one in the same process, given the whole prompt with the conversation's history when there is one
//! (`full_prompt`); the thread moves to it only once the model has worked there, its record asking that the next turn
//! be told everything again (`needs_history`) unless this one was and succeeded.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{ChildStdin, ChildStdout};
use tokio::sync::{oneshot, watch};

use super::{event, failed, is_limit, Events, HostError, Settings};
use crate::env::Env;
use crate::keeper::{Held, Inputs, Keeper, Session};
use crate::util::{log, py_dumps, text_of, truthy};

pub const DEFAULT_MODEL: &str = "gemini-4-neon";
const EFFORTS: &[&str] = &["low", "medium", "high", "xhigh"];
const DEFAULT_EFFORT: &str = "high";
/// Where agy keeps its sign-in, settings and conversations under its `--gemini-dir` (model_subscriptions::AGY_TOKEN).
pub const APP_DATA_DIR: &str = "antigravity-cli";
/// The Google account's sign-in method, which `authenticate` names (T3 Code's `oauth-personal`).
pub const AUTH_METHOD: &str = "oauth-personal";
/// What a typed `refusal` is: agy gives it for the model API's QUOTA_EXHAUSTED only (T3 Code's turnFailure).
const QUOTA: &str =
    "Antigravity usage limit: you have reached your Antigravity quota for this period (QUOTA_EXHAUSTED)";
/// The native session log's step that records an error (CORTEX_STEP_TYPE_ERROR_MESSAGE), and how much of what it says
/// is read: the error leads, a Go stack trace follows.
const ERROR_STEP: i64 = 17;
const ERROR_PAYLOAD: i64 = 16_384;
/// What agy prints in place of a protocol line when it would have a person open a sign-in link.
const AUTH_LINK: &str = "Open the following link to authenticate the ACP server: ";
// A test's stuck fake waits out the grace; a second is as good a proof as five.
const STOP_GRACE: Duration = Duration::from_secs(if cfg!(test) { 1 } else { 5 });
/// How long `agy` has to exit on its own once its input closes after the turn, before it is stopped.
const EXIT_WAIT: Duration = Duration::from_secs(1);
/// How long agy has to end a prompt as cancelled after `session/cancel` before its process is stopped (T3 Code's).
const CANCEL_WAIT: Duration = crate::keeper::INTERRUPT_WAIT;
/// How long `initialize` may take: agy unpacks its runtime on every start before it answers.
const START_WAIT: Duration = Duration::from_secs(if cfg!(test) { 10 } else { 120 });
/// How long any other request before the prompt may take.
const REQUEST_WAIT: Duration = Duration::from_secs(if cfg!(test) { 2 } else { 60 });
/// Lines that are not JSON (a banner, a warning) skipped before the stream counts as broken, and how many of the last
/// ones a failure's error keeps (each cut to KEPT_CHARS).
const SKIP_MAX: usize = 100;
const SKIP_KEPT: usize = 5;
const KEPT_CHARS: usize = 300;
/// The most of what `agy` said (stderr and skipped lines) a failure's error carries.
const ERROR_CHARS: usize = 1000;
/// What resuming a thread whose record is gone says: worded for continuity::missing, so the turn moves to a new thread.
const MISSING: &str = "No conversation found for this Antigravity thread on this computer";
/// A turn that must continue its saved conversation, which `agy` would not resume.
const LOST: &str = "No conversation found: Antigravity could not resume the saved conversation, so this turn was not sent";
/// A turn that must continue its saved conversation, which `agy` did not say it resumed in time.
const UNCONFIRMED: &str = "No conversation found: Antigravity did not say it resumed the saved conversation, so this turn was not sent";

/// `agy acp` on `home` (its `--gemini-dir`), everything of its own in APP_DATA_DIR there.
pub fn acp_argv(cmd: &[String], home: &str) -> Vec<String> {
    let mut argv = cmd.to_vec();
    argv.extend(["acp", "--gemini-dir", home, "--app-data-dir", APP_DATA_DIR].map(String::from));
    argv
}

/// The environment `agy acp` runs in (T3 Code's antigravityEnvironment): `env` without what would point it at another
/// home or open a browser, then its home, no self-update, unbuffered output, `tmp` as its TMPDIR and, on Linux, a CA
/// bundle its own Python may not find. A plan's (`plan`) also goes without any other Google sign-in, and keeps its
/// sign-in in a file of its home, never the keyring: told it runs over SSH, which the 1.5 CLI needs for that. Any other
/// keeps the computer's own sign-in (its keyring, a key or its Application Default Credentials) as it is.
pub fn acp_env(env: &Env, home: &str, plan: bool, tmp: &Path) -> Env {
    let t = crate::model_subscriptions::turn_home("antigravity");
    let mut strip: Vec<String> = vec!["GEMINI_HOME".into(), "BROWSER".into()];
    if plan {
        strip.extend(t.iter().flat_map(|t| t.strip.iter().map(|k| k.to_string())));
        strip.extend(crate::harness_tools::api_key_env("antigravity"));
    }
    let mut out: Env = env
        .iter()
        .filter(|(k, _)| {
            !strip.contains(*k) && !k.starts_with("AGY_ACP_") && !(plan && k.starts_with("AGY_"))
        })
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    out.extend(crate::harness_tools::env_for("antigravity"));
    for (k, v) in [
        ("GEMINI_HOME", home),
        ("AGY_CLI_DISABLE_AUTO_UPDATE", "1"),
        ("PYTHONUNBUFFERED", "1"),
    ] {
        out.insert(k.into(), v.into());
    }
    out.insert("TMPDIR".into(), tmp.display().to_string());
    if let Some(ca) = ca_bundle(env) {
        out.insert("SSL_CERT_FILE".into(), ca);
    }
    if plan {
        out.insert("AGY_ACP_FORCE_FILE_STORAGE".into(), "1".into());
        out.extend(
            t.iter()
                .flat_map(|t| t.env.iter().map(|(k, v)| (k.to_string(), v.to_string()))),
        );
    }
    out
}

/// The method `authenticate` names (T3 Code's authMethod): a plan's is always the Google account's. Any other turn keeps
/// the computer's own sign-in as `env` selects it: a Gemini API key, Google Cloud (its key or its Application Default
/// Credentials), else the Google account.
pub fn auth_method(env: &Env, plan: bool) -> &'static str {
    let set = |k: &str| env.get(k).is_some_and(|v| !v.trim().is_empty());
    if plan {
        AUTH_METHOD
    } else if set("GEMINI_API_KEY") {
        "gemini-api-key"
    } else if [
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AGY_ADC_AUTH",
    ]
    .iter()
    .any(|k| set(k))
        || env
            .get("GOOGLE_GENAI_USE_VERTEXAI")
            .is_some_and(|v| matches!(v.trim().to_lowercase().as_str(), "1" | "true"))
    {
        "agent-platform"
    } else {
        AUTH_METHOD
    }
}

/// A CA bundle for agy's own Python on Linux, which may look for one where this distribution has none, unless `env`
/// names one already.
fn ca_bundle(env: &Env) -> Option<String> {
    if !cfg!(target_os = "linux")
        || env.contains_key("SSL_CERT_FILE")
        || env.contains_key("SSL_CERT_DIR")
    {
        return None;
    }
    env.get("NIX_SSL_CERT_FILE")
        .cloned()
        .into_iter()
        .chain(
            [
                "/etc/ssl/certs/ca-bundle.crt",
                "/etc/ssl/certs/ca-certificates.crt",
                "/etc/pki/tls/certs/ca-bundle.crt",
                "/etc/ssl/cert.pem",
            ]
            .map(String::from),
        )
        .find(|p| Path::new(p).is_absolute() && Path::new(p).exists())
}

/// `initialize`'s parameters: Tico reads and writes files itself and runs no terminal for agy.
pub fn initialize_params() -> Value {
    json!({"protocolVersion": 1,
           "clientCapabilities": {"fs": {"readTextFile": false, "writeTextFile": false}, "terminal": false},
           "clientInfo": {"name": "tico", "title": "Tico", "version": env!("CARGO_PKG_VERSION")}})
}

/// How many models a session offers: its `model` config option's values, else ACP's `models` list.
pub fn models(session: &Value) -> usize {
    match option(&session["configOptions"], |o| o["id"] == "model") {
        Some(o) => values(o).len(),
        None => session["models"]["availableModels"]
            .as_array()
            .map_or(0, Vec::len),
    }
}

/// The values a select config option offers, its groups' included.
fn values(option: &Value) -> Vec<String> {
    option["options"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|o| match o.get("options").and_then(Value::as_array) {
            Some(group) => group.clone(),
            None => vec![o.clone()],
        })
        .filter_map(|o| o["value"].as_str().map(String::from))
        .collect()
}

fn option(options: &Value, which: impl Fn(&Value) -> bool) -> Option<&Value> {
    options
        .as_array()?
        .iter()
        .find(|o| o["type"] == "select" && which(o))
}

/// The model and effort a turn asks for: effort high unless set; a model id that names an effort (`gemini-4-neon-low`)
/// takes this turn's in its place.
fn model_effort(settings: &Settings, effort: Option<&str>) -> Result<(String, String), HostError> {
    let model = settings
        .model
        .clone()
        .filter(|m| !m.is_empty())
        .unwrap_or_else(|| DEFAULT_MODEL.into());
    let effort = effort
        .filter(|e| !e.is_empty())
        .or(settings.effort.as_deref().filter(|e| !e.is_empty()))
        .unwrap_or(DEFAULT_EFFORT);
    if !EFFORTS.contains(&effort) {
        return Err(failed("Unsupported Antigravity effort"));
    }
    let base = EFFORTS
        .iter()
        .find_map(|e| model.strip_suffix(&format!("-{e}")))
        .unwrap_or(&model);
    Ok((base.to_string(), effort.to_string()))
}

/// The value to set the session's `model` option to for `model` at `effort` (T3 Code's
/// applyAntigravityAcpModelSelection): the model when listed, else its id with the effort when that is (an older agy
/// lists them so); None when it is current or the session offers no choice. Err when the account does not have it.
fn model_value(options: &Value, model: &str, effort: &str) -> Result<Option<String>, String> {
    let Some(o) = option(options, |o| o["id"] == "model") else {
        return Ok(None);
    };
    let listed = values(o);
    let legacy = format!("{model}-{effort}");
    let want = if listed.iter().any(|v| v == model) {
        model.to_string()
    } else if listed.contains(&legacy) {
        legacy
    } else {
        return Err(format!("Antigravity model '{model}' is unavailable for this Google account. Select an available model."));
    };
    Ok((o["currentValue"].as_str() != Some(want.as_str())).then_some(want))
}

/// The id of the session's thinking level option (`thought_level`, offered for the current model only) to set to
/// `effort`: None when there is none, it does not offer `effort`, or that is current.
fn effort_option(options: &Value, effort: &str) -> Option<String> {
    let o = option(options, |o| o["category"] == "thought_level")?;
    (values(o).iter().any(|v| v == effort) && o["currentValue"].as_str() != Some(effort))
        .then(|| text_of(&o["id"]))
}

/// The bot's MCP servers as ACP names them (as hosts/grok.rs passes them), of the transports agy says it takes
/// (`mcpCapabilities`) only, as T3 Code passes them.
fn servers(settings: &Settings, init: &Value) -> Value {
    let caps = &init["agentCapabilities"]["mcpCapabilities"];
    let taken: Vec<Value> = settings
        .mcp_servers
        .iter()
        .filter(|s| caps[text_of(&s["transport"]).as_str()] == true)
        .cloned()
        .collect();
    json!(crate::mcp::grok_config(&taken, &settings.env))
}

/// A sign-in agy has not got (T3 Code's isAntigravitySignInRequiredError): `Authentication required` (-32000), or the
/// 1.5 CLI's -32603 when it has no sign-in and no browser.
fn signed_out(error: &Value) -> bool {
    match error["code"].as_i64() {
        Some(-32000) => true,
        Some(-32603) => error["message"]
            .as_str()
            .is_some_and(|m| m.starts_with("No browser is available here")),
        _ => false,
    }
}

/// What agy said refusing a request: its message, scrubbed of credentials and bounded, never its `data` as it is. The
/// data is read in memory only, for a usage limit or a sign-in agy has not got (hosts::is_auth_rejected_on), which
/// the text then names.
fn said(error: &Value, scrub: &crate::redact::Redactor) -> String {
    let message = error["message"].as_str().unwrap_or_default();
    let text: String = crate::run_diagnostics::safe(message.trim(), scrub)
        .chars()
        .take(ERROR_CHARS)
        .collect();
    let text = if text.is_empty() {
        format!("error {}", error["code"])
    } else {
        text
    };
    let data = error.get("data").map(|d| match d {
        Value::String(d) => d.clone(),
        d => d.to_string(),
    });
    let all = format!("{message} {}", data.unwrap_or_default());
    let auth = |t: &str| super::is_auth_rejected_on("antigravity", t);
    if (signed_out(error) || auth(&all)) && !auth(&text) {
        format!("Authentication required: {text}")
    } else if is_limit(&all) && !is_limit(&text) {
        format!("Antigravity usage limit: {text}")
    } else {
        text
    }
}

/// The turn's native session log (T3 Code's AntigravityTurnOutcome): agy records each step of `session`'s conversation
/// in it, an error it streams as chat text and ends with `end_turn` among them.
fn session_log(home: &str, session: &str) -> PathBuf {
    Path::new(home)
        .join(APP_DATA_DIR)
        .join("conversations")
        .join(format!("{session}.db"))
}

/// `read` on the session log's `steps` table, read only: Some(None) when it has no such table yet, None when the log
/// is not a plain file (a link could be another session's) or cannot be read.
fn steps<T>(
    log: &Path,
    read: impl FnOnce(&rusqlite::Connection) -> rusqlite::Result<T>,
) -> Option<Option<T>> {
    use rusqlite::OpenFlags;
    if !std::fs::symlink_metadata(log).ok()?.is_file() {
        return None;
    }
    let db = rusqlite::Connection::open_with_flags(
        log,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .ok()?;
    db.busy_timeout(Duration::from_millis(100)).ok()?;
    let tables: i64 = db
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'steps'",
            [],
            |r| r.get(0),
        )
        .ok()?;
    if tables == 0 {
        return Some(None);
    }
    read(&db).ok().map(Some)
}

/// The log's last step before the prompt: -1 when there is no log yet (a conversation agy begins with this turn); None
/// when it cannot be read, and only the prompt's typed answer counts, so no earlier step is taken for this turn's.
fn last_step(log: &Path) -> Option<i64> {
    match std::fs::symlink_metadata(log) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(-1),
        Err(_) => return None,
        Ok(_) => {}
    }
    let last = steps(log, |db| {
        db.query_row("SELECT coalesce(max(idx), -1) FROM steps", [], |r| r.get(0))
    })?;
    Some(last.unwrap_or(-1))
}

/// What the prompt's last step says when it records an error: a step after `before`, so never an earlier turn's.
fn step_error(log: &Path, before: i64) -> Option<String> {
    use rusqlite::types::Value as Sql;
    let (kind, payload) = steps(log, |db| {
        db.query_row(
            "SELECT step_type, substr(step_payload, 1, ?1) FROM steps WHERE idx > ?2 ORDER BY idx DESC LIMIT 1",
            rusqlite::params![ERROR_PAYLOAD, before],
            |r| Ok((r.get::<_, i64>(0)?, r.get::<_, Sql>(1)?)),
        )
    })??;
    (kind == ERROR_STEP).then(|| match payload {
        Sql::Text(t) => t,
        Sql::Blob(b) => String::from_utf8_lossy(&b).into_owned(),
        _ => String::new(),
    })
}

/// A failure from what an error step says (T3 Code's turnFailure): the quota, else the model API's own message and
/// status, else that there was an error. Scrubbed of credentials and bounded.
fn step_failure(text: &str, scrub: &crate::redact::Redactor) -> String {
    use regex::Regex;
    use std::sync::OnceLock;
    static R: OnceLock<[Regex; 3]> = OnceLock::new();
    let [quota, api, status] = R.get_or_init(|| {
        [
            Regex::new(r"\breason:QUOTA_EXHAUSTED\b").unwrap(),
            // The Go harness's rendering of a Google API error.
            Regex::new(r"(?s)Error (\d{3}), Message: (.+?), Status: ([A-Z_]+|\d{3}[^,\n]*)")
                .unwrap(),
            // Antigravity 1.5 with a Google account: "STATUS (code NNN): message".
            Regex::new(r"(?:^|[^A-Z_])([A-Z_]+) \(code (\d{3})\): ([^\x00-\x1f\x7f]+)").unwrap(),
        ]
    });
    if quota.is_match(text) {
        return QUOTA.into();
    }
    let (code, message, name) = match (api.captures(text), status.captures(text)) {
        (Some(c), _) => (c[1].to_string(), c[2].to_string(), c[3].to_string()),
        (None, Some(c)) => (c[2].to_string(), c[3].to_string(), c[1].to_string()),
        (None, None) => return "Antigravity stopped this turn because of an error".into(),
    };
    let bounded = |text: &str, most: usize| -> String {
        crate::run_diagnostics::safe(text.trim(), scrub)
            .chars()
            .take(most)
            .collect()
    };
    format!(
        "Antigravity stopped this turn: HTTP {code} {}: {}",
        bounded(&name, KEPT_CHARS),
        bounded(&message, ERROR_CHARS)
    )
}

/// What a stop reason that ends the turn unfinished means, as the turn's failure: None for `end_turn` and `cancelled`.
fn stopped(stop: &str) -> Option<String> {
    let why = match stop {
        "refusal" => QUOTA,
        "error" => "Antigravity stopped: error",
        "max_tokens" => "Antigravity stopped: the answer reached the model's output token limit",
        "max_turn_requests" => "Antigravity stopped: the turn reached its limit of model requests",
        _ => return None,
    };
    Some(why.into())
}

/// Python's `str(uuid.UUID(text))`: the canonical lower-case hyphenated form, or None when it is no uuid.
fn canonical(text: &str) -> Option<String> {
    uuid::Uuid::parse_str(text.trim())
        .ok()
        .map(|u| u.hyphenated().to_string())
}

/// A conversation id agy names: a uuid in its canonical form, else a plain token as it is.
fn conversation(text: &str) -> Option<String> {
    canonical(text).or_else(|| {
        let t = text.trim();
        (!t.is_empty()
            && t.len() <= 128
            && t.chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'))
        .then(|| t.to_string())
    })
}

/// Python's `int(value or 0)` for a usage count.
fn count(v: &Value) -> i64 {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => *b as i64,
        _ => 0,
    }
}

/// The last `n` characters of `text`.
fn last_chars(text: &str, n: usize) -> String {
    let skip = text.chars().count().saturating_sub(n);
    text.chars().skip(skip).collect()
}

/// The environment the turn's agy starts from: the turn's own, else this runner's.
fn env_of(settings: &Settings) -> Env {
    if settings.env.is_empty() {
        crate::env::process_environment()
    } else {
        settings.env.clone()
    }
}

/// The turn's `--gemini-dir`: its plan's home, else the bot's own `~/.gemini`.
fn home_for(settings: &Settings) -> Result<String, HostError> {
    if let Some(home) = &settings.plan_home {
        return Ok(home.clone());
    }
    settings
        .env
        .get("HOME")
        .cloned()
        .or_else(|| std::env::var("HOME").ok())
        .filter(|h| !h.is_empty())
        .map(|h| Path::new(&h).join(".gemini").display().to_string())
        .ok_or_else(|| failed("Antigravity needs a HOME"))
}

/// A running `agy`, owned by the shared process owner (super::own_with_before_reap): its exit code once reaped.
#[derive(Clone)]
struct Proc {
    stopper: super::Stopper,
    exited: watch::Receiver<Option<i32>>,
}

impl Proc {
    /// SIGTERM to the group, SIGKILL after STOP_GRACE, then until it has gone: whatever it was given (its home, its
    /// TMPDIR, the turn's folder) is not removed while it runs.
    async fn reap(&self) {
        let mut exited = self.exited.clone();
        if exited.borrow().is_none() {
            self.stopper.terminate(STOP_GRACE);
        }
        let _ = exited.wait_for(|e| e.is_some()).await;
    }
}

/// agy's input, which a stop of the turn writes to as well as its reader.
type Stdin = Arc<tokio::sync::Mutex<Option<ChildStdin>>>;

/// The two ends of agy's protocol stream.
struct Io {
    stdin: Stdin,
    stdout: Lines<BufReader<ChildStdout>>,
    next: i64,
}

/// Where a process's stderr goes: the events of the host holding it, none while it is kept between turns.
type Sink = Arc<Mutex<Option<Events>>>;

/// A running `agy acp`: its stream, its stderr, and what a later turn needs to go on in it.
struct Agent {
    proc: Proc,
    io: Io,
    stderr: crate::run_diagnostics::Stderr,
    sink: Sink,
    /// What it was started with (`fingerprint`); None when it is not to be kept (a plan whose home it cannot hold).
    key: Option<Inputs>,
    /// Its `--gemini-dir`, and a plan's home held while it runs.
    home: String,
    _plan: Option<crate::model_subscriptions::KeptHome>,
    /// The folder of the token file it reads HUB_TOKEN from (crate::warm).
    tokens: PathBuf,
    /// The scope it was launched in where turns are limited (crate::turn_limits::launch_unit).
    unit: Option<String>,
    /// The conversation it holds open, and that session's config options as they now stand.
    session: Option<String>,
    options: Value,
    /// Its output ended.
    closed: bool,
}

impl Agent {
    fn attach(&self, tx: Option<Events>) {
        *self.sink.lock().unwrap_or_else(|p| p.into_inner()) = tx;
    }
}

/// agy's adapter for the session keeper (crate::keeper): kept with its token blanked and its stderr going nowhere, what
/// it says meanwhile read by `between`. It has no background work of its own to report (as T3's ACP adapter).
impl Session for Agent {
    fn alive(&self) -> bool {
        !self.closed && self.proc.exited.borrow().is_none()
    }

    fn lend(&self, token: &str) -> bool {
        crate::warm::write_token_for(&self.tokens.join("lease-token"), token, None)
    }

    fn park(self: Box<Self>, wanted: oneshot::Receiver<()>) -> crate::keeper::Parked {
        self.attach(None);
        Box::pin(async move { Box::new(between(*self, wanted).await) as Box<dyn Session> })
    }

    /// Its input closed, then reaped (Proc::reap).
    fn stop(&self) -> crate::keeper::Stopping<'_> {
        Box::pin(async move {
            drop(self.io.stdin.lock().await.take());
            self.proc.reap().await;
        })
    }

    fn into_any(self: Box<Self>) -> Box<dyn std::any::Any + Send> {
        self
    }
}

impl Drop for Agent {
    fn drop(&mut self) {
        if self.proc.exited.borrow().is_none() {
            self.proc.stopper.terminate(STOP_GRACE);
        }
        let _ = std::fs::remove_dir_all(&self.tokens);
    }
}

/// How long a kept agy is given to read an answer to what it asked between turns.
const REPLY_WAIT: Duration = Duration::from_secs(5);

/// What a kept agy says between turns, until it is wanted back or its output ends: what it asks is answered, a tool's
/// permission refused as no turn runs, and the rest dropped, so the next turn reads only its own.
async fn between(mut agent: Agent, mut wanted: oneshot::Receiver<()>) -> Agent {
    loop {
        let line = tokio::select! {
            _ = &mut wanted => return agent,
            line = agent.io.stdout.next_line() => line,
        };
        let Ok(Some(line)) = line else {
            agent.closed = true;
            return agent;
        };
        let Ok(msg) = serde_json::from_str::<Value>(line.trim()) else {
            continue;
        };
        if let (Some(id), Some(method)) = (
            msg.get("id").filter(|i| !i.is_null()),
            msg["method"].as_str(),
        ) {
            let reply = if method == "session/request_permission" {
                json!({"jsonrpc": "2.0", "id": id, "result": {"outcome": {"outcome": "cancelled"}}})
            } else {
                json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "unsupported client request"}})
            };
            // agy may have stopped reading: the write is given up when the agent is wanted back or after a while, and
            // a stream left mid-line is not used again.
            let sent = tokio::select! {
                _ = &mut wanted => None,
                sent = tokio::time::timeout(REPLY_WAIT, write(&agent.io, &reply)) => sent.ok(),
            };
            if !matches!(sent, Some(Ok(()))) {
                agent.closed = true;
                return agent;
            }
        }
    }
}

/// What a process for this turn would be started with, the turn's own token and memory scope aside: its command line
/// (its home among it, a plan's named by the plan), environment, folder, records and MCP servers. The model and effort
/// are set in the session.
fn fingerprint(argv: &[String], env: &Env, settings: &Settings, records: &Path) -> Inputs {
    let mut inputs = Inputs::default();
    inputs.add("argv", &json!(argv).to_string());
    inputs.env(env, &["GEMINI_HOME"]);
    inputs.add("folder", &settings.cwd);
    inputs.add("records", &records.display().to_string());
    inputs.add("mcp_servers", &json!(settings.mcp_servers).to_string());
    inputs
}

/// One turn's stream as the reader sees it.
#[derive(Default)]
struct Stream {
    /// The session the turn runs in: only its updates are the turn's.
    session: String,
    /// The prompt has gone: updates from here on are the turn's events.
    prompting: bool,
    /// The turn's streamed reply, a paragraph between its messages (a tool call ends one).
    streamed: String,
    new_message: bool,
    /// The `messageId` of the last text chunk.
    message: Option<String>,
    /// The model worked in this conversation (a reply, a thought or a tool).
    worked: bool,
    /// A conversation agy began for this turn: kept only once the model works in it or the turn succeeds, so a refusal
    /// before any work leaves the saved one as it was.
    staged: Option<String>,
    /// agy could not resume the saved conversation, so `staged` is in its place.
    restored: bool,
    /// The turn's prompt was the whole one, the conversation's history included.
    told_all: bool,
    skipped: Vec<String>,
    skipped_count: usize,
    /// The tool calls reported (by `toolCallId`): each once, from its `tool_call` or an update that came first.
    tools: Vec<Value>,
}

/// How a turn's stream ended.
enum End {
    /// `session/prompt` answered: its result, and the failure the session log recorded for an `end_turn`.
    Result(Value, Option<String>),
    /// agy refused a request, or did not answer it in time: why.
    Failed(String),
    /// The stream closed, or broke (why).
    Closed(Option<String>),
    /// `agy` did not continue the saved conversation on a turn that must continue it: why.
    Mismatch(&'static str),
}

/// A request's answer.
enum Answer {
    Ok(Value),
    Err(Value),
    /// None in the time it had.
    Late,
}

/// `method` refused (or not answered in time) before the prompt went.
fn refused(method: &str, answer: Answer, scrub: &crate::redact::Redactor) -> String {
    match answer {
        Answer::Err(e) => format!("Antigravity {method} failed: {}", said(&e, scrub)),
        _ => format!("Antigravity did not answer {method} in time"),
    }
}

/// What one turn's reader works with.
struct Conv<'a> {
    tid: &'a str,
    turn: &'a str,
    agent: &'a mut Agent,
    stream: &'a mut Stream,
    scrub: &'a crate::redact::Redactor,
    /// `authenticate` names the Google account: a sign-in link agy prints ends the turn, as no person is there to open
    /// it. On a key or Google Cloud sign-in it is only skipped.
    oauth: bool,
}

struct Inner {
    up: bool,
    thread: Option<String>,
    settings: Option<Settings>,
    provider: Option<String>,
    /// The saved conversation was not the one `agy` ran: it began a new one for the thread.
    restored: bool,
    /// The thread's conversation lacks the conversation's history (its record's `needs_history`).
    needs_history: bool,
    /// `needs_history` was set during this turn: a turn told everything does not clear it then.
    flagged: bool,
    /// What a new conversation begun in place of the saved one is given instead of the turn's prompt.
    full_prompt: Option<String>,
    turn: Option<String>,
    /// The running turn's process, which a stop reaps while its reader holds it.
    proc: Option<Proc>,
    reader: Option<tokio::task::JoinHandle<()>>,
    /// The process between this attempt's turns, holding the thread's conversation open (`release`).
    agent: Option<Agent>,
    /// The prompt in flight: where `session/cancel` goes, and for which session.
    live: Option<(Stdin, String)>,
    /// The attempt's first process was a kept one already running (its timings' `warm`): set once.
    warm: Option<bool>,
    /// The launch scopes of the processes this attempt went on in (`scopes`).
    units: Vec<String>,
    tx: Events,
}

struct Shared {
    /// Held while `session/prompt` is written, and by `stop` while it records the stop: no prompt goes after one.
    gate: tokio::sync::Mutex<()>,
    allocation: super::Allocation,
    home: PathBuf,
    cmd: Vec<String>,
    keeper: Arc<Keeper>,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct AntigravityHost(Arc<Shared>);

impl AntigravityHost {
    pub fn new(home: &Path, cmd: Vec<String>, tx: Events, keeper: Arc<Keeper>) -> AntigravityHost {
        AntigravityHost(Arc::new(Shared {
            gate: tokio::sync::Mutex::new(()),
            allocation: super::Allocation::default(),
            home: home.to_path_buf(),
            cmd,
            keeper,
            inner: Mutex::new(Inner {
                up: false,
                thread: None,
                settings: None,
                provider: None,
                restored: false,
                needs_history: false,
                flagged: false,
                full_prompt: None,
                turn: None,
                proc: None,
                reader: None,
                agent: None,
                live: None,
                warm: None,
                units: vec![],
                tx,
            }),
        }))
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn emit(&self, ev: Value) {
        let _ = self.inner().tx.send(ev);
    }

    pub fn start(&self) -> Result<(), HostError> {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(&self.0.home)
            .map_err(|e| failed(format!("could not make the Antigravity home: {e}")))?;
        if !std::fs::symlink_metadata(&self.0.home)
            .map_err(|e| failed(e.to_string()))?
            .is_dir()
        {
            return Err(failed("Antigravity home must not be a symbolic link"));
        }
        // Only the runner reads the conversation records; agy is never given this folder.
        std::fs::set_permissions(&self.0.home, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| failed(e.to_string()))?;
        self.inner().up = true;
        Ok(())
    }

    pub fn track_allocation(&self, turn: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        self.0.allocation.track(turn);
    }

    /// Whether this host takes turns.
    pub fn alive(&self) -> bool {
        self.inner().up
    }

    /// Whether this attempt's first process was a kept one already running.
    pub fn warm(&self) -> bool {
        self.inner().warm.unwrap_or(false)
    }

    /// The launch scopes of the processes this attempt went on in (Host::kept_scopes): a kept one is still in the
    /// scope of the attempt that started it.
    pub fn scopes(&self) -> Vec<String> {
        self.inner().units.clone()
    }

    fn path(&self, thread: &str) -> Option<PathBuf> {
        canonical(thread).map(|t| self.0.home.join(format!("{t}.json")))
    }

    pub fn start_thread(&self, settings: &Settings) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        let mut inner = self.inner();
        inner.thread = Some(id.clone());
        inner.settings = Some(settings.clone());
        inner.provider = None;
        inner.restored = false;
        inner.needs_history = false;
        inner.full_prompt = None;
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        let missing = || failed(MISSING);
        let path = self.path(thread_id).ok_or_else(missing)?;
        let record: Value =
            serde_json::from_str(&std::fs::read_to_string(path).map_err(|_| missing())?)
                .map_err(|_| missing())?;
        let provider = record["conversation_id"]
            .as_str()
            .and_then(conversation)
            .ok_or_else(missing)?;
        let mut inner = self.inner();
        inner.provider = Some(provider);
        inner.thread = Some(thread_id.into());
        inner.settings = Some(settings.clone());
        inner.restored = false;
        inner.needs_history = record["needs_history"] == true;
        inner.full_prompt = None;
        Ok(thread_id.into())
    }

    /// Whether `thread_id`'s conversation lacks the conversation's history (hosts::Host::needs_history).
    pub fn needs_history(&self, thread_id: &str) -> bool {
        let inner = self.inner();
        inner.needs_history && inner.thread.as_deref() == Some(thread_id)
    }

    /// A turn that told `thread_id` everything completed: its record stops asking for the history, unless agy moved it
    /// during that turn.
    pub fn history_told(&self, thread_id: &str) {
        let mut inner = self.inner();
        if inner.thread.as_deref() != Some(thread_id) || !inner.needs_history || inner.flagged {
            return;
        }
        if let Some(cid) = inner.provider.clone() {
            if let Err(e) = self.save(&mut inner, &cid, false) {
                log(&format!(
                    "Antigravity: the conversation record was not updated: {e}"
                ));
            }
        }
    }

    pub fn full_prompt(&self, thread_id: &str, text: Option<String>) {
        let mut inner = self.inner();
        if inner.thread.as_deref() == Some(thread_id) {
            inner.full_prompt = text;
        }
    }

    /// Whether `agy` began a new conversation for `thread_id` in place of its saved one.
    pub fn restored(&self, thread_id: &str) -> bool {
        let inner = self.inner();
        inner.restored && inner.thread.as_deref() == Some(thread_id)
    }

    /// Save `cid` as the thread's conversation, and whether it lacks the conversation's history: one write, so the id
    /// is never kept without what the next turn must be told.
    fn save(&self, inner: &mut Inner, cid: &str, needs_history: bool) -> Result<(), String> {
        let path = inner
            .thread
            .as_deref()
            .and_then(|t| self.path(t))
            .ok_or("not a thread id")?;
        let tmp = path.with_extension("tmp");
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(|e| e.to_string())?;
        let record = if needs_history {
            json!({"conversation_id": cid, "needs_history": true})
        } else {
            json!({"conversation_id": cid})
        };
        f.write_all(py_dumps(&record).as_bytes())
            .map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
        inner.provider = Some(cid.into());
        inner.needs_history = needs_history;
        inner.flagged |= needs_history;
        Ok(())
    }

    /// The conversation agy began for this turn, once the model worked in it or the turn succeeded: a new thread's own,
    /// or one in place of the saved one, which then reads restored, its record saying it lacks the history (it holds
    /// this turn only) until a turn told everything succeeds there. Nothing is started again once the prompt went, so
    /// no work runs twice.
    fn commit(&self, inner: &mut Inner, stream: &mut Stream) -> Result<(), String> {
        if let Some(cid) = stream.staged.take() {
            if stream.restored {
                inner.restored = true;
            }
            self.save(inner, &cid, stream.restored)?;
        }
        Ok(())
    }

    /// `agy acp` for a turn on `home` with `tmp` as its TMPDIR, reading HUB_TOKEN from `token` when given (crate::warm),
    /// in a process group of its own, so a stop reaches the tools it started too.
    fn command(
        &self,
        settings: &Settings,
        home: &str,
        tmp: &Path,
        token: Option<&Path>,
    ) -> Result<tokio::process::Command, HostError> {
        // A plan's turn (model_subscriptions.rs TURN_HOMES): its sign-in is the file in its own home, and nothing else
        // may sign it in. Without that file in place agy is not started at all, so it never runs on the computer's own
        // sign-in instead.
        if let Some(home) = &settings.plan_home {
            crate::model_subscriptions::turn_home("antigravity")
                .ok_or_else(|| failed("Antigravity plans are not supported here"))?
                .sign_in(home)
                .map_err(failed)?;
        }
        let argv = acp_argv(&self.0.cmd, home);
        let mut env = env_of(settings);
        if let (Some(file), true) = (token, env.contains_key("HUB_TOKEN")) {
            env.insert("HUB_TOKEN".into(), format!("tico-file:{}", file.display()));
        }
        let mut command = super::harness_command(None, &argv[0]);
        command
            .args(&argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .current_dir(&settings.cwd)
            .env_clear()
            .envs(acp_env(&env, home, settings.plan_home.is_some(), tmp));
        super::new_group(&mut command);
        Ok(command)
    }

    /// Start a new `agy acp` for `settings` on `home`, reading HUB_TOKEN from a token file of its own, with a TMPDIR of
    /// its own (it unpacks its runtime there on every start), removed once it and its group have gone.
    fn spawn(
        &self,
        settings: &Settings,
        home: &str,
        key: Option<Inputs>,
    ) -> Result<Agent, HostError> {
        // A plan's home stays while the process runs, or it is not kept.
        let plan = match &settings.plan_home {
            Some(home) => crate::model_subscriptions::keep_home(Path::new(home)),
            None => None,
        };
        let key = key.filter(|_| settings.plan_home.is_none() || plan.is_some());
        let tokens = self.0.keeper.folder().map_err(failed)?;
        let file = tokens.join("lease-token");
        crate::warm::write_token_for(
            &file,
            settings.env.get("HUB_TOKEN").map_or("", String::as_str),
            None,
        );
        let tmp = std::env::temp_dir().join(format!("tico-agy-{}", uuid::Uuid::new_v4().simple()));
        let made = crate::isolation::mkdir(&tmp, 0o700)
            .map_err(|e| failed(format!("could not make Antigravity's TMPDIR: {e}")));
        let started = made
            .and_then(|_| self.command(settings, home, &tmp, Some(&file)))
            .and_then(|mut c| {
                c.spawn()
                    .map_err(|e| failed(format!("could not start agy: {e}")))
            });
        let mut child = match started {
            Ok(child) => child,
            Err(e) => {
                let _ = std::fs::remove_dir_all(&tmp);
                let _ = std::fs::remove_dir_all(&tokens);
                return Err(e);
            }
        };
        self.0.allocation.mark();
        let unit = crate::turn_limits::launch_unit(&env_of(settings), child.id());
        let sink: Sink = Arc::new(Mutex::new(Some(self.inner().tx.clone())));
        let to = sink.clone();
        let stderr = crate::run_diagnostics::Stderr::capture_with(
            &mut child,
            move |value| {
                if let Some(tx) = to.lock().unwrap_or_else(|p| p.into_inner()).as_ref() {
                    let _ = tx.send(value);
                }
            },
            &settings.env,
        );
        log(&format!(
            "antigravity: agy started pid {}",
            child.id().map_or("?".into(), |p| p.to_string())
        ));
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| failed("agy has no stdout"))?;
        let stdin = child.stdin.take();
        // Whatever is left of its group when agy exits, however it exits, goes with it: a tool it started never outlives
        // the process. Sent before agy is reaped, so the group's number is still its own.
        let group = child.id().map(|p| p as i32);
        let sweep = move || {
            if let Some(g) = group {
                // SAFETY: agy leads group `g` (new_group) and is not reaped yet.
                unsafe { libc::kill(-g, libc::SIGKILL) };
            }
        };
        let (stopper, status) = super::own_with_before_reap(child, Some(sweep));
        let (done, exited) = watch::channel(None);
        tokio::spawn(async move {
            let code = match status.await {
                Ok(status) => super::return_code(&status),
                Err(_) => -1,
            };
            let _ = tokio::task::spawn_blocking(move || std::fs::remove_dir_all(&tmp)).await;
            let _ = done.send(Some(code));
        });
        Ok(Agent {
            proc: Proc { stopper, exited },
            io: Io {
                stdin: Arc::new(tokio::sync::Mutex::new(stdin)),
                stdout: BufReader::new(stdout).lines(),
                next: 0,
            },
            stderr,
            sink,
            key,
            home: home.to_string(),
            _plan: plan,
            tokens,
            unit,
            session: None,
            options: Value::Null,
            closed: false,
        })
    }

    /// The process for this turn on `thread`, and whether it was already running: the one this attempt or the keeper
    /// holds when it was started as this turn would start it and holds the thread's saved conversation open, else a
    /// new one. A plan's is named by the plan and the sign-in this turn was handed, so a turn no longer handed it
    /// never goes on in its process.
    async fn agent(&self, thread: &str, settings: &Settings) -> Result<(Agent, bool), HostError> {
        let home = home_for(settings)?;
        let named = match &settings.plan_home {
            Some(plan) => {
                crate::model_subscriptions::turn_plan(Path::new(plan)).map(|p| format!("plan:{p}"))
            }
            None => Some(home.clone()),
        };
        let key = named.map(|named| {
            fingerprint(
                &acp_argv(&self.0.cmd, &named),
                &env_of(settings),
                settings,
                &self.0.home,
            )
        });
        let token = settings.env.get("HUB_TOKEN").map_or("", String::as_str);
        let held = self.inner().agent.take();
        let (held, pooled) = match (held, &key) {
            (Some(agent), _) => (Some(agent), false),
            (None, Some(key)) => {
                let agent = self.0.keeper.take::<Agent>(thread, key, token).await;
                let pooled = agent.is_some();
                (agent, pooled)
            }
            (None, None) => {
                self.0.keeper.release(thread).await;
                (None, false)
            }
        };
        if let Some(agent) = held {
            let saved = self.inner().provider.clone();
            if key.is_some()
                && agent.key == key
                && agent.alive()
                && agent.session.is_some()
                && agent.session == saved
                && (pooled || agent.lend(token))
            {
                agent.stderr.clear();
                agent.attach(Some(self.inner().tx.clone()));
                if let Some(plan) = &agent._plan {
                    plan.hide_current();
                }
                self.0.allocation.mark();
                let mut inner = self.inner();
                inner.warm.get_or_insert(pooled);
                inner.units.extend(agent.unit.clone());
                drop(inner);
                return Ok((agent, true));
            }
            agent.stop().await;
        }
        let agent = self.spawn(settings, &home, key)?;
        self.inner().warm.get_or_insert(false);
        Ok((agent, false))
    }

    pub async fn start_turn(
        &self,
        thread_id: &str,
        text: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let busy = || failed("Antigravity is unavailable or already running a turn");
        let settings = {
            let inner = self.inner();
            if !inner.up || inner.thread.as_deref() != Some(thread_id) || inner.turn.is_some() {
                return Err(busy());
            }
            inner.settings.clone().unwrap_or_default()
        };
        let (model, effort) = model_effort(&settings, effort)?;
        let (agent, warm) = self.agent(thread_id, &settings).await?;
        let taken = {
            let inner = self.inner();
            !inner.up || inner.turn.is_some()
        };
        if taken {
            agent.stop().await;
            return Err(busy());
        }
        let mut inner = self.inner();
        inner.flagged = false;
        inner.proc = Some(agent.proc.clone());
        inner.turn = Some(turn.clone());
        let host = self.clone();
        let (tid, id, text) = (thread_id.to_string(), turn.clone(), text.to_string());
        inner.reader = Some(tokio::spawn(async move {
            host.run(&tid, &id, &text, (&model, &effort), agent, warm)
                .await
        }));
        let _ = inner.tx.send(event(
            "status",
            Some(thread_id),
            Some(&turn),
            json!({"state": "active"}),
        ));
        Ok(turn)
    }

    /// The turn's one reader: it alone says how the turn ended.
    async fn run(
        &self,
        tid: &str,
        turn: &str,
        text: &str,
        (model, effort): (&str, &str),
        mut agent: Agent,
        mut warm: bool,
    ) {
        let mut stream = Stream::default();
        let settings = self.inner().settings.clone().unwrap_or_default();
        let scrub = crate::redact::Redactor::new(&crate::redact::env_secrets(&settings.env));
        let oauth = auth_method(&env_of(&settings), settings.plan_home.is_some()) == AUTH_METHOD;
        let end = loop {
            let end = {
                let mut c = Conv {
                    tid,
                    turn,
                    agent: &mut agent,
                    stream: &mut stream,
                    scrub: &scrub,
                    oauth,
                };
                self.converse(&mut c, text, model, effort, warm).await
            };
            // A kept process gone before the prompt went: a new one resumes the saved conversation instead. Once the
            // prompt went, nothing is sent again.
            if !(warm && matches!(end, End::Closed(_)) && !stream.prompting && self.inner().up) {
                break end;
            }
            warm = false;
            agent.stop().await;
            let key = agent.key.clone();
            let respawned = home_for(&settings).and_then(|home| self.spawn(&settings, &home, key));
            match respawned {
                Ok(new) => {
                    agent = new;
                    self.inner().proc = Some(agent.proc.clone());
                    stream = Stream::default();
                }
                Err(e) => break End::Failed(e.to_string()),
            }
        };
        self.inner().live = None;
        // agy answered the prompt cleanly and goes on: it stays for the thread's next turn (`release`). A failed answer
        // (a step error, a refusal, a cut-off) ends it like any failure, its stderr read for the turn's error.
        let clean = matches!(&end, End::Result(result, None)
            if stopped(result["stopReason"].as_str().unwrap_or("end_turn")).is_none());
        let healthy = clean && agent.alive();
        let kept = healthy && {
            let mut inner = self.inner();
            inner.up && inner.turn.as_deref() == Some(turn) && {
                inner.proc = None;
                true
            }
        };
        let mut code = None;
        if !kept {
            // Its input closes: agy takes that as the end of its work and exits.
            drop(agent.io.stdin.lock().await.take());
            // A stopped turn says nothing more, so nothing waits for its code.
            if self.inner().up {
                let wait = if matches!(end, End::Closed(_)) {
                    STOP_GRACE
                } else {
                    EXIT_WAIT
                };
                let mut exited = agent.proc.exited.clone();
                code = tokio::time::timeout(wait, exited.wait_for(|e| e.is_some()))
                    .await
                    .ok()
                    .and_then(|e| e.ok().and_then(|e| *e));
            }
            // Gone, its group with it, before the turn ends: the next turn is never admitted beside it.
            agent.proc.reap().await;
            agent.stderr.drained().await;
        }
        let stderr = agent.stderr.tail();
        if kept {
            // Held before the turn ends, so the next turn finds it; a stop meanwhile stops it here.
            let left = {
                let mut inner = self.inner();
                if inner.up {
                    inner.agent = Some(agent);
                    None
                } else {
                    Some(agent)
                }
            };
            if let Some(agent) = left {
                agent.stop().await;
            }
        }
        match end {
            End::Result(result, failure) => {
                self.finish(tid, turn, &result, failure, &stream, &stderr)
            }
            End::Failed(error) => self.fail(tid, turn, &error, &stderr),
            End::Closed(why) => {
                if let Some(why) = &why {
                    log(&format!("Antigravity stream failed: {why}"));
                }
                self.fail_closed(tid, turn, code, why.as_deref(), &stderr, &stream);
            }
            // The saved conversation stays as it was: a continuation must not run on another one.
            End::Mismatch(error) => self.end(
                tid,
                turn,
                event(
                    "turn_failed",
                    Some(tid),
                    Some(turn),
                    json!({"error": error, "limit": false}),
                ),
            ),
        }
    }

    /// The turn's whole conversation with agy, from `initialize` (in a process already running, from the model and
    /// effort) to the prompt's answer.
    async fn converse(
        &self,
        c: &mut Conv<'_>,
        text: &str,
        model: &str,
        effort: &str,
        warm: bool,
    ) -> End {
        let settings = self.inner().settings.clone().unwrap_or_default();
        let opened = match (warm, c.agent.session.clone()) {
            (true, Some(session)) => Ok((session, c.agent.options.clone())),
            _ => self.open(c, &settings).await,
        };
        let (session, mut options) = match opened {
            Ok(opened) => opened,
            Err(end) => return end,
        };
        c.stream.session = session.clone();
        c.agent.session = Some(session.clone());
        c.agent.options = options.clone();
        match model_value(&options, model, effort) {
            Err(e) => return End::Failed(e),
            Ok(Some(value)) => match self.set(c, &session, &options, "model", &value).await {
                Ok(now) => options = now,
                Err(end) => return end,
            },
            Ok(None) => {}
        }
        if let Some(id) = effort_option(&options, effort) {
            match self.set(c, &session, &options, &id, effort).await {
                Ok(now) => options = now,
                Err(end) => return end,
            }
        }
        c.agent.options = options;
        self.prompt(c, &session, text).await
    }

    /// `initialize`, `authenticate` and the thread's conversation resumed or begun: its id and config options.
    async fn open(&self, c: &mut Conv<'_>, settings: &Settings) -> Result<(String, Value), End> {
        let plan = settings.plan_home.is_some();
        let init = match self
            .call(c, "initialize", initialize_params(), Some(START_WAIT))
            .await
        {
            Ok(Answer::Ok(v)) => v,
            Ok(a) => return Err(End::Failed(refused("initialize", a, c.scrub))),
            Err(why) => return Err(End::Closed(why)),
        };
        let method = auth_method(&env_of(settings), plan);
        match self
            .call(
                c,
                "authenticate",
                json!({"methodId": method}),
                Some(REQUEST_WAIT),
            )
            .await
        {
            Ok(Answer::Ok(_)) => {}
            Ok(a) if plan => return Err(End::Failed(refused("authenticate", a, c.scrub))),
            // The session says whether the computer's own sign-in holds at all.
            Ok(a) => log(&format!(
                "Antigravity: {}",
                refused("authenticate", a, c.scrub)
            )),
            Err(why) => return Err(End::Closed(why)),
        }
        let servers = servers(settings, &init);
        let saved = self.inner().provider.clone();
        let mut resumed = None;
        if let Some(saved) = saved {
            let caps = &init["agentCapabilities"];
            // Resumed, as T3 Code does: a load would replay the conversation's history as updates.
            let method = if caps["sessionCapabilities"]["resume"].is_object() {
                "session/resume"
            } else if caps["loadSession"] == true {
                "session/load"
            } else {
                ""
            };
            let answer = if method.is_empty() {
                Answer::Err(json!({"message": "this agy cannot resume a conversation"}))
            } else {
                let params =
                    json!({"sessionId": saved, "cwd": settings.cwd, "mcpServers": servers});
                match self.call(c, method, params, Some(REQUEST_WAIT)).await {
                    Ok(a) => a,
                    Err(why) => return Err(End::Closed(why)),
                }
            };
            match answer {
                Answer::Ok(v) => resumed = Some((saved, v)),
                Answer::Err(e) if signed_out(&e) => {
                    return Err(End::Failed(refused(method, Answer::Err(e), c.scrub)))
                }
                Answer::Err(_) if settings.require_existing_session => {
                    return Err(End::Mismatch(LOST))
                }
                Answer::Late if settings.require_existing_session => {
                    return Err(End::Mismatch(UNCONFIRMED))
                }
                // agy could not resume it: a new conversation takes its place, given everything when that is to hand.
                _ => c.stream.restored = true,
            }
        }
        let (session, setup) = match resumed {
            Some(resumed) => resumed,
            None => {
                let params = json!({"cwd": settings.cwd, "mcpServers": servers});
                let v = match self
                    .call(c, "session/new", params, Some(REQUEST_WAIT))
                    .await
                {
                    Ok(Answer::Ok(v)) => v,
                    Ok(a) => return Err(End::Failed(refused("session/new", a, c.scrub))),
                    Err(why) => return Err(End::Closed(why)),
                };
                // Kept as agy named it, which its updates name it by too.
                let Some(id) = v["sessionId"]
                    .as_str()
                    .filter(|id| conversation(id).is_some())
                    .map(String::from)
                else {
                    return Err(End::Failed(
                        "Antigravity session/new named no conversation".into(),
                    ));
                };
                c.stream.staged = Some(id.clone());
                (id, v)
            }
        };
        Ok((session, setup["configOptions"].clone()))
    }

    /// The turn's prompt in `session`, and how agy answered it.
    async fn prompt(&self, c: &mut Conv<'_>, session: &str, text: &str) -> End {
        let full = if c.stream.restored {
            self.inner().full_prompt.take()
        } else {
            None
        };
        let prompt = match full {
            Some(full) => {
                c.stream.told_all = true;
                self.emit(event(
                    "diagnostic",
                    Some(c.tid),
                    Some(c.turn),
                    json!({"text": super::claude::RESTORING}),
                ));
                full
            }
            None => text.to_string(),
        };
        // Where the session log stands before the prompt, so only the steps it adds are read after.
        let native = Some(session_log(&c.agent.home, session));
        let before = match native.clone() {
            Some(log) => tokio::task::spawn_blocking(move || last_step(&log))
                .await
                .ok()
                .flatten(),
            None => None,
        };
        let params = json!({"sessionId": session, "prompt": [{"type": "text", "text": prompt}]});
        let answer = match self.call(c, "session/prompt", params, None).await {
            // An answer with neither a result nor an error is no answer.
            Ok(Answer::Ok(result)) if !result.is_object() => {
                Answer::Err(json!({"message": "Antigravity answered the prompt with no result"}))
            }
            Ok(a) => a,
            Err(why) => return End::Closed(why),
        };
        // An end_turn whose last step records an error: the model API failed and agy streamed that as its answer.
        let failure = match (&answer, native, before) {
            (Answer::Ok(result), Some(log), Some(before))
                if stopped(result["stopReason"].as_str().unwrap_or_default()).is_none() =>
            {
                tokio::task::spawn_blocking(move || step_error(&log, before))
                    .await
                    .ok()
                    .flatten()
                    .map(|text| step_failure(&text, c.scrub))
            }
            _ => None,
        };
        let mut inner = self.inner();
        let stream = &mut *c.stream;
        match answer {
            Answer::Ok(result) => {
                let stop = result["stopReason"].as_str().unwrap_or_default();
                let ok = failure.is_none() && stopped(stop).is_none();
                // A refusal before any work leaves the saved conversation as it was.
                if ok || stream.worked {
                    if let Err(e) = self.commit(&mut inner, stream) {
                        return End::Closed(Some(e));
                    }
                }
                // A turn told everything that completed in the conversation it began holds the history. Its answer
                // stands if that is not written down: the next turn is told everything again.
                if ok
                    && stop != "cancelled"
                    && stream.restored
                    && stream.told_all
                    && inner.needs_history
                {
                    let cid = inner.provider.clone().unwrap_or_default();
                    if let Err(e) = self.save(&mut inner, &cid, false) {
                        log(&format!(
                            "Antigravity: could not note the conversation holds its history: {e}"
                        ));
                    }
                }
                End::Result(result, failure)
            }
            Answer::Err(e) => {
                if stream.worked {
                    if let Err(e) = self.commit(&mut inner, stream) {
                        return End::Closed(Some(e));
                    }
                }
                End::Failed(said(&e, c.scrub))
            }
            Answer::Late => End::Closed(None),
        }
    }

    /// Set the session's config option `id` to `value`: the session's options as they are now, as agy says them, else
    /// `options` with that value current.
    async fn set(
        &self,
        c: &mut Conv<'_>,
        session: &str,
        options: &Value,
        id: &str,
        value: &str,
    ) -> Result<Value, End> {
        let params = json!({"sessionId": session, "configId": id, "value": value});
        match self
            .call(c, "session/set_config_option", params, Some(REQUEST_WAIT))
            .await
        {
            Ok(Answer::Ok(v)) => Ok(match v.get("configOptions").filter(|o| o.is_array()) {
                Some(now) => now.clone(),
                None => {
                    let mut now = options.clone();
                    if let Some(o) = now
                        .as_array_mut()
                        .and_then(|all| all.iter_mut().find(|o| o["id"] == id))
                    {
                        o["currentValue"] = json!(value);
                    }
                    now
                }
            }),
            Ok(a) => Err(End::Failed(refused(
                "session/set_config_option",
                a,
                c.scrub,
            ))),
            Err(why) => Err(End::Closed(why)),
        }
    }

    /// Send `method` and read agy's stream until it answers, within `wait` when given: what agy asks meanwhile is
    /// answered, and the session's updates once the prompt went are the turn's events. Err(why) when the stream ended
    /// (None: it closed, or the turn stopped).
    async fn call(
        &self,
        c: &mut Conv<'_>,
        method: &str,
        params: Value,
        wait: Option<Duration>,
    ) -> Result<Answer, Option<String>> {
        c.agent.io.next += 1;
        let id = c.agent.io.next;
        let gate = match method {
            "session/prompt" => Some(self.0.gate.lock().await),
            _ => None,
        };
        if gate.is_some() && !self.inner().up {
            return Err(None);
        }
        let prompted = (method == "session/prompt").then(|| text_of(&params["sessionId"]));
        write(
            &c.agent.io,
            &json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}),
        )
        .await?;
        // The prompt went: updates from here on are the turn's, and a stop of the turn is agy's `session/cancel`, which
        // agy reads only after the prompt.
        if let Some(session) = prompted {
            c.stream.prompting = true;
            self.inner().live = Some((c.agent.io.stdin.clone(), session));
        }
        drop(gate);
        let by = wait.map(|w| tokio::time::Instant::now() + w);
        loop {
            let next = match by {
                Some(by) => {
                    match tokio::time::timeout_at(by, c.agent.io.stdout.next_line()).await {
                        Ok(next) => next,
                        Err(_) => return Ok(Answer::Late),
                    }
                }
                None => c.agent.io.stdout.next_line().await,
            };
            let line = match next {
                Ok(Some(l)) => l,
                Ok(None) => return Err(None),
                Err(e) => return Err(Some(e.to_string())),
            };
            if line.trim().is_empty() {
                continue;
            }
            let msg: Value = match serde_json::from_str(line.trim()) {
                Ok(v) => v,
                Err(_) => {
                    // Its link is never passed on or kept: no person is there to open it.
                    if line.starts_with(AUTH_LINK) {
                        if !c.oauth {
                            continue;
                        }
                        return Err(Some(
                            "Authentication required: Antigravity asked for a Google sign-in in a browser".into(),
                        ));
                    }
                    // A banner or warning: skipped, and the last few kept, redacted, for a failure's error.
                    let stream = &mut *c.stream;
                    stream.skipped_count += 1;
                    if stream.skipped_count > SKIP_MAX {
                        return Err(Some("too many lines that are not JSON".into()));
                    }
                    let kept = crate::run_diagnostics::safe(&line, c.scrub);
                    stream
                        .skipped
                        .push(kept.trim().chars().take(KEPT_CHARS).collect());
                    if stream.skipped.len() > SKIP_KEPT {
                        stream.skipped.remove(0);
                    }
                    continue;
                }
            };
            if !self.inner().up {
                return Err(None);
            }
            let asked = msg.get("id").filter(|i| !i.is_null()).cloned();
            match (asked, msg["method"].as_str()) {
                (Some(asked), None) => {
                    if asked.as_i64() == Some(id) {
                        return Ok(match msg.get("error") {
                            Some(e) => Answer::Err(e.clone()),
                            None => Answer::Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
                        });
                    }
                }
                // agy asks its client: a tool's permission is given, as the turn runs with every permission (T3 Code
                // keeps such a session in its `default` mode and answers; the CLI refuses `yolo`).
                (Some(asked), Some(m)) => {
                    let reply = if m == "session/request_permission" {
                        json!({"jsonrpc": "2.0", "id": asked, "result": super::grok::permission(&msg["params"])})
                    } else {
                        json!({"jsonrpc": "2.0", "id": asked, "error": {"code": -32601, "message": "unsupported client request"}})
                    };
                    write(&c.agent.io, &reply).await?;
                }
                (None, Some("session/update")) => {
                    let p = &msg["params"];
                    if c.stream.prompting && text_of(&p["sessionId"]) == c.stream.session {
                        let done = {
                            let mut inner = self.inner();
                            self.on_update(&mut inner, c.tid, c.turn, c.stream, &p["update"])
                        };
                        done.map_err(Some)?;
                    }
                }
                _ => {}
            }
        }
    }

    /// One of the turn's session updates; Err ends the stream.
    fn on_update(
        &self,
        inner: &mut Inner,
        tid: &str,
        turn: &str,
        stream: &mut Stream,
        u: &Value,
    ) -> Result<(), String> {
        let text = u["content"]["text"]
            .as_str()
            .filter(|t| !t.is_empty())
            .map(String::from);
        let (tid, turn) = (Some(tid), Some(turn));
        match u["sessionUpdate"].as_str().unwrap_or_default() {
            "agent_message_chunk" => {
                stream.worked = true;
                if let Some(text) = text {
                    // A chunk names its message: one of another message than the chunk before it starts a new one.
                    let id = u["messageId"]
                        .as_str()
                        .filter(|i| !i.is_empty())
                        .map(String::from);
                    let moved = id.is_some() && stream.message.is_some() && id != stream.message;
                    if id.is_some() {
                        stream.message = id;
                    }
                    let new_message = std::mem::take(&mut stream.new_message) || moved;
                    let piece = super::add_piece(&mut stream.streamed, new_message, &text);
                    let _ = inner.tx.send(event(
                        "delta",
                        tid,
                        turn,
                        json!({"text": piece, "delta_kind": "text"}),
                    ));
                }
            }
            "agent_thought_chunk" => {
                stream.worked = true;
                if let Some(text) = text {
                    let _ = inner.tx.send(event(
                        "delta",
                        tid,
                        turn,
                        json!({"text": text, "delta_kind": "thought"}),
                    ));
                }
            }
            // A tool call ends the message before it. It is reported once, by its kind, never its title or input:
            // from its update when that comes first, so a tool that ran always counts as one.
            kind @ ("tool_call" | "tool_call_update") => {
                stream.worked = true;
                if kind == "tool_call" {
                    stream.new_message = true;
                }
                let id = u.get("toolCallId").cloned().unwrap_or(Value::Null);
                if id.is_null() || !stream.tools.contains(&id) {
                    if !id.is_null() {
                        stream.tools.push(id.clone());
                    }
                    let kind = u
                        .get("kind")
                        .filter(|k| truthy(Some(k)))
                        .map(text_of)
                        .unwrap_or_else(|| "tool".into());
                    let _ = inner.tx.send(event(
                        "tool",
                        tid,
                        turn,
                        json!({"tool": kind, "status": "started", "item_id": id}),
                    ));
                }
            }
            "plan" => stream.worked = true,
            _ => {}
        }
        if stream.worked {
            self.commit(inner, stream)?;
        }
        Ok(())
    }

    /// The turn's events from the prompt's answer.
    fn finish(
        &self,
        tid: &str,
        turn: &str,
        result: &Value,
        failure: Option<String>,
        stream: &Stream,
        stderr: &str,
    ) {
        // A turn stopped meanwhile says nothing more: no usage or answer after the stop.
        {
            let inner = self.inner();
            if !inner.up || inner.turn.as_deref() != Some(turn) {
                return;
            }
        }
        // The prompt's own usage: this turn's, however long the conversation. agy's input leaves out what it read from
        // the cache, which Tico's input counts.
        let usage = &result["usage"];
        let (i, o, c) = (
            count(&usage["inputTokens"]).max(0),
            count(&usage["outputTokens"]).max(0),
            count(&usage["cachedReadTokens"]).max(0),
        );
        // A refusal before any model call spent nothing, and says so by sending no usage: the hub may queue it again.
        if i + o + c > 0 {
            self.emit(event(
                "tokens",
                Some(tid),
                Some(turn),
                json!({"input": i + c, "output": o, "cached": c, "total": i + c + o, "usage": {"input": i + c, "cached": c, "output": o}}),
            ));
        }
        let stop = result["stopReason"].as_str().unwrap_or("end_turn");
        let failure = stopped(stop).or(failure);
        if let Some(failure) = failure {
            return self.fail(tid, turn, &failure, stderr);
        }
        self.emit(event(
            "message",
            Some(tid),
            Some(turn),
            json!({"text": stream.streamed.trim(), "final": true}),
        ));
        let status = if stop == "cancelled" {
            "interrupted"
        } else {
            "completed"
        };
        self.end(
            tid,
            turn,
            event(
                "turn_completed",
                Some(tid),
                Some(turn),
                json!({"status": status}),
            ),
        );
    }

    /// A turn agy refused: one failure carrying what it printed too (stderr, redacted and bounded:
    /// run_diagnostics::Stderr), which may name the reason a refusal does not, a sign-in or a quota.
    fn fail(&self, tid: &str, turn: &str, error: &str, stderr: &str) {
        // The quota (a typed refusal, or the session log's QUOTA_EXHAUSTED) is a usage limit whatever stderr says.
        let quota = error == QUOTA;
        let said = stderr.trim();
        let error = if said.is_empty() {
            error.to_string()
        } else {
            format!("{error}: {}", last_chars(said, ERROR_CHARS))
        };
        self.end(
            tid,
            turn,
            event(
                "turn_failed",
                Some(tid),
                Some(turn),
                json!({"error": error, "limit": quota || is_limit(&error)}),
            ),
        );
    }

    /// A process that ended with no answer: one failure carrying what it said, so a refusal it printed (on stderr or as
    /// a plain line) is read as one.
    fn fail_closed(
        &self,
        tid: &str,
        turn: &str,
        code: Option<i32>,
        why: Option<&str>,
        stderr: &str,
        stream: &Stream,
    ) {
        let reason = match (why, code) {
            (Some(why), _) => format!("Antigravity stream failed: {why}"),
            (None, Some(c)) => crate::run_diagnostics::process_reason(c, stderr),
            (None, None) => "Antigravity stream ended before a result".into(),
        };
        let said: Vec<&str> = stream
            .skipped
            .iter()
            .map(String::as_str)
            .chain(stderr.lines())
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .collect();
        let error = if said.is_empty() {
            reason
        } else {
            format!("{reason}: {}", last_chars(&said.join("\n"), ERROR_CHARS))
        };
        let mut failure = event(
            "turn_failed",
            Some(tid),
            Some(turn),
            json!({"error": error, "limit": is_limit(&error)}),
        );
        if let Some(c) = code.filter(|c| *c != 0) {
            failure["exit_code"] = json!(c);
            failure["process_exit"] = json!(true);
        }
        self.end(tid, turn, failure);
    }

    /// The turn's last events, unless it was stopped: a stop says nothing more.
    fn end(&self, tid: &str, turn: &str, outcome: Value) {
        let mut inner = self.inner();
        if !inner.up || inner.turn.as_deref() != Some(turn) {
            return;
        }
        let _ = inner.tx.send(outcome);
        let _ = inner.tx.send(event(
            "status",
            Some(tid),
            Some(turn),
            json!({"state": "idle"}),
        ));
        inner.turn = None;
    }

    pub fn steer(&self) -> Result<(), HostError> {
        Err(failed("Antigravity steering is not enabled"))
    }

    /// A stop of the running turn (T3 Code's): once its prompt went, agy is told `session/cancel` and ends the prompt
    /// as cancelled, and the process stays for the thread's next turn; one that does not within CANCEL_WAIT, or a turn
    /// not yet prompting, is stopped. With no turn running there is nothing to stop.
    pub async fn interrupt(&self) {
        let (running, live) = {
            let inner = self.inner();
            (inner.turn.is_some(), inner.live.clone())
        };
        if !running {
            return;
        }
        if let Some((stdin, session)) = live {
            let cancel = json!({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session}});
            // One deadline for the cancel and the prompt's end: agy may have stopped reading while the reader's own
            // write holds its input.
            let by = tokio::time::Instant::now() + CANCEL_WAIT;
            let sent = tokio::time::timeout_at(by, write_to(&stdin, &cancel)).await;
            if matches!(sent, Ok(Ok(()))) {
                // Lent while waited on, and given back however the wait ends (this future dropped too), so `stop`
                // still joins it.
                let mut lent = Lent(self, self.inner().reader.take());
                if let Some(reader) = lent.1.as_mut() {
                    if tokio::time::timeout_at(by, reader).await.is_ok() {
                        lent.1 = None;
                        return;
                    }
                }
            }
        }
        self.stop().await;
    }

    /// The attempt is over: with `keep`, a process holding the thread's conversation goes to the keeper with its token
    /// blanked, for the thread's next turn, with `held` (what its folder needs while it is kept, crate::keeper::Held);
    /// any other is stopped.
    pub async fn release(&self, keep: bool, held: Option<Held>) {
        let (agent, thread) = {
            let mut inner = self.inner();
            (inner.agent.take(), inner.thread.clone())
        };
        let Some(agent) = agent else { return };
        match (thread, agent.key.clone()) {
            (Some(thread), Some(key)) if keep && agent.alive() => {
                self.0.keeper.put(&thread, key, agent, held).await
            }
            _ => agent.stop().await,
        }
    }

    /// Stop the turn's process (Proc::reap), its reader, and the process this attempt holds.
    pub async fn stop(&self) {
        // A prompt being written finishes first, unless agy has stopped reading it: its stop ends that write.
        let gate = tokio::time::timeout(STOP_GRACE, self.0.gate.lock()).await;
        let (proc, reader, agent) = {
            let mut inner = self.inner();
            inner.up = false;
            inner.turn = None;
            inner.live = None;
            (inner.proc.take(), inner.reader.take(), inner.agent.take())
        };
        drop(gate);
        if let Some(p) = proc {
            p.reap().await;
        }
        if let Some(agent) = agent {
            agent.stop().await;
        }
        if let Some(r) = reader {
            let _ = tokio::time::timeout(Duration::from_secs(2), r).await;
        }
    }
}

/// The turn's reader taken from the host by `interrupt`, back in its place when dropped.
struct Lent<'a>(&'a AntigravityHost, Option<tokio::task::JoinHandle<()>>);

impl Drop for Lent<'_> {
    fn drop(&mut self) {
        if let Some(reader) = self.1.take() {
            self.0.inner().reader.get_or_insert(reader);
        }
    }
}

/// One protocol line to agy. A write that fails is agy gone: its exit says why.
async fn write(io: &Io, msg: &Value) -> Result<(), Option<String>> {
    write_to(&io.stdin, msg).await
}

async fn write_to(stdin: &Stdin, msg: &Value) -> Result<(), Option<String>> {
    let mut stdin = stdin.lock().await;
    let Some(s) = stdin.as_mut() else {
        return Err(None);
    };
    let line = msg.to_string() + "\n";
    s.write_all(line.as_bytes()).await.map_err(|_| None)?;
    s.flush().await.map_err(|_| None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use tokio::sync::mpsc;

    const SAVED: &str = "6f9619ff-8b86-d011-b42d-00c04fc964ff";
    const OTHER: &str = "0b2e6f1c-3a1d-4f43-9a51-6c2f2f7e9d10";
    /// agy 1.5.16's own answers with no sign-in, as it gave them on a Linux computer told it runs over SSH.
    const NO_BROWSER: &str = "No browser is available here (this is a remote (SSH) session, so the browser's sign-in redirect can't reach this machine). Run `agy acp --login` in a terminal on this machine, then retry; or use a Gemini API key.";

    #[tokio::test]
    async fn turn_launch_keeps_the_bot_identity_and_a_supervisor_only_record() {
        if !super::super::isolationtests::child("hosts::antigravity::tests::turn_launch_keeps_the_bot_identity_and_a_supervisor_only_record") { return; }
        let f = super::super::isolationtests::Fixture::new();
        let mut settings = f.settings();
        let host = AntigravityHost::new(
            &f.root.join("agy/acme"),
            f.argv(),
            mpsc::unbounded_channel().0,
            Arc::new(Keeper::new(f.root.join("agy-warm"), 4)),
        );
        let home = home_for(&settings).unwrap();
        let tmp = f.root.join("tmp");
        super::super::isolationtests::prefix(
            &host.command(&settings, &home, &tmp, None).unwrap(),
            &acp_argv(&f.argv(), &home),
        );
        if !f.actual() {
            return;
        }
        host.start().unwrap();
        let thread = host.start_thread(&settings);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        let record = host.path(&thread).unwrap();
        settings
            .env
            .insert("TEST_PRIVATE".into(), record.display().to_string());
        f.run(
            host.command(&settings, &home, &tmp, None).unwrap(),
            &[],
            &[],
        )
        .await;
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            record.metadata().unwrap().uid(),
            0,
            "conversation record stays supervisor-only"
        );
    }

    /// An `agy acp` for one turn, answering as agy 1.5.16 does (its `initialize` as it gave it). It logs its argv, its
    /// pid, every request after `initialize` (`.calls`), its client's answers (`.replies`) and the prompt (`.prompts`).
    /// A prompt is answered with the fixture (`$FIXTURE`: its lines, `@ID@` the prompt's id and `@SID@` the session's;
    /// with its `.stderr` beside it, that on stderr and exit 1 in place of an answer), or with the prompt as the reply.
    /// `session/new` begins `$NEW` (else SAVED). A resume is refused with `$LOSE`, never answered with `$NOINIT`. With
    /// `$REFUSE_FRESH` a prompt in a conversation it began is refused; with `$SIGNED_OUT` it has no sign-in; with `$LINK`
    /// it prints a sign-in link on `authenticate` and has no Google sign-in. A prompt adds a `$STEP` step (`$STEP_TEXT`)
    /// to the conversation's session log, and logs the token its HUB_TOKEN file holds (`.tokens`). A prompt `hang` is
    /// answered only by `session/cancel`, as cancelled; a prompt `wait` streams `early`, then ` late` once `.go` is
    /// there. With `$STUCK`
    /// it ignores SIGTERM (then writes `.ready`) and never answers; with `$ORPHAN` it leaves a tool that ignores SIGTERM
    /// in its group. It exits when its input closes.
    fn fake_agy(dir: &Path) -> String {
        let path = dir.join("fake-agy");
        crate::util::write_script(
            &path,
            &r##"#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
env = os.environ.get
log = env("ARGS_LOG")
open(log, "a").write(" ".join(sys.argv[1:]) + "\n")
open(log + ".pid", "w").write(str(os.getpid()))
if env("STUCK"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open(log + ".ready", "w").close()
    while True:
        time.sleep(1)
if env("ORPHAN"):
    tool = subprocess.Popen(["sh", "-c", "trap '' TERM; exec sleep 30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(log + ".orphan", "w").write(str(tool.pid))
INIT = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True}, "sessionCapabilities": {"resume": {}, "close": {}, "load": {}, "list": {}, "additionalDirectories": {}}, "auth": {"logout": {}}, "_meta": {"antigravity": {"tier": True, "systemPrompt": {"append": True}}}}, "agentInfo": {"name": "Antigravity ACP Server", "version": "1.5.16"}, "authMethods": [{"id": "oauth-personal", "name": "Log in with Google", "description": "Log in with your Google account"}]}
NO_BROWSER = "@NO_BROWSER@"
model, level, sid, fresh, hung = "gemini-4-neon", "high", None, False, None
hub = env("HUB_TOKEN") or ""
open(log + ".hub", "w").write(hub)
def chunk(text):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
def options():
    return [{"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": model,
             "options": [{"value": "gemini-4-neon", "name": "Gemini 4 Neon"}, {"value": "gemini-4-hydrogen", "name": "Gemini 4 Hydrogen"}]},
            {"id": "thinking_level", "name": "Thinking", "category": "thought_level", "type": "select", "currentValue": level,
             "options": [{"value": e, "name": e} for e in ("low", "medium", "high", "xhigh")]}]
def send_line(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
def send(m):
    send_line(json.dumps(m))
def answer(i, result):
    send({"jsonrpc": "2.0", "id": i, "result": result})
def refuse(i, code, message):
    send({"jsonrpc": "2.0", "id": i, "error": {"code": code, "message": message}})
for line in sys.stdin:
    m = json.loads(line)
    i, method, p = m.get("id"), m.get("method"), m.get("params") or {}
    if method is None:
        open(log + ".replies", "a").write(line)
        continue
    if method != "initialize":
        open(log + ".calls", "a").write(method + " " + json.dumps(p, sort_keys=True) + "\n")
    if method == "session/cancel":
        if hung is not None:
            answer(hung, {"stopReason": "cancelled"})
            hung = None
    elif method == "initialize":
        answer(i, INIT)
    elif method == "authenticate":
        if env("LINK"):
            send_line("Open the following link to authenticate the ACP server: https://accounts.acme.example/o/oauth2/auth?state=s1")
        if env("SIGNED_OUT") or (env("LINK") and p["methodId"] == "oauth-personal"):
            refuse(i, -32603, NO_BROWSER)
        else:
            answer(i, {})
    elif method in ("session/resume", "session/load"):
        if env("SIGNED_OUT"):
            refuse(i, -32000, "Authentication required")
        elif env("LOSE"):
            refuse(i, -32603, "Conversation not found")
        elif not env("NOINIT"):
            sid = p["sessionId"]
            answer(i, {"configOptions": options()})
    elif method == "session/new":
        if env("SIGNED_OUT"):
            refuse(i, -32000, "Authentication required")
        else:
            sid, fresh = env("NEW") or "@SAVED@", True
            answer(i, {"sessionId": sid, "configOptions": options()})
    elif method == "session/set_config_option":
        if p["configId"] == "model":
            model = p["value"]
        else:
            level = p["value"]
        answer(i, {"configOptions": options()})
    elif method == "session/prompt":
        text = p["prompt"][0]["text"]
        open(log + ".prompts", "a").write(text + "\n")
        if hub.startswith("tico-file:"):
            open(log + ".tokens", "a").write(open(hub[len("tico-file:"):]).read() + "\n")
        if env("STEP"):
            import sqlite3
            home = sys.argv[sys.argv.index("--gemini-dir") + 1]
            folder = os.path.join(home, "antigravity-cli", "conversations")
            os.makedirs(folder, exist_ok=True)
            db = sqlite3.connect(os.path.join(folder, sid + ".db"))
            db.execute("CREATE TABLE IF NOT EXISTS steps (idx INTEGER PRIMARY KEY, step_type INTEGER, step_payload BLOB)")
            db.execute("INSERT INTO steps (step_type, step_payload) VALUES (?, ?)", (int(env("STEP")), (env("STEP_TEXT") or "").encode()))
            db.commit()
            db.close()
        fixture = env("FIXTURE")
        if fixture:
            for row in open(fixture).read().splitlines():
                if row.strip():
                    sys.stdout.write(row.replace("@ID@", str(i)).replace("@SID@", sid) + "\n")
            sys.stdout.flush()
            stderr = fixture[: -len(".jsonl")] + ".stderr"
            if os.path.exists(stderr):
                sys.stderr.write(open(stderr).read())
                sys.stderr.flush()
                sys.exit(1)
        elif env("REFUSE_FRESH") and fresh:
            refuse(i, -32603, "no")
        elif text == "hang":
            hung = i
        elif text == "wait":
            chunk("early")
            for _ in range(500):
                if os.path.exists(log + ".go"):
                    break
                time.sleep(0.02)
            chunk(" late")
            answer(i, {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 2}})
        else:
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
            answer(i, {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 2}})
    else:
        refuse(i, -32601, "unknown")
"##
            .replace("@SAVED@", SAVED)
            .replace("@NO_BROWSER@", NO_BROWSER),
        );
        path.display().to_string()
    }

    #[test]
    fn only_a_plan_turns_agy_runs_over_ssh_and_on_nothing_but_the_plan() {
        use crate::model_subscriptions as subs;
        let rig = Rig::new();
        let value = r#"{"token":{"access_token":"ya29.synthetic-access-token-0123456789"}}"#;
        let items = json!([{"id": "agyplan", "runtime": "antigravity", "kind": "antigravity_login",
                            "file": subs::AGY_TOKEN, "value": value, "revision": 1, "billing": "subscription"}]);
        let mut env = rig.settings(&[]).env;
        env.insert("GEMINI_API_KEY".into(), "AIza-computer-key".into());
        // The computer's Google Cloud credentials (Application Default Credentials), which no plan turn may use.
        env.insert("AGY_ADC_AUTH".into(), "1".into());
        env.insert("BROWSER".into(), "firefox".into());
        let mut got = subs::apply(
            &rig.dir,
            &items,
            &mut env,
            &mut vec![],
            &mut Default::default(),
        );
        subs::start(&mut got, "antigravity", &mut env).unwrap();
        let plan_home = subs::plan_home(&got, "antigravity");
        let home = PathBuf::from(plan_home.clone().unwrap());
        assert_eq!(env["GEMINI_HOME"], home.display().to_string());
        assert_eq!(
            std::fs::read_to_string(home.join(subs::AGY_TOKEN)).unwrap(),
            value
        );
        let command = |env: &crate::env::Env, plan_home: &Option<String>| {
            let settings = Settings {
                env: env.clone(),
                plan_home: plan_home.clone(),
                ..rig.settings(&[])
            };
            let (host, _rx) = rig.host();
            let home = home_for(&settings).unwrap();
            host.command(&settings, &home, &rig.dir.join("tmp"), None)
                .map(|command| {
                    let command = command.as_std();
                    let args: Vec<String> = command
                        .get_args()
                        .map(|a| a.to_string_lossy().to_string())
                        .collect();
                    let envs = command
                        .get_envs()
                        .map(|(k, v)| {
                            (
                                k.to_string_lossy().to_string(),
                                v.map(|v| v.to_string_lossy().to_string()),
                            )
                        })
                        .collect::<BTreeMap<String, Option<String>>>();
                    (args, envs)
                })
        };
        // A plan turn's agy, and one started again on the same turn whatever its environment says, runs over SSH on
        // the plan's home, with its sign-in in a file there, and nothing else.
        let mut lost = env.clone();
        lost.remove("GEMINI_HOME");
        for env in [&env, &lost] {
            let (args, plan) = command(env, &plan_home).unwrap();
            assert_eq!(
                args.join(" "),
                format!(
                    "acp --gemini-dir {} --app-data-dir antigravity-cli",
                    home.display()
                )
            );
            assert_eq!(plan["SSH_CONNECTION"].as_deref(), Some(subs::OVER_SSH.1));
            assert_eq!(plan["GEMINI_HOME"], plan_home);
            assert_eq!(plan["AGY_ACP_FORCE_FILE_STORAGE"].as_deref(), Some("1"));
            assert_eq!(plan["AGY_CLI_DISABLE_AUTO_UPDATE"].as_deref(), Some("1"));
            assert_eq!(
                plan["TMPDIR"].as_deref(),
                Some(rig.dir.join("tmp").to_str().unwrap())
            );
            for key in ["GEMINI_API_KEY", "AGY_ADC_AUTH", "BROWSER"] {
                assert!(plan.get(key).is_none_or(Option::is_none), "{plan:?}");
            }
        }
        // Tico's own commands in the turn (git) get the turn's environment, which never says SSH and keeps the bot's key.
        assert!(!env.contains_key("SSH_CONNECTION") && env.contains_key("GEMINI_API_KEY"));
        // On the computer's own sign-in (a Mac's keychain) agy runs on the bot's own ~/.gemini, never told it runs
        // over SSH or to keep its sign-in in a file, and keeps the computer's key.
        let mut own = env.clone();
        own.remove("GEMINI_HOME");
        let (args, computer) = command(&own, &None).unwrap();
        let gemini = format!("{}/.gemini", own["HOME"]);
        assert!(
            args.join(" ").contains(&format!("--gemini-dir {gemini} ")),
            "{args:?}"
        );
        assert_eq!(computer["GEMINI_HOME"].as_deref(), Some(gemini.as_str()));
        assert!(
            !computer.contains_key("SSH_CONNECTION")
                && !computer.contains_key("AGY_ACP_FORCE_FILE_STORAGE")
        );
        assert_eq!(
            computer["GEMINI_API_KEY"].as_deref(),
            Some("AIza-computer-key")
        );
        assert!(!computer.contains_key("BROWSER"));
        // Its sign-in gone (a home let go), a plan turn's agy is not started at all.
        subs::finish(&mut got);
        assert!(!home.exists());
        assert!(command(&env, &plan_home).is_err());
    }

    async fn turn_events(rx: &mut mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
        let mut got = vec![];
        while let Ok(Some(mut ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await
        {
            if ev["kind"] == "diagnostic" && ev.get("stderr_tail").is_some() {
                continue;
            }
            let end = ev["kind"] == "status" && ev["state"] == "idle";
            ev.as_object_mut()
                .unwrap()
                .retain(|k, _| k != "thread_id" && k != "turn_id");
            got.push(ev);
            if end {
                break;
            }
        }
        got
    }

    struct Rig {
        dir: PathBuf,
        cmd: String,
        pool: Arc<Keeper>,
    }

    impl Rig {
        fn new() -> Rig {
            let dir =
                std::env::temp_dir().join(format!("fake-agy-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(&dir).unwrap();
            let cmd = fake_agy(&dir);
            let pool = Arc::new(Keeper::new(dir.join("agy-warm"), 4));
            Rig { dir, cmd, pool }
        }

        fn settings(&self, vars: &[(&str, String)]) -> Settings {
            let mut s = Settings {
                cwd: self.dir.display().to_string(),
                model: Some("gemini-4-hydrogen".into()),
                ..Default::default()
            };
            s.env.insert("PATH".into(), "/usr/bin:/bin".into());
            s.env
                .insert("HOME".into(), self.dir.join("home").display().to_string());
            s.env.insert(
                "ARGS_LOG".into(),
                self.dir.join("args.log").display().to_string(),
            );
            for (k, v) in vars {
                s.env.insert(k.to_string(), v.clone());
            }
            s
        }

        fn host(&self) -> (AntigravityHost, mpsc::UnboundedReceiver<Value>) {
            self.host_on(self.pool.clone())
        }

        fn host_on(&self, pool: Arc<Keeper>) -> (AntigravityHost, mpsc::UnboundedReceiver<Value>) {
            let (tx, rx) = mpsc::unbounded_channel();
            let host =
                AntigravityHost::new(&self.dir.join("agy/acme"), vec![self.cmd.clone()], tx, pool);
            host.start().unwrap();
            (host, rx)
        }

        fn pid(&self) -> i32 {
            self.read(".pid").trim().parse().unwrap()
        }

        fn read(&self, what: &str) -> String {
            std::fs::read_to_string(self.dir.join(format!("args.log{what}"))).unwrap_or_default()
        }

        fn runs(&self) -> Vec<String> {
            self.read("").lines().map(String::from).collect()
        }
    }

    impl Drop for Rig {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    #[tokio::test]
    async fn every_antigravity_fixture_gives_its_events() {
        let rig = Rig::new();
        for (path, expected) in crate::hosts::fixtures::cases("antigravity") {
            let (host, mut rx) = rig.host();
            let s = rig.settings(&[("FIXTURE", path.display().to_string())]);
            let thread = host.start_thread(&s);
            host.start_turn(&thread, "hello", None).await.unwrap();
            assert_eq!(turn_events(&mut rx).await, expected, "{}", path.display());
            host.stop().await;
        }
        // The tool's permission agy asked for was given.
        assert!(
            rig.read(".replies").contains(r#""optionId":"allow""#),
            "{}",
            rig.read(".replies")
        );
    }

    #[tokio::test]
    async fn a_new_process_resumes_the_conversation_on_any_effort() {
        let rig = Rig::new();
        let s = rig.settings(&[]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", Some("medium"))
            .await
            .unwrap();
        let first = turn_events(&mut rx).await;
        assert!(
            first.contains(&json!({"kind": "message", "text": "one", "final": true})),
            "{first:?}"
        );
        host.stop().await;
        // The next turn, on another effort and another host: the saved conversation goes on.
        let (cold, mut rx) = rig.host();
        assert!(crate::continuity::missing(
            &cold
                .resume_thread(&uuid::Uuid::new_v4().to_string(), &s)
                .unwrap_err()
                .to_string()
        ));
        cold.resume_thread(&thread, &s).unwrap();
        cold.start_turn(&thread, "two", Some("xhigh"))
            .await
            .unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "two", "final": true})));
        assert!(!cold.restored(&thread));
        cold.stop().await;
        let home = rig.dir.join("home/.gemini");
        let argv = format!(
            "acp --gemini-dir {} --app-data-dir antigravity-cli",
            home.display()
        );
        assert_eq!(rig.runs(), [argv.clone(), argv]);
        let calls: Vec<String> = rig.read(".calls").lines().map(String::from).collect();
        let cwd = rig.dir.display().to_string();
        let set = |id: &str, value: &str| {
            format!(
                r#"session/set_config_option {{"configId": "{id}", "sessionId": "{SAVED}", "value": "{value}"}}"#
            )
        };
        assert_eq!(
            calls[..4],
            [
                r#"authenticate {"methodId": "oauth-personal"}"#.to_string(),
                format!(r#"session/new {{"cwd": "{cwd}", "mcpServers": []}}"#),
                set("model", "gemini-4-hydrogen"),
                set("thinking_level", "medium"),
            ]
        );
        assert_eq!(
            calls[5..9],
            [
                r#"authenticate {"methodId": "oauth-personal"}"#.to_string(),
                format!(
                    r#"session/resume {{"cwd": "{cwd}", "mcpServers": [], "sessionId": "{SAVED}"}}"#
                ),
                set("model", "gemini-4-hydrogen"),
                set("thinking_level", "xhigh"),
            ]
        );
    }

    #[tokio::test]
    async fn a_sign_in_agy_has_not_got_fails_the_turn_as_one() {
        // agy's own answers with no sign-in: authenticate's (a computer's own sign-in may be another method, so the
        // turn goes on to ask for a session), then session/new's.
        let rig = Rig::new();
        let s = rig.settings(&[("SIGNED_OUT", "1".into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        let failed = events.iter().find(|e| e["kind"] == "turn_failed").unwrap();
        let error = text_of(&failed["error"]);
        assert!(
            super::super::is_auth_rejected_on("antigravity", &error),
            "{error}"
        );
        assert_eq!(failed["limit"], false);
        host.stop().await;
        assert!(!rig.dir.join("args.log.prompts").exists(), "never sent");
        // A plan turn's authenticate refusal, and a -32000 by any words, read as a sign-in it has not got too.
        for refusal in [
            json!({"code": -32603, "message": NO_BROWSER}),
            json!({"code": -32000, "message": "Not signed in"}),
        ] {
            let error = refused(
                "authenticate",
                Answer::Err(refusal),
                &crate::redact::Redactor::new(&[]),
            );
            assert!(
                super::super::is_auth_rejected_on("antigravity", &error),
                "{error}"
            );
        }
        // Its data is read for what it is, never passed on.
        let data = json!({"code": -32603, "message": "Internal error",
                          "data": {"token": "ya29.synthetic-secret", "reason": "QUOTA_EXHAUSTED"}});
        let text = said(&data, &crate::redact::Redactor::new(&[]));
        assert!(!text.contains("ya29") && is_limit(&text), "{text}");
        assert!(!super::super::is_auth_rejected_on(
            "antigravity",
            &said(
                &json!({"code": -32603, "message": "Internal error"}),
                &crate::redact::Redactor::new(&[])
            )
        ));
    }

    #[tokio::test]
    async fn a_key_or_google_cloud_turn_keeps_its_method_and_a_sign_in_link_does_not_end_it() {
        for (var, method) in [
            ("GEMINI_API_KEY", "gemini-api-key"),
            ("AGY_ADC_AUTH", "agent-platform"),
        ] {
            let rig = Rig::new();
            let s = rig.settings(&[(var, "1".into()), ("LINK", "1".into())]);
            let (host, mut rx) = rig.host();
            let thread = host.start_thread(&s);
            host.start_turn(&thread, "hi", None).await.unwrap();
            let events = turn_events(&mut rx).await;
            assert!(
                events.iter().any(|e| e["kind"] == "turn_completed"),
                "{var}: {events:?}"
            );
            assert!(events
                .iter()
                .all(|e| !e.to_string().contains("accounts.acme.example")));
            host.stop().await;
            assert!(
                rig.read(".calls")
                    .starts_with(&format!(r#"authenticate {{"methodId": "{method}"}}"#)),
                "{}",
                rig.read(".calls")
            );
        }
        // On the Google account the link ends the turn as a sign-in it has not got, and a plan's is always that.
        let rig = Rig::new();
        let s = rig.settings(&[("LINK", "1".into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        let failed = events.iter().find(|e| e["kind"] == "turn_failed").unwrap();
        assert!(super::super::is_auth_rejected_on(
            "antigravity",
            &text_of(&failed["error"])
        ));
        host.stop().await;
        let key: Env = [("GEMINI_API_KEY".to_string(), "k".to_string())].into();
        assert_eq!(auth_method(&key, true), AUTH_METHOD);
        assert_eq!(auth_method(&Env::new(), false), AUTH_METHOD);
    }

    #[tokio::test]
    async fn an_error_the_session_log_records_fails_the_turn_agy_ended_as_an_answer() {
        let rig = Rig::new();
        let error = "Error 503, Message: The model is overloaded., Status: UNAVAILABLE, Details: []\ngoroutine 1";
        let quota = "Error 429, Message: Quota exhausted., Status: RESOURCE_EXHAUSTED, Details: [reason:QUOTA_EXHAUSTED]";
        let turn = |vars: Vec<(&'static str, String)>, resume: Option<String>| {
            let s = rig.settings(&vars);
            let (host, mut rx) = rig.host();
            let thread = match resume {
                Some(t) => {
                    host.resume_thread(&t, &s).unwrap();
                    t
                }
                None => host.start_thread(&s),
            };
            async move {
                host.start_turn(&thread, "hi", None).await.unwrap();
                let events = turn_events(&mut rx).await;
                host.stop().await;
                (thread, events)
            }
        };
        let end = |events: &[Value]| {
            events
                .iter()
                .find(|e| e["kind"] == "turn_failed" || e["kind"] == "turn_completed")
                .cloned()
                .unwrap()
        };
        // The model API failed: agy streamed that as its answer and ended the turn, its log's last step an error.
        let (thread, events) = turn(
            vec![("STEP", "17".into()), ("STEP_TEXT", error.into())],
            None,
        )
        .await;
        let failed = end(&events);
        assert_eq!(failed["kind"], "turn_failed", "{events:?}");
        assert_eq!(
            failed["error"],
            "Antigravity stopped this turn: HTTP 503 UNAVAILABLE: The model is overloaded."
        );
        assert!(events.iter().all(|e| e["kind"] != "message"), "{events:?}");
        // The next turn's own steps are read, never that error before it.
        let (thread, events) = turn(vec![("STEP", "15".into())], Some(thread)).await;
        assert_eq!(end(&events)["kind"], "turn_completed", "{events:?}");
        let (_, events) = turn(
            vec![("STEP", "17".into()), ("STEP_TEXT", quota.into())],
            Some(thread),
        )
        .await;
        let failed = end(&events);
        assert_eq!(
            (failed["error"].as_str(), failed["limit"].as_bool()),
            (Some(QUOTA), Some(true))
        );
        // A typed refusal is the quota, whatever agy printed.
        let refusal = json!({"jsonrpc": "2.0", "id": "@ID@", "result": {"stopReason": "refusal"}});
        let (host, _, events) = played(&rig, "typed-refusal", &[refusal], None).await;
        let failed = end(&events);
        assert_eq!(
            (failed["error"].as_str(), failed["limit"].as_bool()),
            (Some(QUOTA), Some(true))
        );
        host.stop().await;
    }

    #[tokio::test]
    async fn a_conversation_agy_did_not_resume_is_replaced_and_the_turn_runs_once() {
        // agy refuses to resume the saved conversation. With no fuller prompt to give, the turn runs in the one it
        // begins instead: kept after its work, the thread reads restored, and the record asks for the history on the
        // next turn.
        let rig = Rig::new();
        let s = rig.settings(&[("LOSE", "1".into()), ("NEW", OTHER.into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "short", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        let message = events.iter().find(|e| e["kind"] == "message").unwrap();
        assert_eq!(message["text"], "short");
        assert!(host.restored(&thread));
        host.stop().await;
        assert_eq!(rig.runs().len(), 1, "one process");
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(
            record.contains(OTHER) && record.contains("needs_history"),
            "{record}"
        );
    }

    #[tokio::test]
    async fn a_conversation_agy_did_not_resume_is_given_everything_when_there_is_more() {
        let rig = Rig::new();
        let s = rig.settings(&[("LOSE", "1".into()), ("NEW", OTHER.into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.full_prompt(&thread, Some("everything".into()));
        host.start_turn(&thread, "short", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        let message = events.iter().find(|e| e["kind"] == "message").unwrap();
        assert_eq!(message["text"], "everything");
        assert!(events.iter().any(|e| e["kind"] == "turn_completed"));
        assert!(host.restored(&thread));
        assert!(!host.needs_history(&thread), "it was told everything");
        host.stop().await;
        assert_eq!(
            rig.read(".prompts"),
            "everything\n",
            "only the whole prompt went"
        );
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(
            record.contains(OTHER) && !record.contains("needs_history"),
            "{record}"
        );
    }

    #[tokio::test]
    async fn a_replacement_refused_before_any_work_keeps_the_saved_record() {
        let rig = Rig::new();
        let s = rig.settings(&[
            ("LOSE", "1".into()),
            ("NEW", OTHER.into()),
            ("REFUSE_FRESH", "1".into()),
        ]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, true).unwrap();
        let before = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        host.full_prompt(&thread, Some("everything".into()));
        host.start_turn(&thread, "short", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        assert!(
            events.iter().any(|e| e["kind"] == "turn_failed"),
            "{events:?}"
        );
        assert!(!host.restored(&thread));
        assert!(host.needs_history(&thread));
        host.stop().await;
        let after = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert_eq!(after, before);
    }

    #[tokio::test]
    async fn a_refusal_before_any_work_keeps_the_saved_conversation() {
        let rig = Rig::new();
        let (host, thread, events) = played(
            &rig,
            "refusal",
            &[json!({"jsonrpc": "2.0", "id": "@ID@", "error": {"code": -32603, "message": "no"}})],
            Some(SAVED),
        )
        .await;
        assert!(events.iter().any(|e| e["kind"] == "turn_failed"));
        host.stop().await;
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(
            record.contains(SAVED) && !host.restored(&thread),
            "{record}"
        );
    }

    /// A turn whose prompt `agy` answers with `lines` (protocol lines, one per line), on a new thread of a new host, or
    /// on `saved`; its events.
    async fn played(
        rig: &Rig,
        name: &str,
        lines: &[Value],
        saved: Option<&str>,
    ) -> (AntigravityHost, String, Vec<Value>) {
        let dir = rig.dir.join(name);
        std::fs::create_dir_all(&dir).unwrap();
        let fixture = dir.join("turn.jsonl");
        let text: String = lines
            .iter()
            .map(|l| format!("{l}\n").replace(r#""@ID@""#, "@ID@"))
            .collect();
        std::fs::write(&fixture, text).unwrap();
        let s = rig.settings(&[
            ("FIXTURE", fixture.display().to_string()),
            ("NEW", OTHER.into()),
        ]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        if let Some(cid) = saved {
            host.save(&mut host.inner(), cid, false).unwrap();
        }
        host.start_turn(&thread, "hi", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        (host, thread, events)
    }

    #[tokio::test]
    async fn a_turn_cut_short_fails_and_a_tool_seen_only_in_an_update_still_ran() {
        let rig = Rig::new();
        let chunk = json!({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "@SID@",
                           "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial"}}}});
        let failed = |events: &[Value]| {
            events
                .iter()
                .find(|e| e["kind"] == "turn_failed")
                .cloned()
                .unwrap_or_else(|| panic!("{events:?}"))
        };
        // A truncated answer is not a completed turn, and an answer with no result is none.
        for (name, result) in [
            ("max-tokens", json!({"stopReason": "max_tokens"})),
            ("max-requests", json!({"stopReason": "max_turn_requests"})),
            ("no-result", Value::Null),
        ] {
            let answer = json!({"jsonrpc": "2.0", "id": "@ID@", "result": result});
            let (host, _, events) = played(&rig, name, &[chunk.clone(), answer], None).await;
            assert!(events.iter().all(|e| e["kind"] != "message"), "{events:?}");
            assert_eq!(failed(&events)["limit"], false, "{name}");
            host.stop().await;
        }
        // A completed tool agy reports only by its update ran, and is reported once; the quota after it stays a limit.
        let update = json!({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "@SID@",
                            "update": {"sessionUpdate": "tool_call_update", "toolCallId": "t9", "status": "completed"}}});
        let refusal = json!({"jsonrpc": "2.0", "id": "@ID@", "result": {"stopReason": "refusal"}});
        let (host, _, events) = played(
            &rig,
            "update-only",
            &[update.clone(), update, refusal],
            None,
        )
        .await;
        let tools: Vec<&Value> = events.iter().filter(|e| e["kind"] == "tool").collect();
        assert_eq!(
            tools,
            [&json!({"kind": "tool", "tool": "tool", "status": "started", "item_id": "t9"})]
        );
        assert_eq!(failed(&events)["limit"], true);
        host.stop().await;
        // A session id agy names in capitals is kept as it is, so its updates are the turn's.
        let upper = OTHER.to_uppercase();
        let dir = rig.dir.join("upper");
        std::fs::create_dir_all(&dir).unwrap();
        let fixture = dir.join("turn.jsonl");
        std::fs::write(
            &fixture,
            format!(
                "{chunk}\n{}\n",
                json!({"jsonrpc": "2.0", "id": "@ID@", "result": {"stopReason": "end_turn"}})
            )
            .replace(r#""@ID@""#, "@ID@"),
        )
        .unwrap();
        let s = rig.settings(&[
            ("FIXTURE", fixture.display().to_string()),
            ("NEW", upper.clone()),
        ]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).await.unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "partial", "final": true})));
        host.stop().await;
        assert!(std::fs::read_to_string(host.path(&thread).unwrap())
            .unwrap()
            .contains(&upper));
    }

    #[tokio::test]
    async fn a_new_threads_conversation_is_kept_only_once_the_model_worked_or_the_turn_succeeded() {
        let rig = Rig::new();
        let refusal =
            json!({"jsonrpc": "2.0", "id": "@ID@", "error": {"code": -32603, "message": "no"}});
        let (host, thread, events) = played(&rig, "new-refusal", &[refusal.clone()], None).await;
        assert!(events.iter().any(|e| e["kind"] == "turn_failed"));
        assert!(!host.path(&thread).unwrap().exists());
        host.stop().await;
        // Work before the refusal: the conversation holds it, so it is the thread's.
        let chunk = json!({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "@SID@",
                           "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "a"}}}});
        let (host, thread, _) = played(&rig, "new-work", &[chunk, refusal], None).await;
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(
            record.contains(OTHER) && !record.contains("needs_history"),
            "{record}"
        );
        assert!(!host.restored(&thread) && !host.needs_history(&thread));
        host.stop().await;
    }

    #[tokio::test]
    async fn a_tool_left_in_agys_group_goes_with_it_when_agy_exits_first() {
        let rig = Rig::new();
        let s = rig.settings(&[("ORPHAN", "1".into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).await.unwrap();
        assert!(turn_events(&mut rx)
            .await
            .iter()
            .any(|e| e["kind"] == "turn_completed"));
        let orphan: i32 = rig.read(".orphan").trim().parse().unwrap();
        host.stop().await;
        let gone = (0..50).any(|_| {
            std::thread::sleep(Duration::from_millis(20));
            (unsafe { libc::kill(orphan, 0) }) == -1
        });
        assert!(gone, "the tool agy left ignoring SIGTERM is gone");
    }

    #[tokio::test]
    async fn a_turn_that_must_continue_its_conversation_stops_rather_than_start_another() {
        let rig = Rig::new();
        for (flag, error) in [("LOSE", LOST), ("NOINIT", UNCONFIRMED)] {
            let mut s = rig.settings(&[(flag, "1".into()), ("NEW", OTHER.into())]);
            s.require_existing_session = true;
            let (host, mut rx) = rig.host();
            let thread = host.start_thread(&s);
            host.save(&mut host.inner(), SAVED, false).unwrap();
            host.start_turn(&thread, "go on", None).await.unwrap();
            let events = turn_events(&mut rx).await;
            let failed = events.iter().find(|e| e["kind"] == "turn_failed").unwrap();
            assert_eq!(failed["error"], error);
            assert!(crate::continuity::missing(error));
            host.stop().await;
            assert!(
                !rig.read(".calls").contains("session/new") && rig.read(".prompts").is_empty(),
                "no other conversation, and the prompt never went"
            );
            let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
            assert!(
                record.contains(SAVED) && !record.contains(OTHER),
                "{record}"
            );
        }
    }

    #[tokio::test]
    async fn any_other_resumed_turn_agy_does_not_confirm_begins_a_new_conversation_after_the_wait()
    {
        let rig = Rig::new();
        let s = rig.settings(&[("NOINIT", "1".into()), ("NEW", OTHER.into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "hello", None).await.unwrap();
        let events = turn_events(&mut rx).await;
        assert!(
            events.iter().any(|e| e["kind"] == "turn_completed"),
            "{events:?}"
        );
        assert!(host.restored(&thread));
        host.stop().await;
    }

    #[tokio::test]
    async fn stop_waits_for_a_process_that_ignores_sigterm_to_go() {
        let rig = Rig::new();
        let s = rig.settings(&[("STUCK", "1".into())]);
        let (host, _rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).await.unwrap();
        // Its SIGTERM is ignored once it says it is ready, so the stop must escalate.
        let ready = rig.dir.join("args.log.ready");
        let by = std::time::Instant::now() + Duration::from_secs(10);
        while !ready.exists() {
            assert!(std::time::Instant::now() < by, "the fake never got ready");
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let pid: i32 = rig.read(".pid").trim().parse().unwrap();
        host.stop().await;
        assert_eq!(
            unsafe { libc::kill(pid, 0) },
            -1,
            "agy is gone once stop returns"
        );
    }

    #[test]
    fn efforts_default_to_high_and_pick_the_sessions_model_and_thinking_level() {
        let s = Settings {
            effort: Some("max".into()),
            ..Default::default()
        };
        assert!(model_effort(&s, None).is_err());
        assert_eq!(
            model_effort(&s, Some("xhigh")).unwrap(),
            ("gemini-4-neon".to_string(), "xhigh".to_string())
        );
        let named = Settings {
            model: Some("gemini-4-hydrogen-low".into()),
            ..Default::default()
        };
        assert_eq!(
            model_effort(&named, None).unwrap(),
            ("gemini-4-hydrogen".to_string(), "high".to_string())
        );
        let options = json!([
            {"id": "model", "type": "select", "currentValue": "gemini-4-neon", "options": [
                {"group": "g", "name": "Gemini", "options": [{"value": "gemini-4-neon"}, {"value": "gemini-4-hydrogen"}]}]},
            {"id": "thinking_level", "category": "thought_level", "type": "select", "currentValue": "high",
             "options": [{"value": "low"}, {"value": "high"}]}]);
        assert_eq!(models(&json!({"configOptions": options})), 2);
        assert_eq!(models(&json!({"sessionId": "s"})), 0);
        assert_eq!(
            model_value(&options, "gemini-4-hydrogen", "low"),
            Ok(Some("gemini-4-hydrogen".into()))
        );
        assert_eq!(model_value(&options, "gemini-4-neon", "low"), Ok(None));
        assert!(model_value(&options, "gemini-9", "low")
            .unwrap_err()
            .contains("unavailable for this Google account"));
        // An older agy lists each model with its level in its id.
        let legacy = json!([{"id": "model", "type": "select", "currentValue": "x", "options": [{"value": "gemini-4-neon-low"}]}]);
        assert_eq!(
            model_value(&legacy, "gemini-4-neon", "low"),
            Ok(Some("gemini-4-neon-low".into()))
        );
        assert_eq!(model_value(&Value::Null, "gemini-4-neon", "low"), Ok(None));
        assert_eq!(
            effort_option(&options, "low"),
            Some("thinking_level".into())
        );
        assert_eq!(effort_option(&options, "high"), None);
        assert_eq!(effort_option(&options, "xhigh"), None);
    }

    #[test]
    fn mcp_servers_go_to_agy_only_in_a_transport_it_says_it_takes() {
        let settings = Settings {
            mcp_servers: vec![
                json!({"name": "docs", "transport": "http", "url": "https://docs.acme.example/mcp"}),
                json!({"name": "feed", "transport": "sse", "url": "https://feed.acme.example/sse"}),
            ],
            ..Default::default()
        };
        // agy 1.5.16 says none.
        assert_eq!(
            servers(&settings, &json!({"agentCapabilities": {}})),
            json!([])
        );
        let http = servers(
            &settings,
            &json!({"agentCapabilities": {"mcpCapabilities": {"http": true}}}),
        );
        assert_eq!(
            http,
            json!([{"type": "http", "name": "docs", "url": "https://docs.acme.example/mcp", "headers": []}])
        );
    }

    /// Whether process `pid` has gone (a stop reaps it at once; a kill is reaped by its owner soon after).
    async fn gone(pid: i32) -> bool {
        for _ in 0..100 {
            if unsafe { libc::kill(pid, 0) } == -1 {
                return true;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        false
    }

    fn methods(calls: &str) -> Vec<&str> {
        calls.lines().filter_map(|l| l.split(' ').next()).collect()
    }

    #[tokio::test]
    async fn a_threads_next_turn_goes_on_in_the_process_kept_for_it_holding_no_token_between() {
        let rig = Rig::new();
        // On a computer with turn limits each attempt names its own scope (execute.rs): not a start input.
        let scope = crate::turn_limits::SCOPE_ENV;
        let first = crate::turn_limits::scope_key("attempt-one");
        let mut s = rig.settings(&[("HUB_TOKEN", "attempt-one".into()), (scope, first.clone())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", Some("medium"))
            .await
            .unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "one", "final": true})));
        assert!(!host.warm());
        host.release(true, None).await;
        // Kept, its token file blank.
        let file = rig.read(".hub");
        let file = file.strip_prefix("tico-file:").unwrap();
        assert_eq!(std::fs::read_to_string(file).unwrap(), "");
        let pid = rig.pid();
        // The next attempt's host on the thread: the same process and conversation, with no handshake again; only
        // the new effort is set, in the live session.
        s.env.insert("HUB_TOKEN".into(), "attempt-two".into());
        s.env
            .insert(scope.into(), crate::turn_limits::scope_key("attempt-two"));
        let (next, mut rx) = rig.host();
        next.resume_thread(&thread, &s).unwrap();
        let before = methods(&rig.read(".calls")).len();
        next.start_turn(&thread, "two", Some("xhigh"))
            .await
            .unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "two", "final": true})));
        assert!(next.warm());
        assert_eq!(rig.runs().len(), 1, "one process");
        assert_eq!(
            methods(&rig.read(".calls"))[before..],
            ["session/set_config_option", "session/prompt"]
        );
        assert!(rig.read(".calls").contains(r#""value": "xhigh""#));
        assert_eq!(rig.read(".prompts"), "one\ntwo\n", "each prompt once");
        assert_eq!(rig.read(".tokens"), "attempt-one\nattempt-two\n");
        // It is still in the first attempt's scope: a memory kill now is this attempt's (turn_limits::oom_failure).
        assert_eq!(next.scopes(), vec![format!("{first}-{pid}.scope")]);
        next.release(true, None).await;
        rig.pool.prune(true).await;
        assert!(gone(pid).await, "the runner stopping stops it");
    }

    #[tokio::test]
    async fn a_kept_process_goes_when_idle_and_another_home_gets_its_own() {
        let rig = Rig::new();
        let s = rig.settings(&[]);
        // Unused for T3's two minutes: stopped (as of then: the keeper's clock is its caller's).
        let idle = Arc::new(Keeper::new(rig.dir.join("idle"), 4));
        let (host, mut rx) = rig.host_on(idle.clone());
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", None).await.unwrap();
        turn_events(&mut rx).await;
        host.release(true, None).await;
        let pid = rig.pid();
        let kept = std::time::Instant::now();
        idle.prune_at(kept + crate::keeper::IDLE - Duration::from_secs(5), false)
            .await;
        assert_eq!(
            unsafe { libc::kill(pid, 0) },
            0,
            "kept within the two minutes"
        );
        idle.prune_at(kept + crate::keeper::IDLE, false).await;
        assert!(gone(pid).await, "evicted once idle");
        // Another home: a process of its own, the kept one stopped.
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", None).await.unwrap();
        turn_events(&mut rx).await;
        host.release(true, None).await;
        let kept = rig.pid();
        let mut other = s.clone();
        other
            .env
            .insert("HOME".into(), rig.dir.join("other").display().to_string());
        let (next, mut rx) = rig.host();
        next.resume_thread(&thread, &other).unwrap();
        next.start_turn(&thread, "two", None).await.unwrap();
        turn_events(&mut rx).await;
        assert!(!next.warm() && gone(kept).await);
        assert!(rig.runs()[2].contains(&rig.dir.join("other/.gemini").display().to_string()));
        next.stop().await;
    }

    #[tokio::test]
    async fn a_kept_process_that_died_is_replaced_by_one_that_resumes_and_nothing_is_sent_twice() {
        let rig = Rig::new();
        let s = rig.settings(&[]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", None).await.unwrap();
        turn_events(&mut rx).await;
        host.release(true, None).await;
        let pid = rig.pid();
        unsafe { libc::kill(pid, libc::SIGKILL) };
        assert!(gone(pid).await);
        let (next, mut rx) = rig.host();
        next.resume_thread(&thread, &s).unwrap();
        next.start_turn(&thread, "two", None).await.unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "two", "final": true})));
        assert!(!next.warm());
        next.stop().await;
        assert_eq!(rig.runs().len(), 2);
        assert!(rig.read(".calls").contains(&format!(
            r#"session/resume {{"cwd": "{}", "mcpServers": [], "sessionId": "{SAVED}"}}"#,
            rig.dir.display()
        )));
        assert_eq!(rig.read(".prompts"), "one\ntwo\n");
    }

    #[tokio::test]
    async fn a_stopped_turn_is_cancelled_in_agy_and_its_process_kept() {
        let rig = Rig::new();
        let s = rig.settings(&[]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hang", None).await.unwrap();
        let by = std::time::Instant::now() + Duration::from_secs(10);
        while !rig.read(".prompts").contains("hang") {
            assert!(std::time::Instant::now() < by, "the prompt never went");
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        host.interrupt().await;
        let events = turn_events(&mut rx).await;
        assert!(
            events.contains(&json!({"kind": "turn_completed", "status": "interrupted"})),
            "{events:?}"
        );
        assert!(rig.read(".calls").contains("session/cancel"));
        // Nothing runs now, so a second stop leaves it.
        host.interrupt().await;
        assert!(host.alive());
        host.release(true, None).await;
        let (next, mut rx) = rig.host();
        next.resume_thread(&thread, &s).unwrap();
        next.start_turn(&thread, "two", None).await.unwrap();
        assert!(turn_events(&mut rx)
            .await
            .iter()
            .any(|e| e["kind"] == "turn_completed"));
        assert!(next.warm() && rig.runs().len() == 1);
        next.stop().await;
    }

    #[tokio::test]
    async fn the_reply_streams_as_agy_sends_it() {
        let rig = Rig::new();
        let s = rig.settings(&[]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "wait", None).await.unwrap();
        // agy holds the rest of its answer back until told: its first words are passed on before it.
        let first = loop {
            let ev = tokio::time::timeout(Duration::from_secs(10), rx.recv())
                .await
                .unwrap()
                .unwrap();
            assert!(
                ev["kind"] != "message" && ev["kind"] != "turn_completed",
                "{ev}"
            );
            if ev["kind"] == "delta" {
                break ev;
            }
        };
        assert_eq!(
            (&first["text"], &first["delta_kind"]),
            (&json!("early"), &json!("text"))
        );
        std::fs::write(rig.dir.join("args.log.go"), "").unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "early late", "final": true})));
        host.stop().await;
    }

    /// A turn on the plan `items` hand: its home made and held as a turn's, the settings that run on it, and the home.
    fn plan_turn(
        rig: &Rig,
        items: &Value,
    ) -> (
        Settings,
        Vec<crate::model_subscriptions::Delivered>,
        PathBuf,
    ) {
        use crate::model_subscriptions as subs;
        let mut env = rig.settings(&[]).env;
        let mut got = subs::apply(
            &rig.dir,
            items,
            &mut env,
            &mut vec![],
            &mut Default::default(),
        );
        subs::start(&mut got, "antigravity", &mut env).unwrap();
        let home = subs::plan_home(&got, "antigravity").unwrap();
        let settings = Settings {
            env,
            plan_home: Some(home.clone()),
            ..rig.settings(&[])
        };
        (settings, got, PathBuf::from(home))
    }

    #[tokio::test]
    async fn a_plan_turns_agy_goes_on_for_the_same_plan_and_its_home_goes_with_it() {
        use crate::model_subscriptions as subs;
        let rig = Rig::new();
        let items = json!([{"id": "agyplan", "runtime": "antigravity", "kind": "antigravity_login",
                            "file": subs::AGY_TOKEN, "revision": 1, "billing": "subscription",
                            "value": r#"{"token":{"access_token":"ya29.synthetic-plan-token-0123456789"}}"#}]);
        let (s, mut got, first) = plan_turn(&rig, &items);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", None).await.unwrap();
        turn_events(&mut rx).await;
        host.release(true, None).await;
        subs::finish(&mut got);
        // The turn is over, its process kept: the home and its sign-in stay while it runs.
        assert!(first.join(subs::AGY_TOKEN).exists());
        let pid = rig.pid();
        // The next turn on the same plan, handed a home of its own, goes on in that process.
        let (s, mut got, second) = plan_turn(&rig, &items);
        assert_ne!(first, second);
        let (next, mut rx) = rig.host();
        next.resume_thread(&thread, &s).unwrap();
        next.start_turn(&thread, "two", None).await.unwrap();
        assert!(turn_events(&mut rx)
            .await
            .contains(&json!({"kind": "message", "text": "two", "final": true})));
        assert!(next.warm());
        assert_eq!(rig.runs().len(), 1, "one process");
        next.release(true, None).await;
        subs::finish(&mut got);
        assert!(!second.exists() && first.join(subs::AGY_TOKEN).exists());
        // Released after two minutes unused: its home and sign-in go with it.
        rig.pool
            .prune_at(std::time::Instant::now() + crate::keeper::IDLE, false)
            .await;
        assert!(gone(pid).await);
        assert!(!first.exists(), "no copy left behind");
        // A turn no longer handed the plan gets a process of its own.
        let own = rig.settings(&[]);
        let (last, mut rx) = rig.host();
        last.resume_thread(&thread, &own).unwrap();
        last.start_turn(&thread, "three", None).await.unwrap();
        turn_events(&mut rx).await;
        assert!(!last.warm());
        assert_eq!(rig.runs().len(), 2);
        last.stop().await;
    }
}
