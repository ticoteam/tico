# pyright: strict
"""The daily release hour and Release now (docs/pipelines-v3.md D13): GET/PATCH /api/v2/release-schedule and POST
/api/v2/release/now, the paths and methods the CLI calls.

The setting (`release-schedule`, a team setting in registry_metadata) is what it was: on or off, one or two hours in
company time (`updates.ZONE`), and the Release Owner and Technical Writer, named or found by starter template. What
waits for a release is now the open tasks in a stage that has an "In a release" requirement. Release now gives the
Release Owner ONE task, naming no waiting work, or answers with its unfinished one. Firing the daily hour is a routine on the Release Owner
(D13); this module only reads the next hour.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any, cast

from fastapi import FastAPI, Request

from . import bot_settings as _BS
from . import models as M
from .store import H as _H
from .store import Problem

H: Any = _H
BS: Any = _BS

KEY = "release-schedule"
FIRED = "release-schedule-fired"
DEFAULT_TIMES = ["10:00"]
MAX_TIMES = 2
AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TITLE = "Release: "
# Starter templates (templates/catalog): the Release Owner comes from release-owner. release-notes is a writer, not
# the Release Owner (D1).
RELEASE_OWNER, WRITER = "release-owner", "docs-writer"


def zone() -> tzinfo:
    from .updates import ZONE

    found: tzinfo = ZONE
    return found


def load(c: Any) -> dict[str, Any]:
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        loaded: Any = json.loads(row[0]) if row else {}
    except ValueError:
        loaded = {}
    # isinstance narrows the parsed JSON to dict[Unknown, Unknown]; its values are any JSON
    stored = cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}
    given: list[Any] = stored.get("times") or []
    times = [t for t in given if isinstance(t, str) and AT.match(t)][:MAX_TIMES]
    return {
        "on": stored["on"] if isinstance(stored.get("on"), bool) else True,
        "times": sorted(dict.fromkeys(times)) or list(DEFAULT_TIMES),
        "bot": str(stored.get("bot") or ""),
        "writer": str(stored.get("writer") or ""),
    }


def save(c: Any, actor: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Set what `changes` names: `on`, `times` (one or two HH:MM in company time), `bot` (the Release Owner) and
    `writer` (the Technical Writer), the last two a bot or "" to go back to finding it by template."""
    before = load(c)
    after = dict(before)
    if changes.get("on") is not None:
        after["on"] = bool(changes["on"])
    if changes.get("times") is not None:
        asked: list[Any] = changes["times"]
        times = [str(t).strip() for t in asked]
        if not 1 <= len(times) <= MAX_TIMES or any(not AT.match(t) for t in times):
            raise Problem("invalid", "Give one or two release times as HH:MM, from 00:00 to 23:59", 422)
        after["times"] = sorted(dict.fromkeys(times))
    for field in ("bot", "writer"):
        if changes.get(field) is not None:
            value = H.actor_id(str(changes[field]).strip())
            resolved: dict[str, Any] = (H.resolve_bot(c, value) or {}) if value else {}
            slug: str = resolved.get("id", value) if value else ""
            bot: dict[str, Any] = (H.bot(c, slug) or {}) if slug else {}
            if slug and not bot.get("state") == "active":
                raise Problem("invalid", f"No active bot {slug}", 422)
            after[field] = slug
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(after, sort_keys=True)),
    )
    if after != before:
        H.event(c, actor, "release.schedule", "team", {"before": before, "after": after})
    return after


def _named(c: Any, slug: str, templates: tuple[str, ...]) -> str | None:
    """The bot the setting names (if active), else the first active bot of the first template that has one."""
    if slug:
        bot: dict[str, Any] = H.resolve_bot(c, slug) or {}
        return bot["id"] if bot.get("state") == "active" else None
    for template in templates:
        found = c.execute(
            f"SELECT b.id FROM bots b JOIN bot_config bc ON bc.bot=b.id {BS.JOIN} WHERE b.state='active' "
            "AND s.template=? ORDER BY b.slug LIMIT 1",
            (template,),
        ).fetchone()
        if found:
            return found[0]
    return None


def release_manager(c: Any, cfg: dict[str, Any] | None = None) -> str | None:
    return _named(c, (cfg or load(c))["bot"], (RELEASE_OWNER,))


def technical_writer(c: Any, cfg: dict[str, Any] | None = None) -> str | None:
    return _named(c, (cfg or load(c))["writer"], (WRITER,))


def pending(c: Any, visible_sql: str | None = None) -> list[str]:
    """The open tasks waiting for a release: in a stage with an "In a release" requirement, oldest first. With
    `visible_sql`, only those a reader may read: what a person is shown. A release itself moves them all."""
    seen = f" AND t.id IN (SELECT id FROM tasks WHERE {visible_sql})" if visible_sql else ""
    return [
        r[0]
        for r in c.execute(
            "SELECT t.id FROM tasks t WHERE t.status='open' AND EXISTS (SELECT 1 FROM requirements r "
            "WHERE r.stage_id=t.stage_id AND r.kind='item' AND r.source='github' AND r.condition='in_release' "
            "AND r.withdrawn_at IS NULL)" + seen + " ORDER BY t.created_at, t.id"
        )
    ]


def next_at(cfg: dict[str, Any], at: datetime) -> datetime | None:
    """The next release hour after `at`, or None while the schedule is off (release_hour.following)."""
    from . import release_hour

    found = release_hour.following(cfg, at)
    return found.astimezone(zone()) if found else None


def may_release(c: Any, who: Any) -> bool:
    """Any person on the team; of the bots, the Release Owner and the manager of a pipeline a release moves on."""
    if who.role in ("owner", "human"):
        return True
    if who.role != "bot":
        return False
    me = H.actor_id(who.actor)
    if me == release_manager(c):
        return True
    return any(
        H.actor_id(row[0]) == me
        for row in c.execute(
            "SELECT DISTINCT p.manager_id FROM pipelines p JOIN stages s ON s.pipeline_id=p.id "
            "JOIN requirements r ON r.stage_id=s.id WHERE r.kind='item' AND r.source='github' "
            "AND r.condition='in_release' AND p.manager_id IS NOT NULL"
        )
    )


def view(c: Any, who: Any, auth: Any) -> dict[str, Any]:
    cfg = load(c)
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (FIRED,)).fetchone()
    loaded: Any = json.loads(row[0]) if row and row[0] else {}
    # isinstance narrows the parsed JSON to dict[Unknown, Unknown]; its values are any JSON
    fired = cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}
    nxt = next_at(cfg, datetime.now(zone()))
    return {
        **cfg,
        "zone": str(zone()),
        "release_manager": release_manager(c, cfg),
        "technical_writer": technical_writer(c, cfg),
        "next": nxt.isoformat() if nxt else None,
        "last": fired[max(fired)] if fired else None,
        "pending": len(pending(c, auth.task_sql(c, who))),
        "can_change": bool(auth.bot_admin(who)),
        "can_release": may_release(c, who),
    }


def request_now(c: Any, actor: str, note: str = "", visible_sql: str | None = None) -> dict[str, Any]:
    """ONE task for the Release Owner, or its unfinished Release task. Anyone may read that task, so it says who
    asked and their note, never which tasks wait or how many: the Release Owner finds what waits itself, and closes
    the task when nothing does. It is filed whether or not anything waits, so the answer never tells a requester that
    hidden work waits. The answer's `tasks` counts only the waiting tasks the requester may read (`visible_sql`).
    `sent` is false, with a `reason` (no_release_manager, already_open), when no new task was filed; the unfinished
    task's id is given only to someone who may read it."""
    seen = len(pending(c, visible_sql))
    manager = release_manager(c)
    if not manager:
        return {"sent": False, "reason": "no_release_manager", "task": None, "tasks": seen}
    owner = H.bot_actor(manager)
    found = c.execute(
        "SELECT id FROM tasks WHERE owner_id=? AND status='open' AND substr(title, 1, ?)=? "
        "ORDER BY created_at DESC LIMIT 1",
        (owner, len(TITLE), TITLE),
    ).fetchone()
    if found:
        mine = (
            not visible_sql or c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({visible_sql})", (found[0],)).fetchone()
        )
        return {"sent": False, "reason": "already_open", "task": found[0] if mine else None, "tasks": seen}
    name = H.actor_name(c, actor)
    body = (
        f"Asked by {name}" + (f": {note}" if note else ".") + "\n\nRelease every task waiting for a release, or close "
        "this task if none waits."
    )
    task = H.task_create(c, actor, f"{TITLE}requested by {name}", body, owner, deduplicate=False)
    H.event(c, actor, "release.requested", task["id"], {})
    return {"sent": True, "reason": None, "task": task["id"], "tasks": seen}


def register(app: FastAPI, auth: Any, mutate: Callable[..., Any], store: Any) -> None:
    @app.get("/api/v2/release-schedule")
    def release_schedule(request: Request):
        """The team's release schedule, the Release Owner and Technical Writer found, the next hour, how many
        tasks wait for a release, and what this viewer may do (`can_change`, `can_release`)."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return view(c, who, auth)

    @app.patch("/api/v2/release-schedule")
    def release_schedule_save(request: Request, body: M.ReleaseScheduleUpdate):
        """Turn the daily release on or off, set its hours, or name the Release Owner and Technical Writer. An
        owner or an admin; only what is sent changes."""
        who = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin changes the release schedule", 403)

        def write(c: Any) -> dict[str, Any]:
            return save(c, who.actor, body.model_dump(exclude_none=True))

        mutate(request, body, write)
        with store.read() as c:
            return view(c, who, auth)

    @app.post("/api/v2/release/now")
    def release_now(request: Request, body: M.ReleaseNow):
        """Ask the Release Owner for a release now, without waiting for the daily hour."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not may_release(c, who):
                raise Problem("forbidden", "Only a person, the Release Owner or a releasing pipeline's manager", 403)

        def write(c: Any) -> dict[str, Any]:
            return request_now(c, who.actor, body.note or "", auth.task_sql(c, who))

        return mutate(request, body, write)
