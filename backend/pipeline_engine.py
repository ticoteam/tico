# pyright: strict
"""The pipeline engine: what a stage's requirements ask of a ticket before it leaves the stage, the move gate, and where
each requirement stands on the ticket's card.

F0 lands the hooks with no behaviour behind them. Every function answers "nothing configured" until a stage has
stage_requirements rows, so a move, a status change or a task payload is the same as before for every pipeline
without them:

- hubdb.task_update calls `check_move` before the review gate when a ticket changes step.
- hubdb._set_status calls `on_step_change` after it writes the step event.
- app.py fills each task's `requirements` from `dots_many`; a task with none has no such key.

Track A replaces these bodies. Other tracks call this module's public functions and do not reach into hubdb for it.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store


def check_move(
    conn: "sqlite3.Connection", actor: str, row: dict[str, Any], step_id: str | None, override: bool = False
) -> None:
    """Before a ticket changes to `step_id`: refuse the move when that stage's requirements do not allow it, unless a rule
    editor overrides them (logged). Nothing is configured in F0, so this does nothing."""
    return None


def on_step_change(
    conn: "sqlite3.Connection", actor: str, row: dict[str, Any], type_id: str | None, step_id: str | None, ts: str
) -> None:
    """After a ticket's step changed and its step event is written: open its stage visit and record what the requirements
    need. Nothing is configured in F0, so this does nothing."""
    return None


def dots_many(c: "sqlite3.Connection", rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """{task id: [requirement dot, ...]} for the tickets among `rows` whose stage has requirements, from one query for the
    page. Nothing is configured in F0, so it is empty."""
    return {}


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The engine's routes. None yet: Track A adds any it needs here."""
    return None
