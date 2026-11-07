# pyright: strict
"""Stage requirements: the rules a stage sets (its kind, what it needs, who reviews), their versions, and a pipeline's
prefix and settings. Only the pipeline's owner and admins edit them (pipeline_rights). Track B owns this module.

A requirement is an exit rule: the work must meet it before the ticket leaves the stage. Every edit appends a version
(stage_requirement_versions) and keeps the one it replaces, so a decision made under v1 still reads as made under v1.
Retiring a requirement keeps its rows and its log; it only stops counting for new tickets.
"""

import json
import re
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import Request
from pydantic import Field

from . import hubdb
from . import pipeline_rights
from .models import Contract
from .store import Problem

# hubdb is untyped: its functions are Any here (as in stranded_resume.py), not partially unknown.
H: Any = hubdb

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

KINDS = ("attachment", "field", "approval", "run")
REVIEWERS = ("nobody", "bot", "person", "bot_then_person")
FORMATS = ("image", "video", "page", "file", "link")
FIELDS = ("pr_linked", "label", "assignee", "due")
RUN_SOURCES = ("github_checks", "test_receipt", "check_pool")
PREFIX = re.compile(r"^[A-Za-z]{1,2}$")
RUBRIC_MAX = 2000
NAME_MAX = 80
LIMIT_MAX_MINUTES = 60 * 24 * 365
EVERY_MIN, EVERY_MAX = 5, 24 * 60
# The fields a requirement's definition holds; a version keeps exactly these.
DEFINITION = (
    "name",
    "kind",
    "needs",
    "reviewed_by",
    "reviewer_id",
    "backup_reviewer_id",
    "rubric",
    "applies_to_label",
    "position",
)


class RequirementBody(Contract):
    """A requirement's whole definition. An edit sends all of it; the version it replaces is kept."""

    name: str = Field(min_length=1, max_length=NAME_MAX)
    kind: Literal["attachment", "field", "approval", "run"]
    needs: dict[str, Any] = Field(default_factory=dict)
    reviewed_by: Literal["nobody", "bot", "person", "bot_then_person"]
    reviewer_id: str | None = Field(default=None, max_length=128)
    backup_reviewer_id: str | None = Field(default=None, max_length=128)
    rubric: str | None = Field(default=None, max_length=RUBRIC_MAX)
    applies_to_label: str | None = Field(default=None, max_length=80)
    position: int | None = Field(default=None, ge=0, le=10000)


class PipelineSettingsBody(Contract):
    """What a pipeline's owner or an admin sets. Every field is optional; a field left out stays as it is."""

    prefix: str | None = Field(default=None, max_length=2)
    owner_id: str | None = Field(default=None, max_length=128)
    manager_id: str | None = Field(default=None, max_length=128)
    manager_every_minutes: int | None = Field(default=None, ge=EVERY_MIN, le=EVERY_MAX)


class StageLimitBody(Contract):
    """The stage's "Warn after" time in minutes; null turns the warning off."""

    limit_minutes: int | None = Field(default=None, ge=1, le=LIMIT_MAX_MINUTES)


class Nothing(Contract):
    """The body of a route that takes none (mutate keys its idempotency on the body)."""


def requirements_for_stage(conn: "sqlite3.Connection", pipeline_stage_id: str) -> list[dict[str, Any]]:
    """The live requirements of a stage in position order."""
    rows = conn.execute(
        "SELECT * FROM stage_requirements WHERE pipeline_stage_id=? AND retired_at IS NULL "
        "ORDER BY position, created_at, id",
        (pipeline_stage_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def requirement(conn: "sqlite3.Connection", requirement_id: str) -> dict[str, Any] | None:
    """One requirement row, retired or not, or None."""
    row = conn.execute("SELECT * FROM stage_requirements WHERE id=?", (requirement_id,)).fetchone()
    return _row(row) if row else None


def versions(conn: "sqlite3.Connection", requirement_id: str) -> list[dict[str, Any]]:
    """Every version of a requirement, oldest first, each with its definition."""
    found = conn.execute(
        "SELECT version, definition, changed_by, created_at FROM stage_requirement_versions "
        "WHERE stage_requirement_id=? ORDER BY version",
        (requirement_id,),
    ).fetchall()
    return [{"version": r[0], "definition": json.loads(r[1]), "changed_by": r[2], "created_at": r[3]} for r in found]


def _row(row: "sqlite3.Row | tuple[Any, ...]") -> dict[str, Any]:
    out = dict(row)
    out["needs"] = json.loads(out.get("needs") or "{}")
    return out


def _stage(conn: "sqlite3.Connection", stage_id: str) -> tuple[str, str]:
    """(pipeline id, stage name) of a stage, or a 404."""
    found = conn.execute("SELECT type_id, name FROM task_steps WHERE id=?", (stage_id,)).fetchone()
    if found is None:
        raise Problem("not_found", "No such stage", 404)
    return str(found[0]), str(found[1])


def _may_edit(conn: "sqlite3.Connection", actor: str, pipeline_id: str) -> None:
    if not pipeline_rights.may_edit_rules(conn, actor, pipeline_id):
        raise Problem("forbidden", "Only the pipeline's owner and admins can change its rules", 403)


def _human(actor: object) -> bool:
    text = str(actor or "")
    return text.startswith("human:") and len(text) > len("human:")


def check_definition(body: dict[str, Any]) -> dict[str, Any]:
    """The definition as it is stored, or a 422 for a combination the engine cannot evaluate. Checks what the kinds and
    reviewers need from each other; the stage and the people named are checked by the caller."""
    kind, reviewed_by = body["kind"], body["reviewed_by"]
    needs = dict(body.get("needs") or {})
    if kind == "attachment":
        formats: Any = needs.get("formats")
        if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in cast("list[Any]", formats)):
            raise Problem("validation", f"An attachment needs formats from: {', '.join(FORMATS)}", 422)
        needs = {
            "formats": sorted(set(cast("list[str]", formats))),
            "newer_than_change": bool(needs.get("newer_than_change", True)),
            "is_work": bool(needs.get("is_work", False)),
        }
    elif kind == "field":
        if needs.get("field") not in FIELDS:
            raise Problem("validation", f"A field needs a field from: {', '.join(FIELDS)}", 422)
        needs = {"field": needs["field"]}
    elif kind == "run":
        if needs.get("source") not in RUN_SOURCES:
            raise Problem("validation", f"A run needs a source from: {', '.join(RUN_SOURCES)}", 422)
        needs = {"source": needs["source"], "at_head": True}
    else:
        needs = {}
    if kind == "approval" and reviewed_by != "person":
        raise Problem("validation", "An approval is reviewed by a person", 422)
    reviewer = body.get("reviewer_id") or None
    backup = body.get("backup_reviewer_id") or None
    if reviewed_by in ("person", "bot_then_person") and not _human(reviewer):
        raise Problem("validation", "A person must be named as the reviewer", 422)
    if reviewed_by == "nobody" and (reviewer or backup):
        raise Problem("validation", "Nobody reviews this requirement, so it names no reviewer", 422)
    if backup and not _human(backup):
        raise Problem("validation", "A backup reviewer is a person", 422)
    rubric = (body.get("rubric") or "").strip() or None
    if reviewed_by in ("bot", "bot_then_person") and kind == "attachment" and not rubric:
        raise Problem("validation", "A bot review needs a rubric to judge the attachment against", 422)
    return {
        "name": body["name"].strip(),
        "kind": kind,
        "needs": needs,
        "reviewed_by": reviewed_by,
        "reviewer_id": reviewer if reviewed_by in ("person", "bot_then_person") else None,
        "backup_reviewer_id": backup if reviewed_by != "nobody" else None,
        "rubric": rubric if reviewed_by != "nobody" else None,
        "applies_to_label": (body.get("applies_to_label") or "").strip() or None,
        "position": body.get("position"),
    }


def _name_free(conn: "sqlite3.Connection", stage_id: str, name: str, except_id: str = "") -> None:
    taken = conn.execute(
        "SELECT 1 FROM stage_requirements WHERE pipeline_stage_id=? AND name=? AND retired_at IS NULL AND id<>?",
        (stage_id, name, except_id),
    ).fetchone()
    if taken:
        raise Problem("conflict", f"This stage already has a requirement named {name}", 409)


def add(conn: "sqlite3.Connection", actor: str, stage_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """A new requirement on a stage, at version 1."""
    pipeline_id, _ = _stage(conn, stage_id)
    _may_edit(conn, actor, pipeline_id)
    definition = check_definition(body)
    _name_free(conn, stage_id, definition["name"])
    now = H.now()
    position = definition["position"]
    if position is None:
        found = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM stage_requirements "
            "WHERE pipeline_stage_id=? AND retired_at IS NULL",
            (stage_id,),
        ).fetchone()
        position = int(found[0])
    row_id = H.new_id()
    conn.execute(
        "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, reviewer_id, "
        "backup_reviewer_id, rubric, applies_to_label, position, version, created_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (
            row_id,
            pipeline_id,
            stage_id,
            definition["name"],
            definition["kind"],
            json.dumps(definition["needs"]),
            definition["reviewed_by"],
            definition["reviewer_id"],
            definition["backup_reviewer_id"],
            definition["rubric"],
            definition["applies_to_label"],
            position,
            actor,
            now,
            now,
        ),
    )
    _version(conn, row_id, 1, {**definition, "position": position}, actor, now)
    H.event(conn, actor, "requirement.add", row_id, {"stage": stage_id, "name": definition["name"]})
    return requirement(conn, row_id) or {}


def _version(
    conn: "sqlite3.Connection", requirement_id: str, version: int, definition: dict[str, Any], by: str, at: str
) -> None:
    conn.execute(
        "INSERT INTO stage_requirement_versions (stage_requirement_id, version, definition, changed_by, created_at) "
        "VALUES (?,?,?,?,?)",
        (requirement_id, version, json.dumps(definition, sort_keys=True, default=str), by, at),
    )


def edit(conn: "sqlite3.Connection", actor: str, requirement_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Replace a requirement's definition. A change bumps its version and keeps the one it replaces; an edit that
    changes nothing is a no-op. A retired requirement cannot be edited."""
    current = requirement(conn, requirement_id)
    if current is None or current["retired_at"]:
        raise Problem("not_found", "No such requirement", 404)
    pipeline_id, _ = _stage(conn, current["pipeline_stage_id"])
    _may_edit(conn, actor, pipeline_id)
    definition = check_definition(body)
    if definition["position"] is None:
        definition["position"] = current["position"]
    stored = {k: current[k] for k in DEFINITION}
    if stored == definition:
        return current
    if definition["name"] != current["name"]:
        _name_free(conn, current["pipeline_stage_id"], definition["name"], requirement_id)
    version = int(current["version"]) + 1
    now = H.now()
    conn.execute(
        "UPDATE stage_requirements SET name=?, kind=?, needs=?, reviewed_by=?, reviewer_id=?, backup_reviewer_id=?, "
        "rubric=?, applies_to_label=?, position=?, version=?, updated_at=? WHERE id=?",
        (
            definition["name"],
            definition["kind"],
            json.dumps(definition["needs"]),
            definition["reviewed_by"],
            definition["reviewer_id"],
            definition["backup_reviewer_id"],
            definition["rubric"],
            definition["applies_to_label"],
            definition["position"],
            version,
            now,
            requirement_id,
        ),
    )
    _version(conn, requirement_id, version, definition, actor, now)
    H.event(conn, actor, "requirement.edit", requirement_id, {"version": version})
    return requirement(conn, requirement_id) or {}


def retire(conn: "sqlite3.Connection", actor: str, requirement_id: str) -> dict[str, Any]:
    """Stop a requirement counting for new tickets. Its rows, versions and decisions stay."""
    current = requirement(conn, requirement_id)
    if current is None:
        raise Problem("not_found", "No such requirement", 404)
    pipeline_id, _ = _stage(conn, current["pipeline_stage_id"])
    _may_edit(conn, actor, pipeline_id)
    if current["retired_at"]:
        return current
    now = H.now()
    conn.execute(
        "UPDATE stage_requirements SET retired_at=?, retired_by=?, updated_at=? WHERE id=?",
        (now, actor, now, requirement_id),
    )
    H.event(conn, actor, "requirement.retire", requirement_id, {"stage": current["pipeline_stage_id"]})
    return requirement(conn, requirement_id) or {}


def set_prefix(conn: "sqlite3.Connection", actor: str, pipeline_id: str, prefix: str) -> str:
    """The pipeline's ticket prefix: one or two letters, unique among pipelines. Stored in capitals."""
    value = str(prefix or "").strip()
    if not PREFIX.match(value):
        raise Problem("validation", "A prefix is one or two letters", 422)
    value = value.upper()
    taken = conn.execute("SELECT id FROM task_types WHERE prefix=? AND id<>?", (value, pipeline_id)).fetchone()
    if taken:
        raise Problem("conflict", f"The prefix {value} is already used by another pipeline", 409)
    conn.execute("UPDATE task_types SET prefix=?, updated_at=? WHERE id=?", (value, H.now(), pipeline_id))
    H.event(conn, actor, "pipeline.prefix", pipeline_id, {"prefix": value})
    return value


def update_settings(conn: "sqlite3.Connection", actor: str, pipeline_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Change the pipeline's prefix, owner, manager or manager cadence. Only its owner and admins may."""
    found = conn.execute("SELECT id FROM task_types WHERE id=?", (pipeline_id,)).fetchone()
    if found is None:
        raise Problem("not_found", "No such pipeline", 404)
    _may_edit(conn, actor, pipeline_id)
    if body.get("owner_id") is not None and not _human(body["owner_id"]):
        raise Problem("validation", "A pipeline's owner is a person", 422)
    if body.get("manager_id") is not None and not str(body["manager_id"]).startswith("bot:"):
        raise Problem("validation", "A pipeline manager is a bot", 422)
    if body.get("prefix") is not None:
        set_prefix(conn, actor, pipeline_id, body["prefix"])
    now = H.now()
    if body.get("owner_id") is not None:
        conn.execute(
            "UPDATE task_types SET pipeline_owner_id=?, updated_at=? WHERE id=?", (body["owner_id"], now, pipeline_id)
        )
    if body.get("manager_id") is not None:
        conn.execute("UPDATE task_types SET manager=?, updated_at=? WHERE id=?", (body["manager_id"], now, pipeline_id))
    if body.get("manager_every_minutes") is not None:
        conn.execute(
            "UPDATE task_types SET manager_every_minutes=?, updated_at=? WHERE id=?",
            (int(body["manager_every_minutes"]), now, pipeline_id),
        )
    H.event(conn, actor, "pipeline.settings", pipeline_id, {k: v for k, v in body.items() if v is not None})
    row = conn.execute(
        "SELECT id, name, prefix, pipeline_owner_id AS owner_id, manager AS manager_id, manager_every_minutes "
        "FROM task_types WHERE id=?",
        (pipeline_id,),
    ).fetchone()
    return dict(row)


def set_stage_limit(conn: "sqlite3.Connection", actor: str, stage_id: str, minutes: int | None) -> dict[str, Any]:
    """The stage's "Warn after" time, in minutes, or None for no warning."""
    pipeline_id, name = _stage(conn, stage_id)
    _may_edit(conn, actor, pipeline_id)
    conn.execute("UPDATE task_steps SET limit_minutes=? WHERE id=?", (minutes, stage_id))
    H.event(conn, actor, "stage.limit", stage_id, {"limit_minutes": minutes})
    return {"id": stage_id, "name": name, "limit_minutes": minutes}


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The requirement, pipeline settings and stage limit routes. Reads need a signed-in company member; every write
    goes through pipeline_rights, so a bot is refused unless it is the pipeline's owner."""
    db: Any = store  # Store is untyped, so its connections are Any here, not partially unknown

    @app.get("/api/v2/pipeline-stages/{stage_id}/requirements")
    def stage_requirements(request: Request, stage_id: str) -> dict[str, Any]:
        """The live requirements of a stage, in order."""
        with db.read() as c:
            _stage(c, stage_id)
            return {"requirements": requirements_for_stage(c, stage_id)}

    @app.get("/api/v2/requirements/{requirement_id}")
    def requirement_read(request: Request, requirement_id: str) -> dict[str, Any]:
        """One requirement with its version history."""
        with db.read() as c:
            row = requirement(c, requirement_id)
            if row is None:
                raise Problem("not_found", "No such requirement", 404)
            return {**row, "versions": versions(c, requirement_id)}

    @app.post("/api/v2/pipeline-stages/{stage_id}/requirements")
    def requirement_add(request: Request, stage_id: str, body: RequirementBody) -> dict[str, Any]:
        """Add a requirement to a stage. The pipeline's owner and admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return add(c, who.actor, stage_id, body.model_dump())

        return mutate(request, body, write)

    @app.put("/api/v2/requirements/{requirement_id}")
    def requirement_edit(request: Request, requirement_id: str, body: RequirementBody) -> dict[str, Any]:
        """Replace a requirement's definition; the version it replaces is kept. The pipeline's owner and admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return edit(c, who.actor, requirement_id, body.model_dump())

        return mutate(request, body, write)

    @app.delete("/api/v2/requirements/{requirement_id}")
    def requirement_retire(request: Request, requirement_id: str) -> dict[str, Any]:
        """Retire a requirement: it stops counting for new tickets and its history stays."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return retire(c, who.actor, requirement_id)

        return mutate(request, Nothing(), write)

    @app.patch("/api/v2/pipelines/{pipeline_id}/settings")
    def pipeline_settings(request: Request, pipeline_id: str, body: PipelineSettingsBody) -> dict[str, Any]:
        """The pipeline's prefix, owner, manager and manager cadence. The pipeline's owner and admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return update_settings(c, who.actor, pipeline_id, body.model_dump())

        return mutate(request, body, write)

    @app.patch("/api/v2/pipeline-stages/{stage_id}")
    def stage_limit(request: Request, stage_id: str, body: StageLimitBody) -> dict[str, Any]:
        """The stage's Warn-after time in minutes (null for none). The pipeline's owner and admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return set_stage_limit(c, who.actor, stage_id, body.limit_minutes)

        return mutate(request, body, write)
