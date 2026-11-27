//! The generic ACP host: any CLI that speaks ACP (Agent Client Protocol, hosts/acp.rs) over its stdin and stdout runs
//! here, described by its harness manifest (`protocol = "acp"` and an `[acp]` table, docs/harnesses.md) rather than by
//! a Rust host of its own. Grok Build is the first; its manifest says
//!
//!     grok agent -m grok-4.6 --reasoning-effort high --always-approve --no-leader stdio
//!
//! One process per bot, started with the first thread once the bot's model, effort and environment are known; a
//! thread with another command line or environment restarts it, as an ACP session cannot set its tools' environment.
//! `session/prompt` is a long-running request whose answer ends the turn, so it is sent without waiting and its
//! `stopReason` becomes `turn_completed` (or `turn_failed`). Turn ids are the runner's own.
//!
//! What stays per CLI lives beside its manifest: how a login is read and handed to the bot user (hosts/grok.rs,
//! reached through `prepare_login_home` and `file_signed_in` in hosts/mod.rs).

use std::collections::{HashMap, HashSet};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::process::ChildStdin;

use super::acp::{self, Message, Reply, RequestError};
use super::{event, failed, is_limit, Events, HostError, Settings};
use crate::env::Env;
use crate::util::{log, text_of, truthy};

/// How a saved conversation is opened again (`[acp] resume`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Reopen {
    /// `session/load` (the agent replays the conversation as updates, which no turn takes).
    Load,
    /// `session/resume`.
    Resume,
    /// By what the agent's `initialize` answer says it can do (acp::resume_method).
    Auto,
}

/// How a tool's permission (`session/request_permission`) is answered (`[acp] permission`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Permissions {
    /// Always given: for a CLI started with its own always-approve switch.
    Allow,
    /// Given while a turn runs on the session that asks, refused otherwise.
    Turn,
    /// Always refused.
    Refuse,
}

/// The `mcpServers` a session is opened with (`[acp] mcp_servers`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum McpServers {
    /// The bot's servers in ACP's own shape (crate::mcp::acp_config).
    Acp,
    /// None: the CLI takes no servers from its client.
    None,
}

/// A manifest's `[acp]` table: everything the generic host needs to run one CLI.
#[derive(Clone, Debug, PartialEq)]
pub struct AcpSpec {
    /// The command line after the executable; `{model}` and `{effort}` are the bot's.
    pub args: Vec<String>,
    /// What errors and logs call the running agent ("grok agent").
    pub label: String,
    /// `{model}` when the bot names none.
    pub default_model: String,
    /// The efforts the CLI takes, lower case; another is `default_effort`. Empty: any the bot names.
    pub efforts: Vec<String>,
    pub default_effort: String,
    /// Variables set on the agent process only (not on logins or version probes, which `[env]` covers). Sorted.
    pub env: Vec<(String, String)>,
    pub mcp_servers: McpServers,
    pub resume: Reopen,
    pub permission: Permissions,
    /// How long one request may wait for its answer, and one write for the agent to read it.
    pub request_timeout: Duration,
    /// How long a stop waits for the agent's input before ending the process anyway.
    pub stop_wait: Duration,
    /// How long `session/cancel` may wait for the agent's input; None: the keeper's interrupt wait.
    pub cancel_wait: Option<Duration>,
}

const KEYS: &[&str] = &[
    "args",
    "label",
    "default_model",
    "efforts",
    "default_effort",
    "env",
    "mcp_servers",
    "resume",
    "permission",
    "request_timeout_s",
    "stop_wait_s",
    "cancel_wait_s",
];

impl AcpSpec {
    /// The `[acp]` table of the manifest at `at`, for `executable`; Err names the field.
    pub fn parse(raw: &toml::Value, executable: &str, at: &str) -> Result<AcpSpec, String> {
        let Some(t) = raw.as_table() else {
            return Err(format!("{at}: [acp] must be a table"));
        };
        if let Some(k) = t.keys().find(|k| !KEYS.contains(&k.as_str())) {
            return Err(format!("{at}: unknown acp field {k}"));
        }
        let text = |k: &str| -> Result<String, String> {
            match t.get(k) {
                None => Ok(String::new()),
                Some(v) => v
                    .as_str()
                    .map(|s| s.trim().to_string())
                    .ok_or_else(|| format!("{at}: acp.{k} must be a string")),
            }
        };
        let list = |k: &str| -> Result<Vec<String>, String> {
            match t.get(k) {
                None => Ok(vec![]),
                Some(toml::Value::Array(a)) => a
                    .iter()
                    .map(|v| v.as_str().filter(|s| !s.is_empty()).map(String::from))
                    .collect::<Option<Vec<_>>>()
                    .ok_or_else(|| format!("{at}: acp.{k} must be a list of strings")),
                Some(_) => Err(format!("{at}: acp.{k} must be a list of strings")),
            }
        };
        let choice = |k: &str, default: &str, choices: &[&str]| -> Result<String, String> {
            let v = text(k)?;
            let v = if v.is_empty() { default.to_string() } else { v };
            if choices.contains(&v.as_str()) {
                Ok(v)
            } else {
                Err(format!("{at}: acp.{k} is one of {}", choices.join(", ")))
            }
        };
        let seconds = |k: &str| -> Result<Option<Duration>, String> {
            match t.get(k) {
                None => Ok(None),
                Some(v) => v
                    .as_integer()
                    .filter(|n| *n > 0)
                    .map(|n| Some(Duration::from_secs(n as u64)))
                    .ok_or_else(|| format!("{at}: acp.{k} must be a positive number of seconds")),
            }
        };
        let args = list("args")?;
        if args.is_empty() {
            return Err(format!(
                "{at}: acp.args is the command line after the executable"
            ));
        }
        let uses = |p: &str| args.iter().any(|a| a.contains(p));
        if args.iter().any(|a| {
            let rest = a.replace("{model}", "").replace("{effort}", "");
            rest.contains('{') || rest.contains('}')
        }) {
            return Err(format!(
                "{at}: acp.args may hold {{model}} and {{effort}}, nothing else in braces"
            ));
        }
        let default_model = text("default_model")?;
        if uses("{model}") && default_model.is_empty() {
            return Err(format!(
                "{at}: acp.args uses {{model}}, so acp.default_model is needed"
            ));
        }
        let efforts: Vec<String> = list("efforts")?
            .into_iter()
            .map(|e| e.to_lowercase())
            .collect();
        let default_effort = text("default_effort")?.to_lowercase();
        if uses("{effort}") && default_effort.is_empty() {
            return Err(format!(
                "{at}: acp.args uses {{effort}}, so acp.default_effort is needed"
            ));
        }
        if !efforts.is_empty() && !default_effort.is_empty() && !efforts.contains(&default_effort) {
            return Err(format!(
                "{at}: acp.default_effort is not one of acp.efforts"
            ));
        }
        let mut env = vec![];
        match t.get("env") {
            None => {}
            Some(toml::Value::Table(vars)) => {
                for (k, v) in vars {
                    let name_ok = !k.is_empty()
                        && k.starts_with(|c: char| c.is_ascii_uppercase())
                        && k.chars()
                            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_');
                    match v.as_str() {
                        Some(v) if name_ok => env.push((k.clone(), v.to_string())),
                        _ => return Err(format!("{at}: [acp.env] maps variable names to strings")),
                    }
                }
            }
            Some(_) => return Err(format!("{at}: [acp.env] maps variable names to strings")),
        }
        env.sort();
        let label = text("label")?;
        Ok(AcpSpec {
            label: if label.is_empty() {
                executable.to_string()
            } else {
                label
            },
            default_model,
            efforts,
            default_effort,
            env,
            mcp_servers: match choice("mcp_servers", "acp", &["acp", "none"])?.as_str() {
                "none" => McpServers::None,
                _ => McpServers::Acp,
            },
            resume: match choice("resume", "auto", &["auto", "load", "resume"])?.as_str() {
                "load" => Reopen::Load,
                "resume" => Reopen::Resume,
                _ => Reopen::Auto,
            },
            permission: match choice("permission", "turn", &["turn", "allow", "refuse"])?.as_str() {
                "allow" => Permissions::Allow,
                "refuse" => Permissions::Refuse,
                _ => Permissions::Turn,
            },
            request_timeout: seconds("request_timeout_s")?.unwrap_or(Duration::from_secs(120)),
            stop_wait: seconds("stop_wait_s")?.unwrap_or(Duration::from_secs(5)),
            cancel_wait: seconds("cancel_wait_s")?,
            args,
        })
    }

    /// The effort the CLI is given: the bot's when the CLI takes it, else the default.
    pub fn effort(&self, effort: Option<&str>) -> String {
        let asked = effort.unwrap_or_default().trim().to_lowercase();
        if self.efforts.contains(&asked) || (self.efforts.is_empty() && !asked.is_empty()) {
            asked
        } else {
            self.default_effort.clone()
        }
    }

    /// `program` and `args`, with the bot's model (else the default) and effort in place.
    pub fn argv(&self, program: &str, model: Option<&str>, effort: Option<&str>) -> Vec<String> {
        let model = model
            .map(str::trim)
            .filter(|m| !m.is_empty())
            .unwrap_or(&self.default_model);
        let effort = self.effort(effort);
        // One pass: a model whose name holds "{effort}" stays as it is.
        std::iter::once(program.to_string())
            .chain(self.args.iter().map(|a| {
                a.split("{model}")
                    .map(|part| part.replace("{effort}", &effort))
                    .collect::<Vec<_>>()
                    .join(model)
            }))
            .collect()
    }

    fn cancel_wait(&self) -> Duration {
        self.cancel_wait.unwrap_or(crate::keeper::INTERRUPT_WAIT)
    }
}

type Client = acp::Client<ChildStdin, Prompt>;

/// A `session/prompt` in flight: its response ends the turn.
struct Prompt {
    session: String,
    turn: String,
}

type Running = (Vec<String>, Env, super::Stopper, Arc<AtomicBool>);

struct Shared {
    allocation: super::Allocation,
    bot: String,
    /// The manifest runtime this host runs (`grok`), for its capabilities and login glue.
    runtime: String,
    program: String,
    spec: AcpSpec,
    tx: Events,
    /// The running process's client, a new one for each process: requests in flight, a `session/prompt`'s answer
    /// coming back to `finish`.
    client: Mutex<Arc<Client>>,
    /// The command and environment it runs with, and how to stop it.
    child: Mutex<Option<Running>>,
    /// The running process's `initialize` answer: what it can do.
    init: Mutex<Value>,
    turns: Mutex<HashMap<String, String>>,
    reply: Mutex<HashMap<String, String>>,
    boundary: Mutex<HashSet<String>>,
    /// The `messageId` of each turn's last text chunk.
    message: Mutex<HashMap<String, String>>,
    done_turns: Mutex<HashSet<String>>,
}

#[derive(Clone)]
pub struct AcpHost(Arc<Shared>);

impl AcpHost {
    pub fn new(bot: &str, runtime: &str, program: &str, spec: AcpSpec, tx: Events) -> AcpHost {
        let write_wait = spec.request_timeout;
        AcpHost(Arc::new(Shared {
            allocation: super::Allocation::default(),
            bot: bot.into(),
            runtime: runtime.into(),
            program: program.into(),
            spec,
            tx,
            client: Mutex::new(Arc::new(Client::new(None, write_wait))),
            child: Mutex::new(None),
            init: Mutex::new(Value::Null),
            turns: Mutex::new(HashMap::new()),
            reply: Mutex::new(HashMap::new()),
            boundary: Mutex::new(HashSet::new()),
            message: Mutex::new(HashMap::new()),
            done_turns: Mutex::new(HashSet::new()),
        }))
    }

    /// The manifest runtime this host runs.
    pub fn runtime(&self) -> &str {
        &self.0.runtime
    }

    fn label(&self) -> &str {
        &self.0.spec.label
    }

    fn emit(&self, ev: Value) {
        let _ = self.0.tx.send(ev);
    }

    pub fn track_allocation(&self, turn: Arc<AtomicBool>) {
        self.0.allocation.track(turn);
    }

    pub fn alive(&self) -> bool {
        self.0
            .child
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|(_, _, _, gone)| !gone.load(Ordering::SeqCst))
    }

    /// Start the agent with `cmd` in `env` (and the manifest's `[acp.env]`) and say `initialize`.
    async fn launch(&self, cmd: Vec<String>, env: Env) -> Result<(), HostError> {
        let label = self.label().to_string();
        let mut command = super::harness_command(None, &cmd[0]);
        let mut full = env.clone();
        full.extend(self.0.spec.env.iter().cloned());
        command
            .args(&cmd[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env_clear()
            .envs(&full);
        super::new_group(&mut command);
        let mut child = command
            .spawn()
            .map_err(|e| failed(format!("could not start {label}: {e}")))?;
        self.0.allocation.mark();
        let _stderr = crate::run_diagnostics::Stderr::capture(&mut child, self.0.tx.clone(), &full);
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| failed(format!("{label} has no stdout")))?;
        let client = Arc::new(Client::new(child.stdin.take(), self.0.spec.request_timeout));
        *self.0.client.lock().unwrap() = client.clone();
        *self.0.init.lock().unwrap() = Value::Null;
        log(&format!(
            "{}[{}]: agent started pid {}",
            self.0.runtime,
            self.0.bot,
            child.id().map_or("?".into(), |p| p.to_string())
        ));
        let (stopper, exited) = super::own(child);
        let gone = Arc::new(AtomicBool::new(false));
        let flag = gone.clone();
        let diagnostics = self.0.tx.clone();
        let reaper = format!("{label} reaper ended");
        tokio::spawn(async move {
            let status = exited
                .await
                .unwrap_or_else(|_| Err(std::io::Error::other(reaper)));
            _stderr.drained().await;
            let code = super::return_code(&status);
            let _ = diagnostics.send(json!({"kind": "error", "error": crate::run_diagnostics::process_reason(code, &_stderr.tail()), "exit_code": code, "process_exit": true}));
            flag.store(true, Ordering::SeqCst);
        });
        *self.0.child.lock().unwrap() = Some((cmd, env, stopper, gone.clone()));
        let host = self.clone();
        tokio::spawn(async move {
            let mut lines = acp::Reader::new(stdout);
            while let Ok(Some(line)) = lines.next().await {
                if let acp::Line::Message(msg) = line {
                    host.on_message(&client, msg).await;
                }
            }
            gone.store(true, Ordering::SeqCst);
            // Its own client only: a process started since keeps its requests.
            for prompt in client.close() {
                host.finish(
                    &prompt.session,
                    &prompt.turn,
                    Err(json!({"message": format!("{} exited", host.label())})),
                );
            }
        });
        let init = self.request("initialize", json!({"protocolVersion": acp::PROTOCOL_VERSION, "clientCapabilities": {"fs": {"readTextFile": false, "writeTextFile": false}}})).await?;
        *self.0.init.lock().unwrap() = init;
        Ok(())
    }

    /// Nothing runs until the first thread says which model, effort and environment the bot uses.
    pub fn start(&self) {}

    pub async fn stop(&self) {
        // A write the agent is not reading holds its input until the request timeout: the stop does not wait behind
        // it, as ending the process ends that write.
        let _ = tokio::time::timeout(self.0.spec.stop_wait, self.client().disconnect()).await;
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
        let cmd = self.0.spec.argv(
            &self.0.program,
            settings.model.as_deref(),
            settings.effort.as_deref(),
        );
        let current = self
            .0
            .child
            .lock()
            .unwrap()
            .as_ref()
            .map(|(c, e, _, _)| (c.clone(), e.clone()));
        let env = if !settings.env.is_empty() {
            settings.env.clone()
        } else {
            current
                .as_ref()
                .map(|(_, e)| e.clone())
                .unwrap_or_else(crate::env::process_environment)
        };
        // A CLI's login store can need handing to the bot user while the agent stays alive (Grok's, API-key sessions
        // included): repair before every new or resumed thread, even when the process can be reused.
        super::prepare_login_home(&self.0.runtime, &env);
        if self.alive()
            && current
                .as_ref()
                .is_some_and(|(c, e)| *c == cmd && *e == env)
        {
            return Ok(());
        }
        if current.is_some() {
            if !self.0.turns.lock().unwrap().is_empty() {
                return Err(failed(format!(
                    "cannot change the {} tool environment during a turn",
                    self.label()
                )));
            }
            self.stop().await;
        }
        self.launch(cmd, env).await
    }

    fn running(&self) -> Result<(), HostError> {
        if self.alive() {
            Ok(())
        } else {
            Err(failed(format!("{} is not running", self.label())))
        }
    }

    fn client(&self) -> Arc<Client> {
        self.0.client.lock().unwrap().clone()
    }

    async fn request(&self, method: &str, params: Value) -> Result<Value, HostError> {
        self.running()?;
        let wait = self.0.spec.request_timeout;
        match self.client().request(method, params, wait).await {
            Ok(result) => Ok(result),
            Err(RequestError::Write(e)) => Err(self.closed(e)),
            Err(RequestError::Late) => Err(failed(format!(
                "{method} timed out after {}s",
                wait.as_secs()
            ))),
            Err(RequestError::Ended) => Err(failed(format!("{method}: {} exited", self.label()))),
            Err(RequestError::Refused(err)) => {
                Err(failed(format!("{method}: {}", acp::error_text(&err))))
            }
        }
    }

    /// The agent's input closed, or a write to it failed or timed out.
    fn closed(&self, e: Option<std::io::Error>) -> HostError {
        match e {
            None => failed(format!("{} stdin closed", self.label())),
            Some(e) => failed(format!("{} stdin closed: {e}", self.label())),
        }
    }

    /// Whether a tool's permission asked for on session `params.sessionId` is given.
    fn gives(&self, params: &Value) -> acp::Permission {
        let give = match self.0.spec.permission {
            Permissions::Allow => true,
            Permissions::Refuse => false,
            Permissions::Turn => self
                .0
                .turns
                .lock()
                .unwrap()
                .contains_key(&text_of(&params["sessionId"])),
        };
        if give {
            acp::Permission::Give
        } else {
            acp::Permission::Refuse
        }
    }

    /// One message from the process whose client is `client`.
    async fn on_message<W: tokio::io::AsyncWrite + Unpin>(
        &self,
        client: &acp::Client<W, Prompt>,
        msg: Value,
    ) {
        match acp::classify(&msg) {
            Message::Response { id, reply } => {
                if let Some((prompt, reply)) = client.resolve(&id, reply) {
                    self.finish(&prompt.session, &prompt.turn, reply);
                }
            }
            // The agent asks its ACP client something.
            Message::Request { id, method, params } => {
                let reply = acp::answer(id, &method, &params, self.gives(&params));
                let _ = client.write(&reply).await;
            }
            Message::Notification { method, params } => {
                let p = Some(params).filter(|p| p.is_object()).unwrap_or(json!({}));
                self.on_notification(&method, &p);
            }
        }
    }

    fn on_notification(&self, method: &str, p: &Value) {
        if method != "session/update" {
            return; // vendor announcements (`_x.ai/*`) are informational
        }
        let sid = text_of(&p["sessionId"]);
        let Some(turn) = self.0.turns.lock().unwrap().get(&sid).cloned() else {
            return;
        };
        let u = &p["update"];
        let text = if u["content"].is_object() {
            u["content"]
                .get("text")
                .filter(|t| truthy(Some(t)))
                .cloned()
        } else {
            None
        };
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
                    let new_message = id.is_some_and(|id| {
                        self.0
                            .message
                            .lock()
                            .unwrap()
                            .insert(turn.clone(), id.into())
                            .is_some_and(|last| last != id)
                    });
                    super::add_piece(r, new_message, &text_of(&text))
                };
                self.emit(event(
                    "delta",
                    s,
                    t,
                    json!({"text": piece, "delta_kind": "text"}),
                ));
            }
            ("tool_call", _) => {
                // A new tool call separates progress from the answer that follows; the call is reported by its
                // kind (read, execute, ...), never its title or input.
                self.0.boundary.lock().unwrap().insert(turn.clone());
                let kind = u
                    .get("kind")
                    .filter(|k| truthy(Some(k)))
                    .map(text_of)
                    .unwrap_or_else(|| "tool".into());
                self.emit(event("tool", s, t, json!({"tool": kind, "status": "started", "item_id": u.get("toolCallId").cloned().unwrap_or(Value::Null)})));
            }
            ("agent_thought_chunk", Some(text)) => {
                self.emit(event(
                    "delta",
                    s,
                    t,
                    json!({"text": text, "delta_kind": "thought"}),
                ));
            }
            _ => {}
        }
    }

    fn finish(&self, sid: &str, turn: &str, reply: Reply) {
        if !self.0.done_turns.lock().unwrap().insert(turn.into()) {
            return;
        }
        self.0.turns.lock().unwrap().remove(sid);
        let text = self
            .0
            .reply
            .lock()
            .unwrap()
            .remove(turn)
            .unwrap_or_default();
        self.0.boundary.lock().unwrap().remove(turn);
        self.0.message.lock().unwrap().remove(turn);
        let (s, t) = (Some(sid), Some(turn));
        let result = match reply {
            Err(err) => {
                let msg = acp::error_text(&err);
                self.emit(event(
                    "turn_failed",
                    s,
                    t,
                    json!({"error": msg, "limit": is_limit(&msg)}),
                ));
                return;
            }
            Ok(result) => result,
        };
        let stop = result
            .get("stopReason")
            .filter(|v| truthy(Some(v)))
            .map(text_of)
            .unwrap_or_else(|| "end_turn".into());
        if !text.is_empty() && stop != "cancelled" {
            self.emit(event("message", s, t, json!({"text": text, "final": true})));
        }
        if stop == "refusal" || stop == "error" {
            self.emit(event(
                "turn_failed",
                s,
                t,
                json!({"error": format!("stopReason {stop}"), "limit": false}),
            ));
        } else {
            let status = if stop == "cancelled" {
                "interrupted"
            } else {
                "completed"
            };
            self.emit(event(
                "turn_completed",
                s,
                t,
                json!({"status": status, "stop_reason": stop}),
            ));
        }
        self.emit(event("status", s, None, json!({"state": "idle"})));
    }

    fn servers(&self, settings: &Settings) -> Value {
        match self.0.spec.mcp_servers {
            McpServers::Acp => json!(crate::mcp::acp_config(&settings.mcp_servers, &settings.env)),
            McpServers::None => json!([]),
        }
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        self.configure(settings).await?;
        let params = acp::new_session(&settings.cwd, self.servers(settings));
        let res = self.request("session/new", params).await?;
        match res["sessionId"].as_str().filter(|s| !s.is_empty()) {
            Some(sid) => Ok(sid.into()),
            None => Err(failed(format!(
                "session/new for {bot} returned no session id"
            ))),
        }
    }

    /// The method that opens a saved conversation on the running agent.
    fn resume_method(&self) -> Result<&'static str, HostError> {
        match self.0.spec.resume {
            Reopen::Load => Ok("session/load"),
            Reopen::Resume => Ok("session/resume"),
            Reopen::Auto => acp::resume_method(&self.0.init.lock().unwrap()).ok_or_else(|| {
                failed(format!(
                    "{} cannot open a saved conversation (no session/resume or session/load)",
                    self.label()
                ))
            }),
        }
    }

    pub async fn resume_thread(
        &self,
        thread_id: &str,
        settings: &Settings,
    ) -> Result<String, HostError> {
        self.configure(settings).await?;
        let method = self.resume_method()?;
        let params = acp::open_session(thread_id, &settings.cwd, self.servers(settings));
        self.request(method, params).await?;
        Ok(thread_id.into())
    }

    pub async fn start_turn(&self, thread_id: &str, text: &str) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        self.0
            .turns
            .lock()
            .unwrap()
            .insert(thread_id.into(), turn.clone());
        self.0
            .reply
            .lock()
            .unwrap()
            .insert(turn.clone(), String::new());
        let slot = acp::Slot::Hold(Prompt {
            session: thread_id.into(),
            turn: turn.clone(),
        });
        let sent = match self.running() {
            Ok(()) => self
                .client()
                .send("session/prompt", acp::prompt(thread_id, text), slot)
                .await
                .map_err(|e| match e {
                    RequestError::Write(e) => self.closed(e),
                    _ => failed(format!("{} exited", self.label())),
                }),
            Err(e) => Err(e),
        };
        if let Err(e) = sent {
            // No turn runs: the thread is free for the next one, or for a new environment.
            self.0.turns.lock().unwrap().remove(thread_id);
            self.0.reply.lock().unwrap().remove(&turn);
            return Err(e);
        }
        Ok(turn)
    }

    /// ACP has no way into a running prompt.
    pub fn steer(&self) -> Result<(), HostError> {
        Err(failed(format!(
            "{} cannot steer a running turn",
            self.label()
        )))
    }

    /// `session/cancel`, given up after the cancel wait when the agent is not reading its input.
    pub async fn interrupt(&self, thread_id: &str) -> Result<(), HostError> {
        self.running()?;
        let by = tokio::time::Instant::now() + self.0.spec.cancel_wait();
        if self.client().cancel_by(thread_id, by).await {
            Ok(())
        } else {
            Err(failed(format!(
                "{} did not take session/cancel",
                self.label()
            )))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    /// The shipped Grok manifest's `[acp]`.
    fn grok() -> AcpSpec {
        crate::harness_tools::acp_spec("grok").expect("grok.toml runs on the generic ACP host")
    }

    fn spec(text: &str) -> Result<AcpSpec, String> {
        let raw: toml::Value = toml::from_str(text).unwrap();
        AcpSpec::parse(&raw, "acme", "acme.toml (acme)")
    }

    #[test]
    fn grok_keeps_its_command_line_on_the_generic_host() {
        let g = grok();
        assert_eq!(
            g.argv("grok", None, Some("XHigh")).join(" "),
            "grok agent -m grok-4.6 --reasoning-effort xhigh --always-approve --no-leader stdio"
        );
        assert_eq!(
            g.argv("grok", Some("grok-5"), Some("max"))[3..6],
            ["grok-5", "--reasoning-effort", "high"]
        );
        assert_eq!(
            g.argv("/opt/grok", Some("  "), None).join(" "),
            "/opt/grok agent -m grok-4.6 --reasoning-effort high --always-approve --no-leader stdio"
        );
        for effort in ["low", "medium", "high", "xhigh"] {
            assert_eq!(g.effort(Some(effort)), effort);
        }
        assert_eq!(g.label, "grok agent");
        assert_eq!(
            g.resume,
            Reopen::Load,
            "Grok opens a saved session with session/load"
        );
        assert_eq!(
            g.permission,
            Permissions::Allow,
            "grok runs with --always-approve"
        );
        assert_eq!(g.mcp_servers, McpServers::Acp);
        assert_eq!(g.request_timeout, Duration::from_secs(120));
        assert_eq!(g.stop_wait, Duration::from_secs(5));
        assert_eq!(
            g.cancel_wait, None,
            "the keeper's interrupt wait, as before"
        );
        // The operator's own MCP servers from other tools' files and the xAI account stay off.
        assert_eq!(
            g.env,
            [
                ("GROK_CLAUDE_MCPS_ENABLED", "false"),
                ("GROK_CURSOR_MCPS_ENABLED", "false"),
                ("GROK_MANAGED_MCPS_ENABLED", "false"),
            ]
            .map(|(k, v)| (k.to_string(), v.to_string()))
        );
    }

    #[test]
    fn an_acp_table_is_read_with_its_defaults_and_refused_when_wrong() {
        let s = spec(
            r#"args = ["--acp", "--model={model}"]
default_model = "m1""#,
        )
        .unwrap();
        assert_eq!(s.label, "acme", "the executable when no label is given");
        assert_eq!(s.resume, Reopen::Auto);
        assert_eq!(s.permission, Permissions::Turn);
        assert_eq!(s.mcp_servers, McpServers::Acp);
        assert_eq!(
            s.argv("acme", Some("m{effort}2"), None),
            ["acme", "--acp", "--model=m{effort}2"]
        );
        assert_eq!(
            s.effort(Some(" Turbo ")),
            "turbo",
            "no efforts listed: the bot's own"
        );
        let s = spec(
            r#"args = ["acp"]
resume = "resume"
permission = "refuse"
mcp_servers = "none"
request_timeout_s = 7
stop_wait_s = 2
cancel_wait_s = 3
[env]
ACME_NO_TELEMETRY = "1""#,
        )
        .unwrap();
        assert_eq!(
            (s.resume, s.permission, s.mcp_servers),
            (Reopen::Resume, Permissions::Refuse, McpServers::None)
        );
        assert_eq!(
            (s.request_timeout, s.stop_wait, s.cancel_wait),
            (
                Duration::from_secs(7),
                Duration::from_secs(2),
                Some(Duration::from_secs(3))
            )
        );
        assert_eq!(s.env, [("ACME_NO_TELEMETRY".to_string(), "1".to_string())]);
        for (text, error) in [
            ("label = \"x\"", "acp.args is the command line"),
            ("args = []", "acp.args is the command line"),
            ("args = [\"\"]", "acp.args must be a list of strings"),
            ("args = \"acp\"", "acp.args must be a list of strings"),
            ("args = [\"{modle}\"]", "nothing else in braces"),
            (
                "args = [\"-m\", \"{model}\"]",
                "acp.default_model is needed",
            ),
            ("args = [\"{effort}\"]", "acp.default_effort is needed"),
            (
                "args = [\"{effort}\"]\nefforts = [\"low\"]\ndefault_effort = \"high\"",
                "acp.default_effort is not one of acp.efforts",
            ),
            (
                "args = [\"a\"]\nresume = \"never\"",
                "acp.resume is one of auto, load, resume",
            ),
            (
                "args = [\"a\"]\npermission = \"yes\"",
                "acp.permission is one of",
            ),
            (
                "args = [\"a\"]\nmcp_servers = \"stdio\"",
                "acp.mcp_servers is one of acp, none",
            ),
            (
                "args = [\"a\"]\nrequest_timeout_s = 0",
                "positive number of seconds",
            ),
            (
                "args = [\"a\"]\ncancel_wait_s = \"5\"",
                "positive number of seconds",
            ),
            (
                "args = [\"a\"]\n[env]\nlower = \"1\"",
                "[acp.env] maps variable names",
            ),
            (
                "args = [\"a\"]\n[env]\nACME = 1",
                "[acp.env] maps variable names",
            ),
            ("args = [\"a\"]\nmodel = \"m\"", "unknown acp field model"),
        ] {
            let got = spec(text).unwrap_err();
            assert!(got.contains(error), "{text:?}: {got}");
            assert!(got.starts_with("acme.toml (acme): "), "{got}");
        }
        let not_a_table = AcpSpec::parse(&toml::Value::String("acp".into()), "acme", "at");
        assert_eq!(not_a_table.unwrap_err(), "at: [acp] must be a table");
    }

    #[tokio::test]
    async fn every_grok_fixture_gives_the_python_hosts_events() {
        // The agent's side of one turn on session `sess-1`, whose `session/prompt` was request 3.
        for (path, expected) in crate::hosts::fixtures::cases("grok") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = AcpHost::new("acme", "grok", "grok", grok(), tx);
            host.0
                .turns
                .lock()
                .unwrap()
                .insert("sess-1".into(), "turn-1".into());
            let client = Client::new(None, Duration::from_secs(5));
            client.expect(
                3,
                acp::Slot::Hold(Prompt {
                    session: "sess-1".into(),
                    turn: "turn-1".into(),
                }),
            );
            for line in std::fs::read_to_string(&path)
                .unwrap()
                .lines()
                .filter(|l| !l.trim().is_empty())
            {
                host.on_message(&client, serde_json::from_str(line).unwrap())
                    .await;
            }
            let mut got = vec![];
            while let Ok(mut ev) = rx.try_recv() {
                ev.as_object_mut()
                    .unwrap()
                    .retain(|k, _| k != "thread_id" && k != "turn_id");
                got.push(ev);
            }
            assert_eq!(got, expected, "{}", path.display());
        }
    }

    /// A tool's permission, asked on `session`: the outcome the host wrote back to the agent.
    async fn permission_answer(host: &AcpHost, session: &str) -> Value {
        let (ours, theirs) = tokio::io::duplex(64 * 1024);
        let client: acp::Client<_, Prompt> = acp::Client::new(Some(ours), Duration::from_secs(5));
        let mut agent = acp::Reader::new(theirs);
        let ask = json!({"jsonrpc": "2.0", "id": "p1", "method": "session/request_permission",
            "params": {"sessionId": session, "options": [{"kind": "allow_once", "optionId": "once"}, {"kind": "reject_once", "optionId": "no"}]}});
        host.on_message(&client, ask).await;
        match agent.next().await.unwrap() {
            Some(acp::Line::Message(m)) => {
                assert_eq!(m["id"], "p1");
                m["result"]["outcome"].clone()
            }
            other => panic!("expected an answer, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn a_tools_permission_is_answered_as_the_manifest_says() {
        let allowed = json!({"outcome": "selected", "optionId": "once"});
        let refused = json!({"outcome": "cancelled"});
        let with = |permission| {
            let mut s = grok();
            s.permission = permission;
            let host = AcpHost::new("acme", "grok", "grok", s, mpsc::unbounded_channel().0);
            host.0
                .turns
                .lock()
                .unwrap()
                .insert("in-turn".into(), "turn-1".into());
            host
        };
        let host = with(Permissions::Allow);
        assert_eq!(permission_answer(&host, "in-turn").await, allowed);
        assert_eq!(permission_answer(&host, "idle").await, allowed);
        let host = with(Permissions::Turn);
        assert_eq!(permission_answer(&host, "in-turn").await, allowed);
        assert_eq!(
            permission_answer(&host, "idle").await,
            refused,
            "no turn runs on that session"
        );
        let host = with(Permissions::Refuse);
        assert_eq!(permission_answer(&host, "in-turn").await, refused);
    }

    /// A fake ACP agent. It records every line it reads in FAKE_CALLS, says FAKE_CAPS in its `initialize` answer, and
    /// names each new session after its own pid. A prompt's text picks the turn: `hi` streams "Hello", asks a tool's
    /// permission and ends with " there" once answered; `wait` ends only on `session/cancel`; `exit` exits mid-turn;
    /// `refuse` answers with an error.
    const FAKE_AGENT: &str = r#"#!/usr/bin/env python3
import json, os, sys
def say(m):
    m["jsonrpc"] = "2.0"
    print(json.dumps(m), flush=True)
def chunk(sid, text, mid):
    say({"method": "session/update", "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk", "messageId": mid, "content": {"type": "text", "text": text}}}})
sessions, waiting = 0, {}
say({"method": "_acme/banner", "params": {}})
print("Welcome to acme", flush=True)
for line in sys.stdin:
    with open(os.environ["FAKE_CALLS"], "a") as calls:
        calls.write(line)
    msg = json.loads(line)
    method, p = msg.get("method"), msg.get("params") or {}
    if method is None:
        if "perm" in waiting:
            pid, sid = waiting.pop("perm")
            chunk(sid, " there", "m1")
            say({"id": pid, "result": {"stopReason": "end_turn"}})
        continue
    if method == "session/cancel":
        if "cancel" in waiting:
            say({"id": waiting.pop("cancel"), "result": {"stopReason": "cancelled"}})
        continue
    if method == "initialize":
        say({"id": msg["id"], "result": {"protocolVersion": 1, "agentCapabilities": json.loads(os.environ.get("FAKE_CAPS") or "{}"), "agentInfo": {"env": os.environ.get("ACME_ONLY_IN_THE_AGENT")}}})
    elif method == "session/new":
        sessions += 1
        say({"id": msg["id"], "result": {"sessionId": "s-%d-%d" % (os.getpid(), sessions)}})
    elif method in ("session/load", "session/resume"):
        # History replayed before the answer belongs to no turn.
        chunk(p["sessionId"], "old history", "h1")
        say({"id": msg["id"], "result": {}})
    elif method == "session/prompt":
        sid, text = p["sessionId"], p["prompt"][0]["text"]
        if text == "hi":
            chunk(sid, "Hello", "m1")
            say({"id": "perm-1", "method": "session/request_permission", "params": {"sessionId": sid, "toolCall": {"toolCallId": "t1"}, "options": [{"kind": "allow_once", "optionId": "once"}, {"kind": "reject_once", "optionId": "no"}]}})
            waiting["perm"] = (msg["id"], sid)
        elif text == "wait":
            waiting["cancel"] = msg["id"]
        elif text == "exit":
            sys.exit(3)
        else:
            say({"id": msg["id"], "error": {"code": -32000, "message": "usage limit reached"}})
    else:
        say({"id": msg["id"], "error": {"code": -32601, "message": "method not found"}})
"#;

    struct Fake {
        dir: std::path::PathBuf,
        program: String,
    }

    impl Fake {
        fn new() -> Fake {
            let dir =
                std::env::temp_dir().join(format!("acp-host-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(&dir).unwrap();
            let program = dir.join("acme");
            crate::util::write_script(&program, FAKE_AGENT);
            Fake {
                program: program.display().to_string(),
                dir,
            }
        }

        /// The turn's environment: PATH for the agent's interpreter, where it records, and what it can do.
        fn env(&self, caps: &str, extra: &[(&str, &str)]) -> Env {
            let mut env: Env = [
                (
                    "PATH".to_string(),
                    std::env::var("PATH").unwrap_or_default(),
                ),
                (
                    "FAKE_CALLS".to_string(),
                    self.dir.join("calls.jsonl").display().to_string(),
                ),
                ("FAKE_CAPS".to_string(), caps.to_string()),
            ]
            .into_iter()
            .collect();
            env.extend(extra.iter().map(|(k, v)| (k.to_string(), v.to_string())));
            env
        }

        /// Every message the agent read, in order.
        fn calls(&self) -> Vec<Value> {
            std::fs::read_to_string(self.dir.join("calls.jsonl"))
                .unwrap_or_default()
                .lines()
                .map(|l| serde_json::from_str(l).unwrap())
                .collect()
        }

        fn methods(&self) -> Vec<String> {
            self.calls().iter().map(|c| text_of(&c["method"])).collect()
        }
    }

    impl Drop for Fake {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    fn acme(resume: &str, permission: &str) -> AcpSpec {
        spec(&format!(
            r#"args = ["--model", "{{model}}", "acp"]
default_model = "acme-1"
label = "acme agent"
resume = "{resume}"
permission = "{permission}"
request_timeout_s = 10
cancel_wait_s = 5
[env]
ACME_ONLY_IN_THE_AGENT = "yes""#
        ))
        .unwrap()
    }

    fn settings(fake: &Fake, env: Env) -> Settings {
        Settings {
            env,
            cwd: fake.dir.display().to_string(),
            mcp_servers: vec![
                json!({"name": "hub", "transport": "http", "url": "https://mcp.acme.example/mcp",
                "headers": {"Authorization": "Bearer ${HUB_TOKEN}"}}),
            ],
            ..Default::default()
        }
    }

    /// Events until one of `kinds` for a turn, which ends the wait.
    async fn until(rx: &mut mpsc::UnboundedReceiver<Value>, kinds: &[&str]) -> Vec<Value> {
        let mut got = vec![];
        loop {
            let ev = tokio::time::timeout(Duration::from_secs(10), rx.recv())
                .await
                .expect("the turn ended in time")
                .expect("the host is still sending");
            let kind = text_of(&ev["kind"]);
            got.push(ev);
            if kinds.contains(&kind.as_str()) {
                return got;
            }
        }
    }

    fn texts(events: &[Value], kind: &str) -> Vec<String> {
        events
            .iter()
            .filter(|e| e["kind"] == kind)
            .map(|e| text_of(&e["text"]))
            .collect()
    }

    #[tokio::test]
    async fn a_manifest_agent_starts_prompts_answers_a_permission_and_resumes() {
        let fake = Fake::new();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = AcpHost::new("acme", "acme", &fake.program, acme("load", "turn"), tx);
        host.start();
        assert!(!host.alive(), "nothing runs before the first thread");
        let env = fake.env("{}", &[("HUB_TOKEN", "synthetic-token")]);
        let mut s = settings(&fake, env);
        s.model = Some("acme-2".into());
        let thread = host.start_thread("acme", &s).await.unwrap();
        assert!(host.alive());
        let calls = fake.calls();
        assert_eq!(calls[0]["method"], "initialize");
        assert_eq!(calls[0]["params"]["protocolVersion"], 1);
        assert_eq!(calls[1]["method"], "session/new");
        assert_eq!(
            calls[1]["params"]["cwd"],
            json!(fake.dir.display().to_string())
        );
        assert_eq!(
            calls[1]["params"]["mcpServers"],
            json!([{"type": "http", "name": "hub", "url": "https://mcp.acme.example/mcp",
                "headers": [{"name": "Authorization", "value": "Bearer synthetic-token"}]}]),
            "the bot's servers in ACP's shape, their variables filled in"
        );
        let cmd = host.0.child.lock().unwrap().as_ref().unwrap().0.clone();
        assert_eq!(cmd, [fake.program.as_str(), "--model", "acme-2", "acp"]);
        assert!(
            !s.env.contains_key("ACME_ONLY_IN_THE_AGENT"),
            "[acp.env] goes on the agent's process only"
        );
        assert_eq!(
            host.0.init.lock().unwrap()["agentInfo"]["env"],
            "yes",
            "the agent got [acp.env]"
        );

        let turn = host.start_turn(&thread, "hi").await.unwrap();
        let events = until(&mut rx, &["turn_completed", "turn_failed"]).await;
        assert_eq!(texts(&events, "delta"), ["Hello", " there"]);
        assert_eq!(texts(&events, "message"), ["Hello there"]);
        let done = events.last().unwrap();
        assert_eq!(
            (
                &done["kind"],
                &done["status"],
                &done["turn_id"],
                &done["thread_id"]
            ),
            (
                &json!("turn_completed"),
                &json!("completed"),
                &json!(turn),
                &json!(thread)
            )
        );
        let idle = rx.try_recv().unwrap();
        assert_eq!(
            (&idle["kind"], &idle["state"]),
            (&json!("status"), &json!("idle"))
        );
        // The permission was asked during the turn: given, with the allow option.
        let answer = fake
            .calls()
            .into_iter()
            .find(|c| c["id"] == "perm-1")
            .expect("the host answered the permission");
        assert_eq!(
            answer["result"]["outcome"],
            json!({"outcome": "selected", "optionId": "once"})
        );

        // Resumed on the same process with session/load; the replayed history reaches no turn.
        assert_eq!(host.resume_thread(&thread, &s).await.unwrap(), thread);
        assert_eq!(fake.methods().last().unwrap(), "session/load");
        assert_eq!(
            fake.methods().iter().filter(|m| *m == "initialize").count(),
            1,
            "the agent was reused"
        );
        let after = rx.try_recv();
        assert!(after.is_err(), "history is no turn's: {after:?}");

        // A refused prompt fails the turn with the agent's reason.
        host.start_turn(&thread, "refuse").await.unwrap();
        let events = until(&mut rx, &["turn_completed", "turn_failed"]).await;
        let failed = events.last().unwrap();
        assert_eq!(
            (&failed["kind"], &failed["error"], &failed["limit"]),
            (
                &json!("turn_failed"),
                &json!("usage limit reached"),
                &json!(true)
            )
        );
        assert!(
            matches!(host.steer(), Err(HostError::Failed(m)) if m == "acme agent cannot steer a running turn")
        );
        host.stop().await;
        assert!(!host.alive());
    }

    #[tokio::test]
    async fn resume_follows_the_manifest_or_what_the_agent_says_it_can_do() {
        let fake = Fake::new();
        let run = |resume: &'static str, caps: &'static str| {
            let fake = &fake;
            async move {
                let host = AcpHost::new(
                    "acme",
                    "acme",
                    &fake.program,
                    acme(resume, "turn"),
                    mpsc::unbounded_channel().0,
                );
                let s = settings(fake, fake.env(caps, &[]));
                let got = host.resume_thread("saved-1", &s).await;
                host.stop().await;
                (got, fake.methods().last().cloned().unwrap_or_default())
            }
        };
        let (got, last) = run("resume", "{}").await;
        assert_eq!(
            (got.unwrap(), last.as_str()),
            ("saved-1".to_string(), "session/resume")
        );
        let (got, last) = run(
            "auto",
            r#"{"sessionCapabilities": {"resume": {}}, "loadSession": true}"#,
        )
        .await;
        assert_eq!(
            (got.unwrap(), last.as_str()),
            ("saved-1".to_string(), "session/resume")
        );
        let (got, last) = run("auto", r#"{"loadSession": true}"#).await;
        assert_eq!(
            (got.unwrap(), last.as_str()),
            ("saved-1".to_string(), "session/load")
        );
        let (got, last) = run("auto", "{}").await;
        assert!(
            matches!(&got, Err(HostError::Failed(m)) if m.contains("acme agent cannot open a saved conversation")),
            "{:?}",
            got.err()
        );
        assert_eq!(
            last, "initialize",
            "nothing was asked of an agent that cannot"
        );
        // The saved conversation is opened in the turn's folder with the bot's servers.
        let open = fake
            .calls()
            .into_iter()
            .rev()
            .find(|c| c["method"] == "session/load")
            .unwrap();
        assert_eq!(open["params"]["sessionId"], "saved-1");
        assert_eq!(open["params"]["cwd"], json!(fake.dir.display().to_string()));
        assert_eq!(open["params"]["mcpServers"][0]["name"], "hub");
    }

    #[tokio::test]
    async fn a_cancel_ends_the_turn_as_interrupted() {
        let fake = Fake::new();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = AcpHost::new("acme", "acme", &fake.program, acme("load", "turn"), tx);
        let thread = host
            .start_thread("acme", &settings(&fake, fake.env("{}", &[])))
            .await
            .unwrap();
        let turn = host.start_turn(&thread, "wait").await.unwrap();
        host.interrupt(&thread).await.unwrap();
        let events = until(&mut rx, &["turn_completed", "turn_failed"]).await;
        let done = events.last().unwrap();
        assert_eq!(
            (
                &done["kind"],
                &done["status"],
                &done["stop_reason"],
                &done["turn_id"]
            ),
            (
                &json!("turn_completed"),
                &json!("interrupted"),
                &json!("cancelled"),
                &json!(turn)
            )
        );
        let cancel = fake
            .calls()
            .into_iter()
            .find(|c| c["method"] == "session/cancel")
            .unwrap();
        assert_eq!(cancel["params"], json!({"sessionId": thread}));
        assert!(
            cancel.get("id").is_none(),
            "session/cancel is a notification"
        );
        host.stop().await;
        // A stopped agent takes no cancel.
        assert!(
            matches!(host.interrupt(&thread).await, Err(HostError::Failed(m)) if m == "acme agent is not running")
        );
    }

    fn pid_of(thread: &str) -> String {
        thread.split('-').nth(1).unwrap().to_string()
    }

    #[tokio::test]
    async fn an_agent_that_exits_fails_its_turn_and_the_next_thread_restarts_it() {
        let fake = Fake::new();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = AcpHost::new("acme", "acme", &fake.program, acme("load", "turn"), tx);
        let s = settings(&fake, fake.env("{}", &[]));
        let thread = host.start_thread("acme", &s).await.unwrap();
        let turn = host.start_turn(&thread, "exit").await.unwrap();
        let events = until(&mut rx, &["turn_failed", "turn_completed"]).await;
        let failed = events.last().unwrap();
        assert_eq!(
            (&failed["kind"], &failed["error"], &failed["turn_id"]),
            (
                &json!("turn_failed"),
                &json!("acme agent exited"),
                &json!(turn)
            )
        );
        // The reaper reports the exit too; then the host is no longer alive.
        for _ in 0..100 {
            if !host.alive() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert!(!host.alive());
        assert!(
            matches!(host.start_turn(&thread, "hi").await, Err(HostError::Failed(m)) if m == "acme agent is not running")
        );
        assert!(
            host.0.turns.lock().unwrap().is_empty(),
            "a turn that never started holds nothing"
        );
        // The next thread starts a new process with the same command and environment.
        let again = host.start_thread("acme", &s).await.unwrap();
        assert_ne!(pid_of(&again), pid_of(&thread));
        assert!(host.alive());
        assert_eq!(
            fake.methods().iter().filter(|m| *m == "initialize").count(),
            2
        );
        host.stop().await;
    }

    #[tokio::test]
    async fn a_new_environment_or_model_restarts_the_agent_but_never_during_a_turn() {
        let fake = Fake::new();
        let host = AcpHost::new(
            "acme",
            "acme",
            &fake.program,
            acme("load", "turn"),
            mpsc::unbounded_channel().0,
        );
        let s = settings(&fake, fake.env("{}", &[("TURN", "1")]));
        let first = host.start_thread("acme", &s).await.unwrap();
        let same = host.start_thread("acme", &s).await.unwrap();
        assert_eq!(
            pid_of(&same),
            pid_of(&first),
            "the same settings reuse the agent"
        );
        let other_env = settings(&fake, fake.env("{}", &[("TURN", "2")]));
        let second = host.start_thread("acme", &other_env).await.unwrap();
        assert_ne!(
            pid_of(&second),
            pid_of(&first),
            "another environment restarts it"
        );
        let mut other_model = settings(&fake, fake.env("{}", &[("TURN", "2")]));
        other_model.model = Some("acme-9".into());
        let third = host.start_thread("acme", &other_model).await.unwrap();
        assert_ne!(pid_of(&third), pid_of(&second), "another model restarts it");
        // A running turn keeps its agent: the change is refused, and the turn still ends.
        host.start_turn(&third, "wait").await.unwrap();
        let refused = host.start_thread("acme", &other_env).await;
        assert!(
            matches!(&refused, Err(HostError::Failed(m)) if m == "cannot change the acme agent tool environment during a turn"),
            "{:?}",
            refused.err()
        );
        assert!(host.alive());
        host.interrupt(&third).await.unwrap();
        host.stop().await;
    }
}
