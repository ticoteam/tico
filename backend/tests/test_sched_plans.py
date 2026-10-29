"""Plans of the scheduler's and task page's periodic reads, on a migrated store with TIDY's planner statistics.

TIDY's sqlite_stat1 says messages has 16k rows (analysed long ago; it has 72k now) and jobs and attempts have none. With
those stats the planner scans messages and seeks each job, or scans every attempt, so each of these reads cost 40-150 ms
under the write lock. Each statement must name the partial index that keeps it to the live rows."""
from backend import turn_fixer
from backend import wake_presets as W
from backend.tests.test_api import api  # noqa: F401  (api is a fixture)
from backend import views

NOW = "2026-10-29T14:00:00.000Z"
# TIDY's stale statistics (sqlite_stat1, 2026-10-29): only messages has rows.
TIDY_STATS = [("messages", "messages_pending_conversation", "212 3"),
              ("messages", "messages_from_kind", "16209 181 99"),
              ("messages", "messages_reply_kind", "16209 6 6"),
              ("messages", "slack_task_completion_notices", "23 1 1 1"),
              ("messages", "messages_open_asks", "16209 173 35 28 28"),
              ("messages", "messages_conversation", "16209 8 1"),
              ("messages", "messages_to_delivered", "16209 173 3"),
              ("messages", "sqlite_autoindex_messages_1", "16209 1")]


def tidy_stats(api):
    """Reproduce TIDY's planner statistics on this store's schema."""
    with api.app.state.store.transaction() as c:
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1")
        c.executemany("INSERT INTO sqlite_stat1 VALUES(?,?,?)", TIDY_STATS)


def plan(api, sql, args=()):
    with api.app.state.store.read() as c:
        return " | ".join(str(row[-1]) for row in c.execute("EXPLAIN QUERY PLAN " + sql, args))


def test_held_tick_reads_walk_held_queued_jobs(api):
    # The planner's choice depends on sizes and statistics, so the hint itself is pinned: without INDEXED BY the plan
    # scanned messages on TIDY (the old statements had no hint).
    tidy_stats(api)
    for sql, args in ((W.HELD_NEW_SQL, ()), (W.HELD_DUE_SQL, (NOW,))):
        assert "INDEXED BY jobs_held_queued" in sql
        text = plan(api, sql, args)
        assert "USING INDEX jobs_held_queued" in text, text
        assert "SCAN m" not in text, text


def test_task_wait_walks_queued_jobs(api):
    tidy_stats(api)
    assert "INDEXED BY jobs_queued" in views.TASK_WAIT_SQL
    text = plan(api, views.TASK_WAIT_SQL, ("tk",))
    assert "USING INDEX jobs_queued" in text, text
    assert "SCAN m" not in text, text


def test_dead_turn_sweep_walks_live_runs(api):
    tidy_stats(api)
    assert "INDEXED BY attempts_live_lease" in turn_fixer.DEAD_SQL
    text = plan(api, turn_fixer.DEAD_SQL, (NOW,))
    assert "USING INDEX attempts_live_lease" in text, text
    assert "attempts_created" not in text, text
