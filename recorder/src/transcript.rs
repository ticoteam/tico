//! Conversation lines from per-channel chunks. Long chunks transcribe better but hold many turns,
//! so lines are rebuilt from word timings: the microphone is "Me", meeting audio is whoever
//! diarization found (or the chunk's speaker), and speech is ordered by when it was said.
//! Microphone words that repeat meeting audio within a second are speaker echo and are dropped,
//! the transcript-level echo removal meeting recorders use when the call plays on speakers.
use crate::store::{Chunk, Recording, Word};
use serde::Serialize;
use std::collections::BTreeMap;

const ECHO_MS: u64 = 1000;
/// A pause this long inside one chunk ends an utterance, so the other side can interleave.
const UTTERANCE_GAP_MS: u64 = 800;
/// Same-speaker utterances closer than this join one line.
const LINE_GAP_MS: u64 = 1500;

#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub speaker: String,
    pub me: bool,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    /// The chunk the line starts in.
    pub chunk_id: String,
}

fn normal(word: &str) -> String {
    let word: String = word
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect();
    match word.as_str() {
        "zero" => "0",
        "one" => "1",
        "two" => "2",
        "three" => "3",
        "four" => "4",
        "five" => "5",
        "six" => "6",
        "seven" => "7",
        "eight" => "8",
        "nine" => "9",
        "ten" => "10",
        "eleven" => "11",
        "twelve" => "12",
        "thirteen" => "13",
        "fourteen" => "14",
        "fifteen" => "15",
        "sixteen" => "16",
        "seventeen" => "17",
        "eighteen" => "18",
        "nineteen" => "19",
        "twenty" => "20",
        "thirty" => "30",
        "forty" => "40",
        "fifty" => "50",
        "sixty" => "60",
        "seventy" => "70",
        "eighty" => "80",
        "ninety" => "90",
        _ => &word,
    }
    .to_owned()
}

fn tokens(text: &str) -> Vec<String> {
    text.split(|c: char| !c.is_alphanumeric() && c != '\'')
        .map(normal)
        .filter(|s| !s.is_empty())
        .collect()
}

/// Conservative spelling tolerance: short acknowledgements still require an exact match.
fn similar(a: &str, b: &str) -> bool {
    if a == b {
        return true;
    }
    let (a, b): (Vec<_>, Vec<_>) = (a.chars().collect(), b.chars().collect());
    let limit = if a.len().min(b.len()) >= 9 {
        2
    } else if a.len().min(b.len()) >= 5 {
        1
    } else {
        return false;
    };
    if a.len().abs_diff(b.len()) > limit {
        return false;
    }
    let mut row: Vec<usize> = (0..=b.len()).collect();
    for (i, x) in a.iter().enumerate() {
        let mut prev = row[0];
        row[0] = i + 1;
        for (j, y) in b.iter().enumerate() {
            let old = row[j + 1];
            row[j + 1] = (row[j] + 1).min(old + 1).min(prev + usize::from(x != y));
            prev = old;
        }
    }
    row[b.len()] <= limit
}

/// Meeting-audio tokens by time, including joined tokens for differently split hyphenated words.
struct Heard(Vec<(u64, String)>);
impl Heard {
    fn of<'a>(words: impl Iterator<Item = &'a Word>) -> Self {
        let mut heard: Vec<_> = words
            .flat_map(|w| tokens(&w.text).into_iter().map(move |t| (w.start_ms, t)))
            .collect();
        heard.sort_by_key(|(at, _)| *at);
        let joined: Vec<_> = heard
            .windows(2)
            .filter(|w| w[1].0.saturating_sub(w[0].0) < UTTERANCE_GAP_MS)
            .flat_map(|w| {
                let mut forms = vec![(w[0].0, format!("{}{}", w[0].1, w[1].1))];
                if let (Ok(tens), Ok(units)) = (w[0].1.parse::<u32>(), w[1].1.parse::<u32>()) {
                    if tens >= 20 && tens <= 90 && tens % 10 == 0 && units < 10 {
                        forms.push((w[0].0, (tens + units).to_string()));
                    }
                }
                forms
            })
            .collect();
        heard.extend(joined);
        heard.sort_by_key(|(at, _)| *at);
        Self(heard)
    }
    fn chunks(record: &Recording) -> Self {
        Self::of(
            record
                .chunks
                .iter()
                .filter(|c| c.channel != "me")
                .flat_map(|c| c.words.iter()),
        )
    }
    fn echo(&self, start: u64, text: &str) -> bool {
        let first = self
            .0
            .partition_point(|(at, _)| at.saturating_add(ECHO_MS) < start);
        let nearby: Vec<_> = self.0[first..]
            .iter()
            .take_while(|(at, _)| *at <= start.saturating_add(ECHO_MS))
            .collect();
        let words = tokens(text);
        !words.is_empty()
            && (words
                .iter()
                .all(|word| nearby.iter().any(|(_, other)| similar(word, other)))
                || nearby
                    .iter()
                    .any(|(_, other)| similar(&normal(text), other)))
    }
    fn clean(&self, words: &[Word]) -> Vec<Word> {
        let mut out = vec![];
        let mut from = 0;
        while from < words.len() {
            let mut to = from + 1;
            while to < words.len()
                && words[to].start_ms.saturating_sub(words[to - 1].end_ms) < UTTERANCE_GAP_MS
            {
                to += 1;
            }
            let utterance = &words[from..to];
            let echoes: Vec<_> = utterance
                .iter()
                .map(|w| self.echo(w.start_ms, &w.text))
                .collect();
            // Drop the entire echoed utterance, including recognition debris such as "an" or "uh".
            // Separate genuine local speech in the same chunk keeps its own decision.
            if echoes.iter().filter(|e| **e).count() * 5 < utterance.len() * 3 {
                out.extend(
                    utterance
                        .iter()
                        .zip(echoes)
                        .filter(|(_, echo)| !echo)
                        .map(|(w, _)| w.clone()),
                );
            }
            from = to;
        }
        out
    }
}

/// Streamed microphone utterances without speaker bleed, using the same rule as saved chunks.
pub fn without_echo(me: &[Word], them: &[Word]) -> Vec<Word> {
    Heard::of(them.iter()).clean(me)
}

/// Utterances in time order, shared by the page, exports and live delivery.
pub fn utterances(record: &Recording) -> Vec<Line> {
    let cleaned = chunk_words(record);
    let mut out = vec![];
    for chunk in &record.chunks {
        let Some(words) = cleaned.get(&chunk.id) else {
            continue;
        };
        let me = chunk.channel == "me";
        let mut current: Option<Line> = None;
        for word in words {
            let speaker = if me {
                "Me"
            } else if word.speaker.is_empty() {
                &chunk.speaker
            } else {
                &word.speaker
            };
            match &mut current {
                Some(line)
                    if line.speaker == speaker
                        && word.start_ms.saturating_sub(line.end_ms) < UTTERANCE_GAP_MS =>
                {
                    line.text.push(' ');
                    line.text.push_str(&word.text);
                    line.end_ms = line.end_ms.max(word.end_ms);
                }
                _ => {
                    out.extend(current.take());
                    current = Some(Line {
                        speaker: speaker.into(),
                        me,
                        start_ms: word.start_ms,
                        end_ms: word.end_ms.max(word.start_ms),
                        text: word.text.clone(),
                        chunk_id: chunk.id.clone(),
                    });
                }
            }
        }
        out.extend(current);
    }
    out.sort_by_key(|line| (line.start_ms, !line.me));
    out
}

/// Lines for display and for Tico: consecutive utterances of one speaker joined.
pub fn lines(record: &Recording) -> Vec<Line> {
    join(utterances(record))
}
fn join(utterances: Vec<Line>) -> Vec<Line> {
    let mut out: Vec<Line> = vec![];
    for utterance in utterances {
        match out.last_mut() {
            Some(line)
                if line.speaker == utterance.speaker
                    && line.me == utterance.me
                    && utterance.start_ms.saturating_sub(line.end_ms) < LINE_GAP_MS =>
            {
                line.text.push(' ');
                line.text.push_str(&utterance.text);
                line.end_ms = line.end_ms.max(utterance.end_ms);
                // A line holding any streamed words is marked by them.
                if utterance.chunk_id.starts_with(STREAM_PREFIX) {
                    line.chunk_id = utterance.chunk_id;
                }
            }
            _ => out.push(utterance),
        }
    }
    out
}

/// Chunk id of the stand-in chunk that carries one channel's streamed words.
const STREAM_PREFIX: &str = "stream:";

/// The text still being spoken on one channel: the stream's latest interim result.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Partial {
    pub channel: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    pub words: Vec<Word>,
}

/// One row of the live transcript. `streamed` rows hold streamed text that a finished chunk
/// has not replaced yet; a `partial` row is still changing.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LiveTurn {
    pub speaker: String,
    pub me: bool,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    pub streamed: bool,
    pub partial: bool,
}

/// Where a channel's finished chunks already hold the transcript of record.
pub fn covered(record: &Recording, channel: &str, at_ms: u64) -> bool {
    record.chunks.iter().any(|chunk| {
        chunk.channel == channel
            && ["done", "silence"].contains(&chunk.status.as_str())
            && (chunk.start_ms..chunk.end_ms).contains(&at_ms)
    })
}

/// Whether meeting-audio transcription has reached a microphone range. Silence counts as a
/// completed reference; a later streaming final also settles the preceding quiet interval.
pub fn echo_ready(record: &Recording, from: u64, to: u64, them: &[Word]) -> bool {
    if !record.meeting_audio {
        return true;
    }
    if them.iter().map(|w| w.end_ms).max().unwrap_or(0) >= to.saturating_add(ECHO_MS) {
        return true;
    }
    let chunks: Vec<_> = record
        .chunks
        .iter()
        .filter(|c| c.channel == "them")
        .collect();
    // Inputs can start/stop a little apart. Missing audio before the tap starts or after Stop
    // is not an unfinished transcription; only recorded overlapping ranges can be pending.
    let reached = record.state != "recording" || chunks.iter().any(|c| c.end_ms >= to);
    reached
        && chunks
            .iter()
            .filter(|c| c.start_ms < to && c.end_ms > from)
            .all(|c| ["done", "silence"].contains(&c.status.as_str()))
}

/// Hold a whole microphone utterance until its reference is ready, before echo decisions or
/// live outbox bookkeeping can make an irreversible delivery.
pub fn ready_microphone(record: &Recording, words: &[Word], them: &[Word]) -> Vec<Word> {
    let mut ready = vec![];
    let mut from = 0;
    while from < words.len() {
        let mut to = from + 1;
        while to < words.len()
            && words[to].start_ms.saturating_sub(words[to - 1].end_ms) < UTTERANCE_GAP_MS
        {
            to += 1;
        }
        if echo_ready(record, words[from].start_ms, words[to - 1].end_ms, them) {
            ready.extend_from_slice(&words[from..to]);
        }
        from = to;
    }
    ready
}

/// A live-delivery snapshot: pending reference ranges stay pending, even when the microphone
/// request completed first. Streamed meeting words can release and clean finished mic chunks.
pub fn echo_snapshot(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Recording {
    let mut out = record.clone();
    let them = reference_words(record, streamed);
    if let Some(live) = streamed.get("them") {
        let mut live: Vec<_> = live.iter().filter(|w| !w.speaker.is_empty()).collect();
        live.sort_by_key(|w| w.start_ms);
        let mut ends = Vec::with_capacity(live.len());
        let mut end = 0;
        for word in &live {
            end = end.max(word.end_ms);
            ends.push(end);
        }
        let overlapping = |word: &Word| {
            let from = ends.partition_point(|end| *end <= word.start_ms);
            let until = word.end_ms;
            live[from..]
                .iter()
                .take_while(move |other| other.start_ms < until)
        };
        for chunk in out.chunks.iter_mut().filter(|c| c.channel == "them") {
            let mut votes: BTreeMap<String, BTreeMap<String, u64>> = BTreeMap::new();
            for word in chunk.words.iter().filter(|w| !w.turn.is_empty()) {
                for other in overlapping(word) {
                    let overlap = word
                        .end_ms
                        .min(other.end_ms)
                        .saturating_sub(word.start_ms.max(other.start_ms));
                    if overlap > 0 {
                        *votes
                            .entry(word.turn.clone())
                            .or_default()
                            .entry(other.speaker.clone())
                            .or_default() += overlap;
                    }
                }
            }
            for word in &mut chunk.words {
                if word.speaker.is_empty() {
                    if word.turn.is_empty() {
                        if let Some(other) = overlapping(word).max_by_key(|other| {
                            word.end_ms
                                .min(other.end_ms)
                                .saturating_sub(word.start_ms.max(other.start_ms))
                        }) {
                            word.speaker = other.speaker.clone();
                        }
                        continue;
                    }
                    if let Some((name, _)) = votes
                        .get(&word.turn)
                        .and_then(|v| v.iter().max_by_key(|(_, n)| *n))
                    {
                        word.speaker = name.clone();
                    }
                }
            }
        }
    }
    for chunk in &mut out.chunks {
        if chunk.channel != "me" {
            continue;
        }
        if !echo_ready(record, chunk.start_ms, chunk.end_ms, &them) {
            chunk.status = "pending".into();
            chunk.words.clear();
            chunk.text.clear();
        } else {
            chunk.provider["echo_ready"] = serde_json::json!(true);
            if chunk.words.is_empty() {
                continue;
            }
            chunk.words = without_echo(&chunk.words, &them);
            chunk.text = chunk
                .words
                .iter()
                .map(|w| w.text.as_str())
                .collect::<Vec<_>>()
                .join(" ");
        }
    }
    out
}

fn reference_words(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Vec<Word> {
    record
        .chunks
        .iter()
        .filter(|c| c.channel == "them")
        .flat_map(|c| c.words.iter())
        .chain(streamed.get("them").into_iter().flatten())
        .cloned()
        .collect()
}

/// The recording with streamed words added where no finished chunk covers them yet. Finished
/// chunks always win: once a chunk is transcribed, the streamed words of its time range go.
fn with_streamed(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Recording {
    let mut merged = echo_snapshot(record, streamed);
    let them = reference_words(record, streamed);
    for (channel, words) in streamed {
        let me = channel == "me";
        let words: Vec<Word> = words
            .iter()
            .filter(|word| !word.text.trim().is_empty() && !covered(record, channel, word.start_ms))
            .cloned()
            .collect();
        let words = if me {
            without_echo(&ready_microphone(record, &words, &them), &them)
        } else {
            words
        };
        let (Some(first), Some(last)) = (words.first(), words.iter().map(|w| w.end_ms).max())
        else {
            continue;
        };
        let mut chunk: Chunk = serde_json::from_value(serde_json::json!({
            "id": format!("{STREAM_PREFIX}{channel}"), "recording_id": record.id, "channel": channel,
            "speaker": if me { "Me" } else { "Meeting audio" },
            "start_ms": first.start_ms, "end_ms": last.max(first.start_ms + 1), "file": "",
            "status": "done", "signal": {"samples": 0, "nonzero": 0, "peak": 0.0, "rms": 0.0,
            "duration_ms": 0, "sample_rate": 16000}, "text": "", "error": "", "attempts": 0,
            "provider": {"source": "stream", "echo_ready": true},
        }))
        .expect("stand-in chunk");
        chunk.text = words
            .iter()
            .map(|w| w.text.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        chunk.words = words;
        merged.chunks.push(chunk);
    }
    merged
}

/// Lines from finished chunks plus final streamed words elsewhere, without the changing tail.
/// Drafting reads these, so suggestions can come from speech a few seconds old.
pub fn live_lines(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Vec<Line> {
    lines(&with_streamed(record, streamed))
}

/// The transcript to show while recording: chunk text where a chunk is done, streamed final
/// text elsewhere, and each channel's partial text at the end, marked as partial.
pub fn live_turns(
    record: &Recording,
    streamed: &BTreeMap<String, Vec<Word>>,
    partials: &[Partial],
) -> Vec<LiveTurn> {
    let mut out: Vec<LiveTurn> = live_lines(record, streamed)
        .into_iter()
        .map(|line| LiveTurn {
            streamed: line.chunk_id.starts_with(STREAM_PREFIX),
            speaker: line.speaker,
            me: line.me,
            start_ms: line.start_ms,
            end_ms: line.end_ms,
            text: line.text,
            partial: false,
        })
        .collect();
    let mut reference = reference_words(record, streamed);
    reference.extend(
        partials
            .iter()
            .filter(|p| p.channel == "them")
            .flat_map(|p| p.words.clone()),
    );
    let mut tails: Vec<_> = partials
        .iter()
        .filter(|partial| {
            !partial.text.trim().is_empty() && !covered(record, &partial.channel, partial.start_ms)
        })
        .collect();
    tails.sort_by_key(|partial| partial.start_ms);
    for partial in tails {
        let me = partial.channel == "me";
        if me {
            // A changing mic tail cannot establish that unmatched fragments are local speech.
            // Wait for a settled meeting reference, then apply the whole-utterance rule.
            if !echo_ready(record, partial.start_ms, partial.end_ms, &reference) {
                continue;
            }
            let words = if partial.words.is_empty() {
                vec![Word {
                    start_ms: partial.start_ms,
                    end_ms: partial.end_ms,
                    text: partial.text.clone(),
                    ..Default::default()
                }]
            } else {
                partial.words.clone()
            };
            let clean = without_echo(&words, &reference);
            if clean.is_empty() {
                continue;
            }
            out.push(LiveTurn {
                speaker: "Me".into(),
                me: true,
                start_ms: partial.start_ms,
                end_ms: partial.end_ms,
                text: clean
                    .iter()
                    .map(|w| w.text.as_str())
                    .collect::<Vec<_>>()
                    .join(" "),
                streamed: true,
                partial: true,
            });
            continue;
        }
        let speaker = if me {
            "Me".to_owned()
        } else {
            partial
                .words
                .iter()
                .rev()
                .find(|word| !word.speaker.is_empty())
                .map_or_else(
                    || "Meeting audio".to_owned(),
                    |word| {
                        if word.speaker.starts_with("Speaker ") {
                            return word.speaker.clone();
                        }
                        let mut labels: Vec<_> =
                            reference.iter().filter(|w| !w.speaker.is_empty()).collect();
                        labels.sort_by_key(|w| w.start_ms);
                        let mut names = BTreeMap::new();
                        for w in labels {
                            let n = names.len() + 1;
                            names.entry(&w.speaker).or_insert(n);
                        }
                        format!("Speaker {}", names.get(&word.speaker).copied().unwrap_or(1))
                    },
                )
        };
        out.push(LiveTurn {
            speaker,
            me,
            start_ms: partial.start_ms,
            end_ms: partial.end_ms,
            text: partial.text.trim().into(),
            streamed: true,
            partial: true,
        });
    }
    out
}

/// Each chunk's words without echo, for the live feed (which shares whole chunks in order).
pub fn chunk_texts(record: &Recording) -> BTreeMap<String, String> {
    let mut out: BTreeMap<String, String> = BTreeMap::new();
    for utterance in utterances(record) {
        let text = out.entry(utterance.chunk_id).or_default();
        if !text.is_empty() {
            text.push(' ');
        }
        text.push_str(&utterance.text);
    }
    out
}

/// Each chunk's words without echo, as `chunk_texts` has them. `speaker` is kept only where
/// diarization named a meeting-audio voice. A chunk without word timings is one word spanning it.
pub fn chunk_words(record: &Recording) -> BTreeMap<String, Vec<Word>> {
    let heard = Heard::chunks(record);
    let mut out = BTreeMap::new();
    let mut labelled: Vec<_> = record
        .chunks
        .iter()
        .filter(|c| c.channel != "me")
        .flat_map(|c| c.words.iter().map(move |w| (w.start_ms, identity(c, w))))
        .chain(
            record
                .chunks
                .iter()
                .filter(|c| c.channel != "me" && c.words.is_empty())
                .map(|c| (c.start_ms, identity(c, &Word::default()))),
        )
        .filter(|(_, label)| !label.is_empty())
        .collect();
    labelled.sort_by_key(|w| w.0);
    let mut next = labelled
        .iter()
        .filter_map(|(_, label)| label.strip_prefix("Speaker ")?.parse::<usize>().ok())
        .max()
        .unwrap_or(0)
        + 1;
    let mut names = BTreeMap::new();
    for (_, label) in labelled {
        if label.starts_with("Speaker ") {
            continue;
        }
        if !names.contains_key(&label) {
            names.insert(label, format!("Speaker {next}"));
            next += 1;
        }
    }
    for chunk in record.chunks.iter().filter(|c| !c.text.trim().is_empty()) {
        let me = chunk.channel == "me";
        if me
            && chunk.provider["echo_ready"] != true
            && !echo_ready(record, chunk.start_ms, chunk.end_ms, &[])
        {
            continue;
        }
        let words = if chunk.words.is_empty() {
            vec![Word {
                start_ms: chunk.start_ms,
                end_ms: chunk.end_ms,
                text: chunk.text.trim().into(),
                ..Default::default()
            }]
        } else {
            chunk.words.clone()
        };
        let words = if me {
            heard.clean(&words)
        } else {
            words
                .into_iter()
                .map(|word| {
                    let label = identity(chunk, &word);
                    Word {
                        speaker: names.get(&label).cloned().unwrap_or(label),
                        ..word
                    }
                })
                .collect()
        };
        out.insert(chunk.id.clone(), words);
    }
    out
}

fn identity(chunk: &Chunk, word: &Word) -> String {
    if !word.speaker.is_empty() {
        word.speaker.clone()
    } else if !word.turn.is_empty() {
        format!("{}:{}", chunk.id, word.turn)
    } else if !["Meeting audio", "Me"].contains(&chunk.speaker.as_str()) {
        chunk.speaker.clone()
    } else {
        String::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::Word;

    fn chunk(id: &str, channel: &str, start_ms: u64, words: &[(u64, &str, &str)]) -> Chunk {
        serde_json::from_value(serde_json::json!({
            "id": id, "recording_id": "r", "channel": channel,
            "speaker": if channel == "me" { "Me" } else { "Meeting audio" },
            "start_ms": start_ms, "end_ms": start_ms + 45_000, "file": "", "status": "done",
            "signal": {"samples": 1, "nonzero": 1, "peak": 0.5, "rms": 0.1, "duration_ms": 45000, "sample_rate": 16000},
            "text": words.iter().map(|w| w.1).collect::<Vec<_>>().join(" "),
            "error": "", "attempts": 1, "provider": {},
            "words": words.iter().map(|(at, text, speaker)| Word {
                start_ms: *at, end_ms: at + 300, text: (*text).into(), speaker: (*speaker).into(), turn: String::new(),
            }).collect::<Vec<_>>(),
        }))
        .unwrap()
    }

    fn record(chunks: Vec<Chunk>) -> Recording {
        serde_json::from_value(serde_json::json!({
            "id": "r", "title": "t", "started_at": "", "state": "stopped", "microphone": "",
            "meeting_audio": true, "warning": "", "chunks": chunks, "deliveries": [],
        }))
        .unwrap()
    }

    #[test]
    fn long_chunks_become_interleaved_turns_without_speaker_echo() {
        let record = record(vec![
            chunk(
                "m",
                "me",
                0,
                &[
                    (0, "Can", ""),
                    (300, "you", ""),
                    (600, "hear?", ""),
                    // The far end's reply, played on speakers, reaches the mic 40 ms later.
                    (2040, "yes,", ""),
                    (2340, "fine.", ""),
                    (5000, "Great.", ""),
                ],
            ),
            chunk(
                "t",
                "them",
                0,
                &[
                    (2000, "Yes,", "Speaker 1"),
                    (2300, "fine.", "Speaker 1"),
                    (3500, "Me", "Speaker 2"),
                    (3800, "too.", "Speaker 2"),
                ],
            ),
        ]);
        let got: Vec<_> = lines(&record)
            .into_iter()
            .map(|line| (line.speaker, line.text))
            .collect();
        assert_eq!(
            got,
            [
                ("Me", "Can you hear?"),
                ("Speaker 1", "Yes, fine."),
                ("Speaker 2", "Me too."),
                ("Me", "Great.")
            ]
            .map(|(a, b)| (a.to_owned(), b.to_owned()))
        );
        assert_eq!(chunk_texts(&record)["m"], "Can you hear? Great.");
    }

    #[test]
    fn finished_chunks_replace_streamed_text_for_their_time_range() {
        let word = |at: u64, text: &str, speaker: &str| Word {
            start_ms: at,
            end_ms: at + 300,
            text: text.into(),
            speaker: speaker.into(),
            turn: String::new(),
        };
        let mut streamed = BTreeMap::new();
        streamed.insert(
            "me".to_owned(),
            vec![
                word(1000, "we", ""),
                word(1300, "chip", ""),
                word(50_000, "later", ""),
            ],
        );
        streamed.insert(
            "them".to_owned(),
            vec![word(2000, "sure", "S1"), word(2300, "thing", "S1")],
        );
        let partials = [Partial {
            channel: "me".into(),
            start_ms: 51_000,
            end_ms: 52_000,
            text: "and then".into(),
            words: vec![],
        }];
        // Nothing transcribed yet: streamed text everywhere, the partial last.
        let mut pending = chunk("m", "me", 0, &[]);
        pending.status = "pending".into();
        pending.text.clear();
        let got = live_turns(&record(vec![pending.clone()]), &streamed, &partials);
        let rows: Vec<_> = got
            .iter()
            .map(|t| (t.speaker.as_str(), t.text.as_str(), t.streamed, t.partial))
            .collect();
        assert_eq!(
            rows,
            [
                ("Me", "we chip", true, false),
                ("Speaker 1", "sure thing", true, false),
                ("Me", "later", true, false),
                ("Me", "and then", true, true)
            ]
        );
        // The microphone chunk 0-45 s is done: its words replace the streamed ones there, and
        // streamed text after it and on the other channel stays.
        let done = chunk("m", "me", 0, &[(1000, "We", ""), (1300, "ship.", "")]);
        let got = live_turns(&record(vec![done]), &streamed, &partials);
        let rows: Vec<_> = got
            .iter()
            .map(|t| (t.speaker.as_str(), t.text.as_str(), t.streamed, t.partial))
            .collect();
        assert_eq!(
            rows,
            [
                ("Me", "We ship.", false, false),
                ("Speaker 1", "sure thing", true, false),
                ("Me", "later", true, false),
                ("Me", "and then", true, true)
            ]
        );
        // The batch transcript itself never includes streamed words.
        let done = chunk("m", "me", 0, &[(1000, "We", ""), (1300, "ship.", "")]);
        assert_eq!(lines(&record(vec![done])).len(), 1);
    }

    #[test]
    fn chunks_without_word_timings_keep_their_text() {
        let mut plain = chunk("m", "me", 0, &[(0, "Hello", "")]);
        plain.words.clear();
        let got = lines(&record(vec![plain]));
        assert_eq!(
            (got[0].speaker.as_str(), got[0].text.as_str()),
            ("Me", "Hello")
        );
    }

    #[test]
    fn whole_echo_utterances_drop_short_unmatched_fragments_but_keep_local_speech() {
        let mic = chunk(
            "m",
            "me",
            0,
            &[
                (1020, "Uh,", ""),
                (1220, "we", ""),
                (1420, "should", ""),
                (1620, "ship", ""),
                (1820, "this", ""),
                (2020, "in-progress", ""),
                (2220, "an", ""),
                (5000, "My", ""),
                (5300, "different", ""),
                (5600, "proposal", ""),
            ],
        );
        let them = chunk(
            "t",
            "them",
            0,
            &[
                (1200, "We", ""),
                (1400, "should", ""),
                (1600, "ship", ""),
                (1800, "this", ""),
                (2000, "in progress.", ""),
            ],
        );
        let record = record(vec![mic, them]);
        assert_eq!(chunk_texts(&record)["m"], "My different proposal");
        assert_eq!(chunk_words(&record)["m"].len(), 3);
    }

    #[test]
    fn fuzzy_echo_handles_punctuation_hyphens_spelling_and_numbers() {
        let words = |texts: &[&str], offset| {
            texts
                .iter()
                .enumerate()
                .map(|(i, text)| Word {
                    start_ms: offset + i as u64 * 200,
                    end_ms: offset + i as u64 * 200 + 180,
                    text: (*text).into(),
                    ..Default::default()
                })
                .collect::<Vec<_>>()
        };
        let remote = words(
            &[
                "The",
                "in-progress",
                "release",
                "ships",
                "twenty",
                "one",
                "updates",
            ],
            1000,
        );
        let mic = words(
            &[
                "the",
                "inprogress",
                "relese",
                "ships",
                "21",
                "updatez",
                "uh",
            ],
            1020,
        );
        assert!(without_echo(&mic, &remote).is_empty());
        assert_eq!(
            without_echo(&words(&["no", "my", "local", "idea"], 1000), &remote).len(),
            4
        );
        assert_eq!(
            without_echo(
                &mic,
                &words(
                    &["the", "inprogress", "relese", "ships", "21", "updatez"],
                    10_000
                )
            )
            .len(),
            7
        );
    }

    #[test]
    fn finished_microphone_waits_for_overlapping_meeting_transcription() {
        let mic = chunk(
            "m",
            "me",
            0,
            &[(1020, "Ship", ""), (1320, "tomorrow", ""), (1620, "uh", "")],
        );
        let mut remote = chunk("t", "them", 0, &[]);
        remote.status = "running".into();
        let mut record = record(vec![mic, remote]);
        record.state = "recording".into();
        assert!(chunk_texts(&record).is_empty());
        assert!(live_lines(&record, &BTreeMap::new()).is_empty());
        assert_eq!(
            echo_snapshot(&record, &BTreeMap::new()).chunks[0].status,
            "pending"
        );
        record.chunks[1] = chunk(
            "t",
            "them",
            0,
            &[(1000, "Ship", ""), (1300, "tomorrow", "")],
        );
        assert!(!chunk_texts(&record).contains_key("m"));
        assert_eq!(live_lines(&record, &BTreeMap::new()).len(), 1);
        record.chunks[0] = chunk("m", "me", 0, &[(5000, "Local", ""), (5300, "idea", "")]);
        assert_eq!(chunk_texts(&record)["m"], "Local idea");
    }

    #[test]
    fn streamed_reference_releases_finished_mic_and_silence_releases_genuine_speech() {
        let mic = chunk(
            "m",
            "me",
            0,
            &[(1020, "Ship", ""), (1320, "tomorrow", ""), (1620, "uh", "")],
        );
        let mut remote = chunk("t", "them", 0, &[]);
        remote.status = "pending".into();
        let mut record = record(vec![mic, remote]);
        record.state = "recording".into();
        let streamed = BTreeMap::from([(
            "them".into(),
            vec![
                Word {
                    start_ms: 1000,
                    end_ms: 1200,
                    text: "Ship".into(),
                    ..Default::default()
                },
                Word {
                    start_ms: 1300,
                    end_ms: 1500,
                    text: "tomorrow".into(),
                    ..Default::default()
                },
                Word {
                    start_ms: 46_000,
                    end_ms: 46_300,
                    text: "Later".into(),
                    ..Default::default()
                },
            ],
        )]);
        assert!(live_lines(&record, &streamed).iter().all(|l| !l.me));
        let snapshot = echo_snapshot(&record, &streamed);
        assert_eq!(snapshot.chunks[0].status, "done");
        assert!(snapshot.chunks[0].text.is_empty());
        record.chunks[0] = chunk("m", "me", 0, &[(5000, "Local", ""), (5300, "idea", "")]);
        record.chunks[1].status = "silence".into();
        assert_eq!(live_lines(&record, &BTreeMap::new())[0].text, "Local idea");
    }

    #[test]
    fn streamed_microphone_finals_and_partials_wait_and_drop_whole_echo() {
        let mut record = record(vec![]);
        record.state = "recording".into();
        let me = vec![
            Word {
                start_ms: 1020,
                end_ms: 1200,
                text: "Ship".into(),
                ..Default::default()
            },
            Word {
                start_ms: 1220,
                end_ms: 1400,
                text: "tomorrow".into(),
                ..Default::default()
            },
            Word {
                start_ms: 1420,
                end_ms: 1500,
                text: "uh".into(),
                ..Default::default()
            },
        ];
        let mut streamed = BTreeMap::from([("me".into(), me.clone())]);
        let partial = Partial {
            channel: "me".into(),
            start_ms: 1020,
            end_ms: 1500,
            text: "Ship tomorrow uh".into(),
            words: me,
        };
        assert!(live_turns(&record, &streamed, &[partial.clone()]).is_empty());
        streamed.insert(
            "them".into(),
            vec![
                Word {
                    start_ms: 1000,
                    end_ms: 1200,
                    text: "Ship".into(),
                    ..Default::default()
                },
                Word {
                    start_ms: 1200,
                    end_ms: 1400,
                    text: "tomorrow".into(),
                    ..Default::default()
                },
                Word {
                    start_ms: 3000,
                    end_ms: 3200,
                    text: "Next".into(),
                    ..Default::default()
                },
            ],
        );
        assert!(
            live_turns(&record, &streamed, &[partial])
                .iter()
                .all(|t| !t.me)
        );
    }

    #[test]
    fn provisional_speakers_are_numbered_by_first_appearance_across_chunks() {
        let record = record(vec![
            chunk(
                "b",
                "them",
                45_000,
                &[(46_000, "Again", "S4"), (47_000, "Second", "S0")],
            ),
            chunk(
                "a",
                "them",
                0,
                &[(1000, "First", "S4"), (2000, "Hello", "S0")],
            ),
        ]);
        let got: Vec<_> = lines(&record).into_iter().map(|l| l.speaker).collect();
        assert_eq!(got, ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]);
    }

    #[test]
    fn batch_turns_follow_the_overlapping_live_voice_until_voiceprints_are_ready() {
        let mut a = chunk("a", "them", 0, &[(1000, "First", "")]);
        a.words[0].turn = "service-3".into();
        let mut b = chunk("b", "them", 45_000, &[(46_000, "Again", "")]);
        b.words[0].turn = "service-0".into();
        let record = record(vec![a, b]);
        let live = BTreeMap::from([(
            "them".into(),
            vec![
                Word {
                    start_ms: 1000,
                    end_ms: 1300,
                    text: "First".into(),
                    speaker: "Speaker 2".into(),
                    ..Default::default()
                },
                Word {
                    start_ms: 46_000,
                    end_ms: 46_300,
                    text: "Again".into(),
                    speaker: "Speaker 2".into(),
                    ..Default::default()
                },
            ],
        )]);
        assert!(
            live_lines(&record, &live)
                .iter()
                .all(|l| l.speaker == "Speaker 2")
        );
    }

    #[test]
    fn untimed_service_chunk_labels_never_reach_the_view_raw() {
        let mut chunk = chunk("remote", "them", 0, &[(1000, "Hello", "")]);
        chunk.words.clear();
        chunk.speaker = "S4".into();
        assert_eq!(lines(&record(vec![chunk]))[0].speaker, "Speaker 1");
    }

    #[test]
    fn different_capture_start_and_stop_times_do_not_hold_finished_mic_forever() {
        let mut mic = chunk(
            "m",
            "me",
            0,
            &[(100, "Opening", ""), (44_800, "Closing", "")],
        );
        mic.end_ms = 45_000;
        let mut remote = chunk("t", "them", 2000, &[]);
        remote.end_ms = 44_900;
        remote.status = "silence".into();
        assert_eq!(
            chunk_texts(&record(vec![mic, remote]))["m"],
            "Opening Closing"
        );
    }
}
