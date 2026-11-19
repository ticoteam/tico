# pyright: strict
"""What Tico does as the client of a remote MCP server and of its OAuth server, shared by Granola (backend/granola_mcp.py,
a person's own account) and by MCP tools (backend/mcp_connect.py, a company's server in Tools): JSON-RPC messages and
answers (plain JSON or server-sent events), the WWW-Authenticate challenge an MCP server sends without a sign-in,
dynamic client registration (RFC 7591), PKCE (RFC 7636) and token answers. Nothing here sends a request: each caller
sends with its own client and pacing, and hands the answers here. No provider text is ever returned in an error.
"""

import base64
import hashlib
import json
import re
import secrets
import uuid
from typing import Any, cast

Json = dict[str, Any]

PROTOCOL = "2025-06-18"
CLIENT_NAME = "Tico"
ACCEPT = "application/json, text/event-stream"


class AnswerError(Exception):
    """An answer that is not what the protocol says: `code` is a fixed word, never the provider's text."""

    def __init__(self, code: str = "bad_response") -> None:
        super().__init__(code)
        self.code = code


def rpc(method: str, params: Json | None = None, notification: bool = False) -> Json:
    """A JSON-RPC 2.0 message; a request gets a fresh id, a notification none."""
    body: Json = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        body["id"] = uuid.uuid4().hex
    return body


def initialize(client: str = "tico") -> Json:
    return rpc(
        "initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": client, "version": "1"}}
    )


def headers(token: str = "", session: dict[str, str] | None = None, scheme: str = "bearer") -> dict[str, str]:
    """The headers of one MCP POST: what it accepts, the protocol version and session once known, and the sign-in in the
    tool's way (`bearer`, or `header:<Name>` with the token as the whole value)."""
    out = {"Accept": ACCEPT, "Content-Type": "application/json"}
    session = session or {}
    out["MCP-Protocol-Version"] = session.get("version", PROTOCOL)
    if session.get("id"):
        out["Mcp-Session-Id"] = session["id"]
    if token:
        if scheme.startswith("header:"):
            out[scheme[7:]] = token
        else:
            out["Authorization"] = "Bearer " + token
    return out


def message(text: str | None, content_type: str | None, request_id: object) -> Json:
    """The JSON-RPC answer to `request_id` in an MCP answer's body: the JSON itself, or the `data:` of the server-sent
    event that carries it. AnswerError when there is none."""
    if "text/event-stream" in (content_type or ""):
        for event in (text or "").replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                candidate: object = json.loads(data)
            except ValueError:
                continue
            if isinstance(candidate, dict) and cast(Json, candidate).get("id") == request_id:
                return cast(Json, candidate)
        raise AnswerError()
    try:
        value: object = json.loads(text or "")
    except ValueError:
        raise AnswerError() from None
    if not isinstance(value, dict):
        raise AnswerError()
    return cast(Json, value)


def challenge(value: str | None) -> dict[str, str]:
    """The parameters of a `WWW-Authenticate: Bearer ...` challenge ({"resource_metadata": url, "scope": ...}), lowercased
    names; {} when it is not a Bearer challenge."""
    value = value or ""
    if not re.match(r"\s*bearer\b", value, re.I):
        return {}
    found: dict[str, str] = {}
    for name, quoted, bare in re.findall(r'([A-Za-z_][\w-]*)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))', value):
        found[name.lower()] = (quoted.replace('\\"', '"') if quoted else bare)[:2000]
    return found


def registration(redirect_uris: list[str], grant_types: list[str], auth_method: str = "none") -> Json:
    """The body of a dynamic client registration (RFC 7591) for Tico."""
    return {
        "client_name": CLIENT_NAME,
        "token_endpoint_auth_method": auth_method,
        "grant_types": list(grant_types),
        "response_types": ["code"],
        "redirect_uris": list(redirect_uris),
    }


def registered(value: object) -> tuple[str, str]:
    """(client_id, client_secret or '') from a registration's answer; AnswerError when it has no client id."""
    if not isinstance(value, dict):
        raise AnswerError()
    answer = cast(Json, value)
    client_id, secret = answer.get("client_id"), answer.get("client_secret")
    if not isinstance(client_id, str) or not client_id or len(client_id) > 500:
        raise AnswerError()
    return client_id, secret if isinstance(secret, str) and len(secret) <= 2000 else ""


def pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def seconds(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (ValueError, TypeError, OverflowError):
        return default


def tokens(value: object, now: float, previous: Json | None = None) -> Json:
    """{access_token, refresh_token, expires_at} from a token answer; a refresh that names no new refresh token keeps the
    previous one. AnswerError when it has no access token."""
    if not isinstance(value, dict):
        raise AnswerError()
    answer = cast(Json, value)
    access = answer.get("access_token")
    if not isinstance(access, str) or not access:
        raise AnswerError()
    refresh = answer.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        refresh = (previous or {}).get("refresh_token") or ""
    expires = answer.get("expires_in")
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": now + seconds(expires, 3600) if expires is not None else None,
    }
