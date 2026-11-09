# pyright: strict
"""Overview > Factories: the flow map. Factories, the pipelines in them, the handoffs between pipelines and where outside
work comes in. Track F owns this module.

`read` is one set-based read: a handful of grouped queries, never one per card. It counts only the cards the reader may
open (the caller's `Auth.task_sql`, as the Tasks board does) and leaves out a members-only pipeline the reader is not in.

Where outside work comes from: a card whose first visit was in a factory's first pipeline, created in the period, and that
no spawn made. Tasks record no source of their own, so the label is read from what they do record: the card's first tag
by name ("Ads", from task_tags), else "Customers" for a guest's card, else "Team ideas" for a card a person made, else
"Bots" for one a bot made. A factory's done count is the cards that entered its output stage in the period.

Each count is one grouped SQL read over the shown pipelines; nothing is capped silently. The label list is capped at
MAX_LABELS per factory, with the rest added up to "Other".
"""

from collections import defaultdict
from datetime import timedelta
from statistics import median
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import hubdb, pipeline_engine, pipeline_manager as PM, pipeline_review, pipeline_rights
from .pipeline_setup import completion_stage
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
LOOP_MEDIAN_LAPS = 3  # a loop shows its median lap time from this many completed laps
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
    output = "output_stage_id" if "output_stage_id" in have else "NULL AS output_stage_id"
    return _rows(
        c,
        f"SELECT id, name, {goal}, {manager}, {output} FROM factories WHERE retired_at IS NULL "
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
    shown = pipeline_rights.visible_pipeline_ids(c, actor, [r["id"] for r in rows])
    return [r for r in rows if str(r["id"]) in shown]


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


def _for_you(c: "sqlite3.Connection", actor: str, ids: list[str], seen: str) -> dict[str, int]:
    """Cards waiting on the reader's review, per shown pipeline. One review queue read for the reader, then only the items
    on cards the reader may open (`seen`), so a hidden card never counts."""
    if not actor.startswith("human:") or not ids or not _columns(c, "stage_requirements"):
        return {}
    shown = set(ids)
    items = [i for i in pipeline_review.queue_for(c, actor) if str(i["pipeline_id"]) in shown]
    if not items:
        return {}
    task_ids = sorted({str(i["task_id"]) for i in items})
    readable = {
        str(r[0]) for r in c.execute(f"SELECT id FROM tasks WHERE ({seen}) AND id IN ({_marks(task_ids)})", task_ids)
    }
    counted: dict[str, int] = defaultdict(int)
    for item in items:
        if str(item["task_id"]) in readable:
            counted[str(item["pipeline_id"])] += 1
    return dict(counted)


def _links(c: "sqlite3.Connection", ids: set[str]) -> list[Row]:
    """The live links between pipelines the reader sees."""
    if not _columns(c, "pipeline_links"):
        return []
    return [
        r
        for r in _rows(
            c, "SELECT id, from_pipeline_id, to_pipeline_id, mode FROM pipeline_links WHERE retired_at IS NULL"
        )
        if r["from_pipeline_id"] in ids and r["to_pipeline_id"] in ids
    ]


def _edges(c: "sqlite3.Connection", ids: list[str], seen: str, since: str) -> list[Row]:
    """Per pair of shown pipelines and link mode: the handoffs in the period and the median minutes each card spent in the
    source pipeline before it (from its first visit there, else its creation). The median is taken in SQL: the middle
    row (or the two middle rows, averaged) of each edge's ranked ages, so one row per edge leaves the database."""
    if not _columns(c, "pipeline_handoffs") or not ids:
        return []
    start = (
        "(SELECT MIN(v.entered_at) FROM stage_visits v WHERE v.task_id=h.source_task_id "
        "AND v.pipeline_id=l.from_pipeline_id AND v.entered_at<=h.created_at)"
        if _columns(c, "stage_visits")
        else "NULL"
    )
    m = _marks(ids)
    sql = (
        "WITH hops AS (SELECT l.from_pipeline_id AS a, l.to_pipeline_id AS b, l.mode AS mode, "
        f"MAX(0, (julianday(h.created_at) - julianday(coalesce({start}, t.created_at))) * 1440.0) AS age "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id=h.pipeline_link_id "
        "LEFT JOIN tasks t ON t.id=h.source_task_id "
        f"WHERE l.retired_at IS NULL AND l.from_pipeline_id IN ({m}) AND l.to_pipeline_id IN ({m}) "
        f"AND h.created_at>=? AND h.returned_at IS NULL AND h.target_task_id IN (SELECT id FROM tasks WHERE ({seen})) "
        f"AND (l.mode<>'spawn' OR h.source_task_id IN (SELECT id FROM tasks WHERE ({seen})))), "
        "ranked AS (SELECT a, b, mode, age, ROW_NUMBER() OVER (PARTITION BY a, b, mode ORDER BY age) AS rn, "
        "COUNT(*) OVER (PARTITION BY a, b, mode) AS n FROM hops) "
        "SELECT a, b, mode, n AS count, AVG(age) AS median_age FROM ranked "
        "WHERE rn IN ((n+1)/2, (n+2)/2) GROUP BY a, b, mode ORDER BY a, b, mode"
    )
    return _rows(c, sql, [*ids, *ids, since])


def _sccs(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Groups of two or more pipelines that feed each other around a cycle (Tarjan's strongly connected components)."""
    graph: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        if a in graph and b in graph and a != b and b not in graph[a]:
            graph[a].append(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1:
                out.append(group)

    for n in nodes:
        if n not in index:
            visit(n)
    return out


def _laps(seq: list[Row]) -> list[tuple[float, Any]]:
    """Completed laps in one card's hops, in time order, as (minutes, the time the lap ended): a lap starts where the card
    leaves a pipeline and ends when it comes back to that pipeline. A hop that does not follow on from the card's last one
    starts a new lap."""
    out: list[tuple[float, Any]] = []
    pos: str | None = None
    first = ""
    began: Any = None
    for h in seq:
        if pos is not None and str(h["a"]) != pos:
            pos = None
        if pos is None:
            first, began = str(h["a"]), H.parse_ts(h["at"])
        pos = str(h["b"])
        ended = H.parse_ts(h["at"])
        if pos == first and began and ended:
            out.append((max(0.0, (ended - began).total_seconds() / 60), ended))
            pos = None
    return out


def _spawn_laps(c: "sqlite3.Connection", members: list[str], seen: str) -> list[tuple[str, str, float, Any]]:
    """The spawn laps among `members` (D64), as (from pipeline, to pipeline, minutes, the time the lap ended). A spawn lap
    starts when the parent leaves its spawn stage (the spawn handoff) and ends when the parent resumes: its first step into
    a stage past the one it waits in (the stage after the spawn stage). The subtask's own return is not recorded, so the
    parent's history is the evidence. Every departure is read, not only those in the period, so a lap that left before it
    still counts when the parent resumes in it; unfinished laps are left out."""
    m = _marks(members)
    rows = _rows(
        c,
        "SELECT l.from_pipeline_id AS a, l.to_pipeline_id AS b, h.created_at AS departed, ("
        " SELECT MIN(e.created_at) FROM history_events e WHERE e.subject_type='task' AND e.kind='step'"
        " AND e.subject_id=h.source_task_id AND e.created_at>=h.created_at"
        " AND e.new IN (SELECT w.id FROM task_steps w WHERE w.type_id=l.from_pipeline_id AND w.position > ("
        "  SELECT MIN(x.position) FROM task_steps x WHERE x.type_id=l.from_pipeline_id AND x.position > s.position))"
        ") AS resumed "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id=h.pipeline_link_id "
        "JOIN task_steps s ON s.id=l.from_pipeline_stage_id "
        f"WHERE l.mode='spawn' AND l.retired_at IS NULL AND h.returned_at IS NULL "
        f"AND l.from_pipeline_id IN ({m}) AND l.to_pipeline_id IN ({m}) "
        f"AND h.source_task_id IN (SELECT id FROM tasks WHERE ({seen})) "
        f"AND h.target_task_id IN (SELECT id FROM tasks WHERE ({seen}))",
        [*members, *members],
    )
    out: list[tuple[str, str, float, Any]] = []
    for r in rows:
        departed = H.parse_ts(r["departed"])
        resumed = H.parse_ts(r["resumed"]) if r["resumed"] else None
        if departed and resumed:
            out.append((str(r["a"]), str(r["b"]), max(0.0, (resumed - departed).total_seconds() / 60), resumed))
    return out


def _loops(c: "sqlite3.Connection", ordered: list[str], links: list[Row], seen: str, since: str) -> list[Row]:
    """Cycles in the links the reader sees, with their completed laps in the period. A spawn link also closes a cycle: the
    parent resumes in its own pipeline (D64). A move lap is one card's hops round a cycle; the hops of a card that came back
    in the period are loaded whole, so a lap that left before the period still counts when it returns in it. A spawn lap
    is read from the parent's departure and resumption (_spawn_laps). The median lap time shows only from LOOP_MEDIAN_LAPS
    laps."""
    edges: list[tuple[str, str]] = []
    for r in links:
        a, b = str(r["from_pipeline_id"]), str(r["to_pipeline_id"])
        edges.append((a, b))
        if str(r["mode"]) == "spawn":
            edges.append((b, a))  # the parent resumes in its own pipeline (D64): that is the way back, no link needed
    groups = _sccs(ordered, edges)
    if not groups or not _columns(c, "pipeline_handoffs"):
        return []
    members = sorted({p for g in groups for p in g})
    m = _marks(members)
    hops = _rows(
        c,
        "SELECT h.target_task_id AS task, l.from_pipeline_id AS a, l.to_pipeline_id AS b, h.created_at AS at "
        "FROM pipeline_handoffs h JOIN pipeline_links l ON l.id=h.pipeline_link_id "
        f"WHERE l.retired_at IS NULL AND l.mode='move' AND l.from_pipeline_id IN ({m}) AND l.to_pipeline_id IN ({m}) "
        "AND h.returned_at IS NULL AND h.target_task_id IN (SELECT target_task_id FROM pipeline_handoffs "
        "WHERE created_at>=? AND returned_at IS NULL) "
        f"AND h.target_task_id IN (SELECT id FROM tasks WHERE ({seen})) "
        "ORDER BY h.created_at, h.id",
        [*members, *members, since],
    )
    by_card: dict[str, list[Row]] = defaultdict(list)
    for h in hops:
        by_card[str(h["task"])].append(h)
    spawned = _spawn_laps(c, members, seen)
    since_at = H.parse_ts(since)
    rank = {pid: i for i, pid in enumerate(ordered)}
    out: list[Row] = []
    for group in sorted(groups, key=lambda g: min(rank.get(p, 0) for p in g)):
        inside = set(group)
        laps: list[float] = []
        for seq in by_card.values():
            laps.extend(
                minutes
                for minutes, ended in _laps([h for h in seq if str(h["a"]) in inside and str(h["b"]) in inside])
                if ended >= since_at
            )
        laps.extend(minutes for a, b, minutes, ended in spawned if a in inside and b in inside and ended >= since_at)
        out.append(
            {
                "pipelines": sorted(group, key=lambda p: rank.get(p, 0)),
                "laps": len(laps),
                "median_lap_minutes": round(median(laps)) if len(laps) >= LOOP_MEDIAN_LAPS else None,
            }
        )
    return out


def _done(c: "sqlite3.Connection", stages: dict[str, str], seen: str, since: str) -> dict[str, int]:
    """Per output stage: the cards that shipped into it in the period (a factory's done count). The candidates are the cards
    that entered the stage in the period, read from the step history, which every move writes, so a pipeline with no stage
    requirements counts too, and the cards of its pipeline marked done in the period (a status write moves no step). A candidate counts only when its shipment (pipeline_engine.shipped_at, the first time it
    shipped) is in the period: a card shipped last month, reopened and cancelled this week is not a done card this week, and
    a Close that lands an unfinished card in the output stage is not one either. A card that re-enters a stage counts once."""
    if not stages:
        return {}
    rows = _rows(
        c,
        "SELECT DISTINCT e.new AS sid, e.subject_id AS tid FROM history_events e "
        f"WHERE e.subject_type='task' AND e.kind='step' AND e.new IN ({_marks(list(stages.values()))}) "
        f"AND e.created_at>=? AND e.subject_id IN (SELECT id FROM tasks WHERE ({seen}))",
        [*stages.values(), since],
    )
    # A card finished by `status=done` writes no step entry (booted Engineering's Done is closed, so it stays where it was),
    # and it shipped all the same: its pipeline's output stage takes it as a candidate too.
    rows += _rows(
        c,
        "SELECT DISTINCT s.id AS sid, e.subject_id AS tid FROM history_events e "
        "JOIN tasks t ON t.id = e.subject_id JOIN task_steps s ON s.type_id = t.type_id "
        f"WHERE e.subject_type='task' AND e.kind='status' AND e.new='done' AND s.id IN ({_marks(list(stages.values()))}) "
        f"AND e.created_at>=? AND t.id IN (SELECT id FROM tasks WHERE ({seen}))",
        [*stages.values(), since],
    )
    shipments = pipeline_engine.shipped_at(c, sorted({str(r["tid"]) for r in rows}))
    counted: dict[str, set[str]] = {}
    for r in rows:
        when = shipments.get(str(r["tid"]))
        if when is not None and when >= since:
            counted.setdefault(str(r["sid"]), set()).add(str(r["tid"]))
    return {sid: len(cards) for sid, cards in counted.items()}


def _label(raw: str) -> str:
    """A tag's name as the map shows it: "ads" and "Ads" are one label, tidied to a short line."""
    text = raw.strip().replace("-", " ").replace("_", " ")[:24]
    return text[:1].upper() + text[1:] or "Other"


def _inputs(c: "sqlite3.Connection", firsts: dict[str, str], seen: str, since: str) -> list[Row]:
    """Outside work: cards whose first visit was in each factory's first pipeline, created in the period, that no spawn
    made, by origin. Counted in SQL over every such card; only the label groups are capped (MAX_LABELS, then "Other")."""
    if not firsts:
        return []
    pids = list(firsts.values())
    spawned = ""
    if _columns(c, "pipeline_handoffs") and _columns(c, "pipeline_links"):
        spawned = (
            "AND tasks.id NOT IN (SELECT h.target_task_id FROM pipeline_handoffs h "
            "JOIN pipeline_links l ON l.id=h.pipeline_link_id WHERE l.mode='spawn')"
        )
    intake = (
        "COALESCE((SELECT v.pipeline_id FROM stage_visits v WHERE v.task_id=tasks.id AND v.direction='initial' "
        "ORDER BY v.entered_at LIMIT 1), tasks.type_id)"
        if _columns(c, "stage_visits")
        else "tasks.type_id"
    )
    tag = (
        "(SELECT tg.label FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tt.task_id=tasks.id "
        "ORDER BY tg.label LIMIT 1)"
        if _columns(c, "task_tags") and _columns(c, "tags")
        else "NULL"
    )
    guest = "tasks.guest" if "guest" in _columns(c, "tasks") else "0"
    origin = (
        f"COALESCE({tag}, CASE WHEN {guest} THEN 'Customers' "
        "WHEN tasks.requester_id LIKE 'human:%' THEN 'Team ideas' ELSE 'Bots' END)"
    )
    rows = _rows(
        c,
        f"SELECT pid, origin, COUNT(*) AS n FROM (SELECT {intake} AS pid, {origin} AS origin FROM tasks "
        f"WHERE ({seen}) AND tasks.created_at>=? {spawned}) WHERE pid IN ({_marks(pids)}) GROUP BY pid, origin",
        [since, *pids],
    )
    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        tally[str(r["pid"])][_label(str(r["origin"]))] += int(r["n"])
    out: list[Row] = []
    for pid in pids:
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
    # Every aggregate reads only the cards the reader may open now: the caller's rule, then the members-only pipelines.
    seen = pipeline_rights.list_sql(c, actor, visible_sql or _own_cards(actor))
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
    chains: dict[str, list[str]] = {}
    for fid in factory_order:
        inside = set(in_factory[fid])
        chain = _order(
            {pid: names[pid] for pid in in_factory[fid]},
            [(a, b) for a, b in pairs if a in inside and b in inside],
            created,
        )
        ordered.extend(chain)
        firsts[fid] = chain[0]
        chains[fid] = chain
    ordered.extend(sorted((pid for pid in ids if not by_id[pid]["factory_id"]), key=lambda i: (names[i].lower(), i)))

    counts = _counts(c, ids, seen, now, since)
    live = _live(c, ids, seen, now)
    waiting = _for_you(c, actor, ids, seen)

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
    # A factory's output stage: its own, else the completion stage of its last pipeline in flow order.
    outputs: dict[str, str] = {}
    for fid in factory_order:
        stage = str(factories[fid].get("output_stage_id") or "")
        if not stage and chains[fid]:
            stage = completion_stage(c, chains[fid][-1]) or ""
        if stage:
            outputs[fid] = stage
    finished = _done(c, outputs, seen, since)
    drawn = [
        {
            "id": fid,
            "name": factories[fid]["name"],
            "goal": factories[fid]["goal"],
            "manager_id": factories[fid]["manager_id"],
            "done": finished.get(outputs.get(fid, ""), 0),
            "overdue": sum(counts[pid]["overdue"] for pid in in_factory[fid]),
            "live": len({bot for pid in in_factory[fid] for bot in live.get(pid, ())}),
        }
        for fid in factory_order
    ]

    edges = [
        {
            "from": str(r["a"]),
            "to": str(r["b"]),
            "mode": str(r["mode"]),
            "count": int(r["count"]),
            "median_age_minutes": round(float(r["median_age"])) if r["median_age"] is not None else None,
        }
        for r in _edges(c, ids, seen, since)
    ][:MAX_EDGES]
    return {
        "factories": drawn,
        "pipelines": pipelines,
        "edges": edges,
        "inputs": _inputs(c, firsts, seen, since),
        "loops": _loops(c, ordered, links, seen, since),
    }


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
            c = cast("sqlite3.Connection", c)
            # A pipeline the reader may not see answers as a missing one does: the same reply, never a hidden pipeline's rules.
            if not pipeline_rights.may_see(c, who.actor, pipeline_id):
                return {"may_edit_rules": False}
            return {"may_edit_rules": pipeline_rights.may_edit_rules(c, who.actor, pipeline_id)}
