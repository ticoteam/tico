"""The route spellings after the v0.2.21 rename layer went: the canonical path answers; the old spellings that rc.1
clients still call (backend/legacy_spellings.py) answer the same handler, marked deprecated; every other old spelling
is gone."""

import pytest
from fastapi.routing import APIRoute

from backend import legacy_spellings
from backend.tests.helpers.api import headers  # noqa: F401  (the api fixture)

VOLATILE = ("checked", "routes", "since", "actor_name", "actors")  # a clock, timings, display names

REMOVED = [
    "/api/v2/inbox",
    "/api/v2/archive",
    "/api/v2/judge",
    "/api/v2/catalog",
    "/api/v2/onboarding/departments",
    "/api/v2/fleet/check",
    "/api/v2/tico/fleet",
    "/api/v2/ops/timing",
    "/api/v2/people/{pid}",
    "/api/v2/goal-proposals",
    "/api/v2/integrations",
    "/api/v2/runners/{rid}/restart",
    "/api/v2/runners/{rid}/logins",
]


def _strip(body):
    return {k: v for k, v in body.items() if k not in VOLATILE} if isinstance(body, dict) else body


@pytest.mark.parametrize("method,old,new", legacy_spellings.KEPT)
def test_a_kept_spelling_is_the_same_route_marked_deprecated(api, method, old, new):
    routes = {(route.path, m): route for route in api.app.routes if isinstance(route, APIRoute) for m in route.methods}
    canonical, kept = routes[(new, method)], routes[(old, method)]
    assert kept.endpoint is canonical.endpoint
    assert kept.deprecated and not canonical.deprecated


@pytest.mark.parametrize("path", REMOVED)
def test_a_removed_spelling_is_no_route(api, path):
    assert not any(route.path == path for route in api.app.routes)


@pytest.mark.parametrize(
    "old,new",
    [("/api/v2/getting-started", "/api/v2/setup/getting-started"), ("/api/v2/onboarding", "/api/v2/setup")],
)
def test_a_kept_read_answers_what_its_canonical_path_does(api, old, new):
    fresh, stale = api.get(new, headers=headers()), api.get(old, headers=headers())
    assert fresh.status_code == stale.status_code == 200, (fresh.text, stale.text)
    assert _strip(fresh.json()) == _strip(stale.json())


def test_the_merged_routes_answer_their_new_spellings(api):
    assert api.get("/api/v2/messages?unread=1", headers=headers()).status_code == 200
    assert api.get("/api/v2/messages?unread=0", headers=headers()).status_code == 422
    assert api.get("/api/v2/archives", headers=headers()).status_code == 200
    assert api.get("/api/v2/archives?source=missing", headers=headers()).status_code == 404
    assert api.get("/api/v2/health/issues", headers=headers()).status_code == 200
    assert api.get("/api/humans", headers=headers()).status_code == 200


def test_the_kept_spellings_map_to_their_canonical_paths():
    assert legacy_spellings.canonical("/api/v2/runners/r1/inbox-only") == "/api/v2/computers/r1/inbox-only"
    assert legacy_spellings.canonical("/api/v2/getting-started") == "/api/v2/setup/getting-started"
    assert legacy_spellings.canonical("/api/v2/goal-proposals/turned-down") == "/api/v2/proposals/turned-down"
    assert legacy_spellings.canonical("/api/v2/computers/r1/inbox-only") is None
    assert legacy_spellings.canonical("/api/v2/runners/r1/restart") is None
    assert legacy_spellings.legacy_of("/api/v2/setup") == ["/api/v2/onboarding"]
    assert legacy_spellings.legacy_of("/api/v2/humans/p1") == []
