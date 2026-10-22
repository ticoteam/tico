"""Verifiers for who a browser is: Cloudflare Access, an AWS ALB, the built-in OIDC sign-in, or a hosted owner's
sign-in link (backend/owner_link.py).

Each verifier reads only its own provider's credentials, so a header meant for the other
provider is inert. `email()` returns the verified, lowercased email, or None when the request
carries no credential of this provider; a credential that is present but wrong raises a 401.
The caller matches the email against the roster, the same way for every provider.
"""

import base64
import json
import re
import threading
import time
from urllib.parse import quote

import httpx
import jwt

from .store import Problem

PROXIES = ("cloudflare", "aws-alb", "oidc", "owner-link")
ALB_KID = re.compile(r"[A-Za-z0-9-]{1,64}")
ALB_REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d")
ALB_ARN = re.compile(r"arn:aws[a-z-]*:elasticloadbalancing:[a-z0-9-]+:\d{12}:loadbalancer/.+")
ALB_COOKIE_PREFIX = "AWSELBAuthSessionCookie"
# One ALB signs with a handful of keys; the bound only stops a stream of made-up kids.
KEY_CACHE_MAX = 16
NEGATIVE_TTL = 30.0


def _segment(part):
    """One base64url JSON segment, padded or not; anything but an object is refused."""
    value = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    if not isinstance(value, dict):
        raise ValueError("segment is not an object")
    return value


def _display_name(claims):
    """A person's name from verified claims, kept short and on one line; never an address."""
    name = " ".join(str((claims or {}).get("name") or "").split())[:100]
    return "" if "@" in name else name


def _invalid(exc=None):
    return Problem("identity", "Invalid or expired sign-in", 401)


def check(settings):
    """Refuse to start with a proxy selected but not configured."""
    kind = settings.proxy_kind
    if kind and kind not in PROXIES:
        raise RuntimeError("TICO_AUTH_PROXY must be one of: " + ", ".join(PROXIES))
    if kind == "cloudflare" and not (settings.access_issuer and settings.access_audience):
        raise RuntimeError("TICO_AUTH_PROXY=cloudflare needs TICO_ACCESS_ISSUER and TICO_ACCESS_AUDIENCE")
    if kind == "aws-alb":
        if not settings.alb_arn or not settings.alb_region:
            raise RuntimeError("TICO_AUTH_PROXY=aws-alb needs TICO_ALB_ARN and TICO_ALB_REGION")
        if not ALB_ARN.fullmatch(settings.alb_arn):
            raise RuntimeError("TICO_ALB_ARN must be the load balancer's ARN")
        if not ALB_REGION.fullmatch(settings.alb_region):
            raise RuntimeError("TICO_ALB_REGION must be an AWS region such as us-west-2")
    if kind == "oidc":
        from . import oidc, owner_link
        oidc.check(settings)
        if owner_link.break_glass(settings):
            owner_link.check(settings)
    if kind == "owner-link":
        from . import owner_link
        owner_link.check(settings)
    if settings.cognito_logout_url and not settings.cognito_logout_url.startswith("https://"):
        raise RuntimeError("TICO_COGNITO_LOGOUT_URL must be an https URL")


def build(settings, store=None):
    if settings.proxy_kind == "cloudflare":
        return CloudflareAccess(settings)
    if settings.proxy_kind == "aws-alb":
        return AwsAlb(settings)
    if settings.proxy_kind == "oidc":
        from . import oidc
        return oidc.Oidc(settings, store)
    if settings.proxy_kind == "owner-link":
        from . import owner_link
        return owner_link.OwnerLink(settings, store)
    return None


def _cookies(headers):
    from .auth import cookies
    return cookies(headers)


class CloudflareAccess:
    name = "cloudflare"

    def __init__(self, settings, jwks=None):
        self.settings = settings
        self.jwks = jwks
        url = (settings.access_jwks_url
               or ((settings.access_issuer + "/cdn-cgi/access/certs") if settings.access_issuer else ""))
        if jwks is None and url:
            # Keys are kept for a day: the default 5-minute lifespan re-downloaded them on the
            # first request after every pause, once per concurrent request of a page load. An
            # unknown key id still refreshes at once.
            self.jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=86400, timeout=10)

    def warm(self):
        if self.jwks:
            self.jwks.get_signing_keys()

    def _claims(self, headers):
        # Machine API paths bypass the edge login redirect; browsers still send their signed
        # session cookie, which gets the same checks as the header.
        assertion = headers.get("cf-access-jwt-assertion", "") or _cookies(headers).get("CF_Authorization", "")
        if not assertion:
            return None
        if not self.jwks or not self.settings.access_audience:
            raise Problem("identity", "Sign in to " + self.settings.app_name, 401)
        try:
            key = self.jwks.get_signing_key_from_jwt(assertion).key
            return jwt.decode(assertion, key, algorithms=["RS256"],
                              issuer=self.settings.access_issuer,
                              audience=self.settings.access_audience,
                              options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.PyJWTError as exc:
            raise _invalid() from exc

    def email(self, headers):
        claims = self._claims(headers)
        return None if claims is None else str(claims.get("email", "")).lower()

    def display_name(self, headers):
        """The name in the verified assertion, when the identity provider sends one (a `name` claim), else ""."""
        return _display_name(self._claims(headers))

    def logout(self, headers):
        return "/cdn-cgi/access/logout", []


class AwsAlb:
    name = "aws-alb"

    def __init__(self, settings):
        self.settings = settings
        base = settings.alb_keys_url or ("https://public-keys.auth.elb." + settings.alb_region
                                         + ".amazonaws.com")
        self.keys_url = base.rstrip("/")
        self._keys = {}
        self._missing = {}
        self._lock = threading.Lock()

    def warm(self):
        pass                            # keys are per kid, and only known from a token

    def _key(self, kid):
        now = time.monotonic()
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            if self._missing.get(kid, 0) > now:
                raise _invalid()
        try:
            reply = httpx.get(self.keys_url + "/" + quote(kid, safe=""), timeout=5, follow_redirects=False)
            reply.raise_for_status()
            key = jwt.get_algorithm_by_name("ES256").prepare_key(reply.content)
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            with self._lock:
                if len(self._missing) >= KEY_CACHE_MAX * 4:
                    self._missing.clear()
                self._missing[kid] = now + NEGATIVE_TTL
            raise _invalid() from exc
        with self._lock:
            if len(self._keys) >= KEY_CACHE_MAX:
                self._keys.pop(next(iter(self._keys)))
            self._keys[kid] = key
        return key

    def display_name(self, headers):
        """The `name` claim of the verified ALB assertion, or ""."""
        return _display_name(self._claims(headers))

    def email(self, headers):
        claims = self._claims(headers)
        if claims is None:
            return None
        email = str(claims.get("email") or "").strip().lower()
        if not email or claims.get("email_verified") in (False, "false"):
            raise _invalid()
        return email

    def _claims(self, headers):
        raw = headers.get("x-amzn-oidc-data", "").strip()
        if not raw:
            return None
        parts = raw.split(".")
        if len(parts) != 3:
            raise _invalid()
        try:
            header, claims = _segment(parts[0]), _segment(parts[1])
            signature = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        except (ValueError, TypeError) as exc:
            raise _invalid() from exc
        kid = header.get("kid")
        # Everything checked here happens before the network: an attacker's token must not
        # choose which URL is fetched or make us fetch at all.
        if (header.get("alg") != "ES256" or not isinstance(kid, str) or not ALB_KID.fullmatch(kid)
                or header.get("signer") != self.settings.alb_arn):
            raise _invalid()
        # The ALB signs its base64url segments with their "=" padding, so the signature covers
        # the header and payload exactly as received; re-encoding them first breaks every token.
        signed = (parts[0] + "." + parts[1]).encode()
        if not jwt.get_algorithm_by_name("ES256").verify(signed, self._key(kid), signature):
            raise _invalid()
        expires = claims.get("exp", header.get("exp"))
        if not isinstance(expires, (int, float)) or expires <= time.time():
            raise _invalid()
        return claims

    def logout(self, headers):
        # The ALB keeps its own session in sharded cookies; expiring them ends the session.
        names = [name for name in _cookies(headers) if name.startswith(ALB_COOKIE_PREFIX)]
        return self.settings.cognito_logout_url or "/", names
