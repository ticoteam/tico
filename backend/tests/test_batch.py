"""A batch: what needs a person, frozen and walked; responses collected, applied together on commit;
the assistant's per-item report opening the next batch. backend/batch.py."""

import json

from backend import batch
from backend import hubdb as H
from backend.store import Problem
from backend.tests.test_api import api, get, post, setup_attempt


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
    approval = approval_for_ana(api, token=finance["token"])
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
    assert end["end"] and end["summary"]["approving"] == ["Approve this spend"]
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
        event = c.execute("SELECT detail_json FROM events WHERE action='batch.committed'").fetchone()
    assert reply["body"] == "Close. Keep the trial seats." and reply["from_actor"] == "human:ana"
    assert coo == 0, "nothing is written to the assistant's room"
    assert "Rule for finance: Always archive postcard complaints." in json.loads(event["detail_json"])["applied"]
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


def walk(api, b):
    """Every item key of a batch, in order, leaving the cursor at the end."""
    keys = [b["item"]["key"]] if b.get("item") else []
    while not b.get("end"):
        b = post(api, f"batch/{b['id']}/next", {})
        if b.get("item"):
            keys.append(b["item"]["key"])
    return keys



def test_a_bot_task_waiting_on_a_person_needs_that_person_until_they_act(api):
    """A bot's own task set waiting on Ana (`--on`) is one item in her Needs you and batch, its title
    and waiting note, and counts toward the bot's needs_human. Her "done" goes back on the task, wakes
    the bot and takes the item off her list; the task's status stays the bot's."""
    _, _, attempt = setup_attempt(api, "finance")
    token = attempt["token"]
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, "finance", state="idle")
    task = post(api, "tasks", {"title": "Roll out v2 to the staging host", "body": "Ship it", "owner": "finance"},
                token=token)
    move = {"version": task["version"], "status": "waiting"}
    for body, why in ((move, "nobody will answer"), ({**move, "waiting_on": "finance"}, "not a person"),
                      ({"version": task["version"], "waiting_on": "ana"}, "set --status waiting")):
        assert why in str(post(api, f"tasks/{task['id']}", body, token=token, expected=422)), body
    note = "Grant me SSH access to the staging host"
    task = post(api, f"tasks/{task['id']}", {**move, "waiting_on": "ana", "note": note}, token=token)
    assert (task["status"], task["waiting_on"]) == ("waiting", "human:ana")
    assert get(api, "status?bot=finance")["status"]["needs_human"] == 1

    need = next(it for it in get(api, "needs-you")["items"] if it["id"] == task["id"])
    assert need["kind"] == "waiting"
    b = post(api, "batch", {"bot": "finance"})
    item = b["item"]
    assert (item["key"], item["kind"], item["from"], item["title"], item["note"]) == (
        "task:" + task["id"], "waiting", "bot:finance", "Roll out v2 to the staging host", note)
    post(api, f"batch/{b['id']}/respond", {"kind": "decide", "decision": "approve", "text": "ok"}, expected=422)
    post(api, f"batch/{b['id']}/respond", {"kind": "decide", "decision": "done", "text": "Added your key"})
    done = post(api, f"batch/{b['id']}/commit", {})
    assert done["errors"] == [] and done["applied"][0].startswith("Told finance")

    task = get(api, f"tasks/{task['id']}")["task"]
    assert (task["status"], task["waiting_on"]) == ("waiting", None)
    assert get(api, "status?bot=finance")["status"]["needs_human"] == 0
    assert all(it["id"] != task["id"] for it in get(api, "needs-you")["items"])
    with api.app.state.store.read() as c:
        told = c.execute("SELECT 1 FROM messages WHERE from_actor='human:ana' AND to_actor='bot:finance' "
                         "AND body='Added your key'").fetchone()
    assert told, "her answer is on the task, to the bot"
    # Any other move clears who it waits on.
    post(api, f"tasks/{task['id']}", {"version": task["version"], "waiting_on": "ana"}, token=token)
    task = get(api, f"tasks/{task['id']}")["task"]
    task = post(api, f"tasks/{task['id']}", {"version": task["version"], "status": "doing"}, token=token)
    assert task["waiting_on"] is None
