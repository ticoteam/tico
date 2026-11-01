"""Which wakes may wait for a bot's next run (token saving 2/9, Dev 140).

A message to a bot starts a run (store.QUEUE_TRIGGER queues a job) unless it is quiet. `gate` is asked once for
every message about to be written to a bot that would start one (H._write_message), and decides with two rules:

- `own_write`: the bot's own write caused it. Its own comments, step moves, status changes, relations and
  checkpoints, on any of its tasks, never wake that same bot: its run already knows.
- `parked`: it is about a task the bot owns that waits on something (an unfinished `blocked_by` task, a pull
  request link armed with `wake_once`, or a milestone in backend/milestone_waits.py), and it is not that thing
  arriving. A comment, a relation change or a step move does not wake a parked task; its blocker finishing does.

A skipped wake is never dropped. The message is written to its task's room as before, quietly, and a quiet note
linked to it (the one bot_to_bot.hold leaves, carried and marked read by execution.claim) takes it to the bot's
next run whatever starts it, in the order it came. Every skip leaves one `wake.skipped` event (bot, task, the
message, its trigger and the rule) for the weekly audit's sample (docs/hub-sql.md). A bot's own comment on its
task leaves one too (`own_comment`): it is written to the other side, so the bot's own wake never exists to gate.

`ALWAYS_WAKE` is the list nothing filters, neither these rules nor any later one (token saving 3/9, 4/9, 9/9 and
the bot-to-bot rule in backend/bot_to_bot.py ask `always_wakes` first). `AWAITED` is what a waiting task waits for:
it wakes the bot even when the bot's own write caused it, because the session that waits is not the one that wrote
(knowledge: a bot finishing its own last subtask must still resume the parent, PR #657).

`same_state` (token saving 3/9, Dev 141): a stall or status check about a task the bot owns, when the task stands as it
did at the bot's last checkpoint, starts no run. `state_of` is that stand: status, step, owner, relations and their
states, open asks, every comment, the pull request links' head and checks. execution.complete stores it with the
checkpoint (`tasks.checkpoint_state`), and not when someone else changed the task during the turn, so a change the
bot never saw cannot hide in it. No checkpoint, no stored state, or any difference: the check runs as before.
Fail open: anything `gate` cannot read wakes.
"""

import contextvars
import hashlib
import json
import re

# Who made the request whose writes are being made (backend/app.py request_guard): the person, or the bot, or the
# person a bot or the Assistant acts for. Empty for the keeper's own sweeps and the runner's calls, which no rule
# reads as anyone's own write.
CAUSE = contextvars.ContextVar("wake_cause", default="")

# Never filtered. Each entry: (key, what it is). `always_wakes` returns the key that matched.
ALWAYS_WAKE = (
    ("person", "a person's message or comment, or a notice of a person's own act (an approval, a step move)"),
    ("manager", "a task comment from a party’s org-chart manager"),
    ("ask", "an ask, answer or steer: a question to the bot, or the answer it waits on"),
    ("approval", "an approval decided, withdrawn or asked about, or a structured question"),
    ("needs_action", "a message its sender flagged needs-action (or a flag that is not plainly true or false)"),
    ("reply", "a reply to the bot's own ask, to a message it flagged needs-action, or to one it waits on (wait_s)"),
    ("gate", "a factory gate verdict on the bot's ticket: Review 1, Review 2, QA or Security"),
    ("handoff", "a builder hand-off on its Dev ticket: a verdict to act on, a failed check, a merge conflict or a merge"),
)
ALWAYS_KINDS = frozenset({"ask", "answer", "steer"})
# References that make a message more than news: a decision, a question, an instruction.
ALWAYS_REFS = frozenset({"approval", "questions", "command", "run_now"})
FLAG = "needs_action"
VERDICT = re.compile(r"^\s*VERDICT:\s*(approve|changes)\s+PR\s+#\d+\s+head\s+[0-9a-f]{7,40}\b", re.I | re.M)
# The same verdict read for its fields, as a clause anywhere in prose: a review or QA child's verdict and the head it
# judged (hubdb.record_verdict). A quoted format line ("approve|changes", "pass or fail", "PR #<n> head <sha>")
# names no verdict, so it never overwrites a real one before it.
VERDICT_FIELDS = re.compile(r"\bVERDICT:\s*(approve|changes|pass|fail)(?![\w-])(?!\s*[|/])(?!\s+or\b)"
                            r"(?:\s+PR\s+#\d+\b)?(?:\s+head\s+([0-9a-f]{7,40})\b)?(?!\s+(?:PR|head)\b)",
                            re.I | re.M)


def verdict_fields(text):
    """The last VERDICT clause wins, including clauses inside prose."""
    found = None
    for found in VERDICT_FIELDS.finditer(str(text or "")):
        pass
    return (found[1].lower(), (found[2] or "").lower() or None) if found else None


# What a parked task waits for, by the refs its wake carries. These wake even when the bot's own write caused them.
AWAITED = (
    ("blocker_done", "a task this one waits on (blocked_by) finished"),
    ("wake_once", "a pull request link armed with wake_once got its result (github.flush_wakes, trigger pr_checks)"),
    ("subtask", "a subtask moved or finished"),
    ("worktree", "the worktree it waited for is ready"),
    ("watcher", "something the bot watches moved or ended"),
    ("resume", "a deferred task's stored resume condition came true (backend/quiet_work.py)"),
    ("milestone", "a pull request it waits for merged or a release it waits for was published (backend/milestone_waits.py)"),
    ("reconnected", "the account or credential its reconnect card waited for works again (backend/reconnects.py)"),
)
# Only news may wait: a comment, or a task's status, step or relation moving (run_triggers.TRIGGERS). A stall or
# status check, a schedule, a pull request's checks and anything else keep their own rules (wake presets).
HOLDABLE_TRIGGERS = ("comment", "status_or_relation")
# wake_presets.classify classes that are news. An assignment, a blocker, a review request, an awaited result, a
# question or a person's message is not, whatever its trigger says.
HOLDABLE_CLASSES = ("notice", "message")
# The references news carries: a plain message's, the trigger stamp, a finished task's summary. Anything else (a
# wake kind, a batch, an answer) is more than news, and so is a reference no rule here knows yet.
# `news` names the event of a notice that is news but not a plain comment (rules.NOTE_EVENTS, task_done...).
# The person whose own act a keeper notice reports (H._write_message): kept on the message, after the request ends.
CAUSED_BY = "caused_by"
NEWS_REFS = frozenset({"task", "comment", "attachments", "files", "unsolicited", "turn_id", "warnings", "via",
                       FLAG, "trigger", "trigger_source", "task_completion", CAUSED_BY, "rule", "news"})

# The stall checks that only ask "has anything moved?"; same_state may hold these. A stuck run, a setup that is
# held, a restated ask, a due date and a snooze ending are other news and keep waking.
SAME_STATE_WAKES = ("status_check", "stalled")
RULES = ("own_write", "parked", "same_state")
# Holds that leave no note: the check only asked whether anything moved (same_state), or a rule said to ignore it.
SILENT = ("same_state", "ignored")
# Holds a table rule makes (backend/rules.py): the generic words, as the rule's own id is on the event.
ROW_HOLDS = ("rule", "ignored")
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
    if refs.get("comment") and str(actor).startswith("bot:") and str(target).startswith("bot:"):
        from . import hubdb as H
        row = H.task(conn, H._task_ref(refs.get("task")) or "")
        if (row and actor not in (row["owner"], row["requester"])
                and H.task_participant(conn, actor, row)):
            return "manager"
    if kind in ALWAYS_KINDS:
        return "ask"
    if set(refs) & ALWAYS_REFS:
        return "approval"
    if refs.get(FLAG, False) is not False:
        return "needs_action"
    if VERDICT.search(str(body or "")):
        return "gate"
    if actor == "keeper" and refs.get("handoff"):
        return "handoff"        # only Tico marks one (backend/verdict_moves.py)
    if in_reply_to and _awaited_reply(conn, target, in_reply_to):
        return "reply"
    return None


def _awaited_reply(conn, target, in_reply_to):
    """Whether a reply answers something `target` waits on: its own ask, a message it flagged needs-action, or one
    it sent with wait_s. True when the parent cannot be read."""
    try:
        parent = conn.execute("SELECT from_actor,kind,refs,wait_s FROM messages WHERE id=?",
                              (in_reply_to,)).fetchone()
    except Exception:  # noqa: BLE001 - fail open
        return True
    if not parent:
        return True
    return parent["from_actor"] == target and bool(parent["kind"] == "ask" or parent["wait_s"]
                                                   or '"' + FLAG + '": true' in str(parent["refs"] or ""))


def awaited(refs):
    """The AWAITED key this wake's refs name, or None."""
    wake = refs.get("wake")
    if refs.get("blocker_done"):
        return "blocker_done"
    if refs.get("trigger") == "pr_checks" and wake != "pr_review":      # a review note is news, not a result
        return "wake_once"
    if refs.get("subtask"):
        return "subtask"
    if refs.get("worktree_wait"):
        return "worktree"
    if wake in ("watcher", "watcher-done"):
        return "watcher"
    if wake == "resume":
        return "resume"
    if refs.get("milestone"):
        return "milestone"
    if refs.get("reconnected"):
        return "reconnected"
    return None


def parked(conn, task_row):
    """What the task waits on: 'blocked_by', 'wake_once', 'milestone', 'reconnect', or None when it is not parked."""
    if not task_row or task_row.get("status") not in UNFINISHED:
        return None
    marks = ",".join("?" * len(UNFINISHED))
    if conn.execute("SELECT 1 FROM task_relations r JOIN tasks t ON t.id=r.from_task WHERE r.to_task=? "
                    f"AND r.kind='blocks' AND t.status IN ({marks}) LIMIT 1",
                    (task_row["id"], *UNFINISHED)).fetchone():
        return "blocked_by"
    if conn.execute("SELECT 1 FROM task_links WHERE task_id=? AND kind='pr' AND state IN ('open','draft','merged') "
                    "AND json_extract(detail,'$.wake_once') IS NOT NULL LIMIT 1",
                    (task_row["id"],)).fetchone():
        return "wake_once"
    from .milestone_waits import pending
    if pending(conn, task_row["id"]):
        return "milestone"
    from .reconnects import pending as reconnect_pending
    if reconnect_pending(conn, task_row["id"]):
        return "reconnect"
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
    return hold_rule(conn, target, task_row, cause)


def hold_rule(conn, target, task_row, cause=None):
    """For a wake that is news (_news): the rule that lets it wait, or None."""
    cause = CAUSE.get() if cause is None else cause
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
    row = conn.execute("SELECT json_extract(config,'$.harness') FROM bot_config WHERE bot=?",
                       (str(target)[4:],)).fetchone()
    return not (row and row[0] in EXTERNAL_HARNESSES)


def state_of(conn, task_row):
    """A short hash of where the task stands (see the module note), or None when it cannot be read. Tico's own
    notices in the task's room are left out (a held check is not news); every comment, from anyone, counts."""
    from . import hubdb as H
    try:
        tid = task_row["id"]
        row = H.task(conn, tid)
        if not row:
            return None
        parts = {
            "task": [row.get(k) for k in ("status", "step_id", "owner", "requester", "waiting_on", "snoozed_until",
                                           "version")],
            "relations": [list(r) for r in conn.execute(
                "SELECT r.kind, r.from_task, r.to_task, t.status FROM task_relations r JOIN tasks t ON t.id="
                "CASE WHEN r.from_task=? THEN r.to_task ELSE r.from_task END "
                "WHERE ? IN (r.from_task, r.to_task) ORDER BY r.kind, r.from_task, r.to_task", (tid, tid))],
            "asks": [a["id"] for a in H.open_task_asks(conn, row)],
            "comments": [list(r) for r in conn.execute(
                "SELECT id, edited_at FROM messages WHERE conversation_id=? AND from_actor<>? AND deleted_at IS NULL "
                "ORDER BY rowid", (row["conversation_id"], H.KEEPER))],
            "links": [list(r) for r in conn.execute(
                "SELECT kind, url, state, pr_sha, json_extract(detail,'$.head_sha'), "
                "checks, mergeable, review_state, pending_comments FROM task_links "
                "WHERE task_id=? ORDER BY created_at, id", (tid,))],
        }
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]
    except Exception:  # noqa: BLE001 - unreadable state is a state nobody can call the same
        return None


def settled_state(conn, task_row, bot, started, attempt_id):
    """The state to store with the bot's checkpoint, or None when someone else changed the task since the turn
    `started_at`, or another turn of the same bot overlapped it: the bot may not have seen that, so the next check runs."""
    from . import hubdb as H
    try:
        me = "bot:" + bot
        # Actor identity alone cannot attribute a write to this turn: task lanes run in parallel.
        # Include siblings that finished during this turn, not only those still running now.
        if conn.execute("SELECT 1 FROM attempts WHERE bot=? AND id<>? "
                        "AND (state IN ('leased','running') OR finished_at>=?) LIMIT 1",
                        (bot, attempt_id, started)).fetchone():
            return None
        if conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND ts>? AND actor<>? LIMIT 1",
                        (task_row["id"], started, me)).fetchone():
            return None
        # The hash includes related tasks' states too. Their events live on those tasks, not this one;
        # a change during the turn must not be saved as if the bot had already seen it.
        if conn.execute("SELECT 1 FROM task_events WHERE task_id IN ("
                        "SELECT CASE WHEN from_task=? THEN to_task ELSE from_task END FROM task_relations "
                        "WHERE ? IN (from_task,to_task)) AND ts>? AND actor<>? LIMIT 1",
                        (task_row["id"], task_row["id"], started, me)).fetchone():
            return None
        if conn.execute("SELECT 1 FROM messages WHERE conversation_id=? AND created_at>? AND from_actor NOT IN (?,?) "
                        "LIMIT 1", (task_row["conversation_id"], started, me, H.KEEPER)).fetchone():
            return None
        # Starting a task lane records this turn's own session link after claim. That is known
        # to this turn; explicit edits by someone else are already caught by task_events above.
        if conn.execute("SELECT 1 FROM task_links WHERE task_id=? AND kind<>'session' "
                        "AND coalesce(updated_at,created_at)>? LIMIT 1",
                        (task_row["id"], started)).fetchone():
            return None
        return state_of(conn, task_row)
    except Exception:  # noqa: BLE001
        return None


def at_checkpoint(conn, task_row, target):
    """Whether the task stands exactly as at `target`'s last checkpoint on it, with no newer turn of its running:
    no stored state, a blocked checkpoint, someone else's checkpoint or any difference is False."""
    if not task_row or task_row.get("owner") != target or task_row.get("checkpoint_blocked") \
            or not task_row.get("checkpoint_state") or task_row.get("checkpoint_by") != target:
        return False
    from . import hubdb as H
    lanes, _ = H._session_lanes(conn, task_row)
    # A completed turn's checkpoint says nothing about a newer active turn's health. Its
    # first status check seeds the retry and diagnostic path when it stops making progress.
    if conn.execute("SELECT 1 FROM attempts WHERE bot=? AND lane IN (?,?) "
                    "AND state IN ('leased','running') LIMIT 1", (H.actor_id(target), *lanes)).fetchone():
        return False
    return state_of(conn, task_row) == task_row["checkpoint_state"]


def same_state(conn, actor, target, kind, refs, body, task_row, cause=None, in_reply_to=None):
    """Whether this wake is a plain stall or status check about a task `target` owns that stands exactly as at its
    last checkpoint. Nothing on ALWAYS_WAKE or AWAITED is ever held."""
    if not task_row or task_row.get("owner") != target or refs.get("trigger") != "stall_check" \
            or refs.get("wake") not in SAME_STATE_WAKES or set(refs) - NEWS_REFS - {"wake"} or awaited(refs):
        return None
    if task_row.get("checkpoint_blocked") or not task_row.get("checkpoint_state") \
            or task_row.get("checkpoint_by") != target:
        return None
    if always_wakes(conn, actor, target, kind, refs, body, CAUSE.get() if cause is None else cause, in_reply_to):
        return None
    return "same_state" if at_checkpoint(conn, task_row, target) else None


def gate(conn, actor, target, kind, refs, body, conv, in_reply_to=None):
    """The rule a message about to be written to `target` waits under, or None when it wakes as before. Most
    messages are not news (_news) and cost no query beyond a reply's parent. Fail open."""
    from . import hubdb as H
    try:
        refs = refs or {}
        if not isinstance(refs, dict) or not str(target).startswith("bot:"):
            return None
        cause = CAUSE.get()
        if not _would_queue(conn, actor, target, refs):
            return None
        task_id = H.message_task_id({"refs": refs}, conv)
        task_row = H.task(conn, task_id) if task_id else None
        if _news(conn, actor, target, kind, refs, body, cause, in_reply_to):
            return rule_for(conn, actor, target, kind, refs, body, task_row, cause, in_reply_to)
        return same_state(conn, actor, target, kind, refs, body, task_row, cause, in_reply_to)
    except Exception:  # noqa: BLE001 - fail open: a wake this cannot read starts its run as before
        return None


def own_comment(conn, msg, task_row):
    """A bot's own comment on a task it is a party to (owner, requester or the one that filed it). The comment goes
    to the other side, so no wake for the bot itself is ever written: this is the `own_write` skip the audit counts.
    Nothing is held (no note): the bot wrote it, so its run already knows."""
    from . import hubdb as H
    author = msg["from_actor"]
    if not H.is_bot(author) or author not in (task_row["owner"], task_row["requester"], H.task_origin(conn, task_row)):
        return None
    return H.event(conn, H.KEEPER, SKIPPED, msg["id"], {"bot": H.actor_id(author), "task": task_row["id"],
                                                         "rule": "own_write", "trigger": "comment", "kind": msg["kind"],
                                                         "from": author, "cause": CAUSE.get() or None, "note": None})


def hold(conn, msg, rule, rule_id=None):
    """Keep a skipped wake for the bot's next run, whatever starts it: a quiet note linked to the message (carried
    and marked read by execution.claim, as bot_to_bot.hold's are), and the `wake.skipped` audit row. `rule` is the
    reason (own_write, parked, same_state, or a table rule's "rule" / "ignored"); `rule_id` the id of the rule
    behind it, kept on the event."""
    from . import hubdb as H
    task_id = H.message_task_id(msg, H.conversation(conn, msg["conversation_id"]))
    task_row = H.task(conn, task_id) if task_id else None
    if rule in SILENT:
        # Nothing to carry: the check only asked whether anything moved and nothing did, or a rule said to ignore it.
        from . import run_triggers
        return H.event(conn, H.KEEPER, SKIPPED, msg["id"], {
            "bot": H.actor_id(msg["to_actor"]), "task": task_id, "rule": rule, "rule_id": rule_id or rule,
            "trigger": run_triggers.of_message(msg)[0], "kind": msg["kind"], "from": msg["from_actor"],
            "cause": CAUSE.get() or None, "note": None})
    where = f"on task {task_row['title']} ({task_row['id']})" if task_row else "in a direct message"
    why = "a wake rule holds it" if rule in ROW_HOLDS else {
        "own_write": "your own write caused it", "blocked_by": "the task waits on another task (blocked by)",
        "wake_once": "the task waits on its pull request's checks (wake once)",
        "milestone": "the task waits for a pull request to merge or a release to be published",
        "reconnect": "the task waits for an account to be reconnected"}.get(
        rule if rule == "own_write" else parked(conn, task_row), "the task is waiting")
    text = str(msg["body"] or "")
    if len(text) > H.NOTE_MAX - 400:
        text = text[:H.NOTE_MAX - 400] + "... (the rest is in the message)"
    sender = "Tico" if msg["from_actor"] == H.KEEPER else H.actor_id(msg["from_actor"])
    note = {"id": H.new_id(), "from_actor": msg["from_actor"], "to_actor": msg["to_actor"],
            "body": f"From {sender} {where}, held for this run because {why} (message {msg['id']}).\n\n{text}",
            "created_at": msg["created_at"], "message_id": msg["id"]}
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created_at,message_id) VALUES "
                 "(:id,:from_actor,:to_actor,:body,:created_at,:message_id)", note)
    from . import run_triggers
    trigger, _ = run_triggers.of_message(msg)
    H.event(conn, H.KEEPER, SKIPPED, msg["id"], {"bot": H.actor_id(msg["to_actor"]), "task": task_id, "rule": rule,
                                                  "rule_id": rule_id or rule, "trigger": trigger, "kind": msg["kind"], "from": msg["from_actor"],
                                                  "cause": CAUSE.get() or None, "note": note["id"]})
    return note
