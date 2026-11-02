//! The Claude Code host: one `claude -p` process per turn, one Claude session per
//! bot thread. The prompt goes in on stdin as a stream-json user message and stdin stays open for steers;
//! `--replay-user-messages` echoes each message as Claude reads it, so a `result` ends the runner's turn only
//! when no steer is unread and no background task of the session still runs, and then stdin closes and the process
//! exits. Claude stays up for its background tasks either way; open stdin lets a follow-up in meanwhile.

use std::collections::{HashMap, HashSet};
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Child;
use tokio::sync::mpsc;
use tokio::time::Instant;

use super::{event, failed, hub_mcp_server, is_auth_retryable, is_limit, shell_outside_actions, tool_summary, Events, HostError, Settings};
use crate::util::{iso, log, py_dumps, text_of};

pub const STREAM_ARGS: &[&str] = &[
    "--input-format", "stream-json", "--replay-user-messages", "--output-format", "stream-json", "--verbose",
    "--include-partial-messages", "--permission-mode", "bypassPermissions",
];
const EFFORTS: &[&str] = &["low", "medium", "high", "xhigh", "max"];
const STOP_TIMEOUT: Duration = Duration::from_secs(10);
/// How long a turn waits, after a `result`, with a steer unread and nothing at all on stdout.
pub const STEER_READ_TIMEOUT_S: u64 = 120;
/// How long a turn waits for Claude's `result` after Claude reported a usage limit and did no further model work.
const LIMIT_SETTLE: Duration = if cfg!(test) { Duration::from_millis(500) } else { Duration::from_secs(20) };
const STEER_MIN_VERSION: (u64, u64, u64) = (2, 1, 296);
const AUTOCOMPACT_BUFFER: i64 = 33_000;
const AUTOCOMPACT_RANGE: (i64, i64) = (100_000, 1_000_000);
/// The tools that start a subagent: `Agent`, called `Task` by older Claude Code.
const AGENT_TOOLS: &[&str] = &["Agent", "Task"];
/// The tools whose every call keeps running in the background.
const BACKGROUND_TOOLS: &[&str] = &["Monitor"];
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

/// A model request's usage from a stream-json line: (its message id, when the line names one; uncached input with cache
/// writes, cache reads, output). `message_start` and each content block's `assistant` line carry the request's usage so
/// far; the `message_delta` that ends it names no id and carries the final output count.
fn request_usage(msg: &Value) -> Option<(Option<&str>, [i64; 3])> {
    let ev = &msg["event"];
    let (id, u) = match (msg["type"].as_str()?, ev["type"].as_str().unwrap_or_default()) {
        ("assistant", _) => (msg["message"]["id"].as_str(), &msg["message"]["usage"]),
        ("stream_event", "message_start") => (ev["message"]["id"].as_str(), &ev["message"]["usage"]),
        ("stream_event", "message_delta") => (None, &ev["usage"]),
        _ => return None,
    };
    let cached = count(u, "cache_read_input_tokens");
    let tokens = [usage_tokens(u).0 - cached, cached, count(u, "output_tokens")];
    tokens.iter().any(|n| *n > 0).then_some((id.filter(|id| !id.is_empty()), tokens))
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
    /// The saved session was not on this computer and the thread restarted as a new one under the same id.
    restored: bool,
}

/// One process's stdin: what is still to write, whether it takes more, and which messages Claude has not read.
struct Pipe {
    lines: mpsc::UnboundedSender<Option<String>>,
    open: bool,
    first: String,
    unread: HashSet<String>,
    order: Vec<String>,
    /// The follow-ups written after the prompt, in order: a restart of a missing session writes them again.
    followups: Vec<String>,
    cost: f64,
    awaiting: Option<Instant>,
    timed_out: bool,
    /// Claude reported a usage limit: unless model work follows, the turn ends here, `result` or not.
    limit_at: Option<Instant>,
    limit_text: Option<String>,
    /// The session's own background tasks still running (subagents, shells, monitors), from Claude's last
    /// `background_tasks_changed`. Claude stays up until they end, so stdin stays open for steers meanwhile.
    background: usize,
    /// A `result` read every message written: once no background task runs, the turn is over.
    answered: bool,
    stopper: super::Stopper,
    /// The held background tasks as Claude last listed them.
    held: Vec<Value>,
    /// The tool call that started each background task, by task id (`task_started`).
    task_calls: HashMap<String, String>,
    /// Since when the answered turn has waited on nothing but background shells, with no model work.
    idle_since: Option<Instant>,
    /// How long it may (Settings::background_wait).
    wait: Option<Duration>,
    /// Claude's pid, for finding its shells.
    pid: Option<u32>,
    /// The turn ended on its wait: Claude was killed so its shells run on, and these are the ones kept.
    kept: Option<Vec<Value>>,
    /// Claude runs under a reaping parent that writes its shells' exit codes here.
    reaping: Option<std::path::PathBuf>,
}

impl Pipe {
    /// Writes a follow-up message after the prompt; Claude has not read it until it replays it.
    fn follow_up(&mut self, text: &str) {
        let mid = uuid::Uuid::new_v4().to_string();
        self.unread.insert(mid.clone());
        self.order.push(mid.clone());
        self.followups.push(text.to_string());
        self.answered = false;
        let _ = self.lines.send(Some(user_line(&mid, text)));
    }

    fn close(&mut self) {
        if self.open {
            self.open = false;
            let _ = self.lines.send(None);
        }
    }

    /// Closes stdin when the turn is over: every message read and answered, and no background task left.
    fn close_if_done(&mut self) {
        if self.answered && self.background == 0 && self.unread.is_empty() {
            self.close();
        }
    }

    /// The bot answered and nothing is left but shells it started in the background: no subagent works, no
    /// follow-up is unread.
    fn only_waiting(&self) -> bool {
        self.open && self.answered && self.unread.is_empty() && self.background > 0 && self.held.iter().all(is_shell)
    }

    /// When the turn stops waiting on its shells, if it only waits on them.
    fn wait_deadline(&self) -> Option<Instant> {
        self.idle_since.zip(self.wait).map(|(since, wait)| since + wait)
    }
}

/// The reaping parent's program and arguments up to the command it runs.
fn reaper(status: &std::path::Path) -> Vec<std::ffi::OsString> {
    let exe = std::env::current_exe().unwrap_or_else(|_| "tico-runner".into());
    vec![exe.into(), crate::reap::FLAG.into(), status.into(), "--".into()]
}

/// How much of a background command a kept shell carries.
const COMMAND_CHARS: usize = 300;

/// What a command that only watches CI waits for: `gh pr checks <n> --watch` a pull request's checks, `gh run watch
/// <id>` one workflow run. The hub's own check watch does the first without a process.
pub fn ci_watch(command: &str) -> Option<Value> {
    let words: Vec<&str> = command.split_whitespace().collect();
    let at = words.windows(2).position(|w| w[0] == "gh" && (w[1] == "pr" || w[1] == "run"))?;
    let rest = &words[at + 1..];
    // One command, not a watch followed by more work.
    if rest.iter().any(|w| ["&&", ";", "||", "|"].iter().any(|op| w.contains(op))) {
        return None;
    }
    let number = |from: &[&str]| from.iter().find(|w| !w.is_empty() && w.chars().all(|c| c.is_ascii_digit())).and_then(|w| w.parse::<u64>().ok());
    match rest {
        ["pr", "checks", args @ ..] if args.contains(&"--watch") => number(args).map(|n| json!({"pr": n})),
        ["run", "watch", args @ ..] => number(args).map(|n| json!({"run": n})),
        _ => None,
    }
}

/// A background task that is a command, not model work: a shell, or a monitor's command.
fn is_shell(task: &Value) -> bool {
    matches!(task["task_type"].as_str(), Some("local_bash") | Some("monitor"))
}

/// Every process's parent, by pid.
pub(crate) fn parents() -> HashMap<u32, u32> {
    let mut parent: HashMap<u32, u32> = HashMap::new();
    #[cfg(target_os = "linux")]
    for entry in std::fs::read_dir("/proc").into_iter().flatten().flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else { continue };
        let Ok(stat) = std::fs::read_to_string(entry.path().join("stat")) else { continue };
        // After the command's closing paren: state, then ppid.
        if let Some(ppid) = stat.rsplit_once(')').and_then(|(_, rest)| rest.split_whitespace().nth(1)).and_then(|p| p.parse().ok()) {
            parent.insert(pid, ppid);
        }
    }
    #[cfg(not(target_os = "linux"))]
    if let Ok(out) = std::process::Command::new("ps").args(["-A", "-o", "pid=,ppid="]).stderr(Stdio::null()).output() {
        for line in String::from_utf8_lossy(&out.stdout).lines() {
            let mut cols = line.split_whitespace().filter_map(|c| c.parse::<u32>().ok());
            if let (Some(pid), Some(ppid)) = (cols.next(), cols.next()) {
                parent.insert(pid, ppid);
            }
        }
    }
    parent
}

/// The file a process's standard output goes to.
pub fn stdout_of(pid: u32) -> Option<String> {
    #[cfg(target_os = "linux")]
    {
        std::fs::read_link(format!("/proc/{pid}/fd/1")).ok().map(|o| o.display().to_string())
    }
    #[cfg(not(target_os = "linux"))]
    {
        let out = std::process::Command::new("lsof").args(["-a", "-p", &pid.to_string(), "-d", "1", "-Fn"]).stderr(Stdio::null()).output().ok()?;
        String::from_utf8_lossy(&out.stdout).lines().find_map(|l| l.strip_prefix('n').map(str::to_string))
    }
}

/// What Claude (`claude`) still runs besides the shells it keeps (`kept`) and what they run: its MCP servers and any
/// other helper of its own, stopped with it so an ended turn leaves nothing behind. A job the bot detached itself
/// (`setsid`, `nohup ... &` from a command that has ended) has already left Claude's tree for the reaping parent's, and
/// is not here.
fn strays_of(claude: u32, kept: &[u32]) -> Vec<u32> {
    let parent = parents();
    // Whatever a kept shell runs under stays too: killing it would only orphan the shell.
    let mut over_kept = HashSet::new();
    for &k in kept {
        let mut at = k;
        while let Some(&p) = parent.get(&at).filter(|&&p| p != claude && p > 1) {
            over_kept.insert(p);
            at = p;
        }
    }
    let mut strays = vec![];
    for &pid in parent.keys().filter(|p| !over_kept.contains(*p)) {
        let mut at = pid;
        while let Some(&p) = parent.get(&at) {
            if kept.contains(&at) {
                break;
            }
            if p == claude {
                strays.push(pid);
                break;
            }
            if p <= 1 {
                break;
            }
            at = p;
        }
    }
    strays
}

/// The pid of each shell Claude (`claude`) runs for one of `tasks`, by the output file Claude points its stdout at
/// (`.../tasks/<task_id>.output`).
fn shells_of(claude: u32, tasks: &[Value]) -> Vec<Value> {
    let mut found = vec![];
    let parent = parents();
    let under = |mut pid: u32| {
        while let Some(&p) = parent.get(&pid) {
            if p == claude {
                return true;
            }
            if p <= 1 {
                break;
            }
            pid = p;
        }
        false
    };
    for (&pid, _) in parent.iter().filter(|(&pid, _)| under(pid)) {
        let Some(out) = stdout_of(pid) else { continue };
        for t in tasks {
            let id = t["task_id"].as_str().unwrap_or_default();
            if id.is_empty() || !out.ends_with(&format!("/tasks/{id}.output")) {
                continue;
            }
            // The task's own shell, not a command inside it writing to the same file.
            if parent.get(&pid).is_some_and(|&p| p != claude && stdout_of(p).as_deref() == Some(out.as_str())) {
                continue;
            }
            let mut kept = json!({"task_id": id, "description": t["description"], "pid": pid, "output_file": out});
            for key in ["command", "ci_watch"] {
                if !t[key].is_null() {
                    kept[key] = t[key].clone();
                }
            }
            found.push(kept);
        }
    }
    found
}

#[derive(Default)]
struct Usage {
    input: i64,
    output: i64,
    cached: i64,
    cost: Option<f64>,
    texts: Vec<String>,
    /// Each model request this turn, the bot's and its subagents', in the order Claude made them; true for the bot's own.
    requests: Vec<(String, [i64; 3], bool)>,
    /// The request each stream (the bot's, or a subagent's by its `parent_tool_use_id`) is in, for its `message_delta`.
    streaming: HashMap<String, String>,
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
    /// The status file of the reaping parent the last launched Claude runs under (crate::reap).
    reaping: Option<std::path::PathBuf>,
    /// How far each thread's transcript has been read for goal records.
    goal_offsets: HashMap<String, u64>,
    /// This turn's tool calls by `tool_use` id, the bot's own and its subagents'.
    tools: HashMap<String, ToolCall>,
    /// Threads whose session Claude compacted (a `compact_boundary`) since the runner last asked.
    compacted: HashSet<String>,
    /// What a resumed thread's turn gives a missing session restarted as new, in place of its prompt.
    full_prompts: HashMap<String, String>,
    /// What a shortened turn's prompt left out, written into the turn if Claude compacts the session during it.
    refresh: HashMap<String, String>,
    /// Each thread's context size after its last turn (Host::context_tokens).
    context: HashMap<String, i64>,
    /// Called each time a turn's pipe has just been published (ordering tests).
    #[cfg(test)]
    pipe_opened: Option<Arc<dyn Fn(&ClaudeHost) + Send + Sync>>,
}

/// A tool call reported as started, kept until its end is reported once.
#[derive(Clone)]
struct ToolCall {
    name: String,
    /// The `Agent` call whose subagent made this call; none for the bot's own.
    parent: Option<String>,
    /// For a subagent's start: what the hub is told about it.
    subagent: Option<Value>,
    /// A backgrounded call (a subagent, a shell or a monitor) returns at once; it ends with its `task_notification`.
    background: bool,
    ended: bool,
    /// A shell's or a monitor's command, clipped.
    command: Option<String>,
}

struct Shared {
    bot: String,
    cmd: Vec<String>,
    tx: Events,
    steer: std::sync::atomic::AtomicBool,
    autocompact: std::sync::atomic::AtomicBool,
    inner: Mutex<Inner>,
    capture: Mutex<Option<Arc<crate::capture::Capture>>>,
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
            capture: Mutex::new(None),
        }))
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn emit(&self, ev: Value) {
        let _ = self.0.tx.send(ev);
    }

    pub fn set_capture(&self, capture: Arc<crate::capture::Capture>) {
        capture.attach("claude");
        *self.0.capture.lock().unwrap_or_else(|p| p.into_inner()) = Some(capture);
    }

    fn capture(&self) -> Option<Arc<crate::capture::Capture>> {
        self.0.capture.lock().unwrap_or_else(|p| p.into_inner()).clone()
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
            // A turn that ended on its wait left its reaping parent for the shells it kept: they outlive the runner.
            if let Some(p) = inner.pipe.as_mut().filter(|p| p.kept.is_none()) {
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

    /// A host that is up and steers without a Claude process, for tests that feed its events themselves.
    #[cfg(test)]
    pub(crate) fn pretend_up(&self, steer: bool) {
        self.inner().up = true;
        self.0.steer.store(steer, std::sync::atomic::Ordering::Relaxed);
    }

    /// Claude's running turn on `thread`, as tests set it; Claude itself never says (crate::hosts::Host::active_turn).
    #[cfg(test)]
    pub(crate) fn running_turn(&self, thread: &str) -> Option<String> {
        self.inner().turn.clone().filter(|(t, _)| t == thread).map(|(_, u)| u)
    }

    #[cfg(test)]
    pub(crate) fn pretend_turn(&self, thread: &str, turn: &str) {
        self.inner().turn = Some((thread.into(), turn.into()));
    }

    pub fn start_thread(&self, settings: &Settings) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        self.inner().threads.insert(id.clone(), Thread { settings: settings.clone(), started: false, fork_from: None, session_id: None, restored: false });
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> String {
        self.inner().threads.insert(thread_id.into(), Thread { settings: settings.clone(), started: true, fork_from: None, session_id: None, restored: false });
        thread_id.into()
    }

    pub fn session_id(&self, thread_id: &str) -> String {
        self.inner().threads.get(thread_id).and_then(|t| t.session_id.clone()).unwrap_or_else(|| thread_id.to_string())
    }

    /// Whether the thread's saved session was not on this computer, so Claude began a new one under its id.
    pub fn restored(&self, thread_id: &str) -> bool {
        self.inner().threads.get(thread_id).is_some_and(|t| t.restored)
    }

    /// The session Claude said it runs `thread_id` as (its system init), once it has.
    pub fn confirmed(&self, thread_id: &str) -> Option<String> {
        self.inner().threads.get(thread_id).and_then(|t| t.session_id.clone())
    }

    /// Whether Claude compacted `thread_id`'s session since this was last asked.
    pub fn compacted(&self, thread_id: &str) -> bool {
        self.inner().compacted.remove(thread_id)
    }

    pub fn context_tokens(&self, thread_id: &str) -> Option<i64> {
        self.inner().context.remove(thread_id)
    }

    pub fn full_prompt(&self, thread_id: &str, text: Option<String>) {
        let mut inner = self.inner();
        match text {
            Some(t) => inner.full_prompts.insert(thread_id.into(), t),
            None => inner.full_prompts.remove(thread_id),
        };
    }

    pub fn compaction_refresh(&self, thread_id: &str, text: Option<String>) {
        let mut inner = self.inner();
        match text {
            Some(t) => inner.refresh.insert(thread_id.into(), t),
            None => inner.refresh.remove(thread_id),
        };
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
            argv.extend(["--setting-sources".into(), if t.settings.packet_only { "".into() } else { "project,local".into() }]);
        }
        argv.push("--strict-mcp-config".into());
        if t.settings.packet_only {
            argv.extend(["--tools".into(), "".into(), "--disable-slash-commands".into(),
                "--settings".into(), "{\"disableAllHooks\":true}".into()]);
        }
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

    /// Starts Claude for one turn: the process, its stderr tail, and its reaping parent's status file if it has one.
    fn launch(&self, thread_id: &str, t: &Thread, effort: Option<&str>) -> std::io::Result<(Child, crate::run_diagnostics::Stderr, Option<std::path::PathBuf>)> {
        let argv = self.argv(thread_id, t, effort);
        // Drain stderr independently; only the bounded, scrubbed tail is kept.
        // A turn that may end on its background wait runs under a reaping parent, which learns its shells' exit codes.
        let reaping = (cfg!(target_os = "linux") && t.settings.background_wait.is_some())
            .then(|| std::env::temp_dir().join(format!("tico-claude-reap-{}", uuid::Uuid::new_v4().simple())));
        let mut command = match &reaping {
            // A test binary runs the parent from one test, with everything it runs in the environment.
            Some(_) if cfg!(test) => tokio::process::Command::new(reaper(std::path::Path::new("")).remove(0)),
            Some(path) => {
                let mut c = crate::isolation::command_under(&reaper(path), &argv[0]);
                c.args(&argv[1..]);
                c
            }
            None => {
                let mut c = crate::isolation::command(&argv[0]);
                c.args(&argv[1..]);
                c
            }
        };
        // Claude, or its reaping parent with Claude under it, leads a group of its own: a stop ends what the turn
        // started too, and a background-wait end, which kills Claude alone, leaves the shells it keeps in that group.
        super::new_group(&mut command);
        #[cfg(test)]
        if reaping.is_some() {
            command.args(["reap::tests::as_parent", "--exact", "--test-threads=1", "-q"]);
        }
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .current_dir(&t.settings.cwd);
        command.env_clear().envs(turn_env(&t.settings));
        #[cfg(test)]
        if let Some(path) = &reaping {
            let mut args = vec![json!(path), json!("--")];
            args.extend(argv.iter().map(|a| json!(a)));
            command.env(crate::reap::tests::ARGS, Value::Array(args).to_string());
        }
        let mut child = command.spawn()?;
        let stderr = crate::run_diagnostics::Stderr::capture(&mut child, self.0.tx.clone(), &turn_env(&t.settings));
        Ok((child, stderr, reaping))
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
            let (child, stderr, reaping) = self.launch(thread_id, &t, effort).map_err(|e| failed(format!("could not start claude: {e}")))?;
            inner.reaping = reaping;
            inner.turn = Some((thread_id.into(), turn.clone()));
            inner.reply.insert(turn.clone(), String::new());
            inner.tools.clear();
            inner.context.remove(thread_id);
            (child, stderr)
        };
        log(&format!("claude[{}]: turn started pid {}", self.0.bot, child.id().map_or("?".into(), |p| p.to_string())));
        // `tool_events`: this run reports every tool call, so a run with none provably called none.
        self.emit(event("status", Some(thread_id), None, json!({"state": "active", "tool_events": true})));
        let host = self.clone();
        let (tid, t2, text, effort) = (thread_id.to_string(), turn.clone(), text.to_string(), effort.map(String::from));
        let reader = tokio::spawn(async move { host.read_loop(child, stderr, tid, t2, text, effort).await });
        self.inner().reader = Some(reader);
        Ok(turn)
    }

    /// Publishes the turn's pipe with the prompt and any carried follow-ups already queued, under one lock: a
    /// steer is let in only once the pipe is open, so it always lands after them.
    fn open_pipe(&self, tid: &str, stdin: Option<tokio::process::ChildStdin>, stopper: super::Stopper, pid: Option<u32>, text: &str, carried: Vec<String>) {
        let (tx, mut rx) = mpsc::unbounded_channel::<Option<String>>();
        let first = uuid::Uuid::new_v4().to_string();
        let _ = tx.send(Some(user_line(&first, text)));
        let mut stdin = stdin;
        let capture = self.capture();
        // Its own task: a prompt can be larger than the pipe's buffer, and nothing else waits on it.
        tokio::spawn(async move {
            while let Some(Some(line)) = rx.recv().await {
                let Some(s) = stdin.as_mut() else { break };
                if let Some(c) = &capture {
                    c.sent(&line);
                }
                if s.write_all(line.as_bytes()).await.is_err() || s.flush().await.is_err() {
                    break;
                }
            }
            drop(stdin.take());
        });
        let mut inner = self.inner();
        let wait = inner.threads.get(tid).and_then(|t| t.settings.background_wait);
        let reaping = inner.reaping.take();
        let mut pipe = Pipe {
            lines: tx,
            open: true,
            first: first.clone(),
            unread: HashSet::from([first.clone()]),
            order: vec![first],
            followups: vec![],
            cost: 0.0,
            awaiting: None,
            timed_out: false,
            limit_at: None,
            limit_text: None,
            background: 0,
            answered: false,
            stopper,
            held: vec![],
            task_calls: HashMap::new(),
            idle_since: None,
            wait,
            pid,
            kept: None,
            reaping,
        };
        for text in carried {
            pipe.follow_up(&text);
        }
        inner.pipe = Some(pipe);
    }

    async fn read_loop(&self, mut child: Child, mut stderr: crate::run_diagnostics::Stderr, tid: String, turn: String, mut text: String, effort: Option<String>) {
        let mut recovered = false;
        // What a restarted session is told after its prompt: the follow-ups the missing one never read.
        let mut requeued: Vec<String> = vec![];
        loop {
            let stdout = child.stdout.take();
            let stdin = child.stdin.take();
            let pid = child.id();
            let (stopper, exited) = super::own(child);
            self.open_pipe(&tid, stdin, stopper, pid, &text, std::mem::take(&mut requeued));
            #[cfg(test)]
            let opened = self.inner().pipe_opened.clone();
            #[cfg(test)]
            if let Some(opened) = opened {
                opened(self);
            }
            let mut result: Option<Value> = None;
            let mut saw_event = false;
            if let Some(stdout) = stdout {
                let mut lines = BufReader::new(stdout).lines();
                loop {
                    let deadline = self.inner().pipe.as_ref().and_then(|p| p.awaiting.into_iter().chain(p.limit_at).chain(p.wait_deadline()).min());
                    let next = match deadline {
                        Some(d) => tokio::select! {
                            l = lines.next_line() => Some(l),
                            _ = tokio::time::sleep_until(d) => None,
                        },
                        None => Some(lines.next_line().await),
                    };
                    let line = match next {
                        None => {
                            if self.end_waiting() {
                                continue;
                            }
                            let mut inner = self.inner();
                            if let Some(p) = inner.pipe.as_mut() {
                                if p.limit_at.is_some_and(|at| at <= Instant::now()) {
                                    // A limit with no `result` behind it: stdin stays open for steers, so Claude
                                    // can wait there with no model work at all. End the turn as limited.
                                    p.limit_at = None;
                                    p.awaiting = None;
                                    p.close();
                                    p.stopper.terminate(STOP_TIMEOUT);
                                    continue;
                                }
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
                    if let Some(c) = self.capture() {
                        c.received(&line);
                    }
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
                        if self.before_prompt(&msg) {
                            continue;
                        }
                        self.settle(&turn, &msg);
                        self.track_wait(&msg);
                        result = Some(msg);
                        continue;
                    } else {
                        saw_event = true;
                    }
                    self.track_wait(&msg);
                }
            }
            let (unread, followups, timed_out, limit_text) = {
                let mut inner = self.inner();
                match inner.pipe.as_mut() {
                    Some(p) => {
                        p.close();
                        p.awaiting = None;
                        p.limit_at = None;
                        (p.unread.iter().any(|m| *m != p.first), std::mem::take(&mut p.followups), p.timed_out, p.limit_text.take())
                    }
                    None => (false, vec![], false, None),
                }
            };
            let (kept, reaping) = self.inner().pipe.as_ref().map_or((false, false), |p| (p.kept.is_some(), p.reaping.is_some()));
            let rc = if kept && reaping {
                // Its parent stays for the shells it kept; the turn is over now.
                tokio::spawn(async move {
                    let _ = exited.await;
                });
                0
            } else {
                match exited.await {
                    Ok(status) => super::return_code(&status),
                    Err(_) => -1,
                }
            };
            // Killed on its wait, after its answer: the turn completed.
            let rc = if kept && rc == -libc::SIGKILL { 0 } else { rc };
            let clean = result.as_ref().is_some_and(|r| !crate::util::truthy(r.get("is_error"))) && rc == 0;
            if timed_out {
                self.emit(event("diagnostic", Some(&tid), Some(&turn), json!({"end_reason": format!("Timeout: follow-up read limit ({STEER_READ_TIMEOUT_S} seconds)")})));
            }
            let failure = if timed_out {
                Some(format!("Claude did not read a follow-up message within {STEER_READ_TIMEOUT_S} s; it was not applied"))
            } else if let Some(text) = limit_text.filter(|_| result.is_none()) {
                Some(text)
            } else if unread && clean {
                Some("Claude exited before reading a follow-up message; it was not applied".to_string())
            } else {
                None
            };
            stderr.drained().await;
            let tail = stderr_tail(&mut stderr);
            // Claude answers only with an error result when the session file is not on this computer: restart once
            // as a new session under the same id. Never after an event. A follow-up that went in meanwhile did nothing
            // there either, so the new session is given it again after its prompt.
            let missing = result.as_ref().is_some_and(|r| error_text(Some(r), rc, &tail).starts_with("No conversation found with session ID"));
            if missing && !recovered && !saw_event && rc != 0 {
                let retry = {
                    let mut inner = self.inner();
                    let up = inner.up && !inner.interrupted.contains(&turn);
                    match inner.threads.get_mut(&tid) {
                        Some(t) if up && !t.settings.require_existing_session && (t.started || t.fork_from.is_some()) => {
                            t.started = false;
                            t.fork_from = None;
                            t.session_id = None;
                            t.restored = true;
                            let t = t.clone();
                            self.launch(&tid, &t, effort.as_deref()).ok().map(|(c, s, r)| {
                                inner.reaping = r;
                                (c, s)
                            })
                        }
                        _ => None,
                    }
                };
                if let Some((c, s)) = retry {
                    child = c;
                    stderr = s;
                    recovered = true;
                    requeued = followups;
                    // The new session holds none of what the short prompt left out.
                    let full = {
                        let mut inner = self.inner();
                        inner.refresh.remove(&tid);
                        inner.full_prompts.remove(&tid)
                    };
                    if let Some(full) = full {
                        text = full;
                    }
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

    /// Starts or stops the clock on an answered turn that waits only on its background shells: any model work, a
    /// follow-up or a finished task stops it.
    fn track_wait(&self, msg: &Value) {
        let mut inner = self.inner();
        let Some(p) = inner.pipe.as_mut() else { return };
        let worked = matches!(msg["type"].as_str(), Some("assistant") | Some("stream_event"))
            || (msg["type"] == "user" && !crate::util::truthy(msg.get("isReplay")))
            || msg["subtype"] == "task_notification";
        if worked || !p.only_waiting() {
            p.idle_since = None;
        }
        if !worked && p.only_waiting() && p.idle_since.is_none() {
            p.idle_since = Some(Instant::now());
        }
    }

    /// The turn has waited its bound on nothing but its shells: it ends as completed and its shells run on. Claude is
    /// killed outright, since a stop lets it end them first.
    fn end_waiting(&self) -> bool {
        let (pid, held, waited) = {
            let mut inner = self.inner();
            let inner = &mut *inner;
            let Some(p) = inner.pipe.as_mut() else { return false };
            if !p.wait_deadline().is_some_and(|at| at <= Instant::now()) || p.kept.is_some() {
                return false;
            }
            if !p.only_waiting() {
                p.idle_since = None;
                return false;
            }
            // Taken under the lock that `steer` takes: from here a follow-up is refused and goes to the next turn,
            // never accepted into a Claude about to be killed.
            p.close();
            p.idle_since = None;
            p.kept = Some(vec![]);
            let mut held = p.held.clone();
            for t in held.iter_mut() {
                let call = p.task_calls.get(t["task_id"].as_str().unwrap_or_default());
                if let Some(c) = call.and_then(|c| inner.tools.get(c)).and_then(|c| c.command.clone()) {
                    t["ci_watch"] = ci_watch(&c).unwrap_or(Value::Null);
                    t["command"] = json!(c);
                }
            }
            // Under a reaping parent, the pid the runner holds is the parent's; Claude is its child.
            let claude = match &p.reaping {
                Some(path) => crate::reap::claude(path),
                None => p.pid,
            };
            (claude, held, p.wait.unwrap_or_default())
        };
        #[cfg(test)]
        tests::before_discovery(self);
        let kept = pid.map(|pid| shells_of(pid, &held)).unwrap_or_default();
        let kept_pids: Vec<u32> = kept.iter().filter_map(|k| k["pid"].as_u64()).map(|p| p as u32).collect();
        let strays = pid.map(|pid| strays_of(pid, &kept_pids)).unwrap_or_default();
        for stray in &strays {
            // SAFETY: a process under the Claude this turn started, found a moment ago; Claude has not exited.
            unsafe { libc::kill(*stray as i32, libc::SIGKILL) };
        }
        let ((tid, turn), reaping) = {
            let mut inner = self.inner();
            let ids = inner.turn.clone().unwrap_or_default();
            let mut reaping = None;
            if let Some(p) = inner.pipe.as_mut() {
                p.kept = Some(kept.clone());
                match &p.reaping {
                    Some(path) => {
                        reaping = Some(path.display().to_string());
                        p.stopper.signal(crate::reap::KEEP);
                    }
                    None => p.stopper.kill(),
                }
            }
            (ids, reaping)
        };
        let names: Vec<String> = held.iter().map(|t| text_of(&t["description"])).collect();
        log(&format!("claude[{}]: turn ended: background wait ({} of {} shells kept)", self.0.bot, kept.len(), held.len()));
        self.emit(event(
            "diagnostic",
            Some(&tid),
            Some(&turn),
            json!({"text": format!("Turn ended: background wait. The answer was in and only background commands were left: {}.", names.join("; ")),
                   "background_wait": {"waited_s": waited.as_secs(), "commands": held.len(), "kept": kept.len()}}),
        ));
        // This computer's side of it: pids, files and commands stay off the run's events.
        self.emit(event("background_kept", Some(&tid), Some(&turn), json!({"kept": kept, "status_file": reaping, "stopped": strays})));
        true
    }

    /// A resumed session first reports what its previous process left (a background task it no longer runs) and
    /// closes that with an empty, successful `result` of no model turns, before it reads this turn's prompt: that one
    /// answers nothing. An error result before the prompt (a session not on this computer, a refused sign-in) is the
    /// turn's outcome, and stays one.
    fn before_prompt(&self, result: &Value) -> bool {
        let empty = result["subtype"] == "success" && !crate::util::truthy(result.get("is_error")) && result["result"].as_str().is_none_or(str::is_empty);
        empty && result["num_turns"] == 0 && self.inner().pipe.as_ref().is_some_and(|p| p.unread.contains(&p.first))
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
            if crate::util::truthy(result.get("is_error")) {
                p.close();
            } else if p.unread.is_empty() {
                // With background work running Claude stays up and wakes itself when it ends; stdin stays open
                // so a follow-up meanwhile is read at once instead of waiting for that work.
                p.answered = true;
                p.close_if_done();
            } else {
                p.awaiting = Some(Instant::now() + Duration::from_secs(STEER_READ_TIMEOUT_S));
            }
        }
    }

    /// One stream-json line to host events; true when it is the turn's result.
    fn on_message(&self, msg: &Value, tid: &str, turn: &str) -> bool {
        if msg.is_object() {
            self.on_tools(msg, tid, turn);
        }
        if let Some((id, tokens)) = request_usage(msg) {
            // Some models price each request by its prompt length (backend/providers.py LONG_PROMPT), subagents' too.
            let stream = msg["parent_tool_use_id"].as_str().unwrap_or_default().to_string();
            let main = stream.is_empty();
            let mut inner = self.inner();
            let u = inner.usage.entry(turn.into()).or_default();
            let id = match id {
                Some(id) => {
                    u.streaming.insert(stream, id.to_string());
                    Some(id.to_string())
                }
                None => u.streaming.get(&stream).cloned(),
            };
            if let Some(id) = id {
                match u.requests.iter_mut().find(|(seen, _, _)| *seen == id) {
                    Some((_, had, _)) => (0..3).for_each(|i| had[i] = had[i].max(tokens[i])),
                    None => u.requests.push((id, tokens, main)),
                }
            }
        }
        if !msg.is_object() || crate::util::truthy(msg.get("parent_tool_use_id")) {
            return false; // subagent traffic is not the bot's reply
        }
        if let Some(status) = crate::goals::claude_status(msg) {
            self.emit(event("goal", Some(tid), None, Value::Object(status)));
            return false;
        }
        match msg["type"].as_str().unwrap_or_default() {
            "stream_event" => {
                if let Some(p) = self.inner().pipe.as_mut() {
                    p.limit_at = None;
                }
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
                let limit = limit_notice(msg, &text);
                let mut inner = self.inner();
                if limit {
                    inner.limited.insert(turn.into());
                }
                if let Some(p) = inner.pipe.as_mut() {
                    if limit {
                        p.limit_at = Some(Instant::now() + LIMIT_SETTLE);
                        p.limit_text = Some(text.trim().to_string()).filter(|t| !t.is_empty()).or(p.limit_text.take());
                    } else {
                        p.limit_at = None; // the model is working: whatever limit came first did not stop it
                    }
                }
                if !text.is_empty() && inner.last_message.get(turn) != Some(&(id.clone(), text.clone())) {
                    inner.last_message.insert(turn.into(), (id.clone(), text.clone()));
                    inner.reply.insert(turn.into(), text.clone());
                    drop(inner);
                    self.emit(event("message", Some(tid), Some(turn), json!({"text": text, "final": false, "item_id": id})));
                }
            }
            "system" if msg["subtype"] == "background_tasks_changed" => {
                if let Some(p) = self.inner().pipe.as_mut() {
                    // Claude does not wait for an ambient watcher or a task already awaited, so neither holds the turn.
                    let held = |t: &&Value| t["ambient"] != true && t["awaited"] != true;
                    p.held = msg["tasks"].as_array().map_or(vec![], |t| t.iter().filter(held).cloned().collect());
                    p.background = p.held.len();
                    p.close_if_done();
                }
            }
            "system" if msg["subtype"] == "compact_boundary" => {
                // Claude summarised the session, manually or on its own: what earlier turns said may be gone. A turn
                // whose prompt left out what the session already held is given it again at once, after each compaction.
                let mut inner = self.inner();
                inner.compacted.insert(tid.into());
                let running = inner.turn.as_ref().is_some_and(|(t, u)| t == tid && u == turn);
                if let Some(text) = inner.refresh.get(tid).cloned().filter(|_| running) {
                    if let Some(p) = inner.pipe.as_mut().filter(|p| p.open && p.limit_at.is_none()) {
                        p.follow_up(&text);
                    }
                }
            }
            "system" if msg["subtype"] == "init" => {
                let session = msg["session_id"].as_str().filter(|s| !s.is_empty()).unwrap_or(tid).to_string();
                if let Some(t) = self.inner().threads.get_mut(tid) {
                    // Claude owns the id now: resume from here on.
                    t.started = true;
                    t.fork_from = None;
                    t.session_id = Some(session.clone());
                }
                // The session is up: a new thread is saved, and the one it replaces let go, only now (execute.rs Fresh).
                self.emit(event("thread_confirmed", Some(tid), Some(turn), json!({"session_id": session})));
            }
            "rate_limit_event" => {
                let info = &msg["rate_limit_info"];
                // Rejected with overage in use: extra usage pays and the model keeps working.
                if info["status"] == "rejected" && info["isUsingOverage"] != true {
                    let mut inner = self.inner();
                    inner.limited.insert(turn.into());
                    if let Some(p) = inner.pipe.as_mut() {
                        p.limit_at.get_or_insert(Instant::now() + LIMIT_SETTLE);
                        p.limit_text.get_or_insert_with(|| "Claude usage limit reached".into());
                    }
                }
                for payload in rate_limit_windows(info) {
                    self.emit(event("rate_limits", Some(tid), Some(turn), payload));
                }
            }
            "result" => return true,
            _ => {}
        }
        false
    }

    /// `tool` events from one stream-json line: a call starting (`tool_use`), moving to the background
    /// (`task_started`), ending (`tool_result`), or a backgrounded call finishing (`task_notification`). A subagent's
    /// start and end carry `subagent`; a background call's carry `background`.
    fn on_tools(&self, msg: &Value, tid: &str, turn: &str) {
        let parent = msg["parent_tool_use_id"].as_str().filter(|p| !p.is_empty());
        let blocks = || msg["message"]["content"].as_array().into_iter().flatten();
        if msg["subtype"] == "task_started" {
            if let (Some(task), Some(call), Some(p)) = (msg["task_id"].as_str(), msg["tool_use_id"].as_str(), self.inner().pipe.as_mut()) {
                p.task_calls.insert(task.into(), call.into());
            }
        }
        match (msg["type"].as_str().unwrap_or_default(), msg["subtype"].as_str().unwrap_or_default()) {
            ("assistant", _) => {
                for b in blocks().filter(|b| b["type"] == "tool_use") {
                    self.tool_started(b, parent, tid, turn);
                }
            }
            ("user", _) => {
                for b in blocks().filter(|b| b["type"] == "tool_result") {
                    let status = if crate::util::truthy(b.get("is_error")) { "failed" } else { "completed" };
                    self.tool_ended(b["tool_use_id"].as_str().unwrap_or_default(), status, false, &Value::Null, tid, turn);
                }
            }
            ("system", "task_started") if msg["is_backgrounded"] == true => {
                let id = msg["tool_use_id"].as_str().unwrap_or_default();
                let moved = match self.inner().tools.get_mut(id) {
                    Some(call) if !call.background && !call.ended => {
                        call.background = true;
                        Some(call.clone())
                    }
                    _ => None,
                };
                // A call that did not ask for the background at its start was moved there while it ran.
                if let Some(call) = moved {
                    let fields = json!({"tool": call.name, "status": "backgrounded", "item_id": id, "parent_id": call.parent,
                                        "background": true});
                    self.emit(event("tool", Some(tid), Some(turn), fields));
                }
            }
            ("system", "task_notification") => {
                let status = match msg["status"].as_str().unwrap_or_default() {
                    "completed" => "completed",
                    "failed" | "error" => "failed",
                    _ => "interrupted",
                };
                self.tool_ended(msg["tool_use_id"].as_str().unwrap_or_default(), status, true, &msg["usage"], tid, turn);
            }
            _ => {}
        }
    }

    fn tool_started(&self, block: &Value, parent: Option<&str>, tid: &str, turn: &str) {
        let id = block["id"].as_str().unwrap_or_default();
        let name = block["name"].as_str().filter(|n| !n.is_empty()).unwrap_or("tool");
        let input = &block["input"];
        let background = input.get("run_in_background") == Some(&Value::Bool(true)) || BACKGROUND_TOOLS.contains(&name);
        let subagent = AGENT_TOOLS.contains(&name).then(|| {
            json!({"id": id, "parent_id": parent, "type": input.get("subagent_type").cloned().unwrap_or(Value::Null),
                   "model": input.get("model").cloned().unwrap_or(Value::Null), "status": "started"})
        });
        {
            let mut inner = self.inner();
            if id.is_empty() || inner.tools.contains_key(id) {
                return; // Claude repeats an assistant message as it grows; a call starts once
            }
            let command = input["command"].as_str().map(|c| c.chars().take(COMMAND_CHARS).collect());
            let call = ToolCall { name: name.into(), parent: parent.map(String::from), subagent: subagent.clone(), background, ended: false, command };
            inner.tools.insert(id.into(), call);
        }
        // A tool server's arguments are its own: a `path` or `command` there can carry anything.
        let summary = if name.starts_with("mcp__") { String::new() } else { tool_summary(input) };
        let mut fields = json!({"tool": name, "status": "started", "item_id": id, "summary": summary, "parent_id": parent});
        if name == "Bash" {
            if let Some(command) = input["command"].as_str() {
                fields["outside_actions"] = json!(shell_outside_actions(command));
            }
        }
        if let Some(s) = subagent {
            fields["subagent"] = s;
        }
        if background {
            fields["background"] = json!(true);
        }
        self.emit(event("tool", Some(tid), Some(turn), fields));
    }

    fn tool_ended(&self, id: &str, status: &str, notified: bool, usage: &Value, tid: &str, turn: &str) {
        let call = {
            let mut inner = self.inner();
            match inner.tools.get_mut(id) {
                Some(c) if !c.ended && (notified || !c.background) => {
                    c.ended = true;
                    c.clone()
                }
                _ => return,
            }
        };
        let mut fields = json!({"tool": call.name, "status": status, "item_id": id, "parent_id": call.parent});
        if call.background {
            fields["background"] = json!(true);
        }
        if let Some(mut s) = call.subagent {
            s["status"] = json!(status);
            for key in ["tool_uses", "duration_ms"] {
                if let Some(v) = usage.get(key) {
                    s[key] = v.clone();
                }
            }
            fields["subagent"] = s;
        }
        self.emit(event("tool", Some(tid), Some(turn), fields));
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
            let usage = inner.usage.remove(turn);
            if let Some(u) = usage.as_ref() {
                // The newest of the bot's own requests is the session's context; without them, the turn's whole usage
                // is more than it, never less.
                let last = u.requests.iter().rev().find(|(_, _, main)| *main).map_or(u.input + u.output, |(_, r, _)| r[0] + r[1] + r[2]);
                if last > 0 {
                    inner.context.insert(tid.into(), last);
                }
            }
            (inner.interrupted.contains(turn), usage, reply, limited)
        };
        if let Some(u) = usage.as_ref().filter(|u| u.input + u.output > 0) {
            self.emit(event(
                "tokens",
                Some(tid),
                Some(turn),
                json!({"input": u.input, "output": u.output, "total": u.input + u.output, "cost_usd": u.cost,
                       "usage": {"input": u.input, "cached": u.cached, "output": u.output,
                                 // The fourth count marks the bot's own requests (1) from its subagents' (0): the
                                 // newest of the bot's own is the session's context size (backend/topic_change.py).
                                 "requests": u.requests.iter().map(|(_, r, main)| json!([r[0], r[1], r[2], i64::from(*main)]))
                                     .collect::<Vec<_>>()}}),
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
            let process_exit = failure.is_none() && (rc < 0 || result.is_none() && rc != 0);
            let error = failure.unwrap_or_else(|| error_text(result.as_ref(), rc, stderr));
            let mut failure = event(
                "turn_failed",
                Some(tid),
                Some(turn),
                json!({"error": error, "limit": limited || is_limit(&error), "auth_retry": is_auth_retryable(&error)}),
            );
            if process_exit { failure["exit_code"] = json!(rc); failure["process_exit"] = json!(true); }
            self.emit(failure);
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
            // Not once Claude reported a limit: the turn is ending, so the input goes to the next one.
            Some(p) if running && p.open && p.limit_at.is_none() => {
                p.follow_up(text);
                Ok(())
            }
            _ => Err(HostError::SteerRefused(format!("claude turn {turn_id} is over or ending"))),
        }
    }

    /// Whether a replayed message was matched by order rather than by the uuid written (replay tests).
    #[cfg(test)]
    pub fn replay_fallback_used(&self) -> bool {
        self.inner().replay_fallback_logged
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

/// The usage windows of a `rate_limit_event` as `rate_limits` payloads. Claude Code 2.1.30x adds `unifiedWindows`
/// (`five_hour`, `seven_day`), each with a `utilization` from 0 to 1; that field is undocumented, so a window whose
/// utilization is missing or out of range reads unknown (null), never 0 or 100, and any other window name is left out.
/// Without it (an older CLI) only `rateLimitType`'s window goes, with no percentage.
fn rate_limit_windows(info: &Value) -> Vec<Value> {
    let minutes = |name: &str| match name {
        "five_hour" => Some(300),
        "seven_day" => Some(7 * 24 * 60),
        _ => None,
    };
    let named = info["rateLimitType"].as_str().unwrap_or_default();
    let status = |name: &str, own: &Value| match own.as_str() {
        Some(s) => json!(s),
        None if name == named => info.get("status").cloned().unwrap_or(Value::Null),
        None => Value::Null,
    };
    let mut out = vec![];
    let mut covered = false;
    for (name, window) in info["unifiedWindows"].as_object().into_iter().flatten() {
        let Some(mins) = minutes(name).filter(|_| window.is_object()) else { continue };
        let used = window["utilization"].as_f64().filter(|u| window["utilization"].is_number() && u.is_finite() && (0.0..=1.0).contains(u));
        let reset = if window["resetsAt"].is_null() && name == named { &info["resetsAt"] } else { &window["resetsAt"] };
        covered |= name == named;
        out.push(json!({"used_percent": used.map(|u| (u * 10000.0).round() / 100.0), "window_minutes": mins,
                        "resets_at": iso(reset), "status": status(name, &window["status"])}));
    }
    if !covered {
        let window = minutes(named).map_or(Value::Null, |m| json!(m));
        out.push(json!({"used_percent": null, "window_minutes": window, "resets_at": iso(&info["resetsAt"]),
                        "status": info.get("status").cloned().unwrap_or(Value::Null)}));
    }
    out
}

/// Claude Code's own notice that the subscription is out of usage: an assistant message it writes itself
/// (`error`, or the `<synthetic>` model), never the bot's words about limits.
fn limit_notice(msg: &Value, text: &str) -> bool {
    let error = msg["error"].as_str().unwrap_or_default();
    matches!(error, "rate_limit" | "billing_error") || ((!error.is_empty() || msg["message"]["model"] == "<synthetic>") && is_limit(text))
}

fn session_of(thread_id: &str, t: &Thread) -> String {
    t.session_id.clone().unwrap_or_else(|| thread_id.to_string())
}

/// This exact session's transcript: other bots' and people's transcripts stay private.
fn transcript(settings: &Settings, session: &str) -> Option<std::path::PathBuf> {
    // Where the turn's Claude keeps it: its HOME's .claude, as turn_env never lets CLAUDE_CONFIG_DIR through.
    let home = turn_env(settings).remove("HOME").unwrap_or_default();
    let dir = std::path::Path::new(&home).join(".claude");
    let name = format!("{session}.jsonl");
    std::fs::read_dir(dir.join("projects")).ok()?.flatten().map(|e| e.path().join(&name)).find(|p| p.is_file())
}

fn stderr_tail(file: &mut crate::run_diagnostics::Stderr) -> String { file.tail() }

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
        return crate::run_diagnostics::process_reason(rc, stderr);
    }
    format!("claude exited {rc}")
}

/// The turn's environment (hosts/claude.py _env): the given one, else this process's. No turn keeps Claude's auto
/// memory: what a bot remembers lives in its repository's memory/, where its owner sees and edits it (Dev 301), never
/// in a MEMORY.md under this computer's ~/.claude. No turn loads the claude.ai connectors.
fn turn_env(settings: &Settings) -> crate::env::Env {
    let mut env: crate::env::Env = if settings.env.is_empty() { std::env::vars().collect() } else { settings.env.clone() };
    env.insert("CLAUDE_CODE_DISABLE_AUTO_MEMORY".into(), "1".into());
    if settings.packet_only {
        env.insert("CLAUDE_CODE_DISABLE_CLAUDE_MDS".into(), "1".into());
        env.insert("CLAUDE_CODE_DISABLE_CRON".into(), "1".into());
    }
    // Belt and braces: `--strict-mcp-config` already skips the claude.ai connectors.
    env.insert("ENABLE_CLAUDEAI_MCP_SERVERS".into(), "false".into());
    // The login and the session files live under HOME; a trimmed environment must keep it.
    if env.get("HOME").is_none_or(|h| h.is_empty()) {
        if let Ok(home) = std::env::var("HOME") {
            env.insert("HOME".into(), home);
        }
    }
    // Claude reads CLAUDE_CONFIG_DIR ahead of HOME: one inherited from this process (or brought by a grant) would run
    // the turn on some other folder's login, not the one its sign-in and readiness check wrote under HOME.
    env.remove("CLAUDE_CONFIG_DIR");
    env
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packet_turns_keep_the_login_but_drop_memory_hooks_and_tools() {
        let host = ClaudeHost::new("synthetic", vec!["claude".into()], tokio::sync::mpsc::unbounded_channel().0);
        let settings = Settings { packet_only: true, shared: true, ..Settings::default() };
        let thread = host.start_thread(&settings);
        let argv = host.argv(&thread, &host.inner().threads[&thread].clone(), Some("low"));
        assert!(argv.windows(2).any(|w| w == ["--tools", ""]));
        assert!(argv.windows(2).any(|w| w == ["--setting-sources", ""]));
        assert!(argv.contains(&"{\"disableAllHooks\":true}".into()));
        assert_eq!(turn_env(&settings)["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1");
        assert!(!argv.contains(&"--bare".into()), "subscription login must remain available");
    }

    #[tokio::test]
    async fn a_crashed_turn_keeps_stderr_out_of_failure_events() {
        let dir = std::env::temp_dir().join(format!("claude-stderr-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("claude");
        crate::util::write_script(&path, "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) exit 0;; esac\nIFS= read -r line\nprintf '%3000s\\nprivate-stderr-marker\\n' x >&2\nkill -TERM $$\n");
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        let thread = host.start_thread(&settings);
        host.start_turn(&thread, "hello", None).unwrap();
        let mut events = Vec::new();
        loop {
            let ev = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await.unwrap().unwrap();
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            events.push(ev);
            if idle { break; }
        }
        assert!(events.iter().any(|e| e["stderr_tail"].as_str().is_some_and(|t| t.contains("private-stderr-marker"))));
        let failure = events.iter().find(|e| e["kind"] == "turn_failed").unwrap();
        assert_eq!(failure["error"], "Process exited (signal 15)");
        assert!(!failure.to_string().contains("private-stderr-marker"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// A `claude` that writes a fixture once the prompt arrives and exits when stdin closes, as Claude does.
    fn fake_claude(dir: &std::path::Path) -> String {
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\ncat \"$FIXTURE\"\ncat >/dev/null\n",
        );
        path.display().to_string()
    }

    #[tokio::test]
    async fn every_claude_fixture_gives_the_python_hosts_events() {
        let dir = std::env::temp_dir().join(format!("fake-claude-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake_claude(&dir);
        for (path, mut expected) in crate::hosts::fixtures::cases("claude") {
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
            // These shared Python snapshots contain read-only shell commands. Rust adds recovery labels.
            for event in &mut expected {
                if event["tool"] == "Bash" && event["status"] == "started" {
                    event["outside_actions"] = json!([]);
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
    fn recovery_shell_events_match_the_shared_api_fixture() {
        let cases: Vec<Value> = serde_json::from_str(include_str!("../../tests/fixtures/recovery-shell-events.json")).unwrap();
        for case in cases {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = ClaudeHost::new("acme", vec![], tx);
            host.tool_started(&json!({"id": "tool-1", "name": "Bash", "input": {"command": case["command"]}}), None, "th", "tu");
            let got = rx.try_recv().unwrap();
            assert_eq!(got, case["claude"], "{}", case["command"]);
            assert!(!got.to_string().contains("synthetic-private-marker"));
        }
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
        // Every bot's turn, with branches or not, and over an inherited setting that would turn it on.
        assert_eq!(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1");
        settings.env.insert("CLAUDE_CODE_DISABLE_AUTO_MEMORY".into(), "0".into());
        assert_eq!(turn_env(&settings)["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1");
        assert_eq!(turn_env(&Settings { shared: true, ..settings })["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_turn_never_runs_claude_on_an_inherited_or_granted_config_dir() {
        const CHILD: &str = "TICO_TEST_TURN_CONFIG_DIR";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own, whose environment points Claude at a folder outside the turn's HOME.
            let root = std::env::temp_dir().join(format!("tico-turn-config-dir-{}", uuid::Uuid::new_v4().simple()));
            let out = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "hosts::claude::tests::a_turn_never_runs_claude_on_an_inherited_or_granted_config_dir", "--nocapture"])
                .env(CHILD, &root).env("HOME", root.join("home")).env("CLAUDE_CONFIG_DIR", root.join("outside")).output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        // The same session's transcript in both folders: the turn's is the one under its HOME.
        for dir in ["home/.claude", "outside", "profile/.claude"] {
            std::fs::create_dir_all(root.join(dir).join("projects/p")).unwrap();
            std::fs::write(root.join(dir).join("projects/p/s1.jsonl"), dir).unwrap();
        }
        // No environment given: this process's, without its CLAUDE_CONFIG_DIR.
        let bare = Settings::default();
        let env = turn_env(&bare);
        assert_eq!(env["HOME"], root.join("home").display().to_string());
        assert!(!env.contains_key("CLAUDE_CONFIG_DIR"), "{env:?}");
        assert_eq!(transcript(&bare, "s1"), Some(root.join("home/.claude/projects/p/s1.jsonl")));
        // A profile's environment that carries one anyway (an inherited or granted value): left out too.
        let mut given = Settings::default();
        given.env.insert("HOME".into(), root.join("profile").display().to_string());
        given.env.insert("CLAUDE_CONFIG_DIR".into(), root.join("outside").display().to_string());
        let env = turn_env(&given);
        assert_eq!(env["HOME"], root.join("profile").display().to_string());
        assert!(!env.contains_key("CLAUDE_CONFIG_DIR"), "{env:?}");
        assert_eq!(transcript(&given, "s1"), Some(root.join("profile/.claude/projects/p/s1.jsonl")));
    }

    /// Runs one turn on a `claude` that writes `first`, waits a second, writes `then`, and exits when stdin closes.
    async fn limit_turn(first: &str, then: &str, steer_after_first: bool) -> (Vec<Value>, Option<bool>) {
        let dir = std::env::temp_dir().join(format!("claude-limit-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("first.jsonl"), first).unwrap();
        std::fs::write(dir.join("then.jsonl"), then).unwrap();
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\ncat first.jsonl\nsleep 1\ncat then.jsonl\ncat >/dev/null\n",
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.start_thread(&settings);
        let turn = host.start_turn(&thread, "hello", None).unwrap();
        let (mut got, mut steered) = (vec![], None);
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            if steer_after_first && steered.is_none() && ev["kind"] == "message" {
                steered = Some(host.steer(&thread, &turn, "one more thing").is_ok());
            }
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        std::fs::remove_dir_all(dir).unwrap();
        (got, steered)
    }

    /// Runs one turn on a `claude` that writes `first` (a background task starts, then a `result`), then writes `then`
    /// once a steer arrives on stdin, or `idle` after a second with none, and exits when stdin closes. With `steer`,
    /// one goes in after the first `result` was read.
    async fn background_turn(first: &str, then: &str, idle: &str, steer: bool) -> (Vec<Value>, Option<bool>) {
        let dir = std::env::temp_dir().join(format!("claude-bg-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        for (name, text) in [("first.jsonl", first), ("then.jsonl", then), ("idle.jsonl", idle)] {
            std::fs::write(dir.join(name), text).unwrap();
        }
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\ncat first.jsonl\nif [ -s then.jsonl ]; then IFS= read -r steer && cat then.jsonl; else sleep 1; cat idle.jsonl; fi\ncat >/dev/null\n",
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.start_thread(&settings);
        let turn = host.start_turn(&thread, "hello", None).unwrap();
        let mut steered = None;
        if steer {
            // Once the first `result` is read: the turn's own answer is in, its background task still runs.
            let deadline = Instant::now() + Duration::from_secs(10);
            while !host.inner().pipe.as_ref().is_some_and(|p| p.answered || !p.open) && Instant::now() < deadline {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            steered = Some(host.steer(&thread, &turn, "Did you delegate this?").is_ok());
        }
        let mut got = vec![];
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        std::fs::remove_dir_all(dir).unwrap();
        (got, steered)
    }

    const BG_STARTED: &str = r#"{"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "a1", "task_type": "local_agent", "description": "Build concept A"}]}"#;
    const BG_DONE: &str = r#"{"type": "system", "subtype": "background_tasks_changed", "tasks": []}"#;

    fn result_line(text: &str) -> String {
        format!(r#"{{"type": "result", "subtype": "success", "is_error": false, "result": "{text}", "usage": {{"input_tokens": 1, "output_tokens": 1}}}}"#)
    }

    fn assistant_line(id: &str, text: &str) -> String {
        format!(r#"{{"type": "assistant", "message": {{"id": "{id}", "content": [{{"type": "text", "text": "{text}"}}]}}}}"#)
    }

    #[tokio::test]
    async fn a_follow_up_is_answered_while_background_work_runs() {
        let first = format!("{BG_STARTED}\n{}\n{}\n", assistant_line("m1", "Three builders are working."), result_line("Three builders are working."));
        let echo = r#"{"type": "user", "isReplay": true, "message": {"role": "user", "content": "Did you delegate this?"}}"#;
        let then = format!("{echo}\n{}\n{}\n{BG_DONE}\n", assistant_line("m2", "No, I am building them myself."), result_line("No, I am building them myself."));
        let (got, steered) = background_turn(&first, &then, "", true).await;
        assert_eq!(steered, Some(true), "{got:?}");
        let last = got.iter().rev().find(|e| e["kind"] == "message" && e["final"] == true).unwrap();
        assert_eq!(last["text"], "Three builders are working.\n\nNo, I am building them myself.");
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
    }

    #[tokio::test]
    async fn the_turn_ends_when_its_background_work_does() {
        let first = format!("{BG_STARTED}\n{}\n", result_line("Started."));
        let (got, _) = background_turn(&first, "", &format!("{BG_DONE}\n"), false).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
        // An ambient watcher alone does not hold it: Claude does not wait for one.
        let ambient = r#"{"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "w1", "task_type": "monitor_ws", "ambient": true}]}"#;
        let (got, _) = background_turn(&format!("{ambient}\n{}\n", result_line("Watching.")), "", "", false).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
        // A turn with no background task ends at its `result`, as before.
        let (got, _) = background_turn(&format!("{}\n", result_line("Done.")), "", "", false).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
    }

    /// Runs one turn, waiting at most `wait` on background shells, on a `claude` that starts a real shell writing
    /// to `tasks/s1.output` (its pid in `bg.pid`), writes `first`, then runs `rest` and never exits by itself. Returns
    /// the events, how long the turn took, and the shell's pid.
    async fn wait_turn(first: &str, rest: &str, wait: Duration) -> (Vec<Value>, Duration, i32) {
        wait_turn_as("acme", first, rest, wait).await
    }

    async fn wait_turn_as(bot: &str, first: &str, rest: &str, wait: Duration) -> (Vec<Value>, Duration, i32) {
        let dir = std::env::temp_dir().join(format!("claude-wait-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("tasks")).unwrap();
        std::fs::write(dir.join("first.jsonl"), first).unwrap();
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            &format!("#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\nsleep 30 > tasks/s1.output 2>&1 &\necho $! > bg.pid\ncat first.jsonl\n{rest}\nwhile :; do sleep 1 >/dev/null; done\n"),
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new(bot, vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let mut settings = Settings { cwd: dir.display().to_string(), background_wait: Some(wait), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.start_thread(&settings);
        let started = Instant::now();
        host.start_turn(&thread, "hello", None).unwrap();
        let mut got = vec![];
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        let took = started.elapsed();
        let pid: i32 = std::fs::read_to_string(dir.join("bg.pid")).unwrap().trim().parse().unwrap();
        host.stop().await;
        std::fs::remove_dir_all(dir).unwrap();
        (got, took, pid)
    }

    const SHELL_STARTED: &str = r#"{"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "s1", "task_type": "local_bash", "description": "Run the full suite"}]}"#;

    fn alive(pid: i32) -> bool {
        // SAFETY: signal 0 only checks the pid.
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[tokio::test]
    async fn an_answered_turn_waiting_only_on_its_shell_ends_and_the_shell_runs_on() {
        let first = format!("{SHELL_STARTED}\n{}\n{}\n", assistant_line("m1", "Suite running; I'll wait for the notification."), result_line("Suite running; I'll wait for the notification."));
        let (got, took, pid) = wait_turn(&first, "", Duration::from_millis(300)).await;
        let survived = alive(pid);
        // SAFETY: the shell this test's fake started.
        unsafe { libc::kill(pid, libc::SIGTERM) };
        #[cfg(target_os = "linux")]
        {
            // Its parent stayed for it and has its exit code.
            let kept = got.iter().find(|e| e["kind"] == "background_kept").expect("the kept shells");
            let status = std::path::PathBuf::from(kept["status_file"].as_str().unwrap());
            let deadline = Instant::now() + Duration::from_secs(5);
            while !crate::reap::statuses(&status).contains_key(&(pid as u32)) && Instant::now() < deadline {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            assert_eq!(crate::reap::statuses(&status).get(&(pid as u32)), Some(&-libc::SIGTERM));
            let _ = std::fs::remove_file(status);
        }
        assert!(got.iter().any(|e| e["kind"] == "turn_completed" && e["status"] == "completed"), "{got:?}");
        assert!(!got.iter().any(|e| e["kind"] == "turn_failed"), "{got:?}");
        assert!(took < Duration::from_secs(5), "{took:?}");
        let ended = got.iter().find(|e| e["kind"] == "diagnostic" && e["background_wait"].is_object()).expect("a background-wait event");
        assert_eq!(ended["background_wait"]["commands"], 1);
        assert!(survived, "the shell was killed with the turn");
        assert_eq!(ended["background_wait"]["kept"], 1, "{ended}");
        assert_eq!(got.iter().find(|e| e["kind"] == "background_kept").unwrap()["kept"][0]["pid"], pid);
    }

    #[tokio::test]
    async fn a_turn_ended_on_its_wait_leaves_no_helper_of_claudes_running_but_the_bots_own_detached_job() {
        let pids = std::env::temp_dir().join(format!("claude-strays-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&pids).unwrap();
        let first = format!("{SHELL_STARTED}\n{}\n", result_line("Suite running."));
        // An MCP server that ignores its stdin closing, and a job the bot detached in a command that has ended.
        let p = pids.display();
        let rest = format!("sleep 30 </dev/null >/dev/null 2>&1 &\necho $! > {p}/mcp.pid\n( sh -c 'echo $$ > {p}/own.pid; exec sleep 30' </dev/null >/dev/null 2>&1 & )\nsleep 0.05");
        let (got, _, shell) = wait_turn(&first, &rest, Duration::from_millis(300)).await;
        let read = |name: &str| -> i32 { std::fs::read_to_string(pids.join(name)).unwrap().trim().parse().unwrap() };
        let (mcp, own) = (read("mcp.pid"), read("own.pid"));
        let deadline = Instant::now() + Duration::from_secs(3);
        while alive(mcp) && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let (mcp_gone, own_alive, shell_alive) = (!alive(mcp), alive(own), alive(shell));
        // SAFETY: processes this test's fake started.
        unsafe {
            libc::kill(own, libc::SIGKILL);
            libc::kill(shell, libc::SIGKILL);
        }
        std::fs::remove_dir_all(&pids).unwrap();
        assert!(got.iter().any(|e| e["background_wait"].is_object()), "{got:?}");
        assert!(mcp_gone, "Claude's own helper outlived the turn");
        assert!(own_alive, "the bot's detached job was killed");
        assert!(shell_alive, "the kept shell was killed");
        let stopped = &got.iter().find(|e| e["kind"] == "background_kept").unwrap()["stopped"];
        assert!(stopped.as_array().unwrap().contains(&json!(mcp)), "{stopped}");
    }

    #[tokio::test]
    async fn a_stopped_turn_leaves_nothing_it_started_running() {
        let dir = std::env::temp_dir().join(format!("claude-stop-group-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("claude");
        // A tool command, and one that ignores SIGTERM (a server that traps it), left running while Claude works.
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) exit 0;; esac\nIFS= read -r line\necho $$ > group.pid\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > tool.pid\nsh -c 'trap \"\" TERM; exec sleep 900' </dev/null >/dev/null 2>&1 &\necho $! > deaf.pid\nwhile :; do sleep 1 >/dev/null; done\n",
        );
        let (tx, _rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.start_thread(&settings);
        host.start_turn(&thread, "hello", None).unwrap();
        let read = |name: &str| std::fs::read_to_string(dir.join(name)).ok().and_then(|s| s.trim().parse::<i32>().ok());
        let deadline = Instant::now() + Duration::from_secs(10);
        while read("deaf.pid").is_none() && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let (group, tool, deaf) = (read("group.pid").unwrap(), read("tool.pid").unwrap(), read("deaf.pid").unwrap());
        // SAFETY: getpgid only reads.
        assert_eq!(unsafe { libc::getpgid(tool) }, group, "the tool runs in Claude's own group");
        host.stop().await;
        let gone = || {
            // SAFETY: signal 0 only checks whether any process of the group is left.
            unsafe { libc::kill(-group, 0) == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) }
        };
        let deadline = Instant::now() + Duration::from_secs(3);
        while !gone() && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let (tool_alive, deaf_alive, group_gone) = (alive(tool), alive(deaf), gone());
        // SAFETY: processes this test's fake started.
        unsafe {
            libc::kill(tool, libc::SIGKILL);
            libc::kill(deaf, libc::SIGKILL);
        }
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(!tool_alive, "the turn's tool command outlived the stop");
        assert!(!deaf_alive, "a process ignoring SIGTERM outlived the stop");
        assert!(group_gone, "something of the turn's group outlived the stop");
    }

    type Hook = Box<dyn Fn(&ClaudeHost) + Send + Sync>;

    /// Per bot, what a test runs in `end_waiting` once it has decided to end the turn and before it looks for shells.
    fn hooks() -> &'static Mutex<HashMap<String, Hook>> {
        static H: OnceLock<Mutex<HashMap<String, Hook>>> = OnceLock::new();
        H.get_or_init(Default::default)
    }

    pub(super) fn before_discovery(host: &ClaudeHost) {
        if let Some(hook) = hooks().lock().unwrap().get(&host.0.bot) {
            hook(host);
        }
    }

    #[tokio::test]
    async fn a_follow_up_sent_while_the_wait_ends_is_refused_for_the_next_turn_not_lost() {
        let refused = Arc::new(Mutex::new(None));
        let seen = refused.clone();
        hooks().lock().unwrap().insert(
            "acme-steer".into(),
            Box::new(move |host: &ClaudeHost| {
                let (thread, turn) = host.inner().turn.clone().unwrap();
                let sent = host.steer(&thread, &turn, "Also check the docs.");
                *seen.lock().unwrap() = Some(matches!(sent, Err(HostError::SteerRefused(_))));
            }),
        );
        let first = format!("{SHELL_STARTED}\n{}\n", result_line("Suite running."));
        let (got, _, shell) = wait_turn_as("acme-steer", &first, "", Duration::from_millis(300)).await;
        hooks().lock().unwrap().remove("acme-steer");
        // SAFETY: the shell this test's fake started.
        unsafe { libc::kill(shell, libc::SIGKILL) };
        // Refused, so the runner carries it to the next turn instead of acknowledging it into this one.
        assert_eq!(*refused.lock().unwrap(), Some(true), "{got:?}");
        assert!(got.iter().any(|e| e["kind"] == "turn_completed" && e["status"] == "completed"), "{got:?}");
    }

    #[tokio::test]
    async fn a_turn_still_working_or_running_a_subagent_waits_as_before() {
        // Model output keeps coming past the wait: the turn stays open until the shell ends.
        let first = format!("{SHELL_STARTED}\n{}\n", result_line("Started."));
        let delta = r#"{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "."}}}"#;
        let rest = format!("for i in 1 2 3 4 5 6 7 8; do echo '{delta}'; sleep 0.1; done\necho '{BG_DONE}'\nexit 0");
        let (got, took, pid) = wait_turn(&first, &rest, Duration::from_millis(300)).await;
        unsafe { libc::kill(pid, libc::SIGKILL) };
        assert!(!got.iter().any(|e| e["background_wait"].is_object()), "{got:?}");
        assert!(took >= Duration::from_millis(800), "{took:?}");
        // A subagent is model work: the turn waits for it however long it takes.
        let agent = format!("{BG_STARTED}\n{}\n", result_line("Started."));
        let (got, took, pid) = wait_turn(&agent, &format!("sleep 1\necho '{BG_DONE}'\nexit 0"), Duration::from_millis(300)).await;
        unsafe { libc::kill(pid, libc::SIGKILL) };
        assert!(!got.iter().any(|e| e["background_wait"].is_object()), "{got:?}");
        assert!(took >= Duration::from_secs(1), "{took:?}");
    }

    #[test]
    fn only_a_bare_ci_watch_is_one() {
        assert_eq!(ci_watch("gh pr checks 958 --watch --interval 30"), Some(json!({"pr": 958})));
        assert_eq!(ci_watch("cd /w && gh pr checks --watch 958"), Some(json!({"pr": 958})));
        assert_eq!(ci_watch("gh run watch 39278780247 --exit-status"), Some(json!({"run": 39278780247_u64})));
        assert_eq!(ci_watch("gh pr checks 958"), None);
        assert_eq!(ci_watch("gh pr checks 958 --watch && gh pr merge 958 --squash"), None);
        assert_eq!(ci_watch("python3 -m pytest -q"), None);
    }

    /// One turn on a `claude` that writes `first` and exits 1 the first time it runs, and `then` the next time.
    async fn error_first_turn(first: &str, then: &str, resumed: bool, require_existing_session: bool) -> Vec<Value> {
        let dir = std::env::temp_dir().join(format!("claude-err-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("first.jsonl"), first).unwrap();
        std::fs::write(dir.join("then.jsonl"), then).unwrap();
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\nif [ -f ran ]; then cat then.jsonl; cat >/dev/null; exit 0; fi\ntouch ran\ncat first.jsonl\nexit 1\n",
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.require_existing_session = require_existing_session;
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = if resumed { host.resume_thread(&uuid::Uuid::new_v4().to_string(), &settings) } else { host.start_thread(&settings) };
        host.start_turn(&thread, "hello", None).unwrap();
        let mut got = vec![];
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        std::fs::remove_dir_all(dir).unwrap();
        got
    }

    #[tokio::test]
    async fn a_zero_turn_error_before_the_prompt_is_still_the_turns_outcome() {
        // A resumed session that is not on this computer: restarted once as a new session, as before.
        let missing = r#"{"type": "result", "subtype": "error_during_execution", "is_error": true, "num_turns": 0, "errors": ["No conversation found with session ID: 5b1"], "usage": {"input_tokens": 0, "output_tokens": 0}}"#;
        let got = error_first_turn(&format!("{missing}\n"), &format!("{}\n{}\n", assistant_line("m1", "Recovered."), result_line("Recovered.")), true, false).await;
        assert!(got.iter().any(|e| e["kind"] == "diagnostic" && e["text"] == RESTORING), "{got:?}");
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
        // Any other error before the prompt, on a fresh session too, fails the turn with its own text.
        let refused = r#"{"type": "result", "subtype": "success", "is_error": true, "num_turns": 0, "result": "Invalid API key · Please run /login", "usage": {"input_tokens": 0, "output_tokens": 0}}"#;
        let got = error_first_turn(&format!("{refused}\n"), "", false, false).await;
        let failed = got.iter().find(|e| e["kind"] == "turn_failed").expect("a failed turn");
        assert!(text_of(&failed["error"]).contains("Invalid API key"), "{failed}");
    }

    #[tokio::test]
    async fn a_capacity_continuation_with_a_missing_claude_session_never_restarts() {
        let missing = r#"{"type":"result","is_error":true,"num_turns":0,"errors":["No conversation found with session ID: 5b1"]}"#;
        let then = format!("{}\n{}\n", assistant_line("m1", "Repeated work."), result_line("Repeated work."));
        let got = error_first_turn(&format!("{missing}\n"), &then, true, true).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_failed" && text_of(&e["error"]).contains("No conversation found")), "{got:?}");
        assert!(!got.iter().any(|e| e["kind"] == "turn_completed" || (e["kind"] == "diagnostic" && e["text"] == RESTORING)), "{got:?}");
    }

    #[tokio::test]
    async fn a_missing_session_restores_with_its_follow_ups_in_order() {
        // Oct 23: a follow-up went in before Claude answered "No conversation found"; the turn failed outright.
        let missing = r#"{"type": "result", "subtype": "error_during_execution", "is_error": true, "num_turns": 0, "errors": ["No conversation found with session ID: 5b1"], "usage": {"input_tokens": 0, "output_tokens": 0}}"#;
        let replay = |text: &str| format!(r#"{{"type": "user", "isReplay": true, "message": {{"role": "user", "content": "{text}"}}}}"#);
        let then = format!("{}\n{}\n{}\n{}\n{}\n", replay("hello"), replay("Also the QA result."), replay("Then this."), assistant_line("m1", "All read."), result_line("All read."));
        let dir = std::env::temp_dir().join(format!("claude-missing-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("first.jsonl"), format!("{missing}\n")).unwrap();
        std::fs::write(dir.join("then.jsonl"), then).unwrap();
        let path = dir.join("claude");
        // The first process answers only after the follow-up is on its stdin; the second records the three lines it reads.
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\nIFS= read -r steer\nif [ -f ran ]; then IFS= read -r later; printf '%s\\n%s\\n%s\\n' \"$line\" \"$steer\" \"$later\" > read.jsonl; cat then.jsonl; cat >/dev/null; exit 0; fi\ntouch ran\ncat first.jsonl\nexit 1\n",
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        // A steer the moment each pipe is published: the first into the missing session, the second into the
        // restored one, where it must land after the carried follow-up.
        let steered = Arc::new(Mutex::new(vec![]));
        let record = steered.clone();
        host.inner().pipe_opened = Some(Arc::new(move |h: &ClaudeHost| {
            let (thread, turn) = h.inner().turn.clone().unwrap();
            let mut record = record.lock().unwrap();
            let text = ["Also the QA result.", "Then this."][record.len().min(1)];
            record.push(h.steer(&thread, &turn, text).is_ok());
        }));
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.resume_thread(&uuid::Uuid::new_v4().to_string(), &settings);
        host.start_turn(&thread, "hello", None).unwrap();
        let mut got = vec![];
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        let read = std::fs::read_to_string(dir.join("read.jsonl")).unwrap_or_default();
        std::fs::remove_dir_all(dir).unwrap();
        assert_eq!(*steered.lock().unwrap(), [true, true]);
        assert!(got.iter().any(|e| e["kind"] == "diagnostic" && e["text"] == RESTORING), "{got:?}");
        assert!(!got.iter().any(|e| e["kind"] == "turn_failed"), "{got:?}");
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
        // The restored session read the prompt, the carried follow-up, then the newer one.
        let lines: Vec<Value> = read.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        let texts: Vec<&Value> = lines.iter().map(|l| &l["message"]["content"][0]["text"]).collect();
        assert_eq!(texts, [&json!("hello"), &json!("Also the QA result."), &json!("Then this.")], "{read}");
    }

    #[tokio::test]
    async fn a_resumed_sessions_empty_result_before_the_prompt_is_not_the_answer() {
        // Real Claude 2.1.303 on resuming a session whose process was killed with a background shell running.
        let notice = r#"{"type": "system", "subtype": "task_notification", "task_id": "b1", "status": "stopped", "output_file": "", "summary": "Background shell command didn't finish before the previous session ended"}"#;
        let empty = r#"{"type": "result", "subtype": "success", "is_error": false, "num_turns": 0, "result": "", "usage": {"input_tokens": 0, "output_tokens": 0}}"#;
        let prompt = r#"{"type": "user", "isReplay": true, "message": {"role": "user", "content": "hello"}}"#;
        let first = format!("{notice}\n{empty}\n{prompt}\n{}\n", assistant_line("m1", "Checking."));
        let echo = r#"{"type": "user", "isReplay": true, "message": {"role": "user", "content": "Also the docs."}}"#;
        let then = format!("{echo}\n{}\n{}\n", assistant_line("m2", "Docs too."), result_line("Docs too."));
        let dir = std::env::temp_dir().join(format!("claude-resume-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("first.jsonl"), first).unwrap();
        std::fs::write(dir.join("then.jsonl"), then).unwrap();
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            "#!/bin/sh\ncase \"$1\" in --version) echo '2.1.296 (Claude Code)'; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\nIFS= read -r line\ncat first.jsonl\nIFS= read -r steer && cat then.jsonl\ncat >/dev/null\n",
        );
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let mut settings = Settings { cwd: dir.display().to_string(), ..Default::default() };
        settings.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let thread = host.start_thread(&settings);
        let turn = host.start_turn(&thread, "hello", None).unwrap();
        let mut got = vec![];
        let mut steered = None;
        while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            if steered.is_none() && ev["kind"] == "message" && ev["text"] == "Checking." {
                // The model is mid-turn: a follow-up goes in, as in any turn.
                steered = Some(host.steer(&thread, &turn, "Also the docs.").is_ok());
            }
            let idle = ev["kind"] == "status" && ev["state"] == "idle";
            got.push(ev);
            if idle {
                break;
            }
        }
        std::fs::remove_dir_all(dir).unwrap();
        assert_eq!(steered, Some(true), "{got:?}");
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
        let last = got.iter().rev().find(|e| e["kind"] == "message" && e["final"] == true).unwrap();
        assert_eq!(last["text"], "Docs too.");
    }

    #[tokio::test]
    async fn each_model_request_is_counted_once_with_its_final_output() {
        // A request's usage so far rides on message_start and every content block's line; its message_delta has the
        // final output. A subagent streams its own request meanwhile, under its parent tool call.
        let long = r#""usage": {"input_tokens": 2, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 120000, "output_tokens": 16}"#;
        let start = format!(r#"{{"type": "stream_event", "event": {{"type": "message_start", "message": {{"id": "m1", {long}}}}}}}"#);
        let think = format!(r#"{{"type": "assistant", "message": {{"id": "m1", "content": [{{"type": "thinking", "thinking": ""}}], {long}}}}}"#);
        let tool = format!(r#"{{"type": "assistant", "message": {{"id": "m1", "content": [{{"type": "tool_use", "id": "t1", "name": "Task", "input": {{}}}}], {long}}}}}"#);
        let sub_start = r#"{"type": "stream_event", "parent_tool_use_id": "t1", "event": {"type": "message_start", "message": {"id": "m2", "usage": {"input_tokens": 900, "output_tokens": 1}}}}"#;
        let delta = r#"{"type": "stream_event", "event": {"type": "message_delta", "usage": {"output_tokens": 78}}}"#;
        let sub_delta = r#"{"type": "stream_event", "parent_tool_use_id": "t1", "event": {"type": "message_delta", "usage": {"output_tokens": 5}}}"#;
        let result = r#"{"type": "result", "subtype": "success", "is_error": false, "result": "Done.", "usage": {"input_tokens": 902, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 120000, "output_tokens": 83}}"#;
        let lines = [start.as_str(), &think, sub_start, &tool, delta, sub_delta, result].join("\n") + "\n";
        let (got, _) = background_turn(&lines, "", "", false).await;
        let tokens = got.iter().find(|e| e["kind"] == "tokens").unwrap();
        assert_eq!(tokens["usage"]["requests"], json!([[3002, 120000, 78, 1], [900, 0, 5, 0]]), "{got:?}");
        // Together they are the whole run, so the server prices every token at its request's own rate.
        assert_eq!((&tokens["usage"]["input"], &tokens["usage"]["cached"], &tokens["usage"]["output"]), (&json!(123902), &json!(120000), &json!(83)));
    }

    #[tokio::test]
    async fn a_steer_after_a_limit_notice_waits_for_the_next_turn() {
        let notice = r#"{"type": "assistant", "message": {"id": "m", "model": "<synthetic>", "content": [{"type": "text", "text": "You've hit your session limit · resets 12:40am (UTC)"}]}, "error": "rate_limit"}"#;
        let (got, steered) = limit_turn(&format!("{notice}\n"), "", true).await;
        assert_eq!(steered, Some(false));
        let failed = got.iter().find(|e| e["kind"] == "turn_failed").unwrap();
        assert_eq!(failed["limit"], true);
    }

    #[tokio::test]
    async fn a_limit_paid_by_overage_keeps_the_turn_running() {
        let overage = r#"{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "isUsingOverage": true, "rateLimitType": "five_hour", "resetsAt": 1760488800}}"#;
        let result = r#"{"type": "result", "subtype": "success", "is_error": false, "result": "done", "usage": {"input_tokens": 1, "output_tokens": 1}}"#;
        let (got, _) = limit_turn(&format!("{overage}\n"), &format!("{result}\n"), false).await;
        assert!(got.iter().any(|e| e["kind"] == "turn_completed"), "{got:?}");
        assert!(!got.iter().any(|e| e["kind"] == "turn_failed"), "{got:?}");
    }

    #[test]
    fn both_usage_windows_carry_their_percentage() {
        // Claude Code 2.1.301's event, as read on a Max login (Oct 16 2026).
        let info: Value = serde_json::from_str(r#"{"status":"allowed","resetsAt":1792120200,"rateLimitType":"five_hour","overageStatus":"rejected","isUsingOverage":false,
            "unifiedWindows":{"five_hour":{"utilization":0.53,"resetsAt":1792120200},"seven_day":{"utilization":0.17,"resetsAt":1792440000}}}"#).unwrap();
        let got = rate_limit_windows(&info);
        assert_eq!(got, vec![
            json!({"used_percent": 53.0, "window_minutes": 300, "resets_at": iso(&json!(1792120200)), "status": "allowed"}),
            json!({"used_percent": 17.0, "window_minutes": 10080, "resets_at": iso(&json!(1792440000)), "status": null}),
        ]);
        assert_eq!(got[0]["resets_at"], "2026-10-16T03:10:00+00:00");
        // Odd or missing utilization reads unknown, never 0 or 100; an unlabelled window is left out.
        let odd = json!({"status": "allowed", "rateLimitType": "seven_day", "resetsAt": 1792440000,
                         "unifiedWindows": {"five_hour": {"utilization": 1.5, "resetsAt": 1792120200}, "seven_day": {"utilization": "0.2"},
                                            "seven_day_opus": {"utilization": 0.9, "resetsAt": 1792440000}, "monthly": {"utilization": 0.1}}});
        let got = rate_limit_windows(&odd);
        assert_eq!(got.len(), 2, "{got:?}");
        assert!(got.iter().all(|w| w["used_percent"].is_null()), "{got:?}");
        assert_eq!(got[1], json!({"used_percent": null, "window_minutes": 10080, "resets_at": iso(&json!(1792440000)), "status": "allowed"}));
        for bad in [json!(-0.01), json!(null), json!(f64::MAX)] {
            let one = rate_limit_windows(&json!({"unifiedWindows": {"five_hour": {"utilization": bad, "resetsAt": 1792120200}}}));
            assert!(one[0]["used_percent"].is_null(), "{bad}");
        }
        assert_eq!(rate_limit_windows(&json!({"unifiedWindows": {"five_hour": {"utilization": 0}}}))[0]["used_percent"], 0.0);
        // An older CLI: the one window it names, no percentage, as before.
        let older = json!({"status": "allowed_warning", "rateLimitType": "five_hour", "resetsAt": 1792120200});
        assert_eq!(rate_limit_windows(&older), vec![json!({"used_percent": null, "window_minutes": 300, "resets_at": iso(&json!(1792120200)), "status": "allowed_warning"})]);
    }

    #[test]
    fn a_tool_server_call_carries_no_summary() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec!["claude".into()], tx);
        let input = json!({"command": "curl -H 'Authorization: Bearer sk-live-1' x", "path": "/work/a.txt"});
        host.tool_started(&json!({"id": "t1", "name": "mcp__repo__run", "input": input}), None, "th", "tu");
        host.tool_started(&json!({"id": "t2", "name": "Bash", "input": input}), None, "th", "tu");
        let (server, bash) = (rx.try_recv().unwrap(), rx.try_recv().unwrap());
        assert_eq!((server["tool"].as_str(), server["summary"].as_str()), (Some("mcp__repo__run"), Some("")));
        assert_eq!(bash["summary"], "/work/a.txt");
    }

    #[test]
    fn a_refused_turns_event_gives_the_reset() {
        // `rate_limit_event` as Claude Code sends it when the five-hour window refuses a turn.
        let info: Value = serde_json::from_str(r#"{"status":"rejected","resetsAt":4102444800,"rateLimitType":"five_hour","overageStatus":"rejected","isUsingOverage":false}"#).unwrap();
        for w in rate_limit_windows(&info) {
            crate::profiles::remember_weekly("claude-reset-test", "claude", &w);
        }
        let now = 4102444800.0 - 3000.0;
        assert_eq!(crate::profiles::limit_reset("claude-reset-test", "claude", now).as_deref(), Some("2100-01-01T00:00:00+00:00"));
        crate::profiles::forget_windows("claude-reset-test", "claude");
    }

    #[test]
    fn only_claudes_own_notice_is_a_limit() {
        let text = "You've hit your session limit · resets 12:40am (UTC)";
        assert!(limit_notice(&json!({"error": "rate_limit", "message": {"model": "claude-opus-5-5"}}), text));
        assert!(limit_notice(&json!({"message": {"model": "<synthetic>"}}), text));
        assert!(!limit_notice(&json!({"message": {"model": "claude-opus-5-5"}}), text));
        assert!(!limit_notice(&json!({"message": {"model": "<synthetic>"}}), "No response requested."));
    }

    #[test]
    fn a_bots_model_and_effort_reach_claude_code() {
        let (tx, _rx) = mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec!["claude".into()], tx);
        let settings = Settings { model: Some("claude-haiku-5-5".into()), effort: Some("xhigh".into()), ..Default::default() };
        let thread = host.start_thread(&settings);
        let argv = host.argv(&thread, &host.inner().threads[&thread].clone(), None);
        let after = |flag: &str| argv.iter().position(|a| a == flag).map(|i| argv[i + 1].as_str());
        assert_eq!((after("--model"), after("--effort")), (Some("claude-haiku-5-5"), Some("xhigh")));
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
