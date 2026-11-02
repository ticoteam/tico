"""Library files a record links: a vendor or customer contact, a piece of equipment, an item or a location (a vendor's
contract, an item's certificate of analysis, a piece of equipment's docs). Local records only: nothing here reaches the
books. A file is linked under the records right; a viewer reads only the files they can read, by the same Library check
an equipment photo passes (equipment.file_readable). A bot's link asks a person, as its record does."""
import sqlite3

from ..records import reads as RR
from ..records import store as RS
from . import equipment as EQ
from .context import Ctx
from .core import kinds as K
from .core import validate as V
from .core.errors import NOT_AVAILABLE, NOT_FOUND, FinanceError
from .handlers import Plan, Planner, Result
from .store import ap as SAP

# The subject types a file can link to, and the table each one's id names.
SUBJECTS = {"contact": "fin_contacts", "equipment": "equipment", "item": "items", "location": "locations"}


def _subject(ctx: Ctx, subject_type: str, subject_id: str) -> tuple[str, str]:
    """The table and the row id a subject names. A contact must be in this book; a record may be named by an id it was
    merged under (it resolves to the row kept). Anything else is not_found."""
    table = SUBJECTS[subject_type]
    if table == "fin_contacts":
        row = SAP.get(ctx.c, "fin_contacts", subject_id)
        if row is None or str(row["book_id"]) != ctx.book.id:
            raise FinanceError(NOT_FOUND, "no such contact")
        return table, str(row["id"])
    row = RR.get(ctx.c, table, subject_id)
    if row is None:
        raise FinanceError(NOT_FOUND, f"no such {subject_type}")
    return table, str(row["id"])


def _readable(ctx: Ctx, who: str, file_id: str) -> bool:
    if EQ.file_readable is None:
        raise FinanceError(NOT_AVAILABLE, "files can't be checked on this install")
    return EQ.file_readable(ctx.c, who, file_id)


def visible(c: sqlite3.Connection, viewer: str, table: str, record_id: str) -> list[dict[str, object]]:
    """The files a record links that the viewer may read, oldest first: id (the link's, for record_file_remove),
    file_id, added_at and added_by_id. With no check installed no file shows, since none can be read unchecked."""
    check = EQ.file_readable
    if check is None:
        return []
    return [{"id": link, "file_id": file, "added_at": at, "added_by_id": by}
            for link, file, at, by in RS.file_links(c, table, record_id) if check(c, viewer, file)]


def record_file_add(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("subject_type", "subject_id", "file_id"))
    subject_type = V.choice(p, "subject_type", tuple(SUBJECTS))
    table, record_id = _subject(ctx, subject_type, V.text(p, "subject_id", 80))
    file_id = V.text(p, "file_id", 80)
    if not _readable(ctx, who, file_id):
        raise FinanceError(NOT_FOUND, "no such file")

    def apply(aid: str) -> Result:
        return {"record_file_id": RS.link_file(ctx.c, table, record_id, file_id, ctx.at, who)}
    return Plan(K.default_gate(ctx.org, who, "record_file_add"), f"Link a file to a {subject_type}"[:200], apply)


def record_file_remove(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("record_file_id",))
    link_id = V.text(p, "record_file_id", 80)
    if RS.file_link(ctx.c, link_id) is None:
        raise FinanceError(NOT_FOUND, "no such file on a record")

    def apply(aid: str) -> Result:
        RS.unlink_file(ctx.c, link_id)
        return {"record_file_id": link_id}
    return Plan(K.default_gate(ctx.org, who, "record_file_remove"), "Unlink a file from a record", apply)


PLANNERS: dict[str, Planner] = {"record_file_add": record_file_add, "record_file_remove": record_file_remove}
