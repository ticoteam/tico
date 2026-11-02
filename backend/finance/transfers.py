"""Internal transfers (spec §5, §9): money from one of the company's own bank accounts to another. It is a payment_create
with method "internal": payments.payment_create hands it here, and it takes the same payment gates. Over $1,000 asks the
owner (§6.4 rule 2) and a company rule can ask for more. The new-recipient rule (rule 1) never applies: an own account
is never a new recipient, so the gate's facts say known. There is no budget hold: a move between our own accounts is not
an expense.

The requester must see both accounts (the view a payment's line gets). The gate sees both ledger accounts as
`accounts`, so a rule on accounts matches a transfer as it matches a payment.

The send is an outbox write (sync.outbox, op `transfer_create`, the provider call), made once under the transfer's own
key: Mercury's POST /transfer. The claim re-checks it right before that call (recheck, §5 re-check at use): the requester
still has the payment right and sees both accounts, payments are still on, and the gates still let it through. A gate
that now asks a person holds it; anything else cancels it. Neither sends. Until its call is claimed, the transfer can be
cancelled (plan_cancel). An unknown result (no answer, a 5xx, or a worker cut off mid-call) is never sent again on its
own: the row goes `unknown` and a finance admin is asked to check Mercury. A refused send (4xx) goes `failed` and is
reported the same way.

Posting needs no code here. Both legs arrive as feed rows of kind internalTransfer, and banking's matcher posts them as
one transfer entry (Dr the account the money went to, Cr the one it left) with no income or expense line. The matcher
links the two legs to the transfer row and settles it to `posted` (store/banking: settle_pair, settle_legs), for legs
dated from the transfer's submit day through TRANSFER_WINDOW_DAYS on."""
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import payments
from .context import Ctx
from .core import feeds as F
from .core import gates as G
from .core import kinds as K
from .core import rights as R
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Result
from .mercury import dollars
from .store import actions as SX
from .store import banking as SK
from .store import ledger as SL
from .store import tico

HOLD_MINUTES = 15       # a held transfer is looked at again this long after


def _visible(ctx: Ctx, who: str, money: Any) -> bool:
    from .tab import sees_money_account         # lazy import, as banking does
    return sees_money_account(ctx, ctx.view(who), money)


def _bank_account(ctx: Ctx, who: str, money_id: str) -> Any:
    row = SK.get(ctx.c, "fin_money_accounts", money_id)
    if row is None or str(row["book_id"]) != ctx.book.id or str(row["kind"]) != "bank" \
            or str(row["status"] or "active") != "active":
        raise FinanceError(NOT_FOUND, "no such bank account")
    if not _visible(ctx, who, row):
        # Nothing about the account is named: a transfer the requester cannot see both accounts of says only this.
        raise denied("a transfer needs both accounts in your view")
    return row


def _codes(ctx: Ctx, *rows: Any) -> list[str]:
    lines = [ctx.accounts.get(str(r["account_id"] or "")) for r in rows]
    return [line.code for line in lines if line is not None]


def _facts(ctx: Ctx, src: Any, dst: Any, amount: int) -> dict[str, object]:
    """What payment rules match on for a transfer: a payment's facts, with the company's own account as the known
    recipient and both ledger accounts as `accounts`."""
    balance = src["available_balance"] if src["available_balance"] is not None else src["balance"]
    return {"amount": amount, "method": payments.INTERNAL, "recipient.known": True,
            "recipient.name": str(dst["name"]), "counterparty": str(dst["name"]), "accounts": _codes(ctx, src, dst),
            "obligation": "", "balance": balance,
            G.AGES: {"balance": payments.age_minutes(ctx, src["balance_as_of"])}}


def plan_internal(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """payment_create with method "internal": move `amount` (cents) from `from_account` to `to_account`, two of the
    company's bank accounts on one connection. Both must be in the requester's view."""
    V.only(p, ("method", "from_account", "to_account", "amount", "memo"))
    src = _bank_account(ctx, who, V.text(p, "from_account", 80))
    dst = _bank_account(ctx, who, V.text(p, "to_account", 80))
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
    gate = K.default_gate(ctx.org, who, "payment_create")
    facts = _facts(ctx, src, dst, amount)

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
        return {"transfer_id": tid, "method": payments.INTERNAL, "status": "queued"}
    return Plan(gate, f"Move {amount / 100:.2f} {ctx.book.base_currency} from {src['name']} to {dst['name']}", apply,
                facts=facts)


def request_body(payload: Mapping[str, Any]) -> dict[str, object]:
    """Mercury's transfer body: the documented fields, the amount in dollars, and Finance's key for this transfer.
    The key also goes in `note`, a documented field (payments.request_body does the same): an unknown transfer is found
    by it in Mercury's transactions."""
    key = str(payload["idempotency_key"])
    return {"sourceAccountId": str(payload["source_account"]),
            "destinationAccountId": str(payload["destination_account"]),
            "amount": dollars(int(payload["amount"])), "idempotencyKey": key, "note": f"{payments.NOTE}{key}"}


def _later(ctx: Ctx) -> str:
    then = datetime.fromisoformat(ctx.at.replace("Z", "+00:00")) + timedelta(minutes=HOLD_MINUTES)
    return then.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stop(ctx: Ctx, o: Any, t: Any, why: str, *, hold: bool) -> None:
    """The claimed write does not go. A hold gives the claim back (the write waits, and a finance admin is asked, once
    per reason); a cancel ends the transfer and its write, and tells a finance admin. Nothing is sent either way."""
    tid, oid = str(t["id"]), str(o["id"])
    amount = dollars(int(t["amount"]))
    if hold:
        first = str(t["last_error"] or "") != why[:500]
        SK.update(ctx.c, "fin_outbox", oid, {"claimed_at": None, "next_try_at": _later(ctx), "last_error": why[:300],
                                             "updated_at": ctx.at})
        SK.update(ctx.c, "fin_transfers", tid, {"last_error": why[:500], "updated_at": ctx.at})
        if first:
            tico.needs_you(ctx.c, payments.admin_person(ctx), f"Transfer of {amount} is waiting",
                           f"Transfer {tid} was not sent: {why}. Cancel it, or fix what stops it and it goes on its "
                           "own.", private=True)
        return
    SK.update(ctx.c, "fin_transfers", tid, {"status": "cancelled", "last_error": why[:500], "updated_at": ctx.at},
              "status='queued'")
    _give_back(ctx, tid)                         # it never goes to Mercury: the limits its claim reserved come back
    SK.update(ctx.c, "fin_outbox", oid, {"status": "failed", "claimed_at": None, "next_try_at": None,
                                         "last_error": why[:300], "updated_at": ctx.at})
    tico.needs_you(ctx.c, payments.admin_person(ctx), f"Transfer of {amount} was not sent",
                   f"Transfer {tid} was cancelled before it went to Mercury: {why}. Nothing moved.", private=True)


def recheck(ctx: Ctx, o: Any, fresh: list[G.Hit] | None = None) -> bool:
    """Right before Mercury is called (§5, re-check at use), for a transfer write the claim just took: whoever asked still
    has the payment right and sees both accounts, payments are still on for the bank connection, and the gate rules (with
    their counters, less this transfer's own) still let it through. True when it did not, so nothing is sent: the claim
    is released (a hold) or the transfer is cancelled. On a send, the limits it fits that it has not counted yet go into
    `fresh`, for `reserve` once the send is claimed."""
    from . import gating
    body = SK.loads(o["payload_json"])
    t = SK.get(ctx.c, "fin_transfers", str(body["transfer_id"]))
    if t is None or str(t["status"]) != "queued":
        SK.update(ctx.c, "fin_outbox", str(o["id"]), {"status": "failed", "claimed_at": None, "next_try_at": None,
                                                      "last_error": "no longer queued", "updated_at": ctx.at})
        return True
    src = SK.get(ctx.c, "fin_money_accounts", str(t["from_money_account_id"]))
    dst = SK.get(ctx.c, "fin_money_accounts", str(t["to_money_account_id"]))
    action = SX.by_id(ctx.c, str(t["action_id"] or ""))
    who = (str(action["on_behalf_of"] or "") or str(action["actor"])) if action is not None else str(t["created_by"])
    amount = int(t["amount"])
    right = K.authority(ctx.org, who, "payment_create", ctx.rights(who))
    if right is not None and right.outcome == "deny":
        _stop(ctx, o, t, "whoever asked for it no longer has the right to pay", hold=False)
        return True
    if src is None or dst is None or not _visible(ctx, who, src) or not _visible(ctx, who, dst):
        _stop(ctx, o, t, "its accounts are no longer in the requester's view", hold=False)
        return True
    if not payments.settings(ctx.c, str(src["connection_id"])).get("payments_enabled"):
        _stop(ctx, o, t, "payments were turned off for this bank connection", hold=False)
        return True
    found = gating.send_facts(ctx, who, "payment_create", _facts(ctx, src, dst, amount))
    mine = payments.own_counts(ctx, t["counted_json"], t["action_id"])

    def used(rule: G.Rule, key: str, subject: str) -> int:
        have = SK.counter(ctx.c, rule.id, key, subject)[0]
        return have - amount if (rule.id, key, subject) in mine else have
    verdict = G.evaluate(gating.rules(ctx), found, datetime.fromisoformat(ctx.today).date(), used)
    if verdict.outcome == "deny":
        _stop(ctx, o, t, verdict.reason or "a finance rule now refuses it", hold=False)
        return True
    if verdict.outcome == "needs_approval" and (
            verdict.stale or not payments.approved_for(ctx, action, who, verdict.who, verdict.rule)):
        _stop(ctx, o, t, f"{verdict.reason or 'a finance rule asks for approval'}"
                         f"{' (its data is stale)' if verdict.stale else ''}", hold=True)
        return True
    if fresh is not None:
        fresh.extend(h for h in verdict.counts if (h.rule, h.period_key, h.subject) not in mine)
    return False


def reserve(ctx: Ctx, o: Any, hits: Sequence[G.Hit]) -> None:
    """Move the counters of the limits this transfer fits, in the transaction that claims its send (payments.reserve
    does the same): the transfer owns them, so they move once, and come back if Mercury refuses it."""
    if not hits:
        return
    tid = str(SK.loads(o["payload_json"])["transfer_id"])
    t = SK.get(ctx.c, "fin_transfers", tid)
    if t is None:
        return
    owned = [list(x) for x in SK.loads_list(t["counted_json"])]
    for h in hits:
        SK.count(ctx.c, h.rule, h.period_key, h.subject, int(t["amount"]), ctx.at)
        owned.append([h.rule, h.period_key, h.subject])
    SK.update(ctx.c, "fin_transfers", tid, {"counted_json": SK.dump(owned)})


def _give_back(ctx: Ctx, tid: str) -> None:
    """A refused transfer takes back the counters it reserved at its claim, once. Its record then empties
    (payments.unreserve does the same)."""
    t = SK.get(ctx.c, "fin_transfers", tid)
    if t is None:
        return
    for raw in SK.loads_list(t["counted_json"]):
        if isinstance(raw, list) and len(cast(list[Any], raw)) == 3:
            rule, key, subject = cast(list[Any], raw)
            SK.uncount(ctx.c, str(rule), str(key), str(subject), int(t["amount"]), ctx.at)
    SK.update(ctx.c, "fin_transfers", tid, {"counted_json": "[]"})


def plan_cancel(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Cancel an internal transfer that has not gone to Mercury: queued, its write not yet claimed by a call. Once a call is
    claimed or made there is no cancel here (a person checks Mercury). Its maker or a finance admin may cancel it."""
    V.only(p, ("transfer_id",))
    t = SK.get(ctx.c, "fin_transfers", V.text(p, "transfer_id", 80))
    if t is None or str(t["book_id"]) != ctx.book.id \
            or (str(t["created_by"]) != who and not R.is_admin(ctx.org, who, ctx.rights(who))):
        raise FinanceError(NOT_FOUND, "no such transfer")
    status = str(t["status"])
    if status != "queued":
        raise FinanceError(CONFLICT, "it is already in Mercury: check Mercury" if status in (
            "submitted", "unknown", "posted") else f"that transfer is {status}")
    o = SK.outbox_by_key(ctx.c, str(t["idempotency_key"]))
    if o is None or str(o["status"]) != "pending" or o["claimed_at"] is not None:
        raise FinanceError(CONFLICT, "it is being sent to Mercury now: check Mercury")
    tid, oid = str(t["id"]), str(o["id"])

    def apply(aid: str) -> Result:
        # Both writes must still hold: the transfer queued and its write unclaimed. A worker that claimed it first wins.
        if SK.update(ctx.c, "fin_transfers", tid, {"status": "cancelled", "updated_at": ctx.at}, "status='queued'") != 1 \
                or SK.update(ctx.c, "fin_outbox", oid, {"status": "failed", "last_error": "cancelled before it was sent",
                                                         "next_try_at": None, "updated_at": ctx.at},
                             "status='pending' AND claimed_at IS NULL") != 1:
            raise FinanceError(CONFLICT, "the transfer moved on; check its status")
        _give_back(ctx, tid)                     # it never goes to Mercury: the limits its claim reserved come back
        return {"transfer_id": tid, "status": "cancelled"}
    return Plan(K.default_gate(ctx.org, who, "payment_cancel"),
                f"Cancel the transfer of {int(t['amount']) / 100:.2f} {ctx.book.base_currency}", apply)


def sent(ctx: Ctx, payload: Mapping[str, Any], answer: Mapping[str, Any]) -> None:
    """Mercury took the transfer. Its answer carries both legs, each a transaction with its own id: the out-leg's id is
    provider_txn_id (the one an unknown transfer is found by, and the leg a settlement prefers) and the in-leg's is kept
    beside it. The submit day starts the window its legs must post in. Both legs may already be in the feed (they posted
    before this answer was recorded), in which case it settles now; otherwise the matcher settles it when the second leg
    posts."""
    tid = str(payload["transfer_id"])
    SK.update(ctx.c, "fin_transfers", tid, {
        "status": "submitted", "provider_txn_id": _leg_id(answer.get("debitTransaction")),
        "provider_credit_txn_id": _leg_id(answer.get("creditTransaction")), "last_error": None,
        "submitted_at": ctx.at, "updated_at": ctx.at})
    SK.settle_legs(ctx.c, tid, ctx.at, F.TRANSFER_KINDS)


def _leg_id(leg: object) -> str | None:
    """One leg's id from Mercury's answer: its transaction's id, or None when the answer does not carry one."""
    if not isinstance(leg, dict):
        return None
    return str(cast(dict[str, Any], leg).get("id") or "") or None


def note(t: Any) -> str:
    """The note Mercury holds the transfer's key in (payments.NOTE and the key): what an unknown transfer is searched by."""
    return f"{payments.NOTE}{t['idempotency_key']}"


def search_days(t: Any) -> tuple[str, str]:
    """The first and last day (YYYY-MM-DD) a search for an unknown transfer's out-leg covers: its claim's day and the day
    either side of it, since Mercury's created day can fall either side of ours in UTC."""
    day = datetime.fromisoformat(str(t["submitted_at"]).replace("Z", "+00:00")).date()
    return (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()


def source_account(c: Any, t: Any) -> str:
    """The provider's id for the account a transfer leaves ("" when it is not known)."""
    src = SK.get(c, "fin_money_accounts", str(t["from_money_account_id"]))
    return str(src["provider_account_id"]) if src is not None else ""


def matches(t: Any, account: str, row: Mapping[str, object]) -> bool:
    """Whether one of Mercury's transactions is this transfer's out-leg: its key in the note, its amount, out of its
    source account."""
    return str(row.get("accountId") or "") == account and \
        payments.keyed(str(t["idempotency_key"]), row, int(t["amount"]))


def found(ctx: Ctx, t: Any, transactions: Sequence[Mapping[str, object]]) -> bool:
    """Resolve an unknown transfer from Mercury's transactions (payments.found does the same for requests). Its
    out-leg's created time is the submit day Mercury used, so the legs post from that day. With no such transaction,
    the claim time stands. True when it was found."""
    if t is None or str(t["status"]) != "unknown":
        return False
    account = source_account(ctx.c, t)
    mine = next((x for x in transactions if matches(t, account, x)), None)
    if mine is None:
        return False
    tid = str(t["id"])
    SK.update(ctx.c, "fin_transfers", tid, {
        "status": "submitted", "provider_txn_id": str(mine.get("id") or "") or None, "last_error": None,
        "submitted_at": _stamp(mine.get("createdAt")) or str(t["submitted_at"] or ctx.at), "updated_at": ctx.at})
    SK.settle_legs(ctx.c, tid, ctx.at, F.TRANSFER_KINDS)
    return True


def _stamp(value: object) -> str:
    """Mercury's timestamp in the store's form (UTC, microseconds), or "" when it is not one."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def not_sent(ctx: Ctx, payload: Mapping[str, Any], why: str, unknown: bool, *, started: object = None) -> None:
    """A send that did not clearly land: `unknown` when it may have (no answer, a 5xx, a call cut off), else refused.
    Either way a finance admin is asked to check Mercury, and nothing sends the transfer again on its own. `started` is
    when the call was claimed: it may have landed from then, so its legs are looked for from that day, not from the day
    the failure is recorded (a recovery can run days later)."""
    tid = str(payload["transfer_id"])
    fields: dict[str, object] = {"status": "unknown" if unknown else "failed", "last_error": why[:500],
                                 "updated_at": ctx.at}
    if unknown:
        fields["submitted_at"] = str(started or ctx.at)
    SK.update(ctx.c, "fin_transfers", tid, fields)
    if not unknown:
        _give_back(ctx, tid)                     # Mercury refused it: nothing moved, so its limits come back
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
    tico.needs_you(ctx.c, payments.admin_person(ctx), title, body, private=True)
