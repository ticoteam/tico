"""SCIM 2.0 server (RFC 7643/7644) so an identity provider can push people: Okta, Microsoft Entra
provisioning, JumpCloud and the like.

The subset those clients use: /Users (POST, GET by id, GET list with `filter=<attr> eq "<value>"`
and startIndex/count, PUT, PATCH, DELETE), /ServiceProviderConfig, /ResourceTypes, /Schemas, and a
/Groups that is always empty (turn group provisioning off in the IdP; people are what Tico needs).

Auth is one bearer token the owner creates in Settings > Humans (only its hash is kept). The route
is exempt from the app's sign-in (backend/app.py) and carries no browser identity, so the audit
actor is `scim`. Every write goes through the sync engine in backend/directory.py, so the same
rules hold: the owner and people added by hand are never deactivated, and a burst of
deactivations stops at the owner's mass-leave limit.

A user's SCIM `id` is the roster person id; `userName` is the email.
"""
import hmac
from functools import wraps
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import access as Access
from . import directory as D
from . import hubdb as H
from .store import Problem, digest

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
MEDIA = "application/scim+json"
MAX_COUNT = 200
MAX_BODY = 1_000_000


class ScimError(Exception):
    def __init__(self, status, detail, scim_type=""):
        self.status, self.detail, self.scim_type = status, detail, scim_type


def _reply(body, status=200, headers=None):
    return JSONResponse(body, status_code=status, media_type=MEDIA, headers=headers)


def _error(status, detail, scim_type="", headers=None):
    body = {"schemas": [ERROR], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return _reply(body, status, headers)


def resource(p, base):
    first, _, last = p["name"].partition(" ")
    out = {"schemas": [USER], "id": p["id"], "userName": p["email"], "displayName": p["name"],
           "name": {"formatted": p["name"], "givenName": first, "familyName": last},
           "title": p["title"], "active": not p["hidden"],
           "emails": [{"value": p["email"], "type": "work", "primary": True}],
           "meta": {"resourceType": "User", "location": f"{base}/Users/{p['id']}"}}
    if p["external_id"]:
        out["externalId"] = p["external_id"]
    if p["reports_to"]:
        out["schemas"].append(ENTERPRISE)
        out[ENTERPRISE] = {"manager": {"value": p["reports_to"]}}
    return out


# ----------------------------------------------------------------------------------- parsing
_TERM = re.compile(r'^\s*([A-Za-z0-9_.:\[\]"\- ]+?)\s+eq\s+("(?:[^"\\]|\\.)*"|[^\s"]+)\s*$', re.I)
_ATTRS = {"username": "email", "emails.value": "email", "externalid": "external_id", "id": "id",
          "emails[type eq \"work\"].value": "email"}


def parse_filter(text):
    """`userName eq "a@b.c"` (and `and`-joined equalities, which Entra sends) -> [(field, value)]."""
    terms = []
    if not (text or "").strip():
        return terms
    for part in re.split(r'\s+and\s+(?=(?:[^"]*"[^"]*")*[^"]*$)', text or "", flags=re.I):
        m = _TERM.match(part)
        field = _ATTRS.get(m.group(1).strip().lower()) if m else None
        if not field:
            raise ScimError(400, "Only eq filters on userName, externalId, emails.value and id are supported", "invalidFilter")
        value = m.group(2)
        value = json.loads(value) if value.startswith('"') else value    # Entra may send it unquoted
        terms.append((field, str(value)))
    return terms


def _bool(value):
    if isinstance(value, str):
        return value.strip().lower() == "true"          # Entra sends "True"/"False" as text
    return bool(value)


def _set(state, path, value, op):
    """Apply one PATCH path to the desired state. Unknown paths are ignored, as Entra sends many."""
    p = re.sub(r"\s+", " ", str(path or "")).strip()
    low = p.lower()
    if low.startswith(ENTERPRISE.lower() + ":"):
        low = low[len(ENTERPRISE) + 1:]
    remove = op == "remove"
    if low == "active":
        state["active"] = False if remove else _bool(value)
    elif low == "username":
        if not remove and value:
            state["email"] = str(value).strip().lower()
    elif low == "emails" or low.startswith("emails") and low.endswith("value"):
        # Only a fallback: Entra may map userName and the work email to different attributes.
        if isinstance(value, list):
            value = next((e.get("value") for e in value if isinstance(e, dict) and e.get("primary")), None) or \
                    next((e.get("value") for e in value if isinstance(e, dict)), "")
        if not remove and value:
            state["alt"] = str(value).strip().lower()
    elif low in ("displayname", "name.formatted"):
        state["name"] = "" if remove else str(value or "").strip()
    elif low in ("name.givenname", "name.familyname"):
        state["parts"][low.split(".")[1]] = "" if remove else str(value or "").strip()
    elif low == "name" and isinstance(value, dict):
        for k in ("givenName", "familyName", "formatted"):
            if k in value:
                _set(state, "name." + k, value[k], op)
    elif low == "title":
        state["title"] = "" if remove else str(value or "").strip()
    elif low == "externalid":
        state["external_id"] = "" if remove else str(value or "").strip()
    elif low in ("manager", "manager.value"):
        state["manager"] = "" if remove else str((value or {}).get("value") if isinstance(value, dict) else value or "").strip()
    elif not p and isinstance(value, dict):
        for k, v in value.items():
            _set(state, k, v, op)
    elif low == "" or low == ENTERPRISE.lower():
        if isinstance(value, dict):
            for k, v in value.items():
                _set(state, k, v, op)


def desired(body, current=None, ops=None):
    """The record a create/replace/patch asks for. `current` is the person being changed."""
    state = {"email": current["email"] if current else "", "name": current["name"] if current else "",
             "title": current["title"] if current else "", "active": not current["hidden"] if current else True,
             "external_id": current["external_id"] if current else "", "manager": current["reports_to"] if current else "",
             "parts": {}, "alt": ""}
    if ops is None:                                     # POST / PUT: the whole resource
        state["name"] = state["title"] = ""
        for key, value in body.items():
            if key not in ("schemas", "id", "meta", "groups"):
                _set(state, key if key != ENTERPRISE else "", value, "add")
    else:
        for o in ops:
            if not isinstance(o, dict):
                continue
            _set(state, o.get("path"), o.get("value"), str(o.get("op") or "").lower())
    state["email"] = state["email"] or state["alt"]
    if state["parts"] and not state["name"]:
        state["name"] = " ".join(x for x in (state["parts"].get("givenname"), state["parts"].get("familyname")) if x)
    elif state["parts"] and ops is not None:
        first, _, last = state["name"].partition(" ")
        state["name"] = " ".join(x for x in (state["parts"].get("givenname", first), state["parts"].get("familyname", last)) if x)
    return state


# ------------------------------------------------------------------------------------- routes
def install_scim(app, settings, store):
    base = settings.public_url + "/scim/v2"
    lock_actor = "scim"

    def guard(request):
        header = request.headers.get("authorization") or ""
        # Some Okta templates send the header field verbatim, without the "Bearer " prefix.
        token = (header[7:] if header.lower().startswith("bearer ") else header).strip()
        with store.read() as c:
            cfg = D.load(c)
        stored = (cfg["scim"] or {}).get("token_hash") or ""
        if cfg["source"] != "scim" or not stored or not token or not hmac.compare_digest(digest(token), stored):
            raise ScimError(401, "Invalid or missing bearer token")
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BODY:
                raise ScimError(413, "Request too large")
        except ValueError:
            raise ScimError(400, "Invalid Content-Length") from None
        return cfg

    async def body_of(request):
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            raise ScimError(400, "The body is not valid JSON", "invalidSyntax") from None
        if not isinstance(body, dict):
            raise ScimError(400, "The body must be a JSON object", "invalidSyntax")
        return body

    def wrap(fn):
        @wraps(fn)
        async def handler(*args, **kw):
            try:
                guard(kw["request"])
                return await fn(*args, **kw)
            except ScimError as exc:
                return _error(exc.status, exc.detail, exc.scim_type,
                              {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None)
            except Problem as exc:
                return _error(exc.status, exc.detail, headers={"Retry-After": "3600"} if exc.status == 429 else None)
        return handler

    def roster_of(c):
        from .views import roster
        return roster(c)

    def apply_one(state, current):
        """Run one desired record through the engine; returns the person as stored afterwards."""
        email = state["email"]
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise ScimError(400, "userName must be an email address", "invalidValue")
        record = {"email": email, "name": state["name"], "title": state["title"], "active": state["active"],
                  "external_id": state["external_id"], "manager": state["manager"]}
        blocked, person = False, None
        with store.transaction() as c:
            live = roster_of(c)
            owner = Access.load_owner(c, settings)["email"]
            found = D.plan(live, [record], "scim", owner, False, Access.retired_emails(c))
            blocked = bool(found["leaves"]) and _recent_leaves(c) + len(found["leaves"]) > D.load(c)["mass_leave_limit"]
            if not blocked:
                for key in ("protected", "skipped"):
                    for x in found[key]:
                        H.event(c, lock_actor, "directory.person_protected", "", {"email": x["email"], "reason": x.get("reason")})
                D.apply(c, lock_actor, "scim", found, live)
                person = next((p for p in roster_of(c)["people"] if p["email"] == email), None)
                cfg = D.load(c)
                cfg["last"] = {"at": H.now(), "ok": True, "auto": True, "source": "scim", "held": False}
                D.save(c, cfg)
        if blocked:
            # Its own transaction: a refusal rolls back the one it is raised in.
            with store.transaction() as c:
                H.event(c, lock_actor, "directory.scim_guard", "", {"email": email})
            raise Problem("too_many", "Too many deactivations in the last hour; the owner's mass-leave limit stopped this one", 429)
        return person

    def _recent_leaves(c):
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return c.execute("SELECT COUNT(*) FROM events INDEXED BY events_task_origin "
                         "WHERE action='directory.person_left' AND actor=? AND created_at>=?",
                         (lock_actor, since)).fetchone()[0]

    def refresh():
        with store.read() as c:
            app.state.auth.sync_access(c)

    def person_by(c, pid):
        p = next((x for x in roster_of(c)["people"] if x["id"] == pid), None)
        if not p:
            raise ScimError(404, "User not found")
        return p

    @app.get("/scim/v2/ServiceProviderConfig")
    @wrap
    async def config(request: Request):
        return _reply({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                       "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                       "filter": {"supported": True, "maxResults": MAX_COUNT}, "changePassword": {"supported": False},
                       "sort": {"supported": False}, "etag": {"supported": False},
                       "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token",
                                                  "description": "A token created in Settings > Humans"}]})

    @app.get("/scim/v2/ResourceTypes")
    @wrap
    async def resource_types(request: Request):
        return _reply({"schemas": [LIST], "totalResults": 1, "startIndex": 1, "itemsPerPage": 1, "Resources": [
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User",
             "endpoint": "/Users", "schema": USER, "schemaExtensions": [{"schema": ENTERPRISE, "required": False}]}]})

    @app.get("/scim/v2/Schemas")
    @wrap
    async def schemas(request: Request):
        return _reply({"schemas": [LIST], "totalResults": 2, "startIndex": 1, "itemsPerPage": 2, "Resources": [
            {"id": USER, "name": "User", "description": "User account",
             "attributes": [{"name": n, "type": "string", "multiValued": False, "required": n == "userName"}
                            for n in ("userName", "displayName", "title", "externalId")]},
            {"id": ENTERPRISE, "name": "EnterpriseUser", "description": "Enterprise user",
             "attributes": [{"name": "manager", "type": "complex", "multiValued": False, "required": False}]}]})

    @app.get("/scim/v2/Groups")
    @wrap
    async def groups(request: Request):
        return _reply({"schemas": [LIST], "totalResults": 0, "startIndex": 1, "itemsPerPage": 0, "Resources": []})

    @app.get("/scim/v2/Groups/{gid}")
    @wrap
    async def group(request: Request, gid: str):
        raise ScimError(404, "Group not found")

    @app.post("/scim/v2/Groups")
    @wrap
    async def group_create(request: Request):
        raise ScimError(501, "Groups are not supported; turn off group provisioning and provision users only")

    @app.get("/scim/v2/Users")
    @wrap
    async def users(request: Request):
        terms = parse_filter(request.query_params.get("filter", ""))
        try:
            start = max(1, int(request.query_params.get("startIndex", 1)))
            count = min(MAX_COUNT, max(0, int(request.query_params.get("count", 100))))
        except ValueError:
            raise ScimError(400, "startIndex and count must be integers", "invalidValue") from None
        with store.read() as c:
            people = roster_of(c)["people"]
        for field, value in terms:
            people = [p for p in people if str(p[field]).lower() == value.lower()]
        page = people[start - 1:start - 1 + count]
        return _reply({"schemas": [LIST], "totalResults": len(people), "startIndex": start,
                       "itemsPerPage": len(page), "Resources": [resource(p, base) for p in page]})

    @app.post("/scim/v2/Users")
    @wrap
    async def create(request: Request):
        state = desired(await body_of(request))
        with store.read() as c:
            existing = next((p for p in roster_of(c)["people"] if p["email"] == state["email"]), None)
        if existing:
            raise ScimError(409, "A user with that userName already exists", "uniqueness")
        person = apply_one(state, None)
        refresh()
        if not person:
            raise ScimError(400, "Nothing was created; an inactive user is not added", "invalidValue")
        return _reply(resource(person, base), 201, {"Location": f"{base}/Users/{person['id']}"})

    @app.get("/scim/v2/Users/{pid}")
    @wrap
    async def get_user(request: Request, pid: str):
        with store.read() as c:
            return _reply(resource(person_by(c, pid), base))

    @app.put("/scim/v2/Users/{pid}")
    @wrap
    async def replace(request: Request, pid: str):
        body = await body_of(request)
        with store.read() as c:
            current = person_by(c, pid)
        state = desired(body)
        state["email"] = state["email"] or current["email"]
        if state["email"] != current["email"]:
            raise ScimError(400, "Changing userName is not supported", "mutability")
        person = apply_one(state, current)
        refresh()
        return _reply(resource(person or current, base))

    @app.patch("/scim/v2/Users/{pid}")
    @wrap
    async def patch(request: Request, pid: str):
        body = await body_of(request)
        ops = body.get("Operations") or body.get("operations")
        if not isinstance(ops, list):
            raise ScimError(400, "Operations is required", "invalidSyntax")
        with store.read() as c:
            current = person_by(c, pid)
        state = desired(body, current, ops)
        if state["email"] != current["email"]:
            raise ScimError(400, "Changing userName is not supported", "mutability")
        person = apply_one(state, current)
        refresh()
        return _reply(resource(person or current, base))

    @app.delete("/scim/v2/Users/{pid}")
    @wrap
    async def delete(request: Request, pid: str):
        with store.read() as c:
            current = person_by(c, pid)
        apply_one({"email": current["email"], "name": current["name"], "title": current["title"], "active": False,
                   "external_id": current["external_id"], "manager": ""}, current)
        refresh()
        return Response(status_code=204)
