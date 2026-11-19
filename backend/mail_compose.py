# pyright: strict
"""Email Tico writes on the server: replies, forwards and new messages as MIME, and their Gmail drafts.

Every message Tico composes carries a Message-ID Tico chose (`message_id`): `<tico-<kind>-<key>@<mailbox domain>>`. A
draft is made once under it and found again by it (`ensure_draft`, Gmail's `rfc822msgid:` search over drafts), so a
retried automation action or a bot's repeated `tico email draft` finds its draft instead of making a second one. A reply
keeps its thread: Re: once, In-Reply-To and References from the message it answers, and Gmail's thread id beside the
MIME. Nothing here keeps a body: the MIME goes to Gmail, and to the send queue when it is sent.
"""

import base64
import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from email.message import EmailMessage
from typing import Any, cast

from . import gmail_live
from .email_content import MAX_RAW
from .store import Problem, Store

Conn = sqlite3.Connection
Json = dict[str, Any]

RE_PREFIX = re.compile(r"(?i)^\s*(re\s*:\s*)+")
FWD_PREFIX = re.compile(r"(?i)^\s*(fwd?\s*:\s*)+")
FORWARDED = (
    "\n\n---------- Forwarded message ---------\nFrom: {from_header}\nDate: {date}\nSubject: {subject}\n"
    "To: {to}\n\n{body}"
)
BODY_MAX = 100_000
SUBJECT_MAX = 900
RAW_MAX = MAX_RAW  # the send gate's limit on a whole email; Gmail's is 25 MB
MB = 1024 * 1024

# A file on an email: (name, content type, bytes).
Attachment = tuple[str, str, bytes]


def key(*parts: object) -> str:
    """A short stable key of the parts: the Message-ID of one draft."""
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part if part is not None else "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def message_id(kind: str, ident: object, mailbox: str) -> str:
    domain = str(mailbox).rpartition("@")[2].strip().lower() or "tico.invalid"
    ident = re.sub(r"[^A-Za-z0-9._-]", "-", str(ident))[:80]
    return f"<tico-{kind}-{ident}@{domain}>"


def addresses(value: object) -> list[str]:
    items: Sequence[object]
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
    else:
        items = str(value or "").split(",")
    return [str(a).strip().lower() for a in items if "@" in str(a)]


def reply_subject(subject: object) -> str:
    """ "Re: X" once, never "Re: Re: X"."""
    base = RE_PREFIX.sub("", str(subject or "")).strip()
    return f"Re: {base}" if base else "Re:"


def build(
    to: Sequence[str],
    subject: str,
    body: str,
    *,
    from_addr: str,
    message_id: str,
    cc: Sequence[str] = (),
    in_reply_to: str = "",
    references: str = "",
    attachments: Sequence[Attachment] = (),
) -> str:
    """Plain-text MIME, base64url without padding (the Gmail API's raw); with `attachments`, multipart/mixed: the
    text, then each file in order. An email over RAW_MAX (its files base64-encoded) is refused."""
    if len(body or "") > BODY_MAX:
        raise Problem("body", f"The email body is over {BODY_MAX:,} characters", 422)
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = str(subject or "")[:SUBJECT_MAX]
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body or "")
    for name, content_type, data in attachments:
        maintype, _, subtype = (content_type or "").partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    mime = msg.as_bytes()
    if len(mime) > RAW_MAX:
        raise Problem(
            "attachments",
            f"This email is {len(mime) / MB:.1f} MB with its files encoded; an email through Tico is at most "
            f"{RAW_MAX // MB} MB (about {RAW_MAX * 3 // 4 // MB} MB of files). Send fewer or smaller files, or share "
            "a link",
            413,
        )
    return base64.urlsafe_b64encode(mime).decode().rstrip("=")


def _header(item: Json, name: str) -> str:
    headers: Json = item.get("headers") or {}
    return str(headers.get(name) or "").strip()


def reply(
    mailbox: str,
    original: Json,
    body: str,
    mid: str,
    to: object = (),
    cc: object = (),
    attachments: Sequence[Attachment] = (),
) -> tuple[str, str, list[str], str]:
    """A reply to `original` (a normalized Gmail message): (raw, thread id, recipients, subject). It goes to the
    message's sender unless `to` names others."""
    if not str(body or "").strip():
        raise Problem("body", "A reply needs a body", 422)
    to = addresses(to) or addresses([original.get("from")])
    if not to:
        raise Problem("to", "The message has no sender to reply to; name a recipient", 422)
    answered = _header(original, "message-id")
    refs = " ".join(x for x in (_header(original, "references"), answered) if x)
    subject = reply_subject(original.get("subject"))
    raw = build(
        to,
        subject,
        body,
        from_addr=mailbox,
        message_id=mid,
        cc=addresses(cc),
        in_reply_to=answered,
        references=refs,
        attachments=attachments,
    )
    return raw, str(original.get("thread_id") or ""), to, subject


def forward(mailbox: str, original: Json, note: str, to: object, mid: str) -> tuple[str, str, list[str], str]:
    """`original` forwarded to `to` with `note` first: a new message, outside the original's thread."""
    to = addresses(to)
    if not to:
        raise Problem("to", "A forward needs `to`", 422)
    subject = "Fwd: " + FWD_PREFIX.sub("", RE_PREFIX.sub("", str(original.get("subject") or ""))).strip()
    recipients: list[Any] = original.get("to") or []
    body = str(note or "") + FORWARDED.format(
        from_header=original.get("from_header") or original.get("from") or "",
        date=original.get("date") or "",
        subject=original.get("subject") or "",
        to=", ".join(recipients),
        body=original.get("body") or "",
    )
    return build(to, subject, body, from_addr=mailbox, message_id=mid), "", to, subject


def answered_in(mailbox: str, messages: Iterable[Json]) -> Json | None:
    """The message a reply in this thread answers: the newest one the mailbox did not send (else the newest)."""
    ordered = sorted(messages, key=lambda m: (m.get("epoch") or 0, m.get("id") or ""))
    theirs = [m for m in ordered if str(m.get("from") or "").lower() != mailbox.lower()]
    return (theirs or ordered)[-1] if ordered else None


CLAIM_SECONDS = 120  # a claim older than this was left by a request that stopped: another may take it over


def ensure_draft(store: Store, mailbox: str, raw: str, thread_id: str, mid: str) -> Json:
    """The Gmail draft under Message-ID `mid`: the one already made, else a new one. {id, message, thread, created}.

    The request claims the Message-ID in `mail_drafts` before it asks Gmail, so two requests for the same draft at once
    make one: the second is told to try again (409, retryable) while the first works. A draft made before is checked
    in Gmail by its id (a person may have deleted it), and found by its Message-ID when no id was kept. Gmail is
    called under no write lock."""
    from .store import H

    with cast("AbstractContextManager[Conn]", store.transaction()) as c:
        row = c.execute("SELECT * FROM mail_drafts WHERE message_id=?", (mid,)).fetchone()
        now = H.now()
        if (
            row
            and not row["draft_id"]
            and row["claimed_at"] > cast("Callable[..., str]", H.shift)(now, seconds=-CLAIM_SECONDS)
        ):
            raise Problem("draft_busy", "This draft is being written now; try again in a moment", 409, retryable=True)
        kept = row["draft_id"] if row else None
        if not kept:
            c.execute(
                "INSERT INTO mail_drafts(message_id,mailbox,claimed_at) VALUES(?,?,?) ON CONFLICT(message_id) "
                "DO UPDATE SET claimed_at=excluded.claimed_at",
                (mid, mailbox, now),
            )
    if kept:
        with cast("AbstractContextManager[Conn]", store.read()) as c:
            found = cast("Callable[[Conn, str, str], Json | None]", gmail_live.draft_get)(c, mailbox, kept)
        if found:
            return {**found, "created": False}
        with cast("AbstractContextManager[Conn]", store.transaction()) as c:  # deleted in Gmail since: it is made again
            c.execute("UPDATE mail_drafts SET draft_id=NULL,claimed_at=? WHERE message_id=?", (now, mid))
    try:
        with cast("AbstractContextManager[Conn]", store.read()) as c:
            found = cast("Callable[[Conn, str, str], Json | None]", gmail_live.draft_find)(c, mailbox, mid)
            made: Json = (
                {**found, "created": False}
                if found
                else {
                    **cast("Callable[[Conn, str, str, str], Json]", gmail_live.draft_create)(
                        c, mailbox, raw, thread_id
                    ),
                    "created": True,
                }
            )
    except BaseException:
        # nothing was made: the next request may try at once
        with cast("AbstractContextManager[Conn]", store.transaction()) as c:
            c.execute("DELETE FROM mail_drafts WHERE message_id=? AND draft_id IS NULL AND claimed_at=?", (mid, now))
        raise
    with cast("AbstractContextManager[Conn]", store.transaction()) as c:
        c.execute(
            "UPDATE mail_drafts SET draft_id=?,message=?,thread=? WHERE message_id=?",
            (made["id"], made["message"], made["thread"], mid),
        )
    return made
