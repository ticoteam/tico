"""Growth segments (backend/growth_segments.py) and their figures (backend/growth_computed.py): direct and loaded CAC,
churn windows, observed LTV and the health counts. Synthetic customers, read on 2026-11-13.

The team: Consumer (revenue 20 a month at 50% margin) and Rental (churn waits 30 days of inactivity; cohorts of 10 are
enough). Search ads' spend is for Consumer, partners' for Rental, brand's is company-wide."""

from datetime import date, timedelta

import pytest

from backend import growth_computed as GX
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.growth_computed import HEADER, TODAY, as_csv, customer

FULL = HEADER + ["net_d90", "net_d180", "net_d365", "attribution"]


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def cohort(result, value, period):
    return next(r for r in result["cohorts"] if r["value"] == value and r["period"] == period)


def people(start, n, first, attribution="", **more):
    """`n` customers from key `start`, one a day from `first` (a date)."""
    return [
        {**customer(start + i, (first + timedelta(days=i % 28)).isoformat(), **more), "attribution": attribution}
        for i in range(n)
    ]


def team(api):
    post(api, "growth/segments", {"slug": "consumer", "name": "Consumer", "revenue_month": 20, "margin_pct": 50})
    post(
        api,
        "growth/segments",
        {"slug": "rental", "name": "Rental", "churn_days": 30, "min_cohort": 10, "customer_rule": "a booked stay"},
    )
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads", "segment": "consumer"})
    post(api, "growth/channels", {"slug": "partners", "name": "Partners", "segment": "Rental"})
    post(api, "growth/channels", {"slug": "brand", "name": "Brand"})
    for ch, spend in (("search-ads", 1200), ("partners", 400), ("brand", 700)):
        put(api, "growth/cohorts", {"channel": ch, "month": "2026-09", "spend": spend})


def september():
    """40 Consumer (30 from search ads, 10 from brand, labels in either case), 20 Rental (2 of them from search ads,
    Consumer's channel) and 10 in no segment (a label naming none, or blank)."""
    sep = date(2026, 9, 1)
    rows = people(0, 15, sep, channel="search-ads", segment="consumer")
    rows += people(100, 15, sep, channel="search-ads", segment="Consumer")
    rows += people(200, 10, sep, channel="brand", segment="CONSUMER")
    rows += people(300, 18, sep, channel="partners", segment="rental")
    rows += people(400, 2, sep, channel="search-ads", segment="Rental")
    rows += people(500, 5, sep, channel="brand", segment="hotels")
    rows += people(600, 5, sep, channel="brand")
    return rows


def load(api, rows, expected=200):
    return post(api, "growth/customers", {"csv": as_csv(rows, FULL)}, expected=expected)


# ----------------------------------------------------------------------------- the segments themselves
def test_the_owner_and_admins_keep_segments_and_everyone_reads_them(api):
    team(api)
    listed = get(api, "growth/segments", token="cara-test")
    assert listed["may_edit"] is False
    assert [(s["slug"], s["channels"]) for s in listed["segments"]] == [
        ("consumer", ["search-ads"]),
        ("rental", ["partners"]),
    ]
    assert get(api, "growth/segments")["may_edit"] is True
    post(api, "growth/segments", {"slug": "b2b", "name": "B2B"}, token="cara-test", expected=403)
    patch(api, "growth/segments/rental", {"churn_days": 45}, token="cara-test", expected=403)
    post(api, "growth/segments/rental/retire", {}, token="cara-test", expected=403)
    assert patch(api, "growth/segments/rental", {"churn_days": 45}, token="ben-test")["churn_days"] == 45
    post(api, "growth/segments", {"slug": "rental", "name": "Other"}, expected=409)
    post(api, "growth/segments", {"slug": "rentals", "name": "RENTAL"}, expected=409)
    post(api, "growth/segments", {"slug": "none", "name": "Nobody"}, expected=422)
    patch(api, "growth/segments/rental", {"name": None}, expected=422)

    retired = post(api, "growth/segments/rental/retire", {})
    assert retired["retired_at"] and [s["slug"] for s in get(api, "growth/segments")["segments"]] == ["consumer"]
    assert len(get(api, "growth/segments?include_retired=true")["segments"]) == 2
    post(api, "growth/channels", {"slug": "events", "name": "Events", "segment": "rental"}, expected=404)


def test_a_channel_is_given_a_segment_by_slug_or_name_and_cleared_to_company_wide(api):
    team(api)
    assert get(api, "growth/channels/brand")["segment"] is None
    patch(api, "growth/channels/brand", {"segment": "CONSUMER"})
    assert get(api, "growth/channels/brand")["segment"] == "consumer"
    patch(api, "growth/channels/brand", {"segment": None})
    assert get(api, "growth/channels/brand")["segment"] is None
    patch(api, "growth/channels/brand", {"segment": "nobody"}, expected=404)


# ----------------------------------------------------------------------------- the figures
def test_direct_and_loaded_cac_split_company_spend_by_each_segments_new_customers(api):
    team(api)
    load(api, september())
    result = get(api, "growth/computed?by=segment&month=2026-09")
    assert result["cac_note"] is None
    consumer, rental, none = (cohort(result, v, "2026-09") for v in ("consumer", "rental", "none"))
    assert [r["new_customers"]["value"] for r in (consumer, rental, none)] == [40, 20, 10]
    # Consumer: 1,200 of search ads over 40; loaded adds brand's 700 x 40/60 (No segment takes no share).
    assert consumer["cac"]["value"] == 30.0 and consumer["cac_loaded"]["value"] == 41.67
    assert rental["cac"]["value"] == 20.0 and rental["cac_loaded"]["value"] == 31.67
    assert none["cac"]["status"] == none["cac_loaded"]["status"] == "no_spend"
    assert result["health"] == {"no_segment": 10, "mismatch": 2}
    # The segment's revenue when nothing nearer has one; payback is on direct CAC (30 / (20 x 50%)).
    assert consumer["revenue_month"]["value"] == 20.0 and consumer["payback_months"]["value"] == 3.0
    # Rental's own minimum: 20 is enough.
    assert rental["too_few"] is False and none["too_few"] is True
    # A filter by name keeps the segment, and the loaded share still counts every segment's customers.
    one = get(api, "growth/computed?by=segment&month=2026-09&segment=Rental")
    assert [r["value"] for r in one["cohorts"]] == ["rental"] and one["cohorts"][0]["cac_loaded"]["value"] == 31.67
    # By plan, spend is still not split.
    assert get(api, "growth/computed?by=plan&month=2026-09")["cac_note"] == "spend is not split by plan"


def test_churn_days_delays_every_kept_window_of_the_segment(api):
    team(api)
    load(api, september())
    result = get(api, "growth/computed?by=segment&month=2026-09")
    consumer, rental = cohort(result, "consumer", "2026-09"), cohort(result, "rental", "2026-09")
    assert consumer["kept_d30"]["status"] == "ok" and "churn_days" not in consumer
    assert rental["churn_days"] == 30
    assert rental["kept_d30"]["status"] == "not_yet" and rental["kept_d30"]["fills_on"] == "2026-11-29"


def test_observed_ltv_is_the_mean_net_revenue_once_the_customers_are_that_old(api):
    team(api)
    june = date(2026, 6, 1)
    rows = people(0, 40, june, channel="search-ads", segment="consumer", attribution="utm")
    for row in rows:
        row["net_d90"] = 60 if int(row["customer_key"][4:]) % 2 else 40
    rental = people(100, 12, june, channel="partners", segment="rental", attribution="code")
    for row in rental[:6]:
        row["net_d90"] = -5  # refunds
    load(api, rows + rental + people(200, 10, june, channel="brand", segment="consumer", attribution="utm"))
    result = get(api, "growth/computed?by=segment&month=2026-06")
    consumer, rental = cohort(result, "consumer", "2026-06"), cohort(result, "rental", "2026-06")
    assert consumer["ltv_observed_d90"] == {
        "value": 50.0,
        "status": "ok",
        "origin": "computed",
        "count": 40,
        "denominator": 50,
    }
    assert consumer["ltv_observed_d180"]["status"] == "not_yet"
    assert consumer["ltv_observed_d180"]["fills_on"] == "2026-11-28"
    assert rental["ltv_observed_d90"]["status"] == "too_few" and rental["ltv_observed_d90"]["count"] == 6
    assert consumer["attribution"] == {"utm": 50} and rental["attribution"] == {"code": 12}
    # Without net values anywhere, there is no observed figure at all.
    load(api, september())
    assert "ltv_observed_d90" not in cohort(get(api, "growth/computed?by=segment&month=2026-09"), "consumer", "2026-09")


def test_a_bad_net_value_is_refused_by_column(api):
    rows = people(0, 5, date(2026, 9, 1), segment="consumer")
    rows[2]["net_d180"] = "lots"
    refused = load(api, rows, expected=422)["error"]
    assert refused["rule"] == "net" and "net d180" in refused["detail"] and "lots" not in refused["detail"]


def test_the_segment_read_has_direct_and_loaded_cac_its_rule_and_its_channels(api):
    team(api)
    load(api, september())
    read = get(api, "growth/segments/rental")
    assert read["segment"] == "rental" and read["name"] == "Rental" and read["customer_rule"] == "a booked stay"
    assert read["churn_days"] == 30 and read["channels"] == ["partners"] and read["min_cohort"] == 10
    month = read["months"][0]
    assert month["month"] == "2026-09" and month["cac"] == 20.0 and month["cac_loaded"] == 31.67
    assert read["health"] == {"no_segment": 10, "mismatch": 2}  # the team's, whatever the segment
    assert get(api, "growth/segments/none")["name"] == "No segment"


def test_a_team_without_segments_reads_its_labels_as_before(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "spend": 4000})
    rows = people(0, 40, date(2026, 9, 1), channel="search-ads", segment="agency")
    rows += people(100, 40, date(2026, 9, 1), channel="search-ads", segment="studio")
    load(api, rows)
    result = get(api, "growth/computed?by=segment&month=2026-09")
    assert result["cac_note"] == "spend is not split by segment" and "health" not in result
    agency = cohort(result, "agency", "2026-09")
    assert agency["cac"]["status"] == "not_split" and "cac_loaded" not in agency
    assert get(api, "growth/segments/agency")["spend"] == "spend is not split by segment"
