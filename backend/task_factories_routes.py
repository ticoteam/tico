# pyright: strict
"""Company › Factories (S12) and a pipeline manager's Log (S10), read from the pipelines' own tables.

GET /api/v2/factories?period=1d|7d|30d: each live factory with how many of its tasks reached Done in the period (Closed
never counts), and each live pipeline with its factory, how many open tasks the reader may read, what it files into,
and its mark. POST /api/v2/factories makes one (the company's owner and Admins). GET /api/v2/pipelines/{id}/log: each
push past, each snooze a bot made with no end date, and each save of the pipeline (Tico's included), newest first.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request

from . import access as _Access
from . import models as M
from .store import H as _H
from .store import Problem

H: Any = _H
Access: Any = _Access

PERIODS = {"1d": 1, "7d": 7, "30d": 30}
LOG_LIMIT = 100
# A pipeline's mark (S9): red, a bot in it can't run; amber, it needs a person.
CANT_RUN, NEEDS_PERSON = "cant_run", "needs_person"


def _since(period: str) -> str:
    if period not in PERIODS:
        raise Problem("period", "period is 1d, 7d or 30d", 422)
    at = datetime.now(timezone.utc) - timedelta(days=PERIODS[period])
    return at.isoformat().replace("+00:00", "Z")


def _marks(c: Any, pipeline_ids: list[Any], visible_sql: str) -> dict[Any, str | None]:
    """Each pipeline's mark over the open tasks the reader may read: CANT_RUN when one in it waits on a bot that can't run, else NEEDS_PERSON when one
    waits on a person, else None. Snoozed tasks are set aside and count for neither. Every task's waits are read the
    one way every screen reads them (waiting_on.py)."""
    from . import waiting_on as W

    marks: dict[Any, str | None] = dict.fromkeys(pipeline_ids)
    rows: list[Any] = c.execute(
        "SELECT t.id, t.pipeline_id FROM tasks t WHERE t.status='open' AND t.pipeline_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id=t.id AND s.ended_at IS NULL) "
        f"AND t.id IN (SELECT id FROM tasks WHERE {visible_sql})"
    ).fetchall()
    pipeline_of: dict[str, Any] = {r[0]: r[1] for r in rows if r[1] in marks}
    for tid, waits in W.waiting_on_many(c, list(pipeline_of)).items():
        states = {w.state for w in waits}
        pid = pipeline_of[tid]
        if W.PAUSED in states:
            marks[pid] = CANT_RUN
        elif W.PERSON in states and marks[pid] is None:
            marks[pid] = NEEDS_PERSON
    return marks


def factories(c: Any, visible_sql: str, period: str) -> dict[str, Any]:
    since = _since(period)
    found: list[dict[str, Any]] = [
        {"id": r[0], "name": r[1], "goal": r[2] or "", "manager_id": r[3], "done": 0}
        for r in c.execute(
            "SELECT id, name, goal, manager_id FROM factories WHERE retired_at IS NULL "
            "ORDER BY position, created_at, id"
        )
    ]
    done: dict[Any, Any] = dict(
        c.execute(
            "SELECT p.factory_id, count(*) FROM tasks t JOIN pipelines p ON p.id=t.pipeline_id "
            "WHERE t.status='done' AND t.done_at>=? AND p.factory_id IS NOT NULL "
            f"AND t.id IN (SELECT id FROM tasks WHERE {visible_sql}) "
            "GROUP BY p.factory_id",
            (since,),
        ).fetchall()
    )
    for row in found:
        row["done"] = done.get(row["id"], 0)
    live = {row["id"] for row in found}
    pipes: list[dict[str, Any]] = [
        {"id": r[0], "name": r[1], "factory_id": r[2] if r[2] in live else None}
        for r in c.execute(
            "SELECT id, name, factory_id FROM pipelines WHERE retired_at IS NULL ORDER BY created_at, id"
        )
    ]
    counts: dict[Any, Any] = dict(
        c.execute(
            "SELECT pipeline_id, count(*) FROM tasks WHERE status='open' AND pipeline_id IS NOT NULL "
            f"AND ({visible_sql}) "
            "GROUP BY pipeline_id"
        ).fetchall()
    )
    files_into: dict[Any, list[str]] = {}
    for pid, on_enter in c.execute(
        "SELECT s.pipeline_id, s.on_enter FROM stages s WHERE s.retired_at IS NULL AND s.kind='work' "
        "AND s.on_enter <> '{}' ORDER BY s.pipeline_id, s.position"
    ):
        settings: dict[str, Any] = H._json(on_enter, None) or {}
        target = settings.get("file_into")
        if isinstance(target, str) and target and target not in files_into.setdefault(pid, []):
            files_into[pid].append(target)
    marks = _marks(c, [p["id"] for p in pipes], visible_sql)
    for pipe in pipes:
        pipe["open"] = counts.get(pipe["id"], 0)
        pipe["files_into"] = files_into.get(pipe["id"], [])
        pipe["mark"] = marks[pipe["id"]]
    return {"factories": found, "pipelines": pipes}


def create(c: Any, who: Any, body: M.FactoryNew) -> dict[str, Any]:
    """A new factory at the end of the list. Only the company's owner and Admins make one."""
    found: dict[str, Any] = (H.human(c, H.actor_id(who.actor)) or {}) if H.is_human(who.actor) else {}
    email = str(found.get("email") or "").strip().lower()
    if not email or email not in set(Access.owner_and_admin_emails(c)):
        raise Problem("forbidden", "Only the company's owner and Admins make a factory", 403)
    name = body.name.strip()
    if not name:
        raise Problem("validation", "A factory has a name", 422)
    if c.execute("SELECT 1 FROM factories WHERE retired_at IS NULL AND lower(name)=lower(?)", (name,)).fetchone():
        raise Problem("factory_name", f"There is already a factory called {name}", 409)
    stamp = H.now()
    position = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM factories WHERE retired_at IS NULL").fetchone()[0]
    ident = str(H.new_id())
    c.execute(
        "INSERT INTO factories (id, name, goal, position, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (ident, name, body.goal.strip() or None, int(position), stamp, stamp),
    )
    H.event(c, who.actor, "factory.create", ident, {"name": name})
    return {"id": ident, "name": name, "goal": body.goal.strip(), "manager_id": None, "done": 0}


def _and(words: list[str]) -> str:
    """`A`, `A and B`, `A, B and C`."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _save_text(c: Any, by: str, detail: dict[str, Any], pipeline_id: str) -> str:
    """What a pipeline save says in the Log. Tico's move of untouched preset wording names the stages it moved (the goal
    for the pipeline's own goal); a person's save says it was made or saved."""
    who = H.actor_name(c, by)
    moved = detail.get("newer_preset_text")
    if not moved:
        return f"{who} {'made' if detail.get('made') else 'saved'} the pipeline"
    names = {
        str(r["id"]): r["name"] for r in c.execute("SELECT id,name FROM stages WHERE pipeline_id=?", (pipeline_id,))
    }
    where: list[str] = []
    for path in moved:
        label = "the goal" if path.endswith("/goal") else names.get(path.split("/")[0])
        if label and label not in where:
            where.append(label)
    return f"{who} updated check wording to the new default" + (f" on {_and(where)}" if where else "")


def log(c: Any, pipeline_id: str, visible_sql: str) -> list[dict[str, Any]]:
    """The manager rail's Log (S10): each push past out of a stage of this pipeline, each snooze a bot made with no end
    date on one of its tasks, and each save of the pipeline (`pipeline.save`, written to events with the pipeline as its
    target), newest first. Tasks by title; a task the reader may not read is left out."""
    t_visible = f"t.id IN (SELECT id FROM tasks WHERE {visible_sql})"
    lines: list[dict[str, Any]] = []
    for at, by, title, stage, why, tid in c.execute(
        "SELECT v.left_at, v.by_id, t.title, s.name, v.why, t.id FROM visits v JOIN stages s ON s.id=v.stage_id "
        "JOIN tasks t ON t.id=v.task_id "
        f"WHERE s.pipeline_id=? AND v.how='pushed_past' AND {t_visible} ORDER BY v.left_at DESC LIMIT ?",
        (pipeline_id, LOG_LIMIT),
    ):
        text = f"{H.actor_name(c, by) if by else 'Someone'} moved {title or 'a task'} past {stage}"
        lines.append({"at": at, "text": text, "task_id": tid, "by": by, "why": why or ""})
    for at, by, title, why, tid in c.execute(
        "SELECT z.created_at, z.by_id, t.title, z.why, t.id FROM snoozes z JOIN tasks t ON t.id=z.task_id "
        f"WHERE t.pipeline_id=? AND z.until_at IS NULL AND z.by_id LIKE 'bot:%' AND {t_visible} "
        "ORDER BY z.created_at DESC LIMIT ?",
        (pipeline_id, LOG_LIMIT),
    ):
        text = f"{H.actor_name(c, by)} snoozed {title or 'a task'} with no end"
        lines.append({"at": at, "text": text, "task_id": tid, "by": by, "why": why or ""})
    for at, by, detail in c.execute(
        "SELECT created_at, actor, detail FROM events WHERE action='pipeline.save' AND target=? "
        "ORDER BY created_at DESC LIMIT ?",
        (pipeline_id, LOG_LIMIT),
    ):
        text = _save_text(c, by, H._json(detail, {}), pipeline_id)
        lines.append({"at": at, "text": text, "task_id": None, "by": by, "why": ""})
    lines.sort(key=lambda line: line["at"] or "", reverse=True)
    return lines[:LOG_LIMIT]


def register(app: FastAPI, auth: Any, mutate: Callable[..., Any], store: Any) -> None:
    @app.get("/api/v2/factories")
    def factory_list(request: Request, period: str = "7d"):
        """Company › Factories: the live factories with their Done count for the period, and every live pipeline with
        its open count, what it files into and its mark."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return factories(c, auth.task_sql(c, who), period)

    @app.post("/api/v2/factories")
    def factory_create(request: Request, body: M.FactoryNew):
        """Make a factory. The company's owner and Admins only."""
        who = request.state.identity
        auth.domain(who)

        def write(c: Any) -> dict[str, Any]:
            return {"factory": create(c, who, body)}

        return mutate(request, body, write)

    @app.get("/api/v2/pipelines/{pipeline_id}/log")
    def pipeline_log(request: Request, pipeline_id: str):
        """The manager rail's Log and Learned (S10). Learned has no source in this engine yet: it is always empty."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not c.execute("SELECT 1 FROM pipelines WHERE id=?", (pipeline_id,)).fetchone():
                raise Problem("not_found", "No such pipeline", 404)
            return {"log": log(c, pipeline_id, auth.task_sql(c, who)), "learned": []}
