"""Dream: the auto dream (backend/dream_auto.py), once a day, for up to "Most bots per run" bots (default 3). The verdict is
dream_auto's: the changed lines, the lookback, the gaps and the busy checks. The 02:00 timer is gone; this step is its
only trigger. No customer tokens are counted here: a started dream is a task, and its turns are the bot's own.
"""
from .. import dream, dream_auto
from ..store import H


def _skip(reason):
    return {"status": "skipped", "reason": reason, "checked": 0, "found": 0, "woke": 0, "cost_usd": 0.0, "details": {}}


def run(c, ctx):
    store = ctx["store"]
    settings = store.settings
    if settings.rehearsal or settings.test_identities:
        return _skip("rehearsal and test servers start no auto dreams")
    if dream.creator(store) is None:
        return _skip("the dream's task path is not wired on this server")
    limit = ctx["settings"].get("max_bots") or 0
    if limit <= 0:
        return _skip('"Most bots per run" is 0')
    report = {}
    started = dream_auto.run(store, ctx["auth"], H.parse_ts(ctx["now"]), limit=limit, report=report)
    return {"status": "ran", "reason": report.get("reason", ""), "checked": report.get("judged", 0),
            "found": report.get("due", 0), "woke": len(started), "cost_usd": 0.0,
            "details": {"started": report.get("started", []), "held": report.get("held", []),
                        "over": report.get("over", 0), "limit": limit}}
