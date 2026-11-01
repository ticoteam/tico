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
    word.chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

/// Meeting-audio words by start time, to find speaker echo on the microphone.
struct Heard(Vec<(u64, String)>);
impl Heard {
    fn of<'a>(words: impl Iterator<Item = &'a Word>) -> Self {
        let mut heard: Vec<(u64, String)> = words
            .map(|word| (word.start_ms, normal(&word.text)))
            .filter(|(_, word)| !word.is_empty())
            .collect();
        heard.sort();
        Self(heard)
    }
    fn chunks(record: &Recording) -> Self {
        Self::of(
            record
                .chunks
                .iter()
                .filter(|chunk| chunk.channel != "me")
                .flat_map(|chunk| chunk.words.iter()),
        )
    }
    /// Whether a microphone word at `start` repeats meeting audio within a second.
    fn echo(&self, start: u64, text: &str) -> bool {
        let word = normal(text);
        if word.is_empty() {
            return false;
        }
        let first = self.0.partition_point(|(at, _)| *at + ECHO_MS < start);
        self.0[first..]
            .iter()
            .take_while(|(at, _)| *at <= start + ECHO_MS)
            .any(|(_, other)| *other == word)
    }
}

/// Streamed microphone words without the ones that repeat streamed meeting audio.
pub fn without_echo(me: &[Word], them: &[Word]) -> Vec<Word> {
    let heard = Heard::of(them.iter());
    me.iter()
        .filter(|word| !heard.echo(word.start_ms, &word.text))
        .cloned()
        .collect()
}

/// Utterances in time order: runs of words from one chunk and speaker without a long pause.
pub fn utterances(record: &Recording) -> Vec<Line> {
    let heard = Heard::chunks(record);
    let echo =
        |chunk: &Chunk, start: u64, text: &str| chunk.channel == "me" && heard.echo(start, text);
    let mut out = vec![];
    for chunk in &record.chunks {
        if chunk.text.trim().is_empty() {
            continue;
        }
        let me = chunk.channel == "me";
        if chunk.words.is_empty() {
            out.push(Line {
                speaker: chunk.speaker.clone(),
                me,
                start_ms: chunk.start_ms,
                end_ms: chunk.end_ms,
                text: chunk.text.trim().into(),
                chunk_id: chunk.id.clone(),
            });
            continue;
        }
        let mut current: Option<Line> = None;
        for word in &chunk.words {
            if echo(chunk, word.start_ms, &word.text) {
                continue;
            }
            let speaker = if me || word.speaker.is_empty() {
                &chunk.speaker
            } else {
                &word.speaker
            };
            match &mut current {
                Some(line)
                    if line.speaker == *speaker
                        && word.start_ms.saturating_sub(line.end_ms) < UTTERANCE_GAP_MS =>
                {
                    line.text.push(' ');
                    line.text.push_str(&word.text);
                    line.end_ms = line.end_ms.max(word.end_ms);
                }
                _ => {
                    out.extend(current.take());
                    current = Some(Line {
                        speaker: speaker.clone(),
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

/// The recording with streamed words added where no finished chunk covers them yet. Finished
/// chunks always win: once a chunk is transcribed, the streamed words of its time range go.
fn with_streamed(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Recording {
    let mut merged = record.clone();
    for (channel, words) in streamed {
        let me = channel == "me";
        let words: Vec<Word> = words
            .iter()
            .filter(|word| !word.text.trim().is_empty() && !covered(record, channel, word.start_ms))
            .cloned()
            .collect();
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
            "provider": {"source": "stream"},
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
    let mut tails: Vec<_> = partials
        .iter()
        .filter(|partial| {
            !partial.text.trim().is_empty() && !covered(record, &partial.channel, partial.start_ms)
        })
        .collect();
    tails.sort_by_key(|partial| partial.start_ms);
    for partial in tails {
        let me = partial.channel == "me";
        let speaker = if me {
            "Me".to_owned()
        } else {
            partial
                .words
                .iter()
                .rev()
                .find(|word| !word.speaker.is_empty())
                .map_or_else(|| "Meeting audio".to_owned(), |word| word.speaker.clone())
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
    for chunk in record
        .chunks
        .iter()
        .filter(|chunk| !chunk.text.trim().is_empty())
    {
        let me = chunk.channel == "me";
        let words = if chunk.words.is_empty() {
            vec![Word {
                start_ms: chunk.start_ms,
                end_ms: chunk.end_ms,
                text: chunk.text.trim().into(),
                ..Default::default()
            }]
        } else {
            chunk
                .words
                .iter()
                .filter(|word| !(me && heard.echo(word.start_ms, &word.text)))
                .map(|word| Word {
                    speaker: if me {
                        String::new()
                    } else {
                        word.speaker.clone()
                    },
                    ..word.clone()
                })
                .collect()
        };
        out.insert(chunk.id.clone(), words);
    }
    out
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
                ("S1", "sure thing", true, false),
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
                ("S1", "sure thing", true, false),
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
}
