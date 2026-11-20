# pyright: strict
"""Questions as tasks (docs/tasks.md, "Questions"). While the company switch `questions_as_tasks` is on, a bot's
question to a person is a task filed under the task it works on (the parent): owned by the person, asked for by the
bot, titled with the question's first line, described by the rest. A `questions` row makes a task a question and holds
only what answering it safely needs: the parent and its visit, the bot, the person, the round, an optional link, and
how it ended. Its status, owner, snooze and chat are the task's own.

- One writer (`ask`), for task_moves_routes.task_ask, task_comments_routes.question_write and person_asks.ask_on. It
  runs every check a ❓ runs (question_marks.check: reach, ask shape, an approval already waiting on the person,
  task_engine.people_page, validate_gate.check_question) and lets a person into a private parent as a ❓ does. The
  same bot asking the same person again on the same visit reopens its question with the next round: the new words are
  its message there and the old ones stay in its chat. Two bots asking are two questions.
- One answer (`answer`, POST /api/v2/tasks/{id}/question/answer only: a post in the question's chat is a comment):
  the person who owns it now, at the round they were shown, in one write. It records their words on the question and
  finishes it (never a push past: the task never leaves the stage it was filed in, `refuse_move`), restarts the
  parent's ladder and Late clock as an answered ❓ does, relays the words to the parent's chat quietly (Defer never,
  no @ wakes) where the parent's readers may read them (task_privacy.require_destination; else a link), and wakes
  at most one bot: whoever has the parent's turn once the answer is in.
- On the parent an open question is an open ❓ of its bot: the person's turn, never Late, the bot not chased and not
  ending the parent (waiting_on.of, late_rule, question_marks.refuse_ending). It is no filed task: its `links` row is
  removed as it is filed, so the filed-task holds and wakes (task_engine._ended, waiting_on._filed_open) never see it.
- Endings (`ended_reason`): answered; withdrawn (its bot closes it, or anyone else does); parent_ended (the parent is
  done, closed, merged or deleted); parent_moved (the parent left the visit it was asked on); asker_gone (the bot left
  or lost the parent: its holder is told); person_gone (the person left: the wait ends and the bot is told).
- Only new asks follow the switch. Questions already filed stay readable and answerable with it off.
"""

import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any, cast

from . import hubdb as _hubdb
from . import question_marks

H: Any = _hubdb  # untyped module, through Any as in question_marks.py

Row = dict[str, Any]

KEY = "questions_as_tasks"  # the company switch, in registry_metadata
PREFIX = "question:"  # a question's wait id on its parent: "question:<question task id>"
KIND = "question_task"  # its Needs-you item kind (a ❓'s is "question")
ANSWERED, WITHDRAWN = "answered", "withdrawn"
PARENT_ENDED, PARENT_MOVED = "parent_ended", "parent_moved"
ASKER_GONE, PERSON_GONE = "asker_gone", "person_gone"
LOOK_AGAIN = "Look again: this question changed since you opened it"
NOT_A_PERSON = "A question is for a person: hand it to someone with Give"
STAYS = "A question stays where it was asked: answer it, or close it"
STILL_OPEN = (
    'Your question to {name} is still open: "{title}". Finishing, moving on or closing the task would withdraw it '
    "unanswered. Wait for their answer, or withdraw it first: tico task close {question} -m <why>."
)
WHY = {
    WITHDRAWN: "Withdrawn",
    PARENT_ENDED: "The task it was asked on ended",
    PARENT_MOVED: "The task it was asked on moved on",
    ASKER_GONE: "Its asker can no longer work on the task",
    PERSON_GONE: "Its person can no longer answer it",
}


class QuestionError(Exception):
    """A question write that cannot go ahead: `code`, `detail` and the HTTP `status` the route answers with."""

    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def _engine() -> Any:
    from . import task_engine

    return task_engine


def enabled(conn: sqlite3.Connection) -> bool:
    """Whether new asks to a person are questions in this company (off unless turned on)."""
    found = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    return bool(found) and str(found[0]) == "true"


def set_enabled(conn: sqlite3.Connection, on: bool, by: str) -> None:
    conn.execute(
        "INSERT INTO registry_metadata(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, "true" if on else "false"),
    )
    H.event(conn, by, "questions.switch", KEY, {"on": on})


def touches(conn: sqlite3.Connection, task_ids: Iterable[object]) -> bool:
    """Whether any of these tasks is a question or was asked one: with the switch off, nothing else reads `questions`
    for them, so a company that never turned it on reads and writes as before."""
    ids = H._dump(sorted({str(t) for t in task_ids if t}))
    return (
        conn.execute(
            "SELECT 1 FROM questions WHERE task_id IN (SELECT value FROM json_each(?)) "
            "OR parent_id IN (SELECT value FROM json_each(?)) LIMIT 1",
            (ids, ids),
        ).fetchone()
        is not None
    )


def asks_as_task(conn: sqlite3.Connection, parent_id: object) -> bool:
    """Whether a bot's question on this task is filed as a question task: the switch is on, and the task is not a dream
    (backend/dream.py), whose Learnings panel reads and answers its one ❓ on the task's own chat."""
    if not enabled(conn):
        return False
    from . import dream as _dream

    marked = f"SELECT 1 FROM tasks WHERE id=? AND {cast(Any, _dream).MARKED}"
    return conn.execute(marked, (str(parent_id or ""),)).fetchone() is None


def get(conn: sqlite3.Connection, task_id: object) -> Row | None:
    found = conn.execute("SELECT * FROM questions WHERE task_id=?", (str(task_id or ""),)).fetchone()
    return dict(found) if found else None


def state(row: Mapping[str, Any]) -> str:
    reason = row.get("ended_reason")
    return "open" if not reason else "answered" if reason == ANSWERED else "withdrawn"


def wait_id(row: Mapping[str, Any]) -> str:
    return PREFIX + str(row["task_id"])


def parent_wait_id(row: Mapping[str, Any]) -> str:
    """A question's wait id on its parent (waiting_on.of): names the person it waits on, never the question's task, so
    nothing that lists or tells what the parent waits on can name a question its reader may not open."""
    return PREFIX + str(row["person_id"])


# What a question's wait on its parent says, to every reader: never its words, nor a name. Who it waits on is the
# wait's `who`, an actor id each reader is shown as a ❓ wait's is (teammate_access.Known), never text in the line.
LINE = "A question"


def is_wait(value: object) -> bool:
    """Whether a wait's id is a question's (`wait_id`)."""
    return str(value or "").startswith(PREFIX)


def split(text: str) -> tuple[str, str]:
    """A question's title (its first line, as a ❓'s line) and description (the lines after it)."""
    lines = str(text or "").strip().splitlines()
    return question_marks.first_line(text), "\n".join(lines[1:]).strip()


# ---- reads -----------------------------------------------------------------------------------------------------------

_OPEN = (
    "SELECT q.*, t.title, t.number FROM questions q JOIN tasks t ON t.id = q.task_id "
    "WHERE q.ended_reason IS NULL ORDER BY q.updated_at, q.task_id"
)


def _open_by_parent(conn: sqlite3.Connection) -> dict[str, list[Row]]:
    """Every open question by its parent, read once per snapshot (read_cache): a pass over many tasks asks each.
    Shared: never change a row."""
    from . import read_cache as _read_cache

    def build() -> dict[str, list[Row]]:
        out: dict[str, list[Row]] = {}
        for found in conn.execute(_OPEN):
            row = dict(found)
            out.setdefault(str(row["parent_id"]), []).append(row)
        return out

    return cast(Any, _read_cache).value(conn, ("questions_open",), build, shared=True)


def _answerable(conn: sqlite3.Connection, row: Mapping[str, Any]) -> bool:
    from . import task_requirements as _task_requirements

    return bool(cast(Any, _task_requirements).active_person(conn, row["person_id"]))


def on_parent(conn: sqlite3.Connection, parent: Mapping[str, Any]) -> list[Row]:
    """The parent's open questions on its open visit whose person can still answer them, oldest first."""
    visit: Row = parent.get("visit") or {}
    if not visit.get("id"):
        return []
    rows = _open_by_parent(conn).get(str(parent["id"]), [])
    return [q for q in rows if q["visit_id"] == visit["id"] and _answerable(conn, q)]


def open_by(conn: sqlite3.Connection, asker: str, parent: Mapping[str, Any]) -> list[Row]:
    return [q for q in on_parent(conn, parent) if q["asker_id"] == asker]


def refuse_ending(conn: sqlite3.Connection, by: str, task_id: object) -> None:
    """A bot's done, forward move or close of a task while its own question there is open (question_marks
    .refuse_ending asks this after its own ❓)."""
    row = _engine().task(conn, str(task_id))
    found = open_by(conn, by, row) if row else []
    if found:
        q = found[0]
        detail = STILL_OPEN.format(name=H.actor_name(conn, q["person_id"]), title=q["title"], question=q["task_id"])
        H.refuse(conn, by, "task_question_open", detail, task=str(task_id))


def read(conn: sqlite3.Connection, task_id: object, visible_sql: str) -> Row | None:
    """A question task's `question`, for whoever reads the task (GET /api/v2/tasks/{id}): its parent (named only to a
    reader of it), asker, person, round, link, state, how it ended and its answer."""
    q = get(conn, task_id)
    if not q:
        return None
    parent = conn.execute(
        f"SELECT id, number, title FROM tasks WHERE id=? AND ({visible_sql})", (q["parent_id"],)
    ).fetchone()
    body = conn.execute("SELECT body FROM tasks WHERE id=?", (q["task_id"],)).fetchone()
    said = (
        conn.execute(
            "SELECT body FROM messages WHERE id=? AND deleted_at IS NULL", (q["answer_message_id"],)
        ).fetchone()
        if q.get("answer_message_id")
        else None
    )
    return {
        "parent": {"id": parent[0], "number": parent[1], "title": parent[2]} if parent else None,
        "detail": str(body[0] or "") if body else "",
        "asker": q["asker_id"],
        "person": q["person_id"],
        "round": q["round"],
        "link_url": q.get("link_url"),
        "state": state(q),
        "ended_reason": q.get("ended_reason"),
        "answered_at": q.get("answered_at"),
        "answered_by": q.get("answered_by_id"),
        "answer_message_id": q.get("answer_message_id"),
        "answer_text": str(said[0]) if said else None,
    }


def listed_on(conn: sqlite3.Connection, parent_id: object, visible_sql: str) -> list[Row]:
    """The parent's open questions the reader may read (`questions` of GET /api/v2/tasks/{id}), oldest first."""
    found = conn.execute(
        "SELECT q.task_id, tasks.number, tasks.title, q.person_id, q.ended_reason FROM tasks "
        f"JOIN questions q ON q.task_id = tasks.id WHERE q.parent_id=? AND q.ended_reason IS NULL AND ({visible_sql}) "
        "ORDER BY q.created_at, q.task_id",
        (str(parent_id or ""),),
    ).fetchall()
    return [
        {"task_id": r[0], "number": r[1], "title": r[2], "person": r[3], "state": state({"ended_reason": r[4]})}
        for r in found
    ]


def next_snooze_end(conn: sqlite3.Connection, person: str) -> str | None:
    """When the person's next snoozed open question comes back, or None: Needs you reads again then."""
    found = conn.execute(
        "SELECT MIN(z.until_at) FROM snoozes z JOIN questions q ON q.task_id = z.task_id "
        "WHERE q.person_id=? AND q.ended_reason IS NULL AND z.ended_at IS NULL AND z.until_at > ?",
        (person, H.now()),
    ).fetchone()
    return str(found[0]) if found and found[0] else None


def rule_text(conn: sqlite3.Connection, stored: Mapping[str, Any], text: str) -> str:
    """The text a held question is checked again on (org_rule_check.recheck): its words and its link while it is still
    asked as a question task, its words alone once the switch is off (a ❓ carries no link)."""
    replay: Any = stored.get("replay") or {}
    link = str(cast(Row, replay).get("link") or "") if isinstance(replay, dict) else ""
    task_id = cast(Row, replay).get("task") if isinstance(replay, dict) else None
    if link and text.endswith(f"\n\n{link}") and not asks_as_task(conn, task_id):
        return text[: -len(f"\n\n{link}")]
    return text


def refuse_move(conn: sqlite3.Connection, row: Mapping[str, Any], kind: str, by: str) -> None:
    """A question task never leaves the stage it was filed in (task_engine.move and refile), so no stage's sign-off can
    sit in front of its answer: while it is open only a close moves it, and once it has a question row it is never
    refiled. Its own ends (answered, withdrawn) are written after its row has ended."""
    q = get(conn, row["id"])
    if q and (kind == "refile" or (not q.get("ended_reason") and kind != "closed")):
        H.refuse(conn, by, "task_question", STAYS, task=str(row["id"]))


def answered_marks(conn: sqlite3.Connection, visit_id: str) -> str:
    """When a person last answered a question on this visit, for task_engine.progress_mark: one read per snapshot for
    every visit (read_cache), as question_marks.replies is."""
    from . import read_cache as _read_cache

    def build() -> dict[str, str]:
        sql = "SELECT visit_id, MAX(answered_at) FROM questions WHERE answered_at IS NOT NULL GROUP BY visit_id"
        return {str(v): str(at) for v, at in conn.execute(sql)}

    return cast(Any, _read_cache).value(conn, ("questions_answered",), build, shared=True).get(str(visit_id), "")


def items(conn: sqlite3.Connection, only: Any, snoozed: str = "hide") -> dict[str, list[Row]]:
    """Each person's open questions as Needs-you items (kind `question_task`), for the people in `only`. A question
    the person snoozed (the task's own snooze) is out until its time (`snoozed` as in waiting_on._lists)."""
    rows = [
        q
        for by_parent in _open_by_parent(conn).values()
        for q in by_parent
        if q["person_id"] in only and _answerable(conn, q)
    ]
    if not rows:
        return {}
    ids = H._dump(sorted({str(q["task_id"]) for q in rows}))
    tasks = {
        str(r[0]): (r[1], r[2], r[3], r[4])
        for r in conn.execute(
            "SELECT id, number, title, status, owner_id FROM tasks WHERE id IN (SELECT value FROM json_each(?))", (ids,)
        )
    }
    now = H.now()
    held: dict[str, str] = {}
    for task_id, until in conn.execute(
        "SELECT task_id, until_at FROM snoozes WHERE task_id IN (SELECT value FROM json_each(?)) AND ended_at IS NULL",
        (ids,),
    ):
        if not until or str(until) > now:
            held[str(task_id)] = str(until or "")
    out: dict[str, list[Row]] = {}
    for q in rows:
        own = tasks.get(str(q["task_id"]))
        if not own or own[2] != "open":
            continue
        until = held.get(str(q["task_id"]))
        if (snoozed == "hide" and until is not None) or (snoozed == "only" and until is None):
            continue
        out.setdefault(str(q["person_id"]), []).append(
            {
                "kind": KIND,
                "id": wait_id(q),
                "task_id": q["task_id"],
                "number": own[0],
                "title": own[1],
                "owner_id": own[3],
                "line": own[1],
                "missing": [own[1]],
                "asker": q["asker_id"],
                "since": q["updated_at"],
                "round": q["round"],
                "link_url": q.get("link_url"),
                **({"snoozed_until": until} if until else {}),
            }
        )
    return out


# ---- asking ------------------------------------------------------------------------------------------------------------


def ask(
    conn: sqlite3.Connection,
    asker: str,
    parent_id: str,
    person: str,
    text: str,
    *,
    checked: bool = True,
    let_in: bool | None = None,
    link_url: str | None = None,
) -> Row:
    """`asker`, a bot, asks `person` `text` on the parent task: every check a ❓ runs, then the question filed (or its
    earlier one to them on this visit reopened). `checked` and `let_in` as question_marks.ask. Returns the question as
    `public` gives it."""
    E = _engine()
    parent: Row | None = E.task(conn, parent_id)
    if not parent or parent.get("status") != "open" or not parent.get("visit"):
        H.refuse(conn, asker, "identity", "The task has ended: a question is for an open task")
    assert parent is not None
    if (not checked) if let_in is None else let_in:
        if not H.task_private_readable(conn, person, parent):
            H.task_reach(conn, asker, parent, person)
    target = question_marks.check(conn, asker, str(parent["id"]), person, text, checked=checked)
    earlier = conn.execute(
        "SELECT * FROM questions WHERE parent_id=? AND asker_id=? AND person_id=? AND visit_id=? "
        "AND (ended_reason IS NULL OR ended_reason=?) ORDER BY created_at DESC LIMIT 1",
        (parent["id"], asker, target, parent["visit"]["id"], ANSWERED),
    ).fetchone()
    if earlier:
        return public(_again(conn, dict(earlier), asker, text, checked=checked, link_url=link_url))
    return public(file(conn, asker, parent, target, text, link_url=link_url))


def file(
    conn: sqlite3.Connection,
    asker: str,
    parent: Row,
    person: str,
    text: str,
    *,
    link_url: str | None = None,
    source_mark_id: str | None = None,
    at: str | None = None,
) -> Row:
    """File the question task under `parent` and its row, its checks already run. Returns the row."""
    title, body = split(text)
    made: Row = H.task_create(conn, asker, title, "", person, parent_id=parent["id"], lint=False)
    # A question is not filed work: without its link, the filed-task holds and wakes (task_engine._ended) skip it.
    conn.execute("DELETE FROM links WHERE to_task_id=?", (made["id"],))
    conn.execute("UPDATE tasks SET body=? WHERE id=?", (body, made["id"]))
    # Its words, whole, open its chat as the bot's request, a single line too: a later round's words follow them.
    H.first_message(conn, asker, made["id"], made["conversation_id"], text)
    stamp = at or H.now()
    row: Row = {
        "task_id": made["id"],
        "parent_id": parent["id"],
        "visit_id": parent["visit"]["id"],
        "asker_id": asker,
        "person_id": person,
        "round": 1,
        "link_url": link_url or None,
        "source_mark_id": source_mark_id,
        "created_at": stamp,
        "updated_at": stamp,
    }
    conn.execute(
        "INSERT INTO questions (task_id, parent_id, visit_id, asker_id, person_id, round, link_url, source_mark_id, "
        "created_at, updated_at) VALUES (:task_id, :parent_id, :visit_id, :asker_id, :person_id, :round, :link_url, "
        ":source_mark_id, :created_at, :updated_at)",
        row,
    )
    H.event(conn, asker, "question.asked", made["id"], {"parent": parent["id"], "to": person, "round": 1})
    return get(conn, made["id"]) or row


def _again(conn: sqlite3.Connection, row: Row, asker: str, text: str, *, checked: bool, link_url: str | None) -> Row:
    """The same bot asks the same person again on the same visit: the question opens again with the next round, its
    new words as the bot's message in its chat and as its title and description. A snooze of the old round ends."""
    E = _engine()
    qid = str(row["task_id"])
    title, body = split(text)
    found: Row | None = E.task(conn, qid)
    if found and found.get("status") != "open":
        E.move(conn, qid, E.REOPEN, asker, "Asked again", cascade=True)
    elif found and found.get("snooze"):
        E.unsnooze(conn, qid, H.TICO_ACTOR, "Asked again")
    conn.execute("UPDATE tasks SET title=?, body=?, updated_at=? WHERE id=?", (title, body, H.now(), qid))
    H.task_comment(conn, asker, qid, text, wake=False, checked=checked)
    at = H.now()
    conn.execute(
        "UPDATE questions SET round=round+1, ended_reason=NULL, link_url=?, updated_at=? WHERE task_id=?",
        (link_url or None, at, qid),
    )
    H.event(conn, asker, "question.asked", qid, {"parent": row["parent_id"], "round": int(row["round"]) + 1})
    return get(conn, qid) or row


def public(row: Mapping[str, Any]) -> Row:
    """A question as the ask routes answer with it."""
    return {
        "task_id": row["task_id"],
        "parent_id": row["parent_id"],
        "asker": row["asker_id"],
        "person": row["person_id"],
        "round": row["round"],
        "link_url": row.get("link_url"),
        "state": state(row),
    }


# ---- answering ---------------------------------------------------------------------------------------------------------


def answer(conn: sqlite3.Connection, task_id: str, by: str, text: str, round_: int, *, via: object = None) -> Row:
    """`by` answers the question at the round they were shown, in one write: their message on it, the question
    finished, the parent's progress, the relay and at most one wake (`_answered`). Refused with 409 LOOK_AGAIN unless
    `by` is its person now and `round_` its round; only their own hand, their token or their Assistant on their word
    answers for them (question_marks.REPLY_VIA), never an automation writing as them."""
    q = get(conn, task_id)
    if not q:
        raise QuestionError("not_found", "No such question", 404)
    if via not in question_marks.REPLY_VIA:
        raise QuestionError("forbidden", "Only the person it is for answers it", 403)
    own: Row = _engine().task(conn, task_id) or {}
    current = q["person_id"] == by and own.get("owner_id") == by and own.get("status") == "open"
    if q.get("ended_reason") or not current or int(q["round"]) != int(round_):
        raise QuestionError("look_again", LOOK_AGAIN, 409)
    words = str(text or "").strip()
    if not words:
        raise QuestionError("validation", "Write the answer", 422)
    # Whether the parent may be woken is read from where it stood before this write: the answer's own progress resets a
    # person-level ladder to its owner, which must not turn a person's hold into a bot's turn.
    held = _held_for_a_person(conn, q)
    from . import threads as _threads

    # Quiet as its relay is: the answer goes to the bot by its one wake, never to an @name in its words.
    with cast(Any, _threads).relaying():
        said: Row = H.task_comment(conn, by, task_id, words, wake=False)
    return _answered(conn, q, by, words, str(said["id"]), wake=not held)


def _held_for_a_person(conn: sqlite3.Connection, q: Mapping[str, Any]) -> bool:
    """Whether the parent waits on a person now other than through this question (another question or ❓, an OK, a
    page, a task to share), is at the ladder's person level, or is snoozed: then an answer wakes nobody."""
    from . import waiting_on as _waiting_on

    W = cast(Any, _waiting_on)
    parent: Row | None = _engine().task(conn, q["parent_id"])
    visit: Row = (parent or {}).get("visit") or {}
    if not parent or parent.get("snooze") or (visit.get("level") or "owner") == "person":
        return True
    if any(other["task_id"] != q["task_id"] for other in on_parent(conn, parent)):
        return True
    return any(w.state == W.PERSON and not is_wait(w.requirement_id) for w in W.of(conn, parent))


def _answered(conn: sqlite3.Connection, q: Row, by: str, words: str, message_id: str, *, wake: bool) -> Row:
    """The answer write's work once its checks passed (`answer`)."""
    E = _engine()
    qid, parent_id, asker = str(q["task_id"]), str(q["parent_id"]), str(q["asker_id"])
    at = H.now()
    conn.execute(
        "UPDATE questions SET ended_reason=?, answered_at=?, answered_by_id=?, answer_message_id=?, updated_at=? "
        "WHERE task_id=?",
        (ANSWERED, at, by, message_id, at, qid),
    )
    H.event(conn, by, "question.answered", qid, {"parent": parent_id, "round": q["round"]})
    _finish(conn, qid, by, f"Answered by {H.actor_name(conn, by)}")
    found: Row | None = E.task(conn, parent_id)
    visit: Row = (found or {}).get("visit") or {}
    if not found or found.get("status") != "open" or visit.get("id") != q["visit_id"]:
        return get(conn, qid) or q
    # Progress on the parent, as a reply to a ❓ is: its ladder and Late clock start again, a bot's snooze ends.
    question_marks.after_reply(conn, parent_id, by, "Answered")
    own: Row = E.task(conn, qid) or {}
    title = str(own.get("title") or "")
    relayed = _relay(conn, found, qid, by, asker, title, words)
    # One wake at most, for whoever has the parent's turn now that the answer is in: nobody while another ask there
    # still waits on a person (waiting_on.turn), and not necessarily the bot that asked.
    from . import waiting_on as _waiting_on

    parent: Row = E.task(conn, parent_id) or found
    turn = cast(Any, _waiting_on).turn(conn, parent)
    if wake and turn and _may_wake(conn, parent):
        # The answer's words never ride on the wake, a notice in the parent's chat: the bot reads them where it may,
        # in that chat (`relayed`) or on the question.
        name, number = H.actor_name(conn, by), own.get("number") or qid
        where = "Their answer is on this task's chat." if relayed else f"Read it on the question ({qid})."
        if turn == asker:
            lead = f'{name} answered your question "{title}" (#{number}). {where}'
        else:
            lead = f"{name} answered a question asked on this task (#{number}). {where}"
        _wake(conn, parent, str(turn), lead, f"question:{qid}:{q['round']}")
    return get(conn, qid) or q


def _may_wake(conn: sqlite3.Connection, parent: Row) -> bool:
    """Whether a wake about the parent could start a run now (task_wakes.may_start's own holds): not snoozed, not at
    level person, and under the visit's runs. Otherwise none is queued only to be canceled at claim."""
    from . import task_wakes as _task_wakes

    wakes = cast(Any, _task_wakes)
    visit: Row = parent.get("visit") or {}
    if parent.get("snooze") or not visit or (visit.get("level") or "owner") == "person":
        return False
    return int(wakes.runs_since(conn, parent, starting=True)) < int(wakes.RUNS)


def _relay(conn: sqlite3.Connection, parent: Row, qid: str, by: str, asker: str, title: str, words: str) -> str | None:
    """The answer on the parent's chat as the person's message, where whoever reads that chat may read it
    (task_privacy.require_destination, the subtask rule included). Returns its id, or None when it stays on the
    question."""
    from types import SimpleNamespace

    from . import task_privacy as _task_privacy

    privacy = cast(Any, _task_privacy)
    who = SimpleNamespace(actor=by, attempt_id=None, task_actor="", role="human", via="")
    refs = {"task": parent["id"], "question": qid}
    if not H.task_private_readable(conn, by, parent):
        return None
    try:
        privacy.require_destination(conn, who, asker, parent["conversation_id"], refs)
    except Exception as exc:  # task_privacy's Problem: the parent's chat may not carry it
        if getattr(exc, "status", None) != 403:
            raise
        return None
    from . import threads as _threads

    # Quiet: Defer never, and its words address nobody (an @name in them wakes no one) nor answer a ❓ there.
    with cast(Any, _threads).relaying():
        said: Row = H.task_comment(
            conn, by, parent["id"], f"Re: {title}: {words}", wake=False, extra_refs={"question": qid}
        )
    return str(said["id"])


def _wake(conn: sqlite3.Connection, parent: Row, bot: str, lead: str, kind: str) -> None:
    from . import task_wakes as _task_wakes

    if not H.is_bot(bot) or not parent.get("visit"):
        return
    wakes = cast(Any, _task_wakes)
    wakes.add(
        conn,
        parent,
        bot,
        parent["visit"],
        wakes.body(conn, parent, lead, to=bot),
        wake_key=f"{parent['id']}:{parent['visit']['id']}:{kind}",
    )


def _finish(conn: sqlite3.Connection, qid: str, by: str, why: str) -> None:
    """The question task ends with its question, however it ended: closed, with why (the engine's close, which no
    stage holds). Finishing a question is no stage's work, so it never answers a requirement, advances, or enters
    another stage; that it was answered is its row's (`state`), not Done."""
    E = _engine()
    row: Row | None = E.task(conn, qid)
    if row and row.get("status") == "open":
        E.move(conn, qid, E.CLOSED, by, why, cascade=True)


def _end(conn: sqlite3.Connection, q: Mapping[str, Any], reason: str, by: str) -> None:
    """End an open question for `reason`, its task closed with why it ended."""
    at = H.now()
    conn.execute(
        "UPDATE questions SET ended_reason=?, updated_at=? WHERE task_id=? AND ended_reason IS NULL",
        (reason, at, q["task_id"]),
    )
    H.event(conn, by, "question.ended", q["task_id"], {"reason": reason, "parent": q["parent_id"]})
    _finish(conn, str(q["task_id"]), by, WHY[reason])


def withdraw(conn: sqlite3.Connection, task_id: str, by: str, why: str | None = None) -> Row:
    """The bot that asked takes its question back (POST /api/v2/tasks/{id}/question/withdraw)."""
    q = get(conn, task_id)
    if not q:
        raise QuestionError("not_found", "No such question", 404)
    if q["asker_id"] != by:
        raise QuestionError("forbidden", "Only the bot that asked withdraws a question", 403)
    if q.get("ended_reason"):
        raise QuestionError("look_again", "This question has already ended", 409)
    conn.execute("UPDATE questions SET ended_reason=?, updated_at=? WHERE task_id=?", (WITHDRAWN, H.now(), task_id))
    H.event(conn, by, "question.ended", task_id, {"reason": WITHDRAWN, "parent": q["parent_id"]})
    _finish(conn, task_id, by, why or WHY[WITHDRAWN])
    return get(conn, task_id) or q


def give(conn: sqlite3.Connection, task_id: str, to: str, by: str) -> Row:
    """Give to (POST /api/v2/tasks/{id}/question/give): the question is another person's now. Its person or the bot
    that asked may; the new person passes the checks a ❓'s Give to does. Through the owner change, as any reassign of a
    question goes (`reassigning`)."""
    q = get(conn, task_id)
    if not q:
        raise QuestionError("not_found", "No such question", 404)
    if q.get("ended_reason"):
        raise QuestionError("look_again", "This question has already ended", 409)
    if by not in (q["person_id"], q["asker_id"]):
        raise QuestionError("forbidden", "Only the person it is for, or the bot that asked, gives it to someone", 403)
    E = _engine()
    row: Row = E.task(conn, task_id) or {}
    E.reassign(conn, task_id, to, by, "Given to " + str(H.actor_name(conn, to)), row.get("version"))
    return get(conn, task_id) or q


def reassigning(conn: sqlite3.Connection, row: Mapping[str, Any], owner: str | None, by: str) -> None:
    """A question task changes owner (task_engine.reassign, any route): only to a person who passes a ❓'s Give-to
    checks, as the bot that asked. Its row follows, and a snooze of the old person's ends."""
    q = get(conn, row["id"])
    if not q or q.get("ended_reason"):
        return
    if not H.is_human(owner):
        H.refuse(conn, by, "reach", NOT_A_PERSON, task=str(row["id"]))
    target = question_marks.addressee(conn, str(q["asker_id"]), str(owner), str(q["parent_id"]))
    if row.get("snooze"):
        _engine().unsnooze(conn, str(row["id"]), H.TICO_ACTOR, "Given to someone else")
    # A new round: what the person it was for saw cannot be answered once it comes back to them.
    conn.execute(
        "UPDATE questions SET person_id=?, round=round+1, updated_at=? WHERE task_id=?", (target, H.now(), row["id"])
    )
    H.event(conn, by, "question.given", row["id"], {"from": q["person_id"], "to": target})


def refuse_snooze(conn: sqlite3.Connection, row: Mapping[str, Any], by: str) -> None:
    """Only its person snoozes an open question: it is their list it leaves."""
    q = get(conn, row["id"])
    if q and not q.get("ended_reason") and by != q["person_id"]:
        H.refuse(conn, by, "identity", "Only the person a question is for snoozes it", task=str(row["id"]))


# ---- endings -----------------------------------------------------------------------------------------------------------


def moved(conn: sqlite3.Connection, before: Mapping[str, Any], after: Mapping[str, Any], by: str) -> None:
    """A task moved (task_effects.moved). A question task closed by any other road than this module's (only a close
    gets past `refuse_move`) is withdrawn, and its bot is told when someone else closed it. A parent that ended or left
    the visit its questions were asked on ends them."""
    status = str(after.get("status") or "")
    q = get(conn, after["id"])
    if q and not q.get("ended_reason") and status != "open":
        conn.execute(
            "UPDATE questions SET ended_reason=?, updated_at=? WHERE task_id=?", (WITHDRAWN, H.now(), q["task_id"])
        )
        H.event(conn, by, "question.ended", q["task_id"], {"reason": WITHDRAWN, "parent": q["parent_id"]})
        parent: Row | None = _engine().task(conn, q["parent_id"]) if by != q["asker_id"] else None
        if parent and parent.get("status") == "open":
            name = H.actor_name(conn, by)
            _wake(
                conn,
                parent,
                str(q["asker_id"]),
                f'{name} closed your question "{after.get("title")}" without answering it.',
                f"question:{q['task_id']}:{q['round']}:closed",
            )
    visit: Mapping[str, Any] = after.get("visit") or {}
    open_here = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM questions WHERE parent_id=? AND ended_reason IS NULL", (str(after["id"]),)
        ).fetchall()
    ]
    for each in open_here:
        if status == "open" and each["visit_id"] == visit.get("id"):
            continue
        _end(conn, each, PARENT_ENDED if status != "open" else PARENT_MOVED, H.TICO_ACTOR)


def end_for_parents(conn: sqlite3.Connection, task_ids: Iterable[str], by: str | None = None) -> None:
    """The tasks ended outside a move (a merge): their open questions end with them."""
    for parent_id in sorted({str(t) for t in task_ids if t}):
        for found in conn.execute("SELECT * FROM questions WHERE parent_id=? AND ended_reason IS NULL", (parent_id,)):
            _end(conn, dict(found), PARENT_ENDED, by or H.TICO_ACTOR)


def on_delete(conn: sqlite3.Connection, task_ids: Iterable[str], by: str) -> None:
    """Tasks are being deleted (task_engine.on_delete): a deleted parent's open questions end with it, and a deleted
    question ends as withdrawn, so its parent waits on nobody."""
    ids = sorted({str(t) for t in task_ids if t})
    if not ids or not (enabled(conn) or touches(conn, ids)):
        return
    at, listed = H.now(), H._dump(ids)
    conn.execute(
        "UPDATE questions SET ended_reason=?, updated_at=? WHERE ended_reason IS NULL "
        "AND task_id IN (SELECT value FROM json_each(?))",
        (WITHDRAWN, at, listed),
    )
    for found in conn.execute(
        "SELECT * FROM questions WHERE ended_reason IS NULL AND parent_id IN (SELECT value FROM json_each(?))",
        (listed,),
    ).fetchall():
        _end(conn, dict(found), PARENT_ENDED, by)


def end_gone(conn: sqlite3.Connection, task_id: object = None) -> list[str]:
    """End each open question (on or under `task_id`, or anywhere) whose person or bot can no longer take part: the
    person left (`person_gone`: the wait ends and the bot is told on the parent), or the bot left or can no longer read
    the parent (`asker_gone`: the parent's holder is told). Run where question_marks.clear_gone runs. Returns the
    questions it ended."""
    from . import waiting_on as _waiting_on

    W = cast(Any, _waiting_on)
    E = _engine()
    sql = "SELECT q.*, t.title FROM questions q JOIN tasks t ON t.id = q.task_id WHERE q.ended_reason IS NULL"
    if task_id:
        sql += " AND (q.parent_id=? OR q.task_id=?)"
        rows = [dict(r) for r in conn.execute(sql, (str(task_id), str(task_id)))]
    else:
        rows = [dict(r) for r in conn.execute(sql)]
    ended: list[str] = []
    for q in rows:
        parent: Row | None = E.task(conn, q["parent_id"])
        if not parent:
            continue
        asker = str(q["asker_id"])
        bot: Row | None = H.bot(conn, H.actor_id(asker)) if H.is_bot(asker) else None
        if not _answerable(conn, q):
            _end(conn, q, PERSON_GONE, H.TICO_ACTOR)
            question_marks.after_reply(conn, q["parent_id"], H.TICO_ACTOR, "Its question can't be answered")
            name = H.actor_name(conn, q["person_id"])
            _wake(
                conn,
                E.task(conn, q["parent_id"]) or parent,
                asker,
                f"{name} can no longer answer your question \"{q['title']}\". Ask someone who can, or go on "
                "without it.",
                f"question:{q['task_id']}:gone",
            )
        elif not bot or bot.get("state") == "archived" or not H.task_private_readable(conn, asker, parent):
            _end(conn, q, ASKER_GONE, H.TICO_ACTOR)
            holder = W.holder(conn, parent)
            told = (
                f"{H.actor_name(conn, asker)} can no longer work on this task, so its question to "
                f"{H.actor_name(conn, q['person_id'])} was withdrawn. Ask again if it still matters."
            )
            # A person holding the parent sees the question ended there (its row and its closed task): nothing is
            # written to them, so nothing about the task reaches anyone who does not already read it.
            if H.is_bot(holder) and holder != asker:
                _wake(
                    conn,
                    E.task(conn, q["parent_id"]) or parent,
                    str(holder),
                    told,
                    f"question:{q['task_id']}:asker-gone",
                )
        else:
            continue
        ended.append(str(q["task_id"]))
    return ended


# ---- the backfill ------------------------------------------------------------------------------------------------------


# What a plain ❓ comment's message carries in its refs: that it is a comment on its task, its delivery, and its mark's
# mirror. Anything more (attachments, files, pages shown, another task named, a live browser) is a part the question
# task would not carry, or a reader it would not keep: the backfill skips it.
PLAIN_REFS = {"comment", "quiet", "send_defer_for", "send_way", "send_people", "task", "question"}
WAYS = (None, "steer", "queue", "defer")


def _not_plain(refs: object, mark: Mapping[str, Any]) -> str:
    """Why a ❓'s message refs are not exactly a plain comment's on the mark's own task, or "" when they are."""
    if not isinstance(refs, dict):
        return "no refs"
    from . import delivery as _delivery

    defer_for = str(cast(Any, _delivery).DEFER_FOR)  # a Defer's deadline as delivery takes it: never, or how long
    said = cast(Row, refs)
    extra = sorted(set(said) - PLAIN_REFS)
    if extra:
        return "it carries " + ", ".join(extra)
    question: Any = said.get("question")
    checks = (
        (said.get("comment") is True, "comment"),
        (said.get("task") == mark["task_id"], "task"),
        (isinstance(question, dict) and cast(Row, question).get("id") == mark["id"], "question"),
        (said.get("quiet") in (None, True), "quiet"),
        (said.get("send_way") in WAYS, "send_way"),
        (
            said.get("send_defer_for") is None or bool(re.match(defer_for, str(said["send_defer_for"]))),
            "send_defer_for",
        ),
        (said.get("send_people") in (None, True), "send_people"),
    )
    bad = [name for ok, name in checks if not ok]
    return ("unexpected " + ", ".join(bad)) if bad else ""


def _names_another(conn: sqlite3.Connection, refs: object, task_id: str) -> bool:
    """Whether any value in the refs names a task other than the mark's own (task_privacy.references, which finds a
    task id anywhere in them): its readers would not come with it."""
    from . import task_privacy as _task_privacy

    named: set[str] = {str(t) for t in cast(Any, _task_privacy).references(conn, refs)}
    return bool(named - {str(task_id)})


def command(conn: Any, action: str, by: str) -> Row:
    """python -m backend.manage questions DATABASE show|on|off|backfill, in the command's one transaction."""
    if action in ("on", "off"):
        set_enabled(conn, action == "on", by)
    if action == "backfill" and not enabled(conn):
        raise SystemExit("Turn questions on first: python -m backend.manage questions DATABASE on")
    report = backfill(conn) if action == "backfill" else {}
    return {**report, "on": enabled(conn)}


def _counts(conn: sqlite3.Connection) -> Row:
    marks = conn.execute("SELECT COUNT(*) FROM question_marks WHERE cleared_at IS NULL AND task_id <> ''").fetchone()[0]
    opened = conn.execute("SELECT COUNT(*) FROM questions WHERE ended_reason IS NULL").fetchone()[0]
    made = conn.execute("SELECT COUNT(*) FROM questions WHERE source_mark_id IS NOT NULL").fetchone()[0]
    return {"open_task_marks": marks, "open_questions": opened, "from_marks": made}


def backfill(conn: sqlite3.Connection) -> Row:
    """Turn each open ❓ on a task into a question (python -m backend.manage questions DB backfill). Each mark is
    reconciled first, its person (who it waits on now), its room (the task's own chat), its snooze (the person's, kept
    until its time) and its history (its message stays in the parent's chat; the question's chat opens with the same
    words, dated when it was asked), then filed and cleared in one savepoint, so a mark that fails leaves nothing
    behind and the rest go on. Retryable: a mark already turned (`source_mark_id`) is not turned again. A ❓ in a
    direct chat, one showing a live browser to take over, a dream's (Learnings reads it) and one whose message carries
    more than its words (attachments, files, pages) stay ❓. Returns counts before and after, what it turned and what it
    skipped, with why."""
    E = _engine()
    before = _counts(conn)
    turned: list[Row] = []
    skipped: list[Row] = []
    marks = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM question_marks WHERE cleared_at IS NULL AND task_id <> '' ORDER BY created_at, id"
        )
    ]
    for mark in marks:
        mid = str(mark["id"])

        def skip(why: str, mid: str = mid) -> None:
            skipped.append({"mark_id": mid, "why": why})

        if conn.execute("SELECT 1 FROM questions WHERE source_mark_id=?", (mid,)).fetchone():
            skip("already a question")
            continue
        message: Row | None = H.message(conn, mark["message_id"])
        if not message:
            skip("its message is gone")
            continue
        refs: Any = H._json(message.get("refs"), {}) or {}
        shows: list[Any] = list(cast(Row, refs).get("shows") or []) if isinstance(refs, dict) else []
        if any(str(r).startswith("browser:") for r in shows):
            skip("it shows a live browser to take over")
            continue
        odd = _not_plain(cast(object, refs), mark)
        if odd:
            skip("its message is not a plain ❓ comment on its task: " + odd)
            continue
        if _names_another(conn, cast(object, refs), str(mark["task_id"])):
            skip("its message names another task, whose readers it keeps")
            continue
        if message.get("in_reply_to"):
            skip("its message replies to another, whose readers it keeps")
            continue
        parent: Row | None = E.task(conn, mark["task_id"])
        if not parent or parent.get("status") != "open" or not parent.get("visit"):
            skip("its task is not open")
            continue
        if not asks_as_task(conn, parent["id"]):
            skip("it is a dream's question, which Learnings reads and answers")
            continue
        own_thread = conn.execute("SELECT id FROM conversations WHERE task_id=?", (parent["id"],)).fetchall()
        if len(own_thread) != 1 or str(message.get("conversation_id")) != str(own_thread[0][0]):
            skip("it is not on its task's own thread (a shared chat, a sub-thread, or another room)")
            continue
        if not question_marks.answerable(conn, mark):
            skip("its person can no longer answer it")
            continue
        until = question_marks.snoozes(conn, str(mark["person_id"])).get((str(mark["person_id"]), mid))
        conn.execute("SAVEPOINT question_backfill")
        try:
            made = file(
                conn,
                str(mark["bot_id"]),
                parent,
                str(mark["person_id"]),
                str(message.get("body") or mark["line"]),
                source_mark_id=mid,
                at=str(mark["created_at"]),
            )
            if until:
                E.snooze(conn, made["task_id"], mark["person_id"], "Snoozed", until)
            conn.execute(
                "UPDATE question_marks SET cleared_at=?, cleared_reason='question', cleared_by_id=? "
                "WHERE id=? AND cleared_at IS NULL",
                (H.now(), H.TICO_ACTOR, mid),
            )
            H._recount(conn, mark["bot_id"])
        except H.Refused as exc:
            conn.execute("ROLLBACK TO question_backfill")
            conn.execute("RELEASE question_backfill")
            skip(f"refused: {exc.detail or exc.rule}")
            continue
        conn.execute("RELEASE question_backfill")
        turned.append({"mark_id": mid, "task_id": made["task_id"], "parent_id": parent["id"]})
    return {"before": before, "after": _counts(conn), "turned": turned, "skipped": skipped}
