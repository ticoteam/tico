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
  ordinary answers there: they steer only an OK (and a page). A stage's answer is typed (`settings`: `name`, `type`
  text, number or choice, `min`/`max` for a number, `question`; choices in `choices`): the value is kept in one exact
  form (`typed`), with the name and type copied onto the answer, and `values` reads a task's newest answer by name.
  One that names nobody is the task owner's to give (`owners_answer`).
- `pushed_past`: someone moved the task on without it, with the reason in `notes`. Any kind.

A requirement never waits on a person who left, was removed or cannot sign in (`active_person`): it waits on nobody named instead, and
`waiting_on` gives it to the person the task falls to.
"""

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

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
    if owners_answer(requirement):  # the owner's to give, as their own "done" is: not met, and on them
        settings: Row = requirement.get("settings") or {}
        asked = str(settings.get("question") or "").strip()
        return Standing(requirement, NOT_MET, task.get("owner_id"), asked or name_of(requirement))
    person = person_of(conn, requirement, task["visit"]["id"])
    return Standing(requirement, WAITING, person, requirement.get("line") or "Answer")


def owners_answer(requirement: Row) -> bool:
    """A stage's answer that names nobody: the task's owner gives it, as they say done."""
    return (
        requirement.get("kind") == "answer" and bool(requirement.get("stage_id")) and not requirement.get("person_id")
    )


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
# Jev was unsure of an earlier page of this requirement on this visit (one since replaced): the bot had its one round.
UNSURE_BEFORE_SQL = (
    "SELECT 1 FROM requirement_pages p JOIN page_verdicts v ON v.file_id = p.file_id AND v.version = p.version "
    "AND v.rubric_hash = p.rubric_hash WHERE p.visit_id=? AND p.requirement_id=? AND p.replaced_at IS NOT NULL "
    "AND v.verdict = 'unsure' LIMIT 1"
)
# What Jev's unsure says to the bot that holds the stage, and then to the pipeline's manager bot (`_unsure_to_bots`).
FIX_OR_EXPLAIN = "Not yet met: fix it or explain it on the page."
DECIDE = "Decide if it meets the check: OK it or send it back, and say why."
# What a bot does to get a page a person OKs looked at again, instead of an ask of its own (task_engine._people_page).
PAGE_OK_HINT = "Attach the new version; Tico asks the person."


def _page(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    """A page attached to this visit (task_pages), Jev's verdict on it (page_verdicts), then the named person's OK of that
    exact version when the requirement names one or is people only. An answer counts only for the page version it was given on.
    Jev unsure on a stage a bot holds goes to bots first (two sign-offs rule, Nov 11; `_unsure_to_bots`): back to that bot
    once, then to the pipeline's manager bot. Otherwise, and when Jev could not check, a person decides, once per visit:
    after a person decided one such page, the next one goes back to the owner, so a noisy rubric never fills a person's
    list."""
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
        line = f"Sent back: {notes}" if notes else "Sent back"
        if requirement.get("person_id") or people_only(requirement):
            line = f"{line}. {PAGE_OK_HINT}"
        return Standing(requirement, NOT_MET, owner, line)
    verdict = V.get(conn, page["file_id"], page["version"], page.get("rubric_hash") or "")
    said = (verdict or {}).get("verdict")
    reasons = " ".join(str((verdict or {}).get("reasons") or "").split())
    if said is None:
        return Standing(requirement, WAITING, JEV, "Jev is checking")
    if said == "not_met":
        return Standing(requirement, NOT_MET, owner, f"Jev sent it back: {reasons}" if reasons else "Jev sent it back")
    person = person_of(conn, requirement, visit_id)
    oks_anyway = bool(requirement.get("person_id") or people_only(requirement))
    back = _sent_back(conn, visit_id, requirement["id"]) if oks_anyway else None
    if back and not requirement.get("person_id"):
        person = person or str(back["by_id"])  # the person who sent it back is asked again, by Tico, not by the bot
    after = f'Version {page["version"]}, after the send back: "{back["said"]}". ' if back else ""
    if said in ("unsure", "failed"):
        # A failed verdict's reason is why the calls failed (last_error): it was Jev, not the page.
        failure = " ".join(str((verdict or {}).get("last_error") or "").split())
        line = (
            (f"Jev couldn't check this: {failure}" if failure else "Jev couldn't check this")
            if said == "failed"
            else (f"Jev is unsure: {reasons}" if reasons else "Jev is unsure")
        )
        if oks_anyway:
            # A person OKs this page whatever Jev says: they are asked at once, every version, with Jev's reason. A bot
            # round trip adds nothing (Acme, Nov 11: after a send back, v2 went to nobody and the Builder improvised asks).
            return Standing(requirement, WAITING, person, after + line)
        if said == "unsure":
            routed = _unsure_to_bots(conn, task, requirement, line)
            if routed:
                return routed
        if conn.execute(ASKED_ONCE_SQL, (visit_id, requirement["id"])).fetchone():
            return Standing(requirement, NOT_MET, owner, line)
        return Standing(requirement, WAITING, person, line)
    if oks_anyway:
        # A people-only page that names nobody (or whose person left) is any person's: Jev passing it is never enough.
        from . import hubdb

        H: Any = hubdb
        name = H.actor_name(conn, person) if person else "someone"
        return Standing(requirement, WAITING, person, f"{after}OK {name} (version {page['version']})")
    return Standing(requirement, MET, line="Jev passed it")


def _sent_back(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> Row | None:
    """The newest send back of an earlier version of this page on this visit, by a person who can still act: who said
    it (`by_id`) and what (`said`). A page a person OKs comes back to them marked with it."""
    found = conn.execute(
        "SELECT by_id, notes FROM answers WHERE visit_id=? AND requirement_id=? AND answer='no' AND undone_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (visit_id, requirement_id),
    ).fetchone()
    if not found or not str(found[0] or "").startswith("human:") or not active_person(conn, found[0]):
        return None
    return {"by_id": str(found[0]), "said": " ".join(str(found[1] or "").split())}


def _unsure_to_bots(conn: "sqlite3.Connection", task: Row, requirement: Row, line: str) -> Standing | None:
    """Jev unsure of a page on a stage a bot holds (docs/pipelines-v3.md "Jev is checking"; the two sign-offs rule of
    Nov 11 put this before a person). The first time this visit, it is that bot's turn with Jev's reason and the check
    line, to fix the page or explain on it. The next time, the pipeline's manager bot decides: it OKs the page or sends it
    back, with why, as an answer on the task. None, so a person decides as before, when a person holds the stage, or no
    manager bot is set, or the manager is the bot that holds it. A page a person OKs anyway (people only, or naming
    someone) never comes here."""
    from . import waiting_on as W

    held = W.holder(conn, task)
    if not W.is_bot(held):
        return None
    settings: Row = requirement.get("settings") or {}
    check = " ".join(str(settings.get("rubric") or "").split()) or name_of(requirement)
    said = line if check in line else f"{line} The check: {check}"
    if not conn.execute(UNSURE_BEFORE_SQL, (task["visit"]["id"], requirement["id"])).fetchone():
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"{FIX_OR_EXPLAIN} {said}")
    pipeline: Row = task.get("pipeline") or {}
    manager: object = pipeline.get("manager_id")
    if W.is_bot(manager) and manager != held:
        return Standing(requirement, WAITING, str(manager), f"{DECIDE} {said}")
    return None


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
# - answer (a stage's): `name` (how the answer is read back, `values`), `type` (ANSWER_TYPES), `min` and `max` (a
#   number's bounds), `question` (what is asked, in full). A choice's choices are the requirement's `choices`.
# - item: its condition's (item_sources.Condition.settings, `settings_of`).
SETTINGS: dict[str, tuple[str, ...]] = {
    "owner_done": (),
    "ok": (),
    "answer": ("name", "type", "min", "max", "question"),
    "linked_done": (),
    "page": ("rubric", "at_head", "with_release"),
    "item": (),
}


# The types a stage's answer takes, and what its name looks like (a slug, unique within the pipeline).
ANSWER_TYPES = ("text", "number", "choice")
ANSWER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
WHOLE = re.compile(r"^[+-]?[0-9]+$")


def answer_type(requirement: Row) -> str:
    """The type of an answer requirement: its setting, else text (a question added to one task)."""
    settings: Row = requirement.get("settings") or {}
    return str(settings.get("type") or "text")


def _bound(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def number_rule(requirement: Row) -> str:
    """What a number answer must be, said in one line: "Confidence is a whole number from 0 to 100"."""
    settings: Row = requirement.get("settings") or {}
    low, high = _bound(settings.get("min")), _bound(settings.get("max"))
    what = f"{name_of(requirement)} is a whole number"
    if low is not None and high is not None:
        return f"{what} from {low} to {high}"
    if low is not None:
        return f"{what} of {low} or more"
    if high is not None:
        return f"{what} of {high} or less"
    return what


def typed(requirement: Row, value: str) -> tuple[str | None, str]:
    """An answer's value in its one stored form, or None and the line that says why it does not fit. A number is a
    whole number within its bounds ("080" is "80"); a choice is one of the choices, spelled as the choice is; text is
    anything not empty. A question added to one task is text, and its choices are a suggestion."""
    kind = answer_type(requirement)
    value = value.strip()
    if not value:
        return None, "Give an answer"
    if kind == "number":
        settings: Row = requirement.get("settings") or {}
        low, high = _bound(settings.get("min")), _bound(settings.get("max"))
        if not WHOLE.match(value) or len(value) > 18:
            return None, number_rule(requirement)
        number = int(value)
        if (low is not None and number < low) or (high is not None and number > high):
            return None, number_rule(requirement)
        return str(number), ""
    if kind == "choice":
        choices = [str(c) for c in cast("list[object]", requirement.get("choices") or [])]
        for choice in choices:
            if choice == value or choice.casefold() == value.casefold():
                return choice, ""
        return None, f"{name_of(requirement)} is one of: {', '.join(choices)}"
    return value, ""


NAMED_SQL = (
    "SELECT v.task_id, a.name, a.answer, a.value_type, a.by_id, a.created_at, v.stage_id FROM answers a "
    "JOIN visits v ON v.id = a.visit_id WHERE v.task_id IN (%s) AND a.name IS NOT NULL AND a.undone_at IS NULL "
    "ORDER BY a.created_at, a.id"
)


def prime_values(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> None:
    """Read the named answers of many tasks together, for the `values` of each that follow in this snapshot (Needs
    you shows a task for each ask on it)."""
    found = read_cache.cache(conn)
    if found is None:
        return
    unread = sorted({str(t) for t in task_ids if ("named_answers", str(t)) not in found})
    for key, rows in _by(conn, NAMED_SQL, "task_id", unread).items():
        found.setdefault(("named_answers", key), rows)


def values(conn: "sqlite3.Connection", task_id: str) -> dict[str, Row]:
    """{name: {value, type, by, at, stage_id}}: the newest answer by each name a task was given, on any of its visits,
    that was not taken back. A number reads as a whole number."""
    out: dict[str, Row] = {}
    for row in _cached(conn, "named_answers", NAMED_SQL, "task_id", task_id):
        kind, value = row["value_type"], row["answer"]
        shown: object = int(value) if kind == "number" and WHOLE.match(str(value)) else value
        out[str(row["name"])] = {
            "value": shown,
            "type": kind or "text",
            "by": row["by_id"],
            "at": row["created_at"],
            "stage_id": row["stage_id"],
        }
    return out


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
