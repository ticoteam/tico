"""Read, organize and send mail in Gmail, without retaining messages on the server.

Callers check mailbox access before asking for a token. Every token comes from the one door, backend/google_tokens.py:
the Workspace key or the person's own Connect Google grant. `send` delivers one frozen email the send queue has already
permitted (backend/email_sending.py `drain`); it never retries. Only the token caches survive a call;
the connector's pure normalizer supplies the same message fields without loading Google client libraries.

Drafts are found again by the Message-ID Tico gave them (`draft_find`, Gmail's `rfc822msgid:` search over drafts), so a
retried draft or automation action finds the draft it made instead of making a second one.
"""

import base64
import builtins
import json
import uuid
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import google_tokens
from .email_content import MAX_RAW
from .mail_lib.gmail import normalize
from .store import Problem

BASE = "https://gmail.googleapis.com/gmail/v1/users/"
UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/"
SEND_SECONDS = 60
INLINE_RAW_MAX = 4 * 1024 * 1024  # a bigger email goes up as a multipart upload: a JSON body is capped lower
SECONDS = 10
LIMIT_MAX = 50
WORKERS = 10
HISTORY_MAX = 100  # message ids one history call returns (whole records); the rest wait for the next
HISTORY_PAGES = 20
SCAN_MAX = 20000  # message ids `all_ids` lists
HTTP_MAX_BYTES = 8 * 1024 * 1024
PAGE_MAX_BYTES = 256 * 1024
OPEN_MAX_BYTES = 2 * 1024 * 1024
METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Date", "List-Id", "List-Unsubscribe")
MUTABLE_SYSTEM_LABELS = frozenset(("INBOX", "UNREAD", "STARRED", "IMPORTANT"))
PROTECTED_LABELS = frozenset(("TRASH", "SPAM", "SENT", "DRAFT", "DRAFTS"))


ATTACHMENT_MAX_BYTES = 6 * 1024 * 1024  # one attachment read through Tico; a bigger one is opened in Gmail


def gmail_call(url, *, token, data=None, max_bytes=HTTP_MAX_BYTES):
    """(status, JSON object), or status 0 when Gmail cannot answer. Tests replace this function."""
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": "Bearer " + token})
    if data is not None:
        request.data = json.dumps(data).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            status, raw = answer.status, answer.read(max_bytes + 1)
    except urllib.error.HTTPError as refused:
        with refused:
            refused.close()
            return refused.code, {}
    except (OSError, ValueError):
        return 0, {}
    if len(raw) > max_bytes:
        raise Problem("gmail_size", "This Gmail response is too large; open it in Gmail", 502)
    if not raw and data is not None and status in (200, 204):
        return status, {}
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return 0, {}
    return (status, body) if isinstance(body, dict) else (0, {})


def gmail_delete_call(url, *, token):
    """The HTTP status of one DELETE, or 0 when Gmail cannot answer. Tests replace this function."""
    request = urllib.request.Request(url, method="DELETE", headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            return answer.status
    except urllib.error.HTTPError as refused:
        with refused:
            refused.close()
            return refused.code
    except (OSError, ValueError):
        return 0


def _call(mailbox, path, token, *, data=None, max_bytes=None, **query):
    url = BASE + urllib.parse.quote(mailbox, safe="") + "/" + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    status, body = gmail_call(
        url,
        token=token,
        **({"data": data} if data is not None else {}),
        **({"max_bytes": max_bytes} if max_bytes else {}),
    )
    if status in (200, 201, 204) and isinstance(body, dict) and "error" not in body:
        return body
    if status == 400:
        raise Problem("gmail_query", "Gmail refused the query or page token; check it and try again", 422)
    if status in (401, 403):
        verb = "change" if data is not None else "read"
        raise Problem(
            "gmail_refused", f"Gmail refused this {verb}; check the Whole Workspace connection and Gmail access", 403
        )
    if status == 404:
        raise Problem("not_found", "Gmail could not find this mailbox, thread or message", 404)
    if status == 429:
        raise Problem("gmail_unavailable", "Gmail's quota was reached; try again shortly", 503, retryable=True)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)


def _get(mailbox, path, token, **query):
    return _call(mailbox, path, token, **query)


def _token(c, mailbox, write=False):
    return google_tokens.token(c, mailbox, "gmail", write=write)


def _message(raw):
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("threadId"):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete message; try again", 502, retryable=True)
    return normalize(raw)


def _bounded(value, cap):
    if len(json.dumps(value).encode()) > cap:
        raise Problem("gmail_size", "This Gmail response is too large; narrow the search or open it in Gmail", 502)
    return value


def list(c, mailbox, q="", page_token="", limit=50):
    """One bounded page of metadata, with Gmail's opaque continuation token."""
    cap = min(LIMIT_MAX, max(1, limit))
    token = _token(c, mailbox)
    query = {"maxResults": cap, "fields": "messages(id),nextPageToken"}
    if q:
        query["q"] = q
    if page_token:
        query["pageToken"] = page_token
    page = _get(mailbox, "messages", token, **query)
    ids = page.get("messages", [])
    nxt = page.get("nextPageToken") or None
    if (
        not isinstance(ids, builtins.list)
        or len(ids) > cap
        or any(not isinstance(item, dict) or not item.get("id") for item in ids)
        or (nxt is not None and not isinstance(nxt, str))
    ):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502, retryable=True)

    messages = _metadata(mailbox, token, [item["id"] for item in ids])
    return _bounded({"messages": messages, "next_cursor": nxt, "q": q}, PAGE_MAX_BYTES)


def _parts_mask(depth):
    inner = "," + _parts_mask(depth - 1) if depth > 1 else ""
    return "parts(mimeType,filename,body/size" + inner + ")"


# A list row reads the parts tree without any part's bytes: format=metadata leaves out `parts`, so every row said it
# had no attachment (a sent email with its PDF listed has_attachment false). Six levels hold mixed > related >
# alternative and a forwarded message inside.
LIST_FIELDS = (
    "id,threadId,labelIds,snippet,internalDate,payload(mimeType,filename,headers,body/size," + _parts_mask(6) + ")"
)
LIST_HEADERS = frozenset(name.lower() for name in METADATA_HEADERS)


def _listed(raw):
    """One message read with LIST_FIELDS, its headers cut to the ones a metadata read gives."""
    payload = raw.get("payload") if isinstance(raw, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("headers"), builtins.list):
        payload["headers"] = [
            h for h in payload["headers"] if isinstance(h, dict) and str(h.get("name", "")).lower() in LIST_HEADERS
        ]
    return _message(raw)


def _metadata(mailbox, token, ids):
    def one(mid):
        return _listed(
            _get(mailbox, "messages/" + urllib.parse.quote(mid, safe=""), token, format="full", fields=LIST_FIELDS)
        )

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return builtins.list(pool.map(one, ids))


def metadata(c, mailbox, ids):
    """These messages' headers and snippets (no bodies), in the order given; at most LIMIT_MAX."""
    if len(ids) > LIMIT_MAX:
        raise Problem("gmail_size", "Read at most 50 messages at once", 422)
    return _bounded(_metadata(mailbox, _token(c, mailbox), builtins.list(ids)), PAGE_MAX_BYTES)


def first(c, mailbox, q):
    """The newest message the query finds, as {id, threadId}, or None: one list call, no message read."""
    page = _get(mailbox, "messages", _token(c, mailbox), q=q, maxResults=1, fields="messages(id,threadId)")
    rows = page.get("messages") or []
    if not isinstance(rows, builtins.list) or any(not isinstance(row, dict) or not row.get("id") for row in rows):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502, retryable=True)
    return {"id": str(rows[0]["id"]), "threadId": str(rows[0].get("threadId") or "")} if rows else None


def thread(c, mailbox, thread_id):
    raw = _get(mailbox, "threads/" + urllib.parse.quote(thread_id, safe=""), _token(c, mailbox), format="full")
    messages = raw.get("messages")
    if not isinstance(messages, builtins.list) or not messages:
        raise Problem("gmail_unavailable", "Gmail returned an incomplete thread; try again", 502, retryable=True)
    return _bounded(
        sorted((_message(item) for item in messages), key=lambda item: (item["epoch"], item["id"])), OPEN_MAX_BYTES
    )


def message(c, mailbox, message_id):
    raw = _get(mailbox, "messages/" + urllib.parse.quote(message_id, safe=""), _token(c, mailbox), format="full")
    return _bounded(_message(raw), OPEN_MAX_BYTES)


def message_ids(c, mailbox, query, limit=100):
    """Resolve a bounded selection before changing labels, so paging never sees our own changes."""
    cap = min(500, max(1, limit))
    token, ids, seen_pages, cursor = _token(c, mailbox), [], set(), ""
    while len(ids) < cap:
        params = {"q": query, "maxResults": cap - len(ids), "fields": "messages(id),nextPageToken"}
        if cursor:
            params["pageToken"] = cursor
        page = _get(mailbox, "messages", token, **params)
        rows, cursor = page.get("messages", []), page.get("nextPageToken") or ""
        if (
            not isinstance(rows, builtins.list)
            or len(rows) > cap - len(ids)
            or any(
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"]
                or not row["id"].isascii()
                or len(row["id"]) > 128
                for row in rows
            )
            or not isinstance(cursor, str)
            or cursor in seen_pages
        ):
            raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502)
        ids.extend(row["id"] for row in rows if row["id"] not in ids)
        if not cursor:
            break
        seen_pages.add(cursor)
        if len(seen_pages) >= 500:
            raise Problem("gmail_unavailable", "Gmail paging did not finish; narrow the query", 502)
    return ids


def profile(c, mailbox):
    """The mailbox's current Gmail history id: where a first watch starts following it."""
    found = _get(mailbox, "profile", _token(c, mailbox), fields="historyId").get("historyId")
    if not isinstance(found, (str, int)) or not str(found).isdigit():
        raise Problem("gmail_unavailable", "Gmail returned an incomplete profile; try again", 502, retryable=True)
    return str(found)


def history(c, mailbox, start, limit=HISTORY_MAX):
    """The history records since history id `start`, oldest first, as [(record id, [message ids added])], and the
    newest history id once every record was returned (None when more wait). A record is never split: records are added
    until they hold `limit` message ids, so a record of more than `limit` messages comes whole, alone. The caller
    saves a record's id once it has heard all its messages. An expired `start` is Problem `not_found` (404)."""
    token, records, cursor, count, pages = _token(c, mailbox), [], "", 0, 0
    while True:
        query = {"startHistoryId": start, "historyTypes": "messageAdded", "maxResults": 500}
        if cursor:
            query["pageToken"] = cursor
        page = _get(mailbox, "history", token, **query)
        rows, cursor, newest = page.get("history") or [], page.get("nextPageToken") or "", page.get("historyId")
        if not isinstance(rows, builtins.list) or not isinstance(cursor, str):
            raise Problem("gmail_unavailable", "Gmail returned an incomplete history; try again", 502, retryable=True)
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("id") or "").isdigit():
                raise Problem(
                    "gmail_unavailable", "Gmail returned an incomplete history; try again", 502, retryable=True
                )
            if records and count >= limit:
                return records, None
            added = row.get("messagesAdded")
            ids = []
            for item in added if isinstance(added, builtins.list) else ():
                mid = ((item or {}).get("message") or {}).get("id") if isinstance(item, dict) else None
                if isinstance(mid, str) and mid and mid not in ids:
                    ids.append(mid)
            records.append((str(row["id"]), ids))
            count += len(ids)
        pages += 1
        if not cursor:
            newest = str(newest or "")
            return records, newest if newest.isdigit() else None
        if pages >= HISTORY_PAGES:
            return records, None


def all_ids(c, mailbox, query, cap=SCAN_MAX):
    """Every message id the query finds, newest first (Gmail's order), paging through: at most `cap`."""
    token, ids, cursor, seen = _token(c, mailbox), [], "", set()
    while len(ids) < cap:
        params = {"q": query, "maxResults": 500, "fields": "messages(id),nextPageToken"}
        if cursor:
            params["pageToken"] = cursor
        page = _get(mailbox, "messages", token, **params)
        rows, cursor = page.get("messages") or [], page.get("nextPageToken") or ""
        if (
            not isinstance(rows, builtins.list)
            or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in rows)
            or not isinstance(cursor, str)
            or cursor in seen
        ):
            raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502, retryable=True)
        ids.extend(row["id"] for row in rows)
        if not cursor:
            break
        seen.add(cursor)
    return ids[:cap]


def label_total(c, mailbox, label_id):
    """How many messages carry this label now (Gmail's own count; nothing is listed)."""
    found = _get(
        mailbox, "labels/" + urllib.parse.quote(label_id, safe=""), _token(c, mailbox), fields="messagesTotal"
    ).get("messagesTotal", 0)
    return found if isinstance(found, int) and found >= 0 else 0


def labels(c, mailbox):
    rows = _get(mailbox, "labels", _token(c, mailbox)).get("labels")
    if not isinstance(rows, builtins.list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not row["id"]
        or not isinstance(row.get("name"), str)
        or not row["name"]
        or row.get("type") not in ("user", "system")
        for row in rows
    ):
        raise Problem("gmail_unavailable", "Gmail returned incomplete labels; try again", 502)
    return rows


def resolve_label(c, mailbox, name, *, create=False):
    """Only the label action may create; system labels always pass the same allowlist."""
    if name.upper() in PROTECTED_LABELS:
        raise Problem("gmail_label", "Tico may not change this Gmail system label", 422)
    rows = labels(c, mailbox)
    for row in rows:
        if name in (row["name"], row["id"]) or (row["type"] == "system" and name.upper() == row["id"]):
            if row["type"] != "user" and row["id"] not in MUTABLE_SYSTEM_LABELS:
                raise Problem("gmail_label", "Tico may not change this Gmail system label", 422)
            return row["id"]
    if not create:
        raise Problem("not_found", "Gmail could not find this label", 404)
    token = _token(c, mailbox, write=True)
    row = _call(mailbox, "labels", token, data={"name": name})
    if not row.get("id") or row.get("type") != "user":
        raise Problem("gmail_unavailable", "Gmail returned an incomplete label; try again", 502)
    return row["id"]


def organize(c, mailbox, ids, add=(), remove=()):
    """Change only safe system labels or verified user-label IDs. There is no delete or trash operation."""
    changes = set(add) | set(remove)
    if changes & PROTECTED_LABELS:
        raise Problem("gmail_label", "Tico may not change this Gmail system label", 422)
    unknown = changes - MUTABLE_SYSTEM_LABELS
    if unknown and not unknown <= {row["id"] for row in labels(c, mailbox) if row["type"] == "user"}:
        raise Problem("gmail_label", "Tico may only change INBOX, UNREAD, STARRED, IMPORTANT or user labels", 422)
    if not ids:
        return
    token = _token(c, mailbox, write=True)
    for start in range(0, len(ids), 1000):
        _call(
            mailbox,
            "messages/batchModify",
            token,
            data={
                "ids": ids[start : start + 1000],
                "addLabelIds": builtins.list(add),
                "removeLabelIds": builtins.list(remove),
            },
        )


def send_call(url, *, token, body, content_type):
    """(status, JSON object) for one messages.send or large drafts.create, or status 0 when Gmail did not answer.
    Tests replace this."""
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": content_type, "Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_SECONDS) as answer:
            status, raw = answer.status, answer.read(HTTP_MAX_BYTES)
    except urllib.error.HTTPError as refused:
        with refused:
            refused.close()
            return refused.code, {}
    except (OSError, ValueError):
        return 0, {}
    try:
        found = json.loads(raw)
    except (ValueError, UnicodeError):
        return status, {}
    return status, found if isinstance(found, dict) else {}


def _upload_body(raw, meta):
    """(body, content type) of a Gmail multipart upload: the `meta` JSON, then the MIME of `raw` (base64url)."""
    padded = raw.replace("-", "+").replace("_", "/")
    mime = base64.b64decode(padded + "=" * (-len(padded) % 4))
    boundary = "tico-" + uuid.uuid4().hex
    body = (
        b"--"
        + boundary.encode()
        + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        + json.dumps(meta).encode()
        + b"\r\n--"
        + boundary.encode()
        + b"\r\nContent-Type: message/rfc822\r\n\r\n"
        + mime
        + b"\r\n--"
        + boundary.encode()
        + b"--"
    )
    return body, "multipart/related; boundary=" + boundary


def send(mailbox, token, raw, thread_id=""):
    """Send exactly this frozen MIME once: (state, Gmail message id, detail). Gmail refusing it is `failed` (nothing
    left); no answer, a server error or an answer without an id is `unknown`, never retried into a second copy."""
    path = urllib.parse.quote(mailbox, safe="") + "/messages/send"
    if len(raw) <= INLINE_RAW_MAX:
        body = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        status, found = send_call(
            BASE + path, token=token, body=json.dumps(body).encode(), content_type="application/json"
        )
    else:
        body, content_type = _upload_body(raw, {"threadId": thread_id} if thread_id else {})
        status, found = send_call(
            UPLOAD + path + "?uploadType=multipart", token=token, body=body, content_type=content_type
        )
    if status in (200, 201) and found.get("id"):
        return "sent", str(found["id"])[:200], "sent"
    if 400 <= status < 500:
        return "failed", "", f"Gmail refused the email (HTTP {status}); nothing was sent"
    return "unknown", "", "Gmail did not confirm delivery"


def payload(c, mailbox, message_id):
    """One message as Gmail has it (format full): its parts tree, for the attachments a message carries."""
    raw = _get(mailbox, "messages/" + urllib.parse.quote(message_id, safe=""), _token(c, mailbox), format="full")
    if not isinstance(raw, dict) or not raw.get("id"):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete message; try again", 502, retryable=True)
    return raw


def raw_message(c, mailbox, message_id):
    """The message's own bytes (format raw): what a DKIM check reads."""
    found = _get(mailbox, "messages/" + urllib.parse.quote(message_id, safe=""), _token(c, mailbox), format="raw").get(
        "raw"
    )
    if not isinstance(found, str) or not found:
        raise Problem("gmail_unavailable", "Gmail returned no raw message; try again", 502, retryable=True)
    try:
        return base64.urlsafe_b64decode(found + "=" * (-len(found) % 4))
    except (ValueError, TypeError) as bad:
        raise Problem("gmail_unavailable", "Gmail returned an unreadable message", 502) from bad


def attachment(c, mailbox, message_id, attachment_id):
    """One attachment's base64url data and size, at most ATTACHMENT_MAX_BYTES."""
    found = _get(
        mailbox,
        "messages/"
        + urllib.parse.quote(message_id, safe="")
        + "/attachments/"
        + urllib.parse.quote(attachment_id, safe=""),
        _token(c, mailbox),
    )
    data, size = found.get("data"), found.get("size")
    if not isinstance(data, str):
        raise Problem("gmail_unavailable", "Gmail returned an attachment without data; try again", 502, retryable=True)
    return {"data": data, "size": size if isinstance(size, int) and size >= 0 else 0}


def _draft(found):
    message = found.get("message") if isinstance(found, dict) else None
    if not isinstance(message, dict) or not found.get("id") or not message.get("id"):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete draft; try again", 502, retryable=True)
    return {"id": str(found["id"]), "message": str(message["id"]), "thread": str(message.get("threadId") or "")}


def draft_find(c, mailbox, message_id):
    """The draft whose Message-ID header is `message_id` (with its angle brackets), as {id, message, thread}, or None."""
    page = _get(mailbox, "drafts", _token(c, mailbox), q="rfc822msgid:" + message_id.strip("<>"), maxResults=1)
    rows = page.get("drafts") or []
    if not isinstance(rows, builtins.list):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502, retryable=True)
    return _draft(rows[0]) if rows else None


def draft_get(c, mailbox, draft_id):
    """The draft with this id as {id, message, thread}, or None when Gmail no longer has it."""
    try:
        found = _get(mailbox, "drafts/" + urllib.parse.quote(draft_id, safe=""), _token(c, mailbox), format="minimal")
    except Problem as missing:
        if missing.code == "not_found":
            return None
        raise
    return _draft(found)


def draft_create(c, mailbox, raw, thread_id=""):
    """A new Gmail draft of this MIME (base64url), in `thread_id` when it answers one: {id, message, thread}. A draft
    over INLINE_RAW_MAX (an email with files) goes up as a multipart upload, as a large send does."""
    token = _token(c, mailbox, write=True)
    if len(raw) <= INLINE_RAW_MAX:
        message = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        return _draft(_call(mailbox, "drafts", token, data={"message": message}))
    body, content_type = _upload_body(raw, {"message": {"threadId": thread_id}} if thread_id else {})
    path = urllib.parse.quote(mailbox, safe="") + "/drafts?uploadType=multipart"
    status, found = send_call(UPLOAD + path, token=token, body=body, content_type=content_type)
    if status in (200, 201) and found.get("id"):
        return _draft(found)
    if status in (401, 403):
        raise Problem(
            "gmail_refused", "Gmail refused this change; check the Whole Workspace connection and Gmail access", 403
        )
    if status in (400, 413):
        raise Problem("gmail_size", "Gmail refused this draft (HTTP " + str(status) + "); it may be too large", 413)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)


# A draft's format=raw answer: the whole email base64url-encoded, up to the send gate's limit, with room for the JSON.
DRAFT_RAW_MAX_BYTES = MAX_RAW * 4 // 3 + 1024 * 1024


def draft_raw(c, mailbox, draft_id):
    """A draft's MIME (base64url) and thread, as Gmail holds it now: what a send of the draft freezes."""
    found = _call(
        mailbox,
        "drafts/" + urllib.parse.quote(draft_id, safe=""),
        _token(c, mailbox),
        max_bytes=DRAFT_RAW_MAX_BYTES,
        format="raw",
    )
    message = found.get("message") if isinstance(found, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("raw"), str) or not message["raw"]:
        raise Problem("gmail_unavailable", "Gmail returned an incomplete draft; try again", 502, retryable=True)
    return {"raw": message["raw"].rstrip("="), "thread": str(message.get("threadId") or "")}


def draft_delete(c, mailbox, draft_id):
    """Discard one draft. True when Gmail deleted it, False when it was already gone."""
    url = BASE + urllib.parse.quote(mailbox, safe="") + "/drafts/" + urllib.parse.quote(draft_id, safe="")
    status = gmail_delete_call(url, token=_token(c, mailbox, write=True))
    if status in (200, 204):
        return True
    if status == 404:
        return False
    if status in (401, 403):
        raise Problem("gmail_refused", "Gmail refused this change; check the connection and Gmail access", 403)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)
