"""Owner sign-in link for a server our team hosts (TICO_AUTH_PROXY=owner-link), or its break-glass way in.

The quick start's owner token is reusable and only safe on loopback (backend/config.py). A hosted server is
reachable by anyone, so its owner gets a different link instead: single use, unused for at most LINK_DAYS, and only
on an https address of a hosted install (TICO_HOSTED). The installer makes the link's secret on the operator's own
computer and gives the server only its hash (TICO_OWNER_LINK_HASH), so the secret is never on the server, in a
command, or in a log. The secret is the link's fragment (`/auth/owner-link#<secret>`), which a browser never sends, so
no proxy or access log in front of the server sees it either. Opening the link shows one button; the page's script
puts the fragment into the form. Pressing it spends the link and starts an ordinary session in `oidc_sessions`, which
holds a random id in a Secure, HttpOnly, SameSite=Lax cookie. The button stops mail scanners that fetch links from
using the link up. A link that was used, replaced or is too old gets a page that says to ask
Tico for a new one. `python -m backend.owner_link replace DATABASE HASH [--sign-out]` puts a new link in place of the old one
and can also sign out every owner session; the installer runs it (`python3 -m setup owner-link`).

Break-glass: a hosted install that signs people in with OpenID Connect (TICO_AUTH_PROXY=oidc, TICO_HOSTED, and a
TICO_OWNER_LINK_HASH from the installer) keeps the same link next to it, so the owner still gets in while the issuer
is down. Its sessions are the OIDC proxy's own rows, and nothing here calls the issuer.
"""

import argparse
import html
import ipaddress
import re
import sys
import threading
import time
from urllib.parse import urlsplit

from fastapi import Form, Request
from fastapi.responses import RedirectResponse, Response

from .store import H, Problem, digest

KIND = "owner-link"
LINK_PATH = "/auth/owner-link"
SCRIPT_PATH = "/auth/owner-link.js"
LINK_DAYS = 7
HASH_RE = re.compile(r"[0-9a-f]{64}")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
HOSTED = ("own", "invited")
# Wrong or spent links one address may try in a window; the next try is refused until the window ends.
FAILURES_PER_WINDOW = 10
FAILURE_WINDOW_SECONDS = 15 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_links(
 token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, revoked_at TEXT);
"""


def break_glass(settings):
    """A hosted install on the built-in OIDC sign-in that the installer gave an owner link: the link works beside it."""
    return settings.proxy_kind == "oidc" and settings.hosted in HOSTED and bool(settings.owner_link_hash)


def check(settings):
    """A reachable server takes this sign-in only as a hosted install on https, with a link to start from."""
    what = "TICO_AUTH_PROXY=owner-link" if settings.proxy_kind == KIND else "The owner sign-in link (TICO_OWNER_LINK_HASH)"
    if not settings.public_url.startswith("https://") or settings.loopback or not _is_domain(settings.public_url):
        raise RuntimeError(what + " needs an https address on a domain name (TICO_DOMAIN), not an IP "
                           "address; it is for servers Tico hosts")
    if settings.hosted not in HOSTED:
        raise RuntimeError(what + " is for a hosted install: set TICO_HOSTED to own or invited")
    if settings.owner_link_hash and not HASH_RE.fullmatch(settings.owner_link_hash):
        raise RuntimeError("TICO_OWNER_LINK_HASH must be the 64-character sha256 of the link's secret")
    if not settings.owner_email:
        raise RuntimeError(what + " needs TICO_OWNER_EMAIL")


def place_first(settings, store):
    """On every start, after the tables exist: the hash the installer gave becomes the first link, once."""
    if settings.owner_link_hash:
        with store.transaction() as c:
            place(c, settings.owner_link_hash, settings.owner_email, only_if_none=True)


def _is_domain(url):
    """An https address our zone names: a host name, never an IPv4 or IPv6 literal (no certificate of ours covers one)."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return bool(host) and "." in host


def place(c, token_hash, owner_email, *, sign_out=False, only_if_none=False):
    """Make `token_hash` the one live link: every unused older link is revoked. With `sign_out`, every session of
    the owner ends too. `only_if_none` is the first start: a server that has ever had a link keeps its own."""
    if only_if_none and c.execute("SELECT 1 FROM owner_links LIMIT 1").fetchone():
        return False
    now = H.now()
    # Every other unused link stops working; the same hash placed again (a retried command) stays live.
    c.execute("UPDATE owner_links SET revoked_at=? WHERE used_at IS NULL AND revoked_at IS NULL AND token_hash<>?",
              (now, token_hash))
    c.execute("INSERT OR IGNORE INTO owner_links(token_hash,created_at,expires_at) VALUES(?,?,?)",
              (token_hash, now, H.shift(now, days=LINK_DAYS)))
    if sign_out:
        c.execute("DELETE FROM oidc_sessions WHERE human IN (SELECT id FROM humans WHERE lower(email)=?)",
                  (owner_email,))
    return True


def spend(c, token):
    """True once per live link: the row is marked used in the caller's transaction."""
    if not TOKEN_RE.fullmatch(token or ""):
        return False
    row = c.execute("SELECT used_at,revoked_at,expires_at FROM owner_links WHERE token_hash=?", (digest(token),)).fetchone()
    if not row or row["used_at"] or row["revoked_at"] or row["expires_at"] <= H.now():
        return False
    return c.execute("UPDATE owner_links SET used_at=? WHERE token_hash=? AND used_at IS NULL",
                     (H.now(), digest(token))).rowcount == 1


class Failures:
    """Wrong-link attempts per address, kept in memory and bounded."""

    def __init__(self, limit=FAILURES_PER_WINDOW, window=FAILURE_WINDOW_SECONDS, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._seen, self._lock = {}, threading.Lock()

    def blocked(self, ip):
        with self._lock:
            start, count = self._seen.get(ip, (0.0, 0))
            return self.clock() - start < self.window and count >= self.limit

    def note(self, ip):
        with self._lock:
            now = self.clock()
            if len(self._seen) > 10000:
                self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.window}
            start, count = self._seen.get(ip, (now, 0))
            self._seen[ip] = (start, count + 1) if now - start < self.window else (now, 1)


class OwnerLink:
    """The proxy: who a browser is comes from its session, exactly like the built-in OIDC sign-in."""
    name = KIND
    sessions = True

    def __init__(self, settings, store):
        from . import oidc
        self.settings, self.store = settings, store
        self.secure = True
        self.cookie = "__Host-tico_session"
        self.failures = Failures()
        for method in ("start_session", "email", "session_email", "revoke_session", "set_cookie"):
            setattr(self, method, getattr(oidc.Oidc, method).__get__(self))

    def warm(self):
        place_first(self.settings, self.store)

    def logout(self, headers):
        from .auth import cookies
        raw = cookies(headers).get(self.cookie, "")
        if raw:
            with self.store.transaction() as c:
                c.execute("DELETE FROM oidc_sessions WHERE id_hash=?", (digest(raw),))
        return LINK_PATH + "?signed_out=1", [self.cookie]


ASK = "Ask Tico for a new sign-in link."
# The link's fragment into the form, and out of the address bar and the history.
SCRIPT = (
    "(function(){var t=location.hash.slice(1),f=document.getElementById('owner-link-token'),"
    "b=document.getElementById('owner-link-button');if(t&&f&&b){f.value=t;b.disabled=false;}"
    "if(location.hash)history.replaceState(null,'',location.pathname);})();\n")


def register(app, auth):
    """GET shows the button, POST spends the link. Both answer 404 unless this is an owner-link server, or a hosted
    OIDC one with the link as break-glass."""
    from . import oidc
    from .client_ip import client_ip
    settings, store = auth.settings, auth.store
    # The break-glass link's wrong tries; an owner-link server keeps its own on the proxy.
    failures = Failures()

    def active():
        if isinstance(auth.proxy, OwnerLink):
            return auth.proxy
        if isinstance(auth.proxy, oidc.Oidc) and break_glass(settings):
            return auth.proxy
        raise Problem("not_found", "Not found", 404)

    @app.get(LINK_PATH)
    def owner_link_page(request: Request, signed_out: str = "", next: str = ""):
        active()
        if set(request.query_params) - {"next", "signed_out"}:
            # The secret goes after #, never in the query, where logs in front of the server would keep it. The app's
            # own `next` (ui/app/session.js: where to come back to) and sign-out's marker are all a query may carry.
            return oidc._page(settings, 400, "This sign-in link is not valid", ASK)
        if signed_out:
            return oidc._page(settings, 200, "Signed out", "You are signed out. " + ASK)
        back = oidc.safe_next(next)
        form = ('<form method="post" action="' + LINK_PATH + '"><input type="hidden" id="owner-link-token" name="token">'
                + ('<input type="hidden" name="next" value="' + html.escape(back) + '">' if back != "/" else "") +
                '<button class="b" id="owner-link-button" type="submit" disabled>Sign in</button></form>'
                '<script src="' + SCRIPT_PATH + '"></script>')
        page = oidc._page(settings, 200, "Sign in to " + settings.app_name,
                          "Open the sign-in link Tico sent you, then sign in as " + html.escape(settings.owner_email)
                          + ". The link works once; if it was used or is a week old, " + ASK[0].lower() + ASK[1:] + form)
        page.headers["Cache-Control"] = "no-store"
        # same-origin, not no-referrer: under no-referrer a browser sends the press with `Origin: null`, which the
        # cross-site check below refuses. A referrer never carries the fragment, and this page has no secret in its query.
        page.headers["Referrer-Policy"] = "same-origin"
        return page

    @app.get(SCRIPT_PATH)
    def owner_link_script():
        active()
        return Response(SCRIPT, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.post(LINK_PATH)
    def owner_link_sign_in(request: Request, token: str = Form("", max_length=128), next: str = Form("", max_length=2048)):
        proxy = active()
        # The app's cross-site check is for signed-in writes; this press comes before any sign-in, so it checks itself.
        origin = request.headers.get("origin")
        if origin and not settings.allows_origin(origin):
            raise Problem("origin", "Cross-site writes are not allowed", 403)
        ip = client_ip(request, settings)
        tries = getattr(proxy, "failures", failures)
        if tries.blocked(ip):
            return oidc._page(settings, 429, "Too many tries", "Wait a few minutes, or " + ASK[0].lower() + ASK[1:])
        with store.transaction() as c:
            human = c.execute("SELECT id FROM humans WHERE lower(email)=?", (settings.owner_email,)).fetchone()
            ok = bool(human) and spend(c, token)
            sid = proxy.start_session(c, human["id"], settings.owner_email) if ok else ""
        if not ok:
            tries.note(ip)
            return oidc._page(settings, 410, "This sign-in link has been used or has expired", ASK)
        # Back where the app was, a same-origin path only (oidc.safe_next: never another site, never //, \\ or a scheme).
        response = RedirectResponse(oidc.safe_next(next), status_code=303)
        proxy.set_cookie(response, proxy.cookie, sid, settings.session_absolute_seconds)
        response.headers["Cache-Control"] = "no-store"
        return response


def main(argv=None):
    """`replace DATABASE HASH [--sign-out]`, run in the server container by the installer (python3 -m setup owner-link).
    Offline like `backend.manage`: `docker compose exec` has no entrypoint, so the database is named, not read from
    the server's settings."""
    from pathlib import Path
    p = argparse.ArgumentParser(prog="python -m backend.owner_link")
    p.add_argument("command", choices=["replace"])
    p.add_argument("database", type=Path)
    p.add_argument("hash")
    p.add_argument("--sign-out", action="store_true", help="also end every session of the owner")
    args = p.parse_args(argv)
    if not HASH_RE.fullmatch(args.hash):
        print("The hash must be 64 lowercase hex characters.", file=sys.stderr)
        return 2
    import os
    kind = os.environ.get("TICO_AUTH_PROXY", "").strip().lower()
    if kind != KIND and not (kind == "oidc" and os.environ.get("TICO_HOSTED", "").strip() in HOSTED):
        print("This server does not use the owner sign-in link (TICO_AUTH_PROXY=owner-link, or oidc on a hosted install).",
              file=sys.stderr)
        return 2
    from .auth import Auth
    from .config import Settings
    from .manage import registry_dir
    from .store import Store
    store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
    store.initialize()
    auth = Auth(store)
    with store.transaction() as c:
        auth.sync_access(c)   # the owner stored in the database, as the server signs them in
        place(c, args.hash, auth.owner_email, sign_out=args.sign_out)
    print("The new owner sign-in link is in place; older unused links no longer work"
          + (", and the owner is signed out everywhere." if args.sign_out else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
