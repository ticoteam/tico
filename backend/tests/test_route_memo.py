"""Apps built in one process share their routes' field validators (backend/route_memo.py), never their handlers,
state or each other's types."""
import subprocess
import sys
from pathlib import Path
from typing import Literal

import fastapi
import pydantic
import pytest
import starlette
from fastapi import FastAPI, Header, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend import route_memo as RM

# The versions whose route building route_memo's keys were checked against: what get_dependant, ModelField and
# compile_path read. An upgrade can make them read more than a key holds.
REVIEWED = {"fastapi": "0.141.1", "starlette": "1.8.0", "pydantic": "2.14.1"}


@pytest.fixture(autouse=True)
def no_cap_clear(monkeypatch):
    """A cache reaching its cap clears itself; these tests compare what two builds share, so none clears mid-test."""
    monkeypatch.setattr(RM, "ADAPTER_LIMIT", 10**9)


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


def dependant(app, path):
    return next(r for r in app.routes if getattr(r, "path", None) == path).dependant


def fields(app, path):
    found = dependant(app, path)
    return found.path_params + found.query_params + found.body_params


def route(app, path):
    return next(r for r in app.routes if getattr(r, "path", None) == path)


def test_a_second_app_reuses_validators_but_keeps_its_own_state_and_types():
    first, second = build("first"), build("second")
    # FastAPI really goes through the memo: a renamed or bypassed get_dependant or compile_path fails here.
    assert route(first, "/items/{item_id}").path_regex is route(second, "/items/{item_id}").path_regex
    assert RM.DEPENDANTS
    one, two = dependant(first, "/items/{item_id}"), dependant(second, "/items/{item_id}")
    assert one.call is not two.call and one.query_params is not two.query_params
    shared = list(zip(fields(first, "/items/{item_id}"), fields(second, "/items/{item_id}")))
    assert len(shared) == 3
    for a, b in shared:
        assert a is b and a._type_adapter is b._type_adapter, a.name
    # A model made inside the function is each app's own, and is never kept by the cache.
    (note_a,), (note_b,) = fields(first, "/notes"), fields(second, "/notes")
    assert note_a is not note_b and note_a._type_adapter is not note_b._type_adapter
    assert not any(".build.<locals>." in repr(key) for key in [*RM.ADAPTERS, *RM.DEPENDANTS])
    assert all(template.call is None for template in RM.DEPENDANTS.values())
    sizes = len(RM.ADAPTERS), len(RM.DEPENDANTS)
    build("third")
    assert (len(RM.ADAPTERS), len(RM.DEPENDANTS)) == sizes

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


def test_a_signature_differing_only_in_what_get_dependant_reads_is_not_shared():
    """Header(None) and Header(None, convert_underscores=False) build the same validator but different aliases."""
    def header_app(convert):
        app = FastAPI()

        @app.get("/h")
        def read(x_token: str | None = Header(None, convert_underscores=convert)):
            return {"token": x_token}
        return app
    converted, kept = header_app(True), header_app(False)
    assert dependant(converted, "/h").header_params[0].alias == "x-token"
    assert dependant(kept, "/h").header_params[0].alias == "x_token"
    assert TestClient(kept).get("/h", headers={"x_token": "t"}).json() == {"token": "t"}
    assert header_app(True) and dependant(header_app(False), "/h").header_params[0].alias == "x_token"


def test_route_building_is_still_the_reviewed_version():
    found = {"fastapi": fastapi.__version__, "starlette": starlette.__version__, "pydantic": pydantic.VERSION}
    assert found == REVIEWED, (
        "FastAPI, Starlette or pydantic changed: re-review backend/route_memo.py's keys (what get_dependant, "
        "ModelField.__post_init__ and compile_path read) against the new version, then update REVIEWED.")


def test_paths_compile_once_into_their_own_convertor_dicts():
    one, two = RM.compile_path("/a/{x:int}"), RM.compile_path("/a/{x:int}")
    assert one[0] is two[0] and one[1] == "/a/{x}" and one[2] == two[2] and one[2] is not two[2]


def test_only_test_processes_patch_fastapi():
    """The root conftest.py installs the memo in every test process; a server process importing the app keeps
    FastAPI's own route building."""
    import fastapi.routing
    assert fastapi.routing.get_dependant is RM.get_dependant and fastapi.routing.compile_path is RM.compile_path
    check = ("import backend.app, fastapi.routing as r, fastapi.dependencies.utils as u, starlette.routing as s\n"
             "from fastapi._compat import v2\n"
             "from backend import route_memo as RM\n"
             "assert r.get_dependant is u.get_dependant is RM._get_dependant\n"
             "assert r.compile_path is s.compile_path is RM._compile_path\n"
             "assert v2.ModelField.__post_init__ is RM._build_adapter\n")
    result = subprocess.run([sys.executable, "-c", check], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
