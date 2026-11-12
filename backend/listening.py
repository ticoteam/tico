"""Listening: sources, rules and rate limits on an automation.

A listening is an automation whose trigger is `listening` (backend/automation_flow.py checks its shape). Its sources
say where posts come from, its Keep and Drop rules which ones matter, and its limits how many are sent on and when.
Two tables (backend/migrations/20261111190425_listening_feed.py):

    listening_sources   one row per source in the trigger, in step with the flow on every save (`sync_sources`)
    listening_items     what the sources saved, and what became of each

An item is `new` when saved, then `duplicate` (a copy of one already here), `kept` or `dropped` (the rules, asked of
the decision model), `sent` (it went out in a run), `failed` (the model could not score it in three tries) or
`expired` (not sent within a week, or its source was removed).

Posts arrive three ways, all through `save`: a bot that read a Search source, the server reading a Page source
(`read_pages`, the market watch's reader), and a Push address anyone holding its token may post to.

`run_pass` is the scheduler's pass, once per `automations.tick`, outside that tick's transaction: the model calls and
the copy comparison run between short transactions, never inside one.
"""

import hashlib
import json
import logging
import re
import secrets
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from pydantic import Field

from clients import judge as J

from . import automation_flow as F
from . import decide_groups as DG
from . import hubdb as H
from .models import Contract
from .store import Problem

log = logging.getLogger(__name__)

READ_STATUSES = ("ok", "blocked", "rate_limited", "error")
ITEM_STATUSES = ("new", "duplicate", "kept", "dropped", "sent", "failed", "expired")
MAX_ITEMS = 200  # items in one save
TEXT_MAX = 20_000  # of a post's text kept
SENT_TEXT = 4_000  # of a post's text carried in a run
TITLE_MAX = 140
MAX_SCORE = 50  # items scored in one pass
CLAIM_MINUTES = 10  # a claim older than this is free again: its pass was cut off
MAX_TRIES = 3
MAX_ERRORS = 3  # failed posts in one pass before the rest wait for the next
GROUP = DG.MOST  # posts asked about in one call (backend/decide_groups.py); 1 asks one post per call
YES = 0.75  # a rule holds at this score
COPY_DAYS = 30  # the copy check's window
COPY_COMPARISONS = 2_000
COPY_ALIKE = 0.95
COPY_TOKENS = 8  # shorter texts are compared by key only: a few shared words are not a copy
COPY_KEY = 20  # a text key shorter than this says too little to call two posts the same
COPY_TEXT = 2_000  # of a post's text compared
EXPIRE_DAYS = 7
KEEP_SENT_DAYS = 90
KEEP_DAYS = 30
SEARCH_DUE_HOURS = 24
PAGE_DUE_HOURS = 24
PAGES_PER_PASS = 10
PASS_SECONDS = 20  # of page reads and model calls in one pass, across every listening: the tick waits on the pass
OVERDUE_DAYS = 2  # a source not read for this long shows hollow
STALLED_MINUTES = 30  # a post waiting unscored this long shows the listening hollow
PUSH_PER_MINUTE = 60
PUSH_PATH = "/api/v2/listening/in/"
RECENT_MAX = 25
RECENT_TEXT = 240
LIST_MAX = 500
DEFAULT_AT = "08:00"

# The Page source's reader: `backend.market_watch.read`, or a test's own.
READER = None
CLOCK = time.monotonic  # the pass's clock, replaced in tests
_PUSHES = {}  # source id -> the times of its last calls, for PUSH_PER_MINUTE
_PUSH_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- small helpers
LINK = re.compile(r"(?:https?://|www\.)\S+", re.I)  # the copy check ignores links: a tracking code is not a difference


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def text_key(content):
    """The words of a post, without the community tag, links or punctuation: equal for a cross-post. A long post's key ends
    in a digest of all of it, so two that only start alike are not the same."""
    text = LINK.sub(" ", re.sub(r"^\[[^\]]{1,80}\]\s*", "", str(content or "")))
    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    return key if len(key) <= 400 else key[:336] + hashlib.sha256(key.encode()).hexdigest()


def tokens(text):
    return Counter(re.findall(r"[a-z0-9]+", LINK.sub(" ", str(text or "")).lower()))


def alike(a, b):
    """How much of two posts' words are shared, 0 to 1, counting a repeated word each time."""
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 0.0


def _iso(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _has_tables(c):
    return H._has_table(c, "listening_sources") and H._has_table(c, "automations")


def title_of(item):
    """The post's own title, or the start of its text. No model writes one."""
    own = normalize(item["title"])
    if own:
        return own[:TITLE_MAX]
    text = normalize(item["text"])
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1] + "…"


def site_label(source):
    if not source:
        return ""
    if source["kind"] == "page":
        return "Page"
    site = str(source["site"] or "")
    return site[:1].upper() + site[1:]


# ----------------------------------------------------------------------------- the trigger
def zone_of(trigger):
    from . import automation_due as D

    return D.zone_of(trigger)


def smallest_per(trigger):
    pers = {limit["per"] for limit in trigger.get("limits") or []}
    return next((per for per in F.PERS if per in pers), "day")


def send_cron(trigger):
    """The send times as a schedule: the shortest period in the limits sets them."""
    hour, minute = (trigger.get("at") or DEFAULT_AT).split(":")
    clock = f"{int(minute)} {int(hour)}"
    return {"hour": "0 * * * *", "day": f"{clock} * * *", "week": f"{clock} * * 1", "month": f"{clock} 1 * *"}[
        smallest_per(trigger)
    ]


def next_send(trigger, at):
    from .scheduler import next_due

    return next_due(send_cron(trigger), at, zone_of(trigger))


def window_start(per, at, zone):
    """Where the calendar window holding `at` starts, in the listening's zone: the clock hour, the day, Monday, the
    1st."""
    local = at.astimezone(ZoneInfo(zone))
    if per == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if per == "week":
        start -= timedelta(days=start.weekday())
    elif per == "month":
        start = start.replace(day=1)
    return start


def allowance(c, aid, trigger, at):
    """How many items may go now: the least left over all the limits, each counted in its own calendar window."""
    zone = zone_of(trigger)
    left = []
    for limit in trigger.get("limits") or []:
        since = _iso(window_start(limit["per"], at, zone))
        sent = c.execute(
            "SELECT count(*) FROM listening_items WHERE automation_id=? AND status='sent' AND sent_at>=?", (aid, since)
        ).fetchone()[0]
        left.append(limit["count"] - sent)
    return max(0, min(left)) if left else 0


# ----------------------------------------------------------------------------- sources
def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def sync_sources(c, aid, flow, now=None):
    """The source rows follow the flow (called on every save, and with no flow when the automation is deleted). A
    source taken out keeps its row and what it sent; its other items expire. One put back under the same id is the
    same source again."""
    if not H._has_table(c, "listening_sources"):
        return
    now = now or H.now()
    trigger = (flow or {}).get("trigger") or {}
    wanted = {s["id"]: s for s in trigger.get("sources") or []} if trigger.get("type") == "listening" else {}
    have = {r["key"]: dict(r) for r in c.execute("SELECT * FROM listening_sources WHERE automation_id=?", (aid,))}
    for key, source in wanted.items():
        kind, site, target = source["kind"], source.get("site") or "", source.get("target") or ""
        row = have.get(key)
        if not row:
            c.execute(
                "INSERT INTO listening_sources(id,automation_id,key,kind,site,target,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'never',?,?)",
                ("lsrc_" + H.new_id(), aid, key, kind, site, target, now, now),
            )
        elif (row["kind"], row["target"]) != (kind, target):
            # It reads something else now: what the old read said no longer holds, and an address is a push source's.
            c.execute(
                "UPDATE listening_sources SET kind=?,site=?,target=?,status='never',last_read_at=NULL,last_ok_at=NULL,"
                "last_error=NULL,snapshot=NULL,token_hash=CASE WHEN ?='push' THEN token_hash ELSE NULL END,"
                "deleted_at=NULL,updated_at=? WHERE id=?",
                (kind, site, target, kind, now, row["id"]),
            )
        elif row["site"] != site or row["deleted_at"]:
            c.execute(
                "UPDATE listening_sources SET site=?,deleted_at=NULL,updated_at=? WHERE id=?", (site, now, row["id"])
            )
    for key, row in have.items():
        if key in wanted or row["deleted_at"]:
            continue
        c.execute(
            "UPDATE listening_sources SET deleted_at=?,token_hash=NULL,updated_at=? WHERE id=?", (now, now, row["id"])
        )
        c.execute(
            "UPDATE listening_items SET status='expired',claimed_at=NULL,updated_at=? "
            "WHERE source_id=? AND status IN ('new','kept','failed')",
            (now, row["id"]),
        )


def _base(settings):
    return (getattr(settings, "runner_url", "") or getattr(settings, "public_url", "") or "").rstrip("/")


def _new_address(c, settings, source_id, now=None):
    token = secrets.token_urlsafe(24)
    c.execute(
        "UPDATE listening_sources SET token_hash=?,updated_at=? WHERE id=?", (_digest(token), now or H.now(), source_id)
    )
    return _base(settings) + PUSH_PATH + token


def issue_addresses(c, settings, aid):
    """An address for each push source that has none yet, shown this once: only its token's hash is kept."""
    if not H._has_table(c, "listening_sources"):
        return []
    rows = c.execute(
        "SELECT id, key FROM listening_sources WHERE automation_id=? AND kind='push' AND token_hash IS NULL "
        "AND deleted_at IS NULL ORDER BY created_at, key",
        (aid,),
    ).fetchall()
    return [{"source": r["key"], "source_id": r["id"], "address": _new_address(c, settings, r["id"])} for r in rows]


def source_dot(row, now):
    """Filled: its last read went well and is recent. A push source is never overdue: filled once something came."""
    if row["kind"] == "push":
        return "filled" if row["last_ok_at"] else "hollow"
    fresh = row["last_read_at"] and row["last_read_at"] >= H.shift(now, days=-OVERDUE_DAYS)
    return "filled" if row["status"] == "ok" and fresh else "hollow"


def source_view(row, now):
    return {
        "id": row["id"],
        "automation_id": row["automation_id"],
        "key": row["key"],
        "kind": row["kind"],
        "site": row["site"],
        "target": row["target"],
        "status": row["status"],
        "dot": source_dot(row, now),
        "last_read_at": row["last_read_at"],
        "last_ok_at": row["last_ok_at"],
        "last_error": row["last_error"],
        **({"has_address": bool(row["token_hash"])} if row["kind"] == "push" else {}),
    }


def sources_of(c, aid, now=None):
    now = now or H.now()
    rows = c.execute(
        "SELECT * FROM listening_sources WHERE automation_id=? AND deleted_at IS NULL ORDER BY created_at, key", (aid,)
    ).fetchall()
    return [source_view(r, now) for r in rows]


def summary(c, row, flow, now=None):
    """What the list and the editor show of a listening beside its flow: when it last sent and sends next, how many
    kept items wait, each source's state, and one dot. Hollow: no source yet, a source that is not being read, or
    posts waiting unscored (decisions are off, or the spend cap is reached)."""
    if not H._has_table(c, "listening_sources"):
        return None
    now = now or H.now()
    aid = row["id"]
    sources = sources_of(c, aid, now)
    counts = {
        r[0]: r[1]
        for r in c.execute(
            "SELECT status, count(*) FROM listening_items WHERE automation_id=? AND status IN ('new','kept') "
            "GROUP BY status",
            (aid,),
        ).fetchall()
    }
    waiting_long = c.execute(
        "SELECT 1 FROM listening_items WHERE automation_id=? AND status='new' AND created_at<? LIMIT 1",
        (aid, H.shift(now, minutes=-STALLED_MINUTES)),
    ).fetchone()
    last = c.execute(
        "SELECT max(sent_at) FROM listening_items WHERE automation_id=? AND status='sent'", (aid,)
    ).fetchone()[0]
    filled = bool(sources) and all(s["dot"] == "filled" for s in sources) and not waiting_long
    return {
        "dot": "filled" if filled else "hollow",
        "last_sent_at": last,
        "next_send_at": row["next_due_at"] if flow.get("enabled") else None,
        "waiting": counts.get("kept", 0),
        "unscored": counts.get("new", 0),
        "sources": sources,
    }


def worst(c):
    """The worst source state across the listenings that are on, for the Tools line: `blocked`, `error`,
    `rate_limited`, `overdue`, `ok`, or None when nothing listens."""
    if not _has_tables(c):
        return None
    now = H.now()
    rows = c.execute(
        "SELECT s.* FROM listening_sources s JOIN automations a ON a.id=s.automation_id "
        "WHERE s.deleted_at IS NULL AND a.deleted_at IS NULL AND a.enabled=1 AND a.trigger_type='listening'"
    ).fetchall()
    if not rows:
        return None
    states = set()
    for row in rows:
        if row["status"] in ("blocked", "error", "rate_limited"):
            states.add(row["status"])
        elif source_dot(row, now) == "hollow" and row["kind"] != "push":
            states.add("overdue")
    return next((s for s in ("blocked", "error", "rate_limited", "overdue") if s in states), "ok")


def trouble(c):
    """Each source of a listening that is on whose last read did not go well, for the health check."""
    if not _has_tables(c):
        return []
    rows = c.execute(
        "SELECT s.*, a.name FROM listening_sources s JOIN automations a ON a.id=s.automation_id "
        "WHERE s.deleted_at IS NULL AND a.deleted_at IS NULL AND a.enabled=1 AND a.trigger_type='listening' "
        "AND s.status IN ('blocked','error','rate_limited') ORDER BY a.name, s.created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def stalled(c):
    """The names of the listenings that are on with posts waiting unscored: decisions are off, or a cap is reached."""
    if not _has_tables(c):
        return []
    rows = c.execute(
        "SELECT DISTINCT a.name FROM listening_items i JOIN automations a ON a.id=i.automation_id "
        "WHERE i.status='new' AND i.created_at<? AND a.deleted_at IS NULL AND a.enabled=1 ORDER BY a.name",
        (H.shift(H.now(), minutes=-STALLED_MINUTES),),
    ).fetchall()
    return [r[0] for r in rows]


# ----------------------------------------------------------------------------- save
def _source(c, source_id):
    row = c.execute(
        "SELECT s.*, a.enabled, a.deleted_at AS automation_deleted_at, a.flow FROM listening_sources s "
        "JOIN automations a ON a.id=s.automation_id WHERE s.id=?",
        (source_id,),
    ).fetchone()
    if not row or row["deleted_at"] or row["automation_deleted_at"]:
        raise Problem("not_found", "No such source", 404)
    return dict(row)


def save(c, source, status, items, now=None, error=""):
    """One read of one source: its state and times, and each post not seen before as a `new` item. A post already
    here (the same `native_id` in this listening) is left alone, so saying it twice adds nothing. Refused while the
    listening is off."""
    if status not in READ_STATUSES:
        raise Problem("validation", f"status is one of {', '.join(READ_STATUSES)}", 422)
    if not source["enabled"]:
        raise Problem("listening_off", "This listening is off", 409)
    now = now or H.now()
    c.execute(
        "UPDATE listening_sources SET status=?,last_read_at=?,last_ok_at=CASE WHEN ?='ok' THEN ? ELSE last_ok_at END,"
        "last_error=?,updated_at=? WHERE id=?",
        (status, now, status, now, None if status == "ok" else (normalize(error)[:300] or None), now, source["id"]),
    )
    saved = 0
    for item in items:
        text = str(item.get("text") or "")[:TEXT_MAX]
        title = normalize(item.get("title"))[:300]
        saved += c.execute(
            "INSERT OR IGNORE INTO listening_items(id,automation_id,source_id,native_id,url,author,title,text,"
            "posted_at,text_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'new',?,?)",
            (
                "lsn_" + H.new_id(),
                source["automation_id"],
                source["id"],
                str(item["native_id"]),
                str(item.get("url") or ""),
                str(item.get("author") or ""),
                title,
                text,
                item.get("posted_at") or None,
                text_key(text or title),
                now,
                now,
            ),
        ).rowcount
    fresh = c.execute("SELECT * FROM listening_sources WHERE id=?", (source["id"],)).fetchone()
    return {"source": source_view(fresh, now), "seen": len(items), "saved": saved}


def _push_allowed(source_id):
    """At most PUSH_PER_MINUTE calls a minute for one address, counted on this server."""
    at = time.monotonic()
    with _PUSH_LOCK:
        calls = _PUSHES.setdefault(source_id, deque())
        while calls and calls[0] <= at - 60:
            calls.popleft()
        if len(calls) >= PUSH_PER_MINUTE:
            return False
        calls.append(at)
        return True


# ----------------------------------------------------------------------------- the pass
def _reader(settings):
    if READER is not None:
        return READER
    # As the market watch: a rehearsal or a test server reads no page.
    if getattr(settings, "rehearsal", False) or getattr(settings, "test_identities", False):
        return None
    from . import market_watch

    return market_watch.read


def _budget(seconds=PASS_SECONDS):
    """A pass's time for page reads and model calls: a call that answers whether any is left."""
    end = CLOCK() + seconds
    return lambda: CLOCK() < end


def read_pages(store, aid, now, left=None):
    """The server reads each Page source once a day. The first read is what later ones are compared with; after it, a
    page that changed enough (the market watch's measure) saves one item: its title the first changed line, its text
    what was added and removed."""
    from . import market_watch as MW

    reader = _reader(store.settings)
    if reader is None:
        return
    with store.read() as c:
        due = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM listening_sources WHERE automation_id=? AND kind='page' AND deleted_at IS NULL "
                "AND (last_read_at IS NULL OR last_read_at<?) ORDER BY last_read_at LIMIT ?",
                (aid, H.shift(now, hours=-PAGE_DUE_HOURS), PAGES_PER_PASS),
            )
        ]
    for row in due:
        if left and not left():  # the pass's time is used up: the page is still due at the next
            return
        result = reader(row["target"])
        items, text = [], None
        if result.get("status") == "ok":
            text = MW.normalize(result.get("text") or "")[: MW.TEXT_MAX]
            if row["snapshot"] is not None:
                removed, added = MW.changes(row["snapshot"], text)
                if MW.size_of(removed, added) >= MW.MIN_CHANGE:
                    items.append(
                        {
                            "native_id": "page:" + hashlib.sha256(text.encode()).hexdigest()[:24],
                            "url": row["target"],
                            "title": normalize((added or removed)[0])[:TITLE_MAX],
                            "text": MW.excerpt(removed, added),
                            "posted_at": now,
                        }
                    )
        with store.transaction() as c:
            try:
                source = _source(c, row["id"])
                save(c, source, result.get("status") or "error", items, now, result.get("error") or "")
            except Problem:  # removed or turned off while the page was read
                continue
            if text is not None:
                c.execute("UPDATE listening_sources SET snapshot=? WHERE id=?", (text, row["id"]))


def _duplicate(c, item_id, original_id, now, claimed=None):
    """Mark one `new` item a copy of `original_id`; False when another pass got to it first. Copies never chain: a
    copy of a copy points at that one's original, and what already pointed at this item moves, with its count, to
    the original too."""
    known = c.execute("SELECT status, duplicate_of_id FROM listening_items WHERE id=?", (original_id,)).fetchone()
    if known and known["status"] == "duplicate" and known["duplicate_of_id"]:
        original_id = known["duplicate_of_id"]
    if original_id == item_id:
        return False
    done = c.execute(
        "UPDATE listening_items SET status='duplicate',duplicate_of_id=?,claimed_at=NULL,updated_at=? "
        "WHERE id=? AND status='new'" + (" AND claimed_at=?" if claimed else ""),
        (original_id, now, item_id, *((claimed,) if claimed else ())),
    ).rowcount
    if done:
        own = c.execute("SELECT copies FROM listening_items WHERE id=?", (item_id,)).fetchone()[0]
        c.execute("UPDATE listening_items SET duplicate_of_id=? WHERE duplicate_of_id=?", (original_id, item_id))
        c.execute("UPDATE listening_items SET copies=0 WHERE id=?", (item_id,))
        c.execute("UPDATE listening_items SET copies=copies+1+?,updated_at=? WHERE id=?", (own, now, original_id))
    return bool(done)


def exact_copies(store, aid, now):
    """Every `new` item whose text is, word for word, that of an earlier item of this listening is its copy. The
    earliest stays the original, so copies never chain."""
    with store.read() as c:
        rows = c.execute(
            "SELECT id, text_key, status FROM listening_items WHERE automation_id=? AND status<>'duplicate' "
            "AND created_at>=? ORDER BY rowid",
            (aid, H.shift(now, days=-COPY_DAYS)),
        ).fetchall()
    first, copies = {}, []
    for row in rows:
        key = row["text_key"]
        if len(key) < COPY_KEY:
            continue
        if key not in first:
            first[key] = row["id"]
        elif row["status"] == "new":
            copies.append((row["id"], first[key]))
    if copies:
        with store.transaction() as c:
            for item_id, original in copies:
                _duplicate(c, item_id, original, now)
    return len(copies)


def _capped(c, aid, flow, now):
    """Whether the automation's own spend cap is reached, counting what scoring cost since its last send."""
    cap = (flow.get("spend_cap") or {}).get("per_30m")
    if cap is None:
        return False
    from . import automations as A

    unsent = c.execute(
        "SELECT coalesce(sum(cost),0) FROM listening_items WHERE automation_id=? AND run_id IS NULL AND scored_at>=?",
        (aid, H.shift(now, minutes=-30)),
    ).fetchone()[0]
    return A._spent(c, aid, "") + float(unsent or 0) >= float(cap)


def questions_of(rules):
    """Each rule as a yes-or-no question for the decision model, `r<n>` in the trigger's order."""
    return {f"r{n}": {"type": "noul", "instructions": rule["text"]} for n, rule in enumerate(rules)}


def verdict(rules, answers):
    """(status, tag, score) from the model's answers. Kept: a Keep rule holds and no Drop rule does; the tag is the
    Keep rule that scored highest. With no Keep rules, everything a Drop rule does not take is kept, untagged."""

    def score_of(n):
        try:
            return float((answers.get(f"r{n}") or {}).get("noul") or 0)
        except (TypeError, ValueError):
            return 0.0

    keeps = [(score_of(n), rule["tag"]) for n, rule in enumerate(rules) if rule["do"] == "keep"]
    drops = [score_of(n) for n, rule in enumerate(rules) if rule["do"] == "drop"]
    best = max(keeps, key=lambda k: k[0], default=None)
    worst_drop = max(drops, default=0.0)
    if worst_drop >= YES:
        return "dropped", "", round(best[0] if best else 1 - worst_drop, 4)
    if best is None:
        return "kept", "", round(1 - worst_drop, 4)
    if best[0] >= YES:
        return "kept", best[1], round(best[0], 4)
    return "dropped", "", round(best[0], 4)


def _state(item, source):
    """What the decision model reads of a post."""
    return {
        "site": site_label(source),
        "url": item["url"],
        "author": item["author"],
        "posted_at": item["posted_at"] or "",
        "title": item["title"],
        "text": item["text"],
    }


def _decided(c, item_id, now, status, tag, value, cost, claimed=None):
    c.execute(
        "UPDATE listening_items SET status=?,tag=?,score=?,cost=cost+?,scored_at=?,claimed_at=NULL,updated_at=? "
        "WHERE id=? AND status='new'" + (" AND claimed_at=?" if claimed else ""),
        (status, tag, value, cost, now, now, item_id, *((claimed,) if claimed else ())),
    )


def score(store, row, flow, now, primary=None, left=None):
    """Claim up to MAX_SCORE `new` items, newest first, then for each, outside any transaction: a near copy of an
    earlier item is marked so; the rest are asked of the decision model, every rule in one call. With decisions off
    or a spend cap reached nothing is claimed and the items stay `new`. When the pass's time is used up (`left`) the
    claims still held are released, for the next pass."""
    from .judge import server_engine

    aid, rules = row["id"], flow["trigger"].get("rules") or []
    with store.transaction() as c:
        engine = None
        if rules:
            engine = None if _capped(c, aid, flow, now) else server_engine(store, c, primary)
            if engine is None:
                return {"off": True}
        claimed = [
            dict(r)
            for r in c.execute(
                "SELECT rowid AS n, * FROM listening_items WHERE automation_id=? AND status='new' "
                "AND (claimed_at IS NULL OR claimed_at<?) ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (aid, H.shift(now, minutes=-CLAIM_MINUTES), MAX_SCORE),
            )
        ]
        if not claimed:
            return {"scored": 0}
        c.execute(
            f"UPDATE listening_items SET claimed_at=? WHERE id IN ({','.join('?' * len(claimed))})",
            (now, *(r["id"] for r in claimed)),
        )
        others = [
            dict(r)
            for r in c.execute(
                "SELECT rowid AS n, id, substr(text,1,?) AS text, substr(text,-?) AS tail, length(text) AS size, title "
                "FROM listening_items WHERE automation_id=? AND status<>'duplicate' AND created_at>=? "
                "ORDER BY rowid DESC LIMIT ?",
                (COPY_TEXT, COPY_TEXT, aid, H.shift(now, days=-COPY_DAYS), COPY_COMPARISONS + MAX_SCORE),
            )
        ]
        sources = {r["id"]: dict(r) for r in c.execute("SELECT * FROM listening_sources WHERE automation_id=?", (aid,))}
    questions = questions_of(rules)
    label = "listening." + aid
    words, gone = {}, set()

    def words_of(other):
        if other["id"] not in words:
            words[other["id"]] = tokens(other["text"] or other["title"])
        return words[other["id"]]

    def copy_of(item):
        """The earlier item this one is a near copy of. Two texts are alike when their first COPY_TEXT characters
        are; a longer text also needs the whole lengths within the bar and its last COPY_TEXT characters alike, so
        two long posts that only open the same are not copies."""
        mine = tokens(item["text"][:COPY_TEXT] or item["title"])
        size, length = sum(mine.values()), len(item["text"])
        tail = None
        if size < COPY_TOKENS:
            return None
        compared = 0
        for other in others:  # newest first
            if other["n"] >= item["n"] or other["id"] in gone:
                continue
            compared += 1
            if compared > COPY_COMPARISONS:
                break
            theirs = words_of(other)
            total = sum(theirs.values())
            # Two texts whose lengths differ by more than the bar allows cannot clear it.
            if min(size, total) < COPY_ALIKE * max(size, total):
                continue
            if alike(mine, theirs) < COPY_ALIKE:
                continue
            if max(length, other["size"]) > COPY_TEXT:
                if min(length, other["size"]) < COPY_ALIKE * max(length, other["size"]):
                    continue
                tail = tail or tokens(item["text"][-COPY_TEXT:])
                if alike(tail, tokens(other["tail"])) < COPY_ALIKE:
                    continue
            return other["id"]
        return None

    def on(c):
        return bool(
            c.execute(
                "SELECT 1 FROM automations WHERE id=? AND deleted_at IS NULL AND enabled=1 "
                "AND trigger_type='listening'",
                (aid,),
            ).fetchone()
        )

    def release(items):
        with store.transaction() as c:
            for item in items:
                c.execute(
                    "UPDATE listening_items SET claimed_at=NULL WHERE id=? AND status='new' AND claimed_at=?",
                    (item["id"], now),
                )

    out = {"scored": 0, "duplicates": 0, "errors": 0}
    ordered = sorted(claimed, key=lambda r: -r["n"])  # newest first: when time runs out the old ones wait

    def ask(waiting, rest):
        """Ask about the posts `waiting` (each with its place), several to a call (backend/decide_groups.py), and write
        what came back. False when the pass stops; the claims still held, from `rest` on included, are released."""
        pairs = [(_state(item, sources.get(item["source_id"])), questions) for _, item in waiting]
        for run in DG.packs(pairs, GROUP):
            # Before each paid call: time left, the listening still on, the cap not reached by what this pass spent.
            with store.read() as c:
                go = on(c) and not _capped(c, aid, flow, now)
            if not go or (left and not left()):
                release([waiting[n][1] for n in range(run[0], len(waiting))] + ordered[rest:])
                out["cut"] = True
                return False
            assert engine is not None  # rules without an engine returned above
            calls = []
            replies = DG.ask_run(engine, [pairs[n] for n in run], label, calls, run)
            stop = False
            with store.transaction() as c:
                for call in calls:
                    H.event(c, H.KEEPER, "judge.call", label, DG.audit_row(call, label, automation=aid))
                # Turned off or deleted while the call ran: no result is written, the posts stay `new`.
                still = on(c)
                for n, reply in zip(run, replies, strict=True):
                    item = waiting[n][1]
                    if isinstance(reply, BaseException):  # one post's failure is that post's; the pass goes on
                        code = J.error_code(reply) if isinstance(reply, J.JudgeError) else type(reply).__name__
                        if code != "spend_cap":  # the month's decision cap is not the post's fault, so not a try
                            c.execute(
                                "UPDATE listening_items SET tries=tries+1,claimed_at=NULL,updated_at=?,"
                                "status=CASE WHEN tries+1>=? THEN 'failed' ELSE status END "
                                "WHERE id=? AND status='new' AND claimed_at=?",
                                (now, MAX_TRIES, item["id"], now),
                            )
                        out["errors"] += 1
                        stop = stop or code == "spend_cap" or out["errors"] >= MAX_ERRORS
                        continue
                    if still:
                        status, tag, value = verdict(rules, reply.get("answers") or {})
                        _decided(c, item["id"], now, status, tag, value, float(reply.get("cost_usd") or 0), claimed=now)
                        out["scored"] += 1
            if stop or not still:
                release([waiting[n][1] for n in range(run[-1] + 1, len(waiting))] + ordered[rest:])
                if not still:
                    release([waiting[n][1] for n in run])
                return False
        return True

    waiting = []
    for place, item in enumerate(ordered):
        original = copy_of(item)
        if original:
            with store.transaction() as c:
                if _duplicate(c, item["id"], original, now, claimed=now):
                    gone.add(item["id"])
                    out["duplicates"] += 1
            continue
        if not rules:
            with store.transaction() as c:
                if still := on(c):
                    _decided(c, item["id"], now, "kept", "", 1.0, 0.0, claimed=now)
            if not still:
                release(ordered[place:])
                break
            out["scored"] += 1
            continue
        waiting.append((place, item))
        if len(waiting) == GROUP:
            if not ask(waiting, place + 1):
                waiting = []
                break
            waiting = []
    if waiting:
        ask(waiting, len(ordered))
    return out


def expire(c, aid, now):
    """A post not sent within a week of arriving is too old to send."""
    return c.execute(
        "UPDATE listening_items SET status='expired',claimed_at=NULL,updated_at=? "
        "WHERE automation_id=? AND status IN ('new','kept') AND created_at<?",
        (now, aid, H.shift(now, days=-EXPIRE_DAYS)),
    ).rowcount


def _short_url(url):
    return re.sub(r"^https?://(www\.)?", "", str(url or "")).rstrip("/")


def _lines(entries):
    width = max((len(e["tag"]) for e in entries), default=0)
    out = []
    for n, entry in enumerate(entries, 1):
        head = f"{n}. {entry['tag'].ljust(width)}  " if width else f"{n}. "
        copies = entry["copies"]
        about = [entry["site"], f"{copies} {'copy' if copies == 1 else 'copies'}" if copies else ""]
        about.append(_short_url(entry["url"]))
        out.append(head + entry["title"])
        out.append("   " + " · ".join(part for part in about if part))
    return "\n".join(out)


def payload_of(c, row, flow, items, at, now, waiting):
    """What a send's run carries (the trigger's payload): the text, each item, the items grouped by tag in the order
    their first item ranks, and the counts since the send before."""
    aid, zone = row["id"], zone_of(flow["trigger"])
    sources = {r["id"]: dict(r) for r in c.execute("SELECT * FROM listening_sources WHERE automation_id=?", (aid,))}
    entries = []
    for item in items:
        source = sources.get(item["source_id"])
        entries.append(
            {
                "id": item["id"],
                "tag": item["tag"],
                "title": title_of(item),
                "url": item["url"],
                "site": site_label(source),
                "source": source["key"] if source else "",
                "author": item["author"],
                "copies": item["copies"],
                "score": item["score"],
                "text": item["text"][:SENT_TEXT],
            }
        )
    groups = {}
    for entry in entries:
        groups.setdefault(entry["tag"], []).append(entry)
    last = c.execute(
        "SELECT max(sent_at) FROM listening_items WHERE automation_id=? AND status='sent'", (aid,)
    ).fetchone()[0]
    since = last or ""

    def count(where):
        return c.execute(
            f"SELECT count(*) FROM listening_items WHERE automation_id=? AND {where}", (aid, since)
        ).fetchone()[0]

    local = at.astimezone(ZoneInfo(zone))
    head = f"{flow['name']} · {local.strftime('%a %b')} {local.day}, {local.strftime('%H:%M')}"
    return {
        "listening": {
            "name": flow["name"],
            "sent_at": now,
            "count": len(entries),
            "waiting": waiting,
            "text": f"{head} · {len(entries)} of {len(entries) + waiting}\n\n{_lines(entries)}",
            "items": entries,
            "groups": [
                {"tag": tag, "count": len(members), "text": _lines(members), "items": members}
                for tag, members in groups.items()
            ],
            "counts": {
                "read": count("created_at>?"),
                "duplicates": count("status='duplicate' AND updated_at>?"),
                "dropped": count("status='dropped' AND scored_at>?"),
                "kept": count("status IN ('kept','sent') AND scored_at>?"),
                "sent": len(entries),
                "waiting": waiting,
                "failed": count("status='failed' AND updated_at>?"),
            },
        }
    }


def send(store, aid, at, now):
    """When the send time has come: the allowance's worth of kept items, best first, go out as one run and are marked
    `sent` with it, in one transaction. Nothing to send queues no run. The next time is the first one after now, so
    the times missed while the server was down send once."""
    from . import automations as A
    from . import cloud_access

    with store.transaction() as c:
        row = c.execute(
            "SELECT * FROM automations WHERE id=? AND deleted_at IS NULL AND enabled=1 AND trigger_type='listening'",
            (aid,),
        ).fetchone()
        if not row:
            return None
        row = dict(row)
        flow = A.flow_of(row)
        trigger = flow["trigger"]
        after = _iso(next_send(trigger, at))
        if not row["next_due_at"]:
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (after, aid))
            return None
        if row["next_due_at"] > now:
            return None
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (after, aid))
        if cloud_access.routines_paused(c):  # out of credit or locked: what is kept waits, as a schedule is passed over
            return None
        room = allowance(c, aid, trigger, at)
        items = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM listening_items WHERE automation_id=? AND status='kept' "
                "ORDER BY coalesce(score,0)+0.05*min(copies,4) DESC, posted_at DESC, rowid DESC LIMIT ?",
                (aid, room),
            )
        ]
        if not items:
            return None
        kept = c.execute(
            "SELECT count(*) FROM listening_items WHERE automation_id=? AND status='kept'", (aid,)
        ).fetchone()[0]
        payload = payload_of(c, row, flow, items, at, now, kept - len(items))
        queued = A.fire(c, "listening", payload, f"listening:{aid}:{row['next_due_at']}", only=aid)
        if not queued:
            return None
        rid = queued[0]
        # What scoring cost since the last send is this run's, so it counts toward the automation's spend cap. Each
        # scored item is charged once, to the first run after it: `run_id` on one not sent yet says it was.
        cost = c.execute(
            "SELECT coalesce(sum(cost),0) FROM listening_items WHERE automation_id=? AND run_id IS NULL AND cost>0",
            (aid,),
        ).fetchone()[0]
        c.execute("UPDATE listening_items SET run_id=? WHERE automation_id=? AND run_id IS NULL AND cost>0", (rid, aid))
        c.execute(
            f"UPDATE listening_items SET status='sent',run_id=?,sent_at=?,updated_at=? "
            f"WHERE id IN ({','.join('?' * len(items))})",
            (rid, now, now, *(i["id"] for i in items)),
        )
        if cost:
            run = c.execute("SELECT saved_state FROM automation_runs WHERE id=?", (rid,)).fetchone()
            state = json.loads(run["saved_state"] or "{}")
            state["cost"] = round(float(state.get("cost") or 0) + float(cost), 6)
            c.execute(
                "UPDATE automation_runs SET cost=?,saved_state=? WHERE id=?",
                (state["cost"], json.dumps(state, default=str), rid),
            )
        return rid


def run_pass(store, at=None, primary=None):
    """One pass over every listening that is on: read its pages, mark copies, score, expire, send. Each listening's
    failure is its own; the others still run. Reading and scoring share PASS_SECONDS, so the tick is never held long;
    the listening whose unscored post has waited longest goes first, so a busy one cannot keep the others waiting.
    Expire and send run for every listening whatever time is left: they make no outside call and must be on time."""
    at = at or datetime.now(timezone.utc)
    now = _iso(at)
    with store.read() as c:
        if not _has_tables(c):
            return
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT a.*, (SELECT min(i.created_at) FROM listening_items i WHERE i.automation_id=a.id "
                "AND i.status='new') AS waiting_since FROM automations a WHERE a.deleted_at IS NULL "
                "AND a.trigger_type='listening' ORDER BY waiting_since IS NULL, waiting_since, a.created_at"
            )
        ]
    left = _budget()
    for row in rows:
        if not left():
            break
        if not row["enabled"]:  # off: nothing is read or scored
            continue
        try:
            read_pages(store, row["id"], now, left)
            exact_copies(store, row["id"], now)
            score(store, row, json.loads(row["flow"]), now, primary, left)
        except Exception:
            log.exception("listening pass failed for %s", row["id"])
    for row in rows:
        try:
            with store.transaction() as c:
                expire(c, row["id"], now)  # on or off: what waits still grows too old
            send(store, row["id"], at, now)
        except Exception:
            log.exception("listening send failed for %s", row["id"])


def prune(c, now=None):
    """Maintenance: sent items go after KEEP_SENT_DAYS, the rest after KEEP_DAYS (the copy check's window), and a
    removed source once nothing of its is left."""
    if not H._has_table(c, "listening_items"):
        return 0
    now = now or H.now()
    gone = c.execute(
        "DELETE FROM listening_items WHERE (status='sent' AND coalesce(sent_at,created_at)<?) "
        "OR (status<>'sent' AND created_at<?)",
        (H.shift(now, days=-KEEP_SENT_DAYS), H.shift(now, days=-KEEP_DAYS)),
    ).rowcount
    c.execute(
        "DELETE FROM listening_sources WHERE deleted_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM listening_items i WHERE i.source_id=listening_sources.id)"
    )
    return gone


# ----------------------------------------------------------------------------- reads
def item_view(row, sources):
    source = sources.get(row["source_id"])
    return {
        "id": row["id"],
        "automation_id": row["automation_id"],
        "source_id": row["source_id"],
        "source": source["key"] if source else "",
        "site": site_label(source),
        "native_id": row["native_id"],
        "url": row["url"],
        "author": row["author"],
        "title": title_of(row),
        "text": row["text"][:SENT_TEXT],
        "posted_at": row["posted_at"],
        "status": row["status"],
        "tag": row["tag"],
        "score": row["score"],
        "copies": row["copies"],
        "duplicate_of_id": row["duplicate_of_id"],
        "tries": row["tries"],
        "scored_at": row["scored_at"],
        "run_id": row["run_id"] if row["status"] == "sent" else None,
        "sent_at": row["sent_at"],
        "created_at": row["created_at"],
    }


def _visible(c, who):
    """The listenings `who` may see, by id: the automations' own rule."""
    from . import automations as A

    out = {}
    for row in c.execute("SELECT * FROM automations WHERE deleted_at IS NULL AND trigger_type='listening'").fetchall():
        flow = A.flow_of(row)
        if A.may_see(c, who, flow):
            out[row["id"]] = flow
    return out


def stats(c, who, since=None):
    """The Market page's What are buyers saying? chart and list: items sent each UTC day by site, and the newest sent
    items, each one's text clipped. Only from the listenings the caller may see."""
    since = since or H.shift(H.now(), days=-7)
    seen = _visible(c, who) if _has_tables(c) else {}
    days, recent = {}, []
    if seen:
        marks = ",".join("?" * len(seen))
        sources = {
            r["id"]: dict(r)
            for r in c.execute(f"SELECT * FROM listening_sources WHERE automation_id IN ({marks})", tuple(seen))
        }
        for r in c.execute(
            f"SELECT substr(sent_at,1,10) AS day, source_id, count(*) AS n FROM listening_items "
            f"WHERE automation_id IN ({marks}) AND status='sent' AND sent_at>=? GROUP BY day, source_id",
            (*seen, since),
        ):
            site = site_label(sources.get(r["source_id"])) or "Other"
            days.setdefault(r["day"], {})
            days[r["day"]][site] = days[r["day"]].get(site, 0) + r["n"]
        for r in c.execute(
            f"SELECT * FROM listening_items WHERE automation_id IN ({marks}) AND status='sent' AND sent_at>=? "
            f"ORDER BY sent_at DESC, rowid DESC LIMIT ?",
            (*seen, since, RECENT_MAX),
        ):
            text = normalize(r["text"] or r["title"])
            recent.append(
                {
                    "item_id": r["id"],
                    "source": site_label(sources.get(r["source_id"])) or "Other",
                    "url": r["url"],
                    "author": r["author"],
                    "published_at": r["posted_at"],
                    "text": text if len(text) <= RECENT_TEXT else text[: RECENT_TEXT - 1] + "…",
                    "destinations": [r["tag"]] if r["tag"] else [],
                    "at": r["sent_at"],
                }
            )
    return {"since": since, "daily": [{"day": d, "sources": days[d]} for d in sorted(days)], "recent": recent}


# ----------------------------------------------------------------------------- HTTP
class ListeningItem(Contract):
    native_id: str = Field(min_length=1, max_length=300)
    url: str = Field(default="", max_length=2000)
    author: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=1000)
    text: str | None = Field(default=None, max_length=200_000)
    posted_at: str | None = Field(default=None, max_length=40)


class ListeningSave(Contract):
    status: str = Field(default="ok", max_length=20)
    error: str = Field(default="", max_length=2000)
    items: list[ListeningItem] = Field(default_factory=list, max_length=MAX_ITEMS)


def install(app, store, auth, mutate):
    from . import automations as A

    def may_feed(c, who, source):
        """Who saves what a source read: any bot (the one that reads sites signed in), the top of the org, and whoever
        may change the listening."""
        return who.role in ("owner", "bot") or A.may_change(c, who, json.loads(source["flow"]))

    @app.get("/api/v2/listening/sources")
    def sources_list(request: Request, due: int = 0, automation: str = "", limit: int = LIST_MAX):
        """The sources the caller may read. With `due=1`: the Search sources of the listenings that are on that were
        not read in the last day, the longest unread first."""
        who = request.state.identity
        auth.domain(who)
        now = H.now()
        with store.read() as c:
            rows = c.execute(
                "SELECT s.*, a.name, a.enabled, a.flow FROM listening_sources s JOIN automations a "
                "ON a.id=s.automation_id WHERE s.deleted_at IS NULL AND a.deleted_at IS NULL "
                "AND a.trigger_type='listening'"
                + (" AND a.id=?" if automation else "")
                + (
                    " AND a.enabled=1 AND s.kind='search' AND (s.last_read_at IS NULL OR s.last_read_at<?)"
                    if due
                    else ""
                )
                + " ORDER BY s.last_read_at IS NOT NULL, s.last_read_at, s.created_at, s.key",
                (*((automation,) if automation else ()), *((H.shift(now, hours=-SEARCH_DUE_HOURS),) if due else ())),
            ).fetchall()
            out = [
                {**source_view(r, now), "automation": r["name"]}
                for r in rows
                if who.role in ("owner", "bot") or A.may_see(c, who, json.loads(r["flow"]))
            ]
        return {"sources": out[: max(1, min(int(limit), LIST_MAX))]}

    @app.post("/api/v2/listening/sources/{source_id}/items")
    def source_save(request: Request, source_id: str, body: ListeningSave):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            source = _source(c, source_id)
            if not may_feed(c, who, source):
                raise Problem("forbidden", "Only a bot or whoever may change this listening saves what it read", 403)
            return save(c, source, body.status, [i.model_dump() for i in body.items], error=body.error)

        return mutate(request, body, work)

    @app.post("/api/v2/listening/sources/{source_id}/address")
    def source_address(request: Request, source_id: str, body: Contract):
        """A new address for a push source. The old one stops working; the new one is shown this once."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            source = _source(c, source_id)
            if not A.may_change(c, who, json.loads(source["flow"])):
                raise Problem("forbidden", "Only whoever may change this listening gives it a new address", 403)
            if source["kind"] != "push":
                raise Problem("validation", "Only a push source has an address", 422)
            address = _new_address(c, store.settings, source["id"])
            return {"source": source["key"], "source_id": source["id"], "push_address": address}

        # The address is shown once: the idempotency record keeps the answer without it (store.SHOWN_ONCE).
        out = dict(mutate(request, body, work))
        if "push_address" in out:
            out["address"] = out.pop("push_address")
        return out

    @app.post(PUSH_PATH + "{token}")
    def push(token: str, body: ListeningSave):
        """A push source's address: no sign-in, the token is the right. The body is Save's."""
        with store.transaction() as c:
            found = c.execute("SELECT id FROM listening_sources WHERE token_hash=?", (_digest(token),)).fetchone()
            if not found:
                raise Problem("not_found", "No such address", 404)
            source = _source(c, found["id"])
            if not _push_allowed(source["id"]):
                raise Problem("rate_limited", f"At most {PUSH_PER_MINUTE} calls a minute", 429)
            out = save(c, source, body.status, [i.model_dump() for i in body.items], error=body.error)
            return {"seen": out["seen"], "saved": out["saved"]}

    @app.get("/api/v2/listening/items")
    def items_list(request: Request, automation: str, status: str = "", run: str = "", limit: int = 100):
        """A listening's items, newest first, for its run page and for looking into what it did."""
        who = request.state.identity
        auth.domain(who)
        if status and status not in ITEM_STATUSES:
            raise Problem("validation", f"status is one of {', '.join(ITEM_STATUSES)}", 422)
        with store.read() as c:
            row = A._row(c, automation)
            if not row or not A.may_see(c, who, A.flow_of(row)):
                raise Problem("not_found", "No such listening", 404)
            sources = {
                r["id"]: dict(r)
                for r in c.execute("SELECT * FROM listening_sources WHERE automation_id=?", (automation,))
            }
            rows = c.execute(
                "SELECT * FROM listening_items WHERE automation_id=?"
                + (" AND status=?" if status else "")
                + (" AND run_id=? AND status='sent'" if run else "")
                + " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (
                    automation,
                    *((status,) if status else ()),
                    *((run,) if run else ()),
                    max(1, min(int(limit), LIST_MAX)),
                ),
            ).fetchall()
            return {"items": [item_view(r, sources) for r in rows]}

    @app.get("/api/v2/listening/stats")
    def stats_route(request: Request, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return stats(c, who, since)
