"""Wake limits for Maintenance (plan Amendment v2 section B). Find stuck, Update and Dream all wake bots through here.

The limits, enforced together:
- one Maintenance wake per bot per run (the live run's start is the window);
- three wakes per task a day (a rolling 24 hours);
- fifty wakes across the fleet an hour (a rolling hour).

A wake is written as events rows when it is recorded. One wake is one list: a `maintenance.wake` event against the bot,
however many tasks the list names, and one `maintenance.wake.task` event against each distinct task in it. So a list of
ten tasks is one wake toward the fleet cap and one wake against each of its tasks. The events are the ledger, so a restart
or a second process sees the same counts. Every count is by actor (Keeper), so each one reads the events_task_origin index
(actor, action, target, created_at).
"""
from .store import H

WAKES_PER_BOT_RUN = 1
WAKES_PER_TASK_DAY = 3
WAKES_PER_HOUR = 50
BOT_ACTION = "maintenance.wake"
TASK_ACTION = "maintenance.wake.task"
# INDEXED BY pins events_task_origin, as backend/batch.py does: the planner otherwise takes events_action_ts.
COUNT_TARGET = ("SELECT count(*) FROM events INDEXED BY events_task_origin "
                "WHERE actor=? AND action=? AND target=? AND created_at>=?")
COUNT_FLEET = "SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action=? AND created_at>=?"


def _count(c, action, target, since):
    return c.execute(COUNT_TARGET, (H.KEEPER, action, target, since)).fetchone()[0]


def allow(c, bot_id, task_id=None):
    """Whether one more Maintenance wake may go to this bot, for this task, now."""
    now = H.now()
    live = c.execute("SELECT started_at FROM maintenance_runs WHERE finished_at IS NULL "
                     "ORDER BY started_at DESC LIMIT 1").fetchone()
    # Outside a live run the window is the last hour, so a wake is never unlimited.
    if _count(c, BOT_ACTION, bot_id, live[0] if live else H.shift(now, hours=-1)) >= WAKES_PER_BOT_RUN:
        return False
    if task_id and _count(c, TASK_ACTION, task_id, H.shift(now, days=-1)) >= WAKES_PER_TASK_DAY:
        return False
    fleet = c.execute(COUNT_FLEET, (H.KEEPER, BOT_ACTION, H.shift(now, hours=-1))).fetchone()[0]
    return fleet < WAKES_PER_HOUR


def record(c, bot_id, task_ids=()):
    """Write down one Maintenance wake: one `maintenance.wake` event against the bot (one wake, however many tasks it
    names), and one `maintenance.wake.task` event per distinct task id, so each task's own cap counts its wakes."""
    task_ids = list(dict.fromkeys(task_ids))
    H.event(c, H.KEEPER, BOT_ACTION, bot_id, {"task_ids": task_ids} if task_ids else None)
    for task_id in task_ids:
        H.event(c, H.KEEPER, TASK_ACTION, task_id, {"bot_id": bot_id})
