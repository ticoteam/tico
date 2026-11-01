"""Listening's observation store and the shared intake: save, score, route, resolve.

Four tables in the hub database (hubdb `LISTENING_SCHEMA`), and a fifth, `listening_destinations`,
that Settings > Listening writes (`ensure`):

    listen_runs        one sweep of one query on one site; `status` tells "no results" from "blocked"
    listen_items       what the agent saw, one row per platform post, unique on (source, native_id)
    listen_judgments   the decision model's scores, one noul per category; a new row on reclassification
    intake_items       the shared inbox, unique on (destination, item_id); the receiver resolves it
    listening_destinations   destinations set in Settings; merged over the registry file's, the table wins

Flow: Listening reads pages signed in and posts a run with the items it saw (`POST
/listening/runs`). The decision model scores each item against every category in one call (`POST
/listening/judge`, the `listening-item` question set). Every destination whose threshold a
score clears gets one intake row. The receiving bot reads its `new` rows and marks each
accepted (with the record it made), rejected (with a reason) or duplicate.

Dedupe is the two unique keys: a post saved twice is one row, and a post routed twice to the
same place is one row, so a re-sent run or a re-run judgment is harmless. Retries are the
receiver polling for `new`; nothing is pushed. Reclassification adds a judgment and routes it:
a destination the post newly clears gets a row, rows already there are left alone.

Only Listening (and the owner) writes runs and judgments. A receiver reads and resolves only
its own destination. The posts carry third-party names and handles, so a bot that is neither
sees no rows (`backend/sql.py` says the same for `hub sql`).
"""

import hashlib
import json
import logging
import re
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import yaml
from fastapi import Request
from pydantic import Field

from clients import judge as J

from . import hubdb as H
from .models import Contract
from .store import Problem

LISTENER = "bot:listening"
QUESTION_SET = "listening-item"
RUN_STATUSES = ("ok", "blocked", "rate_limited", "error")
INTAKE_STATUSES = ("new", "accepted", "rejected", "duplicate")
RESOLVED = ("accepted", "rejected", "duplicate")
SOURCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
MAX_ITEMS = 200
MAX_JUDGE = 50
REJUDGE_DAYS = 2              # after a question-set version bump, rescore only this recent a post

# Where a post goes and when. Destinations are the company's: `<registry>/listening.yaml`
# (`templates/environment-registry/listening.yaml` is the shape), and a company with none routes
# nowhere. Each destination names a `category` (a noul in questions/listening-item.json), a
# `threshold`, the `receiver` bot that resolves its inbox, optional `readers`, an `unless` category
# and threshold that keeps a post out (a vendor's promotion dressed as a question is not a lead),
# and `what`, the sentence the receiver reads. A post goes to every destination whose threshold its
# score reaches. Changing the file changes routing from the next decision on; rows already in an
# inbox stay.
LISTENING_FILE = "listening.yaml"
WARNING_CACHE_LIMIT = 128
_warning_revisions = OrderedDict()
_warning_lock = threading.Lock()


def _warn_configuration(settings, kind, source, problems, questions=None):
    """Log once per configuration revision; retain only bounded opaque fingerprints."""
    key = (hashlib.sha256(str(settings.registry_dir.absolute()).encode()).digest(), kind)
    revision = hashlib.sha256(json.dumps([source, questions, problems], default=str).encode()).digest()
    with _warning_lock:
        previous = _warning_revisions.get(key)
        if kind == "destinations" and previous != revision:
            _warning_revisions.pop((key[0], "questions"), None)
        _warning_revisions[key] = revision
        _warning_revisions.move_to_end(key)
        while len(_warning_revisions) > WARNING_CACHE_LIMIT:
            _warning_revisions.popitem(last=False)
    if previous != revision:
        for problem in problems:
            logging.getLogger("tico.listening").warning("%s: %s", LISTENING_FILE, problem)


def _actor(value):
    value = str(value or "").strip()
    return value if not value or ":" in value else "bot:" + value


def question_set(settings):
    return J.load_set(QUESTION_SET, registry_dir=settings.registry_dir)


def category_problems(dests, qset):
    """Configuration names only; question text and other registry content stay private."""
    questions = qset["questions"]
    problems = []
    for name, cfg in dests.items():
        for field, category in (("category", cfg["category"]), ("unless category", (cfg.get("unless") or {}).get("category"))):
            if category is not None and (questions.get(category) or {}).get("type") != "noul":
                problems.append(f"{name}: {field} {category!r} needs a noul question in {QUESTION_SET}")
    return problems


def _finish(settings, out, source, qset, check_questions, problems=None):
    """Log the destination problems once, and check the categories of what is configured."""
    if problems is not None:
        _warn_configuration(settings, "destinations", source, problems)
    if out and check_questions:
        try:
            loaded = qset or question_set(settings)
            found = category_problems(out, loaded)
        except J.JudgeError as exc:
            loaded = getattr(exc, "config_revision", exc.code)
            found = [f"{QUESTION_SET} question set could not be loaded; check registry/questions"]
        _warn_configuration(settings, "questions", source, found, loaded)
    elif check_questions:
        _warn_configuration(settings, "questions", source, [])
    return out


def destinations(settings, conn=None, *, qset=None, check_questions=True):
    """The company's destinations: the registry file's, with the ones saved in Settings over them (the table
    wins on the same key; pass `conn` to include it). A malformed file entry is skipped and logged."""
    extra = stored(conn) if conn is not None else {}
    try:
        source = (settings.registry_dir / LISTENING_FILE).read_text()
    except OSError:
        return _finish(settings, dict(extra), None, qset, check_questions, [])
    try:
        document = yaml.safe_load(source) or {}
    except yaml.YAMLError:
        return _finish(settings, dict(extra), source, qset, check_questions, [])
    found = document.get("destinations") if isinstance(document, dict) else None
    if found is not None and not isinstance(found, dict):
        _warn_configuration(settings, "destinations", source, ["destinations must be a map"])
        return dict(extra)
    out, problems = {}, []
    for name, cfg in (found or {}).items():
        try:
            entry = {"category": str(cfg["category"]), "threshold": float(cfg["threshold"]),
                     "receiver": _actor(cfg["receiver"]), "what": str(cfg.get("what") or "")}
            if not 0 < entry["threshold"] <= 1 or not entry["category"] or not entry["receiver"]:
                raise ValueError("category, receiver and a threshold in (0, 1]")
            if cfg.get("readers"):
                entry["readers"] = [_actor(r) for r in cfg["readers"]]
            if cfg.get("unless"):
                entry["unless"] = {"category": str(cfg["unless"]["category"]), "threshold": float(cfg["unless"]["threshold"])}
        except (KeyError, TypeError, ValueError, AttributeError):
            problems.append(f"destination {name!r} skipped (invalid destination configuration)")
            continue
        out[str(name)] = entry
    out.update(extra)
    return _finish(settings, out, source, qset, check_questions, problems)


# ----------------------------------------------------------------------------- destinations saved in Settings
DESTINATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS listening_destinations (
  key TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  threshold REAL NOT NULL,
  receiver TEXT NOT NULL,
  readers TEXT,
  unless TEXT,
  what TEXT,
  updated_by TEXT,
  updated_at TEXT)
"""
DESTINATION_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MARKET_SEEDED = "listening.market_seeded"    # registry_metadata: the market destination was offered once
MARKET_DESTINATION = {"category": "market", "threshold": 0.70, "receiver": "bot:librarian",
                      "what": "a post about the market: competitors, buyers, prices, regulation"}


def ensure(c):
    c.execute(DESTINATIONS_SCHEMA)


def stored(conn):
    """The destinations saved in Settings, shaped like the file's."""
    try:
        rows = H._rows(conn.execute("SELECT * FROM listening_destinations ORDER BY key"))
    except sqlite3.OperationalError:       # a database from before the table
        return {}
    out = {}
    for row in rows:
        entry = {"category": row["category"], "threshold": float(row["threshold"]),
                 "receiver": row["receiver"], "what": row["what"] or ""}
        readers = _loads(row["readers"], [])
        if readers:
            entry["readers"] = [str(r) for r in readers]
        unless = _loads(row["unless"], None)
        if isinstance(unless, dict) and unless.get("category"):
            entry["unless"] = {"category": str(unless["category"]), "threshold": float(unless.get("threshold") or 0)}
        out[row["key"]] = entry
    return out


def _noul_category(qset, category, field):
    if (qset["questions"].get(category) or {}).get("type") != "noul":
        raise Problem("invalid", f"{field} {category!r} is not a yes-or-no question in {QUESTION_SET}", 422)


def _threshold(value, field="threshold"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= 1:
        raise Problem("invalid", f"{field} is a number above 0 and up to 1", 422)
    return float(value)


def check_destination(conn, settings, key, body):
    """A destination as the file would be read: the key, a noul category, a threshold in (0, 1], a receiver that
    is a bot that exists, readers that exist and an optional `unless`. Refused with 422 before it is saved."""
    key = str(key or "").strip().lower()
    if not DESTINATION_KEY.match(key):
        raise Problem("invalid", "a destination key is a short lowercase name like market or leads", 422)
    try:
        qset = question_set(settings)
    except J.JudgeError:
        raise Problem("judge_questions", "The listening-item question set could not be loaded. "
                      "Check registry/questions/listening-item.json.", 422) from None
    category = str(body.category or "").strip()
    _noul_category(qset, category, "category")
    receiver = H.resolve_actor(conn, _actor(body.receiver))
    if not receiver or not receiver.startswith("bot:") or (H.bot(conn, receiver[4:]) or {}).get("state") == "archived":
        raise Problem("invalid", f"receiver {body.receiver!r} is not a bot on this team", 422)
    readers = []
    for reader in body.readers:
        actor = H.resolve_actor(conn, _actor(reader))
        if not actor or actor.startswith("bot:") and (H.bot(conn, actor[4:]) or {}).get("state") == "archived":
            raise Problem("invalid", f"reader {reader!r} is not on this team", 422)
        if actor not in readers and actor != receiver:
            readers.append(actor)
    entry = {"category": category, "threshold": _threshold(body.threshold), "receiver": receiver,
             "what": str(body.what or "").strip()}
    if readers:
        entry["readers"] = readers
    if body.unless:
        unless = str(body.unless.category or "").strip()
        _noul_category(qset, unless, "unless category")
        entry["unless"] = {"category": unless, "threshold": _threshold(body.unless.threshold, "unless threshold")}
    return key, entry


def save_destination(conn, settings, who, key, body):
    key, entry = check_destination(conn, settings, key, body)
    now = H.now()
    conn.execute("INSERT INTO listening_destinations (key, category, threshold, receiver, readers, unless, what, "
                 "updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "category=excluded.category, threshold=excluded.threshold, receiver=excluded.receiver, "
                 "readers=excluded.readers, unless=excluded.unless, what=excluded.what, "
                 "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (key, entry["category"], entry["threshold"], entry["receiver"],
                  json.dumps(entry.get("readers") or []), json.dumps(entry.get("unless")), entry["what"], who.actor, now))
    H.event(conn, who.actor, "listening.destination", key, {"category": entry["category"], "receiver": entry["receiver"]})
    return key


def remove_destination(conn, settings, who, key):
    """A saved destination goes; one only the file has is the file's to change."""
    if not conn.execute("SELECT 1 FROM listening_destinations WHERE key=?", (key,)).fetchone():
        if key in destinations(settings, None, check_questions=False):
            raise Problem("yaml", f"{key} comes from listening.yaml; edit that file to remove it", 409)
        raise Problem("not_found", f"no destination {key}", 404)
    conn.execute("DELETE FROM listening_destinations WHERE key=?", (key,))
    H.event(conn, who.actor, "listening.destination_removed", key, {})
    return key


def seed_market(conn, settings):
    """Once, when the Librarian exists and nothing routes `market`: offer it the market posts. The marker is
    set either way, so an owner who removes the destination later keeps it removed."""
    if conn.execute("SELECT 1 FROM registry_metadata WHERE key=?", (MARKET_SEEDED,)).fetchone():
        return False
    librarian = H.bot(conn, MARKET_DESTINATION["receiver"][4:])
    if not librarian or librarian.get("state") == "archived":
        return False
    try:
        qset = question_set(settings)
    except J.JudgeError:
        return False                       # try again once the questions load
    added = False
    if (qset["questions"].get("market") or {}).get("type") == "noul" and not any(
            d["category"] == "market" for d in destinations(settings, conn, check_questions=False).values()):
        key = "market"
        while conn.execute("SELECT 1 FROM listening_destinations WHERE key=?", (key,)).fetchone() \
                or key in destinations(settings, None, check_questions=False):
            key += "-2"
        conn.execute("INSERT INTO listening_destinations (key, category, threshold, receiver, readers, unless, what, "
                     "updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (key, "market", MARKET_DESTINATION["threshold"], MARKET_DESTINATION["receiver"], "[]", "null",
                      MARKET_DESTINATION["what"], H.KEEPER, H.now()))
        H.event(conn, H.KEEPER, "listening.destination", key, {"category": "market", "receiver": "bot:librarian", "seeded": True})
        added = True
    conn.execute("INSERT INTO registry_metadata VALUES(?,?)", (MARKET_SEEDED, json.dumps({"at": H.now(), "added": added})))
    return added


def destination_list(conn, settings):
    """Every destination with where it comes from, the categories to choose and the bots that can receive."""
    from_file = destinations(settings, None, check_questions=False)
    saved = stored(conn)
    rows = []
    for key, cfg in sorted({**from_file, **saved}.items()):
        meta = {}
        if key in saved:
            meta = H._one(conn, "SELECT updated_by, updated_at FROM listening_destinations WHERE key=?", (key,)) or {}
        rows.append({"key": key, "category": cfg["category"], "threshold": cfg["threshold"], "receiver": cfg["receiver"],
                     "readers": cfg.get("readers", []), "unless": cfg.get("unless"), "what": cfg.get("what", ""),
                     "source": "settings" if key in saved else "yaml", "overrides_yaml": key in saved and key in from_file,
                     **meta})
    try:
        qset = question_set(settings)
        categories = [{"id": qid, "label": qid.replace("_", " ")} for qid, q in qset["questions"].items()
                      if q.get("type") == "noul"]
    except J.JudgeError:
        categories = []
    bots = [{"actor": "bot:" + r["slug"], "name": r["display_name"] or r["slug"]} for r in H._rows(conn.execute(
        "SELECT slug, display_name FROM bots WHERE state<>'archived' ORDER BY display_name, slug"))]
    return {"destinations": rows, "categories": categories, "receivers": bots}


def receivers(dests):
    return {cfg["receiver"] for cfg in dests.values()}


def destinations_of(actor, dests):
    """The inboxes `actor` may read: those it receives, and those it is a reader of. Only the
    receiver resolves an item (`resolve`)."""
    return [name for name, cfg in dests.items() if actor == cfg["receiver"] or actor in cfg.get("readers", ())]


def sees_everything(who):
    return who.role == "owner" or who.actor == LISTENER


def require_listener(who):
    if not sees_everything(who):
        raise Problem("forbidden", "Only Listening and the company owner save runs and decisions", 403)


def require_reader(who, dests):
    if sees_everything(who) or destinations_of(who.actor, dests):
        return
    raise Problem("forbidden", "Saved posts are for Listening, the bots it routes to, and the owner", 403)


# ----------------------------------------------------------------------------- small helpers
def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def content_hash(text):
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def run(conn, run_id):
    return H._row(conn.execute("SELECT * FROM listen_runs WHERE id=?", (run_id,)).fetchone())


def item(conn, item_id):
    return H._row(conn.execute("SELECT * FROM listen_items WHERE id=?", (item_id,)).fetchone())


def _judgment_row(row):
    if not row:
        return None
    out = dict(row)
    out["scores"] = _loads(out.pop("scores"), {})
    return out


def judgment(conn, judgment_id):
    return _judgment_row(conn.execute("SELECT * FROM listen_judgments WHERE id=?", (judgment_id,)).fetchone())


def current_judgment(conn, item_id):
    """The newest judgment is the current one."""
    return _judgment_row(conn.execute("SELECT * FROM listen_judgments WHERE item_id=? "
                                      "ORDER BY judged_at DESC, rowid DESC LIMIT 1", (item_id,)).fetchone())


def intake_row(conn, intake_id):
    return H._row(conn.execute("SELECT * FROM intake_items WHERE id=?", (intake_id,)).fetchone())


# ----------------------------------------------------------------------------- writes
def record_run(conn, actor, *, source, query, status, started_at=None, pages_read=0, items_seen=None,
               note="", items=()):
    """One sweep and every post it saw, in one transaction. A post already saved is not changed."""
    source = str(source or "").strip().lower()
    if not SOURCE.match(source):
        raise Problem("invalid", f"a source is a short lowercase name like x, reddit or linkedin, not {source!r}", 422)
    if status not in RUN_STATUSES:
        raise Problem("invalid", f"a run status is {'|'.join(RUN_STATUSES)}, not {status}", 422)
    query = str(query or "").strip()
    if not query:
        raise Problem("invalid", "say which query or page the run read", 422)
    if status != "ok" and not str(note or "").strip():
        raise Problem("invalid", f"a {status} run says what happened in `note`", 422)
    items = list(items or [])
    if len(items) > MAX_ITEMS:
        raise Problem("invalid", f"a run saves at most {MAX_ITEMS} posts; split the sweep", 422)
    now = H.now()
    row = {"id": H.new_id(), "source": source, "query": query, "started_at": started_at or now,
           "status": status, "pages_read": int(pages_read or 0),
           "items_seen": len(items) if items_seen is None else int(items_seen), "note": str(note or "")}
    conn.execute("INSERT INTO listen_runs (id, source, query, started_at, status, pages_read, items_seen, note) "
                 "VALUES (:id,:source,:query,:started_at,:status,:pages_read,:items_seen,:note)", row)
    saved, seen = [], set()
    for raw in items:
        native_id = str(raw.get("native_id") or "").strip()
        url = str(raw.get("url") or "").strip()
        content = str(raw.get("content") or "").strip()
        if not native_id or not url or not content:
            raise Problem("invalid", "every post needs native_id, url and content", 422)
        if native_id in seen:
            continue
        seen.add(native_id)
        existing = conn.execute("SELECT id FROM listen_items WHERE source=? AND native_id=?",
                                (source, native_id)).fetchone()
        if existing:
            saved.append({"id": existing["id"], "native_id": native_id, "new": False})
            continue
        new = {"id": H.new_id(), "source": source, "native_id": native_id, "url": url,
               "author": (str(raw.get("author")).strip() or None) if raw.get("author") else None,
               "author_url": (str(raw.get("author_url")).strip() or None) if raw.get("author_url") else None,
               "content": content, "content_hash": content_hash(content),
               "published_at": raw.get("published_at") or None, "first_seen_at": now, "first_run_id": row["id"]}
        conn.execute("INSERT INTO listen_items (id, source, native_id, url, author, author_url, content, content_hash, "
                     "published_at, first_seen_at, first_run_id) VALUES (:id,:source,:native_id,:url,:author,"
                     ":author_url,:content,:content_hash,:published_at,:first_seen_at,:first_run_id)", new)
        saved.append({"id": new["id"], "native_id": native_id, "new": True})
    H.event(conn, actor, "listen.run", row["id"], {"source": source, "status": status, "items": len(saved),
                                                    "new": sum(1 for s in saved if s["new"])})
    return {"run": run(conn, row["id"]), "items": saved}


def check_scores(scores):
    if not isinstance(scores, dict) or not scores:
        raise Problem("invalid", "scores is a map of category -> probability", 422)
    out = {}
    for name, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
            raise Problem("invalid", f"score {name} is not a probability between 0 and 1", 422)
        out[str(name)] = float(value)
    return out


def text_key(content):
    """The words of a post, without the community tag or punctuation: equal for a cross-post."""
    text = re.sub(r"^\[[^\]]{1,80}\]\s*", "", str(content or ""))
    return re.sub(r"[^a-z0-9]+", "", text.lower())[:400]


def cross_post(conn, row, destination, days=30):
    """True when the same text (same author, when known) already sits in this inbox."""
    key = text_key(row["content"])
    if len(key) < 40:
        return False
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    for other in conn.execute("SELECT i.id, i.author, i.content FROM intake_items k JOIN listen_items i ON i.id=k.item_id "
                              "WHERE k.destination=? AND k.item_id<>? AND k.created_at>=?",
                              (destination, row["id"], since)):
        if (not row["author"] or not other["author"] or other["author"] == row["author"]) \
                and text_key(other["content"]) == key:
            return True
    return False


def route(conn, row, scored, dests):
    """Open an intake row for every destination this judgment clears. Existing rows stay, and a
    cross-post of text already in that inbox is not sent again."""
    opened = []
    for name, cfg in dests.items():
        if scored["scores"].get(cfg["category"], 0.0) < cfg["threshold"]:
            continue
        unless = cfg.get("unless")
        if unless and scored["scores"].get(unless["category"], 0.0) >= unless["threshold"]:
            continue
        if cross_post(conn, row, name):
            continue
        intake_id = H.new_id()
        cur = conn.execute("INSERT OR IGNORE INTO intake_items (id, destination, item_id, judgment_id, status, created_at) "
                           "VALUES (?,?,?,?, 'new', ?)", (intake_id, name, row["id"], scored["id"], H.now()))
        if cur.rowcount:
            opened.append(intake_row(conn, intake_id))
            from . import automations
            automations.on_listening(conn, opened[-1], row)
    return opened


def add_judgment(conn, actor, item_id, question_set, scores, dests, judged_at=None):
    """Save one set of scores for a post and route it."""
    row = item(conn, item_id)
    if not row:
        raise Problem("not_found", f"no saved post {item_id}", 404)
    question_set = str(question_set or "").strip()
    if not question_set:
        raise Problem("invalid", "name the question set and version, like listening-item@1", 422)
    scores = check_scores(scores)
    jid = H.new_id()
    conn.execute("INSERT INTO listen_judgments (id, item_id, question_set, scores, judged_at) VALUES (?,?,?,?,?)",
                 (jid, item_id, question_set, json.dumps(scores, sort_keys=True), judged_at or H.now()))
    scored = judgment(conn, jid)
    opened = route(conn, row, scored, dests)
    H.event(conn, actor, "listen.judgment", item_id, {"set": question_set, "routed": [o["destination"] for o in opened]})
    return {"judgment": scored, "intake": opened}


def resolve(conn, who, intake_id, status, dests, receiver_ref="", reason=""):
    """The receiver's verdict. Saying the same thing twice is fine; changing it is refused."""
    row = intake_row(conn, intake_id)
    if not row:
        raise Problem("not_found", f"no intake item {intake_id}", 404)
    if who.role != "owner" and dests.get(row["destination"], {}).get("receiver") != who.actor:
        raise Problem("forbidden", f"only the {row['destination']} receiver or the owner resolves this item", 403)
    if status not in RESOLVED:
        raise Problem("invalid", f"a verdict is {'|'.join(RESOLVED)}, not {status}", 422)
    receiver_ref, reason = str(receiver_ref or "").strip(), str(reason or "").strip()
    if status in ("accepted", "duplicate") and not receiver_ref:
        raise Problem("invalid", f"{status} names the record: the id you created or the one it duplicates", 422)
    if status == "rejected" and not reason:
        raise Problem("invalid", "say in one sentence why it is rejected", 422)
    if row["status"] != "new":
        if (row["status"], row["receiver_ref"] or "", row["reason"] or "") == (status, receiver_ref, reason):
            return row
        raise Problem("conflict", f"{intake_id} is already {row['status']}", 409)
    conn.execute("UPDATE intake_items SET status=?, receiver_ref=?, reason=?, decided_at=? WHERE id=?",
                 (status, receiver_ref or None, reason or None, H.now(), intake_id))
    H.event(conn, who.actor, "intake.resolve", intake_id, {"destination": row["destination"], "status": status,
                                                           "receiver_ref": receiver_ref})
    return intake_row(conn, intake_id)


# ----------------------------------------------------------------------------- reads
def state_for(row):
    """What the decision model reads: the post as the agent saw it."""
    return {"source": row["source"], "url": row["url"], "author": row["author"] or "",
            "published_at": row["published_at"] or "", "content": row["content"]}


def unjudged(conn, label, limit=MAX_JUDGE):
    """Posts the current question set has not scored: every post never judged, and a post judged
    by an older version only while it is recent. A version bump rescores the last two days, not the
    whole archive, so new sweeps never queue behind old posts and receivers are not re-sent them."""
    since = H.shift(H.now(), days=-REJUDGE_DAYS)
    return H._rows(conn.execute(
        "SELECT * FROM listen_items i WHERE NOT EXISTS (SELECT 1 FROM listen_judgments j "
        "WHERE j.item_id=i.id AND j.question_set=?) AND (i.first_seen_at>=? OR NOT EXISTS "
        "(SELECT 1 FROM listen_judgments o WHERE o.item_id=i.id)) ORDER BY first_seen_at, rowid LIMIT ?",
        (label, since, int(limit))))


def inbox(conn, who, dests, destination=None, status="new", limit=100):
    """Intake rows with the post and the scores that routed it, oldest first."""
    if status and status not in INTAKE_STATUSES:
        raise Problem("invalid", f"a status is {'|'.join(INTAKE_STATUSES)}", 422)
    if destination and destination not in dests:
        raise Problem("invalid", f"a destination is {'|'.join(dests) or 'not configured (registry/listening.yaml)'}", 422)
    allowed = list(dests) if sees_everything(who) else destinations_of(who.actor, dests)
    if destination:
        if destination not in allowed:
            raise Problem("forbidden", f"{who.actor} does not receive {destination}", 403)
        allowed = [destination]
    if not allowed:
        return []
    marks = ",".join("?" * len(allowed))
    sql = f"SELECT * FROM intake_items WHERE destination IN ({marks})"
    args = list(allowed)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY created_at, rowid LIMIT ?"
    args.append(max(1, min(int(limit or 100), 500)))
    out = []
    for row in H._rows(conn.execute(sql, args)):
        post = item(conn, row["item_id"])
        scored = judgment(conn, row["judgment_id"])
        cfg = dests.get(row["destination"]) or {"category": "?", "threshold": 0}
        out.append({**row, "post": post, "scores": scored["scores"] if scored else {},
                    "question_set": scored["question_set"] if scored else None,
                    "routed_because": f"{cfg['category']} >= {cfg['threshold']}"})
    return out


def trace(conn, item_id):
    """A post, the run that first saw it, every judgment, and every place it went."""
    row = item(conn, item_id)
    if not row:
        raise Problem("not_found", f"no saved post {item_id}", 404)
    judgments = [_judgment_row(r) for r in conn.execute(
        "SELECT * FROM listen_judgments WHERE item_id=? ORDER BY judged_at, rowid", (item_id,)).fetchall()]
    intake = H._rows(conn.execute("SELECT * FROM intake_items WHERE item_id=? ORDER BY created_at, rowid", (item_id,)))
    return {"item": row, "run": run(conn, row["first_run_id"]) if row["first_run_id"] else None,
            "judgments": judgments, "current": judgments[-1] if judgments else None, "intake": intake}


def runs(conn, since=None, source=None, limit=200):
    sql, args = "SELECT * FROM listen_runs WHERE 1", []
    if since:
        sql += " AND started_at>=?"
        args.append(since)
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
    args.append(max(1, min(int(limit or 200), 1000)))
    return H._rows(conn.execute(sql, args))


def stats(conn, since=None):
    """Coverage by source and status, and precision by destination from the receivers' verdicts."""
    since = since or (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    coverage = {}
    for r in conn.execute("SELECT source, status, count(*) n, sum(items_seen) seen FROM listen_runs "
                          "WHERE started_at>=? GROUP BY source, status", (since,)):
        coverage.setdefault(r["source"], {})[r["status"]] = {"runs": r["n"], "items_seen": r["seen"] or 0}
    verdicts = {}
    for r in conn.execute("SELECT destination, status, count(*) n FROM intake_items WHERE created_at>=? "
                          "GROUP BY destination, status", (since,)):
        verdicts.setdefault(r["destination"], {s: 0 for s in INTAKE_STATUSES})[r["status"]] = r["n"]
    for counts in verdicts.values():
        decided = counts["accepted"] + counts["rejected"]
        counts["precision"] = round(counts["accepted"] / decided, 3) if decided else None
    saved = conn.execute("SELECT count(*) FROM listen_items WHERE first_seen_at>=?", (since,)).fetchone()[0]
    routed = conn.execute("SELECT count(DISTINCT item_id) FROM intake_items WHERE created_at>=?", (since,)).fetchone()[0]
    return {"since": since, "coverage": coverage, "destinations": verdicts, "saved": saved, "routed": routed}


RECENT_MAX = 25
RECENT_TEXT = 240


def timeline(conn, who, dests, since):
    """Market › Listening's chart and list (redesign only): posts first seen each UTC day by source, and the newest
    posts routed to a destination the caller reads, one row per post, its text clipped."""
    days = {}
    for r in conn.execute("SELECT substr(first_seen_at,1,10) day, source, count(*) n FROM listen_items "
                          "WHERE first_seen_at>=? GROUP BY day, source", (since,)):
        days.setdefault(r["day"], {})[r["source"]] = r["n"]
    allowed = list(dests) if sees_everything(who) else destinations_of(who.actor, dests)
    recent = []
    if allowed:
        marks = ",".join("?" * len(allowed))
        for r in conn.execute(f"SELECT item_id, group_concat(destination) dests, max(created_at) at FROM intake_items "
                              f"WHERE destination IN ({marks}) AND created_at>=? GROUP BY item_id "
                              f"ORDER BY at DESC LIMIT ?", (*allowed, since, RECENT_MAX)):
            post = item(conn, r["item_id"])
            if not post:
                continue
            text = " ".join(str(post["content"] or "").split())
            recent.append({"item_id": post["id"], "source": post["source"], "url": post["url"],
                           "author": post["author"] or "", "published_at": post["published_at"],
                           "text": text if len(text) <= RECENT_TEXT else text[:RECENT_TEXT - 1] + "…",
                           "destinations": sorted(set(r["dests"].split(","))), "at": r["at"]})
    return {"daily": [{"day": d, "sources": days[d]} for d in sorted(days)], "recent": recent}


# ----------------------------------------------------------------------------- Decisions
def scores_from(answers, questions):
    return {qid: float(answers[qid]["noul"]) for qid, q in questions.items() if q.get("type") == "noul"}


def judge_pending(store, engine, who, limit=MAX_JUDGE, item_ids=None):
    """Score posts that have no judgment from the current set and route them. One decision call a post,
    each audited as a `judge.call` the way `/api/v2/decisions` audits, so the caller's budget counts it."""
    from .judge import audit_detail, daily_cap, used_today
    try:
        qset = question_set(store.settings)
    except J.JudgeError:
        raise Problem("judge_questions", "The listening-item question set could not be loaded. "
                      "Check registry/questions/listening-item.json.", 422) from None
    with store.read() as c:
        dests = destinations(store.settings, c, qset=qset)
    questions = qset["questions"]
    with store.read() as c:
        if item_ids:
            rows = [r for r in (item(c, i) for i in item_ids) if r]
            missing = [i for i in item_ids if not item(c, i)]
            if missing:
                raise Problem("not_found", f"no saved post {missing[0]}", 404)
        else:
            rows = unjudged(c, qset["label"], limit)
        used = used_today(c, who.actor)
    cap = daily_cap(store.settings, who.role)
    results, errors = [], []
    for row in rows[: int(limit)]:
        if used >= cap:
            errors.append({"item_id": row["id"], "error": "budget"})
            break
        try:
            answer = engine(state_for(row), questions, qset["label"])
        except J.JudgeError as exc:
            with store.transaction() as c:
                H.event(c, who.actor, "judge.call", qset["label"], {"questions": len(questions), "error": J.error_code(exc)})
            errors.append({"item_id": row["id"], "error": J.error_code(exc)})
            if exc.retryable or exc.code == "spend_cap":    # the rest stay unjudged for the next call
                break
            continue
        used += 1
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", qset["label"], {
                "questions": len(questions), "types": ["noul"], **audit_detail(answer),
                "answers": J.summary(answer["answers"])})
            results.append(add_judgment(c, who.actor, row["id"], qset["label"],
                                        scores_from(answer["answers"], questions), dests))
    return {"question_set": qset["label"], "judged": len(results),
            "routed": sum(len(r["intake"]) for r in results), "results": results, "errors": errors}


# ----------------------------------------------------------------------------- HTTP
class ListenItem(Contract):
    native_id: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    author: str | None = Field(default=None, max_length=300)
    author_url: str | None = Field(default=None, max_length=2000)
    content: str = Field(min_length=1, max_length=20_000)
    published_at: str | None = Field(default=None, max_length=40)


class ListenRun(Contract):
    source: str = Field(min_length=1, max_length=40)
    query: str = Field(min_length=1, max_length=2000)
    status: str = Field(min_length=1, max_length=20)
    started_at: str | None = Field(default=None, max_length=40)
    pages_read: int = Field(default=0, ge=0, le=10_000)
    items_seen: int | None = Field(default=None, ge=0, le=100_000)
    note: str = Field(default="", max_length=4000)
    items: list[ListenItem] = Field(default_factory=list, max_length=MAX_ITEMS)


class ListenJudgment(Contract):
    item_id: str = Field(min_length=1, max_length=80)
    question_set: str = Field(min_length=1, max_length=80)
    scores: dict[str, float] = Field(min_length=1, max_length=40)


class ListenJudgments(Contract):
    judgments: list[ListenJudgment] = Field(min_length=1, max_length=MAX_ITEMS)


class ListenJudge(Contract):
    limit: int = Field(default=20, ge=1, le=MAX_JUDGE)
    item_ids: list[str] = Field(default_factory=list, max_length=MAX_JUDGE)


class IntakeResolve(Contract):
    status: str = Field(min_length=1, max_length=20)
    receiver_ref: str = Field(default="", max_length=300)
    reason: str = Field(default="", max_length=2000)


class DestinationUnless(Contract):
    category: str = Field(min_length=1, max_length=80)
    threshold: float


class DestinationBody(Contract):
    category: str = Field(min_length=1, max_length=80)
    threshold: float
    receiver: str = Field(min_length=1, max_length=120)
    readers: list[str] = Field(default_factory=list, max_length=20)
    unless: DestinationUnless | None = None
    what: str = Field(default="", max_length=300)


def install(app, store, auth, mutate):
    def settings_reader(who):
        auth.domain(who)
        if who.role != "owner" and not auth.bot_admin(who):
            raise Problem("forbidden", "Listening destinations are for the owner and the admins", 403)

    @app.get("/api/v2/listening/destinations")
    def destination_index(request: Request):
        settings_reader(request.state.identity)
        with store.read() as c:
            return destination_list(c, store.settings)

    @app.put("/api/v2/listening/destinations/{key}")
    def destination_put(request: Request, key: str, body: DestinationBody):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if who.role != "owner":
                raise Problem("forbidden", "Only the owner changes Listening destinations", 403)
            saved = save_destination(c, store.settings, who, key, body)
            return {"destination": next(d for d in destination_list(c, store.settings)["destinations"] if d["key"] == saved)}

        return mutate(request, body, work)

    @app.post("/api/v2/listening/destinations/{key}/remove")
    def destination_remove(request: Request, key: str, body: Contract):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if who.role != "owner":
                raise Problem("forbidden", "Only the owner changes Listening destinations", 403)
            return {"removed": remove_destination(c, store.settings, who, key.strip().lower())}

        return mutate(request, body, work)

    @app.post("/api/v2/listening/runs")
    def run_create(request: Request, body: ListenRun):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            require_listener(who)
            return record_run(c, who.actor, source=body.source, query=body.query, status=body.status,
                              started_at=body.started_at, pages_read=body.pages_read, items_seen=body.items_seen,
                              note=body.note, items=[i.model_dump() for i in body.items])

        return mutate(request, body, work)

    @app.get("/api/v2/listening/runs")
    def run_list(request: Request, since: str | None = None, source: str | None = None, limit: int = 200):
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"runs": runs(c, since, source, limit)}

    @app.post("/api/v2/listening/judgments")
    def judgment_create(request: Request, body: ListenJudgments):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            require_listener(who)
            dests = destinations(store.settings, c)
            return {"results": [add_judgment(c, who.actor, j.item_id, j.question_set, j.scores, dests)
                                for j in body.judgments]}

        return mutate(request, body, work)

    @app.post("/api/v2/listening/judge")
    def judge_route(request: Request, body: ListenJudge):
        who = request.state.identity
        auth.domain(who)
        require_listener(who)
        from .judge import over_spend_cap, server_engine
        with store.read() as c:
            capped = over_spend_cap(c, store.settings)
            engine = None if capped else server_engine(store, c, getattr(app.state, "judge", None))
        if capped:
            raise Problem("spend_cap", "Decide has reached the team's monthly spend cap; posts stay unjudged "
                          "until the 1st (UTC)", 429)
        if engine is None:
            raise Problem("judge_unconfigured", "No decision model is configured on this server", 503)
        return judge_pending(store, engine, who, body.limit, body.item_ids or None)

    @app.get("/api/v2/listening/items/{item_id}")
    def item_trace(request: Request, item_id: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            dests = destinations(store.settings, c)
            require_reader(who, dests)
            found = trace(c, item_id)
        if not sees_everything(who):
            mine = set(destinations_of(who.actor, dests))
            if not any(row["destination"] in mine for row in found["intake"]):
                raise Problem("not_found", f"no saved post {item_id}", 404)
            found["intake"] = [row for row in found["intake"] if row["destination"] in mine]
        return found

    @app.get("/api/v2/listening/stats")
    def stats_route(request: Request, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            dests = destinations(store.settings, c)
            require_reader(who, dests)
            out = stats(c, since)
            out.update(timeline(c, who, dests, out["since"]))
            return out

    @app.get("/api/v2/intake")
    def intake_list(request: Request, destination: str | None = None, status: str | None = "new", limit: int = 100):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            dests = destinations(store.settings, c)
            require_reader(who, dests)
            return {"items": inbox(c, who, dests, destination, status or None, limit),
                    "destinations": dests}

    @app.post("/api/v2/intake/{intake_id}/resolve")
    def intake_resolve(request: Request, intake_id: str, body: IntakeResolve):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return {"intake": resolve(c, who, intake_id, body.status, destinations(store.settings, c), body.receiver_ref, body.reason)}

        return mutate(request, body, work)
