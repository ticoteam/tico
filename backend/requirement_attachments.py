# pyright: strict
"""Evidence attached to a stage requirement on a ticket: a file, a link, a check run or a receipt, recorded with the digest
and work version it was attached to. Track B owns this module. Nothing is configured in F0."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def attachments_for(conn: "sqlite3.Connection", task_id: str, stage_requirement_id: str) -> list[dict[str, Any]]:
    """The attachments a ticket has against one requirement, newest first. Empty in F0."""
    return []


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The attachment routes. None yet."""
    return None
