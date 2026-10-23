//! The Codex host: one `codex app-server` process over stdio, newline-delimited
//! JSON-RPC 2.0. The bot's environment is both the process's own and, through `shell_environment_policy`,
//! what each thread's shell commands see; the operator's own MCP servers are stubbed so they start nothing.

use std::collections::{HashMap, HashSet};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::ChildStdin;
use tokio::sync::oneshot;

use super::{command_program, event, failed, hub_mcp_server, is_limit, tool_summary, Events, HostError, Settings};
use crate::env::Env;
use crate::util::{iso, log, py_dumps, text_of};

const REQUEST_TIMEOUT: u64 = 120;
const START_TIMEOUT: u64 = 60;
const ENV_SKIP: &[&str] = &[
    "PWD", "OLDPWD", "SHLVL", "_", "TMPDIR", "XPC_SERVICE_NAME", "XPC_FLAGS", "__CF_USER_TEXT_ENCODING", "TERM_SESSION_ID", "SSH_AUTH_SOCK",
];
const ENV_MAX_VALUE: usize = 8000;
const NOOP_COMMAND: &str = "/usr/bin/true";
/// Thread items that are not tool calls. Any other item type, including one added later, is reported as a tool,
/// because an unreported call would let a stopped run look safe to repeat.
const NOT_TOOLS: &[&str] = &[
    "userMessage", "hookPrompt", "agentMessage", "plan", "reasoning", "subAgentActivity",
    "enteredReviewMode", "exitedReviewMode", "contextCompaction", "sleep",
];

/// A tool item's name and what it acts on: the files it changes or the program it runs, never its arguments.
fn tool_of(item: &Value) -> (String, String) {
    let kind = item["type"].as_str().unwrap_or_default();
    let named = |key: &str| item[key].as_str().filter(|t| !t.is_empty()).unwrap_or(kind).to_string();
    match kind {
        "commandExecution" => ("shell".into(), command_program(item["command"].as_str().unwrap_or_default())),
        "fileChange" => {
            let paths: Vec<&str> = item["changes"].as_array().into_iter().flatten().filter_map(|c| c["path"].as_str()).collect();
            ("apply_patch".into(), tool_summary(&json!({"path": paths.join(" ")})))
        }
        "mcpToolCall" => (format!("mcp__{}__{}", text_of(&item["server"]), text_of(&item["tool"])), String::new()),
        "dynamicToolCall" | "collabAgentToolCall" => (named("tool"), String::new()),
        // An output with no call item of its own still means a call ran.
        "functionCallOutput" => match item["namespace"].as_str().filter(|n| !n.is_empty()) {
            Some(ns) => (format!("{ns}__{}", named("name")), String::new()),
            None => (named("name"), String::new()),
        },
        "webSearch" => ("web_search".into(), String::new()),
        "imageView" => ("view_image".into(), tool_summary(&json!({"path": item["path"]}))),
        _ => (kind.to_string(), String::new()),
    }
}

/// The `set` table for `shell_environment_policy`: the bot's environment, trimmed.
pub fn env_for_config(env: &Env) -> Map<String, Value> {
    env.iter()
        .filter(|(k, v)| !ENV_SKIP.contains(&k.as_str()) && v.len() <= ENV_MAX_VALUE && !v.contains('\0'))
        .map(|(k, v)| (k.clone(), json!(v)))
        .collect()
}

/// A `mcp_servers` override that stops every server in the operator's config.toml from starting.
pub fn mcp_disable_config(codex_home: Option<&str>) -> Map<String, Value> {
    let home = codex_home
        .map(String::from)
        .or_else(|| std::env::var("CODEX_HOME").ok().filter(|h| !h.is_empty()))
        .unwrap_or_else(|| format!("{}/.codex", std::env::var("HOME").unwrap_or_default()));
    let Ok(text) = std::fs::read_to_string(format!("{home}/config.toml")) else { return Map::new() };
    let Ok(doc) = text.parse::<toml::Table>() else { return Map::new() };
    let mut out = Map::new();
    if let Some(toml::Value::Table(servers)) = doc.get("mcp_servers") {
        for (name, s) in servers {
            let Some(s) = s.as_table() else { continue };
            let has_command = match s.get("command") {
                Some(toml::Value::String(c)) => !c.is_empty(),
                Some(toml::Value::Array(a)) => !a.is_empty(),
                Some(toml::Value::Boolean(b)) => *b,
                Some(_) => true,
                None => false,
            };
            out.insert(name.clone(), if has_command { json!({"command": NOOP_COMMAND, "args": []}) } else { json!({"startup_timeout_ms": 1}) });
        }
    }
    if out.is_empty() {
        Map::new()
    } else {
        let mut m = Map::new();
        m.insert("mcp_servers".into(), Value::Object(out));
        m
    }
}

type Reply = Result<Value, Value>;

struct Shared {
    tx: Events,
    env: Env,
    base_config: Map<String, Value>,
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<i64, oneshot::Sender<Reply>>>,
    next_id: AtomicI64,
    alive: AtomicBool,
    child: Mutex<Option<(super::Stopper, Arc<AtomicBool>)>>,
    exited: tokio::sync::Mutex<Option<oneshot::Receiver<std::io::Result<std::process::ExitStatus>>>>,
    /// True once the app-server process this host last started has been reaped. Never taken by `stop`, so it still
    /// tells after a stop that was cut off (`running`, `stop_and_reap`).
    reaped: Mutex<Option<tokio::sync::watch::Receiver<bool>>>,
    active_turn: Mutex<HashMap<String, String>>,
    done_turns: Mutex<HashSet<String>>,
    token_seen: Mutex<HashMap<String, [i64; 3]>>,
    /// A subagent's thread to the thread that started it: its tool calls are reported on the parent's turn.
    children: Mutex<HashMap<String, String>>,
    /// Tool events already sent, by item id and status: a start or end is reported once.
    tools_sent: Mutex<HashSet<String>>,
    capture: Mutex<Option<Arc<crate::capture::Capture>>>,
}

#[derive(Clone)]
pub struct CodexHost(Arc<Shared>);

impl CodexHost {
    /// A host for a subscription profile: the servers silenced are the ones in that profile's config.toml.
    pub fn on_home(env: Env, tx: Events, codex_home: Option<&str>) -> CodexHost {
        CodexHost(Arc::new(Shared {
            tx,
            env,
            base_config: mcp_disable_config(codex_home),
            stdin: tokio::sync::Mutex::new(None),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicI64::new(0),
            alive: AtomicBool::new(false),
            child: Mutex::new(None),
            exited: tokio::sync::Mutex::new(None),
            reaped: Mutex::new(None),
            active_turn: Mutex::new(HashMap::new()),
            done_turns: Mutex::new(HashSet::new()),
            token_seen: Mutex::new(HashMap::new()),
            children: Mutex::new(HashMap::new()),
            tools_sent: Mutex::new(HashSet::new()),
            capture: Mutex::new(None),
        }))
    }

    fn emit(&self, ev: Value) {
        let _ = self.0.tx.send(ev);
    }

    pub fn set_capture(&self, capture: Arc<crate::capture::Capture>) {
        capture.attach("codex");
        *self.0.capture.lock().unwrap() = Some(capture);
    }

    fn capture(&self) -> Option<Arc<crate::capture::Capture>> {
        self.0.capture.lock().unwrap().clone()
    }

    pub async fn start(&self) -> Result<(), HostError> {
        if self.alive() {
            return Ok(());
        }
        // An earlier app-server that stopped answering but still runs is ended first: it may be using this login.
        if self.running() {
            self.stop_and_reap().await;
        }
        let mut command = crate::isolation::command("codex");
        command.arg("app-server").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).env_clear().envs(&self.0.env);
        let mut child = command.spawn().map_err(|e| failed(format!("could not start codex app-server: {e}")))?;
        let _stderr = crate::run_diagnostics::Stderr::capture(&mut child, self.0.tx.clone(), &self.0.env);
        let stdout = child.stdout.take().ok_or_else(|| failed("codex app-server has no stdout"))?;
        *self.0.stdin.lock().await = child.stdin.take();
        log(&format!("codex: app-server started pid {}", child.id().map_or("?".into(), |p| p.to_string())));
        let (stopper, exited) = super::own(child);
        // `exited` resolves once; whether it has is kept here for `alive`.
        let gone = Arc::new(AtomicBool::new(false));
        let (relay_tx, relay_rx) = oneshot::channel();
        let (reaped_tx, reaped_rx) = tokio::sync::watch::channel(false);
        let flag = gone.clone();
        let diagnostics = self.0.tx.clone();
        tokio::spawn(async move {
            let status = exited.await.unwrap_or_else(|_| Err(std::io::Error::other("codex app-server reaper ended")));
            _stderr.drained().await;
            let code = super::return_code(&status);
            let _ = diagnostics.send(json!({"kind": "error", "error": crate::run_diagnostics::process_reason(code, &_stderr.tail()), "exit_code": code, "process_exit": true}));
            flag.store(true, Ordering::SeqCst);
            let _ = reaped_tx.send(true);
            let _ = relay_tx.send(status);
        });
        *self.0.reaped.lock().unwrap() = Some(reaped_rx);
        *self.0.child.lock().unwrap() = Some((stopper, gone));
        *self.0.exited.lock().await = Some(relay_rx);
        self.0.alive.store(true, Ordering::SeqCst);
        let host = self.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                if let Some(c) = host.capture() {
                    c.received(line);
                }
                if let Ok(msg) = serde_json::from_str::<Value>(line) {
                    host.on_message(msg).await;
                }
            }
            host.0.alive.store(false, Ordering::SeqCst);
            for (_, slot) in host.0.pending.lock().unwrap().drain() {
                let _ = slot.send(Err(json!({"message": "codex app-server exited"})));
            }
        });
        let info = json!({"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"});
        self.request("initialize", json!({"clientInfo": info, "capabilities": {"experimentalApi": true}}), START_TIMEOUT).await?;
        self.notify("initialized", json!({})).await
    }

    pub async fn stop(&self) {
        drop(self.0.stdin.lock().await.take());
        let child = self.0.child.lock().unwrap().take();
        if let Some((stopper, _)) = child {
            stopper.terminate(Duration::from_secs(5));
            if let Some(exited) = self.0.exited.lock().await.take() {
                let _ = tokio::time::timeout(Duration::from_secs(6), exited).await;
            }
        }
        self.0.alive.store(false, Ordering::SeqCst);
    }

    /// Whether the app-server process is still there, answering or not: from its start until it is reaped. `alive`
    /// is about the protocol and turns false sooner (stdout closed, a stop begun), while the process can live on.
    pub fn running(&self) -> bool {
        self.0.reaped.lock().unwrap().as_ref().is_some_and(|r| !*r.borrow())
    }

    /// `stop`, then wait until the process has been reaped. Safe after a stop that was cut off: that stop's TERM and
    /// its KILL five seconds later still come from the reaper task (super::own), and this waits for them.
    pub async fn stop_and_reap(&self) {
        self.stop().await;
        let reaped = self.0.reaped.lock().unwrap().clone();
        if let Some(mut reaped) = reaped {
            let _ = reaped.wait_for(|gone| *gone).await;
        }
    }

    pub fn alive(&self) -> bool {
        if !self.0.alive.load(Ordering::SeqCst) {
            return false;
        }
        match self.0.child.lock().unwrap().as_ref() {
            Some((_, gone)) => !gone.load(Ordering::SeqCst),
            None => false,
        }
    }

    async fn write(&self, msg: &Value) -> Result<(), HostError> {
        if !self.alive() {
            return Err(failed("codex app-server is not running"));
        }
        let line = msg.to_string() + "\n";
        if let Some(c) = self.capture() {
            c.sent(&line);
        }
        let mut stdin = self.0.stdin.lock().await;
        let Some(s) = stdin.as_mut() else { return Err(failed("codex app-server stdin closed")) };
        if let Err(e) = async {
            s.write_all(line.as_bytes()).await?;
            s.flush().await
        }
        .await
        {
            return Err(failed(format!("codex app-server stdin closed: {e}")));
        }
        Ok(())
    }

    pub async fn notify(&self, method: &str, params: Value) -> Result<(), HostError> {
        self.write(&json!({"jsonrpc": "2.0", "method": method, "params": params})).await
    }

    pub async fn request(&self, method: &str, params: Value, timeout: u64) -> Result<Value, HostError> {
        let id = self.0.next_id.fetch_add(1, Ordering::SeqCst) + 1;
        let (tx, rx) = oneshot::channel();
        self.0.pending.lock().unwrap().insert(id, tx);
        if let Err(e) = self.write(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})).await {
            self.0.pending.lock().unwrap().remove(&id);
            return Err(e);
        }
        match tokio::time::timeout(Duration::from_secs(timeout), rx).await {
            Err(_) => {
                self.0.pending.lock().unwrap().remove(&id);
                Err(failed(format!("{method} timed out after {timeout}s")))
            }
            Ok(Err(_)) => Err(failed(format!("{method}: codex app-server exited"))),
            Ok(Ok(Ok(result))) => Ok(result),
            Ok(Ok(Err(err))) => {
                let text = err["message"].as_str().filter(|m| !m.is_empty()).map(String::from).unwrap_or_else(|| err.to_string());
                Err(failed(format!("{method}: {text}")))
            }
        }
    }

    async fn on_message(&self, msg: Value) {
        let id = msg.get("id").filter(|i| !i.is_null()).cloned();
        let method = msg.get("method").and_then(|m| m.as_str()).map(String::from);
        match (id, method) {
            (Some(id), None) => {
                // A response to one of our requests.
                if let Some(slot) = id.as_i64().and_then(|i| self.0.pending.lock().unwrap().remove(&i)) {
                    let _ = slot.send(match msg.get("error") {
                        Some(e) => Err(e.clone()),
                        None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
                    });
                }
            }
            (Some(id), Some(_)) => {
                // A server request; the runner approves nothing.
                let _ = self.write(&json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "the runner answers no requests"}})).await;
            }
            (None, method) => {
                let p = msg.get("params").filter(|p| p.is_object()).cloned().unwrap_or(json!({}));
                self.on_notification(method.as_deref().unwrap_or_default(), &p);
            }
        }
    }

    fn on_notification(&self, method: &str, p: &Value) {
        let tid = p["threadId"].as_str().map(String::from);
        let turn = p["turnId"].as_str().map(String::from);
        let (t, u) = (tid.as_deref(), turn.as_deref());
        match method {
            "thread/started" => {
                // A subagent's thread starts before any of its items, so its calls are known to be the parent's
                // from the first one.
                let thread = &p["thread"];
                let spawned = &thread["source"]["subAgent"]["thread_spawn"]["parent_thread_id"];
                let parent = thread["parentThreadId"].as_str().or(spawned.as_str()).unwrap_or_default();
                let child = text_of(&thread["id"]);
                if !parent.is_empty() && !child.is_empty() && child != parent {
                    let mut children = self.0.children.lock().unwrap();
                    let root = children.get(parent).cloned().unwrap_or_else(|| parent.to_string());
                    children.insert(child, root);
                }
            }
            "turn/started" => {
                let started = p["turn"]["id"].as_str().map(String::from);
                if tid.as_ref().is_some_and(|tid| self.0.children.lock().unwrap().contains_key(tid)) {
                    return; // a subagent's turn: the parent's turn is still the one running
                }
                if let (Some(tid), Some(s)) = (&tid, &started) {
                    self.0.active_turn.lock().unwrap().insert(tid.clone(), s.clone());
                    self.0.tools_sent.lock().unwrap().clear();
                }
                // `tool_events`: this run reports every tool call, so a run with none provably called none.
                self.emit(event("status", t, started.as_deref(), json!({"state": "active", "tool_events": true})));
            }
            "item/agentMessage/delta" => {
                self.emit(event("delta", t, u, json!({"text": p["delta"].as_str().unwrap_or_default()})));
            }
            "item/started" => self.on_item(false, p),
            "item/completed" => {
                self.on_item(true, p);
                let item = &p["item"];
                if item["type"] == "agentMessage" {
                    self.emit(event(
                        "message",
                        t,
                        u,
                        json!({"text": item["text"].as_str().unwrap_or_default(), "final": item["phase"] == "final_answer",
                               "item_id": item.get("id").cloned().unwrap_or(Value::Null)}),
                    ));
                }
            }
            "thread/tokenUsage/updated" => {
                let usage = &p["tokenUsage"];
                let total = &usage["total"];
                let inc = self.token_increment(t.unwrap_or_default(), usage);
                self.emit(event(
                    "tokens",
                    t,
                    u,
                    json!({"input": total.get("inputTokens").cloned().unwrap_or(Value::Null),
                           "output": total.get("outputTokens").cloned().unwrap_or(Value::Null),
                           "total": total.get("totalTokens").cloned().unwrap_or(Value::Null), "usage": inc}),
                ));
            }
            "account/rateLimits/updated" => {
                let rl = &p["rateLimits"];
                for w in [&rl["primary"], &rl["secondary"]] {
                    if !w.is_object() {
                        continue;
                    }
                    self.emit(event(
                        "rate_limits",
                        t,
                        u,
                        json!({"used_percent": w.get("usedPercent").cloned().unwrap_or(Value::Null),
                               "window_minutes": w.get("windowDurationMins").cloned().unwrap_or(Value::Null),
                               "resets_at": iso(&w["resetsAt"]), "plan_type": rl.get("planType").cloned().unwrap_or(Value::Null)}),
                    ));
                }
            }
            "thread/status/changed" => {
                let state = p["status"]["type"].clone();
                if state == "idle" {
                    if let Some(tid) = &tid {
                        self.0.active_turn.lock().unwrap().remove(tid);
                    }
                }
                self.emit(event("status", t, None, json!({"state": state})));
            }
            "turn/completed" => {
                let done = &p["turn"];
                let finished = done["id"].as_str().map(String::from).or_else(|| tid.as_ref().and_then(|t| self.0.active_turn.lock().unwrap().get(t).cloned()));
                if let Some(tid) = &tid {
                    self.0.active_turn.lock().unwrap().remove(tid);
                }
                let key = finished.clone().unwrap_or_default();
                if !self.0.done_turns.lock().unwrap().insert(key) {
                    return;
                }
                if done["status"] == "failed" {
                    let msg = done["error"]["message"].as_str().filter(|m| !m.is_empty()).unwrap_or("the turn failed").to_string();
                    self.emit(event("turn_failed", t, finished.as_deref(), json!({"limit": is_limit(&msg), "error": msg})));
                } else {
                    let status = done["status"].as_str().filter(|s| !s.is_empty()).unwrap_or("completed");
                    self.emit(event("turn_completed", t, finished.as_deref(), json!({"status": status})));
                }
            }
            "error" => {
                let err = &p["error"];
                let msg = err["message"].as_str().filter(|m| !m.is_empty()).map(String::from).unwrap_or_else(|| py_dumps(err).chars().take(500).collect());
                let will_retry = crate::util::truthy(p.get("willRetry"));
                let fresh = turn.as_ref().is_some_and(|x| !self.0.done_turns.lock().unwrap().contains(x));
                if turn.is_some() && !will_retry && fresh {
                    self.0.done_turns.lock().unwrap().insert(turn.clone().unwrap());
                    if let Some(tid) = &tid {
                        self.0.active_turn.lock().unwrap().remove(tid);
                    }
                    self.emit(event("turn_failed", t, u, json!({"error": msg, "limit": is_limit(&msg)})));
                } else {
                    self.emit(event("error", t, u, json!({"error": msg, "limit": is_limit(&msg)})));
                }
            }
            "thread/goal/updated" => {
                let goal = &p["goal"];
                let status = crate::goals::codex_status(goal);
                if ["active", "paused", "met", "stopped"].contains(&status.as_str()) {
                    let note = goal["note"].as_str().filter(|n| !n.is_empty()).map(|n| json!(n)).unwrap_or_else(|| {
                        if status == "stopped" { goal.get("status").cloned().unwrap_or(json!("")) } else { json!("") }
                    });
                    self.emit(event("goal", t, None, json!({"status": status, "objective": goal.get("objective").cloned().unwrap_or(Value::Null), "note": note})));
                }
            }
            "thread/goal/cleared" => self.emit(event("goal", t, None, json!({"status": "cleared", "note": ""}))),
            _ => {} // hook/*, mcpServer/*, turn/plan, warnings: nothing to do
        }
    }

    /// `tool` events for an item starting or completing. A subagent's own items are reported on the turn of
    /// the thread that started it, with `parent_id` naming the subagent's thread.
    fn on_item(&self, done: bool, p: &Value) {
        let item = &p["item"];
        let kind = item["type"].as_str().unwrap_or_default();
        let tid = text_of(&p["threadId"]);
        let parent_thread = self.0.children.lock().unwrap().get(&tid).cloned();
        let (thread, sub) = match &parent_thread {
            Some(parent) => (parent.clone(), Some(tid.clone())),
            None => (tid.clone(), None),
        };
        let turn = match &sub {
            Some(_) => self.0.active_turn.lock().unwrap().get(&thread).cloned(),
            None => p["turnId"].as_str().map(String::from),
        };
        let (t, u) = (Some(thread.as_str()).filter(|t| !t.is_empty()), turn.as_deref());
        let mut adopt = |child: &str| {
            if !child.is_empty() && child != thread {
                self.0.children.lock().unwrap().insert(child.to_string(), thread.clone());
            }
        };
        if kind == "collabAgentToolCall" {
            item["receiverThreadIds"].as_array().into_iter().flatten().filter_map(Value::as_str).for_each(&mut adopt);
        }
        if kind == "subAgentActivity" {
            let child = text_of(&item["agentThreadId"]);
            adopt(&child);
            let status = match item["kind"].as_str().unwrap_or_default() {
                "started" => "started",
                "completed" => "completed",
                "interrupted" => "interrupted",
                _ => return,
            };
            if !self.0.tools_sent.lock().unwrap().insert(format!("agent:{child}:{status}")) {
                return;
            }
            let subagent = json!({"id": child, "parent_id": sub, "type": item.get("agentPath").cloned().unwrap_or(Value::Null),
                                  "model": item.get("model").cloned().unwrap_or(Value::Null), "status": status});
            self.emit(event("tool", t, u, json!({"tool": "subagent", "status": status, "item_id": format!("agent:{child}"),
                                                 "parent_id": sub, "subagent": subagent})));
            return;
        }
        if kind.is_empty() || NOT_TOOLS.contains(&kind) {
            return;
        }
        let id = text_of(&item["id"]);
        let status = if !done {
            "started"
        } else {
            match item["status"].as_str().unwrap_or_default() {
                "failed" => "failed",
                "declined" => "declined",
                "interrupted" => "interrupted",
                _ => "completed",
            }
        };
        if !self.0.tools_sent.lock().unwrap().insert(format!("{id}:{status}")) {
            return;
        }
        let (name, summary) = tool_of(item);
        let mut fields = json!({"tool": name, "status": status, "item_id": id, "parent_id": sub});
        if !done {
            fields["summary"] = json!(summary);
        }
        self.emit(event("tool", t, u, fields));
    }

    /// The tokens one report adds: the first in this process counts `last`, later ones what the total grew by.
    fn token_increment(&self, thread: &str, usage: &Value) -> Value {
        let fields = |row: &Value| -> [i64; 3] {
            let c = |k: &str| match &row[k] {
                Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0).max(0),
                Value::String(s) => s.trim().parse::<i64>().unwrap_or(0).max(0),
                _ => 0,
            };
            [c("inputTokens"), c("cachedInputTokens"), c("outputTokens")]
        };
        let (total, last) = (fields(&usage["total"]), fields(&usage["last"]));
        let before = self.0.token_seen.lock().unwrap().insert(thread.into(), total);
        let inc = match before {
            None => last,
            Some(b) => [(total[0] - b[0]).max(0), (total[1] - b[1]).max(0), (total[2] - b[2]).max(0)],
        };
        json!({"input": inc[0], "cached": inc[1], "output": inc[2]})
    }

    fn thread_params(&self, settings: &Settings) -> Value {
        let mut cfg = self.0.base_config.clone();
        let env = env_for_config(&settings.env);
        if !env.is_empty() {
            cfg.insert("shell_environment_policy".into(), json!({"inherit": "core", "set": env}));
        }
        // The hub's own MCP server rides next to the stubbed operator ones.
        let mut servers = cfg.get("mcp_servers").and_then(|s| s.as_object()).cloned().unwrap_or_default();
        crate::mcp::codex_merge(&mut servers, crate::mcp::codex_config(&settings.mcp_servers, &settings.env, &self.0.env));
        if let Some(hub) = hub_mcp_server(&settings.env) {
            servers.insert("hub".into(), hub);
        }
        if !servers.is_empty() {
            cfg.insert("mcp_servers".into(), Value::Object(servers));
        }
        cfg.insert("model_auto_compact_token_limit".into(), json!(super::compact_at(&settings.compact_at)));
        // No memory under this computer's CODEX_HOME, even where the operator's config.toml turns it on: a bot
        // remembers in its repository's memory/ (Dev 301).
        cfg.insert("features.memories".into(), json!(false));
        let mut params = json!({"cwd": settings.cwd, "approvalPolicy": "never", "sandbox": "danger-full-access"});
        if let Some(m) = settings.model.as_ref().filter(|m| !m.is_empty()) {
            params["model"] = json!(m);
        }
        params["config"] = Value::Object(cfg);
        params
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        let res = self.request("thread/start", self.thread_params(settings), START_TIMEOUT).await?;
        res["thread"]["id"].as_str().filter(|t| !t.is_empty()).map(String::from).ok_or_else(|| failed(format!("thread/start for {bot} returned no thread id")))
    }

    pub async fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        let mut params = self.thread_params(settings);
        params["threadId"] = json!(thread_id);
        params["excludeTurns"] = json!(true);
        let res = self.request("thread/resume", params, START_TIMEOUT).await?;
        Ok(res["thread"]["id"].as_str().filter(|t| !t.is_empty()).unwrap_or(thread_id).to_string())
    }

    pub async fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let mut params = json!({"threadId": thread_id, "input": [{"type": "text", "text": text}]});
        if let Some(e) = effort.filter(|e| !e.is_empty()) {
            params["effort"] = json!(e);
        }
        let res = self.request("turn/start", params, REQUEST_TIMEOUT).await?;
        let turn = res["turn"]["id"].as_str().filter(|t| !t.is_empty()).map(String::from).ok_or_else(|| failed("turn/start returned no turn id"))?;
        self.0.active_turn.lock().unwrap().insert(thread_id.into(), turn.clone());
        Ok(turn)
    }

    pub async fn steer(&self, thread_id: &str, turn_id: &str, text: &str) -> Result<(), HostError> {
        self.request("turn/steer", json!({"threadId": thread_id, "expectedTurnId": turn_id, "input": [{"type": "text", "text": text}]}), REQUEST_TIMEOUT)
            .await
            .map(|_| ())
    }

    pub async fn start_goal(&self, thread_id: &str, action: &str, objective: &str, effort: Option<&str>) -> Result<String, HostError> {
        let thread = json!({"threadId": thread_id});
        match action {
            "clear" => {
                self.request("thread/goal/clear", thread, REQUEST_TIMEOUT).await?;
                Ok(self.control_completed(thread_id))
            }
            "pause" => {
                if self.request("thread/goal/set", json!({"threadId": thread_id, "status": "paused"}), REQUEST_TIMEOUT).await.is_err() {
                    self.request("thread/goal/clear", thread, REQUEST_TIMEOUT).await?;
                }
                Ok(self.control_completed(thread_id))
            }
            _ => {
                self.request("thread/goal/set", json!({"threadId": thread_id, "objective": objective, "status": "active"}), REQUEST_TIMEOUT).await?;
                self.start_turn(thread_id, objective, effort).await
            }
        }
    }

    pub async fn start_goal_turn(&self, thread_id: &str, objective: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        self.request("thread/goal/set", json!({"threadId": thread_id, "objective": objective, "status": "active"}), REQUEST_TIMEOUT).await?;
        self.start_turn(thread_id, text, effort).await
    }

    /// A control call that runs no model turn still ends one, so the runner's loop finishes as for any turn.
    fn control_completed(&self, thread_id: &str) -> String {
        let turn = uuid::Uuid::new_v4().to_string();
        self.emit(event("turn_completed", Some(thread_id), Some(&turn), json!({"status": "completed"})));
        turn
    }

    /// `/compact` and `/review [instructions]` as the app server's own calls; anything else is a prompt.
    pub async fn start_command(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let trimmed = text.trim();
        let (name, args) = trimmed.split_once(' ').unwrap_or((trimmed, ""));
        match name {
            "/compact" => {
                self.request("thread/compact/start", json!({"threadId": thread_id}), REQUEST_TIMEOUT).await?;
                Ok(self.control_completed(thread_id))
            }
            "/review" => {
                let target = if args.is_empty() { json!({"type": "uncommittedChanges"}) } else { json!({"type": "custom", "instructions": args}) };
                let res = self.request("review/start", json!({"threadId": thread_id, "target": target}), REQUEST_TIMEOUT).await?;
                let turn = res["turn"]["id"].as_str().filter(|t| !t.is_empty()).map(String::from).ok_or_else(|| failed("review/start returned no turn id"))?;
                self.0.active_turn.lock().unwrap().insert(thread_id.into(), turn.clone());
                Ok(turn)
            }
            _ => self.start_turn(thread_id, text, effort).await,
        }
    }

    pub fn active_turn(&self, thread_id: &str) -> Option<String> {
        self.0.active_turn.lock().unwrap().get(thread_id).cloned()
    }

    pub async fn interrupt(&self, thread_id: &str, turn_id: &str) -> Result<(), HostError> {
        self.request("turn/interrupt", json!({"threadId": thread_id, "turnId": turn_id}), 30).await.map(|_| ())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn every_codex_fixture_gives_the_python_hosts_events() {
        for (path, expected) in crate::hosts::fixtures::cases("codex") {
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
            let host = CodexHost::on_home(Env::new(), tx, None);
            for line in std::fs::read_to_string(&path).unwrap().lines().filter(|l| !l.trim().is_empty()) {
                host.on_message(serde_json::from_str(line).unwrap()).await;
            }
            let mut got = vec![];
            while let Ok(ev) = rx.try_recv() {
                got.push(ev);
            }
            assert_eq!(got, expected, "{}", path.display());
        }
    }

    #[test]
    fn the_operators_servers_are_stubbed_and_the_environment_trimmed() {
        let dir = std::env::temp_dir().join(format!("codex-home-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("config.toml"), "[mcp_servers.local]\ncommand = \"npx\"\n[mcp_servers.remote]\nurl = \"https://acme.example/mcp\"\n").unwrap();
        let cfg = mcp_disable_config(Some(dir.to_str().unwrap()));
        assert_eq!(Value::Object(cfg), json!({"mcp_servers": {"local": {"command": "/usr/bin/true", "args": []}, "remote": {"startup_timeout_ms": 1}}}));
        let env: Env = [("PWD", "/x"), ("ACME_KEY", "v"), ("BIG", &"x".repeat(9000)[..])].iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        assert_eq!(Value::Object(env_for_config(&env)), json!({"ACME_KEY": "v"}));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn no_thread_keeps_a_codex_memory_outside_the_repository() {
        let host = CodexHost::on_home(Env::new(), tokio::sync::mpsc::unbounded_channel().0, None);
        assert_eq!(host.thread_params(&Settings::default())["config"]["features.memories"], json!(false));
    }
}
