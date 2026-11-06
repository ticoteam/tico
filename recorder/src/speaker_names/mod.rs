//! Local meeting identity evidence. AX observations never replace the audio's voice labels:
//! names are a projection, so later diarization can use all of a voice's observed turns.
pub mod meet;
mod persistence;
mod sharing;
pub(crate) use persistence::migrate;
pub(crate) use persistence::{settle_names, timeline};
pub(crate) use sharing::correct_shared_names;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(test)]
mod tests;
#[cfg(any(target_os = "macos", test))]
mod watch_log;

use crate::store::{Recording, Word};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[cfg(any(target_os = "macos", test))]
use std::collections::BTreeSet;

/// Meet's visual speaking indication can trail the captured audio by a polling tick.
const LAG_MS: u64 = 300;
pub const POLL_MS: u64 = 250;
/// A stalled browser must not extend the last speaker across an unobserved interval.
#[cfg(any(target_os = "macos", test))]
const MAX_SAMPLE_GAP_MS: u64 = 750;
/// Meet's level indicator drops to silent between syllables; readings this close are one turn.
#[cfg(any(target_os = "macos", test))]
const HOLD_MS: u64 = 700;
/// A voice is named only after this much speaking overlap, and only by a clear majority.
const MIN_EVIDENCE_MS: u64 = 1000;
/// The name's speaking must also cover this share (in percent) of the voice's own speech while
/// indicators were readable, so a long talker is not named after someone's short replies.
const MIN_COVERAGE_PCT: u64 = 40;
/// Name-only revisions wait while this many corrections are queued for Tico, so a rename
/// spreads over passes and never holds up Finish.
pub(crate) const MAX_QUEUED_CORRECTIONS: i64 = 20;

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Interval {
    pub start_ms: u64,
    pub end_ms: u64,
    pub name: String,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Timeline {
    pub intervals: Vec<Interval>,
    pub participants: Vec<String>,
    pub self_names: Vec<String>,
    /// When speaking state was readable: silence there is observed, not unknown.
    #[serde(default)]
    pub readable: Vec<(u64, u64)>,
}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Observation {
    pub participants: Vec<String>,
    pub self_names: Vec<String>,
    /// Several people may speak at once. Ties remain unknown rather than inventing a turn.
    pub active: Vec<String>,
    /// Speaking state was readable this poll. Without it silence is unknown, not observed.
    pub indicators: bool,
    /// People-panel rows seen, and how the local row was told apart: "marker", "structure",
    /// or "none" (then no speaking state is used). For the log only.
    pub rows: usize,
    pub local_by: &'static str,
}

/// One adapter per meeting app. Browser AX transport stays outside the app's parser.
pub trait MeetingAdapter {
    fn detect_window(&self, title: &str, document_url: &str) -> bool;
    fn observe(&self, root: &meet::Node) -> Observation;
}

pub(super) fn name(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty() && value.chars().count() <= 100 && !value.chars().any(char::is_control))
        .then(|| value.to_owned())
}
fn same(a: &str, b: &str) -> bool {
    a.trim().to_lowercase() == b.trim().to_lowercase()
}
impl Timeline {
    fn remote(&self, name: &str) -> bool {
        !self.self_names.iter().any(|local| same(local, name))
    }
}
/// Prefix end times let each word visit just the overlapping observations, including
/// overlapping speakers, instead of rescanning an entire long meeting's timeline.
struct Evidence<'a> {
    intervals: Vec<&'a Interval>,
    ends: Vec<u64>,
}
impl<'a> Evidence<'a> {
    fn new(timeline: &'a Timeline) -> Self {
        let mut intervals: Vec<_> = timeline
            .intervals
            .iter()
            .filter(|i| i.end_ms > i.start_ms && timeline.remote(&i.name))
            .collect();
        intervals.sort_by_key(|i| i.start_ms);
        let mut end = 0;
        let ends = intervals
            .iter()
            .map(|i| {
                end = end.max(i.end_ms);
                end
            })
            .collect();
        Self { intervals, ends }
    }
    fn add(&self, start: u64, end: u64, scores: &mut BTreeMap<String, u64>) {
        let start = start.saturating_add(LAG_MS);
        let end = end.saturating_add(LAG_MS).max(start.saturating_add(1));
        let first = self.ends.partition_point(|at| *at <= start);
        for interval in self
            .intervals
            .iter()
            .skip(first)
            .take_while(|i| i.start_ms < end)
        {
            let overlap = end
                .min(interval.end_ms)
                .saturating_sub(start.max(interval.start_ms));
            if overlap > 0 {
                *scores.entry(interval.name.clone()).or_default() += overlap;
            }
        }
    }
}
/// Total overlap of `[start, end)` with sorted, disjoint spans.
fn covered(spans: &[(u64, u64)], start: u64, end: u64) -> u64 {
    let first = spans.partition_point(|(_, until)| *until <= start);
    spans[first..]
        .iter()
        .take_while(|(from, _)| *from < end)
        .map(|(from, until)| end.min(*until).saturating_sub(start.max(*from)))
        .sum()
}
/// Ambiguous overlap is evidence for neither person: a voice needs enough overlap, two thirds
/// of it for one name, and that name speaking through a real share of the voice's readable speech.
fn judge(scores: &BTreeMap<String, u64>, readable_speech: u64) -> Result<String, &'static str> {
    let total: u64 = scores.values().sum();
    let Some((name, weight)) = scores.iter().max_by_key(|(_, weight)| **weight) else {
        return Err("no overlap");
    };
    if *weight < MIN_EVIDENCE_MS {
        return Err("under 1 s of overlap");
    }
    if weight * 3 < total * 2 {
        return Err("no two-thirds majority");
    }
    if weight * 100 < readable_speech * MIN_COVERAGE_PCT {
        return Err("covers under 40% of the voice's readable speech");
    }
    Ok(name.clone())
}
/// One label's vote, for the log: overlap per name, its own readable speech, and the outcome.
pub(crate) struct Tally {
    pub label: String,
    pub scores: BTreeMap<String, u64>,
    pub speech: u64,
    pub outcome: Result<String, &'static str>,
}
/// A label the recorder chose itself. A speaker a person typed is never renamed.
fn provisional(label: &str) -> bool {
    label
        .strip_prefix("Speaker ")
        .is_some_and(|n| n.parse::<usize>().is_ok())
}

/// The name a label carries on this recording, or the label itself. A label is one stable
/// voice across chunks and the live stream; the store keeps its name on the recording
/// (`speaker_names`, see [`vote`]) so every view and Tico line agrees. Stored voices and labels
/// never change.
pub fn name_of<'a>(record: &'a Recording, label: &'a str) -> &'a str {
    record
        .speaker_names
        .get(label)
        .map_or(label, String::as_str)
}

/// Names that stick: a named voice changes only when another name wins outright, and never
/// goes back to "Speaker N". Evidence that later turns out to be the local person is dropped.
pub fn vote(
    record: &Recording,
    streamed: &BTreeMap<String, Vec<Word>>,
) -> BTreeMap<String, String> {
    tally(record, streamed).0
}
/// [`vote`], with each voted label's tally.
pub(crate) fn tally(
    record: &Recording,
    streamed: &BTreeMap<String, Vec<Word>>,
) -> (BTreeMap<String, String>, Vec<Tally>) {
    let timeline = &record.meeting_speakers;
    let mut out: BTreeMap<String, String> = record
        .speaker_names
        .iter()
        .filter(|(_, name)| timeline.remote(name))
        .map(|(label, name)| (label.clone(), name.clone()))
        .collect();
    if timeline.intervals.is_empty() {
        return (out, vec![]);
    }
    let evidence = Evidence::new(timeline);
    let mut readable = timeline.readable.clone();
    readable.sort();
    let labels = crate::transcript::speaker_labels(record, streamed);
    // Per label: overlap per name, and its own speech while indicators were readable.
    let mut votes: BTreeMap<&str, (BTreeMap<String, u64>, u64)> = BTreeMap::new();
    let mut add = |id: &str, start: u64, end: u64| {
        if let Some(label) = labels.get(id).filter(|label| provisional(label)) {
            let (scores, speech) = votes.entry(label).or_default();
            evidence.add(start, end, scores);
            let (start, end) = (start + LAG_MS, end.max(start + 1) + LAG_MS);
            *speech += covered(&readable, start, end);
        }
    };
    // A person's edit replaced the automatic words; it is no evidence for the voice.
    for chunk in record
        .chunks
        .iter()
        .filter(|c| c.channel == "them" && c.provider["source"] != "edit")
    {
        if chunk.words.is_empty() {
            let id = crate::transcript::speaker_identity(chunk, &Word::default());
            add(&id, chunk.start_ms, chunk.end_ms);
        }
        for word in &chunk.words {
            add(
                &crate::transcript::speaker_identity(chunk, word),
                word.start_ms,
                word.end_ms,
            );
        }
    }
    let covered_them = crate::transcript::Covered::new(record, "them");
    for word in streamed
        .get("them")
        .into_iter()
        .flatten()
        .filter(|w| !covered_them.contains(w.start_ms))
    {
        add(&word.speaker, word.start_ms, word.end_ms);
    }
    let mut tallies = vec![];
    for (label, (scores, speech)) in votes {
        let outcome = judge(&scores, speech);
        if let Ok(name) = &outcome {
            out.insert(label.to_owned(), name.clone());
        }
        tallies.push(Tally {
            label: label.to_owned(),
            scores,
            speech,
            outcome,
        });
    }
    (out, tallies)
}

/// Turn point observations into bounded intervals. A reading covers the time to the next poll;
/// silences up to [`HOLD_MS`] between readings are bridged. Nothing is bridged across a missed
/// poll or a poll whose speaking state was unreadable.
#[derive(Default)]
#[cfg(any(target_os = "macos", test))]
pub(super) struct Samples {
    previous: Option<(u64, Observation)>,
    /// Per name: the end of its last emitted interval.
    open: BTreeMap<String, u64>,
    /// The span the last push observed with readable speaking state, if any.
    pub readable: Option<(u64, u64)>,
}
#[cfg(any(target_os = "macos", test))]
impl Samples {
    pub fn push(&mut self, at: u64, observed: Observation) -> Vec<Interval> {
        let mut out = vec![];
        self.readable = None;
        if let Some((before, previous)) = self.previous.take() {
            if at <= before || at - before > MAX_SAMPLE_GAP_MS || !previous.indicators {
                self.open.clear();
            } else {
                self.readable = Some((before, at));
                for name in previous.active.iter().collect::<BTreeSet<_>>() {
                    if previous
                        .self_names
                        .iter()
                        .chain(&observed.self_names)
                        .any(|local| same(local, name))
                    {
                        self.open.remove(name);
                        continue;
                    }
                    let start = match self.open.get(name) {
                        Some(end) if before - end <= HOLD_MS => *end,
                        _ => before,
                    };
                    self.open.insert(name.clone(), at);
                    out.push(Interval {
                        start_ms: start,
                        end_ms: at,
                        name: name.clone(),
                    });
                }
            }
        }
        self.previous = Some((at, observed));
        out
    }
}

/// Evidence waiting to be written: people seen for the first time, and intervals and readable
/// spans with contiguous pieces joined.
#[derive(Default)]
#[cfg(any(target_os = "macos", test))]
pub(super) struct Batch {
    pub people: Vec<(String, bool)>,
    pub intervals: Vec<Interval>,
    pub readable: Vec<(u64, u64)>,
}
#[cfg(any(target_os = "macos", test))]
impl Batch {
    pub fn add(&mut self, intervals: Vec<Interval>, readable: Option<(u64, u64)>) {
        for interval in intervals {
            match self
                .intervals
                .iter_mut()
                .rev()
                .find(|i| i.name == interval.name)
                .filter(|i| i.end_ms == interval.start_ms)
            {
                Some(open) => open.end_ms = interval.end_ms,
                None => self.intervals.push(interval),
            }
        }
        if let Some((start, end)) = readable {
            match self.readable.last_mut() {
                Some(last) if last.1 == start => last.1 = end,
                _ => self.readable.push((start, end)),
            }
        }
    }
    pub fn is_empty(&self) -> bool {
        self.people.is_empty() && self.intervals.is_empty() && self.readable.is_empty()
    }
}

#[cfg(target_os = "macos")]
pub use macos::{Watch, access, dismiss_permission, open_settings, permission_pending};
#[cfg(not(target_os = "macos"))]
pub fn access() -> Option<bool> {
    None
}
#[cfg(not(target_os = "macos"))]
pub fn permission_pending() -> bool {
    false
}
#[cfg(not(target_os = "macos"))]
pub fn dismiss_permission(_: &std::path::Path) {}
#[cfg(not(target_os = "macos"))]
pub fn open_settings(_: &std::path::Path) {}
