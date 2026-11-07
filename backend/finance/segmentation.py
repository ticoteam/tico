"""Segments in Finance (spec: Money). The company's segments are market graph entities (backend/segments.py); this module
says which segment a line counts toward, which columns a segment P&L shows, who is dedicated to a segment and who leads
it. Teammate tags and leads come from backend/segments.py (provisional until track A lands them).

A line counts toward a segment in this order: its explicit `segment` tag; else the segment of the budget its spend
counts against (the deepest budget that matches it, its parent's segment when it has none); else, for revenue, the
segment of its customer; else Unallocated. Shared costs (payroll, engineering, AI seats) have no budget and stay
Unallocated.
"""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .. import hubdb as H
from .. import segments as SEG
from .core.errors import FinanceError, NOT_FOUND
from .store import tico as ST

UNALLOCATED = "unallocated"  # the column for lines that count toward no segment
TOTAL = "total"  # the column every line counts toward


def _props(text: object) -> dict[str, Any]:
    try:
        value = json.loads(str(text or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _rank(props: Mapping[str, Any]) -> int | None:
    """A segment's rank, or None when it has none (such a segment sorts last)."""
    try:
        return int(props.get("rank"))
    except (TypeError, ValueError):
        return None


def ours(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """The company's segments in the order a segment P&L shows them: by rank, then name, with the unranked last. Each
    one's id, name, one line (`who`), color and rank (None when unranked)."""
    ids = sorted(SEG.ours_ids(c))
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = c.execute(f"SELECT id, name, summary, properties FROM market_entities WHERE id IN ({marks})", ids).fetchall()
    out = []
    for r in rows:
        props = _props(r[3])
        out.append(
            {
                "id": str(r[0]),
                "name": str(r[1]),
                "who": str(r[2] or ""),
                "color": str(props.get("color") or ""),
                "rank": _rank(props),
            }
        )
    return sorted(out, key=lambda x: (x["rank"] is None, x["rank"] or 0, x["name"], x["id"]))


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
        found = H.resolve_bot(c, ident)
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
    """Which segments each actor leads: a teammate tagged with a segment leads it, unless their manager is tagged with it
    too, so the manager leads and the people under them do not. The org chart is read once, and only when some segment
    has a teammate tagged with it, so an install with no segment members reads nothing."""
    out: dict[str, set[str]] = {}
    managers: Mapping[str, str] | None = None
    for sid in sorted(SEG.ours_ids(c)):
        refs = SEG.leads(c, sid)
        if not refs:
            continue
        if managers is None:
            managers = ST.org(c, "").reports_to
        tagged = {actor_of(ref) for ref in refs}
        for ref in refs:
            actor = actor_of(ref)
            if managers.get(actor, "") not in tagged:
                out.setdefault(actor, set()).add(sid)
    return {actor: frozenset(found) for actor, found in out.items()}
