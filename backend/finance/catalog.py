"""Shared records as Finance writes them (backend/records): items, locations and projects. Each planner checks the
payload
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
from .core import rights as R
from .core import validate as V
from .core.errors import NOT_FOUND, FinanceError, denied, invalid
from .handlers import Plan, Planner, Result
from .store import ap as SAP
from .store import ledger as SL

ITEM_FIELDS = ("name", "sku", "kind", "unit", "price", "description", "taxable", "income_account_id",
               "expense_account_id", "asset_account_id")
LOCATION_FIELDS = ("name", "kind", "parent_id", "customer_id", "address")
PROJECT_FIELDS = ("name", "customer_id", "status")
# Locations a mirrored book does not take unless asked: a customer site or a vehicle is kept in Tico.
OUT_OF_BOOKS_LOCATION_KINDS = ("customer_site", "vehicle")


def _shape(build: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return build()
    except RC.RecordError as exc:
        raise invalid(str(exc)) from exc


def _account(ctx: Ctx, who: str, value: str | None, types: tuple[str, ...], what: str) -> str | None:
    """An account an item names: an active account of the right type in this book that the asker's view covers, or none.
    An account the asker cannot see is refused as a missing one, so the refusal does not reveal it exists."""
    if not value:
        return None
    account = ctx.accounts.get(value)
    if account is None or not account.active or account.type not in types or not ctx.view(who).sees_account(account):
        raise FinanceError(NOT_FOUND, f"no such {what} account")
    return value


def _archive_flag(ctx: Ctx, p: V.Payload) -> dict[str, object]:
    """The archived_at a payload's active flag sets: NULL while the record is live, the change's time once archived."""
    if "active" not in p:
        return {}
    return {"archived_at": None if V.flag(p, "active", True) else ctx.at}


def _customer(ctx: Ctx, who: str, value: str) -> str:
    """A customer contact a record names. Refused as a missing one when the asker's view does not cover customers, so
    a refusal never says that a contact exists (the same rule an account follows)."""
    row = SAP.get(ctx.c, "fin_contacts", value)
    view = ctx.view(who)
    if row is None or str(row["book_id"]) != ctx.book.id or str(row["kind"]) not in ("customer", "both") or \
            not (view.everything or view.receivables):
        raise FinanceError(NOT_FOUND, "no such customer")
    return str(row["id"])


def _accounts_need_books(ctx: Ctx, who: str, f: dict[str, object], old: Any | None) -> None:
    """An item's accounts change only for a books right holder: the records right sets the rest of an item."""
    changed = any(key in f and f[key] != (old[key] if old is not None else None) for key, _ in mirror.ITEM_ACCOUNTS)
    if changed and not R.has(ctx.org, who, R.BOOK, ctx.rights(who)):
        raise denied("an item's accounts need the books right")


def _refuse_cycle(ctx: Ctx, location_id: str, parent_id: str | None) -> None:
    """Refuses a parent that sits under the location itself, directly or through its own parents."""
    at, seen = parent_id or "", set[str]()
    while at and at not in seen:
        if at == location_id:
            raise invalid("a location cannot sit under itself or one of its own parts")
        seen.add(at)
        row = RR.get(ctx.c, "locations", at)
        at = str(row["parent_id"] or "") if row is not None else ""


def _item_fields(ctx: Ctx, who: str, p: V.Payload, create: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "kind" in p:
        out["kind"] = RC.choice(V.text(p, "kind", 40), RC.ITEM_KINDS, "an item kind")
    if create or "sku" in p:
        out["sku"] = V.opt_text(p, "sku", 80) or ""
    if create or "unit" in p:
        out["unit"] = V.opt_text(p, "unit", 20) or ""
    if create or "description" in p:
        out["description"] = V.opt_text(p, "description", 2000) or ""
    if create or "taxable" in p:
        out["taxable"] = int(V.flag(p, "taxable", False))
    if create or "price" in p:
        price = RC.price(V.opt_amount(p, "price"))
        out["price"] = price
        out["currency"] = ctx.book.base_currency if price is not None else ""
    for key, types, what in (("income_account_id", ("income",), "income"),
                             ("expense_account_id", ("expense",), "expense"),
                             ("asset_account_id", ("asset",), "asset")):
        if create or key in p:
            out[key] = _account(ctx, who, V.opt_text(p, key, 80), types, what)
    if not create:
        out.update(_archive_flag(ctx, p))
    return out


def _location_fields(ctx: Ctx, who: str, p: V.Payload, create: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "kind" in p:
        out["kind"] = RC.choice(V.opt_text(p, "kind", 40) or "other", RC.LOCATION_KINDS, "a location kind")
    if create or "parent_id" in p:
        parent = V.opt_text(p, "parent_id", 80) or ""
        if parent and not RR.is_active(ctx.c, "locations", parent):
            raise FinanceError(NOT_FOUND, "no such location to sit under")
        out["parent_id"] = RS.resolve(ctx.c, "locations", parent) if parent else None
    if create or "customer_id" in p:
        customer = V.opt_text(p, "customer_id", 80) or ""
        out["customer_id"] = _customer(ctx, who, customer) if customer else None
    if create or "address" in p:
        address = V.obj(p.get("address") or {}, "an address")
        if any(not isinstance(v, str) or len(v) > 200 for v in address.values()) or len(address) > 10:
            raise invalid("an address is up to 10 text fields of 200 characters")
        out["address"] = json.dumps({str(k): v for k, v in address.items()}, sort_keys=True)
    if not create:
        out.update(_archive_flag(ctx, p))
    return out


def _project_fields(ctx: Ctx, who: str, p: V.Payload, create: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "status" in p:
        out["status"] = RC.choice(V.opt_text(p, "status", 20) or "open", RC.PROJECT_STATUSES, "a project status")
    if create or "customer_id" in p:
        customer = V.opt_text(p, "customer_id", 80) or ""
        out["customer_id"] = _customer(ctx, who, customer) if customer else None
    if not create:
        out.update(_archive_flag(ctx, p))
    return out


def _mirrored(ctx: Ctx) -> bool:
    return ctx.book.mode == "mirror"


def _hides_item(ctx: Ctx, who: str, item: Any) -> bool:
    """Whether the item names an account the asker's view does not cover. Its price and accounts are then not theirs to
    see, so the item is not found to them, as a missing one is, whatever they ask to change."""
    view = ctx.view(who)
    return any(account is not None and not view.sees_account(account) for account in
               (ctx.accounts.get(str(item[key] or "")) for key, _ in mirror.ITEM_ACCOUNTS))


def _create(ctx: Ctx, who: str, aid: str, op: str, table: str, record_id: str, fields: dict[str, object],
            key: str, *, books: bool) -> Result:
    """A new record: written here, or queued for the provider under the id it will have once confirmed. A record the
    books do not take is written here alone."""
    if books:
        oid = mirror.queue_record(ctx, who, aid, op, {"local_id": record_id, "day": ctx.today, **fields})
        return {key: record_id, "outbox_id": oid, "status": "queued"}
    RS.insert(ctx.c, table, {**fields, "id": record_id, "created_at": ctx.at, "created_by_id": who})
    return {key: record_id}


def _update(ctx: Ctx, who: str, aid: str, op: str, table: str, record_id: str, fields: dict[str, object],
            key: str, *, books: bool) -> Result:
    """A changed record: written here, or queued for the provider as the change alone (the record it lands on is read
    when the write is sent)."""
    if books:
        return {key: record_id, "outbox_id": mirror.queue_record_update(ctx, who, aid, op, record_id, fields),
                "status": "queued"}
    RS.update(ctx.c, table, record_id, {**fields, "updated_at": ctx.at, "updated_by_id": who})
    return {key: record_id}


def item_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ITEM_FIELDS)
    f = _shape(lambda: _item_fields(ctx, who, p, create=True))
    if _mirrored(ctx) and f["price"] is None:
        raise invalid("an item the provider holds needs a price")
    _accounts_need_books(ctx, who, f, None)
    if _mirrored(ctx) and f["kind"] == "bundle":
        raise invalid("a bundle cannot be created while the books are mirrored: the provider takes no bundles")
    item_id = SL.new_id()

    def apply(aid: str) -> Result:
        return _create(ctx, who, aid, "item.create", "items", item_id, f, "item_id", books=_mirrored(ctx))
    return Plan(K.default_gate(ctx.org, who, "item_create"), f"Add item {f['name']}"[:200], apply)


def _books_kind_change(old: str, new: str) -> None:
    """Refuses a kind change that crosses the item type the books hold, since the books keep an item's type."""
    held = mirror.books_item_type(old)
    if held != mirror.books_item_type(new):
        raise invalid(f"an item the books hold as {held} cannot change to {mirror.books_item_type(new)}: "
                      "add a new item instead")


def item_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("item_id", *ITEM_FIELDS, "active"))
    old = RR.get(ctx.c, "items", V.text(p, "item_id", 80))
    if old is None or _hides_item(ctx, who, old):
        raise FinanceError(NOT_FOUND, "no such item")
    if who in ctx.org.bots and old["archived_at"] is not None:
        raise invalid("a bot cannot change an archived item: a person unarchives it first")
    item_id = str(old["id"])
    f = _shape(lambda: _item_fields(ctx, who, p, create=False))
    _accounts_need_books(ctx, who, f, old)
    if _mirrored(ctx) and f.get("kind") == "bundle" and old["kind"] != "bundle":
        raise invalid("a bundle cannot be made while the books are mirrored: the provider takes no bundles")
    if _mirrored(ctx) and "price" in f and f["price"] is None and old["price"] is not None:
        raise invalid("an item the provider holds keeps a price: set a new one")
    for key, kind in mirror.ITEM_ACCOUNTS:
        # QuickBooks and Xero keep an account the update leaves out, so a cleared one would stay on the provider's item.
        if _mirrored(ctx) and key in f and f[key] is None and old[key] is not None:
            raise invalid(f"an item the provider holds keeps its {kind} account: set another one")
    if _mirrored(ctx) and "kind" in f and f["kind"] != old["kind"]:
        _books_kind_change(str(old["kind"]), str(f["kind"]))
    gate = K.default_gate(ctx.org, who, "item_update")
    if "price" in f and f["price"] != old["price"] and who != ctx.org.owner:
        gate = K.Gate("needs_approval", "finance_admin", "default:item-price")
    if _mirrored(ctx):
        mirror.record_in_provider(ctx, "items", item_id)

    def apply(aid: str) -> Result:
        return _update(ctx, who, aid, "item.update", "items", item_id, f, "item_id", books=_mirrored(ctx))
    return Plan(gate, f"Change item {old['name']}"[:200], apply)


def location_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, (*LOCATION_FIELDS, "in_books"))
    f = _shape(lambda: _location_fields(ctx, who, p, create=True))
    books = _mirrored(ctx) and V.flag(p, "in_books", str(f["kind"]) not in OUT_OF_BOOKS_LOCATION_KINDS)
    if books:
        mirror.provider_takes_records(ctx, "a location")
    location_id = SL.new_id()

    def apply(aid: str) -> Result:
        return _create(ctx, who, aid, "location.create", "locations", location_id, f, "location_id", books=books)
    return Plan(K.default_gate(ctx.org, who, "location_create"), f"Add location {f['name']}"[:200], apply)


def location_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("location_id", *LOCATION_FIELDS, "active"))
    old = RR.get(ctx.c, "locations", V.text(p, "location_id", 80))
    if old is None:
        raise FinanceError(NOT_FOUND, "no such location")
    location_id = str(old["id"])
    f = _shape(lambda: _location_fields(ctx, who, p, create=False))
    if "parent_id" in f:
        _refuse_cycle(ctx, location_id, str(f["parent_id"] or ""))
    books = _mirrored(ctx) and not mirror.kept_local(ctx, "locations", location_id)
    if books:
        mirror.provider_takes_records(ctx, "a location")
        mirror.record_in_provider(ctx, "locations", location_id)

    def apply(aid: str) -> Result:
        return _update(ctx, who, aid, "location.update", "locations", location_id, f, "location_id",
                       books=books)
    return Plan(K.default_gate(ctx.org, who, "location_update"), f"Change location {location_id}"[:200], apply)


def project_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, PROJECT_FIELDS)
    f = _shape(lambda: _project_fields(ctx, who, p, create=True))
    if _mirrored(ctx):
        mirror.provider_takes_records(ctx, "a job")
        if not f.get("customer_id"):
            raise invalid("a job in a mirrored book sits under a customer")
    project_id = SL.new_id()

    def apply(aid: str) -> Result:
        return _create(ctx, who, aid, "project.create", "projects", project_id, f, "project_id", books=_mirrored(ctx))
    return Plan(K.default_gate(ctx.org, who, "project_create"), f"Add project {f['name']}"[:200], apply)


def project_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("project_id", *PROJECT_FIELDS, "active"))
    old = RR.get(ctx.c, "projects", V.text(p, "project_id", 80))
    if old is None:
        raise FinanceError(NOT_FOUND, "no such project")
    project_id = str(old["id"])
    f = _shape(lambda: _project_fields(ctx, who, p, create=False))

    def apply(aid: str) -> Result:
        return _update(ctx, who, aid, "project.update", "projects", project_id, f, "project_id",
                       books=_mirrored(ctx))
    return Plan(K.default_gate(ctx.org, who, "project_update"), f"Change project {project_id}"[:200], apply)


PLANNERS: dict[str, Planner] = {
    "item_create": item_create, "item_update": item_update, "location_create": location_create,
    "location_update": location_update, "project_create": project_create, "project_update": project_update,
}
