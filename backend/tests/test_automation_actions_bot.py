"""A bot's own automation works its run's task (backend/automation_actions.py): it acts as the bot, so each task call
carries the version of the row it just read, as any bot's command must."""

import pytest

from backend import automation_actions as A
from backend.store import H


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def auto(owner="bot:ops"):
    return {"id": "auto_b", "flow": {"name": "Ops triage", "owner": owner, "trigger": {"type": "event", "name": "x.y"}}}


def test_a_bots_automation_notes_labels_and_assigns_its_task(c):
    made = H.task_create(c, "human:ana", "Check the deploy", "Please.", "bot:ops", lint=False)
    values = {"task": {"id": made["id"], "title": made["title"]}}
    assert A.run(c, auto(), "run_1", "note", {"text": "Seen"}, values)["ok"]
    assert A.run(c, auto(), "run_1", "label", {"tag": "billing"}, values)["detail"] == "labelled"
    done = A.run(c, auto(), "run_1", "assign", {"owner": "finance"}, values)
    assert done == {"ok": True, "detail": "assigned to bot:finance"}, done
    assert H.task(c, made["id"])["owner_id"] == "bot:finance"
