# pyright: strict
"""A ❓: a mark a bot puts on its own chat message, for one named person. It means "needs your reply", nothing more: no
task is filed behind it and nothing is sent for it (no notice, no push, no mail). Needs you lists it, and the bot is not
chased while it waits on its own open ❓ (waiting_on.of).

- Only a bot marks, and only its own message. Tico itself never authors one.
- The person must be on the roster, in the bot's reach (as an ask is), and able to read the message.
- The message's first line passes ask_shape.line_refusal, as any ask to a person does.
- On a task, approvals win over questions: one card per person per task. A bot's ❓ to a person whose approval of the
  stage's page waits on them is refused, whatever it says (`approval_of`); one naming that page is refused whoever it
  asks (the person OKs the page there); and in Product's Validate it is refused (validate_gate.check_question).
- `ask` runs every check before it posts the message, so a refused ❓ leaves nothing in the chat.
- One open ❓ per bot per chat (the chat's root conversation, `room_id`): a new one clears the old ("replaced"), and
  the bot is told so (`replaced_warning`), so it puts every question to a person in one ❓.
- A bot does not finish, move on or close a task while its own ❓ there is open (`refuse_ending`): ending the task would
  clear it unanswered. It waits for the reply, or takes the question back by deleting its message (`TAKE_BACK`). A bot
  closing a task it filed itself is withdrawing it, and its question goes with it.

It clears when the person replies in that chat, in their own hand or by their Assistant on their word (`replied`, from
hubdb._write_message: `cleared_via` keeps which); when the person answers the stage's page approval on its task
(`approval_answered`, from answers.answer); when its task ends (done or closed: the migration's trigger; merged:
task_merge; deleted: task_engine.on_delete); or when the bot marks a newer message in the chat. Give to moves an open
one to another person (`give`).

The open mark is mirrored on its message as refs.question {id, to} (the migration's triggers), so every chat renderer
draws its glyph from the message alone and a change reaches open pages through the messages trigger.
"""

import sqlite3
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, cast

from . import ask_shape
from . import hubdb as _hubdb

H: Any = _hubdb  # untyped module, through Any as in ask_quality.py

Row = dict[str, Any]


PREFIX = "qmark:"  # a ❓'s wait and Needs-you item id: "qmark:<mark id>"
SNOOZE_VISIT = "qmark"  # ask_snoozes.visit_id of a snoozed ❓; its requirement_id is the mark's id
LINE_MAX = 160
# Who may clear a ❓ by writing in a person's name: their own hand (Slack included, refs.via "slack"), their personal
# token, and their Assistant on their word. Agent Ops and an automation writing as them do not answer for them.
REPLY_VIA = (None, "", "slack", "token", "assistant")

_OPEN = "SELECT * FROM question_marks WHERE cleared_at IS NULL ORDER BY created_at, id"
_OPEN_ON_TASK = "SELECT * FROM question_marks WHERE cleared_at IS NULL AND task_id=? ORDER BY created_at, id"
_OPEN_ON_TASKS = (
    "SELECT * FROM question_marks WHERE cleared_at IS NULL AND task_id IN (SELECT value FROM json_each(?)) "
    "ORDER BY created_at, id"
)


class Asking:
    """A ❓'s place for the org rule check: the message's own place, marked as a bot's question to a person
    (org_rule_check.asking), so a `kind:ask` rule ("Asks to a person") checks it before it is written."""

    asks_person = True

    def __init__(self, place: Any) -> None:
        self.place = place

    def __call__(self, c: Any, author: Any, recipients: Any) -> Row:
        found: Any = self.place(c, author, recipients) if callable(self.place) else self.place
        out: Row = dict(found) if found else {}
        out["ask"] = True
        return out


def public(mark: Mapping[str, Any]) -> Row:
    """A mark as a route answers with it; `replaced`, the bot's open ❓ in that chat this one cleared, when there was one."""
    keys = ("id", "message_id", "conversation_id", "task_id", "bot_id", "person_id", "line", "created_at")
    out = {k: (mark.get(k) or None) if k == "task_id" else mark.get(k) for k in keys}
    if mark.get("replaced"):
        out["replaced"] = mark["replaced"]
    return out


REPLACED = (
    'This replaced your open ❓ from {when}, "{line}". Put every question in one ❓, numbered when there are several.'
)


def _when(at: object) -> str:
    text = str(at or "")
    return f"{text[:10]} {text[11:16]} UTC" if len(text) >= 16 else text


def replaced_warning(mark: Mapping[str, Any]) -> str | None:
    """What the bot is told when its new ❓ cleared its open one in that chat: the earlier question is no longer asked."""
    old: Any = mark.get("replaced")
    if not old:
        return None
    return REPLACED.format(when=_when(old.get("created_at")), line=old.get("line") or "")


def with_warning(answer: Row, mark: Mapping[str, Any]) -> Row:
    """A ❓ route's answer with `replaced_warning` added to any `warning` it carries (the CLI prints it)."""
    said = replaced_warning(mark)
    if not said:
        return answer
    return {**answer, "warning": " ".join(w for w in (answer.get("warning"), said) if w)}


# How a bot takes back its own ❓: deleting its message clears the mark (the migration's trigger).
TAKE_BACK = "tico thread delete {message}"
STILL_OPEN = (
    'Your ❓ to {name} is still open here: "{line}". Finishing, moving on or closing the task would clear it unanswered. '
    "Wait for their reply, or take the question back first: " + TAKE_BACK + "."
)


# Tico's own feature ending a task whose ❓ it worded for its bot, because the answer no longer matters (a measure read
# since, a vision set elsewhere): the ❓ ends with the task, as it does on any move Tico makes itself.
_SETTLED: ContextVar[bool] = ContextVar("question_marks_settled", default=False)


@contextmanager
def settled() -> Generator[None]:
    token = _SETTLED.set(True)
    try:
        yield
    finally:
        _SETTLED.reset(token)


def open_of(conn: sqlite3.Connection, bot: str, task_id: object) -> Row | None:
    """The bot's own open ❓ on this task's chat that its person can still answer, or None."""
    found = conn.execute(
        "SELECT * FROM question_marks WHERE task_id=? AND bot_id=? AND cleared_at IS NULL", (str(task_id), bot)
    )
    return next((m for m in (dict(r) for r in found) if answerable(conn, m)), None)


def refuse_ending(conn: sqlite3.Connection, by: str, task_id: object, *, closing: bool = False) -> None:
    """Refuse a bot's done, forward move or close of a task while its own ❓ there is open: the task ending would
    clear the question before the person saw it. A person's moves, and Tico's own, are never held by a ❓, nor a bot's
    close of a task it filed itself: that withdraws the task, and the question is moot with it."""
    if not task_id or not H.is_bot(by) or by == H.TICO_ACTOR or _SETTLED.get():
        return
    if closing:
        filed = conn.execute("SELECT requester_id FROM tasks WHERE id=?", (str(task_id),)).fetchone()
        if filed and filed["requester_id"] == by:
            return
    found = open_of(conn, by, task_id)
    if not found:
        return
    detail = STILL_OPEN.format(
        name=H.actor_name(conn, found["person_id"]), line=found["line"], message=found["message_id"]
    )
    H.refuse(conn, by, "task_question_open", detail, task=str(task_id))


def wait_id(mark: Mapping[str, Any]) -> str:
    return PREFIX + str(mark["id"])


def mark_id(value: object) -> str | None:
    """The mark id of a ❓'s wait or item id, or None for anything else."""
    text = str(value or "")
    return text[len(PREFIX) :] if text.startswith(PREFIX) else None


def open_rows(conn: sqlite3.Connection) -> list[Row]:
    """Every open ❓ its person can still answer (`answerable`), oldest first, read once per snapshot (read_cache):
    Needs you and the ladder read them for many tasks in one pass. One whose person left or lost the chat waits on
    nobody, so its bot is not held, even before `clear_gone` clears it. Shared: never change a row."""
    from . import read_cache as _read_cache

    read_cache = cast(Any, _read_cache)

    return read_cache.value(conn, ("question_marks_open",), lambda: _answerable_open(conn, _OPEN), shared=True)


def _answerable_open(conn: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> list[Row]:
    """The open ❓ `sql` reads that their person can still answer (`answerable`), oldest first."""
    rows = [dict(r) for r in conn.execute(sql, args)]
    if not rows:
        return []
    # Their messages, chats and tasks in a few reads for all of them, never a few per mark (a never-stuck pass).
    stored = H.prime_messages(conn, {m["message_id"] for m in rows})
    from . import task_privacy as _task_privacy

    cast(Any, _task_privacy).prime_messages(conn, stored.values())
    H.prime_tasks(conn, {m["task_id"] for m in rows if m["task_id"]}, with_tags=False)
    chats = chat_rows(conn, {m["conversation_id"] for m in stored.values()})
    return [m for m in rows if answerable(conn, m, chats)]


def chat_rows(conn: sqlite3.Connection, ids: Iterable[object]) -> dict[str, Row]:
    """These chats' rows by id, in one read: for `readable` over many marks. Inside a snapshot a chat read already
    (hubdb.conversation, prime_conversations) is not read again (hubdb.conversation_rows); outside one, one read."""
    from . import read_cache

    wanted = sorted({str(i) for i in ids if i})
    if not wanted:
        return {}
    if read_cache.cache(conn) is not None:
        return {str(cid): dict(row) for cid, row in H.conversation_rows(conn, wanted).items()}
    return {
        str(r["id"]): dict(r)
        for r in conn.execute(
            "SELECT * FROM conversations WHERE id IN (SELECT value FROM json_each(?))", (H._dump(wanted),)
        )
    }


def answerable(conn: sqlite3.Connection, mark: Mapping[str, Any], chats: Mapping[str, Row] | None = None) -> bool:
    """Whether the person an open ❓ names can still answer it: still here and able to act (task_requirements
    .active_person), and able to read the marked message. `chats` holds chat rows already read, by id."""
    from . import task_requirements as _task_requirements

    R = cast(Any, _task_requirements)

    if not R.active_person(conn, mark["person_id"]):
        return False
    message = H.message(conn, mark["message_id"])
    if not message:
        return False
    return readable(conn, str(mark["person_id"]), message, (chats or {}).get(str(message["conversation_id"])))


def clear_gone(conn: sqlite3.Connection, task_id: object = None) -> list[str]:
    """Clear each open ❓ (on `task_id`, or anywhere) whose person can no longer answer it: they left, lost sign-in, or
    can no longer read the chat (`cleared_reason` 'addressee_gone'). Nobody else is asked for them: on a task, its bot
    is handed the task back and told the question went nowhere, so it can ask someone who can. Run when the roster or
    a task's readers change, and on each never-stuck pass for anything else. Returns the cleared marks' ids."""
    if task_id:
        found = conn.execute("SELECT * FROM question_marks WHERE cleared_at IS NULL AND task_id=?", (str(task_id),))
        gone = [m for m in (dict(r) for r in found) if not answerable(conn, m)]
    else:  # every open mark: those open_rows leaves out, so a never-stuck pass checks each mark once
        kept = {m["id"] for m in open_rows(conn)}
        gone = [m for m in (dict(r) for r in conn.execute(_OPEN)) if m["id"] not in kept]
    if not gone:
        return []
    at = H.now()
    conn.execute(
        "UPDATE question_marks SET cleared_at=?, cleared_reason='addressee_gone', cleared_by_id=? "
        "WHERE id IN (SELECT value FROM json_each(?)) AND cleared_at IS NULL",
        (at, H.TICO_ACTOR, H._dump([m["id"] for m in gone])),
    )
    for bot in sorted({str(m["bot_id"]) for m in gone}):
        H._recount(conn, bot)
    for m in gone:
        H.event(conn, H.TICO_ACTOR, "question.cleared", m["id"], {"reason": "addressee_gone", "to": m["person_id"]})
        if m["task_id"]:
            after_reply(conn, m["task_id"], H.TICO_ACTOR, "Its question can't be answered")
            _tell_gone(conn, m)
    return [str(m["id"]) for m in gone]


def _tell_gone(conn: sqlite3.Connection, mark: Row) -> None:
    """Wake the bot on the task: its ❓ went nowhere, so it asks someone who can, or goes on without the answer."""
    from . import task_engine as _task_engine

    E = cast(Any, _task_engine)
    from . import task_wakes as _task_wakes

    task_wakes = cast(Any, _task_wakes)

    row: Row | None = E.task(conn, mark["task_id"])
    if not row or row.get("status") != "open" or not row.get("visit"):
        return
    name = H.actor_name(conn, mark["person_id"])
    lead = (
        f"{name} can no longer answer your question \"{mark['line']}\" (they left, or can't read this chat any more). "
        "Ask someone who can, or go on without it."
    )
    task_wakes.add(
        conn,
        row,
        mark["bot_id"],
        row["visit"],
        task_wakes.body(conn, row, lead, to=mark["bot_id"]),
        wake_key=f"{row['id']}:{row['visit']['id']}:qmark-gone:{mark['id']}",
    )


GLYPH = "❓"
# What a line may open with before its ❓ and still lead with it: space and Markdown's emphasis, heading and quote marks.
_LEAD = " \t\r\n*_#>"
UNASKED = "question_unasked"  # the wake's refs key (true): its own turn is never woken for this again
UNASKED_LEAD = (
    "Your reply began with ❓, but nobody was asked: {where}. A ❓ is a mark that only `tico task ask` puts on a message; "
    "typed into a reply or a note it asks no one, and nobody's Needs you shows it. "
    'If you still need an answer, ask now: tico task ask {task} --to <person> "<one short line>", detail on the lines '
    "after it."
)
KEPT = "your reply stayed on your run, where no one reads it"
POSTED = "it was posted as a plain message"


def leads_with_mark(text: object) -> bool:
    """Whether the words open with a ❓ (after any space or Markdown emphasis): written as a question to a person."""
    return str(text or "").lstrip(_LEAD).startswith(GLYPH)


def unasked_reply(
    conn: sqlite3.Connection, aid: str, bot: str, task_id: object, text: str, *, posted: bool, woken_by: object
) -> str | None:
    """A turn on an open task ended with a reply that leads with ❓ and marked no ❓ (nor had an ask held for a person's
    review): the bot meant to ask a person and nobody was asked. The bot is woken on the task once, told how to ask and
    what its words must fix first (ask_shape), so the question becomes a real ❓. A turn this wake started is not woken
    again for it. Returns the wake's message id, or None when nothing was owed."""
    if not task_id or not leads_with_mark(text) or not H.is_bot(bot):
        return None
    from . import task_engine as _task_engine

    found: Row | None = cast(Any, _task_engine).task(conn, str(task_id))
    if not found or found.get("status") != "open" or not found.get("visit"):
        return None
    refs: Any = H._json(cast(Row, woken_by).get("refs"), {}) if isinstance(woken_by, Mapping) else {}
    if isinstance(refs, dict) and cast(Row, refs).get(UNASKED):
        return None
    started = conn.execute("SELECT coalesce(started_at, created_at) FROM attempts WHERE id=?", (aid,)).fetchone()
    since = str(started[0]) if started and started[0] else ""
    if (
        conn.execute("SELECT 1 FROM question_marks WHERE bot_id=? AND created_at>=? LIMIT 1", (bot, since)).fetchone()
        or conn.execute(
            "SELECT 1 FROM org_rule_holds WHERE author=? AND state='held' AND created_at>=? LIMIT 1", (bot, since)
        ).fetchone()
    ):
        return None
    from . import task_wakes as _task_wakes

    task_wakes = cast(Any, _task_wakes)
    row = found
    lead = UNASKED_LEAD.format(where=POSTED if posted else KEPT, task=row["id"])
    shape = ask_shape.line_refusal(str(text).lstrip(_LEAD)[len(GLYPH) :])
    if shape:
        lead += f" Your words would be refused as they are: {shape}"
    H.event(conn, H.TICO_ACTOR, "question.unasked", aid, {"bot": bot, "task": row["id"], "posted": posted})
    return task_wakes.add(
        conn,
        row,
        bot,
        row["visit"],
        task_wakes.body(conn, row, lead, to=bot),
        wake_key=f"{row['id']}:{row['visit']['id']}:{UNASKED}:{aid}",
        refs={UNASKED: True},
    )


def on_task(conn: sqlite3.Connection, task_id: object) -> list[Row]:
    """The task's open ❓ its person can still answer, oldest first. A snapshot that already read every open ❓ (a pass
    over many tasks) takes them from that list; reading one task reads only its own (question_marks_open_task), never
    the company's: every open ❓ with its message, task and chat made each task page cost what the company's
    questions did."""
    from . import read_cache as _read_cache

    read_cache = cast(Any, _read_cache)
    tid = str(task_id or "")
    if not tid:
        return []
    values = read_cache.cache(conn)
    if values is not None and ("question_marks_open",) in values:
        return [m for m in open_rows(conn) if m["task_id"] == tid]

    def build() -> list[Row]:
        rows = [dict(r) for r in conn.execute(_OPEN_ON_TASK, (tid,))]
        if not rows:
            return []
        stored = H.prime_messages(conn, {m["message_id"] for m in rows})
        chats = chat_rows(conn, {m["conversation_id"] for m in stored.values()})
        return [m for m in rows if answerable(conn, m, chats)]

    return read_cache.value(conn, ("question_marks_on_task", tid), build, shared=True)


def prime_on_tasks(conn: sqlite3.Connection, task_ids: Iterable[object]) -> None:
    """Read the open ❓ of many tasks at once for `on_task` in this snapshot: a pass over many tasks (waiting_on.prime)
    reads them in one statement (question_marks_open_task, the ids as one JSON list), never one read and its
    messages and chats per task, nor the company's every open ❓ when it reads only some tasks. Nothing to do when the
    snapshot already read every open ❓ (`open_rows`), which `on_task` then reuses."""
    from . import read_cache as _read_cache

    values = cast(Any, _read_cache).cache(conn)
    if values is None or ("question_marks_open",) in values:
        return
    unread = [t for t in sorted({str(t) for t in task_ids if t}) if ("question_marks_on_task", t) not in values]
    if not unread:
        return
    kept = _answerable_open(conn, _OPEN_ON_TASKS, (H._dump(unread),))
    for tid in unread:
        values[("question_marks_on_task", tid)] = [m for m in kept if m["task_id"] == tid]


REPLIED_SQL = "SELECT task_id, COUNT(*) FROM question_marks WHERE cleared_message_id IS NOT NULL AND task_id <> '' GROUP BY task_id"


def replies(conn: sqlite3.Connection, task_id: object) -> int:
    """How many ❓ on the task a person replied to (task_engine.progress_mark): one read per snapshot for every task
    (read_cache; a write reads again), so a never-stuck pass over many tasks never reads them one task at a time."""
    from . import read_cache as _read_cache

    read_cache = cast(Any, _read_cache)

    def build() -> dict[str, int]:
        return {str(tid): int(n) for tid, n in conn.execute(REPLIED_SQL)}

    return read_cache.value(conn, ("question_marks_replied",), build, shared=True).get(str(task_id), 0)


def for_person(conn: sqlite3.Connection, person: str) -> list[Row]:
    return [m for m in open_rows(conn) if m["person_id"] == person]


def get(conn: sqlite3.Connection, mid: str) -> Row | None:
    found = conn.execute("SELECT * FROM question_marks WHERE id=?", (mid,)).fetchone()
    return dict(found) if found else None


def first_line(body: object) -> str:
    lines = str(body or "").strip().splitlines()
    one = " ".join((lines[0] if lines else "").split())
    return one if len(one) <= LINE_MAX else one[: LINE_MAX - 1].rstrip() + "…"


def room_of(conv: Mapping[str, Any]) -> str:
    return str(conv.get("root_id") or conv["id"])


def _message_task(conn: sqlite3.Connection, message: Row, conv: Row) -> str:
    """The task whose chat the message is in: its conversation's, or the one its refs name in a bot's room."""
    if conv.get("task_id"):
        return str(conv["task_id"])
    refs: Any = H._json(message.get("refs"), {}) or {}
    task_id: Any = cast(Row, refs).get("task") if isinstance(refs, dict) else None
    return str(task_id) if task_id else ""


def readable(conn: sqlite3.Connection, person: str, message: Row, chat: Row | None = None, viewer: Any = None) -> bool:
    """Whether `person` may read the marked message: the thread's members and the message's own check. `chat`, the
    message's chat row, and `viewer`, the person's threads.actor_viewer, when the caller has read them for many ❓s,
    save reading them again for each."""
    from . import threads as _threads

    threads = cast(Any, _threads)

    viewer = viewer if viewer is not None else threads.actor_viewer(conn, person)
    if viewer is None:
        return False
    if chat is not None:
        return threads.thread_readable(conn, viewer, chat) and threads.message_readable(
            conn, viewer, message, thread_checked=True
        )
    return threads.message_readable(conn, viewer, message)


def _addressee(
    conn: sqlite3.Connection, actor: str, person: str, message: Row | None, task_id: str, *, checked: bool = True
) -> str:
    """The person a ❓ (or its Give to) names, refused with a clear reason when it may not be: someone off the roster,
    out of the bot's reach (`checked`: the bot's own pick), or unable to read the chat (`message`; None before it is
    posted, when the task's own check stands for it)."""
    target = str(H._reach(conn, actor, person))
    if not H.is_human(target):
        H.refuse(conn, actor, "reach", f"{H.actor_name(conn, target)} is a bot: a ❓ is for a person's reply")
    task = None
    if task_id:
        from . import task_engine as _task_engine

        E = cast(Any, _task_engine)

        task = E.task(conn, task_id)
    if checked and H.is_bot(actor):
        from types import SimpleNamespace

        from . import reach as _reach

        reach = cast(Any, _reach)

        who = SimpleNamespace(role="bot", actor=actor)
        if task_id:
            reach.check(conn, who, target, "task", task_id=task_id)
        else:
            reach.check(conn, who, target, "chat", conversation_id=(message or {}).get("conversation_id"))
    if task is not None:
        from . import task_calls as _task_calls

        task_calls = cast(Any, _task_calls)

        task_calls.bring_in(conn, actor, task, target)
    if message is not None and not readable(conn, target, message):
        H.refuse(
            conn,
            actor,
            "private",
            f"{H.actor_name(conn, target)} can't read this chat, so they can't reply: add them to it first",
        )
    return target


APPROVAL_FIRST = (
    "{name} already has \"{page}\" to approve on this task; that approval is your ask. Put what you need them to know in "
    "the page or the task note, and wait for their OK."
)


def approval_of(conn: sqlite3.Connection, row: Row, person: str) -> str | None:
    """The name of the stage's page a person signs off that waits on `person` on this task now (their approval card in
    Needs you), or None. Approvals win over questions: one card per person per task, so a bot's ❓ to them is refused
    while it waits, whatever its words say."""
    from . import task_requirements as _task_requirements
    from . import waiting_on as _waiting_on

    R, W = cast(Any, _task_requirements), cast(Any, _waiting_on)
    pages = {
        str(r["id"]): r
        for r in R.for_visit(conn, row["visit"])
        if r["kind"] == "page" and r.get("stage_id") and (R.people_only(r) or R.named(r))
    }
    if not pages:
        return None
    for wait in W.of(conn, row):
        requirement = pages.get(str(wait.requirement_id or ""))
        if requirement and wait.state == W.PERSON and wait.ask and wait.who == person:
            return str(R.document_of(requirement) or R.name_of(requirement))
    return None


def _check(
    conn: sqlite3.Connection, bot: str, task_id: str, person: str, body: str, message: Row | None, *, checked: bool
) -> str:
    """Everything a ❓ must pass, before or after its message is posted (`message` None: before). Returns the person."""
    if not H.is_bot(bot) or bot == H.TICO_ACTOR:
        H.refuse(conn, bot, "identity", "Only a bot marks a message ❓")
    row = None
    if task_id:
        from . import task_engine as _task_engine

        engine = cast(Any, _task_engine)

        status = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not status or status[0] != "open":
            H.refuse(conn, bot, "identity", "The task has ended: a ❓ is for an open task's chat")
        row = engine.task(conn, task_id)
    target = _addressee(conn, bot, person, message, task_id, checked=checked)
    shape = ask_shape.line_refusal(body) if checked else None
    if shape:
        H.refuse(conn, bot, "lint", shape)
    if checked and row:
        from . import task_engine as _task_engine

        engine = cast(Any, _task_engine)
        from . import task_requirements as _task_requirements

        R = cast(Any, _task_requirements)

        held = approval_of(conn, row, target) if row.get("visit") else None
        if held:
            detail = APPROVAL_FIRST.format(name=H.actor_name(conn, target), page=held)
            engine._refuse(conn, bot, "task_requirement", detail, task_id)
        page = engine.people_page(conn, row, [body], question=True) if row.get("visit") else None
        if page:
            engine._refuse(conn, bot, "task_requirement", f"{page} is a person's OK already. {R.PAGE_OK_HINT}", task_id)
        from . import validate_gate as _validate_gate

        cast(Any, _validate_gate).check_question(conn, row, bot, target)
    return target


def check_asking(conn: sqlite3.Connection, bot: str, task_id: str, person: str, body: str) -> str:
    """Everything a bot's ❓ on a task must pass before its message is written (`_check`): run before the rule check's
    verdict is applied, so a person who may not be asked (one who cannot read a private task) is the error the bot
    reads, not a rule's. Returns the person."""
    return _check(conn, bot, task_id, person, body, None, checked=True)


def mark(conn: sqlite3.Connection, bot: str, message_id: str, person: str, *, checked: bool = True) -> Row:
    """Mark the bot's own message ❓ for `person`, replacing the bot's open ❓ in that chat. Returns the mark, with
    `replaced` the open ❓ it cleared ({id, line, created_at}) or None.
    `checked` False: a question Tico's own feature words and addresses for the bot (`ask`), so neither the bot's reach
    nor the words' shape is the bot's to answer for; the person must still be able to read the chat."""
    if not H.is_bot(bot) or bot == H.TICO_ACTOR:
        H.refuse(conn, bot, "identity", "Only a bot marks a message ❓")
    message = H.message(conn, message_id)
    if not message or message.get("sender_id") != bot:
        H.refuse(conn, bot, "identity", "A bot marks only its own message ❓")
    assert message is not None
    conv = H.conversation(conn, message["conversation_id"])
    if not conv:
        H.refuse(conn, bot, "identity", "That message has no chat")
    task_id = _message_task(conn, message, conv)
    # A Warn rule's line the rule check put before the bot's words (org_rule_check.apply) is not the question: the ❓
    # is checked and listed by the bot's own words, so a rule that only warns never stops the question.
    from . import org_rule_check as _org_rule_check

    body = str(cast(Any, _org_rule_check).unwarned(str(message.get("body") or "")))
    target = _check(conn, bot, task_id, person, body, message, checked=checked)
    line = first_line(body)
    room = room_of(conv)
    at = H.now()
    before = conn.execute(
        "SELECT id, line, created_at FROM question_marks WHERE room_id=? AND bot_id=? AND cleared_at IS NULL",
        (room, bot),
    ).fetchone()
    replaced: Row | None = dict(before) if before else None
    conn.execute(
        "UPDATE question_marks SET cleared_at=?, cleared_reason='replaced', cleared_by_id=? "
        "WHERE room_id=? AND bot_id=? AND cleared_at IS NULL",
        (at, bot, room, bot),
    )
    row = {
        "id": H.new_id(),
        "message_id": message["id"],
        "conversation_id": message["conversation_id"],
        "room_id": room,
        "task_id": task_id,
        "bot_id": bot,
        "person_id": target,
        "line": line,
        "created_at": at,
    }
    conn.execute(
        "INSERT INTO question_marks (id, message_id, conversation_id, room_id, task_id, bot_id, person_id, line, "
        "created_at) VALUES (:id, :message_id, :conversation_id, :room_id, :task_id, :bot_id, :person_id, :line, "
        ":created_at)",
        row,
    )
    H.event(conn, bot, "question.marked", row["id"], {"message": message["id"], "to": target, "task": task_id})
    H._recount(conn, bot)  # its status's "waits on a person" (hubdb.status_counts)
    return {**row, "cleared_at": None, "replaced": replaced}


def ask(
    conn: sqlite3.Connection,
    bot: str,
    task_id: str,
    person: str,
    text: str,
    *,
    checked: bool = True,
    let_in: bool | None = None,
    refs: Row | None = None,
) -> Row:
    """`bot` asks `person` on a task's chat: its message there, then the ❓ on it, in the caller's transaction. Returns
    the mark. `checked` False: Tico's own feature words the question and picks the person for the bot (person_asks, a
    rule, the ladder, Agent Ops's setup notices), so neither the bot's reach, its reading the task, nor the words' shape is
    the bot's to answer for. `let_in` (default: not `checked`): a person who may not read a private task is made a
    reader first, as an OK filed for a person is (task_asks.ask_ok); else they are refused. `refs`: more marks on the
    message (`shows`: a website's live browser the person takes over). Wakes nobody and notifies nobody: the ❓ is how
    the person hears, in their Needs you."""
    if (not checked) if let_in is None else let_in:
        row: Row | None = H.task(conn, task_id)
        if row and not H.task_private_readable(conn, person, row):
            H.task_reach(conn, bot, row, person)
    _check(conn, bot, task_id, person, text, None, checked=checked)
    said: Row = H.task_comment(conn, bot, task_id, text, wake=False, checked=checked, extra_refs=refs)
    return mark(conn, bot, str(said["id"]), person, checked=checked)


def ask_in_chat(conn: sqlite3.Connection, bot: str, person: str, text: str, *, checked: bool = True) -> Row:
    """`bot` asks `person` in its direct chat with them (no task): the message, then the ❓ on it. Returns the mark."""
    said: Row = H.say(conn, bot, person, text)
    return mark(conn, bot, str(said["id"]), person, checked=checked)


def give(conn: sqlite3.Connection, mid: str, to: str, by: str) -> Row:
    """Give to: move an open ❓ to another person. Its addressee or the bot that marked it may."""
    found = get(conn, mid)
    if not found or found.get("cleared_at"):
        H.refuse(conn, by, "identity", "That ❓ is not open")
    assert found is not None
    if by not in (found["person_id"], found["bot_id"]):
        H.refuse(conn, by, "identity", "Only the person it is for, or the bot that asked, gives a ❓ to someone else")
    message: Row = H.message(conn, found["message_id"]) or {}
    target = _addressee(conn, found["bot_id"], to, message, found["task_id"])
    if target == found["person_id"]:
        return found
    at = H.now()
    conn.execute(
        "UPDATE question_marks SET person_id=?, given_by_id=?, given_at=? WHERE id=?", (target, by, at, found["id"])
    )
    H.event(conn, by, "question.given", found["id"], {"from": found["person_id"], "to": target})
    return {**found, "person_id": target, "given_by_id": by, "given_at": at}


def replied(conn: sqlite3.Connection, message: Mapping[str, Any], conv: Mapping[str, Any], refs: object) -> list[str]:
    """A person's message just written: it clears each open ❓ for them in that chat. Their own hand, their token or
    their Assistant on their word (`REPLY_VIA`); Agent Ops or an automation writing as them do not. A reply counts as
    progress on the task (task_engine.progress_mark). Returns the tasks of the marks it cleared, each once ('' for a
    direct chat's): a shared chat can hold marks for several tasks, and the reply itself need not name any."""
    sender = str(message.get("sender_id") or "")
    if not H.is_human(sender) or message.get("kind") == "notice":
        return []
    meta: Any = H._json(refs, {}) or {}
    via: Any = cast(Row, meta).get("via") if refs and isinstance(meta, dict) else None
    if via not in REPLY_VIA:
        return []
    room = room_of(conv)
    found = conn.execute(
        "SELECT id, bot_id, task_id FROM question_marks WHERE room_id=? AND person_id=? AND cleared_at IS NULL",
        (room, sender),
    ).fetchall()
    if not found:
        return []
    ids = [str(r[0]) for r in found]
    conn.execute(
        "UPDATE question_marks SET cleared_at=?, cleared_reason='replied', cleared_by_id=?, cleared_message_id=?, "
        "cleared_via=? WHERE id IN (SELECT value FROM json_each(?))",
        (message.get("created_at") or H.now(), sender, message["id"], via or None, H._dump(ids)),
    )
    for bot in sorted({str(r[1]) for r in found}):
        H._recount(conn, bot)
    return sorted({str(r[2] or "") for r in found})


def approval_answered(conn: sqlite3.Connection, task_id: object, person: str, answer_id: str) -> list[str]:
    """A person OK'd or sent back a stage's page on a task: that approval was the ask, so each open ❓ to them on the
    task clears with it (`cleared_reason` 'approval'), as a reply clears one. Not a reply: no `cleared_message_id`, so it
    is not counted as one (task_engine.progress_mark); the answer itself is the task's progress. Returns the marks'
    ids."""
    if not task_id or not H.is_human(person):
        return []
    found = conn.execute(
        "SELECT id, bot_id FROM question_marks WHERE task_id=? AND person_id=? AND cleared_at IS NULL",
        (str(task_id), person),
    ).fetchall()
    if not found:
        return []
    ids = [str(r[0]) for r in found]
    conn.execute(
        "UPDATE question_marks SET cleared_at=?, cleared_reason='approval', cleared_by_id=? "
        "WHERE id IN (SELECT value FROM json_each(?)) AND cleared_at IS NULL",
        (H.now(), person, H._dump(ids)),
    )
    for bot in sorted({str(r[1]) for r in found}):
        H._recount(conn, bot)
    for mid in ids:
        H.event(conn, person, "question.cleared", mid, {"reason": "approval", "answer": answer_id})
    return ids


def after_reply(conn: sqlite3.Connection, task_id: object, by: str, why: str = "Replied") -> None:
    """A reply cleared a ❓ on a task's chat (or its person can no longer answer it): it is progress, so the
    never-stuck ladder starts again; a bot's snooze of the task waited for it and ends; a merge conflict held for it
    goes to its builder now."""
    if not task_id:
        return
    from . import pr_conflicts as _pr_conflicts

    pr_conflicts = cast(Any, _pr_conflicts)
    from . import task_engine as _task_engine

    E = cast(Any, _task_engine)

    tid = str(task_id)
    row: Row = E.task(conn, tid) or {}
    snoozed: Any = row.get("snooze")
    if snoozed and H.is_bot(snoozed["by_id"]):
        E.unsnooze(conn, tid, by, why, consequence="reply")
    E.changed(conn, tid, by)
    pr_conflicts.after_answer(conn, tid)


def end_tasks(conn: sqlite3.Connection, task_ids: Iterable[str], reason: str, by: str | None = None) -> None:
    """The tasks ended outside a status change (a merge, a delete): their open ❓ clear. Done and closed clear through
    the migration's trigger."""
    ids = sorted({str(t) for t in task_ids if t})
    if not ids:
        return
    conn.execute(
        "UPDATE question_marks SET cleared_at=?, cleared_reason=?, cleared_by_id=? "
        "WHERE task_id IN (SELECT value FROM json_each(?)) AND cleared_at IS NULL",
        (H.now(), reason, by, H._dump(ids)),
    )


def snoozes(conn: sqlite3.Connection, *people: str) -> dict[tuple[str, str], str]:
    """{(person, mark id): snoozed until} of the ❓ people snoozed for themselves, while the time is to come."""
    sql = "SELECT person_id, requirement_id, snoozed_until FROM ask_snoozes WHERE visit_id=? AND snoozed_until > ?"
    args: list[Any] = [SNOOZE_VISIT, H.now()]
    if people:
        sql += " AND person_id IN (%s)" % ",".join("?" * len(people))
        args += list(people)
    return {(str(r[0]), str(r[1])): str(r[2]) for r in conn.execute(sql, args)}


def item(mark: Mapping[str, Any], until: str | None = None) -> Row:
    """A ❓ as waiting_on.needs lists it."""
    return {
        "kind": "question",
        "id": wait_id(mark),
        "mark_id": mark["id"],
        "task_id": mark["task_id"] or None,
        "message_id": mark["message_id"],
        "conversation_id": mark["conversation_id"],
        "room_id": mark["room_id"],
        "bot_id": mark["bot_id"],
        "requirement_id": None,
        "line": mark["line"],
        "missing": [mark["line"]],
        "since": mark["created_at"],
        **({"snoozed_until": until} if until else {}),
    }


def items(
    conn: sqlite3.Connection, only: Any, snoozed: str = "hide", people: Iterable[str] = ()
) -> dict[str, list[Row]]:
    """Each person's open ❓ as Needs-you items, for the people in `only`: a task a person snoozed sets its ❓ aside, as
    it does the task's asks, and a ❓ a person snoozed for themselves is out until its time (`snoozed` as in
    waiting_on._lists)."""
    rows = [m for m in open_rows(conn) if m["person_id"] in only]
    if not rows:
        return {}
    held = snoozes(conn, *people)
    set_aside = _snoozed_by_people(conn, {m["task_id"] for m in rows if m["task_id"]})
    out: dict[str, list[Row]] = {}
    for m in rows:
        if m["task_id"] in set_aside:
            continue
        until = held.get((m["person_id"], m["id"]))
        if (snoozed == "hide" and until) or (snoozed == "only" and not until):
            continue
        out.setdefault(m["person_id"], []).append(item(m, until))
    return out


def _snoozed_by_people(conn: sqlite3.Connection, task_ids: set[str]) -> set[str]:
    if not task_ids:
        return set()
    return {
        str(r[0])
        for r in conn.execute(
            "SELECT task_id FROM snoozes WHERE task_id IN (SELECT value FROM json_each(?)) AND ended_at IS NULL "
            "AND by_id LIKE 'human:%' AND until_at > ?",
            (H._dump(sorted(task_ids)), H.now()),
        )
    }
