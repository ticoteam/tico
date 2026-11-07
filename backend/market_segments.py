"""The Market page scoped to the company's segments (docs/market-briefing.md, docs/strategy.md#segments): the segments
side by side, one segment's page, and the Librarian's research task for one segment.

Every number is read from stored rows, never estimated, and a number that is not known is null, not zero: a segment's
accepted size (`market_sizes.spend_usd`, worked out again on the shared parameters as the market page does), its
`growth_pct`, the company's newest revenue or ARR figure (`market_figures`), the newest briefing (`market_briefings`).
"""
# pyright: strict

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request
from pydantic import Field

from . import hubdb as H
from . import market as MK
from . import market_briefing as MB
from . import market_spend as MS
from . import models as M
from . import segments as SG
from .store import Problem, Store

if TYPE_CHECKING:
    from .auth import Auth

Conn = sqlite3.Connection
Row = dict[str, Any]

DIMENSION = "offering"  # the market page's defaults, so these numbers match its first view
BASIS = "product"
CHANGED_MAX = 30
WHAT_CHANGED_MAX = 30
NOTE_MAX = 1000
LIBRARIAN = "bot:librarian"
RESEARCH_EVENT = "market.segment_research_requested"

# The helpers of the modules that are not strict yet, each cast to the signature this module uses it with (the way
# ask_refusals.py and heartbeat.py do it). The private ones are read on purpose: the roll-up and the market page read
# them the same way, so this page cannot drift from the numbers the market page shows.
_ordered_segments = cast(Callable[[Conn], list[Row]], SG.ordered)  # each segment's color and rank come from here
_require_ours = cast(Callable[[Conn, str], str], SG.require_ours)
_market_entity = cast(Callable[[Conn, str], Row | None], MK.entity)
_for_task = cast(Callable[[str], str], MK._for_task)  # pyright: ignore[reportPrivateUsage]
_definition = cast(Callable[[Conn], Row | None], MS.definition)
_shared_values = cast(Callable[[Row | None], dict[str, Any]], MS.shared_values)
_tree = cast(Callable[[Conn, Any], Row], MS._tree)  # pyright: ignore[reportPrivateUsage]
_size_row = cast(Callable[[Any], Row], MS._size_row)  # pyright: ignore[reportPrivateUsage]
_effective = cast(Callable[[Row, dict[str, Any]], Row], MS.effective)
_rolled = cast(
    Callable[[list[str], Any, dict[str, Row | None]], dict[str, Row | None]],
    MS._rolled,  # pyright: ignore[reportPrivateUsage]
)
_spend = cast(Callable[..., Row], MS.spend)
_segment_entry = cast(Callable[..., Row | None], MS.segment_entry)
_active_bets = cast(Callable[[Conn], list[Row]], MB.active_bets)
_latest = cast(Callable[..., Row | None], MB.latest)
_task_create = cast(Callable[..., Row], H.task_create)
_event = cast(Callable[..., Any], H.event)


def _ours(c: Conn, segment_id: str) -> str:
    sid = _require_ours(c, segment_id)
    if not sid:
        raise Problem("not_found", "There is no segment with that id", 404)
    return sid


def _entity(c: Conn, sid: str) -> Row:
    """The market entity of one of the company's segments: ids the caller has checked with require_ours."""
    ent = _market_entity(c, sid)
    if ent is None:
        raise Problem("not_found", f"{sid} is not a segment of the market", 404)
    return ent


def _listed(c: Conn, sid: str) -> Row:
    """One of the company's segments as the list shows it (core's `ordered`): its color and its rank in the list."""
    row = next((seg for seg in _ordered_segments(c) if seg["id"] == sid), None)
    if row is None:
        raise Problem("not_found", f"{sid} is not one of the company's segments", 404)
    return row


# ----------------------------------------------------------------------------- compare
def _tagged(briefing: Row | None, segment_id: str) -> list[Row]:
    """The briefing's items, each tagged with its scope and its week ('' is the whole market)."""
    if not briefing:
        return []
    return [{**item, "segment_id": segment_id, "week": briefing["week"]} for item in briefing["items"]]


def _round_robin(lists: list[list[Row]], limit: int) -> list[Row]:
    """One item from each list in turn, until `limit` are taken, so no list crowds out the others."""
    out: list[Row] = []
    for i in range(max((len(rows) for rows in lists), default=0)):
        for rows in lists:
            if i < len(rows) and len(out) < limit:
                out.append(rows[i])
    return out


def compare(c: Conn) -> dict[str, Any]:
    """Every segment the company plays in, in the company's list order, and the newest briefing items across the whole
    market and them.

    Each segment: `money` is its accepted size in USD (`market_sizes.spend_usd`). `yearly_spend` is that money, or where
    the segment has no accepted size, the sum of its sub-segments' yearly spend at any depth (the roll-up `member_of`
    gives, counting accepted sizes only), or null. `growth` is the accepted size's `growth_pct`, a percent a year.
    `our_share` is the company's newest `market_figures` row for the segment with metric revenue or arr, over
    `yearly_spend`, as a percent to one decimal. Only a figure whose `as_of` is in the spend year counts: one from another
    year leaves `our_share` null. `briefing_week` is the week of the segment's newest briefing.

    `changed` is the newest briefing's items of the whole market (`segment_id` '') and of each segment, taken round-robin
    across them so none crowds out the rest: at most CHANGED_MAX in all, newest week first. Each item carries its
    `segment_id` and its `week`. The published active bets are read once for all the briefings.
    """
    defn = _definition(c)
    shared = _shared_values(defn)
    opts: Row = defn or {}
    year = int(opts.get("year") or datetime.now(timezone.utc).year)
    root = opts.get("root")
    tree: Row = _tree(c, root) if root else {"order": [], "parent": {}, "rows": {}}
    listed = _ordered_segments(c)
    scope = sorted(set(tree["order"]) | {seg["id"] for seg in listed})
    first: dict[str, Row] = {}
    for row in c.execute(
        "SELECT * FROM market_sizes WHERE dimension=? AND year=? AND basis=? AND status='accepted' "
        "AND segment_id IN (SELECT value FROM json_each(?)) ORDER BY created_at, id",
        (DIMENSION, year, BASIS, json.dumps(scope)),
    ):
        first.setdefault(row["segment_id"], row)
    accepted: dict[str, Row] = {sid: _effective(_size_row(row), shared) for sid, row in first.items()}
    rolled = _rolled(tree["order"], tree["parent"], {sid: accepted.get(sid) for sid in tree["order"]})
    active = _active_bets(c)

    segments: list[Row] = []
    lists: list[list[Row]] = [_tagged(_latest(c, "", body=False, active=active), "")]
    for seg in listed:
        sid: str = seg["id"]
        own: Row = accepted.get(sid) or {}
        spend: Any
        if sid in rolled:
            roll: Row = rolled[sid] or {}
            spend = roll.get("spend_usd")
        else:
            spend = own.get("spend_usd")
        figure = c.execute(
            "SELECT value FROM market_figures WHERE entity_id=? AND segment_id=? AND metric IN ('revenue','arr') "
            "AND value IS NOT NULL AND substr(as_of,1,4)=? ORDER BY as_of DESC, created_at DESC, id LIMIT 1",
            (MK.OWN_COMPANY_ID, sid, str(year)),
        ).fetchone()
        share: float | None = round(figure["value"] / spend * 100, 1) if figure and spend and spend > 0 else None
        briefing = _latest(c, sid, body=False, active=active)
        lists.append(_tagged(briefing, sid))
        segments.append(
            {
                "id": sid,
                "name": seg["name"],
                "color": seg["color"],
                "money": own.get("spend_usd"),
                "yearly_spend": spend,
                "growth": own.get("growth_pct"),
                "our_share": share,
                "briefing_week": briefing["week"] if briefing else None,
            }
        )
    changed = _round_robin(lists, CHANGED_MAX)
    changed.sort(key=lambda item: item["week"], reverse=True)
    return {"segments": segments, "changed": changed}


# ----------------------------------------------------------------------------- one segment's page
def page(c: Conn, sid: str) -> dict[str, Any]:
    """One segment's market: its name and one-line who, the five answers and briefing of its newest briefing, its sizes
    as the market page shows them, its players, and the history of it, its sub-segments and its players."""
    listed = _listed(c, sid)
    view = _spend(c, DIMENSION, BASIS)
    entry: Row = (
        next((s for s in view["segments"] if s["id"] == sid), None)
        or _segment_entry(c, sid, DIMENSION, BASIS, view["year"])  # no definition, or outside its tree
        or {}
    )
    briefing = _latest(c, sid)
    shown: Row = briefing or {}
    players: list[Row] = entry.get("players", [])
    scope = sorted({sid, *_tree(c, sid)["order"], *(p["entity_id"] for p in players)})
    history = c.execute(
        f"SELECT {MK.EVENT_COLUMNS} FROM {MK.EVENTS} AND subject_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (json.dumps(scope), WHAT_CHANGED_MAX),
    ).fetchall()
    what_changed: list[Row] = [dict(row) for row in history]
    return {
        "segment": {
            "id": sid,
            "name": listed["name"],
            "who": listed["who"],
            "color": listed["color"],
            "rank": listed["rank"],
        },
        "answers": shown.get("answers", {}),
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
        "what_changed": what_changed,
    }


# ----------------------------------------------------------------------------- research
def _open_research(c: Conn, sid: str) -> str | None:
    """The open Librarian task that a research request filed for this segment. It is found by the segment's id (the
    event's `segment_id`), so a renamed segment keeps its task, and a task that only has the same title does not count."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    row = c.execute(
        "SELECT t.id FROM events e JOIN tasks t ON t.id = e.target "
        f"WHERE e.action=? AND json_extract(e.detail, '$.segment_id')=? AND t.owner_id=? AND t.status IN ({marks}) "
        "ORDER BY e.created_at DESC, e.id DESC LIMIT 1",
        (RESEARCH_EVENT, sid, LIBRARIAN, *H.ACTIVE_STATUSES),
    ).fetchone()
    return row["id"] if row else None


def _free_title(c: Conn, actor: str, base: str) -> str:
    marks = ",".join("?" * len(H.LIVE_STATUSES))
    for n in range(1, 40):
        title = base if n == 1 else f"{base} ({n})"
        if not c.execute(
            f"SELECT 1 FROM tasks WHERE requester_id=? AND owner_id=? AND title=? AND status IN ({marks})",
            (actor, LIBRARIAN, title, *H.LIVE_STATUSES),
        ).fetchone():
            return title
    return f"{base} (40)"


def research(c: Conn, auth: "Auth", who: Any, sid: str, note: str = "") -> dict[str, Any]:
    """Ask the Librarian for one segment's five answers and its sizes. A person may ask. A bot may ask only when the
    bot-contact rules let it open a task with the Librarian (else 403, and it gets no task). While a task for the segment
    is open, asking again returns that task."""
    from . import getting_started as GS
    from . import rooms

    cast(Callable[[Any], object], auth.domain)(who)
    cast(Callable[..., None], auth.require_bot_contact)(c, who, LIBRARIAN, kind="task")
    name: str = _entity(c, sid)["name"]
    found = _open_research(c, sid)
    if found:
        return {"task_id": found, "created": False}
    base = f"Market for {name}"[:200]
    librarian_running = cast(Callable[[Conn, str], object], GS._active)  # pyright: ignore[reportPrivateUsage]
    if not librarian_running(c, GS.LIBRARIAN):
        raise Problem("librarian", "The Librarian is not running yet. It starts once a computer hosts it.", 409)
    lines = [
        f"Answer the five Market questions for the segment {name} ({sid}) and size it. "
        "Only this segment: the company's market as a whole is someone else's task.",
        f"Post its briefing with `hub market briefing post --segment {sid} --week <this ISO week>` and an answers file "
        "(playbooks/write-the-briefing.md, step 3b), and file its sizes with "
        f"`hub market size add --segment {sid}`, proposed, not accepted (playbooks/size-the-market.md).",
    ]
    safe_note = _for_task((note or "").strip())
    if safe_note:
        lines += ["", f"Asked: {safe_note}"]
    conversation = cast(Callable[..., str | None], rooms.task_conversation_id)(c, auth, LIBRARIAN, who.actor)
    task = _task_create(
        c,
        who.actor,
        _free_title(c, who.actor, base),
        "\n".join(lines),
        LIBRARIAN,
        allow_planned=True,
        conversation_id=conversation,
    )
    _event(c, who.actor, RESEARCH_EVENT, task["id"], {"segment_id": sid})
    return {"task_id": task["id"], "created": True}


# ----------------------------------------------------------------------------- HTTP
class SegmentResearch(M.Contract):
    note: str = Field(default="", max_length=NOTE_MAX)


def install(app: FastAPI, store: Store, auth: "Auth", mutate: Callable[..., Any]) -> None:
    domain = cast(Callable[[Any], object], auth.domain)

    @app.get("/api/v2/market/segments/compare")
    def segments_compare(request: Request) -> dict[str, Any]:
        """Every segment the company plays in, side by side (money, yearly spend, growth, our share, newest briefing),
        and the newest changes across them, each tagged with its segment. Market readers."""
        domain(request.state.identity)
        with cast("AbstractContextManager[Conn]", store.read()) as c:
            return compare(c)

    @app.get("/api/v2/market/segments/{segment_id:path}/page")
    def segment_page(request: Request, segment_id: str) -> dict[str, Any]:
        """One segment's market page. Market readers. An id that is not one of the company's segments is a 404."""
        domain(request.state.identity)
        with cast("AbstractContextManager[Conn]", store.read()) as c:
            return page(c, _ours(c, segment_id))

    @app.post("/api/v2/market/segments/{segment_id:path}/research")
    def segment_research(request: Request, segment_id: str, body: SegmentResearch) -> dict[str, Any]:
        """Ask the Librarian for the segment's five answers and sizes. Idempotent while its task is open: the open task
        comes back with `created: false`."""
        who = request.state.identity

        def work(c: Conn) -> dict[str, Any]:
            return research(c, auth, who, _ours(c, segment_id), body.note)

        return mutate(request, body, work)
