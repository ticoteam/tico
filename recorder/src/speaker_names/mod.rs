//! Local meeting identity evidence. AX observations never replace the audio's voice labels:
//! names are a projection, so later diarization can use all of a voice's observed turns.
pub mod meet;
mod persistence;
mod sharing;
pub(crate) use persistence::migrate;
pub(crate) use sharing::correct_shared_names;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(test)]
mod tests;

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
}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Observation {
    pub participants: Vec<String>,
    pub self_names: Vec<String>,
    /// Several people may speak at once. Ties remain unknown rather than inventing a turn.
    pub active: Vec<String>,
    /// Speaking state was readable this poll. Without it silence is unknown, not observed.
    pub indicators: bool,
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
/// Ambiguous overlap is evidence for neither person: a voice needs enough overlap and two
/// thirds of it for one name.
fn winner(scores: BTreeMap<String, u64>) -> Option<String> {
    let total: u64 = scores.values().sum();
    let (name, weight) = scores.into_iter().max_by_key(|(_, weight)| *weight)?;
    (weight >= MIN_EVIDENCE_MS && weight * 3 >= total * 2).then_some(name)
}
/// A label the recorder chose itself. A speaker a person typed is never renamed.
fn provisional(label: &str) -> bool {
    label
        .strip_prefix("Speaker ")
        .is_some_and(|n| n.parse::<usize>().is_ok())
}

/// Names for provisional speaker labels. A label is one stable voice across chunks and the
/// live stream, so it gets at most one name, voted by all its speech; the stored voices and
/// labels never change.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Names(BTreeMap<String, String>);
impl Names {
    pub fn new(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Self {
        if record.meeting_speakers.intervals.is_empty() {
            return Self::default();
        }
        let evidence = Evidence::new(&record.meeting_speakers);
        let labels = crate::transcript::speaker_labels(record, streamed);
        let mut votes: BTreeMap<&str, BTreeMap<String, u64>> = BTreeMap::new();
        let mut add = |id: &str, start: u64, end: u64| {
            if let Some(label) = labels.get(id).filter(|label| provisional(label)) {
                evidence.add(start, end, votes.entry(label).or_default());
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
        let covered = crate::transcript::Covered::new(record, "them");
        for word in streamed
            .get("them")
            .into_iter()
            .flatten()
            .filter(|w| !covered.contains(w.start_ms))
        {
            add(&word.speaker, word.start_ms, word.end_ms);
        }
        Self(
            votes
                .into_iter()
                .filter_map(|(label, votes)| Some((label.to_owned(), winner(votes)?)))
                .collect(),
        )
    }
    /// The name for a shown label, or the label itself.
    pub fn get<'a>(&'a self, label: &'a str) -> &'a str {
        self.0.get(label).map_or(label, String::as_str)
    }
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
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
}
#[cfg(any(target_os = "macos", test))]
impl Samples {
    pub fn push(&mut self, at: u64, observed: Observation) -> Vec<Interval> {
        let mut out = vec![];
        if let Some((before, previous)) = self.previous.take() {
            if at <= before || at - before > MAX_SAMPLE_GAP_MS || !previous.indicators {
                self.open.clear();
            } else {
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

#[cfg(target_os = "macos")]
pub use macos::{Watch, dismiss_permission, open_settings, permission_pending};
#[cfg(not(target_os = "macos"))]
pub fn permission_pending() -> bool {
    false
}
#[cfg(not(target_os = "macos"))]
pub fn dismiss_permission(_: &std::path::Path) {}
#[cfg(not(target_os = "macos"))]
pub fn open_settings(_: &std::path::Path) {}
