"""Stripe balance activity in the books (spec §9). The Stripe balance is a money account and each balance transaction
posts on its own day: a sale is income, or the open invoice it pays when a contact's name matches; Stripe's fee is an
expense; a refund or a dispute reverses income; Stripe's own charges are expenses. A payout moves the balance to the
bank as one transfer entry with the deposit of the same amount, whichever arrives second, so it counts once. Called
from banking.ingest's settle step, inside the caller's transaction."""
from dataclasses import replace
from datetime import timedelta
from typing import Any

from . import banking, documents
from .context import Ctx
from .core import dates
from .core import feeds as F
from .core import ledger as L
from .core.errors import invalid
from .core.org import SYSTEM
from .store import banking as SK

INCOME_CODE = "4000"
FEES_CODE, FEES_NAME = "6200", "Payment processing fees"
CHARGES = ("charge", "payment")      # the sales whose gross may pay an open invoice


def settle(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> None:
    """Post one settled Stripe balance transaction. The row's amount is the net the balance moved, so the balance line
    is net, and the other lines (income or expense, and the fee) carry the gross and the fee and sum to minus net."""
    fid = str(row["id"])
    gross, fee = F.stripe_parts(t.raw)
    if t.kind == F.STRIPE_PAYOUT:
        _payout(ctx, row)
        return
    if t.kind in F.STRIPE_SALES:
        if t.kind in CHARGES and gross > 0 and _invoice(ctx, row, money, t, gross, fee):
            return
        counter = _income(ctx)
    elif t.kind in F.STRIPE_COSTS:
        counter = _fees(ctx)
    else:
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid})   # a kind nothing posts: a person says
        return
    lines = [banking.feed_line(counter.id, -gross)]
    if fee:
        lines.append(banking.feed_line(_fees(ctx).id, fee))
    entry = banking.post_row(ctx, fid, t, lines, _balance(ctx, money).id, [])
    banking.mark_posted(ctx, [fid], entry)


def bank_deposit(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> bool:
    """A bank deposit that is a Stripe payout takes that payout's transfer, once. True when it did, or when several
    payouts could be it and a person picks. False leaves the deposit to the other posting rules (a card's payment and
    any other money account are never a payout)."""
    if str(money["kind"]) != "bank":
        return False
    stripe = [str(m["id"]) for m in SK.money_accounts(ctx.c, ctx.book.id) if str(m["kind"]) == "stripe"]
    start, end = _window(t.txn_date)
    found = [r for r in SK.transfer_candidates(ctx.c, stripe, -t.amount, start, end)
             if _open(r) and str(r["kind"]) == F.STRIPE_PAYOUT]
    fid = str(row["id"])
    if not found:
        return False
    if len(found) > 1:
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid, "why": "several possible payouts"},
                      "ambiguous")
        return True
    payout = found[0]
    banking.transfer(ctx, payout, SK.get(ctx.c, "fin_money_accounts", str(payout["money_account_id"])), row)
    return True


def _invoice(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int, fee: int) -> bool:
    """A sale that pays the one open invoice of its gross amount from a contact the billing name matches: the
    receivable settles on the gross, and the fee posts as usual. False when no single invoice matches (income posts);
    True when several could be it (a person links it, nothing posts)."""
    fid = str(row["id"])
    found = documents.match_feed(ctx, row, replace(t, amount=gross))
    if isinstance(found, str):
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid})
        return True
    if found is None:
        return False
    kind, doc = found
    contact = str(doc[f"{documents.CONTACT_OF[kind]}_id"])
    lines = [banking.feed_line(documents.control(ctx, kind).id, -gross, {}, contact)]
    if fee:
        lines.append(banking.feed_line(_fees(ctx).id, fee))
    entry = banking.post_row(ctx, fid, t, lines, _balance(ctx, money).id, [])
    banking.mark_posted(ctx, [fid], entry)
    documents.settle(ctx, kind, str(doc["id"]), amount=gross, day=str(row["txn_date"]), from_kind="feed",
                     from_id=fid, entry_id=entry)
    return True


def _payout(ctx: Ctx, row: Any) -> None:
    """The balance moved to the bank: the deposit of the same amount within MATCH_DAYS takes one transfer entry for both
    sides. Several deposits that could be it are a person's to pick; none yet and the payout waits as new."""
    fid = str(row["id"])
    bank = [str(m["id"]) for m in SK.money_accounts(ctx.c, ctx.book.id) if str(m["kind"]) == "bank"]
    start, end = _window(str(row["txn_date"]))
    found = [r for r in SK.transfer_candidates(ctx.c, bank, -int(row["amount"]), start, end) if _open(r)]
    if len(found) > 1:
        banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid, "why": "several possible deposits"},
                      "ambiguous")
        return
    if found:
        banking.transfer(ctx, row, SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"])), found[0])


def _open(r: Any) -> bool:
    """A feed row still waiting to post, that is not a card's or a payment's."""
    return str(r["status"]) == "new" and not r["entry_id"] and not r["card_id"] and not r["request_id"]


def _window(day: str) -> tuple[str, str]:
    d = dates.day(day)
    return (d - timedelta(days=banking.MATCH_DAYS)).isoformat(), (d + timedelta(days=banking.MATCH_DAYS)).isoformat()


def _balance(ctx: Ctx, money: Any) -> L.Account:
    account = ctx.accounts.get(str(money["account_id"] or ""))
    if account is None:
        raise invalid("that money account has no account in the books")
    return account


def _income(ctx: Ctx) -> L.Account:
    found = banking.by_code(ctx, INCOME_CODE)
    if found is None:
        raise invalid("the books have no Revenue account")
    return found


def _fees(ctx: Ctx) -> L.Account:
    return banking.by_code(ctx, FEES_CODE) or banking.new_account(ctx, FEES_CODE, FEES_NAME, "expense", "", SYSTEM)
