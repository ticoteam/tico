"""Thread and access tests name their bots `bot:<slug>`, as a bot from before ids reads (its id is its old slug).
test_bot_id_threads.py holds the same paths for a bot whose id is not its slug."""
import re

import pytest

from backend.store import H

SLUG_IS_ID = re.compile(r"\.test_(thread|threads|teammate_access|my_dashboard)(_|$)")


@pytest.fixture(autouse=True)
def _bot_ids_are_their_slugs(request, monkeypatch):
    if SLUG_IS_ID.search(request.module.__name__):
        monkeypatch.setattr(H, "new_bot_id", lambda slug: slug)
