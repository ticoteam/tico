"""The daily release hour and Release now (docs/pipelines-v3.md D13): GET/PATCH /api/v2/release-schedule and POST
/api/v2/release/now, the paths and methods the CLI calls.

The setting (`release-schedule`, a team setting in registry_metadata) is what it was: on or off, one or two hours in
company time (`updates.ZONE`), and the Release Manager and Technical Writer, named or found by starter template. What
waits for a release is now the open tasks in a stage that has an "In a release" requirement. Release now gives the
Release Manager ONE task, or answers with its unfinished one. Firing the daily hour is a routine on the Release Manager
(D13); this module only reads the next hour.
"""

import json
import re
from datetime import datetime, timedelta

from fastapi import Request

from . import bot_settings as BS
from . import models as M
from .store import H, Problem

KEY = "release-schedule"
FIRED = "release-schedule-fired"
DEFAULT_TIMES = ["10:00"]
MAX_TIMES = 2
AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TITLE = "Release: "
# Starter templates (templates/catalog): the Release Manager comes from release-owner, else release-notes.
RELEASE_OWNER, MANAGER, WRITER = "release-owner", "release-notes", "docs-writer"


def zone():
    from .updates import ZONE

    return ZONE


def load(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    times = [t for t in stored.get("times") or [] if isinstance(t, str) and AT.match(t)][:MAX_TIMES]
    return {
        "on": stored["on"] if isinstance(stored.get("on"), bool) else True,
        "times": sorted(dict.fromkeys(times)) or list(DEFAULT_TIMES),
        "bot": str(stored.get("bot") or ""),
        "writer": str(stored.get("writer") or ""),
    }


def save(c, actor, changes):
    """Set what `changes` names: `on`, `times` (one or two HH:MM in company time), `bot` (the Release Manager) and
    `writer` (the Technical Writer), the last two a bot or "" to go back to finding it by template."""
    before = load(c)
    after = dict(before)
    if changes.get("on") is not None:
        after["on"] = bool(changes["on"])
    if changes.get("times") is not None:
        times = [str(t).strip() for t in changes["times"]]
        if not 1 <= len(times) <= MAX_TIMES or any(not AT.match(t) for t in times):
            raise Problem("invalid", "Give one or two release times as HH:MM, from 00:00 to 23:59", 422)
        after["times"] = sorted(dict.fromkeys(times))
    for field in ("bot", "writer"):
        if changes.get(field) is not None:
            value = H.actor_id(str(changes[field]).strip())
            slug = (H.resolve_bot(c, value) or {}).get("id", value) if value else ""
            if slug and not (H.bot(c, slug) or {}).get("state") == "active":
                raise Problem("invalid", f"No active bot {slug}", 422)
            after[field] = slug
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(after, sort_keys=True)),
    )
    if after != before:
        H.event(c, actor, "release.schedule", "team", {"before": before, "after": after})
    return after


def _named(c, slug, templates):
    """The bot the setting names (if active), else the first active bot of the first template that has one."""
    if slug:
        found = H.resolve_bot(c, slug) or {}
        return found["id"] if found.get("state") == "active" else None
    for template in templates:
        found = c.execute(
            f"SELECT b.id FROM bots b JOIN bot_config bc ON bc.bot=b.id {BS.JOIN} WHERE b.state='active' "
            "AND s.template=? ORDER BY b.slug LIMIT 1",
            (template,),
        ).fetchone()
        if found:
            return found[0]
    return None


def release_manager(c, cfg=None):
    return _named(c, (cfg or load(c))["bot"], (RELEASE_OWNER, MANAGER))


def technical_writer(c, cfg=None):
    return _named(c, (cfg or load(c))["writer"], (WRITER,))


def pending(c):
    """The open tasks waiting for a release: in a stage with an "In a release" requirement, oldest first."""
    return [
        r[0]
        for r in c.execute(
            "SELECT t.id FROM tasks t WHERE t.status='open' AND EXISTS (SELECT 1 FROM requirements r "
            "WHERE r.stage_id=t.stage_id AND r.kind='in_release' AND r.withdrawn_at IS NULL) ORDER BY t.created_at, t.id"
        )
    ]


def next_at(cfg, at):
    """The next release hour after `at`, or None while the schedule is off."""
    if not cfg["on"]:
        return None
    local = at.astimezone(zone())
    best = None
    for text in cfg["times"]:
        hour, minute = (int(x) for x in text.split(":"))
        point = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if point <= local:
            point += timedelta(days=1)
        best = point if best is None or point < best else best
    return best


def may_release(c, who):
    """Any person on the team; of the bots, the Release Manager and the manager of a pipeline a release moves on."""
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
            "JOIN requirements r ON r.stage_id=s.id WHERE r.kind='in_release' AND p.manager_id IS NOT NULL"
        )
    )


def view(c, who, auth):
    cfg = load(c)
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (FIRED,)).fetchone()
    fired = json.loads(row[0]) if row and row[0] else {}
    nxt = next_at(cfg, datetime.now(zone()))
    return {
        **cfg,
        "zone": str(zone()),
        "release_manager": release_manager(c, cfg),
        "technical_writer": technical_writer(c, cfg),
        "next": nxt.isoformat() if nxt else None,
        "last": fired[max(fired)] if isinstance(fired, dict) and fired else None,
        "pending": len(pending(c)),
        "can_change": bool(auth.bot_admin(who)),
        "can_release": may_release(c, who),
    }


def request_now(c, actor, note=""):
    """ONE task for the Release Manager, or its unfinished Release task. `sent` is false, with a `reason`
    (no_release_manager, nothing_pending, already_open), when no new task was filed. A person's note is heard even
    with nothing waiting for a release: work often reaches main with no task."""
    manager = release_manager(c)
    if not manager:
        return {"sent": False, "reason": "no_release_manager", "task": None, "tasks": 0}
    owner = H.bot_actor(manager)
    waiting = pending(c)
    if not waiting and not (H.is_human(actor) and note.strip()):
        return {"sent": False, "reason": "nothing_pending", "task": None, "tasks": 0}
    found = c.execute(
        "SELECT id FROM tasks WHERE owner_id=? AND status='open' AND substr(title, 1, ?)=? "
        "ORDER BY created_at DESC LIMIT 1",
        (owner, len(TITLE), TITLE),
    ).fetchone()
    if found:
        return {"sent": False, "reason": "already_open", "task": found[0], "tasks": len(waiting)}
    title = (
        f"{TITLE}{len(waiting)} task{'s' if len(waiting) != 1 else ''} waiting for a release"
        if waiting
        else f"{TITLE}requested by {H.actor_name(c, actor)}"
    )
    lines = [f"Asked by {H.actor_name(c, actor)}" + (f": {note}" if note else ".")]
    lines += [f"- {tid}" for tid in waiting[:60]]
    task = H.task_create(c, actor, title, "\n".join(lines), owner, deduplicate=False)
    H.event(c, actor, "release.requested", task["id"], {"tasks": len(waiting)})
    return {"sent": True, "reason": None, "task": task["id"], "tasks": len(waiting)}


def register(app, auth, mutate, store):
    @app.get("/api/v2/release-schedule")
    def release_schedule(request: Request):
        """The team's release schedule, the Release Manager and Technical Writer found, the next hour, how many
        tasks wait for a release, and what this viewer may do (`can_change`, `can_release`)."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return view(c, who, auth)

    @app.patch("/api/v2/release-schedule")
    def release_schedule_save(request: Request, body: M.ReleaseScheduleUpdate):
        """Turn the daily release on or off, set its hours, or name the Release Manager and Technical Writer. An
        owner or an admin; only what is sent changes."""
        who = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin changes the release schedule", 403)
        mutate(request, body, lambda c: save(c, who.actor, body.model_dump(exclude_none=True)))
        with store.read() as c:
            return view(c, who, auth)

    @app.post("/api/v2/release/now")
    def release_now(request: Request, body: M.ReleaseNow):
        """Ask the Release Manager for a release now, without waiting for the daily hour."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not may_release(c, who):
                raise Problem("forbidden", "Only a person, the Release Manager or a releasing pipeline's manager", 403)
        return mutate(request, body, lambda c: request_now(c, who.actor, body.note or ""))
