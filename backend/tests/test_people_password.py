"""Create password (backend/invites.py `make_password`): an owner or admin asks the Tico account issuer to make someone
on the roster an account with a generated password, shown once. Tico keeps no copy of it anywhere."""

import logging

import pytest

from backend.tests.test_api import api  # noqa: F401  (the api fixture)
from backend.tests.test_invites import owner
from backend.tests.test_oidc import CLIENT, SECRET, provider, signin  # noqa: F401  (the fixtures)

PASSWORD = "abcde-fghjk-mnpqr-stuvw"


def everything(signin):  # noqa: F811
    with signin.auth.store.read() as c:
        return "\n".join(c.iterdump())


def test_an_owner_makes_an_account_and_sees_its_password_once_and_tico_keeps_none_of_it(signin, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    signin.fake.accounts = [(201, {"status": "created", "email": "cara@acme.example", "password": PASSWORD})]
    headers = owner(signin)
    assert signin.api.get("/api/v2/access", headers=headers).json()["account_password"] is True
    made = signin.api.post("/api/v2/access/humans/cara/password", json={}, headers=headers)     # the page's path
    assert made.status_code == 200, made.text
    assert made.json() == {"person": "cara", "email": "cara@acme.example", "password": PASSWORD}
    assert made.headers["cache-control"].startswith("no-store")
    # Only the address goes, with the install's own client id and secret.
    assert signin.fake.account_calls == [{"client_id": CLIENT, "client_secret": SECRET, "email": "cara@acme.example"}]
    dump = everything(signin)
    assert PASSWORD not in dump and "person.account_created" in dump          # not stored, not in an event
    assert PASSWORD not in caplog.text
    # The same request again (a lost answer) never asks twice and never shows the password again.
    again = signin.api.post("/api/v2/access/humans/cara/password", json={}, headers=headers)
    assert again.status_code == 422 and again.json()["error"]["code"] == "password_shown", again.text
    assert "Forgot password?" in again.json()["error"]["detail"] and PASSWORD not in again.text
    assert len(signin.fake.account_calls) == 1


@pytest.mark.parametrize("answer, code, words", [
    ((409, {"error": "account_exists"}), "password_exists", "Cara already has a Tico account; use Invite."),
    ((429, {"error": "too_many", "retry_after": 3600}), "password_limited", "Too many new accounts"),
    ((201, {"status": "created", "password": "short"}), "password_failed", "no account was made"),
], ids=["exists", "too-many", "weak"])
def test_a_refused_account_is_a_plain_422(signin, answer, code, words):  # noqa: F811
    signin.fake.accounts = [answer]
    refused = signin.api.post("/api/v2/access/people/cara/password", json={}, headers=owner(signin))
    assert refused.status_code == 422, refused.text      # never 429 or 502: the browser would retry those
    assert refused.json()["error"]["code"] == code and words in refused.json()["error"]["detail"], refused.text


def test_without_an_issuer_that_makes_accounts_the_button_is_off_and_nothing_is_asked(signin):  # noqa: F811
    headers = owner(signin)
    assert signin.api.get("/api/v2/access", headers=headers).json()["account_password"] is False
    signin.fake.accounts = None
    refused = signin.api.post("/api/v2/access/people/cara/password", json={}, headers=headers)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "password_unavailable"
    assert signin.fake.account_calls == []


def test_a_rehearsal_makes_no_account_and_asks_the_issuer_nothing(signin, monkeypatch):  # noqa: F811
    signin.fake.accounts = [(201, {"status": "created", "password": PASSWORD})]
    headers = owner(signin)
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    monkeypatch.setattr(signin.auth.proxy, "_discover", lambda *a, **k: pytest.fail("a rehearsal asked the issuer"))
    assert signin.api.get("/api/v2/access", headers=headers).json()["account_password"] is False
    refused = signin.api.post("/api/v2/access/people/cara/password", json={}, headers=headers)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "rehearsal"
    assert signin.fake.account_calls == []


def test_only_an_owner_or_admin_makes_accounts_for_someone_else_on_the_roster(signin):  # noqa: F811
    signin.fake.accounts = [(201, {"status": "created", "password": PASSWORD})]
    assert signin.login(claims={"email": "cara@acme.example"}).status_code == 302
    member = {"origin": signin.settings.public_url, "idempotency-key": "k-member"}
    assert signin.api.post("/api/v2/access/people/ben/password", json={}, headers=member).status_code == 403
    signin.api.cookies.clear()
    headers = owner(signin)
    assert signin.api.post("/api/v2/access/people/nobody/password", json={}, headers=headers).status_code == 404
    assert signin.api.post("/api/v2/access/people/ana/password", json={}, headers=headers).status_code == 422
    keyless = {k: v for k, v in headers.items() if k != "idempotency-key"}
    assert signin.api.post("/api/v2/access/people/cara/password", json={}, headers=keyless).status_code == 422
    assert signin.fake.account_calls == []
