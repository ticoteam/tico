"""Customer facts from a synced source (task #366, spec page growth-computed-cohorts v2, AC-3 and AC-16b for sources).

A synced source with the "customers" shape posts customer rows instead of numbers: each run is a load of
backend/growth_customers.py under the source's own id, so it replaces only that source's customers. Acme exports its
paying customers from its billing system each week; synthetic customers only."""

import pytest

from backend import growth_computed as GX
from backend import growth_customers as GC
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.company import bot
from backend.tests.test_growth_computed import TODAY, as_csv, customer, september


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)


def connect(api, token="ana-test", expected=200, system="Billing export", **more):
    body = {
        "truth_for": "customers",
        "system": system,
        "sync_kind": "file",
        "sync_map": {"shape": "customers"},
        **more,
    }
    return post(api, "growth/sources", body, token=token, expected=expected)


def push(api, sid, rows, token="ana-test", expected=200):
    return post(api, f"growth/sources/{sid}/rows", {"csv": as_csv(rows)}, token=token, expected=expected)


def stored(api, source=None):
    with api.app.state.store.read() as c:
        where, args = (" WHERE source=?", (source,)) if source else ("", ())
        return c.execute(f"SELECT count(*) FROM growth_customers{where}", args).fetchone()[0]


def kept_d30(api):
    return get(api, "growth/cohorts?channel=search-ads&month=2026-09")["cohorts"][0]


def test_a_customers_source_run_loads_its_customers_and_the_figures_compute_from_them(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    src = connect(api)
    assert (src["kind"], src["fills"], src["map"]) == ("file", [], {"shape": "customers"})
    result = push(api, src["id"], september())
    assert result["ok"] and result["written"] == 40 and result["error"] is None
    assert stored(api, GC.source_key(src["id"])) == 40
    row = kept_d30(api)
    assert row["kept_d30"] == 75.0 and row["computed_from"] == 40
    assert get(api, "growth/sources")["sources"][0]["health"] == "fresh"


def test_each_source_replaces_only_its_own_customers(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    billing = connect(api)
    crm = connect(api, system="CRM export")  # a customers source fills no typed number: two may be live
    post(api, "growth/customers", {"csv": as_csv([customer(1, "2026-09-02")])})
    push(api, billing["id"], september())
    push(api, crm["id"], [customer(7, "2026-09-03"), customer(8, "2026-09-04")])
    push(api, billing["id"], september()[:10])
    assert (
        stored(api, GC.UPLOAD),
        stored(api, GC.source_key(billing["id"])),
        stored(api, GC.source_key(crm["id"])),
    ) == (
        1,
        10,
        2,
    )


def test_a_refused_load_fails_the_run_naming_the_rule_and_changes_nothing(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    src = connect(api)
    push(api, src["id"], september())
    bad = [{**customer(1, "2026-09-02"), "customer_key": "ana@acme.example"}]
    result = push(api, src["id"], bad)
    assert not result["ok"] and result["written"] == 0
    assert "acme.example" not in result["error"] and "customer key" in result["error"]
    assert stored(api, GC.source_key(src["id"])) == 40
    assert get(api, "growth/sources")["sources"][0]["health"] == "failing"


def test_only_the_owner_and_admins_connect_a_customers_source(api):  # AC-3 for sources
    connect(api, token="cara-test", expected=403)
    connect(api, token=bot(api, "ops"), expected=403)
    assert connect(api, token="ben-test")["map"] == {"shape": "customers"}


def test_a_customers_source_maps_no_numbers(api):
    body = {"shape": "customers", "fields": {"spend": "cost"}}
    r = post(
        api, "growth/sources", {"truth_for": "c", "system": "S", "sync_kind": "file", "sync_map": body}, expected=422
    )
    assert "customers" in r["error"]["detail"]


def test_retiring_a_customers_source_deletes_its_facts_and_its_file_at_once(api):  # AC-16b for sources
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    src = connect(api)
    other = connect(api, system="CRM export")
    push(api, src["id"], september())
    push(api, other["id"], [customer(7, "2026-09-03")])
    post(api, f"growth/sources/{src['id']}/retire", {})
    assert stored(api, GC.source_key(src["id"])) == 0 and stored(api, GC.source_key(other["id"])) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT last_payload FROM gtm_sources WHERE id=?", (src["id"],)).fetchone()[0] is None
    assert "computed_from" in kept_d30(api) and kept_d30(api)["computed_from"] == 1


def test_disconnecting_or_reshaping_a_customers_source_deletes_its_facts(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    src = connect(api)
    push(api, src["id"], september())
    r = api.patch(f"/api/v2/growth/sources/{src['id']}", json={"sync_kind": None}, headers=headers("ana-test"))
    assert r.status_code == 200 and stored(api) == 0
    again = connect(api, system="Billing export 2")
    push(api, again["id"], september())
    r = api.patch(
        f"/api/v2/growth/sources/{again['id']}",
        json={"sync_map": {"fields": {"new_customers": "n"}}},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200 and stored(api) == 0
