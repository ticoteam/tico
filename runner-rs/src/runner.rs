//! The run loop (`Runner.run`, `tick`, `maintain`, `recover_output` in runner/service.py): heartbeat with
//! readiness, claims on the event stream's word, recovery of results a restart left behind, and one tokio
//! task per turn (runner-rs/src/execute.rs).

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};
use tokio::task::JoinHandle;

use crate::client::{ApiError, Client};
use crate::config::{machine_capacity, Config};
use crate::events::{Events, BACKUP_POLL_S, HEARTBEAT_LIVE_S};
use crate::gitcred::{CredentialSocket, TokenCache};
use crate::readiness;
use crate::state::State;
use crate::util::{log, mark, text_of};

pub const RUNNER_VERSION: &str = "0.5.4";
const CLAIM_EVERY: f64 = 0.25;
const CLAIM_IDLE_MAX: f64 = 2.0;
const CLAIM_HINT_S: f64 = 150.0;
/// Keys whose values move on every report without anything having changed (service.py VOLATILE).
const VOLATILE: &[&str] = &["free_bytes", "checked_at", "reported_at", "at", "mono", "server_time"];

pub struct Runner {
    pub config: Config,
    pub client: Client,
    pub state: State,
    pub events: Arc<Events>,
    pub stop: AtomicBool,
    pub capacity: usize,
    pub tokens: Arc<TokenCache>,
    pub socket: Option<CredentialSocket>,
    /// Attempts with a live worker here; recovery leaves them alone.
    pub active: Mutex<HashSet<String>>,
    /// One push or history publish at a time on this computer.
    pub push_lock: tokio::sync::Mutex<()>,
    pub sign_in: crate::signin::SignIn,
    /// The last history-publish failure per bot, so it is logged when it changes.
    pub publish_notes: Mutex<HashMap<String, String>>,
    pub attempt_bots: Mutex<HashMap<String, String>>,
    pub runtime_rows: Mutex<Map<String, Value>>,
    names: Mutex<Option<Value>>,
    /// Lane and checkout locks, live turns' lanes, the repositories this computer holds (sessions.rs).
    pub lanes: Arc<crate::sessions::Lanes>,
    pub worktrees: Arc<crate::worktrees::Worktrees>,
    /// The server takes `record_updated` on worktree reports (its heartbeat reply said so).
    worktree_stamp: AtomicBool,
    flush_locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
    /// Attempts whose first streamed text has reached the server (one timing mark each).
    pub first_posted: Mutex<HashSet<String>>,
    assignments: Mutex<Vec<Value>>,
    published: Mutex<HashMap<String, Option<(i128, u64)>>>,
    beat: Mutex<Option<(String, Instant)>>,
    restart: AtomicBool,
    /// Persistent Antigravity processes kept between a conversation's turns.
    pub warm: crate::warm::WarmSessions,
    /// What each bot's bot.yaml adds: tools, MCP servers, watchers, memory history (crate::perbot).
    pub perbot: crate::perbot::PerBot,
}

pub fn platform() -> &'static str {
    match std::env::consts::OS {
        "macos" => "darwin",
        other => other,
    }
}

/// The value without its volatile keys, with object keys sorted, as `json.dumps(steady(v), sort_keys=True)`.
fn steady(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().filter(|k| !VOLATILE.contains(&k.as_str())).collect();
            keys.sort();
            Value::Object(keys.into_iter().map(|k| (k.clone(), steady(&m[k]))).collect())
        }
        Value::Array(a) => Value::Array(a.iter().map(steady).collect()),
        other => other.clone(),
    }
}

pub fn describe(e: &ApiError) -> String {
    if e.status > 0 {
        format!("HTTP {}: {}", e.status, e.detail)
    } else {
        e.detail.clone()
    }
}

impl Runner {
    pub fn new(config: Config, client: Client, state: State) -> Runner {
        let projects_dir = config.projects_dir();
        let repositories = Arc::new(crate::repositories::Repositories::new(&projects_dir, &state.directory, client.clone()));
        let _ = crate::repositories::MIRRORS.set(repositories.clone());
        let worktrees = Arc::new(crate::worktrees::Worktrees::new(&projects_dir, client.clone()));
        let capacity = machine_capacity(config.data.get("capacity"));
        Runner {
            config,
            client,
            events: Events::new(),
            stop: AtomicBool::new(false),
            capacity,
            tokens: Arc::new(TokenCache::new()),
            socket: None,
            active: Mutex::new(HashSet::new()),
            push_lock: tokio::sync::Mutex::new(()),
            sign_in: crate::signin::SignIn::new(&projects_dir),
            publish_notes: Mutex::new(HashMap::new()),
            attempt_bots: Mutex::new(HashMap::new()),
            runtime_rows: Mutex::new(Map::new()),
            names: Mutex::new(None),
            lanes: Arc::new(crate::sessions::Lanes::new(repositories)),
            worktrees,
            worktree_stamp: AtomicBool::new(false),
            flush_locks: Mutex::new(HashMap::new()),
            first_posted: Mutex::new(HashSet::new()),
            assignments: Mutex::new(vec![]),
            published: Mutex::new(HashMap::new()),
            beat: Mutex::new(None),
            restart: AtomicBool::new(false),
            warm: crate::warm::WarmSessions::new(state.directory.join("antigravity"), capacity.max(8)),
            state,
            perbot: crate::perbot::PerBot::default(),
        }
    }

    pub fn stopping(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }

    /// GET config's names, kept once read; a failure keeps the defaults and asks again next turn.
    pub async fn names(&self) -> crate::prompt::Names {
        let known = self.names.lock().unwrap().clone();
        if let Some(c) = known {
            return crate::prompt::Names::from_config(Some(&c));
        }
        match self.client.get("config").await {
            Ok(c) => {
                *self.names.lock().unwrap() = Some(c.clone());
                crate::prompt::Names::from_config(Some(&c))
            }
            Err(_) => crate::prompt::Names::from_config(None),
        }
    }

    /// The last `runners/assignments` answer.
    pub fn assignments_seen(&self) -> Vec<Value> {
        self.assignments.lock().unwrap().clone()
    }

    /// Send the attempt's unacknowledged events, 100 at a time, keyed on their seq range so a retry is one write.
    pub async fn flush(&self, aid: &str) -> Result<(), ApiError> {
        let lock = self.flush_locks.lock().unwrap().entry(aid.to_string()).or_default().clone();
        let _held = lock.lock().await;
        loop {
            let batch = self.state.pending(aid).map_err(|e| ApiError::new("state", e, 0, false))?;
            if batch.is_empty() {
                return Ok(());
            }
            let key = format!("events:{aid}:{}:{}", batch[0]["seq"], batch[batch.len() - 1]["seq"]);
            let had_text = batch.iter().any(|e| e["kind"] == "delta" || e["kind"] == "message");
            let result = self.client.post(&format!("attempts/{aid}/events"), &json!({"events": batch}), Some(&key)).await?;
            if had_text && self.first_posted.lock().unwrap().insert(aid.to_string()) {
                mark("first_delta_posted", aid);
            }
            let ack = result["ack_seq"].as_i64().unwrap_or(0);
            self.state.ack(aid, ack).map_err(|e| ApiError::new("state", e, 0, false))?;
        }
    }

    pub fn forget_flush(&self, aid: &str) {
        self.flush_locks.lock().unwrap().remove(aid);
        self.first_posted.lock().unwrap().remove(aid);
    }

    /// Send a result under `complete:{aid}`. This runner's server floor takes every field it sends, so
    /// there is no 422 downgrade ladder (service.py `complete` strips fields for older servers).
    pub async fn complete(&self, aid: &str, completion: &Value) -> Result<Value, ApiError> {
        let result = self.client.post(&format!("attempts/{aid}/complete"), completion, Some(&format!("complete:{aid}"))).await;
        if result.is_ok() {
            mark("complete_posted", aid);
        }
        result
    }

    /// Results a restart or an outage left on this computer, sent before anything new.
    pub async fn recover_output(&self) {
        crate::files_publish::drain(&self.state, &self.client).await; // files a restart or an outage left queued
        let rows = match self.state.unfinished() {
            Ok(rows) => rows,
            Err(e) => return log(&format!("Tico runner: could not read saved results ({e}); will retry")),
        };
        for row in rows {
            if self.active.lock().unwrap().contains(&row.id) {
                continue;
            }
            let sent: Result<(), ApiError> = async {
                // The listing is a snapshot: a turn that ended since then saved (and maybe sent) its own result,
                // which stands. Read the row again, under the store's lock, before deciding what to send.
                let Some(completion) =
                    self.state.recovery(&row.id, "Runner restarted during execution").map_err(|e| ApiError::new("state", e, 0, false))?
                else {
                    return Ok(());
                };
                self.flush(&row.id).await?;
                self.complete(&row.id, &completion).await?;
                self.state.phase(&row.id, "synced").map_err(|e| ApiError::new("state", e, 0, false))?;
                Ok(())
            }
            .await;
            self.forget_flush(&row.id);
            if let Err(e) = sent {
                if e.code == "state" {
                    log(&format!("Tico runner: could not recover {} (OperationalError); will retry", row.id));
                } else if !e.retryable {
                    let bot = serde_json::from_str::<Value>(&row.payload).ok().map(|p| text_of(&p["bot"])).filter(|b| !b.is_empty());
                    log(&format!(
                        "Tico runner: {}: recovered result for {} refused ({}); kept on this Mac only",
                        bot.unwrap_or_else(|| "?".into()),
                        row.id,
                        describe(&e)
                    ));
                    let _ = self.state.phase(&row.id, "historical");
                }
            }
        }
    }

    /// Readiness and the heartbeat. `full` re-reads this computer's bots from the server; without it the last
    /// answer stands and the heartbeat goes only when `forced`, changed, or HEARTBEAT_LIVE_S has passed.
    pub async fn maintain(&self, full: bool, forced: bool) -> Result<(), ApiError> {
        let repository_rows = if full { self.lanes.repositories.poll().await } else { None };
        if full {
            let assignments = self.client.get("runners/assignments").await?;
            *self.assignments.lock().unwrap() = assignments.as_array().cloned().unwrap_or_default();
            crate::readiness_extra::fetch_grants(&self.client).await;
        }
        let assignments = self.assignments.lock().unwrap().clone();
        let mut by_bot: std::collections::BTreeMap<String, Value> = Default::default();
        for entry in &assignments {
            by_bot.insert(text_of(&entry["bot"]), entry.clone());
        }
        let candidates: Vec<Value> = by_bot.into_values().collect();
        self.fetch_missing(&assignments).await;
        let runtimes = readiness::runtime_report(&self.config, &self.sign_in, &self.client, &candidates).await;
        *self.runtime_rows.lock().unwrap() = runtimes.clone();
        let mut checks = readiness::preflight(&self.config, &candidates, &runtimes).await;
        self.perbot.decorate(&self.config, &mut checks, &candidates);
        let notes = self.publish_notes.lock().unwrap().clone();
        let hosted: Vec<String> = candidates.iter().filter(|e| text_of(&e["runner_id"]) == self.config.runner_id()).map(|e| text_of(&e["bot"])).collect();
        let published = self.published.lock().unwrap().clone();
        let (agent_instructions, versions) = readiness::changed_agent_instructions(&self.config, &assignments, &published);
        let mut readiness_doc = readiness::document(&runtimes, &checks, &self.state.directory);
        if crate::mail_key::status(&self.config) == Some("exposed") {
            readiness_doc["mail_key"] = json!("exposed"); // Health warns while bots can read the company's mail key
        }
        let mut body = json!({
            "version": RUNNER_VERSION,
            "platform": platform(),
            "capabilities": crate::lanes::capabilities(),
            "capacity": self.capacity,
            "readiness": readiness_doc,
            "mail_agent_instructions": readiness::mail_agent_instructions(&self.config, &assignments),
            "agent_instructions": agent_instructions,
        });
        crate::readiness_extra::extend(&mut body["readiness"], &runtimes, &checks, &notes, &self.client, hosted).await;
        if !crate::lanes::held_back("profiles") {
            let rejected = |runtime: &str, name: &str| {
                let profile = crate::profiles::select(&self.config.data, None, Some(name));
                profile.is_some_and(|p| self.sign_in.rejection(runtime, Some(&p)).is_some())
            };
            body["profiles"] = json!(crate::profiles::report(&self.config.data, &rejected).await);
        }
        let host: Arc<dyn crate::worktrees::Host> = self.lanes.clone();
        let mut worktrees = if full { self.worktrees.poll(host.clone(), &[]) } else { self.worktrees.latest() };
        if !self.worktree_stamp.load(Ordering::SeqCst) {
            for r in &mut worktrees {
                if let Some(m) = r.as_object_mut() {
                    m.remove("record_updated");
                }
            }
        }
        body["worktrees"] = json!(worktrees);
        // The repositories report rides only on full passes, which are always sent.
        let printed = steady(&body).to_string();
        if let Some(rows) = repository_rows {
            body["repositories"] = json!(rows);
        }
        let quiet = {
            let beat = self.beat.lock().unwrap();
            !full
                && !forced
                && self.events.live()
                && beat.as_ref().is_some_and(|(p, at)| *p == printed && at.elapsed().as_secs_f64() < HEARTBEAT_LIVE_S)
        };
        if !quiet {
            let mut tries = 0;
            let beat = loop {
                tries += 1;
                match self.client.post("runners/heartbeat", &body, None).await {
                    Err(e) if e.status == 422 && tries < 12 && crate::readiness_extra::refused(&e.detail, &body["readiness"]).is_some() => {
                        // An older server refused an optional readiness field: it is left out for ten minutes.
                        crate::lanes::hold_back(&crate::readiness_extra::refused(&e.detail, &body["readiness"]).unwrap_or_default());
                        crate::readiness_extra::drop_held(&mut body);
                    }
                    Err(e) if e.status == 422 && tries < 12 => match crate::lanes::refused_field(&e.detail, &body) {
                        // A server from before a capability or the profile report: only that goes, for ten minutes.
                        Some(field) => {
                            crate::lanes::hold_back(&field);
                            if field == "profiles" {
                                body.as_object_mut().unwrap().remove("profiles");
                            } else if let Some(caps) = body["capabilities"].as_array_mut() {
                                caps.retain(|c| c != field.as_str());
                            }
                        }
                        None => return Err(e),
                    },
                    other => break other?,
                }
            };
            *self.beat.lock().unwrap() = Some((printed, Instant::now()));
            self.published.lock().unwrap().extend(versions);
            let actions = beat["worktree_actions"].as_array().cloned().unwrap_or_default();
            if full || !actions.is_empty() {
                self.worktrees.poll(host, &actions);
            }
            self.worktree_stamp.store(beat["worktree_record_stamp"] == true, Ordering::SeqCst);
            if beat["restart"] == true && !self.restart.swap(true, Ordering::SeqCst) {
                log("Tico runner: restart requested; restarting when no turn is running");
            }
        }
        let dir = &self.state.directory;
        // scripts/tico status reads the heartbeat file's mtime.
        if let Ok(f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("heartbeat")) {
            let _ = f.set_modified(std::time::SystemTime::now());
        }
        let _ = std::fs::write(dir.join("runtimes.json"), Value::Object(runtimes).to_string());
        self.recover_output().await;
        self.sweep_sessions(&assignments).await;
        for bot in self.perbot.memory_due(&self.config, &assignments) {
            let config = assignments.iter().find(|e| text_of(&e["bot"]) == bot).map(|e| e["config"].clone()).unwrap_or(Value::Null);
            let path = crate::sessions::local_path(&self.config, &bot, &config);
            crate::memory_history::report(&self.state, &self.client, &bot, &path, "", "", false).await;
        }
        Ok(())
    }

}

impl Runner {
    /// Assigned bots this runner cannot run, each with what it lacks. Read once at start: the runner then refuses
    /// to start rather than leave those bots' work unclaimed.
    pub async fn refusals(&self) -> Result<Vec<String>, ApiError> {
        let assignments = self.client.get("runners/assignments").await?;
        let mine = self.config.runner_id();
        let mut lines = vec![];
        for entry in assignments.as_array().cloned().unwrap_or_default() {
            if text_of(&entry["runner_id"]) != mine {
                continue;
            }
            let needs = readiness::unsupported(&entry);
            if !needs.is_empty() {
                lines.push(format!("{} needs {}", text_of(&entry["bot"]), needs.join(", ")));
            }
        }
        Ok(lines)
    }

    /// Lease expiry does not mean this worker has stopped: every live worker's lane stays busy, and a bot whose
    /// worktrees are being maintained gets nothing (service.py _claim_next).
    async fn claim_next(&self) -> Result<Value, ApiError> {
        let mut maintaining: Vec<String> = self.worktrees.maintaining.lock().unwrap().iter().cloned().collect();
        maintaining.sort();
        if !crate::lanes::held_back("busy_lanes") {
            // Lanes: a bot with a task turn running here can still take its other tasks' turns.
            let body = json!({"next_run": true, "busy_bots": maintaining, "busy_lanes": self.lanes.busy_lanes()});
            match self.client.post("jobs/claim", &body, None).await {
                Err(e) if e.status == 422 => crate::lanes::hold_back("busy_lanes"),
                other => return other,
            }
        }
        // A server from before lanes: a bot with a turn running here gets nothing more.
        let active = self.active.lock().unwrap().clone();
        let mut bots: Vec<String> = active.iter().filter_map(|aid| self.state_bot(aid)).chain(maintaining).collect::<HashSet<_>>().into_iter().collect();
        bots.sort();
        self.client.post("jobs/claim", &json!({"next_run": true, "busy_bots": bots}), None).await
    }

    fn state_bot(&self, aid: &str) -> Option<String> {
        self.attempt_bots.lock().unwrap().get(aid).cloned()
    }
}

/// The loop's own clocks (service.py `wants`, `claim_due`, `claimed`, `claim_wait`).
struct Clock {
    polled: HashMap<&'static str, Instant>,
    next_claim: Option<Instant>,
    idle_claims: u32,
    claim_hint: (Option<Instant>, f64),
    claim_live_at: Option<Instant>,
    last_heartbeat: Option<Instant>,
}

impl Clock {
    fn wants(&mut self, events: &Events, channel: &'static str, every: Option<f64>) -> bool {
        let now = Instant::now();
        let due = if events.take(channel) {
            true
        } else if let Some(every) = every {
            match self.polled.get(channel) {
                None => true,
                Some(at) => now.duration_since(*at).as_secs_f64() >= if events.live() { BACKUP_POLL_S } else { every },
            }
        } else {
            return false;
        };
        if due {
            self.polled.insert(channel, now);
        }
        due
    }

    fn claim_due(&mut self, events: &Events) -> bool {
        let now = Instant::now();
        if !events.live() {
            return self.next_claim.is_none_or(|t| now >= t);
        }
        if events.take("claim") {
            self.claim_hint = (Some(now + Duration::from_secs_f64(CLAIM_HINT_S)), CLAIM_IDLE_MAX);
            return true;
        }
        self.next_claim.is_none() || self.claim_live_at.is_none_or(|t| now >= t)
    }

    fn claimed(&mut self, got: bool) {
        if got {
            return;
        }
        let now = Instant::now();
        let (until, wait) = self.claim_hint;
        if until.is_some_and(|u| now < u) {
            self.claim_live_at = Some(now + Duration::from_secs_f64(wait));
            self.claim_hint = (until, (wait * 2.0).min(60.0));
        } else {
            self.claim_live_at = Some(now + Duration::from_secs_f64(BACKUP_POLL_S));
        }
    }

    fn claim_wait(&mut self, got: bool) -> f64 {
        self.idle_claims = if got { 0 } else { self.idle_claims + 1 };
        CLAIM_IDLE_MAX.min(CLAIM_EVERY * 2f64.powi(self.idle_claims.min(3) as i32))
    }
}

impl Runner {
    pub async fn run(self: Arc<Self>) {
        let names = self.names().await;
        let _ = names;
        self.recover_output().await;
        tokio::spawn(self.events.clone().run(self.client.clone()));
        let mut clock = Clock {
            polled: HashMap::new(),
            next_claim: None,
            idle_claims: 0,
            claim_hint: (None, CLAIM_IDLE_MAX),
            claim_live_at: None,
            last_heartbeat: None,
        };
        let mut workers: HashMap<String, JoinHandle<Result<(), String>>> = HashMap::new();
        let mut maintenance: Option<JoinHandle<Result<(), ApiError>>> = None;
        let mut failures = 0u32;
        let mut paused_note = String::new();
        while !self.stopping() {
            let delay = match self.tick(&mut clock, &mut workers, &mut maintenance, &mut paused_note).await {
                Ok(()) => {
                    if failures > 0 {
                        log("Tico runner: the hub answers again");
                    }
                    failures = 0;
                    0.25
                }
                Err(e) => {
                    failures += 1;
                    if failures == 1 {
                        log(&format!("Tico runner: hub request failing ({}); retrying", describe(&e)));
                    }
                    (2f64.powi(failures.min(5) as i32)).min(30.0)
                }
            };
            if self.restart.load(Ordering::SeqCst) && workers.is_empty() {
                log("Tico runner: no turn running; exiting so the supervisor starts the runner again");
                break;
            }
            // The stream's word (a `work` event) wakes the loop at once instead of at the next quarter second.
            let _ = tokio::time::timeout(Duration::from_secs_f64(delay), self.events.wake.notified()).await;
        }
        self.stop.store(true, Ordering::SeqCst);
        self.events.close();
        for (_, w) in workers {
            let _ = w.await;
        }
        if let Some(m) = maintenance {
            m.abort();
        }
        self.warm.prune(true).await;
        // Git the runner started for mirrors, checkouts and worktrees goes with it, credential helpers included.
        crate::gitrun::stop_all();
        self.worktrees.settle().await;
    }

    async fn tick(
        self: &Arc<Self>,
        clock: &mut Clock,
        workers: &mut HashMap<String, JoinHandle<Result<(), String>>>,
        maintenance: &mut Option<JoinHandle<Result<(), ApiError>>>,
        paused_note: &mut String,
    ) -> Result<(), ApiError> {
        let finished: Vec<String> = workers.iter().filter(|(_, w)| w.is_finished()).map(|(a, _)| a.clone()).collect();
        for aid in finished {
            let worker = workers.remove(&aid).unwrap();
            let outcome = match worker.await {
                Ok(Ok(())) => None,
                Ok(Err(e)) => Some(e),
                Err(e) => Some(format!("worker ended: {e}")),
            };
            if let Some(why) = outcome {
                // The saved reason names the error, so the turn does not read as a runner restart. Saved while the
                // attempt still counts as active, so recovery cannot save its own reason first.
                log(&format!("Tico runner: worker {aid} failed ({why}); recovering saved attempt"));
                let _ = self.state.interrupted(&aid, &format!("The runner stopped this turn on an error ({why}); see this computer's runner log"));
            }
            self.active.lock().unwrap().remove(&aid);
            self.attempt_bots.lock().unwrap().remove(&aid);
            crate::lanes::forget(&aid);
            self.lanes.attempts.lock().unwrap().remove(&aid);
            clock.next_claim = None; // capacity came free: look for the next job now
        }
        self.warm.prune(false).await;
        if maintenance.as_ref().is_some_and(|m| m.is_finished()) {
            if let Ok(Err(e)) = maintenance.take().unwrap().await {
                log(&format!("Tico runner: heartbeat failed ({}); will retry", describe(&e)));
            }
        }
        if maintenance.is_none() {
            clock.wants(&self.events, "config", None);
            let full = clock.wants(&self.events, "sync", Some(15.0));
            let forced = clock.wants(&self.events, "heartbeat", None) | self.sign_in.take_dirty();
            if full || forced || clock.last_heartbeat.is_none_or(|t| t.elapsed().as_secs_f64() >= 15.0) {
                let me = self.clone();
                *maintenance = Some(tokio::spawn(async move { me.maintain(full, forced).await }));
                clock.last_heartbeat = Some(Instant::now());
            }
        }
        crate::relays::poll(self);
        if self.restart.load(Ordering::SeqCst) {
            return Ok(()); // a person asked for a restart: no new work, the running turns finish
        }
        // Programs bots declare in `watchers:` (crate::watchers); a failure is logged and never stops the runner.
        let assignments = self.assignments.lock().unwrap().clone();
        let ctx = crate::watchers::Ctx {
            client: self.client.clone(),
            url: self.config.url(),
            projects_dir: self.config.projects_dir(),
            state_dir: self.state.directory.clone(),
        };
        self.perbot.watchers.tick(&self.config, &assignments, ctx);
        while workers.len() < self.capacity && clock.claim_due(&self.events) {
            let result = self.claim_next().await?;
            let attempt = result.get("attempt").cloned().filter(|a| a.is_object());
            let got = attempt.is_some();
            if let Some(attempt) = attempt {
                let aid = text_of(&attempt["id"]);
                mark("claimed", &aid);
                self.active.lock().unwrap().insert(aid.clone());
                self.attempt_bots.lock().unwrap().insert(aid.clone(), text_of(&attempt["bot"]));
                crate::lanes::track(&attempt);
                let lane = crate::sessions::turn_lane(&attempt);
                let task = crate::sessions::task_of(&lane);
                self.lanes.attempts.lock().unwrap().insert(aid.clone(), (text_of(&attempt["bot"]).trim_start_matches("bot:").to_string(), lane, task));
                if let Err(e) = self.state.record(&attempt) {
                    log(&format!("Tico runner: could not save attempt {aid} ({e})"));
                }
                let me = self.clone();
                workers.insert(aid, tokio::spawn(async move { me.execute(attempt).await }));
            }
            clock.next_claim = Some(Instant::now() + Duration::from_secs_f64(clock.claim_wait(got)));
            clock.claimed(got);
            let paused = text_of(result.get("paused").unwrap_or(&Value::Null));
            if !paused.is_empty() && paused != *paused_note {
                log(&format!("Tico runner: the server is not giving this computer work: {paused}"));
                *paused_note = paused;
            }
            if !got {
                break;
            }
            clock.next_claim = None; // one job often means more: ask again at once
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn steady_drops_volatile_keys_and_sorts() {
        let v = json!({"b": 1, "a": {"free_bytes": 5, "total_bytes": 9}, "at": 3});
        assert_eq!(steady(&v).to_string(), r#"{"a":{"total_bytes":9},"b":1}"#);
    }

    #[test]
    fn idle_claims_back_off_to_two_seconds() {
        let mut c = Clock { polled: HashMap::new(), next_claim: None, idle_claims: 0, claim_hint: (None, 2.0), claim_live_at: None, last_heartbeat: None };
        let waits: Vec<f64> = (0..5).map(|_| c.claim_wait(false)).collect();
        assert_eq!(waits, vec![0.5, 1.0, 2.0, 2.0, 2.0]);
        assert_eq!(c.claim_wait(true), 0.25);
    }
}
