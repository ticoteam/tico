"""Building a FastAPI app costs a test worker less from the second app on (conftest.py pytest_configure calls `install`).

A test app registers about 1,500 routes, and with its database copied (backend/tests/app_snapshots.py) that is nearly
all of a test's setup: about 0.5 s of CPU per app, thousands of apps per run. Two parts of it repeat the same work for
every app and are kept for the worker:

- Python's regex cache holds 512 patterns, so each app compiled every route's path pattern again (`re._MAXCACHE`).
- FastAPI builds a pydantic TypeAdapter for each route parameter and response model (fastapi/_compat/v2.py ModelField),
  about a third of the build. An adapter depends only on the annotation, its metadata, the field's attributes and the
  config; equal ones share an adapter. The annotation is compared by identity, so a class an app defines for itself
  never shares one with another app's; metadata and attributes by their repr, which spells out a function's address.

A server builds one app, so none of this is in the product. Nothing about a request or its validation changes.
"""

import re
import typing

ADAPTERS = {}
# One app makes about 1,050 distinct adapters; about 16 more per app hold that app's own functions and never repeat.
ADAPTERS_MAX = 3000


def adapter_key(field, asdict):
    info = asdict(field.field_info)
    annotation = info["annotation"]
    if isinstance(annotation, (str, typing.ForwardRef)):
        return None  # resolved in the namespace of its own module: never shared
    try:
        hash(annotation)
    except TypeError:
        return None
    attributes = sorted((name, repr(value)) for name, value in info["attributes"].items())
    return (annotation, tuple(repr(item) for item in info["metadata"]), tuple(attributes), repr(field.config))


def install():
    """Keep regexes and TypeAdapters for the worker. Does nothing when FastAPI's internals are not the expected ones."""
    re._MAXCACHE = max(getattr(re, "_MAXCACHE", 0), 8192)
    try:
        from fastapi._compat import v2
    except ImportError:
        return
    model_field = getattr(v2, "ModelField", None)
    build = getattr(model_field, "__post_init__", None)
    if build is None or not hasattr(v2, "asdict") or getattr(build, "kept", False):
        return

    def post_init(self):
        key = adapter_key(self, v2.asdict)
        if key is not None and key in ADAPTERS:
            self._type_adapter = ADAPTERS[key]
            return
        build(self)
        if key is not None and len(ADAPTERS) < ADAPTERS_MAX:
            ADAPTERS[key] = self._type_adapter

    post_init.kept = True
    model_field.__post_init__ = post_init
