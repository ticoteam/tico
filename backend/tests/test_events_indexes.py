"""The events table's indexes: the redundant one (events_target_action) and the uncovered run index (events_run_task)
stay dropped after every start, and every read plans on the index named for it with no scan of events. Each plan
check names the index the read must use, so on the old indexes the credential, decision and run reads fail.

events_task_origin (actor, action, target, ts) stays whole: reads by actor alone (a person's first action, whether
anyone left history) need it on every action, so a partial index would scan events for them."""
import pytest

from backend import judge as B
from backend import task_privacy
from backend.config import Settings
from backend.store import Store

DROPPED = {"events_target_action", "events_run_task"}
KEPT = {"events_ts", "events_task_origin", "events_action_target", "events_action_ts", "events_run_detail",
        "events_tag_attach"}


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
     "SELECT created_at,actor,detail FROM events WHERE target=? AND action IN (?,?) AND actor NOT LIKE 'human:%' "
     "ORDER BY created_at DESC LIMIT 1", ("cred-1", "credential.revealed", "credential.sent_to_computer"),
     "events_action_target"),
    # A judge's decision outcome (backend/judge.py): one action and target.
    ("decision outcome",
     "SELECT id, detail FROM events WHERE target=? AND action='decision.outcome' LIMIT 1", ("call-1",),
     "events_action_target"),
    # The run events naming a run's tasks (task_privacy, privacy_index, read_deps): the partial index, covering.
    ("run events of a run",
     f"SELECT detail FROM events WHERE action IN ({RUN_EVENTS}) AND target=?", ("att-1",),
     "COVERING INDEX events_run_detail"),
    # The runs that took a task in (message_links.candidates): every run event's detail, read from the index alone.
    ("run events naming a task",
     f"SELECT target FROM events WHERE action IN ({RUN_EVENTS}) AND instr(detail, ?)>0", ("task-1",),
     "COVERING INDEX events_run_detail"),
    # One action in a time window, no target (health_monitor, judge, app digest): a range, not the action's every row.
    ("limited runs today",
     "SELECT detail FROM events WHERE action='attempt.limited' AND created_at>?", ("2026-10-30",),
     "events_action_ts (action=? AND ts>?)"),
    ("judge month",
     "SELECT coalesce(sum(json_extract(detail,'$.cost_usd')),0) FROM events WHERE created_at>=? AND action='judge.spend'",
     ("2026-10-01",), "events_action_ts (action=? AND ts>?)"),
    # The rows since the month's running total was last counted (judge.spent_this_month): the rowid range, not the month.
    ("judge month since last count", B.NEW_SPEND_SQL, (0, "2026-10-01", "judge.spend"), "INTEGER PRIMARY KEY (rowid>?)"),
    ("asks expired",
     "SELECT count(*) FROM events WHERE created_at>=? AND action IN ('ask.expired','ask.went_ahead')", ("2026-10-01",),
     "events_action_ts (action=? AND ts>?)"),
    # Actor-led reads stay where they were: by actor alone, with a parameter action, and the pinned counts.
    ("first action of a person",
     "SELECT min(created_at) FROM events WHERE actor=?", ("human:ana",), "events_task_origin (actor=?)"),
    ("anyone's history",
     "SELECT 1 FROM events WHERE actor IN (?,?) LIMIT 1", ("human:ana", "ana"), "events_task_origin (actor=?)"),
    ("ask card",
     "SELECT detail FROM events WHERE actor=? AND action=? AND target=? ORDER BY created_at LIMIT 1",
     ("bot:ops", "task.ask_card", "task-1"), "events_task_origin (actor=? AND action=? AND target=?)"),
    ("judge calls today",
     "SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action=? AND created_at>=?",
     ("bot:ops", "judge.call", "2026-10-30"), "COVERING INDEX events_task_origin (actor=? AND action=?)"),
    # The task-origin reads (hubdb.TASK_ORIGIN_SQL): actor-led, must stay on events_task_origin.
    ("task origin",
     "SELECT target,detail FROM events WHERE actor=? AND action='task.origin' AND target IN (?,?) "
     "ORDER BY target,created_at DESC", ("bot:ops", "task-1", "task-2"),
     "events_task_origin"),
    # The event sweep walks events_ts forward (store.sweep_events).
    ("event sweep",
     "SELECT rowid,created_at FROM events INDEXED BY events_ts WHERE created_at>=? AND created_at<? AND action IN (?,?) ORDER BY created_at LIMIT ?",
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
        c.execute("CREATE INDEX events_run_task ON events(action, target) "
                  "WHERE action IN ('task.next-run.carried','attempt.input.moved')")
    s = open_store(path)
    with s.read() as c:
        present = event_indexes(c)
    assert not DROPPED & present, present
    assert KEPT <= present, present
