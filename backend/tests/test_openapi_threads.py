"""The thread routes in the served OpenAPI document (backend/openapi_v2.py) cover contract section 10
(docs/dev/threads-contract.md) and stay well-formed. Their staged drafts moved into the served document when the routes
shipped, so this checks what is served (backend/tests/test_openapi_v2.py checks the document as a whole)."""

import re

from backend import openapi_v2

# Method and path of every thread route that contract section 10 lists (docs/dev/threads-contract.md).
CONTRACT = {
    ("get", "/api/v2/threads"), ("post", "/api/v2/threads"), ("get", "/api/v2/threads/search"),
    ("get", "/api/v2/threads/{thread}"), ("patch", "/api/v2/threads/{thread}"),
    ("get", "/api/v2/threads/{thread}/timeline"), ("post", "/api/v2/threads/{thread}/read"),
    ("post", "/api/v2/threads/{thread}/read-all"), ("get", "/api/v2/threads/{thread}/members"),
    ("post", "/api/v2/threads/{thread}/members"), ("delete", "/api/v2/threads/{thread}/members/{actor}"),
    ("put", "/api/v2/threads/{thread}/follow"), ("delete", "/api/v2/threads/{thread}/follow"),
    ("post", "/api/v2/threads/{thread}/messages"), ("patch", "/api/v2/messages/{mid}"),
    ("delete", "/api/v2/messages/{mid}"),
}
THREAD_ROWS = [row for row in openapi_v2.STABLE if row[2] == "Threads"]
SCHEMAS = openapi_v2.SCHEMAS


def _shape(path):
    """The path with every parameter name blanked: the contract and the served rows name them differently."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def test_the_served_document_covers_every_route_the_contract_lists():
    served = {(method, _shape(path)) for path, method, *_ in THREAD_ROWS}
    assert {(method, _shape(path)) for method, path in CONTRACT} <= served


def test_every_thread_route_is_tagged_summarised_and_names_its_own_operation():
    ids = [row[3] for row in THREAD_ROWS]
    assert len(ids) == len(set(ids))
    for path, method, tag, op_id, summary, answer in THREAD_ROWS:
        assert tag == "Threads" and summary, op_id
        assert answer is None or answer in SCHEMAS, op_id


def test_every_thread_schema_reference_resolves_to_a_declared_schema():
    threads = {name: schema for name, schema in SCHEMAS.items() if name.startswith("Thread")}
    found = list(_refs(threads))
    assert all(ref.startswith("#/components/schemas/") for ref in found)
    assert {ref.rsplit("/", 1)[-1] for ref in found} <= set(SCHEMAS)


def test_the_message_row_and_the_sidebar_match_the_contract():
    assert SCHEMAS["ThreadMessage"]["required"] == ["id", "seq", "thread_id"]
    assert {"hidden", "deleted"} <= set(SCHEMAS["ThreadMessage"]["properties"])
    assert {"attention", "threads", "dms", "next_cursor"} <= set(SCHEMAS["ThreadSidebar"]["properties"])
    assert "breadcrumb" in SCHEMAS["ThreadTimeline"]["properties"]
