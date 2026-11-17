# pyright: strict
"""Put a route in the stable v2 contract (`GET /api/v2/openapi.json`, built by backend/openapi_v2.py) where it is
declared:

    @app.get("/api/v2/things", **api_v2("Things", "listThings", "The things a person can see", "ThingList"))

gives the operation its tag (a key of openapi_v2.TAGS), operationId and summary, and names its 200 answer in
openapi_v2.SCHEMAS (None keeps what FastAPI says). A route without it is internal: absent from the document and free
to change in any release.
"""

from typing import Any

# The openapi_extra key that marks a stable route and carries its answer's schema name; spec() removes it.
MARK = "x-tico-v2"


def api_v2(
    tag: str,
    operation_id: str,
    summary: str,
    answer: str | None = None,
    *,
    openapi_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The route decorator's keyword arguments for one stable operation. The operationId goes in openapi_extra, not
    FastAPI's operation_id, which would also rename the route's generated schemas."""
    return {
        "tags": [tag],
        "summary": summary,
        "openapi_extra": {**(openapi_extra or {}), "operationId": operation_id, MARK: answer},
    }
