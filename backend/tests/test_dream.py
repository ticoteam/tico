"""Dream (backend/dream.py, docs/learnings.md): a person starts one cleanup task for a bot, its one question reaches
them without ask ingest, their answer wakes the bot, and the commit it makes is that dream's."""

import json

from backend import ask_ingest as A
from backend import hubdb as H
from backend.tests import test_ask_ingest as ingest
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, restrict, runner  # noqa: F401
from backend.tests.test_ask_ingest import gate, plate   # noqa: F401  (the ask ingest fixture)

QUESTIONS = "1. Should weekly reports go to Ana or the team? Suggested: the team\n2. Drop the old deploy rule? Suggested: yes"


def dream(api, token="ben-test", bot="ops", expected=200):
    r = api.post(f"/api/v2/bots/{bot}/dream", json={}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def current(api, token="ben-test", bot="ops"):
    r = api.get(f"/api/v2/bots/{bot}/dream", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["dream"]


def test_one_dream_at_a_time_through_the_task_create_path(api):
    assert current(api) is None
    first = dream(api)["dream"]
    assert first["state"] == "starting" and first["questions"] is None
    assert dream(api)["dream"]["task"] == first["task"], "a second start returns the open dream"
    with api.app.state.store.read() as c:
        row = H.task(c, first["task"])
        assert (row["owner_id"], row["requester_id"], row["title"]) == ("bot:ops", "human:ben", "Dream")
        assert H.task_labels(row) == ["dream"]
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Dream'").fetchone()[0] == 1
        # Filed as POST /api/v2/tasks files work for a bot: its run is queued.
        assert c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' "
                         "AND json_extract(m.refs,'$.task')=?", (first["task"],)).fetchone()[0] == 1
    # Someone else's start while it runs gets the same dream, not a second task.
    assert dream(api, "ana-test")["dream"]["task"] == first["task"]


def test_a_stateless_bot_is_refused(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.stateless',json('true')) WHERE bot='ops'")
    assert dream(api, expected=409)["error"]["code"] == "stateless"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Dream'").fetchone()


def test_someone_who_may_not_give_the_bot_work_is_refused(api):
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["ana", "ben", "cara"]}, read={"people": ["ana", "ben", "cara"]},
                 write={"people": ["ana", "ben"]})
    r = api.post("/api/v2/bots/ops/dream", json={}, headers=headers("cara-test"))
    assert r.status_code in (403, 404), r.text
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Dream'").fetchone()
    assert dream(api)["dream"]["task"]


def test_a_dreams_question_skips_ask_ingest_and_reaches_the_person(gate):
    api, model, token = gate
    plate(api, 15)                                  # a plate full enough that ingest would send a question back
    tid = dream(api)["dream"]["task"]
    r = api.post(f"/api/v2/tasks/{tid}/ask", json={"text": QUESTIONS}, headers=headers(token))
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and model.calls == [] and ingest.items(api) == []
    with api.app.state.store.read() as c:
        row = H.task(c, tid)
        assert row["status"] == "waiting"
        assert [a["recipient_id"] for a in H.open_task_asks(c, row)] == ["human:ben"]
    # Any other task's question from the same bot is still judged.
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, "human:ben", "Plan the November launch", "Dates.", "bot:ops", lint=False)
    r = api.post(f"/api/v2/tasks/{other['id']}/ask", json={"text": "Which date?"}, headers=headers(token))
    assert r.status_code == 200 and r.json()["ingest"]["outcome"] == A.SENT_BACK, r.text


def test_asking_answering_and_the_dreams_own_commit(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid = dream(api)["dream"]["task"]
    first = claim(api, machine, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=machine["token"])
    assert current(api)["state"] == "dreaming"
    post(api, f"tasks/{tid}/ask", {"text": QUESTIONS}, token=first["token"])
    seen = current(api)
    assert seen["state"] == "asking" and seen["questions"]["text"] == QUESTIONS and seen["questions"]["mine"]
    assert current(api, "ana-test")["questions"]["mine"] is False
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Asked", "last_seq": 0},
         token=machine["token"])

    # The person answers the question itself, as Needs you does: the bot is woken on the dream.
    post(api, f"messages/{seen['questions']['message_id']}/answer", {"text": "1. the team\n2. yes"}, token="ben-test")
    assert current(api)["state"] == "dreaming"
    second = claim(api, machine, "ops")
    assert second and second["id"] != first["id"]
    commit = {"sha": "d" * 40, "subject": "Dream: merged report rules", "author": "ops",
              "committed": H.now(), "files": ["memory/learnings.md"], "attempt": second["id"], "shared": True}
    post(api, "bots/ops/memory/report", {"commits": [commit]}, machine["token"])
    [update] = get(api, "bots/ops/memory", token="ben-test")["updates"]
    assert update["source"]["task"] == tid, "the answer's run is the dream's"

    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", tid, status="done", note="Merged two report rules; dropped the old deploy rule.")
    done = current(api)
    assert done["state"] == "done" and done["closed_at"] and "Merged two report rules" in done["summary"]
    assert len(json.dumps(done)) < 6000
    # The next dream reads from where this one ended, and starts as a new task.
    again = dream(api)["dream"]
    assert again["task"] != tid and again["state"] == "starting"
    with api.app.state.store.read() as c:
        assert done["closed_at"][:10] in H.task(c, again["task"])["body"]
