# pyright: strict
"""CI build time inside Finance: the CI budget and its day share, the hold a builder's permit places on that budget, and
the posting of one priced workflow run to account 6150 against Accrued expenses (2100). backend/ci_runs.py decides the
permits, runs the sweep and serves the routes; it reaches Finance's tables only through this module."""

import sqlite3

from . import budgeting
from .context import is_set_up, load
from .core import budgets as B
from .core import dates
from .core.ledger import Book
from .core.org import SYSTEM
from .posting import post_entry
from .store import budgets as SB
from .store import ledger as SL

# The CHART rows' subtypes (setup.py), found by subtype: an older book may have another account on the seeded code.
CI_SUBTYPE, ACCRUED_SUBTYPE = "ci", "accrued"
HOLD_SOURCE = "ci"  # a permit's hold is source "ci" (budgeting.HOLD_SOURCES)


def current_book(c: sqlite3.Connection) -> Book | None:
    """The book once Finance is set up, else None."""
    return SL.book(c) if is_set_up(c) else None


def ci_budget(c: sqlite3.Connection, book: Book) -> B.Budget | None:
    """The company's CI budget: the root budget on the CI account, not a catch-all. None when there is none."""
    account = next((a.id for a in SL.accounts(c, book.id).values() if a.subtype == CI_SUBTYPE), None)
    if account is None:
        return None
    return next(
        (b for b in SB.budgets(c, book.id).values() if b.account_id == account and not b.parent_id and not b.catch_all),
        None,
    )


def day_share(c: sqlite3.Connection, at: str) -> tuple[B.Budget | None, int | None]:
    """(budget, today's share in cents or None). None when Finance is not set up, the CI budget is missing or off, or no
    amount is set: then no daily gate applies."""
    book = current_book(c)
    found = ci_budget(c, book) if book is not None else None
    if found is None or not found.active or book is None:
        return found, None
    versions = SB.versions(c, [found.id]).get(found.id, [])
    day = dates.day(at[:10])
    return found, B.in_window(found, versions, (day, day), book.fiscal_year_start)


def day_spent(c: sqlite3.Connection, book: Book, found: B.Budget, at: str) -> int:
    """Today's CI spend in cents: posted to the account today, and holds on it that have not posted yet."""
    day = at[:10]
    return SL.rollup_sum(c, book.id, day, day, [found.account_id]) + SB.held(c, [found.id], day, day)


def reserve(
    c: sqlite3.Connection, found: B.Budget, book: Book, remote_id: str, cents: int, at: str, expires_at: str
) -> tuple[bool, str]:
    """Hold a permitted run's estimate on the CI budget. Returns (allowed, hold id or "")."""
    decision = budgeting.hold_place(
        c,
        budget_scope={"account_id": found.account_id},
        amount=cents,
        currency=book.base_currency,
        source=HOLD_SOURCE,
        source_id="run:" + remote_id,
        expires_at=expires_at,
        at=at,
        past_reserves=True,
    )
    return decision.allowed, decision.hold_id or ""


def release_unused(c: sqlite3.Connection, hold_id: str, at: str) -> None:
    """A run that cost nothing frees its estimate now."""
    hold = SB.hold(c, hold_id)
    if hold is not None and hold["status"] == "held" and SL.book(c) is not None:
        budgeting.release(load(c, at), hold["id"])


def post_run(
    c: sqlite3.Connection, book: Book, at: str, *, github_run_id: str, kind: str, cost_cents: int, hold_id: str
) -> str:
    """Post one run's cost to CI (6150) against Accrued expenses (2100), absorbing its permit's hold. Returns the entry id,
    or "" when there is nothing to post (no cost, or the chart lacks either account)."""
    accounts = {a.subtype: a.id for a in SL.accounts(c, book.id).values() if a.subtype in (CI_SUBTYPE, ACCRUED_SUBTYPE)}
    if CI_SUBTYPE not in accounts or ACCRUED_SUBTYPE not in accounts or not cost_cents:
        return ""
    ctx = load(c, at)
    hold_ids: list[str] = []
    if hold_id:
        hold = SB.hold(c, hold_id)
        if hold is not None and hold["status"] == "held":
            budgeting.settle(ctx, hold["id"], cost_cents, None, SYSTEM)
        hold = SB.hold(c, hold_id)
        if hold is not None and hold["status"] == "settled" and hold["entry_id"] is None:
            hold_ids.append(hold["id"])
    label = f"CI run {github_run_id}"
    entry = {
        "date": at[:10],
        "memo": f"CI build time, {kind}",
        "source": "usage",
        "source_id": "ci:" + github_run_id,
        "lines": [
            {"account_id": accounts[CI_SUBTYPE], "amount": cost_cents, "description": label},
            {"account_id": accounts[ACCRUED_SUBTYPE], "amount": -cost_cents, "description": label},
        ],
    }
    made = post_entry(c, entry, by=SYSTEM, hold_ids=hold_ids, at=at, touch=True)
    return str(made["entry_id"])
