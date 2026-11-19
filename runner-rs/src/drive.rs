//! The drive loop (service.py execute's `drive`): drain the host until the turn ends, with native goals
//! (task_goals_v1) worked the way the Python runner works them. A Codex goal continues across turns the app
//! server starts itself; a Claude goal is judged in the stream and the transcript. A goal that keeps going
//! without progress is paused after its tries; a person's goal control mid-run settles the run.

use std::borrow::Cow;
use std::collections::{BTreeMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};
use tokio::sync::mpsc::UnboundedReceiver;
use tokio::sync::Notify;

use crate::execute::{count, Drove, Fail, Flags, Meter};
use crate::goals;
use crate::hosts::{self, Host};
use crate::redact::Redactor;
use crate::runner::Runner;
use crate::util::{mark, text_of, truthy};

/// The event kinds the outbox keeps as rows. A drain's rows are written together, in arrival order, and before any
/// other write to the outbox.
const STREAMED: [&str; 8] = [
    "delta",
    "message",
    "tokens",
    "status",
    "error",
    "tool",
    "diagnostic",
    "turn_failed",
];

/// An event as the outbox keeps it: borrowed when no string in it holds a secret, scrubbed when one does.
fn scrubbed<'a>(redactor: Option<&Arc<Redactor>>, v: &'a Value) -> Cow<'a, Value> {
    redactor.map_or(Cow::Borrowed(v), |r| r.scrub_json(v))
}

/// What one drive step heard from the hub beyond the upload. The step only talks to the hub; what it heard is acted
/// on in the drive loop, as it was when the step ran inline.
#[derive(Default)]
struct Asked {
    /// The chat goal is no longer the one this run started with: a person changed or cleared it.
    controlled: bool,
    /// This attempt's inputs, to be steered into the turn.
    messages: Vec<Value>,
}

type StepFuture<'a> = Pin<Box<dyn Future<Output = Result<Asked, Fail>> + Send + 'a>>;

/// The drive step in flight; never ready when there is none.
async fn step_done(step: &mut Option<StepFuture<'_>>) -> Result<Asked, Fail> {
    match step {
        Some(step) => step.as_mut().await,
        None => std::future::pending().await,
    }
}

/// Goal continuations per (bot, session key) thread, for the runner's life (service.py goal_tries).
pub fn tries() -> &'static goals::Tries {
    static T: std::sync::OnceLock<goals::Tries> = std::sync::OnceLock::new();
    T.get_or_init(Default::default)
}

pub fn from_person(message: &Value) -> bool {
    text_of(&message["from_actor"]).starts_with("human:")
}

/// How long a turn whose lease is lost keeps reading what its harness already said. A model that finished while this
/// computer was asleep has its whole answer waiting in the pipe; one still working is not waited for.
const SALVAGE: Duration = if cfg!(test) {
    Duration::from_millis(500)
} else {
    Duration::from_secs(10)
};

/// One metered stretch of a turn: the primary harness, then a fallback's.
pub struct Segment {
    pub meter: Meter,
    pub config: Value,
    pub profile: Option<String>,
    pub billing: &'static str,
    /// The company subscription the segment's turn signs in with (its id); None on the computer's own login.
    pub subscription: Option<String>,
}

/// The goal side of one attempt, shared by the turn and any fallback.
pub struct GoalRun {
    pub chat_goal: Option<Value>,
    pub controlled: bool,
    pub failure: Option<String>,
    pub paused: Option<String>,
    pub tries_key: (String, String),
}

pub struct Drive<'a> {
    pub aid: &'a str,
    pub host: &'a Host,
    pub thread: &'a str,
    pub effort: Option<&'a str>,
    pub limit: Instant,
    pub limit_name: &'a str,
    pub flags: &'a Flags,
    pub wake: &'a Notify,
    pub redactor: Option<&'a Arc<Redactor>>,
    /// The runtime this stretch runs on (a fallback's after a hop).
    pub runtime: &'a str,
    pub bot: &'a str,
    /// The provider thread's session key (execute::session_key), for commands a turn leaves running.
    pub session: &'a str,
    /// A new thread this stretch started, kept only once its host says the session is up (`thread_confirmed`).
    pub fresh: Option<&'a crate::execute::Fresh>,
}

impl Runner {
    /// Writes the rows a drain gathered, in one transaction and in arrival order, then wakes the outbox poster. A
    /// failed write takes the whole batch with it, and its error ends the drive the way a failed append always has.
    async fn write_rows(
        &self,
        d: &Drive<'_>,
        rows: &mut Vec<(String, String)>,
    ) -> Result<(), String> {
        if rows.is_empty() {
            return Ok(());
        }
        let (aid, batch) = (d.aid.to_string(), std::mem::take(rows));
        self.state
            .off(move |s| s.append_batch(&aid, &batch))
            .await?;
        d.wake.notify_one();
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    // `settle!` applies a step the same way wherever it ends; before the last `break` some of what it sets is not read.
    #[allow(unused_assignments)]
    pub(crate) async fn drive(
        &self,
        d: &Drive<'_>,
        rx: &mut UnboundedReceiver<Value>,
        mut turn: String,
        segment: &mut Segment,
        carried: &mut BTreeMap<String, Value>,
        auth_rejected: &mut Option<(String, String)>,
        goal: &mut GoalRun,
    ) -> Result<Drove, Fail> {
        let (aid, host, thread) = (d.aid, d.host, d.thread);
        self.drain.register(aid);
        if !self.drain.start(aid) {
            return Err(Fail::runtime(crate::drain::WAITING));
        }
        // A host whose native goal keeps running across turns (`goal_runs_across_turns`): a completed turn is not
        // the end of the run, and a clear it reports is checked against the goal.
        let across_turns = crate::harness_tools::caps(d.runtime, "").goal_runs_across_turns;
        let scrub = |t: &str| {
            d.redactor
                .map(|r| r.scrub_text(t).into_owned())
                .unwrap_or_else(|| t.to_string())
        };
        let (mut reply, mut tokens, mut outcome) =
            (String::new(), json!({}), "interrupted".to_string());
        let (mut limited, mut capacity, mut retryable, mut acted, mut complete) =
            (false, false, false, false, false);
        let mut continues = false;
        let mut earlier = String::new();
        let mut earlier_tokens: Option<Value> = None;
        let mut last_flush: Option<Instant> = None;
        let mut first_output = false;
        let mut queue: Vec<Value> = vec![];
        // The outbox rows this drain has gathered, not yet written: (kind, JSON payload) in arrival order.
        let mut rows: Vec<(String, String)> = vec![];
        // Set once the lease is lost: until then the turn's remaining output is read, and nothing more is asked of the hub.
        let mut salvage: Option<Instant> = None;
        let answer = |earlier: &str, reply: &str| {
            [earlier, reply]
                .iter()
                .filter(|t| !t.is_empty())
                .cloned()
                .collect::<Vec<_>>()
                .join("\n\n")
        };
        let spent = |earlier: &Option<Value>, tokens: &Value| match earlier {
            None => tokens.clone(),
            Some(e) => {
                let mut m = Map::new();
                for k in ["input", "output", "total"] {
                    m.insert(k.into(), json!(count(&e[k]) + count(&tokens[k])));
                }
                Value::Object(m)
            }
        };
        let chat_goal = goal.chat_goal.clone();
        let mut goal_running = chat_goal.as_ref().is_some_and(|g| g["status"] == "active");
        let revision = chat_goal
            .as_ref()
            .map_or(Value::Null, |g| g["updated_at"].clone());
        let goal_id = chat_goal.as_ref().map_or(Value::Null, |g| g["id"].clone());
        let mut tried: HashSet<String> = HashSet::new();
        let mut pausing: Option<String> = None;
        let (mut pause_ready, mut pause_at, mut clearing) = (false, Instant::now(), false);
        // The hub is asked for this turn's inputs and goal on a notice for its bot, while the event stream is down
        // (every step, as before), or at the slower recovery check (events.rs TURN_RECOVERY_S). `asked` is the notice
        // count at the last ask, None before the first.
        let mut asked: Option<u64> = None;
        let mut recovery_at = Instant::now();
        // The 1-second step (upload, then the goal and inputs reads) runs beside the harness output, so a slow hub never
        // holds back the turn's text or its end. `step_asks`: it reads more than the upload. A step that read is
        // always heard out before the drive ends: the hub hands an input to this attempt when it is read.
        let mut step: Option<StepFuture<'_>> = None;
        let mut step_asks = false;

        // Count a continuation; past the cap, interrupt the turn so its goal can be cleared.
        macro_rules! stuck {
            ($note:expr, $cap:expr) => {{
                let note: String = $note;
                // Not once the lease is lost: pausing interrupts the turn and clears its goal in a new one, and a
                // turn read out after its lease lapsed starts nothing (Dev 242 R1).
                if salvage.is_none() && pausing.is_none() && !clearing && tries().tried(&goal.tries_key, &note, $cap) {
                    let (n, last) = tries().take(&goal.tries_key).unwrap_or((0, String::new()));
                    let last = last.split_whitespace().collect::<Vec<_>>().join(" ");
                    let shown = if last.chars().count() <= 300 { last.clone() } else { last.chars().take(299).collect::<String>() + "…" };
                    let note = format!("Goal paused after {n} tries without progress{}", if last.is_empty() { String::new() } else { format!(": {shown}") });
                    if goal_running {
                        self.state.append(aid, "goal", &json!({"goal_id": goal_id, "revision": revision, "status": "paused", "note": note}))?;
                    }
                    goal.paused = Some(note);
                    goal_running = false;
                    pausing = Some(turn.clone());
                    pause_at = Instant::now();
                    let _ = host.interrupt(thread, &turn).await;
                }
            }};
        }

        // Act on a finished step, as the loop once did inline: its inputs are steered into the turn now running (one
        // the turn refuses as it ends is carried), and a person's goal control ends the run.
        macro_rules! settle {
            ($done:expr) => {{
                let done: Result<Asked, Fail> = $done;
                let done = match done {
                    Ok(heard) if !heard.messages.is_empty() => self
                        .steer_inputs(aid, host, thread, &turn, carried, &heard.messages)
                        .await
                        .map(|()| heard),
                    done => done,
                };
                last_flush = Some(Instant::now());
                match done {
                    Ok(heard) => {
                        if heard.messages.iter().any(from_person) {
                            tries().take(&goal.tries_key);
                        }
                        // Not once the run's own goal row ended the goal: then the change is this run's.
                        if heard.controlled && goal_running {
                            goal.controlled = true;
                            let _ = host.interrupt(thread, &turn).await;
                            // An explicit goal control settles this run so its queued successor can claim.
                            goal_running = false;
                            complete = true;
                            outcome = "completed".into();
                        }
                    }
                    Err(e) => {
                        recovery_at = Instant::now(); // an ask that failed is tried again at the next step
                        if e.api.as_ref().is_some_and(|a| a.code == "stale_lease") {
                            // Refused before the renewer heard: the same lost lease, read out the same way, from now: a
                            // harness found gone further down this pass is waited on, not given up (Dev 242 R1 delta).
                            d.flags.lost.store(true, Ordering::SeqCst);
                            salvage.get_or_insert_with(|| Instant::now() + SALVAGE);
                        } else if e.api.as_ref().is_none_or(|a| !a.retryable) {
                            return Err(e);
                        }
                    }
                }
            }};
        }
        // A step still out when the drive is about to end. One that only uploads is dropped: the attempt's final flush
        // sends its rows again, and the hub skips what it has. One that read is heard out and acted on first.
        macro_rules! settle_step {
            () => {
                if let Some(s) = step.take() {
                    if step_asks {
                        settle!(s.await);
                    }
                }
            };
        }
        // The same before an early end, whose own reason stands: an error the step met is not the drive's.
        macro_rules! end_step {
            () => {
                if let Some(s) = step.take() {
                    if step_asks {
                        if let Ok(heard) = s.await {
                            if !heard.messages.is_empty() {
                                let _ = self
                                    .steer_inputs(
                                        aid,
                                        host,
                                        thread,
                                        &turn,
                                        carried,
                                        &heard.messages,
                                    )
                                    .await;
                            }
                        }
                    }
                }
            };
        }

        while !complete {
            if d.flags.stopped.load(Ordering::SeqCst) {
                // A quarantine's Stop says why (Dev 573): the turn ends with that reason, never read as a lost computer.
                let reason = d.flags.stop_reason.lock().unwrap().clone();
                if let Some(why) = &reason {
                    let _ = self
                        .state
                        .append(aid, "diagnostic", &json!({"end_reason": why}));
                }
                goal.failure = Some(reason.unwrap_or_else(|| "Stopped by a person".into()));
                end_step!();
                let _ = host.interrupt(thread, &turn).await;
                return Ok(Drove {
                    outcome: "interrupted".into(),
                    reply: answer(&earlier, &reply),
                    tokens: spent(&earlier_tokens, &tokens),
                    limited: false,
                    acted,
                    capacity: false,
                    continues: false,
                    retryable: false,
                    turn,
                    yielded: false,
                });
            }
            if let Some(line) = self.drain.due(aid, Instant::now()) {
                let _ = self.state.append(aid, "status", &json!({"yielded": line}));
                end_step!();
                let _ = host.interrupt(thread, &turn).await;
                return Ok(Drove {
                    outcome: "interrupted".into(),
                    reply: answer(&earlier, &reply),
                    tokens: spent(&earlier_tokens, &tokens),
                    limited: false,
                    acted,
                    capacity: false,
                    continues: false,
                    retryable: false,
                    turn,
                    yielded: true,
                });
            }
            let late = Instant::now() >= d.limit;
            if d.flags.lost.load(Ordering::SeqCst) && salvage.is_none() {
                // The hub let the lease go, often because this computer slept or was paused through it. The model may
                // have finished meanwhile: what it already said is read before the turn is given up (Dev 242 QA).
                salvage = Some(Instant::now() + SALVAGE);
            }
            // A harness that has exited is no proof its output was read: a reader may still be draining the pipe it
            // left, so only the window ends a salvage that has not seen the turn end.
            let salvaged = salvage.is_some_and(|until| Instant::now() >= until);
            if self.stopping() || salvaged || late {
                goal.failure = Some(
                    if late {
                        "Run time limit reached"
                    } else if self.stopping() {
                        "The computer stopped"
                    } else {
                        "The execution lease expired"
                    }
                    .into(),
                );
                end_step!();
                let _ = host.interrupt(thread, &turn).await;
                let why = if late {
                    format!("Timeout: {}", d.limit_name)
                } else if self.stopping() {
                    "Runner stopped during execution".into()
                } else {
                    "Computer lost or heartbeat stopped (execution lease expired)".into()
                };
                let _ = self
                    .state
                    .append(aid, "diagnostic", &json!({"end_reason": why}));
                return Err(Fail::runtime(why));
            }
            while let Ok(ev) = rx.try_recv() {
                queue.push(ev);
            }
            let batch: Vec<Value> = std::mem::take(&mut queue);
            for mut event in batch {
                let event_thread = text_of(&event["thread_id"]);
                if !event_thread.is_empty() && event_thread != thread {
                    continue;
                }
                let kind = text_of(&event["kind"]);
                if kind == "turn_failed" || (kind == "error" && truthy(event.get("host_restart"))) {
                    // A harness killed at its memory limit fails with whatever its own exit looks like; the
                    // turn's scope knows why (crate::turn_limits).
                    if let Some(text) = crate::turn_limits::oom_failure(aid).await {
                        crate::util::log(&format!(
                            "Tico runner: {}: {aid}: {text}",
                            crate::turn_limits::CATEGORY
                        ));
                        let _ = self.state.append(
                            aid,
                            "diagnostic",
                            &json!({"category": crate::turn_limits::CATEGORY, "end_reason": text}),
                        );
                        event["error"] = json!(text);
                        event["limit"] = json!(false);
                    }
                }
                if !STREAMED.contains(&kind.as_str()) {
                    // Whatever else writes to the outbox comes after the rows drained before it.
                    self.write_rows(d, &mut rows).await?;
                }
                if kind == "thread_confirmed" {
                    // Its `session_id` where the harness names the session apart from the thread (Claude), else the thread.
                    if let Some(f) = d.fresh {
                        let session = Some(text_of(&event["session_id"]))
                            .filter(|s| !s.is_empty())
                            .unwrap_or_else(|| thread.to_string());
                        // A local save error must not discard a completed answer queued behind this event.
                        // Settlement retries the save outside the cancellable drive and retains completed work.
                        if self.confirm_thread(f, &session).await.is_err() {
                            let _ = self.state.append(aid, "diagnostic", &json!({"text": "Session is running; local session state could not be saved. Settlement will retry the save."}));
                        }
                    }
                    continue;
                }
                let event_turn = text_of(&event["turn_id"]);
                if goal_running && kind == "status" && !event_turn.is_empty() {
                    let fresh = event_turn != turn;
                    turn = event_turn.clone();
                    if fresh {
                        // Codex starts a new turn for each continuation of an unmet goal.
                        let last = reply
                            .trim()
                            .rsplit("\n\n")
                            .next()
                            .unwrap_or_default()
                            .to_string();
                        self.write_rows(d, &mut rows).await?;
                        stuck!(scrub(&last), goals::MAX_CODEX_GOAL_TRIES);
                    }
                }
                if !event_turn.is_empty() && event_turn != turn && !goal_running {
                    continue;
                }
                if kind == "rate_limits" && segment.billing == "subscription" {
                    // No profile: the computer's own login ("").
                    let profile = segment.profile.as_deref().unwrap_or_default();
                    // Whose login the turn ran on: the company subscription it signed in with, else the computer's own.
                    let mut reading = event.clone();
                    match segment.subscription.as_deref() {
                        Some(id) => {
                            reading["origin"] = json!("subscription");
                            reading["subscription_id"] = json!(id);
                        }
                        None => reading["origin"] = json!("login"),
                    }
                    crate::profiles::remember_weekly(
                        profile,
                        &text_of(&segment.config["runtime"]),
                        &reading,
                    );
                }
                if kind == "goal" {
                    let continued = truthy(event.get("tried"));
                    let record = event.get("record").cloned().unwrap_or(Value::Null);
                    if let Some(m) = event.as_object_mut() {
                        m.remove("tried");
                        m.remove("record");
                    }
                    if let Some(g) = &chat_goal {
                        // Only while the goal still runs: a clear after "met" is not checked into a second "met".
                        // Not while salvaging: the native query can wait two minutes or fail, and the answer behind
                        // this event is what the salvage is for. The goal event is only filed on the lapsed run anyway.
                        if across_turns && goal_running && salvage.is_none() {
                            event = host
                                .reconcile_codex_clear(thread, event, &text_of(&g["objective"]))
                                .await?;
                        }
                        if truthy(event.get("objective")) {
                            event["objective"] = goals::tico_objective(&event["objective"]);
                        }
                        let status = text_of(&event["status"]);
                        if (status == "met" || status == "paused") && !clearing {
                            // Waiting on others is done for now: paused, so a person can resume it.
                            let note = text_of(&event["note"]);
                            let waiting = goals::waiting_on(&[&reply, &note]);
                            if !waiting.is_empty() {
                                event["status"] = json!("paused");
                                event["note"] = json!(waiting);
                            }
                        }
                        let mut row = scrubbed(d.redactor, &event).into_owned();
                        row["goal_id"] = g["id"].clone();
                        row["revision"] = revision.clone();
                        self.state.append(aid, "goal", &row)?;
                        goal_running = event["status"] == "active" && pausing.is_none();
                    }
                    let record_key = text_of(&record);
                    if continued && (record.is_null() || !tried.contains(&record_key)) {
                        // Counted with or without a Tico goal: a native goal outlives the chat that set it.
                        tried.insert(record_key);
                        stuck!(scrub(&text_of(&event["note"])), goals::MAX_GOAL_TRIES);
                    }
                }
                if pausing.is_some() && (kind == "turn_completed" || kind == "turn_failed") {
                    pause_ready = true; // the interrupted turn is over; its goal is cleared next
                    continue;
                }
                if clearing && (kind == "delta" || kind == "message") {
                    continue; // the clear's own echo is not the bot's answer
                }
                if kind == "message" || kind == "tool" {
                    // Proof the turn reached the model and may have changed something outside this computer.
                    acted = true;
                }
                if kind == "background_kept" {
                    // Watched after the run; a command still running at the run's time limit is killed then.
                    let deadline = crate::util::epoch_s()
                        + d.limit
                            .saturating_duration_since(Instant::now())
                            .as_secs_f64();
                    crate::afterwatch::keep(
                        &self.state.directory,
                        aid,
                        d.bot,
                        d.session,
                        deadline,
                        &event,
                        d.redactor,
                    );
                    continue;
                }
                if kind == "diagnostic" && event["background_wait"].is_object() {
                    // What ending the wait saved: the rest of the run's time, which the wait would have held.
                    event["background_wait"]["minutes_saved"] =
                        json!(d.limit.saturating_duration_since(Instant::now()).as_secs() / 60);
                }
                if STREAMED.contains(&kind.as_str()) {
                    let clean = scrubbed(d.redactor, &event);
                    if !first_output && (kind == "delta" || kind == "message") {
                        first_output = true;
                        mark("first_output", aid);
                    }
                    if kind == "message" && (truthy(event.get("final")) || reply.is_empty()) {
                        reply = text_of(&clean["text"]);
                    }
                    let saved = if kind == "turn_failed" {
                        "error".to_string()
                    } else {
                        kind.clone()
                    };
                    rows.push((saved, clean.to_string()));
                    if kind == "tokens" {
                        tokens = event.clone();
                        segment.meter.add(&event);
                    }
                }
                if kind == "turn_completed" {
                    outcome = if event["status"] == "interrupted" {
                        "interrupted".into()
                    } else {
                        "completed".into()
                    };
                    if outcome == "interrupted" {
                        // This is a host's acknowledged end, unlike losing its connection or timing out.
                        self.state.append(
                            aid,
                            "diagnostic",
                            &json!({"interruption_complete": true}),
                        )?;
                    }
                    // A goal that runs across turns is not continued once the lease is lost.
                    complete = salvage.is_some()
                        || !(goal_running && across_turns && outcome == "completed");
                } else if kind == "turn_failed"
                    || (kind == "error" && truthy(event.get("host_restart")))
                {
                    outcome = "failed".into();
                    let error = text_of(&event["error"]);
                    goal.failure = Some(if error.is_empty() {
                        "The harness stopped".into()
                    } else {
                        scrub(&error)
                    });
                    limited = truthy(event.get("limit"));
                    capacity = limited && hosts::is_capacity(&error);
                    if capacity && acted {
                        // A busy model after the bot acted is not a turn that never ran: no fallback or replay may
                        // repeat what it did. Only this same thread, which knows what it did, may take the rest
                        // (`continues`); without it the run ends as a failed run saved for review.
                        (limited, capacity, continues) = (false, false, true);
                    }
                    // A sign-in the runtime could not renew refuses the turn before it starts, so the job can go
                    // back in the queue; `acted` guards a token that died after the bot had done something.
                    let rejected = hosts::is_auth_rejected(&error);
                    if rejected {
                        *auth_rejected = Some((d.runtime.to_string(), scrub(&error)));
                    }
                    retryable = truthy(event.get("auth_retry")) && !acted && !rejected;
                    complete = true;
                }
            }
            self.write_rows(d, &mut rows).await?;
            if pausing.is_some() && salvage.is_some() {
                // A pause that was waiting when the lease went: its goal is not cleared in a new turn. The interrupted
                // turn's answer, with the pause note, is the result once that turn has ended.
                if pause_ready {
                    pausing = None;
                    outcome = "completed".into();
                    complete = true;
                }
            } else if let Some(p) = pausing.clone() {
                if pause_ready
                    || pause_at.elapsed() >= Duration::from_secs_f64(goals::GOAL_PAUSE_WAIT_S)
                    || host.active_turn(thread).as_deref() != Some(p.as_str())
                {
                    pausing = None;
                    clearing = true;
                    match host.start_goal(thread, "pause", "", d.effort).await {
                        Ok(t) => {
                            turn = t;
                            outcome = "interrupted".into();
                        }
                        Err(e) => {
                            self.state.append(
                                aid,
                                "diagnostic",
                                &json!({"text": format!("The goal could not be cleared: {e}")}),
                            )?;
                            outcome = "completed".into();
                            complete = true;
                        }
                    }
                }
            }
            if complete {
                // An input the step read is steered, or carried into the next turn as it would have been a step
                // earlier.
                settle_step!();
            }
            if complete
                && outcome == "completed"
                && !carried.is_empty()
                && !goal.controlled
                && salvage.is_none()
                // Nothing new is started once the run was stopped or its lease, time or computer ran out meanwhile.
                && !d.flags.stopped.load(Ordering::SeqCst)
                && !d.flags.lost.load(Ordering::SeqCst)
                && Instant::now() < d.limit
                && !self.stopping()
            {
                // Not on a failed or stopped turn: those settle the attempt, inputs included.
                turn = self
                    .carry_inputs(aid, host, thread, carried, d.effort)
                    .await?;
                outcome = "interrupted".into();
                complete = false;
                earlier = answer(&earlier, &reply);
                reply.clear();
                earlier_tokens = Some(spent(&earlier_tokens, &tokens));
                tokens = json!({});
            }
            // Taken before any request, so a notice that lands while one is out is acted on at the next step.
            // A notice is acted on at the next 1-second step, never sooner: a burst of notices for one bot costs at
            // most one ask a second, as the old poll did.
            let heard = self.events.heard(d.bot);
            if step.is_none() && last_flush.is_none_or(|t| t.elapsed() >= Duration::from_secs(1)) {
                let noticed = asked != Some(heard);
                let ask = noticed || !self.events.live() || Instant::now() >= recovery_at;
                // Once the lease is lost the hub refuses this attempt's goal and inputs; its events and result it keeps.
                let check_goal = goal_running && ask && salvage.is_none();
                let steer = !complete && ask && host.supports_steer() && salvage.is_none();
                // A turn that has ended is uploaded by the attempt's final flush; only a goal read is still asked.
                if !complete || check_goal {
                    if ask {
                        asked = Some(heard);
                        recovery_at = Instant::now() + self.events.turn_recovery();
                    }
                    host.poll_goal(thread);
                    let (goal_id, revision) = (goal_id.clone(), revision.clone());
                    step_asks = check_goal || steer;
                    step = Some(Box::pin(async move {
                        self.flush(aid).await?;
                        let mut asked = Asked::default();
                        if check_goal {
                            let latest = self.client.get(&format!("attempts/{aid}/goal")).await?;
                            let latest = &latest["goal"];
                            if !latest.is_object()
                                || latest["id"] != goal_id
                                || latest["updated_at"] != revision
                            {
                                asked.controlled = true;
                                return Ok(asked);
                            }
                        }
                        if steer {
                            asked.messages = self.fetch_inputs(aid).await?;
                        }
                        Ok(asked)
                    }));
                    if complete {
                        let s = step.take().unwrap();
                        settle!(s.await);
                    }
                }
            }
            if !goal_running && pausing.is_none() && outcome == "completed" {
                complete = true;
            }
            if complete {
                // What the step read is acted on before the run's result is: the hub counts it handed over.
                settle_step!();
                break;
            }
            if !host.alive() {
                // A step that read is heard out first: a lease it found lost turns this into a salvage read.
                settle_step!();
                // Whatever it said before it went is read first.
                while let Ok(ev) = rx.try_recv() {
                    queue.push(ev);
                }
                if complete || !queue.is_empty() {
                    continue;
                }
                if salvage.is_none() {
                    return Err(Fail::runtime("Local runtime exited unexpectedly"));
                }
            }
            // The step first, so a stream that never pauses still moves it on (and the upload lock it may hold).
            tokio::select! {
                biased;
                done = step_done(&mut step) => {
                    step = None;
                    settle!(done);
                }
                got = tokio::time::timeout(Duration::from_millis(100), rx.recv()) => match got {
                    Ok(Some(ev)) => queue.push(ev),
                    Ok(None) => tokio::time::sleep(Duration::from_millis(100)).await, // no sender left; the window ends it
                    Err(_) => {}
                },
            }
        }
        let mut text = answer(&earlier, &reply);
        if let Some(p) = &goal.paused {
            text = if text.is_empty() {
                p.clone()
            } else {
                format!("{text}\n\n{p}")
            };
        }
        Ok(Drove {
            outcome,
            reply: text,
            tokens: spent(&earlier_tokens, &tokens),
            limited,
            acted,
            capacity,
            continues,
            retryable,
            turn,
            yielded: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hosts::claude::ClaudeHost;
    use std::sync::atomic::AtomicBool;
    use std::sync::Mutex;

    /// A hub that has let this attempt's lease go: renewals, inputs and goal reads are refused as stale, while
    /// events and the result are still taken (backend/execution.py `attempt` with `active=False` / `final=True`).
    async fn lapsed_hub() -> (String, Arc<Mutex<Vec<String>>>) {
        hub_renewing(None).await
    }

    /// `lapsed_hub`, or with `stop_reason` a hub that answers each renewal with a Stop and that reason, the way it
    /// answers for a quarantined bot's turn (Dev 573); its inputs and goal reads are then answered too.
    async fn hub_renewing(stop_reason: Option<&'static str>) -> (String, Arc<Mutex<Vec<String>>>) {
        hub_answering(move |path, body| {
            if let Some(why) = stop_reason.filter(|_| path.ends_with("/renew")) {
                (
                    "200 OK",
                    json!({"lease_until": "x", "lease_seconds": 90, "stop": true, "stop_reason": why}),
                )
            } else if stop_reason.is_some()
                && (path.ends_with("/inputs") || path.ends_with("/goal"))
            {
                ("200 OK", json!({"messages": []}))
            } else if ["/renew", "/inputs", "/goal"]
                .iter()
                .any(|p| path.ends_with(p))
                || path.contains("/inputs/")
            {
                (
                    "409 Conflict",
                    json!({"error": {"code": "stale_lease", "detail": "Execution ownership expired; stop this attempt", "retryable": false}}),
                )
            } else {
                events_taken(body)
            }
        })
        .await
    }

    /// The hub's answer to an events upload: all of them taken.
    fn events_taken(body: &Value) -> (&'static str, Value) {
        (
            "200 OK",
            json!({"ack_seq": body["events"].as_array().and_then(|e| e.last()).map_or(json!(0), |l| l["seq"].clone())}),
        )
    }

    /// A hub that keeps each request as "<path> <JSON body>" and answers it with `answer(path, body)`.
    async fn hub_answering(
        answer: impl Fn(&str, &Value) -> (&'static str, Value) + Send + Sync + 'static,
    ) -> (String, Arc<Mutex<Vec<String>>>) {
        hub_answering_after(|_| Duration::ZERO, answer).await
    }

    /// `hub_answering`, each answer sent `delay(path)` after its request is read (a hub busy with its write lock).
    async fn hub_answering_after(
        delay: impl Fn(&str) -> Duration + Send + Sync + 'static,
        answer: impl Fn(&str, &Value) -> (&'static str, Value) + Send + Sync + 'static,
    ) -> (String, Arc<Mutex<Vec<String>>>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let (answer, delay) = (Arc::new(answer), Arc::new(delay));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(vec![]));
        let kept = seen.clone();
        tokio::spawn(async move {
            while let Ok((mut socket, _)) = listener.accept().await {
                let (kept, answer, delay) = (kept.clone(), answer.clone(), delay.clone());
                tokio::spawn(async move {
                    let (mut request, mut buffer) = (Vec::new(), [0u8; 8192]);
                    let head = loop {
                        if let Some(at) = request.windows(4).position(|part| part == b"\r\n\r\n") {
                            break at;
                        }
                        let n = socket.read(&mut buffer).await.unwrap_or(0);
                        if n == 0 {
                            return;
                        }
                        request.extend_from_slice(&buffer[..n]);
                    };
                    let text = String::from_utf8_lossy(&request[..head]).to_string();
                    let length = text
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                        })
                        .unwrap_or(0);
                    while request.len() < head + 4 + length {
                        let n = socket.read(&mut buffer).await.unwrap_or(0);
                        if n == 0 {
                            break;
                        }
                        request.extend_from_slice(&buffer[..n]);
                    }
                    let path = text
                        .lines()
                        .next()
                        .unwrap_or_default()
                        .split_whitespace()
                        .nth(1)
                        .unwrap_or_default()
                        .to_string();
                    let body: Value =
                        serde_json::from_slice(&request[head + 4..]).unwrap_or(Value::Null);
                    kept.lock().unwrap().push(format!("{path} {body}"));
                    let (status, answer) = answer(&path, &body);
                    tokio::time::sleep(delay(&path)).await;
                    let answer = answer.to_string();
                    let response = format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{answer}", answer.len());
                    let _ = socket.write_all(response.as_bytes()).await;
                    let _ = socket.shutdown().await;
                });
            }
        });
        (url, seen)
    }

    fn runner_at(url: &str) -> (Runner, std::path::PathBuf) {
        let dir =
            std::env::temp_dir().join(format!("tico-salvage-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")}).to_string()).unwrap();
        let config = crate::config::Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        (
            Runner::new(
                config,
                client,
                crate::state::State::open(&dir.join("state")).unwrap(),
            ),
            dir,
        )
    }

    fn flags(lost: bool) -> Flags {
        Flags {
            lost: AtomicBool::new(lost),
            stopped: AtomicBool::new(false),
            stop_reason: Mutex::new(None),
            done: AtomicBool::new(false),
            harness_allocated: Arc::new(AtomicBool::new(false)),
            start_acknowledged: AtomicBool::new(false),
            granted: Mutex::new(None),
            done_notify: Notify::new(),
            deadline: Mutex::new(Instant::now()),
        }
    }

    /// The model's last words for `turn` on `thread`, the way the Claude host reports a finished turn.
    fn answer_events(thread: &str, turn: &str) -> Vec<Value> {
        vec![
            json!({"kind": "message", "thread_id": thread, "turn_id": turn, "text": "Acme's renewal summary is ready.", "final": true}),
            json!({"kind": "turn_completed", "thread_id": thread, "turn_id": turn, "status": "completed"}),
        ]
    }

    /// Drive one Acme turn on `host` against `runner`; `feed` gets the host's event sender.
    async fn drive_turn(
        runner: &Runner,
        host: &Host,
        rx: &mut UnboundedReceiver<Value>,
        f: &Flags,
        thread: &str,
    ) -> Result<Drove, Fail> {
        drive_with(runner, host, rx, f, thread, "claude", None, BTreeMap::new())
            .await
            .0
    }

    /// `drive_turn` with a Tico chat goal and inputs a steer refused, handing back what is still carried after.
    #[allow(clippy::too_many_arguments)]
    async fn drive_with(
        runner: &Runner,
        host: &Host,
        rx: &mut UnboundedReceiver<Value>,
        f: &Flags,
        thread: &str,
        runtime: &str,
        chat_goal: Option<Value>,
        carried: BTreeMap<String, Value>,
    ) -> (Result<Drove, Fail>, BTreeMap<String, Value>, GoalRun) {
        let within = Duration::from_secs(10);
        drive_within(
            runner, host, rx, f, thread, runtime, chat_goal, carried, within,
        )
        .await
    }

    /// `drive_with`, for a turn that may run up to `within` (the test's own limit; the turn's is at least a minute).
    #[allow(clippy::too_many_arguments)]
    async fn drive_within(
        runner: &Runner,
        host: &Host,
        rx: &mut UnboundedReceiver<Value>,
        f: &Flags,
        thread: &str,
        runtime: &str,
        chat_goal: Option<Value>,
        mut carried: BTreeMap<String, Value>,
        within: Duration,
    ) -> (Result<Drove, Fail>, BTreeMap<String, Value>, GoalRun) {
        runner.state.record(&json!({"id": "acme-late"})).unwrap();
        let wake = Notify::new();
        let d = Drive {
            limit_name: "max_run_minutes (60 minutes)",
            aid: "acme-late",
            host,
            thread,
            effort: None,
            limit: Instant::now() + within.max(Duration::from_secs(60)),
            flags: f,
            wake: &wake,
            redactor: None,
            runtime,
            bot: "acme",
            session: thread,
            fresh: None,
        };
        let mut segment = Segment {
            meter: Meter::default(),
            config: json!({}),
            profile: None,
            billing: "api",
            subscription: None,
        };
        let mut goal = GoalRun {
            chat_goal,
            controlled: false,
            failure: None,
            paused: None,
            tries_key: ("acme".into(), thread.into()),
        };
        let mut rejected = None;
        let drove = tokio::time::timeout(
            within,
            runner.drive(
                &d,
                rx,
                "turn-1".into(),
                &mut segment,
                &mut carried,
                &mut rejected,
                &mut goal,
            ),
        )
        .await
        .expect("the drive ends");
        (drove, carried, goal)
    }

    #[tokio::test]
    async fn a_model_at_capacity_is_a_limit_before_the_bot_acts_and_a_continuation_after() {
        for acted in [false, true] {
            let (url, _seen) = lapsed_hub().await;
            let (runner, dir) = runner_at(&url);
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
            let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
            if acted {
                tx.send(json!({"kind": "message", "thread_id": "th-cap", "turn_id": "turn-1", "text": "Sent the Acme invoice", "final": false})).unwrap();
            }
            tx.send(json!({"kind": "turn_failed", "thread_id": "th-cap", "turn_id": "turn-1", "limit": true,
                           "error": "Selected model is at capacity. Please try a different model."})).unwrap();
            let f = flags(false);
            let drove = drive_turn(&runner, &host, &mut rx, &f, "th-cap")
                .await
                .unwrap_or_else(|e| panic!("refused: {}", e.text));
            assert_eq!(drove.outcome, "failed");
            // Before it acted the short capacity hold and a fresh retry or fallback are safe; after, only its thread.
            assert_eq!(
                (drove.limited, drove.capacity, drove.continues),
                (!acted, !acted, acted),
                "acted: {acted}"
            );
            std::fs::remove_dir_all(dir).ok();
        }
    }

    #[tokio::test]
    async fn only_an_acknowledged_interruption_records_the_terminal_recovery_receipt() {
        for status in ["interrupted", "completed"] {
            let (url, sent) = lapsed_hub().await;
            let (runner, dir) = runner_at(&url);
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
            let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
            tx.send(json!({"kind": "turn_completed", "thread_id": "th-1", "turn_id": "turn-1", "status": status})).unwrap();
            let drove = drive_turn(&runner, &host, &mut rx, &flags(true), "th-1")
                .await
                .unwrap();
            assert_eq!(drove.outcome, status);
            // The attempt's final flush, before its result: a turn that has ended leaves its last rows to it.
            runner.flush("acme-late").await.unwrap();
            let events: Vec<Value> = sent
                .lock()
                .unwrap()
                .iter()
                .filter_map(|request| {
                    let (path, body) = request.split_once(' ')?;
                    path.ends_with("/events")
                        .then(|| serde_json::from_str::<Value>(body).unwrap())
                })
                .flat_map(|body| body["events"].as_array().cloned().unwrap_or_default())
                .collect();
            assert_eq!(
                events
                    .iter()
                    .any(|e| e["kind"] == "diagnostic"
                        && e["payload"]["interruption_complete"] == true),
                status == "interrupted",
                "{events:?}"
            );
            std::fs::remove_dir_all(dir).ok();
        }
    }

    #[tokio::test]
    async fn run_diagnostics_timeout_names_the_limit_and_tail_uses_a_separate_capped_row() {
        let (runner, dir) = runner_at("http://127.0.0.1:9");
        runner.state.record(&json!({"id": "diagnostics"})).unwrap();
        let secret = "synthetic-granted-secret".to_string();
        let redactor = Arc::new(Redactor::new(&[secret.clone()]));
        let (tx, mut rx) = runner.attempt_events("diagnostics", Some(redactor));
        tx.send(json!({"kind": "diagnostic", "stderr_tail": format!("failure {secret}")}))
            .unwrap();
        tx.send(json!({"kind": "diagnostic", "stderr_tail": format!("last failure {secret}")}))
            .unwrap();
        // A failed call's detail takes its own capped rows, the turn's own secret taken out, and never an event.
        tx.send(crate::run_diagnostics::failed_tool(
            "call_1",
            "shell",
            &format!("deploy --key {secret}"),
            &format!("refused: {secret}"),
            Some(2),
        ))
        .unwrap();
        tx.send(json!({"kind": "_diagnostics_drained"})).unwrap();
        assert_eq!(rx.recv().await.unwrap()["kind"], "_diagnostics_drained");
        let failed = runner.state.pending_failed_tools(None).unwrap();
        assert_eq!(failed.len(), 1);
        let (run, calls) = &failed[0];
        assert_eq!((run.as_str(), calls.len()), ("diagnostics", 1));
        let detail = &calls[0].1;
        assert_eq!(
            (detail["item_id"].as_str(), detail["exit_code"].as_i64()),
            (Some("call_1"), Some(2))
        );
        assert!(detail["command"]
            .as_str()
            .unwrap()
            .starts_with("deploy --key"));
        assert!(!detail.to_string().contains(&secret), "{detail}");
        runner
            .state
            .failed_tools_sent("diagnostics", &["call_1".into()])
            .unwrap();
        assert!(runner.state.pending_failed_tools(None).unwrap().is_empty());
        let tails = runner.state.pending_stderr(Some("diagnostics")).unwrap();
        assert_eq!(tails.len(), 1);
        assert!(tails[0].1.starts_with("last failure"));
        assert!(!tails[0].1.contains(&secret));
        assert!(runner.state.pending("diagnostics").unwrap().is_empty());
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx));
        let f = flags(false);
        let wake = Notify::new();
        let d = Drive {
            aid: "diagnostics",
            host: &host,
            thread: "th",
            effort: None,
            limit: Instant::now(),
            limit_name: "max_run_minutes (60 minutes)",
            flags: &f,
            wake: &wake,
            redactor: None,
            runtime: "claude",
            bot: "acme",
            session: "th",
            fresh: None,
        };
        let mut segment = Segment {
            meter: Meter::default(),
            config: json!({}),
            profile: None,
            billing: "api",
            subscription: None,
        };
        let mut goal = GoalRun {
            chat_goal: None,
            controlled: false,
            failure: None,
            paused: None,
            tries_key: ("acme".into(), "th".into()),
        };
        let err = runner
            .drive(
                &d,
                &mut rx,
                "turn".into(),
                &mut segment,
                &mut BTreeMap::new(),
                &mut None,
                &mut goal,
            )
            .await
            .err()
            .unwrap();
        assert_eq!(err.text, "Timeout: max_run_minutes (60 minutes)");
        assert!(runner
            .state
            .pending("diagnostics")
            .unwrap()
            .iter()
            .any(|e| e["payload"]["end_reason"] == err.text));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn an_answer_that_finished_while_the_computer_slept_is_handed_in_completed_after_the_renewal_is_refused(
    ) {
        // QA case A/B: the runner was paused past the lease and the cleanup; the model finished meanwhile. On wake the
        // renewer hears 409 first (`lost`), with the whole answer still waiting in the host's stream.
        let (url, seen) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
        for ev in answer_events("th-1", "turn-1") {
            tx.send(ev).unwrap();
        }
        let f = flags(true);
        let drove = drive_turn(&runner, &host, &mut rx, &f, "th-1")
            .await
            .unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(
            (drove.outcome.as_str(), drove.reply.as_str()),
            ("completed", "Acme's renewal summary is ready.")
        );
        assert!(
            !seen
                .lock()
                .unwrap()
                .iter()
                .any(|p| p.contains("/inputs") || p.contains("/goal ")),
            "nothing more was asked of the hub"
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn an_inputs_refusal_heard_before_the_renewer_still_reads_out_the_finished_answer() {
        // QA's native log: the inputs poll got the 409 too, and its error used to end the drive at once.
        let (url, seen) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        let watched = seen.clone();
        let feed = tokio::spawn(async move {
            while !watched
                .lock()
                .unwrap()
                .iter()
                .any(|p| p.contains("/inputs "))
            {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            for ev in answer_events("th-2", "turn-1") {
                tx.send(ev).unwrap();
            }
        });
        let f = flags(false);
        let drove = drive_turn(&runner, &host, &mut rx, &f, "th-2")
            .await
            .unwrap_or_else(|e| panic!("refused: {}", e.text));
        feed.await.unwrap();
        assert_eq!(drove.outcome, "completed");
        assert_eq!(drove.reply, "Acme's renewal summary is ready.");
        assert!(
            f.lost.load(Ordering::SeqCst),
            "the refusal counts as a lost lease"
        );
        assert_eq!(
            seen.lock()
                .unwrap()
                .iter()
                .filter(|p| p.contains("/inputs "))
                .count(),
            1,
            "inputs are asked for once, not after the refusal"
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_lost_lease_on_a_model_still_working_ends_the_turn_after_a_short_read() {
        let (url, _) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(false);
        let host = Host::Claude(claude);
        tx.send(json!({"kind": "delta", "thread_id": "th-3", "turn_id": "turn-1", "text": "Acme's renewal"})).unwrap();
        let (f, began) = (flags(true), Instant::now());
        let err = drive_turn(&runner, &host, &mut rx, &f, "th-3")
            .await
            .err()
            .expect("not completed");
        assert_eq!(
            err.text,
            "Computer lost or heartbeat stopped (execution lease expired)"
        );
        assert!(
            began.elapsed() >= SALVAGE && began.elapsed() < SALVAGE + Duration::from_secs(3),
            "{:?}",
            began.elapsed()
        );
        drop(tx);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_renewal_answered_with_a_quarantine_stop_ends_the_turn_with_that_reason_not_as_lost()
    {
        // Dev 573: the hub answers a quarantined bot's renewal with a Stop and its reason, instead of the stale lease
        // that read as a lost computer, waited out SALVAGE and lapsed the attempt.
        let why = "Quarantined: 10 refusals today, resumes about 10:05 UTC";
        let (url, seen) = hub_renewing(Some(why)).await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        tx.send(json!({"kind": "delta", "thread_id": "th-q", "turn_id": "turn-1", "text": "Acme's renewal"})).unwrap();
        let f = Arc::new(flags(false));
        let renewer = crate::lease::spawn(
            &runner.client,
            "acme-late".into(),
            f.clone(),
            Duration::from_millis(20),
        );
        let began = Instant::now();
        let (drove, _, goal) = drive_with(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-q",
            "claude",
            None,
            BTreeMap::new(),
        )
        .await;
        renewer.abort();
        let drove = drove.unwrap_or_else(|e| panic!("ended as a failure: {}", e.text));
        assert_eq!(
            drove.outcome, "interrupted",
            "stopped, the way a person's Stop ends a turn"
        );
        assert!(!f.lost.load(Ordering::SeqCst), "never the lost-lease path");
        // Measured for the record, not limited: a wall-clock bound on this test failed under box load.
        eprintln!("renewal stop elapsed: {:?}", began.elapsed());
        assert_eq!(goal.failure.as_deref(), Some(why));
        let reasons: Vec<Value> = runner
            .state
            .pending("acme-late")
            .unwrap()
            .iter()
            .map(|e| e["payload"]["end_reason"].clone())
            .filter(|r| !r.is_null())
            .collect();
        assert_eq!(
            reasons,
            vec![json!(why)],
            "the end reason is the quarantine's, never a lost computer"
        );
        assert!(seen.lock().unwrap().iter().any(|p| p.contains("/renew ")));
        drop(tx);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn inputs_a_steer_refused_start_no_new_turn_after_the_lease_is_lost() {
        let (url, seen) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
        for ev in answer_events("th-6", "turn-1") {
            tx.send(ev).unwrap();
        }
        let carried: BTreeMap<String, Value> = [(
            "m-1".to_string(),
            json!({"id": "m-1", "body": "Also add Q3.", "from_actor": "human:ana"}),
        )]
        .into();
        let f = flags(true);
        let (drove, left, _) =
            drive_with(&runner, &host, &mut rx, &f, "th-6", "claude", None, carried).await;
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(
            (drove.outcome.as_str(), drove.reply.as_str()),
            ("completed", "Acme's renewal summary is ready.")
        );
        assert_eq!(
            left.keys().collect::<Vec<_>>(),
            ["m-1"],
            "still carried, so the result reports it undelivered"
        );
        assert!(
            !seen.lock().unwrap().iter().any(|p| p.contains("/inputs")),
            "nothing acknowledged"
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn an_input_read_while_the_turn_ends_is_kept_and_starts_nothing_after_a_stop() {
        // The turn's first inputs read takes a second; the turn ends meanwhile, and a person presses Stop. The input
        // the hub handed over is not dropped with the read, and no new turn is started for it after the Stop.
        let (url, seen) = hub_answering_after(
            |path| {
                if path.ends_with("/inputs") {
                    Duration::from_secs(1)
                } else {
                    Duration::ZERO
                }
            },
            |path, body| {
                if path.ends_with("/inputs") {
                    (
                        "200 OK",
                        json!({"messages": [{"id": "m-7", "body": "Also add Q3.", "from_actor": "human:ana"}]}),
                    )
                } else {
                    events_taken(body)
                }
            },
        )
        .await;
        let (runner, dir) = runner_at(&url);
        runner
            .events
            .dispatch("ready", None, &json!({"seq": 1, "release": "v1"}), false);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        assert!(host.supports_steer());
        let f = Arc::new(flags(false));
        let stopper = f.clone();
        let feed = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(200)).await;
            for ev in answer_events("th-7", "turn-1") {
                tx.send(ev).unwrap();
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
            stopper.stopped.store(true, Ordering::SeqCst);
        });
        let (drove, left, _) = drive_with(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-7",
            "claude",
            None,
            BTreeMap::new(),
        )
        .await;
        feed.await.unwrap();
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(drove.reply, "Acme's renewal summary is ready.");
        assert_eq!(
            left.keys().collect::<Vec<_>>(),
            ["m-7"],
            "still carried, so the result reports it undelivered"
        );
        assert!(
            !seen.lock().unwrap().iter().any(|p| p.contains("/ack")),
            "nothing acknowledged"
        );
        std::fs::remove_dir_all(dir).ok();
    }

    fn acme_goal() -> Value {
        json!({"id": "g-1", "status": "active", "objective": "Finish the Acme renewal summary", "updated_at": "2026-10-21T04:00:00Z"})
    }

    /// A hub where a steerable turn's chat goal stays as it was and no input is pending: every inputs and goal read
    /// is the empty answer a poll gets almost always.
    async fn quiet_hub() -> (String, Arc<Mutex<Vec<String>>>) {
        hub_answering(|path, body| {
            if path.ends_with("/goal") {
                ("200 OK", json!({"goal": acme_goal()}))
            } else if path.ends_with("/inputs") {
                ("200 OK", json!({"attempt_id": "acme-late", "messages": []}))
            } else {
                events_taken(body)
            }
        })
        .await
    }

    /// (inputs requests, goal requests) the hub has seen.
    fn asks(seen: &Arc<Mutex<Vec<String>>>) -> (usize, usize) {
        let seen = seen.lock().unwrap();
        let count = |end: &str| {
            seen.iter()
                .filter(|p| p.split(' ').next().is_some_and(|path| path.ends_with(end)))
                .count()
        };
        (count("/inputs"), count("/goal"))
    }

    /// A steerable Claude turn with the Acme chat goal running on a quiet hub, with the event stream up (`live`) or
    /// down. `during` runs beside it and gets the hub's log; the turn's answer comes once it returns.
    async fn idle_turn<F, Fut>(
        live: bool,
        recovery: Option<Duration>,
        within: Duration,
        during: F,
    ) -> (usize, usize)
    where
        F: FnOnce(Arc<crate::events::Events>, Arc<Mutex<Vec<String>>>) -> Fut,
        Fut: std::future::Future<Output = ()> + Send + 'static,
    {
        let (url, seen) = quiet_hub().await;
        let (runner, dir) = runner_at(&url);
        if live {
            runner
                .events
                .dispatch("ready", None, &json!({"seq": 1, "release": "v1"}), false);
            assert!(runner.events.live());
        }
        if let Some(every) = recovery {
            runner.events.set_turn_recovery(every);
        }
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        assert!(host.supports_steer());
        let side = tokio::spawn(during(runner.events.clone(), seen.clone()));
        let feed = tokio::spawn(async move {
            side.await.unwrap();
            for ev in answer_events("th-idle", "turn-1") {
                tx.send(ev).unwrap();
            }
        });
        let f = flags(false);
        let (drove, _, goal) = drive_within(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-idle",
            "claude",
            Some(acme_goal()),
            BTreeMap::new(),
            within,
        )
        .await;
        feed.await.unwrap();
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(drove.outcome, "completed");
        assert!(!goal.controlled, "the goal was read unchanged");
        std::fs::remove_dir_all(dir).ok();
        asks(&seen)
    }

    #[tokio::test]
    async fn an_idle_turn_with_the_stream_up_asks_once_then_only_on_a_notice_for_its_own_bot() {
        let counts = idle_turn(
            true,
            None,
            Duration::from_secs(10),
            |events, seen| async move {
                tokio::time::sleep(Duration::from_millis(2500)).await;
                assert_eq!(
                    asks(&seen),
                    (1, 1),
                    "the first step asks; no notice, no more asks"
                );
                // Work for another bot on this computer is not this turn's.
                events.dispatch(
                    "runner",
                    None,
                    &json!({"kind": "work", "bots": ["cpo"]}),
                    false,
                );
                // Neither is a change that names a channel the turn does not read.
                events.dispatch(
                    "runner",
                    None,
                    &json!({"kind": "logins", "bots": ["acme"]}),
                    false,
                );
                tokio::time::sleep(Duration::from_millis(1300)).await;
                assert_eq!(asks(&seen), (1, 1));
                // A follow-up queued, or the chat goal changed, for this bot: both are asked for within a second.
                events.dispatch(
                    "runner",
                    None,
                    &json!({"kind": "work", "bots": ["cpo", "acme"]}),
                    false,
                );
                // At the next 1-second step (plus at most one 100 ms receive wait).
                tokio::time::sleep(Duration::from_millis(1200)).await;
                assert_eq!(asks(&seen), (2, 2));
                // Anything that means "read everything again" asks too: a reset after a gap.
                events.dispatch("reset", None, &json!({}), false);
                tokio::time::sleep(Duration::from_millis(1200)).await;
                assert_eq!(asks(&seen), (3, 3));
            },
        )
        .await;
        assert_eq!(counts, (3, 3));
    }

    #[tokio::test]
    async fn a_burst_of_notices_for_the_bot_costs_at_most_one_ask_a_second() {
        // Twenty jobs queued for the bot in two seconds (a routine fanning out): no more asks than the old poll.
        let counts = idle_turn(
            true,
            None,
            Duration::from_secs(10),
            |events, _| async move {
                tokio::time::sleep(Duration::from_millis(200)).await;
                for _ in 0..20 {
                    events.dispatch(
                        "runner",
                        None,
                        &json!({"kind": "work", "bots": ["acme"]}),
                        false,
                    );
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
                tokio::time::sleep(Duration::from_millis(1200)).await;
            },
        )
        .await;
        assert!(counts.0 <= 4 && counts.0 == counts.1, "{counts:?}");
    }

    #[tokio::test]
    async fn an_ask_the_hub_failed_is_asked_again_at_the_next_step() {
        // The notice count is taken before the request; a 503 on the inputs read must not leave the follow-up for
        // the recovery check, 15 s away.
        let failed = Arc::new(AtomicBool::new(false));
        let (url, seen) = hub_answering(move |path, body| {
            if path.ends_with("/goal") {
                ("200 OK", json!({"goal": acme_goal()}))
            } else if path.ends_with("/inputs") {
                if failed.swap(true, Ordering::SeqCst) {
                    ("200 OK", json!({"attempt_id": "acme-late", "messages": []}))
                } else {
                    (
                        "503 Service Unavailable",
                        json!({"error": {"code": "unavailable", "detail": "Busy", "retryable": true}}),
                    )
                }
            } else {
                events_taken(body)
            }
        })
        .await;
        let (runner, dir) = runner_at(&url);
        runner
            .events
            .dispatch("ready", None, &json!({"seq": 1, "release": "v1"}), false);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        let watched = seen.clone();
        let feed = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(2600)).await;
            let counts = asks(&watched);
            for ev in answer_events("th-retry", "turn-1") {
                tx.send(ev).unwrap();
            }
            counts
        });
        let f = flags(false);
        let (drove, _, _) = drive_with(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-retry",
            "claude",
            Some(acme_goal()),
            BTreeMap::new(),
        )
        .await;
        let counts = feed.await.unwrap();
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(drove.outcome, "completed");
        assert_eq!(
            counts,
            (2, 2),
            "the failed first ask is asked again a second later, then not again"
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn an_idle_turn_with_the_stream_down_asks_every_second_as_before() {
        let (inputs, goals) = idle_turn(false, None, Duration::from_secs(10), |_, _| async move {
            tokio::time::sleep(Duration::from_millis(3500)).await;
        })
        .await;
        assert!(
            (4..=5).contains(&inputs) && inputs == goals,
            "{inputs} inputs, {goals} goal reads"
        );
    }

    #[tokio::test]
    async fn a_missed_notice_is_caught_by_the_recovery_check() {
        // TURN_RECOVERY_S shortened to 1.5 s (1.2 to 1.8 s with its jitter), checked at the next 1 s step: no notice
        // comes, yet the hub is still asked at 0, 2 and 4 s, where a turn with the stream down asks five times.
        let (inputs, goals) = idle_turn(
            true,
            Some(Duration::from_millis(1500)),
            Duration::from_secs(10),
            |_, _| async move {
                tokio::time::sleep(Duration::from_millis(4500)).await;
            },
        )
        .await;
        assert!(
            (2..=3).contains(&inputs) && inputs == goals,
            "{inputs} inputs, {goals} goal reads"
        );
    }

    /// Bench, not run by CI (spec #140 Measure 1): one idle steerable turn with a running chat goal for 60 s, with
    /// the event stream up and down. `cargo test --release bench_idle_turn -- --ignored --nocapture`.
    #[tokio::test]
    #[ignore]
    async fn bench_idle_turn_requests_for_60_s() {
        for live in [false, true] {
            let (inputs, goals) =
                idle_turn(live, None, Duration::from_secs(75), |_, _| async move {
                    tokio::time::sleep(Duration::from_secs(60)).await;
                })
                .await;
            println!(
                "event stream {}: {inputs} inputs requests, {goals} goal requests in 60 s",
                if live { "up" } else { "down" }
            );
        }
    }

    /// Goal continuations that reach the no-progress cap, then the turn's answer.
    fn stuck_goal_events(thread: &str) -> Vec<Value> {
        let mut events: Vec<Value> = (0..goals::MAX_GOAL_TRIES)
            .map(|n| json!({"kind": "goal", "thread_id": thread, "turn_id": "turn-1", "tried": true, "record": format!("r{n}"), "note": "Still on the Acme summary"}))
            .collect();
        events.extend(answer_events(thread, "turn-1"));
        events
    }

    #[tokio::test]
    async fn a_goal_reaching_its_cap_in_the_buffered_output_starts_no_pause_after_the_lease_is_lost(
    ) {
        let (url, seen) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(false);
        let host = Host::Claude(claude);
        for ev in stuck_goal_events("th-8") {
            tx.send(ev).unwrap();
        }
        let f = flags(true);
        let (drove, _, goal) = drive_with(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-8",
            "claude",
            None,
            BTreeMap::new(),
        )
        .await;
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(
            (drove.outcome.as_str(), drove.reply.as_str()),
            ("completed", "Acme's renewal summary is ready.")
        );
        assert!(goal.paused.is_none(), "no pause, so no /goal clear turn");
        let said = format!(
            "{:?} {:?}",
            seen.lock().unwrap(),
            runner.state.pending("acme-late").unwrap()
        );
        assert!(!said.contains("could not be cleared"), "{said}");
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_buffered_codex_goal_clear_does_not_query_the_harness_before_the_answer_is_read() {
        // Codex reports `thread/goal/cleared` with no turn id; checking it asks the app server, which can
        // take two minutes or, with the process gone as here, fail. Neither may cost the answer queued behind it.
        let (url, _) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let codex = crate::hosts::codex::CodexHost::on_home(Default::default(), tx.clone(), None);
        let host = Host::Codex(codex.clone());
        tx.send(json!({"kind": "goal", "thread_id": "th-11", "status": "cleared", "objective": "Finish the Acme renewal summary"})).unwrap();
        for ev in answer_events("th-11", "turn-1") {
            tx.send(ev).unwrap();
        }
        let f = flags(true);
        let (drove, _, _) = drive_with(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-11",
            "codex",
            Some(acme_goal()),
            BTreeMap::new(),
        )
        .await;
        assert_eq!(
            codex.requests_started(),
            0,
            "salvage must read the queued answer without asking the harness"
        );
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(
            (drove.outcome.as_str(), drove.reply.as_str()),
            ("completed", "Acme's renewal summary is ready.")
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn drain_keeps_a_native_goal_active_for_its_continuation() {
        let (url, _) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx));
        runner.drain.register("acme-late");
        assert!(runner.drain.start("acme-late"));
        runner
            .drain
            .begin(Instant::now() - Duration::from_secs(crate::drain::DEFAULT_LIMIT_S));
        let mut f = flags(false);
        *f.deadline.get_mut().unwrap() = Instant::now() + Duration::from_secs(60);
        let original = acme_goal();
        let (result, _, goal) = drive_with(
            &runner,
            &host,
            &mut rx,
            &f,
            "th-drain",
            "codex",
            Some(original.clone()),
            BTreeMap::new(),
        )
        .await;
        let result = result.unwrap();
        assert_eq!(result.outcome, "interrupted");
        assert!(result.yielded);
        assert_eq!(goal.chat_goal, Some(original));
        assert!(goal.failure.is_none() && goal.paused.is_none() && !goal.controlled);
        assert!(!runner
            .state
            .pending("acme-late")
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "goal"));
        std::fs::remove_dir_all(dir).ok();
    }

    /// The outbox rows of `aid` in seq order, as (seq, kind, payload).
    fn stored_rows(runner: &Runner, aid: &str) -> Vec<(i64, String, Value)> {
        runner
            .state
            .with(|c| {
                let mut stmt = c.prepare(
                    "SELECT seq, kind, payload FROM events WHERE attempt_id = ?1 ORDER BY seq",
                )?;
                let rows = stmt.query_map([aid], |r| {
                    Ok((
                        r.get::<_, i64>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                })?;
                rows.map(|row| {
                    row.map(|(seq, kind, text)| {
                        (
                            seq,
                            kind,
                            serde_json::from_str(&text).unwrap_or(Value::Null),
                        )
                    })
                })
                .collect()
            })
            .unwrap()
    }

    #[tokio::test]
    async fn a_drained_batch_keeps_its_arrival_order_across_kinds() {
        let (url, _sent) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
        for kind in [
            "delta",
            "tool",
            "message",
            "tokens",
            "delta",
            "diagnostic",
            "status",
            "delta",
        ] {
            tx.send(json!({"kind": kind, "thread_id": "th-1", "turn_id": "turn-1", "text": format!("{kind} text")})).unwrap();
        }
        tx.send(json!({"kind": "turn_completed", "thread_id": "th-1", "turn_id": "turn-1", "status": "completed"})).unwrap();
        let (drove, _, _) = drive_with(
            &runner,
            &host,
            &mut rx,
            &flags(true),
            "th-1",
            "claude",
            None,
            BTreeMap::new(),
        )
        .await;
        assert_eq!(
            drove
                .unwrap_or_else(|e| panic!("refused: {}", e.text))
                .outcome,
            "completed"
        );
        let rows = stored_rows(&runner, "acme-late");
        let kinds: Vec<(i64, &str)> = rows
            .iter()
            .map(|(seq, kind, _)| (*seq, kind.as_str()))
            .collect();
        assert_eq!(
            kinds,
            vec![
                (1, "delta"),
                (2, "tool"),
                (3, "message"),
                (4, "tokens"),
                (5, "delta"),
                (6, "diagnostic"),
                (7, "status"),
                (8, "delta")
            ]
        );
        assert_eq!(rows[2].2["text"], "message text");
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn an_acknowledged_interruption_is_stored_after_the_rows_drained_before_it() {
        let (url, _sent) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
        for text in ["first", "second"] {
            tx.send(
                json!({"kind": "delta", "thread_id": "th-1", "turn_id": "turn-1", "text": text}),
            )
            .unwrap();
        }
        tx.send(json!({"kind": "turn_completed", "thread_id": "th-1", "turn_id": "turn-1", "status": "interrupted"})).unwrap();
        let (drove, _, _) = drive_with(
            &runner,
            &host,
            &mut rx,
            &flags(true),
            "th-1",
            "claude",
            None,
            BTreeMap::new(),
        )
        .await;
        assert_eq!(
            drove
                .unwrap_or_else(|e| panic!("refused: {}", e.text))
                .outcome,
            "interrupted"
        );
        let rows = stored_rows(&runner, "acme-late");
        let kinds: Vec<(i64, &str)> = rows
            .iter()
            .map(|(seq, kind, _)| (*seq, kind.as_str()))
            .collect();
        assert_eq!(kinds, vec![(1, "delta"), (2, "delta"), (3, "diagnostic")]);
        assert_eq!(
            (
                rows[1].2["text"].as_str(),
                rows[2].2["interruption_complete"].as_bool()
            ),
            (Some("second"), Some(true))
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_failed_drain_write_ends_the_drive_with_the_store_error_and_keeps_only_the_rows_before_it(
    ) {
        let (url, _sent) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        runner.state.record(&json!({"id": "acme-late"})).unwrap();
        runner
            .state
            .append("acme-late", "status", &json!({"yielded": "kept"}))
            .unwrap();
        runner.state.with(|c| c.execute_batch("CREATE TRIGGER refuse_delta BEFORE INSERT ON events WHEN NEW.kind = 'delta' BEGIN SELECT RAISE(ABORT, 'planted failure'); END;")).unwrap();
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
        tx.send(json!({"kind": "delta", "thread_id": "th-1", "turn_id": "turn-1", "text": "lost"}))
            .unwrap();
        tx.send(json!({"kind": "tool", "thread_id": "th-1", "turn_id": "turn-1", "name": "ls"}))
            .unwrap();
        tx.send(json!({"kind": "turn_completed", "thread_id": "th-1", "turn_id": "turn-1", "status": "completed"})).unwrap();
        let (drove, _, _) = drive_with(
            &runner,
            &host,
            &mut rx,
            &flags(true),
            "th-1",
            "claude",
            None,
            BTreeMap::new(),
        )
        .await;
        let Err(e) = drove else {
            panic!("the drain's write should have failed the drive")
        };
        assert!(e.text.starts_with("runner.sqlite:"), "{}", e.text);
        let kinds: Vec<String> = stored_rows(&runner, "acme-late")
            .into_iter()
            .map(|(_, kind, _)| kind)
            .collect();
        assert_eq!(kinds, vec!["status".to_string()]);
        std::fs::remove_dir_all(dir).ok();
    }

    /// Bench, not run by CI: a 3,000-delta turn whose events are all queued before the drive starts, so one drain
    /// gathers them. Wall time (median of 50 runs) and event-write transactions per run.
    #[tokio::test]
    #[ignore]
    async fn bench_3000_queued_deltas_through_the_drive() {
        let (mut micros, mut commits) = (Vec::new(), Vec::new());
        for _ in 0..50 {
            let (url, _seen) = lapsed_hub().await;
            let (runner, dir) = runner_at(&url);
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
            let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
            for i in 0..3000 {
                tx.send(json!({"kind": "delta", "thread_id": "th-1", "turn_id": "turn-1", "text": format!("token {i} of the answer ")})).unwrap();
            }
            tx.send(json!({"kind": "turn_completed", "thread_id": "th-1", "turn_id": "turn-1", "status": "completed"})).unwrap();
            runner.state.record(&json!({"id": "acme-late"})).unwrap();
            let (f, wake) = (flags(true), Notify::new());
            let d = Drive {
                limit_name: "max_run_minutes (60 minutes)",
                aid: "acme-late",
                host: &host,
                thread: "th-1",
                effort: None,
                limit: Instant::now() + Duration::from_secs(3600),
                flags: &f,
                wake: &wake,
                redactor: None,
                runtime: "claude",
                bot: "acme",
                session: "th-1",
                fresh: None,
            };
            let mut segment = Segment {
                meter: Meter::default(),
                config: json!({}),
                profile: None,
                billing: "api",
                subscription: None,
            };
            let mut goal = GoalRun {
                chat_goal: None,
                controlled: false,
                failure: None,
                paused: None,
                tries_key: ("acme".into(), "th-1".into()),
            };
            let (before, began) = (crate::state::APPENDS.load(Ordering::SeqCst), Instant::now());
            let drove = runner
                .drive(
                    &d,
                    &mut rx,
                    "turn-1".into(),
                    &mut segment,
                    &mut BTreeMap::new(),
                    &mut None,
                    &mut goal,
                )
                .await;
            let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
            micros.push(began.elapsed().as_secs_f64() * 1e6);
            commits.push(crate::state::APPENDS.load(Ordering::SeqCst) - before);
            assert_eq!(drove.outcome, "completed");
            let stored = runner.state.with(|c| c.query_row("SELECT count(*) FROM events WHERE attempt_id = 'acme-late' AND kind = 'delta'", [], |r| r.get::<_, i64>(0))).unwrap();
            assert_eq!(stored, 3000);
            std::fs::remove_dir_all(dir).ok();
        }
        micros.sort_by(|a, b| a.total_cmp(b));
        commits.sort_unstable();
        eprintln!("3,000 queued deltas through drive: wall time median {:.1} ms (min {:.1}, max {:.1}); event-write transactions median {} (min {}, max {})",
            micros[25] / 1e3, micros[0] / 1e3, micros[49] / 1e3, commits[25], commits[0], commits[49]);
    }

    /// A chat turn on a hub whose events uploads take `uploads` to answer and whose inputs reads take `asks` (a write on
    /// the hub, behind its write lock), with the outbox uploader running as in a real attempt: a status line goes up
    /// as the harness starts, the model's first text comes `first` later, and the turn ends `end` after that. Returns
    /// how long after the first text the hub was sent it, and how long after the turn's end the drive returned.
    async fn slow_hub_turn(
        uploads: Duration,
        asks: Duration,
        first: Duration,
        end: Duration,
    ) -> (Duration, Duration) {
        let sent_at: Arc<Mutex<Vec<(Instant, String)>>> = Arc::new(Mutex::new(vec![]));
        let log = sent_at.clone();
        let delay = move |path: &str| {
            if path.ends_with("/inputs") {
                asks
            } else {
                uploads
            }
        };
        let (url, _seen) = hub_answering_after(delay, move |path, body| {
            log.lock()
                .unwrap()
                .push((Instant::now(), format!("{path} {body}")));
            if path.ends_with("/inputs") {
                ("200 OK", json!({"attempt_id": "acme-slow", "messages": []}))
            } else {
                events_taken(body)
            }
        })
        .await;
        let (runner, dir) = runner_at(&url);
        let runner = Arc::new(runner);
        runner
            .events
            .dispatch("ready", None, &json!({"seq": 1, "release": "v1"}), false);
        runner.state.record(&json!({"id": "acme-slow"})).unwrap();
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        assert!(host.supports_steer());
        let f = Arc::new(flags(false));
        let wake = Arc::new(Notify::new());
        let streamer = tokio::spawn(runner.clone().stream_loop(
            "acme-slow".into(),
            wake.clone(),
            f.clone(),
        ));
        let started = Instant::now();
        tx.send(json!({"kind": "status", "thread_id": "th-slow", "turn_id": "turn-1", "text": "Starting"}))
            .unwrap();
        let feed = tokio::spawn(async move {
            tokio::time::sleep(first).await;
            let token = Instant::now();
            tx.send(json!({"kind": "delta", "thread_id": "th-slow", "turn_id": "turn-1", "text": "Acme's"}))
                .unwrap();
            tokio::time::sleep(end).await;
            let ended = Instant::now();
            for ev in answer_events("th-slow", "turn-1") {
                tx.send(ev).unwrap();
            }
            (token, ended)
        });
        let d = Drive {
            limit_name: "max_run_minutes (60 minutes)",
            aid: "acme-slow",
            host: &host,
            thread: "th-slow",
            effort: None,
            limit: started + Duration::from_secs(60),
            flags: &f,
            wake: &wake,
            redactor: None,
            runtime: "claude",
            bot: "acme",
            session: "th-slow",
            fresh: None,
        };
        let mut segment = Segment {
            meter: Meter::default(),
            config: json!({}),
            profile: None,
            billing: "api",
            subscription: None,
        };
        let mut goal = GoalRun {
            chat_goal: None,
            controlled: false,
            failure: None,
            paused: None,
            tries_key: ("acme".into(), "th-slow".into()),
        };
        let drove = runner
            .drive(
                &d,
                &mut rx,
                "turn-1".into(),
                &mut segment,
                &mut BTreeMap::new(),
                &mut None,
                &mut goal,
            )
            .await
            .unwrap_or_else(|e| panic!("refused: {}", e.text));
        let returned = Instant::now();
        assert_eq!(drove.outcome, "completed");
        let (token, ended) = feed.await.unwrap();
        // Everything still in the outbox goes up, as the attempt's final flush sends it.
        runner.flush("acme-slow").await.unwrap();
        f.done.store(true, Ordering::SeqCst);
        wake.notify_one();
        streamer.await.unwrap();
        let first_sent = sent_at
            .lock()
            .unwrap()
            .iter()
            .find(|(_, r)| r.contains("/events") && r.contains("\"delta\""))
            .map(|(at, _)| *at)
            .expect("the first text went up");
        std::fs::remove_dir_all(dir).ok();
        (
            first_sent.saturating_duration_since(token),
            returned.saturating_duration_since(ended),
        )
    }

    #[tokio::test]
    async fn a_slow_inputs_read_does_not_hold_back_the_first_text() {
        // The turn's first step reads its inputs at once, and that read waits 2 s for the hub's write lock. The model's
        // first text, 300 ms in, goes up as soon as it is said, not once the read is answered.
        let (first, _) = slow_hub_turn(
            Duration::from_millis(50),
            Duration::from_secs(2),
            Duration::from_millis(300),
            Duration::from_millis(500),
        )
        .await;
        assert!(
            first < Duration::from_secs(1),
            "first text sent {first:?} after the model said it"
        );
    }

    /// Bench, not run by CI: model-first-text to the hub, and turn end to the drive's return, on hubs slow to answer
    /// uploads, inputs reads or both. `cargo test --release bench_slow_hub -- --ignored --nocapture`.
    #[tokio::test]
    #[ignore]
    async fn bench_slow_hub_first_text_and_turn_end() {
        for (uploads, asks) in [(0u64, 0u64), (50, 2000), (1000, 50), (2000, 2000)] {
            let (first, end) = slow_hub_turn(
                Duration::from_millis(uploads),
                Duration::from_millis(asks),
                Duration::from_millis(300),
                Duration::from_millis(2900),
            )
            .await;
            // first_delta_posted is marked when the hub answers that upload: `uploads` after it was sent.
            println!(
                "uploads {uploads} ms, inputs reads {asks} ms: first text sent +{} ms, first_delta_posted +{} ms, turn end seen +{} ms",
                first.as_millis(),
                first.as_millis() + u128::from(uploads),
                end.as_millis()
            );
        }
    }
}
