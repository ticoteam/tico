"""Which wakes may wait for a bot's next run (token saving 2/9, Dev 140).

A message to a bot starts a run (store.QUEUE_TRIGGER queues a job) unless it is quiet. `gate` is asked once for
every message about to be written to a bot that would start one (H._write_message), and decides with two rules:

- `own_write`: the bot's own write caused it. Its own comments, step moves, status changes, relations and
  checkpoints, on any of its tasks, never wake that same bot: its run already knows.
- `parked`: it is about a task the bot owns that waits on something (an unfinished `blocked_by` task, or a pull
  request link armed with `wake_once`), and it is not that thing arriving. A comment, a relation change or a step
  move does not wake a parked task; its blocker finishing does.

A skipped wake is never dropped. The message is written to its task's room as before, quietly, and a quiet note
linked to it (the one bot_to_bot.hold leaves, carried and marked read by execution.claim) takes it to the bot's
next run whatever starts it, in the order it came. Every skip leaves one `wake.skipped` event (bot, task, the
message, its trigger and the rule) for the weekly audit's sample (docs/hub-sql.md).

`ALWAYS_WAKE` is the list nothing filters, neither these rules nor any later one (token saving 3/9, 4/9, 9/9 and
the bot-to-bot rule in backend/bot_to_bot.py ask `always_wakes` first). `AWAITED` is what a waiting task waits for:
it wakes the bot even when the bot's own write caused it, because the session that waits is not the one that wrote
(knowledge: a bot finishing its own last subtask must still resume the parent, PR #657).
Fail open: anything `gate` cannot read wakes.
"""

import contextvars
import re

# Who made the request whose writes are being made (backend/app.py request_guard): the person, or the bot, or the
# person a bot or the Assistant acts for. Empty for the keeper's own sweeps and the runner's calls, which no rule
# reads as anyone's own write.
CAUSE = contextvars.ContextVar("wake_cause", default="")

# Never filtered. Each entry: (key, what it is). `always_wakes` returns the key that matched.
ALWAYS_WAKE = (
    ("person", "a person's message or comment, or a notice of a person's own act (an approval, a step move)"),
    ("ask", "an ask, answer or steer: a question to the bot, or the answer it waits on"),
    ("approval", "an approval decided, withdrawn or asked about, or a structured question"),
    ("needs_action", "a message its sender flagged needs-action (or a flag that is not plainly true or false)"),
    ("reply", "a reply to the bot's own ask, to a message it flagged needs-action, or to one it waits on (wait_s)"),
    ("gate", "a factory gate verdict on the bot's ticket: Review 1, Review 2, QA or Security"),
)
ALWAYS_KINDS = frozenset({"ask", "answer", "steer"})
# References that make a message more than news: a decision, a question, an instruction.
ALWAYS_REFS = frozenset({"approval", "questions", "command", "run_now"})
FLAG = "needs_action"
VERDICT = re.compile(r"^\s*VERDICT:\s*(approve|changes)\s+PR\s+#\d+\s+head\s+[0-9a-f]{7,40}\b", re.I | re.M)

# What a parked task waits for, by the refs its wake carries. These wake even when the bot's own write caused them.
AWAITED = (
    ("blocker_done", "a task this one waits on (blocked_by) finished"),
    ("wake_once", "a pull request link armed with wake_once got its result (github.flush_wakes, trigger pr_checks)"),
    ("subtask", "a subtask moved or finished"),
    ("worktree", "the worktree it waited for is ready"),
    ("watcher", "something the bot watches moved or ended"),
    ("resume", "a quiet task's stored resume condition came true (backend/quiet_work.py)"),
)
# Only news may wait: a comment, or a task's status, step or relation moving (run_triggers.TRIGGERS). A stall or
# status check, a schedule, a pull request's checks and anything else keep their own rules (wake presets).
HOLDABLE_TRIGGERS = ("comment", "status_or_relation")
# wake_presets.classify classes that are news. An assignment, a blocker, a review request, an awaited result, a
# question or a person's message is not, whatever its trigger says.
HOLDABLE_CLASSES = ("notice", "message")
# The references news carries: a plain message's, the trigger stamp, a finished task's summary. Anything else (a
# wake kind, a batch, an answer) is more than news, and so is a reference no rule here knows yet.
NEWS_REFS = frozenset({"task", "comment", "attachments", "files", "unsolicited", "turn_id", "warnings", "via",
                       FLAG, "trigger", "trigger_source", "task_completion"})

RULES = ("own_write", "parked")
SKIPPED = "wake.skipped"
# A task in one of these still has work to do; a blocker in one of them still blocks.
UNFINISHED = ("open", "doing", "waiting", "review", "ready", "blocked")


def always_wakes(conn, actor, target, kind, refs, body, cause=None, in_reply_to=None):
    """The ALWAYS_WAKE key this message matches, or None. Rules never filter a match. A reply whose parent this
    cannot read matches "reply": fail open."""
    refs = refs if isinstance(refs, dict) else {}
    cause = CAUSE.get() if cause is None else cause
    if str(actor).startswith("human:") or str(cause).startswith("human:"):
        return "person"
    if kind in ALWAYS_KINDS:
        return "ask"
    if set(refs) & ALWAYS_REFS:
        return "approval"
    if refs.get(FLAG, False) is not False:
        return "needs_action"
    if VERDICT.search(str(body or "")):
        return "gate"
    if in_reply_to and _awaited_reply(conn, target, in_reply_to):
        return "reply"
    return None


def _awaited_reply(conn, target, in_reply_to):
    """Whether a reply answers something `target` waits on: its own ask, a message it flagged needs-action, or one
    it sent with wait_s. True when the parent cannot be read."""
    try:
        parent = conn.execute("SELECT from_actor,kind,refs_json,wait_s FROM messages WHERE id=?",
                              (in_reply_to,)).fetchone()
    except Exception:  # noqa: BLE001 - fail open
        return True
    if not parent:
        return True
    return parent["from_actor"] == target and bool(parent["kind"] == "ask" or parent["wait_s"]
                                                   or '"' + FLAG + '": true' in str(parent["refs_json"] or ""))


def awaited(refs):
    """The AWAITED key this wake's refs name, or None."""
    wake = refs.get("wake")
    if refs.get("blocker_done"):
        return "blocker_done"
    if refs.get("trigger") == "pr_checks":
        return "wake_once"
    if refs.get("subtask"):
        return "subtask"
    if refs.get("worktree_wait"):
        return "worktree"
    if wake in ("watcher", "watcher-done"):
        return "watcher"
    if wake == "resume":
        return "resume"
    return None


def parked(conn, task_row):
    """What the task waits on: 'blocked_by', 'wake_once', or None when it is not parked."""
    if not task_row or task_row.get("status") not in UNFINISHED:
        return None
    marks = ",".join("?" * len(UNFINISHED))
    if conn.execute("SELECT 1 FROM task_relations r JOIN tasks t ON t.id=r.from_task WHERE r.to_task=? "
                    f"AND r.kind='blocks' AND t.status IN ({marks}) LIMIT 1",
                    (task_row["id"], *UNFINISHED)).fetchone():
        return "blocked_by"
    if conn.execute("SELECT 1 FROM task_links WHERE task_id=? AND kind='pr' AND state IN ('open','draft','merged') "
                    "AND json_extract(detail_json,'$.wake_once') IS NOT NULL LIMIT 1",
                    (task_row["id"],)).fetchone():
        return "wake_once"
    return None


def _news(conn, actor, target, kind, refs, body, cause, in_reply_to=None):
    """Whether this wake is news a rule may hold: nothing on ALWAYS_WAKE or AWAITED, only NEWS_REFS, a comment or a
    task's status, step or relation moving, and in wake_presets' words a notice or a message. wake_presets' labels
    alone are no permission (it calls any bot's message a message), so the refs are checked here first. Reads a
    table only for a reply's parent."""
    from . import run_triggers, wake_presets
    if set(refs) - NEWS_REFS or awaited(refs):
        return False
    if always_wakes(conn, actor, target, kind, refs, body, cause, in_reply_to):
        return False
    if run_triggers.of_message({"refs": refs, "from_actor": actor})[0] not in HOLDABLE_TRIGGERS:
        return False
    return wake_presets.classify({"refs": refs, "from_actor": actor, "body": body, "kind": kind})[0] in HOLDABLE_CLASSES


def rule_for(conn, actor, target, kind, refs, body, task_row, cause=None, in_reply_to=None):
    """The rule that lets this wake wait, or None (it wakes). `task_row` is the task it is about, or None."""
    cause = CAUSE.get() if cause is None else cause
    refs = refs if isinstance(refs, dict) else {}
    if not _news(conn, actor, target, kind, refs, body, cause, in_reply_to):
        return None
    if cause and cause == target:
        return "own_write"
    if task_row and task_row.get("owner") == target and parked(conn, task_row):
        return "parked"
    return None


MARK = "held_by"


def _would_queue(conn, actor, target, refs):
    """Whether store.QUEUE_TRIGGER would queue a run for this message: to a bot that a runner runs, not from itself,
    not quiet and not a live transcript, decision or tool line."""
    from .harnesses import EXTERNAL_HARNESSES
    if not str(target).startswith("bot:") or actor == target or refs.get("quiet"):
        return False
    live = refs.get("live")
    if isinstance(live, dict) and live.get("kind") in ("transcript", "decision", "tool"):
        return False
    row = conn.execute("SELECT json_extract(config_json,'$.harness') FROM bot_config WHERE bot=?",
                       (str(target)[4:],)).fetchone()
    return not (row and row[0] in EXTERNAL_HARNESSES)


def gate(conn, actor, target, kind, refs, body, conv, in_reply_to=None):
    """The rule a message about to be written to `target` waits under, or None when it wakes as before. Most
    messages are not news (_news) and cost no query beyond a reply's parent. Fail open."""
    from . import hubdb as H
    try:
        refs = refs or {}
        if not isinstance(refs, dict) or not str(target).startswith("bot:"):
            return None
        cause = CAUSE.get()
        if not _news(conn, actor, target, kind, refs, body, cause, in_reply_to) \
                or not _would_queue(conn, actor, target, refs):
            return None
        task_id = H.message_task_id({"refs": refs}, conv)
        return rule_for(conn, actor, target, kind, refs, body, H.task(conn, task_id) if task_id else None, cause,
                        in_reply_to)
    except Exception:  # noqa: BLE001 - fail open: a wake this cannot read starts its run as before
        return None


def hold(conn, msg, rule):
    """Keep a skipped wake for the bot's next run, whatever starts it: a quiet note linked to the message (carried
    and marked read by execution.claim, as bot_to_bot.hold's are), and the `wake.skipped` audit row."""
    from . import hubdb as H
    task_id = H.message_task_id(msg, H.conversation(conn, msg["conversation_id"]))
    task_row = H.task(conn, task_id) if task_id else None
    where = f"on task {task_row['title']} ({task_row['id']})" if task_row else "in a direct message"
    why = {"own_write": "your own write caused it", "blocked_by": "the task waits on another task (blocked by)",
           "wake_once": "the task waits on its pull request's checks (wake once)"}.get(
        rule if rule == "own_write" else parked(conn, task_row), "the task is waiting")
    text = str(msg["body"] or "")
    if len(text) > H.NOTE_MAX - 400:
        text = text[:H.NOTE_MAX - 400] + "... (the rest is in the message)"
    sender = "Tico" if msg["from_actor"] == H.KEEPER else H.actor_id(msg["from_actor"])
    note = {"id": H.new_id(), "from_actor": msg["from_actor"], "to_actor": msg["to_actor"],
            "body": f"From {sender} {where}, held for this run because {why} (message {msg['id']}).\n\n{text}",
            "created": msg["created"], "message_id": msg["id"]}
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created,message_id) VALUES "
                 "(:id,:from_actor,:to_actor,:body,:created,:message_id)", note)
    from . import run_triggers
    trigger, _ = run_triggers.of_message(msg)
    H.event(conn, H.KEEPER, SKIPPED, msg["id"], {"bot": H.actor_id(msg["to_actor"]), "task": task_id, "rule": rule,
                                                  "trigger": trigger, "kind": msg["kind"], "from": msg["from_actor"],
                                                  "cause": CAUSE.get() or None, "note": note["id"]})
    return note
