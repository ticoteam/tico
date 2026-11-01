"""Shared records as Finance's lines use them: a line's location, project and class tags must name an active record, and
an item a line names must be live and not a category (backend/records). Checked where lines are written, so a stale id
never reaches the books. A tag never widens who sees a line: view checks stay with the caller."""
from collections.abc import Mapping
from typing import Any

from ..records import reads as RR
from ..records import store as RS
from .context import Ctx
from .core.errors import NOT_FOUND, FinanceError, invalid
from .store import catalog as SC

RECORD_TABLES = {"location": "locations", "project": "projects"}


def check_tags(ctx: Ctx, tags: Mapping[str, str]) -> dict[str, str]:
    """Refuse a location, project or class tag that names no active record. Returns the tags as they are stored: a
    record named by an id it was brought in under is stored under the row it is, so one record has one name in the
    books."""
    out = dict(tags)
    for kind, value in tags.items():
        if kind == "class":
            found = SC.class_active(ctx.c, value)
        elif kind in RECORD_TABLES:
            found = RR.is_active(ctx.c, RECORD_TABLES[kind], value)
            if found:
                out[kind] = RS.resolve(ctx.c, RECORD_TABLES[kind], value)
        else:
            continue
        if not found:
            raise invalid(f"no active {kind} to tag with")
    return out


def canonical_item_id(ctx: Ctx, item_id: str) -> str:
    """The item an id names: an id an item was brought in under is stored as the item it is."""
    return RS.resolve(ctx.c, "items", item_id)


def live_item(ctx: Ctx, item_id: str) -> Any:
    """The item a line names. An inactive item is refused: it can no longer be bought or sold."""
    row = RR.get(ctx.c, "items", item_id)
    if row is None:
        raise FinanceError(NOT_FOUND, "no such item")
    if row["archived_at"] is not None:
        raise invalid("that item is inactive")
    if row["kind"] == "category":
        raise invalid("a category cannot go on a line: name a product or service instead")
    return row
