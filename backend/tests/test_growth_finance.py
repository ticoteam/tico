"""Growth's Finance ad spend source (backend/growth_finance.py, spec "Growth fills its own numbers" part 3): Finance's
imported daily spend added up per channel and month in the book's currency, Unknown for an unmapped campaign, a restated
day followed after the next import, an account in another currency failing the run, totals only, and connecting only
for someone who sees every connected ad account in Finance. Fake platforms, never the network."""

from typing import Any

import pytest

from backend import growth_finance as GF
from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance.store import ads as SA
from backend.store import Problem, Store
from backend.tests.helpers.finance_ad_spend import http, meta
from backend.tests.helpers.finance_fixtures import AT, OWNER, books, finance_template, store  # noqa: F401  (fixtures)

OCT = ("2026-10-01", "2026-10-31")
LATER = "2026-10-20T12:00:00.000000Z"


def october(insights: dict[str, list[dict[str, Any]]]) -> None:
    """Three campaigns with October spend on Meta account 111; one on 222 in early November is outside October."""
    insights["111"] = [
        {"campaign_id": "brand", "campaign_name": "Brand search", "spend": "4000", "date_start": "2026-10-02"},
        {"campaign_id": "brand", "campaign_name": "Brand search", "spend": "210.50", "date_start": "2026-10-09"},
        {"campaign_id": "retarget", "campaign_name": "Retargeting", "spend": "1880", "date_start": "2026-10-03"},
        {"campaign_id": "podcast", "campaign_name": "Test podcast promo", "spend": "310", "date_start": "2026-10-04"},
    ]
    insights["222"] = []


MAPPING = {"campaigns": {"meta_ads:111:brand": "search-ads", "meta_ads:111:retarget": "search-ads"}}


def test_two_mapped_campaigns_sum_on_their_channel_and_the_third_counts_on_unknown(books: Store):
    """AC-1: 2 of 3 October campaigns mapped to search-ads; the third is Unknown; Growth's month total is Finance's."""
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.read() as c:
        got = GF.rows(c, MAPPING, *OCT)
        finance_total = sum(SA.account_spend(c, "meta-ads", *OCT).values())
    assert got == [GF.SpendRow("2026-10", None, 310.0), GF.SpendRow("2026-10", "search-ads", 6090.5)]
    assert round(sum(r.spend for r in got) * 100) == finance_total == 640050


def test_a_campaign_mapped_to_unknown_or_nothing_counts_on_unknown(books: Store):
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    mapping = {"campaigns": {"meta_ads:111:brand": "unknown", "meta_ads:111:retarget": ""}}
    with books.read() as c:
        assert GF.rows(c, mapping, *OCT) == [GF.SpendRow("2026-10", None, 6400.5)]
        assert GF.rows(c, {}, *OCT) == [GF.SpendRow("2026-10", None, 6400.5)]


def test_a_day_belongs_to_the_month_the_platform_reports_it_in(books: Store):
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.transaction() as c:
        aid = str(c.execute("SELECT id FROM fin_ad_accounts WHERE external_id='111'").fetchone()[0])
        days = [
            SA.SpendRow("2026-10-31", "brand", "", 1000, "USD"),
            SA.SpendRow("2026-11-01", "brand", "", 2000, "USD"),
        ]
        SA.replace_spend(c, aid, "2026-10-01", "2026-11-30", days, AT)
    with books.read() as c:
        assert GF.rows(c, MAPPING, "2026-10-01", "2026-11-30") == [
            GF.SpendRow("2026-10", "search-ads", 10.0),
            GF.SpendRow("2026-11", "search-ads", 20.0),
        ]


def test_a_restated_day_changes_growth_spend_after_the_next_import_and_the_import_rings_growth(books: Store):
    """AC-2: Finance's next import restates a day in the month; the rows a run writes follow it, and each good import
    calls the hooks Growth hangs on it with the run's time."""
    iid, net, insights = meta(books)
    october(insights)
    calls: list[str] = []
    AI.AFTER_RUN.append(lambda _store, at: calls.append(at))
    try:
        assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
        insights["111"][0] = {"campaign_id": "brand", "spend": "4500", "date_start": "2026-10-02"}
        assert AI.run(books, Vault(books), iid, now=LATER, http_=http(net)).status == "ok"
    finally:
        AI.AFTER_RUN.clear()
    assert calls == [AT, LATER]
    with books.read() as c:
        assert GF.rows(c, MAPPING, *OCT)[1] == GF.SpendRow("2026-10", "search-ads", 6590.5)


def test_a_failing_hook_never_fails_the_import_and_a_failed_import_rings_nothing(books: Store):
    iid, net, insights = meta(books)
    october(insights)
    calls: list[str] = []

    def broken(_store: Any, _at: str) -> None:
        raise RuntimeError("growth is down")

    AI.AFTER_RUN.extend([broken, lambda _store, at: calls.append(at)])
    try:
        assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
        net.on("GET", AI.GRAPH + "/act_", lambda *_: (500, {"error": {"message": "down"}}))
        assert AI.run(books, Vault(books), iid, now=LATER, http_=http(net, slept=[])).status == "failed"
    finally:
        AI.AFTER_RUN.clear()
    assert calls == [AT]


def test_an_account_finance_cannot_convert_fails_the_run_naming_it(books: Store):
    """An ad account in another currency than the book's: the run fails with that reason (no conversion in Growth)."""
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.transaction() as c:
        c.execute("UPDATE fin_ad_accounts SET currency='EUR' WHERE external_id='222'")
    with books.read() as c, pytest.raises(GF.Failing) as failed:
        GF.rows(c, MAPPING, *OCT)
    assert str(failed.value) == "Meta Ads act_222 spends in EUR; Finance's books are in USD"
    with books.transaction() as c:
        c.execute("UPDATE fin_ad_accounts SET currency='USD'")
        c.execute("UPDATE fin_ad_spends SET currency='GBP' WHERE campaign_external_id='podcast'")
    with books.read() as c, pytest.raises(GF.Failing, match="act_111 spends in GBP"):
        GF.rows(c, MAPPING, *OCT)


def test_rows_are_channel_month_totals_only(books: Store):
    """AC-15: what a run writes names a channel, a month and spend: never a campaign's name or id."""
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.read() as c:
        got = GF.rows(c, MAPPING, *OCT)
    assert all(set(vars(r)) == {"month", "channel", "spend"} for r in got)
    text = repr(got)
    assert not any(word in text for word in ("brand", "Brand", "retarget", "podcast", "111"))


def test_only_someone_who_sees_every_ad_account_may_connect_and_the_refusal_names_what_is_missing(books: Store):
    """AC-14 (Finance visibility): the owner (a finance admin) may; Cara, who leads a team but has no finance admin and
    not everything in view, is refused, naming what is missing; the campaign list is refused to her the same way."""
    with books.read() as c, pytest.raises(Problem) as none:
        GF.may_connect(c, OWNER)
    assert none.value.status == 409 and "no Google Ads or Meta" in none.value.detail
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.read() as c:
        GF.may_connect(c, OWNER)
        listed = GF.campaigns(c, OWNER, *OCT)
        with pytest.raises(Problem) as refused:
            GF.may_connect(c, "human:cara")
        with pytest.raises(Problem):
            GF.campaigns(c, "human:cara", *OCT)
    assert refused.value.status == 403
    assert refused.value.detail == (
        "Connecting Finance ad spend needs someone who sees every ad account in Finance: you do not see the 2 "
        "connected ad accounts (Meta Ads). A finance admin can connect it."
    )
    assert [a["account"] for a in listed["accounts"]] == ["act_111", "act_222"]
    assert [(x["key"], x["name"], x["last_month_spend"]) for x in listed["campaigns"]] == [
        ("meta_ads:111:brand", "Brand search", 4210.5),
        ("meta_ads:111:retarget", "Retargeting", 1880.0),
        ("meta_ads:111:podcast", "Test podcast promo", 310.0),
    ]


def test_an_import_switched_off_stops_its_accounts_counting(books: Store):
    iid, net, insights = meta(books)
    october(insights)
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.transaction() as c:
        c.execute("UPDATE fin_ad_imports SET enabled=0")
    with books.read() as c:
        assert GF.rows(c, MAPPING, *OCT) == []
        assert GF.held(c) is None


def test_window_counts_back_whole_months():
    assert GF.window("2026-11-13", 12) == ("2025-12-01", "2026-11-13")
    assert GF.window("2026-03-31", 3) == ("2026-01-01", "2026-03-31")
    assert GF.window("2026-03-31", 1) == ("2026-03-01", "2026-03-31")


def test_the_mapping_step_route_is_for_an_admin_who_sees_every_ad_account(api: Any):
    from backend.finance import actions as FA
    from backend.tests.helpers.api import headers
    from backend.tests.helpers.finance_ad_spend import setup_import, tool

    url = "/api/v2/growth/finance-campaigns"
    store: Store = api.app.state.store
    assert api.get(url, headers=headers()).status_code == 409  # Finance is not set up
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    tool(store, "meta-ads", "meta-ads")
    setup_import(store, "meta-ads", "meta_ads", {})
    with store.transaction() as c:
        aid = SA.upsert_account(c, "meta-ads", "meta_ads", "111", "Paid rental", "USD", AT)
        day = AT[:10]
        SA.replace_spend(c, aid, day, day, [SA.SpendRow(day, "brand", "Brand search", 4210, "USD")], AT)
    answer = api.get(url, headers=headers())
    assert answer.status_code == 200, answer.text
    assert [(x["key"], x["last_month_spend"]) for x in answer.json()["campaigns"]] == [("meta_ads:111:brand", 42.1)]
    assert api.get(url, headers=headers("cara-test")).status_code == 403
    assert api.get(url, params={"months": 13}, headers=headers()).status_code == 422
