# pyright: strict
"""A ❓: a mark a bot puts on its own chat message, for one named person. It means "needs your reply", nothing more: no
task is filed behind it and nothing is sent for it (no notice, no push, no mail). Needs you lists it, and the bot is not
chased while it waits on its own open ❓ (waiting_on.of).

- Only a bot marks, and only its own message. Tico itself never authors one.
- The person must be on the roster, in the bot's reach (as an ask is), and able to read the message.
- The message's first line passes ask_shape.line_refusal, as any ask to a person does.
- On a task, a bot's ❓ does not repeat what the stage's people-only page asks already (its message naming that page:
  the person OKs the page there), and in Product's Validate it is refused (validate_gate.check_question).
- `ask` runs every check before it posts the message, so a refused ❓ leaves nothing in the chat.
- One open ❓ per bot per chat (the chat's root conversation, `room_id`): a new one clears the old ("replaced").

It clears when the person replies in that chat, in their own hand or by their Assistant on their word (`replied`, from
hubdb._write_message: `cleared_via` keeps which); when its task ends (done or closed: the migration's trigger; merged:
task_merge; deleted: task_engine.on_delete); or when the bot marks a newer message in the chat. Give to moves an open
one to another person (`give`).

The open mark is mirrored on its message as refs.question {id, to} (the migration's triggers), so every chat renderer
draws its glyph from the message alone and a change reaches open pages through the messages trigger.
"""

import importlib
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any, cast

from . import ask_shape
from . import hubdb as _hubdb

H: Any = _hubdb  # untyped module, through Any as in ask_quality.py

Row = dict[str, Any]


def _mod(name: str) -> Any:
    """An untyped module of the backend, imported where it is used (several of them import this one)."""
    return importlib.import_module("." + name, __package__)


PREFIX = "qmark:"  # a ❓'s wait and Needs-you item id: "qmark:<mark id>"
SNOOZE_VISIT = "qmark"  # ask_snoozes.visit_id of a snoozed ❓; its requirement_id is the mark's id
LINE_MAX = 160
# Who may clear a ❓ by writing in a person's name: their own hand (Slack included, refs.via "slack"), their personal
# token, and their Assistant on their word. BotOps and an automation writing as them do not answer for them.
REPLY_VIA = (None, "", "slack", "token", "assistant")

_OPEN = "SELECT * FROM question_marks WHERE cleared_at IS NULL ORDER BY created_at, id"


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
    """A mark as a route answers with it."""
    keys = ("id", "message_id", "conversation_id", "task_id", "bot_id", "person_id", "line", "created_at")
    return {k: (mark.get(k) or None) if k == "task_id" else mark.get(k) for k in keys}


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
    read_cache = _mod("read_cache")

    def build() -> list[Row]:
        rows = [dict(r) for r in conn.execute(_OPEN)]
        if not rows:
            return []
        # Their messages, chats and tasks in a few reads for all of them, never a few per mark (a never-stuck pass).
        stored = H.prime_messages(conn, {m["message_id"] for m in rows})
        _mod("task_privacy").prime_messages(conn, stored.values())
        H.prime_tasks(conn, {m["task_id"] for m in rows if m["task_id"]}, with_tags=False)
        chats = chat_rows(conn, {m["conversation_id"] for m in stored.values()})
        return [m for m in rows if answerable(conn, m, chats)]

    return read_cache.value(conn, ("question_marks_open",), build, shared=True)


def chat_rows(conn: sqlite3.Connection, ids: Iterable[object]) -> dict[str, Row]:
    """These chats' rows by id, in one read: for `readable` over many marks."""
    wanted = sorted({str(i) for i in ids if i})
    if not wanted:
        return {}
    return {
        str(r["id"]): dict(r)
        for r in conn.execute(
            "SELECT * FROM conversations WHERE id IN (SELECT value FROM json_each(?))", (H._dump(wanted),)
        )
    }


def answerable(conn: sqlite3.Connection, mark: Mapping[str, Any], chats: Mapping[str, Row] | None = None) -> bool:
    """Whether the person an open ❓ names can still answer it: still here and able to act (task_requirements
    .active_person), and able to read the marked message. `chats` holds chat rows already read, by id."""
    R = _mod("task_requirements")

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
        (at, H.KEEPER, H._dump([m["id"] for m in gone])),
    )
    for bot in sorted({str(m["bot_id"]) for m in gone}):
        H._recount(conn, bot)
    for m in gone:
        H.event(conn, H.KEEPER, "question.cleared", m["id"], {"reason": "addressee_gone", "to": m["person_id"]})
        if m["task_id"]:
            after_reply(conn, m["task_id"], H.KEEPER, "Its question can't be answered")
            _tell_gone(conn, m)
    return [str(m["id"]) for m in gone]


def _tell_gone(conn: sqlite3.Connection, mark: Row) -> None:
    """Wake the bot on the task: its ❓ went nowhere, so it asks someone who can, or goes on without the answer."""
    E = _mod("task_engine")
    task_wakes = _mod("task_wakes")

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


def on_task(conn: sqlite3.Connection, task_id: object) -> list[Row]:
    return [m for m in open_rows(conn) if m["task_id"] and m["task_id"] == str(task_id)]


REPLIED_SQL = "SELECT task_id, COUNT(*) FROM question_marks WHERE cleared_message_id IS NOT NULL AND task_id <> '' GROUP BY task_id"


def replies(conn: sqlite3.Connection, task_id: object) -> int:
    """How many ❓ on the task a person replied to (task_engine.progress_mark): one read per snapshot for every task
    (read_cache; a write reads again), so a never-stuck pass over many tasks never reads them one task at a time."""
    read_cache = _mod("read_cache")

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
    threads = _mod("threads")

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
        E = _mod("task_engine")

        task = E.task(conn, task_id)
    if checked and H.is_bot(actor):
        from types import SimpleNamespace

        reach = _mod("reach")

        who = SimpleNamespace(role="bot", actor=actor)
        if task_id:
            reach.check(conn, who, target, "task", task_id=task_id)
        else:
            reach.check(conn, who, target, "chat", conversation_id=(message or {}).get("conversation_id"))
    if task is not None:
        task_calls = _mod("task_calls")

        task_calls.bring_in(conn, actor, task, target)
    if message is not None and not readable(conn, target, message):
        H.refuse(
            conn,
            actor,
            "private",
            f"{H.actor_name(conn, target)} can't read this chat, so they can't reply: add them to it first",
        )
    return target


def _check(
    conn: sqlite3.Connection, bot: str, task_id: str, person: str, body: str, message: Row | None, *, checked: bool
) -> str:
    """Everything a ❓ must pass, before or after its message is posted (`message` None: before). Returns the person."""
    if not H.is_bot(bot) or bot == H.KEEPER:
        H.refuse(conn, bot, "identity", "Only a bot marks a message ❓")
    row = None
    if task_id:
        engine = _mod("task_engine")

        status = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not status or status[0] != "open":
            H.refuse(conn, bot, "identity", "The task has ended: a ❓ is for an open task's chat")
        row = engine.task(conn, task_id)
    target = _addressee(conn, bot, person, message, task_id, checked=checked)
    shape = ask_shape.line_refusal(body) if checked else None
    if shape:
        H.refuse(conn, bot, "lint", shape)
    if checked and row:
        engine = _mod("task_engine")
        R = _mod("task_requirements")

        page = engine.people_page(conn, row, [body], question=True) if row.get("visit") else None
        if page:
            engine._refuse(conn, bot, "task_requirement", f"{page} is a person's OK already. {R.PAGE_OK_HINT}", task_id)
        _mod("validate_gate").check_question(conn, row, bot, target)
    return target


def mark(conn: sqlite3.Connection, bot: str, message_id: str, person: str, *, checked: bool = True) -> Row:
    """Mark the bot's own message ❓ for `person`, replacing the bot's open ❓ in that chat. Returns the mark.
    `checked` False: a question Tico's own feature words and addresses for the bot (`ask`), so neither the bot's reach
    nor the words' shape is the bot's to answer for; the person must still be able to read the chat."""
    if not H.is_bot(bot) or bot == H.KEEPER:
        H.refuse(conn, bot, "identity", "Only a bot marks a message ❓")
    message = H.message(conn, message_id)
    if not message or message.get("sender_id") != bot:
        H.refuse(conn, bot, "identity", "A bot marks only its own message ❓")
    assert message is not None
    conv = H.conversation(conn, message["conversation_id"])
    if not conv:
        H.refuse(conn, bot, "identity", "That message has no chat")
    task_id = _message_task(conn, message, conv)
    target = _check(conn, bot, task_id, person, str(message.get("body") or ""), message, checked=checked)
    line = first_line(message.get("body"))
    room = room_of(conv)
    at = H.now()
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
    return {**row, "cleared_at": None}


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
    rule, the ladder, BotOps's setup notices), so neither the bot's reach, its reading the task, nor the words' shape is
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
    their Assistant on their word (`REPLY_VIA`); BotOps or an automation writing as them do not. A reply counts as
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


def after_reply(conn: sqlite3.Connection, task_id: object, by: str, why: str = "Replied") -> None:
    """A reply cleared a ❓ on a task's chat (or its person can no longer answer it): it is progress, so the
    never-stuck ladder starts again; a bot's snooze of the task waited for it and ends; a merge conflict held for it
    goes to its builder now."""
    if not task_id:
        return
    pr_conflicts = _mod("pr_conflicts")
    E = _mod("task_engine")

    tid = str(task_id)
    row: Row = E.task(conn, tid) or {}
    snoozed: Any = row.get("snooze")
    if snoozed and H.is_bot(snoozed["by_id"]):
        E.unsnooze(conn, tid, by, why)
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
