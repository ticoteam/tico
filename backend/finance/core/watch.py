"""Watch checks (spec §11, the Bookkeeper's Watch job): pure functions over posted charges, monthly spend and the runway
metric. No I/O: the caller reads the books through the viewer's slice and passes plain values, money in integer cents.

Each check returns findings: {"kind", "key" (stable across runs), "label" (what a person reads), "stamp" (its facts;
a change in them is news)}. A check that cannot tell (no burn, too little history) finds nothing. Unused subscriptions
are not here: posted data holds no usage signal to judge one by."""
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


@dataclass(frozen=True)
class Charge:
    id: str
    payee: str              # the counterparty, lower-cased: the grouping key
    amount: int             # cents, a positive charge
    day: date
    shown: str = ""         # the counterparty as the bank names it: what a label says


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
