"""A recording transport at the HTTP seam of clients/tico.Client: its `opener`.

The client builds each request as it does for real (the URL and query, the headers, the JSON or file body, the
retries and the error answers). This records what it opened and answers from a table, so a test of what a client
sends reads the requests, not a guess at the client's method calls. A test of what the hub does uses `live(api)`.
"""

import email.message
import email.policy
import io
import json
import urllib.error
from collections import namedtuple

from clients import remotecli, tico

BASE = "https://tico.acme.example"
Sent = namedtuple("Sent", "method path body headers")  # path: under /api/v2, with its query; body: see _body


class Refusal(Exception):
    """An answer that is an HTTP error, as the server gives it: the client raises it as an APIError."""

    def __init__(self, status, code, detail="", **extra):
        super().__init__(detail)
        self.status, self.code, self.detail, self.extra = status, code, detail, extra


class Raw:
    """An answer that is not JSON: the bytes of a file, with the headers that come with them."""

    def __init__(self, data, headers=None):
        self.data = data
        self.headers = email.message.Message()  # looked up without regard to case, as a server's headers are
        for name, value in (headers or {}).items():
            self.headers[name] = value


class _Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = {} if headers is None else headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Opener:
    def __init__(self, wire):
        self.wire = wire

    def open(self, request, timeout=None):
        headers = {k.lower(): v for k, v in request.headers.items()}
        path = request.full_url[len(BASE) :]
        if not path.startswith("/api/v2/"):
            raise AssertionError(f"the client opened {path}, which is not a Tico API route")
        method = request.get_method()
        self.wire.sent.append(Sent(method, path, _body(request.data, headers), headers))
        route = path[len("/api/v2/") :].split("?", 1)[0]
        answer = self._answer(f"{method} {route}")
        if isinstance(answer, Refusal):
            raise urllib.error.HTTPError(
                request.full_url,
                answer.status,
                answer.detail,
                {},
                io.BytesIO(
                    json.dumps({"error": {"code": answer.code, "detail": answer.detail, **answer.extra}}).encode()
                ),
            )
        if isinstance(answer, BaseException):  # e.g. URLError: the hub could not be reached
            raise answer
        if isinstance(answer, Raw):
            return _Response(answer.data, answer.headers)
        return _Response(json.dumps(answer).encode())

    def _answer(self, key):
        if key not in self.wire.answers:
            if self.wire.default is None:  # no answer for this route: refused, so a request nobody expected fails
                return Refusal(400, "unanswered", key)
            return self.wire.default
        answer = self.wire.answers[key]
        if isinstance(answer, list):  # in order; the last repeats (a state that changes as it is polled)
            if len(answer) > 1:
                self.wire.answers[key] = answer[1:]
            return answer[0]
        return answer


def _body(data, headers):
    """What the request carries: JSON decoded, bytes as they are, a multipart body by field name (a file part is
    {"filename", "content"}), or for a file the client streams only its first bytes ({"head": ...}; the size is the
    Content-Length header). A file is read as a server would read it, not whole."""
    if data is None:
        return None
    if hasattr(data, "read"):
        return {"head": data.read(16)}
    if not isinstance(data, bytes):  # a multipart body arrives as its chunks, in the order they are sent
        data = b"".join(data)
    content_type = headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return _multipart(data, content_type)
    if content_type.startswith("application/json"):
        return json.loads(data)
    return data


def _multipart(data, content_type):
    message = email.message_from_bytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + data, policy=email.policy.HTTP
    )
    fields = {}
    for part in message.iter_parts():
        name, filename = part.get_param("name", header="content-disposition"), part.get_filename()
        content = part.get_payload(decode=True)
        fields[name] = {"filename": filename, "content": content} if filename else content.decode()
    return fields


class Wire:
    """`answers` maps "METHOD route" (the route under /api/v2, without its query) to the JSON the server answers, a
    Raw, a Refusal, or an exception to raise (a URLError). A list is answered in order. `default`, when given, answers
    every other route. Otherwise a request with no answer is refused (400 `unanswered`), so an unexpected request fails
    the test."""

    def __init__(self, answers=None, default=None):
        self.answers = dict(answers or {})
        self.default = default
        self.sent = []

    def client(self, token="token", **options):
        """The real client, at BASE, whatever URL the code under test would have given it."""
        client = tico.Client(BASE, token, **{"retries": 0, **options})
        client.opener = _Opener(self)
        return client

    def writes(self):
        return [s for s in self.sent if s.method != "GET"]


def cli_wire(monkeypatch, answers=None, default=None):
    """`remotecli.run` (the `tico` command) builds its client from HUB_API_URL. This makes that client the real one, at
    a recording transport that answers `answers` and records every request the command sends."""
    wire = Wire(answers, default=default)

    def client(url, token, **options):  # the command's own URL is replaced by the recording one (BASE)
        return wire.client(token, **options)

    monkeypatch.setenv("HUB_API_URL", BASE)
    monkeypatch.setattr(remotecli, "Client", client)  # double: the HTTP transport only; the client is real
    return wire
