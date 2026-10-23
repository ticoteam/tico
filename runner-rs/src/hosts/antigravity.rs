//! The Antigravity host: one persistent `agy` per Hub conversation, kept warm
//! between turns by crate::warm. Each turn is one stream-json user line on stdin; the provider's conversation id
//! is saved beside the stable thread id (`<home>/<thread>.json`), so a cold start resumes it with
//! `--conversation`. A failed or interrupted turn is never replayed. `agy` runs in a session of its own and is
//! stopped as a process group.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::{mpsc, watch};

use super::{event, failed, is_limit, Events, HostError, Settings};
use crate::util::{log, py_dumps, text_of};

pub const NAME: &str = "antigravity";
pub const DEFAULT_MODEL: &str = "gemini-3.8-flash";
const EFFORTS: &[&str] = &["low", "medium", "high"];
const STOP_GRACE: Duration = Duration::from_secs(5);
const USAGE_KEYS: [&str; 3] = ["input_tokens", "output_tokens", "cache_read_tokens"];

/// `agy`'s argv for a first turn: model `<model>-<effort>`, effort low unless set, permissions skipped.
pub fn argv(cmd: &[String], settings: &Settings, effort: Option<&str>, provider: Option<&str>) -> Result<Vec<String>, HostError> {
    let model = settings.model.clone().filter(|m| !m.is_empty()).unwrap_or_else(|| DEFAULT_MODEL.into());
    let effort = effort.filter(|e| !e.is_empty()).or(settings.effort.as_deref().filter(|e| !e.is_empty())).unwrap_or("low");
    if !EFFORTS.contains(&effort) {
        return Err(failed("Unsupported Antigravity effort"));
    }
    let mut argv = cmd.to_vec();
    for a in ["--input-format", "stream-json", "--output-format", "stream-json", "--model"] {
        argv.push(a.into());
    }
    argv.extend([format!("{model}-{effort}"), "--effort".into(), effort.into(), "--dangerously-skip-permissions".into()]);
    if let Some(p) = provider {
        argv.extend(["--conversation".into(), p.into()]);
    }
    Ok(argv)
}

/// Python's `str(uuid.UUID(text))`: the canonical lower-case hyphenated form, or None when it is no uuid.
fn canonical(text: &str) -> Option<String> {
    uuid::Uuid::parse_str(text.trim()).ok().map(|u| u.hyphenated().to_string())
}

/// Python's `int(value or 0)` for a usage count.
fn count(v: &Value) -> i64 {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => *b as i64,
        _ => 0,
    }
}

/// The running `agy`: its stdin, and the task that owns, signals and reaps it.
struct Proc {
    stdin: mpsc::UnboundedSender<String>,
    kill: mpsc::UnboundedSender<i32>,
    exited: watch::Receiver<bool>,
}

struct Inner {
    up: bool,
    proc: Option<Proc>,
    thread: Option<String>,
    turn: Option<String>,
    settings: Option<Settings>,
    provider: Option<String>,
    usage: Value,
    tx: Events,
    reader: Option<tokio::task::JoinHandle<()>>,
}

struct Shared {
    home: PathBuf,
    cmd: Vec<String>,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct AntigravityHost(Arc<Shared>);

impl AntigravityHost {
    pub fn new(home: &Path, cmd: Vec<String>, tx: Events) -> AntigravityHost {
        AntigravityHost(Arc::new(Shared {
            home: home.to_path_buf(),
            cmd,
            inner: Mutex::new(Inner {
                up: false,
                proc: None,
                thread: None,
                turn: None,
                settings: None,
                provider: None,
                usage: json!({}),
                tx,
                reader: None,
            }),
        }))
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Whether two handles are the one host (the warm cache finds its entry by this).
    pub fn same(&self, other: &AntigravityHost) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }

    /// Send this host's events to `tx` from now on: a warm host serves one turn after another.
    pub fn attach(&self, tx: Events) {
        self.inner().tx = tx;
    }

    pub fn start(&self) -> Result<(), HostError> {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(&self.0.home).map_err(|e| failed(format!("could not make the Antigravity home: {e}")))?;
        if !std::fs::symlink_metadata(&self.0.home).map_err(|e| failed(e.to_string()))?.is_dir() {
            return Err(failed("Antigravity home must not be a symbolic link"));
        }
        // agy reads the bot-owned lease-token here; its conversation record remains supervisor-only.
        let mode = if crate::isolation::enabled() { 0o711 } else { 0o700 };
        std::fs::set_permissions(&self.0.home, std::fs::Permissions::from_mode(mode)).map_err(|e| failed(e.to_string()))?;
        self.inner().up = true;
        Ok(())
    }

    pub fn alive(&self) -> bool {
        let inner = self.inner();
        inner.up && inner.proc.as_ref().is_none_or(|p| !*p.exited.borrow())
    }

    fn path(&self, thread: &str) -> Option<PathBuf> {
        canonical(thread).map(|t| self.0.home.join(format!("{t}.json")))
    }

    pub fn start_thread(&self, settings: &Settings) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        let mut inner = self.inner();
        inner.thread = Some(id.clone());
        inner.settings = Some(settings.clone());
        inner.provider = None;
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        if self.inner().thread.as_deref() == Some(thread_id) && self.alive() {
            return Ok(thread_id.into());
        }
        let unavailable = || failed("Antigravity conversation is unavailable");
        let path = self.path(thread_id).ok_or_else(unavailable)?;
        let record: Value = serde_json::from_str(&std::fs::read_to_string(path).map_err(|_| unavailable())?).map_err(|_| unavailable())?;
        let provider = record["conversation_id"].as_str().and_then(canonical).ok_or_else(unavailable)?;
        let mut inner = self.inner();
        inner.provider = Some(provider);
        inner.thread = Some(thread_id.into());
        inner.settings = Some(settings.clone());
        Ok(thread_id.into())
    }

    /// Save the provider's conversation id for this thread, once; a different one later is an error.
    fn remember(&self, inner: &mut Inner, cid: &str) -> Result<(), String> {
        let cid = canonical(cid).ok_or("not a conversation id")?;
        if inner.provider.as_deref() == Some(cid.as_str()) {
            return Ok(());
        }
        if inner.provider.is_some() {
            return Err("Antigravity resumed the wrong conversation".into());
        }
        inner.provider = Some(cid.clone());
        let path = inner.thread.as_deref().and_then(|t| self.path(t)).ok_or("not a thread id")?;
        let tmp = path.with_extension("tmp");
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp).map_err(|e| e.to_string())?;
        f.write_all(py_dumps(&json!({"conversation_id": cid})).as_bytes()).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
        Ok(())
    }

    fn command(&self, settings: &Settings, effort: Option<&str>, provider: Option<&str>) -> Result<Command, HostError> {
        let argv = argv(&self.0.cmd, settings, effort, provider)?;
        let mut command = crate::isolation::command(&argv[0]);
        command.args(&argv[1..]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).current_dir(&settings.cwd);
        if !settings.env.is_empty() {
            command.env_clear().envs(&settings.env);
        }
        // start_new_session: its own process group, so a stop reaches the tools it started too.
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        Ok(command)
    }

    fn spawn(&self, settings: &Settings, effort: Option<&str>, provider: Option<&str>) -> Result<(Proc, tokio::process::ChildStdout), HostError> {
        let mut command = self.command(settings, effort, provider)?;
        let mut child = command.spawn().map_err(|e| failed(format!("could not start agy: {e}")))?;
        let host = self.clone();
        let _stderr = crate::run_diagnostics::Stderr::capture_with(&mut child, move |value| {
            let inner = host.inner();
            if inner.turn.is_some() { let _ = inner.tx.send(value); }
        }, &settings.env);
        log(&format!("antigravity: agy started pid {}", child.id().map_or("?".into(), |p| p.to_string())));
        let stdout = child.stdout.take().ok_or_else(|| failed("agy has no stdout"))?;
        let mut stdin = child.stdin.take();
        let (lines, mut rx) = mpsc::unbounded_channel::<String>();
        tokio::spawn(async move {
            while let Some(line) = rx.recv().await {
                let Some(s) = stdin.as_mut() else { break };
                if s.write_all(line.as_bytes()).await.is_err() || s.flush().await.is_err() {
                    break;
                }
            }
        });
        let (kill, mut asks) = mpsc::unbounded_channel::<i32>();
        let (done, exited) = watch::channel(false);
        let host = self.clone();
        tokio::spawn(async move {
            let mut asking = true;
            loop {
                tokio::select! {
                    status = child.wait() => {
                        let code = super::return_code(&status);
                        let inner = host.inner();
                        let _ = inner.tx.send(json!({"kind": "error", "error": crate::run_diagnostics::process_reason(code, &_stderr.tail()), "exit_code": code, "process_exit": true}));
                        break;
                    },
                    sig = asks.recv(), if asking => match sig {
                        // Not reaped yet: only this task waits for the child, so the group is still its own.
                        Some(sig) => if let Some(pid) = child.id() {
                            unsafe { libc::killpg(pid as i32, sig); }
                        },
                        None => asking = false,
                    },
                }
            }
            let _ = done.send(true);
        });
        Ok((Proc { stdin: lines, kill, exited }, stdout))
    }

    pub fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let mut inner = self.inner();
        let alive = inner.up && inner.proc.as_ref().is_none_or(|p| !*p.exited.borrow());
        if !alive || inner.thread.as_deref() != Some(thread_id) || inner.turn.is_some() {
            return Err(failed("Antigravity is unavailable or already running a turn"));
        }
        if inner.proc.is_none() {
            let settings = inner.settings.clone().unwrap_or_default();
            let (proc, stdout) = self.spawn(&settings, effort, inner.provider.as_deref())?;
            inner.proc = Some(proc);
            let host = self.clone();
            inner.reader = Some(tokio::spawn(async move { host.read(stdout).await }));
        }
        let line = py_dumps(&json!({"event": "user", "message": {"content": text}})) + "\n";
        if inner.proc.as_ref().is_none_or(|p| p.stdin.send(line).is_err()) {
            return Err(failed("Antigravity is unavailable or already running a turn"));
        }
        inner.turn = Some(turn.clone());
        let _ = inner.tx.send(event("status", Some(thread_id), Some(&turn), json!({"state": "active"})));
        Ok(turn)
    }

    async fn read(&self, stdout: tokio::process::ChildStdout) {
        let mut lines = BufReader::new(stdout).lines();
        let why = loop {
            let line = match lines.next_line().await {
                Ok(Some(l)) => l,
                Ok(None) => break None,
                Err(e) => break Some(e.to_string()),
            };
            if line.trim().is_empty() {
                continue;
            }
            let ev: Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(_) => break Some("JSONDecodeError".into()),
            };
            let mut inner = self.inner();
            if !inner.up {
                break None;
            }
            match self.on_event(&mut inner, &ev) {
                Ok(()) => {}
                Err(e) => break Some(e),
            }
        };
        if let Some(why) = why {
            log(&format!("Antigravity stream failed: {why}"));
        }
        let mut inner = self.inner();
        if inner.up {
            if let Some(turn) = inner.turn.take() {
                let ev = event("turn_failed", inner.thread.as_deref(), Some(&turn), json!({"error": "Antigravity stream ended before a result", "limit": false}));
                let _ = inner.tx.send(ev);
            }
        }
        inner.turn = None;
        inner.up = false;
    }

    /// One stdout event; Err ends the stream as Python's exception does.
    fn on_event(&self, inner: &mut Inner, ev: &Value) -> Result<(), String> {
        let (tid, turn) = (inner.thread.clone(), inner.turn.clone());
        if ev["event"] == "init" {
            let cid = Some(&ev["conversation_id"]).filter(|c| crate::util::truthy(Some(c))).unwrap_or(&ev["init"]["conversation_id"]);
            if crate::util::truthy(Some(cid)) {
                self.remember(inner, &text_of(cid))?;
            }
        }
        let step = ev.get("step_update").filter(|s| crate::util::truthy(Some(s))).cloned().unwrap_or(json!({}));
        if crate::util::truthy(step.get("conversation_id")) {
            self.remember(inner, &text_of(&step["conversation_id"]))?;
        }
        let Some(turn) = turn else { return Ok(()) };
        let (tid, turn) = (tid.as_deref(), Some(turn.as_str()));
        if step["step_type"] == "agent_response" && crate::util::truthy(step.get("text_delta")) {
            let _ = inner.tx.send(event("delta", tid, turn, json!({"text": step["text_delta"], "delta_kind": "text"})));
        }
        if step["step_type"] == "tool" {
            let tool = step.get("tool_name").cloned().unwrap_or(json!("tool"));
            let _ = inner.tx.send(event("tool", tid, turn, json!({"tool": tool, "state": step.get("state").cloned().unwrap_or(Value::Null)})));
        }
        if ev["event"] != "result" {
            return Ok(());
        }
        let result = ev.get("result").filter(|r| r.is_object()).ok_or("KeyError: 'result'")?;
        if crate::util::truthy(result.get("conversation_id")) {
            self.remember(inner, &text_of(&result["conversation_id"]))?;
        }
        // Antigravity reports the process's running total: this turn spent the difference.
        let usage = result.get("usage").filter(|u| u.is_object()).cloned().unwrap_or(json!({}));
        let mut delta = Map::new();
        for k in USAGE_KEYS {
            delta.insert(k.into(), json!((count(&usage[k]) - count(&inner.usage[k])).max(0)));
        }
        inner.usage = usage;
        let (i, o, c) = (count(&delta["input_tokens"]), count(&delta["output_tokens"]), count(&delta["cache_read_tokens"]));
        let send = |ev: Value| {
            let _ = inner.tx.send(ev);
        };
        send(event("tokens", tid, turn, json!({"input": i, "output": o, "cached": c, "total": i + o, "usage": {"input": i, "cached": c, "output": o}})));
        if result["status"] == "SUCCESS" {
            let text = result["response"].as_str().unwrap_or_default().trim().to_string();
            send(event("message", tid, turn, json!({"text": text, "final": true})));
            send(event("turn_completed", tid, turn, json!({"status": "completed"})));
        } else {
            let error = [&result["error"], &result["status"]].into_iter().find(|v| crate::util::truthy(Some(v))).map(text_of).unwrap_or_else(|| "Antigravity failed".into());
            send(event("turn_failed", tid, turn, json!({"error": error, "limit": is_limit(&error)})));
        }
        send(event("status", tid, turn, json!({"state": "idle"})));
        inner.turn = None;
        Ok(())
    }

    pub fn steer(&self) -> Result<(), HostError> {
        Err(failed("Antigravity steering is not enabled"))
    }

    /// Interrupting a turn stops the process: the next turn starts a new one on the saved conversation.
    pub async fn interrupt(&self) {
        self.stop().await;
    }

    /// SIGTERM to the group, SIGKILL after 5 s, then up to 5 s more for it to go.
    pub async fn stop(&self) {
        let (proc, reader) = {
            let mut inner = self.inner();
            inner.up = false;
            (inner.proc.as_ref().map(|p| (p.kill.clone(), p.exited.clone())), inner.reader.take())
        };
        if let Some((kill, mut exited)) = proc {
            if !*exited.borrow() {
                let _ = kill.send(libc::SIGTERM);
                if tokio::time::timeout(STOP_GRACE, exited.wait_for(|e| *e)).await.is_err() {
                    let _ = kill.send(libc::SIGKILL);
                    let _ = tokio::time::timeout(STOP_GRACE, exited.wait_for(|e| *e)).await;
                }
            }
        }
        if let Some(r) = reader {
            let _ = tokio::time::timeout(Duration::from_secs(2), r).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn turn_launch_keeps_the_bot_identity_and_revocable_token_access() {
        if !super::super::isolationtests::child("hosts::antigravity::tests::turn_launch_keeps_the_bot_identity_and_revocable_token_access") { return; }
        let f = super::super::isolationtests::Fixture::new();
        let mut settings = f.settings();
        let make = |home: &Path, _: &crate::env::Env| AntigravityHost::new(home, f.argv(), mpsc::unbounded_channel().0);
        let host = make(&f.root.join("warm/scope"), &settings.env);
        super::super::isolationtests::prefix(&host.command(&settings, None, None).unwrap(), &argv(&f.argv(), &settings, None, None).unwrap());
        if !f.actual() { return; }
        let warm = crate::warm::WarmSessions::new(f.root.join("warm"), 1);
        settings.env.insert("HUB_TOKEN".into(), "fixture attempt token".into());
        let attempt = json!({"bot":"acme","conversation":{"id":"conversation"},"principal":"human:acme","config":{"harness":"antigravity"}});
        let (host, env) = warm.acquire(&attempt, &settings.env, make).await.unwrap();
        settings.env = env;
        host.start().unwrap();
        let thread = host.start_thread(&settings);
        let provider = "6f9619ff-8b86-d011-b42d-00c04fc964ff";
        host.remember(&mut host.inner(), provider).unwrap();
        let record = host.path(&thread).unwrap();
        settings.env.insert("TEST_PRIVATE".into(), record.display().to_string());
        let token = PathBuf::from(settings.env["HUB_TOKEN"].strip_prefix("tico-file:").unwrap());
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        assert_eq!(token.metadata().unwrap().uid(), 10003);
        assert_eq!(token.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        f.run(host.command(&settings, None, Some(provider)).unwrap(), &[&token], &[]).await;
        assert_eq!(record.metadata().unwrap().uid(), 0, "conversation record stays supervisor-only");
        warm.release(&host, true).await;
        assert_eq!(std::fs::read_to_string(&token).unwrap(), "", "no live token between turns");
        assert_eq!(token.metadata().unwrap().uid(), 10003);
    }

    /// An `agy` that answers every stdin line with the fixture (`$FIXTURE`), or, with `$CUMULATIVE`, a result whose
    /// usage grows by the same amount each turn, and exits when stdin closes.
    fn fake_agy(dir: &Path) -> String {
        let path = dir.join("agy");
        crate::util::write_script(
            &path,
            "#!/bin/sh\necho \"$@\" >> \"$ARGS_LOG\"\nn=0\nwhile IFS= read -r line; do\n  n=$((n+1))\n  if [ -n \"$CUMULATIVE\" ]; then\n    echo '{\"event\": \"init\", \"conversation_id\": \"6f9619ff-8b86-d011-b42d-00c04fc964ff\"}'\n    echo \"{\\\"event\\\": \\\"result\\\", \\\"result\\\": {\\\"status\\\": \\\"SUCCESS\\\", \\\"response\\\": \\\"turn $n\\\", \\\"usage\\\": {\\\"input_tokens\\\": $((n*10)), \\\"output_tokens\\\": $((n*2)), \\\"cache_read_tokens\\\": $((n*4))}}}\"\n  else\n    cat \"$FIXTURE\"\n  fi\ndone\n",
        );
        path.display().to_string()
    }

    async fn turn_events(rx: &mut mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
        let mut got = vec![];
        while let Ok(Some(mut ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
            let end = ev["kind"] == "status" && ev["state"] == "idle" || ev["kind"] == "turn_failed" && ev["error"] == "Antigravity stream ended before a result";
            ev.as_object_mut().unwrap().retain(|k, _| k != "thread_id" && k != "turn_id");
            got.push(ev);
            if end {
                break;
            }
        }
        got
    }

    fn settings(dir: &Path, vars: &[(&str, String)]) -> Settings {
        let mut s = Settings { cwd: dir.display().to_string(), model: Some("gemini-3.8-flash".into()), ..Default::default() };
        s.env.insert("PATH".into(), "/usr/bin:/bin".into());
        s.env.insert("ARGS_LOG".into(), dir.join("args.log").display().to_string());
        for (k, v) in vars {
            s.env.insert(k.to_string(), v.clone());
        }
        s
    }

    #[tokio::test]
    async fn every_antigravity_fixture_gives_the_python_hosts_events() {
        let dir = std::env::temp_dir().join(format!("fake-agy-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake_agy(&dir);
        for (path, expected) in crate::hosts::fixtures::cases("antigravity") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let home = dir.join(path.file_stem().unwrap());
            let host = AntigravityHost::new(&home, vec![cmd.clone()], tx);
            host.start().unwrap();
            let s = settings(&dir, &[("FIXTURE", path.display().to_string())]);
            let thread = host.start_thread(&s);
            host.start_turn(&thread, "hello", None).unwrap();
            assert_eq!(turn_events(&mut rx).await, expected, "{}", path.display());
            host.stop().await;
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn one_process_serves_each_turn_its_own_usage_and_a_cold_start_resumes_the_conversation() {
        let dir = std::env::temp_dir().join(format!("fake-agy-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake_agy(&dir);
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = AntigravityHost::new(&dir.join("home"), vec![cmd.clone()], tx);
        host.start().unwrap();
        let s = settings(&dir, &[("CUMULATIVE", "1".into())]);
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", Some("medium")).unwrap();
        let first = turn_events(&mut rx).await;
        assert_eq!(host.resume_thread(&thread, &s).unwrap(), thread);
        host.start_turn(&thread, "two", None).unwrap();
        let second = turn_events(&mut rx).await;
        for (events, text) in [(&first, "turn 1"), (&second, "turn 2")] {
            assert_eq!(events[0], json!({"kind": "status", "state": "active"}));
            assert_eq!(events[1], json!({"kind": "tokens", "input": 10, "output": 2, "cached": 4, "total": 12, "usage": {"input": 10, "cached": 4, "output": 2}}));
            assert_eq!(events[2], json!({"kind": "message", "text": text, "final": true}));
        }
        host.stop().await;
        assert!(!host.alive());
        // The provider's id is saved beside the thread, so a new process resumes that conversation.
        let (tx, mut rx) = mpsc::unbounded_channel();
        let cold = AntigravityHost::new(&dir.join("home"), vec![cmd], tx);
        cold.start().unwrap();
        assert!(cold.resume_thread(&uuid::Uuid::new_v4().to_string(), &s).is_err());
        cold.resume_thread(&thread, &s).unwrap();
        cold.start_turn(&thread, "three", None).unwrap();
        assert_eq!(turn_events(&mut rx).await[2]["text"], "turn 1");
        cold.stop().await;
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        let runs: Vec<&str> = args.lines().collect();
        assert_eq!(runs[0], "--input-format stream-json --output-format stream-json --model gemini-3.8-flash-medium --effort medium --dangerously-skip-permissions");
        assert!(runs[1].ends_with("--effort low --dangerously-skip-permissions --conversation 6f9619ff-8b86-d011-b42d-00c04fc964ff"), "{}", runs[1]);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn efforts_follow_the_python_host() {
        let s = Settings { effort: Some("xhigh".into()), ..Default::default() };
        assert!(argv(&["agy".into()], &s, None, None).is_err());
        assert_eq!(argv(&["agy".into()], &s, Some("high"), None).unwrap()[6], "gemini-3.8-flash-high");
    }
}
