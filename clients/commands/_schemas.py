"""Argument schemas and the helpers that read arguments, shared by the tool modules.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

ASK_WAIT_MAX = 300


STATUSES = ("open", "done", "closed")  # a task's status; what it waits on is its `turn`, never a status


GOAL_STATUSES = ("red", "yellow", "green", "done", "dropped")  # what a person or bot sets by hand


PROPOSAL_KINDS = (
    "goal_wording",
    "goal_kpi",
    "kpi_definition",
    "kpi_target",
    "flag",
    "goal_why",
    "recovery",
    "strategy_draft",
    "brand_draft",
)


def _s(description, **extra):
    return {"type": "string", "description": description, **extra}


TASK_ID = {
    "type": "string",
    "description": "Task id: the full id, or 8+ leading characters (`short_id` in tico_task_list)",
}


# How a send reaches its bot (backend/delivery.py), the same words on every send.
DELIVERY = {
    "type": "string",
    "enum": ["steer", "queue", "defer"],
    "description": "To a bot: steer goes into its running turn now (right after it if its harness can't "
    "steer), queue right after its current turn there (or now if idle), defer wakes nobody and "
    "goes in with its next turn there, at the latest after defer_for",
}


DEFER_FOR = {
    "type": "string",
    "pattern": "^(never|[1-9][0-9]{0,5}[mhd])$",
    "description": "With defer: the latest it waits, 30m, 4h, 2d or never",
}


def _delivery(args):
    """The request's delivery fields. A defer_for alone (the tico CLI's --defer) is a defer."""
    way = args.get("delivery") or ("defer" if args.get("defer_for") is not None else None)
    if not way:
        return {}
    return {"delivery": way, **({"defer_for": args["defer_for"]} if way == "defer" and args.get("defer_for") else {})}


def _refs(values):
    out = {}
    for value in values or []:
        kind, _, ident = str(value).partition(":")
        out.setdefault(kind, []).append(ident)
    return out


def _target(api, value):
    if value in ("me", "self"):
        return api.get("me")["actor"]
    return value


def _key(args, suffix=""):
    op = args.get("operation_id")
    return (op + suffix) if op else None
