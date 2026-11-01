"""The Finance tab's money-out and control reads (spec §9, §6, §12): payments, transfers, recipients, reconciliations, statements
and finance rules. Each cuts to the viewer: a payment shows to whoever sees the line it posts to (a private line
needs full detail), and only its status to whoever made it otherwise; a reconciliation or statement with its money
account; recipients to whoever may pay; rules to whoever may write them.
Re-exported by finance/reads, the only door."""
import sqlite3
from typing import Any

from .context import load
from .core import gates as G
from .core import rights as R
from .core.errors import denied
from .store import banking as SK
from .tab import Row, page_size, payment_seen, private_cut, sees_money_account

PAYMENT_STATUSES = ("queued", "sending", "awaiting_bank_approval", "submitted", "settled", "returned", "failed", "canceled",
                    "unknown")


def may_connect(c: sqlite3.Connection, viewer: str, at: str | None = None) -> bool:
    """Whether the viewer may connect a bank (a finance admin), checked before a token is ever used."""
    ctx = load(c, at)
    return R.is_admin(ctx.org, viewer, ctx.rights(viewer))


def _masked(email: str) -> str | None:
    name, at, domain = email.partition("@")
    return f"{name[:1]}•••@{domain}" if at and name else None


def provider_users(c: sqlite3.Connection, viewer: str, at: str | None = None) -> Row:
    """The Mercury users a card can be issued to (Mercury issues cards only to its own users), as the last sync read
    them: for teammates who may issue cards. Id and name; a finance admin also sees each email, masked."""
    ctx = load(c, at)
    held = ctx.rights(viewer)
    if not R.has(ctx.org, viewer, R.CARDS, held):
        raise denied("picking a card holder is for teammates who may issue cards")
    admin = R.is_admin(ctx.org, viewer, held)
    return {"users": [{"id": str(r["provider_user_id"]), "name": str(r["name"]), "connection_id": str(r["connection_id"]),
                       "email": _masked(str(r["email"])) if admin else None, "updated_at": str(r["updated_at"])}
                      for r in SK.provider_users(c)]}


def payments(c: sqlite3.Connection, viewer: str, *, status: str | None = None, cursor: str | None = None,
             limit: int = 50, at: str | None = None) -> Row:
    """Payments the viewer may see, newest first: those posting to a line their current view covers with its tags,
    and their own. A payment to a private line the viewer has no full detail for is left out (phase 0's row rule). The
    payer of a payment their current view does not cover (a revoked grant, a private line) still sees that it exists
    and its status, with amount, recipient, memo, bill and line withheld (null or empty). A provider error shows only
    to someone who sees the payment and can act on it (the payer or a finance admin)."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    cut = set(private_cut(ctx, view))
    admin = R.is_admin(ctx.org, viewer, ctx.rights(viewer))
    most = page_size(limit)
    recs: dict[str, Any] = {}
    out: list[Row] = []
    before = cursor or ""
    nxt: str | None = None
    while len(out) < most:
        batch = SK.payments(c, status=status or "", before=before, most=200)
        if not batch:
            break
        for r in batch:
            before = str(r["created_at"])
            account = ctx.accounts.get(str(r["account_id"]))
            mine = str(r["created_by"]) == viewer
            seen = payment_seen(ctx, view, cut, r)
            if not (seen or mine):
                continue
            rid = str(r["recipient_id"])
            if seen and rid not in recs:
                recs[rid] = SK.get(c, "fin_recipients", rid)
            rec = recs.get(rid) if seen else None
            out.append({"id": str(r["id"]), "status": str(r["status"]),
                        "amount": int(r["amount"]) if seen else None,
                        "currency": str(r["currency"]), "method": str(r["method"]),
                        "memo": str(r["memo"]) if seen else "",
                        "obligation": (str(r["obligation"]) or None) if seen else None, "mode": str(r["mode"]),
                        "recipient": {"id": rid, "name": str(rec["name"]), "last4": rec["last4"],
                                      "version": int(r["recipient_version"])} if rec is not None else None,
                        "account": {"id": account.id, "code": account.code, "name": account.name}
                        if account and seen else None,
                        "money_account_id": str(r["money_account_id"]), "created_by": str(r["created_by"]),
                        "created_at": str(r["created_at"]), "settled_at": r["settled_at"],
                        "error": r["last_error"] if seen and (mine or admin) else None})
            if len(out) == most:
                nxt = before
                break
        if len(batch) < 200:
            break
    return {"payments": out, "next_cursor": nxt}


def transfers(c: sqlite3.Connection, viewer: str, *, cursor: str | None = None, limit: int = 50,
              at: str | None = None) -> Row:
    """Internal transfers (payment_create method "internal") between money accounts the viewer sees both of, newest
    first, with their status. The provider's error shows to whoever made the transfer or a finance admin."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    money = {str(m["id"]): m for m in SK.money_accounts(c, ctx.book.id) if sees_money_account(ctx, view, m)}
    admin = R.is_admin(ctx.org, viewer, ctx.rights(viewer))
    most = page_size(limit)
    rows = SK.transfers(c, list(money), before=cursor or "", most=most + 1)
    nxt = str(rows[most - 1]["created_at"]) if len(rows) > most else None

    def side(mid: str) -> Row:
        m = money[mid]
        return {"id": mid, "name": str(m["name"]), "last4": m["last4"]}
    return {"transfers": [{"id": str(r["id"]), "status": str(r["status"]), "amount": int(r["amount"]),
                           "currency": str(r["currency"]), "memo": str(r["memo"]),
                           "from": side(str(r["from_money_account_id"])), "to": side(str(r["to_money_account_id"])),
                           "created_by": str(r["created_by"]), "created_at": str(r["created_at"]),
                           "settled_at": r["settled_at"],
                           "last_error": r["last_error"] if admin or str(r["created_by"]) == viewer else None}
                          for r in rows[:most]], "next_cursor": nxt}


def recipients(c: sqlite3.Connection, viewer: str, at: str | None = None) -> Row:
    """Current recipients (last 4 only) for whoever may pay (finance.pay) or administer Finance."""
    ctx = load(c, at)
    if not R.has(ctx.org, viewer, R.PAY, ctx.rights(viewer)):
        raise denied("recipients are for teammates who may pay")
    return {"recipients": [{"id": str(r["id"]), "name": str(r["name"]), "method": str(r["method"]),
                            "last4": r["last4"], "version": int(r["version"]), "replaces_id": r["replaces_id"],
                            "known": SK.recipient_paid(c, str(r["id"])), "synced": bool(r["provider_recipient_id"]),
                            "created_at": str(r["created_at"])} for r in SK.recipients(c, 1000)]}


def _seen_money(c: sqlite3.Connection, viewer: str, money_account_id: str | None, at: str | None) -> list[str]:
    ctx = load(c, at)
    view = ctx.view(viewer)
    ids = [str(m["id"]) for m in SK.money_accounts(c, ctx.book.id) if sees_money_account(ctx, view, m)]
    return [i for i in ids if i == money_account_id] if money_account_id else ids


def reconciliations(c: sqlite3.Connection, viewer: str, *, money_account_id: str | None = None,
                    limit: int = 50, at: str | None = None) -> Row:
    """Reconciliations of the money accounts the viewer sees, newest statement first."""
    seen = _seen_money(c, viewer, money_account_id, at)
    rows = SK.reconciliations(c, seen, page_size(limit))
    return {"reconciliations": [{
        "id": str(r["id"]), "money_account_id": str(r["money_account_id"]), "statement_end": str(r["statement_end"]),
        "statement_balance": int(r["statement_balance"]), "difference": int(r["difference"]),
        "status": "undone" if r["undone_at"] else str(r["status"]), "cleared_total": int(r["cleared_total"]),
        "cleared": len(SK.loads_list(r["cleared"])),
        "statement_id": r["statement_id"], "created_by": str(r["created_by"]), "created_at": str(r["created_at"]),
        "undone_at": r["undone_at"]} for r in rows],
        "locked_through": {m: SK.locked_through(c, m) or None for m in seen}}


def statements(c: sqlite3.Connection, viewer: str, *, money_account_id: str | None = None, limit: int = 50,
               at: str | None = None) -> Row:
    """Statements (from the bank's API or typed in) for the money accounts the viewer sees."""
    seen = _seen_money(c, viewer, money_account_id, at)
    return {"statements": [{"id": str(r["id"]), "money_account_id": str(r["money_account_id"]),
                            "start_date": r["start_date"], "end_date": str(r["end_date"]),
                            "starting_balance": r["starting_balance"], "ending_balance": int(r["ending_balance"]),
                            "currency": str(r["currency"]), "source": str(r["source"])}
                           for r in SK.statements_for(c, seen, page_size(limit))]}


def rules(c: sqlite3.Connection, viewer: str, *, external_rule_id: str | None = None, at: str | None = None) -> Row:
    """Finance gate and posting rules for whoever may write them (finance.rules, or a finance admin), with their plain words,
    condition, outcome, examples, linked external rule and latest runs. `external_rule_id` lists the rules linked to
    one external rule (General Counsel's side of the link)."""
    ctx = load(c, at)
    if not R.has(ctx.org, viewer, R.RULES, ctx.rights(viewer)):
        raise denied("finance rules are for teammates who may write them")
    rows = SK.rules_for_external(c, external_rule_id) if external_rule_id else \
        [*SK.rules(c, "gate", active_only=False), *SK.rules(c, "posting", active_only=False)]
    floors: list[Row] = [] if external_rule_id else [
        {"id": fid, "kind": "floor", "priority": 0, "name": name, "words": words, "when": {"where": where},
         "outcome": {}, "examples": [], "external_rule_id": None, "owner_id": "", "active": True, "created_at": "",
         "fixed": True, "runs": []} for fid, name, words, where in G.FLOORS]
    return {"rules": floors + [{"id": str(r["id"]), "kind": str(r["kind"]), "priority": int(r["priority"]),
                       "name": str(r["name"]), "words": str(r["words"]),
                       "when": SK.loads(r["condition"]), "outcome": SK.loads(r["outcome"]),
                       "examples": SK.loads_list(r["examples"]),
                       "external_rule_id": r["external_rule_id"], "owner_id": str(r["owner_id"]), "active": bool(r["active"]),
                       "created_at": str(r["created_at"]), "fixed": str(r["id"]) in G.FIXED,
                       "runs": [{"action_id": x["action_id"], "outcome": SK.loads(x["outcome"]).get("outcome"),
                                 "at": str(x["created_at"])} for x in SK.rule_runs(c, str(r["id"]), 5)]}
                      for r in rows]}
