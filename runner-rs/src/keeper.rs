//! The session keeper (T3 Code's orchestration-v2 ProviderSessionManager): a harness session stays running after its
//! thread's turn ends, so the thread's next message goes into the same process and conversation. The hub still
//! claims, starts and completes every message; only the process is reused.
//!
//! A harness plugs in as a `Session` (hosts/antigravity.rs `Agent` now; Claude and Codex next). The runner keeps one
//! `Keeper` (Runner::keeper) for all of them, one session per thread, under the start inputs it was started with
//! (`Inputs`: its command line, home or plan, environment, folder and MCP servers, `crate::warm::PER_ATTEMPT` aside). A
//! host `take`s its thread's session for a turn, which comes back only when the turn would start it the same way and
//! it still runs, and `put`s it back when the turn ends well, with whatever its workspace needs while it is kept
//! (`held`: a person's own chat's copy of the checkout, crate::sessions::PersonalCopy). Between turns it holds no token
//! (`Session::lend`, blank; one that cannot be blanked is not kept) and no turn reads what it says (`Session::park`).
//!
//! A kept session is released (stopped, and whatever it held removed with it) after `IDLE` unused, unless it reports
//! background work still running (`Session::background`): then it is pinned and rechecked each `IDLE`, up to
//! `MAX_PIN` in all. It also goes when more than `limit` are kept (the oldest first), when its output ends, when its
//! thread's next turn starts it differently (the inputs that changed are logged by name), and when the runner stops.
//! The runner prunes every `PRUNE_EVERY`, and a turn never takes one past its time. A released conversation comes back
//! by the harness's native resume on the thread's next message.

use std::any::Any;
use std::collections::{BTreeMap, HashMap};
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::sync::oneshot;

/// How long a kept session may sit unused (T3's DEFAULT_IDLE_TIMEOUT_MS, for harnesses with a native resume).
pub const IDLE: Duration = Duration::from_secs(2 * 60);
/// How long background work may keep an unused session past `IDLE` (T3's DEFAULT_MAX_IDLE_PIN_MS).
pub const MAX_PIN: Duration = Duration::from_secs(4 * 60 * 60);
/// How long a stopped turn's harness gets to end it before it is stopped itself (T3's PROVIDER_INTERRUPT_TIMEOUT_MS),
/// and how long a kept session gets to come back or stop before it is dropped (its process group killed).
pub const INTERRUPT_WAIT: Duration = Duration::from_secs(if cfg!(test) { 2 } else { 15 });
/// How often the runner releases what is past its time (Runner::run).
pub const PRUNE_EVERY: Duration = Duration::from_secs(15);

/// Whether a kept session's background work still runs (`Session::background`).
pub type Probe = Arc<dyn Fn() -> bool + Send + Sync>;
/// A session while it is kept: it ends, giving the session back, once wanted or once its output has ended.
pub type Parked = Pin<Box<dyn Future<Output = Box<dyn Session>> + Send>>;
/// A session being stopped.
pub type Stopping<'a> = Pin<Box<dyn Future<Output = ()> + Send + 'a>>;
/// What a kept session's workspace needs while it is kept, dropped once it has gone.
pub type Held = Box<dyn Any + Send>;
/// SIGKILL a session's process group now; done once it has exited.
pub type Kill = Arc<dyn Fn() -> Stopping<'static> + Send + Sync>;

/// How long a killed process group has to exit before what it held goes regardless.
const KILL_WAIT: Duration = Duration::from_secs(5);

/// A harness's running session, as the keeper holds it between turns. Dropped, it kills its process group.
pub trait Session: Any + Send {
    /// Whether it can take another turn: its process runs and its output is open.
    fn alive(&self) -> bool;
    /// The hub token for the turn it runs, or "" while kept (crate::warm::write_token_for on its token file): false when
    /// it could not be written.
    fn lend(&self, token: &str) -> bool;
    /// Its background work (T3's hasPendingBackgroundWork), if it can have any: while that runs, it stays past `IDLE`.
    fn background(&self) -> Option<Probe> {
        None
    }
    /// Held between turns: no turn's events, what it asks answered or refused, until `wanted` fires or its output
    /// ends; then it is given back.
    fn park(self: Box<Self>, wanted: oneshot::Receiver<()>) -> Parked;
    /// Stop it: its input closed, it and its process group reaped. What it held goes when it is dropped.
    fn stop(&self) -> Stopping<'_>;
    /// How to kill it outright once a stop is out of time (taken before it is parked, so it reaches a stuck one).
    fn kill(&self) -> Kill;
    /// Itself, for `take` to give the host its own type back.
    fn into_any(self: Box<Self>) -> Box<dyn Any + Send>;
}

/// What a session was started with, each input by name, as a digest of its value: a session is reused only for the same
/// inputs, and the names of those that changed say why one was not.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Inputs(BTreeMap<String, String>);

impl Inputs {
    pub fn add(&mut self, name: &str, value: &str) {
        let digest = ring::digest::digest(&ring::digest::SHA256, value.as_bytes());
        let hex: String = digest.as_ref().iter().map(|b| format!("{b:02x}")).collect();
        self.0.insert(name.to_string(), hex);
    }

    /// Every variable of `env` but `crate::warm::PER_ATTEMPT` and `skip`, each as `env:<name>`.
    pub fn env(&mut self, env: &crate::env::Env, skip: &[&str]) {
        for (k, v) in env {
            if !crate::warm::PER_ATTEMPT.contains(&k.as_str()) && !skip.contains(&k.as_str()) {
                self.add(&format!("env:{k}"), v);
            }
        }
    }

    /// The names of the inputs that differ from `other`'s.
    pub fn changed(&self, other: &Inputs) -> Vec<String> {
        let mut names: Vec<String> = self
            .0
            .iter()
            .filter(|(k, v)| other.0.get(*k) != Some(*v))
            .map(|(k, _)| k.clone())
            .collect();
        names.extend(other.0.keys().filter(|k| !self.0.contains_key(*k)).cloned());
        names
    }
}

/// One kept session.
struct Kept {
    key: Inputs,
    used: Instant,
    /// Since when background work has kept it past `IDLE`.
    pinned: Option<Instant>,
    background: Option<Probe>,
    wanted: oneshot::Sender<()>,
    task: tokio::task::JoinHandle<Box<dyn Session>>,
    kill: Kill,
    held: Option<Held>,
}

impl Kept {
    /// The session, back from `park` by `by`, and what it holds, still held; one not back by then is killed (its
    /// process group) and its task aborted.
    async fn back(self, by: tokio::time::Instant) -> (Option<Box<dyn Session>>, Option<Held>) {
        let _ = self.wanted.send(());
        let mut task = self.task;
        match tokio::time::timeout_at(by, &mut task).await {
            Ok(session) => (session.ok(), self.held),
            Err(_) => {
                killed(&self.kill).await;
                task.abort();
                let _ = task.await;
                (None, self.held)
            }
        }
    }

    /// Stopped within INTERRUPT_WAIT in all, else killed; what it held goes only after.
    async fn end(self) {
        let by = tokio::time::Instant::now() + INTERRUPT_WAIT;
        let kill = self.kill.clone();
        let (session, held) = self.back(by).await;
        if let Some(session) = session {
            stopped(session.stop(), &kill, by).await;
        }
        drop(held);
    }
}

/// A session's `stop` done by `by`, else its process group killed.
async fn stopped(stop: Stopping<'_>, kill: &Kill, by: tokio::time::Instant) {
    if tokio::time::timeout_at(by, stop).await.is_err() {
        killed(kill).await;
    }
}

async fn killed(kill: &Kill) {
    let _ = tokio::time::timeout(KILL_WAIT, kill()).await;
}

/// Stop `sessions` together.
async fn end_all(sessions: Vec<Kept>) {
    let mut ending = tokio::task::JoinSet::new();
    for k in sessions {
        ending.spawn(k.end());
    }
    while ending.join_next().await.is_some() {}
}

/// The sessions kept between their threads' turns (module doc).
pub struct Keeper {
    /// Where each kept session's token folder goes (`folder`).
    dir: PathBuf,
    limit: usize,
    idle: Duration,
    max_pin: Duration,
    kept: Mutex<HashMap<String, Kept>>,
    /// Stops `put` set off, joined when the runner stops (`prune` with `close`).
    stopping: Mutex<Vec<tokio::task::JoinHandle<()>>>,
}

impl Keeper {
    pub fn new(dir: PathBuf, limit: usize) -> Keeper {
        Keeper::with_times(dir, limit, IDLE, MAX_PIN)
    }

    pub fn with_times(dir: PathBuf, limit: usize, idle: Duration, max_pin: Duration) -> Keeper {
        Keeper {
            dir,
            limit,
            idle,
            max_pin,
            kept: Mutex::new(HashMap::new()),
            stopping: Mutex::new(Vec::new()),
        }
    }

    fn kept(&self) -> std::sync::MutexGuard<'_, HashMap<String, Kept>> {
        self.kept.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// A new token folder for a session that may be kept (crate::warm::folder).
    pub fn folder(&self) -> Result<PathBuf, String> {
        crate::warm::folder(&self.dir)
    }

    /// The session kept for `thread`, lent `token`, when it was started with `key`, is within its time and still runs;
    /// any other is stopped and None comes back.
    pub async fn take<S: Session>(&self, thread: &str, key: &Inputs, token: &str) -> Option<S> {
        let mut kept = self.kept().remove(thread)?;
        let changed = kept.key.changed(key);
        let due = self.due(&mut kept, Instant::now());
        let by = tokio::time::Instant::now() + INTERRUPT_WAIT;
        let kill = kept.kill.clone();
        // What it held goes once it is stopped, or once the turn taking it holds its own.
        let (session, _held) = kept.back(by).await;
        let session = session?;
        if !changed.is_empty() {
            // Names only: the values may be secrets.
            crate::util::log(&format!(
                "Tico runner: a kept session starts cold, its start changed: {}",
                changed.join(", ")
            ));
        }
        if !changed.is_empty() || due || !session.alive() {
            stopped(session.stop(), &kill, by).await;
            return None;
        }
        // A thread's key names its harness's own command line, so another harness's session never has it.
        let session = session.into_any().downcast::<S>().ok()?;
        if !session.lend(token) {
            stopped(session.stop(), &kill, by).await;
            return None;
        }
        Some(*session)
    }

    /// Keep `session`, started with `key`, for `thread`'s next turn, with `held` until it goes: its token blanked,
    /// parked until then. One already kept for the thread is stopped; one whose token cannot be blanked is not kept.
    pub async fn put<S: Session>(&self, thread: &str, key: Inputs, session: S, held: Option<Held>) {
        let kill = session.kill();
        if !session.lend("") {
            stopped(
                session.stop(),
                &kill,
                tokio::time::Instant::now() + INTERRUPT_WAIT,
            )
            .await;
            return;
        }
        let background = session.background();
        let (wanted, parked) = oneshot::channel();
        let task = tokio::spawn(Box::new(session).park(parked));
        let old = self.kept().insert(
            thread.to_string(),
            Kept {
                key,
                used: Instant::now(),
                pinned: None,
                background,
                wanted,
                task,
                kill,
                held,
            },
        );
        // Stopped off the turn's path: the turn that gave this one back does not wait on others' stops.
        let mut ending = self.overdue(Instant::now(), false);
        ending.extend(old);
        if !ending.is_empty() {
            let mut stopping = self.stopping.lock().unwrap_or_else(|p| p.into_inner());
            stopping.retain(|stop| !stop.is_finished());
            stopping.push(tokio::spawn(end_all(ending)));
        }
    }

    /// Stop the session kept for `thread`, if any.
    pub async fn release(&self, thread: &str) {
        let kept = self.kept().remove(thread);
        if let Some(kept) = kept {
            kept.end().await;
        }
    }

    /// Whether `k` is past its time as of `now`: unused for `idle`, unless pinned by background work (then checked
    /// again after another idle period, up to `max_pin`).
    fn due(&self, k: &mut Kept, now: Instant) -> bool {
        if now.saturating_duration_since(k.used) < self.idle {
            return false;
        }
        if k.background.as_ref().is_some_and(|busy| busy())
            && now.saturating_duration_since(*k.pinned.get_or_insert(now)) < self.max_pin
        {
            k.used = now;
            return false;
        }
        true
    }

    /// Stop the sessions past their time (module doc), gone, or over the limit (oldest first); every one with `close`,
    /// which also waits for the stops `put` set off. They are stopped together, and all have gone when it returns.
    pub async fn prune(&self, close: bool) {
        self.prune_at(Instant::now(), close).await
    }

    /// `prune` as of `now`.
    pub async fn prune_at(&self, now: Instant, close: bool) {
        end_all(self.overdue(now, close)).await;
        if close {
            let stops =
                std::mem::take(&mut *self.stopping.lock().unwrap_or_else(|p| p.into_inner()));
            for stop in stops {
                let _ = stop.await;
            }
        }
    }

    /// Those `prune` stops, taken out: the gone and past their time first, then the oldest while over the limit.
    fn overdue(&self, now: Instant, close: bool) -> Vec<Kept> {
        let mut kept = self.kept();
        let mut order: Vec<(String, Instant)> =
            kept.iter().map(|(t, k)| (t.clone(), k.used)).collect();
        order.sort_by_key(|(_, used)| *used);
        let mut out = vec![];
        order.retain(|(thread, _)| {
            let k = kept.get_mut(thread).expect("listed");
            let ends = close || k.task.is_finished() || self.due(k, now);
            if ends {
                out.extend(kept.remove(thread));
            }
            !ends
        });
        for (thread, _) in order {
            if kept.len() <= self.limit {
                break;
            }
            out.extend(kept.remove(&thread));
        }
        out
    }

    /// How many sessions are kept.
    #[cfg(test)]
    pub fn count(&self) -> usize {
        self.kept().len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    /// A session that records what the keeper does to it.
    #[derive(Default)]
    struct Fake {
        token: Arc<Mutex<String>>,
        stops: Arc<AtomicUsize>,
        busy: Arc<AtomicBool>,
        dead: bool,
        /// Its token file cannot be written.
        stuck_token: bool,
        /// Parked, it never comes back (a write agy stopped reading).
        stuck: bool,
        /// Its stop never ends.
        stuck_stop: bool,
        killed: Arc<AtomicUsize>,
        dropped: Arc<AtomicBool>,
    }

    impl Drop for Fake {
        fn drop(&mut self) {
            self.dropped.store(true, Ordering::SeqCst);
        }
    }

    impl Session for Fake {
        fn alive(&self) -> bool {
            !self.dead
        }
        fn lend(&self, token: &str) -> bool {
            *self.token.lock().unwrap() = token.into();
            !self.stuck_token
        }
        fn background(&self) -> Option<Probe> {
            let busy = self.busy.clone();
            Some(Arc::new(move || busy.load(Ordering::SeqCst)))
        }
        fn park(self: Box<Self>, wanted: oneshot::Receiver<()>) -> Parked {
            Box::pin(async move {
                if self.stuck {
                    std::future::pending::<()>().await;
                }
                let _ = wanted.await;
                self as Box<dyn Session>
            })
        }
        fn stop(&self) -> Stopping<'_> {
            Box::pin(async move {
                self.stops.fetch_add(1, Ordering::SeqCst);
                if self.stuck_stop {
                    std::future::pending::<()>().await;
                }
            })
        }
        fn kill(&self) -> Kill {
            let killed = self.killed.clone();
            Arc::new(move || {
                killed.fetch_add(1, Ordering::SeqCst);
                Box::pin(async {})
            })
        }
        fn into_any(self: Box<Self>) -> Box<dyn Any + Send> {
            self
        }
    }

    /// Until `done` (a stop `put` set off in the background), at most a few seconds.
    async fn settled(done: impl Fn() -> bool) -> bool {
        for _ in 0..200 {
            if done() {
                return true;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        done()
    }

    fn keeper() -> Keeper {
        Keeper::new(std::env::temp_dir().join("tico-keeper-unused"), 4)
    }

    fn key(model: &str) -> Inputs {
        let mut k = Inputs::default();
        k.add("argv", "agy acp");
        k.add("env:MODEL", model);
        k
    }

    #[tokio::test]
    async fn a_kept_session_holds_no_token_and_comes_back_only_for_the_same_start() {
        let k = keeper();
        let f = Fake::default();
        let (token, stops) = (f.token.clone(), f.stops.clone());
        k.put("thread-1", key("a"), f, None).await;
        assert_eq!(*token.lock().unwrap(), "", "blank while kept");
        let back: Fake = k.take("thread-1", &key("a"), "turn-token-2").await.unwrap();
        assert_eq!(*token.lock().unwrap(), "turn-token-2");
        assert_eq!(stops.load(Ordering::SeqCst), 0);
        // A changed start input: the kept one is stopped and the host starts a new one; the change is named.
        assert_eq!(key("a").changed(&key("b")), vec!["env:MODEL"]);
        let mut more = key("a");
        more.add("env:EXTRA", "1");
        assert_eq!(key("a").changed(&more), vec!["env:EXTRA"]);
        k.put("thread-1", key("a"), back, None).await;
        assert!(k.take::<Fake>("thread-1", &key("b"), "t").await.is_none());
        assert_eq!((stops.load(Ordering::SeqCst), k.count()), (1, 0));
        // So is a dead one.
        let mut f = Fake::default();
        f.dead = true;
        let stops = f.stops.clone();
        k.put("thread-2", key("a"), f, None).await;
        assert!(k.take::<Fake>("thread-2", &key("a"), "t").await.is_none());
        assert_eq!(stops.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn a_session_whose_token_cannot_be_blanked_is_not_kept() {
        let k = keeper();
        let mut f = Fake::default();
        f.stuck_token = true;
        let (stops, dropped) = (f.stops.clone(), f.dropped.clone());
        k.put("thread-1", key("a"), f, None).await;
        assert_eq!(k.count(), 0);
        assert!(stops.load(Ordering::SeqCst) == 1 && dropped.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn an_unused_session_goes_after_two_minutes_and_is_never_taken_after() {
        let k = keeper();
        let f = Fake::default();
        let stops = f.stops.clone();
        let start = Instant::now();
        k.put("thread-1", key("a"), f, None).await;
        k.prune_at(start + IDLE - Duration::from_secs(1), false)
            .await;
        assert_eq!((k.count(), stops.load(Ordering::SeqCst)), (1, 0));
        k.prune_at(start + IDLE + Duration::from_secs(1), false)
            .await;
        assert_eq!((k.count(), stops.load(Ordering::SeqCst)), (0, 1));
        assert_eq!(IDLE, Duration::from_secs(120));
        // Past its time but not yet pruned: a turn does not take it.
        let k = Keeper::with_times(
            std::env::temp_dir().join("tico-keeper-unused"),
            4,
            Duration::ZERO,
            MAX_PIN,
        );
        let f = Fake::default();
        let stops = f.stops.clone();
        let (wanted, parked) = oneshot::channel();
        k.kept().insert(
            "thread-1".into(),
            Kept {
                key: key("a"),
                used: Instant::now(),
                pinned: None,
                background: None,
                wanted,
                kill: f.kill(),
                task: tokio::spawn(Box::new(f).park(parked)),
                held: None,
            },
        );
        assert!(k.take::<Fake>("thread-1", &key("a"), "t").await.is_none());
        assert_eq!(stops.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn what_a_session_holds_goes_once_it_is_released_and_a_stuck_one_is_dropped() {
        let k = keeper();
        let held = Arc::new(AtomicBool::new(false));
        struct Flag(Arc<AtomicBool>);
        impl Drop for Flag {
            fn drop(&mut self) {
                self.0.store(true, Ordering::SeqCst);
            }
        }
        k.put(
            "thread-1",
            key("a"),
            Fake::default(),
            Some(Box::new(Flag(held.clone()))),
        )
        .await;
        assert!(!held.load(Ordering::SeqCst), "kept with its session");
        k.release("thread-1").await;
        assert!(held.load(Ordering::SeqCst));
        // A session that never comes back from park is killed (its process group) and dropped after INTERRUPT_WAIT.
        let mut f = Fake::default();
        f.stuck = true;
        let (dropped, killed) = (f.dropped.clone(), f.killed.clone());
        k.put("thread-2", key("a"), f, None).await;
        k.release("thread-2").await;
        assert!(dropped.load(Ordering::SeqCst) && killed.load(Ordering::SeqCst) == 1);
        // One whose stop does not end in time is killed, and what it held goes only after.
        struct After(Arc<AtomicUsize>, Arc<AtomicBool>);
        impl Drop for After {
            fn drop(&mut self) {
                self.1
                    .store(self.0.load(Ordering::SeqCst) == 1, Ordering::SeqCst);
            }
        }
        let mut f = Fake::default();
        f.stuck_stop = true;
        let after = Arc::new(AtomicBool::new(false));
        let held = After(f.killed.clone(), after.clone());
        k.put("thread-3", key("a"), f, Some(Box::new(held))).await;
        k.release("thread-3").await;
        assert!(after.load(Ordering::SeqCst), "held until it was killed");
    }

    #[tokio::test]
    async fn background_work_pins_a_session_until_it_ends_or_four_hours_pass() {
        let k = keeper();
        let f = Fake::default();
        let (stops, busy) = (f.stops.clone(), f.busy.clone());
        let start = Instant::now();
        busy.store(true, Ordering::SeqCst);
        k.put("thread-1", key("a"), f, None).await;
        // Rechecked each idle period while the work runs.
        for n in 1..=3 {
            k.prune_at(start + (IDLE + Duration::from_secs(1)) * n, false)
                .await;
            assert_eq!(k.count(), 1, "pinned at check {n}");
        }
        busy.store(false, Ordering::SeqCst);
        k.prune_at(
            start + (IDLE + Duration::from_secs(1)) * 3 + IDLE / 2,
            false,
        )
        .await;
        assert_eq!(k.count(), 1, "not idle long enough since the last check");
        k.prune_at(start + (IDLE + Duration::from_secs(1)) * 5, false)
            .await;
        assert_eq!((k.count(), stops.load(Ordering::SeqCst)), (0, 1));
        // Work that never ends is cut off after MAX_PIN.
        let f = Fake::default();
        let (stops, busy) = (f.stops.clone(), f.busy.clone());
        busy.store(true, Ordering::SeqCst);
        let start = Instant::now();
        k.put("thread-2", key("a"), f, None).await;
        let mut at = start;
        while at < start + MAX_PIN {
            at += IDLE + Duration::from_secs(1);
            k.prune_at(at, false).await;
            if at.duration_since(start) < MAX_PIN {
                assert_eq!(k.count(), 1);
            }
        }
        at += IDLE + Duration::from_secs(1);
        k.prune_at(at, false).await;
        assert_eq!((k.count(), stops.load(Ordering::SeqCst)), (0, 1));
    }

    #[tokio::test]
    async fn the_oldest_goes_past_the_limit_and_every_one_when_the_runner_stops() {
        let k = Keeper::new(std::env::temp_dir().join("tico-keeper-unused"), 1);
        let (a, b) = (Fake::default(), Fake::default());
        let (a_stops, b_stops) = (a.stops.clone(), b.stops.clone());
        k.put("thread-a", key("a"), a, None).await;
        k.put("thread-b", key("a"), b, None).await;
        assert!(settled(|| a_stops.load(Ordering::SeqCst) == 1).await);
        assert_eq!((k.count(), b_stops.load(Ordering::SeqCst)), (1, 0));
        k.release("thread-b").await;
        assert_eq!((k.count(), b_stops.load(Ordering::SeqCst)), (0, 1));
        let c = Fake::default();
        let c_stops = c.stops.clone();
        k.put("thread-c", key("a"), c, None).await;
        k.prune(true).await;
        assert_eq!((k.count(), c_stops.load(Ordering::SeqCst)), (0, 1));
        // The runner stopping waits for a stop `put` set off.
        let mut slow = Fake::default();
        slow.stuck_stop = true;
        let killed = slow.killed.clone();
        k.put("thread-d", key("a"), slow, None).await;
        k.put("thread-e", key("a"), Fake::default(), None).await;
        k.prune(true).await;
        assert_eq!((k.count(), killed.load(Ordering::SeqCst)), (0, 1));
    }
}
