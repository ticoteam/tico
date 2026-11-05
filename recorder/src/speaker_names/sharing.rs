//! Name-only revisions for finished turns that reached Tico before any identity evidence.
//! Their existing text/sequence stays intact; a whole shared line cannot be split after ack.
use crate::{store::Recording, transcript};
use anyhow::Result;
use rusqlite::{OptionalExtension, Transaction, params};
use serde_json::{Value, json};

pub(crate) fn correct_shared_names(
    tx: &Transaction,
    record: &Recording,
    destination: &str,
) -> Result<()> {
    if record.meeting_speakers.intervals.is_empty() {
        return Ok(());
    }
    let words = transcript::chunk_words(record);
    let mut query = tx.prepare("SELECT o.seq,o.chunk_id,o.payload_json FROM live_outbox o
        WHERE o.recording_id=? AND o.destination_id=?
        AND NOT EXISTS(SELECT 1 FROM live_spans s WHERE s.recording_id=o.recording_id AND s.destination_id=o.destination_id AND s.seq=o.seq)
        AND NOT EXISTS(SELECT 1 FROM live_edits e WHERE e.recording_id=o.recording_id AND e.destination_id=o.destination_id AND e.seq=o.seq)")?;
    for row in query.query_map([&record.id, destination], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })? {
        let (seq, chunk, original) = row?;
        if !record
            .chunks
            .iter()
            .any(|c| c.id == chunk && c.channel != "me" && c.status == "done")
        {
            continue;
        }
        let Some(words) = words.get(&chunk) else {
            continue;
        };
        let previous: Option<(i64,String)> = tx.query_row("SELECT revision,payload_json FROM live_corrections WHERE recording_id=? AND destination_id=? AND seq=? ORDER BY rowid DESC LIMIT 1",
            params![record.id,destination,seq], |row| Ok((row.get(0)?,row.get(1)?))).optional()?;
        let (revision, shown) = previous.unwrap_or((1, original));
        let shown: Value = serde_json::from_str(&shown)?;
        let (speaker, _) =
            crate::live::corrected(words.clone(), shown["speaker"].as_str().unwrap_or(""));
        if shown["speaker"] == speaker || !record.meeting_speakers.participants.contains(&speaker) {
            continue;
        }
        let payload = json!({"revision":revision+1,"speaker":speaker,"start_ms":shown["start_ms"],"end_ms":shown["end_ms"],"text":shown["text"]});
        tx.execute("INSERT INTO live_corrections(recording_id,destination_id,seq,revision,payload_json) VALUES(?,?,?,?,?)",
            params![record.id,destination,seq,revision+1,payload.to_string()])?;
    }
    Ok(())
}
