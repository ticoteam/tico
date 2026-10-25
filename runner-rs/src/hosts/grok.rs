//! The Grok Build host: one `grok agent stdio` process per bot, speaking ACP (JSON-RPC 2.0
//! over stdio). Model and reasoning effort are options on `grok agent`, before the `stdio` subcommand:
//!
//!     grok agent -m grok-4.6 --reasoning-effort high --always-approve --no-leader stdio
//!
//! `session/prompt` is a long-running request whose response is the end of the turn, so it is sent without
//! waiting and its `stopReason` becomes `turn_completed` (or `turn_failed`). Turn ids are the runner's own.
//! The process starts with the first thread, once the bot's model, effort and environment are known; a thread
//! with another environment restarts it, as ACP sessions cannot set the tools' environment.

use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::ChildStdin;
use tokio::sync::oneshot;

use super::{event, failed, is_limit, Events, HostError, Settings};
use crate::env::Env;
use crate::util::{log, text_of, truthy};

/// The computer's existing Grok login stays in place, but sign-in and token refresh run as the bot user.
/// Earlier supervisor commands can have made this folder before any bot ran. Only hand over
/// the known default store; custom auth paths are never adopted with supervisor privileges.
pub fn hand_login_to_bot() {
    if let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty()) {
        crate::isolation::adopt_store(&[std::path::PathBuf::from(home).join(".grok")]);
    }
}

/// A default home created after startup must also allow API-key turns to save their sessions.
/// Match the effective home, but never let a profile or custom auth path choose a privileged handoff target.
pub fn prepare_home(env: &Env) {
    if std::env::var("HOME").ok().filter(|home| !home.is_empty()).is_some_and(|home| env.get("HOME") == Some(&home)) {
        hand_login_to_bot();
    }
}

/// Grok has no status subcommand. Its scoped auth store is the local sign-in; an empty store after logout is not.
/// Paths and entry fields follow xai-org/grok-build's xai-grok-login storage.rs and model.rs.
pub fn sign_in_detail(env: &Env) -> Option<&'static str> {
    let path = env.get("GROK_AUTH_PATH").filter(|v| !v.is_empty()).map(std::path::PathBuf::from).or_else(|| {
        env.get("GROK_HOME").filter(|v| !v.is_empty()).map(std::path::PathBuf::from)
            .or_else(|| env.get("HOME").map(|h| std::path::Path::new(h).join(".grok")))
            .map(|h| h.join("auth.json"))
    })?;
    // This runs in the supervisor's heartbeat path against a bot-writable store. No component may be a link the
    // bot planted (the fixed label would still answer "does this path parse"), opening a FIFO must not wait for a
    // writer, and devices or growing files must not supply an unbounded stream.
    let file = match crate::isolation::open_unfollowed(&path, libc::O_NONBLOCK) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(""),
        Err(_) => return None,
    };
    const MAX_AUTH_BYTES: u64 = 1024 * 1024;
    let metadata = file.metadata().ok()?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_AUTH_BYTES {
        return None;
    }
    // A second name for a file someone else owns (a supervisor file linked into the store) is not the store.
    use std::os::unix::fs::MetadataExt;
    let owner = crate::isolation::identity().map_or_else(|| unsafe { libc::geteuid() }, |(uid, _)| uid);
    if metadata.nlink() != 1 && metadata.uid() != owner {
        return None;
    }
    let mut text = String::new();
    // The descriptor can grow after metadata was checked; allow one extra byte to detect overflow.
    file.take(MAX_AUTH_BYTES + 1).read_to_string(&mut text).ok()?;
    if text.len() as u64 > MAX_AUTH_BYTES {
        return None;
    }
    if text.trim().is_empty() {
        return Some("");
    }
    let store = serde_json::from_str::<Value>(&text).ok()?;
    let entries = store.as_object()?;
    let modes = || entries.values().filter(|entry| entry["key"].as_str().is_some_and(|k| !k.trim().is_empty()))
        .filter_map(|entry| entry["auth_mode"].as_str());
    if modes().any(|mode| matches!(mode, "oidc" | "external")) {
        Some("Signed in with a Grok account")
    } else if modes().any(|mode| mode == "api_key") {
        Some("Signed in with an API key")
    } else {
        Some("")
    }
}

pub fn signed_in(env: &Env) -> Option<bool> {
    sign_in_detail(env).map(|detail| !detail.is_empty())
}

const PROTOCOL_VERSION: i64 = 1;
const REQUEST_TIMEOUT: u64 = 120;
pub const DEFAULT_MODEL: &str = "grok-4.6";
const DEFAULT_EFFORT: &str = "high";
/// The operator's own MCP servers Grok would pick up from other tools' files (~/.claude.json, ~/.cursor/mcp.json)
/// and from the xAI account: off, so a bot gets its declared servers. Grok has no switch for servers in its own
/// ~/.grok/config.toml or in plugins; those still load.
pub const NO_OPERATOR_MCP: &[(&str, &str)] = &[("GROK_CLAUDE_MCPS_ENABLED", "false"), ("GROK_CURSOR_MCPS_ENABLED", "false"), ("GROK_MANAGED_MCPS_ENABLED", "false")];

/// Grok 4.6's four reasoning levels; anything else is the provider default.
pub fn effort_for(effort: Option<&str>) -> &'static str {
    match effort.unwrap_or_default().trim().to_lowercase().as_str() {
        "low" => "low",
        "medium" => "medium",
        "xhigh" => "xhigh",
        _ => DEFAULT_EFFORT,
    }
}

/// `grok agent -m <model> --reasoning-effort <effort> --always-approve --no-leader stdio`.
pub fn argv(program: &str, model: Option<&str>, effort: Option<&str>) -> Vec<String> {
    let model = model.map(str::trim).filter(|m| !m.is_empty()).unwrap_or(DEFAULT_MODEL);
    [program, "agent", "-m", model, "--reasoning-effort", effort_for(effort), "--always-approve", "--no-leader", "stdio"].map(String::from).to_vec()
}

/// The answer to `session/request_permission`: Grok runs with --always-approve, so an allow option is chosen;
/// an unknown option shape is cancelled rather than guessed.
pub fn permission(params: &Value) -> Value {
    let options = params["options"].as_array().cloned().unwrap_or_default();
    let chosen = ["allow_always", "allow_once"].iter().find_map(|kind| options.iter().find(|o| o["kind"] == *kind));
    match chosen.filter(|o| truthy(o.get("optionId"))) {
        Some(o) => json!({"outcome": {"outcome": "selected", "optionId": o["optionId"]}}),
        None => json!({"outcome": {"outcome": "cancelled"}}),
    }
}

type Reply = Result<Value, Value>;

enum Slot {
    Wait(oneshot::Sender<Reply>),
    /// A `session/prompt`: its response ends the turn.
    Prompt { session: String, turn: String },
}

type Running = (Vec<String>, Env, super::Stopper, Arc<AtomicBool>);

struct Shared {
    bot: String,
    program: String,
    tx: Events,
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<i64, Slot>>,
    next_id: AtomicI64,
    /// The command and environment it runs with, and how to stop it.
    child: Mutex<Option<Running>>,
    turns: Mutex<HashMap<String, String>>,
    reply: Mutex<HashMap<String, String>>,
    boundary: Mutex<HashSet<String>>,
    /// The `messageId` of each turn's last text chunk.
    message: Mutex<HashMap<String, String>>,
    done_turns: Mutex<HashSet<String>>,
}

#[derive(Clone)]
pub struct GrokHost(Arc<Shared>);

impl GrokHost {
    pub fn new(bot: &str, program: &str, tx: Events) -> GrokHost {
        GrokHost(Arc::new(Shared {
            bot: bot.into(),
            program: program.into(),
            tx,
            stdin: tokio::sync::Mutex::new(None),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicI64::new(0),
            child: Mutex::new(None),
            turns: Mutex::new(HashMap::new()),
            reply: Mutex::new(HashMap::new()),
            boundary: Mutex::new(HashSet::new()),
            message: Mutex::new(HashMap::new()),
            done_turns: Mutex::new(HashSet::new()),
        }))
    }

    fn emit(&self, ev: Value) {
        let _ = self.0.tx.send(ev);
    }

    pub fn alive(&self) -> bool {
        self.0.child.lock().unwrap().as_ref().is_some_and(|(_, _, _, gone)| !gone.load(Ordering::SeqCst))
    }

    /// Start `grok agent` with `cmd` in `env` and say `initialize`.
    async fn launch(&self, cmd: Vec<String>, env: Env) -> Result<(), HostError> {
        let mut command = crate::isolation::command(&cmd[0]);
        let mut full = env.clone();
        full.extend(NO_OPERATOR_MCP.iter().map(|(k, v)| (k.to_string(), v.to_string())));
        command.args(&cmd[1..]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).env_clear().envs(&full);
        let mut child = command.spawn().map_err(|e| failed(format!("could not start grok agent: {e}")))?;
        let _stderr = crate::run_diagnostics::Stderr::capture(&mut child, self.0.tx.clone(), &full);
        let stdout = child.stdout.take().ok_or_else(|| failed("grok agent has no stdout"))?;
        *self.0.stdin.lock().await = child.stdin.take();
        log(&format!("grok[{}]: agent started pid {}", self.0.bot, child.id().map_or("?".into(), |p| p.to_string())));
        let (stopper, exited) = super::own(child);
        let gone = Arc::new(AtomicBool::new(false));
        let flag = gone.clone();
        let diagnostics = self.0.tx.clone();
        tokio::spawn(async move {
            let status = exited.await.unwrap_or_else(|_| Err(std::io::Error::other("grok reaper ended")));
            _stderr.drained().await;
            let code = super::return_code(&status);
            let _ = diagnostics.send(json!({"kind": "error", "error": crate::run_diagnostics::process_reason(code, &_stderr.tail()), "exit_code": code, "process_exit": true}));
            flag.store(true, Ordering::SeqCst);
        });
        *self.0.child.lock().unwrap() = Some((cmd, env, stopper, gone.clone()));
        let host = self.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if let Ok(msg) = serde_json::from_str::<Value>(line.trim()) {
                    host.on_message(msg).await;
                }
            }
            gone.store(true, Ordering::SeqCst);
            let slots: Vec<Slot> = host.0.pending.lock().unwrap().drain().map(|(_, s)| s).collect();
            for slot in slots {
                host.answer(slot, Err(json!({"message": "grok agent exited"})));
            }
        });
        self.request("initialize", json!({"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {"fs": {"readTextFile": false, "writeTextFile": false}}})).await?;
        Ok(())
    }

    /// Nothing runs until the first thread says which model, effort and environment the bot uses.
    pub fn start(&self) {}

    pub async fn stop(&self) {
        drop(self.0.stdin.lock().await.take());
        let child = self.0.child.lock().unwrap().take();
        if let Some((_, _, stopper, gone)) = child {
            stopper.terminate(Duration::from_secs(5));
            for _ in 0..60 {
                if gone.load(Ordering::SeqCst) {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }
    }

    /// The process for these settings: started, or restarted when the bot's command or environment changed.
    async fn configure(&self, settings: &Settings) -> Result<(), HostError> {
        let cmd = argv(&self.0.program, settings.model.as_deref(), settings.effort.as_deref());
        let current = self.0.child.lock().unwrap().as_ref().map(|(c, e, _, _)| (c.clone(), e.clone()));
        let env = if !settings.env.is_empty() { settings.env.clone() } else { current.as_ref().map(|(_, e)| e.clone()).unwrap_or_else(crate::env::process_environment) };
        // A supervisor-owned store can appear while the agent stays alive (including API-key sessions).
        // Repair before every new or resumed thread, even when the process can be reused.
        prepare_home(&env);
        if self.alive() && current.as_ref().is_some_and(|(c, e)| *c == cmd && *e == env) {
            return Ok(());
        }
        if current.is_some() {
            if !self.0.turns.lock().unwrap().is_empty() {
                return Err(failed("cannot change the Grok tool environment during a turn"));
            }
            self.stop().await;
        }
        self.launch(cmd, env).await
    }

    async fn write(&self, msg: &Value) -> Result<(), HostError> {
        if !self.alive() {
            return Err(failed("grok agent is not running"));
        }
        let line = msg.to_string() + "\n";
        let mut stdin = self.0.stdin.lock().await;
        let Some(s) = stdin.as_mut() else { return Err(failed("grok agent stdin closed")) };
        async {
            s.write_all(line.as_bytes()).await?;
            s.flush().await
        }
        .await
        .map_err(|e| failed(format!("grok agent stdin closed: {e}")))
    }

    async fn send(&self, method: &str, params: Value, slot: Slot) -> Result<i64, HostError> {
        let id = self.0.next_id.fetch_add(1, Ordering::SeqCst) + 1;
        self.0.pending.lock().unwrap().insert(id, slot);
        if let Err(e) = self.write(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})).await {
            self.0.pending.lock().unwrap().remove(&id);
            return Err(e);
        }
        Ok(id)
    }

    async fn request(&self, method: &str, params: Value) -> Result<Value, HostError> {
        let (tx, rx) = oneshot::channel();
        let id = self.send(method, params, Slot::Wait(tx)).await?;
        match tokio::time::timeout(Duration::from_secs(REQUEST_TIMEOUT), rx).await {
            Err(_) => {
                self.0.pending.lock().unwrap().remove(&id);
                Err(failed(format!("{method} timed out after {REQUEST_TIMEOUT}s")))
            }
            Ok(Err(_)) => Err(failed(format!("{method}: grok agent exited"))),
            Ok(Ok(Ok(result))) => Ok(result),
            Ok(Ok(Err(err))) => Err(failed(format!("{method}: {}", error_text(&err)))),
        }
    }

    fn answer(&self, slot: Slot, reply: Reply) {
        match slot {
            Slot::Wait(tx) => {
                let _ = tx.send(reply);
            }
            Slot::Prompt { session, turn } => self.finish(&session, &turn, reply),
        }
    }

    async fn on_message(&self, msg: Value) {
        let id = msg.get("id").filter(|i| !i.is_null()).cloned();
        match (id, msg.get("method").and_then(|m| m.as_str())) {
            (Some(id), None) => {
                if let Some(slot) = id.as_i64().and_then(|i| self.0.pending.lock().unwrap().remove(&i)) {
                    self.answer(slot, match msg.get("error") {
                        Some(e) => Err(e.clone()),
                        None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
                    });
                }
            }
            (Some(id), Some(method)) => {
                // The agent asks its ACP client something.
                let reply = if method == "session/request_permission" {
                    json!({"jsonrpc": "2.0", "id": id, "result": permission(&msg["params"])})
                } else {
                    json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "unsupported client request"}})
                };
                let _ = self.write(&reply).await;
            }
            (None, method) => {
                let p = msg.get("params").filter(|p| p.is_object()).cloned().unwrap_or(json!({}));
                self.on_notification(method.unwrap_or_default(), &p);
            }
        }
    }

    fn on_notification(&self, method: &str, p: &Value) {
        if method != "session/update" {
            return; // _x.ai/* announcements are informational
        }
        let sid = text_of(&p["sessionId"]);
        let Some(turn) = self.0.turns.lock().unwrap().get(&sid).cloned() else { return };
        let u = &p["update"];
        let text = if u["content"].is_object() { u["content"].get("text").filter(|t| truthy(Some(t))).cloned() } else { None };
        let (s, t) = (Some(sid.as_str()), Some(turn.as_str()));
        match (u["sessionUpdate"].as_str().unwrap_or_default(), text) {
            ("agent_message_chunk", Some(text)) => {
                let piece = {
                    let mut reply = self.0.reply.lock().unwrap();
                    let r = reply.entry(turn.clone()).or_default();
                    if self.0.boundary.lock().unwrap().remove(&turn) {
                        r.clear();
                    }
                    // A chunk names its message: one of another message than the chunk before it starts a new one.
                    let id = u["messageId"].as_str().filter(|i| !i.is_empty());
                    let new_message = id.is_some_and(|id| self.0.message.lock().unwrap().insert(turn.clone(), id.into()).is_some_and(|last| last != id));
                    super::add_piece(r, new_message, &text_of(&text))
                };
                self.emit(event("delta", s, t, json!({"text": piece, "delta_kind": "text"})));
            }
            ("tool_call", _) => {
                // A new tool call separates progress from the answer that follows; the call is reported by its
                // kind (read, execute, ...), never its title or input.
                self.0.boundary.lock().unwrap().insert(turn.clone());
                let kind = u.get("kind").filter(|k| truthy(Some(k))).map(text_of).unwrap_or_else(|| "tool".into());
                self.emit(event("tool", s, t, json!({"tool": kind, "status": "started", "item_id": u.get("toolCallId").cloned().unwrap_or(Value::Null)})));
            }
            ("agent_thought_chunk", Some(text)) => {
                self.emit(event("delta", s, t, json!({"text": text, "delta_kind": "thought"})));
            }
            _ => {}
        }
    }

    fn finish(&self, sid: &str, turn: &str, reply: Reply) {
        if !self.0.done_turns.lock().unwrap().insert(turn.into()) {
            return;
        }
        self.0.turns.lock().unwrap().remove(sid);
        let text = self.0.reply.lock().unwrap().remove(turn).unwrap_or_default();
        self.0.boundary.lock().unwrap().remove(turn);
        self.0.message.lock().unwrap().remove(turn);
        let (s, t) = (Some(sid), Some(turn));
        let result = match reply {
            Err(err) => {
                let msg = error_text(&err);
                self.emit(event("turn_failed", s, t, json!({"error": msg, "limit": is_limit(&msg)})));
                return;
            }
            Ok(result) => result,
        };
        let stop = result.get("stopReason").filter(|v| truthy(Some(v))).map(text_of).unwrap_or_else(|| "end_turn".into());
        if !text.is_empty() && stop != "cancelled" {
            self.emit(event("message", s, t, json!({"text": text, "final": true})));
        }
        if stop == "refusal" || stop == "error" {
            self.emit(event("turn_failed", s, t, json!({"error": format!("stopReason {stop}"), "limit": false})));
        } else {
            let status = if stop == "cancelled" { "interrupted" } else { "completed" };
            self.emit(event("turn_completed", s, t, json!({"status": status, "stop_reason": stop})));
        }
        self.emit(event("status", s, None, json!({"state": "idle"})));
    }

    fn servers(settings: &Settings) -> Value {
        json!(crate::mcp::grok_config(&settings.mcp_servers, &settings.env))
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        self.configure(settings).await?;
        let res = self.request("session/new", json!({"cwd": settings.cwd, "mcpServers": Self::servers(settings)})).await?;
        match res["sessionId"].as_str().filter(|s| !s.is_empty()) {
            Some(sid) => Ok(sid.into()),
            None => Err(failed(format!("session/new for {bot} returned no session id"))),
        }
    }

    pub async fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        self.configure(settings).await?;
        self.request("session/load", json!({"sessionId": thread_id, "cwd": settings.cwd, "mcpServers": Self::servers(settings)})).await?;
        Ok(thread_id.into())
    }

    pub async fn start_turn(&self, thread_id: &str, text: &str) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        self.0.turns.lock().unwrap().insert(thread_id.into(), turn.clone());
        self.0.reply.lock().unwrap().insert(turn.clone(), String::new());
        let slot = Slot::Prompt { session: thread_id.into(), turn: turn.clone() };
        self.send("session/prompt", json!({"sessionId": thread_id, "prompt": [{"type": "text", "text": text}]}), slot).await?;
        Ok(turn)
    }

    pub async fn interrupt(&self, thread_id: &str) -> Result<(), HostError> {
        self.write(&json!({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": thread_id}})).await
    }
}

fn error_text(err: &Value) -> String {
    err.get("message").filter(|m| truthy(Some(m))).map(text_of).unwrap_or_else(|| text_of(err))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    #[test]
    fn auth_status_accepts_only_bounded_valid_stores() {
        let root = std::env::temp_dir().join(format!("grok-auth-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join(".grok")).unwrap();
        let env = Env::from([("HOME".into(), root.display().to_string())]);
        let path = root.join(".grok/auth.json");
        assert_eq!(sign_in_detail(&env), Some(""));
        for (text, expected) in [
            ("", Some("")),
            ("{}", Some("")),
            (r#"{"acme":{"auth_mode":"oidc","key":"synthetic"}}"#, Some("Signed in with a Grok account")),
            (r#"{"acme":{"auth_mode":"api_key","key":"synthetic"}}"#, Some("Signed in with an API key")),
            ("broken", None),
        ] {
            std::fs::write(&path, text).unwrap();
            assert_eq!(sign_in_detail(&env), expected);
        }
        // A valid JSON store padded to exactly the documented 1 MiB limit still works.
        let mut text = "{}".to_string();
        text.extend(std::iter::repeat_n(' ', 1_048_576 - text.len()));
        std::fs::write(&path, &text).unwrap();
        assert_eq!(sign_in_detail(&env), Some(""));
        text.push(' ');
        std::fs::write(&path, text).unwrap();
        assert_eq!(sign_in_detail(&env), None);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn auth_status_rejects_special_files_without_blocking() {
        const CHILD_HOME: &str = "TICO_TEST_GROK_SPECIAL_AUTH_HOME";
        if let Some(root) = std::env::var_os(CHILD_HOME) {
            use std::os::unix::{ffi::OsStrExt, fs::symlink};
            let root = std::path::PathBuf::from(root);
            let path = root.join(".grok/auth.json");
            let env = Env::from([("HOME".into(), root.display().to_string())]);
            let cpath = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
            assert_eq!(sign_in_detail(&env), None, "a FIFO has no sign-in status");
            std::fs::remove_file(&path).unwrap();
            std::fs::create_dir(&path).unwrap();
            assert_eq!(sign_in_detail(&env), None);
            std::fs::remove_dir(&path).unwrap();
            let target = root.join("synthetic.json");
            std::fs::write(&target, r#"{"acme":{"auth_mode":"oidc","key":"synthetic"}}"#).unwrap();
            symlink(&target, &path).unwrap();
            assert_eq!(sign_in_detail(&env), None, "do not follow a regular-file link");
            std::fs::remove_file(&path).unwrap();
            symlink("/dev/zero", &path).unwrap();
            assert_eq!(sign_in_detail(&env), None, "do not follow a device link");
            let device_env = Env::from([("GROK_AUTH_PATH".into(), "/dev/zero".into())]);
            assert_eq!(sign_in_detail(&device_env), None, "reject a direct device override too");
            return;
        }
        // Run the synchronous probe in a child so a regression can be killed without stranding
        // the test runner on a blocking open. Only synthetic files in this directory are read.
        let root = std::env::temp_dir().join(format!("grok-special-auth-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join(".grok")).unwrap();
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "hosts::grok::tests::auth_status_rejects_special_files_without_blocking", "--nocapture"])
            .env(CHILD_HOME, &root).spawn().unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let result = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break Some(status);
            }
            if std::time::Instant::now() >= deadline {
                child.kill().unwrap();
                child.wait().unwrap();
                break None;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        std::fs::remove_dir_all(root).unwrap();
        assert!(result.is_some_and(|status| status.success()), "credential status blocked or accepted a special file: {result:?}");
    }

    #[test]
    fn auth_status_never_follows_a_linked_ancestor() {
        // A link a root test run makes is a trusted system alias, so only an ordinary user can plant one here.
        if unsafe { libc::geteuid() } == 0 { return; }
        let root = std::env::temp_dir().join(format!("grok-linked-auth-{}", uuid::Uuid::new_v4()));
        let elsewhere = root.join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        std::fs::write(elsewhere.join("auth.json"), r#"{"acme":{"auth_mode":"oidc","key":"synthetic"}}"#).unwrap();
        let real = Env::from([("GROK_HOME".into(), elsewhere.display().to_string())]);
        assert_eq!(sign_in_detail(&real), Some("Signed in with a Grok account"), "the real folder still reads");
        std::os::unix::fs::symlink(&elsewhere, root.join(".grok")).unwrap();
        let env = Env::from([("HOME".into(), root.display().to_string())]);
        assert_eq!(sign_in_detail(&env), None, "a planted .grok link is not followed");
        let env = Env::from([("GROK_AUTH_PATH".into(), root.join(".grok/auth.json").display().to_string())]);
        assert_eq!(sign_in_detail(&env), None, "nor through a direct path");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn every_grok_fixture_gives_the_python_hosts_events() {
        // The agent's side of one turn on session `sess-1`, whose `session/prompt` was request 3.
        for (path, expected) in crate::hosts::fixtures::cases("grok") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = GrokHost::new("acme", "grok", tx);
            host.0.turns.lock().unwrap().insert("sess-1".into(), "turn-1".into());
            host.0.pending.lock().unwrap().insert(3, Slot::Prompt { session: "sess-1".into(), turn: "turn-1".into() });
            for line in std::fs::read_to_string(&path).unwrap().lines().filter(|l| !l.trim().is_empty()) {
                host.on_message(serde_json::from_str(line).unwrap()).await;
            }
            let mut got = vec![];
            while let Ok(mut ev) = rx.try_recv() {
                ev.as_object_mut().unwrap().retain(|k, _| k != "thread_id" && k != "turn_id");
                got.push(ev);
            }
            assert_eq!(got, expected, "{}", path.display());
        }
    }

    #[test]
    fn the_command_line_and_permission_answers_follow_the_python_host() {
        assert_eq!(argv("grok", None, Some("XHigh")).join(" "), "grok agent -m grok-4.6 --reasoning-effort xhigh --always-approve --no-leader stdio");
        assert_eq!(argv("grok", Some("grok-5"), Some("max"))[3..6], ["grok-5", "--reasoning-effort", "high"]);
        let options = json!({"options": [{"kind": "allow_once", "optionId": "once"}, {"kind": "allow_always", "optionId": "always"}]});
        assert_eq!(permission(&options), json!({"outcome": {"outcome": "selected", "optionId": "always"}}));
        assert_eq!(permission(&json!({"options": [{"kind": "reject_once", "optionId": "no"}]})), json!({"outcome": {"outcome": "cancelled"}}));
    }
}
