"""Gateway credit top-ups (spec §9): money moved from a bank account onto Gateway credit (1100), the prepaid asset
usage draws down (usage.py). One balanced entry, Dr 1100 Gateway credit / Cr the bank account's line (or clearing, see
below). Both sides are assets, so no expense or income moves and the cash and accrual P&L are the same before and after.

Where the cash leaves, as for a payment marked paid (spec §4.3):
- A connected account: the entry posts at once, and the bank row that later shows the same payment takes it
  (waiting_for, read by banking._settle), so the cash posts once. A row already in the feed can be named by feed_id
  instead; it is then the row that posts Dr 1100.
- An account with no connection: no bank row will ever show the payment, so the credit goes to payments clearing
  (1050) and the bank's own line is not touched."""
from typing import Any

from . import handlers, posting
from . import usage
from .context import Ctx
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Result
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL

KIND = "gateway_topup"
PREFIX = "gateway_topup"        # a top-up's entry is sourced "bank" with source id PREFIX:<action id>
MEMO_MOST = 140


def topup_plan(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """One top-up: Dr Gateway credit / Cr the bank account's line (clearing when the account is not connected). Dated
    the day given (today by default); a day in a closed period posts today and names its own date in the memo, as a
    late bank row does (banking.open_day). With feed_id, the bank row already in the feed posts it (dated by its bank)."""
    from .banking import _unpost, feed_line, mark_posted, open_day, post_row
    from .documents import CLEARING, clearing
    from .tab import sees_money_account
    if ctx.book.mode == "mirror":
        raise FinanceError(NOT_AVAILABLE, "a Gateway top-up is not available while the books are a provider's")
    V.only(p, ("money_account_id", "amount", "date", "memo", "feed_id"))
    money = SK.get(ctx.c, "fin_money_accounts", V.text(p, "money_account_id", 80))
    if money is None or str(money["book_id"]) != ctx.book.id:
        raise invalid("money_account_id is not a money account in this book")
    if not sees_money_account(ctx, ctx.view(who), money):
        raise FinanceError(NOT_FOUND, "no such money account in your view")
    if str(money["kind"]) != "bank" or not money["account_id"]:
        raise invalid("a Gateway top-up comes from a bank account")
    bank = ctx.accounts.get(str(money["account_id"]))
    if bank is None or not bank.active:
        raise invalid("that bank account has no active line in the books")
    gateway = next((a for a in ctx.accounts.values() if a.code == usage.CREDIT), None)
    if gateway is None or not gateway.active:
        raise FinanceError(NOT_FOUND,
                           f"the Gateway credit account ({usage.CREDIT}) is not in the books or is not active")
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("amount is more than zero (minor units)")
    when = V.opt_day(p, "date") or ctx.today
    connected = bool(money["connection_id"])
    row = None
    feed_id = V.opt_text(p, "feed_id", 80)
    if feed_id:
        row = _feed_row(ctx, who, money, feed_id, amount)
    elif connected and _unposted_row(ctx, money, amount, when) is not None:
        raise FinanceError(CONFLICT, "a bank row of this amount is already in the feed and may be this payment; "
                                     "name it with feed_id")
    day = open_day(ctx, when) if row is None else str(row["txn_date"])
    memo = (V.opt_text(p, "memo", MEMO_MOST) or f"Gateway credit top-up from {money['name']}") + \
        ("" if day == when or row is not None else f" (dated {when})")
    for account_id in (bank.id, gateway.id):
        if not handlers.may_write(ctx, who, account_id, {}):
            raise denied("those lines are outside your view")
    base = ctx.book.base_currency
    credit_code = bank.code if connected else CLEARING[0]

    def apply(aid: str) -> Result:
        if row is not None:
            if row["entry_id"]:
                _unpost(ctx, row, "re-categorized")
            t = F.FeedTxn(provider_id=str(row["provider_id"]), account_id="", amount=int(row["amount"]),
                          txn_date=str(row["txn_date"]), status=F.POSTED, kind=str(row["kind"]),
                          counterparty=str(row["counterparty"]), description=str(row["description"]),
                          card_id=str(row["card_id"] or ""))
            entry = post_row(ctx, str(row["id"]), t, [feed_line(gateway.id, amount)], bank.id, [])
            mark_posted(ctx, [str(row["id"])], entry)
            return {"entry_id": entry, "money_account_id": str(money["id"]), "amount": amount,
                    "date": str(row["txn_date"]), "feed_id": str(row["id"])}
        line = bank if connected else clearing(ctx, who)
        e = L.Entry(book_id=ctx.book.id, entry_date=day, memo=memo[:200], source="bank",
                    source_id=f"{PREFIX}:{aid}",
                    lines=(L.Line(account_id=gateway.id, amount=amount, currency=base, original_amount=amount),
                           L.Line(account_id=line.id, amount=-amount, currency=base, original_amount=-amount)))
        entry_id, _ = posting.post(ctx, e, by=who, action_id=aid)
        return {"entry_id": entry_id, "money_account_id": str(money["id"]), "amount": amount, "date": day,
                "feed_id": None}
    return Plan(K.default_gate(ctx.org, who, KIND),
                f"Top up Gateway credit with {amount / 100:.2f} {base} from {money['name']} on {day}"[:200], apply,
                resolved={"date": when},
                facts={"amount": amount, "accounts": sorted({gateway.code, credit_code})})


def _feed_row(ctx: Ctx, who: str, money: Any, feed_id: str, amount: int) -> Any:
    """The bank row a top-up names: money out of this account, of this amount, not pending, ignored, reconciled, a
    payment or card charge, one side of a transfer, already paying a document or already a top-up's bank side."""
    from .banking import seen
    row = SK.get(ctx.c, "fin_feed_txns", feed_id)
    if row is None or not seen(ctx, who, row):
        raise FinanceError(NOT_FOUND, "no such feed row in your view")
    if str(row["money_account_id"]) != str(money["id"]):
        raise invalid("that feed row is on another money account")
    if int(row["amount"]) != -amount:
        raise invalid("that feed row is not money out of this amount")
    if row["pending"] or str(row["status"]) == "ignored":
        raise FinanceError(CONFLICT, "that row is pending or ignored")
    if row["reconciliation_id"]:
        raise FinanceError(CONFLICT, "that row is reconciled; undo the reconciliation to change it")
    if row["request_id"] or row["card_id"]:
        raise FinanceError(CONFLICT, "that row is a Finance payment or a card charge")
    if row["entry_id"]:
        entry = SL.entry_row(ctx.c, str(row["entry_id"]))
        if len(SK.rows_of_entry(ctx.c, str(row["entry_id"]))) > 1 or is_top_up(entry):
            raise FinanceError(CONFLICT, "that row is a transfer between your own accounts or a top-up's bank side")
    if SAP.settled_from(ctx.c, "feed", feed_id):
        raise FinanceError(CONFLICT, "that row already pays a document")
    return row


def _unposted_row(ctx: Ctx, money: Any, amount: int, day: str) -> Any:
    """A settled row of this amount on this account, within the match window of `day`, that no entry has taken yet."""
    from .documents import _window
    start, end = _window(day)
    for r in SK.transfer_candidates(ctx.c, [str(money["id"])], -amount, start, end):
        if r["entry_id"] is None and str(r["status"]) == "new" and not r["request_id"] and not r["card_id"]:
            return r
    return None


def is_top_up(entry: Any) -> bool:
    """Whether a ledger entry is a Gateway top-up's (its bank side is the top-up's own, not a feed row's)."""
    return entry is not None and str(entry["source"]) == "bank" and str(entry["source_id"]).startswith(PREFIX + ":")


def waiting_for(ctx: Ctx, money: Any, t: F.FeedTxn) -> list[str]:
    """The entries of top-ups from this connected account, of this amount, dated within the match window of the row's
    day, that no bank row has taken yet: the row is their bank side. banking._settle links the row when there is
    exactly one."""
    from .documents import _window
    bank = str(money["account_id"])
    start, end = _window(t.txn_date)
    found: list[str] = []
    for e in SL.posted_by_source_prefix(ctx.c, ctx.book.id, "bank", PREFIX):
        if not start <= str(e["entry_date"]) <= end or SK.rows_of_entry(ctx.c, str(e["id"])):
            continue
        bank_side = [line.amount for _, line in SL.entry_lines(ctx.c, str(e["id"])) if line.account_id == bank]
        if bank_side == [t.amount]:
            found.append(str(e["id"]))
    return found


PLANNERS = {KIND: topup_plan}
