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
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

/// Most items found per review, so one long stretch of speech cannot flood the list.
const MAX_PER_REVIEW: usize = 5;
/// New speech that justifies a review while recording.
const MIN_NEW_CHARS: usize = 400;
/// While recording, the newest streamed seconds wait for the next review: the other channel's
/// final text for the same moment can still be on its way.
const SETTLE_MS: u64 = 5_000;
const MAX_PEOPLE: usize = 80;
const MAX_PEOPLE_BYTES: usize = 24_000;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Person {
    pub actor: String,
    pub name: String,
    pub job: String,
}

/// A snapshot of the meeting's Tico, never another window's current org.
#[derive(Clone, Debug, Default)]
pub struct Roster {
    pub people: Vec<Person>,
    pub me: String,
    pub participants: Vec<String>,
}

fn people(roster: &Roster, context: &MeetingContext, lines: &[Line]) -> Vec<Value> {
    let participants: Vec<&str> = roster
        .participants
        .iter()
        .chain(&context.participants)
        .map(String::as_str)
        .chain(
            lines
                .iter()
                .filter(|line| !line.me)
                .map(|line| line.speaker.as_str()),
        )
        .collect();
    let mut ordered: Vec<_> = roster.people.iter().collect();
    ordered.sort_by_key(|person| {
        if person.actor == roster.me {
            (0, 0)
        } else if participants
            .iter()
            .any(|name| **name == person.actor || name.eq_ignore_ascii_case(&person.name))
        {
            (1, 0)
        } else if let Some(at) = context
            .items
            .iter()
            .rev()
            .position(|i| i.owner == person.actor)
        {
            (2, at)
        } else {
            (3, 0)
        }
    });
    let mut seen = BTreeSet::new();
    let mut result = vec![];
    let mut bytes = 2;
    for person in ordered {
        let actor = &person.actor;
        if actor.len() > 200
            || !["human:", "bot:"].iter().any(|prefix| {
                actor
                    .strip_prefix(*prefix)
                    .is_some_and(|id| !id.is_empty() && id.trim() == id)
            })
            || !seen.insert(actor)
        {
            continue;
        }
        let row = json!({
            "actor":actor, "name":person.name.chars().take(160).collect::<String>(),
            "bot":actor.starts_with("bot:"),
            "job":person.job.chars().take(300).collect::<String>()
        });
        let size = row.to_string().len() + 1;
        if bytes + size > MAX_PEOPLE_BYTES {
            continue;
        }
        bytes += size;
        result.push(row);
        if result.len() == MAX_PEOPLE {
            break;
        }
    }
    result
}

fn me<'a>(people: &'a [Value], actor: &str) -> Option<&'a Value> {
    people
        .iter()
        .find(|person| person["actor"] == actor && actor.starts_with("human:"))
}

fn owner(row: &Value, people: &[Value]) -> String {
    row["owner"]
        .as_str()
        .filter(|actor| people.iter().any(|person| person["actor"] == *actor))
        .unwrap_or("")
        .to_owned()
}

const DRAFT_PROMPT: &str = "You find action items in a meeting transcript while the meeting is \
happening. Keep concrete commitments, requests and agreed changes, even small ones. Suggest \
each distinct piece of real work; more of these is fine. Read the NEW lines (EARLIER \
lines are context only). Return JSON only: {\"items\":[{\"title\":\"...\",\"quote\":\"...\",\"owner\":\"...\"}]}. \
title: a short imperative sentence naming the actual deliverable or outcome and, when said, \
who it is for or by when (\"Send Dana the revised pricing by Friday\"). A ping, message, \
reminder or follow-up with stated work becomes that underlying work: \
\"I'll ping Eli to get his sign-off on the pricing doc\" becomes \
\"Get Eli's sign-off on the pricing doc\", still owned by the speaker. \
Do not invent a purpose when none is stated. Drop bare communication steps: ping, message, \
reach out, let someone know, remind, follow up or check in with no stated work. \
\"Let's sync later\" alone is not an item. Sending a concrete deliverable is real work. \
quote: the exact words, copied character for \
character from one NEW line, that the item comes from (at most 25 words). \
Apply the ownership rules below only to retained work; rewriting a title never transfers \
ownership to the person being contacted. Bare-ping ownership examples do not make them items. \
PEOPLE lists the org's people and bots (with jobs when known). ME identifies the signed-in \
person; transcript speaker Me means ME. Keep named speakers and unknown Speaker N distinct. \
speaker_actor identifies a speaker only when their label uniquely matches PEOPLE, or they are Me. \
When speaker_actor is nonempty, that actor owns their 'I will' / 'I'll' commitments. \
owner: the exact actor from PEOPLE of who should DO the task, or \"\" if unclear. \
The person committing owns it: Me saying 'I'll ping Dana' means ME owns the ping, not Dana. \
A named speaker saying 'I'll send the reviewed PR back to Eli' owns sending it, not Eli. \
A direct request 'Eli, review it again' or 'Have Eli review the code again' belongs to Eli. \
For work assigned to a team or role, use its bot or explicitly identified team lead in PEOPLE \
when clear. 'Open a ticket to concierge to negotiate quotes' and 'Let's open a ticket to concierge \
to negotiate quotes' route work to concierge: owner is its bot, or its identified team lead \
when no bot is listed. 'Let's' does not assign that role's work to Me. In contrast, an explicit \
'I'll open the ticket to concierge' commits the speaker to opening it, so the speaker owns it. \
People being pinged or receiving a document are not its owners. Use surrounding lines to \
resolve 'you' only when clear. Never invent an actor or infer who Speaker N is. If several \
people could own it, the name is ambiguous, or the doer is absent from PEOPLE, use \"\". \
Leave out small talk, vague ideas, questions nobody took on, things finished during the meeting, \
and steps that will obviously happen in the next few minutes of this meeting, such as sharing \
a screen or opening the agenda. Drop restatements of EXISTING items, even reworded or framed \
as a ping about the same work. TASKS lists active tasks. If new speech belongs to one, return \
{\"link\":N,\"quote\":\"...\",\"owner\":\"...\"} instead of a title. N must be in TASKS. Return {\"items\":[]} when there are none.";

const ASK_PROMPT: &str = "You answer the user's private questions about their meeting, using \
only its transcript and their notes. \"Me\" in the transcript is the user. Answer in a few \
plain sentences or a short list. Give times like (12:04) when they help. If the transcript does \
not say, say so.";

fn clock(ms: u64) -> String {
    let seconds = ms / 1000;
    format!("{:02}:{:02}", seconds / 60, seconds % 60)
}
fn row(line: &Line, people: &[Value], me_actor: &str) -> Value {
    let actor = if line.me {
        me(people, me_actor).and_then(|person| person["actor"].as_str())
    } else if line.speaker == "Me"
        || line
            .speaker
            .strip_prefix("Speaker ")
            .is_some_and(|n| n.parse::<u32>().is_ok())
    {
        None
    } else {
        let mut matches = people.iter().filter(|person| {
            person["name"]
                .as_str()
                .is_some_and(|name| name.eq_ignore_ascii_case(line.speaker.trim()))
        });
        let first = matches.next();
        first
            .filter(|_| matches.next().is_none())
            .and_then(|person| person["actor"].as_str())
    };
    json!({"t":clock(line.start_ms),"speaker":line.speaker,"speaker_actor":actor.unwrap_or(""),"text":line.text})
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
    draft_with_tasks(store, config, key, id, &[])
}

pub fn draft_with_tasks(
    store: &Store,
    config: &Settings,
    key: &str,
    id: &str,
    tasks: &[crate::task_links::Task],
) -> Result<usize> {
    draft_with_roster(store, config, key, id, tasks, &Roster::default())
}

pub fn draft_with_roster(
    store: &Store,
    config: &Settings,
    key: &str,
    id: &str,
    tasks: &[crate::task_links::Task],
    roster: &Roster,
) -> Result<usize> {
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
    let people = people(roster, &context, &lines);
    let earlier: Vec<Value> = lines[first.saturating_sub(6)..first]
        .iter()
        .map(|line| row(line, &people, &roster.me))
        .collect();
    let existing: Vec<&str> = context
        .items
        .iter()
        .map(|item| item.title.as_str())
        .collect();
    let candidates: Vec<_> = tasks
        .iter()
        .filter(|task| task.number.is_some())
        .take(30)
        .collect();
    let offered: Vec<Value> = candidates
        .iter()
        .map(|task| json!({"number":task.number,"title":task.title}))
        .collect();
    let input = json!({"PEOPLE":people,"ME":me(&people, &roster.me),"TASKS":offered,"EXISTING":existing,"EARLIER":earlier,"NEW":fresh.iter().map(|line| row(line, &people, &roster.me)).collect::<Vec<_>>()});
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
            let matched = row["link"].as_u64().and_then(|number| {
                candidates
                    .iter()
                    .copied()
                    .find(|task| task.number == Some(number))
            });
            if row.get("link").is_some() && matched.is_none() {
                continue;
            }
            let mut title: String = row["title"]
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
            if matched.is_some() && title.is_empty() {
                title = quote.clone();
            }
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
                proposed_task_id: if current.kept_on_mac {
                    String::new()
                } else {
                    matched.map(|task| task.id.clone()).unwrap_or_default()
                },
                task_destination: context.task_candidates_destination.clone(),
                owner: owner(row, &people),
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
    if !item.body.trim().is_empty() {
        parts.push(item.body.trim().to_owned());
    }
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

    fn person(actor: &str, name: &str) -> Person {
        Person {
            actor: actor.into(),
            name: name.into(),
            ..Default::default()
        }
    }

    #[test]
    fn people_prefer_me_participants_and_recent_owners_and_bound_untrusted_fields() {
        let mut roster = Roster {
            people: (0..100)
                .map(|i| person(&format!("human:{i}"), &format!("Person {i}")))
                .collect(),
            me: "human:99".into(),
            participants: vec!["human:98".into()],
        };
        roster.people.extend([
            person("human:99", "Duplicate"),
            person("outside:unlisted", "Outside"),
            person("human:", "Empty"),
            person("bot:concierge", "Concierge"),
        ]);
        roster.people[0].name = "長".repeat(1000);
        roster.people[0].job = "職".repeat(1000);
        let context = MeetingContext {
            items: vec![Item {
                owner: "human:97".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let rows = people(&roster, &context, &[line(0, "Concierge", "Okay")]);
        assert_eq!(rows[0]["actor"], "human:99");
        assert_eq!(rows[1]["actor"], "human:98");
        assert_eq!(rows[2]["actor"], "bot:concierge");
        assert_eq!(rows[2]["bot"], true);
        assert_eq!(rows[3]["actor"], "human:97");
        assert_eq!(rows.len(), MAX_PEOPLE);
        assert!(serde_json::to_vec(&rows).unwrap().len() <= MAX_PEOPLE_BYTES);
        assert_eq!(rows[4]["name"].as_str().unwrap().chars().count(), 160);
        assert_eq!(rows[4]["job"].as_str().unwrap().chars().count(), 300);
        assert!(
            !rows
                .iter()
                .any(|p| p["name"] == "Duplicate" || p["name"] == "Outside")
        );
        // Multibyte names and jobs hit the byte limit before the entry limit.
        for p in &mut roster.people {
            p.name = "長".repeat(160);
            p.job = "職".repeat(300);
        }
        let rows = people(&roster, &context, &[]);
        assert!(rows.len() < MAX_PEOPLE);
        assert!(serde_json::to_vec(&rows).unwrap().len() <= MAX_PEOPLE_BYTES);
    }

    #[test]
    fn me_and_owner_ids_must_be_in_the_offered_people() {
        let roster = Roster {
            people: vec![person("human:ana", "Ana"), person("bot:ops", "Ops")],
            me: "human:ana".into(),
            ..Default::default()
        };
        let rows = people(&roster, &MeetingContext::default(), &[]);
        assert_eq!(me(&rows, &roster.me).unwrap()["name"], "Ana");
        assert!(me(&rows, "human:missing").is_none());
        assert!(me(&rows, "bot:ops").is_none());
        assert_eq!(owner(&json!({"owner":"human:ana"}), &rows), "human:ana");
        assert_eq!(owner(&json!({"owner":"bot:ops"}), &rows), "bot:ops");
        for row in [
            json!({}),
            json!({"owner":null}),
            json!({"owner":12}),
            json!({"owner":"Ana"}),
            json!({"owner":"human:missing"}),
            json!({"owner":" human:ana "}),
        ] {
            assert_eq!(owner(&row, &rows), "");
        }
        assert_eq!(owner(&json!({"owner":"human:ana"}), &[]), "");
    }

    #[test]
    fn speaker_actors_require_a_unique_roster_match_or_the_signed_in_me() {
        let rows = vec![
            json!({"actor":"human:ana", "name":"Ana Rivera"}),
            json!({"actor":"human:eli", "name":"Eli Park"}),
            json!({"actor":"human:same1", "name":"Same Name"}),
            json!({"actor":"human:same2", "name":"Same Name"}),
            json!({"actor":"human:unknown", "name":"Speaker 1"}),
        ];
        for (speaker, expected) in [
            ("Me", "human:ana"),
            ("Eli Park", "human:eli"),
            ("Same Name", ""),
            ("Speaker 1", ""),
            ("Eli", ""),
            ("Outside", ""),
        ] {
            assert_eq!(
                row(&line(0, speaker, "I'll send it"), &rows, "human:ana")["speaker_actor"],
                expected
            );
        }
        assert_eq!(
            row(&line(0, "Me", "I'll send it"), &rows, "human:missing")["speaker_actor"],
            ""
        );
    }

    fn fixture_lines(store: &Store, lines: &[Value]) -> String {
        let record = store.create("Acme action items").unwrap();
        for (i, row) in lines.iter().enumerate() {
            let speaker = row["speaker"].as_str().unwrap();
            let at = i as u64 * 10_000;
            store
                .replace_live_words(
                    &record.id,
                    if speaker == "Me" { "me" } else { "them" },
                    at,
                    at + 2000,
                    &[Word {
                        start_ms: at,
                        end_ms: at + 2000,
                        text: row["text"].as_str().unwrap().into(),
                        speaker: speaker.into(),
                        ..Default::default()
                    }],
                )
                .unwrap();
        }
        record.id
    }

    #[test]
    fn drafted_items_keep_validated_owners_and_the_original_speaker_labels() {
        use std::{
            io::{Read, Write},
            net::TcpListener,
            time::Duration,
        };
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let config = Settings {
            transcription_url: format!("http://{}", listener.local_addr().unwrap()),
            ..Default::default()
        };
        let worker = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut headers = vec![];
            while !headers.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                stream.read_exact(&mut byte).unwrap();
                headers.push(byte[0]);
            }
            let headers = String::from_utf8(headers).unwrap();
            let length = headers
                .lines()
                .find_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    key.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
                .unwrap();
            let mut body = vec![0; length];
            stream.read_exact(&mut body).unwrap();
            let request: Value = serde_json::from_slice(&body).unwrap();
            let input: Value =
                serde_json::from_str(request["messages"][1]["content"].as_str().unwrap()).unwrap();
            assert_eq!(input["ME"]["actor"], "human:ana");
            assert_eq!(input["ME"]["name"], "Ana");
            assert_eq!(input["NEW"][0]["speaker"], "Me");
            assert_eq!(input["NEW"][0]["speaker_actor"], "human:ana");
            assert_eq!(input["NEW"][1]["speaker"], "Eli");
            assert_eq!(input["NEW"][1]["speaker_actor"], "human:eli");
            assert_eq!(input["NEW"][2]["speaker"], "Speaker 1");
            assert_eq!(input["NEW"][2]["speaker_actor"], "");
            let reply = json!({"choices":[{"message":{"content":json!({"items":[
                {"title":"Ping Dana", "quote":"I'll ping Dana", "owner":"human:ana"},
                {"title":"Review code", "quote":"I will review it", "owner":"human:eli"},
                {"title":"Negotiate quotes", "quote":"Concierge will negotiate quotes", "owner":"bot:concierge"},
                {"title":"Send prices", "quote":"Send prices tomorrow", "owner":"human:invented"}
            ]}).to_string()}}]}).to_string();
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}", reply.len()).unwrap();
        });
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let id = fixture_lines(
            &store,
            &[
                json!({"speaker":"Me","text":"I'll ping Dana"}),
                json!({"speaker":"Eli","text":"I will review it"}),
                json!({"speaker":"Speaker 1","text":"Concierge will negotiate quotes"}),
                json!({"speaker":"Me","text":"Send prices tomorrow"}),
            ],
        );
        let roster = Roster {
            people: vec![
                person("human:ana", "Ana"),
                person("human:eli", "Eli"),
                person("bot:concierge", "Concierge"),
            ],
            me: "human:ana".into(),
            ..Default::default()
        };
        assert_eq!(
            draft_with_roster(&store, &config, "test", &id, &[], &roster).unwrap(),
            4
        );
        worker.join().unwrap();
        let mut context = store.context(&id).unwrap();
        assert_eq!(
            context
                .items
                .iter()
                .map(|i| i.owner.as_str())
                .collect::<Vec<_>>(),
            ["human:ana", "human:eli", "bot:concierge", ""]
        );
        context.items[0].owner = "human:eli".into();
        store.save_context(&id, &context).unwrap();
        assert_eq!(store.context(&id).unwrap().items[0].owner, "human:eli");
    }

    fn eval_title(item: &Item, expected: &Value, banned: &[Value]) -> bool {
        let title = item.title.trim().to_lowercase();
        !banned.iter().any(|prefix| {
            title
                .strip_prefix(&prefix.as_str().unwrap().to_lowercase())
                .is_some_and(|rest| {
                    rest.is_empty() || rest.starts_with(|c: char| !c.is_alphanumeric())
                })
        }) && expected["title_keywords"]
            .as_array()
            .unwrap()
            .iter()
            .all(|group| {
                group
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|word| title.contains(&word.as_str().unwrap().to_lowercase()))
            })
    }

    // Match without relying on model output order, including when several owners are the same.
    fn eval_items(
        items: &[Item],
        expected: &[Value],
        banned: &[Value],
        titles: bool,
        owners: bool,
    ) -> bool {
        if items.len() != expected.len() {
            return false;
        }
        let Some((first, rest)) = expected.split_first() else {
            return true;
        };
        items.iter().enumerate().any(|(at, item)| {
            (!titles || eval_title(item, first, banned))
                && (!owners || item.owner == first["owner"].as_str().unwrap())
                && eval_items(
                    &items
                        .iter()
                        .enumerate()
                        .filter(|(i, _)| *i != at)
                        .map(|(_, item)| item.clone())
                        .collect::<Vec<_>>(),
                    rest,
                    banned,
                    titles,
                    owners,
                )
        })
    }

    #[test]
    fn eval_checks_work_titles_and_owners_together_without_output_order() {
        let banned = vec![json!("Ping"), json!("Remind")];
        let expected = vec![
            json!({"owner":"human:ana", "title_keywords":[["pricing"],["sign-off","approval"]]}),
            json!({"owner":"human:dana", "title_keywords":[["invoice"],["tax"]]}),
        ];
        let mut items = vec![
            Item {
                title: "Correct the invoice tax total".into(),
                owner: "human:dana".into(),
                ..Default::default()
            },
            Item {
                title: "Get approval on pricing".into(),
                owner: "human:ana".into(),
                ..Default::default()
            },
        ];
        assert!(eval_items(&items, &expected, &banned, true, true));
        items[1].title = "Ping Eli for pricing approval".into();
        assert!(!eval_items(&items, &expected, &banned, true, true));
        items[1].title = "Get pricing".into();
        assert!(!eval_items(&items, &expected, &banned, true, true));
        items[1].title = "Get pricing sign-off".into();
        items.swap(0, 1);
        items[0].owner = "human:dana".into();
        items[1].owner = "human:ana".into();
        assert!(eval_items(&items, &expected, &banned, false, true));
        assert!(eval_items(&items, &expected, &banned, true, false));
        assert!(!eval_items(&items, &expected, &banned, true, true));
        assert!(!eval_items(&items, &[], &banned, true, true));
        assert!(!eval_items(&[], &expected, &banned, true, true));
        assert!(eval_items(&[], &[], &banned, true, true));
    }

    /// Paid, opt-in eval through the same request, validation and persistence as automatic drafting.
    #[test]
    #[ignore = "needs a real model key; see bench/README.md"]
    fn owner_eval() {
        let key = std::env::var("TICO_RECORDER_ASSIST_KEY").expect("Set TICO_RECORDER_ASSIST_KEY");
        let config = Settings {
            transcription_url: std::env::var("TICO_RECORDER_ASSIST_URL")
                .unwrap_or_else(|_| Settings::default().transcription_url),
            assist_model: std::env::var("TICO_RECORDER_ASSIST_MODEL")
                .unwrap_or_else(|_| settings::ASSIST_MODEL.into()),
            ..Default::default()
        };
        let repeats: usize = std::env::var("TICO_RECORDER_ASSIST_REPEATS")
            .unwrap_or_else(|_| "3".into())
            .parse()
            .unwrap();
        assert!(repeats >= 3);
        let fixture: Value =
            serde_json::from_str(include_str!("../bench/assist_cases.json")).unwrap();
        let people: Vec<Person> = serde_json::from_value(fixture["people"].clone()).unwrap();
        let banned = fixture["banned_title_starts"].as_array().unwrap();
        let mut correct = 0;
        let mut owners_correct = 0;
        let mut titles_correct = 0;
        let mut total = 0;
        for case in fixture["cases"].as_array().unwrap() {
            let expected = case["expected"].as_array().unwrap();
            let mut case_correct = 0;
            let mut case_owners = 0;
            let mut case_titles = 0;
            for _ in 0..repeats {
                let dir = tempfile::tempdir().unwrap();
                let store = Store::open(dir.path()).unwrap();
                let id = fixture_lines(&store, case["lines"].as_array().unwrap());
                let mut context = store.context(&id).unwrap();
                if let Some(existing) = case["existing"].as_array() {
                    for title in existing {
                        context.items.push(Item {
                            id: uuid::Uuid::new_v4().to_string(),
                            kind: "task".into(),
                            title: title.as_str().unwrap().into(),
                            ..Default::default()
                        });
                    }
                }
                let existing = context.items.len();
                store.save_context(&id, &context).unwrap();
                let roster = Roster {
                    people: people
                        .iter()
                        .filter(|p| {
                            case["omit"]
                                .as_array()
                                .is_none_or(|omit| !omit.iter().any(|id| id == &p.actor))
                        })
                        .cloned()
                        .collect(),
                    me: fixture["me"].as_str().unwrap().into(),
                    ..Default::default()
                };
                if let Err(error) = draft_with_roster(&store, &config, &key, &id, &[], &roster) {
                    println!(
                        "{}",
                        json!({"case":case["id"],"error":format!("{error:#}")})
                    );
                    continue;
                }
                let items = store.context(&id).unwrap().items[existing..].to_vec();
                let owners = eval_items(&items, expected, banned, false, true);
                let titles = eval_items(&items, expected, banned, true, false);
                let passed = eval_items(&items, expected, banned, true, true);
                if !passed {
                    println!(
                        "{}",
                        json!({"case":case["id"],"expected":expected,"items":items})
                    );
                }
                case_correct += usize::from(passed);
                case_owners += usize::from(owners);
                case_titles += usize::from(titles);
            }
            total += repeats;
            correct += case_correct;
            owners_correct += case_owners;
            titles_correct += case_titles;
            println!(
                "{}",
                json!({"case":case["id"],"correct":case_correct,"owners_correct":case_owners,"titles_correct":case_titles,"total":repeats})
            );
        }
        println!(
            "{}",
            json!({"requested_model":config.assist_model,"correct":correct,"owners_correct":owners_correct,"titles_correct":titles_correct,"total":total,"accuracy":correct as f64 / total as f64,"owner_accuracy":owners_correct as f64 / total as f64,"title_accuracy":titles_correct as f64 / total as f64})
        );
        assert_eq!(
            correct, total,
            "Draft eval missed cases; inspect the receipt above"
        );
    }

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
