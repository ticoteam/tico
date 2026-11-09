from types import SimpleNamespace

import pytest

from backend.tests.helpers.auth import signing

LOCAL_TOKEN = "local-owner-bearer-token-32chars!!"


def test_machine_path_accepts_only_verified_human_cookie(api, signing):
    token = signing()
    result = api.get("/api/v2/me", headers={"Cookie": "CF_Authorization=" + token})
    assert result.status_code == 200 and result.json()["actor"] == "human:ben"
    assert api.get("/api/v2/me", headers={"Cookie": "CF_Authorization=forged"}).status_code == 401


@pytest.mark.parametrize("changes", [{"aud": "wrong-app"}, {"iss": "https://attacker.example"}, {"exp": 1}])
def test_access_requires_expected_issuer_audience_and_freshness(api, signing, changes):
    assert api.get("/api/v2/me", headers={"Cf-Access-Jwt-Assertion": signing(**changes)}).status_code == 401


def test_valid_but_unrostered_email_is_not_admitted(api, signing):
    assert (
        api.get("/api/v2/me", headers={"Cf-Access-Jwt-Assertion": signing(email="unknown@acme.example")}).status_code
        == 403
    )


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


def test_bot_reads_task_leaves_an_autocommit_connection_as_it_found_it(api):
    """A notice's read check (hubdb.bot_reads_task) on a connection with no transaction open opens none that stays open."""
    from backend import auth as A
    from backend.store import H

    store = api.app.state.store
    with store.transaction() as c:
        task = H.task_create(c, "human:ana", "Check the vendor list", "", "bot:ops", lint=False, private=False)
    with store.read() as c:
        assert not c.in_transaction
        assert A.bot_reads_task(c, "bot:ops", H.task(c, task["id"]))
        assert not c.in_transaction
        assert not A.bot_reads_task(c, "bot:ops", {**H.task(c, task["id"]), "id": "missing"})  # refused, too
        assert not c.in_transaction


# A domain join (Settings > People, "Anyone with a <domain> Google login"): the Google sign-in must be that domain's
# Workspace account, so its hd claim is the domain. The Tico account issuer keeps the verified-email rule alone.
def _issuer(issuer, domains=()):
    from backend import oidc as O

    oidc = O.Oidc.__new__(O.Oidc)
    oidc.issuer = issuer
    oidc.settings = SimpleNamespace(oidc_allowed_domains=tuple(domains))
    return oidc


GOOGLE = "https://accounts.google.com"
TICO_ACCOUNT = "https://auth.tico.example"


def test_a_domain_join_through_google_needs_the_company_hosted_domain():
    from backend.oidc import SignInError

    oidc = _issuer(GOOGLE)
    claims = {"email": "Ben@Acme.example", "email_verified": True, "hd": "acme.example"}
    assert oidc._email(claims, ["acme.example"]) == "ben@acme.example"
    with pytest.raises(SignInError):
        oidc._email({**claims, "hd": None}, ["acme.example"])  # a consumer account at the domain
    with pytest.raises(SignInError):
        oidc._email({**claims, "hd": "other.example"}, ["acme.example"])
    # Not a domain join: the address is not on a listed domain, so the hosted domain is not checked.
    assert (
        oidc._email({**claims, "email": "ben@partner.example", "hd": None}, ["acme.example"]) == "ben@partner.example"
    )
    assert oidc._email({**claims, "hd": None}) == "ben@acme.example"


def test_a_domain_join_through_the_tico_account_keeps_the_verified_email_rule():
    from backend.oidc import SignInError

    oidc = _issuer(TICO_ACCOUNT)
    claims = {"email": "ben@acme.example", "email_verified": True}
    assert oidc._email(claims, ["acme.example"]) == "ben@acme.example"
    with pytest.raises(SignInError):
        oidc._email({**claims, "email_verified": False}, ["acme.example"])
