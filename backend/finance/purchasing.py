"""Purchase orders and estimates (spec §4.4, §4.5). An approved order's unbilled lines hold budget; a bill that
bills one of its lines takes that part of the hold over; an accepted estimate becomes one invoice draft, once. Each
planner checks the payload, the asker's view and the document's state; the writes run when the action commits."""
from collections.abc import Sequence
from typing import Any, cast

from . import budgeting
from . import documents as DOC
from .context import Ctx
from .core import ap as AP
from .core import budgets as B
from .core import kinds as K
from .core import mirror as M
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Planner, Result, may_write, private_hidden, sees_scope
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL
from .store import purchasing as SP

# A partly billed order stays approved (it shows as partial, see SP.shown_po): only an approved order takes bills.
OPEN_ORDER = ("approved",)


def _money(amount: int) -> str:
    return f"{amount / 100:.2f}"


def _row(ctx: Ctx, table: str, row_id: str, what: str) -> Any:
    row = SP.get(ctx.c, table, row_id)
    if row is None or str(row["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, f"no such {what}")
    return row


def load_document_for_action(ctx: Ctx, who: str, table: str, kind: str, row_id: str, what: str
                             ) -> tuple[Any, list[AP.DocLine]]:
    """A document's row and lines for an action. A line on a private account the asker lacks the detail of hides the
    whole document: it is not found, before its state, amounts or vendor are looked at, as the reads leave it out."""
    row = _row(ctx, table, row_id, what)
    lines = _lines_of(ctx, kind, row_id)
    if any(private_hidden(ctx, who, x.account_id) for x in lines):
        raise FinanceError(NOT_FOUND, f"no such {what}")
    return row, lines


def _new_lines_seen(ctx: Ctx, who: str, lines: Sequence[AP.DocLine]) -> None:
    """A new document's lines: one on a private account the asker lacks the detail of is refused as a line not in
    view."""
    if any(private_hidden(ctx, who, x.account_id) for x in lines):
        raise FinanceError(NOT_FOUND, "no such line or account in your view")


def _lines_of(ctx: Ctx, kind: str, parent_id: str) -> list[AP.DocLine]:
    return [AP.DocLine(str(r["account_id"]), int(r["amount"]), str(r["description"]),
                       cast(dict[str, str], SK.loads(r["tags_json"])),
                       None if kind != "estimate" or r["taxable"] is None else bool(r["taxable"]),
                       str(r["item_id"] or ""))
            for r in SP.lines(ctx.c, kind, parent_id)]


def _plain_lines(raw: object, *, invoice: bool = False) -> list[AP.DocLine]:
    """Order and estimate lines: a bill line's po_line_id has no meaning here, so it is refused rather than dropped. An
    estimate's lines are an invoice's, so they say whether they are taxable."""
    lines = AP.doc_lines(raw, invoice=invoice)
    if any(x.po_line_id for x in lines):
        raise invalid("a line names a purchase order line only on a bill")
    return lines


def _line_rows(lines: Sequence[AP.DocLine], who: str, at: str, *, invoice: bool = False) -> list[dict[str, object]]:
    return [{"account_id": x.account_id, "item_id": x.item_id or None, "amount": x.amount,
             "description": x.description, "tags_json": SK.dump(dict(x.tags)), "created_at": at, "created_by": who,
             **({"taxable": None if x.taxable is None else int(x.taxable)} if invoice else {})} for x in lines]


def check_lines_in_view(ctx: Ctx, who: str, lines: Sequence[AP.DocLine]) -> None:
    for x in lines:
        if not may_write(ctx, who, x.account_id, x.tags):
            raise denied("a line's account or tags are outside your view")


def release_order_holds(ctx: Ctx, po_id: str, lines: Sequence[Any]) -> None:
    for line in lines:
        budgeting.release_for(ctx, "purchase_order", f"{po_id}:{int(line['line_no'])}")


def _owed(ctx: Ctx, lines: Sequence[Any]) -> list[tuple[Any, int]]:
    """Each order line with what is still unbilled on it. A provider order's tax line owes nothing: a bill bills the
    lines it names, and tax is not one of them, so the tax line would otherwise keep the order open."""
    out: list[tuple[Any, int]] = []
    for line in lines:
        account = ctx.accounts.get(str(line["account_id"] or ""))
        taxed = account is not None and account.subtype == M.SALES_TAX
        out.append((line, 0 if taxed else int(line["amount"]) - SP.billed(ctx.c, str(line["id"]))))
    return out


def _place_holds(ctx: Ctx, who: str, po: Any, owed: Sequence[tuple[Any, int]],
                 force: bool) -> list[budgeting.HoldDecision]:
    """Each owed line's hold, in line order. A line with nothing owed, or on no expense account, holds nothing. Returns
    the decision of each hold placed."""
    po_id = str(po["id"])
    decisions: list[budgeting.HoldDecision] = []
    for line, left in owed:
        source_id = f"{po_id}:{int(line['line_no'])}"
        account = ctx.accounts.get(str(line["account_id"] or ""))
        if left <= 0 or account is None or account.type != "expense":
            budgeting.release_for(ctx, "purchase_order", source_id)
            continue
        tags = cast(dict[str, str], SK.loads(line["tags_json"]))
        decisions.append(budgeting.place(ctx, budget_scope={"account_id": account.id, **DOC.scope_tags(tags),
                                                            "vendor": str(po["vendor_id"])},
                                         amount=left, currency=ctx.book.base_currency, source="purchase_order",
                                         source_id=source_id, expires_at=None, by=who, force=force))
    return decisions


def settle_order(ctx: Ctx, who: str, po_id: str, force: bool = False) -> list[str]:
    """Bring an order's holds and status up to date with its bills. An approved order holds what is still unbilled on
    each line. A hold already placed shrinks to what is left rather than being released and placed again, so an order
    billed in part keeps its budget on the rest. A closed, voided or draft order holds nothing, and billed in full
    closes
    it. Returns why a hold was not placed (no budget covers the line, or the budget is past its limit)."""
    po = SP.get(ctx.c, "fin_purchase_orders", po_id)
    lines = SP.lines(ctx.c, "po", po_id)
    status, closed_by = str(po["status"]), str(po["closed_by"])
    if status in ("draft", "sent", "void") or (status == "closed" and closed_by == "hand"):
        release_order_holds(ctx, po_id, lines)
        return []
    owed = _owed(ctx, lines)
    new, by = ("closed", "bills") if all(left <= 0 for _, left in owed) else ("approved", "")
    if (new, by) != (status, closed_by):
        SP.update(ctx.c, "fin_purchase_orders", po_id,
                  {"status": new, "closed_by": by, "closed_at": ctx.at if new == "closed" else None,
                   "updated_at": ctx.at})
    if new == "closed":
        release_order_holds(ctx, po_id, lines)
        return []
    refused: list[str] = []
    for d in _place_holds(ctx, who, po, owed, force):
        if not d.allowed or d.hold_id is None:
            refused.append(d.reason or "no budget covers this spend")
    return refused


def sync_orders(ctx: Ctx, who: str, po_ids: Sequence[str]) -> None:
    """After a bill links to, or stops linking to, these orders: their holds and status follow the billing."""
    for po_id in po_ids:
        settle_order(ctx, who, po_id)


def _restore(ctx: Ctx, who: str, line: Any, amount: int) -> None:
    """An order line gets back `amount` of room: its hold grows by it with nothing taken from another hold, since the
    spend it stood for is already reversed in the books. A line that posts to no expense account holds nothing. The
    hold is for the teammate the line's tags name, the one its hold was made for."""
    account = ctx.accounts.get(str(line["account_id"] or ""))
    if account is None or account.type != "expense":
        return
    tags = cast(dict[str, str], SK.loads(line["tags_json"]))
    budgeting.transfer(ctx, budget_scope={"account_id": account.id, **DOC.scope_tags(tags),
                                          "vendor": str(line["vendor_id"])},
                       amount=amount, currency=ctx.book.base_currency, source="purchase_order",
                       source_id=f"{line['po_id']}:{int(line['line_no'])}", expires_at=None, by=who)


def give_back(ctx: Ctx, who: str, bill_id: str, *, posted: bool) -> None:
    """A bill that stops billing gives each order line it billed its room back. A bill still holding a line moves that
    hold back to the line; a posted bill's spend has been reversed in the books by now, so the line takes the amount
    back with nothing moved from a hold. Neither is fit-checked. A line the bill keeps billed after a provider revision
    that no longer names it gives its room back the same way."""
    for n, row in enumerate(SAP.lines(ctx.c, "bill", [bill_id])[bill_id]):
        po_line = str(row["po_line_id"] or "")
        line = SP.po_line(ctx.c, po_line) if po_line else None
        if line is None:
            continue
        held = budgeting.held_amount(ctx, "bill", f"{bill_id}:{n}")
        if held:
            # The line keeps the teammate its hold was made for, which the bill's own tags may not name.
            tags = cast(dict[str, str], SK.loads(line["tags_json"]))
            budgeting.transfer(ctx, amount=held, currency=ctx.book.base_currency, source="purchase_order",
                               source_id=f"{line['po_id']}:{int(line['line_no'])}", expires_at=None, by=who,
                               moved_from=("bill", f"{bill_id}:{n}"),
                               target=budgeting.scope_target(DOC.scope_tags(tags)))
        elif posted:
            _restore(ctx, who, line, int(row["amount"]))
    for po_line_id, amount in SP.bill_retained(ctx.c, bill_id).items():
        line = SP.po_line(ctx.c, po_line_id)
        if line is not None and posted:
            _restore(ctx, who, line, amount)


def bill_orders(ctx: Ctx, bill_id: str) -> list[str]:
    return SP.bill_orders(ctx.c, bill_id)


def link_bill_lines(ctx: Ctx, who: str, vendor_id: str,
                    lines: Sequence[AP.DocLine]) -> tuple[list[AP.DocLine], list[str]]:
    """A bill's lines with each line that bills an order's line checked: the order is this vendor's and open for bills,
    the asker may see it, and the line is not billed past what it has left. An account, item or tags a bill line leaves
    out come from the order line. Returns the lines and the ids of the orders they bill."""
    out: list[AP.DocLine] = []
    po_ids: list[str] = []
    taken: dict[str, int] = {}
    for x in lines:
        if not x.po_line_id:
            out.append(x)
            continue
        row = SP.po_line(ctx.c, x.po_line_id)
        if row is None or str(row["book_id"]) != ctx.book.id:
            raise FinanceError(NOT_FOUND, "no such purchase order line")
        account = str(row["account_id"] or "")
        tags = cast(dict[str, str], SK.loads(row["tags_json"]))
        if private_hidden(ctx, who, account):
            raise FinanceError(NOT_FOUND, "no such purchase order line")
        if not may_write(ctx, who, account, tags):
            raise denied("the purchase order's line is outside your view")
        if str(row["vendor_id"]) != vendor_id:
            raise invalid("a bill bills only purchase orders from its own vendor")
        if str(row["status"]) not in OPEN_ORDER:
            raise FinanceError(CONFLICT, "that purchase order takes no more bills")
        if x.account_id and x.account_id != account:
            raise invalid("a bill line on a purchase order line posts to that line's account")
        if x.tags and dict(x.tags) != tags:
            raise invalid("a bill line on a purchase order line takes that line's tags")
        # A bill written and not yet sent takes its share of the line as well: two bills cannot each take the same room.
        room = (int(row["amount"]) - SP.billed(ctx.c, x.po_line_id) - SP.queued_for_line(ctx.c, x.po_line_id)
                - taken.get(x.po_line_id, 0))
        if x.amount > room:
            raise invalid(f"only {_money(max(room, 0))} is left to bill on that purchase order line")
        taken[x.po_line_id] = taken.get(x.po_line_id, 0) + x.amount
        out.append(AP.DocLine(account, x.amount, x.description, tags, x.taxable,
                              x.item_id or str(row["item_id"] or ""), x.po_line_id))
        po_id = str(row["po_id"])
        if po_id not in po_ids:
            po_ids.append(po_id)
    return out, po_ids


def link_bill_order(ctx: Ctx, who: str, po_id: str) -> str:
    """The purchase order a bill is linked to, or "" for none. A bill of any vendor may link to an order in this book
    the asker may see (no line on a private account, none out of the asker's scope). A link bills no line, holds no
    budget and moves neither the order's status nor its billed total."""
    if not po_id:
        return ""
    row = SP.get(ctx.c, "fin_purchase_orders", po_id)
    if row is None or str(row["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such purchase order")
    for x in SP.lines(ctx.c, "po", po_id):
        account = str(x["account_id"] or "")
        tags = cast(dict[str, str], SK.loads(x["tags_json"]))
        if private_hidden(ctx, who, account) or \
                not sees_scope(ctx, who, account, tags.get("team", ""), tags.get("teammate", "")):
            raise FinanceError(NOT_FOUND, "no such purchase order in your view")
    return po_id


def po_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A purchase order as a draft: its lines hold nothing until it is approved."""
    V.only(p, ("vendor_id", "number", "po_date", "memo", "lines"))
    vendor = DOC.contact_for(ctx, V.text(p, "vendor_id", 80), "bill")
    day = V.day(p, "po_date")
    lines, asked = DOC.resolve_lines(ctx, who, "bill", vendor, _plain_lines(p.get("lines")))
    _new_lines_seen(ctx, who, lines)
    if asked:
        raise invalid("say which account each order line posts to")
    number = V.opt_text(p, "number", 80) or SP.next_number(ctx.c, "fin_purchase_orders", ctx.book.id, "PO")
    if SP.number_taken(ctx.c, "fin_purchase_orders", ctx.book.id, number):
        raise FinanceError(CONFLICT, f"purchase order {number} exists")
    total = sum(x.amount for x in lines)
    oid = SL.new_id()

    def apply(aid: str) -> Result:
        SP.insert(ctx.c, "fin_purchase_orders", {
            "id": oid, "book_id": ctx.book.id, "vendor_id": str(vendor["id"]), "number": number, "po_date": day,
            "status": "draft", "currency": ctx.book.base_currency, "total": total,
            "memo": V.opt_text(p, "memo", 500) or "", "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        SP.insert_lines(ctx.c, "po", oid, _line_rows(lines, who, ctx.at))
        return {"po_id": oid, "number": number, "status": "draft", "total": total}
    return Plan(K.default_gate(ctx.org, who, "po_create"),
                f"Draft purchase order {number} for {_money(total)} {ctx.book.base_currency} to {vendor['name']}"[:200],
                apply, facts=DOC.doc_facts("bill", vendor, lines, ctx))


def po_send(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("po_id",))
    po_id = V.text(p, "po_id", 80)
    po, lines = load_document_for_action(ctx, who, "fin_purchase_orders", "po", po_id, "purchase order")
    check_lines_in_view(ctx, who, lines)
    if str(po["status"]) != "draft":
        raise FinanceError(CONFLICT, "only a draft order is marked sent")

    def apply(aid: str) -> Result:
        if SP.update(ctx.c, "fin_purchase_orders", po_id, {"status": "sent", "sent_at": ctx.at, "updated_at": ctx.at},
                     "status='draft'") != 1:
            raise FinanceError(CONFLICT, "that order moved on")
        return {"po_id": po_id, "status": "sent"}
    # A bot's send asks the owner, as an estimate's send does, before the order is marked sent; a person's is unchanged.
    gate = (K.Gate("needs_approval", "owner", "default:bot-po-send") if who in ctx.org.bots
            else K.default_gate(ctx.org, who, "po_send"))
    return Plan(gate, f"Mark purchase order {po['number']} sent", apply)


def _budget_gate(ctx: Ctx, who: str, po: Any, lines: Sequence[Any]) -> K.Gate:
    """What the budgets the order's lines meet ask of approving it, the check a card or a bill makes: each owed line is
    placed as its hold would be (forced past an ask or hold_payment, so each line meets the holds before it) inside a
    savepoint that is rolled back, so nothing is left held. The worst outcome wins: decline refuses the order, ask routes
    it to a finance admin, and warn or hold_payment goes ahead (approval holds the spend; a hold_payment bill waits for
    its payment)."""
    budgets = ctx.budgets()
    ctx.c.execute("SAVEPOINT order_gate")
    try:
        decisions = _place_holds(ctx, who, po, _owed(ctx, lines), force=True)
    finally:
        ctx.c.execute("ROLLBACK TO SAVEPOINT order_gate")
        ctx.c.execute("RELEASE SAVEPOINT order_gate")
    worst = max(decisions, key=lambda d: B.STRICTNESS.get(d.action, -1), default=None)
    if worst is not None and worst.action == "decline":
        raise denied(f"it does not fit the {budgets[worst.limit_budget_id or ''].name} budget")
    if worst is not None and worst.action == "ask":
        return K.Gate("needs_approval", "finance_admin", f"budget:{worst.limit_budget_id}")
    return K.ALLOW


def po_approve(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Approve a draft or sent order: its unbilled lines hold budget. Approving an order again changes nothing."""
    V.only(p, ("po_id",))
    po_id = V.text(p, "po_id", 80)
    po, lines = load_document_for_action(ctx, who, "fin_purchase_orders", "po", po_id, "purchase order")
    status = str(po["status"])
    check_lines_in_view(ctx, who, lines)
    if status in OPEN_ORDER or status == "closed":
        shown = SP.shown_po(status, SP.billed_total(ctx.c, po_id), int(po["total"]))

        def already(aid: str) -> Result:
            return {"po_id": po_id, "status": shown}
        return Plan(K.ALLOW, f"Purchase order {po['number']} is approved already", already)
    if status not in ("draft", "sent"):
        raise FinanceError(CONFLICT, f"a {status} order cannot be approved")
    total = int(po["total"])
    vendor = SAP.get(ctx.c, "fin_contacts", str(po["vendor_id"]))
    gate = _budget_gate(ctx, who, po, SP.lines(ctx.c, "po", po_id))
    if gate.outcome == "allow":
        gate = K.default_gate(ctx.org, who, "po_approve")

    def apply(aid: str) -> Result:
        if SP.update(ctx.c, "fin_purchase_orders", po_id,
                     {"status": "approved", "approved_by": who, "approved_at": ctx.at, "updated_at": ctx.at},
                     "status IN ('draft','sent')") != 1:
            raise FinanceError(CONFLICT, "that order moved on")
        refused = settle_order(ctx, who, po_id, force=True)
        out: Result = {"po_id": po_id, "status": "approved"}
        if refused:
            out["not_held"] = refused
        return out
    return Plan(gate, f"Approve purchase order {po['number']} for {_money(total)} "
                      f"{ctx.book.base_currency} to {vendor['name']}"[:200], apply,
                facts=DOC.doc_facts("bill", vendor, lines, ctx))


def po_close(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Close an approved order by hand: what is not billed is released and no more bills take it."""
    V.only(p, ("po_id",))
    po_id = V.text(p, "po_id", 80)
    po, lines = load_document_for_action(ctx, who, "fin_purchase_orders", "po", po_id, "purchase order")
    check_lines_in_view(ctx, who, lines)
    if str(po["status"]) not in OPEN_ORDER:
        raise FinanceError(CONFLICT, "only an approved order closes")

    def apply(aid: str) -> Result:
        if SP.update(ctx.c, "fin_purchase_orders", po_id,
                     {"status": "closed", "closed_by": "hand", "closed_at": ctx.at, "updated_at": ctx.at},
                     "status='approved'") != 1:
            raise FinanceError(CONFLICT, "that order moved on")
        settle_order(ctx, who, po_id)
        return {"po_id": po_id, "status": "closed"}
    return Plan(K.default_gate(ctx.org, who, "po_close"),
                f"Close purchase order {po['number']}; what is not billed is released", apply)


def po_void(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Void an order nothing has been billed against; a partly billed one is closed instead."""
    V.only(p, ("po_id",))
    po_id = V.text(p, "po_id", 80)
    po, lines = load_document_for_action(ctx, who, "fin_purchase_orders", "po", po_id, "purchase order")
    check_lines_in_view(ctx, who, lines)
    status = SP.shown_po(str(po["status"]), SP.billed_total(ctx.c, po_id), int(po["total"]))
    if status == "partial":
        raise FinanceError(CONFLICT, "a partly billed order is closed, not voided")
    if status not in ("draft", "sent", "approved"):
        raise FinanceError(CONFLICT, f"a {status} order cannot be voided")

    def apply(aid: str) -> Result:
        if SP.update(ctx.c, "fin_purchase_orders", po_id,
                     {"status": "void", "voided_at": ctx.at, "updated_at": ctx.at},
                     "status IN ('draft','sent','approved')") != 1:
            raise FinanceError(CONFLICT, "that order moved on")
        settle_order(ctx, who, po_id)
        return {"po_id": po_id, "status": "void"}
    return Plan(K.default_gate(ctx.org, who, "po_void"), f"Void purchase order {po['number']}", apply)


def estimate_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """An estimate as a draft: it posts nothing and holds nothing."""
    V.only(p, ("customer_id", "number", "estimate_date", "memo", "lines"))
    customer = DOC.contact_for(ctx, V.text(p, "customer_id", 80), "invoice")
    day = V.day(p, "estimate_date")
    lines, _ = DOC.resolve_lines(ctx, who, "invoice", customer, _plain_lines(p.get("lines"), invoice=True))
    _new_lines_seen(ctx, who, lines)
    number = V.opt_text(p, "number", 80) or SP.next_number(ctx.c, "fin_estimates", ctx.book.id, "EST")
    if SP.number_taken(ctx.c, "fin_estimates", ctx.book.id, number):
        raise FinanceError(CONFLICT, f"estimate {number} exists")
    total = sum(x.amount for x in lines)
    eid = SL.new_id()

    def apply(aid: str) -> Result:
        SP.insert(ctx.c, "fin_estimates", {
            "id": eid, "book_id": ctx.book.id, "customer_id": str(customer["id"]), "number": number,
            "estimate_date": day, "status": "draft", "currency": ctx.book.base_currency, "total": total,
            "memo": V.opt_text(p, "memo", 500) or "", "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        SP.insert_lines(ctx.c, "estimate", eid, _line_rows(lines, who, ctx.at, invoice=True))
        return {"estimate_id": eid, "number": number, "status": "draft", "total": total}
    return Plan(K.default_gate(ctx.org, who, "estimate_create"),
                f"Draft estimate {number} for {_money(total)} {ctx.book.base_currency} to {customer['name']}"[:200],
                apply, facts=DOC.doc_facts("invoice", customer, lines, ctx))


def estimate_send(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Mark a draft estimate sent. Sending asks the owner, whoever sends it."""
    V.only(p, ("estimate_id",))
    eid = V.text(p, "estimate_id", 80)
    est, lines = load_document_for_action(ctx, who, "fin_estimates", "estimate", eid, "estimate")
    check_lines_in_view(ctx, who, lines)
    if str(est["status"]) != "draft":
        raise FinanceError(CONFLICT, "only a draft estimate is marked sent")

    def apply(aid: str) -> Result:
        if SP.update(ctx.c, "fin_estimates", eid, {"status": "sent", "sent_at": ctx.at, "updated_at": ctx.at},
                     "status='draft'") != 1:
            raise FinanceError(CONFLICT, "that estimate moved on")
        return {"estimate_id": eid, "status": "sent"}
    return Plan(K.Gate("needs_approval", "owner", "default:estimate-send"), f"Send estimate {est['number']}", apply)


def _decide(ctx: Ctx, who: str, p: V.Payload, kind: str, to: str) -> Plan:
    """A customer's answer to a sent estimate: accepted or declined."""
    V.only(p, ("estimate_id",))
    eid = V.text(p, "estimate_id", 80)
    est, lines = load_document_for_action(ctx, who, "fin_estimates", "estimate", eid, "estimate")
    check_lines_in_view(ctx, who, lines)
    if str(est["status"]) != "sent":
        raise FinanceError(CONFLICT, f"only a sent estimate can be {to}")

    def apply(aid: str) -> Result:
        if SP.update(ctx.c, "fin_estimates", eid, {"status": to, "decided_at": ctx.at, "updated_at": ctx.at},
                     "status='sent'") != 1:
            raise FinanceError(CONFLICT, "that estimate moved on")
        return {"estimate_id": eid, "status": to}
    return Plan(K.default_gate(ctx.org, who, kind), f"Mark estimate {est['number']} {to}", apply)


def estimate_accept(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    return _decide(ctx, who, p, "estimate_accept", "accepted")


def estimate_decline(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    return _decide(ctx, who, p, "estimate_decline", "declined")


def estimate_convert(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """An accepted estimate becomes one invoice draft with its lines. The estimate is linked to that invoice once: a
    second conversion, or one of a declined estimate, is refused."""
    V.only(p, ("estimate_id",))
    eid = V.text(p, "estimate_id", 80)
    est, lines = load_document_for_action(ctx, who, "fin_estimates", "estimate", eid, "estimate")
    check_lines_in_view(ctx, who, lines)
    status = str(est["status"])
    if est["invoice_id"]:
        raise FinanceError(CONFLICT, "that estimate is invoiced already")
    if status == "declined":
        raise FinanceError(CONFLICT, "a declined estimate cannot be invoiced")
    if status != "accepted":
        raise FinanceError(CONFLICT, "only an accepted estimate can be invoiced")
    inner = DOC.invoice_create(ctx, who, {
        "customer_id": str(est["customer_id"]), "invoice_date": ctx.today, "memo": str(est["memo"] or ""),
        "lines": [{"account_id": x.account_id, "item_id": x.item_id, "amount": x.amount,
                   "description": x.description, "tags": dict(x.tags),
                   **({"taxable": x.taxable} if x.taxable is not None else {})} for x in lines]})

    def apply(aid: str) -> Result:
        out = inner.apply(aid)
        invoice_id = str(out["invoice_id"])
        if SP.claim_invoice(ctx.c, eid, invoice_id, ctx.at) != 1:
            raise FinanceError(CONFLICT, "that estimate was invoiced already")
        return {"estimate_id": eid, "invoice_id": invoice_id, "number": out["number"], "status": "invoiced"}
    return Plan(inner.gate, f"Invoice estimate {est['number']} as one draft", apply, facts=inner.facts)


PLANNERS: dict[str, Planner] = {
    "po_create": po_create, "po_send": po_send, "po_approve": po_approve, "po_close": po_close, "po_void": po_void,
    "estimate_create": estimate_create, "estimate_send": estimate_send, "estimate_accept": estimate_accept,
    "estimate_decline": estimate_decline, "estimate_convert": estimate_convert,
}
