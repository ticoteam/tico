"""The v0.2.21 vocabulary rename of the REST routes, in one place.

Handlers keep registering under the paths they always had. `install(app)` runs once, after every route is
registered, and for each route whose path is in RENAMES it makes the NEW path the canonical route (same endpoint,
methods and response model, in the same position in the route order) and keeps the OLD path as a second route,
marked `deprecated` in OpenAPI. The old spellings answer for one release; then the old routes and this table's
old column go. New routes use the app's route class so its response handling applies to every spelling.

Beyond the renames, three routes are new doors onto an existing answer: `GET /health/issues` (the live snapshot
for the Assistant, what is wrong for everyone else), `GET /messages?unread=1` (the inbox) and `GET /archives?source=`
(one archive, where `/archives` alone lists them).

`/runners/*` stays canonical for the runner software's own calls (enroll, heartbeat, desired, assignments, ...):
only `/runners/{id}/...`, what a person does to one computer, moves to `/computers/{id}/...`. The runner-facing
`/connectors/*` routes stay as they are.
"""
import inspect
import re

from fastapi import Request
from fastapi.routing import APIRoute

from .store import Problem

V2 = "/api/v2"

# (old prefix, new prefix, only for the sub-paths that start with this, or None). The first rule that matches a
# path wins, so an exact sub-path goes before its parent's prefix.
RENAMES = [
    (V2 + "/runners", V2 + "/computers", "/{"),                 # /runners/{rid}/..., not /runners/enroll
    (V2 + "/integrations", V2 + "/tools", None),
    (V2 + "/ops/timing", V2 + "/operations/timing", None),
    (V2 + "/judge", V2 + "/decisions", None),
    (V2 + "/listening/judge", V2 + "/listening/decide", None),
    (V2 + "/onboarding/departments", V2 + "/setup/groups", None),
    (V2 + "/getting-started", V2 + "/setup/getting-started", None),
    (V2 + "/onboarding", V2 + "/setup", None),
    (V2 + "/goal-proposals", V2 + "/proposals", None),
    (V2 + "/catalog", V2 + "/templates", None),
    (V2 + "/access/people", V2 + "/access/humans", None),
    (V2 + "/people", V2 + "/humans", None),
    ("/api/people", "/api/humans", None),                        # the roster and photos, outside v2
]


def _swap(path, old, new, only, forward=True):
    """`path` with its `old` prefix replaced by `new` (or the reverse), or None when the rule does not apply."""
    src, dst = (old, new) if forward else (new, old)
    if path != src and not path.startswith(src + "/"):
        return None
    rest = path[len(src):]
    if only is not None and not (rest.startswith(only) or (only == "/{" and _has_id(rest))):
        return None
    return dst + rest


def _has_id(rest):
    """Whether a sub-path holds an id and something after it (`/abc123/logins`): a concrete request for a computer's own
    route, as well as the registered `/{rid}/...` template. `/enroll` and `/heartbeat` are the runner's own calls."""
    return re.match(r"/[^/{}]+/", rest) is not None


def new_path(path):
    """The canonical spelling of an old route path, or None when `path` was not renamed."""
    for old, new, only in RENAMES:
        found = _swap(path, old, new, only)
        if found is not None:
            return found
    return None


def old_paths(path):
    """Every old spelling still answering for the canonical `path` (usually one, else none)."""
    found = []
    for old, new, only in RENAMES:
        swapped = _swap(path, old, new, only, forward=False)
        if swapped is not None and new_path(swapped) == path:
            found.append(swapped)
    return found


def _copy(route, path, route_class):
    """A route like `route` on another path: the same endpoint, methods, response model and options."""
    accepted = inspect.signature(APIRoute.__init__).parameters
    options = {name: getattr(route, name) for name in accepted
               if name not in ("self", "path", "endpoint") and hasattr(route, name)}
    return route_class(path, route.endpoint, **options)


def _find(routes, path, method):
    return next((r for r in routes if isinstance(r, APIRoute) and r.path == path and method in r.methods), None)


def _put_before(routes, anchor, route):
    routes.insert(routes.index(anchor), route)


def install(app):
    routes = app.router.routes
    # Direct construction must keep the app's response handling on every spelling.
    route_class = app.router.route_class
    # The renames: new path first (canonical), old path kept right behind it, deprecated.
    for route in list(routes):
        if not isinstance(route, APIRoute):
            continue
        path = new_path(route.path)
        if path is None:
            continue
        _put_before(routes, route, _copy(route, path, route_class))
        route.deprecated = True

    # /health/issues: the Assistant's live snapshot, or what /fleet/check says for everyone else.
    check, snapshot = _find(routes, V2 + "/fleet/check", "GET"), _find(routes, V2 + "/tico/fleet", "GET")
    if check and snapshot:
        def health_issues(request: Request):
            """Health checks and fixes over the bots the caller may see, plus the team snapshot for the Assistant."""
            who = request.state.identity
            checked = check.endpoint(request)
            if getattr(who, "via", None) == "assistant":
                return {**snapshot.endpoint(request), **{key: checked[key] for key in ("checks", "issues", "services", "counts")}}
            return checked
        _put_before(routes, check, route_class(V2 + "/health/issues", health_issues, methods=["GET"]))
        check.deprecated = snapshot.deprecated = True

    # /messages?unread=1: the inbox.
    inbox = _find(routes, V2 + "/inbox", "GET")
    if inbox:
        def messages_unread(request: Request, unread: bool = True):
            """The caller's unread messages (`unread=1`, the default: the only list there is), with any notices."""
            if not unread:
                raise Problem("unread_only", "Only unread messages are listed: pass unread=1", 422)
            return inbox.endpoint(request)
        _put_before(routes, inbox, route_class(V2 + "/messages", messages_unread, methods=["GET"],
                                             name="messages_unread"))
        inbox.deprecated = True

    # /archives?source=: one archive; /archives alone lists them. /archive stays, deprecated.
    listing, read = _find(routes, V2 + "/archives", "GET"), _find(routes, V2 + "/archive", "GET")
    if listing and read:
        def archives(request: Request, source: str | None = None):
            """The archives this account may read, or with `source` that one archive's content."""
            return read.endpoint(request, source) if source else listing.endpoint(request)
        _put_before(routes, listing, route_class(V2 + "/archives", archives, methods=["GET"], name="archives"))
        routes.remove(listing)
        read.deprecated = True
