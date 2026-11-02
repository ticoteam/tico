"""Refunds on a bank or card account (spec §4.3, §9): money in from a merchant the books already paid, from the same money
account. The money-out row it refunds is the newest one of this amount still open to refund (exact), else the only one
at least that large. The refund posts the reversal of that row's own lines, in proportion and with their tags, as a
Stripe refund reverses its sale (stripe_post._unwind), and records the row it refunds (`refund_of`). Several rows that
could be it, or none that fits, leave the refund new and asked, with the candidates named by id. A money-out row that
already pays a document is not a candidate: reopening that document is not done here."""
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
    exact = [o for o, left in found if left == t.amount]
    fitting = [o for o, left in found if left >= t.amount]
    if exact:
        _post(ctx, row, money, t, exact[0], card)       # the newest one of this exact amount
    elif len(fitting) == 1:
        _post(ctx, row, money, t, fitting[0], card)
    else:                                              # several could be it, or none fits: a person picks
        open_rows = [str(o["id"]) for o, left in found if left > 0]
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid, "candidates": open_rows})
    return True


def _candidates(ctx: Ctx, money: Any, t: F.FeedTxn) -> list[tuple[Any, int]]:
    """The money-out rows on this account to this counterparty, dated within the window before the refund, newest
    first, each with what it still leaves to refund in cents (its amount less the refunds already posted for it)."""
    start = (date.fromisoformat(t.txn_date) - timedelta(days=WINDOW_DAYS)).isoformat()
    out: list[tuple[Any, int]] = []
    for r in SK.refund_candidates(ctx.c, str(money["id"]), start, t.txn_date):
        if not documents._same_name(t.counterparty, str(r["counterparty"])):
            continue
        if str(r["kind"]) in F.TRANSFER_KINDS or SAP.settled_from(ctx.c, "feed", str(r["id"])):
            continue
        refunded = sum(int(x["amount"]) for x in SK.refunds_of(ctx.c, str(r["id"])))
        out.append((r, -int(r["amount"]) - refunded))
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
             for account, amount, tags, contact in stripe_post._reversal(counter, before, before + t.amount)]
    day = str(original["txn_date"])
    memo = f"{t.counterparty[:140]} refund of the charge dated {day}" if SL.closed_on(ctx.c, ctx.book.id, day) else ""
    entry = banking.post_row(ctx, fid, t, lines, banking._gl(ctx, money).id, [], memo=memo)
    banking.mark_posted(ctx, [fid], entry)
    SK.update(ctx.c, "fin_feed_txns", fid, {"refund_of": str(original["id"]), "updated_at": ctx.at})
    if card is not None:
        banking.card_used(ctx, row, -t.amount, entry)
