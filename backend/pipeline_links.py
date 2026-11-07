# pyright: strict
"""Handoff links between pipelines: a stage that sends its tickets on to another pipeline. `move` hands the ticket
itself over, into the first stage of the target pipeline; `spawn` is stored for the pipeline's manager, which makes a new
ticket there and records the handoff. A stage has at most one live link out (the table's unique index), so a pipeline
never branches. Changing links is changing the pipeline's rules: its owner and the company's Admins, not bots."""

import json
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
LINK_LIMIT = 200  # links per page; a page also stays under LINK_BYTES, as the audit list does
LINK_BYTES = 262_144


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
    """The live links, optionally those leaving or entering one pipeline. Unpaged: for code inside the server, not a route."""
    sql = f"SELECT {COLUMNS} FROM pipeline_links WHERE retired_at IS NULL"
    args: tuple[Any, ...] = ()
    if pipeline_id:
        sql += " AND (from_pipeline_id = ? OR to_pipeline_id = ?)"
        args = (pipeline_id, pipeline_id)
    return _rows(conn, sql + " ORDER BY created_at, id", args)


def visible_links(
    conn: sqlite3.Connection,
    actor: str,
    pipeline_id: str | None = None,
    limit: int = LINK_LIMIT,
    after: str | None = None,
) -> dict[str, Any]:
    """One page of the live links the reader may see, oldest first. A link is left out unless the reader may see both
    pipelines it joins: it names the stage on the far side. `after` is the last link's `<created_at>|<id>` from the page
    before; `next` is the cursor for the page after this one, or None."""
    sql = f"SELECT {COLUMNS} FROM pipeline_links WHERE retired_at IS NULL"
    args: list[Any] = []
    if pipeline_id:
        sql += " AND (from_pipeline_id = ? OR to_pipeline_id = ?)"
        args += [pipeline_id, pipeline_id]
    hidden = sorted(pipeline_rights.hidden_pipeline_ids(conn, actor))
    if hidden:
        marks = ",".join("?" * len(hidden))
        sql += f" AND from_pipeline_id NOT IN ({marks}) AND to_pipeline_id NOT IN ({marks})"
        args += [*hidden, *hidden]
    if after:
        at, _, ident = after.partition("|")
        sql += " AND (created_at > ? OR (created_at = ? AND id > ?))"
        args += [at, at, ident]
    limit = max(1, min(int(limit or LINK_LIMIT), LINK_LIMIT))
    found = _rows(conn, sql + " ORDER BY created_at, id LIMIT ?", (*args, limit + 1))
    shown: list[Row] = []
    used = 256
    for row in found[:limit]:
        size = len(json.dumps(row, default=str).encode()) + 1
        if shown and used + size > LINK_BYTES:
            break  # the first link of a page always fits
        used += size
        shown.append(row)
    last = shown[-1] if len(shown) < len(found) else None
    return {"links": shown, "next": f"{last['created_at']}|{last['id']}" if last else None}


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
    def list_links(  # pyright: ignore[reportUnusedFunction]
        request: Request, pipeline: str | None = None, limit: int = LINK_LIMIT, after: str | None = None
    ) -> dict[str, Any]:
        """The live links the caller may see, a page at a time: `next` is the cursor for the page after."""
        who = request.state.identity
        guard: Any = auth
        reader: Any = store
        with reader.read() as read:
            guard.domain(who)
            return visible_links(cast("sqlite3.Connection", read), who.actor, pipeline, limit, after)

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
