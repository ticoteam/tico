# pyright: strict
"""The services a route module's handlers take, one set per app.

`create_app` puts a `Deps` on `app.state.deps` once it has made the mutation. A handler takes it as a parameter typed
`Injected` (`Depends(get_deps)`), read from its own request, so two apps in one process (tests build many) each answer
from their own store. Only what a module that left backend/app.py needs goes here (backend/api_routing.py says how
such a module builds its routes):

  store   the app's Store (backend/store.py)
  auth    the app's Auth (backend/auth.py)
  mutate  the app's write path (app._mutation): mutate(request, body, work) runs work(c) in one keyed transaction
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Request

if TYPE_CHECKING:
    from .auth import Auth
    from .store import Store


@dataclass(frozen=True)
class Deps:
    store: Store
    auth: Auth
    mutate: Callable[..., Any]


def get_deps(request: Request) -> Deps:
    """The services of the app this request came to."""
    return request.app.state.deps


# A handler's parameter type: `deps: Injected`. FastAPI reads it from the request; it adds no parameter to the document.
Injected = Annotated[Deps, Depends(get_deps)]
