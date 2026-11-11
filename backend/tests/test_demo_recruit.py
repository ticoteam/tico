"""Demo mode's Recruit section, as Ana sees it: the open seats on the chart, the applicants for the install crew job, the
cloud agent request in flight, and the seat filled from a template last week. Built twice, it gives the same rows."""

import sqlite3
from datetime import datetime, timezone

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-recruit"), now=NOW)


def owner_api(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def open_seats(api):
    return {seat["title"]: seat for seat in api.get("/api/v2/seats").json()["seats"]}


def _open_seats_sit_under_their_managers(built):
    with owner_api(built) as api:
        seats = open_seats(api)
        assert seats["Install Crew Coordinator"]["reports_to"] == "operations-manager"
        assert seats["Warranty Claims"]["reports_to"] == "success-team-manager"
        assert seats["Install Crew Coordinator"]["request_state"] == ""
        assert seats["Devin"]["reports_to"] == "product-manager"


def _recruit_offers_templates_for_each_open_seat(built):
    with owner_api(built) as api:
        seats = open_seats(api)
        warranty = api.post(f"/api/v2/seats/{seats['Warranty Claims']['id']}/recruit", json={}).json()
        assert warranty["candidates"][0]["template"] == "returns-and-refunds-specialist"
        assert warranty["candidates"][0]["match"] == "good"
        install = api.post(f"/api/v2/seats/{seats['Install Crew Coordinator']['id']}/recruit", json={}).json()
        assert "dispatcher" in [row["template"] for row in install["candidates"]]


def _applicants_are_open_recruiter_tasks_one_asking_ana_and_one_snoozed(built):
    with owner_api(built) as api:
        tasks = api.get("/api/v2/tasks", params={"owner": "bot:recruiter", "snoozed": "all", "limit": 100}).json()[
            "tasks"
        ]
        applicants = {
            task["title"].rsplit(": ", 1)[1]: task
            for task in tasks
            if task["title"].startswith("Applicant for Install Crew Coordinator")
        }
        assert sorted(applicants) == ["Jordan Park", "Luis Ortega", "Maya Osei", "Priya Nair"]
        assert {task["status"] for task in applicants.values()} == {"open"}
        assert (applicants["Maya Osei"]["turn"]["state"], applicants["Maya Osei"]["turn"]["who"]) == (
            "person",
            "human:ana",
        )
        assert applicants["Luis Ortega"]["snoozed"]


def _the_cloud_agent_request_is_in_flight_with_its_first_task(built):
    with owner_api(built) as api:
        agents = {agent["id"]: agent for agent in api.get("/api/v2/cloud-agents").json()["agents"]}
        assert len(agents) == 8
        assert {key for key, agent in agents.items() if agent["supported"]} == {"claude", "codex"}
        seat = open_seats(api)["Devin"]
        request = api.get(f"/api/v2/seats/{seat['id']}/request").json()
        assert request["agent"]["id"] == "devin"
        assert request["seat"]["request_state"] == "requested"
        assert [task["title"] for task in request["tasks"]] == [
            "Write a replay test for the scene that landed on the wrong window"
        ]


def _the_hire_last_week_closed_its_seat_and_kept_the_bot(built):
    with sqlite3.connect(built.db_path) as db:
        seat = db.execute("SELECT closed_at, filled_by FROM seats WHERE title='Creator Partnerships'").fetchone()
    assert seat[0] and seat[1] == "creators"
    with owner_api(built) as api:
        assert api.get("/api/v2/bots/creators").status_code == 200
        assert "Creator Partnerships" not in open_seats(api)


def test_the_demo_recruit_tells_its_story(built, subtests):
    """Every check reads the one demo build, in file order, each as its own subtest."""
    for check in (
        _open_seats_sit_under_their_managers,
        _recruit_offers_templates_for_each_open_seat,
        _applicants_are_open_recruiter_tasks_one_asking_ana_and_one_snoozed,
        _the_cloud_agent_request_is_in_flight_with_its_first_task,
        _the_hire_last_week_closed_its_seat_and_kept_the_bot,
    ):
        with subtests.test(check.__name__):
            check(built)
