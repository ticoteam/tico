"""Suggest: company skills' remove and promote suggestions from this install's own data (backend/skill_suggestions.py,
for the Skills page; no consent needed, nothing leaves the install), then guidance fetched from Tico Gateway with
count-only stats once daily, with explicit install consent."""

import logging
from datetime import timedelta

from .. import skill_suggestions, suggestions
from ..store import H

LOG = logging.getLogger("tico.maintenance")


def _result(status, reason, checked=0, found=0, details=None):
    return {"status": status, "reason": reason, "checked": checked, "found": found, "woke": 0,
            "cost_usd": 0.0, "details": details or {}}


def run(c, ctx):
    """c is read only. The skills part writes in its own transaction; fetch and report release all write transactions
    before HTTP. The result is the Gateway part's, with the skills part's counts added when it made or closed any."""
    if not ctx["settings"].get("enabled", True):
        return _result("skipped", "Suggest is off")
    try:
        skills = skill_suggestions.run(ctx["store"], H.parse_ts(ctx["now"]))
    except Exception as exc:  # noqa: BLE001 - a fault here must not cost the Gateway part its day; it shows in details
        LOG.exception("skill suggestions failed")
        skills = {"made": 0, "closed": 0, "error": type(exc).__name__}
    result = _gateway(c, ctx)
    if skills["made"] or skills["closed"] or skills.get("error"):
        result["found"] += skills["made"]
        result["details"] = {**result["details"], "skills": skills}
    return result


def _gateway(c, ctx):
    store = ctx["store"]
    if not suggestions.available(c, store.settings) or store.settings.demo or store.settings.rehearsal:
        return _result("skipped", "Needs Tico Gateway")
    if not suggestions.privacy(c, store.settings)["share_stats_for_suggestions"]:
        return _result("skipped", "Privacy consent is off")
    previous = c.execute("SELECT value FROM registry_metadata WHERE key=?", (suggestions.LAST_FETCH,)).fetchone()
    if previous and H.parse_ts(H.now()) - H.parse_ts(previous[0]) < timedelta(days=1):
        return _result("skipped", "Already checked today", details={"limited": True})
    try:
        destination = suggestions.target(store)
        if not destination[0]:
            return _result("skipped", "Needs Tico Gateway")
        now = H.now()
        items = suggestions.fetch(store, destination)
        if items is None:
            return _result("skipped", "Privacy consent is off")
    except Exception as exc:  # noqa: BLE001 - any Gateway failure is this step's failure, not the run's
        return _result("failed", str(exc) if isinstance(exc, suggestions.GatewayError) else "Gateway connection failed")
    with store.transaction() as tx:
        # Consent may have changed while HQ answered.
        if not suggestions.privacy(tx, store.settings)["share_stats_for_suggestions"]:
            return _result("skipped", "Privacy consent is off")
        suggestions.expire(tx, now)
        found = suggestions.store_new(tx, items, now)
        delivered = suggestions.deliver_people(tx, store.settings)
        tx.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (suggestions.LAST_FETCH, now))
    try:
        reported = suggestions.report(store)
    except Exception as exc:  # noqa: BLE001 - delivered suggestions stay delivered; reports retry next run
        reason = str(exc) if isinstance(exc, suggestions.GatewayError) else "Gateway connection failed"
        return _result("failed", reason, len(items), found, {"delivered": delivered, "reported": 0})
    return _result("ran", "Suggestions kept for the next Update", len(items), found,
                   {"delivered": delivered, "reported": reported})
