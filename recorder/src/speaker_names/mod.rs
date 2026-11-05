//! Local meeting identity evidence. AX observations never replace the audio's voice labels:
//! names are a projection, so later diarization can use all of a voice's observed turns.
pub mod meet;
mod persistence;
pub(crate) use persistence::migrate;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(test)]
mod tests;

use crate::store::{Recording, Word};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Meet's visual speaking indication can trail the captured audio by a polling tick.
const LAG_MS: u64 = 300;
pub const POLL_MS: u64 = 250;
/// A stalled browser must not extend the last speaker across an unobserved interval.
const MAX_SAMPLE_GAP_MS: u64 = 750;

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
}

/// One adapter per meeting app. Browser AX transport stays outside the app's parser.
pub trait MeetingAdapter {
    fn detect_window(&self, title: &str, document_url: &str) -> bool;
    fn read_participants(&self, root: &meet::Node) -> (Vec<String>, Vec<String>);
    fn read_active_speakers(&self, root: &meet::Node) -> Vec<String>;
    fn observe(&self, root: &meet::Node) -> Observation {
        let (participants, self_names) = self.read_participants(root);
        Observation {
            participants,
            self_names,
            active: self.read_active_speakers(root),
        }
    }
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
    pub fn at(&self, start: u64, end: u64) -> Option<String> {
        Evidence::new(self).at(start, end)
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
    fn scores(&self, start: u64, end: u64) -> BTreeMap<&'a str, u64> {
        let start = start.saturating_add(LAG_MS);
        let end = end.saturating_add(LAG_MS).max(start.saturating_add(1));
        let first = self.ends.partition_point(|at| *at <= start);
        let mut scores = BTreeMap::new();
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
                *scores.entry(interval.name.as_str()).or_default() += overlap;
            }
        }
        scores
    }
    fn at(&self, start: u64, end: u64) -> Option<String> {
        winner(self.scores(start, end)).map(str::to_owned)
    }
}
/// Ambiguous overlap is evidence for neither person. A clear majority can name a cluster,
/// including its short turns and words that fall in gaps in the AX observations.
fn winner(scores: BTreeMap<&str, u64>) -> Option<&str> {
    let mut scores: Vec<_> = scores.into_iter().collect();
    scores.sort_by_key(|(_, weight)| std::cmp::Reverse(*weight));
    let (name, weight) = *scores.first()?;
    let runner_up = scores.get(1).map_or(0, |(_, n)| *n);
    (weight > runner_up + runner_up / 4).then_some(name)
}

/// Provider turn labels are local to a chunk; diarized voice labels span the recording.
fn cluster(chunk: &str, word: &Word) -> Option<String> {
    if !word.speaker.is_empty() && chunk.starts_with("stream:") {
        Some(format!("turn:{chunk}:{}", word.speaker))
    } else if !word.speaker.is_empty() {
        Some(format!("voice:{}", word.speaker))
    } else if !word.turn.is_empty() {
        Some(format!("turn:{chunk}:{}", word.turn))
    } else {
        None
    }
}
pub struct Names<'a> {
    evidence: Evidence<'a>,
    voices: BTreeMap<String, String>,
}
impl<'a> Names<'a> {
    pub fn new(record: &'a Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Self {
        let evidence = Evidence::new(&record.meeting_speakers);
        let mut votes: BTreeMap<String, BTreeMap<&str, u64>> = BTreeMap::new();
        let mut add = |chunk: &str, word: &Word| {
            if let Some(key) = cluster(chunk, word) {
                for (name, weight) in evidence.scores(word.start_ms, word.end_ms) {
                    *votes
                        .entry(key.clone())
                        .or_default()
                        .entry(name)
                        .or_default() += weight;
                }
            }
        };
        for chunk in record.chunks.iter().filter(|c| c.channel != "me") {
            for word in &chunk.words {
                add(&chunk.id, word);
            }
        }
        for (channel, words) in streamed.iter().filter(|(channel, _)| *channel != "me") {
            for word in words
                .iter()
                .filter(|w| !crate::transcript::covered(record, channel, w.start_ms))
            {
                // Streaming service voices and offline voices have different numbering.
                add(
                    &format!("stream:{channel}"),
                    &Word {
                        speaker: String::new(),
                        turn: word.speaker.clone(),
                        ..word.clone()
                    },
                );
            }
        }
        Self {
            evidence,
            voices: votes
                .into_iter()
                .filter_map(|(voice, votes)| winner(votes).map(|name| (voice, name.to_owned())))
                .collect(),
        }
    }
    pub fn word(&self, chunk: &str, word: &Word) -> String {
        cluster(chunk, word)
            .and_then(|key| self.voices.get(&key).cloned())
            .or_else(|| self.evidence.at(word.start_ms, word.end_ms))
            .unwrap_or_else(|| word.speaker.clone())
    }
    pub fn streamed(&self, channel: &str, words: &mut [Word]) {
        for word in words {
            word.speaker = if channel == "me" {
                "Me".into()
            } else {
                let raw = Word {
                    speaker: String::new(),
                    turn: word.speaker.clone(),
                    ..word.clone()
                };
                let named = self.word(&format!("stream:{channel}"), &raw);
                if named.is_empty() {
                    word.speaker.clone()
                } else {
                    named
                }
            };
        }
    }
}

/// Project names without mutating stored provider/diarization evidence or user edits.
pub fn named(record: &Recording) -> Recording {
    let mut out = record.clone();
    let names = Names::new(record, &BTreeMap::new());
    for chunk in &mut out.chunks {
        if chunk.channel == "me" {
            continue;
        }
        if chunk.words.is_empty() {
            // A manual edit deliberately replaces the automatic transcript.
            if chunk.provider["source"] != "edit" {
                if let Some(name) = record.meeting_speakers.at(chunk.start_ms, chunk.end_ms) {
                    chunk.speaker = name;
                }
            }
        } else {
            for word in &mut chunk.words {
                word.speaker = names.word(&chunk.id, word);
            }
        }
    }
    out
}

/// Turn point observations into bounded intervals. Nothing is inferred through a missed poll.
#[derive(Default)]
pub(super) struct Samples {
    previous: Option<(u64, Observation)>,
}
impl Samples {
    pub fn push(&mut self, at: u64, observed: Observation) -> Vec<Interval> {
        let mut out = vec![];
        if let Some((before, previous)) = self.previous.take() {
            if at > before && at - before <= MAX_SAMPLE_GAP_MS {
                for name in previous.active.into_iter().collect::<BTreeSet<_>>() {
                    if !previous
                        .self_names
                        .iter()
                        .chain(&observed.self_names)
                        .any(|local| same(local, &name))
                    {
                        out.push(Interval {
                            start_ms: before,
                            end_ms: at,
                            name,
                        });
                    }
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
