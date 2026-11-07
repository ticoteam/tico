# pyright: strict
"""Handoff links between pipelines: a stage that sends its tickets on to another pipeline. `move` hands the ticket
itself over, into the first stage of the target pipeline; `spawn` is stored for the pipeline's manager, which makes a new
ticket there and records the handoff. A stage has at most one live link out (the table's unique index), so a pipeline
never branches. Changing links is changing the pipeline's rules: its owner and the company's Admins, not bots."""

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from . import hubdb, pipeline_engine, pipeline_rights

H: Any = hubdb

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

Row = dict[str, Any]
MODES = ("move", "spawn")
COLUMNS = "id, from_pipeline_id, from_pipeline_stage_id, to_pipeline_id, mode, return_stage_id, created_by, created_at"


def _rows(conn: sqlite3.Connection, sql: str, args: "tuple[Any, ...]" = ()) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [str(d[0]) for d in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


def link_for_stage(conn: sqlite3.Connection, pipeline_stage_id: str) -> Row | None:
    """The live link that leaves this stage, or None."""
    found = _rows(
        conn,
        f"SELECT {COLUMNS} FROM pipeline_links WHERE from_pipeline_stage_id = ? AND retired_at IS NULL",
        (pipeline_stage_id,),
    )
    return found[0] if found else None


def links(conn: sqlite3.Connection, pipeline_id: str | None = None) -> list[Row]:
    """The live links, optionally those leaving or entering one pipeline."""
    sql = f"SELECT {COLUMNS} FROM pipeline_links WHERE retired_at IS NULL"
    args: tuple[Any, ...] = ()
    if pipeline_id:
        sql += " AND (from_pipeline_id = ? OR to_pipeline_id = ?)"
        args = (pipeline_id, pipeline_id)
    return _rows(conn, sql + " ORDER BY created_at, id", args)


def create_link(
    conn: sqlite3.Connection,
    actor: str,
    from_stage_id: str,
    to_pipeline_id: str,
    mode: str = "move",
    return_stage_id: str | None = None,
) -> Row:
    """Link a stage to another pipeline. Refused (rule "link"): a mode that is not move or spawn, a stage or pipeline that
    is not there, a link to its own pipeline, a stage that already has a live link out (that would branch), a target with no
    stage to enter, and anyone who may not edit the from-pipeline's rules."""
    if mode not in MODES:
        H.refuse(conn, actor, "link", "A link moves a ticket or spawns one")
    stage = pipeline_engine.stage(conn, from_stage_id)
    if stage is None:
        H.refuse(conn, actor, "link", "No such stage")
        raise AssertionError("refuse raises")
    if not _rows(conn, "SELECT id FROM task_types WHERE id = ?", (to_pipeline_id,)):
        H.refuse(conn, actor, "link", "No such pipeline")
    if not pipeline_rights.may_edit_rules(conn, actor, str(stage["type_id"])):
        H.refuse(conn, actor, "link", "Only the pipeline's owner or an Admin changes its links")
    if stage["type_id"] == to_pipeline_id:
        H.refuse(conn, actor, "link", "A pipeline can't link to itself")
    if stage["status"] in pipeline_engine.CLOSED:
        H.refuse(conn, actor, "link", "A closing stage doesn't hand tickets on")
    if link_for_stage(conn, from_stage_id) is not None:
        H.refuse(conn, actor, "link", f"{stage['name']} already links onward; retire that link first (no branches)")
    if pipeline_engine.first_stage(conn, to_pipeline_id) is None:
        H.refuse(conn, actor, "link", "The other pipeline has no stage to enter")
    if return_stage_id:
        back = pipeline_engine.stage(conn, return_stage_id)
        if back is None or back["type_id"] != stage["type_id"]:
            H.refuse(conn, actor, "link", "The return stage is one of this pipeline's")
    link_id = cast(str, H.new_id())
    conn.execute(
        "INSERT INTO pipeline_links (id, from_pipeline_id, from_pipeline_stage_id, to_pipeline_id, mode, "
        "return_stage_id, created_by, created_at, retired_at) VALUES (?,?,?,?,?,?,?,?,NULL)",
        (link_id, stage["type_id"], from_stage_id, to_pipeline_id, mode, return_stage_id, actor, H.now()),
    )
    H.event(conn, actor, "pipeline_link.create", link_id, {"from": from_stage_id, "to": to_pipeline_id, "mode": mode})
    return _rows(conn, f"SELECT {COLUMNS} FROM pipeline_links WHERE id = ?", (link_id,))[0]


def retire_link(conn: sqlite3.Connection, actor: str, link_id: str) -> Row:
    """Take a link out of use (its handoffs stay on record)."""
    found = _rows(conn, f"SELECT {COLUMNS} FROM pipeline_links WHERE id = ? AND retired_at IS NULL", (link_id,))
    if not found:
        H.refuse(conn, actor, "link", "No such link")
        raise AssertionError("refuse raises")
    link = found[0]
    if not pipeline_rights.may_edit_rules(conn, actor, str(link["from_pipeline_id"])):
        H.refuse(conn, actor, "link", "Only the pipeline's owner or an Admin changes its links")
    conn.execute("UPDATE pipeline_links SET retired_at = ? WHERE id = ?", (H.now(), link_id))
    H.event(conn, actor, "pipeline_link.retire", link_id, {"from": link["from_pipeline_stage_id"]})
    return link


def record_handoff(conn: sqlite3.Connection, link_id: str, source_task_id: str, target_task_id: str) -> str:
    """One row for a ticket sent through a link: the same ticket for a `move`, the new one for a `spawn`."""
    ident = cast(str, H.new_id())
    conn.execute(
        "INSERT INTO pipeline_handoffs (id, pipeline_link_id, source_task_id, target_task_id, created_at, returned_at) "
        "VALUES (?,?,?,?,?,NULL)",
        (ident, link_id, source_task_id, target_task_id, H.now()),
    )
    return ident


MAX_CARRIED = 10  # the source's links carried to a spawned ticket, the spec page first


def _label(conn: sqlite3.Connection, task_id: str) -> str:
    """The ticket's id as people read it (prefix and number, D41), else its id."""
    found = _rows(
        conn,
        "SELECT k.number AS number, t.prefix AS prefix FROM tasks k JOIN task_types t ON t.id = k.type_id WHERE k.id = ?",
        (task_id,),
    )
    if found and found[0]["prefix"] and found[0]["number"]:
        return f"{found[0]['prefix']}{found[0]['number']}"
    return task_id


def _owner_for(conn: sqlite3.Connection, pipeline_id: str, filer: str) -> str:
    """Who owns a spawned ticket: the target pipeline's manager when it is on the roster. A pipeline with no manager, or
    a manager name the roster does not know, leaves the filer as owner: tasks always have one, and a refusal would file
    nothing at all."""
    found = _rows(conn, "SELECT manager FROM task_types WHERE id = ?", (pipeline_id,))
    slug = str(found[0]["manager"] or "") if found else ""
    if not slug:
        return filer
    candidate = slug if slug.startswith(("bot:", "human:")) else f"bot:{slug}"
    return candidate if H.resolve_actor(conn, candidate) else filer


def _carry_attachments(conn: sqlite3.Connection, filer: str, source_id: str, child_id: str) -> None:
    """The source's attached links (its spec page among them) become links of the spawned ticket. A link the rules refuse
    is skipped; the spawn stands."""
    found = _rows(
        conn,
        "SELECT url FROM requirement_attachments WHERE task_id = ? AND removed_at IS NULL AND url IS NOT NULL "
        "GROUP BY url ORDER BY MIN(created_at), MIN(rowid) LIMIT ?",
        (source_id, MAX_CARRIED),
    )
    for record in found:
        conn.execute("SAVEPOINT pipeline_spawn_link")
        try:
            H.task_link(conn, filer, child_id, str(record["url"]))
        except H.Refused:
            conn.execute("ROLLBACK TO pipeline_spawn_link")
        conn.execute("RELEASE pipeline_spawn_link")


def spawn(conn: sqlite3.Connection, link: Row, source_id: str, *, again: bool = False) -> str | None:
    """File the ticket a `spawn` link makes when `source_id` leaves the linked stage: a subtask of the source, in the
    target pipeline's first stage, titled as the source, with a line pointing back at it and the source's links carried
    over. It is filed as the source's owner (so the owner is the requester). Once per (link, source ticket): while a
    handoff for that pair is live (not returned), nothing is filed again. A returned (undone) handoff allows a new one.
    `again=True` is a deliberate second spawn (Validate's "Not validated"), which skips that check and is recorded as its
    own handoff. A refusal by the rules is logged on the source, and the source moves on alone. Returns the new ticket's
    id, or None when nothing was filed."""
    if link["mode"] != "spawn":
        return None
    if not again:
        filed = _rows(
            conn,
            "SELECT id FROM pipeline_handoffs WHERE pipeline_link_id = ? AND source_task_id = ? "
            "AND returned_at IS NULL LIMIT 1",
            (link["id"], source_id),
        )
        if filed:
            return None
    target = str(link["to_pipeline_id"])
    first = pipeline_engine.first_stage(conn, target)
    source = H.task(conn, source_id)
    if first is None or source is None:
        return None
    filer = str(source["owner_id"])
    body = f"Made from {_label(conn, source_id)}: {source['title']}. Its links are attached."
    conn.execute("SAVEPOINT pipeline_spawn")
    try:
        child = H.task_create(
            conn,
            filer,
            str(source["title"]),
            body,
            _owner_for(conn, target, filer),
            parent_id=source_id,
            type=target,
            step=str(first["id"]),
            lint=False,
            deduplicate=False,
            mover=True,
        )
    except H.Refused as exc:
        conn.execute("ROLLBACK TO pipeline_spawn")
        conn.execute("RELEASE pipeline_spawn")
        H._task_event(conn, source_id, H.KEEPER, "spawn_not_filed", None, target, str(exc.detail or exc)[:500])  # pyright: ignore[reportPrivateUsage]
        return None
    conn.execute("RELEASE pipeline_spawn")
    child_id = str(child["id"])
    _carry_attachments(conn, filer, source_id, child_id)
    record_handoff(conn, str(link["id"]), source_id, child_id)
    return child_id


def handoffs(conn: sqlite3.Connection, link_id: str) -> list[Row]:
    return _rows(
        conn,
        "SELECT id, pipeline_link_id, source_task_id, target_task_id, created_at, returned_at FROM pipeline_handoffs "
        "WHERE pipeline_link_id = ? ORDER BY created_at, id",
        (link_id,),
    )


class PipelineLinkBody(BaseModel):
    from_stage_id: str
    to_pipeline_id: str
    mode: str = "move"
    return_stage_id: str | None = None


class PipelineRetireBody(BaseModel):
    pass


def install(app: "FastAPI", store: "Store", mutate: Callable[..., Any], auth: "Auth") -> None:
    """GET /api/v2/pipeline-links (?pipeline=), POST /api/v2/pipeline-links, POST /api/v2/pipeline-links/{id}/retire."""
    from fastapi import Request

    @app.get("/api/v2/pipeline-links")  # pyright: ignore[reportUntypedFunctionDecorator]
    def list_links(request: Request, pipeline: str | None = None) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        guard: Any = auth
        reader: Any = store
        with reader.read() as read:
            guard.domain(request.state.identity)
            return {"links": links(cast("sqlite3.Connection", read), pipeline)}

    @app.post("/api/v2/pipeline-links")  # pyright: ignore[reportUntypedFunctionDecorator]
    def add_link(request: Request, body: PipelineLinkBody) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            return {
                "link": create_link(
                    c, who.actor, body.from_stage_id, body.to_pipeline_id, body.mode, body.return_stage_id
                )
            }

        return cast("dict[str, Any]", mutate(request, body, work))

    @app.post("/api/v2/pipeline-links/{link_id}/retire")  # pyright: ignore[reportUntypedFunctionDecorator]
    def drop_link(request: Request, link_id: str, body: PipelineRetireBody) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            return {"link": retire_link(c, who.actor, link_id)}

        return cast("dict[str, Any]", mutate(request, body, work))
