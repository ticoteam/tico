# pyright: strict
"""What a task needs before it leaves a stage (docs/dev/pipelines-v3.md, "The six requirement kinds").

A requirement sits on a stage (every task that visits it) or on one visit (this task, this time). Each kind answers one
question for a task's open visit: met, waiting on something named, or not met and why. A kind reads tables only. It never
calls GitHub, Jev or a computer, so a move and the never-stuck check can't be stalled by the outside. Kind `item` asks a
`condition` of the items a task links in a `source` (backend/item_sources: GitHub's PR open, Checks pass, PR merged and
In a release); the source's condition says where it stands.

What met a requirement is its newest answer on the visit that is not undone (`answers`):

- `done`: the owner said the stage's work is finished (owner_done).
- `ok`: the named person approved. `no`: they sent it back, with notes. `again`: the owner said done again after a
  send back, so it waits on the person again (answers.reopen_sent_back; older rows are a Look again).
- `give`: a stage's OK was given to another person, named in `notes`. It waits on them now.
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
    "related_input": "Related input",
}
# Kinds whose "Who OKs" switch means something. Elsewhere it reads as `none`.
OK_KINDS = ("ok", "page")
# Answers that steer an OK instead of meeting it: a send back, an ask again, a give. They mean that only on the kinds in
# STEERED. `again` is written only by the owner saying done after a send back (answers.reopen_sent_back); stored rows
# from the retired Look again read the same, so undoing one never reads as the answer that moved the task.
STEERS = ("no", "again", "give")
STEERED = ("ok", "page")


def steers(kind: str, value: object) -> bool:
    """Whether an answer of this value steers a requirement of this kind (send back, ask again, give) instead of
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
    why it was closed) the line says, so a reader who may not open that task is not told them (waiting_on.unnamed).
    `fix` is where a person fixes what stops anyone meeting it (a link in the app, such as GitHub's "#/tools/github"
    while no GitHub App is installed): a bot that has it is Paused on that cause and nobody is woken until it goes."""

    requirement: Row
    state: str
    on: str | None = None
    line: str = ""
    about: str | None = None
    since: str | None = None  # when it was asked of whom it waits on, when that is later than the visit began
    fix: str | None = None
    # On a page a person is asked to OK: the sentences of `line` that say why they are asked again or what a bot read
    # since (new commits since an OK, a bot's read of them). The decision view shows them on their own when it draws the
    # page's rounds instead of the line (backend/page_rounds.py notices).
    notices: tuple[str, ...] = ()
    # A document approval whose decision-model check could not run (three tries): it reaches the person anyway, marked.
    unchecked: bool = False

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


ENDS = (".", "!", "?", "…")


def verdict_and_rest(notes: object) -> tuple[str, str]:
    """A reviewer's notes as (its first line, everything after it), each on one line: the first is its verdict ("No
    blockers", "Blocks: …", "Unsure: …") when it wrote one, as the reviewer's skill asks (task #389)."""
    lines = [line.strip() for line in str(notes or "").strip().splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return "", ""
    return lines[0], " ".join(" ".join(lines[1:]).split())


def sentence(text: str) -> str:
    """`text` as one sentence: a period added only when it has no end mark of its own, inside a closing quote or
    bracket too, so a person's note that ends with one is never followed by a second ("criterion.." was)."""
    text = text.strip()
    if not text or text.rstrip("\"')]”’").endswith(ENDS):
        return text
    return f"{text}."


def sentences(*parts: str) -> str:
    """Sentences joined into one line, each ended once (`sentence`). Empty parts are left out."""
    return " ".join(sentence(part) for part in parts if part and part.strip())


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
    for key in ("settings", "shows"):
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
    # Stored rows hold only plain values, so a shallow copy of each is a full one; the snapshot keeps its own.
    found = read_cache.value(conn, (what, key), lambda: _by(conn, sql, column, [key])[key], shared=True)
    return [dict(row) for row in found]


def prime(conn: "sqlite3.Connection", tasks: Iterable[Row]) -> None:
    """Read the requirements and answers of many tasks' open visits (`task_engine.task()` shapes) together, for the
    `standings` of each that follow in this snapshot: a list costs what one task does."""
    values = read_cache.cache(conn)
    if values is None:
        return
    tasks = list(tasks)
    visits = [visit for visit in (task.get("visit") for task in tasks) if visit]
    from . import pr_conflicts

    pr_conflicts.prime(conn, [str(task["id"]) for task in tasks if task.get("visit")])
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
            "COALESCE(t.status, 'closed') AS status, t.number, t.id IS NULL AS deleted, "
            "t.owner_id AS owner_id, t.conversation_id AS conversation_id, "
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
    from . import validate_gate

    if validate_gate.is_stage(task.get("stage_id")):
        held = validate_gate.held(conn, task, requirement)  # a bot's OK reaches the person with a passed page only
        if held:
            return held
    return Standing(requirement, WAITING, person, what)


def _linked_done(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    filed = _filed(conn, task["visit"]["id"])
    if _at_head_gate(conn, task):
        return _second_review(conn, task, requirement, filed)
    if not filed:
        return Standing(requirement, NOT_MET, task.get("owner_id"), "No task filed yet")
    still_open = [f for f in filed if f["status"] == "open"]
    if still_open:
        first = still_open[0]
        return Standing(requirement, WAITING, first["id"], first.get("title") or "", first["id"])
    has_done = any(f["status"] == "done" for f in filed)
    # A finished retry can replace closed work, but a deleted task still needs the owner's attention.
    closed = [f for f in filed if f["status"] != "done" and (f["deleted"] or not has_done)]
    if closed:
        first = closed[0]
        why = (first.get("why") or "").strip()
        title = first.get("title") or "A filed task"
        line = f"{title} was closed: {why}" if why else f"{title} was closed"
        return Standing(requirement, NOT_MET, task.get("owner_id"), line, first["id"])
    return Standing(requirement, MET, line="Done")


# A second review's request names "PR <url> at <commit>": the PR and the commit it reads.
REVIEW_NAMES_RE = re.compile(
    r"PR\s+https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)\s+at\s+([0-9a-f]{7,40})\b"
)


def _at_head_gate(conn: "sqlite3.Connection", task: Row) -> bool:
    """Whether the task's stage is a Review stage: its page is read at the newest commit (`at_head`). Its linked_done
    gate is the second review (`_second_review`), not any task filed on the visit."""
    return (
        conn.execute(
            "SELECT 1 FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL "
            "AND json_extract(settings, '$.at_head')=1 LIMIT 1",
            (task.get("stage_id"),),
        ).fetchone()
        is not None
    )


def _reviews(conn: "sqlite3.Connection", filed: list[Row]) -> list[Row]:
    """The filed tasks that are second reviews: subtasks filed with a review check model (`--role review`,
    backend/check_models.py), whoever owns them. A routing, repair or build task filed on the visit is none."""
    ids = [f["id"] for f in filed if not f["deleted"]]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    review = {
        row[0]
        for row in conn.execute(
            f"SELECT task_id FROM task_check_models WHERE role='review' AND task_id IN ({marks})", ids
        ).fetchall()
    }
    return [f for f in filed if f["id"] in review]


def _named_commits(conn: "sqlite3.Connection", review: Row) -> list[tuple[str, str]]:
    """(PR ref, commit) pairs the review reads: the PR heads saved when it was filed (check_models.record), then each
    "PR <url> at <commit>" in a message about the review, whoever wrote it (the filer's request or the owner's notes)."""
    from .item_sources import github as F

    named: list[tuple[str, str]] = []
    for (data,) in conn.execute(
        "SELECT data FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='check_model' "
        "AND data IS NOT NULL ORDER BY created_at, id",
        (review["id"],),
    ).fetchall():
        heads = _json_object(data).get("heads")
        if isinstance(heads, dict):
            saved = cast("dict[str, object]", heads)
            named.extend((ref, str(sha).lower()) for ref, sha in saved.items() if sha)
    from .hubdb import MESSAGE_TASK_SQL

    # Only messages about this review: a conversation several tasks share carries other tasks' PRs and commits too.
    for (body,) in conn.execute(
        "SELECT m.body FROM messages m LEFT JOIN conversations cv ON cv.id = m.conversation_id "
        "WHERE m.conversation_id=? AND m.kind='say' AND m.deleted_at IS NULL AND m.superseded_at IS NULL "
        f"AND {MESSAGE_TASK_SQL}=? ORDER BY m.created_at, m.id",
        (review["conversation_id"], review["id"]),
    ).fetchall():
        for repo, number, sha in REVIEW_NAMES_RE.findall(str(body or "")):
            named.append((F.pr_ref(repo, int(number)), sha.lower()))
    return named


def _json_object(text: object) -> dict[str, Any]:
    try:
        value = json.loads(str(text))
    except ValueError:
        return {}
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _names_heads(named: list[tuple[str, str]], heads: dict[str, str | None]) -> bool:
    """Whether the (PR ref, commit) pairs name the newest commit of every PR in `heads`. No PR linked: any review does."""
    return all(
        any(ref == name and head is not None and head.startswith(sha) for name, sha in named)
        for ref, head in heads.items()
    )


def _second_review(conn: "sqlite3.Connection", task: Row, requirement: Row, filed: list[Row]) -> Standing:
    """The Review stage's gate. Only a second review counts (`_reviews`), and only when it is Done and reads the newest
    commit of every PR linked to the task (no PR linked: Done is enough). A Done review of the newest commit meets it
    even while an older one is open; the gate waits only on an open review of the newest commit (or of no commit it
    can read). Any other filed task, a review of an older commit, or an unread PR head leaves it not met."""
    from .item_sources import github as F

    reviews = _reviews(conn, filed)
    if not reviews:
        return Standing(requirement, NOT_MET, task.get("owner_id"), "No second review filed yet")
    heads = F.heads(conn, task["id"])
    if any(head is None for head in heads.values()):
        return Standing(requirement, NOT_MET, task.get("owner_id"), "The newest commit of a linked PR is not read yet")
    named = {f["id"]: _named_commits(conn, f) for f in reviews}
    at_head = [f for f in reviews if _names_heads(named[f["id"]], heads)]
    if any(f["status"] == "done" for f in at_head):
        return Standing(requirement, MET, line="Done")
    waiting = [f for f in reviews if f["status"] == "open" and (f in at_head or not named[f["id"]])]
    if waiting:
        first = waiting[0]
        return Standing(requirement, WAITING, first["id"], first.get("title") or "", first["id"])
    stale = [f for f in reviews if f["status"] in ("open", "done")]
    if stale:
        first = stale[0]
        title = first.get("title") or "The second review"
        return Standing(
            requirement,
            NOT_MET,
            task.get("owner_id"),
            f"{title} did not read the newest commit: file a second review of it",
            first["id"],
        )
    first = reviews[0]
    why = (first.get("why") or "").strip()
    title = first.get("title") or "A second review"
    line = f"{title} was closed: {why}" if why else f"{title} was closed"
    return Standing(requirement, NOT_MET, task.get("owner_id"), line, first["id"])


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
# Jev sent back an earlier page of this requirement on this visit (one since replaced): on a page a person OKs anyway,
# the owner had its one round, so the next send back is that person's to decide (`_page`).
NOT_MET_BEFORE_SQL = UNSURE_BEFORE_SQL.replace("v.verdict = 'unsure'", "v.verdict = 'not_met'")
# What Jev's unsure says to the pipeline's manager bot (`_unsure_to_bots`).
DECIDE = "Decide if it meets the check: OK it or send it back, and say why."


def decide_commands(task_id: str, requirement_id: str) -> str:
    """The two answers a bot asked to DECIDE gives, as commands: its decision is an answer on the requirement."""
    answer = f"tico task answer {task_id} {requirement_id}"
    return (
        f'Give it as an answer, not a move. OK it: {answer} ok -m "<why it meets the check>" --version N. '
        f'Send it back: {answer} no -m "<what to change>" --version N.'
    )


def asked_to_decide(standing: Standing, by: object) -> bool:
    """Whether this standing asks `by`, a bot, to decide a page Jev was unsure of (`_unsure_to_bots`)."""
    return standing.state == WAITING and bool(by) and standing.on == by and standing.line.startswith(DECIDE)


# What a bot does to get a page a person OKs looked at again, instead of an ask of its own (task_engine.people_page).
PAGE_OK_HINT = "Attach the new version; Tico asks the person."
DOCUMENT_OK_HINT = "Write the new version of the {document}; it is submitted for approval."


# A page `at_head` attached for an older commit of a linked PR than its newest one.
STALE_REVIEW = "New commits since the review"


def _previous_page(conn: "sqlite3.Connection", sql: str, visit_id: str, requirement: Row, file_id: str) -> bool:
    """A document's earlier decision-model round belongs to this page, never another page sharing the role."""
    args: tuple[Any, ...] = (visit_id, requirement["id"])
    if document_of(requirement):
        sql = sql.replace(" LIMIT 1", " AND p.file_id=? LIMIT 1")
        args = (*args, file_id)
    return bool(conn.execute(sql, args).fetchone())


def _page(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    """A document approval meets the check only when every submitted page version does."""
    from . import task_pages as P

    document = document_of(requirement)
    if not document:
        return _one_page(conn, task, requirement, answer)
    linked = P.live_documents(conn, task["id"], document)
    pages = P.current_pages(conn, task["visit"]["id"], requirement["id"])
    if linked and not pages:
        page, _ = page_for(conn, task, requirement)
        if page:
            pages = P.current_pages(conn, str(page["visit_id"]), requirement["id"])
    if not pages or linked.keys() != {str(p["file_id"]) for p in pages}:
        return Standing(requirement, NOT_MET, task.get("owner_id"), "No page attached")
    results = [_one_page(conn, task, requirement, answer, page) for page in pages]
    # Work owed to the writer or decision model comes before asking a person to decide the complete set.
    result = next((r for r in results if r.state == NOT_MET), None)
    result = result or next((r for r in results if r.on == JEV and not r.met), None)
    result = result or next((r for r in results if not r.met), results[0])
    result.notices = tuple(dict.fromkeys(n for r in results for n in r.notices))
    result.unchecked = any(r.unchecked for r in results)
    return result


def _one_page(
    conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None, submitted: Row | None = None
) -> Standing:
    """A page attached to this visit (task_pages), Jev's verdict on it (page_verdicts), then the named person's OK of that
    exact version when the requirement names one or is people only. An answer counts only for the page version it was given on,
    except a person's OK of an earlier version of the same page at the same commits (`_same_code_ok`), which stays.
    Jev's send back is the owner's turn; on a page a person OKs anyway, the next send back on the same visit is that
    person's to decide. Jev unsure, or unable to check, on a stage a bot holds goes straight to the pipeline's manager bot
    for one look per version (`_unsure_to_bots`); the page's own bot is not woken to rewrite. On a stage a person OKs,
    an unsure shows the person nothing from Jev. With no manager bot to look, a person decides once per visit: after a
    person decided one such page, the next one goes back to the owner, so a noisy rubric never fills a person's list. On a page a person OKs that is not a review (QA's), commits pushed after the reviews are a bot's to
    review before the person is asked (`_new_commits`). The person's OK is for the commits it was given at: new ones
    since keep it when they change the same lines, or once a bot read and OK'd them (`_after_ok`); and it comes back
    with the task from Merge (`page_for`)."""
    from . import page_verdicts as V
    from .item_sources import github as F

    visit_id = task["visit"]["id"]
    owner = task.get("owner_id")
    page, carried = page_for(conn, task, requirement)
    page = submitted or page
    if not page:
        return Standing(requirement, NOT_MET, owner, "No page attached")
    settings: Row = requirement.get("settings") or {}
    if settings.get("at_head") and json.loads(page["item_versions"] or "{}") != F.heads(conn, task["id"]):
        return Standing(requirement, NOT_MET, owner, STALE_REVIEW)
    signoff = bool(requirement.get("person_id") or people_only(requirement)) and not settings.get("at_head")
    if signoff and answer and _reviews_new(requirement, answer):
        answer = _newest_signoff(conn, visit_id, requirement)  # a bot's review of new commits is not the OK
    answer = answer or carried
    if answer and document_of(requirement):
        from . import task_pages as P

        approved = P.answer_pages(conn, task["id"], answer)
        if approved.get(page["file_id"]) != page["version"]:
            answer = None
        elif answer.get("page_version") != page["version"]:
            answer = dict(answer, page_version=page["version"])
    if answer and (answer.get("page_version") != page["version"] or answer["created_at"] < page["created_at"]):
        # Given on an earlier page. A person's OK of an earlier version of this same page, at the commits the PRs are
        # at now, stays: publishing the page again changed no code (Nov 13, #3584: Chris was asked again for nothing).
        # Only on the visit where it was given: an OK brought back from Merge is the Merge return's to judge.
        if signoff and answer is not carried and _same_code_ok(conn, task, requirement, answer, page):
            name, was = _name(conn, answer["by_id"]), answer["page_version"]
            return Standing(requirement, MET, line=f"OK'd by {name} on version {was}; no new commits since")
        answer = None
    if signoff and carried and answer is carried and (held := _queued_as_is(conn, task, requirement)):
        return held  # back from Merge at the commits the queue just ran: the OK waits for a fix (task #344)
    lapsed, lapsed_by, lapsed_ok, since_ok = "", None, None, None
    if signoff and answer and answer["answer"] == "ok" and _moved_since(conn, task, answer):
        # The OK was for the commits the person was shown. New ones that change the same lines keep it; others keep it
        # once a bot read them and OK'd them (`_after_ok`). Otherwise it lapses: the same person is asked again.
        kept, since_ok = _after_ok(conn, task, requirement, answer)
        if kept:
            return kept
        lapsed_by, lapsed_ok = str(answer["by_id"]), answer
        answer, lapsed = None, sentence(f"New commits since {_name(conn, lapsed_by)}'s OK") + " "
        if carried:
            # Back from Merge with no page of its own: the Builder fixes what was sent back and attaches the page again.
            back = since_ok[-1] if since_ok and since_ok[-1]["answer"] == "no" else None
            said = " ".join(str((back or {}).get("notes") or "").split())
            line = f"{_name(conn, back['by_id'])} sent back the new commits: {said}" if back else lapsed.strip()
            return Standing(requirement, NOT_MET, owner, sentences(line, ATTACH_AGAIN))
    if answer and answer["answer"] == "ok":
        return Standing(requirement, MET, line="OK'd")
    if answer and answer["answer"] == "no":
        notes = (answer.get("notes") or "").strip()
        line = f"Sent back: {notes}" if notes else "Sent back"
        if requirement.get("person_id") or people_only(requirement):
            document = document_of(requirement)
            line = sentences(line, DOCUMENT_OK_HINT.format(document=document) if document else PAGE_OK_HINT)
        return Standing(requirement, NOT_MET, owner, line)
    verdict = V.get(conn, page["file_id"], page["version"], page.get("rubric_hash") or "")
    said = (verdict or {}).get("verdict")
    reasons = " ".join(str((verdict or {}).get("reasons") or "").split())
    if said is None:
        return Standing(requirement, WAITING, JEV, "Decision model is checking")
    oks_anyway = bool(requirement.get("person_id") or people_only(requirement))
    # Jev's verdict on a spec predicts nothing a person decides (task #351: specs it passed were sent back 49% of the
    # time, those it was unsure of 36%), so on a Spec page it never gates: a missing part goes to the person as its line.
    gates = jev_gates(requirement)
    if (
        said == "not_met"
        and gates
        and not (oks_anyway and _previous_page(conn, NOT_MET_BEFORE_SQL, visit_id, requirement, page["file_id"]))
    ):
        return Standing(
            requirement,
            NOT_MET,
            owner,
            f"Decision model sent it back: {reasons}" if reasons else "Decision model sent it back",
        )
    # This version was asked of someone once Jev had looked: its wait counts from then, not from the stage's start.
    asked = max(str(page["created_at"]), str((verdict or {}).get("updated_at") or ""))
    first = ""
    if signoff:
        # Commits pushed after the reviews are a bot's to review before the person is asked (`_new_commits`).
        held, first = _new_commits(conn, task, requirement, asked, since_ok)
        if held:
            return held
    person = person_of(conn, requirement, visit_id)
    back = _sent_back(conn, visit_id, requirement["id"]) if oks_anyway else None
    if back and not requirement.get("person_id"):
        person = person or str(back["by_id"])  # the person who sent it back is asked again, by Tico, not by the bot
    if lapsed_by and not requirement.get("person_id") and lapsed_by.startswith("human:"):
        person = person or (lapsed_by if active_person(conn, lapsed_by) else None)
    if lapsed_ok:
        lapsed = sentence(_since_ok(conn, task, lapsed_ok, person)) + " "
    # Why a person is asked again (new commits, a bot's read of them) is a notice beside the approval, never pasted into
    # its row: the row is the document's name (or the requirement's), and the version line says which version.
    notices = tuple(part.strip() for part in (lapsed, first) if part.strip())
    if said == "not_met":
        # A page a person OKs anyway that Jev sent back before on this visit: the owner had its round, so the person
        # decides with Jev's reason, as on an unsure (Initech, Nov 11: a page Jev could never pass left only a push-past).
        # A Spec page Jev never gates: the person sees the part that is missing.
        said_why = f"Decision model: {reasons}" if reasons else "Decision model: a part is missing"
        if gates:
            said_why = (
                f"Decision model sent it back again: {reasons}" if reasons else "Decision model sent it back again"
            )
        # The decision model only advises the person: what it found is a notice beside the approval, the row its name.
        return Standing(
            requirement, WAITING, person, approval_line(requirement), since=asked, notices=(*notices, said_why)
        )
    if said in ("unsure", "failed"):
        # A failed verdict's reason is why the calls failed (last_error): it was Jev, not the page.
        failure = " ".join(str((verdict or {}).get("last_error") or "").split())
        line = (
            (f"Decision model couldn't check this: {failure}" if failure else "Decision model couldn't check this")
            if said == "failed"
            else (f"Decision model is unsure: {reasons}" if reasons else "Decision model is unsure")
        )
        if oks_anyway:
            # A person OKs this page whatever Jev says: they are asked at once, every version. A bot round trip adds
            # nothing (Acme, Nov 11: after a send back, v2 went to nobody and the Builder improvised asks). Jev's can't
            # tell names nothing to fix and predicts nothing they decide (task #351: people OK'd 41 of 45 QA pages it
            # was unsure of), so they see nothing from Jev, as when every part is there.
            return Standing(
                requirement,
                WAITING,
                person,
                approval_line(requirement),
                since=asked,
                notices=notices,
                unchecked=said == "failed",
            )
        routed = _unsure_to_bots(conn, task, requirement, line)
        if routed:
            routed.since = asked if routed.state == WAITING else None
            return routed
        if _previous_page(conn, ASKED_ONCE_SQL, visit_id, requirement, page["file_id"]):
            return Standing(requirement, NOT_MET, owner, line)
        return Standing(requirement, WAITING, person, line, since=asked)
    if oks_anyway:
        # A people-only page that names nobody (or whose person left) is any person's: Jev passing it is never enough.
        return Standing(requirement, WAITING, person, approval_line(requirement), since=asked, notices=notices)
    return Standing(requirement, MET, line="Decision model passed it")


def _same_code_ok(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row, page: Row) -> bool:
    """Whether `answer`, given on an earlier version of the page now attached, is a person's OK that still holds: the
    same file (another page is another thing to OK), and the linked PRs' newest commits the ones it was given at."""
    if document_of(requirement):
        return False
    if answer["answer"] != "ok" or not str(answer["by_id"]).startswith("human:"):
        return False
    heads = _heads_of(answer.get("heads"))
    if not heads or heads != heads_now(conn, task):
        return False
    shown = conn.execute(
        "SELECT file_id, version FROM requirement_pages WHERE visit_id=? AND requirement_id=? AND created_at<=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (answer["visit_id"], requirement["id"], answer["created_at"]),
    ).fetchone()
    return bool(shown) and shown[0] == page["file_id"] and shown[1] == answer.get("page_version")


def short_heads(heads: dict[str, object] | None) -> str:
    """The commits an OK was given at, as a person reads them: one PR's short head, or each PR's."""
    found = sorted((heads or {}).items())
    if len(found) == 1:
        return str(found[0][1])[:7]
    return ", ".join(f"{ref} at {str(sha)[:7]}" for ref, sha in found)


def _since_ok(conn: "sqlite3.Connection", task: Row, ok: Row, person: str | None) -> str:
    """Why `person` is asked again after new commits did not keep their OK (`ok`): "2 new commits since your OK at
    b4b690b: 'card shows a pick wait's line' and 1 more, 1 file." (item_sources.github.since: GitHub's compare of each
    commit). While Tico has not read what the commits are: "New commits since Ben's OK"."""
    from .item_sources import github as F

    kept = _heads_of(ok.get("heads")) or {}
    whose = "your" if person and person == str(ok["by_id"]) else f"{_name(conn, ok['by_id'])}'s"
    came = F.since(conn, kept, cast("dict[str, object]", heads_now(conn, task)))
    if not came or not came["commits"]:
        return f"New commits since {_name(conn, ok['by_id'])}'s OK"  # as before Tico kept what commits are
    count, titles, files = came["commits"], came["titles"], came["files"]
    what = f"{count} new commit{'s' if count != 1 else ''} since {whose} OK at {short_heads(kept)}"
    if titles:
        more = count - 1
        what += f": '{titles[0]}'" + (f" and {more} more" if more else "")
    return f"{what}, {files} file{'s' if files != 1 else ''}"


def ok_kept(conn: "sqlite3.Connection", task: Row, requirement: Row, page: Row) -> Row | None:
    """The person's OK that a page just attached (`page`, a `requirement_pages` row) keeps: given on an earlier version
    of the same page on this visit, at the commits the PRs are at now (`_same_code_ok`), as `_page` counts it. None when
    the requirement is not a person's OK, or no OK carries. task_pages.attach says so on the task and to the bot."""
    settings: Row = requirement.get("settings") or {}
    if settings.get("at_head") or not (requirement.get("person_id") or people_only(requirement)):
        return None
    visit_id = task["visit"]["id"]
    answer = newest_answers(conn, visit_id).get(requirement["id"])
    if answer and _reviews_new(requirement, answer):
        answer = _newest_signoff(conn, visit_id, requirement)
    if not answer or answer.get("page_version") == page["version"]:
        return None
    return answer if _same_code_ok(conn, task, requirement, answer, page) else None


def document_of(requirement: Row) -> str | None:
    """The task document a stage's page requirement approves (`settings.document`: Spec, QA report, Release report or
    Invoice), or None for a page attached by hand. A bot writing that document while the task is in the stage submits
    it (task_pages.submit)."""
    settings: Row = requirement.get("settings") or {}
    found = settings.get("document")
    return str(found) if requirement.get("kind") == "page" and isinstance(found, str) and found else None


def approval_line(requirement: Row) -> str:
    """What a page a person approves says while it waits on them: the document's name (or the requirement's). Which
    version, who wrote it and what changed is the version line beside the document, never pasted into the row."""
    return document_of(requirement) or name_of(requirement)


def jev_gates(requirement: Row) -> bool:
    """Whether the decision model's not met sends a page back to its owner: everywhere but a Spec a person approves,
    where it only shows the person the part that is missing (`_page`)."""
    oks_anyway = bool(requirement.get("person_id") or people_only(requirement))
    return not (oks_anyway and (document_of(requirement) == "Spec" or _check_of(requirement) == "spec"))


def _check_of(requirement: Row) -> str | None:
    """Which of Jev's shipped checks a page requirement's rubric is (pipeline_judges.check_of), or None."""
    from . import pipeline_judges as PJ

    settings: Row = requirement.get("settings") or {}
    return PJ.check_of(settings.get("rubric") or "")


def last_sent_back(
    conn: "sqlite3.Connection", requirement_id: str, *, visit_id: str | None = None, task_id: str | None = None
) -> Row | None:
    """The newest send back of a requirement that is not undone, on one visit, on any visit of one task, or on any
    visit: {id, visit_id, by_id, notes, page_version, created_at}, or None. Every reader of "the last send back" reads
    it here, so how a send back is stored is read in one place."""
    sql = (
        "SELECT a.id, a.visit_id, a.by_id, a.notes, a.page_version, a.created_at FROM answers a "
        "JOIN visits v ON v.id = a.visit_id WHERE a.requirement_id=? AND a.answer='no' AND a.undone_at IS NULL"
    )
    args: list[str] = [requirement_id]
    if visit_id is not None:
        sql, args = sql + " AND a.visit_id=?", [*args, visit_id]
    if task_id is not None:
        sql, args = sql + " AND v.task_id=?", [*args, task_id]
    cursor = conn.execute(sql + " ORDER BY a.created_at DESC, a.id DESC LIMIT 1", args)
    found = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], found, strict=True)) if found else None


def _sent_back(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> Row | None:
    """The newest send back of an earlier version of this page on this visit, by a person who can still act: who said
    it (`by_id`) and what (`said`). A page a person OKs comes back to them marked with it."""
    found = last_sent_back(conn, requirement_id, visit_id=visit_id)
    if not found or not str(found["by_id"] or "").startswith("human:") or not active_person(conn, found["by_id"]):
        return None
    return {"by_id": str(found["by_id"]), "said": " ".join(str(found["notes"] or "").split())}


# The heads each met Review page was for: a page `at_head` keeps the linked PRs' newest commits as they were when it was
# attached (`item_versions`), and it counts where Jev passed it or someone OK'd that version. The last page of each
# visit only: a replaced one was never the review.
REVIEWED_SQL = (
    "SELECT p.item_versions FROM requirement_pages p JOIN visits v ON v.id = p.visit_id "
    "JOIN requirements r ON r.id = p.requirement_id WHERE v.task_id=? AND p.replaced_at IS NULL "
    "AND json_valid(r.settings) AND json_extract(r.settings, '$.at_head') "
    "AND (EXISTS (SELECT 1 FROM page_verdicts j WHERE j.file_id = p.file_id AND j.version = p.version "
    "AND j.rubric_hash = p.rubric_hash AND j.verdict = 'met') "
    "OR EXISTS (SELECT 1 FROM answers a WHERE a.visit_id = p.visit_id AND a.requirement_id = p.requirement_id "
    "AND a.page_version = p.version AND a.answer = 'ok' AND a.undone_at IS NULL))"
)
NOT_REVIEWED = "not reviewed by a bot."
# The bot that wrote the task's newest review: it reviews commits pushed after it (`_new_commits`).
REVIEWER_SQL = (
    REVIEWED_SQL.replace("SELECT p.item_versions", "SELECT p.attached_by_id")
    + " ORDER BY p.created_at DESC, p.id DESC LIMIT 1"
)
# A bot's OK of the commits pushed after the reviews, at the commits it names (`_new_commits`): they count as reviewed.
NEW_REVIEWED_SQL = (
    "SELECT a.by_id, a.heads, r.person_id FROM answers a JOIN visits v ON v.id = a.visit_id "
    "JOIN requirements r ON r.id = a.requirement_id WHERE v.task_id=? AND r.kind='page' AND a.answer='ok' "
    "AND a.undone_at IS NULL AND a.heads IS NOT NULL AND (r.who_oks = 'people_only' OR r.person_id IS NOT NULL)"
)
# What the bot that wrote the newest review is asked when commits came after it, before a person OKs a page that is not
# a review (QA's page). It does so DELTA_ROUNDS times a visit at most: after that the person is asked, told
# that the newest commits had no bot's review. The Builder's fixes after a send back each start a round.
REVIEW_NEW = "Review the new commits"
# What the Builder is told when the person's OK, brought back from Merge, lapsed: the page is OK'd again.
ATTACH_AGAIN = "Attach the QA page again"
DELTA_ROUNDS = 2


def heads_now(conn: "sqlite3.Connection", task: Row) -> dict[str, str]:
    """{pr ref: newest commit} of the task's linked PRs Tico has read (item_sources.github.heads)."""
    from .item_sources import github as F

    return {ref: str(sha) for ref, sha in F.heads(conn, task["id"]).items() if sha}


def _heads_of(text: object) -> dict[str, object] | None:
    try:
        found = json.loads(str(text)) if text else None
    except ValueError:
        return None
    return cast("dict[str, object]", found) if isinstance(found, dict) else None


def _new_heads(conn: "sqlite3.Connection", task: Row) -> list[tuple[str, str]]:
    """The linked PRs whose newest commit no bot reviewed: no met Review page was for it (`REVIEWED_SQL`), and no bot
    OK'd it as the commits after the reviews (`NEW_REVIEWED_SQL`). Empty when the task had no Review page."""
    heads = heads_now(conn, task)
    if not heads:
        return []
    reviewed: list[dict[str, object]] = []
    for found in conn.execute(REVIEWED_SQL, (task["id"],)).fetchall():
        if (versions := _heads_of(found[0] or "{}")) is not None:
            reviewed.append(versions)
    if not reviewed:
        return []
    for by, text, named in conn.execute(NEW_REVIEWED_SQL, (task["id"],)).fetchall():
        if _is_bot(by) and by != named and (versions := _heads_of(text)) is not None:
            reviewed.append(versions)
    return [(ref, sha) for ref, sha in sorted(heads.items()) if not any(each.get(ref) == sha for each in reviewed)]


def _where(conn: "sqlite3.Connection", task: Row, new: list[tuple[str, str]]) -> str:
    if len(heads_now(conn, task)) == 1:
        return f"now at {new[0][1][:7]}"
    return ", ".join(f"{ref} at {sha[:7]}" for ref, sha in new)


def unreviewed(conn: "sqlite3.Connection", task: Row) -> str | None:
    """One line when a linked PR's newest commit is not one a bot reviewed (`_new_heads`): "New commits since the
    reviews (now at eace67e): not reviewed by a bot." None when every newest commit was reviewed, or the task had no
    Review page. It reads what Tico keeps (the PR's head, each Review page's `item_versions`, the commits a bot's OK
    names), never a page's words. Tico keeps no commit count, so the line names the newest commit, not how many came
    after the review."""
    new = _new_heads(conn, task)
    return f"New commits since the reviews ({_where(conn, task, new)}): {NOT_REVIEWED}" if new else None


def _is_bot(actor: object) -> bool:
    from . import hubdb_core

    core: Any = hubdb_core
    return bool(core.is_bot(actor))


def _name(conn: "sqlite3.Connection", actor: object) -> str:
    from . import hubdb

    H: Any = hubdb
    return str(H.actor_name(conn, actor)) if actor else "someone"


def _reviews_new(requirement: Row, answer: Row) -> bool:
    """Whether `answer`, on a page a person OKs, is a bot's review of the commits after the reviews, not the OK: a bot's
    answer that keeps its commits, unless the page names that bot to OK it."""
    return _is_bot(answer["by_id"]) and bool(answer.get("heads")) and answer["by_id"] != requirement.get("person_id")


def _visit_answers(conn: "sqlite3.Connection", visit_id: str, requirement: Row) -> list[Row]:
    return [
        row
        for row in _cached(conn, "answers_on_visit", ANSWERS_SQL, "visit_id", visit_id)
        if row["requirement_id"] == requirement["id"]
    ]


def _newest_signoff(conn: "sqlite3.Connection", visit_id: str, requirement: Row) -> Row | None:
    """The newest answer on this visit's page that is not a bot's review of new commits (`_reviews_new`)."""
    found = [row for row in _visit_answers(conn, visit_id, requirement) if not _reviews_new(requirement, row)]
    return found[-1] if found else None


def _moved_since(conn: "sqlite3.Connection", task: Row, answer: Row) -> bool:
    """Whether the linked PRs' newest commits are not the ones `answer` was given at. An answer from before Tico kept
    them (`heads` empty) stands."""
    kept = _heads_of(answer.get("heads"))
    return kept is not None and kept != heads_now(conn, task)


def delta_reviewer(conn: "sqlite3.Connection", task: Row) -> str | None:
    """The bot that wrote the task's newest review (Review 2's owner in Engineering), while it is still here. Not the
    task's owner now: a bot never reviews the commits it pushed."""
    from . import hubdb

    H: Any = hubdb
    found = conn.execute(REVIEWER_SQL, (task["id"],)).fetchone()
    who = str(found[0]) if found and found[0] else None
    if not who or not _is_bot(who) or who == task.get("owner_id"):
        return None
    return who if H._actor_exists(conn, who) else None


def page_for(conn: "sqlite3.Connection", task: Row, requirement: Row) -> tuple[Row | None, Row | None]:
    """(the page that counts for a requirement on the task's visit, the person's OK it carries). A page a person OKs
    with none attached on this visit, on a task sent back to this stage from the stage that queues its PRs (Merge, as
    the Release Owner does with new commits after the OK, C22), is the one the person OK'd on the visit before Merge,
    with that OK. Else this visit's page, and no OK carried."""
    from . import task_pages as P

    page = P.current(conn, task["visit"]["id"], requirement["id"])
    if page or not (requirement.get("person_id") or people_only(requirement)):
        return page, None
    ok = _carried_ok(conn, task, requirement)
    return (P.current(conn, str(ok["visit_id"]), requirement["id"]), ok) if ok else (None, None)


def _carried_ok(conn: "sqlite3.Connection", task: Row, requirement: Row) -> Row | None:
    """The person's OK of this page on the task's visit to this stage before the visits to Merge just before this one,
    passing over the stages after this one it went through on its way to Merge (Product's OK release)."""
    from .item_sources import github as F

    visits = conn.execute(
        "SELECT v.id, v.stage_id, s.on_enter, s.position FROM visits v JOIN stages s ON s.id = v.stage_id "
        "WHERE v.task_id=? ORDER BY v.entered_at DESC, v.rowid DESC",
        (task["id"],),
    ).fetchall()
    if len(visits) < 3 or str(visits[0][0]) != str(task["visit"]["id"]):
        return None
    at = 1
    while at < len(visits) and F.queues_prs({"on_enter": visits[at][2]}):
        at += 1
    if at == 1:
        return None
    while at < len(visits) and visits[at][1] != visits[0][1] and int(visits[at][3]) > int(visits[0][3]):
        at += 1
    if at == len(visits) or visits[at][1] != visits[0][1]:
        return None
    ok = _newest_signoff(conn, str(visits[at][0]), requirement)
    if not ok or ok["answer"] != "ok" or not ok.get("heads") or not str(ok["by_id"]).startswith("human:"):
        return None
    return ok


# The end of the line when a task came back from the stage that queues its PRs at the commits the queue ran (task #344).
QUEUED_AS_IS = "The OK from before {stage} comes back once there are new commits"


def _queued_as_is(conn: "sqlite3.Connection", task: Row, requirement: Row) -> Standing | None:
    """The person's OK carried back from Merge (`page_for`) does not count while a linked PR is still at the commit it
    was last queued at: the queue ran that commit and it did not merge, so the task waits for the Builder's fix instead
    of going straight back to be queued as it is (Tico Team, Nov 12-13: four bounces, a queue run each). Once every PR
    has new commits, C22 decides (`_after_ok`). None when no PR is at its queued commit."""
    from .item_sources import github as F

    still = F.queued_as_is(conn, task["id"])
    if not still:
        return None
    one = len(heads_now(conn, task)) == 1
    said: list[str] = []
    for each in still:
        at = str(each["head"])[:7] if one else f"{each['ref']} at {str(each['head'])[:7]}"
        why = ", ".join(str(word) for word in (each.get("reason"), each.get("check")) if word)
        # "failed" only for a failed check: a removal by hand or a conflict says the run did not merge, and why.
        outcome = "it failed" if each.get("failed") else "it did not merge"
        # No removal recorded: it was taken out of Merge while still queued, so the queue may never have run it.
        said.append(
            f"Merge queue ran {at} and {outcome} ({why}): push a fix"
            if why
            else f"Merge queue had {at} and it did not merge: push a fix"
        )
    # The visit it came back from: its stage's name, and the note the move back gave, so the one wake on entering
    # (task_engine._wake_turn, whose "Missing to leave" carries this line) says what the mover said.
    found = conn.execute(
        "SELECT s.name, v.how, v.why FROM visits v JOIN stages s ON s.id = v.stage_id WHERE v.task_id=? "
        "ORDER BY v.entered_at DESC, v.rowid DESC LIMIT 1 OFFSET 1",
        (task["id"],),
    ).fetchone()
    stage = str(found[0]) if found and found[0] else "Merge"
    note = " ".join(str(found[2] or "").split()) if found and found[1] == "back" else ""
    if note:
        said.append(f"Sent back from {stage}: {note}")
    return Standing(requirement, NOT_MET, task.get("owner_id"), sentences(*said, QUEUED_AS_IS.format(stage=stage)))


def _after_ok(conn: "sqlite3.Connection", task: Row, requirement: Row, ok: Row) -> tuple[Standing | None, list[Row]]:
    """New commits since a person's OK (`ok`, a rebase or a conflict fixed, C22): (the standing that keeps the OK or
    holds it for a bot's read, or None when it lapses; the bot reviews of new commits since it). Commits that change
    the same lines as the OK'd ones (item_sources.github.same_changes) keep it; while Tico reads what they change, it
    waits on GitHub. Others go to the bot that wrote the newest review (`_new_commits`, the same DELTA_ROUNDS): its OK
    at the newest commits keeps the person's OK. Its send back, no such bot, or the rounds spent: the OK lapses, and
    the person is asked again once the next commits were read."""
    from .item_sources import github as F

    name = _name(conn, ok["by_id"])
    same = F.same_changes(conn, _heads_of(ok.get("heads")) or {}, cast("dict[str, object]", heads_now(conn, task)))
    if same is None:
        return Standing(requirement, WAITING, F.SOURCE, f"Reading what the commits since {name}'s OK change"), []
    if same:
        return Standing(requirement, MET, line=f"OK'd by {name}; the commits since change the same lines"), []
    since = [
        row
        for row in _visit_answers(conn, task["visit"]["id"], requirement)
        if _reviews_new(requirement, row) and row["created_at"] >= ok["created_at"]
    ]
    if any(row["answer"] == "no" for row in since):
        return None, since
    last = since[-1] if since else None
    if last and _heads_of(last["heads"]) == heads_now(conn, task):
        return Standing(
            requirement, MET, line=f"OK'd by {name}; {_name(conn, last['by_id'])} read the commits since"
        ), since
    held, _ = _new_commits(conn, task, requirement, str(ok["created_at"]), since)
    return held, since


def _new_commits(
    conn: "sqlite3.Connection", task: Row, requirement: Row, asked: str, reviews: list[Row] | None = None
) -> tuple[Standing | None, str]:
    """Before a person OKs a page that is not a review (QA's page): the commits no bot reviewed since the
    reviews (`_new_heads`) go to the bot that wrote the newest review (`delta_reviewer`), which reads only them against
    what the reviews found and the spec, and OKs them or sends them back as an answer on this page, at the commits it
    read (answers.heads). Its send back is the owner's turn to fix them; the next commits are a new round. After
    DELTA_ROUNDS rounds this visit, or with no such bot, the person is asked with "not reviewed by a bot" said. Returns
    the standing that holds the person's ask, or None and what the ask says first. `reviews`: the bot reviews that
    count (after a person's OK, those since it); else all of this visit's."""
    visit_id = task["visit"]["id"]
    if reviews is None:
        reviews = [row for row in _visit_answers(conn, visit_id, requirement) if _reviews_new(requirement, row)]
    here = heads_now(conn, task)
    last = reviews[-1] if reviews else None
    new = _new_heads(conn, task)
    if not new:
        if last and last["answer"] == "ok" and _heads_of(last["heads"]) == here:
            read = f"{_name(conn, last['by_id'])} reviewed the commits since the reviews"
            verdict, rest = verdict_and_rest(last.get("notes"))
            if not verdict:
                return None, sentence(read) + " "
            # The verdict on a line of its own, the rest after a line break: the review screen shows the first and
            # folds the rest behind More (task #389).
            return None, sentence(f"{read}: {verdict}") + (f"\n{rest}" if rest else "") + " "
        return None, ""
    if last and last["answer"] == "no" and _heads_of(last["heads"]) == here:
        said = " ".join(str(last.get("notes") or "").split())
        line = f"{_name(conn, last['by_id'])} sent back the new commits: {said}"
        return Standing(requirement, NOT_MET, task.get("owner_id"), line), ""
    reviewer = delta_reviewer(conn, task)
    rounds = len({str(row["heads"]) for row in reviews})
    if reviewer and rounds < DELTA_ROUNDS:
        line = (
            f"{REVIEW_NEW} since the reviews ({_where(conn, task, new)}): read only them, against what the reviews "
            "found and the spec. OK them or send them back. Your note's first line is the verdict (No blockers, "
            "Blocks: …, or Unsure: …); what you read goes on the lines after it"
        )
        return Standing(requirement, WAITING, reviewer, sentence(line), since=asked), ""
    return None, f"New commits since the reviews ({_where(conn, task, new)}): {NOT_REVIEWED} "


def asked_to_review(standing: Standing, by: object) -> bool:
    """Whether this standing asks `by`, a bot, to review the commits after the reviews (`_new_commits`)."""
    return standing.state == WAITING and bool(by) and standing.on == by and standing.line.startswith(REVIEW_NEW)


def review_commands(task_id: str, requirement_id: str) -> str:
    """The two answers a bot asked to review new commits gives, as commands."""
    answer = f"tico task answer {task_id} {requirement_id}"
    return (
        f'OK them: {answer} ok -m "<the commits you read and what you checked>" --version N. '
        f'Send them back: {answer} no -m "<what must change>" --version N. Do not move the task.'
    )


def _unsure_to_bots(conn: "sqlite3.Connection", task: Row, requirement: Row, line: str) -> Standing | None:
    """Jev unsure of a page on a stage a bot holds (docs/dev/pipelines-v3.md "Jev is checking"; the two sign-offs rule of
    Nov 11 put this before a person). The pipeline's manager bot looks once per version: it gets Jev's line naming the
    part it can't tell, and OKs the page or sends it back, with why, as an answer on the task. The page's own bot is not
    sent back to rewrite on an unsure (task #351: after unsure, 76 reviews and 25 Validate pages were rewritten and
    judged again); a part that is no still goes back to it (`_page`). None, so a person decides as before, when a person
    holds the stage, or no manager bot is set, or the manager is the bot that holds it. `failed` (Jev could not check,
    after its retries) goes the same way: the page may be fine and only Jev was down. A page a person OKs anyway (people
    only, or naming someone) never comes here. The line never carries the check's full wording."""
    from . import waiting_on as W

    held = W.holder(conn, task)
    if not W.is_bot(held):
        return None
    said = sentence(line)
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


def _related_input(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    """Jev picks the bots the work touches and each comments once; met on their comments, on no pick, or on time
    (backend/related_input.py)."""
    from . import related_input

    return related_input.standing(conn, task, requirement, answer)


KINDS: dict[str, Callable[["sqlite3.Connection", Row, Row, "Row | None"], Standing]] = {
    "owner_done": _owner_done,
    "ok": _ok,
    "linked_done": _linked_done,
    "page": _page,
    "item": _item,
    "related_input": _related_input,
}
# The keys a requirement's `settings` may hold, by kind. Anything else is refused at save (pipeline_settings).
# - page: `document` (the task document a person approves, APPROVED: writing it in the stage submits it), `rubric` (Jev's "what to check"; empty means the requirement's line), `at_head` (the page is for the newest
#   commit of every linked PR: a new commit puts it back to the owner), `with_release` (Jev is also shown the release
#   of the task's PRs and of the tasks it filed, so "production is older than the release" is judged against what
#   GitHub says).
# - item: its condition's (item_sources.Condition.settings, `settings_of`).
# - related_input: `bar` (Jev's score a bot needs to be asked, 0 to 1), `most` (bots asked at most), `wait_hours` (how
#   long the stage waits for their comments before it is met anyway). Defaults in backend/related_input.py.
# The documents a stage may name for a person to approve (task_documents: the fixed names a stage approves).
APPROVED = ("Spec", "QA report", "Release report", "Invoice")
SETTINGS: dict[str, tuple[str, ...]] = {
    "owner_done": (),
    "ok": (),
    "linked_done": (),
    "page": ("document", "rubric", "at_head", "with_release"),
    "item": (),
    "related_input": ("bar", "most", "wait_hours"),
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
    """Every requirement of the task's open visit with where it stands, stage's first; before them a merge conflict on
    an open PR of the task while it is before Merge (pr_conflicts: not a stored requirement)."""
    visit = task.get("visit")
    if not visit:
        return []
    # Worked out once per task in a snapshot: Needs you asks for the same task's standings for its list, its brief and
    # its ask. A Standing is never changed after it is made; a write starts the snapshot's values again (read_cache).
    # The key holds every field of the row a standing reads (pr_conflicts reads its status, stage and who works it),
    # so a caller that judges a changed copy of the row gets its own answer, not the stored row's.
    stage: Row = task.get("stage") or {}
    key = (
        "standings",
        task["id"],
        visit["id"],
        visit.get("stage_id"),
        task.get("version"),
        task.get("status"),
        task.get("pipeline_id"),
        task.get("stage_id"),
        stage.get("id"),
        task.get("owner_id"),
        task.get("assignee_id"),
    )
    return list(read_cache.value(conn, key, lambda: _standings(conn, task, visit), shared=True))


def _standings(conn: "sqlite3.Connection", task: Row, visit: Row) -> list[Standing]:
    answers = newest_answers(conn, visit["id"])
    out = [standing(conn, task, r, answers.get(r["id"])) for r in for_visit(conn, visit)]
    from . import pr_conflicts

    # A PR that conflicts with its base branch before Merge: the bot that built it rebases it first, or after a
    # person's answer the stage waits on (pr_conflicts).
    conflict = pr_conflicts.standing(conn, task, out)
    from . import merge_order

    # A task that goes after another one in a stage that queues PRs: held until that one merges (merge_order).
    order = merge_order.standing(conn, task)
    return [s for s in (order, conflict) if s] + out


def unmet(conn: "sqlite3.Connection", task: Row) -> list[Standing]:
    return [s for s in standings(conn, task) if not s.met]


def can_push_past(requirement: Row) -> bool:
    return bool(requirement.get("can_push_past")) and not never_pushed_past(requirement)


def people_only(requirement: Row) -> bool:
    return requirement["kind"] in OK_KINDS and requirement.get("who_oks") == "people_only"
