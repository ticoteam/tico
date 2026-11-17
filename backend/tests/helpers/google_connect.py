"""Helpers that other tests share, moved out of test_google_connect.py so they import no test file."""

import base64
import io
import json
import urllib.error
import urllib.parse
import pytest
from backend import google_connect, google_tokens
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import headers, post


CLIENT = "1234-tico.apps.googleusercontent.com"


@pytest.fixture
def app(api):
    settings = api.app.state.store.settings
    settings.google_oauth_client_id, settings.google_oauth_client_secret = CLIENT, "tico-secret"
    with api.app.state.store.transaction() as c:  # a signed-in agent, as a Hermes bot is
        c.execute(
            "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('inbox','hermes','x',?,"
            "'human:ana')",
            (H.now(),),
        )
    settings.test_identities["inbox-test"] = Identity("bot:inbox", "bot", agent="hermes")
    return api


@pytest.fixture
def own_app(app, monkeypatch):
    """The company's own Google app, set in Tools, signs people in: the environment's client is cleared so
    that it is the one a connect uses now (oauth_client prefers the environment)."""
    settings = app.app.state.store.settings
    monkeypatch.setattr(settings, "google_oauth_client_id", None)
    monkeypatch.setattr(settings, "google_oauth_client_secret", None)
    r = app.put(
        "/api/v2/google/oauth-client",
        json={"client_id": "own-client", "client_secret": "own-secret"},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    return app


def start(api, token="cara-test"):
    answer = post(api, "mail/google/connect", {}, token)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(answer["auth_url"]).query)
    return answer, {k: v[0] for k, v in query.items()}


def back(api, token="cara-test", **query):
    r = api.get(
        "/api/v2/mail/google/callback?" + urllib.parse.urlencode(query), headers=headers(token), follow_redirects=False
    )
    assert r.status_code == 302, r.text
    return r.headers["location"]


class FakeGoogle:
    """Google's OAuth endpoints as google_connect.post_token sees them: `calls` and `uris`, in the order asked."""

    def __init__(self, answer=None, error=None, status=400):
        self.calls, self.uris, self.answer, self.error, self.status = [], [], answer, error, status

    def __call__(self, fields, uri=google_connect.TOKEN_URI):
        self.calls.append(dict(fields))
        self.uris.append(uri)
        if isinstance(self.error, OSError) and not hasattr(self.error, "code"):
            raise self.error
        if self.error:
            raise urllib.error.HTTPError(
                uri, self.status, "Refused", {}, io.BytesIO(json.dumps({"error": self.error}).encode())
            )
        return dict(
            self.answer
            or {"access_token": "ya29.fresh", "expires_in": 3599, "scope": google_connect.G + "gmail.readonly"}
        )


def id_token(email="cara@acme.example", verified=True):
    """An ID token as Google's token endpoint returns it: only its claims are read (google_connect.signed_in_as)."""

    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return part({"alg": "RS256"}) + "." + part({"email": email, "email_verified": verified}) + ".sig"


def grant_answer(refresh_token="1//grant", scope=None, email="cara@acme.example", access_token="ya29.first"):
    """Google's answer to the code exchange: the grant, with the ID token of the account that signed in."""
    answer = {"access_token": access_token, "expires_in": 3599, "id_token": id_token(email)}
    if refresh_token:
        answer["refresh_token"] = refresh_token
    if scope is not None:
        answer["scope"] = scope
    return answer


def signed_in(api, token="cara-test", google=None, code="c-1", **answer):
    """The person starts Connect Google and Google sends them back with a code, which the server trades with `google`
    (a FakeGoogle answering `grant_answer(**answer)` when not given). Returns the FakeGoogle."""
    google = google or FakeGoogle(grant_answer(**answer))
    _, query = start(api, token)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(google_connect, "post_token", google)
        back(api, token, state=query["state"], code=code)
    return google


def door(api, mailbox="cara@acme.example", service="gmail", scopes="read"):
    """The token door's answer for `mailbox` ({access_token, expiry, scopes}), as the server's own callers get it."""
    with api.app.state.store.read() as c:
        return google_tokens.access(c, mailbox, service, scopes)
