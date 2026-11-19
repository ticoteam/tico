"""Display names in the stable v2 answers.

The store keeps raw actor ids (`human:ana`, `bot:ops`). A frontend should not have to look
every one up, so an answer on a stable route also carries the names, added beside the ids and never
instead of them:

- an object with an `owner_id`, `requester_id`, `sender_id`, `recipient_id` or `actor` id gets `owner_name` and so on;
- a read (GET) answer that is an object gets `actors`: {id: name} for every id it names (and for the `who`, `asked_by`
  and `by` of a task's waits and requirements and a pipeline's `manager_id`, LISTED_KEYS), one lookup for the whole
  page;
- for a person, a notice the hub wrote for bots to read ("New task from bot:x: ...") is shown with names
  (the original is kept in `body_raw`). Bots and other callers get the text as written.

Nothing here reads more than the `humans` and `bots` tables, and a name is only a label.
"""

import asyncio
import sqlite3
from fastapi.responses import JSONResponse
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from . import runs
from .openapi_v2 import stable_path

from . import fast_json, read_cache
import re

ACTOR_KEYS = (
    "owner_id",
    "requester_id",
    "sender_id",
    "recipient_id",
    "actor_id",
    "status_by_id",
    "decided_by_id",
    "owner",
    "requester",
    "actor",
    "author",
    "reports_to",
    "status_by",
    "proposed_by",
)
# Keys whose actor ids go into `actors` only, with no `_name` beside them: a task's waits and requirements name who
# they wait on (`who`), who asked (`asked_by`) and who answered (`by`), and `tico task show` prints those names; a
# pipeline names its manager (`manager_id`), who may hold no stage of it.
LISTED_KEYS = ("who", "asked_by", "by", "manager_id")
ID = re.compile(r"\b(human|bot):([A-Za-z0-9._-]*[A-Za-z0-9_])")
# Tico support in a session a person opened (backend/support_access.py): no roster row, always this name.
SUPPORT_ACTOR, SUPPORT_NAME = "human:tico-support", "Tico support"
TICO_FROM = re.compile(r"^New task from keeper: ")


def _title(slug):
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), slug.replace("-", " ").replace("_", " "))


def _is_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _collect(node, found, notices):
    if isinstance(node, dict):
        for key, value in node.items():
            if (key in ACTOR_KEYS or key in LISTED_KEYS) and _is_id(value):
                found.add(value)
            elif key in ("participants", "owners", "participant_ids") and isinstance(value, list):
                found.update(v for v in value if _is_id(v))
            else:
                _collect(value, found, notices)
        if node.get("kind") == "notice" and isinstance(node.get("body"), str) and "sender_id" in node:
            notices.append(node)
            found.update(m.group(0) for m in ID.finditer(node["body"]))
    elif isinstance(node, list):
        for value in node:
            _collect(value, found, notices)


def lookup(conn, ids):
    """{actor id: display name} for the human and bot ids among `ids`; an unknown id is left out. Inside a snapshot each
    actor is read once: a caller that names many threads one at a time (the rail's unread rows) reads the names it
    looked up before, or that it looked up together first, from the snapshot's cache."""
    ids = set(ids)
    out = {SUPPORT_ACTOR: SUPPORT_NAME} if SUPPORT_ACTOR in ids else {}
    known = read_cache.cache(conn)
    known = known.setdefault(("names.lookup",), {}) if known is not None else None
    if known is not None:
        out.update({i: known[i] for i in ids if known.get(i) is not None})
        ids = {i for i in ids if i not in known}
    found = _read_names(conn, ids)
    if known is not None:
        known.update({i: found.get(i) for i in ids if i.startswith(("human:", "bot:"))})
    out.update(found)
    return out


def _read_names(conn, ids):
    humans = sorted({i.split(":", 1)[1] for i in ids if i.startswith("human:")})
    bots = sorted({i.split(":", 1)[1] for i in ids if i.startswith("bot:")})
    out = {}
    for start in range(0, len(humans), 500):
        chunk = humans[start : start + 500]
        sql = "SELECT id,name FROM humans WHERE id IN (%s)" % ",".join("?" * len(chunk))
        for row in read_cache.rows(conn, sql, chunk):
            out["human:" + row[0]] = (row[1] or "").strip() or _title(row[0])
    for start in range(0, len(bots), 500):
        chunk = bots[start : start + 500]
        sql = "SELECT id,slug,display_name FROM bots WHERE id IN (%s)" % ",".join("?" * len(chunk))
        for row in read_cache.rows(conn, sql, chunk):
            out["bot:" + row[0]] = (row[2] or "").strip() or _title(row[1])
    return out


def _apply(node, names):
    if isinstance(node, dict):
        for key in list(node):
            value = node[key]
            if key in ACTOR_KEYS and _is_id(value):
                if value in names:
                    node.setdefault(key.removesuffix("_id") + "_name", names[value])
            elif key in ("participants", "owners", "participant_ids") and isinstance(value, list):
                if all(isinstance(v, str) for v in value) and any(v in names for v in value):
                    node.setdefault(
                        {
                            "participants": "participant_names",
                            "participant_ids": "participant_names",
                            "owners": "owner_names",
                        }[key],
                        [names.get(v, v) for v in value],
                    )
            else:
                _apply(value, names)
    elif isinstance(node, list):
        for value in node:
            _apply(value, names)


def annotate(conn, payload, render_notices=False, actors=True):
    """Add names to a JSON-ready answer, in place, and return it."""
    if not isinstance(payload, (dict, list)):
        return payload
    found, notices = set(), []
    _collect(payload, found, notices)
    if not found and not (render_notices and notices):
        return payload
    names = lookup(conn, found) if found else {}
    _apply(payload, names)
    if render_notices:
        for message in notices:
            # A task the server filed has no "from" a person reads: "New task: <title>".
            text = ID.sub(lambda m: names.get(m.group(0), m.group(0)), TICO_FROM.sub("New task: ", message["body"]))
            if text != message["body"]:
                message.setdefault("body_raw", message["body"])
                message["body"] = text
    if actors and isinstance(payload, dict) and names and "actors" not in payload:
        payload["actors"] = dict(sorted(names.items()))
    return payload


def annotate_json(conn, raw, render_notices=False, actors=True):
    """The same for a JSON body; returns the new bytes, or None when the body is not an object or list."""
    try:
        payload = fast_json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, (dict, list)):
        return None
    annotate(conn, payload, render_notices, actors)
    return fast_json.dumps(payload)


def install_route_class(app, store):
    async def display_names(request, response):
        """Stable v2 answers carry display names beside actor ids (backend/names.py)."""
        # Only API JSON is editable. FileResponse, StreamingResponse and raw byte Response
        # objects must keep their bodies, lengths and digests, whatever their content type.
        if (
            not isinstance(response, JSONResponse)
            or request.method == "HEAD"
            or response.status_code != 200
            or "content-disposition" in response.headers
            or "x-content-sha256" in response.headers
            or "count" in request.query_params
            or not stable_path(app).fullmatch(request.url.path)  # the stable v2 routes (backend/names.py)
            or "application/json" not in response.headers.get("content-type", "")
        ):
            return response
        raw = response.body
        who = getattr(request.state, "identity", None)

        def work():
            with store.read() as c:
                return annotate_json(
                    c, raw, render_notices=bool(who and who.role in ("human", "owner")), actors=request.method == "GET"
                )

        try:
            body = await asyncio.get_running_loop().run_in_executor(None, work)
        except sqlite3.Error:
            body = None
        if body is not None:
            response.body = body
            response.headers["Content-Length"] = str(len(body))
        return response

    def run_names(request, response):
        """An answer's `attempt_id` keys read `run_id`: storage's attempt is the contract's run (backend/runs.py).
        File downloads and other raw bytes keep theirs."""
        body = getattr(response, "body", None)
        if (
            not body
            or "content-disposition" in response.headers
            or "x-content-sha256" in response.headers
            or "content-encoding" in response.headers
            or "application/json" not in response.headers.get("content-type", "")
        ):
            return response
        renamed = runs.public_json(body, request.url.path)
        if renamed is not body:
            response.body = renamed
            response.headers["Content-Length"] = str(len(renamed))
        return response

    class DisplayNameRoute(APIRoute):
        def __init__(self, path, endpoint, **options):
            # FastAPI calls the handler it was given; the JSON it answers is encoded by orjson (backend/fast_json.py).
            # `endpoint` stays the handler itself, for the callers that read its dicts.
            plain = isinstance(options.get("response_class", DefaultPlaceholder(None)), DefaultPlaceholder)
            super().__init__(
                path, fast_json.answering_json(endpoint, options.get("status_code")) if plain else endpoint, **options
            )
            self.endpoint = endpoint

        def get_route_handler(self):
            handler = super().get_route_handler()

            async def named_response(request):
                from . import shared_reads
                from .json_response import compress

                async def answer():
                    return run_names(request, await display_names(request, await handler(request)))

                # A person's identical reads run at most once a second (backend/shared_reads.py); each request gets its
                # own validators and wire encoding.
                return await compress(request, await shared_reads.answer(store, request, self.path, answer))

            return named_response

    # HTTP middleware's call_next wraps every response as a stream, hiding its original
    # type. Annotate before that wrapping so actual file/stream responses stay untouched.
    app.router.route_class = DisplayNameRoute
