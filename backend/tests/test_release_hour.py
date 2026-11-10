"""The daily release hour (backend/release_hour.py): a routine on the Release Manager that does what Release now does,
once per slot, across restarts and clock jumps."""

import json
from datetime import datetime, timedelta

from backend import release_hour as RH
from backend import release_schedule_routes as RS
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
SHIP = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Ship", "owner": "assignee", "requirements": [{"kind": "in_release"}]},
)


def zone_at(hour, minute=0, days=0):
    return datetime(2026, 11, 10, hour, minute, tzinfo=RS.zone()) + timedelta(days=days)


def setup(api, manager=True, waiting=True, on=True):
    """A 10:00 release hour; `manager`: Release Manager ops; `waiting`: one task in a stage that waits for a release."""
    with api.app.state.store.transaction() as c:
        X.bot(c, "ops")
        if waiting:
            X.pipeline(c, "ship", "Shipping", SHIP)
            row = X.new_task(c, ANA, "Fix the login loop", "ship", assignee="bot:ops")
            X.say_done(c, row)
            X.E.move(
                c, row["id"], X.E.NEXT, "bot:ops", seen_version=X.E.task(c, row["id"])["version"]
            )  # built: it waits in Ship for a release
        RS.save(c, ANA, {"on": on, "times": ["10:00"], "bot": "ops" if manager else None})
        if not manager:
            c.execute("UPDATE registry_metadata SET value=json_set(value,'$.bot','') WHERE key=?", (RS.KEY,))


def tick(api, at):
    """One scheduler pass at `at`, from a fresh Scheduler (a restart each time)."""
    Scheduler(api.app.state.store, api.app.state.execution).tick(at)


def release_tasks(api):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT id FROM tasks WHERE owner_id='bot:ops' AND title LIKE 'Release: %'")]


def fired(api):
    """The slots fired, as the schedule's `last` reads them."""
    with api.app.state.store.read() as c:
        row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (RS.FIRED,)).fetchone()
    return json.loads(row[0]) if row else {}


def test_the_hour_fires_once_and_a_restart_or_a_clock_jumped_back_does_not_fire_it_again(api):
    setup(api)
    tick(api, zone_at(9, 59))
    assert release_tasks(api) == [] and fired(api) == {}, "before the hour nothing fires"
    tick(api, zone_at(10, 0))
    (task,) = release_tasks(api)
    tick(api, zone_at(10, 0))  # a restarted server's first pass in the same slot
    tick(api, zone_at(10, 30))
    with api.app.state.store.transaction() as c:  # the Release task closes: a repeat would file a new one
        X.E.move(c, task, X.E.CLOSED, ANA, "shipped", seen_version=X.E.task(c, task)["version"])
    tick(api, zone_at(10, 45))
    tick(api, zone_at(8, 30))  # the clock jumps back before the slot
    tick(api, zone_at(10, 10))  # and forward into it again
    assert release_tasks(api) == [task], "one task for the slot"
    (slot,) = fired(api).values()
    assert slot["sent"] is True and slot["task"] == task and slot["tasks"] == 1
    assert slot["at"] == zone_at(10).isoformat()
    tick(api, zone_at(10, 5, days=1))  # the next day's slot fires on its own
    assert len(fired(api)) == 2


def test_with_nothing_waiting_the_slot_is_used_and_no_task_is_filed(api):
    setup(api, waiting=False)
    tick(api, zone_at(10, 1))
    tick(api, zone_at(10, 2))
    assert release_tasks(api) == []
    (slot,) = fired(api).values()
    assert slot["sent"] is False and slot["reason"] == "nothing_pending"


def test_with_no_release_manager_nothing_is_filed(api):
    setup(api, manager=False)
    tick(api, zone_at(10, 1))
    assert release_tasks(api) == []
    (slot,) = fired(api).values()
    assert slot["reason"] == "no_release_manager"


def test_with_the_schedule_off_nothing_fires(api):
    setup(api, on=False)
    tick(api, zone_at(10, 1))
    assert release_tasks(api) == [] and fired(api) == {}


def test_a_server_down_past_the_grace_skips_the_slot_and_the_next_one_fires():
    cfg = {"on": True, "times": ["10:00", "16:00"], "bot": "", "writer": ""}
    assert RH.due_slot(cfg, zone_at(11, 59)) == zone_at(10)
    assert RH.due_slot(cfg, zone_at(12, 1)) is None, "two hours late: skipped"
    assert RH.due_slot(cfg, zone_at(16, 0)) == zone_at(16)
    assert RH.due_slot(cfg, zone_at(1, 0, days=1)) is None
    assert RH.due_slot({**cfg, "on": False}, zone_at(10, 0)) is None
