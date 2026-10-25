"""The weekly market briefing (docs/market-briefing.md): one page a week, written by the Librarian on Monday after its
sweep from the week's delta, the page monitors' changes, accepted listening items and new customer themes.

The briefing is a market page (`market/briefings/<week>`) and a row here that also lists its items, each tagged with the
market entities it touches. The server works out which bets those entities touch: an active bet of the published
strategy whose `cites`, or whose thesis wrong-if `market_id`, is one of them. It emits `market.briefing` with the
touched bets, so the CEO Bot's routine on that event looks only at them; with none touched, it has nothing to do.
One row per ISO week: posting the same week again replaces it and keeps `created`.
"""
import hashlib
import re
from datetime import datetime, timezone

from fastapi import Query, Request
from pydantic import Field

from . import hubdb as H
from . import market as MK
from . import models as M
from . import strategy as S
from .store import Problem

WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
KINDS = ("change", "signal", "theme", "size")
BODY_MAX = 20_000
TITLE_MAX = 200
ITEMS_MAX = 100
TEXT_MAX = 600
ENTITIES_MAX = 20
LIMIT_MAX = 52
EVENT = "market.briefing"
PAGE = "market/briefings/"
CATEGORY = "Market / Briefings"

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS market_briefings (id TEXT PRIMARY KEY, week TEXT NOT NULL UNIQUE, title TEXT NOT NULL, "
    "body TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]', bets TEXT NOT NULL DEFAULT '[]', created_by TEXT, "
    "created TEXT, updated TEXT)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def this_week(at=None):
    """The ISO week of a day: 2026-W44."""
    year, week, _ = (at or datetime.now(timezone.utc)).isocalendar()
    return f"{year}-W{week:02d}"


# ----------------------------------------------------------------------------- reading
def _row(row, body=True):
    out = dict(row)
    out["items"] = MK.loads(out.get("items"), [])
    out["bets"] = MK.loads(out.get("bets"), [])
    if not body:
        out.pop("body", None)
    return out


def touched(c, ids):
    """[{id, n, statement}] for these bet ids among the published version's active bets; `n` is the bet's number on
    the Strategy page (its place among the active ones), so a card can say "Bet 2"."""
    published = S.current(c)
    active = [b for b in (published or {}).get("bets") or [] if b.get("status") == "active"]
    return [{"id": b["id"], "n": n, "statement": b["statement"]} for n, b in enumerate(active, 1) if b["id"] in ids]


def _view(c, row, body=True):
    out = _row(row, body)
    out["touched"] = touched(c, set(out["bets"]))
    return out


def listing(c, limit=LIMIT_MAX):
    limit = max(1, min(int(limit or LIMIT_MAX), LIMIT_MAX))
    return [_view(c, r, body=False) for r in c.execute("SELECT * FROM market_briefings ORDER BY week DESC LIMIT ?", (limit,))]


def get(c, week):
    row = c.execute("SELECT * FROM market_briefings WHERE week=?", (week,)).fetchone()
    return _view(c, row) if row else None


def latest(c):
    row = c.execute("SELECT * FROM market_briefings ORDER BY week DESC LIMIT 1").fetchone()
    return _view(c, row) if row else None


# ----------------------------------------------------------------------------- writing
def bets_touched(c, items):
    """The ids of the active bets of the published strategy that cite one of the items' entities, or whose thesis
    wrong-if watches one."""
    ids = {e for item in items for e in item["entity_ids"]}
    published = S.current(c)
    out = []
    for b in (published or {}).get("bets") or []:
        if b.get("status") != "active":
            continue
        wrong = b.get("wrong_if") or {}
        watching = set(b.get("cites") or [])
        if wrong.get("kind") == "thesis" and wrong.get("market_id"):
            watching.add(wrong["market_id"])
        if ids & watching:
            out.append(b["id"])
    return out


def post(c, who, week, title, body, items):
    """Write (or replace) the week's briefing, its market page, and emit `market.briefing`. The caller has checked the
    writer. Returns the briefing."""
    from . import routines
    if not WEEK.match(week):
        raise Problem("validation", "week is an ISO week like 2026-W44", 422)
    items = [{"text": " ".join(i.text.split()), "kind": i.kind, "entity_ids": list(dict.fromkeys(i.entity_ids))}
             for i in items]
    if any(not i["text"] for i in items):
        raise Problem("validation", "each item says what happened, in a sentence", 422)
    bets = bets_touched(c, items)
    before = c.execute("SELECT * FROM market_briefings WHERE week=?", (week,)).fetchone()
    now = H.now()
    MK.write_page(c, who.actor, PAGE + week, title, body, CATEGORY, replace=True, record=False)
    if before:
        c.execute("UPDATE market_briefings SET title=?, body=?, items=?, bets=?, updated=? WHERE week=?",
                  (title, body, MK.dumps(items), MK.dumps(bets), now, week))
    else:
        c.execute("INSERT INTO market_briefings (id, week, title, body, items, bets, created_by, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (H.new_id(), week, title, body, MK.dumps(items), MK.dumps(bets),
                                                   who.actor, now, now))
    H.event(c, who.actor, "market.briefing", week, {"items": len(items), "bets": len(bets), "replaced": bool(before)})
    # One event per week; a replacement that touches other bets than before is a new occurrence for them.
    again = MK.loads(before["bets"], []) if before else []
    if not before or (bets and set(bets) != set(again)):
        subject = week if not before else week + "." + hashlib.sha256(",".join(sorted(bets)).encode()).hexdigest()[:6]
        routines.emit(c, EVENT, subject, {"week": week, "bets": ", ".join(bets)}, title=title)
        H.hook(c, "market.briefing", subject, lambda: {"week": week, "bets": list(bets)}, who.actor, revision=0)
    return get(c, week)


# ----------------------------------------------------------------------------- HTTP
class BriefingItem(M.Contract):
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    kind: str = Field(default="change", pattern="^(" + "|".join(KINDS) + ")$")
    entity_ids: list[str] = Field(default_factory=list, max_length=ENTITIES_MAX)


class BriefingPost(M.Contract):
    week: str = Field(min_length=1, max_length=10)
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    body: str = Field(min_length=1, max_length=BODY_MAX)
    items: list[BriefingItem] = Field(default_factory=list, max_length=ITEMS_MAX)


def install(app, store, auth, mutate):
    @app.get("/api/v2/market/briefings")
    def briefings(request: Request, limit: int = Query(default=LIMIT_MAX, ge=1, le=LIMIT_MAX)):
        """The weekly briefings, newest first, without their bodies. Market readers."""
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"briefings": listing(c, limit)}

    @app.get("/api/v2/market/briefings/latest")
    def briefing_latest(request: Request):
        """The newest briefing with its body, or `null` before the first. `touched` names the bets it touches."""
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"briefing": latest(c)}

    @app.get("/api/v2/market/briefings/{week}")
    def briefing_show(request: Request, week: str):
        auth.domain(request.state.identity)
        with store.read() as c:
            row = get(c, week)
        if not row:
            raise Problem("not_found", f"There is no briefing for {week}", 404)
        return {"briefing": row}

    @app.post("/api/v2/market/briefings")
    def briefing_post(request: Request, body: BriefingPost):
        """The Librarian's (or the owner's) briefing for a week; the same week again replaces it."""
        who = request.state.identity

        def work(c):
            MK.require_writer(who)
            return {"briefing": post(c, who, body.week, body.title, body.body, body.items)}

        return mutate(request, body, work)
