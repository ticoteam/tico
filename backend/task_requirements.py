# pyright: strict
"""What a task needs before it leaves a stage (docs/pipelines-v3.md, "The nine requirement kinds").

A requirement sits on a stage (every task that visits it) or on one visit (this task, this time). Each kind answers one
question for a task's open visit: met, waiting on something named, or not met and why. A kind reads tables only. It never
calls GitHub, Jev or a computer, so a move and the never-stuck check can't be stalled by the outside.

What met a requirement is its newest answer on the visit that is not undone (`answers`):

- `done`: the owner said the stage's work is finished (owner_done).
- `ok`: the named person approved. `no`: they sent it back, with notes. `again`: the owner asks them to look again.
- anything else on an `answer` requirement: the choice the person picked, or what they typed.
- `pushed_past`: someone moved the task on without it, with the reason in `notes`. Any kind.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

MET, WAITING, NOT_MET = "met", "waiting", "not_met"
# What a requirement waits on when it is not a person or a task: `Standing.on` for the kinds that read saved facts.
JEV, GITHUB = "jev", "github"
PHASE_ONE = ("owner_done", "ok", "answer", "linked_done")
KIND_NAMES = {
    "owner_done": "Owner says done",
    "ok": "OK from someone",
    "page": "A page",
    "pr_open": "PR open",
    "checks_pass": "Checks pass",
    "pr_merged": "PR merged",
    "in_release": "In a release",
    "linked_done": "Linked work done",
    "answer": "Answer from someone",
}
# Kinds that can never be pushed past, whatever the row says (C3).
NEVER_PUSHED_PAST = ("pr_merged", "in_release")
# Kinds whose "Who OKs" switch means something. Elsewhere it reads as `none`.
OK_KINDS = ("ok", "page", "answer")


@dataclass
class Standing:
    """Where one requirement stands for one visit. `on` is who or what it waits on: an actor, `jev`, `github`, or a
    task id. `line` is one sentence: what is missing, or what met it."""

    requirement: Row
    state: str
    on: str | None = None
    line: str = ""

    @property
    def met(self) -> bool:
        return self.state == MET

    @property
    def name(self) -> str:
        return name_of(self.requirement)


def name_of(requirement: Row) -> str:
    """What a requirement is called on a task: its own line if it has one, else its kind."""
    return (requirement.get("line") or "").strip() or KIND_NAMES.get(requirement["kind"]) or requirement["kind"]


def _rows(cursor: "sqlite3.Cursor") -> list[Row]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _loaded(row: Row) -> Row:
    for key in ("settings", "shows", "choices"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except ValueError:
                row[key] = None
    row["settings"] = row.get("settings") or {}
    return row


def on_stage(conn: "sqlite3.Connection", stage_id: str) -> list[Row]:
    """A stage's requirements, in order."""
    return [
        _loaded(r)
        for r in _rows(
            conn.execute(
                "SELECT * FROM requirements WHERE stage_id=? AND withdrawn_at IS NULL ORDER BY position, created_at, id",
                (stage_id,),
            )
        )
    ]


def on_visit(conn: "sqlite3.Connection", visit_id: str) -> list[Row]:
    """The requirements added to one visit, oldest first."""
    return [
        _loaded(r)
        for r in _rows(
            conn.execute(
                "SELECT * FROM requirements WHERE visit_id=? AND withdrawn_at IS NULL ORDER BY created_at, id",
                (visit_id,),
            )
        )
    ]


def for_visit(conn: "sqlite3.Connection", visit: Row) -> list[Row]:
    """Everything a task needs to leave its open visit: the stage's requirements, then the ones added to this visit."""
    return on_stage(conn, visit["stage_id"]) + on_visit(conn, visit["id"])


def get(conn: "sqlite3.Connection", requirement_id: str) -> Row | None:
    rows = _rows(conn.execute("SELECT * FROM requirements WHERE id=?", (requirement_id,)))
    return _loaded(rows[0]) if rows else None


def newest_answers(conn: "sqlite3.Connection", visit_id: str) -> dict[str, Row]:
    """{requirement id: its newest answer on this visit that is not undone}."""
    out: dict[str, Row] = {}
    for row in _rows(
        conn.execute(
            "SELECT * FROM answers WHERE visit_id=? AND undone_at IS NULL ORDER BY created_at, id",
            (visit_id,),
        )
    ):
        out[row["requirement_id"]] = row
    return out


def _filed(conn: "sqlite3.Connection", visit_id: str) -> list[Row]:
    """The tasks filed during a visit, with how each stands."""
    return _rows(
        conn.execute(
            "SELECT t.id, t.title, t.status, t.number, "
            "(SELECT v.why FROM visits v WHERE v.task_id = t.id AND v.how = 'closed' ORDER BY v.left_at DESC LIMIT 1) "
            "AS why FROM links l JOIN tasks t ON t.id = l.to_task_id WHERE l.visit_id=? ORDER BY l.created_at, l.id",
            (visit_id,),
        )
    )


def _owner_done(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    if answer and answer["answer"] == "done":
        return Standing(requirement, MET, line="Done")
    return Standing(requirement, NOT_MET, task.get("owner_id"), requirement.get("line") or "Say when the work is done")


def _ok(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    person = requirement.get("person_id")
    what = requirement.get("line") or "OK"
    if answer and answer["answer"] == "ok":
        return Standing(requirement, MET, line="OK'd")
    if answer and answer["answer"] == "no":
        notes = (answer.get("notes") or "").strip()
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"Sent back: {notes}" if notes else "Sent back")
    return Standing(requirement, WAITING, person, what)


def _answer(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    if answer and answer["answer"] not in ("again",):
        return Standing(requirement, MET, line=str(answer["answer"]))
    return Standing(requirement, WAITING, requirement.get("person_id"), requirement.get("line") or "Answer")


def _linked_done(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    filed = _filed(conn, task["visit"]["id"])
    if not filed:
        return Standing(requirement, NOT_MET, task.get("owner_id"), "No task filed yet")
    still_open = [f for f in filed if f["status"] == "open"]
    if still_open:
        first = still_open[0]
        return Standing(requirement, WAITING, first["id"], first.get("title") or "")
    closed = [f for f in filed if f["status"] != "done"]
    if closed:
        first = closed[0]
        why = (first.get("why") or "").strip()
        title = first.get("title") or "A filed task"
        return Standing(
            requirement, NOT_MET, task.get("owner_id"), f"{title} was closed: {why}" if why else f"{title} was closed"
        )
    return Standing(requirement, MET, line="Done")


def _later_phase(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    return Standing(requirement, NOT_MET, task.get("owner_id"), f"{name_of(requirement)} is not built yet")


KINDS: dict[str, Callable[["sqlite3.Connection", Row, Row, "Row | None"], Standing]] = {
    "owner_done": _owner_done,
    "ok": _ok,
    "answer": _answer,
    "linked_done": _linked_done,
    "page": _later_phase,
    "pr_open": _later_phase,
    "checks_pass": _later_phase,
    "pr_merged": _later_phase,
    "in_release": _later_phase,
}


def standing(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None = None) -> Standing:
    """Where `requirement` stands for the task's open visit. `task` is `task_engine.task()`'s shape."""
    if answer and answer["answer"] == "pushed_past":
        return Standing(requirement, MET, line="Pushed past")
    return KINDS[requirement["kind"]](conn, task, requirement, answer)


def standings(conn: "sqlite3.Connection", task: Row) -> list[Standing]:
    """Every requirement of the task's open visit with where it stands, stage's first."""
    visit = task.get("visit")
    if not visit:
        return []
    answers = newest_answers(conn, visit["id"])
    return [standing(conn, task, r, answers.get(r["id"])) for r in for_visit(conn, visit)]


def unmet(conn: "sqlite3.Connection", task: Row) -> list[Standing]:
    return [s for s in standings(conn, task) if not s.met]


def can_push_past(requirement: Row) -> bool:
    return bool(requirement.get("can_push_past")) and requirement["kind"] not in NEVER_PUSHED_PAST


def people_only(requirement: Row) -> bool:
    return requirement["kind"] in OK_KINDS and requirement.get("who_oks") == "people_only"
