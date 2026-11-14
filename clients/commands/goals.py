"""Goals, KPIs and proposals.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import argparse
import json
import re

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import GOAL_STATUSES, PROPOSAL_KINDS, _key, _s, _target
from clients.commands._requester import _asked
from clients.commands._shared import json_file, text_file
from clients.tico import APIError


# ----------------------------------------------------------------------------- goals
GOAL_YES = _s(
    "The owner's agreement to a goal read-back: turn or its message id. On not_agreed read back again, never retry the save."
)


@tool(
    "tico_goal_read_back",
    "Tico writes and stores the read-back in the owner's Strategy Owner chat. "
    "Saves nothing until the owner agrees. Read back the whole plan at once. "
    "Returns message_id, text and exact method/path/body steps; save agreed steps with tico_goal_save. "
    "Kinds: goal_create, goal_update, measure_add, target_set, measure_remove, suggestion.",
    {
        "changes": {
            "type": "array",
            "items": {"type": "object"},
            "minItems": 1,
            "maxItems": 500,
            "description": "the whole plan, 1 to 500 changes, each its kind and that change's fields (on a command line, "
            "as JSON)",
        }
    },
    required=("changes",),
    writes=True,
)
def goal_read_back(api, args):
    changes = args["changes"]
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            raise ValueError("changes must be a JSON array of 1 to 500 change objects") from None
    if not isinstance(changes, list) or not 1 <= len(changes) <= 500 or any(not isinstance(c, dict) for c in changes):
        raise ValueError("changes must be a JSON array of 1 to 500 change objects")
    return api.post("goals/read-back", {"changes": changes}, key=_key(args))


@tool(
    "tico_goal_save",
    "Save one exact method/path/body step returned by tico_goal_read_back after the owner agrees. "
    "For later steps of a new goal, created_goal is the id from its first save. On not_agreed read back again; do not retry. "
    "Also records the owner's \"no\" to a suggestion you made in chat: path /api/v2/goal-proposals/turned-down, with the "
    "suggestion's kind, goal_id, kpi_id, payload and reason as the body; the owner's words become its reason.",
    {
        "method": {"type": "string", "enum": ["POST"]},
        "path": _s("The step's /api/v2 path"),
        "body": _s("The step's exact body string, unchanged"),
        "created_goal": _s("First save's goal id for {new}"),
        "on_behalf_of": GOAL_YES,
    },
    required=("method", "path", "body", "on_behalf_of"),
    writes=True,
)
def goal_save(api, args):
    path = args["path"]
    if "{new}" in path:
        created = args.get("created_goal")
        if not created or not re.fullmatch(r"[A-Za-z0-9_-]+", created):
            raise ValueError("created_goal must be the goal id from the first save")
        path = path.replace("{new}", created)
    # This helper only replays goal read-back writes; the server checks the cited yes and exact body.
    if args["method"] != "POST" or not re.fullmatch(
        r"/api/v2/(?:goals(?:/[^/]+(?:/why|/kpis(?:/[^/]+(?:/unlink)?)?)?)?|goal-proposals/(?:[^/]+/decide|turned-down))",
        path,
    ):
        raise ValueError("path must be a goal read-back save step")
    if not args.get("on_behalf_of"):
        raise ValueError("cite the owner's yes with on_behalf_of")
    try:
        body = json.loads(args["body"])
    except ValueError:
        raise ValueError("body must be a JSON object") from None
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return _asked(api, args).post(path.removeprefix("/api/v2/"), body, key=_key(args))


@tool(
    "tico_goal_list",
    "What you are for: your goals in order, the goals they support, and your reports' goals. Read this before you read a task. `all` is every "
    "live goal in the team.",
    {
        "owner": _s("Someone else's: a bot slug or a person id; default is yourself"),
        "all": {"type": "boolean", "default": False},
        "status": _s("With `all`: only these, comma-separated (red,yellow,green,gray,done,dropped)"),
    },
)
def goals(api, args):
    if args.get("all"):
        return api.get("goals", all="1", status=args.get("status") or None)["goals"]
    return api.get("goals", owner=_target(api, args["owner"]) if args.get("owner") else None)


@tool(
    "tico_goal_show",
    "One goal: its body, KPIs with their latest readings, the goals it serves and "
    "the goals under it, the tasks naming it, and its history.",
    {"id": _s("Goal id")},
    required=("id",),
)
def goal_show(api, args):
    return api.get("goals/" + args["id"])["goal"]


@tool(
    "tico_goal_create",
    "Set a goal for yourself, under a goal you own, or for someone below "
    "you on the org chart. A parent is optional: with none the goal is simply not linked. With one "
    "it supports that goal. It starts with no colour, which its data or a person (its owner, the owner of "
    "the goal it serves, or someone above them) sets.",
    {
        "owner": _s("`me`, a bot slug, or a person id (`company` is the owner's)"),
        "title": _s("Plain English: what you are going for"),
        "parent_id": _s("The goal this one supports, if any; leave it out when there is none"),
        "body": _s("What it means, what counts, what does not", default=""),
        "top": {"type": "boolean", "default": False, "description": "Put it first in the owner's order"},
        "bet_id": _s("The strategy bet it serves (`strategy show` lists them)"),
        "on_behalf_of": GOAL_YES,
    },
    required=("owner", "title"),
    writes=True,
)
def goal_create(api, args):
    return _asked(api, args).post(
        "goals",
        {
            "owner": _target(api, args["owner"]),
            "title": args["title"],
            "parent_id": args.get("parent_id"),
            "body": args.get("body") or "",
            "top": bool(args.get("top")),
            "bet_id": args.get("bet_id") or None,
        },
        key=_key(args),
    )["goal"]


@tool(
    "tico_goal_status",
    "Set a goal's colour by hand: red, yellow or green with one honest sentence; "
    "done when reached, dropped when it stops mattering. A colour set by hand sticks, with your name and "
    "note, until a person hands it back (status `auto`); the Strategy Owner only suggests a different one. "
    "`auto` lets the Strategy Owner set the colour again: it ends a colour set by hand and works the automatic one "
    "out now, from the goal's KPIs, or its owner's check-ins and tasks. The goal's owner, the owner of the goal "
    "it serves, or someone above them.",
    {
        "id": _s("Goal id"),
        "status": {"type": "string", "enum": list(GOAL_STATUSES) + ["auto"]},
        "note": _s("One sentence: why it is this colour"),
    },
    required=("id", "status"),
    writes=True,
)
def goal_status(api, args):
    if args["status"] == "auto":
        return api.post(f"goals/{args['id']}/status/auto", {}, key=_key(args))["goal"]
    return api.post(
        f"goals/{args['id']}/status", {"status": args["status"], "note": args.get("note") or ""}, key=_key(args)
    )["goal"]


@tool(
    "tico_goal_refresh",
    "The Strategy Owner's status pass: work the automatic colour of every live goal (or "
    "the ones named) out again. A goal whose colour a person set only gets a visible suggestion. "
    "Returns {changed, suggested, checked}. The Strategy Owner or the team owner.",
    {"goal_ids": {"type": "array", "items": {"type": "string"}, "description": "Only these goals; default all"}},
    writes=True,
)
def goal_refresh(api, args):
    return api.post("goals/refresh", {"goal_ids": args.get("goal_ids") or None}, key=_key(args))


@tool(
    "tico_goal_checkin",
    "Record how the owner says a goal is going, in their words: interpretation, kept "
    "apart from the readings (facts). It colours a goal that has no KPI. The goal's owner, someone above "
    "them, or the Strategy Owner recording an owner's answer (`from_actor`).",
    {
        "id": _s("Goal id"),
        "body": _s("What is going on, in a sentence or two"),
        "signal": {"type": "string", "enum": ["on_track", "at_risk", "off_track"]},
        "from_actor": _s("Whose words these are, when you record them for someone else: a bot slug or a person id"),
        "kpi_id": _s("The KPI that prompted it, if any"),
    },
    required=("id", "body"),
    writes=True,
)
def goal_checkin(api, args):
    body = {
        "body": args["body"],
        "signal": args.get("signal"),
        "kpi_id": args.get("kpi_id"),
        "from_actor": _target(api, args["from_actor"]) if args.get("from_actor") else None,
    }
    return api.post(f"goals/{args['id']}/checkins", body, key=_key(args))["checkin"]


@tool("tico_goal_checkin_list", "A goal's check-ins, newest first.", {"id": _s("Goal id")}, required=("id",))
def goal_checkins(api, args):
    return api.get(f"goals/{args['id']}/checkins")


@tool(
    "tico_goal_needs_you",
    "What waits on you among goals: red KPIs on goals you own, stale KPIs you own, "
    "and definition or target changes you are asked to confirm.",
    {},
)
def goal_needs_you(api, args):
    return api.get("goals/needs-you")


@tool(
    "tico_goal_company",
    "The company's Vision and Description (the Company section at the top of Goals, "
    "where the Strategy Owner is on), empty until a person writes them. Read-only: only the owner or an admin "
    "changes them.",
    {},
)
def goal_company(api, args):
    return api.get("company")


@tool(
    "tico_goal_strategy_bot",
    "The Strategy Owner's name, how much it does (level 1 Passive, "
    "2 suggests actions, 3 acts on its "
    "own; only the owner changes it), whether its regular checks run, and how many items it put in each person's "
    "Needs you today against its cap of 5.",
    {},
)
def goal_strategy_bot(api, args):
    return api.get("strategy-bot")


@tool(
    "tico_goal_strategy_bot_merge",
    "The Strategy Owner only, at level 2 or 3: "
    "when several bots ask a person the same thing, "
    "make them one item in that person's Needs you. The originals leave their Needs you while it is open; their one "
    "answer goes to every bot that asked. Counts toward the 5 a day.",
    {
        "person": _s("The person they all wait on: a person id"),
        "title": _s("The merged item's title"),
        "question": _s("The one question that answers them all"),
        "tasks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2 to 10 task ids waiting on that person",
        },
    },
    required=("person", "title", "question", "tasks"),
    writes=True,
)
def goal_strategy_bot_merge(api, args):
    return api.post(
        "strategy-bot/merge",
        {
            "person": _target(api, args["person"]),
            "title": args["title"],
            "question": args["question"],
            "tasks": list(args["tasks"]),
        },
        key=_key(args),
    )


@tool(
    "tico_goal_strategy_bot_level3_offer",
    "The Strategy Owner only: record that its Monday note offered level 3, once "
    "`level3_offer.due` in tico_goal_strategy_bot is true (10 recoveries approved in a row "
    "unchanged, none offered in 30 "
    "days). Only a person raises the level.",
    {},
    writes=True,
)
def goal_strategy_bot_level3_offer(api, args):
    return api.post("strategy-bot/level3-offer", {}, key=_key(args))


@tool(
    "tico_goal_strategy_bot_decide",
    "The Strategy Owner only, at level 3: "
    "make one decision alone, with its reason: 1 to 10 "
    "changes to bot tasks (create a task for a bot, reorder one to the top of its queue, reassign one to another "
    "bot, close one), logged under one action id with an Undo for the owner. A task for a person, spending, "
    "publishing, outside sends, bots, goals, targets, the vision and instructions stay the owner's: those parts, and "
    "any a bot offline can't take, become one proposal in their Needs you.",
    {
        "reason": _s("Why, in one sentence the owner reads in the log"),
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "do": {"type": "string", "enum": ["create", "reorder", "reassign", "close"]},
                    "task_id": _s("The bot task to reorder, reassign or close"),
                    "owner": _s("The bot for a new or reassigned task"),
                    "title": _s("A new task's title"),
                    "body": _s("A new task's body"),
                    "note": _s("Why a task closes"),
                },
                "required": ["do"],
            },
            "description": "1 to 10 changes (on a command line, as JSON): {\"do\": \"create\", \"owner\": \"<bot>\", "
            '"title": "..."}, {"do": "reorder" or "close", "task_id": "..."}, {"do": "reassign", "task_id": "...", '
            '"owner": "<bot>"}',
        },
        "goal_id": _s("The goal this serves, if any"),
    },
    required=("reason", "changes"),
    writes=True,
)
def goal_strategy_bot_decide(api, args):
    changes = args["changes"]
    if isinstance(changes, str):  # the CLI passes the list as JSON
        try:
            changes = json.loads(changes)
        except ValueError as exc:
            raise ValueError(f"changes is a JSON list of changes: {exc}") from None
    body = {"reason": args["reason"], "changes": list(changes)}
    if args.get("goal_id"):
        body["goal_id"] = args["goal_id"]
    return api.post("strategy-bot/decisions", body, key=_key(args))


@tool(
    "tico_goal_strategy_bot_log",
    "The Strategy Owner log, newest first: "
    "each decision it made alone and each thing it proposed "
    "instead because it stays the owner's, with the reason and what Undo restored. The owner only: anyone else, "
    "the Strategy Owner included, is refused.",
    {
        "before": _s("next_before from the previous page"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Entries in this page (50 at most)"},
    },
)
def goal_strategy_bot_log(api, args):
    return api.get("strategy-bot/log", before=args.get("before"), limit=args.get("limit"))


@tool(
    "tico_goal_update",
    "Edit a goal: title, body, the goal it serves, its owner, or its place in "
    "the owner's order. Moving it is for the parent's owner or someone above.",
    {
        "id": _s("Goal id"),
        "title": _s("New title"),
        "body": _s("New body"),
        "parent_id": _s("The goal it supports; an empty string unlinks it"),
        "owner": _s("New owner: a bot slug or a person id"),
        "rank": {"type": "integer", "description": "Position in the owner's order"},
        "top": {"type": "boolean", "default": False},
        "bet_id": _s("The strategy bet it serves; an empty string unlinks it"),
        "on_behalf_of": GOAL_YES,
    },
    required=("id",),
    writes=True,
)
def goal_update(api, args):
    body = {k: args.get(k) for k in ("title", "body", "parent_id", "rank", "bet_id")}
    body["owner"] = _target(api, args["owner"]) if args.get("owner") else None
    body["top"] = bool(args.get("top"))
    return _asked(api, args).post("goals/" + args["id"], body, key=_key(args))["goal"]


# The target lives on the link between a goal and a KPI: an improvement (baseline, target, deadline) or a
# range to stay inside (min, max).
TARGET_PROPS = {
    "baseline": {"type": "number", "description": "Improvement: where it starts; default the latest reading"},
    "target": {"type": "number", "description": "Improvement: the value to reach (needs a deadline)"},
    "deadline": _s("Improvement: the date the target is due, YYYY-MM-DD"),
    "min": {"type": "number", "description": "Range to keep it in: the lowest acceptable value"},
    "max": {"type": "number", "description": "Range to keep it in: the highest acceptable value"},
}


KPI_PROPS = {
    "definition": _s("What exactly is counted, in a sentence"),
    "unit": _s("demos, $, %, days", default=""),
    "direction": {"type": "string", "enum": ["up", "down", "range"], "description": "Which way is good"},
    "cadence": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "How often it is read"},
    "source_note": _s("Where the number comes from"),
}


KPI_TREE_PROPS = {
    "parent_kpi_id": _s("The KPI this one feeds in the growth tree; \"\" clears"),
    "growth_role": {
        "type": "string",
        "enum": ["north_star", "input", "guardrail", ""],
        "description": "One North Star at a time: naming a new one moves the role; \"\" clears",
    },
    "stage": {
        "type": "string",
        "enum": ["reach", "acquire", "activate", "retain", "refer", ""],
        "description": "Funnel stage; \"\" clears",
    },
    "loop": _s("NAME:STEP, its place in a growth loop (referral:2); \"\" clears"),
}


def _target_body(args):
    """The link's target fields: a range when min or max is given, an improvement when a target is, else none."""
    body = {k: args.get(k) for k in ("baseline", "target", "deadline", "min", "max") if args.get(k) is not None}
    body["kind"] = (
        "maintain"
        if body.get("min") is not None or body.get("max") is not None
        else "improve"
        if body.get("target") is not None
        else "none"
    )
    return body


@tool(
    "tico_kpi_list",
    "The KPIs you may see, each with its latest reading, freshness and colour. `bot` is one "
    "bot's five automatic KPIs (tasks done, time to first response, approval rate, failed runs, model cost; "
    "Read on the bot).",
    {
        "goal_id": _s("Only the KPIs this goal uses"),
        "owner": _s("Only this owner's: me, a bot slug or a person id"),
        "unlinked": {"type": "boolean", "default": False, "description": "Only the KPIs no goal uses"},
        "include_archived": {
            "type": "boolean",
            "default": False,
            "description": "Include archived KPIs for explicit historical review",
        },
        "bot": _s("A bot slug: its automatic KPIs"),
    },
)
def kpi_list(api, args):
    return api.get(
        "kpis",
        goal_id=args.get("goal_id") or None,
        owner=_target(api, args["owner"]) if args.get("owner") else None,
        unlinked="1" if args.get("unlinked") else None,
        auto_for=args.get("bot") or None,
        include_archived="1" if args.get("include_archived") else None,
    )


@tool(
    "tico_kpi_show",
    "One KPI: its definition and version history, owner, the goals that use it with each "
    "target and colour, every reading (oldest first, with what superseded what), and the latest check-ins. "
    "`effective` leaves out the readings a correction replaced.",
    {"id": _s("KPI id"), "effective": {"type": "boolean", "default": False}},
    required=("id",),
)
def kpi_show(api, args):
    kpi = api.get("kpis/" + args["id"])
    if args.get("effective"):
        readings = api.get(f"kpis/{args['id']}/readings", effective="1")
        kpi = {**kpi, "readings": readings.get("readings", readings) if isinstance(readings, dict) else readings}
    return kpi


@tool(
    "tico_kpi_create",
    "Make a KPI: a measure on its own that goals link to. Name what is counted, per what; "
    "give a definition, the unit, which way is good, how often it is read and who is accountable (default you). "
    "With `goal_id` it is linked to that goal, with the target fields on the link (an improvement: target and "
    "deadline; or a range: min/max). The Strategy Owner proposes instead (tico_proposal_create).",
    {
        "name": _s("'booked Calendly demos per two weeks'"),
        "goal_id": _s("Link it to this goal"),
        "owner": _s("`me` (default), `company` (the owner's), a bot slug or a person id"),
        **KPI_PROPS,
        **TARGET_PROPS,
        "on_behalf_of": GOAL_YES,
    },
    required=("name",),
    writes=True,
)
def kpi_add(api, args):
    body = {
        "name": args["name"],
        "goal_id": args.get("goal_id"),
        "owner": _target(api, args["owner"]) if args.get("owner") else None,
        **{k: args[k] for k in KPI_PROPS if args.get(k) is not None},
        **_target_body(args),
    }
    if args.get("on_behalf_of") and args.get("goal_id"):
        body.pop("goal_id")
        return _asked(api, args).post(f"goals/{args['goal_id']}/kpis", body, key=_key(args))["kpi"]
    return _asked(api, args).post("kpis", body, key=_key(args))["kpi"]


@tool(
    "tico_kpi_update",
    "Change a KPI. A change to what it measures (definition, unit, direction, cadence, "
    "source note) is a new definition version; a rename or a new owner is not. The KPI's owner or someone above.",
    {
        "id": _s("KPI id"),
        "name": _s("New name"),
        "owner": _s("New owner: `me`, a bot slug or a person id"),
        **KPI_PROPS,
        **KPI_TREE_PROPS,
    },
    required=("id",),
    writes=True,
)
def kpi_update(api, args):
    body = {k: args.get(k) for k in ("name", *KPI_PROPS)}
    body["owner"] = _target(api, args["owner"]) if args.get("owner") else None
    # The tree fields go only when named, so a rename never clears a KPI's place in the tree; "" clears one.
    for k in ("parent_kpi_id", "growth_role", "stage"):
        if args.get(k) is not None:
            body[k] = args[k] or None
    if args.get("loop") is not None:
        name, sep, step = str(args["loop"]).rpartition(":")
        if args["loop"] and (not sep or not name or not step.isdigit()):
            raise APIError("loop", "--loop is NAME:STEP, for example referral:2; \"\" clears it")
        body["loop_name"], body["loop_step"] = (name, int(step)) if args["loop"] else (None, None)
    return api.post("kpis/" + args["id"], body, key=_key(args))["kpi"]


@tool(
    "tico_kpi_archive",
    "Archive a KPI from active lists and freshness checks. Definitions, links, readings and audit history stay; the owner or someone above them may restore it.",
    {"id": _s("KPI id")},
    required=("id",),
    writes=True,
)
def kpi_archive(api, args):
    return api.post("kpis/" + args["id"] + "/archive", {}, key=_key(args))["kpi"]


@tool(
    "tico_kpi_restore",
    "Restore an archived KPI to active lists and freshness checks. Its definitions, links, readings and audit history stay.",
    {"id": _s("KPI id")},
    required=("id",),
    writes=True,
)
def kpi_restore(api, args):
    return api.post("kpis/" + args["id"] + "/restore", {}, key=_key(args))["kpi"]


@tool(
    "tico_kpi_link",
    "Link a goal to a KPI and set the target on that link, or change the target of an "
    "existing link: an improvement (baseline, target, deadline) or a range (min, max); none is just "
    "linked. The goal's owner or someone above them; the Strategy Owner proposes instead.",
    {
        "goal_id": _s("Goal id"),
        "kpi_id": _s("KPI id (auto:<bot>:<metric> for a bot's automatic KPI)"),
        **TARGET_PROPS,
        "on_behalf_of": GOAL_YES,
    },
    required=("goal_id", "kpi_id"),
    writes=True,
)
def kpi_link(api, args):
    body = {"kpi_id": args["kpi_id"], **_target_body(args)}
    return _asked(api, args).post(f"goals/{args['goal_id']}/kpis", body, key=_key(args))["kpi"]


@tool(
    "tico_kpi_target",
    "Set or remove the target on a goal's measure. Cite the owner's yes to its read-back.",
    {"goal_id": _s("Goal id"), "kpi_id": _s("KPI id"), **TARGET_PROPS, "on_behalf_of": GOAL_YES},
    required=("goal_id", "kpi_id"),
    writes=True,
)
def kpi_target(api, args):
    return _asked(api, args).post(f"goals/{args['goal_id']}/kpis/{args['kpi_id']}", _target_body(args), key=_key(args))[
        "kpi"
    ]


@tool(
    "tico_kpi_unlink",
    "Take a KPI off a goal. The KPI and its readings stay.",
    {"goal_id": _s("Goal id"), "kpi_id": _s("KPI id"), "on_behalf_of": GOAL_YES},
    required=("goal_id", "kpi_id"),
    writes=True,
)
def kpi_unlink(api, args):
    return _asked(api, args).post(f"goals/{args['goal_id']}/kpis/{args['kpi_id']}/unlink", {}, key=_key(args))["goal"]


@tool(
    "tico_kpi_log",
    "Log a reading on a KPI: a fact, with the period it describes, where it came from and how "
    "good it is. Never edited: to correct one, log a new reading that `supersedes` it. The KPI's owner, the "
    "Strategy Owner, or the owner of a goal that uses it.",
    {
        "kpi_id": _s("KPI id"),
        "value": {"type": "number", "description": "The value read"},
        "note": _s("Where the number came from", default=""),
        "period_start": _s(
            "Start of the period it describes, ISO date or date-time; default one cadence before the end"
        ),
        "period_end": _s("End of the period it describes (business time), ISO date or date-time; default now"),
        "at": _s("The old name of period_end"),
        "collected_at": _s("When it was collected, ISO date-time; default now"),
        "evidence": _s("A link or a note that shows where the value came from"),
        "quality": {
            "type": "string",
            "enum": ["measured", "estimate", "partial"],
            "description": "measured (default); estimate is a guess; partial is half a period, not judged",
        },
        "source": _s("Tool or system name (posthog, close)"),
        "definition_version": {
            "type": "integer",
            "description": "The definition version the value was computed under; default current",
        },
        "supersedes": _s("The id of the reading this one corrects (say why in the note)"),
    },
    required=("kpi_id", "value"),
    writes=True,
)
def kpi_log(api, args):
    body = {
        k: args[k]
        for k in (
            "period_start",
            "period_end",
            "at",
            "collected_at",
            "evidence",
            "quality",
            "source",
            "definition_version",
            "supersedes",
        )
        if args.get(k) is not None
    }
    body.update(value=args["value"], note=args.get("note") or "")
    return api.post(f"kpis/{args['kpi_id']}/readings", body, key=_key(args))["reading"]


# ----------------------------------------------------------------------------- proposals
@tool(
    "tico_proposal_create",
    "Propose a change you may not make yourself, for the owner to confirm: clearer "
    "goal wording (goal_wording: payload title/body), a KPI for a goal (goal_kpi: payload kpi or kpi_id, and "
    "target), a new definition (kpi_definition), a target (kpi_target: payload is the target fields), or a "
    "flag (flag: payload issue vague|duplicate), or, where the Strategy Owner "
    "is on, a goal's Why (goal_why: "
    "payload why, one line tying the goal to the company's vision), or, "
    "from the Strategy Owner at level 2 or 3, a red or "
    "stalled goal's recovery (recovery: payload evidence, options [{label, actions}], pick; actions create, "
    "reorder or settle), or, from any bot at any level, a change to the strategy draft (strategy_draft: payload "
    "summary, vision, sections {serve, sell, proof, now, know, how, not, extra: {key: {title, body}}}, bets [{id?, "
    "statement, why, rules_out, wrong_if, cites, status}], each optional; a bet with an id changes only the fields sent, one "
    "without is new; source bet_tripped when a tripped bet prompted it; confirmed, the owner's draft and bets "
    "change, nothing is published), or a change to the brand draft (brand_draft: payload "
    "{sections: {name: value or null}}; confirmed by an admin, merged into the draft, never published). "
    "goal_wording may be only {bet_id} to link a goal to a bet. The Strategy Owner "
    "cannot change a definition or a target it is judged against without one.",
    {
        "kind": {"type": "string", "enum": list(PROPOSAL_KINDS)},
        "goal_id": _s("The goal it is about"),
        "kpi_id": _s("The KPI it is about"),
        "payload": {"type": "object", "description": "The proposed change"},
        "reason": _s("One sentence: why"),
    },
    required=("kind",),
    writes=True,
)
def proposal_create(api, args):
    return api.post(
        "proposals",
        {
            "kind": args["kind"],
            "goal_id": args.get("goal_id"),
            "kpi_id": args.get("kpi_id"),
            "payload": args.get("payload") or {},
            "reason": args.get("reason") or "",
        },
        key=_key(args),
    )["proposal"]


@tool(
    "tico_proposal_list",
    "Proposals waiting for a decision (or `status`: confirmed, rejected, all).",
    {
        "status": _s("pending (default), confirmed, rejected or all", enum=["pending", "confirmed", "rejected", "all"]),
        "goal_id": _s("Only this goal's"),
        "kpi_id": _s("Only this KPI's"),
    },
)
def proposal_list(api, args):
    return api.get(
        "proposals",
        status=args.get("status") or None,
        goal_id=args.get("goal_id") or None,
        kpi_id=args.get("kpi_id") or None,
    )


@tool(
    "tico_proposal_decide",
    "Confirm or reject a proposal. A person's own decision, made by whoever owns "
    "the goal or the KPI: a bot is refused. Confirming makes the change as that person.",
    {
        "id": _s("Proposal id"),
        "decision": {"type": "string", "enum": ["confirm", "reject"]},
        "note": _s("Why, optionally"),
        "option": {"type": "integer", "description": "A recovery: approve this option (0 is the first), not the pick"},
        "on_behalf_of": GOAL_YES,
    },
    required=("id", "decision"),
    writes=True,
)
def proposal_decide(api, args):
    body = {"decision": args["decision"], "note": args.get("note") or ""}
    if args.get("option") is not None:
        body["option"] = int(args["option"])
    path = "goal-proposals" if args.get("on_behalf_of") else "proposals"
    return _asked(api, args).post(f"{path}/{args['id']}/decide", body, key=_key(args))["proposal"]


# ----------------------------------------------------------------------------- the `tico goal` commands
_TO = Arg("goal_id", "--goal")
_YES = Arg("on_behalf_of", metavar="MESSAGE_ID")

_BODY = (Arg("body"), Arg("body", "--body-file", type=text_file, metavar="FILE"))
_GOAL_FLAGS = (*_BODY, Arg("parent_id", "--parent"), Arg("bet_id", "--bet"))

GOAL = (
    Group("goal", "goals: list, read-back, create and edit"),
    Command(
        "goal read-back",
        "Saves nothing until the owner agrees: posts the whole plan (1 to 500 changes) as Tico's read-back in the "
        "owner's chat, with the exact method, path and body of each step to `goal save` after their yes. On not_agreed "
        "read back again; never retry the save.",
        args=(Arg("changes", action="store"),),
        example="""tico goal read-back '[{"kind": "goal_create", "owner": "me", "title": "Ship 0.6"}]'""",
    ),
    Command(
        "goal save",
        "save one exact read-back step after the owner agrees, citing their reply with --on-behalf-of",
        args=(Arg("on_behalf_of", "--on-behalf-of"),),
        example="tico goal save POST goals '{\"title\": \"Ship 0.6\"}' --on-behalf-of turn",
    ),
    Command(
        "goal list",
        "what you are for: your goals, the chain above them, your reports' goals",
        example="tico goal list --all --status red,yellow",
    ),
    Command("goal show", "one goal, its KPIs and check-ins", example="tico goal show g_1"),
    Command(
        "goal create",
        "set a goal; a parent is optional, and it starts with no colour",
        args=(Arg("owner", "--owner"), Arg("title", "--title"), *_GOAL_FLAGS, _YES),
        example="tico goal create --owner me --title 'Ship 0.6' --parent g_1",
    ),
    Command(
        "goal status",
        "set the colour by hand; it sticks until a person hands it back (`auto`)",
        args=(Arg("note", positional=True, nargs="?", default=""),),
        example="tico goal status g_1 yellow 'two specs late'",
    ),
    Command(
        "goal update",
        "change a goal's title, body, parent, owner, order or bet",
        args=(*_GOAL_FLAGS, _YES),
        example="tico goal update g_1 --title 'Ship 0.6 by Friday'",
    ),
    Command(
        "goal refresh",
        "the Strategy Owner's status pass: work automatic colours out again",
        args=(Arg("goal_ids", "--goal"),),
        example="tico goal refresh --goal g_1",
    ),
    Command(
        "goal checkin",
        "how the owner says the goal is going, in their words",
        args=(Arg("from_actor", "--from"), Arg("kpi_id", "--kpi")),
        example="tico goal checkin g_1 'two customers live' --signal on_track",
    ),
    Command("goal checkin-list", "a goal's check-ins, newest first", example="tico goal checkin-list g_1"),
    Command(
        "goal needs-you",
        "red KPIs on your goals, stale KPIs you own, proposals to confirm",
        example="tico goal needs-you",
    ),
    Command("goal company", "the company's Vision and Description (read-only)", example="tico goal company"),
    Command(
        "goal strategy-bot",
        "the Strategy Owner: its level, regular checks and today's Needs you count per person",
        example="tico goal strategy-bot",
    ),
    Command(
        "goal strategy-bot-merge",
        "the Strategy Owner at level 2+: one item for several bots' same ask of a person",
        args=(Arg("tasks", action="store", nargs="+"),),
        example="tico goal strategy-bot-merge human:ana 'Pricing' 'Which plan first?' t_1 t_2",
    ),
    Command(
        "goal strategy-bot-level3-offer",
        "the Strategy Owner: record the level-3 offer its Monday note made",
        example="tico goal strategy-bot-level3-offer",
    ),
    Command(
        "goal strategy-bot-decide",
        "the Strategy Owner at level 3: one decision alone on bot tasks, with Undo",
        args=(Arg("changes", action="store"), Arg("goal_id", "--goal-id")),
        example="""tico goal strategy-bot-decide 'stale' '[{"do": "close", "task_id": "t_1"}]'""",
    ),
    Command(
        "goal strategy-bot-log",
        "the Strategy Owner log (the owner only): what it decided alone and what it proposed instead",
        example="tico goal strategy-bot-log --limit 20",
    ),
)


# ----------------------------------------------------------------------------- the `tico kpi` and `tico proposal` commands
def _json(text):
    """`--payload`: the proposed change, as JSON."""
    try:
        return json.loads(text)
    except ValueError:
        raise argparse.ArgumentTypeError("--payload must be JSON") from None


KPI = (
    Group("kpi", "a measure on its own that goals link to, and its readings"),
    Command(
        "kpi list",
        "KPIs with latest reading and colour",
        args=(_TO, Arg("include_archived", "--archived")),
        example="tico kpi list --goal g_1",
    ),
    Command(
        "kpi show",
        "definition and versions, the goals using it, every reading, check-ins",
        example="tico kpi show k_1 --effective",
    ),
    Command(
        "kpi archive", "archive a KPI, keeping its definitions, links and readings", example="tico kpi archive k_1"
    ),
    Command("kpi restore", "restore an archived KPI", example="tico kpi restore k_1"),
    Command(
        "kpi create",
        "make a KPI; with --goal it is linked, the target on the link",
        args=(_TO, _YES),
        example="tico kpi create 'booked demos per two weeks' --goal g_1 --target 20 --deadline 2026-12-31",
    ),
    Command(
        "kpi update",
        "a change to what it measures is a new definition version",
        args=(
            Arg("parent_kpi_id", "--parent", metavar="ID"),
            Arg("growth_role", "--role"),
            Arg("loop", metavar="NAME:STEP"),
        ),
        example="tico kpi update k_1 --role north_star",
    ),
    Command(
        "kpi link",
        "link a goal to a KPI, or change the target on the link",
        args=(_YES,),
        example="tico kpi link g_1 k_1 --min 3 --max 9",
    ),
    Command(
        "kpi target",
        "set or remove the target on a goal's measure",
        args=(_YES,),
        example="tico kpi target g_1 k_1 --target 20 --deadline 2026-12-31",
    ),
    Command("kpi unlink", "take a KPI off a goal", args=(_YES,), example="tico kpi unlink g_1 k_1"),
    Command(
        "kpi log",
        "a reading: a fact with its period; to correct one, supersede it",
        args=(
            Arg("note", positional=True, nargs="?", default=""),
            Arg("quality"),
            Arg("quality", "--estimate", action="store_const", const="estimate"),
        ),
        example="tico kpi log k_1 12 'from the CRM' --period-end 2026-11-10",
    ),
)

PROPOSAL = (
    Group("proposal", "a change you may not make yourself, for the owner to confirm"),
    Command(
        "proposal create",
        "propose a change for the owner to confirm",
        args=(
            Arg("kind", "--kind"),
            _TO,
            Arg("kpi_id", "--kpi"),
            Arg("payload", "--payload", type=_json, metavar="JSON"),
            Arg("payload", "--payload-file", type=json_file, metavar="FILE"),
        ),
        example="tico proposal create --kind goal_wording --goal g_1 --payload '{\"title\": \"Ship 0.6\"}'",
    ),
    Command(
        "proposal list",
        "proposals waiting for a decision",
        args=(Arg("status", default="pending"), _TO, Arg("kpi_id", "--kpi")),
        example="tico proposal list --status all",
    ),
    Command(
        "proposal decide",
        "a person's own decision; a bot is refused",
        args=(_YES,),
        example="tico proposal decide p_1 confirm --note 'clearer'",
    ),
)
