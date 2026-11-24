//! A graceful drain owns only turns that already started. Unstarted claims return immediately; running turns finish,
//! however long they take (an update waits for them). Only a drain with a deadline, a container stop (a fixed five
//! minutes) or an update under the computer's own `drain_timeout_seconds`, stops a running turn at it: a yielded completion
//! preserves the session and queues a continuation, unless the harness cannot resume the interrupted turn: execute.rs
//! then settles it held for review.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// A container stop's own deadline for running turns: Docker kills the container 15 minutes after SIGTERM, so turns
/// still running are saved and queued to continue well before that.
pub const STOP_LIMIT_S: u64 = 5 * 60;
pub const WAITING: &str =
    "Handed back before starting because the computer is draining; queued to continue";
pub const CAPPED: &str =
    "Paused at the computer's drain time limit; work and session saved, queued to continue";
pub const HELD: &str = "Stopped at the computer's drain time limit; this turn cannot be resumed (its harness cannot resume an interrupted turn, or it ran on a fallback whose thread is not kept), so the run is held for review instead of queued to continue";

#[derive(Clone, Copy, PartialEq)]
enum Turn {
    Waiting,
    Running,
    Returning(&'static str),
}

#[derive(Default)]
struct Inner {
    since: Option<Instant>,
    capped_update: bool,
    turns: HashMap<String, Turn>,
    handed_back: usize,
    /// When a container stop began (`stop`): a drain of its own with a fixed STOP_LIMIT_S, that no update's end clears.
    /// `drain_timeout_seconds` (`limit`) is an update drain's alone.
    stopping: Option<Instant>,
    burst: Option<Burst>,
}

struct Burst {
    confirmed_at_ms: u64,
    cutoff: Instant,
    draining: bool,
}

pub struct Drain {
    inner: Mutex<Inner>,
    /// The computer's `drain_timeout_seconds`, if it set one; otherwise running turns finish.
    limit: Option<Duration>,
}

impl Drain {
    pub fn new(limit: Option<Duration>) -> Self {
        Self {
            inner: Mutex::new(Inner::default()),
            limit,
        }
    }

    pub fn register(&self, aid: &str) {
        self.inner
            .lock()
            .unwrap()
            .turns
            .entry(aid.into())
            .or_insert(Turn::Waiting);
    }

    pub fn begin(&self, now: Instant) {
        self.begin_update(now, true);
    }

    pub fn begin_update(&self, now: Instant, capped: bool) {
        let mut s = self.inner.lock().unwrap();
        if s.since.is_none() || (capped && !s.capped_update) {
            s.since = Some(now);
        }
        s.capped_update = capped;
    }

    pub fn claims_closed(&self) -> bool {
        self.inner.lock().unwrap().active(Instant::now())
    }

    pub fn burst_managed(&self) -> bool {
        self.inner.lock().unwrap().burst.is_some()
    }

    /// Anchor the remaining time to the request's send time. A lost reply leaves the last cutoff armed.
    pub fn confirm_burst(&self, sent: Instant, lease: crate::wire::BurstLease) {
        let mut s = self.inner.lock().unwrap();
        let cutoff = sent
            .checked_add(Duration::from_secs(lease.run_seconds))
            .unwrap_or(sent);
        if let Some(old) = &mut s.burst {
            if lease.confirmed_at_ms < old.confirmed_at_ms {
                return;
            }
            if lease.confirmed_at_ms == old.confirmed_at_ms {
                old.cutoff = old.cutoff.min(cutoff);
                old.draining |= lease.draining;
                return;
            }
        }
        s.burst = Some(Burst {
            confirmed_at_ms: lease.confirmed_at_ms,
            cutoff,
            draining: lease.draining,
        });
    }

    /// A container stop: no claim starts again, and running turns are stopped at STOP_LIMIT_S, inside Docker's grace.
    pub fn stop(&self, now: Instant) {
        self.inner.lock().unwrap().stopping.get_or_insert(now);
    }

    /// Serialize the start boundary with draining: once a drain begins, a waiting claim cannot start.
    pub fn start(&self, aid: &str) -> bool {
        let mut s = self.inner.lock().unwrap();
        match s.turns.get(aid) {
            Some(Turn::Running) => return true,
            // Already handed back: a later start (a fallback, or after a failed update ends the drain) stays refused.
            Some(Turn::Returning(_)) => return false,
            _ => {}
        }
        if s.active(Instant::now()) {
            s.turns.insert(aid.into(), Turn::Returning(WAITING));
            s.handed_back += 1;
            return false;
        }
        s.turns.insert(aid.into(), Turn::Running);
        true
    }

    /// A start sent before the drain keeps its place once the hub accepts it.
    pub fn acknowledged(&self, aid: &str) {
        self.inner
            .lock()
            .unwrap()
            .turns
            .insert(aid.into(), Turn::Running);
    }

    /// The hub can fence a claim before its draining lease reaches this computer.
    pub fn refused_burst_start(&self, aid: &str) {
        let mut s = self.inner.lock().unwrap();
        if let Some(lease) = &mut s.burst {
            lease.draining = true;
        }
        if s.turns.insert(aid.into(), Turn::Returning(WAITING)) != Some(Turn::Returning(WAITING)) {
            s.handed_back += 1;
        }
    }

    pub fn end(&self) {
        let mut s = self.inner.lock().unwrap();
        s.since = None;
        if s.stopping.is_none() {
            s.handed_back = 0;
        }
    }

    pub fn finish(&self, aid: &str) {
        self.inner.lock().unwrap().turns.remove(aid);
    }

    pub fn returning(&self, aid: &str) -> bool {
        matches!(
            self.inner.lock().unwrap().turns.get(aid),
            Some(Turn::Returning(_))
        )
    }

    /// Returned at the active-turn cap rather than before starting: the turn ran and may have acted.
    pub fn capped(&self, aid: &str) -> bool {
        self.inner.lock().unwrap().turns.get(aid) == Some(&Turn::Returning(CAPPED))
    }

    /// A claim being returned stays due, so every later wait (a retried git lock, the start acknowledgement) cancels
    /// too; it is counted once.
    pub(crate) fn due(&self, aid: &str, now: Instant) -> Option<&'static str> {
        let mut s = self.inner.lock().unwrap();
        let turn = *s.turns.get(aid)?;
        if let Turn::Returning(line) = turn {
            return Some(line);
        }
        if !s.active(now) {
            return None;
        }
        let past = |from: Option<Instant>, limit: Option<Duration>| {
            from.zip(limit)
                .is_some_and(|(f, l)| now.saturating_duration_since(f) >= l)
        };
        let line = match turn {
            Turn::Waiting => WAITING,
            Turn::Running
                if (s.capped_update && past(s.since, self.limit))
                    || past(s.stopping, Some(Duration::from_secs(STOP_LIMIT_S)))
                    || s.burst.as_ref().is_some_and(|lease| now >= lease.cutoff) =>
            {
                CAPPED
            }
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
            if let Some(line) = self.due(aid, Instant::now()) {
                return line;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    pub fn progress(&self) -> Option<Value> {
        let s = self.inner.lock().unwrap();
        s.active(Instant::now()).then(|| json!({"running": s.turns.values().filter(|t| **t == Turn::Running).count(), "handed_back": s.handed_back}))
    }
}

impl Inner {
    fn active(&self, now: Instant) -> bool {
        self.since.is_some()
            || self.stopping.is_some()
            || self
                .burst
                .as_ref()
                .is_some_and(|lease| lease.draining || now >= lease.cutoff)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn burst_deadline_survives_a_lost_or_stale_reply() {
        let drain = Drain::new(None);
        let sent = Instant::now();
        drain.confirm_burst(
            sent,
            crate::wire::BurstLease {
                confirmed_at_ms: 1,
                run_seconds: 240,
                draining: false,
            },
        );
        assert!(drain.start("active"));
        drain.register("waiting");
        let fence = sent + Duration::from_secs(75);
        drain.confirm_burst(
            fence,
            crate::wire::BurstLease {
                confirmed_at_ms: 2,
                run_seconds: 165,
                draining: true,
            },
        );
        drain.confirm_burst(
            sent,
            crate::wire::BurstLease {
                confirmed_at_ms: 1,
                run_seconds: 360,
                draining: false,
            },
        );
        assert_eq!(drain.due("waiting", fence), Some(WAITING));
        assert_eq!(drain.due("active", sent + Duration::from_secs(239)), None);
        assert_eq!(
            drain.due("active", sent + Duration::from_secs(240)),
            Some(CAPPED)
        );
    }

    #[test]
    fn running_turns_finish_unless_the_drain_has_a_deadline() {
        let drain = Drain::new(None);
        drain.register("running");
        assert!(drain.start("running"));
        let now = Instant::now();
        drain.begin(now);
        let hours = now + Duration::from_secs(6 * 3600);
        assert_eq!(drain.due("running", hours), None); // an update waits for it, however long
        drain.finish("running");
        drain.end();
        drain.register("next");
        assert!(drain.start("next"));
        drain.begin(now);
        assert_eq!(drain.due("next", hours), None);
    }

    #[test]
    fn a_planned_update_lets_a_running_turn_finish_with_a_configured_limit() {
        let drain = Drain::new(Some(Duration::from_secs(60)));
        drain.register("running");
        assert!(drain.start("running"));
        let now = Instant::now();
        drain.begin_update(now, false);
        assert_eq!(drain.due("running", now + Duration::from_secs(600)), None);
        assert!(!drain.start("waiting"));
        let restart = now + Duration::from_secs(601);
        drain.begin(restart);
        assert_eq!(drain.due("running", restart), None);
        assert_eq!(
            drain.due("running", restart + Duration::from_secs(60)),
            Some(CAPPED)
        );
    }

    #[test]
    fn a_container_stop_has_its_own_five_minutes_whatever_drain_timeout_says() {
        let drain = Drain::new(Some(Duration::from_secs(60))); // drain_timeout_seconds: 60
        drain.register("running");
        assert!(drain.start("running"));
        let now = Instant::now();
        drain.stop(now); // a plain SIGTERM
        assert_eq!(drain.due("running", now + Duration::from_secs(61)), None);
        assert_eq!(
            drain.due("running", now + Duration::from_secs(STOP_LIMIT_S)),
            Some(CAPPED)
        );
        let update = Drain::new(Some(Duration::from_secs(60)));
        update.register("running");
        assert!(update.start("running"));
        update.begin(now); // an update's drain takes the configured minute
        assert_eq!(
            update.due("running", now + Duration::from_secs(60)),
            Some(CAPPED)
        );
    }

    #[test]
    fn a_container_stop_keeps_its_deadline_whatever_an_update_does() {
        let drain = Drain::new(None);
        drain.register("running");
        assert!(drain.start("running"));
        let now = Instant::now();
        drain.stop(now); // SIGTERM: the stop's timer alone
        assert!(drain.progress().is_some());
        drain.begin(now); // an update's drain too
        drain.end(); // the update failed: its drain ends, the stop's does not
        drain.register("waiting");
        assert!(!drain.start("waiting"));
        let at = |s| now + Duration::from_secs(s);
        assert_eq!(drain.due("running", at(STOP_LIMIT_S - 1)), None);
        assert_eq!(drain.due("running", at(STOP_LIMIT_S)), Some(CAPPED));
        assert!(drain.progress().is_some());
    }

    #[test]
    fn start_boundary_and_deadline_survive_repeat_drain_and_update_failure() {
        let limit = 5 * 60;
        let drain = Drain::new(Some(Duration::from_secs(limit))); // the computer's drain_timeout_seconds
        drain.register("waiting");
        drain.register("running");
        assert!(drain.start("running"));
        let now = Instant::now();
        drain.begin(now);
        drain.begin(now + Duration::from_secs(299)); // another heartbeat cannot restart the clock
        assert!(!drain.start("waiting")); // the login became free at the same time as the drain
        assert_eq!(
            drain.progress(),
            Some(json!({"running": 1, "handed_back": 1}))
        );
        assert_eq!(drain.due("running", now + Duration::from_secs(299)), None);
        assert_eq!(
            drain.due("running", now + Duration::from_secs(300)),
            Some(CAPPED)
        );
        assert_eq!(
            drain.due("running", now + Duration::from_secs(301)),
            Some(CAPPED)
        ); // still due, counted once
        assert_eq!(
            drain.progress(),
            Some(json!({"running": 0, "handed_back": 2}))
        );
        drain.finish("running");
        drain.finish("waiting");
        drain.end(); // a failed update resumes work instead of returning every new claim forever
        drain.register("retry");
        assert!(drain.start("retry"));
        assert_eq!(drain.progress(), None);
    }
}
