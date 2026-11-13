//! `tico-recorder://record?server=<org address>&…` links: Tico asks the Recorder to record a call the person is
//! joining. Two kinds share the one action:
//!
//! - `call=<call id>`: a Tico call (a Google Meet started in a thread, `docs/dev/meet-calls-contract.md`), recorded
//!   into the call's own live meeting.
//! - `event=<occurrence id>[&title=<event title>]`: Join on a calendar event (`docs/dev/calendar-contract.md`). Any
//!   web page can open a link, so the link alone starts nothing: the Recorder reads the occurrence from its org
//!   (`GET /api/v2/calendar/events/{occurrence id}`, as the signed-in person) and records only an event that person
//!   can join and that is on now (`joinable`), named with the org's title, linked to the occurrence (`calendar_event`
//!   on `POST /api/v2/live-meetings`). The link's `title` only names the request while it waits for a sign-in.
//!
//! A link with `call` is a call link whatever else it carries; the server checks a call id the same way.
use serde_json::Value;
use tico_recorder_native::settings;

/// A live meeting's name from a link is cut to this many characters (what `POST /live-meetings` takes).
pub const TITLE_MAX: usize = 200;
/// An occurrence id longer than this is not one.
pub const EVENT_MAX: usize = 300;
/// A Join this long before the event starts still records it.
pub const JOIN_EARLY_MINUTES: i64 = 15;

/// What a `record` link asks for. `server` is an org address the Recorder accepts (`settings::safe_url`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RecordLink {
    pub server: String,
    /// A Tico call's id, a uuid in its canonical form.
    pub call: Option<String>,
    /// The calendar event's title as the link gives it (trimmed, without control characters, at most `TITLE_MAX`
    /// characters): only for showing the request; the recording takes the org's title.
    pub title: Option<String>,
    /// The occurrence joined: printable ASCII without spaces, at most `EVENT_MAX` characters. Required without a
    /// `call`.
    pub event: Option<String>,
}

impl RecordLink {
    /// Reads a `tico-recorder://record` link. Refused (`None`): another scheme or action, no safe `server`, a `call`
    /// that is not a uuid, or no `call` and no valid `event`.
    pub fn parse(link: &str) -> Option<Self> {
        let url = reqwest::Url::parse(link).ok()?;
        if url.scheme() != "tico-recorder" || url.host_str() != Some("record") {
            return None;
        }
        let param = |name: &str| {
            url.query_pairs()
                .find(|(key, _)| key == name)
                .map(|(_, value)| value.into_owned())
        };
        let server = settings::safe_url(&param("server")?).ok()?;
        if let Some(call) = param("call") {
            let call = uuid::Uuid::parse_str(&call).ok()?.to_string();
            return Some(Self {
                server,
                call: Some(call),
                ..Self::default()
            });
        }
        let event = param("event").filter(|event| {
            !event.is_empty()
                && event.len() <= EVENT_MAX
                && event.chars().all(|c| c.is_ascii_graphic())
        })?;
        let title: String = param("title")
            .unwrap_or_default()
            .chars()
            .map(|c| if c.is_control() { ' ' } else { c })
            .collect::<String>()
            .trim()
            .chars()
            .take(TITLE_MAX)
            .collect();
        let title = title.trim_end().to_owned();
        Some(Self {
            server,
            call: None,
            title: (!title.is_empty()).then_some(title),
            event: Some(event),
        })
    }
}

/// The path that reads one occurrence (`GET /api/v2/calendar/events/{occurrence id}`), the id percent-encoded where
/// a path needs it (a Google calendar id may hold `#`).
pub fn occurrence_path(event: &str) -> String {
    let mut path = String::from("/api/v2/calendar/events/");
    for byte in event.bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~:@!$&'()*+,;=".contains(&byte) {
            path.push(byte as char);
        } else {
            path.push_str(&format!("%{byte:02X}"));
        }
    }
    path
}

/// Whether the occurrence the org returned may be recorded from a Join link now: the signed-in person can join it
/// (`can.join`), and `now` is from `JOIN_EARLY_MINUTES` before its start to its end. Answers the title to record
/// under (the org's, cut to `TITLE_MAX`), or why not.
pub fn joinable(occurrence: &Value, now: chrono::DateTime<chrono::Utc>) -> Result<String, String> {
    if occurrence["can"]["join"] != true {
        return Err("the event can't be joined by this person".into());
    }
    let at = |field: &str| {
        occurrence[field]
            .as_str()
            .and_then(|text| chrono::DateTime::parse_from_rfc3339(text).ok())
            .map(|at| at.with_timezone(&chrono::Utc))
    };
    let (Some(start), Some(end)) = (at("start"), at("end")) else {
        return Err("the event has no start and end time".into());
    };
    if now < start - chrono::Duration::minutes(JOIN_EARLY_MINUTES) {
        return Err("the event has not started yet".into());
    }
    if now > end {
        return Err("the event is over".into());
    }
    let title: String = occurrence["title"]
        .as_str()
        .unwrap_or_default()
        .trim()
        .chars()
        .take(TITLE_MAX)
        .collect();
    if title.is_empty() {
        return Err("the event has no title".into());
    }
    Ok(title)
}
