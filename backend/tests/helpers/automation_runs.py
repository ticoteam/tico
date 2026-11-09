"""Helpers that other tests share, moved out of test_automation_runs.py so they import no test file."""

import json
from backend import automations
from backend.tests.helpers.api import post


EVENT = {"type": "event", "name": "x.y"}


def make(api, steps, trigger=EVENT, owner="person:ana", **extra):
    return post(api, "automations", {"name": "Test", "owner": owner, "trigger": trigger, "steps": steps, **extra})["id"]


def queued(api, aid, payload=None):
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
        return automations.queue(c, row, payload or {}, "test")


def run_row(api, rid):
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone())
    return {**row, "state": json.loads(row["saved_state"] or "{}")}


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


# ---------------------------------------------------------------------------------------------- the find watermark
FIND = {"id": "look", "type": "find", "source": "tasks", "query": "Acme batch", "limit": 1}
