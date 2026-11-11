"""People, bots, the org chart and reach: the company-management tools of the one tool table (clients/hubtools.py).

Each tool here is registered into `hubtools.TOOLS` by hubtools itself (it reads `TOOLS` below just before it builds
`BY_NAME`), so the MCP server, the stdio adapter and the `tico` CLI serve them like any other: `tico bot list` is
`tico_bot_list`. `add_commands` puts their commands under the CLI's nouns (clients/hubcli.py `parser`), and
clients/remotecli.py runs each through its tool (`COMMANDS`). Nothing here decides who may do what: every rule is the
server's, and a refusal is the server's own words.

Pure stdlib and no import of hubtools at module level, so importing this first is as safe as importing hubtools first.
"""

import argparse
from urllib.parse import quote

from clients.tico import APIError

TOOLS = []  # each {"name", "description", "properties", "required", "writes", "fn"}; hubtools registers them
AUDIENCE = {}  # tool name -> the kinds of caller it is offered to (hubtools.AUDIENCE takes these)

PEOPLE = ("owner", "admin", "member")
REQUESTER = PEOPLE + ("botops",)  # a person's own rights, or BotOps's as the person who asked it


def _s(description, **extra):
    return {"type": "string", "description": description, **extra}


def _flag(description):
    return {"type": "boolean", "description": description}


def _key(args, suffix=""):
    op = args.get("operation_id")
    return (op + suffix) if op else None


def tool(name, description, properties, required=(), *, writes=False, audience=REQUESTER):
    """Collect one tool for hubtools to register; `audience` is who it is offered to (the server still decides)."""

    def register(fn):
        TOOLS.append(
            {
                "name": name,
                "description": description,
                "properties": properties,
                "required": tuple(required),
                "writes": writes,
                "fn": fn,
            }
        )
        AUDIENCE[name] = audience
        return fn

    return register


def _person_api(api):
    from clients import hubtools

    return hubtools._as_person(api)


def _for_person(api):
    from clients import hubtools

    return hubtools._for_person(api)


def _try(fn, *args, **kw):
    """The answer, or None when the server refuses or does not have the route (an older server, a member's read)."""
    try:
        return fn(*args, **kw)
    except APIError:
        return None


# ----------------------------------------------------------------------------- bots: setup state in one call
def _actor_names(bots):
    """bot id -> slug, for the chart's and the pipelines' `bot:<id>` (a starter bot's id is a uuid, its slug its name)."""
    return {str(b.get("id")): b.get("slug") or str(b.get("id")) for b in bots}


def _teammate(ref, slugs):
    """`bot:<slug>` or `human:<id>` for a stored reports_to or stage holder, which may name a bot by its id."""
    ref = str(ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("human:"):
        return ref
    if ref.startswith("person:"):
        return "human:" + ref[len("person:") :]
    bare = ref.removeprefix("bot:")
    if bare in slugs:
        return "bot:" + slugs[bare]
    if ref.startswith("bot:") or bare in slugs.values():
        return "bot:" + bare
    return "human:" + bare  # a bot's bare person id in reports_to is that person (backend/people.py)


def _next_step(row):
    """Why a bot does not take work now and the one command that fixes it, or None while it can."""
    slug, state, setup = row["slug"], row.get("state"), row.get("onboarding_state") or ""
    if state == "archived":
        return None
    if state == "planned":
        return {"why": "planned: never went live", "fix": f"tico bot go-live {slug}"}
    if state == "quarantined":
        return {"why": "quarantined", "fix": f"tico bot quarantine-clear {slug}"}
    if state == "paused":
        return {"why": "paused", "fix": f"tico bot resume {slug}"}
    if setup in ("needs_setup", "needs_onboarding"):
        return {
            "why": "onboarding: takes no tasks until a person above it marks setup done",
            "fix": f"tico bot setup-done {slug}",
        }
    if not row.get("assignment") and not row.get("agent") and state == "active":
        return {"why": "no computer", "fix": f"tico bot place {slug}"}
    return None


def _bot_row(row, slugs):
    status = row.get("status") or {}
    assignment = row.get("assignment") or {}
    out = {
        "slug": row.get("slug"),
        "name": row.get("display_name") or row.get("slug"),
        "state": row.get("state"),
        "stage": row.get("stage"),
        "setup": row.get("onboarding_state") or "",
        "live": status.get("state"),
        "reports_to": _teammate(row.get("reports_to"), slugs),
        "group": row.get("team") or "",
        "template": row.get("template") or "",
        "model": row.get("model") or "",
        "harness": row.get("harness") or "",
        "effort": row.get("effort") or "",
        "computer": assignment.get("label") or "",
        "online": bool(row.get("online")),
    }
    step = _next_step(row)
    if step:
        out["next"] = step
    return out


STATES = ("planned", "active", "paused", "quarantined", "archived")


@tool(
    "tico_bot_list",
    "Every bot you may see, one short row each: state (planned, active, paused, quarantined, archived), stage "
    "(onboarding or active), setup (`needs_setup` while onboarding), what it is doing, who it reports to, its group, "
    "template, model, harness, effort and computer, and `next`: why it takes no work and the one command that fixes it. "
    "`counts` sums them. Anyone may run it; the server shows each caller the bots they may see.",
    {
        "state": _s("Only bots in this state", enum=list(STATES)),
        "stage": _s("Only onboarding or only active bots", enum=["onboarding", "active"]),
        "needs_action": _flag("Only the bots that take no work now (those with a `next`)"),
        "archived": _flag("Also archived bots"),
    },
    audience=REQUESTER,
)
def bot_list(api, args):
    bots = api.get(
        "bots", **({"include_archived": "1"} if args.get("archived") or args.get("state") == "archived" else {})
    )
    slugs = _actor_names(bots)
    rows = [_bot_row(b, slugs) for b in bots]
    if args.get("state"):
        rows = [r for r in rows if r["state"] == args["state"]]
    if args.get("stage"):
        rows = [r for r in rows if r["stage"] == args["stage"]]
    if args.get("needs_action"):
        rows = [r for r in rows if r.get("next")]
    counts = {"bots": len(rows), "needs_action": sum(1 for r in rows if r.get("next"))}
    for field in ("state", "stage"):
        for r in rows:
            counts.setdefault(field, {}).setdefault(r[field] or "", 0)
            counts[field][r[field] or ""] += 1
    return {"bots": rows, "counts": counts}


@tool(
    "tico_bot_go_live_all",
    "Take every planned bot live in one call, as `tico_bot_go_live` does for one: placed if it has no computer, turned "
    "on, its setup started. With setup_done, also mark onboarding complete for each bot still onboarding, so it takes "
    "tasks now (only a person above it on the org chart may: its manager, theirs, or the owner). dry_run lists what it "
    "would do and changes nothing. One bot's refusal does not stop the rest; each is in `failed` with the server's "
    "reason. Run by the owner, an admin or a person who manages the bots.",
    {
        "setup_done": _flag("Also mark each onboarding bot's setup done"),
        "setup": _flag("Start each starter bot's setup chat (default: on, off with setup_done)"),
        "computer": _s("Put them all on this computer (label or id); leave out to let Tico pick for each"),
        "dry_run": _flag("Only list what it would do"),
    },
    writes=True,
)
def bot_go_live_all(api, args):
    who = _person_api(api)
    bots = who.get("bots")
    planned = [b["slug"] for b in bots if b.get("state") == "planned"]
    onboarding = [
        b["slug"]
        for b in bots
        if b.get("state") != "archived" and (b.get("onboarding_state") or "") in ("needs_setup", "needs_onboarding")
    ]
    setup_done = bool(args.get("setup_done"))
    if args.get("dry_run"):
        return {
            "dry_run": True,
            "go_live": planned,
            "setup_done": sorted(set(onboarding) | (set(planned) if setup_done else set())) if setup_done else [],
        }
    setup = args.get("setup")
    setup = (not setup_done) if setup is None else bool(setup)
    went, waiting, done, failed = [], [], [], []
    for slug in planned:
        try:
            answer = who.post(
                f"bots/{quote(slug, safe='')}/go-live",
                {"computer": args.get("computer") or "", "setup": setup},
                key=_key(args, ":live:" + slug),
            )
        except APIError as exc:
            failed.append({"bot": slug, "step": "go-live", "error": exc.code, "detail": exc.detail})
            continue
        (waiting if (answer or {}).get("state") == "waiting_for_repository" else went).append(slug)
    if setup_done:
        # Read again: going live may have changed a bot's setup state.
        now = {b["slug"]: b for b in who.get("bots")}
        for slug, row in now.items():
            if row.get("state") == "archived" or (row.get("onboarding_state") or "") not in (
                "needs_setup",
                "needs_onboarding",
            ):
                continue
            try:
                who.post(f"bots/{quote(slug, safe='')}/onboarded", {}, key=_key(args, ":done:" + slug))
                done.append(slug)
            except APIError as exc:
                failed.append({"bot": slug, "step": "setup-done", "error": exc.code, "detail": exc.detail})
    return {"went_live": went, "waiting_for_repository": waiting, "setup_done": done, "failed": failed}


@tool(
    "tico_bot_quarantine_clear",
    "Lift a bot's quarantine (a refused or stopped run held its work) so it is active and takes work again. Read why it "
    "was quarantined first (tico_health_check, the bot's page). Run by a person who manages the bot, or BotOps as the "
    "person who asked it. Refused with 409 when the bot is not quarantined.",
    {"bot": _s("The bot's slug"), "note": _s("Why it is safe to resume; kept with the change")},
    required=("bot",),
    writes=True,
)
def bot_quarantine_clear(api, args):
    body = {"note": args.get("note") or "", **_for_person(api)}
    return api.post(f"bots/{quote(str(args['bot']), safe='')}/quarantine/clear", body, key=_key(args))


# ----------------------------------------------------------------------------- the org in one call
def _roles(api):
    """person id -> their access row (role, sign_in, create_bots, add_people, left, removable, invited_at) when the caller may read
    who can sign in (the owner and admins); else None."""
    access = _try(api.get, "access")
    if not isinstance(access, dict):
        return None
    invites = access.get("invites") or {}
    out = {}
    for p in access.get("people") or []:
        out[str(p.get("id"))] = {
            "role": p.get("role"),
            "sign_in": p.get("can_sign_in"),
            "create_bots": p.get("create_bots"),
            "add_people": p.get("add_people"),
            "left": bool(p.get("left")),
            "removable": bool(p.get("removable")),
            "email": p.get("email"),
            "name": p.get("name"),
            "invited_at": invites.get(str(p.get("id"))),
        }
    return out


def _stage_holder(owner, slugs):
    owner = str(owner or "")
    if owner in ("", "assignee"):
        return "assignee"
    return _teammate(owner, slugs)


@tool(
    "tico_org_show",
    "The company's org in one call: each person (with their role when you may see roles: owner, admin, member), each bot "
    "(state, stage, setup), the groups and Recruiting seats, who reports to whom (`reports`: each manager's direct "
    "reports), each pipeline with its manager and who holds each stage, and `chart`: the same as indented lines, the "
    "people at the top. Anyone may run it; the server leaves out who the caller may not see.",
    {},
    audience=REQUESTER,
)
def org_show(api, args):
    org = api.get("org")
    bots = api.get("bots")
    slugs = _actor_names(bots)
    live = {b.get("slug"): b for b in bots}
    roles = _roles(api)
    people = []
    for p in org.get("people") or []:
        pid = str(p.get("id"))
        row = {
            "id": "human:" + pid,
            "name": p.get("name"),
            "email": p.get("email"),
            "title": p.get("title") or "",
            "group": p.get("team") or "",
            "reports_to": ("human:" + p["reports_to"]) if p.get("reports_to") else "",
        }
        if roles is not None:
            row["role"] = (roles.get(pid) or {}).get("role")
        people.append(row)
    team = []
    for b in org.get("bots") or []:
        slug = b.get("slug") or b.get("id")
        detail = live.get(slug) or {}
        team.append(
            {
                "id": "bot:" + str(slug),
                "name": b.get("display_name") or slug,
                "state": b.get("status") or detail.get("state"),
                "stage": b.get("stage"),
                "setup": b.get("onboarding_state") or "",
                "group": b.get("team") or "",
                "template": b.get("template") or "",
                "reports_to": _teammate(b.get("reports_to"), slugs),
            }
        )
    pipelines = []
    holds, manages = {}, {}
    listed = _try(api.get, "pipelines")
    for p in (listed or {}).get("pipelines") or []:
        if p.get("retired_at"):
            continue
        manager = _teammate(p.get("manager_id"), slugs) if p.get("manager_id") else ""
        stages = [
            {"stage": s.get("name"), "holder": _stage_holder(s.get("owner"), slugs)} for s in p.get("stages") or []
        ]
        pipelines.append({"id": p.get("id"), "name": p.get("name"), "manager": manager, "stages": stages})
        if manager:
            manages.setdefault(manager, []).append(p.get("name"))
        for s in stages:
            if s["holder"] != "assignee":
                holds.setdefault(s["holder"], []).append(f"{p.get('name')}: {s['stage']}")
    reports = {}
    for row in people + team:
        if row["reports_to"]:
            reports.setdefault(row["reports_to"], []).append(row["id"])
    for row in people + team:
        if row["id"] in manages:
            row["manages_pipelines"] = manages[row["id"]]
        if row["id"] in holds:
            row["holds_stages"] = holds[row["id"]]
    return {
        "people": people,
        "bots": team,
        "groups": [
            {"id": g.get("id"), "name": g.get("name"), "parent": g.get("parent") or ""}
            for g in org.get("org_groups") or []
        ],
        "seats": [
            {"id": s.get("id"), "title": s.get("title"), "reports_to": _teammate(s.get("reports_to"), slugs)}
            for s in org.get("seats") or []
        ],
        "pipelines": pipelines,
        "reports": reports,
        "chart": _chart(people, team, reports),
        **({} if listed is not None else {"pipelines_unavailable": True}),
        **({} if roles is not None else {"roles_hidden": "only the owner and admins see roles"}),
    }


def _chart(people, bots, reports):
    """The org as indented lines, each teammate under its manager: people with no manager first, then bots with none."""
    rows = {r["id"]: r for r in people + bots}

    def line(r):
        bits = [r["id"]]
        if r.get("role"):
            bits.append(r["role"])
        if r["id"].startswith("bot:"):
            bits.append(r.get("state") or "")
            if r.get("setup") in ("needs_setup", "needs_onboarding"):
                bits.append("onboarding")
        text = f"{r.get('name')} ({', '.join(b for b in bits if b)})"
        if r.get("manages_pipelines"):
            text += " · manages " + ", ".join(r["manages_pipelines"])
        if r.get("holds_stages"):
            text += " · holds " + "; ".join(r["holds_stages"])
        return text

    out, seen = [], set()

    def walk(rid, depth):
        if rid in seen or rid not in rows:
            return
        seen.add(rid)
        out.append("  " * depth + line(rows[rid]))
        for child in reports.get(rid, []):
            walk(child, depth + 1)

    for r in people:
        if not r["reports_to"] or r["reports_to"] not in rows:
            walk(r["id"], 0)
    top_bots = [r["id"] for r in bots if not r["reports_to"] or r["reports_to"] not in rows]
    if top_bots:
        out.append("No manager on the chart (they count as reporting to the owner):")
        for rid in top_bots:
            walk(rid, 1)
    for rid in rows:  # anything left is in a reporting loop; still list it once
        walk(rid, 0)
    return out


# ----------------------------------------------------------------------------- people
def _person(api, ref, roles=None):
    """One person by id (`human:` optional), email or exact name, from the roster (and, for the owner and admins, the
    sign-in list, which still has people marked as left)."""
    ref = str(ref or "").strip().removeprefix("human:").removeprefix("person:")
    org = api.get("org")
    rows = {
        str(p.get("id")): {"id": str(p.get("id")), "name": p.get("name"), "email": p.get("email"), **p}
        for p in org.get("people") or []
    }
    for pid, row in (roles or {}).items():
        rows.setdefault(pid, {"id": pid, "name": row.get("name"), "email": row.get("email")})
    found = [
        r
        for r in rows.values()
        if ref in (r["id"], r.get("name") or "") or ref.lower() == str(r.get("email") or "").lower()
    ]
    if len(found) != 1:
        raise APIError(
            "not_found" if not found else "ambiguous",
            f"No one on the roster is {ref!r}" if not found else f"{ref!r} names {len(found)} people; use their id",
            404 if not found else 422,
        )
    return found[0]


@tool(
    "tico_human_show",
    "One person: their profile (name, email, title, group, who they report to, GitHub login), their direct reports "
    "(people and bots), and for the owner and admins their role (owner, admin, member), whether they may sign in, add "
    "bots and add people, and when they were last invited. Take a person's id, email or exact name.",
    {"human": _s("The person: their id, email or exact name")},
    required=("human",),
    audience=REQUESTER,
)
def human_show(api, args):
    roles = _roles(api)
    person = _person(api, args["human"], roles)
    pid = person["id"]
    out = {
        "id": "human:" + pid,
        **{k: person.get(k) for k in ("name", "email", "title", "github", "about")},
        "group": person.get("team") or "",
        "reports_to": ("human:" + person["reports_to"]) if person.get("reports_to") else "",
    }
    if roles is not None and pid in roles:
        out.update({k: v for k, v in roles[pid].items() if k not in ("name", "email")})
    org = api.get("org")
    out["reports"] = ["human:" + str(p["id"]) for p in org.get("people") or [] if p.get("reports_to") == pid] + [
        "bot:" + str(b.get("slug") or b.get("id"))
        for b in org.get("bots") or []
        if str(b.get("reports_to") or "") in (pid, "human:" + pid)
    ]
    return out


@tool(
    "tico_human_invite",
    "Send someone on the roster an invite to sign in, from Tico's own sign-in mail (never the company's mailbox). "
    "Sending again sends again. The owner or an admin. Add someone new first with tico_human_add.",
    {"human": _s("The person: their id, email or exact name")},
    required=("human",),
    writes=True,
    audience=PEOPLE,
)
def human_invite(api, args):
    person = _person(api, args["human"], _roles(api))
    return api.post(f"access/humans/{quote(person['id'], safe='')}/invite", {}, key=_key(args))


@tool(
    "tico_human_remove",
    "Take a person off the team, as the owner: they are marked as left (off the org chart and every list, their sign-in "
    "and tokens end, their history stays). `confirm` must be their exact name or email, so a wrong id removes no one. "
    "With delete, someone with no history (added by mistake) is deleted for good; the server refuses it for anyone "
    "with history. Undo a left mark with tico_settings_set people.person.left.",
    {
        "human": _s("The person: their id, email or exact name"),
        "confirm": _s("Their exact name or email, as tico_human_show shows it"),
        "delete": _flag("Delete them for good (only someone with no history)"),
    },
    required=("human", "confirm"),
    writes=True,
    audience=PEOPLE,
)
def human_remove(api, args):
    person = _person(api, args["human"], _roles(api))
    names = [v for v in (person.get("name"), person.get("email")) if v]
    if args["confirm"] not in names:
        raise APIError(
            "confirmation_required",
            f"--confirm must be {' or '.join(repr(n) for n in names)} exactly; no one was removed",
            409,
        )
    pid = quote(person["id"], safe="")
    api.post(f"humans/{pid}", {"left": True}, key=_key(args, ":left"))
    deleted = False
    if args.get("delete"):
        api.call("DELETE", f"humans/{pid}", None, _key(args, ":delete"))
        deleted = True
    return {"person": "human:" + person["id"], "name": person.get("name"), "left": True, "deleted": deleted}


# ----------------------------------------------------------------------------- usage and billing
USAGE_GROUPS = ("bot", "day", "routine", "task", "harness", "model", "effort", "subscription")


@tool(
    "tico_usage_show",
    "Estimated model spend over a window (default the last 7 days), grouped by bot, day, routine, task, harness, model, "
    "effort or subscription, with totals; `bot` gives one bot's daily series and top routines. People only: each sees "
    "the bots whose usage they may read (their own, those they manage; the owner and admins: all).",
    {
        "group": _s("How to group the rows (default bot)", enum=list(USAGE_GROUPS)),
        "from": _s("First UTC day, YYYY-MM-DD"),
        "to": _s("Last UTC day, YYYY-MM-DD"),
        "bot": _s("One bot's slug: its daily series"),
        "bots": _s("Only these bots: slugs, comma-separated"),
    },
    audience=PEOPLE,
)
def usage_show(api, args):
    query = {k: args[k] for k in ("group", "from", "to", "bot", "bots") if args.get(k)}
    return api.get("usage", **query)


@tool(
    "tico_usage_limits",
    "The spend limits: the company default, each bot you may see usage for, and (owner and admins) the company's and "
    "each group's combined limits, each with what it has spent in each window. People only. Change one with "
    "tico_settings_set (bot.limit.usd, team.limit.usd, pool.limit.usd).",
    {},
    audience=PEOPLE,
)
def usage_limits(api, args):
    return api.get("usage/limits")


@tool(
    "tico_billing_show",
    "The company's Tico balance and status, credits, usage by day and the month's statement (from Tico HQ). The owner "
    "and admins.",
    {"month": _s("YYYY-MM; this month by default")},
    audience=PEOPLE,
)
def billing_show(api, args):
    return api.get("billing", **({"month": args["month"]} if args.get("month") else {}))


# ----------------------------------------------------------------------------- reach
def _party(value):
    """A reach party as the API takes it: `*`, `group:<id>`, `human:<id>` or `bot:<slug>`; a bare word is a bot."""
    value = str(value or "").strip()
    if value in ("*", "everyone"):
        return "*"
    if value.startswith("person:"):
        return "human:" + value[len("person:") :]
    return value if ":" in value else "bot:" + value


@tool(
    "tico_reach_show",
    "Reach (Rules › Internal): who each teammate may reach and how often. Without a party: the defaults, every stored "
    "setting, the lines and the parties you may change. With a party: its effective mode, chats_per_day and "
    "tasks_per_day (and where each comes from), its own setting and the lines from or to it. Anyone may read it.",
    {"party": _s("`*` (everyone), group:<id>, human:<id> or a bot's slug")},
    audience=REQUESTER,
)
def reach_show(api, args):
    whole = api.get("reach")
    if not args.get("party"):
        return whole
    party = _party(args["party"])
    effective = whole.get("effective") or {}
    if party not in effective:
        bot_id = None
        if party.startswith("bot:"):  # stored by the bot's id; a slug is accepted on the way in
            row = next((b for b in api.get("bots") if b.get("slug") == party[4:]), None)
            bot_id = "bot:" + str(row.get("id")) if row else None
        if bot_id not in effective:
            raise APIError("not_found", f"No party {args['party']!r} you may see on the reach map", 404)
        party = bot_id
    return {
        "party": party,
        "effective": effective[party],
        "setting": next((s for s in whole.get("settings") or [] if s.get("party") == party), None),
        "lines": [l for l in whole.get("lines") or [] if party in (l.get("sender"), l.get("receiver"))],
        "may_change": party in (whole.get("may_edit") or []),
    }


def _per_day(value):
    if value is None:
        return None
    if str(value).strip().lower() in ("inherit", "default", ""):
        return "inherit"
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"a per-day count is a whole number or inherit, not {value!r}") from None
    if number < 0:
        raise ValueError("a per-day count is 0 or more")
    return number


@tool(
    "tico_reach_set",
    "Set one party's reach: mode (open, nearby, closed), chats_per_day, tasks_per_day; `inherit` clears a field so it "
    "comes from the teams above again. Only what you give changes. The owner and admins set any party but the owner; "
    "a team lead their teams and those in them; a bot's managers that bot; nobody their own.",
    {
        "party": _s("`*` (everyone), group:<id>, human:<id> or a bot's slug"),
        "mode": _s("open, nearby or closed; inherit clears it", enum=["open", "nearby", "closed", "inherit"]),
        "chats_per_day": {"type": ["integer", "string"], "description": "0-100000, or inherit"},
        "tasks_per_day": {"type": ["integer", "string"], "description": "0-100000, or inherit"},
    },
    required=("party",),
    writes=True,
)
def reach_set(api, args):
    body = {}
    if args.get("mode"):
        body["mode"] = None if args["mode"] == "inherit" else args["mode"]
    for field in ("chats_per_day", "tasks_per_day"):
        value = _per_day(args.get(field))
        if value is not None:
            body[field] = None if value == "inherit" else value
    if not body:
        raise ValueError("Give at least one of mode, chats_per_day, tasks_per_day")
    party = quote(_party(args["party"]), safe=":")
    return _person_api(api).call("PUT", f"reach/settings/{party}", body, _key(args))


# ----------------------------------------------------------------------------- the `tico` commands
# command words -> its tool; clients/remotecli.py runs each through via_tool.
COMMANDS = {
    "bot list": "tico_bot_list",
    "bot go-live-all": "tico_bot_go_live_all",
    "bot quarantine-clear": "tico_bot_quarantine_clear",
    "org show": "tico_org_show",
    "human show": "tico_human_show",
    "human invite": "tico_human_invite",
    "human remove": "tico_human_remove",
    "usage show": "tico_usage_show",
    "usage limits": "tico_usage_limits",
    "billing show": "tico_billing_show",
    "reach show": "tico_reach_show",
    "reach set": "tico_reach_set",
}
# Subcommands of `tico org` itself, which clients/hubcli.py must not rewrite to the old `tico org` (`team show`).
ORG_COMMANDS = ("show",)


def _subcommands(sub, noun, help_):
    """The subcommand group under `tico <noun>`, made when the CLI has no such noun yet."""
    parser = sub.choices.get(noun) or sub.add_parser(noun, help=help_)
    found = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    return found or parser.add_subparsers(dest="sub")


def _command(group, words, help_, example):
    s = group.add_parser(
        words.split()[-1],
        help=help_,
        description=help_[:1].upper() + help_[1:] + ".",  # `--help` says who may run it too
        epilog="Example: " + example,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.set_defaults(fn=words)
    return s


def add_commands(sub):
    """The commands for these tools under `tico bot`, `tico org`, `tico human`, `tico usage`, `tico billing` and `tico
    reach`. `sub` is the CLI's top-level subcommands (clients/hubcli.py `parser`)."""
    bot = _subcommands(sub, "bot", "bots")
    s = _command(
        bot,
        "bot list",
        "every bot's state, stage and setup in one short row, with the command that gets a stuck one working (anyone)",
        "tico bot list --needs-action",
    )
    s.add_argument("--state", choices=STATES)
    s.add_argument("--stage", choices=["onboarding", "active"])
    s.add_argument(
        "--needs-action", dest="needs_action", action="store_true", default=None, help="only bots that take no work now"
    )
    s.add_argument("--archived", action="store_true", default=None, help="also archived bots")
    s = _command(
        bot,
        "bot go-live-all",
        "take every planned bot live; --setup-done also finishes onboarding (the owner, admins, the bots' managers)",
        "tico bot go-live-all --setup-done   (tico bot go-live-all --dry-run first to see the list)",
    )
    s.add_argument("--setup-done", dest="setup_done", action="store_true", default=None, help="also mark setup done")
    s.add_argument("--setup", action=argparse.BooleanOptionalAction, default=None, help="start each setup chat")
    s.add_argument("--computer", help="put them all on this computer")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", default=None, help="only list what it would do")
    s = _command(
        bot,
        "bot quarantine-clear",
        "lift a bot's quarantine so it works again (a person who manages the bot)",
        'tico bot quarantine-clear builder --note "the refused push was a test"',
    )
    s.add_argument("bot")
    s.add_argument("--note", help="why it is safe to resume")

    org = _subcommands(sub, "org", "the org chart")
    _command(
        org,
        "org show",
        "the org in one call: people and roles, bots, who reports to whom, who holds each pipeline stage (anyone)",
        "tico org show",
    )

    human = _subcommands(sub, "human", "the humans on the team")
    s = _command(
        human,
        "human show",
        "one person: profile, direct reports, and (owner and admins) role and sign-in",
        "tico human show ben@acme.example",
    )
    s.add_argument("human", help="their id, email or exact name")
    s = _command(
        human,
        "human invite",
        "send someone on the roster an invite to sign in (owner and admins)",
        "tico human invite ben",
    )
    s.add_argument("human", help="their id, email or exact name")
    s = _command(
        human,
        "human remove",
        "mark someone as left; --delete removes someone with no history for good (the owner)",
        'tico human remove ben --confirm "Ben Ortiz"',
    )
    s.add_argument("human", help="their id, email or exact name")
    s.add_argument("--confirm", required=True, help="their exact name or email")
    s.add_argument("--delete", action="store_true", default=None, help="delete for good (no history only)")

    usage = _subcommands(sub, "usage", "model spend and its limits (people)")
    s = _command(
        usage,
        "usage show",
        "estimated model spend by bot, day, routine, task, harness, model, effort or subscription (people)",
        "tico usage show --group model --from 2026-11-01",
    )
    s.add_argument("--group", choices=USAGE_GROUPS)
    s.add_argument("--from", dest="from")
    s.add_argument("--to")
    s.add_argument("--bot", help="one bot's daily series")
    s.add_argument("--bots", help="only these bots, comma-separated")
    _command(usage, "usage limits", "the spend limits and what each has spent (people)", "tico usage limits")

    billing = _subcommands(sub, "billing", "the company's Tico balance and statement (owner and admins)")
    s = _command(
        billing,
        "billing show",
        "balance, credits, usage by day and the statement (owner and admins)",
        "tico billing show",
    )
    s.add_argument("--month", help="YYYY-MM")

    reach = _subcommands(sub, "reach", "who each teammate may reach, and how often (Rules › Internal)")
    s = _command(
        reach,
        "reach show",
        "the reach map, or one party's effective reach and lines (anyone)",
        "tico reach show bot:builder",
    )
    s.add_argument("party", nargs="?", help="*, group:<id>, human:<id> or a bot's slug")
    s = _command(
        reach,
        "reach set",
        "set a party's mode and daily counts; inherit clears one (owner, admins, team leads, a bot's managers)",
        "tico reach set bot:builder --mode closed --tasks-per-day 20",
    )
    s.add_argument("party", help="*, group:<id>, human:<id> or a bot's slug")
    s.add_argument("--mode", choices=["open", "nearby", "closed", "inherit"])
    s.add_argument("--chats-per-day", dest="chats_per_day", help="a number, or inherit")
    s.add_argument("--tasks-per-day", dest="tasks_per_day", help="a number, or inherit")
