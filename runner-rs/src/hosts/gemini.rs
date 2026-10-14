//! The Gemini CLI host (`runner/hosts/gemini.py`): one headless `gemini -p ""` per turn, the prompt on stdin,
//! stream-json on stdout. The host picks the session id for a thread's first turn and resumes it after. A home of
//! its own (`GEMINI_CLI_HOME`, settings.json rewritten before each turn) keeps the bot's sessions and API-key
//! sign-in apart from the operator's own Gemini login.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};

use super::{event, failed, is_limit, Events, HostError, Settings};
use crate::env::Env;
use crate::util::{log, text_of};

pub const STREAM_ARGS: &[&str] = &["--output-format", "stream-json", "--approval-mode", "yolo", "--skip-trust"];
pub const DEFAULT_MODEL: &str = "gemini-3.8-flash";
pub const MODELS: &[&str] = &["gemini-3.8-flash"];
const STOP_TIMEOUT: Duration = Duration::from_secs(10);
const STDERR_TAIL: usize = 2000;
const RESTORING: &str = "Saved provider session is unavailable; restoring this conversation's context.";

/// Gemini's thinking level for an effort: LOW, MEDIUM or HIGH, HIGH when unset or unknown.
pub fn effort_for(effort: Option<&str>) -> &'static str {
    match effort.unwrap_or_default().trim().to_lowercase().as_str() {
        "low" => "LOW",
        "medium" => "MEDIUM",
        _ => "HIGH",
    }
}

fn count(v: &Value, key: &str) -> i64 {
    match &v[key] {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => *b as i64,
        _ => 0,
    }
}

/// (input, output, total) from a result's `stats`; total is input + output when the CLI gives none.
pub fn usage_tokens(stats: &Value) -> (i64, i64, i64) {
    let (inp, out) = (count(stats, "input_tokens"), count(stats, "output_tokens"));
    let total = count(stats, "total_tokens");
    (inp, out, if total != 0 { total } else { inp + out })
}

/// The settings.json the CLI reads from this host's home: API-key auth, the effort's thinking level, and the
/// bot's declared remote MCP servers. The file keeps their `${VAR}` placeholders, which the CLI expands from
/// the turn's environment; the home is this host's own, so the operator's servers are not here.
pub fn settings_file(model: &str, effort: Option<&str>, servers: &[Value]) -> Value {
    let mut file = json!({
        "experimental": {"dynamicModelConfiguration": true},
        "security": {"auth": {"selectedType": "gemini-api-key", "enforcedType": "gemini-api-key"}},
        "modelConfigs": {"customOverrides": [{
            "match": {"model": model},
            "modelConfig": {"generateContentConfig": {"thinkingConfig": {"thinkingLevel": effort_for(effort)}}},
        }]},
    });
    if !servers.is_empty() {
        file["mcpServers"] = Value::Object(crate::mcp::gemini_config(servers));
    }
    file
}

/// True when a bot's primary or fallback harness is the Gemini CLI, which needs GEMINI_API_KEY.
pub fn uses_gemini_cli(config: &Value) -> bool {
    let harness = text_of(&config["harness"]);
    let harness = if harness.is_empty() && config["runtime"] == "gemini" { "gemini".into() } else { harness };
    harness == "gemini" || (config["fallback"].is_object() && config["fallback"]["harness"] == "gemini")
}

fn credential_names() -> &'static Mutex<Map<String, Value>> {
    static N: OnceLock<Mutex<Map<String, Value>>> = OnceLock::new();
    N.get_or_init(|| Mutex::new(Map::new()))
}

/// The variable names of each bot's granted credentials (`GET runner-credential-grants` `bots`), read on each
/// full sync.
pub fn set_credential_names(bots: &Value) {
    *credential_names().lock().unwrap() = bots.as_object().cloned().unwrap_or_default();
}

/// The gemini runtime's sign-in: a GEMINI_API_KEY grant for every bot that runs the Gemini CLI.
pub fn sign_in(assignments: &[Value]) -> (&'static str, String) {
    let names = credential_names().lock().unwrap().clone();
    let mut missing: Vec<String> = vec![];
    let mut cli = false;
    for row in assignments.iter().filter(|r| r["config"]["runtime"] == "gemini" && uses_gemini_cli(&r["config"])) {
        cli = true;
        let bot = text_of(&row["bot"]);
        if !names.get(&bot).and_then(|n| n.as_array()).is_some_and(|n| n.iter().any(|k| k == "GEMINI_API_KEY")) {
            missing.push(bot);
        }
    }
    missing.sort();
    missing.dedup();
    if !cli {
        ("ready", "Gemini CLI is not used by assigned bots".into())
    } else if !missing.is_empty() {
        ("missing", format!("Gemini API key required ({})", missing.join(", ")))
    } else {
        ("ready", "Gemini API key configured".into())
    }
}

#[derive(Clone)]
struct Thread {
    settings: Settings,
    started: bool,
}

#[derive(Default)]
struct Inner {
    up: bool,
    threads: HashMap<String, Thread>,
    turn: Option<(String, String)>,
    stopper: Option<super::Stopper>,
    reply: HashMap<String, String>,
    interrupted: HashSet<String>,
    done_turns: HashSet<String>,
    reader: Option<tokio::task::JoinHandle<()>>,
}

struct Shared {
    bot: String,
    home: PathBuf,
    cmd: Vec<String>,
    tx: Events,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct GeminiHost(Arc<Shared>);

impl GeminiHost {
    pub fn new(bot: &str, home: &Path, cmd: Vec<String>, tx: Events) -> GeminiHost {
        GeminiHost(Arc::new(Shared { bot: bot.into(), home: home.to_path_buf(), cmd, tx, inner: Mutex::new(Inner::default()) }))
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
            if let Some(s) = inner.stopper.take() {
                if let Some((_, turn)) = inner.turn.clone() {
                    inner.interrupted.insert(turn);
                }
                s.terminate(STOP_TIMEOUT);
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

    pub fn start_thread(&self, settings: &Settings) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        self.inner().threads.insert(id.clone(), Thread { settings: settings.clone(), started: false });
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> String {
        self.inner().threads.insert(thread_id.into(), Thread { settings: settings.clone(), started: true });
        thread_id.into()
    }

    fn argv(&self, thread_id: &str, t: &Thread) -> Vec<String> {
        let model = t.settings.model.clone().filter(|m| !m.is_empty()).unwrap_or_else(|| DEFAULT_MODEL.into());
        let mut argv = self.0.cmd.clone();
        argv.extend(["--model".into(), model]);
        argv.extend(STREAM_ARGS.iter().map(|s| s.to_string()));
        argv.extend([if t.started { "--resume" } else { "--session-id" }.into(), thread_id.into()]);
        // The prompt stays out of the process list: in headless mode -p appends stdin.
        argv.extend(["-p".into(), String::new()]);
        argv
    }

    fn env(&self, settings: &Settings) -> Env {
        let mut env = if settings.env.is_empty() { crate::env::process_environment() } else { settings.env.clone() };
        if env.get("HOME").is_none_or(|h| h.is_empty()) {
            if let Ok(home) = std::env::var("HOME") {
                env.insert("HOME".into(), home);
            }
        }
        env.insert("GEMINI_CLI_HOME".into(), self.0.home.display().to_string());
        env.insert("GEMINI_CLI_SYSTEM_SETTINGS_PATH".into(), self.0.home.join("settings.json").display().to_string());
        env.insert("GEMINI_CLI_TRUST_WORKSPACE".into(), "true".into());
        env
    }

    fn prepare_home(&self, model: &str, effort: Option<&str>, servers: &[Value]) -> std::io::Result<()> {
        use std::io::Write;
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        std::fs::create_dir_all(&self.0.home)?;
        std::fs::set_permissions(&self.0.home, std::fs::Permissions::from_mode(0o700))?;
        let tmp = self.0.home.join(format!("settings-{}.json", uuid::Uuid::new_v4().simple()));
        let written = (|| {
            let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp)?;
            f.write_all((serde_json::to_string_pretty(&settings_file(model, effort, servers)).unwrap() + "\n").as_bytes())?;
            std::fs::rename(&tmp, self.0.home.join("settings.json"))
        })();
        let _ = std::fs::remove_file(&tmp);
        written
    }

    fn launch(&self, thread_id: &str, t: &Thread) -> std::io::Result<(Child, std::fs::File)> {
        // stderr goes to an unlinked file: read back for the error text, never a pipe that could fill.
        let path = std::env::temp_dir().join(format!("tico-gemini-stderr-{}", uuid::Uuid::new_v4().simple()));
        let stderr = std::fs::OpenOptions::new().read(true).write(true).create_new(true).open(&path)?;
        let _ = std::fs::remove_file(&path);
        let argv = self.argv(thread_id, t);
        let mut command = Command::new(&argv[0]);
        command
            .args(&argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(stderr.try_clone()?))
            .current_dir(&t.settings.cwd)
            .env_clear()
            .envs(self.env(&t.settings));
        Ok((command.spawn()?, stderr))
    }

    pub fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let child = {
            let mut inner = self.inner();
            if !inner.up {
                return Err(failed("gemini host is not running"));
            }
            if inner.turn.is_some() {
                return Err(failed("gemini host already has a turn running"));
            }
            let t = inner.threads.get(thread_id).cloned().ok_or_else(|| failed(format!("unknown gemini thread {thread_id}")))?;
            if self.env(&t.settings).get("GEMINI_API_KEY").is_none_or(|k| k.is_empty()) {
                return Err(failed("Gemini API key is not configured for this bot"));
            }
            let model = t.settings.model.clone().filter(|m| !m.is_empty()).unwrap_or_else(|| DEFAULT_MODEL.into());
            self.prepare_home(&model, effort.or(t.settings.effort.as_deref()), &t.settings.mcp_servers)
                .map_err(|e| failed(format!("could not write the Gemini settings: {e}")))?;
            let launched = self.launch(thread_id, &t).map_err(|e| failed(format!("could not start gemini: {e}")))?;
            inner.turn = Some((thread_id.into(), turn.clone()));
            inner.reply.insert(turn.clone(), String::new());
            launched
        };
        log(&format!("gemini[{}]: turn started pid {}", self.0.bot, child.0.id().map_or("?".into(), |p| p.to_string())));
        self.emit(event("status", Some(thread_id), None, json!({"state": "active"})));
        let host = self.clone();
        let (tid, t2, text) = (thread_id.to_string(), turn.clone(), text.to_string());
        let reader = tokio::spawn(async move { host.read_loop(child.0, child.1, tid, t2, text).await });
        self.inner().reader = Some(reader);
        Ok(turn)
    }

    async fn read_loop(&self, mut child: Child, mut stderr: std::fs::File, tid: String, turn: String, text: String) {
        let mut recovered = false;
        loop {
            let stdout = child.stdout.take();
            if let Some(mut stdin) = child.stdin.take() {
                let text = text.clone();
                tokio::spawn(async move {
                    let _ = stdin.write_all(text.as_bytes()).await;
                    let _ = stdin.shutdown().await;
                });
            }
            let (stopper, exited) = super::own(child);
            {
                let mut inner = self.inner();
                if !inner.up || inner.interrupted.contains(&turn) {
                    stopper.terminate(STOP_TIMEOUT);
                }
                inner.stopper = Some(stopper);
            }
            let (mut result, mut saw_event) = (None, false);
            if let Some(stdout) = stdout {
                let mut lines = BufReader::new(stdout).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let Ok(msg) = serde_json::from_str::<Value>(line.trim()) else { continue };
                    saw_event = true;
                    if self.on_message(&msg, &tid, &turn) {
                        result = Some(msg);
                    }
                }
            }
            let rc = match exited.await {
                Ok(status) => super::return_code(&status),
                Err(_) => -1,
            };
            let error = stderr_tail(&mut stderr);
            // The CLI checks --resume before it calls the model, so a missing session restarts once from this
            // conversation's own history, with no tool effect replayed. Never after an event.
            let missing = format!("Error resuming session: Invalid session identifier \"{tid}\".");
            if !recovered && !saw_event && rc != 0 && error.starts_with(&missing) {
                let retry = {
                    let mut inner = self.inner();
                    let up = inner.up && !inner.interrupted.contains(&turn);
                    match inner.threads.get_mut(&tid) {
                        Some(t) if up && t.started => {
                            t.started = false;
                            let t = t.clone();
                            self.launch(&tid, &t).ok()
                        }
                        _ => None,
                    }
                };
                if let Some((c, s)) = retry {
                    child = c;
                    stderr = s;
                    recovered = true;
                    self.emit(event("diagnostic", Some(&tid), Some(&turn), json!({"text": RESTORING})));
                    continue;
                }
            }
            self.finish(&tid, &turn, result, rc, &error);
            return;
        }
    }

    /// One stream-json line to host events; true when it is the turn's result.
    fn on_message(&self, msg: &Value, tid: &str, turn: &str) -> bool {
        if !msg.is_object() {
            return false;
        }
        let tool = || {
            ["tool_name", "name"].iter().map(|k| &msg[*k]).find(|v| crate::util::truthy(Some(v))).cloned().unwrap_or(json!("tool"))
        };
        let item = || ["tool_id", "id"].iter().map(|k| &msg[*k]).find(|v| crate::util::truthy(Some(v))).cloned().unwrap_or(Value::Null);
        match msg["type"].as_str().unwrap_or_default() {
            "init" => {
                if let Some(t) = self.inner().threads.get_mut(tid) {
                    t.started = true;
                }
            }
            "message" if msg["role"] == "assistant" => {
                let text = text_of(&msg["content"]);
                if !text.is_empty() {
                    let delta = msg.get("delta").is_none_or(|d| crate::util::truthy(Some(d)));
                    {
                        let mut inner = self.inner();
                        let reply = inner.reply.entry(turn.into()).or_default();
                        if delta {
                            reply.push_str(&text);
                        } else {
                            *reply = text.clone();
                        }
                    }
                    if delta {
                        self.emit(event("delta", Some(tid), Some(turn), json!({"text": text, "delta_kind": "text"})));
                    } else {
                        self.emit(event("message", Some(tid), Some(turn), json!({"text": text, "final": false})));
                    }
                }
            }
            "tool_use" => {
                self.emit(event("tool", Some(tid), Some(turn), json!({"server": "", "tool": tool(), "status": "started", "item_id": item()})));
            }
            "tool_result" => {
                let status = if msg["status"] == "error" { "failed" } else { "completed" };
                self.emit(event("tool", Some(tid), Some(turn), json!({"server": "", "tool": tool(), "status": status, "item_id": item()})));
            }
            "result" => return true,
            _ => {}
        }
        false
    }

    fn finish(&self, tid: &str, turn: &str, result: Option<Value>, rc: i32, stderr: &str) {
        let (interrupted, reply, requested) = {
            let mut inner = self.inner();
            if inner.turn.as_ref().is_some_and(|(_, t)| t == turn) {
                inner.turn = None;
                inner.stopper = None;
            }
            let reply = inner.reply.remove(turn).unwrap_or_default();
            if !inner.done_turns.insert(turn.into()) {
                return;
            }
            let requested = inner.threads.get(tid).and_then(|t| t.settings.model.clone()).filter(|m| !m.is_empty()).unwrap_or_else(|| DEFAULT_MODEL.into());
            (inner.interrupted.contains(turn), reply, requested)
        };
        let result = result.filter(|r| r.is_object());
        let stats = result.as_ref().map(|r| r["stats"].clone()).filter(|s| s.is_object()).unwrap_or(json!({}));
        let mut actual: Vec<String> = stats["models"].as_object().map(|m| m.keys().cloned().collect()).unwrap_or_default();
        actual.sort();
        let mismatch = !actual.is_empty() && !actual.contains(&requested);
        if result.is_some() {
            let (inp, out, total) = usage_tokens(&stats);
            if total != 0 {
                let cached = count(&stats, "cached");
                self.emit(event("tokens", Some(tid), Some(turn), json!({"input": inp, "output": out, "total": total, "usage": {"input": inp, "cached": cached, "output": out}})));
            }
        }
        if interrupted {
            self.emit(event("turn_completed", Some(tid), Some(turn), json!({"status": "interrupted"})));
        } else if result.as_ref().is_some_and(|r| r["status"] == "success") && rc == 0 && !mismatch {
            if !reply.is_empty() {
                self.emit(event("message", Some(tid), Some(turn), json!({"text": reply, "final": true})));
            }
            self.emit(event("turn_completed", Some(tid), Some(turn), json!({"status": "completed"})));
        } else {
            let error = if mismatch { format!("Gemini CLI used {} instead of {requested}", actual.join(", ")) } else { error_text(result.as_ref(), rc, stderr) };
            self.emit(event("turn_failed", Some(tid), Some(turn), json!({"error": error, "limit": is_limit(&error)})));
        }
        self.emit(event("status", Some(tid), None, json!({"state": "idle"})));
    }

    pub fn interrupt(&self, turn_id: &str) {
        let mut inner = self.inner();
        if inner.turn.as_ref().is_none_or(|(_, t)| t != turn_id) || inner.stopper.is_none() {
            return;
        }
        inner.interrupted.insert(turn_id.into());
        if let Some(s) = &inner.stopper {
            s.terminate(STOP_TIMEOUT);
        }
    }
}

fn stderr_tail(file: &mut std::fs::File) -> String {
    let mut bytes = vec![];
    if file.seek(SeekFrom::Start(0)).is_ok() {
        let _ = file.read_to_end(&mut bytes);
    }
    let text = String::from_utf8_lossy(&bytes);
    let t = text.trim();
    let skip = t.chars().count().saturating_sub(STDERR_TAIL);
    t.chars().skip(skip).collect()
}

pub fn error_text(result: Option<&Value>, rc: i32, stderr: &str) -> String {
    if let Some(r) = result {
        let error = match &r["error"] {
            Value::Object(e) => [e.get("message"), e.get("type")].into_iter().flatten().find(|v| crate::util::truthy(Some(v))).cloned().unwrap_or(Value::Null),
            other => other.clone(),
        };
        if crate::util::truthy(Some(&error)) {
            return text_of(&error);
        }
        if crate::util::truthy(r.get("status")) && r["status"] != "success" {
            return text_of(&r["status"]);
        }
    }
    if !stderr.is_empty() {
        return stderr.into();
    }
    format!("gemini exited {rc}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    /// A `gemini` that logs its argv, then plays `$FIXTURE` once stdin closes, or, for a `--resume` of a session
    /// it was told is gone (`$GONE`), fails as the CLI does before it calls the model.
    fn fake_gemini(dir: &Path) -> String {
        use std::os::unix::fs::PermissionsExt;
        let path = dir.join("gemini");
        std::fs::write(
            &path,
            "#!/bin/sh\necho \"$@\" >> \"$ARGS_LOG\"\ncat > \"$ARGS_LOG.prompt\"\ncase \" $* \" in *\" --resume $GONE \"*) echo \"Error resuming session: Invalid session identifier \\\"$GONE\\\".\" >&2; exit 1;; esac\ncat \"$FIXTURE\"\n",
        )
        .unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        path.display().to_string()
    }

    async fn turn_events(rx: &mut mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
        let mut got = vec![];
        while let Ok(Some(mut ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            ev.as_object_mut().unwrap().retain(|k, _| k != "thread_id" && k != "turn_id");
            got.push(ev);
            if idle {
                break;
            }
        }
        got
    }

    fn settings(dir: &Path, fixture: &Path) -> Settings {
        let mut s = Settings { cwd: dir.display().to_string(), model: Some("gemini-3.8-flash".into()), effort: Some("low".into()), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin".to_string()), ("GEMINI_API_KEY", "test-key".into()), ("ARGS_LOG", dir.join("args.log").display().to_string()), ("FIXTURE", fixture.display().to_string())] {
            s.env.insert(k.into(), v);
        }
        s
    }

    #[tokio::test]
    async fn every_gemini_fixture_gives_the_python_hosts_events() {
        let dir = std::env::temp_dir().join(format!("fake-gemini-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake_gemini(&dir);
        for (path, expected) in crate::hosts::fixtures::cases("gemini") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = GeminiHost::new("acme", &dir.join("home"), vec![cmd.clone()], tx);
            host.start();
            let thread = host.start_thread(&settings(&dir, &path));
            host.start_turn(&thread, "hello", None).unwrap();
            assert_eq!(turn_events(&mut rx).await, expected, "{}", path.display());
            host.stop().await;
        }
        let written: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("home/settings.json")).unwrap()).unwrap();
        assert_eq!(written, settings_file("gemini-3.8-flash", Some("low"), &[]));
        let declared = [json!({"name": "linear", "transport": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_KEY}"}})];
        assert_eq!(settings_file("m", None, &declared)["mcpServers"], json!({"linear": {"httpUrl": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_KEY}"}}}));
        assert_eq!(std::fs::read_to_string(dir.join("args.log.prompt")).unwrap(), "hello");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_missing_saved_session_restarts_once_as_a_new_one() {
        let dir = std::env::temp_dir().join(format!("fake-gemini-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake_gemini(&dir);
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = GeminiHost::new("acme", &dir.join("home"), vec![cmd], tx);
        host.start();
        let mut s = settings(&dir, &crate::hosts::fixtures::dir("gemini").join("turn.jsonl"));
        s.env.insert("GONE".into(), "lost-session".into());
        let thread = host.resume_thread("lost-session", &s);
        host.start_turn(&thread, "hello", Some("medium")).unwrap();
        let events = turn_events(&mut rx).await;
        assert_eq!(events[1], json!({"kind": "diagnostic", "text": RESTORING}));
        assert!(events.iter().any(|e| e["kind"] == "turn_completed" && e["status"] == "completed"));
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        let runs: Vec<&str> = args.lines().collect();
        assert_eq!(runs[0], "--model gemini-3.8-flash --output-format stream-json --approval-mode yolo --skip-trust --resume lost-session -p ");
        assert!(runs[1].contains("--session-id lost-session -p"));
        host.stop().await;
        // No key, no turn.
        let (tx, _rx) = mpsc::unbounded_channel();
        let bare = GeminiHost::new("acme", &dir.join("home"), vec!["gemini".into()], tx);
        bare.start();
        s.env.remove("GEMINI_API_KEY");
        let thread = bare.start_thread(&s);
        assert_eq!(bare.start_turn(&thread, "x", None).unwrap_err().to_string(), "Gemini API key is not configured for this bot");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn efforts_usage_and_errors_follow_the_python_host() {
        assert_eq!((effort_for(Some(" Low ")), effort_for(None), effort_for(Some("xhigh"))), ("LOW", "HIGH", "HIGH"));
        assert_eq!(usage_tokens(&json!({"input_tokens": "12", "output_tokens": 3})), (12, 3, 15));
        assert_eq!(error_text(Some(&json!({"error": {"type": "quota"}})), 1, "x"), "quota");
        assert_eq!(error_text(Some(&json!({"status": "error"})), 1, ""), "error");
        assert_eq!(error_text(None, 2, ""), "gemini exited 2");
        assert!(uses_gemini_cli(&json!({"runtime": "gemini"})));
        assert!(!uses_gemini_cli(&json!({"runtime": "gemini", "harness": "antigravity"})));
        assert!(uses_gemini_cli(&json!({"runtime": "gemini", "harness": "antigravity", "fallback": {"harness": "gemini"}})));
    }
}
