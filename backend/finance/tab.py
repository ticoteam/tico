"""The Finance tab's reads beyond the P&L (spec §12): the balance sheet, transactions, cards, sources and access. Each
resolves the viewer's view first and cuts rows to it; the balance sheet and sources are owner-only unless granted
(§7.5), and transactions and cards follow the lines a teammate sees. Re-exported by finance/reads, the only door."""
import base64
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, cast

from . import closing as CL
from .context import Ctx, load
from .core import dates
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND, denied, invalid
from .core.ledger import PNL_TYPES, Account, Book, descendants
from .core.org import Org
from .store import access as SA
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL
from .store import mirror as SX
from .store import raw_totals as SR

Row = dict[str, object]
MAX_PAGE = 200
SCAN = 2000                       # rows a page may look through before it stops and hands back a cursor
GROUPS = ("banks", "cards", "payments", "payroll", "bills_invoices", "tico", "accounting", "manual")
GROUP_NAMES = {"banks": "Banks", "cards": "Cards", "payments": "Payments", "payroll": "Payroll",
               "bills_invoices": "Bills and invoices", "tico": "Tico", "accounting": "Accounting", "manual": "Manual"}
OTHER_CODE = "6900"
BILLS_EMAIL_KEY = "finance_bills_email"     # the install's bills address (actions.set_bills_email writes it)


def page_size(limit: int) -> int:
    return max(1, min(int(limit), MAX_PAGE))


def encode_cursor(parts: Sequence[str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> list[str]:
    if not cursor:
        return []
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise invalid("that cursor is not one this read gave out") from None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in cast(list[object], value)):
        raise invalid("that cursor is not one this read gave out")
    return cast(list[str], value)


def _money_account_row(r: Any, accounts: Mapping[str, Account]) -> Row:
    gl = accounts.get(str(r["account_id"] or ""))
    return {"id": str(r["id"]), "kind": str(r["kind"]), "subtype": str(r["subtype"] or ""), "name": str(r["name"]),
            "provider": str(r["provider"] or ""), "last4": r["last4"], "holds_cash": bool(r["holds_cash"]),
            "balance": r["balance"], "available_balance": r["available_balance"],
            "currency": r["balance_currency"], "balance_as_of": r["balance_as_of"], "status": str(r["status"]),
            "account": {"id": gl.id, "code": gl.code, "name": gl.name} if gl else None,
            "connection_id": r["connection_id"]}


def sees_money_account(ctx: Ctx, view: VW.View, r: Any) -> bool:
    """A money account is seen with its balance sheet line (owner-only unless granted)."""
    account = ctx.accounts.get(str(r["account_id"] or ""))
    return view.everything or (account is not None and view.sees_account(account))


def feed_scope(ctx: Ctx, view: VW.View) -> tuple[list[str], list[str]]:
    """The money accounts and cards whose feed rows this viewer sees (before the private cut)."""
    money = [str(r["id"]) for r in SK.money_accounts(ctx.c, ctx.book.id) if sees_money_account(ctx, view, r)]
    accounts = _card_accounts(ctx)
    cards = [str(r["id"]) for r in SK.cards(ctx.c, most=5000)
             if sees_card(ctx, view, r, accounts.get(str(r["budget_id"] or ""), ""))]
    return money, cards


# ------------------------------------------------------------------------------------------------ balance sheet
def imported_months(c: sqlite3.Connection, ctx: Ctx, view: VW.View, first: str, last: str, basis: str) -> list[Row]:
    """The months from first to last (YYYY-MM) that hold a standing history import stating a basis other than
    `basis`: the cells a report labels as imported (§4.1). Anyone whose view covers everything gets them, since they
    read the same report lines and need the label to read the numbers; it carries months and bases, never amounts."""
    if not view.everything:
        return []
    return [{"month": str(r["month"]), "basis": str(r["basis"])}
            for r in SR.live_months(c, ctx.book.id, first, last) if str(r["basis"]) != basis]


def balance_sheet(c: sqlite3.Connection, viewer: str, as_of: str | None = None, at: str | None = None) -> Row:
    """Assets, liabilities and equity on a day (today by default), each line with what makes it up: the money accounts
    mapped to it and the kinds of entry posted to it. Owner-only unless granted; a line grant shows that whole line.
    Equity includes the P&L's running total so the sheet balances; it shows only with the whole balance sheet. A private
    account's balance shows to whoever sees the sheet (spec §7.5), but what is posted to it
    shows only with its detail."""
    ctx = load(c, at)
    day = dates.day(as_of or ctx.today).isoformat()
    closed = CL.frozen(ctx, day[:7])
    snap = CL.marker(c, ctx.book.id, day[:7]) if closed is not None else None
    if closed is not None:
        ctx = closed
    view = ctx.view(viewer)
    whole = view.everything or view.balance_sheet
    if not whole and not view.bs_lines:
        raise denied("the balance sheet is the owner's unless granted")
    cut = set(private_cut(ctx, view))
    shown = [a for a in ctx.accounts.values() if a.type not in PNL_TYPES and (whole or a.id in view.bs_lines)]
    sums = SK.balances(c, ctx.book.id, day, [a.id for a in shown])
    detail = [a.id for a in shown if a.id not in cut]
    kinds = SK.sources_by_account(c, ctx.book.id, day, detail)
    money = [r for r in SK.money_accounts(c, ctx.book.id) if str(r["account_id"] or "") in set(detail)]
    owed: dict[str, tuple[int, int]] = {}
    for r in SK.live_payments(c, ctx.book.id):          # what is on its way out, as far as this viewer sees payments
        if payment_seen(ctx, view, cut, r):
            n, total = owed.get(str(r["money_account_id"]), (0, 0))
            owed[str(r["money_account_id"])] = (n + 1, total + int(r["amount"]))
    sections: dict[str, list[Row]] = {"asset": [], "liability": [], "equity": []}
    totals = {"asset": 0, "liability": 0, "equity": 0}
    for a in sorted(shown, key=lambda x: x.code):
        sign = 1 if a.type == "asset" else -1
        under = descendants(ctx.accounts, a.id)
        balance = sign * sums.get(a.id, 0)
        totals[a.type] += balance
        sections[a.type].append({
            "account_id": a.id, "code": a.code, "name": a.name, "subtype": a.subtype, "parent_id": a.parent_id or None,
            "balance": balance, "entry_sources": sorted(kinds.get(a.id, [])),
            "sources": [{"money_account_id": str(r["id"]), "name": str(r["name"]), "provider": str(r["provider"]),
                         "last4": r["last4"], "provider_balance": r["balance"], "balance_as_of": r["balance_as_of"],
                         "due_out": {"count": owed.get(str(r["id"]), (0, 0))[0],
                                     "amount": owed.get(str(r["id"]), (0, 0))[1]}}
                        for r in money if str(r["account_id"]) in under]})
    if whole:
        pnl_ids = [a.id for a in ctx.accounts.values() if a.type in PNL_TYPES]
        earned = -sum(SK.balances(c, ctx.book.id, day, pnl_ids).values())
        sections["equity"].append({"account_id": None, "code": "", "name": "Net income to date", "subtype": "earnings",
                                   "parent_id": None, "balance": earned, "entry_sources": ["pnl"], "sources": []})
        totals["equity"] += earned
    return {"as_of": day, "assets": sections["asset"], "liabilities": sections["liability"],
            "equity": sections["equity"], "totals": totals, "scoped": not whole, "currency": ctx.book.base_currency,
            "closed_through": SL.closed_through(c, ctx.book.id), "view_version": view.version,
            # The sheet has no basis of its own: it is stated in the book's.
            "imported": imported_months(c, ctx, view, "0001-01", day[:7], ctx.book.basis),
            "snapshot": snap}


# ------------------------------------------------------------------------------------------------ transactions
def _holder_tags(ctx: Ctx, holder: str) -> dict[str, str]:
    return {"team": ctx.org.team_of.get(holder, ""), "teammate": holder}


def _other(ctx: Ctx) -> Account | None:
    return next((a for a in ctx.accounts.values() if a.code == OTHER_CODE), None)


def sees_card(ctx: Ctx, view: VW.View, card: Any, budget_account: str = "") -> bool:
    """A card follows its holder's expenses: seen by whoever sees the holder's lines on the card's budget line."""
    if view.everything:
        return True
    account = ctx.accounts.get(budget_account) or _other(ctx)
    return account is not None and view.sees(account, _holder_tags(ctx, str(card["holder"])))


def _card_accounts(ctx: Ctx) -> dict[str, str]:
    budgets = ctx.budgets()
    return {bid: b.account_id for bid, b in budgets.items()}


def _feed_row(ctx: Ctx, r: Any, money: Mapping[str, Any], lines: Mapping[str, list[Row]]) -> Row:
    m = money.get(str(r["money_account_id"]))
    return {"kind": "feed", "id": str(r["id"]), "date": str(r["txn_date"]), "amount": int(r["amount"]),
            "currency": str(r["currency"]), "counterparty": str(r["counterparty"]),
            "description": str(r["description"]), "status": str(r["status"]), "pending": bool(r["pending"]),
            "provider_kind": str(r["kind"] or ""), "card_id": r["card_id"], "entry_id": r["entry_id"],
            "reconciled": bool(r["reconciliation_id"]),
            "money_account": {"id": str(m["id"]), "name": str(m["name"]), "last4": m["last4"]} if m else None,
            "lines": lines.get(str(r["entry_id"] or ""), [])}


def private_cut(ctx: Ctx, view: VW.View) -> list[str]:
    """The private accounts this viewer has no full detail for. A row-level read never shows a line on one, nor any
    row that carries such a line's amount or payee (a bank feed row, a payment): it is left out whole, as phase 0's
    line reads do, so a salary can't be read off a feed amount or worked out by subtraction (spec §7.5)."""
    return sorted(a for a in ctx.private() if a not in view.full_detail)


def payment_seen(ctx: Ctx, view: VW.View, cut: Sequence[str] | set[str], r: Any) -> bool:
    """Whether a payment row shows to this viewer in full: not on a private line they lack full detail for, and on a
    line their current view covers with its tags. Every read of payment amounts (a list, a total) goes through it."""
    account = ctx.accounts.get(str(r["account_id"]))
    if str(r["account_id"]) in cut:
        return False
    obligation = str(r["obligation"] or "")
    if obligation.startswith("bill:") and not bill_seen(ctx, view, cut, obligation.removeprefix("bill:")):
        return False
    return view.everything or (account is not None and view.sees(account, SK.loads(r["tags_json"])))


def bill_seen(ctx: Ctx, view: VW.View, cut: Sequence[str] | set[str], bill_id: str) -> bool:
    """A bill's payment (posted to AP) shows only to who sees that bill: a payables view, and no line of the bill on
    a private account they lack full detail for (so AP never carries a hidden Payroll bill's amount or payee)."""
    from .tab_ap import sees_side
    return sees_side(ctx, view, "bill") and not hidden_bills(ctx, cut, [bill_id])


def hidden_bills(ctx: Ctx, cut: Sequence[str] | set[str], bill_ids: Sequence[str]) -> set[str]:
    """Of these bills, the ones with a line on a cut (private) account."""
    return hidden_documents(ctx, cut, "bill", bill_ids)


def hidden_documents(ctx: Ctx, cut: Sequence[str] | set[str], kind: str, doc_ids: Sequence[str]) -> set[str]:
    """Of these bills or invoices, the ones with a line on a cut (private) account."""
    if not cut or not doc_ids:
        return set()
    lines = SAP.lines(ctx.c, kind, list(dict.fromkeys(doc_ids)))
    return {b for b, xs in lines.items() if any(str(x["account_id"] or "") in cut for x in xs)}


def private_feed(ctx: Ctx, view: VW.View, rows: Sequence[Any]) -> set[str]:
    """Ids of the feed rows that stand for a private line this viewer has no full detail for: posted to an entry with
    such a line, or the bank side of a payment to one."""
    cut = private_cut(ctx, view)
    if not cut or not rows:
        return set()
    entries = SL.entries_touching(ctx.c, sorted({str(r["entry_id"]) for r in rows if r["entry_id"]}), cut)
    out = {str(r["id"]) for r in rows if r["entry_id"] and str(r["entry_id"]) in entries}
    bills: dict[str, list[str]] = {}         # feed row id: the bills it is the bank side of
    # The row's payment, by its request or by the row it settled on (a lost send matched by its note has no request).
    pays: dict[str, list[Any]] = {}
    for pay in SK.payments_by_txn(ctx.c, [str(r["id"]) for r in rows if str(r["id"]) not in out]):
        pays.setdefault(str(pay["provider_txn_id"]), []).append(pay)
    for r in rows:
        fid = str(r["id"])
        if fid in out:
            continue
        mine = list(pays.get(fid, []))
        by_request = SK.payment_by_request(ctx.c, str(r["request_id"])) if r["request_id"] else None
        if by_request is not None:
            mine.append(by_request)
        for pay in mine:
            if str(pay["account_id"]) in cut:
                out.add(fid)
            elif str(pay["obligation"] or "").startswith("bill:"):
                bills.setdefault(fid, []).append(str(pay["obligation"]).removeprefix("bill:"))
        if fid in out:
            continue
        # Bills it settled, whatever the settlement came from (the row, its payment or the entry they share). Voided
        # settlements count: reversing a "Pays bill" entry must not make its row's amounts visible again.
        found = [*SAP.settlements_from(ctx.c, "feed", fid, voided=True),
                 *(SAP.settlements_by_entry(ctx.c, str(r["entry_id"]), voided=True) if r["entry_id"] else [])]
        got = [str(s["to_id"]) for s in found if s["to_kind"] == "bill"]
        if got:
            bills.setdefault(fid, []).extend(got)
    hidden = hidden_bills(ctx, cut, [b for xs in bills.values() for b in xs])
    out |= {fid for fid, xs in bills.items() if hidden & set(xs)}
    return out


def feed_origin(r: Any) -> str:
    """The feed row a bank or card entry (or its reversal) was posted for, from its source id ("" for other entries):
    so an earlier posting of a feed row that now stands for a private line can't show its amount either."""
    return str(r["source_id"]).split(":")[0] if str(r["source"]) in ("bank", "card") else ""


def private_origins(ctx: Ctx, view: VW.View, origins: Sequence[str]) -> set[str]:
    """Which of these feed rows (by id) stand for a private line this viewer has no full detail for (private_feed).
    The one rule every read of settled private money uses: the P&L's lines (settled_lines) and a budget's actual, card
    and held amounts (budgeting)."""
    ids = sorted({str(x) for x in origins} - {""})
    if not ids or not private_cut(ctx, view):
        return set()
    return private_feed(ctx, view, list(SK.feed_rows(ctx.c, ids).values()))


def settled_lines(ctx: Ctx, view: VW.View, a: date, z: date, account_ids: Sequence[str], basis: str) -> list[Any]:
    """The lines a totals read takes out before it sums them (spec §7.5): on these accounts in the window, of the bank
    and card entries posted for a feed row that stands for a private line this viewer has no full detail for. Those
    are the row's posting, its "Pays bill" reclass and any reversal, which copies its origin. The cash basis takes
    only the entries the cash view takes as posted (Entry.cash_view), since the others are corrected out of its cells."""
    if not account_ids or not private_cut(ctx, view):
        return []
    rows = SL.bank_lines(ctx.c, ctx.book.id, a.isoformat(), z.isoformat(), account_ids)
    cut = private_origins(ctx, view, [feed_origin(r) for r in rows])
    return [r for r in rows if feed_origin(r) in cut and (basis != "cash" or str(r["basis"]) == "both")]


def pays_labels(ctx: Ctx, view: VW.View, rows: Sequence[Any]) -> tuple[dict[str, str], set[str]]:
    """For these lines (entry_id, source, source_id, reverses_id): what a "Pays bill" entry shows as its line, the
    documents it pays ("Bill AC-105") rather than the line it moves the spend off (that spend now counts on the bill's
    own lines), to a viewer who can open those documents. Also the entries naming a bill or invoice this viewer can't
    open, whose memo and description must not show: its own entry, an entry that ever paid it (a voided settlement
    still names it) and either one's reversal."""
    from .tab_ap import sees_side
    cut = set(private_cut(ctx, view))
    out: dict[str, str] = {}
    named: dict[str, list[tuple[str, str]]] = {}
    ids = {str(r["entry_id"]) for r in rows} | {str(r["reverses_id"]) for r in rows if r["reverses_id"]}
    paid = SAP.documents_paid_by(ctx.c, sorted(ids), voided=True)
    for r in rows:
        eid = str(r["entry_id"])
        mine = named.setdefault(eid, [])
        if str(r["source"]) in ("bill", "invoice"):
            mine.append((str(r["source"]), str(r["source_id"]).split(":")[0]))
        for d in [*paid.get(eid, []), *paid.get(str(r["reverses_id"] or ""), [])]:
            mine.append((str(d["to_kind"]), str(d["to_id"])))
    kinds = {k for docs in named.values() for k, _ in docs}
    hidden = {(k, i) for k in kinds if sees_side(ctx, view, k)
              for i in hidden_documents(ctx, cut, k, sorted({i for docs in named.values() for x, i in docs if x == k}))}
    closed = {eid for eid, docs in named.items()
              if any(not sees_side(ctx, view, k) or (k, i) in hidden for k, i in docs)}
    for eid in named:
        live = [d for d in paid.get(eid, []) if d["voided_at"] is None]
        if live and eid not in closed and len({str(d["to_kind"]) for d in live}) == 1:
            out[eid] = f"{str(live[0]['to_kind']).capitalize()} " + \
                ", ".join(str(d["number"] or d["to_id"])[:40] for d in live)
    return out, closed


def _visible_lines(ctx: Ctx, view: VW.View, entry_ids: Sequence[str]) -> dict[str, list[Row]]:
    """The P&L lines of these entries the viewer sees (private lines without full detail never show), each vendor
    with the name it shows as (tab_ap.vendor_labels)."""
    from .tab_ap import vendor_labels
    out: dict[str, list[Row]] = {}
    cut = set(private_cut(ctx, view))
    for eid in entry_ids:
        for r, line in SL.entry_lines(ctx.c, eid):
            account = ctx.accounts.get(line.account_id)
            if account is None or account.type not in PNL_TYPES:
                continue
            if account.id in cut:
                continue
            if view.sees(account, line.tags):
                out.setdefault(eid, []).append({"line_id": str(r["id"]), "account_id": account.id,
                                                "account": account.name, "amount": line.amount,
                                                "vendor": line.contact_id, "tags": dict(line.tags)})
    names = vendor_labels(ctx, view, [str(x["vendor"]) for xs in out.values() for x in xs])
    for xs in out.values():
        for x in xs:
            x["vendor_name"] = names.get(str(x["vendor"]), "")
    return out


Fetch = Callable[[tuple[str, str] | None, int], list[Any]]
Visible = Callable[[list[Any]], list[tuple[tuple[str, str], Row]]]


def _stream(fetch: Fetch, visible: Visible, mark: tuple[str, str] | None, most: int
            ) -> tuple[list[tuple[tuple[str, str], Row]], tuple[str, str] | None, bool]:
    """Up to `most` visible rows after `mark` (newest first), where the scan stopped, and whether the source ran out.
    It looks through at most SCAN rows, so a viewer who sees few of many rows still gets an answer quickly."""
    out: list[tuple[tuple[str, str], Row]] = []
    scanned, at = 0, mark
    while len(out) < most and scanned < SCAN:
        batch = fetch(at, most * 2)
        if not batch:
            return out, at, True
        scanned += len(batch)
        out += visible(batch)
        at = _key_of(batch[-1])
        if len(batch) < most * 2:
            return out[:most], at, len(out) <= most
    return out[:most], at, False


def _org_keeps(org: Callable[[str, str], bool], lines: Sequence[Row]) -> bool:
    """A feed row passes an org filter when a line of its entry the viewer sees does; with none, as an unattributed
    line would."""
    if not lines:
        return org("", "")
    for x in lines:
        tags = cast(Mapping[str, str], x.get("tags") or {})
        if org(str(tags.get("team") or ""), str(tags.get("teammate") or "")):
            return True
    return False


def _key_of(r: Any) -> tuple[str, str]:
    return (str(r["txn_date"]), str(r["id"])) if "txn_date" in r.keys() else (str(r["entry_date"]), str(r["line_id"]))


def transactions(c: sqlite3.Connection, viewer: str, *, source: str = "all", money_account_id: str | None = None,
                 status: str | None = None, account_id: str | None = None, start: str | None = None,
                 end: str | None = None, q: str | None = None, cursor: str | None = None, limit: int = 50,
                 at: str | None = None, org: Callable[[str, str], bool] | None = None) -> Row:
    """Feed rows and entries as plain rows, newest first. A feed row shows to whoever sees its money account or
    follows its card (with the lines of its posted entry they see); an entry shows as the P&L lines the viewer sees,
    unless a feed row they see stands for it. A feed row standing for a private line the viewer has no full detail for
    is left out whole (`private_feed`). Filter by money account, status (feed rows), line, dates, a search and `org`
    (keeps a line's team and teammate); `next` is the cursor for the following page."""
    ctx = load(c, at)
    from .tab_ap import vendor_labels
    view = ctx.view(viewer)
    if source not in ("all", "feed", "entry"):
        raise invalid("source is all|feed|entry")
    if status and status not in ("new", "matched", "posted", "ignored", "pending"):
        raise invalid("status is new|matched|posted|ignored|pending")
    most = page_size(limit)
    start_day = dates.day(start).isoformat() if start else ""
    end_day = dates.day(end).isoformat() if end else ""
    text = (q or "").strip()[:100]
    marks = decode_cursor(cursor) + [""] * 6
    marks_f = (marks[0], marks[1]) if marks[0] else None
    marks_l = (marks[2], marks[3]) if marks[2] else None
    money = {str(r["id"]): r for r in SK.money_accounts(c, ctx.book.id)}
    if money_account_id and money_account_id not in money:
        raise FinanceError(NOT_FOUND, "no such money account")
    seen_money = {i for i, r in money.items() if sees_money_account(ctx, view, r)}
    budget_accounts = _card_accounts(ctx)
    seen_cards = {str(r["id"]) for r in SK.cards(c, most=5000)
                  if sees_card(ctx, view, r, budget_accounts.get(str(r["budget_id"] or ""), ""))}
    wanted: set[str] = set()
    if account_id:
        root = ctx.accounts.get(account_id)
        if root is None or not view.sees_account(root):
            raise FinanceError(NOT_FOUND, "no such account")
        wanted = descendants(ctx.accounts, account_id)

    def feed_seen(r: Any) -> bool:
        return str(r["money_account_id"]) in seen_money or str(r["card_id"] or "") in seen_cards

    def fetch_feed(mark: tuple[str, str] | None, n: int) -> list[Any]:
        accounts = [money_account_id] if money_account_id else sorted(money)
        return SK.feed_page(c, before=mark, most=n, money_account_ids=accounts, status=status or "",
                            start=start_day, end=end_day, q=text)

    def show_feed(batch: list[Any]) -> list[tuple[tuple[str, str], Row]]:
        rows = [r for r in batch if feed_seen(r)]
        cut = private_feed(ctx, view, rows)
        rows = [r for r in rows if str(r["id"]) not in cut]
        lines = _visible_lines(ctx, view, [str(r["entry_id"]) for r in rows if r["entry_id"]])
        out: list[tuple[tuple[str, str], Row]] = []
        for r in rows:
            mine = lines.get(str(r["entry_id"] or ""), [])
            # With a line filter, the org filter looks at the same lines: a split passes only when one line is both.
            pick = [x for x in mine if x["account_id"] in wanted] if wanted else mine
            if wanted and not pick:
                continue
            if org is not None and not _org_keeps(org, pick):
                continue
            out.append((_key_of(r), _feed_row(ctx, r, money, lines)))
        return out

    hidden = set(private_cut(ctx, view))
    seen_lines = [a for a in ctx.accounts.values() if view.sees_account(a) and (not wanted or a.id in wanted) and
                  a.id not in hidden]
    # Every entry shows (§12): by its income and expense lines, or by its balance sheet lines when it has no others.
    # Asked for by line, a balance sheet line shows all of its own.
    line_ids = [a.id for a in seen_lines if wanted or a.type in PNL_TYPES]
    balance_ids = [a.id for a in seen_lines if not wanted and a.type not in PNL_TYPES]

    def fetch_lines(mark: tuple[str, str] | None, n: int) -> list[Any]:
        return SK.lines_page(c, ctx.book.id, account_ids=line_ids, before=mark, most=n, start=start_day, end=end_day,
                             q=text, balance_only_ids=balance_ids)

    def show_lines(batch: list[Any]) -> list[tuple[tuple[str, str], Row]]:
        tags = SL.line_tags(c, [str(r["line_id"]) for r in batch])
        stands = SK.feed_by_entry(c, sorted({str(r["entry_id"]) for r in batch}))
        origins = {str(r["line_id"]): feed_origin(r) for r in batch}
        history = SK.feed_rows(c, sorted({o for o in origins.values() if o}))
        cut = private_feed(ctx, view, [*stands.values(), *history.values()])
        names = vendor_labels(ctx, view, [str(r["contact_id"]) for r in batch])
        pays, closed = pays_labels(ctx, view, batch)
        out: list[tuple[tuple[str, str], Row]] = []
        for r in batch:
            account = ctx.accounts[str(r["account_id"])]
            tagged = tags.get(str(r["line_id"]), {})
            fed = stands.get(str(r["entry_id"]))
            shown = fed is not None and feed_seen(fed) and str(fed["id"]) not in cut
            if not view.sees(account, tagged) or shown or origins[str(r["line_id"])] in cut or \
                    (money_account_id and (fed is None or str(fed["money_account_id"]) != money_account_id)) or \
                    (org is not None and not org(tagged.get("team", ""), tagged.get("teammate", ""))):
                continue
            if text and str(r["entry_id"]) in closed and text.casefold() not in str(r["contact_id"]).casefold():
                continue        # matched only on the memo or description it can't show
            out.append((_key_of(r), {"kind": "entry", "id": str(r["line_id"]), "date": str(r["entry_date"]),
                                     "amount": int(r["amount"]), "currency": ctx.book.base_currency,
                                     "entry_id": str(r["entry_id"]), "account_id": account.id,
                                     "account": pays.get(str(r["entry_id"]), account.name)
                                     if origins[str(r["line_id"])] else account.name,
                                     "vendor": str(r["contact_id"]),
                                     "vendor_name": names.get(str(r["contact_id"]), ""),
                                     "description": "" if str(r["entry_id"]) in closed else str(r["description"]),
                                     "memo": "" if str(r["entry_id"]) in closed else str(r["memo"]),
                                     "source": str(r["source"]),
                                     "reversed": str(r["entry_status"]) == "reversed", "tags": tagged}))
        return out

    streams: list[tuple[str, list[tuple[tuple[str, str], Row]], tuple[str, str] | None, bool]] = []
    if source in ("all", "feed") and marks[4] != "1":
        streams.append(("feed", *_stream(fetch_feed, show_feed, marks_f, most)))
    else:
        streams.append(("feed", [], marks_f, True))
    if source in ("all", "entry") and marks[5] != "1" and not status:
        streams.append(("entry", *_stream(fetch_lines, show_lines, marks_l, most)))
    else:
        streams.append(("entry", [], marks_l, True))
    merged = sorted(((key, name, row) for name, rows, _, _ in streams for key, row in rows), reverse=True)
    page = merged[:most]
    cursor_parts: list[str] = []
    flags: list[str] = []
    for name, rows, scan_at, ran_out in streams:
        taken = [key for key, n, _ in page if n == name]
        if taken:
            mark, done = taken[-1], ran_out and len(taken) == len(rows)
        elif rows:
            mark, done = (marks_f if name == "feed" else marks_l), False
        else:
            mark, done = scan_at, ran_out
        cursor_parts += list(mark or ("", ""))
        flags.append("1" if done else "0")
    more = "0" in flags
    return {"rows": [row for _, _, row in page], "next": encode_cursor([*cursor_parts, *flags]) if more else None,
            "currency": ctx.book.base_currency, "view_version": view.version}


# ------------------------------------------------------------------------------------------------ cards
def _frozen_by(r: Any) -> str | None:
    """Why a frozen card is frozen: `budget` (its budget's cutoff, lifted once the budget has room) or `person`."""
    return str(r["freeze_by"]) if str(r["status"]) == "frozen" and r["freeze_by"] else None


def card_row(ctx: Ctx, r: Any, spent: int | None = None, allocation: int | None = None,
             excluded: bool = False) -> Row:
    """A card as anyone sees it: last 4 only, never the number or CVC (Tico never has them). `excluded` marks a spent
    total with its settled private part taken out, as the budget rows are marked."""
    budget = ctx.budgets().get(str(r["budget_id"] or ""))
    return {"id": str(r["id"]), "provider": str(r["provider"]), "holder": str(r["holder"]),
            "nickname": str(r["nickname"] or ""), "last4": r["last4"], "kind": str(r["kind"]),
            "status": str(r["status"]), "frozen_by": _frozen_by(r), "limit": r["limit_amount"],
            "limit_interval": r["limit_interval"],
            "provider_limit": r["provider_limit"], "sync_status": str(r["sync_status"] or ""),
            "currency": str(r["currency"]), "money_account_id": r["money_account_id"],
            "budget": {"id": budget.id, "name": budget.name} if budget else None, "allocation": allocation,
            "spent": spent, "excluded": excluded, "expires_at": r["expires_at"], "created_at": str(r["created_at"])}


def _card_spend_seen(cut: set[str], lines: Sequence[Any]) -> bool:
    """Whether a card's spent total may show. Transactions shows a followed card's feed rows (their amounts) whatever
    line they post to, but leaves a row out whole when it posts to a private line the viewer lacks full detail for;
    so a total holding such a line is not shown (it would give that amount away)."""
    return not any(str(x["account_id"]) in cut for x in lines)


SAVED_ON_MOST = 10      # vendor accounts shown per card; `vendor=` on the cards read finds every one


def _saved_on(c: sqlite3.Connection, card_ids: Sequence[str]) -> dict[str, list[Row]]:
    """The vendor accounts each card is saved on now (the first SAVED_ON_MOST, oldest first): the vendor (its key, for
    `vendor=`), its label, who saved it and when."""
    out: dict[str, list[Row]] = {}
    for r in SK.card_saves(c, card_ids):
        saved = out.setdefault(str(r["card_id"]), [])
        if len(saved) < SAVED_ON_MOST:
            saved.append({"vendor": str(r["vendor_key"]), "label": str(r["vendor_label"]),
                          "saved_by": str(r["saved_by"]), "saved_at": str(r["saved_at"])})
    return out


def cards(c: sqlite3.Connection, viewer: str, *, status: str | None = None, holder: str | None = None,
          vendor: str | None = None, q: str | None = None, cursor: str | None = None, limit: int = 100,
          at: str | None = None) -> Row:
    """The cards the viewer follows (their own, their teams', or all for the owner), with each card's allocation, what
    it spent this budget period and the vendor accounts it is saved on (`saved_on`). `vendor` keeps the cards saved on
    that vendor (a contact's id or a name); `q` matches a card's nickname, last 4 or holder."""
    from . import budgeting
    from .store import budgets as SB
    ctx = load(c, at)
    view = ctx.view(viewer)
    if status and status not in ("active", "frozen", "cancelled"):
        raise invalid("status is active|frozen|cancelled")
    most = page_size(limit)
    marks = decode_cursor(cursor)
    after = marks[0] if marks else ""
    accounts = _card_accounts(ctx)
    cut = set(private_cut(ctx, view))
    saved_at = SK.cards_saved_at(c, ctx.book.id, SAP.vendor_keys(c, ctx.book.id, vendor)) if vendor else None
    text = (q or "").strip().lower()
    out: list[Row] = []
    scanned = 0
    today = dates.day(ctx.today)
    while len(out) <= most and scanned < SCAN:
        batch = SK.cards(c, status=status or "", holder=holder or "", before=after, most=most * 2)
        if not batch:
            break
        scanned += len(batch)
        for r in batch:
            after = str(r["id"])
            if saved_at is not None and str(r["id"]) not in saved_at:
                continue
            if text and text not in f"{r['nickname']} {r['last4'] or ''} {r['holder']}".lower():
                continue
            if not sees_card(ctx, view, r, accounts.get(str(r["budget_id"] or ""), "")):
                continue
            alloc = spent = None
            excluded = False
            bid = str(r["budget_id"] or "")
            if bid in ctx.budgets():
                row = SB.allocation(c, bid, "card", str(r["id"]))
                alloc = int(row["amount"]) if row else None
                st = budgeting.state(ctx, bid, today)
                spent = SB.target_used(c, budgeting.subtree(ctx, bid), st.period_start, st.period_end,
                                       f"card:{r['id']}", absorbed=True)
                if cut and not _card_spend_seen(cut, SB.card_lines(c, str(r["id"]), st.period_start,
                                                                   st.period_end)):
                    spent = None
                elif cut:       # the settled private part comes out, as the P&L's and the budgets' do
                    netted = budgeting.Netted(view)
                    spent -= budgeting.settled_used(ctx, netted, budgeting.subtree(ctx, bid),
                                                    st.period_start, st.period_end, f"card:{r['id']}", absorbed=True)
                    excluded = netted.excluded
            out.append(card_row(ctx, r, spent, alloc, excluded))
            if len(out) > most:
                break
        if len(batch) < most * 2:
            break
    more = len(out) > most or scanned >= SCAN
    page = out[:most]
    saves = _saved_on(c, [str(row["id"]) for row in page])
    for row in page:
        row["saved_on"] = saves.get(str(row["id"]), [])
    nxt = encode_cursor([str(page[-1]["id"]) if len(out) > most else after]) if more and (page or after) else None
    return {"cards": page, "next": nxt, "view_version": view.version}


# ------------------------------------------------------------------------------------------------ sources
def _access_to(ctx: Ctx, account_id: str) -> list[Row]:
    """Who besides the owner sees a balance sheet line: grants of everything, the balance sheet, or that line."""
    out: list[Row] = [{"who": ctx.org.owner, "via": "owner"}] if ctx.org.owner else []
    for g in SA.grants(ctx.c):
        if not VW.live(g, ctx.at):
            continue
        if g.what in ("everything", "balance_sheet", "sources") or (g.what == "bs_lines" and any(
                account_id in descendants(ctx.accounts, x) for x in g.account_ids)):
            out.append({"who": g.subject, "subject_kind": g.subject_kind, "via": g.what, "grant_id": g.id,
                        "expires_at": g.expires_at or None})
    return out


def _sync(conn: Any | None) -> Row | None:
    if conn is None:
        return None
    settings = SK.loads(conn["settings_json"])
    return {"connection_id": str(conn["id"]), "provider": str(conn["provider"]), "status": str(conn["status"]),
            "last_ok_at": conn["last_ok_at"], "last_sync_at": conn["last_sync_at"], "last_error": conn["last_error"],
            "egress_ip": settings.get("egress_ip")}


def _books_mirror(c: sqlite3.Connection, book: Book, cid: str, status: str) -> str:
    """Where a books connection stands: `reconnect` while its grant is not working, `native` unless it is the book's
    mirror, and `exporting` until the book's export has gone out."""
    if status == "reconnect":
        return "reconnect"
    if book.mode != "mirror" or book.mirror_connection_id != cid:
        return "native"
    export = SX.as_map(SX.mirror_state(c, cid).get("export"))
    return "mirror" if not export or export.get("done") else "exporting"

def _direct_on(c: sqlite3.Connection) -> bool:
    """The owner's company switch for direct send (payments.DIRECT_KEY)."""
    from .payments import DIRECT_KEY
    return SL.setting(c, DIRECT_KEY) is True


def sources(c: sqlite3.Connection, viewer: str, *, group: str | None = None, gateway: bool = False,
            at: str | None = None) -> Row:
    """Every source of the books, grouped (banks, cards, payments, payroll, bills and invoices, Tico, accounting,
    manual) with a count per group, so a card program with 500 cards is one row. Each source shows its account
    mapping, sync state and who can see it. Tico's own sources show whenever they exist, entries or not: the Gateway
    while it is connected (`gateway`), the bills inbox while it has an address, and Tico invoices always. Owner-only
    unless granted `sources` (or everything)."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not (view.everything or view.sources):
        raise denied("sources are the owner's unless granted")
    if group and group not in GROUPS:
        raise invalid(f"group is {'|'.join(GROUPS)}")
    conns = {str(r["id"]): r for r in SK.connections(c)}
    money = SK.money_accounts(c, ctx.book.id)
    cards_by = SK.card_counts(c)
    feed_by = SK.feed_counts(c)
    entries = SK.entry_counts(c, ctx.book.id)
    docs = SK.document_counts(c, ctx.book.id)
    rows: dict[str, list[Row]] = {g: [] for g in GROUPS}
    for r in money:
        base = _money_account_row(r, ctx.accounts)
        conn = conns.get(str(r["connection_id"] or ""))
        gl = str(r["account_id"] or "")
        item: Row = {**base, "sync": _sync(conn), "feed": feed_by.get(str(r["id"]), {}),
                     "access": _access_to(ctx, gl) if gl else []}
        kind = str(r["kind"])
        if kind == "card_program":
            item["cards"] = cards_by.get(str(r["id"]), {})
            rows["cards"].append(item)
        elif kind == "stripe":
            rows["payments"].append(item)
        elif kind == "gateway_credit":
            rows["tico"].append(item)
        else:
            rows["banks"].append(item)
    for r in conns.values():
        if str(r["purpose"]) == "payments" or str(r["provider"]) == "mercury":
            s = SK.loads(r["settings_json"])
            rows["payments"].append({"id": str(r["id"]), "kind": "connection", "name": str(r["name"] or r["provider"]),
                                     "provider": str(r["provider"]), "sync": _sync(r),
                                     "payments": {"mode": "approval_queue",
                                                  "eligible": bool(s.get("approval_eligible")),
                                                  "enabled": bool(s.get("payments_enabled")),
                                                  "direct_send": _direct_on(c),
                                                  "why_not": s.get("approval_note")}})
        elif str(r["purpose"]) == "books":
            cid = str(r["id"])
            rows["accounting"].append({"id": cid, "kind": "connection", "name": str(r["name"] or r["provider"]),
                                       "provider": str(r["provider"]), "sync": _sync(r),
                                       "mirror": _books_mirror(c, ctx.book, cid, str(r["status"])),
                                       "outbox": SX.outbox_counts(c, cid)})
    used = entries.get("usage", 0)
    if gateway or used:
        rows["tico"].append({"id": "tico-usage", "kind": "usage", "name": "Tico Gateway", "entries": used,
                             "connected": gateway})
    address = SL.setting(c, BILLS_EMAIL_KEY)
    if isinstance(address, str) and address:
        rows["bills_invoices"].append({"id": "bills-inbox", "kind": "inbox", "name": "Bills inbox", "address": address,
                                       "bills": docs["bills"]})
    elif docs["bills"]:
        rows["bills_invoices"].append({"id": "bills", "kind": "documents", "name": "Bills", "bills": docs["bills"]})
    rows["bills_invoices"].append({"id": "documents", "kind": "documents", "name": "Tico invoices",
                                   "invoices": docs["invoices"]})
    manual = {k: entries.get(k, 0) for k in ("manual", "import")}
    if any(manual.values()):
        rows["manual"].append({"id": "manual", "kind": "entries", "name": "Journal entries and imports", **manual})
    groups = [{"group": g, "name": GROUP_NAMES[g], "count": len(rows[g]), "rows": rows[g] if group in (None, g) else []}
              for g in GROUPS]
    return {"groups": groups, "view_version": view.version}


# ------------------------------------------------------------------------------------------------ access
def _default_of(org_led: bool, defaults: VW.Defaults, owner: bool) -> str:
    if owner:
        return "everything"
    if org_led and defaults.manager == "team_expenses":
        return "team_expenses"
    return "own_expenses" if org_led or defaults.member == "own_expenses" else "none"


def default_team(org: Org, manager: str) -> str | None:
    """The team a manager's default names ("Engineering expenses"): the single top team of what they cover, the teams
    they lead (with the teams under them) and the team of each report, when that top is a team they lead. Their team
    slice then covers everyone on it, so the name is true. A team they only have reports in is never named: a manager
    who leads nothing gets None, and so does a sub-team lead with a report in the parent team. None too when what they
    cover spans several teams, or a report sits on no team."""
    led, mates = org.led_by(manager), org.reports(manager)
    covered = set(led)
    for mate in mates:
        team = org.team_of.get(mate, "")
        if team not in org.teams:
            return None
        covered.add(team)
    tops = {t for t in covered if not covered.intersection(org.chain(t)[1:])}
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    return top if top in led else None


def _grant_row(g: VW.Grant, ctx: Ctx) -> Row:
    return {"id": g.id, "subject_kind": g.subject_kind, "subject": g.subject, "what": g.what, "whose": g.whose,
            "whose_team": g.whose_team or None, "account_ids": sorted(g.account_ids), "new_members": g.new_members,
            "granted_by": g.granted_by, "expires_at": g.expires_at or None, "status": g.status,
            "live": VW.live(g, ctx.at)}


def access(c: sqlite3.Connection, viewer: str, at: str | None = None) -> Row:
    """Who can see what in Finance (spec §7.5): the org chart with each teammate's default and grants, private lines
    and the defaults. The owner, or anyone granted everything, sees everyone, with rights and private lines; a finance
    admin (`finance.admin`) adds no view. A manager sees their own row, the teammates in the teams they manage (nested)
    and everyone who reports to them, with defaults and grants but no rights and no private lines. Anyone else sees
    only their own row. The owner may view as anyone."""
    ctx = load(c, at)
    org = ctx.org
    owner = bool(org.owner) and viewer == org.owner
    admin = ctx.view(viewer).everything
    led = frozenset[str]().union(*(org.below(t) for t in org.led_by(viewer)))
    if admin:
        shown = sorted(org.humans | org.bots)
    else:
        under = org.members(led) | org.reports(viewer)
        shown = sorted({viewer} | {m for m in under if m in org.humans or m in org.bots})
    # The teams the viewer sees: the ones they manage, and the teams of the teammates shown. A default names a team only
    # from this set, so a teammate's row never gives away a team the viewer can't see.
    shown_teams = {org.team_of.get(m, "") for m in shown}
    visible = {t.id for t in org.teams.values() if admin or t.id in led or t.id in shown_teams}
    defaults = SA.defaults(c)
    grants = SA.grants(c, "active") + SA.grants(c, "suspended")
    teammates: list[Row] = []
    for m in shown:
        mine = [g for g in grants if VW.applies(org, g, m)]
        # Rights are admin-only, except a teammate's own.
        rights = sorted(ctx.rights(m)) if admin or m == viewer else None
        default = _default_of(org.manages(m), defaults, bool(org.owner) and m == org.owner)
        team = default_team(org, m) if default == "team_expenses" else None
        teammates.append({"teammate": m, "kind": "bot" if m in org.bots else "person",
                          "team": org.team_of.get(m) or None, "reports_to": org.reports_to.get(m) or None,
                          "default": default,
                          "default_team": team if team in visible else None,
                          "grants": [_grant_row(g, ctx) for g in mine], "rights": rights,
                          "may_grant": _may_grant(ctx, viewer, m, defaults)})
    teams = [{"id": t.id, "name": t.name, "parent": t.parent or None, "lead": t.lead or None}
             for t in sorted(org.teams.values(), key=lambda x: x.name) if t.id in visible]
    private = [{"account_id": a, "name": ctx.accounts[a].name if a in ctx.accounts else a,
                "others_see": p.others_see, "full_detail": sorted(p.full_detail) if owner else None}
               for a, p in sorted(ctx.private().items())] if admin else []
    return {"teams": teams, "teammates": teammates, "private_lines": private,
            "defaults": {"manager": defaults.manager, "member": defaults.member,
                         "manager_payroll": defaults.manager_payroll},
            "can_view_as": owner, "can_edit_defaults": owner, "view_version": ctx.view(viewer).version}


def _may_grant(ctx: Ctx, viewer: str, subject: str, defaults: VW.Defaults) -> bool:
    """Whether the viewer may grant this teammate anything (VW.check_grant on the narrowest grant, their own
    expenses), so the page offers "Grant more" only where a grant would go through."""
    probe = VW.Grant(id="", subject_kind="teammate", subject=subject, what="expenses", whose="own")
    try:
        VW.check_grant(ctx.org, viewer, probe, defaults, ctx.accounts)
    except FinanceError:
        return False
    return True


def view_as(c: sqlite3.Connection, viewer: str, target: str, at: str | None = None) -> str:
    """Whom a read runs as: the owner may view Finance exactly as any teammate sees it; nobody else may."""
    if not target or target == viewer:
        return viewer
    ctx = load(c, at)
    if not ctx.org.owner or viewer != ctx.org.owner:
        raise denied("only the owner views Finance as someone else")
    if target not in ctx.org.humans | ctx.org.bots:
        raise FinanceError(NOT_FOUND, "no such teammate")
    return target
