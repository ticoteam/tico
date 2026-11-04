"""The Maintenance run's wake limits, as a stub until PR1 lands its real module (PR1 replaces this file at rebase).

The real module enforces: at most one Maintenance wake per bot per run, at most three wakes per task per day, and at
most fifty wakes an hour across the fleet. Find stuck, Update and Dream go through these two calls. `allow` is asked per
item; `record` is called once per delivered wake, with the bot and every task the wake named, so one list counts as one
bot wake and one wake per task. Until PR1 lands every wake is allowed and nothing is counted.
"""


def allow(c, bot_id, task_id=None):
    """Whether Maintenance may wake `bot_id` (about `task_id`, when a task is named) now."""
    return True


def record(c, bot_id, task_ids=()):
    """Count one delivered Maintenance wake of `bot_id`, about each of `task_ids`."""
    return None
