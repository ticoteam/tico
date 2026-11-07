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
