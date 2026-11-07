"""The segment a goal or a task is for (goals.segment_id, tasks.segment_id): one of the company's segments, or none.

Writes check the segment with `segments.require_ours`, which refuses one that is not the company's. A filter takes the
same check, so a list asked for a segment the company does not play in is a 404 and not an empty list, and `none` asks
for the rows that have no segment.
"""

from . import hubdb as H
from . import segments

NONE = H.SEGMENT_NONE


def filter_value(c, segment):
    """A segment filter as the reads take it: None for no filter, `none`, or one of the company's segment ids."""
    if not segment:
        return None
    if segment == NONE:
        return NONE
    return segments.require_ours(c, segment) or None


def name(c, segment_id):
    """The segment's name for the words a change is written in, or "" when there is no such segment."""
    if not segment_id:
        return ""
    row = H._one(c, "SELECT name FROM market_entities WHERE id=? AND type='segment'", (segment_id,))
    return row["name"] if row else ""


def keeps(row, segment):
    """Whether a goal or task row is in the segment a filter names (`filter_value`'s result)."""
    if segment == NONE:
        return not row.get("segment_id")
    return row.get("segment_id") == segment
