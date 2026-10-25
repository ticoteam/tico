"""Invite emails (backend/invites.py): the owner clicks Invite and Tico's own sign-in mail sends it, through the Tico
account issuer the install signs in with. Never the company's mailbox; every refusal is shown with a plain reason."""

import uuid

import pytest

from backend.tests.test_api import api  # noqa: F401  (the api fixture)
from backend.tests.test_oidc import CLIENT, SECRET, provider, signin  # noqa: F401  (the fixtures)


def owner(signin):  # noqa: F811
    """Ana, the owner, signed in through the issuer; the headers a browser write carries."""
    done = signin.login(claims={"email": "ana@acme.example"})
    assert done.status_code == 302, done.text
    return {"origin": signin.settings.public_url, "idempotency-key": "invite-" + uuid.uuid4().hex}


def invites_sent(signin):  # noqa: F811
    with signin.auth.store.read() as c:
        from backend import invites
        return invites.sent(c)


def test_invite_goes_out_from_the_tico_issuer_with_the_installs_client_credentials_and_is_recorded(signin):  # noqa: F811
    signin.fake.invites = [(202, {"status": "sent"})]
    headers = owner(signin)
    sent = signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=headers)
    assert sent.status_code == 200, sent.text
    assert sent.json()["person"] == "cara" and sent.json()["invited_at"]
    call, = signin.fake.invite_calls
    # Only the address goes with the install's own client id and secret: no text of the caller's reaches the mail.
    assert call == {"client_id": CLIENT, "client_secret": SECRET, "email": "cara@acme.example"}
    assert invites_sent(signin)["cara"]["by"] == "human:ana"


@pytest.mark.parametrize("answer, code, words", [
    ((502, {"error": "not_sent"}), "invite_failed", "couldn't deliver the invite to cara@acme.example"),
    ((429, {"error": "too_many", "retry_after": 3600}), "invite_limited", "Too many invites"),
    ((503, {"error": "mail_unavailable"}), "invite_failed", "isn't available right now"),
    ((401, {"error": "invalid_client"}), "invite_failed", "refused the invite"),
], ids=["not-sent", "too-many", "no-mail", "bad-client"])
def test_an_invite_that_does_not_go_out_is_a_visible_error_and_is_not_recorded(signin, answer, code, words):  # noqa: F811
    signin.fake.invites = [answer]
    refused = signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=owner(signin))
    # 422, never 429 or 502: the browser retries those as unconfirmed, asking the issuer again and hiding the reason.
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == code and words in refused.json()["error"]["detail"], refused.text
    assert "cara" not in invites_sent(signin)
    assert len(signin.fake.invite_calls) == 1


def test_an_issuer_that_sends_no_invites_is_never_sent_the_secret_and_the_owner_is_told_what_to_do(signin):  # noqa: F811
    refused = signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=owner(signin))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invite_unavailable"
    assert "Tell Cara to sign in at " + signin.settings.public_url in refused.json()["error"]["detail"]
    assert signin.fake.invite_calls == []


def test_a_rehearsal_with_tico_account_sign_in_offers_no_invite_and_asks_the_issuer_nothing(signin, monkeypatch):  # noqa: F811
    """TICO_REHEARSAL=1 runs on a copy of real data with the real install's sign-in settings: an invite would mail a
    real person with the real client secret, so it is refused before discovery and Send invite isn't offered."""
    signin.fake.invites = [(202, {"status": "sent"})]
    headers = owner(signin)
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    monkeypatch.setattr(signin.auth.proxy, "_discover", lambda *a, **k: pytest.fail("a rehearsal asked the issuer"))
    assert signin.api.get("/api/v2/access", headers=headers).json()["invite_email"] is False
    refused = signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=headers)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "rehearsal", refused.text
    with pytest.raises(Exception, match="rehearsal"):
        signin.auth.proxy.invite("cara@acme.example")
    assert signin.fake.invite_calls == [] and "cara" not in invites_sent(signin)


def test_only_an_owner_or_admin_sends_invites_and_only_to_someone_on_the_roster(signin):  # noqa: F811
    signin.fake.invites = [(202, {"status": "sent"})]
    assert signin.login(claims={"email": "cara@acme.example"}).status_code == 302
    headers = {"origin": signin.settings.public_url}
    assert signin.api.post("/api/v2/access/people/ben/invite", json={}, headers=headers).status_code == 403
    signin.api.cookies.clear()
    headers = owner(signin)
    assert signin.api.post("/api/v2/access/people/nobody/invite", json={}, headers=headers).status_code == 404
    assert signin.fake.invite_calls == []


def test_one_invite_request_sends_one_email_across_a_repeat_an_overlap_and_a_lost_answer(signin, monkeypatch):  # noqa: F811
    """The browser repeats a write whose answer it lost with the same Idempotency-Key (ui/app/api.js): that request
    never asks the issuer twice, and its answer stays the first one. A new click is a new key and sends again."""
    from backend import invites
    signin.fake.invites = [(202, {"status": "sent"}) for _ in range(3)]
    headers = owner(signin)
    post = lambda key: signin.api.post("/api/v2/access/people/cara/invite", json={},
                                       headers={**headers, "idempotency-key": key})
    keyless = {k: v for k, v in headers.items() if k != "idempotency-key"}
    assert signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=keyless).status_code == 422
    assert signin.fake.invite_calls == []

    # An overlapping copy, arriving while the first waits on the issuer, is refused and sends nothing.
    issuer, overlapped = signin.auth.proxy.invite, []

    def invite(email):
        if not overlapped:
            overlapped.append(None)
            overlapped[0] = post("k-1")
        return issuer(email)
    monkeypatch.setattr(signin.auth.proxy, "invite", invite)
    first = post("k-1")
    monkeypatch.setattr(signin.auth.proxy, "invite", issuer)
    assert first.status_code == 200, first.text
    assert overlapped[0].status_code == 422 and overlapped[0].json()["error"]["code"] == "invite_unknown"
    again = post("k-1")
    assert again.status_code == 200 and again.json() == first.json() and len(signin.fake.invite_calls) == 1

    # Sent, then the server failed before answering: the repeat records it and sends nothing more.
    record = invites.record
    monkeypatch.setattr(invites, "record", lambda *a: (_ for _ in ()).throw(RuntimeError("lost")))
    with pytest.raises(RuntimeError):
        post("k-2")
    monkeypatch.setattr(invites, "record", record)
    assert len(signin.fake.invite_calls) == 2
    recovered = post("k-2")
    assert recovered.status_code == 200 and post("k-2").json() == recovered.json(), recovered.text
    assert len(signin.fake.invite_calls) == 2 and invites_sent(signin)["cara"]["at"] == recovered.json()["invited_at"]

    assert post("k-3").status_code == 200 and len(signin.fake.invite_calls) == 3      # Invite again: a new send


def test_the_secret_never_goes_to_an_invite_endpoint_off_the_issuer_over_plain_http_or_behind_a_redirect(  # noqa: F811
        signin, provider, monkeypatch):  # noqa: F811
    """Oidc.invite posts the install's client secret only to an https endpoint on the issuer's own host (plain http
    only when both are loopback), and never follows a redirect, which would carry the form somewhere else."""
    import httpx
    elsewhere = provider()
    elsewhere.invites = [(202, {"status": "sent"})]
    signin.fake.invites = []
    signin.fake.invite_endpoint = elsewhere.issuer + "/oauth2/invite"           # another host:port
    refused = signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=owner(signin))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invite_unavailable", refused.text
    signin.fake.invite_endpoint, signin.fake.invite_redirect = None, elsewhere.issuer + "/oauth2/invite"
    signin.auth.proxy._discovery = None                                         # read discovery again
    refused = signin.api.post("/api/v2/access/people/cara/invite", json={}, headers=owner(signin))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invite_failed", refused.text
    assert len(signin.fake.invite_calls) == 1 and elsewhere.invite_calls == []

    proxy, posted = signin.auth.proxy, []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: posted.append(a) or (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(proxy, "issuer", "https://id.acme.example")
    for endpoint in ("http://id.acme.example/oauth2/invite", "https://id.acme.example.evil.example/oauth2/invite"):
        monkeypatch.setattr(proxy, "_discover", lambda: {"tico_invite_endpoint": endpoint})
        assert proxy.invite("cara@acme.example") is None
    assert posted == []
