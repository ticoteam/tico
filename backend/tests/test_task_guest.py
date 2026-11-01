"""A private task with no person on it reaches one person, its guest, through its owning bot's hand-off (Dev 172)."""
import json

import pytest

from backend import task_relations as TR
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, put, ready, runner  # noqa: F401
from backend.tests.test_tasks_board import bot_token

ASK = {"title": "Approve the zip triage plan", "body": "Please approve the triage plan: reply yes or no on this task."}



@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def task(api, requester, owner, title, parent=None):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, requester, title, f"Confidential: {title}.", owner, private=True, lint=False)
        if parent:
            TR._insert(c, H.KEEPER, row["id"], parent, "parent", H.now())
        return row


def run_on(api, bot="ops"):
    """A run of `bot` claimed from its queue, on the oldest work waiting for it. One computer runs the bot; its
    previous run, if any, is finished first."""
    machine = getattr(api, "machine", None)
    if machine is None:
        machine = api.machine = runner(api)
        assign(api, machine, bot)
        ready(api, machine, [bot])
    if getattr(api, "attempt", None):
        post(api, f"attempts/{api.attempt['id']}/started", {"thread_id": "guest-" + api.attempt["id"]}, machine["token"])
        post(api, f"attempts/{api.attempt['id']}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 0},
             machine["token"])
    api.attempt = claim(api, machine, bot)
    return api.attempt


def subtask(parent, owner, **extra):
    return {"owner": owner, **ASK, "relations": [{"task": parent, "kind": "parent"}], **extra}


def dry_run(api, body, token):
    return post(api, "tasks/dry-run", body, token)["problems"]


def refused(api, body, token):
    r = api.post("/api/v2/tasks", json=body, headers=headers(token))
    assert r.status_code == 403, r.text
    return r.json()["error"]["detail"]


def readers(api, tid):
    return {person for person, token in (("ana", "ana-test"), ("ben", "ben-test"), ("cara", "cara-test"))
            if api.get("/api/v2/tasks/" + tid, headers=headers(token)).status_code == 200}


def sql_sees(api, token, words):
    r = api.post("/api/v2/sql", json={"sql": "SELECT id,title,body FROM tasks"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return words in r.text


@pytest.mark.parametrize("requester", ["bot:cpo", "bot:ops"])   # another bot's task, and a bot's task to itself
def test_private_bot_task_reaches_one_named_person_and_nobody_else(api, requester):
    above = task(api, "bot:cpo", "bot:cpo", "Zip advisory intake")
    row = task(api, requester, "bot:ops", "Zip triage", parent=above["id"])
    sibling = task(api, "bot:ops", "bot:ops", "Zip lockfile scan", parent=row["id"])
    attempt = run_on(api)
    assert readers(api, row["id"]) == set()
    body = subtask(row["id"], "ben")

    # Dry-run says what the create does: no privacy refusal, then the create goes through.
    assert not [p for p in dry_run(api, body, attempt["token"]) if "rivate" in p or "guest" in p]
    child = post(api, "tasks", body, attempt["token"])
    assert child["private"] and child["owner"] == "human:ben" and child["requester"] == "bot:ops"

    # Ben, and only Ben, now reads the task and the subtask; its history says who it reached.
    assert get(api, "tasks/" + row["id"], "ben-test")["task"]["guest"] == "human:ben"
    assert readers(api, row["id"]) == {"ben"} and readers(api, child["id"]) == {"ben"}
    assert row["id"] in {t["id"] for t in get(api, "tasks", "ben-test")["tasks"]}
    assert row["id"] not in {t["id"] for t in get(api, "tasks", "cara-test")["tasks"]}
    assert sql_sees(api, "ben-test", "Confidential: Zip triage.")
    assert not sql_sees(api, "cara-test", "Confidential: Zip triage.")
    assert not sql_sees(api, "ana-test", "Confidential: Zip triage.")
    events = get(api, "tasks/" + row["id"], "ben-test")["events"]
    assert any(e["field"] == "guest" and e["new"] == "human:ben" and e["actor"] == "bot:ops" for e in events)

    # The guest reads that one task: not the task above it, nor its other subtasks.
    assert readers(api, above["id"]) == set() and readers(api, sibling["id"]) == set()
    assert not sql_sees(api, "ben-test", "Confidential: Zip advisory intake.")
    assert not sql_sees(api, "ben-test", "Confidential: Zip lockfile scan.")

    # A second, different person is refused, by the create and its dry-run in the same words.
    other = subtask(row["id"], "cara", title="Approve the zip triage plan again")
    words = refused(api, other, attempt["token"])
    assert "already reached ben" in words
    assert words in dry_run(api, other, attempt["token"])
    assert readers(api, row["id"]) == {"ben"}


def test_guest_answer_comes_back_to_the_task_and_nowhere_wider(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    child = post(api, "tasks", subtask(row["id"], "ben"), attempt["token"])
    done = post(api, "tasks/" + child["id"], {"version": child["version"], "status": "done",
                                               "note": "Approved: go with the plan."}, "ben-test")
    assert done["status"] == "done"
    # Ben's answer wakes the bot that owns the task, which can act on its task with it.
    with api.app.state.store.read() as c:
        queued = c.execute("SELECT m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                           "WHERE j.bot='ops' AND j.state='queued'").fetchall()
    assert any(json.loads(r[0] or "{}").get("subtask") == child["id"] for r in queued)
    answer = run_on(api)
    post(api, f"tasks/{row['id']}/comments", {"text": "Ben approved; going ahead."}, answer["token"])
    # ...but not past the task's own readers.
    r = api.post("/api/v2/messages", json={"to": "cara", "text": "Ben approved.", "refs": {"task": row["id"]}},
                 headers=headers(answer["token"]))
    assert r.status_code == 403, r.text


def test_refused_hand_offs_match_their_dry_run(api):
    # A person's private task above: its work cannot reach someone who cannot read it.
    above = task(api, "human:cara", "bot:cpo", "Cara's vendor dispute")
    row = task(api, "bot:cpo", "bot:ops", "Dispute triage", parent=above["id"])
    attempt = run_on(api)
    body = subtask(row["id"], "ben")
    words = refused(api, body, attempt["token"])
    assert words in dry_run(api, body, attempt["token"])
    assert readers(api, row["id"]) == set()
    with api.app.state.store.read() as c:
        assert H.task(c, row["id"])["guest"] is None


def test_only_the_owning_bot_brings_a_person_in(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    child = post(api, "tasks", subtask(row["id"], "ben"), bot_token(api, "cpo"))
    assert child["owner"] == "human:ben"
    # The requester bot's hand-off goes on as before (the subtask alone), and makes nobody a guest.
    assert readers(api, row["id"]) == set()


def test_waiting_on_a_person_makes_them_the_guest_and_reassignment_ends_it(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    current = get(api, "tasks/" + row["id"], attempt["token"])["task"]
    waiting = post(api, "tasks/" + row["id"], {"version": current["version"], "status": "waiting", "waiting_on": "ben",
                                              "note": "Ben, please approve the plan."}, attempt["token"])
    # One question, one card: Ben gets a small subtask with the ask, and the task waits on it.
    card = next(t for t in get(api, "tasks", "ben-test")["tasks"] if t["owner"] == "human:ben")
    assert card["private"] and card["requester"] == "bot:ops"
    assert waiting["status"] == "waiting" and readers(api, row["id"]) == {"ben"} and readers(api, card["id"]) == {"ben"}
    r = api.post("/api/v2/tasks/" + row["id"], json={"version": waiting["version"], "status": "waiting",
                                                       "waiting_on": "cara"}, headers=headers(attempt["token"]))
    assert r.status_code in (403, 422) and "already reached ben" in r.text, r.text
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", row["id"], owner="bot:cpo")
    assert readers(api, row["id"]) == set()


def test_a_guest_who_cannot_read_the_bots_opens_that_task_and_no_other_work_of_theirs(api):
    for slug in ("cpo", "ops"):
        current = get(api, f"bots/{slug}/access", "ana-test")
        put(api, f"bots/{slug}/access", {"see": {"everyone": True}, "read": {"people": ["ana", "ben"]},
                                        "write": {"everyone": True}, "revision": current["revision"]})
    other = post(api, "tasks", {"owner": "ops", "title": "Ops weekly sweep", "body": "Routine."})
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    post(api, "tasks", subtask(row["id"], "cara"), attempt["token"])
    # Cara opens the task she was brought into, as its list and SQL already show it to her...
    assert readers(api, row["id"]) == {"cara"} and sql_sees(api, "cara-test", "Confidential: Zip triage.")
    assert api.get(f"/api/v2/tasks/{row['id']}/comments", headers=headers("cara-test")).status_code == 200
    # ...and nothing else of the bots she may not read.
    assert api.get("/api/v2/tasks/" + other["id"], headers=headers("cara-test")).status_code == 403


def test_the_guests_answer_clears_the_wait_for_good(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    current = get(api, "tasks/" + row["id"], attempt["token"])["task"]
    post(api, "tasks/" + row["id"], {"version": current["version"], "status": "waiting", "waiting_on": "ben",
                                     "note": "Ben, please approve the plan."}, attempt["token"])
    card = next(t for t in get(api, "tasks", "ben-test")["tasks"] if t["owner"] == "human:ben")
    with api.app.state.store.read() as c:
        assert TR.blocker_ids(c, row["id"]) == [card["id"]]
    done = post(api, "tasks/" + card["id"], {"version": card["version"], "status": "done", "note": "Approved."},
                "ben-test")
    post(api, "tasks/" + card["id"], {"version": done["version"], "status": "open"}, "ben-test")
    with api.app.state.store.read() as c:
        assert TR.blocker_ids(c, row["id"]) == [] and H.task(c, row["id"])["status"] == "open"


def test_a_run_carrying_work_the_person_cannot_read_does_not_reach_them(api):
    own = task(api, "bot:ops", "bot:ops", "Zip triage")
    theirs = task(api, "human:cara", "bot:ops", "Cara's vendor dispute")
    attempt = run_on(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], theirs["id"]))
    current = get(api, "tasks/" + own["id"], attempt["token"])["task"]
    wait = {"version": current["version"], "status": "waiting", "waiting_on": "ben",
            "note": "Ben, approve the vendor dispute reply? If yes, I will send it."}
    r = api.post("/api/v2/tasks/" + own["id"], json=wait, headers=headers(attempt["token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy", r.text
    for path, body in (("tasks", subtask(own["id"], "ben")),
                       ("task-questions", {"question": "Send the reply?", "on": "ben", "parent": own["id"],
                                           "options": [{"label": "Yes", "then": "Go with Yes"}, {"label": "No", "then": "Go with No"}]})):
        r = api.post("/api/v2/" + path, json=body, headers=headers(attempt["token"]))
        assert r.status_code == 403, (path, r.text)
    # Nothing reached Ben: no guest, no card, and the task's note is unchanged.
    with api.app.state.store.read() as c:
        row = H.task(c, own["id"])
        assert row["guest"] is None and row["status"] != "waiting" and "vendor" not in (row["note"] or "")
    assert readers(api, own["id"]) == set() and not get(api, "tasks", "ben-test")["tasks"]
    # Once Ben reads every task the run carries, the same wait reaches him.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester='human:ben' WHERE id=?", (theirs["id"],))
    post(api, "tasks/" + own["id"], wait, attempt["token"])
    assert readers(api, own["id"]) == {"ben"}


def test_a_question_reaches_the_guest(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    asked = post(api, "task-questions", {"question": "Ship the zip fix in the next release?", "on": "ben",
                                         "parent": row["id"], "options": [{"label": "Yes", "then": "Go with Yes"}, {"label": "No", "then": "Go with No"}]},
                 attempt["token"])
    assert asked["task"]["requester"] == "human:ben" and asked["task"]["private"]
    assert readers(api, row["id"]) == {"ben"} and readers(api, asked["task"]["id"]) == {"ben"}
    r = api.post("/api/v2/task-questions", json={"question": "Or should Cara decide?", "on": "cara",
                                                   "parent": row["id"], "options": [{"label": "Yes", "then": "Go with Yes"}, {"label": "No", "then": "Go with No"}]},
                 headers=headers(attempt["token"]))
    assert r.status_code == 403 and "already reached ben" in r.text, r.text
