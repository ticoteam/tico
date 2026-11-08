"""Human read API over the server-stored mail copy.

People browse `mail_messages`, never live Gmail. The owner sees every mailbox; anyone else
sees their own address plus the people who report to them (`people.mailboxes_below`). Bots
and runners are refused. Every thread or message body read is audited as `mail.read`.
"""

import json
import re
import sqlite3

from fastapi import Request

from . import routines
from .store import H, P, Problem
from .views import human_only, roster

FTS_TOKEN = re.compile(r"[A-Za-z0-9_@.+'-]+")
CURSOR = re.compile(r"^(\d+)\|([^|]+)\|([^|]+)$")
LIMIT_DEFAULT, LIMIT_MAX = 50, 100
DURATION = {"d": 86400, "h": 3600, "m": 60}


def sanitize_fts(q):
    """Quoted FTS5 tokens ANDed together. Operators and punctuation never reach MATCH as syntax."""
    tokens = FTS_TOKEN.findall(q or "")
    if not tokens:
        return ""
    return " AND ".join('"' + token.replace('"', "") + '"' for token in tokens[:32])


def visible_addresses(c, who):
    """Mailbox addresses this person may browse, in a stable order."""
    people = roster(c)
    if who.role == "owner":
        seen, out = set(), []
        for person in people.get("people") or []:
            addr = str(person.get("email") or "").strip().lower()
            if addr and "@" in addr and addr not in seen:
                seen.add(addr)
                out.append(addr)
        for row in c.execute("SELECT address FROM mail_mailboxes ORDER BY address"):
            addr = str(row["address"] or "").strip().lower()
            if addr and addr not in seen:
                seen.add(addr)
                out.append(addr)
        return out
    if who.role != "human":
        return []
    boxes = routines.message_bot_mailboxes(c, people)
    addrs = list(dict.fromkeys(routines.mailbox_of(p, boxes) for p in P.below(H.actor_id(who.actor), people)
                               if "@" in routines.mailbox_of(p, boxes)))
    if addrs:
        return addrs
    email = str(who.email or "").strip().lower()
    return [email] if email and "@" in email else []


def can_access(c, who):
    return who.role in ("owner", "human") and bool(visible_addresses(c, who))


def require_mailbox(c, who, mailbox):
    addr = str(mailbox or "").strip().lower()
    allowed = visible_addresses(c, who)
    if not addr:
        raise Problem("mailbox", "mailbox is required", 422)
    if addr not in allowed:
        raise Problem("forbidden", "This mailbox is not visible", 403)
    return addr, allowed


def epoch_bound(value, name):
    if not value:
        return None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"(\d+)([dhm])", text)
    if match:
        ts = H.shift(H.now(), seconds=-int(match[1]) * DURATION[match[2]])
        return int(H.parse_ts(ts).timestamp())
    at = H.parse_ts(text)
    if not at:
        raise Problem("date", f"{name} must be an ISO date/time, a unix epoch, or a duration such as 7d", 422)
    return int(at.timestamp())


def decode_cursor(value):
    match = CURSOR.fullmatch(str(value or ""))
    if not match:
        raise Problem("cursor", "Invalid cursor", 422)
    return int(match[1]), match[2], match[3]


def encode_cursor(epoch, mailbox, msg_id):
    return f"{epoch}|{mailbox}|{msg_id}"


def truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes")


def message_public(row, body=False):
    attachments = json.loads(row["attachments"] or "[]")
    out = {
        "mailbox": row["mailbox"], "msg_id": row["msg_id"], "thread_id": row["thread_id"],
        "epoch": row["epoch"], "date": row["date"],
        "from_addr": row["from_addr"], "from_header": row["from_header"],
        "to": json.loads(row["to_addresses"] or "[]"), "cc": json.loads(row["cc"] or "[]"),
        "subject": row["subject"], "snippet": row["snippet"],
        "labels": json.loads(row["labels"] or "[]"),
        "has_attachment": bool(attachments), "attachment_count": len(attachments),
        "body_truncated": bool(row["body_truncated"]),
        "is_internal": bool(row["is_internal"]),
        "has_unsubscribe": bool(row["has_unsubscribe"]),
    }
    if body:
        out["body"] = row["body"]
        out["attachments"] = attachments
        out["rule_hits"] = json.loads(row["rule_hits"] or "[]")
        out["list_id"] = row["list_id"]
    return out


def mailbox_view(c, address, people, who, auth):
    row = c.execute("SELECT * FROM mail_mailboxes WHERE address=?", (address,)).fetchone()
    person = routines.person_for_mailbox(address, routines.message_bot_mailboxes(c, people), people)
    bot = (person or {}).get("inbox_bot") or ""
    bot_row = H.bot(c, bot) if bot else None
    bot = bot if bot_row and bot_row["state"] != "archived" and auth.bot_access(c, who, bot)["read"] else ""
    instructions = c.execute("SELECT updated_at,length(content) AS size FROM mail_agent_instructions "
                             "WHERE bot=?", (bot,)).fetchone() if bot else None
    return {
        "address": address,
        "person_id": (person or {}).get("id") or (row["person_id"] if row else None),
        "name": (person or {}).get("name") or "",
        "synced_at": row["synced_at"] if row else None,
        "message_count": row["message_count"] if row else 0,
        "oldest_epoch": row["oldest_epoch"] if row else None,
        "newest_epoch": row["newest_epoch"] if row else None,
        "error": row["error"] if row else None,
        "agent_bot": bot or None,
        "agent_instructions": bool(instructions and instructions["size"]),
        "agent_updated": instructions["updated_at"] if instructions and instructions["size"] else None,
    }


def install_mail(app, store, auth):
    @app.get("/api/v2/mail/mailboxes")
    def mailboxes(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            people = roster(c)
            return {"mailboxes": [mailbox_view(c, addr, people, who, auth)
                                  for addr in visible_addresses(c, who)]}

    @app.get("/api/v2/mail/agent")
    def agent_instructions(request: Request, mailbox: str = ""):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            people = roster(c)
            person = routines.person_for_mailbox(addr, routines.message_bot_mailboxes(c, people), people)
            bot = (person or {}).get("inbox_bot") or ""
            bot_row = H.bot(c, bot) if bot else None
            if not bot_row or bot_row["state"] == "archived" or not auth.bot_access(c, who, bot)["read"]:
                raise Problem("not_found", "This mailbox has no visible agent", 404)
            row = c.execute("SELECT content,updated_at FROM mail_agent_instructions WHERE bot=?",
                            (bot,)).fetchone()
            if not row or not row["content"].strip():
                raise Problem("not_found", "Agent instructions are not available yet", 404)
            return {"mailbox": addr, "bot": bot, "instructions": row["content"],
                    "updated_at": row["updated_at"]}

    @app.get("/api/v2/mail/messages")
    def messages(request: Request, mailbox: str = "", q: str = "", label: str = "",
                 since: str = "", until: str = "", has_attachment: str = "",
                 cursor: str = "", limit: int = LIMIT_DEFAULT):
        who = request.state.identity
        human_only(who)
        cap = min(LIMIT_MAX, max(1, int(limit or LIMIT_DEFAULT)))
        since_epoch, until_epoch = epoch_bound(since, "since"), epoch_bound(until, "until")
        after = decode_cursor(cursor) if cursor else None
        match = sanitize_fts(q)
        if str(q or "").strip() and not match:
            return {"messages": [], "next_cursor": None, "q": ""}
        with store.read() as c:
            allowed = visible_addresses(c, who)
            if mailbox:
                addr, allowed = require_mailbox(c, who, mailbox)
                boxes = [addr]
            else:
                boxes = allowed
            if not boxes:
                return {"messages": [], "next_cursor": None, "q": match}
            sql = ("SELECT m.mailbox,m.msg_id,m.thread_id,m.epoch,m.date,m.from_addr,m.from_header,"
                   "m.to_addresses,m.cc,m.subject,m.snippet,m.labels,m.attachments,"
                   "m.body_truncated,m.is_internal,m.has_unsubscribe "
                   "FROM mail_messages m WHERE m.deleted_at IS NULL AND m.mailbox IN ({})"
                   .format(",".join("?" * len(boxes))))
            args = list(boxes)
            if match:
                sql += (" AND (m.mailbox, m.msg_id) IN "
                        "(SELECT mailbox, msg_id FROM mail_fts WHERE mail_fts MATCH ?)")
                args.append(match)
            if label:
                sql += " AND EXISTS (SELECT 1 FROM json_each(m.labels) WHERE value=?)"
                args.append(label)
            if since_epoch is not None:
                sql += " AND m.epoch>=?"
                args.append(since_epoch)
            if until_epoch is not None:
                sql += " AND m.epoch<=?"
                args.append(until_epoch)
            if truthy(has_attachment):
                sql += " AND json_array_length(m.attachments)>0"
            if after:
                sql += " AND (m.epoch, m.mailbox, m.msg_id) < (?, ?, ?)"
                args.extend(after)
            sql += " ORDER BY m.epoch DESC, m.mailbox DESC, m.msg_id DESC LIMIT ?"
            args.append(cap + 1)
            try:
                rows = c.execute(sql, args).fetchall()
            except sqlite3.OperationalError as exc:
                raise Problem("query", "Search query was refused", 422) from exc
            more = rows[:cap]
            nxt = encode_cursor(more[-1]["epoch"], more[-1]["mailbox"], more[-1]["msg_id"]) if len(rows) > cap else None
            return {"messages": [message_public(row) for row in more], "next_cursor": nxt, "q": match}

    @app.get("/api/v2/mail/threads/{thread_id}")
    def thread(request: Request, thread_id: str, mailbox: str = ""):
        who = request.state.identity
        human_only(who)
        tid = str(thread_id or "").strip()
        if not tid:
            raise Problem("thread", "thread_id is required", 422)
        # The thread is read outside the write lock; the audit event is written, in a short transaction, before
        # anything is returned.
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            rows = c.execute(
                "SELECT * FROM mail_messages WHERE mailbox=? AND thread_id=? AND deleted_at IS NULL "
                "ORDER BY epoch, msg_id", (addr, tid)).fetchall()
            if not rows:
                raise Problem("not_found", "Thread not found", 404)
            out = {"mailbox": addr, "thread_id": tid, "messages": [message_public(row, body=True) for row in rows]}
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", tid, {"mailbox": addr, "kind": "thread", "messages": len(rows)})
        return out

    @app.get("/api/v2/mail/messages/{msg_id}")
    def message(request: Request, msg_id: str, mailbox: str = ""):
        who = request.state.identity
        human_only(who)
        mid = str(msg_id or "").strip()
        if not mid:
            raise Problem("message", "msg_id is required", 422)
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            row = c.execute("SELECT * FROM mail_messages WHERE mailbox=? AND msg_id=? AND deleted_at IS NULL",
                            (addr, mid)).fetchone()
            if not row:
                raise Problem("not_found", "Message not found", 404)
            out = message_public(row, body=True)
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", mid, {"mailbox": addr, "kind": "message", "thread_id": row["thread_id"]})
        return out
