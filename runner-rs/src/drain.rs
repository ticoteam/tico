//! A graceful drain owns only turns that already started. Unstarted claims return immediately; at the
//! deadline the same yielded completion used for login handoffs preserves the session and queues a continuation,
//! unless the harness cannot resume the interrupted turn: execute.rs then settles it held for review.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

pub const DEFAULT_LIMIT_S: u64 = 5 * 60;
pub const WAITING: &str = "Handed back before starting because the computer is updating or restarting; queued to continue";
pub const CAPPED: &str = "Paused at the computer's drain time limit; work and session saved, queued to continue";
pub const HELD: &str = "Stopped at the computer's drain time limit; this turn cannot be resumed (its harness cannot resume an interrupted turn, or it ran on a fallback whose thread is not kept), so the run is held for review instead of queued to continue";

#[derive(Clone, Copy, PartialEq)]
enum Turn { Waiting, Running, Returning(&'static str) }

#[derive(Default)]
struct Inner {
    since: Option<Instant>,
    turns: HashMap<String, Turn>,
    handed_back: usize,
}

pub struct Drain {
    inner: Mutex<Inner>,
    limit: Duration,
}

impl Drain {
    pub fn new(limit: Duration) -> Self { Self { inner: Mutex::new(Inner::default()), limit } }

    pub fn register(&self, aid: &str) {
        self.inner.lock().unwrap().turns.entry(aid.into()).or_insert(Turn::Waiting);
    }

    pub fn begin(&self, now: Instant) { self.inner.lock().unwrap().since.get_or_insert(now); }

    /// Serialize the start boundary with draining: once a drain begins, a waiting claim cannot start.
    pub fn start(&self, aid: &str) -> bool {
        let mut s = self.inner.lock().unwrap();
        match s.turns.get(aid) {
            Some(Turn::Running) => return true,
            // Already handed back: a later start (a fallback, or after a failed update ends the drain) stays refused.
            Some(Turn::Returning(_)) => return false,
            _ => {}
        }
        if s.since.is_some() {
            s.turns.insert(aid.into(), Turn::Returning(WAITING));
            s.handed_back += 1;
            return false;
        }
        s.turns.insert(aid.into(), Turn::Running);
        true
    }

    pub fn end(&self) {
        let mut s = self.inner.lock().unwrap();
        s.since = None;
        s.handed_back = 0;
    }

    pub fn finish(&self, aid: &str) { self.inner.lock().unwrap().turns.remove(aid); }

    pub fn returning(&self, aid: &str) -> bool { matches!(self.inner.lock().unwrap().turns.get(aid), Some(Turn::Returning(_))) }

    /// Returned at the active-turn cap rather than before starting: the turn ran and may have acted.
    pub fn capped(&self, aid: &str) -> bool { self.inner.lock().unwrap().turns.get(aid) == Some(&Turn::Returning(CAPPED)) }

    /// A claim being returned stays due, so every later wait (a retried git lock, the start acknowledgement) cancels
    /// too; it is counted once.
    pub(crate) fn due(&self, aid: &str, now: Instant) -> Option<&'static str> {
        let mut s = self.inner.lock().unwrap();
        let turn = *s.turns.get(aid)?;
        if let Turn::Returning(line) = turn { return Some(line); }
        let since = s.since?;
        let line = match turn {
            Turn::Waiting => WAITING,
            Turn::Running if now.saturating_duration_since(since) >= self.limit => CAPPED,
            _ => return None,
        };
        s.turns.insert(aid.into(), Turn::Returning(line));
        s.handed_back += 1;
        Some(line)
    }

    /// Cancel preparation even when it is parked inside a lane, login or usage wait. The caller still stops its
    /// host, saves the result in the durable outbox and flushes it before releasing the claim.
    pub async fn wait(&self, aid: &str) -> &'static str {
        loop {
            if let Some(line) = self.due(aid, Instant::now()) { return line; }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    pub fn progress(&self) -> Option<Value> {
        let s = self.inner.lock().unwrap();
        s.since.map(|_| json!({"running": s.turns.values().filter(|t| **t == Turn::Running).count(), "handed_back": s.handed_back}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn start_boundary_and_deadline_survive_repeat_drain_and_update_failure() {
        let drain = Drain::new(Duration::from_secs(DEFAULT_LIMIT_S));
        drain.register("waiting");
        drain.register("running");
        assert!(drain.start("running"));
        let now = Instant::now();
        drain.begin(now);
        drain.begin(now + Duration::from_secs(299)); // another heartbeat cannot restart the clock
        assert!(!drain.start("waiting")); // the login became free at the same time as the drain
        assert_eq!(drain.progress(), Some(json!({"running": 1, "handed_back": 1})));
        assert_eq!(drain.due("running", now + Duration::from_secs(299)), None);
        assert_eq!(drain.due("running", now + Duration::from_secs(300)), Some(CAPPED));
        assert_eq!(drain.due("running", now + Duration::from_secs(301)), Some(CAPPED)); // still due, counted once
        assert_eq!(drain.progress(), Some(json!({"running": 0, "handed_back": 2})));
        drain.finish("running");
        drain.finish("waiting");
        drain.end(); // a failed update resumes work instead of returning every new claim forever
        drain.register("retry");
        assert!(drain.start("retry"));
        assert_eq!(drain.progress(), None);
    }
}
