"""Gateway credit top-ups (spec §9): money moved from a bank account onto Gateway credit (1100), the prepaid asset
usage draws down (usage.py). One balanced entry, Dr 1100 Gateway credit / Cr the bank account's line. Both sides are
assets, so no expense or income moves and the cash and accrual P&L are the same before and after. A top-up is entered
by a person; the bank row that later shows the same payment is a separate feed row and is not matched here."""
from . import handlers, posting
from . import usage
from .context import Ctx
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import NOT_AVAILABLE, NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Result
from .store import banking as SK

KIND = "gateway_topup"
MEMO_MOST = 140


def topup_plan(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """One top-up: Dr Gateway credit / Cr the bank account's line. Dated the day given (today by default); a day in a
    closed period posts today and names its own date in the memo, as a late bank row does (banking.open_day)."""
    from .banking import open_day
    from .tab import sees_money_account
    if ctx.book.mode == "mirror":
        raise FinanceError(NOT_AVAILABLE, "a Gateway top-up is not available while the books are a provider's")
    V.only(p, ("money_account_id", "amount", "date", "memo"))
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
    day = open_day(ctx, when)
    memo = (V.opt_text(p, "memo", MEMO_MOST) or f"Gateway credit top-up from {money['name']}") + \
        ("" if day == when else f" (dated {when})")
    for account_id in (bank.id, gateway.id):
        if not handlers.may_write(ctx, who, account_id, {}):
            raise denied("those lines are outside your view")
    base = ctx.book.base_currency

    def apply(aid: str) -> Result:
        e = L.Entry(book_id=ctx.book.id, entry_date=day, memo=memo[:200], source="bank",
                    source_id=f"gateway_topup:{aid}",
                    lines=(L.Line(account_id=gateway.id, amount=amount, currency=base, original_amount=amount),
                           L.Line(account_id=bank.id, amount=-amount, currency=base, original_amount=-amount)))
        entry_id, _ = posting.post(ctx, e, by=who, action_id=aid)
        return {"entry_id": entry_id, "money_account_id": str(money["id"]), "amount": amount, "date": day}
    return Plan(K.default_gate(ctx.org, who, KIND),
                f"Top up Gateway credit with {amount / 100:.2f} {base} from {money['name']} on {day}"[:200], apply,
                resolved={"date": when},
                facts={"amount": amount, "accounts": sorted({gateway.code, bank.code})})


PLANNERS = {KIND: topup_plan}
