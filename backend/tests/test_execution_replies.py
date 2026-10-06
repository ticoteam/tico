"""Completion delivers new input answers without repeating this attempt's tool sends."""
import pytest

from backend.store import H, encode
from backend.execution import commit_exclusion_notices
from backend.tests.test_api import api, setup_attempt, post, get, ready, claim, runner, assign, headers


def start(api, runner, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, runner["token"])


def finish(api, runner, attempt, text="No matching items."):
    body = {"outcome": "completed", "last_seq": 0, "text": text}
    path = f"attempts/{attempt['id']}/complete"
    done = post(api, path, body, runner["token"], key="completion")
    assert post(api, path, body, runner["token"], key="completion") == done
    return done["message"]


def test_commit_exclusion_metadata_is_added_to_reply_refs_without_changing_transcript(api):
    runner, origin, attempt = setup_attempt(api)
    start(api, runner, attempt)
    text = "Finished the report.\n\nleft out of the commit: reports/<fixture>.md (contains a secret)"
    exclusions = [{"path": "reports/<fixture>.md", "reason": "contains a secret"}]
    created = finish(api, runner, attempt, text)
    reply = next(message for message in get(api, f"conversations/{origin['conversation_id']}/messages")
                 if message["id"] == created["id"])
    assert reply["body"] == text
    assert reply["refs"]["turn_id"] == attempt["id"]
    assert reply["refs"]["run"]["attempt_id"] == attempt["id"]
    assert reply["refs"]["commit_exclusions"] == exclusions


def test_commit_exclusion_recognition_requires_exact_terminal_runner_notices():
    first = "left out of the commit: reports/a.md (contains a secret)"
    second = "left out of the commit: private notes.txt (contains a secret)"
    assert commit_exclusion_notices("Answer.\n\n" + first + "\n\n" + second +
                                   "\n\nnot pushed: a commit made this turn contains a secret") == [
        {"path": "reports/a.md", "reason": "contains a secret"},
        {"path": "private notes.txt", "reason": "contains a secret"},
    ]
    assert commit_exclusion_notices("An example:\n\n" + first + "\n\nPlease preserve the quote.") == []
    assert commit_exclusion_notices("left out of the commit: prose (contains a secret) with punctuation.") == []
    assert commit_exclusion_notices("Answer.\n\n\n" + first) == [
        {"path": "reports/a.md", "reason": "contains a secret"},
    ]
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
        post(api, "tasks/" + task["id"], {"version": task["version"], "status": "done", "quiet": True}, attempt["token"])
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
        c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode({"turn_id": attempt["id"]}), old["id"]))
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
    task = post(api, "tasks/" + task["id"], {"version": task["version"], "status": "done", "quiet": True}, attempt["token"])
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
