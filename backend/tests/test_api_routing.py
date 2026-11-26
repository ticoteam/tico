"""Every route the app answers is a TicoRoute (backend/api_routing.py), and a route module built on its `router()`
reads its own app's store and services (backend/deps.py), never one captured when the module was imported.
backend/report_order.py is the first such module."""

import json
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from backend import api_routing, openapi_v2, report_order, shared_reads
from backend.tests.helpers.api import get, headers, post, put, setup_attempt
from backend.tests.helpers.org_chart import revision, roster

ORDER = "/api/v2/org/order/{manager}"


def test_every_api_route_is_a_tico_route(api):
    plain = [
        (route.path, sorted(route.methods))
        for route in api.app.routes
        if isinstance(route, APIRoute) and not isinstance(route, api_routing.TicoRoute)
    ]
    assert plain == []
    assert api.app.router.route_class is api_routing.TicoRoute
    # No lazy `app.include_router` entry: its routes would be missing from every reader of `app.routes`.
    assert [r for r in api.app.routes if type(r).__name__ == "_IncludedRouter"] == []


def test_a_router_makes_tico_routes_and_refuses_any_other_route_class():
    made = api_routing.router(prefix="/x", tags=["X"])
    assert isinstance(made, APIRouter) and made.route_class is api_routing.TicoRoute and made.prefix == "/x"

    class Narrower(api_routing.TicoRoute):
        pass

    assert api_routing.router(route_class=Narrower).route_class is Narrower
    with pytest.raises(TypeError, match="TicoRoute"):
        api_routing.router(route_class=APIRoute)
    plain = APIRouter()
    plain.get("/x")(lambda: {})
    with pytest.raises(TypeError, match="TicoRoute"):
        api_routing.include(FastAPI(), plain)


def test_a_router_route_answers_like_any_app_route_on_any_app():
    """orjson, `route.endpoint` the handler itself, and storage's `attempt_id` named `run_id` on the wire."""
    from fastapi.testclient import TestClient

    made = api_routing.router()

    @made.get("/api/v2/jobs")
    def jobs():
        return {"attempt_id": "a1", "at": float("nan")}

    app = FastAPI()
    app.state.store = None  # nothing here reads it: no identity, so no shared read and no display names
    api_routing.include(app, made)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/v2/jobs")
    assert isinstance(route, api_routing.TicoRoute) and route.endpoint is jobs and route is not made.routes[0]
    with TestClient(app) as client:
        answer = client.get("/api/v2/jobs")
    assert answer.status_code == 200 and answer.content == b'{"run_id":"a1","at":null}'


def test_report_order_routes_come_from_its_router_in_their_old_slot(api):
    routes = api.app.routes
    ours = [(i, r) for i, r in enumerate(routes) if isinstance(r, APIRoute) and r.path == ORDER]
    # The module's own handlers, once each, through its router: no `install(app, ...)` closure is left.
    assert [(sorted(r.methods), r.endpoint) for _, r in ours] == [
        (["GET"], report_order.report_order),
        (["PUT"], report_order.report_order_set),
    ]
    # Where install_report_order put them: after the team build's routes, before the settings catalog's, and so
    # before the web app's catch-all page and the org rule seal that come after every API route.
    first, last = ours[0][0], ours[-1][0]
    assert routes[first - 1].path == "/api/v2/team-build/hire"
    assert routes[last + 1].path == "/api/v2/settings/catalog"
    page = next(i for i, r in enumerate(routes) if getattr(r, "path", "") == "/{rest:path}")
    assert last < page


def test_the_report_order_contract_is_unchanged(api):
    """Both OpenAPI documents, byte for byte as main served them before the move (taken at 0902b090cd): the deps
    dependency reads the request and adds no parameter, and the operation ids, summaries and schemas stay put."""
    pinned = json.loads((Path(__file__).parent / "fixtures" / "report_order_contract.json").read_text())
    v2 = openapi_v2.spec(api.app)
    assert v2["paths"][ORDER] == pinned["v2"]
    assert {name: v2["components"]["schemas"][name] for name in pinned["schemas"]} == pinned["schemas"]
    assert api.app.openapi()["paths"][ORDER] == pinned["full"]


def test_who_may_order_is_unchanged_through_the_router(api):
    roster(
        api,
        [
            {"id": "ana", "email": "ana@acme.example", "name": "Ana", "primary_for": ["*"]},
            {"id": "ben", "email": "ben@acme.example", "name": "Ben", "reports_to": "ana"},
            {"id": "cara", "email": "cara@acme.example", "name": "Cara", "reports_to": "ana"},
        ],
    )
    for bot in ("ops", "finance"):
        post(api, f"bots/{bot}/definition", {"reports_to": "assistant", "expected_revision": revision(api, bot)})
    denied = put(api, "org/order/assistant", {"reports": ["ops", "finance"]}, token="cara-test", expected=403)
    assert denied["error"]["code"] == "forbidden"
    _, _, ops = setup_attempt(api, "ops")
    put(api, "org/order/assistant", {"reports": ["ops"]}, token=ops["token"], expected=403)
    get(api, "org/order/nobody", expected=404)
    assert [r["id"] for r in put(api, "org/order/assistant", {"reports": ["ops", "finance"]})["reports"]] == [
        "ops",
        "finance",
    ]
    # The write went through the app's own mutation: a retry with the same key answers the same, once.
    key = headers()["Idempotency-Key"]
    body = {"reports": ["finance", "ops"]}
    first = api.put("/api/v2/org/order/assistant", json=body, headers=headers(key=key))
    again = api.put("/api/v2/org/order/assistant", json=body, headers=headers(key=key))
    assert first.status_code == again.status_code == 200 and first.json() == again.json()


def test_two_apps_each_answer_from_their_own_store(tmp_path, api_database, monkeypatch):
    from backend.tests.helpers.api_app import api_client

    monkeypatch.setenv("TICO_READ_RATE_SECONDS", "1")  # shared reads on, as in production (conftest turns them off)
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    built = [api_client(tmp_path / name, api_database) for name in ("one", "two")]
    one, two = (next(b) for b in built)
    try:
        assert one.app.state.store is not two.app.state.store
        for client, name in ((one, "Ops One"), (two, "Ops Two")):
            with client.app.state.store.transaction() as c:
                c.execute("UPDATE bots SET display_name=? WHERE slug='ops'", (name,))
        post(one, "bots/ops/definition", {"reports_to": "assistant", "expected_revision": revision(one, "ops")})
        assert one.app.state.deps.store is one.app.state.store and two.app.state.deps.store is two.app.state.store

        # report_order reads the store of the app it was asked through.
        assert [r["name"] for r in get(one, "org/order/assistant")["reports"]] == ["Ops One"]
        assert get(two, "org/order/assistant")["reports"] == []
        put(one, "org/order/assistant", {"reports": ["ops"]})
        with two.app.state.store.read() as c:
            assert c.execute("SELECT 1 FROM registry_metadata WHERE key='report_order'").fetchone() is None

        # Display names come from each app's own store, and each store counts its own shared reads.
        before = [shared_reads.of(client.app.state.store).computed for client in (one, two)]
        for client, name in ((one, "Ops One"), (two, "Ops Two")):
            task = post(client, "tasks", {"owner": "ops", "title": "Plan"})
            answer = get(client, "tasks/" + task["id"])
            assert answer["task"]["owner_name"] == name and answer["actors"]["bot:ops"] == name
        after = [shared_reads.of(client.app.state.store).computed for client in (one, two)]
        assert [a - b for a, b in zip(after, before)] == [1, 1]

    finally:
        for b in built:
            b.close()
