"""JSON answers encoded by orjson, in the handler's own thread.

FastAPI turns a handler's dict or list into JSON by walking it with `jsonable_encoder` in pure Python, on the event
loop, and then `json.dumps`; for a page of 500 tasks that was about 100 ms during which no other request ran, health
checks and runner heartbeats included. A route's handler now answers its dicts and lists as an `OrjsonResponse`: one
orjson encode, done in the thread pool for a sync handler. Anything orjson refuses falls back to the old encoder.
"""
import functools
import inspect
import json
import typing

import orjson
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

OPTIONS = orjson.OPT_NON_STR_KEYS


def dumps(content):
    try:
        return orjson.dumps(content, default=jsonable_encoder, option=OPTIONS)
    except (TypeError, orjson.JSONEncodeError):
        # An integer beyond 64 bits, or a value jsonable_encoder cannot make plain: the encoder answers used before.
        return json.dumps(jsonable_encoder(content), ensure_ascii=False, separators=(",", ":")).encode()


def loads(raw):
    return orjson.loads(raw)


class OrjsonResponse(JSONResponse):
    def render(self, content):
        return dumps(content)


def answering_json(endpoint, status_code=None):
    """`endpoint`, answering its dicts and lists as an OrjsonResponse; its signature (FastAPI's parameters) and whether
    it is async stay the same. Streams and generators are left alone, as is any other answer."""
    if inspect.isgeneratorfunction(endpoint) or inspect.isasyncgenfunction(endpoint):
        return endpoint
    code = status_code or 200
    # A handler that takes FastAPI's `response: Response` sets headers (an ETag) or a status on it; FastAPI only
    # applies those to answers it builds itself, so they are carried over here.
    try:
        hints = typing.get_type_hints(endpoint)
    except Exception:
        hints = getattr(endpoint, "__annotations__", {})
    sub = next((name for name, kind in hints.items()
                if name != "return" and isinstance(kind, type) and issubclass(kind, Response)), None)

    def answer(out, kwargs):
        if not isinstance(out, (dict, list)):
            return out
        given = kwargs.get(sub) if sub else None
        response = OrjsonResponse(out, status_code=(given.status_code if given is not None and given.status_code
                                                    else code))
        if given is not None:
            response.headers.raw.extend((k, v) for k, v in given.headers.raw if k != b"content-length")
        return response

    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def run(*args, **kwargs):
            return answer(await endpoint(*args, **kwargs), kwargs)
    else:
        @functools.wraps(endpoint)
        def run(*args, **kwargs):
            return answer(endpoint(*args, **kwargs), kwargs)
    return run
