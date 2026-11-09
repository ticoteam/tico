"""Helpers that other tests share, moved out of test_google_workspace.py so they import no test file."""

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from backend import routines
from backend import google_workspace as W
from backend.store import H
from backend.tests.helpers.api import headers


SA = "tico@acme-project.acme.example"


MAILBOX = "inbox@acme.example"


GMAIL = "https://www.googleapis.com/auth/gmail.modify"


CALENDAR = "https://www.googleapis.com/auth/calendar"


KEY = "/api/v2/google/workspace/key"


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


@pytest.fixture
def private(keys):
    return keys[0]


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Each test starts with no wrong codes, no lockout, no cached tokens and no audit throttle."""
    monkeypatch.setattr(W, "_BAD", [])
    monkeypatch.setattr(W, "_AUDITED", {})
    monkeypatch.setitem(W._LOCKOUT, "until", 0.0)
    W.forget_tokens()


@pytest.fixture
def google(monkeypatch):
    """Google's answers: the token endpoint (`state["token"]`), the API probes (`state["probe"]`), and every call made.
    `state["authorized"]` is what the admin authorized in Google Admin: a token asking for any other scope is refused
    with unauthorized_client, as Google's domain-wide delegation does. None authorizes everything."""
    state = {"token": (200, {"access_token": "tok-1", "expires_in": 3600}), "probe": 200, "authorized": None}
    calls = []

    def fake(url, *, data=None, token=None):
        calls.append((url, data, token))
        if url == W.TOKEN_URI:
            asked = set(jwt.decode(data["assertion"], options={"verify_signature": False})["scope"].split())
            if state["authorized"] is not None and not asked <= set(state["authorized"]):
                return 401, {"error": "unauthorized_client"}
            return state["token"]
        return state["probe"], {}

    monkeypatch.setattr(W, "google_call", fake)
    return state, calls


@pytest.fixture
def covered(monkeypatch):
    """Sets the message bot mailboxes the roster would list: (address, bot) pairs."""

    def set_boxes(*pairs):
        monkeypatch.setattr(
            routines,
            "message_bot_mailboxes",
            lambda c, roster=None: [
                {"address": address, "person_id": address.split("@")[0], "bot": bot, "declared": True}
                for address, bot in pairs
            ],
        )

    return set_boxes


def key_json(private, key_id="key-1", **changes):
    body = {
        "type": "service_account",
        "project_id": "acme-project",
        "private_key_id": key_id,
        "private_key": private,
        "client_email": SA,
        "client_id": "1000001",
        "token_uri": W.TOKEN_URI,
    }
    body.update(changes)
    return body


def new_code(api, domain="acme.example", token="ana-test"):
    r = api.post("/api/v2/google/workspace/setup-code", json={"domain": domain}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["code"]


def upload(api, code, key, expected=200):
    """A setup command's upload: the key goes in the body as {"key": ...}, as scripts/google-setup.sh sends it."""
    r = api.post(KEY, json={"key": key}, headers={"Authorization": "Setup " + code})
    assert r.status_code == expected, r.text
    return r.json()


def google_view(api, token="ana-test"):
    r = api.get("/api/v2/google", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def check(api, body=None, token="ana-test", expected=200):
    r = api.post("/api/v2/google/workspace/check", json=body or {}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def rows(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM google_workspaces ORDER BY created_at, id")]


def connected(api, private, key_id="key-1", mailbox=MAILBOX):
    """A key uploaded and checked through to connected. The message bot mailboxes must be set first (`covered`)."""
    upload(api, new_code(api), key_json(private, key_id))
    answer = check(api, {"mailbox": mailbox})
    assert answer["ok"] is True, answer
    return answer


def personal(api, person, mailbox, status="connected", holder="r1", grant="g1", client=None):
    """A Connect Google grant as google_connect keeps it: a holder computer and a grant fingerprint make it live."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO google_connects(person_id,mailbox,status,holder,grant_hash,client_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (person, mailbox, status, holder, grant, client, H.now(), H.now()),
        )
