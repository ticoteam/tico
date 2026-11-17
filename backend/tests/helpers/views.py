"""Helpers that other tests share, moved out of test_views.py so they import no test file."""

from backend.auth import Identity
from backend.tests.helpers.api import as_member  # noqa: F401


def assistant_for(api, actor, email):
    """A token for the Assistant acting as this person: the identity its team snapshot (health/issues) is served to."""
    token = actor.split(":", 1)[1] + "-assistant"
    api.app.state.store.settings.test_identities[token] = Identity(actor, "human", email, via="assistant")
    return token
