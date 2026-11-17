"""Read API over live Gmail (Tico keeps no copy of mail), and live organizing, drafting and unsubscribing.

People and bots use the mailboxes they hold a level on (backend/mailbox_access.py), the owner and managers included,
with no bypass; a person's Assistant is that person. Runners are refused. Every thread, message body or attachment
read is audited as `mail.read`. A draft (`POST /api/v2/mail/drafts`) is written to Gmail under a Message-ID Tico
chose, so the same draft asked for again is found, not made twice (backend/mail_compose.py); a bot's own draft meets
the lint first, and the second reviewer reads it. Sending a draft is the send gate's (`POST /api/v2/email/sends` with
its `draft_id`). `tico email ...` (clients/commands/mail.py) is a thin call to each route.

`GET /api/v2/mail/access` is the mail CLI's question (connectors/mail/access.py): what the caller, a person or a bot's
turn, may do on each mailbox it holds.
"""

import base64
import json
import re
from types import SimpleNamespace

from fastapi import Request
from pydantic import Field

from . import gmail_live, google_tokens, google_workspace, mail_compose, mailbox_access, routines
from .models import Contract
from .store import H, Problem
from .views import human_only, roster
from .api_contract import api_v2, error, ref

LIMIT_DEFAULT, LIMIT_MAX = 50, 100
DURATION = {"d": 86400, "h": 3600, "m": 60}
ORGANIZE_ACTIONS = {
    "archive": ((), ("INBOX",), "unarchive"),
    "unarchive": (("INBOX",), (), "archive"),
    "mark_read": ((), ("UNREAD",), "mark_unread"),
    "mark_unread": (("UNREAD",), (), "mark_read"),
    "label": ((), (), "unlabel"),
    "unlabel": ((), (), "label"),
}


def organize_selection(body):
    action, ids, query = body.get("action"), body.get("ids"), body.get("query")
    if not isinstance(action, str) or action not in ORGANIZE_ACTIONS:
        raise Problem("action", "Choose archive, unarchive, mark_read, mark_unread, label or unlabel", 422)
    if (ids is not None) == (query is not None):
        raise Problem("selection", "Choose either message ids or a Gmail query", 422)
    if ids is not None and (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 500
        or any(not isinstance(mid, str) or not mid.strip() or not mid.isascii() or len(mid) > 128 for mid in ids)
    ):
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


def visible_addresses(c, who):
    """Mailbox addresses this person may browse, their own first: the ones they hold a level on."""
    if who.role not in ("owner", "human"):
        return []
    return [box["address"] for box in mailbox_access.mailboxes_for(c, who)]


def reader(who):
    """A person (their Assistant included) or a bot's turn: what the mail routes answer."""
    if who.role not in ("owner", "human", "bot"):
        raise Problem("identity", "Mail is read by a person or a bot's turn", 403)


def usable_addresses(c, who):
    """The mailboxes a person or a bot holds a level on, their own first."""
    if who.role not in ("owner", "human", "bot"):
        return []
    return [box["address"] for box in mailbox_access.mailboxes_for(c, who)]


def can_access(c, who):
    return who.role in ("owner", "human") and bool(visible_addresses(c, who))


def require_mailbox(c, who, mailbox):
    addr = str(mailbox or "").strip().lower()
    allowed = usable_addresses(c, who)
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


def truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes")


def message_public(row, body=False):
    attachments = json.loads(row["attachments"] or "[]")
    out = {
        "mailbox": row["mailbox"],
        "msg_id": row["msg_id"],
        "thread_id": row["thread_id"],
        "epoch": row["epoch"],
        "date": row["date"],
        "from_addr": row["from_addr"],
        "from_header": row["from_header"],
        "to": json.loads(row["to_addresses"] or "[]"),
        "cc": json.loads(row["cc"] or "[]"),
        "subject": row["subject"],
        "snippet": row["snippet"],
        "labels": json.loads(row["labels"] or "[]"),
        "has_attachment": bool(attachments),
        "attachment_count": len(attachments),
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
    row = {
        **item,
        "mailbox": mailbox,
        "msg_id": item["id"],
        "from_addr": item["from"],
        "to_addresses": json.dumps(item["to"]),
        "cc": json.dumps(item["cc"]),
        "labels": json.dumps(item["labels"]),
        "attachments": json.dumps(item["attachments"]),
        "has_unsubscribe": item["unsubscribe"],
        "rule_hits": "[]",
    }
    everyone = [item["from"], *item["to"], *item["cc"]]
    row["is_internal"] = bool(item["from"]) and all(
        any(
            address.rpartition("@")[2] == domain or address.rpartition("@")[2].endswith("." + domain)
            for domain in domains
        )
        for address in everyone
    )
    return message_public(row, body=body)


CONNECTIONS = {"workspace": "workspace", "personal": "personal"}  # google_workspace.authority: anything else is none


def mailbox_view(c, address, people, who, auth, box=None):
    row = c.execute("SELECT person_id,checked_at,check_error FROM mailboxes WHERE address=?", (address,)).fetchone()
    person = routines.person_for_mailbox(address, routines.message_bot_mailboxes(c, people), people)
    bot = (person or {}).get("inbox_bot") or ""
    bot_row = H.bot(c, bot) if bot and who.role != "bot" else None
    bot = bot if bot_row and bot_row["state"] != "archived" and auth.bot_access(c, who, bot)["read"] else ""
    instructions = (
        c.execute(
            "SELECT updated_at,length(content) AS size FROM mail_agent_instructions WHERE bot=?", (bot,)
        ).fetchone()
        if bot
        else None
    )
    return {
        "address": address,
        "person_id": (person or {}).get("id") or (row["person_id"] if row else None),
        "name": (person or {}).get("name") or "",
        # When the server last read its Gmail (backend/gmail_watch.py), and why the last read failed.
        "synced_at": row["checked_at"] if row else None,
        "message_count": 0,
        "oldest_epoch": None,
        "newest_epoch": None,
        "error": row["check_error"] if row else None,
        "agent_bot": bot or None,
        "agent_instructions": bool(instructions and instructions["size"]),
        "agent_updated": instructions["updated_at"] if instructions and instructions["size"] else None,
        # What the caller may do here, and which credential the server reads it with (none: nothing can).
        "level": (box or {}).get("level") or mailbox_access.level(c, who, address),
        "may": (box or {}).get("may") or mailbox_access.may(c, who, address),
        "connection": CONNECTIONS.get(google_workspace.authority(c, address), "none"),
    }


class DraftBody(Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=900)
    body: str = Field(min_length=1, max_length=mail_compose.BODY_MAX)
    reply_to: str = Field(default="", max_length=200)  # a thread id: the reply answers its newest message from others
    operation_id: str = Field(default="", max_length=200)


class LintBody(Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=900)
    body: str = Field(min_length=1, max_length=mail_compose.BODY_MAX)
    reply_to: str = Field(default="", max_length=200)


class MailboxBody(Contract):
    mailbox: str = Field(min_length=3, max_length=320)


def _no_live_changes(store, what):
    if store.settings.rehearsal or store.settings.demo:
        raise Problem("rehearsal", f"This install does not {what} live email", 403)


def _refused(c, who, addr, need):
    """403 unless `who` may `need` (draft, organize, send) on `addr`, naming the switch or the access that stops it."""
    if mailbox_access.may(c, who, addr).get(need):
        return
    words = {"draft": "write drafts in", "organize": "organize", "send": "send from"}[need]
    if mailbox_access.level(c, who, addr) == "read_write":
        raise Problem(
            "forbidden", f"Tico may not {words} {addr}: it is off in Tools > Settings > Email > What Tico may do", 403
        )
    raise Problem("forbidden", f"You may not {words} {addr} (Tools > {addr} > Who can use it)", 403)


def compose(c, who, addr, body, mid):
    """(raw, thread id, recipients, subject, the message it answers or None) for a draft or a lint."""
    original = None
    if body.reply_to:
        original = mail_compose.answered_in(addr, gmail_live.thread(c, addr, body.reply_to))
    if original:
        raw, thread, to, subject = mail_compose.reply(addr, original, body.body, mid, to=body.to, cc=body.cc)
        return raw, thread, to, subject, original
    to = mail_compose.addresses(body.to)
    if not to:
        raise Problem("to", "A new email needs a recipient: --to, or --reply-to a thread", 422)
    raw = mail_compose.build(
        to, body.subject, body.body, from_addr=addr, message_id=mid, cc=mail_compose.addresses(body.cc)
    )
    return raw, "", to, body.subject, None


def install_mail(app, store, auth):
    @app.post(
        "/api/v2/mail/messages/organize",
        **api_v2(
            "Email",
            "organizeMailMessages",
            "Organize up to 500 messages live in Whole Workspace Gmail; requires organize access; never deletes",
            "MailOrganizeResult",
            request_body={"required": True, "content": {"application/json": {"schema": ref("MailOrganizeBody")}}},
            responses={"403": error("Organize access or the company organize switch is off")},
        ),
    )
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
            if not google_tokens.covered(c, addr):
                raise Problem("mailbox", "Live organizing needs Whole Workspace or Connect Google", 422)
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
        result = {
            "mailbox": addr,
            "action": action,
            "count": len(ids),
            "ids": ids,
            **label_fields,
            "undo": {"action": inverse, "ids": ids, **label_fields},
        }

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
            out = {
                "principal": principal.ref,
                "mailboxes": mailbox_access.mailboxes_for(c, principal),
                "company": google_workspace.permissions(c),
            }
            addr = str(mailbox or "").strip().lower()
            if addr:
                out.update(
                    {
                        "mailbox": addr,
                        "level": mailbox_access.level(c, principal, addr),
                        "may": mailbox_access.may(c, principal, addr),
                    }
                )
            return out

    @app.get("/api/v2/mail/mailboxes")
    def mailboxes(request: Request):
        who = request.state.identity
        reader(who)
        with store.read() as c:
            people = roster(c)
            boxes = mailbox_access.mailboxes_for(c, who)
            return {"mailboxes": [mailbox_view(c, box["address"], people, who, auth, box) for box in boxes]}

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
            row = c.execute("SELECT content,updated_at FROM mail_agent_instructions WHERE bot=?", (bot,)).fetchone()
            if not row or not row["content"].strip():
                raise Problem("not_found", "Agent instructions are not available yet", 404)
            return {"mailbox": addr, "bot": bot, "instructions": row["content"], "updated_at": row["updated_at"]}

    @app.get("/api/v2/mail/messages")
    def messages(
        request: Request,
        mailbox: str = "",
        q: str = "",
        label: str = "",
        since: str = "",
        until: str = "",
        has_attachment: str = "",
        cursor: str = "",
        limit: int = LIMIT_DEFAULT,
    ):
        who = request.state.identity
        reader(who)
        cap = min(LIMIT_MAX, max(1, int(limit or LIMIT_DEFAULT)))
        since_epoch, until_epoch = epoch_bound(since, "since"), epoch_bound(until, "until")
        with store.read() as c:
            if not mailbox:
                if not usable_addresses(c, who):
                    return {"messages": [], "next_cursor": None, "q": q}
                raise Problem("mailbox", "Choose a mailbox to read live from Gmail", 422)
            addr, _ = require_mailbox(c, who, mailbox)
            query = gmail_query(q, label, since_epoch, until_epoch, has_attachment)
            page = gmail_live.list(c, addr, query, page_token=cursor, limit=min(cap, gmail_live.LIMIT_MAX))
            domains = mailbox_access.company_domains(c)
            return {**page, "messages": [live_message_public(addr, item, domains) for item in page["messages"]]}

    @app.get("/api/v2/mail/threads/{thread_id}")
    def thread(request: Request, thread_id: str, mailbox: str = ""):
        who = request.state.identity
        reader(who)
        tid = str(thread_id or "").strip()
        if not tid:
            raise Problem("thread", "thread_id is required", 422)
        # The thread is read outside the write lock; the audit event is written, in a short transaction, before
        # anything is returned.
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            domains = mailbox_access.company_domains(c)
            items = [live_message_public(addr, item, domains, body=True) for item in gmail_live.thread(c, addr, tid)]
            out = {"mailbox": addr, "thread_id": tid, "messages": items}
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", tid, {"mailbox": addr, "kind": "thread", "messages": len(items)})
        return out

    @app.get("/api/v2/mail/messages/{msg_id}")
    def message(request: Request, msg_id: str, mailbox: str = ""):
        who = request.state.identity
        reader(who)
        mid = str(msg_id or "").strip()
        if not mid:
            raise Problem("message", "msg_id is required", 422)
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            out = live_message_public(
                addr, gmail_live.message(c, addr, mid), mailbox_access.company_domains(c), body=True
            )
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", mid, {"mailbox": addr, "kind": "message", "thread_id": out["thread_id"]})
        return out

    @app.post("/api/v2/mail/drafts")
    def draft(request: Request, body: DraftBody):
        """A Gmail draft in a mailbox the caller may write drafts in: a new email, or a reply in a thread. The same
        draft asked again (the same words, or the same operation_id) finds the first one. A bot's own draft meets the
        lint (an error refuses it) and the second reviewer, whose verdict comes back; who receives it, and whether it is
        sent at all, is the send gate's to decide when it is sent."""
        from . import email_sending as ES
        from .email_content import snapshot

        who = request.state.identity
        reader(who)
        addr = body.mailbox.strip().lower()
        bot = who.actor if who.role == "bot" else ""
        # The draft's own words make its Message-ID: asked again (a retry, a rerun step) it is found, not made twice.
        key = mail_compose.key(
            who.actor, addr, body.operation_id, body.reply_to, body.to, body.cc, body.subject, body.body
        )
        mid = mail_compose.message_id("draft", key, addr)
        with store.read() as c:
            require_mailbox(c, who, addr)
            _refused(c, who, addr, "draft")
            _no_live_changes(store, "write")
            raw, thread, to, subject, original = compose(c, who, addr, body, mid)
            snap = snapshot(raw, addr)
            review = None
            if bot:
                errors = ES.lint_findings(c, bot, addr, snap, original["id"] if original else "")["errors"]
                if errors:
                    first = errors[0]
                    raise Problem(
                        "lint",
                        "The lint refused this draft: "
                        + ", ".join(e["id"] for e in errors)
                        + ". "
                        + str(first.get("fix") or ""),
                        422,
                        extra={"lint": [e["id"] for e in errors]},
                    )
                ask = SimpleNamespace(reply_to_id=original["id"] if original else "", thread_id=thread, approval_id="")
                review = ES.review_context(c, auth, bot, ask, addr, snap)
        made = mail_compose.ensure_draft(store, addr, raw, thread, mid)
        verdict = ES.classify(app, store, snap, review)["review"] if bot else None
        with store.transaction() as c:
            H.event(
                c,
                who.actor,
                "mail.draft",
                addr,
                {"mailbox": addr, "draft": made["id"], "thread": made["thread"], "created": made["created"]},
            )
        return {
            "mailbox": addr,
            "draft": made["id"],
            "message": made["message"],
            "thread": made["thread"] or thread,
            "to": to,
            "cc": mail_compose.addresses(body.cc),
            "subject": subject,
            "created": made["created"],
            # The second reviewer on a bot's own draft: send, stop (rewrite it), or None when it could not answer.
            "review": None if verdict is None else ("send" if verdict else "stop"),
        }

    @app.delete("/api/v2/mail/drafts/{draft_id}")
    def discard(request: Request, draft_id: str, mailbox: str = ""):
        who = request.state.identity
        reader(who)
        did = str(draft_id or "").strip()
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            _refused(c, who, addr, "draft")
            _no_live_changes(store, "change")
            deleted = gmail_live.draft_delete(c, addr, did)
        with store.transaction() as c:
            H.event(c, who.actor, "mail.draft_discarded", addr, {"mailbox": addr, "draft": did, "deleted": deleted})
        return {"mailbox": addr, "draft": did, "deleted": deleted}

    @app.post("/api/v2/mail/lint")
    def lint(request: Request, body: LintBody):
        """The deterministic lint on an email before it is drafted: errors refuse a bot's draft, warnings do not."""
        from . import email_sending as ES
        from .email_content import snapshot

        who = request.state.identity
        reader(who)
        addr = body.mailbox.strip().lower()
        with store.read() as c:
            require_mailbox(c, who, addr)
            raw, _thread, _to, _subject, original = compose(
                c, who, addr, body, mail_compose.message_id("lint", "check", addr)
            )
            found = ES.lint_findings(c, who.actor, addr, snapshot(raw, addr), original["id"] if original else "")
        return {"mailbox": addr, "ok": not found["errors"], "errors": found["errors"], "warnings": found["warnings"]}

    def _attachments(c, addr, mid):
        from connectors.mail import gmail as gm

        return gm.attachment_refs((gmail_live.payload(c, addr, mid) or {}).get("payload") or {})

    @app.get("/api/v2/mail/messages/{msg_id}/attachments")
    def attachments(request: Request, msg_id: str, mailbox: str = ""):
        who = request.state.identity
        reader(who)
        mid = str(msg_id or "").strip()
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            found = [
                {k: item[k] for k in ("ref", "name", "type", "size", "part_id", "attachment_id")}
                for item in _attachments(c, addr, mid)
            ]
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", mid, {"mailbox": addr, "kind": "attachments", "count": len(found)})
        return {"mailbox": addr, "message_id": mid, "attachments": found}

    @app.get("/api/v2/mail/messages/{msg_id}/attachments/{ref}")
    def attachment(request: Request, msg_id: str, ref: str, mailbox: str = ""):
        """One attachment's bytes (base64), at most gmail_live.ATTACHMENT_MAX_BYTES; `tico email attachment` writes
        them to a private file."""
        from connectors.mail import Failure
        from connectors.mail import gmail as gm

        who = request.state.identity
        reader(who)
        mid = str(msg_id or "").strip()
        if not gm.valid_attachment_ref(ref):
            raise Problem("ref", "An attachment reference looks like a1: list them with `tico email attachments`", 422)
        cap = gmail_live.ATTACHMENT_MAX_BYTES
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            item = next((a for a in _attachments(c, addr, mid) if a["ref"] == ref), None)
            if item is None:
                raise Problem("not_found", f"Message {mid} has no attachment {ref}", 404)
            if item["size"] > cap:
                raise Problem("too_large", f"The attachment is over {cap // (1024 * 1024)} MB; open it in Gmail", 413)
            if item["inline"]:
                encoded, size = item["_data"], item["size"]
            elif item["attachment_id"]:
                fetched = gmail_live.attachment(c, addr, mid, item["attachment_id"])
                encoded, size = fetched["data"], max(item["size"], fetched["size"])
            else:
                raise Problem("not_found", f"Attachment {ref} has no data Gmail can give; open it in Gmail", 404)
        try:
            data = gm.attachment_bytes(encoded, size, limit=cap)
        except Failure as bad:
            raise Problem("attachment", bad.msg, 502) from bad
        with store.transaction() as c:
            H.event(
                c,
                who.actor,
                "mail.read",
                mid,
                {"mailbox": addr, "kind": "attachment", "ref": ref, "bytes": len(data)},
            )
        return {
            "mailbox": addr,
            "message_id": mid,
            "ref": ref,
            "name": item["name"],
            "type": item["type"],
            "size": len(data),
            "data": base64.b64encode(data).decode(),
        }

    @app.post("/api/v2/mail/messages/{msg_id}/unsubscribe")
    def unsubscribe(request: Request, msg_id: str, body: MailboxBody):
        """One one-click unsubscribe from the message's own link, from the server (backend/mail_unsubscribe.py). Mail
        the mailbox keeps, or a link Tico may not use, is refused with why and nothing is sent; a repeat sends nothing
        again."""
        from . import mail_unsubscribe

        who = request.state.identity
        reader(who)
        mid = str(msg_id or "").strip()
        with store.read() as c:
            addr, _ = require_mailbox(c, who, body.mailbox)
            _refused(c, who, addr, "organize")
        return {"mailbox": addr, "message_id": mid, **mail_unsubscribe.unsubscribe(store, addr, mid, who.actor)}
