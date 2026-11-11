//! `tico-recorder://record?server=<org address>&…` links: Tico asks the Recorder to record a call the person is
//! joining. Two kinds share the one action:
//!
//! - `call=<call id>`: a Tico call (a Google Meet started in a thread, `docs/dev/meet-calls-contract.md`), recorded
//!   into the call's own live meeting.
//! - `title=<event title>[&event=<occurrence id>]`: Join on a calendar event (`docs/dev/calendar-contract.md`). The
//!   Recorder starts a live meeting named `title` and, with `event`, links it to that occurrence
//!   (`calendar_event` on `POST /api/v2/live-meetings`).
//!
//! A link with `call` is a call link whatever else it carries.
use tico_recorder_native::settings;

/// A live meeting's name from a link is cut to this many characters (what `POST /live-meetings` takes).
pub const TITLE_MAX: usize = 200;
/// An occurrence id longer than this is not one, so it is left out.
pub const EVENT_MAX: usize = 300;

/// What a `record` link asks for. `server` is an org address the Recorder accepts (`settings::safe_url`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RecordLink {
    pub server: String,
    /// A Tico call's id, a uuid in its canonical form.
    pub call: Option<String>,
    /// The calendar event's title, trimmed, without control characters, at most `TITLE_MAX` characters.
    pub title: Option<String>,
    /// The occurrence joined: printable ASCII without spaces, at most `EVENT_MAX` characters. Left out when it is
    /// anything else; the call is still recorded, just not linked.
    pub event: Option<String>,
}

impl RecordLink {
    /// Reads a `tico-recorder://record` link. Refused (`None`): another scheme or action, no safe `server`, a `call`
    /// that is not a uuid, or neither a `call` nor a non-blank `title`.
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
        let title: String = param("title")?
            .chars()
            .map(|c| if c.is_control() { ' ' } else { c })
            .collect::<String>()
            .trim()
            .chars()
            .take(TITLE_MAX)
            .collect();
        let title = title.trim_end().to_owned();
        if title.is_empty() {
            return None;
        }
        let event = param("event").filter(|event| {
            !event.is_empty()
                && event.len() <= EVENT_MAX
                && event.chars().all(|c| c.is_ascii_graphic())
        });
        Some(Self {
            server,
            call: None,
            title: Some(title),
            event,
        })
    }
}
