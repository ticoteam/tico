//! The run loop (`Runner.run`, `tick`, `maintain`, `recover_output` in the old Python runner): heartbeat with
//! readiness, claims on the event stream's word, recovery of results a restart left behind, and one tokio
//! task per turn (runner-rs/src/execute.rs).

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
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
use crate::wire::{decode, ClaimAnswer, EventsAnswer, HeartbeatAnswer};

/// The protocol and source version: what the shim matches against runner/version.py and what Health checks (a
/// semver). Not the release; see RUNNER_RELEASE.
pub const RUNNER_VERSION: &str = "0.5.4";
/// The release this binary was built for: build.rs stamps TICO_VERSION ("dev" for a source build). Used where the
/// runner reports what it is: the startup log, `--release`, and panic reports.
pub const RUNNER_RELEASE: &str = env!("TICO_RUNNER_RELEASE");
const CLAIM_EVERY: f64 = 0.25;
const CLAIM_IDLE_MAX: f64 = 2.0;
const CLAIM_HINT_S: f64 = 150.0;
/// Keys whose values move on every report without anything having changed (service.py VOLATILE).
const VOLATILE: &[&str] = &["free_bytes", "checked_at", "reported_at", "at", "mono", "server_time"];

pub struct Runner {
    /// runner.json as read at start; its subscription profiles are read again by `config_now`.
    pub config: Config,
    /// runner.json as last read well, for its profiles while the file can't be read.
    profiles_read: Mutex<Map<String, Value>>,
    pub client: Client,
    pub state: State,
    pub events: Arc<Events>,
    pub stop: AtomicBool,
    pub(crate) drain: crate::drain::Drain,
    pub capacity: usize,
    /// How many turns may hold a place: the owner's "Turns at once" from the last heartbeat reply, or the machine's
    /// capacity while none is set (config::turn_limit). Only new starts read it; running turns keep their places.
    pub turn_limit: AtomicUsize,
    pub tokens: Arc<TokenCache>,
    pub socket: Option<CredentialSocket>,
    /// Attempts with a live worker here; recovery leaves them alone.
    pub active: Mutex<HashSet<String>>,
    /// Workers preparing or running, including one whose lease lapsed while its process still runs.
    pub places: Mutex<HashSet<String>>,
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
    /// The server accepts short start reservations; claims opt in only after its heartbeat says so.
    start_places: AtomicBool,
    /// A claim whose answer never came back: key, body and first send. The next claim sends it again, so a job the
    /// server leased meanwhile is handed over instead of stranded until its lease runs out (Dev 612).
    unanswered_claim: Mutex<Option<(String, Value, Instant)>>,
    /// The server takes runtimes' `credential_source` (its heartbeat reply said so); until then it is left out.
    credential_source: AtomicBool,
    /// The server takes the signed-in account and the 5-hour window (its heartbeat reply said so); until then
    /// they stay on this computer.
    accounts: AtomicBool,
    flush_locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
    /// Attempts whose first streamed text has reached the server (one timing mark each).
    pub first_posted: Mutex<HashSet<String>>,
    assignments: Mutex<Vec<Value>>,
    published: Mutex<HashMap<String, Option<(i128, u64)>>>,
    beat: Mutex<Option<(String, Instant)>>,
    /// When a person asked for a restart (the Restart button): the runner exits once no turn runs, and claims
    /// no new work so that moment comes; unstarted turns are handed back.
    restart: Mutex<Option<Instant>>,
    /// When that restart arrived, ISO-8601 UTC: every heartbeat says so until the runner has restarted, so Health
    /// names the restart, not a Tico bug, for the work it holds back (backend/health.py `restarting`).
    restart_since: Mutex<Option<String>>,
    /// A restart was asked for on a runner nothing would start again: said once.
    restart_refused: AtomicBool,
    /// When the container was told to stop (`docker stop`, a host update): no new claims, the running turns
    /// finish, then the runner exits; a turn still going after STOP_DRAIN_S is interrupted (fn shut_down).
    shutdown: Mutex<Option<Instant>>,
    /// Persistent Antigravity processes kept between a conversation's turns.
    pub warm: crate::warm::WarmSessions,
    /// pi processes kept between a thread's turns (hosts/pi.rs Pool).
    pub pi: Arc<crate::hosts::pi::Pool>,
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
    pub fn new(mut config: Config, client: Client, state: State) -> Runner {
        if crate::config::retire_old_capacity(&mut config.data) {
            if let Err(e) = config.save() { log(&format!("Tico runner: could not save runner.json ({e})")); }
        }
        let projects_dir = config.projects_dir();
        let repositories = Arc::new(crate::repositories::Repositories::new(&projects_dir, &state.directory, client.clone()));
        let _ = crate::repositories::MIRRORS.set(repositories.clone());
        let worktrees = Arc::new(crate::worktrees::Worktrees::new(&projects_dir, client.clone()));
        let capacity = machine_capacity(config.data.get("capacity"));
        let drain_limit = config.data.get("drain_timeout_seconds").and_then(Value::as_u64).filter(|s| *s > 0).unwrap_or(crate::drain::DEFAULT_LIMIT_S);
        Runner {
            profiles_read: Mutex::new(config.data.clone()),
            config,
            client,
            events: Events::new(),
            stop: AtomicBool::new(false),
            drain: crate::drain::Drain::new(Duration::from_secs(drain_limit)),
            capacity,
            turn_limit: AtomicUsize::new(capacity),
            tokens: Arc::new(TokenCache::new()),
            socket: None,
            active: Mutex::new(HashSet::new()),
            places: Mutex::new(HashSet::new()),
            push_lock: tokio::sync::Mutex::new(()),
            sign_in: crate::signin::SignIn::new(&projects_dir),
            publish_notes: Mutex::new(HashMap::new()),
            attempt_bots: Mutex::new(HashMap::new()),
            runtime_rows: Mutex::new(Map::new()),
            names: Mutex::new(None),
            lanes: Arc::new(crate::sessions::Lanes::new(repositories)),
            worktrees,
            worktree_stamp: AtomicBool::new(false),
            start_places: AtomicBool::new(false),
            unanswered_claim: Mutex::new(None),
            credential_source: AtomicBool::new(false),
            accounts: AtomicBool::new(false),
            flush_locks: Mutex::new(HashMap::new()),
            first_posted: Mutex::new(HashSet::new()),
            assignments: Mutex::new(vec![]),
            published: Mutex::new(HashMap::new()),
            beat: Mutex::new(None),
            restart: Mutex::new(None),
            restart_since: Mutex::new(None),
            restart_refused: AtomicBool::new(false),
            shutdown: Mutex::new(None),
            warm: crate::warm::WarmSessions::new(state.directory.join("antigravity"), capacity.max(8)),
            pi: Arc::new(crate::hosts::pi::Pool::new(state.directory.join("pi"), capacity.max(8))),
            state,
            perbot: crate::perbot::PerBot::default(),
        }
    }

    pub fn stopping(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }

    /// A stop that lets the running turns finish (a container's SIGTERM, whose grace is 15 minutes). A second
    /// stop, or SIGINT, stops at once.
    pub fn shut_down(&self) {
        let mut at = self.shutdown.lock().unwrap();
        if at.is_none() {
            *at = Some(Instant::now());
            if crate::follow::feature("runner_drain") { self.drain.begin(Instant::now()); }
            log("Tico runner: stopping; taking no new work and letting the running turns finish");
        } else {
            self.stop.store(true, Ordering::SeqCst);
        }
        self.events.wake.notify_one();
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

    /// Stderr has its own capped row, never repeated in the event outbox or ordinary event stream.
    pub(crate) fn attempt_events(&self, aid: &str, redactor: Option<Arc<crate::redact::Redactor>>) ->
        (crate::hosts::Events, tokio::sync::mpsc::UnboundedReceiver<Value>) {
        let (input, mut rx) = tokio::sync::mpsc::unbounded_channel::<Value>();
        let (out, events) = tokio::sync::mpsc::unbounded_channel();
        let state = self.state.clone();
        let aid = aid.to_string();
        tokio::spawn(async move {
            while let Some(ev) = rx.recv().await {
                if ev["kind"] == "diagnostic" && ev.get("stderr_tail").is_some() {
                    let text = crate::util::text_of(&ev["stderr_tail"]);
                    let text = redactor.as_ref().map_or(text.clone(), |r| r.scrub_text(&text));
                    let text = crate::redact::scrub_credentials(&text, &[]);
                    let id = aid.clone();
                    let _ = state.off(move |s| s.stderr(&id, &text)).await;
                } else { let _ = out.send(ev); }
            }
        });
        (input, events)
    }

    async fn upload_stderr(&self, aid: Option<&str>) {
        if !crate::follow::feature("run_diagnostics") { return; }
        let filter = aid.map(str::to_string);
        let pending = self.state.off(move |s| s.pending_stderr(filter.as_deref())).await.unwrap_or_default();
        for (id, tail, revision) in pending.into_iter().take(10) {
            // Diagnostics never hold up a run result through network retries. The capped row retries on maintenance.
            let sent = tokio::time::timeout(Duration::from_secs(2), self.client.post(&format!("attempts/{id}/diagnostics"), &json!({"tail": tail}), None)).await;
            if matches!(sent, Ok(Ok(_))) || matches!(&sent, Ok(Err(e)) if (400..500).contains(&e.status) && e.status != 429 && e.status != 408) {
                let _ = self.state.off(move |s| s.stderr_sent(&id, revision)).await;
            }
        }
    }

    /// Send the attempt's unacknowledged events, 100 at a time, keyed on their seq range so a retry is one write.
    pub async fn flush(&self, aid: &str) -> Result<(), ApiError> {
        let lock = self.flush_locks.lock().unwrap().entry(aid.to_string()).or_default().clone();
        let _held = lock.lock().await;
        loop {
            let id = aid.to_string();
            let batch = self.state.off(move |s| s.pending(&id)).await.map_err(|e| ApiError::new("state", e, 0, false))?;
            if batch.is_empty() {
                return Ok(());
            }
            let key = format!("events:{aid}:{}:{}", batch[0]["seq"], batch[batch.len() - 1]["seq"]);
            let had_text = batch.iter().any(|e| e["kind"] == "delta" || e["kind"] == "message");
            let result = self.client.post(&format!("attempts/{aid}/events"), &json!({"events": batch}), Some(&key)).await?;
            if had_text && self.first_posted.lock().unwrap().insert(aid.to_string()) {
                mark("first_delta_posted", aid);
            }
            let ack = decode::<EventsAnswer>(result)?.ack_seq;
            let id = aid.to_string();
            self.state.off(move |s| s.ack(&id, ack)).await.map_err(|e| ApiError::new("state", e, 0, false))?;
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
        self.upload_stderr(None).await;
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

    /// The registration with runner.json's subscription profiles as they are now: one a browser sign-in or `profile
    /// add` made, or one taken out by hand, counts at the next heartbeat and turn without a restart. Only the profile
    /// keys are read again; while the file can't be read, the profiles last read stand.
    pub fn config_now(&self) -> Config {
        let mut read = self.profiles_read.lock().unwrap();
        if let Ok(disk) = Config::load(&self.config.path) {
            *read = disk.data;
        }
        self.config.with_profiles_of(&read)
    }

    /// Readiness and the heartbeat. `full` re-reads this computer's bots from the server; without it the last
    /// answer stands and the heartbeat goes only when `forced`, changed, or HEARTBEAT_LIVE_S has passed.
    pub async fn maintain(&self, full: bool, forced: bool) -> Result<(), ApiError> {
        // First, so a panic later in this pass (or one that keeps recurring here) is still sent.
        crate::panics::report(&self.client).await;
        let repository_rows = if full { self.lanes.repositories.poll().await } else { None };
        if full {
            let assignments = self.client.get("runners/assignments").await?;
            *self.assignments.lock().unwrap() = assignments.as_array().cloned().unwrap_or_default();
            // Before the grants are read: a bot from before grants keeps the legacy keys it could read.
            if let Err(e) = crate::credential_migration::migrate(&self.config, &self.client, assignments.as_array().map(Vec::as_slice).unwrap_or_default()).await {
                log(&format!("Tico runner: credential migration failed ({}); will retry", describe(&e)));
            }
            crate::readiness_extra::fetch_grants(&self.client).await;
            // What the server takes in a readiness row (fallback.rs remember), before the first report and not only
            // from the harness relay's minute: a Muse Code data folder is then named from the first heartbeat.
            if !crate::fallback::config_seen() {
                if let Ok(c) = self.client.get("config").await {
                    crate::fallback::remember(&c);
                }
            }
        }
        let assignments = self.assignments.lock().unwrap().clone();
        let candidates = readiness_candidates(&assignments);
        // A built-in bot is set up from the catalog first; any other (or one whose history is on GitHub) is fetched.
        // A bot delivered through an app has no repository to fetch.
        let with_repositories: Vec<Value> = candidates.iter().filter(|e| crate::appdriver::delivery(&e["config"]).is_none()).cloned().collect();
        crate::product_files::bootstrap_missing(&self.config, &with_repositories).await;
        self.fetch_missing(&with_repositories).await;
        // A checkout fetched just now is closed to other bots' leased turns before any turn of its own runs.
        let workspace = self.config.projects_dir();
        let _ = tokio::task::spawn_blocking(move || crate::checkout_groups::gate_workspace(&workspace)).await;
        // Profiles as runner.json has them now, so a sign-in made since start is reported and checked.
        let config = self.config_now();
        let computer_logins = crate::fallback::server_uses_computer_logins()
            && candidates.iter().all(|entry| entry["computer_logins_v1"] == true);
        let runtimes = readiness::runtime_report(&config, &self.sign_in, &self.client, &candidates, computer_logins).await;
        *self.runtime_rows.lock().unwrap() = runtimes.clone();
        let mut checks = readiness::preflight(&config, &self.sign_in, &candidates, &runtimes, computer_logins).await;
        self.perbot.decorate(&self.config, &mut checks, &candidates);
        self.publish_unpublished(&candidates, &mut checks).await;
        let notes = self.publish_notes.lock().unwrap().clone();
        let hosted: Vec<String> = candidates.iter().filter(|e| text_of(&e["runner_id"]) == self.config.runner_id()).map(|e| text_of(&e["bot"])).collect();
        let published = self.published.lock().unwrap().clone();
        let (agent_instructions, versions) = readiness::changed_agent_instructions(&self.config, &assignments, &published);
        let mut readiness_doc = readiness::document(&runtimes, &checks, &self.state.directory);
        let mut capabilities = crate::lanes::capabilities();
        if computer_logins {
            readiness_doc["computer_profile"] = json!(crate::profiles::computer_login(&config.data).map(|p| p.name).unwrap_or_default());
            capabilities.push("computer_logins_v1");
        }
        if crate::mail_key::status(&self.config) == Some("exposed") {
            readiness_doc["mail_key"] = json!("exposed"); // Health warns while bots can read the company's mail key
        }
        if let Some(screen) = crate::screen::readiness() {
            readiness_doc["screen"] = screen; // only while turned on here (crate::screen)
        }
        if let Some(apps) = crate::appdriver::readiness(&candidates) {
            readiness_doc["appdriver"] = apps; // only on a Mac with app delivery (crate::appdriver)
        }
        let mut body = json!({
            "version": RUNNER_VERSION,
            "platform": platform(),
            "capabilities": capabilities,
            "capacity": self.capacity,
            "readiness": readiness_doc,
            "mail_agent_instructions": readiness::mail_agent_instructions(&self.config, &assignments),
            "agent_instructions": agent_instructions,
        });
        crate::readiness_extra::extend(&mut body["readiness"], &runtimes, &checks, &notes, &self.client, hosted).await;
        if let Some(follower) = crate::follow::shared() {
            // The server's release (the stream's word, else asked), and where this runner stands on it.
            let said = self.events.release.lock().unwrap().clone().filter(|r| !full && r.is_string()).map(|r| text_of(&r));
            follower.poll(&self.client, said).await;
            let restarting = self.restart.lock().unwrap().is_some();
            follower.follow_main(restarting).await;
            follower.fields(&mut body);
            if crate::follow::feature("runner_drain") {
                if let Some(progress) = self.drain.progress().filter(|_| body["update"].is_object()) {
                    body["update"]["running"] = progress["running"].clone();
                    body["update"]["handed_back"] = progress["handed_back"].clone();
                }
            }
        }
        if !crate::lanes::held_back("profiles") {
            // Profiles only: a subscription login is shared by its bots, so no bot is asked for (signin.refusal_key).
            let rejected = |runtime: &str, name: &str| {
                let profile = crate::profiles::select(&config.data, Some(name));
                profile.is_some_and(|p| self.sign_in.rejection(runtime, Some(&p), "").is_some())
            };
            body["profiles"] = json!(crate::profiles::report(&config.data, &rejected).await);
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
        if !self.credential_source.load(Ordering::SeqCst) {
            strip_credential_source(&mut body);
        }
        if !self.accounts.load(Ordering::SeqCst) {
            strip_accounts(&mut body);
        }
        add_restart(&mut body, self.restart_since.lock().unwrap().as_deref());
        // The repositories report rides only on full passes, which are always sent.
        let printed = steady(&body).to_string();
        if let Some(rows) = repository_rows {
            body["repositories"] = json!(rows);
        }
        // What an older server refused in the last ten minutes stays out (service.py _report_heartbeat).
        crate::lanes::drop_held(&mut body);
        crate::readiness_extra::drop_held(&mut body);
        let quiet = {
            let beat = self.beat.lock().unwrap();
            !full
                && !forced
                && self.events.live()
                && beat.as_ref().is_some_and(|(p, at)| *p == printed && at.elapsed().as_secs_f64() < HEARTBEAT_LIVE_S)
        };
        if !quiet {
            let began = Instant::now();
            let (beat, tries) = self.post_heartbeat(&mut body).await;
            crate::flight::heartbeat(began.elapsed().as_millis() as u64, tries, beat.as_ref().err());
            let beat = beat?;
            *self.beat.lock().unwrap() = Some((printed, Instant::now()));
            self.published.lock().unwrap().extend(versions);
            let actions = beat.worktree_actions.unwrap_or_default();
            note_skipped_actions(beat.worktree_actions_skipped.unwrap_or(0));
            if full || !actions.is_empty() {
                self.worktrees.poll(host, &actions);
            }
            self.start_places.store(beat.start_places == Some(true), Ordering::SeqCst);
            self.worktree_stamp.store(beat.worktree_record_stamp == Some(true), Ordering::SeqCst);
            self.credential_source.store(beat.runtime_credential_source == Some(true), Ordering::SeqCst);
            self.accounts.store(beat.subscription_accounts == Some(true), Ordering::SeqCst);
            // Performance diagnostics run only while the computer's owner has them on (crate::perf).
            crate::perf::set_enabled(beat.performance == Some(true));
            // This computer is a browser exit only while its owner shares its internet (crate::browser_exit).
            crate::browser_exit::set_sharing(beat.browser_exit == Some(true));
            if let Some(bots) = beat.browser_exit_bots.as_deref() {
                crate::browser_exit::keep_proxies(bots, began);
            }
            if beat.restart == Some(true) && self.restart.lock().unwrap().is_none() {
                // Only a supervisor that starts the runner again may let it exit to restart; take main first if safe.
                if supervised() {
                    if let Some(follower) = crate::follow::shared() {
                        follower.take_main().await;
                    }
                    *self.restart.lock().unwrap() = Some(Instant::now());
                    if crate::follow::feature("runner_drain") { self.drain.begin(Instant::now()); }
                    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
                    *self.restart_since.lock().unwrap() = crate::util::iso(&json!(now.as_secs())).as_str().map(String::from);
                    log("Tico runner: restart requested; restarting when no turn is running");
                } else if !self.restart_refused.swap(true, Ordering::SeqCst) {
                    log("Tico runner: restart requested, but nothing would start this runner again; run `scripts/tico install` once, or restart it by hand");
                }
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
    /// Send the heartbeat `body`, and how many posts it took. A server older than this runner refuses what it doesn't
    /// know (422): only that comes out, held back for ten minutes, and the rest goes again, so a runner updated
    /// before its server still checks in and claims work (runners and servers update independently).
    async fn post_heartbeat(&self, body: &mut Value) -> (Result<HeartbeatAnswer, ApiError>, u32) {
        let mut tries = 0;
        let beat = loop {
            tries += 1;
            let e = match self.client.post("runners/heartbeat", body, None).await {
                Err(e) if e.status == 422 && tries < 12 => e,
                other => break other,
            };
            // An older server refused something optional: only that goes, for ten minutes.
            if let Some(field) = crate::lanes::refused_field(&e.detail, body) {
                crate::lanes::hold_back(&field);
                crate::lanes::drop_held(body);
            } else if body.get("commit").is_some() && e.detail.contains("body.commit") {
                // A server rolled back since it said it takes the commit: it waits for `runners/desired` to say so again.
                body.as_object_mut().unwrap().remove("commit");
                crate::follow::shared().inspect(|f| f.drop_commit());
            } else if capacity_refused(&e.detail) && body["capacity"].as_u64().unwrap_or(0) > 32 {
                // A server from before larger capacities accepts at most 32 and never hands out more turns
                // than the runner reports, so after a rollback the runner keeps working at 32.
                body["capacity"] = json!(32);
            } else if let Some(hold) = crate::readiness_extra::refused(&e.detail, &body["readiness"]) {
                crate::lanes::hold_back(&hold);
                crate::readiness_extra::drop_held(body);
            } else if strip_credential_source(body) {
                // A rollback to a server from before credential sources: the heartbeat keeps working.
                self.credential_source.store(false, Ordering::SeqCst);
            } else if strip_accounts(body) {
                // A rollback to a server from before accounts: the rest of the report still goes.
                self.accounts.store(false, Ordering::SeqCst);
            } else {
                break Err(e);
            }
        };
        let beat = beat.and_then(decode::<HeartbeatAnswer>);
        if let Ok(reply) = &beat {
            // Absent or null is automatic: the machine's own capacity is the limit.
            let owner = reply.turns_at_once.map(|n| n as usize);
            self.turn_limit.store(crate::config::turn_limit(owner, self.capacity), Ordering::SeqCst);
        }
        (beat, tries)
    }

    /// Lease expiry does not mean this worker has stopped: every live worker's lane stays busy, and a bot whose
    /// worktrees are being maintained gets nothing (service.py _claim_next).
    async fn claim_next(&self) -> Result<ClaimAnswer, ApiError> {
        let mut maintaining: Vec<String> = self.worktrees.maintaining.lock().unwrap().iter().cloned().collect();
        maintaining.sort();
        if !crate::lanes::held_back("busy_lanes") {
            // Lanes: a bot with a task turn running here can still take its other tasks' turns.
            let mut body = json!({"next_run": true, "busy_bots": maintaining, "busy_lanes": self.lanes.busy_lanes()});
            if self.start_places.load(Ordering::SeqCst) { body["start_places"] = json!(true); }
            match self.claim_again_or(body).await {
                Err(e) if e.status == 422 => {
                    if e.detail.contains("start_places") { self.start_places.store(false, Ordering::SeqCst); }
                    else { crate::lanes::hold_back("busy_lanes"); }
                },
                other => return other,
            }
        }
        // A server from before lanes: a bot with a turn running here gets nothing more.
        let active = self.active.lock().unwrap().clone();
        let mut bots: Vec<String> = active.iter().filter_map(|aid| self.state_bot(aid)).chain(maintaining).collect::<HashSet<_>>().into_iter().collect();
        bots.sort();
        self.client.post("jobs/claim", &json!({"next_run": true, "busy_bots": bots}), None).await.and_then(decode)
    }

    /// Sends this claim, or first the one whose answer was lost: the same key and body, which the server answers from
    /// its record if it leased a job (store.py is_poll). A slow server took 20-60 s per claim on Oct 27 against this
    /// client's 10 s timeout, and every claim it finished after the runner gave up stranded a lease.
    async fn claim_again_or(&self, body: Value) -> Result<ClaimAnswer, ApiError> {
        let pending = self.unanswered_claim.lock().unwrap().take().filter(|(_, _, at)| at.elapsed() < UNANSWERED_CLAIM_REPLAY);
        let (key, body, at) = pending.unwrap_or_else(|| (uuid::Uuid::new_v4().to_string(), body, Instant::now()));
        let result = self.client.post("jobs/claim", &body, Some(&key)).await;
        if matches!(&result, Err(e) if e.retryable) {
            *self.unanswered_claim.lock().unwrap() = Some((key, body, at));
        }
        result.and_then(decode)
    }

    fn state_bot(&self, aid: &str) -> Option<String> {
        self.attempt_bots.lock().unwrap().get(aid).cloned()
    }
}

/// Only live assignments are readiness candidates, one per bot (service.py readiness_candidates). An archived bot
/// keeps its assignment but is never set up or checked: its repository may be gone, and retrying it on every
/// maintenance pass kept an idle computer busy.
pub fn readiness_candidates(assignments: &[Value]) -> Vec<Value> {
    let mut by_bot: std::collections::BTreeMap<String, Value> = Default::default();
    for entry in assignments.iter().filter(|e| e["state"] != "archived") {
        by_bot.insert(text_of(&entry["bot"]), entry.clone());
    }
    by_bot.into_values().collect()
}

/// Whether a supervisor starts the runner again when it exits (service.py supervised): launchd sets
/// XPC_SERVICE_NAME to the job's label, the systemd user unit `scripts/tico install` writes sets TICO_SYSTEMD_UNIT,
/// and TICO_SUPERVISED=1 covers any other supervisor (the Docker runner, runit).
fn supervised() -> bool {
    let var = |k: &str| std::env::var(k).unwrap_or_default();
    var("XPC_SERVICE_NAME").starts_with("team.tico") || var("TICO_SYSTEMD_UNIT").starts_with("tico-") || var("TICO_SUPERVISED") == "1"
}

/// How long a stopping container's turns may run on; under compose's 15-minute stop_grace_period, so the runner
/// interrupts them itself (and says so) before Docker kills it.
const STOP_DRAIN_S: u64 = 14 * 60;

/// Whether the platform's stop should drain rather than interrupt: a Docker runner, whose compose file gives it
/// 15 minutes. A Mac app or launchd stop keeps stopping at once.
pub fn drains_on_stop() -> bool {
    std::env::var("TICO_RUNNER_KIND").as_deref() == Ok("docker") || std::env::var("TICO_STOP_DRAIN").as_deref() == Ok("1")
}

/// A restart under way rides on the heartbeat (an older server that refuses it gets it held back, lanes.rs ROOT_FIELDS).
/// How long a lost claim answer is asked for again: past a lease, any job it leased has expired back to the queue.
const UNANSWERED_CLAIM_REPLAY: Duration = Duration::from_secs(60);

fn restart_blocks_claims(asked: Option<Instant>, now: Instant, drain: bool) -> bool {
    asked.is_some_and(|at| drain || now.saturating_duration_since(at) >= Duration::from_secs(20 * 60))
}

fn add_restart(body: &mut Value, since: Option<&str>) {
    if let Some(since) = since {
        body["restart"] = json!({"since": since});
    }
}

/// Take `credential_source` out of every runtime row; whether there was any (service.py maintain).
fn strip_credential_source(body: &mut Value) -> bool {
    let mut any = false;
    for row in body["readiness"]["runtimes"].as_object_mut().into_iter().flat_map(|m| m.values_mut()).filter_map(|v| v.as_object_mut()) {
        any |= row.remove("credential_source").is_some();
    }
    any
}

/// Take the signed-in accounts and 5-hour windows out of a heartbeat body: what a server from before #610 has no
/// field for, nor for the own login's usage windows. True when there was one.
pub(crate) fn strip_accounts(body: &mut Value) -> bool {
    let mut any = crate::readiness_extra::strip_runtime_usage(&mut body["readiness"]);
    for row in body["readiness"]["runtimes"].as_object_mut().into_iter().flat_map(|m| m.values_mut()).filter_map(|v| v.as_object_mut()) {
        any |= row.remove("account").is_some();
    }
    for profile in body["profiles"].as_array_mut().into_iter().flatten() {
        for state in profile["runtimes"].as_object_mut().into_iter().flat_map(|m| m.values_mut()).filter_map(|v| v.as_object_mut()) {
            any |= state.remove("account").is_some();
            any |= state.remove("five_hour").is_some();
        }
    }
    any
}

/// A heartbeat refused for its capacity (`body.capacity: ...` in the 422 detail).
fn capacity_refused(detail: &str) -> bool {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"(?:^|[ ;])(?:body\.)?capacity:").unwrap()).is_match(detail)
}

/// The server leaves out worktree actions for tasks this computer's bots can no longer read; said when the count
/// changes, not on every heartbeat.
fn note_skipped_actions(skipped: u64) {
    static LAST: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    if LAST.swap(skipped, Ordering::SeqCst) != skipped && skipped > 0 {
        log(&format!("Tico runner: readiness skipped {skipped} worktree action(s) for tasks this computer's bots can no longer read"));
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
        if let Err(e) = tokio::task::spawn_blocking(crate::turn_users::initialize).await.unwrap_or_else(|e| Err(e.to_string())) {
            log(&format!("Tico runner: Gateway turn users unavailable: {e}"));
        }
        let (state, workspace) = (self.state.directory.clone(), self.config.projects_dir());
        let _ = tokio::task::spawn_blocking(move || {
            crate::checkout_groups::initialize(&state);
            crate::checkout_groups::gate_workspace(&workspace);
        }).await;
        let names = self.names().await;
        let _ = names;
        self.recover_output().await;
        tokio::spawn(self.clone().push_backlog());
        tokio::spawn(self.events.clone().run(self.client.clone()));
        tokio::spawn(crate::perf::run_loop(self.clone()));
        tokio::spawn(crate::afterwatch::run_loop(self.clone()));
        tokio::spawn(crate::memory_tidy_keep::run_loop(self.clone()));
        tokio::spawn(crate::appdriver::run_loop(self.clone()));
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
                        crate::util::diagnostic("Tico runner: the hub answers again");
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
            let shutdown = *self.shutdown.lock().unwrap();
            if shutdown.is_some() && workers.is_empty() {
                log("Tico runner: no turn running; stopping");
                break;
            }
            if shutdown.is_some_and(|at| at.elapsed() >= Duration::from_secs(STOP_DRAIN_S)) {
                log("Tico runner: turns still running at the stop deadline; interrupting them");
                break;
            }
            if self.restart.lock().unwrap().is_some() && workers.is_empty() {
                log("Tico runner: no turn running; exiting so the supervisor starts the runner again");
                break;
            }
            if crate::follow::shared().is_some_and(|f| f.restart_due()) && workers.is_empty() {
                log("Tico runner: no turn running; exiting so the supervisor starts the updated runner");
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
        self.pi.prune(true).await;
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
        if self.shutdown.lock().unwrap().is_some() || self.restart.lock().unwrap().is_some()
            || crate::follow::shared().is_some_and(|f| f.draining()) {
            if crate::follow::feature("runner_drain") { self.drain.begin(Instant::now()); }
        } else {
            self.drain.end(); // a failed or cancelled update takes work again
        }
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
            self.drain.finish(&aid);
            self.active.lock().unwrap().remove(&aid);
            self.places.lock().unwrap().remove(&aid);
            self.attempt_bots.lock().unwrap().remove(&aid);
            crate::lanes::forget(&aid);
            self.lanes.attempts.lock().unwrap().remove(&aid);
            clock.next_claim = None; // capacity came free: look for the next job now
        }
        self.warm.prune(false).await;
        self.pi.prune(false).await;
        if maintenance.as_ref().is_some_and(|m| m.is_finished()) {
            if let Ok(Err(e)) = maintenance.take().unwrap().await {
                log(&format!("Tico runner: heartbeat failed ({}); will retry", describe(&e)));
            }
        }
        if maintenance.is_none() {
            // The company's providers changed: a full pass now, and the default runtime a computer with no bots
            // installs is read again at once (relays.rs).
            let changed = clock.wants(&self.events, "config", None);
            if changed {
                crate::relays::config_changed();
            }
            // A credential changed in the app: its model keys are fetched again by this pass, not after KEY_REFRESH.
            if clock.wants(&self.events, "keys", None) {
                for runtime in crate::harness_tools::runtimes() {
                    if !crate::signin::headless_names(&runtime).is_empty() {
                        self.sign_in.refresh_now(&runtime);
                    }
                }
            }
            let full = clock.wants(&self.events, "sync", Some(15.0)) || changed;
            let forced = clock.wants(&self.events, "heartbeat", None) | self.sign_in.take_dirty();
            if full || forced || clock.last_heartbeat.is_none_or(|t| t.elapsed().as_secs_f64() >= 15.0) {
                let me = self.clone();
                *maintenance = Some(tokio::spawn(async move { me.maintain(full, forced).await }));
                clock.last_heartbeat = Some(Instant::now());
            }
        }
        crate::relays::poll(self);
        if self.shutdown.lock().unwrap().is_some() {
            return Ok(()); // stopping: the running turns finish, nothing new starts
        }
        if restart_blocks_claims(*self.restart.lock().unwrap(), Instant::now(), crate::follow::feature("runner_drain")) {
            return Ok(()); // restarting: no new work, so the running turns finish
        }
        if let Some(follower) = crate::follow::shared() {
            if follower.blocks_claims(!workers.is_empty()).await {
                return Ok(()); // an update is due or under way: the updater restarts this runner once nothing runs
            }
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
        // The server reserves starts atomically; waiting workers do not consume computer capacity.
        while {
            let mut places = self.places.lock().unwrap();
            if places.len() < self.turn_limit.load(Ordering::SeqCst) && clock.claim_due(&self.events) {
                places.insert("<claim>".into());
                true
            } else {
                false
            }
        } {
            let result = self.claim_next().await;
            {
                let mut places = self.places.lock().unwrap();
                places.remove("<claim>");
                if let Some(attempt) = result.as_ref().ok().and_then(|r| r.attempt.as_ref()) {
                    places.insert(text_of(&attempt["id"]));
                }
            }
            let result = result?;
            let attempt = result.attempt.map(Value::Object);
            let got = attempt.is_some();
            if let Some(attempt) = attempt {
                let aid = text_of(&attempt["id"]);
                mark("claimed", &aid);
                self.drain.register(&aid);
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
            let paused = result.paused.unwrap_or_default();
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

    // What a server from before `isolated` (main 95cbeb29) answers a check-in that carries it, word for word.
    const BEFORE_ISOLATED: &str = r#"{"error": {"code": "validation", "retryable": false, "detail": "body.readiness.StructuredReadiness.isolated: Extra inputs are not permitted; body.readiness.dict[constrained-str,bool].runtimes: Input should be a valid boolean; body.readiness.dict[constrained-str,bool].bots: Input should be a valid boolean"}}"#;

    #[tokio::test]
    async fn a_new_runner_checks_in_with_a_server_from_before_isolation_reports_and_still_claims_work() {
        let (url, seen) = crate::client::tests::serve(vec![
            (422, BEFORE_ISOLATED),
            (200, r#"{"ok": true}"#),
            (200, r#"{"attempt": {"id": "att-1", "bot": "ops"}}"#),
        ]).await;
        let dir = std::env::temp_dir().join(format!("tico-old-server-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        let runner = Runner::new(config, client, State::open(&dir.join("state")).unwrap());
        let mut body = json!({"version": "test", "platform": "linux", "readiness": {"schema_version": 1, "isolated": true,
                              "runtimes": {"pi": {"installed": true}}, "bots": {"ops": {"ready": true}}}});
        let (beat, tries) = runner.post_heartbeat(&mut body).await;
        crate::lanes::release("readiness.isolated");
        assert_eq!((beat.is_ok(), tries), (true, 2));
        let claimed = runner.claim_next().await.unwrap();
        assert_eq!(claimed.attempt.unwrap()["id"], "att-1");
        let seen = seen.lock().unwrap();
        assert!(seen[0].contains("\"isolated\":true"), "{}", seen[0]);
        // The same check-in again, less the one field the older server doesn't know: then the claim.
        assert!(seen[1].starts_with("POST /api/v2/runners/heartbeat") && !seen[1].contains("isolated"), "{}", seen[1]);
        assert!(seen[1].contains("\"ops\":{\"ready\":true}") && seen[1].contains("\"pi\":{\"installed\":true}"), "{}", seen[1]);
        assert!(seen[2].starts_with("POST /api/v2/jobs/claim"), "{}", seen[2]);
    }

    #[tokio::test]
    async fn a_panic_since_the_last_pass_is_the_first_thing_a_pass_sends() {
        let _one = crate::panics::TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let (url, seen) = crate::client::tests::serve(vec![(200, r#"{"ok": true}"#)]).await;
        let dir = std::env::temp_dir().join(format!("tico-panic-report-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        let runner = Runner::new(config, client, State::open(&dir.join("state")).unwrap());
        crate::panics::note_for_test("src/drive.rs", 4242);
        // The rest of the pass finds no server: only what it sent first matters here.
        let _ = tokio::time::timeout(std::time::Duration::from_secs(30), runner.maintain(false, false)).await;
        let seen = seen.lock().unwrap();
        assert!(seen[0].starts_with("POST /api/v2/runners/errors") && seen[0].contains(r#"{"file":"src/drive.rs","line":4242}"#), "{}", seen[0]);
    }

    #[test]
    fn old_servers_keep_the_restart_claim_window() {
        let asked = Instant::now();
        assert!(!restart_blocks_claims(None, asked, true));
        assert!(!restart_blocks_claims(Some(asked), asked, false));
        assert!(!restart_blocks_claims(Some(asked), asked + Duration::from_secs(1199), false));
        assert!(restart_blocks_claims(Some(asked), asked + Duration::from_secs(1200), false));
        assert!(restart_blocks_claims(Some(asked), asked, true));
    }

    #[test]
    fn a_restart_under_way_rides_on_the_heartbeat_and_an_older_server_gets_it_held_back() {
        let mut body = json!({"version": "test"});
        add_restart(&mut body, None);
        assert!(body.get("restart").is_none());
        add_restart(&mut body, Some("2026-10-17T11:41:00+00:00"));
        assert_eq!(body["restart"], json!({"since": "2026-10-17T11:41:00+00:00"}));
        let field = crate::lanes::refused_field("body.restart: Extra inputs are not permitted", &body);
        assert_eq!(field.as_deref(), Some("restart"));
        crate::lanes::hold_back("restart");
        crate::lanes::drop_held(&mut body);
        crate::lanes::release("restart");
        assert!(body.get("restart").is_none() && body["version"] == "test");
    }

    #[test]
    fn steady_drops_volatile_keys_and_sorts() {
        let v = json!({"b": 1, "a": {"free_bytes": 5, "total_bytes": 9}, "at": 3});
        assert_eq!(steady(&v).to_string(), r#"{"a":{"total_bytes":9},"b":1}"#);
    }

    #[test]
    fn archived_bots_are_not_readiness_candidates_and_a_capacity_refusal_is_recognised() {
        let live = json!({"bot": "ops", "state": "active"});
        let gone = json!({"bot": "qa-old", "state": "archived"});
        assert_eq!(readiness_candidates(&[gone, live.clone()]), vec![live]);
        assert!(capacity_refused("body.capacity: Input should be less than or equal to 32"));
        assert!(!capacity_refused("body.readiness.capacity_note: bad"));
    }

    #[test]
    fn credential_source_is_stripped_from_every_runtime() {
        let mut body = json!({"readiness": {"runtimes": {"codex": {"ok": true, "credential_source": "team"}}}});
        assert!(strip_credential_source(&mut body) && !strip_credential_source(&mut body));
    }

    #[test]
    fn accounts_stay_here_until_the_server_takes_them() {
        let mut body = json!({"readiness": {"runtimes": {"claude": {"installed": true, "account": {"email": "ops@acme.example"},
                                                               "weekly": {"used_percent": 17}, "five_hour": {"used_percent": 53}}}},
                              "profiles": [{"name": "acme", "runtimes": {"codex": {"signed_in": true, "weekly": {"used_percent": 3},
                                            "account": {"plan": "pro"}, "five_hour": {"used_percent": 9}}}}]});
        assert!(strip_accounts(&mut body) && !strip_accounts(&mut body));
        assert_eq!(body["readiness"]["runtimes"]["claude"], json!({"installed": true}));
        assert_eq!(body["profiles"][0]["runtimes"]["codex"], json!({"signed_in": true, "weekly": {"used_percent": 3}}));
    }

    #[test]
    fn a_profile_made_or_taken_out_while_running_counts_without_a_restart() {
        let dir = std::env::temp_dir().join(format!("tico-config-now-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        let start = json!({"url": "http://127.0.0.1:9", "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")});
        std::fs::write(&path, start.to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = Client::new(&config.url(), &config.token(), 1, 0).unwrap();
        let runner = Runner::new(config, client, State::open(&dir.join("state")).unwrap());
        assert!(crate::profiles::configured(&runner.config_now().data).is_empty());
        // A profile written to runner.json while running (`profile add`, or a hand edit).
        crate::profiles::add_profile(&runner.config, "chris-chatgpt").unwrap();
        let now = runner.config_now();
        assert_eq!(crate::profiles::select(&now.data, Some("chris-chatgpt")).unwrap().name, "chris-chatgpt");
        assert!(crate::profiles::configured(&runner.config.data).is_empty(), "the start-up copy is unchanged");
        // A file caught unreadable keeps the profiles last read.
        let written = std::fs::read_to_string(&path).unwrap();
        std::fs::write(&path, "{").unwrap();
        assert_eq!(crate::profiles::configured(&runner.config_now().data).len(), 1);
        // Taken out by hand: gone at the next read. Only profile keys are read again.
        let mut edited: Value = serde_json::from_str(&written).unwrap();
        edited.as_object_mut().unwrap().remove("profiles");
        edited["url"] = json!("http://elsewhere.invalid");
        std::fs::write(&path, edited.to_string()).unwrap();
        let now = runner.config_now();
        assert!(crate::profiles::configured(&now.data).is_empty());
        assert_eq!(now.url(), "http://127.0.0.1:9");
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn start_places_are_opt_in_and_a_server_rollback_drops_only_that_protocol() {
        let (url, seen) = crate::client::tests::serve(vec![
            (200, r#"{"attempt":null}"#),
            (200, r#"{"attempt":null}"#),
            (422, r#"{"error":{"code":"validation","detail":"body.start_places: Extra inputs are not permitted"}}"#),
            (200, r#"{"attempt":null}"#),
        ]).await;
        let dir = std::env::temp_dir().join(format!("tico-protocol-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url":url, "token":"synthetic", "runner_id":"r1", "projects_dir":dir.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let runner = Runner::new(config.clone(), Client::new(&config.url(), &config.token(), 5, 0).unwrap(), State::open(&dir.join("state")).unwrap());
        runner.claim_next().await.unwrap();
        runner.start_places.store(true, Ordering::SeqCst);
        runner.claim_next().await.unwrap();
        runner.claim_next().await.unwrap();
        let requests = seen.lock().unwrap();
        assert!(!requests[0].contains("start_places"), "older servers get their original contract");
        assert!(requests[1].contains("\"start_places\":true"));
        assert!(requests[2].contains("start_places") && !requests[3].contains("start_places"));
        assert!(!runner.start_places.load(Ordering::SeqCst));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_heartbeat_reply_sets_the_turn_limit_and_an_absent_one_returns_it_to_the_machines_capacity() {
        let (url, _seen) = crate::client::tests::serve(vec![
            (200, r#"{"ok":true,"turns_at_once":1}"#),
            (200, r#"{"ok":true}"#),
        ]).await;
        let dir = std::env::temp_dir().join(format!("tico-turn-limit-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url":url, "token":"synthetic", "runner_id":"r1", "capacity":3,
                                     "projects_dir":dir.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        let runner = Runner::new(config.clone(), client, State::open(&dir.join("state")).unwrap());
        assert_eq!(runner.turn_limit.load(Ordering::SeqCst), 3);
        let mut body = json!({"version": "test"});
        assert!(runner.post_heartbeat(&mut body).await.0.is_ok());
        assert_eq!(runner.turn_limit.load(Ordering::SeqCst), 1);
        assert!(runner.post_heartbeat(&mut body).await.0.is_ok());
        assert_eq!(runner.turn_limit.load(Ordering::SeqCst), 3);
        assert_eq!(runner.capacity, 3, "the machine's own capacity is what the heartbeat body reports");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_claim_whose_answer_was_lost_is_sent_again_with_its_key() {
        let (url, seen) = crate::client::tests::serve(vec![
            (503, r#"{"error":{"code":"unavailable","detail":"busy"}}"#),
            (200, r#"{"attempt":{"id":"a-late","bot":"ops"}}"#),
            (200, r#"{"attempt":null}"#),
        ]).await;
        let dir = std::env::temp_dir().join(format!("tico-lost-claim-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url":url, "token":"synthetic", "runner_id":"r1", "projects_dir":dir.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let runner = Runner::new(config.clone(), Client::new(&config.url(), &config.token(), 5, 0).unwrap(), State::open(&dir.join("state")).unwrap());
        assert!(runner.claim_next().await.is_err());
        runner.start_places.store(true, Ordering::SeqCst);             // the next claim's own body differs
        let claimed = runner.claim_next().await.unwrap();
        assert_eq!(claimed.attempt.unwrap()["id"], "a-late");
        runner.claim_next().await.unwrap();
        let requests = seen.lock().unwrap();
        let key = |r: &str| r.lines().find_map(|l| l.split_once(':').filter(|(k, _)| k.eq_ignore_ascii_case("idempotency-key")).map(|(_, v)| v.trim().to_string()));
        let body = |r: &str| r.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
        assert_eq!(key(&requests[0]), key(&requests[1]), "the lost claim is asked again under its own key");
        assert_eq!(body(&requests[0]), body(&requests[1]), "with the body the key was first sent with");
        assert_ne!(key(&requests[1]), key(&requests[2]), "an answered claim is not repeated");
        assert!(requests[2].contains("start_places"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn idle_claims_back_off_to_two_seconds() {
        let mut c = Clock { polled: HashMap::new(), next_claim: None, idle_claims: 0, claim_hint: (None, 2.0), claim_live_at: None, last_heartbeat: None };
        let waits: Vec<f64> = (0..5).map(|_| c.claim_wait(false)).collect();
        assert_eq!(waits, vec![0.5, 1.0, 2.0, 2.0, 2.0]);
        assert_eq!(c.claim_wait(true), 0.25);
    }
}
