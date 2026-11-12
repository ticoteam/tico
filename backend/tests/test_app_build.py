"""Kept TypeAdapters (backend/tests/app_build.py): equal fields share one; a class of its own or a different
constraint never does, a forward reference is never kept, and validation through a shared one is the same."""

from typing import Annotated

import pydantic
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from backend.tests import app_build


def app(limit):
    class Body(pydantic.BaseModel):
        name: str

    api = FastAPI()

    @api.get("/items")
    def items(n: Annotated[int, Query(le=limit)] = 1):
        return {"n": n}

    @api.post("/body")
    def body(item: Body):
        return {"same": type(item) is Body}

    return api


def test_apps_share_equal_adapters_and_never_a_class_or_a_limit_of_their_own():
    app_build.install()
    first, second, other = TestClient(app(5)), TestClient(app(5)), TestClient(app(9))
    for client in (first, second):
        assert client.get("/items", params={"n": 5}).json() == {"n": 5}
        assert client.get("/items", params={"n": 6}).status_code == 422
        assert client.post("/body", json={"name": "a"}).json() == {"same": True}
    assert other.get("/items", params={"n": 9}).json() == {"n": 9}


def test_a_forward_reference_is_never_kept():
    from fastapi._compat import v2

    class Field:
        field_info = pydantic.fields.FieldInfo(annotation="Body")
        config = None

    assert app_build.adapter_key(Field, v2.asdict) is None


def test_an_apps_own_class_or_function_is_never_kept_so_no_app_outlives_its_test():
    from fastapi._compat import v2

    class Body(pydantic.BaseModel):
        name: str

    class Field:
        field_info = pydantic.fields.FieldInfo(annotation=Body)
        config = None

    class Made:
        field_info = pydantic.fields.FieldInfo(annotation=int, default_factory=lambda: 1)
        config = None

    assert app_build.adapter_key(Field, v2.asdict) is None
    assert app_build.adapter_key(Made, v2.asdict) is None
