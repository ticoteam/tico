"""A task comment's delivery (docs/tasks.md, Conversation): steer reaches the bot now, queue waits for its next turn
on the task, goal sets the task session's goal. The API, the hub CLI and the MCP tool share one rule, tested here."""

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
            "WHERE m.conversation_id=? AND json_extract(m.refs_json,'$.goal_action') IS NOT NULL ORDER BY m.created",
            (cid,))]


def test_queue_waits_for_the_next_turn_steer_joins_the_running_one_and_no_choice_is_as_before(api):
    machine, tid = world(api)
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "plan"}, machine["token"])

    # Queue is saved on the task without waking the bot, so it does not join the running turn either.
    queued = post(api, f"tasks/{tid}/comments", {"text": "Billing first, then the docs.", "delivery": "queue"})
    assert queued["woke"] is False and job_state(api, queued["comment"]["id"]) is None
    running = [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]
    assert queued["comment"]["id"] not in running

    # Steer wakes the bot now: its job is queued in the task's room, and the running turn takes it in.
    steered = post(api, f"tasks/{tid}/comments", {"text": "Use the August numbers.", "delivery": "steer"})
    assert steered["woke"] is True and job_state(api, steered["comment"]["id"]) == "queued"
    running = [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]
    assert steered["comment"]["id"] in running

    # Omitted, the wake rule decides as it always has: a person's comment on a task its bot owns wakes the bot.
    plain = post(api, f"tasks/{tid}/comments", {"text": "Also the API page."})
    assert plain["woke"] is True and job_state(api, plain["comment"]["id"]) == "queued"


def forwarded(turn, after):
    """The room messages the runner puts in a turn's prompt: those after the message its thread last answered, which
    is the earlier turn's trigger (runner-rs/src/prompt.rs reads `history` after the cursor execute.rs sets)."""
    history = turn["history"]
    ids = [m["id"] for m in history]
    newer = history[ids.index(after) + 1:] if after in ids else history
    return [m["body"] for m in newer]


def test_a_comment_queued_mid_turn_reaches_the_bots_next_prompt_once(api):
    machine, tid = world(api)
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "plan"}, machine["token"])
    queued = post(api, f"tasks/{tid}/comments", {"text": "Billing first, then the docs.", "delivery": "queue"})
    assert queued["woke"] is False
    # The turn finishes with its reply after the queued comment.
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Plan drafted.", "last_seq": 0},
         machine["token"])

    later = post(api, f"tasks/{tid}/comments", {"text": "Also the API page."})
    second = claim(api, machine)
    assert second["message"]["id"] == later["comment"]["id"]
    assert first["message"]["id"] in [m["id"] for m in second["history"]]
    assert "Billing first, then the docs." in forwarded(second, first["message"]["id"])
    post(api, f"attempts/{second['id']}/started", {"thread_id": "plan"}, machine["token"])
    post(api, f"attempts/{second['id']}/complete", {"outcome": "completed", "text": "API page noted.", "last_seq": 0},
         machine["token"])

    # Delivered once: the next turn's prompt starts after the second turn's own trigger, so the queued comment is gone.
    again = post(api, f"tasks/{tid}/comments", {"text": "Then the launch note."})
    third = claim(api, machine)
    assert third["message"]["id"] == again["comment"]["id"]
    assert "Billing first, then the docs." not in forwarded(third, second["message"]["id"])


def test_the_send_says_which_ways_a_reader_may_deliver(api):
    machine, tid = world(api)
    cd = get(api, f"tasks/{tid}")["comment_delivery"]
    assert cd["default"] == "steer" and isinstance(cd["steers"], bool) and cd["goal"] is True

    # A person's task with no bot on it: nothing to deliver to.
    person = post(api, "tasks", {"owner": "human:ana", "title": "Call the venue", "body": "Ask about Friday."})
    assert get(api, f"tasks/{person['id']}")["comment_delivery"] == {
        "default": None, "steer": False, "steers": False, "goal": False}


def test_only_someone_on_the_task_may_steer_its_bot(api):
    machine, tid = world(api)
    # Ben is not on Ana's task: his comment waits for the bot's next turn, so the send offers no steer.
    cd = get(api, f"tasks/{tid}", "ben-test")["comment_delivery"]
    assert cd["default"] == "queue" and cd["steer"] is False
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Do mine first.", "delivery": "steer"},
                 headers=headers("ben-test"))
    assert r.status_code == 403 and "delivery" in r.text, r.text
    assert all(c["body"] != "Do mine first." for c in get(api, f"tasks/{tid}")["comments"])
    queued = post(api, f"tasks/{tid}/comments", {"text": "When you can.", "delivery": "queue"}, token="ben-test")
    assert queued["woke"] is False


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
])
def test_a_delivery_that_cannot_apply_is_refused_and_nothing_is_saved(api, body):
    machine, tid = world(api)
    before = len(get(api, f"tasks/{tid}")["comments"])
    r = api.post(f"/api/v2/tasks/{tid}/comments", json=body, headers=headers("ana-test"))
    assert r.status_code == 422, r.text
    assert len(get(api, f"tasks/{tid}")["comments"]) == before


def test_mcp_and_the_hub_cli_send_the_same_delivery(api):
    machine, tid = world(api)
    is_error, payload = call(api, "hub_task_comment", {"id": tid, "text": "Pick the venue.", "delivery": "queue"})
    assert is_error is False and payload["woke"] is False
    # One of the three ways at most, on the command line too.
    assert hubcli.parser().parse_args(["task", "comment", tid, "Pick the venue.", "--goal"]).delivery == "goal"
    assert hubcli.parser().parse_args(["task", "comment", tid, "Pick the venue."]).delivery is None
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["task", "comment", tid, "Pick the venue.", "--steer", "--queue"])
