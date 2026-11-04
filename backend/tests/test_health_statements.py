"""The Health page reads in set queries: its statements do not grow with the escalated waiting tasks, looping tasks or
silent waits it summarises. On the Tico Team hub (Nov 3) a call took 384 statements and 1.2 s, rising as escalations
and loops piled up: each escalated waiting task cost its own reads (measured here: 100 of them, +103 statements)."""
import json
import uuid

from backend import bot_settings as BS
from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import assign, headers, post, runner
from backend.tests.test_claim_statements import seed, traced
from backend.tests.test_getting_started import heartbeat

OLD = H.shift(H.now(), hours=-5)
LATER = H.shift(H.now(), hours=-4)
PR_OLD = H.shift(H.now(), hours=-13)
RUNTIMES = {"codex": {"installed": True, "authenticated": "ready"},
            "claude": {"installed": True, "authenticated": "ready"}}


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


def status(api):
    with traced(api) as seen:
        response = api.get("/api/status", headers=headers())
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def reviews(api, prefix, count):
    """`count` bot pull requests that are green and waiting for review since before the twelve-hour mark, each with a
    review subtask (Health names whose move it is from the subtasks and the task roles)."""
    with api.app.state.store.transaction() as c:
        for n in range(count):
            tid = f"{prefix}-pr{n}"
            c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,0)", (tid, "PR " + tid, "human:ana", "bot:few-00", "open", OLD, OLD))
            detail = {"head_sha": f"{prefix}{n}", "health_wait": {"reason": "review", "since": PR_OLD}}
            c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,mergeable,checks,"
                      "review_state,detail,number,branch,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"{tid}-link", tid, "pr", f"https://github.com/acme/app/pull/{prefix}{n}", "PR", "open",
                       "bot:few-00", OLD, "clean", "passing", "", json.dumps(detail), n + 1, f"b{prefix}{n}", OLD))
            review = tid + "-review"
            c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,0)", (review, "Review PR " + tid, "bot:few-00", "human:ben", "open", OLD, OLD))
            c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,?)",
                      (review, tid, "parent", OLD))


def fleet(api, prefix, count):
    """`count` each of the work Health and Status read per bot, per computer, per task and per pull request: bots on an
    online computer whose tool credential is missing and some of whose models are not signed in, bots on a computer
    that never checked in, stalled waiting tasks held by a quiet subtask of their own, and pull requests waiting for
    review."""
    store = api.app.state.store
    machine = seed(api, prefix, count)                        # bots on an online computer, a queued chat each
    slugs = [f"{prefix}-{n:02d}" for n in range(count)]
    with store.transaction() as c:
        ids = {slug: c.execute("SELECT id FROM bots WHERE slug=?", (slug,)).fetchone()[0] for slug in slugs}
        doc = {"schema_version": 1, "runtimes": RUNTIMES, "bots": {}}
        for n, slug in enumerate(slugs):
            doc["bots"][ids[slug]] = {
                "ready": n % 2 == 0, "runtime": "codex",
                "problems": [] if n % 2 == 0 else ["Codex is not signed in"],
                "tools": [{"env": "STRIPE_KEY", "credential": "missing", "service": "stripe"}]}
        c.execute("UPDATE runner_states SET readiness=?, last_seen_at=? WHERE runner_id=?",
                  (json.dumps(doc), H.now(), machine["runner_id"]))
    for n in range(count):                                    # computers that check in: where a bot could move
        heartbeat(api, runner(api, label=f"{prefix} Mac {n:02d}")["runner_id"], seconds_ago=5, runtimes=RUNTIMES)
    silent = runner(api, label=f"{prefix} Silent")            # never checked in: its bots wait on it
    off = [f"{prefix}off-{n:02d}" for n in range(count)]
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in off},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]})
        for s in off:
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (s,))
            BS.write(c, s, {"name": s, "runtime": "fake"})
    for s in off:
        assign(api, silent, s)
        post(api, "chat/" + s, {"text": "Please summarize the current work."})
    stall(api, count, prefix + "s", blocked=count)
    reviews(api, prefix, count)


def test_health_and_status_statements_do_not_grow_with_the_fleet(api):
    fleet(api, "few", 3)
    health(api)
    status(api)                                               # first-time reads
    small_body, small = health(api)
    _, small_status = status(api)
    fleet(api, "many", 30)
    body, large = health(api)
    _, large_status = status(api)
    # The scenario reaches each check it measures; a check that went quiet would make the counts mean nothing.
    for key in ("stalled_tasks", "aged_prs", "tool_credentials"):
        assert check(small_body, key) and check(body, key), key
    assert large <= small + 4, (small, large)
    assert large_status <= small_status + 4, (small_status, large_status)
