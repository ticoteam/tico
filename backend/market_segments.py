"""The Market page scoped to the company's segments (docs/market-briefing.md, docs/strategy.md#segments): the segments
side by side, one segment's page, and the Librarian's research task for one segment.

Every number is read from stored rows, never estimated, and a number that is not known is null, not zero: a segment's
accepted size (`market_sizes.spend_usd`, worked out again on the shared parameters as the market page does), its
`growth_pct`, the company's newest revenue or ARR figure (`market_figures`), the newest briefing (`market_briefings`).
"""

import json
from datetime import datetime, timezone

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from . import market as MK
from . import market_briefing as MB
from . import market_spend as MS
from . import models as M
from . import segments as SG
from .store import Problem

DIMENSION = "offering"  # the market page's defaults, so these numbers match its first view
BASIS = "product"
COLORS = ("blue", "green", "amber", "red", "purple", "teal", "pink", "gray")
CHANGED_MAX = 30
WHAT_CHANGED_MAX = 30
NOTE_MAX = 1000
LIBRARIAN = "bot:librarian"
RESEARCH_EVENT = "market.segment_research_requested"


def _ours(c, segment_id):
    sid = SG.require_ours(c, segment_id)
    if not sid:
        raise Problem("not_found", "There is no segment with that id", 404)
    return sid


def _rank(ent):
    rank = ent["properties"].get("rank")
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else None


def _color(ent):
    color = ent["properties"].get("color")
    return color if color in COLORS else None


# ----------------------------------------------------------------------------- compare
def compare(c):
    """Every segment the company plays in, and the newest briefing items across them.

    Each segment: `money` is its accepted size in USD (`market_sizes.spend_usd`). `yearly_spend` is that money, or where
    the segment has no accepted size, the sum of its sub-segments' yearly spend at any depth (the roll-up `member_of`
    gives, counting accepted sizes only), or null. `growth` is the accepted size's `growth_pct`, a percent a year.
    `our_share` is the company's newest `market_figures` row for the segment with metric revenue or arr, over
    `yearly_spend`, as a percent to one decimal. `briefing_week` is the week of the segment's newest briefing.
    """
    defn = MS.definition(c)
    shared = MS.shared_values(defn)
    year = int((defn or {}).get("year") or datetime.now(timezone.utc).year)
    root = (defn or {}).get("root")
    tree = MS._tree(c, root) if root else {"order": [], "parent": {}, "rows": {}}
    ents = sorted(
        (MK.entity(c, sid) for sid in SG.ours_ids(c)),
        key=lambda e: (_rank(e) is None, _rank(e) or 0, (e["name"] or "").lower(), e["id"]),
    )
    scope = sorted(set(tree["order"]) | {e["id"] for e in ents})
    first = {}
    for row in c.execute(
        "SELECT * FROM market_sizes WHERE dimension=? AND year=? AND basis=? AND status='accepted' "
        "AND segment_id IN (SELECT value FROM json_each(?)) ORDER BY created_at, id",
        (DIMENSION, year, BASIS, json.dumps(scope)),
    ):
        first.setdefault(row["segment_id"], row)
    accepted = {sid: MS.effective(MS._size_row(row), shared) for sid, row in first.items()}
    rolled = MS._rolled(tree["order"], tree["parent"], {sid: accepted.get(sid) for sid in tree["order"]})

    segments, changed = [], []
    for ent in ents:
        sid = ent["id"]
        own = accepted.get(sid)
        if sid in rolled:
            spend = (rolled[sid] or {}).get("spend_usd")
        else:
            spend = (own or {}).get("spend_usd")
        figure = c.execute(
            "SELECT value FROM market_figures WHERE entity_id=? AND segment_id=? AND metric IN ('revenue','arr') "
            "AND value IS NOT NULL ORDER BY as_of DESC, created_at DESC, id LIMIT 1",
            (MK.OWN_COMPANY_ID, sid),
        ).fetchone()
        share = round(figure["value"] / spend * 100, 1) if figure and spend and spend > 0 else None
        briefing = MB.latest(c, sid, body=False)
        if briefing:
            changed.extend({**item, "segment_id": sid, "week": briefing["week"]} for item in briefing["items"])
        segments.append(
            {
                "id": sid,
                "name": ent["name"],
                "color": _color(ent),
                "money": (own or {}).get("spend_usd"),
                "yearly_spend": spend,
                "growth": (own or {}).get("growth_pct"),
                "our_share": share,
                "briefing_week": briefing["week"] if briefing else None,
            }
        )
    changed.sort(key=lambda item: item["week"], reverse=True)
    return {"segments": segments, "changed": changed[:CHANGED_MAX]}


# ----------------------------------------------------------------------------- one segment's page
def page(c, sid):
    """One segment's market: its name and one-line who, the five answers and briefing of its newest briefing, its sizes
    as the market page shows them, its players, and the history of it, its sub-segments and its players."""
    ent = MK.entity(c, sid)
    view = MS.spend(c, DIMENSION, BASIS)
    entry = next((s for s in view["segments"] if s["id"] == sid), None) or {}
    briefing = MB.latest(c, sid)
    players = entry.get("players", [])
    scope = sorted({sid, *MS._tree(c, sid)["order"], *(p["entity_id"] for p in players)})
    history = c.execute(
        f"SELECT {MK.EVENT_COLUMNS} FROM {MK.EVENTS} AND subject_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (json.dumps(scope), WHAT_CHANGED_MAX),
    ).fetchall()
    return {
        "segment": {"id": sid, "name": ent["name"], "who": ent["summary"], "color": _color(ent), "rank": _rank(ent)},
        "answers": (briefing or {}).get("answers", {}),
        "briefing": briefing,
        "sizes": {
            "year": view["year"],
            "dimension": view["dimension"],
            "basis": view["basis"],
            "chosen": entry.get("size"),
            "rolled_usd": entry.get("rolled_usd"),
            "estimates": entry.get("estimates", []),
            "disagree": entry.get("disagree", False),
            "bases": entry.get("bases", []),
        },
        "players": players,
        "what_changed": [dict(row) for row in history],
    }


# ----------------------------------------------------------------------------- research
def _like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _open_research(c, title):
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    row = c.execute(
        f"SELECT id FROM tasks WHERE owner_id=? AND (title=? OR title LIKE ? ESCAPE '\\') AND status IN ({marks}) "
        "ORDER BY created_at DESC LIMIT 1",
        (LIBRARIAN, title, _like(title) + " (%", *H.ACTIVE_STATUSES),
    ).fetchone()
    return row["id"] if row else None


def _free_title(c, actor, base):
    marks = ",".join("?" * len(H.LIVE_STATUSES))
    for n in range(1, 40):
        title = base if n == 1 else f"{base} ({n})"
        if not c.execute(
            f"SELECT 1 FROM tasks WHERE requester_id=? AND owner_id=? AND title=? AND status IN ({marks})",
            (actor, LIBRARIAN, title, *H.LIVE_STATUSES),
        ).fetchone():
            return title
    return f"{base} (40)"


def research(c, auth, who, sid, note=""):
    """Ask the Librarian for one segment's five answers and its sizes. A person or a bot may ask. While a task for the
    segment is open, asking again returns that task."""
    from . import getting_started as GS
    from . import rooms

    auth.domain(who)
    name = MK.entity(c, sid)["name"]
    base = f"Market for {name}"[:200]
    found = _open_research(c, base)
    if found:
        return {"task_id": found, "created": False}
    if not GS._active(c, GS.LIBRARIAN):
        raise Problem("librarian", "The Librarian is not running yet. It starts once a computer hosts it.", 409)
    lines = [
        f"Answer the five Market questions for the segment {name} ({sid}) and size it. "
        "Only this segment: the company's market as a whole is someone else's task.",
        f"Post its briefing with `hub market briefing post --segment {sid} --week <this ISO week>` and an answers file "
        "(playbooks/write-the-briefing.md, step 3b), and file its sizes with "
        f"`hub market size add --segment {sid}`, proposed, not accepted (playbooks/size-the-market.md).",
    ]
    safe_note = MK._for_task((note or "").strip())
    if safe_note:
        lines += ["", f"Asked: {safe_note}"]
    task = H.task_create(
        c,
        who.actor,
        _free_title(c, who.actor, base),
        "\n".join(lines),
        LIBRARIAN,
        allow_planned=True,
        conversation_id=rooms.task_conversation_id(c, auth, LIBRARIAN, who.actor),
    )
    H.event(c, who.actor, RESEARCH_EVENT, task["id"], {"segment_id": sid})
    return {"task_id": task["id"], "created": True}


# ----------------------------------------------------------------------------- HTTP
class SegmentResearch(M.Contract):
    note: str = Field(default="", max_length=NOTE_MAX)


def install(app, store, auth, mutate):
    @app.get("/api/v2/market/segments/compare")
    def segments_compare(request: Request):
        """Every segment the company plays in, side by side (money, yearly spend, growth, our share, newest briefing),
        and the newest changes across them, each tagged with its segment. Market readers."""
        auth.domain(request.state.identity)
        with store.read() as c:
            return compare(c)

    @app.get("/api/v2/market/segments/{segment_id:path}/page")
    def segment_page(request: Request, segment_id: str):
        """One segment's market page. Market readers. An id that is not one of the company's segments is a 404."""
        auth.domain(request.state.identity)
        with store.read() as c:
            return page(c, _ours(c, segment_id))

    @app.post("/api/v2/market/segments/{segment_id:path}/research")
    def segment_research(request: Request, segment_id: str, body: SegmentResearch):
        """Ask the Librarian for the segment's five answers and sizes. Idempotent while its task is open: the open task
        comes back with `created: false`."""
        who = request.state.identity

        def work(c):
            return research(c, auth, who, _ours(c, segment_id), body.note)

        return mutate(request, body, work)
