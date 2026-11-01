"""Completion delivers new input answers without repeating this attempt's tool sends."""
import json

import pytest

from backend.store import H, encode
from backend.tests.test_api import api, setup_attempt, post, get, ready, claim, runner, assign, headers


def start(api, runner, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, runner["token"])


def finish(api, runner, attempt, text="No matching items."):
    body = {"outcome": "completed", "last_seq": 0, "text": text}
    path = f"attempts/{attempt['id']}/complete"
    done = post(api, path, body, runner["token"], key="completion")
    assert post(api, path, body, runner["token"], key="completion") == done
    return done["message"]


@pytest.mark.parametrize("kind", ["task", "folded"])
def test_equal_answers_to_distinct_inputs_are_delivered_once(api, kind):
    runner, first, attempt = setup_attempt(api)
    start(api, runner, attempt)
    assert finish(api, runner, attempt)["in_reply_to"] == first["id"]
    ready(api, runner, ["ops"])
    if kind == "task":
        task = post(api, "tasks", {"owner": "ops", "title": "Check the second folder", "body": "Check its contents"})
        with api.app.state.store.read() as c:
            second = H.message(c, c.execute("SELECT message_id FROM jobs WHERE state='queued' AND bot='ops'").fetchone()[0])
    else:
        second = post(api, "messages", {"to": "ops", "kind": "ask" if kind == "ask" else "say",
                      "text": "Check the second folder", "conversation_id": first["conversation_id"]})
    attempt = claim(api, runner)
    start(api, runner, attempt)
    if kind == "task":
        post(api, "tasks/" + task["id"], {"version": attempt["task"]["version"], "status": "done", "quiet": True}, attempt["token"])
    if kind == "folded":
        folded = post(api, "chat/ops", {"text": "Include the archive"})
        inputs = post(api, f"attempts/{attempt['id']}/inputs", {}, runner["token"])["messages"]
        assert [m["id"] for m in inputs] == [folded["id"]]
        post(api, f"attempts/{attempt['id']}/inputs/{folded['id']}/ack", {}, runner["token"])
    reply = finish(api, runner, attempt)
    assert reply and reply["in_reply_to"] == second["id"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE from_actor='bot:ops' AND in_reply_to=?", (second["id"],)).fetchone()[0] == 1
        if kind == "folded":
            assert H.message(c, reply["id"])["refs"]["answers"] == [second["id"], folded["id"]]


def test_runner_notices_leave_the_reply_text(api):
    runner, origin, attempt = setup_attempt(api)
    start(api, runner, attempt)
    reply = finish(api, runner, attempt, "Done.\n\nnot pushed: a commit made this turn contains a secret")
    with api.app.state.store.read() as c:
        row = H.message(c, reply["id"])
    assert row["body"] == "Done."
    assert row["refs"]["warnings"] == ["not pushed: a commit made this turn contains a secret"]


@pytest.mark.parametrize("route", ["linked"])
def test_current_attempt_tool_reply_is_not_duplicated(api, route):
    runner, origin, attempt = setup_attempt(api)
    start(api, runner, attempt)
    body = {"text": "Tool answer"}
    if route == "conversation":
        sent = post(api, f"conversations/{origin['conversation_id']}/messages", body, attempt["token"])["message"]
    else:
        body.update(to="human:ana", conversation_id=origin["conversation_id"])
        if route == "linked":
            body["in_reply_to"] = origin["id"]
        sent = post(api, "messages", body, attempt["token"])
    assert finish(api, runner, attempt, "Different summary" if route == "linked" else "Tool answer") is None
    rows = get(api, f"conversations/{origin['conversation_id']}/messages")
    assert [m["id"] for m in rows if m["from_actor"] == "bot:ops"] == [sent["id"]]
    ready(api, runner, ["ops"])
    second = post(api, "chat/ops", {"text": "Check again"})
    attempt = claim(api, runner)
    start(api, runner, attempt)
    assert finish(api, runner, attempt, "Tool answer")["in_reply_to"] == second["id"]


def test_untrusted_attempt_reference_cannot_suppress_a_new_answer(api):
    runner, origin, attempt = setup_attempt(api)
    start(api, runner, attempt)
    old = finish(api, runner, attempt)
    ready(api, runner, ["ops"])
    second = post(api, "chat/ops", {"text": "Check again"})
    attempt = claim(api, runner)
    # Even an old bot message naming the new attempt is not trusted provenance.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", (encode({"turn_id": attempt["id"]}), old["id"]))
    start(api, runner, attempt)
    assert finish(api, runner, attempt)["in_reply_to"] == second["id"]


@pytest.mark.parametrize("revocation", ["generation", "private-task"])
def test_task_completion_replay_requires_current_runner_ownership_and_private_access(api, revocation):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Check the packet", "body": "Check the fixture packet", "private": True})
    attempt = claim(api, machine)
    start(api, machine, attempt)
    task = post(api, "tasks/" + task["id"], {"version": attempt["task"]["version"], "status": "done", "quiet": True}, attempt["token"])
    reply = finish(api, machine, attempt, "Fixture task result")
    assert reply and reply["refs"]["task"] == task["id"]
    # A replay identity is only for reading this receipt; the runner gains no domain credentials.
    get(api, "tasks/" + task["id"], machine["token"], expected=403)
    if revocation in ("assignment", "generation"):
        destination = runner(api, label="Other fixture Mac") if revocation == "assignment" else machine
        assign(api, destination, "ops", generation=attempt["generation"])
    elif revocation == "runner":
        post(api, f"runners/{machine['runner_id']}/revoke", {})
    elif revocation == "private-task":
        post(api, "tasks/" + task["id"], {"version": task["version"], "owner": "finance"})
    else:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    result = api.post(f"/api/v2/attempts/{attempt['id']}/complete",
                     json={"outcome": "completed", "last_seq": 0, "text": "Fixture task result"},
                     headers=headers(machine["token"], "completion"))
    assert result.status_code in (401, 403, 404), result.text
    assert "Fixture task result" not in result.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE from_actor='bot:ops' AND body='Fixture task result'").fetchone()[0] == 1


@pytest.mark.parametrize("notice", ["current", "listed-private"])
def test_routine_reply_in_a_personal_room_is_not_lost_to_an_unrelated_private_task(api, notice):
    # A routine's task lands in the person's room with the bot while the bot holds a private
    # task from the keeper (nightly learnings). The new-task notice used to list that private
    # task as related work, which made the whole run private and refused its reply.
    runner, origin, attempt = setup_attempt(api)
    start(api, runner, attempt)
    finish(api, runner, attempt)
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, H.KEEPER, "Nightly learnings", "Read the day's runs", "bot:ops", private=True)
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        task = H.task_create(c, H.KEEPER, "Refresh the map of the docs", "Refresh it", "bot:ops",
                             deduplicate=False, conversation_id=origin["conversation_id"])
        wake = c.execute("SELECT m.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'").fetchone()
        assert hidden["id"] not in (wake["refs"] or "")
        if notice == "listed-private":
            # What the server wrote before: the private task listed in the room's notice.
            refs = {**H._json(wake["refs"], {}), "reconcile": {"active": [{"id": hidden["id"]}]}}
            c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(refs), wake["id"]))
    ready(api, runner, ["ops"])
    attempt = claim(api, runner)
    start(api, runner, attempt)
    reply = finish(api, runner, attempt, "Map refreshed.")
    with api.app.state.store.read() as c:
        assert tuple(c.execute("SELECT state,final_text FROM attempts WHERE id=?", (attempt["id"],)).fetchone()) == ("completed", "Map refreshed.")
        room = H.task(c, task["id"])["conversation_id"]
    # The task has its own room; the person's room got only the "Started task" line.
    started = [m for m in get(api, f"conversations/{origin['conversation_id']}/messages") if m["created_at"] > task["created_at"]]
    assert [(m["refs"] or {}).get("task_started") for m in started] == [True]
    shown = get(api, f"conversations/{room}/messages")
    if notice == "current":
        assert reply and reply["to_actor"] == "human:ana" and reply["body"] == "Map refreshed."
    else:
        # The reply stays off the room; the person is told, and nothing of the private task is shown.
        assert reply is None
        told = [m for m in shown if m["from_actor"] == H.KEEPER and (m.get("refs") or {}).get("withheld")]
        assert len(told) == 1 and told[0]["to_actor"] == "human:ana"
        assert not any("Map refreshed." in m["body"] or "Nightly" in m["body"] for m in shown)



def test_a_task_session_becomes_the_tasks_one_primary_session_and_a_shared_thread_does_not(api):
    r = runner(api, "ana")
    assign(api, r, "ops")

    def turn(task, thread):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True},
                                        "capabilities": ["task_lanes_v2"]}, token=r["token"])
        attempt = claim(api, r)
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": thread}, r["token"])
        finish(api, r, attempt)
        with api.app.state.store.read() as c:
            return {link["url"]: json.loads(link["detail"])["primary"]
                    for link in H.task_links(c, task["id"]) if link["kind"] == "session"}

    # A bot set to one shared thread carries its other conversations there too: no task's session.
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps({**config, "session": "bot"}),))
    shared = post(api, "tasks", {"owner": "ops", "title": "Check the shared folder", "body": "Check it"})
    assert turn(shared, "shared-thread") == {}
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps({**config, "session": "task"}),))
    task = post(api, "tasks", {"owner": "ops", "title": "Check the task folder", "body": "Check it"})
    assert turn(task, "old") == {"session:ops/old": True}
    # Someone linked the next thread before it ran: it becomes the one primary, the old one an alternate.
    post(api, f"tasks/{task['id']}/links", {"url": "ops/new", "kind": "session"})
    post(api, f"tasks/{task['id']}/comments", {"text": "Check it again"})
    assert turn(task, "new") == {"session:ops/old": False, "session:ops/new": True}


def test_a_runs_confirmed_thread_replaces_the_one_it_started_on_and_only_while_it_runs(api):
    # A run that left a large thread starts on the task's old one and names the new one once its session is up
    # (runner-rs execute.rs Fresh): the run, the bot's session and the task's primary session all move to it.
    r = runner(api, "ana")
    assign(api, r, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True},
                                    "capabilities": ["task_lanes_v2"]}, token=r["token"])
    task = post(api, "tasks", {"owner": "ops", "title": "Watch the Acme release", "body": "Watch build 41"})
    attempt = claim(api, r)
    path = f"attempts/{attempt['id']}/"
    post(api, path + "started", {"thread_id": "old-thread"}, r["token"])

    def threads():
        with api.app.state.store.read() as c:
            links = {link["url"]: json.loads(link["detail"])["primary"]
                     for link in H.task_links(c, task["id"]) if link["kind"] == "session"}
            ran = c.execute("SELECT a.thread_id, t.thread_id FROM attempts a JOIN turns t ON t.id=a.id WHERE a.id=?",
                            (attempt["id"],)).fetchone()
            session = c.execute("SELECT thread_id FROM bot_sessions WHERE bot='ops'").fetchone()
            return links, tuple(ran), session[0]

    assert threads() == ({"session:ops/old-thread": True}, ("old-thread", "old-thread"), "old-thread")
    assert post(api, path + "thread", {"thread_id": "new-thread"}, r["token"], key="thread") == {"thread_id": "new-thread"}
    assert threads() == ({"session:ops/new-thread": True}, ("new-thread", "new-thread"), "new-thread")
    # Another computer cannot move it, and a finished run keeps the thread it ended on.
    other = runner(api, "ana", label="Other Mac")
    post(api, path + "thread", {"thread_id": "stray-thread"}, other["token"], expected=403)
    finish(api, r, attempt)
    post(api, path + "thread", {"thread_id": "late-thread"}, r["token"], expected=409)
    assert threads()[1] == ("new-thread", "new-thread")
