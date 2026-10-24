"""Settings > Computers' hosted servers (backend/company_billing.py, paused-start d8f8efaa): owners and admins only, read
and started at Tico HQ with the install's gateway key; a start HQ refuses while the company is paused for credit says
so in plain words with its account page; an install HQ hosts nothing for shows no section. HQ is a fake opener."""

from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_company_billing import KEY, URL, hq  # noqa: F401

TICO, BOTS = "pv-" + "a" * 16, "pv-" + "b" * 16
LISTED = {"servers": [{"server_id": TICO, "kind": "tico", "power": "on"},
                      {"server_id": BOTS, "kind": "bot_computer", "power": "stopped"}], "deactivated": False}
PAUSED = "Paused: out of credit. Add credit to start this computer."


def test_owner_and_admins_see_the_hosted_servers_and_members_are_refused(api, hq):  # noqa: F811
    hq.answers["/servers"] = (200, LISTED, "application/json")
    assert get(api, "hosted-servers") == {"state": "ok", "servers": LISTED["servers"]}
    assert hq.calls == [(URL + "/servers", "Bearer " + KEY)]
    assert get(api, "hosted-servers", "ben-test")["state"] == "ok"                     # an admin
    r = api.get("/api/v2/hosted-servers", headers={"Authorization": "Bearer cara-test"})   # a member
    assert r.status_code == 403
    r = api.post("/api/v2/hosted-servers/%s/start" % BOTS, headers={"Authorization": "Bearer cara-test"})
    assert r.status_code == 403 and len(hq.calls) == 2                                  # a refusal never asks HQ


def test_a_start_while_paused_for_credit_says_so_with_the_account_page(api, hq):  # noqa: F811
    hq.answers["/servers/%s/start" % BOTS] = (409, {"error": "paused", "detail": "anything HQ says",
                                                    "credit_url": "https://app.tico.dev"}, "application/json")
    assert post(api, "hosted-servers/%s/start" % BOTS, {}) == {
        "state": "paused", "message": PAUSED, "credit_url": "https://app.tico.dev"}
    hq.answers["/servers/%s/start" % BOTS] = (409, {"error": "paused", "credit_url": "javascript:alert(1)"},
                                              "application/json")
    assert post(api, "hosted-servers/%s/start" % BOTS, {})["credit_url"] is None
    hq.answers["/servers/%s/start" % BOTS] = (200, {"machine_id": BOTS, "power": "on"}, "application/json")
    assert post(api, "hosted-servers/%s/start" % BOTS, {}) == {"state": "started"}
    assert hq.calls[-1] == (URL + "/servers/%s/start" % BOTS, "Bearer " + KEY)


def test_a_shut_down_company_another_server_and_nothing_hosted(api, hq, monkeypatch):  # noqa: F811
    hq.answers["/servers/%s/start" % TICO] = (409, {"error": "deactivated"}, "application/json")
    assert post(api, "hosted-servers/%s/start" % TICO, {}) == {"state": "deactivated"}
    assert post(api, "hosted-servers/%s/start" % BOTS, {}) == {"state": "not_found"}  # the fake's 404 not_found
    hq.answers["/servers"] = (200, {"servers": [], "deactivated": True}, "application/json")
    assert get(api, "hosted-servers") == {"state": "deactivated"}
    hq.answers["/servers"] = (404, {"error": "not_configured"}, "application/json")
    assert get(api, "hosted-servers") == {"state": "none"}
    hq.answers["/servers"] = (200, {"servers": [], "deactivated": False}, "application/json")
    assert get(api, "hosted-servers") == {"state": "none"}
    hq.answers["/servers"] = (200, {"servers": [{"server_id": "../key", "kind": "tico", "power": "on"}]},
                              "application/json")
    assert get(api, "hosted-servers") == {"state": "none"}                            # nothing odd reaches the page
    monkeypatch.delenv("TICO_GATEWAY_KEY")
    calls = len(hq.calls)
    assert get(api, "hosted-servers") == {"state": "no_gateway"} and len(hq.calls) == calls


def test_a_server_id_that_is_not_one_never_reaches_hq(api, hq):  # noqa: F811
    for server_id in ("pv-nope", "..%2Fbilling", "pv-" + "a" * 17):
        r = api.post("/api/v2/hosted-servers/%s/start" % server_id, headers={"Authorization": "Bearer ana-test"})
        assert r.status_code in (404, 422), (server_id, r.status_code)
    assert hq.calls == []


def test_a_rehearsal_never_starts_a_real_server(api, hq):  # noqa: F811
    # A rehearsal is a copy of real data, even with the real gateway key in its environment: Start sends nothing.
    api.app.state.store.settings.rehearsal = True
    r = api.post("/api/v2/hosted-servers/%s/start" % BOTS, headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "rehearsal"
    assert hq.calls == []
