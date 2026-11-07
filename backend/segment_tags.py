# pyright: strict
"""The segment a goal or a task is for (goals.segment_id, tasks.segment_id): one of the company's segments, or none.

Writes check the segment with `segments.require_ours`, which refuses one that is not the company's. A filter does not:
a list asked for a segment the company does not play in (a former one, or an unknown id) is empty, not a 404, and `none`
asks for the rows that have no segment.
"""

import sqlite3
from typing import Any, cast

from . import hubdb as H
from . import segments

NONE = H.SEGMENT_NONE
# The filter value for a segment the company does not play in: no row carries it, so the list is empty.
NO_MATCH = "-"


def filter_value(c: sqlite3.Connection, segment: str | None) -> str | None:
    """A segment filter as the reads take it: None for no filter, `none`, the id of one of the company's segments, or
    NO_MATCH for any other id. A former segment's tasks stay tagged, and they are not listed under it."""
    if not segment:
        return None
    if segment == NONE:
        return NONE
    # segments.ours_ids has no annotations yet (track A's module, not strict): its result is typed here until it does.
    ours = cast(set[str], cast(Any, segments).ours_ids(c))
    return segment if segment in ours else NO_MATCH


def name(c: sqlite3.Connection, segment_id: str | None) -> str:
    """The segment's name for the words a change is written in, or "" when there is no such segment."""
    if not segment_id:
        return ""
    row = c.execute("SELECT name FROM market_entities WHERE id=? AND type='segment'", (segment_id,)).fetchone()
    return str(row["name"]) if row else ""


def keeps(row: dict[str, Any], segment: str) -> bool:
    """Whether a goal or task row is in the segment a filter names (`filter_value`'s result)."""
    if segment == NONE:
        return not row.get("segment_id")
    return bool(row.get("segment_id") == segment)
