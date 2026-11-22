# pyright: strict
"""Page relationships and writers; links never grant access to a file's bytes."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from functools import cache
from typing import Any, cast

from . import names, read_cache, task_documents, task_reads
from .store import H, Problem

LINKS_SQL = "SELECT * FROM page_links WHERE page_id=? ORDER BY created_at,id"
TASK_FILE_IDS = (
    "SELECT id FROM bot_files WHERE task_id=? UNION "
    "SELECT page_id FROM page_links WHERE target=? UNION "
    "SELECT substr(item,6) FROM pins WHERE place=? AND item LIKE 'page:%'"
)
WRITERS_SQL = (
    "SELECT actor,count(*) AS versions,max(created_at) AS last_at "
    "FROM bot_file_versions WHERE file_id=? GROUP BY actor ORDER BY last_at DESC,actor"
)


def role_name(role: str, target: str = "") -> str:
    """A link's role as stored: writer, related, an entity's memory, or a task document's name (which may be Memory)."""
    said = role.strip()
    if said.lower() in ("writer", "related") or (said.lower() == "memory" and target.startswith("entity:")):
        return said.lower()
    return task_documents.canonical(said)


def _target(target: str, role: str) -> tuple[str, str]:
    kind, sep, ident = target.partition(":")
    if not sep or not ident or kind not in ("task", "bot", "human", "goal", "page", "entity"):
        raise Problem(
            "validation", "A link target is task:, bot:, human:, goal:, page: or entity: followed by its id", 422
        )
    if kind == "entity":
        if role not in ("related", "memory"):
            raise Problem("validation", "An entity's page is related or its memory", 422)
    elif role not in ("writer", "related") and kind != "task":
        raise Problem("validation", "Document roles require a task target", 422)
    if role == "writer" and kind not in ("bot", "human"):
        raise Problem("validation", "A writer is a bot or a person", 422)
    return kind, ident


def link(c: Any, page_id: str, target: str, role: str, by: str) -> bool:
    role = role_name(role, target)
    _target(target, role)
    if (
        role == "memory"
        and target.startswith("entity:")
        and c.execute(
            "SELECT 1 FROM page_links WHERE target=? AND role='memory' AND page_id<>?", (target, page_id)
        ).fetchone()
    ):
        # One memory page per entity (page_links_memory): find it with the list's linked and role filters.
        raise Problem("conflict", "This entity already has a memory page", 409)
    return (
        c.execute(
            "INSERT OR IGNORE INTO page_links(id,page_id,target,role,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (H.new_id(), page_id, target, role, by, H.now()),
        ).rowcount
        > 0
    )


def unlink(c: Any, page_id: str, target: str, role: str, by: str) -> bool:
    role = role_name(role, target)
    _target(target, role)
    if (
        role == "writer"
        and c.execute(
            "SELECT 1 FROM page_links WHERE page_id=? AND target=? AND role='writer'", (page_id, target)
        ).fetchone()
        and c.execute("SELECT count(*) FROM page_links WHERE page_id=? AND role='writer'", (page_id,)).fetchone()[0]
        <= 1
    ):
        raise Problem("conflict", "A page needs at least one writer", 409)
    return (
        c.execute("DELETE FROM page_links WHERE page_id=? AND target=? AND role=?", (page_id, target, role)).rowcount
        > 0
    )


def pages_for(c: Any, target: str, role: str | None = None) -> list[str]:
    params = [target]
    sql = "SELECT DISTINCT page_id FROM page_links WHERE target=?"
    if role is not None:
        sql += " AND role=?"
        params.append(role_name(role, target))
    return [r[0] for r in c.execute(sql + " ORDER BY page_id", params)]


def prime(c: Any, rows: Iterable[Any]) -> None:
    """Batch neutral link/version inputs; per-reader permission answers stay in this snapshot."""
    ids = sorted({str(r["id"]) for r in rows})
    if not ids:
        return
    links: dict[str, list[Any]] = {fid: [] for fid in ids}
    writers: dict[str, list[Any]] = {fid: [] for fid in ids}
    for part in H.chunks(ids):
        marks = ",".join("?" * len(part))
        for row in c.execute(f"SELECT * FROM page_links WHERE page_id IN ({marks}) ORDER BY created_at,id", part):
            links[row["page_id"]].append(row)
        for row in c.execute(
            f"SELECT file_id,actor,count(*) AS versions,max(created_at) AS last_at FROM bot_file_versions "
            f"WHERE file_id IN ({marks}) GROUP BY file_id,actor ORDER BY last_at DESC,actor",
            part,
        ):
            writers[row["file_id"]].append(row)
    read_cache.prime_rows(c, LINKS_SQL, {(fid,): value for fid, value in links.items()})
    read_cache.prime_rows(c, WRITERS_SQL, {(fid,): value for fid, value in writers.items()})
    targets = {str(r["target"]) for found in links.values() for r in found}
    _prime_targets(c, targets)
    # The entity targets of the whole list: the first reader's check reads them all at once (_entities).
    read_cache.value(c, ("page_link_entity_targets",), _no_ids, shared=True).update(
        t[7:] for t in targets if t.startswith("entity:")
    )
    _prime_approvals(c, (r for found in links.values() for r in found))
    actors = targets | {str(v["actor"]) for found in writers.values() for v in found}
    known: dict[str, str] = cast(Callable[..., dict[str, str]], names.lookup)(c, actors)
    read_cache.prime(c, "SELECT page_link_name FROM names WHERE actor=?", {(a,): known.get(a) for a in actors})


def _prime_targets(c: Any, targets: set[str]) -> None:
    cast(Callable[..., Any], H.prime_tasks)(c, {t[5:] for t in targets if t.startswith("task:")})
    values = read_cache.cache(c)
    for kind, table in (("goal", "goals"), ("page", "bot_files")):
        ids = sorted(t.split(":", 1)[1] for t in targets if t.startswith(kind + ":"))
        if values is not None:
            ids = [i for i in ids if ("one", f"SELECT * FROM {table} WHERE id=?", (i,)) not in values]
        found: dict[str, Any] = {}
        for part in H.chunks(ids):
            for r in c.execute(f"SELECT * FROM {table} WHERE id IN ({','.join('?' * len(part))})", part):
                found[r["id"]] = r
        read_cache.prime(c, f"SELECT * FROM {table} WHERE id=?", {(i,): found.get(i) for i in ids})
        if kind == "page" and found:
            from . import files

            cast(Callable[..., Any], files.prime_visible)(c, list(found.values()))


def _names(c: Any, actors: set[str]) -> dict[str, str]:
    values = read_cache.cache(c)
    if values is not None and all(
        ("one", "SELECT page_link_name FROM names WHERE actor=?", (a,)) in values for a in actors
    ):
        return {
            a: n
            for a in actors
            if (n := values[("one", "SELECT page_link_name FROM names WHERE actor=?", (a,))]) is not None
        }
    return cast(Callable[..., dict[str, str]], names.lookup)(c, actors)


def writers_of(c: Any, page_id: str) -> list[dict[str, Any]]:
    rows: Any = cast(Callable[..., Any], read_cache.rows)(c, WRITERS_SQL, (page_id,))
    known = _names(c, {r["actor"] for r in rows})
    return [
        {
            "actor": r["actor"],
            "name": known.get(r["actor"], r["actor"]),
            "versions": r["versions"],
            "last_at": r["last_at"],
        }
        for r in rows
    ]


def rows_for(c: Any, page_id: str) -> tuple[Any, ...]:
    return cast(Callable[..., tuple[Any, ...]], read_cache.rows)(c, LINKS_SQL, (page_id,))


def _prime_approvals(c: Any, rows: Iterable[Any]) -> None:
    task_documents.prime_approvals(
        c,
        {r["target"][5:] for r in rows if r["role"] not in ("writer", "related") and r["target"].startswith("task:")},
    )


def links_of(c: Any, auth: Any, who: Any, page_id: str) -> list[dict[str, Any]]:
    rows = rows_for(c, page_id)
    targets = {str(r["target"]) for r in rows}
    _prime_targets(c, targets)
    _prime_approvals(c, rows)
    known = _names(c, {t for t in targets if t.startswith(("bot:", "human:"))}) | _entities(c, auth, who, targets)
    out: list[dict[str, Any]] = []
    approvals: dict[str, dict[str, int]] = {}
    for r in rows:
        target = str(r["target"])
        kind, _, ident = target.partition(":")
        name = _target_name(c, auth, who, target, known)
        if name is None:
            continue
        role = str(r["role"])
        link: dict[str, Any] = {
            "target": target,
            "role": role,
            "name": name,
            "by": r["created_by"],
            "at": r["created_at"],
        }
        if kind == "task" and role not in ("writer", "related"):
            if role not in approvals:
                approvals[role] = task_documents.approved_tasks(c, page_id, role)
            if ident in approvals[role]:
                link["approval"] = {"version": approvals[role][ident]}
        out.append(link)
    return out


def _target_name(c: Any, auth: Any, who: Any, target: str, known: dict[str, str]) -> str | None:
    """The name of a link target the viewer may see."""
    kind, _, ident = target.partition(":")
    try:
        if kind == "task":
            task = auth.task(c, who, ident)
            return (f"D-{task['number']} " if task.get("number") else "") + str(task["title"])
        if kind == "bot" and auth.bot_access(c, who, ident)["see"]:
            return known.get(target, ident)
        if kind == "human":
            return known.get(target)
        if kind == "goal":
            from . import goal_routes

            goal: Any = read_cache.one(c, "SELECT * FROM goals WHERE id=?", (ident,))
            if goal and cast(Callable[..., Any], goal_routes.readable_goals)(c, auth, who, [goal]):
                return str(goal["title"])
        if kind == "entity":
            return known.get(target)  # _entities: seen and not merged away
        if kind == "page":
            from . import files

            page: Any = read_cache.one(c, "SELECT * FROM bot_files WHERE id=?", (ident,))
            if (
                page
                and page["state"] != "deleted"
                and cast(Callable[..., bool], files.file_visible)(c, auth, who, page, {})
            ):
                return str(page["title"])
    except Problem as exc:
        if exc.status not in (403, 404):
            raise
    return None


def _no_ids() -> set[str]:
    return set()


def _no_names() -> dict[str, str | None]:
    return {}


def entity_reader(c: Any, auth: Any, who: Any) -> str | None:
    """The actor whose Contacts grants an entity check uses: the request's effective reader (auth.run_viewer: the
    person a shared Assistant run reads as); None for nobody, who sees no entity."""
    viewer = auth.run_viewer(c, who)
    return None if viewer.role == "none" else str(viewer.actor)


def _entities(c: Any, auth: Any, who: Any, targets: Iterable[str]) -> dict[str, str]:
    """{entity:<id>: name} for the entity targets the reader sees under the Contacts read rule. One read a request: the
    first check covers every entity target the snapshot's page list holds (prime) with these, and later ones are
    answered from it. A link kept on a merged-away entity is hidden until unmerge."""
    ids = {t[7:] for t in targets if t.startswith("entity:")}
    if not ids:
        return {}
    from .entities import store as ES
    from .entities import visible as V

    actor = entity_reader(c, auth, who)
    seen = read_cache.value(c, ("page_link_entities", actor), _no_names, shared=True)
    missing = (ids | read_cache.value(c, ("page_link_entity_targets",), _no_ids, shared=True)) - seen.keys()
    if missing:
        found: dict[str, str] = {}
        if actor is not None:
            reader = read_cache.value(c, ("page_link_entity_reader", actor), lambda: V.reader(c, actor), shared=True)
            found = ES.seen_entities(c, reader, sorted(missing))
        seen.update({i: found.get(i) for i in missing})
    return {"entity:" + i: name for i in ids if (name := seen.get(i)) is not None}


def entity_targets(c: Any, auth: Any, who: Any, ref: str, *, stored: bool = False) -> list[str]:
    """The entity:<id> targets a reference names for this reader, most exact first: an id, email or domain, a
    merged-away id answering with its kept entity. With `stored` (an unlink), an entity's own id names its stored
    target first and then its kept entity's, so a link kept on a merged-away entity, or one a merge moved, can be taken
    off by anyone who sees the entity it went into. 404 when none shows, 409 when several match."""
    from .entities import store as ES

    actor = entity_reader(c, auth, who)
    missing = Problem("not_found", "Entity not found", 404)
    if actor is None:
        raise missing
    if stored and c.execute("SELECT 1 FROM entities WHERE id=?", (ref,)).fetchone():
        if not ES.visible_entity(c, actor, ref):
            raise missing
        return list(dict.fromkeys(["entity:" + ref, "entity:" + ES.kept(c, ref)]))
    found = ES.get(c, actor, ref)
    if len(found) > 1:
        raise Problem("ambiguous", f"More than one entity matches {ref}; name one by id", 409)
    if not found:
        raise missing
    return ["entity:" + str(found[0]["id"])]


def may_write(c: Any, auth: Any, who: Any, row: Any) -> bool:
    if who.role == "owner":
        return True
    if who.role not in ("human", "bot"):
        return False
    if who.role == "bot":
        bot = cast(Callable[..., Any], H.bot)(c, who.actor[4:])
        if not bot or bot.get("state") == "archived":
            return False
    from . import pages

    if cast(Callable[..., bool], pages.may_manage)(c, auth, who, row):
        return True
    links: Any = cast(Callable[..., Any], read_cache.rows)(c, LINKS_SQL, (row["id"],))
    if any(r["target"] == who.actor and r["role"] == "writer" for r in links):
        return True
    for r in links:
        if not r["target"].startswith("task:") or r["role"] == "writer":
            continue

        def allowed(task_id: str = r["target"][5:]) -> bool:
            try:
                cast(Callable[..., Any], task_reads.page_rights)(c, auth, who, task_id)
            except Problem as exc:
                if exc.status not in (403, 404, 409):
                    raise
                return False
            return True

        if read_cache.value(
            c,
            ("page_comment", who.actor, who.role, who.attempt_id, getattr(who, "task_actor", ""), r["target"]),
            allowed,
            shared=True,
        ):
            return True
    return False


def filters(
    linked: str = "", role: str = "", writer: str = "", *, check_role_target: bool = False
) -> tuple[list[str], list[str]]:
    """Indexed page-id sets shared by Pages and Library lists (bot_files is aliased f)."""
    where: list[str] = []
    args: list[str] = []
    if linked or role:
        clauses: list[str] = []
        if linked:
            _target(linked, "related")
            clauses.append("target=?")
            args.append(linked)
        if role:
            clauses.append("role=?")
            args.append(role_name(role, linked))
            if check_role_target:
                clauses.append("page_link_target_readable(target)")
        where.append("f.id IN (SELECT page_id FROM page_links WHERE " + " AND ".join(clauses) + ")")
    if writer:
        _target(writer, "writer")
        where.append("f.id IN (SELECT page_id FROM page_links WHERE target=? AND role='writer')")
        args.append(writer)
    return where, args


@contextmanager
def visible_filters(
    c: Any, auth: Any, who: Any, linked: str, role: str, writer: str
) -> Generator[tuple[list[str], list[str]]]:
    """Apply target visibility before limiting, ranking or creating a cursor for a result set."""
    role_only = bool(role and not linked)
    if linked.startswith("entity:"):
        # A reference resolves as a link does: an email or domain, a merged-away id as its kept entity.
        try:
            (linked,) = entity_targets(c, auth, who, linked[7:])
        except Problem as exc:
            if exc.status != 404:
                raise
            yield ["0"], []
            return
    where, args = filters(linked, role, writer, check_role_target=role_only)
    if role_only and role_name(role) != role_name(role, "entity:"):
        # An entity's memory pages: every entity target of that role checked in one read before the rows are scanned.
        _entities(
            c,
            auth,
            who,
            [
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT target FROM page_links WHERE role=? AND target LIKE 'entity:%'",
                    (role_name(role, "entity:"),),
                )
            ],
        )

    @cache
    def readable(target: str) -> bool:
        known = _names(c, {target}) if target.startswith("human:") else _entities(c, auth, who, {target})
        return _target_name(c, auth, who, target, known) is not None

    if any(not readable(target) for target in (linked, writer) if target):
        yield ["0"], []
    elif role_only:
        c.create_function("page_link_target_readable", 1, readable)
        try:
            yield where, args
        finally:
            c.create_function("page_link_target_readable", 1, None)
    else:
        yield where, args
