"""Read API over live Workspace Gmail or the existing copy, and live organizing for authorized callers.

People browse the mailboxes they hold a level on (backend/mailbox_access.py), the
owner and managers included, with no bypass. Reads refuse bots and runners; organizing uses each caller's own level.
Every thread or message body read is
audited as `mail.read`.

`GET /api/v2/mail/access` is the mail CLI's question (connectors/mail/access.py): what the caller, a person or a bot's
turn, may do on each mailbox it holds.
"""

import json
import re
import sqlite3

from fastapi import Request

from . import gmail_live, google_workspace, mailbox_access, routines
from .store import H, Problem
from .views import human_only, roster

FTS_TOKEN = re.compile(r"[A-Za-z0-9_@.+'-]+")
CURSOR = re.compile(r"^(\d+)\|([^|]+)\|([^|]+)$")
LIMIT_DEFAULT, LIMIT_MAX = 50, 100
DURATION = {"d": 86400, "h": 3600, "m": 60}
ORGANIZE_ACTIONS = {"archive": ((), ("INBOX",), "unarchive"),
                    "unarchive": (("INBOX",), (), "archive"),
                    "mark_read": ((), ("UNREAD",), "mark_unread"),
                    "mark_unread": (("UNREAD",), (), "mark_read"),
                    "label": ((), (), "unlabel"), "unlabel": ((), (), "label")}


def organize_selection(body):
    action, ids, query = body.get("action"), body.get("ids"), body.get("query")
    if not isinstance(action, str) or action not in ORGANIZE_ACTIONS:
        raise Problem("action", "Choose archive, unarchive, mark_read, mark_unread, label or unlabel", 422)
    if (ids is not None) == (query is not None):
        raise Problem("selection", "Choose either message ids or a Gmail query", 422)
    if ids is not None and (not isinstance(ids, list) or not 1 <= len(ids) <= 500
                            or any(not isinstance(mid, str) or not mid.strip() or not mid.isascii()
                                   or len(mid) > 128 for mid in ids)):
        raise Problem("ids", "Choose 1 to 500 message ids (up to 128 characters each)", 422)
    if query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 4096):
        raise Problem("query", "A Gmail query is required (up to 4096 characters)", 422)
    limit = body.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise Problem("limit", "limit must be between 1 and 500", 422)
    label = body.get("label")
    if action in ("label", "unlabel"):
        if not isinstance(label, str) or not label.strip() or len(label) > 225:
            raise Problem("label", "A label is required (up to 225 characters)", 422)
        label = label.strip()
    elif label is not None:
        raise Problem("label", "label is only used with label or unlabel", 422)
    return action, list(dict.fromkeys(ids)) if ids is not None else None, query, limit, label


def sanitize_fts(q):
    """Quoted FTS5 tokens ANDed together. Operators and punctuation never reach MATCH as syntax."""
    tokens = FTS_TOKEN.findall(q or "")
    if not tokens:
        return ""
    return " AND ".join('"' + token.replace('"', "") + '"' for token in tokens[:32])


def visible_addresses(c, who):
    """Mailbox addresses this person may browse, their own first: the ones they hold a level on."""
    if who.role not in ("owner", "human"):
        return []
    return [box["address"] for box in mailbox_access.mailboxes_for(c, who)]


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


def gmail_query(q, label, since_epoch, until_epoch, has_attachment):
    terms = [f"({q.strip()})"] if q.strip() else []
    if label:
        terms.append("label:" + json.dumps(label, ensure_ascii=False))
    if since_epoch is not None:
        terms.append(f"after:{since_epoch}")
    if until_epoch is not None:
        terms.append(f"before:{until_epoch}")
    if truthy(has_attachment):
        terms.append("has:attachment")
    return " ".join(terms)


def live_message_public(mailbox, item, domains, body=False):
    """Adapt the shared Gmail normalizer to the stored read API's public shape, only in memory."""
    row = {**item, "mailbox": mailbox, "msg_id": item["id"], "from_addr": item["from"],
           "to_addresses": json.dumps(item["to"]), "cc": json.dumps(item["cc"]),
           "labels": json.dumps(item["labels"]), "attachments": json.dumps(item["attachments"]),
           "has_unsubscribe": item["unsubscribe"], "rule_hits": "[]"}
    everyone = [item["from"], *item["to"], *item["cc"]]
    row["is_internal"] = bool(item["from"]) and all(
        any(address.rpartition("@")[2] == domain or address.rpartition("@")[2].endswith("." + domain)
            for domain in domains) for address in everyone)
    return message_public(row, body=body)


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
    @app.post("/api/v2/mail/messages/organize")
    def organize(request: Request, body: dict):
        who = request.state.identity
        addr = body.get("mailbox")
        if not isinstance(addr, str) or not addr.strip() or len(addr) > 254:
            raise Problem("mailbox", "mailbox is required", 422)
        addr = addr.strip().lower()

        def access(c):
            if not mailbox_access.may(c, who, addr)["organize"]:
                if mailbox_access.level(c, who, addr) == "read_write":
                    reason = f"Tico may not organize {addr}: it is off in Tools > Settings > Email > What Tico may do"
                else:
                    reason = f"You may not organize {addr} (Tools > {addr} > Who can use it)"
                raise Problem("forbidden", reason, 403)
            if google_workspace.authority(c, addr) != "workspace":
                raise Problem("mailbox", "Live organizing needs Whole Workspace", 422)
            if store.settings.rehearsal or store.settings.demo:
                raise Problem("rehearsal", "This install does not organize live email", 403)

        key, selection_operation = request.headers.get("idempotency-key"), request.url.path + ":selection"
        with store.read() as c:
            access(c)
            action, ids, query, limit, label = organize_selection(body)
            completed = store.saved(c, who, request.url.path, key)
            if ids is None and not completed and not store.saved(c, who, selection_operation, key):
                ids = gmail_live.message_ids(c, addr, query, limit)
        if completed:
            def expired(c):
                raise Problem("receipt_expired", "This organize receipt expired; retry the request", 409)
            return store.mutate(who, request.url.path, key, body, expired, check=access)
        # Retain only ids before writing Gmail. A retry of an archive query must not pick another page of mail.
        ids = store.mutate(who, selection_operation, key, body, lambda c: {"ids": ids}, check=access)["ids"]
        with store.read() as c:
            access(c)
            add, remove, inverse = ORGANIZE_ACTIONS[action]
            if label is not None and ids:
                label_id = gmail_live.resolve_label(c, addr, label, create=action == "label")
                add, remove = ((label_id,), ()) if action == "label" else ((), (label_id,))
            gmail_live.organize(c, addr, ids, add=add, remove=remove)
        detail = {"mailbox": addr, "action": action, "label": label, "count": len(ids), "ids": ids}
        label_fields = {"label": label} if label is not None else {}
        result = {"mailbox": addr, "action": action, "count": len(ids), "ids": ids, **label_fields,
                  "undo": {"action": inverse, "ids": ids, **label_fields}}

        def record(c):
            H.event(c, who.actor, "mail.organize", addr, detail)
            return result

        return store.mutate(who, request.url.path, key, body, record, check=access)

    @app.get("/api/v2/mail/access")
    def mail_access(request: Request, mailbox: str = ""):
        """What the caller may do: on every mailbox it holds, and on `mailbox` when named. A person (their Assistant
        included, which is them) or a bot's turn; a computer asks /api/v2/connectors/mail/targets instead."""
        who = request.state.identity
        if who.role not in ("owner", "human", "bot"):
            raise Problem("identity", "Mail access is answered for a person or a bot's turn", 403)
        with store.read() as c:
            principal = mailbox_access.principal(c, who)
            out = {"principal": principal.ref, "mailboxes": mailbox_access.mailboxes_for(c, principal),
                   "company": google_workspace.permissions(c)}
            addr = str(mailbox or "").strip().lower()
            if addr:
                out.update({"mailbox": addr, "level": mailbox_access.level(c, principal, addr),
                            "may": mailbox_access.may(c, principal, addr)})
            return out

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
        with store.read() as c:
            allowed = visible_addresses(c, who)
            if mailbox:
                addr, allowed = require_mailbox(c, who, mailbox)
                boxes = [addr]
            else:
                boxes = allowed
            if mailbox and google_workspace.authority(c, addr) == "workspace":
                query = gmail_query(q, label, since_epoch, until_epoch, has_attachment)
                page = gmail_live.list(c, addr, query, page_token=cursor, limit=min(cap, gmail_live.LIMIT_MAX))
                domains = mailbox_access.company_domains(c)
                return {**page, "messages": [live_message_public(addr, item, domains) for item in page["messages"]]}
            if not mailbox and any(google_workspace.authority(c, box) == "workspace" for box in boxes):
                raise Problem("mailbox", "Choose a mailbox to read live from Gmail", 422)
            after = decode_cursor(cursor) if cursor else None
            match = sanitize_fts(q)
            if str(q or "").strip() and not match:
                return {"messages": [], "next_cursor": None, "q": ""}
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
            if google_workspace.authority(c, addr) == "workspace":
                domains = mailbox_access.company_domains(c)
                items = [live_message_public(addr, item, domains, body=True)
                         for item in gmail_live.thread(c, addr, tid)]
            else:
                rows = c.execute(
                    "SELECT * FROM mail_messages WHERE mailbox=? AND thread_id=? AND deleted_at IS NULL "
                    "ORDER BY epoch, msg_id", (addr, tid)).fetchall()
                if not rows:
                    raise Problem("not_found", "Thread not found", 404)
                items = [message_public(row, body=True) for row in rows]
            out = {"mailbox": addr, "thread_id": tid, "messages": items}
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", tid, {"mailbox": addr, "kind": "thread", "messages": len(items)})
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
            if google_workspace.authority(c, addr) == "workspace":
                out = live_message_public(addr, gmail_live.message(c, addr, mid),
                                          mailbox_access.company_domains(c), body=True)
            else:
                row = c.execute("SELECT * FROM mail_messages WHERE mailbox=? AND msg_id=? AND deleted_at IS NULL",
                                (addr, mid)).fetchone()
                if not row:
                    raise Problem("not_found", "Message not found", 404)
                out = message_public(row, body=True)
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", mid, {"mailbox": addr, "kind": "message", "thread_id": out["thread_id"]})
        return out
