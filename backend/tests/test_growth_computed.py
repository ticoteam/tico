"""Growth computes retention and LTV from customers (backend/growth_customers.py, backend/growth_computed.py).

Synthetic customers only. The examples follow the spec's (page growth-computed-cohorts, v2): a search-ads September 2026
cohort of 40, read on 2026-11-13."""

import csv
import io
import logging
import time
from datetime import date, timedelta

import pytest

from backend import growth_computed as GX
from backend import growth_customers as GC
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.company import bot
from backend.tests.helpers.growth_computed import HEADER, TODAY, as_csv, customer, september


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)


def load(api, rows, token="ana-test", expected=200, header=HEADER):
    return post(api, "growth/customers", {"csv": as_csv(rows, header)}, token=token, expected=expected)


def channel(api, slug="search-ads", name="Search ads"):
    post(api, "growth/channels", {"slug": slug, "name": name})


def cohort(result, value, period):
    return next(r for r in result["cohorts"] if r["value"] == value and r["period"] == period)


# ----------------------------------------------------------------------------- loading, and what is refused
def test_an_admin_loads_120_customers_and_sees_new_customers_by_channel_per_month(api):  # AC-1
    channel(api)
    channel(api, "referrals", "Referrals")
    rows = [
        customer(n, f"2026-{8 + n % 3:02d}-0{1 + n % 9}", "search-ads" if n % 2 else "referrals") for n in range(120)
    ]
    result = load(api, rows, token="ben-test")
    assert result["customers"] == 120 and result["removed"] == 0 and result["unknown_channel"] == 0
    counts = {(r["month"], r["channel"]): r["new_customers"] for r in result["new_by_month"]}
    assert sum(counts.values()) == 120 and set(m for m, _ in counts) == {"2026-08", "2026-09", "2026-10"}
    eco = get(api, "growth/economics?month=2026-09")
    assert {r["channel"]: r["new_customers"] for r in eco["rows"]} == {
        "referrals": counts[("2026-09", "referrals")],
        "search-ads": counts[("2026-09", "search-ads")],
    }
    assert eco["months"] == ["2026-10", "2026-09", "2026-08"]


def test_a_column_outside_the_list_is_refused_by_name_and_nothing_loads(api):  # AC-2
    rows = [{**customer(1, "2026-09-01"), "email": "x"}]
    refused = load(api, rows, header=HEADER + ["email", "name"], expected=422)["error"]
    assert refused["rule"] == "columns" and "email, name" in refused["detail"]
    assert "customer key, signed up, became customer" in refused["detail"]
    assert get(api, "growth/computed")["cohorts"] == []


def test_keys_that_are_emails_or_phone_numbers_are_refused_without_showing_a_value(api):  # AC-2a
    for key in ("ana.lopez@example.com", "+44 20 7946 0958", "(415) 555-0134"):
        rows = [customer(n, "2026-09-01") for n in range(5)] + [{**customer(9, "2026-09-02"), "customer_key": key}]
        refused = load(api, rows, expected=422)["error"]
        assert refused["rule"] == "personal_key" and "email addresses or phone numbers" in refused["detail"]
        assert key not in refused["detail"] and "row 7" in refused["detail"]
    # A database id of digits, or with one dash, is a key, not a phone number.
    for key in ("1004217", "1234-5678"):
        assert load(api, [{**customer(1, "2026-09-01"), "customer_key": key}])["customers"] == 1
    assert load(api, [{**customer(1, "2026-09-01"), "customer_key": "415-555-0134"}], expected=422)["error"][
        "rule"
    ] == ("personal_key")


def test_a_label_column_with_a_value_per_row_is_refused_as_an_identifier(api):  # AC-2b
    rows = [customer(n, "2026-09-01", segment=f"Person {n}") for n in range(60)]
    refused = load(api, rows, expected=422)["error"]
    assert refused["rule"] == "identifier_label" and "segment column looks like an identifier" in refused["detail"]
    # More than one label per two rows is refused too, under 50.
    few = [customer(n, "2026-09-01", plan=["pro", "basic", "team"][n % 3]) for n in range(4)]
    assert load(api, few, expected=422)["error"]["rule"] == "identifier_label"
    long = [customer(n, "2026-09-01", segment="x" * 41 if n == 0 else "agency") for n in range(10)]
    assert load(api, long, expected=422)["error"]["rule"] == "long_label"


def test_a_refused_load_logs_the_rule_and_column_and_no_row_values(api, caplog):  # AC-2c
    caplog.set_level(logging.DEBUG)
    secret = "maria.garcia@example.org"
    rows = [customer(1, "2026-09-01"), {**customer(2, "2026-09-01"), "customer_key": secret}]
    load(api, rows, expected=422)
    bad_date = [customer(1, "2026-09-01"), customer(2, "1st of Sept, Maria's shop")]
    detail = load(api, bad_date, expected=422)["error"]["detail"]
    assert "became customer is not a date" in detail and "Maria" not in detail
    assert secret not in caplog.text and "Maria" not in caplog.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM growth_customers").fetchone()[0] == 0
        events = " ".join(r[0] or "" for r in c.execute("SELECT data FROM history_events"))
    assert secret not in events


def test_only_the_owner_and_admins_load_or_delete_customer_facts(api):  # AC-3
    for token in ("cara-test", bot(api, "ops")):
        refused = load(api, [customer(1, "2026-09-01")], token=token, expected=403)["error"]
        assert refused["detail"] == "Only the owner or an admin loads customer facts"
        assert post(api, "growth/customers/delete", {}, token=token, expected=403)["error"]["detail"] == (
            "Only the owner or an admin deletes customer facts"
        )


# ----------------------------------------------------------------------------- retention
def test_day_30_shows_75_percent_computed_from_40_customers_everywhere(api):  # AC-4
    channel(api)
    load(api, september())
    figure = cohort(get(api, "growth/computed?month=2026-09", token="cara-test"), "search-ads", "2026-09")["kept_d30"]
    assert figure == {
        "value": 75.0,
        "status": "ok",
        "origin": "computed",
        "count": 30,
        "denominator": 40,
        "fills_on": "2026-10-30",
    }
    row = get(api, "growth/cohorts?channel=search-ads&month=2026-09")["cohorts"][0]
    assert row["kept_d30"] == 75.0 and row["computed_or_typed"]["kept_d30"] == "computed" and row["computed_from"] == 40
    eco = get(api, "growth/economics?month=2026-09")["rows"][0]
    assert (
        eco["kept_d30"] == 75.0
        and eco["computed_from"] == 40
        and eco["computed_or_typed"]["new_customers"] == "computed"
    )


def test_day_90_is_not_yet_with_the_date_it_fills_never_zero(api):  # AC-5
    channel(api)
    load(api, september())
    figure = cohort(get(api, "growth/computed"), "search-ads", "2026-09")["kept_d90"]
    assert figure["value"] is None and figure["status"] == "not_yet" and figure["fills_on"] == "2026-12-29"
    row = get(api, "growth/cohorts?month=2026-09")["cohorts"][0]
    assert row["kept_d90"] is None and row["status"]["kept_d90"] == "not_yet"
    assert row["fills_on"]["kept_d90"] == "2026-12-29"


def test_day_90_fills_by_itself_when_it_comes_due_and_ltv_uses_it(api, monkeypatch):  # AC-6
    channel(api)
    put(api, "growth/settings", {"revenue_month": 50, "margin_pct": 80})
    rows = september()
    for row in rows[:10]:  # ten of the thirty kept at day 30 churn on day 60
        row["churned"] = (date.fromisoformat(row["became_customer"]) + timedelta(days=60)).isoformat()
    load(api, rows)
    before = get(api, "growth/economics?month=2026-09")["rows"][0]
    assert before["kept_d90"] is None and before["ltv"] is not None
    monkeypatch.setattr(GX, "today", lambda conf: date(2026, 12, 29))
    after = get(api, "growth/economics?month=2026-09")["rows"][0]
    assert after["kept_d90"] == 50.0 and after["computed_or_typed"]["kept_d90"] == "computed"
    assert after["ltv"] != before["ltv"]  # the tail now decays at the day-30-to-90 rate, not the first month's


def test_a_cohort_of_19_shows_counts_and_too_few_and_the_minimum_floors_at_5(api):  # AC-7
    channel(api)
    load(api, september(kept=12, total=19))
    view = cohort(get(api, "growth/computed"), "search-ads", "2026-09")
    assert view["too_few"] is True and view["kept_d30"]["status"] == "too_few"
    assert view["kept_d30"]["value"] is None and (view["kept_d30"]["count"], view["kept_d30"]["denominator"]) == (
        12,
        19,
    )
    assert view["ltv"]["status"] == "too_few" and view["revenue_month"]["status"] in ("too_few", "missing")
    assert put(api, "growth/settings", {"min_cohort": 4}, expected=422)
    put(api, "growth/settings", {"min_cohort": 5})
    assert cohort(get(api, "growth/computed"), "search-ads", "2026-09")["kept_d30"]["value"] == 63.2


def test_cut_by_segment_gives_each_its_own_figures_and_says_spend_is_not_split(api):  # AC-8
    channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "spend": 4000})
    rows = september(total=80, kept=60)
    for n, row in enumerate(rows):
        row["segment"] = "agency" if n % 2 else "studio"
    load(api, rows)
    result = get(api, "growth/computed?by=segment&month=2026-09")
    assert result["cac_note"] == "spend is not split by segment"
    agency, studio = cohort(result, "agency", "2026-09"), cohort(result, "studio", "2026-09")
    assert agency["new_customers"]["value"] == studio["new_customers"]["value"] == 40
    assert agency["cac"]["status"] == agency["payback_months"]["status"] == "not_split"
    by_channel = cohort(get(api, "growth/computed?month=2026-09"), "search-ads", "2026-09")
    assert by_channel["cac"] == {"value": 50.0, "status": "ok", "origin": "computed"}


def test_cut_by_plan_carries_count_denominator_and_computed(api):  # AC-9
    channel(api)
    rows = september(total=80, kept=60)
    for n, row in enumerate(rows):
        row["plan"] = "pro" if n < 40 else "basic"
    load(api, rows)
    result = get(api, "growth/computed?by=plan", token=bot(api, "ops"))
    for plan in ("pro", "basic"):
        figure = cohort(result, plan, "2026-09")["kept_d30"]
        assert figure["origin"] == "computed" and figure["denominator"] == 40 and figure["count"] is not None


def test_weekly_shows_new_customers_and_retention_and_says_spend_is_monthly(api):  # AC-10
    channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "spend": 4000})
    load(api, september())
    result = get(api, "growth/computed?grain=week")
    weeks = [r for r in result["cohorts"] if r["value"] == "search-ads"]
    assert [w["period"] for w in weeks][-1] == "2026-08-31" and sum(w["new_customers"]["value"] for w in weeks) == 40
    assert all(w["cac"]["status"] == "spend_monthly" for w in weeks) and result["cac_note"] == "spend is monthly"
    assert all(w["kept_d30"]["count"] is not None for w in weeks)


# ----------------------------------------------------------------------------- the trace
def test_a_figure_opens_to_its_rule_and_every_key_split_still_and_churned(api):  # AC-12
    channel(api)
    load(api, september())
    figure = cohort(
        get(api, "growth/computed?customers=true&month=2026-09", token="ben-test"), "search-ads", "2026-09"
    )["kept_d30"]
    assert figure["rule"] == "became customer 2026-09-01 to 2026-09-30, channel search-ads, still paying on day 30"
    still, churned = figure["customers"]["still"], figure["customers"]["churned"]
    assert len(still) == figure["count"] == 30 and len(churned) == 10 and len(still) + len(churned) == 40
    assert churned[0] == {"key": "cus_1030", "churned": "2026-09-11"}
    assert set(churned[0]) == {"key", "churned"}  # keys only, nothing else about the person


def test_without_a_grant_a_member_sees_counts_and_the_rule_and_no_keys(api):  # AC-12a
    channel(api)
    load(api, september())
    ops = bot(api, "ops")
    for token in ("cara-test", ops):
        result = get(api, "growth/computed?customers=true&month=2026-09", token=token)
        figure = cohort(result, "search-ads", "2026-09")["kept_d30"]
        assert result["keys_visible"] is False and "customers" not in figure
        assert figure["count"] == 30 and figure["rule"].endswith("still paying on day 30")
    put(api, "growth/settings", {"key_readers": ["ops"]})
    granted = get(api, "growth/computed?customers=true&month=2026-09", token=ops)
    assert (
        granted["keys_visible"] is True
        and len(cohort(granted, "search-ads", "2026-09")["kept_d30"]["customers"]["still"]) == 30
    )


# ----------------------------------------------------------------------------- computed beside typed
def test_computed_replaces_a_typed_estimate(api):  # AC-13
    channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "kept_d30": 60, "quality": "estimate"})
    load(api, september())
    row = get(api, "growth/cohorts?month=2026-09")["cohorts"][0]
    assert row["kept_d30"] == 75.0 and row["computed_or_typed"]["kept_d30"] == "computed" and row["typed"] == {}


def test_a_typed_actual_that_differs_is_kept_beside_the_computed_figure(api):  # AC-14 (the owner's ask is part 3)
    channel(api)
    put(
        api,
        "growth/cohorts",
        {"channel": "search-ads", "month": "2026-09", "kept_d30": 70, "spend": 900, "quality": "actual"},
    )
    load(api, september())
    row = get(api, "growth/cohorts?month=2026-09")["cohorts"][0]
    assert row["kept_d30"] == 75.0 and row["typed"]["kept_d30"] == 70 and row["computed_or_typed"]["spend"] == "typed"


def test_a_channel_with_no_customer_facts_reads_as_before(api):  # AC-15
    channel(api)
    channel(api, "referrals", "Referrals")
    typed = {"channel": "referrals", "month": "2026-09", "new_customers": 12, "kept_d30": 81, "spend": 300}
    put(api, "growth/cohorts", typed)
    before = get(api, "growth/economics?month=2026-09")["rows"]
    load(api, september())
    after = get(api, "growth/economics?month=2026-09")["rows"]
    referrals = next(r for r in after if r["channel"] == "referrals")
    assert referrals == next(r for r in before if r["channel"] == "referrals")
    assert "computed_from" not in referrals and referrals["computed_or_typed"]["kept_d30"] == "typed"


# ----------------------------------------------------------------------------- loads replace, delete, spells
def test_a_reloaded_churn_updates_retention_and_counts_the_customer_once(api):  # AC-16
    channel(api)
    rows = september()
    load(api, rows)
    rows[0]["churned"] = "2026-09-05"
    load(api, rows)
    view = cohort(get(api, "growth/computed"), "search-ads", "2026-09")
    assert view["new_customers"]["value"] == 40 and view["kept_d30"]["count"] == 29


def test_a_customer_left_out_of_the_next_load_is_gone(api):  # AC-16a
    channel(api)
    rows = september()
    load(api, rows)
    result = load(api, rows[1:])
    assert result["removed"] == 1 and result["customers"] == 39
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM growth_customers WHERE customer_key='cus_1000'").fetchone()[0] == 0
    assert cohort(get(api, "growth/computed"), "search-ads", "2026-09")["new_customers"]["value"] == 39


def test_deleting_all_customer_facts_falls_back_to_typed_values(api):  # AC-16b
    channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-09", "kept_d30": 70})
    load(api, september())
    assert post(api, "growth/customers/delete", {}, token="ben-test")["deleted"] == 40
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM growth_customers").fetchone()[0] == 0
    row = get(api, "growth/cohorts?month=2026-09")["cohorts"][0]
    assert row["kept_d30"] == 70 and row["computed_or_typed"]["kept_d30"] == "typed" and "computed_from" not in row


def test_a_computed_month_with_no_typed_row_has_every_key_a_typed_row_has(api):
    channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-08", "new_customers": 3})
    load(api, september())
    rows = {r["month"]: r for r in get(api, "growth/cohorts")["cohorts"]}
    assert rows["2026-09"]["id"] is None and set(rows["2026-08"]) <= set(rows["2026-09"])
    # Each computed figure's origin names where it came from (part 4: computed beside #304's origins).
    origins = rows["2026-09"]["origins"]
    assert set(origins) == {"new_customers", "kept_d30", "kept_d90", "kept_d180"}
    assert {o["origin"] for o in origins.values()} == {"computed"}


def test_a_customer_who_came_back_starts_a_new_spell_and_the_first_keeps_its_churn(api):  # AC-17
    channel(api)
    rows = september() + [
        customer(7, "2026-08-03", churned="2026-08-20"),
        customer(7, "2026-09-10"),
    ]
    load(api, rows)
    assert (
        get(api, "growth/computed?customers=true", expected=422)["error"]["detail"]
        == "customers needs a month or a week"
    )
    result = {
        "cohorts": [
            *get(api, "growth/computed?customers=true&month=2026-08")["cohorts"],
            *get(api, "growth/computed?customers=true&month=2026-09")["cohorts"],
        ]
    }
    august, sept = cohort(result, "search-ads", "2026-08"), cohort(result, "search-ads", "2026-09")
    assert august["new_customers"]["value"] == 1 and august["kept_d30"]["customers"]["churned"][0]["key"] == "cus_7"
    assert sept["new_customers"]["value"] == 41 and "cus_7" in sept["kept_d30"]["customers"]["still"]
    overlapping = [customer(7, "2026-08-03"), customer(7, "2026-09-10")]
    assert load(api, overlapping, expected=422)["error"]["rule"] == "overlapping_spells"


def test_a_customer_keeps_the_channel_they_first_came_from(api):
    channel(api)
    channel(api, "referrals", "Referrals")
    rows = september()
    load(api, rows)
    rows[0]["channel"] = "referrals"
    load(api, rows)
    assert cohort(get(api, "growth/computed"), "search-ads", "2026-09")["new_customers"]["value"] == 40


def test_a_sunday_late_evening_counts_in_its_monday_week_and_its_month(api):  # AC-19
    channel(api)
    put(api, "growth/settings", {"time_zone": "America/Los_Angeles"})
    load(api, [customer(1, "2026-11-01T23:30:00-08:00"), customer(2, "2026-11-02T07:30:00Z")])
    # The second is 07:30 on Monday in UTC, and still 23:30 on Sunday in Los Angeles.
    weeks = get(api, "growth/computed?grain=week")["cohorts"]
    assert [(w["period"], w["new_customers"]["value"]) for w in weeks] == [("2026-10-26", 2)]
    months = get(api, "growth/computed")["cohorts"]
    assert [(m["period"], m["new_customers"]["value"]) for m in months] == [("2026-11", 2)]


def test_size_100000_customers_over_36_months_compute_well_inside_60_seconds():  # AC-18
    start = date(2023, 11, 1)
    facts = []
    for n in range(100_000):
        became = start + timedelta(days=n % 1095)
        facts.append(
            GX.Fact(
                f"cus_{n}",
                became,
                became + timedelta(days=20 + n % 400) if n % 3 else None,
                ["search-ads", "referrals", "social", "other"][n % 4],
                ["agency", "studio", "solo"][n % 3],
                ["pro", "basic"][n % 2],
            )
        )
    begun = time.monotonic()
    for cut in GX.CUTS:
        for grain in GX.GRAINS:
            GX.compute(facts, TODAY, cut=cut, grain=grain, channels={"search-ads", "referrals", "social"})
    assert time.monotonic() - begun < 60


def test_pure_parse_reads_human_headers_and_refuses_an_empty_file():
    headers, rows = GC.parse_csv("Customer Key,Became customer,Channel,Revenue per month\ncus_1,2026-09-01,x,12\n")
    spells = GC.check(headers, rows, GC.zone("UTC"))
    assert spells[0].customer_key == "cus_1" and spells[0].revenue_month == 12.0
    with pytest.raises(Exception) as refused:
        GC.parse_csv("\n\n")
    assert "no header" in str(refused.value)


def test_weekly_cohorts_asked_for_by_month_hold_each_whole_week(api):  # AC-10 with a month (Review 1)
    channel(api)
    # 5 customers on Monday 31 August and 30 across September: the week of 31 August spans both months.
    rows = [customer(n, "2026-08-31") for n in range(5)] + september()
    load(api, rows)
    by_week = cohort(get(api, "growth/computed?grain=week&week=2026-09-01"), "search-ads", "2026-08-31")
    in_month = cohort(get(api, "growth/computed?grain=week&month=2026-09"), "search-ads", "2026-08-31")
    assert in_month["new_customers"] == by_week["new_customers"]
    assert in_month["new_customers"]["value"] == 5 + sum(1 for r in september() if r["became_customer"] <= "2026-09-06")


def test_a_revenue_of_infinity_is_refused_like_any_other_non_number(api):
    rows = [customer(n, "2026-09-01", revenue="inf" if n == 3 else "40") for n in range(4)]
    refused = load(api, rows, expected=422)["error"]
    assert refused["rule"] == "number" and "revenue per month is not a number" in refused["detail"]
