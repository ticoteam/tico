"""Learn scores activity once and keeps each bot's top items for Update."""

from collections import Counter
from copy import copy
import json
import time

from .. import judge, learnings
from ..store import H


class _BudgetStore:
    """Use the run's cap while Decide reserves and meters each call."""

    def __init__(self, store, cap):
        self.store = store
        self.settings = copy(store.settings)
        self.settings.decisions_monthly_usd = cap

    def __getattr__(self, name):
        return getattr(self.store, name)


def _result(status, reason, checked=0, found=0, cost=0.0, details=None):
    return {"status": status, "reason": reason, "checked": checked, "found": found, "woke": 0,
            "cost_usd": cost, "details": details or {}}


def run(c, ctx):
    """Read on c; keep every write in the store's transactions, outside model calls."""
    from .. import maintenance
    if not ctx["settings"].get("enabled", False):
        return _result("skipped", "Learn is off")
    store, now, run_id = ctx["store"], ctx["now"], ctx["run_id"]
    previous = c.execute("SELECT * FROM learning_runs WHERE id=?", (run_id,)).fetchone()
    if previous and previous["status"] != "running":
        detail = json.loads(previous["detail"])
        status = "ran" if previous["status"] in ("done", "quiet") else "skipped"
        return _result(status, detail.get("reason", "already recorded"), previous["read_count"],
                       previous["routed_count"], detail.get("cost_usd", 0.0), detail)
    cap = ctx.get("cap")
    if isinstance(cap, dict):
        cap = cap.get("monthly_cap_usd")
    cap = float(store.settings.decisions_monthly_usd if cap is None else cap)
    budget = _BudgetStore(store, cap)
    capped = cap > 0 and judge.spent_this_month(c, now) >= cap
    engine = None if capped else learnings.engine_for(budget, c)
    detail = {"items_read": 0, "items_sent": {}, "saved": 0, "dropped": 0, "cost_usd": 0.0}
    started = time.monotonic()
    items, chosen, failed = [], {}, {}
    if engine is None:
        status = "capped" if capped else "unconfigured"
        reason = "monthly decision spend cap" if capped else "no decision model"
    else:
        snapshot = not c.in_transaction
        if snapshot:
            c.execute("BEGIN")
        try:
            team = learnings.roster(c, store.settings, now)
            items, overflow = learnings.collect(c, store.settings, ctx.get("window_end") or now, team)
        finally:
            if snapshot:
                c.execute("ROLLBACK")
        detail.update(items_read=len(items), read=dict(Counter(i["source"] for i in items)),
                      source_dropped=overflow, recipients=len(team))
        tally = learnings.Tally(engine)
        if items:
            qset = learnings.question_set(store.settings)
            with maintenance.heartbeat(store, run_id, ctx["step_id"]):
                failed = learnings.route(tally, qset, items, team)
            chosen = learnings.packets(items, qset, limit=15)
        capped = bool(failed.get("spend_cap"))
        if capped:
            chosen = {}
        detail.update(failed=failed, routes=dict(tally.routes), fallback=tally.fallback, cost_usd=round(tally.cost, 6))
        status = "capped" if capped else "done" if chosen else "quiet"
        reason = "monthly decision spend cap" if capped else "items kept for the next Update"
    detail.update(reason=reason, ms=round((time.monotonic() - started) * 1000))
    with store.transaction() as tx:
        if not maintenance._still_pending(tx, ctx["step_id"]):
            return _result("skipped", "claim lost", len(items), cost=detail["cost_usd"], details=detail)
        tx.execute("INSERT OR IGNORE INTO learning_runs(id,night,status,started_at) VALUES(?,?,'running',?)",
                   (run_id, learnings.night_of(H.parse_ts(now)), now))
        if chosen:
            learnings.dispatch(tx, run_id, "", items, chosen, team, detail)
        if engine is not None and not capped:
            learnings.advance(tx, items, ctx.get("window_end") or now)
        routed = tx.execute("SELECT count(*) FROM learning_items WHERE run_id=?", (run_id,)).fetchone()[0]
        tx.execute("UPDATE learning_runs SET status=?,finished_at=?,read_count=?,routed_count=?,detail=? WHERE id=?",
                   (status, H.now(), len(items), routed, json.dumps(detail), run_id))
        if items:
            H.event(tx, H.KEEPER, "judge.call", f"{learnings.QUESTION_SET}@run",
                    {"label": f"{learnings.QUESTION_SET}@run", "run": run_id, "items": len(items),
                     "failed": failed, "routed": routed, **tally.detail()})
    return _result("skipped" if status in ("capped", "unconfigured") else "ran", reason,
                   len(items), routed, detail["cost_usd"], detail)
