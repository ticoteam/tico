# pyright: strict
"""Tico Team pipeline setup (placeholder, replaced next)."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def install(app: "FastAPI", store: "Store", mutate: Callable[..., Any], auth: "Auth") -> None:
    return None
