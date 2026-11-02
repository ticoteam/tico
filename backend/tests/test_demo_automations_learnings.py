"""Demo automations, Learnings nights and Tico release updates: built as the demo builds them, read back as Ana."""

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    directory = tmp_path_factory.mktemp("demo_automations")
    return directory, demo.build(directory, now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def automation_ids(api):
    return {row["name"]: row["id"] for row in api.get("/api/v2/automations?limit=100").json()["automations"]}


def runs_of(api, aid):
    body = api.get(f"/api/v2/automations/{aid}/runs?limit=100").json()
    return body["runs"], body["daily"]


def test_each_automation_shows_its_runs_and_drops(built):
    _, settings = built
    with signed_in(settings) as api:
        ids = automation_ids(api)
        calibration, _ = runs_of(api, ids["Calibration failures to technical support"])
        assert len(calibration) == 14 and {run["status"] for run in calibration} == {"ok"}
        twice, daily = runs_of(api, ids["Calibration failed twice to Ben"])
        assert len(twice) == 2
        assert sum(day["dropped"] for day in daily) == 12
        nightly, _ = runs_of(api, ids["Nightly orders to bookkeeping"])
        assert len(nightly) == 7 and {run["status"] for run in nightly} == {"ok"}
        reviews, daily = runs_of(api, ids["1-star reviews to reputation"])
        assert len(reviews) == 2 and sum(day["dropped"] for day in daily) == 4


def test_the_run_guard_held_a_loop_and_ben_dropped_it(built):
    _, settings = built
    with signed_in(settings) as api:
        orders = automation_ids(api)["Marketplace orders to inventory"]
        runs, daily = runs_of(api, orders)
        # The loop's runs are the ones the guard held; Ben's drop skipped them, and the orders after it ran.
        assert {run["status"] for run in runs} == {"ok", "skipped"}
        assert sum(day["runs"] for day in daily) == 128
        assert api.get(f"/api/v2/automations/{orders}").json().get("hold") is None


def test_learnings_shows_a_week_of_nights(built):
    _, settings = built
    with signed_in(settings) as api:
        nights = api.get("/api/v2/learnings").json()["nights"]
        assert len(nights) == 7
        assert sorted(night["status"] for night in nights) == ["done"] * 6 + ["quiet"]
        correction = next(night for night in nights if night["night"] == "2026-09-21")
        packet = api.get(f"/api/v2/learnings/{correction['id']}/packets/support-lead")
        assert packet.status_code == 200
        assert any("refund" in item["text"].lower() for item in packet.json()["items"])


def test_two_bots_hear_each_tico_release_after_the_first(built):
    directory, settings = built
    with sqlite3.connect(settings.db_path) as db:
        notes = sorted(to for (to,) in db.execute(
            "SELECT to_actor FROM notes WHERE body LIKE 'Subscribed update%'"))
    assert notes == ["bot:botops", "bot:botops", "bot:product-manager", "bot:product-manager"]
