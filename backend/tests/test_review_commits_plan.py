"""A Review stage's gate reads a second review's messages from its own conversation (task_requirements._named_commits),
never by walking every said message for the order. With no sqlite_stat1 the planner took the (kind, created_at, id)
index for `ORDER BY m.created_at`: 20 ms a review on TIDY's 39k messages (Nov 19), eleven reviews in every Needs-you
read of the Floor, Home and the bot list."""

import sqlite3

from backend import task_requirements as R
from backend.config import Settings
from backend.hubdb import MESSAGE_TASK_SQL
from backend.store import Store


def test_a_reviews_messages_are_sought_by_its_conversation_without_stats(tmp_path):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    with sqlite3.connect(path) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()
        sql = R._review_messages_sql(MESSAGE_TASK_SQL)
        plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("conversation", "task"))]
    assert plan[0].startswith("SEARCH m USING INDEX") and "(conversation_id=?)" in plan[0], plan
