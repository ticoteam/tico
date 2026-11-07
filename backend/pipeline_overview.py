# pyright: strict
"""Overview > Factories: the flow map. Factories, the pipelines in them, the handoffs between pipelines and where outside
work comes in. Track F owns this module.

`read` is one set-based read: a handful of grouped queries, never one per card. It counts only the cards the reader may
open (the caller's `Auth.task_sql`, as the Tasks board does) and leaves out a members-only pipeline the reader is not in.

Where outside work comes from: a card made in a factory's first pipeline that no handoff brought. Tasks record no source
of their own, so the label is read from what they do record: the card's first tag ("Ads"), else "Customers" for a
guest's card, else "Team ideas" for a card a person made, else "Bots" for one a bot made.
"""

import json
from collections import defaultdict
from datetime import timedelta
from statistics import median
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import hubdb, pipeline_manager as PM, pipeline_review, pipeline_rights
from .store import Problem

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

H: Any = hubdb
Row = dict[str, Any]

PERIODS = {"1d": 1, "7d": 7, "30d": 30}
MAX_PIPELINES = 50
MAX_EDGES = 200
MAX_LABELS = 5  # input circles per factory; the rest add up to "Other"
INPUT_ROWS = 5000
LIVE_HOURS = 2  # a turn still open after this long is not "live"
FINISHED = ("done", "closed", "declined")


def _rows(c: "sqlite3.Connection", sql: str, args: "tuple[Any, ...] | list[Any]" = ()) -> list[Row]:
    cursor = c.execute(sql, args)
    names = [str(d[0]) for d in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


def _columns(c: "sqlite3.Connection", table: str) -> set[str]:
    return {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})")}


def _marks(values: "list[Any] | tuple[Any, ...]") -> str:
    return ",".join("?" * len(values))


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _stamp(at: Any) -> str:
    return str(at.strftime("%Y-%m-%dT%H:%M:%S.%f")) + "Z"


def _own_cards(actor: str) -> str:
    """The cards a reader may open when the caller gives no rule: the company's, and the ones they are a party to."""
    who = _quote(actor)
    return f"(coalesce(private,1)=0 OR owner_id={who} OR requester_id={who})"


def _may_see(actor: str, row: Row, members: set[str]) -> bool:
    """A members-only pipeline shows to its owner, its manager and its members; any other shows to everyone."""
    if str(row.get("pipeline_visibility") or "company") != "members":
        return True
    return bool(actor) and (actor == row.get("pipeline_owner_id") or actor == row.get("manager") or actor in members)


def _order(names: dict[str, str], edges: list[tuple[str, str]], ties: dict[str, int]) -> list[str]:
    """Ids in flow order: a thing after the things that feed it, ties by `ties` then name. When only a loop is left,
    its earliest member goes next, so the chain still reads left to right."""
    follow: dict[str, set[str]] = {i: set() for i in names}
    feeds: dict[str, int] = dict.fromkeys(names, 0)
    for a, b in edges:
        if a != b and a in names and b in names and b not in follow[a]:
            follow[a].add(b)
            feeds[b] += 1

    def key(i: str) -> tuple[int, str, str]:
        return (ties.get(i, 0), names[i].lower(), i)

    ready = sorted((i for i in names if feeds[i] == 0), key=key)
    out: list[str] = []
    while len(out) < len(names):
        if not ready:
            ready = [min((i for i in names if i not in out), key=key)]
        i = ready.pop(0)
        if i in out:
            continue
        out.append(i)
        for j in follow[i]:
            feeds[j] -= 1
            if feeds[j] <= 0 and j not in out and j not in ready:
                ready.append(j)
        ready.sort(key=key)
    return out


def _factories(c: "sqlite3.Connection") -> list[Row]:
    have = _columns(c, "factories")
    if not have:
        return []
    goal = "goal" if "goal" in have else "NULL AS goal"
    manager = "manager_id" if "manager_id" in have else "NULL AS manager_id"
    return _rows(
        c,
        f"SELECT id, name, {goal}, {manager} FROM factories WHERE retired_at IS NULL "
        "ORDER BY coalesce(position, 1000000), created_at, id",
    )


def _pipelines(c: "sqlite3.Connection", actor: str) -> list[Row]:
    have = _columns(c, "task_types")
    pick = [
        "id",
        "name",
        "goal" if "goal" in have else "NULL AS goal",
        "factory_id" if "factory_id" in have else "NULL AS factory_id",
        "pipeline_owner_id" if "pipeline_owner_id" in have else "NULL AS pipeline_owner_id",
        "manager" if "manager" in have else "NULL AS manager",
        "pipeline_visibility" if "pipeline_visibility" in have else "'company' AS pipeline_visibility",
    ]
    rows = _rows(c, f"SELECT {', '.join(pick)} FROM task_types WHERE id<>'general' ORDER BY created_at, id")
    mine: set[str] = set()
    if _columns(c, "pipeline_members"):
        mine = {str(r[0]) for r in c.execute("SELECT pipeline_id FROM pipeline_members WHERE actor_id=?", (actor,))}
    return [r for r in rows if _may_see(actor, r, {actor} if str(r["id"]) in mine else set())]


def _counts(c: "sqlite3.Connection", ids: list[str], seen: str, now: str, since: str) -> dict[str, Row]:
    """Per pipeline: open cards, overdue cards (late by pipeline_manager.late_tickets, the rule the manager's rail reads) and
    cards done in the period. The open cards are one query; the Late check is the manager's, over the same rows."""
    out: dict[str, Row] = {i: {"cards": 0, "overdue": 0, "done": 0} for i in ids}
    if not ids:
        return out
    open_rows = _rows(
        c,
        f"SELECT id, type_id, step_id, status, created_at FROM tasks WHERE ({seen}) AND type_id IN ({_marks(ids)}) "
        f"AND status NOT IN ({_marks(FINISHED)})",
        [*ids, *FINISHED],
    )
    late = PM.late_tickets(c, open_rows, now)
    for r in open_rows:
        bucket = out[str(r["type_id"])]
        bucket["cards"] += 1
        bucket["overdue"] += 1 if r["id"] in late else 0
    for r in _rows(
        c,
        f"SELECT type_id AS pid, COUNT(*) AS n FROM tasks WHERE ({seen}) AND status='done' AND done_at>=? "
        f"AND type_id IN ({_marks(ids)}) GROUP BY type_id",
        [since, *ids],
    ):
        out[str(r["pid"])]["done"] = int(r["n"] or 0)
    return out


def _live(c: "sqlite3.Connection", ids: list[str], seen: str, now: str) -> dict[str, set[str]]:
    """The bots with a turn running on a card, by the card's pipeline."""
    out: dict[str, set[str]] = defaultdict(set)
    if not ids:
        return out
    oldest = _stamp(H.parse_ts(now) - timedelta(hours=LIVE_HOURS))
    for r in _rows(
        c,
        f"SELECT DISTINCT t.type_id AS pid, a.bot_id AS bot FROM attempts a JOIN (SELECT * FROM tasks WHERE ({seen})) t "
        "ON t.id=a.task_id WHERE a.started_at IS NOT NULL AND a.finished_at IS NULL AND a.settled_at IS NULL "
        f"AND a.created_at>=? AND t.type_id IN ({_marks(ids)})",
        [oldest, *ids],
    ):
        out[str(r["pid"])].add(str(r["bot"]))
    return out


def _for_you(c: "sqlite3.Connection", actor: str, ids: list[str]) -> dict[str, int]:
    """Cards waiting on the reader's review, per pipeline: only a pipeline with requirements can have any."""
    if not actor.startswith("human:") or not ids or not _columns(c, "stage_requirements"):
        return {}
    with_rules = sorted(
        str(r[0])
        for r in c.execute(
            "SELECT DISTINCT pipeline_id FROM stage_requirements WHERE retired_at IS NULL "
            f"AND pipeline_id IN ({_marks(ids)})",
            ids,
        )
    )
    counted = {pid: len(pipeline_review.queue_for(c, actor, pid)) for pid in with_rules}
    return {pid: n for pid, n in counted.items() if n}


def _links(c: "sqlite3.Connection", ids: set[str]) -> list[Row]:
    """The live links between pipelines the reader sees."""
    if not _columns(c, "pipeline_links"):
        return []
    return [
        r
        for r in _rows(c, "SELECT id, from_pipeline_id, to_pipeline_id FROM pipeline_links WHERE retired_at IS NULL")
        if r["from_pipeline_id"] in ids and r["to_pipeline_id"] in ids
    ]


def _handoffs(c: "sqlite3.Connection", seen: str, since: str) -> dict[str, list[float]]:
    """By link, minutes each card the reader may open spent in the source pipeline before a handoff in the period: from
    its first visit there (else its creation) up to the handoff."""
    if not _columns(c, "pipeline_handoffs"):
        return {}
    visits = _columns(c, "stage_visits")
    start = (
        "(SELECT MIN(v.entered_at) FROM stage_visits v WHERE v.task_id=h.source_task_id "
        "AND v.pipeline_id=l.from_pipeline_id AND v.entered_at<=h.created_at)"
        if visits
        else "NULL"
    )
    out: dict[str, list[float]] = defaultdict(list)
    for r in _rows(
        c,
        f"SELECT h.pipeline_link_id AS lid, h.created_at AS at, coalesce({start}, t.created_at) AS entered "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id=h.pipeline_link_id "
        "LEFT JOIN tasks t ON t.id=h.source_task_id "
        f"WHERE h.created_at>=? AND h.returned_at IS NULL AND h.target_task_id IN (SELECT id FROM tasks WHERE ({seen}))",
        [since],
    ):
        a, b = H.parse_ts(r["entered"]), H.parse_ts(r["at"])
        out[str(r["lid"])].append(max(0.0, (b - a).total_seconds() / 60) if a and b else 0.0)
    return out


def _origin(labels: object, requester: object, guest: object) -> str:
    """Where a card came from, from what the task records (module note)."""
    first = ""
    try:
        parsed: object = json.loads(str(labels or "[]"))
        if isinstance(parsed, list) and parsed:
            first = str(parsed[0])  # pyright: ignore[reportUnknownArgumentType]
    except ValueError:
        first = ""
    if first.strip():
        return first.strip().replace("-", " ").replace("_", " ").capitalize()[:24]
    if guest:
        return "Customers"
    if str(requester or "").startswith("human:"):
        return "Team ideas"
    return "Bots"


def _inputs(c: "sqlite3.Connection", firsts: dict[str, str], seen: str, since: str) -> list[Row]:
    """Outside work: cards made in each factory's first pipeline in the period that no handoff brought, by origin."""
    if not firsts:
        return []
    pids = list(firsts.values())
    handed = ""
    if _columns(c, "pipeline_handoffs"):
        handed = (
            "AND id NOT IN (SELECT target_task_id FROM pipeline_handoffs WHERE returned_at IS NULL "
            "UNION SELECT source_task_id FROM pipeline_handoffs WHERE returned_at IS NULL)"
        )
    guest = "guest" if "guest" in _columns(c, "tasks") else "NULL"
    tasks = _rows(
        c,
        f"SELECT type_id AS pid, labels, requester_id AS requester, {guest} AS guest FROM tasks "
        f"WHERE ({seen}) AND created_at>=? AND type_id IN ({_marks(pids)}) {handed} LIMIT {INPUT_ROWS}",
        [since, *pids],
    )
    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in tasks:
        tally[str(t["pid"])][_origin(t["labels"], t["requester"], t["guest"])] += 1
    out: list[Row] = []
    for pid in firsts.values():
        ranked = sorted(tally[pid].items(), key=lambda kv: (-kv[1], kv[0]))
        out.extend({"label": label, "count": n, "to": pid} for label, n in ranked[:MAX_LABELS])
        rest = sum(n for _, n in ranked[MAX_LABELS:])
        if rest:
            out.append({"label": "Other", "count": rest, "to": pid})
    return out


def read(
    c: "sqlite3.Connection", actor: str, period: str = "7d", visible_sql: str | None = None
) -> dict[str, list[Any]]:
    """The map for `actor` over the last `period` (1d, 7d or 30d). `visible_sql` is the reader's rule over a task's own
    columns (Auth.task_sql); without it a reader sees the company's cards and the ones they are a party to."""
    days = PERIODS.get(period)
    if days is None:
        raise Problem("validation", "Period is 1d, 7d or 30d", 422)
    seen = visible_sql or _own_cards(actor)
    now = str(H.now())
    since = _stamp(H.parse_ts(now) - timedelta(days=days))

    shown = _pipelines(c, actor)[:MAX_PIPELINES]
    by_id = {str(p["id"]): p for p in shown}
    ids = list(by_id)
    names = {pid: str(by_id[pid]["name"]) for pid in ids}
    created = {pid: i for i, pid in enumerate(ids)}
    links = _links(c, set(ids))
    pairs = [(str(link["from_pipeline_id"]), str(link["to_pipeline_id"])) for link in links]

    factories = {str(f["id"]): f for f in _factories(c)}
    # A pipeline whose factory is gone stands alone; a factory with no visible pipeline is not drawn.
    for p in shown:
        if p["factory_id"] not in factories:
            p["factory_id"] = None
    in_factory: dict[str, list[str]] = defaultdict(list)
    for pid in ids:
        if by_id[pid]["factory_id"]:
            in_factory[str(by_id[pid]["factory_id"])].append(pid)

    across = [
        (str(by_id[a]["factory_id"]), str(by_id[b]["factory_id"]))
        for a, b in pairs
        if by_id[a]["factory_id"] and by_id[b]["factory_id"]
    ]
    factory_order = _order(
        {fid: str(factories[fid]["name"]) for fid in in_factory}, across, {fid: i for i, fid in enumerate(factories)}
    )
    ordered: list[str] = []
    firsts: dict[str, str] = {}
    for fid in factory_order:
        inside = set(in_factory[fid])
        chain = _order(
            {pid: names[pid] for pid in in_factory[fid]},
            [(a, b) for a, b in pairs if a in inside and b in inside],
            created,
        )
        ordered.extend(chain)
        firsts[fid] = chain[0]
    ordered.extend(sorted((pid for pid in ids if not by_id[pid]["factory_id"]), key=lambda i: (names[i].lower(), i)))

    counts = _counts(c, ids, seen, now, since)
    live = _live(c, ids, seen, now)
    waiting = _for_you(c, actor, ids)

    pipelines = [
        {
            "id": pid,
            "name": names[pid],
            "goal": by_id[pid]["goal"],
            "manager_id": by_id[pid]["manager"],
            "factory_id": by_id[pid]["factory_id"],
            "cards": counts[pid]["cards"],
            "overdue": counts[pid]["overdue"],
            "for_you": waiting.get(pid, 0),
            "live": len(live.get(pid, ())),
        }
        for pid in ordered
    ]
    drawn = [
        {
            "id": fid,
            "name": factories[fid]["name"],
            "goal": factories[fid]["goal"],
            "manager_id": factories[fid]["manager_id"],
            "done": sum(counts[pid]["done"] for pid in in_factory[fid]),
            "overdue": sum(counts[pid]["overdue"] for pid in in_factory[fid]),
            "live": len({bot for pid in in_factory[fid] for bot in live.get(pid, ())}),
        }
        for fid in factory_order
    ]

    handed = _handoffs(c, seen, since)
    moved: dict[tuple[str, str], list[float]] = defaultdict(list)
    for link in links:
        moved[str(link["from_pipeline_id"]), str(link["to_pipeline_id"])].extend(handed.get(str(link["id"]), []))
    edges = [
        {"from": a, "to": b, "count": len(ages), "median_age_minutes": round(median(ages)) if ages else None}
        for (a, b), ages in sorted(moved.items())
    ][:MAX_EDGES]
    return {"factories": drawn, "pipelines": pipelines, "edges": edges, "inputs": _inputs(c, firsts, seen, since)}


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """GET /api/v2/pipelines/overview?period=7d: the flow map for the signed-in reader.
    GET /api/v2/pipelines/{id}/rights: whether the reader may edit the pipeline's rules (the editor's pencil)."""

    @app.get("/api/v2/pipelines/overview")
    def overview(request: Request, period: str = "7d") -> dict[str, list[Any]]:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity
        guard: Any = auth
        reader: Any = store
        with reader.read() as c:
            guard.domain(who)
            return read(cast("sqlite3.Connection", c), who.actor, period, guard.task_sql(c, who))

    @app.get("/api/v2/pipelines/{pipeline_id}/rights")
    def rights(request: Request, pipeline_id: str) -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity
        guard: Any = auth
        reader: Any = store
        with reader.read() as c:
            guard.domain(who)
            return {
                "may_edit_rules": pipeline_rights.may_edit_rules(cast("sqlite3.Connection", c), who.actor, pipeline_id)
            }
