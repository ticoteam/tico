# pyright: strict
"""A Warn rule's break, answered (Spec "Rule warning asks Block or Don't block", 2026-11-26): a rule set to warn is a
rule still deciding between block and not, so each break of a bot's write asks a person "Yes, block" or "Don't block",
and the answer is saved on the rule. The stored setting stays `warn`; its label is "Ask: block or not".

- Saved: one row per Warn rule a bot's write broke (`record`, from org_rule_check.apply's warn paths, before or after the
  write), with the bot, who it went to, the write's first line and the date. A check that could not run saves none.
  The write's route links the rows to the message it saved (`link`), or to its task (`link_task`, an ask for an OK has
  no message of its own); a mail or a Slack post keeps its rows on the rule alone, answered from the Rules page.
- Edited (`retire`, from threads.edit_message): a message's unanswered breaks go, off it and off the rule; an answered
  one stays on the rule and leaves the message (`retired`), still read by that message's readers only. The edit is
  checked, and asked about, on its own. An unchanged edit is no edit: nothing is checked or saved.
- Never posted (`unposted`): a reply the turn's completion did not post (already sent, refused, a full queue) keeps no
  break, so no first line is kept without the message whose readers it belongs to.
- Read: a break's first line, note and where it was show only to a reader who may read that message now, as the chat
  reader checks it (the chat's members, then the message's own task and privacy), else its chat and task. Anyone
  else who may list the rule's breaks sees the answer and the date, and the break still counts in the tally.
- Answered (`answer`): by a person it went to, or by an Admin or an editor of the rule (`org_rules.may_manage`), never
  a bot. One answer per break: a second is refused with who gave the first.
  - "Don't block" (`pass`): saved; the message stays and the bot is not told.
  - "Yes, block" (`block`): saved; the message's ❓ leaves its person's Needs you (cleared as `rule_block`), and the
    bot is woken on the same task, or in the same chat when there is none, with the rule, its message's first line and
    the person's note. The screens fold the message away. A bot that can no longer read the private task is told
    nothing, by no other way either: the break records that it was not told (`bot_told` 0).
- Undone (`undo`) by whoever answered, within UNDO_HOURS: the answer is removed and the question shows again. Feedback
  the bot already had is not taken back, and a cleared ❓ stays cleared.
- The bot is told about a break once: the person's "Yes, block". org_rule_check sends no note of its own for a break
  saved here.
- Off switch: SWITCH=off is today's warning for every break, old and new: no new break is saved, no question shows on
  any message, and answers and undos are refused (409). Answers already given stay on the rule's list and tally, read
  only, and a message a "Yes, block" folded stays folded. On again, the unanswered questions show again.
"""

import json
import os
import sqlite3
from contextlib import AbstractContextManager
from typing import Any, cast

from fastapi import Request
from pydantic import Field

from . import api_routing
from .api_contract import api_v2
from .deps import Injected
from .models import Contract
from .store import H as hubdb, Problem

H: Any = hubdb  # untyped module, through Any as in question_marks.py
Row = dict[str, Any]

SWITCH = "TICO_RULE_BREAK_ASK"
ANSWERS = ("block", "pass")
UNDO_HOURS = 24
LIST_DAYS = 30
LIST_MAX = 100
LINE_MAX = 300
NOTE_MAX = 1000
NOT_YOURS = "Only the person it went to, an Admin or the rule's editors can answer this"
NOT_YOUR_UNDO = "Only the person who answered can undo it"
TOO_LATE = "An answer can be undone for 24 hours only"
NOT_ANSWERED = "This has no answer to undo"
SWITCHED_OFF = "Rule questions are switched off"


def on() -> bool:
    return os.environ.get(SWITCH, "on").strip().lower() != "off"


def first_line(text: object) -> str:
    """The write's own first line, without a warning line put before it."""
    from . import org_rule_check as _org_rule_check

    checks: Any = _org_rule_check
    body: str = checks.unwarned(text if isinstance(text, str) else "")
    for line in body.splitlines():
        if line.strip():
            return line.strip()[:LINE_MAX]
    return ""


def record(c: Any, verdict: Row | None, rules: list[Row], text: object) -> list[str]:
    """One saved break per Warn rule a bot's write broke. Returns their ids (the gate's `breaks`). Nothing for a
    person's own write, or with the switch off."""
    if not verdict or not rules or not on():
        return []
    author = str(verdict.get("author") or "")
    if not H.is_bot(author):
        return []
    sent: list[Any] = list(verdict.get("recipients") or [])
    people = sorted({str(r) for r in sent if H.is_human(str(r))})
    line, at, ids = first_line(text), H.now(), list[str]()
    for rule in rules:
        bid = H.new_id()
        c.execute(
            "INSERT INTO org_rule_breaks(id, rule_id, author, operation, recipients, first_line, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (bid, rule["id"], author, str(verdict.get("operation") or ""), json.dumps(people), line, at),
        )
        ids.append(bid)
    return ids


def link(c: Any, gate: Row | None, *, message: str = "", task: str = "") -> None:
    """The message (and its task) the breaks a write's gate saved are on."""
    ids: list[str] = list(gate.get("breaks") or []) if gate else []
    if not ids or not message:
        return
    found = c.execute("SELECT conversation_id FROM messages WHERE id=?", (message,)).fetchone()
    conversation = found[0] if found else ""
    if not task and conversation:  # a post on a task's own chat that named no task is still on that task
        owner = c.execute("SELECT id FROM tasks WHERE conversation_id=?", (conversation,)).fetchone()
        task = str(owner[0]) if owner else ""
    c.execute(
        "UPDATE org_rule_breaks SET message_id=?, task_id=?, conversation_id=? "
        "WHERE id IN (SELECT value FROM json_each(?))",
        (message, task or "", conversation or "", json.dumps(ids)),
    )


def link_task(c: Any, gate: Row | None, task: str) -> None:
    """The task a task write's breaks are on (an ask for an OK has no message of its own). A message the write saved
    is linked after, by its route (`link`)."""
    ids: list[str] = list(gate.get("breaks") or []) if gate else []
    if ids and task:
        c.execute(
            "UPDATE org_rule_breaks SET task_id=? WHERE task_id='' AND id IN (SELECT value FROM json_each(?))",
            (task, json.dumps(ids)),
        )


def drop(c: Any, gate: Row | None) -> None:
    """A gate's breaks taken back unasked: a late result for words edited or deleted since saves nothing."""
    ids: list[str] = list(gate.get("breaks") or []) if gate else []
    if ids:
        c.execute(
            "DELETE FROM org_rule_breaks WHERE answer IS NULL AND id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )


def unposted(c: Any, gate: Row | None) -> None:
    """A gate's breaks for a reply that was never posted (no message linked): they go, unasked."""
    ids: list[str] = list(gate.get("breaks") or []) if gate else []
    if ids:
        c.execute(
            "DELETE FROM org_rule_breaks WHERE answer IS NULL AND message_id='' AND id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )


def retire(c: Any, message: str) -> None:
    """An edited message's breaks: unanswered ones go, off it and off the rule; an answered one stays on the rule (its
    tally) and leaves the message (`retired`): it keeps the message's id, so only that message's readers read its
    words. The edit's own breaks are linked after this, by the edit's route."""
    if not message:
        return
    c.execute("DELETE FROM org_rule_breaks WHERE message_id=? AND answer IS NULL AND retired=0", (message,))
    c.execute("UPDATE org_rule_breaks SET retired=1 WHERE message_id=? AND retired=0", (message,))


def readable(c: Any, auth: Any, who: Any, row: Row) -> bool:
    """Whether `who` may read the write a break is about, now, as the chat reader checks it: its message (the chat's
    members first, then the message's task and privacy), else its chat and its task. A break on none of them (a mail,
    a Slack post) is the rule's own, read by whoever may list the rule's breaks."""
    from . import task_privacy as _task_privacy
    from . import threads as _threads

    P: Any = _task_privacy
    T: Any = _threads
    v: Any = T.viewer(auth, who)
    if row.get("message_id"):
        return bool(T.readable(c, v, str(row["message_id"])))
    if row.get("conversation_id") and not T.readable(c, v, str(row["conversation_id"])):
        return False
    if row.get("task_id"):
        task = H.task(c, row["task_id"])
        return bool(task) and bool(P.task_readable(c, who, task))
    return True


def _row(c: Any, break_id: str) -> Row:
    found = c.execute(
        "SELECT b.*, r.text AS rule_text, r.who AS rule_who FROM org_rule_breaks b "
        "LEFT JOIN org_rules r ON r.id=b.rule_id WHERE b.id=?",
        (break_id,),
    ).fetchone()
    if not found:
        raise Problem("not_found", "No such rule break", 404)
    return dict(found)


def _asked(c: Any, row: Row) -> set[str]:
    """The people it went to, and the person its ❓ waits on (given to someone else since, they answer it)."""
    people: set[str] = set(json.loads(row.get("recipients") or "[]"))
    if row.get("message_id") and not row.get("retired"):
        people |= {
            r[0] for r in c.execute("SELECT person_id FROM question_marks WHERE message_id=?", (row["message_id"],))
        }
    return people


def may_answer(c: Any, auth: Any, who: Any, row: Row, ctx: Any = None) -> bool:
    if getattr(who, "role", "") == "bot":
        return False
    if getattr(who, "actor", "") in _asked(c, row):
        return readable(c, auth, who, row)  # a person who lost the message since does not answer it
    if auth is None or row.get("rule_who") is None:
        return False
    from . import org_rules as _org_rules

    rules: Any = _org_rules
    manages: Any = rules.may_manage
    return bool(manages(c, auth, who, json.loads(row["rule_who"] or "{}"), ctx))


def _name(c: Any, actor: object) -> str:
    return str(H.actor_name(c, actor) or H.actor_id(actor))


def view(c: Any, auth: Any, who: Any, row: Row, ctx: Any = None, shown: bool | None = None) -> Row:
    """One break as its reader reads it. Its first line, note and where it was only for a reader of that write now
    (`shown`: the caller already knows, as the timeline does for the messages it shows)."""
    seen = readable(c, auth, who, row) if shown is None else shown
    undoable = (
        on()
        and row.get("answered_by") == getattr(who, "actor", None)
        and bool(row.get("answered_at"))
        and row["answered_at"] > H.shift(H.now(), hours=-UNDO_HOURS)
    )
    return {
        "id": row["id"],
        "rule_id": row["rule_id"],
        "rule_text": row.get("rule_text") or "",
        "bot": row["author"],
        "bot_name": _name(c, row["author"]),
        "first_line": row["first_line"] if seen else "",
        "message_id": (row["message_id"] or None) if seen else None,
        "task_id": (row["task_id"] or None) if seen else None,
        "conversation_id": (row["conversation_id"] or None) if seen else None,
        "created_at": row["created_at"],
        "answer": row["answer"],
        "answered_by": row["answered_by"],
        "answered_by_name": _name(c, row["answered_by"]) if row.get("answered_by") else None,
        "answered_at": row["answered_at"],
        "note": row["note"] if seen else None,
        "bot_told": None if row.get("bot_told") is None else bool(row["bot_told"]),
        "may_answer": on() and not row["answer"] and may_answer(c, auth, who, row, ctx),
        "may_undo": undoable,
    }


def answer(c: Any, auth: Any, who: Any, break_id: str, value: str, note: str | None = None) -> Row:
    """Save a person's answer on the break, then act on it: "Yes, block" takes the message back and tells the bot."""
    if value not in ANSWERS:
        raise Problem("validation", "Answer block or pass", 422)
    if not on():
        raise Problem("switched_off", SWITCHED_OFF, 409)
    row = _row(c, break_id)
    if not may_answer(c, auth, who, row):
        raise Problem("forbidden", NOT_YOURS, 403)
    note = (note.strip()[:NOTE_MAX] or None) if note else None
    at = H.now()
    done = c.execute(
        "UPDATE org_rule_breaks SET answer=?, answered_by=?, answered_at=?, note=? WHERE id=? AND answer IS NULL",
        (value, who.actor, at, note, break_id),
    ).rowcount
    if not done:
        raise Problem("answered", f"Already answered by {_name(c, _row(c, break_id)['answered_by'])}", 409)
    H.event(c, who.actor, "rules.break_answered", "org_rule:" + row["rule_id"], {"break": break_id, "answer": value})
    row = _row(c, break_id)
    if value == "block":
        _block(c, who, row)
    return {"break": view(c, auth, who, row)}


def undo(c: Any, auth: Any, who: Any, break_id: str) -> Row:
    if not on():
        raise Problem("switched_off", SWITCHED_OFF, 409)
    row = _row(c, break_id)
    if not row.get("answer"):
        raise Problem("not_answered", NOT_ANSWERED, 409)
    if row.get("answered_by") != who.actor:
        raise Problem("forbidden", NOT_YOUR_UNDO, 403)
    if row["answered_at"] <= H.shift(H.now(), hours=-UNDO_HOURS):
        raise Problem("too_late", TOO_LATE, 409)
    c.execute(
        "UPDATE org_rule_breaks SET answer=NULL, answered_by=NULL, answered_at=NULL, note=NULL, bot_told=NULL WHERE id=?",
        (break_id,),
    )
    H.event(c, who.actor, "rules.break_undone", "org_rule:" + row["rule_id"], {"break": break_id})
    return {"break": view(c, auth, who, _row(c, break_id))}


def feedback(c: Any, row: Row) -> str:
    said = f' Their note: "{row["note"]}".' if row.get("note") else ""
    return (
        f'{_name(c, row["answered_by"])} said your rule "{row.get("rule_text") or "a rule"}" would block your message '
        f'"{row["first_line"]}", so it was taken back.{said} Rewrite it so it keeps the rule, then send it again.'
    )


def _told(c: Any, row: Row, told: bool) -> None:
    c.execute("UPDATE org_rule_breaks SET bot_told=? WHERE id=?", (1 if told else 0, row["id"]))
    row["bot_told"] = 1 if told else 0


def _block(c: Any, who: Any, row: Row) -> None:
    """The message leaves its person's Needs you, and its bot is woken where it wrote, with the rule and the note. A bot
    that can no longer read the private task is not told, by no other way either (a chat message or a notice would
    carry the task's words out of it): the break records that it was not told."""
    if row.get("message_id") and not row.get("retired"):  # an edited message's ❓ is about its new words
        cleared = c.execute(
            "UPDATE question_marks SET cleared_at=?, cleared_reason='rule_block', cleared_by_id=? "
            "WHERE message_id=? AND cleared_at IS NULL",
            (H.now(), who.actor, row["message_id"]),
        ).rowcount
        if cleared:
            H._recount(c, row["author"])
    text = feedback(c, row)
    if row.get("task_id"):
        from . import task_engine as _task_engine
        from . import task_wakes as _task_wakes

        E: Any = _task_engine
        task_wakes: Any = _task_wakes

        task: Row | None = E.task(c, row["task_id"])
        if task and not H.task_private_readable(c, row["author"], task):
            _told(c, row, False)
            return
        if task and task.get("status") == "open" and task.get("visit"):
            visit: Row = task["visit"]
            woke = task_wakes.add(
                c,
                task,
                row["author"],
                visit,
                task_wakes.body(c, task, text, to=row["author"]),
                wake_key=f"{task['id']}:{visit['id']}:rule-block:{row['id']}:{row['answered_at']}",
            )
            _told(c, row, bool(woke))
            return
    where = row.get("conversation_id") or None
    if not where and row.get("task_id"):
        found = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
        where = found[0] if found else None
    refs: Row = {
        "needs_action": True,
        "rule_break": row["id"],
        **({"task": row["task_id"]} if row.get("task_id") else {}),
    }
    said = H.say(c, H.TICO_ACTOR, row["author"], text, conversation_id=where, kind="notice", refs=refs)
    _told(c, row, bool(said))


def for_messages(c: Any, auth: Any, who: Any, message_ids: list[str]) -> dict[str, list[Row]]:
    """{message id: its breaks as `who` reads them}, for the messages a page shows (so `who` reads them). Switched off,
    only a "Yes, block" stays on its message, read only, so the message it folded stays folded."""
    ids = [m for m in message_ids if m]
    if not ids:
        return {}
    rows: list[Any] = c.execute(
        "SELECT b.*, r.text AS rule_text, r.who AS rule_who FROM org_rule_breaks b "
        "LEFT JOIN org_rules r ON r.id=b.rule_id "
        "WHERE b.retired=0 AND b.message_id IN (SELECT value FROM json_each(?))"
        + ("" if on() else " AND b.answer='block'")
        + " ORDER BY b.created_at, b.rowid",
        (json.dumps(ids),),
    ).fetchall()
    out: dict[str, list[Row]] = {}
    for found in rows:
        row: Row = dict(found)
        out.setdefault(row["message_id"], []).append(view(c, auth, who, row, shown=True))
    return out


def of_rule(c: Any, auth: Any, who: Any, rule_id: str, limit: int = LIST_MAX) -> Row:
    """A rule's breaks of the last LIST_DAYS, newest first, and their tally. For its Admins and editors."""
    from . import org_rules as _org_rules

    R: Any = _org_rules
    rule: Row = R._get(c, rule_id)
    if not R.may_manage(c, auth, who, rule["who"]):
        raise Problem("forbidden", "Only an Admin or an editor of this rule reads its breaks", 403)
    since = H.shift(H.now(), days=-LIST_DAYS)
    tally: Any = c.execute(
        "SELECT count(*), sum(answer='block'), sum(answer='pass'), sum(answer IS NULL) FROM org_rule_breaks "
        "WHERE rule_id=? AND created_at>=?",
        (rule_id, since),
    ).fetchone()
    rows: list[Any] = c.execute(
        "SELECT b.*, r.text AS rule_text, r.who AS rule_who FROM org_rule_breaks b "
        "LEFT JOIN org_rules r ON r.id=b.rule_id WHERE b.rule_id=? AND b.created_at>=? "
        "ORDER BY b.created_at DESC, b.rowid DESC LIMIT ?",
        (rule_id, since, max(1, min(int(limit), LIST_MAX))),
    ).fetchall()
    return {
        "tally": {"breaks": tally[0] or 0, "block": tally[1] or 0, "pass": tally[2] or 0, "open": tally[3] or 0},
        "breaks": [view(c, auth, who, dict(r)) for r in rows],
    }


class BreakAnswer(Contract):
    answer: str = Field(max_length=10)
    note: str | None = Field(default=None, max_length=NOTE_MAX)


class BreakEmpty(Contract):
    pass


router = api_routing.router()


@router.get(
    "/api/v2/rules/{rule_id}/breaks",
    **api_v2(
        "Rules",
        "listRuleBreaks",
        "A Warn rule's breaks of the last 30 days, newest first, each with its answer, and their tally",
    ),
)
def rule_breaks(request: Request, rule_id: str, deps: Injected) -> Row:
    # Store.read is unannotated; it yields a sqlite3 connection (as browser_commands.py reads it).
    with cast(AbstractContextManager[sqlite3.Connection], deps.store.read()) as c:
        return of_rule(c, deps.auth, request.state.identity, rule_id)


@router.post(
    "/api/v2/rule-breaks/{break_id}/answer",
    **api_v2(
        "Rules",
        "answerRuleBreak",
        "Yes, block (block) or Don't block (pass) a Warn rule's break: by the person it went to, an Admin or an editor of the rule",
    ),
)
def rule_break_answer(request: Request, break_id: str, body: BreakAnswer, deps: Injected) -> Any:
    who = request.state.identity
    return deps.mutate(request, body, lambda c: answer(c, deps.auth, who, break_id, body.answer, body.note))  # pyright: ignore[reportUnknownLambdaType]


@router.post(
    "/api/v2/rule-breaks/{break_id}/undo",
    **api_v2("Rules", "undoRuleBreak", "Take back your answer to a rule's break, within 24 hours"),
)
def rule_break_undo(request: Request, break_id: str, body: BreakEmpty, deps: Injected) -> Any:
    who = request.state.identity
    return deps.mutate(request, body, lambda c: undo(c, deps.auth, who, break_id))  # pyright: ignore[reportUnknownLambdaType]
