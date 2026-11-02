"""Purchase orders and estimates as the viewer may read them (spec 4.4, 4.5): a list and one record each. A purchase
order is payables and an estimate is receivables, and the viewer needs the matching finance right too. A record with
a line on a private account the viewer lacks full detail for, or a line outside the viewer's scope, stays hidden, as a
bill does. Lists page by cursor, newest first."""
import sqlite3
from typing import Any, cast

from . import tab_ap
from .context import Ctx, load
from .core import rights as R
from .core.errors import NOT_FOUND, FinanceError, denied, invalid
from .handlers import sees_scope
from .store import banking as SK
from .store import purchasing as SP
from .tab import SCAN, decode_cursor, encode_cursor, page_size, private_cut

Row = dict[str, Any]
BATCH = 200
STATUS = {"po": ("draft", "sent", "approved", "partial", "closed", "void"),
          "estimate": ("draft", "sent", "accepted", "declined", "invoiced")}
NAME = {"po": "purchase order", "estimate": "estimate"}
SIDE = {"po": ("bill", R.AP), "estimate": ("invoice", R.AR)}


def _check(ctx: Ctx, viewer: str, kind: str) -> None:
    side, right = SIDE[kind]
    if not R.has(ctx.org, viewer, right, ctx.rights(viewer)):
        raise denied(f"reading a {NAME[kind]} needs {right}")
    if not tab_ap.sees_side(ctx, ctx.view(viewer), side):
        raise denied(f"{'payables' if side == 'bill' else 'receivables'} are not in your view")


def _sees(ctx: Ctx, viewer: str, lines: list[sqlite3.Row], cut: set[str]) -> bool:
    """Whether the viewer sees every line of a record; one line it cannot see hides the record."""
    for x in lines:
        if str(x["account_id"] or "") in cut:
            return False
        tags = cast(dict[str, str], SK.loads(x["tags_json"]))
        if not sees_scope(ctx, viewer, str(x["account_id"] or ""), tags.get("team", ""), tags.get("teammate", "")):
            return False
    return True


def _tagged(names: dict[str, dict[str, str]], lines: list[sqlite3.Row]) -> Row:
    """The locations and projects a record's lines are tagged with, each once, in line order. A record's list row
    names them, since a location or project column needs no second read."""
    out: Row = {"locations": [], "projects": []}
    for kind, key in (("location", "locations"), ("project", "projects")):
        tagged = (cast(dict[str, str], SK.loads(x["tags_json"])).get(kind) for x in lines)
        seen = dict.fromkeys(str(v) for v in tagged if v)
        out[key] = [{"id": v, "name": names[kind].get(v, v)} for v in seen]
    return out


def _head(kind: str, c: sqlite3.Connection, r: sqlite3.Row) -> Row:
    status = (SP.shown_po(str(r["status"]), int(r["billed"]), int(r["total"])) if kind == "po"
              else SP.shown_estimate(str(r["status"]), r["invoice_id"]))
    out: Row = {"id": str(r["id"]), "number": str(r["number"]),
                "contact": {"id": str(r["vendor_id" if kind == "po" else "customer_id"]),
                            "name": str(r["contact_name"])},
                "date": str(r["po_date" if kind == "po" else "estimate_date"]), "status": status,
                "currency": str(r["currency"]), "total": int(r["total"]), "memo": str(r["memo"] or "")}
    if kind == "po":
        out |= {"closed_by": str(r["closed_by"]), "approved_by": r["approved_by"] or None,
                "billed": int(r["billed"]), "held": SP.held_total(c, str(r["id"]))}
    else:
        out["invoice_id"] = r["invoice_id"] or None
    return out


def _line(ctx: Ctx, kind: str, x: sqlite3.Row, names: dict[str, dict[str, str]]) -> Row:
    tags = cast(dict[str, str], SK.loads(x["tags_json"]))
    out: Row = {"id": str(x["id"]), "line_no": int(x["line_no"]),
                "account": tab_ap.account_ref(ctx, x["account_id"]), "item_id": x["item_id"] or None,
                "item_name": names["item"].get(str(x["item_id"] or "")),
                "amount": int(x["amount"]), "description": str(x["description"]), "tags": tags,
                "tag_names": tab_ap.tag_names(names, tags)}
    if kind == "po":
        out["billed"] = SP.billed(ctx.c, str(x["id"]))
    else:
        out["taxable"] = None if x["taxable"] is None else bool(x["taxable"])
    return out


def _list(c: sqlite3.Connection, viewer: str, kind: str, *, status: str | None, contact_id: str | None, q: str,
          cursor: str | None, limit: int, location_id: str | None, project_id: str | None,
          at: str | None) -> tuple[list[Row], str | None]:
    ctx = load(c, at)
    _check(ctx, viewer, kind)
    if status and status not in STATUS[kind]:
        raise invalid(f"status is {'|'.join(STATUS[kind])}")
    cut = set(private_cut(ctx, ctx.view(viewer)))
    names = tab_ap.list_names(c)
    tagged = tab_ap.tag_filter(c, location_id, project_id)
    most = page_size(limit)
    marks = decode_cursor(cursor)
    before = (marks[0], marks[1]) if len(marks) == 2 else None
    out: list[Row] = []
    scanned = 0
    nxt: str | None = None
    while len(out) < most and scanned < SCAN:
        batch = SP.listing(c, kind, book_id=ctx.book.id, status=status or "", contact_id=contact_id or "",
                           q=(q or "").strip()[:100], before=before, most=BATCH, tagged=tagged)
        if not batch:
            break
        scanned += len(batch)
        for r in batch:
            before = (str(r[SP.DATED[kind]]), str(r["id"]))
            lines = SP.lines(c, kind, str(r["id"]))
            if not _sees(ctx, viewer, lines, cut):
                continue
            out.append({**_head(kind, c, r), **_tagged(names, lines)})
            if len(out) == most:
                break
        if len(batch) < BATCH and len(out) < most:
            before = None
            break
    if before is not None and (len(out) == most or scanned >= SCAN):
        nxt = encode_cursor(list(before))
    return out, nxt


# An order read shows this many linked bills the viewer may see; past them the Library holds the rest, so no paging.
LINKED_SHOWN = 50


def _linked_bills(c: sqlite3.Connection, po_id: str, cut: set[str]) -> tuple[list[sqlite3.Row], bool]:
    """The bills linked to an order that the viewer may see, oldest first, at most LINKED_SHOWN; and whether one more is
    visible. A bill with a line on an account the viewer's cut leaves out is never read, so it counts toward nothing."""
    rows = SP.linked_bills(c, po_id, hidden_accounts=sorted(cut), limit=LINKED_SHOWN + 1)
    return rows[:LINKED_SHOWN], len(rows) > LINKED_SHOWN


def _one(c: sqlite3.Connection, viewer: str, kind: str, doc_id: str, at: str | None) -> Row:
    ctx = load(c, at)
    _check(ctx, viewer, kind)
    r = SP.head(c, kind, doc_id)
    lines = SP.lines(c, kind, doc_id) if r is not None else []
    cut = set(private_cut(ctx, ctx.view(viewer)))
    if r is None or str(r["book_id"]) != ctx.book.id or not _sees(ctx, viewer, lines, cut):
        raise FinanceError(NOT_FOUND, f"no such {NAME[kind]} in your view")
    names = tab_ap.record_names(c)
    out: Row = {**_head(kind, c, r), **_tagged(names, lines), "lines": [_line(ctx, kind, x, names) for x in lines],
                "actions": tab_ap.doc_approvals(c, kind, doc_id), "view_version": ctx.view(viewer).version}
    if kind == "po":
        bills, _ = tab_ap.keep_docs(c, "bill", SP.order_bills(c, doc_id), cut)
        out["bills"] = [{"id": str(b["id"]), "number": str(b["number"]), "vendor": str(b["contact_name"] or ""),
                         "date": str(b["bill_date"]), "status": str(b["status"]), "total": int(b["total"]),
                         "on_order": int(b["on_order"])} for b in bills]
        # Bills linked to the order, of any vendor: a link bills no line, so the totals above do not count them.
        linked, more = _linked_bills(c, doc_id, cut)
        out["linked_bills"] = [{"id": str(b["id"]), "number": str(b["number"]),
                                "vendor": str(b["contact_name"] or ""), "total": int(b["total"]),
                                "status": str(b["status"])} for b in linked]
        out["linked_bills_more"] = more
    return out


def orders(c: sqlite3.Connection, viewer: str, *, status: str | None = None, vendor_id: str | None = None,
           q: str = "", cursor: str | None = None, limit: int = 50, location_id: str | None = None,
           project_id: str | None = None, at: str | None = None) -> Row:
    """Purchase orders the viewer sees, newest first. A search over number, memo and vendor name, a status (as
    shown), a vendor and a line's location or project narrow them. `next_cursor` pages."""
    rows, nxt = _list(c, viewer, "po", status=status, contact_id=vendor_id, q=q, cursor=cursor, limit=limit,
                      location_id=location_id, project_id=project_id, at=at)
    return {"orders": rows, "next_cursor": nxt}


def order(c: sqlite3.Connection, viewer: str, po_id: str, at: str | None = None) -> Row:
    """One purchase order with its lines, what each line has billed, the bills on it and the approvals asked."""
    return _one(c, viewer, "po", po_id, at)


def estimates(c: sqlite3.Connection, viewer: str, *, status: str | None = None, customer_id: str | None = None,
              q: str = "", cursor: str | None = None, limit: int = 50, location_id: str | None = None,
              project_id: str | None = None, at: str | None = None) -> Row:
    """Estimates the viewer sees, newest first. A search over number, memo and customer name, a status (as shown), a
    customer and a line's location or project narrow them. `next_cursor` pages."""
    rows, nxt = _list(c, viewer, "estimate", status=status, contact_id=customer_id, q=q, cursor=cursor, limit=limit,
                      location_id=location_id, project_id=project_id, at=at)
    return {"estimates": rows, "next_cursor": nxt}


def estimate(c: sqlite3.Connection, viewer: str, estimate_id: str, at: str | None = None) -> Row:
    """One estimate with its lines, each saying whether it is taxable."""
    return _one(c, viewer, "estimate", estimate_id, at)
