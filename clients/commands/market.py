"""The market map and market spend.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import re
from datetime import date
from clients.tico import APIError

from clients.commands._registry import tool
from clients.commands._schemas import _key, _s


# ----------------------------------------------------------------------------- market
@tool(
    "tico_market_show",
    "One market entity: its edges both ways, the evidence behind it, and the last ten events.",
    {"id": _s("Entity id, like organization/globex")},
    required=("id",),
)
def market_show(api, args):
    return api.get("market/entities/" + args["id"])


@tool(
    "tico_market_find",
    "Find market entities and evidence by name, alias, summary, quote or our_read.",
    {"text": _s("What to look for")},
    required=("text",),
)
def market_find(api, args):
    return api.get("market/entities", q=args["text"])


@tool(
    "tico_market_edges",
    "Market edges as rows. Symmetric relations (competes_with, partners_with) are stored once "
    "and returned from either end. as_of keeps edges whose since/until covers that date.",
    {
        "src": _s("From this entity"),
        "dst": _s("To this entity"),
        "rel": _s("One of the sixteen relations"),
        "as_of": _s("YYYY-MM-DD"),
    },
)
def entity_links(api, args):
    return api.get(
        "market/edges", src=args.get("src"), dst=args.get("dst"), rel=args.get("rel"), as_of=args.get("as_of")
    )


@tool(
    "tico_market_delta",
    "What changed in the market graph, grouped by entity.",
    {"since": _s("A date or a window like 7d", default="7d")},
)
def market_delta(api, args):
    return api.get("market/delta", since=args.get("since") or "7d")


@tool(
    "tico_market_ask",
    "Ask a question answered from market entities, evidence and market pages. Citations are ids from the graph.",
    {"question": _s("The question")},
    required=("question",),
)
def market_ask(api, args):
    return api.post("market/ask", {"question": args["question"]})


@tool(
    "tico_market_report",
    "Report a market finding in prose. This does not change an entity or an edge. urgent wakes the Librarian now.",
    {
        "kind": {
            "type": "string",
            "enum": ["new-entity", "edge", "property-change", "correction", "question", "other"],
        },
        "about": _s("The name you used"),
        "claim": _s("What you found, in a sentence"),
        "source_url": _s("Where you read it", default=""),
        "quote": _s("What the source said", default=""),
        "confidence": {"type": "string", "enum": ["high", "medium", "low"], "default": "medium"},
        "urgent": {"type": "boolean", "default": False},
        "source_ref": _s("Where the finding came from, e.g. the intake item id; one insight per ref"),
    },
    required=("kind", "claim"),
    writes=True,
)
def market_report(api, args):
    return api.post(
        "market/insights",
        {
            "kind": args["kind"],
            "about": args.get("about") or "",
            "claim": args["claim"],
            "source_url": args.get("source_url") or "",
            "quote": args.get("quote") or "",
            "confidence": args.get("confidence") or "medium",
            "urgent": bool(args.get("urgent")),
            "source_ref": args.get("source_ref") or None,
        },
        key=_key(args),
    )


@tool(
    "tico_market_resolve",
    "Close a market insight: applied, merged, rejected with one sentence, or needs-human.",
    {
        "id": _s("Insight id"),
        "status": {"type": "string", "enum": ["applied", "merged", "rejected", "needs-human"]},
        "resolution": _s("One sentence"),
        "applied_events": {"type": "array", "items": {"type": "string"}},
    },
    required=("id", "status"),
    writes=True,
)
def market_resolve(api, args):
    return api.post(
        f"market/insights/{args['id']}/resolve",
        {
            "status": args["status"],
            "resolution": args.get("resolution") or "",
            "applied_events": args.get("applied_events") or [],
        },
        key=_key(args),
    )


def market_day(value):
    """last_verified_at takes a real calendar date as YYYY-MM-DD. A timestamp is refused, never cut down to its
    date, and so is a day the calendar does not have (2026-02-31): the server keeps either as written."""
    text = str(value)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            date.fromisoformat(text)
            return text
    except ValueError:
        pass
    raise ValueError("last_verified_at must be a date (YYYY-MM-DD)")


@tool(
    "tico_market_apply",
    "Librarian (the market curator): write the evidence first, then an entity or an edge that cites it, and mark the insight applied.",
    {
        "id": _s("Insight id"),
        "source_url": _s("Source URL", default=""),
        "source_kind": _s("reddit, x, news, site, other", default="other"),
        "quote": _s("What the source said", default=""),
        "our_read": _s("The curator's sentence", default=""),
        "entity_type": _s("organization, person, segment, channel, ..."),
        "entity_name": _s("Name, when creating an entity"),
        "entity_id": _s("Existing entity to update"),
        "summary": _s("New summary"),
        "tier": _s("core, lookalike, phrase-stealer or secondary, for a new company", default=""),
        "new_id": _s("Id for a new entity when type/name-slug is wrong, e.g. company/self", default=""),
        "last_verified_at": _s(
            "Date the entity was last checked, YYYY-MM-DD: set on entity_id, or on a new entity", default=""
        ),
        "edge_src": _s("Edge source entity"),
        "edge_rel": _s("One of the sixteen relations"),
        "edge_dst": _s("Edge destination entity"),
    },
    required=("id",),
    writes=True,
)
def market_apply(api, args):
    verified = market_day(args["last_verified_at"]) if args.get("last_verified_at") else None
    body = {
        "evidence": {
            "source_url": args.get("source_url") or "",
            "source_kind": args.get("source_kind") or "other",
            "quote": args.get("quote") or "",
            "our_read": args.get("our_read") or "",
        }
    }
    if args.get("entity_type") and args.get("entity_name"):
        body["entity"] = {
            "type": args["entity_type"],
            "name": args["entity_name"],
            "summary": args.get("summary") or "",
        }
        if args.get("tier"):
            body["entity"]["tier"] = args["tier"]
        if args.get("new_id"):
            body["entity"]["id"] = args["new_id"]
        if verified:
            body["entity"]["last_verified_at"] = verified
    if args.get("entity_id"):
        body["entity_id"] = args["entity_id"]
        if args.get("summary") is not None:
            body["summary"] = args["summary"]
        if verified:
            body["last_verified_at"] = verified
    if verified and "entity" not in body and "entity_id" not in body:
        raise ValueError("last_verified_at needs entity_id, or entity_type and entity_name for a new entity")
    if args.get("edge_src") and args.get("edge_rel") and args.get("edge_dst"):
        body["edge"] = {"src": args["edge_src"], "rel": args["edge_rel"], "dst": args["edge_dst"]}
    return api.post(f"market/insights/{args['id']}/apply", body, key=_key(args))


@tool(
    "tico_market_import",
    "Librarian (the market curator): apply a verified batch. `lines` is JSONL, one entity "
    "{kind: entity, id, type, name, aliases, external_ids, tier, summary, properties, evidence: [...]} or edge "
    "{kind: edge, src, rel, dst, since, confidence, properties, evidence: [...]} per line. Matches by id, name, alias "
    "or domain update; duplicates are skipped; each line is reported. dry_run keeps nothing.",
    {
        "lines": _s("The JSONL text"),
        "name": _s("The file name", default=""),
        "note": _s("One sentence on where the batch came from", default=""),
        "dry_run": {"type": "boolean", "default": False},
    },
    required=("lines",),
    writes=True,
)
def market_import(api, args):
    return api.post(
        "market/import",
        {
            "lines": args["lines"],
            "name": args.get("name") or "",
            "note": args.get("note") or "",
            "dry_run": bool(args.get("dry_run")),
        },
        key=_key(args),
    )


@tool(
    "tico_market_sweep",
    "Librarian (the market curator): one task on the team owner for every needs-human insight in this run, and a "
    "Listening task only for an entity you mark unverified that is past its verification window.",
    {
        "today": _s("YYYY-MM-DD; default today"),
        "unverified": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Objects {id, look_for} the curator could not verify",
        },
    },
    writes=True,
)
def market_sweep(api, args):
    return api.post(
        "market/curator/sweep", {"today": args.get("today"), "unverified": args.get("unverified") or []}, key=_key(args)
    )


@tool(
    "tico_market_refresh",
    "Rewrite the weekly market delta from market events. A Monday updates the page; "
    "any other day leaves it as it is. The curator's sweep does this on Mondays.",
    {"today": _s("YYYY-MM-DD; default today")},
    writes=True,
)
def market_refresh(api, args):
    return api.post("market/delta/refresh", {"today": args.get("today")}, key=_key(args))


@tool(
    "tico_market_page",
    "Librarian (the market curator): rewrite one market page from the graph, whole, in Markdown. `name` is "
    "overview, structure-and-size, coverage-universe, people-who-matter, channels, regulation-and-catalysts, "
    "theses, open-questions or customer-themes; the weekly delta is tico_market_refresh's.",
    {"name": _s("The page, e.g. overview"), "body": _s("The whole page in Markdown")},
    required=("name", "body"),
    writes=True,
)
def market_page(api, args):
    return api.post("market/pages/" + args["name"], {"body": args["body"]}, key=_key(args))


@tool(
    "tico_market_watch_add",
    "Librarian (the market curator): watch one public page of a market entity (its pricing, changelog, "
    "careers, docs or home page). The server reads it once a day and files a market insight only when it changed. "
    "kind release watches a product's releases instead: the url is github:owner/repo or a release feed, and it needs the "
    "Tico AI Gateway.",
    {
        "entity_id": _s("The market entity, like organization/globex"),
        "url": _s("The page's public address, or github:owner/repo for a release"),
        "kind": {
            "type": "string",
            "enum": ["pricing", "careers", "changelog", "docs", "home", "other", "release"],
            "default": "other",
        },
    },
    required=("entity_id", "url"),
    writes=True,
)
def market_watch_add(api, args):
    return api.post(
        "market/watches",
        {"entity_id": args["entity_id"], "url": args["url"], "kind": args.get("kind") or "other"},
        key=_key(args),
    )


@tool(
    "tico_market_watch_list",
    "The watched market pages: entity, address, kind, when last read and changed, and any error.",
    {"entity_id": _s("Only this entity's pages")},
)
def market_watch_list(api, args):
    return api.get("market/watches", entity_id=args.get("entity_id"))


@tool(
    "tico_market_watch_stop",
    "Librarian (the market curator): stop watching a page. The row is kept.",
    {"id": _s("The watch id from tico_market_watch_list")},
    required=("id",),
    writes=True,
)
def market_watch_stop(api, args):
    return api.post(f"market/watches/{args['id']}/stop", {}, key=_key(args))


@tool(
    "tico_market_watch_check",
    "Librarian (the market curator): read the watched pages now, the longest unchecked first (at most 50).",
    {},
    writes=True,
)
def market_watch_check(api, args):
    return api.post("market/watches/check", {}, key=_key(args))


@tool(
    "tico_market_briefing_post",
    "Librarian (the market curator): post the week's market briefing. `body` is Markdown, `items` the "
    "week's findings, each {text, kind: change|signal|theme|size, entity_ids: [market ids it touches]}. The server works out "
    "which bets cite those ids and tells the Strategy Owner. `answers` are one-line answers to the Market page's questions, "
    "keyed changed, money, players, reach, saying (140 characters each); left out, the week's stay. The same week again "
    "replaces it.",
    {
        "week": _s("ISO week, like 2026-W44"),
        "title": _s("A short title"),
        "body": _s("The briefing in Markdown"),
        "items": {"type": "array", "items": {"type": "object"}, "description": "{text, kind, entity_ids} per finding"},
        "answers": {"type": "object", "description": "{changed, money, players, reach, saying}: one sentence each"},
    },
    required=("week", "title", "body"),
    writes=True,
)
def market_briefing_post(api, args):
    body = {"week": args["week"], "title": args["title"], "body": args["body"], "items": args.get("items") or []}
    if args.get("answers") is not None:
        body["answers"] = args["answers"]
    return api.post("market/briefings", body, key=_key(args))


@tool(
    "tico_market_briefing_latest",
    "The newest weekly market briefing with its body, its items and the bets it touches "
    "(`briefing` is null before the first); `source` names the automation run that posted it, if one did.",
    {},
)
def market_briefing_latest(api, args):
    return api.get("market/briefings/latest")


# ----------------------------------------------------------------------------- market spend
def _params(raw):
    """Parameters as the server takes them: {name: number} or {name: {value, source?, judgement?}}. A list of
    `name=value` strings (the CLI's --param) becomes the object."""
    if raw is None or isinstance(raw, dict):
        return raw
    out = {}
    for item in raw:
        name, sep, value = str(item).partition("=")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            raise APIError("validation", f"'{item}': use name=number", 422) from None
        if not sep:
            raise APIError("validation", f"'{item}': use name=number", 422)
    return out


def _inline_evidence(args):
    """Evidence written with the row: `evidence` objects, or the flat evidence_url / quote of the CLI."""
    out = list(args.get("evidence") or [])
    if args.get("evidence_url") or args.get("quote"):
        out.append(
            {
                "source_url": args.get("evidence_url") or "",
                "source_kind": args.get("source_kind") or "other",
                "quote": args.get("quote") or "",
                "our_read": args.get("our_read") or "",
            }
        )
    return out


def spend_lines(result):
    """The spend tree as text, one segment a line: indented by depth, size with range, and how sure."""

    def usd(n):
        for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(n) >= limit:
                return f"${n / limit:.2f}{suffix}"
        return f"${n:.0f}"

    total = result.get("total") or {}
    lines = [
        f"Total {result.get('year')}: {usd(total.get('spend_usd', 0))} "
        f"({usd(total.get('low_usd', 0))} to {usd(total.get('high_usd', 0))})"
    ]
    for seg in result.get("segments") or []:
        size = seg.get("size")
        what = (
            "not sized"
            if not size
            else (
                f"{usd(size['spend_usd'])} ({usd(size['low_usd'])} to {usd(size['high_usd'])}), "
                f"{size['method']}, {size['confidence']}, " + ("accepted" if size.get("accepted") else "proposed")
            )
        )
        lines.append(
            "  " * seg["depth"] + f"{seg['name']} [{seg['id']}]: {what}" + ("  DISAGREE" if seg.get("disagree") else "")
        )
    return lines


@tool(
    "tico_market_spend",
    "The market by spend: its definition, the segment tree, each segment's size with its range, every "
    "estimate with its arithmetic and evidence, and the players with dated figures. `tree` is the same as text.",
    {
        "dimension": {"type": "string", "enum": ["offering", "buyer"], "default": "offering"},
        "basis": {"type": "string", "enum": ["product", "platform"], "default": "product"},
        "year": {"type": "integer", "description": "Default: the definition's year"},
    },
)
def market_spend(api, args):
    out = api.get(
        "market/spend",
        dimension=args.get("dimension") or None,
        basis=args.get("basis") or None,
        year=args.get("year") or None,
    )
    return {**out, "tree": spend_lines(out)}


@tool(
    "tico_market_segment_tree",
    "The market's segments as an indented tree with each one's size, range and how sure.",
    {},
)
def market_segment_tree(api, args):
    return {"tree": spend_lines(api.get("market/spend"))}


@tool(
    "tico_market_size_list",
    "Every estimate of the market's segments (not rejected ones), each with its segment, method, "
    "arithmetic, parameters and evidence; one segment's with `segment`.",
    {"segment": _s("A segment id; default all")},
)
def market_size_list(api, args):
    spend = api.get("market/spend")
    return {
        "sizes": [
            dict(e, segment_id=seg["id"], segment=seg["name"])
            for seg in spend["segments"]
            for e in seg["estimates"]
            if not args.get("segment") or seg["id"] == args["segment"]
        ]
    }


@tool(
    "tico_market_define",
    "Librarian or owner: define the market. Only what you send changes; a list replaces the list; "
    "`params` merges by name (null removes one). `root_name` makes the root segment the first time. Shared `params` "
    "are numbers a formula reads as $name; changing one moves every size that uses it.",
    {
        "description": _s("What the market is, one or two sentences"),
        "root_name": _s("Name of the root segment"),
        "includes": {"type": "array", "items": {"type": "string"}, "description": "What is in"},
        "excludes": {"type": "array", "items": {"type": "string"}, "description": "What is out"},
        "counting_rules": {
            "type": "array",
            "items": {"type": "string"},
            "description": "How to count: one vendor per segment, what is excluded and why",
        },
        "year": {"type": "integer"},
        "geography": _s("e.g. Global"),
        "params": {
            "type": "object",
            "description": "{name: number} or {name: {value, source?, judgement?}}; "
            "give a source, or judgement true when it is a guess",
        },
    },
    writes=True,
)
def market_define(api, args):
    body = {
        k: args[k]
        for k in ("description", "root_name", "includes", "excludes", "counting_rules", "year", "geography")
        if args.get(k) is not None
    }
    if args.get("params") is not None:
        body["params"] = _params(args["params"])
    return api.call("PUT", "market/definition", body, key=_key(args))


@tool(
    "tico_market_segment_add",
    "Librarian or owner: add a segment to the market (under the root, or under `parent_id`). "
    "The tree is at most four levels deep.",
    {
        "name": _s("The segment's name"),
        "parent_id": _s("A segment id; default the root"),
        "summary": _s("One sentence"),
    },
    required=("name",),
    writes=True,
)
def market_segment_add(api, args):
    return api.post(
        "market/segments",
        {"name": args["name"], "parent_id": args.get("parent_id") or None, "summary": args.get("summary") or ""},
        key=_key(args),
    )


@tool(
    "tico_market_size_add",
    "Librarian or owner: file one estimate of a segment's yearly spend in US dollars. Every method "
    "except owner cites evidence (existing evidence_ids, or inline evidence). `formula` is arithmetic over numbers, "
    "+ - * / ( ), your own `params` names and shared $names; the server works spend out from it. Analyst headline "
    "figures go in `spread` as text, not here. It is filed proposed; accept only when the task says the owner allowed it.",
    {
        "segment_id": _s("The segment"),
        "method": {"type": "string", "enum": ["top_down", "bottom_up", "player_sum", "owner"]},
        "spend_usd": {"type": "number", "description": "Yearly spend; omit when a formula gives it"},
        "low_usd": {"type": "number"},
        "high_usd": {"type": "number"},
        "formula": _s("e.g. companies * adoption * price * $smb_share"),
        "params": {
            "type": "object",
            "description": "{name: number} or {name: {value, source?, judgement?}} for the formula",
        },
        "arithmetic": _s("The working, in words"),
        "growth_pct": {"type": "number"},
        "year": {"type": "integer"},
        "dimension": {"type": "string", "enum": ["offering", "buyer"]},
        "basis": {"type": "string", "enum": ["product", "platform"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "overlaps": _s("What this overlaps with"),
        "spread": _s("Analyst headline figures, as text"),
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Inline evidence: {source_url, source_kind, quote, our_read}",
        },
        "evidence_url": _s("One source URL, with quote"),
        "quote": _s("What the source said"),
        "source_kind": _s("news, site, other, ..."),
        "our_read": _s("Your sentence"),
        "accept": {"type": "boolean", "default": False},
    },
    required=("segment_id", "method", "low_usd", "high_usd"),
    writes=True,
)
def market_size_add(api, args):
    body = {
        k: args[k]
        for k in (
            "segment_id",
            "method",
            "spend_usd",
            "low_usd",
            "high_usd",
            "formula",
            "arithmetic",
            "growth_pct",
            "year",
            "dimension",
            "basis",
            "confidence",
            "overlaps",
            "spread",
            "evidence_ids",
            "accept",
        )
        if args.get(k) is not None
    }
    if args.get("params") is not None:
        body["params"] = _params(args["params"])
    body["evidence"] = _inline_evidence(args)
    return api.post("market/sizes", body, key=_key(args))["size"]


@tool(
    "tico_market_size_decide",
    "Accept or reject an estimate, or (owner only) change one of its parameters: that files a "
    "new accepted owner estimate with the formula worked out again. Accepting sends the previous accepted estimate for "
    "the same segment, dimension, basis and year back to proposed.",
    {
        "id": _s("The size id"),
        "decision": {"type": "string", "enum": ["accept", "reject", "params"]},
        "params": {"type": "object", "description": "For params: {name: number} of the estimate's own parameters"},
    },
    required=("id", "decision"),
    writes=True,
)
def market_size_decide(api, args):
    if args["decision"] == "params":
        return api.post(
            f"market/sizes/{args['id']}/params", {"params": _params(args.get("params")) or {}}, key=_key(args)
        )["size"]
    return api.post(f"market/sizes/{args['id']}/{args['decision']}", {}, key=_key(args))["size"]


@tool(
    "tico_market_figure_add",
    "Librarian or owner: record a player's figure in a segment: revenue, arr, customers or "
    "seats, with `kind` (reported, estimated or claimed) and `as_of` (YYYY-MM or YYYY-MM-DD). Needs evidence.",
    {
        "entity_id": _s("A company or product id"),
        "segment_id": _s("The segment"),
        "metric": {"type": "string", "enum": ["revenue", "arr", "customers", "seats"]},
        "value": {"type": "number"},
        "text": _s("When there is no number, what it says"),
        "kind": {"type": "string", "enum": ["reported", "estimated", "claimed"]},
        "as_of": _s("YYYY-MM or YYYY-MM-DD"),
        "evidence_id": _s("An existing evidence id"),
        "evidence_url": _s("Or a source URL, with quote"),
        "quote": _s("What the source said"),
        "source_kind": _s("news, site, other, ..."),
        "our_read": _s("Your sentence"),
    },
    required=("entity_id", "segment_id", "metric", "kind", "as_of"),
    writes=True,
)
def market_figure_add(api, args):
    body = {
        k: args[k]
        for k in ("entity_id", "segment_id", "metric", "value", "text", "kind", "as_of", "evidence_id")
        if args.get(k) is not None
    }
    body["evidence"] = _inline_evidence(args)
    return api.post("market/figures", body, key=_key(args))["figure"]


@tool(
    "tico_market_sources",
    "Where to look when sizing the market: the shipped catalog of sources (what each covers, "
    "whether it is free or needs the team's key, how far to trust it) with the team's own. `add` is a list of sources "
    "to add: {name, kind, covers, access, reliability, how?, url?, id?}.",
    {"add": {"type": "array", "items": {"type": "object"}, "description": "Sources to add; omit to list"}},
    writes=True,
)
def market_sources(api, args):
    if not args.get("add"):
        return api.get("market/sources")
    return {
        "sources": [
            api.post("market/sources", item, key=_key(args, f":{n}"))["source"] for n, item in enumerate(args["add"])
        ]
    }


@tool(
    "tico_market_research",
    "Owner: ask the Librarian to define and size the market. Files one task; asking again "
    "while it is open returns the same task.",
    {"description": _s("The market in one sentence")},
    required=("description",),
    writes=True,
)
def market_research(api, args):
    return api.post("market/spend/research", {"description": args["description"]}, key=_key(args))
