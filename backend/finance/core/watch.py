"""Watch checks (spec §11, the Bookkeeper's Watch job): pure functions over posted charges, monthly spend and the runway
metric. No I/O: the caller reads the books through the viewer's slice and passes plain values, money in integer cents.

Each check returns findings: {"kind", "key" (stable across runs), "label" (what a person reads), "stamp" (its facts;
a change in them is news)}. A check that cannot tell (no burn, too little history) finds nothing. Unused subscriptions
also take the facts of each card's bot (spec 11): the caller reads which cards an idle or archived bot holds and passes
them in, so this stays pure."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

RUNWAY_MONTHS = 6          # cash under this many months of average burn is news
UNUSUAL_RATIO = 2          # a month's spend at least this many times the median of the months before it ...
UNUSUAL_MIN = 10_000       # ... and at least this much more than that median ($100)
HISTORY = 3                # months before the month judged; a series is HISTORY + 1 months, oldest first
DUPLICATE_DAYS = 3         # two charges of one amount to one payee this close together may be a duplicate
PRICE_RISE_PERCENT = 10     # a recurring charge that rises by this many percent ...
PRICE_MIN = 1_000          # ... and by at least this much ($10) is a price increase
UNUSED_MONTHS = 3          # an unused subscription: a monthly charge seen this many months running ...
UNUSED_IDLE_DAYS = 30      # ... on a card whose bot is archived or ran no turn in this many days


@dataclass(frozen=True)
class Charge:
    id: str
    payee: str              # the counterparty, lower-cased: the grouping key
    amount: int             # cents, a positive charge
    day: date
    shown: str = ""         # the counterparty as the bank names it: what a label says
    card: str = ""          # the card it was charged to ("" when it was not a card charge)


def _money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def runway(months: float | None, cash: int | None) -> list[dict[str, Any]]:
    """Cash under RUNWAY_MONTHS of average burn. Nothing burning (months None) has no runway to watch."""
    if months is None or cash is None or months >= RUNWAY_MONTHS:
        return []
    return [{"kind": "runway", "key": "runway", "stamp": {"months": max(0, int(months))},
             "label": f"Cash covers {max(0.0, months):.1f} months of average burn (under {RUNWAY_MONTHS})"}]


def unusual_spend(series: Mapping[str, Sequence[int]], month: str) -> list[dict[str, Any]]:
    """series: payee -> spend in each of HISTORY + 1 months, oldest first, the month judged last (0 where none). The
    month is news when it is UNUSUAL_RATIO times the median of the months before it and UNUSUAL_MIN more; a payee with
    no spend before the month is not judged (a new vendor is a bill, not an anomaly)."""
    found: list[dict[str, Any]] = []
    for payee, months in sorted(series.items()):
        if len(months) != HISTORY + 1:
            continue
        last = months[-1]
        typical = median(months[:-1])
        if typical > 0 and last >= UNUSUAL_RATIO * typical and last - typical >= UNUSUAL_MIN:
            found.append({"kind": "unusual_spend", "key": f"unusual_spend:{payee}:{month}", "stamp": {"amount": last},
                          "label": f"{payee}: {_money(last)} this month, usually {_money(round(typical))}"})
    return found


def duplicate_charges(charges: Sequence[Charge]) -> list[dict[str, Any]]:
    """A charge that repeats an earlier one to the same payee, for the same amount, within DUPLICATE_DAYS."""
    groups: dict[tuple[str, int], list[Charge]] = {}
    for ch in charges:
        groups.setdefault((ch.payee, ch.amount), []).append(ch)
    found: list[dict[str, Any]] = []
    for (payee, amount), rows in sorted(groups.items()):
        ordered = sorted(rows, key=lambda r: (r.day, r.id))
        for before, after in zip(ordered, ordered[1:], strict=False):
            gap = (after.day - before.day).days
            if gap <= DUPLICATE_DAYS:
                found.append({"kind": "duplicate_charge", "key": f"duplicate_charge:{after.id}",
                              "stamp": {"amount": amount, "gap": gap},
                              "label": f"{after.shown or payee}: {_money(amount)} charged again {gap} day(s) after"})
    return found


def price_increases(charges: Sequence[Charge]) -> list[dict[str, Any]]:
    """A recurring charge: the two charges before the latest are the same amount to one payee, and the latest rose by
    PRICE_RISE_PERCENT percent and at least PRICE_MIN."""
    by_payee: dict[str, list[Charge]] = {}
    for ch in charges:
        by_payee.setdefault(ch.payee, []).append(ch)
    found: list[dict[str, Any]] = []
    for payee, rows in sorted(by_payee.items()):
        if len(rows) < 3:
            continue
        older, before, last = sorted(rows, key=lambda r: (r.day, r.id))[-3:]
        if (older.amount == before.amount and last.amount - before.amount >= PRICE_MIN
                and last.amount * 100 >= before.amount * (100 + PRICE_RISE_PERCENT)):
            found.append({"kind": "price_increase", "key": f"price_increase:{last.id}",
                          "stamp": {"amount": last.amount},
                          "label": f"{last.shown or payee}: {_money(last.amount)} now, {_money(before.amount)} before"})
    return found


def unused_subscriptions(charges: Sequence[Charge], idle_cards: Mapping[str, str], today: date) -> list[dict[str, Any]]:
    """A monthly charge: one to the same payee on the same card in each of UNUSED_MONTHS calendar months, counted from
    the months before today's (so an open month is not judged). Only a card in idle_cards is judged: card id -> the slug
    of its bot, which is archived or has run no turn in UNUSED_IDLE_DAYS (the caller reads those facts). Amounts may
    differ month to month."""
    months = _months_before(today, UNUSED_MONTHS)
    seen: dict[tuple[str, str], dict[str, Charge]] = {}
    for ch in charges:
        if ch.card in idle_cards:
            seen.setdefault((ch.card, ch.payee), {}).setdefault(ch.day.strftime("%Y-%m"), ch)
    found: list[dict[str, Any]] = []
    for (card, payee), by_month in sorted(seen.items()):
        if all(month in by_month for month in months):
            last = by_month[months[-1]]
            found.append({"kind": "unused_subscription", "key": f"unused_subscription:{card}:{payee}",
                          "stamp": {"months": UNUSED_MONTHS},
                          "label": f"{last.shown or payee}: {_money(last.amount)} a month on the card of "
                                   f"{idle_cards[card]}, a bot that is archived or ran no turns in "
                                   f"{UNUSED_IDLE_DAYS} days"})
    return found


def _months_before(today: date, n: int) -> list[str]:
    """The n calendar months before today's, as YYYY-MM, oldest first."""
    year, month = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        out.append(f"{year:04d}-{month:02d}")
    return list(reversed(out))
