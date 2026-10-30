use crate::{
    network::{Tico, hash, ready},
    store::{Delivery, Store},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{
        Arc, Mutex,
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
/// At most one pass this often while the library keeps changing (a recording in progress).
const SYNC_GAP: Duration = Duration::from_secs(2);
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
                        errors.retain(|id, _| {
                            records
                                .iter()
                                .any(|row| &row.id == id && row.live_state == "live")
                        });
                        drop(errors);
                        for record in records.into_iter().filter(|row| row.live_state == "live") {
                            if stopped.load(Ordering::Relaxed) {
                                break;
                            }
                            live += 1;
                            let Ok(context) = store.context(&record.id) else {
                                continue;
                            };
                            let result = Tico::saved(config, &context.live_destination)
                                .and_then(|tico| tico.sync_live(&store, &record.id));
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
    pub fn sync_live(&self, store: &Store, id: &str) -> Result<Value> {
        let record = store.get(id)?;
        let context = store.context(id)?;
        ensure!(
            context.live_destination == self.destination.id && !context.live_id.is_empty(),
            "Connect this meeting to Tico first"
        );
        let prefix = format!("/api/v2/live-meetings/{}", context.live_id);
        if context.live_state == "live" {
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
            let mut db = store.db()?;
            let transaction =
                db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let mut seq:i64=transaction.query_row("SELECT COALESCE(MAX(seq),0) FROM live_outbox WHERE recording_id=? AND destination_id=?",[id,&self.destination.id],|row|row.get(0))?;
            let mut last_start = 0;
            {
                let mut query=transaction.prepare("SELECT payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? ORDER BY seq DESC LIMIT 1")?;
                for text in
                    query.query_map([id, &self.destination.id], |row| row.get::<_, String>(0))?
                {
                    last_start = serde_json::from_str::<Value>(&text?)?["start_ms"]
                        .as_u64()
                        .unwrap_or(0);
                }
            }
            let queued = {
                let mut query = transaction.prepare(
                    "SELECT chunk_id FROM live_outbox WHERE recording_id=? AND destination_id=?",
                )?;
                query
                    .query_map([id, &self.destination.id], |row| row.get::<_, String>(0))?
                    .collect::<std::result::Result<std::collections::HashSet<_>, _>>()?
            };
            let texts = crate::transcript::chunk_texts(&record);
            for chunk in &record.chunks {
                if queued.contains(&chunk.id) {
                    continue;
                }
                if chunk.end_ms > watermark || !["done", "silence"].contains(&chunk.status.as_str())
                {
                    break;
                }
                let Some(text) = texts.get(&chunk.id) else {
                    continue;
                };
                ensure!(
                    chunk.start_ms >= last_start,
                    "A late transcript turn precedes the shared transcript. Local audio is safe."
                );
                seq += 1;
                last_start = chunk.start_ms;
                let payload = json!({"seq":seq,"speaker":chunk.speaker,"start_ms":chunk.start_ms,"end_ms":chunk.end_ms,"text":text});
                transaction.execute("INSERT INTO live_outbox(recording_id,destination_id,seq,chunk_id,payload_json) VALUES(?,?,?,?,?)",rusqlite::params![id,self.destination.id,seq,chunk.id,serde_json::to_string(&payload)?])?;
            }
            transaction.commit()?;
            let mut query=db.prepare("SELECT seq,payload_json FROM live_outbox WHERE recording_id=? AND destination_id=? AND acknowledged=0 ORDER BY seq")?;
            let pending = query
                .query_map([id, &self.destination.id], |row| {
                    Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
                })?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            for batch in pending.chunks(100) {
                let chunks = batch
                    .iter()
                    .map(|(_, text)| serde_json::from_str::<Value>(text))
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                let payload = json!({"chunks":chunks});
                let reply = self.call(
                    &format!("{prefix}/chunks"),
                    Some(&payload),
                    &format!("recorder:{id}:live:{}", hash(&payload)),
                )?;
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
        self.call(&prefix, None, "")
    }
    pub fn finish_live(&self, store: &Store, id: &str) -> Result<()> {
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
        self.sync_live(store, id)?;
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
