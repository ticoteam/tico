# pyright: strict
"""What a task needs before it leaves a stage (docs/pipelines-v3.md, "The six requirement kinds").

A requirement sits on a stage (every task that visits it) or on one visit (this task, this time). Each kind answers one
question for a task's open visit: met, waiting on something named, or not met and why. A kind reads tables only. It never
calls GitHub, Jev or a computer, so a move and the never-stuck check can't be stalled by the outside. Kind `item` asks a
`condition` of the items a task links in a `source` (backend/item_sources: GitHub's PR open, Checks pass, PR merged and
In a release); the source's condition says where it stands.

What met a requirement is its newest answer on the visit that is not undone (`answers`):

- `done`: the owner said the stage's work is finished (owner_done).
- `ok`: the named person approved. `no`: they sent it back, with notes. `again`: the owner asks them to look again.
- `give`: a stage's OK was given to another person, named in `notes`. It waits on them now.
- anything on an `answer` requirement: the choice the person picked, or what they typed. `no`, `again` and `give` are
  ordinary answers there: they steer only an OK (and a page).
- `pushed_past`: someone moved the task on without it, with the reason in `notes`. Any kind.

A requirement never waits on a person who left, was removed or cannot sign in (`active_person`): it waits on nobody named instead, and
`waiting_on` gives it to the person the task falls to.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import read_cache

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

MET, WAITING, NOT_MET = "met", "waiting", "not_met"
# What a requirement waits on when it is not a person or a task: `Standing.on` is `jev` for a page Jev has not judged,
# and the source's id (`github`) for an item whose metadata a source has yet to bring.
JEV = "jev"
KIND_NAMES = {
    "owner_done": "Owner says done",
    "ok": "OK from someone",
    "page": "A page",
    "item": "An item",
    "linked_done": "Linked work done",
    "answer": "Answer from someone",
}
# Kinds whose "Who OKs" switch means something. Elsewhere it reads as `none`.
OK_KINDS = ("ok", "page", "answer")
# Answers that steer an OK instead of meeting it: a send back, a look again, a give. They mean that only on the kinds
# in STEERED; on a question they are the answer.
STEERS = ("no", "again", "give")
STEERED = ("ok", "page")


def steers(kind: str, value: object) -> bool:
    """Whether an answer of this value steers a requirement of this kind (send back, look again, give) instead of
    meeting it."""
    return kind in STEERED and value in STEERS


def active_person(conn: "sqlite3.Connection", actor: object) -> bool:
    """Whether `actor` is a person who is still here and can act: `human:<id>` with a `humans` row, whose roster row is
    not marked as left (`hidden`, set by `access.leave`), nor dropped by the directory (`directory_left`), nor has
    sign-in turned off (`sign_in: false`, the People tab's switch). Remove deletes the `humans` row
    (`people_remove`). A bot, nobody, or anything else is not an active person."""
    text = str(actor or "")
    if not text.startswith("human:") or not text[len("human:") :]:
        return False
    if read_cache.cache(conn) is None:
        return conn.execute(ACTIVE_PEOPLE_SQL + " AND h.id=?", (text[len("human:") :],)).fetchone() is not None
    # In one snapshot every person is read once: a list asks this for each task's people.
    return text[len("human:") :] in read_cache.value(conn, ("active_people",), lambda: _active_ids(conn), shared=True)


# The people who are here and can act (`active_person`).
ACTIVE_PEOPLE_SQL = (
    "SELECT h.id FROM humans h WHERE NOT EXISTS (SELECT 1 FROM registry_metadata m, "
    "json_each(CASE WHEN json_valid(m.value) THEN m.value ELSE '{}' END, '$.people') p "
    "WHERE m.key='people' AND json_extract(p.value, '$.id') = h.id "
    "AND (json_extract(p.value, '$.hidden') OR json_extract(p.value, '$.directory_left') "
    "OR json_extract(p.value, '$.sign_in') = 0))"
)


def _active_ids(conn: "sqlite3.Connection") -> frozenset[str]:
    return frozenset(str(found[0]) for found in conn.execute(ACTIVE_PEOPLE_SQL).fetchall())


@dataclass
class Standing:
    """Where one requirement stands for one visit. `on` is who or what it waits on: an actor, `jev`, a source, or a
    task id. `line` is one sentence: what is missing, or what met it. `about` is the task it filed whose title (and
    why it was closed) the line says, so a reader who may not open that task is not told them (waiting_on.unnamed)."""

    requirement: Row
    state: str
    on: str | None = None
    line: str = ""
    about: str | None = None

    @property
    def met(self) -> bool:
        return self.state == MET

    @property
    def name(self) -> str:
        return name_of(self.requirement)


def name_of(requirement: Row) -> str:
    """What a requirement is called on a task: its own line if it has one, else its condition's name (an item), else its
    kind's."""
    found = condition_of(requirement)
    named = found.name if found else KIND_NAMES.get(requirement["kind"])
    return (requirement.get("line") or "").strip() or named or requirement["kind"]


def condition_of(requirement: Row) -> Any:
    """The source's condition an `item` requirement asks (item_sources.Condition), or None (another kind, or a source
    or condition that does not exist)."""
    if requirement.get("kind") != "item":
        return None
    from . import item_sources

    return item_sources.condition(requirement.get("source"), requirement.get("condition"))


def outside(on: object) -> bool:
    """Whether a requirement waits on something outside the company's people and tasks: Jev, or a source."""
    from . import item_sources

    return on == JEV or on in item_sources.SOURCES


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


STAGE_SQL = (
    "SELECT * FROM requirements WHERE stage_id IN (%s) AND withdrawn_at IS NULL ORDER BY position, created_at, id"
)
VISIT_SQL = "SELECT * FROM requirements WHERE visit_id IN (%s) AND withdrawn_at IS NULL ORDER BY created_at, id"
ANSWERS_SQL = "SELECT * FROM answers WHERE visit_id IN (%s) AND undone_at IS NULL ORDER BY created_at, id"


def _by(conn: "sqlite3.Connection", sql: str, column: str, keys: list[str]) -> dict[str, list[Row]]:
    """{key: its rows} for `sql` over many keys, one statement however many (the keys go in as one JSON list)."""
    out: dict[str, list[Row]] = {key: [] for key in keys}
    if keys:
        for row in _rows(conn.execute(sql % "SELECT value FROM json_each(?)", (json.dumps(keys),))):
            out[str(row[column])].append(row)
    return out


def _cached(conn: "sqlite3.Connection", what: str, sql: str, column: str, key: str) -> list[Row]:
    """The rows of `sql` for one key, read once in a snapshot (or seeded by `prime`)."""
    return read_cache.value(conn, (what, key), lambda: _by(conn, sql, column, [key])[key])


def prime(conn: "sqlite3.Connection", tasks: Iterable[Row]) -> None:
    """Read the requirements and answers of many tasks' open visits (`task_engine.task()` shapes) together, for the
    `standings` of each that follow in this snapshot: a list costs what one task does."""
    values = read_cache.cache(conn)
    if values is None:
        return
    visits = [visit for visit in (task.get("visit") for task in tasks) if visit]
    for what, sql, column, keys in (
        ("requirements_on_stage", STAGE_SQL, "stage_id", {str(v["stage_id"]) for v in visits}),
        ("requirements_on_visit", VISIT_SQL, "visit_id", {str(v["id"]) for v in visits}),
        ("answers_on_visit", ANSWERS_SQL, "visit_id", {str(v["id"]) for v in visits}),
    ):
        unread = sorted(key for key in keys if (what, key) not in values)
        for key, rows in _by(conn, sql, column, unread).items():
            values.setdefault((what, key), rows)


def on_stage(conn: "sqlite3.Connection", stage_id: str) -> list[Row]:
    """A stage's requirements, in order."""
    return [_loaded(r) for r in _cached(conn, "requirements_on_stage", STAGE_SQL, "stage_id", stage_id)]


def on_visit(conn: "sqlite3.Connection", visit_id: str) -> list[Row]:
    """The requirements added to one visit, oldest first."""
    return [_loaded(r) for r in _cached(conn, "requirements_on_visit", VISIT_SQL, "visit_id", visit_id)]


def for_visit(conn: "sqlite3.Connection", visit: Row) -> list[Row]:
    """Everything a task needs to leave its open visit: the stage's requirements, then the ones added to this visit."""
    return on_stage(conn, visit["stage_id"]) + on_visit(conn, visit["id"])


def get(conn: "sqlite3.Connection", requirement_id: str) -> Row | None:
    rows = _rows(conn.execute("SELECT * FROM requirements WHERE id=?", (requirement_id,)))
    return _loaded(rows[0]) if rows else None


def newest_answers(conn: "sqlite3.Connection", visit_id: str) -> dict[str, Row]:
    """{requirement id: its newest answer on this visit that is not undone}."""
    out: dict[str, Row] = {}
    for row in _cached(conn, "answers_on_visit", ANSWERS_SQL, "visit_id", visit_id):
        out[row["requirement_id"]] = row
    return out


def person_of(conn: "sqlite3.Connection", requirement: Row, visit_id: str) -> str | None:
    """Who a requirement waits on in one visit: the person it was last given to (a `give` answer that is not undone,
    on a kind a give steers), else the person or bot it names. A person who left or was removed is skipped: with
    nobody left, it is None, and the task's person takes it (`waiting_on.person_for`)."""
    gives = (
        [
            row
            for row in _cached(conn, "answers_on_visit", ANSWERS_SQL, "visit_id", visit_id)
            if row["requirement_id"] == requirement["id"] and row["answer"] == "give"
        ]
        if requirement["kind"] in STEERED
        else []
    )
    given = gives[-1]["notes"] if gives else None  # the newest: answers are oldest first
    for found in (str(given) if given else None, requirement.get("person_id")):
        if found and (not str(found).startswith("human:") or active_person(conn, found)):
            return str(found)
    return None


def _filed(conn: "sqlite3.Connection", visit_id: str) -> list[Row]:
    """The tasks filed during a visit, with how each stands. One that was deleted since counts as closed: it is not
    work that got done."""
    return _rows(
        conn.execute(
            "SELECT l.to_task_id AS id, COALESCE(t.title, 'A deleted task') AS title, "
            "COALESCE(t.status, 'closed') AS status, t.number, "
            "CASE WHEN t.id IS NULL THEN 'It was deleted' ELSE "
            "(SELECT v.why FROM visits v WHERE v.task_id = t.id AND v.how = 'closed' ORDER BY v.left_at DESC LIMIT 1) "
            "END AS why FROM links l LEFT JOIN tasks t ON t.id = l.to_task_id WHERE l.visit_id=? "
            "ORDER BY l.created_at, l.id",
            (visit_id,),
        )
    )


def _owner_done(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    if answer and answer["answer"] == "done":
        return Standing(requirement, MET, line="Done")
    return Standing(requirement, NOT_MET, task.get("owner_id"), requirement.get("line") or "Say when the work is done")


def _ok(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    person = person_of(conn, requirement, task["visit"]["id"])
    what = requirement.get("line") or "OK"
    if answer and answer["answer"] == "ok":
        return Standing(requirement, MET, line="OK'd")
    if answer and answer["answer"] == "no":
        notes = (answer.get("notes") or "").strip()
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"Sent back: {notes}" if notes else "Sent back")
    return Standing(requirement, WAITING, person, what)


def _answer(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    if answer:  # nothing steers a question: any value is the answer, "no", "again" and "give" too
        return Standing(requirement, MET, line=str(answer["answer"]))
    person = person_of(conn, requirement, task["visit"]["id"])
    return Standing(requirement, WAITING, person, requirement.get("line") or "Answer")


def _linked_done(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    filed = _filed(conn, task["visit"]["id"])
    if not filed:
        return Standing(requirement, NOT_MET, task.get("owner_id"), "No task filed yet")
    still_open = [f for f in filed if f["status"] == "open"]
    if still_open:
        first = still_open[0]
        return Standing(requirement, WAITING, first["id"], first.get("title") or "", first["id"])
    closed = [f for f in filed if f["status"] != "done"]
    if closed:
        first = closed[0]
        why = (first.get("why") or "").strip()
        title = first.get("title") or "A filed task"
        line = f"{title} was closed: {why}" if why else f"{title} was closed"
        return Standing(requirement, NOT_MET, task.get("owner_id"), line, first["id"])
    return Standing(requirement, MET, line="Done")


# The five kinds the Product and Engineering pipelines bring. Each is its own function so each lands on its own:
# `_page` with backend/task_pages.py and Jev's verdicts (page_verdicts), the four GitHub kinds with backend/item_sources/github.py.
# The lines each one says are in docs/dev/pipelines-v3-build.md ("Standings of the Product and Engineering kinds").
# A person already decided an earlier page of this requirement on this visit that Jev was unsure of or could not check.
ASKED_ONCE_SQL = (
    "SELECT 1 FROM requirement_pages p JOIN page_verdicts v ON v.file_id = p.file_id AND v.version = p.version "
    "AND v.rubric_hash = p.rubric_hash JOIN answers a ON a.visit_id = p.visit_id AND a.requirement_id = p.requirement_id "
    "AND a.page_version = p.version AND a.undone_at IS NULL AND a.created_at >= p.created_at "
    "WHERE p.visit_id=? AND p.requirement_id=? AND p.replaced_at IS NOT NULL AND v.verdict IN ('unsure', 'failed') "
    "AND a.answer IN ('ok', 'no') LIMIT 1"
)


def _page(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    """A page attached to this visit (task_pages), Jev's verdict on it (page_verdicts), then the named person's OK of that
    exact version when the requirement names one. An answer counts only for the page version it was given on. Jev
    unsure, or unable to check, is a person's to decide, once per visit: after a person decided one such page, the next
    one goes back to the owner, so a noisy rubric never fills a person's list."""
    from . import page_verdicts as V
    from .item_sources import github as F
    from . import task_pages as P

    visit_id = task["visit"]["id"]
    owner = task.get("owner_id")
    page = P.current(conn, visit_id, requirement["id"])
    if not page:
        return Standing(requirement, NOT_MET, owner, "No page attached")
    settings: Row = requirement.get("settings") or {}
    if settings.get("at_head") and json.loads(page["item_versions"] or "{}") != F.heads(conn, task["id"]):
        return Standing(requirement, NOT_MET, owner, "New commits since the review")
    if answer and (answer.get("page_version") != page["version"] or answer["created_at"] < page["created_at"]):
        answer = None  # given on an earlier page
    if answer and answer["answer"] == "ok":
        return Standing(requirement, MET, line="OK'd")
    if answer and answer["answer"] == "no":
        notes = (answer.get("notes") or "").strip()
        return Standing(requirement, NOT_MET, owner, f"Sent back: {notes}" if notes else "Sent back")
    verdict = V.get(conn, page["file_id"], page["version"], page.get("rubric_hash") or "")
    said = (verdict or {}).get("verdict")
    reasons = " ".join(str((verdict or {}).get("reasons") or "").split())
    if said is None:
        return Standing(requirement, WAITING, JEV, "Jev is checking")
    if said == "not_met":
        return Standing(requirement, NOT_MET, owner, f"Jev sent it back: {reasons}" if reasons else "Jev sent it back")
    person = person_of(conn, requirement, visit_id)
    if said in ("unsure", "failed"):
        line = (
            "Jev couldn't check this"
            if said == "failed"
            else (f"Jev is unsure: {reasons}" if reasons else "Jev is unsure")
        )
        if conn.execute(ASKED_ONCE_SQL, (visit_id, requirement["id"])).fetchone():
            return Standing(requirement, NOT_MET, owner, line)
        return Standing(requirement, WAITING, person, line)
    if requirement.get("person_id"):
        from . import hubdb

        H: Any = hubdb
        name = H.actor_name(conn, person) if person else "someone"
        return Standing(requirement, WAITING, person, f"OK {name} (version {page['version']})")
    return Standing(requirement, MET, line="Jev passed it")


def _item(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    """A condition of the items the task links in a source: the source's condition says where it stands."""
    found = condition_of(requirement)
    if found is None:
        return Standing(requirement, NOT_MET, task.get("owner_id"), "No such condition")
    standing: Standing = found.standing(conn, task, requirement, answer)
    return standing


KINDS: dict[str, Callable[["sqlite3.Connection", Row, Row, "Row | None"], Standing]] = {
    "owner_done": _owner_done,
    "ok": _ok,
    "answer": _answer,
    "linked_done": _linked_done,
    "page": _page,
    "item": _item,
}
# The keys a requirement's `settings` may hold, by kind. Anything else is refused at save (pipeline_settings).
# - page: `rubric` (Jev's "what to check"; empty means the requirement's line), `at_head` (the page is for the newest
#   commit of every linked PR: a new commit puts it back to the owner), `with_release` (Jev is also shown the release
#   of the tasks this task filed, so "production is older than the release" is judged against what GitHub says).
# - item: its condition's (item_sources.Condition.settings, `settings_of`).
SETTINGS: dict[str, tuple[str, ...]] = {
    "owner_done": (),
    "ok": (),
    "answer": (),
    "linked_done": (),
    "page": ("rubric", "at_head", "with_release"),
    "item": (),
}


def settings_of(requirement: Row) -> tuple[str, ...]:
    """The keys a requirement's `settings` may hold: its kind's, or for an item its condition's."""
    found = condition_of(requirement)
    if found is not None:
        keys: tuple[str, ...] = found.settings
        return keys
    return SETTINGS.get(requirement.get("kind") or "", ())


def never_pushed_past(requirement: Row) -> bool:
    """A requirement no one may move a task on without, whatever its row says (C3): its condition says never."""
    found = condition_of(requirement)
    return found is not None and found.can_push_past == "never"


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
    return bool(requirement.get("can_push_past")) and not never_pushed_past(requirement)


def people_only(requirement: Row) -> bool:
    return requirement["kind"] in OK_KINDS and requirement.get("who_oks") == "people_only"
