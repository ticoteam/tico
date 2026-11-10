"""Helpers that other tests share, moved out of test_google_connect.py so they import no test file."""

import io
import json
import urllib.error
import urllib.parse
import pytest
from backend import google_connect
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import headers, post
from connectors.mail import oauth


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
            self.answer or {"access_token": "ya29.fresh", "expires_in": 3599, "scope": oauth.G + "gmail.readonly"}
        )


def claimed_connect(app, machine):
    _, query = start(app)
    back(app, state=query["state"], code="c-1")
    return post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])["connect"]


def exchanged(app, machine, refresh_token="1//grant", status="connected", kind="", scope=None):
    """A connect claimed, exchanged at the server for a grant with `refresh_token` (granted `scope`, when given), and
    answered by `machine`."""
    claimed = claimed_connect(app, machine)
    answer = {"access_token": "ya29.first", "expires_in": 3599, "refresh_token": refresh_token}
    if scope is not None:
        answer["scope"] = scope
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(google_connect, "post_token", FakeGoogle(answer))
        post(app, f"connectors/mail/google/{claimed['id']}/exchange", {}, machine["token"])
    post(
        app,
        f"connectors/mail/google/{claimed['id']}/result",
        {"status": status, "can": ["read"]} if status == "connected" else {"status": status, "kind": kind},
        machine["token"],
    )


def refresh(app, token, **body):
    body = {
        "mailbox": "cara@acme.example",
        "service": "gmail",
        "refresh_token": "1//grant",
        "scopes": [oauth.G + "gmail.readonly"],
        **body,
    }
    return app.post("/api/v2/connectors/mail/google/token", json=body, headers=headers(token))
