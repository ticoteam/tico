"""Growth core (backend/growth_core.py): cohort rows and costs pushed, customer facts folded, and the one read, for the
three kinds of company it serves. Synthetic numbers, read on 2026-11-13.

- HotMic-like: a retention segment of viewers, promotions in cash and credits, no revenue.
- Tico-like: no segments and no paid ads; bot cost and hours only.
- TIDY-like: customer facts loaded, which fold into rows and read as slice 1's segment show does."""

import json
from datetime import date, timedelta

import pytest

from backend import growth_computed as GX
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.growth_computed import TODAY
from backend.tests.helpers.growth_segments import load, people, team


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)


def lines(*rows):
    return "\n".join(rows) + "\n"


def push(api, what, source, csv, token="ana-test", expected=200):
    return post(api, f"growth/{what}", {"source": source, "csv": csv}, token=token, expected=expected)


def read(api, query=""):
    return get(api, "growth/read" + (f"?{query}" if query else ""))


def test_a_retention_segment_reads_kept_and_cost_per_retained_never_an_ltv(api):
    post(
        api,
        "growth/segments",
        {
            "slug": "viewers",
            "name": "Viewers",
            "value_kind": "retention",
            "min_cohort": 10,
            "customer_rule": "activated",
        },
    )
    post(api, "growth/channels", {"slug": "promotions", "name": "Promotions", "segment": "viewers"})
    post(api, "growth/channels", {"slug": "organic", "name": "Organic"})
    push(
        api,
        "rows",
        "hotmic-db",
        lines(
            "anchor,period,segment,channel,as_of,entered,activated,activated_eligible,kept_d30,kept_d30_eligible",
            "signup,2026-10,viewers,promotions,2026-11-12,200,80,190,,",
            "signup,2026-10,Viewers,Organic,2026-11-12,300,90,280,,",
            "customer,2026-09,viewers,promotions,2026-11-12,60,,,30,60",
            "customer,2026-09,viewers,organic,2026-11-12,40,,,10,40",
            "customer,2026-10,viewers,promotions,2026-11-12,80,,,,",
            "customer,2026-10,viewers,organic,2026-11-12,90,,,,",
        ),
    )
    push(
        api,
        "costs",
        "promotions",
        lines(
            "period,channel,kind,purpose,amount",
            "2026-09,promotions,promo_cash,,1000",
            "2026-09,promotions,promo_credits,,500",
            "2026-10,promotions,promo_cash,acquisition,1200",
            "2026-10,promotions,promo_credits,,400",
            "2026-10,promotions,promo_cash,retention,300",
        ),
    )
    out = read(api, "scope=segment:viewers&period=2026-10")
    assert out["segment"] == {"slug": "viewers", "name": "Viewers", "rule": "activated", "value_kind": "retention"}
    top = out["headline"]
    assert top["signups"]["value"] == 500
    assert top["activated_rate"] == {"value": 0.362, "n": 470, "cohort": "2026-10", "status": "ok"}
    assert top["paid_rate"]["status"] == "not_supplied"
    assert top["new_customers"]["value"] == 170
    # Retention promotions show beside the cost and never enter CAC.
    assert top["cost"] == {"value": 1600.0, "retention": 300.0}
    assert top["cac"]["value"] == 9.41 and top["cac_loaded"] == {"value": 9.41}
    # October's cohort has no kept yet, so kept is September's, and it says so.
    assert top["kept"] == {"window": "d30", "value": 0.4, "n": 100, "cohort": "2026-09", "status": "ok"}
    assert top["value"]["kept_d30"]["value"] == 0.4
    assert top["value"]["cost_per_retained"] == {"value": 37.5, "cohort": "2026-09", "n": 40}
    assert not {"ltv", "ltv_cac", "payback_months"} & set(top)
    assert [(c["slug"], c["new_customers"], c.get("cost"), c.get("status")) for c in out["channels"]] == [
        ("organic", 90, None, "no_spend"),
        ("promotions", 80, 1600.0, None),
    ]
    # The whole company is retention segments only: its read is the value block too.
    assert "value" in read(api, "period=2026-10")["headline"]


def test_a_company_with_no_segments_and_no_paid_spend_reads_no_spend_with_effort(api):
    post(api, "growth/channels", {"slug": "content", "name": "Content"})
    post(api, "growth/channels", {"slug": "outbound", "name": "Outbound"})
    push(
        api,
        "rows",
        "hq",
        lines(
            "anchor,period,channel,as_of,entered,paid,paid_eligible",
            "signup,2026-10,content,2026-11-10,30,6,30",
            "signup,2026-10,outbound,2026-11-10,10,4,10",
            "customer,2026-10,content,2026-11-10,6,,",
            "customer,2026-10,outbound,2026-11-10,4,,",
            "customer,2026-10,,2026-11-10,2,,",
        ),
    )
    push(
        api,
        "costs",
        "effort",
        lines(
            "period,channel,kind,amount",
            "2026-10,outbound,bot,120",
            "2026-10,outbound,hours,720",
            "2026-10,content,bot,60",
        ),
    )
    out = read(api)
    assert out["period"] == "2026-10" and out["scope"] == "all" and "segment" not in out
    top = out["headline"]
    assert top["paid_rate"] == {"value": 0.25, "n": 40, "cohort": "2026-10", "status": "ok"}
    assert top["cost"] == {"value": None, "status": "no_spend", "with_effort": 900.0}
    assert top["cac"] == {"value": None, "n": 12, "status": "no_spend", "with_effort": 75.0}
    assert top["ltv"]["status"] == "not_supplied" and top["ltv_cac"] == {"value": None, "status": "no_spend"}
    outbound = next(c for c in out["channels"] if c["slug"] == "outbound")
    assert outbound == {
        "slug": "outbound",
        "signups": 10,
        "new_customers": 4,
        "name": "Outbound",
        "new_share": 0.33,
        "owner": None,
        "budget": None,
        "over_budget": None,
        "cost": None,
        "cac": None,
        "status": "no_spend",
        "with_effort": 210.0,
    }
    assert out["channels"][-1] == {"slug": "unknown", "signups": 0, "new_customers": 2, "new_share": 0.17}
    assert out["health"] == {
        "basis": "customers",
        "unknown_share": 0.17,
        "reconciled": "not_reconciled",
        "stale_sources": [],
    }
    assert out["more"] == ["campaigns", "retention", "series"]
    assert len(json.dumps(out)) < 6000  # about 1.5k tokens without detail


def test_tidy_facts_fold_into_rows_that_read_as_the_segment_show_does(api):
    """Parity: the same month through slice 1's segment show and through the read."""
    team(api)
    sep, june = date(2026, 9, 1), date(2026, 6, 1)
    rows = people(0, 30, sep, channel="search-ads", segment="consumer")
    for row in rows[:10]:
        row["churned"] = (date.fromisoformat(row["became_customer"]) + timedelta(days=10)).isoformat()
    rows += people(200, 10, sep, channel="brand", segment="consumer")
    rows += people(300, 18, sep, channel="partners", segment="rental")
    rows += people(400, 2, sep, channel="search-ads", segment="rental")
    rows += people(500, 10, sep, channel="brand")
    earlier = people(600, 40, june, channel="search-ads", segment="consumer")
    for row in earlier:
        row["net_d90"] = 60 if int(row["customer_key"][4:]) % 2 else 40
    load(api, rows + earlier + people(700, 10, june, channel="brand", segment="consumer"))

    shown = get(api, "growth/segments/consumer")
    month = next(m for m in shown["months"] if m["month"] == "2026-09")
    out = read(api, "scope=segment:consumer&period=2026-09&detail=retention")
    top = out["headline"]
    assert top["new_customers"]["value"] == month["new_customers"] == 40
    assert top["cac"]["value"] == month["cac"] == 30.0
    assert top["cac_loaded"]["value"] == month["cac_loaded"] == 41.67
    september = next(r for r in out["retention"] if r["cohort"] == "2026-09")
    assert round(september["kept_d30"]["value"] * 100, 1) == month["kept_d30"] == 75.0
    assert (
        {k: out["health"][k] for k in ("no_segment", "mismatch")}
        == shown["health"]
        == {
            "no_segment": 10,
            "mismatch": 2,
        }
    )
    # Rental waits 30 days of inactivity: its September kept_d30 is not yet, as segment show has it.
    rental = next(m for m in get(api, "growth/segments/rental")["months"] if m["month"] == "2026-09")
    rental_read = read(api, "scope=segment:rental&period=2026-09&detail=retention")["retention"][0]
    assert rental["kept_d30"] is None and rental_read["kept_d30"] == {"value": None, "n": 0, "status": "not_yet"}
    june_shown = next(m for m in shown["months"] if m["month"] == "2026-06")
    assert top["ltv"] == {"value": 50.0, "window": "d90", "n": 40, "cohort": "2026-06", "status": "ok"}
    assert top["ltv"]["value"] == june_shown["ltv_observed_d90"]
    assert top["ltv_cac"] == {"value": 1.7} and top["payback_months"] == {"value": 1.8, "status": "estimate"}
    # The rows were folded on read, never stored.
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM growth_cohort_rows").fetchone()[0] == 0
    # A customer facts source is not a push label: its people would count twice.
    push(api, "rows", "upload", lines("anchor,period,as_of,entered", "signup,2026-09,2026-11-01,5"), expected=422)


def test_a_push_replaces_its_source_for_the_months_it_covers_and_names_ten_campaigns_at_most(api):
    post(api, "growth/channels", {"slug": "meta", "name": "Meta"})
    header = "anchor,period,channel,campaign,as_of,entered"
    first = [header, "customer,2026-09,meta,,2026-10-01,20", "customer,2026-10,meta,,2026-11-01,30"]
    assert push(api, "rows", "hq", lines(*first))["periods"] == ["2026-09", "2026-10"]
    again = push(api, "rows", "hq", lines(header, "customer,2026-10,meta,,2026-11-12,35"))
    assert again["replaced"] == 1 and again["periods"] == ["2026-10"]
    series = {m["period"]: m["new_customers"] for m in read(api, "detail=series&period=2026-10")["series"]}
    assert series["2026-09"] == 20 and series["2026-10"] == 35
    # Past ten named campaigns for a channel in a month, the push is refused: the rest are named other.
    many = [header] + [f"customer,2026-10,meta,ad-{n:02d},2026-11-12,{n + 1}" for n in range(11)]
    refused = push(api, "rows", "ads", lines(*many), expected=422)["error"]
    assert refused["rule"] == "campaigns" and "row 12" in refused["detail"]
    push(api, "rows", "ads", lines(*many[:11], "customer,2026-10,meta,other,2026-11-12,11"))
    out = read(api, "detail=campaigns&period=2026-10")
    assert len(out["campaigns"]["meta"]) == 12 and out["headline"]["new_customers"]["value"] == 35 + sum(range(1, 12))
    # A source holds pushed rows or customer facts, never both, whichever came first.
    push(api, "rows", "upload", lines(header, "customer,2026-08,meta,,2026-09-01,3"))
    assert load(api, people(0, 3, date(2026, 8, 1)), expected=422)["error"]["rule"] == "source"


def test_a_bad_row_is_refused_by_column_and_row_and_nothing_is_pushed(api):
    post(api, "growth/channels", {"slug": "meta", "name": "Meta"})
    header = "anchor,period,channel,as_of,entered,kept_d30,kept_d30_eligible"
    refused = push(api, "rows", "hq", lines(header + ",email", "customer,2026-10,meta,2026-11-01,5,,,x"), expected=422)
    assert refused["error"]["rule"] == "columns" and "email" in refused["error"]["detail"]
    refused = push(
        api,
        "rows",
        "hq",
        lines(
            header,
            "customer,2026-10,meta,2026-11-01,40,30,40",
            "customer,2026-09,meta,2026-11-01,40,50,40",
            "customer,2026-08,meta,2026-11-01,lots,,",
            "customer,2026-07,secret-channel,2026-11-01,4,,",
        ),
        expected=422,
    )["error"]
    detail = refused["detail"]
    assert "kept_d30" in detail and "row 3" in detail and "entered" in detail and "row 4" in detail
    assert "channel" in detail and "row 5" in detail
    net = "anchor,period,as_of,entered,net_d90_sum,net_d90_eligible"
    refused = push(
        api,
        "rows",
        "hq",
        lines(net, "customer,2026-10,2026-11-01,5,0,0", "customer,2026-09,2026-11-01,5,90,0"),
        expected=422,
    )
    assert refused["error"]["rule"] == "net_zero" and "net_d90_sum" in refused["error"]["detail"]
    assert "row 3" in refused["error"]["detail"]
    assert "lots" not in detail and "secret-channel" not in detail and "50" not in detail
    assert read(api)["headline"]["new_customers"]["status"] == "not_supplied"
    cost = push(api, "costs", "ads", lines("period,kind,amount", "2026-10,dollars,10"), expected=422)["error"]
    assert cost["rule"] == "kind" and "row 2" in cost["detail"] and "dollars" not in cost["detail"]
    # Only the owner, an admin or the Growth Manager pushes; everyone signed in reads.
    push(api, "rows", "hq", lines(header, "customer,2026-10,meta,2026-11-01,4,,"), token="cara-test", expected=403)
    assert get(api, "growth/read", token="cara-test")["scope"] == "all"
    get(api, "growth/read?scope=segment:nobody", expected=404)
    get(api, "growth/read?detail=everything", expected=422)


def test_each_channel_carries_its_owner_and_budget_and_mine_is_the_callers_channels(api):
    from backend.tests.helpers.company import bot

    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('5e0b0a7a','seo-owner','SEO Owner','active')")
    owner = bot(api, "5e0b0a7a")
    post(api, "growth/channels", {"slug": "seo", "name": "SEO", "owner_ref": "seo-owner", "budget_month": 500})
    post(api, "growth/channels", {"slug": "meta", "name": "Meta", "budget_month": 1000})
    post(api, "growth/channels", {"slug": "events", "name": "Events", "owner_ref": "seo-owner", "budget_month": 200})
    push(
        api,
        "rows",
        "hq",
        lines(
            "anchor,period,channel,as_of,entered",
            "customer,2026-10,seo,2026-11-01,8",
            "customer,2026-10,meta,2026-11-01,20",
        ),
    )
    push(api, "costs", "ads", lines("period,channel,kind,amount", "2026-10,seo,money,650", "2026-10,meta,money,900"))
    shown = {c["slug"]: c for c in read(api, "period=2026-10")["channels"]}
    assert (shown["seo"]["owner"], shown["seo"]["budget"], shown["seo"]["over_budget"]) == (
        "bot:5e0b0a7a",
        500.0,
        150.0,
    )
    assert (shown["meta"]["owner"], shown["meta"]["budget"], shown["meta"]["over_budget"]) == (None, 1000.0, None)
    mine = get(api, "growth/read?scope=mine&period=2026-10", token=owner)
    assert mine["scope"] == "mine" and [c["slug"] for c in mine["channels"]] == ["seo", "events"]
    assert mine["headline"]["new_customers"]["value"] == 8 and mine["headline"]["cost"] == {"value": 650.0}
    assert mine["channels"][1] == {
        "slug": "events",
        "signups": 0,
        "new_customers": 0,
        "name": "Events",
        "new_share": 0.0,
        "owner": "bot:5e0b0a7a",
        "budget": 200.0,
        "over_budget": None,
        "cost": None,
        "cac": None,
        "status": "no_spend",
    }
    # A channel's owner pushes rows and costs for its own channels only.
    rows = "anchor,period,channel,as_of,entered"
    push(api, "rows", "seo-bot", lines(rows, "customer,2026-10,seo,2026-11-01,3"), token=owner)
    refused = push(
        api,
        "costs",
        "seo-bot",
        lines("period,channel,kind,amount", "2026-10,seo,money,5", "2026-10,meta,money,5"),
        token=owner,
        expected=403,
    )
    assert "meta" in refused["error"]["detail"]
    # A push replaces its whole source, so an owner can't push into a source holding another channel's rows.
    push(api, "rows", "shared", lines(rows, "customer,2026-10,meta,2026-11-01,2"))
    clash = push(api, "rows", "shared", lines(rows, "customer,2026-10,seo,2026-11-01,3"), token=owner, expected=403)
    assert "own source" in clash["error"]["detail"]
    nobody = read(api, "scope=mine&period=2026-10")
    assert nobody["channels"] == [] and nobody["headline"]["new_customers"]["status"] == "not_supplied"


def test_metrics_are_the_channels_kpis_with_the_months_latest_reading(api):
    def kpi(name):
        return post(api, "kpis", {"name": name})["kpi"]["id"]

    signups, refunds = kpi("SEO signups"), kpi("Meta refunds")
    kpi("Office plants")  # not Growth's: no channel links it
    for value, end, quality in (
        (10, "2026-09-30", "measured"),
        (12, "2026-10-31", "estimate"),
        (20, "2026-11-15", None),
    ):
        post(api, f"kpis/{signups}/readings", {"value": value, "period_end": end, "quality": quality})
    post(api, "growth/channels", {"slug": "seo", "name": "SEO", "input_kpi_id": signups})
    post(api, "growth/channels", {"slug": "meta", "name": "Meta", "guardrail_kpi_id": refunds})
    shown = {m["name"]: m for m in read(api, "period=2026-10")["metrics"]}
    assert set(shown) == {"SEO signups", "Meta refunds"}
    seo = shown["SEO signups"]
    assert (seo["channel"], seo["value"], seo["period_end"], seo["estimate"], seo["vs_prior"]) == (
        "seo",
        12.0,
        "2026-10-31",
        True,
        10.0,
    )
    assert shown["Meta refunds"]["value"] is None and shown["Meta refunds"]["channel"] == "meta"
    assert [m["id"] for m in read(api, "scope=channel:meta&period=2026-10")["metrics"]] == [refunds]


def test_a_zero_or_tiny_cost_never_divides_by_zero(api):
    post(api, "growth/channels", {"slug": "free", "name": "Free"})
    header = "anchor,period,channel,as_of,entered,net_d90_sum,net_d90_eligible"
    push(
        api,
        "rows",
        "hq",
        lines(header, "customer,2026-07,free,2026-11-01,50,1500,50", "customer,2026-10,free,2026-11-01,20,,"),
    )
    push(api, "costs", "ads", lines("period,channel,kind,amount", "2026-10,free,money,0"))
    top = read(api, "period=2026-10")["headline"]
    assert top["ltv"]["value"] == 30.0
    assert top["cost"] == {"value": None, "status": "no_spend"} and top["cac"]["status"] == "no_spend"
    assert top["ltv_cac"] == {"value": None, "status": "no_spend"}
    push(api, "costs", "ads", lines("period,channel,kind,amount", "2026-10,free,money,0.004"))
    top = read(api, "period=2026-10")["headline"]
    assert top["cac"]["value"] == 0.0 and top["ltv_cac"]["value"] == 150000.0  # from unrounded CAC


def test_typed_months_count_their_customers_where_no_rows_do_and_cost_shares_without_customers(api):
    for slug in ("typed", "rows", "quiet"):
        post(api, "growth/channels", {"slug": slug, "name": slug.title()})
    for slug, new in (("typed", 10), ("rows", 99)):
        put(api, "growth/cohorts", {"channel": slug, "month": "2026-10", "spend": 100, "new_customers": new})
    put(api, "growth/cohorts", {"channel": "quiet", "month": "2026-10", "spend": 50})
    push(api, "rows", "hq", lines("anchor,period,channel,as_of,entered", "customer,2026-10,rows,2026-11-01,5"))
    out = read(api, "period=2026-10")
    by = {c["slug"]: c for c in out["channels"]}
    assert (
        by["typed"]["new_customers"] == 10 and by["typed"]["cac"] == 10.0
    )  # typed counts where no row covers the month
    assert by["rows"]["new_customers"] == 5  # rows win: the typed 99 is not added
    assert out["headline"]["new_customers"]["value"] == 15
    assert by["quiet"]["status"] == "no_customers" and by["quiet"]["cost_share"] == 0.2
    assert out["health"]["stale_sources"] == []


def test_the_read_records_growth_first_opened(api):
    from backend import growth_setup as GS

    read(api)
    with api.app.state.store.read() as c:
        first = GS.recorded(c)
    assert first["first_opened_at"] and first["first_opened_by"] == "human:ana"


def test_pushed_costs_win_over_typed_spend_and_a_blank_count_with_none_eligible_is_not_supplied(api):
    post(api, "growth/channels", {"slug": "cards", "name": "Cards"})
    put(api, "growth/cohorts", {"channel": "cards", "month": "2026-10", "spend": 300, "bot_cost": 40})
    retention = lines("period,channel,kind,purpose,amount", "2026-10,cards,money,retention,50")
    push(api, "costs", "ads", retention)
    assert read(api, "period=2026-10")["channels"][0]["cost"] == 300.0  # a retention cost leaves typed spend alone
    header = "anchor,period,channel,as_of,entered,activated,activated_eligible,net_d90_sum,net_d90_eligible"
    push(api, "rows", "hq", lines(header, "customer,2026-10,cards,2026-11-01,4,,0,,0"))
    push(api, "costs", "ads", lines("period,channel,kind,amount", "2026-10,cards,money,120"))
    cards = read(api, "period=2026-10")["channels"][0]
    assert cards["new_customers"] == 4 and cards["cost"] == 120.0  # the typed 300 is not added
    assert cards["with_effort"] == 40.0  # typed bot cost stays: no pushed bot cost covers it
    push(api, "costs", "ads", lines("period,channel,kind,amount", "2026-10,cards,money,120", "2026-10,cards,bot,20"))
    assert read(api, "period=2026-10")["channels"][0]["with_effort"] == 35.0
    over = push(api, "rows", "hq", lines(header, "customer,2026-10,cards,2026-11-01,4,3,2,,"), expected=422)
    assert over["error"]["rule"] == "over"  # given values still keep x <= eligible <= entered


def test_an_unfinished_month_reads_partial_with_no_verdicts(api):
    post(api, "growth/channels", {"slug": "ads", "name": "Ads"})
    months = ("2026-08", "2026-09", "2026-10", "2026-11")
    rows = [f"customer,{m},ads,2026-11-12,{10 if m == '2026-11' else 50}" for m in months]
    push(api, "rows", "hq", lines("anchor,period,channel,as_of,entered", *rows))
    push(api, "costs", "ads", lines("period,channel,kind,amount", *(f"{m},ads,money,500" for m in months)))
    done = read(api, "period=2026-10")
    assert "partial" not in done and done["headline"]["new_customers"]["verdict"] == "flat"
    now = read(api, "period=2026-11")  # the 13th: the month is not over
    assert now["partial"] is True
    assert now["headline"]["new_customers"]["verdict"] is None and now["headline"]["cac"]["verdict"] is None
    assert now["channels"][0]["verdict"] is None


def test_signup_rows_alone_give_health_and_channels_their_gaps_and_segment_none_reads_the_no_segment_group(api):
    for slug in ("creators", "viewers"):
        post(api, "growth/segments", {"slug": slug, "name": slug.title()})
    header = "anchor,period,segment,channel,as_of,entered"
    push(
        api,
        "rows",
        "hotmic-db",
        lines(
            header,
            "signup,2026-10,creators,,2026-11-12,40",
            "signup,2026-10,viewers,,2026-11-12,62",
            "signup,2026-10,,,2026-11-12,98",
        ),
    )
    october = read(api, "period=2026-10")
    health = october["health"]
    assert health["basis"] == "signups" and health["unknown_share"] == 1.0 and health["no_segment"] == 98
    # no customer rows: new customers not supplied, on the channel line as in the headline
    assert october["channels"] == [{"slug": "unknown", "signups": 200, "new_customers": None, "new_share": None}]
    # nothing pushed for November: not supplied, never a measured 0
    assert read(api, "period=2026-11")["headline"]["signups"] == {"value": None, "status": "not_supplied"}
    none = read(api, "scope=segment:none&period=2026-10")
    assert none["scope"] == "segment:none" and none["headline"]["signups"]["value"] == 98
    push(api, "rows", "hotmic-db", lines(header, "customer,2026-10,viewers,,2026-11-12,10"))
    assert read(api, "period=2026-10")["health"]["basis"] == "customers"


def test_a_quiet_company_lists_its_channels_reads_nobody_and_measures_health_over_sign_ups(api):
    post(api, "growth/channels", {"slug": "content", "name": "Content"})
    header = "anchor,period,channel,as_of,entered,kept_d30,kept_d30_eligible"
    push(api, "rows", "hq", lines(header, "signup,2026-10,,2026-11-12,2,,", "customer,2026-10,,2026-11-12,0,0,0"))
    out = read(api, "period=2026-10")
    # set up but quiet: listed all the same
    assert [c["slug"] for c in out["channels"]] == ["content", "unknown"]
    assert out["channels"][0]["new_customers"] == 0 and out["channels"][0]["status"] == "no_spend"
    # a cohort of nobody has nothing to wait for
    assert out["headline"]["kept"]["status"] == "nobody"
    # zero customer rows don't make customers the basis
    assert out["health"]["basis"] == "signups" and out["health"]["unknown_share"] == 1.0
