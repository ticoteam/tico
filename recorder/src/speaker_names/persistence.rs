use super::{Interval, Timeline};
#[cfg(any(target_os = "macos", test))]
use super::name;
use crate::store::{Recording, Store, Word};
use anyhow::Result;
use rusqlite::{Connection, params};
#[cfg(any(target_os = "macos", test))]
use rusqlite::OptionalExtension;
use std::collections::BTreeMap;

/// Additive and idempotent for existing recorder libraries; deleting a recording deletes its
/// local identity evidence in the same transaction as its other data.
pub(crate) fn migrate(db: &Connection) -> Result<()> {
    db.execute_batch(
        "CREATE TABLE IF NOT EXISTS meeting_speaker_intervals(
        recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,name TEXT NOT NULL,
        PRIMARY KEY(recording_id,start_ms,name));
        CREATE TABLE IF NOT EXISTS meeting_participants(
        recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        name TEXT NOT NULL,is_self INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(recording_id,name));
        CREATE TABLE IF NOT EXISTS meeting_speaker_readable(
        recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,
        PRIMARY KEY(recording_id,start_ms));
        CREATE TABLE IF NOT EXISTS live_span_labels(
        recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        destination_id TEXT NOT NULL,seq INTEGER NOT NULL,label TEXT NOT NULL,
        PRIMARY KEY(recording_id,destination_id,seq));",
    )?;
    Ok(())
}
pub(crate) fn timeline(db: &Connection, id: &str) -> Result<Timeline> {
    let mut query = db.prepare("SELECT start_ms,end_ms,name FROM meeting_speaker_intervals WHERE recording_id=? ORDER BY start_ms,name")?;
    let intervals = query
        .query_map([id], |row| {
            Ok(Interval {
                start_ms: row.get(0)?,
                end_ms: row.get(1)?,
                name: row.get(2)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut query = db.prepare(
        "SELECT start_ms,end_ms FROM meeting_speaker_readable WHERE recording_id=? ORDER BY start_ms",
    )?;
    let readable = query
        .query_map([id], |row| Ok((row.get(0)?, row.get(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut query = db.prepare(
        "SELECT name,is_self FROM meeting_participants WHERE recording_id=? ORDER BY name",
    )?;
    let mut timeline = Timeline {
        intervals,
        readable,
        ..Default::default()
    };
    for row in query.query_map([id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, bool>(1)?))
    })? {
        let (name, local) = row?;
        timeline.participants.push(name.clone());
        if local {
            timeline.self_names.push(name);
        }
    }
    Ok(timeline)
}
/// Votes the recording's names from its current evidence and keeps them on the record, in the
/// same transaction that read it, so names only move forward.
pub(crate) fn settle_names(db: &Connection, record: &mut Recording) -> Result<()> {
    if record.meeting_speakers.intervals.is_empty() && record.speaker_names.is_empty() {
        return Ok(());
    }
    let mut query = db.prepare("SELECT words FROM live_words WHERE recording_id=? AND channel='them' ORDER BY start_ms,rowid")?;
    let mut live = Vec::new();
    for row in query.query_map([&record.id], |r| r.get::<_, String>(0))? {
        live.extend(serde_json::from_str::<Vec<Word>>(&row?)?);
    }
    let streamed = BTreeMap::from([("them".to_owned(), live)]);
    let names = super::vote(record, &streamed);
    if names != record.speaker_names {
        db.execute(
            "UPDATE recordings SET document=json_set(document,'$.speaker_names',json(?)) WHERE id=?",
            params![serde_json::to_string(&names)?, record.id],
        )?;
        record.speaker_names = names;
    }
    Ok(())
}
impl Store {
    pub fn meeting_speakers(&self, id: &str) -> Result<Timeline> {
        timeline(&*self.db()?, id)
    }
    /// Saves newly seen people, speaking intervals and readable spans. Each extends the
    /// previous row it continues, so a long turn stays one row.
    #[cfg(any(target_os = "macos", test))]
    pub(super) fn observe_speakers(
        &self,
        id: &str,
        people: &[(String, bool)],
        intervals: &[Interval],
        readable: &[(u64, u64)],
    ) -> Result<()> {
        let mut db = self.db()?;
        let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        for (person, local) in people.iter().take(100) {
            let Some(person) = name(person) else {
                continue;
            };
            tx.execute("INSERT INTO meeting_participants(recording_id,name,is_self)
                SELECT ?,?,? WHERE (SELECT COUNT(*) FROM meeting_participants WHERE recording_id=?)<100
                    OR EXISTS(SELECT 1 FROM meeting_participants WHERE recording_id=? AND name=?)
                ON CONFLICT(recording_id,name) DO UPDATE SET is_self=MAX(is_self,excluded.is_self)", params![id,person,local,id,id,person])?;
        }
        for interval in intervals.iter().take(400) {
            if interval.end_ms <= interval.start_ms || name(&interval.name).is_none() {
                continue;
            }
            let last: Option<(u64,u64)> = tx.query_row("SELECT start_ms,end_ms FROM meeting_speaker_intervals WHERE recording_id=? AND name=? ORDER BY start_ms DESC LIMIT 1", params![id,interval.name], |row| Ok((row.get(0)?,row.get(1)?))).optional()?;
            if let Some((start, end)) = last.filter(|(_, end)| *end == interval.start_ms) {
                tx.execute("UPDATE meeting_speaker_intervals SET end_ms=? WHERE recording_id=? AND start_ms=? AND name=? AND end_ms=?", params![interval.end_ms,id,start,interval.name,end])?;
            } else {
                tx.execute(
                    "INSERT OR IGNORE INTO meeting_speaker_intervals VALUES(?,?,?,?)",
                    params![id, interval.start_ms, interval.end_ms, interval.name],
                )?;
            }
        }
        for (start_ms, end_ms) in readable.iter().take(400) {
            if end_ms <= start_ms {
                continue;
            }
            let extended = tx.execute(
                "UPDATE meeting_speaker_readable SET end_ms=? WHERE recording_id=? AND end_ms=?",
                params![end_ms, id, start_ms],
            )?;
            if extended == 0 {
                tx.execute(
                    "INSERT OR IGNORE INTO meeting_speaker_readable VALUES(?,?,?)",
                    params![id, start_ms, end_ms],
                )?;
            }
        }
        tx.commit()?;
        Ok(())
    }
}
