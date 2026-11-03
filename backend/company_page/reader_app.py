"""The reader origin: the Company page's own address, a separate small app in front of the normal one.

ReaderGate is the outermost layer of the server. A request for the reader host goes to this file's Starlette app and can
reach nothing else: the normal route table isn't behind it, so no /api route can answer there whatever a route's own
checks would say, and authorization and app-cookie headers are gone before anything here runs. The app's own sign-in,
cookies and sessions don't exist on this host, and reader cookies mean nothing on the app's host, which never reads them.
With no reader origin set, ReaderGate isn't installed and none of these routes exist.

Sign-in is its own authorization-code flow with its own login state and callback. It reuses Oidc's verification (JWKS, ID
token, verified email) and nothing that touches the roster: a reader who works at the company is still just a reader.

Closed answers one plain 404 on every route here, the same as a path that never existed. Unsubscribing with a good link
is the one exception: that way out outlasts the page.
"""
import hashlib
import hmac
import html
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .. import hubdb as H
from .. import oidc as O
from ..store import Problem
from ..visuals import hostname, origin_of
from . import readers as R

log = logging.getLogger("tico.company_page.reader_app")

CALLBACK_PATH = "/auth/callback"
NOTICE_ENDPOINT = "tico_reader_notice_endpoint"
FORM_BYTES_MAX = 64 * 1024
FILE_BYTES_MAX = 16 * 1024 * 1024
BEFORE = re.compile(r"[0-9A-Za-z:._+-]{1,64}")
INVITE_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,100}")
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ANCHOR_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
FONT_DIR = Path(__file__).parent / "template" / "fonts"
ASSET_TYPES = {"css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8", "woff2": "font/woff2",
               "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf", "png": "image/png", "jpg": "image/jpeg",
               "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}      # no SVG: it can carry script

# Every path this host answers; the router in front of a hosted install lets through only these.
ROUTES = (("GET", "/"), ("GET", "/data.json"), ("GET", "/feed.xml"), ("GET", "/assets/{path:path}"), ("GET", "/sign-in"),
          ("GET", CALLBACK_PATH), ("POST", "/sign-out"), ("GET", "/invite/{token}"), ("GET", "/unsubscribe"),
          ("POST", "/unsubscribe"), ("POST", "/reply"), ("POST", "/can-help"))
# Gone from every request to this host: these prove who someone is to the app or to a proxy in front of it.
STRIPPED = (b"authorization", b"proxy-authorization", b"remote-user", b"x-forwarded-user", b"x-forwarded-email")
STRIPPED_PREFIXES = (b"cf-access-", b"x-amzn-oidc-", b"x-tico-")
SECURITY = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "frame-ancestors 'none'"}     # the page's own policy is a meta tag: render.py
PRIVATE = {**SECURITY, "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Vary": "Cookie"}
PUBLIC = {**SECURITY, "Cache-Control": "public, max-age=60", "Vary": "Cookie"}


def check_origin(settings):
    """The reader origin, normalized, or "" for none. Like a visual origin it must really be separate: a browser sends a
    host's cookies whatever the port, so on a hostname the app or a runner uses, this would sit next to their sessions.
    That stops the server. Something that isn't a usable https address counts as unset: private audiences stay off."""
    value = getattr(settings, "reader_origin", "")
    if not value:
        return ""
    try:
        parts = urlsplit(origin_of(value))
        _ = parts.port              # raises on a port that isn't a number
    except ValueError:
        parts = None
    if not parts or not parts.hostname or "@" in parts.netloc or not (
            parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in O.LOOPBACK)):
        log.warning("TICO_READER_ORIGIN is not an https origin; the Company page has no reader address until it is fixed")
        return ""
    origin = f"{parts.scheme}://{parts.netloc.lower()}"
    for other in (settings.public_url, getattr(settings, "visual_origin", ""), getattr(settings, "runner_url", "")):
        if other and hostname(other) == hostname(origin):
            raise ValueError("TICO_READER_ORIGIN must differ from the app's, visuals' and runners' hostnames, whatever "
                             "the scheme or port")
    return origin


def sign_in_configured(settings):
    """Whether a sign-in provider is set up. An install signing in some other way has none to send readers to."""
    try:
        O.check(settings)
    except RuntimeError:
        return False
    return True


def cookie_names(origin):
    prefix = "__Host-" if origin.startswith("https://") else ""
    return prefix + "tico_reader", prefix + "tico_reader_login"


class ReaderOidc(O.Oidc):
    """The login round trip, on the reader origin. No roster, no allowed domains (a reader comes from anywhere and has
    an entry on the reader list or nothing), no app sessions. Its signing key is its own, so a login state signed here is
    worth nothing on the app's callback, or the other way round."""
    name = "reader"
    sessions = False

    def __init__(self, settings, store):
        super().__init__(settings, store)
        origin = settings.reader_origin
        self.secure = origin.startswith("https://")
        self.cookie, self.login_cookie = cookie_names(origin)
        self.redirect_uri = origin + CALLBACK_PATH
        self.secret = hmac.new(self.secret, b"company-page-reader-login", hashlib.sha256).digest()

    def _email(self, claims):
        return self._verified_email(claims)

    def person(self, *args, **kwargs):
        raise RuntimeError("A reader is not a person on the roster")

    def start_session(self, *args, **kwargs):
        raise RuntimeError("A reader has no app session")

    def start(self, target, invite=""):
        """The provider URL, and the signed login state: an invite's token rides in it to the callback, where it is spent
        only on a verified sign-in, so a link scanner that opens the address spends nothing."""
        doc = self._discover()
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        params = {"response_type": "code", "client_id": self.settings.oidc_client_id, "redirect_uri": self.redirect_uri,
                  "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge_method": "S256",
                  "code_challenge": O._b64(hashlib.sha256(verifier.encode()).digest())}
        endpoint = doc["authorization_endpoint"]
        url = endpoint + ("&" if "?" in endpoint else "?") + urlencode(params)
        return url, self._sign({"s": state, "n": nonce, "v": verifier, "x": O.safe_next(target), "i": invite,
                                "e": time.time() + O.LOGIN_WINDOW})

    def complete(self, query, cookie):
        """(verified email, (issuer, subject), where to go, the invite's token). Raises SignInError. Waits on the
        provider, so no database is held open."""
        pending = self._unsign(cookie)
        email, target, _, account = self.finish(query, cookie)
        return email, account, target, str(pending.get("i") or "")

    def send_notice(self, emails, company, link, unsubscribe):
        """Ask the issuer to mail a link-only notice to up to 50 addresses: (HTTP status, answer), or None when it sends
        none, as a provider other than a Tico account doesn't. Only the issuer's own host gets this client's secret, as
        with `invite`. The body holds no text of ours: the issuer's mail says only that there is an update."""
        if R.rehearsing(self.settings):
            return None
        endpoint = self._endpoint(self._discover(), NOTICE_ENDPOINT)
        if endpoint is None:
            return None
        try:
            reply = httpx.post(endpoint, json={"to": emails, "company": " ".join(company.split())[:80], "url": link,
                                               "unsubscribe_url": unsubscribe},
                               auth=httpx.BasicAuth(self.settings.oidc_client_id, self.settings.oidc_client_secret),
                               timeout=30, follow_redirects=False)
            answer = reply.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise O.SignInError("notice failed: " + type(exc).__name__, 502) from exc
        return reply.status_code, answer if isinstance(answer, dict) else {}


def _private(response):
    response.headers.update(PRIVATE)
    return response


def not_found(*_):
    """The one answer for anything that isn't there, or that nobody here should learn is there."""
    return _private(Response("Not found", 404, media_type="text/plain; charset=utf-8"))


def _read_file(root, rel):
    """A file's bytes under `root`, or None: every path segment plain, no symlink on the way, inside the root."""
    try:
        base = Path(root).resolve(strict=True)
        path = base
        for segment in rel.split("/"):
            if not SEGMENT.fullmatch(segment):
                return None
            path = path / segment
            if path.is_symlink():
                return None
        if not path.is_file() or not path.resolve().is_relative_to(base) or path.stat().st_size > FILE_BYTES_MAX:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _release_file(settings, rel):
    """A file of the current public release. A swap can take the folder away mid-read; the next look finds the new one."""
    from . import release
    for _ in range(2):
        root = release.current_dir(settings)
        if not root:
            return None
        found = _read_file(root, rel)
        if found is not None:
            return found
    return None


def _anchor(value):
    return ANCHOR_UNSAFE.sub("-", str(value or "")).strip("-")


async def _form(request):
    """An urlencoded form of a sane size, as {name: first value}, or None. Read by hand: it never touches disk and needs
    no multipart parser, because nothing here takes more than a little text."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > FORM_BYTES_MAX:
            return None
    try:
        fields = parse_qs(body.decode(), keep_blank_values=True, max_num_fields=16)
    except ValueError:
        return None
    return {name: values[0] for name, values in fields.items()}


def build(store, settings, judge=lambda: None):
    """The app for the reader host. `judge` gives the screening model at request time (app.state.judge)."""
    session_cookie, login_cookie = cookie_names(settings.reader_origin)
    secure = settings.reader_origin.startswith("https://")
    oidc = ReaderOidc(settings, store) if sign_in_configured(settings) else None
    R._state["oidc"] = oidc

    def page(status, title, message, action=None):
        """`message` is HTML: only fixed text, or text already escaped."""
        return _private(O._page(settings, status, title, message, action))

    def no_access():
        return page(403, "No access", "This address isn't on the list. Ask your contact to add it.")

    def csrf_token(token):
        return R.signed(settings, b"csrf", token)

    def same_origin(request):
        """Whether a POST came from a page of this origin. Browsers send these; a tool without them still needs the form's
        token, which only this origin's page holds."""
        origin, site = request.headers.get("origin"), request.headers.get("sec-fetch-site")
        return (not origin or origin == settings.reader_origin) and (not site or site == "same-origin")

    def csrf_ok(request, form, token):
        return same_origin(request) and hmac.compare_digest(str(form.get("csrf", "")).encode(), csrf_token(token).encode())

    def set_session(response, token):
        response.set_cookie(session_cookie, token, max_age=R.SESSION_DAYS * 86400, path="/", secure=secure, httponly=True,
                            samesite="lax")

    def clear(response, *names):
        for name in names:
            response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="lax")
        return response

    def public_file(name, media_type):
        found = _release_file(settings, name)
        if found is None:
            return not_found()
        return Response(found, media_type=media_type, headers=PUBLIC)

    def look(request, before=None):
        """(transparency, the reader signed in or None, their view or None, its edition ids), all read once."""
        with store.read() as c:
            transparency = R.transparency_of(c)
            if transparency == "closed":
                return transparency, None, None, []
            reader = R.session_reader(c, request.cookies.get(session_cookie))
            view, ids = R.compose(c, reader["id"], H.now(), before) if reader else (None, [])
            return transparency, reader, view, ids

    def before_of(request):
        """The `before` of a page of older updates: None for none, False when it is nothing a page ever links to."""
        value = request.query_params.get("before")
        return value if value is None or BEFORE.fullmatch(value) else False

    def record(reader_id, ids):
        """Seen by. A page already rendered is still delivered if this write can't be made."""
        try:
            with store.read() as c:
                fresh = R.unseen(c, reader_id, ids)
            if fresh:
                with store.transaction() as c:
                    R.record_views(c, reader_id, fresh)
        except sqlite3.Error as exc:
            log.warning("Reader views weren't recorded: %s", type(exc).__name__)

    def home(request: Request):
        from . import render
        before = before_of(request)
        if before is False:
            return not_found()
        transparency, reader, view, ids = look(request, before)
        if transparency == "closed":
            return not_found()
        if view:
            token = request.cookies[session_cookie]
            text = render.page({**view, "actions": True, "viewer": {"email": reader["email"]}, "csrf": csrf_token(token)})
            record(reader["id"], ids)
            return _private(Response(text, media_type="text/html; charset=utf-8"))
        if transparency == "open":
            return public_file("index.html", "text/html; charset=utf-8")
        if reader:
            return no_access()
        if not oidc:
            return not_found()
        with store.read() as c:
            door = R.signed_out_view(c)
        return _private(Response(render.signed_out(door), media_type="text/html; charset=utf-8"))

    def data(request: Request):
        before = before_of(request)
        if before is False:
            return not_found()
        transparency, reader, view, ids = look(request, before)
        if view:
            record(reader["id"], ids)
            return _private(JSONResponse(view))
        return public_file("data.json", "application/json") if transparency == "open" else not_found()

    def feed(request: Request):
        with store.read() as c:
            transparency = R.transparency_of(c)
        return public_file("feed.xml", "application/atom+xml") if transparency == "open" else not_found()

    def private_logo(c, rel):
        """A company that isn't Open has no release to carry its logo, and the sign-in page shows it. Only the file the
        settings name now, read the way a release reads it."""
        from . import release
        page = c.execute("SELECT logo_file_id FROM company_page_settings WHERE id='company'").fetchone()
        url = release._logo_url(c, page["logo_file_id"]) if page else ""
        return release._logo_bytes(c, settings, {"company": {"logo_url": url}}) if url == "assets/" + rel else None

    def asset(request: Request):
        """The current release's assets, for a signed-in page as much as the public one. A release is empty unless the
        company is Open, so a private page's fonts come from the template, the same files for everyone."""
        rel = request.path_params["path"]
        media_type = ASSET_TYPES.get(rel.rsplit(".", 1)[-1].lower()) if "." in rel else None
        if not media_type:
            return not_found()
        with store.read() as c:
            transparency = R.transparency_of(c)
            if transparency == "closed":
                return not_found()
            found = _release_file(settings, "assets/" + rel)
            if found is None and rel.startswith("fonts/"):
                found = _read_file(FONT_DIR, rel.removeprefix("fonts/"))
            elif found is None and rel.startswith("logo-") and "/" not in rel:
                found = private_logo(c, rel)
        return not_found() if found is None else Response(found, media_type=media_type, headers=PUBLIC)

    def begin(target, invite=""):
        with store.read() as c:
            if not oidc or R.transparency_of(c) == "closed":
                return not_found()
        try:
            url, state = oidc.start(target, invite)
        except O.SignInError as exc:
            log.warning("Reader sign-in couldn't start: %s", exc.reason)
            return page(503, "Sign-in unavailable", "Try again in a minute.")
        response = _private(RedirectResponse(url, 303))
        response.set_cookie(login_cookie, state, max_age=O.LOGIN_WINDOW, path="/", secure=secure, httponly=True,
                            samesite="lax")
        return response

    def sign_in(request: Request):
        return begin(request.query_params.get("next") or "/")

    def invite(request: Request):
        token = request.path_params["token"]
        return begin("/", token) if INVITE_TOKEN.fullmatch(token) else not_found()

    def callback(request: Request):
        if not oidc:
            return not_found()
        with store.read() as c:
            if R.transparency_of(c) == "closed":
                return not_found()
        try:
            email, account, target, invited = oidc.complete(dict(request.query_params), request.cookies.get(login_cookie))
        except O.SignInError as exc:
            log.info("Reader sign-in refused: %s", exc.reason)
            return clear(page(403, "Sign-in didn't finish", "Try again from the link you were given."), login_cookie)
        issuer, sub = account
        try:
            with store.transaction() as c:
                if R.transparency_of(c) == "closed":
                    return not_found()
                reader = (R.redeem_invite(c, invited, issuer, sub, email) if invited
                          else R.resolve_login(c, issuer, sub, email))
                token = R.start_session(c, reader["id"], request.cookies.get(session_cookie, "")) if reader else None
        except R.InviteRefused as exc:
            log.info("Reader invite refused: %s", exc)
            return clear(page(403, "Link not valid", "This link has been used or has expired. Ask your contact for a new "
                                                     "one."), login_cookie)
        if not token:
            return clear(no_access(), login_cookie)
        response = _private(RedirectResponse(target, 303))
        set_session(response, token)
        return clear(response, login_cookie)

    def do_sign_out(request, form):
        token = request.cookies.get(session_cookie, "")
        with store.read() as c:
            reader = R.session_reader(c, token)
        if reader and not csrf_ok(request, form, token):
            return page(403, "Not signed out", "Reload the page and try again.")
        if reader:
            with store.transaction() as c:
                R.end_session(c, token)
        return clear(_private(RedirectResponse("/", 303)), session_cookie)

    async def sign_out(request: Request):
        form = await _form(request)
        return not_found() if form is None else await run_in_threadpool(do_sign_out, request, form)

    def do_message(request, form, kind):
        token = request.cookies.get(session_cookie, "")
        with store.read() as c:
            reader = None if R.transparency_of(c) == "closed" else R.session_reader(c, token)
            edition = R.readable_edition_id(c, reader["id"], form.get("anchor")) if reader else None
        if not reader:
            return not_found()
        if not csrf_ok(request, form, token):
            return page(403, "Not sent", "Reload the page and try again.")
        if not edition:
            return not_found()
        text = form.get("body", "")
        if kind == "can_help" and not text.strip():
            text = "I can help."        # the button alone: the ask it answers says what with
        try:
            R.submit_message(store, reader["id"], edition, kind, text, form.get("ask_id"), primary=judge())
        except Problem as exc:
            if exc.status == 404:
                return not_found()
            return page(exc.status, "Not sent", html.escape(exc.detail))
        return _private(RedirectResponse("/#" + _anchor(form.get("anchor")), 303))

    async def message(request, kind):
        form = await _form(request)
        return not_found() if form is None else await run_in_threadpool(do_message, request, form, kind)

    async def reply(request: Request):
        return await message(request, "reply")

    async def can_help(request: Request):
        return await message(request, "can_help")

    def do_unsubscribe(request, form):
        """The link is the credential: no sign-in, and it works with the page Closed. GET only asks, because mail scanners
        open links. One link went to many readers, so it asks whose address, and answers alike whether it was on the list."""
        token = (form or {}).get("token") or request.query_params.get("token")
        if not R.unsubscribe_valid(settings, token):
            return not_found()
        email = (form or {}).get("email", "").strip()
        if not email:
            return page(200, "Stop these emails", '<form method="post" action="/unsubscribe"><input type="hidden" '
                        f'name="token" value="{html.escape(token, quote=True)}"><p><label>Email <input type="email" '
                        'name="email" required></label></p><button class="b" type="submit">Stop emails</button></form>')
        with store.transaction() as c:
            R.unsubscribe_address(c, email)
        return page(200, "Emails stopped", "That address won't get emails about new updates.")

    async def unsubscribe(request: Request):
        form = await _form(request) if request.method == "POST" else None
        return await run_in_threadpool(do_unsubscribe, request, form)

    handlers = {404: not_found, 405: not_found}
    return Starlette(exception_handlers=handlers, routes=[
        Route("/", home, methods=["GET"]), Route("/data.json", data, methods=["GET"]),
        Route("/feed.xml", feed, methods=["GET"]), Route("/assets/{path:path}", asset, methods=["GET"]),
        Route("/sign-in", sign_in, methods=["GET"]), Route(CALLBACK_PATH, callback, methods=["GET"]),
        Route("/sign-out", sign_out, methods=["POST"]), Route("/invite/{token}", invite, methods=["GET"]),
        Route("/unsubscribe", unsubscribe, methods=["GET", "POST"]), Route("/reply", reply, methods=["POST"]),
        Route("/can-help", can_help, methods=["POST"])])


def _is_reader_host(raw, want):
    """Whether a Host header names the reader's hostname. Any port counts: the hostname is what cookies follow, and
    check_origin keeps it apart from the app's. Something unreadable is not the reader host."""
    try:
        return (urlsplit("//" + raw.decode("latin-1")).hostname or "").rstrip(".") == want
    except ValueError:
        return False


class ReaderGate:
    """Plain ASGI, so nothing of the normal app's stack (identity, sessions, CORS) runs on a reader request, and it holds
    no reference to the normal routes in the path a reader request takes."""

    def __init__(self, app, reader_app, origin):
        self.app, self.reader_app = app, reader_app
        self.host = hostname(origin)
        self.cookies = cookie_names(origin)

    def _clean(self, headers):
        """Identity headers gone, and of the cookies only this host's two."""
        kept = []
        for name, value in headers:
            lower = name.lower()
            if lower in STRIPPED or lower.startswith(STRIPPED_PREFIXES):
                continue
            if lower == b"cookie":
                pairs = [pair.strip() for pair in value.decode("latin-1").split(";")]
                value = "; ".join(p for p in pairs if p.split("=", 1)[0].strip() in self.cookies).encode("latin-1")
                if not value:
                    continue
            kept.append((name, value))
        return kept

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        hosts = [value for name, value in scope["headers"] if name.lower() == b"host"]
        matching = [raw for raw in hosts if _is_reader_host(raw, self.host)]
        if not matching:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        if len(hosts) > 1:                  # two Host headers: never decide which one a proxy believed
            return await not_found()(scope, receive, send)
        inner = {k: v for k, v in scope.items() if k not in ("app", "router", "route", "state", "endpoint", "path_params")}
        await self.reader_app({**inner, "headers": self._clean(scope["headers"])}, receive, send)


def install(app, store, auth, settings):
    """Put the gate in front of `app`. It goes on last, so it is outermost. No reader origin, no gate and no routes."""
    R._state.update(store=store, settings=settings, oidc=None, not_before=0.0)
    if not settings.reader_origin:
        return
    reader_app = build(store, settings, judge=lambda: getattr(app.state, "judge", None))
    app.add_middleware(ReaderGate, reader_app=reader_app, origin=settings.reader_origin)
