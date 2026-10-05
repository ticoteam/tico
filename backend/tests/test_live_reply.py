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


def test_separate_messages_are_joined_with_a_blank_line(api):
    r, msg, aid = start(api)
    send_events(api, r, aid, [
        ("delta", {"text": "keep the bot ", "delta_kind": "text"}), ("delta", {"text": "planned.", "delta_kind": "text"}),
        ("message", {"text": "keep the bot planned.", "final": False}),
        ("delta", {"text": "I've filed ", "delta_kind": "text"}), ("delta", {"text": "the build", "delta_kind": "text"}),
        ("message", {"text": "I've filed the build", "final": False}),
        ("message", {"text": "I've filed the build", "final": True})])
    x = running(api, msg)
    assert x["text"] == "keep the bot planned.\n\nI've filed the build"
    assert [(p["kind"], p["text"]) for p in x["parts"]] == [("reply", "keep the bot planned."), ("reply", "I've filed the build")]
    assert all(p["at"] for p in x["parts"])


def test_deltas_inside_one_message_are_not_split(api):
    r, msg, aid = start(api)
    send_events(api, r, aid, [("delta", {"text": t, "delta_kind": "text"}) for t in ("Hel", "lo, ", "wor", "ld")])
    x = running(api, msg)
    assert x["text"] == "Hello, world" and [p["text"] for p in x["parts"]] == ["Hello, world"]
    # The message that ends it holds the same words, and changes nothing.
    send_events(api, r, aid, [("message", {"text": "Hello, world", "final": False})], first=5)
    assert running(api, msg)["text"] == "Hello, world"
    # A model's thinking is not the reply.
    send_events(api, r, aid, [("delta", {"text": "hmm", "delta_kind": "thought"})], first=6)
    assert running(api, msg)["text"] == "Hello, world"


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
    assert snap[first["id"]]["run"] == {"job_id": starter_job, "attempt_id": aid, "state": "started_run"}
    assert snap[follow["id"]]["run"] == {"job_id": starter_job, "attempt_id": aid, "state": "added_to_run"}
    assert job != starter_job, "the follow-up has a job of its own, but the run it joined is the first one"

    send_events(api, r, aid, [("message", {"text": "Both done.", "final": True})])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Both done.", "last_seq": 1}, token=r["token"])
    for messages in (get(api, f"conversations/{cid}/snapshot")["messages"], get(api, f"conversations/{cid}/messages")):
        shown = by_id(messages)
        reply = next(m for m in messages if m["from_actor"] == "bot:ops")
        assert reply["answers"] == [first["id"], follow["id"]]
        assert (reply["run"]["job_id"], reply["run"]["attempt_id"]) == (starter_job, aid)
        assert shown[first["id"]]["run"]["state"] == "started_run" and shown[follow["id"]]["run"]["state"] == "added_to_run"
    # What the reply says is stored with it, so it is still there after a restart.
    with api.app.state.store.read() as c:
        stored = json.loads(c.execute("SELECT refs_json FROM messages WHERE from_actor='bot:ops'").fetchone()[0])
    assert stored["answers"] == [first["id"], follow["id"]] and stored["run"] == {"job_id": starter_job, "attempt_id": aid}


def test_the_snapshot_live_events_point_to_carries_the_same_fields(api):
    # A page following GET /api/v2/events reads the snapshot again when its conversation changes.
    r, first, aid = start(api)
    send_events(api, r, aid, [("message", {"text": "One.", "final": False}), ("message", {"text": "Two.", "final": False})])
    snapshot = get(api, f"conversations/{first['conversation_id']}/snapshot")
    assert snapshot["execution"]["text"] == "One.\n\nTwo."
    assert snapshot["messages"][0]["run"]["state"] == "started_run"
    assert "goal" in snapshot
