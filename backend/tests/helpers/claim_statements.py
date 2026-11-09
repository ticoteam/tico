"""Helpers that other tests share, moved out of test_claim_statements.py so they import no test file."""

import contextlib
from backend import bot_settings as BS
from backend.store import H
from backend.tests.helpers.api import assign, post, ready, runner


CAPABILITIES = [
    "task_lanes_v2",
    "session_roots_v1",
    "task_goals_v1",
    "capacity_continue_v1",
    "source_continue_v1",
    "turns_at_once_v1",
    "private_lanes_v1",
    "browser_exit_v1",
    "threads_v1",
]


def seed(api, prefix, count):
    """A computer with `count` active bots, each with a queued chat."""
    store = api.app.state.store
    slugs = [f"{prefix}-{n:02d}" for n in range(count)]
    with store.transaction() as c:
        H.sync_registry(
            c,
            {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]},
        )
        for s in slugs:
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (s,))
            BS.write(c, s, {"name": s, "runtime": "fake"})
    machine = runner(api, label=f"Statements {prefix}")
    for s in slugs:
        assign(api, machine, s)
    ready(api, machine, slugs, CAPABILITIES)
    for s in slugs:
        post(api, "chat/" + s, {"text": "Please summarize the current work."})
    return machine


@contextlib.contextmanager
def traced(api):
    """Every statement the claim runs, on any connection the store lends it (reads and the write transaction)."""
    store, seen = api.app.state.store, []
    original = store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            c.set_trace_callback(seen.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    store.read = borrowed
    try:
        yield seen
    finally:
        del store.read
