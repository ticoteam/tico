"""Find stuck: the Maintenance step that gives each bot its task roundup list (backend/task_roundup.py).

The roundup decides what a bot's list names: one grouped list per bot per run, nothing to a person, and a registry key per
line so nothing is listed twice. This step runs it on Maintenance's schedule. Bots are chosen least recently sent first,
then oldest line. The age comes from the lines the list would show. The bots are chosen by a read on the connection, and at
most the step's max_bots (DEFAULT_MAX_BOTS when it sets none) are sent per run; the rest keep their lines for the next run.
Each bot is sent in its own short transaction under the wake limiter
(backend/maintenance_limits.py): the bot once, and each task its list names, which the limiter may hold for the day. Those
lines are dropped and the rest of the list is sent. The scheduler's standalone roundup timer is off; Maintenance owns
scheduled lists, while the legacy manual roundup remains available.
"""

from .. import hubdb as H
from .. import maintenance_limits as LIMITS
from .. import task_roundup

DEFAULT_MAX_BOTS = 10     # bots sent a list in one run when the step's settings name no max_bots


def run(c, ctx):
    """One pass. `c` is a read connection; `ctx` carries `store` (the transactions each send runs in), `now` (the run's time,
    the wall clock otherwise) and `settings` (the step row's; max_bots caps the bots sent). Returns the step result: checked
    (active bots), found (lines on the chosen lists), woke (lists delivered), and the names of the bots it woke, the bots the
    limiter refused, the tasks whose lines the per-task limit held back, and the lines it sent."""
    ctx = ctx or {}
    at = ctx.get("now") or H.now()
    store = ctx["store"]
    setting = (ctx.get("settings") or {}).get("max_bots")
    max_bots = DEFAULT_MAX_BOTS if setting is None else int(setting)
    checked = c.execute("SELECT count(*) FROM bots WHERE state='active'").fetchone()[0]
    plans = sorted(task_roundup.discover(c, at), key=lambda plan: (
        task_roundup._when(task_roundup._mark(c, task_roundup.SENT_KEY + plan["slug"])), plan["oldest"]))
    found = sum(plan["lines"] for plan in plans)
    woken, refused, limited, listed = [], [], [], 0
    for plan in plans:
        if len(woken) >= max_bots:
            break                             # the rest keep their lines: the next run lists them
        slug = plan["slug"]
        with store.transaction() as tx:
            if not LIMITS.allow(tx, slug):
                refused.append(slug)            # the limiter holds this bot: it is not sent, and no list is recorded
                continue
            held = set()

            def allowed(tid, tx=tx, slug=slug, held=held):
                if LIMITS.allow(tx, slug, tid):
                    return True
                held.add(tid)
                return False

            count, task_ids = task_roundup.send_detail(tx, plan, allowed=allowed)
            limited += sorted(held)
            if count:
                LIMITS.record(tx, slug, task_ids)
                woken.append(slug)
                listed += count
    return {"status": "ran", "reason": "" if woken else "nothing to list", "checked": checked, "found": found,
            "woke": len(woken), "cost_usd": 0.0,
            "details": {"woken": woken, "refused": refused, "limited": limited, "listed": listed}}
