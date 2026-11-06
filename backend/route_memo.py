"""Route building that does not repeat work an earlier app already did in this process.

`create_app` adds about 1,440 routes. For each one FastAPI analyses the handler's signature into a `Dependant`, builds a
pydantic `TypeAdapter` per parameter and response field and compiles the path's regex; that was most of the time an
app took to build. The handlers are closures over each app's own store and settings, so the routes themselves are
rebuilt every time, but three things are pure functions of their inputs and are shared here:

- a handler's `Dependant`, keyed by the path, the options and the typed signature by value. Each app gets its own copy
  with its own handler and lists; the template kept here holds no handler;
- a field's `TypeAdapter`, keyed by a by-value copy of exactly what FastAPI builds it from (the annotation, its
  metadata and the field's attributes). A key holds only values and module-level classes and functions; a field
  that names anything else (a model class made inside `install`, one per app, or the body model FastAPI generates
  per route) builds its own adapter as before, so no app's objects outlive it and no app ever validates with an
  adapter built for another app's types. An adapter holds no state: it validates and serializes;
- a path's compiled regex, format and convertors (convertors are module singletons; each route gets its own dict).

Only tests install it (the root conftest.py, in every pytest process, xdist workers included): a test process builds
an app per test, a server builds one. Production imports never patch FastAPI (backend/tests/test_route_memo.py).
"""
import dataclasses
import enum
import functools
import sys
import types
import typing

import annotated_types
from fastapi import routing as fastapi_routing
from fastapi._compat import v2 as fastapi_v2
from fastapi.dependencies import utils as fastapi_dependencies
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from starlette import routing as starlette_routing

ADAPTER_LIMIT = 20_000
ADAPTERS = {}
DEPENDANTS = {}
_SCALARS = (str, int, float, bool, bytes, complex, type(None))
_ATTRS = tuple(fastapi_v2._Attrs)     # the FieldInfo attributes FastAPI copies into a field's adapter


class Uncacheable(Exception):
    pass


class _Same:
    """An object compared by identity in a key; it holds its object, so its id is never reused while the key lives."""
    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return isinstance(other, _Same) and other.obj is self.obj

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"_Same({self.obj!r})"


def _module_level(obj):
    """Whether `obj` is what its module names at its qualified name: one object for the life of the process. A class
    or function made inside a function (each app's, or a model FastAPI generates per route) is not."""
    module = getattr(obj, "__module__", None)
    if module == "builtins":
        return True
    found = sys.modules.get(module)
    for part in (getattr(obj, "__qualname__", None) or "<none>").split("."):
        found = getattr(found, part, None)
    return found is obj


def frozen(value, depth=0):
    """A hashable copy of `value` equal only to the copy of an equivalent value. Scalars carry their exact type (1,
    1.0 and True stay apart); containers, typing forms, dataclasses and field infos are copied member by member;
    module-level classes and functions, enum members and pydantic's Undefined are kept by identity. Anything else,
    a class or function made inside a function included, raises Uncacheable: it is never kept in a key."""
    if depth > 12:
        raise Uncacheable
    kind = type(value)
    if kind in _SCALARS:
        return (kind, value)
    if kind in (tuple, list):
        return (kind, tuple(frozen(item, depth + 1) for item in value))
    if kind in (set, frozenset):
        return (kind, frozenset(frozen(item, depth + 1) for item in value))
    if kind is dict:
        return (dict, tuple((frozen(k, depth + 1), frozen(v, depth + 1)) for k, v in value.items()))
    if value is PydanticUndefined or value is Ellipsis:
        return _Same(value)
    origin = typing.get_origin(value)
    if origin is not None:
        args = getattr(value, "__metadata__", None)
        args = (value.__origin__, *args) if args is not None else typing.get_args(value)
        if isinstance(origin, type) and not _module_level(origin):
            raise Uncacheable
        # Any other origin is one of typing's own forms (Literal, Annotated, Union): one object per process.
        return ("form", kind, _Same(origin), tuple(frozen(arg, depth + 1) for arg in args))
    if isinstance(value, (type, types.FunctionType, types.BuiltinFunctionType)) or isinstance(value, enum.Enum):
        if not _module_level(value if not isinstance(value, enum.Enum) else kind):
            raise Uncacheable
        return _Same(value)
    if isinstance(value, typing.TypeVar | typing.ParamSpec | typing.TypeAliasType):
        if not _module_level(value):
            raise Uncacheable
        return _Same(value)
    if isinstance(value, FieldInfo):
        return ("field", kind, _field_parts(value, depth))
    if dataclasses.is_dataclass(value) and _module_level(kind):
        return ("dataclass", kind, tuple(frozen(getattr(value, f.name), depth + 1)
                                         for f in dataclasses.fields(value)))
    if isinstance(value, annotated_types.BaseMetadata) and hasattr(value, "__dict__"):
        # pydantic's general metadata (a pattern, max_digits): one cached class, holding only its __dict__.
        return ("metadata", kind, frozen(vars(value), depth + 1))
    raise Uncacheable


def _field_parts(field_info, depth):
    """What FastAPI builds a field's adapter from (fastapi._compat.v2.ModelField): its annotation, metadata and set
    attributes: exactly the attributes it passes on (fastapi._compat.v2.asdict)."""
    attributes = tuple((name, frozen(value, depth + 1)) for name in _ATTRS
                       if (value := getattr(field_info, name, PydanticUndefined)) is not PydanticUndefined)
    return (frozen(field_info.annotation, depth + 1), frozen(list(field_info.metadata), depth + 1), attributes)


def adapter_key(field_info, config):
    if config is not None:
        raise Uncacheable
    return (type(field_info), _field_parts(field_info, 0))


_build_adapter = fastapi_v2.ModelField.__post_init__


def _post_init(self):
    try:
        key = adapter_key(self.field_info, self.config)
        hash(key)
    except (Uncacheable, TypeError):
        return _build_adapter(self)
    adapter = ADAPTERS.get(key)
    if adapter is not None:
        self._type_adapter = adapter
        return
    _build_adapter(self)
    if len(ADAPTERS) >= ADAPTER_LIMIT:
        ADAPTERS.clear()
    ADAPTERS[key] = self._type_adapter


def dependant_key(path, call, name, own_oauth_scopes, parent_oauth_scopes, use_cache, scope):
    """What get_dependant reads: the path, the options and the handler's typed signature (names, kinds, annotations
    and defaults by value). The handler itself is not in it: each app's Dependant gets its own."""
    parameters = tuple((p.name, p.kind, frozen(p.annotation), frozen(p.default))
                       for p in fastapi_dependencies.get_typed_signature(call).parameters.values())
    return (path, name, frozen(own_oauth_scopes), frozen(parent_oauth_scopes), use_cache, scope, parameters)


_get_dependant = fastapi_dependencies.get_dependant


def get_dependant(*, path, call, name=None, own_oauth_scopes=None, parent_oauth_scopes=None, use_cache=True,
                  scope=None):
    options = dict(path=path, name=name, own_oauth_scopes=own_oauth_scopes, parent_oauth_scopes=parent_oauth_scopes,
                   use_cache=use_cache, scope=scope)
    try:
        key = dependant_key(call=call, **options)
        hash(key)
    except (Uncacheable, TypeError):
        return _get_dependant(call=call, **options)
    template = DEPENDANTS.get(key)
    if template is None:
        dependant = _get_dependant(call=call, **options)
        if dependant.dependencies:      # a Depends: its own handler, compared by identity; none in Tico
            return dependant
        if len(DEPENDANTS) >= ADAPTER_LIMIT:
            DEPENDANTS.clear()
        DEPENDANTS[key] = _copy_dependant(dependant, None)     # never an app's handler
        return dependant
    return _copy_dependant(template, call, own_oauth_scopes=own_oauth_scopes, parent_oauth_scopes=parent_oauth_scopes)


def _copy_dependant(dependant, call, **options):
    """`dependant` for `call`, in lists of its own (FastAPI inserts a route's own dependencies into them). The fields
    are shared: each holds a validator and a default that pydantic copies on every use."""
    return dataclasses.replace(dependant, call=call, **{kind: list(getattr(dependant, kind)) for kind in _PARAM_LISTS},
                               **options)


_PARAM_LISTS = ("path_params", "query_params", "header_params", "cookie_params", "body_params", "dependencies")


@functools.lru_cache(maxsize=None)
def _compiled(path):
    return _compile_path(path)


def compile_path(path):
    regex, path_format, convertors = _compiled(path)
    return regex, path_format, dict(convertors)


_compile_path = starlette_routing.compile_path


def install():
    """Once per test process, from the root conftest.py."""
    if fastapi_v2.ModelField.__post_init__ is _post_init:
        return
    fastapi_v2.ModelField.__post_init__ = _post_init
    starlette_routing.compile_path = compile_path
    fastapi_routing.compile_path = compile_path
    fastapi_routing.get_dependant = get_dependant
