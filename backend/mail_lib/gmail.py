# pyright: strict
"""The Gmail message normalizer and attachment decoding. Pure: nothing here talks to Google.

`normalize()` takes a raw Gmail message dict (the API's `format=full` shape) to the shape every bot reads: from, to, cc,
date in America/Los_Angeles, thread, labels, subject, snippet, a plain-text body (from text/plain, or HTML stripped),
attachments listed but not downloaded, and whether the message carries an unsubscribe link.

`attachment_refs()` names the downloadable parts of one message (a1, a2, ...) and `attachment_bytes()` decodes one,
bounded before and after decoding.
"""

import base64
import binascii
import html
import re
from datetime import datetime, timezone, tzinfo
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from ..html_convert import to_text
from . import DEFAULT_TZ, INTERNAL_DOMAIN, MAX_BODY, TRUNCATED, Failure, zone

Json = dict[str, Any]

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ATTACHMENT_REF_RE = re.compile(r"a[1-9][0-9]{0,5}\Z")

UNSUB_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
UNSUB_WORDS = (
    "unsubscribe",
    "opt-out",
    "optout",
    "opt_out",
    "email-preferences",
    "email_preferences",
    "manage-preferences",
    "list-unsubscribe",
)
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", re.I)
UNSUB_LINE_RE = re.compile(r"(?im)^\s*unsubscribe\s*:\s*(\S+)")


def b64(data: object) -> bytes:
    if not data:
        return b""
    s = str(data).replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except ValueError:  # binascii.Error, and a str that is not ASCII
        return b""


def decode(part_body: Json | None, charset: str = "utf-8") -> str:
    raw = b64((part_body or {}).get("data"))
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")  # pragma: no cover - latin-1 decodes every byte, so the loop always returns


def strip_html(source: str | None) -> str:
    """HTML -> readable plain text: a line per paragraph, list item or table row; nothing hidden or in a tag."""
    return to_text(source or "")[0]


def headers_of(part: Json | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    headers: list[Json] = (part or {}).get("headers") or []
    for h in headers:
        k = str(h.get("name", "")).lower()
        if k and k not in out:
            out[k] = h.get("value", "")
    return out


def charset_of(part: Json | None) -> str:
    headers: list[Json] = (part or {}).get("headers") or []
    for h in headers:
        if str(h.get("name", "")).lower() == "content-type":
            m = re.search(r"charset=([\w\-]+)", h.get("value", ""), re.I)
            if m:
                return m.group(1)
    return "utf-8"


def walk(part: Json | None, out: list[Json] | None = None) -> list[Json]:
    out = [] if out is None else out
    if not part:
        return out
    out.append(part)
    children: list[Json] = part.get("parts") or []
    for p in children:
        walk(p, out)
    return out


def safe_filename(name: object) -> str:
    """A display/suggestion name only; downloads never choose their own output path."""
    leaf = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf = re.sub(r"[^A-Za-z0-9._ -]+", "_", leaf).strip(" .-")
    while ".." in leaf:
        leaf = leaf.replace("..", ".")
    leaf = leaf[:180].rstrip(" .")
    return leaf or "attachment.bin"


def valid_attachment_ref(ref: object) -> bool:
    return bool(ATTACHMENT_REF_RE.fullmatch(str(ref or "")))


def attachment_refs(payload: Json | None) -> list[Json]:
    """Safe, repeatable references for downloadable parts in one Gmail payload tree."""
    found: list[Json] = []

    def visit(part: Json | None, path: str) -> None:
        if not part:
            return
        mime = str(part.get("mimeType") or "").lower()
        body: Json = part.get("body") or {}
        original = str(part.get("filename") or "")
        disposition = str(headers_of(part).get("content-disposition") or "").lower()
        if original or mime == "text/calendar" or disposition.startswith("attachment"):
            try:
                size = max(0, int(body.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            attachment_id = str(body.get("attachmentId") or "")
            found.append(
                {
                    "ref": f"a{len(found) + 1}",
                    "name": safe_filename(original or ("invite.ics" if mime == "text/calendar" else "attachment.bin")),
                    "type": mime or "application/octet-stream",
                    "size": size,
                    "part_id": str(part.get("partId") or path),
                    "attachment_id": attachment_id,
                    "inline": "data" in body,
                    "_data": body.get("data") if "data" in body else None,
                }
            )
        children: list[Json] = part.get("parts") or []
        for index, child in enumerate(children):
            visit(child, f"{path}.{index}" if path else str(index))

    visit(payload, "0")
    return found


def attachment_bytes(encoded: Any, declared_size: Any = 0, limit: int = MAX_ATTACHMENT_BYTES) -> bytes:
    """Strictly decode a Gmail base64url payload, bounded before and after decoding. Failure when it is too big, has no
    data or is not valid base64url."""
    try:
        declared = max(0, int(declared_size or 0))
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        raise Failure(
            f"attachment is {declared} bytes; the download limit is {limit} bytes.",
            "Retrieve unusually large files directly in Gmail.",
        )
    if encoded is None:
        raise Failure(
            "Gmail returned an attachment without data.",
            "Try the download again; if it repeats, open the message in Gmail.",
        )
    try:
        raw = str(encoded).encode("ascii")
    except UnicodeEncodeError as e:
        raise Failure(
            "Gmail returned invalid attachment encoding.",
            "Try the download again; if it repeats, open the message in Gmail.",
        ) from e
    max_encoded = ((limit + 2) // 3) * 4 + 4
    if len(raw) > max_encoded:
        raise Failure(
            f"attachment exceeds the {limit}-byte download limit.", "Retrieve unusually large files directly in Gmail."
        )
    raw += b"=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as e:
        raise Failure(
            "Gmail returned invalid attachment encoding.",
            "Try the download again; if it repeats, open the message in Gmail.",
        ) from e
    if len(decoded) > limit:
        raise Failure(
            f"attachment exceeds the {limit}-byte download limit.", "Retrieve unusually large files directly in Gmail."
        )
    return decoded


def bodies_and_attachments(payload: Json | None) -> tuple[list[str], list[str], list[Json]]:
    """(plain text parts, html parts, attachments) from one payload tree."""
    plain: list[str] = []
    rich: list[str] = []
    atts: list[Json] = []
    for p in walk(payload):
        mime = str(p.get("mimeType") or "").lower()
        name = p.get("filename") or ""
        body: Json = p.get("body") or {}
        if name:
            atts.append(
                {
                    "name": safe_filename(name),
                    "type": mime or "application/octet-stream",
                    "size": int(body.get("size") or 0),
                }
            )
            continue
        if mime == "text/plain":
            plain.append(decode(body, charset_of(p)))
        elif mime == "text/html":
            rich.append(decode(body, charset_of(p)))
        elif mime == "text/calendar":
            atts.append({"name": "invite.ics", "type": "text/calendar", "size": int(body.get("size") or 0)})
    return plain, rich, atts


def truncate(text: str) -> tuple[str, bool]:
    """At most MAX_BODY characters, the note included: the hub refuses a longer body, and one such
    message refused every batch of its mailbox (a 32,809-character body)."""
    if len(text) <= MAX_BODY:
        return text, False
    return text[: MAX_BODY - len(TRUNCATED)] + TRUNCATED, True


def find_unsubscribe(header_value: str | None, body_text: str | None, html_text: str | None) -> tuple[bool, str]:
    """(bool, url or ''). The List-Unsubscribe header first, then links in the body."""
    header_value = header_value or ""
    urls = URL_RE.findall(header_value)
    if header_value.strip():
        return True, (urls[0].rstrip(">,") if urls else "")
    for href in UNSUB_HREF_RE.findall(html_text or ""):
        if any(w in href.lower() for w in UNSUB_WORDS):
            return True, html.unescape(href)
    # A link whose visible text is "unsubscribe" but whose href is a tracking redirect.
    for chunk in re.split(r"(?i)<a\b", html_text or "")[1:]:
        head, _, tail = chunk.partition(">")
        if any(w in strip_html(tail[:400]).lower() for w in UNSUB_WORDS):
            m = UNSUB_HREF_RE.search("<a " + head + ">")
            if m:
                return True, html.unescape(m.group(1))
    for url in URL_RE.findall(body_text or ""):
        if any(w in url.lower() for w in UNSUB_WORDS):
            return True, url
    line = UNSUB_LINE_RE.search(body_text or "")
    if line:
        return True, line.group(1)
    return False, ""


def addresses(value: str | None) -> list[str]:
    return [a.lower() for _, a in getaddresses([value or ""]) if a]


def when_of(msg: Json, hdrs: dict[str, Any], tz: tzinfo) -> datetime | None:
    ms = msg.get("internalDate")
    if ms:
        try:
            return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).astimezone(tz)
        except (TypeError, ValueError, OSError):
            pass
    try:
        dt = parsedate_to_datetime(hdrs.get("date", ""))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(tz)
    except (TypeError, ValueError):
        return None


def _joined(plain: list[str], rich: list[str]) -> tuple[str, str]:
    text = "\n\n".join(t for t in plain if t.strip()).strip()
    html_text = "\n\n".join(t for t in rich if t.strip())
    if not text and html_text:
        text = strip_html(html_text)
    return text, html_text


def body_text(payload: Json | None) -> tuple[str, str]:
    """(text, html) of one payload, not truncated: its plain parts, or its HTML stripped when it has none."""
    plain, rich, _ = bodies_and_attachments(payload)
    return _joined(plain, rich)


def normalize(msg: Json, label_names: dict[str, str] | None = None, tz_name: str = DEFAULT_TZ) -> Json:
    """Raw Gmail message dict -> the shape every command prints and every rule reads."""
    tz = zone(tz_name)
    payload: Json = msg.get("payload") or {}
    hdrs = headers_of(payload)
    plain, rich, atts = bodies_and_attachments(payload)
    text, html_text = _joined(plain, rich)
    body, cut = truncate(text)
    has_unsub, unsub_url = find_unsubscribe(hdrs.get("list-unsubscribe"), text, html_text)
    names = label_names or {}
    label_ids: list[str] = msg.get("labelIds") or []
    labels = [names.get(label, label) for label in label_ids]
    frm = addresses(hdrs.get("from"))
    to, cc = addresses(hdrs.get("to")), addresses(hdrs.get("cc"))
    when = when_of(msg, hdrs, tz)
    everyone = frm + to + cc
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "date": when.isoformat(timespec="seconds") if when else "",
        "epoch": int(when.timestamp()) if when else 0,
        "from": frm[0] if frm else "",
        "from_header": hdrs.get("from", ""),
        "to": to,
        "cc": cc,
        "subject": hdrs.get("subject", ""),
        "snippet": html.unescape(msg.get("snippet") or "").strip(),
        "labels": labels,
        "body": body,
        "body_truncated": cut,
        "attachments": atts,
        "unsubscribe": has_unsub,
        "unsubscribe_url": unsub_url,
        "list_id": hdrs.get("list-id", ""),
        "is_internal": bool(everyone)
        and all(a.endswith("@" + INTERNAL_DOMAIN) or a.endswith("." + INTERNAL_DOMAIN) for a in everyone),
        "is_calendar_invite": any(a["type"] == "text/calendar" for a in atts)
        or "method=request" in str(hdrs.get("content-type", "")).lower()
        or any(a["name"].lower().endswith(".ics") for a in atts),
        "headers": hdrs,
        "send_facts": {
            **{
                key: str(hdrs.get(key) or "")[:1000]
                for key in ("auto-submitted", "precedence", "return-path", "message-id")
            },
            "delivery-status": "\n".join(
                decode(p.get("body") or {}, charset_of(p))
                for p in walk(payload)
                if p.get("mimeType") == "message/delivery-status"
            )[:4096],
        },
    }
