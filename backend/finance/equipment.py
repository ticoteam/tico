"""Equipment as Finance writes it (backend/records/store.py): owned pieces such as vans and tools, and a customer's unit at
its site. Local records only: nothing here reaches the books. A bot's photo is a draft a person confirms, so the
approval carries the photo and the person compares it with the record."""
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
from .store import ledger as SL

FIELDS = ("name", "kind", "location_id", "customer_id", "make", "model", "serial", "installed_on", "warranty_ends_on",
          "photo_file_id")
# A bot's photo asks a person who holds the records right: the owner holds every right, whatever the bot holds.
PHOTO_GATE = K.Gate("needs_approval", "owner", "default:bot-equipment-photo")


def _shape(build: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return build()
    except RC.RecordError as exc:
        raise invalid(str(exc)) from exc


def _fields(ctx: Ctx, p: V.Payload, create: bool) -> dict[str, object]:
    """The columns a payload sets. A van names no location, and a customer's unit names its customer."""
    out: dict[str, object] = {}
    if create or "name" in p:
        out["name"] = RC.name(V.text(p, "name", RC.MAX_NAME))
    if create or "kind" in p:
        out["kind"] = RC.choice(V.text(p, "kind", 20), RC.EQUIPMENT_KINDS, "an equipment kind")
    if create or "location_id" in p:
        location = V.opt_text(p, "location_id", 80) or ""
        if location and not RR.is_active(ctx.c, "locations", location):
            raise FinanceError(NOT_FOUND, "no such location")
        out["location_id"] = RS.resolve(ctx.c, "locations", location) if location else None
    if create or "customer_id" in p:
        customer = V.opt_text(p, "customer_id", 80) or ""
        if customer:
            row = SAP.get(ctx.c, "fin_contacts", customer)
            if row is None or str(row["book_id"]) != ctx.book.id or str(row["kind"]) not in ("customer", "both"):
                raise FinanceError(NOT_FOUND, "no such customer")
        out["customer_id"] = customer or None
    for key in ("make", "model", "serial"):
        if create or key in p:
            out[key] = V.opt_text(p, key, 80) or ""
    for key in ("installed_on", "warranty_ends_on"):
        if create or key in p:
            out[key] = V.opt_day(p, key)
    if create or "photo_file_id" in p:
        out["photo_file_id"] = V.opt_text(p, "photo_file_id", 80) or None
    if not create and "active" in p:
        out["archived_at"] = None if V.flag(p, "active", True) else ctx.at
    return out


def _check_owner(kind: str, customer: str) -> None:
    """Owned equipment has no customer; customer equipment names the customer it is at."""
    if kind == "customer" and not customer:
        raise invalid("customer equipment names the customer it is at")
    if kind == "owned" and customer:
        raise invalid("owned equipment has no customer")


def _gate(ctx: Ctx, who: str, kind: str, photo: object) -> K.Gate:
    """A bot's photo asks a person to confirm it; everything else follows the default gate."""
    if photo and who in ctx.org.bots:
        return PHOTO_GATE
    return K.default_gate(ctx.org, who, kind)


def equipment_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, FIELDS)
    f = _shape(lambda: _fields(ctx, p, create=True))
    _check_owner(str(f["kind"]), str(f["customer_id"] or ""))
    equipment_id = SL.new_id()

    def apply(aid: str) -> Result:
        RS.insert(ctx.c, "equipment", {**f, "id": equipment_id, "created_at": ctx.at, "created_by_id": who})
        return {"equipment_id": equipment_id}
    return Plan(_gate(ctx, who, "equipment_create", f["photo_file_id"]), f"Add equipment {f['name']}"[:200], apply)


def equipment_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("equipment_id", *FIELDS, "active"))
    old = RR.get(ctx.c, "equipment", V.text(p, "equipment_id", 80))
    if old is None:
        raise FinanceError(NOT_FOUND, "no such equipment")
    equipment_id = str(old["id"])
    f = _shape(lambda: _fields(ctx, p, create=False))
    if not f:
        raise invalid("say what changes")
    kind = str(f.get("kind", old["kind"]))
    customer = f["customer_id"] if "customer_id" in f else old["customer_id"]
    _check_owner(kind, str(customer or ""))

    def apply(aid: str) -> Result:
        RS.update(ctx.c, "equipment", equipment_id, {**f, "updated_at": ctx.at, "updated_by_id": who})
        return {"equipment_id": equipment_id}
    return Plan(_gate(ctx, who, "equipment_update", f.get("photo_file_id")), f"Change equipment {old['name']}"[:200],
                apply)


PLANNERS: dict[str, Planner] = {"equipment_create": equipment_create, "equipment_update": equipment_update}
