"""Busy turns C: the turn fixer. Synthetic orgs, fake workers and no provider calls."""

import json
import sqlite3
import time

from backend.store import H
from backend.tests.helpers.api import get  # noqa: F401
from backend.tests.helpers.org_turns import one_person
from backend.tests.helpers.start_places import waiters


def botops(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)


def busy(machine, bots=()):
    """The computer's latest claim, as fresh as a claim just made, naming `bots` as busy."""
    from backend import execution

    execution.CLAIMED_BUSY[machine['runner_id']] = (frozenset(bots), frozenset(), time.monotonic())


def fix(api):
    """One pass the way the scheduler runs it: free, then the finding on a read connection, then tend."""
    from contextlib import contextmanager
    from backend import turn_fixer

    store = api.app.state.store

    @contextmanager
    def write(step):
        with store.transaction() as c:
            yield c

    return turn_fixer.run_split(api.app.state.execution, store.settings, write, store.read, 'ana')


def state(api, aid):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT a.state,j.state job FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (aid,)
        ).fetchone()


def notes(api, task_id):
    with api.app.state.store.read() as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT body FROM messages WHERE json_extract(refs,'$.task')=? AND sender_id=?", (task_id, H.KEEPER)
            )
        ]


def test_frees_only_proven_dead_starts_and_never_blindly_retries_a_turn_that_acted(api):
    """Spec 7/8: lease lost and process gone, or stopped, is freed with a task note; a slow or healthy turn is not,
    nor is a lost lease whose computer still runs the bot; a dead turn that used a writing tool is held for BotOps."""
    botops(api)
    machine = waiters(api, n=3, running=2)
    past = H.shift(H.now(), seconds=-5)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id IN ('attempt-0','attempt-4')", (past,))
        c.execute("UPDATE attempts SET stop_requested_at='human:ana' WHERE id='attempt-1'")
        c.execute("UPDATE tasks SET private=0 WHERE id='task-4'")  # a company task BotOps may read
        # attempt-3 runs with a live lease but has made no progress for an hour: slow, not dead.
        c.execute(
            "UPDATE attempts SET started_at=? WHERE id IN ('attempt-3','attempt-4')", (H.shift(H.now(), hours=-1),)
        )
        c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES('attempt-4',1,'tool',?,?)",
            (json.dumps({'tool': 'Bash', 'item_id': 'x'}), H.now()),
        )
    busy(machine, {'ops'})  # the computer still runs the bot: a lost lease alone proves nothing
    assert fix(api)[0] == 1  # only the stopped start
    assert tuple(state(api, 'attempt-0')) == ('leased', 'leased')
    assert state(api, 'attempt-1')['job'] == 'canceled'
    assert notes(api, 'task-1') == ["Tico freed a start a person stopped; it is not retried."]
    busy(machine)  # its latest claim no longer names the bot: the process is gone
    assert fix(api)[0] == 2
    assert tuple(state(api, 'attempt-0')) == ('expired', 'queued')
    assert notes(api, 'task-0') == ["Tico freed a dead start, will retry."]
    for healthy in ('attempt-2', 'attempt-3'):  # a healthy login wait and a slow running turn
        assert state(api, healthy)['state'] in ('leased', 'running')
    assert state(api, 'attempt-4')['job'] == 'uncertain'
    assert 'not retried' in notes(api, 'task-4')[0] and 'Bash' in notes(api, 'task-4')[0]
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM events WHERE action='job.held' AND target='attempt-4'").fetchone()[0] == 1
        )
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id='bot:botops'").fetchone()[0] == 1


def test_dead_turn_on_a_private_task_botops_cannot_read_is_held_without_a_botops_task(api):
    """The BotOps review task is company-wide: a private task's title and owner never reach it."""
    botops(api)
    machine = waiters(api, n=0, running=1)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,visibility='private' WHERE id='task-0'")
        c.execute(
            "UPDATE attempts SET lease_until=?,started_at=? WHERE id='attempt-0'",
            (H.shift(H.now(), seconds=-5), H.shift(H.now(), hours=-1)),
        )
        c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES('attempt-0',1,'tool',?,?)",
            (json.dumps({'tool': 'Bash', 'item_id': 'z'}), H.now()),
        )
    busy(machine)
    assert fix(api)[0] == 1
    assert state(api, 'attempt-0')['job'] == 'uncertain'
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM events WHERE action='job.held' AND target='attempt-0'").fetchone()[0] == 1
        )
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id='bot:botops'").fetchone()[0] == 0


def test_leased_start_that_reported_a_tool_call_is_held_not_requeued(api):
    """A start whose lease lapsed after it reported a tool call did run something: no blind retry."""
    waiters(api, n=1, running=0)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES('attempt-0',9,'tool',?,?)",
            (json.dumps({'tool': 'Bash', 'item_id': 'y'}), H.now()),
        )
        c.execute("UPDATE attempts SET lease_until=? WHERE id='attempt-0'", (H.shift(H.now(), seconds=-5),))
        api.app.state.execution.expire(c, attempt_ids=['attempt-0'])
    assert state(api, 'attempt-0')['job'] == 'uncertain'


def test_a_wait_that_needs_the_person_tells_the_owner_at_once(api):
    one_person(api)
    waiters(api, n=39, running=1)  # 39 waiting for a shared login
    fix(api)
    fix(api)
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM messages WHERE recipient_id='human:ana' AND kind='notice' "
                "AND body LIKE '%needs a login%'"
            ).fetchone()[0]
            == 1
        )


def stale_finding(api):
    """An open incident whose finding is due for a rewrite."""
    from backend import org_turns

    one_person(api)
    waiters(api, n=39, running=1)
    store = api.app.state.store
    with store.transaction() as c:
        org_turns.observe(c, store.settings, detailed=False)
        incident = org_turns.saved(c, org_turns.INCIDENT)
        incident['finding'] = {'updated_at': H.shift(H.now(), minutes=-2), 'text': 'stale'}
        org_turns.save(c, org_turns.INCIDENT, incident)


def watch_writer(store, monkeypatch, module, name):
    """Count calls of module.name made inside and outside a write transaction."""
    from contextlib import contextmanager

    writing = {'on': False}
    seen = {'during_write': 0, 'during_read': 0}
    real_txn, real = store.transaction, getattr(module, name)

    @contextmanager
    def wrapped_txn(*a, **k):
        writing['on'] = True
        try:
            with real_txn(*a, **k) as c:
                yield c
        finally:
            writing['on'] = False

    def watched(*a, **k):
        seen['during_write' if writing['on'] else 'during_read'] += 1
        return real(*a, **k)

    monkeypatch.setattr(store, 'transaction', wrapped_txn)
    monkeypatch.setattr(module, name, watched)
    return seen


def test_finding_classification_is_not_under_the_writer(api, monkeypatch):
    """A due finding classifies the fleet on a read connection; the writer only installs the draft."""
    from backend import org_turns, start_places

    stale_finding(api)
    store = api.app.state.store
    seen = watch_writer(store, monkeypatch, start_places, 'summary')
    fix(api)
    assert seen['during_read'] >= 1
    assert seen['during_write'] == 0
    with store.read() as c:
        found = org_turns.saved(c, org_turns.INCIDENT)['finding']
    assert 'Likely bottleneck' in found['text']


def test_the_scheduler_builds_the_finding_outside_the_writer(api, monkeypatch):
    """The scheduler tick itself takes the split pass: its turn fixer builds the finding on a read connection."""
    from backend import org_turns, turn_fixer
    from backend.scheduler import Scheduler

    stale_finding(api)
    store = api.app.state.store
    seen = watch_writer(store, monkeypatch, turn_fixer, 'finding')
    Scheduler(store, api.app.state.execution).tick()
    assert seen == {'during_write': 0, 'during_read': 1}, seen
    with store.read() as c:
        found = org_turns.saved(c, org_turns.INCIDENT)['finding']
    assert 'Likely bottleneck' in found['text']
