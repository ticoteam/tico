"""Read and organize Workspace mail in Gmail, without retaining messages on the server.

Callers check mailbox access before minting. Only the token cache in google_workspace survives a call;
the connector's pure normalizer supplies the same message fields without loading Google client libraries.
"""

import builtins
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from connectors.mail.gmail import normalize

from . import google_workspace
from .store import Problem

BASE = "https://gmail.googleapis.com/gmail/v1/users/"
SECONDS = 10
LIMIT_MAX = 50
WORKERS = 10
HTTP_MAX_BYTES = 8 * 1024 * 1024
PAGE_MAX_BYTES = 256 * 1024
OPEN_MAX_BYTES = 2 * 1024 * 1024
METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Date", "List-Id", "List-Unsubscribe")
MUTABLE_SYSTEM_LABELS = frozenset(("INBOX", "UNREAD", "STARRED", "IMPORTANT"))
PROTECTED_LABELS = frozenset(("TRASH", "SPAM", "SENT", "DRAFT", "DRAFTS"))


def gmail_call(url, *, token, data=None):
    """(status, JSON object), or status 0 when Gmail cannot answer. Tests replace this function."""
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": "Bearer " + token})
    if data is not None:
        request.data = json.dumps(data).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            status, raw = answer.status, answer.read(HTTP_MAX_BYTES + 1)
    except urllib.error.HTTPError as refused:
        refused.close()
        return refused.code, {}
    except (OSError, ValueError):
        return 0, {}
    if len(raw) > HTTP_MAX_BYTES:
        raise Problem("gmail_size", "This Gmail response is too large; open it in Gmail", 502)
    if not raw and data is not None and status in (200, 204):
        return status, {}
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return 0, {}
    return (status, body) if isinstance(body, dict) else (0, {})


def _call(mailbox, path, token, *, data=None, **query):
    url = BASE + urllib.parse.quote(mailbox, safe="") + "/" + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    status, body = gmail_call(url, token=token, **({"data": data} if data is not None else {}))
    if status in (200, 201, 204) and isinstance(body, dict) and "error" not in body:
        return body
    if status == 400:
        raise Problem("gmail_query", "Gmail refused the query or page token; check it and try again", 422)
    if status in (401, 403):
        verb = "change" if data is not None else "read"
        raise Problem("gmail_refused", f"Gmail refused this {verb}; check the Whole Workspace connection and Gmail access", 403)
    if status == 404:
        raise Problem("not_found", "Gmail could not find this mailbox, thread or message", 404)
    if status == 429:
        raise Problem("gmail_unavailable", "Gmail's quota was reached; try again shortly", 503, retryable=True)
    raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)


def _get(mailbox, path, token, **query):
    return _call(mailbox, path, token, **query)


def _token(c, mailbox):
    return google_workspace.mint(c, mailbox, "gmail", scopes=google_workspace.READ_ONLY_SCOPES["gmail"])["access_token"]


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
    if (not isinstance(ids, builtins.list) or len(ids) > cap
            or any(not isinstance(item, dict) or not item.get("id") for item in ids)
            or (nxt is not None and not isinstance(nxt, str))):
        raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502, retryable=True)

    def metadata(item):
        return _message(_get(mailbox, "messages/" + urllib.parse.quote(item["id"], safe=""), token,
                             format="metadata", metadataHeaders=METADATA_HEADERS))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        messages = [item for item in pool.map(metadata, ids)]
    return _bounded({"messages": messages, "next_cursor": nxt, "q": q}, PAGE_MAX_BYTES)


def thread(c, mailbox, thread_id):
    raw = _get(mailbox, "threads/" + urllib.parse.quote(thread_id, safe=""), _token(c, mailbox), format="full")
    messages = raw.get("messages")
    if not isinstance(messages, builtins.list) or not messages:
        raise Problem("gmail_unavailable", "Gmail returned an incomplete thread; try again", 502, retryable=True)
    return _bounded(sorted((_message(item) for item in messages), key=lambda item: (item["epoch"], item["id"])),
                    OPEN_MAX_BYTES)


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
        if (not isinstance(rows, builtins.list) or len(rows) > cap - len(ids)
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                       or not row["id"] or not row["id"].isascii() or len(row["id"]) > 128 for row in rows)
                or not isinstance(cursor, str) or cursor in seen_pages):
            raise Problem("gmail_unavailable", "Gmail returned an incomplete page; try again", 502)
        ids.extend(row["id"] for row in rows if row["id"] not in ids)
        if not cursor:
            break
        seen_pages.add(cursor)
        if len(seen_pages) >= 500:
            raise Problem("gmail_unavailable", "Gmail paging did not finish; narrow the query", 502)
    return ids


def labels(c, mailbox):
    rows = _get(mailbox, "labels", _token(c, mailbox)).get("labels")
    if (not isinstance(rows, builtins.list)
            or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                   or not isinstance(row.get("name"), str) or not row["name"]
                   or row.get("type") not in ("user", "system") for row in rows)):
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
    token = google_workspace.mint(c, mailbox, "gmail", scopes=google_workspace.WORKSPACE_SCOPES["gmail"])["access_token"]
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
    token = google_workspace.mint(c, mailbox, "gmail", scopes=google_workspace.WORKSPACE_SCOPES["gmail"])["access_token"]
    for start in range(0, len(ids), 1000):
        _call(mailbox, "messages/batchModify", token,
              data={"ids": ids[start:start + 1000], "addLabelIds": builtins.list(add),
                    "removeLabelIds": builtins.list(remove)})
