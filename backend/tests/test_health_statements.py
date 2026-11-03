"""The Health page reads in set queries: its statements do not grow with the escalated waiting tasks, looping tasks or
silent waits it summarises. On the Tico Team hub (Nov 3) a call took 384 statements and 1.2 s, rising as escalations
and loops piled up: each escalated waiting task cost its own reads (measured here: 100 of them, +103 statements)."""
import uuid

from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import headers
from backend.tests.test_claim_statements import seed, traced

OLD = H.shift(H.now(), hours=-5)
LATER = H.shift(H.now(), hours=-4)


def health(api):
    with traced(api) as seen:
        response = api.get("/api/v2/health", headers=headers())
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def stall(api, count, prefix, blocked=0):
    """`count` waiting bot tasks the stall watcher escalated; the first `blocked` wait on a quiet subtask of their own."""
    with api.app.state.store.transaction() as c:
        for n in range(count):
            tid = f"{prefix}{n}"
            c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,0)", (tid, "Stalled " + tid, "human:ana", "bot:few-00", "waiting", OLD, OLD))
            c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,?)",
                      (uuid.uuid4().hex, LATER, "x", "task.stall_escalated", tid, "{}"))
            if n < blocked:
                kid = tid + "-child"
                c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                          "VALUES(?,?,?,?,?,?,?,0)", (kid, "Child " + tid, "bot:few-00", "bot:few-00", "open", OLD, OLD))
                for frm, to, kind in ((kid, tid, "parent"), (kid, tid, "blocks")):
                    c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,?)",
                              (frm, to, kind, OLD))


def loop(api, count, start=0):
    with api.app.state.store.transaction() as c:
        for n in range(start, start + count):
            tid = f"loop{n}"
            c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,0)", (tid, "Looping " + tid, "human:ana", "bot:few-00", "open", OLD, OLD))
            for k in range(22):
                c.execute("INSERT INTO history_events(id,subject_type,subject_id,created_at,actor_id,kind,old,new) "
                          "VALUES(?,'task',?,?,?,?,?,?)",
                          (uuid.uuid4().hex, tid, H.shift(H.now(), minutes=-k), "human:ana", "status", "a", "b"))


def check(body, key):
    return next((x for x in body["checks"] if x["id"] == key), None)


def test_health_statements_do_not_grow_with_escalated_waiting_tasks(api):
    seed(api, "few", 6)
    stall(api, 3, "a", blocked=1)
    health(api)                                   # first-time reads
    small_body, small = health(api)
    stall(api, 100, "b")
    body, large = health(api)
    assert large <= small + 4, (small, large)
    # The task with a quiet subtask of its own is still reported, and only it: the others have no blocker.
    for found in (small_body, body):
        stalled = check(found, "stalled_tasks")
        assert stalled and [i["task"] for i in stalled["items"]] == ["a0"], stalled


def test_health_statements_do_not_grow_with_looping_tasks(api):
    seed(api, "few", 6)
    loop(api, 3)
    health(api)
    body, small = health(api)
    loop(api, 20, start=3)
    _, large = health(api)
    assert check(body, "task_loops")
    assert large <= small + 4, (small, large)
