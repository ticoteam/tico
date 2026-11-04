"""Collect: the per-bot "did work" record that Update reads for its zero-work skip (docs/maintenance.md, plan 2.1).

A bot did work in the window when one of its turns changed something (a task, a comment or a report) or it posted a
message, or it committed memory. A turn that was only a wake answered "nothing new" does not count. No model, and it is
never skipped. Its result is the record: `details.bots` lists the bots that did work, on the step's run row.
"""
from ..store import H

MAX_LISTED = 200    # the record's bot ids, kept on the run row; the count in `found` is never cut


def did_work(c, since, until):
    """The set of bot ids that did work in [since, until)."""
    bots = set()
    for (bot_id,) in c.execute("SELECT DISTINCT bot_id FROM attempts WHERE settled_at>=? AND settled_at<? "
                               "AND coalesce(did_nothing, 0)=0", (since, until)):
        bots.add(bot_id)
    for (sender,) in c.execute("SELECT DISTINCT sender_id FROM messages WHERE sender_id LIKE 'bot:%' "
                               "AND created_at>=? AND created_at<?", (since, until)):
        bots.add(sender[len("bot:"):])
    for (bot_id,) in c.execute("SELECT DISTINCT bot FROM bot_memory_updates WHERE committed_at>=? AND committed_at<?",
                               (since, until)):
        bots.add(bot_id)
    return bots


def run(c, ctx):
    since, until = ctx["window_start"], ctx["window_end"]
    settled = c.execute("SELECT count(*) FROM attempts WHERE settled_at>=? AND settled_at<?",
                        (since, until)).fetchone()[0]
    bots = sorted(did_work(c, since, until))
    return {"status": "ran", "reason": f"{len(bots)} bot{'s' if len(bots) != 1 else ''} did work",
            "checked": settled, "found": len(bots), "woke": 0, "cost_usd": 0.0,
            "details": {"since": since, "until": until, "bots": bots[:MAX_LISTED], "bots_total": len(bots),
                        "stamp": H.now()}}
