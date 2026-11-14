"""Dream: the auto dream (backend/dream_auto.py), once a day, for up to "Most bots per run" bots (default 3). The verdict is
dream_auto's: the need (turns and people's corrections since the last dream), the lookback, the gaps and the waiting and
busy checks. Each dream is queued with its wake held on the release policy, so it starts while its bot is warm. No
customer tokens are counted here: a queued dream is a task, and its turns are the bot's own.
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
    # Only limiter refusals leave the daily attempt unused. A start or any other refusal still counts for the day.
    limited = report.get("limited", [])
    retry = bool(limited) and not started and len(limited) == len(report.get("held", []))
    return {
        "status": "skipped" if retry else "ran",
        "reason": "maintenance wake limits" if retry else report.get("reason", ""),
        "checked": report.get("judged", 0),
        "found": report.get("due", 0),
        "woke": len(started),
        "cost_usd": 0.0,
        "details": {
            "started": report.get("started", []),
            "held": report.get("held", []),
            "limited": limited,
            "over": report.get("over", 0),
            "limit": limit,
        },
    }
