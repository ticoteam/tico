"""OAuth sign-in for Tico's MCP server, for agents that cannot send a pasted token (Dots, ChatGPT, the Claude app).

The MCP authorization flow (OAuth 2.1 with PKCE, RFC 9728 resource metadata, RFC 8414 server metadata, RFC 7591
client registration), ending in an ordinary personal token (backend/personal_tokens.py): the agent then is the person
who said yes, with their rights and no more, listed and revoked with their other tokens. Nothing new decides access.

- The agent's first call to `/api/v2/mcp` answers 401 with `WWW-Authenticate: Bearer resource_metadata=…`.
- Every machine-facing document and endpoint lives under `/api/v2/oauth/`, the one prefix a runner hostname is sure
  to pass (a sign-in proxy guards the rest). The root `/.well-known/` forms answer too, for hosts that route them.
- The person approves on `/oauth/authorize`, a page on the team's own address behind its usual sign-in.
- One approval is one token row. A refresh rotates that row's secret in place, so the token list shows one line per
  connected agent, and Revoke on that line ends the refresh too.
"""

import base64
import hashlib
import html
import json
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import personal_tokens
from .store import H, Problem, digest

PREFIX = "/api/v2/oauth"
MCP_PATH = "/api/v2/mcp"           # backend/mcp.py PATH
AUTHORIZE_PATH = "/oauth/authorize"
RESOURCE_METADATA_PATH = PREFIX + "/protected-resource"
SCOPE = "tico"
CODE_SECONDS = 300
REQUEST_SECONDS = 900
ACCESS_SECONDS = 3600
REFRESH_DAYS = 90
MAX_CLIENTS = 2000
UNUSED_CLIENT_HOURS = 24
MAX_BODY = 20_000
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")

class OAuthError(Exception):
    """An RFC 6749 error answer: `{"error": code, "error_description": …}`, never Tico's problem shape."""

    def __init__(self, code, description, status=400):
        super().__init__(description)
        self.code, self.description, self.status = code, description, status


def _answer(body, status=200):
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _error(exc):
    return _answer({"error": exc.code, "error_description": exc.description}, exc.status)


def issuer(settings):
    return settings.runner_url + PREFIX


def resource(settings):
    return settings.runner_url + MCP_PATH


def challenge_header(settings):
    """What a 401 from the MCP server says, so the agent can find where to sign in."""
    return 'Bearer resource_metadata="' + settings.runner_url + RESOURCE_METADATA_PATH + '"'


def protected_resource(settings):
    return {"resource": resource(settings), "authorization_servers": [issuer(settings)],
            "bearer_methods_supported": ["header"], "scopes_supported": [SCOPE],
            "resource_name": settings.app_name}


def server_metadata(settings):
    base = issuer(settings)
    return {"issuer": base, "authorization_endpoint": settings.public_url + AUTHORIZE_PATH,
            "token_endpoint": base + "/token", "registration_endpoint": base + "/register",
            "revocation_endpoint": base + "/revoke", "scopes_supported": [SCOPE],
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"]}


def _redirect_ok(uri):
    parts = urlsplit(uri)
    if parts.fragment or not parts.netloc:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


def register_client(c, body):
    """RFC 7591 registration for a public client: a name and where it may be sent back to."""
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 10 or not all(
            isinstance(u, str) and len(u) <= 2000 and _redirect_ok(u) for u in uris):
        raise OAuthError("invalid_redirect_uri", "Give 1 to 10 https redirect_uris (http only for localhost)")
    method = body.get("token_endpoint_auth_method") or "none"
    if method != "none":
        raise OAuthError("invalid_client_metadata", "Only public clients (token_endpoint_auth_method none) are supported")
    # Anyone may register, so a client that never got a token is let go after a day: the cap cannot be filled for good.
    stale, used = H.shift(H.now(), hours=-UNUSED_CLIENT_HOURS), "SELECT client_id FROM oauth_grants WHERE token_id IS NOT NULL"
    c.execute(f"DELETE FROM oauth_grants WHERE client_id IN (SELECT id FROM oauth_clients WHERE created<? AND id NOT IN ({used}))",
              (stale,))
    c.execute(f"DELETE FROM oauth_clients WHERE created<? AND id NOT IN ({used})", (stale,))
    if c.execute("SELECT count(*) FROM oauth_clients").fetchone()[0] >= MAX_CLIENTS:
        raise OAuthError("temporarily_unavailable", "Too many registered clients", 503)
    name = " ".join(str(body.get("client_name") or "").split())[:80] or urlsplit(uris[0]).hostname or "An agent"
    client_id, now = "tico_oc_" + secrets.token_urlsafe(18), H.now()
    c.execute("INSERT INTO oauth_clients VALUES(?,?,?,?)", (client_id, name, json.dumps(uris), now))
    return {"client_id": client_id, "client_id_issued_at": int(H.parse_ts(now).timestamp()), "client_name": name,
            "redirect_uris": uris, "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}


def _client(c, client_id):
    row = c.execute("SELECT * FROM oauth_clients WHERE id=?", (client_id or "",)).fetchone()
    if not row:
        raise OAuthError("invalid_client", "Unknown client_id", 401)
    return row


def begin(c, settings, who, query):
    """Check an authorization request and hold it for the person's answer. Returns (grant id, client name, host), or raises
    Problem when the agent cannot even be sent back (unknown client or redirect), OAuthError otherwise. Anyone may
    register a client under any name, so the page also names the host it returns to."""
    client = c.execute("SELECT * FROM oauth_clients WHERE id=?", (query.get("client_id", ""),)).fetchone()
    redirect = query.get("redirect_uri", "")
    if not client or redirect not in json.loads(client["redirect_uris_json"]):
        raise Problem("oauth_client", "This sign-in link is not from an agent Tico knows", 400)
    if query.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only response_type=code")
    if query.get("code_challenge_method") != "S256" or not CHALLENGE.fullmatch(query.get("code_challenge", "")):
        raise OAuthError("invalid_request", "A PKCE code_challenge with S256 is required")
    wanted = query.get("resource")
    if wanted and wanted.rstrip("/") != resource(settings):
        raise OAuthError("invalid_target", "This server signs in only to " + resource(settings))
    if who.role not in ("owner", "human") or who.via_token:
        raise Problem("forbidden", "Sign in to Tico in your browser to connect an agent", 403)
    grant = H.new_id()
    c.execute("INSERT INTO oauth_grants(id,client_id,human,redirect_uri,code_challenge,state,created) "
              "VALUES(?,?,?,?,?,?,?)", (grant, client["id"], H.actor_id(who.actor), redirect,
                                        query["code_challenge"], query.get("state", "")[:500], H.now()))
    return grant, client["name"], urlsplit(redirect).hostname or ""


def back(redirect, settings, **params):
    """The agent's redirect URI with this answer added (and `iss`, RFC 9207)."""
    params = {k: v for k, v in params.items() if v}
    params["iss"] = issuer(settings)
    return redirect + ("&" if urlsplit(redirect).query else "?") + urlencode(params)


def decide(c, auth, settings, who, grant_id, allow):
    """The person's answer. Returns where to send the browser."""
    row = c.execute("SELECT * FROM oauth_grants WHERE id=? AND code_hash IS NULL AND denied_at IS NULL AND human=?",
                    (grant_id, H.actor_id(who.actor))).fetchone()
    if not row or row["created"] <= H.shift(H.now(), seconds=-REQUEST_SECONDS):
        raise Problem("oauth_request", "This sign-in request has expired. Start again from the agent", 400)
    if not allow:
        c.execute("UPDATE oauth_grants SET denied_at=? WHERE id=?", (H.now(), grant_id))
        return back(row["redirect_uri"], settings, error="access_denied", state=row["state"])
    personal_tokens._person(who)
    if not personal_tokens.can_create(c, auth, who):
        raise Problem("forbidden", "Your team lets only the owner and admins connect agents", 403)
    code = secrets.token_urlsafe(32)
    c.execute("UPDATE oauth_grants SET code_hash=?,code_expires=? WHERE id=?",
              (digest(code), H.shift(H.now(), seconds=CODE_SECONDS), grant_id))
    return back(row["redirect_uri"], settings, code=code, state=row["state"])


def _may_hold(c, auth, human):
    """Whether this person may still hold a personal token (the owner's rule may have changed since they said yes)."""
    who = auth.identity_for_actor(c, "human:" + human)
    return bool(who) and personal_tokens.can_create(c, auth, who)


def _tokens(c, grant, client, rotate):
    """Mint the access and refresh secrets for a grant: a new token row the first time, the same row after."""
    access, refresh, now = personal_tokens.PREFIX + secrets.token_urlsafe(30), "tico_rt_" + secrets.token_urlsafe(32), H.now()
    expires = H.shift(now, seconds=ACCESS_SECONDS)
    if rotate:
        c.execute("UPDATE human_tokens SET token_hash=?,expires_at=? WHERE id=?", (digest(access), expires, grant["token_id"]))
        token_id = grant["token_id"]
    else:
        token_id = H.new_id()
        label = (client["name"] + " · " + now[:10])[:100]
        c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created,created_by,expires_at) VALUES(?,?,?,?,?,?,?)",
                  (token_id, grant["human"], label, digest(access), now, "human:" + grant["human"], expires))
        H.event(c, "human:" + grant["human"], "token.create", token_id, {"label": label, "oauth_client": client["id"]})
    # One approval lasts REFRESH_DAYS from the Allow, like a personal token's 90 days; then the person is asked again.
    c.execute("UPDATE oauth_grants SET token_id=?,refresh_hash=?,refresh_expires=coalesce(refresh_expires,?),"
              "code_expires=NULL WHERE id=?", (token_id, digest(refresh), H.shift(now, days=REFRESH_DAYS), grant["id"]))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_SECONDS,
            "refresh_token": refresh, "scope": SCOPE}


def exchange(c, auth, form):
    """The token endpoint: a code with its PKCE verifier, or a refresh token, for a fresh access token."""
    kind = form.get("grant_type")
    client = _client(c, form.get("client_id"))
    if kind == "authorization_code":
        grant = c.execute("SELECT * FROM oauth_grants WHERE code_hash=?", (digest(form.get("code", "")),)).fetchone()
        if not grant or grant["client_id"] != client["id"] or grant["token_id"] or not grant["code_expires"] \
                or grant["code_expires"] <= H.now():
            refused = OAuthError("invalid_grant", "The code is unknown, used or expired")
            if grant and grant["token_id"]:     # a code used twice: the second use ends the first (RFC 6749 §4.1.2)
                c.execute("UPDATE human_tokens SET revoked_at=coalesce(revoked_at,?) WHERE id=?", (H.now(), grant["token_id"]))
                return refused                  # returned, not raised, so the revocation is kept
            raise refused
        if form.get("redirect_uri") != grant["redirect_uri"]:
            raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
        verifier = form.get("code_verifier", "")
        made = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not VERIFIER.fullmatch(verifier) or not secrets.compare_digest(made, grant["code_challenge"]):
            raise OAuthError("invalid_grant", "The PKCE code_verifier does not match")
        return _tokens(c, grant, client, rotate=False)
    if kind == "refresh_token":
        grant = c.execute("SELECT g.*,t.revoked_at AS token_revoked FROM oauth_grants g JOIN human_tokens t ON "
                          "t.id=g.token_id WHERE g.refresh_hash=?", (digest(form.get("refresh_token", "")),)).fetchone()
        if not grant or grant["client_id"] != client["id"] or grant["token_revoked"] \
                or grant["refresh_expires"] <= H.now():
            raise OAuthError("invalid_grant", "The refresh token is unknown, revoked or expired")
        if not _may_hold(c, auth, grant["human"]):
            raise OAuthError("invalid_grant", "Your team now lets only the owner and admins connect agents")
        return _tokens(c, grant, client, rotate=True)
    raise OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token")


def revoke(c, form):
    """RFC 7009: end the grant behind an access or refresh token. Unknown tokens are not an error."""
    token = form.get("token", "")
    row = c.execute("SELECT g.token_id FROM oauth_grants g LEFT JOIN human_tokens t ON t.id=g.token_id "
                    "WHERE g.refresh_hash=? OR t.token_hash=?", (digest(token), digest(token))).fetchone()
    if row and row["token_id"]:
        c.execute("UPDATE human_tokens SET revoked_at=coalesce(revoked_at,?) WHERE id=?", (H.now(), row["token_id"]))


def consent_page(settings, who, client_name, grant_id, host):
    from .oidc import _page
    name = html.escape(client_name)
    form = ('<form method="post" action="' + AUTHORIZE_PATH + '"><input type="hidden" name="grant" value="'
            + html.escape(grant_id) + '">' + name + " will work in " + html.escape(settings.app_name) + " as you ("
            + html.escape(who.email or H.actor_id(who.actor)) + "), with your rights. It returns to <b>"
            + html.escape(host) + "</b>; deny if that is not the agent you are connecting. You can revoke it any time "
            "under Settings &gt; Computers &gt; API tokens.</p><p>"
            '<button class="b" name="decision" value="allow" type="submit">Allow</button> '
            '<button class="b" name="decision" value="deny" type="submit" style="background:none;color:inherit;'
            'border:1px solid currentColor">Deny</button></form>')
    return _page(settings, 200, "Connect " + client_name + "?", form)


async def _body(request):
    """The request body, at most MAX_BODY bytes: these routes are open to anyone, before the API's own size check."""
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY:
            raise OAuthError("invalid_request", "Request too large", 413)
        chunks.append(chunk)
    return b"".join(chunks)


async def _form(request):
    try:
        return {k: v[0] for k, v in parse_qs((await _body(request)).decode("utf-8", "replace"), max_num_fields=20).items()}
    except ValueError:
        raise OAuthError("invalid_request", "Too many fields") from None


def register(app, auth, store):
    """The routes. Writes go straight to a transaction, without the API's Idempotency-Key rule: an OAuth client has
    never heard of it, and each step here is single-use by itself."""
    settings = auth.settings

    def mutate_free(fn):
        with store.transaction() as c:
            return fn(c)

    def doc(body):
        return _answer(body)

    for path in (RESOURCE_METADATA_PATH, "/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/api/v2/mcp"):
        app.add_api_route(path, lambda: doc(protected_resource(settings)), methods=["GET"], include_in_schema=False)
    for path in (PREFIX + "/.well-known/oauth-authorization-server", PREFIX + "/.well-known/openid-configuration",
                 "/.well-known/oauth-authorization-server/api/v2/oauth", "/.well-known/openid-configuration/api/v2/oauth"):
        app.add_api_route(path, lambda: doc(server_metadata(settings)), methods=["GET"], include_in_schema=False)

    @app.post(PREFIX + "/register", include_in_schema=False)
    async def oauth_register(request: Request):
        try:
            body = json.loads(await _body(request) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except OAuthError as exc:
            return _error(exc)
        except ValueError:
            return _error(OAuthError("invalid_client_metadata", "Send a JSON object"))
        try:
            return _answer(mutate_free(lambda c: register_client(c, body)), 201)
        except OAuthError as exc:
            return _error(exc)

    @app.post(PREFIX + "/token", include_in_schema=False)
    async def oauth_token(request: Request):
        try:
            form = await _form(request)
            issued = mutate_free(lambda c: exchange(c, auth, form))
        except OAuthError as exc:
            return _error(exc)
        return _error(issued) if isinstance(issued, OAuthError) else _answer(issued)

    @app.post(PREFIX + "/revoke", include_in_schema=False)
    async def oauth_revoke(request: Request):
        try:
            form = await _form(request)
        except OAuthError as exc:
            return _error(exc)
        mutate_free(lambda c: revoke(c, form))
        return _answer({})

    @app.get(AUTHORIZE_PATH, include_in_schema=False)
    def oauth_authorize(request: Request):
        who, query = request.state.identity, dict(request.query_params)
        try:
            grant, name, host = mutate_free(lambda c: begin(c, settings, who, query))
        except OAuthError as exc:
            # Shown here, not sent back: anyone can register a client, and an error redirect sent before the person
            # saw anything would make this address a way to bounce them to any site (RFC 9700 §4.11.2).
            from .oidc import _page
            return _page(settings, 400, "This sign-in link is not valid", html.escape(exc.description))
        return consent_page(settings, who, name, grant, host)

    @app.post(AUTHORIZE_PATH, include_in_schema=False)
    async def oauth_decide(request: Request):
        try:
            form = await _form(request)
        except OAuthError as exc:
            raise Problem("oauth_request", exc.description, exc.status) from None
        target = mutate_free(lambda c: decide(c, auth, settings, request.state.identity, form.get("grant", ""),
                                              form.get("decision") == "allow"))
        return RedirectResponse(target, 303)
