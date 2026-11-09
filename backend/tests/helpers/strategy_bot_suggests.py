"""Helpers that other tests share, moved out of test_strategy_bot_suggests.py so they import no test file."""

from backend import question_tasks as QT
from backend.store import H
from backend.tests.helpers.strategy_bot_level import level
from backend.tests.helpers.company import with_strategy_bot


OWNER, STRATEGY_BOT = "human:ana", "bot:goal-manager"


def bot_task(api, slug, title, status="open"):
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:" + slug, title, "Work it.", "bot:" + slug, lint=False)
        if status != "open":
            H.task_update(c, "bot:" + slug, made["id"], status=status)
    return made["id"]


def ask_ana(api, slug, title):
    """A bot's task Ana asked for, where the bot asks her a question back: a question task under it, so the task waits
    on her."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, OWNER, title, "Please.", "bot:" + slug)
        QT.ask_plain(c, "bot:" + slug, OWNER, "Which pricing page goes live first?", parent=H.task(c, made["id"]))
        H.task_update(c, "bot:" + slug, made["id"], status="waiting", waiting_on=OWNER)
    return made["id"]
