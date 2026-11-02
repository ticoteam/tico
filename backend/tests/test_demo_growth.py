"""Growth in demo mode: the numbers page tells one season's story from one model, the weekly routine's runs are its
history and its next run is the next Monday, and the paid test that loses is closed as Lost."""

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)      # a Friday, so the Monday runs of the window are all past
CHANNELS = ["creator-videos", "online-store", "marketplace", "retail-partners", "paid-ads", "unknown"]


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


def test_numbers_page_has_the_five_channels_and_unknown_with_their_stages(numbers):
    assert [channel["slug"] for channel in numbers["channels"]] == CHANNELS
    assert numbers["months"] == ["2026-07", "2026-08", "2026-09"]
    assert {channel["slug"]: channel["stage"] for channel in numbers["channels"]} == {
        "creator-videos": "scaling", "online-store": "scaling", "marketplace": "scaling",
        "retail-partners": "testing", "paid-ads": "paused", "unknown": None}


def test_units_are_about_six_thousand_over_the_window(numbers):
    units = sum(row["new_customers"] for channel in numbers["channels"] for row in channel["series"])
    assert 5500 <= units <= 6500


def test_units_ramp_into_the_holiday_and_the_inputs_and_guardrails_are_named(numbers):
    north = numbers["kpis"]["north_star"]
    assert north["name"] == "Units sold"
    series = [point["value"] for point in north["series"]]
    assert len(series) == 8 and series[-1] > series[0]
    assert north["latest"] == series[-1]
    assert {kpi["name"] for kpi in numbers["kpis"]["inputs"]} == {
        "Scene Library subscribers", "Business pipeline", "Business installs won"}
    assert {kpi["name"] for kpi in numbers["kpis"]["guardrails"]} == {"First-try calibration rate", "Returns"}


def test_creator_videos_pay_back_and_paid_ads_do_not(numbers):
    by_slug = {channel["slug"]: channel for channel in numbers["channels"]}

    def last(slug, field):
        return [row[field] for row in by_slug[slug]["series"] if row[field] is not None][-1]

    assert last("creator-videos", "cac") < 40
    assert last("creator-videos", "ltv_cac") > 2
    assert last("paid-ads", "cac") > 100
    assert last("paid-ads", "ltv_cac") < 1


def test_markers_show_the_started_tests_and_the_paid_test_closing_as_lost(numbers):
    markers = {(marker["title"], marker["kind"], marker["outcome"]) for marker in numbers["markers"]}
    assert markers == {
        ("Twelve creators film their own holiday show", "started", None),
        ("Holiday search and social ads for the Storefront", "started", None),
        ("Holiday search and social ads for the Storefront", "finished", "lost"),
        ("Starter bundle on bigmarket.example", "started", None),
    }


def test_the_channel_pages_hold_their_ideas(built):
    with signed_in(built) as api:
        assert "Twelve creators film their own holiday show" in api.get("/api/v2/growth/channels/creator-videos").text
        assert "End-cap display at two homeworks.example stores" in api.get("/api/v2/growth/channels/retail-partners").text


def test_weekly_numbers_routine_is_an_automation_next_due_on_a_monday_with_eight_ok_runs(built):
    with sqlite3.connect(built.db_path) as db:
        aid = db.execute("SELECT id FROM automations WHERE name='Weekly numbers'").fetchone()[0]
    with signed_in(built) as api:
        automation = api.get(f"/api/v2/automations/{aid}")
        runs = api.get(f"/api/v2/automations/{aid}/runs")
    assert automation.status_code == 200 and automation.json()["next_run"] == "2026-09-28T14:00:00.000000Z"
    assert runs.status_code == 200
    assert len(runs.json()["runs"]) == 8
    assert {run["status"] for run in runs.json()["runs"]} == {"ok"}


def test_the_numbers_are_the_same_on_every_build(built, tmp_path_factory):
    assert numbers_of(demo.build(tmp_path_factory.mktemp("growth-again"), now=NOW)) == numbers_of(built)
