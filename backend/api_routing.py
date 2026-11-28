# pyright: strict
"""The route class every API route uses, and the router a route module builds its routes on.

`TicoRoute` is what `install_route_class` used to build per app as a closure (backend/names.py). Each route:

- answers its handler's dicts and lists as orjson (backend/fast_json.py), while `route.endpoint` stays the handler;
- adds display names to a stable v2 JSON answer (names.display_names) and renames `attempt_id` to `run_id`
  (names.run_names);
- shares one person's identical reads (backend/shared_reads.py) and compresses the answer (backend/json_response.py).

It holds no app and no store: each request reads them from `request.app` and `request.app.state.store`, so two apps
built in one process (tests build many) never answer from each other's database. `create_app` sets it as the app's
route class; a route module that leaves backend/app.py builds a `router()` and `include(app, router)` adds its routes
where its old `install(app, ...)` call stood (backend/report_order.py is the first). Its handlers take their services
as a parameter typed `deps.Injected`, `Depends(deps.get_deps)` (backend/deps.py).

Never `app.include_router`: from FastAPI 0.163 that adds one lazy entry for the whole router, so every reader of
`app.routes` (the v2 document and its display-name paths, the Assistant's and Agent Ops' route lists) misses its
routes. `include` adds each route to the app itself, as `@app.get` did.

Not backend/routing.py: that module decides which person takes routed work.
"""

import inspect
from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from starlette.responses import Response

from . import fast_json as _fast_json
from .api_contract import MARK
from . import json_response as _json_response
from . import names as _names
from . import shared_reads

fast_json: Any = _fast_json  # untyped modules
json_response: Any = _json_response
names: Any = _names


# The options `add_api_route` takes, by name: what a TicoRoute keeps of how it was declared.
_OPTIONS = frozenset(
    name
    for name in inspect.signature(APIRouter.add_api_route).parameters
    if name not in ("self", "path", "endpoint", "route_class_override")
)


class TicoRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable[..., Any], **options: Any) -> None:
        # FastAPI calls the handler it was given; the JSON it answers is encoded by orjson (backend/fast_json.py).
        # `endpoint` stays the handler itself, for the callers that read its dicts.
        plain = isinstance(options.get("response_class", DefaultPlaceholder(None)), DefaultPlaceholder)
        # What the route was declared with, unresolved: `include` declares it again on the app with these, so an
        # option left at FastAPI's default (a stream's response_model) is not passed back as an explicit None.
        self.declared: dict[str, Any] = {name: value for name, value in options.items() if name in _OPTIONS}
        super().__init__(
            path, fast_json.answering_json(endpoint, options.get("status_code")) if plain else endpoint, **options
        )
        self.endpoint = endpoint

    def get_route_handler(self) -> Callable[[Request], Any]:
        handler = super().get_route_handler()

        async def named_response(request: Request) -> Response:
            # This request's app's store, never one held here; an app with none (a bare FastAPI() in a test) gets no
            # display names and no shared reads.
            store = getattr(request.app.state, "store", None)

            async def answer() -> Response:
                response = await handler(request)
                if store is not None:
                    response = await names.display_names(store, request, response)
                return names.run_names(request, response)

            # A person's identical reads run at most once a second (backend/shared_reads.py); each request gets its
            # own validators and wire encoding.
            shared = (
                await shared_reads.answer(store, request, self.path, answer) if store is not None else await answer()
            )
            return await json_response.compress(request, shared)

        return named_response


def router(**options: Any) -> APIRouter:
    """An APIRouter whose routes are TicoRoutes. A route class that is not one is refused: its routes would answer
    without display names, run names, shared reads or orjson."""
    route_class = options.pop("route_class", TicoRoute)
    if not (isinstance(route_class, type) and issubclass(route_class, TicoRoute)):
        raise TypeError(f"A Tico router's routes are TicoRoutes, not {route_class!r}")
    if any(options.get(hook) for hook in HOOKS):
        raise TypeError(LIFECYCLE)
    return APIRouter(route_class=route_class, **options)


HOOKS = ("lifespan", "on_startup", "on_shutdown")
LIFECYCLE = (
    "A Tico router has no lifespan, on_startup or on_shutdown: include() adds only its routes. "
    "Background work goes in the app's lifespan workers (backend/lifespan_workers.py)"
)


def include(app: FastAPI, made: APIRouter) -> None:
    """Add a router's routes to the app, each a route of the app's own, in the router's order, here: the place in
    `create_app` decides which route answers first (the web app's page answers whatever nothing before it did).

    All or nothing: every route is checked before any is added. Refused: a route that is not a TicoRoute, a router
    with lifecycle hooks (they would never run), and a stable operation (api_v2) whose path and method or
    operationId the app or the router already declares, which would turn every stable answer into a 500
    (openapi_v2.operations)."""
    for route in made.routes:
        if not isinstance(route, TicoRoute):
            raise TypeError(f"{route!r} is not a TicoRoute: build it on api_routing.router()")
    if made.on_startup or made.on_shutdown or type(made.lifespan_context).__name__ != "_DefaultLifespan":
        raise TypeError(LIFECYCLE)
    adding = cast(list[TicoRoute], made.routes)
    seen = {key for route in app.routes for key in _stable_keys(route)}
    for route in adding:
        for key in _stable_keys(route):
            if key in seen:
                raise ValueError(f"{key!r} is declared twice: the app or this router already has it")
            seen.add(key)
    for route in adding:
        app.router.add_api_route(route.path, route.endpoint, route_class_override=type(route), **route.declared)
    if getattr(app.state, "stable_path", None) is not None:
        app.state.stable_path = None  # built again on next use, with these routes (openapi_v2.stable_path)


def _stable_keys(route: Any) -> list[Any]:
    """What openapi_v2.operations allows once per app: each (path, method) of a stable route, and its operationId."""
    extra: dict[str, Any] = getattr(route, "openapi_extra", None) or {}
    if MARK not in extra:
        return []
    methods: set[str] = getattr(route, "methods", None) or set()
    return [*((route.path_format, method.lower()) for method in sorted(methods)), extra["operationId"]]
