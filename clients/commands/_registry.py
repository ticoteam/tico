"""The table itself: TOOLS, `tool()` that fills it, CLI_TOOL_ALIASES, and the server's name, version and instructions.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import os
import re
from pathlib import Path
from clients.agent_skill import WHO_NEEDS_ME

from clients.commands._schemas import _s


PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


def release_version():
    value = os.environ.get("TICO_VERSION", "").strip()
    if not value:
        try:
            value = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
        except OSError:
            value = ""
    return re.sub(r"^v(?=\d)", "", value) or "dev"


SERVER_INFO = {"name": "tico", "title": "Tico", "version": release_version()}


# What an agent reads on connect: the hub in one line, then the skill a person's own agent
# follows to work their bots (clients/agent_skill.py). Bots ignore the skill; it is for people.
INSTRUCTIONS = (
    "Tico: tasks, messages, Decisions, status. "
    "Every rule is enforced server-side; a refusal says which.\n\n" + WHO_NEEDS_ME
)


TOOLS = []


CLI_TOOL_ALIASES = {
    "tico_rules_due_list": ("tico_rules_due",),
    "tico_rules_due_add": ("tico_rules_due_set",),
    "tico_rules_read": ("tico_rules_get", "tico_rules_read"),  # `tico rules read <id>` or, with --from, a document
    "tico_task_child": ("tico_task_child_create",),
    "tico_task_unfollow": ("tico_task_follow",),  # `tico task unfollow` is tico_task_follow with unfollow
    "tico_automation_show": ("tico_automation_get",),
    "tico_automation_inbox": ("tico_inbox_send",),
    "tico_repo_tick": ("tico_repo_update",),
    "tico_repo_untick": ("tico_repo_update",),
    "tico_bot_repos": ("tico_bot_repos_get", "tico_bot_repos_set"),
    "tico_general_manager_status": ("tico_general_manager",),
    "tico_general_manager_on": ("tico_general_manager_set",),
    "tico_general_manager_off": ("tico_general_manager_set",),
    "tico_general_manager_schedule": ("tico_general_manager_set",),
    "tico_general_manager_bot": ("tico_general_manager_set",),
    "tico_maintenance_status": ("tico_maintenance",),
    "tico_task_show": ("tico_task_show", "tico_task_show_many"),  # `tico task show a b c` is one read
    "tico_calendar_list": ("tico_calendar_list", "tico_calendar_events"),  # `tico calendar list --who` reads events
    "tico_market_size_accept": ("tico_market_size_decide",),
    "tico_market_size_reject": ("tico_market_size_decide",),
    "tico_market_size_params": ("tico_market_size_decide",),
    "tico_auth_status": ("tico_whoami",),  # `tico auth status` is `tico whoami`, as `gh auth status`
}


def tool(name, description, properties, required=(), *, writes=False, local=False):
    """Register one tool; the decorated function is `fn(api, args) -> result`. A `local` tool runs on the
    computer that runs the bot and is served only by the local MCP server (`clients/hubmcp.py`), never by
    the Tico server's own endpoint (backend/mcp.py): it reaches out to the internet, and the server must not."""
    schema = {"type": "object", "properties": dict(properties), "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    if writes:
        schema["properties"]["operation_id"] = _s(
            "Stable id so a retry lands once. "
            + ("This tool requires the id." if "operation_id" in schema.get("required", []) else "Omit for a fresh id.")
        )

    def register(fn):
        TOOLS.append(
            {
                "name": name,
                "description": description,
                "inputSchema": schema,
                "writes": writes,
                "local": local,
                "fn": fn,
            }
        )
        return fn

    return register
