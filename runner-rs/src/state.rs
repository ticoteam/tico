//! `state-<id>/runner.sqlite`: the durable outbox and session references, the same file and schema as
//! `runner/state.py` (the Python side jobs' store). Tables and columns are only ever added, so an older runner can still read it.

use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

pub const SCHEMA: &str = "
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS attempts(
      id TEXT PRIMARY KEY, payload TEXT NOT NULL, phase TEXT NOT NULL,
      completion TEXT, ack INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS attempt_stderr(
      attempt_id TEXT PRIMARY KEY, tail TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
      uploaded INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS events(
      attempt_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
      payload TEXT NOT NULL, PRIMARY KEY(attempt_id,seq));
    CREATE TABLE IF NOT EXISTS sessions(
      bot TEXT NOT NULL, conversation TEXT NOT NULL, runtime TEXT NOT NULL,
      thread TEXT NOT NULL, PRIMARY KEY(bot,conversation,runtime));
    CREATE TABLE IF NOT EXISTS inputs(
      attempt_id TEXT NOT NULL, message_id TEXT NOT NULL, phase TEXT NOT NULL,
      PRIMARY KEY(attempt_id,message_id));
    CREATE TABLE IF NOT EXISTS session_epochs(
      bot TEXT NOT NULL, conversation_id TEXT NOT NULL, session TEXT NOT NULL,
      epoch INTEGER NOT NULL, PRIMARY KEY(bot,conversation_id,session));
    CREATE TABLE IF NOT EXISTS session_cursors(
      thread TEXT PRIMARY KEY, message TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS session_repo_rules(
      thread TEXT PRIMARY KEY, digest TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS import_cursors(
      source TEXT PRIMARY KEY, cursor TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS imports(
      source TEXT NOT NULL, external_id TEXT NOT NULL, phase TEXT NOT NULL,
      meeting_id TEXT, PRIMARY KEY(source,external_id));
    CREATE TABLE IF NOT EXISTS left_out_reported(
      bot TEXT NOT NULL, path TEXT NOT NULL, stamp TEXT NOT NULL, PRIMARY KEY(bot,path));
    CREATE TABLE IF NOT EXISTS close_transcript_pending(
      resource_type TEXT NOT NULL, external_id TEXT NOT NULL,
      created TEXT NOT NULL, last_checked TEXT NOT NULL,
      PRIMARY KEY(resource_type,external_id));
";

/// Clones share one connection, so a clone can carry a call to the blocking pool (`State::off`).
#[derive(Clone)]
pub struct State {
    pub directory: PathBuf,
    conn: Arc<Mutex<Connection>>,
}

#[derive(Debug, Clone)]
pub struct Unfinished {
    pub id: String,
    pub payload: String,
    #[allow(dead_code)]
    pub phase: String,
    #[allow(dead_code)]
    pub completion: Option<String>,
}

fn sql(e: rusqlite::Error) -> String {
    format!("runner.sqlite: {e}")
}

impl State {
    pub fn open(directory: &Path) -> Result<State, String> {
        std::fs::create_dir_all(directory).map_err(|e| format!("cannot create {}: {e}", directory.display()))?;
        // 0700, or under isolation the layout bots can traverse to their gh shim (crate::isolation::protect_state).
        crate::isolation::protect_state(directory).map_err(|e| e.to_string())?;
        let path = directory.join("runner.sqlite");
        std::fs::OpenOptions::new().create(true).append(true).mode(0o600).open(&path).map_err(|e| e.to_string())?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
        let conn = Connection::open(&path).map_err(sql)?;
        conn.busy_timeout(std::time::Duration::from_secs(10)).map_err(sql)?;
        conn.execute_batch(SCHEMA).map_err(sql)?;
        conn.execute_batch("PRAGMA synchronous=FULL;").map_err(sql)?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
        Ok(State { directory: directory.to_path_buf(), conn: Arc::new(Mutex::new(conn)) })
    }

    /// A call from async code, run on the blocking pool: the connection's lock, a busy database and each commit's
    /// fsync then hold a blocking thread, not one of the runtime's two async workers.
    pub async fn off<T: Send + 'static>(&self, f: impl FnOnce(&State) -> T + Send + 'static) -> T {
        let state = self.clone();
        crate::util::off_worker(move || f(&state)).await
    }

    /// One use of the shared connection (the files outbox and memory reports keep their own tables in it).
    pub(crate) fn with<T>(&self, f: impl FnOnce(&mut Connection) -> rusqlite::Result<T>) -> Result<T, String> {
        let mut conn = self.conn.lock().unwrap_or_else(|p| p.into_inner());
        f(&mut conn).map_err(sql)
    }

    pub fn record(&self, attempt: &Value) -> Result<(), String> {
        let id = attempt["id"].as_str().unwrap_or_default().to_string();
        let payload = attempt.to_string();
        self.with(|c| c.execute("INSERT OR IGNORE INTO attempts(id,payload,phase) VALUES(?,?,'claimed')", params![id, payload]).map(|_| ()))
    }

    pub fn input_phase(&self, aid: &str, mid: &str, phase: Option<&str>) -> Result<Option<String>, String> {
        self.with(|c| {
            if let Some(p) = phase {
                c.execute(
                    "INSERT INTO inputs VALUES(?,?,?) ON CONFLICT(attempt_id,message_id) DO UPDATE SET phase=excluded.phase",
                    params![aid, mid, p],
                )?;
            }
            c.query_row("SELECT phase FROM inputs WHERE attempt_id=? AND message_id=?", params![aid, mid], |r| r.get(0)).optional()
        })
    }

    pub fn phase(&self, aid: &str, phase: &str) -> Result<(), String> {
        self.with(|c| c.execute("UPDATE attempts SET phase=? WHERE id=?", params![phase, aid]).map(|_| ()))
    }

    pub fn stderr(&self, aid: &str, tail: &str) -> Result<(), String> {
        self.with(|c| {
            c.execute("DELETE FROM attempt_stderr WHERE updated < datetime('now','-7 days')", [])?;
            c.execute("INSERT INTO attempt_stderr(attempt_id,tail) VALUES(?,?) ON CONFLICT(attempt_id) DO UPDATE SET tail=excluded.tail,revision=revision+1,updated=CURRENT_TIMESTAMP WHERE tail<>excluded.tail", params![aid, tail])?;
            Ok(())
        })
    }

    pub fn pending_stderr(&self, aid: Option<&str>) -> Result<Vec<(String, String, i64)>, String> {
        self.with(|c| {
            let mut q = c.prepare("SELECT attempt_id,tail,revision FROM attempt_stderr WHERE uploaded<revision AND (? IS NULL OR attempt_id=?) ORDER BY updated,attempt_id LIMIT 100")?;
            let rows = q.query_map(params![aid, aid], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
            rows.collect()
        })
    }

    pub fn stderr_sent(&self, aid: &str, revision: i64) -> Result<(), String> {
        self.with(|c| c.execute("UPDATE attempt_stderr SET uploaded=max(uploaded,?) WHERE attempt_id=?", params![revision, aid]).map(|_| ()))
    }

    pub fn append(&self, aid: &str, kind: &str, payload: &Value) -> Result<i64, String> {
        let text = payload.to_string();
        self.with(|c| {
            let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let seq: i64 = tx.query_row("SELECT coalesce(max(seq),0)+1 FROM events WHERE attempt_id=?", params![aid], |r| r.get(0))?;
            tx.execute("INSERT INTO events VALUES(?,?,?,?)", params![aid, seq, kind, text])?;
            tx.commit()?;
            Ok(seq)
        })
    }

    /// At most 100 events past the server's ack, oldest first, as the events endpoint takes them.
    pub fn pending(&self, aid: &str) -> Result<Vec<Value>, String> {
        self.with(|c| {
            let mut stmt = c.prepare(
                "SELECT e.seq, e.kind, e.payload FROM events e JOIN attempts a ON a.id=e.attempt_id \
                 WHERE e.attempt_id=? AND e.seq>a.ack ORDER BY e.seq LIMIT 100",
            )?;
            let rows = stmt.query_map(params![aid], |r| {
                let seq: i64 = r.get(0)?;
                let kind: String = r.get(1)?;
                let payload: String = r.get(2)?;
                Ok(json!({"seq": seq, "kind": kind, "payload": serde_json::from_str::<Value>(&payload).unwrap_or(Value::Null)}))
            })?;
            rows.collect()
        })
    }

    pub fn ack(&self, aid: &str, seq: i64) -> Result<(), String> {
        self.with(|c| c.execute("UPDATE attempts SET ack=max(ack,?) WHERE id=?", params![seq, aid]).map(|_| ()))
    }

    pub fn finish(&self, aid: &str, completion: &Map<String, Value>) -> Result<Value, String> {
        self.with(|c| {
            let seq: i64 = c.query_row("SELECT coalesce(max(seq),0) FROM events WHERE attempt_id=?", params![aid], |r| r.get(0))?;
            let mut done = completion.clone();
            done.insert("last_seq".into(), Value::from(seq));
            let done = Value::Object(done);
            c.execute("UPDATE attempts SET phase='uploading',completion=? WHERE id=?", params![done.to_string(), aid])?;
            Ok(done)
        })
    }

    /// An interrupted completion for an attempt that has none yet; a result already saved stands.
    pub fn interrupted(&self, aid: &str, text: &str) -> Result<(), String> {
        self.with(|c| {
            let seq: i64 = c.query_row("SELECT coalesce(max(seq),0) FROM events WHERE attempt_id=?", params![aid], |r| r.get(0))?;
            let done = json!({"outcome": "interrupted", "text": text, "last_seq": seq});
            c.execute(
                "UPDATE attempts SET phase='uploading',completion=? WHERE id=? AND completion IS NULL AND phase NOT IN ('synced','historical')",
                params![done.to_string(), aid],
            )
            .map(|_| ())
        })
    }

    /// The result recovery sends for `aid`, read again now rather than from an earlier listing: the one saved,
    /// or an interrupted one saved in the same transaction when there is none. None once it is synced or
    /// historical. A turn that finished after the listing keeps its own result.
    pub fn recovery(&self, aid: &str, text: &str) -> Result<Option<Value>, String> {
        self.with(|c| {
            let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let row: Option<(String, Option<String>)> =
                tx.query_row("SELECT phase, completion FROM attempts WHERE id=?", params![aid], |r| Ok((r.get(0)?, r.get(1)?))).optional()?;
            let done = match row {
                None => None,
                Some((phase, _)) if phase == "synced" || phase == "historical" => None,
                Some((_, Some(saved))) => Some(serde_json::from_str(&saved).unwrap_or(Value::Null)),
                Some((_, None)) => {
                    let seq: i64 = tx.query_row("SELECT coalesce(max(seq),0) FROM events WHERE attempt_id=?", params![aid], |r| r.get(0))?;
                    let done = json!({"outcome": "interrupted", "text": text, "last_seq": seq});
                    tx.execute("UPDATE attempts SET phase='uploading',completion=? WHERE id=?", params![done.to_string(), aid])?;
                    Some(done)
                }
            };
            tx.commit()?;
            Ok(done)
        })
    }

    pub fn unfinished(&self) -> Result<Vec<Unfinished>, String> {
        self.with(|c| {
            let mut stmt = c.prepare("SELECT id, payload, phase, completion FROM attempts WHERE phase NOT IN ('synced','historical')")?;
            let rows = stmt.query_map([], |r| Ok(Unfinished { id: r.get(0)?, payload: r.get(1)?, phase: r.get(2)?, completion: r.get(3)? }))?;
            rows.collect()
        })
    }

    /// The files in `files` (path, stamp) not yet reported for this bot at that stamp; remembers them all, and
    /// forgets a file gone from the list so it is reported again if it comes back.
    pub fn unreported(&self, bot: &str, files: &[(String, String)]) -> Result<Vec<String>, String> {
        self.with(|c| {
            let tx = c.transaction()?;
            let seen: std::collections::HashMap<String, String> = {
                let mut q = tx.prepare("SELECT path,stamp FROM left_out_reported WHERE bot=?")?;
                let rows = q.query_map([bot], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
                rows.collect::<rusqlite::Result<_>>()?
            };
            for path in seen.keys().filter(|p| !files.iter().any(|(f, _)| f == *p)) {
                tx.execute("DELETE FROM left_out_reported WHERE bot=? AND path=?", (bot, path))?;
            }
            for (path, stamp) in files {
                tx.execute("INSERT OR REPLACE INTO left_out_reported VALUES(?,?,?)", (bot, path, stamp))?;
            }
            tx.commit()?;
            Ok(files.iter().filter(|(p, s)| seen.get(p) != Some(s)).map(|(p, _)| p.clone()).collect())
        })
    }

    pub fn session(&self, bot: &str, conversation: &str, runtime: &str) -> Result<Option<String>, String> {
        self.with(|c| {
            c.query_row(
                "SELECT thread FROM sessions WHERE bot=? AND conversation=? AND runtime=?",
                params![bot, conversation, runtime],
                |r| r.get(0),
            )
            .optional()
        })
    }

    pub fn save_session(&self, bot: &str, conversation: &str, runtime: &str, thread: &str) -> Result<(), String> {
        self.with(|c| c.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)", params![bot, conversation, runtime, thread]).map(|_| ()))
    }

    /// Drop a failed provider thread without deleting a newer replacement.
    pub fn forget_session(&self, bot: &str, conversation: &str, runtime: &str, thread: &str) -> Result<(), String> {
        self.with(|c| {
            c.execute(
                "DELETE FROM sessions WHERE bot=? AND conversation=? AND runtime=? AND thread=?",
                params![bot, conversation, runtime, thread],
            )
            .map(|_| ())
        })
    }

    /// Whether the Hub cleared this session since it last ran here: its epoch moved (backend/execution.py
    /// `bot_session_epoch` for the bot's shared thread, recorded for conversation "", and `session_epoch` for any
    /// other). Then the session's threads are dropped, in the same transaction that records the new epoch, so a turn
    /// that dies before its new thread is saved still starts fresh on the retry.
    /// A session seen for the first time at epoch 0 was never cleared and resumes. Seen first at a later epoch,
    /// a Clear may have come after its saved thread (one clicked after an upgrade, before the first turn on this
    /// runner): that thread is dropped once, a cold turn rather than a lost Clear.
    pub fn epoch_moved(&self, bot: &str, conversation_id: &str, session: &str, epoch: i64) -> Result<bool, String> {
        self.with(|c| {
            let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let seen: Option<i64> = tx
                .query_row(
                    "SELECT epoch FROM session_epochs WHERE bot=? AND conversation_id=? AND session=?",
                    params![bot, conversation_id, session],
                    |r| r.get(0),
                )
                .optional()?;
            let moved = match seen {
                Some(e) => e != epoch && tx.execute("DELETE FROM sessions WHERE bot=? AND conversation=?", params![bot, session]).map(|_| true)?,
                None => epoch != 0 && tx.execute("DELETE FROM sessions WHERE bot=? AND conversation=?", params![bot, session])? > 0,
            };
            if seen != Some(epoch) {
                tx.execute("INSERT OR REPLACE INTO session_epochs VALUES(?,?,?,?)", params![bot, conversation_id, session, epoch])?;
            }
            tx.commit()?;
            Ok(moved)
        })
    }

    pub fn cursor(&self, thread: &str, message: Option<&str>) -> Result<Option<String>, String> {
        self.with(|c| {
            if let Some(m) = message {
                c.execute("INSERT OR REPLACE INTO session_cursors VALUES(?,?)", params![thread, m])?;
            }
            c.query_row("SELECT message FROM session_cursors WHERE thread=?", params![thread], |r| r.get(0)).optional()
        })
    }

    /// What a thread was last given under `thread` in session_repo_rules (state.py repo_rules): the repo-rules
    /// digest, or under `goal:<thread>` the task goal revision it holds (runner/goals.py task_goal_set).
    pub fn repo_rules(&self, thread: &str, digest: Option<&str>) -> Result<Option<String>, String> {
        self.with(|c| {
            if let Some(d) = digest {
                c.execute("INSERT OR REPLACE INTO session_repo_rules VALUES(?,?)", params![thread, d])?;
            }
            c.query_row("SELECT digest FROM session_repo_rules WHERE thread=?", params![thread], |r| r.get(0)).optional()
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> PathBuf {
        std::env::temp_dir().join(format!("tico-state-{}", uuid::Uuid::new_v4()))
    }

    #[test]
    fn a_moved_epoch_drops_its_sessions_threads_and_an_unchanged_one_keeps_them() {
        let state = State::open(&temp()).unwrap();
        state.save_session("ops", "bot", "claude", "t-old").unwrap();
        state.save_session("ops", "bot", "codex", "x-old").unwrap();
        state.save_session("ops", "task:t1", "claude", "t-task").unwrap();
        // First sight at epoch 0 (never cleared) is only recorded; the same epoch again resumes.
        assert!(!state.epoch_moved("ops", "", "bot", 0).unwrap());
        assert!(!state.epoch_moved("ops", "", "bot", 0).unwrap());
        assert_eq!(state.session("ops", "bot", "claude").unwrap().as_deref(), Some("t-old"));
        // A Clear in any direct chat moves the bot's epoch: the shared thread goes, for every runtime; the task's stays.
        assert!(state.epoch_moved("ops", "", "bot", 1).unwrap());
        assert_eq!(state.session("ops", "bot", "claude").unwrap(), None);
        assert_eq!(state.session("ops", "bot", "codex").unwrap(), None);
        assert_eq!(state.session("ops", "task:t1", "claude").unwrap().as_deref(), Some("t-task"));
        // The new thread then resumes at that epoch.
        state.save_session("ops", "bot", "claude", "t-new").unwrap();
        assert!(!state.epoch_moved("ops", "", "bot", 1).unwrap());
        assert_eq!(state.session("ops", "bot", "claude").unwrap().as_deref(), Some("t-new"));
        // A conversation's own thread moves with that conversation's epoch, apart from the bot's.
        state.save_session("ops", "conversation:c-ana", "claude", "c-old").unwrap();
        assert!(!state.epoch_moved("ops", "c-ana", "conversation:c-ana", 0).unwrap());
        assert!(state.epoch_moved("ops", "c-ana", "conversation:c-ana", 1).unwrap());
        assert_eq!(state.session("ops", "conversation:c-ana", "claude").unwrap(), None);
        assert_eq!(state.session("ops", "bot", "claude").unwrap().as_deref(), Some("t-new"));
        // Another bot's sessions are its own.
        state.save_session("cfo", "bot", "claude", "f-old").unwrap();
        assert!(!state.epoch_moved("cfo", "", "bot", 0).unwrap());
        assert!(state.epoch_moved("ops", "", "bot", 2).unwrap());
        assert_eq!(state.session("cfo", "bot", "claude").unwrap().as_deref(), Some("f-old"));
    }

    #[test]
    fn a_clear_made_before_the_first_turn_on_this_runner_still_starts_a_new_thread() {
        let state = State::open(&temp()).unwrap();
        // Upgraded: a thread saved by an older runner, no epoch row, and the chat cleared since (epoch 1).
        state.save_session("ops", "bot", "claude", "t-before-upgrade").unwrap();
        assert!(state.epoch_moved("ops", "", "bot", 1).unwrap());
        assert_eq!(state.session("ops", "bot", "claude").unwrap(), None);
        // Once: the new thread resumes at that epoch.
        state.save_session("ops", "bot", "claude", "t-new").unwrap();
        assert!(!state.epoch_moved("ops", "", "bot", 1).unwrap());
        assert_eq!(state.session("ops", "bot", "claude").unwrap().as_deref(), Some("t-new"));
        // First sight at a later epoch with no saved thread has nothing to drop.
        assert!(!state.epoch_moved("ops", "c-cara", "task:t9", 3).unwrap());
    }

    #[test]
    fn recovery_keeps_a_result_saved_after_the_listing() {
        let state = State::open(&temp()).unwrap();
        for id in ["done", "lost", "sent"] {
            state.record(&json!({"id": id})).unwrap();
        }
        let listed = state.unfinished().unwrap();
        assert_eq!(listed.len(), 3);
        // After the listing: one turn finishes on its own, one result reaches the server.
        state.finish("done", json!({"outcome": "completed", "text": "hi"}).as_object().unwrap()).unwrap();
        state.phase("sent", "synced").unwrap();
        let why = "Runner restarted during execution";
        assert_eq!(state.recovery("done", why).unwrap().unwrap()["outcome"], "completed");
        assert_eq!(state.recovery("lost", why).unwrap().unwrap()["outcome"], "interrupted");
        assert_eq!(state.recovery("lost", why).unwrap().unwrap()["outcome"], "interrupted");
        assert!(state.recovery("sent", why).unwrap().is_none());
    }

    #[test]
    fn the_outbox_numbers_acks_and_finishes_like_the_python_store() {
        let dir = temp();
        let state = State::open(&dir).unwrap();
        state.record(&json!({"id": "a1", "bot": "ops"})).unwrap();
        state.record(&json!({"id": "a1", "bot": "changed"})).unwrap();
        for i in 0..150 {
            assert_eq!(state.append("a1", "delta", &json!({"text": i})).unwrap(), i + 1);
        }
        let batch = state.pending("a1").unwrap();
        assert_eq!((batch.len(), batch[0]["seq"].as_i64(), batch[99]["seq"].as_i64()), (100, Some(1), Some(100)));
        state.ack("a1", 100).unwrap();
        state.ack("a1", 40).unwrap();
        assert_eq!(state.pending("a1").unwrap()[0]["seq"], 101);
        let done = state.finish("a1", json!({"outcome": "completed"}).as_object().unwrap()).unwrap();
        assert_eq!(done["last_seq"], 150);
        let rows = state.unfinished().unwrap();
        assert_eq!((rows.len(), rows[0].phase.as_str()), (1, "uploading"));
        assert!(rows[0].payload.contains("\"ops\""));
        state.phase("a1", "synced").unwrap();
        assert!(state.unfinished().unwrap().is_empty());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn python_reads_what_rust_wrote() {
        let dir = temp();
        let state = State::open(&dir).unwrap();
        state.record(&json!({"id": "a2", "bot": "ops"})).unwrap();
        state.append("a2", "message", &json!({"text": "hi"})).unwrap();
        state.save_session("ops", "bot", "claude", "thread-1").unwrap();
        state.interrupted("a2", "Runner restarted during execution").unwrap();
        drop(state);
        let script = "import sqlite3,sys,json\nc=sqlite3.connect(sys.argv[1])\nprint(c.execute('pragma journal_mode').fetchone()[0])\nprint(json.loads(c.execute('select completion from attempts').fetchone()[0])['outcome'])\nprint(c.execute('select thread from sessions').fetchone()[0])\nprint(len(c.execute(\"select name from sqlite_master where type='table'\").fetchall()))";
        let out = std::process::Command::new("python3").args(["-c", script]).arg(dir.join("runner.sqlite")).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).split_whitespace().collect::<Vec<_>>(), ["wal", "interrupted", "thread-1", "11"]);
        std::fs::remove_dir_all(dir).ok();
    }
}
