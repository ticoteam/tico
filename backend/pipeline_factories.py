# pyright: strict
"""Factories: the groups of pipelines that make one thing (Product development: Product and Engineering). A factory has a
name, a goal, an owner (a person who may edit it) and a manager (a bot, the Factory Manager). A pipeline is in at most
one factory (task_types.factory_id), and a private pipeline is in none (D61).

Admins make a factory. Its owner or an admin edits it. Retiring a factory stops it counting and takes its pipelines out
of it; its rows and its manager's log stay. Routes: GET /api/v2/factories (the live ones, for any signed-in person),
POST /api/v2/factories, PATCH /api/v2/factories/{id} and POST /api/v2/factories/{id}/retire.
"""

from typing import TYPE_CHECKING, Any

from fastapi import Request
from pydantic import Field

from . import access as Access
from . import hubdb
from .models import Contract, Empty
from .store import Problem

# hubdb is untyped: its functions are Any here (as in stranded_resume.py), not partially unknown.
H: Any = hubdb

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

NAME_MAX = 60
GOAL_MAX = 200
COLUMNS = "id, name, goal, owner_id, manager_id, position, created_at, updated_at, retired_at"


class FactoryBody(Contract):
    """A new factory. The owner is a person and the manager a bot; both may be left out."""

    name: str = Field(min_length=1, max_length=NAME_MAX)
    goal: str | None = Field(default=None, max_length=GOAL_MAX)
    owner_id: str | None = Field(default=None, max_length=128)
    manager_id: str | None = Field(default=None, max_length=128)


class FactoryPatch(Contract):
    """A factory's changes. A field left out stays as it is; for goal, owner_id and manager_id an empty value or null
    clears it, and the name can only be changed."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    goal: str | None = Field(default=None, max_length=GOAL_MAX)
    owner_id: str | None = Field(default=None, max_length=128)
    manager_id: str | None = Field(default=None, max_length=128)


def _admin(conn: "sqlite3.Connection", actor: str) -> bool:
    """The company's owner and Admins, as pipeline_rights finds them."""
    if not actor.startswith("human:"):
        return False
    found = conn.execute("SELECT email FROM humans WHERE id=?", (actor.split(":", 1)[1],)).fetchone()
    email = str((found[0] if found else "") or "").strip().lower()
    return bool(email) and email in set(Access.owner_and_admin_emails(conn))


def _live(conn: "sqlite3.Connection", factory_id: str) -> dict[str, Any]:
    """A live factory's row, or a 404 (a retired one is not found)."""
    row = conn.execute(f"SELECT {COLUMNS} FROM factories WHERE id=? AND retired_at IS NULL", (factory_id,)).fetchone()
    if row is None:
        raise Problem("not_found", "No such factory", 404)
    return dict(row)


def _editable(conn: "sqlite3.Connection", actor: str, factory: dict[str, Any]) -> None:
    """The factory's owner and admins may edit or retire it."""
    if factory["owner_id"] and actor == factory["owner_id"]:
        return
    if not _admin(conn, actor):
        raise Problem("forbidden", "Only the factory's owner and Admins change it", 403)


def _name(value: object) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= NAME_MAX:
        raise Problem("validation", f"A factory's name is 1 to {NAME_MAX} characters", 422)
    return text


def _name_free(conn: "sqlite3.Connection", name: str, except_id: str = "") -> None:
    taken = conn.execute(
        "SELECT 1 FROM factories WHERE name=? COLLATE NOCASE AND retired_at IS NULL AND id<>?", (name, except_id)
    ).fetchone()
    if taken:
        raise Problem("conflict", f"A factory named {name} already exists", 409)


def _person(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("human:") or len(text) <= len("human:"):
        raise Problem("validation", "A factory's owner is a person", 422)
    return text


def _bot(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("bot:") or len(text) <= len("bot:"):
        raise Problem("validation", "A factory's manager is a bot", 422)
    return text


def _view(conn: "sqlite3.Connection", factory_id: str) -> dict[str, Any]:
    row = conn.execute(f"SELECT {COLUMNS} FROM factories WHERE id=?", (factory_id,)).fetchone()
    return dict(row)


def listed(conn: "sqlite3.Connection") -> list[dict[str, Any]]:
    """The live factories in their order, for the pipeline editor's Factory choice and the map."""
    rows = conn.execute(
        "SELECT id, name, goal, owner_id, manager_id, position FROM factories WHERE retired_at IS NULL "
        "ORDER BY coalesce(position, 1000000), created_at, id"
    ).fetchall()
    return [dict(r) for r in rows]


def create(conn: "sqlite3.Connection", actor: str, body: dict[str, Any]) -> dict[str, Any]:
    """A new factory at the end of the list. Only the company's owner and Admins make one."""
    if not _admin(conn, actor):
        raise Problem("forbidden", "Only the company's owner and Admins make a factory", 403)
    name = _name(body.get("name"))
    _name_free(conn, name)
    stamp = H.now()
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM factories WHERE retired_at IS NULL"
    ).fetchone()[0]
    ident = str(H.new_id())
    conn.execute(
        "INSERT INTO factories (id, name, goal, owner_id, manager_id, position, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            ident,
            name,
            (body.get("goal") or "").strip() or None,
            _person(body.get("owner_id")),
            _bot(body.get("manager_id")),
            int(position),
            stamp,
            stamp,
        ),
    )
    H.event(conn, actor, "factory.create", ident, {"name": name})
    return _view(conn, ident)


def update(conn: "sqlite3.Connection", actor: str, factory_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Change the fields the body sent. The factory's owner or an admin may; a refused call changes nothing."""
    current = _live(conn, factory_id)
    _editable(conn, actor, current)
    sets: dict[str, Any] = {}
    if "name" in body:
        if body["name"] is None:
            raise Problem("validation", "A factory has a name", 422)
        name = _name(body["name"])
        if name != current["name"]:
            _name_free(conn, name, factory_id)
        sets["name"] = name
    if "goal" in body:
        sets["goal"] = (body["goal"] or "").strip() or None
    if "owner_id" in body:
        sets["owner_id"] = _person(body["owner_id"])
    if "manager_id" in body:
        sets["manager_id"] = _bot(body["manager_id"])
    if not sets or all(current[k] == v for k, v in sets.items()):
        return current
    columns = ", ".join(f"{column}=?" for column in sets)
    now = H.now()
    conn.execute(f"UPDATE factories SET {columns}, updated_at=? WHERE id=?", (*sets.values(), now, factory_id))
    H.event(conn, actor, "factory.edit", factory_id, {k: v for k, v in sets.items()})
    return _view(conn, factory_id)


def retire(conn: "sqlite3.Connection", actor: str, factory_id: str) -> dict[str, Any]:
    """Retire a factory: it stops counting, and every pipeline in it is left outside every factory. Returns the factory
    with `detached`, how many pipelines left it."""
    current = _live(conn, factory_id)
    _editable(conn, actor, current)
    now = H.now()
    detached = conn.execute("UPDATE task_types SET factory_id=NULL, updated_at=? WHERE factory_id=?", (now, factory_id))
    conn.execute("UPDATE factories SET retired_at=?, updated_at=? WHERE id=?", (now, now, factory_id))
    H.event(conn, actor, "factory.retire", factory_id, {"detached": detached.rowcount})
    return {**_view(conn, factory_id), "detached": detached.rowcount}


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The factory routes. Reads need a signed-in person; every write checks the caller in create, update and retire."""
    db: Any = store  # Store is untyped, so its connections are Any here, not partially unknown
    guard: Any = auth

    @app.get("/api/v2/factories")
    def factory_list(request: Request) -> dict[str, Any]:
        """The live factories, in order."""
        guard.domain(request.state.identity)
        with db.read() as c:
            return {"factories": listed(c)}

    @app.post("/api/v2/factories")
    def factory_create(request: Request, body: FactoryBody) -> dict[str, Any]:
        """Make a factory. The company's owner and Admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return create(c, who.actor, body.model_dump())

        return mutate(request, body, write)

    @app.patch("/api/v2/factories/{factory_id}")
    def factory_update(request: Request, factory_id: str, body: FactoryPatch) -> dict[str, Any]:
        """Change a factory's name, goal, owner or manager. Its owner and admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return update(c, who.actor, factory_id, body.model_dump(exclude_unset=True))

        return mutate(request, body, write)

    @app.post("/api/v2/factories/{factory_id}/retire")
    def factory_retire(request: Request, factory_id: str, body: Empty) -> dict[str, Any]:
        """Retire a factory and take its pipelines out of it. Its owner and admins only."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return retire(c, who.actor, factory_id)

        return mutate(request, body, write)
