"""Collect: the per-bot "did work" record that Update reads for its zero-work skip (docs/maintenance.md, plan 2.1).

A bot did work in the window when one of its settled turns wrote something: an attempt with `did_nothing` 0 (the turn made
a write Tico counts). Messages and memory commits do not count. Bots rewrite their memory and post a reply on every wake,
including a "nothing new" one, so those would make every woken bot look busy. No model, and it is never skipped. Its result
is the record: `details.bots` lists the bots that did work, on the step's run row.
"""
from ..store import H

MAX_LISTED = 200    # the record's bot ids, kept on the run row; the count in `found` is never cut


def did_work(c, since, until):
    """The set of bot ids with a turn settled in [since, until) that wrote something. A failed attempt does not count,
    even one that recorded a write: its work did not finish. The `created_at` bound lets the attempts_created index serve
    the read, so an attempt that started more than a day before the window is not read. state is nullable, so the
    filter is coalesced: a NULL state is not failed."""
    return {bot_id for (bot_id,) in c.execute(
        "SELECT DISTINCT bot_id FROM attempts WHERE created_at>=? AND settled_at>=? AND settled_at<? "
        "AND coalesce(did_nothing, 0)=0 AND coalesce(state, '') <> 'failed'", (H.shift(since, days=-1), since, until))}


def run(c, ctx):
    since, until = ctx["window_start"], ctx["window_end"]
    settled = c.execute("SELECT count(*) FROM attempts WHERE created_at>=? AND settled_at>=? AND settled_at<? "
                        "AND coalesce(state, '') <> 'failed'", (H.shift(since, days=-1), since, until)).fetchone()[0]
    bots = sorted(did_work(c, since, until))
    return {"status": "ran", "reason": f"{len(bots)} bot{'s' if len(bots) != 1 else ''} did work",
            "checked": settled, "found": len(bots), "woke": 0, "cost_usd": 0.0,
            "details": {"since": since, "until": until, "bots": bots[:MAX_LISTED], "bots_total": len(bots),
                        "stamp": H.now()}}
