"""Suggest fetches count-only guidance once daily, with explicit install consent."""

from datetime import timedelta

from .. import suggestions
from ..store import H


def _result(status, reason, checked=0, found=0, details=None):
    return {"status": status, "reason": reason, "checked": checked, "found": found, "woke": 0,
            "cost_usd": 0.0, "details": details or {}}


def run(c, ctx):
    """c is read only. Fetch and report release all write transactions before HTTP."""
    store = ctx["store"]
    if not suggestions.available(c, store.settings) or store.settings.demo or store.settings.rehearsal:
        return _result("skipped", "Needs Tico Gateway")
    if not suggestions.privacy(c, store.settings)["share_stats_for_suggestions"]:
        return _result("skipped", "Privacy consent is off")
    if not ctx["settings"].get("enabled", True):
        return _result("skipped", "Suggest is off")
    now = ctx.get("now") or H.now()
    if not isinstance(now, str):
        now = now.isoformat()
    previous = c.execute("SELECT value FROM registry_metadata WHERE key=?", (suggestions.LAST_FETCH,)).fetchone()
    lasts = [last for last in (ctx["settings"].get("last_run_at"), previous[0] if previous else None) if last]
    if lasts and H.parse_ts(now) - max(H.parse_ts(last) for last in lasts) < timedelta(days=1):
        return _result("skipped", "Already checked today")
    try:
        destination = suggestions.target(store)
        if not destination[0]:
            return _result("skipped", "Needs Tico Gateway")
        items = suggestions.fetch(store, destination)
    except Exception as exc:
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
    except Exception as exc:
        reason = str(exc) if isinstance(exc, suggestions.GatewayError) else "Gateway connection failed"
        return _result("failed", reason, len(items), found, {"delivered": delivered, "reported": 0})
    return _result("ran", "Suggestions kept for the next Update", len(items), found,
                   {"delivered": delivered, "reported": reported})
