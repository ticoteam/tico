"""Helpers that other tests share, moved out of test_delivery.py so they import no test file."""

from datetime import datetime, timedelta, timezone
from backend import delivery
from backend.scheduler import Scheduler
from backend.tests.helpers.api import assign, ready, runner


def setup(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    return machine


def rows(api, **where):
    sql = " AND ".join(f"{k}=?" for k in where) or "1"
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(f"SELECT * FROM deferrals WHERE {sql} ORDER BY created_at", tuple(where.values()))
        ]


def job_of(api, message_id):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT id,state FROM jobs WHERE message_id=?", (message_id,)).fetchone()
    return tuple(row) if row else None


def tick(api, minutes):
    """One scheduler pass `minutes` from now, by a new Scheduler, as after a restart."""
    Scheduler(api.app.state.store, api.app.state.execution).tick(
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    )


def fired(api):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT target FROM events WHERE action=?", (delivery.EVENT,))]
