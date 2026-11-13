"""The Growth page's cards on computed retention (backend/cards.py, backend/growth_computed.py, spec page
growth-computed-cohorts v2, section 3 "Where it shows"): the Retention card carries the picked month's computed cohorts
for the web's Cut by and Weekly / Monthly switches; the Number cards and
the Channels table say whether each figure is computed or typed. Synthetic customers only, read on 2026-11-13."""

import csv
import io
from datetime import date, timedelta

import pytest

from backend import growth_computed as GX
from backend.tests.helpers.api import get, headers, post, put

TODAY = date(2026, 11, 13)
HEADER = ["customer_key", "became_customer", "channel", "segment", "churned"]


def customer(n, became, channel="search-ads", churned="", segment=""):
    return {
        "customer_key": f"cus_{n}",
        "became_customer": became,
        "channel": channel,
        "churned": churned,
        "segment": segment,
    }


def september(kept=30, total=40, segment=""):
    """search-ads' September 2026: `total` customers, the first `kept` still paying on day 30, the rest churned on day 10."""
    out = []
    for n in range(total):
        became = date(2026, 9, 1) + timedelta(days=n % 30)
        churned = "" if n < kept else (became + timedelta(days=10)).isoformat()
        out.append(customer(1000 + n, became.isoformat(), churned=churned, segment=segment))
    return out


def load(api, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)
    writer.writerows([row[h] for h in HEADER] for row in rows)
    return post(api, "growth/customers", {"csv": out.getvalue()})


def channel(api, slug="search-ads", name="Search ads"):
    post(api, "growth/channels", {"slug": slug, "name": name})


def growth(api, query=""):
    r = api.get("/api/v2/places/growth" + query, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)


def card(view, template, metric=None):
    return next(
        p["card"]["data"]
        for p in view["pins"]
        if p["card"]["template"] == template and (metric is None or p["card"]["settings"].get("metric") == metric)
    )


def test_the_retention_card_carries_the_picked_months_computed_cohorts(api):  # AC-8, AC-10 on the web
    channel(api)
    load(api, september(segment="agency"))
    retention = card(growth(api, "?month=2026-09"), "growth_retention")
    computed = retention["computed"]
    assert (computed["by"], computed["grain"], computed["month"]) == ("channel", "month", "2026-09")
    assert computed["computed_from"] == 40 and computed["min_cohort"] == 30
    search = next(r for r in computed["cohorts"] if r["value"] == "search-ads")
    assert search["kept_d30"]["value"] == 75.0 and search["kept_d90"]["status"] == "not_yet"
    assert search["kept_d90"]["fills_on"] == "2026-12-29"
    # "loaded <time> from <source>": the newest load, counts only.
    assert computed["loads"][0]["source"] == "upload" and computed["loads"][0]["customers"] == 40
    assert "customers" not in search["kept_d30"]  # the card never carries keys: the trace is its own read


def test_typed_retention_stays_on_the_card_beside_customer_facts(api):  # AC-15 on the web (Review 1)
    channel(api)
    channel(api, "referrals", "Referrals")
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-09", "new_customers": 25, "kept_d30": 70})
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-08", "new_customers": 9, "kept_d30": 60})
    load(api, september())
    # A month with facts for one channel: the other channel's typed month comes with the computed cohorts, marked typed.
    sept = card(growth(api, "?month=2026-09"), "growth_retention")
    assert [r["value"] for r in sept["computed"]["cohorts"]] == ["search-ads"]
    assert sept["computed"]["typed"] == [
        {"value": "referrals", "new_customers": 25, "kept": [70, None, None], "origin": "typed"}
    ]
    # A month with no facts at all: the typed table, as before facts were loaded.
    aug = card(growth(api, "?month=2026-08"), "growth_retention")
    assert "computed" not in aug and aug["rows"][-1] == {
        "month": "2026-08",
        "new_customers": 9,
        "kept": [60, None, None],
    }


def test_a_team_with_only_typed_rows_keeps_its_retention_card_as_before(api):  # AC-15 on the web
    channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "new_customers": 40, "kept_d30": 70})
    retention = card(growth(api), "growth_retention")
    assert "computed" not in retention and retention["rows"][-1]["kept"] == [70, None, None]


def test_the_retention_card_shows_computed_cohorts_with_no_typed_retention(api):
    channel(api)
    load(api, september())
    retention = card(growth(api), "growth_retention")
    assert retention is not None and retention["computed"]["month"] == "2026-09"


def test_number_cards_and_the_channels_table_say_computed_or_typed(api):
    channel(api)
    channel(api, "referrals", "Referrals")
    load(api, september())
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-10", "new_customers": 12, "spend": 600})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "spend": 4000})
    sept, octo = growth(api, "?month=2026-09"), growth(api, "?month=2026-10")
    assert card(sept, "growth_number", "new_customers")["basis"] == "computed"
    assert card(octo, "growth_number", "new_customers")["basis"] == "typed"
    assert card(sept, "growth_number", "spend")["basis"] == "typed"
    assert card(sept, "growth_number", "cac")["basis"] == "computed"  # it divides by computed new customers
    # Day 30 carried back from September to October is September's computed figure.
    kept = card(octo, "growth_number", "kept_d30")
    assert (kept["from"], kept["basis"]) == ("2026-09", "computed")
    rows = {r["slug"]: r for r in card(sept, "growth_channels")["rows"]}
    assert rows["search-ads"]["origin"]["new_customers"] == "computed"
    assert rows["search-ads"]["origin"]["cac"] == "computed"
    rows = {r["slug"]: r for r in card(octo, "growth_channels")["rows"]}
    assert rows["referrals"]["origin"]["new_customers"] == "typed"


def test_every_channel_together_is_mixed_when_one_is_computed_and_one_typed(api):
    channel(api)
    channel(api, "referrals", "Referrals")
    load(api, september())
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-09", "new_customers": 12})
    assert card(growth(api, "?month=2026-09"), "growth_number", "new_customers")["basis"] == "mixed"
