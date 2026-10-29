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
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
    return bool(row) and switch_on(json.loads(row["config_json"]) if row["config_json"] else {})
