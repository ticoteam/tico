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


class APIError(Exception):
    def __init__(self, code, detail, status=0, retryable=False, operation_id=None, extra=None):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status
        self.retryable, self.operation_id = retryable, operation_id
        # What the server put beside code and detail (backend/store.py Problem.extra), such as a worktree wait.
        self.extra = extra or {}


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

    def request(self, method, path, body=None, key=None, extra_headers=None, binary=False, raw=None, delegate=False):
        if not path.startswith("/api/v2/"):
            raise ValueError("Use a versioned Tico API path")
        key = key or str(uuid.uuid4())
        if raw is not None and body is not None:
            raise ValueError("Send either a JSON body or opaque bytes, not both")
        data = raw if raw is not None and (hasattr(raw, "read") or isinstance(raw, MultipartBody)) else bytes(raw) if raw is not None else (json.dumps(body).encode() if body is not None else None)
        # Persistent harnesses retain a reference, never an expired per-turn token.
        token = self.token
        if token.startswith("tico-file:"):
            try:
                token = Path(token[len("tico-file:"):]).read_text().strip()
            except OSError:
                token = ""
            if not token:
                raise APIError("stale_lease", "No active Hub turn; wait for a new request", status=409)
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "User-Agent": "Tico-Client/" + CLIENT_VERSION}
        if delegate:
            # BotOps doing what the person who asked it could do: the server answers as them, or with a Confirm card.
            headers["X-Tico-On-Behalf-Of"] = delegate if isinstance(delegate, str) else "turn"
        if extra_headers:
            if set(extra_headers) != {"X-Tico-Processing-Token"}:
                raise ValueError("Only a scoped processing credential may be added")
            headers.update(extra_headers)
        if data is not None:
            headers.update({"Content-Type": getattr(data, "content_type", "application/octet-stream") if raw is not None else "application/json",
                            "Idempotency-Key": key})
        if hasattr(data, "read"):
            headers["Content-Length"] = str(os.fstat(data.fileno()).st_size)
        elif isinstance(data, MultipartBody):
            headers["Content-Length"] = str(data.size)
        path = path.replace("#", "%23")     # a task number ("tasks/#18945") is in the path, not a fragment
        timeout = inbound_wait_timeout(method, path, body, self.timeout)
        for attempt in range(self.retries + 1):
            if hasattr(data, "seek"):
                data.seek(0)
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=timeout) as response:
                    if binary:
                        data = response.read(20_000_001)
                        if (len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != response.headers.get("X-Content-SHA256")):
                            raise APIError("file_integrity", "Tico file response failed its size, type, or integrity check")
                        return data
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if binary and exc.code == 302:
                    target = exc.headers.get("Location", "")
                    parsed = urllib.parse.urlsplit(target)
                    signed = urllib.parse.parse_qs(parsed.query)
                    digest = exc.headers.get("X-Content-SHA256", "")
                    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or not {"Signature", "Expires", "Key-Pair-Id"} <= signed.keys() or not re.fullmatch(r"[0-9a-f]{64}", digest):
                        raise APIError("file_integrity", "Tico returned an invalid file redirect")
                    # No bearer token, processing credential or delegation follows the redirect.
                    download = urllib.request.Request(target, headers={"User-Agent": "Tico-Client/" + CLIENT_VERSION})
                    with self.opener.open(download, timeout=self.timeout) as response:
                        data = response.read(20_000_001)
                        if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != digest:
                            raise APIError("file_integrity", "Tico file response failed its size or integrity check")
                        return data
                try:
                    payload = json.load(exc)
                except ValueError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                error = payload.get("error", {})
                if not isinstance(error, dict):
                    error = {}
                # Cloudflare answers 502 then 530 (Error 1033, tunnel down) with a problem-detail
                # body and no `error` key while a deploy activates; keep its title so the log says why.
                detail = error.get("detail") or (f"HTTP {exc.code}" + (": " + str(payload["title"])[:200]
                                                                        if payload.get("title") else ""))
                retryable = exc.code == 429 or 500 <= exc.code < 600
                failure = APIError(error.get("code", "http_error"), detail, exc.code, retryable, key,
                                   {k: v for k, v in error.items() if k not in ("code", "detail", "retryable")})
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                failure = APIError("unavailable", "Tico could not confirm this request; retry with the same operation ID",
                                   retryable=True, operation_id=key)
            except ValueError:
                failure = APIError("protocol", "Tico returned an invalid API response; check the endpoint and sign-in configuration",
                                   operation_id=key)
            if not failure.retryable or attempt == self.retries:
                raise failure
            time.sleep(min(2 ** attempt, 5) + random.random() * 0.2)

    def get(self, path, **query):
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
        return self.request(method.upper(), "/api/v2/" + path.lstrip("/") + suffix,
                            body if body is not None or method.upper() == "GET" else {}, key, delegate=delegate)

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
            self.parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b"\r\n", None))
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
        self.size = len(self.ending) + sum(len(prefix) + (self.file_sizes[path] + 2 if path else 0) for prefix, path in self.parts)

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
