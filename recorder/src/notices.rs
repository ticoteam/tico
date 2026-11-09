//! What a meeting's bots can't do right now, in plain sentences: a bot that couldn't answer, is
//! waiting for its computer, will answer after its current reply, or is being asked again. Tico
//! says so only in the meeting's events (`meeting.bot_turn_failed`, `_waiting`, `_deferred`,
//! `_retried`); the recorder reads new events while it reads the meeting, keeps one notice per
//! bot and kind, and drops a bot's notices when its next reply arrives.
use crate::network::Tico;
use anyhow::Result;
use serde::Serialize;
use serde_json::Value;
use std::sync::Mutex;

/// How far back a meeting is read the first time: notices older than this many events are stale.
const LOOKBACK: u64 = 200;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Notice {
    pub id: String,
    pub kind: String,
    /// The bot's slug; the window shows its display name.
    pub bot: String,
    pub turn_id: String,
    pub reason: String,
    /// Tico's own sentence, when it gives one.
    pub detail: String,
    pub at: i64,
}

impl Notice {
    /// One plain sentence naming the bot.
    pub fn text(&self, name: &str) -> String {
        match self.kind.as_str() {
            "failed" => match self.detail.split_once(": ") {
                Some((_, cause)) if !cause.trim().is_empty() => {
                    format!("{name} couldn't answer: {}", cause.trim())
                }
                _ => format!("{name} couldn't answer"),
            },
            "waiting" if self.reason == "runner_update" => {
                format!("{name} is waiting for its computer: it needs a Tico update")
            }
            "waiting" => format!("{name} is waiting for its computer"),
            "deferred" if self.reason == "busy" => {
                format!("{name} will answer when it finishes its current work")
            }
            "deferred" => format!("{name} will answer after its current reply"),
            _ => format!("{name} is being asked again"),
        }
    }
}

/// Updates `notices` (newest last) with one meeting event.
pub fn apply(notices: &mut Vec<Notice>, kind: &str, payload: &Value, at: i64) {
    let text = |key: &str| payload[key].as_str().unwrap_or("").to_owned();
    let bot = text("bot");
    match kind {
        "meeting.bot_turn_failed"
        | "meeting.bot_turn_waiting"
        | "meeting.bot_turn_deferred"
        | "meeting.bot_turn_retried" => {
            if bot.is_empty() {
                return;
            }
            let short = kind.trim_start_matches("meeting.bot_turn_");
            notices.retain(|notice| !(notice.bot == bot && notice.kind == short));
            notices.push(Notice {
                id: format!("{short}:{}", text("turn_id")),
                kind: short.into(),
                bot,
                turn_id: text("turn_id"),
                reason: text("reason"),
                detail: text("detail"),
                at,
            });
        }
        // The bot answered, or left: what it couldn't do is no longer news.
        "meeting.bot_reply" => {
            let actor = text("actor");
            if let Some(slug) = actor.strip_prefix("bot:") {
                notices.retain(|notice| notice.bot != slug);
            }
        }
        "meeting.bot_left" => notices.retain(|notice| notice.bot != bot),
        // The turn a notice waits on settled (answered, passed or skipped). A failed turn's own
        // settling keeps its notice; the bot's next reply clears it.
        "meeting.router" | "meeting.bot_turn_skipped" => {
            let turn = text("turn_id");
            if !turn.is_empty() {
                notices.retain(|notice| notice.kind == "failed" || notice.turn_id != turn);
            }
        }
        _ => {}
    }
}

/// Per live meeting: the last event read, and its notices.
static MEETINGS: Mutex<Vec<(String, u64, Vec<Notice>)>> = Mutex::new(Vec::new());

/// The meeting's current notices, newest last.
pub fn notices(live_id: &str) -> Vec<Notice> {
    MEETINGS
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .iter()
        .find(|(id, _, _)| id == live_id)
        .map(|(_, _, notices)| notices.clone())
        .unwrap_or_default()
}

impl Tico {
    /// Reads the meeting's events after the last one read, up to `latest` (the meeting view's
    /// `event_id`), and updates its notices. Nothing is asked when nothing new happened.
    pub fn read_notices(&self, live_id: &str, latest: u64) -> Result<()> {
        let after = {
            let meetings = MEETINGS.lock().unwrap_or_else(|error| error.into_inner());
            meetings
                .iter()
                .find(|(id, _, _)| id == live_id)
                .map(|(_, after, _)| *after)
        };
        let after = after.unwrap_or_else(|| latest.saturating_sub(LOOKBACK));
        if after >= latest {
            return Ok(());
        }
        let events = self.events(&format!(
            "/api/v2/live-meetings/{live_id}/events?after={after}"
        ))?;
        let now = chrono::Utc::now().timestamp_millis();
        let mut meetings = MEETINGS.lock().unwrap_or_else(|error| error.into_inner());
        if !meetings.iter().any(|(id, _, _)| id == live_id) {
            meetings.push((live_id.into(), after, vec![]));
        }
        let (_, cursor, notices) = meetings
            .iter_mut()
            .find(|(id, _, _)| id == live_id)
            .expect("meeting notices");
        for (id, kind, payload) in events {
            if id > *cursor {
                *cursor = id;
                apply(notices, &kind, &payload, now);
            }
        }
        Ok(())
    }
}

/// Remembers a meeting's notices as read (for fixtures standing in for a Tico).
pub fn remember(live_id: &str, after: u64, notices: Vec<Notice>) {
    let mut meetings = MEETINGS.lock().unwrap_or_else(|error| error.into_inner());
    meetings.retain(|(id, _, _)| id != live_id);
    meetings.push((live_id.into(), after, notices));
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn shown(notices: &[Notice]) -> Vec<(String, String)> {
        notices
            .iter()
            .map(|notice| (notice.id.clone(), notice.text("Falcon")))
            .collect()
    }
    #[test]
    fn bot_turn_events_become_plain_notices_until_the_bot_answers() {
        let mut notices = vec![];
        apply(
            &mut notices,
            "meeting.bot_turn_failed",
            &json!({"turn_id":"t1","bot":"falcon","reason":"private_checkout",
            "detail":"Falcon can't answer in this private meeting: Studio Mac couldn't make a private copy of its repository"}),
            1,
        );
        apply(
            &mut notices,
            "meeting.bot_turn_waiting",
            &json!({"turn_id":"t2","bot":"falcon","reason":"runner_update",
            "detail":"Studio Mac needs a Tico update before it can join private meetings"}),
            2,
        );
        apply(
            &mut notices,
            "meeting.bot_turn_deferred",
            &json!({"turn_id":"t3","bot":"falcon","reason":"replying_in_meeting","behind":"t2","source_key":"chat:c1"}),
            3,
        );
        apply(
            &mut notices,
            "meeting.bot_turn_retried",
            &json!({"turn_id":"t4","bot":"falcon","reason":"empty_bot_reply","retry":1,"model":null}),
            4,
        );
        apply(
            &mut notices,
            "meeting.chunk",
            &json!({"seq":1,"text":"hello"}),
            5,
        );
        assert_eq!(
            shown(&notices),
            [
                ("failed:t1".into(), "Falcon couldn't answer: Studio Mac couldn't make a private copy of its repository".into()),
                ("waiting:t2".into(), "Falcon is waiting for its computer: it needs a Tico update".into()),
                ("deferred:t3".into(), "Falcon will answer after its current reply".into()),
                ("retried:t4".into(), "Falcon is being asked again".into()),
            ]
        );
        assert_eq!(notices[2].at, 3);
        // A second retry of the same bot replaces the first; newest last.
        apply(
            &mut notices,
            "meeting.bot_turn_retried",
            &json!({"turn_id":"t5","bot":"falcon","retry":2}),
            6,
        );
        assert_eq!(notices.last().unwrap().id, "retried:t5");
        assert_eq!(notices.len(), 4);
        // The deferred turn settled: its notice goes; the failed one stays until a reply.
        apply(
            &mut notices,
            "meeting.router",
            &json!({"turn_id":"t3","bot":"falcon","outcome":"reply"}),
            7,
        );
        apply(
            &mut notices,
            "meeting.router",
            &json!({"turn_id":"t1","bot":"falcon","outcome":"pass"}),
            7,
        );
        assert_eq!(
            notices
                .iter()
                .map(|notice| notice.kind.as_str())
                .collect::<Vec<_>>(),
            ["failed", "waiting", "retried"]
        );
        // Another bot's reply leaves Falcon's notices; Falcon's own reply clears them.
        apply(
            &mut notices,
            "meeting.bot_turn_deferred",
            &json!({"turn_id":"t6","bot":"owl","reason":"busy"}),
            8,
        );
        apply(
            &mut notices,
            "meeting.bot_reply",
            &json!({"id":"c2","actor":"bot:owl","text":"Done","turn_id":"t9"}),
            9,
        );
        assert!(notices.iter().all(|notice| notice.bot == "falcon"));
        apply(
            &mut notices,
            "meeting.bot_reply",
            &json!({"id":"c3","actor":"bot:falcon","text":"Here","turn_id":"t5"}),
            10,
        );
        assert!(notices.is_empty());
        // Plain words without Tico's detail.
        let busy = Notice {
            id: "deferred:t".into(),
            kind: "deferred".into(),
            bot: "owl".into(),
            turn_id: "t".into(),
            reason: "busy".into(),
            detail: String::new(),
            at: 0,
        };
        assert_eq!(
            busy.text("Owl"),
            "Owl will answer when it finishes its current work"
        );
        let failed = Notice {
            kind: "failed".into(),
            ..busy.clone()
        };
        assert_eq!(failed.text("Owl"), "Owl couldn't answer");
        let waiting = Notice {
            kind: "waiting".into(),
            reason: String::new(),
            ..busy
        };
        assert_eq!(waiting.text("Owl"), "Owl is waiting for its computer");
    }
}
