"""The runner's local SQLite file, shared with the Rust runner (runner-rs/src/state.rs, which owns turns and
sessions): the side jobs' import cursors and pending closes here; never a shared task database."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import isolation


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
                c.execute(
                    "INSERT INTO imports(source,external_id,phase,meeting_id) VALUES(?,?,?,?) "
                    "ON CONFLICT(source,external_id) DO UPDATE SET phase=excluded.phase,"
                    "meeting_id=coalesce(excluded.meeting_id,imports.meeting_id)",
                    (source, external_id, phase, meeting_id),
                )
            row = c.execute(
                "SELECT phase FROM imports WHERE source=? AND external_id=?", (source, external_id)
            ).fetchone()
            return row[0] if row else None

    def close_pending(
        self, resource_type=None, external_id=None, *, created=None, checked=None, remove=False, limit=100
    ):
        """Provider activities to revisit when a transcript was not yet available."""
        with self.connect() as c:
            if resource_type and external_id and remove:
                c.execute(
                    "DELETE FROM close_transcript_pending WHERE resource_type=? AND external_id=?",
                    (resource_type, external_id),
                )
            elif resource_type and external_id and created is not None:
                c.execute(
                    "INSERT INTO close_transcript_pending VALUES(?,?,?,?) "
                    "ON CONFLICT(resource_type,external_id) DO UPDATE SET last_checked=excluded.last_checked",
                    (resource_type, external_id, created, checked or created),
                )
            if resource_type and external_id:
                row = c.execute(
                    "SELECT * FROM close_transcript_pending WHERE resource_type=? AND external_id=?",
                    (resource_type, external_id),
                ).fetchone()
                return dict(row) if row else None
            return [
                dict(row)
                for row in c.execute("SELECT * FROM close_transcript_pending ORDER BY last_checked LIMIT ?", (limit,))
            ]
