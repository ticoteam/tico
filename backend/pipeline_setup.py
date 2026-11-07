# pyright: strict
"""The Tico Team setup: the factories, pipelines, stages, links and goals a company starts from. `apply_factory_setup` is
idempotent: a second run finds everything it made and changes nothing. A pipeline that exists is adopted, never
duplicated: the Dev tickets type (role engineering) becomes Engineering and the Product roadmap type (role
product-roadmap) becomes Product, each keeping its own stages. Open cards are closed, and a role-less pipeline gets the
preset's stages, only with `replace=True`. A link whose stage does not exist is refused with replace, else reported.

The route is POST /api/v2/pipelines/setup, for the company's owner and Admins."""

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from . import hubdb

H: Any = hubdb

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

Row = dict[str, Any]
CLOSING = ("closed", "declined")
OPEN_STATUSES = "('open','doing','waiting','review','ready')"


def _stages(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"name": name, "status": status} for name, status in pairs]


PRESETS: dict[str, dict[str, Any]] = {
    "tico_default": {
        "factories": [
            {"name": "Product development", "goal": "Turn what customers need into shipped, working software"},
            {"name": "Growth", "goal": "Find leads and turn them into customers"},
            {"name": "Success", "goal": "Customers reach their goals and refer others"},
        ],
        "pipelines": [
            {
                "name": "Product",
                "role": "product-roadmap",
                "aliases": ["Product roadmap"],
                "factory": "Product development",
                "prefix": "P",
                "goal": "Discerning, high-quality specs for engineering",
                "stages": _stages(
                    ("Ideas", "open"),
                    ("Specifying", "doing"),
                    ("Review", "review"),
                    ("Approved", "done"),
                    ("Declined", "closed"),
                ),
                "manager": ["Product Manager", "Product Factory Manager"],
            },
            {
                "name": "Engineering",
                "role": "engineering",
                "aliases": ["Dev tickets"],
                "factory": "Product development",
                "prefix": "D",
                "goal": "Implement specs safely and efficiently; into production and validated",
                "stages": _stages(
                    ("Queued", "open"),
                    ("Building", "doing"),
                    ("Review", "review"),
                    ("Shipped", "done"),
                    ("Closed", "closed"),
                ),
                "manager": ["Engineering Manager", "Engineering Factory Manager"],
            },
            {
                "name": "Marketing",
                "factory": "Growth",
                "prefix": "M",
                "goal": "Find leads and bring them to Sales",
                "stages": _stages(
                    ("Targets", "open"), ("Outreach", "doing"), ("Replies", "review"), ("Qualified", "done")
                ),
                "manager": ["Marketing Manager", "Marketing Factory Manager"],
            },
            {
                "name": "Sales",
                "factory": "Growth",
                "prefix": "S",
                "goal": "Turn leads into customers",
                "stages": _stages(
                    ("Lead", "open"),
                    ("Talking", "doing"),
                    ("Proposal", "doing"),
                    ("Closing", "review"),
                    ("Won", "done"),
                    ("Lost", "closed"),
                ),
                "manager": ["Sales Manager", "Sales Factory Manager"],
            },
            {
                "name": "Success",
                "factory": "Success",
                "prefix": "SU",
                "goal": "Customers reach their goals; referrals are the main result",
                "stages": _stages(
                    ("Onboarding", "open"),
                    ("Active", "doing"),
                    ("Referral asked", "review"),
                    ("Referred", "done"),
                    ("Closed", "closed"),
                ),
                "manager": ["Success Manager", "Success Factory Manager"],
            },
            {
                "name": "Legal",
                "factory": None,
                "prefix": "L",
                "private": True,
                "goal": "Contracts and compliance questions answered before they hold up the business",
                "stages": _stages(
                    ("Intake", "open"),
                    ("Review", "doing"),
                    ("Counsel check", "review"),
                    ("Done", "done"),
                    ("Closed", "closed"),
                ),
                "manager": ["Legal Manager", "Counsel"],
            },
        ],
        "links": [
            {"from": "Product", "stage": "Approved", "to": "Engineering", "mode": "spawn"},
            {"from": "Marketing", "stage": "Qualified", "to": "Sales", "mode": "move"},
            {"from": "Sales", "stage": "Won", "to": "Success", "mode": "move"},
        ],
    }
}


def _rows(conn: sqlite3.Connection, sql: str, args: "tuple[Any, ...]" = ()) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [str(d[0]) for d in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


def _one(conn: sqlite3.Connection, sql: str, args: "tuple[Any, ...]" = ()) -> Row | None:
    found = _rows(conn, sql, args)
    return found[0] if found else None


def _factory(conn: sqlite3.Connection, actor: str, spec: Row, position: int, made: list[str]) -> str:
    found = _one(
        conn, "SELECT id, goal FROM factories WHERE name = ? COLLATE NOCASE AND retired_at IS NULL", (spec["name"],)
    )
    stamp = H.now()
    if found is not None:
        if not found["goal"]:
            conn.execute(
                "UPDATE factories SET goal = ?, updated_at = ? WHERE id = ?", (spec["goal"], stamp, found["id"])
            )
        return str(found["id"])
    ident = cast(str, H.new_id())
    conn.execute(
        "INSERT INTO factories (id, name, owner_id, position, created_at, updated_at, goal) VALUES (?,?,?,?,?,?,?)",
        (ident, spec["name"], actor, position, stamp, stamp, spec["goal"]),
    )
    made.append(f"factory {spec['name']}")
    return ident


def _existing(conn: sqlite3.Connection, spec: Row) -> Row | None:
    """The type this pipeline already is: the one holding its role, else one with its name or an alias."""
    if spec.get("role"):
        found = _one(conn, "SELECT * FROM task_types WHERE role = ?", (spec["role"],))
        if found is not None:
            return found
    for name in (spec["name"], *spec.get("aliases", [])):
        found = _one(conn, "SELECT * FROM task_types WHERE name = ? COLLATE NOCASE ORDER BY role IS NULL, id", (name,))
        if found is not None:
            return found
    return None


def _manager(conn: sqlite3.Connection, names: list[str]) -> str | None:
    """The slug of the active bot named like one of `names` (a display name or a slug), or None."""
    for name in names:
        found = _one(
            conn,
            "SELECT slug FROM bots WHERE (display_name = ? COLLATE NOCASE OR slug = ? COLLATE NOCASE) "
            "AND COALESCE(state, 'active') = 'active' ORDER BY slug LIMIT 1",
            (name, name.lower().replace(" ", "-")),
        )
        if found is not None:
            return str(found["slug"])
    return None


def _restage(conn: sqlite3.Connection, actor: str, row: Row, stages: list[dict[str, str]], made: list[str]) -> None:
    """replace=True on a type with no role: its open cards are closed, and its stages become the preset's, in its order. A
    "Closed" stage is added last when the preset has no stage of that name, to hold the closed cards. The cards move to
    that holding stage before any other stage is removed, so no removed stage is in use. Writes nothing when the stages
    already match. Never done to a type that holds a role (Engineering, Product): its stages carry rules and flows."""
    want = [dict(s) for s in stages]
    if not any(str(s["name"]).lower() == "closed" for s in want):
        want.append({"name": "Closed", "status": "closed"})

    def shape(steps: list[Row]) -> list[tuple[str, str]]:
        return [(str(s["name"]).lower(), str(s["status"])) for s in steps]

    current = _rows(
        conn, "SELECT id, name, status FROM task_steps WHERE type_id = ? ORDER BY position, id", (row["id"],)
    )
    if shape(current) == shape(want):
        return
    open_cards = _rows(conn, f"SELECT id FROM tasks WHERE type_id = ? AND status IN {OPEN_STATUSES}", (row["id"],))
    for task in open_cards:
        H.task_close(conn, actor, task["id"], note="Closed by pipeline setup (replace)")
    if open_cards:
        made.append(f"cleared {row['name']}")
    cards = _one(conn, "SELECT id FROM tasks WHERE type_id = ? LIMIT 1", (row["id"],)) is not None
    if cards and not any(str(s["name"]).lower() == "closed" for s in current):
        keep = [{"id": s["id"], "name": s["name"], "status": s["status"]} for s in current]
        H.type_update(conn, actor, row["id"], steps=[*keep, {"name": "Closed", "status": "closed"}], mover=True)
        current = _rows(
            conn, "SELECT id, name, status FROM task_steps WHERE type_id = ? ORDER BY position, id", (row["id"],)
        )
    by_name = {str(s["name"]).lower(): s for s in current}
    if cards:
        conn.execute("UPDATE tasks SET step_id = ? WHERE type_id = ?", (by_name["closed"]["id"], row["id"]))
    final: list[dict[str, Any]] = []
    for s in want:
        old = by_name.get(str(s["name"]).lower())
        final.append({"id": old["id"], "name": s["name"], "status": s["status"]} if old else dict(s))
    H.type_update(conn, actor, row["id"], steps=final, mover=True)
    made.append(f"set up the stages of {row['name']}")


def _pipeline(
    conn: sqlite3.Connection, actor: str, spec: Row, factories: dict[str, str], replace: bool, made: list[str]
) -> str:
    existing = _existing(conn, spec)
    if existing is None:
        row = H.type_create(conn, actor, spec["name"], spec["stages"], mover=True, numbered=True)
        made.append(f"pipeline {spec['name']}")
    else:
        row = existing
        if replace and not existing.get("role"):
            _restage(conn, actor, existing, spec["stages"], made)
        if str(row["name"]).lower() != str(spec["name"]).lower():
            H.type_update(conn, actor, row["id"], name=spec["name"], mover=True)
            made.append(f"renamed {row['name']} to {spec['name']}")
        if not row.get("numbered"):
            H.type_update(conn, actor, row["id"], numbered=True, mover=True)
    ident = str(row["id"])
    current = (
        _one(
            conn,
            "SELECT goal, prefix, factory_id, manager, pipeline_owner_id, pipeline_visibility FROM task_types WHERE id = ?",
            (ident,),
        )
        or {}
    )
    updates: dict[str, Any] = {}
    if not current.get("goal"):
        updates["goal"] = spec["goal"]
    if (
        not current.get("prefix")
        and _one(conn, "SELECT id FROM task_types WHERE prefix = ? AND id <> ?", (spec["prefix"], ident)) is None
    ):
        updates["prefix"] = spec["prefix"]
    if spec.get("factory") and not current.get("factory_id"):
        updates["factory_id"] = factories[spec["factory"]]
    if not current.get("pipeline_owner_id"):
        updates["pipeline_owner_id"] = actor
    if not current.get("manager"):
        manager = _manager(conn, spec["manager"])
        if manager:
            updates["manager"] = manager
    if spec.get("private"):
        updates["pipeline_visibility"] = "members"
    changed = {k: v for k, v in updates.items() if current.get(k) != v}
    if changed:
        sets = ", ".join(f"{k} = ?" for k in changed)
        conn.execute(f"UPDATE task_types SET {sets}, updated_at = ? WHERE id = ?", (*changed.values(), H.now(), ident))
        if existing is not None and set(changed) - {"pipeline_visibility"}:
            made.append(f"set up {spec['name']}")
        elif existing is not None:
            made.append(f"made {spec['name']} private")
    return ident


def _link(conn: sqlite3.Connection, actor: str, spec: Row, pipelines: dict[str, str], made: list[str]) -> str | None:
    """Makes one of the preset's links (once). Returns the link's name when its stage does not exist, so nothing is made."""
    from . import pipeline_links

    stage = _one(
        conn,
        "SELECT id FROM task_steps WHERE type_id = ? AND name = ? COLLATE NOCASE",
        (pipelines[spec["from"]], spec["stage"]),
    )
    if stage is None:
        return f"{spec['from']} {spec['stage']} to {spec['to']}"
    if pipeline_links.link_for_stage(conn, str(stage["id"])) is not None:
        return None
    pipeline_links.create_link(conn, actor, str(stage["id"]), pipelines[spec["to"]], spec["mode"])
    made.append(f"link {spec['from']} {spec['stage']} to {spec['to']}")
    return None


def apply_factory_setup(
    c: sqlite3.Connection, actor: str, preset: str = "tico_default", replace: bool = False
) -> dict[str, Any]:
    """Make the preset's factories, pipelines, stages, links and goals. Idempotent: what exists is adopted (and given a
    goal, prefix, factory or manager only where it has none), never duplicated or overwritten. A manager bot is set when
    one named like "<Name> Manager" exists, else left unset. `replace=True` also closes the open cards of an existing,
    role-less pipeline and makes its stages the preset's. A link whose stage cannot be made is refused (422, nothing is
    written) with replace; without it the link is left out and named under `unresolved`.

    {"preset", "factories": {name: id}, "pipelines": {name: id}, "changes": [what was made or changed],
    "unresolved": [links left out]}."""
    from .store import Problem

    if preset not in PRESETS:
        H.refuse(c, actor, "kind", f"Unknown preset {preset}; the choices are {', '.join(PRESETS)}")
    spec = PRESETS[preset]
    made: list[str] = []
    unresolved: list[str] = []
    c.execute("SAVEPOINT pipeline_setup")
    try:
        factories = {f["name"]: _factory(c, actor, f, i, made) for i, f in enumerate(spec["factories"])}
        pipelines = {p["name"]: _pipeline(c, actor, p, factories, replace, made) for p in spec["pipelines"]}
        for link in spec["links"]:
            missing = _link(c, actor, link, pipelines, made)
            if missing:
                unresolved.append(missing)
        if unresolved and replace:
            raise Problem(
                "unresolved",
                "These links name a stage the setup does not make: " + "; ".join(unresolved),
                422,
            )
    except BaseException:
        c.execute("ROLLBACK TO pipeline_setup")
        c.execute("RELEASE pipeline_setup")
        raise
    c.execute("RELEASE pipeline_setup")
    if made:
        H.event(c, actor, "pipeline.setup", preset, {"changes": made, "replace": replace})
    return {
        "preset": preset,
        "factories": factories,
        "pipelines": pipelines,
        "changes": made,
        "unresolved": unresolved,
    }


class PipelineSetupBody(BaseModel):
    preset: str = "tico_default"
    replace: bool = False


def install(app: "FastAPI", store: "Store", mutate: Callable[..., Any], auth: "Auth") -> None:
    """POST /api/v2/pipelines/setup {preset, replace}: the company's owner and Admins only."""
    from fastapi import Request

    from .store import Problem

    @app.post("/api/v2/pipelines/setup")  # pyright: ignore[reportUntypedFunctionDecorator]
    def setup(request: Request, body: PipelineSetupBody) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity
        guard: Any = auth
        if guard.company_role(who) not in ("owner", "admin"):
            raise Problem("forbidden", "Only the company's owner and Admins set up the pipelines", 403)

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            return apply_factory_setup(c, who.actor, body.preset, body.replace)

        return cast("dict[str, Any]", mutate(request, body, work))
