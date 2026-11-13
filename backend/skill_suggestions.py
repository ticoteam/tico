# pyright: strict
"""Remove and promote suggestions for company skills (docs/skills.md#suggestions), made by the maintenance run's Suggest
step and shown to admins on Library › Skills.

- remove: an install skill_usage's coverage calls unused (a receipt, a target that reports usage, the install 30 days
  old, no use in 30 days). An install Tico cannot judge ("unknown") is never suggested for removal.
- promote: a skill the directory does not have that bots used at least PROMOTE_USES times, or that at least PROMOTE_BOTS
  bots used, in 30 days (`unlisted_skill_usage_dailies`). It names the bot that used it most; Ask to submit sends that
  bot the admin's own message asking it to run `tico skill submit <name>`. Nothing here uploads anything.

Nothing is removed or submitted on its own: an admin's Remove takes the install off with the same rights as uninstalling
it. One row per subject (the install, or the skill's name): open until an admin acts on it or dismisses it, or until it
is 30 days old (expired), or until it no longer holds. A closed one comes back only 30 days after it closed, and only
if it still holds.

They wait on the page rather than going out with the run's other suggestions: those (backend/suggestions.py) come from
Tico Gateway, report their outcomes back to it and reach a role's bot through its Update or the owner. These are about
the company's own installs and bots, an admin decides each one, and nothing about them leaves this install.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import hubdb as H
from . import models as M
from . import skill_usage as U
from . import skills as SK
from .store import Problem

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth, Identity
    from .store import Store

PROMOTE_BOTS = 2
PROMOTE_USES = 10
OPEN_DAYS = 30  # an open suggestion expires after this long
AGAIN_DAYS = 30  # a closed one may come back after this long, if it still holds
LIST_CAP = 50
KINDS = ("remove", "promote")


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _id() -> str:
    return "sks_" + uuid.uuid4().hex[:24]


def _listed(c: sqlite3.Connection, name: str) -> bool:
    """Whether the name is in the directory now, or waits as a pending proposal: promoting it is moot."""
    return (
        c.execute(
            "SELECT 1 FROM skills WHERE name=? AND deleted_at IS NULL UNION ALL "
            "SELECT 1 FROM skill_proposals WHERE name=? AND status='pending' LIMIT 1",
            (name, name),
        ).fetchone()
        is not None
    )


def _active_bots(c: sqlite3.Connection) -> set[str]:
    return {r[0] for r in c.execute("SELECT id FROM bots WHERE coalesce(state, '') <> 'archived'")}


def candidates(c: sqlite3.Connection, at: datetime) -> dict[tuple[str, str], dict[str, Any]]:
    """{(kind, subject): {name, target_type, target_id, uses_count, bots_count}} of what holds now."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    r: dict[str, Any]
    for r in U.unused_installs(c, at):
        out[("remove", r["id"])] = {
            "name": r["skill_name"],
            "target_type": r["target_type"],
            "target_id": r["target_id"],
            "uses_count": 0,
            "bots_count": 0,
        }
    active = _active_bots(c)
    for r in U.unlisted_usage(c, at):
        if r["uses"] < PROMOTE_USES and len(r["bots"]) < PROMOTE_BOTS:
            continue
        asked = next((b for b, _ in r["bots"] if b in active), None)
        if not asked or _listed(c, r["name"]):
            continue
        out[("promote", r["name"])] = {
            "name": r["name"],
            "target_type": "Bot",
            "target_id": asked,
            "uses_count": r["uses"],
            "bots_count": len(r["bots"]),
        }
    return out


def _close(c: sqlite3.Connection, row_id: str, status: str, now: str, by: str | None = None) -> None:
    c.execute(
        "UPDATE skill_suggestions SET status=?, closed_by_id=?, closed_at=?, updated_at=? WHERE id=? AND status='open'",
        (status, by, now, now, row_id),
    )


def _resolved(c: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Whether an open suggestion that no longer holds went away because someone did it: the install is gone, or the
    skill is in the directory or proposed."""
    if row["kind"] == "remove":
        return c.execute("SELECT 1 FROM skill_installs WHERE id=?", (row["subject"],)).fetchone() is None
    return _listed(c, row["subject"])


def refresh(c: sqlite3.Connection, at: datetime) -> dict[str, int]:
    """One pass in a write transaction: expire the old, close what no longer holds, open what newly holds.
    Returns {made, closed}."""
    now = _stamp(at)
    found = candidates(c, at)
    closed = made = 0
    for row in c.execute("SELECT * FROM skill_suggestions WHERE status='open'").fetchall():
        if row["created_at"] <= _stamp(at - timedelta(days=OPEN_DAYS)):
            _close(c, row["id"], "expired", now)
            closed += 1
        elif (row["kind"], row["subject"]) not in found:
            _close(c, row["id"], "acted" if _resolved(c, row) else "expired", now)
            closed += 1
    again = _stamp(at - timedelta(days=AGAIN_DAYS))
    for (kind, subject), v in found.items():
        row = c.execute("SELECT * FROM skill_suggestions WHERE kind=? AND subject=?", (kind, subject)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO skill_suggestions (id, kind, subject, name, target_type, target_id, uses_count, bots_count, "
                "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'open',?,?)",
                (
                    _id(),
                    kind,
                    subject,
                    v["name"],
                    v["target_type"],
                    v["target_id"],
                    v["uses_count"],
                    v["bots_count"],
                    now,
                    now,
                ),
            )
            made += 1
        elif row["status"] == "open":
            c.execute(
                "UPDATE skill_suggestions SET target_id=?, uses_count=?, bots_count=?, updated_at=? WHERE id=?",
                (v["target_id"], v["uses_count"], v["bots_count"], now, row["id"]),
            )
        elif (row["closed_at"] or row["updated_at"]) <= again:
            c.execute(
                "UPDATE skill_suggestions SET name=?, target_type=?, target_id=?, uses_count=?, bots_count=?, "
                "status='open', closed_by_id=NULL, closed_at=NULL, created_at=?, updated_at=? WHERE id=?",
                (v["name"], v["target_type"], v["target_id"], v["uses_count"], v["bots_count"], now, now, row["id"]),
            )
            made += 1
    return {"made": made, "closed": closed}


def run(store: "Store", at: datetime | None = None) -> dict[str, int]:
    """The Suggest step's skills part (backend/maintenance_steps/suggest.py): local data only, no model, no Gateway."""
    at = at or datetime.now(timezone.utc)
    with store.transaction() as c:
        return refresh(cast(sqlite3.Connection, c), at)


# ----------------------------------------------------------------------------- the page
def _view(row: sqlite3.Row, names: dict[tuple[str, str], str]) -> dict[str, Any]:
    target = (row["target_type"], row["target_id"])
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "target": {"type": target[0], "id": target[1], "name": names.get(target, target[1])},
        "install_id": row["subject"] if row["kind"] == "remove" else None,
        "uses_30d": row["uses_count"],
        "bots": row["bots_count"],
        "created_at": row["created_at"],
    }


def listing(c: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """The open suggestions that still stand (the install is still there; the name is still outside the directory)."""
    rows = [
        r
        for r in c.execute(
            "SELECT * FROM skill_suggestions WHERE status='open' ORDER BY kind DESC, created_at, id LIMIT ?",
            (LIST_CAP * 2,),
        ).fetchall()
        if not _resolved(c, r)
    ][:LIST_CAP]
    names = U._names(c, {(r["target_type"], r["target_id"]) for r in rows})
    return {"suggestions": [_view(r, names) for r in rows]}


def _open(c: sqlite3.Connection, sid: str, kind: str | None = None) -> sqlite3.Row:
    row = c.execute("SELECT * FROM skill_suggestions WHERE id=?", (sid,)).fetchone()
    if not row or (kind and row["kind"] != kind):
        raise Problem("not_found", "No such suggestion", 404)
    if row["status"] != "open":
        raise Problem("conflict", "This suggestion is closed", 409)
    return row


def remove(c: sqlite3.Connection, auth: "Auth", who: "Identity", sid: str) -> dict[str, Any]:
    """Take the suggested install off (the same rights as uninstalling it), and close the suggestion as acted on."""
    row = _open(c, sid, "remove")
    install = c.execute("SELECT * FROM skill_installs WHERE id=?", (row["subject"],)).fetchone()
    if install:
        SK._target(c, auth, who, install["target_type"], install["target_id"])
        c.execute("DELETE FROM skill_installs WHERE id=?", (install["id"],))
        H.event(
            c,
            who.actor,
            "skill.uninstalled",
            install["id"],
            {"target_type": install["target_type"], "target_id": install["target_id"], "suggestion_id": sid},
        )
    _close(c, sid, "acted", H.now(), H.actor_id(who.actor))
    return {"id": sid, "status": "acted", "removed": bool(install)}


def close(c: sqlite3.Connection, who: "Identity", sid: str, status: str) -> dict[str, str]:
    """Close a suggestion: `acted` after the page asked the bot to submit (promote only), or `dismissed`."""
    row = _open(c, sid, "promote" if status == "acted" else None)
    _close(c, sid, status, H.now(), H.actor_id(who.actor))
    H.event(c, who.actor, f"skill.suggestion_{status}", sid, {"kind": row["kind"], "name": row["name"]})
    return {"id": sid, "status": status}


def install(app: "FastAPI", store: "Store", auth: "Auth", mutate: "Callable[..., Any]") -> None:
    """Admin routes. Installed before backend/skills.py's, so `skills/suggestions` is not read as a skill's name."""

    def admin(request: Request) -> "Identity":
        who = request.state.identity
        auth.domain(who)
        if who.role not in SK.PEOPLE_AND_BOTS or not SK._admin(auth, who):
            raise Problem("forbidden", "Skill suggestions are for admins", 403)
        return who

    @app.get("/api/v2/skills/suggestions")
    def skill_suggestions(request: Request):
        """Open remove and promote suggestions from the maintenance run. Admins only."""
        admin(request)
        with store.read() as c:
            return listing(c)

    @app.post("/api/v2/skills/suggestions/{sid}/remove")
    def skill_suggestion_remove(request: Request, sid: str, body: M.Empty):
        """Take a suggested unused install off, and close the suggestion."""
        who = admin(request)
        return mutate(request, body, lambda c: remove(c, auth, who, sid))

    @app.post("/api/v2/skills/suggestions/{sid}/asked")
    def skill_suggestion_asked(request: Request, sid: str, body: M.Empty):
        """Close a promote suggestion once its bot has been asked to submit the skill."""
        who = admin(request)
        return mutate(request, body, lambda c: close(c, who, sid, "acted"))

    @app.post("/api/v2/skills/suggestions/{sid}/dismiss")
    def skill_suggestion_dismiss(request: Request, sid: str, body: M.Empty):
        """Dismiss a suggestion: it comes back only 30 days later, if it still holds."""
        who = admin(request)
        return mutate(request, body, lambda c: close(c, who, sid, "dismissed"))
