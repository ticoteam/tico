"""Building the message, and the idempotency keys that stop a retry becoming a second email.

Pure. Nothing here talks to Google; `gmail.py` takes the raw string this produces.

Two keys, both sha256 hex:
  draft_key(employee, issue, to, subject, body)   a retried `draft` updates the same Gmail draft
                                                  rather than making a second one
  send_key(employee, issue, draft_id)             written to the database *before* the send call,
                                                  so a crash between the call and the row cannot
                                                  turn into a double send

A reply keeps the thread: same subject, In-Reply-To and References taken from the last message
in the thread, and the Gmail threadId passed alongside the raw MIME.

Attachments come in already read ({name, type, data}); with any, the message is multipart/mixed
with the text first, so lint and the reviewer read the same body Gmail shows.
"""

import base64, hashlib, re
from email.message import EmailMessage

RE_PREFIX = re.compile(r"(?i)^\s*(re\s*:\s*)+")


def key(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p if p is not None else "").strip().encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def draft_key(employee, issue, to, subject, body):
    return key(employee, issue, ",".join(sorted(str(a).lower() for a in to or [])), subject, body)


def send_key(employee, issue, draft_id):
    return key("send", employee, issue, draft_id)


def reply_subject(subject):
    """"Re: X" once, never "Re: Re: X"."""
    base = RE_PREFIX.sub("", str(subject or "")).strip()
    return f"Re: {base}" if base else ""


def thread_headers(messages):
    """(in_reply_to, references, subject, participants) from a normalized thread, newest last."""
    if not messages:
        return "", "", "", []
    ordered = sorted(messages, key=lambda m: m.get("epoch") or 0)
    last = ordered[-1]
    mid = (last.get("headers") or {}).get("message-id", "").strip()
    refs = " ".join(x for x in [(last.get("headers") or {}).get("references", "").strip(), mid]
                    if x).strip()
    people = []
    for m in ordered:
        for a in [m.get("from")] + list(m.get("to") or []) + list(m.get("cc") or []):
            a = str(a or "").strip().lower()
            if a and a not in people:
                people.append(a)
    return mid, refs, reply_subject(last.get("subject") or ""), people


def build(to, subject, body, from_addr="", cc=(), in_reply_to="", references="", attachments=()):
    """A plain-text message, with any attachments, base64url encoded the way the Gmail API wants it."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to if isinstance(to, (list, tuple)) else [to])
    if cc:
        msg["Cc"] = ", ".join(cc)
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject or ""
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body or "")
    for a in attachments or ():
        main, _, sub = str(a.get("type") or "application/octet-stream").partition("/")
        msg.add_attachment(a["data"], maintype=main or "application",
                           subtype=sub or "octet-stream", filename=a["name"])
    return base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")


def decode(raw):
    """The other direction, for tests and for `doctor --e2e`."""
    s = str(raw or "").replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
