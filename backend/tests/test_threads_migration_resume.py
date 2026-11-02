"""A threads migration that stops partway (a crash, a restart) resumes and ends with the same rows as one that ran
straight through. The backfill commits in chunks with their progress, and every step is idempotent per row, so the
resumed run neither skips a row nor stamps one twice. Spec section 4 (`_threads-plan.md`).
"""

import random
import shutil
import sqlite3

import pytest

from backend import hubdb, threads


def raw(path):
    """The DB with no migration run: autocommit, as hubdb.connect opens it, but without running the migrations."""
    return sqlite3.connect(str(path), isolation_level=None)


def pre_threads_hub(path):
    """A hub DB as it was before threads: the schema is there, the migration never ran, the rows carry no threads
    stamps, and no deliveries exist yet. hubdb.connect runs the migrations, so the reset is done after it."""
    conn = hubdb.connect(str(path))
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                                "AND name LIKE 'threads\\_%' ESCAPE '\\'").fetchall():
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute("DELETE FROM registry_metadata WHERE key IN ('threads-migration','threads-migration-progress')")
    conn.execute("DELETE FROM message_deliveries")
    conn.execute("UPDATE conversations SET root_id=NULL, path=NULL, audience=NULL, owner_actor=NULL")
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM conversations")
    rng = random.Random(5)
    humans = [f"human:h{i}" for i in range(4)]
    bots = [f"bot:b{i}" for i in range(5)]
    conversations = [("c-shared-open-1", "shared", "b1"), ("c-shared-old", "shared", "b1"),
                     ("c-shared-open-2", "shared", "b2"), ("c-personal", "personal", "assistant"),
                     ("c-task", "task", None), ("c-direct", "direct", None)]
    for cid, scope, room in conversations:
        closed = "2025-02-01" if cid == "c-shared-old" else None
        conn.execute("INSERT INTO conversations(id,kind,subject,task_id,participants_json,created,last_message_at,"
                     "closed_at,scope,owner_actor,room_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (cid, "task" if scope == "task" else "direct", "", "t-1" if scope == "task" else None,
                      '["human:h0","bot:b0"]', "2025-01-01", None, closed, scope,
                      humans[0] if scope == "personal" else None, room))
    for n in range(240):
        cid = conversations[n % len(conversations)][0]
        refs = rng.choice([None, '{"quiet": true}', '{"task": "t-1", "comment": true}', '{"via": "x"}',
                           '{"task": "t-1"}'])
        conn.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,in_reply_to,"
                     "created,delivered_at,read_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"m{n:04d}", cid, rng.choice(humans + bots), rng.choice([None, None, *humans, *bots]),
                      "say", f"body {n}", refs, f"m{n - 1:04d}" if n and rng.random() < 0.2 else None,
                      f"2025-01-02T00:{n // 60:02d}:{n % 60:02d}Z",
                      f"2025-01-02T00:{n // 60:02d}:{n % 60:02d}Z" if rng.random() < 0.8 else None,
                      None, f"2025-01-03T00:00:00Z" if rng.random() < 0.05 else None))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")      # the copy below takes the main file only
    conn.close()
    return raw(path)


def reader_rows(conn):
    """What the migration writes, without the random ids and the migration-time stamps (which differ run to run)."""
    queries = {
        "conversations": "SELECT id,audience,owner_actor,root_id,path,last_seq,last_unread_seq,"
                         "archived_at IS NOT NULL FROM conversations ORDER BY id",
        "messages": "SELECT id,conversation_id,seq,restricted,legacy_audience,deleted_at FROM messages ORDER BY id",
        "deliveries": "SELECT message_id,recipient,mode,reason,cause,state,revision,delivered_at,read_at "
                      "FROM message_deliveries ORDER BY message_id,recipient",
        "sequences": "SELECT name,value FROM sequences ORDER BY name",
    }
    return {name: [tuple(row) for row in conn.execute(sql).fetchall()] for name, sql in queries.items()}


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(threads, "MIGRATION_CHUNK", 25)     # several chunks per step, so a crash lands between them


def test_an_interrupted_migration_resumes_to_the_same_rows(tmp_path, small_chunks, monkeypatch):
    pre_threads_hub(tmp_path / "a.db").close()
    shutil.copy(tmp_path / "a.db", tmp_path / "b.db")
    straight = raw(tmp_path / "b.db")
    assert threads.migrate(straight) is True
    expected = reader_rows(straight)
    straight.close()

    crashing = raw(tmp_path / "a.db")
    real = threads._set_progress
    calls = []

    def crash_after_some_chunks(c, step, rowid):
        calls.append(step)
        if len(calls) == 9:                                # a few chunks have committed, this one has not
            raise RuntimeError("simulated crash")
        real(c, step, rowid)

    monkeypatch.setattr(threads, "_set_progress", crash_after_some_chunks)
    with pytest.raises(RuntimeError, match="simulated crash"):
        threads.migrate(crashing)
    monkeypatch.setattr(threads, "_set_progress", real)
    assert not threads.migrated(crashing), "the crash should leave the migration unrecorded"
    assert threads._progress(crashing), "the committed chunks should have left their progress"

    assert threads.migrate(crashing) is True                # the restart resumes from that progress
    assert reader_rows(crashing) == expected
    assert threads.migrate(crashing) is False               # and a later start is a no-op
    crashing.close()


def test_message_indexes_wait_for_the_backfill(tmp_path, small_chunks):
    """ensure runs before migrate on every start; the message indexes it would build over unstamped rows are left to
    migrate, which builds them after the backfill."""
    conn = pre_threads_hub(tmp_path / "c.db")
    conn.execute("DROP INDEX IF EXISTS messages_seq")
    conn.execute("DROP INDEX IF EXISTS messages_thread_seq")
    conn.execute("DROP INDEX IF EXISTS messages_origin")
    conn.execute("DROP INDEX IF EXISTS messages_legacy_audience")
    threads.ensure(conn)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert not names & {"messages_seq", "messages_thread_seq", "messages_origin", "messages_legacy_audience"}
    threads.migrate(conn)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"messages_seq", "messages_thread_seq", "messages_origin", "messages_legacy_audience"} <= names
    conn.close()
