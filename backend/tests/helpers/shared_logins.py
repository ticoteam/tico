"""Helpers that other tests share, moved out of test_shared_logins.py so they import no test file."""

import pytest


@pytest.fixture
def queued_login(monkeypatch):
    """Exercise dormant queue support without enabling it in the built-in Codex manifest."""
    from clients.harness_manifests import caps

    monkeypatch.setitem(caps("codex"), "one_turn_per_login", True)
