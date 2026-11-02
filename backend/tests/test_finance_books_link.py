"""QuickBooks and Xero books connections (spec §3.4): the routes that start, finish and drop a consent against a fake
HQ, the refresh's vault write retried, and the refusals that keep the claim the only way to make a books connection.
Nothing here calls HQ or the provider: `books_link._call` answers from lists."""
import json
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from backend import company_server, hook_relay
from backend.finance import actions as FA
from backend.finance import books_link
from backend.finance.store import banking as SK
from backend.finance.store import mirror as SX
from backend.finance.store import tico as T
from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_hook_relay import hq, service  # noqa: F401
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

COMPANY = "2f7c1d3e-5b8a-4c6d-9e0f-1a2b3c4d5e6f"            # a fictional company id
CONSENT = "https://consent.acme.example/start/1"
LATER = "2026-10-15T12:40:00Z"
LINK = hook_relay.Link("gk_test", "https://updates.tico.team/v1/gateway", "fp")
BOOKS_RETURN = "/api/v2/finance/books/return/"


class HQ:
    """HQ's books broker in place of the network: each endpoint answers from its list in order (an exception is
    raised), and every call is recorded as (path, body)."""

    def __init__(self, **answers: list[Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, link: object, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, dict(body)))
        answer = self.answers[path.rsplit("/", 1)[1]].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)


def tokens(access: str, refresh: str, expires: str = "2026-10-15T12:30:00Z") -> dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires, "refresh_expires_at": None,
            "realm_id": "123", "tenant_id": None}


def claims(hq: HQ) -> int:
    return sum(1 for path, _ in hq.calls if path.endswith("/claim"))


def return_path(api: Any, hq: HQ) -> str:
    """The path HQ sends the browser to after the last connect: this server's return route, with its flow id."""
    base = api.app.state.store.settings.public_url
    to = str(hq.calls[-1][1]["return_to"])
    assert to.startswith(base + BOOKS_RETURN)
    return to[len(base):]


def result_of(location: str) -> dict[str, list[str]]:
    """The query of the Finance page a return redirects to (the page is a hash route, so the query is in the fragment)."""
    return parse_qs(urlparse(location).fragment.partition("?")[2])


@pytest.fixture
def linked(api, monkeypatch):
    """This server as the owner's: Finance set up, linked to a fictional company with a Gateway key, and HQ's books
    broker answering in place of the network."""
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
    monkeypatch.setattr(company_server, "view", lambda c: {"company": {"id": COMPANY, "name": "Acme", "slug": "acme"}})
    monkeypatch.setattr(hook_relay, "link_of", lambda c, vault, settings: LINK)
    hq = HQ(connect=[{"url": CONSENT, "expires_in": 900}], claim=[tokens("acc-1", "ref-1")],
            disconnect=[{"ok": True, "disconnected": True}])
    monkeypatch.setattr(books_link, "_call", hq)
    return hq


def test_connect_returns_the_consent_page_and_sends_hq_tico_s_own_return_route(api, linked):
    r = api.post("/api/v2/finance/books/connect", json={"provider": "quickbooks"}, headers=headers())
    assert r.status_code == 200 and r.json() == {"url": CONSENT, "expires_in": 900}
    path, body = linked.calls[0]
    assert path == "oauth/quickbooks/connect"
    assert body["company_id"] == COMPANY and body["user_id"] == OWNER
    assert str(body["return_to"]).startswith(api.app.state.store.settings.public_url + BOOKS_RETURN)


def test_the_return_claims_the_grant_once_and_keeps_its_tokens_in_the_vault(api, linked):
    store = api.app.state.store
    api.post("/api/v2/finance/books/connect", json={"provider": "quickbooks"}, headers=headers())
    back = return_path(api, linked)
    r = api.get(back + "?finance=connected&claim=clm_1", headers=headers(), follow_redirects=False)
    assert r.status_code == 302 and result_of(r.headers["location"]) == {"books": ["connected"]}
    assert claims(linked) == 1
    with store.read() as c:
        row = c.execute("SELECT id, provider, purpose, status, settings_json FROM fin_connections").fetchone()
        assert (row["provider"], row["purpose"], row["status"]) == ("quickbooks", "books", "active")
        assert "ref-1" not in row["settings_json"] and "acc-1" not in row["settings_json"]
        refresh = str(SX.books_settings(c, row["id"])["refresh"])
        assert T.credential_secret(c, api.app.state.vault, refresh) == "ref-1"
    again = api.get(back + "?finance=connected&claim=clm_1", headers=headers(), follow_redirects=False)
    assert again.status_code == 409 and claims(linked) == 1                 # the consent's flow is spent
    accounting = next(g for g in get(api, "finance/sources")["groups"] if g["group"] == "accounting")
    assert [(r["provider"], r["sync"]["status"]) for r in accounting["rows"]] == [("quickbooks", "active")]
    assert "ref-1" not in json.dumps(accounting) and "acc-1" not in json.dumps(accounting)


def test_a_consent_that_came_back_with_an_error_claims_nothing(api, linked):
    store = api.app.state.store
    api.post("/api/v2/finance/books/connect", json={"provider": "quickbooks"}, headers=headers())
    back = return_path(api, linked)
    r = api.get(back + "?finance=error&reason=access_denied&claim=clm_9", headers=headers(), follow_redirects=False)
    assert r.status_code == 302
    assert result_of(r.headers["location"]) == {"books": ["error"], "reason": ["access_denied"]}
    assert claims(linked) == 0
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM fin_connections").fetchone()[0] == 0


def test_a_teammate_who_may_not_connect_is_refused_at_connect_disconnect_and_return(api, linked):
    store = api.app.state.store
    with store.transaction() as c:
        SX.books_connection_insert(c, cid="conn-qb", provider="quickbooks", name="Acme books", by=OWNER, at=AT)
        SX.set_books_settings(c, "conn-qb", {"provider": "quickbooks", "company_id": COMPANY})
    api.post("/api/v2/finance/books/connect", json={"provider": "quickbooks"}, headers=headers())
    back = return_path(api, linked)
    linked.calls.clear()
    assert api.post("/api/v2/finance/books/connect", json={"provider": "quickbooks"},
                    headers=headers("cara-test")).status_code == 403
    assert api.post("/api/v2/finance/books/conn-qb/disconnect", headers=headers("cara-test")).status_code == 403
    assert api.get(back + "?finance=connected&claim=clm_2", headers=headers("cara-test"),
                   follow_redirects=False).status_code == 403
    assert linked.calls == []                                               # HQ is never asked for them


def test_a_return_is_claimed_only_by_the_person_who_started_the_consent_and_once(store, monkeypatch):
    hq = HQ(connect=[{"url": CONSENT, "expires_in": 900}], claim=[tokens("acc-1", "ref-1")])
    monkeypatch.setattr(books_link, "_call", hq)
    books_link.start(store, LINK, provider="quickbooks", company_id=COMPANY, user=OWNER,
                     return_base="https://tico.acme.example" + BOOKS_RETURN.rstrip("/"))
    flow = str(hq.calls[0][1]["return_to"]).rsplit("/", 1)[1]
    vault = T._VAULT[0]
    with pytest.raises(books_link.NotStarted):
        books_link.finish(store, vault, LINK, flow=flow, user="human:ben", outcome="connected", reason="",
                          claim_id="clm_1")
    cid = books_link.finish(store, vault, LINK, flow=flow, user=OWNER, outcome="connected", reason="", claim_id="clm_1")
    with store.read() as c:
        assert SX.books_status(c, cid) == "active"
    with pytest.raises(books_link.NotStarted):
        books_link.finish(store, vault, LINK, flow=flow, user=OWNER, outcome="connected", reason="", claim_id="clm_1")


def _claimed(books: Any, vault: object) -> str:
    return books_link.connect_claim(books, vault, LINK, provider="quickbooks", company_id=COMPANY, claim_id="clm_r",
                                    name="Acme books", by=OWNER, now=AT)


def test_a_consent_for_a_company_already_connected_repairs_that_connection(books, monkeypatch):
    vault = T._VAULT[0]
    other = {**tokens("acc-4", "ref-4"), "realm_id": "456"}
    hq = HQ(claim=[tokens("acc-1", "ref-1"), tokens("acc-3", "ref-3"), other, other])
    monkeypatch.setattr(books_link, "_call", hq)
    first = _claimed(books, vault)
    assert _claimed(books, vault) == first                                  # the same realm: the same connection
    with books.read() as c:
        assert [row[0] for row in SX.books_connections(c, "quickbooks")] == [first]
        assert SX.books_status(c, first) == "active"
        assert T.credential_secret(c, vault, str(SX.books_settings(c, first)["refresh"])) == "ref-3"
    with pytest.raises(books_link.AlreadyConnected):                        # another realm for this company: refused (spec 3.4)
        _claimed(books, vault)
    with books.read() as c:
        assert [row[0] for row in SX.books_connections(c, "quickbooks")] == [first]
    second = books_link.connect_claim(books, vault, LINK, provider="quickbooks", company_id="co-other", claim_id="clm_o",
                                      name="Other books", by=OWNER, now=AT)           # another company may connect it
    assert second != first


def test_a_refresh_whose_vault_write_fails_once_keeps_the_connection_active(books, monkeypatch):
    vault = T._VAULT[0]
    hq = HQ(claim=[tokens("acc-1", "ref-1")], refresh=[tokens("acc-2", "ref-2", "2026-10-15T13:30:00Z")])
    monkeypatch.setattr(books_link, "_call", hq)
    cid = _claimed(books, vault)
    real_save, tries = books_link.save, []

    def flaky(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        tries.append(fresh.refresh_token)
        if len(tries) == 1:
            raise RuntimeError("vault is busy")
        real_save(c, cid_, fresh, by=by, at=at)

    monkeypatch.setattr(books_link, "save", flaky)
    assert books_link.access_token(books, vault, LINK, cid, "quickbooks", now=LATER, force=True) == "acc-2"
    assert tries == ["ref-2", "ref-2"]                                      # the same pair, written again
    assert [path for path, _ in hq.calls].count("oauth/quickbooks/refresh") == 1
    with books.read() as c:
        assert SX.books_status(c, cid) == "active"
        assert T.credential_secret(c, vault, str(SX.books_settings(c, cid)["refresh"])) == "ref-2"


def test_a_refresh_whose_vault_write_always_fails_asks_for_a_reconnect_and_raises(books, monkeypatch):
    vault = T._VAULT[0]
    hq = HQ(claim=[tokens("acc-1", "ref-1")], refresh=[tokens("acc-2", "ref-2", "2026-10-15T13:30:00Z")])
    monkeypatch.setattr(books_link, "_call", hq)
    cid = _claimed(books, vault)
    tries, asks = [], []

    def down(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        tries.append(fresh.refresh_token)
        raise RuntimeError("vault is down")

    monkeypatch.setattr(books_link, "save", down)
    monkeypatch.setattr(T, "needs_you", lambda c, person, title, body: asks.append((person, title)))
    with pytest.raises(books_link.ReconnectRequired) as err:
        books_link.access_token(books, vault, LINK, cid, "quickbooks", now=LATER, force=True)
    assert err.value.code == "vault_write_failed"
    assert len(tries) == books_link.VAULT_TRIES and asks == [(OWNER, "Connect quickbooks again")]
    with books.read() as c:
        assert SX.books_status(c, cid) == "reconnect"
    assert [path for path, _ in hq.calls].count("oauth/quickbooks/refresh") == 1


def test_a_disconnect_HQ_no_longer_holds_a_grant_for_still_drops_the_local_tokens(books, monkeypatch):
    vault = T._VAULT[0]
    hq = HQ(claim=[tokens("acc-1", "ref-1")], disconnect=[books_link.ReconnectRequired("reconnect_required")])
    monkeypatch.setattr(books_link, "_call", hq)
    cid = _claimed(books, vault)
    with books.read() as c:
        before = dict(SX.books_settings(c, cid))
    books_link.drop(books, vault, LINK, cid, by=OWNER)
    with books.read() as c:
        assert SX.books_status(c, cid) == "disconnected"
        assert not T.credential_exists(c, str(before["access"]))
        assert not T.credential_exists(c, str(before["refresh"]))


def test_connection_create_refuses_books_providers_so_the_claim_makes_every_books_connection(books):
    with books.transaction() as c:
        for provider in ("quickbooks", "xero"):
            done = act(c, OWNER, "connection_create", {"provider": provider, "credential_id": "cred-books", "probe": {}})
            assert done.status == "failed" and done.code == "invalid"
    with books.read() as c:
        assert c.execute("SELECT count(*) FROM fin_connections").fetchone()[0] == 0


def test_sources_show_a_books_connection_with_its_mirror_state_and_outbox_counts(api, linked):
    store = api.app.state.store
    api.post("/api/v2/finance/books/connect", json={"provider": "quickbooks"}, headers=headers())
    api.get(return_path(api, linked) + "?finance=connected&claim=clm_3", headers=headers(), follow_redirects=False)
    zero = {"pending": 0, "failed": 0, "unknown": 0, "withheld": 0}
    accounting = next(g for g in get(api, "finance/sources")["groups"] if g["group"] == "accounting")
    cid = accounting["rows"][0]["id"]
    assert [(r["mirror"], r["outbox"]) for r in accounting["rows"]] == [("native", zero)]
    with store.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload={}, key="bill.create:one",
                      by=OWNER, at=AT)
        SX.set_connection_status(c, cid, "reconnect", "reconnect_required", AT)
    accounting = next(g for g in get(api, "finance/sources")["groups"] if g["group"] == "accounting")
    assert [(r["mirror"], r["outbox"]["pending"]) for r in accounting["rows"]] == [("reconnect", 1)]


# ------------------------------------------------------------------------------------------------ HQ's answers by code
FAR = "2030-01-01T00:00:00Z"                  # a token not due for a refresh in any test


class Wire:
    """HQ's answers at the HTTP layer, so books_link._call reads them as it does in production: each endpoint
    answers from its list in order, as (status, body). An error status raises as hook_relay.Link.call does, with
    HQ's error code."""

    def __init__(self, **answers: list[tuple[int, dict[str, Any]]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, path: str, body: Any = None, *, area: str = "") -> dict[str, Any]:
        self.calls.append((path, dict(body or {})))
        status, answer = self.answers[path.rsplit("/", 1)[1]].pop(0)
        if status == 401:
            raise hook_relay.Stop()
        if status >= 400:
            error = answer.get("error")
            raise hook_relay.Failed(error if isinstance(error, str) else f"http_{status}", status, answer)
        return dict(answer)


def ok(answer: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return 200, answer


def err(status: int, code: str) -> tuple[int, dict[str, Any]]:
    return status, {"error": code}


def wired(monkeypatch: pytest.MonkeyPatch, **answers: list[tuple[int, dict[str, Any]]]) -> Wire:
    wire = Wire(**answers)
    monkeypatch.setattr(books_link, "root", lambda link: wire)
    return wire


def xero_tokens(access: str, refresh: str) -> dict[str, Any]:
    return {**tokens(access, refresh, FAR), "realm_id": None, "tenant_id": "ten-1"}


def _claimed_xero(books: Any, vault: object) -> str:
    return books_link.connect_claim(books, vault, LINK, provider="xero", company_id=COMPANY, claim_id="clm_x",
                                    name="Xero", by=OWNER, now=AT)


def test_hq_codes_decide_what_a_refusal_means_for_the_install(monkeypatch):
    wired(monkeypatch, disconnect=[err(409, "access_expired"), err(502, "revoke_failed")],
          claim=[err(409, "connected_elsewhere")])
    with pytest.raises(books_link.BrokerFailed) as expired:
        books_link.revoke(LINK, "xero", COMPANY, "ref-1", "acc-1")
    assert expired.value.code == "access_expired" and not isinstance(expired.value, books_link.ReconnectRequired)
    with pytest.raises(books_link.BrokerFailed) as failed:
        books_link.revoke(LINK, "xero", COMPANY, "ref-1", "acc-1")
    assert failed.value.code == "revoke_failed"
    with pytest.raises(books_link.BrokerFailed) as elsewhere:
        books_link.claim(LINK, "xero", COMPANY, "clm_x")
    assert elsewhere.value.code == "connected_elsewhere" and not isinstance(elsewhere.value,
                                                                              books_link.ReconnectRequired)


def test_a_xero_disconnect_sends_the_install_access_token_and_drops_its_own_connection(books, monkeypatch):
    vault = T._VAULT[0]
    wire = wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1"))],
                 disconnect=[ok({"ok": True, "disconnected": True})])
    cid = _claimed_xero(books, vault)
    books_link.drop(books, vault, LINK, cid, by=OWNER)
    assert wire.calls[-1] == ("oauth/xero/disconnect", {"company_id": COMPANY, "refresh_token": "ref-1",
                                                       "access_token": "acc-1"})
    with books.read() as c:
        assert SX.books_status(c, cid) == "disconnected"


def test_a_xero_disconnect_refused_as_access_expired_refreshes_once_and_asks_again(books, monkeypatch):
    vault = T._VAULT[0]
    wire = wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1"))],
                 disconnect=[err(409, "access_expired"), ok({"ok": True, "disconnected": True})],
                 refresh=[ok(xero_tokens("acc-2", "ref-2"))])
    cid = _claimed_xero(books, vault)
    books_link.drop(books, vault, LINK, cid, by=OWNER)
    sent = [body for path, body in wire.calls if path == "oauth/xero/disconnect"]
    assert [(body["refresh_token"], body["access_token"]) for body in sent] == [("ref-1", "acc-1"), ("ref-2", "acc-2")]
    assert [path for path, _ in wire.calls].count("oauth/xero/refresh") == 1
    with books.read() as c:
        assert SX.books_status(c, cid) == "disconnected"


def test_a_xero_disconnect_still_refused_as_access_expired_after_a_refresh_keeps_the_connection(books, monkeypatch):
    vault = T._VAULT[0]
    wire = wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1"))],
                 disconnect=[err(409, "access_expired"), err(409, "access_expired")],
                 refresh=[ok(xero_tokens("acc-2", "ref-2"))])
    cid = _claimed_xero(books, vault)
    with pytest.raises(books_link.BrokerFailed) as refused:
        books_link.drop(books, vault, LINK, cid, by=OWNER)
    assert refused.value.code == "access_expired"
    assert [path for path, _ in wire.calls].count("oauth/xero/disconnect") == 2
    with books.read() as c:
        assert SX.books_status(c, cid) == "active"
        assert T.credential_secret(c, vault, str(SX.books_settings(c, cid)["refresh"])) == "ref-2"


def test_a_disconnect_HQ_cannot_finish_keeps_the_connection_and_a_second_try_takes_it_off(books, monkeypatch):
    vault = T._VAULT[0]
    wire = wired(monkeypatch, claim=[ok(tokens("acc-1", "ref-1"))],
                 disconnect=[err(502, "revoke_failed"), ok({"ok": True, "disconnected": True})])
    cid = _claimed(books, vault)
    with pytest.raises(books_link.BrokerFailed) as failed:
        books_link.drop(books, vault, LINK, cid, by=OWNER)
    assert failed.value.code == "revoke_failed"
    with books.read() as c:
        assert SX.books_status(c, cid) == "active"
        assert T.credential_exists(c, str(SX.books_settings(c, cid)["refresh"]))
    books_link.drop(books, vault, LINK, cid, by=OWNER)
    with books.read() as c:
        assert SX.books_status(c, cid) == "disconnected"
    sent = [body for path, body in wire.calls if path == "oauth/quickbooks/disconnect"]
    assert sent == [{"company_id": COMPANY, "refresh_token": "ref-1"}] * 2       # QuickBooks is sent no access token


def test_a_consent_HQ_refuses_as_connected_elsewhere_makes_no_connection_and_says_why(books, monkeypatch):
    wired(monkeypatch, claim=[err(409, "connected_elsewhere")])
    with pytest.raises(books_link.BrokerFailed) as refused:
        _claimed_xero(books, T._VAULT[0])
    assert refused.value.code == "connected_elsewhere" and not isinstance(refused.value, books_link.ReconnectRequired)
    with books.read() as c:
        assert c.execute("SELECT count(*) FROM fin_connections").fetchone()[0] == 0


@pytest.fixture
def wired_api(api, monkeypatch):
    """This server as the owner's, linked to a fictional company; HQ's answers are wired per test with `wired`."""
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
    monkeypatch.setattr(company_server, "view", lambda c: {"company": {"id": COMPANY, "name": "Acme", "slug": "acme"}})
    monkeypatch.setattr(hook_relay, "link_of", lambda c, vault, settings: LINK)
    return api


def test_a_xero_consent_another_install_holds_says_so_on_the_return(wired_api, monkeypatch):
    wire = wired(monkeypatch, connect=[ok({"url": CONSENT, "expires_in": 900})],
                 claim=[err(409, "connected_elsewhere")])
    wired_api.post("/api/v2/finance/books/connect", json={"provider": "xero"}, headers=headers())
    r = wired_api.get(return_path(wired_api, wire) + "?finance=connected&claim=clm_5", headers=headers(),
                      follow_redirects=False)
    assert r.status_code == 302
    assert result_of(r.headers["location"]) == {"books": ["error"], "reason": ["connected_elsewhere"]}
    with wired_api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM fin_connections").fetchone()[0] == 0


def test_a_disconnect_HQ_cannot_finish_answers_a_retryable_reason_and_keeps_the_connection(wired_api, monkeypatch):
    store = wired_api.app.state.store
    wire = wired(monkeypatch, connect=[ok({"url": CONSENT, "expires_in": 900})],
                 claim=[ok(xero_tokens("acc-1", "ref-1"))], disconnect=[err(502, "revoke_failed")])
    wired_api.post("/api/v2/finance/books/connect", json={"provider": "xero"}, headers=headers())
    wired_api.get(return_path(wired_api, wire) + "?finance=connected&claim=clm_6", headers=headers(),
                  follow_redirects=False)
    with store.read() as c:
        cid = str(SX.books_connections(c)[0][0])
    r = wired_api.post(f"/api/v2/finance/books/{cid}/disconnect", headers=headers())
    assert r.status_code == 502 and "revoke_failed" in r.text and "disconnect again" in r.text
    with store.read() as c:
        assert SX.books_status(c, cid) == "active"


def _xero_disconnect_setup(wired_api, monkeypatch, disconnect):
    """A Xero connection whose access token is due, so a disconnect refreshes first. Returns HQ's wire and the id."""
    store = wired_api.app.state.store
    expired = {**tokens("acc-1", "ref-1", "2026-01-01T00:00:00Z"), "realm_id": None, "tenant_id": "ten-1"}
    wire = wired(monkeypatch, connect=[ok({"url": CONSENT, "expires_in": 900})], claim=[ok(expired)],
                 refresh=[ok(xero_tokens("acc-2", "ref-2"))], disconnect=disconnect)
    wired_api.post("/api/v2/finance/books/connect", json={"provider": "xero"}, headers=headers())
    wired_api.get(return_path(wired_api, wire) + "?finance=connected&claim=clm_7", headers=headers(),
                  follow_redirects=False)
    with store.read() as c:
        return wire, str(SX.books_connections(c)[0][0])


def test_a_disconnect_whose_vault_write_fails_still_takes_the_grant_off_at_hq(wired_api, monkeypatch):
    store = wired_api.app.state.store
    wire, cid = _xero_disconnect_setup(wired_api, monkeypatch, [ok({"ok": True, "disconnected": True})])

    def down(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        raise RuntimeError("vault is down")

    monkeypatch.setattr(books_link, "save", down)
    r = wired_api.post(f"/api/v2/finance/books/{cid}/disconnect", headers=headers())
    assert r.status_code == 200 and r.json()["status"] == "disconnected"
    sent = [body for path, body in wire.calls if path == "oauth/xero/disconnect"]
    assert sent == [{"company_id": COMPANY, "refresh_token": "ref-2", "access_token": "acc-2"}]   # the rotated pair
    with store.read() as c:
        assert SX.books_status(c, cid) == "disconnected"


def test_a_disconnect_whose_vault_write_and_hq_both_fail_keeps_the_connection_and_asks_to_connect_again(
        wired_api, monkeypatch):
    store = wired_api.app.state.store
    wire, cid = _xero_disconnect_setup(wired_api, monkeypatch, [err(502, "revoke_failed")])

    def down(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        raise RuntimeError("vault is down")

    monkeypatch.setattr(books_link, "save", down)
    r = wired_api.post(f"/api/v2/finance/books/{cid}/disconnect", headers=headers())
    assert r.status_code == 502 and r.json()["error"]["code"] == "vault_write_failed"
    assert r.json()["error"]["retryable"] is True and r.json()["error"]["reason"] == "reconnect_required"
    assert [path for path, _ in wire.calls].count("oauth/xero/disconnect") == 1     # HQ was asked with the rotated pair
    with store.read() as c:
        assert SX.books_status(c, cid) == "reconnect"                                # the owner sees it in Finance


def test_a_disconnect_that_HQ_refuses_keeps_the_connection_once_the_retried_write_lands(wired_api, monkeypatch):
    store = wired_api.app.state.store
    wire, cid = _xero_disconnect_setup(wired_api, monkeypatch, [err(502, "revoke_failed")])
    real_save, tries = books_link.save, []

    def flaky(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        tries.append(fresh.refresh_token)
        if len(tries) <= 2:                                  # the refresh's two writes fail; the retry lands
            raise RuntimeError("vault is busy")
        real_save(c, cid_, fresh, by=by, at=at)

    monkeypatch.setattr(books_link, "save", flaky)
    r = wired_api.post(f"/api/v2/finance/books/{cid}/disconnect", headers=headers())
    assert tries == ["ref-2", "ref-2", "ref-2"]
    assert r.status_code == 502 and r.json()["error"]["code"] == "revoke_failed"
    assert r.json()["error"]["retryable"] is True
    with store.read() as c:
        assert SX.books_status(c, cid) == "active"
        assert T.credential_secret(c, T._VAULT[0], str(SX.books_settings(c, cid)["refresh"])) == "ref-2"


# ------------------------------------------------------------------------------------ one organisation per install
def replacement(**changes: Any) -> dict[str, Any]:
    """HQ's replacement for the held organisation: the new pair, the held company and its tenant."""
    return {"company_id": COMPANY, **xero_tokens("acc-2", "ref-2"), **changes}


def _refusal(replacement_: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """HQ's 409 already_connected, with the replacement when HQ sends one."""
    body: dict[str, Any] = {"error": "already_connected"}
    if replacement_ is not None:
        body["replacement"] = replacement_
    return 409, body


def test_already_connected_with_a_replacement_keeps_the_held_connection_on_the_new_pair(books, monkeypatch):
    vault = T._VAULT[0]
    wire = wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal(replacement())])
    cid = _claimed_xero(books, vault)
    with books.read() as c:
        before = dict(SX.books_settings(c, cid))
    with pytest.raises(books_link.AlreadyConnected) as refused:
        _claimed_xero(books, vault)
    assert refused.value.code == "already_connected" and refused.value.replacement is not None
    with books.read() as c:
        assert [row[0] for row in SX.books_connections(c, "xero")] == [cid]      # the refused organisation made none
        assert SX.books_status(c, cid) == "active"
        after = SX.books_settings(c, cid)
        assert (after["company_id"], after["tenant_id"]) == (COMPANY, "ten-1")
        assert not T.credential_exists(c, str(before["access"]))                # the old pair is no longer used
        assert not T.credential_exists(c, str(before["refresh"]))
        assert T.credential_secret(c, vault, str(after["access"])) == "acc-2"
        assert T.credential_secret(c, vault, str(after["refresh"])) == "ref-2"
    assert books_link.access_token(books, vault, LINK, cid, "xero", now=AT) == "acc-2"   # not due: no HQ call
    assert [path for path, _ in wire.calls].count("oauth/xero/refresh") == 0


@pytest.mark.parametrize("changes", [{"tenant_id": "ten-2"}, {"company_id": "co-other"}, {"tenant_id": None}])
def test_already_connected_whose_ids_are_not_the_held_connections_saves_nothing(books, monkeypatch,
                                                                               changes: dict[str, Any]):
    vault = T._VAULT[0]
    wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal(replacement(**changes))])
    cid = _claimed_xero(books, vault)
    with books.read() as c:
        before = dict(SX.books_settings(c, cid))
    with pytest.raises(books_link.BrokerFailed) as bad:
        _claimed_xero(books, vault)
    assert bad.value.code == "bad_answer"
    with books.read() as c:
        assert SX.books_settings(c, cid) == before
        assert SX.books_status(c, cid) == "active"
        assert T.credential_secret(c, vault, str(before["refresh"])) == "ref-1"


def test_already_connected_without_a_replacement_changes_nothing(books, monkeypatch):
    vault = T._VAULT[0]
    wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal()])
    cid = _claimed_xero(books, vault)
    with books.read() as c:
        before = dict(SX.books_settings(c, cid))
    with pytest.raises(books_link.AlreadyConnected) as refused:
        _claimed_xero(books, vault)
    assert refused.value.code == "already_connected" and refused.value.replacement is None
    with books.read() as c:
        assert [row[0] for row in SX.books_connections(c, "xero")] == [cid]
        assert SX.books_settings(c, cid) == before
        assert SX.books_status(c, cid) == "active"
        assert T.credential_secret(c, vault, str(before["refresh"])) == "ref-1"


def test_a_replacement_whose_vault_write_fails_once_still_lands(books, monkeypatch):
    vault = T._VAULT[0]
    wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal(replacement())])
    cid = _claimed_xero(books, vault)
    real_save, tries = books_link.save, []

    def flaky(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        tries.append(fresh.refresh_token)
        if len(tries) == 1:
            raise RuntimeError("vault is busy")
        real_save(c, cid_, fresh, by=by, at=at)

    monkeypatch.setattr(books_link, "save", flaky)
    with pytest.raises(books_link.AlreadyConnected):
        _claimed_xero(books, vault)
    assert tries == ["ref-2", "ref-2"]                                          # the same pair, written again
    with books.read() as c:
        assert SX.books_status(c, cid) == "active"
        assert T.credential_secret(c, vault, str(SX.books_settings(c, cid)["refresh"])) == "ref-2"


def test_a_replacement_vault_write_that_always_fails_asks_to_connect_again(books, monkeypatch):
    vault = T._VAULT[0]
    wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal(replacement())])
    cid = _claimed_xero(books, vault)
    tries, asks = [], []

    def down(c: Any, cid_: str, fresh: Any, *, by: str, at: str) -> None:
        tries.append(fresh.refresh_token)
        raise RuntimeError("vault is down")

    monkeypatch.setattr(books_link, "save", down)
    monkeypatch.setattr(T, "needs_you", lambda c, person, title, body: asks.append((person, title)))
    with pytest.raises(books_link.ReconnectRequired) as failed:
        _claimed_xero(books, vault)
    assert failed.value.code == "vault_write_failed"
    assert len(tries) == books_link.VAULT_TRIES and asks == [(OWNER, "Connect xero again")]
    with books.read() as c:
        assert SX.books_status(c, cid) == "reconnect"


def test_a_return_refused_already_connected_says_so_and_the_held_connection_keeps_the_new_pair(wired_api, monkeypatch):
    store = wired_api.app.state.store
    wire = wired(monkeypatch,
                 connect=[ok({"url": CONSENT, "expires_in": 900}), ok({"url": CONSENT, "expires_in": 900})],
                 claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal(replacement())])
    wired_api.post("/api/v2/finance/books/connect", json={"provider": "xero"}, headers=headers())
    wired_api.get(return_path(wired_api, wire) + "?finance=connected&claim=clm_8", headers=headers(),
                  follow_redirects=False)
    wired_api.post("/api/v2/finance/books/connect", json={"provider": "xero"}, headers=headers())
    r = wired_api.get(return_path(wired_api, wire) + "?finance=connected&claim=clm_9", headers=headers(),
                      follow_redirects=False)
    assert r.status_code == 302
    assert result_of(r.headers["location"]) == {"books": ["error"], "reason": ["already_connected"]}
    with store.read() as c:
        held = SX.books_connections(c)
        assert len(held) == 1
        assert T.credential_secret(c, T._VAULT[0], str(SX.books_settings(c, held[0][0])["refresh"])) == "ref-2"


# ------------------------------------------------------------------------------ a refresh and a replacement interleaved
def test_a_refresh_that_finishes_after_a_newer_pair_was_saved_drops_its_own_pair(books, monkeypatch):
    vault = T._VAULT[0]
    wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1"))])
    cid = _claimed_xero(books, vault)

    def refresh_while_a_newer_pair_lands(link, provider, company, refresh_token):
        # an unlocked save lands while this refresh is at HQ, as a pair kept after a refused disconnect can
        with books.transaction() as c:
            books_link.save(c, cid, books_link._tokens(xero_tokens("acc-2", "ref-2")), by=OWNER, at=AT)
        return books_link._tokens(xero_tokens("acc-3", "ref-3"))

    monkeypatch.setattr(books_link, "refresh", refresh_while_a_newer_pair_lands)
    assert books_link.access_token(books, vault, LINK, cid, "xero", now=LATER, force=True) == "acc-2"
    with books.read() as c:
        settings = SX.books_settings(c, cid)
        assert T.credential_secret(c, vault, str(settings["refresh"])) == "ref-2"    # the newer pair stays
        assert T.credential_secret(c, vault, str(settings["access"])) == "acc-2"
        assert SX.books_status(c, cid) == "active"


def test_a_replacement_waits_for_a_refresh_at_hq_and_the_newer_pair_is_the_one_kept(books, monkeypatch):
    vault = T._VAULT[0]
    wired(monkeypatch, claim=[ok(xero_tokens("acc-1", "ref-1")), _refusal(replacement())])
    cid = _claimed_xero(books, vault)
    at_hq, release = threading.Event(), threading.Event()

    def slow_refresh(link, provider, company, refresh_token):
        at_hq.set()
        assert release.wait(5)
        return books_link._tokens(xero_tokens("acc-3", "ref-3"))

    monkeypatch.setattr(books_link, "refresh", slow_refresh)
    done: dict[str, Any] = {}

    def refresh_now():
        done["access"] = books_link.access_token(books, vault, LINK, cid, "xero", now=LATER, force=True)

    def replace_now():
        try:
            _claimed_xero(books, vault)
        except books_link.AlreadyConnected as refused:
            done["replacement"] = refused.code

    refresher = threading.Thread(target=refresh_now)
    refresher.start()
    assert at_hq.wait(5)
    replacer = threading.Thread(target=replace_now)
    replacer.start()
    replacer.join(0.3)
    assert replacer.is_alive()                  # the replacement waits for the refresh that holds the connection
    with books.read() as c:
        assert T.credential_secret(c, vault, str(SX.books_settings(c, cid)["refresh"])) == "ref-1"
    release.set()
    refresher.join(5)
    replacer.join(5)
    assert done == {"access": "acc-3", "replacement": "already_connected"}
    with books.read() as c:
        assert T.credential_secret(c, vault, str(SX.books_settings(c, cid)["refresh"])) == "ref-2"   # the replacement is last
        assert SX.books_status(c, cid) == "active"


FIN = "hr_00000000000000f1"        # HQ's system relay for `_finance`, as HQ lists it
OTHER = "hr_00000000000000e2"      # a relay HQ lists for `_finance` without `system`: not the install's
FIN_RELAY = {"id": FIN, "trigger_id": "_finance", "key": "hq", "system": True}


def _books_api(api: Any, provider: str = "quickbooks", cid: str = "conn-books") -> str:
    """A books connection made directly in the server's store, with its trigger, as connect_claim leaves it."""
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        SX.books_connection_insert(c, cid=cid, provider=provider, name="Acme books", by=OWNER, at=AT)
        SX.set_books_settings(c, cid, {"provider": provider, "company_id": COMPANY, "realm_id": "123"})
        SX.set_connection_status(c, cid, "active", "", AT)
        books_link._keep_trigger(c, cid, provider=provider, by=OWNER, now=AT)
    return cid


def _envelope(provider: str = "quickbooks", company: str = COMPANY, event: str = "evt-9") -> bytes:
    """HQ's envelope for one provider call, as its `_finance` relay delivers it."""
    payload: dict[str, Any] = ({"eventNotifications": [{"realmId": "123"}]} if provider == "quickbooks"
                               else {"events": [{"resourceId": "res-1"}]})
    return json.dumps({"type": "tico.finance.webhook", "provider": provider, "company_id": company, "external_id": event,
                       "payload": payload}).encode()


def _fin_inbox(api: Any) -> int:
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM fin_inbox").fetchone()[0]


def test_connecting_makes_the_books_trigger_that_takes_intuit_or_xero_calls(books, monkeypatch):
    monkeypatch.setattr(books_link, "_call", HQ(claim=[tokens("acc-1", "ref-1")]))
    cid = _claimed(books, T._VAULT[0])
    with books.read() as c:
        row = c.execute("SELECT preset, consumer, deleted_at FROM triggers WHERE id=?", (cid,)).fetchone()
    assert row is not None and tuple(row) == ("intuit", "finance", None)


def test_the_books_pass_makes_a_missing_trigger_and_asks_hq_for_nothing(books, monkeypatch):
    vault = T._VAULT[0]
    monkeypatch.setattr(books_link, "_call", HQ(claim=[tokens("acc-1", "ref-1")]))
    cid = _claimed(books, vault)
    with books.transaction() as c:
        c.execute("UPDATE triggers SET deleted_at=? WHERE id=?", (AT, cid))
    monkeypatch.setattr(hook_relay.Link, "call", lambda *a, **k: pytest.fail("the books pass asked HQ"))
    books_link.ensure_triggers(books, now=AT)
    books_link.ensure_triggers(books, now=AT)
    with books.read() as c:
        assert c.execute("SELECT deleted_at FROM triggers WHERE id=?", (cid,)).fetchone()[0] is None


@pytest.mark.parametrize("provider", ["quickbooks", "xero"])
def test_a_finance_call_from_the_recorded_system_relay_is_kept_on_its_books_connection(api, hq, provider):
    _books_api(api, provider=provider)
    hq.relays[FIN] = FIN_RELAY
    hq.deliver(FIN, "d-fin-1", body=_envelope(provider=provider))
    service(api).tick()
    assert _fin_inbox(api) == 1
    assert hq.acks == [1]


def test_a_finance_call_from_a_relay_other_than_the_recorded_one_is_acked_and_not_kept(api, hq):
    _books_api(api)
    hq.relays[FIN] = FIN_RELAY
    hq.relays[OTHER] = {"id": OTHER, "trigger_id": "_finance", "key": "other", "system": False}
    hq.deliver(OTHER, "d-other-1", body=_envelope())
    service(api).tick()
    assert _fin_inbox(api) == 0
    assert hq.acks == [1]


def test_a_finance_call_for_a_company_with_no_books_connection_is_acked_and_recorded_as_unmatched(api, hq):
    _books_api(api)
    hq.relays[FIN] = FIN_RELAY
    hq.deliver(FIN, "d-fin-2", body=_envelope(company="2e5b0c1d-0000-4000-8000-000000000000"))
    service(api).tick()
    assert _fin_inbox(api) == 0
    assert hq.acks == [1]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='finance.webhook_unmatched'").fetchone()[0] == 1


def test_disconnecting_takes_the_connections_trigger_with_it(books, monkeypatch):
    vault = T._VAULT[0]
    hq = HQ(claim=[tokens("acc-1", "ref-1")], disconnect=[books_link.ReconnectRequired("reconnect_required")])
    monkeypatch.setattr(books_link, "_call", hq)
    cid = _claimed(books, vault)
    books_link.drop(books, vault, LINK, cid, by=OWNER)
    with books.read() as c:
        assert c.execute("SELECT deleted_at IS NOT NULL FROM triggers WHERE id=?", (cid,)).fetchone()[0] == 1
