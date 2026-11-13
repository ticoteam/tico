"""The Growth page's numbers (backend/growth_numbers.py): the months' totals and each channel's, the KPI tree with its
readings, and the marketing ideas whose test started or that finished. Examples use Acme, a home-cleaning company."""

from backend import growth as G
from backend import growth_numbers as GN
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post, put  # noqa: F401
from backend.tests.helpers.openapi_v2 import conforms

ANA = "human:ana"

ECONOMICS = (
    "spend",
    "bot_cost",
    "new_customers",
    "cac",
    "kept_d30",
    "kept_d90",
    "kept_d180",
    "expected_months",
    "ltv",
    "ltv_cac",
    "payback_months",
    "quality",
)
MONEY = ("revenue_month", "margin_pct")
FIGURES = ECONOMICS + MONEY


def add_channel(api, slug="google-search", name="Google Search", **more):
    return post(api, "growth/channels", {"slug": slug, "name": name, **more})


def add_fay(api):
    """Fay: a company person on no team, owning no task, KPI or channel."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('fay','Fay','fay@acme.example')")


def bot(api, slug):
    """The bot itself, as an agent credential."""
    token = slug + "-test"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) "
            "VALUES(?,'hermes',?,?,'human:ana')",
            (slug, "x-" + slug, H.now()),
        )
    api.app.state.store.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def label(api, task_id, labels, token="ana-test"):
    """An idea's labels set the way its page sets them: at the version it was read at."""
    version = get(api, "tasks/" + task_id, token)["task"]["version"]
    return post(api, "tasks/" + task_id, {"version": version, "labels": labels}, token)


def idea(api, title, labels=("growth:ideas",), **more):
    return post(api, "tasks", {"title": title, "body": "Try it.", "owner": "ops", "labels": list(labels), **more})["id"]


def marked(api, token="ana-test"):
    return [m["task_id"] for m in get(api, "growth/numbers", token)["markers"]]


def test_an_empty_company_reads_empty_numbers(api):
    empty = get(api, "growth/numbers")
    # What is left to set up and what the caller may do (backend/tests/test_growth_setup.py).
    assert empty.pop("setup")["next"]["step"] == "settings" and empty.pop("you_may")["months"] is True
    assert empty == {
        "months": [],
        "series": [],
        "channels": [],
        "kpis": {"north_star": None, "inputs": [], "guardrails": [], "other": []},
        "markers": [],
        "may_edit": True,
    }
    add_channel(api)
    assert get(api, "growth/numbers")["channels"] == [
        {"slug": "google-search", "name": "Google Search", "stage": "idea", "owner_ref": None, "series": []}
    ]


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
    put(
        api,
        "growth/cohorts",
        {
            "channel": "google-search",
            "month": "2026-10",
            "spend": 3400,
            "bot_cost": 260,
            "new_customers": 71,
            "kept_d30": 81,
            "kept_d90": 64,
            "kept_d180": 52,
        },
    )
    put(
        api,
        "growth/cohorts",
        {
            "channel": "referrals",
            "month": "2026-10",
            "spend": 500,
            "new_customers": 20,
            "kept_d30": 70,
            "revenue_month": 120,
            "margin_pct": 30,
        },
    )
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18, "kept_d30": 70})
    with api.app.state.store.read() as c:
        expected = G.economics_total(G.cohorts(c, month="2026-10"), G.settings(c))
        money = G.aggregate(G.cohorts(c, month="2026-10"), G.settings(c))
        referrals_own = G.aggregate(G.cohorts(c, channel_slug="referrals", month="2026-10"), G.settings(c))
    numbers = get(api, "growth/numbers")
    total = numbers["series"][0]
    assert {key: total[key] for key in ECONOMICS} == {key: expected[key] for key in ECONOMICS}
    assert (
        {key: total[key] for key in MONEY}
        == {key: money[key] for key in MONEY}
        == {"revenue_month": 95.5, "margin_pct": 37.7}
    )
    assert total["new_customers"] == 109 and total["cac"] == round((3400 + 500 + 260) / 109, 2)
    # Each channel's own cohort: referrals sets its own revenue and margin, google search takes the settings.
    referrals = next(ch for ch in numbers["channels"] if ch["slug"] == "referrals")["series"][0]
    assert (
        {key: referrals[key] for key in MONEY}
        == {key: referrals_own[key] for key in MONEY}
        == {"revenue_month": 120, "margin_pct": 30}
    )
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
        ("google-search", ["2026-09", "2026-10"]),
        ("seo", []),
        ("unknown", ["2026-10"]),
    ]
    assert numbers["channels"][2]["name"] == "Unknown"
    # A retired channel's customers stay in the total, as its cohorts do.
    assert [s["new_customers"] for s in numbers["series"]] == [5, 11]


def test_kpis_group_by_growth_role_and_carry_their_last_24_readings_for_whoever_may_read_them(api):
    north = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{north['id']}", {"growth_role": "north_star"})
    booked = post(api, "kpis", {"name": "Bookings"})["kpi"]
    post(
        api,
        f"kpis/{booked['id']}",
        {
            "parent_kpi_id": north["id"],
            "growth_role": "input",
            "stage": "acquire",
            "loop_name": "Referral",
            "loop_step": 2,
        },
    )
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
        north["id"],
        "acquire",
        "Referral",
        2,
    )
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
    log(40, "2026-10-04T00:00:00Z")  # a second reading of the same period: the later one is the value
    series = get(api, "growth/numbers")["kpis"]["inputs"][0]["series"]
    assert series == [
        {"at": "2026-10-01T00:00:00.000000Z", "value": 1},
        {"at": "2026-10-03T00:00:00.000000Z", "value": 30},
        {"at": "2026-10-04T00:00:00.000000Z", "value": 40},
    ]


class _ReadsAll:
    """auth as the markers read it, for a reader who may read every task."""

    def task_sql(self, c, who):
        return "1"


def test_an_idea_marks_when_its_test_starts_and_when_it_is_kept_or_dropped(company):
    """A start is a label change that adds growth:running or idea:live; a finish is a move to Done (kept) or Closed
    (dropped) of a task with a growth label. Another label change, or a task that is not an idea, marks nothing."""
    with company.transaction() as c:
        X.bot(c, "ops")
        made = lambda title, labels: H.task_create(c, ANA, title, "Try it.", "bot:ops", lint=False, labels=labels)["id"]
        kept = made("Weekend discount", ["growth:ideas", "channel:seo"])
        dropped = made("Door hangers in Elm Park", ["idea:on-deck"])
        plain = made("Fix the login page", [])
        H.task_update(c, ANA, kept, labels=["growth:running", "channel:seo"])
        H.task_update(c, ANA, kept, labels=["growth:measuring", "channel:seo"])
        H.task_done(c, ANA, kept, "Bookings rose 8%")
        H.task_close(c, ANA, dropped, "Inconclusive: too few replies")
        H.task_close(c, ANA, plain, "Not needed")
        found = GN._markers(c, _ReadsAll(), None, "2000-01-01T00:00:00")
    assert sorted((m["kind"], m["outcome"], m["task_id"], tuple(m["channels"])) for m in found) == sorted(
        [
            ("started", None, kept, ("seo",)),
            ("finished", "kept", kept, ("seo",)),
            ("finished", "dropped", dropped, ()),
        ]
    )
    assert [m["at"] for m in found] == sorted((m["at"] for m in found), reverse=True)


def test_a_test_gives_a_started_and_a_finished_marker_newest_first(api):
    tid = idea(api, "Weekend discount")
    label(api, tid, ["growth:running"])
    version = get(api, "tasks/" + tid)["task"]["version"]
    post(api, f"tasks/{tid}/done", {"note": "Bookings rose 8%", "version": version})
    found = get(api, "growth/numbers")["markers"]
    assert [(m["kind"], m["outcome"], m["task_id"], m["title"]) for m in found] == [
        ("finished", "kept", tid, "Weekend discount"),
        ("started", None, tid, "Weekend discount"),
    ]
    assert found[0]["at"] > found[1]["at"] and found[0]["channels"] == [] and found[0]["goal_id"] is None


def test_an_idea_started_twice_gives_two_markers_with_its_channel_and_goal(api):
    goal = post(api, "goals", {"title": "Raise activation", "owner": "ana"})["goal"]["id"]
    tid = idea(api, "Door hangers in Elm Park", ("growth:ideas", "channel:seo"), goal_id=goal)
    label(api, tid, ["growth:running", "channel:seo"])
    label(api, tid, ["idea:paused", "channel:seo"])
    label(api, tid, ["growth:running", "channel:seo"])
    found = get(api, "growth/numbers")["markers"]
    assert [(m["kind"], m["task_id"], m["channels"], m["goal_id"]) for m in found] == [
        ("started", tid, ["seo"], goal)
    ] * 2


def test_a_private_idea_is_marked_only_for_a_reader_who_may_see_it(api):
    add_fay(api)
    tid = post(
        api,
        "tasks",
        {
            "title": "Cold email to dentists",
            "body": "Test a cold email.",
            "owner": "cara",
            "private": True,
            "labels": ["growth:ideas", "channel:email"],
        },
    )["id"]
    label(api, tid, ["growth:running", "channel:email"])  # labels are a mover's: Ana, who asked for it
    assert tid in marked(api, "cara-test")
    get(api, "tasks/" + tid, "fay-test", expected=404)
    for token in ("ana-test", "ben-test", "cara-test", "fay-test"):
        can_read = api.get("/api/v2/tasks/" + tid, headers=headers(token)).status_code == 200
        assert (tid in marked(api, token)) == can_read, token
    assert tid not in marked(api, "fay-test")


def test_a_deleted_task_leaves_the_markers(api):
    tid = idea(api, "Flyers at the farmers market")
    label(api, tid, ["growth:running"])
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
    put(
        api,
        "growth/cohorts",
        {
            "channel": "google-search",
            "month": "2026-10",
            "spend": 3400,
            "bot_cost": 260,
            "new_customers": 71,
            "kept_d30": 81,
            "kept_d90": 64,
            "kept_d180": 52,
        },
    )
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18})
    north = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{north['id']}", {"growth_role": "north_star"})
    post(api, f"kpis/{north['id']}/readings", {"value": 412, "period_end": "2026-10-31T00:00:00Z"})
    tid = idea(api, "Weekend discount")
    label(api, tid, ["growth:running"])
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    operation = document["paths"]["/api/v2/growth/numbers"]["get"]["responses"]["200"]
    schema = operation["content"]["application/json"]["schema"]
    answer = get(api, "growth/numbers")
    assert answer["kpis"]["north_star"]["series"] and answer["markers"] and answer["channels"][1]["series"]
    assert conforms(answer, schema, document) is None


def _idea(c, title, labels):
    return H.task_create(c, ANA, title, "Try it.", "bot:ops", lint=False, labels=labels)["id"]


def test_an_idea_already_running_starts_no_new_test_when_it_takes_the_other_running_label(company):
    """growth:running and idea:live say the same thing: a test starts when an idea takes the first of them, not when
    it adds or swaps to the other while it runs."""
    with company.transaction() as c:
        X.bot(c, "ops")
        tid = _idea(c, "Weekend discount", ["growth:ideas"])
        H.task_update(c, ANA, tid, labels=["growth:running"])
        H.task_update(c, ANA, tid, labels=["growth:running", "idea:live"])
        H.task_update(c, ANA, tid, labels=["idea:live"])
        H.task_update(c, ANA, tid, labels=["idea:live", "channel:seo"])
        found = GN._markers(c, _ReadsAll(), None, "2000-01-01T00:00:00")
    assert [m["kind"] for m in found] == ["started"]


def test_only_a_real_ending_marks_a_finish_and_it_stays_when_the_ideas_labels_go(company):
    """A done or closed move is a finish. A task restored from the trash as it ended did not finish again; an idea that
    finished keeps its finish when its labels are cleared later; one reopened and done again finished twice."""
    from backend import task_delete

    with company.transaction() as c:
        X.bot(c, "ops")
        restored = _idea(c, "Weekend discount", ["growth:ideas"])
        H.task_done(c, ANA, restored, "Bookings rose 8%")
        assert task_delete.delete_tasks(c, [restored], apply=True, actor=ANA, keep_work=True)["applied"]
        assert task_delete.restore_tasks(c, [restored], actor=ANA)["restored"] == [restored]
        cleared = _idea(c, "Door hangers in Elm Park", ["idea:on-deck"])
        H.task_close(c, ANA, cleared, "Too few replies")
        H.task_update(c, ANA, cleared, labels=[])
        twice = _idea(c, "Referral cards", ["growth:ideas"])
        H.task_done(c, ANA, twice, "Worked")
        H.task_move(c, ANA, twice, "reopen", "Try a second round")
        H.task_done(c, ANA, twice, "Worked again")
        found = GN._markers(c, _ReadsAll(), None, "2000-01-01T00:00:00")
    assert sorted((m["task_id"], m["outcome"]) for m in found) == sorted(
        [(restored, "kept"), (cleared, "dropped"), (twice, "kept"), (twice, "kept")]
    )


def test_the_markers_read_no_more_rows_than_they_show(company, monkeypatch):
    """The newest markers are picked in SQL: label changes that start nothing are never read into Python."""
    monkeypatch.setattr(GN, "MARKERS_MAX", 2)
    with company.transaction() as c:
        X.bot(c, "ops")
        busy = _idea(c, "Weekend discount", ["growth:ideas"])
        H.task_update(c, ANA, busy, labels=["growth:running"])
        for n in range(5):  # still running: no new start
            H.task_update(c, ANA, busy, labels=["growth:running", f"channel:c{n}"])
        newest = [_idea(c, title, ["growth:ideas"]) for title in ("Flyers", "Radio spot")]
        for tid in newest:
            H.task_update(c, ANA, tid, labels=["idea:live"])
        read, rows = [], H._rows
        monkeypatch.setattr(H, "_rows", lambda found: read.append(r := rows(found)) or r)
        found = GN._markers(c, _ReadsAll(), None, "2000-01-01T00:00:00")
    assert [m["task_id"] for m in found] == newest[::-1]
    assert max(len(r) for r in read) <= 2
