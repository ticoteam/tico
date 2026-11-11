# pyright: strict
"""What a task needs before it leaves a stage (docs/pipelines-v3.md, "The nine requirement kinds").

A requirement sits on a stage (every task that visits it) or on one visit (this task, this time). Each kind answers one
question for a task's open visit: met, waiting on something named, or not met and why. A kind reads tables only. It never
calls GitHub, Jev or a computer, so a move and the never-stuck check can't be stalled by the outside.

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
# What a requirement waits on when it is not a person or a task: `Standing.on` for the kinds that read saved facts.
JEV, GITHUB = "jev", "github"
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
    """Where one requirement stands for one visit. `on` is who or what it waits on: an actor, `jev`, `github`, or a
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
# `_page` with backend/task_pages.py and Jev's verdicts (page_verdicts), the four GitHub kinds with backend/task_facts.py.
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
    from . import task_facts as F
    from . import task_pages as P

    visit_id = task["visit"]["id"]
    owner = task.get("owner_id")
    page = P.current(conn, visit_id, requirement["id"])
    if not page:
        return Standing(requirement, NOT_MET, owner, "No page attached")
    settings: Row = requirement.get("settings") or {}
    if settings.get("at_head") and json.loads(page["heads"] or "{}") != F.heads(conn, task["id"]):
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


# The four GitHub kinds read saved facts only (backend/task_facts.py) and, for "PR merged", the Merge stage's effects
# (backend/merge_queue.py). With many PRs, the first one not met, in link order, gives the line. A PR closed without
# merging is left out while another is linked; when every one is, the owner is told. A wait on GitHub ends: when GitHub
# has said nothing about the PR (or the releases it waits for) for 2 hours, it is the owner's turn (`_silent`).
def _now() -> Any:
    from . import hubdb

    return hubdb


def _prs(conn: "sqlite3.Connection", task: Row, requirement: Row) -> tuple[list[Row], Standing | None]:
    """The PRs a GitHub kind reads, each with `ref` and `fact`, or the standing when there are none to read."""
    from . import task_facts as F

    links: list[Row] = [
        {**link, "ref": F.pr_ref(str(link["repo"]), int(link["number"]))} for link in F.prs_of(conn, task["id"])
    ]
    if not links:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), "No PR linked")
    live = [link for link in links if not (link["fact"] and link["fact"]["state"] == "closed")]
    if not live:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), f"{links[0]['ref']} was closed without merging")
    return live, None


def _first(requirement: Row, found: list[Standing]) -> Standing:
    """The first unmet standing of many PRs', else the first met one."""
    return next((s for s in found if not s.met), found[0])


def _reading(requirement: Row) -> Standing:
    return Standing(requirement, WAITING, GITHUB, "Reading the PR from GitHub")


def _seconds_since(ts: object) -> float | None:
    H = _now()
    at, now = H.parse_ts(ts), H.parse_ts(H.now())
    return None if at is None or now is None else (now - at).total_seconds()


def _silent(task: Row, requirement: Row, link: Row, found: Standing, heard: object = None) -> Standing:
    """A wait on GitHub about one PR becomes the owner's turn once GitHub has said nothing for SILENT_S: no webhook and
    no read of the PR got through (GitHub down or refusing, or the PR gone). `heard` is when GitHub last answered, when
    the caller knows better than the PR's fact (its releases' read); else the fact's last read, else when it was
    linked. Every wait on GitHub so ends in a bot's turn, whatever the stage's expected time."""
    from . import task_facts as F

    if found.state != WAITING or found.on != GITHUB:
        return found
    fact: Row = link.get("fact") or {}
    quiet = _seconds_since(heard or fact.get("read_at") or fact.get("updated_at") or link.get("created_at"))
    if quiet is None or quiet < F.SILENT_S:
        return found
    return Standing(
        requirement, NOT_MET, task.get("owner_id"), f"GitHub hasn't answered about {link['ref']} for 2 hours"
    )


def _pr_open(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(
        requirement,
        [
            Standing(requirement, MET, line="PR open")
            if link["fact"]
            else _silent(task, requirement, link, _reading(requirement))
            for link in prs
        ],
    )


def _checks_one(task: Row, requirement: Row, link: Row) -> Standing:
    from . import merge_queue
    from . import task_facts as F

    fact, ref = link["fact"], link["ref"]
    if not fact:
        return _reading(requirement)
    if fact["state"] == "merged":
        return Standing(requirement, MET, line="Checks pass")
    state = fact["checks_state"]
    if state == "failure":
        return Standing(
            requirement, NOT_MET, task.get("owner_id"), f"Checks failed on {ref}: {merge_queue.failing_names(fact)}"
        )
    if state == "pending":
        running = _seconds_since(fact.get("checks_started_at"))
        if running is not None and running >= F.CHECKS_TOO_LONG_S:
            return Standing(requirement, NOT_MET, task.get("owner_id"), "Checks still running after 2 hours")
        return Standing(requirement, WAITING, GITHUB, "Checks running")
    if state == "none":
        seen = _seconds_since(fact.get("head_seen_at"))
        if seen is None or seen < F.NO_CHECKS_AFTER_S:
            return Standing(requirement, WAITING, GITHUB, "Waiting for checks")
        return Standing(requirement, MET, line="No checks on this repo")  # C13
    return Standing(requirement, MET, line="Checks pass")


def _checks_pass(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    found = [_silent(task, requirement, link, _checks_one(task, requirement, link)) for link in prs]
    if all(s.met for s in found):
        return Standing(requirement, MET, line=next((s.line for s in found if s.line == "Checks pass"), found[0].line))
    return _first(requirement, found)


def _merged_one(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    from . import merge_queue

    fact, ref = link["fact"], link["ref"]
    if not fact:
        return _reading(requirement)
    if fact["state"] == "merged":
        return Standing(requirement, MET, line="Merged")
    owner = task.get("owner_id")
    queues = merge_queue.queues_prs(task.get("stage"))
    effect = merge_queue.newest(conn, task["visit"]["id"], ref) if queues else None
    if effect and effect["state"] == "owed":
        return Standing(requirement, WAITING, GITHUB, "Queuing the PR")
    if effect and effect["state"] == "failed":
        return Standing(requirement, NOT_MET, owner, f"GitHub refused the merge: {effect.get('last_error') or ref}")
    if fact["queue_state"] == "removed":
        return Standing(requirement, NOT_MET, owner, merge_queue.removed_line(fact))
    if fact["queue_state"] in ("queued", "auto_merge") or effect:
        return Standing(requirement, WAITING, GITHUB, "In the merge queue")
    if queues:
        return Standing(requirement, WAITING, GITHUB, "Queuing the PR")  # owed as soon as its head is known
    return Standing(requirement, NOT_MET, owner, "Not merged yet")


def _pr_merged(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(
        requirement, [_silent(task, requirement, link, _merged_one(conn, task, requirement, link)) for link in prs]
    )


def _release_one(conn: "sqlite3.Connection", requirement: Row, link: Row) -> Standing:
    from . import task_facts as F

    fact = link["fact"]
    if not fact:
        return _reading(requirement)
    if fact["state"] != "merged" or not fact.get("merged_sha"):
        return Standing(requirement, WAITING, GITHUB, "Not merged yet")
    settings: Row = requirement.get("settings") or {}
    repo = F.release_repo(settings, str(link["repo"]))
    publishes = F.publishes_releases(conn, repo)
    if publishes is None:
        return Standing(requirement, WAITING, GITHUB, "Reading releases")
    if not publishes:
        return Standing(requirement, MET, line="This repo doesn't publish releases")  # C31
    for release in reversed(F.releases(conn, repo, prereleases=bool(settings.get("prereleases")))):
        if F.included(conn, release["id"], str(fact["merged_sha"])):
            return Standing(requirement, MET, line=f"In {release['tag']}")
    return Standing(requirement, WAITING, GITHUB, f"Waiting for a release of {repo}")


def _in_release(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(requirement, [_release_silent(conn, task, requirement, link) for link in prs])


def _release_silent(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    """In a release for one PR, with `_silent`: a merged PR is not read again, so once it merged GitHub's word is the
    read of the release repo's releases (never read yet: since it merged)."""
    from . import task_facts as F

    found = _release_one(conn, requirement, link)
    fact: Row = link.get("fact") or {}
    if fact.get("state") != "merged":
        return _silent(task, requirement, link, found)
    repo = F.release_repo(requirement.get("settings") or {}, str(link["repo"]))
    return _silent(task, requirement, link, found, F.releases_read_at(conn, repo) or fact.get("updated_at"))


KINDS: dict[str, Callable[["sqlite3.Connection", Row, Row, "Row | None"], Standing]] = {
    "owner_done": _owner_done,
    "ok": _ok,
    "answer": _answer,
    "linked_done": _linked_done,
    "page": _page,
    "pr_open": _pr_open,
    "checks_pass": _checks_pass,
    "pr_merged": _pr_merged,
    "in_release": _in_release,
}
# The keys a requirement's `settings` may hold, by kind. Anything else is refused at save (pipeline_settings).
# - page: `rubric` (Jev's "what to check"; empty means the requirement's line), `at_head` (the page is for the newest
#   commit of every linked PR: a new commit puts it back to the owner), `with_release` (Jev is also shown the release
#   of the tasks this task filed, so "production is older than the release" is judged against a fact).
# - in_release: `release_repo` ("owner/name"; empty means the server's release repo, else each PR's own repo) and
#   `prereleases` (a pre-release counts, D3; off by default).
SETTINGS: dict[str, tuple[str, ...]] = {
    "owner_done": (),
    "ok": (),
    "answer": (),
    "linked_done": (),
    "page": ("rubric", "at_head", "with_release"),
    "pr_open": (),
    "checks_pass": (),
    "pr_merged": (),
    "in_release": ("release_repo", "prereleases"),
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
