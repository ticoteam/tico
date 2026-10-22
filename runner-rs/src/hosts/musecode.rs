//! The Muse Code host: one `muse exec --json` process per turn (Meta's coding CLI).
//!
//! A thread is a Muse session whose id Tico makes (a UUID): every turn runs with `--session-id <id>`, and an id
//! the CLI already has continues that session, so a restart resumes it. The prompt goes in a file the turn can
//! read (the CLI takes no prompt on stdin). Events are one JSON object per line: `run.output.delta` streams text,
//! `run.terminal.completed|failed|cancelled` ends the run with the final `text` or a `reason`. Token counts are not
//! in that stream (Muse Code 1.4.8): they are `model_completed` records in the session's durable log, read after
//! each turn (`session_usage`), with the model `run.model.configured` named. The hub and the bot's remote MCP servers are per-run
//! `-c mcp_servers.<name>=...` settings whose secrets stay `${VAR}` in the environment. MUSE_NO_AUTO_UPDATE=1
//! stops the launcher updating itself; the runner updates harnesses.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{ChildStdout, Command};

use super::{event, failed, hub_mcp_server, is_limit, Events, HostError, Settings};
use crate::env::Env;
use crate::util::{log, text_of, truthy};

pub const MODELS: &[&str] = &["muse-spark-1.3", "muse-wonder-1.0", "muse-wonder-full-1.0"];
pub const DEFAULT_MODEL: &str = "muse-spark-1.3";
const EFFORTS: &[&str] = &["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"];
const DEFAULT_EFFORT: &str = "medium";
const STOP_TIMEOUT: Duration = Duration::from_secs(10);
const STDERR_TAIL: usize = 2000;
/// Set on every Muse process, the sign-in check's and `--version`'s too, so the launcher never updates itself.
pub const QUIET: &[(&str, &str)] = &[("MUSE_NO_AUTO_UPDATE", "1"), ("NO_COLOR", "1")];

/// The catalog id as the CLI's model id; anything else passes through, so a model Meta adds works unchanged.
pub fn model_for(model: Option<&str>) -> String {
    let m = model.unwrap_or_default().trim();
    if m.is_empty() || m == "default" {
        DEFAULT_MODEL.into()
    } else {
        m.into()
    }
}

pub fn effort_for(effort: Option<&str>) -> String {
    let e = effort.unwrap_or_default().trim().to_lowercase();
    if EFFORTS.contains(&e.as_str()) {
        e
    } else {
        DEFAULT_EFFORT.into()
    }
}

/// Python's `int(row[key])` for the first of `keys` that is set and whole, else 0.
fn count(row: &Map<String, Value>, keys: &[&str]) -> i64 {
    for key in keys {
        let n = match row.get(*key) {
            None | Some(Value::Null) => continue,
            Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
            Some(Value::String(s)) => s.trim().parse::<i64>().ok(),
            Some(Value::Bool(b)) => Some(*b as i64),
            _ => None,
        };
        if let Some(n) = n {
            return n;
        }
    }
    0
}

/// The tool a JSONL event starts, if any. Real Muse 1.4.7 runs announce a tool call as
/// `task.lifecycle.proposed` with `event.task_kind` "tool.<name>"; `tool.*.started` is the older shape.
fn tool_started(kind: &str, payload: &Value) -> Option<String> {
    if kind == "task.lifecycle.proposed" {
        let task = payload["event"]["task_kind"].as_str()?;
        return task.strip_prefix("tool.").filter(|n| !n.is_empty()).map(str::to_string);
    }
    if kind.starts_with("tool.") && kind.ends_with(".started") {
        let name = ["tool_name", "name", "tool"].iter().find(|k| truthy(payload.get(**k))).map(|k| text_of(&payload[*k]));
        return Some(name.unwrap_or_else(|| "tool".into()));
    }
    None
}

/// (input, cached, output) from one event's own token counters, or None. A session's `cumulative` running totals
/// are skipped: a resumed session's totals include earlier turns.
pub fn usage_of(payload: &Value) -> Option<(i64, i64, i64)> {
    let mut found: Option<(i64, i64, i64)> = None;
    let mut stack = vec![payload];
    while let Some(row) = stack.pop() {
        let Some(row) = row.as_object() else { continue };
        let has = |a: &str, b: &str| row.contains_key(a) || row.contains_key(b);
        if has("inputTokens", "input_tokens") && has("outputTokens", "output_tokens") {
            let inp = count(row, &["inputTokens", "input_tokens"]);
            let cached = count(row, &["cacheReadTokens", "cache_read_tokens", "cachedTokens", "cached_tokens"]);
            let out = count(row, &["outputTokens", "output_tokens"]);
            let (a, b, c) = found.unwrap_or_default();
            found = Some((a + inp, b + cached, c + out));
            continue;
        }
        stack.extend(row.iter().filter(|(k, v)| *k != "cumulative" && v.is_object()).map(|(_, v)| v));
    }
    found
}

/// The session's durable log under Muse's data folder (`$XDG_DATA_HOME/muse/sessions/<yyyy>/<mm>/<dd>/<id>/`,
/// `~/.local/share` without XDG_DATA_HOME), or None before its first turn wrote one.
pub fn session_log(env: &Env, session_id: &str) -> Option<PathBuf> {
    let data = match env.get("XDG_DATA_HOME").filter(|d| !d.is_empty()) {
        Some(d) => PathBuf::from(d),
        None => PathBuf::from(env.get("HOME").filter(|h| !h.is_empty())?).join(".local").join("share"),
    };
    if session_id.is_empty() || session_id.contains('/') {
        return None;
    }
    let dirs = |p: &Path| -> Vec<PathBuf> {
        let mut v: Vec<PathBuf> = std::fs::read_dir(p).into_iter().flatten().flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
        v.sort();
        v
    };
    let mut found = None;
    for y in dirs(&data.join("muse").join("sessions")) {
        for m in dirs(&y) {
            for d in dirs(&m) {
                let log = d.join(session_id).join("session.jsonl");
                if log.is_file() {
                    found = Some(log);
                }
            }
        }
    }
    found
}

/// (input, cached, output, model) summed over the `model_completed` records of `run_id` (any run when None) from
/// byte `offset` on, or None when there are none. Records that repeat the same counts (`goal_usage_attribution`)
/// are not counted.
pub fn session_usage(path: &Path, offset: u64, run_id: Option<&str>) -> Option<(i64, i64, i64, String)> {
    let mut file = std::fs::File::open(path).ok()?;
    let size = file.metadata().ok()?.len();
    if offset > 0 && offset <= size {
        file.seek(SeekFrom::Start(offset)).ok()?; // a log that shrank (rewritten) is read whole, by run id
    }
    let mut bytes = vec![];
    file.read_to_end(&mut bytes).ok()?;
    let (mut found, mut model): (Option<(i64, i64, i64)>, String) = (None, String::new());
    for line in String::from_utf8_lossy(&bytes).lines().filter(|l| l.contains("model_completed")) {
        let Ok(rec) = serde_json::from_str::<Value>(line) else { continue };
        let (payload, event) = (&rec["payload"], &rec["payload"]["event"]);
        if event["kind"] != "model_completed" || run_id.is_some_and(|r| payload["run_id"].as_str() != Some(r)) {
            continue;
        }
        let empty = Map::new();
        let u = event["usage"].as_object().unwrap_or(&empty);
        let row = (
            count(u, &["input_tokens", "inputTokens"]),
            count(u, &["cache_read_tokens", "cacheReadTokens", "cached_tokens", "cachedTokens"]),
            count(u, &["output_tokens", "outputTokens"]),
        );
        let (a, b, c) = found.unwrap_or_default();
        found = Some((a + row.0, b + row.1, c + row.2));
        if let Some(m) = event["model"].as_str().filter(|m| !m.is_empty()) {
            model = m.into();
        }
    }
    found.map(|(a, b, c)| (a, b, c, model))
}

fn placeholder_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\$\{([A-Z_][A-Z0-9_]*)\}").unwrap())
}

/// `clients/mcp_servers.muse_config`: the `mcp_servers.<name>` values for the bot's remote servers ({name, url,
/// headers}), and the extra environment they need. Muse takes a secret only as a header that is exactly one
/// `${VAR}`, so any other header (`Bearer ${VAR}`) is resolved into a variable of its own, `TICO_MCP_<n>`, that
/// only this run's environment carries.
pub fn muse_config(servers: &[Value], env: &Env) -> (Map<String, Value>, Env) {
    let (mut out, mut extra) = (Map::new(), Env::new());
    for s in servers {
        let mut headers = Map::new();
        for (name, value) in s["headers"].as_object().into_iter().flatten() {
            let value = text_of(value);
            let found: Vec<&str> = placeholder_re().captures_iter(&value).map(|c| c.get(1).unwrap().as_str()).collect();
            if found.len() == 1 && value.trim() == format!("${{{}}}", found[0]) {
                headers.insert(name.clone(), json!(value.trim()));
                continue;
            }
            let var = format!("TICO_MCP_{}", extra.len() + 1);
            let expanded = placeholder_re().replace_all(&value, |c: &regex::Captures| env.get(&c[1]).cloned().unwrap_or_default());
            extra.insert(var.clone(), expanded.into_owned());
            headers.insert(name.clone(), json!(format!("${{{var}}}")));
        }
        let mut server = Map::new();
        server.insert("transport".into(), json!("streamable_http"));
        server.insert("url".into(), s["url"].clone());
        if !headers.is_empty() {
            server.insert("headers".into(), Value::Object(headers));
        }
        out.insert(text_of(&s["name"]), Value::Object(server));
    }
    (out, extra)
}

/// (["-c", "mcp_servers.<name>=<json>", ...], extra environment) for one run: the hub as a stdio server, then
/// the bot's remote servers. Secrets stay in the environment as `${VAR}`.
pub fn mcp_overrides(settings: &Settings) -> (Vec<String>, Env) {
    let mut args = vec![];
    if let Some(hub) = hub_mcp_server(&settings.env) {
        let vars: Map<String, Value> =
            hub["env"].as_object().into_iter().flatten().map(|(k, _)| (k.clone(), json!(format!("${{{k}}}")))).collect();
        let server = json!({"transport": "stdio", "command": hub["command"], "args": hub["args"], "env": vars});
        args.extend(["-c".into(), format!("mcp_servers.hub={server}")]);
    }
    let (config, extra) = muse_config(&settings.mcp_servers, &settings.env);
    for (name, server) in config {
        args.extend(["-c".into(), format!("mcp_servers.{name}={server}")]);
    }
    (args, extra)
}

/// Whether a Meta-account login file exists: Some(false) when it does not, None when its folder cannot be read
/// (a bot user's 0700 config folder), which is not "signed out".
pub fn signed_in(path: &Path) -> Option<bool> {
    match std::fs::metadata(path) {
        Ok(m) => Some(m.is_file()),
        Err(e) if matches!(e.kind(), std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory) => Some(false),
        Err(_) => None,
    }
}

static STATE_DIR: OnceLock<PathBuf> = OnceLock::new();

/// The runner's state directory, set once at start, so readiness and profiles find Tico's own Muse folder.
pub fn set_state_dir(dir: &Path) {
    let _ = STATE_DIR.set(dir.to_path_buf());
}

/// Muse Code's XDG_CONFIG_HOME for bot turns, sign-in and readiness (service.muse_config_home): a subscription
/// profile's own folder, else the runner's. Never the operator's ~/.config, whose Muse MCP servers would load
/// in every bot turn.
pub fn config_home(profile: Option<&crate::profiles::Profile>) -> PathBuf {
    if let Some(home) = profile.and_then(|p| p.home("musecode")) {
        return home;
    }
    own_folder("config")
}

/// `musecode/<name>` in the runner's bot-owned host state.
fn own_folder(name: &str) -> PathBuf {
    let state = STATE_DIR.get().cloned().unwrap_or_else(|| std::env::temp_dir().join("tico-runner-state"));
    crate::isolation::bot_state(&state).join("musecode").join(name)
}

/// Muse Code's XDG_DATA_HOME (sessions, their locks and logs): the runner's own `musecode/data`, or
/// `musecode/profiles/<name>/data` for a subscription profile with a home of its own, so two logins never share
/// sessions. Never `~/.local/share`, which turns made as another user before isolation may still own, and never
/// inside a config home: those are handed to the bot user whole (`prepare_config`, `profiles::hand_to_bot`), which
/// would take over a folder this check must refuse.
pub fn data_home(profile: Option<&crate::profiles::Profile>) -> PathBuf {
    match profile.filter(|p| p.home("musecode").is_some()) {
        Some(p) if crate::profiles::valid_name(&p.name) => own_folder("profiles").join(&p.name).join("data"),
        Some(p) => own_folder("profiles").join(p.name.bytes().map(|b| format!("{b:02x}")).collect::<String>()).join("data"),
        None => own_folder("data"),
    }
}

/// Whether the user a turn runs as may create entries in directory `path`: judged from its owner and mode under
/// isolation (the supervisor's own access check would pass on DAC_OVERRIDE), else by this process's access.
fn turn_can_write(path: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    let Some((uid, gid)) = crate::isolation::identity() else {
        let Ok(c) = std::ffi::CString::new(path.as_os_str().as_encoded_bytes()) else { return false };
        // SAFETY: access on a NUL-terminated path.
        return unsafe { libc::access(c.as_ptr(), libc::W_OK | libc::X_OK) } == 0;
    };
    let Ok(m) = std::fs::metadata(path) else { return false };
    can_write(m.mode(), (m.uid(), m.gid()), (uid, gid))
}

/// The kernel's rule for a directory `mode` owned by `owner`, for a user with no supplementary groups (turns run
/// with `--clear-groups`): the owner's bits alone when it owns it, else the group's when it is in that group, else
/// the others'. Creating an entry needs write and search.
fn can_write(mode: u32, owner: (u32, u32), user: (u32, u32)) -> bool {
    let bits = if owner.0 == user.0 { 0o300 } else if owner.1 == user.1 { 0o030 } else { 0o003 };
    mode & bits == bits
}

/// The first folder of Muse's data home that a turn's user cannot write (the home, `muse` and `muse/sessions`,
/// where Muse makes each session's lock), as a problem naming it; None when every one there is writable. A folder
/// not made yet is no problem: the runner makes it for the bot user.
pub fn data_problem(home: &Path) -> Option<String> {
    let muse = home.join("muse");
    for dir in [home.to_path_buf(), muse.clone(), muse.join("sessions")] {
        match std::fs::symlink_metadata(&dir) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return None,
            Ok(m) if m.is_dir() && turn_can_write(&dir) => continue,
            Ok(m) if !m.is_dir() => return Some(format!("Muse Code data folder {} is not a plain folder (symbolic links are refused)", dir.display())),
            _ => {}
        }
        return Some(format!("Muse Code can't save sessions: the bot user can't write {} (another user owns it or it is read-only)", dir.display()));
    }
    None
}

/// Make `home` (XDG_DATA_HOME) ready for a bot turn: a 0700 folder the bot user owns, or the reason a turn could
/// not write it. Under isolation the bot user makes it (`mkdir` as the bot), so a link it planted in its own folders
/// never leads the supervisor's rights anywhere; folders already there are never handed over.
pub fn prepare_data(home: &Path) -> Result<(), HostError> {
    if let Some(problem) = data_problem(home) {
        return Err(failed(problem));
    }
    let made = if crate::isolation::enabled() {
        crate::isolation::std_command("mkdir").args(["-p", "-m", "700", "--"]).arg(home).stdin(Stdio::null()).output().and_then(|out| {
            if out.status.success() {
                Ok(())
            } else {
                Err(std::io::Error::other(String::from_utf8_lossy(&out.stderr).trim().to_string()))
            }
        })
    } else {
        use std::os::unix::fs::DirBuilderExt;
        std::fs::DirBuilder::new().recursive(true).mode(0o700).create(home)
    };
    made.map_err(|e| failed(format!("Muse Code data folder {}: {e}", home.display())))?;
    data_problem(home).map_or(Ok(()), |problem| Err(failed(problem)))
}

/// Make `home` (XDG_CONFIG_HOME) ready for a bot turn: `muse/` exists and its settings name no MCP servers, so
/// none but this run's own load. The folder is Tico's own, never a person's.
pub fn prepare_config(home: &Path) -> Result<PathBuf, HostError> {
    for path in [home.to_path_buf(), home.join("muse")] {
        match std::fs::symlink_metadata(path) {
            Ok(m) if !m.is_dir() => return Err(failed("Muse config directories must not be symbolic links")),
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => return Err(failed(e.to_string())),
            _ => {}
        }
    }
    let directory = crate::isolation::mkdir(&home.join("muse"), 0o700).map_err(|e| failed(format!("Muse settings folder: {e}")))?;
    // Only this login's Muse home, not the profile directory or the runner's private state.
    crate::isolation::adopt(&[home.to_path_buf()]);
    let path = directory.join("settings.json");
    let stored = match std::fs::read_to_string(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(directory),
        Err(e) => return Err(failed(format!("Muse settings are not readable: {e}"))),
        Ok(text) => serde_json::from_str::<Value>(&text).ok(),
    };
    let cleaned = match stored {
        Some(Value::Object(m)) if !m.contains_key("mcpServers") && !m.contains_key("mcp_servers") => return Ok(directory),
        Some(Value::Object(mut m)) => {
            m.remove("mcpServers");
            m.remove("mcp_servers");
            Value::Object(m)
        }
        _ => json!({"schema_version": 1}), // Muse refuses a broken file anyway; this one is ours to rewrite
    };
    let temporary = directory.join(format!(".settings-{}.json", uuid::Uuid::new_v4().simple()));
    let written = std::fs::write(&temporary, serde_json::to_string_pretty(&cleaned).unwrap_or_default()).and_then(|_| {
        crate::isolation::chown(&temporary);
        std::fs::rename(&temporary, &path)
    });
    let _ = std::fs::remove_file(&temporary);
    written.map_err(|e| failed(format!("Muse settings could not be written: {e}")))?;
    Ok(directory)
}

/// Where Muse keeps its Meta-account login for this environment: MUSE_AUTH_PATH, else the XDG config dir.
pub fn auth_file(env: &Env) -> PathBuf {
    if let Some(p) = env.get("MUSE_AUTH_PATH").filter(|p| !p.is_empty()) {
        return PathBuf::from(p);
    }
    let config = env.get("XDG_CONFIG_HOME").filter(|p| !p.is_empty()).map(PathBuf::from).unwrap_or_else(|| {
        let home = env.get("HOME").filter(|h| !h.is_empty()).cloned().unwrap_or_default();
        PathBuf::from(home).join(".config")
    });
    config.join("muse").join("auth.json")
}

#[derive(Default)]
struct Turn {
    reply: String,
    /// Text streamed since the last tool call: the answer when the terminal carries no text of its own.
    streamed: String,
    terminal: String,
    reason: String,
    usage: Option<(i64, i64, i64)>,
    /// This turn's run id (`run_stream.id`), so only its records in the session log count.
    run: String,
    /// The model the run used (`run.model.configured`, else the session log's).
    model: String,
}

#[derive(Default)]
struct Inner {
    up: bool,
    threads: HashMap<String, Settings>,
    turn: Option<(String, String)>,
    stopper: Option<super::Stopper>,
    interrupted: HashSet<String>,
    done_turns: HashSet<String>,
    reader: Option<tokio::task::JoinHandle<()>>,
}

struct Shared {
    bot: String,
    cmd: Vec<String>,
    /// `echo` is Muse's offline provider (tests and smoke runs); it takes no model or effort.
    provider: Option<String>,
    state_dir: PathBuf,
    /// Muse's XDG_CONFIG_HOME for every turn (see `config_home`): never the operator's.
    config_home: PathBuf,
    /// Muse's XDG_DATA_HOME for every turn (see `data_home`), so sessions stay apart per login.
    data_home: PathBuf,
    tx: Events,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct MuseCodeHost(Arc<Shared>);

impl MuseCodeHost {
    #[cfg(test)]
    pub fn new(bot: &str, cmd: Vec<String>, provider: Option<String>, state_dir: PathBuf, tx: Events) -> MuseCodeHost {
        let (config_home, data_home) = (state_dir.join("config"), state_dir.join("data"));
        Self::on_config(bot, cmd, provider, state_dir, (config_home, data_home), tx)
    }

    /// `homes`: Muse's (XDG_CONFIG_HOME, XDG_DATA_HOME) for every turn.
    pub fn on_config(bot: &str, cmd: Vec<String>, provider: Option<String>, state_dir: PathBuf, homes: (PathBuf, PathBuf), tx: Events) -> MuseCodeHost {
        let (config_home, data_home) = homes;
        MuseCodeHost(Arc::new(Shared { bot: bot.into(), cmd, provider, state_dir, config_home, data_home, tx, inner: Mutex::new(Inner::default()) }))
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn emit(&self, ev: Value) {
        let _ = self.0.tx.send(ev);
    }

    pub fn start(&self) {
        self.inner().up = true;
    }

    pub async fn stop(&self) {
        let reader = {
            let mut inner = self.inner();
            inner.up = false;
            if let Some((_, turn)) = inner.turn.clone() {
                inner.interrupted.insert(turn);
                if let Some(s) = &inner.stopper {
                    s.terminate(STOP_TIMEOUT);
                }
            }
            inner.reader.take()
        };
        if let Some(r) = reader {
            let _ = tokio::time::timeout(STOP_TIMEOUT + Duration::from_secs(1), r).await;
        }
    }

    pub fn alive(&self) -> bool {
        self.inner().up
    }

    /// The session is created by its first turn; the id is ours so a restart can name it.
    pub fn start_thread(&self, settings: &Settings) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        self.inner().threads.insert(id.clone(), settings.clone());
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> String {
        self.inner().threads.insert(thread_id.into(), settings.clone());
        thread_id.into()
    }

    pub fn argv(&self, thread_id: &str, settings: &Settings, prompt_file: &Path, effort: Option<&str>) -> Vec<String> {
        let mut argv = self.0.cmd.clone();
        argv.extend(
            ["exec", "--json", "--yolo", "--approval-mode", "never", "--approval-judge", "off", "--user-input-auto-resolve", "--workspace"]
                .map(String::from),
        );
        argv.extend([settings.cwd.clone(), "--session-id".into(), thread_id.into()]);
        if let Some(p) = &self.0.provider {
            argv.extend(["--provider".into(), p.clone()]);
        }
        if self.0.provider.as_deref() != Some("echo") {
            argv.extend(["--model".into(), model_for(settings.model.as_deref())]);
            argv.extend(["--reasoning-effort".into(), effort_for(effort.or(settings.effort.as_deref()))]);
        }
        argv.extend(mcp_overrides(settings).0);
        argv.extend(["--prompt-file".into(), prompt_file.display().to_string()]);
        argv
    }

    /// The turn's environment (this process's when the settings carry none), HOME kept, Tico's own Muse folders,
    /// QUIET and the MCP variables.
    pub fn env(&self, settings: &Settings) -> Env {
        let mut env = if settings.env.is_empty() { std::env::vars().collect() } else { settings.env.clone() };
        if env.get("HOME").is_none_or(|h| h.is_empty()) {
            if let Ok(home) = std::env::var("HOME") {
                env.insert("HOME".into(), home);
            }
        }
        env.insert("XDG_CONFIG_HOME".into(), self.0.config_home.display().to_string());
        env.insert("XDG_DATA_HOME".into(), self.0.data_home.display().to_string());
        env.extend(QUIET.iter().map(|(k, v)| (k.to_string(), v.to_string())));
        env.extend(mcp_overrides(settings).1);
        env
    }

    fn prompt_file(&self, text: &str) -> std::io::Result<PathBuf> {
        use std::os::unix::fs::OpenOptionsExt;
        crate::isolation::mkdir(&self.0.state_dir, 0o700)?;
        crate::isolation::chown(&self.0.state_dir);
        let path = self.0.state_dir.join(format!("prompt-{}.md", uuid::Uuid::new_v4().simple()));
        let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path)?;
        if let Err(e) = f.write_all(text.as_bytes()).and_then(|_| crate::isolation::chown_file(&f)) {
            let _ = std::fs::remove_file(&path);
            return Err(e);
        }
        Ok(path)
    }

    fn command(&self, thread_id: &str, settings: &Settings, prompt: &Path, effort: Option<&str>) -> Command {
        let argv = self.argv(thread_id, settings, prompt, effort);
        let mut command = crate::isolation::command(&argv[0]);
        command.args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::piped())
            .current_dir(&settings.cwd).env_clear().envs(self.env(settings));
        command
    }

    pub fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let (stdout, exited, stderr, prompt, pid, env, offset) = {
            let mut inner = self.inner();
            if !inner.up {
                return Err(failed("muse host is not running"));
            }
            if inner.turn.is_some() {
                return Err(failed("muse host already has a turn running"));
            }
            let settings = inner.threads.get(thread_id).cloned().ok_or_else(|| failed(format!("unknown muse thread {thread_id}")))?;
            // The data folder first: nothing is handed to the bot user before a folder it must not have is refused.
            prepare_data(&self.0.data_home)?;
            prepare_config(&self.0.config_home)?;
            let env = self.env(&settings);
            let offset = session_log(&env, thread_id).and_then(|p| std::fs::metadata(p).ok()).map_or(0, |m| m.len());
            let prompt = self.prompt_file(text).map_err(|e| failed(format!("could not start muse: {e}")))?;
            let spawned = (|| {
                // stderr goes to an unlinked file: read back for the error text, never a pipe that could fill.
                let path = std::env::temp_dir().join(format!("tico-muse-stderr-{}", uuid::Uuid::new_v4().simple()));
                let stderr = std::fs::OpenOptions::new().read(true).write(true).create_new(true).open(&path)?;
                let _ = std::fs::remove_file(&path);
                let child = self.command(thread_id, &settings, &prompt, effort)
                    .stderr(Stdio::from(stderr.try_clone()?))
                    .spawn()?;
                Ok::<_, std::io::Error>((child, stderr))
            })();
            let (mut child, stderr) = match spawned {
                Ok(c) => c,
                Err(e) => {
                    let _ = std::fs::remove_file(&prompt);
                    return Err(failed(format!("could not start muse: {e}")));
                }
            };
            let pid = child.id();
            let stdout = child.stdout.take();
            let (stopper, exited) = super::own(child);
            inner.stopper = Some(stopper);
            inner.turn = Some((thread_id.into(), turn.clone()));
            (stdout, exited, stderr, prompt, pid, env, offset)
        };
        log(&format!("muse[{}]: turn started pid {}", self.0.bot, pid.map_or("?".into(), |p| p.to_string())));
        self.emit(event("status", Some(thread_id), None, json!({"state": "active"})));
        let host = self.clone();
        let (tid, t2) = (thread_id.to_string(), turn.clone());
        let reader = tokio::spawn(async move { host.read_loop(stdout, exited, stderr, (prompt, env, offset), tid, t2).await });
        self.inner().reader = Some(reader);
        Ok(turn)
    }

    /// `files`: the prompt file to remove, and the turn's environment and session-log size before it ran.
    async fn read_loop(
        &self,
        stdout: Option<ChildStdout>,
        exited: tokio::sync::oneshot::Receiver<std::io::Result<std::process::ExitStatus>>,
        mut stderr: std::fs::File,
        files: (PathBuf, Env, u64),
        tid: String,
        turn: String,
    ) {
        let (prompt, env, offset) = files;
        let mut state = Turn::default();
        if let Some(stdout) = stdout {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if let Ok(ev) = serde_json::from_str::<Value>(line.trim()) {
                    self.on_event(&ev, &tid, &turn, &mut state);
                }
            }
        }
        let rc = match exited.await {
            Ok(status) => super::return_code(&status),
            Err(_) => -1,
        };
        let _ = std::fs::remove_file(&prompt);
        if state.usage.is_none() {
            let run = Some(state.run.as_str()).filter(|r| !r.is_empty());
            if let Some((a, b, c, model)) = session_log(&env, &tid).and_then(|p| session_usage(&p, offset, run)) {
                state.usage = Some((a, b, c));
                if !model.is_empty() {
                    state.model = model;
                }
            }
        }
        self.finish(&tid, &turn, rc, &stderr_tail(&mut stderr), state);
    }

    /// One JSONL event. Unknown payload types are skipped: the CLI adds them freely.
    fn on_event(&self, ev: &Value, tid: &str, turn: &str, state: &mut Turn) {
        if !ev.is_object() {
            return;
        }
        let kind = text_of(&ev["payload_type"]);
        let empty = json!({});
        let payload = if ev["payload"].is_object() { &ev["payload"] } else { &empty };
        if let Some((a, b, c)) = usage_of(payload) {
            let (x, y, z) = state.usage.unwrap_or_default();
            state.usage = Some((x + a, y + b, z + c));
        }
        if state.run.is_empty() {
            if let Some(id) = payload["run_stream"]["id"].as_str() {
                state.run = id.into();
            }
        }
        if kind == "run.model.configured" {
            if let Some(m) = payload["model_id"].as_str().filter(|m| !m.is_empty()) {
                state.model = m.into();
            }
        }
        if kind == "run.output.delta" {
            if let Some(text) = payload["text"].as_str().filter(|t| !t.is_empty()) {
                state.streamed.push_str(text);
                self.emit(event("delta", Some(tid), Some(turn), json!({"text": text, "delta_kind": "text"})));
            }
        } else if let Some(name) = tool_started(&kind, payload) {
            state.streamed.clear(); // text before a tool call is progress, not the answer
            self.emit(event("tool", Some(tid), Some(turn), json!({"name": name, "tool": name, "phase": "tool_execution_start"})));
        } else if kind.starts_with("run.terminal.") {
            state.terminal = if truthy(payload.get("terminal")) {
                text_of(&payload["terminal"])
            } else {
                kind.rsplit('.').next().unwrap_or_default().to_string()
            };
            state.reason = if truthy(payload.get("reason")) { text_of(&payload["reason"]) } else { String::new() };
            if let Some(t) = payload["text"].as_str() {
                state.reply = t.into();
            }
        }
    }

    fn finish(&self, tid: &str, turn: &str, rc: i32, stderr: &str, state: Turn) {
        let interrupted = {
            let mut inner = self.inner();
            if inner.turn.as_ref().is_some_and(|(_, t)| t == turn) {
                inner.turn = None;
                inner.stopper = None;
            }
            if !inner.done_turns.insert(turn.into()) {
                return;
            }
            inner.interrupted.contains(turn)
        };
        if let Some((inp, cached, out)) = state.usage {
            let mut body = json!({"input": inp, "output": out, "total": inp + out, "usage": {"input": inp, "cached": cached, "output": out}});
            if !state.model.is_empty() {
                body["model"] = json!(state.model);
            }
            self.emit(event("tokens", Some(tid), Some(turn), body));
        }
        if interrupted {
            self.emit(event("turn_completed", Some(tid), Some(turn), json!({"status": "interrupted"})));
        } else if state.terminal == "completed" && rc == 0 {
            let reply = if state.reply.is_empty() { state.streamed.trim().to_string() } else { state.reply };
            if !reply.is_empty() {
                self.emit(event("message", Some(tid), Some(turn), json!({"text": reply, "final": true})));
            }
            self.emit(event("turn_completed", Some(tid), Some(turn), json!({"status": "completed"})));
        } else {
            let error = if !state.reason.is_empty() {
                state.reason
            } else if !stderr.is_empty() {
                stderr.into()
            } else if !state.terminal.is_empty() {
                format!("muse run ended {}", state.terminal)
            } else {
                format!("muse exited {rc}")
            };
            let limit = is_limit(&error);
            self.emit(event("turn_failed", Some(tid), Some(turn), json!({"error": error, "limit": limit})));
        }
        self.emit(event("status", Some(tid), None, json!({"state": "idle"})));
    }

    pub fn interrupt(&self, _thread_id: &str, turn_id: &str) {
        let mut inner = self.inner();
        if inner.turn.as_ref().is_none_or(|(_, t)| t != turn_id) {
            return;
        }
        inner.interrupted.insert(turn_id.into());
        if let Some(s) = &inner.stopper {
            s.terminate(STOP_TIMEOUT);
        }
    }
}

/// The last STDERR_TAIL characters of stderr, without the CLI's progress notes ("muse: workspace root: ..."),
/// which are not why a run failed.
fn stderr_tail(file: &mut std::fs::File) -> String {
    let mut bytes = vec![];
    if file.seek(SeekFrom::Start(0)).is_ok() {
        let _ = file.read_to_end(&mut bytes);
    }
    let text = String::from_utf8_lossy(&bytes);
    let kept = text.lines().filter(|l| !l.trim().is_empty() && !l.starts_with("muse: ")).collect::<Vec<_>>().join("\n");
    let kept = kept.trim();
    let skip = kept.chars().count().saturating_sub(STDERR_TAIL);
    kept.chars().skip(skip).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    #[tokio::test]
    async fn turn_launch_keeps_the_bot_identity_and_prompt_access() {
        if !super::super::isolationtests::child("hosts::musecode::tests::turn_launch_keeps_the_bot_identity_and_prompt_access") { return; }
        let f = super::super::isolationtests::Fixture::new();
        let host = MuseCodeHost::new("acme", f.argv(), Some("echo".into()), f.root.join("muse"), mpsc::unbounded_channel().0);
        let settings = f.settings();
        let prompt = f.root.join("muse/prompt.md");
        super::super::isolationtests::prefix(&host.command("thread", &settings, &prompt, None), &host.argv("thread", &settings, &prompt, None));
        if !f.actual() { return; }
        prepare_config(&host.0.config_home).unwrap();
        let auth = host.0.config_home.join("muse/auth.json");
        std::fs::write(&auth, "fixture login").unwrap();
        prepare_config(&host.0.config_home).unwrap();
        prepare_data(&host.0.data_home).unwrap();
        let session = host.0.data_home.join("session-probe");
        let prompt = host.prompt_file("fixture prompt").unwrap();
        f.run(host.command("thread", &settings, &prompt, None), &[&prompt, &auth], &[&auth, &session]).await;
        use std::os::unix::fs::MetadataExt;
        assert_eq!(prompt.metadata().unwrap().uid(), 10003);
        assert_eq!(host.0.data_home.metadata().unwrap().uid(), 10003);
    }

    fn script(dir: &Path, name: &str, body: &str) -> String {
        let path = dir.join(name);
        crate::util::write_script(&path, body);
        path.display().to_string()
    }

    async fn until_idle(rx: &mut mpsc::UnboundedReceiver<Value>, secs: u64) -> Vec<Value> {
        let mut got = vec![];
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(secs), rx.recv()).await {
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        got
    }

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("{name}-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[tokio::test]
    async fn every_musecode_fixture_gives_the_python_hosts_events() {
        let dir = temp("fake-muse");
        let cmd = script(&dir, "muse", "#!/bin/sh\ncat \"$FIXTURE\"\n");
        for (path, expected) in crate::hosts::fixtures::cases("musecode") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = MuseCodeHost::new("acme", vec![cmd.clone()], None, dir.join("state"), tx);
            host.start();
            let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
            settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
            settings.env.insert("FIXTURE".into(), path.display().to_string());
            let thread = host.start_thread(&settings);
            host.start_turn(&thread, "hello", None).unwrap();
            let mut got = until_idle(&mut rx, 10).await;
            for ev in &mut got {
                ev.as_object_mut().unwrap().retain(|k, _| k != "thread_id" && k != "turn_id");
            }
            assert_eq!(got, expected, "{}", path.display());
        }
        // Every prompt file is gone once its turn ends.
        assert_eq!(std::fs::read_dir(dir.join("state")).unwrap().flatten().filter(|e| e.file_name() != "config" && e.file_name() != "data").count(), 0);
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// `muse exec --json` (1.4.8) carries no token counts: they are `model_completed` records in the session's
    /// durable log. Only this turn's run counts; the attribution copies and an earlier turn do not.
    #[tokio::test]
    async fn usage_and_model_come_from_the_session_log_the_turn_appended() {
        let dir = temp("fake-muse");
        let fixtures = crate::hosts::fixtures::dir("musecode");
        let stream = fixtures.join("real-tool.jsonl");
        let sid = serde_json::from_str::<Value>(std::fs::read_to_string(&stream).unwrap().lines().next().unwrap()).unwrap()["stream"]["id"]
            .as_str()
            .unwrap()
            .to_string();
        // Under the turn's own XDG_DATA_HOME, not HOME's ~/.local/share.
        let log = dir.join("state/data/muse/sessions/2026/10/14").join(&sid).join("session.jsonl");
        std::fs::create_dir_all(log.parent().unwrap()).unwrap();
        std::fs::write(&log, "").unwrap(); // the session existed before this turn
        let cmd = script(&dir, "muse", "#!/bin/sh\ncat \"$SESSION\" > \"$LOG\"\ncat \"$FIXTURE\"\n");
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = MuseCodeHost::new("coo", vec![cmd], None, dir.join("state"), tx);
        host.start();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        for (k, v) in [
            ("PATH", "/usr/bin:/bin".to_string()),
            ("HOME", dir.join("home").display().to_string()),
            ("FIXTURE", stream.display().to_string()),
            ("SESSION", fixtures.join("session-logs/real-tool.session.jsonl").display().to_string()),
            ("LOG", log.display().to_string()),
        ] {
            settings.env.insert(k.into(), v);
        }
        let thread = host.resume_thread(&sid, &settings);
        host.start_turn(&thread, "x", None).unwrap();
        let tokens: Vec<Value> = until_idle(&mut rx, 10).await.into_iter().filter(|e| e["kind"] == "tokens").collect();
        assert_eq!(tokens.len(), 1, "{tokens:?}");
        assert_eq!(host.env(&settings)["XDG_DATA_HOME"], dir.join("state/data").display().to_string());
        assert_eq!(tokens[0]["usage"], json!({"input": 37200, "cached": 24200, "output": 215}));
        assert_eq!(tokens[0]["model"], "muse-spark-1.3");
        let mut meter = crate::execute::Meter::default();
        meter.add(&tokens[0]);
        let r = meter.report("", "musecode", "subscription").unwrap();
        assert_eq!((r["input_tokens"].clone(), r["cached_tokens"].clone(), r["model"].clone()), (json!(13000), json!(24200), json!("muse-spark-1.3")));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_turn_names_its_session_keeps_secrets_off_argv_and_reads_the_prompt_from_a_file() {
        let dir = temp("fake-muse");
        // Records its argv, its environment and the prompt file's text, then answers like `--provider echo`.
        let cmd = script(
            &dir,
            "muse",
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$OUT/argv\"\nenv > \"$OUT/env\"\nwhile [ $# -gt 1 ]; do [ \"$1\" = --prompt-file ] && cat \"$2\" > \"$OUT/prompt\"; shift; done\n\
             echo '{\"payload_type\":\"run.terminal.completed\",\"payload\":{\"terminal\":\"completed\",\"text\":\"done\"}}'\n",
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = MuseCodeHost::new("coo", vec![cmd], None, dir.join("state"), tx);
        host.start();
        let mut settings = Settings { cwd: dir.display().to_string(), model: Some("muse-spark-1.3".into()), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin"), ("HOME", "/home/x"), ("HUB_API_URL", "https://hub.test"), ("HUB_TOKEN", "tok-secret"),
                       ("HUB_BOT", "coo"), ("LINEAR_API_KEY", "lin-secret"), ("OUT", &dir.display().to_string())] {
            settings.env.insert(k.into(), v.into());
        }
        settings.mcp_servers = vec![json!({"name": "linear", "service": "linear", "url": "https://mcp.linear.app/mcp", "transport": "http",
                                           "env": "LINEAR_API_KEY", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}", "X-Key": "${LINEAR_API_KEY}"}})];
        let thread = host.start_thread(&settings);
        host.start_turn(&thread, "Say hi", None).unwrap();
        let got = until_idle(&mut rx, 10).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_completed" && e["status"] == "completed"), "{got:?}");
        let argv: Vec<String> = std::fs::read_to_string(dir.join("argv")).unwrap().lines().map(String::from).collect();
        let after = |flag: &str| argv[argv.iter().position(|a| a == flag).unwrap() + 1].clone();
        assert_eq!(&argv[..2], &["exec", "--json"]);
        assert_eq!(after("--session-id"), thread);
        assert_eq!(after("--model"), "muse-spark-1.3");
        assert_eq!(after("--reasoning-effort"), "medium");
        assert_eq!(std::fs::read_to_string(dir.join("prompt")).unwrap(), "Say hi");
        assert!(!argv.iter().any(|a| a.contains("Say hi") || a.contains("secret")), "{argv:?}");
        let overrides: Vec<&String> = argv.iter().filter(|a| a.starts_with("mcp_servers.")).collect();
        let hub: Value = serde_json::from_str(overrides[0].strip_prefix("mcp_servers.hub=").unwrap()).unwrap();
        assert_eq!(hub["transport"], "stdio");
        assert_eq!(hub["env"]["HUB_TOKEN"], "${HUB_TOKEN}");
        let linear: Value = serde_json::from_str(overrides[1].strip_prefix("mcp_servers.linear=").unwrap()).unwrap();
        assert_eq!(linear, json!({"transport": "streamable_http", "url": "https://mcp.linear.app/mcp",
                                  "headers": {"Authorization": "${TICO_MCP_1}", "X-Key": "${LINEAR_API_KEY}"}}));
        let env = std::fs::read_to_string(dir.join("env")).unwrap();
        assert!(env.lines().any(|l| l == "TICO_MCP_1=Bearer lin-secret"));
        assert!(env.lines().any(|l| l == "MUSE_NO_AUTO_UPDATE=1"));
        // A restarted runner resumes the same session by its id.
        assert_eq!(host.resume_thread(&thread, &settings), thread);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn an_interrupted_turn_is_interrupted_not_failed() {
        let dir = temp("fake-muse");
        let cmd = script(&dir, "muse", "#!/bin/sh\necho '{\"payload_type\":\"run.output.delta\",\"payload\":{\"text\":\"Working\"}}'\nexec sleep 30\n");
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = MuseCodeHost::new("coo", vec![cmd], None, dir.join("state"), tx);
        host.start();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.start_thread(&settings);
        let turn = host.start_turn(&thread, "x", None).unwrap();
        while let Some(ev) = rx.recv().await {
            if ev["kind"] == "delta" {
                break;
            }
        }
        host.interrupt(&thread, &turn);
        let got = until_idle(&mut rx, 15).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_completed" && e["status"] == "interrupted"), "{got:?}");
        assert!(!got.iter().any(|e| e["kind"] == "turn_failed"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn tool_calls_follow_the_real_muse_stream() {
        let proposed = |task: &str| json!({"event": {"kind": "proposed", "task_kind": task}});
        assert_eq!(tool_started("task.lifecycle.proposed", &proposed("tool.mcp__hub__hub_task_list")).as_deref(), Some("mcp__hub__hub_task_list"));
        assert_eq!(tool_started("task.lifecycle.proposed", &proposed("model.meta.response")), None);
        assert_eq!(tool_started("task.lifecycle.started", &json!({"task_id": "t"})), None);
        assert_eq!(tool_started("tool.bash.started", &json!({"tool_name": "bash"})).as_deref(), Some("bash"));
    }

    #[test]
    fn flags_usage_and_sign_in_follow_the_python_host() {
        assert_eq!(model_for(None), "muse-spark-1.3");
        assert_eq!(model_for(Some("default")), "muse-spark-1.3");
        assert_eq!(model_for(Some("muse-next")), "muse-next");
        assert_eq!(effort_for(Some("MAX")), "max");
        assert_eq!(effort_for(Some("turbo")), "medium");
        let u = json!({"usage": {"inputTokens": 100, "outputTokens": 7, "cacheReadTokens": 40,
                                 "cumulative": {"inputTokens": 900, "outputTokens": 70}}, "other": {"input_tokens": "3", "output_tokens": 1}});
        assert_eq!(usage_of(&u), Some((103, 40, 8)));
        assert_eq!(usage_of(&json!({"text": "x"})), None);
        let mut env = Env::new();
        env.insert("HOME".into(), "/home/x".into());
        assert_eq!(auth_file(&env), PathBuf::from("/home/x/.config/muse/auth.json"));
        env.insert("XDG_CONFIG_HOME".into(), "/cfg".into());
        assert_eq!(auth_file(&env), PathBuf::from("/cfg/muse/auth.json"));
        assert!(crate::hosts::is_auth_rejected("invalid run configuration: missing Meta credentials"));
        // A config folder the runner cannot read is "could not be checked", not "signed out".
        use std::os::unix::fs::PermissionsExt;
        let dir = temp("muse-auth");
        assert_eq!(signed_in(&dir.join("auth.json")), Some(false));
        std::fs::write(dir.join("auth.json"), "{}").unwrap();
        assert_eq!(signed_in(&dir.join("auth.json")), Some(true));
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o000)).unwrap();
        let locked = signed_in(&dir.join("auth.json"));
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::remove_dir_all(&dir).unwrap();
        if unsafe { libc::geteuid() } != 0 {
            assert_eq!(locked, None);
        }
    }

    /// The installed CLI on its offline echo provider, two turns on one thread with the hub MCP server: both land
    /// in one session. `MUSE_BIN=/path/to/muse cargo test real_muse -- --ignored` (needs no account).
    #[tokio::test]
    #[ignore]
    async fn real_muse_two_turns_share_one_session() {
        let Ok(bin) = std::env::var("MUSE_BIN") else { return };
        let dir = temp("real-muse");
        std::fs::create_dir_all(dir.join("repo")).unwrap();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = MuseCodeHost::new("coo", vec![bin.clone()], Some("echo".into()), dir.join("state"), tx);
        host.start();
        let mut settings = Settings { cwd: dir.join("repo").display().to_string(), ..Default::default() };
        settings.env = std::env::vars().collect();
        settings.env.insert("HUB_API_URL".into(), "http://127.0.0.1:9".into());
        settings.env.insert("HUB_TOKEN".into(), "tok-test".into());
        settings.env.insert("HUB_BOT".into(), "coo".into());
        let thread = host.start_thread(&settings);
        for text in ["first", "second"] {
            host.start_turn(&thread, text, None).unwrap();
            let got = until_idle(&mut rx, 120).await;
            let messages: Vec<&Value> = got.iter().filter(|e| e["kind"] == "message").map(|e| &e["text"]).collect();
            assert_eq!(messages, vec![&json!(format!("echo: {text}"))], "{got:?}");
        }
        println!("session {thread}");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn turns_run_on_ticos_own_muse_folder_with_no_settings_mcp_servers() {
        let dir = temp("muse-config");
        let home = dir.join("config");
        assert_eq!(prepare_config(&home).unwrap(), home.join("muse"));
        let settings = home.join("muse").join("settings.json");
        std::fs::write(&settings, r#"{"schema_version": 1, "theme": "dark", "mcpServers": {"mine": {}}, "mcp_servers": {}}"#).unwrap();
        prepare_config(&home).unwrap();
        let kept: Value = serde_json::from_str(&std::fs::read_to_string(&settings).unwrap()).unwrap();
        assert_eq!(kept, json!({"schema_version": 1, "theme": "dark"}));
        std::fs::write(&settings, "not json").unwrap();
        prepare_config(&home).unwrap();
        assert_eq!(serde_json::from_str::<Value>(&std::fs::read_to_string(&settings).unwrap()).unwrap(), json!({"schema_version": 1}));
        let (tx, _rx) = mpsc::unbounded_channel();
        let data = dir.join("data");
        let host = MuseCodeHost::on_config("coo", vec!["muse".into()], None, dir.join("state"), (home.clone(), data.clone()), tx);
        let mut s = Settings { cwd: dir.display().to_string(), ..Default::default() };
        s.env.insert("XDG_CONFIG_HOME".into(), "/operator/.config".into());
        s.env.insert("XDG_DATA_HOME".into(), "/operator/.local/share".into());
        assert_eq!(host.env(&s)["XDG_CONFIG_HOME"], home.display().to_string());
        assert_eq!(host.env(&s)["XDG_DATA_HOME"], data.display().to_string());
        let _ = std::fs::remove_dir_all(dir);
    }
    /// Sessions (and their locks) go to a folder of Tico's own per login: the runner's beside its config home, a
    /// subscription profile's in that profile's Muse home, so two profiles never share one.
    #[test]
    fn each_login_keeps_its_sessions_in_its_own_data_folder() {
        use crate::profiles::Profile;
        let dir = temp("muse-data");
        let (a, b) = (Profile::new("a", &dir.join("a").display().to_string(), false), Profile::new("b", &dir.join("b").display().to_string(), false));
        assert_eq!(data_home(Some(&a)), config_home(None).with_file_name("profiles").join("a/data"));
        assert_ne!(data_home(Some(&a)), data_home(Some(&b)));
        assert_eq!(data_home(Some(&Profile::new("mine", "~", true))), data_home(None)); // the operator's own logins
        assert_eq!(data_home(None).parent(), config_home(None).parent());
        assert_ne!(data_home(None), config_home(None));
        // Never inside a folder that is handed to the bot user whole: a config home or the profile's own folder.
        for (p, home) in [(Some(&a), &dir.join("a")), (Some(&b), &dir.join("b")), (None, &config_home(None))] {
            assert!(!data_home(p).starts_with(home) && !data_home(p).starts_with(config_home(p)), "{p:?}");
        }
        let mut env = Env::new();
        assert!(crate::hosts::use_own_config_home("musecode", Some(&b), &mut env));
        assert_eq!(env["XDG_DATA_HOME"], data_home(Some(&b)).display().to_string());
        let _ = std::fs::remove_dir_all(dir);
    }

    /// A data folder made 0700 for the bot user; one already there that the turn's user cannot write fails the turn
    /// (and readiness) naming that folder, before Muse can fail on its session lock.
    #[tokio::test]
    async fn a_data_folder_the_turn_cannot_write_is_named_not_a_lock_failure() {
        use std::os::unix::fs::PermissionsExt;
        let dir = temp("muse-data");
        let data = dir.join("data");
        assert_eq!(data_problem(&data), None);
        prepare_data(&data).unwrap();
        assert_eq!(std::fs::metadata(&data).unwrap().permissions().mode() & 0o777, 0o700);
        std::os::unix::fs::symlink(&data, dir.join("link")).unwrap();
        assert!(prepare_data(&dir.join("link")).unwrap_err().to_string().contains("not a plain folder"));
        let sessions = data.join("muse/sessions");
        std::fs::create_dir_all(&sessions).unwrap();
        std::fs::set_permissions(&sessions, std::fs::Permissions::from_mode(0o555)).unwrap();
        let cmd = script(&dir, "muse", "#!/bin/sh\ntouch \"$OUT\"\n");
        let (tx, _rx) = mpsc::unbounded_channel();
        let host = MuseCodeHost::on_config("coo", vec![cmd], None, dir.join("state"), (dir.join("config"), data.clone()), tx);
        host.start();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        settings.env.insert("OUT".into(), dir.join("ran").display().to_string());
        let thread = host.start_thread(&settings);
        let started = host.start_turn(&thread, "x", None);
        let problem = data_problem(&data);
        std::fs::set_permissions(&sessions, std::fs::Permissions::from_mode(0o755)).unwrap();
        if unsafe { libc::geteuid() } != 0 {
            let error = started.unwrap_err().to_string();
            assert!(error.contains(&sessions.display().to_string()) && error.contains("can't write"), "{error}");
            assert_eq!(problem.as_deref(), Some(error.as_str()));
            assert!(!dir.join("ran").exists(), "muse never started");
            // Refused before the config home was prepared, so nothing was handed to the bot user first.
            assert!(!dir.join("config").exists(), "config home prepared before the data check");
            assert_eq!(crate::hosts::data_problem("claude", None), None);
        }
        let _ = std::fs::remove_dir_all(dir);
    }

    /// Under isolation the supervisor's own access check would pass (DAC_OVERRIDE): a folder there that another
    /// user owns, like the ~/.local/share/muse pre-isolation turns left, is judged by the turn's uid and named.
    #[test]
    fn under_isolation_a_data_folder_another_user_owns_is_named() {
        if !super::super::isolationtests::child("hosts::musecode::tests::under_isolation_a_data_folder_another_user_owns_is_named") { return; }
        use std::os::unix::fs::PermissionsExt;
        let dir = temp("muse-data");
        let data = dir.join("data");
        std::fs::create_dir_all(data.join("muse/sessions")).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let problem = data_problem(&data).expect("a folder the bot user does not own");
        assert!(problem.contains(&data.display().to_string()), "{problem}");
        assert!(prepare_data(&data).unwrap_err().to_string().contains(&data.display().to_string()));
        let _ = std::fs::remove_dir_all(dir);
    }

    /// The data check follows the kernel for a turn's user with no supplementary groups: the owner's bits alone decide
    /// for the owner (even where group or others could write), then the group's, then the others'; and making an
    /// entry needs both write and search.
    #[test]
    fn the_data_check_follows_owner_then_group_then_other() {
        let (bot, other_group) = ((10003, 10002), (10003, 20000));
        for (mode, owner, user, writable) in [
            (0o700, (10003, 10002), bot, true),          // owner
            (0o077, (10003, 10002), bot, false),         // owner precedence: group and others' bits don't count
            (0o500, (10003, 10002), bot, false),         // owner, no write
            (0o600, (10003, 10002), bot, false),         // owner, no search
            (0o770, (10002, 10002), bot, true),          // group
            (0o707, (10002, 10002), bot, false),         // group precedence: others' bits don't count
            (0o750, (10002, 10002), bot, false),         // group, no write (the pre-isolation ~/.local/share/muse)
            (0o760, (10002, 10002), bot, false),         // group, no search
            (0o777, (10002, 10002), other_group, true),  // other
            (0o775, (10002, 10002), other_group, false), // other, no write
            (0o776, (10002, 10002), other_group, false), // other, no search
        ] {
            assert_eq!(can_write(mode, owner, user), writable, "{mode:o} owner {owner:?} user {user:?}");
        }
    }
}
