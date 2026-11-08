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


MAX_CARRIED = 10  # the source's links carried to a spawned ticket, the spec page first

# The address this hub is reached at, set by install, so a company file or page is linked by its absolute URL.
hub_origin = ""


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
    """Who owns a spawned ticket, as a new ticket filed at the target's first stage would be owned: the least busy bot of
    that stage's assign pool with a free slot (the allocator's rule, Fix V-11); else the target pipeline's manager when it is
    on the roster; else the filer, since a ticket always has an owner."""
    from . import pipeline_engine, step_flow

    first = pipeline_engine.first_stage(conn, pipeline_id)
    if first is not None:
        step = _one(conn, "SELECT id, type_id, position, flow FROM task_steps WHERE id = ?", (first["id"],))
        flow: dict[str, Any] = step_flow.of(step)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        rule: Any = flow.get("assign") if step else None
        picked = _pool_bot(conn, step, rule) if step and rule else None
        if picked:
            return f"bot:{picked}"
    found = _rows(conn, "SELECT manager FROM task_types WHERE id = ?", (pipeline_id,))
    slug = str(found[0]["manager"] or "") if found else ""
    if not slug:
        return filer
    candidate = slug if slug.startswith(("bot:", "human:")) else f"bot:{slug}"
    return candidate if H.resolve_actor(conn, candidate) else filer


def _pool_bot(conn: sqlite3.Connection, step: Row, rule: Row) -> str | None:
    """The bot the allocator would give a new ticket at this step: the pool bot with the fewest tickets in hand (ties: the
    fewest open tickets, then the slug) among those that are active, onboarded and have a free slot. None when none does."""
    from . import task_allocator

    allocator: Any = task_allocator
    free: list[tuple[int, int, str]] = []
    for slug in rule["pool"]:
        if not allocator._available(conn, slug):  # pyright: ignore[reportPrivateUsage]
            continue
        in_hand = int(allocator.in_hand(conn, slug, step["type_id"], step))
        if in_hand >= int(rule["slots"]):
            continue
        free.append((in_hand, int(allocator._open(conn, slug)), slug))  # pyright: ignore[reportPrivateUsage]
    return min(free)[2] if free else None


def _one(conn: sqlite3.Connection, sql: str, args: "tuple[Any, ...]" = ()) -> Row | None:
    found = _rows(conn, sql, args)
    return found[0] if found else None


def _evidence(conn: sqlite3.Connection, source_id: str, stage_id: str | None) -> list[Row]:
    """The source's attachments that can travel as links, then its ordinary links. Each is a row with `url`, `title`,
    `requirement` and `stage`. An attachment's url is its content address (requirement_attachments.views): a link's URL,
    or a company file's or page's version, made absolute on hub_origin (empty when the hub has none, so it is not
    carried). Attachments come newest first; the spec is moved to the front by _spec_first. Ordinary links (url and
    doc) follow, oldest first."""
    from . import requirement_attachments

    attached = _rows(
        conn,
        "SELECT a.source, a.url, a.blob_id, a.file_id, a.file_version, r.name AS requirement, "
        "r.pipeline_stage_id AS stage FROM requirement_attachments a "
        "JOIN stage_requirements r ON r.id = a.stage_requirement_id "
        "WHERE a.task_id = ? AND a.removed_at IS NULL ORDER BY a.created_at DESC, a.rowid DESC",
        (source_id,),
    )
    carried: list[Row] = []
    for row in attached:
        view = requirement_attachments.views(conn, [row])[0]
        href = str(view.get("href") or "")
        if view.get("source") == "link":
            url, title = href, ""
        else:
            url, title = (hub_origin + href if hub_origin and href else ""), str(view.get("name") or "")
        # The file, blob and version say what the carry must check the new owner may read (_item_readable).
        carried.append(
            {
                "url": url,
                "title": title,
                "requirement": view["requirement"],
                "stage": view["stage"],
                "blob_id": row["blob_id"],
                "file_id": row["file_id"],
                "file_version": row["file_version"],
            }
        )
    ordinary = _rows(
        conn,
        "SELECT url, title, '' AS requirement, NULL AS stage FROM task_links "
        "WHERE task_id = ? AND kind IN ('url', 'doc') ORDER BY created_at, id",
        (source_id,),
    )
    return [*carried, *ordinary]


def _spec_first(rows: list[Row], leaving_stage: str | None) -> list[Row]:
    """`rows` with the spec in front: the first requirement attachment named like a spec (a "Spec approved" approval is
    not the spec, so a "Spec" requirement wins over it), else the newest one on the leaving stage. The rest keep their order."""

    def named(r: Row) -> str:
        return str(r["requirement"]).lower()

    spec = next((r for r in rows if "spec" in named(r) and "approv" not in named(r)), None)
    if spec is None:
        spec = next((r for r in rows if "spec" in named(r)), None)
    if spec is None and leaving_stage:
        spec = next((r for r in rows if r["stage"] == leaving_stage), None)
    if spec is None:
        return rows
    return [spec, *(r for r in rows if r is not spec)]


def _readable_ticket(conn: sqlite3.Connection, owner: str, source: Row) -> bool:
    """Whether the new ticket's owner may read the source ticket: its own privacy, and the pipeline rule (a members-only
    pipeline shows to its members, its owner, its manager and the company owner; the ticket's own owner and requester
    still read it, D67)."""
    hidden = pipeline_rights.hidden_pipeline_ids(conn, owner)
    return H.task_private_readable(conn, owner, source) and not pipeline_rights.left_out(hidden, owner, source)


def _item_readable(conn: sqlite3.Connection, owner: str, row: Row) -> bool:
    """Whether the owner may read an attachment the spawn would carry: the company file's version (and its attempt) or the
    blob behind it. A link has no file to check, so the ticket's readability decides it alone."""
    from . import task_privacy

    if row.get("file_id"):
        versions = _rows(
            conn,
            "SELECT blob_id, attempt_id FROM bot_file_versions WHERE file_id = ? AND version = ?",
            (row["file_id"], row["file_version"]),
        )
        if not versions:
            return False
        for version in versions:
            if version["blob_id"] and not task_privacy.blob_readable(conn, owner, version["blob_id"]):
                return False
            if version["attempt_id"] and not task_privacy.attempt_readable(conn, owner, version["attempt_id"]):
                return False
        return True
    if row.get("blob_id"):
        return bool(task_privacy.blob_readable(conn, owner, row["blob_id"]))
    return True


def _carry(conn: sqlite3.Connection, filer: str, source_id: str, child_id: str, leaving_stage: str | None) -> None:
    """The source's links become links of the spawned ticket through the link API (H.task_link), spec first, at most
    MAX_CARRIED. A link the child's owner may not read is not carried (D67: the pipeline rule, the source's privacy, and
    each file's or blob's readers). A file or blob has no link API for a ticket and is not carried either. Each skip is
    logged on the child as `carry_skipped`, naming only the requirement (never the URL or title)."""
    source = H.task(conn, source_id)
    child = H.task(conn, child_id)
    if source is None or child is None:
        return
    owner = str(child["owner_id"])
    ticket_ok = _readable_ticket(conn, owner, source)
    if not ticket_ok:
        H._task_event(  # pyright: ignore[reportPrivateUsage]
            conn,
            source_id,
            H.KEEPER,
            "spawn_links_skipped",
            None,
            child_id,
            "The new ticket's owner cannot read this one",
        )
    carried: list[str] = []
    for row in _spec_first(_evidence(conn, source_id, leaving_stage), leaving_stage):
        url = str(row["url"] or "").strip()
        if not url or url in carried:
            continue
        if not ticket_ok or not _item_readable(conn, owner, row):
            H._task_event(  # pyright: ignore[reportPrivateUsage]
                conn, child_id, H.KEEPER, "carry_skipped", None, str(row["requirement"] or "") or "A link", ""
            )
            continue
        if len(carried) >= MAX_CARRIED:
            break
        conn.execute("SAVEPOINT pipeline_spawn_link")
        try:
            H.task_link(conn, filer, child_id, url, str(row["title"] or "") or None)
        except H.Refused as exc:
            conn.execute("ROLLBACK TO pipeline_spawn_link")
            H._task_event(  # pyright: ignore[reportPrivateUsage]
                conn, source_id, H.KEEPER, "spawn_link_not_carried", None, child_id, str(exc.detail or exc)[:500]
            )
        finally:
            conn.execute("RELEASE pipeline_spawn_link")
        carried.append(url)


def spawn(
    conn: sqlite3.Connection, link: Row, source_id: str, *, again: bool = False, note: str | None = None
) -> str | None:
    """File the ticket a `spawn` link makes when `source_id` leaves the linked stage: a subtask of the source, in the target
    pipeline's first stage, titled as the source, with a line pointing back at it, owned as the target's first stage
    assigns it (_owner_for) and requested by the source's owner. The source's links are carried over (_carry). Once per
    (link, source ticket): while a handoff for that pair is live (not returned), nothing is filed again. A returned (undone)
    handoff allows a new one. `again=True` is a deliberate second spawn (Validate's "Not validated"), which skips that check
    and is recorded as its own handoff; `note` leads its body as "Follow-up: <note>".

    A ticket the rules refuse to file raises Problem 409 `spawn_refused` with the reason, so the move that made the spawn
    rolls back with it: a card never advances past a stage whose linked work was not filed. Returns the new ticket's id,
    or None when nothing was to be filed (a move back, a link of another mode, a spawn already filed)."""
    from .store import Problem

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
    lead = f"Follow-up: {note}. " if note else ""
    body = f"{lead}Made from {_label(conn, source_id)}: {source['title']}. Its links are attached."
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
        raise Problem("spawn_refused", f"The linked ticket could not be filed: {exc.detail or exc}", 409) from exc
    conn.execute("RELEASE pipeline_spawn")
    child_id = str(child["id"])
    _carry(conn, filer, source_id, child_id, str(link["from_pipeline_stage_id"]))
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

    global hub_origin
    guard: Any = auth  # the Auth class is untyped
    hub_origin = str(guard.settings.public_url or "").rstrip("/")

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
