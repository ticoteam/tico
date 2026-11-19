"""A second review's named commits (backend/task_requirements.py `_named_commits`) read the review room's own messages by
the room's index. With no sqlite_stat1, as on TIDY, SQLite took the kind index for its date order and read every say in
the company for each review: 19 ms a review, eleven reviews in one Needs you build (Nov 19)."""

import sqlite3

from backend import task_requirements as R
from backend.config import Settings
from backend.store import Store


def test_a_reviews_named_commits_read_the_review_rooms_messages_by_its_index(tmp_path):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    c = sqlite3.connect(path)
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'").fetchone():
        c.execute("DELETE FROM sqlite_stat1")  # no stats, as TIDY has none
        c.commit()
        c.close()
        c = sqlite3.connect(path)
    seen = []
    c.set_trace_callback(seen.append)
    try:
        assert R._named_commits(c, {"id": "task-1", "conversation_id": "room-1"}, []) == []
    finally:
        c.set_trace_callback(None)
    [sql] = [s for s in seen if "FROM messages m" in s]
    plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, ("x",) * sql.count("?"))]
    assert "messages_conversation" in plan[0], plan
