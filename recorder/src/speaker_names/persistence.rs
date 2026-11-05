use super::{Interval, Observation, Timeline, name};
use crate::store::Store;
use anyhow::Result;
use rusqlite::{Connection, OptionalExtension, params};

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
        PRIMARY KEY(recording_id,name));",
    )?;
    Ok(())
}
impl Store {
    pub fn meeting_speakers(&self, id: &str) -> Result<Timeline> {
        let db = self.db()?;
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
            "SELECT name,is_self FROM meeting_participants WHERE recording_id=? ORDER BY name",
        )?;
        let mut timeline = Timeline {
            intervals,
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
    pub(super) fn observe_speakers(
        &self,
        id: &str,
        observed: &Observation,
        intervals: &[Interval],
    ) -> Result<()> {
        let mut db = self.db()?;
        let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        for person in observed
            .participants
            .iter()
            .chain(&observed.self_names)
            .filter_map(|s| name(s))
            .take(100)
        {
            let local = observed.self_names.iter().any(|s| super::same(s, &person));
            tx.execute("INSERT INTO meeting_participants(recording_id,name,is_self)
                SELECT ?,?,? WHERE (SELECT COUNT(*) FROM meeting_participants WHERE recording_id=?)<100
                    OR EXISTS(SELECT 1 FROM meeting_participants WHERE recording_id=? AND name=?)
                ON CONFLICT(recording_id,name) DO UPDATE SET is_self=MAX(is_self,excluded.is_self)", params![id,person,local,id,id,person])?;
        }
        for interval in intervals.iter().take(100) {
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
        tx.commit()?;
        Ok(())
    }
}
