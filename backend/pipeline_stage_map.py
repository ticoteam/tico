# pyright: strict
"""The Engineering stage map: the Dev tickets board's steps become the seven stages Build, Review 1, Review 2, Merge queue,
Release, Watch 24h and Closed (spec 795, items 1 and 6).

Pipeline setup applies it to the Engineering pipeline on every run, although that pipeline holds a role: the map is the
one stage change a role-held pipeline gets. It applies in full or not at all. A step the map does not name blocks the
whole map and is named under `unresolved`, and nothing moves. Otherwise each card moves to the stage its step maps to
(a card with no step goes to Merge queue), a step that stays keeps its id and its cards, and each step the map merges
into another goes once it is empty. A second run changes nothing.

Routing is kept with the steps, not the names. A flow that points at a removed step is pointed at the step it became,
and the Dev board's routing (backend/step_flow.py DEV) is then written onto each stage it names. Where Review 1 or Review 2
fails, the ticket goes to Build; Build is shared by every fixes step, so the review a ticket came from is not a flow:
backend/step_flow.py `fixes_for` reads it from the step events the ticket recorded."""

import json
from typing import Any, cast

from . import hubdb as H

# The hub's writers and helpers are not typed yet: this module reaches them through Any, and types its own data.
_hub: Any = H

LATE_MINUTES = 240  # the default Warn after of a Dev board's working step (backend/pipeline_engine.py)

STAGES: tuple[tuple[str, str], ...] = (
    ("Build", "open"),
    ("Review 1", "review"),
    ("Review 2", "review"),
    ("Merge queue", "review"),
    ("Release", "review"),
    ("Watch 24h", "review"),
    ("Closed", "closed"),
)
NO_STEP = "Merge queue"  # a card with no current step waits here: the map starts the stage empty

# The thirteen steps the Dev tickets board had, by lower-cased name, and the stage each one's cards go to.
MAP: dict[str, str] = {
    "on deck": "Build",
    "in progress": "Build",
    "blocked": "Build",
    "review 1": "Review 1",
    "review 1 fixes": "Build",
    "review 2": "Review 2",
    "review 2 fixes": "Build",
    "in qa": "Review 2",
    "qa fixes": "Build",
    "pending release": "Release",
    "post release qa": "Watch 24h",
    "done": "Closed",
    "closed": "Closed",
}
_NEW = {name.lower(): name for name, _ in STAGES}


def _key(name: object) -> str:
    return str(name).strip().lower()


def _rows(c: Any, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = c.execute(sql, args)
    names = [str(d[0]) for d in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


def _target(name: str) -> str | None:
    """The stage a step named `name` goes to, or None when the map does not name it."""
    if _key(name) in _NEW:
        return _NEW[_key(name)]
    return MAP.get(_key(name))


def _move_card(
    c: Any, actor: str, card: dict[str, Any], type_id: str, dest: dict[str, Any], origin: str | None
) -> None:
    """One card to another stage: its status stays, the move is a step event, and an open stage visit follows it."""
    from . import pipeline_engine

    ts = H.now()
    c.execute("UPDATE tasks SET step_id = ?, updated_at = ? WHERE id = ?", (dest["id"], ts, card["id"]))
    _hub._task_event(  # pyright: ignore[reportPrivateUsage]
        c, card["id"], actor, "step", origin, dest["id"], "Moved by the Engineering stage map"
    )
    visit = pipeline_engine.open_visit(c, card["id"])
    if visit is not None:
        c.execute("UPDATE stage_visits SET left_at = ? WHERE id = ?", (ts, visit["id"]))
        pipeline_engine._open_visit_at(  # pyright: ignore[reportPrivateUsage]
            c, card["id"], type_id, dest["id"], ts, direction="initial", came_from=origin, actor=actor
        )


def _flow_of(step: dict[str, Any]) -> dict[str, Any]:
    raw: Any = step.get("flow")
    value: Any = _hub._json(raw, {}) if isinstance(raw, str) else (raw or {})  # pyright: ignore[reportPrivateUsage]
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in cast(dict[str, Any], value).items()}


def apply(c: Any, actor: str, row: dict[str, Any], made: list[str], unresolved: list[str]) -> str:
    """Applies the map to the Engineering type `row`. Returns "same" (nothing to do), "applied", or "blocked" (a step the
    map does not name: nothing moved, and it is named under `unresolved`)."""
    from . import pipeline_links, step_flow

    flows_mod: Any = step_flow

    type_id = str(row["id"])
    steps = _rows(
        c,
        "SELECT id, name, status, position, flow, limit_minutes FROM task_steps WHERE type_id = ? ORDER BY position, id",
        (type_id,),
    )
    unknown = [str(s["name"]) for s in steps if _target(str(s["name"])) is None]
    if unknown:
        unresolved.append(
            "Engineering stages were not changed: "
            + ", ".join(unknown)
            + " is not in the stage map; name the stage it moves to, then re-run setup"
        )
        return "blocked"
    no_step = _rows(c, "SELECT id, title FROM tasks WHERE type_id = ? AND step_id IS NULL", (type_id,))
    names = [str(s["name"]) for s in steps]
    if names == [name for name, _ in STAGES] and not no_step:
        return "same"

    # Each new stage keeps the step of its own name, else the first step the map sends to it (by position).
    target = {str(s["id"]): _target(str(s["name"])) or "" for s in steps}
    survivor: dict[str, dict[str, Any] | None] = {}
    for stage, _status in STAGES:
        own = [s for s in steps if s["name"] == stage]
        sent = [s for s in steps if target[str(s["id"])] == stage]
        survivor[stage] = (own or sent or [None])[0]
    kept = {str(s["id"]) for s in survivor.values() if s is not None}
    removed = [s for s in steps if str(s["id"]) not in kept]

    # The rules a person set on a step (wake the Builder on In QA, ...) name the stage they moved to before any step goes.
    from . import rules

    rules_mod: Any = rules

    renamed = {str(s["name"]): target[str(s["id"])] for s in steps if target[str(s["id"])] != s["name"]}
    rules_mod.steps_renamed(c, type_id, renamed)

    # Phase one: the new stages exist and the survivors carry their new names, so every card has a place to go.
    stage_status = dict(STAGES)
    phase_one: list[dict[str, Any]] = []
    for stage, _status in STAGES:
        step = survivor[stage]
        if step is not None:
            phase_one.append({"id": step["id"], "name": stage, "status": step["status"]})
        else:
            phase_one.append({"name": stage, "status": stage_status[stage]})
    phase_one += [{"id": s["id"], "name": s["name"], "status": s["status"]} for s in removed]
    _hub.type_update(c, actor, type_id, steps=phase_one, mover=True)
    fresh = {
        str(s["name"]): s for s in _rows(c, "SELECT id, name, status FROM task_steps WHERE type_id = ?", (type_id,))
    }
    for step in removed:
        for link in pipeline_links.links(c):
            if str(link["from_pipeline_stage_id"]) == str(step["id"]):
                pipeline_links.retire_link(c, actor, str(link["id"]))
        c.execute(
            "UPDATE stage_requirements SET retired_at = ? WHERE pipeline_stage_id = ? AND retired_at IS NULL",
            (H.now(), step["id"]),
        )
        dest = fresh[target[str(step["id"])]]
        for card in _rows(c, "SELECT id FROM tasks WHERE step_id = ?", (step["id"],)):
            _move_card(c, actor, card, type_id, dest, str(step["id"]))
    if no_step:
        landing = fresh[NO_STEP]
        for card in no_step:
            _move_card(c, actor, card, type_id, landing, None)

    # Routing: a flow that named a removed step names the stage it became, and the Dev board's routing (step_flow.DEV)
    # is written onto each stage it names. This runs before the removed steps go, so no flow points at a missing step.
    by_stage = {
        str(r["name"]): r
        for r in _rows(c, "SELECT id, name, status, flow FROM task_steps WHERE type_id = ?", (type_id,))
    }
    gone = {str(s["id"]): by_stage[target[str(s["id"])]]["id"] for s in removed}
    for stage, _status in STAGES:
        step = by_stage[stage]
        flow = _flow_of(step)
        for key in flows_mod.STEP_KEYS:
            if str(flow.get(key)) in gone:
                flow[key] = gone[str(flow[key])]
        flow.update(flows_mod.resolved(flows_mod.DEV.get(stage, {}), by_stage))
        c.execute(
            "UPDATE task_steps SET flow = ? WHERE id = ?",
            (json.dumps(flow, sort_keys=True) if flow else None, step["id"]),
        )

    # Phase two: only the stages the map keeps remain, in the map's order.
    final: list[dict[str, Any]] = []
    for stage, status in STAGES:
        step = survivor[stage]
        if step is not None:
            final.append({"id": step["id"], "name": stage, "status": step["status"]})
        else:
            final.append({"name": stage, "status": status})
    _hub.type_update(c, actor, type_id, steps=final, mover=True)
    # Build is the working step now: it keeps the late clock the Dev board's working steps had (the larger of the limits
    # of the steps that merged into it, else the default), so a started ticket still shows as late.
    into_build = [s for s in steps if target[str(s["id"])] == "Build"]
    limits = [int(s["limit_minutes"]) for s in into_build if s.get("limit_minutes")]
    c.execute(
        "UPDATE task_steps SET limit_minutes = ? WHERE id = ?",
        (max(limits) if limits else LATE_MINUTES, by_stage["Build"]["id"]),
    )
    made.append(f"set the Engineering stages to the stage map ({len(removed)} steps merged or removed)")
    return "applied"
