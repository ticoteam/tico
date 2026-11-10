"""A bot's task tabs read what a task waits on from the engine (backend/bot_task_tabs.py `waits_ids`, `tab_sql`): Needs
you, Open and Waiting over the bot's own tasks. On a store with no app."""

import pytest

from backend import bot_task_tabs as T
from backend.store import H
from backend.tests.helpers import engine as X

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
    H.task_ask(c, "bot:ops", asking["id"], ANA, "Lisbon or Porto?", seen_version=asking["version"])
    for_ana = H.task_create(c, "bot:ops", "Sign the venue contract", "Sign it.", ANA, lint=False)
    assert _shown(c, "needs") == {asking["id"], for_ana["id"]}
    assert _shown(c, "needs", viewer="human:ben") == set()
    assert _shown(c, "active") == {working["id"], asking["id"]}
    assert _shown(c, "blocked") == set()  # what waits on Ana is in her Needs you, not in Waiting
    assert _shown(c, "blocked", viewer="human:ben") == {asking["id"]}
