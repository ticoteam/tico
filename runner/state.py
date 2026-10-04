"""Durable local event outbox and session references; never a shared task database."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import isolation


# The default provider thread. BotOps isolates jobs by default; other bots can
# opt into the same isolation with session: task.
BOT_THREAD = "bot"


def session_key(config, attempt):
    """The provider thread this turn continues. BotOps and bots set to `session: task` get one
    per task, so a review never carries every earlier review into each call; a wake about the
    same task (a stalled nudge, a reconcile, a reply) resumes it. A wake with no task keys on
    its conversation, so a person's follow-up still lands where the first answer was."""
    mode = (config or {}).get("session") or ("task" if attempt.get("bot") == "botops" else "bot")
    if mode != "task":
        return BOT_THREAD
    task = (attempt.get("task") or {}).get("id")
    if task:
        return "task:" + task
    conversation = (attempt.get("conversation") or {}).get("id")
    return "conversation:" + conversation if conversation else BOT_THREAD


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if isolation.enabled():
            # Mirrors are public read-only; the rest of the state remains private.
            # Traversal alone exposes no directory listing; existing entries stay private.
            for entry in self.directory.iterdir():
                if entry.name != 'mirrors' and not entry.is_symlink():
                    os.chmod(entry, 0o700 if entry.is_dir() else 0o600)
            os.chmod(self.directory, 0o711)
        else:
            os.chmod(self.directory, 0o700)
        self.path = self.directory / "runner.sqlite"
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS attempts(
                  id TEXT PRIMARY KEY, payload TEXT NOT NULL, phase TEXT NOT NULL,
                  completion TEXT, ack INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events(
                  attempt_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
                  payload TEXT NOT NULL, PRIMARY KEY(attempt_id,seq));
                CREATE TABLE IF NOT EXISTS sessions(
                  bot TEXT NOT NULL, conversation TEXT NOT NULL, runtime TEXT NOT NULL,
                  thread TEXT NOT NULL, PRIMARY KEY(bot,conversation,runtime));
                CREATE TABLE IF NOT EXISTS inputs(
                  attempt_id TEXT NOT NULL, message_id TEXT NOT NULL, phase TEXT NOT NULL,
                  PRIMARY KEY(attempt_id,message_id));
                CREATE TABLE IF NOT EXISTS session_cursors(
                  thread TEXT PRIMARY KEY, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS import_cursors(
                  source TEXT PRIMARY KEY, cursor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports(
                  source TEXT NOT NULL, external_id TEXT NOT NULL, phase TEXT NOT NULL,
                  meeting_id TEXT, PRIMARY KEY(source,external_id));
                CREATE TABLE IF NOT EXISTS close_transcript_pending(
                  resource_type TEXT NOT NULL, external_id TEXT NOT NULL,
                  created TEXT NOT NULL, last_checked TEXT NOT NULL,
                  PRIMARY KEY(resource_type,external_id));
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=FULL")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def record(self, attempt):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO attempts(id,payload,phase) VALUES(?,?,'claimed')",
                      (attempt["id"], json.dumps(attempt)))

    def input_phase(self, aid, mid, phase=None):
        with self.connect() as c:
            if phase:
                c.execute("INSERT INTO inputs VALUES(?,?,?) ON CONFLICT(attempt_id,message_id) DO UPDATE SET phase=excluded.phase", (aid, mid, phase))
            row = c.execute("SELECT phase FROM inputs WHERE attempt_id=? AND message_id=?", (aid, mid)).fetchone()
            return row[0] if row else None

    def phase(self, aid, phase):
        with self.connect() as c:
            c.execute("UPDATE attempts SET phase=? WHERE id=?", (phase, aid))

    def append(self, aid, kind, payload):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            seq = c.execute("SELECT coalesce(max(seq),0)+1 FROM events WHERE attempt_id=?", (aid,)).fetchone()[0]
            c.execute("INSERT INTO events VALUES(?,?,?,?)", (aid, seq, kind, json.dumps(payload)))
            return seq

    def pending(self, aid):
        with self.connect() as c:
            rows = c.execute("SELECT e.* FROM events e JOIN attempts a ON a.id=e.attempt_id "
                             "WHERE e.attempt_id=? AND e.seq>a.ack ORDER BY e.seq LIMIT 100", (aid,)).fetchall()
            return [{"seq": r["seq"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows]

    def ack(self, aid, seq):
        with self.connect() as c:
            c.execute("UPDATE attempts SET ack=max(ack,?) WHERE id=?", (seq, aid))

    def finish(self, aid, completion):
        with self.connect() as c:
            seq = c.execute("SELECT coalesce(max(seq),0) FROM events WHERE attempt_id=?", (aid,)).fetchone()[0]
            completion = {**completion, "last_seq": seq}
            c.execute("UPDATE attempts SET phase='uploading',completion=? WHERE id=?", (json.dumps(completion), aid))
        return completion

    def unfinished(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM attempts WHERE phase NOT IN ('synced','historical')")]

    def session(self, bot, conversation, runtime):
        with self.connect() as c:
            row = c.execute("SELECT thread FROM sessions WHERE bot=? AND conversation=? AND runtime=?",
                            (bot, conversation, runtime)).fetchone()
            return row[0] if row else None

    def save_session(self, bot, conversation, runtime, thread):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)", (bot, conversation, runtime, thread))

    def forget_session(self, bot, conversation, runtime, thread):
        """Drop a failed provider thread without deleting a newer replacement."""
        with self.connect() as c:
            c.execute("DELETE FROM sessions WHERE bot=? AND conversation=? AND runtime=? AND thread=?",
                      (bot, conversation, runtime, thread))

    def cursor(self, thread, message=None):
        with self.connect() as c:
            if message is not None:
                c.execute("INSERT OR REPLACE INTO session_cursors VALUES(?,?)", (thread, message))
            row = c.execute("SELECT message FROM session_cursors WHERE thread=?", (thread,)).fetchone()
            return row[0] if row else None

    def import_cursor(self, source, cursor=None):
        """How far an importer has read its source, so a restart never re-reads the world."""
        with self.connect() as c:
            if cursor is not None:
                c.execute("INSERT OR REPLACE INTO import_cursors VALUES(?,?)", (source, str(cursor)))
            row = c.execute("SELECT cursor FROM import_cursors WHERE source=?", (source,)).fetchone()
            return row[0] if row else None

    def import_phase(self, source, external_id, phase=None, meeting_id=None):
        """What this machine has already done with one outside record: never import it twice."""
        with self.connect() as c:
            if phase:
                c.execute("INSERT INTO imports(source,external_id,phase,meeting_id) VALUES(?,?,?,?) "
                          "ON CONFLICT(source,external_id) DO UPDATE SET phase=excluded.phase,"
                          "meeting_id=coalesce(excluded.meeting_id,imports.meeting_id)",
                          (source, external_id, phase, meeting_id))
            row = c.execute("SELECT phase FROM imports WHERE source=? AND external_id=?",
                            (source, external_id)).fetchone()
            return row[0] if row else None

    def close_pending(self, resource_type=None, external_id=None, *, created=None, checked=None,
                      remove=False, limit=100):
        """Provider activities to revisit when a transcript was not yet available."""
        with self.connect() as c:
            if resource_type and external_id and remove:
                c.execute("DELETE FROM close_transcript_pending WHERE resource_type=? AND external_id=?",
                          (resource_type, external_id))
            elif resource_type and external_id and created is not None:
                c.execute("INSERT INTO close_transcript_pending VALUES(?,?,?,?) "
                          "ON CONFLICT(resource_type,external_id) DO UPDATE SET last_checked=excluded.last_checked",
                          (resource_type, external_id, created, checked or created))
            if resource_type and external_id:
                row = c.execute("SELECT * FROM close_transcript_pending WHERE resource_type=? AND external_id=?",
                                (resource_type, external_id)).fetchone()
                return dict(row) if row else None
            return [dict(row) for row in c.execute(
                "SELECT * FROM close_transcript_pending ORDER BY last_checked LIMIT ?", (limit,))]
