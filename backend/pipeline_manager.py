# pyright: strict
"""The pipeline manager: a bot for each pipeline that asks, refiles, holds and forwards the tickets at its stages. Its
watchdog reports a pass that stopped without finishing. Nothing is configured in F0, so both entry points return at once."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def tick(store: "Store", at: Any) -> None:
    """One manager pass for each pipeline that has a manager. None in F0."""
    return None


def watchdog(store: "Store", at: Any) -> None:
    """Reports a manager pass that stopped without finishing. None in F0."""
    return None


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The manager has no routes."""
    return None
