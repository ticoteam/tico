"""The thread routes' OpenAPI drafts (backend/openapi_threads.py) cover the contract and stay well-formed until the
routes ship and move into the served document (backend/tests/test_openapi_v2.py checks that part)."""

import json

from backend import openapi_threads as drafts
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


def test_the_drafts_cover_every_route_the_contract_lists():
    assert {(method, path) for path, method, *_ in drafts.ROUTES} == CONTRACT


def test_every_draft_is_tagged_summarised_and_names_an_operation_nothing_else_uses():
    ids = [route[3] for route in drafts.ROUTES]
    assert len(ids) == len(set(ids))
    assert not set(ids) & {row[3] for row in openapi_v2.STABLE}
    for path, method, tag, op_id, summary, answer in drafts.ROUTES:
        assert tag == drafts.TAG[0] and summary, op_id


def test_every_declared_answer_and_reference_resolves_to_a_declared_schema():
    answers = {route[5] for route in drafts.ROUTES if route[5]}
    assert answers <= set(drafts.SCHEMAS)
    found = list(_refs(drafts.SCHEMAS))
    assert all(ref.startswith("#/components/schemas/") for ref in found)
    assert {ref.rsplit("/", 1)[-1] for ref in found} <= set(drafts.SCHEMAS)
    json.dumps(drafts.SCHEMAS)


def test_the_hidden_row_and_the_required_fields_match_the_contract():
    assert drafts.THREAD_MESSAGE["required"] == ["id", "seq", "thread_id"]
    assert {"hidden", "deleted", "sub_thread"} <= set(drafts.THREAD_MESSAGE["properties"])
    assert {"attention", "threads", "dms", "next_cursor"} <= set(drafts.THREAD_SIDEBAR["properties"])
    assert "breadcrumb" in drafts.THREAD_DETAIL["properties"]
