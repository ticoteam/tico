//! Log lines for one recording's Meet watch, so a live call can be debugged from the log
//! alone: change-driven, rate-limited, and cheap per poll. Names only, never transcript text.
use super::{Interval, Observation};
use crate::diag::Limit;
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};

/// A turn this long after its last reading is over (Samples bridges up to 0.7 s).
const TURN_GAP_MS: u64 = 1000;
const STATS_EVERY: Duration = Duration::from_secs(60);

#[derive(Default)]
pub(super) struct PollInfo {
    pub found: bool,
    pub browser: String,
    /// Meet-titled windows walked.
    pub windows: usize,
    pub nodes: usize,
    /// The walk ran out of time or nodes, so the poll was discarded.
    pub truncated: bool,
    pub ms: u64,
}

pub(super) struct WatchLog {
    rec: String,
    found: bool,
    rows: Option<(usize, &'static str, Vec<String>)>,
    rows_limit: Limit,
    turns: BTreeMap<String, (u64, u64)>,
    turn_limit: Limit,
    ms: Vec<u64>,
    nodes: Vec<usize>,
    truncated: usize,
    browser: String,
    since: Instant,
}
fn pct<T: Copy + Ord + Default>(values: &[T], p: usize) -> T {
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    sorted
        .get(sorted.len().saturating_sub(1) * p / 100)
        .copied()
        .unwrap_or_default()
}
fn secs(ms: u64) -> f64 {
    ms as f64 / 1000.0
}
impl WatchLog {
    pub fn new(id: &str) -> Self {
        Self {
            rec: id.chars().take(8).collect(),
            found: false,
            rows: None,
            rows_limit: Limit::new(20),
            turns: BTreeMap::new(),
            turn_limit: Limit::new(60),
            ms: vec![],
            nodes: vec![],
            truncated: 0,
            browser: String::new(),
            since: Instant::now(),
        }
    }
    pub fn rec(&self) -> &str {
        &self.rec
    }
    pub fn poll(
        &mut self,
        info: &PollInfo,
        observed: &Observation,
        intervals: &[Interval],
        at: u64,
    ) -> Vec<String> {
        let rec = &self.rec;
        let mut out = vec![];
        if info.found != self.found {
            self.found = info.found;
            out.push(if info.found {
                format!(
                    "meet: rec={rec} document found in {} ({} Meet window(s) walked, {} nodes, {} ms)",
                    info.browser, info.windows, info.nodes, info.ms
                )
            } else {
                self.rows = None;
                format!("meet: rec={rec} document lost")
            });
        }
        if info.found {
            if !info.browser.is_empty() {
                self.browser.clone_from(&info.browser);
            }
            self.ms.push(info.ms);
            self.nodes.push(info.nodes);
            self.truncated += usize::from(info.truncated);
            if !info.truncated {
                let rows = (
                    observed.rows,
                    observed.local_by,
                    observed.self_names.clone(),
                );
                if self.rows.as_ref() != Some(&rows) {
                    let line = match observed.local_by {
                        "" => "no People-panel rows (panel closed?); names from tiles only, no speaking state".to_owned(),
                        "none" => format!(
                            "{} rows; local row unknown, speaking state not used",
                            observed.rows
                        ),
                        how => format!(
                            "{} rows; local {} by {how}",
                            observed.rows,
                            observed.self_names.join(", ")
                        ),
                    };
                    self.rows = Some(rows);
                    if let Some(dropped) = self.rows_limit.allow() {
                        out.push(format!("meet: rec={rec} {line}{}", dropped_note(dropped)));
                    }
                }
            }
        }
        for interval in intervals {
            match self.turns.get_mut(&interval.name) {
                Some(turn) if turn.1 == interval.start_ms => turn.1 = interval.end_ms,
                _ => {
                    if let Some(old) = self
                        .turns
                        .insert(interval.name.clone(), (interval.start_ms, interval.end_ms))
                    {
                        out.extend(self.turn(&interval.name, old));
                    }
                }
            }
        }
        let over: Vec<_> = self
            .turns
            .iter()
            .filter(|(_, (_, end))| at.saturating_sub(*end) > TURN_GAP_MS)
            .map(|(name, turn)| (name.clone(), *turn))
            .collect();
        for (name, turn) in over {
            self.turns.remove(&name);
            out.extend(self.turn(&name, turn));
        }
        if self.since.elapsed() >= STATS_EVERY {
            out.extend(self.stats());
        }
        out
    }
    fn turn(&mut self, name: &str, (start, end): (u64, u64)) -> Option<String> {
        let dropped = self.turn_limit.allow()?;
        Some(format!(
            "meet: rec={} {name} spoke {:.2}-{:.2} s ({:.1} s){}",
            self.rec,
            secs(start),
            secs(end),
            secs(end - start),
            dropped_note(dropped)
        ))
    }
    fn stats(&mut self) -> Option<String> {
        self.since = Instant::now();
        if self.ms.is_empty() {
            return None;
        }
        let line = format!(
            "meet: rec={} {} polls in {}, {} ms p50, {} ms p95, {} nodes p95, {} cut short",
            self.rec,
            self.ms.len(),
            self.browser,
            pct(&self.ms, 50),
            pct(&self.ms, 95),
            pct(&self.nodes, 95),
            self.truncated
        );
        self.ms.clear();
        self.nodes.clear();
        self.truncated = 0;
        Some(line)
    }
    pub fn end(&mut self) -> Vec<String> {
        let mut out = vec![];
        for (name, turn) in std::mem::take(&mut self.turns) {
            out.extend(self.turn(&name, turn));
        }
        out.extend(self.stats());
        out.push(format!("meet: rec={} watch ended", self.rec));
        out
    }
}
fn dropped_note(dropped: u32) -> String {
    if dropped > 0 {
        format!(" ({dropped} lines dropped)")
    } else {
        String::new()
    }
}
