"""Needs you's messages to answer (thread_attention._addressed) plan from the person's deliveries whatever sqlite_stat1
says. TIDY's stale stats (messages analysed when small) once put messages first: a scan of every message a call,
which grew to minutes and queued every other request behind it (v0.5.56, Nov 3)."""
import sqlite3

from backend import thread_attention as TA
from backend import threads
from backend.auth import Identity
from backend.config import Settings
from backend.store import Store

STALE = [("messages", None, "16000"), ("messages", "messages_conversation", "16000 2"),
         ("message_deliveries", None, "5000000"), ("conversations", None, "900000")]


def test_addressed_reads_the_persons_deliveries_first_even_with_stale_stats(tmp_path):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    c = sqlite3.connect(path)
    c.execute("ANALYZE")
    c.execute("DELETE FROM sqlite_stat1")
    c.executemany("INSERT INTO sqlite_stat1(tbl, idx, stat) VALUES (?,?,?)", STALE)
    c.commit()
    c.close()
    c = sqlite3.connect(path)              # a fresh connection loads the stale stats
    c.row_factory = sqlite3.Row
    seen = []
    c.set_trace_callback(seen.append)
    try:
        assert TA._addressed(c, "human:ana") == []
    finally:
        c.set_trace_callback(None)
    [sql] = [s for s in seen if "FROM message_deliveries d" in s]
    plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("human:ana",) * sql.count("?"))]
    assert "message_deliveries_addressed" in plan[0], plan
    assert not any(p.startswith(("SCAN m", "SCAN cv", "SCAN d")) for p in plan), plan


def test_subtree_unread_seeks_human_messages_and_the_readers_deliveries_with_stale_stats(tmp_path):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    with sqlite3.connect(path) as c:
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1")
        c.executemany("INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES(?,?,?)", STALE)
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        v = threads.Viewer(None, Identity("human:ana", "human"))
        v._backfill = 0
        sql, args = threads._subtree_unread_sql(c, v, ["root"])
        plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args)]
    assert any("SEARCH m USING INDEX messages_thread_human" in p for p in plan), plan
    assert any(p.startswith("SEARCH d ") and "recipient=?" in p for p in plan), plan
    assert not any(p.startswith(("SCAN m", "SCAN d")) for p in plan), plan
