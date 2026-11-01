"""Connections (backend/connections.py): one list of every connection, filtered per reader. The owner and admins see
every row; a member sees their own and the company's rows they may read; the list is paged and byte-capped.

Ana owns the company, Ben is an admin (a member after `as_member`), Cara is a member."""

from backend import connections
from backend.store import H
from backend.tests.test_api import api, as_member, get, put, setup_attempt  # noqa: F401  (fixture)
from backend.tests.test_inbound_audience import narrow


def seed(api):
    narrow(api)                                            # ibx_a, ibx_b, ibx_c; Cara is in East, under Sales
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "sales"})
    put(api, "inbound/inboxes/ibx_c/audience", {"audience": "team", "group_id": "ops-team"})
    now = H.now()
    with api.app.state.store.transaction() as c:
        for wid, who in (("wh-ben", "human:ben"), ("wh-cara", "human:cara")):
            c.execute("INSERT INTO webhooks(id,name,url,host,created_by,created,updated) VALUES(?,?,?,?,?,?,?)",
                      (wid, "Hook " + wid, "https://hooks.acme.example/x", "hooks.acme.example", who, now, now))
        c.execute("INSERT INTO triggers(id,name,secret_hash,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  ("tr-1", "Orders", "x", "human:ana", now, now))


def ids(api, token):
    return {r["id"] for r in get(api, "connections?limit=100", token)["connections"]}


def test_admins_see_every_row_and_members_their_own_and_what_they_may_read(api):
    seed(api)
    everything = {"inbox:ibx_a", "inbox:ibx_b", "inbox:ibx_c", "webhook:wh-ben", "webhook:wh-cara", "trigger:tr-1"}
    assert everything <= ids(api, "ana-test")
    assert everything <= ids(api, "ben-test")              # an admin
    cara = ids(api, "cara-test")
    assert {"inbox:ibx_a", "inbox:ibx_b", "webhook:wh-cara", "trigger:tr-1"} <= cara
    assert not cara & {"inbox:ibx_c", "webhook:wh-ben"}    # another group's inbox, another person's webhook
    assert "slack" not in cara and "github" not in cara    # not connected: only admins see them, to connect
    assert {"slack", "github"} <= ids(api, "ana-test")
    as_member(api, "ben@acme.example")
    ben = ids(api, "ben-test")
    assert "webhook:wh-ben" in ben and not ben & {"inbox:ibx_c", "webhook:wh-cara"}


def test_a_row_says_whose_it_is_who_reads_it_and_where_to_change_it(api):
    seed(api)
    rows = {r["id"]: r for r in get(api, "connections", "cara-test")["connections"]}
    team = rows["inbox:ibx_b"]
    assert team["scope"] == "team" and team["readers"] == "Sales" and team["configure"] == "#/settings/inbound"
    assert team["can_configure"] is False
    assert rows["inbox:ibx_a"]["scope"] == "company" and rows["inbox:ibx_a"]["readers"] == "Everyone"
    assert rows["webhook:wh-cara"]["scope"] == "person" and rows["webhook:wh-cara"]["can_configure"] is True
    assert set(rows["trigger:tr-1"]) == {"id", "kind", "name", "detail", "status", "scope", "readers", "bots",
                                        "more_bots", "configure", "can_configure"}


def test_bots_and_agents_see_only_company_rows_they_may_read(api):
    seed(api)
    attempt = setup_attempt(api)[2]
    rows = ids(api, attempt["token"])
    assert "trigger:tr-1" in rows and "inbox:ibx_a" in rows
    assert not rows & {"inbox:ibx_b", "inbox:ibx_c", "webhook:wh-ben", "webhook:wh-cara"}


def test_the_list_is_paged_and_byte_capped(api, monkeypatch):
    seed(api)
    first = get(api, "connections?limit=2")
    assert len(first["connections"]) == 2 and first["next"] == "2"
    second = get(api, "connections?limit=2&cursor=" + first["next"])
    assert not {r["id"] for r in first["connections"]} & {r["id"] for r in second["connections"]}
    monkeypatch.setattr(connections, "BYTES_MAX", 1)
    capped = get(api, "connections?limit=100")
    assert len(capped["connections"]) == 1 and capped["next"] == "1"
    get(api, "connections?cursor=abc", expected=422)
