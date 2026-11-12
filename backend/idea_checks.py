"""Ideas: say it once (Product task #209, Engineering #227).

Product's Ideas is an ordinary stage: what is not snoozed moves on to Spec by itself when Spec has room, oldest first.

    filed(conn, row, by, ts)       a task entered Ideas new: its duplicate check is owed, and a bot's own idea is snoozed
    holds(conn, row) -> bool       Jev has not answered for this idea yet, and WAIT_S has not passed: it does not move
    filed_snooze(snooze) -> bool   the snooze was written as a bot filed its own idea; only a person ends it
    run(store, now) -> int         the Heartbeat's duplicate pass: one Jev call for up to BATCH owed ideas

Filing. A person's idea, or one a bot filed for a person from their message (`--for` and `--from-message`, checked in
app.task_create), has that person as its requester and is not snoozed. Any other idea (a bot's own) is snoozed with no
end in the same write, marked `filed`: grayed at the bottom of Ideas until a person clicks its moon. A bot never ends
that snooze (task_engine.unsnooze, snooze).

The duplicate check. Every idea filed into Ideas is owed one check (`idea_checks` row). The never-stuck pass reads
tables only, so the check is its own Heartbeat step (`run`): one call on `questions/idea-check.json` for up to BATCH
ideas, two questions each (`same`: which open or recent Product task, or another idea in the pass, it is the same as,
or new; `merge`: which it should be closed into, or none). When both name the same task at SURE or over, the idea is
closed as a duplicate of it and linked, its person is added to the task's requesters, and the task says who also asked.
A person's idea that matches a bot's idea still in Ideas closes the bot's idea instead. Anything else (new, under SURE,
a failed or missing model, no answer within WAIT_S) leaves the idea as it was: the check fails open, and no other model
is asked. While the check is owed and younger than WAIT_S the idea waits on Jev (waiting_on: `jev`); after that it
moves whatever happened to the call.
"""

import logging
from typing import TYPE_CHECKING, Any, Callable

from clients import judge as J

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger("tico.idea_checks")

IDEAS = "product-ideas"
PIPELINE = "product"
QUESTION_SET = "idea-check"
WAIT_S = 120  # an idea waits on Jev at most this long after it was filed, then moves on whatever the check did
CALL_S = 90  # the call's own deadline, inside WAIT_S
SURE = 0.85  # both answers name the same task at this confidence or over: the idea is a duplicate
BATCH = 20  # ideas in one call: two questions each, Jev's forty a call
RECENT_DAYS = 30  # a Product task that ended this recently is still offered as the one an idea repeats
OPTIONS = 100  # tasks offered as the ones an idea may repeat, newest first
TEXT = 1200  # characters of an idea's text Jev reads
TITLE = 200
FILED_WHY = "A bot's idea: unsnooze to send it on."
# Outcomes kept on the row: the idea is new or Jev was unsure, it was closed as a duplicate, it closed a bot's idea that
# repeated it, the call failed or was not made, the idea was not checked (private, left Ideas, ended).
NEW, DUPLICATE, KEPT, FAILED, SKIPPED = "new", "duplicate", "kept", "failed", "skipped"
# The engine a test sets: (state, questions, label) -> {"answers": {...}, ...}. None asks the server's Jev.
ENGINE: "Callable[[Row, Row, str], Row] | None" = None


def _h() -> Any:
    from . import hubdb

    return hubdb


def is_ideas(stage_row: Row | None) -> bool:
    return bool(stage_row) and (stage_row or {}).get("id") == IDEAS


def filed_snooze(snooze: Row | None) -> bool:
    return bool(snooze) and bool((snooze or {}).get("filed"))


def filed(conn: "sqlite3.Connection", row: Row, by: str, ts: str) -> None:
    """A task entered Ideas new (task_engine.enter): its duplicate check is owed, and when nobody person asked for it,
    it is snoozed with no end in the same write, marked `filed`."""
    H = _h()
    visit: Row = row["visit"]
    conn.execute(
        "INSERT OR IGNORE INTO idea_checks (task_id, visit_id, owed_at) VALUES (?,?,?)", (row["id"], visit["id"], ts)
    )
    if H.is_human(row.get("requester_id")) or row.get("snooze"):
        return
    conn.execute(
        "INSERT INTO snoozes (id, task_id, visit_id, by_id, why, until_at, created_at, filed) VALUES (?,?,?,?,?,?,?,1)",
        (H.new_id(), row["id"], visit["id"], str(row.get("requester_id") or by), FILED_WHY, None, ts),
    )
    from . import task_engine as E

    E._event(conn, row["id"], str(by), "snooze", None, None, FILED_WHY, {"filed": True})  # pyright: ignore[reportPrivateUsage]


def holds(conn: "sqlite3.Connection", row: Row, now: str | None = None) -> bool:
    """Whether an idea in Ideas still waits on its duplicate check: owed, and filed less than WAIT_S ago."""
    if not is_ideas(row.get("stage")):
        return False
    found = conn.execute(
        "SELECT owed_at FROM idea_checks WHERE task_id=? AND checked_at IS NULL", (row["id"],)
    ).fetchone()
    if not found:
        return False
    H = _h()
    return str(now or H.now()) < str(H.shift(found[0], seconds=WAIT_S))


# ----------------------------------------------------------------------------- the pass
_OWED = "SELECT task_id FROM idea_checks WHERE checked_at IS NULL ORDER BY owed_at, rowid LIMIT ?"
# Product tasks an idea may repeat: open ones, and ones that ended in the last RECENT_DAYS, newest first. Private tasks
# are never offered: Jev is shown nothing a reader of the idea may not see.
_OFFERED = (
    "SELECT id, number, title, status, stage_id, requester_id FROM tasks WHERE pipeline_id=? AND private=0 "
    "AND (status='open' OR COALESCE(closed_at, done_at, updated_at) >= ?) ORDER BY created_at DESC, id DESC LIMIT ?"
)


def _mark(conn: "sqlite3.Connection", task_id: str, outcome: str, at: str, **fields: Any) -> None:
    conn.execute(
        "UPDATE idea_checks SET checked_at=?, outcome=?, same_id=?, confidence=?, call_id=? "
        "WHERE task_id=? AND checked_at IS NULL",
        (at, outcome, fields.get("same_id"), fields.get("confidence"), fields.get("call_id"), task_id),
    )


def _option(task: Row) -> str:
    return f"t{task['number']}" if task.get("number") is not None else f"id{task['id']}"


def _label(task: Row) -> str:
    title = " ".join(str(task.get("title") or "").split())[:TITLE]
    return f"#{task['number']}: {title}" if task.get("number") is not None else title


def questions_for(qset: Row, ideas: list[Row], offered: list[Row]) -> tuple[Row, dict[str, Row]]:
    """Two questions per idea, `same_<n>` and `merge_<n>`, over the offered tasks and the pass's other ideas (never the
    idea itself). Returns the questions and each option's task."""
    pool: dict[str, Row] = {}
    for task in [*offered, *ideas]:
        pool.setdefault(_option(task), task)
    questions: Row = {}
    for n, idea in enumerate(ideas):
        mine = {name: _label(task) for name, task in pool.items() if task["id"] != idea["id"]}
        for qid in ("same", "merge"):
            base = qset["questions"][qid]
            asked = dict(base["instructions"])
            asked["question"] = asked["question"].replace("the idea", f"`ideas[{n}]`", 1)
            questions[f"{qid}_{n}"] = J.with_options({**base, "instructions": asked}, mine)
    return questions, pool


def _engine(store: Any) -> "Callable[[Row, Row, str], Row] | None":
    if ENGINE is not None:
        return ENGINE
    from . import judge

    J_: Any = judge
    with store.read() as c:
        return J_.server_engine(store, c, J_.primary_engine(store.settings, store))


def _ask(store: Any, state: Row, questions: Row, label: str) -> tuple[Row | None, str | None]:
    """One call, within CALL_S: (checked answers, None), or (None, why it failed)."""
    asks = _engine(store)
    if asks is None:
        return None, "unconfigured"
    try:
        with J.within(CALL_S):
            result = asks(state, questions, label)
        return {"answers": J.check_answers(result.get("answers"), questions), **result}, None
    except J.JudgeError as exc:
        return None, J.error_code(exc)
    except Exception as exc:  # noqa: BLE001 - a fake, a provider bug: the ideas go on as they are
        LOG.warning("The idea check failed: %s", type(exc).__name__)
        return None, "error"


def run(store: Any, now: str | None = None) -> int:
    """The duplicate pass on the Heartbeat: read up to BATCH owed checks in a short read, ask Jev once with no
    transaction held, then record each idea's outcome in one short write and nudge it on. Returns how many ideas were
    checked."""
    from . import task_engine as E

    H = _h()
    at = str(now or H.now())
    with store.read() as c:
        owed = [str(found[0]) for found in c.execute(_OWED, (BATCH,)).fetchall()]
        rows = [E.task(c, task_id) for task_id in owed]
        ideas: list[Row] = [
            row for row in rows if row and row["status"] == "open" and is_ideas(row["stage"]) and not row.get("private")
        ]
        since = str(H.shift(at, days=-RECENT_DAYS))
        mine = {row["id"] for row in ideas}
        offered = [
            dict(zip(("id", "number", "title", "status", "stage_id", "requester_id"), found, strict=True))
            for found in c.execute(_OFFERED, (PIPELINE, since, OPTIONS)).fetchall()
        ]
        offered = [task for task in offered if task["id"] not in mine]
    skipped = [task_id for task_id in owed if task_id not in {row["id"] for row in ideas}]
    if not owed:
        return 0
    answers: Row | None = None
    why: str | None = None
    call_id: str | None = None
    pool: dict[str, Row] = {}
    if ideas:
        qset = J.load_set(QUESTION_SET)
        questions, pool = questions_for(qset, ideas, offered)
        state = {
            "ideas": [
                {"title": str(row.get("title") or "")[:TITLE], "text": str(row.get("body") or "")[:TEXT]}
                for row in ideas
            ],
            "tasks": [_label(task) for task in pool.values()],
        }
        result, why = _ask(store, state, questions, qset["label"])
        answers = (result or {}).get("answers")
        detail: Row = {"label": qset["label"], "ideas": len(ideas), "questions": len(questions), "options": len(pool)}
        if result is not None:
            from . import judge

            J_: Any = judge
            detail.update(J_.audit_detail(result), answers=J.summary(answers))
        if why:
            detail["error"] = why
        with store.transaction() as c:
            call_id = str(H.event(c, H.KEEPER, "judge.call", qset["label"], detail)["id"])
    with store.transaction() as c:
        for task_id in skipped:
            _mark(c, task_id, SKIPPED, at)
        for n, idea in enumerate(ideas):
            _apply(c, idea, n, answers, pool, at, call_id)
    return len(ideas)


def _sure(answers: Row | None, qid: str) -> tuple[str | None, float]:
    found: Row | None = (answers or {}).get(qid)
    if not found:
        return None, 0.0
    return str(found.get("choice")), float(found.get("confidence") or 0)


def _apply(
    conn: "sqlite3.Connection", idea: Row, n: int, answers: Row | None, pool: dict[str, Row], at: str, call: str | None
) -> None:
    """One idea's outcome, in the pass's write: its check is marked, a sure duplicate is closed, and the idea is nudged
    on (task_engine.advance) when it stays."""
    from . import task_engine as E

    row = E.task(conn, idea["id"])
    owed = conn.execute("SELECT 1 FROM idea_checks WHERE task_id=? AND checked_at IS NULL", (idea["id"],)).fetchone()
    if not owed:
        return
    if not row or row["status"] != "open" or not is_ideas(row["stage"]):
        _mark(conn, idea["id"], SKIPPED, at, call_id=call)
        return
    if answers is None:
        _mark(conn, idea["id"], FAILED, at, call_id=call)
        E.advance(conn, idea["id"])
        return
    same, sure_same = _sure(answers, f"same_{n}")
    merge, sure_merge = _sure(answers, f"merge_{n}")
    other = pool.get(same or "")
    if not other or same != merge or min(sure_same, sure_merge) < SURE:
        _mark(conn, idea["id"], NEW, at, same_id=(other or {}).get("id"), confidence=sure_same, call_id=call)
        E.advance(conn, idea["id"])
        return
    target = E.task(conn, str(other["id"]))
    if not target or target["id"] == row["id"]:
        _mark(conn, idea["id"], NEW, at, call_id=call)
        E.advance(conn, idea["id"])
        return
    H = _h()
    # A person's idea is never the one closed for a bot's idea still waiting in Ideas: the bot's goes.
    if (
        H.is_human(row.get("requester_id"))
        and not H.is_human(target.get("requester_id"))
        and target["status"] == "open"
        and is_ideas(target["stage"])
    ):
        _close(conn, target, row)
        _mark(conn, idea["id"], KEPT, at, same_id=target["id"], confidence=min(sure_same, sure_merge), call_id=call)
        E.advance(conn, idea["id"])
        return
    _close(conn, row, target)
    _mark(conn, idea["id"], DUPLICATE, at, same_id=target["id"], confidence=min(sure_same, sure_merge), call_id=call)


def _close(conn: "sqlite3.Connection", dup: Row, original: Row) -> None:
    """Close `dup` as a duplicate of `original`, linked; a person who asked for it is added to the original's requesters,
    and the original says who also asked."""
    from . import task_engine as E
    from . import task_relations as TR

    H = _h()
    number = f"#{original['number']}" if original.get("number") is not None else "another task"
    TR.relate(conn, H.KEEPER, dup["id"], original["id"], "duplicate_of")
    E.move(conn, dup["id"], E.CLOSED, E.SYSTEM, f"Duplicate of {number}: Jev found the same idea")
    asker = dup.get("requester_id")
    if H.is_human(asker):
        from . import roadmaps

        if not conn.execute(
            "SELECT 1 FROM task_requesters WHERE task_id=? AND actor=?", (original["id"], asker)
        ).fetchone():
            roadmaps.add_requester(conn, original["id"], {"source": "manual"}, actor=asker)
    name = H.actor_name(conn, asker) if asker else "Someone"
    filed = f"#{dup['number']}" if dup.get("number") is not None else "a duplicate"
    H.task_comment(
        conn,
        H.KEEPER,
        original["id"],
        f"Also asked by {name}: {filed}, \"{' '.join(str(dup.get('title') or '').split())[:TITLE]}\", closed as the same idea.",
        wake=False,
    )


def state_of(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """The idea's check row, for the task page and tests."""
    cursor = conn.execute("SELECT * FROM idea_checks WHERE task_id=?", (task_id,))
    found = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], found, strict=True)) if found else None
