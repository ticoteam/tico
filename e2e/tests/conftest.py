"""Every e2e test runs with outside network blocked (backend/tests/live.py): a test here reaches a local server only.
An e2e run once reached a live provider, so this is not left to each test file."""
import pytest

from backend.tests.live import block_outside_network


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    block_outside_network(monkeypatch, "the e2e tests have no network")
