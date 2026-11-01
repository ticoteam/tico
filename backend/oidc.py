"""Built-in browser sign-in with OpenID Connect (TICO_AUTH_PROXY=oidc).

Authorization-code flow with PKCE against Google, Microsoft Entra ID or any issuer that publishes
discovery. A verified ID token only proves who the person is; `Auth.authenticate` then matches the
email against the roster like it does for every proxy. What the browser holds afterwards is a
random session id whose hash is a row in `oidc_sessions`, so a session can be revoked, and no
provider token is ever stored.
"""

import asyncio
import base64
import functools
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .config import PRODUCT_NAME
from .store import H, Problem, digest

log = logging.getLogger("tico.oidc")

LOGIN_PATH = "/auth/login"
CALLBACK_PATH = "/auth/callback"
SIGNED_OUT_PATH = "/auth/signed-out"
# With TICO_OIDC_LABEL set, a signed-out browser lands here first: one "Sign in with <label>" button to LOGIN_PATH.
SIGN_IN_PATH = "/auth/sign-in"
AUTH_PATHS = (LOGIN_PATH, CALLBACK_PATH, SIGNED_OUT_PATH, SIGN_IN_PATH)
LABEL = re.compile(r"[^\x00-\x1f\x7f<>]{1,40}")
# A separate browser app on an allowed origin (TICO_CORS_ORIGINS, backend/cors.py) signs in here:
# `/auth/login?next=<its URL>&code_challenge=<S256>` returns it a one-time code in the URL fragment,
# and `POST /auth/token` exchanges the code (with the PKCE verifier) for a bearer session.
TOKEN_PATH = "/auth/token"
REVOKE_PATH = "/auth/token/revoke"
BEARER_PREFIX = "tico_st_"
CODE_WINDOW = 60
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
# Only asymmetric algorithms: an HMAC "signature" would be checkable with a public key.
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
LEEWAY = 60
# Which account chooser the provider shows at /auth/login?prompt=: only these two (login is the provider's own choice).
PROMPTS = ("select_account", "login")
LOGIN_WINDOW = 600
TOUCH_EVERY = 60
DISCOVERY_TTL = 3600
JWKS_MIN_INTERVAL = 60.0
UNPINNED_TENANTS = ("common", "organizations", "consumers")
SAFE_NEXT = re.compile(r"/[^/\\\s][^\s\\]*")
SECRET_FILE = "tico-session-secret"


class SignInError(Exception):
    """A sign-in that cannot continue; `reason` is for the log, never shown or holds a token."""

    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.reason, self.status = reason, status


class NotOnRoster(Exception):
    def __init__(self, email):
        super().__init__("not on roster")
        self.email = email


def _retry_after(reply):
    """Seconds to wait after a 429: the JSON `retry_after` if it parses, else the Retry-After header, else 300; at most
    an hour."""
    wait = None
    try:
        body = reply.json()
        if isinstance(body, dict) and body.get("retry_after") is not None:
            wait = float(body["retry_after"])
    except (ValueError, TypeError):
        wait = None
    if wait is None:
        try:
            wait = float(reply.headers.get("Retry-After", ""))
        except ValueError:
            wait = None
    return int(min(3600, max(0, 300 if wait is None else wait)))


def _loopback(url):
    return (urlparse(url).hostname or "") in LOOPBACK


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def safe_next(value):
    """A same-origin path, or "/": never an absolute or protocol-relative URL."""
    value = str(value or "")
    if not SAFE_NEXT.fullmatch(value) or value.startswith("/auth/") or any(ord(ch) < 32 for ch in value):
        return "/"
    return value


def _microsoft(issuer):
    return urlparse(issuer).hostname == "login.microsoftonline.com"


def _google(issuer):
    return urlparse(issuer).hostname == "accounts.google.com"


def read_secret_file(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        raise RuntimeError("TICO_OIDC_CLIENT_SECRET_FILE cannot be read") from None


def check(settings):
    """Refuse to start with built-in sign-in selected but not configured."""
    issuer = settings.oidc_issuer
    if not (issuer and settings.oidc_client_id):
        raise RuntimeError("TICO_AUTH_PROXY=oidc needs TICO_OIDC_ISSUER and TICO_OIDC_CLIENT_ID")
    if urlparse(issuer).scheme != "https" and not (urlparse(issuer).scheme == "http" and _loopback(issuer)):
        raise RuntimeError("TICO_OIDC_ISSUER must be an https URL")
    if _microsoft(issuer):
        tenant = urlparse(issuer).path.strip("/").split("/")[0].lower()
        if not tenant or tenant in UNPINNED_TENANTS:
            raise RuntimeError("TICO_OIDC_ISSUER for Microsoft must name your own tenant "
                               "(https://login.microsoftonline.com/<tenant-id>/v2.0), not "
                               + (tenant or "an empty tenant"))
    if settings.oidc_client_secret_file and not settings.oidc_client_secret:
        settings.oidc_client_secret = read_secret_file(settings.oidc_client_secret_file)
    if not settings.oidc_client_secret:
        raise RuntimeError("TICO_AUTH_PROXY=oidc needs TICO_OIDC_CLIENT_SECRET or TICO_OIDC_CLIENT_SECRET_FILE")
    # The redirect URI and the Secure cookies both need https; loopback is the development exception.
    if not settings.public_url.startswith("https://") and not settings.loopback:
        raise RuntimeError("TICO_AUTH_PROXY=oidc needs an https TICO_PUBLIC_URL (loopback may use http)")
    if settings.session_secret and len(settings.session_secret) < 32:
        raise RuntimeError("TICO_SESSION_SECRET must be at least 32 characters")
    if any("@" in d or "." not in d for d in settings.oidc_allowed_domains):
        raise RuntimeError("TICO_OIDC_ALLOWED_DOMAINS is a comma-separated list of domains such as acme.com")
    if settings.oidc_label and not LABEL.fullmatch(settings.oidc_label):
        raise RuntimeError("TICO_OIDC_LABEL is the provider's name for the sign-in button, at most 40 characters")


def _session_secret(settings):
    if settings.session_secret:
        return settings.session_secret.encode()
    path = settings.db_path.parent / SECRET_FILE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError(str(path) + " must be readable only by its owner (chmod 600)") from None
        value = path.read_text().strip()
        if len(value) < 32:
            raise RuntimeError(str(path) + " is too short to sign sessions; delete it to generate a new one")
        return value.encode()
    value = secrets.token_hex(32)
    with os.fdopen(fd, "w") as handle:
        handle.write(value)
    return value.encode()


class Oidc:
    name = "oidc"
    # Marks a proxy that keeps its sessions in the database: `email` takes the connection.
    sessions = True

    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.issuer = settings.oidc_issuer
        self.secret = _session_secret(settings)
        self.secure = settings.public_url.startswith("https://")
        prefix = "__Host-" if self.secure else ""
        self.cookie = prefix + "tico_session"
        self.login_cookie = prefix + "tico_login"
        self.redirect_uri = settings.public_url + CALLBACK_PATH
        # Where a signed-out browser goes: the button page when the provider has a name to show, else the provider.
        self.sign_in_path = SIGN_IN_PATH if settings.oidc_label else LOGIN_PATH
        self.jwks_min_interval = JWKS_MIN_INTERVAL
        self._lock = threading.Lock()
        self._discovery, self._discovered_at = None, 0.0
        self._keys, self._jwks_at = {}, None

    def warm(self):
        self._discover()

    # ---- provider metadata ------------------------------------------------------------

    def _fetch(self, url):
        if urlparse(url).scheme != "https" and not (_loopback(self.issuer) and _loopback(url)):
            raise SignInError("provider URL is not https", 502)
        try:
            reply = httpx.get(url, timeout=10, follow_redirects=False)
            reply.raise_for_status()
            data = reply.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SignInError("provider fetch failed: " + type(exc).__name__, 502) from exc
        if not isinstance(data, dict):
            raise SignInError("provider answered with something else than an object", 502)
        return data

    def _discover(self):
        with self._lock:
            if self._discovery and time.monotonic() - self._discovered_at < DISCOVERY_TTL:
                return self._discovery
        doc = self._fetch(self.issuer.rstrip("/") + "/.well-known/openid-configuration")
        # A tenant given by name is published under its id, so only Microsoft may differ.
        if str(doc.get("issuer", "")).rstrip("/") != self.issuer.rstrip("/") and not _microsoft(self.issuer):
            raise SignInError("discovery issuer does not match TICO_OIDC_ISSUER", 502)
        for field in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = doc.get(field)
            if not isinstance(value, str) or not value:
                raise SignInError("discovery lacks " + field, 502)
            if field != "issuer" and urlparse(value).scheme != "https" and not (
                    _loopback(self.issuer) and _loopback(value)):
                raise SignInError("discovery " + field + " is not https", 502)
        with self._lock:
            self._discovery, self._discovered_at = doc, time.monotonic()
        return doc

    def _load_keys(self, doc):
        data = self._fetch(doc["jwks_uri"])
        keys = {}
        for entry in data.get("keys") or []:
            if not isinstance(entry, dict) or entry.get("use") == "enc" or not entry.get("kid"):
                continue
            try:
                keys[str(entry["kid"])] = jwt.PyJWK(entry).key
            except (jwt.PyJWTError, ValueError, KeyError):
                continue
        return keys

    def _key(self, kid, doc):
        with self._lock:
            key = self._keys.get(kid)
            recent = (self._jwks_at is not None
                      and time.monotonic() - self._jwks_at < self.jwks_min_interval)
            if key or recent:
                if not key:
                    raise SignInError("unknown signing key")
                return key
            self._jwks_at = time.monotonic()
        keys = self._load_keys(doc)
        with self._lock:
            self._keys = keys
        if kid not in keys:
            raise SignInError("unknown signing key")
        return keys[kid]

    # ---- the login round trip ---------------------------------------------------------

    def _sign(self, payload):
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return body + "." + _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())

    def _unsign(self, value):
        body, _, mac = str(value or "").partition(".")
        good = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        if not body or not hmac.compare_digest(mac, good):
            raise SignInError("login cookie is not ours")
        try:
            payload = json.loads(_unb64(body))
        except ValueError:
            raise SignInError("login cookie is unreadable") from None
        if not isinstance(payload, dict) or float(payload.get("e", 0)) < time.time():
            raise SignInError("login expired")
        return payload

    def begin(self, target, fresh=False, challenge="", prompt=""):
        """The provider URL to send the browser to, and the signed cookie that remembers this attempt.
        `target` is a same-origin path, or (with the PKCE `challenge`) the URL of an allowed frontend.
        `prompt` is passed to the provider when it is one of PROMPTS; `fresh` asks for the account chooser."""
        doc = self._discover()
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        params = {"response_type": "code", "client_id": self.settings.oidc_client_id,
                  "redirect_uri": self.redirect_uri, "scope": "openid email profile",
                  "state": state, "nonce": nonce, "code_challenge_method": "S256",
                  "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest())}
        domains = self.settings.oidc_allowed_domains
        if _google(self.issuer) and len(domains) == 1:
            params["hd"] = domains[0]            # a hint only; the ID token's claim is what is checked
        if prompt in PROMPTS:
            params["prompt"] = prompt
        elif fresh:
            params["prompt"] = "select_account"
        endpoint = doc["authorization_endpoint"]
        url = endpoint + ("&" if "?" in endpoint else "?") + urlencode(params)
        frontend = bool(challenge)
        cookie = self._sign({"s": state, "n": nonce, "v": verifier, "x": target if frontend else safe_next(target),
                             "c": challenge, "e": time.time() + LOGIN_WINDOW})
        return url, cookie

    def finish(self, query, cookie):
        """The verified email of a finished login, the path (or frontend URL) to return to, the frontend's PKCE
        challenge ("" for the built-in page), and the account it is: (issuer, subject). Raises SignInError.
        No database is held open: this waits on the provider."""
        pending = self._unsign(cookie)
        if query.get("error"):
            raise SignInError("provider refused: " + re.sub(r"[^a-z_]", "", str(query["error"]))[:40])
        code, state = str(query.get("code") or ""), str(query.get("state") or "")
        if not code or not hmac.compare_digest(state.encode(), str(pending["s"]).encode()):
            raise SignInError("state mismatch")
        doc = self._discover()
        claims = self._verify(self._exchange(doc, code, pending["v"]), pending["n"], doc)
        return (self._email(claims), pending["x"], str(pending.get("c") or ""),
                (str(claims["iss"]), str(claims["sub"])))

    def person(self, c, email, account=None):
        """The roster id this verified sign-in is. The account (issuer, subject) is matched first, so a person who changed
        their address keeps their place, and the address moves to the new one unless someone else has it. Otherwise the
        email is matched, and the account is remembered. An address the owner admits joins here on its first verified
        sign-in (the same rule as the proxy's join, under this transaction). Raises NotOnRoster."""
        from . import access
        if account:
            issuer, sub = account
            row = c.execute("SELECT id,email FROM humans WHERE idp_issuer=? AND idp_sub=?", (issuer, sub)).fetchone()
            if row:
                if self._refused(c, row["id"]):
                    raise NotOnRoster(email)
                if str(row["email"] or "").lower() != email:
                    self._move_email(c, row["id"], email)
                return row["id"]
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
        if not row:
            # An address the owner admits (Settings > People) joins on its first verified sign-in; a retired one never does.
            if email in access.retired_emails(c) or not access.admitted(access.load_access(c, self.settings), email):
                raise NotOnRoster(email)
            row = {"id": access.join_on_sign_in(c, email)}
        elif self._refused(c, row["id"]):
            raise NotOnRoster(email)
        if account:
            self._remember(c, row["id"], account)
        return row["id"]

    def _move_email(self, c, pid, email):
        """The person's sign-in address changes to the one the account verified now. Refused (NotOnRoster) when a
        retired address or another person's. Everything follows: see access.change_email / change_owner_email."""
        from . import access, views
        from . import people as P
        if email in access.retired_emails(c) or access._email_taken(c, email, pid):
            raise NotOnRoster(email)
        roster = views.roster(c)
        person = P.person(pid, roster)
        owner = access.load_owner(c, self.settings)
        actor = "human:" + pid
        try:
            if person and person["email"] and person["email"] == owner["email"]:
                access.change_owner_email(c, actor, roster, owner, email, expected_revision=owner["revision"],
                                          now=H.now(), settings=self.settings, via="sign_in")
            else:
                access.change_email(c, actor, roster, pid, email, now=H.now(), settings=self.settings, via="sign_in")
        except Problem as exc:
            log.info("sign-in address change refused: %s", exc.code)
            raise NotOnRoster(email) from exc

    def _remember(self, c, pid, account):
        """Store the account a person signed in with. A different subject from the same issuer is an account change: it
        is audited, and this issuer's email check is still what let it in."""
        issuer, sub = account
        row = c.execute("SELECT idp_issuer,idp_sub FROM humans WHERE id=?", (pid,)).fetchone()
        if row and row["idp_sub"] and row["idp_issuer"] == issuer and row["idp_sub"] != sub:
            H.event(c, "human:" + pid, "person.account_changed", pid, {"issuer": issuer})
        c.execute("UPDATE humans SET idp_issuer=?, idp_sub=? WHERE id=?", (issuer, sub, pid))

    def _refused(self, c, human):
        """Whether the roster refuses this person's sign-in: hidden, left the directory, or sign-in turned off. Checked
        before any address moves, account is remembered or session issued."""
        from . import views
        from . import people as P
        person = next((p for p in views.roster(c).get("people", []) if p.get("id") == human), None)
        return bool(person and not P.may_sign_in(person))

    def _exchange(self, doc, code, verifier):
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                "code_verifier": verifier}
        auth = None
        methods = doc.get("token_endpoint_auth_methods_supported")
        if isinstance(methods, list) and "client_secret_post" not in methods and "client_secret_basic" in methods:
            auth = httpx.BasicAuth(self.settings.oidc_client_id, self.settings.oidc_client_secret)
        else:
            data.update(client_id=self.settings.oidc_client_id, client_secret=self.settings.oidc_client_secret)
        try:
            reply = httpx.post(doc["token_endpoint"], data=data, auth=auth, timeout=10, follow_redirects=False)
            body = reply.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SignInError("token exchange failed: " + type(exc).__name__, 502) from exc
        token = body.get("id_token") if isinstance(body, dict) else None
        if reply.status_code != 200 or not isinstance(token, str) or not token:
            raise SignInError("token endpoint refused the code")
        return token

    def _verify(self, token, nonce, doc):
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise SignInError("id token is malformed") from exc
        allowed = doc.get("id_token_signing_alg_values_supported")
        algs = [a for a in ALGORITHMS if not isinstance(allowed, list) or a in allowed] or ["RS256"]
        if header.get("alg") not in algs or not isinstance(header.get("kid"), str):
            raise SignInError("id token algorithm or key id not accepted")
        key = self._key(header["kid"], doc)
        try:
            claims = jwt.decode(token, key, algorithms=[header["alg"]], audience=self.settings.oidc_client_id,
                                issuer=doc["issuer"], leeway=LEEWAY,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.PyJWTError as exc:
            raise SignInError("id token rejected: " + type(exc).__name__) from exc
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 \
                and claims.get("azp") != self.settings.oidc_client_id:
            raise SignInError("id token authorized party mismatch")
        if not hmac.compare_digest(str(claims.get("nonce") or "").encode(), str(nonce).encode()):
            raise SignInError("nonce mismatch")
        if _microsoft(self.issuer):
            tenant = urlparse(doc["issuer"]).path.strip("/").split("/")[0].lower()
            if str(claims.get("tid") or "").lower() != tenant:
                raise SignInError("token is from another tenant")
        return claims

    def _email(self, claims):
        microsoft = _microsoft(self.issuer)
        email = claims.get("email")
        if microsoft and not (isinstance(email, str) and "@" in email):
            email = claims.get("preferred_username")
        if not isinstance(email, str) or "@" not in email:
            raise SignInError("id token carries no email")
        email = email.strip().lower()
        verified = claims.get("email_verified")
        if isinstance(verified, str):
            verified = verified.lower() == "true"
        # Microsoft publishes no email_verified; its tenant check above is what vouches for the address.
        if ("email_verified" in claims or not microsoft) and verified is not True:
            raise SignInError("email is not verified")
        domains = self.settings.oidc_allowed_domains
        if domains:
            if email.rsplit("@", 1)[1] not in domains:
                raise SignInError("email domain is not allowed")
            if _google(self.issuer) and str(claims.get("hd") or "").lower() not in domains:
                raise SignInError("hosted domain is not allowed")
        return email

    # ---- sessions -----------------------------------------------------------------------

    def start_session(self, c, human, email, previous=""):
        """A new session for this person. The id changes on every sign-in, and a session id
        the browser already held is dropped with it."""
        now = H.now()
        if previous:
            c.execute("DELETE FROM oidc_sessions WHERE id_hash=?", (digest(previous),))
        c.execute("DELETE FROM oidc_sessions WHERE expires_at<=? OR last_seen_at<?",
                  (now, H.shift(now, seconds=-self.settings.session_idle_seconds)))
        sid = secrets.token_urlsafe(32)
        c.execute("INSERT INTO oidc_sessions(id_hash,human,email,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?,?)",
                  (digest(sid), human, email, now, now, H.shift(now, seconds=self.settings.session_absolute_seconds)))
        return sid

    # ---- invites (backend/invites.py) ---------------------------------------------------

    def invite(self, email):
        """Ask the issuer to mail `email` an invite to sign in here: (HTTP status, answer), or None when the issuer
        sends no invites. Only a Tico account issuer does: its discovery names `tico_invite_endpoint` on its own host,
        so this client's secret never goes anywhere the token exchange would not send it. A rehearsal asks nothing,
        discovery included: it is a copy of real data, and the issuer would mail a real person."""
        return self._tico_call("tico_invite_endpoint", email, "Invites are off in a rehearsal: this server is a copy of "
                                                              "real data and sends no mail")

    def make_account(self, email):
        """Ask the issuer to make `email` a Tico account with a generated password (backend/invites.py
        `make_password`): (HTTP status, answer), or None when the issuer makes none. Same rules as `invite`: only a
        Tico account issuer's own `tico_accounts_endpoint`, never in a rehearsal. The answer carries the password once;
        the caller hands it to the owner and keeps nothing."""
        return self._tico_call("tico_accounts_endpoint", email, "Accounts aren't made in a rehearsal: this server is a "
                                                                "copy of real data")

    def offers(self, field):
        """Whether the issuer's discovery names a usable `field` endpoint (the cached copy, or a fresh read). For the
        People page, so it never raises."""
        try:
            return self._endpoint(self._discover(), field) is not None
        except (SignInError, Problem):
            return False

    def _endpoint(self, doc, field):
        endpoint = doc.get(field)
        if not isinstance(endpoint, str) or urlparse(endpoint).netloc != urlparse(self.issuer).netloc or (
                urlparse(endpoint).scheme != "https" and not (_loopback(self.issuer) and _loopback(endpoint))):
            return None
        return endpoint

    def _tico_call(self, field, email, rehearsal):
        from .invites import rehearsing
        if rehearsing(self.settings):
            raise Problem("rehearsal", rehearsal, 422)
        endpoint = self._endpoint(self._discover(), field)
        if endpoint is None:
            return None
        data = {"client_id": self.settings.oidc_client_id, "client_secret": self.settings.oidc_client_secret,
                "email": email}
        try:
            reply = httpx.post(endpoint, data=data, timeout=20, follow_redirects=False)
            answer = reply.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SignInError(field.split("_")[1] + " failed: " + type(exc).__name__, 502) from exc
        return reply.status_code, answer if isinstance(answer, dict) else {}

    def push_people(self, rows):
        """Send the install's sign-in list to the Tico account (backend/people_mirror.py): (HTTP status, answer), or None
        when the account takes none (no `tico_people_endpoint`) or this is a rehearsal, which sends nothing out.
        Same client credentials as `invite`; only the issuer's own host is ever sent to."""
        from .invites import rehearsing
        if rehearsing(self.settings):
            return None
        endpoint = self._endpoint(self._discover(), "tico_people_endpoint")
        if endpoint is None:
            return None
        data = {"client_id": self.settings.oidc_client_id, "client_secret": self.settings.oidc_client_secret,
                "people": json.dumps(rows, separators=(",", ":"), sort_keys=True)}
        try:
            reply = httpx.post(endpoint, data=data, timeout=20, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise SignInError("people list failed: " + type(exc).__name__, 502) from exc
        if reply.status_code == 429:
            # The account may answer in HTML (a proxy's limit page): the wait then comes from its header, if any.
            return 429, {"retry_after": _retry_after(reply)}
        try:
            answer = reply.json()
        except ValueError:
            answer = None
        return reply.status_code, answer if isinstance(answer, dict) else {}

    def email(self, headers, c):
        """The signed-in person's email, or None when the request has no live session."""
        from .auth import cookies
        return self.session_email(cookies(headers).get(self.cookie, ""), c)

    def session_email(self, raw, c):
        """The email behind a session id, from the cookie or from a bearer session."""
        if not raw:
            return None
        row = c.execute("SELECT s.id_hash,s.last_seen_at,s.expires_at,h.email FROM oidc_sessions s "
                        "JOIN humans h ON h.id=s.human WHERE s.id_hash=?", (digest(raw),)).fetchone()
        if not row:
            return None
        now = H.now()
        if row["expires_at"] <= now or row["last_seen_at"] < H.shift(now, seconds=-self.settings.session_idle_seconds):
            return None
        if row["last_seen_at"] < H.shift(now, seconds=-TOUCH_EVERY):
            # A hint for the idle clock, written only if the database is free right now (auth.touch): a sign-in never
            # queues behind writers for it, and a skipped one is retried by the next request a minute on.
            from .auth import touch
            touch(c, "UPDATE oidc_sessions SET last_seen_at=? WHERE id_hash=?", (now, row["id_hash"]))
        return str(row["email"] or "").lower()

    def logout(self, headers):
        from .auth import cookies
        raw = cookies(headers).get(self.cookie, "")
        if raw:
            with self.store.transaction() as c:
                c.execute("DELETE FROM oidc_sessions WHERE id_hash=?", (digest(raw),))
        return SIGNED_OUT_PATH, [self.cookie]

    # ---- separate frontends (docs/custom-frontend.md) ---------------------------------------

    def frontend_target(self, value):
        """The URL of an allowed frontend, normalised, or None. Its origin must be exactly one of
        TICO_CORS_ORIGINS; a fragment, credentials or an odd path are refused, so the redirect cannot
        land anywhere but that app's own page."""
        try:
            url = urlparse(str(value or ""))
            origin = "%s://%s" % (url.scheme, url.netloc)
            path = url.path or "/"
            if (origin.lower() not in self.settings.cors_origins or url.username is not None or url.fragment
                    or not (path == "/" or SAFE_NEXT.fullmatch(path))
                    or any(ord(ch) < 32 for ch in str(value))):
                return None
            return origin.lower() + path + ("?" + url.query if url.query else "")
        except ValueError:
            return None

    def issue_code(self, c, human, email, challenge, origin):
        """A one-time code for the frontend at `origin`; only its hash is stored, and it is good for a minute."""
        now = H.now()
        c.execute("DELETE FROM oidc_codes WHERE expires_at<=?", (now,))
        code = secrets.token_urlsafe(32)
        c.execute("INSERT INTO oidc_codes(code_hash,human,email,challenge,origin,expires_at) VALUES(?,?,?,?,?,?)",
                  (digest(code), human, email, challenge, origin, H.shift(now, seconds=CODE_WINDOW)))
        return code

    def redeem_code(self, c, code, verifier, origin):
        """A bearer session for the code, once. The code is spent whether or not the rest checks out."""
        row = c.execute("SELECT * FROM oidc_codes WHERE code_hash=?", (digest(code),)).fetchone()
        if row:
            c.execute("DELETE FROM oidc_codes WHERE code_hash=?", (digest(code),))
        good = _b64(hashlib.sha256(verifier.encode()).digest()) if VERIFIER.fullmatch(verifier) else ""
        if (not row or row["expires_at"] <= H.now() or not hmac.compare_digest(good, row["challenge"])
                or not origin or origin.lower() != row["origin"]):
            return None
        return self.start_session(c, row["human"], row["email"]), row["human"]

    def revoke_session(self, raw):
        with self.store.transaction() as c:
            c.execute("DELETE FROM oidc_sessions WHERE id_hash=?", (digest(raw),))

    def set_cookie(self, response, name, value, max_age):
        response.set_cookie(name, value, max_age=max_age, path="/", secure=self.secure,
                            httponly=True, samesite="lax")


@functools.lru_cache(maxsize=4)
def _wordmark(ui_dir, name):
    """One of the wordmark SVGs, inlined so a sign-in page needs no second request."""
    try:
        svg = (Path(ui_dir) / "assets/tico" / name).read_text()
    except OSError:
        return ""
    return svg.replace("<svg ", '<svg class="logo" ', 1)


def _brand(settings):
    """Tico's wordmark (dark ink, reversed in a dark scheme) while the app is called Tico; a company
    that named its app gets the name as text."""
    if settings.app_name != PRODUCT_NAME:
        return '<p class="brand">' + html.escape(settings.app_name) + "</p>"
    light, dark = _wordmark(settings.ui_dir, "tico-wordmark.svg"), _wordmark(settings.ui_dir, "tico-wordmark-reversed.svg")
    return '<div class="brand" role="img" aria-label="' + html.escape(PRODUCT_NAME) + '">' + (
        '<span class="on-light">' + light + '</span><span class="on-dark">' + dark + "</span></div>") if light and dark else ""


def _page(settings, status, title, message, action=None):
    button = ""
    if action:
        button = '<p><a class="b" href="' + html.escape(action[0]) + '">' + html.escape(action[1]) + "</a></p>"
    body = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<title>" + html.escape(title) + " - " + html.escape(settings.app_name) + "</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;background:#f6f6f4;color:#1c1c1a;margin:0;"
        "display:grid;place-items:center;min-height:100vh}main{max-width:26rem;padding:2rem;background:#fff;"
        "border-radius:12px;box-shadow:0 1px 8px #0002;margin:1rem}h1{font-size:1.25rem;margin:0 0 .5rem}"
        ".b{display:inline-block;padding:.6rem 1rem;background:#1c1c1a;color:#fff;border-radius:8px;"
        "text-decoration:none}.brand{margin:0 0 1.25rem;font-weight:700;font-size:1.25rem}.logo{height:1.75rem;width:auto;display:block}"
        ".on-dark{display:none}@media(prefers-color-scheme:dark){body{background:#151514;color:#eee}"
        "main{background:#222}.b{background:#eee;color:#111}.on-light{display:none}.on-dark{display:block}}"
        "</style></head><body><main>" + _brand(settings) + "<h1>"
        + html.escape(title) + "</h1><p>" + message + "</p>" + button + "</main></body></html>")
    return HTMLResponse(body, status_code=status)


def register(app, auth):
    """`/auth/login`, `/auth/callback` and `/auth/signed-out`. They answer 404 unless the
    server was started with TICO_AUTH_PROXY=oidc."""
    settings, store = auth.settings, auth.store

    def active():
        if not isinstance(auth.proxy, Oidc):
            raise Problem("not_found", "Not found", 404)
        return auth.proxy

    def canonical_entry(request, path, query):
        # Existing single-host proxies may replace Host; canonical entry is opt-in with aliases.
        if not os.environ.get("TICO_DOMAIN_ALIASES", "").strip():
            return None
        # Pending login cookies are host-only, while the provider's callback is canonical.
        # Compare Host, not the proxy's internal HTTP scheme, and never trust forwarded hosts.
        canonical = urlparse(settings.public_url)
        hosts = request.headers.getlist("host")
        host = hosts[0] if len(hosts) == 1 else ""
        try:
            incoming = urlparse("//" + host)
            port = 443 if canonical.scheme == "https" else 80
            same = (bool(canonical.hostname) and bool(host) and host.isascii()
                    and all(32 < ord(ch) < 127 for ch in host)
                    and incoming.netloc == host and not host.endswith(":")
                    and incoming.username is None and incoming.password is None
                    and not (incoming.path or incoming.query or incoming.fragment)
                    and incoming.hostname == canonical.hostname
                    and (incoming.port if incoming.port is not None else port)
                    == (canonical.port if canonical.port is not None else port))
        except ValueError:
            same = False
        if same:
            return None
        query = urlencode({key: value for key, value in query.items() if value})
        return RedirectResponse(settings.public_url + path + ("?" + query if query else ""), status_code=302,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.get(LOGIN_PATH)
    def login(request: Request, next: str = "/", fresh: str = "", code_challenge: str = "", prompt: str = ""):
        oidc = active()
        challenge = ""
        target = None if next.startswith("/") else oidc.frontend_target(next)
        if target or (code_challenge and not next.startswith("/")):
            # A separate frontend: only an allowed origin, and only with a PKCE challenge.
            if not target or not CHALLENGE.fullmatch(code_challenge):
                return _page(settings, 400, "This app cannot sign you in",
                             "Its address is not one this server allows (TICO_CORS_ORIGINS), or it did not send "
                             "a PKCE code_challenge.")
            next, challenge = target, code_challenge
        next = next if challenge else safe_next(next)
        prompt = prompt if prompt in PROMPTS else ""
        entry = canonical_entry(request, LOGIN_PATH, {
            "next": next if next != "/" else "", "fresh": "1" if fresh else "", "code_challenge": challenge,
            "prompt": prompt})
        if entry is not None:
            return entry
        try:
            url, cookie = oidc.begin(next, bool(fresh), challenge, prompt)
        except SignInError as exc:
            log.warning("sign-in could not start: %s", exc.reason)
            return _page(settings, 502, "Sign-in is unavailable",
                         "The sign-in provider could not be reached. Try again in a minute.",
                         (LOGIN_PATH, "Try again"))
        response = RedirectResponse(url, status_code=302)
        oidc.set_cookie(response, oidc.login_cookie, cookie, LOGIN_WINDOW)
        return response

    @app.get(SIGN_IN_PATH)
    def sign_in_page(request: Request, next: str = "/"):
        """One button, "Sign in with <TICO_OIDC_LABEL>", to the provider; without a label, straight to it."""
        active()
        next = safe_next(next)
        entry = canonical_entry(request, SIGN_IN_PATH, {"next": next if next != "/" else ""})
        if entry is not None:
            return entry
        target = LOGIN_PATH + ("?" + urlencode({"next": next}) if next != "/" else "")
        if not settings.oidc_label:
            return RedirectResponse(target, status_code=302)
        page = _page(settings, 200, "Sign in to " + settings.app_name,
                     "You come straight back here once you're signed in.",
                     (target, "Sign in with " + settings.oidc_label))
        page.headers["Cache-Control"] = "no-store"
        return page

    @app.get(CALLBACK_PATH)
    def callback(request: Request):
        from .auth import cookies
        oidc = active()
        jar = cookies(request.headers)
        try:
            email, target, challenge, account = oidc.finish(dict(request.query_params), jar.get(oidc.login_cookie, ""))
            with store.transaction() as c:
                human = oidc.person(c, email, account)
                if challenge:
                    # No cookie for a frontend: it gets a code, in the fragment so no server or log sees it.
                    origin = "%s://%s" % urlparse(target)[:2]
                    sid = None
                    code = oidc.issue_code(c, human, email, challenge, origin)
                else:
                    sid = oidc.start_session(c, human, email, jar.get(oidc.cookie, ""))
        except NotOnRoster as exc:
            # The browser's old session (of whoever was signed in before) ends with this refusal.
            if jar.get(oidc.cookie):
                oidc.revoke_session(jar.get(oidc.cookie))
            response = _page(settings, 403, "You're not on the list",
                             "You're signed in as <b>" + html.escape(exc.email) + "</b>, and that address is not on "
                             + html.escape(settings.company_name) + "'s list for " + html.escape(settings.app_name) + ".",
                             (LOGIN_PATH + "?prompt=select_account", "Use another account"))
            response.delete_cookie(oidc.login_cookie, path="/", secure=oidc.secure, httponly=True)
            response.delete_cookie(oidc.cookie, path="/", secure=oidc.secure, httponly=True)
            return response
        except SignInError as exc:
            log.warning("sign-in refused: %s", exc.reason)
            response = _page(settings, exc.status, "Sign-in did not complete",
                             "Your sign-in could not be confirmed. Start again; if it keeps failing, "
                             "check that you are using your work account.", (LOGIN_PATH, "Try again"))
            response.delete_cookie(oidc.login_cookie, path="/", secure=oidc.secure, httponly=True)
            return response
        if challenge:
            response = RedirectResponse(target + "#" + urlencode({"tico_code": code}), status_code=302)
        else:
            response = RedirectResponse(target, status_code=302)
            oidc.set_cookie(response, oidc.cookie, sid, settings.session_absolute_seconds)
        response.delete_cookie(oidc.login_cookie, path="/", secure=oidc.secure, httponly=True)
        return response

    @app.post(TOKEN_PATH)
    async def token(request: Request):
        """Exchange the one-time code a frontend got in its URL for a bearer session."""
        oidc = active()
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not all(isinstance(body.get(k), str) for k in ("code", "code_verifier")):
            raise Problem("invalid_request", "Send JSON with code and code_verifier", 400)
        def redeem():
            with store.transaction() as c:
                return oidc.redeem_code(c, body["code"][:200], body["code_verifier"][:200],
                                        request.headers.get("origin", ""))
        made = await asyncio.to_thread(redeem)
        if not made:
            raise Problem("invalid_grant", "The code is wrong, expired, already used, or from another app", 400)
        sid, human = made
        return JSONResponse({"access_token": BEARER_PREFIX + sid, "token_type": "Bearer",
                             "expires_in": settings.session_absolute_seconds,
                             "idle_timeout": settings.session_idle_seconds, "person": human},
                            headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    @app.post(REVOKE_PATH)
    def revoke(request: Request):
        """Sign a bearer session out (the request is already authenticated by it)."""
        oidc = active()
        bearer = request.headers.get("authorization", "")
        if bearer.startswith("Bearer " + BEARER_PREFIX):
            oidc.revoke_session(bearer[7 + len(BEARER_PREFIX):])
        return {"revoked": True}

    @app.get(SIGNED_OUT_PATH)
    def signed_out():
        active()
        return _page(settings, 200, "You're signed out", "Your session on this browser has ended.",
                     (LOGIN_PATH + "?fresh=1", "Sign in again"))
