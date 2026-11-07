# pyright: strict
"""Handoff links between pipelines: a stage that sends its tickets on to another pipeline. `move` hands the ticket over,
`spawn` makes a new one and records the handoff. Track A owns this module. Nothing is configured in F0."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def link_for_stage(conn: "sqlite3.Connection", pipeline_stage_id: str) -> dict[str, Any] | None:
    """The live link that leaves this stage, or None. None in F0."""
    return None


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The links' routes. None yet."""
    return None
