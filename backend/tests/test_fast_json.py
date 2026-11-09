"""JSON answers by orjson (backend/fast_json.py): the same JSON as before, and FastAPI still sees the same handler."""

import asyncio
import inspect
import json
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from backend import fast_json


class Item(BaseModel):
    id: str
    tags: list[str]


def test_dumps_matches_the_old_encoder():
    content = {
        "tasks": [{"id": "t1", "title": "Naïve café ✓", "n": 3, "ok": True, "none": None, "f": 1.5}],
        "when": datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc),
        "labels": {"b", "a"},
        7: "int key",
        "model": Item(id="i1", tags=["x"]),
    }
    old = json.loads(json.dumps(jsonable_encoder(content), ensure_ascii=False))
    new = json.loads(fast_json.dumps(content))
    assert sorted(new.pop("labels")) == sorted(old.pop("labels"))
    assert new == old


def test_what_orjson_refuses_falls_back():
    assert json.loads(fast_json.dumps({"big": 2**70})) == {"big": 2**70}


def test_handlers_keep_their_signature_and_kind():
    def sync(request, tid: str, limit: int = 5):
        return {"tid": tid, "limit": limit}

    async def coro(tid: str):
        return [tid]

    def stream():
        yield b"x"

    wrapped = fast_json.answering_json(sync, 201)
    assert inspect.signature(wrapped) == inspect.signature(sync)
    out = wrapped(None, "t1")
    assert isinstance(out, fast_json.OrjsonResponse) and out.status_code == 201
    assert json.loads(out.body) == {"tid": "t1", "limit": 5}
    async_wrapped = fast_json.answering_json(coro)
    assert inspect.iscoroutinefunction(async_wrapped)
    assert json.loads(asyncio.run(async_wrapped("t2")).body) == ["t2"]
    assert fast_json.answering_json(stream) is stream
    assert fast_json.answering_json(lambda: "text")() == "text"


def test_headers_set_on_fastapis_response_are_kept():
    from fastapi import Response

    def listing(response: Response):
        response.headers["ETag"] = '"v1"'
        return {"tasks": []}

    sub = Response()
    sub.status_code = None
    out = fast_json.answering_json(listing)(response=sub)
    assert out.headers["etag"] == '"v1"' and out.status_code == 200 and json.loads(out.body) == {"tasks": []}
