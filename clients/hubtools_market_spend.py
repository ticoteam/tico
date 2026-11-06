"""Market sizing and spend: the `hub market` size, segment, figure and source tools."""

from clients.tico import APIError
from clients.hubtools_core import (
    _key,
    _s,
    tool,
)


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
    "hub_market_spend",
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
    "hub_market_segment_tree", "The market's segments as an indented tree with each one's size, range and how sure.", {}
)
def market_segment_tree(api, args):
    return {"tree": spend_lines(api.get("market/spend"))}


@tool(
    "hub_market_size_list",
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
    "hub_market_define",
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
    "hub_market_segment_add",
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
    "hub_market_size_add",
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
    "hub_market_size_decide",
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
    "hub_market_figure_add",
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
    "hub_market_sources",
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
    "hub_market_research",
    "Owner: ask the Librarian to define and size the market. Files one task; asking again "
    "while it is open returns the same task.",
    {"description": _s("The market in one sentence")},
    required=("description",),
    writes=True,
)
def market_research(api, args):
    return api.post("market/spend/research", {"description": args["description"]}, key=_key(args))
