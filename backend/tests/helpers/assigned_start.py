"""Helpers that other tests share, moved out of test_assigned_start.py so they import no test file."""

import pytest
from backend import assigned_start as A, task_roundup
from backend.store import H


@pytest.fixture
def clock(monkeypatch):
    value = ['2026-10-20T00:00:00.000000Z']
    monkeypatch.setattr(H, 'now', lambda: value[0])
    return value


def advance(api, clock, hours):
    clock[0] = H.shift(clock[0], hours=hours)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runner_states SET last_seen_at=?', (H.now(),))


def sweep(api):
    """One roundup run now, as the scheduler runs it: the tasks it listed as assigned, not started (their ledger rows are
    the ones it wrote)."""
    store = api.app.state.store
    # Diffed by event id: a task listed again (a reassignment) has the same target as its earlier ledger row.
    with store.read() as c:
        before = {r[0] for r in c.execute('SELECT id FROM events WHERE action=?', (A.EVENT,))}
    with store.transaction() as c:
        task_roundup.run(c, H.now())
    with store.read() as c:
        return sorted(
            {r[1] for r in c.execute('SELECT id,target FROM events WHERE action=?', (A.EVENT,)) if r[0] not in before}
        )


def cancel(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")
