"""Strategy, brand, growth and lenses.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import argparse
import json
from urllib.parse import quote
import time
from clients.tico import APIError

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s, _target
from clients.commands._requester import _as_person
from clients.commands._shared import json_file


# ----------------------------------------------------------------------------- strategy
def _wrong_if(args):
    """The wrong-if the flags describe, {} to clear it when every flag is empty strings, None when none is sent."""
    if args.get("wrong_if") is not None:
        return args["wrong_if"]
    if args.get("wrong_if_kpi"):
        out = {
            "kind": "kpi",
            "kpi_id": args["wrong_if_kpi"],
            "direction": args.get("wrong_if_direction"),
            "threshold": args.get("wrong_if_threshold"),
        }
    elif args.get("wrong_if_thesis"):
        out = {"kind": "thesis", "market_id": args["wrong_if_thesis"]}
    elif args.get("wrong_if_text"):
        out = {"kind": "text", "text": args["wrong_if_text"]}
    else:
        return None
    if args.get("check_by"):
        out["check_by"] = args["check_by"]
    return out


def _draft_revision(api, args):
    if args.get("revision") is not None:
        return int(args["revision"])
    draft = _as_person(api).get("strategy").get("draft")
    if not draft:
        raise APIError(
            "forbidden",
            "Only the owner writes the strategy; a bot proposes a change with tico_proposal_create, kind strategy_draft",
            403,
        )
    return draft["revision"]


# The fixed sections, in the page's order (backend/strategy.py FIXED_KEYS); any other key is an extra section.
STRATEGY_SECTIONS = ("serve", "sell", "proof", "now", "know", "how", "not")


BET_PROPS = {
    "why": _s("The reasoning, one or two sentences"),
    "rules_out": _s("What the bet refuses; an active bet needs one"),
    "wrong_if": {
        "type": "object",
        "description": "What would show it is wrong: {kind: kpi, kpi_id, direction "
        "above|below, threshold}, {kind: thesis, market_id} or {kind: text, text, check_by}; check_by "
        "(YYYY-MM-DD) on any. An active bet needs one; {} clears it",
    },
    "wrong_if_kpi": _s("Instead of wrong_if: the KPI id"),
    "wrong_if_direction": {
        "type": "string",
        "enum": ["above", "below"],
        "description": "Instead of wrong_if: wrong when the KPI goes above or below the threshold",
    },
    "wrong_if_threshold": {"type": "number", "description": "Instead of wrong_if: the KPI's value that would show it"},
    "wrong_if_thesis": _s("Instead of wrong_if: the market id it watches"),
    "wrong_if_text": _s("Instead of wrong_if: the observation, in words (needs check_by)"),
    "check_by": _s("YYYY-MM-DD: the day to look again"),
    "cites": {"type": "array", "items": {"type": "string"}, "description": "Market entity or page ids it rests on"},
    "status": {"type": "string", "enum": ["proposed", "active", "retired"], "description": "Proposed by default"},
    "rank": {"type": "integer", "description": "Its place in the list"},
}


def _bet_body(args):
    body = {
        k: args.get(k)
        for k in ("statement", "why", "rules_out", "cites", "status", "rank", "revision")
        if args.get(k) is not None
    }
    wrong = _wrong_if(args)
    if wrong is not None:
        body["wrong_if"] = wrong
    return body


@tool(
    "tico_strategy_show",
    "The team's published strategy: summary, vision, the seven sections, the bets with their "
    "wrong-ifs, each bet's goals and colour, the team's website, and proof_since (when How we know they want it "
    "last changed). The owner also gets the draft. With `section`, only that section of the published strategy.",
    {
        "section": {
            "type": "string",
            "enum": list(STRATEGY_SECTIONS),
            "description": "Only this section of the published strategy, as text (proof: How we know they want it; "
            "now: Why now; how: How we sell)",
        }
    },
)
def strategy_show(api, args):
    strategy = _as_person(api).get("strategy")
    if not args.get("section"):
        return strategy
    current = strategy.get("current") if isinstance(strategy, dict) else None
    text = (current.get("sections") or {}).get(args["section"]) or "" if current else None
    return {"section": args["section"], "text": text}


@tool(
    "tico_strategy_summary",
    "The published strategy as the text every run's context carries: the summary, one line "
    "per active bet with what would show it wrong, what we won't do, the version.",
    {},
)
def strategy_summary(api, args):
    return api.get("strategy/summary")


@tool(
    "tico_strategy_draft",
    "Owner only: change the strategy draft, only the fields sent (\"\" clears one). A bot "
    "proposes instead: tico_proposal_create, kind strategy_draft.",
    {
        "summary": _s("At most 400 characters: what every bot reads"),
        "vision": _s("One or two sentences"),
        "sections": {
            "type": "object",
            "description": "{serve, sell, proof, now, know, how, not, extra: {key: {title, body}}}, any of them",
        },
        "section": {
            "type": "array",
            "items": {"type": "string"},
            "description": "KEY=TEXT, instead of sections: serve, sell, proof, now, know, how, not, or an extra "
            "section's key; KEY= clears it",
        },
        "revision": {"type": "integer", "description": "The draft's revision you read; default the current one"},
    },
    writes=True,
)
def strategy_draft(api, args):
    sections = dict(args.get("sections") or {})
    for item in args.get("section") or []:
        key, _, text = str(item).partition("=")
        if key.strip() in STRATEGY_SECTIONS:
            sections[key.strip()] = text
        else:
            sections.setdefault("extra", {})[key.strip()] = (
                {"title": key.strip().replace("-", " ").capitalize(), "body": text} if text else ""
            )
    body = {"revision": _draft_revision(api, args)}
    for k in ("summary", "vision"):
        if args.get(k) is not None:
            body[k] = args[k]
    if sections:
        body["sections"] = sections
    return _as_person(api).call("PUT", "strategy/draft", body, _key(args))["draft"]


@tool(
    "tico_strategy_publish",
    "Owner only: the draft becomes the next numbered version. Needs an active bet with "
    "rules_out and wrong_if, and \"What we won't do\".",
    {
        "note": _s("What this version is for, in a line", default=""),
        "revision": {"type": "integer", "description": "The draft's revision you read; default the current one"},
    },
    writes=True,
)
def strategy_publish(api, args):
    return _as_person(api).post(
        "strategy/publish", {"note": args.get("note") or "", "revision": _draft_revision(api, args)}, key=_key(args)
    )


@tool(
    "tico_strategy_versions",
    "The published strategy versions, newest first; with n, that version whole.",
    {"n": {"type": "integer", "description": "One version's number"}},
)
def strategy_versions(api, args):
    return api.get(f"strategy/versions/{int(args['n'])}") if args.get("n") else api.get("strategy/versions")


@tool(
    "tico_strategy_diff",
    "What changed between two published versions: fields, and bets added, retired or changed.",
    {
        "from_n": {"type": "integer", "description": "The older version's number"},
        "to_n": {"type": "integer", "description": "The newer version's number"},
    },
    required=("from_n", "to_n"),
)
def strategy_diff(api, args):
    return api.get("strategy/diff", **{"from": int(args["from_n"]), "to": int(args["to_n"])})


@tool(
    "tico_strategy_bet_add",
    "Owner only: a new bet, proposed unless status says otherwise. A bot proposes one with "
    "tico_proposal_create, kind strategy_draft.",
    {"statement": _s("One sentence that could turn out wrong"), **BET_PROPS},
    required=("statement",),
    writes=True,
)
def strategy_bet_add(api, args):
    return _as_person(api).post("strategy/bets", _bet_body(args), key=_key(args))


@tool(
    "tico_strategy_bet_update",
    "Owner only: change a bet, only the fields sent.",
    {"id": _s("Bet id"), "statement": _s("One sentence that could turn out wrong"), **BET_PROPS},
    required=("id",),
    writes=True,
)
def strategy_bet_update(api, args):
    return _as_person(api).post("strategy/bets/" + args["id"], _bet_body(args), key=_key(args))


@tool(
    "tico_strategy_bet_retire",
    "Owner only: retire a bet. Its goals keep their link; the next publish asks the owner what to do with them.",
    {"id": _s("Bet id")},
    required=("id",),
    writes=True,
)
def strategy_bet_retire(api, args):
    return _as_person(api).post("strategy/bets/" + args["id"], {"status": "retired"}, key=_key(args))


# ----------------------------------------------------------------------------- brand, growth
BRAND_SECTIONS = ("name", "positioning", "voice", "tone", "words", "naming", "visual", "claims", "examples")


@tool(
    "tico_brand_show",
    "The brand page: the published version, the version list, and for the owner and admins the "
    "draft. With section, only that section's value.",
    {
        "section": {"type": "string", "enum": list(BRAND_SECTIONS), "description": "Only this section"},
        "version": {"type": "integer", "description": "A published version's number; default the latest"},
    },
)
def brand_show(api, args):
    if args.get("version"):
        page = _as_person(api).get(f"brand/versions/{int(args['version'])}")
        body = page.get("body") or {}
    else:
        page = _as_person(api).get("brand")
        body = ((page.get("published") or {}).get("body")) or {}
    if args.get("section"):
        return {
            "section": args["section"],
            "n": page.get("n") or (page.get("published") or {}).get("n"),
            "value": body.get(args["section"]),
        }
    return page


@tool("tico_brand_summary", "The published brand page as the short text a growth-facing bot's turn carries.", {})
def brand_summary(api, args):
    return api.get("brand/summary")


def _brand_sections(args):
    """`sections` as an object, or one `section` with its `body` as JSON text (the CLI's --section/--body)."""
    sections = dict(args.get("sections") or {})
    if args.get("section"):
        raw = args.get("body")
        try:
            sections[args["section"]] = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            raise APIError("body", "--body must be JSON: a string in quotes, a list or an object") from None
    return sections


@tool(
    "tico_brand_draft",
    "Owner or admin only: change sections of the brand draft (a section set to null is removed); "
    "publish makes it a version. A bot proposes instead: tico_proposal_create, kind brand_draft, payload "
    "{sections: {...}}.",
    {
        "sections": {"type": "object", "description": "{section: value or null}: " + ", ".join(BRAND_SECTIONS)},
        "section": {"type": "string", "enum": list(BRAND_SECTIONS), "description": "One section, with body"},
        "body": _s("That section's value as JSON text; null removes it"),
        "revision": {"type": "integer", "description": "The draft's revision you read; stale is refused"},
    },
    writes=True,
)
def brand_draft(api, args):
    body = {"sections": _brand_sections(args)}
    if args.get("revision") is not None:
        body["revision"] = int(args["revision"])
    return _as_person(api).call("PUT", "brand/draft", body, _key(args))


@tool(
    "tico_brand_publish",
    "Owner or admin only: the brand draft becomes the next numbered version.",
    {"note": _s("What this version changes, in a line", default="")},
    writes=True,
)
def brand_publish(api, args):
    return _as_person(api).post("brand/publish", {"note": args.get("note") or ""}, key=_key(args))


@tool(
    "tico_brand_check",
    "Check a draft (a post, an email, an ad) against the published brand: words to avoid, "
    "wrong spellings of names, claims the brand never makes. Each hit has kind, match, rule, level (block or warn) "
    "and index. Fix every block before it goes out.",
    {"text": _s("The draft, whole (on a command line, a file holding it, or - for standard input)")},
    required=("text",),
)
def brand_check(api, args):
    return api.post("brand/check", {"text": args["text"]})


CHANNEL_PROPS = {
    "name": _s("What people call it: Google Search"),
    "track": {"type": "string", "enum": ["performance", "brand"], "description": "Judged by CAC, or by reach"},
    "stage": {"type": "string", "enum": ["idea", "testing", "scaling", "paused"]},
    "owner": _s("Who runs it: me, a bot slug or a person id; \"\" clears"),
    "budget_month": {"type": "number", "description": "Spend per month, in the team's unit"},
    "source_id": _s("The source that holds its spend"),
    "why": _s("Why we use it, in a line"),
    "guardrail_kpi_id": _s("A KPI that must stay under guardrail_max"),
    "guardrail_max": {"type": "number", "description": "The most that KPI may reach"},
    "sort": {"type": "integer", "description": "Its place in the list, lowest first"},
}


def _channel_body(api, args, keys):
    body = {k: args[k] for k in keys if k in CHANNEL_PROPS and k != "owner" and args.get(k) is not None}
    if args.get("owner") is not None:
        body["owner_ref"] = _target(api, args["owner"]) if args["owner"] else None
    return body


# Every Growth read's answer carries these two (backend/growth_setup.py).
SETUP_NOTE = (
    " you_may: what you may do, per action (settings, channels, sources, months). setup: while Growth is not set up, "
    "each step and the next one with its command, its tool and who may do it; then only done."
)


@tool(
    "tico_growth_channel_list",
    "The growth channels by sort then name: stage, owner, monthly budget, why, guardrail. may_edit: you may add "
    "and change channels." + SETUP_NOTE,
    {"include_retired": {"type": "boolean", "default": False, "description": "Also the retired ones"}},
)
def growth_channel_list(api, args):
    return api.get("growth/channels", include_retired="true" if args.get("include_retired") else None)


@tool(
    "tico_growth_channel_show",
    "One channel by slug, with its open marketing tasks (labelled channel:<slug>)." + SETUP_NOTE,
    {"slug": _s("Channel slug")},
    required=("slug",),
)
def growth_channel_show(api, args):
    return api.get("growth/channels/" + args["slug"])


@tool(
    "tico_growth_channel_add",
    "Owner or admin only: a new channel.",
    {"slug": _s("Lowercase, digits and dashes: google-search"), **CHANNEL_PROPS},
    required=("slug", "name"),
    writes=True,
)
def growth_channel_add(api, args):
    body = {"slug": args["slug"], **_channel_body(api, args, CHANNEL_PROPS)}
    return _as_person(api).post("growth/channels", body, key=_key(args))


@tool(
    "tico_growth_channel_update",
    "Change a channel, only the fields sent. Owner or admin; the bot that owns the "
    "channel may change its why, stage and monthly budget.",
    {"slug": _s("Channel slug"), **CHANNEL_PROPS},
    required=("slug",),
    writes=True,
)
def growth_channel_update(api, args):
    return _as_person(api).patch(
        "growth/channels/" + args["slug"], _channel_body(api, args, CHANNEL_PROPS), key=_key(args)
    )


@tool(
    "tico_growth_channel_retire",
    "Owner or admin only: retire a channel. Its cohorts keep the link.",
    {"slug": _s("Channel slug")},
    required=("slug",),
    writes=True,
)
def growth_channel_retire(api, args):
    return _as_person(api).post(f"growth/channels/{args['slug']}/retire", {}, key=_key(args))


SOURCE_PROPS = {
    "truth_for": _s("The number it is the truth for: bookings, ad spend"),
    "system": _s("The system: Stripe, Google Ads"),
    "reach_kind": {
        "type": "string",
        "enum": ["mcp", "credential", "url", "person"],
        "description": "How a bot reaches it",
    },
    "reach_ref": _s("The MCP server, credential name, URL or person"),
    "may": _s("What a bot may do there: read only, pause ads"),
    "owner": _s("Who answers for it: me, a bot slug or a person id"),
}


def _source_body(api, args):
    body = {k: args[k] for k in SOURCE_PROPS if k != "owner" and args.get(k) is not None}
    if args.get("owner") is not None:
        body["owner_ref"] = _target(api, args["owner"]) if args["owner"] else None
    return body


@tool(
    "tico_growth_source_list",
    "Which system holds the truth for each number (bookings, spend, sign-ups), how a bot "
    "reaches it and what it may do there, and any number two sources both claim. may_edit: you may change sources."
    + SETUP_NOTE,
    {"include_retired": {"type": "boolean", "default": False, "description": "Also the retired ones"}},
)
def growth_source_list(api, args):
    return api.get("growth/sources", include_retired="true" if args.get("include_retired") else None)


@tool(
    "tico_growth_source_add",
    "Owner or admin only: name the system that holds the truth for a number.",
    SOURCE_PROPS,
    required=("truth_for", "system", "reach_kind"),
    writes=True,
)
def growth_source_add(api, args):
    return _as_person(api).post("growth/sources", _source_body(api, args), key=_key(args))


@tool(
    "tico_growth_source_update",
    "Owner or admin only: change a source, only the fields sent.",
    {"id": _s("Source id"), **SOURCE_PROPS},
    required=("id",),
    writes=True,
)
def growth_source_update(api, args):
    return _as_person(api).patch("growth/sources/" + args["id"], _source_body(api, args), key=_key(args))


@tool(
    "tico_growth_source_retire",
    "Owner or admin only: retire a source.",
    {"id": _s("Source id")},
    required=("id",),
    writes=True,
)
def growth_source_retire(api, args):
    return _as_person(api).post(f"growth/sources/{args['id']}/retire", {}, key=_key(args))


COHORT_FIELDS = (
    "spend",
    "bot_cost",
    "new_customers",
    "kept_d30",
    "kept_d90",
    "kept_d180",
    "revenue_month",
    "margin_pct",
    "quality",
)


@tool(
    "tico_growth_cohort_list",
    "Monthly cohorts by channel: spend, bot cost, new customers, % kept at 30/90/180 days, revenue and margin."
    + SETUP_NOTE,
    {
        "channel": _s("A channel slug, or unknown"),
        "month": _s("YYYY-MM"),
        "limit": {
            "type": "integer",
            "default": 500,
            "maximum": 2000,
            "description": "At most this many rows (default 500)",
        },
    },
)
def growth_cohort_list(api, args):
    return api.get("growth/cohorts", channel=args.get("channel"), month=args.get("month"), limit=args.get("limit"))


@tool(
    "tico_growth_cohort_set",
    "Set one channel's numbers for one month, only the fields sent (any bot or an admin). "
    "Use channel unknown for customers no channel claims.",
    {
        "channel": _s("A channel slug, or unknown"),
        "month": _s("YYYY-MM"),
        "spend": {"type": "number", "description": "Spend in the month, in the team's unit"},
        "bot_cost": {"type": "number", "description": "What the bots working it cost in the month"},
        "new_customers": {"type": "integer", "minimum": 0, "description": "New customers in the month"},
        "kept_d30": {"type": "number", "description": "% kept after 30 days"},
        "kept_d90": {"type": "number", "description": "% kept after 90 days"},
        "kept_d180": {"type": "number", "description": "% kept after 180 days"},
        "revenue_month": {"type": "number", "description": "Revenue per customer per month"},
        "margin_pct": {"type": "number", "description": "Gross margin %"},
        "quality": {"type": "string", "enum": ["actual", "estimate"]},
        "sources": {"type": "object", "description": "{field: where the number came from}"},
        "source": {"type": "array", "items": {"type": "string"}, "description": "FIELD=TEXT, instead of sources"},
    },
    required=("channel", "month"),
    writes=True,
)
def growth_cohort_set(api, args):
    body = {
        "channel": args["channel"],
        "month": args["month"],
        **{k: args[k] for k in COHORT_FIELDS if args.get(k) is not None},
    }
    sources = dict(args.get("sources") or {})
    for pair in args.get("source") or []:
        field, sep, text = pair.partition("=")
        if not sep:
            raise APIError("source", "--source is FIELD=TEXT")
        sources[field] = text
    if sources:
        body["sources"] = sources
    return api.call("PUT", "growth/cohorts", body, _key(args))


@tool(
    "tico_growth_economics",
    "Unit economics by channel for a month: CAC (spend plus bot cost per new customer), "
    "expected months kept, LTV and LTV:CAC, with the total and the Unknown row." + SETUP_NOTE,
    {"month": _s("YYYY-MM; default the latest with numbers")},
)
def growth_economics(api, args):
    return api.get("growth/economics", month=args.get("month"))


@tool(
    "tico_growth_numbers",
    "The growth numbers over the last months: each month's total, each channel's economics, "
    "the KPI tree with its series, and the markers. may_edit: you may record months." + SETUP_NOTE,
    {
        "months": {
            "type": "integer",
            "minimum": 1,
            "maximum": 24,
            "description": "How many months back, ending now; default 12",
        }
    },
)
def growth_numbers(api, args):
    return api.get("growth/numbers", months=args.get("months"))


@tool(
    "tico_growth_kpis",
    "The KPI tree: the North Star, its inputs and guardrails, each KPI's funnel stage and loop." + SETUP_NOTE,
    {},
)
def growth_kpis(api, args):
    return api.get("growth/kpis")


@tool(
    "tico_growth_settings",
    "The growth settings (unit, revenue per customer per month, margin %). With values or set, "
    "owner or admin only: change them. may_edit: you may change them." + SETUP_NOTE,
    {
        "values": {"type": "object", "description": "{unit, revenue_month, margin_pct}"},
        "set": {"type": "array", "items": {"type": "string"}, "description": "KEY=VALUE, instead of values"},
    },
    writes=True,
)
def growth_settings(api, args):
    values = dict(args.get("values") or {})
    for pair in args.get("set") or []:
        k, sep, v = pair.partition("=")
        if not sep:
            raise APIError("set", "--set is KEY=VALUE")
        if k in ("revenue_month", "margin_pct"):
            try:
                v = float(v)
            except ValueError:
                raise APIError("set", f"{k} is a number") from None
        values[k] = v
    if not values:
        return api.get("growth/settings")
    return _as_person(api).call("PUT", "growth/settings", values, _key(args))


@tool(
    "tico_growth_setup",
    "Growth's first run: the four steps from an empty Growth to its first numbers (settings, a channel, its source, "
    "its first month), each done or not with when it was first done, the next step with its CLI command and MCP tool, "
    "whether you may do it and if not who may, and when the team first opened Growth and first saw its numbers.",
    {},
)
def growth_setup(api, args):
    return api.get("growth/setup")


def _growth_text(args, result):
    from clients import remotecli  # tables and indented lines, shared with `tico brand`

    return remotecli.growth_text(args.fn, result)


_RETIRED = Arg("include_retired", "--retired", default=None)
_CHANNEL = (
    Arg("budget_month", "--budget", metavar="N"),
    Arg("source_id", "--source", metavar="ID"),
    Arg("guardrail_kpi_id", "--guardrail-kpi", metavar="ID"),
    Arg("guardrail_max", "--guardrail-max", metavar="N"),
)
_SOURCE = (Arg("truth_for", "--truth-for"), Arg("system", "--system"), Arg("reach_kind", "--reach-kind"))

# `tico growth ...` (clients/cli_spec.py): each leaf's flags come from its tool's schema.
CLI = (
    Group("growth", "growth: channels, sources, monthly cohorts, unit economics, the numbers"),
    Group("growth channel", "the channels customers come from"),
    Command(
        "growth channel list",
        "the channels: stage, owner, budget",
        args=(_RETIRED,),
        render=_growth_text,
        example="tico growth channel list",
    ),
    Command(
        "growth channel show", "one channel", render=_growth_text, example="tico growth channel show google-search"
    ),
    Command(
        "growth channel add",
        "add a channel (owner or admin)",
        args=(Arg("name", "--name"), *_CHANNEL),
        render=_growth_text,
        example="tico growth channel add google-search --name 'Google Search' --track performance --budget 2000",
    ),
    Command(
        "growth channel update",
        "change a channel (owner or admin; the owning bot may change why, stage, budget)",
        args=_CHANNEL,
        render=_growth_text,
        example="tico growth channel update google-search --stage scaling",
    ),
    Command(
        "growth channel retire",
        "retire a channel (owner or admin)",
        render=_growth_text,
        example="tico growth channel retire google-search",
    ),
    Group("growth source", "which system holds the truth for each number"),
    Command(
        "growth source list",
        "the sources, and any number two of them claim",
        args=(_RETIRED,),
        render=_growth_text,
        example="tico growth source list",
    ),
    Command(
        "growth source add",
        "say which system holds a number (owner or admin)",
        args=_SOURCE,
        render=_growth_text,
        example="tico growth source add --truth-for 'ad spend' --system 'Google Ads' --reach-kind mcp",
    ),
    Command(
        "growth source update",
        "change a source (owner or admin)",
        args=_SOURCE,
        render=_growth_text,
        example="tico growth source update src_1 --may 'read only'",
    ),
    Command(
        "growth source retire",
        "retire a source (owner or admin)",
        render=_growth_text,
        example="tico growth source retire src_1",
    ),
    Group("growth cohort", "monthly numbers by channel"),
    Command(
        "growth cohort list",
        "the monthly cohorts by channel",
        args=(Arg("channel", metavar="SLUG"), Arg("month", metavar="YYYY-MM")),
        render=_growth_text,
        example="tico growth cohort list --channel google-search",
    ),
    Command(
        "growth cohort set",
        "one channel's month, only the fields given (any bot or an admin)",
        args=(
            Arg("channel", "--channel", metavar="SLUG"),
            Arg("month", "--month", metavar="YYYY-MM"),
            Arg("new_customers", "--new", metavar="N"),
            Arg("kept_d30", "--d30", metavar="PCT"),
            Arg("kept_d90", "--d90", metavar="PCT"),
            Arg("kept_d180", "--d180", metavar="PCT"),
            Arg("revenue_month", "--revenue", metavar="N"),
            Arg("margin_pct", "--margin", metavar="PCT"),
            Arg("sources", type=json.loads, metavar="JSON"),
            Arg("source", metavar="FIELD=TEXT"),
        ),
        render=_growth_text,
        example="tico growth cohort set --channel google-search --month 2026-10 --spend 1800 --new 6",
    ),
    Command(
        "growth economics",
        "CAC, months kept, LTV and LTV:CAC by channel",
        args=(Arg("month", metavar="YYYY-MM"),),
        render=_growth_text,
        example="tico growth economics --month 2026-10",
    ),
    Command(
        "growth numbers",
        "the monthly series, each channel's economics, the KPI tree, markers",
        args=(Arg("months", metavar="N"),),
        render=lambda args, result: json.dumps(result, indent=2),
        example="tico growth numbers --months 6",
    ),
    Command(
        "growth kpis", "the KPI tree: North Star, inputs, guardrails", render=_growth_text, example="tico growth kpis"
    ),
    Command(
        "growth setup",
        "the four setup steps, each done or not, and the next one",
        render=_growth_text,
        example="tico growth setup",
    ),
    Command(
        "growth settings",
        "unit, revenue, margin; --set or --values changes them (owner or admin)",
        args=(Arg("values", type=json.loads, metavar="JSON"), Arg("set", metavar="KEY=VALUE")),
        render=_growth_text,
        example="tico growth settings --set unit=customer --set margin_pct=70",
    ),
)


# ----------------------------------------------------------------------------- lenses
LENS_WAIT_MAX = 60


@tool(
    "tico_lens_list",
    "The lenses: thought exercises run against the strategy or the market, each with its steps "
    "(the questions and the shape of each answer), what it shows and what would sharpen it.",
    {"applies_to": {"type": "string", "enum": ["strategy", "market"], "description": "Only these lenses"}},
)
def lens_list(api, args):
    return api.get("lenses", applies_to=args.get("applies_to"))


@tool(
    "tico_lens_run",
    "Run a lens: answers by step key (a step left out is skipped and assumed from the strategy), on "
    "the published strategy or, for the owner, the draft. The owner, the Strategy Owner and, for a market lens, the "
    "Librarian may run one. The Strategy Owner's run files its changes as one strategy_draft proposal; nobody's run "
    "publishes. Waits up to `wait` seconds (at most 60) for the result.",
    {
        "lens": _s("Lens id, from `lens list`"),
        "answers": {
            "type": "object",
            "description": "{step key: answer}. text: a string; list: an array of strings "
            "(at most 12); scores: {columns: [\"Us\", ...], rows: [{factor, scores: [0-5 per column]}]}; "
            "choice: one of the step's options",
        },
        "draft": {"type": "boolean", "description": "Owner only: run on the draft instead of the published version"},
        "wait": {"type": "integer", "description": "Seconds to wait for the result; 0 returns the running run"},
    },
    required=("lens",),
    writes=True,
)
def lens_run(api, args):
    run = api.post(
        f"lenses/{quote(args['lens'], safe='')}/runs",
        {"answers": args.get("answers") or {}, "on": "draft" if args.get("draft") else "published"},
        key=_key(args),
    )
    deadline = time.monotonic() + max(0, min(int(args.get("wait") or 0), LENS_WAIT_MAX))
    while run.get("status") == "running" and time.monotonic() < deadline:
        time.sleep(2)
        run = api.get("lenses/runs/" + run["id"])
    return run


@tool(
    "tico_lens_show",
    "One lens run: its answers, state and result (summary, sections, chart, findings, assumptions, "
    "changes, what would sharpen it). A run on the draft is the owner's.",
    {"id": _s("Run id")},
    required=("id",),
)
def lens_show(api, args):
    return api.get("lenses/runs/" + quote(args["id"], safe=""))


@tool(
    "tico_lens_runs",
    "Lens runs, newest first: each with its summary; tico_lens_show has the rest.",
    {
        "lens": _s("Only runs of this lens"),
        "limit": {"type": "integer", "description": "At most this many (default 50)"},
    },
)
def lens_runs(api, args):
    return api.get("lenses/runs", lens=args.get("lens"), limit=args.get("limit"))


def _answers(path):
    """`--answers-file`: a JSON object of {step key: answer}."""
    value = json_file(path)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("is a JSON object of {step key: answer}")
    return value


LENS = (
    Group("lens", "thought exercises run against the strategy or the market"),
    Command(
        "lens list",
        "the lenses, with their steps",
        args=(Arg("applies_to", "--market", action="store_const", const="market"),),
        example="tico lens list --market",
    ),
    Command(
        "lens run",
        "run a lens; prints the run, waiting up to 60 seconds for its summary",
        args=(
            Arg("lens", metavar="id"),
            Arg("answers", "--answers-file", type=_answers, metavar="FILE"),
            Arg("wait", "--no-wait", action="store_const", const=0, default=LENS_WAIT_MAX),
        ),
        example="tico lens run pre-mortem --answers-file answers.json",
    ),
    Command("lens show", "one run: answers, result and changes", example="tico lens show lr_1"),
    Command("lens runs", "runs, newest first", example="tico lens runs --lens pre-mortem --limit 5"),
)


# ----------------------------------------------------------------------------- the `tico strategy` and `tico brand` commands
def _strategy_text(args, result):
    """`strategy show`: one section as text with `--section`, else the strategy as JSON."""
    if not args.section:
        return json.dumps(result, indent=2)
    return "Nothing published yet." if result["text"] is None else result["text"]


def _read_draft(fields):
    """`brand check <file>`: the draft the file holds, or standard input for `-`, read as the command runs."""
    import sys
    from pathlib import Path

    path = fields["text"]
    return {**fields, "text": sys.stdin.read() if path == "-" else Path(path).read_text()}


_WHOLE = Arg("wrong_if", type=json.loads, metavar="JSON")
_BET = (
    Arg("wrong_if_kpi", metavar="ID"),
    Arg("wrong_if_threshold", metavar="N"),
    Arg("wrong_if_thesis", metavar="MARKET_ID"),
    Arg("wrong_if_text", metavar="TEXT"),
    Arg("check_by", metavar="DATE"),
    Arg("cites", "--cite", metavar="MARKET_ID"),
    _WHOLE,
)

STRATEGY = (
    Group("strategy", "the team's strategy: its bets, its sections, the versions"),
    Command(
        "strategy show",
        "the published strategy, its bets and their goals; the owner also gets the draft",
        render=_strategy_text,
        example="tico strategy show --section serve",
    ),
    Command("strategy summary", "the text every run's context carries", example="tico strategy summary"),
    Command(
        "strategy draft",
        "owner only: change the draft; a bot proposes instead (proposal create --kind strategy_draft)",
        args=(Arg("sections", type=json.loads, metavar="JSON"), Arg("section", metavar="KEY=TEXT")),
        example="tico strategy draft --section 'serve=Small offices with one IT person' --revision 4",
    ),
    Command(
        "strategy publish",
        "owner only: the draft becomes the next version",
        example="tico strategy publish --note 'serve: small offices'",
    ),
    Command(
        "strategy versions",
        "the published versions, newest first; with N, that version whole",
        args=(Arg("n", positional=True, nargs="?"),),
        example="tico strategy versions 3",
    ),
    Command(
        "strategy diff",
        "what changed between two published versions",
        args=(Arg("from_n", "--from"), Arg("to_n", "--to")),
        example="tico strategy diff --from 2 --to 3",
    ),
    Group("strategy bet", "owner only: add, change or retire a bet"),
    Command(
        "strategy bet add",
        "a new bet, proposed unless --status says otherwise",
        args=(Arg("statement", "--statement"), *_BET),
        example="tico strategy bet add --statement 'Small offices buy without a demo' --wrong-if-kpi k_1 "
        "--wrong-if-direction below --wrong-if-threshold 5",
    ),
    Command(
        "strategy bet update",
        "change a bet; only the flags given change",
        args=_BET,
        example="tico strategy bet update b_1 --status active --rules-out 'enterprise deals'",
    ),
    Command("strategy bet retire", "retire a bet; its goals keep their link", example="tico strategy bet retire b_1"),
)

BRAND = (
    Group("brand", "the brand: voice, words, names, claims"),
    Command(
        "brand show",
        "the published page; owner and admins also get the draft",
        args=(Arg("version", metavar="N"),),
        render=_growth_text,
        example="tico brand show --section voice",
    ),
    Command(
        "brand summary",
        "the text a growth-facing bot's turn carries",
        render=_growth_text,
        example="tico brand summary",
    ),
    Command(
        "brand check",
        "check a draft against the published brand",
        args=(Arg("text", metavar="file"),),
        convert=_read_draft,
        render=_growth_text,
        example="tico brand check post.md",
    ),
    Command(
        "brand draft",
        "owner or admin: change the draft, one section with --section and --body or several with --sections; a bot "
        "proposes instead (proposal create --kind brand_draft)",
        args=(Arg("sections", type=json.loads, metavar="JSON"),),
        render=_growth_text,
        example="""tico brand draft --section voice --body '"plain, warm, short"' --revision 2""",
    ),
    Command(
        "brand publish",
        "owner or admin: the draft becomes the next version",
        render=_growth_text,
        example="tico brand publish --note 'new claims'",
    ),
)
