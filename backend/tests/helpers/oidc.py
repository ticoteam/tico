"""Helpers that other tests share, moved out of test_oidc.py so they import no test file."""

import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from backend import identity_proxy


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
        # It mails an install's unread-threads digest (backend/oidc.py `digest`): a list of (status, answer) to answer.
        self.digests, self.digest_calls = None, []
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
                    self.reply(
                        {
                            "issuer": provider.issuer,
                            "authorization_endpoint": provider.issuer + "/authorize",
                            "token_endpoint": provider.issuer + "/token",
                            "jwks_uri": provider.issuer + "/keys",
                            "id_token_signing_alg_values_supported": ["RS256"],
                            **(
                                {"tico_invite_endpoint": provider.invite_endpoint or provider.issuer + "/oauth2/invite"}
                                if provider.invites is not None
                                else {}
                            ),
                            **(
                                {"tico_accounts_endpoint": provider.issuer + "/oauth2/accounts"}
                                if provider.accounts is not None
                                else {}
                            ),
                            **(
                                {"tico_people_endpoint": provider.issuer + "/oauth2/people"}
                                if provider.people is not None
                                else {}
                            ),
                            **(
                                {"tico_digest_endpoint": provider.issuer + "/oauth2/digest"}
                                if provider.digests is not None
                                else {}
                            ),
                        }
                    )
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
                if urlparse(self.path).path.endswith("/oauth2/digest"):
                    provider.digest_calls.append(one)
                    status, answer = provider.digests.pop(0)
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
        self.keys = [("key-%d" % self.generation, pool_key(self.generation))]  # the old key is retired

    @staticmethod
    def jwk(kid, key):
        numbers = key.public_key().public_numbers()
        size = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")
        return {
            "kty": "RSA",
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
            "n": _b64(size(numbers.n)),
            "e": _b64(size(numbers.e)),
        }

    def approve(self, authorize_url, **claims):
        """The person consents: a code that will exchange for an ID token with these claims."""
        query = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        code = "code-%d" % len(self.codes)
        self.codes[code] = {"query": query, "claims": claims}
        return code, query["state"]

    def token(self, form):
        entry = self.codes.pop(form.get("code"), None)
        if (
            not entry
            or form.get("client_id") != CLIENT
            or form.get("client_secret") != SECRET
            or form.get("redirect_uri") != entry["query"]["redirect_uri"]
            or _b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest()) != entry["query"]["code_challenge"]
        ):
            return {"error": "invalid_grant"}, 400
        now = int(time.time())
        email = entry["claims"].get("email", "ben@acme.example")
        # One account per address, as a real issuer has: a sign-in on a new address is a new account unless a test says so.
        claims = {
            "iss": self.issuer,
            "aud": CLIENT,
            "sub": "acct-" + email,
            "iat": now,
            "exp": now + 300,
            "nonce": entry["query"]["nonce"],
            "email": email,
            "email_verified": True,
        }
        claims.update(entry["claims"])
        claims = {k: v for k, v in claims.items() if v is not None}
        kid, key = self.keys[0]
        return {
            "id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid}),
            "access_token": "opaque",
            "token_type": "Bearer",
        }, 200


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

    ns = SimpleNamespace(
        api=api,
        fake=fake,
        auth=auth,
        settings=settings,
        proxy=proxy,
        start=start,
        login=login,
        sessions=sessions,
        provider=provider,
    )
    return ns
