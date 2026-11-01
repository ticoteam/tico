"""Purchase orders and estimates as the viewer may read them (spec 4.4, 4.5): a list and one record each. A purchase
order is payables and an estimate is receivables, and the viewer needs the matching finance right too. A record with a
line the viewer cannot see stays hidden, as a bill does."""
import sqlite3
from typing import Any, cast

from . import tab_ap
from .context import Ctx, load
from .core import rights as R
from .core.errors import NOT_FOUND, FinanceError, denied, invalid
from .handlers import sees_scope
from .store import banking as SK
from .store import purchasing as SP
from .tab import page_size

Row = dict[str, Any]
STATUS = {"po": ("draft", "sent", "approved", "partial", "closed", "void"),
          "estimate": ("draft", "sent", "accepted", "declined", "invoiced")}
NAME = {"po": "purchase order", "estimate": "estimate"}
SIDE = {"po": ("bill", R.AP), "estimate": ("invoice", R.AR)}


def _check(ctx: Ctx, viewer: str, kind: str) -> None:
    side, right = SIDE[kind]
    if right not in ctx.rights(viewer):
        raise denied(f"reading a {NAME[kind]} needs {right}")
    if not tab_ap.sees_side(ctx, ctx.view(viewer), side):
        raise denied(f"{'payables' if side == 'bill' else 'receivables'} are not in your view")


def _sees(ctx: Ctx, viewer: str, lines: list[sqlite3.Row]) -> bool:
    """Whether the viewer sees every line of a record; one line it cannot see hides the record."""
    for x in lines:
        tags = cast(dict[str, str], SK.loads(x["tags_json"]))
        if not sees_scope(ctx, viewer, str(x["account_id"] or ""), tags.get("team", ""), tags.get("teammate", "")):
            return False
    return True


def _head(kind: str, r: sqlite3.Row) -> Row:
    out: Row = {"id": str(r["id"]), "number": str(r["number"]),
                "contact": {"id": str(r["vendor_id" if kind == "po" else "customer_id"]),
                            "name": str(r["contact_name"])},
                "date": str(r["po_date" if kind == "po" else "estimate_date"]), "status": str(r["status"]),
                "currency": str(r["currency"]), "total": int(r["total"]), "memo": str(r["memo"] or "")}
    if kind == "po":
        out |= {"closed_by": str(r["closed_by"]), "approved_by": r["approved_by"] or None}
    else:
        out["invoice_id"] = r["invoice_id"] or None
    return out


def _line(ctx: Ctx, kind: str, x: sqlite3.Row) -> Row:
    out: Row = {"id": str(x["id"]), "line_no": int(x["line_no"]),
                "account": tab_ap.account_ref(ctx, x["account_id"]), "item_id": x["item_id"] or None,
                "amount": int(x["amount"]), "description": str(x["description"]),
                "tags": SK.loads(x["tags_json"])}
    if kind == "po":
        out["billed"] = SP.billed(ctx.c, str(x["id"]))
    else:
        out["taxable"] = None if x["taxable"] is None else bool(x["taxable"])
    return out


def _list(c: sqlite3.Connection, viewer: str, kind: str, status: str | None, contact_id: str | None, limit: int,
          at: str | None) -> list[Row]:
    ctx = load(c, at)
    _check(ctx, viewer, kind)
    if status and status not in STATUS[kind]:
        raise invalid(f"status is {'|'.join(STATUS[kind])}")
    out: list[Row] = []
    for r in SP.listing(c, kind, book_id=ctx.book.id, status=status or "", contact_id=contact_id or "",
                        most=page_size(limit)):
        lines = SP.lines(c, kind, str(r["id"]))
        if _sees(ctx, viewer, lines):
            out.append(_head(kind, r))
    return out


def _one(c: sqlite3.Connection, viewer: str, kind: str, doc_id: str, at: str | None) -> Row:
    ctx = load(c, at)
    _check(ctx, viewer, kind)
    r = SP.head(c, kind, doc_id)
    lines = SP.lines(c, kind, doc_id) if r is not None else []
    if r is None or str(r["book_id"]) != ctx.book.id or not _sees(ctx, viewer, lines):
        raise FinanceError(NOT_FOUND, f"no such {NAME[kind]} in your view")
    return {**_head(kind, r), "lines": [_line(ctx, kind, x) for x in lines],
            "view_version": ctx.view(viewer).version}


def orders(c: sqlite3.Connection, viewer: str, *, status: str | None = None, vendor_id: str | None = None,
           limit: int = 50, at: str | None = None) -> Row:
    """Purchase orders the viewer sees, newest first, up to `limit` of the newest that match."""
    return {"orders": _list(c, viewer, "po", status, vendor_id, limit, at)}


def order(c: sqlite3.Connection, viewer: str, po_id: str, at: str | None = None) -> Row:
    """One purchase order with its lines and what each line has billed."""
    return _one(c, viewer, "po", po_id, at)


def estimates(c: sqlite3.Connection, viewer: str, *, status: str | None = None, customer_id: str | None = None,
              limit: int = 50, at: str | None = None) -> Row:
    """Estimates the viewer sees, newest first, up to `limit` of the newest that match."""
    return {"estimates": _list(c, viewer, "estimate", status, customer_id, limit, at)}


def estimate(c: sqlite3.Connection, viewer: str, estimate_id: str, at: str | None = None) -> Row:
    """One estimate with its lines, each saying whether it is taxable."""
    return _one(c, viewer, "estimate", estimate_id, at)
