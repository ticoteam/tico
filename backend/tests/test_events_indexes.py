"""The events table's indexes: the redundant one (events_target_action) stays dropped after every start, and the reads
that used it plan on events_action_target (action, target, ts) with no scan of events. Each plan check names the
index the read must use, so on the old index the credential and decision reads fail."""
import pytest

from backend import task_privacy
from backend.config import Settings
from backend.store import Store

DROPPED = {"events_target_action"}
KEPT = {"events_ts", "events_task_origin", "events_action_target", "events_run_task", "events_tag_attach"}


def open_store(path):
    s = Store(Settings(db_path=path))
    s.initialize(seed_market=False)
    return s


def event_indexes(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'")}


def plan(c, sql, args=()):
    return " ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))


RUN_EVENTS = task_privacy.RUN_TASK_EVENTS_SQL
READS = [
    # Credentials last used (backend/credentials.py): one target, two actions, newest first.
    ("credential last used",
     "SELECT ts,actor,detail_json FROM events WHERE target=? AND action IN (?,?) AND actor NOT LIKE 'human:%' "
     "ORDER BY ts DESC LIMIT 1", ("cred-1", "credential.revealed", "credential.sent_to_computer"),
     "events_action_target"),
    # A judge's decision outcome (backend/judge.py): one action and target.
    ("decision outcome",
     "SELECT id, detail_json FROM events WHERE target=? AND action='decision.outcome' LIMIT 1", ("call-1",),
     "events_action_target"),
    # The run events naming a run's tasks (task_privacy, privacy_index): the partial index, kept (2 MB, every task page).
    ("run events of a run",
     f"SELECT detail_json FROM events WHERE action IN ({RUN_EVENTS}) AND target=?", ("att-1",),
     "events_run_task"),
    # The task-origin reads (hubdb.TASK_ORIGIN_SQL): actor-led, must stay on events_task_origin.
    ("task origin",
     "SELECT target,detail_json FROM events WHERE actor=? AND action='task.origin' AND target IN (?,?) "
     "ORDER BY target,ts DESC", ("bot:ops", "task-1", "task-2"),
     "events_task_origin"),
    # The event sweep walks events_ts forward (store.sweep_events).
    ("event sweep",
     "SELECT rowid,ts FROM events INDEXED BY events_ts WHERE ts>=? AND ts<? AND action IN (?,?) ORDER BY ts LIMIT ?",
     ("2000-01-01", "2026-01-01", "sql.query", "status.set", 500), "events_ts"),
]


@pytest.mark.parametrize("name,sql,args,index", READS, ids=[r[0] for r in READS])
def test_read_plans_on_the_kept_index_and_never_scans_events(tmp_path, name, sql, args, index):
    s = open_store(tmp_path / "hub.sqlite")
    with s.read() as c:
        text = plan(c, sql, args)
    assert index in text, text
    assert "SCAN events" not in text, text
    assert not any(dropped in text for dropped in DROPPED), text


def test_the_redundant_indexes_stay_dropped_after_every_start(tmp_path):
    path = tmp_path / "hub.sqlite"
    open_store(path)
    s = open_store(path)            # a second start runs every migration again
    with s.read() as c:
        present = event_indexes(c)
    assert not DROPPED & present, present
    assert KEPT <= present, present


def test_an_upgraded_database_loses_the_redundant_indexes_on_start(tmp_path):
    path = tmp_path / "hub.sqlite"
    s = open_store(path)
    # What an older release left behind: both indexes exist, as its migrations created them.
    with s.transaction() as c:
        c.execute("CREATE INDEX events_target_action ON events(target,action)")
    s = open_store(path)
    with s.read() as c:
        present = event_indexes(c)
    assert not DROPPED & present, present
    assert KEPT <= present, present
