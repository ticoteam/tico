"""Refunds on a bank or card account (spec §4.3, §9): money in from a merchant the books already paid, from the same money
account. The money-out row it refunds is the newest one of this amount still open to refund (exact), else the only one
at least that large. The refund posts the reversal of that row's own lines, in proportion and with their tags, as a
Stripe refund reverses its sale (stripe_post._unwind), and records the row it refunds (`refund_of`). Several rows that
could be it, or none that fits, leave the refund new and asked, with the candidates named by id. A money-out row that
already pays a bill is not reopened here: money back that could refund it stays new and is asked, naming the bill, so no
posting rule can post it as income (a vendor's refund is not income)."""
from datetime import date, timedelta
from typing import Any

from . import banking, documents, stripe_post
from .context import Ctx
from .core import feeds as F
from .core import ledger as L
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL

WINDOW_DAYS = 120       # the money-out row a refund reverses is dated at most this many days before the refund


def settle(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, card: Any) -> bool:
    """Post a money-in row that refunds a money-out row on this account, or ask about it. False when no money-out row to
    this counterparty is in the window: nothing says the row is a refund, so the ordinary path takes it."""
    if t.amount <= 0 or not t.counterparty:
        return False
    found = _candidates(ctx, money, t)
    if not found:
        return False
    fid = str(row["id"])
    open_found = [(o, left) for o, left, paid in found if not paid]
    paid_found = [(o, left) for o, left, paid in found if paid and left >= t.amount]
    if not open_found and not paid_found:
        return False
    exact = [o for o, left in open_found if left == t.amount]
    fitting = [o for o, left in open_found if left >= t.amount]
    if not paid_found and exact:
        _post(ctx, row, money, t, exact[0], card)       # the newest one of this exact amount
    elif not paid_found and len(fitting) == 1:
        _post(ctx, row, money, t, fitting[0], card)
    else:                                              # several could be it, a bill's payment could be it, or none fits
        open_rows = [str(o["id"]) for o, left in open_found if left > 0]
        bills = sorted({to for o, _ in paid_found for kind, to in SAP.settled_to(ctx.c, "feed", str(o["id"]))
                        if kind == "bill"})
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid, "candidates": open_rows, "bills": bills})
    return True


def _candidates(ctx: Ctx, money: Any, t: F.FeedTxn) -> list[tuple[Any, int, bool]]:
    """The money-out rows on this account to this counterparty, dated within the window before the refund, newest
    first, each with what it still leaves to refund in cents (its amount less the refunds already posted for it) and
    whether a bill or invoice settled it (its payment)."""
    start = (date.fromisoformat(t.txn_date) - timedelta(days=WINDOW_DAYS)).isoformat()
    out: list[tuple[Any, int, bool]] = []
    for r in SK.refund_candidates(ctx.c, str(money["id"]), start, t.txn_date):
        if not documents.same_name(t.counterparty, str(r["counterparty"])):
            continue
        if str(r["kind"]) in F.TRANSFER_KINDS:
            continue
        refunded = sum(int(x["amount"]) for x in SK.refunds_of(ctx.c, str(r["id"])))
        paid = SAP.settled_from(ctx.c, "feed", str(r["id"])) > 0
        out.append((r, -int(r["amount"]) - refunded, paid))
    return out


def _counter(ctx: Ctx, original: Any) -> list[stripe_post.Share]:
    """The lines a money-out row posted besides its money line: each a debit, which a refund credits back. Empty when
    there is nothing a refund can reverse (the row is not posted, or its lines are not all debits)."""
    if not original["entry_id"]:
        return []
    lines = [(line.account_id, line.amount, dict(line.tags), line.contact_id)
             for _, line in SL.entry_lines(ctx.c, str(original["entry_id"])) if line.role != L.MONEY_ROLE]
    return lines if lines and all(amount > 0 for _, amount, _, _ in lines) else []


def _post(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, original: Any, card: Any) -> None:
    """The refund's entry: the reversal of the original's lines for this refund's share, then the money account's line.
    A refund of a charge in a closed period posts today and keeps the charge's date in the memo."""
    fid = str(row["id"])
    counter = _counter(ctx, original)
    if not counter:
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid, "candidates": [str(original["id"])]})
        return
    before = sum(int(x["amount"]) for x in SK.refunds_of(ctx.c, str(original["id"])))
    lines = [banking.feed_line(account, amount, tags, contact)
             for account, amount, tags, contact in stripe_post.reversal_shares(counter, before, before + t.amount)]
    day = str(original["txn_date"])
    memo = f"{t.counterparty[:140]} refund of the charge dated {day}" if SL.closed_on(ctx.c, ctx.book.id, day) else ""
    entry = banking.post_row(ctx, fid, t, lines, banking.gl_account(ctx, money).id, [], memo=memo)
    banking.mark_posted(ctx, [fid], entry)
    SK.update(ctx.c, "fin_feed_txns", fid, {"refund_of": str(original["id"]), "updated_at": ctx.at})
    if card is not None:
        banking.card_used(ctx, row, -t.amount, entry)
