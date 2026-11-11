"""People, groups, seats, settings, setup, alerts, SQL, audit, tokens, events and the raw API.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from urllib.parse import quote
from clients.tico import APIError

from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.commands._shared import ASKED, BUDGET_VALUE, _teammate_access
from clients.commands._requester import ON_BEHALF_OF, _as_person, _asked, _for_person


# A person's own API tokens (backend/personal_tokens.py, docs/connect-an-agent.md). A token reads and revokes its
# person's tokens; minting one takes a signed-in browser, so a leaked token cannot extend its own life.
@tool(
    "tico_token_list",
    "Your personal API tokens: label, created, last used, expires, revoked; never a secret. `all`: everyone's "
    "(owner and admins, from a signed-in browser).",
    {"all": {"type": "boolean", "description": "Everyone's tokens, with whose each is (owner and admins)"}},
)
def token_list(api, args):
    return api.get("access/tokens" if args.get("all") else "me/tokens")


@tool(
    "tico_token_create",
    "Make a personal API token for yourself; the answer shows it once. Only from a signed-in browser session: a "
    "token cannot make tokens.",
    {
        "label": _s("What the token is for, shown in Settings > Computers > API tokens", maxLength=80),
        "expires_in_days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "90 unless given"},
    },
    required=("label",),
    writes=True,
)
def token_create(api, args):
    body = {"label": args["label"]}
    if args.get("expires_in_days") is not None:
        body["expires_in_days"] = args["expires_in_days"]
    return api.post("me/tokens", body, key=_key(args))


@tool(
    "tico_token_revoke",
    "Stop one of your personal API tokens at once (the owner and admins: anyone's, from a signed-in browser).",
    {"id": _s("The token's id, from tico_token_list")},
    required=("id",),
    writes=True,
)
def token_revoke(api, args):
    return api.post("me/tokens/" + quote(str(args["id"]), safe="") + "/revoke", {}, key=_key(args))


ALERT_PREFERENCES = {
    **{k: {"type": "boolean"} for k in ("legal_mail", "urgent_calls", "emergency_exception")},
    **{k: {"type": "string"} for k in ("timezone", "quiet_start", "quiet_end")},
    "monthly_cap": {"type": "integer", "minimum": 0, "maximum": 10000},
}


@tool(
    "tico_alerts_show",
    "Your own phone, alert preferences and spend. Only a person acting as themselves; never BotOps or the Assistant.",
    {},
)
def alerts_show(api, args):
    return api.get("alerts/me")


@tool(
    "tico_alerts_set",
    "Choose alerts, timezone, quiet hours and a monthly USD cap. Empty quiet_start and quiet_end disables quiet hours; emergency_exception is explicit. Only your own preferences.",
    ALERT_PREFERENCES,
    writes=True,
)
def alerts_set(api, args):
    return api.call(
        "PUT", "alerts/me", {k: args[k] for k in ALERT_PREFERENCES if args.get(k) is not None}, key=_key(args)
    )


@tool(
    "tico_alerts_verify",
    "Send a free code to your own US/Canada phone. SMS consent is required; call consent is optional and separate.",
    {"phone": _s("Your own phone"), "sms_consent": {"type": "boolean"}, "call_consent": {"type": "boolean"}},
    required=("phone", "sms_consent"),
    writes=True,
)
def alerts_verify(api, args):
    return api.post(
        "alerts/me/phone", {k: args[k] for k in ("phone", "sms_consent", "call_consent") if k in args}, key=_key(args)
    )


@tool(
    "tico_alerts_confirm",
    "Confirm your phone with its six-digit code. A person acting as themselves only.",
    {"code": _s("The six-digit verification code")},
    required=("code",),
    writes=True,
)
def alerts_confirm(api, args):
    return api.post("alerts/me/phone/confirm", {"code": args["code"]}, key=_key(args))


@tool(
    "tico_alerts_remove",
    "Revoke your phone and cancel queued alerts. Retry removal if HQ is unavailable; local paging stops immediately.",
    {},
    writes=True,
)
def alerts_remove(api, args):
    return api.call("DELETE", "alerts/me/phone", None, key=_key(args))


@tool(
    "tico_alerts_test",
    "Send yourself a test alert. The company pays $1 per accepted alert; caps apply. Use a stable operation_id for retries.",
    {"accept_charge": {"type": "boolean"}},
    required=("accept_charge", "operation_id"),
    writes=True,
)
def alerts_test(api, args):
    return api.post("alerts/me/test", {"accept_charge": args["accept_charge"]}, key=_key(args))


@tool(
    "tico_alerts_company",
    "Company alert monthly cap/spend in USD; owner/admin acting as themselves. Set monthly_cap to change it; zero stops paid alerts.",
    {"monthly_cap": {"type": "integer", "minimum": 0, "maximum": 100000}},
    writes=True,
)
def alerts_company(api, args):
    if args.get("monthly_cap") is None:
        return api.get("alerts/company")
    return api.call("PUT", "alerts/company", {"monthly_cap": args["monthly_cap"]}, key=_key(args))


@tool(
    "tico_human_access",
    "Show, or set, who may see, read and write to a person, as the person who asked you (it must be "
    "them, someone they report up to, or an admin). See: on the chart and lists; Read: their Home; Write: DM them or add "
    "them to a thread. Each is `everyone`, or a comma list of person ids, `group:<id>` and `bot:<slug>`; a group names "
    "the people and bots in it and the groups under it. Levels you leave out stay as they are.",
    {
        "human": _s("The person's id"),
        "see": _s("everyone, or ben,group:legal,bot:analyst"),
        "read": _s("Who may read their Home"),
        "write": _s("Who may DM them or add them to a thread"),
    },
    required=("human",),
    writes=True,
)
def human_access(api, args):
    return _teammate_access(api, f"humans/{quote(str(args['human']), safe='')}", args)


@tool(
    "tico_group_access",
    "Show, or set, who may see, read and write to a group, as the person who asked you (a lead of "
    "the group or one above it, or an admin). By default everyone sees and reads it and its members write to it. Each "
    "level is `everyone`, or a comma list of person ids, `group:<id>` and `bot:<slug>`. Levels left out stay as they are.",
    {
        "group": _s("The group's id"),
        "see": _s("everyone, or ben,group:legal,bot:analyst"),
        "read": _s("Who may read its Home"),
        "write": _s("Who may post to it or add it to a thread"),
    },
    required=("group",),
    writes=True,
)
def group_access(api, args):
    return _teammate_access(api, f"groups/{quote(str(args['group']), safe='')}", args)


@tool(
    "tico_seat_list",
    "The Recruiting seats: roles on the org chart with their job description and who they report "
    "to, and no bot yet. A seat holds no work: work for its role stays with its manager.",
    {},
)
def seat_list(api, args):
    return api.get("seats")


@tool(
    "tico_org_models",
    "The published Tico starter org chart models: recommendations come from these models only. "
    "They contain Tico's public role and team catalog, never another company's bots or data.",
    {},
)
def org_models(api, args):
    return api.get("org-models")


@tool(
    "tico_seat_create",
    "Add a Recruiting seat, as a person: a role with its job description, reporting to you or "
    "someone below you (the owner: anyone). It has no bot, repository or computer until it is filled.",
    {
        "title": _s("The role's title"),
        "description": _s("Its job description"),
        "reports_to": _s("A bot's slug or human:<id>; you when you leave it out"),
        "team": _s("Optional group id"),
    },
    required=("title",),
    writes=True,
)
def seat_create(api, args):
    body = {k: args[k] for k in ("title", "description", "reports_to", "team") if args.get(k) is not None}
    return api.post("seats", body, key=_key(args))


@tool(
    "tico_seat_update",
    "Change a Recruiting seat's title, job description, reporting line or group, as a person "
    "it reports up to (or the owner).",
    {
        "seat": _s("The seat id"),
        "title": _s("New title"),
        "description": _s("New job description"),
        "reports_to": _s("A bot's slug or human:<id>"),
        "team": _s("Group id"),
    },
    required=("seat",),
    writes=True,
)
def seat_update(api, args):
    body = {k: args[k] for k in ("title", "description", "reports_to", "team") if args.get(k) is not None}
    return api.patch(f"seats/{args['seat']}", body, key=_key(args))


@tool(
    "tico_seat_close",
    "Take a Recruiting seat off the org chart, as a person it reports up to (or the owner).",
    {"seat": _s("The seat id")},
    required=("seat",),
    writes=True,
)
def seat_close(api, args):
    return api.post(f"seats/{args['seat']}/close", {}, key=_key(args))


@tool(
    "tico_human_add",
    "Add a person to the team roster and the sign-in list, as the person who asked you. A "
    "member with permission may add a teammate in the team's email domain; an owner or admin may add anyone. "
    "Authorized requests run at once.",
    {
        "email": _s("Their email address"),
        "name": _s("Their name"),
        "title": _s("Their title"),
        "reports_to": _s("A person id they report to"),
    },
    required=("email",),
    writes=True,
)
def people_add(api, args):
    body = {
        "email": args["email"],
        "name": args.get("name") or "",
        "title": args.get("title") or "",
        "reports_to": args.get("reports_to") or "",
        **_for_person(api),
    }
    return api.post("access/humans", body, key=_key(args))


@tool(
    "tico_human_list",
    "The people on the roster: id, name, email, title, group (`team`), who they report to, and "
    "their GitHub login (`github`, from their profile; empty when unset).",
    {},
)
def people_list(api, args):
    return [
        {k: p.get(k) for k in ("id", "name", "email", "title", "team", "reports_to", "github")}
        for p in api.get("org")["people"]
    ]


@tool(
    "tico_setup_show",
    "Where Finish setup stands, for the owner or an admin: whether the welcome wizard is finished (`completed`, "
    "`needed`), its saved answers and picks, the people the company file from setup names (`company_import.people`: "
    "`id` is the key `tico_setup_invite` takes, `person` is set once they are on the roster, `invited_at` once Tico "
    "mailed them), a company file that could not be imported (`company_import_failed`), and the getting-started "
    "checklist (`getting_started.items`: `done`, `optional`, `skipped`).",
    {},
)
def setup_show(api, args):
    setup = api.get("setup")
    started = api.get("getting-started")
    return {**setup, "getting_started": {k: started.get(k) for k in ("items", "done", "total", "complete")}}


@tool(
    "tico_setup_complete",
    "Finish the welcome wizard with the answers and picks it has saved (`tico_setup_show`), as the owner: the built-in "
    "bots are created and every other role becomes a Recruiting seat. Finishing twice keeps the first time. The "
    "same as the wizard's Create my team. Answers with the setup record. The wizard's choices are saved with "
    "`tico_api` PUT /api/v2/setup, or come from the company file or sample company the install was given.",
    {},
    writes=True,
)
def setup_complete(api, args):
    return api.post("setup/complete", {}, key=_key(args))


@tool(
    "tico_setup_invite",
    "Invite one person the company file from setup names, as the owner: they go on the roster and the sign-in list "
    "with this email, the seats that waited with the owner now report to them, and Tico mails the invitation. "
    "Inviting again sends again. Take `person_key` from `tico_setup_show` (`company_import.people[].id`). Answers with "
    "the setup record.",
    {
        "person_key": _s("The person's id in the company file (`company_import.people[].id` from tico_setup_show)"),
        "email": _s("Their email address; leave out to use the one they were added with"),
    },
    required=("person_key",),
    writes=True,
)
def setup_invite(api, args):
    return api.post(
        f"setup/company/people/{quote(str(args['person_key']), safe='')}/invite",
        {"email": args.get("email") or ""},
        key=_key(args),
    )


@tool(
    "tico_group_list",
    "The groups: sub-teams of the team, each with its id, name, parent group (groups nest), the "
    "humans in it (ids) and the bots in it (slugs) you may see. A teammate, human or bot, is in one group at a time.",
    {},
)
def group_list(api, args):
    return api.get("groups")


@tool(
    "tico_group_update",
    "Create, rename, move, lead or fill a group, as the person who asked you (the owner or an "
    "admin; a group's lead within the groups it leads). Leave `group` out to create one from `name`. `parent` nests it "
    "under another group (an empty one moves it to the top). Adding a teammate to a group takes it out of the one it "
    "was in; built-in bots stay outside groups. Answers with the group.",
    {
        "group": _s("The group's id; leave out to create a new one"),
        "name": _s("Its name"),
        "parent": _s("The id of the group it is in; empty for the top"),
        "add_humans": {"type": "array", "items": {"type": "string"}, "description": "Human ids to put in the group"},
        "add_bots": {"type": "array", "items": {"type": "string"}, "description": "Bot slugs to put in the group"},
        "remove_humans": {"type": "array", "items": {"type": "string"}, "description": "Human ids to take out of it"},
        "remove_bots": {"type": "array", "items": {"type": "string"}, "description": "Bot slugs to take out of it"},
        "lead": _s("Its lead: bot:<slug> or person:<id>; empty for none"),
        "on_behalf_of": ASKED,
    },
    writes=True,
)
def group_update(api, args):
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    add = {"people": list(args.get("add_humans") or []), "bots": list(args.get("add_bots") or [])}
    remove = {"people": list(args.get("remove_humans") or []), "bots": list(args.get("remove_bots") or [])}
    if not args.get("group"):
        if not args.get("name"):
            raise ValueError("Name the new group, or say which group to change: tico group update <group> ...")
        body = {"name": args["name"], "add": add, **{k: args[k] for k in ("parent", "lead") if args.get(k)}}
        return who.post("groups", body, key=_key(args))
    body = {"add": add, "remove": remove, **{k: args[k] for k in ("name", "parent", "lead") if args.get(k) is not None}}
    return who.patch(f"groups/{args['group']}", body, key=_key(args))


@tool(
    "tico_group_delete",
    "Delete a group, as the person who asked you (the owner or an admin). Its teammates and the "
    "groups in it move up to its parent group.",
    {"group": _s("The group's id"), "on_behalf_of": ASKED},
    required=("group",),
    writes=True,
)
def group_delete(api, args):
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    return who.call("DELETE", f"groups/{quote(str(args['group']), safe='')}", None, _key(args))


@tool(
    "tico_org_reports_to",
    "Change who a teammate reports to on the org chart, as the person who asked you: a bot "
    "reports to a bot or a person, a person to a person. Answers with the change.",
    {
        "teammate": _s("bot:<slug> or human:<id>"),
        "reports_to": _s("A bot's slug or human:<id> (a person: their id)"),
        "on_behalf_of": ASKED,
    },
    required=("teammate", "reports_to"),
    writes=True,
)
def org_reports_to(api, args):
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    kind, _, ref = str(args["teammate"]).strip().partition(":")
    boss = str(args["reports_to"]).strip()
    if kind == "human" and ref:
        return who.post(f"humans/{quote(ref, safe='')}", {"reports_to": boss.removeprefix("human:")}, key=_key(args))
    if kind != "bot" or not ref:
        raise ValueError("Name the teammate as bot:<slug> or human:<id>")
    row = next((b for b in api.get("bots") if ref in (b.get("id"), b.get("slug") or b.get("name"))), None)
    if not row:
        raise ValueError("No bot " + ref)
    return who.post(
        f"bots/{quote(ref, safe='')}/definition",
        {"reports_to": boss, "expected_revision": row.get("revision")},
        key=_key(args),
    )


@tool(
    "tico_org_order",
    "A manager's direct reports, humans and bots, in the order the team chart shows them (placed ones "
    "first, then the rest: humans, then by name). With `set`, put them in that order, as the person who asked you: "
    "whoever may put reports under that manager may order them, and a bot orders its own reports. Reports left out "
    "keep their places, so naming two swaps those two. Who reports to whom does not change. Answers with the order.",
    {
        "manager": _s(
            "The manager: a bot's slug or a human's id (`bot:` or `human:` in front when one could be either)"
        ),
        "set": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Its direct reports in the new order: bot slugs and human ids",
        },
    },
    required=("manager",),
    writes=True,
)
def org_order(api, args):
    path = "org/order/" + quote(str(args["manager"]).strip(), safe=":")
    if not args.get("set"):
        return api.get(path)
    return _as_person(api).call("PUT", path, {"reports": list(args["set"])}, _key(args))


def _api_path(path):
    path = str(path or "").strip()
    return path[len("/api/v2/") :] if path.startswith("/api/v2/") else path.lstrip("/")


@tool(
    "tico_api",
    "Use a v2 route with your own rights. BotOps uses the requester's rights by default, including a requesting bot's narrower rights. "
    "Requested bot deletion, outside-domain invites, Team rules and "
    "Tico updates run directly. Never put a secret in body; use Credentials or its chat card. Prefer a friendly tool when one fits.",
    {
        "method": _s("GET, POST, PUT, PATCH or DELETE", enum=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        "path": _s("A v2 route: /api/v2/bots/jira-manager/model or bots/jira-manager/model"),
        "body": {"type": "object", "description": "The JSON body for a write"},
        "query": {"type": "object", "description": "Query parameters for a read"},
        "on_behalf_of": ON_BEHALF_OF,
    },
    required=("method", "path"),
    writes=True,
)
def api_call(api, args):
    return _as_person(api, args.get("on_behalf_of")).call(
        str(args["method"]).upper(), _api_path(args["path"]), args.get("body"), _key(args), args.get("query")
    )


SETTING_TARGET = {
    "bot": _s("The bot's slug, for a setting set per bot"),
    "computer": _s("The computer's id or label, for a setting set per computer"),
    "person": _s("The person's id, for a setting set per person"),
    "id": _s("The id of the one thing it sets (a trigger, webhook, rule, routine, repository...)"),
}


def _setting_query(args):
    return {k: args[k] for k in ("bot", "computer", "person", "id") if args.get(k)}


def _pointer(data, pointer):
    for part in [p for p in str(pointer or "").split(".") if p]:
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


@tool(
    "tico_settings_find",
    "Find a control in Settings by its words: each one's `key`, plain `name`, `where` it sits in "
    "Settings, who may change it, its `class` (direct: runs at once as the person; confirm: BotOps acting for a "
    "person gets a Confirm card for them; person: only the person, in Settings; a `class_when` names the body that makes a direct one confirm or "
    "person), the route and body it writes, and the `card` link. Then `tico_settings_show` or `tico_settings_set` with its key.",
    {"words": _s("What the setting is about, e.g. sql, budget, release time")},
    required=("words",),
)
def settings_find(api, args):
    return _as_person(api).get("settings/catalog", q=args["words"])["settings"]


@tool(
    "tico_settings_show",
    "One setting from `tico_settings_find`: its current `value` (read with your rights, or the "
    "requester's for BotOps), who may change it, whether you may (`you_may`), its class and the card link.",
    {"key": _s("The setting's key, e.g. team.rules.admin_sql"), **SETTING_TARGET},
    required=("key",),
)
def settings_show(api, args):
    who = _as_person(api)
    shown = who.get("settings/catalog/" + quote(args["key"], safe=""), **_setting_query(args))
    read = shown.pop("read", None)
    if read:
        try:
            shown["value"] = _pointer(who.get(read["path"]), read["pointer"])
        except APIError as exc:
            shown["value_error"] = str(exc)
    return shown


@tool(
    "tico_settings_set",
    "Change one setting from `tico_settings_find`, as the person who asked (BotOps, the "
    "Assistant) or yourself. direct: it changes and the answer has the new `value`; confirm, from BotOps or the "
    "Assistant: a Confirm card in the person's chat (`needs_confirm`), nothing changed yet; person: refused with the "
    "one-line reason and the card's `link`. A refusal names who may change it. The Assistant cites the person's words "
    "(`on_behalf_of`) for a direct one; without them, or while the owner's \"Assistant acts without asking\" is off, "
    "it is a card too.",
    {
        "key": _s("The setting's key"),
        "value": {"description": "The new value: on/off, a number, text, a list or JSON, as `tico_settings_show` says"},
        **SETTING_TARGET,
        "on_behalf_of": ASKED,
    },
    required=("key", "value"),
    writes=True,
)
def settings_set(api, args):
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    key = quote(args["key"], safe="")
    planned = who.post(
        "settings/catalog/" + key + "/plan", {"value": args["value"], **_setting_query(args)}, key=_key(args, ":plan")
    )
    if "run" not in planned:
        return planned  # the Confirm card
    run = planned["run"]
    who.call(run["method"], run["path"], run["body"], _key(args))
    # The value read back after the change, where Settings shows one; else the value sent.
    shown = settings_show(api, {"key": args["key"], **_setting_query(args)})
    return {
        "key": args["key"],
        "name": planned["setting"]["name"],
        "where": planned["setting"]["where"],
        "value": shown["value"] if "value" in shown else planned["value"],
    }


@tool(
    "tico_team_budget",
    "Read or set the team's default runs per hour and per day for bots with no budget of their own "
    "(owner and admins). 0 is no limit.",
    {"hour": BUDGET_VALUE, "day": BUDGET_VALUE},
    (),
    writes=True,
)
def team_budget(api, args):
    body = {
        field: int(args[name])
        for field, name in (("turns_per_hour", "hour"), ("turns_per_day", "day"))
        if name in args and args[name] != "default"
    }
    return (
        _as_person(api).post("settings/turn-budgets", body, key=_key(args))
        if body
        else _as_person(api).get("settings/turn-budgets")
    )


@tool(
    "tico_audit",
    "Who changed what in the caps, spend, groups, automations and inboxes, newest first. "
    "The owner reads every row; anyone else reads their own changes. Page with `before` from `next`.",
    {
        "object": _s("One object, such as automation:<id> or inbox:<address>"),
        "limit": {"type": "integer", "default": 50, "description": "Rows a page, up to 200"},
        "before": _s("`next` from the page before"),
    },
)
def audit(api, args):
    query = {k: args[k] for k in ("object", "limit", "before") if args.get(k) not in (None, "")}
    return _as_person(api).get("audit", **query)


@tool(
    "tico_team_show",
    "The Team chart: who each person is, how to reach them (email, Slack, "
    "phone), what they own, their goals, and which bots hang under them. Use this to find who "
    "handles a kind of work before you file a task or ping someone.",
    {
        "person": _s("Optional person id: that person and everyone under them"),
        "team": _s("Optional group id: that group and the groups in it, like engineering or sales"),
    },
)
def org(api, args):
    return api.get("org", person=args.get("person") or None, team=args.get("team") or None)


# ----------------------------------------------------------------------------- sql, integrations
@tool(
    "tico_sql",
    "Read-only SQL over what you may see (docs/hub-sql.md). One SELECT, WITH or "
    "EXPLAIN QUERY PLAN; bind `:key` with `params`.",
    {
        "sql": _s("The statement"),
        "params": {"type": "object", "description": "Named bindings"},
        "max_rows": {"type": "integer", "minimum": 1, "description": "At most this many rows"},
    },
    required=("sql",),
)
def sql(api, args):
    body = {"sql": args["sql"], "params": args.get("params") or {}}
    if args.get("max_rows"):
        body["max_rows"] = int(args["max_rows"])
    return api.post("sql", body)


@tool(
    "tico_mcp_stats",
    "How outside assistants' tool calls perform, per tool: calls, median and p90 "
    "server time, answer size, errors. For tuning the on-the-go setup.",
    {
        "days": {"type": "integer", "description": "Look back this many days (default 7)"},
        "via": _s("Only one assistant's calls, by its token label, e.g. grok-bot"),
    },
)
def live_stats(api, args):
    query = "&".join(f"{k}={args[k]}" for k in ("days", "via") if args.get(k))
    return api.get("live/stats" + (f"?{query}" if query else ""))


@tool(
    "tico_origin_show",
    "Why an item is on the person's list: the task, the bot's question or the approval it names. "
    "Answers who made it, its cause (the automation that opened it, the org rule that asked for it, the parent task, or "
    "the bot that filed it), how many items that cause made this week and how many are still open, and up to three "
    "ready-to-run commands that would stop the source. Read-only; a ref the person cannot read is not found.",
    {"ref": _s("A task id (or 8+ leading characters), a message id (a bot's question) or an approval id")},
    required=("ref",),
)
def origin_show(api, args):
    return api.get("origins/" + args["ref"])
