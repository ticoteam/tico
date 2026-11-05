use crate::audio::{Channel, Signal, encode_wav, inspect_wav};
use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fs,
    io::Write,
    ops::{Deref, DerefMut},
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex, Weak,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use uuid::Uuid;

const AUTOMATIC_RETRIES: u32 = 2;

#[derive(Clone)]
pub struct Store {
    root: PathBuf,
    shared: Arc<Shared>,
    pub(crate) link_activity: Arc<Mutex<BTreeMap<String, crate::task_links::Activity>>>,
}

/// What every copy of one opened library shares: its identity for the connection cache, and
/// the change signal threads wait on instead of polling.
struct Shared {
    id: u64,
    /// Bumped by every committed write through this process.
    data: AtomicU64,
    /// Bumped by writes and by `poke`; waiters sleep on `wake` until it moves.
    changes: Mutex<u64>,
    wake: Condvar,
    live: Mutex<()>,
    task_links: Mutex<()>,
}
impl Shared {
    fn bump(&self, data: bool) {
        if data {
            self.data.fetch_add(1, Ordering::SeqCst);
        }
        let mut changes = self
            .changes
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        *changes += 1;
        self.wake.notify_all();
    }
}

static NEXT_STORE: AtomicU64 = AtomicU64::new(1);
/// Weak references let settings writers wake every opened library without keeping one alive.
static OPEN_STORES: Mutex<Vec<Weak<Shared>>> = Mutex::new(Vec::new());

pub(crate) fn settings_changed() {
    let mut stores = OPEN_STORES
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    stores.retain(|weak| {
        if let Some(shared) = weak.upgrade() {
            shared.bump(false);
            true
        } else {
            false
        }
    });
}
/// Connections kept open per thread and library, so a query costs no open, pragma or close.
const KEPT_PER_THREAD: usize = 4;
thread_local! {
    static CONNECTIONS: RefCell<Vec<(u64, Connection)>> = const { RefCell::new(Vec::new()) };
}

/// A connection borrowed from this thread's cache; it goes back when dropped. A write through
/// it wakes everything waiting on the library's changes.
pub struct Db {
    connection: Option<Connection>,
    shared: Arc<Shared>,
    before: u64,
}
impl Deref for Db {
    type Target = Connection;
    fn deref(&self) -> &Connection {
        self.connection.as_ref().expect("open connection")
    }
}
impl DerefMut for Db {
    fn deref_mut(&mut self) -> &mut Connection {
        self.connection.as_mut().expect("open connection")
    }
}
impl Drop for Db {
    fn drop(&mut self) {
        let Some(connection) = self.connection.take() else {
            return;
        };
        if connection.total_changes() != self.before {
            self.shared.bump(true);
        }
        // A connection left inside a transaction (a panic mid-write) is closed, not reused.
        if !connection.is_autocommit() {
            return;
        }
        let id = self.shared.id;
        let _ = CONNECTIONS.try_with(|kept| {
            let mut kept = kept.borrow_mut();
            if kept.iter().any(|(store, _)| *store == id) {
                return;
            }
            if kept.len() >= KEPT_PER_THREAD {
                kept.remove(0);
            }
            kept.push((id, connection));
        });
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Recording {
    pub id: String,
    pub title: String,
    pub started_at: String,
    pub state: String,
    pub microphone: String,
    pub meeting_audio: bool,
    pub warning: String,
    pub chunks: Vec<Chunk>,
    pub deliveries: Vec<Delivery>,
    /// Speaker labels for meeting audio: "" (not run), "running", "done" or the error.
    #[serde(default)]
    pub speakers: String,
    /// Stable voice/turn ids to their first displayed label; names can attach to these ids later.
    #[serde(default)]
    pub speaker_labels: BTreeMap<String, String>,
}
impl Recording {
    pub(crate) fn register_speakers(&mut self, mut identities: Vec<(String, String)>) {
        identities.retain(|(id, _)| !id.is_empty());
        // Reserve model identities first, then bind each stable chunk/turn to that identity.
        for (_, preferred) in &identities {
            if !preferred.is_empty() && !self.speaker_labels.contains_key(preferred) {
                self.reserve_speaker(preferred, preferred);
            }
        }
        for (id, preferred) in identities {
            if !id.is_empty() && !self.speaker_labels.contains_key(&id) {
                let label = self.speaker_labels.get(&preferred).cloned();
                if let Some(label) = label {
                    self.speaker_labels.insert(id, label);
                } else {
                    self.reserve_speaker(&id, "");
                }
            }
        }
    }
    fn reserve_speaker(&mut self, id: &str, preferred: &str) {
        let used = |label: &str| self.speaker_labels.values().any(|v| v == label);
        let provisional = preferred.starts_with("stream:")
            || preferred.starts_with("Speaker ")
            || preferred
                .strip_prefix('S')
                .is_some_and(|n| n.parse::<usize>().is_ok());
        let label = if !preferred.is_empty() && !provisional {
            preferred.to_owned()
        } else if preferred.starts_with("Speaker ") && !used(preferred) {
            preferred.to_owned()
        } else {
            let next = self
                .speaker_labels
                .values()
                .filter_map(|v| v.strip_prefix("Speaker ")?.parse::<usize>().ok())
                .max()
                .unwrap_or(0)
                + 1;
            format!("Speaker {next}")
        };
        self.speaker_labels.insert(id.to_owned(), label);
    }
}
/// One recognised word, in recording time. `speaker` is set when diarization named a voice;
/// `turn` is the transcription service's own speaker label, which only holds within one chunk.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Word {
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub speaker: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub turn: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Chunk {
    pub id: String,
    pub recording_id: String,
    pub channel: String,
    pub speaker: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub file: String,
    pub signal: Signal,
    pub status: String,
    pub text: String,
    pub error: String,
    pub attempts: u32,
    pub provider: serde_json::Value,
    /// Automatic retries used since the last manual retry, and when the next may start.
    #[serde(default)]
    pub retries: u32,
    #[serde(default)]
    pub retry_at: i64,
    /// Word timings from the provider, when it returns them; empty after a manual edit.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub words: Vec<Word>,
}
/// Library row computed in SQL, without decoding every chunk of every recording.
#[derive(Debug, Clone, Default)]
pub struct Summary {
    pub id: String,
    pub title: String,
    pub started_at: String,
    pub state: String,
    pub warning: String,
    pub duration_ms: u64,
    pub chunks: usize,
    pub pending: usize,
    pub failed: usize,
    pub silent: usize,
    pub live_state: String,
    /// The connection a shared meeting lives in, if it was shared.
    pub live_destination: String,
    pub deliveries: Vec<Delivery>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Delivery {
    pub destination_id: String,
    pub meeting_id: String,
    pub status: String,
    pub error: String,
    pub receipt: serde_json::Value,
}
fn clean_id(value: &str) -> Result<()> {
    Uuid::parse_str(value).context("Invalid recording identifier")?;
    Ok(())
}
fn private_dir(path: &Path) -> Result<()> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}
fn save_chunk(db: &Connection, value: &Chunk) -> Result<()> {
    db.execute("INSERT INTO chunks(id,recording_id,status,start_ms,document) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document",
        params![value.id,value.recording_id,value.status,value.start_ms,serde_json::to_string(value)?])?;
    Ok(())
}
fn read_chunk(db: &Connection, id: &str) -> Result<Chunk> {
    let text: String =
        db.query_row("SELECT document FROM chunks WHERE id=?", [id], |r| r.get(0))?;
    Ok(serde_json::from_str(&text)?)
}
// Reuse an existing write transaction when transcript edits also reserve displayed identities.
fn read_record(db: &Connection, id: &str, reserve: bool) -> Result<Recording> {
    let text: String = db
        .query_row("SELECT document FROM recordings WHERE id=?", [id], |r| {
            r.get(0)
        })
        .optional()?
        .context("Recording not found")?;
    let mut value: Recording = serde_json::from_str(&text)?;
    let mut query =
        db.prepare("SELECT document FROM chunks WHERE recording_id=? ORDER BY start_ms,id")?;
    value.chunks = query
        .query_map([id], |r| r.get::<_, String>(0))?
        .map(|text| Ok(serde_json::from_str(&text?)?))
        .collect::<Result<_>>()?;
    drop(query);
    value.chunks.sort_by(|a, b| {
        a.start_ms
            .cmp(&b.start_ms)
            .then(a.channel.cmp(&b.channel))
            .then(a.id.cmp(&b.id))
    });
    let mut query =
        db.prepare("SELECT document FROM deliveries WHERE recording_id=? ORDER BY destination_id")?;
    value.deliveries = query
        .query_map([id], |r| r.get::<_, String>(0))?
        .map(|text| Ok(serde_json::from_str(&text?)?))
        .collect::<Result<_>>()?;
    drop(query);
    if !reserve {
        return Ok(value);
    }
    let before = value.speaker_labels.clone();
    let mut query = db.prepare("SELECT words FROM live_words WHERE recording_id=? AND channel='them' ORDER BY start_ms,rowid")?;
    let mut live = Vec::new();
    for row in query.query_map([id], |r| r.get::<_, String>(0))? {
        live.extend(serde_json::from_str::<Vec<Word>>(&row?)?);
    }
    drop(query);
    live.sort_by_key(|w| w.start_ms);
    let mut ends = Vec::with_capacity(live.len());
    let mut end = 0;
    for word in &live {
        end = end.max(word.end_ms);
        ends.push(end);
    }
    value.register_speakers(
        live.iter()
            .map(|w| (w.speaker.clone(), w.speaker.clone()))
            .collect(),
    );
    let mut identities = Vec::new();
    let mut aliases = Vec::new();
    for chunk in value.chunks.iter().filter(|c| c.channel == "them") {
        if chunk.words.is_empty() {
            identities.push((
                crate::transcript::speaker_identity(chunk, &Word::default()),
                chunk.speaker.clone(),
            ));
        }
        let mut votes: BTreeMap<String, BTreeMap<String, u64>> = BTreeMap::new();
        for word in &chunk.words {
            let id = crate::transcript::speaker_identity(chunk, word);
            let first = ends.partition_point(|end| *end <= word.start_ms);
            for other in live[first..]
                .iter()
                .take_while(|w| w.start_ms < word.end_ms)
                .filter(|w| !w.speaker.is_empty())
            {
                let overlap = word
                    .end_ms
                    .min(other.end_ms)
                    .saturating_sub(word.start_ms.max(other.start_ms));
                if overlap > 0 {
                    *votes
                        .entry(id.clone())
                        .or_default()
                        .entry(other.speaker.clone())
                        .or_default() += overlap;
                }
            }
        }
        for word in &chunk.words {
            let id = crate::transcript::speaker_identity(chunk, word);
            let preferred = votes
                .get(&id)
                .and_then(|v| v.iter().max_by_key(|(_, weight)| *weight))
                .map_or_else(|| word.speaker.clone(), |(speaker, _)| speaker.clone());
            // The diarizer identity spans chunks; keep its link when the stream disappears.
            if !word.speaker.is_empty() && word.speaker != preferred {
                aliases.push((word.speaker.clone(), preferred.clone()));
            }
            identities.push((id, preferred));
        }
    }
    value.register_speakers(aliases);
    value.register_speakers(identities);
    if value.speaker_labels != before {
        db.execute("UPDATE recordings SET document=json_set(document,'$.speaker_labels',json(?)) WHERE id=?",
            params![serde_json::to_string(&value.speaker_labels)?, id])?;
    }
    Ok(value)
}
impl Store {
    pub(crate) fn task_links_lock(&self) -> std::sync::MutexGuard<'_, ()> {
        self.shared
            .task_links
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }
    /// Serialize network snapshots and controls, including delayed state replies.
    pub(crate) fn live_lock(&self) -> std::sync::MutexGuard<'_, ()> {
        self.shared.live.lock().unwrap_or_else(|e| e.into_inner())
    }
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let store = Self {
            root: root.as_ref().to_owned(),
            link_activity: Default::default(),
            shared: Arc::new(Shared {
                id: NEXT_STORE.fetch_add(1, Ordering::Relaxed),
                data: AtomicU64::new(0),
                changes: Mutex::new(0),
                wake: Condvar::new(),
                live: Mutex::new(()),
                task_links: Mutex::new(()),
            }),
        };
        {
            let mut stores = OPEN_STORES
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            stores.retain(|weak| weak.strong_count() > 0);
            stores.push(Arc::downgrade(&store.shared));
        }
        private_dir(&store.root)?;
        private_dir(&store.root.join("audio"))?;
        let db = store.db()?;
        db.pragma_update(None, "journal_mode", "WAL")?;
        db.execute_batch("CREATE TABLE IF NOT EXISTS recordings(id TEXT PRIMARY KEY,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          status TEXT NOT NULL,start_ms INTEGER NOT NULL,document TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS chunks_recording ON chunks(recording_id,start_ms,id);
          CREATE INDEX IF NOT EXISTS chunks_queue ON chunks(status,start_ms,id);
          CREATE INDEX IF NOT EXISTS chunks_state ON chunks(recording_id,status);
          CREATE TABLE IF NOT EXISTS deliveries(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          destination_id TEXT NOT NULL,document TEXT NOT NULL,PRIMARY KEY(recording_id,destination_id));
          CREATE TABLE IF NOT EXISTS workspace(recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS live_outbox(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          destination_id TEXT NOT NULL,seq INTEGER NOT NULL,chunk_id TEXT NOT NULL,payload_json TEXT NOT NULL,
          acknowledged INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(recording_id,destination_id,seq),UNIQUE(recording_id,destination_id,chunk_id));
          CREATE TABLE IF NOT EXISTS live_words(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          channel TEXT NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,words TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS live_words_range ON live_words(recording_id,channel,start_ms);
          CREATE TABLE IF NOT EXISTS live_spans(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          destination_id TEXT NOT NULL,seq INTEGER NOT NULL,channel TEXT NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,
          PRIMARY KEY(recording_id,destination_id,seq));
          CREATE TABLE IF NOT EXISTS live_sources(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          destination_id TEXT NOT NULL,chunk_id TEXT NOT NULL,channel TEXT NOT NULL,end_ms INTEGER NOT NULL,overflow_ms INTEGER NOT NULL,
          PRIMARY KEY(recording_id,destination_id,chunk_id));
          CREATE TABLE IF NOT EXISTS live_corrections(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          destination_id TEXT NOT NULL,seq INTEGER NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,
          state INTEGER NOT NULL DEFAULT 0);
          CREATE INDEX IF NOT EXISTS live_corrections_seq ON live_corrections(recording_id,destination_id,seq);
          CREATE TABLE IF NOT EXISTS live_edits(recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          destination_id TEXT NOT NULL,seq INTEGER NOT NULL,
          PRIMARY KEY(recording_id,destination_id,seq));")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(
                store.root.join("recorder.sqlite"),
                fs::Permissions::from_mode(0o600),
            )?;
        }
        Ok(store)
    }
    pub fn root(&self) -> &Path {
        &self.root
    }
    /// This thread's connection to the library, opened once and kept. WAL lets the audio writer,
    /// the workers and the window read and write side by side; FULL syncs every commit, so a
    /// saved chunk survives power loss, not only a crash.
    pub fn db(&self) -> Result<Db> {
        let id = self.shared.id;
        let kept = CONNECTIONS
            .try_with(|kept| {
                let mut kept = kept.borrow_mut();
                let index = kept.iter().position(|(store, _)| *store == id)?;
                Some(kept.remove(index).1)
            })
            .ok()
            .flatten();
        let connection = match kept {
            Some(connection) => connection,
            None => {
                let db = Connection::open(self.root.join("recorder.sqlite"))?;
                db.busy_timeout(Duration::from_secs(5))?;
                db.pragma_update(None, "foreign_keys", "ON")?;
                db.pragma_update(None, "synchronous", "FULL")?;
                db
            }
        };
        Ok(Db {
            before: connection.total_changes(),
            connection: Some(connection),
            shared: self.shared.clone(),
        })
    }
    /// Counts every write to the library through this process; equal values mean nothing changed.
    pub fn version(&self) -> u64 {
        self.shared.data.load(Ordering::SeqCst)
    }
    /// The change signal: moves with every write and every `poke`.
    pub fn changes(&self) -> u64 {
        *self
            .shared
            .changes
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }
    /// Wakes everything waiting on changes without writing.
    pub fn poke(&self) {
        self.shared.bump(false);
    }
    /// Every settings or credential writer advances the process-wide generation.
    pub fn settings_version(&self) -> u64 {
        crate::settings::version()
    }
    /// Sleeps until the change signal moves past `since`, or `timeout`; returns its new value.
    pub fn wait_changes(&self, since: u64, timeout: Duration) -> u64 {
        let deadline = Instant::now() + timeout;
        let mut changes = self
            .shared
            .changes
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        while *changes == since {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            changes = self
                .shared
                .wake
                .wait_timeout(changes, left)
                .unwrap_or_else(|error| error.into_inner())
                .0;
        }
        *changes
    }
    pub fn create(&self, title: &str) -> Result<Recording> {
        let title = title.trim();
        ensure!(
            !title.is_empty() && title.chars().count() <= 300,
            "Enter a title of at most 300 characters"
        );
        let value = Recording {
            id: Uuid::new_v4().to_string(),
            title: title.into(),
            started_at: chrono::Utc::now().to_rfc3339(),
            state: "stopped".into(),
            microphone: String::new(),
            meeting_audio: false,
            warning: String::new(),
            chunks: vec![],
            deliveries: vec![],
            speakers: String::new(),
            speaker_labels: BTreeMap::new(),
        };
        self.db()?.execute(
            "INSERT INTO recordings VALUES(?,?)",
            params![value.id, serde_json::to_string(&value)?],
        )?;
        private_dir(&self.root.join("audio").join(&value.id))?;
        Ok(value)
    }
    pub fn list(&self) -> Result<Vec<Recording>> {
        let db = self.db()?;
        let mut query = db.prepare("SELECT id FROM recordings")?;
        let ids = query
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(query);
        drop(db);
        let mut rows: Vec<_> = ids.iter().map(|id| self.get(id)).collect::<Result<_>>()?;
        rows.sort_by(|a, b| b.started_at.cmp(&a.started_at));
        Ok(rows)
    }
    pub fn summaries(&self) -> Result<Vec<Summary>> {
        let db = self.db()?;
        let mut query = db.prepare(
            "SELECT r.id,r.document,
              COALESCE((SELECT MAX(json_extract(c.document,'$.end_ms')) FROM
                (SELECT document FROM chunks WHERE recording_id=r.id ORDER BY start_ms DESC LIMIT 4) c),0),
              s.total,s.pending,s.failed,s.silent,
              COALESCE((SELECT json_extract(document,'$.live_state') FROM workspace WHERE recording_id=r.id),''),
              COALESCE((SELECT json_extract(document,'$.live_destination') FROM workspace WHERE recording_id=r.id),'')
             FROM recordings r LEFT JOIN (SELECT recording_id,COUNT(*) total,
               SUM(status IN ('pending','running')) pending,SUM(status='failed') failed,
               SUM(status='silence') silent FROM chunks GROUP BY recording_id) s ON s.recording_id=r.id",
        )?;
        let mut rows = query
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(1)?,
                    Summary {
                        id: r.get(0)?,
                        duration_ms: r.get::<_, i64>(2)?.max(0) as u64,
                        chunks: r.get::<_, Option<i64>>(3)?.unwrap_or(0) as usize,
                        pending: r.get::<_, Option<i64>>(4)?.unwrap_or(0) as usize,
                        failed: r.get::<_, Option<i64>>(5)?.unwrap_or(0) as usize,
                        silent: r.get::<_, Option<i64>>(6)?.unwrap_or(0) as usize,
                        live_state: r.get(7)?,
                        live_destination: r.get(8)?,
                        ..Default::default()
                    },
                ))
            })?
            .map(|row| {
                let (document, mut summary) = row?;
                let record: Recording = serde_json::from_str(&document)?;
                summary.title = record.title;
                summary.started_at = record.started_at;
                summary.state = record.state;
                summary.warning = record.warning;
                Ok(summary)
            })
            .collect::<Result<Vec<_>>>()?;
        let mut query = db.prepare("SELECT recording_id,document FROM deliveries")?;
        for row in query.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
            let (id, document) = row?;
            if let Some(summary) = rows.iter_mut().find(|row| row.id == id) {
                summary.deliveries.push(serde_json::from_str(&document)?);
            }
        }
        rows.sort_by(|a, b| b.started_at.cmp(&a.started_at));
        Ok(rows)
    }
    pub(crate) fn exists(&self, id: &str) -> Result<()> {
        clean_id(id)?;
        self.db()?
            .query_row("SELECT 1 FROM recordings WHERE id=?", [id], |_| Ok(()))
            .optional()?
            .context("Recording not found")
    }
    pub fn get(&self, id: &str) -> Result<Recording> {
        clean_id(id)?;
        let mut connection = self.db()?;
        let db = connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let value = read_record(&db, id, true)?;
        db.commit()?;
        Ok(value)
    }
    /// Analysis can link voices before their first display reserves a fallback label.
    #[cfg(feature = "diarize")]
    pub(crate) fn peek(&self, id: &str) -> Result<Recording> {
        clean_id(id)?;
        read_record(&*self.db()?, id, false)
    }
    pub fn mutate(
        &self,
        id: &str,
        change: impl FnOnce(&mut Recording) -> Result<()>,
    ) -> Result<()> {
        clean_id(id)?;
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let text: String =
            transaction.query_row("SELECT document FROM recordings WHERE id=?", [id], |r| {
                r.get(0)
            })?;
        let mut value: Recording = serde_json::from_str(&text)?;
        change(&mut value)?;
        value.chunks.clear();
        value.deliveries.clear();
        transaction.execute(
            "UPDATE recordings SET document=? WHERE id=?",
            params![serde_json::to_string(&value)?, id],
        )?;
        transaction.commit()?;
        Ok(())
    }
    pub fn state(&self, id: &str, state: &str) -> Result<()> {
        ensure!(
            ["recording", "stopped", "interrupted"].contains(&state),
            "Invalid recording state"
        );
        self.mutate(id, |record| {
            record.state = state.into();
            Ok(())
        })
    }
    pub fn save_audio(
        &self,
        id: &str,
        channel: Channel,
        start: u64,
        end: u64,
        samples: &[f32],
        rate: u32,
    ) -> Result<Chunk> {
        self.save_wav(id, channel, start, end, &encode_wav(samples, rate)?)
    }
    pub fn save_wav(
        &self,
        recording_id: &str,
        channel: Channel,
        start_ms: u64,
        end_ms: u64,
        bytes: &[u8],
    ) -> Result<Chunk> {
        self.save_wav_with_id(
            recording_id,
            channel,
            start_ms,
            end_ms,
            bytes,
            &Uuid::new_v4().to_string(),
        )
    }
    pub(crate) fn save_wav_with_id(
        &self,
        recording_id: &str,
        channel: Channel,
        start_ms: u64,
        end_ms: u64,
        bytes: &[u8],
        chunk_id: &str,
    ) -> Result<Chunk> {
        clean_id(chunk_id)?;
        if let Ok(existing) = read_chunk(&*self.db()?, chunk_id) {
            ensure!(
                existing.recording_id == recording_id,
                "Audio identifier belongs to another recording"
            );
            return Ok(existing);
        }
        self.get(recording_id)?;
        ensure!(end_ms > start_ms, "Invalid audio time range");
        let signal = inspect_wav(bytes)?;
        ensure!(signal.samples > 0, "Audio contains no samples");
        let id = chunk_id.to_string();
        let name = format!("{}-{start_ms}-{end_ms}-{id}.wav", channel.as_str());
        let directory = self.root.join("audio").join(recording_id);
        private_dir(&directory)?;
        let path = directory.join(&name);
        let temporary = path.with_extension("tmp");
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut output = options.open(&temporary)?;
        output.write_all(bytes)?;
        output.sync_all()?;
        drop(output);
        fs::rename(temporary, &path)?;
        let chunk = Chunk {
            id,
            recording_id: recording_id.into(),
            channel: channel.as_str().into(),
            speaker: if channel == Channel::Microphone {
                "Me"
            } else {
                "Meeting audio"
            }
            .into(),
            start_ms,
            end_ms,
            file: format!("audio/{recording_id}/{name}"),
            status: if signal.silent() {
                "silence"
            } else {
                "pending"
            }
            .into(),
            signal,
            text: String::new(),
            error: String::new(),
            attempts: 0,
            provider: serde_json::json!({}),
            retries: 0,
            retry_at: 0,
            words: vec![],
        };
        // Write-ahead audio: if the process dies before this transaction, recovery indexes the WAV.
        save_chunk(&*self.db()?, &chunk)?;
        Ok(chunk)
    }
    pub fn audio_path(&self, chunk: &Chunk) -> Result<PathBuf> {
        let path = self.root.join(&chunk.file).canonicalize()?;
        ensure!(
            path.starts_with(self.root.canonicalize()?) && path.is_file(),
            "Invalid saved audio path"
        );
        Ok(path)
    }
    /// Startup only, while the controller holds the app's exclusive process lock.
    pub fn recover(&self) -> Result<usize> {
        let mut recovered = 0;
        for record in self.list()? {
            if record.state == "recording" {
                self.mutate(&record.id,|r|{r.state="interrupted".into();r.warning="The app stopped during recording. Saved audio is available; the final unsaved buffer may be missing.".into();Ok(())})?;
            }
            for mut chunk in record.chunks.clone() {
                if chunk.status == "running" {
                    chunk.status = "pending".into();
                    chunk.error = "Previous transcription was interrupted".into();
                    save_chunk(&*self.db()?, &chunk)?;
                }
            }
            let directory = self.root.join("audio").join(&record.id);
            if !directory.exists() {
                continue;
            }
            for entry in fs::read_dir(directory)? {
                let entry = entry?;
                let name = entry.file_name().to_string_lossy().to_string();
                if !entry.file_type()?.is_file() || !name.ends_with(".wav") {
                    continue;
                }
                let parts: Vec<_> = name.trim_end_matches(".wav").splitn(4, '-').collect();
                if parts.len() != 4
                    || clean_id(parts[3]).is_err()
                    || record.chunks.iter().any(|c| c.id == parts[3])
                {
                    continue;
                }
                let Ok(channel) = Channel::parse(parts[0]) else {
                    continue;
                };
                let (Ok(start_ms), Ok(end_ms)) = (parts[1].parse::<u64>(), parts[2].parse::<u64>())
                else {
                    continue;
                };
                ensure!(end_ms > start_ms, "Invalid recovered audio timing");
                let signal = inspect_wav(&fs::read(entry.path())?)?;
                let chunk = Chunk {
                    id: parts[3].into(),
                    recording_id: record.id.clone(),
                    channel: channel.as_str().into(),
                    speaker: if channel == Channel::Microphone {
                        "Me"
                    } else {
                        "Meeting audio"
                    }
                    .into(),
                    start_ms,
                    end_ms,
                    file: format!("audio/{}/{name}", record.id),
                    status: if signal.silent() {
                        "silence"
                    } else {
                        "pending"
                    }
                    .into(),
                    signal,
                    text: String::new(),
                    error: String::new(),
                    attempts: 0,
                    provider: serde_json::json!({"source":"recovered"}),
                    retries: 0,
                    retry_at: 0,
                    words: vec![],
                };
                save_chunk(&*self.db()?, &chunk)?;
                recovered += 1;
            }
        }
        Ok(recovered)
    }
    pub fn claim_transcription(&self) -> Result<Option<Chunk>> {
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let text: Option<String> = transaction
            .query_row(
                "SELECT document FROM chunks WHERE status='pending'
                 AND COALESCE(json_extract(document,'$.retry_at'),0)<=? ORDER BY start_ms,id LIMIT 1",
                [chrono::Utc::now().timestamp_millis()],
                |r| r.get(0),
            )
            .optional()?;
        let Some(text) = text else {
            return Ok(None);
        };
        let mut chunk: Chunk = serde_json::from_str(&text)?;
        chunk.status = "running".into();
        chunk.attempts += 1;
        chunk.error.clear();
        save_chunk(&transaction, &chunk)?;
        transaction.commit()?;
        Ok(Some(chunk))
    }
    pub fn transcription_result(
        &self,
        claimed: &Chunk,
        text: &str,
        error: &str,
        provider: serde_json::Value,
    ) -> Result<()> {
        self.transcription_words(claimed, text, vec![], error, provider)
    }
    pub fn transcription_words(
        &self,
        claimed: &Chunk,
        text: &str,
        words: Vec<Word>,
        error: &str,
        provider: serde_json::Value,
    ) -> Result<()> {
        // An oversized reply fails this chunk instead of leaving it claimed until restart.
        let (text, error) = if text.chars().count() > 10000 {
            (
                "",
                "The transcription provider returned an oversized result",
            )
        } else {
            (text, error)
        };
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let mut chunk = read_chunk(&transaction, &claimed.id)?;
        ensure!(
            chunk.status == "running" && chunk.attempts == claimed.attempts,
            "This transcription attempt is no longer current"
        );
        chunk.text = text.trim().into();
        chunk.words = if error.is_empty() { words } else { vec![] };
        if chunk.channel == "them" && !chunk.words.is_empty() {
            // New meeting-audio words: speaker labels are found again for the whole meeting.
            transaction.execute(
                "UPDATE recordings SET document=json_set(document,'$.speakers','') WHERE id=?",
                [&chunk.recording_id],
            )?;
        }
        chunk.error = error.chars().take(1000).collect();
        chunk.provider = provider;
        chunk.status = if error.is_empty() {
            "done"
        } else if chunk.retries < AUTOMATIC_RETRIES {
            // Transient network/provider failures recover without a click; backoff spaces attempts.
            chunk.retries += 1;
            chunk.retry_at = chrono::Utc::now().timestamp_millis()
                + 15_000 * i64::from(4_u32.pow(chunk.retries - 1));
            "pending"
        } else {
            "failed"
        }
        .into();
        save_chunk(&transaction, &chunk)?;
        transaction.commit()?;
        Ok(())
    }
    /// Saves diarized speaker names onto meeting-audio words. Chunks that changed since the
    /// words were read (a retry or an edit) keep their new words.
    pub fn label_speakers(&self, id: &str, labels: &[(String, Vec<Word>)]) -> Result<()> {
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        for (chunk_id, words) in labels {
            let mut chunk = read_chunk(&transaction, chunk_id)?;
            ensure!(
                chunk.recording_id == id,
                "Chunk belongs to another recording"
            );
            let same = chunk.words.len() == words.len()
                && chunk
                    .words
                    .iter()
                    .zip(words)
                    .all(|(a, b)| a.start_ms == b.start_ms && a.text == b.text);
            if same {
                chunk.words = words.clone();
                save_chunk(&transaction, &chunk)?;
            }
        }
        transaction.commit()?;
        Ok(())
    }
    /// A stopped, unsent recording whose meeting audio is transcribed with word timings and
    /// has no speaker labels yet.
    pub fn needs_speakers(&self) -> Result<Option<String>> {
        Ok(self.db()?.query_row(
            "SELECT id FROM recordings r WHERE json_extract(document,'$.state')!='recording'
             AND COALESCE(json_extract(document,'$.speakers'),'')=''
             AND NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.recording_id=r.id)
             AND NOT EXISTS(SELECT 1 FROM chunks c WHERE c.recording_id=r.id AND c.status IN ('pending','running'))
             AND EXISTS(SELECT 1 FROM chunks c WHERE c.recording_id=r.id AND c.status='done'
               AND json_extract(c.document,'$.channel')='them' AND json_array_length(json_extract(c.document,'$.words'))>0)
             LIMIT 1",
            [],
            |row| row.get(0),
        ).optional()?)
    }
    pub fn retry_transcription(&self, id: &str) -> Result<usize> {
        let record = self.get(id)?;
        let mut changed = 0;
        for mut chunk in record.chunks {
            if chunk.status == "failed" {
                chunk.status = "pending".into();
                chunk.error.clear();
                chunk.retries = 0;
                chunk.retry_at = 0;
                save_chunk(&*self.db()?, &chunk)?;
                changed += 1;
            }
        }
        Ok(changed)
    }
    pub fn edit_transcript(
        &self,
        id: &str,
        chunk_id: &str,
        speaker: &str,
        text: &str,
    ) -> Result<()> {
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let record = read_record(&transaction, id, true)?;
        ensure!(
            record.state != "recording",
            "Stop recording before editing the transcript"
        );
        ensure!(
            record.deliveries.is_empty(),
            "The transcript is locked after sending begins; retry the original delivery"
        );
        let live:String=transaction.query_row("SELECT COALESCE((SELECT json_extract(document,'$.live_id') FROM workspace WHERE recording_id=?),'')",[id],|row|row.get(0))?;
        ensure!(
            live.is_empty(),
            "This transcript has been shared live; edit it in Tico"
        );
        ensure!(
            speaker.chars().count() <= 100 && text.chars().count() <= 10000,
            "Transcript edit is too large"
        );
        let mut chunk = read_chunk(&transaction, chunk_id)?;
        ensure!(
            chunk.recording_id == id && ["done", "silence"].contains(&chunk.status.as_str()),
            "Finish transcription before editing this turn"
        );
        chunk.speaker = speaker.trim().into();
        chunk.text = text.trim().into();
        // Edited text replaces the provider's words, and with them any diarized speakers.
        chunk.words.clear();
        save_chunk(&transaction, &chunk)?;
        transaction.commit()?;
        Ok(())
    }
    /// Reserve partial-stream voices before their first display, including across reconnects.
    pub(crate) fn register_stream_speakers(&self, id: &str, words: &[Word]) -> Result<()> {
        self.mutate(id, |record| {
            record.register_speakers(
                words
                    .iter()
                    .map(|w| (w.speaker.clone(), w.speaker.clone()))
                    .collect(),
            );
            Ok(())
        })
    }
    /// Saves one final result of the live stream. It replaces the streamed words that start
    /// within `[from_ms, to_ms]` on this channel, then adds `words`. Streamed words show live
    /// text and early drafts, and reach a live meeting at once; the recording's own transcript is
    /// always the finished chunks.
    pub fn replace_live_words(
        &self,
        id: &str,
        channel: &str,
        from_ms: u64,
        to_ms: u64,
        words: &[Word],
    ) -> Result<()> {
        clean_id(id)?;
        let (low, high) = (from_ms.saturating_sub(10), to_ms + 10);
        let inside = |word: &Word| (low..high).contains(&word.start_ms);
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let overlapping = {
            let mut query = transaction.prepare(
                "SELECT rowid,words FROM live_words WHERE recording_id=? AND channel=? AND end_ms>=? AND start_ms<?",
            )?;
            query
                .query_map(params![id, channel, low as i64, high as i64], |row| {
                    Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
                })?
                .collect::<std::result::Result<Vec<_>, _>>()?
        };
        for (rowid, text) in overlapping {
            let kept: Vec<Word> = serde_json::from_str::<Vec<Word>>(&text)?
                .into_iter()
                .filter(|word| !inside(word))
                .collect();
            match (kept.first(), kept.iter().map(|word| word.end_ms).max()) {
                (Some(first), Some(end)) => transaction.execute(
                    "UPDATE live_words SET start_ms=?,end_ms=?,words=? WHERE rowid=?",
                    params![
                        first.start_ms as i64,
                        end as i64,
                        serde_json::to_string(&kept)?,
                        rowid
                    ],
                )?,
                _ => transaction.execute("DELETE FROM live_words WHERE rowid=?", [rowid])?,
            };
        }
        if let (Some(first), Some(end)) = (
            words.iter().map(|word| word.start_ms).min(),
            words.iter().map(|word| word.end_ms).max(),
        ) {
            transaction.execute(
                "INSERT INTO live_words(recording_id,channel,start_ms,end_ms,words) VALUES(?,?,?,?,?)",
                params![id, channel, first as i64, end as i64, serde_json::to_string(words)?],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }
    /// Final streamed words per channel ("me", "them"), in time order.
    pub fn live_words(&self, id: &str) -> Result<BTreeMap<String, Vec<Word>>> {
        clean_id(id)?;
        let db = self.db()?;
        let mut query = db.prepare(
            "SELECT channel,words FROM live_words WHERE recording_id=? ORDER BY start_ms,rowid",
        )?;
        let mut out: BTreeMap<String, Vec<Word>> = BTreeMap::new();
        for row in query.query_map([id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })? {
            let (channel, text) = row?;
            out.entry(channel)
                .or_default()
                .extend(serde_json::from_str::<Vec<Word>>(&text)?);
        }
        for words in out.values_mut() {
            words.sort_by_key(|word| word.start_ms);
        }
        Ok(out)
    }
    pub fn delivery(&self, id: &str, value: &Delivery) -> Result<()> {
        self.db()?.execute("INSERT INTO deliveries VALUES(?,?,?) ON CONFLICT(recording_id,destination_id) DO UPDATE SET document=excluded.document",params![id,value.destination_id,serde_json::to_string(value)?])?;
        Ok(())
    }
    /// Merge only the caller's fields into the current receipt; link retries run beside uploads.
    pub(crate) fn update_delivery(
        &self,
        id: &str,
        value: &mut Delivery,
        change: impl FnOnce(&mut Delivery) -> Result<()>,
    ) -> Result<()> {
        let mut db = self.db()?;
        let transaction = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let text: Option<String> = transaction
            .query_row(
                "SELECT document FROM deliveries WHERE recording_id=? AND destination_id=?",
                params![id, value.destination_id],
                |row| row.get(0),
            )
            .optional()?;
        let mut current = match text {
            Some(text) => serde_json::from_str(&text)?,
            None => value.clone(),
        };
        change(&mut current)?;
        transaction.execute("INSERT INTO deliveries VALUES(?,?,?) ON CONFLICT(recording_id,destination_id) DO UPDATE SET document=excluded.document",params![id,current.destination_id,serde_json::to_string(&current)?])?;
        transaction.commit()?;
        *value = current;
        Ok(())
    }
    pub fn remove_delivery(&self, id: &str, destination: &str) -> Result<()> {
        self.db()?.execute(
            "DELETE FROM deliveries WHERE recording_id=? AND destination_id=?",
            params![id, destination],
        )?;
        Ok(())
    }
    pub fn delete(&self, id: &str) -> Result<()> {
        ensure!(
            self.get(id)?.state != "recording",
            "Stop recording before deleting it"
        );
        self.db()?
            .execute("DELETE FROM recordings WHERE id=?", [id])?;
        let directory = self.root.join("audio").join(id);
        if directory.exists() {
            fs::remove_dir_all(directory)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_thread_keeps_one_connection_and_writes_wake_waiters_but_reads_do_not() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        // Same connection on the next borrow: a TEMP table lives only in the connection that made it.
        store
            .db()
            .unwrap()
            .execute_batch("CREATE TEMP TABLE probe(x)")
            .unwrap();
        store
            .db()
            .unwrap()
            .execute_batch("SELECT * FROM temp.probe")
            .unwrap();
        let journal: String = store
            .db()
            .unwrap()
            .query_row("PRAGMA journal_mode", [], |row| row.get(0))
            .unwrap();
        assert_eq!(journal, "wal");
        let (version, changes) = (store.version(), store.changes());
        store.list().unwrap();
        store.summaries().unwrap();
        assert_eq!((store.version(), store.changes()), (version, changes));
        let writer = store.clone();
        let waiter = std::thread::spawn(move || {
            writer.create("Wakes the waiter").unwrap();
        });
        let woke = store.wait_changes(changes, Duration::from_secs(10));
        waiter.join().unwrap();
        assert!(woke > changes && store.version() > version);
        // A poke wakes waiters without counting as a write.
        let (version, changes) = (store.version(), store.changes());
        store.poke();
        assert_eq!(
            store.wait_changes(changes, Duration::from_secs(1)),
            changes + 1
        );
        assert_eq!(store.version(), version);
    }
    #[test]
    fn silence_survives_restart_and_never_enters_the_paid_queue() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Silence diagnosis").unwrap();
        let chunk = store
            .save_audio(
                &record.id,
                Channel::Microphone,
                0,
                1000,
                &vec![0.; 16000],
                16000,
            )
            .unwrap();
        assert_eq!(chunk.status, "silence");
        assert!(store.claim_transcription().unwrap().is_none());
        let reopened = Store::open(dir.path()).unwrap();
        assert_eq!(
            reopened.get(&record.id).unwrap().chunks[0].signal.nonzero,
            0
        );
        assert!(reopened.audio_path(&chunk).unwrap().exists());
    }
    #[test]
    fn recovery_preserves_audio_after_the_process_dies_before_its_database_commit() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Interrupted capture").unwrap();
        store.state(&record.id, "recording").unwrap();
        let chunk = store
            .save_audio(
                &record.id,
                Channel::Meeting,
                4000,
                5000,
                &vec![0.1; 16000],
                16000,
            )
            .unwrap();
        store
            .db()
            .unwrap()
            .execute("DELETE FROM chunks WHERE id=?", [&chunk.id])
            .unwrap();
        assert_eq!(store.recover().unwrap(), 1);
        assert_eq!(store.recover().unwrap(), 0);
        let record = store.get(&record.id).unwrap();
        assert_eq!(record.state, "interrupted");
        assert_eq!(record.chunks[0].start_ms, 4000);
    }
    #[test]
    fn worker_claims_are_exclusive_and_stale_results_cannot_overwrite_a_retry() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Concurrent STT").unwrap();
        store
            .save_audio(
                &record.id,
                Channel::Microphone,
                0,
                1000,
                &vec![0.1; 16000],
                16000,
            )
            .unwrap();
        let workers: Vec<_> = (0..3)
            .map(|_| {
                let store = store.clone();
                std::thread::spawn(move || store.claim_transcription().unwrap())
            })
            .collect();
        let claims: Vec<_> = workers
            .into_iter()
            .filter_map(|worker| worker.join().unwrap())
            .collect();
        assert_eq!(claims.len(), 1);
        store.recover().unwrap();
        let next = store.claim_transcription().unwrap().unwrap();
        assert!(
            store
                .transcription_result(&claims[0], "Stale words", "", serde_json::json!({}))
                .is_err()
        );
        store
            .transcription_result(&next, "Current words", "", serde_json::json!({}))
            .unwrap();
        assert_eq!(
            store.get(&record.id).unwrap().chunks[0].text,
            "Current words"
        );
    }
    #[test]
    fn a_final_replaces_the_streamed_words_it_covers_and_keeps_the_rest() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Streamed finals").unwrap();
        let word = |start_ms: u64, text: &str| Word {
            start_ms,
            end_ms: start_ms + 200,
            text: text.into(),
            ..Default::default()
        };
        let save = |from, to, words: &[Word]| {
            store
                .replace_live_words(&record.id, "me", from, to, words)
                .unwrap()
        };
        save(1000, 1600, &[word(1000, "We"), word(1300, "ship")]);
        save(1800, 2400, &[word(1800, "Friday"), word(2100, "maybe")]);
        // A revision of the second half keeps the first final and replaces "maybe".
        save(2100, 2600, &[word(2100, "morning"), word(2400, "then")]);
        let texts = |store: &Store| -> Vec<String> {
            store.live_words(&record.id).unwrap()["me"]
                .iter()
                .map(|word| word.text.clone())
                .collect()
        };
        assert_eq!(texts(&store), ["We", "ship", "Friday", "morning", "then"]);
        // The end of the utterance restates all of it.
        let whole = [
            word(1000, "We"),
            word(1300, "ship"),
            word(1800, "Friday"),
            word(2100, "morning."),
        ];
        save(900, 2700, &whole);
        assert_eq!(
            texts(&Store::open(dir.path()).unwrap()),
            ["We", "ship", "Friday", "morning."]
        );
        // Other channels and recordings are untouched; deleting the recording removes its words.
        store
            .replace_live_words(&record.id, "them", 0, 5000, &[word(1200, "Hi")])
            .unwrap();
        save(0, 5000, &[]);
        assert!(!store.live_words(&record.id).unwrap().contains_key("me"));
        assert_eq!(store.live_words(&record.id).unwrap()["them"].len(), 1);
        store.delete(&record.id).unwrap();
        let left: i64 = store
            .db()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM live_words", [], |row| row.get(0))
            .unwrap();
        assert_eq!(left, 0);
    }
    #[test]
    fn provider_failures_retry_with_backoff_then_wait_for_a_manual_retry() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Flaky provider").unwrap();
        store
            .save_audio(
                &record.id,
                Channel::Microphone,
                0,
                1000,
                &vec![0.1; 16000],
                16000,
            )
            .unwrap();
        let backdate = |store: &Store| {
            store
                .db()
                .unwrap()
                .execute(
                    "UPDATE chunks SET document=json_set(document,'$.retry_at',0)",
                    [],
                )
                .unwrap();
        };
        for attempt in 0..=AUTOMATIC_RETRIES {
            let claim = store.claim_transcription().unwrap().unwrap();
            store
                .transcription_result(&claim, "", "HTTP 503", serde_json::json!({}))
                .unwrap();
            let chunk = &store.get(&record.id).unwrap().chunks[0];
            if attempt < AUTOMATIC_RETRIES {
                assert_eq!(chunk.status, "pending");
                assert!(
                    store.claim_transcription().unwrap().is_none(),
                    "retried without backoff"
                );
                backdate(&store);
            } else {
                assert_eq!(chunk.status, "failed");
            }
        }
        assert!(store.claim_transcription().unwrap().is_none());
        assert_eq!(store.retry_transcription(&record.id).unwrap(), 1);
        let claim = store.claim_transcription().unwrap().unwrap();
        let oversized = "word ".repeat(2500);
        store
            .transcription_result(&claim, &oversized, "", serde_json::json!({}))
            .unwrap();
        let chunk = &store.get(&record.id).unwrap().chunks[0];
        assert_eq!(chunk.status, "pending");
        assert!(chunk.text.is_empty() && chunk.error.contains("oversized"));
    }
    #[test]
    fn displayed_speaker_ids_keep_their_labels_after_new_voices_and_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Stable voices").unwrap();
        let add = |start, speaker: &str, turn: &str| {
            store
                .save_audio(
                    &record.id,
                    Channel::Meeting,
                    start,
                    start + 1000,
                    &[0.2; 1600],
                    16000,
                )
                .unwrap();
            let claimed = store.claim_transcription().unwrap().unwrap();
            store
                .transcription_words(
                    &claimed,
                    "Hello",
                    vec![Word {
                        start_ms: start,
                        end_ms: start + 300,
                        text: "Hello".into(),
                        speaker: speaker.into(),
                        turn: turn.into(),
                    }],
                    "",
                    serde_json::json!({}),
                )
                .unwrap();
        };
        add(0, "Speaker 1", "first");
        add(2000, "", "short");
        let before = store.get(&record.id).unwrap();
        let key = format!("chunk:{}:turn:short", before.chunks[1].id);
        assert_eq!(before.speaker_labels[&key], "Speaker 2");
        add(4000, "Speaker 2", "new");
        let after = store.get(&record.id).unwrap();
        assert_eq!(after.speaker_labels[&key], "Speaker 2");
        assert_eq!(
            crate::transcript::lines(&after)
                .iter()
                .map(|l| l.speaker.as_str())
                .collect::<Vec<_>>(),
            ["Speaker 1", "Speaker 2", "Speaker 3"]
        );
        let reopened = Store::open(dir.path()).unwrap();
        assert_eq!(
            reopened.get(&record.id).unwrap().speaker_labels,
            after.speaker_labels
        );
        let mut relabelled = after.chunks[1].words.clone();
        relabelled[0].speaker = "Speaker 9".into();
        store
            .label_speakers(&record.id, &[(after.chunks[1].id.clone(), relabelled)])
            .unwrap();
        assert_eq!(
            crate::transcript::lines(&store.get(&record.id).unwrap())[1].speaker,
            "Speaker 2"
        );
    }
    #[test]
    fn replacing_a_visible_stream_with_batch_words_keeps_its_speaker_identity() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Stream voice identity").unwrap();
        let live = Word {
            start_ms: 1000,
            end_ms: 1300,
            text: "Hello".into(),
            speaker: "stream:S4".into(),
            ..Default::default()
        };
        store
            .replace_live_words(&record.id, "them", 1000, 1300, &[live])
            .unwrap();
        assert_eq!(
            store.get(&record.id).unwrap().speaker_labels["stream:S4"],
            "Speaker 1"
        );
        store
            .save_audio(&record.id, Channel::Meeting, 0, 2000, &[0.2; 1600], 16000)
            .unwrap();
        let claimed = store.claim_transcription().unwrap().unwrap();
        store
            .transcription_words(
                &claimed,
                "Hello",
                vec![Word {
                    start_ms: 1020,
                    end_ms: 1320,
                    text: "Hello".into(),
                    speaker: "Speaker 1".into(),
                    turn: "service-7".into(),
                }],
                "",
                serde_json::json!({}),
            )
            .unwrap();
        let batch = store.get(&record.id).unwrap();
        let id = format!("chunk:{}:turn:service-7", claimed.id);
        assert_eq!(batch.speaker_labels[&id], "Speaker 1");
        assert_eq!(crate::transcript::lines(&batch)[0].speaker, "Speaker 1");
        assert_eq!(batch.speaker_labels["Speaker 1"], "Speaker 1");
        // Reopening without a stream must retain the global diarizer-to-stream alias.
        let store = Store::open(dir.path()).unwrap();
        store
            .db()
            .unwrap()
            .execute("DELETE FROM live_words WHERE recording_id=?", [&record.id])
            .unwrap();
        store
            .save_audio(
                &record.id,
                Channel::Meeting,
                3000,
                5000,
                &[0.2; 1600],
                16000,
            )
            .unwrap();
        let claimed = store.claim_transcription().unwrap().unwrap();
        store
            .transcription_words(
                &claimed,
                "Again",
                vec![Word {
                    start_ms: 3200,
                    end_ms: 3500,
                    text: "Again".into(),
                    speaker: "Speaker 1".into(),
                    turn: "service-9".into(),
                }],
                "",
                serde_json::json!({}),
            )
            .unwrap();
        let later = store.get(&record.id).unwrap();
        assert_eq!(
            later.speaker_labels[&format!("chunk:{}:turn:service-9", claimed.id)],
            "Speaker 1"
        );
        assert!(
            crate::transcript::lines(&later)
                .iter()
                .all(|line| line.speaker == "Speaker 1")
        );
    }
    #[test]
    fn diarization_without_service_turn_ids_cannot_renumber_visible_words() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Stable local diarization").unwrap();
        store
            .save_audio(&record.id, Channel::Meeting, 0, 2000, &[0.2; 1600], 16000)
            .unwrap();
        let claimed = store.claim_transcription().unwrap().unwrap();
        let word = Word {
            start_ms: 1000,
            end_ms: 1300,
            text: "Hello".into(),
            speaker: "Speaker 1".into(),
            ..Default::default()
        };
        store
            .transcription_words(
                &claimed,
                "Hello",
                vec![word.clone()],
                "",
                serde_json::json!({}),
            )
            .unwrap();
        assert_eq!(
            crate::transcript::lines(&store.get(&record.id).unwrap())[0].speaker,
            "Speaker 1"
        );
        store
            .label_speakers(
                &record.id,
                &[(
                    claimed.id.clone(),
                    vec![Word {
                        speaker: "Speaker 2".into(),
                        ..word
                    }],
                )],
            )
            .unwrap();
        assert_eq!(
            crate::transcript::lines(&store.get(&record.id).unwrap())[0].speaker,
            "Speaker 1"
        );
    }
}
