//! The Codex host: one `codex app-server` process over stdio, newline-delimited
//! JSON-RPC 2.0. The bot's environment is both the process's own and, through `shell_environment_policy`,
//! what each thread's shell commands see; the operator's own MCP servers are stubbed so they start nothing.
//!
//! A thread's app-server may be kept between its turns (crate::keeper, T3 Code's Codex adapter, one process per
//! thread here, since a bot's environment, folder and plan are its own): its next turn is the next `turn/start` on the
//! live thread, with no `thread/resume`. It reads HUB_TOKEN from a token file of its own (crate::warm), blanked while it
//! is kept. The turn's settings decide whether it will do (`take_kept`): a process started any other way (`fingerprint`)
//! or signed in otherwise (`signed_in`) starts the turn cold, and one that has gone resumes the thread as before. What
//! it says while kept goes nowhere; its tool calls and subagents still running hold it (crate::keeper::MAX_PIN).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::ChildStdin;
use tokio::sync::oneshot;

use super::{
    command_program, event, failed, hub_mcp_server, is_limit, shell_outside_actions,
    shell_reads_only, tool_summary, Events, HostError, Settings,
};
use crate::env::Env;
use crate::keeper::{Held, Inputs, Keeper, Session};
use crate::redact::Redactor;
use crate::turn_sweep::{self, MARK_ENV};
use crate::util::{diagnostic, iso, log, py_dumps, text_of};

const REQUEST_TIMEOUT: u64 = 120;
const START_TIMEOUT: u64 = 60;
/// How long a compaction may take (it summarises the whole transcript with the model).
const COMPACT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(540);
const ENV_SKIP: &[&str] = &[
    "PWD",
    "OLDPWD",
    "SHLVL",
    "_",
    "TMPDIR",
    "XPC_SERVICE_NAME",
    "XPC_FLAGS",
    "__CF_USER_TEXT_ENCODING",
    "TERM_SESSION_ID",
    "SSH_AUTH_SOCK",
];
const ENV_MAX_VALUE: usize = 8000;
const NOOP_COMMAND: &str = "/usr/bin/true";
/// Thread items that are not tool calls. Any other item type, including one added later, is reported as a tool,
/// because an unreported call would let a stopped run look safe to repeat.
const NOT_TOOLS: &[&str] = &[
    "userMessage",
    "hookPrompt",
    "agentMessage",
    "plan",
    "reasoning",
    "subAgentActivity",
    "enteredReviewMode",
    "exitedReviewMode",
    "contextCompaction",
    "sleep",
];

/// A tool item's name and what it acts on: the files it changes or the program it runs, never its arguments.
fn tool_of(item: &Value) -> (String, String) {
    let kind = item["type"].as_str().unwrap_or_default();
    let named = |key: &str| {
        item[key]
            .as_str()
            .filter(|t| !t.is_empty())
            .unwrap_or(kind)
            .to_string()
    };
    match kind {
        "commandExecution" => (
            "shell".into(),
            command_program(item["command"].as_str().unwrap_or_default()),
        ),
        "fileChange" => {
            let paths: Vec<&str> = item["changes"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|c| c["path"].as_str())
                .collect();
            (
                "apply_patch".into(),
                tool_summary(&json!({"path": paths.join(" ")})),
            )
        }
        "mcpToolCall" => (
            format!(
                "mcp__{}__{}",
                text_of(&item["server"]),
                text_of(&item["tool"])
            ),
            String::new(),
        ),
        "dynamicToolCall" | "collabAgentToolCall" => (named("tool"), String::new()),
        // An output with no call item of its own still means a call ran.
        "functionCallOutput" => match item["namespace"].as_str().filter(|n| !n.is_empty()) {
            Some(ns) => (format!("{ns}__{}", named("name")), String::new()),
            None => (named("name"), String::new()),
        },
        "webSearch" => ("web_search".into(), String::new()),
        "imageView" => (
            "view_image".into(),
            tool_summary(&json!({"path": item["path"]})),
        ),
        _ => (kind.to_string(), String::new()),
    }
}

/// A failed tool item's command (its paths or its input when it has none) and what it printed or the error it ended with, for the
/// run's capture (crate::run_diagnostics::failed_tool): `commandExecution`'s `command` and `aggregatedOutput`, an MCP
/// or dynamic call's arguments and its error, result or content items, a function call's output.
fn failed_detail(item: &Value) -> (String, String) {
    let command = match (&item["command"], tool_of(item).1) {
        (Value::String(c), _) => c.clone(),
        (_, summary) if !summary.is_empty() => summary, // a file change's paths, never its diff
        _ => ["arguments", "input"]
            .iter()
            .map(|k| &item[*k])
            .find(|v| !v.is_null())
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_default(),
    };
    let output = [
        "aggregatedOutput",
        "error",
        "output",
        "result",
        "contentItems",
    ]
    .iter()
    .map(|k| printed(&item[*k]))
    .find(|t| !t.trim().is_empty())
    .unwrap_or_default();
    (command, output)
}

/// The text a tool result carries: a string, `message`/`text` fields, or the texts of a content list.
fn printed(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Array(items) => items
            .iter()
            .map(printed)
            .filter(|t| !t.is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        Value::Object(map) => ["message", "text", "content"]
            .iter()
            .filter_map(|k| map.get(*k))
            .map(printed)
            .find(|t| !t.is_empty())
            .unwrap_or_else(|| v.to_string()),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// The app server's extra skill roots for a turn: its staged company skills folder's `<name>/SKILL.md` folder
/// (crate::company_skills::skills_root), a folder of the runner's own state. Never a folder under CODEX_HOME, which
/// may be the operator's own.
pub fn skill_roots(settings: &Settings) -> Vec<String> {
    settings
        .company_skills
        .iter()
        .map(|dir| {
            crate::company_skills::skills_root(std::path::Path::new(dir))
                .display()
                .to_string()
        })
        .collect()
}

/// The `set` table for `shell_environment_policy`: the bot's environment, trimmed.
pub fn env_for_config(env: &Env) -> Map<String, Value> {
    env.iter()
        .filter(|(k, v)| {
            !ENV_SKIP.contains(&k.as_str()) && v.len() <= ENV_MAX_VALUE && !v.contains('\0')
        })
        .map(|(k, v)| (k.clone(), json!(v)))
        .collect()
}

/// A `mcp_servers` override that stops every server in the operator's config.toml from starting.
pub fn mcp_disable_config(codex_home: Option<&str>) -> Map<String, Value> {
    let home = codex_home
        .map(String::from)
        .or_else(|| std::env::var("CODEX_HOME").ok().filter(|h| !h.is_empty()))
        .unwrap_or_else(|| format!("{}/.codex", std::env::var("HOME").unwrap_or_default()));
    let Ok(text) = std::fs::read_to_string(format!("{home}/config.toml")) else {
        return Map::new();
    };
    let Ok(doc) = text.parse::<toml::Table>() else {
        return Map::new();
    };
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
            out.insert(
                name.clone(),
                if has_command {
                    json!({"command": NOOP_COMMAND, "args": []})
                } else {
                    json!({"startup_timeout_ms": 1})
                },
            );
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

struct PlanLogin {
    params: Value,
    cwd: PathBuf,
    config: Vec<String>,
    provider: &'static str,
}

pub(crate) fn external_login(token: &Value, account: &Value) -> Value {
    json!({"type": "chatgptAuthTokens", "accessToken": token, "chatgptAccountId": account})
}

fn plan_config(provider: &str, route: Option<&str>, cwd: &std::path::Path) -> Vec<String> {
    let checkout = json!(cwd.display().to_string());
    let base_url = route.unwrap_or("https://chatgpt.com");
    let chat = json!(format!("{base_url}/backend-api"));
    let mut config = vec![
        format!("model_provider={}", json!(provider)),
        format!("chatgpt_base_url={chat}"),
        r#"cli_auth_credentials_store="ephemeral""#.into(),
        format!(r#"projects={{ {checkout}={{ trust_level="untrusted" }} }}"#),
    ];
    let Some(url) = route else { return config };
    let api = json!(format!("{url}/backend-api/codex"));
    config.extend([
        format!(
            r#"model_providers={{ tico_exit={{ name="Tico exit", base_url={api}, wire_api="responses", requires_openai_auth=true, supports_websockets=false, request_max_retries=0, stream_max_retries=0 }} }}"#
        ),
        "features.plugins=false".into(),
        "features.remote_plugin=false".into(),
        "features.apps=false".into(),
        "features.remote_control=false".into(),
        "features.enable_request_compression=false".into(),
        "features.respect_system_proxy=false".into(),
        "features.system_proxy_fallback=false".into(),
        "features.unbounded_connection_retries=false".into(),
        "analytics.enabled=false".into(),
        "check_for_update_on_startup=false".into(),
    ]);
    config
}

struct Shared {
    plan: Option<PlanLogin>,
    allocation: super::Allocation,
    /// The attempt's events; none while the keeper holds the process between turns (`Kept::park`).
    tx: Mutex<Option<Events>>,
    env: Env,
    base_config: Map<String, Value>,
    /// The keeper that may hold this process between its thread's turns (`with_keeper`), and the folder of the token
    /// file it reads HUB_TOKEN from (crate::warm).
    keeper: Option<Arc<Keeper>>,
    tokens: Option<PathBuf>,
    /// What this turn starts the process with (`fingerprint`), once the turn's settings are known (`take_kept`).
    key: Mutex<Option<Inputs>>,
    /// This attempt went on in a kept process (Host::warm), and the scope that process was launched in.
    warm: AtomicBool,
    unit: Mutex<Option<String>>,
    /// Tool calls and subagents started and not yet ended, by item: background work that keeps a kept process past
    /// crate::keeper::IDLE (`Session::background`).
    running: Arc<Mutex<HashMap<String, String>>>,
    /// The last `turn/interrupt` was answered, so the turn it stopped is ending in the app server.
    interrupted: AtomicBool,
    /// True once the process's output has ended or it has exited.
    ended: tokio::sync::watch::Sender<bool>,
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<i64, oneshot::Sender<Reply>>>,
    next_id: AtomicI64,
    alive: AtomicBool,
    child: Mutex<Option<(super::Stopper, Arc<AtomicBool>)>>,
    /// Confirms a successful wait even if the caller that started the stop was cancelled.
    reaped: Mutex<Option<tokio::sync::watch::Receiver<bool>>>,
    /// The current app-server, swept before replacing it or ending the attempt.
    sweep: Mutex<Option<turn_sweep::Turn>>,
    sweep_redactor: Mutex<Option<Arc<Redactor>>>,
    sweep_reports: Mutex<Vec<Value>>,
    active_turn: Mutex<HashMap<String, String>>,
    done_turns: Mutex<HashSet<String>>,
    token_seen: Mutex<HashMap<String, [i64; 3]>>,
    /// A subagent's thread to the thread that started it: its tool calls are reported on the parent's turn.
    children: Mutex<HashMap<String, String>>,
    /// Tool events already sent, by item id and status: a start or end is reported once.
    tools_sent: Mutex<HashSet<String>>,
    /// Threads the app server has started a turn on, so they are written to its rollout: a `thread/start` answer
    /// alone lives only in this app-server process, and a resume of it after the process is gone finds nothing.
    confirmed: Mutex<HashSet<String>>,
    capture: Mutex<Option<Arc<crate::capture::Capture>>>,
    /// Threads being compacted before a turn (`compact`): None until the app server says how it ended.
    compacting: Mutex<HashMap<String, Option<Result<(), String>>>>,
    compacted: tokio::sync::Notify,
    /// The extra skill roots this app server was last given (`set_skill_roots`); empty is Codex's own default.
    skill_roots: Mutex<Vec<String>>,
}

#[derive(Clone)]
pub struct CodexHost(Arc<Shared>);

impl CodexHost {
    /// A host for a subscription profile: the servers silenced are the ones in that profile's config.toml.
    pub fn on_home(env: Env, tx: Events, codex_home: Option<&str>) -> CodexHost {
        CodexHost(Arc::new(Shared {
            plan: None,
            allocation: super::Allocation::default(),
            tx: Mutex::new(Some(tx)),
            env,
            base_config: mcp_disable_config(codex_home),
            keeper: None,
            tokens: None,
            key: Mutex::new(None),
            warm: AtomicBool::new(false),
            unit: Mutex::new(None),
            running: Arc::default(),
            interrupted: AtomicBool::new(false),
            ended: tokio::sync::watch::channel(false).0,
            stdin: tokio::sync::Mutex::new(None),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicI64::new(0),
            alive: AtomicBool::new(false),
            child: Mutex::new(None),
            reaped: Mutex::new(None),
            sweep: Mutex::new(None),
            sweep_redactor: Mutex::new(None),
            sweep_reports: Mutex::new(Vec::new()),
            active_turn: Mutex::new(HashMap::new()),
            done_turns: Mutex::new(HashSet::new()),
            token_seen: Mutex::new(HashMap::new()),
            children: Mutex::new(HashMap::new()),
            tools_sent: Mutex::new(HashSet::new()),
            confirmed: Mutex::new(HashSet::new()),
            capture: Mutex::new(None),
            compacting: Mutex::new(HashMap::new()),
            compacted: tokio::sync::Notify::new(),
            skill_roots: Mutex::new(Vec::new()),
        }))
    }

    pub(crate) fn with_plan(
        mut self,
        tokens: &Value,
        url: Option<&str>,
        cwd: &std::path::Path,
    ) -> Result<Self, HostError> {
        let shared =
            Arc::get_mut(&mut self.0).ok_or_else(|| failed("Codex host already started"))?;
        let cwd = cwd
            .canonicalize()
            .map_err(|_| failed("Cannot resolve Codex checkout"))?;
        let provider = if url.is_some() {
            crate::model_exit::CODEX_PROVIDER
        } else {
            "openai"
        };
        let config = plan_config(provider, url, &cwd);
        if url.is_some() {
            let home = shared
                .env
                .get("CODEX_HOME")
                .ok_or_else(|| failed("Missing Codex turn home"))?;
            crate::model_subscriptions::write_private(
                &PathBuf::from(home).join("config.toml"),
                &config.join("\n"),
            )
            .map_err(failed)?;
        }
        shared.plan = Some(PlanLogin {
            params: external_login(&tokens["access_token"], &tokens["account_id"]),
            cwd,
            config,
            provider,
        });
        Ok(self)
    }

    /// A host whose process the keeper may hold for its thread's next turn (crate::keeper): the process reads HUB_TOKEN
    /// from a token file of its own (crate::warm), written with each turn's token and blanked while it is kept.
    pub(crate) fn with_keeper(mut self, keeper: Arc<Keeper>) -> Result<Self, HostError> {
        let shared =
            Arc::get_mut(&mut self.0).ok_or_else(|| failed("Codex host already started"))?;
        let tokens = keeper.folder().map_err(failed)?;
        if let Some(token) = shared.env.get("HUB_TOKEN").cloned() {
            crate::warm::write_token_for(&tokens.join("lease-token"), &token, None);
            shared.env.insert("HUB_TOKEN".into(), token_ref(&tokens));
        }
        shared.tokens = Some(tokens);
        shared.keeper = Some(keeper);
        Ok(self)
    }

    /// Whether the keeper may hold this host's process (`with_keeper`).
    pub(crate) fn keeps(&self) -> bool {
        self.0.keeper.is_some()
    }

    /// Whether this attempt went on in a process an earlier one left running (`take_kept`).
    pub fn warm(&self) -> bool {
        self.0.warm.load(Ordering::SeqCst)
    }

    /// The launch scope of the kept process this attempt went on in (Host::kept_scopes): still the earlier attempt's.
    pub fn scopes(&self) -> Vec<String> {
        match self.warm() {
            true => self.0.unit.lock().unwrap().clone().into_iter().collect(),
            false => vec![],
        }
    }

    /// The home Codex signs in from: CODEX_HOME, else `~/.codex`.
    fn codex_home(&self) -> Option<PathBuf> {
        let home = |key| self.0.env.get(key).filter(|value| !value.is_empty());
        home("CODEX_HOME")
            .map(PathBuf::from)
            .or_else(|| home("HOME").map(|home| PathBuf::from(home).join(".codex")))
    }

    /// What a process for this turn would be started with, the turn's own token, scope and sweep mark aside: its command
    /// line (a plan's settings among it), its environment, and the thread as this turn would resume it (its model,
    /// folder, MCP servers, commands' environment and config, and company skills), which a kept process is not told
    /// again. The effort goes with each turn. A plan's home made for each turn (model_subscriptions `turn_only`) is
    /// named by its plan: a kept process goes on in the home it was started in, held until it goes (`release`).
    fn fingerprint(&self, settings: &Settings) -> Inputs {
        let mut inputs = Inputs::default();
        let plan = self.0.plan.as_ref().map(|p| {
            json!({"cwd": p.cwd.display().to_string(), "config": p.config, "provider": p.provider})
        });
        inputs.add("argv", &json!(["codex", "app-server", plan]).to_string());
        let turn_home = self
            .codex_home()
            .filter(|home| crate::model_subscriptions::turn_only(home));
        let mut shell = settings.env.clone();
        match turn_home
            .as_deref()
            .and_then(|home| home.ancestors().nth(3))
        {
            Some(plan) => {
                inputs.env(&self.0.env, &["CODEX_HOME"]);
                inputs.add("plan_home", &plan.display().to_string());
                if shell.contains_key("CODEX_HOME") {
                    shell.insert("CODEX_HOME".into(), plan.display().to_string());
                }
            }
            None => inputs.env(&self.0.env, &[]),
        }
        shell.remove(crate::turn_limits::SCOPE_ENV);
        if shell.contains_key("HUB_TOKEN") {
            shell.insert("HUB_TOKEN".into(), "tico-file:".into());
        }
        let params = self.params_for(settings, &shell, None);
        for (name, value) in params.as_object().into_iter().flatten() {
            match value {
                Value::Object(config) if name == "config" => {
                    for (k, v) in config {
                        inputs.add(&format!("config:{k}"), &v.to_string());
                    }
                }
                _ => inputs.add(&format!("thread:{name}"), &value.to_string()),
            }
        }
        inputs.add("skill_roots", &json!(skill_roots(settings)).to_string());
        inputs
    }

    /// `key` with the sign-in the process has now: a plan's tokens and its home's sign-in file. A login handed anew
    /// since (a newer revision in the file, other plan tokens) starts the next turn cold; one the kept process
    /// refreshed itself stays in place (model_subscriptions `place`), so it does not.
    fn signed_in(&self, mut key: Inputs) -> Inputs {
        let plan = self
            .0
            .plan
            .as_ref()
            .map_or(Value::Null, |p| p.params.clone());
        let file = self
            .codex_home()
            .and_then(|home| crate::model_subscriptions::read_codex_auth(&home.join("auth.json")))
            .unwrap_or_default();
        key.add("sign_in", &format!("{plan}\n{file}"));
        key
    }

    /// The turn's settings are known: this host notes what its process would start with (`fingerprint`), lends its own
    /// token file the turn's token, and takes the process the keeper holds for `previous` when that one was started the
    /// same way and still runs, to go on in place of this host (Host::warm), with this attempt's events, capture,
    /// masking and admission. None: this host starts its own.
    pub(crate) async fn take_kept(
        &self,
        previous: Option<&str>,
        settings: &Settings,
    ) -> Option<CodexHost> {
        let keeper = self.0.keeper.clone()?;
        let token = settings.env.get("HUB_TOKEN").map_or("", String::as_str);
        if let Some(dir) = &self.0.tokens {
            crate::warm::write_token_for(&dir.join("lease-token"), token, None);
        }
        let key = self.fingerprint(settings);
        *self.0.key.lock().unwrap() = Some(key.clone());
        let mut kept = keeper
            .take::<Kept>(previous?, &self.signed_in(key.clone()), token)
            .await?;
        let host = kept.0.take()?;
        *host.0.tx.lock().unwrap() = self.0.tx.lock().unwrap().clone();
        *host.0.capture.lock().unwrap() = self.capture();
        *host.0.sweep_redactor.lock().unwrap() = self.0.sweep_redactor.lock().unwrap().clone();
        *host.0.key.lock().unwrap() = Some(key);
        let turn = self.0.allocation.0.lock().unwrap().turn.clone();
        if let Some(turn) = turn {
            host.0.allocation.track(turn);
        }
        host.0.interrupted.store(false, Ordering::SeqCst);
        host.0.warm.store(true, Ordering::SeqCst);
        log("codex: the thread's next turn goes on in its kept app-server");
        Some(host)
    }

    /// The attempt is over, its turn on `thread` ended: the process goes to the keeper for the thread's next turn
    /// (crate::keeper), holding `held` (what its folder needs meanwhile) and a plan's home with its sign-in
    /// (model_subscriptions::keep_home) until it goes. True once it has; false leaves it to be stopped as before: no
    /// keeper or thread, a turn still running in it (a stopped one whose `turn/interrupt` was answered gets
    /// crate::keeper::INTERRUPT_WAIT to end), or a process that has gone. A plan's home made for this turn alone stays
    /// as long as the process does, and goes with it.
    pub(crate) async fn release(&self, thread: Option<&str>, held: Option<Held>) -> bool {
        let (Some(keeper), Some(thread)) = (self.0.keeper.clone(), thread) else {
            return false;
        };
        let Some(key) = self.0.key.lock().unwrap().clone() else {
            return false;
        };
        if self.turn_running(thread) {
            if !self.0.interrupted.load(Ordering::SeqCst) {
                return false;
            }
            let by = tokio::time::Instant::now() + crate::keeper::INTERRUPT_WAIT;
            while self.turn_running(thread) {
                if tokio::time::Instant::now() >= by || !self.alive() {
                    return false;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }
        if !self.alive() {
            return false;
        }
        let plan = self
            .codex_home()
            .and_then(|home| crate::model_subscriptions::keep_home(&home));
        let key = self.signed_in(key);
        let held: Held = Box::new((held, plan));
        keeper
            .put(thread, key, Kept(Some(self.clone())), Some(held))
            .await;
        true
    }

    fn routed(&self) -> bool {
        self.0
            .plan
            .as_ref()
            .is_some_and(|plan| plan.provider == crate::model_exit::CODEX_PROVIDER)
    }

    fn emit(&self, ev: Value) {
        if let Some(tx) = self.0.tx.lock().unwrap().as_ref() {
            let _ = tx.send(ev);
        }
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
        // Even an already-reaped server can have leftovers. End their lifetime before starting
        // the replacement, while the old process identities and scope still describe this turn.
        let replacing = self.0.reaped.lock().unwrap().is_some();
        if replacing {
            self.stop_and_reap().await.map_err(failed)?;
            let previous = self.0.sweep.lock().unwrap().take();
            if let Some(previous) = previous {
                if let Some(report) = self.sweep_reaped(previous).await {
                    self.0.sweep_reports.lock().unwrap().push(report);
                }
                self.prepare_sweep();
            }
            self.0.reaped.lock().unwrap().take();
        }
        let mut command = super::harness_command(None, "codex");
        command
            .arg("app-server")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env_clear()
            .envs(&self.0.env);
        if let Some(plan) = &self.0.plan {
            command
                .args(["--listen", "stdio://"])
                .current_dir(&plan.cwd);
            for setting in &plan.config {
                command.args(["-c", setting]);
            }
        }
        super::new_group(&mut command);
        let mut child = {
            let mut sweep = self.0.sweep.lock().unwrap();
            if let Some(target) = sweep.as_ref() {
                command.env(MARK_ENV, &target.mark);
            }
            let child = command
                .spawn()
                .map_err(|e| failed(format!("could not start codex app-server: {e}")))?;
            if let (Some(target), Some(pid)) = (sweep.as_mut(), child.id()) {
                let tracking = turn_sweep::Tracking::new(pid);
                #[cfg(target_os = "macos")]
                turn_sweep::Tracking::watch(&tracking);
                target.tracking = Some(tracking);
                let stem = self
                    .0
                    .env
                    .get(crate::turn_limits::SCOPE_ENV)
                    .map(String::as_str);
                target
                    .cgroups
                    .extend(crate::turn_limits::scope_dir(stem, pid));
            }
            child
        };
        self.0.allocation.mark();
        *self.0.unit.lock().unwrap() = crate::turn_limits::launch_unit(&self.0.env, child.id());
        self.0.ended.send_replace(false);
        // Whatever the process prints goes to the attempt it is running for, and nowhere while it is kept.
        let sink = self.clone();
        let _stderr = crate::run_diagnostics::Stderr::capture_with(
            &mut child,
            move |value| sink.emit(value),
            &self.0.env,
        );
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| failed("codex app-server has no stdout"))?;
        *self.0.stdin.lock().await = child.stdin.take();
        log(&format!(
            "codex: app-server started pid {}",
            child.id().map_or("?".into(), |p| p.to_string())
        ));
        #[cfg(target_os = "macos")]
        let before_reap = self
            .0
            .sweep
            .lock()
            .unwrap()
            .as_ref()
            .and_then(|t| t.tracking.clone())
            .map(|tracking| move || turn_sweep::Tracking::sample_before_reap(&tracking));
        #[cfg(not(target_os = "macos"))]
        let before_reap = None::<fn()>;
        let (stopper, exited) = super::own_with_before_reap(child, before_reap);
        // `exited` resolves once; whether it has is kept here for `alive`.
        let gone = Arc::new(AtomicBool::new(false));
        let (reaped_tx, reaped_rx) = tokio::sync::watch::channel(false);
        *self.0.reaped.lock().unwrap() = Some(reaped_rx);
        *self.0.child.lock().unwrap() = Some((stopper, gone.clone()));
        self.0.alive.store(true, Ordering::SeqCst);
        let host = self.clone();
        let mut reader = tokio::spawn(async move {
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
            host.0.ended.send_replace(true);
        });
        let host = self.clone();
        tokio::spawn(async move {
            let status = exited
                .await
                .unwrap_or_else(|_| Err(std::io::Error::other("codex app-server reaper ended")));
            _stderr.drained().await;
            // Drain final usage/completion, then detach the old reader if a tool inherited the pipe.
            if tokio::time::timeout(Duration::from_millis(100), &mut reader)
                .await
                .is_err()
            {
                reader.abort();
                let _ = reader.await;
                host.0.alive.store(false, Ordering::SeqCst);
                for (_, slot) in host.0.pending.lock().unwrap().drain() {
                    let _ = slot.send(Err(json!({"message": "codex app-server exited"})));
                }
            }
            let code = super::return_code(&status);
            host.emit(json!({"kind": "error", "error": crate::run_diagnostics::process_reason(code, &_stderr.tail()), "exit_code": code, "process_exit": true}));
            gone.store(true, Ordering::SeqCst);
            host.0.ended.send_replace(true);
            if status.is_ok() {
                let _ = reaped_tx.send(true);
            }
        });
        let info = json!({"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"});
        self.request(
            "initialize",
            json!({"clientInfo": info, "capabilities": {"experimentalApi": true}}),
            START_TIMEOUT,
        )
        .await?;
        self.notify("initialized", json!({})).await?;
        if let Some(plan) = &self.0.plan {
            if self
                .request("account/login/start", plan.params.clone(), START_TIMEOUT)
                .await?["type"]
                != "chatgptAuthTokens"
            {
                return Err(failed("Codex did not accept the plan's temporary sign-in"));
            }
        }
        Ok(())
    }

    pub async fn stop(&self) {
        let _ = self.stop_and_reap().await;
    }

    /// Leave escalation and reaping with `own`, even if this caller times out or is cancelled.
    async fn stop_and_reap(&self) -> Result<(), &'static str> {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(6);
        let reaped = self.0.reaped.lock().unwrap().clone();
        let tracking = self
            .0
            .sweep
            .lock()
            .unwrap()
            .as_ref()
            .and_then(|t| t.tracking.clone());
        if let Some(tracking) = tracking {
            let _ = tokio::time::timeout(
                Duration::from_millis(250),
                tokio::task::spawn_blocking(move || turn_sweep::Tracking::sample(&tracking)),
            )
            .await;
        }
        tokio::time::timeout_at(deadline, async {
            let child = self.0.child.lock().unwrap().take();
            if let Some((stopper, _)) = child {
                let grace = Duration::from_secs(5);
                if self.0.sweep.lock().unwrap().is_some() {
                    stopper.terminate_child(grace);
                } else {
                    stopper.terminate(grace);
                }
            }
            drop(self.0.stdin.lock().await.take());
            self.0.alive.store(false, Ordering::SeqCst);
            if let Some(mut reaped) = reaped {
                reaped
                    .wait_for(|gone| *gone)
                    .await
                    .map_err(|_| "Could not reap the Codex app-server.")?;
            }
            Ok(())
        })
        .await
        .map_err(|_| "Timed out waiting for the Codex app-server to be reaped.")?
    }

    pub(super) fn set_attempt(&self, redactor: Option<Arc<Redactor>>) {
        *self.0.sweep_redactor.lock().unwrap() = redactor;
        self.prepare_sweep();
    }

    fn prepare_sweep(&self) {
        *self.0.sweep.lock().unwrap() = Some(turn_sweep::Turn {
            mark: uuid::Uuid::new_v4().to_string(),
            ..Default::default()
        });
    }

    async fn sweep_reaped(&self, target: turn_sweep::Turn) -> Option<Value> {
        let mut redactor = self.0.sweep_redactor.lock().unwrap().clone();
        if let Some(home) = self.codex_home() {
            // The app-server can refresh the login after startup. Mask the current
            // credentials before forming or clipping a command-line diagnostic.
            let secrets = tokio::task::spawn_blocking(move || {
                let auth = crate::model_subscriptions::read_codex_auth(&home.join("auth.json"))?;
                Some(crate::model_subscriptions::secrets_in(&auth))
            })
            .await
            .ok()
            .flatten();
            if let Some(secrets) = secrets {
                redactor
                    .get_or_insert_with(|| Arc::new(Redactor::new(&[])))
                    .add(&secrets);
            }
        }
        turn_sweep::finish(target, "codex", redactor).await
    }

    pub(crate) async fn finish_turn(&self) -> Vec<Value> {
        let mut reports = std::mem::take(&mut *self.0.sweep_reports.lock().unwrap());
        if self.0.sweep.lock().unwrap().is_none() {
            self.stop().await;
            return reports;
        }
        let reaped = self.stop_and_reap().await;
        let target = self.0.sweep.lock().unwrap().take();
        if let Some(target) = target {
            reports.extend(match reaped {
                Ok(()) => self.sweep_reaped(target).await,
                Err(error) => {
                    Some(json!({"text": format!("Codex process cleanup incomplete. {error}")}))
                }
            });
        }
        reports
    }

    pub fn track_allocation(&self, turn: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        self.0.allocation.track(turn);
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
        let Some(s) = stdin.as_mut() else {
            return Err(failed("codex app-server stdin closed"));
        };
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
        self.write(&json!({"jsonrpc": "2.0", "method": method, "params": params}))
            .await
    }

    #[cfg(test)]
    pub(crate) fn requests_started(&self) -> i64 {
        self.0.next_id.load(Ordering::SeqCst)
    }

    pub async fn request(
        &self,
        method: &str,
        params: Value,
        timeout: u64,
    ) -> Result<Value, HostError> {
        let id = self.0.next_id.fetch_add(1, Ordering::SeqCst) + 1;
        let (tx, rx) = oneshot::channel();
        self.0.pending.lock().unwrap().insert(id, tx);
        if let Err(e) = self
            .write(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}))
            .await
        {
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
                let text = err["message"]
                    .as_str()
                    .filter(|m| !m.is_empty())
                    .map(String::from)
                    .unwrap_or_else(|| err.to_string());
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
                if let Some(slot) = id
                    .as_i64()
                    .and_then(|i| self.0.pending.lock().unwrap().remove(&i))
                {
                    let _ = slot.send(match msg.get("error") {
                        Some(e) => Err(e.clone()),
                        None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
                    });
                }
            }
            (Some(id), Some(method)) => {
                let (reply, decision, unexpected) = server_request_reply(&method);
                let thread = msg["params"]["threadId"].as_str().unwrap_or("-");
                let line = format!("codex: answered {method} on thread {thread}: {decision}");
                if unexpected {
                    diagnostic(&line);
                } else {
                    log(&line);
                }
                let answer = match reply {
                    Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
                    Err(error) => json!({"jsonrpc": "2.0", "id": id, "error": error}),
                };
                let _ = self.write(&answer).await;
            }
            (None, method) => {
                let p = msg
                    .get("params")
                    .filter(|p| p.is_object())
                    .cloned()
                    .unwrap_or(json!({}));
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
                let parent = thread["parentThreadId"]
                    .as_str()
                    .or(spawned.as_str())
                    .unwrap_or_default();
                let child = text_of(&thread["id"]);
                if !parent.is_empty() && !child.is_empty() && child != parent {
                    let mut children = self.0.children.lock().unwrap();
                    let root = children
                        .get(parent)
                        .cloned()
                        .unwrap_or_else(|| parent.to_string());
                    children.insert(child, root);
                }
            }
            "turn/started" => {
                let started = p["turn"]["id"].as_str().map(String::from);
                if tid
                    .as_ref()
                    .is_some_and(|tid| self.0.children.lock().unwrap().contains_key(tid))
                {
                    return; // a subagent's turn: the parent's turn is still the one running
                }
                if let (Some(tid), Some(s)) = (&tid, &started) {
                    self.0
                        .active_turn
                        .lock()
                        .unwrap()
                        .insert(tid.clone(), s.clone());
                    self.0.tools_sent.lock().unwrap().clear();
                }
                // `tool_events`: this run reports every tool call, so a run with none provably called none.
                self.emit(event(
                    "status",
                    t,
                    started.as_deref(),
                    json!({"state": "active", "tool_events": true}),
                ));
                if tid
                    .as_ref()
                    .is_some_and(|tid| self.0.confirmed.lock().unwrap().insert(tid.clone()))
                {
                    // A new thread is saved, and the one it replaces let go, only now (execute.rs Fresh).
                    self.emit(event(
                        "thread_confirmed",
                        t,
                        started.as_deref(),
                        json!({"session_id": t}),
                    ));
                }
            }
            "item/agentMessage/delta" => {
                self.emit(event(
                    "delta",
                    t,
                    u,
                    json!({"text": p["delta"].as_str().unwrap_or_default()}),
                ));
            }
            "item/started" => self.on_item(false, p),
            "item/completed" => {
                self.on_item(true, p);
                let item = &p["item"];
                if item["type"] == "contextCompaction" {
                    self.compaction_ended(t, Ok(()));
                }
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
                let finished = done["id"].as_str().map(String::from).or_else(|| {
                    tid.as_ref()
                        .and_then(|t| self.0.active_turn.lock().unwrap().get(t).cloned())
                });
                if let Some(tid) = &tid {
                    self.0.active_turn.lock().unwrap().remove(tid);
                }
                let key = finished.clone().unwrap_or_default();
                if done["status"] != "completed" && done["status"].is_string() {
                    // A stopped or failed turn leaves nothing of its own running.
                    let thread = tid.clone().unwrap_or_default();
                    self.0.running.lock().unwrap().retain(|_, of| *of != thread);
                }
                if !self.0.done_turns.lock().unwrap().insert(key) {
                    return;
                }
                // The turn a compaction runs as ends it: well, or with the reason it did not.
                match done["status"].as_str().unwrap_or("completed") {
                    "failed" => self.compaction_ended(
                        t,
                        Err(done["error"]["message"]
                            .as_str()
                            .filter(|m| !m.is_empty())
                            .unwrap_or("the compaction failed")
                            .to_string()),
                    ),
                    "interrupted" => {
                        self.compaction_ended(t, Err("the compaction was interrupted".into()))
                    }
                    _ => self.compaction_ended(t, Ok(())),
                }
                if done["status"] == "failed" {
                    let msg = with_kind(
                        &done["error"],
                        done["error"]["message"]
                            .as_str()
                            .filter(|m| !m.is_empty())
                            .unwrap_or("the turn failed")
                            .to_string(),
                        self.0.plan.is_some(),
                    );
                    self.emit(event(
                        "turn_failed",
                        t,
                        finished.as_deref(),
                        json!({"limit": is_limit(&msg), "error": msg}),
                    ));
                } else {
                    let status = done["status"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .unwrap_or("completed");
                    self.emit(event(
                        "turn_completed",
                        t,
                        finished.as_deref(),
                        json!({"status": status}),
                    ));
                }
            }
            "error" => {
                let err = &p["error"];
                let msg = with_kind(
                    err,
                    err["message"]
                        .as_str()
                        .filter(|m| !m.is_empty())
                        .map(String::from)
                        .unwrap_or_else(|| py_dumps(err).chars().take(500).collect()),
                    self.0.plan.is_some(),
                );
                let will_retry = crate::util::truthy(p.get("willRetry"));
                let fresh = turn
                    .as_ref()
                    .is_some_and(|x| !self.0.done_turns.lock().unwrap().contains(x));
                if turn.is_some() && !will_retry && fresh {
                    self.0
                        .done_turns
                        .lock()
                        .unwrap()
                        .insert(turn.clone().unwrap());
                    if let Some(tid) = &tid {
                        self.0.active_turn.lock().unwrap().remove(tid);
                    }
                    self.emit(event(
                        "turn_failed",
                        t,
                        u,
                        json!({"error": msg, "limit": is_limit(&msg)}),
                    ));
                } else {
                    self.emit(event(
                        "error",
                        t,
                        u,
                        json!({"error": msg, "limit": is_limit(&msg)}),
                    ));
                }
            }
            "thread/goal/updated" => {
                let goal = &p["goal"];
                let status = crate::goals::codex_status(goal);
                if ["active", "paused", "met", "stopped"].contains(&status.as_str()) {
                    let note = goal["note"]
                        .as_str()
                        .filter(|n| !n.is_empty())
                        .map(|n| json!(n))
                        .unwrap_or_else(|| {
                            if status == "stopped" {
                                goal.get("status").cloned().unwrap_or(json!(""))
                            } else {
                                json!("")
                            }
                        });
                    self.emit(event("goal", t, None, json!({"status": status, "objective": goal.get("objective").cloned().unwrap_or(Value::Null), "note": note})));
                }
            }
            "thread/goal/cleared" => self.emit(event(
                "goal",
                t,
                None,
                json!({"status": "cleared", "note": ""}),
            )),
            "thread/compacted" => self.compaction_ended(t, Ok(())),
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
        let (t, u) = (
            Some(thread.as_str()).filter(|t| !t.is_empty()),
            turn.as_deref(),
        );
        let mut adopt = |child: &str| {
            if !child.is_empty() && child != thread {
                self.0
                    .children
                    .lock()
                    .unwrap()
                    .insert(child.to_string(), thread.clone());
            }
        };
        if kind == "collabAgentToolCall" {
            item["receiverThreadIds"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .for_each(&mut adopt);
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
            if !child.is_empty() {
                self.note_running(&format!("agent:{child}"), status == "started", &thread);
            }
            if !self
                .0
                .tools_sent
                .lock()
                .unwrap()
                .insert(format!("agent:{child}:{status}"))
            {
                return;
            }
            let subagent = json!({"id": child, "parent_id": sub, "type": item.get("agentPath").cloned().unwrap_or(Value::Null),
                                  "model": item.get("model").cloned().unwrap_or(Value::Null), "status": status});
            self.emit(event(
                "tool",
                t,
                u,
                json!({"tool": "subagent", "status": status, "item_id": format!("agent:{child}"),
                                                 "parent_id": sub, "subagent": subagent}),
            ));
            return;
        }
        if kind == "userMessage" {
            // The input this runner sent, never a subagent's.
            if sub.is_none() {
                self.selected_skills(item, t, u);
            }
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
        self.note_running(&id, !done, &thread);
        if !self
            .0
            .tools_sent
            .lock()
            .unwrap()
            .insert(format!("{id}:{status}"))
        {
            return;
        }
        let (name, summary) = tool_of(item);
        let mut fields = json!({"tool": name, "status": status, "item_id": id, "parent_id": sub});
        if !done {
            fields["summary"] = json!(summary);
        }
        if kind == "commandExecution" && item["command"].is_string() {
            let command = item["command"].as_str().unwrap();
            fields["outside_actions"] = json!(shell_outside_actions(command));
            fields["reads_only"] = json!(shell_reads_only(command));
        }
        self.emit(event("tool", t, u, fields));
        let exit_code = item["exitCode"].as_i64();
        if done && (status == "failed" || exit_code.is_some_and(|c| c != 0)) {
            let (command, output) = failed_detail(item);
            self.emit(crate::run_diagnostics::failed_tool(
                &id, &name, &command, &output, exit_code,
            ));
        }
    }

    /// A tool call or subagent `item` of `thread` started (`on`) or ended: what `Kept::background` reads.
    fn note_running(&self, item: &str, on: bool, thread: &str) {
        if item.is_empty() {
            return;
        }
        let mut running = self.0.running.lock().unwrap();
        if on {
            running.insert(item.to_string(), thread.to_string());
        } else {
            running.remove(item);
        }
    }

    /// A `Skill` call, shaped as Claude's (hosts/claude.rs), for each skill the turn's input selects by name: a `skill`
    /// input item, which the app server echoes in the turn's userMessage item. It is Codex's only explicit selection
    /// signal: a `$name` in the text is resolved inside Codex with no event, and a SKILL.md the model reads is not a
    /// selection, so neither is counted. Only the skill's name leaves the computer, for the company's usage counts
    /// (backend/skill_runtime.py); the call ends as it starts, since the skill is loaded with the input.
    fn selected_skills(&self, item: &Value, t: Option<&str>, u: Option<&str>) {
        let id = text_of(&item["id"]);
        if id.is_empty() {
            return;
        }
        for input in item["content"].as_array().into_iter().flatten() {
            let Some(name) = input["name"]
                .as_str()
                .filter(|n| input["type"] == "skill" && crate::company_skills::name_ok(n))
            else {
                continue;
            };
            let call = format!("skill:{id}:{name}");
            if !self
                .0
                .tools_sent
                .lock()
                .unwrap()
                .insert(format!("{call}:started"))
            {
                continue;
            }
            self.emit(event("tool", t, u, json!({"tool": "Skill", "status": "started", "item_id": call, "summary": "", "parent_id": null, "skill": name})));
            self.0
                .tools_sent
                .lock()
                .unwrap()
                .insert(format!("{call}:completed"));
            self.emit(event(
                "tool",
                t,
                u,
                json!({"tool": "Skill", "status": "completed", "item_id": call, "parent_id": null}),
            ));
        }
    }

    /// The tokens one report adds: the first in this process counts `last`, later ones what the total grew by.
    fn token_increment(&self, thread: &str, usage: &Value) -> Value {
        let fields = |row: &Value| -> [i64; 3] {
            let c = |k: &str| match &row[k] {
                Value::Number(n) => n
                    .as_i64()
                    .or_else(|| n.as_f64().map(|f| f as i64))
                    .unwrap_or(0)
                    .max(0),
                Value::String(s) => s.trim().parse::<i64>().unwrap_or(0).max(0),
                _ => 0,
            };
            [c("inputTokens"), c("cachedInputTokens"), c("outputTokens")]
        };
        let (total, last) = (fields(&usage["total"]), fields(&usage["last"]));
        let before = self
            .0
            .token_seen
            .lock()
            .unwrap()
            .insert(thread.into(), total);
        let inc = match before {
            None => last,
            Some(b) => [
                (total[0] - b[0]).max(0),
                (total[1] - b[1]).max(0),
                (total[2] - b[2]).max(0),
            ],
        };
        json!({"input": inc[0], "cached": inc[1], "output": inc[2]})
    }

    fn thread_params(&self, settings: &Settings) -> Value {
        let mark = self
            .0
            .sweep
            .lock()
            .unwrap()
            .as_ref()
            .map(|t| t.mark.clone());
        let mut shell = settings.env.clone();
        // A process the keeper may hold reads the turn's token from its token file, as its commands and Tico's MCP
        // server do (crate::warm).
        if let (Some(dir), true) = (&self.0.tokens, shell.contains_key("HUB_TOKEN")) {
            shell.insert("HUB_TOKEN".into(), token_ref(dir));
        }
        self.params_for(settings, &shell, mark.as_deref())
    }

    /// The thread's parameters for `settings`, with `shell` as its commands' environment and `mark` (the sweep's).
    fn params_for(&self, settings: &Settings, shell: &Env, mark: Option<&str>) -> Value {
        let mut cfg = self.0.base_config.clone();
        let mut env = env_for_config(shell);
        // Codex's shell policy inherits only core variables. Detached commands on Linux need the mark too.
        if let Some(mark) = mark {
            env.insert(MARK_ENV.into(), json!(mark));
        }
        if !env.is_empty() {
            cfg.insert(
                "shell_environment_policy".into(),
                json!({"inherit": "core", "set": env}),
            );
        }
        // The hub's own MCP server rides next to the stubbed operator ones.
        let mut servers = cfg
            .get("mcp_servers")
            .and_then(|s| s.as_object())
            .cloned()
            .unwrap_or_default();
        // The bot's servers see the turn's own token: one whose header names it is never kept past its turn
        // (`fingerprint`).
        crate::mcp::codex_merge(
            &mut servers,
            crate::mcp::codex_config(&settings.mcp_servers, &settings.env, &self.0.env),
        );
        if let Some(hub) = hub_mcp_server(shell) {
            servers.insert("tico".into(), hub);
        }
        if !servers.is_empty() {
            cfg.insert("mcp_servers".into(), Value::Object(servers));
        }
        cfg.insert(
            "model_auto_compact_token_limit".into(),
            json!(super::compact_at(&settings.compact_at)),
        );
        // No memory under this computer's CODEX_HOME, even where the operator's config.toml turns it on: a bot
        // remembers in its repository's memory/ (Dev 301).
        cfg.insert("features.memories".into(), json!(false));
        // The bot's verbosity; none keeps the model's own.
        if let Some(level) = settings.verbosity.as_deref() {
            cfg.insert("model_verbosity".into(), json!(level));
        }
        let mut params = json!({"cwd": settings.cwd, "approvalPolicy": "never",
            "sandbox": "danger-full-access"});
        if let Some(m) = settings.model.as_ref().filter(|m| !m.is_empty()) {
            params["model"] = json!(m);
        }
        if let Some(plan) = &self.0.plan {
            params["modelProvider"] = json!(plan.provider);
        }
        params["config"] = Value::Object(cfg);
        params
    }

    /// Gives the app server the turn's staged company skills as its extra skill roots (`skills/extraRoots/set`, Codex
    /// 0.136 and later), or takes them away. The call holds for the whole app-server process, which runs this thread's
    /// turns (a kept one only when they are the same, `fingerprint`), so it is sent only when the roots change. It never fails the turn: an app server without it runs the turn
    /// without company skills.
    async fn set_skill_roots(&self, settings: &Settings) {
        let roots = skill_roots(settings);
        if *self.0.skill_roots.lock().unwrap() == roots {
            return;
        }
        match self
            .request(
                "skills/extraRoots/set",
                json!({"extraRoots": roots}),
                START_TIMEOUT,
            )
            .await
        {
            Ok(_) => *self.0.skill_roots.lock().unwrap() = roots,
            Err(e) => log(&format!("codex: running without company skills: {e}")),
        }
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        self.set_skill_roots(settings).await;
        let res = self
            .request("thread/start", self.thread_params(settings), START_TIMEOUT)
            .await?;
        res["thread"]["id"]
            .as_str()
            .filter(|t| !t.is_empty())
            .map(String::from)
            .ok_or_else(|| failed(format!("thread/start for {bot} returned no thread id")))
    }

    pub async fn resume_thread(
        &self,
        thread_id: &str,
        settings: &Settings,
    ) -> Result<String, HostError> {
        // A kept process (`take_kept`) has the thread loaded, started as this turn would start it: its next turn goes
        // straight in.
        if self.warm() && self.alive() && self.0.confirmed.lock().unwrap().contains(thread_id) {
            return Ok(thread_id.to_string());
        }
        self.set_skill_roots(settings).await;
        let mut params = self.thread_params(settings);
        params["threadId"] = json!(thread_id);
        params["excludeTurns"] = json!(true);
        let target = self.0.plan.as_ref().map_or("openai", |plan| plan.provider);
        let previous_provider = if self.routed() {
            self.request(
                "thread/read",
                json!({"threadId": thread_id, "includeTurns": false}),
                START_TIMEOUT,
            )
            .await
            .ok()
        } else {
            None
        };
        let result = if previous_provider
            .as_ref()
            .and_then(|r| r["thread"]["modelProvider"].as_str())
            .is_some_and(|provider| provider != target)
        {
            Err(failed(
                "The previous Codex thread uses another model provider",
            ))
        } else {
            self.request("thread/resume", params.clone(), START_TIMEOUT)
                .await
        };
        let mismatch = match &result {
            Ok(res) => res["thread"]["modelProvider"]
                .as_str()
                .is_some_and(|provider| {
                    provider != target
                        && (self.routed() || provider == crate::model_exit::CODEX_PROVIDER)
                }),
            Err(err) => {
                let detail = err.to_string();
                if self.routed() {
                    detail.to_ascii_lowercase().contains("model provider")
                } else {
                    detail.contains(crate::model_exit::CODEX_PROVIDER)
                }
            }
        };
        let res = if mismatch && !settings.require_existing_session {
            params.as_object_mut().unwrap().remove("excludeTurns");
            params["modelProvider"] = json!(target);
            self.request("thread/fork", params, START_TIMEOUT).await?
        } else if mismatch {
            return Err(failed(
                "The previous Codex thread uses another model provider",
            ));
        } else {
            result?
        };
        Ok(res["thread"]["id"]
            .as_str()
            .filter(|t| !t.is_empty())
            .unwrap_or(thread_id)
            .to_string())
    }

    /// A thread this host started a turn on (`turn/started`); None before.
    pub fn confirmed(&self, thread_id: &str) -> Option<String> {
        self.0
            .confirmed
            .lock()
            .unwrap()
            .contains(thread_id)
            .then(|| thread_id.to_string())
    }

    pub async fn start_turn(
        &self,
        thread_id: &str,
        text: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        let mut params = json!({"threadId": thread_id, "input": [{"type": "text", "text": text}]});
        if let Some(e) = effort.filter(|e| !e.is_empty()) {
            params["effort"] = json!(e);
        }
        let res = self.request("turn/start", params, REQUEST_TIMEOUT).await?;
        let turn = res["turn"]["id"]
            .as_str()
            .filter(|t| !t.is_empty())
            .map(String::from)
            .ok_or_else(|| failed("turn/start returned no turn id"))?;
        self.0
            .active_turn
            .lock()
            .unwrap()
            .insert(thread_id.into(), turn.clone());
        Ok(turn)
    }

    /// Write `text` into the running turn. Codex's replies for a turn that already ended (or one that is now
    /// another turn, or a compaction) wrote nothing, so they are refusals: the runner gives the input a turn of its own.
    pub async fn steer(&self, thread_id: &str, turn_id: &str, text: &str) -> Result<(), HostError> {
        match self.request("turn/steer", json!({"threadId": thread_id, "expectedTurnId": turn_id, "input": [{"type": "text", "text": text}]}), REQUEST_TIMEOUT).await {
            Ok(_) => Ok(()),
            Err(HostError::Failed(m)) if steer_turn_over(&m) => Err(HostError::SteerRefused(m)),
            Err(e) => Err(e),
        }
    }

    pub async fn start_goal(
        &self,
        thread_id: &str,
        action: &str,
        objective: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        let thread = json!({"threadId": thread_id});
        match action {
            "clear" => {
                self.request("thread/goal/clear", thread, REQUEST_TIMEOUT)
                    .await?;
                Ok(self.control_completed(thread_id))
            }
            "pause" => {
                if self
                    .request(
                        "thread/goal/set",
                        json!({"threadId": thread_id, "status": "paused"}),
                        REQUEST_TIMEOUT,
                    )
                    .await
                    .is_err()
                {
                    self.request("thread/goal/clear", thread, REQUEST_TIMEOUT)
                        .await?;
                }
                Ok(self.control_completed(thread_id))
            }
            _ => {
                self.request(
                    "thread/goal/set",
                    json!({"threadId": thread_id, "objective": objective, "status": "active"}),
                    REQUEST_TIMEOUT,
                )
                .await?;
                self.start_turn(thread_id, objective, effort).await
            }
        }
    }

    pub async fn start_goal_turn(
        &self,
        thread_id: &str,
        objective: &str,
        text: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        self.request(
            "thread/goal/set",
            json!({"threadId": thread_id, "objective": objective, "status": "active"}),
            REQUEST_TIMEOUT,
        )
        .await?;
        self.start_turn(thread_id, text, effort).await
    }

    /// A compaction this host asked for has ended (the first word on a thread counts; a later one changes nothing).
    fn compaction_ended(&self, thread: Option<&str>, result: Result<(), String>) {
        let Some(thread) = thread else { return };
        if let Some(slot) = self
            .0
            .compacting
            .lock()
            .unwrap()
            .get_mut(thread)
            .filter(|slot| slot.is_none())
        {
            *slot = Some(result);
            self.0.compacted.notify_waiters();
        }
    }

    /// Compacts a resumed thread (`thread/compact/start`) and returns once the app server says it is done, so the
    /// turn that follows runs on the shorter transcript. The thread's turn_completed ends the caller's wait.
    pub async fn compact(&self, thread_id: &str) -> Result<String, HostError> {
        self.0
            .compacting
            .lock()
            .unwrap()
            .insert(thread_id.into(), None);
        let waiting = self.0.compacted.notified();
        tokio::pin!(waiting);
        waiting.as_mut().enable();
        let started = self
            .request(
                "thread/compact/start",
                json!({"threadId": thread_id}),
                REQUEST_TIMEOUT,
            )
            .await;
        let ended = match started {
            Err(e) => Err(e.to_string()),
            Ok(_) => {
                let end = tokio::time::Instant::now() + COMPACT_TIMEOUT;
                loop {
                    if let Some(result) = self
                        .0
                        .compacting
                        .lock()
                        .unwrap()
                        .get(thread_id)
                        .cloned()
                        .flatten()
                    {
                        break result;
                    }
                    if tokio::time::timeout_at(end, waiting.as_mut())
                        .await
                        .is_err()
                    {
                        break Err("the compaction did not finish in time".into());
                    }
                    waiting.set(self.0.compacted.notified());
                    waiting.as_mut().enable();
                }
            }
        };
        self.0.compacting.lock().unwrap().remove(thread_id);
        ended.map_err(failed)?;
        Ok(self.control_completed(thread_id))
    }

    /// A control call that runs no model turn still ends one, so the runner's loop finishes as for any turn.
    fn control_completed(&self, thread_id: &str) -> String {
        let turn = uuid::Uuid::new_v4().to_string();
        self.emit(event(
            "turn_completed",
            Some(thread_id),
            Some(&turn),
            json!({"status": "completed"}),
        ));
        turn
    }

    /// `/compact` and `/review [instructions]` as the app server's own calls; anything else is a prompt.
    pub async fn start_command(
        &self,
        thread_id: &str,
        text: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        let trimmed = text.trim();
        let (name, args) = trimmed.split_once(' ').unwrap_or((trimmed, ""));
        match name {
            "/compact" => {
                self.request(
                    "thread/compact/start",
                    json!({"threadId": thread_id}),
                    REQUEST_TIMEOUT,
                )
                .await?;
                Ok(self.control_completed(thread_id))
            }
            "/review" => {
                let target = if args.is_empty() {
                    json!({"type": "uncommittedChanges"})
                } else {
                    json!({"type": "custom", "instructions": args})
                };
                let res = self
                    .request(
                        "review/start",
                        json!({"threadId": thread_id, "target": target}),
                        REQUEST_TIMEOUT,
                    )
                    .await?;
                let turn = res["turn"]["id"]
                    .as_str()
                    .filter(|t| !t.is_empty())
                    .map(String::from)
                    .ok_or_else(|| failed("review/start returned no turn id"))?;
                self.0
                    .active_turn
                    .lock()
                    .unwrap()
                    .insert(thread_id.into(), turn.clone());
                Ok(turn)
            }
            _ => self.start_turn(thread_id, text, effort).await,
        }
    }

    pub fn active_turn(&self, thread_id: &str) -> Option<String> {
        self.0.active_turn.lock().unwrap().get(thread_id).cloned()
    }

    /// A turn of `thread_id` is running: one begun whose `turn/completed` has not come (it can come before
    /// `start_turn` notes the turn as active).
    fn turn_running(&self, thread_id: &str) -> bool {
        self.active_turn(thread_id)
            .is_some_and(|turn| !self.0.done_turns.lock().unwrap().contains(&turn))
    }

    pub async fn interrupt(&self, thread_id: &str, turn_id: &str) -> Result<(), HostError> {
        let answered = self
            .request(
                "turn/interrupt",
                json!({"threadId": thread_id, "turnId": turn_id}),
                30,
            )
            .await
            .map(|_| ());
        self.0.interrupted.store(answered.is_ok(), Ordering::SeqCst);
        answered
    }
}

/// HUB_TOKEN for a process that reads the turn's token from its token file in `dir` (crate::warm).
fn token_ref(dir: &std::path::Path) -> String {
    format!("tico-file:{}", dir.join("lease-token").display())
}

impl Drop for Shared {
    fn drop(&mut self) {
        if let Some(dir) = &self.tokens {
            let _ = std::fs::remove_dir_all(dir);
        }
    }
}

/// Codex's adapter for the session keeper (crate::keeper): its app-server between a thread's turns, its token blanked
/// and its stderr going nowhere. Its reader runs on: what it says meanwhile is dropped, and what it asks answered as in a
/// turn. Its background work is a tool call or subagent still running (T3's hasPendingBackgroundWork).
struct Kept(Option<CodexHost>);

impl Session for Kept {
    fn alive(&self) -> bool {
        self.0.as_ref().is_some_and(CodexHost::alive)
    }

    fn lend(&self, token: &str) -> bool {
        self.0
            .as_ref()
            .and_then(|host| host.0.tokens.as_ref())
            .is_some_and(|dir| crate::warm::write_token_for(&dir.join("lease-token"), token, None))
    }

    fn background(&self) -> Option<crate::keeper::Probe> {
        let running = self.0.as_ref()?.0.running.clone();
        Some(Arc::new(move || !running.lock().unwrap().is_empty()))
    }

    fn park(self: Box<Self>, wanted: oneshot::Receiver<()>) -> crate::keeper::Parked {
        let ended = self.0.as_ref().map(|host| {
            *host.0.tx.lock().unwrap() = None;
            *host.0.capture.lock().unwrap() = None;
            host.0.ended.subscribe()
        });
        Box::pin(async move {
            if let Some(mut ended) = ended {
                tokio::select! {
                    _ = wanted => {}
                    _ = ended.wait_for(|e| *e) => {}
                }
            }
            self as Box<dyn Session>
        })
    }

    /// Stopped and reaped, and what its commands left swept (`finish_turn`), as at the end of a turn.
    fn stop(&self) -> crate::keeper::Stopping<'_> {
        Box::pin(async move {
            if let Some(host) = &self.0 {
                for report in host.finish_turn().await {
                    log(&format!("codex: a kept app-server's cleanup: {report}"));
                }
            }
        })
    }

    /// SIGKILL to its group now, then until it has gone.
    fn kill(&self) -> crate::keeper::Kill {
        let host = self.0.clone();
        Arc::new(move || {
            let host = host.clone();
            Box::pin(async move {
                let Some(host) = host else { return };
                let stopper = host
                    .0
                    .child
                    .lock()
                    .unwrap()
                    .as_ref()
                    .map(|(s, _)| s.clone());
                if let Some(stopper) = stopper {
                    stopper.terminate(Duration::ZERO);
                }
                let mut ended = host.0.ended.subscribe();
                let _ = ended.wait_for(|e| *e).await;
            })
        })
    }

    fn into_any(self: Box<Self>) -> Box<dyn std::any::Any + Send> {
        self
    }
}

impl Drop for Kept {
    fn drop(&mut self) {
        let Some(host) = self.0.take() else { return };
        if host.alive() {
            if let Some((stopper, _)) = host.0.child.lock().unwrap().as_ref() {
                stopper.terminate(Duration::from_secs(5));
            }
        }
    }
}

const DECLINED: &str = "Tico runs this bot unattended, so no one can approve this request.";

/// The runner's answer to a request the app server sends it: the reply, the decision for the log, and whether the
/// request should not have come (a diagnostic). Threads run with `approvalPolicy: never` and full access, so an
/// approval request is unexpected; one that comes is declined, which lets the turn go on without it (`cancel` or
/// `abort` would end the turn). No one is there to answer a question, so an MCP elicitation is cancelled and a
/// `requestUserInput` (whose answer has no cancel) gets no answers. The runner registers no dynamic tools, and
/// declines Codex requests to refresh external tokens (managed plans refresh through the exit lease) and
/// attestation. Methods this list does not know also get "method not found".
fn server_request_reply(method: &str) -> (Reply, &'static str, bool) {
    let not_found = |m: &str| {
        Err(json!({"code": -32601, "message": format!("the runner does not answer {m}")}))
    };
    match method {
        "item/commandExecution/requestApproval" | "item/fileChange/requestApproval" => {
            (Ok(json!({"decision": "decline"})), "declined", true)
        }
        "item/permissions/requestApproval" => (
            Ok(json!({"permissions": {}, "scope": "turn"})),
            "declined (granted nothing)",
            true,
        ),
        "execCommandApproval" | "applyPatchApproval" => (
            Ok(json!({"decision": {"denied": {"rejection": DECLINED}}})),
            "declined",
            true,
        ),
        "mcpServer/elicitation/request" => (Ok(json!({"action": "cancel"})), "cancelled", false),
        "item/tool/requestUserInput" => (Ok(json!({"answers": {}})), "no answers", false),
        "item/tool/call" => (
            Ok(
                json!({"contentItems": [{"type": "inputText", "text": "Tico registered no dynamic tools."}], "success": false}),
            ),
            "failed (no dynamic tools)",
            true,
        ),
        "account/chatgptAuthTokens/refresh" | "attestation/generate" => {
            (not_found(method), "method not found", false)
        }
        _ => (not_found(method), "method not found (unknown)", true),
    }
}

// Codex names an overload or quota refusal in the error's structure (`codexErrorInfo`, or the API's `type`), and its
// message may say neither ("Server busy"). Name the kind in the text the limit rules read; a message that already
// reads as a limit is kept as it is, so a genuine quota is never relabelled as capacity.
fn with_kind(err: &Value, msg: String, managed: bool) -> String {
    let info = &err["codexErrorInfo"];
    let kind = info
        .as_str()
        .map(String::from)
        .or_else(|| info.as_object().and_then(|o| o.keys().next().cloned()))
        .or_else(|| err["type"].as_str().map(String::from))
        .or_else(|| err["error"]["type"].as_str().map(String::from))
        .unwrap_or_default()
        .to_lowercase();
    let status = info
        .as_object()
        .and_then(|o| o.values().next())
        .and_then(|v| v["httpStatusCode"].as_u64());
    let tag = if managed
        && (status == Some(401)
            || kind == "unauthorized"
            || msg.to_ascii_lowercase().contains("401 unauthorized"))
    {
        "authentication_error"
    } else if kind.contains("overloaded") {
        "overloaded"
    } else if kind.contains("usagelimit") || kind.contains("usage_limit") {
        "usage limit"
    } else if status == Some(429) {
        "status 429"
    } else if status == Some(503) {
        "status 503"
    } else {
        return msg;
    };
    if is_limit(&msg) {
        msg
    } else {
        format!("{msg} ({tag})")
    }
}

/// Codex's `turn/steer` errors that mean the expected turn is no longer one input can be written into.
fn steer_turn_over(message: &str) -> bool {
    [
        "no active turn to steer",
        "expected active turn id",
        "cannot steer a compact turn",
    ]
    .iter()
    .any(|m| message.contains(m))
}

#[cfg(test)]
mod tests {
    #[tokio::test(start_paused = true)]
    async fn a_reaper_that_never_finishes_cannot_hold_the_attempt_open() {
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Default::default(), tx, None);
        host.prepare_sweep();
        let (still_waiting, reaped) = tokio::sync::watch::channel(false);
        *host.0.reaped.lock().unwrap() = Some(reaped);
        let start = tokio::time::Instant::now();
        let report = host.finish_turn().await;
        assert_eq!(start.elapsed(), Duration::from_secs(6));
        assert!(report[0]["text"].as_str().unwrap().contains("Timed out"));
        assert!(
            still_waiting.send(true).is_ok(),
            "the reaper still owns the watch"
        );
    }

    #[tokio::test]
    async fn a_failed_wait_reports_incomplete_cleanup() {
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Default::default(), tx, None);
        host.prepare_sweep();
        let (failed, reaped) = tokio::sync::watch::channel(false);
        *host.0.reaped.lock().unwrap() = Some(reaped);
        drop(failed);
        let report = host.finish_turn().await;
        assert!(report[0]["text"]
            .as_str()
            .unwrap()
            .contains("Could not reap"));
        assert!(report[0].get("leftover_processes").is_none());
    }

    use super::*;

    #[tokio::test]
    async fn every_codex_fixture_gives_the_python_hosts_events() {
        for (path, mut expected) in crate::hosts::fixtures::cases("codex") {
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
            let host = CodexHost::on_home(Env::new(), tx, None);
            for line in std::fs::read_to_string(&path)
                .unwrap()
                .lines()
                .filter(|l| !l.trim().is_empty())
            {
                host.on_message(serde_json::from_str(line).unwrap()).await;
            }
            let mut got = vec![];
            while let Ok(ev) = rx.try_recv() {
                // A failed call's detail goes to the run's capture, not the event stream (a test below).
                if ev.get("failed_tool").is_none() {
                    got.push(ev);
                }
            }
            // These shared Python snapshots hold no outside actions; Rust adds recovery labels and whether each
            // line only reads (`cat`, `wc` and `ls` do, c3's `curl` does not).
            for event in &mut expected {
                if event["tool"] == "shell" {
                    event["outside_actions"] = json!([]);
                    event["reads_only"] = json!(event["item_id"] != "c3");
                }
            }
            assert_eq!(got, expected, "{}", path.display());
        }
    }

    #[tokio::test]
    async fn a_failed_call_keeps_its_command_and_output_tail_scrubbed_and_a_good_one_keeps_nothing()
    {
        let path = crate::hosts::fixtures::dir("codex").join("failed-tools/stream.jsonl");
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        for line in std::fs::read_to_string(&path).unwrap().lines() {
            host.on_message(serde_json::from_str(line).unwrap()).await;
        }
        let mut failed = vec![];
        let mut tools = vec![];
        while let Ok(ev) = rx.try_recv() {
            if ev["kind"] == "diagnostic" && ev["failed_tool"].is_object() {
                failed.push(ev["failed_tool"].clone());
            } else if ev["kind"] == "tool" {
                tools.push(ev);
            }
        }
        assert!(
            tools
                .iter()
                .all(|t| t.get("output_tail").is_none() && t.get("command").is_none()),
            "tool events stay as they were: {tools:?}"
        );
        assert_eq!(
            failed
                .iter()
                .map(|f| f["item_id"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["call_ui", "call_node"],
            "only failed calls"
        );
        let shell = &failed[0];
        assert_eq!(
            (shell["tool"].as_str(), shell["exit_code"].as_i64()),
            (Some("shell"), Some(1))
        );
        assert!(shell["command"]
            .as_str()
            .unwrap()
            .contains("npx playwright test ui/tests/login.spec.ts"));
        let tail = shell["output_tail"].as_str().unwrap();
        assert!(
            tail.contains("Executable doesn't exist")
                && tail.ends_with("1 failed, 50 passed (31.2s)")
        );
        assert!(
            tail.lines().count() <= 40 && tail.len() <= 4096 && !tail.contains("renders row 1\n")
        );
        let all = serde_json::to_string(&failed).unwrap();
        assert!(
            !all.contains("tico_live_7f3a9c2e1b4d8f6a0e5c3b7d9a1f2e4c"),
            "{all}"
        );
        let node = &failed[1];
        assert_eq!(node["tool"], "node");
        assert!(node["command"]
            .as_str()
            .unwrap()
            .contains("chromium.launch()"));
        assert_eq!(
            node["output_tail"],
            "Error: Chromium is not installed (ENOENT)"
        );
    }

    #[test]
    fn recovery_shell_events_match_the_shared_api_fixture() {
        let cases: Vec<Value> = serde_json::from_str(include_str!(
            "../../tests/fixtures/recovery-shell-events.json"
        ))
        .unwrap();
        for case in cases {
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
            let host = CodexHost::on_home(Env::new(), tx, None);
            host.on_item(
                false,
                &json!({"threadId": "th", "turnId": "tu", "item": {
                "type": "commandExecution", "id": "tool-1", "command": case["command"]}}),
            );
            let got = rx.try_recv().unwrap();
            assert_eq!(got, case["codex"], "{}", case["command"]);
            assert!(!got.to_string().contains("synthetic-private-marker"));
        }
    }

    #[test]
    fn the_operators_servers_are_stubbed_and_the_environment_trimmed() {
        let dir =
            std::env::temp_dir().join(format!("codex-home-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("config.toml"), "[mcp_servers.local]\ncommand = \"npx\"\n[mcp_servers.remote]\nurl = \"https://acme.example/mcp\"\n").unwrap();
        let cfg = mcp_disable_config(Some(dir.to_str().unwrap()));
        assert_eq!(
            Value::Object(cfg),
            json!({"mcp_servers": {"local": {"command": "/usr/bin/true", "args": []}, "remote": {"startup_timeout_ms": 1}}})
        );
        let env: Env = [
            ("PWD", "/x"),
            ("ACME_KEY", "v"),
            ("BIG", &"x".repeat(9000)[..]),
        ]
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        assert_eq!(
            Value::Object(env_for_config(&env)),
            json!({"ACME_KEY": "v"})
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn no_thread_keeps_a_codex_memory_outside_the_repository() {
        let host = CodexHost::on_home(Env::new(), tokio::sync::mpsc::unbounded_channel().0, None);
        assert_eq!(
            host.thread_params(&Settings::default())["config"]["features.memories"],
            json!(false)
        );
    }

    #[test]
    fn a_thread_gets_the_bots_verbosity_and_none_on_default() {
        let host = CodexHost::on_home(Env::new(), tokio::sync::mpsc::unbounded_channel().0, None);
        let settings = Settings {
            verbosity: Some("low".into()),
            ..Default::default()
        };
        assert_eq!(
            host.thread_params(&settings)["config"]["model_verbosity"],
            json!("low")
        );
        assert!(host.thread_params(&Settings::default())["config"]
            .get("model_verbosity")
            .is_none());
        // A Claude output style is not Codex's.
        let styled = Settings {
            output_style: Some("Concise".into()),
            ..Default::default()
        };
        assert!(!host.thread_params(&styled).to_string().contains("Concise"));
    }

    #[tokio::test]
    async fn a_thread_is_confirmed_by_its_first_turn_not_by_thread_start() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        host.on_message(json!({"method": "thread/started", "params": {"thread": {"id": "thr_n"}}}))
            .await;
        assert_eq!(
            host.confirmed("thr_n"),
            None,
            "a thread/start alone is only in this app server"
        );
        // A subagent's turn confirms nothing of the parent's.
        host.on_message(json!({"method": "thread/started", "params": {"thread": {"id": "thr_sub", "parentThreadId": "thr_n"}}})).await;
        host.on_message(json!({"method": "turn/started", "params": {"threadId": "thr_sub", "turn": {"id": "turn_s"}}})).await;
        assert_eq!(host.confirmed("thr_sub"), None);
        for turn in ["turn_1", "turn_2"] {
            host.on_message(json!({"method": "turn/started", "params": {"threadId": "thr_n", "turn": {"id": turn}}})).await;
        }
        assert_eq!(host.confirmed("thr_n").as_deref(), Some("thr_n"));
        let confirmed: Vec<Value> = std::iter::from_fn(|| rx.try_recv().ok())
            .filter(|e| e["kind"] == "thread_confirmed")
            .collect();
        assert_eq!(
            confirmed,
            vec![
                json!({"kind": "thread_confirmed", "thread_id": "thr_n", "turn_id": "turn_1", "session_id": "thr_n"})
            ]
        );
    }

    #[test]
    fn company_skills_are_a_root_of_the_runners_own_never_codex_home() {
        let base =
            std::env::temp_dir().join(format!("codex-skills-{}", uuid::Uuid::new_v4().simple()));
        let (state, home) = (base.join("state"), base.join("codex-home"));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::write(home.join("config.toml"), "model = \"gpt-5.5\"\n").unwrap();
        let files = vec![crate::company_skills::File {
            path: "SKILL.md".into(),
            content: "---\nname: acme-brief\ndescription: Write the Acme brief\n---\nWrite it.\n"
                .into(),
        }];
        let sha = crate::company_skills::bundle_sha(&files);
        let (staged, _) =
            crate::company_skills::stage(&state, "ops", &[("acme-brief".into(), sha, files)])
                .unwrap();
        let settings = Settings {
            company_skills: Some(staged.display().to_string()),
            ..Settings::default()
        };
        let roots = skill_roots(&settings);
        assert_eq!(
            roots,
            vec![staged.join(".claude").join("skills").display().to_string()]
        );
        // Codex's layout (one folder per skill, with its SKILL.md), in the runner's state; nothing under CODEX_HOME.
        assert!(std::path::Path::new(&roots[0])
            .join("acme-brief")
            .join("SKILL.md")
            .is_file());
        assert!(roots[0].starts_with(
            &state
                .join("skill-sessions")
                .join("ops-")
                .display()
                .to_string()
        ));
        assert!(!home.join("skills").exists());
        assert!(skill_roots(&Settings::default()).is_empty());
        let host = CodexHost::on_home(
            Env::new(),
            tokio::sync::mpsc::unbounded_channel().0,
            Some(home.to_str().unwrap()),
        );
        assert!(
            !host
                .thread_params(&settings)
                .to_string()
                .contains("skill-sessions"),
            "the root goes only to skills/extraRoots/set"
        );
        std::fs::remove_dir_all(base).unwrap();
    }

    /// A fake app server that records each request it answers; `FAKE_REFUSE_ROOTS` makes it an app server without
    /// `skills/extraRoots/set`. On `initialize` it sends each method in `FAKE_ASK` as a request of its own and
    /// records the runner's answers in `FAKE_ANSWERS`.
    const FAKE_APP_SERVER: &str = r#"#!/usr/bin/env python3
import json, os, sys
def say(m): print(json.dumps(m), flush=True)
threads = 0
for line in sys.stdin:
    msg = json.loads(line)
    method, p = msg.get("method"), msg.get("params") or {}
    if "id" not in msg:
        continue
    if method is None:
        open(os.environ["FAKE_ANSWERS"], "a").write(line)
        continue
    open(os.environ["FAKE_CALLS"], "a").write(json.dumps({"method": method, "params": p}) + "\n")
    if method == "initialize":
        for n, asked in enumerate(json.loads(os.environ.get("FAKE_ASK") or "[]")):
            say({"id": "s%d" % n, "method": asked, "params": {"threadId": "th-1"}})
    if method == "skills/extraRoots/set" and os.environ.get("FAKE_REFUSE_ROOTS"):
        say({"id": msg["id"], "error": {"code": -32601, "message": "method not found"}})
    elif method == "skills/extraRoots/set":
        say({"id": msg["id"], "result": {}})
        say({"method": "skills/changed", "params": {}})
    elif method == "thread/start":
        threads += 1
        say({"id": msg["id"], "result": {"thread": {"id": "th-%d" % threads}}})
    elif method == "thread/resume":
        say({"id": msg["id"], "result": {"thread": {"id": p["threadId"]}}})
    elif method == "turn/steer" and os.environ.get("FAKE_STEER_ERROR"):
        say({"id": msg["id"], "error": {"code": -32600, "message": os.environ["FAKE_STEER_ERROR"]}})
    else:
        say({"id": msg["id"], "result": {}})
"#;

    /// A message steered just as the Codex turn ended is refused, so the runner carries it into a new turn instead of
    /// stopping the attempt partway (Nov 12: `turn/steer: no active turn to steer` left a review waiting on a person).
    #[tokio::test]
    async fn a_steer_into_a_turn_that_just_ended_is_refused_not_failed() {
        let dir =
            std::env::temp_dir().join(format!("codex-steer-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        crate::util::write_script(&dir.join("codex"), FAKE_APP_SERVER);
        let path = format!(
            "{}:{}",
            dir.display(),
            std::env::var("PATH").unwrap_or_default()
        );
        let steer = |reply: &str| {
            let env: Env = [
                ("PATH".to_string(), path.clone()),
                (
                    "FAKE_CALLS".to_string(),
                    dir.join("calls.jsonl").display().to_string(),
                ),
                ("FAKE_STEER_ERROR".to_string(), reply.to_string()),
            ]
            .into_iter()
            .filter(|(_, v)| !v.is_empty())
            .collect();
            async move {
                let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
                host.start().await.unwrap();
                let got = host.steer("th-1", "turn-1", "Judge: unsure").await;
                host.stop().await;
                got
            }
        };
        for reply in [
            "no active turn to steer",
            "expected active turn id `turn-1` but found `turn-2`",
            "cannot steer a compact turn",
        ] {
            assert!(
                matches!(steer(reply).await, Err(HostError::SteerRefused(m)) if m == format!("turn/steer: {reply}")),
                "{reply}"
            );
        }
        assert!(matches!(
            steer("input must not be empty").await,
            Err(HostError::Failed(_))
        ));
        assert!(steer("").await.is_ok());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn the_app_server_gets_the_turns_skill_root_once_and_a_refusal_still_runs_the_turn() {
        let dir =
            std::env::temp_dir().join(format!("codex-roots-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let program = dir.join("codex");
        crate::util::write_script(&program, FAKE_APP_SERVER);
        let path = format!(
            "{}:{}",
            dir.display(),
            std::env::var("PATH").unwrap_or_default()
        );
        let calls = |file: &std::path::Path| -> Vec<Value> {
            std::fs::read_to_string(file)
                .unwrap_or_default()
                .lines()
                .map(|l| serde_json::from_str(l).unwrap())
                .collect()
        };
        let staged = Settings {
            company_skills: Some("/state/skill-sessions/a-1".into()),
            ..Settings::default()
        };
        let root = json!(["/state/skill-sessions/a-1/.claude/skills"]);

        let log = dir.join("calls.jsonl");
        let env: Env = [
            ("PATH".to_string(), path.clone()),
            ("FAKE_CALLS".to_string(), log.display().to_string()),
        ]
        .into_iter()
        .collect();
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
        host.start().await.unwrap();
        assert_eq!(host.start_thread("ops", &staged).await.unwrap(), "th-1");
        assert_eq!(host.resume_thread("th-1", &staged).await.unwrap(), "th-1");
        host.start_thread("ops", &Settings::default())
            .await
            .unwrap();
        host.stop().await;
        let got = calls(&log);
        let methods: Vec<&str> = got.iter().map(|c| c["method"].as_str().unwrap()).collect();
        assert_eq!(
            methods,
            [
                "initialize",
                "skills/extraRoots/set",
                "thread/start",
                "thread/resume",
                "skills/extraRoots/set",
                "thread/start"
            ],
            "set before the thread starts, again only when the roots change"
        );
        assert_eq!(got[1]["params"], json!({"extraRoots": root}));
        assert_eq!(
            got[4]["params"],
            json!({"extraRoots": []}),
            "a turn without company skills takes them away"
        );

        // An app server too old for the call: the thread still starts, without company skills.
        let log = dir.join("refused.jsonl");
        let env: Env = [
            ("PATH".to_string(), path),
            ("FAKE_CALLS".to_string(), log.display().to_string()),
            ("FAKE_REFUSE_ROOTS".to_string(), "1".to_string()),
        ]
        .into_iter()
        .collect();
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
        host.start().await.unwrap();
        assert_eq!(host.start_thread("ops", &staged).await.unwrap(), "th-1");
        host.stop().await;
        let methods: Vec<String> = calls(&log).iter().map(|c| text_of(&c["method"])).collect();
        assert_eq!(
            methods,
            ["initialize", "skills/extraRoots/set", "thread/start"]
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// Every request Codex can send gets an answer its schema accepts (`codex app-server generate-json-schema`,
    /// codex-cli 0.169.0): a stray approval is declined so the turn goes on, a question is let go, and only what the
    /// runner never set up, or does not know, is "method not found".
    #[test]
    fn each_server_request_gets_a_deliberate_answer() {
        let answer = |m: &str| server_request_reply(m);
        for m in [
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        ] {
            assert_eq!(
                answer(m),
                (Ok(json!({"decision": "decline"})), "declined", true),
                "{m}"
            );
        }
        assert_eq!(
            answer("item/permissions/requestApproval").0,
            Ok(json!({"permissions": {}, "scope": "turn"})),
            "grants nothing"
        );
        for m in ["execCommandApproval", "applyPatchApproval"] {
            let (reply, _, unexpected) = answer(m);
            assert!(unexpected);
            assert!(
                reply.unwrap()["decision"]["denied"]["rejection"].is_string(),
                "{m}: denied, not abort"
            );
        }
        assert_eq!(
            answer("mcpServer/elicitation/request"),
            (Ok(json!({"action": "cancel"})), "cancelled", false)
        );
        assert_eq!(
            answer("item/tool/requestUserInput").0,
            Ok(json!({"answers": {}}))
        );
        let tool = answer("item/tool/call").0.unwrap();
        assert_eq!(tool["success"], json!(false));
        assert_eq!(tool["contentItems"][0]["type"], json!("inputText"));
        for m in [
            "account/chatgptAuthTokens/refresh",
            "attestation/generate",
            "item/somethingNew",
        ] {
            assert_eq!(answer(m).0.unwrap_err()["code"], json!(-32601), "{m}");
        }
        assert!(!answer("account/chatgptAuthTokens/refresh").2);
        assert!(
            answer("item/somethingNew").2,
            "an unknown request is a diagnostic"
        );
    }

    /// The answer goes back on the request's own id, as a result or an error.
    #[tokio::test]
    async fn a_server_request_is_answered_on_its_own_id() {
        let dir =
            std::env::temp_dir().join(format!("codex-asks-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        crate::util::write_script(&dir.join("codex"), FAKE_APP_SERVER);
        let answers = dir.join("answers.jsonl");
        let env: Env = [
            (
                "PATH".to_string(),
                format!(
                    "{}:{}",
                    dir.display(),
                    std::env::var("PATH").unwrap_or_default()
                ),
            ),
            (
                "FAKE_CALLS".to_string(),
                dir.join("calls.jsonl").display().to_string(),
            ),
            ("FAKE_ANSWERS".to_string(), answers.display().to_string()),
            (
                "FAKE_ASK".to_string(),
                json!([
                    "item/commandExecution/requestApproval",
                    "attestation/generate"
                ])
                .to_string(),
            ),
        ]
        .into_iter()
        .collect();
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
        host.start().await.unwrap();
        // The fake reads its input in order, so once this is answered the answers above are written.
        host.request("noop", json!({}), 10).await.unwrap();
        host.stop().await;
        let got: Vec<Value> = std::fs::read_to_string(&answers)
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(
            got,
            [
                json!({"jsonrpc": "2.0", "id": "s0", "result": {"decision": "decline"}}),
                json!({"jsonrpc": "2.0", "id": "s1", "error": {"code": -32601, "message": "the runner does not answer attestation/generate"}}),
            ]
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn only_a_skill_input_item_counts_as_a_skill_use() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        let item = |method: &str, thread: &str, item: Value| json!({"method": method, "params": {"threadId": thread, "turnId": "turn_1", "item": item}});
        let path = "/state/skill-sessions/a-1/.claude/skills/acme-brief/SKILL.md";
        host.on_message(json!({"method": "turn/started", "params": {"threadId": "thr", "turn": {"id": "turn_1"}}})).await;
        let selected = json!({"type": "userMessage", "id": "um_1", "content": [
            {"type": "text", "text": "Use $acme-notes for the Acme brief", "text_elements": []},
            {"type": "skill", "name": "acme-brief", "path": path},
            {"type": "skill", "name": "../acme", "path": "/elsewhere/SKILL.md"},
            {"type": "mention", "name": "acme-app", "path": "app://acme"}]});
        // Started and completed both carry the input: one use.
        host.on_message(item("item/started", "thr", selected.clone()))
            .await;
        host.on_message(item("item/completed", "thr", selected))
            .await;
        // No selection: a `$name` typed in the text, the model reading a SKILL.md, a subagent's input.
        host.on_message(item("item/completed", "thr", json!({"type": "userMessage", "id": "um_2", "content": [{"type": "text", "text": "$acme-brief again", "text_elements": []}]}))).await;
        host.on_message(item(
            "item/started",
            "thr",
            json!({"type": "commandExecution", "id": "c1", "command": format!("cat {path}")}),
        ))
        .await;
        host.on_message(json!({"method": "thread/started", "params": {"thread": {"id": "thr_sub", "parentThreadId": "thr"}}})).await;
        host.on_message(item("item/completed", "thr_sub", json!({"type": "userMessage", "id": "um_3", "content": [{"type": "skill", "name": "acme-brief", "path": path}]}))).await;
        let events: Vec<Value> = std::iter::from_fn(|| rx.try_recv().ok()).collect();
        let skills: Vec<&Value> = events.iter().filter(|e| e["tool"] == "Skill").collect();
        assert_eq!(
            skills,
            vec![
                &json!({"kind": "tool", "thread_id": "thr", "turn_id": "turn_1", "tool": "Skill", "status": "started", "item_id": "skill:um_1:acme-brief",
                    "summary": "", "parent_id": null, "skill": "acme-brief"}),
                &json!({"kind": "tool", "thread_id": "thr", "turn_id": "turn_1", "tool": "Skill", "status": "completed", "item_id": "skill:um_1:acme-brief", "parent_id": null}),
            ]
        );
        assert!(
            !serde_json::to_string(&events).unwrap().contains(path),
            "the skill's name leaves, never its path"
        );
    }

    #[tokio::test]
    async fn a_structured_overload_is_capacity_and_a_quota_stays_a_quota() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        for line in [
            r#"{"method": "turn/started", "params": {"threadId": "thr_o", "turn": {"id": "turn_1"}}}"#,
            r#"{"method": "error", "params": {"threadId": "thr_o", "turnId": "turn_1", "willRetry": false, "error": {"message": "Server busy", "codexErrorInfo": "serverOverloaded"}}}"#,
            r#"{"method": "turn/started", "params": {"threadId": "thr_o", "turn": {"id": "turn_2"}}}"#,
            r#"{"method": "turn/completed", "params": {"threadId": "thr_o", "turn": {"id": "turn_2", "status": "failed", "error": {"message": "Quota exceeded. Check your plan and billing details.", "codexErrorInfo": "serverOverloaded"}}}}"#,
        ] {
            host.on_message(serde_json::from_str(line).unwrap()).await;
        }
        let failed: Vec<Value> = std::iter::from_fn(|| rx.try_recv().ok())
            .filter(|e| e["kind"] == "turn_failed")
            .collect();
        assert_eq!(failed.len(), 2);
        let (busy, quota) = (
            failed[0]["error"].as_str().unwrap(),
            failed[1]["error"].as_str().unwrap(),
        );
        assert!(
            failed[0]["limit"] == true && crate::hosts::is_capacity(busy),
            "{busy}"
        );
        assert!(
            failed[1]["limit"] == true && !crate::hosts::is_capacity(quota),
            "{quota}"
        );
    }

    /// An app server whose `LONG` turn runs until `turn/interrupt`, which it answers and then ends the turn; any other
    /// turn ends at once. Each call goes to `FAKE_CALLS`.
    const FAKE_KEPT: &str = r#"#!/usr/bin/env python3
import json, os, sys
def say(m): print(json.dumps(m), flush=True)
n = 0
for line in sys.stdin:
    msg = json.loads(line)
    method, p = msg.get("method"), msg.get("params") or {}
    if method is None or "id" not in msg:
        continue
    open(os.environ["FAKE_CALLS"], "a").write(json.dumps({"method": method, "pid": os.getpid()}) + "\n")
    i = msg["id"]
    if method == "thread/start":
        say({"id": i, "result": {"thread": {"id": "th-1"}}})
    elif method == "turn/start":
        n += 1
        t, th = "t%d" % n, p["threadId"]
        say({"id": i, "result": {"turn": {"id": t}}})
        say({"method": "turn/started", "params": {"threadId": th, "turn": {"id": t}}})
        if p["input"][0]["text"] != "LONG":
            say({"method": "turn/completed", "params": {"threadId": th, "turn": {"id": t, "status": "completed"}}})
    elif method == "turn/interrupt":
        say({"id": i, "result": {}})
        say({"method": "turn/completed", "params": {"threadId": p["threadId"], "turn": {"id": p["turnId"], "status": "interrupted"}}})
    else:
        say({"id": i, "result": {}})
"#;

    /// A folder with `FAKE_KEPT` as `codex`, and the environment that runs it.
    fn kept_rig(name: &str) -> (PathBuf, Env) {
        let dir = std::env::temp_dir().join(format!("{name}-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        crate::util::write_script(&dir.join("codex"), FAKE_KEPT);
        let env: Env = [
            (
                "PATH".to_string(),
                format!(
                    "{}:{}",
                    dir.display(),
                    std::env::var("PATH").unwrap_or_default()
                ),
            ),
            (
                "FAKE_CALLS".to_string(),
                dir.join("calls.jsonl").display().to_string(),
            ),
        ]
        .into_iter()
        .collect();
        (dir, env)
    }

    fn turn_settings(token: &str, model: &str) -> Settings {
        Settings {
            model: Some(model.into()),
            env: [("HUB_TOKEN".to_string(), token.to_string())].into(),
            ..Settings::default()
        }
    }

    async fn ended(host: &CodexHost, turn: &str) {
        let by = tokio::time::Instant::now() + Duration::from_secs(10);
        while !host.0.done_turns.lock().unwrap().contains(turn) {
            assert!(tokio::time::Instant::now() < by, "turn {turn} never ended");
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    /// A turn a Stop interrupted leaves its app-server to the keeper once Codex has ended it; the thread's next turn
    /// takes that process (warm), lent its own token, with no thread/resume. One that would start it with another
    /// model stops it and starts its own.
    #[tokio::test]
    async fn an_interrupted_turn_keeps_its_app_server_for_the_threads_next_turn() {
        let (dir, mut env) = kept_rig("codex-kept");
        env.insert("HUB_TOKEN".into(), "turn-1".into());
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let events = || tokio::sync::mpsc::unbounded_channel().0;
        let first = CodexHost::on_home(env.clone(), events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let one = turn_settings("turn-1", "gpt-5.5");
        assert!(first.take_kept(None, &one).await.is_none());
        first.start().await.unwrap();
        let th = first.start_thread("ops", &one).await.unwrap();
        let turn = first.start_turn(&th, "LONG", None).await.unwrap();
        first.interrupt(&th, &turn).await.unwrap();
        assert!(
            first.release(Some(&th), None).await,
            "kept after Codex ended the turn"
        );
        let file = first.0.tokens.clone().unwrap().join("lease-token");
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "");
        assert_eq!(keeper.count(), 1);

        let next = CodexHost::on_home(env.clone(), events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let two = turn_settings("turn-2", "gpt-5.5");
        let kept = next.take_kept(Some(&th), &two).await.expect("warm");
        assert!(Arc::ptr_eq(&kept.0, &first.0) && kept.warm() && kept.alive());
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "turn-2");
        assert_eq!(kept.resume_thread(&th, &two).await.unwrap(), th);
        let turn = kept.start_turn(&th, "Go on", None).await.unwrap();
        ended(&kept, &turn).await;
        assert!(kept.release(Some(&th), None).await);
        let methods: Vec<String> = std::fs::read_to_string(dir.join("calls.jsonl"))
            .unwrap()
            .lines()
            .map(|l| text_of(&serde_json::from_str::<Value>(l).unwrap()["method"]))
            .filter(|m| m.starts_with("thread/") || m.starts_with("turn/"))
            .collect();
        assert_eq!(
            methods,
            ["thread/start", "turn/start", "turn/interrupt", "turn/start"]
        );

        let other = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let three = turn_settings("turn-3", "gpt-5.4");
        assert!(other.take_kept(Some(&th), &three).await.is_none());
        assert!(!kept.alive());
        assert_eq!(keeper.count(), 0);
        drop((first, next, kept, other));
        std::fs::remove_dir_all(dir).ok();
    }

    /// A ChatGPT plan's sign-in file stays in its home while the keeper holds the app-server on it, after the turn that
    /// placed it ended, and goes once the app-server has idled out.
    #[tokio::test]
    async fn a_kept_app_servers_plan_home_keeps_its_sign_in_until_the_keeper_lets_it_go() {
        let (dir, mut env) = kept_rig("codex-kept-plan");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let id = format!("kept-{}", uuid::Uuid::new_v4().simple());
        let item = json!([{"id": id, "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                           "value": "{\"tokens\":1}", "revision": 1, "billing": "subscription"}]);
        let mut delivered = crate::model_subscriptions::apply(
            &state,
            &item,
            &mut env,
            &mut vec![],
            &mut Default::default(),
        );
        crate::model_subscriptions::start(&mut delivered, "codex", &mut env).unwrap();
        let file = delivered[0].file.clone().unwrap();
        assert!(file.exists());

        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None)
            .with_keeper(keeper.clone())
            .unwrap();
        let settings = turn_settings("turn-1", "gpt-5.5");
        assert!(host.take_kept(None, &settings).await.is_none());
        host.start().await.unwrap();
        let th = host.start_thread("ops", &settings).await.unwrap();
        let turn = host.start_turn(&th, "Hi", None).await.unwrap();
        ended(&host, &turn).await;
        assert!(host.release(Some(&th), None).await);
        crate::model_subscriptions::finish(&mut delivered);
        assert!(file.exists(), "the kept app-server still signs in from it");

        keeper
            .prune_at(
                std::time::Instant::now() + crate::keeper::IDLE + Duration::from_secs(1),
                false,
            )
            .await;
        assert_eq!(keeper.count(), 0);
        assert!(!host.alive());
        assert!(!file.exists(), "no copy left behind");
        drop(host);
        std::fs::remove_dir_all(dir).ok();
    }
}
