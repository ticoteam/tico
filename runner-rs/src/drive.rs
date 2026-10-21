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

/// How long a turn whose lease is lost keeps reading what its harness already said. A model that finished while this
/// computer was asleep has its whole answer waiting in the pipe; one still working is not waited for.
const SALVAGE: Duration = if cfg!(test) { Duration::from_millis(500) } else { Duration::from_secs(10) };

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
        // Set once the lease is lost: until then the turn's remaining output is read, and nothing more is asked of the hub.
        let mut salvage: Option<Instant> = None;
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

        while !complete {
            if d.flags.stopped.load(Ordering::SeqCst) {
                goal.failure = Some("Stopped by a person".into());
                let _ = host.interrupt(thread, &turn).await;
                return Ok(Drove { outcome: "interrupted".into(), reply: answer(&earlier, &reply), tokens: spent(&earlier_tokens, &tokens), limited: false, retryable: false, turn });
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
                    // A goal that runs across turns is not continued once the lease is lost.
                    complete = salvage.is_some() || !(goal_running && across_turns && outcome == "completed");
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
            if pausing.is_some() && salvage.is_some() {
                // A pause that was waiting when the lease went: its goal is not cleared in a new turn. The interrupted
                // turn's answer, with the pause note, is the result once that turn has ended.
                if pause_ready {
                    pausing = None;
                    outcome = "completed".into();
                    complete = true;
                }
            } else if let Some(p) = pausing.clone() {
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
            if complete && outcome == "completed" && !carried.is_empty() && !goal.controlled && salvage.is_none() {
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
                    if salvage.is_some() {
                        return Ok(()); // the hub refuses this attempt's goal and inputs now; its events and result it keeps
                    }
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
                    if e.api.as_ref().is_some_and(|a| a.code == "stale_lease") {
                        // Refused before the renewer heard: the same lost lease, read out the same way.
                        d.flags.lost.store(true, Ordering::SeqCst);
                    } else if e.api.as_ref().is_none_or(|a| !a.retryable) {
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
                if !queue.is_empty() {
                    continue;
                }
                if salvage.is_none() {
                    return Err(Fail::runtime("Local runtime exited unexpectedly"));
                }
            }
            match tokio::time::timeout(Duration::from_millis(100), rx.recv()).await {
                Ok(Some(ev)) => queue.push(ev),
                Ok(None) => tokio::time::sleep(Duration::from_millis(100)).await, // no sender left; the window ends it
                Err(_) => {}
            }
        }
        let mut text = answer(&earlier, &reply);
        if let Some(p) = &goal.paused {
            text = if text.is_empty() { p.clone() } else { format!("{text}\n\n{p}") };
        }
        Ok(Drove { outcome, reply: text, tokens: spent(&earlier_tokens, &tokens), limited, retryable, turn })
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
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(vec![]));
        let kept = seen.clone();
        tokio::spawn(async move {
            while let Ok((mut socket, _)) = listener.accept().await {
                let kept = kept.clone();
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
                    let length = text.lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
                    while request.len() < head + 4 + length {
                        let n = socket.read(&mut buffer).await.unwrap_or(0);
                        if n == 0 {
                            break;
                        }
                        request.extend_from_slice(&buffer[..n]);
                    }
                    let path = text.lines().next().unwrap_or_default().split_whitespace().nth(1).unwrap_or_default().to_string();
                    let body: Value = serde_json::from_slice(&request[head + 4..]).unwrap_or(Value::Null);
                    kept.lock().unwrap().push(format!("{path} {body}"));
                    let (status, answer) = if ["/renew", "/inputs", "/goal"].iter().any(|p| path.ends_with(p)) || path.contains("/inputs/") {
                        ("409 Conflict", json!({"error": {"code": "stale_lease", "detail": "Execution ownership expired; stop this attempt", "retryable": false}}))
                    } else {
                        ("200 OK", json!({"ack_seq": body["events"].as_array().and_then(|e| e.last()).map_or(json!(0), |l| l["seq"].clone())}))
                    };
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
        let dir = std::env::temp_dir().join(format!("tico-salvage-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")}).to_string()).unwrap();
        let config = crate::config::Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        (Runner::new(config, client, crate::state::State::open(&dir.join("state")).unwrap()), dir)
    }

    fn flags(lost: bool) -> Flags {
        Flags { lost: AtomicBool::new(lost), stopped: AtomicBool::new(false), done: AtomicBool::new(false),
                done_notify: Notify::new(), deadline: Mutex::new(Instant::now()) }
    }

    /// The model's last words for `turn` on `thread`, the way the Claude host reports a finished turn.
    fn answer_events(thread: &str, turn: &str) -> Vec<Value> {
        vec![
            json!({"kind": "message", "thread_id": thread, "turn_id": turn, "text": "Acme's renewal summary is ready.", "final": true}),
            json!({"kind": "turn_completed", "thread_id": thread, "turn_id": turn, "status": "completed"}),
        ]
    }

    /// Drive one Acme turn on `host` against `runner`; `feed` gets the host's event sender.
    async fn drive_turn(runner: &Runner, host: &Host, rx: &mut UnboundedReceiver<Value>, f: &Flags, thread: &str) -> Result<Drove, Fail> {
        drive_with(runner, host, rx, f, thread, None, BTreeMap::new()).await.0
    }

    /// `drive_turn` with a Tico chat goal and inputs a steer refused, handing back what is still carried after.
    async fn drive_with(runner: &Runner, host: &Host, rx: &mut UnboundedReceiver<Value>, f: &Flags, thread: &str, chat_goal: Option<Value>,
                        mut carried: BTreeMap<String, Value>) -> (Result<Drove, Fail>, BTreeMap<String, Value>, GoalRun) {
        runner.state.record(&json!({"id": "acme-late"})).unwrap();
        let wake = Notify::new();
        let d = Drive { aid: "acme-late", host, thread, effort: None, limit: Instant::now() + Duration::from_secs(60),
                        flags: f, wake: &wake, redactor: None, runtime: "claude" };
        let mut segment = Segment { meter: Meter::default(), config: json!({}), profile: None, billing: "api" };
        let mut goal = GoalRun { chat_goal, controlled: false, failure: None, paused: None, tries_key: ("acme".into(), thread.into()) };
        let mut rejected = None;
        let drove = tokio::time::timeout(Duration::from_secs(10), runner.drive(&d, rx, "turn-1".into(), &mut segment, &mut carried, &mut rejected, &mut goal))
            .await
            .expect("the drive ends");
        (drove, carried, goal)
    }

    #[tokio::test]
    async fn an_answer_that_finished_while_the_computer_slept_is_handed_in_completed_after_the_renewal_is_refused() {
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
        let drove = drive_turn(&runner, &host, &mut rx, &f, "th-1").await.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!((drove.outcome.as_str(), drove.reply.as_str()), ("completed", "Acme's renewal summary is ready."));
        assert!(!seen.lock().unwrap().iter().any(|p| p.contains("/inputs") || p.contains("/goal ")), "nothing more was asked of the hub");
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
            while !watched.lock().unwrap().iter().any(|p| p.contains("/inputs ")) {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            for ev in answer_events("th-2", "turn-1") {
                tx.send(ev).unwrap();
            }
        });
        let f = flags(false);
        let drove = drive_turn(&runner, &host, &mut rx, &f, "th-2").await.unwrap_or_else(|e| panic!("refused: {}", e.text));
        feed.await.unwrap();
        assert_eq!(drove.outcome, "completed");
        assert_eq!(drove.reply, "Acme's renewal summary is ready.");
        assert!(f.lost.load(Ordering::SeqCst), "the refusal counts as a lost lease");
        assert_eq!(seen.lock().unwrap().iter().filter(|p| p.contains("/inputs ")).count(), 1, "inputs are asked for once, not after the refusal");
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
        let err = drive_turn(&runner, &host, &mut rx, &f, "th-3").await.err().expect("not completed");
        assert!(err.text.contains("loss of ownership"), "{}", err.text);
        assert!(began.elapsed() >= SALVAGE && began.elapsed() < SALVAGE + Duration::from_secs(3), "{:?}", began.elapsed());
        drop(tx);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_harness_that_exited_before_its_reader_drained_the_pipe_still_hands_in_its_answer() {
        // R1: Codex's reaper marks the process gone before its stdout reader has decoded the last lines. Exit first,
        // the finished answer a moment later: it is still read out.
        let (url, _) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone())); // not alive: the process is gone
        let feed = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(300)).await;
            for ev in answer_events("th-4", "turn-1") {
                tx.send(ev).unwrap();
            }
        });
        let f = flags(true);
        let drove = drive_turn(&runner, &host, &mut rx, &f, "th-4").await.unwrap_or_else(|e| panic!("refused: {}", e.text));
        feed.await.unwrap();
        assert_eq!((drove.outcome.as_str(), drove.reply.as_str()), ("completed", "Acme's renewal summary is ready."));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_lost_lease_with_the_harness_gone_and_nothing_left_to_read_ends_within_the_window() {
        let (url, _) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let host = Host::Claude(ClaudeHost::new("acme", vec![], tx.clone()));
        let (f, began) = (flags(true), Instant::now());
        let err = drive_turn(&runner, &host, &mut rx, &f, "th-5").await.err().expect("not completed");
        assert!(err.text.contains("loss of ownership"), "{}", err.text);
        assert!(began.elapsed() < SALVAGE + Duration::from_secs(1), "{:?}", began.elapsed());
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
        let carried: BTreeMap<String, Value> = [("m-1".to_string(), json!({"id": "m-1", "body": "Also add Q3.", "from_actor": "human:ana"}))].into();
        let f = flags(true);
        let (drove, left, _) = drive_with(&runner, &host, &mut rx, &f, "th-6", None, carried).await;
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!((drove.outcome.as_str(), drove.reply.as_str()), ("completed", "Acme's renewal summary is ready."));
        assert_eq!(left.keys().collect::<Vec<_>>(), ["m-1"], "still carried, so the result reports it undelivered");
        assert!(!seen.lock().unwrap().iter().any(|p| p.contains("/inputs")), "nothing acknowledged");
        std::fs::remove_dir_all(dir).ok();
    }

    fn acme_goal() -> Value {
        json!({"id": "g-1", "status": "active", "objective": "Finish the Acme renewal summary", "updated_at": "2026-10-21T04:00:00Z"})
    }

    #[tokio::test]
    async fn a_goal_read_refused_before_the_renewer_reads_out_the_answer_and_asks_nothing_more() {
        let (url, seen) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(true);
        let host = Host::Claude(claude);
        let watched = seen.clone();
        let feed = tokio::spawn(async move {
            while !watched.lock().unwrap().iter().any(|p| p.contains("/goal ")) {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            tokio::time::sleep(Duration::from_millis(300)).await;
            for ev in answer_events("th-7", "turn-1") {
                tx.send(ev).unwrap();
            }
        });
        let f = flags(false);
        let (drove, _, _) = drive_with(&runner, &host, &mut rx, &f, "th-7", Some(acme_goal()), BTreeMap::new()).await;
        feed.await.unwrap();
        assert_eq!(drove.unwrap_or_else(|e| panic!("refused: {}", e.text)).outcome, "completed");
        assert!(f.lost.load(Ordering::SeqCst));
        let asked = seen.lock().unwrap().iter().filter(|p| p.contains("/goal ") || p.contains("/inputs")).count();
        assert_eq!(asked, 1, "one goal read, refused; no inputs or goal after");
        std::fs::remove_dir_all(dir).ok();
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
    async fn a_goal_reaching_its_cap_in_the_buffered_output_starts_no_pause_after_the_lease_is_lost() {
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
        let (drove, _, goal) = drive_with(&runner, &host, &mut rx, &f, "th-8", None, BTreeMap::new()).await;
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!((drove.outcome.as_str(), drove.reply.as_str()), ("completed", "Acme's renewal summary is ready."));
        assert!(goal.paused.is_none(), "no pause, so no /goal clear turn");
        let said = format!("{:?} {:?}", seen.lock().unwrap(), runner.state.pending("acme-late").unwrap());
        assert!(!said.contains("could not be cleared"), "{said}");
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_pause_waiting_when_the_lease_is_lost_ends_with_the_answer_and_starts_no_clear_turn() {
        let (url, seen) = lapsed_hub().await;
        let (runner, dir) = runner_at(&url);
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let claude = ClaudeHost::new("acme", vec![], tx.clone());
        claude.pretend_up(false);
        claude.pretend_turn("th-9", "turn-1"); // the harness still runs the turn being paused
        let host = Host::Claude(claude);
        let mut events = stuck_goal_events("th-9");
        let completed = events.pop().unwrap();
        for ev in events {
            tx.send(ev).unwrap();
        }
        let f = Arc::new(flags(false));
        let (lose, feed) = (f.clone(), tx.clone());
        let later = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(300)).await; // the pause is waiting on the interrupted turn
            lose.lost.store(true, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(200)).await;
            feed.send(json!({"kind": "turn_completed", "thread_id": "th-9", "turn_id": "turn-1", "status": "interrupted"})).unwrap();
            drop(completed);
        });
        let began = Instant::now();
        let (drove, _, goal) = drive_with(&runner, &host, &mut rx, &f, "th-9", None, BTreeMap::new()).await;
        later.await.unwrap();
        let drove = drove.unwrap_or_else(|e| panic!("refused: {}", e.text));
        assert_eq!(drove.outcome, "completed");
        assert!(drove.reply.starts_with("Acme's renewal summary is ready.") && drove.reply.contains("Goal paused after"), "{}", drove.reply);
        assert!(goal.paused.is_some());
        assert!(began.elapsed() < SALVAGE + Duration::from_secs(1), "bounded: {:?}", began.elapsed());
        let said = format!("{:?} {:?}", seen.lock().unwrap(), runner.state.pending("acme-late").unwrap());
        assert!(!said.contains("could not be cleared"), "no /goal clear turn was tried: {said}");
        std::fs::remove_dir_all(dir).ok();
    }
}
