# pyright: strict
"""Stage requirements: the rules a stage sets (its kind, what it needs, who reviews), their versions, and a pipeline's
prefix and settings. Only the pipeline's owner and admins edit them (pipeline_rights). Track B owns this module.
Nothing is configured in F0."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def requirements_for_stage(conn: "sqlite3.Connection", pipeline_stage_id: str) -> list[dict[str, Any]]:
    """The live requirements of a stage in position order. Empty in F0."""
    return []


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The requirement routes. None yet."""
    return None
