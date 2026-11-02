"""Thread and access tests name their bots `bot:<slug>`, as a bot from before ids reads (its id is its old slug). A test
module imports `bot_ids_are_their_slugs` to make every bot it creates that way. test_bot_id_threads.py holds the same
paths for a bot whose id is not its slug."""
import pytest

from backend.store import H


@pytest.fixture(autouse=True)
def bot_ids_are_their_slugs(monkeypatch):
    monkeypatch.setattr(H, "new_bot_id", lambda slug: slug)
