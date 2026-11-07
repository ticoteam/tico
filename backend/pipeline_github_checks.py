# pyright: strict
"""The GitHub check run that a `run` requirement publishes on the pull request at the ticket's commit. It needs the
`checks: write` grant in github_app.py, which is withheld until it is accepted. Track C owns this module. Nothing is
configured in F0."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def publish(conn: "sqlite3.Connection", task_id: str, stage_requirement_id: str, state: str, head_sha: str) -> None:
    """Publishes one check run's state for a requirement. None in F0."""
    return None


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """Check runs have no routes."""
    return None
