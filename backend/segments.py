"""Business segments (docs/strategy.md#segments): the groups of customers the company wins differently, each with its own
strategy, market, people, goals, budget and P&L line under the one company strategy.

A segment is a market graph `segment` entity (backend/market.py) the company plays in: `company/self` has a current
`operates_in` edge to it (backend/strategy_market.py). There is no second segment concept.
"""

from . import hubdb as H
from . import market as MK
from .store import Problem

ALL = "all"  # a teammate tag: works across every segment


def ours_ids(c):
    """The ids of the company's segments: active `segment` entities with a current `operates_in` edge from
    `company/self`."""
    today = H.now()[:10]
    rows = c.execute(
        "SELECT e.dst FROM market_edges e JOIN market_entities s ON s.id=e.dst WHERE e.rel='operates_in' AND e.src=? "
        "AND s.type='segment' AND s.status='active' AND (e.valid_from_on IS NULL OR substr(e.valid_from_on,1,10)<=?) "
        "AND (e.valid_until_on IS NULL OR substr(e.valid_until_on,1,10)>=?)",
        (MK.OWN_COMPANY_ID, today, today),
    )
    return {r[0] for r in rows}


def require_ours(c, segment_id):
    """`segment_id` normalised: "" (no segment) stays "", anything else must be one of the company's segments."""
    sid = str(segment_id or "").strip()
    if sid and sid not in ours_ids(c):
        raise Problem("not_found", f"{sid} is not one of the company's segments", 404)
    return sid


# provisional: track A replaces these with the teammate tags and the leads of a segment (the finance and goals tracks
# build against these names). Until segment_members exists they say nothing is tagged.
def has_members(c):
    """Whether the segment_members table exists: the teammates track creates it, and until then nobody is tagged."""
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='segment_members'").fetchone() is not None


def _actor(ref):
    """A teammate ref as a finance actor: `person:<id>` is `human:<id>`, a `bot:<id>` stays as it is."""
    kind, _, ident = str(ref).partition(":")
    return "human:" + ident if kind == "person" else str(ref)


def member_segments(c, ref):
    """The segments a teammate ref (`person:<id>` or `bot:<id>`) is tagged with: segment ids, `ALL` for All segments."""
    if not has_members(c):
        return []
    rows = c.execute("SELECT segment_id FROM segment_members WHERE member=? ORDER BY segment_id", (ref,))
    return [r[0] for r in rows]


def leads(c, segment_id, managers):
    """The teammate refs that lead a segment: dedicated to it, and their manager is not. `managers` is the org chart's
    reports-to map, actor to actor (Finance reads it; this module does not)."""
    if not has_members(c) or segment_id == ALL:
        return []
    members = [
        r[0] for r in c.execute("SELECT member FROM segment_members WHERE segment_id=? ORDER BY member", (segment_id,))
    ]
    dedicated = {_actor(m) for m in members}
    return [m for m in members if managers.get(_actor(m), "") not in dedicated]
