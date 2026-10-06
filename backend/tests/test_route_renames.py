"""The v0.2.21 REST vocabulary (backend/route_renames.py): the new path is canonical, the old one still answers
for a release, marked deprecated."""
import pytest

from backend.tests.test_api import api, headers  # noqa: F401

PAIRS = [("/api/humans", "/api/people"), ("/api/v2/messages", "/api/v2/inbox")]


@pytest.mark.parametrize("new,old", PAIRS)
def test_the_old_path_answers_what_the_new_one_does(api, new, old):
    fresh, stale = api.get(new, headers=headers()), api.get(old, headers=headers())
    assert fresh.status_code == stale.status_code == 200, (fresh.text, stale.text)
    volatile = ("checked", "routes", "since", "actor_name", "actors")      # a clock, timings, display names
    strip = lambda body: {k: v for k, v in body.items() if k not in volatile} if isinstance(body, dict) else body
    assert strip(fresh.json()) == strip(stale.json())
