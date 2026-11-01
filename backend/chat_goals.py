"""Thread-scoped native goals, delivered through the conversation's existing job queue."""
import json
from typing import Literal

from fastapi import Request
from pydantic import Field

from . import models as M, providers
from .store import H, Problem, bot_readiness, readiness_document, encode

TICO_COMMANDS = [
    {"name": "goal", "args": "<objective>", "help": "Pin a goal the bot works toward", "kind": "tico",
     "sub": ["pause", "resume", "clear", "edit"]},
    {"name": "new", "args": "", "help": "New chat", "kind": "tico"},
    {"name": "task", "args": "<title>", "help": "Make a task for this bot", "kind": "tico"},
    {"name": "branch", "args": "", "help": "Make my branch", "kind": "tico"},
    {"name": "help", "args": "", "help": "Show commands", "kind": "tico"},
]


class GoalAction(M.Contract):
    action: Literal["set", "edit", "pause", "resume", "clear"]
    objective: str | None = Field(default=None, min_length=1, max_length=4000)


def current(c, cid):
    row = c.execute("SELECT * FROM chat_goals WHERE conversation_id=?", (cid,)).fetchone()
    return dict(row) if row else None


def capabilities(c, auth, conv):
    bots = [H.actor_id(p) for p in conv["participants"] if p.startswith("bot:")]
    if conv.get("kind") == "task" and conv.get("task_id"):
        # A task's own room is its owning bot's, whoever else is in it (a requesting bot).
        owner = (H.task(c, conv["task_id"]) or {}).get("owner_id") or ""
        bots = [H.actor_id(owner)] if H.is_bot(owner) else []
    if len(bots) != 1 or bots[0] == auth.settings.assistant_bot:
        return None, False, []
    bot = bots[0]
    config = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    runtime, _ = providers.bot_choice(c, auth.settings, json.loads(config[0] or "{}") if config else {})
    row = c.execute("SELECT r.readiness FROM assignments a JOIN runners r ON r.id=a.runner_id "
                    "WHERE a.bot=? AND r.revoked_at IS NULL", (bot,)).fetchone()
    report = readiness_document(row[0]) if row else {}
    detail = bot_readiness(row[0], bot) if row else {}
    if "goals" not in detail:
        detail = report.get("runtimes", {}).get(runtime, {})
        if "goals" not in detail:
            detail = next((row for row in report.get("harnesses", {}).values()
                           if row.get("runtime") == runtime), {})
    # Goals are probed per installed version and reported on the heartbeat; only that report says yes.
    supported = detail.get("goals") is True
    commands = [*TICO_COMMANDS, *[{key: value for key, value in command.items() if value is not None}
                                for command in detail.get("commands", [])
                                if command.get("kind") == "harness" and command.get("name") not in
                                {item["name"] for item in TICO_COMMANDS}]]
    return bot, supported, commands


def readable_active(c, auth, who, bot, conversations=None):
    """`conversations`: the bot's active goals' conversation ids, when the caller has read them already."""
    if conversations is None:
        conversations = [row[0] for row in c.execute(
            "SELECT conversation_id FROM chat_goals WHERE bot=? AND status='active'", (bot,))]
    for conversation in conversations:
        try:
            auth.conversation(c, who, conversation)
            auth.require_read(c, who, bot)
            return True
        except Problem:
            pass
    return False


def report(c, attempt, payload):
    """Only the leased thread and the current goal revision may change the pinned state."""
    msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0])
    goal = current(c, msg["conversation_id"])
    if not goal or goal["bot"] != attempt["bot_id"] or goal["status"] != "active":
        return
    origin = msg.get("refs") or {}
    context = origin.get("goal_context") or {"id": origin.get("goal_id"), "updated_at": origin.get("goal_revision")}
    room = H.conversation(c, goal["conversation_id"]) or {}
    # A task room's goal rides every turn on the task (task_goal_row), not only messages sent under it:
    # the attempt was handed this revision, which the payload must still name.
    if (context.get("id") != goal["id"] or context.get("updated_at") != goal["updated_at"]) and not (
            room.get("kind") == "task" and room.get("task_id")):
        return
    if payload.get("goal_id") != goal["id"] or payload.get("revision") != goal["updated_at"]:
        return
    if payload.get("objective") and str(payload["objective"]).strip() != goal["objective"].strip():
        return
    status = payload.get("status")
    if status not in ("active", "paused", "met", "stopped", "cleared"):
        raise Problem("validation", "Unknown goal status", 422)
    if status == "active":
        return
    note = str(payload.get("note") or "")[:4000]
    if status == "cleared":
        # Explicit clears already changed the row; an active native goal clearing itself stops here.
        status, note = "stopped", note or "The harness cleared the goal"
    if status == "paused" and "waiting on:" in note.lower() and room.get("kind") == "task" and room.get("task_id"):
        # The runner pauses a goal whose bot signed off "Waiting on: ...". A task already marked done is met:
        # what it still waits on is not this task's work.
        row = H.task(c, room["task_id"])
        if row and row["status"] in ("done", "closed"):
            status = "met"
    now = H.now()
    c.execute("UPDATE chat_goals SET status=?,note=?,updated_at=?,ended_at=? WHERE id=?",
              (status, note, now, now if status in ("met", "stopped", "cleared") else None, goal["id"]))
    if status in ("met", "stopped"):
        # A notice to the bot itself stays in the chat without waking it or notifying people.
        H._write_message(c, "bot:" + goal["bot"], "bot:" + goal["bot"],
              ("Goal met: " if status == "met" else "Goal stopped: ") + goal["objective"] +
              ("\n" + note if note else ""), H.conversation(c, goal["conversation_id"]), "notice",
              {"chat_goal": goal["id"], "goal_status": status}, None, None)
    H.event(c, "bot:" + goal["bot"], "chat.goal", goal["conversation_id"], {"status": status})
    if room.get("kind") == "task" and room.get("task_id"):
        task_goal_ended(c, goal, room["task_id"], status, note)


# Room left in a task goal's objective for the runner's waiting clause (runner-rs/src/goals.rs native_objective).
TASK_GOAL_CHARS = 3500


def task_goal_objective(task, objective, steps=()):
    """What a task's native goal holds the bot to: the task's own objective, then marking it done, or on a
    steps-only type (`steps`, its step names) moving it to the step its work has reached."""
    tail = (f"\nThen move task {task['id']} to the step its work has reached ({', '.join(steps)}) with a summary "
            f"(hub task update {task['id']} --step \"<step>\" --note ...)." if steps else
            f"\nThen mark task {task['id']} done with a summary (hub task update {task['id']} --status done --note ...).")
    return objective[:TASK_GOAL_CHARS - len(tail)].rstrip() + tail


def task_goal_row(c, auth, conv, bot, task, objective):
    """The active native goal a bot's task turn runs under, in the task's own room, or None (the runner then
    works the goal from its prompt line). Made here when there is none, or when the keeper's own goal was cleared
    (the task's goal was off) or set for an older objective. A goal a person set, edited or cleared in the room is
    theirs and stands. A paused or stopped goal stays so: the task does not spin it up again; a person resumes it."""
    if conv.get("closed_at"):
        return None
    owner, supported, _ = capabilities(c, auth, conv)
    if owner != bot or not supported:
        return None
    objective = task_goal_objective(task, objective, H.steps_only(c, task))
    goal = current(c, conv["id"])
    if goal and (goal["set_by"] != H.KEEPER or goal["objective"] == objective and goal["status"] != "cleared"):
        return goal if goal["status"] == "active" else None
    now = H.now()
    c.execute("DELETE FROM chat_goals WHERE conversation_id=?", (conv["id"],))
    c.execute("INSERT INTO chat_goals VALUES(?,?,?,?,?,?,?,?,?,?)",
              (H.new_id(), conv["id"], bot, objective, "active", "", H.KEEPER, now, now, None))
    H.event(c, H.KEEPER, "chat.goal", conv["id"], {"status": "active", "task": task["id"]})
    return current(c, conv["id"])


def clear_task_goal(c, conv):
    """End the active goal Tico set for a task whose goal no longer applies, as a person's clear does: the row
    is cleared and a "/goal clear" control queued in the room, so the runner clears the native goal too.
    A goal a person set in the room is theirs and stays."""
    goal = current(c, conv["id"])
    if goal and goal["set_by"] != H.KEEPER and goal["status"] == "cleared":
        # A person's clear stands while the task's goal is on; once it is off, turning it on again starts a new one.
        c.execute("UPDATE chat_goals SET set_by=? WHERE id=?", (H.KEEPER, goal["id"]))
        return None
    if not goal or goal["set_by"] != H.KEEPER or goal["status"] not in ("active", "paused"):
        return None
    now = H.now()
    c.execute("UPDATE chat_goals SET status='cleared',note=?,updated_at=?,ended_at=? WHERE id=?",
              ("The task's goal was turned off", now, now, goal["id"]))
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id IN "
              "(SELECT id FROM messages WHERE conversation_id=? AND json_extract(refs,'$.goal_action') IS NOT NULL)",
              (conv["id"],))
    H._write_message(c, H.KEEPER, "bot:" + goal["bot"], "/goal clear", conv, "say",
                     {"task": conv["task_id"], "command": True, "goal_action": "clear", "goal_id": goal["id"],
                      "goal_revision": now, "goal_objective": goal["objective"]}, None, None)
    H.event(c, H.KEEPER, "chat.goal", conv["id"], {"status": "cleared", "task": conv["task_id"]})
    return current(c, conv["id"])


def task_goal_ended(c, goal, task_id, status, note):
    """A task goal the harness ended. Met: the task is marked done with the judge's reason, when the bot has not
    marked it already (the objective told it to). Paused or stopped: the task stays open and the main chat hears
    it is blocked (waiting on someone) or stuck."""
    row = H.task(c, task_id)
    if not row or row["owner_id"] != "bot:" + goal["bot"] or row["status"] not in H.ACTIVE_STATUSES:
        return
    if status == "met":
        # Only the goal Tico set for the task, for its objective as it stands: a goal a person set in the room
        # ("summarize where it is") being met says nothing about the task (its "Goal met" line is in the room).
        from .execution import current_task_goal
        wanted = current_task_goal(c, goal["bot"], row)
        # A steps-only type's task is finished by the bot moving it to a step, not by its goal being met.
        steps = H.steps_only(c, row)
        if (row["status"] in ("open", "doing") and goal["set_by"] == H.KEEPER and wanted and not steps
                and goal["objective"] == task_goal_objective(row, wanted["objective"], steps)):
            H.task_update(c, row["owner_id"], task_id, status="done", note=(note or "Goal met.")[:4000])
        return
    if status in ("paused", "stopped"):
        # A harness pauses a goal that needs a person (Codex "blocked"); the runner's tries guard pauses a stuck one.
        waiting = "waiting on:" in note.lower() or (status == "paused" and not note.startswith("Goal paused after"))
        H.task_report(c, row, "blocked" if waiting else "stuck", note)


def install(app, store, auth, mutate, send):
    @app.get("/api/v2/conversations/{cid}/goal")
    def read(request: Request, cid: str):
        with store.read() as c:
            conv = auth.conversation(c, request.state.identity, cid)
            bot, supported, commands = capabilities(c, auth, conv)
            if bot:
                auth.require_read(c, request.state.identity, bot)
            return {"goal": current(c, cid), "supported": supported, "commands": commands}

    @app.post("/api/v2/conversations/{cid}/goal")
    def write(request: Request, cid: str, body: GoalAction):
        def work(c):
            goal = set_goal(c, auth, request.state.identity, cid, body.action, body.objective, send)
            return {"goal": goal}
        return mutate(request, body, work)


def may_direct(c, who, task):
    """Whether `who` may direct a task's bot by choice, with a steer or a goal: someone on the task, or a person who
    may move any task (the owner, a mover). Anyone else's comment waits for the bot's next turn, as it does without a
    delivery, so a reader never spends a bot's turns on a task that isn't theirs."""
    mover = who.role == "owner" or who.role == "human" and H.can_move(c, who.actor)
    return mover or H.task_participant(c, who.actor, task)


def own_room(c, task):
    """Whether a task's conversation is the task's own room. A task filed into a chat before task rooms keeps that
    chat as its conversation, and a goal set from its comments would replace the chat's goal, so only the own room
    takes one. The same test as the task's goal turns (backend/execution.py)."""
    room = H.conversation(c, task["conversation_id"]) or {}
    return room.get("kind") == "task" and room.get("task_id") == task["id"]


def access(c, auth, who, cid):
    """Who may change a conversation's goal: the one rule for the goal route and for a task comment that sets the
    task session's goal (delivery goal). The caller may write the conversation and its bot, the bot's harness keeps
    goals, and the room is open. A task's own room is directed by someone on the task (may_direct), as its bot is
    steered. Returns (conversation, bot); refuses with the Problem a goal change gets."""
    conv = auth.conversation(c, who, cid, write=True)
    bot, supported, _ = capabilities(c, auth, conv)
    if bot:
        auth.require_write(c, who, bot)
        auth.require_bot_contact(c, who, "bot:" + bot, cid, conv.get("task_id"), kind="message")
    if not supported:
        raise Problem("goal_unsupported", "This bot's harness doesn't support goals", 409)
    if conv.get("closed_at"):
        raise Problem("closed", "Start a new chat first", 409)
    task = H.task(c, conv["task_id"]) if conv.get("kind") == "task" and conv.get("task_id") else None
    if task and who.role != "bot" and not may_direct(c, who, task):
        raise Problem("forbidden", "Only someone on this task can set its goal", 403)
    return conv, bot


def may_set(c, auth, who, cid):
    """Whether `who` may set a goal in this conversation: `access` without its refusal, for what a composer offers."""
    try:
        access(c, auth, who, cid)
    except Problem:
        return False
    return True


def set_goal(c, auth, who, cid, action, objective, send):
    """A goal change in a conversation, as the goal route makes it: the goal row and the `/goal` control the runner
    runs. `objective` is the text for set and edit. A task comment that sets the task session's goal comes here too."""
    _, bot = access(c, auth, who, cid)
    goal = current(c, cid)
    if action in ("set", "edit") and (objective is None or not objective.strip() or len(objective) > 4000):
        raise Problem("validation", "Write a goal of 1–4,000 characters", 422)
    if action != "set" and not goal:
        raise Problem("goal_missing", "Set a goal first", 409)
    if action == "resume" and goal["status"] != "paused":
        raise Problem("goal_state", "Only a paused goal can resume", 409)
    if action == "pause" and goal["status"] != "active":
        raise Problem("goal_state", "Only an active goal can pause", 409)
    now = H.now()
    status = {"pause": "paused", "clear": "cleared"}.get(action, "active")
    if action == "set":
        c.execute("DELETE FROM chat_goals WHERE conversation_id=?", (cid,))
        c.execute("INSERT INTO chat_goals VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (H.new_id(), cid, bot, objective, status, "", who.actor, now, now, None))
    else:
        # Whoever changes a goal owns it from then on: a person's clear or edit of the goal Tico set for a
        # task is theirs, so the task's goal does not bring it back (task_goal_row) or end it (clear_task_goal).
        c.execute("UPDATE chat_goals SET objective=?,status=?,note=?,updated_at=?,ended_at=?,set_by=? WHERE conversation_id=?",
                  (objective if action == "edit" else goal["objective"], status, "", now,
                   now if status == "cleared" else None, who.actor, cid))
    goal = current(c, cid)
    # Superseded controls must never reset a newer objective when the computer catches up.
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id IN "
              "(SELECT id FROM messages WHERE conversation_id=? AND json_extract(refs,'$.goal_action') IS NOT NULL)", (cid,))
    text = "/goal clear" if action in ("pause", "clear") else "/goal " + goal["objective"]
    message = send(c, who, M.MessageCreate(to="bot:" + bot, text=text, conversation_id=cid, command=True))
    refs = {**(message.get("refs") or {}), "goal_action": action, "goal_id": goal["id"],
            "goal_revision": now, "goal_objective": goal["objective"]}
    c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(refs), message["id"]))
    # The objective is stored after send()'s own recheck: charge the final references.
    H.held_queue_recheck(c, message["id"])
    H.event(c, who.actor, "chat.goal", cid, {"status": status})
    return goal
