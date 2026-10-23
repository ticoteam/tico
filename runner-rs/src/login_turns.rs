//! Optional one-turn-per-login queue (`one_turn_per_login`). Disabled for Codex: bots sharing a ChatGPT login
//! run concurrently and share the plan's usage allowance. Kept for harnesses that opt in.
//! A turn takes its login before its host starts and keeps it until the host stops; another bot's turn on the same
//! login waits for it in order, or runs on its own fallback (crate::execute). A login signed in with a key (an OpenAI
//! API key) is never held.
//!
//! In memory: this runner starts every turn on this computer, and a turn that ends, fails, runs out of time or
//! panics drops what it holds. That frees the login at once, unless the process of the host the turn started is still
//! running (a panic or a cancelled turn skips or cuts off its stop, or the host stopped answering but lives on): then
//! the login is kept until that process has been stopped and reaped. A restarted runner holds nothing.
//!
//! A login is never copied into another bot's home to get round this: a copy of a ChatGPT auth.json rotates apart
//! from the original (the guard test below). Concurrent turns use the original login in its own home.

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio::sync::oneshot;

/// How long a turn keeps a login while another turn waits for it (Dev 278). Past it the turn stops at its next safe
/// point and its continuation comes back right after the next waiter's turn; with nobody waiting there is no cap.
pub const HOLD_CAP: Duration = Duration::from_secs(20 * 60);
/// How long a turn past HOLD_CAP may run on to a safe point before it is interrupted.
pub const YIELD_GRACE: Duration = Duration::from_secs(5 * 60);
/// How often a waiter may be passed over by one that asked later before it goes next, whatever its class (K).
pub const MAX_SKIPS: u32 = 2;
/// How long a turn that gave way keeps its place right after the next waiter, for its continuation to arrive.
const RESERVED_FOR: Duration = Duration::from_secs(30 * 60);
/// How long a waiter whose wait ended without the login (its lease lapsed while renewals failed) keeps its place:
/// the same work queued again within it goes back where it was, not to the back.
const PLACE_KEPT_FOR: Duration = Duration::from_secs(10 * 60);

/// Which waiting turn takes a login when it comes free (Dev 278): release-blocking QA and validation, then reviews and
/// a person's own chat turn, then the rest; first come, first served within a class. The server sets it on the
/// attempt (`login_class`); a turn that names none is `Other`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Class {
    Release = 1,
    Review = 2,
    Other = 3,
}

impl Class {
    pub fn parse(text: &str) -> Class {
        match text {
            "release" => Class::Release,
            "review" => Class::Review,
            _ => Class::Other,
        }
    }
}

/// A turn that wants a login: its bot, the task it works as a person reads it ("" for none), its class, the work it
/// is for (the task's or the chat's id: a lapsed waiter queued again for it keeps its place) and its attempt.
#[derive(Clone, Debug, PartialEq)]
pub struct Turn {
    pub bot: String,
    pub task: String,
    pub class: Class,
    pub work: String,
    pub attempt: String,
}

impl Turn {
    pub fn new(bot: &str, task: &str, class: Class) -> Turn {
        Turn { bot: bot.into(), task: task.into(), class, work: String::new(), attempt: String::new() }
    }

    pub fn of(mut self, work: &str, attempt: &str) -> Turn {
        self.work = work.into();
        self.attempt = attempt.into();
        self
    }

    fn same_work(&self, other: &Turn) -> bool {
        !self.work.is_empty() && self.bot == other.bot && self.work == other.work
    }
}

/// What a waiting turn is told: the bot whose turn holds the login, its task, since when (seconds since the epoch),
/// and the waiter's place in the queue (1 = next).
#[derive(Clone, Debug, PartialEq)]
pub struct Waiting {
    pub holder: String,
    /// The holder's attempt, so a holder is always a run a person can find ("" when it named none).
    pub attempt: String,
    pub task: String,
    pub since: f64,
    pub position: usize,
}

/// A login that serves one turn at a time: `key` is the runtime and the login (crate::fallback::login: the
/// profile's own, or "" for the computer's), `label` how a person reads it.
#[derive(Clone, Debug, PartialEq)]
pub struct Login {
    pub key: String,
    pub label: String,
}

impl Login {
    pub fn new(runtime: &str, login: &str) -> Login {
        let label = if login.is_empty() { "this computer's ChatGPT login".to_string() } else { format!("ChatGPT login \"{login}\"") };
        Login { key: format!("{runtime}:{login}"), label }
    }

    /// The chat's and the run's line while a turn waits:
    /// "Waiting for <login> (held by <bot> on <task> since <HH:MM>Z; position N)".
    pub fn waiting(&self, w: &Waiting, bot: &str) -> String {
        let on = if w.task.is_empty() { String::new() } else { format!(" on {}", w.task) };
        format!("Waiting for {} (held by {}{on} since {}; position {})", self.label, who(&w.holder, bot), clock(w.since), w.position)
    }

    /// The run's note when the turn goes to its fallback instead: "ChatGPT login busy with <bot>; ...".
    pub fn busy(&self, holder: &str, bot: &str) -> String {
        let label = self.label.strip_prefix("this computer's ").map_or_else(|| self.label.clone(), String::from);
        format!("{label} busy with {}", who(holder, bot))
    }

    /// The run's note when its turn gave the login to the next waiter at the cap.
    pub fn yielded(&self) -> String {
        format!("Paused after {} min so the next turn could use {}; resumes next", HOLD_CAP.as_secs() / 60, self.label)
    }
}

fn who(holder: &str, bot: &str) -> String {
    if holder == bot { "another turn of this bot".into() } else { holder.into() }
}

/// "HH:MMZ" of a time in seconds since the epoch.
fn clock(epoch: f64) -> String {
    let s = epoch.max(0.0) as u64 % 86_400;
    format!("{:02}:{:02}Z", s / 3600, s / 60 % 60)
}

fn epoch_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or_default()
}

struct Holding {
    turn: Turn,
    since: Instant,
    at: f64,
}

struct Waiter {
    id: u64,
    /// When it asked, in queue order: kept across a lapsed lease (Kept).
    seq: u64,
    turn: Turn,
    skipped: u32,
    grant: Option<oneshot::Sender<Held>>,
}

/// The continuation a turn that gave way at the cap comes back as: the same bot on the same work (its task, or its
/// chat: a bot's chat turns share the task label ""), or on the same task label when the work is unknown.
struct Reserved {
    bot: String,
    task: String,
    work: String,
    until: Instant,
}

/// The place of a waiter whose wait ended without the login, for PLACE_KEPT_FOR.
struct Kept {
    turn: Turn,
    seq: u64,
    skipped: u32,
    until: Instant,
}

#[derive(Default)]
struct Slot {
    holder: Option<Holding>,
    waiters: Vec<Waiter>,
    reserved: Option<Reserved>,
    kept: Vec<Kept>,
    next_id: u64,
}

impl Slot {
    fn reserved_for(&self, turn: &Turn) -> bool {
        self.reserved.as_ref().is_some_and(|r| {
            r.bot == turn.bot && (if r.work.is_empty() { r.task == turn.task } else { r.work == turn.work }) && Instant::now() < r.until
        })
    }

    /// The waiters in the order they take the login: one passed over MAX_SKIPS times first, then a continuation
    /// whose turn gave way at the cap, then by class, each in the order they asked.
    fn order(&self) -> Vec<usize> {
        let mut at: Vec<usize> = (0..self.waiters.len()).collect();
        at.sort_by_key(|&i| {
            let w = &self.waiters[i];
            if w.skipped >= MAX_SKIPS {
                (0, 0, w.seq)
            } else if self.reserved_for(&w.turn) {
                (1, 0, w.seq)
            } else {
                (2, w.turn.class as u8, w.seq)
            }
        });
        at
    }

    fn waiting(&self, id: u64) -> Option<Waiting> {
        let h = self.holder.as_ref()?;
        let position = self.order().iter().position(|&i| self.waiters[i].id == id)? + 1;
        Some(Waiting { holder: h.turn.bot.clone(), attempt: h.turn.attempt.clone(), task: h.turn.task.clone(), since: h.at, position })
    }

    fn hold(&mut self, turn: Turn) {
        self.holder = Some(Holding { turn, since: Instant::now(), at: epoch_now() });
    }
}

fn slots() -> &'static Mutex<HashMap<String, Slot>> {
    static S: OnceLock<Mutex<HashMap<String, Slot>>> = OnceLock::new();
    S.get_or_init(Default::default)
}

/// The login is free: the next waiter takes it, or nobody holds it. A waiter that gave up just now hands it on.
fn release(key: &str) {
    let next = {
        let mut slots = slots().lock().unwrap();
        let Some(slot) = slots.get_mut(key) else { return };
        slot.holder = None;
        let Some(&first) = slot.order().first() else { return };
        let mut w = slot.waiters.remove(first);
        // Everyone who asked before it was passed over once more.
        for other in slot.waiters.iter_mut().filter(|o| o.seq < w.seq) {
            other.skipped += 1;
        }
        if slot.reserved_for(&w.turn) {
            slot.reserved = None;
        }
        slot.hold(w.turn.clone());
        w.grant.take()
    };
    // A waiter gone since (its receiver dropped) drops this Held, which frees the login for the next one.
    if let Some(grant) = next {
        let _ = grant.send(Held::new(key));
    }
}

/// What a held login waits for before it is free again: the host its turn started on the login. `running` is about
/// the process, not the protocol: a host that stopped answering, or whose stop was begun and cut off, may still run.
pub trait Lingering: Send + 'static {
    fn running(&self) -> bool;
    /// Stop the host if it still runs, and return once its process is gone.
    fn stop(self: Box<Self>) -> Pin<Box<dyn Future<Output = ()> + Send>>;
}

impl Lingering for crate::hosts::Host {
    fn running(&self) -> bool {
        crate::hosts::Host::running(self)
    }

    fn stop(self: Box<Self>) -> Pin<Box<dyn Future<Output = ()> + Send>> {
        Box::pin(async move { crate::hosts::Host::stop_and_reap(&self).await })
    }
}

/// A login held by one turn; dropping it (the turn's host stopped, or the turn ended any other way) frees it.
pub struct Held {
    key: String,
    host: Option<Box<dyn Lingering>>,
}

impl Held {
    fn new(key: &str) -> Held {
        Held { key: key.into(), host: None }
    }

    /// The host this turn runs on the login: if it is still running when the turn lets go, the login stays held
    /// until it has stopped, so no other turn's host ever runs beside it.
    pub fn bind(&mut self, host: impl Lingering) {
        self.host = Some(Box::new(host));
    }

    /// The run's note when this turn gave the login to the next waiter at the cap.
    pub fn yielded(&self) -> String {
        self.cap().yielded()
    }

    /// What the turn's drive loop watches for the cap while the turn runs.
    pub fn cap(&self) -> Cap {
        Cap { key: self.key.clone() }
    }

    /// This turn gives way at the cap: its continuation takes the login right after the next waiter's turn.
    pub fn give_way(&self) {
        let mut slots = slots().lock().unwrap();
        if let Some(slot) = slots.get_mut(&self.key) {
            if let Some(turn) = slot.holder.as_ref().map(|h| h.turn.clone()) {
                slot.reserved = Some(Reserved { bot: turn.bot, task: turn.task, work: turn.work, until: Instant::now() + RESERVED_FOR });
            }
        }
    }
}

/// A held login's cap, as the turn holding it watches it.
#[derive(Clone, Debug)]
pub struct Cap {
    key: String,
}

impl Cap {
    /// Whether the turn should give the login to a waiting turn: it has held it HOLD_CAP or longer (as of `now`) and
    /// another turn waits for it.
    pub fn due(&self, now: Instant) -> bool {
        let slots = slots().lock().unwrap();
        slots.get(&self.key).is_some_and(|s| {
            !s.waiters.is_empty() && s.holder.as_ref().is_some_and(|h| now.saturating_duration_since(h.since) >= hold_cap(&self.key))
        })
    }

    /// How long the turn may run on to a safe point once asked to give the login up (YIELD_GRACE).
    pub fn grace(&self) -> Duration {
        #[cfg(test)]
        if let Some(grace) = test_graces().lock().unwrap().get(&self.key) {
            return *grace;
        }
        YIELD_GRACE
    }

    pub fn yielded(&self) -> String {
        let (runtime, login) = self.key.split_once(':').unwrap_or((&self.key, ""));
        Login::new(runtime, login).yielded()
    }
}

#[cfg(test)]
fn test_caps() -> &'static Mutex<HashMap<String, Duration>> {
    static C: OnceLock<Mutex<HashMap<String, Duration>>> = OnceLock::new();
    C.get_or_init(Default::default)
}

/// A shorter cap for one test login, so a drive test need not run twenty minutes.
#[cfg(test)]
pub fn cap_for_test(key: &str, cap: Duration) {
    test_caps().lock().unwrap().insert(key.into(), cap);
}

#[cfg(test)]
fn test_graces() -> &'static Mutex<HashMap<String, Duration>> {
    static G: OnceLock<Mutex<HashMap<String, Duration>>> = OnceLock::new();
    G.get_or_init(Default::default)
}

/// A shorter grace for one test login, so a turn that never reaches a safe point is interrupted in the test's time.
#[cfg(test)]
pub fn grace_for_test(key: &str, grace: Duration) {
    test_graces().lock().unwrap().insert(key.into(), grace);
}

fn hold_cap(_key: &str) -> Duration {
    #[cfg(test)]
    if let Some(cap) = test_caps().lock().unwrap().get(_key) {
        return *cap;
    }
    HOLD_CAP
}

impl Drop for Held {
    fn drop(&mut self) {
        let key = std::mem::take(&mut self.key);
        match (self.host.take().filter(|h| h.running()), tokio::runtime::Handle::try_current()) {
            (Some(host), Ok(runtime)) => {
                runtime.spawn(async move {
                    host.stop().await;
                    release(&key);
                });
            }
            _ => release(&key),
        }
    }
}

/// The login if it is free now, else Err with the bot whose turn holds it.
pub fn try_take(key: &str, turn: &Turn) -> Result<Held, String> {
    match take_or_queue(key, turn, false) {
        Ok(held) => Ok(held),
        Err((_, holder)) => Err(holder),
    }
}

/// The login at once, or (with `queue`) a place in its queue: Err with the place (when queued) and the holder.
#[allow(clippy::type_complexity)]
fn take_or_queue(key: &str, turn: &Turn, queue: bool) -> Result<Held, (Option<(u64, oneshot::Receiver<Held>)>, String)> {
    let mut slots = slots().lock().unwrap();
    let slot = slots.entry(key.into()).or_default();
    if slot.holder.is_none() {
        slot.hold(turn.clone());
        if slot.reserved_for(turn) {
            slot.reserved = None;
        }
        return Ok(Held::new(key));
    }
    let holder = slot.holder.as_ref().map(|h| h.turn.bot.clone()).unwrap_or_default();
    if !queue {
        return Err((None, holder));
    }
    let (tx, rx) = oneshot::channel();
    slot.next_id += 1;
    let id = slot.next_id;
    let now = Instant::now();
    slot.kept.retain(|k| now < k.until);
    let (seq, skipped) = match slot.kept.iter().position(|k| k.turn.same_work(turn)) {
        Some(i) => {
            let k = slot.kept.remove(i);
            (k.seq, k.skipped)
        }
        None => (id, 0),
    };
    slot.waiters.push(Waiter { id, seq, turn: turn.clone(), skipped, grant: Some(tx) });
    Err((Some((id, rx)), holder))
}

/// A turn's place in a login's queue: leaving it (the wait was given up or is over) takes the turn out. Left
/// without the login, its place is kept a while for the same work (a lease that lapsed and is leased again).
struct Queued {
    key: String,
    id: u64,
}

impl Drop for Queued {
    fn drop(&mut self) {
        if let Some(slot) = slots().lock().unwrap().get_mut(&self.key) {
            if let Some(i) = slot.waiters.iter().position(|w| w.id == self.id) {
                let w = slot.waiters.remove(i);
                if !w.turn.work.is_empty() {
                    slot.kept.push(Kept { turn: w.turn, seq: w.seq, skipped: w.skipped, until: Instant::now() + PLACE_KEPT_FOR });
                }
            }
        }
    }
}

/// The work whose wait for `key` should not keep its place (its task is done or closed, or a person stopped it).
pub fn forget(key: &str, turn: &Turn) {
    if let Some(slot) = slots().lock().unwrap().get_mut(key) {
        slot.kept.retain(|k| !k.turn.same_work(turn));
    }
}

/// The login once it is this turn's turn (Slot::order).
pub async fn take(key: &str, turn: &Turn) -> Held {
    match wait_for(key, turn, || None, |_| {}, Duration::from_secs(3600)).await {
        Ok(held) => held,
        Err(_) => unreachable!("a wait that is never over ends only with the login"),
    }
}

/// The login for a turn that may wait for it: at once when it is free, else in its place in the queue. `over` says
/// why the turn should not run any more (its lease, a Stop, the computer stopping); it is asked before taking the
/// login, every `tick` while waiting and once more when the login is granted, so a cancelled turn never starts on it
/// (dropping it passes the login to the next turn in the queue). `waiting` hears who holds the login and the turn's
/// place when it starts to wait and whenever either changes.
pub async fn wait_for(key: &str, turn: &Turn, over: impl Fn() -> Option<String>, mut waiting: impl FnMut(&Waiting), tick: Duration) -> Result<Held, String> {
    if let Some(why) = over() {
        return Err(why);
    }
    let (id, granted) = match take_or_queue(key, turn, true) {
        Ok(held) => return Ok(held),
        Err((place, _)) => place.expect("a queued turn has its place"),
    };
    let _queued = Queued { key: key.into(), id };
    let mut told: Option<Waiting> = None;
    let mut tell = |told: &mut Option<Waiting>| {
        let now = slots().lock().unwrap().get(key).and_then(|s| s.waiting(id));
        if let Some(now) = now.filter(|n| told.as_ref() != Some(n)) {
            waiting(&now);
            *told = Some(now);
        }
    };
    tell(&mut told);
    tokio::pin!(granted);
    let held = loop {
        tokio::select! {
            held = &mut granted => break held.expect("a waiter's grant is sent before its place is given up"),
            _ = tokio::time::sleep(tick) => {
                if let Some(why) = over() {
                    return Err(why);
                }
                tell(&mut told);
            }
        }
    };
    if let Some(why) = over() {
        return Err(why);
    }
    Ok(held)
}

/// The bot whose turn holds the login, if one does.
pub fn holder(key: &str) -> Option<String> {
    slots().lock().unwrap().get(key).and_then(|s| s.holder.as_ref().map(|h| h.turn.bot.clone()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;

    fn t(bot: &str) -> Turn {
        Turn::new(bot, "", Class::Other)
    }

    /// Two turns on `keys` at once, each holding its login for a moment: the most that ran together.
    async fn overlap(keys: [&str; 2]) -> usize {
        let (now, most) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let turns = keys.map(|key| {
            let (now, most, key) = (now.clone(), most.clone(), key.to_string());
            tokio::spawn(async move {
                let _held = take(&key, &t("ops")).await;
                let running = now.fetch_add(1, Ordering::SeqCst) + 1;
                most.fetch_max(running, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(50)).await;
                now.fetch_sub(1, Ordering::SeqCst);
            })
        });
        for t in turns {
            t.await.unwrap();
        }
        most.load(Ordering::SeqCst)
    }

    #[tokio::test]
    async fn two_turns_on_one_login_run_one_after_the_other_and_other_logins_run_beside_them() {
        assert_eq!(overlap(["codex:test-same", "codex:test-same"]).await, 1);
        assert_eq!(overlap(["codex:test-a", "codex:test-b"]).await, 2);
        assert_eq!(overlap(["codex:", "codex:test-own"]).await, 2);
    }

    #[tokio::test]
    async fn a_busy_login_names_its_holder_and_is_free_again_once_the_turn_lets_go() {
        let key = "codex:test-busy";
        let held = try_take(key, &t("builder")).unwrap();
        assert_eq!(holder(key).as_deref(), Some("builder"));
        assert_eq!(try_take(key, &t("support")).err().as_deref(), Some("builder"));
        drop(held);
        assert_eq!(holder(key), None);
        let again = try_take(key, &t("support")).unwrap();
        assert_eq!(holder(key).as_deref(), Some("support"));
        drop(again);
    }

    #[tokio::test]
    async fn waiting_turns_take_the_login_in_the_order_they_asked() {
        let key = "codex:test-order";
        let first = try_take(key, &t("a")).unwrap();
        let order = Arc::new(Mutex::new(vec![]));
        let mut waiters = vec![];
        for bot in ["b", "c", "d"] {
            let order = order.clone();
            waiters.push(tokio::spawn(async move {
                let _held = take(key, &t(bot)).await;
                order.lock().unwrap().push(bot);
                tokio::time::sleep(Duration::from_millis(5)).await;
            }));
            tokio::time::sleep(Duration::from_millis(10)).await; // each one asks after the last
        }
        drop(first);
        for w in waiters {
            w.await.unwrap();
        }
        assert_eq!(*order.lock().unwrap(), ["b", "c", "d"]);
    }

    #[tokio::test]
    async fn a_turn_that_panics_or_runs_out_of_time_frees_the_login() {
        let key = "codex:test-crash";
        let crashed = tokio::spawn(async move {
            let _held = take(key, &t("ops")).await;
            panic!("the turn crashed");
        });
        assert!(crashed.await.unwrap_err().is_panic());
        assert_eq!(holder(key), None);
        drop(try_take(key, &t("next")).expect("free after a panic"));
        // A turn cut off at its time limit (the future dropped mid-turn) lets go too.
        let timed_out = tokio::time::timeout(Duration::from_millis(20), async move {
            let _held = take(key, &t("ops")).await;
            tokio::time::sleep(Duration::from_secs(60)).await;
        })
        .await;
        assert!(timed_out.is_err());
        drop(try_take(key, &t("next")).expect("free after a timeout"));
        // A turn that gave up waiting leaves the queue: the login is not kept for it.
        let held = try_take(key, &t("a")).unwrap();
        assert!(tokio::time::timeout(Duration::from_millis(20), take(key, &t("gave-up"))).await.is_err());
        drop(held);
        assert_eq!(try_take(key, &t("b")).map(|_| ()).err(), None);
    }

    /// A real Codex host on a stand-in app-server: it answers `initialize`, writes its pid to $FAKE_PID, and then
    /// runs until it is stopped. With FAKE_MODE=deaf it ignores TERM (only the KILL five seconds later ends it); with
    /// FAKE_MODE=silent it closes its output once started and runs on; otherwise a TERM ends it half a second later.
    async fn codex_host(mode: &str) -> (crate::hosts::Host, i32, std::path::PathBuf) {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("tico-login-host-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let program = dir.join("codex");
        std::fs::write(&program, r#"#!/usr/bin/env python3
import json, os, signal, sys, time
mode = os.environ.get("FAKE_MODE", "")
with open(os.environ["FAKE_PID"], "w") as f:
    f.write(str(os.getpid()))
if mode == "deaf":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.5), os._exit(0)))
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
    if msg.get("method") == "initialized" and mode == "silent":
        sys.stdout = open(os.devnull, "w")
        os.close(1)
time.sleep(60)
"#).unwrap();
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        let pid_file = dir.join("pid");
        let path = format!("{}:{}", dir.display(), std::env::var("PATH").unwrap_or_default());
        let env: crate::env::Env = [("PATH".to_string(), path),
                                    ("FAKE_MODE".to_string(), mode.to_string()),
                                    ("FAKE_PID".to_string(), pid_file.display().to_string())].into_iter().collect();
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = crate::hosts::Host::Codex(crate::hosts::codex::CodexHost::on_home(env, tx, None));
        host.start().await.expect("the stand-in app-server starts");
        let pid = std::fs::read_to_string(&pid_file).unwrap().trim().parse().unwrap();
        (host, pid, dir)
    }

    /// Whether `pid` is still a process (a zombie not yet reaped counts).
    fn exists(pid: i32) -> bool {
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[tokio::test]
    async fn a_codex_turn_cut_off_while_its_host_is_stopping_keeps_the_login_until_the_app_server_is_reaped() {
        let key = "codex:test-cut-stop";
        let (host, pid, _dir) = codex_host("deaf").await;
        let mut held = take(key, &t("ops")).await;
        held.bind(host.clone());
        // The turn's own cleanup begins the stop and is cut off while the app-server, deaf to TERM, still runs: the
        // protocol says not alive already, the process lives on until the KILL five seconds later.
        let started = std::time::Instant::now();
        assert!(tokio::time::timeout(Duration::from_millis(300), host.stop()).await.is_err());
        assert!(!host.alive() && host.running() && exists(pid));
        drop(held);
        tokio::time::sleep(Duration::from_secs(1)).await;
        assert_eq!(try_take(key, &t("next")).map(|_| ()).err().as_deref(), Some("ops"), "free while the app-server still ran");
        let next = tokio::time::timeout(Duration::from_secs(15), take(key, &t("next"))).await.expect("free once reaped");
        assert!(!exists(pid), "the next turn got the login before the app-server was reaped");
        assert!(started.elapsed() >= Duration::from_secs(4), "{:?}", started.elapsed());
        drop(next);
    }

    #[tokio::test]
    async fn a_turn_that_panics_with_its_codex_host_running_keeps_the_login_until_the_app_server_is_reaped() {
        let key = "codex:test-panic";
        // An app-server that closed its output but still runs: the protocol says not alive, the process is there.
        let (host, pid, _dir) = codex_host("silent").await;
        tokio::time::timeout(Duration::from_secs(5), async {
            while host.alive() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the closed output is seen");
        assert!(host.running() && exists(pid));
        let crashed = tokio::spawn(async move {
            let mut held = take(key, &t("ops")).await;
            held.bind(host);
            panic!("the worker crashed mid-turn");
        });
        assert!(crashed.await.unwrap_err().is_panic());
        assert_eq!(try_take(key, &t("next")).map(|_| ()).err().as_deref(), Some("ops"));
        let next = tokio::time::timeout(Duration::from_secs(15), take(key, &t("next"))).await.expect("free once reaped");
        assert!(!exists(pid), "the next turn got the login while the crashed turn's app-server still ran");
        drop(next);

        // A turn cut off mid-run (its future dropped) is the same.
        let (host, pid, _dir) = codex_host("").await;
        let h = host.clone();
        let cut = tokio::time::timeout(Duration::from_millis(50), async move {
            let mut held = take(key, &t("ops")).await;
            held.bind(h);
            std::future::pending::<()>().await;
        });
        assert!(cut.await.is_err());
        let next = tokio::time::timeout(Duration::from_secs(15), take(key, &t("next"))).await.expect("free once reaped");
        assert!(!exists(pid) && !host.running());
        drop(next);

        // A host the turn already stopped frees the login at once.
        let (host, _pid, _dir) = codex_host("").await;
        host.stop_and_reap().await;
        let mut held = try_take(key, &t("ops")).unwrap();
        held.bind(host);
        drop(held);
        drop(try_take(key, &t("next")).expect("free at once"));
    }

    #[tokio::test]
    async fn a_waiting_turn_that_is_over_never_takes_the_login_and_the_next_one_does() {
        let over = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let why = |over: &Arc<std::sync::atomic::AtomicBool>| {
            let over = over.clone();
            move || over.load(Ordering::SeqCst).then(|| "Execution interrupted after stop".to_string())
        };
        // Already stopped, login free: refused, and the login stays free.
        let key = "codex:test-over-free";
        over.store(true, Ordering::SeqCst);
        assert!(wait_for(key, &t("ops"), why(&over), |_| {}, Duration::from_secs(60)).await.is_err());
        assert!(try_take(key, &t("next")).is_ok());

        // Stopped just before the holder lets go, with no tick in between: the grant is checked too, and the turn
        // queued behind it gets the login.
        let key = "codex:test-over-grant";
        over.store(false, Ordering::SeqCst);
        let first = try_take(key, &t("builder")).unwrap();
        let said = Arc::new(Mutex::new(vec![]));
        let heard = said.clone();
        let ops = why(&over);
        let stopped = tokio::spawn(async move { wait_for(key, &t("ops"), ops, move |w| heard.lock().unwrap().push(w.holder.clone()), Duration::from_secs(60)).await });
        tokio::time::sleep(Duration::from_millis(20)).await;
        let behind = tokio::spawn(async move { wait_for(key, &t("support"), || None, |_| {}, Duration::from_secs(60)).await });
        tokio::time::sleep(Duration::from_millis(20)).await;
        over.store(true, Ordering::SeqCst);
        drop(first);
        assert!(stopped.await.unwrap().is_err(), "a stopped turn started on the login");
        let next = tokio::time::timeout(Duration::from_secs(5), behind).await.expect("the next waiter proceeds").unwrap().unwrap();
        assert_eq!(holder(key).as_deref(), Some("support"));
        assert_eq!(*said.lock().unwrap(), ["builder"]);
        drop(next);

        // A lease lost while the holder still runs ends the wait at the next tick.
        let key = "codex:test-over-lost";
        over.store(false, Ordering::SeqCst);
        let first = try_take(key, &t("builder")).unwrap();
        let ops = why(&over);
        let lost = tokio::spawn(async move { wait_for(key, &t("ops"), ops, |_| {}, Duration::from_millis(10)).await });
        tokio::time::sleep(Duration::from_millis(30)).await;
        over.store(true, Ordering::SeqCst);
        assert!(tokio::time::timeout(Duration::from_secs(5), lost).await.expect("the wait ends").unwrap().is_err());
        assert_eq!(holder(key).as_deref(), Some("builder"));
        drop(first);
        assert!(try_take(key, &t("next")).is_ok(), "the given-up waiter left the queue");
    }

    /// No code path copies a login file into another home. Switch account signs in inside the login's own home
    /// (login.rs begin_switch) and seeds nothing for Codex; the only other copy is the replay recorder's, test-only.
    #[test]
    fn no_code_path_copies_a_codex_login_between_homes() {
        let login = crate::harness_tools::login_command("codex").expect("Codex has a login section");
        assert!(login.seed.is_empty(), "Switch account copies {:?} into the side home", login.seed);
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let (mut files, mut dirs) = (vec![], vec![src.clone()]);
        while let Some(d) = dirs.pop() {
            for e in std::fs::read_dir(&d).unwrap().flatten() {
                let p = e.path();
                if p.is_dir() {
                    dirs.push(p);
                } else if p.extension().is_some_and(|x| x == "rs") {
                    files.push(p);
                }
            }
        }
        let copying = regex::Regex::new(r#"fs::copy|fs::write|fs::rename|write_private|"cp"|\bcopy\b"#).unwrap();
        let mut found = vec![];
        for path in files {
            let rel = path.strip_prefix(&src).unwrap().display().to_string();
            if rel == "hosts/replay.rs" || rel == "login_turns.rs" {
                continue; // test-only (hosts/mod.rs: #[cfg(test)] mod replay), and this guard itself
            }
            let text = std::fs::read_to_string(&path).unwrap();
            // Production code only: everything before the file's own test module (`mod` or `pub(crate) mod`).
            let code = ["#[cfg(test)]\nmod ", "#[cfg(test)]\npub(crate) mod "].iter().filter_map(|m| text.find(m)).min()
                .map_or(text.as_str(), |at| &text[..at]);
            for (n, line) in code.lines().enumerate() {
                if line.contains("auth.json") && copying.is_match(line) {
                    found.push(format!("{rel}:{}: {}", n + 1, line.trim()));
                }
            }
        }
        assert!(found.is_empty(), "a login file is copied: {found:#?}");
    }

    #[test]
    fn the_lines_name_the_login_who_holds_it_on_what_since_when_and_the_place() {
        let computer = Login::new("codex", "");
        assert_eq!(computer.key, "codex:");
        let since = 20_000.0 * 86_400.0 + 19.0 * 3600.0 + 6.0 * 60.0;
        let w = Waiting { holder: "pr-reviewer-sol".into(), attempt: String::new(), task: "Review 1: PR #1005".into(), since, position: 2 };
        assert_eq!(computer.waiting(&w, "qa-engineer"),
                   "Waiting for this computer's ChatGPT login (held by pr-reviewer-sol on Review 1: PR #1005 since 19:06Z; position 2)");
        assert_eq!(computer.busy("builder", "support"), "ChatGPT login busy with builder");
        let work = Login::new("codex", "work");
        let own = Waiting { holder: "support".into(), attempt: String::new(), task: String::new(), since: since + 59.0 * 60.0, position: 1 };
        assert_eq!(work.waiting(&own, "support"), "Waiting for ChatGPT login \"work\" (held by another turn of this bot since 20:05Z; position 1)");
        assert_eq!(work.busy("builder", "support"), "ChatGPT login \"work\" busy with builder");
        assert_eq!(work.yielded(), "Paused after 20 min so the next turn could use ChatGPT login \"work\"; resumes next");
    }

    /// `waiters` queue on `key` one after the other while `held_by` holds it; each, once it has the login, notes itself
    /// and lets go. The order they had it in.
    async fn served(key: &'static str, held_by: Held, waiters: Vec<Turn>) -> Vec<String> {
        let order = Arc::new(Mutex::new(vec![]));
        let mut turns = vec![];
        for turn in waiters {
            let order = order.clone();
            turns.push(tokio::spawn(async move {
                let _held = take(key, &turn).await;
                order.lock().unwrap().push(turn.bot.clone());
                tokio::time::sleep(Duration::from_millis(5)).await;
            }));
            tokio::time::sleep(Duration::from_millis(10)).await; // each one asks after the last
        }
        drop(held_by);
        for t in turns {
            t.await.unwrap();
        }
        let order = order.lock().unwrap().clone();
        order
    }

    #[tokio::test]
    async fn a_release_blocking_qa_turn_goes_ahead_of_earlier_reviews_and_reviews_ahead_of_the_rest() {
        let key = "codex:test-class";
        let held_by = try_take(key, &t("builder")).unwrap();
        let order = served(key, held_by, vec![
            Turn::new("support", "Weekly digest", Class::Other),
            Turn::new("pr-reviewer-sol", "Review 1: PR #1005", Class::Review),
            Turn::new("qa-engineer", "QA: PR #987", Class::Release),
        ]).await;
        assert_eq!(order, ["qa-engineer", "pr-reviewer-sol", "support"]);
        assert_eq!(holder(key), None);
    }

    #[tokio::test]
    async fn an_ordinary_turn_passed_over_twice_goes_next_whatever_waits_behind_it() {
        let key = "codex:test-starve";
        let held_by = try_take(key, &t("builder")).unwrap();
        let release = |bot: &str| Turn::new(bot, "QA: PR #1", Class::Release);
        let order = served(key, held_by, vec![t("support"), release("qa-1"), release("qa-2"), release("qa-3"), release("qa-4")]).await;
        assert_eq!(MAX_SKIPS, 2);
        assert_eq!(order, ["qa-1", "qa-2", "support", "qa-3", "qa-4"]);
    }

    #[tokio::test]
    async fn a_waiting_turn_hears_who_holds_the_login_on_what_and_its_place_as_they_change() {
        let key = "codex:test-place";
        let held_by = try_take(key, &Turn::new("pr-reviewer-sol", "Review 1: PR #1005", Class::Review)).unwrap();
        let heard = Arc::new(Mutex::new(Vec::<Waiting>::new()));
        let h = heard.clone();
        let review = tokio::spawn(async move {
            let turn = Turn::new("reviewer-2", "Review 2: PR #11", Class::Review);
            wait_for(key, &turn, || None, move |w| h.lock().unwrap().push(w.clone()), Duration::from_millis(2)).await
        });
        tokio::time::sleep(Duration::from_millis(20)).await;
        {
            let heard = heard.lock().unwrap();
            assert_eq!(heard.len(), 1, "{heard:?}");
            assert_eq!((heard[0].holder.as_str(), heard[0].task.as_str(), heard[0].position), ("pr-reviewer-sol", "Review 1: PR #1005", 1));
            assert!(heard[0].since > 1_700_000_000.0);
        }
        // A release-blocking turn asks later and goes first: the review hears it is second now.
        let qa = tokio::spawn(async move {
            let _held = take(key, &Turn::new("qa-engineer", "QA: PR #987", Class::Release)).await;
            tokio::time::sleep(Duration::from_millis(50)).await;
        });
        tokio::time::sleep(Duration::from_millis(20)).await;
        assert_eq!(heard.lock().unwrap().last().map(|w| w.position), Some(2));
        drop(held_by);
        qa.await.unwrap();
        let held = review.await.unwrap().expect("the review has the login");
        assert_eq!(holder(key).as_deref(), Some("reviewer-2"));
        let last = heard.lock().unwrap().last().cloned().unwrap();
        assert_eq!((last.holder.as_str(), last.task.as_str(), last.position), ("qa-engineer", "QA: PR #987", 1));
        drop(held);
    }

    #[tokio::test]
    async fn a_waiter_whose_lease_lapses_and_is_leased_again_keeps_its_place() {
        let key = "codex:test-lapse";
        let held_by = try_take(key, &Turn::new("builder", "Dev 278", Class::Other).of("task-278", "att-1")).unwrap();
        let review = Turn::new("pr-reviewer-sol", "Review 1: PR #1009", Class::Review).of("task-1009", "att-2");
        let lapsed = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let l = lapsed.clone();
        let (r, place) = (review.clone(), Arc::new(Mutex::new(0)));
        let p = place.clone();
        let first = tokio::spawn(async move {
            wait_for(key, &r, move || l.load(Ordering::SeqCst).then(|| "Lease expired".to_string()), move |w| *p.lock().unwrap() = w.position, Duration::from_millis(2)).await
        });
        tokio::time::sleep(Duration::from_millis(10)).await;
        let later = tokio::spawn(async move { drop(take(key, &Turn::new("support", "Digest", Class::Review).of("task-9", "att-3")).await) });
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert_eq!(*place.lock().unwrap(), 1);
        lapsed.store(true, Ordering::SeqCst);
        assert!(first.await.unwrap().is_err(), "the lapsed wait ends");
        // Leased again as a new attempt of the same work: still first, ahead of the review that asked after it.
        let again = review.clone().of("task-1009", "att-4");
        let (p, heard) = (Arc::new(Mutex::new(0)), Arc::new(Mutex::new(String::new())));
        let (p2, h2) = (p.clone(), heard.clone());
        let back = tokio::spawn(async move {
            let held = wait_for(key, &again, || None, move |w| *p2.lock().unwrap() = w.position, Duration::from_millis(2)).await.unwrap();
            *h2.lock().unwrap() = holder(key).unwrap_or_default();
            tokio::time::sleep(Duration::from_millis(20)).await;
            drop(held);
        });
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert_eq!(*p.lock().unwrap(), 1);
        drop(held_by);
        back.await.unwrap();
        later.await.unwrap();
        assert_eq!(*heard.lock().unwrap(), "pr-reviewer-sol");
        // Work whose task is done or closed is forgotten: queued again, it goes to the back.
        forget(key, &review);
    }

    #[tokio::test]
    async fn the_holders_attempt_is_named_to_waiters() {
        let key = "codex:test-attempt";
        let held = try_take(key, &Turn::new("pr-reviewer-sol", "Review 1", Class::Review).of("task-1", "att-holder")).unwrap();
        let heard = Arc::new(Mutex::new(String::new()));
        let h = heard.clone();
        let w = tokio::spawn(async move {
            wait_for(key, &t("qa"), || None, move |w| *h.lock().unwrap() = w.attempt.clone(), Duration::from_millis(2)).await.map(drop)
        });
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert_eq!(*heard.lock().unwrap(), "att-holder");
        drop(held);
        w.await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn a_turn_is_due_to_give_way_only_past_the_cap_and_only_while_another_waits() {
        let key = "codex:test-cap";
        let held = try_take(key, &t("builder")).unwrap();
        let start = Instant::now();
        assert!(!held.cap().due(start + HOLD_CAP + Duration::from_secs(3600)), "nobody waits: no cap");
        let waiter = tokio::spawn(async move { drop(take(key, &t("qa")).await) });
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(!held.cap().due(Instant::now()), "someone waits, but the cap is not reached");
        assert!(!held.cap().due(start + HOLD_CAP - Duration::from_secs(1)));
        assert!(held.cap().due(start + HOLD_CAP));
        drop(held);
        waiter.await.unwrap();
        assert_eq!(holder(key), None);
    }

    #[tokio::test]
    async fn a_turn_that_gives_way_at_the_cap_comes_back_right_after_the_next_waiter() {
        let key = "codex:test-give-way";
        let builder = Turn::new("builder", "Dev 278", Class::Other);
        let held = try_take(key, &builder).unwrap();
        let order = Arc::new(Mutex::new(vec![]));
        let queue = |turn: Turn, order: Arc<Mutex<Vec<String>>>| tokio::spawn(async move {
            let _held = take(key, &turn).await;
            order.lock().unwrap().push(turn.bot.clone());
            tokio::time::sleep(Duration::from_millis(30)).await;
        });
        let qa = queue(Turn::new("qa-engineer", "QA: PR #987", Class::Release), order.clone());
        tokio::time::sleep(Duration::from_millis(10)).await;
        let other = queue(t("support"), order.clone());
        tokio::time::sleep(Duration::from_millis(10)).await;
        held.give_way();
        drop(held);
        tokio::time::sleep(Duration::from_millis(10)).await;
        // The continuation asks after both, while the QA turn has the login: it is next, ahead of the earlier one.
        let again = queue(builder.clone(), order.clone());
        for t in [qa, other, again] {
            t.await.unwrap();
        }
        assert_eq!(*order.lock().unwrap(), ["qa-engineer", "builder", "support"]);
        // Its place was used: a later turn of the same task waits its turn like any other.
        let held = try_take(key, &t("ops")).unwrap();
        let order = served(key, held, vec![t("support"), builder]).await;
        assert_eq!(order, ["support", "builder"]);
    }

    #[tokio::test]
    async fn turns_of_every_class_on_one_login_still_run_one_at_a_time() {
        let key = "codex:test-classes-overlap";
        let (now, most) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let first = try_take(key, &t("builder")).unwrap();
        let mut turns = vec![];
        for (n, class) in [Class::Other, Class::Release, Class::Review, Class::Other, Class::Release, Class::Review].into_iter().enumerate() {
            let (now, most) = (now.clone(), most.clone());
            turns.push(tokio::spawn(async move {
                let mut held = take(key, &Turn::new(&format!("bot-{n}"), "", class)).await;
                let running = now.fetch_add(1, Ordering::SeqCst) + 1;
                most.fetch_max(running, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(5)).await;
                if n % 2 == 0 {
                    held.give_way();
                }
                now.fetch_sub(1, Ordering::SeqCst);
                held.host = None;
            }));
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
        drop(first);
        for t in turns {
            t.await.unwrap();
        }
        assert_eq!(most.load(Ordering::SeqCst), 1);
        assert_eq!(holder(key), None);
    }
}
