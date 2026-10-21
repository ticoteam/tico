"""Hosted release following, using the normal server updater after turns drain.

The request lives in SQLite so a restart cannot lose the claim fence. The updater
still owns bundles, snapshots, health checks and rollback; computers follow the
server through their existing idle-aware update path.
"""
import json
import re

from . import releases
from .store import H, Problem

KEY = "server-update-policy"
STABLE = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+")


def load(c, settings=None):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {
        "enabled": bool(settings and settings.hosted), "target": "", "state": "idle",
        "failed_version": "", "error": "", "retry_at": "", "actor": H.KEEPER}


def save(c, policy):
    c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (KEY, json.dumps(policy)))


def view(c, settings):
    p = load(c, settings)
    return {k: p[k] for k in ("enabled", "target", "state", "failed_version", "error")}


def holding(c):
    return bool(load(c)["target"])


def configure(c, settings, enabled, actor):
    p = load(c, settings)
    p["enabled"] = enabled
    # An update already sent to Docker must settle; disabling only cancels one
    # still waiting for idle and prevents subsequent automatic requests.
    if not enabled and p["state"] == "waiting":
        p.update(target="", state="idle", error="", retry_at="")
    save(c, p)
    H.event(c, actor, "system.update.policy", "", {"enabled": enabled})
    return view(c, settings)


def request(c, settings, target, actor):
    if not STABLE.fullmatch(target):
        raise Problem("invalid_version", "Choose a stable release version", 422)
    target = target.lstrip("v")
    current = releases.version()
    if not releases.parse(current) or releases.newer(current, target):
        raise Problem("invalid_version", "Choose the running release or a newer release", 409)
    if not releases._updater()[0]:
        raise Problem("manual_update", "This installation has no server updater", 409)
    p = load(c, settings)
    if p["target"] and p["target"] != target:
        raise Problem("update_pending", "A server update is already pending", 409)
    if target != current and not p["target"]:
        p.update(target=target, state="waiting", error="", retry_at="", actor=actor, attempted=False)
        save(c, p)
        H.event(c, actor, "system.update.queued", target, {"from": current, "to": target})
    return view(c, settings)


def record_outcome(c, result):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key='update-pending'").fetchone()
    if not row:
        return
    pending = json.loads(row[0])
    if result.get("to") and str(result["to"]).lstrip("v") != pending["to"]:
        return
    c.execute("DELETE FROM registry_metadata WHERE key='update-pending'")
    H.event(c, pending["actor"], "system.update.finished", pending["to"],
            {"outcome": result["state"], "from": pending["from"], "to": pending["to"],
             "message": result.get("message", "")})


def busy(c):
    if c.execute("SELECT 1 FROM attempts WHERE state IN ('leased','running') LIMIT 1").fetchone():
        return True
    # A worker can still be finishing after its lease lapsed. Its next claim
    # reports that work even while the server holds new claims.
    from .execution import CLAIMED_BUSY, claimed_busy
    return any(any(claimed_busy(runner)) for runner in list(CLAIMED_BUSY))


def tick(store):
    settings = store.settings
    if settings.rehearsal:
        return
    with store.read() as c:
        initial = load(c, settings)
    if not initial["enabled"] and not initial["target"]:
        return
    offer = releases.notice() if initial["enabled"] else {}
    current, now = releases.version(), H.now()
    with store.transaction() as c:
        p = load(c, settings)
        latest = offer.get("latest", "")
        if (not p["target"] and p["enabled"] and offer.get("available")
                and STABLE.fullmatch(latest) and latest.lstrip("v") != p["failed_version"]
                and releases._updater()[0]):
            request(c, settings, latest, H.KEEPER)
            p = load(c, settings)
        if not p["target"] or (p["retry_at"] and now < p["retry_at"]):
            return
        target = p["target"]
    try:
        result = releases.status()
        state, to = result.get("state"), str(result.get("to") or "").lstrip("v")
        reached = bool(releases.parse(current) and not releases.newer(target, current))
        if reached and state == "healthy" and to == current:
            with store.transaction() as c:
                p = load(c, settings)
                if p["target"] == target:
                    record_outcome(c, result)
                    p.update(target="", state="idle", error="", retry_at="")
                    save(c, p)
            return
        if state in ("pulling", "restarting", "uncertain") or (state == "healthy" and to == target):
            with store.transaction() as c:
                p = load(c, settings)
                if p["target"] == target and to == target:
                    p.update(state="updating", error="", retry_at="")
                    save(c, p)
            return
        if (state in ("failed", "rolled_back") and to == target
                and (reached or p.get("attempted") or p["actor"] == H.KEEPER)):
            with store.transaction() as c:
                p = load(c, settings)
                if p["target"] == target:
                    record_outcome(c, result)
                    p.update(target="", state="failed", failed_version=target,
                             error="The update failed or rolled back; waiting for a different release", retry_at="")
                    save(c, p)
            return
        if reached:
            # A matching image alone is insufficient: the updater may still
            # restore its snapshot. Missing/lost status keeps the durable fence.
            with store.transaction() as c:
                p = load(c, settings)
                if p["target"] == target:
                    p.update(state="updating", error="Waiting for the updater to confirm this release is healthy")
                    save(c, p)
            return
        with store.transaction() as c:
            p = load(c, settings)
            if p["target"] != target or busy(c):
                return
            p.update(state="updating", error="", retry_at="", attempted=True)
            save(c, p)
            record = {"actor": p["actor"], "from": current, "to": target}
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('update-pending',?)", (json.dumps(record),))
            H.event(c, p["actor"], "system.update.started", target, {"from": current, "to": target})
        releases.start(target)
    except Problem:
        with store.transaction() as c:
            p = load(c, settings)
            if p["target"] == target:
                p.update(error="The updater did not answer; checking again in 15 minutes",
                         retry_at=H.shift(now, minutes=15))
                save(c, p)
