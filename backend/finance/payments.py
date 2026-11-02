"""Recipients and payments (spec §9). A payment is one immutable instruction (recipient version, amount, money account,
method, the line it posts to) with one idempotency key for its whole life, sent through Mercury's approval queue so a
person approves in Mercury too. Lifecycle: queued → awaiting_bank_approval → submitted → settled | returned | failed |
cancelled, plus unknown when a call timed out after it may have landed. One live payment per obligation; a live
payment keeps its hold until it settles or ends. Direct send is not built: only the owner's switch and its refusal.

The planners run inside `act`; the worker steps (`recheck`, `sent`, `polled`, `found`) are what finance/sync.py runs
around its provider calls, each in its own short transaction."""
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import budgeting, posting
from .context import Ctx
from .core import feeds as F
from .core import gates as G
from .core import kinds as K
from .core import ledger as L
from .core import org as O
from .core import rights as R
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, denied, invalid
from .core.org import SYSTEM
from .handlers import Plan, Result, may_write
from .mercury import IP_HINT, Answer, dollars, error_text
from .store import actions as SX
from .store import ap as SAP
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import tico

CASH_BELOW_KEY = "finance_cash_below"   # the owner's cash alert floor (minor units; 0 or unset: off)
DIRECT_KEY = "finance_direct_send"     # the owner's company switch (§9 mode 2), off unless the owner turns it on
METHODS = ("ach", "wire", "check")
INTERNAL = "internal"                   # payment_create's method for a move between our own bank accounts (§5)
MERCURY_METHOD = {"ach": "ach", "wire": "domesticWire", "check": "check"}
SENT, FAILED = "finance.payment.sent", "finance.payment.failed"
RETRY = (60, 300, 1800, 3600)
NOTE = "tico:"                          # every request's note carries its key, so an unknown one can be found again
UNKNOWN_ASK_MINUTES = 30                # an unknown payment not found in Mercury after this long goes to a finance admin
SENDING_MINUTES = 10                    # a payment claimed for sending longer than this was cut off mid-call: unknown


def _event(ctx: Ctx, name: str, pay: Any, why: str = "") -> None:
    from .banking import event
    event(ctx, name, str(pay["id"]), {"payment_id": str(pay["id"]), **({"why": why} if why else {})})


def settings(c: Any, connection_id: str) -> dict[str, Any]:
    row = SK.get(c, "fin_connections", connection_id)
    return SK.loads(row["settings_json"]) if row is not None else {}


# ------------------------------------------------------------------------------------------------ recipients
TAX_KINDS = ("contact_create", "contact_update")          # a contact's tax id is sealed the same way (§4.2)
SEALED_KINDS = ("recipient_add", *TAX_KINDS)
BANK_FIELDS = ("account_number", "routing_number", "tax_id")
SEALED_FIELDS = ("details_ref", "details_fp", "last4")     # what Finance keeps in their place; never sent by a caller
TAX_FIELDS = ("tax_id_ref", "tax_id_fp", "tax_id_last4")
REFS = ("details_ref", "tax_id_ref")


def _bank_details(p: Mapping[str, object]) -> tuple[str, dict[str, Any]]:
    method = V.choice(p, "method", METHODS)
    if method == "check":
        return method, {"address": V.obj(p.get("address") or {}, "address")}
    account, routing = V.text(p, "account_number", 34), V.text(p, "routing_number", 9)
    if not account.isdigit() or not routing.isdigit() or len(routing) != 9:
        raise invalid("an account number is digits and a routing number is 9 digits")
    return method, {"accountNumber": account, "routingNumber": routing}


def strip_details(c: Any, kind: str, body: Mapping[str, object]) -> tuple[dict[str, object], str]:
    """A recipient request with its account and routing numbers taken out, before anything is written or hashed: the
    payload keeps a keyed fingerprint and the last 4 only. The second value is the numbers as JSON, to seal ("" for a
    check, a dry run's caller drops it). Other kinds pass through unchanged."""
    out = dict(body)
    if kind not in SEALED_KINDS:
        return out, ""
    if kind in TAX_KINDS:
        return _strip_tax_id(c, out)
    if any(k in body for k in SEALED_FIELDS):
        raise invalid("send account_number and routing_number; Finance keeps them in the credential vault")
    method, info = _bank_details(body)
    for k in BANK_FIELDS:
        out.pop(k, None)
    out["details_fp"] = tico.fingerprint(c, "payee", F.details_hash(method, info))
    if method == "check":
        return out, ""
    out["last4"] = F.last4(info["accountNumber"])
    return out, json.dumps(info, sort_keys=True)


def _strip_tax_id(c: Any, out: dict[str, object]) -> tuple[dict[str, object], str]:
    """A contact's tax id taken out the same way: a keyed fingerprint and the last 4 stay, the number is sealed. An
    empty `tax_id` clears it."""
    if any(k in out for k in TAX_FIELDS):
        raise invalid("send tax_id; Finance keeps it in the credential vault")
    if "tax_id" not in out:
        return out, ""
    raw = out.pop("tax_id")
    if raw in (None, ""):
        out["tax_id_last4"] = ""
        return out, ""
    if V.has_card_number(raw):
        raise invalid(V.CARD_NUMBER_REFUSED)     # refused, never sealed
    if not isinstance(raw, str):
        raise invalid("tax_id is text")
    digits = "".join(ch for ch in raw if ch.isalnum())
    if not 4 <= len(digits) <= 20 or len(digits) != len(raw.replace("-", "").replace(" ", "")):
        raise invalid("a tax id is 4-20 letters and digits (dashes and spaces allowed)")
    out["tax_id_fp"] = tico.fingerprint(c, "taxid", digits)
    out["tax_id_last4"] = digits[-4:]
    return out, json.dumps({"tax_id": digits})


def seal_details(c: Any, body: Mapping[str, object], plain: str, by: str, at: str) -> dict[str, object]:
    """Seal stripped bank details (or a tax id) into the vault inside the action's savepoint, so a refusal rolls it
    back too."""
    if not plain:
        return dict(body)
    if "tax_id_fp" in body:
        last4 = str(body.get("tax_id_last4") or "")
        ref = tico.seal_credential(c, f"Finance tax id {str(body.get('name') or '')[:100]} ••{last4}", plain,
                                   f"••{last4}", by, at)
        return {**body, "tax_id_ref": ref}
    last4 = str(body.get("last4") or "")
    ref = tico.seal_credential(c, f"Finance payee {str(body.get('name') or '')[:100]} ••{last4}", plain,
                               f"••{last4}", by, at)
    return {**body, "details_ref": ref}


def drop_details(c: Any, body: Mapping[str, object]) -> None:
    """An action that will never run deletes the bank details (or tax id) it sealed."""
    for key in REFS:
        ref = body.get(key)
        if isinstance(ref, str) and ref:
            tico.drop_credential(c, ref)


def open_details(c: Any, ref: str) -> dict[str, Any]:
    """The sealed account and routing numbers, at send time ({} when they are gone)."""
    raw = tico.open_credential(c, ref) if ref else ""
    try:
        got: object = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return cast(dict[str, Any], got) if isinstance(got, dict) else {}


def recipient_add(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A payee and where their money goes. Changed details are a new recipient version (`replaces` the old one), so a
    changed invoice can never quietly redirect a known payee's money: the new version is unknown to the rules. The
    account and routing numbers were sealed into the vault before this runs (`strip_details`, `seal_details`): the
    payload holds their vault ref, a keyed fingerprint and the last 4."""
    V.only(p, ("name", "method", "account_type", "address", "email", "contact_id", "replaces", "money_account_id",
               *SEALED_FIELDS))
    name = V.text(p, "name", 120)
    method = V.choice(p, "method", METHODS)
    address = V.obj(p.get("address") or {}, "address")
    if method == "check" and not address:
        raise invalid("a check needs a mailing address")
    digest = V.text(p, "details_fp", 128)
    last4 = V.opt_text(p, "last4", 4) or "" if method != "check" else ""
    old = None
    if "replaces" in p:
        old = SK.get(ctx.c, "fin_recipients", V.text(p, "replaces", 80))
        if old is None or str(old["status"]) != "active":
            raise FinanceError(NOT_FOUND, "no such active recipient")
        if str(old["details_hash"]) == digest and str(old["method"]) == method:
            raise FinanceError(CONFLICT, "those are the recipient's current details")
    elif any(str(r["details_hash"]) == digest for r in SK.recipients(ctx.c, 5000)):
        raise FinanceError(CONFLICT, "a recipient with those details exists")
    connection = _connection(ctx, V.opt_text(p, "money_account_id", 80))

    def apply(aid: str) -> Result:
        ref = V.opt_text(p, "details_ref", 80) or ""
        if method != "check" and not ref:
            raise invalid("the bank details were not sealed")
        rid = SK.insert(ctx.c, "fin_recipients", {
            "connection_id": connection, "name": name, "method": method, "last4": last4 or None,
            "details_hash": digest, "details_ref": ref or None,
            "version": int(old["version"]) + 1 if old is not None else 1,
            "replaces_id": str(old["id"]) if old is not None else None, "status": "active",
            "contact_id": V.opt_text(p, "contact_id", 80) or name, "created_at": ctx.at, "created_by": who,
            "updated_at": ctx.at})
        if old is not None:
            SK.update(ctx.c, "fin_recipients", str(old["id"]), {"status": "replaced", "updated_at": ctx.at})
        body: dict[str, object] = {"recipient_id": rid, "name": name, "method": method,
                                   "email": V.opt_text(p, "email", 200) or "", "address": address,
                                   "details_ref": ref, "account_type": V.opt_text(p, "account_type", 20)
                                   or "businessChecking"}
        SK.outbox_add(ctx.c, connection_id=connection, action_id=aid, op="recipient_create", payload=body,
                      key=f"recipient-{rid}", by=who, at=ctx.at)
        tico.after_commit(ctx.c, ring)
        return {"recipient_id": rid, "last4": last4 or None}
    return Plan(K.default_gate(ctx.org, who, "recipient_add"),
                f"{'Change' if old is not None else 'Add'} recipient {name} ({method}"
                f"{' ••' + last4 if last4 else ''})", apply,
                facts={"recipient.name": name, "method": method, "recipient.changed": old is not None})


def _connection(ctx: Ctx, money_id: str | None) -> str | None:
    banks = [m for m in SK.money_accounts(ctx.c, ctx.book.id) if str(m["kind"]) == "bank" and m["connection_id"]
             and str(m["status"] or "active") == "active"]
    if money_id:
        banks = [m for m in banks if str(m["id"]) == money_id]
    return str(banks[0]["connection_id"]) if banks else None


# ------------------------------------------------------------------------------------------------ payments
def _bank(ctx: Ctx, money_id: str | None) -> Any:
    banks = [m for m in SK.money_accounts(ctx.c, ctx.book.id) if str(m["kind"]) == "bank" and m["connection_id"]
             and str(m["status"] or "active") == "active"]
    if money_id:
        banks = [m for m in banks if str(m["id"]) == money_id]
    if not banks:
        raise invalid("connect a bank account (Mercury) to pay from first")
    if len(banks) > 1 and not money_id:
        raise invalid("name the account to pay from (money_account_id)")
    return banks[0]


def known(ctx: Ctx, recipient_id: str) -> bool:
    """A recipient version is known once a payment to it has settled (§6.4 rule 1)."""
    return SK.recipient_paid(ctx.c, recipient_id)


def age_minutes(ctx: Ctx, stamp: object) -> int | None:
    if not stamp:
        return None
    then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    now = datetime.fromisoformat(ctx.at.replace("Z", "+00:00"))
    return max(0, int((now - then).total_seconds() // 60))


# The fields a payment_create payload carries; recipient_version is the version an approval was asked for (Plan.resolved).
CREATE_FIELDS = ("recipient_id", "amount", "money_account_id", "memo", "obligation", "account_id", "tags", "mode",
                 "purpose", "recipient_version")


def payment_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Pay a recipient from a bank account through Mercury's approval queue. `account_id` (and tags) is the line the
    payment posts to when it settles; `obligation` (a bill or any key) allows one live payment at a time. `method` internal is a move between two of the company's bank accounts instead
    (transfers.plan_internal)."""
    if V.opt_text(p, "method", 20) == INTERNAL:
        from .transfers import plan_internal     # lazy: transfers imports this module
        return plan_internal(ctx, who, p)
    V.only(p, CREATE_FIELDS)
    mode = V.choice(p, "mode", ("approval_queue", "direct"), "approval_queue")
    if mode == "direct" and SL.setting(ctx.c, DIRECT_KEY) is not True:
        raise FinanceError(NOT_AVAILABLE, "direct send is off for this company")
    rec = SK.get(ctx.c, "fin_recipients", V.text(p, "recipient_id", 80))
    if rec is None:
        raise FinanceError(NOT_FOUND, "no such recipient")
    # An approval being decided follows a replaced recipient to its current version: the decision then sees the new
    # details and asks again (actions.approval_decided). Anyone else is told to pay the current version.
    followed = _current_version(ctx, rec) if ctx.deciding and str(rec["status"]) == "replaced" else None
    if followed is not None:
        rec = followed
    if str(rec["status"]) != "active":
        raise FinanceError(CONFLICT, "that recipient's details changed; pay its current version")
    if mode == "direct" and not known(ctx, str(rec["id"])):
        # §9: direct send is for recipients already paid; a new one waits for Mercury's approval queue (rule 1 asks too).
        raise denied("direct send is only for a recipient that has been paid before")
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("a payment is more than 0")
    bank = _bank(ctx, V.opt_text(p, "money_account_id", 80))
    conf = settings(ctx.c, str(bank["connection_id"]))
    if not conf.get("payments_enabled"):
        raise denied(str(conf.get("approval_note") or "payments are off for this bank connection"))
    obligation = V.opt_text(p, "obligation", 120) or ""
    if obligation and SK.live_payment(ctx.c, obligation) is not None:
        raise FinanceError(CONFLICT, "a payment for that is already live")
    line = ctx.accounts.get(V.text(p, "account_id", 80))
    if line is None or not line.active:
        raise FinanceError(NOT_FOUND, "no such account")
    tags = posting.with_team(ctx, L.tags(p.get("tags")))
    if not may_write(ctx, who, line.id, tags):
        raise denied("that line or those tags are outside your view")
    memo = V.opt_text(p, "memo", 140) or ""
    method = str(rec["method"])
    purpose = _purpose(p, method)
    pid = SL.new_id()
    gate = K.default_gate(ctx.org, who, "payment_create")
    bill = obligation.startswith("bill:")
    if bill:
        # A bill's payment: checked against the bill, and its expense budgets (already spent, never held again).
        from .documents import bill_payment
        budget_gate = bill_payment(ctx, who, obligation, rec, amount, line.id)
        gate = budget_gate if gate.outcome == "allow" else gate

    def apply(aid: str) -> Result:
        hold = budgeting.place(ctx, budget_scope={"account_id": line.id, **tags, "vendor": vendor_of(rec)},
                               amount=amount, currency=ctx.book.base_currency, source="payment", source_id=pid,
                               expires_at=None, by=who) if not bill else None
        if hold is not None and not hold.allowed:
            raise denied(f"it does not fit: {hold.reason}")
        SK.insert(ctx.c, "fin_payments", {
            "id": pid, "book_id": ctx.book.id, "action_id": aid, "idempotency_key": f"pay-{aid}",
            "recipient_id": str(rec["id"]), "recipient_version": int(rec["version"]),
            "money_account_id": str(bank["id"]), "amount": amount, "currency": ctx.book.base_currency,
            "method": method, "memo": memo, "obligation": obligation, "account_id": line.id,
            "tags_json": SK.dump(tags), "mode": mode, "status": "queued",
            "hold_id": hold.hold_id if hold is not None else None,
            "purpose_json": SK.dump(purpose),
            "next_try_at": ctx.at, "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        tico.after_commit(ctx.c, ring)
        return {"payment_id": pid, "status": "queued"}
    return Plan(gate,
                f"Pay {amount / 100:.2f} {ctx.book.base_currency} to {rec['name']} by {method}"
                f"{' for ' + obligation if obligation else ''}", apply,
                facts={**_facts(ctx, rec, amount, line.code, obligation, tags, bank), "mode": mode},
                # The recipient version the approval is for (§5): a changed one voids it (actions.approval_decided).
                resolved={"recipient_version": int(rec["version"]),
                          **({"recipient_id": str(rec["id"])} if followed is not None else {})})


def _current_version(ctx: Ctx, rec: Any) -> Any:
    """The active version that replaced this recipient, following replacements (a change is a new version, §13). None
    when the chain ends without an active one."""
    for _ in range(100):
        if rec is None or str(rec["status"]) != "replaced":
            break
        rec = SK.recipient_replacing(ctx.c, str(rec["id"]))
    return rec if rec is not None and str(rec["status"]) == "active" else None


def moved(body: Mapping[str, object], resolved: Mapping[str, object]) -> bool:
    """Whether the recipient an approval was asked for has been replaced since (its details changed): the approval is
    void, and the payment is asked again for the new details (§5, §6.4 rule 1)."""
    return "recipient_id" in resolved and str(resolved["recipient_id"]) != str(body.get("recipient_id") or "")


def _facts(ctx: Ctx, rec: Any, amount: int, code: str, obligation: str, tags: Mapping[str, str], bank: Any
           ) -> Result:
    """What payment rules match on: at the action, and again right before the send (`recheck`)."""
    return {"amount": amount, "recipient.id": str(rec["id"]), "recipient.known": known(ctx, str(rec["id"])),
            "recipient.name": str(rec["name"]), "counterparty": str(rec["name"]), "method": str(rec["method"]),
            "accounts": [code], "obligation": obligation, **{f"tags.{k}": v for k, v in tags.items()},
            "balance": bank["available_balance"] if bank["available_balance"] is not None else bank["balance"],
            G.AGES: {"balance": age_minutes(ctx, bank["balance_as_of"])}}


# Mercury's requestSendMoney purpose.simple.category values, and what each says about additionalInfo: required for a
# vendor, contractor (their name) or other (a description), optional for a subsidiary, refused for the rest.
PURPOSE_CATEGORIES = ("employee", "landlord", "vendor", "contractor", "subsidiary", "transferToMyExternalAccount",
                      "familyMemberOrFriend", "forGoodsOrServices", "angelInvestment", "savingsOrInvestments",
                      "expenses", "travel", "other")
PURPOSE_NEEDS_INFO = ("vendor", "contractor", "other")
PURPOSE_MAY_INFO = ("subsidiary",)


def _purpose(p: V.Payload, method: str) -> dict[str, str]:
    """A wire's purpose, which Mercury requires, checked as Mercury checks it before it is queued: a known category,
    and additional_info exactly where that category takes it. Other methods carry none."""
    if method != "wire":
        if "purpose" in p:
            raise invalid("only a wire takes a purpose")
        return {}
    raw = V.obj(p.get("purpose") or {}, "purpose")
    V.only(raw, ("category", "additional_info"))
    category = V.opt_text(raw, "category", 80) or ""
    if not category:
        raise invalid("a wire needs a purpose: {category, additional_info}")
    if category not in PURPOSE_CATEGORIES:
        raise invalid(f"a wire's purpose category is one of {', '.join(PURPOSE_CATEGORIES)}")
    info = V.opt_text(raw, "additional_info", 140) or ""
    if category in PURPOSE_NEEDS_INFO and not info:
        raise invalid(f"a {category} wire needs additional_info ("
                      f"{'a description' if category == 'other' else 'their name'})")
    if info and category not in PURPOSE_NEEDS_INFO + PURPOSE_MAY_INFO:
        raise invalid(f"a {category} wire takes no additional_info")
    return {"category": category, **({"additional_info": info} if info else {})}


def vendor_of(rec: Any) -> str:
    """The vendor a payment to this recipient holds and settles under: one key for both."""
    return str(rec["contact_id"] or rec["name"] or "")


def ring() -> None:
    """Wake the bank worker. Queued with `tico.after_commit`, so it reads the rows this transaction wrote (rung before
    the commit, it finds nothing and sleeps until its next sweep), and an undone action drops it."""
    from . import sync
    sync.ring()


def _ask_mercury(ctx: Ctx, who: str, pay: Any) -> Plan:
    """A finance admin's cancel of an unknown payment (flagged: its key was not found for UNKNOWN_ASK_MINUTES). Nothing is
    cancelled here, since the planner cannot call Mercury: the worker looks Mercury up by the key (sync.pay), and the
    payment ends cancelled only when a complete lookup begun after this ask finds no request with the key (not_found). A
    request with the key that turns up instead keeps it going, and it is never sent again under a fresh key (§9)."""
    if pay["cancel_asked_at"]:
        raise FinanceError(CONFLICT, "Finance is already looking for it in Mercury")

    def apply(aid: str) -> Result:
        if SK.update(ctx.c, "fin_payments", str(pay["id"]), {"cancel_asked_at": ctx.at, "cancel_asked_by": who,
                                                             "next_try_at": ctx.at, "updated_at": ctx.at},
                     "status=? AND cancel_asked_at IS NULL", ("unknown",)) != 1:
            raise FinanceError(CONFLICT, "the payment moved on; check its status")
        return {"payment_id": str(pay["id"]), "status": "unknown", "cancel": "asked"}
    return Plan(K.default_gate(ctx.org, who, "payment_cancel"),
                f"Ask Mercury about the {int(pay['amount']) / 100:.2f} payment before cancelling it", apply)


def payment_cancel(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Cancel a payment Finance has not sent yet. Once it is in Mercury's queue there is no API to cancel it: a person
    rejects it in Mercury, which ends the payment here when the worker sees it."""
    if "transfer_id" in p:
        from .transfers import plan_cancel      # lazy: transfers imports this module
        return plan_cancel(ctx, who, p)
    V.only(p, ("payment_id",))
    pay = SK.get(ctx.c, "fin_payments", V.text(p, "payment_id", 80))
    if pay is None or (str(pay["created_by"]) != who and not may_write(ctx, who, str(pay["account_id"]),
                                                                       SK.loads(pay["tags_json"]))):
        raise FinanceError(NOT_FOUND, "no such payment")
    status = str(pay["status"])
    if status in ("awaiting_bank_approval", "submitted"):
        if str(pay["mode"]) == "direct":
            raise FinanceError(CONFLICT, "it was sent to Mercury directly: it cannot be cancelled here")
        raise FinanceError(CONFLICT, "it is already in Mercury: reject it in Mercury")
    lost = status == "unknown" and bool(pay["flagged_at"]) and R.is_admin(ctx.org, who, ctx.rights(who))
    if status == "unknown" and not lost:
        raise FinanceError(CONFLICT, "Finance is still finding out whether Mercury has it; try again shortly")
    if status == "sending":
        raise FinanceError(CONFLICT, "it is being sent to Mercury now; reject it in Mercury if it gets there")
    if status != "queued" and not lost:
        raise FinanceError(CONFLICT, f"that payment is {status}")
    if lost:
        return _ask_mercury(ctx, who, pay)

    def apply(aid: str) -> Result:
        if SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "cancelled", "updated_at": ctx.at},
                     "status=?", (status,)) != 1:
            raise FinanceError(CONFLICT, "the payment moved on; check its status")
        if pay["hold_id"]:
            budgeting.release(ctx, str(pay["hold_id"]))
        unreserve(ctx, pay)
        from .documents import payment_ended
        payment_ended(ctx, pay)                    # a bill it was paying is open again
        return {"payment_id": str(pay["id"]), "status": "cancelled"}
    return Plan(K.default_gate(ctx.org, who, "payment_cancel"), f"Cancel the {int(pay['amount']) / 100:.2f} payment",
                apply)


def payment_settings(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """The owner's company switches: direct send (§9: payments may then go straight to Mercury, for recipients already
    paid, past the same gate rules; off by default) and the cash alert (finance.cash.below fires when the books' cash
    drops under `cash_below`; 0 turns it off)."""
    V.only(p, ("direct_send", "cash_below"))
    if not p:
        raise invalid("say direct_send or cash_below")
    on = V.flag(p, "direct_send") if "direct_send" in p else SL.setting(ctx.c, DIRECT_KEY) is True
    floor = V.integer(p, "cash_below", 0, 10**15) if "cash_below" in p else None

    def apply(aid: str) -> Result:
        out: Result = {"direct_send": on, "available": True}
        if "direct_send" in p:
            SL.set_setting(ctx.c, DIRECT_KEY, on, who, ctx.at)
        if floor is not None:
            SL.set_setting(ctx.c, CASH_BELOW_KEY, floor, who, ctx.at)
            out["cash_below"] = floor
        return out
    words = [f"turn direct send {'on' if on else 'off'}"] if "direct_send" in p else []
    if floor is not None:
        words.append(f"alert when cash drops under {floor / 100:.2f}" if floor else "turn the cash alert off")
    return Plan(K.ALLOW, f"{' and '.join(words).capitalize()} for the company", apply)


# ------------------------------------------------------------------------------------------------ worker steps
def _later(ctx: Ctx, attempts: int) -> str:
    wait = RETRY[min(attempts, len(RETRY) - 1)]
    then = datetime.fromisoformat(ctx.at.replace("Z", "+00:00")) + timedelta(seconds=wait)
    return then.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def end(ctx: Ctx, pay: Any, status: str, why: str = "") -> None:
    """A payment that will not settle: its hold is released and the failure is an event. A payment Mercury refused
    (`failed`) also goes to whoever made it as a Needs-you item, so a refusal (a wire without a purpose) is never
    silent."""
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": status, "last_error": why[:300] or None,
                                                      "next_try_at": None, "updated_at": ctx.at})
    if pay["hold_id"]:
        budgeting.release(ctx, str(pay["hold_id"]))
    if status in ("failed", "cancelled"):
        unreserve(ctx, pay)
    _event(ctx, FAILED, pay, why or status)
    from .documents import payment_ended
    payment_ended(ctx, pay)                        # a bill it paid is owed again
    if status == "failed":
        maker = str(pay["created_by"])
        person = maker if maker in ctx.org.humans else admin_person(ctx)
        tico.needs_you(ctx.c, person, f"Payment of {int(pay['amount']) / 100:.2f} failed",
                       f"Mercury refused payment {pay['id']}: {why[:300]}. Fix it and pay again.", private=True)


def admin_person(ctx: Ctx) -> str:
    """The finance admin who gets Finance's Needs-you items (the company's approver setting, else the owner)."""
    return O.approver(ctx.org, "finance_admin", SYSTEM, ctx.approver_setting()) or ctx.org.owner


def _hold(ctx: Ctx, pay: Any, why: str) -> str:
    """Keep a payment queued and ask: a finance rule or its budget now wants a person to look before it goes."""
    first = str(pay["last_error"] or "") != why[:300]
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "queued", "last_error": why[:300],
                                                      "next_try_at": _later(ctx, len(RETRY)), "updated_at": ctx.at})
    if first:
        tico.needs_you(ctx.c, admin_person(ctx), f"Payment of {int(pay['amount']) / 100:.2f} is waiting",
                       f"Payment {pay['id']} was not sent: {why}. Cancel it, or fix what stops it and it goes on its "
                       "own.", private=True)
    return "wait"


def own_counts(ctx: Ctx, counted_json: object, action_id: object) -> set[tuple[str, str, str]]:
    """The rule counters this payment already moved: its own action's, and those reserved when it was claimed to send
    (so a recheck or a retried send never counts it twice)."""
    out: set[tuple[str, str, str]] = set()
    for raw in SK.loads_list(counted_json):
        if isinstance(raw, list) and len(cast(list[Any], raw)) == 3:
            rule, key, subject = cast(list[Any], raw)
            out.add((str(rule), str(key), str(subject)))
    run = SK.last_rule_run(ctx.c, str(action_id or ""))
    if run is None:
        return out
    hits = cast(list[Any], SK.loads(run["outcome_json"]).get("hits") or [])
    for raw in hits:
        h = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        if h.get("outcome") == "limit" and h.get("period_key"):
            out.add((str(h.get("rule")), str(h.get("period_key")), str(h.get("subject") or "")))
    return out


def unreserve(ctx: Ctx, pay: Any) -> None:
    """A payment that failed or was cancelled gives back the counters it reserved when it was claimed to send (only
    those: its action's own count stays, as any refused action's does), once: the record empties."""
    owned = SK.loads_list(pay["counted_json"])
    if not owned:
        return
    for raw in owned:
        if isinstance(raw, list) and len(cast(list[Any], raw)) == 3:
            rule, key, subject = cast(list[Any], raw)
            SK.uncount(ctx.c, str(rule), str(key), str(subject), int(pay["amount"]), ctx.at)
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"counted_json": "[]"})


def reserve(ctx: Ctx, pay: Any, hits: Sequence[G.Hit]) -> None:
    """Move the counters of limits that apply now but did not when the payment's action committed (a rule added
    since, a new period), in the transaction that claims it to send; the payment owns them, so they move once."""
    if not hits:
        return
    owned = [list(x) for x in SK.loads_list(pay["counted_json"])]
    for h in hits:
        SK.count(ctx.c, h.rule, h.period_key, h.subject, int(pay["amount"]), ctx.at)
        owned.append([h.rule, h.period_key, h.subject])
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"counted_json": SK.dump(owned)})


def decided_by(ctx: Ctx, action: Any) -> str:
    """Who approved the payment's action: the decider of its Tico approval when that was approved ("" when it had none).
    The action's own `approval_by` is the requester, never the decider."""
    approval = str(action["approval_id"] or "") if action is not None else ""
    appr = tico.approval(ctx.c, approval) if approval else None
    return str(appr.get("decided_by") or "") if appr is not None and appr.get("decision") == "approved" else ""


def approved_for(ctx: Ctx, action: Any, who: str, who_needed: str, rule: str) -> bool:
    """Whether what a gate asks for now was already given: the payer may approve it themselves now (as at the action),
    or the payment's action was approved for this very gate (its rule: an approval for one rule or budget never covers
    another) by someone who may approve it."""
    if O.approver(ctx.org, who_needed, who, ctx.approver_setting()) == who:
        return True
    by = decided_by(ctx, action)
    if not by or not rule or str(action["rule_id"] or "") != rule:
        return False
    if ctx.org.owner and by == ctx.org.owner:
        return True
    if who_needed == "owner":
        return False
    return who_needed == "manager" or R.is_admin(ctx.org, by, ctx.rights(by))


def _asked_again(ctx: Ctx, pay: Any, action: Any, successor: Any) -> str:
    """A queued payment whose recipient was replaced: its approval is void (§5), so it is not cancelled quietly. It ends
    cancelled (its hold and counters released, the bill owed again), and its action waits for a finance admin's approval
    of the new details (§6.4 rule 1), with the new recipient version. Nothing goes to Mercury meanwhile."""
    from . import actions as FA          # lazy: actions imports this module
    why = "the recipient's details changed after it was approved"
    body = {k: v for k, v in json.loads(str(action["payload_json"])).items() if k in CREATE_FIELDS}
    body.update({"recipient_id": str(successor["id"]), "recipient_version": int(successor["version"])})
    end(ctx, pay, "cancelled", f"{why}; asked again")
    summary = (f"Pay {int(pay['amount']) / 100:.2f} {ctx.book.base_currency} to {successor['name']} "
               "(its details changed; asked again)")
    FA.void_for_recipient(ctx, str(action["id"]), "payment_create", body, summary,
                          O.Principal(actor=str(action["actor"]), for_actor=str(action["on_behalf_of"] or ""),
                                      rule=str(action["rule_id"] or "")))
    return why


def recheck(ctx: Ctx, pay: Any, fresh: list[G.Hit] | None = None) -> str:
    """Right before the provider call, the same judgment as at the action (§9): the payer still has the right and the
    line in view, the recipient is still that version, payments are on, the gate rules (with their counters, less this
    payment's own, and their inputs' ages) and the budget still let it through. "" to go ahead; "wait" when it stays
    queued (the recipient is not at Mercury yet, or a person must look: it is held and asked about); else why it was
    cancelled (a deny or a declined budget ends it and releases its hold). On "" the limits it fits that it has not
    counted yet are added to `fresh`, for `reserve` once the send is claimed."""
    from . import gating
    rec = SK.get(ctx.c, "fin_recipients", str(pay["recipient_id"]))
    money = SK.get(ctx.c, "fin_money_accounts", str(pay["money_account_id"]))
    action = SX.by_id(ctx.c, str(pay["action_id"] or ""))
    who = (str(action["on_behalf_of"] or "") or str(action["actor"])) if action is not None else str(pay["created_by"])
    tags = cast(dict[str, str], SK.loads(pay["tags_json"]))
    line = ctx.accounts.get(str(pay["account_id"]))
    why = ""
    if str(pay["mode"]) == "direct" and SL.setting(ctx.c, DIRECT_KEY) is not True:
        # The company turned direct send off since the action: the payment takes the approval-queue route like any
        # other, so the send below is a request, never a direct call (the switch is read here, in the claim).
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {"mode": "approval_queue", "updated_at": ctx.at})
        pay = SK.get(ctx.c, "fin_payments", str(pay["id"]))
    right = K.authority(ctx.org, who, "payment_create", ctx.rights(who))
    ended = "cancelled"
    if right is not None and right.outcome == "deny":
        why = "whoever asked for it no longer has the right to pay"
    elif line is None or not line.active or not may_write(ctx, who, line.id, tags):
        why = "its line is no longer in the payer's view"
    elif rec is None or str(rec["status"]) != "active" or int(rec["version"]) != int(pay["recipient_version"]):
        # Changed details (§13) void the approval: asked again for the new version, never cancelled quietly.
        successor = _current_version(ctx, rec) if rec is not None else None
        if successor is not None and action is not None and str(successor["id"]) != str(pay["recipient_id"]):
            return _asked_again(ctx, pay, action, successor)
        why, ended = "the recipient is no longer active", "failed"       # a failure is told to whoever made it
    elif not rec["provider_recipient_id"]:
        return "wait"
    elif money is None or not settings(ctx.c, str(money["connection_id"])).get("payments_enabled"):
        why = "payments were turned off for this bank connection"
    if why:
        end(ctx, pay, ended, why)
        return why
    assert line is not None and rec is not None and money is not None
    amount = int(pay["amount"])
    # The same facts create gave the action, mode included (the mode the send now uses, after any switch above).
    found = gating.send_facts(ctx, who, "payment_create",
                              {**_facts(ctx, rec, amount, line.code, str(pay["obligation"] or ""), tags, money),
                               "mode": str(pay["mode"])})
    mine = own_counts(ctx, pay["counted_json"], pay["action_id"])

    def used(rule: G.Rule, key: str, subject: str) -> int:
        have = SK.counter(ctx.c, rule.id, key, subject)[0]
        return have - amount if (rule.id, key, subject) in mine else have
    verdict = G.evaluate(gating.rules(ctx), found, datetime.fromisoformat(ctx.today).date(), used)
    if verdict.outcome == "deny":
        why = verdict.reason or "a finance rule now refuses it"
        end(ctx, pay, "cancelled", why)
        return why
    if verdict.outcome == "needs_approval" and (verdict.stale or
                                                not approved_for(ctx, action, who, verdict.who, verdict.rule)):
        return _hold(ctx, pay, f"{verdict.reason or 'a finance rule asks for approval'}"
                               f"{' (its data is stale)' if verdict.stale else ''}")
    hold = SB.hold(ctx.c, str(pay["hold_id"])) if pay["hold_id"] else None
    if hold is not None and str(hold["status"]) == "held":
        from .core import budgets as B
        from .core import dates
        at = dates.day(ctx.today)
        for b in B.chain(ctx.budgets(), str(hold["budget_id"])):
            s = budgeting.state(ctx, b.id, at)       # its own hold is already in `held`: counted once
            if s.amount is None or s.remaining is None or s.remaining >= 0 or b.at_limit == "warn":
                continue
            if b.at_limit == "decline":
                why = f"it no longer fits the {b.name} budget"
                end(ctx, pay, "cancelled", why)
                return why
            return _hold(ctx, pay, f"the {b.name} budget is over")
    elif pay["hold_id"]:
        why = "its budget hold is gone"
        end(ctx, pay, "cancelled", why)
        return why
    elif str(pay["obligation"] or "").startswith("bill:"):
        # A bill's payment holds no budget (the bill spent it): its bill's budgets are judged again as at the action,
        # so one lowered past its actuals since asks a person first (never refused: the bill is owed either way).
        from .documents import bill_budget, payment_waits
        bill = SAP.get(ctx.c, "fin_bills", str(pay["obligation"]).removeprefix("bill:"))
        if bill is not None and payment_waits(ctx, bill):
            return "wait"               # a purchase order's bill is paid once its budget fits (never held for a person)
        asks = bill_budget(ctx, str(pay["obligation"]).removeprefix("bill:"))
        # Approved for this budget at the action: it goes, even if that budget dropped further since (the person
        # approved paying this bill past it). A different budget now over asks again, unless the payer may approve.
        if asks.outcome == "needs_approval" and not approved_for(ctx, action, who, asks.who, asks.rule):
            budget = ctx.budgets().get(asks.rule.removeprefix("budget:"))
            return _hold(ctx, pay, f"the {budget.name if budget else 'bill'} budget is over")
    if fresh is not None:
        fresh.extend(h for h in verdict.counts if (h.rule, h.period_key, h.subject) not in mine)
    return ""


def request_body(pay: Any, rec: Any) -> dict[str, object]:
    """The body for a payment's send: request-send-money (approval queue), or the same fields to a direct transaction.
    The key goes in `note` and leads `externalMemo` too: Mercury's list of requests returns a memo (undocumented which
    field it echoes), not the idempotency key, so an unknown request is found by its key in what comes back, its
    recipient and its amount."""
    key = f"{NOTE}{pay['idempotency_key']}"
    memo = str(pay["memo"] or "")
    body: dict[str, object] = {
        "recipientId": str(rec["provider_recipient_id"]), "amount": dollars(int(pay["amount"])),
        "paymentMethod": MERCURY_METHOD[str(pay["method"])], "idempotencyKey": str(pay["idempotency_key"]),
        "note": key, "externalMemo": f"{key} {memo}".strip()[:140]}
    purpose = cast(dict[str, str], SK.loads(pay["purpose_json"]))
    if purpose.get("category"):
        body["purpose"] = {"simple": {"category": purpose["category"],
                                      **({"additionalInfo": purpose["additional_info"]}
                                         if purpose.get("additional_info") else {})}}
    return body


def _accepted(ctx: Ctx, pay: Any, answer: Answer, attempts: int) -> None:
    if str(pay["mode"]) == "direct":
        # A direct send is Mercury's transaction at once (no approver): submitted, and settled when its bank row posts.
        # Its id is kept where an approval request's id goes, and the bank row matches it (banking._settle).
        gone = str(answer.body.get("status") or "").lower() in ("failed", "blocked", "cancelled")
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {
            "status": "submitted", "provider_request_id": str(answer.body.get("id") or answer.body.get("requestId") or ""),
            "attempts": attempts, "last_error": None, "unknown_since": None, "next_try_at": _later(ctx, 1),
            "updated_at": ctx.at})
        if gone:
            end(ctx, pay, "failed", f"Mercury refused the direct send ({answer.body.get('status')})")
        return
    status = F.request_status(answer.body.get("status")) or "awaiting_bank_approval"
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {
        "status": status, "provider_request_id": str(answer.body.get("requestId") or answer.body.get("id") or ""),
        "attempts": attempts, "last_error": None, "unknown_since": None, "next_try_at": _later(ctx, 1),
        "updated_at": ctx.at})
    if status == "cancelled":
        end(ctx, pay, "cancelled", "rejected in Mercury")


def unknown(ctx: Ctx, pay: Any, why: str, attempts: int | None = None) -> None:
    """It may have reached Mercury: never sent again; found by its key, or after a while a finance admin checks."""
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {
        "status": "unknown", "attempts": int(pay["attempts"]) + 1 if attempts is None else attempts,
        "last_error": why[:300], "unknown_since": pay["unknown_since"] or ctx.at,
        "next_try_at": _later(ctx, 1), "updated_at": ctx.at})


def sent(ctx: Ctx, pay: Any, answer: Answer) -> None:
    """What a request-send-money call came back with. A 409 that carries the request is the same request, accepted. A
    timeout, a 5xx or a bare 409 may have landed: `unknown`, hold kept, never sent again. 401/403 is the IP allowlist
    and 429 a busy Mercury: it goes back to the queue. Any other refusal fails it (and says so to its maker)."""
    attempts = int(pay["attempts"]) + 1
    if answer.ok or (answer.status == 409 and (answer.body.get("requestId") or answer.body.get("id"))):
        _accepted(ctx, pay, answer, attempts)
        return
    if answer.status is None or answer.status >= 500 or answer.status == 409:
        unknown(ctx, pay, error_text(answer, True), attempts)
        return
    if answer.refused_write or answer.status == 429:
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {
            "status": "queued", "attempts": attempts, "last_error": IP_HINT if answer.refused_write else
            error_text(answer, True), "next_try_at": _later(ctx, attempts), "updated_at": ctx.at})
        return
    why = error_text(answer, True)
    if answer.status == 400 and "purpose" in why.lower():
        # A fixable refusal: it stays queued with its hold, and a finance admin is asked.
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {"attempts": attempts})
        _hold(ctx, pay, f"Mercury refused its wire purpose ({why})")
        return
    end(ctx, pay, "failed", why)


def polled(ctx: Ctx, pay: Any, answer: Answer) -> None:
    """A request's status from Mercury. Rejected or cancelled in Mercury ends it and releases its hold; approved
    becomes `submitted` until its bank transaction posts (finance/banking.py settles it)."""
    if str(pay["status"]) in ("settled", "failed", "cancelled", "returned"):
        return                      # its debit already settled it (or it ended) in this sweep: nothing to move back
    if not answer.ok:
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {"next_try_at": _later(ctx, int(pay["attempts"]) + 1),
                                                          "attempts": int(pay["attempts"]) + 1, "updated_at": ctx.at})
        return
    status = F.request_status(answer.body.get("status"))
    if status == "cancelled":
        end(ctx, pay, "cancelled", f"{answer.body.get('status')} in Mercury")
        return
    fields: dict[str, object] = {"next_try_at": _later(ctx, 1), "updated_at": ctx.at}
    if status and status != str(pay["status"]):
        fields["status"] = status
    SK.update(ctx.c, "fin_payments", str(pay["id"]), fields)


KEY_FIELDS = ("memo", "note", "externalMemo")     # the fields Mercury echoes a request's or a transfer's note in


def keyed(key: str, row: Mapping[str, object], amount: int) -> bool:
    """Whether one of Mercury's rows is the instruction with this idempotency key: the key (NOTE + key) in a field that
    comes back, and this amount (Mercury's sign aside). Mercury does not return the idempotency key itself. Shared by
    payments (requests) and transfers (transactions)."""
    if not any(f"{NOTE}{key}" in str(row.get(f) or "") for f in KEY_FIELDS):
        return False
    try:
        return abs(F.cents(row.get("amount"))) == amount
    except FinanceError:
        return False


def matches(pay: Any, rec: Any, r: Mapping[str, object]) -> bool:
    """Whether one of Mercury's requests is this payment: keyed, and to the same recipient."""
    if rec is None or str(r.get("recipientId") or "") != str(rec["provider_recipient_id"] or ""):
        return False
    return keyed(str(pay["idempotency_key"]), r, int(pay["amount"]))


def found(ctx: Ctx, pay: Any, requests: Sequence[Mapping[str, object]]) -> bool:
    """Resolve an `unknown` payment from Mercury's requests (`matches`). False when none is it."""
    rec = SK.get(ctx.c, "fin_recipients", str(pay["recipient_id"]))
    mine = next((r for r in requests if matches(pay, rec, r)), None)
    if mine is None:
        return False
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "awaiting_bank_approval", "updated_at": ctx.at,
                                                      "unknown_since": None, "last_error": None,
                                                      "cancel_asked_at": None, "cancel_asked_by": "",
                                                      "provider_request_id": str(mine.get("requestId") or mine.get("id")
                                                                                 or "")})
    polled(ctx, SK.get(ctx.c, "fin_payments", str(pay["id"])), Answer(200, dict(mine)))
    return True


def not_found(ctx: Ctx, pay: Any) -> None:
    """Mercury's requests did not show it. It stays `unknown` with its hold; once it has been looked for over
    UNKNOWN_ASK_MINUTES, one Needs-you goes to a finance admin to check Mercury (and cancel it here if it is not
    there)."""
    fields: dict[str, object] = {"next_try_at": _later(ctx, 2), "updated_at": ctx.at}
    asked = str(pay["cancel_asked_at"] or "")
    if asked and asked <= ctx.at:
        # A finance admin asked to cancel it before this sweep's complete lookup by its key began, and it found nothing.
        end(ctx, pay, "cancelled", f"not in Mercury when its key was looked for ({pay['cancel_asked_by']} cancelled it)")
        return
    if asked:
        fields["next_try_at"] = ctx.at          # asked during this sweep: the next one looks again, soon
    since = str(pay["unknown_since"] or ctx.at)
    age = age_minutes(ctx, since) or 0
    if age >= UNKNOWN_ASK_MINUTES and not pay["flagged_at"]:
        fields["flagged_at"] = ctx.at
        tico.needs_you(ctx.c, admin_person(ctx), f"Check Mercury for a {int(pay['amount']) / 100:.2f} payment",
                       f"Payment {pay['id']} may or may not have reached Mercury (no answer since {since}). Look for "
                       f"a request or transaction with note {NOTE}{pay['idempotency_key']}. If it is not there, cancel the payment "
                       "in Finance; Finance will not send it again on its own.", private=True)
    SK.update(ctx.c, "fin_payments", str(pay["id"]), fields)


def cut_off(ctx: Ctx, pay: Any) -> None:
    """A payment left `sending` by a worker that stopped mid-call: it may have landed, so it is unknown."""
    unknown(ctx, pay, "the send was cut off; finding out whether Mercury has it", int(pay["attempts"]))


def feed_settled(ctx: Ctx, pay: Any, feed_id: str) -> None:
    """Its bank transaction posted: settled, the entry absorbed the hold, and the bill it pays (if any) is settled."""
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "settled", "provider_txn_id": feed_id,
                                                      "settled_at": ctx.at, "next_try_at": None, "updated_at": ctx.at})
    _event(ctx, SENT, pay)
    from .documents import payment_settled
    payment_settled(ctx, pay, feed_id)             # a bill it pays is settled on the bank row's day


def feed_failed(ctx: Ctx, request_id: str, status: str) -> None:
    pay = SK.payment_by_request(ctx.c, request_id)
    if pay is not None and str(pay["status"]) not in ("returned", "failed", "cancelled"):
        if str(pay["status"]) == "settled" and pay["hold_id"]:
            SK.update(ctx.c, "fin_payments", str(pay["id"]), {"hold_id": None})
            pay = SK.get(ctx.c, "fin_payments", str(pay["id"]))
        end(ctx, pay, status, f"the bank {status} it")


PLANNERS = {"recipient_add": recipient_add, "payment_create": payment_create, "payment_cancel": payment_cancel,
            "payment_settings": payment_settings}

__all__ = ["PLANNERS", "recheck", "own_counts", "approved_for", "not_found", "cut_off", "unknown", "sent", "polled", "found", "feed_settled", "feed_failed", "request_body", "end"]
