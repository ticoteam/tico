"""Internal transfers (spec §5, §9): money from one of the company's own bank accounts to another, through Mercury's
transfer endpoint (`POST /transfer`, with an idempotency key). It is money movement, not money out: §6.4 has no rule
for it (rules 1 and 2 are for payments), so the default gate allows it, and a company's own gate rules and approvals
apply like any action's.

Posting needs no code here. Both legs arrive as feed rows of kind internalTransfer, and banking's matcher posts them as
one transfer entry (Dr the account the money went to, Cr the one it left). Neither leg is an income or expense line.

The send is an outbox write (sync.outbox, op `transfer_create`), made once under the transfer's own key. An unknown
result (no answer, a 5xx, or a worker cut off mid-call) is never sent again on its own: the row goes `unknown` and a
finance admin is asked to check Mercury. A refused send (4xx) goes `failed` and is reported the same way."""
from collections.abc import Mapping
from typing import Any

from . import payments
from .context import Ctx
from .core import kinds as K
from .core import validate as V
from .core.errors import NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Result
from .mercury import dollars
from .store import banking as SK
from .store import ledger as SL
from .store import tico


def _bank_account(ctx: Ctx, money_id: str) -> Any:
    row = SK.get(ctx.c, "fin_money_accounts", money_id)
    if row is None or str(row["book_id"]) != ctx.book.id or str(row["kind"]) != "bank" \
            or str(row["status"] or "active") != "active":
        raise FinanceError(NOT_FOUND, "no such bank account")
    return row


def transfer_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Move money between two of the company's own bank accounts on one bank connection. `amount` is in cents."""
    V.only(p, ("from_account", "to_account", "amount", "memo"))
    src = _bank_account(ctx, V.text(p, "from_account", 80))
    dst = _bank_account(ctx, V.text(p, "to_account", 80))
    if str(src["id"]) == str(dst["id"]):
        raise invalid("a transfer needs two different accounts")
    if not src["connection_id"] or src["connection_id"] != dst["connection_id"]:
        raise invalid("a transfer goes between two accounts on the same bank connection")
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("a transfer is more than 0")
    if not src["provider_account_id"] or not dst["provider_account_id"]:
        raise invalid("sync both accounts from Mercury before moving money between them")
    conf = payments.settings(ctx.c, str(src["connection_id"]))
    if not conf.get("payments_enabled"):
        raise denied(str(conf.get("approval_note") or "payments are off for this bank connection"))
    tid = SL.new_id()
    key = f"transfer-{tid}"
    memo = V.opt_text(p, "memo", 140) or ""
    gate = K.default_gate(ctx.org, who, "transfer_create")

    def apply(aid: str) -> Result:
        SK.insert(ctx.c, "fin_transfers", {
            "id": tid, "book_id": ctx.book.id, "action_id": aid,
            "from_money_account_id": str(src["id"]), "to_money_account_id": str(dst["id"]),
            "amount": amount, "currency": ctx.book.base_currency, "memo": memo, "idempotency_key": key,
            "status": "queued", "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        SK.outbox_add(ctx.c, connection_id=str(src["connection_id"]), action_id=aid, op="transfer_create",
                      payload={"transfer_id": tid, "source_account": str(src["provider_account_id"]),
                               "destination_account": str(dst["provider_account_id"]), "amount": amount,
                               "idempotency_key": key}, key=key, by=who, at=ctx.at)
        tico.after_commit(ctx.c, payments.ring)
        return {"transfer_id": tid, "status": "queued"}
    return Plan(gate, f"Move {amount / 100:.2f} {ctx.book.base_currency} from {src['name']} to {dst['name']}", apply,
                facts={"amount": amount, "from_account": str(src["id"]), "to_account": str(dst["id"])})


def request_body(payload: Mapping[str, Any]) -> dict[str, object]:
    """Mercury's transfer body: the documented fields, the amount in dollars and Finance's key for this transfer."""
    return {"sourceAccountId": str(payload["source_account"]),
            "destinationAccountId": str(payload["destination_account"]),
            "amount": dollars(int(payload["amount"])), "idempotencyKey": str(payload["idempotency_key"])}


def sent(ctx: Ctx, payload: Mapping[str, Any], answer: Mapping[str, Any]) -> None:
    """Mercury took the transfer: its id is kept. The bank feed posts both legs when they arrive."""
    SK.update(ctx.c, "fin_transfers", str(payload["transfer_id"]), {
        "status": "submitted", "provider_txn_id": str(answer.get("id") or "") or None, "last_error": None,
        "updated_at": ctx.at})


def not_sent(ctx: Ctx, payload: Mapping[str, Any], why: str, unknown: bool) -> None:
    """A send that did not clearly land: `unknown` when it may have (no answer, a 5xx, a call cut off), else refused.
    Either way a finance admin is asked to check Mercury, and nothing sends the transfer again on its own."""
    tid = str(payload["transfer_id"])
    SK.update(ctx.c, "fin_transfers", tid, {"status": "unknown" if unknown else "failed", "last_error": why[:500],
                                           "updated_at": ctx.at})
    amount = dollars(int(payload["amount"]))
    if unknown:
        title = f"Check Mercury for transfer {tid}"
        body = (f"Finance sent a transfer of {amount} between two of the company's Mercury accounts and got no clear "
                f"answer back. It may or may not have gone through. Check Mercury before anything else. Finance will "
                f"not send it again on its own.")
    else:
        title = f"Mercury refused transfer {tid}"
        body = (f"Mercury refused a transfer of {amount} between two of the company's Mercury accounts: "
                f"{why[:200]}. Nothing moved. Finance will not send it again on its own.")
    tico.needs_you(ctx.c, payments.admin_person(ctx), title, body)


PLANNERS = {"transfer_create": transfer_create}
