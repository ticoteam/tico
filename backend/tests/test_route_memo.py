"""Apps built in one process share their field validators (backend/route_memo.py), never their state or each
other's types."""
from typing import Literal

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend import route_memo as RM

RM.install()


def build(store):
    """An app the way create_app builds one: handlers and models made inside a function, over its own state."""
    app = FastAPI()

    class Note(BaseModel):      # one class per app
        text: str

    @app.get("/items/{item_id}")
    def item(item_id: int, kind: Literal["a", "b"] = Query("a"), limit: int = Query(10, ge=1)):
        return {"store": store, "item": item_id, "kind": kind, "limit": limit}

    @app.post("/notes")
    def note(body: Note):
        return {"store": store, "text": body.text}
    return app


def fields(app, path):
    route = next(r for r in app.routes if getattr(r, "path", None) == path)
    return route.dependant.path_params + route.dependant.query_params + route.dependant.body_params


def test_a_second_app_reuses_validators_but_keeps_its_own_state_and_types():
    first, second = build("first"), build("second")
    for a, b in zip(fields(first, "/items/{item_id}"), fields(second, "/items/{item_id}")):
        assert a._type_adapter is b._type_adapter, a.name
        assert a is not b and a.field_info is not b.field_info
    # A model made inside the function is each app's own, and is never kept by the cache.
    (note_a,), (note_b,) = fields(first, "/notes"), fields(second, "/notes")
    assert note_a._type_adapter is not note_b._type_adapter
    assert not any("<locals>" in repr(key) for key in RM.ADAPTERS)
    size = len(RM.ADAPTERS)
    build("third")
    assert len(RM.ADAPTERS) == size

    client = TestClient(second)
    assert client.get("/items/7?kind=b&limit=3").json() == {"store": "second", "item": 7, "kind": "b", "limit": 3}
    assert client.post("/notes", json={"text": "hi"}).json() == {"store": "second", "text": "hi"}
    for bad in ("/items/x", "/items/7?kind=c", "/items/7?limit=0"):
        assert client.get(bad).status_code == 422, bad
    assert client.post("/notes", json={}).status_code == 422


def test_keys_tell_apart_values_that_compare_equal():
    assert len({RM.frozen(1), RM.frozen(True), RM.frozen(1.0)}) == 3
    assert RM.frozen(Literal[1]) != RM.frozen(Literal[True])
    assert RM.frozen((1, [2])) != RM.frozen((1, (2,)))
    assert RM.adapter_key(Query(1), None) != RM.adapter_key(Query(True), None)
    assert RM.adapter_key(Query(1, ge=1), None) != RM.adapter_key(Query(1, ge=2), None)
    assert RM.adapter_key(Query(1, ge=1), None) == RM.adapter_key(Query(1, ge=1), None)

    class Local(BaseModel):
        x: int
    for made_here in (Local, list[Local], lambda: 1, object()):
        with pytest.raises(RM.Uncacheable):
            RM.frozen(made_here)


def test_paths_compile_once_into_their_own_convertor_dicts():
    one, two = RM.compile_path("/a/{x:int}"), RM.compile_path("/a/{x:int}")
    assert one[0] is two[0] and one[1] == "/a/{x}" and one[2] == two[2] and one[2] is not two[2]
