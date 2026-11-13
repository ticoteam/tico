"""Synthetic runner traffic during the worktree-heavy heartbeat and scheduler pass."""

import json
import math
import threading
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import flight, worktrees
from backend.scheduler import Scheduler
from backend.store import H, encode
from backend.tests.helpers.api import headers, runner
from backend.enqueue import enqueue_stored
from backend import task_engine as E
from backend.tests.helpers.attempt_contention import fleet, on_engine


def test_heartbeat_prepares_provenance_outside_write_and_discards_a_stale_snapshot(api, monkeypatch):
    from backend import task_privacy
    from backend.tests.helpers.api import post

    computer = fleet(api, attempts=4, trees=3)
    store = api.app.state.store
    observed = []
    original = task_privacy.attempt_tasks

    def counted(c, aid, memo=None):
        if memo is None:
            observed.append(aid)
        return original(c, aid, memo)

    monkeypatch.setattr(task_privacy, "attempt_tasks", counted)
    body = {
        "version": "synthetic",
        "platform": "test",
        "readiness": {"schema_version": 1, "bots": {}, "worktrees": True},
    }
    result = post(api, "runners/heartbeat", body, computer["token"])
    assert len(observed) == 4
    assert len(result["worktree_actions"]) == 3
    prepare = worktrees.live_turn_cache

    def changes_after_read(c, computer_id):
        selected = prepare(c, computer_id)
        with store.transaction() as writer:
            # attempt-0's turn now carries task-4 too: the snapshot read before it is stale.
            writer.execute("UPDATE messages SET refs=? WHERE id='message-0'", (encode({"task": ["task-0", "task-4"]}),))
        return selected

    monkeypatch.setattr(worktrees, "live_turn_cache", changes_after_read)
    result = post(api, "runners/heartbeat", body, computer["token"])
    assert "tree-4" not in {a["link_id"] for a in result["worktree_actions"]}
    monkeypatch.setattr(worktrees, "live_turn_cache", prepare)
    with store.transaction() as c:
        row = on_engine(c, "task-4")
        E.reassign(c, "task-4", "bot:finance", "human:ana", seen_version=row["version"])
    result = post(api, "runners/heartbeat", body, computer["token"])
    assert "tree-4" in {a["link_id"] for a in result["worktree_actions"]}


def test_event_batch_replay_conflict_and_gap_are_atomic(api):
    from backend.tests.helpers.api import post

    computer = fleet(api, attempts=1, trees=0)
    path = "attempts/attempt-0/events"
    batch = [{"seq": i, "kind": "delta", "payload": {"text": str(i)}} for i in range(1, 4)]
    assert post(api, path, {"events": batch}, computer["token"])["ack_seq"] == 3
    assert post(api, path, {"events": batch + [batch[-1]]}, computer["token"])["ack_seq"] == 3
    post(api, path, {"events": [dict(batch[0], payload={"text": "different"})]}, computer["token"], expected=409)
    post(
        api,
        path,
        {"events": [{"seq": i, "kind": "delta", "payload": {}} for i in (4, 6)]},
        computer["token"],
        expected=409,
    )
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempt_events WHERE attempt_id='attempt-0'").fetchone()[0] == 3
        assert c.execute("SELECT last_seq FROM attempts WHERE id='attempt-0'").fetchone()[0] == 3


def test_shared_provenance_rechecks_permissions_and_never_publishes_rolled_back_sources(api, monkeypatch):
    from backend import task_privacy

    fleet(api, attempts=1, trees=2)
    store = api.app.state.store
    builds = []
    original = task_privacy._attempt_tasks

    def build(c, aid):
        builds.append(aid)
        return original(c, aid)

    monkeypatch.setattr(task_privacy, '_attempt_tasks', build)
    with store.transaction() as c:
        # Finance owns the task now; while it is not private, Ops' run still reads it.
        row = on_engine(c, "task-0")
        E.reassign(c, "task-0", "bot:finance", "human:ana", seen_version=row["version"])
        c.execute("UPDATE tasks SET private=0 WHERE id='task-0'")
    with store.read_transaction() as c:
        assert task_privacy.attempt_readable(c, 'bot:ops', 'attempt-0')
    count = len(builds)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,requester_id='human:ben' WHERE id='task-0'")
    with store.read_transaction() as c:
        assert not task_privacy.attempt_readable(c, 'bot:ops', 'attempt-0')
    assert len(builds) == count  # Only neutral provenance survives; permission never does.
    # Even a manual transaction on a borrowed connection must not publish a clock
    # value that rollback can reuse for entirely different source rows.
    with store.read() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE messages SET refs=? WHERE id='message-0'", (encode({'task': 'task-1'}),))
        assert task_privacy.attempt_tasks(c, 'attempt-0') == {'task-1'}
        c.rollback()
    with store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id='message-0'", (encode({'task': 'task-2'}),))
    with store.read_transaction() as c:
        assert task_privacy.attempt_tasks(c, 'attempt-0') == {'task-2'}


def test_claim_cleans_a_bounded_page_of_obsolete_notices(api):
    from backend import execution
    from backend.tests.helpers.api import post

    computer = fleet(api, attempts=0, trees=20)
    store = api.app.state.store
    with store.transaction() as c:
        for i in range(20):
            row = on_engine(c, f"task-{i}")
            E.move(c, row["id"], E.DONE, "human:ana", "Finished", seen_version=row["version"])
        for i in range(20):
            c.execute(
                "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                "VALUES(?,?,?,'bot:ops','notice','Synthetic finished task',?,?)",
                (f'obsolete-{i}', f'room-{i}', H.KEEPER, H.now(), encode({'task': f'task-{i}'})),
            )
            enqueue_stored(c, f'obsolete-{i}')
    assert post(api, 'jobs/claim', {}, computer['token'])['attempt'] is None
    with store.read() as c:
        obsolete = "SELECT count(*) FROM jobs WHERE state=? AND message_id LIKE 'obsolete-%'"
        assert c.execute(obsolete, ("canceled",)).fetchone()[0] == execution.CLAIM_MAINTENANCE
        assert c.execute(obsolete, ("queued",)).fetchone()[0] == 20 - execution.CLAIM_MAINTENANCE


def test_health_flags_runner_p95_and_clears_after_the_window(api):
    import sqlite3
    from backend import health, maintenance_health

    with sqlite3.connect(":memory:") as empty:
        assert health.runner_route_latency(empty) is None
    store = api.app.state.store
    now = int(time.time())
    rec = flight.Recorder()
    for _ in range(20):
        rec.request("POST /api/v2/attempts/{aid}/renew", "runner", 200, 1500)
        rec.request("POST /api/v2/attempts/{aid}/inputs", "runner", 200, 10)
    rec.drain(now)
    with store.transaction() as c:
        flight.write(c, rec.take(now // 60 * 60))
        assert "renew" in health.runner_route_latency(c, now)["summary"]
        assert any(row["key"] == "runner_route_latency" for row in maintenance_health._server_checks(c))
        assert health.runner_route_latency(c, now + 16 * 60) is None


WORKERS = 12
