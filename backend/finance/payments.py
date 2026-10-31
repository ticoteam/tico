"""Recipients and payments (spec §9). A payment is one immutable instruction (recipient version, amount, money account,
method, the line it posts to) with one idempotency key for its whole life, sent through Mercury's approval queue so a
person approves in Mercury too. Lifecycle: queued → awaiting_bank_approval → submitted → settled | returned | failed |
cancelled, plus unknown when a call timed out after it may have landed. One live payment per obligation; a live
payment keeps its hold until it settles or ends. Direct send is not built: only the owner's switch and its refusal.

The planners run inside `act`; the worker steps (`due`, `precheck`, `sent`, `polled`) are what finance/sync.py runs
around its provider calls, each in its own short transaction."""
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import budgeting, posting
from .context import Ctx
from .core import feeds as F
from .core import gates as G
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Result, may_write
from .mercury import IP_HINT, Answer, dollars, error_text
from .store import banking as SK
from .store import ledger as SL
from .store import tico

DIRECT_KEY = "finance_direct_send"     # the owner's company switch (§9 mode 2); v1 sends nothing directly either way
METHODS = ("ach", "wire", "check")
MERCURY_METHOD = {"ach": "ach", "wire": "domesticWire", "check": "check"}
SENT, FAILED = "finance.payment.sent", "finance.payment.failed"
RETRY = (60, 300, 1800, 3600)
NOTE = "tico:"                          # every request's note carries its key, so an unknown one can be found again


def _event(ctx: Ctx, name: str, pay: Any, why: str = "") -> None:
    from .banking import event
    event(ctx, name, str(pay["id"]), {"payment_id": str(pay["id"]), **({"why": why} if why else {})})


def settings(c: Any, connection_id: str) -> dict[str, Any]:
    row = SK.get(c, "fin_connections", connection_id)
    return SK.loads(row["settings_json"]) if row is not None else {}


# ------------------------------------------------------------------------------------------------ recipients
SEALED_KINDS = ("recipient_add",)
BANK_FIELDS = ("account_number", "routing_number")
SEALED_FIELDS = ("details_ref", "details_fp", "last4")     # what Finance keeps in their place; never sent by a caller


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


def seal_details(c: Any, body: Mapping[str, object], plain: str, by: str, at: str) -> dict[str, object]:
    """Seal stripped bank details into the vault (inside the action's savepoint, so a refusal rolls it back too)."""
    if not plain:
        return dict(body)
    last4 = str(body.get("last4") or "")
    ref = tico.seal_credential(c, f"Finance payee {str(body.get('name') or '')[:100]} ••{last4}", plain,
                               f"••{last4}", by, at)
    return {**body, "details_ref": ref}


def drop_details(c: Any, body: Mapping[str, object]) -> None:
    """An action that will never run deletes the bank details it sealed."""
    ref = body.get("details_ref")
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
        return {"recipient_id": rid, "last4": last4 or None}
    return Plan(K.default_gate(ctx.org, who, "recipient_add"),
                f"{'Change' if old is not None else 'Add'} recipient {name} ({method}"
                f"{' ••' + last4 if last4 else ''})", apply,
                facts={"recipient.name": name, "method": method, "recipient.changed": old is not None})


def _connection(ctx: Ctx, money_id: str | None) -> str | None:
    banks = [m for m in SK.money_accounts(ctx.c, ctx.book.id) if str(m["kind"]) == "bank" and m["connection_id"]]
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


def _age(ctx: Ctx, stamp: object) -> int | None:
    if not stamp:
        return None
    then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    now = datetime.fromisoformat(ctx.at.replace("Z", "+00:00"))
    return max(0, int((now - then).total_seconds() // 60))


def payment_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Pay a recipient from a bank account through Mercury's approval queue. `account_id` (and tags) is the line the
    payment posts to when it settles; `obligation` (a bill or any key) allows one live payment at a time."""
    V.only(p, ("recipient_id", "amount", "money_account_id", "memo", "obligation", "account_id", "tags", "mode"))
    if V.choice(p, "mode", ("approval_queue", "direct"), "approval_queue") == "direct":
        on = SL.setting(ctx.c, DIRECT_KEY) is True
        raise FinanceError(NOT_AVAILABLE, "direct send is not available in this version; payments go through "
                           "Mercury's approval queue" if on else "direct send is off for this company")
    rec = SK.get(ctx.c, "fin_recipients", V.text(p, "recipient_id", 80))
    if rec is None:
        raise FinanceError(NOT_FOUND, "no such recipient")
    if str(rec["status"]) != "active":
        raise FinanceError(CONFLICT, "that recipient's details changed; pay its current version")
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
    pid = SL.new_id()

    def apply(aid: str) -> Result:
        hold = budgeting.place(ctx, budget_scope={"account_id": line.id, **tags, "vendor": str(rec["contact_id"])},
                               amount=amount, currency=ctx.book.base_currency, source="payment", source_id=pid,
                               expires_at=None, by=who)
        if not hold.allowed:
            raise denied(f"it does not fit: {hold.reason}")
        SK.insert(ctx.c, "fin_payments", {
            "id": pid, "book_id": ctx.book.id, "action_id": aid, "idempotency_key": f"pay-{aid}",
            "recipient_id": str(rec["id"]), "recipient_version": int(rec["version"]),
            "money_account_id": str(bank["id"]), "amount": amount, "currency": ctx.book.base_currency,
            "method": method, "memo": memo, "obligation": obligation, "account_id": line.id,
            "tags_json": SK.dump(tags), "mode": "approval_queue", "status": "queued", "hold_id": hold.hold_id,
            "next_try_at": ctx.at, "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        ring()
        return {"payment_id": pid, "status": "queued"}
    facts: Result = {"amount": amount, "recipient.id": str(rec["id"]), "recipient.known": known(ctx, str(rec["id"])),
                     "recipient.name": str(rec["name"]), "counterparty": str(rec["name"]), "method": method,
                     "accounts": [line.code], "obligation": obligation, **{f"tags.{k}": v for k, v in tags.items()},
                     "balance": bank["available_balance"] if bank["available_balance"] is not None else bank["balance"],
                     G.AGES: {"balance": _age(ctx, bank["balance_as_of"])}}
    return Plan(K.default_gate(ctx.org, who, "payment_create"),
                f"Pay {amount / 100:.2f} {ctx.book.base_currency} to {rec['name']} by {method}"
                f"{' for ' + obligation if obligation else ''}", apply, facts=facts)


def ring() -> None:
    from . import sync
    sync.ring()


def payment_cancel(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Cancel a payment Finance has not sent yet. Once it is in Mercury's queue there is no API to cancel it: a person
    rejects it in Mercury, which ends the payment here when the worker sees it."""
    V.only(p, ("payment_id",))
    pay = SK.get(ctx.c, "fin_payments", V.text(p, "payment_id", 80))
    if pay is None or (str(pay["created_by"]) != who and not may_write(ctx, who, str(pay["account_id"]),
                                                                       SK.loads(pay["tags_json"]))):
        raise FinanceError(NOT_FOUND, "no such payment")
    status = str(pay["status"])
    if status in ("awaiting_bank_approval", "submitted"):
        raise FinanceError(CONFLICT, "it is already in Mercury: reject it in Mercury")
    if status == "unknown":
        raise FinanceError(CONFLICT, "Finance is still finding out whether Mercury has it; try again shortly")
    if status != "queued":
        raise FinanceError(CONFLICT, f"that payment is {status}")

    def apply(aid: str) -> Result:
        if SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "cancelled", "updated_at": ctx.at},
                     "status='queued'") != 1:
            raise FinanceError(CONFLICT, "the payment moved on; check its status")
        if pay["hold_id"]:
            budgeting.release(ctx, str(pay["hold_id"]))
        return {"payment_id": str(pay["id"]), "status": "cancelled"}
    return Plan(K.default_gate(ctx.org, who, "payment_cancel"), f"Cancel the {int(pay['amount']) / 100:.2f} payment",
                apply)


def payment_settings(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """The owner's company switch for direct send (§9). v1 builds the switch only: direct payments stay refused."""
    V.only(p, ("direct_send",))
    on = V.flag(p, "direct_send")

    def apply(aid: str) -> Result:
        SL.set_setting(ctx.c, DIRECT_KEY, on, who, ctx.at)
        return {"direct_send": on, "available": False}
    return Plan(K.ALLOW, f"Turn direct send {'on' if on else 'off'} for the company", apply)


# ------------------------------------------------------------------------------------------------ worker steps
def _later(ctx: Ctx, attempts: int) -> str:
    wait = RETRY[min(attempts, len(RETRY) - 1)]
    then = datetime.fromisoformat(ctx.at.replace("Z", "+00:00")) + timedelta(seconds=wait)
    return then.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def end(ctx: Ctx, pay: Any, status: str, why: str = "") -> None:
    """A payment that will not settle: its hold is released and the failure is an event."""
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": status, "last_error": why[:300] or None,
                                                      "next_try_at": None, "updated_at": ctx.at})
    if pay["hold_id"]:
        budgeting.release(ctx, str(pay["hold_id"]))
    _event(ctx, FAILED, pay, why or status)


def precheck(ctx: Ctx, pay: Any) -> str:
    """Right before the provider call: the recipient is still that version, payments are still on, and no rule now
    denies it. "" to go ahead; else why it ended (and it is ended)."""
    rec = SK.get(ctx.c, "fin_recipients", str(pay["recipient_id"]))
    money = SK.get(ctx.c, "fin_money_accounts", str(pay["money_account_id"]))
    why = ""
    if rec is None or str(rec["status"]) != "active" or int(rec["version"]) != int(pay["recipient_version"]):
        why = "the recipient's details changed after it was approved"
    elif not rec["provider_recipient_id"]:
        return "wait"
    elif money is None or not settings(ctx.c, str(money["connection_id"])).get("payments_enabled"):
        why = "payments were turned off for this bank connection"
    else:
        from .gating import rules
        found = {"kind": "payment_create", "amount": int(pay["amount"]), "recipient.id": str(rec["id"]),
                 "recipient.known": known(ctx, str(rec["id"])), "counterparty": str(rec["name"]),
                 "actor": str(pay["created_by"])}
        verdict = G.evaluate(rules(ctx), found, datetime.fromisoformat(ctx.today).date())
        if verdict.outcome == "deny":
            why = verdict.reason or "a finance rule now refuses it"
    if why:
        end(ctx, pay, "cancelled", why)
    return why


def request_body(pay: Any, rec: Any) -> dict[str, object]:
    return {"recipientId": str(rec["provider_recipient_id"]), "amount": dollars(int(pay["amount"])),
            "paymentMethod": MERCURY_METHOD[str(pay["method"])], "idempotencyKey": str(pay["idempotency_key"]),
            "note": f"{NOTE}{pay['idempotency_key']}", "externalMemo": str(pay["memo"] or "")[:140] or None}


def sent(ctx: Ctx, pay: Any, answer: Answer) -> None:
    """What a request-send-money call came back with. A timeout or 5xx may have landed: `unknown`, hold kept, found
    again by the same key. 401/403 is the IP allowlist: it waits. Any other refusal fails it."""
    attempts = int(pay["attempts"]) + 1
    if answer.ok:
        status = F.request_status(answer.body.get("status")) or "awaiting_bank_approval"
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {
            "status": status, "provider_request_id": str(answer.body.get("requestId") or answer.body.get("id") or ""),
            "attempts": attempts, "last_error": None, "next_try_at": _later(ctx, 1), "updated_at": ctx.at})
        if status == "cancelled":
            end(ctx, pay, "cancelled", "rejected in Mercury")
        return
    if answer.status is None or answer.status >= 500 or answer.status == 409:
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "unknown", "attempts": attempts,
                                                          "last_error": error_text(answer, True),
                                                          "next_try_at": _later(ctx, attempts), "updated_at": ctx.at})
        return
    if answer.refused_write:
        SK.update(ctx.c, "fin_payments", str(pay["id"]), {"attempts": attempts, "last_error": IP_HINT,
                                                          "next_try_at": _later(ctx, attempts), "updated_at": ctx.at})
        return
    end(ctx, pay, "failed", error_text(answer, True))


def polled(ctx: Ctx, pay: Any, answer: Answer) -> None:
    """A request's status from Mercury. Rejected or cancelled in Mercury ends it and releases its hold; approved
    becomes `submitted` until its bank transaction posts (finance/banking.py settles it)."""
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


def found(ctx: Ctx, pay: Any, requests: list[Mapping[str, object]]) -> bool:
    """Resolve an `unknown` payment from Mercury's list of requests: one whose note carries this payment's key is it.
    False when none does (the worker then sends again under the same key, never a fresh one)."""
    mine = next((r for r in requests if f"{NOTE}{pay['idempotency_key']}" in str(r.get("note") or "")), None)
    if mine is None:
        return False
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "awaiting_bank_approval", "updated_at": ctx.at,
                                                      "provider_request_id": str(mine.get("requestId") or mine.get("id")
                                                                                 or "")})
    polled(ctx, SK.get(ctx.c, "fin_payments", str(pay["id"])), Answer(200, dict(mine)))
    return True


def feed_settled(ctx: Ctx, pay: Any, feed_id: str) -> None:
    """Its bank transaction posted: settled, and the entry absorbed the hold."""
    SK.update(ctx.c, "fin_payments", str(pay["id"]), {"status": "settled", "provider_txn_id": feed_id,
                                                      "settled_at": ctx.at, "next_try_at": None, "updated_at": ctx.at})
    _event(ctx, SENT, pay)


def feed_failed(ctx: Ctx, request_id: str, status: str) -> None:
    pay = SK.payment_by_request(ctx.c, request_id)
    if pay is not None and str(pay["status"]) not in ("returned", "failed", "cancelled"):
        if str(pay["status"]) == "settled" and pay["hold_id"]:
            SK.update(ctx.c, "fin_payments", str(pay["id"]), {"hold_id": None})
            pay = SK.get(ctx.c, "fin_payments", str(pay["id"]))
        end(ctx, pay, status, f"the bank {status} it")


PLANNERS = {"recipient_add": recipient_add, "payment_create": payment_create, "payment_cancel": payment_cancel,
            "payment_settings": payment_settings}

__all__ = ["PLANNERS", "precheck", "sent", "polled", "found", "feed_settled", "feed_failed", "request_body", "end"]
