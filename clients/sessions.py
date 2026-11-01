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


# A turn in a public thread on `session: bot` (native threads, docs/dev/threads-contract.md §8): the bot's one public
# thread. It clears with the bot's shared thread (bot_session_epoch). The old `bot` thread mixed private and public
# context, so it is never carried into this one.
PUBLIC_THREAD = "public"
PRIVATE, PUBLIC = "private", "public"


def root_key(root_id, audience_class):
    """`root:<root id>:<public|private>`: a thread tree's session. A private thread's session never serves a public
    turn, and the other way round."""
    return f"root:{root_id}:{PRIVATE if audience_class == PRIVATE else PUBLIC}"


def session_key(config, bot, task_id, conversation_id, root_id=None, audience_class=None, task_private=False):
    """The provider thread this turn continues. A turn on a task continues that task's own session,
    so a task never carries every earlier task into each call; a wake about the same task (a stalled
    nudge, a reconcile, a reply) resumes it. `session: bot` opts out (one thread for everything), except
    for a stateless bot: its tasks still keep their own sessions.

    A turn from a server with native threads names its thread's root and audience class: a turn with no task
    continues that thread tree's session (`root_key`), and `session: bot` keeps one public thread (`public`) while
    each private thread keeps its own. From an older server (no root): other turns share the bot's one thread, and
    `session: task`, BotOps's default, keys a wake with no task on its conversation.

    On `session: bot` a private task (`task_private`), or a task turn in a private thread, keeps its own session
    (`task:<id>`): the shared thread serves Home and public turns, so private work never enters it."""
    mode = (config or {}).get("session") or ("task" if bot == "botops" else None)
    on_bot = mode == "bot" and not stateless(config)
    if on_bot and task_id and (task_private or (root_id and audience_class == PRIVATE)):
        return "task:" + task_id
    if root_id:
        if on_bot:
            if task_id:
                return BOT_THREAD
            return root_key(root_id, PRIVATE) if audience_class == PRIVATE else PUBLIC_THREAD
        if task_id:
            return "task:" + task_id
        return root_key(root_id, audience_class)
    if on_bot:
        return BOT_THREAD
    if task_id:
        return "task:" + task_id
    if mode == "task" and conversation_id:
        return "conversation:" + conversation_id
    return BOT_THREAD


def adopted_key(key):
    """The legacy key whose saved session a new root key takes over on first use: `conversation:<root id>` for
    `root:<root id>:<class>`, else None. `bot` is never adopted into `public`."""
    if key.startswith("root:"):
        parts = key.split(":")
        if len(parts) == 3:
            return "conversation:" + parts[1]
    return None


def lane(config, bot, task_id, conversation_id, root_id=None, audience_class=None):
    """A task-keyed turn has its own session, so it runs in its own lane beside the bot's other
    tasks; everything else shares the main lane. A private task on `session: bot` keeps its own session but stays in
    the main lane, in the bot's one checkout (the server passes no `task_private` here)."""
    key = session_key(config, bot, task_id, conversation_id, root_id, audience_class)
    return key if key.startswith("task:") else MAIN_LANE
