import time
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from backend.identity_proxy import CloudflareAccess
from backend.tests.test_api import api

LOCAL_TOKEN = "local-owner-bearer-token-32chars!!"


_KEY = []


@pytest.fixture
def signing(api):
    if not _KEY:                                    # RSA key generation is slow; one key serves the module
        _KEY.append(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    key = _KEY[0]
    auth = api.app.state.auth
    auth.settings.access_issuer = "https://test.cloudflareaccess.com"
    auth.settings.access_audience = "expected-app-audience"
    jwks = Mock()
    jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=key.public_key())
    auth.proxy = CloudflareAccess(auth.settings, jwks=jwks)
    def token(**changes):
        claims = {"iss": auth.settings.access_issuer, "aud": auth.settings.access_audience,
                  "sub": "person-id", "email": "ben@acme.example", "iat": int(time.time()), "exp": int(time.time()) + 600}
        return jwt.encode({**claims, **changes}, key, algorithm="RS256")
    return token


def test_machine_path_accepts_only_verified_human_cookie(api, signing):
    token = signing()
    result = api.get("/api/v2/me", headers={"Cookie": "CF_Authorization=" + token})
    assert result.status_code == 200 and result.json()["actor"] == "human:ben"
    assert api.get("/api/v2/me", headers={"Cookie": "CF_Authorization=forged"}).status_code == 401


@pytest.mark.parametrize("changes", [{"aud": "wrong-app"}, {"iss": "https://attacker.example"}, {"exp": 1}])
def test_access_requires_expected_issuer_audience_and_freshness(api, signing, changes):
    assert api.get("/api/v2/me", headers={"Cf-Access-Jwt-Assertion": signing(**changes)}).status_code == 401


def test_valid_but_unrostered_email_is_not_admitted(api, signing):
    assert api.get("/api/v2/me", headers={"Cf-Access-Jwt-Assertion": signing(email="unknown@acme.example")}).status_code == 403


def test_local_owner_bearer_rejected_when_token_wrong(api):
    auth = api.app.state.auth
    auth.settings.access_issuer = ""
    auth.settings.local_owner_email = "ben@acme.example"
    auth.settings.local_owner_token = LOCAL_TOKEN
    assert api.get("/api/v2/me", headers={"Authorization": "Bearer " + ("y" * len(LOCAL_TOKEN))}).status_code == 401


# A bot's own question to a person, and the wall around it. A routine's turn is granted its own
# task and nothing else, which left every routine-only bot unable to ask anybody anything: the
# monitors fell back to filing tasks on Ben instead. (2026-09-22.)
def _row(kind="ask", participants=("bot:coo", "human:ben"), task_id=None):
    return {"kind": kind, "participants": list(participants), "task_id": task_id}


def _turn(actor="bot:coo"):
    return SimpleNamespace(actor=actor, role="bot", agent=None, attempt_id="att-1")


def test_chat_history_stays_scoped_to_the_run():
    # The reason the wall exists: a task run must not read a person's chat with the bot.
    from backend.auth import _own_ask_to_a_person
    assert _own_ask_to_a_person(_turn(), _row(kind="chat")) is False


def test_a_task_conversation_is_not_let_through_this_door():
    from backend.auth import _own_ask_to_a_person
    assert _own_ask_to_a_person(_turn(), _row(task_id="t-1")) is False


def test_a_bot_cannot_reach_an_ask_it_is_not_in():
    from backend.auth import _own_ask_to_a_person
    assert _own_ask_to_a_person(_turn("bot:cpo"), _row()) is False
