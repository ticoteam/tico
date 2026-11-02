"""Growth (backend/growth.py): channels, sources, monthly cohorts and the unit economics they add up to, and the KPI tree.
Examples use Acme, a home-cleaning company."""

import pytest

from backend import growth as G
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401
from backend.tests.test_company import bot


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def add_channel(api, slug="google-search", name="Google Search", **more):
    return post(api, "growth/channels", {"slug": slug, "name": name, **more})


# ----------------------------------------------------------------------------- economics
def test_october_economics_for_google_search_and_the_unknown_row():
    conf = {"revenue_month": 90, "margin_pct": 40}
    google = {"channel": "google-search", "spend": 3400, "bot_cost": 260, "new_customers": 71,
              "kept_d30": 81, "kept_d90": 64, "kept_d180": 52}
    row = G.economics_row(google, conf)
    assert row["cac"] == 51.55
    assert 9 <= row["expected_months"] <= 14 and 320 <= row["ltv"] <= 500
    assert 6 <= row["ltv_cac"] <= 10 and 1.3 <= row["payback_months"] <= 1.6
    unknown = G.economics_row({"channel": None, "new_customers": 18, "kept_d30": 70}, conf)
    assert unknown["new_customers"] == 18 and unknown["cac"] is None and unknown["ltv"] is None
    total = G.economics_total([google, {"channel": None, "new_customers": 18, "kept_d30": 70}], conf)
    assert total["new_customers"] == 89 and total["spend"] == 3400 and total["cac"] == round(3660 / 89, 2)
    assert total["kept_d30"] == round((81 * 71 + 70 * 18) / 89, 2)


def test_expected_months_needs_d30_caps_at_sixty_and_uses_what_is_known():
    assert G.expected_months(None, 60, 50) is None
    assert G.expected_months(100, 100, 100) == G.LIFETIME_CAP
    only_d30 = G.expected_months(50)
    assert 1 < only_d30 < 3          # halves every month: about 0.75 to month 1, then a short geometric tail
    assert G.expected_months(81, None, 52) > G.expected_months(81, None, 40)


def test_economics_route_reads_the_month_and_flags_a_breached_guardrail(api):
    kpi = post(api, "kpis", {"name": "Refund rate", "unit": "%"})["kpi"]
    post(api, f"kpis/{kpi['id']}/readings", {"value": 7, "period_end": "2026-10-31T00:00:00Z"})
    add_channel(api, guardrail_kpi_id=kpi["id"], guardrail_max=5)
    put(api, "growth/settings", {"revenue_month": 90, "margin_pct": 40})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 3400, "bot_cost": 260,
                                "new_customers": 71, "kept_d30": 81, "kept_d90": 64, "kept_d180": 52,
                                "sources": {"spend": "Google Ads billing"}})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-09", "spend": 3000, "new_customers": 50})
    eco = get(api, "growth/economics")
    assert eco["month"] == "2026-10" and eco["months"] == ["2026-10", "2026-09"]
    assert [r["channel"] for r in eco["rows"]] == ["google-search", "unknown"]
    assert eco["rows"][0]["cac"] == 51.55 and eco["rows"][0]["sources"] == {"spend": "Google Ads billing"}
    assert eco["total"]["new_customers"] == 89 and eco["total"]["name"] == "All channels"
    assert eco["guardrails"] == [{"channel": "google-search", "kpi_id": kpi["id"], "kpi_name": "Refund rate",
                                  "value": 7.0, "max": 5.0, "breached": True}]
    assert get(api, "growth/economics?month=2026-09")["rows"][0]["cac"] == 60.0
    # The same channel and month again updates the row; only the fields sent change.
    put(api, "growth/cohorts", {"channel": None, "month": "2026-10", "kept_d30": 70})
    unknown = get(api, "growth/cohorts?channel=unknown")["cohorts"]
    assert len(unknown) == 1 and unknown[0]["new_customers"] == 18 and unknown[0]["kept_d30"] == 70


def test_economics_without_numbers_lists_each_active_channel_and_unknown_blank(api):
    add_channel(api)
    add_channel(api, slug="referrals", name="Referrals")
    add_channel(api, slug="flyers", name="Flyers")
    post(api, "growth/channels/flyers/retire", {})
    eco = get(api, "growth/economics")
    assert eco["month"] is None and eco["months"] == []
    assert [r["channel"] for r in eco["rows"]] == ["google-search", "referrals", "unknown"]
    assert all(r["spend"] is None and r["cac"] is None and r["quality"] is None for r in eco["rows"])
    assert eco["total"]["new_customers"] is None and eco["total"]["quality"] is None
    # A month without numbers, once another month has some, is blank too.
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-10", "new_customers": 4})
    assert [r["channel"] for r in get(api, "growth/economics")["rows"]] == ["referrals"]
    empty = get(api, "growth/economics?month=2026-11")
    assert empty["month"] == "2026-11" and len(empty["rows"]) == 3 and empty["rows"][1]["new_customers"] is None


# ----------------------------------------------------------------------------- permissions
def test_channel_and_cohort_permissions(api):
    ops, finance = bot(api, "ops"), bot(api, "finance")
    add_channel(api, owner_ref="ops", budget_month=3000)
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"}, token=ops, expected=403)
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"}, token="cara-test", expected=403)
    post(api, "growth/channels", {"slug": "google-search", "name": "X"}, token="ben-test", expected=409)
    changed = patch(api, "growth/channels/google-search", {"stage": "scaling", "why": "Cheapest bookings",
                                                           "budget_month": 4000}, token=ops)
    assert (changed["stage"], changed["budget_month"]) == ("scaling", 4000)
    assert patch(api, "growth/channels/google-search", {"name": "Mine"}, token=ops, expected=403)["error"]
    patch(api, "growth/channels/google-search", {"stage": "paused"}, token=finance, expected=403)
    patch(api, "growth/channels/google-search", {"name": "Google Ads"}, token="ben-test")
    assert put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 1}, token=finance)["spend"] == 1
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 1}, token="cara-test", expected=403)
    put(api, "growth/cohorts", {"channel": "nope", "month": "2026-10"}, token=finance, expected=404)
    post(api, "growth/channels/google-search/retire", {}, token=ops, expected=403)
    post(api, "growth/channels/google-search/retire", {})
    assert get(api, "growth/channels")["channels"] == []
    assert get(api, "growth/channels?include_retired=true", token="cara-test")["channels"][0]["retired_at"]


def test_lists_and_settings_say_whether_the_caller_may_edit(api):
    ops = bot(api, "ops")
    for path in ("growth/channels", "growth/sources", "growth/settings"):
        assert get(api, path)["may_edit"] is True and get(api, path, token="ben-test")["may_edit"] is True, path
        assert get(api, path, token="cara-test")["may_edit"] is False and get(api, path, token=ops)["may_edit"] is False


def test_sources_list_conflicts_and_only_admins_write(api):
    post(api, "growth/sources", {"truth_for": "Revenue", "system": "Stripe", "reach_kind": "mcp"}, token=bot(api, "ops"),
         expected=403)
    first = post(api, "growth/sources", {"truth_for": "Revenue", "system": "Stripe", "reach_kind": "mcp"})
    post(api, "growth/sources", {"truth_for": "revenue", "system": "QuickBooks", "reach_kind": "credential"},
         token="ben-test")
    assert [x.lower() for x in get(api, "growth/sources", token="cara-test")["conflicts"]] == ["revenue"]
    post(api, f"growth/sources/{first['id']}/retire", {})
    assert get(api, "growth/sources")["conflicts"] == []


def test_the_total_weighs_profit_per_cohort_and_a_hidden_kpis_guardrail_stays_hidden(api):
    # Rich-but-thin and poor-but-fat cohorts: profit per customer-month is 10 and 9, so 9.5 together, never
    # the 55 x 50% = 27.5 that averaging revenue and margin apart would invent.
    rows = [{"channel": "a", "spend": 950, "new_customers": 10, "kept_d30": 80, "revenue_month": 100, "margin_pct": 10},
            {"channel": "b", "spend": 950, "new_customers": 10, "kept_d30": 80, "revenue_month": 10, "margin_pct": 90}]
    total = G.economics_total(rows, {})
    assert total["cac"] == 95.0 and total["payback_months"] == 10.0
    assert total["ltv"] == round(9.5 * total["expected_months"], 2)
    kpi = post(api, "kpis", {"name": "Refund rate"})["kpi"]
    add_channel(api, guardrail_kpi_id=kpi["id"], guardrail_max=5)
    with api.app.state.store.transaction() as c:
        assert [g["kpi_id"] for g in G.guardrails(c)] == [kpi["id"]]
        c.execute("UPDATE kpis SET owner_id='bot:acme-helper' WHERE id=?", (kpi["id"],))
        assert G.guardrails(c, {"bot:acme-helper"}) == []


# ----------------------------------------------------------------------------- KPI tree
def test_kpi_tree_fields_and_one_north_star(api):
    active = post(api, "kpis", {"name": "Active customers"})["kpi"]
    booked = post(api, "kpis", {"name": "Bookings"})["kpi"]
    post(api, f"kpis/{active['id']}", {"growth_role": "north_star"})
    post(api, f"kpis/{booked['id']}", {"parent_kpi_id": active["id"], "growth_role": "input", "stage": "acquire",
                                       "loop_name": "Referral", "loop_step": 2})
    tree = {k["id"]: k for k in get(api, "growth/kpis")["kpis"]}
    assert tree[booked["id"]]["parent_kpi_id"] == active["id"] and tree[booked["id"]]["loop_step"] == 2
    post(api, f"kpis/{active['id']}", {"parent_kpi_id": booked["id"]}, expected=422)      # a loop in the tree
    post(api, f"kpis/{booked['id']}", {"growth_role": "north_star"})
    tree = {k["id"]: k for k in get(api, "growth/kpis")["kpis"]}
    assert tree[active["id"]]["growth_role"] is None and tree[booked["id"]]["growth_role"] == "north_star"
    post(api, f"kpis/{booked['id']}", {"stage": None})
    assert {k["id"]: k for k in get(api, "growth/kpis")["kpis"]}[booked["id"]]["stage"] is None


def test_a_kpi_says_whether_the_caller_may_edit_it_and_an_unreadable_one_has_no_name(api):
    kpi = post(api, "kpis", {"name": "Refund rate"})["kpi"]
    hidden = post(api, "kpis", {"name": "Inbox depth"})["kpi"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='bot:inbox' WHERE id=?", (hidden["id"],))   # the fixture restricts inbox to Ana
    may = lambda token: {k["id"]: k["may_edit"] for k in get(api, "growth/kpis", token)["kpis"]}   # noqa: E731
    assert may("ana-test") == {kpi["id"]: True, hidden["id"]: True}
    assert may("cara-test") == {kpi["id"]: False} and may(bot(api, "ops"))[kpi["id"]] is False


def test_a_member_cannot_move_the_north_star_off_a_kpi_they_may_not_edit_nor_read_a_hidden_one(api):
    company = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{company['id']}", {"growth_role": "north_star"})
    own = post(api, "kpis", {"name": "Cara's signups"})["kpi"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='human:cara' WHERE id=?", (own["id"],))
    refused = post(api, f"kpis/{own['id']}", {"growth_role": "north_star"}, token="cara-test", expected=403)
    assert "Active customers" not in str(refused)
    roles = {k["id"]: k["growth_role"] for k in get(api, "growth/kpis")["kpis"]}
    assert roles[company["id"]] == "north_star" and roles[own["id"]] is None
    # A bot's North Star is named only to those who may read that bot (the fixture restricts inbox to Ana).
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='bot:inbox' WHERE id=?", (company["id"],))
    named = {k["id"]: k.get("name") for k in get(api, "growth/kpis", "cara-test")["kpis"]}
    assert named.get(company["id"]) is None
