# pyright: strict
"""Review mode: the ▶ queue a reviewer works through, claims, Approve with its 10-second undo, send-back to the
assignee, Message manager and Mark met. Track C owns this module.

A person reviews one requirement of one ticket at a time. The queue lists what waits on them (a requirement whose
reviewer is them, a send-back routed to them, or a backup after the manager asked), Late first and then oldest. A claim
tells a second reviewer "Chris is reviewing" for ten minutes.

Every write here is one atomic step: `_atomic` opens a write transaction (or a savepoint inside the caller's, which is
how the routes run, under `Store.mutate`'s BEGIN IMMEDIATE). The version check and the decision are made inside that
step, so two reviewers, an approve racing a push, and an undo after the card moved are decided by the database, never
by a Python lock:

- Approve names the work version, attachment digest and requirement version the reviewer saw; any difference refuses it.
- Approve that meets the stage's last requirement moves the ticket to the next stage (or through a `move` link).
- Undo works for 10 seconds, only for the approver, and only while the ticket is still where the approval left it.
- Only a person approves (D10). A bot, or a requirement nobody reviews by person, refuses.
- Mark met is a rule editor's override of one requirement, bound to the ticket's current work version (D31/D43).

A requirement's standing (`requirement_state`) is computed here from the decisions, as the engine contract says: `met`
is the latest non-undone met/override decision at the current work version whose attachment was not replaced.
"""

import itertools
import json
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from pydantic import Field

from . import models as M
from . import pipeline_engine, pipeline_github_checks, pipeline_links, pipeline_rights, requirement_attachments
from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Generator, Iterable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

CLAIM_MINUTES = 10
UNDO_SECONDS = 10
NOTE_BYTES = 500
ACTIVE = ("open", "doing", "waiting", "review", "ready")
PERSON_REVIEWED = ("person", "bot_then_person")


def _review_of(requirement: dict[str, Any]) -> str:
    """The review that applies to a requirement. A field or run is a rule (D60): only a person's review of one waits on its
    source, and a bot or bot-then-person review of one (rows from before the API refused it) reads as nobody's, as
    pipeline_engine._standing reads it."""
    if requirement["kind"] in ("field", "run") and requirement["reviewed_by"] != "person":
        return "nobody"
    return str(requirement["reviewed_by"])


Row = dict[str, Any]
CURRENT: Any = object()  # approve(): no version named, so the current one is taken (a caller inside the engine)


class Declined(Problem, cast(Any, H).Refused):
    """A refusal that is both a hubdb.Refused (callers inside a write catch that) and a Problem (it keeps its code, status
    and extra for the API)."""

    def __init__(self, code: str, detail: str, status: int = 400, extra: dict[str, Any] | None = None) -> None:
        cast(Any, Problem).__init__(self, code, detail, status, extra=extra)
        self.rule, self.severity, self.task = code, "normal", None


def _hub() -> Any:
    return H


def _now() -> str:
    return str(_hub().now())


def _rows(c: "sqlite3.Connection", sql: str, args: tuple[Any, ...] = ()) -> list[Row]:
    return [dict(row) for row in c.execute(sql, args).fetchall()]


def _one(c: "sqlite3.Connection", sql: str, args: tuple[Any, ...] = ()) -> Row | None:
    row = c.execute(sql, args).fetchone()
    return dict(row) if row is not None else None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _ids(values: Any) -> str:
    """The distinct non-empty values as a JSON array, to bind once as `IN (SELECT value FROM json_each(?))`."""
    return json.dumps(sorted({str(v) for v in values if v}))


def _shift(ts: str, **delta: float) -> str:
    return str(_hub().shift(ts, **delta))


@contextmanager
def _atomic(c: "sqlite3.Connection") -> "Generator[None]":
    """One all-or-nothing write: a savepoint inside the caller's write transaction, else its own BEGIN IMMEDIATE."""
    if c.in_transaction:
        name = "pipeline_review_" + uuid.uuid4().hex[:8]
        c.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            c.execute(f"ROLLBACK TO {name}")
            c.execute(f"RELEASE {name}")
            raise
        c.execute(f"RELEASE {name}")
        return
    c.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")


def _clip(text: str | None) -> str | None:
    """`text` cut to NOTE_BYTES bytes (a decision's reason is capped there by the table); None when empty."""
    value = (text or "").strip()
    if not value:
        return None
    return value.encode("utf-8")[:NOTE_BYTES].decode("utf-8", "ignore")


def _person(actor: str) -> str:
    """The actor, when it is a person; a bot or anything else refuses (D10: a bot never approves)."""
    if not str(actor or "").startswith("human:"):
        raise Declined("not_person", "Only a person reviews", 403)
    return actor


# ----------------------------------------------------------------------------- reading
CHUNK = 500  # ids one read binds as a JSON array; a longer queue reads in more chunks, never more reads per row
Key = tuple[str, str]  # (ticket id, requirement id)


def _chunks(values: "Iterable[Any]") -> list[str]:
    """The distinct non-empty values, sorted, as JSON arrays of at most CHUNK each, for `IN (SELECT value FROM json_each(?))`."""
    ordered = sorted({str(v) for v in values if v})
    return [json.dumps(ordered[i : i + CHUNK]) for i in range(0, len(ordered), CHUNK)]


def _read(c: "sqlite3.Connection", sql: str, *dimensions: "Iterable[Any]") -> list[Row]:
    """Runs `sql` once per chunk of each dimension's ids (one `?` per dimension, in order) and returns every row. A
    (ticket, requirement) pair falls in one run, so the rows of one pair keep the order that run gave them."""
    rows: list[Row] = []
    for chunk in itertools.product(*(_chunks(values) for values in dimensions)):
        rows.extend(_rows(c, sql, chunk))
    return rows


def _task(c: "sqlite3.Connection", task_id: str) -> Row:
    task = cast("Row | None", _hub().task(c, task_id))
    if task is None:
        raise Declined("not_found", "No such ticket", 404)
    return task


def _tasks(c: "sqlite3.Connection", task_ids: "Iterable[str]") -> dict[str, Row]:
    """The tickets as `hubdb.task` returns them (the TASKS view, with its tags), read once per chunk."""
    sql = f"SELECT * FROM {_hub().TASKS} tasks WHERE id IN (SELECT value FROM json_each(?))"
    found: dict[str, Row] = {}
    for chunk in _chunks(task_ids):
        rows = _rows(c, sql, (chunk,))
        _hub().hydrate_task_tags(c, rows)
        found.update({row["id"]: row for row in rows})
    return found


def _revision(task: Row) -> int:
    return int(task.get("work_revision") or 0)


def _requirement(c: "sqlite3.Connection", requirement_id: str) -> Row:
    found = _one(c, "SELECT * FROM stage_requirements WHERE id=?", (requirement_id,))
    if found is None:
        raise Declined("not_found", "No such requirement", 404)
    return found


def _current_attachment(c: "sqlite3.Connection", task_id: str, requirement_id: str) -> Row | None:
    """The newest attachment against the requirement that was not removed."""
    return _one(
        c,
        "SELECT * FROM requirement_attachments WHERE task_id=? AND stage_requirement_id=? AND removed_at IS NULL "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (task_id, requirement_id),
    )


def _open_visit(c: "sqlite3.Connection", task_id: str) -> Row | None:
    return _one(c, "SELECT * FROM stage_visits WHERE task_id=? AND left_at IS NULL", (task_id,))


def _later(created_at: Any, than: Any) -> bool:
    """Whether `created_at` is after `than`. A missing time is never after, as SQL reads a comparison with NULL."""
    return created_at is not None and than is not None and created_at > than


def _routed_in(decisions: list[Row]) -> str | None:
    """Who the latest send-back routed the next attachment to (it skips the bot), if anyone."""
    for decision in decisions:
        if decision["verdict"] == "sent_back":
            return cast("str | None", decision["route_next_to"])
    return None


def _bot_passed_in(decisions: list[Row], revision: int) -> bool:
    """Whether the bot's latest call at this work version, for bot_then_person, was unsure or failed."""
    for decision in decisions:
        if decision["judge"] in ("llm", "bot") and decision["work_version"] == revision:
            return decision["verdict"] in ("unsure", "failed")
    return False


def _sent_back_in(decisions: list[Row], revision: int, attachment: Row | None) -> bool:
    """Whether the newest send-back still holds the item off the queue: nothing new was attached since and the work did
    not change. The newest attachment is the latest one, so it is after the send-back exactly when something newer came."""
    for decision in decisions:
        if decision["verdict"] != "sent_back":
            continue
        newer = attachment is not None and _later(attachment["created_at"], decision["created_at"])
        return decision["work_version"] == revision and not newer
    return False


def _why_in(actor: str, requirement: Row, routed: str | None, asked: bool) -> str | None:
    """Why `actor` is to see this item: `reviewer`, `sent_back` (routed to them) or `backup`; None when they do not."""
    if requirement.get("reviewer_id") == actor:
        return "reviewer"
    if routed == actor:
        return "sent_back"
    if requirement.get("backup_reviewer_id") == actor and asked:
        return "backup"
    return None


def _waits_in(requirement: Row, state: str, decisions: list[Row], revision: int, attachment: Row | None) -> bool:
    """Whether the requirement now waits for a person's decision: not met, something to decide on, not held off by a
    send-back, and for bot_then_person the bot passed it up or a send-back routed it to a person."""
    if requirement.get("retired_at") or _review_of(requirement) not in PERSON_REVIEWED:
        return False
    if state in ("met", "missing"):
        return False
    if _sent_back_in(decisions, revision, attachment):
        return False
    if _review_of(requirement) == "bot_then_person":
        return _routed_in(decisions) is not None or _bot_passed_in(decisions, revision)
    return True


class _Facts:
    """What the review rules read about (ticket, requirement) pairs, each kind fetched once for all of them: the decisions
    not undone, the newest attachment, each ticket's open visit, and the visits the manager asked the backup about. The
    queue and the single-row helpers read the rules through this, so they cannot disagree about an item. A requirement's
    standing is the engine's (pipeline_engine.task_standings), never a second reading of the decisions."""

    def __init__(self) -> None:
        self.standing: dict[str, dict[str, str]] = {}  # ticket id -> {requirement id: the engine's state}
        self.decisions: dict[Key, list[Row]] = {}
        self.attachments: dict[Key, Row] = {}
        self.visits: dict[str, Row] = {}
        self.labels: dict[str, set[str]] = {}  # ticket id -> its label keys (a requirement may apply to one label)
        self.asked: set[Key] = set()  # (visit id, requirement id) the manager asked the backup about

    @classmethod
    def load(cls, c: "sqlite3.Connection", task_ids: "Iterable[str]", requirements: "Iterable[Row]") -> "_Facts":
        facts = cls()
        tasks = list(task_ids)
        reqs = [r["id"] for r in requirements]
        for decision in _read(
            c,
            "SELECT * FROM stage_decisions WHERE undone_at IS NULL AND task_id IN (SELECT value FROM json_each(?)) "
            "AND stage_requirement_id IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC, rowid DESC",
            tasks,
            reqs,
        ):
            facts.decisions.setdefault((decision["task_id"], decision["stage_requirement_id"]), []).append(decision)
        for attachment in _read(
            c,
            "SELECT * FROM requirement_attachments WHERE removed_at IS NULL "
            "AND task_id IN (SELECT value FROM json_each(?)) "
            "AND stage_requirement_id IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC, rowid DESC",
            tasks,
            reqs,
        ):
            facts.attachments.setdefault((attachment["task_id"], attachment["stage_requirement_id"]), attachment)
        for visit in _read(
            c, "SELECT * FROM stage_visits WHERE left_at IS NULL AND task_id IN (SELECT value FROM json_each(?))", tasks
        ):
            facts.visits.setdefault(visit["task_id"], visit)
        for label in _read(
            c,
            "SELECT tt.task_id AS task_id, g.key AS key FROM task_tags tt JOIN tags g ON g.id = tt.tag_id "
            "WHERE tt.task_id IN (SELECT value FROM json_each(?))",
            tasks,
        ):
            facts.labels.setdefault(label["task_id"], set()).add(str(label["key"]))
        facts.standing = pipeline_engine.task_standings(c, tasks)
        visit_ids = [visit["id"] for visit in facts.visits.values()]
        for asked in _read(
            c,
            "SELECT stage_visit_id, stage_requirement_id FROM pipeline_manager_actions WHERE action='ask_backup' "
            "AND stage_visit_id IN (SELECT value FROM json_each(?)) "
            "AND stage_requirement_id IN (SELECT value FROM json_each(?))",
            visit_ids,
            reqs,
        ):
            facts.asked.add((asked["stage_visit_id"], asked["stage_requirement_id"]))
        return facts

    def counts(self, task: Row, requirement: Row) -> bool:
        """Whether the engine holds the ticket to this requirement where it stands (its current stage, or a stage it left)."""
        return str(requirement["id"]) in self.standing.get(str(task["id"]), {})

    def state(self, task: Row, requirement: Row) -> str:
        """`met`, `failed`, `missing` (an attachment requirement with nothing attached) or `waiting` (anything else: a
        reviewer is to decide, or the requirement is out of date). The engine's standing, mapped to the review's words."""
        engine = self.standing.get(str(task["id"]), {}).get(str(requirement["id"]), "")
        if engine in ("met", "failed"):
            return engine
        if engine == "none" and requirement["kind"] == "attachment":
            return "missing"
        return "waiting"

    def applies(self, task: Row, requirement: Row) -> bool:
        return pipeline_engine.applies(requirement, self.labels.get(task["id"], set()))

    def why_sees(self, actor: str, task_id: str, requirement: Row) -> str | None:
        key = (task_id, requirement["id"])
        visit = self.visits.get(task_id)
        asked = visit is not None and (visit["id"], requirement["id"]) in self.asked
        return _why_in(actor, requirement, _routed_in(self.decisions.get(key, [])), asked)

    def waits_on_person(self, task: Row, requirement: Row) -> bool:
        key = (task["id"], requirement["id"])
        return _waits_in(
            requirement,
            self.state(task, requirement),
            self.decisions.get(key, []),
            _revision(task),
            self.attachments.get(key),
        )


def requirement_state(c: "sqlite3.Connection", task: Row, requirement: Row) -> str:
    """`met`, `failed` (the latest judgement at this work version), `waiting` (a reviewer is to decide) or `missing`
    (an attachment requirement with nothing attached)."""
    return _Facts.load(c, [task["id"]], [requirement]).state(task, requirement)


def _why_sees(c: "sqlite3.Connection", actor: str, task_id: str, requirement: Row) -> str | None:
    """Why `actor` is to see this item, or None. Only these see it, whatever else they may do on the ticket."""
    return _Facts.load(c, [task_id], [requirement]).why_sees(actor, task_id, requirement)


def _waits_on_person(c: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """Whether the item waits for a person's decision now (see `_waits_in`)."""
    return _Facts.load(c, [task["id"]], [requirement]).waits_on_person(task, requirement)


def _open_claim(c: "sqlite3.Connection", task_id: str, requirement_id: str, now: str | None = None) -> Row | None:
    """The unexpired claim on the item. An expired one counts as released."""
    return _one(
        c,
        "SELECT * FROM review_claims WHERE task_id=? AND stage_requirement_id=? AND released_at IS NULL "
        "AND (expires_at IS NULL OR expires_at>?)",
        (task_id, requirement_id, now or _now()),
    )


def _named(owner: str, humans: dict[str, Any], bots: dict[str, Row]) -> str:
    """What a person reads for an owner, as `hubdb.actor_name` does it, from rows fetched in a batch."""
    key = owner.split(":", 1)[1] if ":" in owner else owner
    if not owner.startswith("human:"):
        bot = bots.get(key)
        if bot:
            return str(bot.get("display_name") or bot.get("slug") or key)
    return str(humans.get(key) or key)


def _bot_note(decisions: list[Row], work_version: int) -> str:
    """The reason of the latest judge's (a bot's or the LLM judge's) call on this requirement, when it is for the current
    work; else ''."""
    for decision in decisions:
        if decision["judge"] in ("llm", "bot"):
            return str(decision.get("reason") or "") if decision["work_version"] == work_version else ""
    return ""


class _Queue:
    """What the queue reads for all its rows at once, one read per kind and chunk: the unexpired claims, the stage and
    pipeline names, the stages' Warn-after limits, and when each ticket not in a visit last stepped. Each row is built
    from these, so the reads do not grow with the rows."""

    def __init__(self, c: "sqlite3.Connection", waiting: list[tuple[Row, Row, str]], facts: _Facts, now: str) -> None:
        tasks = [task for task, _, _ in waiting]
        requirements = [requirement for _, requirement, _ in waiting]
        self.facts = facts
        self.now = now
        self.claims: dict[Key, Row] = {}
        for held in _read(
            c,
            "SELECT * FROM review_claims WHERE released_at IS NULL AND task_id IN (SELECT value FROM json_each(?)) "
            "AND stage_requirement_id IN (SELECT value FROM json_each(?))",
            [task["id"] for task in tasks],
            [requirement["id"] for requirement in requirements],
        ):
            if held["expires_at"] is None or held["expires_at"] > now:
                self.claims.setdefault((held["task_id"], held["stage_requirement_id"]), held)
        self.stages: dict[str, Row] = {
            row["id"]: row
            for row in _read(
                c,
                "SELECT id, name, limit_minutes FROM task_steps WHERE id IN (SELECT value FROM json_each(?))",
                [*(task.get("step_id") for task in tasks), *(r["pipeline_stage_id"] for r in requirements)],
            )
        }
        self.types: dict[str, Any] = {
            row["id"]: row["name"]
            for row in _read(
                c,
                "SELECT id, name FROM task_types WHERE id IN (SELECT value FROM json_each(?))",
                [requirement["pipeline_id"] for requirement in requirements],
            )
        }
        from . import (
            pipeline_manager,
        )  # the one Late rule; read here, as the manager imports the engine, not this module

        self.late_since: dict[str, str] = {
            ticket: info["since"] for ticket, info in pipeline_manager.late_tickets(c, tasks, now).items()
        }
        self.stepped: dict[str, Any] = {
            row["subject_id"]: row["at"]
            for row in _read(
                c,
                "SELECT subject_id, max(created_at) AS at FROM history_events WHERE subject_type='task' AND kind='step' "
                "AND subject_id IN (SELECT value FROM json_each(?)) GROUP BY subject_id",
                [task["id"] for task in tasks if task["id"] not in facts.visits],
            )
        }

    def since(self, task: Row) -> str:
        """When the ticket's clock started: for a late ticket, the start the Late rule counts from (holds pause it, so it
        is the latest release); otherwise when it entered its stage: the open visit's entry, else its last step, else when
        it was created."""
        if task["id"] in self.late_since:
            return str(self.late_since[task["id"]])
        visit = self.facts.visits.get(task["id"])
        if visit:
            return str(visit["entered_at"])
        return str(self.stepped.get(task["id"]) or task.get("created_at") or _now())

    def late(self, task: Row, since: str) -> bool:
        """Whether the ticket is late by the one Late rule (pipeline_manager.late_tickets): past its Warn after in its open
        visit, with time on hold not counted (D40)."""
        return task["id"] in self.late_since

    def item(self, actor: str, task: Row, requirement: Row, why: str) -> Row:
        key = (task["id"], requirement["id"])
        attachment = self.facts.attachments.get(key)
        claim_row = self.claims.get(key)
        since = self.since(task)
        stage = self.stages.get(requirement["pipeline_stage_id"]) or {}
        return {
            "task_id": task["id"],
            "title": task.get("title") or "",
            "number": task.get("number"),
            "pipeline_id": requirement["pipeline_id"],
            "pipeline": self.types.get(requirement["pipeline_id"]) or "",
            "stage_id": requirement["pipeline_stage_id"],
            "stage": stage.get("name") or "",
            "requirement_id": requirement["id"],
            "requirement": requirement["name"],
            "requirement_version": requirement["version"],
            "kind": requirement["kind"],
            "work_version": _revision(task),
            "attachment": (
                {
                    "id": attachment["id"],
                    "source": attachment["source"],
                    "url": attachment["url"],
                    "blob_id": attachment["blob_id"],
                    "file_id": attachment["file_id"],
                    "file_version": attachment["file_version"],
                    "commit_sha": attachment["commit_sha"],
                }
                if attachment
                else None
            ),
            "attachment_digest": attachment["digest"] if attachment else None,
            "why": why,
            "since": since,
            "late": self.late(task, since),
            "claimed_by": claim_row["claimed_by"] if claim_row and claim_row["claimed_by"] != actor else None,
            "claimed_by_me": bool(claim_row and claim_row["claimed_by"] == actor),
            "stage_visit_id": (self.facts.visits.get(task["id"]) or {}).get("id"),
        }


def _queue_fields(
    c: "sqlite3.Connection", actor: str, items: list[Row], live: list[Row], tasks: dict[str, Row], queue: _Queue
) -> None:
    """Adds assignee, bot_note, reviewer_id, can_mark_met, can_not_validate and moves_to to each queue item. The people
    and bots, the move links and the pipelines' steps are each read once over all the items; `may_edit_rules` runs once
    per pipeline; Not validated's route is read per ticket at its stage (it follows the ticket's own handoff)."""
    if not items:
        return
    owners = {item["task_id"]: tasks[item["task_id"]].get("owner_id") for item in items}
    bare = [o.split(":", 1)[1] if ":" in o else o for o in owners.values() if o]
    humans = {
        r["id"]: r["name"]
        for r in _read(c, "SELECT id, name FROM humans WHERE id IN (SELECT value FROM json_each(?))", bare)
    }
    bots = {
        r["id"]: r
        for r in _read(c, "SELECT id, display_name, slug FROM bots WHERE id IN (SELECT value FROM json_each(?))", bare)
    }
    raw_links = {
        r["from_pipeline_stage_id"]: r["to_pipeline_id"]
        for r in _read(
            c,
            "SELECT from_pipeline_stage_id, to_pipeline_id FROM pipeline_links WHERE mode='move' AND retired_at IS NULL "
            "AND from_pipeline_stage_id IN (SELECT value FROM json_each(?))",
            [item["stage_id"] for item in items],
        )
    }
    # A move link into a pipeline the reader may not see names nothing: its stage is left out of the destinations (D67).
    targets = pipeline_rights.visible_pipeline_ids(c, actor, sorted(set(raw_links.values())))
    blocked = {stage for stage, target in raw_links.items() if target not in targets}
    links = {stage: target for stage, target in raw_links.items() if stage not in blocked}
    pipeline_ids = {item["pipeline_id"] for item in items}
    steps = _read(
        c,
        "SELECT id, type_id, name, position, status FROM task_steps WHERE type_id IN (SELECT value FROM json_each(?))",
        [*pipeline_ids, *links.values()],
    )
    steps.sort(key=lambda step: (step["position"], step["id"]))
    names = {s["id"]: s["name"] for s in steps}
    requirements = {r["id"]: r for r in live}
    rights = {p: pipeline_rights.may_edit_rules(c, actor, p) for p in pipeline_ids}
    candidates: dict[
        tuple[str, str], tuple[Row, list[Row], str | None] | None
    ] = {}  # Not validated's candidates per stage, read once
    at_stage_tasks = [item["task_id"] for item in items if tasks[item["task_id"]].get("step_id") == item["stage_id"]]
    delivered = _delivered_links(c, at_stage_tasks)

    def _route_ok(item: Row) -> bool:
        key = (str(item["pipeline_id"]), str(item["stage_id"]))
        if key not in candidates:
            candidates[key] = _route_candidates(c, actor, *key)
        found = candidates[key]
        return found is not None and _choose_link(found[1], found[2], delivered.get(item["task_id"], [])) is not None

    for item in items:
        key = (item["task_id"], item["requirement_id"])
        owner = owners.get(item["task_id"])
        item["assignee"] = {"id": owner, "name": _named(owner, humans, bots)} if owner else None
        item["bot_note"] = _bot_note(queue.facts.decisions.get(key, []), item["work_version"])
        claim_row = queue.claims.get(key)
        item["reviewer_id"] = (claim_row["claimed_by"] if claim_row else None) or requirements[
            item["requirement_id"]
        ].get("reviewer_id")
        # An approval is never overridden (mark_met refuses protected_approval), so the action is not offered for one.
        item["can_mark_met"] = bool(rights[item["pipeline_id"]]) and item["kind"] != "approval"
        at_stage = tasks[item["task_id"]].get("step_id") == item["stage_id"]
        # Another reviewer's claim holds the item: Not validated is theirs to send, so it is not offered to anyone else.
        # Another reviewer's claim holds the item: Not validated is theirs to send, so it is not offered to anyone else.
        item["can_not_validate"] = at_stage and item["claimed_by"] is None and _route_ok(item)
        item["moves_to"] = _moves_to(item, live, queue.facts, links, steps, names, blocked) if at_stage else None


def _attachment_views(c: "sqlite3.Connection", items: list[Row], facts: "_Facts") -> None:
    """Adds each shown attachment's format, name and href (its immutable content address), one media lookup for all."""
    shown = [item for item in items if item["attachment"]]
    rows = [facts.attachments[(item["task_id"], item["requirement_id"])] for item in shown]
    for item, view in zip(shown, requirement_attachments.views(c, rows), strict=True):
        item["attachment"].update(format=view["format"], name=view["name"], href=view["href"])


def _moves_to(
    item: Row,
    live: list[Row],
    facts: _Facts,
    links: dict[str, str],
    steps: list[Row],
    names: dict[str, str],
    blocked: set[str] | None = None,
) -> Row | None:
    """Where the ticket goes if this item is the stage's last unmet requirement, the same destination approve uses;
    None while another requirement of the stage is unmet, at the last stage, or when the stage's move link leads to a
    pipeline the reader may not see (`blocked`)."""
    if blocked and item["stage_id"] in blocked:
        return None
    labels = facts.labels.get(item["task_id"], set())
    for other in live:
        if other["pipeline_stage_id"] != item["stage_id"] or other["id"] == item["requirement_id"]:
            continue
        if other.get("enforced_by"):
            continue  # a mirror of the review gate, which still decides it (as in `_stage_met`)
        if not pipeline_engine.applies(other, labels):
            continue
        if facts.state({"id": item["task_id"]}, other) != "met":
            return None
    destination = _destination_in(item["stage_id"], item["pipeline_id"], links.get(item["stage_id"]), steps)
    if destination is None:
        return None
    return {"stage_id": destination[1], "name": names.get(destination[1], "")}


def _authorized(
    c: "sqlite3.Connection",
    actor: str,
    task_ids: "Iterable[str]",
    pipeline_ids: "Iterable[str]",
    visible_sql: str | None,
) -> tuple[set[str], set[str]]:
    """The tickets the reader may open now and the pipelines they may see, of those given. A ticket is readable when its
    current pipeline is visible to them or they are its owner or requester (D67: a ticket's own parties keep it wherever
    it is now), it passes the task rules (`visible_sql`, the route's list clause, when given) and
    `hubdb.task_private_readable` admits them. Whether the requirement's own pipeline is visible is checked by the caller.
    Reads only the ticket rows, never their tags, decisions or attachments."""
    found: dict[str, Row] = {}
    ids = sorted({str(t) for t in task_ids if t})
    for chunk in _chunks(ids):
        sql = "SELECT * FROM tasks WHERE id IN (SELECT value FROM json_each(?))"
        if visible_sql:
            sql += f" AND id IN (SELECT id FROM tasks WHERE {visible_sql})"
        found.update({str(row["id"]): row for row in _rows(c, sql, (chunk,))})
    types = {str(row["type_id"]) for row in found.values() if row.get("type_id")}
    visible = pipeline_rights.visible_pipeline_ids(c, actor, sorted(types | {str(p) for p in pipeline_ids if p}))
    # The copied-page provenance is read once; a public ticket with no copy is open to everyone, so only the private or
    # copied tickets need the per-ticket rule.
    from . import counsel

    copies = cast(dict[str, Any], cast(Any, counsel.page_copies)(c))
    allowed: set[str] = set()
    for tid, row in found.items():
        party = actor in (row.get("owner_id"), row.get("requester_id"))
        if row.get("type_id") and str(row["type_id"]) not in visible and not party:
            continue
        open_to_all = tid not in copies and not _hub().task_private(c, row)
        if open_to_all or _hub().task_private_readable(c, actor, row):
            allowed.add(tid)
    return allowed, visible


def queue_for(
    c: "sqlite3.Connection",
    actor: str,
    pipeline_id: str | None = None,
    stage_id: str | None = None,
    visible_sql: str | None = None,
) -> list[Row]:
    """The items waiting for this reviewer, Late first, then oldest. `pipeline_id` and `stage_id` narrow it to one
    pipeline or one stage. Only a reviewer, a send-back's route or an asked backup sees an item; a bot sees none. An item
    is a requirement of the ticket's current stage, or a stale one of a stage it has left (D33), reviewed in place. The
    tickets the reader may not open, and the items on pipelines they may not see, are left out (`visible_sql` is the
    route's task list clause, which also applies the bot activity controls)."""
    if not str(actor or "").startswith("human:"):
        return []
    people = ",".join("?" * len(PERSON_REVIEWED))
    open_status = ",".join("?" * len(ACTIVE))
    where = (
        f"r.retired_at IS NULL AND r.reviewed_by IN ({people}) AND NOT (r.kind IN ('field', 'run') AND r.reviewed_by<>'person') "
        f"AND t.status IN ({open_status}) "
        "AND (r.reviewer_id=? OR r.backup_reviewer_id=? OR EXISTS (SELECT 1 FROM stage_decisions d "
        "WHERE d.task_id=t.id AND d.stage_requirement_id=r.id AND d.route_next_to=?))"
    )
    args: list[Any] = [*PERSON_REVIEWED, *ACTIVE, actor, actor, actor]
    if pipeline_id:
        where += " AND r.pipeline_id=?"
        args.append(pipeline_id)
    if stage_id:
        where += " AND r.pipeline_stage_id=?"
        args.append(stage_id)
    sql = (
        "SELECT t.id AS task_id, r.id AS requirement_id, r.pipeline_stage_id AS stage_id FROM stage_requirements r "
        f"JOIN tasks t ON t.step_id=r.pipeline_stage_id WHERE {where} "
        "UNION SELECT t.id AS task_id, r.id AS requirement_id, r.pipeline_stage_id AS stage_id FROM stage_requirements r "
        "JOIN stage_visits v ON v.pipeline_stage_id=r.pipeline_stage_id AND v.left_at IS NOT NULL "
        "AND r.created_at<=v.left_at JOIN tasks t ON t.id=v.task_id "
        f"WHERE {where}"
    )
    now = _now()
    pairs = _rows(c, sql, tuple([*args, *args]))
    live = _read(
        c,
        "SELECT * FROM stage_requirements WHERE pipeline_stage_id IN (SELECT value FROM json_each(?)) "
        "AND retired_at IS NULL",
        [pair["stage_id"] for pair in pairs],
    )
    requirements = {r["id"]: r for r in live}
    # Who may open what is decided before any ticket is hydrated or any attachment, decision or visit is read: a ticket the
    # reader may not open, and an item on a pipeline they may not see, are dropped here (D67).
    allowed, visible = _authorized(
        c,
        actor,
        [pair["task_id"] for pair in pairs],
        [requirement["pipeline_id"] for requirement in requirements.values()],
        visible_sql,
    )
    pairs = [
        pair
        for pair in pairs
        if pair["task_id"] in allowed and requirements.get(pair["requirement_id"], {}).get("pipeline_id") in visible
    ]
    tasks = _tasks(c, [pair["task_id"] for pair in pairs])
    facts = _Facts.load(c, tasks, live)
    waiting: list[tuple[Row, Row, str]] = []
    for pair in pairs:
        task = tasks.get(pair["task_id"])
        requirement = requirements.get(pair["requirement_id"])
        if (
            task is None
            or requirement is None
            or not facts.applies(task, requirement)
            or not facts.counts(task, requirement)
        ):
            continue
        why = facts.why_sees(actor, task["id"], requirement)
        if why and facts.waits_on_person(task, requirement):
            waiting.append((task, requirement, why))
    queue = _Queue(c, waiting, facts, now)
    items = [queue.item(actor, task, requirement, why) for task, requirement, why in waiting]
    _queue_fields(c, actor, items, live, tasks, queue)
    _attachment_views(c, items, facts)
    items.sort(key=lambda item: (not item["late"], item["since"], item["task_id"], item["requirement_id"]))
    return items


# ----------------------------------------------------------------------------- claims
def _guard_item(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str) -> tuple[Row, Row]:
    """The ticket and the requirement, or the refusal: a person, at the stage, whom the item waits on."""
    _person(actor)
    task = _task(c, task_id)
    requirement = _requirement(c, requirement_id)
    if requirement.get("retired_at") or _review_of(requirement) not in PERSON_REVIEWED:
        raise Declined("not_person_reviewed", "A person does not review this requirement", 409)
    if task.get("status") not in ACTIVE or not pipeline_engine.counts_on_card(c, task_id, requirement):
        raise Declined("moved", "The ticket is no longer at this stage", 409)
    if _why_sees(c, actor, task_id, requirement) is None:
        raise Declined("not_yours", "This item is not waiting on you", 403)
    return task, requirement


def _claimed(c: "sqlite3.Connection", held: Row) -> Declined:
    name = str(_hub().actor_name(c, held["claimed_by"]) or held["claimed_by"])
    return Declined(
        "claimed",
        f"{name} is reviewing",
        409,
        extra={"claimed_by": held["claimed_by"], "expires_at": held["expires_at"]},
    )


def _claim_in(c: "sqlite3.Connection", actor: str, task: Row, requirement: Row) -> Row:
    """Takes or refreshes the claim inside a write. Another person's unexpired claim refuses."""
    now = _now()
    c.execute(
        "UPDATE review_claims SET released_at=? WHERE task_id=? AND stage_requirement_id=? AND released_at IS NULL "
        "AND expires_at IS NOT NULL AND expires_at<=?",
        (now, task["id"], requirement["id"], now),
    )
    held = _open_claim(c, task["id"], requirement["id"], now)
    if held and held["claimed_by"] != actor:
        raise _claimed(c, held)
    attachment = _current_attachment(c, task["id"], requirement["id"])
    digest = attachment["digest"] if attachment else None
    expires = _shift(now, minutes=CLAIM_MINUTES)
    if held:
        c.execute(
            "UPDATE review_claims SET expires_at=?, work_version=?, attachment_digest=? WHERE id=?",
            (expires, _revision(task), digest, held["id"]),
        )
        return {**held, "expires_at": expires, "work_version": _revision(task), "attachment_digest": digest}
    made: Row = {
        "id": _new_id("rc"),
        "task_id": task["id"],
        "stage_requirement_id": requirement["id"],
        "claimed_by": actor,
        "work_version": _revision(task),
        "attachment_digest": digest,
        "claimed_at": now,
        "expires_at": expires,
    }
    c.execute(
        "INSERT INTO review_claims (id, task_id, stage_requirement_id, claimed_by, work_version, attachment_digest, "
        "claimed_at, expires_at) VALUES (:id,:task_id,:stage_requirement_id,:claimed_by,:work_version,"
        ":attachment_digest,:claimed_at,:expires_at)",
        made,
    )
    return made


def _release_in(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str) -> None:
    c.execute(
        "UPDATE review_claims SET released_at=? WHERE task_id=? AND stage_requirement_id=? AND claimed_by=? "
        "AND released_at IS NULL",
        (_now(), task_id, requirement_id, actor),
    )


def claim(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str) -> Row:
    """Starts reviewing an item for ten minutes. A second reviewer is refused with who holds it."""
    with _atomic(c):
        task, requirement = _guard_item(c, actor, task_id, requirement_id)
        if not _waits_on_person(c, task, requirement):
            raise Declined("nothing_to_review", "Nothing is waiting on a person here", 409)
        held = _claim_in(c, actor, task, requirement)
    return {
        "task_id": task_id,
        "requirement_id": requirement_id,
        "claimed_by": actor,
        "expires_at": held["expires_at"],
        "work_version": held["work_version"],
        "attachment_digest": held["attachment_digest"],
    }


def release(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str) -> Row:
    """Lets go of one's own claim."""
    _person(actor)
    with _atomic(c):
        _release_in(c, actor, task_id, requirement_id)
    return {"task_id": task_id, "requirement_id": requirement_id, "released": True}


def _check_claim(c: "sqlite3.Connection", actor: str, task: Row, requirement: Row) -> None:
    held = _open_claim(c, task["id"], requirement["id"])
    if held and held["claimed_by"] != actor:
        raise _claimed(c, held)


# ----------------------------------------------------------------------------- decisions
def _decide(
    c: "sqlite3.Connection",
    actor: str,
    task: Row,
    requirement: Row,
    verdict: str,
    reason: str | None,
    attachment: Row | None,
    route_next_to: str | None = None,
) -> Row:
    visit = _open_visit(c, task["id"])
    decision: Row = {
        "id": _new_id("sd"),
        "task_id": task["id"],
        "pipeline_stage_id": requirement["pipeline_stage_id"],
        "stage_visit_id": visit["id"] if visit else None,
        "stage_requirement_id": requirement["id"],
        "requirement_version": requirement["version"],
        "requirement_attachment_id": attachment["id"] if attachment else None,
        "attachment_digest": attachment["digest"] if attachment else None,
        "work_version": _revision(task),
        "judge": "person",
        "judge_actor": actor,
        "verdict": verdict,
        "reason": reason,
        "route_next_to": route_next_to,
        "created_at": _now(),
    }
    c.execute(
        "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_visit_id, stage_requirement_id, "
        "requirement_version, requirement_attachment_id, attachment_digest, work_version, judge, judge_actor, verdict, "
        "reason, route_next_to, created_at) VALUES (:id,:task_id,:pipeline_stage_id,:stage_visit_id,"
        ":stage_requirement_id,:requirement_version,:requirement_attachment_id,:attachment_digest,:work_version,"
        ":judge,:judge_actor,:verdict,:reason,:route_next_to,:created_at)",
        decision,
    )
    return decision


def _destination_in(stage_id: str, pipeline_id: str, link: str | None, steps: list[Row]) -> tuple[str, str] | None:
    """(type id, step id) after stage `stage_id` of `pipeline_id`, from the steps of the pipeline and of a move link's
    target (ordered by position, id). Through the link to the target's first live stage, else the next live stage of
    the pipeline. None at the last stage. Reads nothing, so the queue can run it over many rows."""

    def live(step: Row) -> bool:
        return step["status"] not in ("closed", "declined")

    if link:
        first = next((s for s in steps if s["type_id"] == link and live(s)), None)
        return (link, first["id"]) if first else None
    here = next((s for s in steps if s["id"] == stage_id), None)
    if here is None:
        return None
    after = next(
        (s for s in steps if s["type_id"] == pipeline_id and s["position"] > here["position"] and live(s)), None
    )
    return (pipeline_id, after["id"]) if after else None


def _next_destination(c: "sqlite3.Connection", requirement: Row) -> tuple[str, str] | None:
    """(type id, step id) the ticket goes to after this stage: through the stage's `move` link to the first stage of the
    target pipeline, else the next position whose status is not closed or declined. None at the last stage."""
    link = _one(
        c,
        "SELECT to_pipeline_id FROM pipeline_links WHERE from_pipeline_stage_id=? AND mode='move' "
        "AND retired_at IS NULL",
        (requirement["pipeline_stage_id"],),
    )
    target = link["to_pipeline_id"] if link else None
    types = _ids([requirement["pipeline_id"], *([target] if target else [])])
    steps = _rows(
        c,
        "SELECT id, type_id, name, position, status FROM task_steps WHERE type_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY position, id",
        (types,),
    )
    return _destination_in(requirement["pipeline_stage_id"], requirement["pipeline_id"], target, steps)


def _stage_met(c: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """Whether every live requirement of the stage that applies to this ticket is met, judged from one read of the stage's
    facts."""
    others = [
        other
        for other in _rows(
            c,
            "SELECT * FROM stage_requirements WHERE pipeline_stage_id=? AND retired_at IS NULL",
            (requirement["pipeline_stage_id"],),
        )
        if not other.get("enforced_by")  # a mirror of the review gate, which still decides it
    ]
    facts = _Facts.load(c, [task["id"]], others)
    applying = [other for other in others if facts.applies(task, other)]
    return all(facts.state(task, other) == "met" for other in applying)


def _advance(c: "sqlite3.Connection", actor: str, task: Row, requirement: Row) -> Row | None:
    """Moves the ticket on when its stage is fully met. Returns what moved ({from_type, from_step, to_type, to_step}),
    or None when it stays (not the last requirement, no next stage, or the engine refuses the move)."""
    if task.get("step_id") != requirement["pipeline_stage_id"]:
        return None  # a stale requirement of a stage the ticket has left is approved in place, and moves nothing
    if not _stage_met(c, task, requirement):
        return None
    destination = _next_destination(c, requirement)
    if destination is None:
        return None
    to_type, to_step = destination
    moved: Row = {
        "from_type": task.get("type_id"),
        "from_step": task.get("step_id"),
        "to_type": to_type,
        "to_step": to_step,
    }
    handoff: str | None = None
    c.execute("SAVEPOINT pipeline_advance")
    try:
        _hub().task_update(
            c,
            actor,
            task["id"],
            type=to_type if to_type != task.get("type_id") else None,
            step=to_step,
            mover=True,
            note="Approved",
        )
        link = pipeline_links.link_for_stage(c, requirement["pipeline_stage_id"])
        if link is not None and link["mode"] == "move" and link["to_pipeline_id"] == to_type:
            # The same record the engine writes for a move through a link, so the flow map counts this handoff. Undo
            # keeps its id to mark it returned.
            handoff = pipeline_links.record_handoff(c, str(link["id"]), task["id"], task["id"])
    except _hub().Refused:
        c.execute("ROLLBACK TO pipeline_advance")
        c.execute("RELEASE pipeline_advance")
        return None
    c.execute("RELEASE pipeline_advance")
    moved["handoff"] = handoff
    return moved


def approve(
    c: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement_id: str,
    *,
    work_version: int | None = None,
    attachment_digest: Any = CURRENT,
    requirement_version: int,
    note: str | None = None,
) -> Row:
    """A person's Approve of what they saw. `work_version`, `attachment_digest` and `requirement_version` come from the queue
    item, and the requirement version is required (an approval of an older rule is refused, D33). The approval is refused
    when the work, the attachment or the requirement changed since, when someone else holds the claim, when the actor is not
    the person the item waits on, or when the rule's prerequisites do not hold now (freshness, format, a field set, a run
    that passed). When it meets the last requirement of the stage the ticket moves on. Returns the decision, whether it
    advanced and until when it can be undone."""
    with _atomic(c):
        task, requirement = _guard_item(c, actor, task_id, requirement_id)
        _check_claim(c, actor, task, requirement)
        current = _current_attachment(c, task_id, requirement_id)
        if work_version is not None and _revision(task) != work_version:
            raise Declined("stale", "The work changed since you looked", 409, extra={"reason": "work"})
        if attachment_digest is not CURRENT and (current["digest"] if current else None) != attachment_digest:
            raise Declined("stale", "The attachment changed since you looked", 409, extra={"reason": "attachment"})
        if requirement_version != requirement["version"]:
            raise Declined("stale", "The requirement changed since you looked", 409, extra={"reason": "requirement"})
        if requirement_state(c, task, requirement) == "met":
            raise Declined("already_met", "This requirement is already met", 409)
        problem = pipeline_engine.approval_problem(c, task_id, requirement, current)
        if problem is not None:
            code, message = problem
            raise Declined(code, message, 409, extra={"reason": "freshness" if code == "stale" else code})
        decision = _decide(c, actor, task, requirement, "met", _clip(note), current)
        moved = _advance(c, actor, task, requirement)
        if moved:
            c.execute(
                "UPDATE stage_decisions SET outcome=?, outcome_at=? WHERE id=?",
                (json.dumps({"advanced": moved}), _now(), decision["id"]),
            )
        _release_in(c, actor, task_id, requirement_id)
        pipeline_github_checks.publish_affected(c, task_id)
    return {
        "decision_id": decision["id"],
        "verdict": "met",
        "advanced": moved is not None,
        "undo_until": _shift(decision["created_at"], seconds=UNDO_SECONDS),
    }


def _spawned_by(c: "sqlite3.Connection", task_id: str, since: str) -> list[Row]:
    """The live spawn handoffs a ticket made since `since` (the approval that moved it), each with its subtask."""
    return _rows(
        c,
        "SELECT h.id AS handoff, h.target_task_id AS child, h.created_at AS filed, t.type_id AS type_id, "
        "t.status AS status, t.step_id AS step_id FROM pipeline_handoffs h "
        "JOIN pipeline_links l ON l.id = h.pipeline_link_id AND l.mode = 'spawn' "
        "JOIN tasks t ON t.id = h.target_task_id "
        "WHERE h.source_task_id = ? AND h.returned_at IS NULL AND h.created_at >= ? ORDER BY h.created_at, h.id",
        (task_id, since),
    )


def _picked_up(c: "sqlite3.Connection", child: Row) -> bool:
    """Whether a spawned subtask has been picked up: it is no longer open in its pipeline's first stage, or a step or
    status change came after it was filed."""
    first = pipeline_engine.first_stage(c, str(child["type_id"]))
    if child["status"] != "open" or first is None or child["step_id"] != first["id"]:
        return True
    return (
        _one(
            c,
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind IN ('step','status') "
            "AND created_at>? LIMIT 1",
            (child["child"], child["filed"]),
        )
        is not None
    )


def undo(c: "sqlite3.Connection", actor: str, decision_id: str) -> Row:
    """Takes back one's own approval within UNDO_SECONDS, and the move it caused. Refused after that, once the ticket is
    no longer where the approval left it, and for a Not validated decision (it filed work for someone: the person closes
    that follow-up instead). When the approval's exit spawned subtasks that nobody has picked up yet, they close with
    "Approval undone" and the handoff is returned; if one was picked up, nothing changes."""
    _person(actor)
    with _atomic(c):
        decision = _one(c, "SELECT * FROM stage_decisions WHERE id=?", (decision_id,))
        if decision is not None and decision["verdict"] == "failed" and decision["judge_actor"] == actor:
            raise Declined("not_undoable", "A Not validated decision is not undone; close its follow-up instead", 409)
        if decision is None or decision["verdict"] != "met" or decision["judge_actor"] != actor:
            raise Declined("not_found", "No approval of yours to undo", 404)
        if decision["undone_at"]:
            raise Declined("already_undone", "Already undone", 409)
        now = _now()
        if now > _shift(decision["created_at"], seconds=UNDO_SECONDS):
            raise Declined("too_late", "Too late to undo", 409)
        try:
            outcome: Row = json.loads(decision["outcome"]) if decision["outcome"] else {}
        except ValueError:
            outcome = {}
        moved = cast("Row | None", outcome.get("advanced"))
        task = _task(c, decision["task_id"])
        if moved:
            # The approval's own move is the one step event since the decision; another one means it went on.
            steps = _rows(
                c,
                "SELECT id FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='step' "
                "AND created_at>=?",
                (decision["task_id"], decision["created_at"]),
            )
            if task.get("step_id") != moved["to_step"] or len(steps) > 1:
                raise Declined("moved_on", "The ticket has moved on", 409)
            spawned = _spawned_by(c, decision["task_id"], decision["created_at"])
            if any(_picked_up(c, child) for child in spawned):
                raise Declined("already_started", "A follow-up this approval filed was already picked up", 409)
            for child in spawned:
                _hub().task_close(c, _hub().KEEPER, child["child"], note="Approval undone")
                c.execute("UPDATE pipeline_handoffs SET returned_at=? WHERE id=?", (now, child["handoff"]))
            _hub().task_update(
                c,
                _hub().KEEPER,
                decision["task_id"],
                type=moved["from_type"] if moved["from_type"] != moved["to_type"] else None,
                step=moved["from_step"],
                note=f"Approval undone by {_hub().actor_name(c, actor)}",
            )
            if moved.get("handoff"):
                # An undone approval's handoff stops counting in the flow map (pipeline_overview reads returned_at).
                c.execute(
                    "UPDATE pipeline_handoffs SET returned_at=? WHERE id=? AND returned_at IS NULL",
                    (now, moved["handoff"]),
                )
        else:
            visit = _open_visit(c, decision["task_id"])
            if decision["stage_visit_id"] and (visit is None or visit["id"] != decision["stage_visit_id"]):
                raise Declined("moved_on", "The ticket has moved on", 409)
        c.execute("UPDATE stage_decisions SET undone_at=?, undone_by=? WHERE id=?", (now, actor, decision_id))
        pipeline_github_checks.publish_affected(c, decision["task_id"])
    return {"decision_id": decision_id, "undone": True, "moved_back": moved is not None}


def send_back(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str, note: str) -> Row:
    """Sends the work back to its assignee with a note (D36). The item leaves the queue; the next attachment returns to
    this reviewer with no bot pass (`route_next_to`)."""
    text = _clip(note)
    if text is None:
        raise Declined("note_required", "Say what to change", 422)
    with _atomic(c):
        task, requirement = _guard_item(c, actor, task_id, requirement_id)
        _check_claim(c, actor, task, requirement)
        if requirement_state(c, task, requirement) == "met":
            raise Declined("already_met", "This requirement is already met", 409)
        decision = _decide(
            c, actor, task, requirement, "sent_back", text, _current_attachment(c, task_id, requirement_id), actor
        )
        _release_in(c, actor, task_id, requirement_id)
        _hub().task_comment(c, actor, task_id, f"Sent back at {requirement['name']}: {text}")
    return {"decision_id": decision["id"], "verdict": "sent_back"}


def _back_stage(c: "sqlite3.Connection", stage_id: str) -> Row | None:
    """The live stage just before this one in its pipeline (Validate goes back to In development)."""
    here = _one(c, "SELECT type_id, position FROM task_steps WHERE id=?", (stage_id,))
    if here is None:
        return None
    return _one(
        c,
        "SELECT id, name FROM task_steps WHERE type_id=? AND position<? AND status NOT IN ('closed','declined') "
        "ORDER BY position DESC, id LIMIT 1",
        (here["type_id"], here["position"]),
    )


def _route_candidates(
    c: "sqlite3.Connection", actor: str, pipeline_id: str, stage_id: str
) -> tuple[Row, list[Row], str | None] | None:
    """The back stage, the spawn links that could file the follow-up for a card at this stage (nearest first), and the
    pipeline the back stage's `subtasks` run names (None for a requirement that names none). The links are the ones from a
    stage at or before the back stage; with a named pipeline, only the links into it. None when Not validated is not
    offered here at all: the reader is not a person, there is no stage before, or the stage before does not wait on a
    `subtasks` run. The links list may be empty: the caller refuses. The queue flag and the action both read this."""
    if not str(actor or "").startswith("human:"):
        return None
    back = _back_stage(c, stage_id)
    if back is None:
        return None
    run = _one(
        c,
        "SELECT needs FROM stage_requirements WHERE pipeline_stage_id=? AND kind='run' AND retired_at IS NULL "
        "AND json_extract(needs, '$.source')='subtasks' ORDER BY position, id LIMIT 1",
        (back["id"],),
    )
    if run is None:
        return None
    named = str(json.loads(run["needs"] or "{}").get("pipeline") or "") or None
    links = _rows(
        c,
        "SELECT l.* FROM pipeline_links l JOIN task_steps s ON s.id = l.from_pipeline_stage_id "
        "WHERE s.type_id = ? AND s.position <= (SELECT position FROM task_steps WHERE id = ?) "
        "AND s.status NOT IN ('closed','declined') AND l.mode = 'spawn' AND l.retired_at IS NULL "
        "AND (? IS NULL OR l.to_pipeline_id = ?) ORDER BY s.position DESC, l.id",
        (pipeline_id, back["id"], named, named),
    )
    return back, links, named


def _delivered_links(c: "sqlite3.Connection", task_ids: "Iterable[str]") -> dict[str, list[str]]:
    """{ticket id: the spawn link ids whose live handoffs filed a subtask that has shipped, in handoff order}, from one read
    per chunk of tickets. A handoff counts only when its subtask is in the link's target pipeline, and "shipped" is the
    engine's own reading (pipeline_engine.shipped), so a cancelled subtask never delivers the work."""
    ids = list(task_ids)
    filed = _read(
        c,
        "SELECT h.source_task_id AS task_id, h.pipeline_link_id AS link_id, h.target_task_id AS child "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id = h.pipeline_link_id AND l.mode = 'spawn' "
        "AND l.retired_at IS NULL JOIN tasks t ON t.id = h.target_task_id AND t.type_id = l.to_pipeline_id "
        "WHERE h.returned_at IS NULL AND h.source_task_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY h.created_at, h.id",
        ids,
    )
    if not filed:
        return {}
    shipped = pipeline_engine.shipped(c, [str(row["child"]) for row in filed])
    out: dict[str, list[str]] = {}
    for row in filed:
        if str(row["child"]) not in shipped:
            continue
        links = out.setdefault(str(row["task_id"]), [])
        if str(row["link_id"]) not in links:
            links.append(str(row["link_id"]))
    return out


def _pick_link(links: list[Row], delivered: list[str]) -> Row | None:
    """The spawn link the follow-up goes through: the one candidate link whose shipped subtask delivered this ticket's work
    (D64). None unless exactly one candidate did: Not validated is not guessed from chronology or the nearest stage."""
    wanted = set(delivered)
    matched = [link for link in links if str(link["id"]) in wanted]
    return matched[0] if len(matched) == 1 else None


def _choose_link(links: list[Row], named: str | None, delivered: list[str]) -> Row | None:
    """The spawn link Not validated files through (D64). When the waited-on requirement names its pipeline, the links are
    already the ones into that pipeline: the one such link is the route, and several fall back to _pick_link. Without a name,
    it is _pick_link: the one candidate whose shipped subtask delivered the work, or none."""
    if named is not None and len(links) == 1:
        return links[0]
    return _pick_link(links, delivered)


def _not_validated_route(
    c: "sqlite3.Connection", actor: str, pipeline_id: str, stage_id: str, task_id: str
) -> tuple[Row, Row] | None:
    """Where "Not validated" sends a card from this stage (D66), or None when it is not offered: the back stage and the
    spawn link the follow-up files through (see _choose_link). It is offered only on a stage that comes directly after a
    live In development stage that waits on its `subtasks`. The reader must be a person. When that requirement names a
    pipeline and no spawn link files into it from an earlier stage, or no name and the links are ambiguous or none delivered
    shipped work, it refuses (`development_link`) instead of guessing."""
    found = _route_candidates(c, actor, pipeline_id, stage_id)
    if found is None:
        return None
    back, links, named = found
    delivered = _delivered_links(c, [task_id]).get(task_id, [])
    link = _choose_link(links, named, delivered)
    if link is None:
        matched = [str(row["id"]) for row in links if str(row["id"]) in delivered]
        if not links:
            raise Declined(
                "development_link",
                "No spawn link files into the pipeline this stage waits on, so Not validated has no link to file through",
                409,
            )
        if not matched:
            raise Declined(
                "development_link",
                "No shipped subtask came through a spawn link, so Not validated has no link to file through",
                409,
            )
        raise Declined(
            "development_link",
            "Shipped subtasks came through more than one spawn link, so Not validated cannot tell which one to file through",
            409,
        )
    return back, link


def not_validated(
    c: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement_id: str,
    reason: str,
    work_version: int | None = None,
    requirement_version: int | None = None,
    attachment_id: Any = CURRENT,
    attachment_digest: Any = CURRENT,
    stage_visit_id: Any = CURRENT,
) -> Row:
    """Validate's "Not validated" (D64), a person's call on the validation requirement. The spec's measures did not hold:
    the card goes back to the stage before Validate (In development, which then waits on the new subtask), and one
    follow-up Engineering subtask is filed under it, with the reason and the source's links. The follow-up is a second
    spawn on purpose, so it is its own handoff. Person only, at Validate, on an item the person is waiting on.

    `attachment_id`, `attachment_digest` and `stage_visit_id` are the attachment and the stage visit the reviewer saw; when
    given, they must still be the current ones, checked inside the write (stale otherwise, reason attachment or visit)."""
    text = _clip(reason)
    if text is None:
        raise Declined("reason_required", "Say what did not validate", 422)
    with _atomic(c):
        task, requirement = _guard_item(c, actor, task_id, requirement_id)
        _check_claim(c, actor, task, requirement)
        if task.get("step_id") != requirement["pipeline_stage_id"]:
            raise Declined("moved", "The ticket is no longer at this stage", 409)
        if work_version is not None and _revision(task) != work_version:
            raise Declined("stale", "The work changed since you looked", 409, extra={"reason": "work"})
        if requirement_version is not None and requirement_version != requirement["version"]:
            raise Declined("stale", "The requirement changed since you looked", 409, extra={"reason": "requirement"})
        current = _current_attachment(c, task_id, requirement_id)
        if attachment_id is not CURRENT or attachment_digest is not CURRENT:
            same = (current["id"] if current else None) == attachment_id and (
                current["digest"] if current else None
            ) == attachment_digest
            if not same:
                raise Declined("stale", "The attachment changed since you looked", 409, extra={"reason": "attachment"})
        visit = _open_visit(c, task_id)
        if stage_visit_id is not CURRENT and (visit["id"] if visit else None) != stage_visit_id:
            raise Declined("stale", "The stage changed since you looked", 409, extra={"reason": "visit"})
        if requirement_state(c, task, requirement) == "met":
            raise Declined("already_met", "This requirement is already met", 409)
        route = _not_validated_route(c, actor, str(task["type_id"]), str(requirement["pipeline_stage_id"]), task_id)
        if route is None:
            raise Declined("not_validatable", "Not validated is not offered at this stage", 409)
        back, spawn_link = route
        decision = _decide(c, actor, task, requirement, "failed", text, current)
        with pipeline_engine.decided_move(actor):  # the reviewer decided it; the move is recorded as theirs
            _hub().task_update(c, actor, task_id, step=str(back["id"]), mover=True, note=f"Not validated: {text}")
        child = pipeline_links.spawn(c, spawn_link, task_id, again=True, note=text)
        if child is None:
            raise Declined("not_filed", "The follow-up ticket could not be filed", 409)
        c.execute(
            "UPDATE stage_decisions SET outcome=?, outcome_at=? WHERE id=?",
            (json.dumps({"moved_back": str(back["id"]), "follow_up": child}), _now(), decision["id"]),
        )
        _release_in(c, actor, task_id, requirement_id)
        pipeline_github_checks.publish_affected(c, task_id)
    return {
        "decision_id": decision["id"],
        "verdict": "failed",
        "moved_back_to": back["name"],
        "follow_up": child,
    }


def message_manager(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str, text: str) -> Row:
    """Writes to the pipeline's manager bot about this item, with its context."""
    body = (text or "").strip()
    if not body:
        raise Declined("note_required", "Write the message", 422)
    with _atomic(c):
        task, requirement = _guard_item(c, actor, task_id, requirement_id)
        manager = _hub().type_manager(c, {**task, "type_id": requirement["pipeline_id"]})
        if not manager:
            raise Declined("no_manager", "This pipeline has no manager", 409)
        who = str(_hub().bot_actor(manager))
        stage = _one(c, "SELECT name FROM task_steps WHERE id=?", (requirement["pipeline_stage_id"],)) or {}
        context = f"[Review of {requirement['name']} at {stage.get('name') or 'its stage'}] {body}"
        _hub().task_comment(c, actor, task_id, context, to=who)
    return {"task_id": task_id, "requirement_id": requirement_id, "to": who}


def mark_met(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str, note: str) -> Row:
    """A rule editor's override of one requirement, with a required reason. It is a decision bound to the ticket's
    current work version, so new work makes it out of date like any other met. A bot, the pipeline manager included,
    cannot mark met. It does not move the ticket."""
    _person(actor)
    reason = _clip(note)
    if reason is None:
        raise Declined("why_required", "Say why", 422)
    with _atomic(c):
        task = _task(c, task_id)
        requirement = _requirement(c, requirement_id)
        if requirement.get("retired_at"):
            raise Declined("not_found", "No such requirement", 404)
        if not pipeline_rights.may_edit_rules(c, actor, requirement["pipeline_id"]):
            raise Declined("forbidden", "Only the pipeline's owner and admins mark a requirement met", 403)
        if task.get("status") not in ACTIVE or not pipeline_engine.counts_on_card(c, task_id, requirement):
            raise Declined("moved", "The ticket is no longer at this stage", 409)
        if requirement["kind"] == "approval":
            raise Declined("protected_approval", "A spec or release approval needs its named person to approve it", 409)
        current = _current_attachment(c, task_id, requirement_id)
        if requirement_state(c, task, requirement) == "met":
            raise Declined("already_met", "This requirement is already met", 409)
        decision = _decide(c, actor, task, requirement, "override", reason, current)
        pipeline_github_checks.publish_affected(c, task_id)
    return {"decision_id": decision["id"], "verdict": "override", "work_version": decision["work_version"]}


# ----------------------------------------------------------------------------- routes
class ReviewTarget(M.Contract):
    work_version: int = Field(ge=0)
    attachment_digest: str | None = Field(default=None, max_length=200)
    requirement_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2_000)


class ReviewNote(M.Contract):
    note: str = Field(min_length=1, max_length=2_000)


class NotValidated(ReviewNote):
    """Not validated names the work, the requirement, the attachment (id and digest, null when none) and the stage visit the
    reviewer saw; any of them changing since refuses it (stale)."""

    work_version: int = Field(ge=0)
    requirement_version: int = Field(ge=1)
    attachment_id: str | None = Field(max_length=128)
    attachment_digest: str | None = Field(max_length=200)
    stage_visit_id: str | None = Field(max_length=128)


class ReviewMessage(M.Contract):
    text: str = Field(min_length=1, max_length=4_000)


class ReviewWhy(M.Contract):
    why: str = Field(min_length=1, max_length=2_000)


class ReviewNothing(M.Contract):
    pass


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The review routes. A queue is read; every other call is a keyed write that one person makes."""
    from fastapi import Request

    from . import views

    human_only = cast(Any, views).human_only

    def reader(request: Request, pipeline_id: str | None, stage_id: str | None) -> Row:
        who = request.state.identity
        human_only(who)
        with cast(Any, store).read() as c:
            # A pipeline the reader may not see has no queue for them, and it answers exactly as one that is not there.
            # Items on members-only pipelines they may not see are left out, and so are tickets they may not open.
            reader_actor = who.task_actor or who.actor
            if pipeline_id and not pipeline_rights.may_see(c, reader_actor, pipeline_id):
                raise Problem("not_found", "No such pipeline", 404)
            hidden = pipeline_rights.hidden_pipeline_ids(c, reader_actor)
            scope = pipeline_rights.list_sql(c, who.actor, cast(Any, auth).task_sql(c, who))
            items = queue_for(c, who.actor, pipeline_id, stage_id, visible_sql=scope)
            return {"items": [item for item in items if item["pipeline_id"] not in hidden]}

    @app.get("/api/v2/pipelines/review/queue")
    def review_queue_all(request: Request) -> Row:  # pyright: ignore[reportUnusedFunction]
        """The ▶ queue across every pipeline: what waits on you, Late first, then oldest."""
        return reader(request, None, None)

    @app.get("/api/v2/pipelines/{pipeline_id}/review/queue")
    def review_queue_pipeline(request: Request, pipeline_id: str) -> Row:  # pyright: ignore[reportUnusedFunction]
        """The ▶ queue for one pipeline."""
        return reader(request, pipeline_id, None)

    @app.get("/api/v2/pipelines/{pipeline_id}/stages/{stage_id}/review/queue")
    def review_queue_stage(request: Request, pipeline_id: str, stage_id: str) -> Row:  # pyright: ignore[reportUnusedFunction]
        """The ▶ queue for one stage."""
        return reader(request, pipeline_id, stage_id)

    def write(request: Request, tid: str, body: M.Contract, fn: "Callable[[sqlite3.Connection, str], Row]") -> Row:
        who = request.state.identity
        human_only(who)

        def run(c: "sqlite3.Connection") -> Row:
            cast(Any, auth).task(c, who, tid)
            return fn(c, who.actor)

        return cast("Row", mutate(request, body, run))

    base = "/api/v2/pipelines/tasks/{tid}/requirements/{rid}"

    @app.post(base + "/claim")
    def review_claim(request: Request, tid: str, rid: str, body: ReviewNothing) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Start reviewing an item: a second reviewer is told who holds it."""
        return write(request, tid, body, lambda c, actor: claim(c, actor, tid, rid))

    @app.post(base + "/release")
    def review_release(request: Request, tid: str, rid: str, body: ReviewNothing) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Let go of your claim."""
        return write(request, tid, body, lambda c, actor: release(c, actor, tid, rid))

    @app.post(base + "/approve")
    def review_approve(request: Request, tid: str, rid: str, body: ReviewTarget) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Approve what you saw. Refused when the work, attachment or requirement changed since. Undo it within 10 s."""
        return write(
            request,
            tid,
            body,
            lambda c, actor: approve(
                c,
                actor,
                tid,
                rid,
                work_version=body.work_version,
                attachment_digest=body.attachment_digest,
                requirement_version=body.requirement_version,
                note=body.note,
            ),
        )

    @app.post(base + "/send-back")
    def review_send_back(request: Request, tid: str, rid: str, body: ReviewNote) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Send the work back to its assignee with a note."""
        return write(request, tid, body, lambda c, actor: send_back(c, actor, tid, rid, body.note))

    @app.post(base + "/not-validated")
    def review_not_validated(request: Request, tid: str, rid: str, body: NotValidated) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Validate's "Not validated": back to In development, with one follow-up Engineering ticket."""
        return write(
            request,
            tid,
            body,
            lambda c, actor: not_validated(
                c,
                actor,
                tid,
                rid,
                body.note,
                work_version=body.work_version,
                requirement_version=body.requirement_version,
                attachment_id=body.attachment_id,
                attachment_digest=body.attachment_digest,
                stage_visit_id=body.stage_visit_id,
            ),
        )

    @app.post(base + "/message-manager")
    def review_message_manager(request: Request, tid: str, rid: str, body: ReviewMessage) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Message the pipeline's manager about this item."""
        return write(request, tid, body, lambda c, actor: message_manager(c, actor, tid, rid, body.text))

    @app.post(base + "/mark-met")
    def review_mark_met(request: Request, tid: str, rid: str, body: ReviewWhy) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Mark one requirement met, with a reason. Only the pipeline's owner and admins."""
        return write(request, tid, body, lambda c, actor: mark_met(c, actor, tid, rid, body.why))

    @app.post("/api/v2/pipelines/decisions/{did}/undo")
    def review_undo(request: Request, did: str, body: ReviewNothing) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Take back your approval within 10 seconds, and the move it caused."""
        who = request.state.identity
        human_only(who)

        def run(c: "sqlite3.Connection") -> Row:
            return undo(c, who.actor, did)

        return cast("Row", mutate(request, body, run))
