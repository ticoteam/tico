"""The live reply for a custom frontend: its pieces in order, and which run took each message."""
import json

from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401


def start(api):
    r, msg, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    return r, msg, attempt["id"]


def send_events(api, r, aid, events, first=1):
    post(api, f"attempts/{aid}/events", {"events": [{"seq": first + i, "kind": k, "payload": p}
                                                    for i, (k, p) in enumerate(events)]}, token=r["token"])


def running(api, msg):
    return get(api, f"conversations/{msg['conversation_id']}/snapshot")["execution"]


def test_a_tool_is_one_short_label_and_never_its_arguments(api):
    r, msg, aid = start(api)
    secret = "hunter2-token"
    send_events(api, r, aid, [
        ("message", {"text": "Filing it now.", "final": False}),
        ("tool", {"tool": "mcp__hub__task_create", "status": "started", "item_id": "c1",
                  "input": {"title": "x", "api_key": secret}, "command": f"curl -H 'Authorization: {secret}'"}),
        ("tool", {"tool": "mcp__hub__task_create", "status": "completed", "item_id": "c1", "output": secret}),
        ("message", {"text": "Filed.", "final": False})])
    x = running(api, msg)
    assert [(p["kind"], p["text"]) for p in x["parts"]] == [
        ("progress", "Filing it now."), ("tool", "Ran hub task create"), ("reply", "Filed.")]
    assert all(set(p) == {"kind", "text", "at"} for p in x["parts"])
    assert secret not in json.dumps(x)
    assert x["text"] == "Filing it now.\n\nFiled.", "a tool call is not part of the words"


def test_a_follow_up_says_which_run_took_it_and_the_reply_lists_both(api):
    r, first, aid = start(api)
    cid = first["conversation_id"]
    follow = post(api, "chat/ops", {"text": "Also include the Q3 numbers."})
    assert "run" not in next(m for m in get(api, f"conversations/{cid}/snapshot")["messages"] if m["id"] == follow["id"]), \
        "no run has taken it yet"
    # The runner offers it to the working turn and acknowledges it.
    assert [m["id"] for m in post(api, f"attempts/{aid}/inputs", {}, token=r["token"])["messages"]] == [follow["id"]]
    post(api, f"attempts/{aid}/inputs/{follow['id']}/ack", {}, token=r["token"])
    job = running(api, first)["job_id"]
    with api.app.state.store.read() as c:
        starter_job = c.execute("SELECT id FROM jobs WHERE message_id=?", (first["id"],)).fetchone()[0]

    def by_id(messages):
        return {m["id"]: m for m in messages}
    snap = by_id(get(api, f"conversations/{cid}/snapshot")["messages"])
    assert snap[first["id"]]["run"] == {"job_id": starter_job, "run_id": aid, "state": "started_run"}
    assert snap[follow["id"]]["run"] == {"job_id": starter_job, "run_id": aid, "state": "added_to_run"}
    assert job != starter_job, "the follow-up has a job of its own, but the run it joined is the first one"

    send_events(api, r, aid, [("message", {"text": "Both done.", "final": True})])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Both done.", "last_seq": 1}, token=r["token"])
    for messages in (get(api, f"conversations/{cid}/snapshot")["messages"], get(api, f"conversations/{cid}/messages")):
        shown = by_id(messages)
        reply = next(m for m in messages if m["sender_id"] == "bot:ops")
        assert reply["answers"] == [first["id"], follow["id"]]
        assert (reply["run"]["job_id"], reply["run"]["run_id"]) == (starter_job, aid)
        assert shown[first["id"]]["run"]["state"] == "started_run" and shown[follow["id"]]["run"]["state"] == "added_to_run"
    # What the reply says is stored with it, so it is still there after a restart.
    with api.app.state.store.read() as c:
        stored = json.loads(c.execute("SELECT refs FROM messages WHERE sender_id='bot:ops'").fetchone()[0])
    assert stored["answers"] == [first["id"], follow["id"]] and stored["run"] == {"job_id": starter_job, "attempt_id": aid}


def test_a_subagent_and_its_calls_are_steps_but_not_the_reply(api):
    """Claude and Codex runners report a subagent's start and end, and the calls it makes (`parent_id`). The
    steps show the subagent by its type and its calls under it; neither its description nor its prompt is read."""
    r, msg, aid = start(api)
    secret = "hunter2-token"
    agent = {"id": "a1", "parent_id": None, "type": "general-purpose", "description": f"count words {secret}",
             "model": None}
    send_events(api, r, aid, [
        ("status", {"state": "active", "tool_events": True}),
        ("tool", {"tool": "Bash", "status": "started", "item_id": "b1", "summary": "cat note.txt", "parent_id": None}),
        ("tool", {"tool": "Bash", "status": "completed", "item_id": "b1", "parent_id": None}),
        ("tool", {"tool": "Agent", "status": "started", "item_id": "a1", "summary": secret, "parent_id": None,
                  "subagent": {**agent, "status": "started"}}),
        ("tool", {"tool": "Bash", "status": "started", "item_id": "b2", "summary": f"wc -w {secret}", "parent_id": "a1"}),
        ("tool", {"tool": "Bash", "status": "failed", "item_id": "b2", "parent_id": "a1"}),
        ("tool", {"tool": "Agent", "status": "completed", "item_id": "a1", "parent_id": None,
                  "subagent": {**agent, "status": "completed", "tool_uses": 1}}),
        ("message", {"text": "One word.", "final": False})])
    steps = get(api, f"turns/{aid}/steps")
    assert [(s["kind"], s["tool"], s["text"], s.get("nested", False)) for s in steps["steps"]] == [
        ("tool", "Bash", "", False),
        ("subagent", "subagent", "general-purpose", False),
        ("tool", "Bash", "failed", True),
        ("said", "", "One word.", False)]
    assert steps["tool_calls"] == 2
    assert secret not in json.dumps(steps)
    parts = running(api, msg)["parts"]
    assert [(p["kind"], p["text"]) for p in parts] == [
        ("tool", "Ran Bash"), ("tool", "Ran Agent"), ("reply", "One word.")]
    assert secret not in json.dumps(parts)
