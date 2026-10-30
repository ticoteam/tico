//! Action items and private answers from the transcript, with the recorder's own model key.
//! Nothing here reaches Tico: drafts stay on this computer until the person sends one.
use crate::{
    network::{client, hash, response},
    settings::{self, Settings},
    store::{Recording, Store, Word},
    transcript::{self, Line},
    workspace::{Context as MeetingContext, Item, MAX_ITEMS, MAX_TURNS, Turn},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::BTreeMap;

/// Most items found per review, so one long stretch of speech cannot flood the list.
const MAX_PER_REVIEW: usize = 5;
/// New speech that justifies a review while recording.
const MIN_NEW_CHARS: usize = 400;
/// While recording, the newest streamed seconds wait for the next review: the other channel's
/// final text for the same moment can still be on its way.
const SETTLE_MS: u64 = 5_000;

const DRAFT_PROMPT: &str = "You find action items in a meeting transcript while the meeting is \
happening. An action item is something a person committed to, asked for, or the group agreed \
should happen: a task, a follow-up to send, a document to change. Read the NEW lines (EARLIER \
lines are context only). Return JSON only: {\"items\":[{\"title\":\"...\",\"quote\":\"...\"}]}. \
title: a short imperative sentence naming the deliverable and, when said, who it is for or by \
when (\"Send Dana the revised pricing by Friday\"). quote: the exact words, copied character for \
character from one NEW line, that the item comes from (at most 25 words). Leave out small talk, \
vague ideas, questions nobody took on, things finished during the meeting, and anything already \
in EXISTING. Return {\"items\":[]} when there are none.";

const ASK_PROMPT: &str = "You answer the user's private questions about their meeting, using \
only its transcript and their notes. \"Me\" in the transcript is the user. Answer in a few \
plain sentences or a short list. Give times like (12:04) when they help. If the transcript does \
not say, say so.";

fn clock(ms: u64) -> String {
    let seconds = ms / 1000;
    format!("{:02}:{:02}", seconds / 60, seconds % 60)
}
fn row(line: &Line) -> Value {
    json!({"t":clock(line.start_ms),"speaker":line.speaker,"text":line.text})
}

/// One chat completion through the transcription provider (OpenRouter by default).
fn complete(
    config: &Settings,
    key: &str,
    messages: Vec<Value>,
    json_reply: bool,
) -> Result<String> {
    ensure!(
        !key.is_empty() && !config.assist_model.trim().is_empty(),
        "Add a transcription key in Settings"
    );
    let url = format!(
        "{}/chat/completions",
        settings::safe_url(&config.transcription_url)?
    );
    let mut body = json!({"model":config.assist_model,"messages":messages,"temperature":0.2,"max_tokens":1200});
    if json_reply {
        body["response_format"] = json!({"type":"json_object"});
    }
    let reply = response(client()?.post(&url).bearer_auth(key).json(&body).send()?)
        .context("The suggestion model did not answer")?;
    Ok(reply["choices"][0]["message"]["content"]
        .as_str()
        .context("The suggestion model returned no text")?
        .trim()
        .to_owned())
}

/// The JSON object in a reply, also when a model wraps it in prose or a code fence.
fn object(text: &str) -> Option<Value> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    serde_json::from_str(text.get(start..=end)?).ok()
}

/// The transcript words a quote stands for, with the line they are in, or None when the model
/// invented them.
fn locate<'a>(lines: &'a [Line], quote: &str) -> Option<(String, u64, &'a str)> {
    let wanted = quote.trim().trim_matches(['"', '“', '”']).trim();
    if wanted.is_empty() {
        return None;
    }
    let lower = wanted.to_lowercase();
    lines.iter().find_map(|line| {
        if line.text.contains(wanted) {
            return Some((wanted.to_owned(), line.start_ms, line.text.as_str()));
        }
        // Same words in another case: keep the transcript's own spelling.
        let at = line.text.to_lowercase().find(&lower)?;
        let found = line.text.get(at..at + wanted.len())?;
        Some((found.to_owned(), line.start_ms, line.text.as_str()))
    })
}

/// Whether an item's quote is backed by the transcript: by the text it was drafted from (which
/// a later chunk may have reworded), or by the transcript as it reads now. Typed items have none.
pub fn quote_supported(item: &Item, record: &Recording) -> bool {
    let quote = item.quote.trim();
    quote.is_empty()
        || (!item.source.is_empty() && item.source.contains(&item.quote))
        || transcript::lines(record)
            .iter()
            .any(|line| line.text.contains(&item.quote))
        || record
            .chunks
            .iter()
            .any(|chunk| chunk.text.contains(&item.quote))
}

/// The lines drafting reads: finished chunks plus final streamed text, and whether the
/// recording has settled (stopped, every chunk transcribed). Until then the newest streamed
/// seconds are left for the next review.
fn review_lines(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> (Vec<Line>, bool) {
    let settled = record.state != "recording"
        && record
            .chunks
            .iter()
            .all(|chunk| !["pending", "running"].contains(&chunk.status.as_str()));
    if settled {
        return (transcript::live_lines(record, streamed), true);
    }
    let horizon = streamed
        .values()
        .flatten()
        .map(|word| word.end_ms)
        .max()
        .unwrap_or(0)
        .saturating_sub(SETTLE_MS);
    let ready: BTreeMap<String, Vec<Word>> = streamed
        .iter()
        .map(|(channel, words)| {
            let words = words.iter().filter(|word| word.end_ms <= horizon).cloned();
            (channel.clone(), words.collect())
        })
        .collect();
    (transcript::live_lines(record, &ready), false)
}

/// Whether there is enough new speech to draft from: 400+ characters reviewed by nobody yet,
/// or any new speech once the recording stopped and transcription settled. Reads streamed text
/// too, so drafts can follow the conversation within seconds instead of waiting for chunks.
pub fn draft_due(store: &Store, id: &str) -> Result<bool> {
    let record = store.get(id)?;
    let after = store.context(id)?.drafted_through_ms;
    let (lines, settled) = review_lines(&record, &store.live_words(id)?);
    let fresh: usize = lines
        .iter()
        .filter(|line| line.end_ms > after)
        .map(|line| line.text.trim().len())
        .sum();
    Ok(fresh >= MIN_NEW_CHARS || (settled && fresh > 0))
}

/// Drafts from speech after `drafted_through_ms`, added to the meeting's list. Returns how many.
pub fn draft(store: &Store, config: &Settings, key: &str, id: &str) -> Result<usize> {
    let record = store.get(id)?;
    let context = store.context(id)?;
    let (lines, _) = review_lines(&record, &store.live_words(id)?);
    let after = context.drafted_through_ms;
    let first = lines.partition_point(|line| line.end_ms <= after);
    let mut fresh = vec![];
    let mut bytes = 0;
    let mut through = after;
    for line in &lines[first..] {
        bytes += line.text.len() + 40;
        if bytes > 40_000 && !fresh.is_empty() {
            break;
        }
        through = through.max(line.end_ms);
        fresh.push(line.clone());
    }
    if fresh.is_empty() {
        return Ok(0);
    }
    let earlier: Vec<Value> = lines[first.saturating_sub(6)..first]
        .iter()
        .map(row)
        .collect();
    let existing: Vec<&str> = context
        .items
        .iter()
        .map(|item| item.title.as_str())
        .collect();
    let input = json!({"EXISTING":existing,"EARLIER":earlier,"NEW":fresh.iter().map(row).collect::<Vec<_>>()});
    let text = complete(
        config,
        key,
        vec![
            json!({"role":"system","content":DRAFT_PROMPT}),
            json!({"role":"user","content":input.to_string()}),
        ],
        true,
    )?;
    let found = object(&text).context("The suggestion model returned something other than JSON")?;
    let mut added = 0;
    store.update_context(id, |current| {
        for row in found["items"]
            .as_array()
            .into_iter()
            .flatten()
            .take(MAX_PER_REVIEW)
        {
            let title: String = row["title"]
                .as_str()
                .unwrap_or("")
                .trim()
                .chars()
                .take(300)
                .collect();
            let Some((quote, at_ms, source)) = locate(&fresh, row["quote"].as_str().unwrap_or(""))
            else {
                continue;
            };
            let duplicate = current
                .items
                .iter()
                .any(|item| item.title.eq_ignore_ascii_case(&title) || item.quote == quote);
            if title.is_empty() || duplicate || current.items.len() >= MAX_ITEMS {
                continue;
            }
            current.items.push(Item {
                id: format!("draft-{}", hash(&json!([id, title, at_ms]))),
                kind: "task".into(),
                title,
                quote,
                at_ms,
                source: source.chars().take(2000).collect(),
                ..Default::default()
            });
            added += 1;
        }
        current.drafted_through_ms = current.drafted_through_ms.max(through);
        Ok(())
    })?;
    Ok(added)
}

/// A private answer about one recording. It never leaves this computer except to the model.
pub fn ask(
    store: &Store,
    config: &Settings,
    key: &str,
    id: &str,
    question: &str,
) -> Result<String> {
    let record = store.get(id)?;
    let context: MeetingContext = store.context(id)?;
    // Newest speech matters most when the whole meeting does not fit.
    let mut rows = vec![];
    let mut bytes = 0;
    for line in transcript::lines(&record).iter().rev() {
        bytes += line.text.len() + 40;
        if bytes > 120_000 {
            break;
        }
        rows.push(format!(
            "[{}] {}: {}",
            clock(line.start_ms),
            line.speaker,
            line.text
        ));
    }
    rows.reverse();
    let mut messages = vec![json!({"role":"system","content":format!(
        "{ASK_PROMPT}\n\nMeeting: {}\n\nMy notes:\n{}\n\nTranscript:\n{}",
        record.title,
        context.notes.chars().take(20_000).collect::<String>(),
        rows.join("\n")
    )})];
    for turn in &context.chat[context.chat.len().saturating_sub(10)..] {
        let role = if turn.role == "you" {
            "user"
        } else {
            "assistant"
        };
        messages
            .push(json!({"role":role,"content":turn.text.chars().take(4000).collect::<String>()}));
    }
    let question = question.trim();
    messages.push(json!({"role":"user","content":question.chars().take(4000).collect::<String>()}));
    let answer = complete(config, key, messages, false)?;
    ensure!(!answer.is_empty(), "The model returned no answer");
    store.update_context(id, |current| {
        let at = chrono::Utc::now().to_rfc3339();
        for (role, text) in [("you", question), ("assistant", answer.as_str())] {
            current.chat.push(Turn {
                role: role.into(),
                text: text.into(),
                via: "text".into(),
                at: at.clone(),
            });
        }
        let extra = current.chat.len().saturating_sub(MAX_TURNS);
        current.chat.drain(..extra);
        Ok(())
    })?;
    Ok(answer)
}

/// What a bot gets with an action item: the item, the words it came from, the talk around them,
/// and where the meeting lives. A private meeting is not readable by bots, so the excerpt matters.
pub fn task_body(record: &Recording, item: &Item, sender: &str, link: &str) -> String {
    let lines = transcript::lines(record);
    let mut parts = vec![item.title.clone()];
    if !item.quote.is_empty() {
        let speaker = lines
            .iter()
            .find(|line| line.text.contains(&item.quote))
            .map_or("", |line| line.speaker.as_str());
        parts.push(format!(
            "> “{}” — {speaker}, {}",
            item.quote,
            clock(item.at_ms)
        ));
    }
    // The two minutes before the item and the half minute after, or the end of the meeting for
    // an item typed by hand.
    let end = lines.last().map_or(0, |line| line.end_ms);
    let at = if item.quote.is_empty() && item.at_ms == 0 {
        end
    } else {
        item.at_ms
    };
    let excerpt: Vec<String> = lines
        .iter()
        .filter(|line| line.end_ms + 120_000 >= at && line.start_ms <= at + 30_000)
        .map(|line| format!("[{}] {}: {}", clock(line.start_ms), line.speaker, line.text))
        .collect();
    let mut excerpt = excerpt.join("\n");
    if excerpt.len() > 6000 {
        let cut = excerpt
            .char_indices()
            .nth(6000)
            .map_or(excerpt.len(), |(at, _)| at);
        excerpt.truncate(cut);
        excerpt.push('…');
    }
    if !excerpt.is_empty() {
        parts.push(format!("Transcript around it:\n{excerpt}"));
    }
    let started = record.started_at.get(..10).unwrap_or("");
    let mut source = format!(
        "From the meeting “{}”, {started}, sent by {sender} from Tico Recorder.",
        record.title
    );
    if !link.is_empty() {
        source.push_str(&format!("\nMeeting: {link}"));
    }
    parts.push(source);
    parts.join("\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(start_ms: u64, speaker: &str, text: &str) -> Line {
        Line {
            speaker: speaker.into(),
            me: speaker == "Me",
            start_ms,
            end_ms: start_ms + 2000,
            text: text.into(),
            chunk_id: "c".into(),
        }
    }

    #[test]
    fn quotes_must_come_from_the_transcript() {
        let lines = [line(
            65_000,
            "Speaker 1",
            "Okay, I'll send Dana the pricing deck tomorrow.",
        )];
        assert_eq!(
            locate(&lines, "“i'll send Dana the pricing deck”"),
            Some((
                "I'll send Dana the pricing deck".into(),
                65_000,
                "Okay, I'll send Dana the pricing deck tomorrow."
            ))
        );
        assert_eq!(locate(&lines, "We will ship on Friday"), None);
        assert_eq!(locate(&lines, "  "), None);
    }

    #[test]
    fn replies_wrapped_in_prose_still_parse() {
        let reply = "Here you go:\n```json\n{\"items\":[{\"title\":\"A\",\"quote\":\"b\"}]}\n```";
        assert_eq!(object(reply).unwrap()["items"][0]["title"], "A");
        assert!(object("no items").is_none());
    }
}
