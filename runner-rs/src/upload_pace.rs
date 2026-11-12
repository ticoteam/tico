//! When a turn's outbox goes up (crate::execute `stream_loop`). Each upload is one writer transaction on the
//! server, which has one write lock, so a runner streaming many turns at once spreads its text over fewer, larger
//! uploads, while what a person waits for still goes at once:
//!
//! - At once: a turn's first visible text (a delta or a message, the same test as `first_delta_posted`), and every
//!   event that is not a later text delta or a usage reading (status, tool, message, error, diagnostic, goal). The
//!   deltas waiting before it go in the same upload, in order: an upload always takes the outbox from its oldest row.
//! - Later deltas wait until the first of: the window since the last upload has passed, the oldest has waited
//!   `max_age`, the waiting rows reach `byte_limit` bytes, or a full upload (100 rows) is waiting.
//!
//! The window is `per_turn` for each turn streaming on this runner, or `latency_factor` times the smoothed upload
//! time when the server is slow, whichever is longer, kept within `min_window..=max_window`. One turn with fast
//! uploads keeps today's 50 ms; 20 or more turns, or uploads near 250 ms, reach the 1 s ceiling, so the runner's
//! uploads per second stay about level as turns are added. The bounds cap how far slow uploads can widen the window.
//!
//! The outbox summary is kept in memory as rows are written and acknowledged (Outbox), so deciding when to upload
//! never reads the runner's database, whose one connection is busy with each event's fsync.
//!
//! An upload takes what was recorded when it began; rows recorded meanwhile wait for the pacer, not the upload.
//!
//! Nothing here changes what is stored: `Runner::flush` still sends every row once, keyed on its seq range, and
//! acknowledges in order. A failed upload is tried again a second later, as before.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Event kinds that may wait for the window once the turn's first text is up. Every other kind goes at once.
pub const MAY_WAIT: [&str; 2] = ["delta", "tokens"];

/// The outbox rows past the server's ack, summed (Outbox::waiting).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Waiting {
    /// The oldest waiting row's seq: a new oldest row restarts the age clock.
    pub first_seq: i64,
    pub count: usize,
    /// Payload bytes of the waiting rows.
    pub bytes: u64,
    /// A row of a kind not in MAY_WAIT is waiting.
    pub urgent: bool,
    /// A delta or message is waiting: visible text.
    pub text: bool,
}

/// Each attempt's rows written by this process and not yet acknowledged: (seq, payload bytes, may wait, text).
/// crate::state keeps it beside the database (State::append, append_batch, ack). Rows from before a restart are
/// not here; those go up through recovery's flush, which this does not pace.
#[derive(Default)]
pub struct Outbox(Mutex<HashMap<String, VecDeque<(i64, u64, bool, bool)>>>);

impl Outbox {
    /// Rows numbered from `first` were committed, in order, as (kind, payload bytes).
    pub fn wrote<'a>(
        &self,
        aid: &str,
        first: i64,
        rows: impl IntoIterator<Item = (&'a str, usize)>,
    ) {
        let mut all = self.0.lock().unwrap_or_else(|p| p.into_inner());
        let queue = all.entry(aid.to_string()).or_default();
        for (offset, (kind, bytes)) in rows.into_iter().enumerate() {
            queue.push_back((
                first + offset as i64,
                bytes as u64,
                MAY_WAIT.contains(&kind),
                kind == "delta" || kind == "message",
            ));
        }
    }

    /// The server holds every row up to `seq`.
    pub fn acked(&self, aid: &str, seq: i64) {
        let mut all = self.0.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(queue) = all.get_mut(aid) {
            while queue.front().is_some_and(|r| r.0 <= seq) {
                queue.pop_front();
            }
            if queue.is_empty() {
                all.remove(aid);
            }
        }
    }

    pub fn forget(&self, aid: &str) {
        self.0.lock().unwrap_or_else(|p| p.into_inner()).remove(aid);
    }

    pub fn waiting(&self, aid: &str) -> Waiting {
        let all = self.0.lock().unwrap_or_else(|p| p.into_inner());
        let Some(queue) = all.get(aid) else {
            return Waiting::default();
        };
        let mut w = Waiting {
            first_seq: queue.front().map_or(0, |r| r.0),
            count: queue.len(),
            ..Waiting::default()
        };
        for &(_, bytes, may_wait, text) in queue {
            w.bytes += bytes;
            w.urgent |= !may_wait;
            w.text |= text;
        }
        w
    }

    /// The newest row written for `aid` and not yet acknowledged.
    pub fn last_seq(&self, aid: &str) -> Option<i64> {
        let all = self.0.lock().unwrap_or_else(|p| p.into_inner());
        all.get(aid).and_then(|q| q.back()).map(|r| r.0)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bounds {
    pub min_window: Duration,
    pub max_window: Duration,
    /// Window per turn streaming on this runner.
    pub per_turn: Duration,
    /// Window per unit of smoothed upload time (0: latency is ignored).
    pub latency_factor: u32,
    pub byte_limit: u64,
    pub max_age: Duration,
    /// Rows that fill one upload (Runner::flush's cap): waiting longer cannot make it fewer uploads.
    pub full: usize,
    /// First text and control events skip the window.
    pub urgent_at_once: bool,
    /// One flush sends only what was recorded when it began (Runner::flush); the old runner drained the outbox
    /// until empty, so under a slow server one flush became back-to-back uploads of whatever streamed meanwhile.
    pub flush_snapshot: bool,
}

pub const ADAPTIVE: Bounds = Bounds {
    min_window: Duration::from_millis(50),
    max_window: Duration::from_secs(1),
    per_turn: Duration::from_millis(50),
    latency_factor: 4,
    byte_limit: 64 * 1024,
    max_age: Duration::from_secs(1),
    full: 100,
    urgent_at_once: true,
    flush_snapshot: true,
};

/// The runner before adaptive pacing: the first event at once, then at most one upload per fixed 50 ms, whatever
/// the event or the load. Kept for tests and the bench (bench/perf-event-uploads.py) as the baseline.
#[cfg(test)]
pub const FIXED: Bounds = Bounds {
    min_window: Duration::from_millis(50),
    max_window: Duration::from_millis(50),
    per_turn: Duration::ZERO,
    latency_factor: 0,
    byte_limit: u64::MAX,
    max_age: Duration::MAX,
    full: usize::MAX,
    urgent_at_once: false,
    flush_snapshot: false,
};

/// The runner-wide load signal: turns streaming now, and the smoothed time of recent uploads.
#[derive(Default)]
pub struct Load {
    turns: AtomicUsize,
    /// Exponentially smoothed upload time in microseconds (each new reading weighs a quarter).
    latency_us: AtomicU64,
}

/// Counts one turn as streaming until dropped (an aborted stream loop drops it too).
pub struct Streaming<'a>(&'a Load);

impl Drop for Streaming<'_> {
    fn drop(&mut self) {
        self.0.turns.fetch_sub(1, Ordering::SeqCst);
    }
}

impl Load {
    pub fn streaming(&self) -> Streaming<'_> {
        self.turns.fetch_add(1, Ordering::SeqCst);
        Streaming(self)
    }

    pub fn turns(&self) -> usize {
        self.turns.load(Ordering::SeqCst)
    }

    /// One upload took `took`. A lost race between two turns' readings only drops one sample from the average.
    pub fn observe(&self, took: Duration) {
        let sample = took.as_micros().min(u64::MAX as u128) as u64;
        let old = self.latency_us.load(Ordering::Relaxed);
        let new = if old == 0 {
            sample
        } else {
            (old * 3 + sample) / 4
        };
        self.latency_us.store(new, Ordering::Relaxed);
    }

    pub fn latency(&self) -> Duration {
        Duration::from_micros(self.latency_us.load(Ordering::Relaxed))
    }

    pub fn window(&self, b: &Bounds) -> Duration {
        window(b, self.turns(), self.latency())
    }
}

pub fn window(b: &Bounds, turns: usize, latency: Duration) -> Duration {
    let by_turns = b
        .per_turn
        .saturating_mul(turns.min(u32::MAX as usize) as u32);
    let by_latency = latency.saturating_mul(b.latency_factor);
    by_turns.max(by_latency).clamp(b.min_window, b.max_window)
}

/// One turn's upload clock.
#[derive(Debug, Default)]
pub struct Pacer {
    last_post: Option<Instant>,
    /// After a failed upload: not before this.
    retry_at: Option<Instant>,
    /// The oldest waiting row (its seq) and when this pacer first saw it.
    oldest: Option<(i64, Instant)>,
}

impl Pacer {
    /// When the waiting rows should go up: `now` or later, or None when nothing waits. `shown`: the turn's first
    /// text is already up.
    pub fn due(
        &mut self,
        now: Instant,
        w: &Waiting,
        shown: bool,
        window: Duration,
        b: &Bounds,
    ) -> Option<Instant> {
        if w.count == 0 {
            self.oldest = None;
            return None;
        }
        let since = match self.oldest {
            Some((seq, at)) if seq == w.first_seq => at,
            _ => {
                self.oldest = Some((w.first_seq, now));
                now
            }
        };
        let at_once = (b.urgent_at_once && (w.urgent || (w.text && !shown)))
            || w.bytes >= b.byte_limit
            || w.count >= b.full;
        let paced = match self.last_post {
            _ if at_once => now,
            None => now, // the turn's first upload waits for nothing
            Some(last) => {
                let by_window = last + window;
                match since.checked_add(b.max_age) {
                    Some(by_age) => by_window.min(by_age),
                    None => by_window,
                }
            }
        };
        Some(match self.retry_at {
            Some(retry) => paced.max(retry).max(now),
            None => paced.max(now),
        })
    }

    /// An upload ended at `now`; a failed one leaves its rows waiting and is tried again a second later.
    pub fn posted(&mut self, now: Instant, ok: bool) {
        if ok {
            self.last_post = Some(now);
            self.retry_at = None;
        } else {
            self.retry_at = Some(now + Duration::from_secs(1));
        }
    }
}

#[cfg(test)]
mod streamed;

#[cfg(test)]
mod tests {
    use super::*;

    const MS: Duration = Duration::from_millis(1);

    fn deltas(first_seq: i64, count: usize) -> Waiting {
        Waiting {
            first_seq,
            count,
            bytes: 40 * count as u64,
            urgent: false,
            text: true,
        }
    }

    /// A pacer that has just made one upload at `t0`.
    fn after_upload(t0: Instant) -> Pacer {
        let mut p = Pacer::default();
        p.posted(t0, true);
        p
    }

    #[test]
    fn one_turn_with_fast_uploads_keeps_the_50_ms_window_and_load_widens_it_within_bounds() {
        let b = ADAPTIVE;
        assert_eq!(window(&b, 1, 5 * MS), 50 * MS);
        assert_eq!(window(&b, 0, Duration::ZERO), 50 * MS);
        assert_eq!(window(&b, 10, 5 * MS), 500 * MS);
        assert_eq!(window(&b, 30, 5 * MS), 1000 * MS);
        assert_eq!(window(&b, 500, 5 * MS), 1000 * MS);
        // A slow server widens it for one turn too, never past the ceiling.
        assert_eq!(window(&b, 1, 100 * MS), 400 * MS);
        assert_eq!(window(&b, 1, Duration::from_secs(30)), 1000 * MS);
        // The old runner's window never moves.
        assert_eq!(window(&FIXED, 30, Duration::from_secs(1)), 50 * MS);
    }

    #[test]
    fn the_load_signal_counts_streaming_turns_and_smooths_latency_and_shrinks_back() {
        let load = Load::default();
        let (a, b2) = (load.streaming(), load.streaming());
        assert_eq!(load.turns(), 2);
        drop(a);
        assert_eq!(load.turns(), 1);
        drop(b2);
        assert_eq!(load.turns(), 0);
        load.observe(400 * MS);
        assert_eq!(load.window(&ADAPTIVE), 1000 * MS);
        // One fast reading moves it a quarter of the way; a run of fast ones brings it back to the floor.
        load.observe(Duration::ZERO);
        assert_eq!(load.latency(), 300 * MS);
        for _ in 0..20 {
            load.observe(4 * MS);
        }
        assert_eq!(load.window(&ADAPTIVE), 50 * MS);
    }

    #[test]
    fn ac3_the_first_visible_text_goes_at_once_under_any_load() {
        let t0 = Instant::now();
        for turns in [1, 10, 30, 500] {
            let w = window(&ADAPTIVE, turns, 300 * MS);
            // A status line went up a moment ago: the first text still skips the window.
            let mut p = after_upload(t0);
            assert_eq!(
                p.due(t0 + MS, &deltas(2, 1), false, w, &ADAPTIVE),
                Some(t0 + MS)
            );
            // Once it is shown, a later delta waits for the window.
            let mut p = after_upload(t0);
            assert_eq!(
                p.due(t0 + MS, &deltas(3, 1), true, w, &ADAPTIVE),
                Some(t0 + w)
            );
        }
        // A turn's very first upload waits for nothing, as before.
        let mut p = Pacer::default();
        assert_eq!(
            p.due(t0, &deltas(1, 1), true, 1000 * MS, &ADAPTIVE),
            Some(t0)
        );
    }

    #[test]
    fn ac4_a_control_event_goes_at_once_and_takes_the_deltas_before_it() {
        let t0 = Instant::now();
        let w = window(&ADAPTIVE, 30, 5 * MS);
        let mut p = after_upload(t0);
        let mut waiting = deltas(10, 25);
        assert_eq!(
            p.due(t0 + 10 * MS, &waiting, true, w, &ADAPTIVE),
            Some(t0 + w)
        );
        // A tool, status or final message joins the outbox behind the deltas: the whole outbox goes now, from its
        // oldest row (Runner::flush sends in seq order), so the deltas go first in the same upload.
        waiting.count += 1;
        waiting.urgent = true;
        assert_eq!(
            p.due(t0 + 20 * MS, &waiting, true, w, &ADAPTIVE),
            Some(t0 + 20 * MS)
        );
        // The old runner held it for the 50 ms window.
        let mut old = after_upload(t0);
        assert_eq!(
            old.due(t0 + 20 * MS, &waiting, true, 50 * MS, &FIXED),
            Some(t0 + 50 * MS)
        );
    }

    #[test]
    fn ac5_waiting_deltas_go_at_the_maximum_age_or_the_byte_limit_or_a_full_upload() {
        let t0 = Instant::now();
        let w = 1000 * MS;
        // Age: the oldest was first seen 700 ms after the last upload; it may wait only until a second after that...
        // ...but the window (last + 1 s) comes first here. With a later upload the age bound decides.
        let mut p = after_upload(t0);
        assert_eq!(
            p.due(t0 + 700 * MS, &deltas(5, 3), true, w, &ADAPTIVE),
            Some(t0 + w)
        );
        let mut p = Pacer {
            oldest: Some((5, t0)),
            ..Default::default()
        };
        p.posted(t0 + 500 * MS, true); // an upload by the drive loop's pass, the oldest still waiting after it
        assert_eq!(
            p.due(t0 + 600 * MS, &deltas(5, 3), true, w, &ADAPTIVE),
            Some(t0 + 1000 * MS)
        );
        // A new oldest row restarts the age clock.
        assert_eq!(
            p.due(t0 + 700 * MS, &deltas(9, 1), true, w, &ADAPTIVE),
            Some(t0 + 1500 * MS)
        );
        // Bytes: 64 KiB waiting goes at once.
        let mut p = after_upload(t0);
        let big = Waiting {
            bytes: 64 * 1024,
            ..deltas(5, 40)
        };
        assert_eq!(p.due(t0 + MS, &big, true, w, &ADAPTIVE), Some(t0 + MS));
        // A full upload's worth of rows goes at once.
        let mut p = after_upload(t0);
        assert_eq!(
            p.due(t0 + MS, &deltas(5, 100), true, w, &ADAPTIVE),
            Some(t0 + MS)
        );
        // Nothing waiting: nothing due, and the age clock is cleared.
        assert_eq!(
            p.due(t0 + MS, &Waiting::default(), true, w, &ADAPTIVE),
            None
        );
        assert!(p.oldest.is_none());
    }

    #[test]
    fn a_failed_upload_is_tried_again_a_second_later_even_for_a_control_event() {
        let t0 = Instant::now();
        let mut p = after_upload(t0);
        p.posted(t0 + 10 * MS, false);
        let urgent = Waiting {
            urgent: true,
            ..deltas(4, 2)
        };
        assert_eq!(
            p.due(t0 + 20 * MS, &urgent, true, 50 * MS, &ADAPTIVE),
            Some(t0 + 1010 * MS)
        );
        p.posted(t0 + 1010 * MS, true);
        assert_eq!(
            p.due(t0 + 1020 * MS, &urgent, true, 50 * MS, &ADAPTIVE),
            Some(t0 + 1020 * MS)
        );
    }

    #[test]
    fn the_outbox_sums_unacknowledged_rows_and_drops_them_as_acks_arrive() {
        let outbox = Outbox::default();
        assert_eq!(outbox.waiting("a1"), Waiting::default());
        outbox.wrote("a1", 1, [("status", 10)]);
        outbox.wrote("a1", 2, [("delta", 40), ("delta", 40), ("tokens", 20)]);
        let w = outbox.waiting("a1");
        assert_eq!(
            (w.first_seq, w.count, w.bytes, w.urgent, w.text),
            (1, 4, 110, true, true)
        );
        outbox.acked("a1", 1);
        let w = outbox.waiting("a1");
        assert_eq!(
            (w.first_seq, w.count, w.bytes, w.urgent, w.text),
            (2, 3, 100, false, true)
        );
        assert_eq!(outbox.last_seq("a1"), Some(4));
        outbox.acked("a1", 4);
        assert_eq!(outbox.waiting("a1"), Waiting::default());
        assert_eq!(outbox.last_seq("a1"), None);
        outbox.wrote("a1", 5, [("tool", 30)]);
        outbox.forget("a1");
        assert_eq!(outbox.waiting("a1").count, 0);
    }

    #[test]
    fn only_later_text_and_usage_may_wait() {
        for kind in ["status", "tool", "message", "error", "diagnostic", "goal"] {
            assert!(!MAY_WAIT.contains(&kind), "{kind}");
        }
    }
}
