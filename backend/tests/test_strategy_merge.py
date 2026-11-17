"""The Strategy Owner's merged asks (backend/strategy_merge.py): a bot's question to a person is a ❓ message now, so
nothing new is merged. A merged item from before still passes its answer on (test_answers.py). On a store with no
app."""

import pytest

from backend import strategy_bot, strategy_merge as SM, strategy_suggest
from backend.store import H
from backend.tests.helpers import engine as X

ANA = "human:ana"


@pytest.fixture
def c(company, monkeypatch):
    monkeypatch.setattr(strategy_suggest, "suggests", lambda conn: True)
    with company.transaction() as conn:
        for slug in ("sales", "support", strategy_bot.SLUG):
            X.bot(conn, slug)
        yield conn


def test_nothing_new_is_merged_now_that_a_question_is_a_message(c):
    tasks = [
        H.task_create(c, ANA, title, "Plan it.", bot, lint=False)["id"]
        for title, bot in (
            ("Plan the sales launch", "bot:sales"),
            ("Plan the support launch", "bot:support"),
        )
    ]
    with pytest.raises(H.Refused, match="Only the"):
        SM.merge(c, "bot:sales", ANA, "Region?", "Which region goes first?", tasks)
    with pytest.raises(H.Refused, match="❓ message"):
        SM.merge(c, SM.STRATEGY_BOT, ANA, "Region?", "Which region goes first?", tasks)
    assert c.execute("SELECT count(*) FROM strategy_merges").fetchone()[0] == 0
