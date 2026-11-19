"""A bot's task tabs read what a task waits on from the engine (backend/bot_task_tabs.py `waits_ids`, `tab_sql`): Needs
you, Open and Waiting over the bot's own tasks. On a store with no app."""

import json
from typing import get_args

import pytest

from backend import bot_task_tabs as T
from backend import models as M
from backend import question_marks as Q
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401 - api is a fixture

ANA = "human:ana"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "ops")
        yield conn


def _shown(c, builtin, viewer=ANA):
    tab = {"conditions": T.DEFAULT_CONDITIONS[builtin]}
    where, args = T.tab_sql(c, tab, "ops", viewer)
    return {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE {where}", args)}


def test_needs_you_and_waiting_read_the_engine_and_open_holds_the_bots_work(c):
    working = H.task_create(c, ANA, "Draft the plan", "Draft it.", "bot:ops", lint=False)
    asking = H.task_create(c, ANA, "Pick a venue", "Pick one.", "bot:ops", lint=False)
    Q.ask(c, "bot:ops", asking["id"], ANA, "Lisbon or Porto?")  # the bot's ❓ to Ana
    for_ana = H.task_create(c, "bot:ops", "Sign the venue contract", "Sign it.", ANA, lint=False)
    assert _shown(c, "needs") == {asking["id"], for_ana["id"]}
    assert _shown(c, "needs", viewer="human:ben") == set()
    assert _shown(c, "active") == {working["id"], asking["id"]}
    assert _shown(c, "blocked") == set()  # what waits on Ana is in her Needs you, not in Waiting
    assert _shown(c, "blocked", viewer="human:ben") == {asking["id"]}


def test_the_tabs_route_lists_the_bots_tasks_the_caller_may_read(api):
    """GET /bots/{bot}/task-tabs/tasks filters by the caller's own task access (auth.task_sql) alone."""
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the plan", "body": "Draft it."})
    out = get(api, "bots/ops/task-tabs/tasks")
    assert any(task["id"] in tab["ids"] for tab in out["tabs"])


def test_the_request_model_names_the_same_fields_as_the_filters():
    assert set(get_args(M.BotTaskCondition.model_fields["field"].annotation)) == set(T.FIELDS)


def test_a_filter_on_a_stage_and_a_pipeline_saves_and_reads_back(api):
    """PUT /bots/{bot}/task-tabs keeps Stage and Pipeline rows, and a Task-list tabs 2/3 `types` tab becomes a Pipeline
    row; a row saved under the old names (step, type) reads back under the new ones."""
    rows = [{"field": "stage", "value": ["general-todo"]}, {"field": "pipeline", "value": ["general"]}]
    tabs = [{"name": "General to do", "conditions": rows}, {"name": "Old", "types": ["general"], "states": ["open"]}]
    r = api.put("/api/v2/bots/ops/task-tabs", json={"tabs": tabs}, headers=headers())
    assert r.status_code == 200, r.text
    saved = get(api, "bots/ops/task-tabs")["tabs"]
    assert saved[0]["conditions"] == rows
    assert {"field": "pipeline", "value": ["general"]} in saved[1]["conditions"]
    with api.app.state.store.transaction() as c:
        old = [
            {
                "name": "Old",
                "conditions": [{"field": "step", "value": ["general-todo"]}, {"field": "type", "value": ["general"]}],
            }
        ]
        c.execute("UPDATE bot_task_tabs SET tabs=? WHERE bot='ops'", (json.dumps(old),))
    assert get(api, "bots/ops/task-tabs")["tabs"][0]["conditions"] == rows
