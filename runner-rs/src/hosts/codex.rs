//! The Codex host: one `codex app-server` process over stdio, newline-delimited
//! JSON-RPC 2.0. The bot's environment is both the process's own and, through `shell_environment_policy`,
//! what each thread's shell commands see; the operator's own MCP servers are stubbed so they start nothing.
//!
//! A thread's app-server may be kept between its turns (crate::keeper, T3 Code's Codex adapter, one process per
//! thread here, since a bot's environment, folder and plan are its own): its next turn is the next `turn/start` on the
//! live thread, with no `thread/resume`. It reads HUB_TOKEN from a token file of its own (crate::warm), blanked while it
//! is kept. The turn's settings decide whether it will do (`take_kept`): a process started any other way (`fingerprint`)
//! or signed in otherwise (`signed_in`) starts the turn cold, as does one that has exited (`Kept::alive`); one that
//! dies after its turn took it is that turn's crash. What it says while kept goes nowhere; its tool calls and subagents
//! still running hold it (crate::keeper::MAX_PIN), each until it ends itself. Its stderr is the process's, not a
//! turn's: a line an earlier turn caused may land in a later turn's diagnostics, the same thread and bot's, and is
//! scrubbed of every secret lent to the process while it lives (run_diagnostics::Stderr::lend).

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
    /// crate::keeper::IDLE (`Session::background`). Each goes only on its own end.
    running: Arc<Mutex<HashSet<String>>>,
    /// The last `turn/interrupt` was answered, so the turn it stopped is ending in the app server.
    interrupted: AtomicBool,
    /// True once the process has exited (and been waited for).
    ended: tokio::sync::watch::Sender<bool>,
    /// A plan's home made for one turn that this process was started on, held from when the keeper first takes it until
    /// it has stopped (`release`, `finish_turn`).
    home: Mutex<Option<crate::model_subscriptions::KeptHome>>,
    /// The turn's own token is in this process's config (a bot's MCP server header names HUB_TOKEN): never kept.
    holds_token: AtomicBool,
    /// A `turn/start` went out to the process, so the turn may have begun there even with no answer.
    turn_sent: AtomicBool,
    /// The process's stderr tail, which a kept process starts afresh for each turn it goes on with (`take_kept`).
    stderr: Mutex<Option<crate::run_diagnostics::Stderr>>,
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<i64, oneshot::Sender<Reply>>>,
    next_id: AtomicI64,
    alive: AtomicBool,
    child: Mutex<Option<(super::Stopper, Arc<AtomicBool>)>>,
    /// The process has exited, set as soon as it is reaped, before its output has drained (`exited`).
    exited: Mutex<Arc<AtomicBool>>,
    /// A `/compact` went to the process (`start_command`): answered when Codex took it, not when it ended, so the
    /// process is not kept (`release`).
    asked_compact: AtomicBool,
    /// Confirms a successful wait even if the caller that started the stop was cancelled.
    reaped: Mutex<Option<tokio::sync::watch::Receiver<bool>>>,
    /// The current app-server, swept before replacing it or ending the attempt.
    sweep: Mutex<Option<turn_sweep::Turn>>,
    sweep_redactor: Mutex<Option<Arc<Redactor>>>,
    sweep_reports: Mutex<Vec<Value>>,
    active_turn: Mutex<HashMap<String, String>>,
    done_turns: Mutex<HashSet<String>>,
    token_seen: Mutex<HashMap<String, [i64; 3]>>,
    /// A subagent's thread to the thread that started it and the turn it started in, set once when first seen: its tool
    /// calls are reported on that turn, whichever turn of the parent's runs when they come.
    children: Mutex<HashMap<String, (String, Option<String>)>>,
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
            home: Mutex::new(None),
            holds_token: AtomicBool::new(false),
            turn_sent: AtomicBool::new(false),
            stderr: Mutex::new(None),
            stdin: tokio::sync::Mutex::new(None),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicI64::new(0),
            alive: AtomicBool::new(false),
            child: Mutex::new(None),
            exited: Mutex::new(Arc::new(AtomicBool::new(false))),
            asked_compact: AtomicBool::new(false),
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
            if !crate::warm::write_token_for(&tokens.join("lease-token"), &token, None) {
                return Err(failed("Could not write the turn's token file for Codex"));
            }
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

    /// What a process for this turn would be started with, the turn's own token, scope, sweep mark and company skills
    /// folder aside: its command line (a plan's settings among it), its environment, and the thread as this turn would
    /// resume it (its model, folder, MCP servers, commands' environment and config), which a kept process is not told
    /// again. The effort goes with each turn, and the company skills with each turn's resume (`set_skill_roots`). A
    /// plan's home made for each turn (model_subscriptions `turn_only`) is named by its plan, the payer: a kept process
    /// goes on in the home it was started in, held until it goes (`release`).
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
        inputs
    }

    /// `key` with the sign-in a process started now would have: the plan login handed to this turn (`login`, its
    /// subscription and revision), a plan's tokens and its home's sign-in file. A kept process keeps the key it was
    /// started with, so a login handed anew since (a newer revision, the same bytes too, other plan tokens) starts the
    /// next turn cold.
    fn signed_in(&self, mut key: Inputs, login: Option<(&str, i64)>) -> Inputs {
        let login = login.map_or(String::new(), |(id, revision)| format!("{id}@{revision}"));
        key.add("login", &login);
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

    /// Whether the bot's MCP servers would put the turn's own token in the process's config (a header naming
    /// HUB_TOKEN, which Codex is given as it is, not as the token file).
    fn holds_token(&self, settings: &Settings) -> bool {
        let mut without = settings.env.clone();
        without.remove("HUB_TOKEN");
        let config = |env: &Env| crate::mcp::codex_config(&settings.mcp_servers, env, &self.0.env);
        config(&settings.env) != config(&without)
    }

    /// The turn's settings are known: this host notes what its process would start with (`fingerprint`, and the
    /// sign-in it would start on), and takes the process the keeper holds for `previous` when that one was started the
    /// same way and still runs, to go on in place of this host (Host::warm), with this attempt's events, capture,
    /// masking and admission. A kept process gets the turn's token only once the hub has started the turn
    /// (`lend_started`); its own process, not yet started, has it now. None: this host starts its own, the thread
    /// resumed in it as any cold turn's; a kept one that has exited is never taken (`Kept::alive`). One that dies after
    /// it was taken is that turn's crash, as its own process's would be.
    pub(crate) async fn take_kept(
        &self,
        previous: Option<&str>,
        settings: &Settings,
        login: Option<(&str, i64)>,
    ) -> Option<CodexHost> {
        let keeper = self.0.keeper.clone()?;
        if let (Some(dir), Some(token)) = (&self.0.tokens, settings.env.get("HUB_TOKEN")) {
            crate::warm::write_token_for(&dir.join("lease-token"), token, None);
        }
        let key = self.signed_in(self.fingerprint(settings), login);
        *self.0.key.lock().unwrap() = Some(key.clone());
        let holds_token = self.holds_token(settings);
        self.0.holds_token.store(holds_token, Ordering::SeqCst);
        let mut kept = keeper.take::<Kept>(previous?, &key, "").await?;
        let host = kept.0.take()?;
        // What it says from now on goes to this attempt. Its stderr is the process's, not a turn's: scrubbed of every
        // attempt's secrets it was lent (run_diagnostics::Stderr::lend), so an earlier turn's line in this one's is
        // still clean, and of this thread and bot.
        *host.0.tx.lock().unwrap() = self.0.tx.lock().unwrap().clone();
        let redactor = self.0.sweep_redactor.lock().unwrap().clone();
        if let Some(stderr) = host.0.stderr.lock().unwrap().as_ref() {
            stderr.lend(redactor.clone());
        }
        *host.0.capture.lock().unwrap() = self.capture();
        *host.0.sweep_redactor.lock().unwrap() = redactor;
        host.0.holds_token.store(holds_token, Ordering::SeqCst);
        let turn = self.0.allocation.0.lock().unwrap().turn.clone();
        if let Some(turn) = turn {
            host.0.allocation.track(turn);
        }
        host.0.interrupted.store(false, Ordering::SeqCst);
        host.0.turn_sent.store(false, Ordering::SeqCst);
        host.0.warm.store(true, Ordering::SeqCst);
        log("codex: the thread's next turn goes on in its kept app-server");
        Some(host)
    }

    /// The hub has started the turn (`/started`): a kept process this attempt went on in now gets its token. Err when
    /// it could not be written: the turn fails before its prompt goes there, and the process is stopped, not kept.
    pub(crate) fn lend_started(&self, settings: &Settings) -> Result<(), HostError> {
        if let (true, Some(dir), Some(token)) =
            (self.warm(), &self.0.tokens, settings.env.get("HUB_TOKEN"))
        {
            if !crate::warm::write_token_for(&dir.join("lease-token"), token, None) {
                return Err(failed(
                    "Could not give the kept Codex app-server the turn's token",
                ));
            }
        }
        Ok(())
    }

    /// The plan's home made for one turn that this process signs in from: a kept one's is an earlier turn's
    /// (model_subscriptions::go_on_in).
    pub(crate) fn own_home(&self) -> Option<PathBuf> {
        self.codex_home()
            .filter(|home| crate::model_subscriptions::turn_only(home))
    }

    /// Kill the process now and wait until it has gone and its output has drained (`alive`).
    #[cfg(test)]
    pub(crate) async fn die(&self) {
        let stopper = self
            .0
            .child
            .lock()
            .unwrap()
            .as_ref()
            .map(|(s, _)| s.clone());
        if let Some(stopper) = stopper {
            stopper.terminate(Duration::ZERO);
        }
        let by = tokio::time::Instant::now() + Duration::from_secs(10);
        while self.alive() {
            assert!(
                tokio::time::Instant::now() < by,
                "the app-server never went"
            );
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    /// A `turn/start` went out to the process: the turn may have begun there, answered or not.
    pub(crate) fn turn_sent(&self) -> bool {
        self.0.turn_sent.load(Ordering::SeqCst)
    }

    /// The attempt is over, its turn on `thread` ended: the process goes to the keeper for the thread's next turn
    /// (crate::keeper), holding `held` (what its folder needs meanwhile) until it goes. True once it has; false leaves
    /// it to be stopped as before: no keeper or thread, a turn still running in it (a stopped one whose
    /// `turn/interrupt` was answered gets crate::keeper::INTERRUPT_WAIT to end), a process that has gone, one whose
    /// config holds the turn's token, or one on a plan's home its turns share (another thread's turn or a newer
    /// revision can rewrite its sign-in), one given a `/compact` (its compaction may still be running), or one whose
    /// attempt lost its lease (`lost`) meanwhile. A plan's home made for the turn it started in is the process's own,
    /// held (model_subscriptions::keep_home) until it has stopped (`finish_turn`).
    pub(crate) async fn release(
        &self,
        thread: Option<&str>,
        held: Option<Held>,
        lost: &AtomicBool,
    ) -> bool {
        let (Some(keeper), Some(thread)) = (self.0.keeper.clone(), thread) else {
            return false;
        };
        let Some(key) = self.0.key.lock().unwrap().clone() else {
            return false;
        };
        if self.0.holds_token.load(Ordering::SeqCst) || self.0.asked_compact.load(Ordering::SeqCst)
        {
            return false;
        }
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
        if self.0.home.lock().unwrap().is_none() {
            if let Some(home) = self.codex_home() {
                let own = crate::model_subscriptions::turn_only(&home);
                match crate::model_subscriptions::keep_home(&home) {
                    Some(hold) if own => *self.0.home.lock().unwrap() = Some(hold),
                    // A plan's home its turns share (the hold just taken goes again), or one made for the turn that is
                    // no longer in place.
                    Some(_) => return false,
                    None if own => return false,
                    None => {}
                }
            }
        }
        keeper
            .put(thread, key, Kept(Some(self.clone())), held)
            .await;
        // A lease lost at any time before it was kept is seen here, once it is in the keeper: taken out and stopped.
        if lost.load(Ordering::SeqCst) {
            keeper.release(thread).await;
            return false;
        }
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
        // A new process: no thread is loaded in it, nor company skills, whatever this host's earlier one had
        // (`resume_thread`, `set_skill_roots`).
        self.0.warm.store(false, Ordering::SeqCst);
        self.0.skill_roots.lock().unwrap().clear();
        // Whatever the process prints goes to the attempt it is running for, and nowhere while it is kept.
        let sink = self.clone();
        let _stderr = crate::run_diagnostics::Stderr::capture_with(
            &mut child,
            move |value| sink.emit(value),
            &self.0.env,
        );
        _stderr.lend(self.0.sweep_redactor.lock().unwrap().clone());
        *self.0.stderr.lock().unwrap() = Some(_stderr.clone());
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
        // `exited` resolves once; whether it has is kept here for `exited`, and once its output has drained for `alive`.
        let gone = Arc::new(AtomicBool::new(false));
        let reaped_now = Arc::new(AtomicBool::new(false));
        *self.0.exited.lock().unwrap() = reaped_now.clone();
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
        });
        let host = self.clone();
        tokio::spawn(async move {
            let status = exited
                .await
                .unwrap_or_else(|_| Err(std::io::Error::other("codex app-server reaper ended")));
            reaped_now.store(true, Ordering::SeqCst);
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
        if let Some(stderr) = self.0.stderr.lock().unwrap().as_ref() {
            stderr.lend(redactor.clone());
        }
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
            #[cfg(test)]
            tests::SWEPT_SIGN_IN.with(|read| read.set(Some(secrets.is_some())));
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
            // Its plan home goes only once it has been seen to stop, as below.
            if self.stop_and_reap().await.is_ok() {
                drop(self.0.home.lock().unwrap().take());
            }
            return reports;
        }
        let reaped = self.stop_and_reap().await;
        let target = self.0.sweep.lock().unwrap().take();
        if let Some(target) = target {
            reports.extend(match &reaped {
                Ok(()) => self.sweep_reaped(target).await,
                Err(error) => {
                    Some(json!({"text": format!("Codex process cleanup incomplete. {error}")}))
                }
            });
        }
        // The plan's home it was started on goes once it has been seen to stop and swept (the sweep masks what its
        // sign-in file holds); one not seen to stop keeps it as long as this host lives.
        if reaped.is_ok() {
            drop(self.0.home.lock().unwrap().take());
        }
        reports
    }

    pub fn track_allocation(&self, turn: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        self.0.allocation.track(turn);
    }

    /// The process has exited, though what it wrote last may still be read (`alive` stays true until it has been):
    /// the keeper takes no process that has.
    fn exited(&self) -> bool {
        self.0.exited.lock().unwrap().load(Ordering::SeqCst)
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
        if msg["method"] == "turn/start" {
            // From here the process may read it, whatever comes back.
            self.0.turn_sent.store(true, Ordering::SeqCst);
        }
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
                    let root = children.get(parent).cloned().unwrap_or_else(|| {
                        let turn = self.0.active_turn.lock().unwrap().get(parent).cloned();
                        (parent.to_string(), turn)
                    });
                    children.entry(child).or_insert(root);
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
        let (thread, sub, turn) = match parent_thread {
            Some((parent, turn)) => (parent, Some(tid.clone()), turn),
            None => (tid.clone(), None, p["turnId"].as_str().map(String::from)),
        };
        let (t, u) = (
            Some(thread.as_str()).filter(|t| !t.is_empty()),
            turn.as_deref(),
        );
        // A subagent is the turn's that spawned it, set once: a later turn waiting on it or messaging it (its
        // `receiverThreadIds`) does not make it that turn's.
        let mut adopt = |child: &str| {
            if !child.is_empty() && child != thread {
                self.0
                    .children
                    .lock()
                    .unwrap()
                    .entry(child.to_string())
                    .or_insert_with(|| (thread.clone(), turn.clone()));
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
                self.note_running(&format!("agent:{child}"), status == "started");
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
        self.note_running(&id, !done);
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
        // An earlier turn's call failing now is not this attempt's to keep.
        let earlier = u.is_some_and(|u| self.0.done_turns.lock().unwrap().contains(u));
        if done && !earlier && (status == "failed" || exit_code.is_some_and(|c| c != 0)) {
            let (command, output) = failed_detail(item);
            self.emit(crate::run_diagnostics::failed_tool(
                &id, &name, &command, &output, exit_code,
            ));
        }
    }

    /// A tool call or subagent `item` started (`on`), or ended: what `Kept::background` reads.
    fn note_running(&self, item: &str, on: bool) {
        if item.is_empty() {
            return;
        }
        let mut running = self.0.running.lock().unwrap();
        if on {
            running.insert(item.to_string());
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
        // Its commands sign in from the plan home the process does: a kept one's is an earlier turn's, this turn's
        // own gone (model_subscriptions::go_on_in).
        if let (Some(home), true) = (self.own_home(), shell.contains_key("CODEX_HOME")) {
            shell.insert("CODEX_HOME".into(), home.display().to_string());
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
        // straight in, told only this turn's company skills. One gone since is never started again: the turn ends as a
        // crash would.
        self.set_skill_roots(settings).await;
        if self.warm() && self.0.confirmed.lock().unwrap().contains(thread_id) {
            return Ok(thread_id.to_string());
        }
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
        // No text goes on with the thread's own history and no new input (drive.rs `overload_wait`): a prompt
        // already in the thread is never sent again.
        let input = if text.is_empty() {
            json!([])
        } else {
            json!([{"type": "text", "text": text}])
        };
        let mut params = json!({"threadId": thread_id, "input": input});
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
                self.0.asked_compact.store(true, Ordering::SeqCst);
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
        self.0
            .as_ref()
            .is_some_and(|host| host.alive() && !host.exited())
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
            host.0.allocation.0.lock().unwrap().turn = None;
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
pub(crate) mod tests {
    thread_local! {
        /// Whether the last sweep (`sweep_reaped`) read the process's sign-in file to mask it.
        pub(super) static SWEPT_SIGN_IN: std::cell::Cell<Option<bool>> = const { std::cell::Cell::new(None) };
    }

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
    /// turn ends at once. A turn's `ERR:` text goes to its stderr first; `CLOSE` closes its output and `DIE` ends it,
    /// with no answer. `BUSY<n>` and the n-1 turns after it end on a busy model, as Codex does when it will not retry,
    /// and `QUOTA` on a quota Codex labels an overload too; a turn with no input says "Went on". Each call goes to
    /// `FAKE_CALLS`.
    const FAKE_KEPT: &str = r#"#!/usr/bin/env python3
import json, os, sys
def say(m): print(json.dumps(m), flush=True)
n = busy = 0
for line in sys.stdin:
    msg = json.loads(line)
    method, p = msg.get("method"), msg.get("params") or {}
    if method is None or "id" not in msg:
        continue
    open(os.environ["FAKE_CALLS"], "a").write(json.dumps({"method": method, "pid": os.getpid(), "params": p}) + "\n")
    i = msg["id"]
    text = (p.get("input") or [{}])[0].get("text", "")
    if method == "turn/start" and text.startswith("ERR:"):
        sys.stderr.write(text[4:])
        sys.stderr.flush()
    if method == "turn/start" and text == "CLOSE":
        os.close(1)
        continue
    if method == "turn/start" and text == "DIE":
        os._exit(1)
    if method == "thread/start":
        say({"id": i, "result": {"thread": {"id": "th-1"}}})
    elif method == "turn/start":
        n += 1
        t, th = "turn-%d" % n, p["threadId"]
        say({"id": i, "result": {"turn": {"id": t}}})
        say({"method": "turn/started", "params": {"threadId": th, "turn": {"id": t}}})
        busy = int(text[4:]) if text.startswith("BUSY") else busy
        if busy or text == "QUOTA":
            busy = max(busy - 1, 0)
            err = {"message": "Quota exceeded. Check your plan and billing details." if text == "QUOTA" else "Server busy", "codexErrorInfo": "serverOverloaded"}
            say({"method": "error", "params": {"threadId": th, "turnId": t, "willRetry": False, "error": err}})
            say({"method": "turn/completed", "params": {"threadId": th, "turn": {"id": t, "status": "failed", "error": err}}})
        elif text != "LONG":
            if not p["input"]:
                say({"method": "item/completed", "params": {"threadId": th, "turnId": t, "item": {"type": "agentMessage", "id": "m-" + t, "text": "Went on", "phase": "final_answer"}}})
            say({"method": "turn/completed", "params": {"threadId": th, "turn": {"id": t, "status": "completed"}}})
    elif method == "turn/interrupt":
        say({"id": i, "result": {}})
        say({"method": "turn/completed", "params": {"threadId": p["threadId"], "turn": {"id": p["turnId"], "status": "interrupted"}}})
    else:
        say({"id": i, "result": {}})
"#;

    /// A folder with `FAKE_KEPT` as `codex`, and the environment that runs it.
    pub(crate) fn kept_rig(name: &str) -> (PathBuf, Env) {
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
        assert!(first.take_kept(None, &one, None).await.is_none());
        first.start().await.unwrap();
        let th = first.start_thread("ops", &one).await.unwrap();
        let turn = first.start_turn(&th, "LONG", None).await.unwrap();
        first.interrupt(&th, &turn).await.unwrap();
        assert!(
            first
                .release(Some(&th), None, &AtomicBool::new(false))
                .await,
            "kept after Codex ended the turn"
        );
        let file = first.0.tokens.clone().unwrap().join("lease-token");
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "");
        assert_eq!(keeper.count(), 1);

        let next = CodexHost::on_home(env.clone(), events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let two = turn_settings("turn-2", "gpt-5.5");
        let kept = next.take_kept(Some(&th), &two, None).await.expect("warm");
        assert!(Arc::ptr_eq(&kept.0, &first.0) && kept.warm() && kept.alive());
        // What the previous turn left running cannot read the next turn's token before the hub has started it.
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "");
        kept.lend_started(&two).unwrap();
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "turn-2");
        assert_eq!(kept.resume_thread(&th, &two).await.unwrap(), th);
        let turn = kept.start_turn(&th, "Go on", None).await.unwrap();
        ended(&kept, &turn).await;
        assert!(kept.release(Some(&th), None, &AtomicBool::new(false)).await);
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
        assert!(other.take_kept(Some(&th), &three, None).await.is_none());
        assert!(!kept.alive());
        assert_eq!(keeper.count(), 0);
        drop((first, next, kept, other));
        std::fs::remove_dir_all(dir).ok();
    }

    /// Each call the fake app server got.
    pub(crate) fn calls(dir: &std::path::Path) -> Vec<Value> {
        std::fs::read_to_string(dir.join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    /// A first turn of `th` on `host` (cold), ended and given to the keeper.
    async fn first_turn(host: &CodexHost, settings: &Settings, text: &str) -> String {
        first_turn_on(host, settings, text, None).await
    }

    /// `first_turn` signed in with the plan login `login` (its subscription and revision).
    async fn first_turn_on(
        host: &CodexHost,
        settings: &Settings,
        text: &str,
        login: Option<(&str, i64)>,
    ) -> String {
        assert!(host.take_kept(None, settings, login).await.is_none());
        host.start().await.unwrap();
        let th = host.start_thread("ops", settings).await.unwrap();
        let turn = host.start_turn(&th, text, None).await.unwrap();
        ended(host, &turn).await;
        th
    }

    /// A plan login handed to `env` for one turn, as model_subscriptions hands it: in a home of the turn's own when
    /// `own`, else in the plan's home its turns share.
    fn plan_turn(
        state: &std::path::Path,
        id: &str,
        own: bool,
        env: &mut Env,
    ) -> Vec<crate::model_subscriptions::Delivered> {
        plan_turn_at(state, id, own, 1, env)
    }

    /// `plan_turn` at `revision`, the same sign-in file.
    fn plan_turn_at(
        state: &std::path::Path,
        id: &str,
        own: bool,
        revision: i64,
        env: &mut Env,
    ) -> Vec<crate::model_subscriptions::Delivered> {
        let mut item = json!({"id": id, "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                              "value": "{\"tokens\":1}", "revision": revision, "billing": "subscription"});
        if own {
            item["source"] = json!("attempt");
        }
        let mut delivered = crate::model_subscriptions::apply(
            state,
            &json!([item]),
            env,
            &mut vec![],
            &mut Default::default(),
        );
        crate::model_subscriptions::start(&mut delivered, "codex", env).unwrap();
        delivered
    }

    /// A plan's sign-in in a home made for one turn stays while the keeper holds the app-server started on it: through
    /// the thread's next turn, which goes on in that app-server with a home of its own, until the app-server goes.
    #[tokio::test]
    async fn a_kept_app_server_keeps_its_own_turns_plan_home_through_the_next_turn_until_it_goes() {
        let (dir, base) = kept_rig("codex-kept-plan");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let id = format!("kept-{}", uuid::Uuid::new_v4().simple());
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let events = || tokio::sync::mpsc::unbounded_channel().0;

        let mut env = base.clone();
        let mut delivered = plan_turn(&state, &id, true, &mut env);
        let first_file = delivered[0].file.clone().unwrap();
        assert!(crate::model_subscriptions::turn_only(
            first_file.parent().unwrap()
        ));
        let first = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let th = first_turn(&first, &turn_settings("turn-1", "gpt-5.5"), "Hi").await;
        assert!(
            first
                .release(Some(&th), None, &AtomicBool::new(false))
                .await
        );
        crate::model_subscriptions::finish(&mut delivered);
        assert!(
            first_file.exists(),
            "the kept app-server still signs in from it"
        );

        let mut env = base.clone();
        let mut delivered = plan_turn(&state, &id, true, &mut env);
        let next_file = delivered[0].file.clone().unwrap();
        assert_ne!(next_file, first_file);
        let next = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let kept = next
            .take_kept(Some(&th), &turn_settings("turn-2", "gpt-5.5"), None)
            .await
            .expect("warm");
        assert!(
            first_file.exists(),
            "its app-server goes on signed in there"
        );
        let turn = kept.start_turn(&th, "Go on", None).await.unwrap();
        ended(&kept, &turn).await;
        assert!(kept.release(Some(&th), None, &AtomicBool::new(false)).await);
        crate::model_subscriptions::finish(&mut delivered);
        assert!(!next_file.exists() && first_file.exists());

        keeper
            .prune_at(
                std::time::Instant::now() + crate::keeper::IDLE + Duration::from_secs(1),
                false,
            )
            .await;
        assert_eq!(keeper.count(), 0);
        assert!(!kept.alive());
        assert!(
            !first_file.parent().unwrap().exists(),
            "no copy left behind"
        );
        drop((first, next, kept));
        std::fs::remove_dir_all(dir).ok();
    }

    /// A plan's home its turns share (a legacy `codex_login` file), which another thread's turn or a newer revision can
    /// rewrite, keeps no app-server: it stops at the end of its turn, as before.
    #[tokio::test]
    async fn an_app_server_on_a_plan_home_its_turns_share_is_not_kept() {
        let (dir, mut env) = kept_rig("codex-shared-plan");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let id = format!("shared-{}", uuid::Uuid::new_v4().simple());
        let mut delivered = plan_turn(&state, &id, false, &mut env);
        let file = delivered[0].file.clone().unwrap();
        assert!(!crate::model_subscriptions::turn_only(
            file.parent().unwrap()
        ));
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None)
            .with_keeper(keeper.clone())
            .unwrap();
        let th = first_turn(&host, &turn_settings("turn-1", "gpt-5.5"), "Hi").await;
        assert!(!host.release(Some(&th), None, &AtomicBool::new(false)).await);
        assert_eq!(keeper.count(), 0);
        host.finish_turn().await;
        crate::model_subscriptions::finish(&mut delivered);
        assert!(!file.exists());
        std::fs::remove_dir_all(dir).ok();
    }

    /// A kept app-server that could not take the turn's token (its token file unwritable) fails the turn before its
    /// prompt goes there.
    #[tokio::test]
    async fn a_kept_app_server_that_cannot_be_given_the_turns_token_fails_the_turn() {
        let (dir, env) = kept_rig("codex-kept-lend");
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let events = || tokio::sync::mpsc::unbounded_channel().0;
        let first = CodexHost::on_home(env.clone(), events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let th = first_turn(&first, &turn_settings("turn-1", "gpt-5.5"), "Hi").await;
        assert!(
            first
                .release(Some(&th), None, &AtomicBool::new(false))
                .await
        );
        let next = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let two = turn_settings("turn-2", "gpt-5.5");
        let kept = next.take_kept(Some(&th), &two, None).await.expect("warm");
        std::fs::remove_dir_all(kept.0.tokens.clone().unwrap()).unwrap();
        assert!(kept.lend_started(&two).is_err());
        kept.finish_turn().await;
        drop((first, next, kept));
        std::fs::remove_dir_all(dir).ok();
    }

    /// Not kept: an app-server given a `/compact` (Codex answers it when it takes it, before the compaction ends), nor
    /// one whose attempt lost its lease.
    #[tokio::test]
    async fn an_app_server_given_a_compact_or_whose_lease_was_lost_is_not_kept() {
        let (dir, env) = kept_rig("codex-kept-compact");
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        for lost in [false, true] {
            let host =
                CodexHost::on_home(env.clone(), tokio::sync::mpsc::unbounded_channel().0, None)
                    .with_keeper(keeper.clone())
                    .unwrap();
            let th = first_turn(&host, &turn_settings("turn-1", "gpt-5.5"), "Hi").await;
            if !lost {
                host.start_command(&th, "/compact", None).await.unwrap();
            }
            assert!(!host.release(Some(&th), None, &AtomicBool::new(lost)).await);
            assert_eq!(keeper.count(), 0);
            // One whose lease was lost is taken back out of the keeper and stopped.
            assert!(!lost || !host.alive());
            host.finish_turn().await;
        }
        std::fs::remove_dir_all(dir).ok();
    }

    /// A plan's home a kept app-server signs in from stays through its sweep, which masks what its sign-in file holds,
    /// and with a process not seen to stop.
    #[tokio::test]
    async fn a_kept_app_servers_plan_home_stays_through_its_sweep_and_an_unconfirmed_stop() {
        let (dir, base) = kept_rig("codex-kept-sweep");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let id = format!("sweep-{}", uuid::Uuid::new_v4().simple());
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let mut env = base.clone();
        let mut delivered = plan_turn(&state, &id, true, &mut env);
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None)
            .with_keeper(keeper.clone())
            .unwrap();
        host.set_attempt(None);
        let th = first_turn(&host, &turn_settings("turn-1", "gpt-5.5"), "Hi").await;
        assert!(host.release(Some(&th), None, &AtomicBool::new(false)).await);
        crate::model_subscriptions::finish(&mut delivered);
        SWEPT_SIGN_IN.with(|read| read.set(None));
        keeper
            .prune_at(
                std::time::Instant::now() + crate::keeper::IDLE + Duration::from_secs(1),
                false,
            )
            .await;
        assert_eq!(keeper.count(), 0);
        assert_eq!(
            SWEPT_SIGN_IN.with(|read| read.get()),
            Some(true),
            "swept with its sign-in file there"
        );
        drop(host);

        // Not seen to stop, with a sweep or without one (a cleanup retry): its home stays while the host lives.
        for sweep in [true, false] {
            let mut env = base.clone();
            let mut delivered = plan_turn(&state, &id, true, &mut env);
            let home = std::path::PathBuf::from(env.get("CODEX_HOME").unwrap());
            let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
            *host.0.home.lock().unwrap() = crate::model_subscriptions::keep_home(&home);
            crate::model_subscriptions::finish(&mut delivered);
            if sweep {
                host.prepare_sweep();
            }
            let (failed, reaped) = tokio::sync::watch::channel(false);
            *host.0.reaped.lock().unwrap() = Some(reaped);
            drop(failed);
            host.finish_turn().await;
            assert!(
                home.join("auth.json").exists(),
                "kept while the process may run ({sweep})"
            );
            drop(host);
            assert!(!home.join("auth.json").exists());
        }
        std::fs::remove_dir_all(dir).ok();
    }

    /// The commands of a thread started in a kept app-server sign in from the plan home that process does, not the one
    /// made for the turn that went on in it.
    #[test]
    fn a_kept_app_servers_new_thread_signs_its_commands_in_from_its_own_home() {
        let (dir, base) = kept_rig("codex-kept-home");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let id = format!("home-{}", uuid::Uuid::new_v4().simple());
        let (mut first, mut second) = (base.clone(), base.clone());
        let mut one = plan_turn(&state, &id, true, &mut first);
        let mut two = plan_turn(&state, &id, true, &mut second);
        let (kept, turn) = (
            first.get("CODEX_HOME").unwrap().clone(),
            second.get("CODEX_HOME").unwrap().clone(),
        );
        assert_ne!(kept, turn);
        let host = CodexHost::on_home(first, tokio::sync::mpsc::unbounded_channel().0, None);
        let params = host
            .thread_params(&Settings {
                env: second,
                ..Settings::default()
            })
            .to_string();
        assert!(
            params.contains(&kept) && !params.contains(&turn),
            "{params}"
        );
        crate::model_subscriptions::finish(&mut one);
        crate::model_subscriptions::finish(&mut two);
        std::fs::remove_dir_all(dir).ok();
    }

    /// A plan login handed again at a newer revision, its sign-in file the same, is another login: the thread's next
    /// turn starts its own app-server.
    #[tokio::test]
    async fn a_plan_login_at_a_new_revision_with_the_same_file_starts_the_next_turn_cold() {
        let (dir, base) = kept_rig("codex-kept-revision");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let id = format!("rev-{}", uuid::Uuid::new_v4().simple());
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let events = || tokio::sync::mpsc::unbounded_channel().0;
        let mut env = base.clone();
        let mut delivered = plan_turn_at(&state, &id, true, 1, &mut env);
        let first = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let one = turn_settings("turn-1", "gpt-5.5");
        let th = first_turn_on(&first, &one, "Hi", Some((&id, 1))).await;
        assert!(
            first
                .release(Some(&th), None, &AtomicBool::new(false))
                .await
        );
        crate::model_subscriptions::finish(&mut delivered);

        let mut env = base.clone();
        let mut delivered = plan_turn_at(&state, &id, true, 2, &mut env);
        let next = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let two = turn_settings("turn-2", "gpt-5.5");
        assert!(next
            .take_kept(Some(&th), &two, Some((&id, 2)))
            .await
            .is_none());
        assert_eq!(keeper.count(), 0);
        crate::model_subscriptions::finish(&mut delivered);
        drop((first, next));
        std::fs::remove_dir_all(dir).ok();
    }

    /// A `turn/start` the app-server read and died on, unanswered, may have begun the turn: it counts as sent, so no
    /// fallback runs the message again (execute.rs `tools_settled`).
    #[tokio::test]
    async fn a_turn_start_the_app_server_died_on_counts_as_sent() {
        let (dir, env) = kept_rig("codex-sent");
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
        host.start().await.unwrap();
        let th = host
            .start_thread("ops", &turn_settings("t", "gpt-5.5"))
            .await
            .unwrap();
        assert!(!host.turn_sent());
        assert!(host.start_turn(&th, "DIE", None).await.is_err());
        assert!(host.turn_sent());
        host.finish_turn().await;
        std::fs::remove_dir_all(dir).ok();
    }

    /// A subagent an earlier turn started is that turn's: a tool it starts while a later turn runs is reported on its
    /// own turn, and goes on holding the kept process (`Kept::background`) when that later turn is interrupted.
    #[tokio::test]
    async fn an_earlier_turns_subagent_tool_still_holds_the_process_after_a_later_turn_is_interrupted(
    ) {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        let turn = |method: &str, id: &str, status: &str| json!({"method": method, "params": {"threadId": "th", "turn": {"id": id, "status": status}}});
        host.on_message(turn("turn/started", "t1", "inProgress"))
            .await;
        host.on_message(json!({"method": "thread/started",
            "params": {"thread": {"id": "sub", "parentThreadId": "th"}}}))
            .await;
        host.on_message(turn("turn/completed", "t1", "completed"))
            .await;
        host.on_message(turn("turn/started", "t2", "inProgress"))
            .await;
        host.on_message(json!({"method": "item/started", "params": {"threadId": "sub", "turnId": "s1",
            "item": {"type": "commandExecution", "id": "c1", "command": "sleep 600", "status": "inProgress"}}}))
            .await;
        host.on_message(turn("turn/completed", "t2", "interrupted"))
            .await;
        let kept = Kept(Some(host.clone()));
        assert!((kept.background().unwrap())(), "its tool still runs");
        let mut tools = vec![];
        while let Ok(event) = rx.try_recv() {
            if event["kind"] == "tool" {
                tools.push(event);
            }
        }
        assert_eq!(tools.len(), 1, "{tools:?}");
        assert_eq!(tools[0]["turn_id"], "t1");
    }

    /// A later turn that waits on an earlier turn's subagent (`receiverThreadIds`) before its tool ends does not take
    /// it over: the tool, and its failure, stay the earlier turn's.
    #[tokio::test]
    async fn a_later_turn_waiting_on_an_earlier_turns_subagent_leaves_its_tool_on_the_earlier_turn()
    {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        let turn = |method: &str, id: &str| json!({"method": method, "params": {"threadId": "th", "turn": {"id": id, "status": "completed"}}});
        let tool = |method: &str, status: &str| {
            json!({"method": method, "params": {"threadId": "sub", "turnId": "s1", "item": {"type": "commandExecution",
                "id": "c1", "command": "make test", "status": status, "exitCode": 2, "aggregatedOutput": "boom"}}})
        };
        host.on_message(turn("turn/started", "t1")).await;
        host.on_message(json!({"method": "thread/started",
            "params": {"thread": {"id": "sub", "parentThreadId": "th"}}}))
            .await;
        host.on_message(tool("item/started", "inProgress")).await;
        host.on_message(turn("turn/completed", "t1")).await;
        host.on_message(turn("turn/started", "t2")).await;
        host.on_message(json!({"method": "item/started", "params": {"threadId": "th", "turnId": "t2",
            "item": {"type": "collabAgentToolCall", "id": "w1", "tool": "wait", "status": "inProgress",
                     "receiverThreadIds": ["sub"]}}}))
            .await;
        host.on_message(tool("item/completed", "failed")).await;
        let mut on = vec![];
        while let Ok(event) = rx.try_recv() {
            if event["item_id"] == "c1" || event["failed_tool"]["item_id"] == "c1" {
                on.push((text_of(&event["kind"]), text_of(&event["turn_id"])));
            }
        }
        assert!(!on.is_empty());
        assert!(
            on.iter().all(|(kind, turn)| kind != "tool" || turn == "t1"),
            "{on:?}"
        );
        assert!(
            on.iter().all(|(kind, _)| kind == "tool"),
            "no failure kept for t2: {on:?}"
        );
    }

    /// A call an earlier turn started that fails while a later one runs is not kept as the later turn's failure.
    #[tokio::test]
    async fn an_earlier_turns_failed_call_is_not_the_next_turns_diagnostic() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = CodexHost::on_home(Env::new(), tx, None);
        let turn = |method: &str, id: &str| json!({"method": method, "params": {"threadId": "th", "turn": {"id": id, "status": "completed"}}});
        let call = |method: &str, id: &str, turn: &str| {
            json!({"method": method, "params": {"threadId": "th", "turnId": turn, "item": {"type": "commandExecution",
                "id": id, "command": "make test", "status": "failed", "exitCode": 2, "aggregatedOutput": "boom"}}})
        };
        host.on_message(turn("turn/started", "t1")).await;
        host.on_message(call("item/started", "c1", "t1")).await;
        host.on_message(turn("turn/completed", "t1")).await;
        host.on_message(turn("turn/started", "t2")).await;
        host.on_message(call("item/completed", "c1", "t1")).await;
        host.on_message(call("item/started", "c2", "t2")).await;
        host.on_message(call("item/completed", "c2", "t2")).await;
        let mut failed = vec![];
        while let Ok(event) = rx.try_recv() {
            if let Some(id) = event["failed_tool"]["item_id"].as_str() {
                failed.push(id.to_string());
            }
        }
        assert_eq!(failed, ["c2"]);
    }

    /// A process whose output closed is killed and waited for until it has been reaped, not only until its output ended.
    #[tokio::test]
    async fn a_kept_app_servers_kill_waits_until_it_has_been_reaped() {
        let (dir, env) = kept_rig("codex-kept-kill");
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None);
        host.start().await.unwrap();
        let th = host
            .start_thread("ops", &turn_settings("t", "gpt-5.5"))
            .await
            .unwrap();
        assert!(host.start_turn(&th, "CLOSE", None).await.is_err());
        let gone = host
            .0
            .child
            .lock()
            .unwrap()
            .as_ref()
            .map(|(_, g)| g.clone())
            .unwrap();
        let kept = Kept(Some(host.clone()));
        (kept.kill())().await;
        assert!(gone.load(Ordering::SeqCst), "killed and reaped");
        drop((kept, host));
        std::fs::remove_dir_all(dir).ok();
    }

    /// A bot's MCP server whose header names HUB_TOKEN holds the turn's own token in the app-server's config: that
    /// app-server stops with its turn.
    #[tokio::test]
    async fn an_app_server_whose_mcp_server_holds_the_turns_token_is_not_kept() {
        let (dir, mut env) = kept_rig("codex-kept-mcp");
        env.insert("HUB_TOKEN".into(), "turn-1".into());
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let host = CodexHost::on_home(env, tokio::sync::mpsc::unbounded_channel().0, None)
            .with_keeper(keeper.clone())
            .unwrap();
        let mut one = turn_settings("turn-1", "gpt-5.5");
        one.mcp_servers = vec![json!({"name": "ops", "url": "https://mcp.example/ops",
                                      "headers": {"Authorization": "Bearer ${HUB_TOKEN}"}})];
        let th = first_turn(&host, &one, "Hi").await;
        assert!(!host.release(Some(&th), None, &AtomicBool::new(false)).await);
        assert_eq!(keeper.count(), 0);
        host.finish_turn().await;
        std::fs::remove_dir_all(dir).ok();
    }

    /// Each turn stages its company skills in a folder of its own: the next turn still goes on in the kept app-server,
    /// told that turn's folder.
    #[tokio::test]
    async fn a_turns_own_company_skills_folder_keeps_the_app_server_and_is_given_to_it() {
        let (dir, env) = kept_rig("codex-kept-skills");
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let events = || tokio::sync::mpsc::unbounded_channel().0;
        let with_skills = |token: &str, folder: &str| Settings {
            company_skills: Some(dir.join(folder).display().to_string()),
            ..turn_settings(token, "gpt-5.5")
        };
        let first = CodexHost::on_home(env.clone(), events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let th = first_turn(&first, &with_skills("turn-1", "skills-1"), "Hi").await;
        assert!(
            first
                .release(Some(&th), None, &AtomicBool::new(false))
                .await
        );
        let next = CodexHost::on_home(env, events(), None)
            .with_keeper(keeper.clone())
            .unwrap();
        let two = with_skills("turn-2", "skills-2");
        let kept = next.take_kept(Some(&th), &two, None).await.expect("warm");
        assert_eq!(kept.resume_thread(&th, &two).await.unwrap(), th);
        let roots: Vec<Value> = calls(&dir)
            .iter()
            .filter(|c| c["method"] == "skills/extraRoots/set")
            .map(|c| c["params"]["extraRoots"].clone())
            .collect();
        assert_eq!(roots.last(), Some(&json!(skill_roots(&two))), "{roots:?}");
        kept.finish_turn().await;
        drop((first, next, kept));
        std::fs::remove_dir_all(dir).ok();
    }

    /// A kept app-server's stderr is the process's: what it says during a later turn is scrubbed of that turn's
    /// secrets and of every earlier turn's it was lent, whichever turn caused it.
    #[tokio::test]
    async fn a_kept_app_servers_stderr_is_scrubbed_of_every_turns_secrets() {
        let (dir, env) = kept_rig("codex-kept-stderr");
        let keeper = Arc::new(Keeper::new(dir.join("kept"), 8));
        let first = CodexHost::on_home(env.clone(), tokio::sync::mpsc::unbounded_channel().0, None)
            .with_keeper(keeper.clone())
            .unwrap();
        first.set_attempt(Some(Arc::new(Redactor::new(&[
            "synthetic-first-secret".to_string()
        ]))));
        let th = first_turn(&first, &turn_settings("turn-1", "gpt-5.5"), "ERR:noise-1\n").await;
        assert!(
            first
                .release(Some(&th), None, &AtomicBool::new(false))
                .await
        );
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let next = CodexHost::on_home(env, tx, None)
            .with_keeper(keeper.clone())
            .unwrap();
        next.set_attempt(Some(Arc::new(Redactor::new(&[
            "synthetic-second-secret".to_string()
        ]))));
        let kept = next
            .take_kept(Some(&th), &turn_settings("turn-2", "gpt-5.5"), None)
            .await
            .expect("warm");
        let turn = kept
            .start_turn(
                &th,
                "ERR:old synthetic-first-secret\nnew synthetic-second-secret\n",
                None,
            )
            .await
            .unwrap();
        ended(&kept, &turn).await;
        // Its stderr is read apart from its stdout: wait for the tail holding both lines.
        let mut tails = vec![];
        let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
        while !tails
            .iter()
            .any(|t: &String| t.contains("old") && t.contains("new"))
        {
            match tokio::time::timeout_at(deadline, rx.recv()).await {
                Ok(Some(event)) => tails.extend(event["stderr_tail"].as_str().map(String::from)),
                _ => panic!("no stderr tail with both lines: {tails:?}"),
            }
        }
        assert!(tails.iter().all(|t| !t.contains("synthetic-")), "{tails:?}");
        kept.finish_turn().await;
        drop((first, next, kept));
        std::fs::remove_dir_all(dir).ok();
    }
}
