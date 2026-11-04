"""Wake limits for Maintenance (plan Amendment v2 section B). Find stuck, Update and Dream all wake bots through here.

The limits, enforced together:
- one Maintenance wake per bot per run (the live run's start is the window);
- three wakes per task a day (a rolling 24 hours);
- fifty wakes across the fleet an hour (a rolling hour).

A wake is written as an events row when it is recorded: `maintenance.wake` against the bot, and `maintenance.wake.task`
against the task. The events are the ledger, so a restart or a second process sees the same counts.
"""
from .store import H

WAKES_PER_BOT_RUN = 1
WAKES_PER_TASK_DAY = 3
WAKES_PER_HOUR = 50
BOT_ACTION = "maintenance.wake"
TASK_ACTION = "maintenance.wake.task"


def _count(c, action, target, since):
    return c.execute("SELECT count(*) FROM events WHERE action=? AND target=? AND created_at>=?",
                     (action, target, since)).fetchone()[0]


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
    fleet = c.execute("SELECT count(*) FROM events WHERE action=? AND created_at>=?",
                      (BOT_ACTION, H.shift(now, hours=-1))).fetchone()[0]
    return fleet < WAKES_PER_HOUR


def record(c, bot_id, task_id=None):
    """Write down one Maintenance wake to a bot, and to its task when it has one."""
    H.event(c, H.KEEPER, BOT_ACTION, bot_id, {"task_id": task_id} if task_id else None)
    if task_id:
        H.event(c, H.KEEPER, TASK_ACTION, task_id, {"bot_id": bot_id})
