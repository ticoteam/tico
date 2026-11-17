# pyright: strict
"""The Floor (Company's first card): every factory the reader may see, its live pipelines, and each work stage's open
tasks by who holds them now (GET /api/v2/company/floor).

A whole-company read in a fixed handful of grouped queries, whatever the number of factories, pipelines or stages:
the factories, the pipelines, their work stages, the open tasks grouped by stage and holder, and the visits that
entered a pipeline or reached Done in the last week. A stage lists at most OWNERS_MAX holders, the most loaded first;
the rest are summed in `more`.

- `count`: open tasks in the stage the reader may read (the task access filter every count uses).
- `owners`: those grouped by the task's holder (`owner_id`, the `owner` filter of GET /v2/tasks). A task nobody holds,
  or held by a person the reader may not be told about (teammate_access.Known), counts in `count` and is in no group.
- `needs_you`: of those, the ones in the reader's own Needs you (the asks, tasks and @s a stage's Flow walks).
- `manager_id`: the pipeline's manager, which a factory with no manager of its own is drawn under.
- `in`, `out`: tasks that entered the pipeline (a first visit, `entered_how='new'`, or an open one moved in from another
  pipeline) and tasks that reached a Done stage (not one moved there already done), in the last 24 hours (`day`) and 7
  days (`week`), each task once per window.

Retired factories and pipelines are left out. A private pipeline is left out unless the reader may read its tasks:
its manager, its owner, the company's person (private_pipelines.readers), or a reader of one of its open tasks. A
factory left with no pipeline the reader sees is left out, except one the reader manages. Pipelines in no factory are
drawn last in one hall, "Other" (`id` and `manager_id` null), when one of them holds an open task the reader may read.
"""

import json
from typing import Any

from . import hubdb as _hubdb
from . import teammate_access as _teammate_access
from . import views as _views

H: Any = _hubdb
TA: Any = _teammate_access
V: Any = _views

OWNERS_MAX = 12  # holders listed per stage; the rest are summed in `more`
# A visit that brought a task into the pipeline (`in`), and one that finished it there (`out`). A task moved from another
# pipeline (task_engine.refile) comes in when it is open; one moved already done was not finished here.
CAME_IN = "(v.entered_how='new' OR (v.entered_how='moved' AND s.kind='work'))"
DONE_HERE = "(s.kind='done' AND v.entered_how<>'moved')"


def _company_person(c: Any) -> str | None:
    """The person every private pipeline adds to its tasks' readers (private_pipelines.readers)."""
    from . import waiting_on

    person = waiting_on.company_person(c)
    return str(person) if person else None


def _needs_task_ids(c: Any, auth: Any, who: Any) -> list[str]:
    """The tasks the reader's Needs you holds in a stage (views.NEEDS_STAGE_KINDS), each once."""
    items: list[dict[str, Any]] = V.needs_items(c, auth, who, None, count_only=True)
    return sorted({str(it["task_id"]) for it in items if it.get("kind") in V.NEEDS_STAGE_KINDS and it.get("task_id")})


def _rates(c: Any, pipeline_ids: list[str], visible_sql: str) -> dict[str, dict[str, dict[str, int]]]:
    """{pipeline: {"in": {day, week}, "out": {day, week}}} from the visits of the last 7 days (visits_entered)."""
    now = H.now()
    day, week = H.shift(now, days=-1), H.shift(now, days=-7)
    out: dict[str, dict[str, dict[str, int]]] = {
        pid: {"in": {"day": 0, "week": 0}, "out": {"day": 0, "week": 0}} for pid in pipeline_ids
    }
    if not pipeline_ids:
        return out
    rows = c.execute(
        "SELECT s.pipeline_id, "
        f"COUNT(DISTINCT CASE WHEN {CAME_IN} AND v.entered_at>=? THEN v.task_id END), "
        f"COUNT(DISTINCT CASE WHEN {CAME_IN} THEN v.task_id END), "
        f"COUNT(DISTINCT CASE WHEN {DONE_HERE} AND v.entered_at>=? THEN v.task_id END), "
        f"COUNT(DISTINCT CASE WHEN {DONE_HERE} THEN v.task_id END) "
        "FROM visits v JOIN stages s ON s.id=v.stage_id "
        f"WHERE v.entered_at>=? AND ({CAME_IN} OR {DONE_HERE}) "
        "AND s.pipeline_id IN (SELECT value FROM json_each(?)) "
        f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=v.task_id AND ({visible_sql})) "
        "GROUP BY s.pipeline_id",
        (day, day, week, json.dumps(pipeline_ids)),
    ).fetchall()
    for pid, in_day, in_week, out_day, out_week in rows:
        if pid in out:
            out[pid] = {"in": {"day": in_day, "week": in_week}, "out": {"day": out_day, "week": out_week}}
    return out


def _holders(c: Any, visible_sql: str, needs: list[str]) -> dict[str, list[tuple[str | None, int, int]]]:
    """{stage: [(holder, open tasks, of them in Needs you)]} over the open pipeline tasks the reader may read."""
    by_stage: dict[str, list[tuple[str | None, int, int]]] = {}
    for stage_id, owner, count, mine in c.execute(
        "SELECT stage_id, owner_id, count(*), COALESCE(SUM(id IN (SELECT value FROM json_each(?))), 0) FROM tasks "
        f"WHERE status='open' AND pipeline_id IS NOT NULL AND stage_id IS NOT NULL AND ({visible_sql}) "
        "GROUP BY stage_id, owner_id",
        (json.dumps(needs),),
    ):
        by_stage.setdefault(stage_id, []).append((owner, int(count), int(mine)))
    return by_stage


def _unknown(c: Any, auth: Any, who: Any, held: dict[str, list[tuple[str | None, int, int]]]) -> set[str]:
    """The holders who are people the reader may not be told about (teammate_access.Known), read once for them all."""
    people = {h for rows in held.values() for h, _, _ in rows if h and h.startswith("human:")}
    return set(TA.Known(c, who, auth).unseen_of(sorted(people))) if people else set()


def _stage(row: Any, held: list[tuple[str | None, int, int]], unknown: set[str]) -> dict[str, Any]:
    named = sorted(((w, n, mine) for w, n, mine in held if w and w not in unknown), key=lambda x: (-x[1], str(x[0])))
    shown, rest = named[:OWNERS_MAX], named[OWNERS_MAX:]
    return {
        "id": row[0],
        "name": row[2],
        "count": sum(n for _, n, _ in held),
        "needs_you": sum(mine for _, _, mine in held),
        "owners": [{"actor": who, "count": n, "needs_you": mine} for who, n, mine in shown],
        "more": {"owners": len(rest), "count": sum(n for _, n, _ in rest)},
    }


def floor(c: Any, auth: Any, who: Any) -> dict[str, Any]:
    visible_sql: str = auth.task_sql(c, who)
    me = str(who.actor)
    factories: list[dict[str, Any]] = [
        {"id": r[0], "name": r[1], "manager_id": r[2], "pipelines": []}
        for r in c.execute(
            "SELECT id, name, manager_id FROM factories WHERE retired_at IS NULL ORDER BY position, created_at, id"
        )
    ]
    other: dict[str, Any] = {"id": None, "name": "Other", "manager_id": None, "pipelines": []}
    by_factory = {f["id"]: f for f in factories}
    pipes: list[Any] = [
        r
        for r in c.execute(
            "SELECT id, name, factory_id, private, manager_id, owner_id FROM pipelines "
            "WHERE retired_at IS NULL ORDER BY created_at, id"
        )
        if r[2] is None or r[2] in by_factory
    ]
    stages: dict[str, list[Any]] = {}
    for r in c.execute(
        "SELECT id, pipeline_id, name FROM stages WHERE retired_at IS NULL AND kind='work' ORDER BY pipeline_id, position"
    ):
        stages.setdefault(r[1], []).append(r)
    held = _holders(c, visible_sql, _needs_task_ids(c, auth, who))
    unknown = _unknown(c, auth, who, held)
    person = _company_person(c) if any(p[3] for p in pipes) else None
    shown: list[Any] = []
    other_reads = False
    for p in pipes:
        reads_some = any(held.get(s[0]) for s in stages.get(p[0], []))
        if p[3] and not reads_some and me not in (p[4], p[5], person):
            continue
        shown.append(p)
        other_reads = other_reads or p[2] is None and reads_some
    rates = _rates(c, [p[0] for p in shown], visible_sql)
    for p in shown:
        (other if p[2] is None else by_factory[p[2]])["pipelines"].append(
            {
                "id": p[0],
                "name": p[1],
                "manager_id": p[4],
                **rates[p[0]],
                "stages": [_stage(s, held.get(s[0], []), unknown) for s in stages.get(p[0], [])],
            }
        )
    halls = [f for f in factories if f["pipelines"] or f["manager_id"] == me]
    return {"factories": halls + [other] if other_reads else halls}
