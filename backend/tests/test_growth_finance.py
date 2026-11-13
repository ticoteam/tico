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

    def heard(_store: Any, at: str) -> None:
        calls.append(at)

    AI.AFTER_RUN.append(heard)
    try:
        assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
        insights["111"][0] = {"campaign_id": "brand", "spend": "4500", "date_start": "2026-10-02"}
        assert AI.run(books, Vault(books), iid, now=LATER, http_=http(net)).status == "ok"
    finally:
        AI.AFTER_RUN.remove(heard)
    assert calls == [AT, LATER]
    with books.read() as c:
        assert GF.rows(c, MAPPING, *OCT)[1] == GF.SpendRow("2026-10", "search-ads", 6590.5)


def test_a_failing_hook_never_fails_the_import_and_a_failed_import_rings_nothing(books: Store):
    iid, net, insights = meta(books)
    october(insights)
    calls: list[str] = []

    def broken(_store: Any, _at: str) -> None:
        raise RuntimeError("growth is down")

    def heard(_store: Any, at: str) -> None:
        calls.append(at)

    AI.AFTER_RUN.extend([broken, heard])
    try:
        assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
        net.on("GET", AI.GRAPH + "/act_", lambda *_: (500, {"error": {"message": "down"}}))
        assert AI.run(books, Vault(books), iid, now=LATER, http_=http(net, slept=[])).status == "failed"
    finally:
        AI.AFTER_RUN.remove(broken)
        AI.AFTER_RUN.remove(heard)
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


# ------------------------------------------------------------------------------------------------ into Growth
def _finance_with_spend(store: Store, days: list[tuple[str, str, int]]) -> str:
    """Finance set up with one Meta Ads account (act_111) holding these (day, campaign, cents); its ad account id."""
    from backend.finance import actions as FA
    from backend.tests.helpers.finance_ad_spend import setup_import, tool

    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    tool(store, "meta-ads", "meta-ads")
    setup_import(store, "meta-ads", "meta_ads", {})
    with store.transaction() as c:
        aid = SA.upsert_account(c, "meta-ads", "meta_ads", "111", "Paid rental", "USD", AT)
        _restate(c, aid, days)
    return aid


def _restate(c: Any, aid: str, days: list[tuple[str, str, int]]) -> None:
    first, last = min(d for d, _, _ in days), max(d for d, _, _ in days)
    SA.replace_spend(c, aid, first, last, [SA.SpendRow(d, k, k.title(), cents, "USD") for d, k, cents in days], AT)


def test_a_connected_source_fills_spend_per_channel_follows_a_restated_day_and_refuses_typing(api: Any):
    """AC-1, AC-2 and AC-11 through the engine: connect with 2 campaigns mapped to search-ads and 1 left out; sync
    fills search-ads and Unknown with Finance's month total between them; a restated day reaches Growth on the refresh
    after Finance's import; typing that spend is refused naming the source."""
    from datetime import datetime, timezone

    from backend import growth_sync as GS
    from backend.tests.helpers.api import get, post, put

    store: Store = api.app.state.store
    month = GS.this_month()
    day = f"{month}-01"
    aid = _finance_with_spend(store, [(day, "brand", 421000), (day, "retarget", 188000), (day, "podcast", 31000)])
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    mapping = {"campaigns": {"meta_ads:111:brand": "search-ads", "meta_ads:111:retarget": "Search-Ads"}}
    src = post(
        api,
        "growth/sources",
        {
            "truth_for": "spend",
            "system": "Finance ad spend",
            "reach_kind": "url",
            "sync_kind": GF.KIND,
            "sync_map": mapping,
        },
    )
    assert (src["kind"], src["fills"]) == (GF.KIND, ["spend"])
    result = post(api, f"growth/sources/{src['id']}/sync", {})
    assert result["ok"] and result["written"] == 2, result

    def spend(slug: str) -> float | None:
        rows = get(api, f"growth/cohorts?channel={slug}&month={month}")["cohorts"]
        return rows[0]["spend"] if rows else None

    assert (spend("search-ads"), spend("unknown")) == (6090.0, 310.0)
    first = get(api, f"growth/cohorts?channel=search-ads&month={month}")["cohorts"][0]["origins"]["spend"]
    assert (first["source"], first["quality"]) == ("Finance ad spend", "actual")

    with store.transaction() as c:
        _restate(c, aid, [(day, "brand", 450000), (day, "retarget", 188000), (day, "podcast", 31000)])
    GF.refresh(store, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    assert spend("search-ads") == 6380.0
    put(api, "growth/cohorts", {"channel": "search-ads", "month": month, "spend": 1}, expected=409)
    assert spend("search-ads") == 6380.0


def test_an_account_in_another_currency_makes_the_source_failing_and_nothing_changes(api: Any):
    from backend import growth_sync as GS
    from backend.tests.helpers.api import get, post

    store: Store = api.app.state.store
    month = GS.this_month()
    _finance_with_spend(store, [(f"{month}-01", "brand", 1000)])
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    body = {
        "truth_for": "spend",
        "system": "Finance ad spend",
        "reach_kind": "url",
        "sync_kind": GF.KIND,
        "sync_map": {"campaigns": {"meta_ads:111:brand": "search-ads"}},
    }
    src = post(api, "growth/sources", body)
    assert post(api, f"growth/sources/{src['id']}/sync", {})["ok"]
    with store.transaction() as c:
        c.execute("UPDATE fin_ad_accounts SET currency='EUR'")
    result = post(api, f"growth/sources/{src['id']}/sync", {})
    assert not result["ok"] and result["error"] == "Meta Ads act_111 spends in EUR; Finance's books are in USD"
    listed = next(s for s in get(api, "growth/sources")["sources"] if s["id"] == src["id"])
    assert listed["health"] == "failing" and listed["last_error"] == result["error"]
    assert get(api, f"growth/cohorts?channel=search-ads&month={month}")["cohorts"][0]["spend"] == 10.0


def test_finance_ad_spend_fills_spend_only_and_keeps_its_campaign_map(api: Any):
    from backend.tests.helpers.api import get, post

    _finance_with_spend(api.app.state.store, [(AT[:10], "brand", 1000)])
    base = {"truth_for": "spend", "system": "Finance ad spend", "sync_kind": GF.KIND}  # reach defaults
    post(api, "growth/sources", {**base, "sync_map": {"fields": {"new_customers": "x"}}}, expected=422)
    src = post(api, "growth/sources", {**base, "sync_map": {"campaigns": {"meta_ads:111:brand": "search-ads"}}})
    row = get(api, "growth/sources")["sources"][0]
    assert row["id"] == src["id"] and row["fills"] == ["spend"] and row["reach_kind"] == "credential"
    with api.app.state.store.read() as c:
        saved = c.execute("SELECT sync_map FROM gtm_sources WHERE id=?", (src["id"],)).fetchone()[0]
    assert '"campaigns": {"meta_ads:111:brand": "search-ads"}' in saved
    # Every Growth reader reads the source list: it says how many campaigns are mapped, never which (AC-15).
    assert row["map"]["campaigns_mapped"] == 1 and "meta_ads:111" not in str(
        get(api, "growth/sources", token="cara-test")
    )


def test_a_remap_moves_spend_so_growth_still_equals_finance(api: Any):
    """Review 1: after every campaign is remapped to another channel, the channels they left (search-ads, Unknown) go to
    0 on the next run instead of keeping their old spend, so Growth's month total still equals Finance's; switching
    Finance's import off empties them the same way."""
    from backend import growth_sync as GS
    from backend.tests.helpers.api import get, post

    store: Store = api.app.state.store
    month = GS.this_month()
    day = f"{month}-01"
    before = GS._month(GS._index(month) - 1)  # a month the window rewrites too, not only the current one
    _finance_with_spend(
        store,
        [(day, "brand", 421000), (day, "retarget", 188000), (day, "podcast", 31000), (f"{before}-15", "brand", 5000)],
    )
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/channels", {"slug": "social", "name": "Social"})
    keys = ["meta_ads:111:brand", "meta_ads:111:retarget"]
    body = {"truth_for": "spend", "system": "Finance ad spend", "sync_kind": GF.KIND}
    src = post(api, "growth/sources", {**body, "sync_map": {"campaigns": dict.fromkeys(keys, "search-ads")}})
    assert post(api, f"growth/sources/{src['id']}/sync", {})["ok"]

    def spends(m: str = month) -> dict[str, float | None]:
        rows = get(api, f"growth/cohorts?month={m}")["cohorts"]
        return {str(r["channel"] or "unknown"): r["spend"] for r in rows}

    assert spends() == {"search-ads": 6090.0, "unknown": 310.0}
    assert spends(before) == {"search-ads": 50.0}
    every = {"campaigns": dict.fromkeys([*keys, "meta_ads:111:podcast"], "social")}
    r = api.patch(f"/api/v2/growth/sources/{src['id']}", json={"sync_map": every}, headers=_headers())
    assert r.status_code == 200, r.text
    assert post(api, f"growth/sources/{src['id']}/sync", {})["ok"]
    assert spends() == {"search-ads": 0.0, "social": 6400.0, "unknown": 0.0}
    assert spends(before) == {"search-ads": 0.0, "social": 50.0}
    with store.read() as c:
        logged = [str(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='growth.source.update'")]
    assert logged and not any("meta_ads:111" in p for p in logged)  # the log names no campaign (AC-15)
    with store.read() as c:
        finance_total = sum(SA.account_spend(c, "meta-ads", day, day).values())
    assert round(sum(v or 0 for v in spends().values()) * 100) == finance_total

    with store.transaction() as c:
        c.execute("UPDATE fin_ad_imports SET enabled=0")
    assert post(api, f"growth/sources/{src['id']}/sync", {})["ok"]
    assert spends() == {"search-ads": 0.0, "social": 0.0, "unknown": 0.0}


def _headers() -> dict[str, str]:
    from backend.tests.helpers.api import headers

    return headers()


def test_a_retired_channel_goes_to_zero_and_a_new_channel_with_its_name_still_syncs(api: Any):
    """Review 2: a campaign moved off a channel that is then retired leaves that channel at 0, not its old spend, and
    the run stays ok; a new channel named like the retired one then fills normally. Growth's month total equals
    Finance's at each step."""
    from backend import growth_sync as GS
    from backend.tests.helpers.api import get, headers, post

    store: Store = api.app.state.store
    month = GS.this_month()
    day = f"{month}-01"
    _finance_with_spend(store, [(day, "brand", 421000), (day, "retarget", 188000), (day, "podcast", 31000)])
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/channels", {"slug": "social", "name": "Social"})
    body = {"truth_for": "spend", "system": "Finance ad spend", "sync_kind": GF.KIND}
    first = {"meta_ads:111:brand": "social", "meta_ads:111:retarget": "search-ads"}
    src = post(api, "growth/sources", {**body, "sync_map": {"campaigns": first}})

    def sync() -> dict[str, Any]:
        result = post(api, f"growth/sources/{src['id']}/sync", {})
        assert result["ok"], result
        return result

    def remap(campaigns: dict[str, str]) -> None:
        r = api.patch(
            f"/api/v2/growth/sources/{src['id']}", json={"sync_map": {"campaigns": campaigns}}, headers=headers()
        )
        assert r.status_code == 200, r.text

    def spends() -> dict[str, float]:
        with store.read() as c:
            return {
                str(r[0] or "unknown"): float(r[1] or 0)
                for r in c.execute(
                    "SELECT ch.slug, k.spend FROM growth_cohorts k LEFT JOIN growth_channels ch ON ch.id=k.channel_id "
                    "WHERE k.month=?",
                    (month,),
                )
            }

    sync()
    assert spends() == {"social": 4210.0, "search-ads": 1880.0, "unknown": 310.0}

    remap({"meta_ads:111:brand": "search-ads", "meta_ads:111:retarget": "search-ads"})
    post(api, "growth/channels/social/retire", {})
    sync()
    assert spends() == {"social": 0.0, "search-ads": 6090.0, "unknown": 310.0}
    assert sum(spends().values()) == 6400.0

    post(api, "growth/channels", {"slug": "social-paid", "name": "Social"})
    remap({"meta_ads:111:brand": "social-paid", "meta_ads:111:retarget": "search-ads"})
    sync()
    assert spends() == {"social": 0.0, "social-paid": 4210.0, "search-ads": 1880.0, "unknown": 310.0}
    assert sum(spends().values()) == 6400.0
    assert get(api, "growth/sources")["sources"][0]["health"] == "fresh"
