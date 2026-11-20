"""Read, organize and send mail in Gmail, without retaining messages on the server.

Callers check mailbox access before asking for a token. Every token comes from the one door, backend/google_tokens.py:
the Workspace key or the person's own Connect Google grant. `send` delivers one frozen email the send queue has already
permitted (backend/email_sending.py `drain`); it never retries. Only the token caches survive a call;
the connector's pure normalizer supplies the same message fields without loading Google client libraries.

Drafts are found again by the Message-ID Tico gave them (`draft_find`, Gmail's `rfc822msgid:` search over drafts), so a
retried draft or automation action finds the draft it made instead of making a second one. A draft is named by its own
id (r-...) or by its message's id, whoever made it (`draft_resolve`), and is changed in place, keeping its id
(`draft_update`). `trash` and `spam` move messages out of the inbox and back; nothing here deletes a message for good.
"""

import base64
import builtins
import json
import uuid
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import google_tokens, outbound_http
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


def gmail_call(url, *, token, data=None, max_bytes=HTTP_MAX_BYTES, method=None):
    """(status, JSON object), or status 0 when Gmail cannot answer. A call with `data` is a POST unless `method` says
    PUT. Tests replace this function."""
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    try:
        reply = outbound_http.send(
            method or ("GET" if body is None else "POST"),
            url,
            headers=headers,
            content=body,
            timeout=SECONDS,
            max_bytes=max_bytes,
        )
    except outbound_http.Failed:
        return 0, {}
    if not reply.ok:
        return reply.status, {}
    if len(reply.content) > max_bytes:
        raise Problem("gmail_size", "This Gmail response is too large; open it in Gmail", 502)
    if not reply.content and data is not None and reply.status in (200, 204):
        return reply.status, {}
    try:
        found = reply.json()
    except (ValueError, UnicodeError):
        return 0, {}
    return (reply.status, found) if isinstance(found, dict) else (0, {})


def gmail_delete_call(url, *, token):
    """The HTTP status of one DELETE, or 0 when Gmail cannot answer. Tests replace this function."""
    try:
        headers = {"Authorization": "Bearer " + token}
        return outbound_http.send("DELETE", url, headers=headers, timeout=SECONDS, max_bytes=0).status
    except outbound_http.Failed:
        return 0


def _call(mailbox, path, token, *, data=None, max_bytes=None, method=None, **query):
    url = BASE + urllib.parse.quote(mailbox, safe="") + "/" + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    status, body = gmail_call(
        url,
        token=token,
        **({"data": data} if data is not None else {}),
        **({"max_bytes": max_bytes} if max_bytes else {}),
        **({"method": method} if method else {}),
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


def label_total(c, mailbox, label_id, token=None):
    """How many messages carry this label now (Gmail's own count; nothing is listed). `token`: one the caller already
    holds for the mailbox, so the call needs no database (`c` may be None)."""
    found = _get(
        mailbox,
        "labels/" + urllib.parse.quote(label_id, safe=""),
        token or _token(c, mailbox),
        fields="messagesTotal",
    ).get("messagesTotal", 0)
    return found if isinstance(found, int) and found >= 0 else 0


def labels(c, mailbox, token=None):
    """The mailbox's labels. `token` as for label_total."""
    rows = _get(mailbox, "labels", token or _token(c, mailbox)).get("labels")
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
    """Change only safe system labels or verified user-label IDs. Trash and spam are `trash` and `spam`."""
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


def spam(c, mailbox, ids, restore=False):
    """Report these messages as spam (out of the inbox), or with `restore` bring them back to the inbox. Gmail keeps
    spam 30 days; nothing here deletes."""
    for start in range(0, len(ids), 1000):
        _call(
            mailbox,
            "messages/batchModify",
            _token(c, mailbox, write=True),
            data={
                "ids": ids[start : start + 1000],
                "addLabelIds": ["INBOX"] if restore else ["SPAM"],
                "removeLabelIds": ["SPAM"] if restore else ["INBOX"],
            },
        )


def trash(c, mailbox, ids, restore=False):
    """Move these messages to Gmail's Trash, or with `restore` back out of it. Gmail has no batch call for either and
    keeps a trashed message 30 days; nothing here deletes one for good."""
    if not ids:
        return
    token, verb = _token(c, mailbox, write=True), "/untrash" if restore else "/trash"

    def one(mid):
        _call(mailbox, "messages/" + urllib.parse.quote(mid, safe="") + verb, token, data={})

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        builtins.list(pool.map(one, ids))


def thread_message_ids(c, mailbox, thread_ids):
    """Every message id of these threads, in order, drafts left out (Gmail files no label on a draft, and an unsent
    reply is the person's): what an action on whole threads changes, however many there are."""
    token, ids = _token(c, mailbox), []
    for tid in thread_ids:
        rows = _get(
            mailbox,
            "threads/" + urllib.parse.quote(tid, safe=""),
            token,
            format="minimal",
            fields="messages(id,labelIds)",
        ).get("messages")
        if not isinstance(rows, builtins.list) or any(not isinstance(r, dict) or not r.get("id") for r in rows):
            raise Problem("gmail_unavailable", "Gmail returned an incomplete thread; try again", 502, retryable=True)
        ids.extend(str(r["id"]) for r in rows if "DRAFT" not in (r.get("labelIds") or []) and str(r["id"]) not in ids)
    return ids


LABEL_COUNTS = ("messagesTotal", "messagesUnread", "threadsTotal", "threadsUnread")


def labels_counted(c, mailbox):
    """Every label with Gmail's own counts: [{id, name, type, messages_total, messages_unread, threads_total,
    threads_unread}], system labels first."""
    rows, token = labels(c, mailbox), _token(c, mailbox)

    def one(row):
        found = _get(mailbox, "labels/" + urllib.parse.quote(row["id"], safe=""), token, fields=",".join(LABEL_COUNTS))
        counts = {
            "messages_total": found.get("messagesTotal"),
            "messages_unread": found.get("messagesUnread"),
            "threads_total": found.get("threadsTotal"),
            "threads_unread": found.get("threadsUnread"),
        }
        return {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            **{k: v if isinstance(v, int) and v >= 0 else 0 for k, v in counts.items()},
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        out = builtins.list(pool.map(one, rows))
    return sorted(out, key=lambda row: (row["type"] != "system", row["name"].lower()))


def user_label(c, mailbox, name_or_id):
    """The user label named or numbered so, as {id, name, type}; a system label is refused, a missing one not_found."""
    for row in labels(c, mailbox):
        if name_or_id in (row["name"], row["id"]):
            if row["type"] != "user":
                raise Problem("gmail_label", "Tico changes only your own labels, never Gmail's system labels", 422)
            return row
    raise Problem("not_found", "Gmail could not find this label", 404)


def label_create(c, mailbox, name):
    """The user label of this name, made when missing: {id, name, type, created}. Asked again, it is found."""
    for row in labels(c, mailbox):
        if row["name"] == name or (row["type"] == "system" and name.upper() == row["id"]):
            if row["type"] != "user":
                raise Problem("gmail_label", "This is a Gmail system label; choose another name", 422)
            return {"id": row["id"], "name": row["name"], "type": "user", "created": False}
    row = _call(mailbox, "labels", _token(c, mailbox, write=True), data={"name": name})
    if not row.get("id") or row.get("type") != "user":
        raise Problem("gmail_unavailable", "Gmail returned an incomplete label; try again", 502)
    return {"id": str(row["id"]), "name": str(row["name"]), "type": "user", "created": True}


def label_rename(c, mailbox, label_id, name):
    row = _call(
        mailbox,
        "labels/" + urllib.parse.quote(label_id, safe=""),
        _token(c, mailbox, write=True),
        data={"name": name},
        method="PATCH",
    )
    if not row.get("id"):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete label; try again", 502)
    return {"id": str(row["id"]), "name": str(row.get("name") or name), "type": "user"}


def label_delete(c, mailbox, label_id):
    """Delete a user label: Gmail takes it off its messages and keeps the messages. True when deleted, False when it
    was already gone."""
    url = BASE + urllib.parse.quote(mailbox, safe="") + "/labels/" + urllib.parse.quote(label_id, safe="")
    status = gmail_delete_call(url, token=_token(c, mailbox, write=True))
    if status in (200, 204):
        return True
    if status == 404:
        return False
    if status in (401, 403):
        raise Problem("gmail_refused", "Gmail refused this change; check the connection and Gmail access", 403)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)


def send_call(url, *, token, body, content_type, method=None):
    """(status, JSON object) for one messages.send or large drafts.create (POST) or drafts.update (PUT), or status 0
    when Gmail did not answer. Tests replace this."""
    headers = {"Accept": "application/json", "Content-Type": content_type, "Authorization": "Bearer " + token}
    try:
        reply = outbound_http.send(
            method or "POST", url, headers=headers, content=body, timeout=SEND_SECONDS, max_bytes=HTTP_MAX_BYTES
        )
    except outbound_http.Failed:
        return 0, {}
    if not reply.ok:
        return reply.status, {}
    try:
        found = json.loads(reply.content[:HTTP_MAX_BYTES])
    except (ValueError, UnicodeError):
        return reply.status, {}
    return reply.status, found if isinstance(found, dict) else {}


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
    """The draft with this id as {id, message, thread}, or None when Gmail has no draft by that id (a message id
    Gmail refuses as a draft id included)."""
    try:
        found = _get(mailbox, "drafts/" + urllib.parse.quote(draft_id, safe=""), _token(c, mailbox), format="minimal")
    except Problem as missing:
        if missing.code in ("not_found", "gmail_query"):
            return None
        raise
    return _draft(found)


DRAFT_PAGE = 500  # drafts one list call returns (Gmail's most)
DRAFT_PAGES = 1000  # pages of ids a lookup by message or thread reads before it refuses (half a million drafts)


def _draft_page(c, mailbox, q="", page_token="", limit=None):
    """One page of drafts as [{id, message, thread}], and Gmail's continuation token or None."""
    limit = limit or DRAFT_PAGE
    query = {"maxResults": limit, "fields": "drafts(id,message(id,threadId)),nextPageToken"}
    if q:
        query["q"] = q
    if page_token:
        query["pageToken"] = page_token
    page = _get(mailbox, "drafts", _token(c, mailbox), **query)
    rows, nxt = page.get("drafts") or [], page.get("nextPageToken") or None
    if not isinstance(rows, builtins.list) or len(rows) > limit or (nxt is not None and not isinstance(nxt, str)):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502, retryable=True)
    return [_draft(row) for row in rows], nxt


def draft_scan(c, mailbox, wanted, q="", enough=None):
    """The drafts `wanted({id, message, thread})` keeps, newest first, paging through every draft (ids only) until
    `enough(found)` says to stop or Gmail has no more. A mailbox past DRAFT_PAGES pages is refused, never cut short."""
    found, cursor, seen = [], "", set()
    for _ in range(DRAFT_PAGES):
        rows, cursor = _draft_page(c, mailbox, q, cursor)
        found.extend(row for row in rows if wanted(row))
        if not cursor or (enough and enough(found)):
            return found
        if cursor in seen:
            break
        seen.add(cursor)
    raise Problem("gmail_size", "This mailbox has too many drafts to look through; find the draft in Gmail", 502)


def draft_resolve(c, mailbox, ident):
    """The draft named by its own id or by its message's id, as {id, message, thread}; None when neither names one.
    Any draft in the mailbox counts, whoever made it."""
    return draft_get(c, mailbox, ident) or next(
        iter(draft_scan(c, mailbox, lambda row: row["message"] == ident, enough=bool)), None
    )


def draft_ids(c, mailbox, message_ids):
    """{message id: draft id} for those of these messages that are drafts."""
    wanted = set(message_ids)
    if not wanted:
        return {}
    rows = draft_scan(c, mailbox, lambda row: row["message"] in wanted, enough=lambda found: len(found) >= len(wanted))
    return {row["message"]: row["id"] for row in rows}


DRAFT_FIELDS = "id,message(" + LIST_FIELDS + ")"


def drafts(c, mailbox, q="", thread="", page_token="", limit=50):
    """One bounded page of drafts, newest first: each its draft id with its message's headers, snippet and attachment
    names. `thread` keeps the drafts in that thread: every draft is looked through for them, and the cursor is how
    many of them came before."""
    cap = min(LIMIT_MAX, max(1, limit))
    if thread:
        matched = draft_scan(c, mailbox, lambda row: row["thread"] == thread, q)
        start = int(page_token) if page_token.isdigit() else 0
        rows, nxt = matched[start : start + cap], str(start + cap) if start + cap < len(matched) else None
    else:
        rows, nxt = _draft_page(c, mailbox, q, page_token, cap)
    token = _token(c, mailbox)

    def one(row):
        found = _get(
            mailbox, "drafts/" + urllib.parse.quote(row["id"], safe=""), token, format="full", fields=DRAFT_FIELDS
        )
        return {"draft": row["id"], **_listed(found.get("message"))}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        listed = builtins.list(pool.map(one, rows))
    return _bounded({"drafts": listed, "next_cursor": nxt, "q": q}, PAGE_MAX_BYTES)


def draft_message(c, mailbox, draft_id):
    """A draft's message read whole, with `draft` its id; None when Gmail has no draft by that id."""
    try:
        found = _get(mailbox, "drafts/" + urllib.parse.quote(draft_id, safe=""), _token(c, mailbox), format="full")
    except Problem as missing:
        if missing.code in ("not_found", "gmail_query"):
            return None
        raise
    return {"draft": str(found.get("id") or draft_id), **_bounded(_message(found.get("message")), OPEN_MAX_BYTES)}


def _draft_upload(url, token, raw, meta, method=None):
    """A draft over INLINE_RAW_MAX (an email with files) goes up as a multipart upload, as a large send does."""
    body, content_type = _upload_body(raw, meta)
    status, found = send_call(
        url + "?uploadType=multipart",
        token=token,
        body=body,
        content_type=content_type,
        **({"method": method} if method else {}),
    )
    if status in (200, 201) and found.get("id"):
        return _draft(found)
    if status == 404:
        raise Problem("not_found", "Gmail no longer has this draft", 404)
    if status in (401, 403):
        raise Problem(
            "gmail_refused", "Gmail refused this change; check the Whole Workspace connection and Gmail access", 403
        )
    if status in (400, 413):
        raise Problem("gmail_size", "Gmail refused this draft (HTTP " + str(status) + "); it may be too large", 413)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)


def draft_update(c, mailbox, draft_id, raw, thread_id=""):
    """Replace a draft's message with this MIME (base64url), keeping the draft's id: {id, message, thread}. Gmail
    gives the new message its own id."""
    token = _token(c, mailbox, write=True)
    path = "drafts/" + urllib.parse.quote(draft_id, safe="")
    thread = {"threadId": thread_id} if thread_id else {}
    if len(raw) <= INLINE_RAW_MAX:
        return _draft(
            _call(mailbox, path, token, data={"id": draft_id, "message": {"raw": raw, **thread}}, method="PUT")
        )
    url = UPLOAD + urllib.parse.quote(mailbox, safe="") + "/" + path
    return _draft_upload(url, token, raw, {"id": draft_id, "message": thread}, method="PUT")


def draft_create(c, mailbox, raw, thread_id=""):
    """A new Gmail draft of this MIME (base64url), in `thread_id` when it answers one: {id, message, thread}. A draft
    over INLINE_RAW_MAX (an email with files) goes up as a multipart upload, as a large send does."""
    token = _token(c, mailbox, write=True)
    if len(raw) <= INLINE_RAW_MAX:
        message = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        return _draft(_call(mailbox, "drafts", token, data={"message": message}))
    meta = {"message": {"threadId": thread_id}} if thread_id else {}
    return _draft_upload(UPLOAD + urllib.parse.quote(mailbox, safe="") + "/drafts", token, raw, meta)


# A draft's format=raw answer: the whole email base64url-encoded, up to the send gate's limit, with room for the JSON.
DRAFT_RAW_MAX_BYTES = MAX_RAW * 4 // 3 + 1024 * 1024


def draft_raw(c, mailbox, draft_id):
    """A draft's MIME (base64url), thread and message id, as Gmail holds it now: what a send of the draft freezes."""
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
    return {
        "raw": message["raw"].rstrip("="),
        "thread": str(message.get("threadId") or ""),
        "message": str(message.get("id") or ""),
    }


def draft_delete(c, mailbox, draft_id):
    """Discard one draft. True when Gmail deleted it, False when it was already gone (or the id is no draft id)."""
    url = BASE + urllib.parse.quote(mailbox, safe="") + "/drafts/" + urllib.parse.quote(draft_id, safe="")
    status = gmail_delete_call(url, token=_token(c, mailbox, write=True))
    if status in (200, 204):
        return True
    if status in (400, 404):
        return False
    if status in (401, 403):
        raise Problem("gmail_refused", "Gmail refused this change; check the connection and Gmail access", 403)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)
