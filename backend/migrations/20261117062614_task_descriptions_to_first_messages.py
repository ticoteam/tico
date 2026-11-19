"""A task is a title and a chat: each task's description becomes its first chat message.

Tasks had a description (tasks.body) shown above their chat. Now what a task asks is the first message on its chat, from
whoever wrote it (spec v3.4, change 8). For each task whose body says more than its title:

1. A person's or a bot's request: a message from whoever wrote it (the `task.create` event's actor, else the task's
   requester), placed first on the task's chat. It is dated when the task was made and takes a `seq` just before the
   first message the chat had after that, so every timeline shows it first. It is quiet (Defer never): nobody is
   notified and no thread turns unread; the thread's counters are left as they were.
2. Bot instructions, copied into the body by a routine or a setup, are not a person's words, so they are not posted:
   - a routine's occurrence (schedule_occurrences) or an automation's turn (a keeper wake naming `automation_run`);
   - a set-up task Tico filed with its steps ("Set yourself up", "Set up <bot> from the <template> template", "Set up
     Docs", "Set up the market map", "Size the market", a bot's tool access change, or a body that starts with a
     setup's own words).
   The routine or the template keeps those words. A finished one's copy is dropped. An open one still to be worked has
   them moved to Tico's note to its owner bot (keeper to bot: no seq, in no timeline), where its runs read them
   (task_wakes.instructions), so work in flight keeps its instructions.
3. What Tico itself (the keeper) filed for a bot is the same: Tico speaks to a bot through its runs, so its words become
   that note, open or finished (a later check reads a review task's "Bot id:" line there). What Tico filed for a
   person is posted as Tico's message, as in 1.

Then the body is emptied, without counting as a change to the work (tasks_body_work_revision is set aside for it).
Every read is by an index: the tasks with a body once, then each task's messages by messages_refs_task, its routine by
schedule_occurrences_task, events by events_action_target and a chat's messages by messages_conversation. Running this
again changes nothing: a moved body is empty.
"""

import json
import uuid

# task_wakes.INSTRUCTIONS_HEAD as this migration writes it: the line its runs find the instructions under.
HEAD = "Your instructions for this task (from its routine or setup, not from a person):"

SETUP = (
    "title = 'Set yourself up' OR title LIKE '%Set up % from the % template' "
    "OR title = 'Set up Docs' OR title LIKE 'Set up Docs (%)' "
    "OR title = 'Set up the market map' OR title LIKE 'Set up the market map (%)' "
    "OR title = 'Size the market' OR title LIKE 'Size the market (%)' "
    "OR (owner_id LIKE 'bot:%' AND (title LIKE 'Add % access to %' OR title LIKE 'Remove % access from %' "
    "OR title LIKE 'Change % access on %')) "
    "OR ltrim(body) LIKE 'Run your setup%' OR ltrim(body) LIKE 'Create bot-% from the % template%' "
    "OR ltrim(body) LIKE 'Build our market map from the owner''s answers%' "
    "OR ltrim(body) LIKE 'Define and size our market:%'"
)
STAMP_WORK = "tasks_body_work_revision"
COLUMNS = ("id", "title", "body", "requester_id", "owner_id", "status", "conversation_id", "created_at")


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _routine(conn, task_id, occurrences):
    if occurrences and conn.execute("SELECT 1 FROM schedule_occurrences WHERE task_id = ?", (task_id,)).fetchone():
        return True
    return (
        conn.execute(
            "SELECT 1 FROM messages WHERE json_extract(refs, '$.task') = ? AND sender_id = 'keeper' "
            "AND json_extract(refs, '$.automation_run') IS NOT NULL LIMIT 1",
            (task_id,),
        ).fetchone()
        is not None
    )


def _setup(conn, task_id):
    return conn.execute(f"SELECT 1 FROM tasks WHERE id = ? AND ({SETUP})", (task_id,)).fetchone() is not None


def _creator(conn, task, events):
    if events:
        found = conn.execute(
            "SELECT actor FROM events WHERE action = 'task.create' AND target = ? ORDER BY created_at LIMIT 1",
            (task["id"],),
        ).fetchone()
        if found and found[0]:
            return found[0]
    return task["requester_id"] or "keeper"


def _note(conn, task, text):
    """Tico's note to the owner bot with the task's instructions: no seq (keeper to bot), read, so nothing waits."""
    at = task["created_at"]
    conn.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at, "
        "delivered_at, read_at) VALUES (?, ?, 'keeper', ?, 'notice', ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            task["conversation_id"],
            task["owner_id"],
            f"{task['title'] or 'This task'} is yours.\n\n{HEAD}\n{text}",
            json.dumps({"task": task["id"], "instructions": True, "quiet": True}),
            at,
            at,
            at,
        ),
    )


def _first(conn, task, sender, text):
    """The request as the chat's first message: dated with the task, ordered before what came after it."""
    thread = task["conversation_id"]
    counters = conn.execute("SELECT last_seq, last_unread_seq FROM conversations WHERE id = ?", (thread,)).fetchone()
    message_id = str(uuid.uuid4())
    refs = {"task": task["id"], "comment": True, "request": True, "send_way": "defer", "quiet": True}
    conn.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?, ?, ?, NULL, 'say', ?, ?, ?)",
        (message_id, thread, sender, text, json.dumps(refs), task["created_at"]),
    )
    after = conn.execute(
        "SELECT min(seq) FROM messages WHERE conversation_id = ? AND created_at >= ? AND seq IS NOT NULL AND id <> ?",
        (thread, task["created_at"], message_id),
    ).fetchone()[0]
    if after is not None:
        conn.execute("UPDATE messages SET seq = ? WHERE id = ?", (int(after) - 1, message_id))
        if counters:
            conn.execute(
                "UPDATE conversations SET last_seq = ?, last_unread_seq = ? WHERE id = ?",
                (counters[0], counters[1], thread),
            )
    elif counters:
        # Its chat had nothing since: it is the newest message, read by everyone already (unread stays as it was).
        conn.execute("UPDATE conversations SET last_unread_seq = ? WHERE id = ?", (counters[1], thread))


def up(conn):
    if not all(_table(conn, name) for name in ("tasks", "messages", "conversations")):
        return
    occurrences = _table(conn, "schedule_occurrences")
    events = _table(conn, "events")
    # The ids first, then one task at a time: the bodies are never all in memory at once.
    ids = [found[0] for found in conn.execute("SELECT id FROM tasks WHERE body IS NOT NULL AND body <> ''")]
    cleared = []
    for task_id in ids:
        row = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM tasks WHERE id = ?", (task_id,)).fetchone()
        task = dict(zip(COLUMNS, row, strict=True))
        text = str(task["body"]).strip()
        if not text or text == str(task["title"] or "").strip():
            cleared.append(task["id"])
            continue
        if not task["conversation_id"]:
            continue  # no chat to hold it: the body stays where it is
        bot_owner = str(task["owner_id"] or "").startswith("bot:")
        if task["requester_id"] == "keeper" and bot_owner:
            _note(conn, task, text)
        elif _routine(conn, task["id"], occurrences) or _setup(conn, task["id"]):
            if bot_owner and task["status"] == "open":
                _note(conn, task, text)
        else:
            already = conn.execute(
                "SELECT 1 FROM messages WHERE json_extract(refs, '$.task') = ? AND json_extract(refs, '$.request') = 1 "
                "LIMIT 1",
                (task["id"],),
            ).fetchone()
            if not already:
                _first(conn, task, _creator(conn, task, events), text)
        cleared.append(task["id"])
    if not cleared:
        return
    kept = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (STAMP_WORK,)).fetchone()
    if kept:
        conn.execute(f"DROP TRIGGER {STAMP_WORK}")
    for task_id in cleared:
        conn.execute("UPDATE tasks SET body = '' WHERE id = ?", (task_id,))
    if kept:
        conn.execute(kept[0])
