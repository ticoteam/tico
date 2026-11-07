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

import json
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from pydantic import Field

from . import models as M
from . import pipeline_rights
from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Generator

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

CLAIM_MINUTES = 10
UNDO_SECONDS = 10
NOTE_BYTES = 500
ACTIVE = ("open", "doing", "waiting", "review", "ready")
PERSON_REVIEWED = ("person", "bot_then_person")
MET_VERDICTS = ("met", "override")
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
def _task(c: "sqlite3.Connection", task_id: str) -> Row:
    task = cast("Row | None", _hub().task(c, task_id))
    if task is None:
        raise Declined("not_found", "No such ticket", 404)
    return task


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


def _decisions(c: "sqlite3.Connection", task_id: str, requirement_id: str) -> list[Row]:
    """The decisions not undone about one requirement of one ticket, newest first."""
    return _rows(
        c,
        "SELECT * FROM stage_decisions WHERE task_id=? AND stage_requirement_id=? AND undone_at IS NULL "
        "ORDER BY created_at DESC, rowid DESC",
        (task_id, requirement_id),
    )


def _needs(requirement: Row) -> dict[str, Any]:
    try:
        value = json.loads(requirement.get("needs") or "{}")
    except ValueError:
        return {}
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _fresh_required(requirement: Row) -> bool:
    """Whether a met decision must be at the current work version: always, except an attachment that opted out."""
    if requirement["kind"] != "attachment":
        return True
    return _needs(requirement).get("newer_than_change", True) is not False


def _met_in(requirement: Row, attachment: Row | None, decisions: list[Row], revision: int) -> Row | None:
    """The decision that makes the requirement met, from its decisions (newest first), or None. The newest met, override,
    withdrawn or failed decision decides; a met one counts only at `revision` and for the attachment it judged."""
    for decision in decisions:
        if decision["verdict"] not in (*MET_VERDICTS, "withdrawn", "failed"):
            continue
        if decision["verdict"] not in MET_VERDICTS:
            return None
        if _fresh_required(requirement) and decision["work_version"] != revision:
            return None
        if attachment and decision["attachment_digest"] and decision["attachment_digest"] != attachment["digest"]:
            return None
        return decision
    return None


def _met_decision(c: "sqlite3.Connection", task: Row, requirement: Row, attachment: Row | None) -> Row | None:
    """The decision that makes the requirement met now, or None (see `_met_in`)."""
    return _met_in(requirement, attachment, _decisions(c, task["id"], requirement["id"]), _revision(task))


def requirement_state(c: "sqlite3.Connection", task: Row, requirement: Row) -> str:
    """`met`, `failed` (the latest judgement at this work version), `waiting` (a reviewer is to decide) or `missing`
    (an attachment requirement with nothing attached)."""
    attachment = _current_attachment(c, task["id"], requirement["id"])
    if _met_decision(c, task, requirement, attachment):
        return "met"
    for decision in _decisions(c, task["id"], requirement["id"]):
        if decision["verdict"] in ("met", "override", "withdrawn", "failed"):
            if decision["verdict"] == "failed" and decision["work_version"] == _revision(task):
                return "failed"
            break
    if requirement["kind"] == "attachment" and attachment is None:
        return "missing"
    return "waiting"


def _sent_back_hold(c: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """Whether the newest send-back still holds the item off the queue: nothing new was attached since and the work did
    not change."""
    for decision in _decisions(c, task["id"], requirement["id"]):
        if decision["verdict"] != "sent_back":
            continue
        newer = _one(
            c,
            "SELECT 1 AS x FROM requirement_attachments WHERE task_id=? AND stage_requirement_id=? AND created_at>? "
            "AND removed_at IS NULL LIMIT 1",
            (task["id"], requirement["id"], decision["created_at"]),
        )
        return decision["work_version"] == _revision(task) and newer is None
    return False


def _routed_to(c: "sqlite3.Connection", task_id: str, requirement_id: str) -> str | None:
    """Who the latest send-back routed the next attachment to (it skips the bot), if anyone."""
    for decision in _decisions(c, task_id, requirement_id):
        if decision["verdict"] == "sent_back":
            return cast("str | None", decision["route_next_to"])
    return None


def _asked_backup(c: "sqlite3.Connection", task_id: str, requirement: Row) -> bool:
    """Whether the pipeline manager asked the backup reviewer about this ticket's current visit."""
    visit = _open_visit(c, task_id)
    if visit is None:
        return False
    return (
        _one(
            c,
            "SELECT 1 AS x FROM pipeline_manager_actions WHERE stage_visit_id=? AND stage_requirement_id=? "
            "AND action='ask_backup' LIMIT 1",
            (visit["id"], requirement["id"]),
        )
        is not None
    )


def _why_sees(c: "sqlite3.Connection", actor: str, task_id: str, requirement: Row) -> str | None:
    """Why `actor` is to see this item: `reviewer`, `sent_back` (routed to them) or `backup`; None when they do not.
    Only these see it, whatever else they may do on the ticket."""
    if requirement.get("reviewer_id") == actor:
        return "reviewer"
    if _routed_to(c, task_id, requirement["id"]) == actor:
        return "sent_back"
    if requirement.get("backup_reviewer_id") == actor and _asked_backup(c, task_id, requirement):
        return "backup"
    return None


def _bot_passed_it_up(c: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """For bot_then_person: whether the bot's latest call at this work version was unsure or failed."""
    for decision in _decisions(c, task["id"], requirement["id"]):
        if decision["judge"] in ("llm", "bot") and decision["work_version"] == _revision(task):
            return decision["verdict"] in ("unsure", "failed")
    return False


def _waits_on_person(c: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """Whether the requirement now waits for a person's decision: not met, something to decide on, not held off by a
    send-back, and for bot_then_person the bot passed it up or a send-back routed it to a person."""
    if requirement.get("retired_at") or requirement["reviewed_by"] not in PERSON_REVIEWED:
        return False
    if requirement_state(c, task, requirement) in ("met", "missing"):
        return False
    if _sent_back_hold(c, task, requirement):
        return False
    if requirement["reviewed_by"] == "bot_then_person":
        return _routed_to(c, task["id"], requirement["id"]) is not None or _bot_passed_it_up(c, task, requirement)
    return True


def _open_claim(c: "sqlite3.Connection", task_id: str, requirement_id: str, now: str | None = None) -> Row | None:
    """The unexpired claim on the item. An expired one counts as released."""
    return _one(
        c,
        "SELECT * FROM review_claims WHERE task_id=? AND stage_requirement_id=? AND released_at IS NULL "
        "AND (expires_at IS NULL OR expires_at>?)",
        (task_id, requirement_id, now or _now()),
    )


def _entered_at(c: "sqlite3.Connection", task: Row) -> str:
    visit = _open_visit(c, task["id"])
    if visit:
        return str(visit["entered_at"])
    event = _one(
        c,
        "SELECT max(created_at) AS at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='step'",
        (task["id"],),
    )
    return str((event or {}).get("at") or task.get("created_at") or _now())


def _late(c: "sqlite3.Connection", task: Row, since: str, now: str) -> bool:
    """Whether the ticket has been at its stage longer than the stage's Warn after."""
    stage = _one(c, "SELECT limit_minutes FROM task_steps WHERE id=?", (task.get("step_id") or "",))
    minutes = (stage or {}).get("limit_minutes")
    return bool(minutes) and since < _shift(now, minutes=-float(minutes))


def _item(c: "sqlite3.Connection", actor: str, task: Row, requirement: Row, why: str, now: str) -> Row:
    attachment = _current_attachment(c, task["id"], requirement["id"])
    stage = _one(c, "SELECT id, name FROM task_steps WHERE id=?", (requirement["pipeline_stage_id"],)) or {}
    pipeline = _one(c, "SELECT id, name FROM task_types WHERE id=?", (requirement["pipeline_id"],)) or {}
    claim_row = _open_claim(c, task["id"], requirement["id"], now)
    since = _entered_at(c, task)
    return {
        "task_id": task["id"],
        "title": task.get("title") or "",
        "number": task.get("number"),
        "pipeline_id": requirement["pipeline_id"],
        "pipeline": pipeline.get("name") or "",
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
        "late": _late(c, task, since, now),
        "claimed_by": claim_row["claimed_by"] if claim_row and claim_row["claimed_by"] != actor else None,
        "claimed_by_me": bool(claim_row and claim_row["claimed_by"] == actor),
    }


def _named(owner: str, humans: dict[str, Any], bots: dict[str, Row]) -> str:
    """What a person reads for an owner, as `hubdb.actor_name` does it, from rows fetched in a batch."""
    key = owner.split(":", 1)[1] if ":" in owner else owner
    if not owner.startswith("human:"):
        bot = bots.get(key)
        if bot:
            return str(bot.get("display_name") or bot.get("slug") or key)
    return str(humans.get(key) or key)


def _bot_note(decisions: list[Row], work_version: int) -> str:
    """The reason of the latest bot judgement on this requirement, when it is for the current work; else ''."""
    for decision in decisions:
        if decision["judge"] == "bot":
            return str(decision.get("reason") or "") if decision["work_version"] == work_version else ""
    return ""


def _queue_fields(c: "sqlite3.Connection", actor: str, items: list[Row], now: str) -> None:
    """Adds assignee, bot_note, moves_to, reviewer_id and can_mark_met to each queue item. Each input is read with one
    query over all the items, so the count does not grow with the rows; `may_edit_rules` runs once per pipeline."""
    if not items:
        return
    task_ids = _ids(i["task_id"] for i in items)
    stage_ids = _ids(i["stage_id"] for i in items)
    live = _rows(
        c,
        "SELECT * FROM stage_requirements WHERE pipeline_stage_id IN (SELECT value FROM json_each(?)) "
        "AND retired_at IS NULL",
        (stage_ids,),
    )
    req_ids = _ids(r["id"] for r in live)
    requirements = {r["id"]: r for r in live}
    owners = {
        r["id"]: r["owner_id"]
        for r in _rows(c, "SELECT id, owner_id FROM tasks WHERE id IN (SELECT value FROM json_each(?))", (task_ids,))
    }
    bare = _ids(o.split(":", 1)[1] if ":" in o else o for o in owners.values() if o)
    humans = {
        r["id"]: r["name"]
        for r in _rows(c, "SELECT id, name FROM humans WHERE id IN (SELECT value FROM json_each(?))", (bare,))
    }
    bots = {
        r["id"]: r
        for r in _rows(
            c, "SELECT id, display_name, slug FROM bots WHERE id IN (SELECT value FROM json_each(?))", (bare,)
        )
    }
    decisions: dict[tuple[str, str], list[Row]] = {}
    for d in _rows(
        c,
        "SELECT * FROM stage_decisions WHERE undone_at IS NULL AND task_id IN (SELECT value FROM json_each(?)) "
        "AND stage_requirement_id IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC, rowid DESC",
        (task_ids, req_ids),
    ):
        decisions.setdefault((d["task_id"], d["stage_requirement_id"]), []).append(d)
    current: dict[tuple[str, str], Row] = {}
    for a in _rows(
        c,
        "SELECT id, task_id, stage_requirement_id, digest FROM requirement_attachments WHERE removed_at IS NULL "
        "AND task_id IN (SELECT value FROM json_each(?)) AND stage_requirement_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY created_at DESC, rowid DESC",
        (task_ids, req_ids),
    ):
        current.setdefault((a["task_id"], a["stage_requirement_id"]), a)
    holders: dict[tuple[str, str], str] = {}
    for cl in _rows(
        c,
        "SELECT task_id, stage_requirement_id, claimed_by FROM review_claims WHERE released_at IS NULL "
        "AND (expires_at IS NULL OR expires_at>?) AND task_id IN (SELECT value FROM json_each(?)) "
        "AND stage_requirement_id IN (SELECT value FROM json_each(?))",
        (now, task_ids, req_ids),
    ):
        holders[(cl["task_id"], cl["stage_requirement_id"])] = cl["claimed_by"]
    links = {
        r["from_pipeline_stage_id"]: r["to_pipeline_id"]
        for r in _rows(
            c,
            "SELECT from_pipeline_stage_id, to_pipeline_id FROM pipeline_links WHERE mode='move' AND retired_at IS NULL "
            "AND from_pipeline_stage_id IN (SELECT value FROM json_each(?))",
            (stage_ids,),
        )
    }
    pipeline_ids = {i["pipeline_id"] for i in items}
    steps = _rows(
        c,
        "SELECT id, type_id, name, position, status FROM task_steps WHERE type_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY position, id",
        (_ids([*pipeline_ids, *links.values()]),),
    )
    names = {s["id"]: s["name"] for s in steps}
    rights = {p: pipeline_rights.may_edit_rules(c, actor, p) for p in pipeline_ids}
    for item in items:
        key = (item["task_id"], item["requirement_id"])
        owner = owners.get(item["task_id"])
        item["assignee"] = {"id": owner, "name": _named(owner, humans, bots)} if owner else None
        item["bot_note"] = _bot_note(decisions.get(key, []), item["work_version"])
        item["reviewer_id"] = holders.get(key) or requirements[item["requirement_id"]].get("reviewer_id")
        item["can_mark_met"] = bool(rights[item["pipeline_id"]])
        item["moves_to"] = _moves_to(item, live, decisions, current, links, steps, names)


def _moves_to(
    item: Row,
    live: list[Row],
    decisions: dict[tuple[str, str], list[Row]],
    current: dict[tuple[str, str], Row],
    links: dict[str, str],
    steps: list[Row],
    names: dict[str, str],
) -> Row | None:
    """Where the ticket goes if this item is the stage's last unmet requirement, the same destination approve uses;
    None while another requirement of the stage is unmet, or at the last stage."""
    for other in live:
        if other["pipeline_stage_id"] != item["stage_id"] or other["id"] == item["requirement_id"]:
            continue
        if other.get("enforced_by"):
            continue  # a mirror of the review gate, which still decides it (as in `_stage_met`)
        key = (item["task_id"], other["id"])
        if _met_in(other, current.get(key), decisions.get(key, []), item["work_version"]) is None:
            return None
    destination = _destination_in(item["stage_id"], item["pipeline_id"], links.get(item["stage_id"]), steps)
    if destination is None:
        return None
    return {"stage_id": destination[1], "name": names.get(destination[1], "")}


def queue_for(
    c: "sqlite3.Connection", actor: str, pipeline_id: str | None = None, stage_id: str | None = None
) -> list[Row]:
    """The items waiting for this reviewer, Late first, then oldest. `pipeline_id` and `stage_id` narrow it to one
    pipeline or one stage. Only a reviewer, a send-back's route or an asked backup sees an item; a bot sees none."""
    if not str(actor or "").startswith("human:"):
        return []
    sql = (
        "SELECT t.id AS task_id, r.id AS requirement_id FROM stage_requirements r "
        "JOIN tasks t ON t.step_id=r.pipeline_stage_id "
        f"WHERE r.retired_at IS NULL AND r.reviewed_by IN ({','.join('?' * len(PERSON_REVIEWED))}) "
        f"AND t.status IN ({','.join('?' * len(ACTIVE))}) "
        "AND (r.reviewer_id=? OR r.backup_reviewer_id=? OR EXISTS (SELECT 1 FROM stage_decisions d "
        "WHERE d.task_id=t.id AND d.stage_requirement_id=r.id AND d.route_next_to=?))"
    )
    args: list[Any] = [*PERSON_REVIEWED, *ACTIVE, actor, actor, actor]
    if pipeline_id:
        sql += " AND r.pipeline_id=?"
        args.append(pipeline_id)
    if stage_id:
        sql += " AND r.pipeline_stage_id=?"
        args.append(stage_id)
    now = _now()
    items: list[Row] = []
    for pair in _rows(c, sql, tuple(args)):
        task = cast("Row | None", _hub().task(c, pair["task_id"]))
        requirement = _one(c, "SELECT * FROM stage_requirements WHERE id=?", (pair["requirement_id"],))
        if task is None or requirement is None:
            continue
        why = _why_sees(c, actor, task["id"], requirement)
        if why and _waits_on_person(c, task, requirement):
            items.append(_item(c, actor, task, requirement, why, now))
    _queue_fields(c, actor, items, now)
    items.sort(key=lambda item: (not item["late"], item["since"], item["task_id"], item["requirement_id"]))
    return items


# ----------------------------------------------------------------------------- claims
def _guard_item(c: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str) -> tuple[Row, Row]:
    """The ticket and the requirement, or the refusal: a person, at the stage, whom the item waits on."""
    _person(actor)
    task = _task(c, task_id)
    requirement = _requirement(c, requirement_id)
    if requirement.get("retired_at") or requirement["reviewed_by"] not in PERSON_REVIEWED:
        raise Declined("not_person_reviewed", "A person does not review this requirement", 409)
    if task.get("step_id") != requirement["pipeline_stage_id"] or task.get("status") not in ACTIVE:
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
    """Whether every live requirement of the stage is met."""
    for other in _rows(
        c,
        "SELECT * FROM stage_requirements WHERE pipeline_stage_id=? AND retired_at IS NULL",
        (requirement["pipeline_stage_id"],),
    ):
        if other.get("enforced_by"):
            continue  # a mirror of the review gate, which still decides it
        if requirement_state(c, task, other) != "met":
            return False
    return True


def _advance(c: "sqlite3.Connection", actor: str, task: Row, requirement: Row) -> Row | None:
    """Moves the ticket on when its stage is fully met. Returns what moved ({from_type, from_step, to_type, to_step}),
    or None when it stays (not the last requirement, no next stage, or the engine refuses the move)."""
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
    except _hub().Refused:
        c.execute("ROLLBACK TO pipeline_advance")
        c.execute("RELEASE pipeline_advance")
        return None
    c.execute("RELEASE pipeline_advance")
    return moved


def approve(
    c: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement_id: str,
    *,
    work_version: int | None = None,
    attachment_digest: Any = CURRENT,
    requirement_version: int | None = None,
    note: str | None = None,
) -> Row:
    """A person's Approve of what they saw (a version left out is not checked): `work_version`, `attachment_digest` and `requirement_version` come from the
    queue item. It is refused when the work, the attachment or the requirement changed since, when someone else holds the
    claim, or when the actor is not the person the item waits on. When it meets the last requirement of the stage the
    ticket moves on. Returns the decision, whether it advanced and until when it can be undone."""
    with _atomic(c):
        task, requirement = _guard_item(c, actor, task_id, requirement_id)
        _check_claim(c, actor, task, requirement)
        current = _current_attachment(c, task_id, requirement_id)
        if work_version is not None and _revision(task) != work_version:
            raise Declined("stale", "The work changed since you looked", 409, extra={"reason": "work"})
        if attachment_digest is not CURRENT and (current["digest"] if current else None) != attachment_digest:
            raise Declined("stale", "The attachment changed since you looked", 409, extra={"reason": "attachment"})
        if requirement_version is not None and requirement_version != requirement["version"]:
            raise Declined("stale", "The requirement changed since you looked", 409, extra={"reason": "requirement"})
        if requirement["kind"] == "attachment" and current is None:
            raise Declined("nothing_to_approve", "Nothing is attached to approve", 409)
        if _met_decision(c, task, requirement, current):
            raise Declined("already_met", "This requirement is already met", 409)
        decision = _decide(c, actor, task, requirement, "met", _clip(note), current)
        moved = _advance(c, actor, task, requirement)
        if moved:
            c.execute(
                "UPDATE stage_decisions SET outcome=?, outcome_at=? WHERE id=?",
                (json.dumps({"advanced": moved}), _now(), decision["id"]),
            )
        _release_in(c, actor, task_id, requirement_id)
    return {
        "decision_id": decision["id"],
        "verdict": "met",
        "advanced": moved is not None,
        "undo_until": _shift(decision["created_at"], seconds=UNDO_SECONDS),
    }


def undo(c: "sqlite3.Connection", actor: str, decision_id: str) -> Row:
    """Takes back one's own approval within UNDO_SECONDS, and the move it caused. Refused after that, or once the ticket
    went somewhere else since."""
    _person(actor)
    with _atomic(c):
        decision = _one(c, "SELECT * FROM stage_decisions WHERE id=?", (decision_id,))
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
        if moved:
            task = _task(c, decision["task_id"])
            # The approval's own move is the one step event since the decision; another one means it went on.
            steps = _rows(
                c,
                "SELECT id FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='step' "
                "AND created_at>=?",
                (decision["task_id"], decision["created_at"]),
            )
            if task.get("step_id") != moved["to_step"] or len(steps) > 1:
                raise Declined("moved_on", "The ticket has moved on", 409)
            _hub().task_update(
                c,
                _hub().KEEPER,
                decision["task_id"],
                type=moved["from_type"] if moved["from_type"] != moved["to_type"] else None,
                step=moved["from_step"],
                note=f"Approval undone by {_hub().actor_name(c, actor)}",
            )
        c.execute("UPDATE stage_decisions SET undone_at=?, undone_by=? WHERE id=?", (now, actor, decision_id))
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
        if task.get("step_id") != requirement["pipeline_stage_id"] or task.get("status") not in ACTIVE:
            raise Declined("moved", "The ticket is no longer at this stage", 409)
        current = _current_attachment(c, task_id, requirement_id)
        if _met_decision(c, task, requirement, current):
            raise Declined("already_met", "This requirement is already met", 409)
        decision = _decide(c, actor, task, requirement, "override", reason, current)
    return {"decision_id": decision["id"], "verdict": "override", "work_version": decision["work_version"]}


# ----------------------------------------------------------------------------- routes
class ReviewTarget(M.Contract):
    work_version: int = Field(ge=0)
    attachment_digest: str | None = Field(default=None, max_length=200)
    requirement_version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2_000)


class ReviewNote(M.Contract):
    note: str = Field(min_length=1, max_length=2_000)


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
            return {"items": queue_for(c, who.actor, pipeline_id, stage_id)}

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
