"""Small stdlib HTTP client usable by existing bot environments."""

import os
import mimetypes
import json
import hashlib
import ipaddress
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

CLIENT_VERSION = "0.2.1"


def gmail_read_timeout(method, path, default):
    """Live Gmail reads and organizing may mint and page before answering."""
    route = path.split("?", 1)[0]
    if method == "POST" and route == "/api/v2/mail/messages/organize":
        return max(default, 90)
    if method == "GET" and re.fullmatch(r"/api/v2/mail/(?:messages(?:/[^/]+)?|threads/[^/]+)", route):
        return max(default, 90)
    return default


def inbound_wait_timeout(method, path, body, default, *, query=None):
    """A valid Inbox wait gets its server deadline plus ten seconds (up to 130).

    Both MCP transports use this exact-route rule. Malformed input gets no extra
    time, and a caller's explicitly longer timeout remains authoritative.
    """
    if method != "POST" or path != "/api/v2/inbound/wait" or query or not isinstance(body, dict):
        return default
    wait = body.get("timeout", 25)
    if type(wait) is not int or not 0 <= wait <= 120:
        return default
    return max(default, wait + 10)


# Routes the server answers only after a chain of model calls (backend/judge.py CHAIN_SECONDS, 25 s, for decisions):
# their callers wait past it.
SLOW_POSTS = frozenset(
    {"/api/v2/decisions", "/api/v2/gateway/search", "/api/v2/gateway/fetch", "/api/v2/gateway/digest"}
)
SLOW_WAIT = 45
# A file streamed from disk (a video up to the server's limit): the server stores the bytes after the last one is sent.
STREAM_WAIT = 600
# An image takes an image model's time (the Gateway's own try is 120 s): its caller waits past that.
LONG_POSTS = {"/api/v2/gateway/images": 150, "/api/v2/browser/commands": 40}
# A browser verb acts on a live page: a second send could click twice, so it is sent once (also over MCP).
ONCE_POSTS = {"/api/v2/browser/commands"}
# A finished video's MP4, fetched through the server from the Gateway: the caller waits for the whole file.
VIDEO_CONTENT = re.compile(r"/api/v2/gateway/videos/gv_[0-9a-f]{24}/content")
VIDEO_CONTENT_WAIT = 150


class APIError(Exception):
    def __init__(self, code, detail, status=0, retryable=False, operation_id=None, extra=None):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status
        self.retryable, self.operation_id = retryable, operation_id
        # What the server put beside code and detail (backend/store.py Problem.extra), such as a worktree wait.
        self.extra = extra or {}


def http_failure(exc, key):
    """The APIError for an HTTP error answer: the server's code, detail and extra fields, retryable on 429 and 5xx."""
    try:
        payload = json.load(exc)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    error = payload.get("error", {})
    if isinstance(error, str):
        # A flat refusal: `error` is the code, `detail` (a task refusal) or `message` (CONTRACT §1 task
        # caps) the words, the rest is extra (a task refusal's `missing`, `next` and `version`).
        error = {
            "code": error,
            "detail": payload.get("detail") or payload.get("message") or "",
            **{k: v for k, v in payload.items() if k not in ("error", "message", "detail")},
        }
    if not isinstance(error, dict):
        error = {}
    # Cloudflare answers 502 then 530 (Error 1033, tunnel down) with a problem-detail
    # body and no `error` key while a deploy activates; keep its title so the log says why.
    detail = error.get("detail") or (
        f"HTTP {exc.code}" + (": " + str(payload["title"])[:200] if payload.get("title") else "")
    )
    retryable = exc.code == 429 or 500 <= exc.code < 600
    return APIError(
        error.get("code", "http_error"),
        detail,
        exc.code,
        retryable,
        key,
        {k: v for k, v in error.items() if k not in ("code", "detail", "retryable")},
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A Cloudflare login redirect is an auth failure, not a destination for credentials.
        return None


def plain_http_host(host):
    """Where plain http is accepted: loopback, or a one-label name such as a compose service (local
    development and CI). Anything else, private addresses included, is HTTPS."""
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost" or ("." not in host and re.fullmatch(r"[A-Za-z0-9-]+", host) is not None)


class Client:
    def __init__(self, url, token, timeout=15, retries=3):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and plain_http_host(parsed.hostname)):
            raise ValueError("Tico requires HTTPS, except on loopback or a one-label service name")
        self.url, self.token, self.timeout, self.retries = url.rstrip("/"), token, timeout, retries
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Use the Tico origin URL without credentials, path, query, or fragment")
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(
        self,
        method,
        path,
        body=None,
        key=None,
        extra_headers=None,
        binary=False,
        raw=None,
        delegate=False,
        limit=20_000_000,
    ):
        if not path.startswith("/api/v2/"):
            raise ValueError("Use a versioned Tico API path")
        key = key or str(uuid.uuid4())
        if raw is not None and body is not None:
            raise ValueError("Send either a JSON body or opaque bytes, not both")
        data = (
            raw
            if raw is not None and (hasattr(raw, "read") or isinstance(raw, MultipartBody))
            else bytes(raw)
            if raw is not None
            else (json.dumps(body).encode() if body is not None else None)
        )
        headers = {
            "Authorization": "Bearer " + self.bearer(),
            "Accept": "application/json",
            "User-Agent": "Tico-Client/" + CLIENT_VERSION,
        }
        if delegate:
            # BotOps doing what the person who asked it could do: the server answers as them, or with a Confirm card.
            headers["X-Tico-On-Behalf-Of"] = delegate if isinstance(delegate, str) else "turn"
        if extra_headers:
            if set(extra_headers) != {"X-Tico-Processing-Token"}:
                raise ValueError("Only a scoped processing credential may be added")
            headers.update(extra_headers)
        if data is not None:
            headers.update(
                {
                    "Content-Type": getattr(data, "content_type", "application/octet-stream")
                    if raw is not None
                    else "application/json",
                    "Idempotency-Key": key,
                }
            )
        if hasattr(data, "read"):
            headers["Content-Length"] = str(os.fstat(data.fileno()).st_size)
        elif isinstance(data, MultipartBody):
            headers["Content-Length"] = str(data.size)
        path = path.replace("#", "%23")  # a task number ("tasks/#18945") is in the path, not a fragment
        timeout = inbound_wait_timeout(method, path, body, self.timeout)
        timeout = gmail_read_timeout(method, path, timeout)
        if method == "POST" and path in SLOW_POSTS:
            timeout = max(timeout, SLOW_WAIT)
        if hasattr(data, "read"):
            timeout = max(timeout, STREAM_WAIT)
        if method == "POST" and path in LONG_POSTS:
            timeout = max(timeout, LONG_POSTS[path])
        if method == "GET" and binary and VIDEO_CONTENT.fullmatch(path):
            timeout = max(timeout, VIDEO_CONTENT_WAIT)
        retries = 0 if method == "POST" and path in ONCE_POSTS else self.retries
        for attempt in range(retries + 1):
            if hasattr(data, "seek"):
                data.seek(0)
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=timeout) as response:
                    if binary:
                        data = response.read(limit + 1)
                        if len(data) > limit or hashlib.sha256(data).hexdigest() != response.headers.get(
                            "X-Content-SHA256"
                        ):
                            raise APIError(
                                "file_integrity", "Tico file response failed its size, type, or integrity check"
                            )
                        return data
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                with exc:
                    if binary and exc.code == 302:
                        target = exc.headers.get("Location", "")
                        parsed = urllib.parse.urlsplit(target)
                        signed = urllib.parse.parse_qs(parsed.query)
                        digest = exc.headers.get("X-Content-SHA256", "")
                        if (
                            parsed.scheme != "https"
                            or not parsed.netloc
                            or parsed.username
                            or parsed.password
                            or not {"Signature", "Expires", "Key-Pair-Id"} <= signed.keys()
                            or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        ):
                            raise APIError("file_integrity", "Tico returned an invalid file redirect")
                        # No bearer token, processing credential or delegation follows the redirect.
                        download = urllib.request.Request(
                            target, headers={"User-Agent": "Tico-Client/" + CLIENT_VERSION}
                        )
                        with self.opener.open(download, timeout=self.timeout) as response:
                            data = response.read(limit + 1)
                            if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
                                raise APIError(
                                    "file_integrity", "Tico file response failed its size or integrity check"
                                )
                            return data
                    failure = http_failure(exc, key)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                failure = APIError(
                    "unavailable",
                    "Tico could not confirm this request; retry with the same operation ID",
                    retryable=True,
                    operation_id=key,
                )
            except ValueError:
                failure = APIError(
                    "protocol",
                    "Tico returned an invalid API response; check the endpoint and sign-in configuration",
                    operation_id=key,
                )
            if not failure.retryable or attempt == retries:
                raise failure
            time.sleep(min(2**attempt, 5) + random.random() * 0.2)

    def bearer(self):
        """The token to send. Persistent harnesses retain a reference (`tico-file:PATH`), never an expired per-turn
        token."""
        token = self.token
        if token.startswith("tico-file:"):
            try:
                token = Path(token[len("tico-file:") :]).read_text().strip()
            except OSError:
                token = ""
            if not token:
                raise APIError("stale_lease", "No active Hub turn; wait for a new request", status=409)
        return token

    def stream(self, path, query=None, last_event_id=None, timeout=60):
        """Open a server-sent event stream (GET /api/v2/events) and return the open response; read it by lines. An
        HTTP refusal raises APIError as `request` does; a network failure raises APIError `unavailable` (retryable)."""
        filtered = {k: v for k, v in (query or {}).items() if v not in (None, "")}
        url = self.url + "/api/v2/" + path.lstrip("/") + ("?" + urllib.parse.urlencode(filtered) if filtered else "")
        headers = {
            "Authorization": "Bearer " + self.bearer(),
            "Accept": "text/event-stream",
            "Cache-Control": "no-store",
            "User-Agent": "Tico-Client/" + CLIENT_VERSION,
        }
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        try:
            return self.opener.open(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout)
        except urllib.error.HTTPError as exc:
            with exc:
                raise http_failure(exc, None) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise APIError("unavailable", "Tico's event stream could not be reached", retryable=True) from None

    def get(self, path, /, **query):  # positional: a query may carry its own "path" (file-approvals does)
        filtered = {k: v for k, v in query.items() if v is not None}
        suffix = "?" + urllib.parse.urlencode(filtered) if filtered else ""
        return self.request("GET", "/api/v2/" + path + suffix)

    def post(self, path, body=None, key=None):
        return self.request("POST", "/api/v2/" + path, body or {}, key)

    def patch(self, path, body=None, key=None):
        return self.request("PATCH", "/api/v2/" + path, body or {}, key)

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        """Any v2 route by method, path relative to /api/v2/; `delegate` acts as the person BotOps works for."""
        filtered = {k: v for k, v in (query or {}).items() if v is not None}
        suffix = "?" + urllib.parse.urlencode(filtered) if filtered else ""
        return self.request(
            method.upper(),
            "/api/v2/" + path.lstrip("/") + suffix,
            body if body is not None or method.upper() == "GET" else {},
            key,
            delegate=delegate,
        )

    def post_bytes(self, path, data, key=None):
        """One opaque binary body (an audio chunk), under the same retry rule as a JSON write."""
        return self.request("POST", "/api/v2/" + path, key=key, raw=data)

    def post_multipart(self, path, files, fields=None, key=None):
        return self.request("POST", "/api/v2/" + path, key=key, raw=MultipartBody(files, fields or {}))

    def features(self):
        try:
            return self.get("config").get("features", {})
        except APIError as exc:
            if exc.status not in (403, 404):
                raise
            return {}

    def download(self, file_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", file_id):
            raise ValueError("Use an opaque Tico file ID, not a URL or path")
        return self.request("GET", "/api/v2/files/" + file_id, binary=True)


class MultipartBody:
    """A replayable iterable; each retry opens the same files and uses bounded reads."""

    def __init__(self, files, fields):
        boundary = "tico-" + uuid.uuid4().hex
        self.content_type = "multipart/form-data; boundary=" + boundary
        self.parts = []
        for name, value in fields.items():
            if not re.fullmatch(r"[a-z_]+", name):
                raise ValueError("Invalid upload field")
            value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            self.parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                    + value.encode()
                    + b"\r\n",
                    None,
                )
            )
        for name, path in files.items():
            path = Path(path)
            if not re.fullmatch(r"[a-z_]+", name):
                raise ValueError("Invalid upload field")
            filename = re.sub(r'[\r\n"\\]', "_", path.name)
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            prefix = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
            self.parts.append((prefix, path))
        self.ending = f"--{boundary}--\r\n".encode()
        self.file_sizes = {path: path.stat().st_size for _, path in self.parts if path}
        self.size = len(self.ending) + sum(
            len(prefix) + (self.file_sizes[path] + 2 if path else 0) for prefix, path in self.parts
        )

    def __iter__(self):
        for prefix, path in self.parts:
            yield prefix
            if path:
                with path.open("rb") as source:
                    remaining = self.file_sizes[path]
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise APIError("file_changed", "The upload file changed; retry with the finished file")
                        remaining -= len(chunk)
                        yield chunk
                    if source.read(1):
                        raise APIError("file_changed", "The upload file changed; retry with the finished file")
                yield b"\r\n"
        yield self.ending
