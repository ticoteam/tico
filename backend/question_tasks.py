"""Question tasks (spec D8): a bot's decision for a person is a task the person owns, with the question on it.

`hub task question "<q>" --on <person>` (backend/media.py task_question) files it: under the bot's task when asked from
one (a subtask the task is blocked on, as hubdb._ask_in_subtask files a card), else on its own. The question lives in
the task's fields (`questions`, `if_no_answer`, `review`, `spend`; the migration names them), never in an ask message.
Answering is the close: a pick, a Yes / No or a note closes it done with `answer` recorded, a decline closes it
declined, and the bot hears it once (on its task through hubdb._subtask_wake, else as the task's requester).

Every surface reads it through `as_ask`, the question shaped as the ask a question card already draws (Needs you,
Decide, the task page, the Ask panel). One open question
task per topic per person and bot (`open_for`, `fold`). ask_deadlines and still_needed settle them on their dates.
"""

import json
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from . import hubdb as H
from . import task_relations as TR

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import TypeGuard

    from .auth import Auth, Identity
    from .models import TaskAnswer

JSON_FIELDS = ("questions", "if_no_answer", "review", "spend", "answer")
COLUMNS = ("questions", "question_topic", "if_no_answer", "review", "spend", "answer", "asked_at")
FIRST_ID = "decision"
# An open question task, as the partial index tasks_open_questions reads it (its migration spells the same predicate,
# literal statuses included, so SQLite can use it).
OPEN_SQL = (
    "questions IS NOT NULL AND answer IS NULL AND status IN (" + ",".join(f"'{s}'" for s in H.ACTIVE_STATUSES) + ")"
)
# "Waiting on <person>" is derived, never stored (spec D8, step 6b): a waiting task waits on the person who owns an open
# question task that is one of its subtasks or blockers. `waits_sql("tasks", "=?")` is that test in a filter.
_WAITS = (
    "EXISTS (SELECT 1 FROM task_relations wr JOIN tasks wq ON wq.id=wr.from_task WHERE wr.to_task={t}.id "
    "AND wr.kind IN ('parent','blocks') AND wq.questions IS NOT NULL AND wq.answer IS NULL AND wq.status IN ("
    + ",".join(f"'{s}'" for s in H.ACTIVE_STATUSES)
    + ") AND wq.owner_id {owner})"
)


def waits_sql(alias: str, owner: str = "LIKE 'human:%'") -> str:
    """SQL true when the task aliased `alias` is waiting on a person matching `owner` (a condition on the question
    task's owner: "=?", "LIKE 'human:%'")."""
    return f"({alias}.status='waiting' AND {_WAITS.format(t=alias, owner=owner)})"


def _named(
    conn: sqlite3.Connection,
    card: dict[str, Any],
    kinds: set[str],
    parent: dict[str, Any],
    seen: dict[tuple[Any, Any], bool],
) -> bool:
    """Whether private question task `card` may name its person on `parent`: its words reach `parent`'s readers
    (hubdb.subtask_flows_up, read from the rows in hand). One that may not is only "a private task" there
    (task_holds._task_hold). `seen` keeps each (party, parent) answer for the page."""
    if "parent" not in kinds or not H.task_private(conn, parent):
        return False  # only a subtask's words flow up, and only to a private parent
    parties = [p for p in (card["requester_id"], card["owner_id"]) if p]
    parties += sorted(H.reader_actors(conn, card)) if H.task_readers(card) else []
    direct = {parent.get("owner_id"), parent.get("requester_id"), *H.task_readers(parent)}

    def reads(party: Any) -> bool:
        key = (party, parent["id"])
        if key not in seen:
            seen[key] = party in direct or H.task_private_readable(conn, party, parent)
        return seen[key]

    return bool(parties) and all(reads(p) for p in parties)


def waiting_on_many(conn: sqlite3.Connection, rows: Any) -> dict[Any, str]:
    """{task id: the person it waits on} for the waiting tasks among `rows`: the owner of its first open question task
    (a subtask or a blocker), oldest first. One read per 500 tasks, so a board costs one or two: each card comes with
    what its privacy needs, so a private one costs no reads of its own."""
    rows = {r["id"]: r for r in rows if r and r.get("status") == "waiting"}
    out: dict[Any, str]
    ids: list[Any]
    seen: dict[tuple[Any, Any], bool]
    out, ids, seen = {}, list(rows), {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        found: dict[tuple[Any, Any], dict[str, Any]] = {}
        for to_task, card_id, owner, requester, private, readers, kind in conn.execute(
            "SELECT wr.to_task, wq.id, wq.owner_id, wq.requester_id, wq.private, wq.readers, wr.kind "
            f"FROM task_relations wr JOIN tasks wq ON wq.id=wr.from_task WHERE wr.to_task IN ({','.join('?' * len(part))}) "
            "AND wr.kind IN ('parent','blocks') AND wq.questions IS NOT NULL AND wq.answer IS NULL "
            f"AND wq.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) AND wq.owner_id LIKE 'human:%' "
            "ORDER BY wq.created_at, wq.rowid",
            (*part, *H.ACTIVE_STATUSES),
        ).fetchall():
            card = found.setdefault(
                (to_task, card_id),
                {
                    "id": card_id,
                    "owner_id": owner,
                    "requester_id": requester,
                    "private": private,
                    "readers": readers,
                    "kinds": set(),
                },
            )
            card["kinds"].add(kind)
        for (to_task, _), card in found.items():  # insertion order: oldest card first
            if to_task in out or card["private"] and not _named(conn, card, card["kinds"], rows[to_task], seen):
                continue
            out[to_task] = card["owner_id"]
    return out


def waiting_on(conn: sqlite3.Connection, row: dict[str, Any] | None) -> str | None:
    """The person waiting task `row` waits on (waiting_on_many), or None."""
    return waiting_on_many(conn, [row]).get((row or {}).get("id"))


def wait_card(
    conn: sqlite3.Connection, actor: str, row: dict[str, Any], person: str, said: str
) -> dict[str, Any] | None:
    """`--status waiting --on <person>` (hubdb._ask_in_subtask): a question task with no options for the person under
    `row`, the waiting note as its question. Returns the new task."""
    card = cast(
        dict[str, Any], H.task_create(conn, actor, H._ask_title(said), said, person, parent_id=row["id"], lint=False)
    )
    store(
        conn,
        card["id"],
        {"questions": [{"id": FIRST_ID, "header": "", "question": said, "why": "", "options": [], "multi": False}]},
    )
    return cast(dict[str, Any] | None, H.task(conn, card["id"]))


def _load(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return cast(dict[str, Any] | list[Any], value)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _dump(value: Any) -> str | None:
    return None if value in (None, {}, []) else json.dumps(value)


def fields(row: dict[str, Any] | None) -> dict[str, Any]:
    """The question fields of a task row, decoded ({field: value or None})."""
    return {k: _load((row or {}).get(k)) for k in JSON_FIELDS}


def view_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    """fields() as a task view carries them: the review's files without their attachments, which only the card's ask
    carries, decorated for each reader (visuals.decorate: a file the reader may not open says nothing of itself). An
    answer that copies a page's words brings their sources (body_sources), so a reader who may not open that page
    reads none of the task's text (files.readable_page_refs). Any other task carries none of them: a board of 500
    tasks sends no empty question fields."""
    if not is_question(row):
        return {}
    f = fields(row)
    if isinstance(f["review"], dict):
        f["review"] = {k: v for k, v in cast(dict[str, Any], f["review"]).items() if k != "attachments"}
    sources = (cast(dict[str, Any], f["answer"]) or {}).get("body_sources") if isinstance(f["answer"], dict) else None
    return {**f, **({"body_sources": sources} if sources else {})}


@contextmanager
def sourced(conn: sqlite3.Connection, sources: Any) -> "Generator[None]":
    """Stamp `sources` on every history event, audit event and message the close inside writes (its note, the bot's
    wake, the parent's note): each copies the answer's words, so each is redacted for a reader who may not open those
    pages."""
    marks = [
        (table, column, conn.execute(f"SELECT coalesce(max(rowid), 0) FROM {table}").fetchone()[0])
        for table, column in (("history_events", "data"), ("messages", "refs"), ("events", "detail"))
    ]
    yield
    if not sources:
        return
    for table, column, mark in marks:
        value: Any
        for rid, value in conn.execute(f"SELECT rowid, {column} FROM {table} WHERE rowid>?", (mark,)).fetchall():
            value = H._json(value, {}) or {}
            if isinstance(value, dict):
                _, value = cast(tuple[str, dict[str, Any]], H.sourced_text("", sources, value))
                conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (json.dumps(value, sort_keys=True), rid))


def is_question(row: dict[str, Any] | None) -> "TypeGuard[dict[str, Any]]":
    return bool(row and row.get("questions"))


def is_open(row: dict[str, Any] | None) -> bool:
    """A question task still waiting for its answer."""
    return is_question(row) and not row.get("answer") and row.get("status") in H.ACTIVE_STATUSES


def chat_refusal(who: Any, person: str | None) -> str | None:
    """Why a question for `person` may not be filed as a task from `who`'s turn, or None. In the Assistant chat the
    person is in, the question is asked in the reply (the Assistant's NEVER list): a chat turn acts as that person
    (backend/auth.py chat_person, via "assistant"), so a question for that same person is refused. A question for anyone
    else still files, as a background run's does."""
    if getattr(who, "via", "") == "assistant" and person and person == who.actor:
        return "Ask it in your reply: a question for the person you are chatting with is not filed as a task."
    return None


def asked_at(row: dict[str, Any]) -> str | None:
    """The question's clock: when it was asked, or last asked again (a new wording folded in)."""
    return row.get("asked_at") or row.get("created_at")


def as_ask(row: dict[str, Any]) -> dict[str, Any]:
    """The question task as the ask a question card reads: its id is the task's, from the bot that asked (the task's
    requester) to the person who owns it, its refs the stored question, and its answer target the task itself."""
    f = fields(row)
    questions: list[dict[str, Any]] = f["questions"] or []
    refs: dict[str, Any] = {
        "questions": questions,
        "task": row["id"],
        "task_question": True,
        "target": {"task": row["id"]},
    }
    if f["if_no_answer"]:
        refs["if_no_answer"] = f["if_no_answer"]
    if row.get("question_topic"):
        refs["topic"] = row["question_topic"]
    review: dict[str, Any] = f["review"] or {}
    if review.get("files"):
        refs["review"] = {"files": review["files"], **({"pick": True} if review.get("pick") else {})}
        refs["attachments"] = review.get("attachments") or []
    if f["spend"]:
        refs["spend"] = f["spend"]
    if questions and questions[0].get("id") != FIRST_ID:
        # Reworded in place (fold): an answer names the current question id (task_review.checked_answers).
        refs["asked_again_at"] = asked_at(row)
    return with_pick_sources(
        {
            "id": row["id"],
            "kind": "ask",
            "question_task": True,
            "sender_id": row["requester_id"],
            "recipient_id": row["owner_id"],
            "body": (questions[0].get("question") if questions else row["title"]),
            "created_at": asked_at(row),
            "conversation_id": row.get("conversation_id"),
            "refs": refs,
            "read_at": None,
            "delivered_at": None,
        }
    )


def store(conn: sqlite3.Connection, task_id: str, ask: dict[str, Any], topic: str | None = None) -> None:
    """Put the checked question (task_review.check_ask's shape) on the task, its clock started now."""
    review: dict[str, Any] | None = ask.get("review")
    if review:
        review = {**review, "attachments": ask.get("attachments") or []}
    conn.execute(
        "UPDATE tasks SET questions=?, if_no_answer=?, review=?, spend=?, question_topic=?, asked_at=?, "
        "answer=NULL WHERE id=?",
        (
            json.dumps(ask["questions"]),
            _dump(ask.get("if_no_answer")),
            _dump(review),
            _dump(ask.get("spend")),
            topic,
            H.now(),
            task_id,
        ),
    )


def file(conn: sqlite3.Connection, bot: str, card: dict[str, Any], parent: dict[str, Any] | None) -> None:
    """A new question task under `parent`: marked as the card the bot filed for the person (hubdb.ask_card), and the
    bot's own task blocked on it, so the answer opens that task again and the bot hears it once (hubdb._answered)."""
    if not parent:
        return
    H.event(conn, bot, H.ASK_CARD, card["id"], {"for": parent["id"], "to": card["owner_id"]})
    if H.task_private(conn, card) and not H.task_private(conn, parent):
        # A public task never waits on a private one, and the private answer never reaches it (_subtask_result_parent):
        # the bot hears it as this task's requester (task_update's requester notice).
        return
    if parent["owner_id"] == bot and card["id"] not in TR.blocker_ids(conn, parent["id"]):
        TR.add_blocker(conn, bot, parent["id"], card["id"], note="asked in a question task")


def open_for(
    conn: sqlite3.Connection,
    bot: str,
    person: str,
    *,
    parent: str | None = None,
    topic: str | None = None,
    private: bool = False,
) -> dict[str, Any] | None:
    """`bot`'s open question task for `person` that a new question on the same topic joins, or None: with a topic key,
    the one with that key under any task; without one, one under the same parent with no key either. Never across
    privacy: a private question joins only a private one, and only under the same parent."""
    topic = H.question_topic(topic)
    if not topic and not parent:
        return None
    for found in conn.execute(
        f"SELECT id FROM tasks WHERE owner_id=? AND requester_id=? AND {OPEN_SQL} ORDER BY created_at, rowid",
        (person, bot),
    ).fetchall():
        card = H.task(conn, found[0])
        if bool(H.task_private(conn, card)) != bool(private):
            continue
        same_parent = bool(parent) and cast(bool, TR.parent_of(conn, card["id"]) == parent)
        if private and not same_parent:
            continue
        if H.question_topic(card.get("question_topic")) == topic and (topic or same_parent):
            return card
    return None


def ask_plain(conn, bot, person, text, *, parent=None, why=""):
    """A plain question from `bot` to `person` (a Yes / No with no options) as a question task: under live task
    `parent`, which then waits on it, else on its own, private to the two of them. Returns the new task."""
    text = str(text or "").strip()
    live = bool(parent and parent["status"] in H.ACTIVE_STATUSES)
    card = H.task_create(
        conn,
        bot,
        H._ask_title(text)[:200] or "A question for you",
        text,
        person,
        lint=False,
        deduplicate=False,
        quiet_notice=True,
        parent_id=parent["id"] if live else None,
        private=None if live else True,
    )
    store(
        conn,
        card["id"],
        {"questions": [{"id": FIRST_ID, "header": "", "question": text, "why": why, "options": [], "multi": False}]},
    )
    if live:
        file(conn, bot, H.task(conn, card["id"]), parent)
    H.hook(
        conn,
        "needs_you.created",
        card["id"],
        lambda: {"id": card["id"], "to": person, "from": bot, "task_id": parent["id"] if live else None},
        bot,
        revision=0,
    )
    return H.task(conn, card["id"])


def ask_alone(conn, bot, person, text, *, why=""):
    """A plain question from `bot` to `person` outside any task (ask_plain). The same words while one is still open
    are not asked twice: returns the new task, or None when nothing was filed."""
    text = str(text or "").strip()
    if not text:
        return None
    for found in conn.execute(
        f"SELECT id FROM tasks WHERE owner_id=? AND requester_id=? AND {OPEN_SQL}", (person, bot)
    ).fetchall():
        card = H.task(conn, found[0])
        if " ".join(str((fields(card)["questions"] or [{}])[0].get("question") or "").split()) == " ".join(
            text.split()
        ):
            return None
    return ask_plain(conn, bot, person, text, why=why)


def answer_from_reply(conn, person, bot, conversation_id, message):
    """A person's reply in their chat with `bot` (a Slack thread's follow-up) to the bot's last line answers the bot's
    question task when it is the one open question from `bot` to `person` asked since the person last wrote there: it
    closes done with the reply as its note (closed reads a named option as the pick). Returns the task id, or None."""
    since = (
        conn.execute(
            "SELECT max(created_at) FROM messages WHERE conversation_id=? AND sender_id=? AND id<>? "
            "AND deleted_at IS NULL",
            (conversation_id, person, message["id"]),
        ).fetchone()[0]
        or ""
    )
    found = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM tasks WHERE owner_id=? AND requester_id=? AND {OPEN_SQL} "
            "AND coalesce(asked_at, created_at)>=?",
            (person, bot, since),
        )
    ]
    if len(found) != 1:
        return None
    # The reply itself reaches the bot (and wakes it): no second notice that the task is done. A task of the bot's that
    # waited on it still opens again, with its one wake there.
    H.task_update(conn, person, found[0], status="done", note=str(message.get("body") or "").strip(), heard=(bot,))
    return found[0]


def earlier(conn: sqlite3.Connection, card: dict[str, Any]) -> list[dict[str, Any]]:
    """The wordings this question task had before, oldest first ({question, at})."""
    rows = conn.execute(
        "SELECT detail FROM events WHERE action=? AND target=? ORDER BY created_at, rowid",
        (H.QUESTION_FOLDED, card["id"]),
    ).fetchall()
    return [
        {"question": d.get("was"), "at": d.get("at")} for d in (H._json(r[0], {}) or {} for r in rows) if d.get("was")
    ]


def fold(
    conn: sqlite3.Connection,
    bot: str,
    card: dict[str, Any],
    question: str,
    ask: dict[str, Any],
    *,
    title: str | None = None,
    body: str | None = None,
) -> dict[str, Any] | None:
    """A new wording of an open question task (open_for): the person keeps one task, with the latest question and
    options and the earlier wordings listed under its body. Nothing new is sent: no second Needs-you item, no
    notice, no run. Its clock restarts, so a default goes ahead the team's days after the latest wording."""
    old: list[dict[str, Any]] = fields(card)["questions"] or []
    was = str((old[0].get("question") if old else card["title"]) or "").strip()
    before = [*earlier(conn, card), {"question": was, "at": asked_at(card)}]
    # Each wording is its own question id ("decision-2"...): a pick made on the wording before is refused.
    questions = [{**q, "id": f"{FIRST_ID}-{len(before) + 1}"} if not i else q for i, q in enumerate(ask["questions"])]
    store(
        conn,
        card["id"],
        {**ask, "questions": questions},
        topic=card.get("question_topic") or H.question_topic(ask.get("topic")),
    )
    kept = before[-H.EARLIER_ASKS_KEPT :]
    lines = "\n".join(f"- {e['question']} ({str(e.get('at') or '')[:10]})" for e in reversed(kept))
    text = (body or question).strip() + f"\n\nAsked before:\n{lines}"
    new_title = title or question
    conn.execute(
        "UPDATE tasks SET title=?, body=?, version=version+1, updated_at=? WHERE id=?",
        (new_title, text, H.now(), card["id"]),
    )
    if new_title != card["title"]:
        H._task_event(conn, card["id"], bot, "title", card["title"], new_title, "")
    H._task_event(conn, card["id"], bot, "body", card.get("body"), text, "Asked again in new words")
    H.event(
        conn,
        bot,
        H.QUESTION_FOLDED,
        card["id"],
        {
            "to": card["owner_id"],
            "topic": card.get("question_topic"),
            "asks": len(before) + 1,
            "was": was,
            "at": asked_at(card),
        },
    )
    return cast(dict[str, Any] | None, H.task(conn, card["id"]))


def record(conn: sqlite3.Connection, task_id: str, value: dict[str, Any]) -> None:
    conn.execute("UPDATE tasks SET answer=? WHERE id=?", (json.dumps(value), task_id))


def reopened(conn: sqlite3.Connection, row: dict[str, Any] | None, at: str) -> None:
    """An answered question task moved back to an open status: a live question again, its answer cleared and its
    clock restarted, so its card, Needs you and the date sweeps (open_rows) see it once more."""
    if is_question(row) and row.get("answer"):
        conn.execute("UPDATE tasks SET answer=NULL, asked_at=? WHERE id=?", (at, row["id"]))


def may_answer(c: sqlite3.Connection, auth: "Auth", who: "Identity", row: dict[str, Any]) -> bool:
    """The person a question task asks (its owner), or a person acting for them: the install owner, or their manager
    on the org chart (auth.manages). Never the bot that asked, nor any other bot."""
    if who.actor == row["owner_id"]:
        return True
    if who.role == "owner":
        return True
    return (
        who.role == "human"
        and H.is_human(row["owner_id"])
        and auth.manages(c, who, "person", H.actor_id(row["owner_id"]))
    )


def _picked(questions: list[dict[str, Any]], status: str, text: str) -> tuple[dict[str, Any], str | None]:
    """The pick a close names, ({question id: [label]}, what it says besides): declined picks nothing; a note naming an
    option picks it (_label_said); done with nothing said on a Yes / No is a yes."""
    if status == "declined":
        return {}, text or None
    answers, other = _label_said(questions, text)
    if status == "done" and not text and len(questions) == 1:
        yes = next(
            (
                o["label"]
                for o in cast(list[dict[str, Any]], questions[0].get("options") or [])
                if o["label"].casefold() == "yes"
            ),
            None,
        )
        answers = {questions[0]["id"]: [yes]} if yes else {}
    return answers, other


def _label_said(questions: list[dict[str, Any]], text: str) -> tuple[dict[str, Any], str | None]:
    """The option a note names, as {question id: [label]} and what it says besides: "Yes." or "Yes. Ship it Friday"
    (Decide's Yes / No) picks Yes; anything else is a note."""
    if len(questions) != 1:
        return {}, text
    q = questions[0]
    folded = text.casefold()
    for option in cast(list[dict[str, Any]], q.get("options") or []):
        label = str(option.get("label") or "").strip()
        key = label.casefold()
        if key and (folded.rstrip(".! ") == key or folded.startswith(key + ".") or folded.startswith(key + " -")):
            rest = text[len(label) :].lstrip(".!- ").strip()
            return {q["id"]: [label]}, rest or None
    return {}, text


def closed(conn: sqlite3.Connection, actor: str, row: dict[str, Any] | None, status: str, note: str | None) -> None:
    """A question task moved to done or declined with no answer recorded (a status write, Decide's Yes / No, the
    Assistant's close): the note is the answer. A note naming an option picks it; done with nothing said on a Yes / No
    is a yes; declined is the person declining the question."""
    if not is_question(row) or status not in ("done", "declined"):
        return
    if conn.execute("SELECT answer FROM tasks WHERE id=?", (row["id"],)).fetchone()[0]:
        return  # answered first (answer, settle): that answer stands
    questions: list[dict[str, Any]] = fields(row)["questions"] or []
    text = str(note or "").strip()
    answers, other = _picked(questions, status, text)
    record(
        conn,
        row["id"],
        {
            "target": {"task": row["id"]},
            "answers": answers,
            "other": other,
            "dismiss": status == "declined",
            "by": actor,
            "at": H.now(),
            "text": text or ("Declined." if status == "declined" else "Done."),
        },
    )


def close_by_label(
    c: sqlite3.Connection, auth: "Auth", who: "Identity", row: dict[str, Any], note: str | None
) -> dict[str, Any] | None:
    """Done on an open question task that names one of its options (Decide's Yes, Catch up, a note "Tuesday. After
    the newsletter", a bare Done on a Yes / No), by someone who may answer it: answered as that pick (answer), so a
    picked spend gets its approval and files under review their verdicts. Returns answer's result, or None when the
    close names no option; the plain close then records its note (closed)."""
    if not is_open(row) or not may_answer(c, auth, who, row):
        return None
    from . import models as M

    questions: list[dict[str, Any]] = fields(row)["questions"] or []
    answers, other = _picked(questions, "done", str(note or "").strip())
    if not answers:
        return None
    return answer(c, auth, who, row["id"], M.TaskAnswer(target={"task": row["id"]}, answers=answers, other=other))


def with_pick_sources(asked: dict[str, Any]) -> dict[str, Any]:
    """A pick's options name their files only through the review's entries (file_reviews.options gives each entry its
    label and no file), so each option takes its entry's file as its source. A close that copies a picked label then
    carries that page's source too (task_review.answer_text), and a reader who may not open the page sees none of it."""
    refs: dict[str, Any] = asked["refs"]
    review: dict[str, Any] = refs.get("review") or {}
    questions: list[dict[str, Any]] = refs.get("questions") or []
    if not review.get("pick") or not questions:
        return asked
    by_label: dict[str, dict[str, Any]] = {
        e["label"]: {"file": e["file"]} for e in review.get("files") or [] if e.get("label") and e.get("file")
    }
    first: dict[str, Any] = questions[0]
    options: list[dict[str, Any]] = [
        {**o, "source": by_label[o["label"]]} if o.get("label") in by_label and not o.get("source") else o
        for o in first.get("options") or []
    ]
    return {**asked, "refs": {**refs, "questions": [{**first, "options": options}, *questions[1:]]}}


def answer(
    c: sqlite3.Connection, auth: "Auth", who: "Identity", task_id: str, body: "TaskAnswer", warn: str = ""
) -> dict[str, Any]:
    """A structured answer to a question task (POST /tasks/{id}/answers with target {task}): checked as a comment
    ask's is (task_review.checked_answers), recorded as `answer`, and the task closed with it as the note: done, or
    declined when dismissed. Files under review get their verdicts, a picked spend its approval."""
    from .store import Problem
    from . import file_reviews
    from . import task_privacy as privacy
    from .task_review import answer_words, checked_answers, readable_text

    auth.task(c, who, task_id)
    row = cast(dict[str, Any] | None, H.task(c, task_id))
    if not is_question(row):
        raise Problem("validation", "The target has no structured question", 422)
    if row["requester_id"] == who.actor:
        raise Problem("forbidden", "The asker cannot answer their own question", 403)
    if not may_answer(c, auth, who, row):
        raise Problem("forbidden", "Only the person it asks, or someone acting for them, answers this question", 403)
    asked = as_ask(row)
    questions = {q["id"]: q for q in asked["refs"]["questions"]}
    body, passed = cast("tuple[TaskAnswer, dict[str, Any]]", checked_answers(questions, asked["refs"], body))
    given: dict[str, Any] = {
        "target": {"task": task_id},
        "answers": body.answers,
        "other": body.other,
        "dismiss": body.dismiss,
        "by": who.actor,
    }
    previous = _load(row.get("answer"))
    if previous:
        # A retry with a new transport key records nothing twice; a different answer to a closed question is late.
        if all(previous.get(k) == v for k, v in given.items()):
            return {
                "answer": previous,
                "comments": H.task_comments(c, task_id, actor=privacy.actor(who)),
                "woke": False,
            }
        raise Problem("answered", "This question was already answered", 409)
    if row["status"] not in H.ACTIVE_STATUSES:
        raise Problem("answered", "This question is closed", 409)
    by = cast(
        dict[str, Any] | None,
        H.human(c, H.actor_id(who.actor)) if H.is_human(who.actor) else H.bot(c, H.actor_id(who.actor)),
    )
    name: str = readable_text((by or {}).get("name") or H.actor_id(who.actor))
    text: str = answer_words(name, questions, body, passed)
    if body.other:
        text += " " + body.other
    if warn:
        text = warn + "\n\n" + text
    value: dict[str, Any] = {
        **given,
        "at": H.now(),
        **({"not": {k: v for k, v in passed.items() if v}} if any(passed.values()) else {}),
    }
    review, spend = asked["refs"].get("review"), asked["refs"].get("spend")
    labels: list[str] = [] if body.dismiss else [label for picks in body.answers.values() for label in picks]
    if review and not body.dismiss:
        value["reviewed"] = file_reviews.verdicts(review, labels, body.other)
        file_reviews.require_readable(c, auth, who, value["reviewed"])
    if spend and spend["label"] in labels:
        value["spend_approval"] = file_reviews.spend_approval(c, who, asked, task_id, spend, [])
    text, refs = cast(
        tuple[str, dict[str, Any]],
        H.answer_text(asked, body.answers, text=text, passed=passed, refs={"answer": value}),
    )
    # The pages its words copy (body_sources) stay with them, so a reader who may not open one gets it redacted.
    value = {
        **refs["answer"],
        "text": text,
        **({"body_sources": refs["body_sources"]} if refs.get("body_sources") else {}),
    }
    record(c, task_id, value)
    with sourced(c, value.get("body_sources")):
        H.task_update(c, who.actor, task_id, status="declined" if body.dismiss else "done", note=text)
    if value.get("reviewed"):
        file_reviews.record(c, who, task_id, task_id, task_id, value["reviewed"])
    parent = TR.parent_of(c, task_id)
    H.hook(
        c,
        "needs_you.answered",
        task_id,
        lambda: {"id": task_id, "to": who.actor, "from": row["requester_id"], "task_id": parent},
        who.actor,
    )
    return {"answer": value, "comments": H.task_comments(c, task_id, actor=privacy.actor(who)), "woke": True}


def take_recommendation(
    c: sqlite3.Connection, auth: "Auth", who: "Identity", row: dict[str, Any], version: int | None = None
) -> dict[str, Any]:
    """ "Do what you recommend" on a question task: its recommended option picked as if by hand, or the task done with
    the bot's default as the note."""
    from .store import Problem
    from . import models as M
    from .views import recommendation

    if row["owner_id"] != who.actor:
        raise Problem("forbidden", "Only the person it asked takes its recommendation", 403)
    if not is_open(row):
        raise Problem("answered", "This question was already answered", 409)
    if version is not None and row["version"] != version:
        raise Problem("version_conflict", "Task changed; fetch it and retry", 409)
    asked = as_ask(row)
    rec = cast(dict[str, Any] | None, recommendation(asked, who.actor))
    if not rec:
        raise Problem("no_recommendation", "This question carries no recommendation to take", 409)
    if rec["option"]:
        question = asked["refs"]["questions"][0]
        picked = answer(
            c, auth, who, row["id"], M.TaskAnswer(target={"task": row["id"]}, answers={question["id"]: [rec["option"]]})
        )
        return {**picked, "did": rec["do"]}
    text, refs = cast(
        tuple[str, dict[str, Any]],
        H.answer_text(asked, rec["do"], text="Yes, do what you recommend: " + rec["do"]),
    )
    record(
        c,
        row["id"],
        {
            "target": {"task": row["id"]},
            "answers": {},
            "other": text,
            "dismiss": False,
            "by": who.actor,
            "at": H.now(),
            "text": text,
            "recommended": True,
            **({"body_sources": refs["body_sources"]} if refs.get("body_sources") else {}),
        },
    )
    with sourced(c, refs.get("body_sources")):  # the bot's default may copy a page's words
        H.task_update(c, who.actor, row["id"], status="done", note=text)
    return {"answer": _load(H.task(c, row["id"]).get("answer")), "woke": True, "did": rec["do"]}


# ---- dates: ask_deadlines and still_needed run on question tasks --------------------------------------------------


def open_rows(conn: sqlite3.Connection, keeper: bool = False) -> list[dict[str, Any]]:
    """Open question tasks a bot put to a person, oldest question first. A question On ice (D69) is left out, as a snoozed
    one is: a parked card is never late, so no sweep sends it back or closes it on its date. `keeper` adds Tico's own
    questions (the stuck rule's, backend/stuck_cards.py), which only the expiry sweep closes (backend/ask_deadlines.py)."""
    askers = "(requester_id LIKE 'bot:%' OR requester_id=?)" if keeper else "requester_id LIKE 'bot:%'"
    rows: list[dict[str, Any]] = H._rows(
        conn.execute(
            f"SELECT * FROM {H.TASKS} t WHERE {OPEN_SQL} AND NOT {H.ON_ICE % 't'} "
            f"AND owner_id LIKE 'human:%' AND {askers} ORDER BY coalesce(asked_at, created_at)",
            (H.KEEPER,) if keeper else (),
        )
    )
    return [r for r in rows if not H.TS.snoozed(r)]


def settle(conn: sqlite3.Connection, row: dict[str, Any], text: str, *, answers: dict[str, Any] | None = None) -> None:
    """The bot's default went ahead on its date: the keeper answers the question task with it and closes it done.
    The bot hears it once (hubdb._answered on its task, or as the requester)."""
    asked = as_ask(row)
    value: dict[str, Any] = {
        "target": {"task": row["id"]},
        "answers": answers or {},
        "other": None,
        "dismiss": False,
        "by": H.KEEPER,
        "at": H.now(),
        "if_no_answer": "went_ahead",
    }
    text, refs = cast(
        tuple[str, dict[str, Any]], H.answer_text(asked, answers or {}, text=text, refs={"answer": value})
    )
    text, refs = cast(
        tuple[str, dict[str, Any]],
        H.answer_text(asked, (asked["refs"].get("if_no_answer") or {}).get("do") or "", text=text, refs=refs),
    )
    record(
        conn,
        row["id"],
        {
            **refs["answer"],
            "text": text,
            **({"body_sources": refs["body_sources"]} if refs.get("body_sources") else {}),
        },
    )
    with sourced(conn, refs.get("body_sources")):
        H.task_update(conn, H.KEEPER, row["id"], status="done", note=text)
    H.event(
        conn,
        H.KEEPER,
        "ask.went_ahead",
        row["id"],
        {"to": row["owner_id"], "bot": row["requester_id"], "task": row["id"]},
    )


def expire(conn: sqlite3.Connection, row: dict[str, Any], text: str) -> None:
    """Its date passed and nothing goes ahead on its own: the question task closes, and the bot hears why."""
    asked = as_ask(row)
    _, refs = cast(
        tuple[str, dict[str, Any]],
        H.answer_text(asked, (asked["refs"].get("if_no_answer") or {}).get("do") or "", text=text),
    )
    # No answer, but how it ended: what the note says the bot would have done keeps its sources (view_fields).
    record(
        conn,
        row["id"],
        {
            "target": {"task": row["id"]},
            "answers": {},
            "other": None,
            "dismiss": False,
            "by": H.KEEPER,
            "at": H.now(),
            "text": text,
            "if_no_answer": "expired",
            **({"body_sources": refs["body_sources"]} if refs.get("body_sources") else {}),
        },
    )
    with sourced(conn, refs.get("body_sources")):  # the note names what the bot said it would do
        H.task_close(conn, H.KEEPER, row["id"], text, strict=False)
    H.event(
        conn, H.KEEPER, "ask.expired", row["id"], {"to": row["owner_id"], "bot": row["requester_id"], "task": row["id"]}
    )


def send_back(conn: sqlite3.Connection, row: dict[str, Any], why: str) -> None:
    """No answer and no date: the question task closes quietly and goes on its bot's roundup (still_needed), with no
    wake of its own; the bot's task blocked on it is unblocked without one."""
    H.task_close(
        conn, H.KEEPER, row["id"], why, quiet=True, strict=False, closing_tasks=tuple(TR.blocked_ids(conn, row["id"]))
    )


# ---- the waits migration 20261106112609_derived_waiting set aside ---------------------------------------------------


def move_waits(conn: sqlite3.Connection) -> None:
    """Each open task the derived-waiting migration found waiting on someone (`task_waits_moved`), once, at the first
    start after it (Store.initialize): a person becomes an open question task for them with no options under the task,
    its waiting note as the question (hubdb._ask_in_subtask, as `--status waiting --on <person>` files one now), unless
    an open question to them already holds it; a bot is the blocker it owns when there is one, else a line on the note.
    Each row in its own savepoint: one that fails is logged and dropped, never retried at every start. The table goes
    when it is empty."""
    import logging

    # Not H._has_table: a pooled connection remembers tables it saw, and this one is dropped below.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_waits_moved'").fetchone():
        return
    for task_id, on in conn.execute(
        "SELECT task_id, waiting_on FROM task_waits_moved ORDER BY created_at, task_id"
    ).fetchall():
        conn.execute("SAVEPOINT move_wait")
        try:
            _move_wait(conn, task_id, on)
        except Exception:  # one task's old wait never stops a start
            conn.execute("ROLLBACK TO move_wait")
            logging.getLogger(__name__).exception("waiting_on of task %s not moved", task_id)
        conn.execute("RELEASE move_wait")
        conn.execute("DELETE FROM task_waits_moved WHERE task_id=?", (task_id,))
    conn.execute("DROP TABLE IF EXISTS task_waits_moved")


def _move_wait(conn: sqlite3.Connection, task_id: str, on: str) -> None:
    from . import task_holds

    row = cast(dict[str, Any] | None, H.task(conn, task_id))
    if not row or row["status"] not in H.ACTIVE_STATUSES:
        return
    said = str(row.get("note") or "").strip()
    if H.is_human(on):
        # The note a wait set: "Blocked on <name>: <ask>", the name as it was then.
        if said.startswith("Blocked on ") and ": " in said:
            said = said.split(": ", 1)[1]
        # An open question task of theirs already holds it: move_asks, which runs first, made one from any open ask.
        asked = H._waits_on(conn, row, on)
        if H.is_bot(row["owner_id"]) and not asked:
            conn.execute("SAVEPOINT move_wait_card")
            try:
                card = cast(dict[str, Any], H._ask_in_subtask(conn, row["owner_id"], row, on, said or row["title"]))
            except H.Refused as refused:
                # A cap, reach or privacy rule refuses the card: the wait is kept as words on the note instead.
                conn.execute("ROLLBACK TO move_wait_card")
                conn.execute("RELEASE move_wait_card")
                _note_line(conn, row, on, said)
                H._task_event(
                    conn, task_id, H.KEEPER, "waiting_on", on, None, f"No question task: {refused.detail}"[:500]
                )
                return
            conn.execute("RELEASE move_wait_card")
            H._task_event(conn, task_id, H.KEEPER, "waiting_on", on, None, f"Asked in question task {card['id']}")
            task_holds.refresh(conn, task_id)
            return
        H._task_event(
            conn, task_id, H.KEEPER, "waiting_on", on, None, "An open question to them holds it" if asked else ""
        )
        return
    held = any(b["owner_id"] == on for b in TR.live_blockers(conn, task_id, H.ACTIVE_STATUSES))
    if not held and H.is_bot(on):
        _note_line(conn, row, on, said)
    H._task_event(conn, task_id, H.KEEPER, "waiting_on", on, None, "Its blocker holds it" if held else "")


# ---- the asks migration 20261106131713_asks_to_question_tasks set aside ----------------------------------------------


def move_asks(conn):
    """Each open ask the asks migration found (`asks_moved`), once, at the first start after it (Store.initialize),
    before move_waits: an ask to a person on a live task becomes a question subtask for them, or joins the open
    question card they already have there; a loose ask to a person becomes a question task of its own; an ask to a bot
    becomes a DM between the two, flagged needs-action. The old message stays in its thread, superseded, so it is
    history and in no list. Each row in its own savepoint: one that cannot move keeps its words on record (`_kept`)
    instead, and one that cannot be kept is superseded with its reason (`_lost`). A row is never tried again at every
    start. The table goes when it is empty."""
    import logging

    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='asks_moved'").fetchone():
        return
    log = logging.getLogger(__name__)
    for (mid,) in conn.execute("SELECT message_id FROM asks_moved ORDER BY created_at, message_id").fetchall():
        conn.execute("SAVEPOINT move_ask")
        try:
            _move_ask(conn, mid)
        except Exception as exc:  # one old ask never stops a start
            conn.execute("ROLLBACK TO move_ask")
            log.exception("ask %s not moved", mid)
            why = str(getattr(exc, "detail", "") or exc)
            try:
                _kept(conn, mid, why)
            except Exception:
                conn.execute("ROLLBACK TO move_ask")
                log.exception("ask %s not kept either", mid)
                _lost(conn, mid, why)
        conn.execute("RELEASE move_ask")
        conn.execute("DELETE FROM asks_moved WHERE message_id=?", (mid,))
    conn.execute("DROP TABLE IF EXISTS asks_moved")


def _open_ask(conn, mid):
    """The ask message `mid`, while it is still open, else None."""
    m = H.message(conn, mid)
    if not m or m.get("kind") != "ask" or m.get("answered_by_id") or m.get("superseded_at"):
        return None
    if conn.execute(
        "SELECT 1 FROM messages WHERE in_reply_to=? AND kind='answer' AND deleted_at IS NULL", (mid,)
    ).fetchone():
        return None
    return m


def _retire(conn, m, task_id, how, data=None):
    """The old ask leaves every list: superseded, with where it went, on its task's record and in the log."""
    refs = {**(m.get("refs") or {}), "moved": {"how": how, **(data or {})}}
    conn.execute(
        "UPDATE messages SET superseded_at=?, refs=? WHERE id=?", (H.now(), json.dumps(refs, sort_keys=True), m["id"])
    )
    if task_id and H.task(conn, task_id):
        H._task_event(conn, task_id, H.KEEPER, "ask", m["id"], how, (data or {}).get("note", "")[:500])
    H.event(
        conn,
        H.KEEPER,
        "ask.moved",
        m["id"],
        {"how": how, "task": task_id, "from": m["sender_id"], "to": m["recipient_id"], **(data or {})},
    )
    H.recount(conn, [m["sender_id"]])


def _questions(m):
    """The ask's question as a question task stores it: its structured questions, else its words as one question with
    no options (Yes / No)."""
    refs = m.get("refs") or {}
    found = refs.get("questions") if isinstance(refs.get("questions"), list) else None
    if found:
        return [dict(q) for q in found]
    return [
        {
            "id": FIRST_ID,
            "header": "",
            "question": str(m.get("body") or "").strip(),
            "why": "",
            "options": [],
            "multi": False,
        }
    ]


def _shape(m):
    """The ask as a question task takes it (store's shape), its topic too."""
    refs = m.get("refs") or {}
    return {
        "questions": _questions(m),
        "if_no_answer": refs.get("if_no_answer"),
        "review": refs.get("review"),
        "spend": refs.get("spend"),
        "attachments": refs.get("attachments"),
        "topic": refs.get("topic"),
    }


def _store_ask(conn, card_id, m):
    """The ask's question, default, files under review and spend on question task `card_id`, its clock the ask's."""
    ask = _shape(m)
    store(conn, card_id, ask, topic=H.question_topic(ask["topic"]))
    conn.execute("UPDATE tasks SET asked_at=? WHERE id=?", (H.ask_clock(m), card_id))


def _move_ask(conn, mid):
    m = _open_ask(conn, mid)
    if not m:
        return
    conv = H.conversation(conn, m["conversation_id"])
    task_id = H.message_task_id(m, conv)
    row = H.task(conn, task_id) if task_id else None
    sender, to = m["sender_id"], m["recipient_id"]
    if m.get("expires_at") and m["expires_at"] <= H.now():
        return _retire(conn, m, task_id, "expired")  # it had left Needs you on its date
    if H.is_bot(to):
        return _to_dm(conn, m, row)
    if not H.is_human(to):
        return _kept(conn, mid, "asked of the system, which has no inbox")
    if task_id and not row:
        return _retire(conn, m, task_id, "task gone")
    if row and row["status"] not in H.ACTIVE_STATUSES:
        return _retire(conn, m, task_id, "task ended")  # an ask on an ended task ends with it
    if row:
        card = _open_card(conn, row, to)
        if card:
            # One card per person per task: the ask joins it as a comment from the one who asked, and adds what the card
            # lacks.
            _join(conn, card, m)
            H.task_comment(conn, sender, card["id"], str(m.get("body") or "").strip() or card["title"], wake=False)
            return _retire(conn, m, task_id, "joined", {"task_id": card["id"]})
    card, how = _ask_task(conn, m, row)
    _retire(conn, m, task_id, how, {"task_id": card["id"]})
    if row:
        from . import task_holds

        task_holds.refresh(conn, row["id"])


def _open_card(conn, row, person):
    """The open question task `person` owns under or blocking task `row`, oldest first, or None."""
    found = conn.execute(
        "SELECT wq.id FROM task_relations wr JOIN tasks wq ON wq.id=wr.from_task WHERE wr.to_task=? "
        "AND wr.kind IN ('parent','blocks') AND wq.owner_id=? AND wq.questions IS NOT NULL AND wq.answer IS NULL "
        f"AND wq.status IN ({','.join(repr(s) for s in H.ACTIVE_STATUSES)}) ORDER BY wq.created_at, wq.rowid LIMIT 1",
        (row["id"], person),
    ).fetchone()
    return H.task(conn, found[0]) if found else None


def _join(conn, card, m):
    """An ask that joins an open card adds what the card lacks: its options (the recommended one too), its default and
    its files under review. The card's own question stays as it is; the ask's words come in as its comment."""
    have, ask = fields(card), _shape(m)
    questions = [dict(q) for q in (have["questions"] or [])]
    if questions and ask["questions"]:
        first, asked = questions[0], ask["questions"][0]
        if not first.get("options") and asked.get("options"):
            first["options"] = [dict(o) for o in asked["options"]]
        picks = {o.get("label") for o in asked.get("options") or [] if o.get("recommended")}
        if picks and not any(o.get("recommended") for o in first.get("options") or []):
            for o in first.get("options") or []:
                if o.get("label") in picks:
                    o["recommended"] = True
    files = {**ask["review"], "attachments": ask.get("attachments") or []} if ask["review"] else None
    review = have["review"] or files
    conn.execute(
        "UPDATE tasks SET questions=?, if_no_answer=?, review=? WHERE id=?",
        (json.dumps(questions), _dump(have["if_no_answer"] or ask["if_no_answer"]), _dump(review), card["id"]),
    )


def _ask_task(conn, m, row):
    """A question task for the person the ask was put to: the open one on its topic that the person already has
    (open_for, as the live path folds it), else under its task when it has a live one (a private task's makes a private
    one, which only its readers see), else on its own and private to the two of them, as the ask was. Returns (task, how)."""
    sender, person = m["sender_id"], m["recipient_id"]
    questions = _questions(m)
    question = str(questions[0].get("question") or m.get("body") or "").strip()
    title = H._ask_title(question)[:200] or "A question for you"
    body = str(m.get("body") or "").strip() or question
    live = bool(row and row["status"] in H.ACTIVE_STATUSES)
    topic = H.question_topic((m.get("refs") or {}).get("topic"))
    found = open_for(
        conn,
        sender,
        person,
        parent=row["id"] if row else None,
        topic=topic,
        private=not row or H.task_private(conn, row),
    )
    if found:
        # The new wording folds into the open question task on this topic (question_tasks.fold): nothing new is sent.
        fold(conn, sender, found, question, _shape(m), title=title, body=body)
        return found, "folded"
    if row:
        conn.execute("SAVEPOINT ask_task")
        try:
            card = H.task_create(
                conn, sender, title, body, person, parent_id=row["id"], lint=False, deduplicate=False, quiet_notice=True
            )
        except H.Refused:
            # A subtask of an ended task, or one the rules refuse there: a question task of its own instead.
            conn.execute("ROLLBACK TO ask_task")
            card = None
        conn.execute("RELEASE ask_task")
        if card:
            _store_ask(conn, card["id"], m)
            if live:
                conn.execute("SAVEPOINT ask_file")
                try:
                    file(conn, sender, H.task(conn, card["id"]), row)
                except H.Refused:
                    # A private task whose readers the card does not share takes no blocker on it (a private
                    # dependency needs the same audience): the bot hears the answer on the card, as its requester.
                    conn.execute("ROLLBACK TO ask_file")
                conn.execute("RELEASE ask_file")
            return card, "question_task"
        # Its task's title only where the person reads that task: a private one's stays off.
        readable = not H.task_private(conn, row) or H.task_private_readable(conn, person, row)
        body = f"{body}\n\nAbout task {row['id']}: {row['title']}" if readable else body
    card = H.task_create(
        conn,
        sender,
        title,
        body,
        person,
        lint=False,
        deduplicate=False,
        quiet_notice=True,
        private=True if not row or H.task_private(conn, row) else None,
    )
    _store_ask(conn, card["id"], m)
    return card, "question_task"


def _to_dm(conn, m, row):
    """An ask to a bot as what bots do now (D9): a message in the DM between the two, flagged needs-action, so the bot
    it asks takes it on its next run now and its reply wakes the asker."""
    sender, bot = m["sender_id"], m["recipient_id"]
    conv = H._pair_conversation(conn, sender, bot, "chat", allow_held=True)
    refs = {"needs_action": True, "asked_before": m["id"], **({"task": row["id"]} if row else {})}
    sent = H.say(conn, sender, bot, str(m.get("body") or "").strip(), conversation_id=conv["id"], refs=refs)
    _retire(conn, m, row["id"] if row else None, "dm", {"message_id": sent["id"]})


def _kept(conn, mid, why):
    """An open ask that could not move: retired all the same, its words kept where its people read them, on its task as
    a quiet keeper line, and the reason in the log (`ask.not_moved`)."""
    m = H.message(conn, mid)
    if not m or m.get("kind") != "ask" or m.get("superseded_at"):
        return
    task_id = H.message_task_id(m, H.conversation(conn, m["conversation_id"]))
    row = H.task(conn, task_id) if task_id else None
    said = str(m.get("body") or "").strip()
    if row and row["status"] in H.ACTIVE_STATUSES and said:
        line = (
            f"An open question from {H.actor_name(conn, m['sender_id'])} to {H.actor_name(conn, m['recipient_id'])} "
            f"did not move to a question task ({why}): {said}"
        )[:4000]
        H.task_comment(conn, H.KEEPER, task_id, line, wake=False)
    H.event(conn, H.KEEPER, "ask.not_moved", mid, {"why": why[:500], "task": task_id})
    _retire(conn, m, task_id, "kept", {"note": why})


def _lost(conn, mid, why):
    """An open ask that neither moved nor could be kept: superseded all the same, with the reason in the log. It writes
    only the message and its event, not a task, so the failure above is not needed again."""
    conn.execute("UPDATE messages SET superseded_at=? WHERE id=? AND superseded_at IS NULL", (H.now(), mid))
    H.event(conn, H.KEEPER, "ask.not_moved", mid, {"why": why[:500], "task": None})


def _note_line(conn: sqlite3.Connection, row: dict[str, Any], on: str, said: str) -> None:
    """ "Waiting on <name>: <note>" on the task's note, once: the old wait kept as words where nothing can hold it."""
    line = f"Waiting on {H.actor_name(conn, on)}"
    if not said.startswith(line):
        note = (line + (f": {said}" if said else ""))[:1000]
        conn.execute("UPDATE tasks SET note=? WHERE id=?", (note, row["id"]))
        H._task_event(conn, row["id"], H.KEEPER, "note", row.get("note"), note, "")
