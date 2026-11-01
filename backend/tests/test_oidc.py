"""Built-in OIDC sign-in, end to end against a provider running in the test process."""

import base64
import hashlib
import html
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import access as Access
from backend import hubdb, identity_proxy, oidc, people_mirror
from backend.config import Settings
from backend.tests.test_api import api, post  # noqa: F401  (the api fixture)

CLIENT = "tico-client"
SECRET = "client-secret-value"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


_POOL = []


def pool_key(index):
    """Key generation dominates the run time, so keys are made once and shared."""
    while len(_POOL) <= index:
        _POOL.append(rsa.generate_private_key(65537, 2048))
    return _POOL[index]


class Provider:
    """Discovery, JWKS, authorization bookkeeping and a token endpoint, on 127.0.0.1."""

    def __init__(self, prefix=""):
        self.prefix, self.codes, self.jwks_fetches, self.token_calls = prefix, {}, 0, []
        # A Tico account issuer also sends invites (backend/invites.py): set to a list of (status, answer) to answer.
        self.invites, self.invite_calls = None, []
        # Where discovery says invites go (default: this issuer's /oauth2/invite), and where an invite POST is redirected.
        self.invite_endpoint = self.invite_redirect = None
        # It may also make accounts with a generated password (POST /oauth2/accounts): a list of (status, answer).
        self.accounts, self.account_calls = None, []
        # It takes the sign-in list (backend/people_mirror.py): a list of (status, answer) to answer; None = not advertised.
        self.people, self.people_calls = None, []
        self.keys = []
        self.rotate()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body, status=200):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                path = urlparse(self.path).path
                if path.endswith("/.well-known/openid-configuration"):
                    self.reply({"issuer": provider.issuer, "authorization_endpoint": provider.issuer + "/authorize",
                                "token_endpoint": provider.issuer + "/token", "jwks_uri": provider.issuer + "/keys",
                                "id_token_signing_alg_values_supported": ["RS256"],
                                **({"tico_invite_endpoint": provider.invite_endpoint or provider.issuer + "/oauth2/invite"}
                                   if provider.invites is not None else {}),
                                **({"tico_accounts_endpoint": provider.issuer + "/oauth2/accounts"}
                                   if provider.accounts is not None else {}),
                                **({"tico_people_endpoint": provider.issuer + "/oauth2/people"}
                                   if provider.people is not None else {})})
                elif path.endswith("/keys"):
                    provider.jwks_fetches += 1
                    self.reply({"keys": [provider.jwk(kid, key) for kid, key in provider.keys]})
                else:
                    self.reply({}, 404)

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                one = {k: v[0] for k, v in form.items()}
                if urlparse(self.path).path.endswith("/oauth2/invite"):
                    provider.invite_calls.append(one)
                    if provider.invite_redirect:
                        self.send_response(307)
                        self.send_header("Location", provider.invite_redirect)
                        self.send_header("Content-Length", "0")
                        return self.end_headers()
                    status, answer = provider.invites.pop(0)
                    return self.reply(answer, status)
                if urlparse(self.path).path.endswith("/oauth2/people"):
                    provider.people_calls.append(one)
                    status, answer = provider.people.pop(0)
                    return self.reply(answer, status)
                if urlparse(self.path).path.endswith("/oauth2/accounts"):
                    provider.account_calls.append(one)
                    status, answer = provider.accounts.pop(0)
                    return self.reply(answer, status)
                provider.token_calls.append(one)
                self.reply(*provider.token(one))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = "http://127.0.0.1:%d%s" % (self.server.server_port, prefix)
        threading.Thread(target=lambda: self.server.serve_forever(0.02), daemon=True).start()

    def rotate(self):
        self.generation = getattr(self, "generation", -1) + 1
        self.keys = [("key-%d" % self.generation, pool_key(self.generation))]      # the old key is retired

    @staticmethod
    def jwk(kid, key):
        numbers = key.public_key().public_numbers()
        size = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")
        return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
                "n": _b64(size(numbers.n)), "e": _b64(size(numbers.e))}

    def approve(self, authorize_url, **claims):
        """The person consents: a code that will exchange for an ID token with these claims."""
        query = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        code = "code-%d" % len(self.codes)
        self.codes[code] = {"query": query, "claims": claims}
        return code, query["state"]

    def token(self, form):
        entry = self.codes.pop(form.get("code"), None)
        if (not entry or form.get("client_id") != CLIENT or form.get("client_secret") != SECRET
                or form.get("redirect_uri") != entry["query"]["redirect_uri"]
                or _b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
                != entry["query"]["code_challenge"]):
            return {"error": "invalid_grant"}, 400
        now = int(time.time())
        email = entry["claims"].get("email", "ben@acme.example")
        # One account per address, as a real issuer has: a sign-in on a new address is a new account unless a test says so.
        claims = {"iss": self.issuer, "aud": CLIENT, "sub": "acct-" + email, "iat": now, "exp": now + 300,
                  "nonce": entry["query"]["nonce"], "email": email, "email_verified": True}
        claims.update(entry["claims"])
        claims = {k: v for k, v in claims.items() if v is not None}
        kid, key = self.keys[0]
        return {"id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid}),
                "access_token": "opaque", "token_type": "Bearer"}, 200


@pytest.fixture
def provider():
    made = []

    def make(prefix=""):
        made.append(Provider(prefix))
        return made[-1]
    yield make
    for item in made:
        item.server.shutdown()
        item.server.server_close()


@pytest.fixture
def signin(api, provider, monkeypatch):
    monkeypatch.delenv("TICO_DOMAIN_ALIASES", raising=False)
    fake = provider()
    auth = api.app.state.auth
    settings = auth.settings

    def start(issuer=None, **changes):
        settings.auth_proxy, settings.oidc_issuer = "oidc", issuer or fake.issuer
        settings.oidc_client_id, settings.oidc_client_secret = CLIENT, SECRET
        settings.session_secret = "s" * 40
        for key, value in changes.items():
            setattr(settings, key, value)
        api.base_url = settings.public_url
        auth.proxy = identity_proxy.build(settings, auth.store)
        return auth.proxy
    proxy = start()

    def login(target="/", follow=False, claims=None, client=None, **query):
        client = client or api
        began = client.get("/auth/login", params={"next": target, **query}, follow_redirects=False)
        assert began.status_code == 302, began.text
        code, state = ns.fake.approve(began.headers["location"], **(claims or {}))
        return client.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)

    def sessions():
        with auth.store.read() as c:
            return c.execute("SELECT * FROM oidc_sessions").fetchall()
    ns = SimpleNamespace(api=api, fake=fake, auth=auth, settings=settings, proxy=proxy, start=start,
                         login=login, sessions=sessions, provider=provider)
    return ns


def me(client, **kw):
    return client.get("/api/v2/me", **kw)


def test_full_flow_signs_in_the_roster_person(signin):
    began = signin.api.get("/auth/login", params={"next": "/tasks?x=1"}, follow_redirects=False)
    location = urlparse(began.headers["location"])
    assert began.status_code == 302 and location.path.endswith("/authorize")
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert query["response_type"] == "code" and query["client_id"] == CLIENT
    assert query["code_challenge_method"] == "S256" and query["scope"] == "openid email profile"
    assert query["redirect_uri"] == signin.settings.public_url + "/auth/callback"
    assert SECRET not in began.headers["location"]
    login_cookie = began.headers["set-cookie"]
    assert "HttpOnly" in login_cookie and "samesite=lax" in login_cookie.lower()

    code, state = signin.fake.approve(began.headers["location"])
    done = signin.api.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert done.status_code == 302 and done.headers["location"] == "/tasks?x=1"
    assert signin.fake.token_calls[0]["client_secret"] == SECRET
    cookie = next(v for v in done.headers.get_list("set-cookie") if v.startswith("tico_session="))
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    who = me(signin.api)
    assert who.status_code == 200 and who.json()["actor"] == "human:ben"
    assert signin.api.get("/api/me").json()["proxy_session"] is True
    value = signin.api.cookies.get("tico_session")
    assert value not in json.dumps([dict(r) for r in signin.sessions()])      # only a hash is stored


def test_cookie_is_host_prefixed_and_secure_on_https(signin):
    signin.start()
    signin.settings.public_url = "https://tico.example"
    proxy = signin.start()
    assert proxy.cookie == "__Host-tico_session"
    began = signin.api.get("/auth/login", follow_redirects=False)
    assert "__Host-tico_login=" in began.headers["set-cookie"] and "Secure" in began.headers["set-cookie"]
    assert "Domain" not in began.headers["set-cookie"]
    code, state = signin.fake.approve(began.headers["location"])
    done = signin.api.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)
    session = next(v for v in done.headers.get_list("set-cookie") if v.startswith("__Host-tico_session="))
    assert "Secure" in session and "Path=/" in session and "Domain" not in session


@pytest.mark.parametrize("aliases,label", [(None, ""), ("", "Tico account"), (" \t ", "")])
def test_no_alias_login_preserves_host_rewriting_proxies(signin, monkeypatch, aliases, label):
    public = "https://app.acme.example"
    if aliases is not None:
        monkeypatch.setenv("TICO_DOMAIN_ALIASES", aliases)
    signin.start(public_url=public, oidc_label=label)
    # The browser keeps its public HTTPS URL and cookie jar while a proxy replaces Host upstream.
    signin.api.headers["Host"] = "server:8765"
    assert me(signin.api).status_code == 401
    page = signin.api.get(oidc.SIGN_IN_PATH, params={"next": "/tasks?x=1"}, follow_redirects=False)
    if label:
        assert page.status_code == 200 and "Sign in with Tico account" in page.text
        button = re.search(r'<a class="b" href="([^"]+)"', page.text)
        assert button is not None
        login = html.unescape(button[1])
    else:
        assert page.status_code == 302
        login = page.headers["location"]
    assert login.startswith(oidc.LOGIN_PATH + "?")
    began = signin.api.get(login, follow_redirects=False)
    query = parse_qs(urlparse(began.headers["location"]).query)
    assert began.status_code == 302 and query["redirect_uri"] == [public + oidc.CALLBACK_PATH]
    pending = [cookie for cookie in signin.api.cookies.jar if cookie.name == "__Host-tico_login"]
    assert len(pending) == 1 and pending[0].domain == "app.acme.example" and not pending[0].domain_specified
    code, state = signin.fake.approve(began.headers["location"])
    done = signin.api.get(query["redirect_uri"][0], params={"code": code, "state": state}, follow_redirects=False)
    assert done.request.headers["host"] == "server:8765"
    assert "__Host-tico_login=" in done.request.headers["cookie"]
    assert done.status_code == 302 and done.headers["location"] == "/tasks?x=1"
    assert signin.fake.token_calls[0]["redirect_uri"] == public + oidc.CALLBACK_PATH
    assert me(signin.api).status_code == 200 and signin.api.get("/api/me").json()["proxy_session"] is True
    session = [cookie for cookie in signin.api.cookies.jar if cookie.name == "__Host-tico_session"]
    assert len(session) == 1 and session[0].domain == "app.acme.example" and not session[0].domain_specified


@pytest.mark.parametrize("label", ["", "Tico account"])
def test_fresh_alias_login_reaches_canonical_before_creating_host_only_cookies(signin, label, monkeypatch):
    monkeypatch.setenv("TICO_DOMAIN_ALIASES", "old.acme.example")
    canonical, alias = "https://app.acme.example", "https://old.acme.example"
    signin.start(public_url=canonical, oidc_label=label)
    page = signin.api.get(alias + "/tasks?x=1", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 302
    entry = signin.api.get(urljoin(alias, page.headers["location"]), follow_redirects=False)
    assert entry.status_code == 302 and entry.headers["location"].startswith(canonical + signin.auth.proxy.sign_in_path)
    assert "set-cookie" not in entry.headers and entry.headers["cache-control"] == "no-store"
    assert signin.fake.codes == {} and signin.fake.token_calls == []
    began = signin.api.get(entry.headers["location"], follow_redirects=False)
    if label:
        assert began.status_code == 200 and "Sign in with Tico account" in began.text
        button = re.search(r'<a class="b" href="([^"]+)"', began.text)
        assert button is not None
        began = signin.api.get(urljoin(canonical, html.unescape(button[1])), follow_redirects=False)
    query = parse_qs(urlparse(began.headers["location"]).query)
    assert query["redirect_uri"] == [canonical + oidc.CALLBACK_PATH]
    assert "Domain" not in began.headers["set-cookie"]
    pending = [cookie for cookie in signin.api.cookies.jar if cookie.name == "__Host-tico_login"]
    assert len(pending) == 1 and pending[0].domain == "app.acme.example" and not pending[0].domain_specified
    code, state = signin.fake.approve(began.headers["location"])
    done = signin.api.get(query["redirect_uri"][0], params={"code": code, "state": state}, follow_redirects=False)
    assert done.status_code == 302 and done.headers["location"] == "/tasks?x=1"
    assert me(signin.api).status_code == 200
    assert signin.api.get(alias + "/api/v2/me").status_code == 401


def test_canonical_entry_uses_only_host_authority_and_preserves_real_ports(signin, monkeypatch):
    monkeypatch.setenv("TICO_DOMAIN_ALIASES", "old.acme.example")
    for public, hosts, aliases in [
        ("https://app.acme.example", ["app.acme.example", "APP.ACME.EXAMPLE:443"],
         ["old.acme.example", "app.acme.example.evil.example", "app.acme.example.", "app.acme.example:8443", "app.acme.example:0",
          "app.acme.example:", "app.acme.example?", "app.acme.example#"]),
        ("http://127.0.0.1:8765", ["127.0.0.1:8765"], ["127.0.0.1", "127.0.0.1:8766", "localhost:8765"]),
        ("http://[::1]:8765", ["[::1]:8765"], ["[::1]:8766", "[::1]:", "::1"]),
    ]:
        proxy = signin.start(public_url=public)
        calls = []

        def begin(*args):
            calls.append(args)
            return "https://identity.example/authorize", "synthetic-pending"

        monkeypatch.setattr(proxy, "begin", begin)
        for host in hosts:
            reply = signin.api.get("http://internal.example/auth/login", headers={
                "Host": host, "Forwarded": "host=evil.example;proto=http", "X-Forwarded-Host": "evil.example"},
                follow_redirects=False)
            assert reply.headers["location"] == "https://identity.example/authorize"
        assert len(calls) == len(hosts)
        for host in aliases:
            for path in (oidc.LOGIN_PATH, oidc.SIGN_IN_PATH):
                reply = signin.api.get("http://internal.example" + path, headers={
                    "Host": host, "Forwarded": "host=" + urlparse(public).netloc,
                    "X-Forwarded-Host": urlparse(public).netloc}, follow_redirects=False)
                assert reply.status_code == 302 and reply.headers["location"] == public + path
                assert "set-cookie" not in reply.headers
        for path in (oidc.LOGIN_PATH, oidc.SIGN_IN_PATH):
            duplicate = [("Host", urlparse(public).netloc), ("Host", "old.acme.example")]
            reply = signin.api.get(public + path, headers=duplicate, follow_redirects=False)
            assert reply.request.headers.get_list("host") == [value for _, value in duplicate]
            assert reply.status_code == 302 and reply.headers["location"] == public + path
            assert "set-cookie" not in reply.headers
        assert len(calls) == len(hosts)


def test_alias_entry_only_keeps_existing_valid_login_arguments(signin, monkeypatch):
    monkeypatch.setenv("TICO_DOMAIN_ALIASES", "old.acme.example")
    public, alias, frontend = "https://app.acme.example", "https://old.acme.example", "https://frontend.acme.example"
    proxy = signin.start(public_url=public, cors_origins=(frontend,))

    def no_begin(*args):
        raise AssertionError("An alias must not begin a provider login")

    monkeypatch.setattr(proxy, "begin", no_begin)
    reply = signin.api.get(alias + oidc.LOGIN_PATH, params={
        "next": "/tasks#/x", "fresh": "0", "code_challenge": "ignored-for-local", "unknown": "drop-me"},
        follow_redirects=False)
    assert parse_qs(urlparse(reply.headers["location"]).query) == {"next": ["/tasks#/x"], "fresh": ["1"]}
    assert "set-cookie" not in reply.headers
    for target in ("https://evil.example/x", "//evil.example/x", "/auth/login"):
        reply = signin.api.get(alias + oidc.LOGIN_PATH, params={"next": target}, follow_redirects=False)
        assert reply.headers["location"] == public + oidc.LOGIN_PATH and "set-cookie" not in reply.headers
    for target, challenge in [(frontend + "/", ""), (frontend + "/", "short"), ("https://evil.example/", "a" * 43)]:
        reply = signin.api.get(alias + oidc.LOGIN_PATH, params={"next": target, "code_challenge": challenge},
                               follow_redirects=False)
        assert reply.status_code == 400 and "location" not in reply.headers and "set-cookie" not in reply.headers
    reply = signin.api.get(alias + oidc.SIGN_IN_PATH, params={
        "next": "//evil.example/x", "fresh": "1", "code_challenge": "a" * 43, "unknown": "drop-me"},
        follow_redirects=False)
    assert reply.headers["location"] == public + oidc.SIGN_IN_PATH and "set-cookie" not in reply.headers


@pytest.mark.parametrize("claims", [
    {"aud": "someone-else"}, {"iss": "https://evil.example"}, {"nonce": "not-the-nonce"},
    {"email_verified": False}], ids=lambda claims: ",".join(claims))
def test_bad_id_tokens_never_start_a_session(signin, claims):
    result = signin.login(claims=claims)
    assert result.status_code in (400, 502) and result.headers["content-type"].startswith("text/html")
    assert "session" not in result.headers.get("set-cookie", "")
    assert signin.sessions() == [] and me(signin.api).status_code == 401


def test_wrong_signature_is_refused(signin):
    signin.login()                                           # the provider's real key is now cached
    signin.api.cookies.clear()
    began = signin.api.get("/auth/login", follow_redirects=False)
    code, state = signin.fake.approve(began.headers["location"])
    other = pool_key(1)
    signin.fake.keys[0] = (signin.fake.keys[0][0], other)          # same kid, another key
    result = signin.api.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert result.status_code == 400 and len(signin.sessions()) == 1 and me(signin.api).status_code == 401


def test_state_and_login_cookie_are_enforced(signin):
    began = signin.api.get("/auth/login", follow_redirects=False)
    code, state = signin.fake.approve(began.headers["location"])
    assert signin.api.get("/auth/callback", params={"code": code, "state": state + "x"},
                          follow_redirects=False).status_code == 400
    # The provider's code is spent by a good state only, so a fresh attempt is needed.
    began = signin.api.get("/auth/login", follow_redirects=False)
    code, state = signin.fake.approve(began.headers["location"])
    signin.api.cookies.clear()
    assert signin.api.get("/auth/callback", params={"code": code, "state": state},
                          follow_redirects=False).status_code == 400          # no cookie at all
    forged = began.headers["set-cookie"].split(";")[0]
    name, _, value = forged.partition("=")
    payload = json.loads(base64.urlsafe_b64decode(value.split(".")[0] + "=="))
    body = _b64(json.dumps({**payload, "s": "chosen"}).encode())
    assert signin.api.get("/auth/callback", params={"code": code, "state": "chosen"},
                          headers={"cookie": name + "=" + body + "." + value.split(".")[1]},
                          follow_redirects=False).status_code == 400          # signature no longer fits
    assert signin.sessions() == []
    assert signin.api.get("/auth/callback", params={"error": "access_denied"},
                          follow_redirects=False).status_code == 400


def test_disallowed_domain_and_hosted_domain(signin):
    signin.start(oidc_allowed_domains=("acme.example",))
    assert signin.login(claims={"email": "ben@other.example"}).status_code == 400
    assert signin.sessions() == []
    assert signin.login().status_code == 302
    signin.api.cookies.clear()
    # A Google issuer also needs the hd claim to be an allowed domain.
    original = oidc._google
    oidc._google = lambda issuer: True
    try:
        assert signin.login(claims={"hd": "other.example"}).status_code == 400
        assert signin.login(claims={"hd": "acme.example"}).status_code == 302
        assert signin.login(claims={"hd": None}).status_code == 400
    finally:
        oidc._google = original


def test_idle_and_absolute_lifetimes(signin):
    signin.login()
    assert me(signin.api).status_code == 200
    with signin.auth.store.transaction() as c:
        c.execute("UPDATE oidc_sessions SET last_seen_at=?", ("2000-01-01T00:00:00.000000Z",))
    assert me(signin.api).status_code == 401
    signin.login()
    assert me(signin.api).status_code == 200
    with signin.auth.store.transaction() as c:
        c.execute("UPDATE oidc_sessions SET expires_at=?", ("2000-01-01T00:00:00.000000Z",))
    assert me(signin.api).status_code == 401


@pytest.mark.parametrize("target,expected", [
    ("//evil.example/x", "/"), ("javascript:alert(1)", "/"), ("/tasks#/x", "/tasks#/x")])
def test_next_only_returns_to_same_origin_paths(signin, target, expected):
    done = signin.login(target)
    assert done.status_code == 302 and done.headers["location"] == expected


def test_logout_ends_the_session_and_lands_on_a_sign_in_page(signin):
    signin.login()
    value = signin.api.cookies.get("tico_session")
    out = signin.api.get("/api/v2/logout", follow_redirects=False)
    assert out.status_code == 302 and out.headers["location"] == "/auth/signed-out"
    assert signin.sessions() == [] and me(signin.api).status_code == 401
    replay = me(signin.api, headers={"cookie": "tico_session=" + value})
    assert replay.status_code == 401                        # the id itself is dead, not only the cookie
    page = signin.api.get("/auth/signed-out")
    assert page.status_code == 200 and "/auth/login" in page.text


def test_a_person_marked_left_loses_the_session_and_cannot_return(signin):
    signin.login(claims={"email": "cara@acme.example"})
    assert me(signin.api).json()["actor"] == "human:cara"
    cookie = signin.api.cookies.get("tico_session")
    post(signin.api, "people/cara", {"left": True})
    assert signin.sessions() == []
    assert me(signin.api, headers={"cookie": "tico_session=" + cookie}).status_code == 401
    again = signin.login(claims={"email": "cara@acme.example"})
    assert again.status_code == 403 and "not on" in again.text


def test_writes_from_another_origin_are_refused(signin):
    signin.login()
    url = signin.settings.public_url
    bad = signin.api.post("/api/v2/me", headers={"origin": "https://evil.example"})
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "origin"
    ok = signin.api.post("/api/v2/me", headers={"origin": url})
    assert ok.json().get("error", {}).get("code") != "origin"


def base(tmp_path, **kw):
    return dict(db_path=tmp_path / "hub.db", registry_dir=tmp_path, auth_proxy="oidc",
                oidc_issuer="https://accounts.google.com", oidc_client_id="id", oidc_client_secret="s",
                public_url="https://tico.example", **kw)


@pytest.mark.parametrize("changes,message", [
    ({"oidc_client_secret": ""}, "TICO_OIDC_CLIENT_SECRET"),
    ({"oidc_issuer": "https://login.microsoftonline.com/common/v2.0"}, "own tenant"),
    ({"public_url": "http://tico.example"}, "https TICO_PUBLIC_URL")])
def test_startup_validation_names_the_problem(tmp_path, changes, message):
    with pytest.raises(RuntimeError, match=message):
        Settings(**{**base(tmp_path), **changes})


def roster_email(auth, pid):
    with auth.store.read() as c:
        return c.execute("SELECT email FROM humans WHERE id=?", (pid,)).fetchone()["email"]


def test_the_account_is_remembered_on_first_sign_in(signin):
    signin.login(claims={"sub": "acct-ben"})
    with signin.auth.store.read() as c:
        row = c.execute("SELECT idp_issuer,idp_sub FROM humans WHERE id='ben'").fetchone()
    assert row["idp_sub"] == "acct-ben" and row["idp_issuer"] == signin.fake.issuer


def test_a_changed_address_signs_in_the_same_person_and_moves_the_email(signin):
    signin.login(claims={"sub": "acct-ben"})
    signin.api.cookies.clear()
    done = signin.login(claims={"sub": "acct-ben", "email": "ben.new@acme.example"})
    assert done.status_code == 302
    assert me(signin.api).json()["actor"] == "human:ben"
    assert roster_email(signin.auth, "ben") == "ben.new@acme.example"
    with signin.auth.store.read() as c:
        roster = next(p for p in Access._load_json(c, "people")["people"] if p["id"] == "ben")
    assert roster["email"] == "ben.new@acme.example"


def test_a_changed_address_already_held_by_someone_else_is_refused(signin):
    signin.login(claims={"sub": "acct-ben"})
    signin.api.cookies.clear()
    refused = signin.login(claims={"sub": "acct-ben", "email": "cara@acme.example"})
    assert refused.status_code == 403 and "not on" in refused.text
    assert roster_email(signin.auth, "ben") == "ben@acme.example"
    assert roster_email(signin.auth, "cara") == "cara@acme.example"


def test_a_left_person_stays_out_when_their_account_comes_back_on_a_new_address(signin):
    signin.login(claims={"sub": "acct-cara", "email": "cara@acme.example"})
    post(signin.api, "people/cara", {"left": True})
    signin.api.cookies.clear()
    again = signin.login(claims={"sub": "acct-cara", "email": "cara.new@acme.example"})
    assert again.status_code == 403
    assert roster_email(signin.auth, "cara") == "cara@acme.example"


def test_the_not_on_the_list_page_names_the_address_and_ends_the_old_session(signin):
    signin.login()
    assert len(signin.sessions()) == 1
    refused = signin.login(claims={"email": "dan@other.example", "sub": "acct-dan"})
    assert refused.status_code == 403
    assert "dan@other.example" in refused.text and "Use another account" in refused.text
    assert "/auth/login?prompt=select_account" in refused.text
    assert signin.sessions() == [] and signin.api.cookies.get("tico_session") is None


def _set_access(signin, **fields):
    with signin.auth.store.transaction() as c:
        Access._store(c, Access.ACCESS, {**Access._load_json(c, Access.ACCESS), **fields})


def _set_owner(signin, **fields):
    with signin.auth.store.transaction() as c:
        Access._store(c, Access.OWNER, {**Access._load_json(c, Access.OWNER), **fields})


def _humans_with(signin, email):
    with signin.auth.store.read() as c:
        return c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchall()


def test_an_admitted_domain_address_joins_once_and_the_second_sign_in_is_the_same_person(signin):
    _set_access(signin, allowed_domains=["acme.example"])
    joined = signin.login(claims={"email": "dan@acme.example", "sub": "acct-dan"})
    assert joined.status_code == 302
    assert me(signin.api).json()["actor"] == "human:dan"
    signin.api.cookies.clear()
    again = signin.login(claims={"email": "dan@acme.example", "sub": "acct-dan"})
    assert again.status_code == 302
    assert me(signin.api).json()["actor"] == "human:dan"
    assert len(_humans_with(signin, "dan@acme.example")) == 1
    with signin.auth.store.read() as c:
        row = c.execute("SELECT idp_issuer,idp_sub FROM humans WHERE id='dan'").fetchone()
    assert row["idp_sub"] == "acct-dan" and row["idp_issuer"] == signin.fake.issuer


def test_an_address_the_owner_does_not_admit_is_refused_and_not_added(signin):
    _set_access(signin, allowed_domains=["acme.example"])
    refused = signin.login(claims={"email": "dan@other.example", "sub": "acct-dan"})
    assert refused.status_code == 403 and "dan@other.example" in refused.text
    assert _humans_with(signin, "dan@other.example") == []
    assert signin.sessions() == []


def test_a_retired_address_never_joins_even_at_an_admitted_domain(signin):
    _set_access(signin, allowed_domains=["acme.example"])
    _set_owner(signin, retired=["old@acme.example"])
    refused = signin.login(claims={"email": "old@acme.example", "sub": "acct-old"})
    assert refused.status_code == 403
    assert _humans_with(signin, "old@acme.example") == []
    assert signin.sessions() == []


def test_a_public_mail_domain_is_never_admitted_by_default_with_a_gmail_owner(signin):
    # The owner signs in with gmail and nothing typed in Settings: no domain is allowed by default.
    _set_owner(signin, email="ana@gmail.com")
    with signin.auth.store.read() as c:
        access = Access.load_access(c, signin.settings)
        assert access["allowed_domains"] == [] and Access.home_domain("ana@gmail.com", []) == ""
        assert not Access.admitted(access, "pat@gmail.com")
    refused = signin.login(claims={"email": "pat@gmail.com", "sub": "acct-pat"})
    assert refused.status_code == 403
    assert _humans_with(signin, "pat@gmail.com") == [] and signin.sessions() == []


@pytest.mark.parametrize("prompt,sent", [("select_account", "select_account"), ("login", "login"), ("consent", None)])
def test_login_passes_only_the_two_prompts_to_the_provider(signin, prompt, sent):
    began = signin.api.get("/auth/login", params={"prompt": prompt}, follow_redirects=False)
    query = {k: v[0] for k, v in parse_qs(urlparse(began.headers["location"]).query).items()}
    assert query.get("prompt") == sent


def _roster_with_bot_and_sign_in_off(auth):
    with auth.store.transaction() as c:
        roster = Access._load_json(c, "people")
        people = [dict(p) for p in roster["people"]]
        for p in people:
            if p["id"] == "cara":
                p["sign_in"] = False
        people.append({"id": "coo-bot", "email": "coo@acme.example", "bot": "coo"})
        Access._save_roster(c, {**roster, "people": people})


def test_the_people_list_goes_to_the_account_with_roles_and_no_bots(signin):
    signin.fake.people = [(200, {"stored": 2})]
    _roster_with_bot_and_sign_in_off(signin.auth)
    reply = people_mirror.push_now(signin.auth)
    assert reply == (200, {"stored": 2})
    sent = json.loads(signin.fake.people_calls[0]["people"])
    assert sent == [{"email": "ana@acme.example", "role": "owner"}, {"email": "ben@acme.example", "role": "admin"}]
    assert signin.fake.people_calls[0]["client_secret"] == SECRET


def test_an_odd_address_is_left_out_so_it_cannot_block_the_list(signin):
    signin.fake.people = [(200, {"stored": 3})]
    with signin.auth.store.transaction() as c:
        roster = Access._load_json(c, "people")
        Access._save_roster(c, {**roster, "people": [*roster["people"], {"id": "odd", "email": "odd name@acme.example"}]})
    assert people_mirror.push_now(signin.auth) == (200, {"stored": 3})
    sent = [row["email"] for row in json.loads(signin.fake.people_calls[-1]["people"])]
    assert sent == ["ana@acme.example", "ben@acme.example", "cara@acme.example"]


def test_the_account_id_never_leaves_the_database_layer(signin):
    signin.login(claims={"sub": "acct-ben"})
    with signin.auth.store.read() as c:
        assert "idp_sub" in dict(c.execute("SELECT * FROM humans WHERE id='ben'").fetchone())
        assert "idp_sub" not in hubdb.human(c, "ben") and "idp_issuer" not in hubdb.human(c, "ben")
        assert all("idp_sub" not in row and "idp_issuer" not in row for row in hubdb.humans(c))


def test_the_people_list_is_sent_only_when_the_account_advertises_it(signin):
    assert people_mirror.push_now(signin.auth) is None
    assert signin.fake.people_calls == []


@pytest.mark.parametrize("where", ["admins", "allowed", "owner"])
def test_a_move_onto_an_admin_owner_or_allowed_address_is_refused(signin, where):
    signin.login(claims={"sub": "acct-cara"})
    signin.api.cookies.clear()
    if where == "owner":
        _set_owner(signin, email="boss@acme.example")
    else:
        with signin.auth.store.read() as c:
            current = Access.load_access(c, signin.settings)[where]
        _set_access(signin, **{where: [*current, "boss@acme.example"]})
    refused = signin.login(claims={"sub": "acct-cara", "email": "boss@acme.example"})
    assert refused.status_code == 403 and "not on" in refused.text
    assert roster_email(signin.auth, "cara") == "cara@acme.example"
    assert _humans_with(signin, "boss@acme.example") == []
    assert me(signin.api).status_code == 401


def _set_sign_in(signin, pid, on):
    with signin.auth.store.transaction() as c:
        roster = Access._load_json(c, "people")
        people = [{**p, "sign_in": on} if p["id"] == pid else p for p in roster["people"]]
        Access._save_roster(c, {**roster, "people": people})


def test_sign_in_off_stops_an_address_move_and_the_account_being_remembered(signin):
    signin.login(claims={"sub": "acct-cara", "email": "cara@acme.example"})
    _set_sign_in(signin, "cara", False)
    signin.api.cookies.clear()
    moved = signin.login(claims={"sub": "acct-cara", "email": "cara.new@acme.example"})
    assert moved.status_code == 403
    assert roster_email(signin.auth, "cara") == "cara@acme.example"
    assert me(signin.api).status_code == 401
    # A new account on the old address is not remembered either.
    other = signin.login(claims={"sub": "acct-cara-2", "email": "cara@acme.example"})
    assert other.status_code == 403
    with signin.auth.store.read() as c:
        row = c.execute("SELECT idp_sub FROM humans WHERE id='cara'").fetchone()
    assert row["idp_sub"] == "acct-cara"


@pytest.fixture
def mirror(monkeypatch):
    """The people mirror's state, reset for one test; any timer it armed is cancelled afterwards."""
    monkeypatch.setattr(people_mirror, "_auth", None)
    monkeypatch.setattr(people_mirror, "_timer", None)
    monkeypatch.setattr(people_mirror, "_next_allowed", 0.0)
    yield people_mirror
    if people_mirror._timer is not None:
        people_mirror._timer.cancel()


def test_a_429_with_an_html_body_waits_for_the_retry_after_header(signin, mirror, monkeypatch):
    signin.fake.people = []                       # the account advertises the endpoint (read before any sign-in)
    signin.login()
    monkeypatch.setattr(mirror, "_auth", signin.auth)
    limited = httpx.Response(429, headers={"Retry-After": "120"}, content=b"<html>Too many requests</html>")
    monkeypatch.setattr(oidc.httpx, "post", lambda *a, **k: limited)
    mirror.send()
    assert mirror._timer is not None and mirror._timer.interval == 120
    assert mirror._next_allowed > 0


def test_a_change_during_the_wait_does_not_send_early(signin, mirror, monkeypatch):
    signin.fake.people = []                       # the account advertises the endpoint (read before any sign-in)
    signin.login()
    monkeypatch.setattr(mirror, "_auth", signin.auth)
    monkeypatch.setattr(oidc.httpx, "post", lambda *a, **k: httpx.Response(429, json={"retry_after": 600}))
    mirror.send()
    sent = []
    monkeypatch.setattr(mirror, "push_now", lambda *a, **k: sent.append(1))
    mirror.changed()
    mirror.send()                                 # the daily loop, or a timer, during the wait
    assert sent == [] and mirror._timer.interval > mirror.DEBOUNCE_SECONDS * 3
    monkeypatch.setattr(mirror, "_next_allowed", 0.0)
    mirror.send()                                 # the wait is over: one send, with the newest list
    assert sent == [1]
