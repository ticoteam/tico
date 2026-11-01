"""Bank and card feeds into the books (spec §4.3, §8.4, §9), inside the caller's transaction and with no network:
connecting a provider, taking in a transaction (from a webhook, the Events API or a backfill page), posting it once,
re-categorizing it, and reconciling an account to its statement.

Posting a feed row:
- An IO card charge is a liability: Dr expense (the card's budget line, else Other expenses; the holder as teammate,
  the counterparty as vendor) / Cr the card program's liability. Pending is the authorization: a hold. Its posted
  update settles the hold into the entry (hold_ids), so it counts once. A refund posts the other way. What a card
  used is read from these entries (store/budgets.card_used), reversals and replacements at their own dates.
- Money moving between two of our accounts (paying the card bill, a sweep to savings) is one transfer entry for both
  rows, posted when the second side is seen.
- A bank debit that pays a Finance payment posts Dr the payment's line / Cr bank and settles the payment's hold.
- A row that pays the one open bill (invoice) owing its exact amount, from a contact the counterparty names, posts to
  accounts payable (receivable) and settles it: the expense is the bill's, never posted twice.
- Otherwise the first posting rule (or the counterparty contact's default line) says where it posts; a row nothing
  covers asks (needs_category): a debit sits in Other expenses until `categorize` moves it, a credit waits as `new`.
- Pending -> posted, or an amount change, updates the row in place; a changed posted row is reversed and posted
  again, never twice. A row a done reconciliation cleared is locked."""
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast

from . import budgeting, documents, payments, posting, posting_rules
from .context import Ctx
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .core.org import SYSTEM
from .handlers import Plan, Result, may_write
from .store import ap as SAP
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL

NEW_EVENT, CATEGORY_EVENT, DECLINED_EVENT = "finance.feed.new", "finance.feed.needs_category", "finance.card.declined"
OTHER, BANK, CARDS, OPENING = "6900", "1000", "2100", "3900"
BOOKS_PROVIDERS = ("quickbooks", "xero")
MATCH_DAYS = 5                     # a transfer's two sides post within this many days of each other
AUTH_DAYS = 30                     # an authorization's hold lasts this long without its settlement


def event(ctx: Ctx, name: str, subject: str, payload: Mapping[str, object], key: str = "") -> None:
    """A finance.* event for Tico's rules, ids only, written with the change (once per name, subject and key)."""
    SB.event(ctx.c, name, subject, key, payload, SYSTEM, ctx.at)


def by_code(ctx: Ctx, code: str) -> L.Account | None:
    return next((a for a in ctx.accounts.values() if a.code == code), None)


def _gl(ctx: Ctx, money: Any) -> L.Account:
    account = ctx.accounts.get(str(money["account_id"] or ""))
    if account is None:
        raise invalid("that money account has no account in the books")
    return account


def new_account(ctx: Ctx, base: str, name: str, type_: str, subtype: str, who: str) -> L.Account:
    """The first free code from `base` (1000 → 1001, 1002...) for a provider account Tico maps."""
    taken = {a.code for a in ctx.accounts.values()}
    code = int(base)
    while str(code) in taken:
        code += 1
    made = L.Account(id=SL.new_id(), book_id=ctx.book.id, code=str(code), name=name[:120], type=type_,
                     subtype=subtype)
    L.check_account(made, ctx.accounts)
    SL.insert_account(ctx.c, made, who, ctx.at)
    ctx.changed()
    return made


# ------------------------------------------------------------------------------------------------ connecting
def connection_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Connect a Mercury account with the company's token (a Tico credential, read at use time and never copied
    here). `probe` is what finance/sync.probe read with that token just before: the accounts (last 4 only), the IO
    credit accounts and Mercury's users. Each account becomes a money account mapped to a line in the books: checking
    and savings are bank assets, IO is a credit card liability. Payments through Mercury's approval queue turn on
    only when someone other than the token's creator can approve in Mercury; Finance never falls back to direct
    send."""
    if str(p.get("provider") or "") in BOOKS_PROVIDERS:
        # A books connection is made only by its claim (books_link.finish), never as a bank connection.
        raise invalid("QuickBooks and Xero connect through POST /api/v2/finance/books/connect, not here")
    if V.opt_text(p, "provider", 20) == "stripe":
        from . import stripe_sync        # lazy: it imports this module
        return stripe_sync.connection_create(ctx, who, p)
    V.only(p, ("provider", "credential_id", "name", "probe", "approver_email", "token_creator_email", "server_ip"))
    V.choice(p, "provider", ("mercury",))
    cred = V.text(p, "credential_id", 120)
    if any(str(r["credential_ref"] or "") == cred and str(r["status"]) != "disconnected" for r in SK.connections(ctx.c)):
        raise FinanceError(CONFLICT, "that credential is already connected")
    probe = V.obj(p.get("probe"), "probe")
    accounts = [V.obj(a, "an account") for a in V.items(probe.get("accounts"), "probe.accounts")]
    raw_credit: object = probe.get("credit") or []
    credit = [V.obj(a, "a credit account") for a in V.items(raw_credit, "probe.credit")]
    if not accounts and not credit:
        raise invalid("the token sees no Mercury accounts")
    approver = (V.opt_text(p, "approver_email", 200) or "").strip().lower()
    creator = (V.opt_text(p, "token_creator_email", 200) or "").strip().lower()
    users: object = probe.get("users")
    emails = {str(V.obj(u, "a user").get("email") or "").lower() for u in V.items(users, "users")} \
        if users is not None else None
    if not approver:
        note = "name a Mercury user other than the token's creator to approve payments in Mercury"
    elif approver == creator:
        note = "Mercury needs the approver to be someone other than the token's creator"
    elif emails is not None and approver not in emails:
        note = "that approver is not a user in this Mercury account"
    else:
        note = ""
    settings = {"approval_eligible": not note, "payments_enabled": not note, "approval_note": note or None,
                "approver_email": approver or None,
                "egress_ip": V.opt_text(p, "server_ip", 64) or probe.get("egress_ip") or None}
    name = V.opt_text(p, "name", 120) or "Mercury"

    def apply(aid: str) -> Result:
        cid = SK.insert(ctx.c, "fin_connections", {
            "provider": "mercury", "purpose": "bank", "credential_ref": cred, "status": "active", "name": name,
            "settings": SK.dump(settings), "next_sync_at": ctx.at, "created_at": ctx.at, "created_by": who})
        if users is not None:
            SK.set_provider_users(ctx.c, cid, [V.obj(u, "a user") for u in V.items(users, "users")], ctx.at)
        mapped: list[Result] = []
        for a in accounts:
            if str(a.get("status") or "active") not in ("active", "pending"):
                continue
            if (kept := _reuse(ctx, cid, a)) is not None:
                mapped.append(kept)
                continue
            subtype = "savings" if "saving" in str(a.get("kind") or "").lower() else "checking"
            bank = by_code(ctx, BANK)
            used = bank is not None and any(str(r["account_id"]) == bank.id for r in SK.money_accounts(ctx.c,
                                                                                                        ctx.book.id))
            gl = bank if bank is not None and not used else new_account(
                ctx, BANK, f"{a.get('name') or 'Mercury'} ••{a.get('last4') or ''}", "asset", "bank", who)
            mapped.append(_money(ctx, cid, a, "bank", subtype, gl, who))
        for a in credit:
            if str(a.get("status") or "active") not in ("active", "pending"):
                continue
            if (kept := _reuse(ctx, cid, a)) is not None:
                mapped.append(kept)
                continue
            cards = by_code(ctx, CARDS)
            gl = cards if cards is not None and not any(
                str(r["account_id"]) == cards.id for r in SK.money_accounts(ctx.c, ctx.book.id)) else None
            if gl is None:
                gl = new_account(ctx, CARDS, "Credit cards" if cards is None else "Mercury IO", "liability",
                                  "credit_card", who)
            mapped.append(_money(ctx, cid, {**a, "name": a.get("name") or "Mercury IO"}, "card_program", "credit", gl,
                                 who))
        return {"connection_id": cid, "money_accounts": mapped, "payments_enabled": not note,
                "approval_note": note or None}
    return Plan(K.ALLOW, f"Connect Mercury ({len(accounts)} account(s), {len(credit)} credit)", apply)


def _reuse(ctx: Ctx, cid: str, a: Mapping[str, object]) -> Result | None:
    """A Mercury account already in the books (connected before, perhaps with another token for the same Mercury
    organization) is the same money account: it moves to this connection, so its history is never taken in twice."""
    m = SK.money_account_by_provider_id(ctx.c, ctx.book.id, "mercury", str(a.get("id") or ""))
    if m is None:
        return None
    SK.update(ctx.c, "fin_money_accounts", str(m["id"]), {"connection_id": cid, "status": "active",
                                                          "updated_at": ctx.at})
    gl = ctx.accounts.get(str(m["account_id"]))
    return {"money_account_id": str(m["id"]), "kind": str(m["kind"]), "account_id": str(m["account_id"]),
            "code": gl.code if gl is not None else ""}


def _money(ctx: Ctx, cid: str, a: Mapping[str, object], kind: str, subtype: str, gl: L.Account, who: str) -> Result:
    current, available = a.get("current"), a.get("available")
    mid = SK.insert(ctx.c, "fin_money_accounts", {
        "book_id": ctx.book.id, "kind": kind, "subtype": subtype, "name": str(a.get("name") or "Mercury")[:120],
        "provider": "mercury", "connection_id": cid, "account_id": gl.id, "provider_account_id": str(a.get("id")),
        "last4": str(a.get("last4") or "")[-4:] or None, "holds_cash": 0 if kind == "card_program" else 1,
        "balance": current if isinstance(current, int) else None,
        "available_balance": available if isinstance(available, int) else None,
        "balance_currency": ctx.book.base_currency, "balance_as_of": ctx.at if isinstance(current, int) else None,
        "created_at": ctx.at, "created_by": who})
    return {"money_account_id": mid, "kind": kind, "account_id": gl.id, "code": gl.code}


def set_balance(ctx: Ctx, money_id: str, current: int | None, available: int | None, as_of: str) -> None:
    """The provider's own balance, with when it was read: what stale checks and Sources show."""
    SK.update(ctx.c, "fin_money_accounts", money_id, {"balance": current, "available_balance": available,
                                                      "balance_as_of": as_of, "updated_at": ctx.at})


# ------------------------------------------------------------------------------------------------ opening balance
def _opening_line(ctx: Ctx) -> L.Account:
    """Opening balance equity: setup makes it; an older book gets it the first time an opening balance posts."""
    found = next((a for a in ctx.accounts.values() if a.type == "equity" and a.subtype == "opening"), None)
    return found or new_account(ctx, OPENING, "Opening balance equity", "equity", "opening", SYSTEM)


def opening_amount(ctx: Ctx, money: Any, through: str) -> int:
    """What the money account's opening-balance entry put on its GL line, if it is dated through `through`."""
    entry = SK.opening_entry(ctx.c, ctx.book.id, str(money["id"]))
    if entry is None or str(entry["entry_date"]) > through:
        return 0
    gl = str(money["account_id"] or "")
    return sum(line.amount for _, line in SL.entry_lines(ctx.c, str(entry["id"])) if line.account_id == gl)


def set_opening(ctx: Ctx, money: Any, amount: int, day: str) -> str | None:
    """Make the money account's one opening-balance entry say `amount` on `day` (Dr/Cr its GL line against Opening
    balance equity). Unchanged: nothing. Changed: the old entry is reversed and the new one posted, each on an open
    day (a closed month keeps its meaning). The live entry's id, or None when the opening is 0."""
    gl = _gl(ctx, money)
    prior = SK.opening_entry(ctx.c, ctx.book.id, str(money["id"]))
    if prior is not None:
        moved = sum(line.amount for _, line in SL.entry_lines(ctx.c, str(prior["id"])) if line.account_id == gl.id)
        dated = str(prior["entry_date"])
        if moved == amount and (dated == day or SL.closed_on(ctx.c, ctx.book.id, day)):
            return str(prior["id"])
        posting.reverse(ctx, str(prior["id"]), day=open_day(ctx, dated), memo="Opening balance changed", by=SYSTEM)
    if amount == 0:
        return None
    mid = str(money["id"])
    source_id = f"opening:{mid}" if prior is None else f"opening:{mid}:{ctx.at}"
    done = posting.post_entry(ctx.c, {"date": open_day(ctx, day), "source": "sync", "source_id": source_id,
                                      "memo": f"Opening balance: {money['name']}"[:200],
                                      "lines": [feed_line(gl.id, amount), feed_line(_opening_line(ctx).id, -amount)]},
                              at=ctx.at)
    return str(done["entry_id"])


def opening_balance(ctx: Ctx, money_id: str) -> str | None:
    """After a backfill: the provider's current balance less every settled feed row is what the account held before
    the first of them, posted as of the day before it (no rows: today). An IO credit line's balance and rows carry
    the same sign (owed negative), so the liability side comes out the same way. Idempotent per money account; a
    later backfill that reaches further back adjusts the one entry. Nothing without a balance or a GL line."""
    money = SK.get(ctx.c, "fin_money_accounts", money_id)
    if money is None or money["balance"] is None or not money["account_id"]:
        return None
    total, first = SK.settled_total(ctx.c, money_id)
    day = (dates.day(first) - timedelta(days=1)).isoformat() if first else ctx.today
    return set_opening(ctx, money, int(money["balance"]) - total, day)


def _before_opening(ctx: Ctx, money: Any, amount: int, day: str) -> None:
    """A settled row first seen after the opening was posted, dated on or before it: the provider's balance already
    held it inside the opening, so the opening gives it back (and moves before it), never counting it twice."""
    entry = SK.opening_entry(ctx.c, ctx.book.id, str(money["id"]))
    if entry is None:
        return
    dated = str(entry["entry_date"])
    if day > dated:
        return
    have = opening_amount(ctx, money, dated)
    earlier = (dates.day(day) - timedelta(days=1)).isoformat()
    set_opening(ctx, money, have - amount, min(dated, earlier))


# ------------------------------------------------------------------------------------------------ taking in
def ingest(ctx: Ctx, connection_id: str, t: F.FeedTxn, by: str = SYSTEM) -> str | None:
    """Take in one provider transaction: insert or update its row and post what it means. The feed row id, or None
    when it is on an account this connection does not map. A replay of the same version changes nothing."""
    money = SK.money_account_by_provider(ctx.c, connection_id, t.account_id)
    if money is None:
        return None
    row = SK.feed_row(ctx.c, str(money["id"]), t.provider_id)
    if row is not None:
        have = int(row["version"])
        # A snapshot older than the row (or one with no version, like a /transactions page, once a versioned event
        # was taken in) changes nothing, and a posted row never goes back to pending.
        if (t.version and have >= t.version) or (not t.version and have > 0) or (row["entry_id"] and t.pending):
            return str(row["id"])
    card = SK.card_by_provider(ctx.c, "mercury", t.card_id) if t.card_id else None
    fields: dict[str, object] = {
        "txn_date": t.txn_date, "amount": t.amount, "counterparty": t.counterparty, "description": t.description,
        "pending": 1 if t.pending else 0, "provider_status": t.status, "kind": t.kind,
        "card_id": str(card["id"]) if card else None, "request_id": t.request_id or None, "raw": SK.dump(t.raw),
        "version": max(t.version, int(row["version"]) if row is not None else 0), "updated_at": ctx.at,
        "posted_at": t.raw.get("postedAt") if not t.pending else None}
    if row is None:
        fid = SK.insert(ctx.c, "fin_feed_txns", {**fields, "money_account_id": str(money["id"]),
                                                 "provider_id": t.provider_id, "currency": ctx.book.base_currency,
                                                 "status": "new", "created_at": ctx.at, "created_by": by})
        event(ctx, NEW_EVENT, fid, {"feed_id": fid})
    else:
        fid = str(row["id"])
        if row["reconciliation_id"] and (int(row["amount"]) != t.amount or t.void or t.reversed):
            SK.update(ctx.c, "fin_feed_txns", fid, {"raw": SK.dump(t.raw), "provider_status": t.status,
                                                    "version": fields["version"], "updated_at": ctx.at})
            event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid, "why": "changed after it was reconciled"},
                  f"v{t.version}")
            return fid
        SK.update(ctx.c, "fin_feed_txns", fid, fields)
    was_settled = row is not None and not int(row["pending"]) and str(row["provider_status"] or "") not in (
        *F.VOID, F.REVERSED)
    if not t.pending and not (t.void or t.reversed) and not was_settled and ctx.book.mode != "mirror":
        _before_opening(ctx, money, t.amount, t.txn_date)
    _settle(ctx, SK.get(ctx.c, "fin_feed_txns", fid), money, card, t)
    return fid


def _settle(ctx: Ctx, row: Any, money: Any, card: Any, t: F.FeedTxn) -> None:
    fid = str(row["id"])
    if ctx.book.mode == "mirror":
        from .mirror import settle_row
        settle_row(ctx, row, money, t)
        return
    program = str(money["kind"]) == "card_program"
    if t.void or t.reversed:
        undo(ctx, row)
        if card is not None and t.status in ("failed", "blocked"):
            event(ctx, DECLINED_EVENT, str(card["id"]), {"card_id": str(card["id"]), "feed_id": fid}, fid)
        status = "returned" if t.reversed else "failed"
        if t.request_id:
            payments.feed_failed(ctx, t.request_id, status)
        elif t.amount < 0 and SK.direct_payment(ctx.c, t.provider_id) is not None:
            # A direct send has no request: its Mercury transaction id is its key, so a voided bank row ends it here
            # (its hold released), as an approval request's voided row does.
            payments.feed_failed(ctx, t.provider_id, status)
        return
    if t.pending:
        if program and card is not None and t.amount < 0:
            _card_hold(ctx, row, card, -t.amount)
        return
    if str(money["kind"]) == "stripe":    # a Stripe row posts once and never changes: stripe_post settles or waits
        from . import stripe_post        # lazy: it posts through this module's helpers
        stripe_post.settle(ctx, row, money, t)
        return
    if row["entry_id"]:
        if _unchanged(ctx, row, money):
            return
        _unpost(ctx, row, "the bank changed this transaction")
        row = SK.get(ctx.c, "fin_feed_txns", fid)
    if str(row["status"]) == "ignored":
        return
    gl = _gl(ctx, money)
    pay = SK.payment_by_request(ctx.c, t.request_id) if t.request_id else None
    if pay is None and t.amount < 0:
        pay = SK.direct_payment(ctx.c, t.provider_id) or _marked(ctx, money, t)
    if pay is not None and t.amount < 0:
        tags = cast(dict[str, str], SK.loads(pay["tags"]))
        rec = SK.get(ctx.c, "fin_recipients", str(pay["recipient_id"]))
        vendor = payments.vendor_of(rec) if rec is not None else t.counterparty
        entry = post_row(ctx, fid, t, [feed_line(str(pay["account_id"]), -t.amount, tags, vendor)], gl.id,
                      [str(pay["hold_id"])] if pay["hold_id"] else [])
        mark_posted(ctx, [fid], entry)
        payments.feed_settled(ctx, pay, fid)
        return
    if t.kind in F.TRANSFER_KINDS:
        other = _counterpart(ctx, row, money)
        if other is not None:
            transfer(ctx, row, money, other)
        return                      # one side of a move between our accounts: it posts when the other side arrives
    if program and card is not None:
        # A charge or refund on a card Tico issued posts against the card liability: to the line a posting rule picks,
        # else the card's budget line, tagged with its holder either way.
        # A charge paying an entered bill goes to AP instead (the bill already took the expense); the card's use and
        # hold work the same either way.
        account, tags = card_line(ctx, card)
        found = documents.match_feed(ctx, row, t) if t.amount < 0 else None
        if isinstance(found, str):      # it may pay a bill past what was looked at: it stays new, a person links it
            event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid})
            return
        if found is not None:
            lines = [feed_line(documents.control(ctx, "bill").id, -t.amount, {},
                               str(found[1][f"{documents.CONTACT_OF['bill']}_id"]))]
            ask = False
        else:
            lines, ask = posting_rules.card_lines(ctx, row, money, account, tags)
        holds = [str(row["hold_id"])] if row["hold_id"] and t.amount < 0 else []
        entry = post_row(ctx, fid, t, lines, gl.id, holds)
        mark_posted(ctx, [fid], entry)
        card_used(ctx, row, -t.amount, entry)
        if found is not None:
            documents.feed_paid(ctx, row, found[0], found[1], entry)
        if ask:
            event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid})
        return
    # Anything else: a payment marked paid from this account, else the one open bill or invoice it pays, else the
    # first posting rule (or the counterparty's default line), else a debit posts to Other expenses and a credit
    # waits, and a person is asked (needs_category).
    if t.amount > 0:
        from . import stripe_post        # a deposit may be the transfer of a Stripe payout
        if stripe_post.bank_deposit(ctx, row, money, t):
            return
    expected = documents.expected_match(ctx, row, money, t)
    if expected:
        if expected == documents.ASK:   # it may pay one of several marked payments: it stays new, a person picks
            event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid})
        return                      # the bank row of a payment marked paid from this account: linked, posted once
    found = documents.match_feed(ctx, row, t)
    if isinstance(found, str):          # it may pay a document past what was looked at: it stays new, a person links it
        event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid})
        return
    if found is not None:
        kind, doc = found
        entry = post_row(ctx, fid, t, [feed_line(documents.control(ctx, kind).id, -t.amount, {},
                                                 str(doc[f"{documents.CONTACT_OF[kind]}_id"]))], gl.id, [])
        mark_posted(ctx, [fid], entry)
        documents.feed_paid(ctx, row, kind, doc, entry)
        return
    if posting_rules.post_feed(ctx, row, money, t, gl.id):
        return
    if t.amount < 0:
        spend = by_code(ctx, OTHER)
        if spend is not None:
            entry = post_row(ctx, fid, t, [feed_line(spend.id, -t.amount, {}, t.counterparty)], gl.id, [])
            mark_posted(ctx, [fid], entry)
    event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid})


def _marked(ctx: Ctx, money: Any, t: F.FeedTxn) -> Any:
    """A payment whose send answer was lost, recognized by the `tico:<key>` its request carried, when the debit shows
    it (in `note` or `externalMemo`) at the same amount on the same account: tied to the debit's request from now."""
    shown = f"{t.note} {t.raw.get('externalMemo') or ''}"
    for pay in SK.payments_unlinked(ctx.c, str(money["id"]), -t.amount):
        if f"{payments.NOTE}{pay['idempotency_key']}" in shown:
            if t.request_id:
                SK.update(ctx.c, "fin_payments", str(pay["id"]), {"provider_request_id": t.request_id,
                                                                  "updated_at": ctx.at})
            return SK.get(ctx.c, "fin_payments", str(pay["id"]))
    return None


def payment_linked(ctx: Ctx, pay: Any) -> None:
    """A payment just tied to its Mercury request (found again after a lost send answer). A debit for that request a
    sync already took in posted as an unknown spend: it posts again as the payment (its line, tags and vendor),
    settling it and taking in its hold, once. A debit the bank already returned or failed ends the payment (its hold
    released). A reconciled one keeps its bank line: its spend moves to the payment's line (`_reclass`), which takes
    in the hold, and the payment settles on it."""
    rid = str(pay["provider_request_id"] or "")
    if not rid or str(pay["status"]) in ("settled", "failed", "cancelled", "returned"):
        return
    row = SK.payment_debit(ctx.c, str(pay["money_account_id"]), rid)
    if row is None:
        return
    gone = str(row["provider_status"] or "")
    if gone in (*F.VOID, F.REVERSED):
        payments.feed_failed(ctx, rid, "returned" if gone == F.REVERSED else "failed")
        return
    if str(row["status"]) == "ignored":
        return
    if row["reconciliation_id"]:
        event(ctx, CATEGORY_EVENT, str(row["id"]), {"feed_id": str(row["id"]),
                                                    "why": "its payment was found after it was reconciled"},
              f"pay:{pay['id']}")
        _reclass(ctx, row, pay)
        payments.feed_settled(ctx, pay, str(row["id"]))
        return
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    if money is None:
        return
    if row["entry_id"]:
        _unpost(ctx, row, "its payment was found")
        row = SK.get(ctx.c, "fin_feed_txns", str(row["id"]))
    t = F.FeedTxn(provider_id=str(row["provider_id"]), account_id="", amount=int(row["amount"]),
                  txn_date=str(row["txn_date"]), status=F.POSTED, kind=str(row["kind"]),
                  counterparty=str(row["counterparty"]), description=str(row["description"]), request_id=rid)
    _settle(ctx, row, money, None, t)


def _reclass(ctx: Ctx, row: Any, pay: Any) -> None:
    """A reconciled debit found to be a payment: its expense side moves to the payment's line, tags and vendor in an
    entry on an open day (Cr each line it hit, Dr the payment's line), never touching the reconciled bank line, and
    that entry takes in the payment's hold in the same post (so budget crossings see the final state, never the move
    and the hold both). When the debit already sits on the payment's line, tags and vendor, its own entry takes the
    hold in; when it never posted, the hold stays settled, counting, until it does."""
    hold = str(pay["hold_id"] or "")
    if not row["entry_id"]:
        if hold:
            budgeting.settle(ctx, hold, -int(row["amount"]), None)
        return
    entry_id = str(row["entry_id"])
    spent = [x for _, x in SL.entry_lines(ctx.c, entry_id) if x.role != L.MONEY_ROLE]
    account = str(pay["account_id"])
    tags = posting.with_team(ctx, cast(dict[str, str], SK.loads(pay["tags"])))
    rec = SK.get(ctx.c, "fin_recipients", str(pay["recipient_id"]))
    vendor = SAP.vendor_key(ctx.c, ctx.book.id,
                            (payments.vendor_of(rec) if rec is not None else str(row["counterparty"]))[:80])
    if all(x.account_id == account and dict(x.tags) == tags and x.contact_id == L.name_key(vendor) for x in spent):
        if hold:
            budgeting.settle(ctx, hold, -int(row["amount"]), entry_id)
        return
    lines: list[dict[str, object]] = [{"account_id": x.account_id, "amount": -x.amount, "tags": dict(x.tags),
                                       "contact_id": x.contact_id} for x in spent]
    lines.append(feed_line(account, sum(x.amount for x in spent), tags, vendor))
    fid = str(row["id"])
    debit = SL.entry_row(ctx.c, entry_id)
    day = str(debit["entry_date"]) if debit is not None else ctx.today
    done = posting.post_entry(ctx.c, {"date": open_day(ctx, day), "source": "bank",      # where the debit sits
                                      "source_id": fresh_source(ctx, "bank", f"{fid}:{F.RECLASS}"),
                                      "memo": f"Payment {pay['id']} found after reconciling"[:200], "lines": lines},
                              hold_ids=[hold] if hold else [], at=ctx.at)
    if not done.get("posted"):
        raise FinanceError(CONFLICT, f"feed row {fid} did not reclassify")


def card_line(ctx: Ctx, card: Any) -> tuple[str, dict[str, str]]:
    """Where a card's spend posts: its budget's expense line, else Other expenses; tagged with its holder."""
    holder = str(card["holder"])
    tags = posting.with_team(ctx, {"teammate": holder})
    b = ctx.budgets().get(str(card["budget_id"] or ""))
    account = ctx.accounts.get(b.account_id) if b is not None and b.account_id else None
    if account is None or account.type != "expense":
        account = by_code(ctx, OTHER)
    if account is None:
        raise invalid("the books have no Other expenses line")
    return account.id, tags


def card_used(ctx: Ctx, row: Any, amount: int, entry_id: str) -> None:
    """A settled card transaction. What a card used is read from the ledger (store/budgets.card_used: every entry for
    its rows, reversals and replacements at their own dates), so the books alone decide it; its authorization hold,
    if still held, only settles here (taken in, it no longer counts)."""
    hold = SB.hold(ctx.c, str(row["hold_id"])) if row["hold_id"] else None
    if hold is not None and str(hold["status"]) == "held":
        SB.settle(ctx.c, str(hold["id"]), amount, entry_id, ctx.at)


def _card_hold(ctx: Ctx, row: Any, card: Any, amount: int) -> None:
    """An authorization: a hold against the card's budget, resized if the pending amount moves."""
    account, tags = card_line(ctx, card)
    b = ctx.budgets().get(str(card["budget_id"] or ""))
    scope: str | Mapping[str, object] = b.id if b is not None and b.active else {"account_id": account, **tags}
    until = dates.day(ctx.today) + timedelta(days=AUTH_DAYS)
    decision = budgeting.place(ctx, budget_scope=scope, amount=amount, currency=ctx.book.base_currency, source="card",
                               source_id=str(row["id"]), expires_at=until.isoformat() + "T00:00:00.000000Z",
                               target=f"card:{card['id']}")
    if decision.hold_id and decision.hold_id != row["hold_id"]:
        SK.update(ctx.c, "fin_feed_txns", str(row["id"]), {"hold_id": decision.hold_id})
        SK.insert(ctx.c, "fin_card_auths", {"card_id": str(card["id"]), "provider_auth_id": str(row["provider_id"]),
                                            "amount": amount, "currency": ctx.book.base_currency,
                                            "merchant": str(row["counterparty"]), "mode": "synced",
                                            "decision": "approved", "hold_id": decision.hold_id,
                                            "created_at": ctx.at, "created_by": SYSTEM})


def left_of(ctx: Ctx, row: Any) -> int:
    """A bank row's amount no payment has applied yet, signed: the part its own entry has to carry."""
    amount = int(row["amount"])
    applied = SAP.settled_from(ctx.c, "feed", str(row["id"]))
    return (1 if amount >= 0 else -1) * (abs(amount) - applied)


def feed_line(account_id: str, amount: int, tags: Mapping[str, str] | None = None, vendor: str = "") -> dict[str, object]:
    line: dict[str, object] = {"account_id": account_id, "amount": amount, "tags": dict(tags or {})}
    if vendor:
        line["contact_id"] = vendor[:80]
    return line


def provider_fee(row: Any, money: Any) -> int:
    """What the money row's provider took out of it before it arrived (a Stripe sale's fee), in cents; 0 for a bank row.
    A row's counter line is its amount plus this much, and the fee posts as its own line (provider_fee_line)."""
    from .stripe_post import fee_of
    return fee_of(row) if str(money["provider"]) == "stripe" else 0


def provider_fee_line(ctx: Ctx, row: Any, money: Any) -> dict[str, object] | None:
    """The line for provider_fee's amount, or None when the provider took none."""
    from .stripe_post import fee_line
    return fee_line(ctx, row) if str(money["provider"]) == "stripe" else None


def vendor_lines(ctx: Ctx, lines: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """A feed row's lines with each vendor as its one key: a counterparty that names a vendor contact (any case) is
    that contact's id, so its card rows and its bills count under one vendor (store/ap.vendor_key); a merchant with
    no contact keeps its name (posting.post keys it folded and keeps the text as its label)."""
    keys: dict[str, str] = {}
    out: list[dict[str, object]] = []
    for line in lines:
        key = str(line.get("contact_id") or "")
        if key and key not in keys:
            keys[key] = SAP.vendor_key(ctx.c, ctx.book.id, key)
        out.append({**line, "contact_id": keys[key]} if key else dict(line))
    return out


def post_row(ctx: Ctx, fid: str, t: F.FeedTxn, lines: list[dict[str, object]], gl_id: str, holds: Sequence[str],
             day: str = "") -> str:
    """One entry for a feed row: its other lines, then the money account's own line (`gl_id`), recorded as such
    (L.MONEY_ROLE, kept by reversals): a card's use is read from that line as it was posted, whatever account the
    card program maps to later (store/budgets.card_used). Every revision gets its own source id (`fresh_source`),
    so a repost is never deduped onto an earlier, reversed entry. `day` overrides the row's own open day."""
    source = "card" if t.card else "bank"
    done = posting.post_entry(ctx.c, {"date": day or open_day(ctx, t.txn_date), "source": source,
                                      "source_id": fresh_source(ctx, source, fid),
                                      "memo": (t.counterparty or t.description)[:200],
                                      "lines": [*vendor_lines(ctx, lines), feed_line(gl_id, t.amount)]},
                              hold_ids=holds, at=ctx.at, roles={len(lines): L.MONEY_ROLE})
    if not done.get("posted"):
        raise FinanceError(CONFLICT, f"feed row {fid} did not post")      # never settle on an older entry
    return str(done["entry_id"])


def fresh_source(ctx: Ctx, source: str, base: str) -> str:
    """`base` for a row's first entry, then `base:<now>`, `base:<now>:2`... for each repost (a correction and a
    payment found in one sweep each get their own)."""
    sid, n = base, 1
    while SL.entry_by_source(ctx.c, ctx.book.id, source, sid) is not None:
        n += 1
        sid = f"{base}:{ctx.at}" if n == 2 else f"{base}:{ctx.at}:{n - 1}"
    return sid


def open_day(ctx: Ctx, day: str) -> str:
    """Where a dated item posts: its own day, or today when that day is in a closed period (§4.1: late items post in
    the current open period, so a closed month keeps its meaning)."""
    return ctx.today if SL.closed_on(ctx.c, ctx.book.id, day) else day


def _combined_row(ctx: Ctx, row: Any) -> bool:
    """Whether the row's entry is the one it posted for several payments marked paid (settled from it, on it)."""
    return len([s for s in SAP.settlements_by_entry(ctx.c, str(row["entry_id"]))
                if str(s["from_kind"]) == "feed" and str(s["from_id"]) == str(row["id"])]) > 1


def mark_posted(ctx: Ctx, ids: Sequence[str], entry_id: str) -> None:
    for fid in ids:
        SK.update(ctx.c, "fin_feed_txns", fid, {"status": "posted", "entry_id": entry_id, "entry_line_id": None,
                                                "updated_at": ctx.at})


def _unchanged(ctx: Ctx, row: Any, money: Any) -> bool:
    """Whether the posted entry still says what the row says: its money line equals the amount (and, unless it is a
    transfer dated by its later side, the day matches)."""
    entry = SL.entry_row(ctx.c, str(row["entry_id"]))
    if entry is None or str(entry["status"]) != "posted":
        return False
    gl = str(money["account_id"])
    moved = sum(line.amount for _, line in SL.entry_lines(ctx.c, str(row["entry_id"])) if line.account_id == gl)
    # A transfer's two sides, or a row linked to a payment marked paid on a bill or invoice, keep their entry's day.
    shared = len(SK.rows_of_entry(ctx.c, str(row["entry_id"]))) > 1 or str(entry["source"]) in L.DOC_SOURCES
    day, posted = str(row["txn_date"]), str(entry["entry_date"])
    # A row dated in a closed period posted on a later open day (`open_day`), or one paying several documents marked
    # paid posted on its reversals' later day (`documents._post_combined`): that shift is not a change.
    same_day = posted == day or (posted > day and (SL.closed_on(ctx.c, ctx.book.id, day) or
                                                   _combined_row(ctx, row)))
    return moved == int(row["amount"]) and (shared or same_day)


def _unpost(ctx: Ctx, row: Any, memo: str) -> None:
    """Reverse a row's entry (and any reclassification of it, `_reclass`); the other side of a transfer goes back to
    waiting too."""
    entry_id = str(row["entry_id"])
    entry = SL.entry_row(ctx.c, entry_id)
    later = SL.posted_by_source_prefix(ctx.c, ctx.book.id, "bank", f"{row['id']}:{F.RECLASS}")
    for e in [entry, *later]:
        if e is not None and str(e["status"]) == "posted" and not e["reverses_id"]:
            # Its period may be closed by now: the reversal posts on an open day, as a late item does.
            posting.reverse(ctx, str(e["id"]), day=open_day(ctx, str(e["entry_date"])), memo=memo, by=SYSTEM)
    from .stripe_post import SUMMARY, SUMMARY_ENTRY  # local: stripe_post imports this module
    for r in SK.rows_of_entry(ctx.c, entry_id):
        raw = dict(SK.loads(r["raw"]))
        changes: dict[str, object] = {"status": "new", "entry_id": None, "entry_line_id": None, "updated_at": ctx.at}
        if raw.get(SUMMARY):
            # The summary is reversed, but its closed period still holds this row's amount (see store row_effects).
            raw[SUMMARY_ENTRY] = entry_id
            if str(r["id"]) == str(row["id"]):
                raw.pop(SUMMARY)        # this row leaves its summary; its own posting comes next
            changes["raw"] = SK.dump(raw)
        SK.update(ctx.c, "fin_feed_txns", str(r["id"]), changes)
        SK.unlink_transfers(ctx.c, str(r["id"]), ctx.at)
    # A bill or invoice it paid is owed again.
    documents.feed_gone(ctx, str(row["id"]), [str(e["id"]) for e in [entry, *later] if e is not None])


def undo(ctx: Ctx, row: Any) -> None:
    """A row the provider cancelled, failed or reversed: its hold is released and its entry reversed (for a card, the
    reversal gives its use back in the period it posts in, as the books do)."""
    if row["hold_id"]:
        budgeting.release(ctx, str(row["hold_id"]))
        SB.void_settled(ctx.c, str(row["hold_id"]), ctx.at)
    if row["entry_id"]:
        _unpost(ctx, row, "the bank cancelled this transaction")
    SK.update(ctx.c, "fin_feed_txns", str(row["id"]), {"status": "ignored", "entry_id": None, "entry_line_id": None,
                                                       "updated_at": ctx.at})


def _counterpart(ctx: Ctx, row: Any, money: Any) -> Any:
    """The other side of a move between two of our money accounts: a transfer-kind row (internal or treasury
    transfer) with the opposite amount on another of them within MATCH_DAYS, not yet posted. An ambiguous match waits
    for a person."""
    others = [str(r["id"]) for r in SK.money_accounts(ctx.c, ctx.book.id) if str(r["id"]) != str(money["id"])]
    day = dates.day(str(row["txn_date"]))
    start, end = (day - timedelta(days=MATCH_DAYS)).isoformat(), (day + timedelta(days=MATCH_DAYS)).isoformat()
    found = [r for r in SK.transfer_candidates(ctx.c, others, -int(row["amount"]), start, end)
             if str(r["kind"]) in F.TRANSFER_KINDS and not r["entry_id"] and not r["card_id"] and not r["request_id"]]
    if len(found) > 1:
        # Several could be the other side: a person picks (categorize), nothing is guessed.
        event(ctx, CATEGORY_EVENT, str(row["id"]), {"feed_id": str(row["id"]), "why": "several possible transfers"},
              "ambiguous")
        return None
    return found[0] if found else None


def transfer(ctx: Ctx, row: Any, money: Any, other: Any) -> None:
    """One entry for both sides: Dr the account money went to, Cr the one it left."""
    other_money = SK.get(ctx.c, "fin_money_accounts", str(other["money_account_id"]))
    here, there = _gl(ctx, money), _gl(ctx, other_money)
    amount = int(row["amount"])
    first = sorted([str(row["id"]), str(other["id"])])
    done = posting.post_entry(ctx.c, {
        "date": open_day(ctx, max(str(row["txn_date"]), str(other["txn_date"]))), "source": "bank",
        "source_id": fresh_source(ctx, "bank", f"transfer:{first[0]}:{first[1]}"),
        "memo": f"Transfer: {money['name']} / {other_money['name']}"[:200],
        "lines": [feed_line(here.id, amount), feed_line(there.id, -amount)]}, at=ctx.at)
    mark_posted(ctx, [str(row["id"]), str(other["id"])], str(done["entry_id"]))
    out_leg, in_leg = (row, other) if amount < 0 else (other, row)
    SK.settle_pair(ctx.c, str(out_leg["id"]), str(in_leg["id"]), ctx.at)   # a Finance transfer, if one fits


# ------------------------------------------------------------------------------------------------ categorize
def seen(ctx: Ctx, who: str, row: Any) -> bool:
    from .tab import sees_card, sees_money_account
    view = ctx.view(who)
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    if money is not None and sees_money_account(ctx, view, money):
        return True
    card = SK.get(ctx.c, "fin_cards", str(row["card_id"] or "")) if row["card_id"] else None
    return card is not None and sees_card(ctx, view, card, "")


def categorize_feed(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Move a feed row to a line and tags (reverse and post again), or ignore it. A row a done reconciliation
    cleared, or one side of a transfer, stays as it is. A row that marked payments may be waiting for is not posted
    here either: that would be a second bank line beside the payments' own (feed_match links it). Ignoring posts
    nothing, so it is allowed."""
    V.only(p, ("feed_id", "account_id", "tags", "ignore"))
    row = SK.get(ctx.c, "fin_feed_txns", V.text(p, "feed_id", 80))
    if row is None or not seen(ctx, who, row):
        raise FinanceError(NOT_FOUND, "no such feed row in your view")
    if row["reconciliation_id"]:
        raise FinanceError(CONFLICT, "that row is reconciled; undo the reconciliation to change it")
    if row["entry_id"] and len(SK.rows_of_entry(ctx.c, str(row["entry_id"]))) > 1:
        raise FinanceError(CONFLICT, "that row is a transfer between your own accounts")
    amount = int(row["amount"])
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    mirrored = ctx.book.mode == "mirror"
    if mirrored:
        from .mirror import check_feed
        check_feed(ctx, row, ignore=V.flag(p, "ignore"))
    if V.flag(p, "ignore"):
        def ignore(aid: str) -> Result:
            if mirrored:
                from .mirror import ignore_feed
                ignore_feed(ctx, row)
            else:
                undo(ctx, row)
            return {"feed_id": str(row["id"]), "status": "ignored"}
        return Plan(K.default_gate(ctx.org, who, "categorize"), "Ignore a feed row", ignore,
                    facts={"amount": abs(amount), "counterparty": str(row["counterparty"])})
    if row["pending"]:
        raise FinanceError(CONFLICT, "that row is still pending at the bank")
    if money is not None and documents.may_wait(ctx, row, money):
        raise FinanceError(CONFLICT, "that row may be a payment already marked paid that waits for its bank row; "
                                     "link it with feed_match")
    account = ctx.accounts.get(V.text(p, "account_id", 80))
    if account is None or not account.active:
        raise FinanceError(NOT_FOUND, "no such account")
    tags = posting.with_team(ctx, L.tags(p.get("tags")))
    if not may_write(ctx, who, account.id, tags):
        raise denied("that line or those tags are outside your view")
    gl = _gl(ctx, money)
    t = F.FeedTxn(provider_id=str(row["provider_id"]), account_id="", amount=amount, txn_date=str(row["txn_date"]),
                  status=F.POSTED, kind=str(row["kind"]), counterparty=str(row["counterparty"]),
                  description=str(row["description"]), card_id=str(row["card_id"] or ""))

    def apply(aid: str) -> Result:
        if mirrored:
            from .mirror import write_feed
            return write_feed(ctx, row, account, tags, gl, by=who, action_id=aid)
        left = left_of(ctx, row)            # only the part no payment has applied is posted here
        taken = provider_fee(row, money)    # a sale's fee is part of its gross, which a payment settles
        if left + taken == 0:
            raise FinanceError(CONFLICT, "that bank row is already applied in full")
        if row["entry_id"]:
            _unpost(ctx, row, "re-categorized")
        holds = [str(row["hold_id"])] if row["hold_id"] and amount < 0 else []
        fee = provider_fee_line(ctx, row, money)
        lines = [feed_line(account.id, -(left + taken), tags, t.counterparty)]
        if fee is not None:
            lines.append(fee)
        entry = post_row(ctx, str(row["id"]), replace(t, amount=left), lines, gl.id, holds)
        mark_posted(ctx, [str(row["id"])], entry)
        return {"feed_id": str(row["id"]), "entry_id": entry}
    return Plan(K.default_gate(ctx.org, who, "categorize"),
                f"Post a feed row to {account.code} {account.name}", apply,
                facts={"amount": abs(amount), "counterparty": str(row["counterparty"]), "accounts": [account.code],
                       **{f"tags.{k}": v for k, v in tags.items()}})


def reversal_locked(ctx: Ctx, entry_id: str) -> bool:
    """Whether reversing this entry would unpost a row a done reconciliation cleared."""
    return any(r["reconciliation_id"] for r in SK.rows_of_entry(ctx.c, entry_id))


# ------------------------------------------------------------------------------------------------ reconcile
def _signed(money: Any, shown: int) -> int:
    """A statement balance as the books hold it: a card statement shows what is owed (positive), which is a credit
    balance on the liability, so it is negated; a bank statement shows the balance."""
    return -shown if str(money["kind"]) == "card_program" else shown


def _book_gap(ctx: Ctx, money: Any, end: str, cleared: set[str], statement: int) -> int:
    """How far the books are from the statement: the money account's whole GL balance through `end` (every entry on
    that line: feed postings, journals, an opening balance), with each uncleared row's own entries through `end`
    (an original a later correction reversed on an open day still counts there) swapped for what the statement holds
    of it: its live entry when it is being cleared, else nothing. 0 when the GL line is the statement. With no GL
    line, 0."""
    gl = str(money["account_id"] or "")
    if not gl:
        return 0
    balance = SK.balances(ctx.c, ctx.book.id, end, [gl]).get(gl, 0)
    for r in SK.row_effects(ctx.c, ctx.book.id, str(money["id"]), gl, end):
        balance -= int(r["through_amount"])
        if str(r["id"]) in cleared:
            balance += int(r["live_amount"])
    return statement - balance


def reconcile(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Tie a money account to its statement: the statement's end day and balance (from Mercury's statement when it
    has one, else typed), the cleared rows (default: every settled row through the end day not yet cleared), the
    difference and a status. Done (difference 0) locks the cleared rows; open keeps them editable."""
    V.only(p, ("money_account_id", "statement_end", "statement_balance", "cleared", "opening_balance"))
    money = SK.get(ctx.c, "fin_money_accounts", V.text(p, "money_account_id", 80))
    from .tab import sees_money_account
    if money is None or not sees_money_account(ctx, ctx.view(who), money):
        raise FinanceError(NOT_FOUND, "no such money account")
    end = V.day(p, "statement_end")
    last = SK.last_reconciliation(ctx.c, str(money["id"]))
    if last is not None and str(last["statement_end"]) >= end:
        raise FinanceError(CONFLICT, f"this account is reconciled through {last['statement_end']}")
    stmt = SK.statement(ctx.c, str(money["id"]), end)
    shown = V.opt_amount(p, "statement_balance")
    if shown is None:
        if stmt is None:
            raise invalid("no statement from the bank ends that day: enter its balance")
        shown = int(stmt["ending_balance"])
    window = {str(r["id"]): r for r in SK.feed_window(ctx.c, str(money["id"]), end)}
    if "cleared" in p:
        ids = V.texts(p, "cleared", 5000)
        missing = [i for i in ids if i not in window]
        if missing:
            raise invalid(f"{len(missing)} row(s) are not settled rows of this account through {end}, or are "
                          "already cleared")
    else:
        ids = sorted(window)
    if last is None:
        # A first reconciliation opens at the opening-balance entry the backfill posted, unless one is typed.
        typed = V.opt_amount(p, "opening_balance")
        opening = _signed(money, typed) if typed is not None else opening_amount(ctx, money, end)
    else:
        if "opening_balance" in p:
            raise invalid("the opening balance is the last reconciliation's")
        opening = int(last["cleared_total"])
    total = opening + sum(int(window[i]["amount"]) for i in ids)
    difference = _signed(money, shown) - total
    book_gap = _book_gap(ctx, money, end, set(ids), _signed(money, shown))
    waiting, waiting_total = SK.feed_outstanding(ctx.c, str(money["id"]), end)
    status = "done" if difference == 0 and book_gap == 0 else "open"

    def apply(aid: str) -> Result:
        rid = SK.insert(ctx.c, "fin_reconciliations", {
            "money_account_id": str(money["id"]), "statement_end": end, "statement_balance": shown,
            "cleared": SK.dump(ids), "difference": difference, "status": status,
            "completed_at": ctx.at if status == "done" else None, "cleared_total": total,
            "statement_id": str(stmt["id"]) if stmt is not None else None, "action_id": aid, "created_at": ctx.at,
            "created_by": who})
        if status == "done":
            SK.set_cleared(ctx.c, ids, rid, ctx.at)
        return {"reconciliation_id": rid, "status": status, "difference": difference, "cleared": len(ids),
                "book_difference": book_gap, "outstanding": waiting, "outstanding_total": waiting_total}
    return Plan(K.default_gate(ctx.org, who, "reconcile"), f"Reconcile {money['name']} through {end}", apply)


def reconcile_undo(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Undo the latest done reconciliation of an account: its rows are editable again."""
    V.only(p, ("reconciliation_id",))
    rec = SK.get(ctx.c, "fin_reconciliations", V.text(p, "reconciliation_id", 80))
    money = SK.get(ctx.c, "fin_money_accounts", str(rec["money_account_id"])) if rec is not None else None
    from .tab import sees_money_account
    if rec is None or money is None or not sees_money_account(ctx, ctx.view(who), money):
        raise FinanceError(NOT_FOUND, "no such reconciliation")
    last = SK.last_reconciliation(ctx.c, str(money["id"]))
    if last is None or str(last["id"]) != str(rec["id"]):
        raise FinanceError(CONFLICT, "only the latest done reconciliation of an account can be undone")

    def apply(aid: str) -> Result:
        ids = [str(r) for r in SK.loads_list(rec["cleared"])]
        SK.set_cleared(ctx.c, ids, None, ctx.at)
        SK.update(ctx.c, "fin_reconciliations", str(rec["id"]), {"undone_at": ctx.at, "undone_by": who})
        return {"reconciliation_id": str(rec["id"]), "undone": True}
    return Plan(K.default_gate(ctx.org, who, "reconcile_undo"), f"Undo the {rec['statement_end']} reconciliation of "
                f"{money['name']}", apply)


# ------------------------------------------------------------------------------------------------ a source's page
def _in_view(ctx: Ctx, who: str, money: Sequence[Any], lines: Sequence[L.Account] = ()) -> None:
    """Sources actions act on what the actor sees (finance.admin grants no view, §7.5): the sources view, every money
    account they touch (with its balance sheet line) and every line they map onto."""
    from .tab import sees_money_account
    if who == SYSTEM:
        return
    view = ctx.view(who)
    if not (view.everything or view.sources):
        raise denied("sources are the owner's unless granted")
    if not all(sees_money_account(ctx, view, m) for m in money) or \
            not all(view.everything or view.sees_account(a) for a in lines):
        raise FinanceError(NOT_FOUND, "no such money account")


def _connection(ctx: Ctx, who: str, p: V.Payload) -> Any:
    row = SK.get(ctx.c, "fin_connections", V.text(p, "connection_id", 80))
    if row is None:
        raise FinanceError(NOT_FOUND, "no such connection")
    _in_view(ctx, who, [m for m in SK.money_accounts(ctx.c, ctx.book.id)
                        if str(m["connection_id"] or "") == str(row["id"])])
    return row


def connection_sync(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Sync now (the Sources page): the connection's next sync is due at once, and so is the poll of each payment from
    it waiting on the bank (a rejection in Mercury lands now, not on the poll cadence); the worker is woken after
    commit."""
    V.only(p, ("connection_id",))
    row = _connection(ctx, who, p)
    if str(row["status"]) == "disconnected":
        raise FinanceError(CONFLICT, "that connection is disconnected; connect it again")

    def apply(aid: str) -> Result:
        SK.update(ctx.c, "fin_connections", str(row["id"]), {"next_sync_at": ctx.at, "updated_at": ctx.at})
        SK.payments_poll_now(ctx.c, str(row["id"]), ctx.at)
        return {"connection_id": str(row["id"]), "next_sync_at": ctx.at}
    return Plan(K.ALLOW, f"Sync {row['name']} now", apply)


def connection_disconnect(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Stop syncing a connection. Its money accounts, feed and entries stay in the books (connecting the same Mercury
    accounts again picks them up). Refused while a payment from it is in flight or a call to it is still owed, since
    only the connection can finish those. The token stays in Tico's credentials until someone deletes it there."""
    V.only(p, ("connection_id",))
    row = _connection(ctx, who, p)
    if str(row["status"]) == "disconnected":
        raise FinanceError(CONFLICT, "that connection is already disconnected")
    paying, calls = SK.connection_busy(ctx.c, str(row["id"]))
    if paying or calls:
        raise FinanceError(CONFLICT, f"{paying} payment(s) and {calls} card or recipient call(s) are still in flight "
                                     "on this connection; let them finish or cancel them first")

    def apply(aid: str) -> Result:
        SK.update(ctx.c, "fin_connections", str(row["id"]), {"status": "disconnected", "next_sync_at": None,
                                                              "updated_at": ctx.at})
        for m in SK.money_accounts(ctx.c, ctx.book.id):
            if str(m["connection_id"] or "") == str(row["id"]):
                SK.update(ctx.c, "fin_money_accounts", str(m["id"]), {"status": "disconnected", "updated_at": ctx.at})
        return {"connection_id": str(row["id"]), "status": "disconnected"}
    return Plan(K.ALLOW, f"Disconnect {row['name']}", apply)


def connection_map(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Map a money account to another line of the books (a bank to an asset, a card program to a liability), one
    money account per line. Postings from now on use the new line; entries already posted stay where they are (move
    them with a journal entry), so a later reconciliation shows any difference until they are moved."""
    V.only(p, ("money_account_id", "account_id"))
    money = SK.get(ctx.c, "fin_money_accounts", V.text(p, "money_account_id", 80))
    if money is None or str(money["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such money account")
    gl = ctx.accounts.get(V.text(p, "account_id", 80))
    if gl is None or not gl.active:
        raise FinanceError(NOT_FOUND, "no such account")
    _in_view(ctx, who, [money], [gl])
    want = "liability" if str(money["kind"]) == "card_program" else "asset"
    if gl.type != want:
        raise invalid(f"a {'card program' if want == 'liability' else 'bank account'} maps to an {want} line")
    if any(str(m["account_id"] or "") == gl.id and str(m["id"]) != str(money["id"])
           for m in SK.money_accounts(ctx.c, ctx.book.id)):
        raise FinanceError(CONFLICT, f"{gl.name} already belongs to another money account")
    old = ctx.accounts.get(str(money["account_id"] or ""))
    posted = SK.money_posted(ctx.c, str(money["id"]))

    def apply(aid: str) -> Result:
        SK.update(ctx.c, "fin_money_accounts", str(money["id"]), {"account_id": gl.id, "updated_at": ctx.at})
        return {"money_account_id": str(money["id"]), "account_id": gl.id, "code": gl.code,
                "previous_account_id": old.id if old else None, "posted_before": posted}
    return Plan(K.ALLOW, f"Map {money['name']} to {gl.code} {gl.name}" +
                (f" ({posted} posted row(s) stay on {old.code} {old.name})" if posted and old else ""), apply)


__all__ = ["connection_create", "connection_sync", "connection_disconnect", "connection_map", "ingest", "categorize_feed", "reconcile", "reconcile_undo", "set_balance", "event",
           "card_line", "undo", "reversal_locked", "new_account", "MATCH_DAYS", "card_used"]
