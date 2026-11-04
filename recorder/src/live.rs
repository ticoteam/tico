use crate::{
    network::{Tico, hash, ready},
    store::{Delivery, Recording, Store, Word},
    transcript,
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

/// Transcript delivery continues while the meeting window is closed or minimized.
pub struct LiveSync {
    stop: Arc<AtomicBool>,
    errors: Arc<Mutex<HashMap<String, String>>>,
    store: Store,
}
/// At most one pass this often while the library keeps changing (a recording in progress), so
/// bots hear streamed speech within a second or two.
const SYNC_GAP: Duration = Duration::from_secs(1);
/// A paused meeting is read back this often, to notice when it is resumed in Tico.
const PAUSED_WAIT: Duration = Duration::from_secs(10);
/// With shared meetings live, a pass at least this often even when nothing changed here.
const LIVE_WAIT: Duration = Duration::from_secs(30);
/// With nothing shared live, sleep until the library changes or this passes.
const IDLE_WAIT: Duration = Duration::from_secs(120);
impl LiveSync {
    pub fn start(store: Store) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let errors = Arc::new(Mutex::new(HashMap::new()));
        let stopped = stop.clone();
        let status = errors.clone();
        let worker_store = store.clone();
        std::thread::Builder::new()
            .name("tico-live-sync".into())
            .spawn(move || {
                crate::qos::utility();
                let store = worker_store;
                let mut config: Option<(u64, crate::settings::Settings)> = None;
                let mut paused_at: HashMap<String, std::time::Instant> = HashMap::new();
                while !stopped.load(Ordering::Relaxed) {
                    let mark = store.changes();
                    let started = std::time::Instant::now();
                    let mut live = 0;
                    let mut failed = false;
                    // Only shared, still-live meetings are visited; the library can be large.
                    let version = store.settings_version();
                    if config.as_ref().is_none_or(|(seen, _)| *seen != version)
                        && let Ok(read) = crate::settings::read(store.root())
                    {
                        config = Some((version, read));
                    }
                    if let (Ok(records), Some((_, config))) = (store.summaries(), &config) {
                        let mut errors = status.lock().unwrap_or_else(|error| error.into_inner());
                        let shared = |state: &str| ["live", "paused"].contains(&state);
                        errors.retain(|id, _| {
                            records
                                .iter()
                                .any(|row| &row.id == id && shared(&row.live_state))
                        });
                        drop(errors);
                        for record in records.into_iter().filter(|row| shared(&row.live_state)) {
                            if stopped.load(Ordering::Relaxed) {
                                break;
                            }
                            live += 1;
                            let Ok(context) = store.context(&record.id) else {
                                continue;
                            };
                            let paused = context.live_state == "paused";
                            if paused
                                && paused_at
                                    .get(&record.id)
                                    .is_some_and(|at| at.elapsed() < PAUSED_WAIT)
                            {
                                continue;
                            }
                            if paused {
                                paused_at.insert(record.id.clone(), std::time::Instant::now());
                            }
                            // Live: only what is new goes; paused: Tico is asked if it resumed.
                            let result =
                                Tico::saved(config, &context.live_destination).and_then(|tico| {
                                    if paused {
                                        tico.sync_live(&store, &record.id).map(drop)
                                    } else {
                                        tico.push_live(&store, &record.id)
                                    }
                                });
                            let mut errors =
                                status.lock().unwrap_or_else(|error| error.into_inner());
                            match result {
                                Ok(_) => {
                                    errors.remove(&record.id);
                                }
                                Err(error) => {
                                    failed = true;
                                    errors.insert(record.id, format!("{error:#}"));
                                }
                            }
                        }
                    }
                    std::thread::sleep(SYNC_GAP.saturating_sub(started.elapsed()));
                    if stopped.load(Ordering::Relaxed) {
                        break;
                    }
                    let wait = if failed {
                        SYNC_GAP * 3
                    } else if live > 0 {
                        LIVE_WAIT
                    } else {
                        IDLE_WAIT
                    };
                    store.wait_changes(mark, wait);
                }
            })
            .expect("live sync worker");
        Self {
            stop,
            errors,
            store,
        }
    }
    pub fn error(&self, id: &str) -> String {
        self.errors
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .get(id)
            .cloned()
            .unwrap_or_default()
    }
    /// Every meeting's sync error, for noticing when one appears or clears.
    pub fn errors(&self) -> Vec<(String, String)> {
        let mut rows: Vec<_> = self
            .errors
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .iter()
            .map(|(id, error)| (id.clone(), error.clone()))
            .collect();
        rows.sort();
        rows
    }
}
impl Drop for LiveSync {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.store.poke();
    }
}

impl Tico {
    /// The meeting's live ID, connecting it first if needed. A new meeting is private unless `team`.
    pub fn ensure_live(&self, store: &Store, id: &str, team: bool) -> Result<String> {
        let context = store.context(id)?;
        if context.live_id.is_empty() {
            self.connect(store, id, Some(if team { "team" } else { "private" }))?;
        }
        Ok(store.context(id)?.live_id)
    }
    /// `visibility` is `private` or `team`; `None` leaves the server's default.
    pub fn connect(&self, store: &Store, id: &str, visibility: Option<&str>) -> Result<()> {
        self.verify()?;
        let context = store.context(id)?;
        let record = store.get(id)?;
        ensure!(
            record
                .deliveries
                .iter()
                .all(|entry| entry.meeting_id.is_empty()),
            "This recording was already sent to Tico"
        );
        ensure!(
            context.live_destination.is_empty() || context.live_destination == self.destination.id,
            "This recording is connected to a different Tico"
        );
        ensure!(context.live_state != "ended", "The live meeting has ended");
        store.update_context(id, |current| {
            current.live_destination = self.destination.id.clone();
            Ok(())
        })?;
        let mut body = json!({"title":record.title.chars().take(200).collect::<String>(),"client_id":format!("recorder:{id}"),"client":"tico-recorder"});
        if let Some(visibility) = visibility {
            body["visibility"] = json!(visibility);
        }
        // Tico refuses unknown fields. Before v0.5.0 it has no `client` (the meeting is just not
        // labelled "From Tico Recorder"); before v0.4.10 no `visibility`, so a private meeting is
        // never opened to the team.
        let reply = self
            .call(
                "/api/v2/live-meetings",
                Some(&body),
                &format!("recorder:{id}:live:connect"),
            )
            .or_else(|error| {
                if !format!("{error:#}").contains("HTTP 422") {
                    return Err(error);
                }
                let mut older = body.clone();
                older.as_object_mut().map(|fields| fields.remove("client"));
                self.call(
                    "/api/v2/live-meetings",
                    Some(&older),
                    &format!("recorder:{id}:live:connect:unlabelled"),
                )
            })
            .map_err(|error| {
                if visibility.is_some() && format!("{error:#}").contains("HTTP 422") {
                    anyhow::anyhow!(
                        "This Tico can't keep live meetings private yet, so nothing was shared. Update Tico to add bots or people."
                    )
                } else {
                    error
                }
            })?;
        let live_id = reply["id"]
            .as_str()
            .context("Tico did not return a live meeting ID")?
            .to_owned();
        store.update_context(id, |current| {
            current.live_id = live_id;
            current.live_state = reply["state"].as_str().unwrap_or("live").into();
            Ok(())
        })?;
        self.sync_live(store, id)?;
        Ok(())
    }
    /// Shares what is new, then reads the meeting back (and notes if it was paused or resumed).
    pub fn sync_live(&self, store: &Store, id: &str) -> Result<Value> {
        let _guard = store.live_lock();
        self.sync_live_inner(store, id)
    }
    fn sync_live_inner(&self, store: &Store, id: &str) -> Result<Value> {
        self.push_live_inner(store, id)?;
        let context = store.context(id)?;
        let view = self.call(
            &format!("/api/v2/live-meetings/{}", context.live_id),
            None,
            "",
        )?;
        note_state(store, id, &view)?;
        Ok(view)
    }
    /// Shares streamed turns and finished chunks as new transcript text while the meeting is
    /// live, and corrections of streamed turns while it is live or paused.
    pub fn push_live(&self, store: &Store, id: &str) -> Result<()> {
        let _guard = store.live_lock();
        self.push_live_inner(store, id)
    }
    fn push_live_inner(&self, store: &Store, id: &str) -> Result<()> {
        let record = store.get(id)?;
        let context = store.context(id)?;
        ensure!(
            context.live_destination == self.destination.id && !context.live_id.is_empty(),
            "Connect this meeting to Tico first"
        );
        let prefix = format!("/api/v2/live-meetings/{}", context.live_id);
        if context.live_discarded {
            return Ok(());
        }
        let record = shareable(&record, &context.paused_windows);
        let live = context.live_state == "live";
        if !live && context.live_state != "paused" {
            return Ok(());
        }
        let mut streamed = store.live_words(id)?;
        for words in streamed.values_mut() {
            words.retain(|word| !excluded(&context.paused_windows, word.start_ms, word.end_ms));
        }
        let mut db = store.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        scrub_pending(
            &transaction,
            id,
            &self.destination.id,
            &context.paused_windows,
            !context.paused_windows.is_empty(),
        )?;
        let mut queue = Queue::read(&transaction, id, &self.destination.id)?;
        if live {
            queue.streamed(&transaction, &record, &streamed)?;
            queue.finished(&transaction, &record)?;
        }
        queue.corrections(&transaction, &record)?;
        transaction.commit()?;
        if live {
            let mut query=db.prepare("SELECT seq,payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? AND acknowledged=0 ORDER BY seq")?;
            let pending = query
                .query_map([id, &self.destination.id], |row| {
                    Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
                })?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            drop(query);
            for batch in pending.chunks(100) {
                let chunks = batch
                    .iter()
                    .map(|(_, text)| serde_json::from_str::<Value>(text))
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                let payload = json!({"chunks":chunks});
                let reply = match self.call(
                    &format!("{prefix}/chunks"),
                    Some(&payload),
                    &format!("recorder:{id}:live:{}", hash(&payload)),
                ) {
                    Ok(reply) => reply,
                    // Paused (or ended) on Tico: nothing new goes until it is resumed.
                    Err(error) if format!("{error:#}").contains("HTTP 409 (meeting_state)") => {
                        note_state(store, id, &self.call(&prefix, None, "")?)?;
                        return Ok(());
                    }
                    Err(error) => return Err(error),
                };
                ensure!(
                    batch.iter().all(|(seq, _)| reply["accepted"]
                        .as_array()
                        .is_some_and(|rows| rows.contains(&json!(seq)))),
                    "Tico did not acknowledge every transcript turn"
                );
                for (seq, _) in batch {
                    db.execute("UPDATE live_outbox SET acknowledged=1 WHERE recording_id=? AND destination_id=? AND seq=?",rusqlite::params![id,self.destination.id,seq])?;
                }
            }
        }
        // Corrections follow the turns they correct, in revision order.
        let mut query = db.prepare(
            "SELECT c.rowid,c.seq,c.revision,c.payload_json FROM live_corrections c JOIN live_outbox o
             ON o.recording_id=c.recording_id AND o.destination_id=c.destination_id AND o.seq=c.seq
             WHERE c.recording_id=? AND c.destination_id=? AND c.state=0 AND o.acknowledged=1
             ORDER BY c.seq,c.revision",
        )?;
        let pending = query
            .query_map([id, &self.destination.id], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(query);
        for (rowid, seq, revision, text) in pending {
            let payload: Value = serde_json::from_str(&text)?;
            let state = match self.call(
                &format!("{prefix}/chunks/{seq}/corrections"),
                Some(&payload),
                &format!("recorder:{id}:live:{seq}:r{revision}:{}", hash(&payload)),
            ) {
                Ok(_) => 1,
                // Tico refuses empty text, so a streamed turn the transcript has no words for
                // keeps its streamed text there.
                Err(error)
                    if payload["text"] == "" && format!("{error:#}").contains("HTTP 422") =>
                {
                    2
                }
                Err(error) => return Err(error),
            };
            db.execute(
                "UPDATE live_corrections SET state=? WHERE rowid=?",
                rusqlite::params![state, rowid],
            )?;
        }
        Ok(())
    }
    /// Pause is committed locally before touching the network. A failed or uncertain request
    /// leaves sharing paused until the person explicitly resumes it.
    pub fn control_live(&self, store: &Store, id: &str, action: &str, at_ms: u64) -> Result<()> {
        ensure!(
            ["pause", "resume", "discard"].contains(&action),
            "Unknown live control"
        );
        let _guard = store.live_lock();
        let context = store.context(id)?;
        ensure!(
            context.live_destination == self.destination.id && !context.live_id.is_empty(),
            "Connect this meeting first"
        );
        ensure!(
            context.live_state != "ended" || action == "discard",
            "The meeting has ended"
        );
        ensure!(
            !context.live_discarded || action == "discard",
            "This meeting was discarded"
        );
        if action == "discard" {
            let record = store.get(id)?;
            ensure!(
                record.state != "recording",
                "Stop recording before discarding"
            );
            ensure!(
                record.deliveries.is_empty(),
                "This meeting was already filed"
            );
            store.update_context(id, |c| {
                c.live_discarded = true;
                Ok(())
            })?;
        } else if action == "pause" {
            store.update_context(id, |c| {
                pause_window(c, at_ms);
                c.locally_paused = true;
                Ok(())
            })?;
        }
        if action == "pause" {
            scrub_pause(store, id)?;
        }
        let reply = self.call(
            &format!("/api/v2/live-meetings/{}/control", context.live_id),
            Some(&json!({"action": if action == "discard" { "end" } else { action }})),
            &format!("recorder:{id}:control:{}", uuid::Uuid::new_v4()),
        )?;
        let state = if action == "discard" {
            "ended"
        } else if action == "pause" {
            "paused"
        } else {
            "live"
        };
        ensure!(
            reply["state"] == state,
            "Tico did not confirm the meeting control"
        );
        store.update_context(id, |c| {
            if action == "resume" {
                c.locally_paused = false;
                if let Some((start, end)) =
                    c.paused_windows.last_mut().filter(|(_, end)| end.is_none())
                {
                    *end = Some(at_ms.max(*start));
                }
            }
            c.live_state = state.into();
            Ok(())
        })
    }
    pub fn finish_live(&self, store: &Store, id: &str) -> Result<()> {
        // Keep controls serialized through the final sync and end request.
        let _guard = store.live_lock();
        ensure!(
            store.context(id)?.live_state != "paused",
            "Resume sharing before Finish or Send"
        );
        ensure!(
            !store.context(id)?.live_discarded,
            "This meeting was discarded; its recording stays local"
        );
        let record = store.get(id)?;
        ensure!(record.state != "recording", "Stop recording first");
        ensure!(
            record
                .chunks
                .iter()
                .all(|chunk| ["done", "silence"].contains(&chunk.status.as_str())),
            "Finish or retry transcription first"
        );
        let has_transcript = record
            .chunks
            .iter()
            .any(|chunk| !chunk.text.trim().is_empty());
        if has_transcript {
            ready(&record)?;
        }
        let context = store.context(id)?;
        ensure!(
            context.live_destination == self.destination.id && !context.live_id.is_empty(),
            "Connect this meeting to Tico first"
        );
        if !has_transcript && context.live_state == "ended" {
            return Ok(());
        }
        if record.deliveries.iter().any(|delivery| {
            delivery.destination_id == self.destination.id && !delivery.meeting_id.is_empty()
        }) {
            return Ok(());
        }
        // Fence edits until the final sync and filing finish, while controls stay serialized.
        let _fence = fence_send(id);
        self.sync_live_inner(store, id)?;
        let context = store.context(id)?;
        ensure!(
            context.live_state != "paused",
            "Resume sharing before Finish or Send"
        );
        // An unsent edit cannot appear in the filed transcript after the meeting ends.
        let unsent = pending_edits(&*store.db()?, id, &self.destination.id)?;
        ensure!(
            unsent == 0,
            "{unsent} edited line(s) did not reach Tico because the meeting ended there. Nothing was filed."
        );
        let prefix = format!("/api/v2/live-meetings/{}", context.live_id);
        if context.live_state != "ended" {
            self.call(
                &format!("{prefix}/control"),
                Some(&json!({"action":"end"})),
                &format!("recorder:{id}:live:end"),
            )?;
            store.update_context(id, |current| {
                current.live_state = "ended".into();
                Ok(())
            })?;
        }
        if !has_transcript {
            return Ok(());
        }
        store.update_context(id,|current|{if current.finalize_payload.is_none(){current.finalize_payload=Some(json!({"notes":current.notes,"participants":current.participants,"calendar":if current.calendar.is_object(){current.calendar.clone()}else{json!({})}}));}Ok(())})?;
        // Finalize files the shared transcript only; the frozen notes follow in `send`.
        let reply = self.call(
            &format!("{prefix}/finalize"),
            Some(&json!({})),
            &format!("recorder:{id}:live:finalize"),
        )?;
        let meeting_id = reply["meeting_id"]
            .as_str()
            .context("Tico did not return the finalized meeting ID")?;
        store.delivery(
            id,
            &Delivery {
                destination_id: self.destination.id.clone(),
                meeting_id: meeting_id.into(),
                status: "partial".into(),
                error: String::new(),
                receipt: json!({"source":"live"}),
            },
        )?;
        Ok(())
    }
}

/// Saves the meeting's state as Tico reports it, so new text stops while it is paused and
/// starts again once it is resumed there. An ended meeting stays ended.
pub(crate) fn note_state(store: &Store, id: &str, view: &Value) -> Result<()> {
    let state = match view["state"].as_str() {
        Some(state @ ("live" | "paused" | "ended")) => state,
        _ => return Ok(()),
    };
    let current = store.context(id)?.live_state;
    if current == state || current == "ended" {
        return Ok(());
    }
    let at_ms = if state == "paused" {
        // The server supplies no capture timestamp for a remote pause. Keep the uncertain
        // interval local, starting at the last acknowledged capture start (or earlier pending
        // turn). This also covers words not transcribed when the pause was discovered.
        let db = store.db()?;
        let mut query = db.prepare("SELECT acknowledged,payload_json FROM live_outbox WHERE recording_id=? AND destination_id=?")?;
        let context = store.context(id)?;
        let mut confirmed = 0;
        let mut pending = u64::MAX;
        for row in query.query_map([id, &context.live_destination], |r| {
            Ok((r.get::<_, bool>(0)?, r.get::<_, String>(1)?))
        })? {
            let (ack, payload) = row?;
            let start = serde_json::from_str::<Value>(&payload)?["start_ms"]
                .as_u64()
                .unwrap_or(0);
            if ack {
                confirmed = confirmed.max(start);
            } else {
                pending = pending.min(start);
            }
        }
        confirmed.min(pending)
    } else {
        capture_time(&store.get(id)?)
    };
    store.update_context(id, |context| {
        if context.live_discarded || (context.locally_paused && state == "live") {
            return Ok(());
        }
        if state == "paused" {
            pause_window(context, at_ms);
        }
        if state == "live" {
            if let Some((start, end)) = context
                .paused_windows
                .last_mut()
                .filter(|(_, end)| end.is_none())
            {
                *end = Some(at_ms.max(*start));
            }
        }
        context.live_state = state.into();
        Ok(())
    })?;
    if state == "paused" {
        scrub_pause(store, id)?;
    }
    Ok(())
}

/// Keep sequence numbers stable: overlapping pending turns become removal markers rather than
/// holes. The full source stays local. Corrections already queued before discovering a pause
/// are invalidated too; the next pass derives fresh corrections from the filtered recording.
fn scrub_pending(
    tx: &rusqlite::Transaction,
    id: &str,
    destination: &str,
    windows: &[(u64, Option<u64>)],
    corrections: bool,
) -> Result<()> {
    let mut query = tx.prepare("SELECT seq,payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? AND acknowledged=0")?;
    let rows = query
        .query_map([id, destination], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    for (seq, text) in rows {
        let mut payload: Value = serde_json::from_str(&text)?;
        if excluded(
            windows,
            payload["start_ms"].as_u64().unwrap_or(0),
            payload["end_ms"].as_u64().unwrap_or(u64::MAX),
        ) {
            payload["text"] = json!("[removed]");
            tx.execute("UPDATE live_outbox SET payload_json=? WHERE recording_id=? AND destination_id=? AND seq=?", rusqlite::params![payload.to_string(), id, destination, seq])?;
        }
    }
    if corrections {
        let mut query = tx.prepare("SELECT c.rowid,c.payload_json,EXISTS(SELECT 1 FROM live_edits e WHERE e.recording_id=c.recording_id AND e.destination_id=c.destination_id AND e.seq=c.seq) FROM live_corrections c WHERE c.recording_id=? AND c.destination_id=? AND c.state=0")?;
        let rows = query
            .query_map([id, destination], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, bool>(2)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        for (rowid, text, edited) in rows {
            let mut payload: Value = serde_json::from_str(&text)?;
            // Explicit edits of public lines cannot be re-derived from audio. Preserve them;
            // edits of overlapping lines still obey the pause privacy boundary.
            if edited
                && !excluded(
                    windows,
                    payload["start_ms"].as_u64().unwrap_or(0),
                    payload["end_ms"].as_u64().unwrap_or(u64::MAX),
                )
            {
                continue;
            }
            payload["text"] = json!("[removed]");
            tx.execute(
                "UPDATE live_corrections SET payload_json=? WHERE rowid=?",
                rusqlite::params![payload.to_string(), rowid],
            )?;
        }
    }
    Ok(())
}
fn scrub_pause(store: &Store, id: &str) -> Result<()> {
    let context = store.context(id)?;
    let mut db = store.db()?;
    let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    scrub_pending(
        &tx,
        id,
        &context.live_destination,
        &context.paused_windows,
        true,
    )?;
    tx.commit()?;
    Ok(())
}

/// Capture timestamps, rather than transcription arrival time, define off-the-record speech.
pub fn capture_time(record: &Recording) -> u64 {
    let saved = record.chunks.iter().map(|c| c.end_ms).max().unwrap_or(0);
    if record.state != "recording" {
        return saved;
    }
    chrono::DateTime::parse_from_rfc3339(&record.started_at)
        .ok()
        .map(|start| {
            (chrono::Utc::now() - start.with_timezone(&chrono::Utc))
                .num_milliseconds()
                .max(0) as u64
        })
        .unwrap_or(saved)
        .max(saved)
}
fn pause_window(c: &mut crate::workspace::Context, at: u64) {
    if c.paused_windows.last().is_none_or(|(_, end)| end.is_some()) {
        c.paused_windows.push((at, None));
    }
    c.live_state = "paused".into();
}
fn excluded(windows: &[(u64, Option<u64>)], start: u64, end: u64) -> bool {
    windows.iter().any(|(from, to)| {
        start < to.unwrap_or(u64::MAX) && end.max(start.saturating_add(1)) > *from
    })
}
/// Make a network-only copy. Without word timings, omit an overlapping chunk in full: guessing
/// a partial text boundary could disclose private speech. The stored recording is never edited.
pub(crate) fn shareable(record: &Recording, windows: &[(u64, Option<u64>)]) -> Recording {
    let mut shared = record.clone();
    for chunk in &mut shared.chunks {
        if chunk.words.is_empty() {
            if excluded(windows, chunk.start_ms, chunk.end_ms) {
                chunk.text.clear();
            }
        } else {
            chunk
                .words
                .retain(|w| !excluded(windows, w.start_ms, w.end_ms));
            chunk.text = chunk
                .words
                .iter()
                .map(|w| w.text.as_str())
                .collect::<Vec<_>>()
                .join(" ");
        }
    }
    shared
}
/// Every task export shares the meeting's privacy and discard fences.
pub fn item_send_blocked(context: &crate::workspace::Context) -> Option<&'static str> {
    if context.live_discarded {
        Some("This meeting was discarded; its recording stays local")
    } else if context.live_state == "paused" || context.locally_paused {
        Some("Resume sharing before sending an action item")
    } else {
        None
    }
}

/// Exact text cannot reliably identify a reworded or number-normalized quote. Require
/// review of kept titles and quotes when the recording contains paused speech.
pub fn paused_review_required(context: &crate::workspace::Context) -> bool {
    !context.paused_windows.is_empty()
        && context.items.iter().any(|i| {
            i.task_id.is_empty() && (!i.quote.trim().is_empty() || !i.title.trim().is_empty())
        })
}

/// Kept draft items can disclose paused speech even though the transcript cannot.
pub fn paused_quotes(record: &Recording, context: &crate::workspace::Context) -> bool {
    let tokens = |s: &str| {
        s.split_whitespace()
            .map(|w| {
                w.chars()
                    .filter(|c| c.is_alphanumeric())
                    .flat_map(char::to_lowercase)
                    .collect::<String>()
            })
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>()
    };
    context
        .items
        .iter()
        .filter(|i| i.task_id.is_empty() && !i.quote.trim().is_empty())
        .any(|item| {
            if excluded(
                &context.paused_windows,
                item.at_ms,
                item.at_ms.saturating_add(1),
            ) {
                return true;
            }
            let quote = tokens(&item.quote);
            if quote.is_empty() {
                return false;
            }
            // Match the whole quote, retaining a pause flag for every matched token. A quote
            // can start in public speech and continue across a pause or a chunk boundary.
            ["me", "them"].iter().any(|channel| {
                let mut text = Vec::new();
                for chunk in record.chunks.iter().filter(|c| c.channel == *channel) {
                    if chunk.words.is_empty() {
                        let paused =
                            excluded(&context.paused_windows, chunk.start_ms, chunk.end_ms);
                        text.extend(tokens(&chunk.text).into_iter().map(|t| (t, paused)));
                    } else {
                        for word in &chunk.words {
                            let paused =
                                excluded(&context.paused_windows, word.start_ms, word.end_ms);
                            text.extend(tokens(&word.text).into_iter().map(|t| (t, paused)));
                        }
                    }
                }
                text.windows(quote.len()).any(|span| {
                    span.iter().map(|(t, _)| t).eq(quote.iter())
                        && span.iter().any(|(_, paused)| *paused)
                })
            })
        })
}

/// A pause this long ends a streamed turn, so the meeting sees who spoke when.
const TURN_GAP_MS: u64 = 1500;
/// Streamed turns are at most this many words (the server takes 10,000 characters).
const TURN_WORDS: usize = 200;

/// A streamed turn as shared: its seq, channel and the time its words really span. The start
/// sent can be later, since start times never go backwards in the shared transcript.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Span {
    pub seq: i64,
    pub channel: String,
    pub start_ms: u64,
    pub end_ms: u64,
}

/// A transcript turn to share.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Turn {
    pub speaker: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
}

/// A finished chunk already used: its words from `overflow_ms` on were shared as a new turn,
/// and the rest correct the streamed turns they fall in. 0 shared it whole (nothing streamed).
struct Source {
    chunk_id: String,
    channel: String,
    overflow_ms: u64,
}

fn midpoint(word: &Word) -> u64 {
    (word.start_ms + word.end_ms.max(word.start_ms)) / 2
}

/// One channel's new streamed words as turns, split where the speaker changes or speech pauses.
/// The microphone is "Me"; meeting audio carries the service's speaker label.
pub(crate) fn streamed_turns(channel: &str, words: &[Word]) -> Vec<Turn> {
    let mut out: Vec<Turn> = vec![];
    let mut count = 0;
    for word in words.iter().filter(|word| !word.text.trim().is_empty()) {
        let speaker = match (channel, word.speaker.as_str()) {
            ("me", _) => "Me",
            (_, "") => "Meeting audio",
            (_, label) => label,
        };
        match out.last_mut() {
            Some(turn)
                if turn.speaker == speaker
                    && word.start_ms.saturating_sub(turn.end_ms) < TURN_GAP_MS
                    && count < TURN_WORDS =>
            {
                turn.text.push(' ');
                turn.text.push_str(word.text.trim());
                turn.end_ms = turn.end_ms.max(word.end_ms);
                count += 1;
            }
            _ => {
                out.push(Turn {
                    speaker: speaker.into(),
                    start_ms: word.start_ms,
                    end_ms: word.end_ms.max(word.start_ms),
                    text: word.text.trim().into(),
                });
                count = 1;
            }
        }
    }
    out
}

/// The words each streamed turn (one channel, by start) gets from the finished transcript: the
/// turn holding a word's midpoint, else the nearest turn (the earlier one on a tie).
pub(crate) fn assign(spans: &[Span], words: &[Word]) -> Vec<Vec<Word>> {
    let mut out = vec![vec![]; spans.len()];
    if spans.is_empty() {
        return out;
    }
    for word in words {
        let at = midpoint(word);
        let index = match spans.partition_point(|span| span.start_ms <= at) {
            0 => 0,
            after if at <= spans[after - 1].end_ms || after == spans.len() => after - 1,
            after if spans[after].start_ms - at < at - spans[after - 1].end_ms => after,
            after => after - 1,
        };
        out[index].push(word.clone());
    }
    out
}

/// A streamed turn's text and speaker from its finished words: the words in time order, and the
/// voice diarization named most often (else the speaker it has). No words is empty text.
pub(crate) fn corrected(mut words: Vec<Word>, speaker: &str) -> (String, String) {
    words.sort_by_key(|word| word.start_ms);
    let text = words
        .iter()
        .map(|word| word.text.trim())
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let mut voices: Vec<(&str, usize)> = vec![];
    for word in words.iter().filter(|word| !word.speaker.is_empty()) {
        match voices.iter_mut().find(|(name, _)| *name == word.speaker) {
            Some((_, count)) => *count += 1,
            None => voices.push((&word.speaker, 1)),
        }
    }
    let named = voices.iter().fold(
        None,
        |best: Option<(&str, usize)>, &(name, count)| match best {
            Some((_, most)) if most >= count => best,
            _ => Some((name, count)),
        },
    );
    (named.map_or(speaker, |(name, _)| name).into(), text)
}

/// The shared transcript's queue for one meeting, read and written in one transaction.
struct Queue<'a> {
    id: &'a str,
    destination: &'a str,
    seq: i64,
    /// Start of the last turn queued; the next may not start earlier.
    last_start: u64,
    spans: Vec<Span>,
    sources: Vec<Source>,
    /// Finished chunks already used (sources, and chunks shared before sources were kept).
    done: HashSet<String>,
}
impl<'a> Queue<'a> {
    fn read(
        transaction: &rusqlite::Transaction,
        id: &'a str,
        destination: &'a str,
    ) -> Result<Self> {
        let seq: i64 = transaction.query_row(
            "SELECT COALESCE(MAX(seq),0) FROM live_outbox WHERE recording_id=? AND destination_id=?",
            [id, destination],
            |row| row.get(0),
        )?;
        let mut last_start = 0;
        let mut query = transaction.prepare(
            "SELECT payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? ORDER BY seq DESC LIMIT 1",
        )?;
        for text in query.query_map([id, destination], |row| row.get::<_, String>(0))? {
            last_start = serde_json::from_str::<Value>(&text?)?["start_ms"]
                .as_u64()
                .unwrap_or(0);
        }
        let mut query = transaction.prepare(
            "SELECT seq,channel,start_ms,end_ms FROM live_spans WHERE recording_id=? AND destination_id=? ORDER BY seq",
        )?;
        let spans = query
            .query_map([id, destination], |row| {
                Ok(Span {
                    seq: row.get(0)?,
                    channel: row.get(1)?,
                    start_ms: row.get::<_, i64>(2)? as u64,
                    end_ms: row.get::<_, i64>(3)? as u64,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let mut query = transaction.prepare(
            "SELECT chunk_id,channel,overflow_ms FROM live_sources WHERE recording_id=? AND destination_id=?",
        )?;
        let sources = query
            .query_map([id, destination], |row| {
                Ok(Source {
                    chunk_id: row.get(0)?,
                    channel: row.get(1)?,
                    overflow_ms: row.get::<_, i64>(2)? as u64,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let mut query = transaction.prepare(
            "SELECT chunk_id FROM live_outbox WHERE recording_id=? AND destination_id=?",
        )?;
        let mut done = query
            .query_map([id, destination], |row| row.get::<_, String>(0))?
            .collect::<std::result::Result<HashSet<_>, _>>()?;
        done.extend(sources.iter().map(|source| source.chunk_id.clone()));
        Ok(Self {
            id,
            destination,
            seq,
            last_start,
            spans,
            sources,
            done,
        })
    }
    /// Queues the next turn; a chunk's own turn carries its id, a streamed one gets its own.
    fn push(
        &mut self,
        transaction: &rusqlite::Transaction,
        chunk_id: Option<&str>,
        turn: &Turn,
    ) -> Result<i64> {
        self.seq += 1;
        let start = turn.start_ms.max(self.last_start);
        self.last_start = start;
        let payload = json!({"seq":self.seq,"speaker":turn.speaker,"start_ms":start,"end_ms":turn.end_ms.max(start),"text":turn.text});
        transaction.execute(
            "INSERT INTO live_outbox(recording_id,destination_id,seq,chunk_id,payload_json) VALUES(?,?,?,?,?)",
            rusqlite::params![
                self.id,
                self.destination,
                self.seq,
                chunk_id.map_or_else(|| format!("stream:{}", self.seq), str::to_owned),
                serde_json::to_string(&payload)?
            ],
        )?;
        Ok(self.seq)
    }
    /// Where a channel's shared text ends: its last streamed turn or used chunk. Streamed words
    /// before it are already shared, or are the finished transcript's to tell.
    fn shared_until(&self, record: &Recording, channel: &str) -> u64 {
        let spans = self.spans.iter().filter(|span| span.channel == channel);
        let chunks = record
            .chunks
            .iter()
            .filter(|chunk| chunk.channel == channel && self.done.contains(&chunk.id));
        spans
            .map(|span| span.end_ms)
            .chain(chunks.map(|chunk| chunk.end_ms))
            .max()
            .unwrap_or(0)
    }
    /// Streamed final words not shared yet, as new turns in time order across both channels.
    fn streamed(
        &mut self,
        transaction: &rusqlite::Transaction,
        record: &Recording,
        streamed: &BTreeMap<String, Vec<Word>>,
    ) -> Result<()> {
        let mut turns = vec![];
        for channel in ["me", "them"] {
            let Some(words) = streamed.get(channel) else {
                continue;
            };
            let words = match (channel, streamed.get("them")) {
                ("me", Some(them)) => transcript::without_echo(words, them),
                _ => words.clone(),
            };
            let from = self.shared_until(record, channel);
            // After the recording stops, only words a chunk is still transcribing are early.
            let early = |at: u64| {
                record.state == "recording"
                    || record.chunks.iter().any(|chunk| {
                        chunk.channel == channel
                            && !["done", "silence"].contains(&chunk.status.as_str())
                            && (chunk.start_ms..chunk.end_ms).contains(&at)
                    })
            };
            let fresh: Vec<Word> = words
                .into_iter()
                .filter(|word| {
                    word.start_ms >= from
                        && !transcript::covered(record, channel, word.start_ms)
                        && early(word.start_ms)
                })
                .collect();
            turns.extend(
                streamed_turns(channel, &fresh)
                    .into_iter()
                    .map(|turn| (channel, turn)),
            );
        }
        turns.sort_by_key(|(channel, turn)| (turn.start_ms, *channel != "me"));
        for (channel, turn) in turns {
            let seq = self.push(transaction, None, &turn)?;
            transaction.execute(
                "INSERT INTO live_spans(recording_id,destination_id,seq,channel,start_ms,end_ms) VALUES(?,?,?,?,?,?)",
                rusqlite::params![
                    self.id,
                    self.destination,
                    seq,
                    channel,
                    turn.start_ms as i64,
                    turn.end_ms as i64
                ],
            )?;
            self.spans.push(Span {
                seq,
                channel: channel.into(),
                start_ms: turn.start_ms,
                end_ms: turn.end_ms,
            });
        }
        Ok(())
    }
    /// Finished chunks in order. Without streamed turns on its channel a chunk is shared whole,
    /// as before live text; otherwise only its words past the last streamed turn are new.
    fn finished(&mut self, transaction: &rusqlite::Transaction, record: &Recording) -> Result<()> {
        let watermark = if record.state != "recording" {
            u64::MAX
        } else {
            ["me", "them"]
                .iter()
                .filter(|channel| **channel == "me" || record.meeting_audio)
                .map(|channel| {
                    record
                        .chunks
                        .iter()
                        .filter(|chunk| chunk.channel == *channel)
                        .map(|chunk| chunk.end_ms)
                        .max()
                        .unwrap_or(0)
                })
                .min()
                .unwrap_or(0)
        };
        let texts = transcript::chunk_texts(record);
        let words = transcript::chunk_words(record);
        for chunk in &record.chunks {
            if self.done.contains(&chunk.id) {
                continue;
            }
            if chunk.end_ms > watermark || !["done", "silence"].contains(&chunk.status.as_str()) {
                break;
            }
            let overflow_ms = self
                .spans
                .iter()
                .filter(|span| span.channel == chunk.channel)
                .map(|span| span.end_ms)
                .max()
                .unwrap_or(0);
            let turn = if overflow_ms == 0 {
                texts.get(&chunk.id).map(|text| Turn {
                    speaker: chunk.speaker.clone(),
                    start_ms: chunk.start_ms,
                    end_ms: chunk.end_ms,
                    text: text.clone(),
                })
            } else {
                let rest: Vec<&Word> = words
                    .get(&chunk.id)
                    .into_iter()
                    .flatten()
                    .filter(|word| midpoint(word) >= overflow_ms)
                    .collect();
                rest.first().map(|first| Turn {
                    speaker: chunk.speaker.clone(),
                    start_ms: first.start_ms,
                    end_ms: chunk.end_ms,
                    text: rest
                        .iter()
                        .map(|word| word.text.trim())
                        .collect::<Vec<_>>()
                        .join(" "),
                })
            };
            if let Some(turn) = turn.filter(|turn| !turn.text.trim().is_empty()) {
                // Mixed with streamed turns a late chunk is placed at the end; alone, it is a fault.
                ensure!(
                    !self.spans.is_empty() || turn.start_ms >= self.last_start,
                    "A late transcript turn precedes the shared transcript. Local audio is safe."
                );
                self.push(transaction, Some(&chunk.id), &turn)?;
            }
            transaction.execute(
                "INSERT INTO live_sources(recording_id,destination_id,chunk_id,channel,end_ms,overflow_ms) VALUES(?,?,?,?,?,?)",
                rusqlite::params![
                    self.id,
                    self.destination,
                    chunk.id,
                    chunk.channel,
                    chunk.end_ms as i64,
                    overflow_ms as i64
                ],
            )?;
            self.done.insert(chunk.id.clone());
            self.sources.push(Source {
                chunk_id: chunk.id.clone(),
                channel: chunk.channel.clone(),
                overflow_ms,
            });
        }
        Ok(())
    }
    /// Queues a correction for each streamed turn whose finished words differ from what Tico has.
    /// A turn is settled once every chunk under it is used.
    fn corrections(&self, transaction: &rusqlite::Transaction, record: &Recording) -> Result<()> {
        if self.spans.is_empty() {
            return Ok(());
        }
        let used: HashSet<&str> = self
            .sources
            .iter()
            .map(|source| source.chunk_id.as_str())
            .collect();
        let settled = |span: &Span| {
            let mut under = record.chunks.iter().filter(|chunk| {
                chunk.channel == span.channel
                    && chunk.start_ms <= span.end_ms
                    && chunk.end_ms > span.start_ms
            });
            let first = under.next();
            first.is_some_and(|chunk| used.contains(chunk.id.as_str()))
                && under.all(|chunk| used.contains(chunk.id.as_str()))
        };
        // What Tico has for each streamed turn: the original, then the latest correction.
        let mut sent: HashMap<i64, (Value, i64)> = HashMap::new();
        let mut query = transaction.prepare(
            "SELECT seq,payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? AND chunk_id LIKE 'stream:%'",
        )?;
        for row in query.query_map([self.id, self.destination], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })? {
            let (seq, text) = row?;
            sent.insert(seq, (serde_json::from_str(&text)?, 1));
        }
        let mut query = transaction.prepare(
            "SELECT seq,revision,state,payload_json FROM live_corrections WHERE recording_id=? AND destination_id=? ORDER BY rowid",
        )?;
        for row in query.query_map([self.id, self.destination], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
            ))
        })? {
            let (seq, revision, state, text) = row?;
            let latest: Value = serde_json::from_str(&text)?;
            if let Some((shown, current)) = sent.get_mut(&seq) {
                shown["speaker"] = latest["speaker"].clone();
                shown["text"] = latest["text"].clone();
                // A refused correction left Tico at the revision it had.
                if state != 2 {
                    *current = (*current).max(revision);
                }
            }
        }
        let words = transcript::chunk_words(record);
        // A line a person edited keeps their text: finished words no longer correct it.
        let mut query = transaction
            .prepare("SELECT seq FROM live_edits WHERE recording_id=? AND destination_id=?")?;
        let edited: HashSet<i64> = query
            .query_map([self.id, self.destination], |row| row.get(0))?
            .collect::<std::result::Result<_, _>>()?;
        drop(query);
        for channel in ["me", "them"] {
            let mut spans: Vec<Span> = self
                .spans
                .iter()
                .filter(|span| span.channel == channel)
                .cloned()
                .collect();
            if spans.is_empty() {
                continue;
            }
            spans.sort_by_key(|span| span.start_ms);
            let heard: Vec<Word> = self
                .sources
                .iter()
                .filter(|source| source.channel == channel && source.overflow_ms > 0)
                .flat_map(|source| {
                    words
                        .get(&source.chunk_id)
                        .into_iter()
                        .flatten()
                        .filter(|word| midpoint(word) < source.overflow_ms)
                })
                .cloned()
                .collect();
            for (span, words) in spans.iter().zip(assign(&spans, &heard)) {
                if edited.contains(&span.seq) {
                    continue;
                }
                let Some((shown, revision)) = sent.get(&span.seq) else {
                    continue;
                };
                if !settled(span) {
                    continue;
                }
                let (speaker, text) = corrected(words, shown["speaker"].as_str().unwrap_or(""));
                if shown["speaker"] == speaker.as_str() && shown["text"] == text.as_str() {
                    continue;
                }
                let next = revision + 1;
                let payload = json!({"revision":next,"speaker":speaker,"start_ms":shown["start_ms"],"end_ms":shown["end_ms"],"text":text});
                transaction.execute(
                    "INSERT INTO live_corrections(recording_id,destination_id,seq,revision,payload_json) VALUES(?,?,?,?,?)",
                    rusqlite::params![
                        self.id,
                        self.destination,
                        span.seq,
                        next,
                        serde_json::to_string(&payload)?
                    ],
                )?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod pause_tests {
    use super::*;

    #[test]
    fn pause_boundaries_filter_both_channels_and_untimed_chunks_without_changing_local_text() {
        let local: Recording = serde_json::from_value(json!({
            "id":"synthetic", "title":"Pause boundaries", "started_at":"", "state":"stopped",
            "microphone":"", "meeting_audio":true, "warning":"", "deliveries":[],
            "chunks":[
                {"id":"me", "recording_id":"synthetic", "channel":"me", "speaker":"Me", "start_ms":0, "end_ms":12000,
                 "file":"", "status":"done", "signal":{"samples":0,"nonzero":0,"peak":0.,"rms":0.,"duration_ms":0,"sample_rate":16000},
                 "text":"before crossing paused resumed", "error":"", "attempts":1,"provider":{},
                 "words":[
                     {"start_ms":1000,"end_ms":2000,"text":"before"},
                     {"start_ms":4990,"end_ms":5010,"text":"crossing"},
                     {"start_ms":6000,"end_ms":6100,"text":"paused"},
                     {"start_ms":10000,"end_ms":10100,"text":"resumed"}]},
                {"id":"them", "recording_id":"synthetic", "channel":"them", "speaker":"Meeting audio", "start_ms":4000, "end_ms":11000,
                 "file":"", "status":"done", "signal":{"samples":0,"nonzero":0,"peak":0.,"rms":0.,"duration_ms":0,"sample_rate":16000},
                 "text":"untimed private speech", "error":"", "attempts":1,"provider":{}}
            ]
        })).unwrap();
        let shared = shareable(&local, &[(5000, Some(10000))]);
        assert_eq!(shared.chunks[0].text, "before resumed");
        assert!(shared.chunks[1].text.is_empty());
        assert_eq!(local.chunks[0].words.len(), 4);
        assert_eq!(local.chunks[1].text, "untimed private speech");
        let open = shareable(&local, &[(5000, None)]);
        assert_eq!(open.chunks[0].text, "before");
    }
    #[test]
    fn pause_scrub_preserves_explicit_public_edits_but_not_overlapping_or_automatic_corrections() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Synthetic pause and edits").unwrap();
        let mut db = store.db().unwrap();
        let tx = db.transaction().unwrap();
        for (seq, start, edited) in [(1, 1000, true), (2, 6000, true), (3, 1000, false)] {
            let payload = json!({"revision":2,"start_ms":start,"end_ms":start+100,"text":"Explicit public edit"});
            tx.execute("INSERT INTO live_corrections(recording_id,destination_id,seq,revision,payload_json) VALUES(?,'synthetic',?,2,?)", rusqlite::params![record.id,seq,payload.to_string()]).unwrap();
            if edited {
                tx.execute("INSERT INTO live_edits(recording_id,destination_id,seq) VALUES(?,'synthetic',?)", rusqlite::params![record.id,seq]).unwrap();
            }
        }
        scrub_pending(&tx, &record.id, "synthetic", &[(5000, Some(10000))], true).unwrap();
        for (seq, expected) in [
            (1, "Explicit public edit"),
            (2, "[removed]"),
            (3, "[removed]"),
        ] {
            let text: String = tx
                .query_row(
                    "SELECT payload_json FROM live_corrections WHERE recording_id=? AND seq=?",
                    rusqlite::params![record.id, seq],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(
                serde_json::from_str::<Value>(&text).unwrap()["text"],
                expected
            );
        }
    }
}
/// Meetings whose Send is under way. Edits to them are refused, so the final pass sends every edit
/// Tico will file.
static SENDING: Mutex<BTreeSet<String>> = Mutex::new(BTreeSet::new());

fn sending_lock() -> MutexGuard<'static, BTreeSet<String>> {
    SENDING.lock().unwrap_or_else(|error| error.into_inner())
}

/// Keeps a meeting fenced while Send runs; the fence lifts when Send returns, successful or not.
struct SendFence(String);

impl Drop for SendFence {
    fn drop(&mut self) {
        sending_lock().remove(&self.0);
    }
}

fn fence_send(id: &str) -> SendFence {
    sending_lock().insert(id.to_owned());
    SendFence(id.to_owned())
}

/// Whether Send is running for this meeting, so its lines show no edit controls.
pub fn is_sending(id: &str) -> bool {
    sending_lock().contains(id)
}

/// What a removed line says in Tico: the line stays in place and reads this instead of its text.
pub const REMOVED: &str = "[removed]";

/// One line shared live, as Tico holds it: the text of its latest revision sent, or of its first.
#[derive(Debug, Clone, PartialEq)]
pub struct SharedLine {
    pub seq: i64,
    pub speaker: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    /// Tico has the line (its chunk was accepted); only such a line can be edited.
    pub acknowledged: bool,
    /// A person changed or removed it; automatic corrections leave it alone.
    pub edited: bool,
}

/// Every line shared live for this meeting, in sequence, at its latest revision queued for Tico (a
/// queued revision may not have reached Tico yet). A refused revision is not shown.
pub fn shared_lines(store: &Store, id: &str, destination: &str) -> Result<Vec<SharedLine>> {
    let db = store.db()?;
    let mut query = db.prepare(
        "SELECT seq,payload_json,acknowledged FROM live_outbox WHERE recording_id=? AND destination_id=? ORDER BY seq",
    )?;
    let sent = query
        .query_map([id, destination], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(query);
    let mut latest: HashMap<i64, Value> = HashMap::new();
    let mut query = db.prepare(
        "SELECT seq,payload_json FROM live_corrections WHERE recording_id=? AND destination_id=? AND state<>2 ORDER BY rowid",
    )?;
    for row in query.query_map([id, destination], |row| {
        Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
    })? {
        let (seq, text) = row?;
        latest.insert(seq, serde_json::from_str(&text)?);
    }
    drop(query);
    let edited = edited_seqs(&db, id, destination)?;
    sent.into_iter()
        .map(|(seq, text, acknowledged)| {
            let payload = match latest.remove(&seq) {
                Some(payload) => payload,
                None => serde_json::from_str(&text)?,
            };
            Ok(SharedLine {
                seq,
                speaker: payload["speaker"].as_str().unwrap_or_default().into(),
                start_ms: payload["start_ms"].as_u64().unwrap_or(0),
                end_ms: payload["end_ms"].as_u64().unwrap_or(0),
                text: payload["text"].as_str().unwrap_or_default().into(),
                acknowledged: acknowledged == 1,
                edited: edited.contains(&seq),
            })
        })
        .collect()
}

fn edited_seqs(db: &rusqlite::Connection, id: &str, destination: &str) -> Result<HashSet<i64>> {
    let mut query =
        db.prepare("SELECT seq FROM live_edits WHERE recording_id=? AND destination_id=?")?;
    let seqs = query
        .query_map([id, destination], |row| row.get(0))?
        .collect::<std::result::Result<_, _>>()?;
    Ok(seqs)
}

/// Edits a person made that no pass has sent to Tico yet.
fn pending_edits(db: &rusqlite::Connection, id: &str, destination: &str) -> Result<i64> {
    Ok(db.query_row(
        "SELECT COUNT(*) FROM live_corrections c JOIN live_edits e
         ON e.recording_id=c.recording_id AND e.destination_id=c.destination_id AND e.seq=c.seq
         WHERE c.recording_id=? AND c.destination_id=? AND c.state=0",
        [id, destination],
        |row| row.get(0),
    )?)
}

/// A person's edit of a line already shared live: its new text, or `None` to remove it. Tico takes
/// it as the line's next revision, sent by the next pass; the line keeps its speaker and times.
/// Tico accepts corrections while a meeting is live or paused, so this works during the meeting
/// and after Stop, until Send ends the meeting there.
pub fn edit_shared(store: &Store, id: &str, seq: i64, text: Option<&str>) -> Result<()> {
    // Held for the whole edit, so Send's fence either sees this edit committed or refuses it.
    let sending = sending_lock();
    ensure!(
        !sending.contains(id),
        "This meeting is being sent to Tico. Edits can't be saved until it finishes."
    );
    let context = store.context(id)?;
    ensure!(
        !context.live_id.is_empty() && !context.live_destination.is_empty(),
        "This transcript is not shared live"
    );
    ensure!(
        ["live", "paused"].contains(&context.live_state.as_str()),
        "This meeting has ended in Tico. Edit it there."
    );
    let text = match text {
        Some(text) => {
            let text = text.trim();
            ensure!(
                !text.is_empty(),
                "A line cannot be empty. Remove it instead."
            );
            ensure!(
                text.chars().count() <= 10_000,
                "This line is too long for Tico"
            );
            text.to_owned()
        }
        None => REMOVED.to_owned(),
    };
    let destination = context.live_destination;
    let mut db = store.db()?;
    let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let (acknowledged, sent): (i64, String) = transaction
        .query_row(
            "SELECT acknowledged,payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? AND seq=?",
            rusqlite::params![id, destination, seq],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .context("This line was not shared live")?;
    ensure!(
        acknowledged == 1,
        "This line is still on its way to Tico. Edit it once it arrives."
    );
    // The text on screen: the latest revision sent (not refused), else the line as first sent.
    let mut query = transaction.prepare(
        "SELECT payload_json FROM live_corrections WHERE recording_id=? AND destination_id=? AND seq=? AND state<>2 ORDER BY rowid DESC LIMIT 1",
    )?;
    let shown: Value = match query.query_row(rusqlite::params![id, destination, seq], |row| {
        row.get::<_, String>(0)
    }) {
        Ok(text) => serde_json::from_str(&text)?,
        Err(rusqlite::Error::QueryReturnedNoRows) => serde_json::from_str(&sent)?,
        Err(error) => return Err(error.into()),
    };
    drop(query);
    // Keeping the text on screen is still a choice: the line is marked edited even when no revision
    // is needed, so a later automatic correction leaves it alone.
    if shown["text"].as_str() != Some(text.as_str()) {
        let revision: i64 = transaction.query_row(
            "SELECT COALESCE(MAX(revision),1)+1 FROM live_corrections WHERE recording_id=? AND destination_id=? AND seq=? AND state<>2",
            rusqlite::params![id, destination, seq],
            |row| row.get(0),
        )?;
        let payload = json!({
            "revision": revision,
            "speaker": shown["speaker"],
            "start_ms": shown["start_ms"],
            "end_ms": shown["end_ms"],
            "text": text,
        });
        transaction.execute(
            "INSERT INTO live_corrections(recording_id,destination_id,seq,revision,payload_json) VALUES(?,?,?,?,?)",
            rusqlite::params![id, destination, seq, revision, serde_json::to_string(&payload)?],
        )?;
    }
    transaction.execute(
        "INSERT OR IGNORE INTO live_edits(recording_id,destination_id,seq) VALUES(?,?,?)",
        rusqlite::params![id, destination, seq],
    )?;
    transaction.commit()?;
    Ok(())
}

/// The transcript of a meeting shared live, as the window shows it: each line as Tico holds it, with
/// edit controls only where an edit can reach Tico, then the speech not yet sent. Speech that starts
/// before the last queued line waits until that line is queued, so the list never shows one line
/// twice; words still being spoken that overlap it wait with it. None when the meeting is not shared.
pub fn shared_transcript(store: &Store, record: &Recording, recording: bool) -> Option<Value> {
    let context = store.context(&record.id).ok()?;
    if context.live_id.is_empty() || context.live_destination.is_empty() {
        return None;
    }
    let lines = shared_lines(store, &record.id, &context.live_destination).ok()?;
    let open = ["live", "paused"].contains(&context.live_state.as_str()) && !is_sending(&record.id);
    let queued_until = lines.iter().map(|line| line.end_ms).max();
    let mut blocks: Vec<Value> = lines
        .iter()
        .map(|line| {
            let removed = line.edited && line.text == REMOVED;
            json!({"speaker":line.speaker,"me":line.speaker == "Me","start_ms":line.start_ms,
                "paragraphs":[line.text],"partial":false,"streamed":false,"seq":line.seq,
                "edited":line.edited,"removed":removed,
                // A removed line stays editable, so a removal can be undone.
                "editable":open && line.acknowledged})
        })
        .collect();
    if let Ok(turns) = crate::stream::live_turns(store, &record.id) {
        blocks.extend(
            turns
                .iter()
                .filter(|turn| !turn.text.trim().is_empty())
                .filter(|turn| queued_until.is_none_or(|end| turn.start_ms >= end))
                .map(|turn| {
                    json!({"speaker":turn.speaker,"me":turn.me,"start_ms":turn.start_ms,
                        "paragraphs":[turn.text.trim()],"partial":turn.partial,
                        "streamed":turn.streamed,"seq":null,"editable":false})
                }),
        );
    }
    Some(json!({"id": record.id, "live": recording, "shared": true, "blocks": blocks}))
}
