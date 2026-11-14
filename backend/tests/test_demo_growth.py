"""Growth in demo mode: the numbers page tells one season's story from one model, the weekly routine's runs are its
history and its next run is the next Monday, and the paid test that loses is closed: a dropped idea. The store's customer
export, loaded after the last run, makes the Retention card compute its cohorts, and they agree with the typed rows."""

import sqlite3
from datetime import datetime, timezone

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)  # a Friday, so the Monday runs of the window are all past
CHANNELS = ["creator-videos", "online-store", "marketplace", "retail-partners", "paid-ads", "unknown"]


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("growth"), now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def numbers_of(settings):
    with signed_in(settings) as api:
        response = api.get("/api/v2/growth/numbers")
        assert response.status_code == 200
        return response.json()


@pytest.fixture(scope="module")
def numbers(built):
    return numbers_of(built)


def _numbers_page_has_the_five_channels_and_unknown_with_their_stages(numbers):
    assert [channel["slug"] for channel in numbers["channels"]] == CHANNELS
    assert numbers["months"] == ["2026-07", "2026-08", "2026-09"]
    assert {channel["slug"]: channel["stage"] for channel in numbers["channels"]} == {
        "creator-videos": "scaling",
        "online-store": "scaling",
        "marketplace": "scaling",
        "retail-partners": "testing",
        "paid-ads": "paused",
        "unknown": None,
    }


def _units_are_about_six_thousand_over_the_window(numbers):
    units = sum(row["new_customers"] for channel in numbers["channels"] for row in channel["series"])
    assert 5500 <= units <= 6500


def _units_ramp_into_the_holiday_and_the_inputs_and_guardrails_are_named(numbers):
    north = numbers["kpis"]["north_star"]
    assert north["name"] == "Units sold"
    series = [point["value"] for point in north["series"]]
    assert len(series) == 8 and series[-1] > series[0]
    assert north["latest"] == series[-1]
    assert {kpi["name"] for kpi in numbers["kpis"]["inputs"]} == {
        "Scene Library subscribers",
        "Business pipeline",
        "Business installs won",
    }
    assert {kpi["name"] for kpi in numbers["kpis"]["guardrails"]} == {"First-try calibration rate", "Returns"}


def _creator_videos_pay_back_and_paid_ads_do_not(numbers):
    by_slug = {channel["slug"]: channel for channel in numbers["channels"]}

    def last(slug, field):
        return [row[field] for row in by_slug[slug]["series"] if row[field] is not None][-1]

    assert last("creator-videos", "cac") < 40
    assert last("creator-videos", "ltv_cac") > 2
    assert last("paid-ads", "cac") > 100
    assert last("paid-ads", "ltv_cac") < 1


def _markers_show_the_started_tests_and_the_paid_test_closed_as_dropped(numbers):
    markers = {(marker["title"], marker["kind"], marker["outcome"]) for marker in numbers["markers"]}
    assert markers == {
        ("Twelve creators film their own holiday show", "started", None),
        ("Holiday search and social ads for the Storefront", "started", None),
        ("Holiday search and social ads for the Storefront", "finished", "dropped"),
        ("Starter bundle on bigmarket.example", "started", None),
    }


def _the_channel_pages_hold_their_ideas(built):
    with signed_in(built) as api:
        assert "Twelve creators film their own holiday show" in api.get("/api/v2/growth/channels/creator-videos").text
        assert (
            "End-cap display at two homeworks.example stores" in api.get("/api/v2/growth/channels/retail-partners").text
        )


def _weekly_numbers_routine_is_an_automation_next_due_on_a_monday_with_eight_ok_runs(built):
    with sqlite3.connect(built.db_path) as db:
        aid = db.execute("SELECT id FROM automations WHERE name='Weekly numbers'").fetchone()[0]
    with signed_in(built) as api:
        assert api.get("/api/v2/me").json()["actor"] == "human:ana"
        automation = api.get(f"/api/v2/automations/{aid}")
        listing = api.get(f"/api/v2/automations/{aid}/runs").json()
    assert automation.status_code == 200 and automation.json()["next_run"] == "2026-09-28T14:00:00.000000Z"
    runs = listing["runs"]
    assert len(runs) == 8 and {run["status"] for run in runs} == {"ok"}
    assert sum(day["runs"] for day in listing["daily"]) == 8
    for run in runs:
        (step,) = run["steps"]
        assert step["type"] == "bot_turn" and step["status"] == "ok"
        assert run["cost"] == pytest.approx(step["cost"]) and run["cost"] > 0
    with signed_in(built) as api:
        task = api.get(f"/api/v2/tasks/{runs[0]['steps'][0]['outputs']['ref']}").json()["task"]
    assert task["title"] == "Weekly numbers" and task["status"] == "done"


def _retention_is_computed_from_the_loaded_customers_and_cuts_by_channel_segment_and_plan(built):
    with signed_in(built) as api:
        by_channel = api.get("/api/v2/growth/computed", params={"by": "channel", "month": "2026-07"}).json()
        cuts = {cut: api.get("/api/v2/growth/computed", params={"by": cut}).json() for cut in ("segment", "plan")}
    assert by_channel["computed_from"] > 0 and by_channel["loads"][0]["source"] == "upload"
    july = {row["value"]: row for row in by_channel["cohorts"]}
    creators = july["creator-videos"]["kept_d30"]
    assert creators["status"] == "ok" and creators["origin"] == "computed" and 80 <= creators["value"] < 90
    assert {row["value"] for row in cuts["segment"]["cohorts"]} == {"home", "business"}
    assert {row["value"] for row in cuts["plan"]["cohorts"]} == {"lights-only", "scene-library"}
    # A month whose customers are not all 30 days old yet says when day 30 fills, never 0%.
    september = [row for row in cuts["plan"]["cohorts"] if row["period"] == "2026-09"]
    assert september and {row["kept_d30"]["status"] for row in september} == {"not_yet"}


def _a_retention_figure_opens_to_the_synthetic_customer_keys(built):
    with signed_in(built) as api:
        found = api.get(
            "/api/v2/growth/computed",
            params={"by": "channel", "month": "2026-07", "channel": "creator-videos", "customers": "true"},
        ).json()
    assert found["keys_visible"]
    (cohort,) = found["cohorts"]
    trace = cohort["kept_d30"]["customers"]
    assert trace["still"] and trace["churned"]
    assert all(key.startswith("ACME-") for key in trace["still"] + [row["key"] for row in trace["churned"]])
    assert all(row["churned"] for row in trace["churned"])
    assert len(trace["still"]) + len(trace["churned"]) == cohort["new_customers"]["value"]


def _no_typed_actual_differs_from_its_computed_figure(built):
    with signed_in(built) as api:
        cohorts = api.get("/api/v2/growth/cohorts", params={"limit": 100}).json()["cohorts"]
    differs = [
        (row["channel"], row["month"], field)
        for row in cohorts
        for field, origin in (row.get("origins") or {}).items()
        if (origin or {}).get("differs")
    ]
    assert differs == []
    with sqlite3.connect(built.db_path) as db:
        assert db.execute("SELECT count(*) FROM growth_computed_choices").fetchone()[0] == 0


def test_the_demo_growth_tells_its_story(built, numbers, subtests):
    """Every check reads the one demo build, in file order, each as its own subtest."""
    for check in (
        _numbers_page_has_the_five_channels_and_unknown_with_their_stages,
        _units_are_about_six_thousand_over_the_window,
        _units_ramp_into_the_holiday_and_the_inputs_and_guardrails_are_named,
        _creator_videos_pay_back_and_paid_ads_do_not,
        _markers_show_the_started_tests_and_the_paid_test_closed_as_dropped,
    ):
        with subtests.test(check.__name__):
            check(numbers)
    for check in (
        _the_channel_pages_hold_their_ideas,
        _weekly_numbers_routine_is_an_automation_next_due_on_a_monday_with_eight_ok_runs,
        _retention_is_computed_from_the_loaded_customers_and_cuts_by_channel_segment_and_plan,
        _a_retention_figure_opens_to_the_synthetic_customer_keys,
        _no_typed_actual_differs_from_its_computed_figure,
    ):
        with subtests.test(check.__name__):
            check(built)
