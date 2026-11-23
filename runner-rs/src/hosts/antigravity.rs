//! The Antigravity host: each turn starts its own `agy` and reaps it when the turn ends. The prompt is one stream-json
//! user line on stdin, closed once the result is in; the provider's conversation id is saved beside the stable thread id
//! (`<home>/<thread>.json`, supervisor-only), so the next turn resumes it with `--conversation` on any model or effort.
//! A failed or interrupted turn is never replayed. `agy` runs in a process group of its own, owned by the shared process
//! owner: a stop reaches the group, and whatever of it is left when `agy` exits goes too. A resumed turn's prompt is held
//! until `agy` names its conversation (`init`, sent before it reads its input): a turn that must continue the saved one
//! is never sent into another. On any other turn, a conversation `agy` began in place of the saved one before the
//! prompt went is left unprompted: a new `agy` starts the turn over, given the whole prompt with the conversation's
//! history (`full_prompt`). One it moves to after the model worked is kept, its record asking that the next turn be
//! told everything again (`needs_history`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{ChildStdout, Command};
use tokio::sync::{oneshot, watch};

use super::{event, failed, is_limit, Events, HostError, Settings};
use crate::util::{log, py_dumps, text_of};

pub const DEFAULT_MODEL: &str = "gemini-4-neon";
const EFFORTS: &[&str] = &["low", "medium", "high", "xhigh"];
const DEFAULT_EFFORT: &str = "high";
// A test's stuck fake waits out the grace; a second is as good a proof as five.
const STOP_GRACE: Duration = Duration::from_secs(if cfg!(test) { 1 } else { 5 });
/// How long `agy` may go on writing after its result before the turn stops reading it.
const DRAIN: Duration = Duration::from_secs(5);
/// How long `agy` has to exit on its own once its stdin closes after the result, before the turn ends (and a stop
/// signals it).
const EXIT_WAIT: Duration = Duration::from_secs(1);
/// Lines that are not JSON (a banner, a warning) skipped before the stream counts as broken, and how many of the last
/// ones a failure's error keeps (each cut to KEPT_CHARS).
const SKIP_MAX: usize = 100;
const SKIP_KEPT: usize = 5;
const KEPT_CHARS: usize = 300;
/// The most of what `agy` said (stderr and skipped lines) a failure's error carries.
const ERROR_CHARS: usize = 1000;
/// What resuming a thread whose record is gone says: worded for continuity::missing, so the turn moves to a new thread.
const MISSING: &str = "No conversation found for this Antigravity thread on this computer";
/// A turn that must continue its saved conversation, which `agy` did not resume.
const LOST: &str =
    "No conversation found: Antigravity began a new conversation in place of the saved one";
/// A turn that must continue its saved conversation, which `agy` never named before it would have read the prompt.
const UNCONFIRMED: &str = "No conversation found: Antigravity did not say it resumed the saved conversation, so this turn was not sent";
/// How long a resumed turn's prompt waits for `agy` to name its conversation (`init`, which agy 1.5.15 sends before it
/// reads its input). A turn that must continue it is never sent without it; any other is sent after the wait.
const INIT_WAIT: Duration = Duration::from_secs(if cfg!(test) { 2 } else { 60 });

/// `agy`'s argv: model `<model>-<effort>`, effort high unless set, permissions skipped, the saved conversation if any.
pub fn argv(
    cmd: &[String],
    settings: &Settings,
    effort: Option<&str>,
    provider: Option<&str>,
) -> Result<Vec<String>, HostError> {
    let model = settings
        .model
        .clone()
        .filter(|m| !m.is_empty())
        .unwrap_or_else(|| DEFAULT_MODEL.into());
    let effort = effort
        .filter(|e| !e.is_empty())
        .or(settings.effort.as_deref().filter(|e| !e.is_empty()))
        .unwrap_or(DEFAULT_EFFORT);
    if !EFFORTS.contains(&effort) {
        return Err(failed("Unsupported Antigravity effort"));
    }
    // A model id that already names an effort (`gemini-4-neon-low`) takes this turn's in its place.
    let base = EFFORTS
        .iter()
        .find_map(|e| model.strip_suffix(&format!("-{e}")))
        .unwrap_or(&model);
    let mut argv = cmd.to_vec();
    for a in [
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
    ] {
        argv.push(a.into());
    }
    argv.extend([
        format!("{base}-{effort}"),
        "--effort".into(),
        effort.into(),
        "--dangerously-skip-permissions".into(),
    ]);
    if let Some(p) = provider {
        argv.extend(["--conversation".into(), p.into()]);
    }
    Ok(argv)
}

/// Python's `str(uuid.UUID(text))`: the canonical lower-case hyphenated form, or None when it is no uuid.
fn canonical(text: &str) -> Option<String> {
    uuid::Uuid::parse_str(text.trim())
        .ok()
        .map(|u| u.hyphenated().to_string())
}

/// Python's `int(value or 0)` for a usage count.
fn count(v: &Value) -> i64 {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => *b as i64,
        _ => 0,
    }
}

/// The last `n` characters of `text`.
fn last_chars(text: &str, n: usize) -> String {
    let skip = text.chars().count().saturating_sub(n);
    text.chars().skip(skip).collect()
}

/// A running `agy`, owned by the shared process owner (super::own_with_before_reap): its exit code once reaped.
#[derive(Clone)]
struct Proc {
    stopper: super::Stopper,
    exited: watch::Receiver<Option<i32>>,
}

impl Proc {
    /// SIGTERM to the group, SIGKILL after STOP_GRACE, then until it has gone: whatever it was given (its home, the
    /// turn's folder) is not removed while it runs.
    async fn reap(&self) {
        let mut exited = self.exited.clone();
        if exited.borrow().is_none() {
            self.stopper.terminate(STOP_GRACE);
        }
        let _ = exited.wait_for(|e| e.is_some()).await;
    }
}

/// What the turn's reader holds of its process: stdout, stderr, and the stdin it closes once the result is in.
struct Running {
    stdout: Lines<BufReader<ChildStdout>>,
    stderr: crate::run_diagnostics::Stderr,
    /// Sends the held prompt (a resumed turn's, once `agy` names the saved conversation); dropped unsent, it is never
    /// written.
    release: Option<oneshot::Sender<()>>,
    close: Option<oneshot::Sender<()>>,
    exited: watch::Receiver<Option<i32>>,
}

/// One process's stream as the reader sees it.
#[derive(Default)]
struct Stream {
    /// The turn's streamed text as it was sent, and with a paragraph between its messages (a tool call ends one).
    streamed: (String, String),
    new_message: bool,
    /// Per-step usage emitted by `step_update`, keyed by `step_index`.
    step_usage: BTreeMap<i64, Value>,
    /// A step other than the prompt's own (`user_input`) has run: the model worked in this conversation.
    worked: bool,
    /// `agy` named its conversation (`init`) and the turn may go on: its held prompt is sent.
    confirmed: bool,
    /// Another conversation than the saved one that an event named: kept only once the model works in it or the turn
    /// succeeds, so a refusal before any work leaves the saved one as it was.
    staged: Option<String>,
    skipped: Vec<String>,
    skipped_count: usize,
}

/// How a process's stream ended.
enum End {
    Result(Value),
    /// No result: the stream closed, or broke (why).
    Closed(Option<String>),
    /// `agy` did not continue the saved conversation on a turn that must continue it: why.
    Mismatch(&'static str),
    /// `agy` began another conversation before the held prompt went: the turn starts over in a new one with its whole
    /// prompt (`full_prompt`), so this turn's part alone never goes into an empty conversation.
    Replaced,
}

struct Inner {
    up: bool,
    thread: Option<String>,
    settings: Option<Settings>,
    provider: Option<String>,
    /// The saved conversation was not the one `agy` ran: it began a new one for the thread.
    restored: bool,
    /// The thread's conversation lacks the conversation's history (its record's `needs_history`).
    needs_history: bool,
    /// `needs_history` was set during this turn: a turn told everything does not clear it then.
    flagged: bool,
    /// What a new conversation begun in place of the saved one is given instead of the turn's prompt.
    full_prompt: Option<String>,
    turn: Option<String>,
    proc: Option<Proc>,
    reader: Option<tokio::task::JoinHandle<()>>,
    tx: Events,
}

struct Shared {
    allocation: super::Allocation,
    home: PathBuf,
    cmd: Vec<String>,
    inner: Mutex<Inner>,
}

#[derive(Clone)]
pub struct AntigravityHost(Arc<Shared>);

impl AntigravityHost {
    pub fn new(home: &Path, cmd: Vec<String>, tx: Events) -> AntigravityHost {
        AntigravityHost(Arc::new(Shared {
            allocation: super::Allocation::default(),
            home: home.to_path_buf(),
            cmd,
            inner: Mutex::new(Inner {
                up: false,
                thread: None,
                settings: None,
                provider: None,
                restored: false,
                needs_history: false,
                flagged: false,
                full_prompt: None,
                turn: None,
                proc: None,
                reader: None,
                tx,
            }),
        }))
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn emit(&self, ev: Value) {
        let _ = self.inner().tx.send(ev);
    }

    pub fn start(&self) -> Result<(), HostError> {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(&self.0.home)
            .map_err(|e| failed(format!("could not make the Antigravity home: {e}")))?;
        if !std::fs::symlink_metadata(&self.0.home)
            .map_err(|e| failed(e.to_string()))?
            .is_dir()
        {
            return Err(failed("Antigravity home must not be a symbolic link"));
        }
        // Only the runner reads the conversation records; agy is never given this folder.
        std::fs::set_permissions(&self.0.home, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| failed(e.to_string()))?;
        self.inner().up = true;
        Ok(())
    }

    pub fn track_allocation(&self, turn: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        self.0.allocation.track(turn);
    }

    /// Whether this host takes turns: each starts its own process.
    pub fn alive(&self) -> bool {
        self.inner().up
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
        inner.restored = false;
        inner.needs_history = false;
        inner.full_prompt = None;
        id
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
        let missing = || failed(MISSING);
        let path = self.path(thread_id).ok_or_else(missing)?;
        let record: Value =
            serde_json::from_str(&std::fs::read_to_string(path).map_err(|_| missing())?)
                .map_err(|_| missing())?;
        let provider = record["conversation_id"]
            .as_str()
            .and_then(canonical)
            .ok_or_else(missing)?;
        let mut inner = self.inner();
        inner.provider = Some(provider);
        inner.thread = Some(thread_id.into());
        inner.settings = Some(settings.clone());
        inner.restored = false;
        inner.needs_history = record["needs_history"] == true;
        inner.full_prompt = None;
        Ok(thread_id.into())
    }

    /// Whether `thread_id`'s conversation lacks the conversation's history (hosts::Host::needs_history).
    pub fn needs_history(&self, thread_id: &str) -> bool {
        let inner = self.inner();
        inner.needs_history && inner.thread.as_deref() == Some(thread_id)
    }

    /// A turn that told `thread_id` everything completed: its record stops asking for the history, unless agy moved it
    /// again during that turn.
    pub fn history_told(&self, thread_id: &str) {
        let mut inner = self.inner();
        if inner.thread.as_deref() != Some(thread_id) || !inner.needs_history || inner.flagged {
            return;
        }
        if let Some(cid) = inner.provider.clone() {
            if let Err(e) = self.save(&mut inner, &cid, false) {
                log(&format!("Antigravity: the conversation record was not updated: {e}"));
            }
        }
    }

    pub fn full_prompt(&self, thread_id: &str, text: Option<String>) {
        let mut inner = self.inner();
        if inner.thread.as_deref() == Some(thread_id) {
            inner.full_prompt = text;
        }
    }

    /// Whether `agy` began a new conversation for `thread_id` in place of its saved one.
    pub fn restored(&self, thread_id: &str) -> bool {
        let inner = self.inner();
        inner.restored && inner.thread.as_deref() == Some(thread_id)
    }

    /// Save `cid` as the thread's conversation, and whether it lacks the conversation's history: one write, so the id
    /// is never kept without what the next turn must be told.
    fn save(&self, inner: &mut Inner, cid: &str, needs_history: bool) -> Result<(), String> {
        let path = inner
            .thread
            .as_deref()
            .and_then(|t| self.path(t))
            .ok_or("not a thread id")?;
        let tmp = path.with_extension("tmp");
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(|e| e.to_string())?;
        let record = if needs_history {
            json!({"conversation_id": cid, "needs_history": true})
        } else {
            json!({"conversation_id": cid})
        };
        f.write_all(py_dumps(&record).as_bytes())
            .map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
        inner.provider = Some(cid.into());
        inner.needs_history = needs_history;
        inner.flagged |= needs_history;
        Ok(())
    }

    /// A conversation `agy` named, on any event. The first one a new thread hears of is saved. Another than the saved
    /// one ends a turn that must continue it (`Mismatch`); on any other it is staged (`commit`).
    fn named(
        &self,
        inner: &mut Inner,
        stream: &mut Stream,
        cid: &str,
        must_continue: bool,
    ) -> Result<Option<End>, String> {
        let cid = canonical(cid).ok_or("not a conversation id")?;
        match inner.provider.clone() {
            None => self.save(inner, &cid, false)?,
            Some(saved) if saved == cid => {}
            Some(_) if must_continue => return Ok(Some(End::Mismatch(LOST))),
            Some(_) => stream.staged = Some(cid),
        }
        Ok(None)
    }

    /// A staged conversation, once the model worked in it or the turn succeeded: kept for the thread, which reads
    /// restored, with its record saying it lacks the history (it holds this turn only), so the next turn is told
    /// everything again. Nothing is started again, so no work runs twice.
    fn commit(&self, inner: &mut Inner, stream: &mut Stream) -> Result<(), String> {
        if let Some(cid) = stream.staged.take() {
            inner.restored = true;
            self.save(inner, &cid, true)?;
        }
        Ok(())
    }

    fn command(
        &self,
        settings: &Settings,
        effort: Option<&str>,
        provider: Option<&str>,
    ) -> Result<Command, HostError> {
        let argv = argv(&self.0.cmd, settings, effort, provider)?;
        let mut command = super::harness_command(None, &argv[0]);
        command
            .args(&argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .current_dir(&settings.cwd);
        if !settings.env.is_empty() {
            command.env_clear().envs(&settings.env);
        }
        command.envs(crate::harness_tools::env_for("antigravity"));
        // Its own process group, so a stop reaches the tools it started too.
        super::new_group(&mut command);
        Ok(command)
    }

    /// Start `agy` on `text`: its stdin gets the one user line and stays open until the reader closes it.
    fn spawn(
        &self,
        settings: &Settings,
        effort: Option<&str>,
        provider: Option<&str>,
        text: &str,
        hold: bool,
    ) -> Result<(Proc, Running), HostError> {
        let mut child = self
            .command(settings, effort, provider)?
            .spawn()
            .map_err(|e| failed(format!("could not start agy: {e}")))?;
        self.0.allocation.mark();
        let host = self.clone();
        let stderr = crate::run_diagnostics::Stderr::capture_with(
            &mut child,
            move |value| {
                let inner = host.inner();
                if inner.turn.is_some() {
                    let _ = inner.tx.send(value);
                }
            },
            &settings.env,
        );
        log(&format!(
            "antigravity: agy started pid {}",
            child.id().map_or("?".into(), |p| p.to_string())
        ));
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| failed("agy has no stdout"))?;
        let mut stdin = child.stdin.take();
        let line = py_dumps(&json!({"event": "user", "message": {"content": text}})) + "\n";
        let (close, closed) = oneshot::channel::<()>();
        let (release, released) = oneshot::channel::<()>();
        tokio::spawn(async move {
            // A held prompt goes only once the reader releases it; never when it is dropped unsent.
            if hold && released.await.is_err() {
                return;
            }
            if let Some(s) = stdin.as_mut() {
                let _ = s.write_all(line.as_bytes()).await;
                let _ = s.flush().await;
            }
            let _ = closed.await;
        });
        // Whatever is left of its group when agy exits, however it exits, goes with it: a tool it started never outlives
        // the turn. Sent before agy is reaped, so the group's number is still its own.
        let group = child.id().map(|p| p as i32);
        let sweep = move || {
            if let Some(g) = group {
                // SAFETY: agy leads group `g` (new_group) and is not reaped yet.
                unsafe { libc::kill(-g, libc::SIGKILL) };
            }
        };
        let (stopper, status) = super::own_with_before_reap(child, Some(sweep));
        let (done, exited) = watch::channel(None);
        tokio::spawn(async move {
            let code = match status.await {
                Ok(status) => super::return_code(&status),
                Err(_) => -1,
            };
            let _ = done.send(Some(code));
        });
        Ok((
            Proc {
                stopper,
                exited: exited.clone(),
            },
            Running {
                stdout: BufReader::new(stdout).lines(),
                stderr,
                release: hold.then_some(release),
                close: Some(close),
                exited,
            },
        ))
    }

    pub fn start_turn(
        &self,
        thread_id: &str,
        text: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        let turn = uuid::Uuid::new_v4().to_string();
        let mut inner = self.inner();
        if !inner.up || inner.thread.as_deref() != Some(thread_id) || inner.turn.is_some() {
            return Err(failed(
                "Antigravity is unavailable or already running a turn",
            ));
        }
        let settings = inner.settings.clone().unwrap_or_default();
        inner.flagged = false;
        // A resumed turn's prompt waits until agy names the conversation it resumed.
        let provider = inner.provider.clone();
        let (proc, running) = self.spawn(
            &settings,
            effort,
            provider.as_deref(),
            text,
            provider.is_some(),
        )?;
        inner.proc = Some(proc);
        inner.turn = Some(turn.clone());
        let host = self.clone();
        let (tid, id, effort) = (
            thread_id.to_string(),
            turn.clone(),
            effort.map(String::from),
        );
        inner.reader = Some(tokio::spawn(async move {
            host.run(&tid, &id, effort, running).await
        }));
        let _ = inner.tx.send(event(
            "status",
            Some(thread_id),
            Some(&turn),
            json!({"state": "active"}),
        ));
        Ok(turn)
    }

    /// The turn's one reader: it alone says how the turn ended.
    async fn run(&self, tid: &str, turn: &str, effort: Option<String>, mut running: Running) {
        let mut stream = Stream::default();
        loop {
            let end = self.read(tid, turn, &mut stream, &mut running).await;
            // A prompt still held is never sent, and stdin closes, whatever the end: agy takes that as the end of its
            // input and exits.
            drop(running.release.take());
            if let Some(close) = running.close.take() {
                let _ = close.send(());
            }
            let up = self.inner().up;
            match end {
                End::Result(result) => {
                    if up {
                        let _ = tokio::time::timeout(
                            EXIT_WAIT,
                            running.exited.wait_for(|e| e.is_some()),
                        )
                        .await;
                    }
                    running.stderr.drained().await;
                    self.finish(tid, turn, &result, &stream, &running.stderr.tail());
                    let _ = tokio::time::timeout(DRAIN, async {
                        while let Ok(Some(_)) = running.stdout.next_line().await {}
                    })
                    .await;
                    return;
                }
                End::Closed(why) => {
                    if let Some(why) = &why {
                        log(&format!("Antigravity stream failed: {why}"));
                    }
                    // A stopped turn says nothing more, so nothing waits for its code.
                    let code = if up {
                        tokio::time::timeout(STOP_GRACE, running.exited.wait_for(|e| e.is_some()))
                            .await
                            .ok()
                            .and_then(|e| e.ok().and_then(|e| *e))
                    } else {
                        None
                    };
                    running.stderr.drained().await;
                    self.fail_closed(
                        tid,
                        turn,
                        code,
                        why.as_deref(),
                        &running.stderr.tail(),
                        &stream,
                    );
                    return;
                }
                End::Mismatch(error) => {
                    // Kept on the host while it goes, so a stop meanwhile waits for it too. The saved conversation
                    // stays as it was: a continuation must not run on another one.
                    let proc = self.inner().proc.clone();
                    if let Some(p) = proc {
                        p.reap().await;
                    }
                    self.end(
                        tid,
                        turn,
                        event(
                            "turn_failed",
                            Some(tid),
                            Some(turn),
                            json!({"error": error, "limit": false}),
                        ),
                    );
                    return;
                }
                End::Replaced => {
                    // Nothing went to the conversation agy began: it exits on its closed input having done nothing.
                    // Kept on the host while it goes, so a stop meanwhile waits for it too.
                    let _ =
                        tokio::time::timeout(EXIT_WAIT, running.exited.wait_for(|e| e.is_some()))
                            .await;
                    let proc = self.inner().proc.clone();
                    if let Some(p) = proc {
                        p.reap().await;
                    }
                    // One new start, with no saved conversation and the whole prompt (the history included), begun
                    // while the host holds its lock: a stop either takes this process or finds none started.
                    let started = {
                        let mut inner = self.inner();
                        if !inner.up || inner.turn.as_deref() != Some(turn) {
                            return;
                        }
                        let text = inner.full_prompt.take().unwrap_or_default();
                        inner.provider = None;
                        inner.restored = true;
                        let settings = inner.settings.clone().unwrap_or_default();
                        match self.spawn(&settings, effort.as_deref(), None, &text, false) {
                            Ok((proc, again)) => {
                                inner.proc = Some(proc);
                                Ok(again)
                            }
                            Err(e) => Err(e.to_string()),
                        }
                    };
                    match started {
                        Ok(again) => {
                            self.emit(event(
                                "diagnostic",
                                Some(tid),
                                Some(turn),
                                json!({"text": super::claude::RESTORING}),
                            ));
                            running = again;
                            stream = Stream::default();
                        }
                        Err(error) => {
                            self.end(
                                tid,
                                turn,
                                event(
                                    "turn_failed",
                                    Some(tid),
                                    Some(turn),
                                    json!({"error": error, "limit": is_limit(&error)}),
                                ),
                            );
                            return;
                        }
                    }
                }
            }
        }
    }

    /// Read one process's stream to its result or its end.
    async fn read(&self, tid: &str, turn: &str, stream: &mut Stream, running: &mut Running) -> End {
        let settings = self.inner().settings.clone().unwrap_or_default();
        let scrub = crate::redact::Redactor::new(&crate::redact::env_secrets(&settings.env));
        // While its prompt is held, agy has INIT_WAIT in all to name its conversation, whatever else it prints.
        let init_by = tokio::time::Instant::now() + INIT_WAIT;
        loop {
            let next = if running.release.is_some() {
                match tokio::time::timeout_at(init_by, running.stdout.next_line()).await {
                    Ok(next) => next,
                    Err(_) if settings.require_existing_session => {
                        return End::Mismatch(UNCONFIRMED)
                    }
                    Err(_) => {
                        if let Some(release) = running.release.take() {
                            let _ = release.send(());
                        }
                        continue;
                    }
                }
            } else {
                running.stdout.next_line().await
            };
            let line = match next {
                Ok(Some(l)) => l,
                Ok(None) => return End::Closed(None),
                Err(e) => return End::Closed(Some(e.to_string())),
            };
            if line.trim().is_empty() {
                continue;
            }
            let ev: Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(_) => {
                    // A banner or warning: skipped, and the last few kept, redacted, for a failure's error.
                    stream.skipped_count += 1;
                    if stream.skipped_count > SKIP_MAX {
                        return End::Closed(Some("too many lines that are not JSON".into()));
                    }
                    let kept = crate::run_diagnostics::safe(&line, &scrub);
                    stream
                        .skipped
                        .push(kept.trim().chars().take(KEPT_CHARS).collect());
                    if stream.skipped.len() > SKIP_KEPT {
                        stream.skipped.remove(0);
                    }
                    continue;
                }
            };
            let mut inner = self.inner();
            if !inner.up {
                return End::Closed(None);
            }
            match self.on_event(&mut inner, tid, turn, stream, &ev) {
                Ok(None) => {}
                Ok(Some(end)) => return end,
                Err(e) => return End::Closed(Some(e)),
            }
            if stream.confirmed {
                if let Some(release) = running.release.take() {
                    let _ = release.send(());
                }
            }
        }
    }

    /// One stdout event; Err ends the stream.
    fn on_event(
        &self,
        inner: &mut Inner,
        tid: &str,
        turn: &str,
        stream: &mut Stream,
        ev: &Value,
    ) -> Result<Option<End>, String> {
        // A turn that must continue its saved conversation never runs in another (checked on every id agy names).
        let must_continue = inner
            .settings
            .as_ref()
            .is_some_and(|s| s.require_existing_session);
        if ev["event"] == "init" {
            let cid = Some(&ev["conversation_id"])
                .filter(|c| crate::util::truthy(Some(c)))
                .unwrap_or(&ev["init"]["conversation_id"]);
            if crate::util::truthy(Some(cid)) {
                // A continuation is not sent at all into another conversation.
                if let Some(end) = self.named(inner, stream, &text_of(cid), must_continue)? {
                    return Ok(Some(end));
                }
                // agy began another before the held prompt went: with the whole prompt to give a new one, the turn
                // starts over there rather than send this turn's part alone into an empty conversation.
                if stream.staged.is_some() && !stream.confirmed && inner.full_prompt.is_some() {
                    return Ok(Some(End::Replaced));
                }
            } else if must_continue {
                return Ok(Some(End::Mismatch(UNCONFIRMED)));
            }
            stream.confirmed = true;
        }
        let step = ev
            .get("step_update")
            .filter(|s| crate::util::truthy(Some(s)))
            .cloned()
            .unwrap_or(json!({}));
        // Model work: any step but the echo of the prompt (`user_input`), and not a bare conversation id.
        stream.worked |= step["step_type"]
            .as_str()
            .is_some_and(|t| !t.is_empty() && t != "user_input");
        if crate::util::truthy(step.get("conversation_id")) {
            if let Some(end) =
                self.named(inner, stream, &text_of(&step["conversation_id"]), must_continue)?
            {
                return Ok(Some(end));
            }
        }
        if stream.worked {
            self.commit(inner, stream)?;
        }
        let (tid, turn) = (Some(tid), Some(turn));
        if let Some(usage) = step.get("usage").filter(|u| u.is_object()) {
            let idx = step
                .get("step_index")
                .and_then(Value::as_i64)
                .unwrap_or_else(|| stream.step_usage.len() as i64);
            stream.step_usage.insert(idx, usage.clone());
        }
        if step["step_type"] == "agent_response" && crate::util::truthy(step.get("text_delta")) {
            let text = text_of(&step["text_delta"]);
            stream.streamed.0.push_str(&text);
            let piece = super::add_piece(
                &mut stream.streamed.1,
                std::mem::take(&mut stream.new_message),
                &text,
            );
            let _ = inner.tx.send(event(
                "delta",
                tid,
                turn,
                json!({"text": piece, "delta_kind": "text"}),
            ));
        }
        if step["step_type"] == "tool" {
            stream.new_message = true;
            let tool = step.get("tool_name").cloned().unwrap_or(json!("tool"));
            let _ = inner.tx.send(event(
                "tool",
                tid,
                turn,
                json!({"tool": tool, "state": step.get("state").cloned().unwrap_or(Value::Null)}),
            ));
        }
        if ev["event"] != "result" {
            return Ok(None);
        }
        let result = ev
            .get("result")
            .filter(|r| r.is_object())
            .ok_or("KeyError: 'result'")?;
        // A refusal before any work names no conversation the thread should move to: the saved one stays.
        if result["status"] == "SUCCESS" || stream.worked {
            if crate::util::truthy(result.get("conversation_id")) {
                if let Some(end) =
                    self.named(inner, stream, &text_of(&result["conversation_id"]), must_continue)?
                {
                    return Ok(Some(end));
                }
            }
            self.commit(inner, stream)?;
        }
        Ok(Some(End::Result(result.clone())))
    }

    /// The turn's events from its result.
    fn finish(&self, tid: &str, turn: &str, result: &Value, stream: &Stream, stderr: &str) {
        // A turn stopped (or replaced) meanwhile says nothing more: no usage or answer after the stop.
        {
            let inner = self.inner();
            if !inner.up || inner.turn.as_deref() != Some(turn) {
                return;
            }
        }
        let usage = result
            .get("usage")
            .filter(|u| u.is_object())
            .cloned()
            .unwrap_or(json!({}));
        // agy reports per-step usage on step_update and conversation-cumulative usage on result (across --conversation
        // resumes too), so this turn's steps are summed; an older build without steps reports this process's total.
        let spent = |k: &str| {
            if stream.step_usage.is_empty() {
                count(&usage[k]).max(0)
            } else {
                stream
                    .step_usage
                    .values()
                    .map(|u| count(&u[k]))
                    .sum::<i64>()
                    .max(0)
            }
        };
        let (i, o, c) = (
            spent("input_tokens"),
            spent("output_tokens"),
            spent("cache_read_tokens"),
        );
        let (tid, turn) = (Some(tid), Some(turn));
        // A refusal before any model call spent nothing, and says so by sending no usage: the hub may queue it again.
        if i + o + c > 0 {
            self.emit(event(
                "tokens",
                tid,
                turn,
                json!({"input": i, "output": o, "cached": c, "total": i + o, "usage": {"input": i, "cached": c, "output": o}}),
            ));
        }
        let outcome = if result["status"] == "SUCCESS" {
            let mut text = result["response"]
                .as_str()
                .unwrap_or_default()
                .trim()
                .to_string();
            // Antigravity's answer runs the turn's messages together with nothing between them: when it is just the
            // streamed text, each message keeps its own paragraph.
            if !text.is_empty() && text == stream.streamed.0.trim() {
                text = stream.streamed.1.trim().to_string();
            }
            self.emit(event(
                "message",
                tid,
                turn,
                json!({"text": text, "final": true}),
            ));
            event("turn_completed", tid, turn, json!({"status": "completed"}))
        } else {
            let error = [&result["error"], &result["status"]]
                .into_iter()
                .find(|v| crate::util::truthy(Some(v)))
                .map(text_of)
                .unwrap_or_else(|| "Antigravity failed".into());
            // What it printed (stderr, redacted and bounded: run_diagnostics::Stderr) may name the reason a plain
            // result does not, a sign-in or a quota: it is read with it.
            let said = stderr.trim();
            let error = if said.is_empty() {
                error
            } else {
                format!("{error}: {}", last_chars(said, ERROR_CHARS))
            };
            event(
                "turn_failed",
                tid,
                turn,
                json!({"error": error, "limit": is_limit(&error)}),
            )
        };
        self.end(tid.unwrap_or_default(), turn.unwrap_or_default(), outcome);
    }

    /// A process that ended with no result: one failure carrying what it said, so a refusal it printed (on stderr or as
    /// a plain line) is read as one.
    fn fail_closed(
        &self,
        tid: &str,
        turn: &str,
        code: Option<i32>,
        why: Option<&str>,
        stderr: &str,
        stream: &Stream,
    ) {
        let reason = match (why, code) {
            (Some(why), _) => format!("Antigravity stream failed: {why}"),
            (None, Some(c)) => crate::run_diagnostics::process_reason(c, stderr),
            (None, None) => "Antigravity stream ended before a result".into(),
        };
        let said: Vec<&str> = stream
            .skipped
            .iter()
            .map(String::as_str)
            .chain(stderr.lines())
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .collect();
        let error = if said.is_empty() {
            reason
        } else {
            format!("{reason}: {}", last_chars(&said.join("\n"), ERROR_CHARS))
        };
        let mut failure = event(
            "turn_failed",
            Some(tid),
            Some(turn),
            json!({"error": error, "limit": is_limit(&error)}),
        );
        if let Some(c) = code.filter(|c| *c != 0) {
            failure["exit_code"] = json!(c);
            failure["process_exit"] = json!(true);
        }
        self.end(tid, turn, failure);
    }

    /// The turn's last events, unless it was stopped: a stop says nothing more.
    fn end(&self, tid: &str, turn: &str, outcome: Value) {
        let mut inner = self.inner();
        if !inner.up || inner.turn.as_deref() != Some(turn) {
            return;
        }
        let _ = inner.tx.send(outcome);
        let _ = inner.tx.send(event(
            "status",
            Some(tid),
            Some(turn),
            json!({"state": "idle"}),
        ));
        inner.turn = None;
    }

    pub fn steer(&self) -> Result<(), HostError> {
        Err(failed("Antigravity steering is not enabled"))
    }

    /// Interrupting a turn stops its process: the next turn starts a new one on the saved conversation.
    pub async fn interrupt(&self) {
        self.stop().await;
    }

    /// Stop the turn's process (crate::hosts Proc::reap) and its reader.
    pub async fn stop(&self) {
        let (proc, reader) = {
            let mut inner = self.inner();
            inner.up = false;
            inner.turn = None;
            (inner.proc.take(), inner.reader.take())
        };
        if let Some(p) = proc {
            p.reap().await;
        }
        if let Some(r) = reader {
            let _ = tokio::time::timeout(Duration::from_secs(2), r).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    const SAVED: &str = "6f9619ff-8b86-d011-b42d-00c04fc964ff";
    const OTHER: &str = "0b2e6f1c-3a1d-4f43-9a51-6c2f2f7e9d10";

    #[tokio::test]
    async fn turn_launch_keeps_the_bot_identity_and_a_supervisor_only_record() {
        if !super::super::isolationtests::child("hosts::antigravity::tests::turn_launch_keeps_the_bot_identity_and_a_supervisor_only_record") { return; }
        let f = super::super::isolationtests::Fixture::new();
        let mut settings = f.settings();
        let host = AntigravityHost::new(
            &f.root.join("agy/acme"),
            f.argv(),
            mpsc::unbounded_channel().0,
        );
        super::super::isolationtests::prefix(
            &host.command(&settings, None, None).unwrap(),
            &argv(&f.argv(), &settings, None, None).unwrap(),
        );
        if !f.actual() {
            return;
        }
        host.start().unwrap();
        let thread = host.start_thread(&settings);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        let record = host.path(&thread).unwrap();
        settings
            .env
            .insert("TEST_PRIVATE".into(), record.display().to_string());
        f.run(
            host.command(&settings, None, Some(SAVED)).unwrap(),
            &[],
            &[],
        )
        .await;
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            record.metadata().unwrap().uid(),
            0,
            "conversation record stays supervisor-only"
        );
    }

    /// An `agy` for one turn, which names its conversation (`init`) before it reads its input, as agy 1.5.15 does: it
    /// logs its argv, its pid and the user line it read (`.prompts`), and answers with the fixture (`$FIXTURE`, printed
    /// first, then the fixture's `.stderr` beside it on stderr and exit 1), or with its conversation (`$NEW` when it
    /// begins one) and the prompt it was given. `--conversation` resumes that one unless `$LOSE` names the one it begins
    /// instead; with `$LATE` it names the asked one first and the other only after a step; with `$NOINIT` it names none.
    /// With `$STUCK` it ignores SIGTERM and never answers; with `$ORPHAN` it leaves a tool that ignores SIGTERM in its
    /// group. It exits when stdin closes.
    fn fake_agy(dir: &Path) -> String {
        let path = dir.join("fake-agy");
        crate::util::write_script(
            &path,
            &format!(
                r##"#!/bin/sh
echo "$@" >> "$ARGS_LOG"
echo $$ > "$ARGS_LOG.pid"
if [ -n "$STUCK" ]; then trap '' TERM; while :; do sleep 1; done; fi
if [ -n "$ORPHAN" ]; then (trap '' TERM; exec sleep 30) >/dev/null 2>&1 & echo $! > "$ARGS_LOG.orphan"; fi
if [ -n "$FIXTURE" ]; then
  cat "$FIXTURE"
  IFS= read -r line || exit 0
  printf '%s\n' "$line" >> "$ARGS_LOG.prompts"
  if [ -f "${{FIXTURE%.jsonl}}.stderr" ]; then cat "${{FIXTURE%.jsonl}}.stderr" >&2; exit 1; fi
else
  conv=${{NEW:-{SAVED}}}
  asked=""
  prev=""
  for a in "$@"; do [ "$prev" = --conversation ] && asked="$a" && conv="${{LOSE:-$a}}"; prev="$a"; done
  if [ -n "$LATE" ]; then
    echo "{{\"event\": \"init\", \"conversation_id\": \"${{asked:-$conv}}\"}}"
  elif [ -z "$NOINIT" ]; then
    echo "{{\"event\": \"init\", \"conversation_id\": \"$conv\"}}"
  fi
  IFS= read -r line || exit 0
  printf '%s\n' "$line" >> "$ARGS_LOG.prompts"
  text=$(printf '%s' "$line" | sed 's/.*"content": "\(.*\)"}}}}$/\1/')
  if [ -n "$LATE" ]; then
    echo '{{"event": "step_update", "step_update": {{"step_type": "agent_response", "text_delta": "x"}}}}'
    echo "{{\"event\": \"step_update\", \"step_update\": {{\"conversation_id\": \"$conv\"}}}}"
  fi
  echo "{{\"event\": \"result\", \"result\": {{\"status\": \"SUCCESS\", \"response\": \"$text\", \"usage\": {{\"input_tokens\": 10, \"output_tokens\": 2}}}}}}"
fi
read -r _
"##
            ),
        );
        path.display().to_string()
    }

    async fn turn_events(rx: &mut mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
        let mut got = vec![];
        while let Ok(Some(mut ev)) = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await
        {
            if ev["kind"] == "diagnostic" && ev.get("stderr_tail").is_some() {
                continue;
            }
            let end = ev["kind"] == "status" && ev["state"] == "idle";
            ev.as_object_mut()
                .unwrap()
                .retain(|k, _| k != "thread_id" && k != "turn_id");
            got.push(ev);
            if end {
                break;
            }
        }
        got
    }

    struct Rig {
        dir: PathBuf,
        cmd: String,
    }

    impl Rig {
        fn new() -> Rig {
            let dir =
                std::env::temp_dir().join(format!("fake-agy-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(&dir).unwrap();
            let cmd = fake_agy(&dir);
            Rig { dir, cmd }
        }

        fn settings(&self, vars: &[(&str, String)]) -> Settings {
            let mut s = Settings {
                cwd: self.dir.display().to_string(),
                model: Some("gemini-4-hydrogen".into()),
                ..Default::default()
            };
            s.env.insert("PATH".into(), "/usr/bin:/bin".into());
            s.env.insert(
                "ARGS_LOG".into(),
                self.dir.join("args.log").display().to_string(),
            );
            for (k, v) in vars {
                s.env.insert(k.to_string(), v.clone());
            }
            s
        }

        fn host(&self) -> (AntigravityHost, mpsc::UnboundedReceiver<Value>) {
            let (tx, rx) = mpsc::unbounded_channel();
            let host = AntigravityHost::new(&self.dir.join("agy/acme"), vec![self.cmd.clone()], tx);
            host.start().unwrap();
            (host, rx)
        }

        fn runs(&self) -> Vec<String> {
            std::fs::read_to_string(self.dir.join("args.log"))
                .unwrap_or_default()
                .lines()
                .map(String::from)
                .collect()
        }
    }

    impl Drop for Rig {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    #[tokio::test]
    async fn every_antigravity_fixture_gives_its_events() {
        let rig = Rig::new();
        for (path, expected) in crate::hosts::fixtures::cases("antigravity") {
            let (host, mut rx) = rig.host();
            let s = rig.settings(&[("FIXTURE", path.display().to_string())]);
            let thread = host.start_thread(&s);
            host.start_turn(&thread, "hello", None).unwrap();
            assert_eq!(turn_events(&mut rx).await, expected, "{}", path.display());
            host.stop().await;
        }
    }

    #[tokio::test]
    async fn each_turn_is_its_own_process_and_resumes_the_conversation_on_any_effort() {
        let rig = Rig::new();
        let s = rig.settings(&[]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "one", Some("medium")).unwrap();
        let first = turn_events(&mut rx).await;
        assert_eq!(
            first[2],
            json!({"kind": "message", "text": "one", "final": true})
        );
        host.stop().await;
        // The next turn, on another effort and another host: the saved conversation goes on.
        let (cold, mut rx) = rig.host();
        assert!(crate::continuity::missing(
            &cold
                .resume_thread(&uuid::Uuid::new_v4().to_string(), &s)
                .unwrap_err()
                .to_string()
        ));
        cold.resume_thread(&thread, &s).unwrap();
        cold.start_turn(&thread, "two", Some("xhigh")).unwrap();
        assert_eq!(turn_events(&mut rx).await[2]["text"], "two");
        assert!(!cold.restored(&thread));
        cold.stop().await;
        let runs = rig.runs();
        assert_eq!(runs[0], "--input-format stream-json --output-format stream-json --model gemini-4-hydrogen-medium --effort medium --dangerously-skip-permissions");
        assert_eq!(runs[1], format!("--input-format stream-json --output-format stream-json --model gemini-4-hydrogen-xhigh --effort xhigh --dangerously-skip-permissions --conversation {SAVED}"));
    }

    #[tokio::test]
    async fn a_conversation_agy_did_not_resume_is_kept_and_the_turn_runs_once() {
        // agy names the conversation it began in place of the saved one before it reads the prompt. With no fuller
        // prompt to start again with, the turn runs there once: kept after its work, the thread reads restored, and
        // the record asks for the history on the next turn.
        let rig = Rig::new();
        let s = rig.settings(&[("LOSE", OTHER.into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "short", None).unwrap();
        let events = turn_events(&mut rx).await;
        let message = events.iter().find(|e| e["kind"] == "message").unwrap();
        assert_eq!(message["text"], "short");
        assert!(host.restored(&thread));
        host.stop().await;
        let runs = rig.runs();
        assert_eq!(runs.len(), 1, "never started again");
        assert!(runs[0].ends_with(&format!("--conversation {SAVED}")));
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(record.contains(OTHER) && record.contains("needs_history"), "{record}");
    }

    #[tokio::test]
    async fn a_conversation_agy_began_before_the_prompt_starts_again_with_everything() {
        // The process that named another conversation is closed without the prompt; a new one is given the full one.
        let rig = Rig::new();
        let s = rig.settings(&[("LOSE", OTHER.into()), ("NEW", OTHER.into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.full_prompt(&thread, Some("everything".into()));
        host.start_turn(&thread, "short", None).unwrap();
        let events = turn_events(&mut rx).await;
        let message = events.iter().find(|e| e["kind"] == "message").unwrap();
        assert_eq!(message["text"], "everything");
        assert!(events.iter().any(|e| e["kind"] == "turn_completed"));
        assert!(host.restored(&thread));
        assert!(!host.needs_history(&thread), "it was told everything");
        host.stop().await;
        let runs = rig.runs();
        assert_eq!(runs.len(), 2, "{runs:?}");
        assert!(runs[0].ends_with(&format!("--conversation {SAVED}")));
        assert!(!runs[1].contains("--conversation"), "{}", runs[1]);
        let prompts = std::fs::read_to_string(rig.dir.join("args.log.prompts")).unwrap();
        assert_eq!(prompts.lines().count(), 1, "the first process never had the prompt: {prompts}");
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(record.contains(OTHER) && !record.contains("needs_history"), "{record}");
    }

    #[tokio::test]
    async fn a_refusal_before_any_step_keeps_the_saved_conversation() {
        let rig = Rig::new();
        let dir = rig.dir.join("refusal");
        std::fs::create_dir_all(&dir).unwrap();
        let fixture = dir.join("other.jsonl");
        std::fs::write(
            &fixture,
            // agy names another conversation, then refuses before any work.
            format!("{{\"event\": \"init\", \"conversation_id\": \"{OTHER}\"}}\n{{\"event\": \"result\", \"result\": {{\"status\": \"ERROR\", \"error\": \"no\", \"conversation_id\": \"{OTHER}\"}}}}\n"),
        )
        .unwrap();
        let s = rig.settings(&[("FIXTURE", fixture.display().to_string())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "hi", None).unwrap();
        assert!(turn_events(&mut rx)
            .await
            .iter()
            .any(|e| e["kind"] == "turn_failed"));
        host.stop().await;
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(
            record.contains(SAVED) && !host.restored(&thread),
            "{record}"
        );
    }

    #[tokio::test]
    async fn a_tool_left_in_agys_group_goes_with_it_when_agy_exits_first() {
        let rig = Rig::new();
        let s = rig.settings(&[("ORPHAN", "1".into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).unwrap();
        assert!(turn_events(&mut rx)
            .await
            .iter()
            .any(|e| e["kind"] == "turn_completed"));
        let orphan: i32 = std::fs::read_to_string(rig.dir.join("args.log.orphan"))
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        host.stop().await;
        let gone = (0..50).any(|_| {
            std::thread::sleep(Duration::from_millis(20));
            (unsafe { libc::kill(orphan, 0) }) == -1
        });
        assert!(gone, "the tool agy left ignoring SIGTERM is gone");
    }

    #[tokio::test]
    async fn a_turn_that_must_continue_its_conversation_stops_rather_than_start_another() {
        let rig = Rig::new();
        let mut s = rig.settings(&[("LOSE", OTHER.into())]);
        s.require_existing_session = true;
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "go on", None).unwrap();
        let events = turn_events(&mut rx).await;
        let failed = events.iter().find(|e| e["kind"] == "turn_failed").unwrap();
        assert!(crate::continuity::missing(&text_of(&failed["error"])));
        host.stop().await;
        assert_eq!(rig.runs().len(), 1, "no second start");
        assert!(
            !rig.dir.join("args.log.prompts").exists(),
            "the prompt never went to the other conversation"
        );
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(
            record.contains(SAVED) && !record.contains(OTHER),
            "{record}"
        );
    }

    #[tokio::test]
    async fn a_continuation_agy_never_confirms_is_not_sent() {
        let rig = Rig::new();
        let mut s = rig.settings(&[("NOINIT", "1".into())]);
        s.require_existing_session = true;
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "go on", None).unwrap();
        let events = turn_events(&mut rx).await;
        let failed = events.iter().find(|e| e["kind"] == "turn_failed").unwrap();
        assert_eq!(failed["error"], UNCONFIRMED);
        assert!(crate::continuity::missing(UNCONFIRMED));
        host.stop().await;
        assert!(!rig.dir.join("args.log.prompts").exists(), "never sent");
    }

    #[tokio::test]
    async fn any_other_resumed_turn_is_sent_after_the_wait_when_agy_names_no_conversation() {
        let rig = Rig::new();
        let s = rig.settings(&[("NOINIT", "1".into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "hello", None).unwrap();
        let events = turn_events(&mut rx).await;
        assert!(
            events.iter().any(|e| e["kind"] == "turn_completed"),
            "{events:?}"
        );
        host.stop().await;
    }

    #[tokio::test]
    async fn another_conversation_named_after_a_step_is_kept_and_the_thread_reads_restored() {
        let rig = Rig::new();
        let s = rig.settings(&[("LOSE", OTHER.into()), ("LATE", "1".into())]);
        let (host, mut rx) = rig.host();
        let thread = host.start_thread(&s);
        host.save(&mut host.inner(), SAVED, false).unwrap();
        host.start_turn(&thread, "hi", None).unwrap();
        let events = turn_events(&mut rx).await;
        assert!(events.iter().any(|e| e["kind"] == "turn_completed"));
        assert!(host.restored(&thread));
        assert_eq!(rig.runs().len(), 1, "nothing is replayed after a step");
        assert!(host.needs_history(&thread));
        let record = std::fs::read_to_string(host.path(&thread).unwrap()).unwrap();
        assert!(record.contains(OTHER) && record.contains("needs_history"), "{record}");
        // Told everything in the very turn agy moved it in is not told it in the new conversation: the mark stays.
        host.history_told(&thread);
        assert!(host.needs_history(&thread));
        host.stop().await;
    }

    #[tokio::test]
    async fn stop_waits_for_a_process_that_ignores_sigterm_to_go() {
        let rig = Rig::new();
        let s = rig.settings(&[("STUCK", "1".into())]);
        let (host, _rx) = rig.host();
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hi", None).unwrap();
        let pid_file = rig.dir.join("args.log.pid");
        let pid = loop {
            if let Some(pid) = std::fs::read_to_string(&pid_file)
                .ok()
                .and_then(|p| p.trim().parse::<i32>().ok())
            {
                break pid;
            }
            tokio::task::yield_now().await;
        };
        host.stop().await;
        assert_eq!(
            unsafe { libc::kill(pid, 0) },
            -1,
            "agy is gone once stop returns"
        );
    }

    #[test]
    fn efforts_default_to_high_replace_a_named_one_and_reject_unsupported_levels() {
        let s = Settings {
            effort: Some("max".into()),
            ..Default::default()
        };
        assert!(argv(&["agy".into()], &s, None, None).is_err());
        assert_eq!(
            argv(&["agy".into()], &s, Some("xhigh"), None).unwrap()[6],
            "gemini-4-neon-xhigh"
        );
        let named = Settings {
            model: Some("gemini-4-hydrogen-low".into()),
            ..Default::default()
        };
        let default = argv(&["agy".into()], &named, None, None).unwrap();
        assert_eq!(
            (default[6].as_str(), default[8].as_str()),
            ("gemini-4-hydrogen-high", "high")
        );
    }
}
