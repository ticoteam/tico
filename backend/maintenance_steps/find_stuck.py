"""Find stuck: the Maintenance step that gives each bot its task roundup list (backend/task_roundup.py).

The roundup decides what a bot's list names: one grouped list per bot per run, nothing to a person, and a registry key per
line so nothing is listed twice. This step runs it on Maintenance's schedule. The bots with a list are chosen by a read
on the connection; each is then sent its list in its own short transaction, under the wake limiter
(backend/maintenance_limits.py). Until the cutover the scheduler's roundup timer is the one that runs, so only one of the
two sends a run's lists.
"""

from .. import hubdb as H
from .. import maintenance_limits as LIMITS
from .. import task_roundup


def run(c, ctx):
    """One pass. `c` is a read connection; `ctx` carries `store` (the transactions each send runs in) and `now` (the run's
    time, the wall clock otherwise). Returns the step result: checked (active bots), found (lines on the chosen lists),
    woke (lists delivered), and the names of the bots it woke, the bots the limiter refused and the lines it sent."""
    ctx = ctx or {}
    at = ctx.get("now") or H.now()
    store = ctx["store"]
    checked = c.execute("SELECT count(*) FROM bots WHERE state='active'").fetchone()[0]
    plans = task_roundup.discover(c, at)
    found = sum(plan["lines"] for plan in plans)
    woken, refused, listed = [], [], 0
    for plan in plans:
        slug = plan["slug"]
        with store.transaction() as tx:
            if not LIMITS.allow(tx, slug):
                refused.append(slug)            # the limiter holds this bot: it is not sent, and no list is recorded
                continue
            count, task_ids = task_roundup.send_detail(tx, plan)
            if count:
                LIMITS.record(tx, slug, task_ids)
                woken.append(slug)
                listed += count
    return {"status": "ran", "reason": "" if woken else "nothing to list", "checked": checked, "found": found,
            "woke": len(woken), "cost_usd": 0.0,
            "details": {"woken": woken, "refused": refused, "listed": listed}}
