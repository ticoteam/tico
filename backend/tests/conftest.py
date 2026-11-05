"""Fixtures for independently testing the layers of API authorization."""
import pytest


@pytest.fixture
def underlying_authority(monkeypatch):
    """Requester-delegation/RBAC tests exercise their inner policy, including forbidden outer routes.

    The production-write sweep in test_scope_projection exercises the unmodified route perimeter,
    including scoped BotOps administration refusals. Source projection and object rights stay active here.
    """
    from backend import scoped_routes
    monkeypatch.setattr(scoped_routes, "require_route", lambda *args, **kwargs: None)
