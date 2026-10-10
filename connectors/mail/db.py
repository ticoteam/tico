"""All mail state in one SQLite file: <projects>/runtime/mail/mail.db (WAL).

  seen        one row per message this employee has been shown, and when it was triaged
  watermarks  per mailbox and employee: how far a run has already looked
  rule_hits   every rule that fired, so a wrong archive is one query away
  audit       the same lines as audit.jsonl, queryable
  drafts      one row per idempotency key, so a retried `draft` updates its own Gmail draft
  sends       one row per send, written *before* the API call; also where `sent-log --reconcile`
              puts the owner's own sends, so the cooldowns count them
  reviews     every second-model verdict, with the backend and the draft it judged
  messages    every normalized message a sync (or an opportunistic fetch) has stored,
              so the Mac can push a batch later without holding a hub token
  sync_state  per mailbox: Gmail history cursor, last run, last error, live count

Nothing here talks to Google. Tests point it at a temp file.
"""

import json, sqlite3, time
from pathlib import Path

from . import DB_PATH, stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  mailbox    TEXT NOT NULL,
  employee   TEXT NOT NULL,
  msg_id     TEXT NOT NULL,
  thread_id  TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  triaged_at TEXT,
  PRIMARY KEY (mailbox, employee, msg_id)
);
CREATE TABLE IF NOT EXISTS watermarks (
  mailbox    TEXT NOT NULL,
  employee   TEXT NOT NULL,
  history_id TEXT NOT NULL DEFAULT '',
  updated    TEXT NOT NULL,
  PRIMARY KEY (mailbox, employee)
);
CREATE TABLE IF NOT EXISTS rule_hits (
  ts       TEXT NOT NULL,
  mailbox  TEXT NOT NULL,
  employee TEXT NOT NULL,
  msg_id   TEXT NOT NULL,
  rule_id  TEXT NOT NULL,
  actions  TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS audit (
  ts       TEXT NOT NULL,
  employee TEXT NOT NULL,
  issue    TEXT NOT NULL DEFAULT '',
  mailbox  TEXT NOT NULL DEFAULT '',
  action   TEXT NOT NULL,
  target   TEXT NOT NULL DEFAULT '',
  detail   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS drafts (
  key        TEXT PRIMARY KEY,
  ts         TEXT NOT NULL,
  updated    TEXT NOT NULL DEFAULT '',
  employee   TEXT NOT NULL,
  mailbox    TEXT NOT NULL,
  issue      TEXT NOT NULL DEFAULT '',
  recipients TEXT NOT NULL DEFAULT '',
  subject    TEXT NOT NULL DEFAULT '',
  thread_id  TEXT NOT NULL DEFAULT '',
  draft_id   TEXT NOT NULL DEFAULT '',
  message_id TEXT NOT NULL DEFAULT '',
  review_id  INTEGER
);
CREATE TABLE IF NOT EXISTS sends (
  key        TEXT PRIMARY KEY,
  ts         TEXT NOT NULL,
  employee   TEXT NOT NULL,
  mailbox    TEXT NOT NULL,
  issue      TEXT NOT NULL DEFAULT '',
  recipient  TEXT NOT NULL DEFAULT '',
  recipients TEXT NOT NULL DEFAULT '',
  subject    TEXT NOT NULL DEFAULT '',
  thread_id  TEXT NOT NULL DEFAULT '',
  draft_id   TEXT NOT NULL DEFAULT '',
  message_id TEXT NOT NULL DEFAULT '',
  day        TEXT NOT NULL DEFAULT '',
  source     TEXT NOT NULL DEFAULT 'mail',
  status     TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS reviews (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  employee  TEXT NOT NULL,
  mailbox   TEXT NOT NULL DEFAULT '',
  target    TEXT NOT NULL DEFAULT '',
  backend   TEXT NOT NULL DEFAULT '',
  ok        INTEGER NOT NULL DEFAULT 0,
  available INTEGER NOT NULL DEFAULT 1,
  verdict   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
  mailbox          TEXT NOT NULL,
  msg_id           TEXT NOT NULL,
  thread_id        TEXT NOT NULL DEFAULT '',
  epoch            INTEGER NOT NULL DEFAULT 0,
  date             TEXT NOT NULL DEFAULT '',
  from_addr        TEXT NOT NULL DEFAULT '',
  from_header      TEXT NOT NULL DEFAULT '',
  to_json          TEXT NOT NULL DEFAULT '[]',
  cc_json          TEXT NOT NULL DEFAULT '[]',
  subject          TEXT NOT NULL DEFAULT '',
  snippet          TEXT NOT NULL DEFAULT '',
  labels_json      TEXT NOT NULL DEFAULT '[]',
  body             TEXT NOT NULL DEFAULT '',
  body_truncated   INTEGER NOT NULL DEFAULT 0,
  attachments_json TEXT NOT NULL DEFAULT '[]',
  list_id          TEXT NOT NULL DEFAULT '',
  is_internal      INTEGER NOT NULL DEFAULT 0,
  has_unsubscribe  INTEGER NOT NULL DEFAULT 0,
  fetched_at       TEXT NOT NULL DEFAULT '',
  updated_at       TEXT NOT NULL DEFAULT '',
  deleted_at       TEXT,
  pushed_at        TEXT,
  PRIMARY KEY (mailbox, msg_id)
);
CREATE TABLE IF NOT EXISTS sync_state (
  mailbox       TEXT PRIMARY KEY,
  history_id    TEXT NOT NULL DEFAULT '',
  last_full_at  TEXT NOT NULL DEFAULT '',
  last_run_at   TEXT NOT NULL DEFAULT '',
  last_error    TEXT NOT NULL DEFAULT '',
  message_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS judge_cache (
  cache_key   TEXT PRIMARY KEY,
  expires_at  INTEGER NOT NULL,
  answers     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS rule_hits_ts ON rule_hits(ts);
CREATE INDEX IF NOT EXISTS sends_day ON sends(employee, mailbox, day);
CREATE INDEX IF NOT EXISTS sends_recipient ON sends(mailbox, recipient);
CREATE INDEX IF NOT EXISTS messages_push ON messages(pushed_at, mailbox);
CREATE INDEX IF NOT EXISTS messages_epoch ON messages(mailbox, epoch);
CREATE INDEX IF NOT EXISTS judge_cache_expiry ON judge_cache(expires_at);
"""


def connect(path=None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_judgment(conn, cache_key):
    row = conn.execute("SELECT answers FROM judge_cache WHERE cache_key=? AND expires_at>?",
                       (cache_key, int(time.time()))).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["answers"])
    except ValueError:
        return None


def put_judgment(conn, cache_key, answers, ttl=7 * 86400):
    """Cache only successful answers. The key hashes the message state and set definition."""
    now = int(time.time())
    conn.execute("INSERT OR REPLACE INTO judge_cache(cache_key, expires_at, answers) VALUES (?,?,?)",
                 (cache_key, now + ttl, json.dumps(answers, sort_keys=True)))
    conn.execute("DELETE FROM judge_cache WHERE expires_at<=?", (now,))
    conn.commit()


# ---------------------------------------------------------------- seen

def seen_ids(conn, mailbox, employee):
    rows = conn.execute("SELECT msg_id FROM seen WHERE mailbox=? AND employee=?",
                        (mailbox, employee))
    return {r["msg_id"] for r in rows}


def mark_seen(conn, mailbox, employee, msg_id, thread_id="", when=None):
    conn.execute(
        "INSERT OR IGNORE INTO seen (mailbox, employee, msg_id, thread_id, first_seen) "
        "VALUES (?,?,?,?,?)", (mailbox, employee, msg_id, thread_id or "", when or stamp()))
    conn.commit()


def mark_triaged(conn, mailbox, employee, msg_id, thread_id="", when=None):
    when = when or stamp()
    mark_seen(conn, mailbox, employee, msg_id, thread_id, when)
    conn.execute("UPDATE seen SET triaged_at=? WHERE mailbox=? AND employee=? AND msg_id=?",
                 (when, mailbox, employee, msg_id))
    conn.commit()


# ---------------------------------------------------------------- watermarks

def get_watermark(conn, mailbox, employee):
    r = conn.execute("SELECT history_id, updated FROM watermarks WHERE mailbox=? AND employee=?",
                     (mailbox, employee)).fetchone()
    return (r["history_id"], r["updated"]) if r else ("", "")


def set_watermark(conn, mailbox, employee, history_id="", when=None):
    conn.execute(
        "INSERT INTO watermarks (mailbox, employee, history_id, updated) VALUES (?,?,?,?) "
        "ON CONFLICT(mailbox, employee) DO UPDATE SET history_id=excluded.history_id, "
        "updated=excluded.updated",
        (mailbox, employee, str(history_id or ""), when or stamp()))
    conn.commit()


# ---------------------------------------------------------------- rule hits and audit

def record_rule_hit(conn, mailbox, employee, msg_id, rule_id, actions, when=None):
    conn.execute("INSERT INTO rule_hits (ts, mailbox, employee, msg_id, rule_id, actions) "
                 "VALUES (?,?,?,?,?,?)",
                 (when or stamp(), mailbox, employee, msg_id, rule_id,
                  json.dumps(actions, sort_keys=True)))
    conn.commit()


def record_audit(conn, row):
    conn.execute("INSERT INTO audit (ts, employee, issue, mailbox, action, target, detail) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (row["ts"], row["employee"], row.get("issue", "") or "", row.get("mailbox", ""),
                  row["action"], row.get("target", ""),
                  json.dumps(row.get("detail") or {}, sort_keys=True)))
    conn.commit()


# ---------------------------------------------------------------- drafts

def get_draft(conn, key):
    r = conn.execute("SELECT * FROM drafts WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def put_draft(conn, key, employee, mailbox, issue, recipients, subject, thread_id="",
              draft_id="", message_id="", review_id=None, when=None):
    """Insert or update the row for this idempotency key. Returns (row, created)."""
    when = when or stamp()
    existing = get_draft(conn, key)
    if existing:
        conn.execute("UPDATE drafts SET updated=?, thread_id=?, draft_id=?, message_id=?, "
                     "review_id=?, subject=?, recipients=?, issue=? WHERE key=?",
                     (when, thread_id or existing["thread_id"], draft_id or existing["draft_id"],
                      message_id or existing["message_id"], review_id, subject,
                      ",".join(recipients), str(issue or ""), key))
    else:
        conn.execute("INSERT INTO drafts (key, ts, updated, employee, mailbox, issue, "
                     "recipients, subject, thread_id, draft_id, message_id, review_id) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (key, when, when, employee, mailbox, str(issue or ""),
                      ",".join(recipients), subject, thread_id, draft_id, message_id, review_id))
    conn.commit()
    return get_draft(conn, key), not existing


def draft_by_gmail_id(conn, mailbox, draft_id):
    r = conn.execute("SELECT * FROM drafts WHERE mailbox=? AND draft_id=? ORDER BY updated DESC",
                     (mailbox, draft_id)).fetchone()
    return dict(r) if r else None


def drop_draft(conn, mailbox, draft_id):
    """Forget a discarded draft, so a retried `draft` makes a new one instead of updating it."""
    conn.execute("DELETE FROM drafts WHERE mailbox=? AND draft_id=?", (mailbox, draft_id))
    conn.commit()


def draft_was_sent(conn, mailbox, draft_id):
    return conn.execute("SELECT 1 FROM sends WHERE mailbox=? AND draft_id=? AND status='sent'",
                        (mailbox, draft_id)).fetchone() is not None


def draft_thread_ids(conn, mailbox):
    """Threads in this mailbox that a hub employee has drafted a reply on."""
    rows = conn.execute("SELECT DISTINCT thread_id FROM drafts WHERE mailbox=? AND thread_id!=''",
                        (mailbox,))
    return {r["thread_id"] for r in rows}


def scheduling_thread_ids(conn, mailbox):
    """Threads with a scheduling offer or booking. The table is scheduling.py's, made on first
    use, so it may not exist yet; that is an empty set, not an error."""
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='scheduling_actions'").fetchone():
        return set()
    rows = conn.execute("SELECT DISTINCT thread FROM scheduling_actions WHERE mailbox=? "
                        "AND thread!=''", (mailbox,))
    return {r["thread"] for r in rows}


# ---------------------------------------------------------------- sends

def get_send(conn, key):
    r = conn.execute("SELECT * FROM sends WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def claim_send(conn, key, employee, mailbox, issue, recipients, subject, thread_id="",
               draft_id="", day="", source="mail", when=None):
    """Write the row before the API call. (row, claimed): claimed=False means already sent."""
    existing = get_send(conn, key)
    if existing:
        return existing, False
    conn.execute("INSERT INTO sends (key, ts, employee, mailbox, issue, recipient, recipients, "
                 "subject, thread_id, draft_id, day, source, status) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (key, when or stamp(), employee, mailbox, str(issue or ""),
                  (recipients or [""])[0], ",".join(recipients or []), subject, thread_id,
                  draft_id, day, source, "pending"))
    conn.commit()
    return get_send(conn, key), True


def finish_send(conn, key, message_id="", status="sent"):
    conn.execute("UPDATE sends SET message_id=?, status=? WHERE key=?",
                 (message_id or "", status, key))
    conn.commit()
    return get_send(conn, key)


def drop_send(conn, key):
    """Undo a claim that never became a send, so the key is free to try again."""
    conn.execute("DELETE FROM sends WHERE key=?", (key,))
    conn.commit()


def sends_on_day(conn, employee, mailbox, day):
    r = conn.execute("SELECT count(*) c FROM sends WHERE employee=? AND mailbox=? AND day=? "
                     "AND status!='failed'", (employee, mailbox, day)).fetchone()
    return int(r["c"] if r else 0)


def last_send_to(conn, mailbox, recipient):
    """When anyone last wrote to this address from this mailbox, the bot or the owner in person."""
    r = conn.execute("SELECT max(ts) t FROM sends WHERE mailbox=? AND recipient=? "
                     "AND status!='failed'", (mailbox, str(recipient).lower())).fetchone()
    return (r["t"] or "") if r else ""


def send_message_ids(conn, mailbox):
    """Gmail message ids already in the log, so a reconcile does not duplicate our own sends."""
    rows = conn.execute("SELECT message_id FROM sends WHERE mailbox=? AND message_id!=''",
                        (mailbox,))
    return {r["message_id"] for r in rows}


def send_rows(conn, employee=None, mailbox=None, since=None, limit=500):
    sql, args = "SELECT * FROM sends WHERE 1=1", []
    if employee:
        sql, args = sql + " AND employee=?", args + [employee]
    if mailbox:
        sql, args = sql + " AND mailbox=?", args + [mailbox]
    if since:
        sql, args = sql + " AND ts>=?", args + [since]
    sql += " ORDER BY ts DESC LIMIT ?"
    return [dict(r) for r in conn.execute(sql, args + [int(limit)]).fetchall()]


# ---------------------------------------------------------------- reviews

def record_review(conn, employee, mailbox, target, verdict, when=None):
    cur = conn.execute("INSERT INTO reviews (ts, employee, mailbox, target, backend, ok, "
                       "available, verdict) VALUES (?,?,?,?,?,?,?,?)",
                       (when or stamp(), employee, mailbox, target,
                        verdict.get("backend", ""), 1 if verdict.get("ok") else 0,
                        1 if verdict.get("available") else 0,
                        json.dumps(verdict, sort_keys=True, default=str)))
    conn.commit()
    return cur.lastrowid


def get_review(conn, review_id):
    r = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["verdict"] = json.loads(d["verdict"])
    except ValueError:                                  # pragma: no cover
        d["verdict"] = {}
    return d


def audit_rows(conn, since=None, employee=None, limit=500):
    sql, args = "SELECT * FROM audit WHERE 1=1", []
    if since:
        sql, args = sql + " AND ts >= ?", args + [since]
    if employee:
        sql, args = sql + " AND employee = ?", args + [employee]
    sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
    rows = conn.execute(sql, args + [int(limit)]).fetchall()
    out = []
    for r in reversed(rows):
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"])
        except Exception:
            d["detail"] = {}
        out.append(d)
    return out


# ---------------------------------------------------------------- persisted messages (inbox bots)

def _json_list(value):
    return json.dumps(value or [], sort_keys=True)


def _message_values(mailbox, msg):
    """Normalized Gmail message -> the columns we persist (no timestamps)."""
    return {
        "mailbox": mailbox,
        "msg_id": msg.get("id") or msg.get("msg_id") or "",
        "thread_id": msg.get("thread_id") or "",
        "epoch": int(msg.get("epoch") or 0),
        "date": msg.get("date") or "",
        "from_addr": msg.get("from") or msg.get("from_addr") or "",
        "from_header": msg.get("from_header") or "",
        "to_json": _json_list(msg.get("to")),
        "cc_json": _json_list(msg.get("cc")),
        "subject": msg.get("subject") or "",
        "snippet": msg.get("snippet") or "",
        "labels_json": _json_list(msg.get("labels")),
        "body": msg.get("body") or "",
        "body_truncated": 1 if msg.get("body_truncated") else 0,
        "attachments_json": _json_list(msg.get("attachments")),
        "list_id": msg.get("list_id") or "",
        "is_internal": 1 if msg.get("is_internal") else 0,
        "has_unsubscribe": 1 if (msg.get("unsubscribe") or msg.get("has_unsubscribe")) else 0,
    }


def _same_content(row, values):
    return all(str(row[k] if row[k] is not None else "") == str(values[k])
               for k in values if k not in ("mailbox", "msg_id"))


def _decode_message(row):
    d = dict(row)
    for key, field in (("to_json", "to"), ("cc_json", "cc"),
                       ("labels_json", "labels"), ("attachments_json", "attachments")):
        try:
            d[field] = json.loads(d.pop(key) or "[]")
        except ValueError:
            d[field] = []
    d["body_truncated"] = bool(d.get("body_truncated"))
    d["is_internal"] = bool(d.get("is_internal"))
    d["has_unsubscribe"] = bool(d.get("has_unsubscribe"))
    d["id"] = d.get("msg_id")
    return d


def get_message(conn, mailbox, msg_id):
    r = conn.execute("SELECT * FROM messages WHERE mailbox=? AND msg_id=?",
                     (mailbox, msg_id)).fetchone()
    return _decode_message(r) if r else None


def upsert_message(conn, mailbox, msg, when=None):
    """Insert or update a normalized message. Returns True when a row was created.

    An unchanged row is left alone (pushed_at stays). A change, or a previously
    deleted message coming back, clears deleted_at and pushed_at so the next
    export picks it up.
    """
    when = when or stamp()
    values = _message_values(mailbox, msg)
    if not values["msg_id"]:
        return False
    existing = conn.execute("SELECT * FROM messages WHERE mailbox=? AND msg_id=?",
                            (mailbox, values["msg_id"])).fetchone()
    cols = ("thread_id", "epoch", "date", "from_addr", "from_header", "to_json",
            "cc_json", "subject", "snippet", "labels_json", "body", "body_truncated",
            "attachments_json", "list_id", "is_internal", "has_unsubscribe")
    args = [values[c] for c in cols]
    if existing:
        if _same_content(existing, values) and not existing["deleted_at"]:
            return False
        conn.execute(
            "UPDATE messages SET thread_id=?, epoch=?, date=?, from_addr=?, from_header=?, "
            "to_json=?, cc_json=?, subject=?, snippet=?, labels_json=?, body=?, "
            "body_truncated=?, attachments_json=?, list_id=?, is_internal=?, "
            "has_unsubscribe=?, updated_at=?, deleted_at=NULL, pushed_at=NULL "
            "WHERE mailbox=? AND msg_id=?",
            args + [when, mailbox, values["msg_id"]])
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO messages (mailbox, msg_id, thread_id, epoch, date, from_addr, "
        "from_header, to_json, cc_json, subject, snippet, labels_json, body, "
        "body_truncated, attachments_json, list_id, is_internal, has_unsubscribe, "
        "fetched_at, updated_at, deleted_at, pushed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
        [mailbox, values["msg_id"]] + args + [when, when])
    conn.commit()
    return True


def mark_deleted(conn, mailbox, msg_id, when=None):
    """Tombstone a message. Clears pushed_at so the delete is exported."""
    when = when or stamp()
    cur = conn.execute("UPDATE messages SET deleted_at=?, pushed_at=NULL, updated_at=? "
                       "WHERE mailbox=? AND msg_id=?", (when, when, mailbox, msg_id))
    if cur.rowcount == 0:
        conn.execute(
            "INSERT OR IGNORE INTO messages (mailbox, msg_id, deleted_at, fetched_at, "
            "updated_at, pushed_at) VALUES (?,?,?,?,?,NULL)",
            (mailbox, msg_id, when, when, when))
    conn.commit()


def unpushed(conn, mailbox=None, mailboxes=None, limit=500):
    """Rows waiting to be pushed: never pushed, or changed since the last ack."""
    sql, args = "SELECT * FROM messages WHERE pushed_at IS NULL", []
    if mailbox:
        sql, args = sql + " AND mailbox=?", args + [mailbox]
    elif mailboxes is not None:
        if not mailboxes:
            return []
        sql += " AND mailbox IN (%s)" % ",".join("?" * len(mailboxes))
        args = args + list(mailboxes)
    sql += " ORDER BY mailbox, epoch, msg_id LIMIT ?"
    return [_decode_message(r) for r in conn.execute(sql, args + [int(limit or 500)]).fetchall()]


def mark_pushed(conn, mailbox, msg_ids, when=None):
    """Ack one or more message ids as received by the (future) server push."""
    if isinstance(msg_ids, str):
        msg_ids = [msg_ids]
    when = when or stamp()
    for mid in msg_ids:
        conn.execute("UPDATE messages SET pushed_at=? WHERE mailbox=? AND msg_id=?",
                     (when, mailbox, mid))
    conn.commit()


def message_count(conn, mailbox):
    r = conn.execute("SELECT count(*) c FROM messages WHERE mailbox=? AND deleted_at IS NULL",
                     (mailbox,)).fetchone()
    return int(r["c"] if r else 0)


def get_sync_state(conn, mailbox):
    r = conn.execute("SELECT * FROM sync_state WHERE mailbox=?", (mailbox,)).fetchone()
    return dict(r) if r else None


def set_sync_state(conn, mailbox, history_id=None, last_full_at=None, last_run_at=None,
                   last_error=None, message_count=None):
    """Insert or update the per-mailbox sync cursor. None keeps the current value."""
    cur = get_sync_state(conn, mailbox) or {}
    values = (
        history_id if history_id is not None else cur.get("history_id") or "",
        last_full_at if last_full_at is not None else cur.get("last_full_at") or "",
        last_run_at if last_run_at is not None else cur.get("last_run_at") or "",
        last_error if last_error is not None else cur.get("last_error") or "",
        int(message_count if message_count is not None else cur.get("message_count") or 0),
    )
    conn.execute(
        "INSERT INTO sync_state (mailbox, history_id, last_full_at, last_run_at, "
        "last_error, message_count) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(mailbox) DO UPDATE SET history_id=excluded.history_id, "
        "last_full_at=excluded.last_full_at, last_run_at=excluded.last_run_at, "
        "last_error=excluded.last_error, message_count=excluded.message_count",
        (mailbox,) + values)
    conn.commit()
    return get_sync_state(conn, mailbox)
