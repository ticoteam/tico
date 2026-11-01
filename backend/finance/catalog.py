"""Shared records as Finance writes them (backend/records): items, places and projects. Each planner checks the payload
and the asker's right, then writes the record when the action commits. In a mirrored book the write goes to the provider
first (mirror.queue_record) and the local record changes when the provider confirms it. The gate for an item's price
change asks a finance admin, since a price changes what every new line posts."""
import json
from collections.abc import Callable
from typing import Any

from ..records import core as RC
from ..records import reads as RR
from ..records import store as RS
from . import mirror
from .context import Ctx
from .core import kinds as K
from .core import validate as V
from .core.errors import NOT_FOUND, FinanceError, invalid
from .handlers import Plan, Planner, Result
from .store import ap as SAP
from .store import ledger as SL

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


def _mirrored(ctx: Ctx) -> bool:
    return ctx.book.mode == "mirror"


def _create(ctx: Ctx, who: str, aid: str, op: str, table: str, record_id: str, fields: dict[str, object],
            key: str) -> Result:
    """A new record: written here, or queued for the provider under the id it will have once confirmed."""
    if _mirrored(ctx):
        oid = mirror.queue_record(ctx, who, aid, op, {"local_id": record_id, "day": ctx.today, **fields})
        return {key: record_id, "outbox_id": oid, "status": "queued"}
    RS.insert(ctx.c, table, {**fields, "id": record_id, "created_at": ctx.at, "created_by": who})
    return {key: record_id}


def _update(ctx: Ctx, who: str, aid: str, op: str, table: str, record_id: str, fields: dict[str, object],
            row: Any, key: str) -> Result:
    """A changed record: written here, or queued for the provider with the record as it will be (its columns, changed)."""
    if _mirrored(ctx):
        columns = mirror.RECORD_COLUMNS[op.partition(".")[0]]
        body = {**{k: row[k] for k in columns}, **fields, "local_id": record_id, "day": ctx.today}
        return {key: record_id, "outbox_id": mirror.queue_record(ctx, who, aid, op, body), "status": "queued"}
    RS.update(ctx.c, table, record_id, {**fields, "updated_at": ctx.at, "updated_by": who})
    return {key: record_id}


def item_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ITEM_FIELDS)
    f = _shape(lambda: _item_fields(ctx, p, create=True))
    item_id = SL.new_id()

    def apply(aid: str) -> Result:
        return _create(ctx, who, aid, "item.create", "rec_items", item_id, f, "item_id")
    return Plan(K.default_gate(ctx.org, who, "item_create"), f"Add item {f['name']}"[:200], apply)


def item_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("item_id", *ITEM_FIELDS, "active"))
    item_id = V.text(p, "item_id", 80)
    old = RR.get(ctx.c, "rec_items", item_id)
    if old is None:
        raise FinanceError(NOT_FOUND, "no such item")
    f = _shape(lambda: _item_fields(ctx, p, create=False))
    gate = K.default_gate(ctx.org, who, "item_update")
    if "price" in f and f["price"] != old["price"] and who != ctx.org.owner:
        gate = K.Gate("needs_approval", "finance_admin", "default:item-price")
    if _mirrored(ctx):
        mirror.record_in_provider(ctx, "rec_items", item_id)

    def apply(aid: str) -> Result:
        return _update(ctx, who, aid, "item.update", "rec_items", item_id, f, old, "item_id")
    return Plan(gate, f"Change item {old['name']}"[:200], apply)


def place_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, PLACE_FIELDS)
    f = _shape(lambda: _place_fields(ctx, p, create=True))
    place_id = SL.new_id()

    def apply(aid: str) -> Result:
        return _create(ctx, who, aid, "place.create", "rec_places", place_id, f, "place_id")
    return Plan(K.default_gate(ctx.org, who, "place_create"), f"Add place {f['name']}"[:200], apply)


def place_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("place_id", *PLACE_FIELDS, "active"))
    place_id = V.text(p, "place_id", 80)
    old = RR.get(ctx.c, "rec_places", place_id)
    if old is None:
        raise FinanceError(NOT_FOUND, "no such place")
    if p.get("parent_id") == place_id:
        raise invalid("a place cannot sit under itself")
    f = _shape(lambda: _place_fields(ctx, p, create=False))
    if _mirrored(ctx):
        mirror.record_in_provider(ctx, "rec_places", place_id)

    def apply(aid: str) -> Result:
        return _update(ctx, who, aid, "place.update", "rec_places", place_id, f, old, "place_id")
    return Plan(K.default_gate(ctx.org, who, "place_update"), f"Change place {place_id}"[:200], apply)


def project_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, PROJECT_FIELDS)
    f = _shape(lambda: _project_fields(ctx, p, create=True))
    if _mirrored(ctx) and not f.get("customer_id"):
        raise invalid("a job in a mirrored book sits under a customer")
    project_id = SL.new_id()

    def apply(aid: str) -> Result:
        return _create(ctx, who, aid, "project.create", "rec_projects", project_id, f, "project_id")
    return Plan(K.default_gate(ctx.org, who, "project_create"), f"Add project {f['name']}"[:200], apply)


def project_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("project_id", *PROJECT_FIELDS, "active"))
    project_id = V.text(p, "project_id", 80)
    old = RR.get(ctx.c, "rec_projects", project_id)
    if old is None:
        raise FinanceError(NOT_FOUND, "no such project")
    f = _shape(lambda: _project_fields(ctx, p, create=False))

    def apply(aid: str) -> Result:
        return _update(ctx, who, aid, "project.update", "rec_projects", project_id, f, old, "project_id")
    return Plan(K.default_gate(ctx.org, who, "project_update"), f"Change project {project_id}"[:200], apply)


PLANNERS: dict[str, Planner] = {
    "item_create": item_create, "item_update": item_update, "place_create": place_create,
    "place_update": place_update, "project_create": project_create, "project_update": project_update,
}
