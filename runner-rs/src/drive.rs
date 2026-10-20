//! The drive loop (service.py execute's `drive`): drain the host until the turn ends, with native goals
//! (task_goals_v1) worked the way the Python runner works them. A Codex goal continues across turns the app
//! server starts itself; a Claude goal is judged in the stream and the transcript. A goal that keeps going
//! without progress is paused after its tries; a person's goal control mid-run settles the run.

use std::collections::{BTreeMap, HashSet};
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

/// Goal continuations per (bot, session key) thread, for the runner's life (service.py goal_tries).
pub fn tries() -> &'static goals::Tries {
    static T: std::sync::OnceLock<goals::Tries> = std::sync::OnceLock::new();
    T.get_or_init(Default::default)
}

pub fn from_person(message: &Value) -> bool {
    text_of(&message["from_actor"]).starts_with("human:")
}

/// One metered stretch of a turn: the primary harness, then a fallback's.
pub struct Segment {
    pub meter: Meter,
    pub config: Value,
    pub profile: Option<String>,
    pub billing: &'static str,
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
    pub flags: &'a Flags,
    pub wake: &'a Notify,
    pub redactor: Option<&'a Arc<Redactor>>,
    /// The runtime this stretch runs on (a fallback's after a hop).
    pub runtime: &'a str,
    pub bot: &'a str,
    /// The provider thread's session key (execute::session_key), for commands a turn leaves running.
    pub session: &'a str,
}

impl Runner {
    #[allow(clippy::too_many_arguments)]
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
        // A host whose native goal keeps running across turns (`goal_runs_across_turns`): a completed turn is not
        // the end of the run, and a clear it reports is checked against the goal.
        let across_turns = crate::harness_tools::caps(d.runtime, "").goal_runs_across_turns;
        let redact = |v: &Value| d.redactor.map(|r| r.scrub_json(v)).unwrap_or_else(|| v.clone());
        let scrub = |t: &str| d.redactor.map(|r| r.scrub_text(t)).unwrap_or_else(|| t.to_string());
        let (mut reply, mut tokens, mut outcome) = (String::new(), json!({}), "interrupted".to_string());
        let (mut limited, mut retryable, mut acted, mut complete) = (false, false, false, false);
        let mut earlier = String::new();
        let mut earlier_tokens: Option<Value> = None;
        let mut last_flush: Option<Instant> = None;
        let mut first_output = false;
        let mut queue: Vec<Value> = vec![];
        let answer = |earlier: &str, reply: &str| [earlier, reply].iter().filter(|t| !t.is_empty()).cloned().collect::<Vec<_>>().join("\n\n");
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
        let revision = chat_goal.as_ref().map_or(Value::Null, |g| g["updated_at"].clone());
        let goal_id = chat_goal.as_ref().map_or(Value::Null, |g| g["id"].clone());
        let mut tried: HashSet<String> = HashSet::new();
        let mut pausing: Option<String> = None;
        let (mut pause_ready, mut pause_at, mut clearing) = (false, Instant::now(), false);

        // Count a continuation; past the cap, interrupt the turn so its goal can be cleared.
        macro_rules! stuck {
            ($note:expr, $cap:expr) => {{
                let note: String = $note;
                if pausing.is_none() && !clearing && tries().tried(&goal.tries_key, &note, $cap) {
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

        while !complete {
            if d.flags.stopped.load(Ordering::SeqCst) {
                goal.failure = Some("Stopped by a person".into());
                let _ = host.interrupt(thread, &turn).await;
                return Ok(Drove { outcome: "interrupted".into(), reply: answer(&earlier, &reply), tokens: spent(&earlier_tokens, &tokens), limited: false, retryable: false, turn });
            }
            let late = Instant::now() >= d.limit;
            if self.stopping() || d.flags.lost.load(Ordering::SeqCst) || late {
                goal.failure = Some(if late { "Run time limit reached" } else if self.stopping() { "The computer stopped" } else { "The execution lease expired" }.into());
                let _ = host.interrupt(thread, &turn).await;
                return Err(Fail::runtime("Execution interrupted after stop, run-time limit, or loss of ownership"));
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
                let event_turn = text_of(&event["turn_id"]);
                if goal_running && kind == "status" && !event_turn.is_empty() {
                    let fresh = event_turn != turn;
                    turn = event_turn.clone();
                    if fresh {
                        // Codex starts a new turn for each continuation of an unmet goal.
                        let last = reply.trim().rsplit("\n\n").next().unwrap_or_default().to_string();
                        stuck!(scrub(&last), goals::MAX_CODEX_GOAL_TRIES);
                    }
                }
                if !event_turn.is_empty() && event_turn != turn && !goal_running {
                    continue;
                }
                if kind == "rate_limits" && segment.billing == "subscription" {
                    // No profile: the computer's own login ("").
                    let profile = segment.profile.as_deref().unwrap_or_default();
                    crate::profiles::remember_weekly(profile, &text_of(&segment.config["runtime"]), &event);
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
                        if across_turns && goal_running {
                            event = host.reconcile_codex_clear(thread, event, &text_of(&g["objective"])).await?;
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
                        let mut row = redact(&event);
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
                    let deadline = crate::util::epoch_s() + d.limit.saturating_duration_since(Instant::now()).as_secs_f64();
                    crate::afterwatch::keep(&self.state.directory, aid, d.bot, d.session, deadline, &event, d.redactor);
                    continue;
                }
                if kind == "diagnostic" && event["background_wait"].is_object() {
                    // What ending the wait saved: the rest of the run's time, which the wait would have held.
                    event["background_wait"]["minutes_saved"] = json!(d.limit.saturating_duration_since(Instant::now()).as_secs() / 60);
                }
                if ["delta", "message", "tokens", "status", "error", "tool", "diagnostic", "turn_failed"].contains(&kind.as_str()) {
                    let clean = redact(&event);
                    if !first_output && (kind == "delta" || kind == "message") {
                        first_output = true;
                        mark("first_output", aid);
                    }
                    let (id, saved, row) = (aid.to_string(), if kind == "turn_failed" { "error".to_string() } else { kind.clone() }, clean.clone());
                    self.state.off(move |s| s.append(&id, &saved, &row)).await?;
                    d.wake.notify_one();
                    if kind == "message" && (truthy(event.get("final")) || reply.is_empty()) {
                        reply = text_of(&clean["text"]);
                    }
                    if kind == "tokens" {
                        tokens = event.clone();
                        segment.meter.add(&event);
                    }
                }
                if kind == "turn_completed" {
                    outcome = if event["status"] == "interrupted" { "interrupted".into() } else { "completed".into() };
                    complete = !(goal_running && across_turns && outcome == "completed");
                } else if kind == "turn_failed" || (kind == "error" && truthy(event.get("host_restart"))) {
                    outcome = "failed".into();
                    let error = text_of(&event["error"]);
                    goal.failure = Some(if error.is_empty() { "The harness stopped".into() } else { scrub(&error) });
                    limited = truthy(event.get("limit"));
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
            if let Some(p) = pausing.clone() {
                if pause_ready || pause_at.elapsed() >= Duration::from_secs_f64(goals::GOAL_PAUSE_WAIT_S) || host.active_turn(thread).as_deref() != Some(p.as_str()) {
                    pausing = None;
                    clearing = true;
                    match host.start_goal(thread, "pause", "", d.effort).await {
                        Ok(t) => {
                            turn = t;
                            outcome = "interrupted".into();
                        }
                        Err(e) => {
                            self.state.append(aid, "diagnostic", &json!({"text": format!("The goal could not be cleared: {e}")}))?;
                            outcome = "completed".into();
                            complete = true;
                        }
                    }
                }
            }
            if complete && outcome == "completed" && !carried.is_empty() && !goal.controlled {
                // Not on a failed or stopped turn: those settle the attempt, inputs included.
                turn = self.carry_inputs(aid, host, thread, carried, d.effort).await?;
                outcome = "interrupted".into();
                complete = false;
                earlier = answer(&earlier, &reply);
                reply.clear();
                earlier_tokens = Some(spent(&earlier_tokens, &tokens));
                tokens = json!({});
            }
            if last_flush.is_none_or(|t| t.elapsed() >= Duration::from_secs(1)) {
                let step: Result<(), Fail> = async {
                    host.poll_goal(thread);
                    self.flush(aid).await?;
                    if goal_running {
                        let latest = self.client.get(&format!("attempts/{aid}/goal")).await?;
                        let latest = &latest["goal"];
                        if !latest.is_object() || latest["id"] != goal_id || latest["updated_at"] != revision {
                            goal.controlled = true;
                            let _ = host.interrupt(thread, &turn).await;
                            // An explicit goal control settles this run so its queued successor can claim.
                            goal_running = false;
                            complete = true;
                            outcome = "completed".into();
                        }
                    }
                    if !complete && host.supports_steer() {
                        let messages = self.receive_inputs(aid, host, thread, &turn, carried).await?;
                        if messages.iter().any(from_person) {
                            tries().take(&goal.tries_key);
                        }
                    }
                    Ok(())
                }
                .await;
                if let Err(e) = step {
                    if e.api.as_ref().is_none_or(|a| !a.retryable) {
                        return Err(e);
                    }
                }
                last_flush = Some(Instant::now());
            }
            if !goal_running && pausing.is_none() && outcome == "completed" {
                complete = true;
            }
            if complete {
                break;
            }
            if !host.alive() {
                // Whatever it said before it went is read first.
                while let Ok(ev) = rx.try_recv() {
                    queue.push(ev);
                }
                if queue.is_empty() {
                    return Err(Fail::runtime("Local runtime exited unexpectedly"));
                }
                continue;
            }
            if let Ok(Some(ev)) = tokio::time::timeout(Duration::from_millis(100), rx.recv()).await {
                queue.push(ev);
            }
        }
        let mut text = answer(&earlier, &reply);
        if let Some(p) = &goal.paused {
            text = if text.is_empty() { p.clone() } else { format!("{text}\n\n{p}") };
        }
        Ok(Drove { outcome, reply: text, tokens: spent(&earlier_tokens, &tokens), limited, retryable, turn })
    }
}
