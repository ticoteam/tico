"""Shared records as Finance writes them (backend/records): items, places and projects. Each planner checks the payload
and the asker's right, then writes the record when the action commits. The gate for an item's price change asks a
finance admin, since a price changes what every new line posts."""
import json
from collections.abc import Callable

from ..records import core as RC
from ..records import reads as RR
from ..records import store as RS
from .context import Ctx
from .core import kinds as K
from .core import validate as V
from .core.errors import NOT_FOUND, FinanceError, invalid
from .handlers import Plan, Planner, Result
from .store import ap as SAP

ITEM_FIELDS = ("name", "sku", "kind", "unit", "price", "income_account_id", "expense_account_id", "asset_account_id")
PLACE_FIELDS = ("name", "kind", "parent_id", "address")
PROJECT_FIELDS = ("name", "customer_id", "status")


def _shape(build: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return build()
    except RC.RecordError as exc:
        raise invalid(str(exc)) from exc


def _account(ctx: Ctx, value: str | None, types: tuple[str, ...], what: str) -> str | None:
    """An account an item names: an active account of the right type in this book, or none."""
    if not value:
        return None
    account = ctx.accounts.get(value)
    if account is None or not account.active or account.type not in types:
        raise FinanceError(NOT_FOUND, f"no such {what} account")
    return value


def _item_fields(ctx: Ctx, p: V.Payload, create: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "kind" in p:
        out["kind"] = RC.choice(V.text(p, "kind", 40), RC.ITEM_KINDS, "an item kind")
    if create or "sku" in p:
        out["sku"] = V.opt_text(p, "sku", 80) or ""
    if create or "unit" in p:
        out["unit"] = V.opt_text(p, "unit", 20) or ""
    if create or "price" in p:
        price = RC.price(V.opt_amount(p, "price"))
        out["price"] = price
        out["currency"] = ctx.book.base_currency if price is not None else ""
    for key, types, what in (("income_account_id", ("income",), "income"),
                             ("expense_account_id", ("expense",), "expense"),
                             ("asset_account_id", ("asset",), "asset")):
        if create or key in p:
            out[key] = _account(ctx, V.opt_text(p, key, 80), types, what)
    if not create and "active" in p:
        out["active"] = int(V.flag(p, "active", True))
    return out


def _place_fields(ctx: Ctx, p: V.Payload, create: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "kind" in p:
        out["kind"] = RC.choice(V.opt_text(p, "kind", 40) or "location", RC.PLACE_KINDS, "a place kind")
    if create or "parent_id" in p:
        parent = V.opt_text(p, "parent_id", 80) or ""
        if parent and not RR.is_active(ctx.c, "rec_places", parent):
            raise FinanceError(NOT_FOUND, "no such place to sit under")
        out["parent_id"] = parent or None
    if create or "address" in p:
        address = V.obj(p.get("address") or {}, "an address")
        if any(not isinstance(v, str) or len(v) > 200 for v in address.values()) or len(address) > 10:
            raise invalid("an address is up to 10 text fields of 200 characters")
        out["address_json"] = json.dumps({str(k): v for k, v in address.items()}, sort_keys=True)
    if not create and "active" in p:
        out["active"] = int(V.flag(p, "active", True))
    return out


def _project_fields(ctx: Ctx, p: V.Payload, create: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "status" in p:
        out["status"] = RC.choice(V.opt_text(p, "status", 20) or "open", RC.PROJECT_STATUSES, "a project status")
    if create or "customer_id" in p:
        customer = V.opt_text(p, "customer_id", 80) or ""
        if customer:
            row = SAP.get(ctx.c, "fin_contacts", customer)
            if row is None or str(row["book_id"]) != ctx.book.id or str(row["kind"]) not in ("customer", "both"):
                raise FinanceError(NOT_FOUND, "no such customer")
        out["customer_id"] = customer or None
    if not create and "active" in p:
        out["active"] = int(V.flag(p, "active", True))
    return out


def _live_book(ctx: Ctx) -> None:
    """Records in a mirrored book are written to the provider first (mirror.py); the local write waits for that."""
    if ctx.book.mode == "mirror":
        raise invalid("records in a mirrored book are written through the provider; that is not built yet")


def item_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ITEM_FIELDS)
    _live_book(ctx)
    f = _shape(lambda: _item_fields(ctx, p, create=True))

    def apply(aid: str) -> Result:
        return {"item_id": RS.insert(ctx.c, "rec_items", {**f, "created_at": ctx.at, "created_by": who})}
    return Plan(K.default_gate(ctx.org, who, "item_create"), f"Add item {f['name']}"[:200], apply)


def item_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("item_id", *ITEM_FIELDS, "active"))
    _live_book(ctx)
    item_id = V.text(p, "item_id", 80)
    old = RR.get(ctx.c, "rec_items", item_id)
    if old is None:
        raise FinanceError(NOT_FOUND, "no such item")
    f = _shape(lambda: _item_fields(ctx, p, create=False))
    gate = K.default_gate(ctx.org, who, "item_update")
    if "price" in f and f["price"] != old["price"] and who != ctx.org.owner:
        gate = K.Gate("needs_approval", "finance_admin", "default:item-price")

    def apply(aid: str) -> Result:
        RS.update(ctx.c, "rec_items", item_id, {**f, "updated_at": ctx.at, "updated_by": who})
        return {"item_id": item_id}
    return Plan(gate, f"Change item {old['name']}"[:200], apply)


def place_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, PLACE_FIELDS)
    _live_book(ctx)
    f = _shape(lambda: _place_fields(ctx, p, create=True))

    def apply(aid: str) -> Result:
        return {"place_id": RS.insert(ctx.c, "rec_places", {**f, "created_at": ctx.at, "created_by": who})}
    return Plan(K.default_gate(ctx.org, who, "place_create"), f"Add place {f['name']}"[:200], apply)


def place_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("place_id", *PLACE_FIELDS, "active"))
    _live_book(ctx)
    place_id = V.text(p, "place_id", 80)
    if RR.get(ctx.c, "rec_places", place_id) is None:
        raise FinanceError(NOT_FOUND, "no such place")
    if p.get("parent_id") == place_id:
        raise invalid("a place cannot sit under itself")
    f = _shape(lambda: _place_fields(ctx, p, create=False))

    def apply(aid: str) -> Result:
        RS.update(ctx.c, "rec_places", place_id, {**f, "updated_at": ctx.at, "updated_by": who})
        return {"place_id": place_id}
    return Plan(K.default_gate(ctx.org, who, "place_update"), f"Change place {place_id}"[:200], apply)


def project_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, PROJECT_FIELDS)
    _live_book(ctx)
    f = _shape(lambda: _project_fields(ctx, p, create=True))

    def apply(aid: str) -> Result:
        return {"project_id": RS.insert(ctx.c, "rec_projects", {**f, "created_at": ctx.at, "created_by": who})}
    return Plan(K.default_gate(ctx.org, who, "project_create"), f"Add project {f['name']}"[:200], apply)


def project_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("project_id", *PROJECT_FIELDS, "active"))
    _live_book(ctx)
    project_id = V.text(p, "project_id", 80)
    if RR.get(ctx.c, "rec_projects", project_id) is None:
        raise FinanceError(NOT_FOUND, "no such project")
    f = _shape(lambda: _project_fields(ctx, p, create=False))

    def apply(aid: str) -> Result:
        RS.update(ctx.c, "rec_projects", project_id, {**f, "updated_at": ctx.at, "updated_by": who})
        return {"project_id": project_id}
    return Plan(K.default_gate(ctx.org, who, "project_update"), f"Change project {project_id}"[:200], apply)


PLANNERS: dict[str, Planner] = {
    "item_create": item_create, "item_update": item_update, "place_create": place_create,
    "place_update": place_update, "project_create": project_create, "project_update": project_update,
}
