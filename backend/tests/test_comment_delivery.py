"""A task comment's delivery (docs/tasks.md, Conversation; backend/delivery.py): steer reaches the bot's running turn
now, queue runs right after it, defer waits for the bot's next turn on the task or its deadline, goal sets the task
session's goal. The API, the hub CLI and the MCP tool share one rule, tested here."""

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, runner  # noqa: F401
from backend.tests.test_mcp import call
from clients import hubcli


def world(api, goals=True):
    """A runner with bot `ops` on a task it owns; the bot's harness reports goals or not."""
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test",
         "capabilities": ["task_lanes_v2", "session_roots_v1", "task_goals_v1"], "readiness": {
         "schema_version": 1, "bots": {"ops": {"ready": True, "runtime": "codex", "goals": goals}}}}, machine["token"])
    task = post(api, "tasks", {"owner": "ops", "title": "Plan the launch", "body": "For the team."})
    return machine, task["id"]


def job_state(api, message_id):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state FROM jobs WHERE message_id=?", (message_id,)).fetchone()
    return row[0] if row else None


def goal_controls(api, cid):
    """The `/goal` controls in a conversation, each with the state of its job (None when it has none)."""
    with api.app.state.store.read() as c:
        return [tuple(r) for r in c.execute(
            "SELECT m.id, j.state FROM messages m LEFT JOIN jobs j ON j.message_id=m.id "
            "WHERE m.conversation_id=? AND json_extract(m.refs,'$.goal_action') IS NOT NULL ORDER BY m.created_at",
            (cid,))]


def deferrals(api, item):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM deferrals WHERE item=?", (item,))]


def inputs(api, machine, attempt):
    return [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]


def test_steer_joins_the_running_turn_queue_follows_it_and_defer_rides_along(api):
    machine, tid = world(api)
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "plan"}, machine["token"])

    # Steer wakes the bot now: its job is queued in the task's room, and the running turn takes it in.
    steered = post(api, f"tasks/{tid}/comments", {"text": "Use the August numbers.", "delivery": "steer"})
    assert steered["woke"] is True and job_state(api, steered["comment"]["id"]) == "queued"
    assert steered["sent_as"] == {"delivery": "steer", "until": None}
    assert steered["comment"]["id"] in inputs(api, machine, attempt)

    # Queue wakes the bot too, but after this turn: its job waits and the running turn does not take it in.
    queued = post(api, f"tasks/{tid}/comments", {"text": "Billing first, then the docs.", "delivery": "queue"})
    assert queued["woke"] is True and job_state(api, queued["comment"]["id"]) == "queued"
    assert queued["sent_as"] == {"delivery": "queue", "until": None}
    assert queued["comment"]["id"] not in inputs(api, machine, attempt)

    # A defer sent while the bot works this task rides along: it goes right after the turn, as a queue.
    rides = post(api, f"tasks/{tid}/comments", {"text": "And the FAQ.", "delivery": "defer"})
    assert rides["sent_as"]["delivery"] == "queue" and job_state(api, rides["comment"]["id"]) == "queued"
    assert rides["comment"]["id"] not in inputs(api, machine, attempt) and deferrals(api, rides["comment"]["id"]) == []

    # Omitted, a person's comment on a task they gave a bot steers, as it always has.
    plain = post(api, f"tasks/{tid}/comments", {"text": "Also the API page."})
    assert plain["sent_as"]["delivery"] == "steer" and plain["comment"]["id"] in inputs(api, machine, attempt)


def test_defer_wakes_nobody_and_keeps_a_deadline(api):
    machine, tid = world(api)
    before = H.now()
    held = post(api, f"tasks/{tid}/comments", {"text": "Billing first.", "delivery": "defer"})
    assert held["woke"] is False and job_state(api, held["comment"]["id"]) is None
    [row] = deferrals(api, held["comment"]["id"])
    assert row["kind"] == "comment" and row["task_id"] == tid and row["due_at"] >= H.shift(before, hours=24)
    assert held["sent_as"] == {"delivery": "defer", "until": row["due_at"]}

    timed = post(api, f"tasks/{tid}/comments", {"text": "Then the docs.", "delivery": "defer", "defer_for": "30m"})
    assert deferrals(api, timed["comment"]["id"])[0]["due_at"] < H.shift(before, hours=1)
    never = post(api, f"tasks/{tid}/comments", {"text": "Some day.", "delivery": "defer", "defer_for": "never"})
    assert deferrals(api, never["comment"]["id"])[0]["due_at"] is None and never["sent_as"]["until"] is None


def forwarded(turn, after):
    """The room messages the runner puts in a turn's prompt: those after the message its thread last answered, which
    is the earlier turn's trigger (runner-rs/src/prompt.rs reads `history` after the cursor execute.rs sets)."""
    history = turn["history"]
    ids = [m["id"] for m in history]
    newer = history[ids.index(after) + 1:] if after in ids else history
    return [m["body"] for m in newer]


def test_a_comment_queued_mid_turn_runs_right_after_it(api):
    machine, tid = world(api)
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "plan"}, machine["token"])
    queued = post(api, f"tasks/{tid}/comments", {"text": "Billing first, then the docs.", "delivery": "queue"})
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Plan drafted.", "last_seq": 0},
         machine["token"])
    second = claim(api, machine)
    assert second["message"]["id"] == queued["comment"]["id"]


def test_a_deferred_comment_reaches_the_bots_next_prompt_once(api):
    machine, tid = world(api)
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "plan"}, machine["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Plan drafted.", "last_seq": 0},
         machine["token"])
    held = post(api, f"tasks/{tid}/comments", {"text": "Billing first, then the docs.", "delivery": "defer"})
    assert held["woke"] is False

    later = post(api, f"tasks/{tid}/comments", {"text": "Also the API page."})
    second = claim(api, machine)
    assert second["message"]["id"] == later["comment"]["id"]
    assert "Billing first, then the docs." in forwarded(second, first["message"]["id"])
    head, *body = second["deferred"].split("\n")
    assert head == "Deferred since your last turn: 1 comment"
    assert any(held["comment"]["id"] in line and "Billing first, then the docs." in line for line in body)
    assert "Also the API page." not in second["deferred"], "the turn's own message is its Current message"
    assert deferrals(api, held["comment"]["id"])[0]["how"] == "carried"
    post(api, f"attempts/{second['id']}/started", {"thread_id": "plan"}, machine["token"])
    post(api, f"attempts/{second['id']}/complete", {"outcome": "completed", "text": "API page noted.", "last_seq": 0},
         machine["token"])

    # Delivered once: the next turn's prompt starts after the second turn's own trigger, so the comment is gone.
    again = post(api, f"tasks/{tid}/comments", {"text": "Then the launch note."})
    third = claim(api, machine)
    assert third["message"]["id"] == again["comment"]["id"]
    assert "Billing first, then the docs." not in forwarded(third, second["message"]["id"])
    assert third["deferred"] == ""


def test_the_send_says_which_ways_a_reader_may_deliver(api):
    machine, tid = world(api)
    cd = get(api, f"tasks/{tid}")["comment_delivery"]
    assert cd["default"] == "steer" and cd["default_for"] is None and cd["steer"] is True and cd["queue"] is True
    assert isinstance(cd["steers"], bool) and cd["goal"] is True

    # A person's task with no bot on it: nothing to deliver to.
    person = post(api, "tasks", {"owner": "human:ana", "title": "Call the venue", "body": "Ask about Friday."})
    assert get(api, f"tasks/{person['id']}")["comment_delivery"] == {
        "default": None, "default_for": None, "steer": False, "queue": False, "steers": False, "goal": False}


@pytest.mark.parametrize("way", ["steer", "queue"])
def test_only_someone_on_the_task_may_wake_its_bot(api, way):
    machine, tid = world(api)
    # Ben is not on Ana's task: his comment waits for the bot's next turn there, with no deadline.
    cd = get(api, f"tasks/{tid}", "ben-test")["comment_delivery"]
    assert cd["default"] == "defer" and cd["default_for"] == "never" and cd["steer"] is False and cd["queue"] is False
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Do mine first.", "delivery": way},
                 headers=headers("ben-test"))
    assert r.status_code == 403 and "delivery" in r.text, r.text
    assert all(c["body"] != "Do mine first." for c in get(api, f"tasks/{tid}")["comments"])
    held = post(api, f"tasks/{tid}/comments", {"text": "When you can."}, token="ben-test")
    assert held["woke"] is False and held["sent_as"] == {"delivery": "defer", "until": None}


def test_only_someone_on_the_task_may_set_its_goal(api):
    machine, tid = world(api)
    cid = get(api, f"tasks/{tid}")["task"]["conversation_id"]
    # Ben can read the task and write its bot, but he is not on it: the send offers no goal, and a goal comment
    # is refused.
    assert get(api, f"tasks/{tid}", "ben-test")["comment_delivery"]["goal"] is False
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Drop the billing work.", "delivery": "goal"},
                 headers=headers("ben-test"))
    assert r.status_code == 403 and "delivery" in r.text, r.text
    assert all(c["body"] != "Drop the billing work." for c in get(api, f"tasks/{tid}")["comments"])
    assert get(api, f"conversations/{cid}/goal")["goal"] is None


def test_the_goal_route_sets_no_task_sessions_goal_for_someone_off_the_task(api):
    machine, tid = world(api)
    cid = get(api, f"tasks/{tid}")["task"]["conversation_id"]
    r = api.post(f"/api/v2/conversations/{cid}/goal", json={"action": "set", "objective": "Drop the billing work."},
                 headers=headers("ben-test"))
    assert r.status_code == 403, r.text
    assert get(api, f"conversations/{cid}/goal")["goal"] is None
    # Ana, on the task, sets it.
    done = post(api, f"conversations/{cid}/goal", {"action": "set", "objective": "Finish the billing work."})
    assert done["goal"]["objective"] == "Finish the billing work."


def test_a_bot_whose_harness_cannot_take_goals_offers_no_goal(api):
    machine, tid = world(api, goals=False)
    cd = get(api, f"tasks/{tid}")["comment_delivery"]
    assert cd["default"] == "steer" and cd["goal"] is False


def test_goal_sets_the_task_sessions_goal_as_a_chat_goal_does(api):
    machine, tid = world(api)
    cid = get(api, f"tasks/{tid}")["task"]["conversation_id"]
    text = "Finish the billing migration before anything else."
    done = post(api, f"tasks/{tid}/comments", {"text": text, "delivery": "goal"})
    assert done["comment"]["body"] == text
    assert done["goal"]["objective"] == text and done["goal"]["status"] == "active"
    assert get(api, f"conversations/{cid}/goal")["goal"]["objective"] == text


def test_a_task_filed_into_a_chat_cannot_replace_the_chats_goal(api):
    """A task filed into a chat before task rooms keeps that chat as its conversation. Set as goal there would replace
    the chat's goal and cancel its queued control, so it is refused, nothing changes, and the send does not offer it."""
    world(api)
    room = post(api, "chat/ops", {"text": "Keep the billing notes current."})["conversation_id"]
    chat_goal = post(api, f"conversations/{room}/goal",
                     {"action": "set", "objective": "Keep the billing notes current."})["goal"]
    with api.app.state.store.transaction() as c:      # filed into the chat, with no task room of its own
        legacy = H.task_create(c, "human:ana", "Plan the billing notes", "For the team.", "bot:ops",
                               conversation_id=room, own_room=False)
    assert legacy["conversation_id"] == room
    controls = goal_controls(api, room)
    assert [state for _, state in controls] == ["queued"]

    assert get(api, f"tasks/{legacy['id']}")["comment_delivery"]["goal"] is False
    r = api.post(f"/api/v2/tasks/{legacy['id']}/comments", json={"text": "Drop the billing notes.", "delivery": "goal"},
                 headers=headers("ana-test"))
    assert r.status_code == 409 and "no room of its own" in r.text, r.text
    assert get(api, f"conversations/{room}/goal")["goal"] == chat_goal
    assert goal_controls(api, room) == controls
    assert all(c["body"] != "Drop the billing notes." for c in get(api, f"tasks/{legacy['id']}")["comments"])


def test_a_goal_the_harness_cannot_take_saves_no_comment(api):
    machine, tid = world(api, goals=False)
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Ship it.", "delivery": "goal"},
                 headers=headers("ana-test"))
    assert r.status_code == 409 and "goal_unsupported" in r.text, r.text
    assert all(m["body"] != "Ship it." for m in get(api, f"tasks/{tid}")["comments"])


@pytest.mark.parametrize("body", [
    {"text": "Pick the venue.", "delivery": "later"},                           # not a way
    {"text": "Pick the venue.", "delivery": "goal", "public": True},            # a goal is not public
    {"text": "Pick the venue.", "needs_action": True, "delivery": "queue"},     # needs_action is steer
    {"text": "Pick the venue.", "delivery": "queue", "defer_for": "4h"},        # a deadline is for defer
    {"text": "Pick the venue.", "delivery": "defer", "defer_for": "soon"},      # not a time
    {"text": "Pick the venue.", "delivery": "defer", "defer_for": "800d"},      # over two years
])
def test_a_delivery_that_cannot_apply_is_refused_and_nothing_is_saved(api, body):
    machine, tid = world(api)
    before = len(get(api, f"tasks/{tid}")["comments"])
    r = api.post(f"/api/v2/tasks/{tid}/comments", json=body, headers=headers("ana-test"))
    assert r.status_code == 422, r.text
    assert len(get(api, f"tasks/{tid}")["comments"]) == before


def test_mcp_and_the_hub_cli_send_the_same_delivery(api):
    machine, tid = world(api)
    is_error, payload = call(api, "hub_task_comment", {"id": tid, "text": "Pick the venue.", "delivery": "defer",
                                                       "defer_for": "4h"})
    assert is_error is False and payload["woke"] is False and payload["sent_as"]["delivery"] == "defer"
    # One of the ways at most, on the command line too.
    parse = hubcli.parser().parse_args
    assert parse(["task", "comment", tid, "Pick the venue.", "--goal"]).delivery == "goal"
    assert hubcli.delivery_of(parse(["task", "comment", tid, "Pick the venue."])) == {}
    assert hubcli.delivery_of(parse(["task", "comment", tid, "x", "--queue"])) == {"delivery": "queue"}
    assert hubcli.delivery_of(parse(["task", "comment", tid, "x", "--defer"])) == {"delivery": "defer"}
    assert hubcli.delivery_of(parse(["task", "comment", tid, "x", "--defer", "2d"])) == {
        "delivery": "defer", "defer_for": "2d"}
    with pytest.raises(SystemExit):
        parse(["task", "comment", tid, "x", "--defer", "soon"])
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["task", "comment", tid, "Pick the venue.", "--steer", "--queue"])
