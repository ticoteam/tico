"""Automations.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from urllib.parse import quote

from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.commands._requester import _as_person


@tool(
    "tico_event_types",
    "Every event Tico names (task.done, task.closed, webhook.received, custom.<name>...), each with "
    "its group, a plain description, its version and an example payload (`data`). Automations (an event trigger), wake rules and "
    "outgoing webhooks read this one list; some events have an older spelling (`aliases`) that still works, and "
    "`sourceless` marks the events a rule judges with no task (webhook.received, tico.notice); `outbound` is false "
    "for those two, which an outgoing webhook cannot subscribe to.",
    {
        "group": _s(
            "Only this group: task, approval, needs_you, bot, release, meeting, strategy, market, inbound, health, "
            "webhook, tico, custom"
        )
    },
)
def event_types(api, args):
    return api.get("event-types", group=args.get("group"))


# Automations (backend/automations.py). One permission rule for every
# surface: the owner, anyone above the owner, the group's lead; BotOps acts with the requester's rights. A bot that may
# not change one proposes: its edit comes back as a pending version for a person to restore.
_AUTO_FIELDS = {
    "name": _s("What it is called"),
    "owner": _s("person:<id>, bot:<slug> or group:<id>"),
    "group": _s("group:<id>, for permissions"),
    "enabled": {"type": "boolean"},
    "spend_cap": {
        "type": ["object", "null"],
        "description": "{per_30m: dollars} for its LLM and bot turns; null = the bot's limit",
    },
    "batch": {
        "type": "object",
        "description": "{times: [\"08:00\", ...], tz}: filters on receipt, the rest at these times",
    },
    "destinations": {
        "type": ["array", "null"],
        "items": {"type": "object"},
        "description": "Named chains a filter rule's `to` runs, at most 20: {id (a name like legal), name, "
        "color (amber, orange, violet, green, blue, teal, pink, gray or red; optional), then: "
        "the actions, at most 10 (empty = ignore)}. A rule sends a thread there with `to`, "
        "after its own actions; null takes them away",
    },
    "trigger": {
        "type": "object",
        "description": "{type: message|task_received|schedule|webhook|listening|event|due, ...}; a due trigger is {type: due, first_due: YYYY-MM-DD, repeats: once|weekly|monthly|quarterly|yearly, open_days (default 14), tz}",
    },
    "steps": {
        "type": "array",
        "items": {"type": "object"},
        "description": "The whole step tree (filter, decide, llm, find, bot_turn, paths, action). An action step is "
        "{type: action, do, args}; args by do: draft_reply and send_reply body (or text), to, bot "
        "(Slack send); forward to, body, webhook; label name (or tag); create_task title, body, owner, due (a date; a due trigger's day by default); "
        "assign owner, text; note text (or note), bot; wake_bot bot, text; post_update to ([updates, market]), text (none posts what the bot_turn before it wrote); "
        "call_webhook webhook, type, data; archive, leave_for_you and unsubscribe take none",
    },
}


@tool(
    "tico_automation_list",
    "The automations you may see, newest first, each with `can_edit` and `problems` (wake rules naming a missing step).",
    {
        "owner": _s("Only this owner's"),
        "limit": {"type": "integer", "default": 50},
        "cursor": {"type": "integer", "description": "`next` from the page before"},
    },
)
def automation_list(api, args):
    query = "&".join(f"{k}={args[k]}" for k in ("owner", "limit", "cursor") if args.get(k) not in (None, ""))
    return _as_person(api).get("automations" + ("?" + query if query else ""))


@tool(
    "tico_automation_get",
    "One automation: its trigger, steps, version, today's counts, next run and `problems` (wake rules naming a missing step).",
    {"id": _s("auto_...")},
    required=("id",),
)
def automation_get(api, args):
    return _as_person(api).get(f"automations/{args['id']}")


@tool(
    "tico_automation_question_sets",
    "The saved question sets a Decide step may name (`set`), each with its `id` "
    "(name@version), `summary` and `questions`. Read-only.",
    {},
)
def automation_question_sets(api, args):
    return _as_person(api).get("automations/question-sets")


@tool(
    "tico_automation_guide",
    "How BotOps builds and edits automations: the rules and the flow format (triggers, "
    "steps, actions and their args), generated from the hub's code. Read it before creating or updating one.",
    {},
)
def automation_guide(api, args):
    return _as_person(api).get("automations/guide")


@tool(
    "tico_automation_templates",
    "The inbox templates (founder, support, sales, blank): each with its `id`, `name` and "
    "destination names. Read-only.",
    {},
)
def automation_templates(api, args):
    return _as_person(api).get("automations/templates")


@tool(
    "tico_automation_template",
    "One inbox template filled in for an owner and an inbox: the flow the hub would save, "
    "with its roles resolved on the org chart and what the owner may not use taken out. Nothing is saved; save it "
    "with tico_automation_create (off until turned on).",
    {
        "id": _s("founder, support, sales or blank"),
        "owner": _s("person:<id>, bot:<slug> or group:<id>"),
        "inbox": _s("the inbox: an email address, slack:dm or slack:#<channel>"),
    },
    required=("id", "owner", "inbox"),
)
def automation_template(api, args):
    query = "&".join(f"{k}={quote(str(args[k]), safe='')}" for k in ("owner", "inbox"))
    return _as_person(api).get(f"automations/templates/{quote(str(args['id']), safe='')}?{query}")


@tool(
    "tico_automation_create",
    "Save a new automation (version 1). Checked on save: a bad flow is refused with why. "
    "In Assistant chat a Message routine saves off and returns an approval action card; only the person's click "
    "turns it on. Omit enabled or set false. Choose defaults to Draft; use Reply only when the person explicitly "
    "asks to send replies and the inbox permits sending. "
    "BotOps saves it off unless `enabled` is sent. A bot without rights to it saves a proposal: off, its first "
    "version pending, for a person to turn on.",
    {k: v for k, v in _AUTO_FIELDS.items()},
    required=("name", "owner", "trigger"),
    writes=True,
)
def automation_create(api, args):
    body = {k: args[k] for k in _AUTO_FIELDS if k in args}
    return _as_person(api).post("automations", {"steps": [], **body}, key=_key(args))


@tool(
    "tico_automation_update",
    "Change an automation. A new trigger, steps or batch times is a new version. "
    "The Assistant must propose Message routine edits with tico_assistant_propose (PATCH "
    "/api/v2/automations/<id>); pausing with enabled:false is direct. A bot "
    "without edit rights gets back `pending` (a proposal a person restores). Action args are named in `steps`. Then check `problems`.",
    {"id": _s("auto_..."), **_AUTO_FIELDS, "summary": _s("A few words on the change")},
    required=("id",),
    writes=True,
)
def automation_update(api, args):
    body = {k: args[k] for k in (*_AUTO_FIELDS, "summary") if k in args}
    return _as_person(api).patch(f"automations/{args['id']}", body, key=_key(args))


@tool(
    "tico_automation_delete",
    "Delete an automation; its runs stay. A built-in one is refused (pause it instead).",
    {"id": _s("auto_...")},
    required=("id",),
    writes=True,
)
def automation_delete(api, args):
    return _as_person(api).post(f"automations/{args['id']}/delete", {}, key=_key(args))


@tool(
    "tico_automation_run",
    "Run an automation now, whatever its trigger says, and get the run: each step's status, "
    "outputs and cost. `input` stands in for the trigger's output (message, task, item, event, body).",
    {"id": _s("auto_..."), "input": {"type": "object", "description": "A sample trigger output"}},
    required=("id",),
    writes=True,
)
def automation_run(api, args):
    return _as_person(api).post(f"automations/{args['id']}/run", {"input": args.get("input") or {}}, key=_key(args))[
        "run"
    ]


@tool(
    "tico_automation_runs",
    "An automation's runs, newest first, with step detail for 30 days and daily counts.",
    {"id": _s("auto_..."), "limit": {"type": "integer", "default": 25}, "before": _s("`next` from the page before")},
    required=("id",),
)
def automation_runs(api, args):
    query = "&".join(f"{k}={args[k]}" for k in ("limit", "before") if args.get(k) not in (None, ""))
    return _as_person(api).get(f"automations/{args['id']}/runs" + ("?" + query if query else ""))


@tool(
    "tico_automation_held",
    "End an automation's run-guard hold (`hold` on tico_automation_get): `release` queues its "
    "held runs, oldest first; `drop` skips them. Fix what starts a loop first.",
    {"id": _s("auto_..."), "action": {"type": "string", "enum": ["release", "drop"]}},
    required=("id", "action"),
    writes=True,
)
def automation_held(api, args):
    return _as_person(api).post(f"automations/{args['id']}/held", {"action": args["action"]}, key=_key(args))


@tool(
    "tico_automation_recent_runs",
    "Runs across every automation you may see in the last `range`, newest first: what "
    "started each, its outcome or failing step, steps, time and cost. `trigger: system` is Tico's own jobs.",
    {
        "range": {"type": "string", "enum": ["5m", "1h", "24h", "7d"], "default": "1h"},
        "status": {"type": "string", "enum": ["done", "failed", "waiting", "running", "held"]},
        "trigger": _s("message, task_received, schedule, webhook, listening, event, due or system; comma-separated"),
        "owner": _s("person:<id>, bot:<slug> or group:<id>"),
        "limit": {"type": "integer", "default": 50},
        "before": _s("`next` from the page before"),
    },
)
def automation_recent_runs(api, args):
    query = "&".join(
        f"{k}={quote(str(args[k]), safe=',')}"
        for k in ("range", "status", "trigger", "owner", "limit", "before")
        if args.get(k) not in (None, "")
    )
    return _as_person(api).get("automations/runs" + ("?" + query if query else ""))


@tool(
    "tico_automation_stats",
    "Runs, failures and cost per automation you may see over the last `range`, in time "
    "buckets oldest first, with totals: which automations fail or cost the most.",
    {"range": {"type": "string", "enum": ["5m", "1h", "24h", "7d"], "default": "1h"}},
)
def automation_stats(api, args):
    return _as_person(api).get("automations/stats?range=" + quote(str(args.get("range") or "1h"), safe=""))


@tool(
    "tico_automation_versions",
    "An automation's versions, newest first; `status: pending` for proposals only.",
    {"id": _s("auto_..."), "status": {"type": "string", "enum": ["active", "pending"]}},
    required=("id",),
)
def automation_versions(api, args):
    status = f"?status={args['status']}" if args.get("status") else ""
    return _as_person(api).get(f"automations/{args['id']}/versions{status}")


@tool(
    "tico_automation_restore",
    "Make a version current: a pending one keeps its number, an older one is saved as a new version.",
    {"id": _s("auto_..."), "n": {"type": "integer"}},
    required=("id", "n"),
    writes=True,
)
def automation_restore(api, args):
    return _as_person(api).post(f"automations/{args['id']}/versions/{int(args['n'])}/restore", {}, key=_key(args))


@tool(
    "tico_automation_discard",
    "Discard a pending version: a proposal a bot made, which then goes away. The "
    "automation stays as it is; any other version is refused (409).",
    {"id": _s("auto_..."), "n": {"type": "integer", "description": "The pending version's number"}},
    required=("id", "n"),
    writes=True,
)
def automation_discard(api, args):
    return _as_person(api).call("DELETE", f"automations/{args['id']}/versions/{int(args['n'])}", None, _key(args))


@tool(
    "tico_inbox_send",
    "Read or set an inbox's send switch (on by default). Off, an automation's send_reply saves a "
    "draft instead. The inbox's person or the top of the org sets it.",
    {
        "inbox": _s("An email address, slack:dm or slack:#<channel>"),
        "send": {"type": "boolean", "description": "Omit to read it"},
    },
    required=("inbox",),
    writes=True,
)
def inbox_send(api, args):
    from urllib.parse import quote

    path = "inboxes/" + quote(str(args["inbox"]), safe="@:")
    if "send" not in args:
        return _as_person(api).get(path)
    return _as_person(api).patch(path, {"send": bool(args["send"])}, key=_key(args))
