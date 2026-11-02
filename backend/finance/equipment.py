"""Equipment as Finance writes it (backend/records/store.py): owned pieces such as vans and tools, and a customer's unit at
its site. Local records only: nothing here reaches the books. A bot's photo is a draft a person confirms, so the
approval carries the photo and the person compares it with the record."""
import sqlite3
from collections.abc import Callable
from datetime import date, datetime

from .. import due_date_flow as FLOW
from ..records import core as RC
from ..records import due_dates as RD
from ..records import reads as RR
from ..records import store as RS
from .catalog import record_customer
from .context import Ctx
from .core import kinds as K
from .core import validate as V
from .core.errors import NOT_AVAILABLE, NOT_FOUND, FinanceError, invalid
from .handlers import Plan, Planner, Result
from .store import ledger as SL

FIELDS = ("name", "kind", "location_id", "customer_id", "make", "model", "serial", "installed_on", "warranty_ends_on",
          "photo_file_id")
# A warranty's end makes one once-only due date on the piece (plan H8). Its id comes from the piece, so a renamed or
# archived one is found again and never made twice.
WARRANTY_NAME = "Warranty ends"
WARRANTY_LEAD = 60
# A bot's photo asks a person who holds the records right: the owner holds every right, whatever the bot holds.
PHOTO_GATE = K.Gate("needs_approval", "owner", "default:bot-equipment-photo")
# Whether an actor may read a Library file by its id: Library's own check (files.readable_by), which needs the sign-in's
# Auth, so the app installs it once (finance_routes.install). Until then a photo is refused, never taken unchecked.
file_readable: Callable[[sqlite3.Connection, str, str], bool] | None = None


def set_file_check(check: Callable[[sqlite3.Connection, str, str], bool]) -> None:
    """Install the photo read check: (connection, actor, file id) -> readable."""
    global file_readable
    file_readable = check


def _shape(build: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return build()
    except RC.RecordError as exc:
        raise invalid(str(exc)) from exc


def _photo_readable(ctx: Ctx, who: str, photo: str) -> bool:
    if file_readable is None:
        raise FinanceError(NOT_AVAILABLE, "photos can't be checked on this install")
    return file_readable(ctx.c, who, photo)


def _fields(ctx: Ctx, who: str, p: V.Payload, create: bool) -> dict[str, object]:
    """The columns a payload sets. A van names no location, and a customer's unit names its customer. A photo must be
    a Library file the asker can read: a file they cannot see is no photo to them."""
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
        # The records right (or a bot's approval for it) decides who writes; a holder names any customer.
        out["customer_id"] = record_customer(ctx, who, customer, holder=True) if customer else None
    for key in ("make", "model", "serial"):
        if create or key in p:
            out[key] = V.opt_text(p, key, 80) or ""
    for key in ("installed_on", "warranty_ends_on"):
        if create or key in p:
            out[key] = V.opt_day(p, key)
    if create or "photo_file_id" in p:
        photo = V.opt_text(p, "photo_file_id", 80) or ""
        if photo and not _photo_readable(ctx, who, photo):
            raise FinanceError(NOT_FOUND, "no such photo")
        out["photo_file_id"] = photo or None
    if not create and "active" in p:
        out["archived_at"] = None if V.flag(p, "active", True) else ctx.at
    return out


def _sync_warranty(ctx: Ctx, equipment_id: str, day: str | None, creator: str, who: str) -> None:
    """Keep the piece's warranty due date in step with its warranty end. No row yet: a date from today on adds one (the
    creator takes it), and a past date adds nothing. An active row follows a changed date, counting from today, and is
    archived when the date is cleared; a new date brings an archived one back, since that warranty is a new fact. A row
    that was renamed is the person's now, so it is left alone."""
    due_id = f"warranty-{equipment_id}"
    today = RD.local_day(datetime.fromisoformat(ctx.at), FLOW.zone())
    row = RD.get(ctx.c, due_id)
    if row is None:
        if day and date.fromisoformat(day) >= today:
            RD.insert(ctx.c, {"id": due_id, "name": WARRANTY_NAME, "subject_type": "equipment",
                              "subject_id": equipment_id, "repeats": "once", "every_count": None, "starts_on": day,
                              "counted_from_on": today.isoformat(), "next_due_on": None, "lead_days": WARRANTY_LEAD,
                              "assignee_id": creator, "created_at": ctx.at, "created_by_id": who})
        return
    if str(row["name"]) != WARRANTY_NAME:
        return
    if day is None:
        if row["archived_at"] is None:
            RD.update(ctx.c, due_id, {"archived_at": ctx.at, "updated_at": ctx.at, "updated_by_id": who})
    elif day != str(row["starts_on"]) or row["archived_at"] is not None:
        RD.update(ctx.c, due_id, {"starts_on": day, "counted_from_on": today.isoformat(), "archived_at": None,
                                  "updated_at": ctx.at, "updated_by_id": who})


def _check_owner(kind: str, customer: str) -> None:
    """Owned equipment has no customer; customer equipment names the customer it is at."""
    if kind == "customer" and not customer:
        raise invalid("customer equipment names the customer it is at")
    if kind == "owned" and customer:
        raise invalid("owned equipment has no customer")


def _check_location(ctx: Ctx, kind: str, location: str, customer: str) -> None:
    """A customer's unit at a location that names a customer must name that same customer; a location that names
    none takes the unit as it is."""
    if kind != "customer" or not location:
        return
    row = RR.get(ctx.c, "locations", location)
    if row is not None and row["customer_id"] and str(row["customer_id"]) != customer:
        raise invalid("that location belongs to another customer")


def _gate(ctx: Ctx, who: str, kind: str, photo: object) -> K.Gate:
    """A bot's photo asks a person to confirm it; everything else follows the default gate."""
    if photo and who in ctx.org.bots:
        return PHOTO_GATE
    return K.default_gate(ctx.org, who, kind)


def equipment_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, FIELDS)
    f = _shape(lambda: _fields(ctx, who, p, create=True))
    _check_owner(str(f["kind"]), str(f["customer_id"] or ""))
    _check_location(ctx, str(f["kind"]), str(f["location_id"] or ""), str(f["customer_id"] or ""))
    equipment_id = SL.new_id()

    def apply(aid: str) -> Result:
        RS.insert(ctx.c, "equipment", {**f, "id": equipment_id, "created_at": ctx.at, "created_by_id": who})
        warranty = f["warranty_ends_on"]
        _sync_warranty(ctx, equipment_id, str(warranty) if warranty else None, who, who)
        return {"equipment_id": equipment_id}
    return Plan(_gate(ctx, who, "equipment_create", f["photo_file_id"]), f"Add equipment {f['name']}"[:200], apply)


def equipment_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("equipment_id", *FIELDS, "active"))
    old = RR.get(ctx.c, "equipment", V.text(p, "equipment_id", 80))
    if old is None:
        raise FinanceError(NOT_FOUND, "no such equipment")
    equipment_id = str(old["id"])
    f = _shape(lambda: _fields(ctx, who, p, create=False))
    if not f:
        raise invalid("say what changes")
    kind = str(f.get("kind", old["kind"]))
    customer = str((f["customer_id"] if "customer_id" in f else old["customer_id"]) or "")
    location = str((f["location_id"] if "location_id" in f else old["location_id"]) or "")
    _check_owner(kind, customer)
    _check_location(ctx, kind, location, customer)

    def apply(aid: str) -> Result:
        RS.update(ctx.c, "equipment", equipment_id, {**f, "updated_at": ctx.at, "updated_by_id": who})
        if "warranty_ends_on" in f and f["warranty_ends_on"] != old["warranty_ends_on"]:
            warranty = f["warranty_ends_on"]
            _sync_warranty(ctx, equipment_id, str(warranty) if warranty else None, str(old["created_by_id"]), who)
        return {"equipment_id": equipment_id}
    return Plan(_gate(ctx, who, "equipment_update", f.get("photo_file_id")), f"Change equipment {old['name']}"[:200],
                apply)


PLANNERS: dict[str, Planner] = {"equipment_create": equipment_create, "equipment_update": equipment_update}
