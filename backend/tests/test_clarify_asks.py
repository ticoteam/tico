"""One tap on Clarify sends a stale bot ask's bot the experiment's winning prompt, once per ask."""

from backend import clarify_asks
from backend import hubdb as H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401


def _stale_ask(api, task_id, body, days=10):
    with api.app.state.store.transaction() as c:
        ask = H.say(c, "bot:finance", "human:ana", body, kind="ask",
                    conversation_id=H.task(c, task_id)["conversation_id"],
                    refs={"task": task_id, "questions": [{"id": "q", "question": body, "multi": False,
                                                          "options": [{"label": "Gusto"}, {"label": "Rippling"}]}]})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), days=-days), ask["id"]))
        return ask


def test_clarify_sends_the_winning_prompt_once_and_leaves_the_ask_open(api):
    _, _, finance = setup_attempt(api, "finance")
    pick = post(api, "tasks", {"title": "Gusto or Rippling for payroll?",
                               "body": "Should payroll move to Gusto or Rippling?", "owner_id": "human:ana"})
    ask = _stale_ask(api, pick["id"], "Should payroll move to Gusto or Rippling?")

    card = next(i for i in get(api, "needs-you")["items"] if i["id"] == pick["id"])
    assert card["ask"]["id"] == ask["id"] and card["ask"].get("stale") is True

    done = post(api, "needs-you/clarify", {"ask": ask["id"]})
    assert done["bot"] == "bot:finance" and done["task"] == pick["id"]
    with api.app.state.store.transaction() as c:
        nudge = H.message(c, done["message"])
        assert nudge["sender_id"] == "human:ana" and nudge["recipient_id"] == "bot:finance"
        assert clarify_asks.CLARIFY_PROMPT in (nudge["body"] or "")
        assert "Gusto or Rippling" in (nudge["body"] or "")
        assert H.message(c, ask["id"])["answered_by_id"] is None     # the ask stays open
    # The card stays on her list: she asked for a clearer question, not an answer.
    assert pick["id"] in {i["id"] for i in get(api, "needs-you")["items"]}

    post(api, "needs-you/clarify", {"ask": ask["id"]}, expected=409)          # once per ask
    post(api, "needs-you/clarify", {"ask": ask["id"]}, token="ben-test", expected=403)  # not his question
    post(api, "needs-you/clarify", {"ask": "nope"}, expected=404)

    fresh = _stale_ask(api, pick["id"], "And should it start in January?", days=1)
    assert "stale" not in next(i for i in get(api, "needs-you")["items"]
                               if i["id"] == pick["id"])["ask"]
    post(api, "needs-you/clarify", {"ask": fresh["id"]}, expected=422)        # still fresh

    post(api, f"tasks/{pick['id']}/answers", {"target": {"comment": ask["id"]}, "answers": {"q": ["Gusto"]}})
    post(api, "needs-you/clarify", {"ask": ask["id"]}, expected=409)          # answered since


def test_clarify_on_a_loose_ask_reaches_the_bot_with_no_task(api):
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        ask = H.say(c, "bot:finance", "human:ana", "Quick one: should the demo stay static?", kind="ask",
                    refs={"questions": [{"id": "q", "question": "Quick one: should the demo stay static?",
                                         "multi": False, "options": [{"label": "Yes"}, {"label": "No"}]}]})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), days=-10), ask["id"]))
    done = post(api, "needs-you/clarify", {"ask": ask["id"]})
    assert done == {"bot": "bot:finance", "task": None, "message": done["message"]}
    with api.app.state.store.transaction() as c:
        nudge = H.message(c, done["message"])
        assert nudge["recipient_id"] == "bot:finance" and clarify_asks.CLARIFY_PROMPT in (nudge["body"] or "")


def test_clarify_on_a_waiting_task_wakes_the_bot_but_keeps_the_wait(api):
    _, _, finance = setup_attempt(api, "finance")
    vendor = post(api, "tasks", {"title": "Switch the payroll vendor", "body": "x", "owner_id": "bot:finance"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?",
                  ("Waiting on Ana to pick the vendor.", vendor["id"]))
    ask = _stale_ask(api, vendor["id"], "Should payroll move to Gusto or Rippling?")
    before = get(api, f"tasks/{vendor['id']}")["task"]["updated_at"]

    done = post(api, "needs-you/clarify", {"ask": ask["id"]})
    with api.app.state.store.transaction() as c:
        row = H.task(c, vendor["id"])
        assert row["waiting_on"] == "human:ana" and row["updated_at"] > before
        assert c.execute("SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
                         (vendor["id"], "bot:finance", H.now())).fetchone() is not None
    assert done["task"] == vendor["id"]
