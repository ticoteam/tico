//! Name-only revisions for finished-chunk lines Tico already has. Their text and sequence stay;
//! the speaker follows the current name for the voice label the line's words carry.
use super::{MAX_QUEUED_CORRECTIONS, name_of};
use crate::{
    live::{corrected, queued_corrections},
    store::Recording,
    transcript,
};
use anyhow::Result;
use rusqlite::{OptionalExtension, Transaction, params};
use serde_json::{Value, json};

pub(crate) fn correct_shared_names(
    tx: &Transaction,
    record: &Recording,
    destination: &str,
) -> Result<()> {
    // Names are never taken back, so with none there is nothing to revise.
    if record.speaker_names.is_empty() {
        return Ok(());
    }
    // A rename spreads over passes rather than queueing a revision for every line at once.
    let mut budget = MAX_QUEUED_CORRECTIONS - queued_corrections(tx, &record.id, destination)?;
    // Labels without names: what each line's voice is, whatever it is called now.
    let words = transcript::chunk_labels(record);
    // Streamed turns are revised with their finished words; a person's edit is kept.
    let mut query = tx.prepare("SELECT o.seq,o.chunk_id,o.payload_json FROM live_outbox o
        WHERE o.recording_id=? AND o.destination_id=?
        AND NOT EXISTS(SELECT 1 FROM live_spans s WHERE s.recording_id=o.recording_id AND s.destination_id=o.destination_id AND s.seq=o.seq)
        AND NOT EXISTS(SELECT 1 FROM live_edits e WHERE e.recording_id=o.recording_id AND e.destination_id=o.destination_id AND e.seq=o.seq)")?;
    let rows = query
        .query_map([&record.id, destination], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (seq, key, original) in rows {
        if budget <= 0 {
            break;
        }
        // A chunk's later turns are keyed "<chunk>:turn:<n>".
        let chunk = key.split(":turn:").next().unwrap_or(&key);
        if !record
            .chunks
            .iter()
            .any(|c| c.id == chunk && c.channel == "them")
        {
            continue;
        }
        let original: Value = serde_json::from_str(&original)?;
        let (start, end) = (
            original["start_ms"].as_u64().unwrap_or(0),
            original["end_ms"].as_u64().unwrap_or(0),
        );
        let heard: Vec<_> = words
            .get(chunk)
            .into_iter()
            .flatten()
            .filter(|w| {
                let mid = (w.start_ms + w.end_ms) / 2;
                start <= mid && mid <= end
            })
            .cloned()
            .collect();
        if heard.iter().all(|w| w.speaker.is_empty()) {
            continue;
        }
        let (label, _) = corrected(heard, "");
        let speaker = name_of(record, &label);
        // Tico refused exactly this change last time; asking again would be refused again.
        let last: Option<(i64, String)> = tx
            .query_row(
                "SELECT state,payload_json FROM live_corrections WHERE recording_id=? AND destination_id=? AND seq=? ORDER BY rowid DESC LIMIT 1",
                params![record.id, destination, seq],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        if let Some((2, text)) = &last {
            if serde_json::from_str::<Value>(text)?["speaker"] == speaker {
                continue;
            }
        }
        // A refused revision never reached Tico, so the next one builds on the last accepted.
        let previous: Option<(i64, String)> = tx
            .query_row(
                "SELECT revision,payload_json FROM live_corrections WHERE recording_id=? AND destination_id=? AND seq=? AND state<>2 ORDER BY rowid DESC LIMIT 1",
                params![record.id, destination, seq],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        let (revision, shown) = match previous {
            Some((revision, text)) => (revision, serde_json::from_str(&text)?),
            None => (1, original),
        };
        if shown["speaker"] == speaker {
            continue;
        }
        budget -= 1;
        let payload = json!({"revision":revision+1,"speaker":speaker,"start_ms":shown["start_ms"],"end_ms":shown["end_ms"],"text":shown["text"]});
        tx.execute(
            "INSERT INTO live_corrections(recording_id,destination_id,seq,revision,payload_json) VALUES(?,?,?,?,?)",
            params![record.id, destination, seq, revision + 1, payload.to_string()],
        )?;
    }
    Ok(())
}
