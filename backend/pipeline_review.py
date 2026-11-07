# pyright: strict
"""Review mode: the queue a reviewer works through, claims, Approve with its 10-second undo, send-back to the assignee,
and Message manager. Track C owns this module. Nothing is configured in F0."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def queue_for(c: "sqlite3.Connection", actor: str, pipeline_id: str | None = None) -> list[dict[str, Any]]:
    """The items waiting for this reviewer, over-limit first, then oldest. Empty in F0."""
    return []


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The review routes. None yet."""
    return None
