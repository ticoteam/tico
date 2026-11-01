"""Plans of the scheduler's and task page's periodic reads, on a migrated store with TIDY's planner statistics.

TIDY's sqlite_stat1 says messages has 16k rows (analysed long ago; it has 72k now) and jobs and attempts have none. With
those stats the planner scans messages and seeks each job, or scans every attempt, so each of these reads cost 40-150 ms
under the write lock. Each statement must name the partial index that keeps it to the live rows."""
from backend import turn_fixer
from backend import wake_presets as W
from backend.tests.test_api import api, post  # noqa: F401  (api is a fixture)
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


def test_health_held_totals_walk_live_runs(api):
    # Unhinted, the GROUP BY picked attempts_runner_finished and walked every attempt ever run (48 ms on Tico Team).
    from backend import start_places
    tidy_stats(api)
    assert "INDEXED BY attempts_live_lease" in start_places.HELD_TOTALS_SQL
    text = plan(api, start_places.HELD_TOTALS_SQL, (NOW, NOW))
    assert "USING INDEX attempts_live_lease" in text, text
    assert "attempts_runner_finished" not in text, text


def test_needs_you_withdrawn_count_seeks_replaced_asks(api):
    # On messages_to_delivered it walked every message to the person (31k on Tico Team) for a few hundred asks.
    tidy_stats(api)
    text = plan(api, views.WITHDRAWN_ASKS_SQL, ("chris", NOW))
    assert "SEARCH s USING INDEX messages_superseded_asks_to" in text, text


def test_worktree_inventory_seeks_one_computers_worktrees(api):
    # worktrees.inventory runs on every heartbeat; without a computer index it scanned every worktree link ever kept.
    import sqlite3
    from backend import worktrees
    c = sqlite3.connect(str(api.app.state.store.settings.db_path), isolation_level=None)
    c.row_factory = sqlite3.Row
    seen = []
    c.set_trace_callback(lambda sql: seen.append(sql) if sql.lstrip().startswith("SELECT l.*") else None)
    worktrees.inventory(c, "computer-1")
    c.set_trace_callback(None)
    [sql] = seen
    text = " | ".join(str(row[-1]) for row in c.execute("EXPLAIN QUERY PLAN " + sql))
    assert "USING INDEX task_links_worktree_computer" in text, text
    # The bot join seeks bots' slug key; a scan of bots would repeat for every worktree link the computer kept.
    assert "SCAN b" not in text, text
    c.close()


def test_worktree_inventory_bot_state_matches_each_owner(api):
    # The bot join seeks a slug only for an owner that is exactly 'bot:<slug>'; other owners and empty slugs match nothing.
    import json
    import sqlite3
    from backend import worktrees
    db = str(api.app.state.store.settings.db_path)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    now = '2026-10-29T00:00:00Z'
    c.execute("INSERT INTO bots(slug,state,created_at) VALUES('wt-archived','archived',?),('wt-active','active',?)", (now, now))
    c.execute("INSERT INTO tasks(id,title,owner,requester,status,created_at,updated_at) VALUES('wt-task','t','bot:wt-active','human:ana','open',?,?)", (now, now))
    cases = {
        'detail': ('wt-a', json.dumps({'owner': 'bot:wt-archived'}), 'human:ana'),
        'added': ('wt-b', '{}', 'bot:wt-archived'),
        'task': ('wt-c', '{}', 'human:ana'),
        'empty': ('wt-d', json.dumps({'owner': 'bot:'}), 'human:ana'),
        'human': ('wt-e', json.dumps({'owner': 'human:ana'}), 'human:ana'),
    }
    for key, (link, detail, added) in cases.items():
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,computer_id,detail) "
                  "VALUES(?,'wt-task','worktree','u','present',?,?,'wt-computer',?)", (link, added, now, detail))
    rows = {r['id']: (r['owner'], r['bot_state']) for r in worktrees.inventory(c, 'wt-computer')}
    assert rows == {'wt-a': ('bot:wt-archived', 'archived'), 'wt-b': ('bot:wt-archived', 'archived'),
                    'wt-c': ('bot:wt-active', 'active'), 'wt-d': ('bot:', 'active'),
                    'wt-e': ('human:ana', 'active')}, rows
    c.close()


def test_queue_refusal_scan_reads_every_bots_state_in_one_query(api):
    # The scheduler's claim observations: one read for the targets, one for all their saved states (not one per bot).
    from backend import queue_refusals
    from backend.tests.test_task_lanes import task_bot
    task_bot(api, lanes=True, others=("finance", "inbox"))
    for bot in ("ops", "finance", "inbox"):
        post(api, "chat/" + bot, {"text": "Check the queue."})
    from backend.store import H
    store = api.app.state.store
    seen = []
    with store.read_transaction() as c:
        c.set_trace_callback(lambda sql: seen.append(sql))
        queue_refusals.pending(c, store.settings, H.now(), [])
        c.set_trace_callback(None)
    assert len([s for s in seen if "queue-refusal:" in s]) <= 2, seen
