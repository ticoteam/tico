"""The Growth page's numbers (backend/growth_numbers.py): the months' totals and each channel's, the KPI tree with its
readings, and the marketing tests that started or finished. Examples use Acme, a home-cleaning company."""

from backend import growth as G
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401
from backend.tests.test_company import bot
from backend.tests.test_openapi_v2 import conforms

ECONOMICS = ("spend", "bot_cost", "new_customers", "cac", "kept_d30", "kept_d90", "kept_d180", "expected_months", "ltv",
             "ltv_cac", "payback_months", "quality")
MONEY = ("revenue_month", "margin_pct")
FIGURES = ECONOMICS + MONEY


def add_channel(api, slug="google-search", name="Google Search", **more):
    return post(api, "growth/channels", {"slug": slug, "name": name, **more})


def add_fay(api):
    """Fay: a company person on no team, owning no task, KPI or channel."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('fay','Fay','fay@acme.example')")


def move(api, task_id, step, token="ana-test"):
    """A task's step moved the way its page moves it: at the version it was read at."""
    version = get(api, "tasks/" + task_id, token)["task"]["version"]
    return post(api, "tasks/" + task_id, {"version": version, "step": step}, token)


def marked(api, token="ana-test"):
    return [m["task_id"] for m in get(api, "growth/numbers", token)["markers"]]


def test_an_empty_company_reads_empty_numbers(api):
    assert get(api, "growth/numbers") == {"months": [], "series": [], "channels": [],
                                          "kpis": {"north_star": None, "inputs": [], "guardrails": [], "other": []},
                                          "markers": [], "may_edit": True}
    add_channel(api)
    assert get(api, "growth/numbers")["channels"] == [
        {"slug": "google-search", "name": "Google Search", "stage": "idea", "owner_ref": None, "series": []}]


def test_a_figure_not_recorded_is_null_never_zero(api):
    add_channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "new_customers": 12})
    total = get(api, "growth/numbers")["series"][0]
    assert total["new_customers"] == 12 and total["quality"] == "actual"
    assert all(total[key] is None for key in FIGURES if key not in ("new_customers", "quality"))


def test_the_total_is_economics_total_over_the_months_cohorts(api):
    put(api, "growth/settings", {"revenue_month": 90, "margin_pct": 40})
    add_channel(api)
    add_channel(api, slug="referrals", name="Referrals")
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 3400, "bot_cost": 260,
                                "new_customers": 71, "kept_d30": 81, "kept_d90": 64, "kept_d180": 52})
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-10", "spend": 500, "new_customers": 20,
                                "kept_d30": 70, "revenue_month": 120, "margin_pct": 30})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18, "kept_d30": 70})
    with api.app.state.store.read() as c:
        expected = G.economics_total(G.cohorts(c, month="2026-10"), G.settings(c))
        money = G.aggregate(G.cohorts(c, month="2026-10"), G.settings(c))
        referrals_own = G.aggregate(G.cohorts(c, channel_slug="referrals", month="2026-10"), G.settings(c))
    numbers = get(api, "growth/numbers")
    total = numbers["series"][0]
    assert {key: total[key] for key in ECONOMICS} == {key: expected[key] for key in ECONOMICS}
    assert {key: total[key] for key in MONEY} == {key: money[key] for key in MONEY} == {"revenue_month": 95.5,
                                                                                       "margin_pct": 37.7}
    assert total["new_customers"] == 109 and total["cac"] == round((3400 + 500 + 260) / 109, 2)
    # Each channel's own cohort: referrals sets its own revenue and margin, google search takes the settings.
    referrals = next(ch for ch in numbers["channels"] if ch["slug"] == "referrals")["series"][0]
    assert {key: referrals[key] for key in MONEY} == {key: referrals_own[key] for key in MONEY} == {
        "revenue_month": 120, "margin_pct": 30}
    google = next(ch for ch in numbers["channels"] if ch["slug"] == "google-search")["series"][0]
    assert (google["revenue_month"], google["margin_pct"]) == (90, 40)


def test_the_months_are_the_newest_with_cohorts_oldest_first_and_limited_to_one_to_twenty_four(api):
    add_channel(api)
    for month in ("2026-06", "2026-07", "2026-08", "2026-09", "2026-10"):
        put(api, "growth/cohorts", {"channel": "google-search", "month": month, "new_customers": 1})
    every = ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert get(api, "growth/numbers")["months"] == every
    recent = get(api, "growth/numbers?months=2")
    assert recent["months"] == ["2026-09", "2026-10"] == [s["month"] for s in recent["series"]]
    assert [s["month"] for s in recent["channels"][0]["series"]] == ["2026-09", "2026-10"]
    assert get(api, "growth/numbers?months=24")["months"] == every
    get(api, "growth/numbers?months=0", expected=422)
    get(api, "growth/numbers?months=25", expected=422)


def test_channels_are_live_in_their_order_each_with_its_own_months_and_unknown_last(api):
    add_channel(api, slug="seo", name="SEO", sort=2)
    add_channel(api, slug="google-search", name="Google Search", sort=1)
    add_channel(api, slug="flyers", name="Flyers", sort=3)
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-09", "new_customers": 5})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "new_customers": 6})
    put(api, "growth/cohorts", {"channel": "flyers", "month": "2026-10", "new_customers": 2})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 3})
    post(api, "growth/channels/flyers/retire", {})
    numbers = get(api, "growth/numbers")
    assert [(ch["slug"], [s["month"] for s in ch["series"]]) for ch in numbers["channels"]] == [
        ("google-search", ["2026-09", "2026-10"]), ("seo", []), ("unknown", ["2026-10"])]
    assert numbers["channels"][2]["name"] == "Unknown"
    # A retired channel's customers stay in the total, as its cohorts do.
    assert [s["new_customers"] for s in numbers["series"]] == [5, 11]


def test_kpis_group_by_growth_role_and_carry_their_last_24_readings_for_whoever_may_read_them(api):
    north = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{north['id']}", {"growth_role": "north_star"})
    booked = post(api, "kpis", {"name": "Bookings"})["kpi"]
    post(api, f"kpis/{booked['id']}", {"parent_kpi_id": north["id"], "growth_role": "input", "stage": "acquire",
                                       "loop_name": "Referral", "loop_step": 2})
    triage = post(api, "kpis", {"name": "Inbox triage"})["kpi"]
    post(api, f"kpis/{triage['id']}", {"growth_role": "input"})
    refunds = post(api, "kpis", {"name": "Refund rate", "unit": "%"})["kpi"]
    post(api, f"kpis/{refunds['id']}", {"growth_role": "guardrail"})
    signups = post(api, "kpis", {"name": "Sign-ups"})["kpi"]
    post(api, f"kpis/{signups['id']}", {"stage": "refer"})
    shares = post(api, "kpis", {"name": "Shares"})["kpi"]
    post(api, f"kpis/{shares['id']}", {"loop_name": "Referral", "loop_step": 3})
    post(api, "kpis", {"name": "Inbox depth"})
    with api.app.state.store.transaction() as c:
        # The fixture restricts the inbox bot to Ana.
        c.execute("UPDATE kpis SET owner_id='bot:inbox' WHERE id=?", (triage["id"],))
    for day in range(1, 27):
        post(api, f"kpis/{booked['id']}/readings", {"value": day, "period_end": f"2026-10-{day:02d}T00:00:00Z"})
    kpis = get(api, "growth/numbers")["kpis"]
    assert kpis["north_star"]["name"] == "Active customers" and kpis["north_star"]["series"] == []
    assert [k["name"] for k in kpis["inputs"]] == ["Bookings", "Inbox triage"]
    assert [k["name"] for k in kpis["guardrails"]] == ["Refund rate"]
    assert [k["name"] for k in kpis["other"]] == ["Sign-ups", "Shares"]
    bookings = kpis["inputs"][0]
    assert (bookings["parent_kpi_id"], bookings["stage"], bookings["loop_name"], bookings["loop_step"]) == (
        north["id"], "acquire", "Referral", 2)
    assert bookings["latest"] == 26 and [p["value"] for p in bookings["series"]] == list(range(3, 27))
    assert bookings["series"][-1]["at"].startswith("2026-10-26")
    assert [k["name"] for k in get(api, "growth/numbers", "cara-test")["kpis"]["inputs"]] == ["Bookings"]
    assert "Inbox depth" not in str(kpis)


def test_a_kpi_series_is_the_sparkline_rule_one_value_per_period_without_partials_or_superseded_readings(api):
    kpi = post(api, "kpis", {"name": "Bookings"})["kpi"]
    post(api, f"kpis/{kpi['id']}", {"growth_role": "input"})

    def log(value, period, **more):
        return post(api, f"kpis/{kpi['id']}/readings", {"value": value, "period_end": period, **more})["reading"]
    log(1, "2026-10-01T00:00:00Z")
    log(2, "2026-10-02T00:00:00Z", quality="partial")
    first = log(3, "2026-10-03T00:00:00Z")
    # A correction names the reading it supersedes, which then drops out of the series.
    log(30, "2026-10-03T00:00:00Z", supersedes=first["id"], note="Recounted the day's bookings")
    log(4, "2026-10-04T00:00:00Z")
    log(40, "2026-10-04T00:00:00Z")   # a second reading of the same period: the later one is the value
    series = get(api, "growth/numbers")["kpis"]["inputs"][0]["series"]
    assert series == [{"at": "2026-10-01T00:00:00.000000Z", "value": 1},
                      {"at": "2026-10-03T00:00:00.000000Z", "value": 30},
                      {"at": "2026-10-04T00:00:00.000000Z", "value": 40}]


def test_a_test_gives_a_started_and_a_finished_marker_newest_first(api):
    tid = post(api, "tasks", {"title": "Weekend discount", "body": "10% off Saturdays.", "owner_id": "ops",
                              "type": "idea"})["id"]
    move(api, tid, "idea-doing")
    move(api, tid, "idea-won")
    found = get(api, "growth/numbers")["markers"]
    assert [(m["kind"], m["outcome"], m["task_id"], m["title"]) for m in found] == [
        ("finished", "won", tid, "Weekend discount"), ("started", None, tid, "Weekend discount")]
    assert found[0]["at"] > found[1]["at"] and found[0]["channels"] == [] and found[0]["goal_id"] is None


def test_a_task_moved_twice_gives_two_markers_with_its_channel_and_goal(api):
    goal = post(api, "goals", {"title": "Raise activation", "owner_id": "ana"})["goal"]["id"]
    tid = post(api, "tasks", {"title": "Door hangers in Elm Park", "body": "Print and place them.", "owner_id": "ops",
                              "type": "idea", "labels": ["channel:seo"], "goal_id": goal})["id"]
    move(api, tid, "idea-doing")
    move(api, tid, "idea-paused")
    move(api, tid, "idea-doing")
    found = get(api, "growth/numbers")["markers"]
    assert [(m["kind"], m["task_id"], m["channels"], m["goal_id"]) for m in found] == [
        ("started", tid, ["seo"], goal)] * 2


def test_a_private_marketing_task_is_marked_only_for_a_reader_who_may_see_it(api):
    add_fay(api)
    tid = post(api, "tasks", {"title": "Cold email to dentists", "body": "Test a cold email.", "owner_id": "cara",
                              "type": "idea", "private": True, "labels": ["channel:email"]})["id"]
    move(api, tid, "idea-doing", token="cara-test")
    assert tid in marked(api, "cara-test")
    get(api, "tasks/" + tid, "fay-test", expected=404)
    for token in ("ana-test", "ben-test", "cara-test", "fay-test"):
        can_read = api.get("/api/v2/tasks/" + tid, headers=headers(token)).status_code == 200
        assert (tid in marked(api, token)) == can_read, token
    assert tid not in marked(api, "fay-test")


def test_a_deleted_task_leaves_the_markers(api):
    tid = post(api, "tasks", {"title": "Flyers at the farmers market", "body": "Try it.", "owner_id": "ops",
                              "type": "idea"})["id"]
    move(api, tid, "idea-doing")
    assert tid in marked(api)
    post(api, f"tasks/{tid}/delete", {})
    assert tid not in marked(api)


def test_anyone_signed_in_reads_and_may_edit_is_for_admins_and_bots(api):
    add_fay(api)
    readers = ("ana-test", "ben-test", bot(api, "ops"), "cara-test", "fay-test")
    assert [get(api, "growth/numbers", token)["may_edit"] for token in readers] == [True, True, True, False, False]
    assert api.get("/api/v2/growth/numbers").status_code == 401


def test_the_live_answer_matches_the_declared_contract(api):
    add_channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 3400, "bot_cost": 260,
                                "new_customers": 71, "kept_d30": 81, "kept_d90": 64, "kept_d180": 52})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18})
    north = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{north['id']}", {"growth_role": "north_star"})
    post(api, f"kpis/{north['id']}/readings", {"value": 412, "period_end": "2026-10-31T00:00:00Z"})
    tid = post(api, "tasks", {"title": "Weekend discount", "body": "10% off Saturdays.", "owner_id": "ops",
                              "type": "idea"})["id"]
    move(api, tid, "idea-doing")
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    operation = document["paths"]["/api/v2/growth/numbers"]["get"]["responses"]["200"]
    schema = operation["content"]["application/json"]["schema"]
    answer = get(api, "growth/numbers")
    assert answer["kpis"]["north_star"]["series"] and answer["markers"] and answer["channels"][1]["series"]
    assert conforms(answer, schema, document) is None
