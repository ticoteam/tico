"""A batch: what needs a person, frozen and walked; responses collected, applied together on commit;
the assistant's per-item report opening the next batch. backend/batch.py."""

import json

from backend import hubdb as H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401


def ask_ana(api, bot, text, title="Pick the outbound tool", token=None):
    """A bot that owns a task Ana filed asks him its one question."""
    task = post(api, "tasks", {"title": title, "body": "Decide", "owner": bot})
    token = token or setup_attempt(api, bot)[2]["token"]
    return post(api, f"tasks/{task['id']}/ask", {"text": text}, token=token)


def approval_for_ana(api, bot="finance", kind="spend", payload=None, task=None, token=None):
    attempt = {"token": token} if token else setup_attempt(api, bot)[2]
    body = {"kind": kind, "payload": payload or {"amount": 1200, "account": "ads", "what": "LinkedIn ads"}}
    if task:
        body["task_id"] = task
    return post(api, "approvals", body, token=attempt["token"])


def test_responses_are_recorded_not_applied_and_commit_applies_them_with_the_persons_identity(api):
    _, _, finance = setup_attempt(api, "finance")
    # Both decisive items trace back to Ana, so the approval leads on its kind.
    ads = post(api, "tasks", {"title": "Run the LinkedIn ads", "body": "Q4", "owner": "bot:finance"})
    approval = approval_for_ana(api, token=finance["token"], task=ads["id"])
    ask = ask_ana(api, "finance", "Close or Apollo?", token=finance["token"])
    chore = post(api, "tasks", {"title": "Sign the lease", "body": "Ready", "owner": "human:ana"},
                 token=finance["token"])
    junk = post(api, "tasks", {"title": "Drop the old draft to Yair", "body": "Obsolete", "owner": "human:ana"},
                token=finance["token"])
    b = post(api, "batch", {})
    bid = b["id"]
    assert b["item"]["key"] == "approval:" + approval["id"]

    # Responding records; nothing changes yet.
    r = post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "approve", "text": "Go ahead", "heard": "yeah go ahead"})
    assert r["recorded"] and r["n"] == 1 and r["response"]["heard"] == "yeah go ahead"
    assert post(api, "batch", {})["item"]["response"] == [r["response"]], "the current item shows its responses"
    assert get(api, f"approvals/{approval['id']}")["decision"] is None
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "done"}, expected=422)      # an approval is approved or declined
    post(api, f"batch/{bid}/respond", {"kind": "needs_info"}, expected=422)                      # a question needs its text
    nxt = post(api, f"batch/{bid}/next", {})
    assert nxt["item"]["kind"] == "question" and nxt["item"]["question"] == "Close or Apollo?"
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "approve"}, expected=422)   # a question is not an approval
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "answer", "text": "Close. Keep the trial seats."})
    # A response can name another item by position, and the last response to an item wins.
    post(api, f"batch/{bid}/respond", {"kind": "skip", "item": 1})
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "approve", "item": 1, "text": "Go ahead"})
    nxt = post(api, f"batch/{bid}/next", {})
    assert nxt["item"]["key"] == "task:" + chore["id"]
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "done", "text": "Signed this morning"})
    nxt = post(api, f"batch/{bid}/next", {})
    assert nxt["item"]["key"] == "task:" + junk["id"]
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "close", "text": "Not needed any more."})
    post(api, f"batch/{bid}/respond", {"kind": "rule", "text": "Always archive postcard complaints.", "item": 4})
    end = post(api, f"batch/{bid}/next", {})
    assert end["end"] and end["summary"]["approving"] == ["Pay 1200: LinkedIn ads"]
    assert end["summary"]["counts"] == {"approve": 1, "answer": 1, "done": 1, "close": 1, "rule": 1}
    assert end["summary"]["responses"] == 5 and end["summary"]["items"] == 4, "a rule beside a decision is two responses"
    assert get(api, f"tasks/{chore['id']}")["task"]["status"] == "open", "still nothing applied"

    done = post(api, f"batch/{bid}/commit", {})
    assert done["committed"] and done["errors"] == []
    assert done["sent"] == {"finance": 1} and done["recorded"] == {"finance": 4}
    assert get(api, f"approvals/{approval['id']}")["decision"] == "approved"
    assert get(api, f"tasks/{chore['id']}")["task"]["status"] == "done"
    assert get(api, f"tasks/{junk['id']}")["task"]["status"] == "closed"
    with api.app.state.store.read() as c:
        reply = c.execute("SELECT body,from_actor FROM messages WHERE in_reply_to=?", (ask["id"],)).fetchone()
        coo = c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:coo' OR from_actor='bot:coo'").fetchone()[0]
        event = c.execute("SELECT detail FROM events WHERE action='batch.committed'").fetchone()
    assert reply["body"] == "Close. Keep the trial seats." and reply["from_actor"] == "human:ana"
    assert coo == 0, "nothing is written to the assistant's room"
    assert "Rule for finance: Always archive postcard complaints." in json.loads(event["detail"])["applied"]
    assert get(api, "batch")["batch"] is None
    post(api, f"batch/{bid}/commit", {}, expected=409)
    post(api, f"batch/{bid}/respond", {"kind": "skip"}, expected=409)


def test_a_batch_belongs_to_a_person_and_a_failed_item_does_not_stop_the_commit(api):
    _, _, attempt = setup_attempt(api, "finance")
    approval = approval_for_ana(api, token=attempt["token"])
    b = post(api, "batch", {})
    post(api, f"batch/{b['id']}/next", {}, token="ben-test", expected=404)
    post(api, "batch", {}, token=attempt["token"], expected=403)
    post(api, f"batch/{b['id']}/respond", {"kind": "decide", "decision": "approve"})
    # Someone decides the approval by hand before the commit: that item fails, the batch still commits.
    post(api, f"approvals/{approval['id']}", {"decision": "declined"})
    done = post(api, f"batch/{b['id']}/commit", {})
    assert done["committed"] and len(done["errors"]) == 1 and "already declined" in done["errors"][0]
    assert get(api, "batch")["batch"] is None



def test_done_on_a_bots_request_needs_no_words_and_the_bot_hears_who_finished_it(api):
    _, _, finance = setup_attempt(api, "finance")
    chore = post(api, "tasks", {"title": "Sign the lease", "body": "Ready", "owner": "human:ana"}, token=finance["token"])
    b = post(api, "batch", {})
    assert b["item"]["key"] == "task:" + chore["id"]
    post(api, f"batch/{b['id']}/respond", {"kind": "decide", "decision": "done"})
    assert post(api, f"batch/{b['id']}/commit", {})["errors"] == []
    assert get(api, f"tasks/{chore['id']}")["task"]["status"] == "done"
    with api.app.state.store.read() as c:
        told = [r["body"] for r in c.execute("SELECT body FROM messages WHERE to_actor='bot:finance'")]
    assert "Ana marked this done: Sign the lease" in told


def test_what_a_person_asked_for_leads_unless_a_bots_item_presses(api):
    """Needs you, Decide and the lineup put work that traces back to a person (through a bot's subtask too) ahead
    of what bots made for themselves; a bot's overdue or long-waiting item still goes first."""
    _, _, finance = setup_attempt(api, "finance")
    _, _, ops = setup_attempt(api, "ops")
    approval = approval_for_ana(api, token=finance["token"])
    chore = post(api, "tasks", {"title": "Rotate the report template", "body": "Housekeeping", "owner": "human:ana"},
                 token=finance["token"])
    todo = post(api, "tasks", {"title": "Book the venue", "body": "For the offsite", "owner": "human:ana"})
    quote = post(api, "tasks", {"title": "Quote for Acme", "body": "A customer", "owner": "bot:ops"}, token="ben-test")
    confirm = post(api, "tasks", {"title": "Confirm the Acme discount", "body": "For the quote", "owner": "human:ana",
                                  "relations": [{"task": quote["id"], "kind": "parent"}]}, token=ops["token"])
    items = {i["id"]: i for i in get(api, "needs-you")["items"]}
    order = list(items)
    assert order.index(confirm["id"]) < order.index(approval["id"]) < order.index(chore["id"])
    assert order.index(approval["id"]) < order.index(todo["id"]), "a person's own to-do does not lift over a bot's ask"
    assert items[confirm["id"]]["from_person"] == "human:ben", "a bot's subtask keeps the person its parent came from"
    assert not items[approval["id"]]["from_person"] and not items[chore["id"]]["from_person"]
    b = post(api, "batch", {})
    assert b["item"]["key"] == "task:" + confirm["id"]
    assert [g["who"] for g in b["lineup"]] == ["bot:ops", "bot:finance"]

    # Overdue, or waiting (a day for an approval, which holds its bot up): a bot's item goes ahead again.
    overdue = post(api, "tasks", {"title": "Renew the domain", "body": "Lapsed", "owner": "human:ana",
                                  "due": H.shift(H.now(), days=-1)}, token=finance["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE approvals SET created_at=? WHERE id=?", (H.shift(H.now(), days=-2), approval["id"]))
    order = [i["id"] for i in get(api, "needs-you")["items"]]
    # Anything with a due date leads, then a pressing item.
    assert order[:2] == [overdue["id"], approval["id"]] and order.index(overdue["id"]) < order.index(chore["id"])
