# pyright: strict
"""The Factory Manager: one manager bot per factory (factories.manager_id) that reads the factory's pipelines every 30 minutes.

A pass is plain code. It finds the judgment items of the factory:

- a pipeline whose manager has stopped (its passes missed, see pipeline_manager.stalled);
- a bottleneck: the stage with the most over-limit cards across the factory, when the same stage is the bottleneck on two
  passes in a row;
- a handoff whose target pipeline has no stages to take the card (no intake).

The bot is woken only for those items, and only when the set of items changed since the previous pass, so the same
bottleneck two passes running wakes it once. A wake updates one open "Factory check" task for the factory. Its allowed
actions are ask_manager, open_botops, propose_rule and post_note (the amendment's list); this pass records ask_manager.
The Factory Manager never moves a card and never marks one met: the only writes are its run log and its check task.
Factories without a manager, and databases without the factories.manager_id column (Track A's migration), are skipped.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from . import hubdb

# hubdb is untyped: its functions are Any here (as in stranded_resume.py), not partially unknown.
H: Any = hubdb
from . import pipeline_manager as PM

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth

LOG = logging.getLogger("tico.factory_manager")
ALLOWED = ("ask_manager", "open_botops", "propose_rule", "post_note")
EMITTED = ("ask_manager",)
EVERY = timedelta(minutes=30)
INTAKE_PERIOD = timedelta(days=7)
WAKE = "factory_manager"
CHECK_PREFIX = "Factory check: "
NOTE_PREFIX = "Factory note: "


def _managed(c: "sqlite3.Connection") -> list[dict[str, Any]]:
    """The live factories with a manager. Empty until Track A's migration adds factories.manager_id."""
    if "manager_id" not in {row[1] for row in c.execute("PRAGMA table_info(factories)")}:
        return []
    return H._rows(
        c.execute(
            "SELECT id, name, owner_id, manager_id, created_at FROM factories "
            "WHERE retired_at IS NULL AND manager_id IS NOT NULL AND manager_id<>'' ORDER BY position, name"
        )
    )


def _actor(manager_id: str) -> str:
    return manager_id if manager_id.startswith("bot:") else "bot:" + manager_id


def _pipelines(c: "sqlite3.Connection", factory_id: str) -> list[dict[str, Any]]:
    return H._rows(
        c.execute(
            "SELECT id, name, manager, manager_every_minutes, created_at FROM task_types WHERE factory_id=? ORDER BY name",
            (factory_id,),
        )
    )


def last_run(c: "sqlite3.Connection", factory_id: str) -> dict[str, Any] | None:
    rows = H._rows(
        c.execute(
            "SELECT * FROM factory_manager_runs WHERE factory_id=? ORDER BY started_at DESC, id DESC LIMIT 1",
            (factory_id,),
        )
    )
    return rows[0] if rows else None


def _bottleneck(c: "sqlite3.Connection", pipelines: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    """The stage with the most over-limit cards across the factory's pipelines, or None when none is over its Limit."""
    counts: dict[str, dict[str, Any]] = {}
    for pipeline in pipelines:
        for item in PM.over_limit(c, pipeline["id"], at):
            stage = item["pipeline_stage_id"]
            entry = counts.setdefault(stage, {"stage_id": stage, "name": item["stage"], "count": 0})
            entry["count"] += 1
    if not counts:
        return None
    return max(counts.values(), key=lambda entry: (entry["count"], entry["stage_id"]))


def _no_intake(c: "sqlite3.Connection", factory_id: str, at: datetime) -> list[dict[str, Any]]:
    """Handoff links out of this factory with no intake in the period: the source pipeline handed cards over in the
    last 7 days, and the target pipeline has had no stage visit entered in that time, so nothing can take them."""
    since = PM.stamp_of(at - INTAKE_PERIOD)
    return H._rows(
        c.execute(
            "SELECT DISTINCT l.id, l.to_pipeline_id, t.name AS to_name FROM pipeline_links l "
            "JOIN task_types t ON t.id = l.to_pipeline_id "
            "JOIN task_types f ON f.id = l.from_pipeline_id "
            "JOIN pipeline_handoffs h ON h.pipeline_link_id = l.id AND h.created_at >= ? "
            "WHERE f.factory_id=? AND l.retired_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM stage_visits v WHERE v.pipeline_id = l.to_pipeline_id AND v.entered_at >= ?)",
            (since, factory_id, since),
        )
    )


def _daily_note(
    c: "sqlite3.Connection", factory: dict[str, Any], run_id: str, bottleneck: dict[str, Any] | None, at: datetime
) -> bool:
    """Post the factory's note for the UTC day before, once per UTC day: plain code, no model call. It is a task owned by
    the factory's owner (a person sees it in their task list; no bot is woken for it), titled "Factory note: <name>
    <day>". Returns whether a note was posted this pass."""
    fid = factory["id"]
    if not factory["owner_id"]:
        return False
    today = datetime(at.year, at.month, at.day, tzinfo=timezone.utc)
    if c.execute(
        "SELECT 1 FROM factory_manager_actions WHERE factory_id=? AND action='post_note' AND created_at>=? LIMIT 1",
        (fid, PM.stamp_of(today)),
    ).fetchone():
        return False
    day = today - timedelta(days=1)
    counts = c.execute(
        "SELECT SUM(woke) AS woke, SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, COUNT(*) AS runs "
        "FROM factory_manager_runs WHERE factory_id=? AND started_at>=? AND started_at<?",
        (fid, PM.stamp_of(day), PM.stamp_of(today)),
    ).fetchone()
    title = f"{NOTE_PREFIX}{factory['name']} {day:%Y-%m-%d}"
    lines = [
        f"{factory['name']} on {day:%Y-%m-%d} (UTC):",
        f"- Factory Manager passes: {counts['runs'] or 0}, failed: {counts['failed'] or 0}, wakes: {counts['woke'] or 0}.",
        f"- Bottleneck at the last pass: {bottleneck['name'] if bottleneck else 'none'}.",
    ]
    H.task_create(
        c,
        H.KEEPER,
        title,
        "\n".join(lines),
        factory["owner_id"],
        deduplicate=False,
        lint=False,
        private=False,
        trigger="schedule",
        trigger_source=WAKE,
    )
    c.execute(
        "INSERT INTO factory_manager_actions (id, factory_id, factory_manager_run_id, action, target_actor, note, "
        "created_at) VALUES (?,?,?,'post_note',?,?,?)",
        (PM.new_id("fma"), fid, run_id, factory["owner_id"], title, PM.stamp_of(at)),
    )
    return True


def _open_check(c: "sqlite3.Connection", factory: dict[str, Any], owner: str, body: str) -> None:
    """Update the factory's one open check task and wake its manager, or open it."""
    title = CHECK_PREFIX + factory["name"]
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    found = c.execute(
        f"SELECT id FROM tasks WHERE owner_id=? AND title=? AND status IN ({marks}) ORDER BY created_at LIMIT 1",
        (owner, title, *H.ACTIVE_STATUSES),
    ).fetchone()
    if found is not None:
        H.task_update(c, H.KEEPER, found[0], body=body)
        H._wake(c, H.task(c, found[0]), owner, body, {"wake": WAKE}, trigger="schedule", source=WAKE)
        return
    H.task_create(
        c,
        H.KEEPER,
        title,
        body,
        owner,
        deduplicate=False,
        lint=False,
        private=False,
        wake_refs={"wake": WAKE},
        trigger="schedule",
        trigger_source=WAKE,
    )


def _pass(c: "sqlite3.Connection", factory: dict[str, Any], at: datetime) -> dict[str, Any]:
    """One Factory Manager pass, inside the caller's transaction."""
    factory_id = factory["id"]
    stamp = PM.stamp_of(at)
    previous = last_run(c, factory_id)  # read before this pass writes its own run row
    run_id = PM.new_id("fmr")
    c.execute(
        "INSERT INTO factory_manager_runs (id, factory_id, started_at, status) VALUES (?,?,?,'ok')",
        (run_id, factory_id, stamp),
    )
    pipelines = _pipelines(c, factory_id)
    dead = [p for p in pipelines if p["manager"] and PM.stalled(c, p, at) >= PM.MISSED_RUNS]
    bottleneck = _bottleneck(c, pipelines, at)
    items: list[str] = [f"manager:{p['id']}" for p in dead]
    if bottleneck is not None and previous is not None and previous["bottleneck_stage_id"] == bottleneck["stage_id"]:
        items.append(f"bottleneck:{bottleneck['stage_id']}")
    no_intake = _no_intake(c, factory_id, at)
    items.extend(f"intake:{link['id']}" for link in no_intake)
    key = ",".join(sorted(items))
    previous_key = (previous or {}).get("wake_key") or ""
    woke = bool(items) and key != previous_key
    if woke:
        lines = [f"Factory check for {factory['name']}:"]
        lines += [
            f"- {p['name']}: its manager has not run; it needs a look (ask it, or open a BotOps task)." for p in dead
        ]
        if bottleneck is not None and f"bottleneck:{bottleneck['stage_id']}" in items:
            lines.append(f"- Bottleneck: {bottleneck['name']} ({bottleneck['count']} over their Limit).")
        lines += [f"- Handoff to {link['to_name']} has no stages to take the card." for link in no_intake]
        owner = _actor(factory["manager_id"])
        _open_check(c, factory, owner, "\n".join(lines))
        c.execute(
            "INSERT INTO factory_manager_actions (id, factory_id, factory_manager_run_id, action, target_actor, note, "
            "created_at) VALUES (?,?,?,'ask_manager',?,?,?)",
            (PM.new_id("fma"), factory_id, run_id, owner, "judgment wake: " + ", ".join(items), stamp),
        )
    c.execute(
        "UPDATE factory_manager_runs SET finished_at=?, bottleneck_stage_id=?, judgment_count=?, wake_key=?, woke=?, "
        "model_called=? WHERE id=?",
        (
            PM.stamp_of(datetime.now(timezone.utc)),
            bottleneck["stage_id"] if bottleneck else None,
            len(items),
            key,
            int(woke),
            int(woke),  # a wake is the one model call a pass can make
            run_id,
        ),
    )
    _daily_note(c, factory, run_id, bottleneck, at)
    return {"items": len(items), "woke": woke}


def tick(store: Any, at: Any) -> None:
    """One pass for each factory with a manager, every 30 minutes. Plain code; the model is woken only for the items."""
    when = PM.when_of(at)
    with store.read() as c:
        due: list[dict[str, Any]] = []
        for factory in _managed(c):
            last = last_run(c, factory["id"])
            started = H.parse_ts(last["started_at"]) if last else None
            if last is None or started is None or when - started >= EVERY:
                due.append(factory)
    for factory in due:
        try:
            with store.transaction() as c:
                _pass(c, factory, when)
        except Exception as exc:  # noqa: BLE001 - recorded as the factory's failed run; the others still run
            LOG.warning("factory manager pass failed for %s: %s", factory["id"], type(exc).__name__)
            with store.transaction() as c:
                c.execute(
                    "INSERT INTO factory_manager_runs (id, factory_id, started_at, finished_at, status, error) "
                    "VALUES (?,?,?,?,'failed',?)",
                    (
                        PM.new_id("fmr"),
                        factory["id"],
                        PM.stamp_of(when),
                        PM.stamp_of(datetime.now(timezone.utc)),
                        type(exc).__name__,
                    ),
                )


def view(c: "sqlite3.Connection", factory_id: str) -> dict[str, Any] | None:
    """The factory's manager and its run log (errors first, then the newest runs and actions). None if no such factory."""
    if "manager_id" not in {row[1] for row in c.execute("PRAGMA table_info(factories)")}:
        return None
    found = c.execute("SELECT id, name, manager_id FROM factories WHERE id=?", (factory_id,)).fetchone()
    if found is None:
        return None
    runs = H._rows(
        c.execute(
            "SELECT id, started_at, finished_at, status, bottleneck_stage_id, judgment_count, woke, error "
            "FROM factory_manager_runs WHERE factory_id=? ORDER BY started_at DESC, id DESC LIMIT 50",
            (factory_id,),
        )
    )
    actions = H._rows(
        c.execute(
            "SELECT id, action, target_actor, note, created_at FROM factory_manager_actions WHERE factory_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT 50",
            (factory_id,),
        )
    )
    return {
        "factory_id": factory_id,
        "name": found["name"],
        "manager_id": found["manager_id"],
        "last_run": runs[0] if runs else None,
        "errors": [run for run in runs if run["status"] == "failed"],
        "runs": runs,
        "actions": actions,
    }


def install(app: "FastAPI", store: Any, mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """GET /api/v2/factories/{factory_id}/manager: the Factory Manager's run log (read only)."""
    from fastapi import Request

    from .store import Problem

    @app.get("/api/v2/factories/{factory_id}/manager")
    def factory_manager_read(request: Request, factory_id: str) -> dict[str, Any]:
        """The factory's Factory Manager: its last pass, its run log with errors first, and its actions."""
        who = request.state.identity
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "People and bots read the Factory Manager", 403)
        with store.read() as c:
            out = view(c, factory_id)
        if out is None:
            raise Problem("not_found", "No such factory", 404)
        return out


def factory_rail(c: "sqlite3.Connection", factory_id: str, at: Any = None) -> dict[str, Any] | None:
    """The factory's bottleneck right now: the stage with the most over-limit cards across its pipelines, or None.
    None too when there is no such factory."""
    if "manager_id" not in {row[1] for row in c.execute("PRAGMA table_info(factories)")}:
        return None
    if c.execute("SELECT 1 FROM factories WHERE id=?", (factory_id,)).fetchone() is None:
        return None
    pipelines = _pipelines(c, factory_id)
    return {"bottleneck": _bottleneck(c, pipelines, PM.when_of(at))}
