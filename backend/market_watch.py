"""Page monitors: the market graph's eyes on a competitor's own pages (docs/market-watch.md).

A watch is one public page of one market entity (its pricing, changelog, careers page...). Once a day the server
reads each active watch through `clients/doc_fetch.py`, the same guard `hub doc fetch` uses (public addresses only,
every redirect checked, 5 MB and 20 seconds), keeps the page's normalized text, and only when it changed by a real
amount stores a piece of evidence, files a `market_insights` row for the Librarian to curate and writes a
`market_events` row on the entity, so a bet that cites the entity trips the fit check (backend/strategy.py). No model
is called; the Librarian reads the insight. The server does this, not a bot, because a bot's watcher has no Tico
token to read the list.

The fetch runs on a daemon thread outside any database transaction; each result is written in its own short one.
The first read of a page is its baseline and files nothing. Rows are never deleted: stopping a watch ends it.
"""
import difflib
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from clients import doc_fetch as F
from . import hubdb as H
from . import market as MK
from . import models as M
from .store import Problem

log = logging.getLogger("tico.market_watch")

KINDS = ("pricing", "careers", "changelog", "docs", "home", "other")
MAX_ACTIVE = 200            # active watches
BATCH = 50                  # pages read per check, the longest-unchecked first
TEXT_MAX = 50_000           # of a page's normalized text kept for the next comparison
MIN_CHANGE = 40             # characters added or removed before a change counts
EXCERPT_MAX = 1_200         # of the diff kept as the evidence's quote
LINES_MAX = 3_000           # lines compared; a longer page is cut before the diff
STATUSES = ("ok", "error", "blocked")
DAY_KEY = "market.watch.checked"
ACTOR = H.KEEPER            # who writes what a check finds

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS market_watches (id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, url TEXT NOT NULL, "
    "kind TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_by TEXT, created_at TEXT, checked_at TEXT, changed_at TEXT, "
    "last_hash TEXT, last_text TEXT, last_status TEXT, error TEXT)",
    "CREATE INDEX IF NOT EXISTS market_watches_active ON market_watches(active, checked_at)",
    "CREATE INDEX IF NOT EXISTS market_watches_entity ON market_watches(entity_id)",
)

# Test seams: the name resolver and socket the fetch uses, and how the check thread is started.
RESOLVER = None
OPENER = None
_RUNNING = threading.Lock()


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def spawn(work):
    threading.Thread(target=work, name="market-watch", daemon=True).start()


# ----------------------------------------------------------------------------- text
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
# A line that is only digits and the marks between them, a date, or a copyright line: they change on every visit.
NOISE = re.compile(
    r"^(?:[\d\s.,:;/\\|+*#%$€£–—-]+"
    rf"|(?:{MONTHS}\.?\s+)?\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:{MONTHS}\.?\s+)?\d{{4}}"
    rf"|{MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    r"|(?:©|\(c\)|copyright)\b.*)$", re.I)


def normalize(text):
    """Visible text with whitespace collapsed, one line per line, and the lines that are only digits, dates or a
    copyright notice dropped."""
    lines = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.split())
        if line and not NOISE.match(line):
            lines.append(line)
    return "\n".join(lines)[:TEXT_MAX]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def changes(old, new):
    """(removed lines, added lines) between two normalized texts, as the diff sees them."""
    before, after = old.splitlines()[:LINES_MAX], new.splitlines()[:LINES_MAX]
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            removed.extend(before[i1:i2])
            added.extend(after[j1:j2])
    return removed, added


def size_of(removed, added):
    return sum(len(line) for line in removed) + sum(len(line) for line in added)


def excerpt(removed, added):
    """What changed, for a person: added lines first, then removed, as short as EXCERPT_MAX allows."""
    out, left = [], EXCERPT_MAX
    for sign, lines in (("+", added), ("-", removed)):
        for line in lines:
            piece = f"{sign} {line}"
            if len(piece) + 1 > left:
                piece = piece[:max(0, left - 2)].rstrip() + "…"
            if left < 4:
                return "\n".join(out)
            out.append(piece)
            left -= len(piece) + 1
    return "\n".join(out)


# ----------------------------------------------------------------------------- reading a page
def fetch_page(url):
    """The page's normalized text. Raises doc_fetch.FetchError. Tests patch this seam; the guard is the fetcher's."""
    page = F.fetch(url, TEXT_MAX * 2, env={}, resolver=RESOLVER or F.resolve, opener=OPENER or F.connect)
    return normalize(page["text"])


def read(url):
    """{status, text?, error?} for one page, never raising: a refusal by the guard is `blocked`, the rest `error`."""
    try:
        return {"status": "ok", "text": fetch_page(url)}
    except F.FetchError as exc:
        blocked = exc.code in ("scheme", "userinfo", "url", "port", "private_address")
        return {"status": "blocked" if blocked else "error", "error": f"{exc.code}: {exc.message}"[:300]}
    except Exception as exc:       # a page can be anything: the watch says so and the rest still run
        return {"status": "error", "error": type(exc).__name__}


# ----------------------------------------------------------------------------- rows
def row_of(row, text=False):
    row = dict(row)
    row["active"] = bool(row["active"])
    if not text:
        row.pop("last_text", None)
    return row


def get(c, watch_id):
    found = c.execute("SELECT * FROM market_watches WHERE id=?", (watch_id,)).fetchone()
    return dict(found) if found else None


def listing(c, entity_id=None):
    sql, args = "SELECT * FROM market_watches", ()
    if entity_id:
        sql, args = sql + " WHERE entity_id=?", (entity_id,)
    return [row_of(r) for r in c.execute(sql + " ORDER BY active DESC, created_at, id", args)]


def host_of(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def add(c, actor, entity_id, url, kind):
    """A new watch. The caller has checked the writer. The address passes the same guard the doc fetcher applies
    before any DNS lookup (a name that resolves to an internal address is refused by the fetch itself, every time)."""
    from . import strategy_setup
    if kind not in KINDS:
        raise Problem("validation", f"a watched page is {'|'.join(KINDS)}, not {kind}", 422)
    try:
        url = strategy_setup.normalize_url(url)
    except ValueError as exc:
        raise Problem("validation", str(exc), 422, extra={"reason": "url"}) from None
    if not url:
        raise Problem("validation", "give the page's address", 422)
    strategy_setup.refuse_unsafe(url)
    entity = MK.entity(c, entity_id)
    if not entity:
        raise Problem("not_found", f"There is no market entity {entity_id}", 404)
    if c.execute("SELECT 1 FROM market_watches WHERE active=1 AND url=?", (url,)).fetchone():
        raise Problem("exists", "That page is already watched", 409)
    if c.execute("SELECT count(*) FROM market_watches WHERE active=1").fetchone()[0] >= MAX_ACTIVE:
        raise Problem("limit", f"At most {MAX_ACTIVE} pages are watched at once; stop one first", 409)
    row = {"id": H.new_id(), "entity_id": entity_id, "url": url, "kind": kind, "created_by": actor, "created_at": H.now()}
    c.execute("INSERT INTO market_watches (id, entity_id, url, kind, active, created_by, created_at) "
              "VALUES (:id, :entity_id, :url, :kind, 1, :created_by, :created_at)", row)
    H.event(c, actor, "market.watch.add", row["id"], {"entity": entity_id, "kind": kind})
    return row_of(get(c, row["id"]))


def stop(c, actor, watch_id):
    row = get(c, watch_id)
    if not row:
        raise Problem("not_found", f"There is no watch {watch_id}", 404)
    if row["active"]:
        c.execute("UPDATE market_watches SET active=0 WHERE id=?", (watch_id,))
        H.event(c, actor, "market.watch.stop", watch_id, {"entity": row["entity_id"]})
    return row_of(get(c, watch_id))


# ----------------------------------------------------------------------------- checking
def due(c, limit=BATCH):
    """The active watches to read now, the longest unchecked first (never checked first of all)."""
    return [dict(r) for r in c.execute(
        "SELECT id, entity_id, url, kind FROM market_watches WHERE active=1 "
        "ORDER BY checked_at IS NOT NULL, checked_at, id LIMIT ?", (limit,))]


def record(c, watch, result):
    """One page's result, in the caller's short transaction. Returns True when it filed an insight."""
    row = get(c, watch["id"])
    if not row or not row["active"]:
        return False                # stopped while it was being read
    now = H.now()
    if result["status"] != "ok":
        c.execute("UPDATE market_watches SET checked_at=?, last_status=?, error=? WHERE id=?",
                  (now, result["status"], result.get("error") or "", row["id"]))
        return False
    text = result["text"]
    fresh = digest(text)
    if row["last_hash"] is None:    # the baseline
        c.execute("UPDATE market_watches SET checked_at=?, last_status='ok', error=NULL, last_hash=?, last_text=? WHERE id=?",
                  (now, fresh, text, row["id"]))
        return False
    c.execute("UPDATE market_watches SET checked_at=?, last_status='ok', error=NULL WHERE id=?", (now, row["id"]))
    if row["last_hash"] == fresh:
        return False                # nothing new
    removed, added = changes(row["last_text"] or "", text)
    if size_of(removed, added) < MIN_CHANGE:
        return False                # too small to report; the baseline stays, so slow edits add up
    c.execute("UPDATE market_watches SET changed_at=?, last_hash=?, last_text=? WHERE id=?", (now, fresh, text, row["id"]))
    file_change(c, row, removed, added, fresh)
    return True


def file_change(c, watch, removed, added, fresh):
    entity = MK.entity(c, watch["entity_id"])
    name = (entity or {}).get("name") or watch["entity_id"]
    quote = excerpt(removed, added)
    evidence, _ = MK.create_evidence(c, ACTOR, source_url=watch["url"], source_kind="page-change", captured_at=H.now(),
                                     quote=quote, our_read=f"The {watch['kind']} page of {name} changed.")
    insight = MK.report(c, ACTOR, kind="property-change", about=name, claim=f"Page changed: {name} {watch['kind']}",
                        source_url=watch["url"], quote=quote, source_ref=f"watch:{watch['id']}:{fresh[:12]}")
    # Recorded for the weekly delta, but no fit check: a diff is not yet a market fact. A bet trips when the Librarian
    # applies the insight to the entity (market.apply_insight), never on the raw change.
    MK._event(c, "entity", watch["entity_id"], ACTOR, f"page.{watch['kind']}", None,
              f"changed: {host_of(watch['url'])}", insight["id"], evidence["id"], trip=False)
    return insight


def run_checks(store, limit=BATCH):
    """Read up to `limit` active watches and write what each shows. The pages are fetched with no transaction open;
    a failing page is that watch's `error`, never an exception out of here. Returns {read, changed, errors}."""
    with store.read() as c:
        watches = due(c, limit)
    out = {"read": 0, "changed": 0, "errors": 0}
    for watch in watches:
        result = read(watch["url"])
        try:
            with store.transaction() as c:
                if record(c, watch, result):
                    out["changed"] += 1
        except Exception as exc:
            log.warning("Could not record a page check (%s)", type(exc).__name__)
            continue
        out["read"] += 1
        out["errors"] += result["status"] != "ok"
    return out


def words(found):
    if not found["read"]:
        return "No pages watched"
    parts = [f"{found['read']} page{'s' if found['read'] != 1 else ''} read"]
    if found["changed"]:
        parts.append(f"{found['changed']} changed")
    if found["errors"]:
        parts.append(f"{found['errors']} could not be read")
    return ", ".join(parts)


def start(store):
    """Check now on a daemon thread, unless a check is already running. True when one was started."""
    if not _RUNNING.acquire(blocking=False):
        return False

    def work():
        from . import system_routines as SR
        run = SR.Run("market_watch")
        try:
            found = run_checks(store)
            run.did(words(found), quiet=not (found["changed"] or found["errors"]))
        except Exception as exc:
            run.error = SR.failure_words(exc)
            log.warning("Page check failed (%s)", type(exc).__name__)
        finally:
            _RUNNING.release()
            SR.write(store, run)
    spawn(work)
    return True


def claim(c, day):
    """Take today's check: True once per UTC day, however many ticks or servers ask."""
    found = c.execute("SELECT value FROM registry_metadata WHERE key=?", (DAY_KEY,)).fetchone()
    if found and (json.loads(found[0]) or {}).get("day") == day:
        return False
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (DAY_KEY, json.dumps({"day": day, "at": H.now()})))
    return True


def tick(store, at=None):
    """From the scheduler's tick: once a day, check the watched pages. Off in rehearsal and test servers; with
    nothing watched it does nothing but remember the day."""
    settings = store.settings
    if settings.rehearsal or settings.test_identities:
        return False
    day = (at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    # The tick comes every few seconds: once this process has seen the day claimed it never asks again.
    if store.__dict__.get("market_watch_day") == day:
        return False
    with store.transaction() as c:
        claimed = claim(c, day)
        watching = claimed and c.execute("SELECT 1 FROM market_watches WHERE active=1 LIMIT 1").fetchone()
    store.__dict__["market_watch_day"] = day
    return bool(watching) and start(store)


# ----------------------------------------------------------------------------- HTTP
class WatchAdd(M.Contract):
    entity_id: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="other", max_length=20)


def install(app, store, auth, mutate):
    @app.get("/api/v2/market/watches")
    def watches(request: Request, entity_id: str | None = Query(default=None, max_length=200)):
        """The watched pages, newest last, without their kept text. Market readers."""
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"watches": listing(c, entity_id)}

    @app.post("/api/v2/market/watches")
    def watch_add(request: Request, body: WatchAdd):
        who = request.state.identity

        def work(c):
            MK.require_writer(who)
            return {"watch": add(c, who.actor, body.entity_id, body.url, body.kind)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/watches/check")
    def watch_check(request: Request, body: M.Empty):
        """Read the watched pages now (the longest unchecked first, at most 50) on a background thread."""
        who = request.state.identity
        mutate(request, body, lambda c: MK.require_writer(who) or {"started": True})
        start(store)
        return JSONResponse({"started": True}, status_code=202)

    @app.post("/api/v2/market/watches/{watch_id}/stop")
    def watch_stop(request: Request, watch_id: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            MK.require_writer(who)
            return {"watch": stop(c, who.actor, watch_id)}

        return mutate(request, body, work)
