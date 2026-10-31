"""Bank and card feed rows as Finance reads them (spec §4.3, §9), with no I/O: a provider's transaction normalized to
signed cents, the webhook signature check, statement balances, and the hash that makes changed payee details a new
recipient. Mercury is the first provider; its documented gaps are settled here, once, in tested functions."""
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from .errors import invalid

SIGNATURE_TOLERANCE = 300            # seconds a signed delivery may be old (Mercury recommends 5 minutes)
# Mercury transaction statuses: pending is an authorization or an in-flight transfer; sent has posted. The rest end it.
POSTED, PENDING = "sent", "pending"
VOID = ("cancelled", "failed", "blocked")
REVERSED = "reversed"
RECLASS = "reclass"     # a found payment's reconciled debit moved to its line (banking._reclass): `<feed id>:reclass`


def reclass_of(source: str, source_id: str) -> str:
    """The feed row a reclassification entry moved the spend of, else ""."""
    parts = source_id.split(":")
    return parts[0] if source == "bank" and len(parts) > 1 and parts[1] == RECLASS else ""
# Mercury IO statements dated after this day carry charges positive (its changelog); earlier ones carry what is owed
# negative (its endpoint page). Normalized once, in `credit_owed`.
CREDIT_SIGN_FLIP = "2026-06-15"
TRANSFER_KINDS = ("internalTransfer", "treasuryTransfer")
CARD_KINDS = ("creditCardTransaction", "debitCardTransaction", "creditCardCredit", "debitCardCredit",
              "cardInternationalTransactionFee")


def cents(value: object) -> int:
    """Dollars as Mercury sends them (a JSON number in 0.01 steps) to integer cents. Refuses sub-cent amounts rather
    than round money."""
    if isinstance(value, bool) or value is None:
        raise invalid("an amount is a number")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise invalid("an amount is a number") from None
    scaled = d * 100
    if scaled != scaled.to_integral_value():
        raise invalid("an amount has at most two decimals")
    return int(scaled)


def day_of(stamp: object) -> str:
    """The UTC day of a provider timestamp (or a plain day)."""
    text = str(stamp or "")
    if len(text) == 10:
        return text
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise invalid("a timestamp is ISO 8601") from None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.date().isoformat()


@dataclass(frozen=True)
class FeedTxn:
    """One provider transaction. `amount` is signed from the account's side: negative is money out of a bank account
    or a charge added to a card balance; positive is money in or a card refund or payment."""
    provider_id: str
    account_id: str
    amount: int
    txn_date: str
    status: str
    kind: str = ""
    counterparty: str = ""
    description: str = ""
    card_id: str = ""
    request_id: str = ""
    note: str = ""
    version: int = 0
    raw: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())

    @property
    def pending(self) -> bool:
        return self.status == PENDING

    @property
    def void(self) -> bool:
        return self.status in VOID

    @property
    def reversed(self) -> bool:
        return self.status == REVERSED

    @property
    def card(self) -> bool:
        return bool(self.card_id) or self.kind in CARD_KINDS


def _dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def mercury_txn(raw: dict[str, Any], version: int = 0) -> FeedTxn:
    """A Mercury transaction. Its top-level `amount` sign is undocumented; Mercury's recorded responses carry debits
    negative and credits positive (as its documented `merchant.amount` does), so the sign is kept as sent. The day is
    the posting day once posted, else the day it was made."""
    tid, account = str(raw.get("id") or ""), str(raw.get("accountId") or "")
    if not tid or not account:
        raise invalid("a transaction has an id and an account")
    status = str(raw.get("status") or PENDING)
    when = raw.get("postedAt") if status == POSTED and raw.get("postedAt") else raw.get("createdAt")
    details = _dict(raw.get("details"))
    card = str(raw.get("cardId") or "")
    if not card:
        card = str(_dict(details.get("debitCardInfo") or details.get("creditCardInfo")).get("id") or "")
    return FeedTxn(provider_id=tid, account_id=account, amount=cents(raw.get("amount")), txn_date=day_of(when),
                   status=status, kind=str(raw.get("kind") or ""),
                   counterparty=str(raw.get("counterpartyNickname") or raw.get("counterpartyName") or "")[:200],
                   description=str(raw.get("bankDescription") or raw.get("externalMemo") or "")[:500],
                   card_id=card, request_id=str(raw.get("requestId") or ""), note=str(raw.get("note") or "")[:500],
                   version=version, raw=raw)


def verify_signature(header: str, body: bytes, secret: str, now: float,
                     tolerance: int = SIGNATURE_TOLERANCE) -> tuple[bool, str]:
    """Mercury's `Mercury-Signature: t=<unix>,v1=<hex>`: HMAC-SHA256 of `<t>.<raw body>` under the endpoint secret,
    compared in constant time, refused when older than `tolerance`. (ok, why not)."""
    if not secret:
        return False, "no signing secret"
    parts: dict[str, list[str]] = {}
    for piece in str(header or "").split(","):
        k, _, v = piece.strip().partition("=")
        parts.setdefault(k, []).append(v)
    stamp, sent = (parts.get("t") or [""])[0], parts.get("v1") or []
    if not stamp.isdigit() or not sent:
        return False, "no signature"
    if abs(now - int(stamp)) > tolerance:
        return False, "the signature is too old"
    want = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(want, s.strip().lower()) for s in sent):
        return False, "the signature does not match"
    return True, ""


def sign(body: bytes, secret: str, stamp: int) -> str:
    """The header Mercury would send for `body` (tests and fixtures)."""
    digest = hmac.new(secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def credit_owed(statement: dict[str, Any]) -> tuple[int | None, int]:
    """(starting, ending) amount owed on a Mercury IO statement, positive when owed. Mercury documents both signs; a
    statement ending after CREDIT_SIGN_FLIP carries charges positive, an earlier one owed negative."""
    account = _dict(_dict(statement.get("details")).get("accountDetails"))
    source: dict[str, Any] = account or statement
    flip = 1 if day_of(statement.get("endDate") or "") > CREDIT_SIGN_FLIP else -1
    start = source.get("startingBalance")
    ending = source.get("endingBalance", statement.get("endingBalance"))
    return (None if start is None else flip * cents(start)), flip * cents(ending)


def bank_ending(statement: dict[str, Any]) -> int:
    """A depository statement's ending balance (Mercury sends no starting balance)."""
    return cents(statement.get("endingBalance"))


def details_hash(method: str, info: dict[str, Any]) -> str:
    """What identifies where money goes: the method plus account and routing numbers (or the mailing address for a
    check). Any change makes a new recipient, so a changed invoice can never redirect a known payee's money."""
    if method == "check":
        address = _dict(info.get("address")) or info
        keys = ("address1", "address2", "city", "region", "postalCode", "country")
        parts = [str(address.get(k) or "").strip().lower() for k in keys]
    else:
        parts = [str(info.get("accountNumber") or "").strip(), str(info.get("routingNumber") or "").strip()]
        if not all(parts):
            raise invalid("an account and routing number are needed")
    return hashlib.sha256("|".join([method, *parts]).encode()).hexdigest()


def last4(number: object) -> str:
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    return digits[-4:]


REQUEST_STATUS = {"pendingApproval": "awaiting_bank_approval", "approved": "submitted", "rejected": "cancelled",
                  "cancelled": "cancelled"}


def request_status(value: object) -> str:
    """A Mercury send-money request's status as a payment status; anything unknown stays where it is ("")."""
    return REQUEST_STATUS.get(str(value or ""), "")


# ------------------------------------------------------------------------------------------------ Stripe
STRIPE_BALANCE = "balance"        # Stripe's balance transactions all sit on the one balance account
STRIPE_PAYOUT = "payout"          # the balance moved to the bank: a transfer, matched to the deposit, never income
STRIPE_SALES = ("charge", "payment", "refund", "payment_refund", "adjustment")   # income, or its reversal (disputes)
STRIPE_COSTS = ("stripe_fee", "network_cost", "tax_fee")                         # Stripe's own charges, no income


def _int(value: object) -> int:
    """A Stripe amount: whole cents (Stripe never sends a fraction of one)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid("a Stripe amount is a whole number of cents")
    return value


def stripe_txn(raw: dict[str, Any]) -> FeedTxn:
    """A Stripe balance transaction. Its `net` (gross less fee) is what the balance moved, so it is the feed amount;
    gross and fee are kept in the raw copy for posting (`stripe_parts`). The day is the UTC day it was created. Only
    the billing name is kept from the source, the part that matches an invoice; the source is kept by id."""
    tid = str(raw.get("id") or "")
    if not tid:
        raise invalid("a balance transaction has an id")
    gross, fee, net = _int(raw.get("amount")), _int(raw.get("fee")), _int(raw.get("net"))
    created = _int(raw.get("created"))
    day = datetime.fromtimestamp(created, timezone.utc).date().isoformat()
    source = raw.get("source")
    billing = _dict(_dict(source).get("billing_details"))
    keep: dict[str, Any] = {"id": tid, "type": str(raw.get("type") or ""), "amount": gross, "fee": fee, "net": net,
                            "currency": str(raw.get("currency") or ""),
                            "description": str(raw.get("description") or "")[:500],
                            "source": str(_dict(source).get("id") or source or "")[:200]}
    return FeedTxn(provider_id=tid, account_id=STRIPE_BALANCE, amount=net, txn_date=day, status=POSTED,
                   kind=str(raw.get("type") or ""), counterparty=str(billing.get("name") or "")[:200],
                   description=str(raw.get("description") or "")[:500], version=1, raw=keep)


def stripe_parts(raw: Mapping[str, Any]) -> tuple[int, int]:
    """A stored Stripe balance transaction's (gross, fee) in cents."""
    return _int(raw.get("amount")), _int(raw.get("fee"))
