"""One-click unsubscribe (RFC 8058) from the server, for an automation's action or a bot's or person's own request.

The link is the message's own List-Unsubscribe header, used only when the message carries exactly one https link,
asks for one-click (List-Unsubscribe-Post) and a DKIM signature over From and both headers passed Gmail's check
(`endpoint`, `verify_dkim`): mail cannot name an address for Tico to call by saying so. The request goes out through backend/egress.py (https to a public
address only, no redirect followed, no proxy), and it is claimed in `mail_unsubscribes` before it goes, so a retry after
an unclear answer sends nothing: it answers what the first one did. A sender who answers with a redirect or an error has
not confirmed; nothing more is tried.

Some mail is never unsubscribed from (`keep_reason`): starred mail, mail from inside the company or from someone on the
people roster, and mail from a sender the mailbox already writes to. The endpoint carries a recipient token, so only its
host and a hash of it are kept.
"""

import hashlib
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit

from . import egress, gmail_live, mail_facts, mailbox_access
from .store import H, Problem

BODY = b"List-Unsubscribe=One-Click"
HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}
SIGNED = {b"from", b"list-unsubscribe", b"list-unsubscribe-post"}
TRANSPORT = None  # tests stand in for the network here (egress.post `transport`)


class Unsupported(ValueError):
    """The message offers no one-click unsubscribe Tico may use: a person unsubscribes by hand."""


def _tags(value):
    return {k.strip().lower(): re.sub(r"\s+", "", v) for k, v in re.findall(r"([A-Za-z.]+)\s*=\s*([^;]+)", value)}


def verify_dkim(raw):
    """Whether Gmail verified a DKIM signature that covers From and both unsubscribe headers. Gmail checks every
    signature when the mail arrives and puts its answer on top of the message (`Authentication-Results:
    mx.google.com; dkim=pass ... header.b=<the signature's first characters>`); a sender can only add such headers
    below it. The passing signature, found by its `b=`, must name the three headers in its own signed `h=` list.
    Tests replace this."""
    msg = BytesParser(policy=policy.compat32).parsebytes(raw)
    results = msg.get_all("Authentication-Results", [])
    if not results or not str(results[0]).strip().lower().startswith("mx.google.com"):
        return False
    passed = [
        found.group(1) for found in re.finditer(r"dkim=pass[^;]*?header\.b=([A-Za-z0-9+/=]+)", str(results[0]), re.I)
    ]
    for signature in msg.get_all("DKIM-Signature", [])[:10]:
        tags = _tags(str(signature))
        names = {name.strip().lower().encode() for name in tags.get("h", "").split(":")}
        if SIGNED <= names and any(prefix and tags.get("b", "").startswith(prefix) for prefix in passed):
            return True
    return False


def endpoint(raw):
    """The one https one-click link of this raw message, or Unsupported with why."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    for name in ("List-Unsubscribe", "List-Unsubscribe-Post", "From"):
        if len(msg.get_all(name, [])) != 1:
            raise Unsupported("The message has no single one-click unsubscribe header; unsubscribe by hand.")
    if str(msg["List-Unsubscribe-Post"]).strip().lower() != "list-unsubscribe=one-click":
        raise Unsupported("The sender does not offer one-click unsubscribe.")
    urls = [u.strip() for u in re.findall(r"<([^<>]+)>", str(msg["List-Unsubscribe"]))]
    urls = [u for u in urls if u.lower().startswith("https:")]
    if len(urls) != 1:
        raise Unsupported("The message does not name exactly one https unsubscribe link.")
    try:
        egress.check(urls[0])
    except egress.Blocked as blocked:
        raise Unsupported(f"Not unsubscribing: {blocked.reason}.") from blocked
    if not verify_dkim(raw):
        raise Unsupported("No DKIM signature covering the unsubscribe headers verifies; unsubscribe by hand.")
    return urls[0]


def keep_reason(c, mailbox, original):
    """Why this mail is kept (never unsubscribed from), or '' when it may be. `original` is the normalized message."""
    sender = str(original.get("from") or "").strip().lower()
    box = mailbox.strip().lower()
    if not sender:
        return "the sender is unknown"
    if "STARRED" in (original.get("labels") or []):
        return "the message is starred"
    domains = mailbox_access.company_domains(c)
    domain = sender.rpartition("@")[2]
    if sender == box or any(domain == d or domain.endswith("." + d) for d in domains):
        return "the mail is internal"
    from . import views

    if sender in {str(p.get("email") or "").strip().lower() for p in views.roster(c)["people"]}:
        return "the sender is on the people roster"
    if mail_facts.sent_to(c, box, sender) or _answered_in_thread(c, box, original, sender):
        return "the mailbox already writes to the sender"
    return ""


def _answered_in_thread(c, mailbox, original, sender):
    """Whether the thread holds a message the mailbox sent to this sender (SENT, from the mailbox, to or cc them)."""
    if not original.get("thread_id"):
        return False
    for item in gmail_live.thread(c, mailbox, original["thread_id"]):
        if (
            "SENT" in (item.get("labels") or [])
            and str(item.get("from") or "").lower() == mailbox
            and sender in {a.lower() for a in (item.get("to") or []) + (item.get("cc") or [])}
        ):
            return True
    return False


def request_key(mailbox, url):
    return hashlib.sha256((mailbox.strip().lower() + "\n" + url).encode()).hexdigest()


def submit(store, mailbox, url, actor):
    """Send the one-click request once: {status, accepted, host, http_status, already_attempted}. `status` is accepted,
    manual_required (the sender answered with an error or a redirect), refused (the address is not one Tico sends to:
    nothing was sent) or unknown (no answer; never tried again)."""
    host = urlsplit(url).hostname or ""
    key = request_key(mailbox, url)
    with store.transaction(durable=True) as c:
        prior = c.execute("SELECT status,http_status FROM mail_unsubscribes WHERE key=?", (key,)).fetchone()
        if prior:
            return {
                "status": prior["status"],
                "accepted": prior["status"] == "accepted",
                "host": host,
                "http_status": prior["http_status"],
                "already_attempted": True,
            }
        c.execute(
            "INSERT INTO mail_unsubscribes(key,mailbox,host,status,actor,created_at) VALUES(?,?,?,'pending',?,?)",
            (key, mailbox, host, actor, H.now()),
        )
    code = None
    try:
        code = egress.post(url, BODY, HEADERS, transport=TRANSPORT).status
        status = "accepted" if 200 <= code < 300 else "manual_required"
    except egress.Blocked:
        status = "refused"
    except Exception:  # noqa: BLE001 - an unclear answer is unknown, never sent again
        status = "unknown"
    with store.transaction(durable=True) as c:
        c.execute(
            "UPDATE mail_unsubscribes SET status=?,http_status=?,finished_at=? WHERE key=?",
            (status, code, H.now(), key),
        )
    return {"status": status, "accepted": status == "accepted", "host": host, "http_status": code}


def unsubscribe(store, mailbox, msg_id, actor):
    """The whole request for one message: read it, keep what is kept, find its link, send once. Returns
    {ok, detail, ...}; a reason it is kept or unsupported is ok false with nothing sent."""
    if store.settings.rehearsal or store.settings.demo:
        raise Problem("rehearsal", "This install does not unsubscribe from live email", 403)
    with store.read() as c:
        try:
            original = gmail_live.message(c, mailbox, msg_id)
        except Problem as missing:
            if missing.code == "not_found":
                return {"ok": False, "detail": f"there is no message {msg_id} in {mailbox} any more"}
            raise
        why = keep_reason(c, mailbox, original)
        if why:
            return {"ok": False, "detail": f"not unsubscribing: {why}"}
        raw = gmail_live.raw_message(c, mailbox, msg_id)
    try:
        url = endpoint(raw)
    except Unsupported as unsupported:
        return {"ok": False, "detail": str(unsupported)}
    sent = submit(store, mailbox, url, actor)
    with store.transaction() as c:
        H.event(
            c,
            actor,
            "mail.unsubscribe",
            mailbox,
            {"mailbox": mailbox, "ids": [msg_id], "host": sent["host"], "status": sent["status"]},
        )
    if sent["accepted"]:
        detail = "already unsubscribed" if sent.get("already_attempted") else "unsubscribed"
        return {"ok": True, "detail": detail, "host": sent["host"]}
    return {"ok": False, "detail": f"not confirmed by the sender: {sent['status']}", "host": sent["host"]}
