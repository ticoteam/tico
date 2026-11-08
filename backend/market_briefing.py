"""The weekly market briefing (docs/market-briefing.md): one page a week, written by the Librarian on Monday after its
sweep from the week's delta, the page monitors' changes, accepted listening items and new customer themes.

The briefing is a market page (`market/briefings/<week>`) and a row here that also lists its items, each tagged with the
market entities it touches. The server works out which bets those entities touch: an active bet of the published
strategy whose `cites`, or whose thesis wrong-if `market_id`, is one of them. It emits `market.briefing` with the
touched bets, so the Strategy Bot's routine on that event looks only at them; with none touched, it has nothing to do.
One row per ISO week: posting the same week again replaces it and keeps `created_at`. A briefing an automation posted
(`post_update` to market, backend/automation_actions.py) carries `source: {automation, run, task}`; a post by hand
clears it. `cadence` says how Market labels it: `daily` when an automation posted it on a schedule that runs on more
than one day a week (the built-in Market briefing, each morning) or on anything but a schedule, so it is dated by its
day; `weekly` for one posted by hand or by a once-a-week schedule.
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
    "created_at TEXT, updated_at TEXT)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)
    H.add_column(c, "market_briefings", "source", "TEXT")


def this_week(at=None):
    """The ISO week of a day: 2026-W44."""
    year, week, _ = (at or datetime.now(timezone.utc)).isocalendar()
    return f"{year}-W{week:02d}"


# ----------------------------------------------------------------------------- reading
def _row(row, body=True):
    out = dict(row)
    out["items"] = MK.loads(out.get("items"), [])
    out["bets"] = MK.loads(out.get("bets"), [])
    source = out.pop("source", None)
    if source:
        out["source"] = MK.loads(source, {})
    if not body:
        out.pop("body", None)
    return out


def touched(c, ids):
    """[{id, n, statement}] for these bet ids among the published version's active bets; `n` is the bet's number on
    the Strategy page (its place among the active ones), so a card can say "Bet 2"."""
    published = S.current(c)
    active = [b for b in (published or {}).get("bets") or [] if b.get("status") == "active"]
    return [{"id": b["id"], "n": n, "statement": b["statement"]} for n, b in enumerate(active, 1) if b["id"] in ids]


def _once_a_week(cron):
    """A five-field cron that runs on one day of the week only: `0 7 * * 1`, not `0 7 * * *` or `0 7 * * 1-5`."""
    fields = str(cron or "").split()
    return len(fields) == 5 and fields[2] == "*" and re.fullmatch(r"[0-7]|[A-Za-z]{3}", fields[4]) is not None


def cadence(c, source):
    """`daily` or `weekly` (see the module's note): read off the posting automation's trigger."""
    aid = (source or {}).get("automation") if isinstance(source, dict) else None
    if not aid:
        return "weekly"
    row = c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()
    trigger = (MK.loads(row["flow"], {}) if row else {}).get("trigger")
    trigger = trigger if isinstance(trigger, dict) else {}
    return "weekly" if trigger.get("type") == "schedule" and _once_a_week(trigger.get("cron")) else "daily"


def _view(c, row, body=True):
    out = _row(row, body)
    out["touched"] = touched(c, set(out["bets"]))
    out["cadence"] = cadence(c, out.get("source"))
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


def post(c, who, week, title, body, items, source=None):
    """Write (or replace) the week's briefing, its market page, and emit `market.briefing`. The caller has checked the
    writer. `items=None` keeps the week's stored items and bets. `source` is the automation run that posted it, if one
    did. Returns the briefing."""
    if not WEEK.match(week):
        raise Problem("validation", "week is an ISO week like 2026-W44", 422)
    before = c.execute("SELECT * FROM market_briefings WHERE week=?", (week,)).fetchone()
    if items is None:
        items = MK.loads(before["items"], []) if before else []
        bets = MK.loads(before["bets"], []) if before else []
    else:
        items = [{"text": " ".join(i.text.split()), "kind": i.kind, "entity_ids": list(dict.fromkeys(i.entity_ids))}
                 for i in items]
        if any(not i["text"] for i in items):
            raise Problem("validation", "each item says what happened, in a sentence", 422)
        bets = bets_touched(c, items)
    now = H.now()
    MK.write_page(c, who.actor, PAGE + week, title, body, CATEGORY, replace=True, record=False)
    if before:
        c.execute("UPDATE market_briefings SET title=?, body=?, items=?, bets=?, updated_at=?, source=? WHERE week=?",
                  (title, body, MK.dumps(items), MK.dumps(bets), now, MK.dumps(source) if source else None, week))
    else:
        c.execute("INSERT INTO market_briefings (id, week, title, body, items, bets, created_by, created_at, updated_at, "
                  "source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (H.new_id(), week, title, body, MK.dumps(items), MK.dumps(bets), who.actor, now, now,
                   MK.dumps(source) if source else None))
    H.event(c, who.actor, "market.briefing", week, {"items": len(items), "bets": len(bets), "replaced": bool(before)})
    # One event per week; a replacement that touches other bets than before is a new occurrence for them.
    again = MK.loads(before["bets"], []) if before else []
    if not before or (bets and set(bets) != set(again)):
        subject = week if not before else week + "." + hashlib.sha256(",".join(sorted(bets)).encode()).hexdigest()[:6]
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
