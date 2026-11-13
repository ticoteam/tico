"""Plans of the scheduler's and task page's periodic reads, on a migrated store with TIDY's planner statistics.

TIDY's sqlite_stat1 says messages has 16k rows (analysed long ago; it has 72k now) and jobs and attempts have none. With
those stats the planner scans messages and seeks each job, or scans every attempt, so each of these reads cost 40-150 ms
under the write lock. Each statement must name the partial index that keeps it to the live rows."""

from backend import turn_fixer
from backend import wake_presets as W
from backend.tests.helpers.api import post  # noqa: F401  (api is a fixture)
from backend import views

NOW = "2026-10-29T14:00:00.000Z"
# TIDY's stale statistics (sqlite_stat1, 2026-10-29): only messages has rows.
TIDY_STATS = [
    ("messages", "messages_from_kind", "16209 181 99"),
    ("messages", "messages_reply_kind", "16209 6 6"),
    ("messages", "slack_task_completion_notices", "23 1 1 1"),
    ("messages", "messages_conversation", "16209 8 1"),
    ("messages", "messages_to_delivered", "16209 173 3"),
    ("messages", "sqlite_autoindex_messages_1", "16209 1"),
]


def tidy_stats(api):
    """Reproduce TIDY's planner statistics on this store's schema."""
    with api.app.state.store.transaction() as c:
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1")
        c.executemany("INSERT INTO sqlite_stat1 VALUES(?,?,?)", TIDY_STATS)


def plan(api, sql, args=()):
    with api.app.state.store.read() as c:
        return " | ".join(str(row[-1]) for row in c.execute("EXPLAIN QUERY PLAN " + sql, args))


def register_worktree_computer(c, computer):
    # A missing registration is retired; a registered computer needs no heartbeat to keep its trees.
    c.execute(
        "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
        (computer, computer, computer, NOW),
    )


def test_worktree_inventory_bot_state_matches_each_owner(api):
    # The bot join seeks a slug only for an owner that is exactly 'bot:<slug>'; other owners and empty slugs match nothing.
    import json
    import sqlite3
    from backend import worktrees

    db = str(api.app.state.store.settings.db_path)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    register_worktree_computer(c, 'wt-computer')
    assert c.execute("SELECT last_seen_at FROM runner_states WHERE runner_id='wt-computer'").fetchone()[0] is None
    now = '2026-10-29T00:00:00Z'
    c.execute(
        "INSERT INTO bots(id,slug,state,created_at) VALUES('wt-archived','wt-archived','archived',?),('wt-active','wt-active','active',?)",
        (now, now),
    )
    c.execute(
        "INSERT INTO tasks(id,title,owner_id,requester_id,status,created_at,updated_at) VALUES('wt-task','t','bot:wt-active','human:ana','open',?,?)",
        (now, now),
    )
    cases = {
        'detail': ('wt-a', json.dumps({'owner': 'bot:wt-archived'}), 'human:ana'),
        'added': ('wt-b', '{}', 'bot:wt-archived'),
        'task': ('wt-c', '{}', 'human:ana'),
        'empty': ('wt-d', json.dumps({'owner': 'bot:'}), 'human:ana'),
        'human': ('wt-e', json.dumps({'owner': 'human:ana'}), 'human:ana'),
    }
    for key, (link, detail, added) in cases.items():
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,computer_id,detail) "
            "VALUES(?,'wt-task','worktree','u','present',?,?,'wt-computer',?)",
            (link, added, now, detail),
        )
    rows = {r['id']: (r['owner_id'], r['bot_state']) for r in worktrees.inventory(c, 'wt-computer')}
    assert rows == {
        'wt-a': ('bot:wt-archived', 'archived'),
        'wt-b': ('bot:wt-archived', 'archived'),
        'wt-c': ('bot:wt-active', 'active'),
        'wt-d': ('bot:', 'active'),
        'wt-e': ('human:ana', 'active'),
    }, rows
    c.execute("UPDATE runners SET revoked_at=? WHERE id='wt-computer'", (now,))
    assert worktrees.inventory(c, 'wt-computer') == []
    c.execute("DELETE FROM runners WHERE id='wt-computer'")
    assert worktrees.inventory(c, 'wt-computer') == []
    c.close()
