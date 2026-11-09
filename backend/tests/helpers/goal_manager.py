"""Helpers that other tests share, moved out of test_goal_manager.py so they import no test file."""

import shutil
from pathlib import Path
from backend.tests.helpers.onboarding import ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD


TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "catalog" / "goal-manager"


BASE = [(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")]


def with_goal_manager(api):
    """The catalog a release ships: the real template, copied next to the test's own cards."""
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "goal-manager")
