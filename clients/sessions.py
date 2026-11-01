"""Which provider thread a turn continues, and which lane it runs in.

Pure functions shared by the server (backend/execution.py, which hands out work per lane) and the
runner (runner/state.py, which keys its sessions), so the two never disagree about a turn.
"""

# The thread a turn with no task continues. Every task is its own session by default; `session: bot`
# keeps everything on this one thread, and `session: task` (BotOps's default) also keys chat with no
# task on its conversation.
BOT_THREAD = "bot"

# Every turn that is not keyed on a task: the bot thread and conversation-keyed chat, which share
# the bot's main checkout and so run one at a time.
MAIN_LANE = "main"

# A private live meeting's bot turns: `private:<conversation id>`, its own lane, which the runner runs in a checkout of
# the bot's repository kept for that meeting alone, never pushed and taken away when the meeting ends
# (runner-rs/src/sessions.rs). The server picks it from the meeting's room (backend/execution.py job_lane).
PRIVATE_LANE = "private:"
# That checkout's folder and branch name: `private-<conversation id>` (backend/worktrees.py session_tasks).
PRIVATE_FOLDER = "private-"


def private_lane(conversation_id):
    return PRIVATE_LANE + conversation_id


def stateless(config):
    """Whether the bot keeps nothing from one task to the next. The server sets `stateless` on the config it hands
    out, from the template too (backend/hubdb.py with_stateless), and the runner reads the same key."""
    return (config or {}).get("stateless") is True


def session_key(config, bot, task_id, conversation_id):
    """The provider thread this turn continues. A turn on a task continues that task's own session,
    so a task never carries every earlier task into each call; a wake about the same task (a stalled
    nudge, a reconcile, a reply) resumes it. Other turns share the bot's one thread. `session: bot`
    opts out (one thread for everything), except for a stateless bot: its tasks still keep their own
    sessions. `session: task`, BotOps's default, also keys a wake with no task on its conversation, so
    a person's follow-up still lands where the first answer was."""
    mode = (config or {}).get("session") or ("task" if bot == "botops" else None)
    if mode == "bot" and not stateless(config):
        return BOT_THREAD
    if task_id:
        return "task:" + task_id
    if mode == "task" and conversation_id:
        return "conversation:" + conversation_id
    return BOT_THREAD


def lane(config, bot, task_id, conversation_id):
    """A task-keyed turn has its own session, so it runs in its own lane beside the bot's other
    tasks; everything else shares the main lane."""
    key = session_key(config, bot, task_id, conversation_id)
    return key if key.startswith("task:") else MAIN_LANE
