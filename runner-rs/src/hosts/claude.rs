//! The Claude Code host (`runner/hosts/claude.py`): one `claude -p` process per turn, one Claude session per
//! bot thread. The prompt goes in on stdin as a stream-json user message and stdin stays open for steers;
//! `--replay-user-messages` echoes each message as Claude reads it, so a `result` ends the runner's turn only
//! when no steer is unread, and then stdin closes and the process exits.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Seek, SeekFrom};
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Child;
use tokio::sync::mpsc;
use tokio::time::Instant;

use super::{event, failed, hub_mcp_server, is_auth_retryable, is_limit, Events, HostError, Settings};
use crate::util::{iso, log, py_dumps, text_of};

pub const STREAM_ARGS: &[&str] = &[
    "--input-format", "stream-json", "--replay-user-messages", "--output-format", "stream-json", "--verbose",
    "--include-partial-messages", "--permission-mode", "bypassPermissions",
];
const EFFORTS: &[&str] = &["low", "medium", "high", "xhigh", "max"];
const STOP_TIMEOUT: Duration = Duration::from_secs(10);
const STDERR_TAIL: usize = 2000;
/// How long a turn waits, after a `result`, with a steer unread and nothing at all on stdout.
pub const STEER_READ_TIMEOUT_S: u64 = 120;
const STEER_MIN_VERSION: (u64, u64, u64) = (2, 1, 296);
const AUTOCOMPACT_BUFFER: i64 = 33_000;
const AUTOCOMPACT_RANGE: (i64, i64) = (100_000, 1_000_000);
pub const RESTORING: &str = "Saved provider session is unavailable; restoring this conversation's context.";

pub fn version_tuple(text: &str) -> Option<(u64, u64, u64)> {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| regex::Regex::new(r"(\d+)\.(\d+)\.(\d+)").unwrap());
    let c = r.captures(text)?;
    Some((c[1].parse().ok()?, c[2].parse().ok()?, c[3].parse().ok()?))
}

/// `claude --version` (or `--help`); "" when it cannot be read.
pub async fn installed_version(cmd: &[String], flag: &str) -> String {
    let run = crate::isolation::command(&cmd[0]).args(&cmd[1..]).arg(flag).stdin(Stdio::null()).kill_on_drop(true).output();
    match tokio::time::timeout(Duration::from_secs(5), run).await {
        Ok(Ok(out)) if out.status.success() => {
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            let text = if stdout.is_empty() { String::from_utf8_lossy(&out.stderr).to_string() } else { stdout };
            text.trim().to_string()
        }
        _ => String::new(),
    }
}

pub fn autocompact_window(value: &Value) -> i64 {
    (super::compact_at(value) + AUTOCOMPACT_BUFFER).clamp(AUTOCOMPACT_RANGE.0, AUTOCOMPACT_RANGE.1)
}

pub fn effort_for(effort: Option<&str>) -> Option<String> {
    let e = effort.unwrap_or_default().trim().to_lowercase();
    EFFORTS.contains(&e.as_str()).then_some(e)
}

/// `--model`, or None for Claude's default: the registry's defaults name a Codex model Claude would refuse.
pub fn model_for(model: Option<&str>) -> Option<String> {
    let m = model.unwrap_or_default().trim();
    if m.is_empty() || m == "default" || m.to_lowercase().starts_with("gpt-") {
        return None;
    }
    Some(m.to_string())
}

fn count(u: &Value, key: &str) -> i64 {
    match &u[key] {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => *b as i64,
        _ => 0,
    }
}

/// (input, output) from a Claude usage object; cached prompt tokens count as input.
pub fn usage_tokens(usage: &Value) -> (i64, i64) {
    let inp = count(usage, "input_tokens") + count(usage, "cache_creation_input_tokens") + count(usage, "cache_read_input_tokens");
    (inp, count(usage, "output_tokens"))
}

pub fn user_line(mid: &str, text: &str) -> String {
    py_dumps(&json!({"type": "user", "uuid": mid, "message": {"role": "user", "content": [{"type": "text", "text": text}]}})) + "\n"
}

#[derive(Clone)]
pub(crate) struct Thread {
    settings: Settings,
    started: bool,
    fork_from: Option<String>,
    session_id: Option<String>,
}

/// One process's stdin: what is still to write, whether it takes more, and which messages Claude has not read.
struct Pipe {
    lines: mpsc::UnboundedSender<Option<String>>,
    open: bool,
    first: String,
    unread: HashSet<String>,
    order: Vec<String>,
    steered: bool,
    cost: f64,
    awaiting: Option<Instant>,
    timed_out: bool,
    stopper: super::Stopper,
}

impl Pipe {
    fn close(&mut self) {
        if self.open {
            self.open = false;
            let _ = self.lines.send(None);
        }
    }
}

#[derive(Default)]
struct Usage {
    input: i64,
    output: i64,
    cached: i64,
    cost: Option<f64>,
    texts: Vec<String>,
}

#[derive(Default)]
struct Inner {
    up: bool,
    threads: HashMap<String, Thread>,
    turn: Option<(String, String)>,
    pipe: Option<Pipe>,
    reply: HashMap<String, String>,
    last_message: HashMap<String, (Value, String)>,
    limited: HashSet<String>,
    usage: HashMap<String, Usage>,
    interrupted: HashSet<String>,
    done_turns: HashSet<String>,
    replay_fallback_logged: bool,
    reader: Option<tokio::task::JoinHandle<()>>,
    /// How far each thread's transcript has been read for goal records.
    goal_offsets: HashMap<String, u64>,
}

struct Shared {
    bot: String,
    cmd: Vec<String>,
    tx: Events,
    steer: std::sync::atomic::AtomicBool,
    autocompact: std::sync::atomic::AtomicBool,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct ClaudeHost(Arc<Shared>);

#[allow(clippy::type_complexity)]
fn cache() -> &'static Mutex<HashMap<(Vec<String>, &'static str), bool>> {
    #[allow(clippy::type_complexity)]
    static C: OnceLock<Mutex<HashMap<(Vec<String>, &'static str), bool>>> = OnceLock::new();
    C.get_or_init(|| Mutex::new(HashMap::new()))
}

impl ClaudeHost {
    pub fn new(bot: &str, cmd: Vec<String>, tx: Events) -> ClaudeHost {
        ClaudeHost(Arc::new(Shared {
            bot: bot.into(),
            cmd,
            tx,
            steer: false.into(),
            autocompact: false.into(),
            inner: Mutex::new(Inner::default()),
        }))
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn emit(&self, ev: Value) {
        let _ = self.0.tx.send(ev);
    }

    pub fn supports_steer(&self) -> bool {
        self.0.steer.load(std::sync::atomic::Ordering::Relaxed)
    }

    /// Steering needs a Claude Code that replays its input, and `--autocompact` one whose `--help` lists it;
    /// each is read once per command for the runner's life.
    pub async fn start(&self) -> Result<(), HostError> {
        let key = self.0.cmd.clone();
        let known = cache().lock().unwrap().get(&(key.clone(), "steer")).copied();
        let steer = match known {
            Some(v) => v,
            None => {
                let v = version_tuple(&installed_version(&key, "--version").await).unwrap_or((0, 0, 0)) >= STEER_MIN_VERSION;
                cache().lock().unwrap().insert((key.clone(), "steer"), v);
                v
            }
        };
        let known = cache().lock().unwrap().get(&(key.clone(), "autocompact")).copied();
        let auto = match known {
            Some(v) => v,
            None => {
                let v = installed_version(&key, "--help").await.contains("--autocompact");
                cache().lock().unwrap().insert((key, "autocompact"), v);
                v
            }
        };
        self.0.steer.store(steer, std::sync::atomic::Ordering::Relaxed);
        self.0.autocompact.store(auto, std::sync::atomic::Ordering::Relaxed);
        self.inner().up = true;
        Ok(())
    }

    pub async fn stop(&self) {
        let reader = {
            let mut inner = self.inner();
            inner.up = false;
            if let Some((_, turn)) = inner.turn.clone() {
                inner.interrupted.insert(turn);
            }
            if let Some(p) = inner.pipe.as_mut() {
                p.close();
                p.stopper.terminate(STOP_TIMEOUT);
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
        self.inner().threads.insert(id.clone(), Thread { settings: settings.clone(), started: false, fork_from: None, session_id: None });
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> String {
        self.inner().threads.insert(thread_id.into(), Thread { settings: settings.clone(), started: true, fork_from: None, session_id: None });
        thread_id.into()
    }

    pub fn session_id(&self, thread_id: &str) -> String {
        self.inner().threads.get(thread_id).and_then(|t| t.session_id.clone()).unwrap_or_else(|| thread_id.to_string())
    }

    pub fn argv(&self, thread_id: &str, t: &Thread, effort: Option<&str>) -> Vec<String> {
        let mut argv = self.0.cmd.clone();
        argv.push("-p".into());
        argv.extend(STREAM_ARGS.iter().map(|s| s.to_string()));
        let mut servers = crate::mcp::claude_config(&t.settings.mcp_servers);
        if let Some(hub) = hub_mcp_server(&t.settings.env) {
            servers.insert("hub".into(), hub);
        }
        // Only the hub, the bot's declared servers and the bot repo's own `.mcp.json`, for every bot:
        // `--strict-mcp-config` keeps out the operator's own servers (~/.claude.json, user settings, plugins,
        // claude.ai connectors) (hosts/claude.py _argv).
        if t.settings.shared {
            argv.extend(["--setting-sources".into(), "project,local".into()]);
        }
        argv.push("--strict-mcp-config".into());
        let project = std::fs::read_to_string(std::path::Path::new(&t.settings.cwd).join(".mcp.json"))
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .and_then(|v| v.get("mcpServers").and_then(Value::as_object).cloned())
            .unwrap_or_default();
        let mut merged = project;
        merged.extend(servers);
        servers = merged;
        if !servers.is_empty() {
            argv.push("--mcp-config".into());
            argv.push(py_dumps(&json!({"mcpServers": servers})));
        }
        // The task's worktrees in other repos (sessions.rs repo rules), each readable and writable by the session.
        for directory in &t.settings.add_dirs {
            argv.extend(["--add-dir".into(), directory.clone()]);
        }
        if let Some(m) = model_for(t.settings.model.as_deref()) {
            argv.extend(["--model".into(), m]);
        }
        if let Some(e) = effort_for(effort.or(t.settings.effort.as_deref())) {
            argv.extend(["--effort".into(), e]);
        }
        if self.0.autocompact.load(std::sync::atomic::Ordering::Relaxed) {
            argv.extend(["--autocompact".into(), autocompact_window(&t.settings.compact_at).to_string()]);
        }
        if let Some(from) = &t.fork_from {
            argv.extend(["--resume".into(), from.clone(), "--fork-session".into(), "--session-id".into(), thread_id.into()]);
        } else if t.started {
            argv.extend(["--resume".into(), t.session_id.clone().unwrap_or_else(|| thread_id.into())]);
        } else {
            argv.extend(["--session-id".into(), thread_id.into()]);
        }
        argv
    }

    fn launch(&self, thread_id: &str, t: &Thread, effort: Option<&str>) -> std::io::Result<(Child, std::fs::File)> {
        let argv = self.argv(thread_id, t, effort);
        // Claude's stderr goes to an unlinked file: read back for the error text, never a pipe that could fill.
        let path = std::env::temp_dir().join(format!("tico-claude-stderr-{}", uuid::Uuid::new_v4().simple()));
        let stderr = std::fs::OpenOptions::new().read(true).write(true).create_new(true).open(&path)?;
        let _ = std::fs::remove_file(&path);
        let mut command = crate::isolation::command(&argv[0]);
        command
            .args(&argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(stderr.try_clone()?))
            .current_dir(&t.settings.cwd);
        command.env_clear().envs(turn_env(&t.settings));
        Ok((command.spawn()?, stderr))
    }

    pub fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let (child, stderr) = {
            let mut inner = self.inner();
            if !inner.up {
                return Err(failed("claude host is not running"));
            }
            if inner.turn.is_some() {
                return Err(failed("claude host already has a turn running"));
            }
            let t = inner.threads.get(thread_id).cloned().ok_or_else(|| failed(format!("unknown claude thread {thread_id}")))?;
            if let Some(size) = transcript(&t.settings, &session_of(thread_id, &t)).and_then(|p| std::fs::metadata(p).ok()).map(|m| m.len()) {
                inner.goal_offsets.insert(thread_id.into(), size);
            }
            let launched = self.launch(thread_id, &t, effort).map_err(|e| failed(format!("could not start claude: {e}")))?;
            inner.turn = Some((thread_id.into(), turn.clone()));
            inner.reply.insert(turn.clone(), String::new());
            launched
        };
        log(&format!("claude[{}]: turn started pid {}", self.0.bot, child.id().map_or("?".into(), |p| p.to_string())));
        self.emit(event("status", Some(thread_id), None, json!({"state": "active"})));
        let host = self.clone();
        let (tid, t2, text, effort) = (thread_id.to_string(), turn.clone(), text.to_string(), effort.map(String::from));
        let reader = tokio::spawn(async move { host.read_loop(child, stderr, tid, t2, text, effort).await });
        self.inner().reader = Some(reader);
        Ok(turn)
    }

    fn open_pipe(&self, stdin: Option<tokio::process::ChildStdin>, stopper: super::Stopper, text: &str) {
        let (tx, mut rx) = mpsc::unbounded_channel::<Option<String>>();
        let first = uuid::Uuid::new_v4().to_string();
        let _ = tx.send(Some(user_line(&first, text)));
        let mut stdin = stdin;
        // Its own task: a prompt can be larger than the pipe's buffer, and nothing else waits on it.
        tokio::spawn(async move {
            while let Some(Some(line)) = rx.recv().await {
                let Some(s) = stdin.as_mut() else { break };
                if s.write_all(line.as_bytes()).await.is_err() || s.flush().await.is_err() {
                    break;
                }
            }
            drop(stdin.take());
        });
        self.inner().pipe = Some(Pipe {
            lines: tx,
            open: true,
            first: first.clone(),
            unread: HashSet::from([first.clone()]),
            order: vec![first],
            steered: false,
            cost: 0.0,
            awaiting: None,
            timed_out: false,
            stopper,
        });
    }

    async fn read_loop(&self, mut child: Child, mut stderr: std::fs::File, tid: String, turn: String, text: String, effort: Option<String>) {
        let mut recovered = false;
        loop {
            let stdout = child.stdout.take();
            let stdin = child.stdin.take();
            let (stopper, exited) = super::own(child);
            self.open_pipe(stdin, stopper, &text);
            let mut result: Option<Value> = None;
            let mut saw_event = false;
            if let Some(stdout) = stdout {
                let mut lines = BufReader::new(stdout).lines();
                loop {
                    let deadline = self.inner().pipe.as_ref().and_then(|p| p.awaiting);
                    let next = match deadline {
                        Some(d) => tokio::select! {
                            l = lines.next_line() => Some(l),
                            _ = tokio::time::sleep_until(d) => None,
                        },
                        None => Some(lines.next_line().await),
                    };
                    let line = match next {
                        None => {
                            let mut inner = self.inner();
                            if let Some(p) = inner.pipe.as_mut() {
                                if p.awaiting.is_some() && p.open && !p.unread.is_empty() {
                                    p.awaiting = None;
                                    p.timed_out = true;
                                    p.close();
                                    p.stopper.terminate(STOP_TIMEOUT); // the reader sees EOF and fails the turn
                                } else {
                                    p.awaiting = None;
                                }
                            }
                            continue;
                        }
                        Some(Ok(Some(line))) => line,
                        Some(_) => break,
                    };
                    {
                        let mut inner = self.inner();
                        if let Some(p) = inner.pipe.as_mut().filter(|p| p.awaiting.is_some()) {
                            p.awaiting = Some(Instant::now() + Duration::from_secs(STEER_READ_TIMEOUT_S));
                        }
                    }
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    let Ok(msg) = serde_json::from_str::<Value>(line) else { continue };
                    if msg["type"] == "user" && crate::util::truthy(msg.get("isReplay")) && !crate::util::truthy(msg.get("parent_tool_use_id")) {
                        self.replayed(msg.get("uuid").and_then(|u| u.as_str()));
                    } else if self.on_message(&msg, &tid, &turn) {
                        self.settle(&turn, &msg);
                        result = Some(msg);
                    } else {
                        saw_event = true;
                    }
                }
            }
            let (unread, steered, timed_out) = {
                let mut inner = self.inner();
                match inner.pipe.as_mut() {
                    Some(p) => {
                        p.close();
                        p.awaiting = None;
                        (p.unread.iter().any(|m| *m != p.first), p.steered, p.timed_out)
                    }
                    None => (false, false, false),
                }
            };
            let rc = match exited.await {
                Ok(status) => super::return_code(&status),
                Err(_) => -1,
            };
            let clean = result.as_ref().is_some_and(|r| !crate::util::truthy(r.get("is_error"))) && rc == 0;
            let failure = if timed_out {
                Some(format!("Claude did not read a follow-up message within {STEER_READ_TIMEOUT_S} s; it was not applied"))
            } else if unread && clean {
                Some("Claude exited before reading a follow-up message; it was not applied".to_string())
            } else {
                None
            };
            let tail = stderr_tail(&mut stderr);
            // Claude answers only with an error result when the session file is not on this computer: restart once
            // as a new session under the same id. Never after an event, and never once a steer went in.
            let missing = result.as_ref().is_some_and(|r| error_text(Some(r), rc, &tail).starts_with("No conversation found with session ID"));
            if missing && !recovered && !saw_event && !steered && rc != 0 {
                let retry = {
                    let mut inner = self.inner();
                    let up = inner.up && !inner.interrupted.contains(&turn);
                    match inner.threads.get_mut(&tid) {
                        Some(t) if up && (t.started || t.fork_from.is_some()) => {
                            t.started = false;
                            t.fork_from = None;
                            t.session_id = None;
                            let t = t.clone();
                            self.launch(&tid, &t, effort.as_deref()).ok()
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
            self.finish(&tid, &turn, result, rc, &tail, failure);
            return;
        }
    }

    /// Claude read message `mid`; one missing or not written here stands for the oldest unread message.
    fn replayed(&self, mid: Option<&str>) {
        let mut inner = self.inner();
        let mut log_fallback = false;
        if let Some(p) = inner.pipe.as_mut() {
            let mid = match mid.filter(|m| p.order.iter().any(|o| o == m)) {
                Some(m) => Some(m.to_string()),
                None => {
                    log_fallback = true;
                    p.order.iter().find(|m| p.unread.contains(*m)).cloned()
                }
            };
            if let Some(m) = mid {
                p.unread.remove(&m);
            }
            if p.awaiting.is_some() && p.unread.iter().all(|m| *m == p.first) {
                p.awaiting = None; // read: its own result follows
            }
        }
        if log_fallback && !inner.replay_fallback_logged {
            inner.replay_fallback_logged = true;
            drop(inner);
            log(&format!("claude[{}]: a replayed message did not carry the uuid written; counting replays in order instead", self.0.bot));
        }
    }

    /// A `result` arrived: the turn is over when Claude read every message written to it.
    fn settle(&self, turn: &str, result: &Value) {
        let (inp, out) = usage_tokens(&result["usage"]);
        let mut inner = self.inner();
        let cost = result["total_cost_usd"].as_f64();
        let text = result["result"].as_str().unwrap_or_default().to_string();
        let mut pipe_cost = inner.pipe.as_ref().map_or(0.0, |p| p.cost);
        let u = inner.usage.entry(turn.into()).or_default();
        u.input += inp;
        u.output += out;
        u.cached += count(&result["usage"], "cache_read_input_tokens");
        if let Some(c) = cost {
            u.cost = Some(u.cost.unwrap_or(0.0) + (c - pipe_cost).max(0.0));
            pipe_cost = c;
        }
        if !text.is_empty() && u.texts.last() != Some(&text) {
            u.texts.push(text);
        }
        if let Some(p) = inner.pipe.as_mut() {
            p.cost = pipe_cost;
            let first = p.first.clone();
            p.unread.remove(&first);
            if crate::util::truthy(result.get("is_error")) || p.unread.is_empty() {
                p.close();
            } else {
                p.awaiting = Some(Instant::now() + Duration::from_secs(STEER_READ_TIMEOUT_S));
            }
        }
    }

    /// One stream-json line to host events; true when it is the turn's result.
    fn on_message(&self, msg: &Value, tid: &str, turn: &str) -> bool {
        if !msg.is_object() || crate::util::truthy(msg.get("parent_tool_use_id")) {
            return false; // subagent traffic is not the bot's reply
        }
        if let Some(status) = crate::goals::claude_status(msg) {
            self.emit(event("goal", Some(tid), None, Value::Object(status)));
            return false;
        }
        match msg["type"].as_str().unwrap_or_default() {
            "stream_event" => {
                let ev = &msg["event"];
                let delta = &ev["delta"];
                if ev["type"] == "content_block_delta" && delta["type"] == "text_delta" {
                    if let Some(text) = delta["text"].as_str().filter(|t| !t.is_empty()) {
                        self.emit(event("delta", Some(tid), Some(turn), json!({"text": text, "delta_kind": "text"})));
                    }
                }
            }
            "assistant" => {
                let m = &msg["message"];
                let text: String = m["content"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|c| c["type"] == "text")
                    .map(|c| c["text"].as_str().unwrap_or_default())
                    .collect();
                let id = m.get("id").cloned().unwrap_or(Value::Null);
                let mut inner = self.inner();
                if !text.is_empty() && inner.last_message.get(turn) != Some(&(id.clone(), text.clone())) {
                    inner.last_message.insert(turn.into(), (id.clone(), text.clone()));
                    inner.reply.insert(turn.into(), text.clone());
                    drop(inner);
                    self.emit(event("message", Some(tid), Some(turn), json!({"text": text, "final": false, "item_id": id})));
                }
            }
            "system" if msg["subtype"] == "init" => {
                let mut inner = self.inner();
                if let Some(t) = inner.threads.get_mut(tid) {
                    // Claude owns the id now: resume from here on.
                    t.started = true;
                    t.fork_from = None;
                    t.session_id = Some(msg["session_id"].as_str().filter(|s| !s.is_empty()).unwrap_or(tid).to_string());
                }
            }
            "rate_limit_event" => {
                let info = &msg["rate_limit_info"];
                if info["status"] == "rejected" {
                    self.inner().limited.insert(turn.into());
                }
                let window = match info["rateLimitType"].as_str() {
                    Some("five_hour") => json!(300),
                    Some("seven_day") => json!(7 * 24 * 60),
                    _ => Value::Null,
                };
                self.emit(event(
                    "rate_limits",
                    Some(tid),
                    Some(turn),
                    json!({"used_percent": null, "window_minutes": window, "resets_at": iso(&info["resetsAt"]), "status": info.get("status").cloned().unwrap_or(Value::Null)}),
                ));
            }
            "result" => return true,
            _ => {}
        }
        false
    }

    fn finish(&self, tid: &str, turn: &str, result: Option<Value>, rc: i32, stderr: &str, failure: Option<String>) {
        let (interrupted, usage, reply, limited) = {
            let mut inner = self.inner();
            if inner.turn.as_ref().is_some_and(|(_, t)| t == turn) {
                inner.turn = None;
                inner.pipe = None;
            }
            let reply = inner.reply.remove(turn).unwrap_or_default();
            inner.last_message.remove(turn);
            let limited = inner.limited.remove(turn);
            if !inner.done_turns.insert(turn.into()) {
                return;
            }
            (inner.interrupted.contains(turn), inner.usage.remove(turn), reply, limited)
        };
        if let Some(u) = usage.as_ref().filter(|u| u.input + u.output > 0) {
            self.emit(event(
                "tokens",
                Some(tid),
                Some(turn),
                json!({"input": u.input, "output": u.output, "total": u.input + u.output, "cost_usd": u.cost,
                       "usage": {"input": u.input, "cached": u.cached, "output": u.output}}),
            ));
        }
        self.poll_goal(tid);
        let ok = result.as_ref().is_some_and(|r| !crate::util::truthy(r.get("is_error"))) && rc == 0 && failure.is_none();
        if interrupted {
            self.emit(event("turn_completed", Some(tid), Some(turn), json!({"status": "interrupted"})));
        } else if ok {
            let text = match usage.as_ref().filter(|u| !u.texts.is_empty()) {
                Some(u) => u.texts.join("\n\n"),
                None => reply,
            };
            if !text.is_empty() {
                self.emit(event("message", Some(tid), Some(turn), json!({"text": text, "final": true})));
            }
            let stop = result.as_ref().and_then(|r| r.get("stop_reason")).cloned().unwrap_or(Value::Null);
            self.emit(event("turn_completed", Some(tid), Some(turn), json!({"status": "completed", "stop_reason": stop})));
        } else {
            let error = failure.unwrap_or_else(|| error_text(result.as_ref(), rc, stderr));
            self.emit(event(
                "turn_failed",
                Some(tid),
                Some(turn),
                json!({"error": error, "limit": limited || is_limit(&error), "auth_retry": is_auth_retryable(&error)}),
            ));
        }
        self.emit(event("status", Some(tid), None, json!({"state": "idle"})));
    }

    /// `/goal <objective>`, or `/goal clear` to pause or clear, as a turn of its own.
    pub fn start_goal(&self, thread_id: &str, action: &str, objective: &str, effort: Option<&str>) -> Result<String, HostError> {
        let text = if action == "pause" || action == "clear" { "/goal clear".to_string() } else { format!("/goal {objective}") };
        self.start_turn(thread_id, &text, effort)
    }

    /// `/goal` first, as the turn's input, and the prompt written right behind it: Claude reads it as the goal
    /// starts (a steer), so the goal works on the task's own prompt. Needs steering.
    pub fn start_goal_turn(&self, thread_id: &str, objective: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        if !self.supports_steer() {
            return Err(failed("this Claude Code cannot take a goal and a prompt in one turn"));
        }
        let turn = self.start_goal(thread_id, "set", objective, effort)?;
        if let Err(HostError::SteerRefused(_)) = self.steer(thread_id, &turn, text) {
            // Only a turn that ended at once refuses it: the goal holds, and the task's next turn brings its prompt.
            log(&format!("claude[{}]: the task prompt missed the goal turn", self.0.bot));
        }
        Ok(turn)
    }

    /// Goal records this session's transcript gained since the turn began (Claude writes some only there).
    pub fn poll_goal(&self, thread_id: &str) {
        let (path, offset) = {
            let inner = self.inner();
            let Some(t) = inner.threads.get(thread_id) else { return };
            let Some(path) = transcript(&t.settings, &session_of(thread_id, t)) else { return };
            (path, inner.goal_offsets.get(thread_id).copied().unwrap_or(0))
        };
        let Ok(bytes) = std::fs::read(&path) else { return };
        let start = (offset as usize).min(bytes.len());
        let rest = &bytes[start..];
        let complete = rest.iter().rposition(|b| *b == b'\n').map_or(0, |i| i + 1);
        for line in rest[..complete].split(|b| *b == b'\n') {
            let Ok(message) = serde_json::from_slice::<Value>(line) else { continue };
            if let Some(status) = crate::goals::claude_status(&message).filter(|_| !crate::util::truthy(message.get("isSidechain"))) {
                self.emit(event("goal", Some(thread_id), None, Value::Object(status)));
            }
        }
        self.inner().goal_offsets.insert(thread_id.into(), (start + complete) as u64);
    }

    /// Write `text` into the running turn, or refuse with nothing written once it is over or ending.
    pub fn steer(&self, thread_id: &str, turn_id: &str, text: &str) -> Result<(), HostError> {
        let mut inner = self.inner();
        let running = inner.turn.as_ref().is_some_and(|(t, u)| t == thread_id && u == turn_id);
        match inner.pipe.as_mut() {
            Some(p) if running && p.open => {
                let mid = uuid::Uuid::new_v4().to_string();
                p.unread.insert(mid.clone());
                p.order.push(mid.clone());
                p.steered = true;
                let _ = p.lines.send(Some(user_line(&mid, text)));
                Ok(())
            }
            _ => Err(HostError::SteerRefused(format!("claude turn {turn_id} is over or ending"))),
        }
    }

    pub fn interrupt(&self, _thread_id: &str, turn_id: &str) {
        let mut inner = self.inner();
        if inner.turn.as_ref().is_none_or(|(_, t)| t != turn_id) {
            return; // already over; nothing to stop
        }
        inner.interrupted.insert(turn_id.into());
        if let Some(p) = inner.pipe.as_mut() {
            p.close();
            p.stopper.terminate(STOP_TIMEOUT);
        }
    }
}

fn session_of(thread_id: &str, t: &Thread) -> String {
    t.session_id.clone().unwrap_or_else(|| thread_id.to_string())
}

/// This exact session's transcript: other bots' and people's transcripts stay private.
fn transcript(settings: &Settings, session: &str) -> Option<std::path::PathBuf> {
    let get = |k: &str| if settings.env.is_empty() { std::env::var(k).ok() } else { settings.env.get(k).cloned() }.filter(|v| !v.is_empty());
    let home = get("HOME").or_else(|| std::env::var("HOME").ok()).unwrap_or_default();
    let dir = get("CLAUDE_CONFIG_DIR").map(std::path::PathBuf::from).unwrap_or_else(|| std::path::Path::new(&home).join(".claude"));
    let name = format!("{session}.jsonl");
    std::fs::read_dir(dir.join("projects")).ok()?.flatten().map(|e| e.path().join(&name)).find(|p| p.is_file())
}

fn stderr_tail(file: &mut std::fs::File) -> String {
    let mut text = String::new();
    if file.seek(SeekFrom::Start(0)).is_ok() {
        let mut bytes = vec![];
        let _ = file.read_to_end(&mut bytes);
        text = String::from_utf8_lossy(&bytes).to_string();
    }
    let t = text.trim();
    let skip = t.chars().count().saturating_sub(STDERR_TAIL);
    t.chars().skip(skip).collect()
}

pub fn error_text(result: Option<&Value>, rc: i32, stderr: &str) -> String {
    if let Some(r) = result {
        if let Some(errors) = r["errors"].as_array().filter(|e| !e.is_empty()) {
            return errors.iter().map(text_of).collect::<Vec<_>>().join("; ");
        }
        if let Some(t) = r["result"].as_str().filter(|t| !t.is_empty()) {
            return t.into();
        }
        if crate::util::truthy(r.get("subtype")) {
            return text_of(&r["subtype"]);
        }
    }
    if !stderr.is_empty() {
        return stderr.into();
    }
    format!("claude exited {rc}")
}

/// The turn's environment (hosts/claude.py _env): the given one, else this process's; a bot with branches keeps
/// no auto memory, and no turn loads the claude.ai connectors.
fn turn_env(settings: &Settings) -> crate::env::Env {
    let mut env: crate::env::Env = if settings.env.is_empty() { std::env::vars().collect() } else { settings.env.clone() };
    if settings.shared {
        env.insert("CLAUDE_CODE_DISABLE_AUTO_MEMORY".into(), "1".into());
    }
    // Belt and braces: `--strict-mcp-config` already skips the claude.ai connectors.
    env.insert("ENABLE_CLAUDEAI_MCP_SERVERS".into(), "false".into());
    // The login and the session files live under HOME; a trimmed environment must keep it.
    if env.get("HOME").is_none_or(|h| h.is_empty()) {
        if let Ok(home) = std::env::var("HOME") {
            env.insert("HOME".into(), home);
        }
    }
    env
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `claude` that writes a fixture once the prompt arrives and exits when stdin closes, as Claude does.
    fn fake_claude(dir: &std::path::Path) -> String {
        use std::os::unix::fs::PermissionsExt;
        let path = dir.join("claude");
        std::fs::write(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\ncat \"$FIXTURE\"\ncat >/dev/null\n",
        )
        .unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        path.display().to_string()
    }

    #[tokio::test]
    async fn every_claude_fixture_gives_the_python_hosts_events() {
        let dir = std::env::temp_dir().join(format!("fake-claude-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake_claude(&dir);
        for (path, expected) in crate::hosts::fixtures::cases("claude") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = ClaudeHost::new("acme", vec![cmd.clone()], tx);
            host.start().await.unwrap();
            assert!(host.supports_steer());
            let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
            settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
            settings.env.insert("FIXTURE".into(), path.display().to_string());
            let thread = host.start_thread(&settings);
            host.start_turn(&thread, "hello", None).unwrap();
            let mut got = vec![];
            while let Ok(Some(mut ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
                let idle = ev["kind"] == "status" && ev["state"] == "idle";
                ev.as_object_mut().unwrap().retain(|k, _| k != "thread_id" && k != "turn_id");
                got.push(ev);
                if idle {
                    break;
                }
            }
            assert_eq!(got, expected, "{}", path.display());
            // The init line hands the session to Claude: the next turn resumes it.
            let sid = host.session_id(&thread);
            assert_ne!(sid, thread);
            let argv = host.argv(&thread, &host.inner().threads[&thread].clone(), None);
            assert_eq!(&argv[argv.len() - 2..], &["--resume".to_string(), sid]);
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_turn_gets_only_hub_declared_and_project_mcp_servers() {
        let dir = std::env::temp_dir().join(format!("claude-mcp-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".mcp.json"), r#"{"mcpServers": {"repo": {"command": "x"}, "hub": {"command": "stale"}}}"#).unwrap();
        let (tx, _rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec!["claude".into()], tx);
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        for (k, v) in [("HUB_API_URL", "http://hub.invalid"), ("HUB_TOKEN", "t"), ("HUB_BOT", "acme")] {
            settings.env.insert(k.into(), v.into());
        }
        let thread = host.start_thread(&settings);
        let argv = host.argv(&thread, &host.inner().threads[&thread].clone(), None);
        assert!(argv.contains(&"--strict-mcp-config".to_string()) && !argv.contains(&"--setting-sources".to_string()));
        let config: Value = serde_json::from_str(&argv[argv.iter().position(|a| a == "--mcp-config").unwrap() + 1]).unwrap();
        assert_eq!(config["mcpServers"]["repo"]["command"], "x");
        assert_ne!(config["mcpServers"]["hub"]["command"], "stale");
        let env = turn_env(&settings);
        assert_eq!(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false");
        assert!(!env.contains_key("CLAUDE_CODE_DISABLE_AUTO_MEMORY"));
        assert_eq!(turn_env(&Settings { shared: true, ..settings })["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn flags_follow_the_python_host() {
        assert_eq!(model_for(Some("gpt-5.4")), None);
        assert_eq!(model_for(Some(" claude-opus-5 ")).as_deref(), Some("claude-opus-5"));
        assert_eq!(effort_for(Some("XHIGH")).as_deref(), Some("xhigh"));
        assert_eq!(effort_for(Some("minimal")), None);
        assert_eq!(autocompact_window(&json!(null)), 283_000);
        assert_eq!(autocompact_window(&json!(10)), 100_000);
        assert_eq!(version_tuple("2.1.296 (Claude Code)"), Some((2, 1, 296)));
        assert_eq!(usage_tokens(&json!({"input_tokens": 3, "cache_creation_input_tokens": 4, "cache_read_input_tokens": "5", "output_tokens": 7})), (12, 7));
    }
}
