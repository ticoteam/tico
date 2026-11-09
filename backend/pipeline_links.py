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

    from .auth import Auth, Identity
    from .store import Store

Row = dict[str, Any]
MODES = ("move", "spawn")
# When a spawn files: as its card leaves the stage (the default, every spawn so far) or as a card enters it (Lead's Qualified).
FIRES = ("leave", "enter")
COLUMNS = "id, from_pipeline_id, from_pipeline_stage_id, to_pipeline_id, mode, return_stage_id, created_by, created_at"
LINK_LIMIT = 200  # links per page; a page also stays under LINK_BYTES, as the audit list does
LINK_BYTES = 262_144


def _rows(conn: sqlite3.Connection, sql: str, args: "tuple[Any, ...]" = ()) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [str(d[0]) for d in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


def link_for_stage(conn: sqlite3.Connection, pipeline_stage_id: str) -> Row | None:
    """The live link that leaves this stage, or None. Its `fires` is included for the engine; it is not in COLUMNS."""
    found = _rows(
        conn,
        f"SELECT {COLUMNS}, fires FROM pipeline_links WHERE from_pipeline_stage_id = ? AND retired_at IS NULL",
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
    fires: str = "leave",
) -> Row:
    """Link a stage to another pipeline. Refused (rule "link"): a mode that is not move or spawn, a stage or pipeline that
    is not there, a link to its own pipeline, a stage that already has a live link out (that would branch), a target with no
    stage to enter, and anyone who may not edit the from-pipeline's rules."""
    if mode not in MODES:
        H.refuse(conn, actor, "link", "A link moves a ticket or spawns one")
    if fires not in FIRES:
        H.refuse(conn, actor, "link", "A link fires when a card leaves its stage or enters it")
    if fires == "enter" and mode != "spawn":
        H.refuse(conn, actor, "link", "Only a spawn link can fire when a card enters its stage")
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
        "return_stage_id, created_by, created_at, retired_at, fires) VALUES (?,?,?,?,?,?,?,?,NULL,?)",
        (link_id, stage["type_id"], from_stage_id, to_pipeline_id, mode, return_stage_id, actor, H.now(), fires),
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


def approval_of(conn: sqlite3.Connection, task_id: str, stage_id: str | None, actor: str) -> tuple[str, dict[str, Any]]:
    """What let a ticket leave `stage_id`, as the hand-off's activity line words it and as its data: the newest met or
    override decision the stage holds (its judge and verdict), else the actor who moved it (no gate)."""
    name = _rows(conn, "SELECT name FROM task_steps WHERE id = ?", (stage_id,)) if stage_id else []
    stage = str(name[0]["name"]) if name else ""
    decided = _rows(
        conn,
        "SELECT judge, judge_actor, verdict FROM stage_decisions WHERE task_id = ? AND pipeline_stage_id = ? "
        "AND verdict IN ('met', 'override') ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id, stage_id),
    )
    if decided:
        judge = str(decided[0]["judge_actor"] or decided[0]["judge"] or "")
        verdict = str(decided[0]["verdict"])
        return f"approved by {judge} ({verdict}) at {stage}", {
            "judge": decided[0]["judge"],
            "judge_actor": decided[0]["judge_actor"],
            "verdict": verdict,
            "stage": stage,
        }
    return f"moved by {actor}", {"judge": None, "judge_actor": None, "verdict": None, "stage": stage, "moved_by": actor}


def record_handoff(
    conn: sqlite3.Connection, link_id: str, source_task_id: str, target_task_id: str, actor: str | None = None
) -> str:
    """One row for a ticket sent through a link: the same ticket for a `move`, the new one for a `spawn`. Each hand-off
    also writes its activity: "handoff" on the source (its new pipeline in `new`), and for a spawn "handoff_from" on the
    target (the source in `old`, the pipeline it came from in `new`). Both carry the hand-off id in their data, so the
    Overview list reads the same line."""
    ident = cast(str, H.new_id())
    link = _rows(
        conn,
        "SELECT from_pipeline_stage_id, from_pipeline_id, to_pipeline_id FROM pipeline_links WHERE id = ?",
        (link_id,),
    )
    conn.execute(
        "INSERT INTO pipeline_handoffs (id, pipeline_link_id, source_task_id, target_task_id, created_at, returned_at) "
        "VALUES (?,?,?,?,?,NULL)",
        (ident, link_id, source_task_id, target_task_id, H.now()),
    )
    if link:
        by = actor or H.KEEPER
        line, data = approval_of(conn, source_task_id, link[0]["from_pipeline_stage_id"], by)
        data = {**data, "handoff_id": ident, "link_id": link_id}
        to = str(link[0]["to_pipeline_id"])
        H._task_event(  # pyright: ignore[reportPrivateUsage]
            conn, source_task_id, by, "handoff", None, to, line, data=data
        )
        if target_task_id != source_task_id:
            H._task_event(  # pyright: ignore[reportPrivateUsage]
                conn,
                target_task_id,
                by,
                "handoff_from",
                source_task_id,
                str(link[0]["from_pipeline_id"]),
                line,
                data=data,
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
    that stage's assign pool with a free slot (the allocator's rule, Fix V-11); else the least busy available pool bot
    over its slots. The pool is the pipeline's role holder (spec A5). A first stage with no assign rule has no pool, and the
    role holder would resolve only through task_types.role, which holds a role key, not a bot: no stored record links a
    bot to a role. So when no role holder resolves, the filer owns the ticket. The pipeline's manager is never chosen
    (spec A5: a manager change does not move a Dev ticket), on a stage with a pool or without one."""
    from . import pipeline_engine, step_flow

    first = pipeline_engine.first_stage(conn, pipeline_id)
    if first is not None:
        step = _one(conn, "SELECT id, type_id, position, flow FROM task_steps WHERE id = ?", (first["id"],))
        flow: dict[str, Any] = step_flow.of(step)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        rule: Any = flow.get("assign") if step else None
        if step and rule:
            picked = _pool_bot(conn, step, rule) or _pool_bot(conn, step, rule, within_slots=False)
            return f"bot:{picked}" if picked else filer
    return filer


def _pool_bot(conn: sqlite3.Connection, step: Row, rule: Row, *, within_slots: bool = True) -> str | None:
    """The bot the allocator would give a new ticket at this step: the pool bot with the fewest tickets in hand (ties: the
    fewest open tickets, then the slug) among those that are active, onboarded and, unless `within_slots` is False, have a
    free slot. None when none does."""
    from . import task_allocator

    allocator: Any = task_allocator
    free: list[tuple[int, int, str]] = []
    for slug in rule["pool"]:
        if not allocator._available(conn, slug):  # pyright: ignore[reportPrivateUsage]
            continue
        in_hand = int(allocator.in_hand(conn, slug, step["type_id"], step))
        if within_slots and in_hand >= int(rule["slots"]):
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


NEUTRAL_TITLE = "New request"  # a spawned ticket's title when the source's content may not be copied (D64, D67)
NEUTRAL_BODY = "Made from a ticket you may not open."


def _members_only(conn: sqlite3.Connection, type_id: object) -> bool:
    found = _one(conn, "SELECT pipeline_visibility FROM task_types WHERE id = ?", (str(type_id or ""),))
    return bool(found) and str(found["pipeline_visibility"] or "company") == "members"


def _shares_source(conn: sqlite3.Connection, owner: str, source: Row, pipeline_id: str) -> bool:
    """Whether everyone who can read a new ticket on `pipeline_id` (owned by `owner`) may read `source`, so its title, label,
    body and links may be copied onto it. A company-wide source with no privacy is read by everyone. A members-only source
    is read by its pipeline's members, so it is shared only onto that same pipeline and only when the owner reads it too. A
    private source's readers are its own list, so it is shared to no one. When unsure, it is not shared."""
    private = H.task_private(conn, source)
    if not private and not _members_only(conn, source["type_id"]):
        return True
    if private:
        return False
    return str(source["type_id"] or "") == pipeline_id and _readable_ticket(conn, owner, source)


def _identity(conn: sqlite3.Connection, actor: str) -> "Identity":
    """The identity the file policy reads `actor` as: a bot, the company owner, or a person."""
    from .auth import Identity

    if actor.startswith("bot:"):
        return Identity(actor, "bot")
    return Identity(actor, "owner" if actor == pipeline_rights.company_owner(conn) else "human")


NO_RIGHTS = (
    "human:no-rights"  # a plain company member with no rights of their own: the reader every open file must admit
)


def _policy_reads(conn: sqlite3.Connection, actor: str, row: Row) -> bool:
    """Whether `actor` may read an attachment's content: a company file must pass the file and page policy (files.file_visible:
    audience, task privacy, scope), its version's blob and attempt must be readable to them, and a page with an audience is not
    carried at all (its other readers cannot be listed here). A blob is checked by its privacy alone. A link has no file, so
    there is nothing here to check. When unsure, it is not readable."""
    from . import files, task_privacy
    from .auth import Auth

    blob_readable: Callable[..., bool] = task_privacy.blob_readable  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    attempt_readable: Callable[..., bool] = task_privacy.attempt_readable  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    file_visible: Callable[..., bool] = files.file_visible  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    restricted_pages: Callable[..., Any] = files.restricted_pages  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    read_policy: Callable[..., Any] = Auth.read_policy  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if row.get("file_id"):
        file = _one(conn, "SELECT * FROM bot_files WHERE id = ?", (row["file_id"],))
        if file is None or row["file_id"] in restricted_pages(conn):
            return False
        versions = _rows(
            conn,
            "SELECT blob_id, attempt_id FROM bot_file_versions WHERE file_id = ? AND version = ?",
            (row["file_id"], row["file_version"]),
        )
        if not versions:
            return False
        for version in versions:
            if version["blob_id"] and not blob_readable(conn, actor, version["blob_id"]):
                return False
            if version["attempt_id"] and not attempt_readable(conn, actor, version["attempt_id"]):
                return False
        return bool(file_visible(conn, read_policy(conn), _identity(conn, actor), file, {}))
    if row.get("blob_id"):
        return blob_readable(conn, actor, row["blob_id"])
    return True


PROVENANCE_LIMIT = 200  # the nodes a provenance walk may reach; past it the item is unsure, so it is not carried


def _pipeline_tasks(conn: sqlite3.Connection, row: Row) -> list[str] | None:
    """The tasks an attachment's provenance sits on, or None when that cannot be established (a lookup that does not resolve,
    or a walk past PROVENANCE_LIMIT nodes). The walk runs to a fixed point over blobs, files, runs (attempts) and tasks: a blob
    leads to its task attachments and to every file version holding it; a file to its task, its `task:` scope, and its versions'
    blobs and runs; a run to the task it ran on; a task ends the walk. A task with no pipeline, or no task at all, adds nothing.

    The budget counts every distinct node, tasks included. Each lookup reads distinct values, at most the budget left plus
    one: a lookup that reaches that cap may have more unread, so the walk is unsure and the item is skipped. A blob shared by
    thousands of tasks costs a handful of statements and is skipped, not walked (it runs under the writer)."""
    tasks: set[str] = set()
    queue: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def reach(kind: str, ident: str) -> bool:
        """Adds a node once; False once the walk is past its budget."""
        node = (kind, ident)
        if node in seen:
            return True
        seen.add(node)
        if len(seen) > PROVENANCE_LIMIT:
            return False
        if kind == "task":
            tasks.add(ident)
        else:
            queue.append(node)
        return True

    def bounded(sql: str, ident: str) -> list[Row] | None:
        """The distinct rows of a lookup, read up to the budget left plus one; None when it reached that cap, since the
        rows it did not read could hold provenance the walk never sees (unsure, so the item is skipped)."""
        cap = PROVENANCE_LIMIT - len(seen) + 1
        found = _rows(conn, f"{sql} LIMIT ?", (ident, cap))
        return None if len(found) >= cap else found

    if row.get("blob_id") and not reach("blob", str(row["blob_id"])):
        return None
    if row.get("file_id") and not reach("file", str(row["file_id"])):
        return None
    while queue:
        kind, ident = queue.pop()
        found: list[tuple[str, str]] = []
        if kind == "blob":
            assets = bounded("SELECT DISTINCT task_id FROM task_assets WHERE blob_id = ?", ident)
            holders = bounded("SELECT DISTINCT file_id FROM bot_file_versions WHERE blob_id = ?", ident)
            if assets is None or holders is None:
                return None
            found += [("task", str(r["task_id"])) for r in assets]
            found += [("file", str(r["file_id"])) for r in holders]
        elif kind == "file":
            file = _one(conn, "SELECT task_id, scope FROM bot_files WHERE id = ?", (ident,))
            if file is None:
                return None
            if file["task_id"]:
                found.append(("task", str(file["task_id"])))
            if str(file["scope"] or "").startswith("task:"):
                found.append(("task", str(file["scope"])[5:]))
            blobs = bounded(
                "SELECT DISTINCT blob_id FROM bot_file_versions WHERE file_id = ? AND blob_id IS NOT NULL", ident
            )
            runs = bounded(
                "SELECT DISTINCT attempt_id FROM bot_file_versions WHERE file_id = ? AND attempt_id IS NOT NULL", ident
            )
            if blobs is None or runs is None:
                return None
            found += [("blob", str(r["blob_id"])) for r in blobs]
            found += [("attempt", str(r["attempt_id"])) for r in runs]
        elif kind == "attempt":
            run = _one(conn, "SELECT task_id FROM attempts WHERE id = ?", (ident,))
            if run is None:
                return None
            if run["task_id"]:
                found.append(("task", str(run["task_id"])))
        for node in found:
            if not reach(*node):
                return None
    return sorted(tasks)


def _on_open_pipelines(conn: sqlite3.Connection, task_ids: list[str], child_type: str, members_child: bool) -> bool:
    """Whether every task sits on no members-only pipeline, or (for a members-only child) on the child's own pipeline. A task
    that cannot be found is unsure, so it is not carried."""
    for task_id in task_ids:
        found = _one(conn, "SELECT type_id FROM tasks WHERE id = ?", (task_id,))
        if found is None:
            return False
        type_id = str(found["type_id"] or "")
        if _members_only(conn, type_id) and not (members_child and type_id == child_type):
            return False
    return True


def _item_readable(conn: sqlite3.Connection, owner: str, child: Row, row: Row) -> bool:
    """Whether an attachment the spawn would carry may go to `child`, which every one of its readers may then read (D64, D67).
    The child's owner must read the item (_policy_reads), as before. Then the child's audience decides:
    - an open child (company-wide, not private) or a private one: the item must be open to a plain member with no rights of
      its own, and sit on no members-only pipeline (a private child's readers are its own list, and an open item suits all).
    - a members-only child: the item must be open to a plain member, and may sit on that same pipeline (its members read it with
      the child); the child's requester, who also reads the child, must read it too.
    A link has no file and is decided by the sharing check alone. Anything not shown to be covered is skipped."""
    if not _policy_reads(conn, owner, row):
        return False
    if not _policy_reads(conn, NO_RIGHTS, row):
        return False
    child_type = str(child["type_id"] or "")
    task_ids = _pipeline_tasks(conn, row)
    if task_ids is None:
        return False
    if H.task_private(conn, child) or not _members_only(conn, child_type):
        return _on_open_pipelines(conn, task_ids, child_type, members_child=False)
    requester = str(child["requester_id"] or "")
    return (
        bool(requester)
        and _policy_reads(conn, requester, row)
        and _on_open_pipelines(conn, task_ids, child_type, members_child=True)
    )


def _carry(conn: sqlite3.Connection, filer: str, source_id: str, child_id: str, leaving_stage: str | None) -> None:
    """The source's links become links of the spawned ticket through the link API (H.task_link), spec first, at most
    MAX_CARRIED. Nothing is carried unless everyone who can read the child may read the source (_shares_source); then each
    file or blob must pass _item_readable, which covers every reader of the child, not only its owner. A file or blob has no link API for a ticket and is not carried
    as one. Each skip is logged on the child as `carry_skipped`, naming only the requirement (never the URL or title); a child
    that gets no content from the source gets one such event naming "A link"."""
    source = H.task(conn, source_id)
    child = H.task(conn, child_id)
    if source is None or child is None:
        return
    owner = str(child["owner_id"])
    if not _shares_source(conn, owner, source, str(child["type_id"] or "")):
        H._task_event(  # pyright: ignore[reportPrivateUsage]
            conn,
            source_id,
            H.KEEPER,
            "spawn_links_skipped",
            None,
            child_id,
            "The new ticket's readers cannot read this one",
        )
        H._task_event(  # pyright: ignore[reportPrivateUsage]
            conn, child_id, H.KEEPER, "carry_skipped", None, "A link", ""
        )
        return
    carried: list[str] = []
    for row in _spec_first(_evidence(conn, source_id, leaving_stage), leaving_stage):
        url = str(row["url"] or "").strip()
        if not url or url in carried:
            continue
        if not _item_readable(conn, owner, child, row):
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
    conn: sqlite3.Connection,
    link: Row,
    source_id: str,
    *,
    again: bool = False,
    note: str | None = None,
    actor: str | None = None,
) -> str | None:
    """File the ticket a `spawn` link makes when `source_id` leaves the linked stage: a subtask of the source, in the target
    pipeline's first stage, titled and labelled as the source when everyone who can read the new ticket may read the source
    (_shares_source), else with neutral text (NEUTRAL_TITLE, NEUTRAL_BODY). It is owned as the target's first stage assigns it
    (_owner_for) and requested by the source's owner. The source's links are carried over (_carry). Once per
    (link, source ticket): while a handoff for that pair is live (not returned), nothing is filed again. A returned (undone)
    handoff allows a new one. `again=True` is a deliberate second spawn (Validate's "Not validated"), which skips that check
    and is recorded as its own handoff; `note` leads its body as "Follow-up: <note>".

    A ticket the rules refuse to file raises Problem 409 `spawn_refused` with the reason, so the move that made the spawn
    rolls back with it: a card never advances past a stage whose linked work was not filed. A first stage with no room
    (D68) raises the stage_full refusal instead, so the card waits at its stage. Returns the new ticket's id, or None when
    nothing was to be filed (a move back, a link of another mode, a spawn already filed)."""
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
    if source is None:
        return None
    # Every working stage of the target is On ice (D69): it takes no linked ticket, so the card waits at its stage, as for
    # a full first stage, until a person opens a stage there. Nothing is dropped, nor filed straight into Done.
    frozen = pipeline_engine.frozen_refusal(conn, target, source_id, "the linked ticket")
    if frozen is not None:
        raise frozen
    if first is None:
        return None
    full = pipeline_engine.full_stage(conn, str(first["id"]), source_id)
    if full is not None:
        # The linked ticket waits for room (D68): the move that would have filed it is refused, so the card stays where it is.
        raise pipeline_engine.room_refusal(
            "stage_full",
            f"{pipeline_engine.full_detail(full)}, so the linked ticket waits",
            source_id,
            str(first["id"]),
        )
    filer = str(source["owner_id"])
    owner = _owner_for(conn, target, filer)
    lead = f"Follow-up: {note}. " if note else ""
    if _shares_source(conn, owner, source, target):
        title = str(source["title"])
        body = f"{lead}Made from {_label(conn, source_id)}: {source['title']}. Its links are attached."
    else:  # the new ticket's readers may not open the source: nothing of it is copied (Codex F2)
        title, body = NEUTRAL_TITLE, f"{lead}{NEUTRAL_BODY}"
    conn.execute("SAVEPOINT pipeline_spawn")
    try:
        child = H.task_create(
            conn,
            filer,
            title,
            body,
            owner,
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
    record_handoff(conn, str(link["id"]), source_id, child_id, actor or filer)
    return child_id


def handoffs(conn: sqlite3.Connection, link_id: str) -> list[Row]:
    return _rows(
        conn,
        "SELECT id, pipeline_link_id, source_task_id, target_task_id, created_at, returned_at FROM pipeline_handoffs "
        "WHERE pipeline_link_id = ? ORDER BY created_at, id",
        (link_id,),
    )


HANDOFF_CARDS = 200  # the cards one board read asks about


def card_handoffs(conn: sqlite3.Connection, reader_sql: str, card_ids: list[str]) -> dict[str, dict[str, Any]]:
    """For each card the reader may open: `chip`, the card it sent to another pipeline by a spawn link (the latest open
    hand-off only; a returned one counts for nothing), and `from`, the card it was spawned from. The chip names the
    pipeline; the card it leads to and that card's stage are given only to a reader who may open it (none otherwise).
    A move is one card, so it has no chip and no From. A card or link that no longer exists gives no chip."""
    if not card_ids:
        return {}
    marks = ",".join("?" * len(card_ids))
    shown = {
        str(r["id"])
        for r in _rows(conn, f"SELECT id FROM tasks WHERE id IN ({marks}) AND ({reader_sql})", tuple(card_ids))
    }
    if not shown:
        return {}
    out: dict[str, dict[str, Any]] = {cid: {"chip": None, "from": None} for cid in shown}

    def may_open(ids: set[str]) -> set[str]:
        if not ids:
            return set()
        at = ",".join("?" * len(ids))
        return {
            str(r["id"]) for r in _rows(conn, f"SELECT id FROM tasks WHERE id IN ({at}) AND ({reader_sql})", tuple(ids))
        }

    sent = _rows(
        conn,
        "SELECT h.id AS handoff_id, h.source_task_id AS source_id, h.target_task_id AS target_id, "
        "l.to_pipeline_id AS to_type, tt.name AS to_name, s.name AS stage_name, "
        "l.from_pipeline_id AS from_type, ft.name AS from_name, src.title AS source_title "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id = h.pipeline_link_id AND l.mode = 'spawn' "
        "JOIN tasks t ON t.id = h.target_task_id "
        "LEFT JOIN task_types tt ON tt.id = l.to_pipeline_id "
        "LEFT JOIN task_types ft ON ft.id = l.from_pipeline_id "
        "LEFT JOIN task_steps s ON s.id = t.step_id "
        "LEFT JOIN tasks src ON src.id = h.source_task_id "
        f"WHERE h.returned_at IS NULL AND h.source_task_id IN ({marks}) ORDER BY h.created_at, h.id",
        tuple(card_ids),
    )
    latest: dict[str, Row] = {str(r["source_id"]): r for r in sent}  # the newest open hand-off per card wins
    open_to = may_open({str(r["target_id"]) for r in latest.values()})
    for cid, r in latest.items():
        if cid not in shown:
            continue
        target = str(r["target_id"])
        can = target in open_to
        out[cid]["chip"] = {
            "handoff_id": r["handoff_id"],
            "to_pipeline_id": r["to_type"],
            "pipeline": r["to_name"],
            "task_id": target if can else None,
            "stage": r["stage_name"] if can else None,
            "readable": can,
        }

    received = _rows(
        conn,
        "SELECT h.id AS handoff_id, h.source_task_id AS source_id, h.target_task_id AS target_id, "
        "fl.name AS from_name, src.title AS source_title "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id = h.pipeline_link_id AND l.mode = 'spawn' "
        "LEFT JOIN task_types fl ON fl.id = l.from_pipeline_id "
        "LEFT JOIN tasks src ON src.id = h.source_task_id "
        f"WHERE h.returned_at IS NULL AND h.target_task_id IN ({marks}) ORDER BY h.created_at, h.id",
        tuple(card_ids),
    )
    came = {str(r["target_id"]): r for r in received}
    open_from = may_open({str(r["source_id"]) for r in came.values()})
    for cid, r in came.items():
        if cid not in shown:
            continue
        source = str(r["source_id"])
        can = source in open_from
        out[cid]["from"] = {
            "handoff_id": r["handoff_id"],
            "pipeline": r["from_name"],
            "task_id": source if can else None,
            "title": r["source_title"] if can else None,
            "readable": can,
        }
    return out


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

    @app.get("/api/v2/pipeline-links/handoffs")  # pyright: ignore[reportUntypedFunctionDecorator]
    def handoffs_of(request: Request, tasks: str = "") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """The chip and From of each card asked for (?tasks=a,b), for the cards the reader may open."""
        who = request.state.identity
        guard: Any = auth
        reader: Any = store
        ids = [t for t in tasks.split(",") if t][:HANDOFF_CARDS]
        with reader.read() as read:
            guard.domain(who)
            c = cast("sqlite3.Connection", read)
            rule = pipeline_rights.list_sql(c, who.task_actor or who.actor, guard.task_sql(c, who))
            return {"cards": card_handoffs(c, rule, ids)}

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
