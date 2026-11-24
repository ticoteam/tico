//! The session keeper (T3 Code's orchestration-v2 ProviderSessionManager): a harness session stays running after its
//! thread's turn ends, so the thread's next message goes into the same process and conversation. The hub still
//! claims, starts and completes every message; only the process is reused.
//!
//! A harness plugs in as a `Session` (hosts/antigravity.rs `Agent` now; Claude and Codex next). The runner keeps one
//! `Keeper` (Runner::keeper) for all of them, one session per thread, under the start inputs it was started with
//! (`key`: the harness's own fingerprint of its command line, home or plan, environment, folder and MCP servers,
//! `crate::warm::PER_ATTEMPT` aside). A host `take`s its thread's session for a turn, which comes back only when the
//! turn would start it the same way and it still runs, and `put`s it back when the turn ends well. Between turns it
//! holds no token (`Session::lend`, blank) and no turn reads what it says (`Session::park`).
//!
//! A kept session is released (stopped, and whatever it held removed with it) after `IDLE` unused, unless it reports
//! background work still running (`Session::background`): then it is pinned and rechecked each `IDLE`, up to
//! `MAX_PIN` in all. It also goes when more than `limit` are kept (the oldest first), when its output ends, when its
//! thread's next turn starts it differently, and when the runner stops. A released conversation comes back by the
//! harness's native resume on the thread's next message.

use std::any::Any;
use std::collections::HashMap;
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
/// How long a stopped turn's harness gets to end it before it is stopped itself (T3's PROVIDER_INTERRUPT_TIMEOUT_MS).
pub const INTERRUPT_WAIT: Duration = Duration::from_secs(15);

/// Whether a kept session's background work still runs (`Session::background`).
pub type Probe = Arc<dyn Fn() -> bool + Send + Sync>;
/// A session while it is kept: it ends, giving the session back, once wanted or once its output has ended.
pub type Parked = Pin<Box<dyn Future<Output = Box<dyn Session>> + Send>>;
/// A session being stopped.
pub type Stopping<'a> = Pin<Box<dyn Future<Output = ()> + Send + 'a>>;

/// A harness's running session, as the keeper holds it between turns.
pub trait Session: Any + Send {
    /// Whether it can take another turn: its process runs and its output is open.
    fn alive(&self) -> bool;
    /// The hub token for the turn it runs, or "" while kept (crate::warm::write_token_for on its token file).
    fn lend(&self, token: &str);
    /// The scope unit its process was launched in where turns are limited (crate::turn_limits::launch_unit): an OOM of
    /// the process is a failure of whichever attempt runs it then (Host::kept_scopes).
    fn unit(&self) -> Option<String>;
    /// Its background work (T3's hasPendingBackgroundWork), if it can have any: while that runs, it stays past `IDLE`.
    fn background(&self) -> Option<Probe> {
        None
    }
    /// Held between turns: no turn's events, what it asks answered or refused, until `wanted` fires or its output
    /// ends; then it is given back.
    fn park(self: Box<Self>, wanted: oneshot::Receiver<()>) -> Parked;
    /// Stop it: its input closed, it and its process group reaped. What it held goes when it is dropped.
    fn stop(&self) -> Stopping<'_>;
    /// Itself, for `take` to give the host its own type back.
    fn into_any(self: Box<Self>) -> Box<dyn Any + Send>;
}

/// One kept session.
struct Kept {
    key: String,
    used: Instant,
    /// Since when background work has kept it past `IDLE`.
    pinned: Option<Instant>,
    background: Option<Probe>,
    wanted: oneshot::Sender<()>,
    task: tokio::task::JoinHandle<Box<dyn Session>>,
}

impl Kept {
    async fn back(self) -> Option<Box<dyn Session>> {
        let _ = self.wanted.send(());
        self.task.await.ok()
    }

    async fn end(self) {
        if let Some(session) = self.back().await {
            session.stop().await;
        }
    }
}

/// The sessions kept between their threads' turns (module doc).
pub struct Keeper {
    /// Where each kept session's token folder goes (`folder`).
    dir: PathBuf,
    limit: usize,
    idle: Duration,
    max_pin: Duration,
    kept: Mutex<HashMap<String, Kept>>,
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
        }
    }

    fn kept(&self) -> std::sync::MutexGuard<'_, HashMap<String, Kept>> {
        self.kept.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// A new token folder for a session that may be kept (crate::warm::folder).
    pub fn folder(&self) -> Result<PathBuf, String> {
        crate::warm::folder(&self.dir)
    }

    /// The session kept for `thread`, lent `token`, when it was started with `key` and still runs; one started
    /// differently, or gone, is stopped and None comes back.
    pub async fn take<S: Session>(&self, thread: &str, key: &str, token: &str) -> Option<S> {
        let kept = self.kept().remove(thread)?;
        let same = kept.key == key;
        let session = kept.back().await?;
        if !same || !session.alive() {
            session.stop().await;
            return None;
        }
        // A thread's key names its harness's own command line, so another harness's session never has it.
        let session = session.into_any().downcast::<S>().ok()?;
        session.lend(token);
        Some(*session)
    }

    /// Keep `session`, started with `key`, for `thread`'s next turn: its token blanked, parked until then. One already
    /// kept for the thread is stopped.
    pub async fn put<S: Session>(&self, thread: &str, key: String, session: S) {
        session.lend("");
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
            },
        );
        if let Some(old) = old {
            old.end().await;
        }
        self.prune(false).await;
    }

    /// Stop the session kept for `thread`, if any.
    pub async fn release(&self, thread: &str) {
        let kept = self.kept().remove(thread);
        if let Some(kept) = kept {
            kept.end().await;
        }
    }

    /// Stop the sessions past their time (module doc), gone, or over the limit (oldest first); every one with `close`.
    pub async fn prune(&self, close: bool) {
        self.prune_at(Instant::now(), close).await
    }

    /// `prune` as of `now`.
    pub async fn prune_at(&self, now: Instant, close: bool) {
        let stopping: Vec<Kept> = {
            let mut kept = self.kept();
            let mut order: Vec<(String, Instant)> =
                kept.iter().map(|(t, k)| (t.clone(), k.used)).collect();
            order.sort_by_key(|(_, used)| *used);
            let mut out = vec![];
            for (thread, _) in order {
                let over = kept.len() > self.limit;
                let k = kept.get_mut(&thread).expect("listed");
                let release = if close || over || k.task.is_finished() {
                    true
                } else if now.saturating_duration_since(k.used) < self.idle {
                    false
                } else if k.background.as_ref().is_some_and(|busy| busy())
                    && now.saturating_duration_since(*k.pinned.get_or_insert(now)) < self.max_pin
                {
                    // Pinned by its background work: checked again after another idle period.
                    k.used = now;
                    false
                } else {
                    true
                };
                if release {
                    out.extend(kept.remove(&thread));
                }
            }
            out
        };
        for k in stopping {
            k.end().await;
        }
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
    struct Fake {
        token: Arc<Mutex<String>>,
        stops: Arc<AtomicUsize>,
        busy: Arc<AtomicBool>,
        dead: bool,
    }

    impl Session for Fake {
        fn alive(&self) -> bool {
            !self.dead
        }
        fn lend(&self, token: &str) {
            *self.token.lock().unwrap() = token.into();
        }
        fn unit(&self) -> Option<String> {
            None
        }
        fn background(&self) -> Option<Probe> {
            let busy = self.busy.clone();
            Some(Arc::new(move || busy.load(Ordering::SeqCst)))
        }
        fn park(self: Box<Self>, wanted: oneshot::Receiver<()>) -> Parked {
            Box::pin(async move {
                let _ = wanted.await;
                self as Box<dyn Session>
            })
        }
        fn stop(&self) -> Stopping<'_> {
            Box::pin(async move {
                self.stops.fetch_add(1, Ordering::SeqCst);
            })
        }
        fn into_any(self: Box<Self>) -> Box<dyn Any + Send> {
            self
        }
    }

    fn fake() -> (Fake, Arc<Mutex<String>>, Arc<AtomicUsize>, Arc<AtomicBool>) {
        let f = Fake {
            token: Arc::new(Mutex::new("turn-token-1".into())),
            stops: Arc::default(),
            busy: Arc::default(),
            dead: false,
        };
        let (t, s, b) = (f.token.clone(), f.stops.clone(), f.busy.clone());
        (f, t, s, b)
    }

    fn keeper() -> Keeper {
        Keeper::new(std::env::temp_dir().join("tico-keeper-unused"), 4)
    }

    #[tokio::test]
    async fn a_kept_session_holds_no_token_and_comes_back_only_for_the_same_start() {
        let k = keeper();
        let (f, token, stops, _) = fake();
        k.put("thread-1", "key-a".into(), f).await;
        assert_eq!(*token.lock().unwrap(), "", "blank while kept");
        let back: Fake = k.take("thread-1", "key-a", "turn-token-2").await.unwrap();
        assert_eq!(*token.lock().unwrap(), "turn-token-2");
        assert_eq!(stops.load(Ordering::SeqCst), 0);
        // A changed start input: the kept one is stopped and the host starts a new one.
        k.put("thread-1", "key-a".into(), back).await;
        assert!(k.take::<Fake>("thread-1", "key-b", "t").await.is_none());
        assert_eq!((stops.load(Ordering::SeqCst), k.count()), (1, 0));
        // So is a dead one.
        let (mut f, _, stops, _) = fake();
        f.dead = true;
        k.put("thread-2", "key-a".into(), f).await;
        assert!(k.take::<Fake>("thread-2", "key-a", "t").await.is_none());
        assert_eq!(stops.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn an_unused_session_goes_after_two_minutes() {
        let k = keeper();
        let (f, _, stops, _) = fake();
        let start = Instant::now();
        k.put("thread-1", "key".into(), f).await;
        k.prune_at(start + IDLE - Duration::from_secs(1), false)
            .await;
        assert_eq!((k.count(), stops.load(Ordering::SeqCst)), (1, 0));
        k.prune_at(start + IDLE + Duration::from_secs(1), false)
            .await;
        assert_eq!((k.count(), stops.load(Ordering::SeqCst)), (0, 1));
        assert_eq!(IDLE, Duration::from_secs(120));
    }

    #[tokio::test]
    async fn background_work_pins_a_session_until_it_ends_or_four_hours_pass() {
        let k = keeper();
        let (f, _, stops, busy) = fake();
        let start = Instant::now();
        busy.store(true, Ordering::SeqCst);
        k.put("thread-1", "key".into(), f).await;
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
        let (f, _, stops, busy) = fake();
        busy.store(true, Ordering::SeqCst);
        let start = Instant::now();
        k.put("thread-2", "key".into(), f).await;
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
        let (a, _, a_stops, _) = fake();
        let (b, _, b_stops, _) = fake();
        k.put("thread-a", "key".into(), a).await;
        k.put("thread-b", "key".into(), b).await;
        assert_eq!(
            (
                a_stops.load(Ordering::SeqCst),
                b_stops.load(Ordering::SeqCst)
            ),
            (1, 0)
        );
        k.release("thread-b").await;
        assert_eq!((k.count(), b_stops.load(Ordering::SeqCst)), (0, 1));
        let (c, _, c_stops, _) = fake();
        k.put("thread-c", "key".into(), c).await;
        k.prune(true).await;
        assert_eq!((k.count(), c_stops.load(Ordering::SeqCst)), (0, 1));
    }
}
