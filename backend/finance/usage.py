"""Tico's own Gateway spend on the books (spec §9). The amount of record is the Gateway's meter: the worker keeps each
fetch of `GET /v1/gateway/key/usage` as a snapshot, and a short job posts the month-to-date change in charged Tico
price since the last posting, Dr AI services (category `image`: Video production) / Cr Gateway credit.

Amounts stay in nanodollars until the posted total for the month rounds to cents, so each posting is the rounded
month-to-date total less what is already on the books for that month and line, and a rounding remainder carries
forward by itself. A posting's source id names (month, line, snapshot), so running the job again posts nothing twice.
Each posted amount is split across bots by their share of Gateway-billed usage in the install's own records over the
same window (runs on Tico models, and decision calls on the Gateway's model); the rest is Unattributed. Runs on the
company's own keys, sign-ins and subscriptions are not on the meter and post nothing."""
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import cast
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .context import Ctx, is_set_up, load
from .core import dates
from .core.errors import invalid
from .core.ledger import MAX_LINES
from .core.org import SYSTEM
from .posting import post_entry
from .setup import AI, VIDEO
from .store import ledger as SL
from .store import tico
from .store import usage as SU

NANO = 10**9
NANO_PER_CENT = 10**7
CREDIT = "1100"                         # Gateway credit: the prepaid asset usage draws down
LINES = (("ai", AI), ("video", VIDEO))
VIDEO_CATEGORIES = frozenset({"image"})
RECONCILE_KEY = "gateway_usage"         # the last reconcile per month: meter total against what is on the books
KEEP_MONTHS = 3
MAX_BOTS = MAX_LINES - 2                # bot lines in one entry; the rest goes Unattributed


def nano(value: object) -> int:
    """A USD amount from the meter (a Decimal, an int or decimal text; never a float) in nanodollars."""
    if isinstance(value, bool) or isinstance(value, float):
        raise invalid("a meter amount is decimal, not a float")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise invalid(f"{value!r} is not an amount") from None
    if not number.is_finite():
        raise invalid(f"{value!r} is not an amount")
    return int((number * NANO).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def cents(nanos: int) -> int:
    return int((Decimal(nanos) / NANO_PER_CENT).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def stamp(value: object, fallback: str) -> str:
    """An ISO timestamp in Tico's format (UTC, microseconds, Z), so it compares with run and hold times as text."""
    text = str(value or "").strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def line_of(category: object) -> str:
    return "video" if str(category or "") in VIDEO_CATEGORIES else "ai"


# ------------------------------------------------------------------------------------------------ snapshots
def snapshot(c: sqlite3.Connection, payload: Mapping[str, object], *, fetched_at: str,
             by: str = SYSTEM) -> str | None:
    """Keep one fetch of the meter (the worker parses it with decimals). The snapshot id, or None when the meter has
    not moved since the last one kept."""
    month = dates.month(payload.get("month")).isoformat()[:7]
    raw = payload.get("rows")
    items: list[object] = list(cast(list[object], raw)) if isinstance(raw, list) else []
    rows = [cast(Mapping[str, object], r) for r in items if isinstance(r, Mapping)]
    kept: list[dict[str, object]] = []
    totals = {"ai": 0, "video": 0}
    for r in rows:
        if r.get("charged") is not True:
            continue
        price = nano(r.get("price_usd") or 0)
        line = line_of(r.get("category"))
        totals[line] += price
        calls = r.get("calls")
        kept.append({"model": str(r.get("model") or "")[:120], "category": str(r.get("category") or "")[:40],
                     "calls": calls if isinstance(calls, int) and not isinstance(calls, bool) else 0,
                     "price_nano": price, "line": line})
    as_of = stamp(payload.get("as_of"), fetched_at)
    return SU.add_snapshot(c, month=month, as_of=as_of, ai_nano=totals["ai"], video_nano=totals["video"], rows=kept,
                           by=by, at=fetched_at)


# ------------------------------------------------------------------------------------------------ attribution
def weights(c: sqlite3.Connection, after: str, through: str) -> dict[str, int]:
    """{teammate or "" (Unattributed): nanodollars} of Gateway-billed usage the install recorded in (after, through]."""
    out: dict[str, int] = {}
    for bot, cost in tico.gateway_run_costs(c, after, through).items():
        key = "bot:" + bot if bot else ""
        out[key] = out.get(key, 0) + max(0, nano(cost))
    for actor, cost in tico.gateway_decision_costs(c, after, through).items():
        key = actor if actor.startswith("bot:") else ""
        out[key] = out.get(key, 0) + max(0, nano(cost))
    return {k: v for k, v in out.items() if v > 0}


def split(amount: int, shares: Mapping[str, int], most: int = MAX_BOTS) -> list[tuple[str, int]]:
    """`amount` cents split by `shares` (largest remainder, so the parts add up exactly), as (teammate, cents) with
    "" for Unattributed. Past `most` bots, the smallest go to Unattributed. Parts of 0 are left out."""
    if not shares or amount == 0:
        return [("", amount)] if amount else []
    ranked = sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))
    named = [(k, v) for k, v in ranked if k][:most]
    rest = sum(v for k, v in ranked if (k, v) not in named)
    parts = [*named, ("", rest)] if rest else named
    whole = sum(v for _, v in parts)
    sign, total = (1 if amount > 0 else -1), abs(amount)
    exact = [(k, Decimal(total) * v / whole) for k, v in parts]
    floors = [(k, int(x)) for k, x in exact]
    left = total - sum(v for _, v in floors)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i][1] - floors[i][1]), i))
    out = dict(floors)
    for i in order[:left]:
        out[exact[i][0]] = out[exact[i][0]] + 1
    merged: dict[str, int] = {}
    for k, v in out.items():
        merged[k] = merged.get(k, 0) + v
    return [(k, sign * v) for k, v in sorted(merged.items(), key=lambda kv: (kv[0] == "", kv[0])) if v]


# ------------------------------------------------------------------------------------------------ posting
def _code_ids(ctx: Ctx) -> dict[str, str]:
    return {a.code: a.id for a in ctx.accounts.values()}


def _window(month: str, prior: str | None, as_of: str) -> tuple[str, str]:
    """(after, through) for attribution: since the month's last posting (else its start) up to the snapshot, cut at the
    month's end so a late fetch does not take next month's runs."""
    start = dates.month(month)
    following = dates.add_months(start, 1).isoformat() + "T00:00:00"
    return prior or start.isoformat() + "T00:00:00", min(as_of, following)


def _entry_day(ctx: Ctx, month: str, as_of: str) -> str:
    """The snapshot's day, inside its month; today when that day's period is closed."""
    start = dates.month(month)
    day = min(dates.day(as_of), dates.month_end(start))
    day = max(day, start)
    return ctx.today if SL.closed_on(ctx.c, ctx.book.id, day.isoformat()) else day.isoformat()


def post(c: sqlite3.Connection, at: str | None = None, by: str = SYSTEM) -> list[dict[str, object]]:
    """Post every month whose newest snapshot is not on the books yet; one entry per (month, line) that moved. What it
    posted, as {month, line, entry_id, amount}. Safe to run again."""
    if not is_set_up(c):
        return []
    ctx = load(c, at)
    ids = _code_ids(ctx)
    done: list[dict[str, object]] = []
    for snap in SU.unposted(c):
        month, as_of, sid = str(snap["month"]), str(snap["as_of"]), str(snap["id"])
        prior = SU.last_posted(c, month)
        after, through = _window(month, str(prior["as_of"]) if prior else None, as_of)
        meter = {"ai": int(snap["ai_nano"]), "video": int(snap["video_nano"])}
        report: dict[str, object] = {"as_of": as_of}
        for line, code in LINES:
            account = ids.get(code)
            if not account or CREDIT not in ids:
                continue
            prefix = f"gateway:{month}:{line}:"
            target = cents(meter[line])
            delta = target - SU.posted_on(c, ctx.book.id, account, prefix)
            if delta:
                shares = weights(c, after, through) if line == "ai" else {}
                lines: list[dict[str, object]] = []
                for mate, part in split(delta, shares):
                    item: dict[str, object] = {"account_id": account, "amount": part,
                                               "description": f"Tico AI Gateway, {month}"}
                    if mate:
                        item["tags"] = {"teammate": mate}
                    lines.append(item)
                lines.append({"account_id": ids[CREDIT], "amount": -delta, "description": f"Tico AI Gateway, {month}"})
                entry = {"date": _entry_day(ctx, month, as_of), "memo": f"Tico AI Gateway usage, {month}",
                         "source": "usage", "source_id": prefix + sid, "lines": lines}
                holds = SU.run_holds_settled(c, as_of) if line == "ai" else []
                made = post_entry(c, entry, by=by, hold_ids=holds, at=ctx.at)
                done.append({"month": month, "line": line, "entry_id": made["entry_id"], "amount": delta})
            report[line] = {"meter_nano": meter[line], "posted": SU.posted_on(c, ctx.book.id, account, prefix)}
        SU.mark_posted(c, month, as_of, ctx.at)
        _reconciled(c, month, report, by, ctx.at)
    return done


def _reconciled(c: sqlite3.Connection, month: str, report: Mapping[str, object], by: str, at: str) -> None:
    """Keep the last reconcile of the recent months: the meter's charged total against what the books hold."""
    stored = SL.setting(c, RECONCILE_KEY)
    months: dict[str, object] = dict(cast(dict[str, object], stored)) if isinstance(stored, dict) else {}
    months[month] = dict(report)
    keep = sorted(months)[-KEEP_MONTHS:]
    SL.set_setting(c, RECONCILE_KEY, {m: months[m] for m in keep}, by, at)


def months_to_fetch(today: date) -> list[str]:
    """This month, and last month too for the first three days (charges the meter records late)."""
    this = today.replace(day=1)
    out = [this.isoformat()[:7]]
    if today.day <= 3:
        out.insert(0, dates.add_months(this, -1).isoformat()[:7])
    return out


def reconcile(c: sqlite3.Connection) -> dict[str, object]:
    """The last reconcile per recent month: {month: {as_of, ai: {meter_nano, posted}, video: {...}}}."""
    stored = SL.setting(c, RECONCILE_KEY)
    return dict(cast(dict[str, object], stored)) if isinstance(stored, dict) else {}


__all__ = ["snapshot", "post", "split", "weights", "nano", "cents", "reconcile"]
