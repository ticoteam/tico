//! The Muse Code host (`runner/hosts/musecode.py`): one `muse exec --json` process per turn (Meta's coding CLI).
//!
//! A thread is a Muse session whose id Tico makes (a UUID): every turn runs with `--session-id <id>`, and an id
//! the CLI already has continues that session, so a restart resumes it. The prompt goes in a file the turn can
//! read (the CLI takes no prompt on stdin). Events are one JSON object per line: `run.output.delta` streams text,
//! `run.terminal.completed|failed|cancelled` ends the run with the final `text` or a `reason`, and token counts
//! ride on per-completion usage objects. The hub and the bot's remote MCP servers are per-run
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

pub const EXECUTABLE: &str = "muse";
pub const MODELS: &[&str] = &["muse-spark-1.3"];
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
    let state = STATE_DIR.get().cloned().unwrap_or_else(|| std::env::temp_dir().join("tico-runner-state"));
    crate::isolation::bot_state(&state).join("musecode").join("config")
}

/// Make `home` (XDG_CONFIG_HOME) ready for a bot turn: `muse/` exists and its settings name no MCP servers, so
/// none but this run's own load. The folder is Tico's own, never a person's.
pub fn prepare_config(home: &Path) -> Result<PathBuf, HostError> {
    let directory = crate::isolation::mkdir(&home.join("muse"), 0o700).map_err(|e| failed(format!("Muse settings folder: {e}")))?;
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
    tx: Events,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct MuseCodeHost(Arc<Shared>);

impl MuseCodeHost {
    #[cfg(test)]
    pub fn new(bot: &str, cmd: Vec<String>, provider: Option<String>, state_dir: PathBuf, tx: Events) -> MuseCodeHost {
        let config_home = state_dir.join("config");
        Self::on_config(bot, cmd, provider, state_dir, config_home, tx)
    }

    pub fn on_config(bot: &str, cmd: Vec<String>, provider: Option<String>, state_dir: PathBuf, config_home: PathBuf, tx: Events) -> MuseCodeHost {
        MuseCodeHost(Arc::new(Shared { bot: bot.into(), cmd, provider, state_dir, config_home, tx, inner: Mutex::new(Inner::default()) }))
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

    /// The turn's environment (this process's when the settings carry none), HOME kept, Tico's own Muse folder,
    /// QUIET and the MCP variables.
    pub fn env(&self, settings: &Settings) -> Env {
        let mut env = if settings.env.is_empty() { std::env::vars().collect() } else { settings.env.clone() };
        if env.get("HOME").is_none_or(|h| h.is_empty()) {
            if let Ok(home) = std::env::var("HOME") {
                env.insert("HOME".into(), home);
            }
        }
        env.insert("XDG_CONFIG_HOME".into(), self.0.config_home.display().to_string());
        env.extend(QUIET.iter().map(|(k, v)| (k.to_string(), v.to_string())));
        env.extend(mcp_overrides(settings).1);
        env
    }

    fn prompt_file(&self, text: &str) -> std::io::Result<PathBuf> {
        use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
        std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&self.0.state_dir)?;
        let path = self.0.state_dir.join(format!("prompt-{}.md", uuid::Uuid::new_v4().simple()));
        let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path)?;
        if let Err(e) = f.write_all(text.as_bytes()) {
            let _ = std::fs::remove_file(&path);
            return Err(e);
        }
        Ok(path)
    }

    pub fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let (stdout, exited, stderr, prompt, pid) = {
            let mut inner = self.inner();
            if !inner.up {
                return Err(failed("muse host is not running"));
            }
            if inner.turn.is_some() {
                return Err(failed("muse host already has a turn running"));
            }
            let settings = inner.threads.get(thread_id).cloned().ok_or_else(|| failed(format!("unknown muse thread {thread_id}")))?;
            prepare_config(&self.0.config_home)?;
            let prompt = self.prompt_file(text).map_err(|e| failed(format!("could not start muse: {e}")))?;
            let spawned = (|| {
                // stderr goes to an unlinked file: read back for the error text, never a pipe that could fill.
                let path = std::env::temp_dir().join(format!("tico-muse-stderr-{}", uuid::Uuid::new_v4().simple()));
                let stderr = std::fs::OpenOptions::new().read(true).write(true).create_new(true).open(&path)?;
                let _ = std::fs::remove_file(&path);
                let argv = self.argv(thread_id, &settings, &prompt, effort);
                let child = Command::new(&argv[0])
                    .args(&argv[1..])
                    .stdin(Stdio::null())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::from(stderr.try_clone()?))
                    .current_dir(&settings.cwd)
                    .env_clear()
                    .envs(self.env(&settings))
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
            (stdout, exited, stderr, prompt, pid)
        };
        log(&format!("muse[{}]: turn started pid {}", self.0.bot, pid.map_or("?".into(), |p| p.to_string())));
        self.emit(event("status", Some(thread_id), None, json!({"state": "active"})));
        let host = self.clone();
        let (tid, t2) = (thread_id.to_string(), turn.clone());
        let reader = tokio::spawn(async move { host.read_loop(stdout, exited, stderr, prompt, tid, t2).await });
        self.inner().reader = Some(reader);
        Ok(turn)
    }

    async fn read_loop(
        &self,
        stdout: Option<ChildStdout>,
        exited: tokio::sync::oneshot::Receiver<std::io::Result<std::process::ExitStatus>>,
        mut stderr: std::fs::File,
        prompt: PathBuf,
        tid: String,
        turn: String,
    ) {
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
            self.emit(event(
                "tokens",
                Some(tid),
                Some(turn),
                json!({"input": inp, "output": out, "total": inp + out, "usage": {"input": inp, "cached": cached, "output": out}}),
            ));
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

    fn script(dir: &Path, name: &str, body: &str) -> String {
        use std::os::unix::fs::PermissionsExt;
        let path = dir.join(name);
        std::fs::write(&path, body).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
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
        assert_eq!(std::fs::read_dir(dir.join("state")).unwrap().flatten().filter(|e| e.file_name() != "config").count(), 0);
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
        let host = MuseCodeHost::on_config("coo", vec!["muse".into()], None, dir.join("state"), home.clone(), tx);
        let mut s = Settings { cwd: dir.display().to_string(), ..Default::default() };
        s.env.insert("XDG_CONFIG_HOME".into(), "/operator/.config".into());
        assert_eq!(host.env(&s)["XDG_CONFIG_HOME"], home.display().to_string());
        let _ = std::fs::remove_dir_all(dir);
    }
}
