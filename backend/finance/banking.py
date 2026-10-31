"""Bank and card feeds into the books (spec §4.3, §8.4, §9), inside the caller's transaction and with no network:
connecting a provider, taking in a transaction (from a webhook, the Events API or a backfill page), posting it once,
re-categorizing it, and reconciling an account to its statement.

Posting a feed row:
- An IO card charge is a liability: Dr expense (the card's budget line, else Other expenses; the holder as teammate,
  the counterparty as vendor) / Cr the card program's liability. Pending is the authorization: a hold. Its posted
  update settles the hold into the entry (hold_ids), so it counts once. A refund posts the other way.
- Money moving between two of our accounts (paying the card bill, a sweep to savings) is one transfer entry for both
  rows, posted when the second side is seen.
- A bank debit that pays a Finance payment posts Dr the payment's line / Cr bank and settles the payment's hold.
- Any other debit posts to Other expenses until `categorize` moves it; an unknown credit waits as `new` and asks.
- Pending -> posted, or an amount change, updates the row in place; a changed posted row is reversed and posted
  again, never twice. A row a done reconciliation cleared is locked."""
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, cast

from . import budgeting, payments, posting
from .context import Ctx
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .core.org import SYSTEM
from .handlers import Plan, Result, may_write
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL

NEW_EVENT, CATEGORY_EVENT, DECLINED_EVENT = "finance.feed.new", "finance.feed.needs_category", "finance.card.declined"
OTHER, BANK, CARDS = "6900", "1000", "2100"
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


def _new_account(ctx: Ctx, base: str, name: str, type_: str, subtype: str, who: str) -> L.Account:
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
    V.only(p, ("provider", "credential_id", "name", "probe", "approver_email", "token_creator_email", "server_ip"))
    V.choice(p, "provider", ("mercury",))
    cred = V.text(p, "credential_id", 120)
    if any(str(r["credential_ref"] or "") == cred for r in SK.connections(ctx.c)):
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
                "approver_email": approver or None, "direct_send": False,
                "egress_ip": V.opt_text(p, "server_ip", 64) or probe.get("egress_ip") or None}
    name = V.opt_text(p, "name", 120) or "Mercury"

    def apply(aid: str) -> Result:
        cid = SK.insert(ctx.c, "fin_connections", {
            "provider": "mercury", "purpose": "bank", "credential_ref": cred, "status": "active", "name": name,
            "settings_json": SK.dump(settings), "next_sync_at": ctx.at, "created_at": ctx.at, "created_by": who})
        mapped: list[Result] = []
        for a in accounts:
            if str(a.get("status") or "active") not in ("active", "pending"):
                continue
            subtype = "savings" if "saving" in str(a.get("kind") or "").lower() else "checking"
            bank = by_code(ctx, BANK)
            used = bank is not None and any(str(r["account_id"]) == bank.id for r in SK.money_accounts(ctx.c,
                                                                                                        ctx.book.id))
            gl = bank if bank is not None and not used else _new_account(
                ctx, BANK, f"{a.get('name') or 'Mercury'} ••{a.get('last4') or ''}", "asset", "bank", who)
            mapped.append(_money(ctx, cid, a, "bank", subtype, gl, who))
        for a in credit:
            if str(a.get("status") or "active") not in ("active", "pending"):
                continue
            cards = by_code(ctx, CARDS)
            gl = cards if cards is not None and not any(
                str(r["account_id"]) == cards.id for r in SK.money_accounts(ctx.c, ctx.book.id)) else None
            if gl is None:
                gl = _new_account(ctx, CARDS, "Credit cards" if cards is None else "Mercury IO", "liability",
                                  "credit_card", who)
            mapped.append(_money(ctx, cid, {**a, "name": a.get("name") or "Mercury IO"}, "card_program", "credit", gl,
                                 who))
        return {"connection_id": cid, "money_accounts": mapped, "payments_enabled": not note,
                "approval_note": note or None}
    return Plan(K.ALLOW, f"Connect Mercury ({len(accounts)} account(s), {len(credit)} credit)", apply)


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


# ------------------------------------------------------------------------------------------------ taking in
def ingest(ctx: Ctx, connection_id: str, t: F.FeedTxn, by: str = SYSTEM) -> str | None:
    """Take in one provider transaction: insert or update its row and post what it means. The feed row id, or None
    when it is on an account this connection does not map. A replay of the same version changes nothing."""
    money = SK.money_account_by_provider(ctx.c, connection_id, t.account_id)
    if money is None:
        return None
    row = SK.feed_row(ctx.c, str(money["id"]), t.provider_id)
    if row is not None and t.version and int(row["version"]) >= t.version:
        return str(row["id"])
    card = SK.card_by_provider(ctx.c, "mercury", t.card_id) if t.card_id else None
    fields: dict[str, object] = {
        "txn_date": t.txn_date, "amount": t.amount, "counterparty": t.counterparty, "description": t.description,
        "pending": 1 if t.pending else 0, "provider_status": t.status, "kind": t.kind,
        "card_id": str(card["id"]) if card else None, "request_id": t.request_id or None, "raw_json": SK.dump(t.raw),
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
            SK.update(ctx.c, "fin_feed_txns", fid, {"raw_json": SK.dump(t.raw), "provider_status": t.status,
                                                    "version": fields["version"], "updated_at": ctx.at})
            event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid, "why": "changed after it was reconciled"},
                  f"v{t.version}")
            return fid
        SK.update(ctx.c, "fin_feed_txns", fid, fields)
    _settle(ctx, SK.get(ctx.c, "fin_feed_txns", fid), money, card, t)
    return fid


def _settle(ctx: Ctx, row: Any, money: Any, card: Any, t: F.FeedTxn) -> None:
    fid = str(row["id"])
    program = str(money["kind"]) == "card_program"
    if t.void or t.reversed:
        undo(ctx, row)
        if card is not None and t.status in ("failed", "blocked"):
            event(ctx, DECLINED_EVENT, str(card["id"]), {"card_id": str(card["id"]), "feed_id": fid}, fid)
        if t.request_id:
            payments.feed_failed(ctx, t.request_id, "returned" if t.reversed else "failed")
        return
    if t.pending:
        if program and card is not None and t.amount < 0:
            _card_hold(ctx, row, card, -t.amount)
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
    if pay is not None and t.amount < 0:
        tags = cast(dict[str, str], SK.loads(pay["tags_json"]))
        entry = _post(ctx, fid, t, [_line(str(pay["account_id"]), -t.amount, tags, t.counterparty),
                                    _line(gl.id, t.amount)], [str(pay["hold_id"])] if pay["hold_id"] else [])
        _posted(ctx, [fid], entry)
        payments.feed_settled(ctx, pay, fid)
        return
    if program and card is not None:
        account, tags = card_line(ctx, card)
        holds = [str(row["hold_id"])] if row["hold_id"] and t.amount < 0 else []
        entry = _post(ctx, fid, t, [_line(account, -t.amount, tags, t.counterparty), _line(gl.id, t.amount)], holds)
        _posted(ctx, [fid], entry)
        return
    other = _counterpart(ctx, row, money)
    if other is not None:
        _transfer(ctx, row, money, other)
        return
    if program or t.kind in F.TRANSFER_KINDS:
        return                      # one side of a move between our accounts: it posts when the other side arrives
    if t.amount < 0:
        spend = by_code(ctx, OTHER)
        if spend is None:
            return
        entry = _post(ctx, fid, t, [_line(spend.id, -t.amount, {}, t.counterparty), _line(gl.id, t.amount)], [])
        _posted(ctx, [fid], entry)
        return
    event(ctx, CATEGORY_EVENT, fid, {"feed_id": fid})


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


def _line(account_id: str, amount: int, tags: Mapping[str, str] | None = None, vendor: str = "") -> dict[str, object]:
    line: dict[str, object] = {"account_id": account_id, "amount": amount, "tags": dict(tags or {})}
    if vendor:
        line["contact_id"] = vendor[:80]
    return line


def _post(ctx: Ctx, fid: str, t: F.FeedTxn, lines: list[dict[str, object]], holds: Sequence[str]) -> str:
    """One entry for a feed row; a repost after a reversal gets a fresh source id, so it is never deduped away."""
    source = "card" if t.card else "bank"
    source_id = fid if SL.entry_by_source(ctx.c, ctx.book.id, source, fid) is None else f"{fid}:{ctx.at}"
    done = posting.post_entry(ctx.c, {"date": t.txn_date, "source": source, "source_id": source_id,
                                      "memo": (t.counterparty or t.description)[:200], "lines": lines},
                              hold_ids=holds, at=ctx.at)
    return str(done["entry_id"])


def _posted(ctx: Ctx, ids: Sequence[str], entry_id: str) -> None:
    for fid in ids:
        SK.update(ctx.c, "fin_feed_txns", fid, {"status": "posted", "entry_id": entry_id, "updated_at": ctx.at})


def _unchanged(ctx: Ctx, row: Any, money: Any) -> bool:
    """Whether the posted entry still says what the row says: its money line equals the amount (and, unless it is a
    transfer dated by its later side, the day matches)."""
    entry = SL.entry_row(ctx.c, str(row["entry_id"]))
    if entry is None or str(entry["status"]) != "posted":
        return False
    gl = str(money["account_id"])
    moved = sum(line.amount for _, line in SL.entry_lines(ctx.c, str(row["entry_id"])) if line.account_id == gl)
    shared = len(SK.rows_of_entry(ctx.c, str(row["entry_id"]))) > 1
    return moved == int(row["amount"]) and (shared or str(entry["entry_date"]) == str(row["txn_date"]))


def _unpost(ctx: Ctx, row: Any, memo: str) -> None:
    """Reverse a row's entry; the other side of a transfer goes back to waiting too."""
    entry_id = str(row["entry_id"])
    entry = SL.entry_row(ctx.c, entry_id)
    if entry is not None and str(entry["status"]) == "posted" and not entry["reverses_id"]:
        posting.reverse(ctx, entry_id, day=None, memo=memo, by=SYSTEM)
    for r in SK.rows_of_entry(ctx.c, entry_id):
        SK.update(ctx.c, "fin_feed_txns", str(r["id"]), {"status": "new", "entry_id": None, "updated_at": ctx.at})


def undo(ctx: Ctx, row: Any) -> None:
    """A row the provider cancelled, failed or reversed: its hold is released and its entry reversed."""
    if row["hold_id"]:
        budgeting.release(ctx, str(row["hold_id"]))
    if row["entry_id"]:
        _unpost(ctx, row, "the bank cancelled this transaction")
    SK.update(ctx.c, "fin_feed_txns", str(row["id"]), {"status": "ignored", "entry_id": None, "updated_at": ctx.at})


def _counterpart(ctx: Ctx, row: Any, money: Any) -> Any:
    """The other side of a move between two of our money accounts: the opposite amount on another of them within
    MATCH_DAYS, not yet part of a transfer."""
    others = [str(r["id"]) for r in SK.money_accounts(ctx.c, ctx.book.id) if str(r["id"]) != str(money["id"])]
    day = dates.day(str(row["txn_date"]))
    start, end = (day - timedelta(days=MATCH_DAYS)).isoformat(), (day + timedelta(days=MATCH_DAYS)).isoformat()
    for r in SK.transfer_candidates(ctx.c, others, -int(row["amount"]), start, end):
        if r["card_id"] or r["request_id"]:
            continue
        if r["entry_id"] and len(SK.rows_of_entry(ctx.c, str(r["entry_id"]))) > 1:
            continue
        return r
    return None


def _transfer(ctx: Ctx, row: Any, money: Any, other: Any) -> None:
    """One entry for both sides: Dr the account money went to, Cr the one it left."""
    if other["entry_id"]:
        _unpost(ctx, other, "it is one side of a transfer")
    other_money = SK.get(ctx.c, "fin_money_accounts", str(other["money_account_id"]))
    here, there = _gl(ctx, money), _gl(ctx, other_money)
    amount = int(row["amount"])
    first = sorted([str(row["id"]), str(other["id"])])
    done = posting.post_entry(ctx.c, {
        "date": max(str(row["txn_date"]), str(other["txn_date"])), "source": "bank",
        "source_id": f"transfer:{first[0]}:{first[1]}" + ("" if SL.entry_by_source(
            ctx.c, ctx.book.id, "bank", f"transfer:{first[0]}:{first[1]}") is None else f":{ctx.at}"),
        "memo": f"Transfer: {money['name']} / {other_money['name']}"[:200],
        "lines": [_line(here.id, amount), _line(there.id, -amount)]}, at=ctx.at)
    _posted(ctx, [str(row["id"]), str(other["id"])], str(done["entry_id"]))


# ------------------------------------------------------------------------------------------------ categorize
def _seen(ctx: Ctx, who: str, row: Any) -> bool:
    from .tab import sees_card, sees_money_account
    view = ctx.view(who)
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    if money is not None and sees_money_account(ctx, view, money):
        return True
    card = SK.get(ctx.c, "fin_cards", str(row["card_id"] or "")) if row["card_id"] else None
    return card is not None and sees_card(ctx, view, card, "")


def categorize_feed(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Move a feed row to a line and tags (reverse and post again), or ignore it. A row a done reconciliation
    cleared, or one side of a transfer, stays as it is."""
    V.only(p, ("feed_id", "account_id", "tags", "ignore"))
    row = SK.get(ctx.c, "fin_feed_txns", V.text(p, "feed_id", 80))
    if row is None or not _seen(ctx, who, row):
        raise FinanceError(NOT_FOUND, "no such feed row in your view")
    if row["reconciliation_id"]:
        raise FinanceError(CONFLICT, "that row is reconciled; undo the reconciliation to change it")
    if row["entry_id"] and len(SK.rows_of_entry(ctx.c, str(row["entry_id"]))) > 1:
        raise FinanceError(CONFLICT, "that row is a transfer between your own accounts")
    amount = int(row["amount"])
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    if V.flag(p, "ignore"):
        def ignore(aid: str) -> Result:
            undo(ctx, row)
            return {"feed_id": str(row["id"]), "status": "ignored"}
        return Plan(K.default_gate(ctx.org, who, "categorize", total=abs(amount)), "Ignore a feed row", ignore,
                    facts={"amount": abs(amount), "counterparty": str(row["counterparty"])})
    if row["pending"]:
        raise FinanceError(CONFLICT, "that row is still pending at the bank")
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
        if row["entry_id"]:
            _unpost(ctx, row, "re-categorized")
        holds = [str(row["hold_id"])] if row["hold_id"] and amount < 0 else []
        entry = _post(ctx, str(row["id"]), t, [_line(account.id, -amount, tags, t.counterparty),
                                               _line(gl.id, amount)], holds)
        _posted(ctx, [str(row["id"])], entry)
        return {"feed_id": str(row["id"]), "entry_id": entry}
    return Plan(K.default_gate(ctx.org, who, "categorize", total=abs(amount)),
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
        opening = _signed(money, V.opt_amount(p, "opening_balance") or 0)
    else:
        if "opening_balance" in p:
            raise invalid("the opening balance is the last reconciliation's")
        opening = int(last["cleared_total"])
    total = opening + sum(int(window[i]["amount"]) for i in ids)
    difference = _signed(money, shown) - total
    status = "done" if difference == 0 else "open"

    def apply(aid: str) -> Result:
        rid = SK.insert(ctx.c, "fin_reconciliations", {
            "money_account_id": str(money["id"]), "statement_end": end, "statement_balance": shown,
            "cleared_json": SK.dump(ids), "difference": difference, "status": status,
            "completed_at": ctx.at if status == "done" else None, "cleared_total": total,
            "statement_id": str(stmt["id"]) if stmt is not None else None, "action_id": aid, "created_at": ctx.at,
            "created_by": who})
        if status == "done":
            SK.set_cleared(ctx.c, ids, rid, ctx.at)
        return {"reconciliation_id": rid, "status": status, "difference": difference, "cleared": len(ids)}
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
        ids = [str(r) for r in SK.loads_list(rec["cleared_json"])]
        SK.set_cleared(ctx.c, ids, None, ctx.at)
        SK.update(ctx.c, "fin_reconciliations", str(rec["id"]), {"undone_at": ctx.at, "undone_by": who})
        return {"reconciliation_id": str(rec["id"]), "undone": True}
    return Plan(K.default_gate(ctx.org, who, "reconcile_undo"), f"Undo the {rec['statement_end']} reconciliation of "
                f"{money['name']}", apply)


__all__ = ["connection_create", "ingest", "categorize_feed", "reconcile", "reconcile_undo", "set_balance", "event",
           "card_line", "undo", "reversal_locked"]
