"""The Growth line (backend/growth_line.py, task #414): one month joined source to KPI, as the page draws it and
`GET /api/v2/growth/numbers` returns it. Acme, a home-cleaning company, has four channels and Unknown, three sources
(an ad spend sheet, a customers query that went stale, and Stripe, which syncs nothing), a CAC guardrail on Search Ads
and an input KPI that Search Ads moves."""

import pytest

from backend import growth_computed as GX
from backend import growth_sync as GS
from backend.store import H
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.company import bot
from backend.tests.helpers.growth_cards import classic_cards  # noqa: F401 (the fixture)
from backend.tests.helpers.growth_computed import TODAY, as_csv, september

M = GS._month(GS._index(GS.this_month()) - 1)  # last month: inside a synced source's window
OLD = "2026-01-10T08:00:00.000000Z"  # a good sync long before twice its refresh time


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def kpi(api, name, unit="", role=None, value=None):
    found = post(api, "kpis", {"name": name, "unit": unit})["kpi"]
    if role:
        post(api, f"kpis/{found['id']}", {"growth_role": role})
    if value is not None:
        post(api, f"kpis/{found['id']}/readings", {"value": value, "period_end": f"{M}-28T00:00:00Z"})
    return found["id"]


def source(api, name, fields, rows):
    found = post(
        api,
        "growth/sources",
        {"truth_for": name, "system": name, "sync_kind": "file", "sync_map": {"fields": fields}},
    )
    header = ["month", "channel", *fields.values()]
    text = ",".join(header) + "\n" + "".join(f"{M},{ch}," + ",".join(map(str, vals)) + "\n" for ch, vals in rows)
    assert post(api, f"growth/sources/{found['id']}/rows", {"csv": text})["ok"]
    return found["id"]


def acme(api):
    north = kpi(api, "Active customers", role="north_star", value=480)
    new_kpi = kpi(api, "New customers", role="input", value=142)
    cac_kpi = kpi(api, "Search Ads CAC", unit="$", role="guardrail", value=372)
    post(
        api,
        "growth/channels",
        {
            "slug": "search-ads",
            "name": "Search Ads",
            "budget_month": 15000,
            "guardrail_kpi_id": cac_kpi,
            "guardrail_max": 350,
            "input_kpi_id": new_kpi,
            "sort": 1,
        },
    )
    for i, (slug, name) in enumerate((("seo", "SEO"), ("referrals", "Referrals"), ("social", "Social")), 2):
        post(api, "growth/channels", {"slug": slug, "name": name, "sort": i})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": M, "new_customers": 50, "quality": "actual"})
    put(api, "growth/cohorts", {"channel": "referrals", "month": M, "new_customers": 30, "spend": 900})
    put(api, "growth/cohorts", {"month": M, "new_customers": 14, "quality": "actual"})  # Unknown
    sheet = source(api, "Ad spend sheet", {"spend": "spend"}, [("search-ads", [18600]), ("seo", [2000])])
    query = source(api, "Customers query", {"new_customers": "signups"}, [("seo", [40]), ("social", [8])])
    stripe = post(api, "growth/sources", {"truth_for": "bookings", "system": "Stripe", "reach_kind": "mcp"})["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE gtm_sources SET last_good_run_at=? WHERE id=?", (OLD, query))
    return {"north": north, "new": new_kpi, "cac": cac_kpi, "sheet": sheet, "query": query, "stripe": stripe}


def line(api, token="ana-test", month=M):
    return get(api, f"growth/numbers?months=6&month={month}", token)["line"]


def row(view, slug):
    return next(ch for ch in view["channels"] if ch["slug"] == slug)


def test_the_line_joins_each_source_to_the_numbers_it_fills_and_each_channel_to_its_kpis(api):
    """AC-1, AC-3, AC-17: every source with its health, each channel with its numbers, where each came from, and the
    KPIs; the same values as `growth economics`."""
    ids = acme(api)
    view = line(api)
    assert view["month"] == M
    assert [s["name"] for s in view["sources"]] == [
        "Ad spend sheet",
        "Stripe",
        "Customers query",
        "Typed by hand",
    ]  # by what each is the truth for
    by = {s["id"]: s for s in view["sources"]}
    assert by[ids["sheet"]]["fills"] == [
        {"channel": "search-ads", "number": "spend"},
        {"channel": "seo", "number": "spend"},
    ]
    assert by[ids["stripe"]]["health"] == "not_synced" and by[ids["stripe"]]["fills"] == []
    assert [ch["slug"] for ch in view["channels"]] == ["search-ads", "seo", "referrals", "social", "unknown", "all"]
    ads = row(view, "search-ads")
    # Spend synced from the sheet, new customers typed: joined to both, each number saying which.
    assert ads["sources"] == [ids["sheet"], "typed"]
    assert (ads["numbers"]["spend"]["origin"], ads["numbers"]["spend"]["source_names"]) == (
        "synced",
        ["Ad spend sheet"],
    )
    assert ads["numbers"]["spend"]["synced_at"]
    assert (ads["numbers"]["new_customers"]["origin"], ads["numbers"]["new_customers"]["source_names"]) == (
        "typed",
        ["Typed by hand"],
    )
    assert ads["input_kpi"] == ids["new"] and ads["guardrail"]["kpi_id"] == ids["cac"]
    kpis = {k["id"]: k for k in view["kpis"]}
    assert [k["role"] for k in view["kpis"]] == ["north_star", "input", "guardrail"]
    assert kpis[ids["new"]]["moved_by"] == ["search-ads"] and kpis[ids["cac"]]["held_by"] == ["search-ads"]
    # Every value equals the economics read.
    eco = get(api, f"growth/economics?month={M}")
    for r in eco["rows"]:
        mine = row(view, r["channel"])
        for field in ("spend", "new_customers", "kept_d30"):
            assert mine["numbers"][field]["value"] == r[field]
        for field in ("cac", "ltv_cac", "payback_months"):
            assert mine["economics"][field]["value"] == r[field]
    assert row(view, "all")["economics"]["cac"]["value"] == eco["total"]["cac"]


def test_a_stale_source_marks_every_number_it_fills_and_the_headline_says_since_when(api):
    """AC-2, AC-15: the stale query's numbers are marked, the fresh sheet's are not; source and headline give one date."""
    ids = acme(api)
    view = line(api)
    query = next(s for s in view["sources"] if s["id"] == ids["query"])
    assert query["health"] == "stale" and query["since"] == OLD
    seo, social, ads = row(view, "seo"), row(view, "social"), row(view, "search-ads")
    assert seo["numbers"]["new_customers"]["stale"] and social["numbers"]["new_customers"]["stale"]
    assert seo["economics"]["cac"]["stale"]  # made from a stale number
    assert not seo["numbers"]["spend"]["stale"] and not ads["numbers"]["spend"]["stale"]
    head = view["headline"]
    assert head["stale_sources"] == [ids["query"]] and head["stale_since"] == OLD and head["stale_numbers"] == 3
    assert "One source has not synced since 10 Jan, so 3 numbers below are from then." in head["sentence"]
    assert "One channel is over its guardrail." in head["sentence"]
    assert head["sentence"].split(" ")[1] == "brought" and head["new_customers"] == 142


def test_over_budget_and_over_guardrail_say_by_how_much_and_the_trace_writes_the_sum(api):
    """AC-4, AC-5, AC-6: $18,600 against a $15,000 budget; the CAC guardrail at $372 against $350; CAC as a sum."""
    acme(api)
    ads = row(line(api), "search-ads")
    spend = ads["numbers"]["spend"]
    assert (spend["budget"], spend["over_budget"], spend["budget_says"]) == (15000, 3600, "$3,600 over budget")
    assert ads["guardrail"]["breached"] and ads["guardrail"]["says"] == "$22 over guardrail"
    cac = ads["economics"]["cac"]
    assert cac["value"] == 372 and cac["from"] == ["spend", "new_customers"]
    assert cac["trace"].startswith("$18,600 spend (Ad spend sheet, synced ")
    assert cac["trace"].endswith(
        "÷ 50 new customers (Typed by hand) = $372 CAC; held to Search Ads CAC at most $350: $372, $22 over guardrail"
    )
    guard = next(k for k in line(api)["kpis"] if k["role"] == "guardrail")
    assert guard["breached"] and guard["over"] == 22


def test_an_earlier_month_reads_the_guardrail_kpi_as_it_was_then(api):
    """AC-5, AC-15 for a picked month: the guardrail KPI was $300 in the month before, under its $350 max, so that
    month's line, CAC trace and headline say nothing is over, while last month's still says $22 over."""
    ids = acme(api)
    before = GS._month(GS._index(M) - 1)
    post(api, f"kpis/{ids['cac']}/readings", {"value": 300, "period_end": f"{before}-28T00:00:00Z"})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": before, "new_customers": 40, "spend": 8000})
    view = line(api, month=before)
    ads = row(view, "search-ads")
    assert ads["guardrail"]["value"] == 300 and not ads["guardrail"]["breached"] and ads["guardrail"]["over"] is None
    assert "over guardrail" not in ads["economics"]["cac"]["trace"]
    assert "guardrail" not in view["headline"]["sentence"] and view["headline"]["over_guardrail"] == []
    guard = next(k for k in view["kpis"] if k["id"] == ids["cac"])
    assert guard["value"] == 300 and not guard["breached"]
    assert row(line(api), "search-ads")["guardrail"]["says"] == "$22 over guardrail"


def test_every_channel_together_counts_spend_from_named_channels_and_unknowns_customers(api):
    """AC-7: All channels' CAC is named channels' spend over all new customers, Unknown's among them."""
    acme(api)
    every = row(line(api), "all")
    assert every["numbers"]["new_customers"]["value"] == 142 and every["sources"] == []
    assert every["economics"]["cac"]["trace"] == (
        "$21,500 spend and agent cost from named channels only ÷ all 142 new customers, 14 of them from Unknown"
        " = $151 CAC"  # whole dollars from $10, as the page writes money
    )


def test_the_bot_that_owns_a_channel_links_it_to_an_input_kpi_and_a_member_may_not(api):
    """AC-10, AC-11: the owning bot sets the input KPI; a member, another bot or a KPI that does not exist is refused;
    unlinked, the row has no KPI and says so by null."""
    ops, finance = bot(api, "ops"), bot(api, "finance")
    post(api, "growth/channels", {"slug": "seo", "name": "SEO", "owner_ref": "ops"})
    goal = kpi(api, "Organic sign-ups", role="input")
    assert row(line(api), "seo")["input_kpi"] is None
    assert patch(api, "growth/channels/seo", {"input_kpi_id": goal}, token=ops)["input_kpi_id"] == goal
    assert row(line(api), "seo")["input_kpi"] == goal
    assert next(k for k in line(api)["kpis"] if k["id"] == goal)["moved_by"] == ["seo"]
    patch(api, "growth/channels/seo", {"input_kpi_id": None}, token=finance, expected=403)
    patch(api, "growth/channels/seo", {"input_kpi_id": None}, token="cara-test", expected=403)
    patch(api, "growth/channels/seo", {"input_kpi_id": "nope"}, token="ben-test", expected=404)
    assert patch(api, "growth/channels/seo", {"input_kpi_id": ""}, token="ben-test")["input_kpi_id"] is None


def test_channels_with_no_source_are_typed_by_hand_and_no_channel_draws_no_line(api):
    """AC-12, AC-13: with channels and nothing synced, the only source is Typed by hand; with no channel, no line."""
    assert line(api) is None
    post(api, "growth/channels", {"slug": "seo", "name": "SEO"})
    view = line(api)
    assert [s["name"] for s in view["sources"]] == ["Typed by hand"]
    assert [ch["slug"] for ch in view["channels"]] == ["seo", "all"]
    assert view["headline"]["sentence"] == ""  # nothing to say yet: never a 0


def test_a_month_too_young_for_day_30_reads_not_yet_never_zero(api):
    """AC-14: this month's customers are under 30 days old: kept day 30 and LTV:CAC are not yet."""
    post(api, "growth/channels", {"slug": "seo", "name": "SEO"})
    now = GS.this_month()
    put(api, "growth/cohorts", {"channel": "seo", "month": now, "new_customers": 9, "spend": 900})
    seo = row(line(api, month=now), "seo")
    assert seo["numbers"]["kept_d30"]["value"] is None and seo["numbers"]["kept_d30"]["status"] == "not_yet"
    assert seo["economics"]["ltv_cac"]["status"] == "not_yet"
    assert seo["economics"]["cac"]["value"] == 100


def test_a_kpi_the_reader_may_not_see_is_not_linked_on_their_line(api):
    """A channel's KPIs owned by a bot the reader may not read are no link and no column for that reader."""
    from backend import growth_line as GL

    ids = acme(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='bot:inbox' WHERE id IN (?,?)", (ids["new"], ids["cac"]))
    with api.app.state.store.read() as c:
        hidden = GL.line(c, M, {"bot:inbox"})
    ads = row(hidden, "search-ads")
    assert ads["input_kpi"] is None and ads["guardrail"] is None
    assert [k["id"] for k in hidden["kpis"]] == [ids["north"]]


def test_a_trace_opened_or_a_source_picked_is_counted_once_a_day_per_reader(api):
    """The measure: who used the line, per day, never each click."""
    post(api, "growth/line/used", {"use": "trace"})
    post(api, "growth/line/used", {"use": "trace"})
    post(api, "growth/line/used", {"use": "source"}, token="cara-test")
    post(api, "growth/line/used", {"use": "share"}, expected=422)
    with api.app.state.store.read() as c:
        rows = H._rows(c.execute("SELECT actor, use FROM growth_line_uses ORDER BY actor, use"))
    assert [(r["actor"], r["use"]) for r in rows] == [("human:ana", "trace"), ("human:cara", "source")]


@pytest.mark.usefixtures("classic_cards")
def test_the_funnel_shows_no_share_between_kpis_of_unlike_units(api):
    """AC-18: customers over dollars is no share; two counts still show theirs, whatever they count."""
    for name, unit, stage, value in (
        ("Ad spend", "$", "reach", 20000),
        ("Sign-ups", "", "acquire", 200),
        ("Activated", "", "activate", 50),
        ("Kept homes", "homes", "retain", 40),
    ):
        found = kpi(api, name, unit=unit, value=value)
        post(api, f"kpis/{found}", {"stage": stage})
    view = get(api, f"places/growth?month={M}")
    funnel = next(p["card"]["data"] for p in view["pins"] if p["card"]["template"] == "growth_funnel")
    conv = {s["stage"]: s["conv"] for s in funnel["stages"]}
    assert conv["acquire"] is None and conv["activate"] == 25.0 and conv["retain"] == 80.0


def test_a_number_computed_from_uploaded_customers_says_how_many_and_lists_no_typed_source(api, monkeypatch):
    """AC-8, AC-12: kept day 30 computed from 40 uploaded customers names the upload and the count; with nothing typed
    and no source, the line lists the upload alone, not "Typed by hand"."""
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/customers", {"csv": as_csv(september())})
    view = line(api, month="2026-09")
    kept = row(view, "search-ads")["numbers"]["kept_d30"]
    assert (kept["origin"], kept["computed_from"], kept["sources"]) == ("computed", 40, ["upload"])
    assert [s["id"] for s in view["sources"]] == ["upload"]


def test_a_month_in_progress_is_not_compared_with_a_whole_month(api):
    """AC-15: half of this month against all of last month is no change; last month against the one before still is."""
    acme(api)
    now = GS.this_month()
    put(api, "growth/cohorts", {"channel": "search-ads", "month": now, "new_customers": 20, "quality": "actual"})
    assert line(api, month=now)["headline"]["change_pct"] is None
    before = GS._month(GS._index(M) - 1)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": before, "new_customers": 71, "quality": "actual"})
    assert line(api)["headline"]["change_pct"] == 100  # 142 against 71
