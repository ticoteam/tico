"""GET /api/v2/bots/{bot}/turns reads each run, its message, its conversation's membership and its attempt details in
batches, so the statements it takes do not grow with how many turns the bot has (or how many of them failed)."""
import contextlib

import pytest

from backend import threads
from backend.store import H
from backend.tests import test_api as T
from backend.tests.test_api import api, as_member, assign, claim, post, ready, runner  # noqa: F401  (the api fixture)

# Tool calls each run streams, as a runner reports them.
STEPS = 10


def runs(api, count, room=False, failed=False, machine=None):
    """`count` runs of bot `ops`, each answering a message from Ana: a direct chat, or (`room`) a room Ana and Ben share
    with the bot. A `failed` run ends failed instead of completed and keeps no turn row."""
    if machine is None:
        as_member(api, "ben@acme.example")
        machine = runner(api, "ana")
        assign(api, machine, "ops")
        ready(api, machine, ["ops"])
    for k in range(count):
        if room:
            with api.app.state.store.transaction() as c:
                shared = H.open_conversation(c, "human:ana", ["human:ana", "human:ben", "bot:ops"], subject=f"Room {k}")
            msg = post(api, "messages", {"to": "ops", "text": f"Question {k}", "conversation_id": shared["id"]})
        else:
            msg = post(api, "chat/ops", {"text": f"Question {k}"})
        aid = claim(api, machine)["id"]
        post(api, f"attempts/{aid}/started", {"thread_id": f"run {k}"}, machine["token"])
        rows = [{"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}}]
        for j in range(STEPS):
            call = {"tool": "Read", "item_id": f"r{k}-{j}"}
            rows += [{"seq": 2 + 2 * j, "kind": "tool", "payload": {**call, "status": "started"}},
                     {"seq": 3 + 2 * j, "kind": "tool", "payload": {**call, "status": "completed"}}]
        post(api, f"attempts/{aid}/events", {"events": rows}, machine["token"])
        if failed:
            with api.app.state.store.transaction() as c:
                # The run record goes (no trigger, so the attempt is no run); the failed attempt stays.
                c.execute("UPDATE attempts SET state='failed',finished_at=?,trigger=NULL WHERE id=?", (H.now(), aid))
        else:
            post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": f"Answer {k}.", "last_seq": len(rows)},
                 machine["token"])
    return machine


def turns_statements(api, monkeypatch, path, token="ana-test"):
    """GET `path` as `token`: the body, and the statements it took. Each request starts from cold roster and row
    caches, as a poll from a new process would."""
    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)
        return borrowed
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction", "transaction"):
            patched.setattr(store, name, traced(getattr(store, name)))
        response = api.get(path, headers=T.headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_the_turns_list_takes_the_same_statements_however_many_turns_it_reads(tmp_path, monkeypatch):
    """Direct chats, rooms with people in them and failed runs in rooms, read together from one install."""
    counted = {}
    for name, count in (("small", 3), ("big", 20)):
        (tmp_path / name).mkdir()
        install = T.api.__wrapped__(tmp_path / name, None)
        api = next(install)
        try:
            machine = None
            for shape in ({}, {"room": True}, {"room": True, "failed": True}):
                machine = runs(api, count, machine=machine, **shape)
            body, counted[name] = turns_statements(api, monkeypatch, "/api/v2/bots/ops/turns")
            assert len(body) == count * 3, len(body)
        finally:
            install.close()
    few, many = counted["small"], counted["big"]
    assert few > 0 and many > 0, (few, many)
    assert many - few <= 4, (
        f"GET /api/v2/bots/ops/turns takes {many} statements with 20 turns of each shape against {few} with 3: a read "
        "runs once per turn again instead of once for the whole list. Batch it (see threads.prime_members).")


def test_priming_the_membership_reads_does_not_change_the_list(api, monkeypatch):
    """The batched membership read only warms what the per-row checks read: the list is the same without it."""
    runs(api, 4, room=True, failed=True)
    batched, _ = turns_statements(api, monkeypatch, "/api/v2/bots/ops/turns")
    with monkeypatch.context() as patched:
        patched.setattr(threads, "prime_members", lambda c, who, ids: None)
        unbatched, _ = turns_statements(api, monkeypatch, "/api/v2/bots/ops/turns")
    assert batched == unbatched
    assert len(batched) == 4
