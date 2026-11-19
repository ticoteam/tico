"""Read API over live Gmail (Tico keeps no copy of mail), and live organizing, drafting and unsubscribing.

People and bots use the mailboxes they hold a level on (backend/mailbox_access.py), the owner and managers included,
with no bypass; a person's Assistant is that person. Runners are refused. Every thread, message body or attachment
read is audited as `mail.read`. A draft (`POST /api/v2/mail/drafts`) is written to Gmail under a Message-ID Tico
chose, so the same draft asked for again is found, not made twice (backend/mail_compose.py); a bot's own draft meets
the lint first, and the second reviewer reads it. Sending a draft is the send gate's (`POST /api/v2/email/sends` with
its `draft_id`). Every draft in the mailbox, whoever made it, is listed, read, changed in place (meeting the same lint
and review) and discarded by its draft id (r-...) or by its message's id; a draft's message in a thread or a search
carries its `draft_id`. `tico email ...` (clients/commands/mail.py) is a thin call to each route.

`GET /api/v2/mail/access` answers what the caller, a person or a bot's turn, may do on each mailbox it holds.
"""

import base64
import hashlib
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
ATTACHMENTS_MAX = 20
DRAFT_ID = re.compile(r"r-?\d+")  # Gmail's draft ids; a message id is hex
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
    # Tico files to attach, in order: FILE (its current version) or FILE@N, each one the caller may download.
    attachments: list[str] = Field(default_factory=list, max_length=ATTACHMENTS_MAX)
    operation_id: str = Field(default="", max_length=200)


class LintBody(Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=900)
    body: str = Field(min_length=1, max_length=mail_compose.BODY_MAX)
    reply_to: str = Field(default="", max_length=200)


class DraftUpdateBody(Contract):
    """What changes in a draft; a field left out keeps what the draft has now (its files included)."""

    mailbox: str = Field(min_length=3, max_length=320)
    to: list[str] | None = Field(default=None, max_length=50)
    cc: list[str] | None = Field(default=None, max_length=50)
    subject: str | None = Field(default=None, max_length=900)
    body: str | None = Field(default=None, min_length=1, max_length=mail_compose.BODY_MAX)
    # Tico files that replace the draft's files, in order (FILE or FILE@N); [] takes them all off.
    attachments: list[str] | None = Field(default=None, max_length=ATTACHMENTS_MAX)
    operation_id: str = Field(default="", max_length=200)


class MailboxBody(Contract):
    mailbox: str = Field(min_length=3, max_length=320)


def no_draft(addr, ident):
    return Problem(
        "not_found",
        f"{addr} has no draft {ident}: it was sent or discarded, or the id is neither a draft id (r-...) nor a draft's "
        f"message id. `tico email drafts {addr}` lists its drafts",
        404,
    )


def draft_public(addr, item, domains, body=False):
    """A draft's message as the read API shows a message, with its draft id and when it was last saved."""
    out = live_message_public(addr, item, domains, body=body)
    if not body:
        out["attachments"] = [a["name"] for a in item["attachments"]]
    return {"draft_id": item["draft"], **out, "updated": out["date"]}


def with_draft_ids(c, addr, messages):
    """Each draft's message among these carries its `draft_id`: the id a discard, update or send names."""
    ids = [m["msg_id"] for m in messages if "DRAFT" in m["labels"]]
    found = gmail_live.draft_ids(c, addr, ids) if ids else {}
    for m in messages:
        if m["msg_id"] in found:
            m["draft_id"] = found[m["msg_id"]]
    return messages


def read_draft(c, addr, ident):
    """The draft named by its draft id or its message's id, its message read whole; not_found when neither."""
    found = gmail_live.draft_message(c, addr, ident)
    if found is None:
        row = next(iter(gmail_live.draft_scan(c, addr, lambda r: r["message"] == ident)), None)
        found = gmail_live.draft_message(c, addr, row["id"]) if row else None
    if found is None:
        raise no_draft(addr, ident)
    return found


def parsed_draft(raw):
    """A draft's MIME (base64url) as (message, its plain text or None, its files as (name, type, bytes))."""
    from email import policy
    from email.parser import BytesParser

    msg = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    text = msg.get_body(preferencelist=("plain",))
    files = []
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        files.append(
            (part.get_filename() or "attachment.bin", part.get_content_type(), data if isinstance(data, bytes) else b"")
        )
    return msg, text.get_content() if text is not None else None, files


def header_addresses(msg, name):
    from email.utils import getaddresses

    return mail_compose.addresses([a for _, a in getaddresses(msg.get_all(name) or [])])


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


def compose(c, who, addr, body, mid, files=()):
    """(raw, thread id, recipients, subject, the message it answers or None) for a draft or a lint. `files` are the
    attachments' (name, content type, bytes), in order."""
    original = None
    if body.reply_to:
        original = mail_compose.answered_in(addr, gmail_live.thread(c, addr, body.reply_to))
    if original:
        raw, thread, to, subject = mail_compose.reply(
            addr, original, body.body, mid, to=body.to, cc=body.cc, attachments=files
        )
        return raw, thread, to, subject, original
    to = mail_compose.addresses(body.to)
    if not to:
        raise Problem("to", "A new email needs a recipient: --to, or --reply-to a thread", 422)
    raw = mail_compose.build(
        to,
        body.subject,
        body.body,
        from_addr=addr,
        message_id=mid,
        cc=mail_compose.addresses(body.cc),
        attachments=files,
    )
    return raw, "", to, body.subject, None


def attachment_file(c, app, auth, who, ref):
    """One attachment named by a Tico file id, FILE or FILE@N: {ref, name, content_type, size, digest}, read under the
    check its download makes (GET /api/v2/files/{id}): a Library file through Files.readable_version, any other
    stored file (a message's or a task's) through media.blob_for. One the caller may not read is not found."""
    from .files import is_file_id
    from .media import blob_for

    fid, at, number = str(ref).strip().partition("@")
    if not fid or at and not (number.isdigit() and int(number) >= 1):
        raise Problem(
            "attachments",
            f"{ref} is not a Tico file id (FILE or FILE@N); a file on your computer goes up with "
            "`tico email draft --attach PATH`",
            422,
        )
    version = int(number) if number else None
    library = is_file_id(fid) or c.execute("SELECT 1 FROM bot_files WHERE id=?", (fid,)).fetchone()
    if library:
        try:
            v = app.state.files.readable_version(c, who, fid, version)
            return {
                "ref": f"{fid}@{v['version']}",
                "name": v["name"],
                "content_type": v["mime"],
                "size": v["size"],
                "digest": v["blob_digest"],
            }
        except Problem as exc:
            # An adopted legacy blob id is also a plain stored file, as its download falls back to (media.py).
            if is_file_id(fid) or version not in (None, 1) or exc.status not in (403, 404):
                raise
    if version not in (None, 1):
        raise Problem("not_found", "File version not found", 404)
    row = blob_for(c, auth, who, fid)
    return {
        "ref": fid,
        "name": row["name"],
        "content_type": row["content_type"],
        "size": row["size"],
        "digest": row["digest"],
    }


def attachment_files(store, app, auth, who, refs):
    """The attachments' metadata (attachment_file) and then their bytes: (metadata, [(name, type, bytes)]). Files
    together over the email's limit are refused before any is read."""
    if not refs:
        return [], []
    with store.read() as c:
        found = [attachment_file(c, app, auth, who, ref) for ref in refs]
    total = sum(int(f["size"] or 0) for f in found)
    if total * 4 // 3 > mail_compose.RAW_MAX:
        raise Problem(
            "attachments",
            f"These files are {total / mail_compose.MB:.1f} MB; an email through Tico carries about "
            f"{mail_compose.RAW_MAX * 3 // 4 // mail_compose.MB} MB of files. Send fewer or smaller files, or share "
            "a link",
            413,
        )
    blobs = app.state.blobs
    return found, [(f["name"], f["content_type"] or "application/octet-stream", blobs.get(f["digest"])) for f in found]


def _verdict(review):
    """The second reviewer's verdict in a word: send, stop, or None when it gave none."""
    return None if review is None else ("send" if review else "stop")


def install_mail(app, store, auth):
    def bot_checks(c, bot, addr, snap, thread, reply_id):
        """A bot's own draft meets the lint (an error refuses it) before Gmail sees it; the second reviewer's context,
        or None for a person's draft."""
        from . import email_sending as ES

        if not bot:
            return None
        errors = ES.lint_findings(c, bot, addr, snap, reply_id)["errors"]
        if errors:
            raise Problem(
                "lint",
                "The lint refused this draft: "
                + ", ".join(e["id"] for e in errors)
                + ". "
                + str(errors[0].get("fix") or ""),
                422,
                extra={"lint": [e["id"] for e in errors]},
            )
        ask = SimpleNamespace(reply_to_id=reply_id, thread_id=thread, approval_id="")
        return ES.review_context(c, auth, bot, ask, addr, snap)

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
        included, which is them) or a bot's turn; a computer holds no mailbox."""
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
            listed = [live_message_public(addr, item, domains) for item in page["messages"]]
            return {**page, "messages": with_draft_ids(c, addr, listed)}

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
            out = {"mailbox": addr, "thread_id": tid, "messages": with_draft_ids(c, addr, items)}
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", tid, {"mailbox": addr, "kind": "thread", "messages": len(items)})
        return out

    @app.get("/api/v2/mail/messages/{msg_id}")
    def message(request: Request, msg_id: str, mailbox: str = ""):
        """One message with its body. A draft id (r-...) reads that draft's message, with its `draft_id`."""
        who = request.state.identity
        reader(who)
        mid = str(msg_id or "").strip()
        if not mid:
            raise Problem("message", "msg_id is required", 422)
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            domains = mailbox_access.company_domains(c)
            if DRAFT_ID.fullmatch(mid):
                out = draft_public(addr, read_draft(c, addr, mid), domains, body=True)
            else:
                out = with_draft_ids(
                    c, addr, [live_message_public(addr, gmail_live.message(c, addr, mid), domains, True)]
                )[0]
        with store.transaction() as c:
            H.event(c, who.actor, "mail.read", mid, {"mailbox": addr, "kind": "message", "thread_id": out["thread_id"]})
        return out

    @app.get("/api/v2/mail/drafts")
    def drafts(
        request: Request, mailbox: str = "", q: str = "", thread: str = "", cursor: str = "", limit: int = LIMIT_DEFAULT
    ):
        """The mailbox's drafts, newest first, whoever made them: each its draft id, message id, thread, recipients,
        subject, when it was last saved and its files' names. `q` is a Gmail query over drafts; `thread` keeps one
        thread's drafts."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            page = gmail_live.drafts(
                c,
                addr,
                q.strip(),
                thread.strip(),
                cursor,
                min(max(1, int(limit or LIMIT_DEFAULT)), gmail_live.LIMIT_MAX),
            )
            domains = mailbox_access.company_domains(c)
            return {**page, "mailbox": addr, "drafts": [draft_public(addr, d, domains) for d in page["drafts"]]}

    @app.get("/api/v2/mail/drafts/{draft_id}")
    def draft_read(request: Request, draft_id: str, mailbox: str = ""):
        """One draft, named by its draft id or its message's id: headers, body and files; audited as a read."""
        who = request.state.identity
        reader(who)
        did = str(draft_id or "").strip()
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            out = draft_public(addr, read_draft(c, addr, did), mailbox_access.company_domains(c), body=True)
        with store.transaction() as c:
            H.event(
                c, who.actor, "mail.read", out["msg_id"], {"mailbox": addr, "kind": "draft", "draft": out["draft_id"]}
            )
        return out

    @app.post("/api/v2/mail/drafts")
    def draft(request: Request, body: DraftBody):
        """A Gmail draft in a mailbox the caller may write drafts in: a new email, or a reply in a thread. The same
        draft asked again (the same words and files, or the same operation_id) finds the first one. `attachments` are
        Tico files the caller may download, sent as they are now (multipart/mixed). A bot's own draft meets the
        lint (an error refuses it) and the second reviewer, whose verdict comes back; who receives it, and whether it is
        sent at all, is the send gate's to decide when it is sent."""
        from . import email_sending as ES
        from .email_content import snapshot

        who = request.state.identity
        reader(who)
        addr = body.mailbox.strip().lower()
        bot = who.actor if who.role == "bot" else ""
        with store.read() as c:
            require_mailbox(c, who, addr)
            _refused(c, who, addr, "draft")
            _no_live_changes(store, "write")
        # The draft's own words make its Message-ID: asked again (a retry, a rerun step) it is found, not made twice.
        # Each file counts as it is now, its version and the hash of its bytes: other files, or a file changed since,
        # are another draft. A draft without files keeps the key it always had.
        found, files = attachment_files(store, app, auth, who, body.attachments)
        attached = [json.dumps([[f["ref"], f["digest"]] for f in found])] if found else []
        key = mail_compose.key(
            who.actor, addr, body.operation_id, body.reply_to, body.to, body.cc, body.subject, body.body, *attached
        )
        mid = mail_compose.message_id("draft", key, addr)
        with store.read() as c:
            raw, thread, to, subject, original = compose(c, who, addr, body, mid, files)
            snap = snapshot(raw, addr)
            review = bot_checks(c, bot, addr, snap, thread, original["id"] if original else "")
        made = mail_compose.ensure_draft(store, addr, raw, thread, mid)
        checked = ES.classify(app, store, snap, review) if bot else {}
        verdict = checked.get("review")
        # The verdict a bot's draft got, with its reason, is kept beside the draft: what the bot was told can be read back.
        said = {"review": _verdict(verdict), "review_reason": checked.get("review_reason", "")} if bot else {}
        with store.transaction() as c:
            H.event(
                c,
                who.actor,
                "mail.draft",
                addr,
                {"mailbox": addr, "draft": made["id"], "thread": made["thread"], "created": made["created"], **said},
            )
        return {
            "mailbox": addr,
            "draft": made["id"],
            "message": made["message"],
            "thread": made["thread"] or thread,
            "to": to,
            "cc": mail_compose.addresses(body.cc),
            "subject": subject,
            "attachments": [{k: f[k] for k in ("ref", "name", "content_type", "size")} for f in found],
            "created": made["created"],
            # The second reviewer on a bot's own draft: send, stop (rewrite it), or None when it could not answer.
            # `review_reason` says why in words whenever it is not send (backend/email_sending.py `reviewed`).
            "review": _verdict(verdict),
            "review_reason": checked.get("review_reason", ""),
        }

    @app.patch("/api/v2/mail/drafts/{draft_id}")
    def update_draft(request: Request, draft_id: str, body: DraftUpdateBody):
        """Change a draft in place, keeping its draft id (Gmail gives its message a new id): any of to, cc, subject,
        body and files, the rest kept as the draft has them. A reply stays a reply in its thread. A bot's draft meets
        the lint and the second reviewer again, as a new one does."""
        from . import email_sending as ES
        from .email_content import snapshot

        who = request.state.identity
        reader(who)
        addr = body.mailbox.strip().lower()
        bot = who.actor if who.role == "bot" else ""
        ident = str(draft_id or "").strip()
        with store.read() as c:
            require_mailbox(c, who, addr)
            _refused(c, who, addr, "draft")
            _no_live_changes(store, "change")
            found = gmail_live.draft_resolve(c, addr, ident)
            if not found:
                raise no_draft(addr, ident)
            current = gmail_live.draft_raw(c, addr, found["id"])
        msg, text, kept = parsed_draft(current["raw"])
        if body.attachments is None:
            listed = [{"ref": "", "name": n, "content_type": t, "size": len(b)} for n, t, b in kept]
            files = kept
        else:
            listed, files = attachment_files(store, app, auth, who, body.attachments)
        if body.body is None and text is None:
            raise Problem("body", "This draft has no plain text to keep; give the whole new body", 422)
        to = mail_compose.addresses(body.to) if body.to is not None else header_addresses(msg, "To")
        cc = mail_compose.addresses(body.cc) if body.cc is not None else header_addresses(msg, "Cc")
        subject = body.subject if body.subject is not None else str(msg["Subject"] or "")
        words = body.body if body.body is not None else (text or "")
        if not to:
            raise Problem("to", "A draft needs a recipient: --to", 422)
        answered, refs = str(msg["In-Reply-To"] or ""), str(msg["References"] or "")
        digests = [f"{n}:{hashlib.sha256(b).hexdigest()}" for n, _t, b in files]
        mid = mail_compose.message_id(
            "draft", mail_compose.key(who.actor, addr, found["id"], to, cc, subject, words, *digests), addr
        )
        raw = mail_compose.build(
            to,
            subject,
            words,
            from_addr=addr,
            message_id=mid,
            cc=cc,
            in_reply_to=answered,
            references=refs,
            attachments=files,
        )
        thread = current["thread"] if answered or refs else ""
        snap = snapshot(raw, addr)
        with store.read() as c:
            reply_id = ES.resolve_reply(c, addr, snap, "", thread)[0] if thread else ""
            review = bot_checks(c, bot, addr, snap, thread, reply_id)
            made = gmail_live.draft_update(c, addr, found["id"], raw, thread)
        checked = ES.classify(app, store, snap, review) if bot else {}
        said = {"review": _verdict(checked.get("review")), "review_reason": checked.get("review_reason", "")}
        with store.transaction() as c:
            # The draft's words changed: a later `draft` of its old words is another draft, and of these words is this.
            c.execute("DELETE FROM mail_drafts WHERE draft_id=? OR message_id=?", (made["id"], mid))
            c.execute(
                "INSERT INTO mail_drafts(message_id,mailbox,claimed_at,draft_id,message,thread) VALUES(?,?,?,?,?,?)",
                (mid, addr, H.now(), made["id"], made["message"], made["thread"]),
            )
            H.event(
                c,
                who.actor,
                "mail.draft_updated",
                addr,
                {"mailbox": addr, "draft": made["id"], "thread": made["thread"], **(said if bot else {})},
            )
        return {
            "mailbox": addr,
            "draft": made["id"],
            "message": made["message"],
            "thread": made["thread"] or thread,
            "to": to,
            "cc": cc,
            "subject": subject,
            "attachments": [{k: f[k] for k in ("ref", "name", "content_type", "size")} for f in listed],
            "updated": True,
            **said,
        }

    @app.delete("/api/v2/mail/drafts/{draft_id}")
    def discard(request: Request, draft_id: str, mailbox: str = ""):
        """Discard one draft, named by its draft id or its message's id, whoever made it. Gmail deletes a draft for
        good. A name that is no draft is not_found, never a silent false."""
        who = request.state.identity
        reader(who)
        ident = str(draft_id or "").strip()
        with store.read() as c:
            addr, _ = require_mailbox(c, who, mailbox)
            _refused(c, who, addr, "draft")
            _no_live_changes(store, "change")
            found = gmail_live.draft_resolve(c, addr, ident)
            if not found:
                raise no_draft(addr, ident)
            deleted = gmail_live.draft_delete(c, addr, found["id"])
        with store.transaction() as c:
            H.event(
                c,
                who.actor,
                "mail.draft_discarded",
                addr,
                {"mailbox": addr, "draft": found["id"], "message": found["message"], "deleted": deleted},
            )
        return {"mailbox": addr, "draft": found["id"], "message": found["message"], "deleted": deleted}

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
        from .mail_lib import gmail as gm

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
        from .mail_lib import Failure
        from .mail_lib import gmail as gm

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
