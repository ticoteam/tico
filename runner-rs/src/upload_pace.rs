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
    /// When the oldest waiting row was written: its age bounds how long the rows may wait.
    pub oldest_at: Option<Instant>,
    pub count: usize,
    /// Payload bytes of the waiting rows.
    pub bytes: u64,
    /// A row of a kind not in MAY_WAIT is waiting.
    pub urgent: bool,
    /// A delta or message is waiting: visible text.
    pub text: bool,
}

/// Each attempt's rows written by this process and not yet acknowledged: (seq, payload bytes, may wait, text, when
/// written).
/// crate::state keeps it beside the database (State::append, append_batch, ack). Rows from before a restart are
/// not here; those go up through recovery's flush, which this does not pace.
#[derive(Default)]
pub struct Outbox(Mutex<HashMap<String, VecDeque<(i64, u64, bool, bool, Instant)>>>);

impl Outbox {
    /// Rows numbered from `first` were committed, in order, as (kind, payload bytes).
    pub fn wrote<'a>(
        &self,
        aid: &str,
        first: i64,
        rows: impl IntoIterator<Item = (&'a str, usize)>,
    ) {
        let now = Instant::now();
        let mut all = self.0.lock().unwrap_or_else(|p| p.into_inner());
        let queue = all.entry(aid.to_string()).or_default();
        for (offset, (kind, bytes)) in rows.into_iter().enumerate() {
            queue.push_back((
                first + offset as i64,
                bytes as u64,
                MAY_WAIT.contains(&kind),
                kind == "delta" || kind == "message",
                now,
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
            oldest_at: queue.front().map(|r| r.4),
            count: queue.len(),
            ..Waiting::default()
        };
        for &(_, bytes, may_wait, text, _) in queue {
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
    /// Runner::flush reads on the store's reader connection and releases the attempt's lock before writing the ack,
    /// so the next upload (a control event, a turn's first text) does not wait for the store's queue of fsyncs.
    pub ack_unlocked: bool,
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
    ack_unlocked: true,
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
    ack_unlocked: false,
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

/// How long after its last upload a turn with nothing waiting still counts toward the load: a turn between bursts
/// (a tool running, the model thinking) is about to stream again, one that stopped is not.
pub const IDLE: Duration = Duration::from_secs(1);

/// One stream loop's share of the load: counted while its turn has rows waiting or uploaded within `IDLE`, so idle
/// turns (waiting on a tool or a person) do not widen the window for the turns that stream.
pub struct Active<'a> {
    load: &'a Load,
    counted: Option<Streaming<'a>>,
}

impl<'a> Active<'a> {
    pub fn new(load: &'a Load) -> Self {
        Active {
            load,
            counted: None,
        }
    }

    /// Counts the turn in or out at `now`; returns when to look again to count it out, if nothing else wakes it.
    pub fn update(
        &mut self,
        now: Instant,
        waiting: bool,
        last_post: Option<Instant>,
    ) -> Option<Instant> {
        let recent = last_post.map(|t| t + IDLE).filter(|until| *until > now);
        if waiting || recent.is_some() {
            if self.counted.is_none() {
                self.counted = Some(self.load.streaming());
            }
        } else {
            self.counted = None;
        }
        if waiting {
            None // the rows' own due time wakes the loop
        } else {
            recent
        }
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
            return None;
        }
        // From when the oldest row was written, not when the pacer first saw it: rows written during an upload are
        // seen only after it, and a person never waits more than `max_age` for text the bot already wrote.
        let since = w.oldest_at.unwrap_or(now);
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

    /// When the last successful upload ended.
    pub fn last_post(&self) -> Option<Instant> {
        self.last_post
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

    /// `count` deltas waiting, the oldest written at `written`.
    fn deltas(written: Instant, count: usize) -> Waiting {
        Waiting {
            oldest_at: Some(written),
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
    fn only_turns_with_rows_waiting_or_uploaded_within_a_second_count_toward_the_load() {
        let load = Load::default();
        let t0 = Instant::now();
        let mut idle: Vec<Active> = (0..20).map(|_| Active::new(&load)).collect();
        for a in &mut idle {
            assert_eq!(a.update(t0, false, None), None);
        }
        assert_eq!(
            load.turns(),
            0,
            "twenty turns waiting on tools do not widen the window"
        );
        let mut busy = Active::new(&load);
        assert_eq!(busy.update(t0, true, None), None);
        assert_eq!(load.turns(), 1);
        assert_eq!(load.window(&ADAPTIVE), 50 * MS);
        // Uploaded and nothing waiting: still counted for a second, then out.
        assert_eq!(
            busy.update(t0 + 10 * MS, false, Some(t0 + 10 * MS)),
            Some(t0 + 1010 * MS)
        );
        assert_eq!(load.turns(), 1);
        assert_eq!(busy.update(t0 + 1010 * MS, false, Some(t0 + 10 * MS)), None);
        assert_eq!(load.turns(), 0);
        // Streaming again: counted again, once.
        busy.update(t0 + 2000 * MS, true, Some(t0 + 10 * MS));
        busy.update(t0 + 2001 * MS, true, Some(t0 + 10 * MS));
        assert_eq!(load.turns(), 1);
        drop(busy);
        assert_eq!(load.turns(), 0);
    }

    #[test]
    fn ac3_the_first_visible_text_goes_at_once_under_any_load() {
        let t0 = Instant::now();
        for turns in [1, 10, 30, 500] {
            let w = window(&ADAPTIVE, turns, 300 * MS);
            // A status line went up a moment ago: the first text still skips the window.
            let mut p = after_upload(t0);
            assert_eq!(
                p.due(t0 + MS, &deltas(t0 + MS, 1), false, w, &ADAPTIVE),
                Some(t0 + MS)
            );
            // Once it is shown, a later delta waits for the window.
            let mut p = after_upload(t0);
            assert_eq!(
                p.due(t0 + MS, &deltas(t0 + MS, 1), true, w, &ADAPTIVE),
                Some(t0 + w)
            );
        }
        // A turn's very first upload waits for nothing, as before.
        let mut p = Pacer::default();
        assert_eq!(
            p.due(t0, &deltas(t0, 1), true, 1000 * MS, &ADAPTIVE),
            Some(t0)
        );
    }

    #[test]
    fn ac4_a_control_event_goes_at_once_and_takes_the_deltas_before_it() {
        let t0 = Instant::now();
        let w = window(&ADAPTIVE, 30, 5 * MS);
        let mut p = after_upload(t0);
        let mut waiting = deltas(t0 + 5 * MS, 25);
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
        // The window (last upload + 1 s) comes before the age bound of a delta written 700 ms after that upload.
        let mut p = after_upload(t0);
        assert_eq!(
            p.due(t0 + 700 * MS, &deltas(t0 + 700 * MS, 3), true, w, &ADAPTIVE),
            Some(t0 + w)
        );
        // Written before a later upload (by the drive loop's pass, or during a slow one) and still waiting: its age,
        // counted from when it was written, decides, not when the pacer first saw it.
        let mut p = after_upload(t0 + 500 * MS);
        assert_eq!(
            p.due(t0 + 600 * MS, &deltas(t0, 3), true, w, &ADAPTIVE),
            Some(t0 + 1000 * MS)
        );
        // Already past its age when first seen (written as a 300 ms upload began): it goes at once.
        let mut p = after_upload(t0 + 1300 * MS);
        assert_eq!(
            p.due(
                t0 + 1300 * MS,
                &deltas(t0 + 200 * MS, 3),
                true,
                w,
                &ADAPTIVE
            ),
            Some(t0 + 1300 * MS)
        );
        // Bytes: 64 KiB waiting goes at once.
        let mut p = after_upload(t0);
        let big = Waiting {
            bytes: 64 * 1024,
            ..deltas(t0, 40)
        };
        assert_eq!(p.due(t0 + MS, &big, true, w, &ADAPTIVE), Some(t0 + MS));
        // A full upload's worth of rows goes at once.
        let mut p = after_upload(t0);
        assert_eq!(
            p.due(t0 + MS, &deltas(t0 + MS, 100), true, w, &ADAPTIVE),
            Some(t0 + MS)
        );
        // Nothing waiting: nothing due.
        assert_eq!(
            p.due(t0 + MS, &Waiting::default(), true, w, &ADAPTIVE),
            None
        );
    }

    #[test]
    fn a_failed_upload_is_tried_again_a_second_later_even_for_a_control_event() {
        let t0 = Instant::now();
        let mut p = after_upload(t0);
        p.posted(t0 + 10 * MS, false);
        let urgent = Waiting {
            urgent: true,
            ..deltas(t0, 2)
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
        assert_eq!((w.count, w.bytes, w.urgent, w.text), (4, 110, true, true));
        let first_written = w.oldest_at.unwrap();
        outbox.acked("a1", 1);
        let w = outbox.waiting("a1");
        assert_eq!((w.count, w.bytes, w.urgent, w.text), (3, 100, false, true));
        assert!(w.oldest_at.unwrap() >= first_written);
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
