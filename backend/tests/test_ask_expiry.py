"""An unanswered ask leaves a person's queue when a newer ask or the task's end replaces it; an old one stays, stale,
until the weekday batch sends it back to its bot (backend/still_needed.py)."""
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.store import H


def needs(api, who="ana-test"):
    response = api.get("/api/v2/needs-you", headers=headers(who))
    assert response.status_code == 200, response.text
    return response.json()


def bot_ask(c, tid, text, refs=None, to="human:ana"):
    task = H.task(c, tid)
    return H.say(c, "bot:ops", to, text, kind="ask", conversation_id=task["conversation_id"],
                 refs={"task": tid, **(refs or {})})


def test_a_newer_ask_or_the_tasks_end_supersedes_and_old_asks_fold_out(api):
    tid = post(api, "tasks", {"owner_id": "ops", "title": "Pick a vendor", "body": "x"})["id"]
    other = post(api, "tasks", {"owner_id": "ops", "title": "Pick a venue", "body": "x"})["id"]
    with api.app.state.store.transaction() as c:
        first = bot_ask(c, tid, "Which vendor, A or B?")
        budget = bot_ask(c, tid, "What is the budget?", to="human:ben")
        second = bot_ask(c, tid, "Vendor B is out of stock; is A fine?")
        # only the same recipient's earlier question is replaced: Ben's stays open
        assert [a["id"] for a in H.open_task_asks(c, H.task(c, tid))] == [second["id"], budget["id"]]
        H.supersede_asks(c, tid, from_actor="bot:ops", to_actor="human:ben")
        assert H.message(c, first["id"])["superseded_at"] and not H.message(c, second["id"])["superseded_at"]
        assert H.waiting_for(c, H.task(c, tid)) == "an unanswered question"
        bot_ask(c, tid, "Ship it?", {"questions": [{"id": "ok", "question": "Ship?"}]})
        H.task_update(c, "bot:ops", tid, status="done", note="Ordered")
        # done ends every question asked about the work, picks too; the acceptance question asked of its requester
        # once it is done stays until closing ends it
        assert H.open_task_asks(c, H.task(c, tid)) == []
        c.execute("UPDATE tasks SET requester_id='human:ana' WHERE id=?", (tid,))
        review = bot_ask(c, tid, "Accept the order?", {"questions": [{"id": "ok", "question": "Accept?"}]})
        assert [a["id"] for a in H.open_task_asks(c, H.task(c, tid))] == [review["id"]]
        assert tid in [t["id"] for t in H.tasks_asked_of(c, "human:ana")]
        H.task_close(c, "human:ana", tid)
        assert H.open_task_asks(c, H.task(c, tid)) == []
        old = bot_ask(c, other, "Indoor or outdoor?")
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), days=-4), old["id"]))
    queue = needs(api)
    assert tid not in [i["id"] for i in queue["items"]] + [i["id"] for i in queue["older"]]
    stale = next(i for i in queue["items"] if i["id"] == other)
    assert stale["ask"]["stale"] is True and queue["older"] == []
    assert api.get("/api/v2/needs-you?count=1", headers=headers()).json()["count"] == len(queue["items"])
