"""shared() reuses one sealed-credentials scan within a read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import computer_keys


def test_shared_reuses_sealed_credentials_within_a_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        first = computer_keys._sealed_credentials(c)
        second = computer_keys._sealed_credentials(c)
        assert first is second
        assert computer_keys.shared(c, "codex") == {}
