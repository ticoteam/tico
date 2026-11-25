# pyright: strict
"""Only if (CONTRACT §2): a trigger's live check, read just before its run starts.

An event says something just happened; it cannot say whether something is still true. A trigger's `only_if`
(`{read, at_least: N}` or `{read, none: true}`) counts one list and the run starts only when the rule holds.

`read` is written as an API read (`tasks?stage=Release ready&status=open`, `prs?repo=acme/app&idle_hours=48`), but it is
never sent through the HTTP stack: it names one of SOURCES and that source's filters, and each source is a direct query
of the database with the reader's own visibility, the same `Auth.task_sql` fragment `GET /api/v2/tasks` and
`GET /api/v2/prs` apply. Nothing a source runs writes, refreshes a token or calls out. An unknown source or filter is
refused when the automation is saved (`check`).

It reads with the access of whoever last set what the run does (`set_by`, which backend/automations.py `_save` writes:
a save that changes the check, the steps, the owner or anything else the run does, or makes a version current, binds
it to the editor; a rename or a pause keeps it), never with the owner's: a person who may edit a routine but not see a
private task cannot learn whether it matched, nor have its items sent where they choose. The Assistant never reads one
(`reader`). The run's record keeps the count and the rule, never the items; the items reach only the run's steps, as
`only_if.items`, with the people the reader may not see blanked as in `GET /api/v2/tasks`.

The count is one COUNT query and the items are the first MAX_ITEMS rows, both in one read snapshot. A miss, or a check
that cannot be read (its reader left, a stage or person it names is gone), skips the run with its reason.
"""

import logging
import re
from typing import Any, cast
from urllib.parse import parse_qsl

from .store import H as _H
from .store import Problem

Json = dict[str, Any]
H: Any = _H
log = logging.getLogger(__name__)

KEYS = frozenset({"read", "at_least", "none", "set_by"})
MAX_READ = 500
MAX_AT_LEAST = 1000
MAX_ITEMS = 20
ITEM_TEXT = 500  # what an item keeps of its title, as a find's items are cut
MAX_IDLE_HOURS = 8760  # as GET /api/v2/prs takes it (task_links_routes.MAX_IDLE_HOURS)
# Each source's filters. A filter not here is refused on save, so a misspelled one never widens the count.
SOURCES: dict[str, frozenset[str]] = {
    "tasks": frozenset({"stage", "status", "owner", "requester", "label", "lane", "q"}),
    "prs": frozenset({"repo", "state", "idle_hours"}),
}
TASK_STATUSES = ("open", "done", "closed")
PR_STATES = ("open", "draft", "merged", "closed", "shipped")
TASK_FIELDS = (
    "id",
    "number",
    "title",
    "status",
    "stage_id",
    "owner_id",
    "requester_id",
    "assignee_id",
    "lane",
    "updated_at",
)


class Refused(ValueError):
    """An only_if that cannot be saved; its words say why."""


class Unread(Exception):
    """A saved check that cannot be read now; its words are the run's reason."""


def parse(read: str) -> tuple[str, dict[str, str]]:
    """`read` as (source, filters), refused (Refused) when it is not one of SOURCES with its own filters."""
    path, _, query = read.partition("?")
    if path not in SOURCES:
        raise Refused(f"read is one of {', '.join(sorted(SOURCES))}, with its filters, such as tasks?status=open")
    pairs = parse_qsl(query, keep_blank_values=True)
    params = dict(pairs)
    allowed = SOURCES[path]
    unknown = sorted({k for k, _ in pairs} - allowed)
    if unknown:
        raise Refused(f"{path} has no filter {', '.join(unknown)}; its filters are {', '.join(sorted(allowed))}")
    if len(params) != len(pairs):
        raise Refused("read names each filter once")
    for key, value in params.items():
        if not value.strip() or len(value) > 200:
            raise Refused(f"{key} is a value of at most 200 characters")
    status = params.get("status")
    if status and status != "all" and any(s not in TASK_STATUSES for s in status.split(",")):
        raise Refused("status is open, done, closed or all")
    if params.get("lane") and params["lane"] not in H.TASK_LANES:
        raise Refused("lane is company or product")
    if params.get("state") and params["state"].lower() not in PR_STATES:
        raise Refused(f"state is {', '.join(PR_STATES)}")
    idle = params.get("idle_hours")
    if idle is not None and not (idle.isascii() and idle.isdigit() and int(idle) <= MAX_IDLE_HOURS):
        raise Refused(f"idle_hours is a whole number of hours, up to {MAX_IDLE_HOURS}")
    return path, params


def check(only_if: Any) -> None:
    """`{read, at_least: N}` or `{read, none: true}` (and the `set_by` a save writes), else Refused."""
    if not isinstance(only_if, dict):
        raise Refused("is {read, at_least} or {read, none}")
    given = cast(Json, only_if)
    if not set(given) <= KEYS:
        raise Refused("is {read, at_least} or {read, none}")
    read = given.get("read")
    if not isinstance(read, str) or not 0 < len(read) <= MAX_READ:
        raise Refused(f"read is at most {MAX_READ} characters, such as tasks?status=open")
    parse(read)
    least, none = given.get("at_least"), given.get("none")
    if (least is None) == (none is None):
        raise Refused("has exactly one of at_least or none")
    if least is not None and not (type(least) is int and 1 <= least <= MAX_AT_LEAST):
        raise Refused(f"at_least is a whole number from 1 to {MAX_AT_LEAST}")
    if least is None and none is not True:
        raise Refused("none is true")
    set_by = given.get("set_by")
    if set_by is not None and not isinstance(set_by, str):
        raise Refused("set_by is who set it")


def rule(only_if: Json | None) -> Json:
    """The check as its author wrote it: `set_by` is the server's."""
    return {k: v for k, v in (only_if or {}).items() if k != "set_by"}


def holds(only_if: Json, count: int) -> bool:
    return count == 0 if only_if.get("none") else count >= int(only_if["at_least"])


def reader(c: Any, auth: Any, actor: str) -> Any:
    """The identity a check reads as: the person or agent who set it. One who left, or an agent no longer on the team,
    reads as nobody, and the check fails. So does the Assistant: it serves everyone, and outside a turn for one of them
    it has nobody's private tasks to read by (Auth.task_sql)."""
    from . import auth as _auth

    auth_module: Any = _auth
    if actor == H.assistant_actor(c):
        raise Unread("the Assistant reads for whoever it is helping: set it again yourself")
    if H.is_bot(actor):
        found = H.resolve_bot(c, actor)
        if not found:
            raise Unread("the agent that set it is not on the team")
        return auth_module.bot_identity(c, H.bot_actor(found["id"]))
    if H.is_human(actor):
        return auth.identity_for_actor(c, actor)
    raise Unread("nobody set it to read as")


def _person(c: Any, known: Any, params: dict[str, str], key: str) -> str | None:
    """The person or agent a filter names. One the reader may not see reads exactly as one not on the roster, so the
    reason never tells them apart."""
    if key not in params:
        return None
    found = H.resolve_actor(c, params[key])
    if not found or str(found) in known.people:
        raise Unread(f"{key} {params[key]} is not on the roster")
    return str(found)


def _tasks(c: Any, known: Any, visible: str, params: dict[str, str]) -> tuple[int, list[Json]]:
    """Tasks as `GET /api/v2/tasks` selects them for this reader (snoozed ones left out, as there, unless asked for by
    number), the people on them it may not see blanked as there (teammate_access.Known)."""
    from . import task_list_routes

    stages: Any = task_list_routes
    match: list[tuple[str, tuple[Any, ...]]] = []
    if "stage" in params:
        ids = cast("list[str]", stages.stage_ids(c, params["stage"]))
        if not ids:
            raise Unread(f"there is no stage {params['stage']}")
        match.append((f"tasks.stage_id IN ({','.join('?' * len(ids))})", tuple(ids)))
    status, q = params.get("status"), params.get("q")
    by_number = bool(q and re.fullmatch(r"#\d+", q.strip()))
    selection: Json = {
        "owner": _person(c, known, params, "owner"),
        "requester": _person(c, known, params, "requester"),
        "status": status.split(",") if status and status != "all" else None,
        "lane": params.get("lane"),
        "label": params.get("label"),
        "q": q,
        "snoozed": None if status == "all" or by_number else "hide",
        "visible": visible,
        "match": match,
    }
    # One grouped COUNT over the whole selection, loading no row: the count H.tasks gives a board's stages.
    count = sum(int(n) for n in cast("dict[Any, Any]", H.tasks(c, per_stage=True, **selection)).values())
    rows = cast("list[Any]", H.tasks(c, limit=MAX_ITEMS, **selection)) if count else []
    shown = cast("list[Json]", known.tasks([dict(row) for row in rows]))
    return count, [{k: row.get(k) for k in TASK_FIELDS} for row in shown]


def _prs(c: Any, visible: str, params: dict[str, str]) -> tuple[int, list[Json]]:
    """Pull requests as `GET /api/v2/prs` selects them for this reader: one COUNT, then the first MAX_ITEMS rows."""
    from . import task_links_routes

    links: Any = task_links_routes
    idle = params.get("idle_hours")
    selection: Json = {
        "repo": params.get("repo"),
        "state": params.get("state"),
        "idle_hours": int(idle) if idle is not None else None,
    }
    count = int(links.pr_count(c, visible, **selection))
    rows = cast("list[Json]", links.pr_rows(c, visible, limit=MAX_ITEMS, **selection)) if count else []
    return count, rows


def evaluate(app: Any, only_if: Json) -> tuple[Json, list[Json]]:
    """The check: its record ({read, count, ok} and, when the run is skipped, `reason`) and the items the steps read."""
    read = str(only_if["read"])
    out: Json = {"read": read, "count": 0, "ok": False}
    items: list[Json] = []
    try:
        if app is None:
            raise Unread("the server is stopping")
        source, params = parse(read)
        auth: Any = app.state.auth
        with app.state.store.read_transaction() as c:  # one snapshot: the count and the items agree
            auth.sync_access(c)  # the owner and admins as stored now: a background run has had no request to adopt them
            who = reader(c, auth, str(only_if.get("set_by") or ""))
            visible = str(auth.task_sql(c, who))
            if source == "tasks":
                from . import teammate_access

                known: Any = cast(Any, teammate_access).Known(c, who, auth)
                count, items = _tasks(c, known, visible, params)
            else:
                count, items = _prs(c, visible, params)
    except (Unread, Refused) as e:
        out["reason"] = f"Only if check failed: {e}"[:300]
        return out, []
    except Problem as e:
        out["reason"] = f"Only if check failed: {getattr(e, 'detail', e)}"[:300]
        return out, []
    except Exception as e:  # noqa: BLE001 - a check that cannot be read fails closed, with its reason
        log.warning("only_if read %s failed", read, exc_info=True)
        out["reason"] = f"Only if check failed: {str(e) or type(e).__name__}"[:300]
        return out, []
    for item in items:
        if isinstance(item.get("title"), str):
            item["title"] = str(item["title"])[:ITEM_TEXT]
    out.update(count=count, ok=holds(only_if, count))
    if not out["ok"]:
        out["reason"] = (
            f"Only if: {read} is not empty"
            if only_if.get("none")
            else f"Only if: {read} has {count}, needs at least {only_if['at_least']}"
        )[:300]
    return out, items
