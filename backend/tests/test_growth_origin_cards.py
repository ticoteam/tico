"""Each Growth number card says where its figure came from (backend/cards.py growth_origin, task #309, spec AC-9, AC-10,
AC-12): the synced source with its newest sync and health, who typed the rest, the inputs a worked-out figure is made
from, and a number a source disagrees with, with who may choose. Acme, a home-cleaning company, syncs spend from a sheet
it exports each week and types new customers by hand."""

from backend import growth_sync as GS
from backend.tests.helpers.api import headers, post, put
from backend.tests.helpers.company import bot

M = GS._month(GS._index(GS.this_month()) - 1)


def growth(api, query="", token="ana-test"):
    r = api.get("/api/v2/places/growth" + query, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def card(view, metric):
    return next(p["card"]["data"] for p in view["pins"] if p["card"]["settings"].get("metric") == metric)


def acme(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": M, "new_customers": 120, "quality": "actual"})
    put(api, "growth/cohorts", {"channel": "referrals", "month": M, "new_customers": 12, "quality": "estimate"})
    ops = bot(api, "ops")
    sheet = post(
        api,
        "growth/sources",
        {
            "truth_for": "spend",
            "system": "Spend sheet",
            "sync_kind": "file",
            "owner_ref": "ops",
            "sync_map": {"fields": {"spend": "spend", "new_customers": "signups"}},
        },
    )
    text = f"month,channel,spend,signups\n{M},search-ads,4210,131\n{M},referrals,300,\n"
    result = post(api, f"growth/sources/{sheet['id']}/rows", {"csv": text})
    assert result["ok"] and result["held"] == 1  # 120 typed as actual is held beside the sheet's 131
    return sheet, ops


def test_a_synced_number_names_its_source_and_a_typed_one_who_typed_it(api):
    """AC-10: spend names the sheet, its sync time and health; new customers were typed (one an estimate)."""
    sheet, _ = acme(api)
    view = growth(api, f"?month={M}")
    spend = card(view, "spend")["origin"]
    assert spend["fields"] == [] and spend["typed"] is None and spend["conflict_count"] == 0
    assert [(s["source"], s["health"]) for s in spend["synced"]] == [("Spend sheet", "fresh")]
    assert spend["synced"][0]["source_id"] == sheet["id"] and spend["synced"][0]["at"]
    new = card(view, "new_customers")["origin"]
    assert new["synced"] == [] and new["typed"]["count"] == 2 and new["typed"]["quality"] == "estimate"
    assert new["typed"]["by"] and new["typed"]["at"]


def test_a_worked_out_figure_names_its_inputs_and_their_sources(api):
    """CAC is spend and bot cost over new customers: its line names those inputs, the sheet and the typed counts."""
    acme(api)
    cac = card(growth(api, f"?month={M}"), "cac")["origin"]
    assert cac["fields"] == ["spend", "bot_cost", "new_customers"]
    assert [s["source"] for s in cac["synced"]] == ["Spend sheet"] and cac["typed"]["count"] == 2


def test_a_conflict_shows_both_values_and_only_the_sources_owner_or_an_admin_may_choose(api):
    """AC-12: 120 typed, the sheet says 131. The card lists both; an admin and the sheet's owner may choose, a member
    may not; once chosen, the card has no conflict left."""
    _, ops = acme(api)
    new = card(growth(api, f"?month={M}"), "new_customers")["origin"]
    assert new["conflict_count"] == 1
    assert new["conflicts"] == [
        {
            "channel": "search-ads",
            "channel_name": "Search Ads",
            "month": M,
            "field": "new_customers",
            "typed": 120,
            "value": 131,
            "source": "Spend sheet",
            "may": True,
        }
    ]
    assert card(growth(api, f"?month={M}", token=ops), "new_customers")["origin"]["conflicts"][0]["may"] is True
    assert (
        card(growth(api, f"?month={M}", token="cara-test"), "new_customers")["origin"]["conflicts"][0]["may"] is False
    )
    body = {"channel": "search-ads", "month": M, "field": "new_customers", "keep": "source"}
    post(api, "growth/cohorts/conflicts", body)
    new = card(growth(api, f"?month={M}"), "new_customers")["origin"]
    assert new["conflict_count"] == 0 and [s["source"] for s in new["synced"]] == ["Spend sheet"]


def test_a_stale_or_failing_source_marks_the_numbers_it_fills(api):
    """AC-9: the sheet's run fails; spend still shows the sheet's number, now marked failing since then. Retired, the
    sheet is named with no health."""
    sheet, _ = acme(api)
    post(api, f"growth/sources/{sheet['id']}/rows", {"error": "file has no spend column"})
    spend = card(growth(api, f"?month={M}"), "spend")
    assert spend["value"] == 4510 and spend["origin"]["synced"][0]["health"] == "failing"
    assert spend["origin"]["synced"][0]["since"]
    post(api, f"growth/sources/{sheet['id']}/retire", {})
    assert card(growth(api, f"?month={M}"), "spend")["origin"]["synced"][0]["health"] is None


def test_a_number_typed_before_origins_were_kept_counts_as_typed(api):
    """Old typed rows stay as they are, marked typed; with no number at all the card has no origin line."""
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": M, "spend": 900})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM growth_origins")
    view = growth(api, f"?month={M}")
    assert card(view, "spend")["origin"]["typed"] == {"count": 1, "by": None, "at": None, "quality": "actual"}
    assert card(view, "kept_d30")["origin"] is None
