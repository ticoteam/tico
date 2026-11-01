use crate::store::Store;
use anyhow::{Result, ensure};
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Item {
    pub id: String,
    pub kind: String,
    pub title: String,
    pub quote: String,
    pub at_ms: u64,
    pub owner: String,
    pub document: String,
    pub change: String,
    pub selected: bool,
    /// Set once the person sent this item to a bot or person as a Tico task.
    pub task_id: String,
    pub task_url: String,
    /// Who it went to, by name, as shown after sending.
    pub sent_to: String,
    /// The transcript line the item was drafted from, as it read then. Streamed text can be
    /// rewritten by the chunked transcript later; the quote is checked against this. Stays local.
    pub source: String,
}
/// One turn of the private chat about this recording. `via` leaves room for voice turns.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Turn {
    pub role: String,
    pub text: String,
    pub via: String,
    /// When it was said (RFC 3339), so private turns sit in time order beside the meeting chat.
    pub at: String,
}
pub const MAX_ITEMS: usize = 200;
pub const MAX_TURNS: usize = 200;
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Context {
    pub notes: String,
    pub participants: Vec<String>,
    pub items: Vec<Item>,
    pub calendar: Value,
    pub live_id: String,
    pub live_destination: String,
    pub live_state: String,
    /// Capture-time intervals kept only on this computer. An open end survives a crash.
    pub paused_windows: Vec<(u64, Option<u64>)>,
    /// Discard is durable before the request, so recovery cannot accidentally file it.
    pub live_discarded: bool,
    pub locally_paused: bool,
    pub legacy_import_complete: bool,
    pub finalize_payload: Option<Value>,
    /// Private, router-answered chat. Never sent to a shared meeting.
    pub chat: Vec<Turn>,
    /// Transcript already reviewed for drafts, so automatic drafting only sends new speech.
    pub drafted_through_ms: u64,
}
impl Store {
    pub fn context(&self, id: &str) -> Result<Context> {
        self.exists(id)?;
        let text: Option<String> = self
            .db()?
            .query_row(
                "SELECT document FROM workspace WHERE recording_id=?",
                [id],
                |row| row.get(0),
            )
            .optional()?;
        Ok(match text {
            Some(text) => serde_json::from_str(&text)?,
            None => Context::default(),
        })
    }
    pub fn save_context(&self, id: &str, value: &Context) -> Result<()> {
        self.exists(id)?;
        ensure!(
            value.notes.len() <= 200_000
                && value.items.len() <= MAX_ITEMS
                && value.chat.len() <= MAX_TURNS
                && value.participants.len() <= 100,
            "Meeting context is too large"
        );
        self.db()?.execute("INSERT INTO workspace VALUES(?,?) ON CONFLICT(recording_id) DO UPDATE SET document=excluded.document",params![id,serde_json::to_string(value)?])?;
        Ok(())
    }
    pub fn update_context(
        &self,
        id: &str,
        change: impl FnOnce(&mut Context) -> Result<()>,
    ) -> Result<()> {
        self.exists(id)?;
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let text: Option<String> = transaction
            .query_row(
                "SELECT document FROM workspace WHERE recording_id=?",
                [id],
                |row| row.get(0),
            )
            .optional()?;
        let mut value = match text {
            Some(text) => serde_json::from_str(&text)?,
            None => Context::default(),
        };
        change(&mut value)?;
        ensure!(
            value.notes.len() <= 200_000
                && value.items.len() <= MAX_ITEMS
                && value.chat.len() <= MAX_TURNS
                && value.participants.len() <= 100,
            "Meeting context is too large"
        );
        transaction.execute("INSERT INTO workspace VALUES(?,?) ON CONFLICT(recording_id) DO UPDATE SET document=excluded.document",params![id,serde_json::to_string(&value)?])?;
        transaction.commit()?;
        Ok(())
    }
}
