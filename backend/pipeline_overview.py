# pyright: strict
"""Overview > Pipelines: the pipelines a company has, their stages, and how work moves through them. Track F owns this
module. Nothing is configured in F0."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def overview(c: "sqlite3.Connection", actor: str) -> dict[str, list[dict[str, Any]]]:
    """The pipelines this actor may see, with their stages. Empty in F0."""
    return {"pipelines": []}


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The overview's routes. None yet."""
    return None
