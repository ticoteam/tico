"""Page monitors: the market graph's eyes on a competitor's own pages (docs/market-watch.md).

A watch is one public page of one market entity (its pricing, changelog, careers page...). Once a day the server
reads each active watch through `clients/doc_fetch.py`, the same guard `tico doc fetch` uses (public addresses only,
every redirect checked, 5 MB and 20 seconds), keeps the page's normalized text, and only when it changed by a real
amount stores a piece of evidence, files a `market_insights` row for the Librarian to curate and writes a
market history row on the entity, so a bet that cites the entity trips the fit check (backend/strategy.py). No model
is called; the Librarian reads the insight. The server does this, not a bot, because a bot's watcher has no Tico
token to read the list.

The fetch runs on a daemon thread outside any database transaction; each result is written in its own short one.
The first read of a page is its baseline and files nothing. Rows are never deleted: stopping a watch ends it.

With the Tico AI Gateway connected, Tico HQ reads the watches instead (its Signals: pages daily, release feeds every six
hours, the baselines kept there). Each active watch is registered there once (`signal_watch_id` on the row), a stopped one
is deleted there, and each check files the changes HQ found since a stored cursor, through the same path as a local read.
A watch HQ does not hold yet is read here as before. Release feeds are read only through the Gateway.
"""

import contextlib
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
from clients import gateway as GW
from . import hubdb as H
from . import market as MK
from . import models as M
from .store import Problem

log = logging.getLogger("tico.market_watch")


@contextlib.contextmanager
def _guard(what, on_error=None):
    """A failure inside the block is logged as "Could not <what>" and passed to on_error; the caller keeps going."""
    try:
        yield
    except Exception as exc:  # one page's failure must not stop the others, the cursor, or the request
        log.warning("Could not %s (%s)", what, type(exc).__name__)
        if on_error is not None:
            on_error(exc)


KINDS = ("pricing", "careers", "changelog", "docs", "home", "other", "release")
RELEASE = "release"  # a product's releases: `github:owner/repo` or a feed address, read only through the Gateway
CHANGES_PER_CALL = 200  # the most changes HQ answers at once (Signals' limit)
CURSOR_KEY = "market.watch.signals"  # registry_metadata: the Gateway's cursor over the changes filed so far
MAX_ACTIVE = 200  # active watches
BATCH = 50  # pages read per check, the longest-unchecked first
TEXT_MAX = 50_000  # of a page's normalized text kept for the next comparison
MIN_CHANGE = 40  # characters added or removed before a change counts
EXCERPT_MAX = 1_200  # of the diff kept as the evidence's quote
LINES_MAX = 3_000  # lines compared; a longer page is cut before the diff
STATUSES = ("ok", "error", "blocked")
DAY_KEY = "market.watch.checked"
ACTOR = H.KEEPER  # who writes what a check finds
GITHUB = re.compile(r"github:([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})")

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS entity_watches (id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, url TEXT NOT NULL, "
    "kind TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_by TEXT, created_at TEXT, checked_at TEXT, changed_at TEXT, "
    "last_hash TEXT, last_text TEXT, last_status TEXT, error TEXT, signal_watch_id TEXT)",
    "CREATE INDEX IF NOT EXISTS entity_watches_active ON entity_watches(active, checked_at)",
    "CREATE INDEX IF NOT EXISTS entity_watches_entity ON entity_watches(entity_id)",
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
    r"|(?:©|\(c\)|copyright\b).*)$",
    re.I,
)


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
                piece = piece[: max(0, left - 2)].rstrip() + "…"
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
        log.warning("Could not read the watched page on %s (%s: %s)", host_of(url), exc.code, exc.message)
        return {"status": "blocked" if blocked else "error", "error": f"{exc.code}: {exc.message}"[:300]}
    except Exception as exc:  # a page can be anything: the watch says so and the rest still run
        log.warning("Could not read the watched page on %s (%s)", host_of(url), F.failure_words(exc))
        return {"status": "error", "error": F.failure_words(exc)[:300]}


# ----------------------------------------------------------------------------- rows
def row_of(row, text=False):
    row = dict(row)
    row["active"] = bool(row["active"])
    if not text:
        row.pop("last_text", None)
    return row


def get(c, watch_id):
    found = c.execute("SELECT * FROM entity_watches WHERE id=?", (watch_id,)).fetchone()
    return dict(found) if found else None


def listing(c, entity_id=None):
    sql, args = "SELECT * FROM entity_watches", ()
    if entity_id:
        sql, args = sql + " WHERE entity_id=?", (entity_id,)
    return [row_of(r) for r in c.execute(sql + " ORDER BY active DESC, created_at, id", args)]


def host_of(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def read_address(url):
    """The address a watch's evidence cites: the page itself, or a GitHub repository's releases page."""
    if url.startswith("github:"):
        return "https://github.com/" + url[len("github:") :] + "/releases"
    return url


def add(c, actor, entity_id, url, kind):
    """A new watch. The caller has checked the writer. The address passes the same guard the doc fetcher applies
    before any DNS lookup (a name that resolves to an internal address is refused by the fetch itself, every time)."""
    from . import strategy_setup

    if kind not in KINDS:
        raise Problem("validation", f"a watched page is {'|'.join(KINDS)}, not {kind}", 422)
    found = GITHUB.fullmatch(str(url or "").strip()) if kind == RELEASE else None
    if found:
        repo = found.group(2)
        if repo in (".", "..") or repo.lower().endswith(".atom"):
            raise Problem("validation", "give the repository as github:owner/repo", 422, extra={"reason": "url"})
        url = f"github:{found.group(1).lower()}/{repo.lower()}"
    else:
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
    if c.execute("SELECT 1 FROM entity_watches WHERE active=1 AND url=?", (url,)).fetchone():
        raise Problem("exists", "That page is already watched", 409)
    if c.execute("SELECT count(*) FROM entity_watches WHERE active=1").fetchone()[0] >= MAX_ACTIVE:
        raise Problem("limit", f"At most {MAX_ACTIVE} pages are watched at once; stop one first", 409)
    row = {
        "id": H.new_id(),
        "entity_id": entity_id,
        "url": url,
        "kind": kind,
        "created_by": actor,
        "created_at": H.now(),
    }
    c.execute(
        "INSERT INTO entity_watches (id, entity_id, url, kind, active, created_by, created_at) "
        "VALUES (:id, :entity_id, :url, :kind, 1, :created_by, :created_at)",
        row,
    )
    H.event(c, actor, "market.watch.add", row["id"], {"entity": entity_id, "kind": kind})
    return row_of(get(c, row["id"]))


def stop(c, actor, watch_id):
    row = get(c, watch_id)
    if not row:
        raise Problem("not_found", f"There is no watch {watch_id}", 404)
    if row["active"]:
        c.execute("UPDATE entity_watches SET active=0 WHERE id=?", (watch_id,))
        H.event(c, actor, "market.watch.stop", watch_id, {"entity": row["entity_id"]})
    return row_of(get(c, watch_id))


# ----------------------------------------------------------------------------- checking
def due(c, limit=BATCH, skip_mirrored=False):
    """The active watches to read here now, the longest unchecked first (never checked first of all). A release is read
    only by the Gateway; with the Gateway, a watch it holds (`skip_mirrored`) is read there."""
    held = " AND signal_watch_id IS NULL" if skip_mirrored else ""
    return [
        dict(r)
        for r in c.execute(
            "SELECT id, entity_id, url, kind FROM entity_watches WHERE active=1 AND kind<>? "
            f"{held} ORDER BY checked_at IS NOT NULL, checked_at, id LIMIT ?",
            (RELEASE, limit),
        )
    ]


def record(c, watch, result):
    """One page's result, in the caller's short transaction. Returns True when it filed an insight."""
    row = get(c, watch["id"])
    if not row or not row["active"]:
        return False  # stopped while it was being read
    now = H.now()
    if result["status"] != "ok":
        c.execute(
            "UPDATE entity_watches SET checked_at=?, last_status=?, error=? WHERE id=?",
            (now, result["status"], result.get("error") or "", row["id"]),
        )
        return False
    text = result["text"]
    fresh = digest(text)
    if row["last_hash"] is None:  # the baseline
        c.execute(
            "UPDATE entity_watches SET checked_at=?, last_status='ok', error=NULL, last_hash=?, last_text=? WHERE id=?",
            (now, fresh, text, row["id"]),
        )
        return False
    c.execute("UPDATE entity_watches SET checked_at=?, last_status='ok', error=NULL WHERE id=?", (now, row["id"]))
    if row["last_hash"] == fresh:
        return False  # nothing new
    removed, added = changes(row["last_text"] or "", text)
    if size_of(removed, added) < MIN_CHANGE:
        return False  # too small to report; the baseline stays, so slow edits add up
    c.execute(
        "UPDATE entity_watches SET changed_at=?, last_hash=?, last_text=? WHERE id=?", (now, fresh, text, row["id"])
    )
    file_change(c, row, removed, added, fresh, now)
    return True


def file_change(c, watch, removed, added, fresh, when, address=None):
    """Files one change of a watch as its evidence, its market insight and an event on the entity. `fresh` is the
    change's digest, `when` the time it was found, `address` the page it cites (the watch's own by default)."""
    entity = MK.entity(c, watch["entity_id"])
    name = (entity or {}).get("name") or watch["entity_id"]
    quote = excerpt(removed, added)
    if watch["kind"] == RELEASE:
        our_read, claim = f"{name} published a new release.", f"Release: {name}"
    else:
        our_read, claim = f"The {watch['kind']} page of {name} changed.", f"Page changed: {name} {watch['kind']}"
    source = address or read_address(watch["url"])
    evidence, _ = MK.create_evidence(
        c,
        ACTOR,
        source_url=source,
        source_kind="page-change",
        captured_at=when,
        quote=quote,
        our_read=our_read,
    )
    insight = MK.report(
        c,
        ACTOR,
        kind="property-change",
        about=name,
        claim=claim,
        source_url=source,
        quote=quote,
        source_ref=f"watch:{watch['id']}:{fresh[:12]}",
    )
    # Recorded for the weekly delta, but no fit check: a diff is not yet a market fact. A bet trips when the Librarian
    # applies the insight to the entity (market.apply_insight), never on the raw change.
    MK._event(
        c,
        "entity",
        watch["entity_id"],
        ACTOR,
        f"page.{watch['kind']}",
        None,
        f"changed: {host_of(watch['url']) or watch['url']}",
        insight["id"],
        evidence["id"],
        trip=False,
    )
    return insight


def _connected(store, c):
    from . import gateway_connection

    return gateway_connection.connected(c, store.settings)


def connection(store):
    """(key, base URL) of the Tico AI Gateway when this install has one and may ask it, else None. A rehearsal never asks."""
    from . import gateway_connection
    from .credentials import Vault

    if store.settings.rehearsal:
        return None
    with store.read() as c:
        key, base, _ = gateway_connection.target(c, Vault(store), store.settings)
    return (key, base) if key else None


def _cursor(c):
    found = c.execute("SELECT value FROM registry_metadata WHERE key=?", (CURSOR_KEY,)).fetchone()
    try:
        value = json.loads(found[0]) if found else {}
    except ValueError:
        value = {}
    cursor = value.get("cursor") if isinstance(value, dict) else None
    return cursor if isinstance(cursor, str) and re.fullmatch(r"v1\.[0-9]{1,18}", cursor) else ""


def _set_cursor(c, cursor):
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (CURSOR_KEY, json.dumps({"cursor": cursor, "at": H.now()})),
    )


def file_signal(c, change):
    """One change HQ found on a watch of this install, filed the way a local read's change is. 1 when it was filed."""
    row = c.execute(
        "SELECT * FROM entity_watches WHERE signal_watch_id=? AND active=1", (str(change.get("watch_id") or ""),)
    ).fetchone()
    if not row:
        return 0  # a watch stopped here since: HQ's change is not news
    watch = dict(row)
    found = str(change.get("found_at") or H.now())
    if watch["kind"] == RELEASE:
        removed, added = [], [str(change.get("title") or "")] + str(change.get("added") or "").splitlines()
    else:
        removed, added = str(change.get("removed") or "").splitlines(), str(change.get("added") or "").splitlines()
    c.execute("UPDATE entity_watches SET changed_at=? WHERE id=?", (found, watch["id"]))
    file_change(
        c,
        watch,
        removed,
        added,
        digest(str(change.get("id") or "")),
        found,
        address=str(change.get("url") or "") or None,
    )
    return 1


def _file_one(c, change):
    """file_signal inside a savepoint: a change that cannot be filed is logged and skipped, so the cursor still moves."""
    c.execute("SAVEPOINT signal_change")
    filed = 0
    with _guard("file a Gateway page change", on_error=lambda _: c.execute("ROLLBACK TO signal_change")):
        filed = file_signal(c, change)
    c.execute("RELEASE signal_change")
    return filed


def mirror(store, key, base, pull=True):
    """Mirror the watches at the Gateway's Signals. Each active watch it does not hold is registered once and its id kept
    on the row; each stopped watch is ended there (a 404 is ended already); each held watch takes HQ's status. With `pull`,
    the changes found since the cursor are filed, a page of them in one transaction with the cursor. Returns {read,
    changed, errors} for the watches HQ holds, or None when HQ's list cannot be had (the caller then reads here)."""
    status, listed, _ = GW.signals_watches(base, key)
    if status != 200:
        return None
    held = {w["id"]: w for w in listed.get("watches") or [] if isinstance(w, dict) and w.get("id")}
    with store.read() as c:
        active = [
            dict(r) for r in c.execute("SELECT id, kind, url, signal_watch_id FROM entity_watches WHERE active=1")
        ]
        ended = [
            dict(r)
            for r in c.execute(
                "SELECT id, signal_watch_id FROM entity_watches WHERE active=0 AND signal_watch_id IS NOT NULL"
            )
        ]
    pairs = []
    for row in active:
        ident = row["signal_watch_id"]
        if ident not in held:
            made_status, made, _ = GW.signals_watch(
                base, key, RELEASE if row["kind"] == RELEASE else "page", row["url"]
            )
            ident = made.get("id") if made_status in (200, 201) and isinstance(made.get("id"), str) else None
            if ident is None:
                continue  # HQ refused it: this check reads it here
            held[ident] = made
            with store.transaction() as c:
                c.execute("UPDATE entity_watches SET signal_watch_id=? WHERE id=? AND active=1", (ident, row["id"]))
        pairs.append((row["id"], ident))
    for row in ended:
        gone, _, _ = GW.signals_end(base, key, row["signal_watch_id"])
        if gone in (204, 404):
            with store.transaction() as c:
                c.execute("UPDATE entity_watches SET signal_watch_id=NULL WHERE id=?", (row["id"],))
    out = {"read": 0, "changed": 0, "errors": 0}
    with store.transaction() as c:
        for row_id, ident in pairs:
            theirs = held.get(ident) or {}
            if theirs.get("status") in (None, "pending"):
                continue  # HQ has not read it yet
            ok = theirs["status"] == "ok"
            c.execute(
                "UPDATE entity_watches SET checked_at=?, last_status=?, error=? WHERE id=?",
                (
                    theirs.get("checked_at") or H.now(),
                    theirs["status"],
                    None if ok else f"Read by the Gateway: {theirs['status']}",
                    row_id,
                ),
            )
            out["read"] += 1
            out["errors"] += not ok
    if not pull:
        return out
    with store.read() as c:
        cursor = _cursor(c)
    while True:
        status, page, _ = GW.signals_changes(base, key, cursor, CHANGES_PER_CALL)
        if status != 200:
            break
        found = [x for x in page.get("changes") or [] if isinstance(x, dict)]
        nxt = page.get("next") if isinstance(page.get("next"), str) else cursor
        with store.transaction() as c:
            out["changed"] += sum(_file_one(c, change) for change in found)
            _set_cursor(c, nxt)
        if len(found) < CHANGES_PER_CALL or nxt == cursor:
            break
        cursor = nxt
    return out


def sync_soon(store):
    """Mirror the watches now, on its own thread, when the Gateway is connected: a new watch is registered at HQ and a
    stopped one ended there without waiting for the daily check. Returns True when a sync was started."""
    link = connection(store)
    if link is None:
        return False

    def work():
        with _guard("mirror the page watches"):
            mirror(store, *link, pull=False)

    spawn(work)
    return True


def run_checks(store, limit=BATCH):
    """Read up to `limit` active watches and write what each shows. With the Gateway connected, HQ's Signals are mirrored
    and their changes filed first, and only the watches HQ does not hold are read here. The pages are fetched with no
    transaction open; a failing page is that watch's `error`, never an exception out of here. Returns {read, changed,
    errors}."""
    link = connection(store)
    out = {"read": 0, "changed": 0, "errors": 0}
    if link is not None:
        mirrored = None
        with _guard("read the Gateway's page changes"):
            mirrored = mirror(store, *link)
        for key in ("read", "changed", "errors"):
            out[key] += (mirrored or {}).get(key, 0)
    with store.read() as c:
        watches = due(c, limit, skip_mirrored=link is not None)
    for watch in watches:
        result = read(watch["url"])
        with _guard("record a page check"):
            with store.transaction() as c:
                if record(c, watch, result):
                    out["changed"] += 1
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
            with _guard("run the page check", on_error=lambda exc: setattr(run, "error", SR.failure_words(exc))):
                found = run_checks(store)
                run.did(words(found), quiet=not (found["changed"] or found["errors"]))
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
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (DAY_KEY, json.dumps({"day": day, "at": H.now()})),
    )
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
        watching = claimed and c.execute("SELECT 1 FROM entity_watches WHERE active=1 LIMIT 1").fetchone()
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
            if body.kind == RELEASE and not _connected(store, c):
                raise Problem(
                    "gateway_required",
                    "A release is watched through the Tico AI Gateway, which is not connected: the owner connects it in Settings > Computers",
                    409,
                )
            return {"watch": add(c, who.actor, body.entity_id, body.url, body.kind)}

        made = mutate(request, body, work)
        sync_soon(store)  # HQ registers the page now, not at the next daily check
        return made

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

        stopped = mutate(request, body, work)
        sync_soon(store)  # HQ ends the watch now, not at the next daily check
        return stopped
