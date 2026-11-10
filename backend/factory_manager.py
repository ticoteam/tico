# pyright: strict
"""The Factory Manager's run log, read only: GET /api/v2/factories/{factory_id}/manager.

Its pass is gone with the old engine: every item it found read the old pipeline tables (a pipeline manager that stopped,
the stage with the most cards over their limit, a handoff with no intake). Nothing writes the log now. The route stays
only until app.py stops installing it; then this module goes.
"""

from typing import TYPE_CHECKING, Any

from . import hubdb

# hubdb is untyped: its functions are Any here, not partially unknown.
H: Any = hubdb

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth


def view(c: "sqlite3.Connection", factory_id: str) -> dict[str, Any] | None:
    """The factory's manager and its run log (errors first, then the newest runs and actions). None if no such factory."""
    if "manager_id" not in {row[1] for row in c.execute("PRAGMA table_info(factories)")}:
        return None
    found = c.execute("SELECT id, name, manager_id FROM factories WHERE id=?", (factory_id,)).fetchone()
    if found is None:
        return None
    runs = H._rows(
        c.execute(
            "SELECT id, started_at, finished_at, status, bottleneck_stage_id, judgment_count, woke, error "
            "FROM factory_manager_runs WHERE factory_id=? ORDER BY started_at DESC, id DESC LIMIT 50",
            (factory_id,),
        )
    )
    actions = H._rows(
        c.execute(
            "SELECT id, action, target_actor, note, created_at FROM factory_manager_actions WHERE factory_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT 50",
            (factory_id,),
        )
    )
    return {
        "factory_id": factory_id,
        "name": found["name"],
        "manager_id": found["manager_id"],
        "last_run": runs[0] if runs else None,
        "errors": [run for run in runs if run["status"] == "failed"],
        "runs": runs,
        "actions": actions,
    }


def install(app: "FastAPI", store: Any, mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """GET /api/v2/factories/{factory_id}/manager: the Factory Manager's run log (read only)."""
    from fastapi import Request

    from .store import Problem

    @app.get("/api/v2/factories/{factory_id}/manager")
    def factory_manager_read(request: Request, factory_id: str) -> dict[str, Any]:
        """The factory's Factory Manager: its last pass, its run log with errors first, and its actions."""
        who = request.state.identity
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "People and bots read the Factory Manager", 403)
        with store.read() as c:
            out = view(c, factory_id)
        if out is None:
            raise Problem("not_found", "No such factory", 404)
        return out
