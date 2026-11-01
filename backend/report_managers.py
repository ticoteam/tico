"""Managers and their reports (docs/permissions.md): a bot changes its direct reports' model, task model, and name and
description, as itself. Two switches turn that off: the team rule `managers_manage_reports` and the manager's own
`manages_reports`, on by default. `Auth.report_manager` decides who may make a change; this module names the parts."""
import json

from . import team_rules

# The `via` every change a manager bot makes to its report is recorded with (settings_changes, the events it writes).
VIA = "report_manager"
# The scopes a manager bot may change on a report: its model, its task model, and its name and description.
SCOPES = ("model", "task_model", "definition")
# What of a report's definition a manager changes: its name and description, and nothing else.
NAME_FIELDS = ("display_name", "description")
# The bot's own switch, in its config: `false` turns it off, anything else (or nothing) leaves it on.
KEY = "manages_reports"


def switch_on(config):
    """Whether a bot's own switch lets its manager bot change its reports: on unless it was turned off."""
    return (config or {}).get(KEY) is not False


def manages(c, slug):
    """Whether bot `slug` manages its direct reports now: the team rule and its own switch both on."""
    if not team_rules.load(c)["managers_manage_reports"]:
        return False
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()
    return bool(row) and switch_on(json.loads(row["config"]) if row["config"] else {})


# ---- creating, archiving and restoring a direct report ----------------------------------------------------------
# A manager bot's own token may not do any of these. BotOps does them on the manager's ask, as that bot
# (`Auth.report_lifecycle`), and only for its direct reports. The change is recorded on the report as a `lifecycle`
# change with via `report_manager`, and every one leaves an event naming the manager.
LIFECYCLE_OPS = ("create", "archive", "restore")
LIFECYCLE = "lifecycle"
EVENT = "bot.report_lifecycle"
WINDOW_DAYS = 7
# Each cap counts the operations it covers, through BotOps, in the rolling window; the team rule holds the limit.
CAPS = {"create": ("report_creates_per_week", ("create",)),
        "archive": ("report_archives_per_week", ("archive", "restore"))}


def cap_used(c, manager, op, now):
    """How many operations under cap `op` this manager bot made through BotOps in the last WINDOW_DAYS."""
    from .store import H
    ops = CAPS[op][1]
    since = H.shift(now, days=-WINDOW_DAYS)
    return c.execute("SELECT count(*) FROM events INDEXED BY events_task_origin WHERE action=? AND actor=? AND ts>=? "
                     f"AND json_extract(detail,'$.op') IN ({','.join('?' * len(ops))})",
                     (EVENT, "bot:" + manager, since, *ops)).fetchone()[0]


def cap_limit(c, op):
    """The team's limit for cap `op` this week (team_rules), 0 when the owner turned it off."""
    return team_rules.load(c)[CAPS[op][0]]


def person_above(c, slug):
    """The person a bot reports up to: the first human on its `reports_to` chain, or "" when none."""
    seen, parent = {slug}, ""
    row = c.execute("SELECT reports_to,config FROM bot_config WHERE bot=?", (slug,)).fetchone()
    if row:
        parent = row["reports_to"] or (json.loads(row["config"] or "{}") or {}).get("reports_to") or ""
    while parent and parent not in seen:
        if parent.startswith("human:"):
            return parent
        seen.add(parent)
        row = c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (parent,)).fetchone()
        parent = (row["reports_to"] if row else "") or ""
    return ""
