# pyright: strict
"""Put a route in the stable v2 contract (`GET /api/v2/openapi.json`, built by backend/openapi_v2.py) where it is
declared:

    @app.get("/api/v2/things", **api_v2("Things", "listThings", "The things a person can see", "ThingList"))

gives the operation its tag (a key of openapi_v2.TAGS), operationId and summary, and names its 200 answer in
openapi_v2.SCHEMAS (None keeps what FastAPI says). A route without it is internal: absent from the document and free
to change in any release. What FastAPI cannot see in a handler (a body read by hand, an answer that is not JSON, the
errors it gives) is said here too, next to the route.
"""

from collections.abc import Mapping
from typing import Any

# The openapi_extra key that marks a stable route and carries what api_v2 says about it; spec() removes it.
MARK = "x-tico-v2"


def ref(name: str) -> dict[str, str]:
    """A reference to the schema `name` in the document's components."""
    return {"$ref": "#/components/schemas/" + name}


# A response that is an error: {"error": {...}}. Give it its own description: {**ERROR, "description": "..."}.
ERROR: dict[str, Any] = {
    "description": 'A problem: {"error": {"code", "detail", "retryable", ...}}',
    "content": {"application/json": {"schema": ref("Problem")}},
}

# The 422 of a write a bot may ask through: its ask can fail the person's asking criteria.
ASK_REFUSED: dict[str, Any] = {
    **ERROR,
    "description": "ask_refused: a bot ask fails the person's asking criteria; "
    "detail gives the reason and fix. Nothing is created or changed; no quarantine "
    "or org-rule hold. Progress notes should be posted as updates.",
}

# The Idempotency-Key note of a PUT whose handler never passes through Store.mutate.
KEYLESS = "Optional: this write does not check the header."


def api_v2(
    tag: str,
    operation_id: str,
    summary: str,
    answer: str | dict[str, Any] | None = None,
    *,
    description: str | None = None,
    parameters: dict[str, str] | None = None,
    request_body: dict[str, Any] | None = None,
    responses: Mapping[str, Mapping[str, Any] | None] | None = None,
    security: list[dict[str, list[str]]] | None = None,
    idempotency: str | None = None,
    openapi_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The route decorator's keyword arguments for one stable operation. The operationId goes in openapi_extra, not
    FastAPI's operation_id, which would also rename the route's generated schemas.

    answer: the 200 answer, a schema name in SCHEMAS or the whole response object (bytes, CSV, a stream).
    description: the operation's description; the handler's docstring is never published.
    parameters: descriptions of FastAPI's parameters by name (a description given to Query() would reach the schema too).
    request_body: the body, for a handler that reads it by hand.
    responses: more responses by status code, over the 401 and 422 every signed-in operation has; None drops one.
    security: [] for a route open without sign-in (it has no 401 or 422 either), or the schemes it takes instead.
    idempotency: for a /api/v2 write that does not need the Idempotency-Key header, what the header does instead.
    """
    contract: dict[str, Any] = {
        key: value
        for key, value in (
            ("answer", answer),
            ("description", description),
            ("parameters", parameters),
            ("request_body", request_body),
            ("responses", responses),
            ("security", security),
            ("idempotency", idempotency),
        )
        if value is not None
    }
    return {
        "tags": [tag],
        "summary": summary,
        "openapi_extra": {**(openapi_extra or {}), "operationId": operation_id, MARK: contract},
    }
