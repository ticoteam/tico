"""Segments in Finance (spec: Money). The company's segments are market graph entities (backend/segments.py); this module
says which segment a line counts toward, which columns a segment P&L shows, who is dedicated to a segment and who leads
it. Teammate tags and leads come from backend/segments.py.

A line counts toward a segment in this order: its explicit `segment` tag; else the segment of the budget its spend
counts against (the deepest budget that matches it, its parent's segment when it has none); else, for revenue, the
segment of its customer; else Unallocated. Shared costs (payroll, engineering, AI seats) have no budget and stay
Unallocated.
"""

import sqlite3
from collections.abc import Callable
from typing import Any, cast

from .. import hubdb as H
from .. import segments as SEG
from .core.errors import FinanceError, NOT_FOUND

UNALLOCATED = "unallocated"  # the column for lines that count toward no segment
TOTAL = "total"  # the column every line counts toward


def ours(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """The company's segments in the Market page's order, with its colors and ranks (backend/segments.py ordered): each
    one's id, name, one line (`who`), color and rank, its place in that list. A segment P&L's columns come in this order."""
    return SEG.ordered(c)


def checked(c: sqlite3.Connection, value: object) -> str:
    """A segment id as stored: "" (none) stays "", anything else must be one of the company's segments."""
    sid = str(value or "").strip()
    if sid and sid not in SEG.ours_ids(c):
        raise FinanceError(NOT_FOUND, "that is not one of the company's segments")
    return sid


def tagged(tagset: str) -> str:
    """The segment a rollup row's tag set names explicitly ("" when it names none)."""
    for part in tagset.split("|"):
        kind, sep, value = part.partition("=")
        if sep and kind == "segment":
            return value
    return ""


def member_ref(c: sqlite3.Connection, actor: str) -> str:
    """A finance actor as a teammate ref (`person:<id>` or `bot:<id>`, backend/groups.py's form)."""
    kind, _, ident = actor.partition(":")
    if kind == "human":
        return "person:" + ident
    if kind == "bot":
        found = cast(Callable[..., dict[str, Any] | None], H.resolve_bot)(c, ident)
        return "bot:" + (found["id"] if found else ident)
    return ""


def actor_of(ref: str) -> str:
    kind, _, ident = ref.partition(":")
    return "human:" + ident if kind == "person" else ref


def dedicated(c: sqlite3.Connection, actor: str) -> frozenset[str]:
    """The segments an actor is dedicated to: tagged with them, not All. Nothing without company segments."""
    if not SEG.ours_ids(c):
        return frozenset()
    ref = member_ref(c, actor)
    return frozenset(s for s in SEG.member_segments(c, ref) if s != SEG.ALL) if ref else frozenset()


def lead_map(c: sqlite3.Connection) -> dict[str, frozenset[str]]:
    """Which segments each actor leads, as the segment core decides it (backend/segments.py leads). Only the segments
    somebody is tagged with are asked, so an install with no segment members reads no org chart."""
    ours = SEG.ours_ids(c)
    if not ours:
        return {}
    tagged = {r[0] for r in c.execute("SELECT DISTINCT segment_id FROM segment_members")}
    out: dict[str, set[str]] = {}
    for sid in sorted(ours & tagged):
        for ref in SEG.leads(c, sid):
            out.setdefault(actor_of(ref), set()).add(sid)
    return {actor: frozenset(found) for actor, found in out.items()}
