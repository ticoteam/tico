"""Subscriptions: the recurring charges a book pays (step 6 of the Finance plan). A row is one vendor paid from one card
or money account, with its cadence (monthly or annual), its expected amount, its next charge, its seats and its status:

- suggested: detect() found a repeat. Monthly: a charge in each of the three calendar months before this one, amounts
  within MONTHLY_SPREAD percent of each other, each on the same day of the month within DAY_SLACK days. Annual: the two
  latest charges 11 to 13 months apart, on the same day of the month within ANNUAL_SLACK days.
- active, ignored, cancelled: confirmed by a person or the Bookkeeper (subscription_confirm). Ignored rows raise no
  Watch findings and are never suggested again. A suggested or active row with no charge for MISSED_PERIODS cadence
  periods past its next charge becomes cancelled on its own; detect() is the only thing that does that.

detect() runs once a day from the scheduler's Finance upkeep (scheduler.finance_tick, through actions.detect_subscriptions),
over the posted debits Watch reads, as the system (every charge). Nothing here writes to a provider's books: no outbox rows.

Watch (price_findings, unused_findings): with no confirmed rows, Watch is what it was. With them, a charge on an active
subscription is judged against its expected amount (a price increase at PRICE_RISE_PERCENT or more above it), an unused
check runs as it always does, a charge on an ignored or cancelled subscription raises no finding, and every other charge
keeps today's checks. Duplicate charges are unchanged.

Reads: rows (GET /api/v2/finance/subscriptions, the subscriptions report, the subscription search kind), summary (the
subscriptions_monthly metric), brief_lines (the briefing). Visibility is visible.subscriptions_visible: a full-detail reader,
the owners the attribution in force on the latest charge names (and their managers), or anyone who sees that charge's
line. A subscription's attribution is the share in force on its latest charge's expense line (store/attribution.py), not a
store of its own. `monthly` is an annual amount over twelve, rounded half up to the whole minor unit.

Source key: a charge's card id, else its money account id (a feed row's fin_money_accounts id; an entry's GL account id
in a mirror book, where entries are read only for accounts with no feed rows, so the two never meet for one account).

Actions (need finance.book, which the Bookkeeper holds; never gated, audited by the action log): subscription_confirm
{subscription_id, status: active|ignored|cancelled, amount?, cadence?, seats?} and subscription_undo {subscription_id}, which
puts back the fields before the latest confirmation. Money is integer minor units, dates ISO days.
"""

import calendar
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

from . import attribution as ATTR
from . import visible as VIS
from .context import Ctx, is_set_up, load
from .core import attribution as CA
from .core import kinds as K
from .core import validate as V
from .core import watch as W
from .core.errors import CONFLICT, NOT_FOUND, FinanceError
from .core.org import SYSTEM
from .handlers import Plan, Result
from .store import ap as SAP
from .store import attribution as SATT
from .store import ledger as SL
from .store import subscriptions as SS

CADENCES = ("monthly", "annual")
CONFIRM_STATUSES = ("active", "ignored", "cancelled")
MONTHLY_SPREAD = 25  # percent: a monthly charge's three amounts are within this of the highest
DAY_SLACK = 5  # days: a monthly charge falls within this many days of the same day of the month
ANNUAL_SLACK = 10  # days: an annual charge falls within this many days of the same day of the month
ANNUAL_MONTHS = (11, 12, 13)  # months between an annual charge and the one before it
MISSED_PERIODS = 2  # cadence periods past the next charge with no charge: the subscription is cancelled
DETECT_DAYS = 400  # the look reads this much history: two annual charges need more than Watch's 120 days
RENEWAL_DAYS = 30  # an active subscription whose next charge is this near is a renewal
PRIOR_FIELDS = ("status", "cadence", "amount", "seats", "next_charge_on", "confirmed_at", "confirmed_by")
STATUS_ORDER = {"active": 0, "suggested": 1, "cancelled": 2, "ignored": 3}
Row = dict[str, Any]
Key = tuple[str, str]


@dataclass(frozen=True)
class Found:
    """One repeat: the vendor, the cadence, its latest charge and the day of the first charge seen."""

    vendor: str
    cadence: str
    last: W.Charge
    first_on: date


def _key(ch: W.Charge) -> Key:
    """A charge's subscription key: its vendor, and the card (else the money account) it left."""
    return (ch.payee, ch.card or ch.money)


def _plus_months(d: date, months: int) -> date:
    """The same day `months` months on, the day clamped to the last day of that month."""
    index = d.year * 12 + d.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_on(last: date, cadence: str) -> date:
    """The next charge after one on `last`: a month or a year on."""
    return _plus_months(last, 12 if cadence == "annual" else 1)


def deadline(next_charge: date, cadence: str) -> date:
    """The day past which a subscription with no charge is cancelled: MISSED_PERIODS cadence periods after its next charge."""
    return _plus_months(next_charge, MISSED_PERIODS * (12 if cadence == "annual" else 1))


def _monthly(ordered: Sequence[W.Charge], today: date) -> bool:
    """A charge in each of the three calendar months before today's (the open month is not judged), the latest one in each
    month, on about the same day of the month and at amounts within MONTHLY_SPREAD of each other."""
    latest: dict[str, W.Charge] = {}
    for ch in ordered:  # oldest first, so a month keeps its latest charge
        latest[ch.day.strftime("%Y-%m")] = ch
    picks = [latest.get(month) for month in W.months_before(today, 3)]
    if any(pick is None for pick in picks):
        return False
    cs = [pick for pick in picks if pick is not None]
    anchor = cs[-1].day.day
    if any(abs(ch.day.day - anchor) > DAY_SLACK for ch in cs):
        return False
    high, low = max(ch.amount for ch in cs), min(ch.amount for ch in cs)
    return high * 100 <= low * (100 + MONTHLY_SPREAD)


def _annual(ordered: Sequence[W.Charge]) -> bool:
    """The two latest charges 11 to 13 months apart, on about the same day of the month."""
    if len(ordered) < 2:
        return False
    before, last = ordered[-2].day, ordered[-1].day
    apart = (last.year - before.year) * 12 + last.month - before.month
    return apart in ANNUAL_MONTHS and abs(last.day - before.day) <= ANNUAL_SLACK


def find(charges: Sequence[W.Charge], today: date) -> list[Found]:
    """The repeats in the charges, grouped by vendor and card (or money account). Pure: no reads, no writes."""
    groups: dict[Key, list[W.Charge]] = {}
    for ch in charges:
        if ch.payee:
            groups.setdefault(_key(ch), []).append(ch)
    out: list[Found] = []
    for _, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda r: (r.day, r.id))
        if _monthly(ordered, today):
            out.append(Found(ordered[-1].payee, "monthly", ordered[-1], ordered[0].day))
        elif _annual(ordered):
            out.append(Found(ordered[-1].payee, "annual", ordered[-1], ordered[0].day))
    return out


def _latest_by_key(charges: Sequence[W.Charge]) -> dict[Key, W.Charge]:
    latest: dict[Key, W.Charge] = {}
    for ch in sorted(charges, key=lambda r: (r.day, r.id)):
        latest[_key(ch)] = ch
    return latest


def _rows_key(row: Mapping[str, Any]) -> Key:
    return (str(row["vendor_key"]), str(row["source_key"]))


# ------------------------------------------------------------------------------------------------ watch
def watch_rows(c: sqlite3.Connection) -> list[Row]:
    """The book's confirmed subscriptions (active, ignored or cancelled): the rows Watch reads. Suggested rows are not
    confirmed, so their charges keep today's checks."""
    book = SL.book(c)
    if book is None:
        return []
    return [dict(r) for r in SS.book_rows(c, book.id) if r["status"] != "suggested"]


def price_findings(charges: Sequence[W.Charge], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The Watch price increases. With no confirmed rows, today's check over every charge (the same output as before)."""
    if not rows:
        return W.price_increases(charges)
    confirmed = {_rows_key(r) for r in rows}
    active = {_rows_key(r): r for r in rows if r["status"] == "active"}
    generic = [ch for ch in charges if _key(ch) not in confirmed]  # ignored and cancelled charges raise nothing
    latest = _latest_by_key([ch for ch in charges if _key(ch) in active])
    items = [(latest[k], int(active[k]["amount"]), "") for k in sorted(active) if k in latest]
    return W.price_increases(generic) + W.subscription_price_increases(items)


def unused_findings(
    charges: Sequence[W.Charge], rows: Sequence[Mapping[str, Any]], idle: Mapping[str, str], today: date
) -> list[dict[str, Any]]:
    """The Watch unused subscriptions. With no confirmed rows, today's check over every charge; with them, the charges
    that are not on an ignored or cancelled subscription are judged by the same rule."""
    if not rows:
        return W.unused_subscriptions(charges, idle, today)
    confirmed = {_rows_key(r) for r in rows}
    active = {_rows_key(r) for r in rows if r["status"] == "active"}
    kept = [ch for ch in charges if _key(ch) not in confirmed or _key(ch) in active]
    return W.unused_subscriptions(kept, idle, today)


# ------------------------------------------------------------------------------------------------ detection
def _upsert_found(c: sqlite3.Connection, book_id: str, found: Found, today: date, at: str) -> bool:
    """Record one repeat: a new suggestion, or the refreshed fields of a suggestion. True when a row was made."""
    last = found.last
    source = last.card or last.money
    nxt = next_on(last.day, found.cadence)
    fields: dict[str, object] = {
        "cadence": found.cadence,
        "amount": last.amount,
        "card_id": last.card or None,
        "money_account_id": last.money or None,
        "next_charge_on": nxt.isoformat(),
        "last_charge_on": last.day.isoformat(),
        "last_charge_id": last.id,
        "last_amount": last.amount,
        "first_charge_on": found.first_on.isoformat(),
        "updated_at": at,
    }
    row = SS.by_key(c, book_id, found.vendor, source)
    if row is None:
        if deadline(nxt, found.cadence) < today:
            return False  # a repeat that has already lapsed is not news
        SS.insert(
            c,
            book_id,
            {
                **fields,
                "vendor_key": found.vendor,
                "source_key": source,
                "status": "suggested",
                "seats": None,
                "created_at": at,
                "created_by": SYSTEM,
            },
        )
        return True
    if row["status"] == "suggested":
        SS.update(c, str(row["id"]), fields)
    return False


def detect(c: sqlite3.Connection, at: str | None = None) -> dict[str, int]:
    """The daily look (scheduler Finance upkeep): suggest new repeats; move each row's last charge and next charge on from
    the book's latest charge with its key (confirmed rows included: only their dates and last amount move); and cancel a
    suggested or active row that has missed MISSED_PERIODS cadence periods. Returns how many repeats were found, how many
    suggestions were made, and how many rows were cancelled."""
    from . import reads as FR  # reads imports this module

    if not is_set_up(c):
        return {"found": 0, "new": 0, "cancelled": 0}
    ctx = load(c, at)
    book_id = ctx.book.id
    today = date.fromisoformat(ctx.today)
    charges = FR.watch_charges(c, SYSTEM, ctx.at, DETECT_DAYS)
    found = find(charges, today)
    new = sum(1 for f in found if _upsert_found(c, book_id, f, today, ctx.at))
    latest = _latest_by_key(charges)
    cancelled = 0
    for row in SS.book_rows(c, book_id):
        ch = latest.get(_rows_key(row))
        if ch is not None and ch.day.isoformat() > str(row["last_charge_on"] or ""):
            SS.update(
                c,
                str(row["id"]),
                {
                    "last_charge_on": ch.day.isoformat(),
                    "last_charge_id": ch.id,
                    "last_amount": ch.amount,
                    "next_charge_on": next_on(ch.day, str(row["cadence"])).isoformat(),
                    "updated_at": ctx.at,
                },
            )
            row = SS.get(c, str(row["id"]))
        due = str(row["next_charge_on"] or "")
        if row["status"] in ("suggested", "active") and due:
            if today > deadline(date.fromisoformat(due), str(row["cadence"])):
                SS.update(c, str(row["id"]), {"status": "cancelled", "updated_at": ctx.at})
                cancelled += 1
    return {"found": len(found), "new": new, "cancelled": cancelled}


# ------------------------------------------------------------------------------------------------ reads
def monthly_of(amount: int, cadence: str) -> int:
    """What a subscription costs in a month: its amount, or an annual one's twelfth (half up, whole minor units)."""
    return amount if cadence == "monthly" else int(amount / 12 + 0.5)


def _lines_of(c: sqlite3.Connection, charge_id: str) -> list[str]:
    """The expense lines a charge posted: a feed row's entry's, or the charge's own entry's (an entry charge's id)."""
    if not charge_id:
        return []
    feed = c.execute("SELECT entry_id FROM fin_feed_txns WHERE id=?", (charge_id,)).fetchone()
    entry = charge_id if feed is None else str(feed[0] or "")
    if not entry:
        return []
    return [
        str(r[0])
        for r in c.execute(
            "SELECT l.id FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
            "WHERE l.entry_id=? AND a.type='expense' ORDER BY l.line_no",
            (entry,),
        ).fetchall()
    ]


def _attribution(c: sqlite3.Connection, line_ids: Sequence[str]) -> tuple[CA.Split, ...]:
    """The shares in force on the first of a charge's lines that has any (none: no attribution)."""
    for line_id in line_ids:
        splits = SATT.in_force_splits(c, line_id)
        if splits:
            return splits
    return ()


def _line_seen(ctx: Ctx, viewer: str, line_ids: Sequence[str]) -> bool:
    if not line_ids:
        return False
    return any(ATTR.visible(ctx, viewer, line) for _, line in SL.lines(ctx.c, list(line_ids)).values())


def rows(ctx: Ctx, viewer: str, status: str | None = None) -> list[Row]:
    """The subscriptions the viewer sees (optionally one status): active first, then by vendor name."""
    prepared: list[tuple[Row, tuple[CA.Split, ...], bool]] = []
    for r in [dict(x) for x in SS.book_rows(ctx.c, ctx.book.id, status)]:
        lines = _lines_of(ctx.c, str(r["last_charge_id"] or ""))
        prepared.append((r, _attribution(ctx.c, lines), _line_seen(ctx, viewer, lines)))
    shown = VIS.subscriptions_visible(ctx, viewer, [(str(r["id"]), sp, seen) for r, sp, seen in prepared])
    full = ctx.view(viewer).everything
    vendors = sorted({str(r["vendor_key"]) for r, _, _ in prepared})
    contacts = SAP.contacts(ctx.c, vendors)
    named = VIS.named_all_time(ctx, ctx.view(viewer), [v for v in vendors if v in contacts])
    out: list[Row] = []
    for r, splits, seen in prepared:
        if str(r["id"]) not in shown:
            continue
        vendor = str(r["vendor_key"])
        contact = contacts.get(vendor)
        if contact is not None:
            name = str(contact["name"]) if named.get(vendor, False) else ""
        elif seen or full:
            name = SS.vendor_label(ctx.c, ctx.book.id, vendor) or vendor
        else:
            name = ""
        card_id = str(r["card_id"] or "")
        confirmed_by = str(r["confirmed_by"] or "")
        cadence = str(r["cadence"])
        out.append(
            {
                "id": str(r["id"]),
                "vendor": {"id": vendor, "name": name},
                "status": str(r["status"]),
                "cadence": cadence,
                "amount": int(r["amount"]),
                "monthly": monthly_of(int(r["amount"]), cadence),
                "seats": None if r["seats"] is None else int(r["seats"]),
                "next_charge_on": str(r["next_charge_on"]) if r["next_charge_on"] else None,
                "card": {"id": card_id, "name": SS.card_name(ctx.c, card_id)} if card_id else None,
                "attribution": [{"to": sp.teammate or sp.team, "share": round(sp.share_bps / 100, 2)} for sp in splits],
                "source": f"detected · confirmed by {confirmed_by}" if confirmed_by else "detected",
                "as_of": ctx.at,
                "_sort": (STATUS_ORDER.get(str(r["status"]), 9), name.lower(), str(r["id"])),
            }
        )
    out.sort(key=lambda x: cast(tuple[int, str, str], x.pop("_sort")))
    return out


def monthly_total(found: Sequence[Row]) -> int:
    """The monthly cost of the active subscriptions in `found`."""
    return sum(int(x["monthly"]) for x in found if x["status"] == "active")


def read(c: sqlite3.Connection, viewer: str, status: str | None = None, at: str | None = None) -> Row:
    """GET /api/v2/finance/subscriptions: the viewer's subscriptions, with the monthly total of the active ones."""
    ctx = load(c, at)
    found = rows(ctx, viewer, status)
    return {"rows": found, "monthly_total": monthly_total(found), "as_of": ctx.at}


def summary(ctx: Ctx, viewer: str) -> Row:
    """The metric subscriptions_monthly: the active subscriptions' monthly total in the viewer's slice, and their count."""
    active = rows(ctx, viewer, "active")
    return {"amount": monthly_total(active), "count": len(active), "as_of": ctx.at}


def report(ctx: Ctx, viewer: str, top: int) -> Row:
    """The subscriptions report: the first `top` rows, the count, the active monthly total, and the active ones whose next
    charge falls within RENEWAL_DAYS days of today."""
    found = rows(ctx, viewer)
    today = date.fromisoformat(ctx.today)
    horizon = (today + timedelta(days=RENEWAL_DAYS)).isoformat()
    soon = [
        x
        for x in found
        if x["status"] == "active" and x["next_charge_on"] and today.isoformat() <= str(x["next_charge_on"]) <= horizon
    ]
    return {
        "rows": found[:top],
        "count": len(found),
        "monthly_total": monthly_total(found),
        "renewals_30d": soon,
        "as_of": ctx.at,
    }


def search(ctx: Ctx, viewer: str, text: str, most: int) -> list[Row]:
    """The subscriptions the viewer sees whose vendor name matches (hub_finance_search kind subscription)."""
    needle = text.lower()
    out: list[Row] = []
    for x in rows(ctx, viewer):
        vendor = cast(Row, x["vendor"])
        name = str(vendor["name"])
        if needle and needle not in name.lower():
            continue
        out.append({**x, "kind": "subscription", "label": name or str(vendor["id"])})
        if len(out) >= most:
            break
    return out


def _dollars(minor: int) -> str:
    return f"${round(abs(minor) / 100):,}"


def brief_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """The briefing line for a reader (brief.py): the active subscriptions they see, their monthly total, and how that moved
    since the start of last month. The previous total counts the subscriptions running then, at their current amounts,
    plus those cancelled since (by updated_at). Nothing when there are none."""
    ctx = load(c, at)
    active = rows(ctx, reader, "active")
    if not active:
        return []
    total = monthly_total(active)
    today = date.fromisoformat(ctx.today)
    last_month = _plus_months(today.replace(day=1), -1)
    shown = {str(x["id"]) for x in rows(ctx, reader)}
    before = 0
    for r in SS.book_rows(c, ctx.book.id):
        if str(r["id"]) not in shown or r["status"] == "suggested" or not r["first_charge_on"]:
            continue
        if str(r["first_charge_on"]) >= last_month.isoformat():
            continue
        if r["status"] == "active" or (r["status"] == "cancelled" and str(r["updated_at"]) >= last_month.isoformat()):
            before += monthly_of(int(r["amount"]), str(r["cadence"]))
    clause = ""
    if total != before:
        word = "up" if total > before else "down"
        clause = f", {word} {_dollars(total - before)} since {last_month.strftime('%b')}"
    return [f"Subscriptions: {_dollars(total)}/mo across {len(active)}{clause}."]


# ------------------------------------------------------------------------------------------------ actions
def _row(ctx: Ctx, subscription_id: str) -> Any:
    row = SS.get(ctx.c, subscription_id)
    if row is None or str(row["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such subscription")
    return row


def _vendor_name(ctx: Ctx, vendor_key: str) -> str:
    contact = SAP.contacts(ctx.c, [vendor_key]).get(vendor_key)
    if contact is not None:
        return str(contact["name"])
    return SS.vendor_label(ctx.c, ctx.book.id, vendor_key) or vendor_key


def subscription_confirm(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Confirm a subscription: active (it is one), ignored (it is not: no findings, never suggested again) or cancelled.
    The expected amount, cadence and seats may change with it; the next charge follows the cadence."""
    V.only(p, ("subscription_id", "status", "amount", "cadence", "seats"))
    sid = V.text(p, "subscription_id", 80)
    status = V.choice(p, "status", CONFIRM_STATUSES)
    row = _row(ctx, sid)
    amount = V.opt_amount(p, "amount")
    cadence = V.choice(p, "cadence", CADENCES, str(row["cadence"]))
    current_seats = None if row["seats"] is None else int(row["seats"])
    if "seats" not in p:
        seats = current_seats
    else:
        seats = None if p.get("seats") is None else V.integer(p, "seats", 0, 100_000)
    new_amount = int(row["amount"]) if amount is None else amount
    vendor = _vendor_name(ctx, str(row["vendor_key"]))

    def apply(aid: str) -> Result:
        now = SS.get(ctx.c, sid)
        prior = {k: now[k] for k in PRIOR_FIELDS}
        fields: dict[str, object] = {
            "status": status,
            "cadence": cadence,
            "amount": new_amount,
            "seats": seats,
            "confirmed_at": ctx.at,
            "confirmed_by": who,
            "prior": SS.prior_json(prior),
            "updated_at": ctx.at,
        }
        if cadence != str(now["cadence"]) and now["last_charge_on"]:
            fields["next_charge_on"] = next_on(date.fromisoformat(str(now["last_charge_on"])), cadence).isoformat()
        SS.update(ctx.c, sid, fields)
        return {"subscription_id": sid, "status": status}

    return Plan(K.ALLOW, f"Mark {vendor} as {status}", apply)


def subscription_undo(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Put back the fields a subscription had before its latest confirmation."""
    V.only(p, ("subscription_id",))
    sid = V.text(p, "subscription_id", 80)
    row = _row(ctx, sid)
    prior = SS.prior_of(row)
    if prior is None:
        raise FinanceError(CONFLICT, "nothing to undo on that subscription")
    vendor = _vendor_name(ctx, str(row["vendor_key"]))

    def apply(aid: str) -> Result:
        SS.update(ctx.c, sid, {**prior, "prior": None, "updated_at": ctx.at})
        return {"subscription_id": sid, "status": str(prior["status"])}

    return Plan(K.ALLOW, f"Undo the last change to the subscription {vendor}", apply)


PLANNERS: dict[str, Any] = {
    "subscription_confirm": subscription_confirm,
    "subscription_undo": subscription_undo,
}

__all__ = [
    "PLANNERS",
    "brief_lines",
    "deadline",
    "detect",
    "find",
    "monthly_of",
    "monthly_total",
    "next_on",
    "price_findings",
    "read",
    "report",
    "rows",
    "search",
    "summary",
    "unused_findings",
    "watch_rows",
]
