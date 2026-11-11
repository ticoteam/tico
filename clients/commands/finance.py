"""Finance.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together. `tico finance` is made
from these tools (clients/cli_spec.py), at the end."""

import argparse
import json
import sys
from urllib.parse import quote

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.tico import APIError


# ----------------------------------------------------------------------------- Finance (docs/finance-tools.md)
# Seven tools over /api/v2/finance (backend/finance_routes.py), each as the caller: its own Finance rights and view,
# read fresh from the books. A result is cut to about 4k tokens, or up to about 16k when the call asks for a bigger
# `limit` or `top_n`; rows that do not fit fold into one "Other · n" row, and search and inbox carry on by cursor.
FINANCE_CHARS = 16000  # about 4k tokens


FIN_OTHER = " other"  # an "Other · n" row's key, as backend/finance/reads.py OTHER: no real key starts


# with a space, so a vendor named Other stays its own row
FINANCE_MAX_CHARS = 64000  # about 16k tokens


FINANCE_REPORTS = [
    "pnl",
    "balance_sheet",
    "cash_flow",
    "budget_vs_actual",
    "ap_aging",
    "ar_aging",
    "spend_by",
    "campaigns",
    "subscriptions",
    "close",
    "sales_tax",
    "1099",
]


TAX_WORKPAPERS = {"sales_tax": "finance/tax/sales-tax", "1099": "finance/tax/1099"}  # spec 11: whole, full view only


FINANCE_METRICS = [
    "cash",
    "burn",
    "runway_months",
    "revenue",
    "spend",
    "budget_remaining",
    "budget_pct_used",
    "ap_due",
    "ar_overdue",
    "uncategorized_count",
    "unreconciled_days",
    "duplicate_charges",
    "price_increases",
    "unattributed",
    "ad_spend",
    "per_conversion",
    "subscriptions_monthly",
]


FINANCE_SEARCH = [
    "entry",
    "feed",
    "bill",
    "invoice",
    "contact",
    "rule",
    "card",
    "account",
    "item",
    "project",
    "vendor",
    "campaign",
    "receipt",
    "subscription",
]


FINANCE_INBOX = [
    "uncategorized",
    "bill_to_enter",
    "unmatched_receipt",
    "missing_receipt",
    "bill_due",
    "invoice_overdue",
    "budget_near_limit",
    "sync_failed",
    "period_ready",
    "unreconciled",
    "bill_email",
]


FIN_DAY = {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$", "description": "YYYY-MM-DD"}


# Whole-number fields an "Other · n" row does not add up: they describe one row, not an amount.
_FIN_NOT_ADDED = {"pct", "days_late", "priority", "depth", "level", "version", "revision", "view_version"}


# Lists that are the report's calendar (one item per period column), not entity rows: every row's amounts line up
# with them, so they are never folded or cut.
_FIN_COLUMNS = {"columns", "periods", "buckets"}


# The last guard's steps: text cut to n characters, nested lists of rows or words cut to m items.
_FIN_SHRINK = ((400, 20), (120, 5), (60, 1))


def _fin_budget(asked, default):
    """The character budget for a call: the default, scaled up with a bigger limit or top_n, never past the maximum."""
    try:
        asked = int(asked or 0)
    except (TypeError, ValueError):
        asked = 0
    return FINANCE_CHARS if asked <= default else min(FINANCE_MAX_CHARS, FINANCE_CHARS * asked // default)


def _fin_size(value):
    return len(json.dumps(value, default=str))


def _fin_fit(result, key, budget):
    """How many of `result[key]`'s rows fit within `budget` beside the rest of the result (at least one: a row too
    big alone has its detail cut by `_fin_guard`)."""
    rows = result.get(key) or []
    room = budget - _fin_size({**result, key: []}) - 120  # 120: the "Other · n" row
    n, used = 0, 0
    for row in rows:
        used += _fin_size(row) + 2
        if used > room:
            break
        n += 1
    return max(1, n) if rows else 0


def _fin_other(rows, **more):
    """One "Other · n" row for `rows`: their count and the sum of each amount (column lists add up item by item)."""
    out = {"key": FIN_OTHER, "label": f"Other · {len(rows)}", "count": len(rows)}
    for row in rows:
        for k, v in row.items() if isinstance(row, dict) else ():
            if k in ("key", "label", "count") or k in _FIN_NOT_ADDED or k.endswith("_pct"):
                continue
            if isinstance(v, int) and not isinstance(v, bool):
                out[k] = out.get(k, 0) + v
            elif isinstance(v, list) and v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
                was = out.get(k) if isinstance(out.get(k), list) else [0] * len(v)
                out[k] = [a + b for a, b in zip(was + [0] * (len(v) - len(was)), v, strict=False)]
    out.update(more)
    return out


def _fin_longest(value, path=()):
    """The path to the longest list of entity rows (dicts) anywhere in `value`, and its length. Period columns are
    not rows: folding them would leave every row's amounts out of line with the calendar."""
    best = (None, 0)
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _FIN_COLUMNS:
                continue
            found = _fin_longest(v, path + (k,))
            if found[1] > best[1]:
                best = found
    elif isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        best = (path, len(value))
        for i, v in enumerate(value):
            found = _fin_longest(v, path + (i,))
            if found[1] > best[1]:
                best = found
    return best


def _fin_fold(result, budget):
    """Shrink a report to `budget`: the longest list of rows keeps what fits and folds the rest into "Other · n",
    until the whole fits or no list can shrink further. Rows already folded are not folded again."""
    for _ in range(20):
        if _fin_size(result) <= budget:
            return result
        path, length = _fin_longest(result)
        if not path or length <= 2:
            return result
        parent = result
        for step in path[:-1]:
            parent = parent[step]
        rows = parent[path[-1]]
        tail = rows[-1] if isinstance(rows[-1], dict) and rows[-1].get("key") == FIN_OTHER else None
        body = rows[:-1] if tail else rows
        over = _fin_size(result) - budget
        keep, cut = len(body), 0
        while keep > 1 and cut < over + 120:
            keep -= 1
            cut += _fin_size(body[keep]) + 2
        rest = body[keep:] + ([tail] if tail else [])
        folded = sum(int(r.get("count", 1)) if r is tail else 1 for r in rest)
        other = _fin_other(
            [r for r in rest if r is not tail]
            + ([{k: v for k, v in tail.items() if k not in ("key", "label", "count")}] if tail else [])
        )
        other.update(label=f"Other · {folded}", count=folded)
        parent[path[-1]] = body[:keep] + [other]
    return result


def _fin_numbers(value):
    return all(x is None or (isinstance(x, (int, float)) and not isinstance(x, bool)) for x in value)


def _fin_shrink(value, text, items):
    """A copy of `value` with strings past `text` characters cut and nested lists past `items` cut: rows fold into
    "Other · n", words into "+n more". Lists of numbers (amounts per column) and period columns stay whole."""
    if isinstance(value, str):
        return value if len(value) <= text else value[:text] + "…"
    if isinstance(value, dict):
        return {
            k: (
                [_fin_shrink(x, text, items) for x in v]
                if k in _FIN_COLUMNS and isinstance(v, list)
                else _fin_shrink(v, text, items)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        if _fin_numbers(value):
            return value
        if len(value) <= items:
            return [_fin_shrink(x, text, items) for x in value]
        kept = [_fin_shrink(x, text, items) for x in value[:items]]
        rest = value[items:]
        if all(isinstance(x, dict) for x in rest):
            count = sum(int(x.get("count") or 1) if x.get("key") == FIN_OTHER else 1 for x in rest)
            other = _fin_other([{k: v for k, v in x.items() if k not in ("key", "label", "count")} for x in rest])
            other.update(label=f"Other · {count}", count=count)
            return kept + [_fin_shrink(other, text, items)]
        return kept + [f"+{len(rest)} more"]
    return value


def _fin_guard(result, budget, key=None):
    """The last check on a Finance result: never over `budget`. Past it, long text and nested detail are cut step by
    step (the rows of `key`, the page, keep their number: only what is inside them is cut, so paging stays exact).
    If even that does not fit, an error says so instead of a result over the cap."""
    size = _fin_size(result)
    if size <= budget or not isinstance(result, dict):
        return result
    for text, items in _FIN_SHRINK:
        out = {
            k: (
                [_fin_shrink(r, text, items) for r in v]
                if k == key and isinstance(v, list)
                else _fin_shrink(v, text, items)
            )
            for k, v in result.items()
        }
        if _fin_size(out) <= budget:
            return out
    marker = {
        "error": "too_big",
        "chars": size,
        "cap": budget,
        "message": "This result is over the size cap even with its detail cut: ask for fewer rows, a narrower "
        "window or a kind",
    }
    if result.get("next_cursor"):
        marker["next_cursor"] = result["next_cursor"]
        marker["message"] += "; next_cursor skips past these rows"
    return marker


def _fin_whole(result, budget):
    """A tax workpaper goes whole or not at all (spec 11): a cut-down one would be partial totals, so it is never shrunk.
    Over `budget` it is not read here; a person exports it from Finance -> Tax."""
    size = _fin_size(result)
    if size <= budget:
        return result
    return {
        "error": "too_big",
        "chars": size,
        "cap": budget,
        "message": "This tax workpaper is too long to read whole here, and a part of it would be a partial total. "
        "A person can export it from Finance -> Tax.",
    }


def _fin_query(args, names):
    return {k: args[k] for k in names if args.get(k) is not None and args.get(k) != ""}


def _fin_paged(api, path, query, key, budget):
    """A paged read cut to `budget`: when the page is too big it is asked again for the rows that fit, so `next_cursor`
    carries on right after them, and one "Other · n" row says how many moved to the next page."""
    result = api.get(path, **query)
    if _fin_size(result) <= budget or not result.get(key):
        return _fin_guard(result, budget, key)
    n = _fin_fit(result, key, budget)
    asked = len(result[key])
    if n >= asked:
        return _fin_guard(result, budget, key)
    smaller = api.get(path, **{**query, "limit": n})
    smaller[key] = list(smaller.get(key) or []) + [
        {"key": FIN_OTHER, "label": f"Other · {asked - n}", "count": asked - n, "next": "page on with next_cursor"}
    ]
    return _fin_guard(smaller, budget, key)


@tool(
    "tico_finance_search",
    "Search what you may see: entries, bank and card rows, bills, invoices, contacts, rules, cards (each with the "
    "vendors it is saved on). account: chart lines by code or name, with ids, for categorize, bill_create and "
    "rule_create. campaign: an ad campaign by name, with owner, spend, clicks and conversions. subscription: a "
    "recurring charge by vendor name, with status, monthly cost and next charge. receipt: by file name or vendor "
    "(status: unread, suggested, unmatched, attached, rejected). Name a kind to page with cursor; otherwise the first "
    "few of each kind, except accounts, vendors, campaigns, subscriptions and receipts (name one).",
    {
        "kind": _s("What to search", enum=FINANCE_SEARCH),
        "q": _s("Words to match"),
        "status": _s("Row, document or card status"),
        "contact_id": _s("Bills/invoices: this vendor or customer"),
        "vendor": _s("Cards: saved on this vendor (contact id or name)"),
        "account_id": _s("Entries/rows: this chart line"),
        "money_account_id": _s("Entries/rows: this bank or card account"),
        "start": FIN_DAY,
        "end": FIN_DAY,
        "aging": _s("Bills/invoices: aging bucket", enum=["current", "1_30", "31_60", "61_90", "over_90"]),
        "contact_kind": _s("Contacts: vendor, customer or both", enum=["vendor", "customer", "both"]),
        "active": {"type": "boolean", "description": "Contacts: active or not"},
        "rule_kind": _s("Rules: gate or posting", enum=["gate", "posting"]),
        "month": _s("Campaigns: YYYY-MM (default this month)"),
        "cursor": _s("next_cursor from the last page (needs kind)"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Rows per page (default 50)"},
    },
)
def finance_search(api, args):
    query = _fin_query(
        args,
        (
            "kind",
            "q",
            "status",
            "contact_id",
            "vendor",
            "account_id",
            "money_account_id",
            "start",
            "end",
            "aging",
            "contact_kind",
            "rule_kind",
            "month",
            "cursor",
            "limit",
        ),
    )
    if args.get("active") is not None:
        query["active"] = "true" if args["active"] else "false"
    budget = _fin_budget(args.get("limit"), 50)
    if query.get("kind"):
        return _fin_paged(api, "finance/search", query, "results", budget)
    result = api.get("finance/search", **query)
    rows = result.get("results") or []
    n = _fin_fit(result, "results", budget) if _fin_size(result) > budget else len(rows)
    if n < len(rows):
        rest = rows[n:]
        kinds = {}
        for row in rest:
            name = str(row.get("kind") or row.get("source") or "row")
            kinds[name] = kinds.get(name, 0) + 1
        result["results"] = rows[:n] + [
            {
                "key": FIN_OTHER,
                "label": f"Other · {len(rest)}",
                "count": len(rest),
                "kinds": kinds,
                "next": "name a kind to page through it",
            }
        ]
    return _fin_guard(result, budget, "results")


@tool(
    "tico_finance_report",
    "One report cut to your view: pnl, balance_sheet, cash_flow, budget_vs_actual, ap_aging, ar_aging, spend_by, "
    "campaigns (owner, spend, clicks, conversions), subscriptions (monthly total, renewals in 30 days) or close (a "
    "month's checklist). Rows past top_n fold into \"Other · n\". sales_tax and 1099 are a year's tax workpapers: "
    "whole, and only for someone who sees everything.",
    {
        "kind": _s("The report", enum=FINANCE_REPORTS),
        "start": FIN_DAY,
        "end": FIN_DAY,
        "period": _s("Column size", enum=["day", "week", "month", "quarter", "year"]),
        "basis": _s("cash or accrual (default: company's)", enum=["cash", "accrual"]),
        "compare": _s("prior adds the window before", enum=["prior"]),
        "top_n": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Rows before Other (default 20)"},
        "by": _s(
            "spend_by: group by",
            enum=[
                "department",
                "team",
                "teammate",
                "bot",
                "person",
                "vendor",
                "account",
                "location",
                "class",
                "project",
                "location_class",
                "location_project",
                "class_project",
            ],
        ),
        "month": _s("close: the month, YYYY-MM"),
        "year": {
            "type": "integer",
            "minimum": 2000,
            "maximum": 2100,
            "description": "sales_tax, 1099: the year (default this year)",
        },
    },
    ("kind",),
)
def finance_report(api, args):
    if args["kind"] == "close":
        if not args.get("month"):
            raise APIError("validation", "close needs month (YYYY-MM)")
        query = _fin_query(args, ("top_n",))
        return _fin_guard(api.get("finance/close/" + quote(str(args["month"]), safe=""), **query), FINANCE_CHARS)
    if args["kind"] in TAX_WORKPAPERS:
        return _fin_whole(api.get(TAX_WORKPAPERS[args["kind"]], **_fin_query(args, ("year",))), FINANCE_CHARS)
    query = _fin_query(args, ("start", "end", "period", "basis", "compare", "top_n", "by"))
    result = api.get("finance/reports/" + quote(str(args["kind"]), safe=""), **query)
    budget = _fin_budget(args.get("top_n"), 20)
    return _fin_guard(_fin_fold(result, budget), budget)


@tool(
    "tico_finance_metric",
    "One number you may see, with when it was worked out: cash, burn, runway_months, revenue, spend, "
    "budget_remaining, budget_pct_used, ap_due, ar_overdue, uncategorized_count, unreconciled_days, unattributed, "
    "ad_spend (your campaigns' spend over start..end; default this month), per_conversion (ad spend over conversions, "
    "null if none), subscriptions_monthly (monthly cost of active subscriptions, with count).",
    {
        "name": _s("The metric", enum=FINANCE_METRICS),
        "budget_id": _s("budget_remaining/budget_pct_used: the budget"),
        "start": FIN_DAY,
        "end": FIN_DAY,
        "account_id": _s("cash: one money account"),
        "team": _s("spend: one team"),
        "teammate": _s("spend: one teammate (human:id or bot:id)"),
        "days": {"type": "integer", "minimum": 0, "maximum": 3650, "description": "ap_due/ar_overdue: within n days"},
    },
    ("name",),
)
def finance_metric(api, args):
    query = _fin_query(args, ("budget_id", "start", "end", "account_id", "team", "teammate", "days"))
    return api.get("finance/metrics/" + quote(str(args["name"]), safe=""), **query)


@tool(
    "tico_finance_brief",
    "Finance changes for you: short lines on the money you may see (budgets, your team's, cash and burn where you see "
    "the company), with as_of (when the numbers last moved) and changed_since.",
    {},
    (),
)
def finance_brief(api, args):
    return api.get("finance/brief")


@tool(
    "tico_finance_act",
    "One Finance action: committed, sent for approval, denied or failed. kind=help lists kinds; kind=help with payload "
    "{'kind': k} gives k's fields. Requires operation_id, the action's key: a retry never acts twice.",
    {
        "kind": _s("The action kind, or help"),
        "payload": {"type": "object", "description": "The kind's fields"},
        "dry_run": {"type": "boolean", "description": "Write nothing; say what would happen"},
    },
    ("kind",),
    writes=True,
)
def finance_act(api, args):
    kind, payload = str(args["kind"]), args.get("payload") or {}
    if kind == "help":
        named = payload.get("kind") if isinstance(payload, dict) else None
        return api.get("finance/action-kinds", **({"kind": named} if named else {}))
    if not args.get("dry_run") and not args.get("operation_id"):
        raise APIError("validation", "Send an operation_id: it is the action's key, so a retry never acts twice")
    return api.post(
        "finance/actions",
        {"kind": kind, "payload": payload, "dry_run": bool(args.get("dry_run"))},
        key=_key(args) or ("dry-run" if args.get("dry_run") else None),
    )


@tool(
    "tico_finance_action",
    "A Finance action's status and result (yours, one you decide, or one your view covers).",
    {"action_id": _s("The action id, from `finance act`")},
    ("action_id",),
)
def finance_action(api, args):
    return _fin_guard(api.get("finance/actions/" + quote(str(args["action_id"]), safe="")), FINANCE_CHARS)


@tool(
    "tico_finance_inbox",
    "Your Finance work queue, with counts per kind: uncategorized rows, bills to enter, receipts, bills due, overdue "
    "invoices, budgets near limit, failed syncs, months ready to close, unreconciled (not reconciled for over a month), "
    "bill_email (emailed bills Finance did not take). Name a kind to page through it alone, past its first 200.",
    {
        "kind": _s("Only this kind, paged with cursor", enum=FINANCE_INBOX),
        "cursor": _s("next_cursor from the last page (same kind)"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Items per page (default 50)"},
    },
)
def finance_inbox(api, args):
    return _fin_paged(
        api, "finance/inbox", _fin_query(args, ("kind", "cursor", "limit")), "items", _fin_budget(args.get("limit"), 50)
    )


@tool(
    "tico_finance_card_fill",
    "Fill your Finance card into your browser's checkout. Not available yet.",
    {
        "card_id": _s("Your card"),
        "merchant": _s("The checkout's domain"),
        "amount": {"type": "integer", "minimum": 1, "description": "Minor units"},
    },
    local=True,
)
def finance_card_fill(api, args):
    raise APIError("not_available", "Card fill is not available yet")


# ----------------------------------------------------------------------------- `tico finance` (clients/cli_spec.py)
def _payload(text):
    """`--payload`: a JSON object, or `-` to read it from stdin."""
    try:
        value = json.loads(sys.stdin.read() if text == "-" else text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--payload is not JSON ({exc})") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--payload must be a JSON object")
    return value


CLI = (
    Group("finance", "the books, as you: search, reports, metrics, actions, your work queue (docs/finance-tools.md)"),
    Command(
        "finance search",
        "entries, bank and card rows, bills, invoices, contacts and rules you may see",
        args=(
            Arg("active", action="store", type=lambda v: v.lower() in ("1", "true", "yes", "on"), metavar="true|false"),
        ),
        example="tico finance search --kind bill --q 'Summit Leasing'",
    ),
    Command(
        "finance report",
        "one report cut to your view; rows past --top-n fold into Other",
        example="tico finance report pnl --start 2026-10-01 --end 2026-10-31",
    ),
    Command(
        "finance metric", "one number you may see, with when it was worked out", example="tico finance metric cash"
    ),
    Command("finance brief", "what changed in the money you may see: a few short lines", example="tico finance brief"),
    Command(
        "finance act",
        "ask for one Finance action (help lists kinds)",
        args=(
            Arg("payload", type=_payload, metavar="JSON|-"),
            Arg("dry_run", default=None),
            Arg("operation_id", "--operation-id"),
        ),
        example="""tico finance act bill_approve --payload '{"bill_id": "b1"}' --operation-id bill-approve:b1""",
    ),
    Command("finance action", "an action's status and result", example="tico finance action fa_1"),
    Command("finance inbox", "your Finance work queue", example="tico finance inbox --kind bill_due"),
)
