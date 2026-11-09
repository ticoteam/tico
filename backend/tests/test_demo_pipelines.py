"""The demo's Dev tickets pipeline works in the demo app: its review queue and its manager answer, and the tickets'
requirement states are the ones the demo tells (a waiting screenshot, a stale one, a met test run), because the evidence
is attached at the revision the ticket is really at."""

import sqlite3
from datetime import datetime, timezone

import pytest
import time_machine

from backend import demo
from backend.tests.helpers.demo_finance import signed_in

EARLY_MONTH = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    with time_machine.travel(EARLY_MONTH, tick=False):
        settings = demo.build(tmp_path_factory.mktemp("demo-pipelines"), now=EARLY_MONTH)
        with sqlite3.connect(settings.db_path) as db:
            (type_id,) = db.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
            by_title = dict(db.execute("SELECT title, id FROM tasks WHERE type_id=?", (type_id,)).fetchall())
        yield signed_in(settings), type_id, by_title


def _the_pipeline_review_queue_and_manager_answer_in_the_demo(pipeline):
    client, type_id, _ = pipeline
    queue = client.get("/api/v2/pipelines/" + type_id + "/review/queue")
    assert queue.status_code == 200, queue.text
    manager = client.get("/api/v2/pipelines/" + type_id + "/manager")
    assert manager.status_code == 200, manager.text


def _the_demo_tickets_show_the_states_the_demo_tells(pipeline):
    client, _, by_title = pipeline

    def states(title):
        found = client.get(f"/api/v2/tasks/{by_title[title]}/requirements").json()["requirements"]
        return {r["name"]: r["state"] for r in found}

    assert states("Assistant can approve a spec")["Screenshots"] == "waiting"  # attached at the current revision
    assert states("Assistant can approve a spec")["Tests pass"] == "met"
    assert (
        states("Phone board shows one stage")["Screenshots"] == "out_of_date"
    )  # attached before the last change: stale
    assert states("Pipeline stages and the manager rail")["Spec page"] == "waiting"


def test_the_demo_pipelines_tell_their_story(pipeline, subtests):
    """Every check reads the one demo build, in file order, each as its own subtest."""
    for check in (
        _the_pipeline_review_queue_and_manager_answer_in_the_demo,
        _the_demo_tickets_show_the_states_the_demo_tells,
    ):
        with subtests.test(check.__name__):
            check(pipeline)
