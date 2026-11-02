"""Tico's own Gateway spend on the books (spec §9). The amount of record is the Gateway's meter: the worker keeps each
fetch of `GET /v1/gateway/key/usage` as a snapshot, and a short job posts the month-to-date change in charged Tico
price since the last posting, Dr AI services (category `image`: Video production) / Cr Gateway credit.

Amounts stay in nanodollars until the posted total for the month rounds to cents, so each posting is the rounded
month-to-date total less what is already on the books for that month and line, and a rounding remainder carries
forward by itself. A posting's source id names (month, line, snapshot), so running the job again posts nothing twice.
Each bot gets what the install itself recorded it spending on the Gateway this month (runs on Tico models, image models
on Video production; decision calls on AI services) less what earlier postings already put on it, never more than the
posting; the rest is Unattributed. When the records come to more than the meter moved (the meter lags), the bots share the posting in
proportion. Runs on the company's own keys, sign-ins and subscriptions are not on the meter and post nothing.

A posting also takes in the run holds the meter now covers: settled run holds whose run finished in the snapshot's month
by the snapshot's time, even when the meter did not move. A month stays on the fetch list until a snapshot taken at least FINAL_DAYS after
its end is posted, so a server that was down over the turn of the month still reads it; that final posting takes in
(or, where the meter billed nothing on the line, releases) every settled run hold of the month still left."""
import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import cast
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from . import budgeting
from .context import Ctx, is_set_up, load
from .core import dates
from .core.errors import invalid
from .core.ledger import MAX_LINES
from .core.org import SYSTEM
from .posting import post_entry
from .setup import AI, VIDEO
from .store import budgets as SB
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
BACKLOG_KEY = "gateway_usage_months"    # months that still need a final read of the meter
FINAL_DAYS = 3                          # charges the meter records late: a month's final read is this long after it
MOST_BACKLOG = 12
FINAL_KEY = "gateway_usage_final"       # {month: when its final read came back}, kept until that month is closed
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
    remember(c, month, by, fetched_at)
    if fetched_at >= _final_after(month):
        _final_read(c, month, by, fetched_at)   # an unchanged final read still closes the month
    return SU.add_snapshot(c, month=month, as_of=as_of, ai_nano=totals["ai"], video_nano=totals["video"], rows=kept,
                           by=by, at=fetched_at)


def video_models(snap: Mapping[str, object]) -> list[str]:
    """The models a snapshot's meter puts in category `image`."""
    rows = cast(list[Mapping[str, object]], json.loads(str(snap["rows"] or "[]")))
    return sorted({str(r.get("model") or "") for r in rows if r.get("line") == "video" and r.get("model")})


# ------------------------------------------------------------------------------------------------ attribution
def weights(c: sqlite3.Connection, after: str, through: str, line: str = "ai",
            models: Sequence[str] = ()) -> dict[str, int]:
    """{bot teammate: nanodollars} of Gateway-billed usage the install recorded on `line` in (after, through]: runs (an
    image model's on `video`, the rest on `ai`) and, on `ai`, bots' decision calls."""
    out: dict[str, int] = {}
    for (bot, on), cost in tico.gateway_run_costs(c, after, through, models).items():
        if bot and on == line:
            out["bot:" + bot] = out.get("bot:" + bot, 0) + max(0, nano(cost))
    if line == "ai":
        for actor, cost in tico.gateway_decision_costs(c, after, through).items():
            if actor.startswith("bot:"):
                out[actor] = out.get(actor, 0) + max(0, nano(cost))
    return {k: v for k, v in out.items() if v > 0}


def _largest_remainder(total: int, exact: Sequence[tuple[str, Decimal]]) -> dict[str, int]:
    """Whole cents for each exact share, adding up to `total` (largest remainder)."""
    floors = {k: int(x) for k, x in exact}
    left = total - sum(floors.values())
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i][1] - int(exact[i][1])), i))
    for i in order[:max(0, left)]:
        floors[exact[i][0]] += 1
    return floors


def split(amount: int, recorded: Mapping[str, int], most: int = MAX_BOTS) -> list[tuple[str, int]]:
    """`amount` cents as (teammate, cents), "" for Unattributed. Each bot gets its own `recorded` nanodollars in cents;
    when they come to more than `amount`, the bots share `amount` in proportion. Past `most` bots, the smallest go to
    Unattributed; a negative amount (a correction) goes Unattributed. Parts of 0 are left out."""
    if amount <= 0 or not recorded:
        return [("", amount)] if amount else []
    ranked = sorted(((k, v) for k, v in recorded.items() if k and v > 0), key=lambda kv: (-kv[1], kv[0]))[:most]
    whole = sum(v for _, v in ranked)
    if whole <= 0:
        return [("", amount)]
    if whole <= amount * NANO_PER_CENT:
        own = cents(whole)
        parts = _largest_remainder(own, [(k, Decimal(v) / NANO_PER_CENT) for k, v in ranked])
    else:
        parts = _largest_remainder(amount, [(k, Decimal(amount) * v / whole) for k, v in ranked])
    rest = amount - sum(parts.values())
    out = [(k, v) for k, v in sorted(parts.items()) if v]
    return [*out, ("", rest)] if rest else out


# ------------------------------------------------------------------------------------------------ posting
def _code_ids(ctx: Ctx) -> dict[str, str]:
    return {a.code: a.id for a in ctx.accounts.values()}


def _through(month: str, as_of: str) -> str:
    """Attribution reads the month's records up to the snapshot, cut at the month's end so a late fetch does not take
    next month's runs."""
    return min(as_of, dates.add_months(dates.month(month), 1).isoformat() + "T00:00:00")


def unassigned(c: sqlite3.Connection, book_id: str, account_id: str, month: str, through: str, line: str,
               models: Sequence[str]) -> dict[str, int]:
    """{bot teammate: nanodollars} the install recorded on `line` this month up to `through` that no usage posting has
    put on the bot yet. Progress lives in the books, not in snapshot rows, so a replaced snapshot assigns nothing twice."""
    start = dates.month(month).isoformat() + "T00:00:00"
    recorded = weights(c, start, through, line, models)
    given = SU.posted_by_teammate(c, book_id, account_id, f"gateway:{month}:{line}:")
    left = {k: v - given.get(k, 0) * NANO_PER_CENT for k, v in recorded.items()}
    return {k: v for k, v in left.items() if v > 0}


def _entry_day(ctx: Ctx, month: str, as_of: str) -> str:
    """The snapshot's day, inside its month; today when that day's period is closed."""
    start = dates.month(month)
    day = min(dates.day(as_of), dates.month_end(start))
    day = max(day, start)
    return ctx.today if SL.closed_on(ctx.c, ctx.book.id, day.isoformat()) else day.isoformat()


def post(c: sqlite3.Connection, at: str | None = None, by: str = SYSTEM) -> list[dict[str, object]]:
    """Post each month's newest snapshot that is not on the books yet; one entry per (month, line) that moved, and the
    run holds that snapshot covers taken in. A snapshot older than the month's last posting is set aside, never taken
    as the month's total. What it posted, as {month, line, entry_id, amount}. Safe to run again."""
    if not is_set_up(c):
        return []
    ctx = load(c, at)
    done: list[dict[str, object]] = []
    for _ in range(100):
        snaps = SU.unposted(c)
        if not snaps:
            break
        for snap in snaps:
            done += _post_one(ctx, snap, by)
    _close_final_reads(ctx, by)
    return done


def _final_read(c: sqlite3.Connection, month: str, by: str, at: str) -> None:
    stored = SL.setting(c, FINAL_KEY)
    months: dict[str, object] = dict(cast(dict[str, object], stored)) if isinstance(stored, dict) else {}
    months[month] = at
    SL.set_setting(c, FINAL_KEY, {m: months[m] for m in sorted(months)[-MOST_BACKLOG:]}, by, at)


def _close_final_reads(ctx: Ctx, by: str) -> None:
    """Close each month whose final read came back once none of its snapshots waits to post, even when that read
    matched the last one kept (the meter did not move, so no snapshot was added)."""
    c = ctx.c
    stored = SL.setting(c, FINAL_KEY)
    months = sorted(str(m) for m in cast(dict[str, object], stored)) if isinstance(stored, dict) else []
    if not months:
        return
    waiting = {str(s["month"]) for s in SU.unposted(c)}
    closed = [m for m in months if m not in waiting]
    for month in closed:
        snap = SU.latest(c, month)
        _close_month(ctx, month, video_models(snap) if snap is not None else [])
        _backlog_done(c, month, by, ctx.at)
    if closed:
        left = {m: cast(dict[str, object], stored)[m] for m in months if m not in closed}
        SL.set_setting(c, FINAL_KEY, left, by, ctx.at)


def _post_one(ctx: Ctx, snap: Mapping[str, object], by: str) -> list[dict[str, object]]:
    """Post one snapshot: each line that moved, then the run holds it covers, then the budget crossings once for all of
    it (a hold still on AI services until the video line takes it in must not count beside the AI posting)."""
    c, ids = ctx.c, _code_ids(ctx)
    month, as_of, sid = str(snap["month"]), str(snap["as_of"]), str(snap["id"])
    prior = SU.last_posted(c, month)
    if prior is not None and as_of < str(prior["as_of"]):
        SU.ignore(c, sid, ctx.at)
        return []
    through = _through(month, as_of)
    models = video_models(snap)
    meter = {"ai": int(str(snap["ai_nano"])), "video": int(str(snap["video_nano"]))}
    holds = _holds_by_line(c, month, as_of, models)
    report: dict[str, object] = {"as_of": as_of}
    done: list[dict[str, object]] = []
    touched: list[tuple[str, str]] = []
    for line, code in LINES:
        account = ids.get(code)
        if not account or CREDIT not in ids:
            continue
        prefix = f"gateway:{month}:{line}:"
        delta = cents(meter[line]) - SU.posted_on(c, ctx.book.id, account, prefix)
        if delta:
            lines: list[dict[str, object]] = []
            for mate, part in split(delta, unassigned(c, ctx.book.id, account, month, through, line, models)):
                item: dict[str, object] = {"account_id": account, "amount": part,
                                           "description": f"Tico AI Gateway, {month}"}
                if mate:
                    item["tags"] = {"teammate": mate}
                lines.append(item)
                touched.append((account, mate))
            lines.append({"account_id": ids[CREDIT], "amount": -delta, "description": f"Tico AI Gateway, {month}"})
            entry = {"date": _entry_day(ctx, month, as_of), "memo": f"Tico AI Gateway usage, {month}",
                     "source": "usage", "source_id": prefix + sid, "lines": lines}
            made = post_entry(c, entry, by=by, hold_ids=holds[line], at=ctx.at, touch=False)
            done.append({"month": month, "line": line, "entry_id": made["entry_id"], "amount": delta})
        else:
            _absorb_into_last(ctx, prefix, holds[line], release=False)
        report[line] = {"meter_nano": meter[line], "posted": SU.posted_on(c, ctx.book.id, account, prefix)}
    SU.mark_posted(c, month, as_of, ctx.at)
    fetched = str(snap["created_at"])
    if fetched >= _final_after(month):
        _close_month(ctx, month, models)
        _backlog_done(c, month, by, ctx.at)
    _crossings(ctx, touched, by)
    _reconciled(c, month, report, by, ctx.at)
    return done


def _absorb_into_last(ctx: Ctx, prefix: str, hold_ids: Sequence[str], *, release: bool) -> None:
    """Settled run holds the meter already carries: the month's last posting on the line stands for them. With none
    (the meter never billed that line), `release` frees them: the meter is the record."""
    if not hold_ids:
        return
    last = SU.last_entry(ctx.c, ctx.book.id, prefix)
    for hid in hold_ids:
        if last:
            SB.absorb(ctx.c, hid, last)
        elif release:
            SU.release_settled(ctx.c, hid, ctx.at)


def _close_month(ctx: Ctx, month: str, models: Sequence[str]) -> None:
    """The month's final posting: no settled run hold of the month outlives it."""
    left = _holds_by_line(ctx.c, month, "9999-12-31T23:59:59.999999Z", models)
    for line, _ in LINES:
        _absorb_into_last(ctx, f"gateway:{month}:{line}:", left[line], release=True)


def _crossings(ctx: Ctx, touched: Sequence[tuple[str, str]], by: str) -> None:
    ids: list[str] = []
    for account, mate in dict.fromkeys(touched):
        b = budgeting.leaf(ctx, account, teammate=mate)
        if b is not None:
            ids += budgeting.chain_of(ctx, b.id)
    if ids:
        budgeting.crossings(ctx, ids, by)


def _holds_by_line(c: sqlite3.Connection, month: str, as_of: str, models: Sequence[str]) -> dict[str, list[str]]:
    """The settled run holds whose run finished in `month` by `as_of`, by the line their run cost lands on."""
    held = tico.run_holds_settled(c, month, as_of)
    video = tico.video_jobs(c, [sid.removeprefix("job:") for _, sid in held], models) if held else set[str]()
    out: dict[str, list[str]] = {"ai": [], "video": []}
    for hid, sid in held:
        out["video" if sid.removeprefix("job:") in video else "ai"].append(hid)
    return out


def _reconciled(c: sqlite3.Connection, month: str, report: Mapping[str, object], by: str, at: str) -> None:
    """Keep the last reconcile of the recent months: the meter's charged total against what the books hold."""
    stored = SL.setting(c, RECONCILE_KEY)
    months: dict[str, object] = dict(cast(dict[str, object], stored)) if isinstance(stored, dict) else {}
    months[month] = dict(report)
    keep = sorted(months)[-KEEP_MONTHS:]
    SL.set_setting(c, RECONCILE_KEY, {m: months[m] for m in keep}, by, at)


def _final_after(month: str) -> str:
    """When a read of `month` counts as its final one: FINAL_DAYS after the month ends, as a Tico timestamp."""
    end = datetime.combine(dates.add_months(dates.month(month), 1), datetime.min.time(), tzinfo=timezone.utc)
    return (end + timedelta(days=FINAL_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def backlog(c: sqlite3.Connection) -> list[str]:
    """The months whose final meter read is not posted yet."""
    stored = SL.setting(c, BACKLOG_KEY)
    items = cast(list[object], stored) if isinstance(stored, list) else []
    return sorted({str(m) for m in items if isinstance(m, str)})


def remember(c: sqlite3.Connection, month: str, by: str, at: str) -> None:
    """`month` was read: keep it on the fetch list until its final read is posted."""
    months = backlog(c)
    if month not in months and at < _final_after(month):
        SL.set_setting(c, BACKLOG_KEY, sorted([*months, month])[-MOST_BACKLOG:], by, at)


def _backlog_done(c: sqlite3.Connection, month: str, by: str, at: str) -> None:
    months = backlog(c)
    if month in months:
        SL.set_setting(c, BACKLOG_KEY, [m for m in months if m != month], by, at)


def months_to_fetch(c: sqlite3.Connection, today: date) -> list[str]:
    """This month, last month for its first FINAL_DAYS days, and every month still waiting on its final read."""
    this = today.replace(day=1)
    out = {this.isoformat()[:7], *backlog(c)}
    if today.day <= FINAL_DAYS:
        out.add(dates.add_months(this, -1).isoformat()[:7])
    return sorted(out)


def reconcile(c: sqlite3.Connection) -> dict[str, object]:
    """The last reconcile per recent month: {month: {as_of, ai: {meter_nano, posted}, video: {...}}}."""
    stored = SL.setting(c, RECONCILE_KEY)
    return dict(cast(dict[str, object], stored)) if isinstance(stored, dict) else {}


__all__ = ["snapshot", "post", "split", "weights", "nano", "cents", "reconcile", "months_to_fetch"]
