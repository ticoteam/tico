"""A thin Gmail client plus the message normalizer.

`Gmail` wraps a googleapiclient service object: retries, pagination, and nothing else. Tests
pass a fake service with the same shape, so none of this needs the network. `history()` is
the incremental mailbox cursor; a 404 on the startHistoryId becomes `HistoryExpired` so the
caller can fall back to a query.

`normalize()` is pure: a raw Gmail message dict in, the shape every bot reads out - from, to,
cc, date in America/Los_Angeles, thread, labels, subject, snippet, a plain-text body (from
text/plain, or HTML stripped), attachments listed but not downloaded, and whether the message
carries an unsubscribe link.
"""

import base64, binascii, html, re, time
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from . import DEFAULT_TZ, Failure, INTERNAL_DOMAIN, MAX_BODY, TRUNCATED, zone

HISTORY_TYPES = ("messageAdded", "labelAdded", "labelRemoved", "messageDeleted")


class HistoryExpired(Failure):
    """Gmail no longer retains this startHistoryId; the caller should query instead."""

RETRY_STATUS = (403, 429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
INLINE_RAW_LIMIT = 4 * 1024 * 1024          # a bigger draft goes up as media (_draft_body)
ATTACHMENT_REF_RE = re.compile(r"a[1-9][0-9]{0,5}\Z")


# ---------------------------------------------------------------- client

def _status(exc):
    """HTTP status of a googleapiclient HttpError, without importing googleapiclient."""
    for attr in ("status_code",):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "resp", None)
    v = getattr(resp, "status", None)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _retryable(exc):
    st = _status(exc)
    if st is None or st not in RETRY_STATUS:
        return False
    if st == 403:                                       # only the rate-limit flavours of 403
        text = str(exc).lower()
        return "ratelimit" in text or "rate limit" in text or "userratelimitexceeded" in text
    return True


class Gmail:
    """Every Gmail call the connector makes goes through here."""

    def __init__(self, service, mailbox, sleep=time.sleep):
        self.service, self.mailbox, self.sleep = service, mailbox, sleep
        self._labels = None

    # -- plumbing ---------------------------------------------------
    def _exec(self, request, what):
        last = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return request.execute()
            except Exception as e:                      # HttpError, socket errors
                last = e
                if attempt == MAX_ATTEMPTS - 1 or not _retryable(e):
                    break
                self.sleep(2 ** attempt)
        raise Failure(f"Gmail {what} failed for {self.mailbox}: {last}",
                      gmail_hint(last, self.mailbox), status=_status(last))

    def _pages(self, method, params, key, limit=None, what="list"):
        out, token = [], None
        for _ in range(50):
            p = dict(params)
            if token:
                p["pageToken"] = token
            data = self._exec(method(userId="me", **p), what) or {}
            out.extend(data.get(key) or [])
            token = data.get("nextPageToken")
            if not token or (limit and len(out) >= limit):
                break
        return out[:limit] if limit else out

    # -- reads ------------------------------------------------------
    def profile(self):
        return self._exec(self.service.users().getProfile(userId="me"), "users.getProfile")

    def labels(self):
        """{id: name} for this mailbox, cached for the process."""
        if self._labels is None:
            data = self._exec(self.service.users().labels().list(userId="me"), "labels.list") or {}
            self._labels = {l["id"]: l.get("name", l["id"]) for l in (data.get("labels") or [])}
        return self._labels

    def label_ids(self):
        return {name: lid for lid, name in self.labels().items()}

    def create_label(self, name):
        body = {"name": name, "labelListVisibility": "labelShow",
                "messageListVisibility": "show"}
        lab = self._exec(self.service.users().labels().create(userId="me", body=body),
                         "labels.create")
        self._labels = None
        return lab

    def list_ids(self, query=None, label_ids=None, limit=100):
        params = {"maxResults": min(limit or 100, 500)}
        if query:
            params["q"] = query
        if label_ids:
            params["labelIds"] = list(label_ids)
        msgs = self._pages(self.service.users().messages().list, params, "messages",
                           limit=limit, what="messages.list")
        return [m["id"] for m in msgs if m.get("id")]

    def get(self, msg_id, fmt="full"):
        return self._exec(
            self.service.users().messages().get(userId="me", id=msg_id, format=fmt),
            "messages.get")

    def get_attachment(self, msg_id, attachment_id):
        """Fetch one attachment through the authenticated Gmail service."""
        return self._exec(
            self.service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=attachment_id),
            "messages.attachments.get")

    def thread(self, thread_id):
        data = self._exec(self.service.users().threads().get(userId="me", id=thread_id,
                                                             format="full"), "threads.get") or {}
        return data.get("messages") or []

    def history(self, start_history_id, history_types=None, limit=None):
        """users.history.list. Raises HistoryExpired when the cursor is too old (404).

        Returns {"history": [records], "historyId": "<new cursor>"}.
        """
        params = {
            "startHistoryId": str(start_history_id),
            "historyTypes": list(history_types or HISTORY_TYPES),
            "maxResults": min(limit or 100, 500),
        }
        out, token, latest = [], None, str(start_history_id)
        try:
            for _ in range(50):
                p = dict(params)
                if token:
                    p["pageToken"] = token
                data = self._exec(self.service.users().history().list(userId="me", **p),
                                  "history.list") or {}
                out.extend(data.get("history") or [])
                if data.get("historyId"):
                    latest = str(data["historyId"])
                token = data.get("nextPageToken")
                if not token or (limit and len(out) >= limit):
                    break
        except Failure as e:
            if e.status == 404 or _history_gone(e):
                raise HistoryExpired(
                    f"Gmail history id {start_history_id} has expired for {self.mailbox}",
                    "Fall back to after:<last_run − 2d>.", status=404) from e
            raise
        return {"history": out[:limit] if limit else out, "historyId": latest}

    # -- state changes ----------------------------------------------
    def modify(self, msg_id, add=(), remove=()):
        body = {"addLabelIds": list(add), "removeLabelIds": list(remove)}
        return self._exec(
            self.service.users().messages().modify(userId="me", id=msg_id, body=body),
            "messages.modify")

    # -- drafts and sending (stage 2; every caller is behind policy.py and lint.py) --
    def create_draft(self, raw, thread_id=""):
        body, media = _draft_body(raw, thread_id)
        return self._exec(self.service.users().drafts().create(userId="me", body=body, **media),
                          "drafts.create")

    def update_draft(self, draft_id, raw, thread_id=""):
        body, media = _draft_body(raw, thread_id)
        return self._exec(
            self.service.users().drafts().update(userId="me", id=draft_id, body=body, **media),
            "drafts.update")

    def get_draft(self, draft_id):
        return self._exec(self.service.users().drafts().get(userId="me", id=draft_id,
                                                            format="full"), "drafts.get")

    def delete_draft(self, draft_id):
        return self._exec(self.service.users().drafts().delete(userId="me", id=draft_id),
                          "drafts.delete")

    def send_draft(self, draft_id):
        """The only path out of the company. Policy, lint and the reviewer sit in front of it."""
        return self._exec(
            self.service.users().drafts().send(userId="me", body={"id": draft_id}),
            "drafts.send")


def _draft_body(raw, thread_id=""):
    """(body, extra kwargs) for drafts.create/update. A message with large attachments goes as a
    resumable media upload: a plain JSON request body is capped well below Gmail's 25 MB."""
    body = {"message": {"raw": raw}}
    if thread_id:
        body["message"]["threadId"] = thread_id
    if len(raw or "") <= INLINE_RAW_LIMIT:
        return body, {}
    from googleapiclient.http import MediaInMemoryUpload  # noqa: PLC0415  (only real sends get here)
    s = str(raw).replace("-", "+").replace("_", "/")
    mime = base64.b64decode(s + "=" * (-len(s) % 4))
    body = {"message": {"threadId": thread_id}} if thread_id else {}
    return body, {"media_body": MediaInMemoryUpload(mime, mimetype="message/rfc822",
                                                    resumable=True)}


def gmail_hint(exc, mailbox):
    text = str(exc or "").lower()
    if "unauthorized_client" in text or "not authorized" in text:
        return ("domain-wide delegation is not granted, or not for these scopes. Google Workspace "
                "Admin console > Security > Access and data control > API controls > Domain-wide "
                "delegation: add the service account's client id with gmail.modify and calendar.")
    if "invalid_grant" in text:
        return (f"Google will not let the service account act as {mailbox}. Check the address "
                "exists as a real user mailbox in this Workspace (not a group or an alias) and "
                "that the clock on this Mac is right.")
    if "insufficient" in text or "insufficientpermissions" in text:
        return "The delegation is missing the gmail.modify scope. Add it and try again."
    if "has not been used" in text or "accessnotconfigured" in text or "disabled" in text:
        return "Enable the Gmail API (and the Calendar API) in the Google Cloud project."
    if "notfound" in text or "not found" in text:
        return "No such message, thread, or label in this mailbox."
    return "Run `scripts/mail.sh doctor` for the full picture."


def _history_gone(exc):
    text = f"{getattr(exc, 'msg', '')} {exc}".lower()
    return "notfound" in text or "not found" in text


# ---------------------------------------------------------------- normalization (pure)

UNSUB_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
UNSUB_WORDS = ("unsubscribe", "opt-out", "optout", "opt_out", "email-preferences",
               "email_preferences", "manage-preferences", "list-unsubscribe")
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", re.I)
TAG_RE = re.compile(r"<[^>]+>")
DROP_RE = re.compile(r"<(script|style|head)\b.*?</\1>", re.I | re.S)
BREAK_RE = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h[1-6]|/table)[^>]*>")
BLANKS_RE = re.compile(r"\n{3,}")


def b64(data):
    if not data:
        return b""
    s = str(data).replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except Exception:
        return b""


def decode(part_body, charset="utf-8"):
    raw = b64((part_body or {}).get("data"))
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")               # pragma: no cover


def strip_html(source):
    """HTML -> readable plain text. Deliberately dumb: no parser, no dependency."""
    s = DROP_RE.sub(" ", source or "")
    s = BREAK_RE.sub("\n", s)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = "\n".join(re.sub(r"[ \t ]+", " ", ln).strip() for ln in s.split("\n"))
    return BLANKS_RE.sub("\n\n", s).strip()


def headers_of(part):
    out = {}
    for h in (part or {}).get("headers") or []:
        k = str(h.get("name", "")).lower()
        if k and k not in out:
            out[k] = h.get("value", "")
    return out


def charset_of(part):
    for h in (part or {}).get("headers") or []:
        if str(h.get("name", "")).lower() == "content-type":
            m = re.search(r"charset=([\w\-]+)", h.get("value", ""), re.I)
            if m:
                return m.group(1)
    return "utf-8"


def walk(part, out=None):
    out = [] if out is None else out
    if not part:
        return out
    out.append(part)
    for p in part.get("parts") or []:
        walk(p, out)
    return out


def safe_filename(name):
    """A display/suggestion name only; downloads never choose their own output path."""
    leaf = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf = re.sub(r"[^A-Za-z0-9._ -]+", "_", leaf).strip(" .-")
    while ".." in leaf:
        leaf = leaf.replace("..", ".")
    leaf = leaf[:180].rstrip(" .")
    return leaf or "attachment.bin"


def valid_attachment_ref(ref):
    return bool(ATTACHMENT_REF_RE.fullmatch(str(ref or "")))


def attachment_refs(payload):
    """Safe, repeatable references for downloadable parts in one Gmail payload tree."""
    found = []

    def visit(part, path):
        if not part:
            return
        mime = str(part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        original = str(part.get("filename") or "")
        disposition = str(headers_of(part).get("content-disposition") or "").lower()
        if original or mime == "text/calendar" or disposition.startswith("attachment"):
            try:
                size = max(0, int(body.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            attachment_id = str(body.get("attachmentId") or "")
            found.append({
                "ref": f"a{len(found) + 1}",
                "name": safe_filename(original or
                                      ("invite.ics" if mime == "text/calendar"
                                       else "attachment.bin")),
                "type": mime or "application/octet-stream",
                "size": size,
                "part_id": str(part.get("partId") or path),
                "attachment_id": attachment_id,
                "inline": "data" in body,
                "_data": body.get("data") if "data" in body else None,
            })
        for index, child in enumerate(part.get("parts") or []):
            visit(child, f"{path}.{index}" if path else str(index))

    visit(payload, "0")
    return found


def attachment_bytes(encoded, declared_size=0, limit=MAX_ATTACHMENT_BYTES):
    """Strictly decode a Gmail base64url payload, bounded before and after decoding."""
    try:
        declared = max(0, int(declared_size or 0))
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        raise Failure(f"attachment is {declared} bytes; the download limit is {limit} bytes.",
                      "Retrieve unusually large files directly in Gmail.")
    if encoded is None:
        raise Failure("Gmail returned an attachment without data.",
                      "Try the download again; if it repeats, open the message in Gmail.")
    try:
        raw = str(encoded).encode("ascii")
    except UnicodeEncodeError:
        raise Failure("Gmail returned invalid attachment encoding.",
                      "Try the download again; if it repeats, open the message in Gmail.")
    max_encoded = ((limit + 2) // 3) * 4 + 4
    if len(raw) > max_encoded:
        raise Failure(f"attachment exceeds the {limit}-byte download limit.",
                      "Retrieve unusually large files directly in Gmail.")
    raw += b"=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise Failure("Gmail returned invalid attachment encoding.",
                      "Try the download again; if it repeats, open the message in Gmail.")
    if len(decoded) > limit:
        raise Failure(f"attachment exceeds the {limit}-byte download limit.",
                      "Retrieve unusually large files directly in Gmail.")
    return decoded


def bodies_and_attachments(payload):
    """(plain text parts, html parts, attachments) from one payload tree."""
    plain, rich, atts = [], [], []
    for p in walk(payload):
        mime = str(p.get("mimeType") or "").lower()
        name = p.get("filename") or ""
        body = p.get("body") or {}
        if name:
            atts.append({"name": safe_filename(name), "type": mime or "application/octet-stream",
                         "size": int(body.get("size") or 0)})
            continue
        if mime == "text/plain":
            plain.append(decode(body, charset_of(p)))
        elif mime == "text/html":
            rich.append(decode(body, charset_of(p)))
        elif mime == "text/calendar":
            atts.append({"name": "invite.ics", "type": "text/calendar",
                         "size": int(body.get("size") or 0)})
    return plain, rich, atts


def truncate(text):
    """At most MAX_BODY characters, the note included: the hub refuses a longer body, and one such
    message refused every batch of its mailbox (a 32,809-character body)."""
    if len(text) <= MAX_BODY:
        return text, False
    return text[:MAX_BODY - len(TRUNCATED)] + TRUNCATED, True


def find_unsubscribe(header_value, body_text, html_text):
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
    if re.search(r"(?im)^\s*unsubscribe\s*:\s*(\S+)", body_text or ""):
        return True, re.search(r"(?im)^\s*unsubscribe\s*:\s*(\S+)", body_text).group(1)
    return False, ""


def addresses(value):
    return [a.lower() for _, a in getaddresses([value or ""]) if a]


def when_of(msg, hdrs, tz):
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


def normalize(msg, label_names=None, tz_name=DEFAULT_TZ):
    """Raw Gmail message dict -> the shape every command prints and every rule reads."""
    tz = zone(tz_name)
    payload = msg.get("payload") or {}
    hdrs = headers_of(payload)
    plain, rich, atts = bodies_and_attachments(payload)
    text = "\n\n".join(t for t in plain if t.strip()).strip()
    html_text = "\n\n".join(t for t in rich if t.strip())
    if not text and html_text:
        text = strip_html(html_text)
    body, cut = truncate(text)
    has_unsub, unsub_url = find_unsubscribe(hdrs.get("list-unsubscribe"), text, html_text)
    names = label_names or {}
    labels = [names.get(l, l) for l in (msg.get("labelIds") or [])]
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
        "is_internal": bool(everyone) and all(
            a.endswith("@" + INTERNAL_DOMAIN) or a.endswith("." + INTERNAL_DOMAIN)
            for a in everyone),
        "is_calendar_invite": any(a["type"] == "text/calendar" for a in atts)
        or "method=request" in str(hdrs.get("content-type", "")).lower()
        or any(a["name"].lower().endswith(".ics") for a in atts),
        "headers": hdrs,
    }


def thread_last_reply_by(messages, tz_name=DEFAULT_TZ):
    """The sender of the newest message in a thread, for the last_reply_by condition."""
    best, best_at = "", -1
    for m in messages:
        n = m if "from" in m and "epoch" in m else normalize(m, tz_name=tz_name)
        if n["epoch"] >= best_at:
            best, best_at = n["from"], n["epoch"]
    return best


# ---------------------------------------------------------------- rendering

def render_md(messages, title="Messages"):
    """One block per message. Model-readable, no tables, nothing to parse."""
    out = [f"# {title}", ""]
    if not messages:
        return "\n".join(out + ["Nothing to show.", ""])
    for m in messages:
        atts = ", ".join(f"{a['name']} ({a['type']}, {a['size']}B)" for a in m["attachments"])
        out += [
            f"## {m['subject'] or '(no subject)'}",
            f"id: {m['id']}",
            f"thread: {m['thread_id']}",
            f"from: {m['from_header'] or m['from']}",
            f"to: {', '.join(m['to']) or '-'}",
        ]
        if m["cc"]:
            out.append(f"cc: {', '.join(m['cc'])}")
        out += [
            f"date: {m['date']}",
            f"labels: {', '.join(m['labels']) or '-'}",
            f"unsubscribe: {'yes' if m['unsubscribe'] else 'no'}",
        ]
        if atts:
            out.append(f"attachments: {atts}")
        out += ["", "body:", m["body"] or "(empty)", ""]
    return "\n".join(out).rstrip() + "\n"


BRIEF_FROM, BRIEF_SUBJECT, BRIEF_SNIPPET = 40, 90, 160


def clip(text, n):
    """One line, at most n characters; a cut is marked with '...' inside the budget."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:max(n - 3, 0)] + "..."


def brief_from(m):
    """The display name when the header has one, else the address."""
    name, addr = parseaddr(m.get("from_header") or "")
    return name or addr or m.get("from") or "-"


def render_brief(messages, title="Messages"):
    """A listing a model can read at a glance: one block of four lines per message, no bodies.

    - <id>  <date>  <from>
      <subject>
      thread:<thread_id>  labels:<a,b or ->  unsub:<y/n>  att:<n or ->
      > <snippet>

    This is what an inbox bot lists with. It opens the few threads that need reading with
    `thread`, instead of receiving every body up front.
    """
    n = len(messages)
    out = [f"# {title}: {n} message{'' if n == 1 else 's'}"]
    for m in messages:
        date = (m.get("date") or "")[:16].replace("T", " ") or "-"
        labels = ",".join(m.get("labels") or []) or "-"
        atts = len(m.get("attachments") or [])
        out += [
            f"- {m.get('id', '')}  {date}  {clip(brief_from(m), BRIEF_FROM)}",
            f"  {clip(m.get('subject') or '(no subject)', BRIEF_SUBJECT)}",
            f"  thread:{m.get('thread_id', '')}  labels:{labels}  "
            f"unsub:{'y' if m.get('unsubscribe') else 'n'}  att:{atts or '-'}",
            f"  > {clip(m.get('snippet'), BRIEF_SNIPPET)}",
        ]
        if m.get("judgment"):                       # `inbox --judge` (connectors/mail/judge.py)
            from .judge import render_judgment
            out.append(render_judgment(m["judgment"]))
    return "\n".join(out) + "\n"
