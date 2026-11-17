"""One-click unsubscribe (RFC 8058) from the server, for an automation's action or a bot's or person's own request.

The link is the message's own List-Unsubscribe header, used only when the message carries exactly one https link,
asks for one-click (List-Unsubscribe-Post) and a DKIM signature of the From address's own domain over From and both
headers verifies here, with the signer's published key (`endpoint`, `verify_dkim`): mail cannot name an address for
Tico to call by saying so, and only the sender's own domain chooses where the request goes. The request goes out through backend/egress.py (https to a public
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
    """A DKIM tag list (`v=1; a=rsa-sha256; ...`) as {tag: value}, folding whitespace removed."""
    out = {}
    for part in value.split(";"):
        name, eq, tagged = part.partition("=")
        if eq:
            out[name.strip().lower()] = re.sub(r"\s+", "", tagged)
    return out


def _fields(raw):
    """(the header fields in order as (name, raw bytes with their folding, without the final CRLF), the body), from a
    message with CRLF or LF line ends."""
    raw = re.sub(rb"\r?\n", b"\r\n", raw)
    head, _, body = raw.partition(b"\r\n\r\n")
    fields = []
    for line in head.split(b"\r\n"):
        if line[:1] in (b" ", b"\t") and fields:
            fields[-1] = (fields[-1][0], fields[-1][1] + b"\r\n" + line)
        elif b":" in line:
            fields.append((line.split(b":", 1)[0].strip().lower().decode("ascii", "replace"), line))
    return fields, body


def _canon_header(field, relaxed):
    if not relaxed:
        return field + b"\r\n"
    name, _, value = field.partition(b":")
    value = re.sub(rb"[ \t]+", b" ", re.sub(rb"\r\n(?=[ \t])", b"", value)).strip(b" ")
    return name.strip().lower() + b":" + value + b"\r\n"


def _canon_body(body, relaxed):
    if relaxed:
        lines = [re.sub(rb"[ \t]+", b" ", line).rstrip(b" ") for line in body.split(b"\r\n")]
        body = b"\r\n".join(lines)
    body = body.rstrip(b"\r\n")
    return body + b"\r\n" if body or not relaxed else b""


def dkim_key(selector, domain):
    """The public key record (`v=DKIM1; k=rsa; p=...`) at <selector>._domainkey.<domain>, or ''. Tests replace this."""
    import dns.resolver

    try:
        answers = dns.resolver.resolve(f"{selector}._domainkey.{domain}", "TXT", lifetime=5)
    except Exception:  # noqa: BLE001 - no key is no verification
        return ""
    return "".join(b"".join(r.strings).decode("ascii", "replace") for r in answers)[:8192]


def _verified(tags, data, signature):
    """Whether `signature` over `data` verifies with the signer's published key (rsa-sha256 or ed25519-sha256)."""
    import base64
    import hashlib

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

    record = _tags(dkim_key(tags.get("s", ""), tags.get("d", "")))
    try:
        key = base64.b64decode(record.get("p", ""), validate=True)
        if not key:
            return False  # a revoked key
        if tags.get("a") == "rsa-sha256" and record.get("k", "rsa") == "rsa":
            public = serialization.load_der_public_key(key)
            if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 1024:
                return False
            public.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
            return True
        if tags.get("a") == "ed25519-sha256" and record.get("k") == "ed25519":
            ed25519.Ed25519PublicKey.from_public_bytes(key).verify(signature, hashlib.sha256(data).digest())
            return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    return False


def verify_dkim(raw):
    """Whether a DKIM signature (RFC 6376) of the From address's own domain verifies over this raw message and signs
    From and both unsubscribe headers, checked here with the signer's published key: the body hash, then the
    signature over the headers its `h=` names. A signature with a body length limit (`l=`) is not used: text could be
    added after the signed part. Tests replace this."""
    import base64
    import hashlib

    fields, body = _fields(raw)
    sender = ""
    for name, field in fields:
        if name == "from":
            found = re.findall(rb"[\w.+%'-]+@([\w-]+(?:\.[\w-]+)+)", field)
            sender = found[-1].decode().lower() if found else ""
    for index, (name, field) in enumerate(fields):
        if name != "dkim-signature":
            continue
        tags = _tags(field.split(b":", 1)[1].decode("ascii", "replace"))
        domain = tags.get("d", "").lower()
        signed = [h.strip().lower() for h in tags.get("h", "").split(":") if h.strip()]
        if (
            tags.get("v") != "1"
            or "l" in tags
            or not domain
            or not (sender == domain or sender.endswith("." + domain))
            or not {h.decode() for h in SIGNED} <= set(signed)
        ):
            continue
        header_way, _, body_way = tags.get("c", "simple/simple").partition("/")
        canonical = _canon_body(body, (body_way or "simple") == "relaxed")
        try:
            if base64.b64decode(tags.get("bh", ""), validate=True) != hashlib.sha256(canonical).digest():
                continue
            signature = base64.b64decode(tags.get("b", ""), validate=True)
        except ValueError:
            continue
        relaxed = header_way == "relaxed"
        remaining = [(n, f) for i, (n, f) in enumerate(fields) if i != index]
        data = b""
        for wanted in signed:  # each name takes the last unused field of that name, bottom up (RFC 6376 5.4.2)
            for at in range(len(remaining) - 1, -1, -1):
                if remaining[at][0] == wanted:
                    data += _canon_header(remaining.pop(at)[1], relaxed)
                    break
        own = re.sub(rb"(\bb\s*=)[^;]*", rb"\1", field)
        data += _canon_header(own, relaxed)[:-2]
        if _verified(tags, data, signature):
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
