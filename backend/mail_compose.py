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
from email.message import EmailMessage

from . import gmail_live
from .store import Problem

RE_PREFIX = re.compile(r"(?i)^\s*(re\s*:\s*)+")
FWD_PREFIX = re.compile(r"(?i)^\s*(fwd?\s*:\s*)+")
FORWARDED = (
    "\n\n---------- Forwarded message ---------\nFrom: {from_header}\nDate: {date}\nSubject: {subject}\n"
    "To: {to}\n\n{body}"
)
BODY_MAX = 100_000
SUBJECT_MAX = 900


def key(*parts):
    """A short stable key of the parts: the Message-ID of one draft."""
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part if part is not None else "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def message_id(kind, ident, mailbox):
    domain = str(mailbox).rpartition("@")[2].strip().lower() or "tico.invalid"
    ident = re.sub(r"[^A-Za-z0-9._-]", "-", str(ident))[:80]
    return f"<tico-{kind}-{ident}@{domain}>"


def addresses(value):
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    return [str(a).strip().lower() for a in items if "@" in str(a)]


def reply_subject(subject):
    """ "Re: X" once, never "Re: Re: X"."""
    base = RE_PREFIX.sub("", str(subject or "")).strip()
    return f"Re: {base}" if base else "Re:"


def build(to, subject, body, *, from_addr, message_id, cc=(), in_reply_to="", references=""):
    """Plain-text MIME, base64url without padding (the Gmail API's raw)."""
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
    return base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")


def _header(item, name):
    return str((item.get("headers") or {}).get(name) or "").strip()


def reply(mailbox, original, body, mid, to=(), cc=()):
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
    )
    return raw, str(original.get("thread_id") or ""), to, subject


def forward(mailbox, original, note, to, mid):
    """`original` forwarded to `to` with `note` first: a new message, outside the original's thread."""
    to = addresses(to)
    if not to:
        raise Problem("to", "A forward needs `to`", 422)
    subject = "Fwd: " + FWD_PREFIX.sub("", RE_PREFIX.sub("", str(original.get("subject") or ""))).strip()
    body = str(note or "") + FORWARDED.format(
        from_header=original.get("from_header") or original.get("from") or "",
        date=original.get("date") or "",
        subject=original.get("subject") or "",
        to=", ".join(original.get("to") or []),
        body=original.get("body") or "",
    )
    return build(to, subject, body, from_addr=mailbox, message_id=mid), "", to, subject


def answered_in(mailbox, messages):
    """The message a reply in this thread answers: the newest one the mailbox did not send (else the newest)."""
    ordered = sorted(messages, key=lambda m: (m.get("epoch") or 0, m.get("id") or ""))
    theirs = [m for m in ordered if str(m.get("from") or "").lower() != mailbox.lower()]
    return (theirs or ordered)[-1] if ordered else None


def ensure_draft(c, mailbox, raw, thread_id, mid):
    """The Gmail draft under Message-ID `mid`: the one already there, else a new one. {id, message, thread, created}."""
    found = gmail_live.draft_find(c, mailbox, mid)
    if found:
        return {**found, "created": False}
    return {**gmail_live.draft_create(c, mailbox, raw, thread_id), "created": True}
