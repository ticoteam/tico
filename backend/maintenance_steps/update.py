"""Maintenance step: Update. Starts the day's update cycle once a day, from START_HOUR Pacific.

The step builds `update_queue` (updates.build_queue, which does nothing once today's queue exists), then decides each
bot's request before the queue's pump sends it:
- a bot that did no work since its last update gets no request (`skipped`, the zero-work skip);
- a bot with no work but learnings or suggestions waiting gets one delivery-only turn that writes no update
  (`delivery_only`; that turn ends `delivered_without_update` and is never retried: close call D);
- every other bot is asked, as before.

The pump (`updates.dispatch`, scheduler.py's tick) sends the rest one at a time, so the step returns `pending` while
they go out. It is not wired into the scheduler yet: PR1 registers this step and switches off the old 05:00 start.
"""
from .. import updates
from ..store import H

def did_work(c, bot_id, since):
    """Whether the bot settled a turn that wrote something after `since` (an ISO timestamp; "" for all of it).

    Work is a settled turn with did_nothing = 0 (set when the turn settles, run_triggers.SETTLE_SQL). A failed turn is
    not work. Messages a bot sends and the commits it makes to its memory do not count: bots reply on every wake and
    rewrite their memory, so every woken bot would look like it worked and the zero-work skip would never fire.
    Bounded on attempts.created_at (indexed with bot_id by attempts_bot_created)."""
    return c.execute("SELECT 1 FROM attempts WHERE bot_id=? AND created_at>? AND did_nothing=0 "
                     "AND coalesce(state,'')<>'failed' LIMIT 1", (bot_id, since)).fetchone() is not None


def _since(c, bot):
    """Where a bot's work is counted from: its last update of either kind, or all of it before its first."""
    return c.execute("SELECT max(created_at) FROM updates WHERE bot=?", (bot,)).fetchone()[0] or ""


def _reports_will_post(c, bot, day, since, chart):
    """Whether a direct report posted an update since `since`, or is asked today for one (its row is queued or out and
    is not a delivery turn). The queue is decided deepest first, so a report is settled before its manager is. A
    manager with no work of its own still writes the roll-up of a report that did work, so it is asked."""
    reports = updates.direct_reports(c, bot, chart)
    if not reports:
        return False
    marks = ",".join("?" * len(reports))
    if c.execute(f"SELECT 1 FROM updates WHERE bot IN ({marks}) AND created_at>? LIMIT 1",
                 (*reports, since)).fetchone():
        return True
    return c.execute(f"SELECT 1 FROM update_queue WHERE day=? AND redo=0 AND delivery_only=0 AND state IN ('queued','sent') "
                     f"AND bot IN ({marks}) LIMIT 1", (day, *reports)).fetchone() is not None


def plan_request(c, bot, day, chart=None):
    """What a bot's request today is: "ask" (it did work since its last update, or a direct report will post one), "deliver"
    (no work, but learnings or suggestions are waiting: a delivery-only turn), or "skip" (no work and nothing waiting)."""
    since = _since(c, bot)
    if did_work(c, bot, since) or _reports_will_post(c, bot, day, since, chart or updates._chart(c)):
        return "ask"
    slots = updates.request_slots(c, bot)
    return "deliver" if slots["learnings"] or slots["suggestions"] else "skip"


def _result(status, reason, checked=0, found=0, details=None):
    return {"status": status, "reason": reason, "checked": checked, "found": found, "woke": 0, "cost_usd": 0.0,
            "details": details or {}}


def run(c, ctx):
    """Start today's cycle (once a day, from START_HOUR) and decide its requests; `pending` while the pump sends them."""
    local = updates.local_now(ctx.get("now"))
    if local.hour < updates.START_HOUR:
        return _result("skipped", f"before {updates.START_HOUR:02d}:00 Pacific: today's cycle has not started")
    day = local.date().isoformat()
    kind = updates.kind_for(day)
    settings = ctx["settings"]
    writer = updates.writer_for(c, settings, ctx.get("store"))
    built = updates.build_queue(c, day, kind, settings.assistant_bot if writer else None)
    chart = updates._chart(c)
    skipped = delivered = asked = 0
    rows = c.execute("SELECT * FROM update_queue WHERE state='queued' AND day=? AND redo=0 AND bot NOT LIKE 'human:%' "
                     "ORDER BY rank", (day,)).fetchall()
    for row in rows:
        decided = plan_request(c, row["bot"], day, chart)
        if decided == "skip":
            updates._finish(c, row, "skipped", updates.NO_WORK)
            skipped += 1
            continue
        c.execute("UPDATE update_queue SET delivery_only=? WHERE id=?", (int(decided == "deliver"), row["id"]))
        delivered += decided == "deliver"
        asked += decided == "ask"
    queued = c.execute("SELECT count(*) FROM update_queue WHERE state='queued' AND day=?", (day,)).fetchone()[0]
    details = {"day": day, "kind": kind, "built": built, "asked": asked, "delivery_only": delivered,
               "skipped": skipped, "queued": queued}
    reason = (f"today's {kind} cycle: {queued} requests queued, {skipped} skipped (no work since their last update), "
              f"{delivered} delivery-only")
    return _result("pending" if queued else "ran", reason, checked=len(rows), found=asked + delivered, details=details)
