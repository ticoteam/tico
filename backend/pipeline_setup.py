# pyright: strict
"""The Tico Team setup: the factories, pipelines, stages, links and goals a company starts from. `apply_factory_setup` is
idempotent: a second run finds everything it made and changes nothing. A pipeline that exists is adopted, never
duplicated: the Dev tickets type (role engineering) becomes Engineering and the Product roadmap type (role
product-roadmap) becomes Product, each keeping its own stages. Open cards are closed, and a role-less pipeline gets the
preset's stages, only with `replace=True`. A link whose stage does not exist is refused with replace, else reported.

The route is POST /api/v2/pipelines/setup, for the company's owner and Admins."""

import json
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
OPEN_STATUSES = ("open", "doing", "waiting", "review", "ready")


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
                    ("In development", "doing"),
                    ("Validate", "review"),
                    ("Done", "done"),
                    ("Declined", "closed"),
                ),
                # Every hub boots with the roadmap seed (roadmap_flow.ensure). Its stages take the preset's names in place,
                # so their cards stay; Approved is retired into In development and Ready to ship into Done, and each goes
                # once it is empty.
                "adopt": {
                    "renamed": {
                        "Suggested": "Ideas",
                        "Exploring": "Specifying",
                        "Spec": "Review",
                        "Building": "In development",
                        "Shipped": "Done",
                    },
                    "retired": {"Approved": "In development", "Ready to ship": "Done"},
                },
                "requirements": [
                    {
                        "stage": "Specifying",
                        "name": "Spec page",
                        "kind": "attachment",
                        "needs": {"formats": ["page"]},
                        "reviewed_by": "bot_then_person",
                        "rubric": "The page says what we're building, how we'll know it worked, and how it gets validated.",
                    },
                    {"stage": "Review", "name": "Spec approved", "kind": "approval", "reviewed_by": "person"},
                    {
                        "stage": "In development",
                        "name": "Engineering shipped",
                        "kind": "run",
                        "needs": {"source": "subtasks"},
                        "into": "Engineering",  # the pipeline whose subtasks count (its id is written into needs)
                        "reviewed_by": "nobody",
                    },
                    {
                        "stage": "Validate",
                        "name": "Validated",
                        "kind": "attachment",
                        "needs": {"formats": ["page", "link"]},
                        "reviewed_by": "bot_then_person",
                        "rubric": "The page says whether the spec's 'how we'll know' measures held, each with its number.",
                    },
                ],
                "manager": ["Product Pipeline Manager", "Product Manager", "Product Factory Manager"],
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
                "manager": ["Engineering Pipeline Manager", "Engineering Manager", "Engineering Factory Manager"],
            },
            {
                "name": "Marketing",
                "factory": "Growth",
                "prefix": "M",
                "goal": "Find leads and bring them to Sales",
                "stages": _stages(
                    ("Targets", "open"), ("Outreach", "doing"), ("Replies", "review"), ("Qualified", "done")
                ),
                "manager": ["Marketing Pipeline Manager", "Marketing Manager", "Marketing Factory Manager"],
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
                "manager": ["Sales Pipeline Manager", "Sales Manager", "Sales Factory Manager"],
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
                "manager": ["Success Pipeline Manager", "Success Manager", "Success Factory Manager"],
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
                "manager": ["Legal Pipeline Manager", "Legal Manager", "Counsel"],
            },
        ],
        "links": [
            {"from": "Product", "stage": "Review", "to": "Engineering", "mode": "spawn"},
        ],
    },
    "company_default": {
        "factories": [
            {"name": "Product", "goal": "Turn what customers need into shipped, working software"},
            {"name": "Growth", "goal": "Find leads and turn them into customers"},
            {"name": "Success", "goal": "Customers reach their goals and refer others"},
        ],
        "pipelines": [
            {
                "name": "Product",
                "role": "product-roadmap",
                "aliases": ["Product roadmap"],
                "factory": "Product",
                "prefix": "P",
                "goal": "Discerning, high-quality specs for engineering",
                "stages": _stages(
                    ("On ice", "open"),
                    ("Ideas", "open"),
                    ("Specifying", "doing"),
                    ("Review", "review"),
                    ("In development", "doing"),
                    ("Validate", "review"),
                    ("Done", "done"),
                    ("Declined", "closed"),
                ),
                # The boot seed (roadmap_flow.ensure) takes these names in place; Approved goes into In development.
                "adopt": {
                    "renamed": {
                        "Suggested": "Ideas",
                        "Exploring": "Specifying",
                        "Spec": "Review",
                        "Building": "In development",
                        "Shipped": "Done",
                    },
                    "retired": {"Approved": "In development"},
                },
                "restage_empty_roles": True,
                "requirements": [
                    {
                        "stage": "Specifying",
                        "name": "Spec page",
                        "kind": "attachment",
                        "needs": {"formats": ["page"]},
                        "reviewed_by": "bot_then_person",
                        "rubric": "The page says what we're building, how we'll know it worked, and how it gets validated.",
                    },
                    {"stage": "Review", "name": "Spec approved", "kind": "approval", "reviewed_by": "person"},
                    {
                        "stage": "In development",
                        "name": "Engineering shipped",
                        "kind": "run",
                        "needs": {"source": "subtasks"},
                        "into": "Engineering",
                        "reviewed_by": "nobody",
                    },
                    {
                        "stage": "Validate",
                        "name": "Validated",
                        "kind": "attachment",
                        "needs": {"formats": ["page", "link"]},
                        "reviewed_by": "bot_then_person",
                        "rubric": "The page says whether the spec's 'how we'll know' measures held, each with its number.",
                    },
                ],
                # New companies skip the name match: a manager is bound by seat or bot (pipeline_setup bindings).
                "manager": [],
            },
            {
                "name": "Engineering",
                "role": "engineering",
                "aliases": ["Dev tickets"],
                "factory": "Product",
                "prefix": "D",
                "goal": "Implement specs safely and efficiently; into production and validated",
                # #2963's recommended no-QA board, so the review checks attach by stage name.
                "stages": _stages(
                    ("On Deck", "open"),
                    ("In Progress", "doing"),
                    ("Review 1", "review"),
                    ("Review 1 Fixes", "doing"),
                    ("Review 2", "review"),
                    ("Review 2 Fixes", "doing"),
                    ("Pending Release", "review"),
                    ("Done", "done"),
                    ("Closed", "closed"),
                ),
                "adopt": {
                    "renamed": {
                        "Todo": "On Deck",
                        "Doing": "In Progress",
                        "In review": "Review 1",
                        "Ready to ship": "Pending Release",
                    }
                },
                "restage_empty_roles": True,
                "manager": [],
            },
            {
                "name": "Growth Experiments",
                "factory": "Growth",
                "prefix": "GX",
                "goal": "Test growth ideas, keep what works, drop the rest",
                "stages": _stages(
                    ("Ideas", "open"),
                    ("Running", "doing"),
                    ("Measuring", "review"),
                    ("Kept", "done"),
                    ("Dropped", "closed"),
                ),
                "manager": [],
            },
            {
                "name": "Lead",
                "factory": "Growth",
                "prefix": "L",
                "goal": "Find leads and bring the qualified ones to Opportunity",
                "stages": _stages(
                    ("Targets", "open"),
                    ("Outreach", "doing"),
                    ("Replies", "review"),
                    ("Qualified", "review"),
                    ("Closed", "closed"),
                ),
                "manager": [],
            },
            {
                "name": "Opportunity",
                "factory": "Growth",
                "prefix": "O",
                "goal": "Turn leads into customers",
                "stages": _stages(
                    ("Lead", "open"),
                    ("Talking", "doing"),
                    ("Proposal", "doing"),
                    ("Closing", "review"),
                    ("Won", "done"),
                    ("Lost", "closed"),
                ),
                "manager": [],
            },
            {
                "name": "Customer",
                "factory": "Success",
                "prefix": "C",
                "goal": "Customers reach their goals; referrals are the main result",
                "stages": _stages(
                    ("Onboarding", "open"),
                    ("Active", "doing"),
                    ("Referral asked", "review"),
                    ("Referred", "done"),
                    ("Closed", "closed"),
                ),
                "manager": [],
            },
            {
                "name": "Success Experiments",
                "factory": "Success",
                "prefix": "SX",
                "goal": "Test customer success playbooks, keep what works, drop the rest",
                "stages": _stages(
                    ("Ideas", "open"),
                    ("Running", "doing"),
                    ("Measuring", "review"),
                    ("Kept", "done"),
                    ("Dropped", "closed"),
                ),
                "manager": [],
            },
            {
                "name": "Legal",
                "factory": None,
                "prefix": "LG",
                "private": True,
                "goal": "Contracts and compliance questions answered before they hold up the business",
                "stages": _stages(
                    ("Intake", "open"),
                    ("Review", "doing"),
                    ("Rules check", "review"),
                    ("Done", "done"),
                    ("Closed", "closed"),
                ),
                "manager": [],
            },
            {
                "name": "Finance",
                "factory": None,
                "prefix": "F",
                "private": True,
                "goal": "Money in, money out, and the books that show both",
                "stages": _stages(
                    ("Intake", "open"),
                    ("Doing", "doing"),
                    ("Review", "review"),
                    ("Done", "done"),
                    ("Closed", "closed"),
                ),
                "manager": [],
            },
        ],
        "links": [
            {"from": "Product", "stage": "Review", "to": "Engineering", "mode": "spawn"},
            # A Lead card entering Qualified files one Opportunity (once per card; pipeline_engine._enter_spawn_stage).
            {"from": "Lead", "stage": "Qualified", "to": "Opportunity", "mode": "spawn", "fires": "enter"},
        ],
    },
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
    """The slug of the active bot named like one of `names` (a display name or a slug), or None. The names are tried in
    order: "<X> Pipeline Manager" first, then the older "<X> Manager" and "<X> Factory Manager"."""
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


def _active_manager(conn: sqlite3.Connection, slug: object) -> bool:
    """Whether a pipeline's manager is a bot that works: an archived, paused, quarantined or planned bot, or one that
    does not exist, counts as none, so setup may replace it."""
    if not slug:
        return False
    found = H.resolve_bot(conn, str(slug))
    return found is not None and str(found.get("state") or "active") == "active"


def _pipeline_stages(spec: Row) -> list[dict[str, str]]:
    """The one stage list a pipeline is made with and replaced to. A pipeline without a role gets a "Closed" stage last
    when its preset names none, to hold closed cards. A role's stages are the preset's as written: their flows own them."""
    want = [dict(s) for s in spec["stages"]]
    if not spec.get("role") and not any(str(s["name"]).lower() == "closed" for s in want):
        want.append({"name": "Closed", "status": "closed"})
    return want


def _restage(conn: sqlite3.Connection, actor: str, row: Row, stages: list[dict[str, str]], made: list[str]) -> None:
    """replace=True on a type with no role: its stages become `stages` (from _pipeline_stages, the list it was made with),
    in that order. A stage the list keeps (same name) keeps its id, and its cards stay where they are with their status.
    Only cards on a removed stage move: an open one to the first stage, a done or closed one to Closed, its status kept.
    Each move is a step event. Writes nothing when the stages already match. A role type is restaged only when it is
    empty (the preset's restage_empty_roles), so no card moves off a board a company is using."""
    from . import pipeline_engine

    want = [dict(s) for s in stages]

    def shape(steps: list[Row]) -> list[tuple[str, str]]:
        return [(str(s["name"]).lower(), str(s["status"])) for s in steps]

    def read() -> list[Row]:
        return _rows(
            conn, "SELECT id, name, status FROM task_steps WHERE type_id = ? ORDER BY position, id", (row["id"],)
        )

    current = read()
    if shape(current) == shape(want):
        return
    have = {str(s["name"]).lower() for s in current}
    missing = [s for s in want if str(s["name"]).lower() not in have]
    if missing:  # the new stages exist first, so cards have somewhere to go before a removed stage is dropped
        keep = [{"id": s["id"], "name": s["name"], "status": s["status"]} for s in current]
        H.type_update(conn, actor, row["id"], steps=[*keep, *missing], mover=True)
        current = read()
    by_name = {str(s["name"]).lower(): s for s in current}
    kept = {str(s["name"]).lower() for s in want}
    moved = 0
    for step in [s for s in current if str(s["name"]).lower() not in kept]:
        for card in _rows(
            conn, "SELECT id, status, type_id, step_id, created_at FROM tasks WHERE step_id = ?", (step["id"],)
        ):
            dest = by_name[str(want[0]["name"]).lower()] if card["status"] in OPEN_STATUSES else by_name["closed"]
            ts = H.now()
            conn.execute("UPDATE tasks SET step_id = ?, updated_at = ? WHERE id = ?", (dest["id"], ts, card["id"]))
            H._task_event(  # pyright: ignore[reportPrivateUsage]
                conn, card["id"], actor, "step", step["id"], dest["id"], "Moved by pipeline setup (replace)"
            )
            # The status stays as it was (so no task_update), but the stage visit follows the card: a visit left open on
            # a stage this replace removes would keep its Late clock and review items on a stage that is gone. Only the
            # visit moves: no step hook runs, so a relocation never spawns linked work or settles a parent.
            visit = pipeline_engine.open_visit(conn, card["id"])
            if visit is not None:
                conn.execute("UPDATE stage_visits SET left_at = ? WHERE id = ?", (ts, visit["id"]))
                pipeline_engine._open_visit_at(  # pyright: ignore[reportPrivateUsage]
                    conn, card["id"], row["id"], dest["id"], ts, direction="initial", came_from=step["id"], actor=actor
                )
            moved += 1
    if moved:
        made.append(f"moved {moved} cards off removed stages of {row['name']}")
    final: list[dict[str, Any]] = []
    for s in want:
        old = by_name.get(str(s["name"]).lower())
        final.append({"id": old["id"], "name": s["name"], "status": s["status"]} if old else dict(s))
    H.type_update(conn, actor, row["id"], steps=final, mover=True)
    made.append(f"set up the stages of {row['name']}")


def _has_open_subtask(conn: sqlite3.Connection, task_id: str) -> bool:
    """Whether a card has an open task below it (any depth). Finishing the card would close it."""
    return any(str(kid["status"]) in H.ACTIVE_STATUSES for kid in H.descendants(conn, task_id))


def _adopt(conn: sqlite3.Connection, actor: str, row: Row, spec: Row, made: list[str], unresolved: list[str]) -> None:
    """An existing pipeline takes the preset's stage names. A renamed stage keeps its id, so its cards stay. A retired
    stage's links and requirements retire, its cards move to the stage that replaces it, and it goes once it is empty
    (a stage the move refuses stays, and the refusal stands for the whole setup). A card with an open subtask stays where
    it is: moving it to a done or closed stage would close that subtask, and cleanup never cancels work. Such a stage stays
    until it is empty, and each card left is named under `unresolved`. Preset stages the pipeline lacks are added in the
    preset's order; stages the preset does not name are kept at the end."""
    from . import pipeline_links

    steps = _rows(conn, "SELECT id, name, status FROM task_steps WHERE type_id = ? ORDER BY position", (row["id"],))
    adopt: dict[str, Any] = spec.get("adopt") or {}
    renamed: dict[str, str] = adopt.get("renamed", {})
    retired: dict[str, str] = adopt.get("retired", {})
    stages: list[dict[str, str]] = spec["stages"]
    touched = False
    for old, new in renamed.items():
        has = [s for s in steps if str(s["name"]).lower() == old.lower()]
        if has and not any(str(s["name"]).lower() == new.lower() for s in steps):
            has[0]["name"] = new
            touched = True
            made.append(f"renamed {old} to {new}")
    ordered: list[Row] = []
    for stage in stages:
        found = next((s for s in steps if str(s["name"]).lower() == stage["name"].lower()), None)
        ordered.append(found if found is not None else {"name": stage["name"], "status": stage["status"]})
    placed = {str(s["id"]) for s in ordered if "id" in s}
    ordered += [s for s in steps if str(s["id"]) not in placed]
    if (
        touched
        or any("id" not in s for s in ordered)
        or [str(s["id"]) for s in ordered] != [str(s["id"]) for s in steps]
    ):
        H.type_update(conn, actor, row["id"], steps=ordered, mover=True)
        made.append(f"set up stages of {spec['name']}")
    for old, target in retired.items():
        gone = _one(conn, "SELECT id FROM task_steps WHERE type_id = ? AND name = ? COLLATE NOCASE", (row["id"], old))
        into = _one(
            conn,
            "SELECT id, status FROM task_steps WHERE type_id = ? AND name = ? COLLATE NOCASE",
            (row["id"], target),
        )
        if gone is None or into is None:
            continue
        link = pipeline_links.link_for_stage(conn, str(gone["id"]))
        if link is not None:
            pipeline_links.retire_link(conn, actor, str(link["id"]))
        cards = _rows(conn, "SELECT id, title FROM tasks WHERE step_id = ?", (gone["id"],))
        # Only a move into a done or closed stage closes open subtasks; any other move leaves them alone.
        closes = str(into["status"]) in ("done", "closed")
        held = [card for card in cards if closes and _has_open_subtask(conn, str(card["id"]))]
        for card in cards:
            if card in held:
                continue
            H.task_update(conn, actor, str(card["id"]), step=str(into["id"]), mover=True)
        if len(cards) > len(held):
            made.append(f"moved {len(cards) - len(held)} {old} cards to {target}")
        # A held card is named only to a caller who can read it; the rest are counted, with no title.
        hidden: list[Row] = []
        for card in held:
            if H.task_private_readable(conn, actor, H.task(conn, str(card["id"]))):
                unresolved.append(
                    f"{card['title']} is still in {old}: finish or close its open subtasks, then re-run setup"
                )
            else:
                hidden.append(card)
        if hidden:
            unresolved.append(
                f"{len(hidden)} card{'s' if len(hidden) > 1 else ''} you can't see "
                f"{'are' if len(hidden) > 1 else 'is'} still in {old}: "
                f"{'their owners' if len(hidden) > 1 else 'its owner'} finish or close the open subtasks"
            )
        conn.execute(
            "UPDATE stage_requirements SET retired_at = ? WHERE pipeline_stage_id = ? AND retired_at IS NULL",
            (H.now(), gone["id"]),
        )
        conn.execute("SAVEPOINT pipeline_adopt_drop")
        try:
            fresh = _rows(
                conn, "SELECT id, name, status FROM task_steps WHERE type_id = ? ORDER BY position", (row["id"],)
            )
            left = [s for s in fresh if str(s["id"]) != str(gone["id"])]
            H.type_update(conn, actor, row["id"], steps=left, mover=True)
        except H.Refused:
            conn.execute("ROLLBACK TO pipeline_adopt_drop")
            conn.execute("RELEASE pipeline_adopt_drop")
            made.append(f"kept {old} (it is still in use)")
            continue
        conn.execute("RELEASE pipeline_adopt_drop")
        made.append(f"removed {old}")


def _name_pipeline(conn: sqlite3.Connection, requirement_id: str, pipeline_id: str) -> bool:
    """Names the pipeline on a `subtasks` run setup made before runs named one, so the engine counts only that pipeline's
    subtasks (D64). It writes the current needs only: a new version would bump the version approvals were given at, and
    the history is append-only. A row that already names it is left alone, so a second run changes nothing. True when it
    wrote the name."""
    row = _one(conn, "SELECT needs FROM stage_requirements WHERE id = ?", (requirement_id,))
    if row is None:
        return False
    needs: Row = json.loads(row["needs"] or "{}")
    if needs.get("pipeline") == pipeline_id:
        return False
    needs["pipeline"] = pipeline_id
    conn.execute(
        "UPDATE stage_requirements SET needs = ?, updated_at = ? WHERE id = ?",
        (json.dumps(needs), H.now(), requirement_id),
    )
    return True


def _requirement(
    conn: sqlite3.Connection, actor: str, pipeline_id: str, spec: Row, made: list[str], pipelines: dict[str, str]
) -> None:
    """A preset requirement on its stage, once. A person signs off a spec, so only a person's setup names reviewers. A spec
    with `into` names the pipeline its `subtasks` run waits on (written into needs, also on an existing row)."""
    from . import pipeline_requirements

    if not actor.startswith("human:"):
        return
    stage = _one(
        conn, "SELECT id FROM task_steps WHERE type_id = ? AND name = ? COLLATE NOCASE", (pipeline_id, spec["stage"])
    )
    if stage is None:
        return
    target = pipelines.get(str(spec["into"])) if spec.get("into") else None
    existing = _one(
        conn,
        "SELECT id FROM stage_requirements WHERE pipeline_stage_id = ? AND name = ? AND retired_at IS NULL",
        (stage["id"], spec["name"]),
    )
    if existing is not None:
        if target and _name_pipeline(conn, str(existing["id"]), target):
            made.append(f"requirement {spec['name']} on {spec['stage']} now names its pipeline")
        return
    body = {
        "name": spec["name"],
        "kind": spec["kind"],
        "needs": {**spec.get("needs", {}), **({"pipeline": target} if target else {})},
        "reviewed_by": spec["reviewed_by"],
        "reviewer_id": actor if "person" in spec["reviewed_by"] else None,
        "rubric": spec.get("rubric"),
    }
    pipeline_requirements.add(conn, actor, str(stage["id"]), body)
    made.append(f"requirement {spec['name']} on {spec['stage']}")


def _public_names(conn: sqlite3.Connection, ident: str, name: str, made: list[str]) -> None:
    """Every stage's public name is its stage name (the Product roadmap's public page shows them). Only the stages that
    differ are written, so a second run changes nothing."""
    if _one(conn, "SELECT 1 FROM task_steps WHERE type_id = ? AND public_name <> name LIMIT 1", (ident,)) is None:
        return
    conn.execute("UPDATE task_steps SET public_name = name WHERE type_id = ? AND public_name <> name", (ident,))
    conn.execute("UPDATE task_types SET updated_at = ? WHERE id = ?", (H.now(), ident))
    made.append(f"public names of {name} follow its stages")


def _open_seat(conn: sqlite3.Connection, seat_id: object) -> bool:
    """Whether a seat exists and is still open (not filled, not closed)."""
    return (
        bool(seat_id)
        and _one(conn, "SELECT id FROM seats WHERE id = ? AND closed_at IS NULL", (str(seat_id),)) is not None
    )


def _has_cards(conn: sqlite3.Connection, type_id: str) -> bool:
    return _one(conn, "SELECT 1 FROM tasks WHERE type_id = ? LIMIT 1", (type_id,)) is not None


def _manager_updates(conn: sqlite3.Connection, spec: Row, current: Row, binding: Row, unresolved: list[str]) -> Row:
    """Who runs a pipeline. An active manager is never replaced, by a binding or by a name. Otherwise, in order: the bot
    the binding names (an active bot, by id or slug, written as its bare id), the open seat the binding names (its hire
    sets the manager; see seat_filled), a seat already set that is still open, the name match. A pipeline with none of
    those is named under unresolved."""
    if _active_manager(conn, current.get("manager")):
        return {}
    if binding.get("bot"):
        bot = H.resolve_bot(conn, str(binding["bot"]))
        if bot is not None and str(bot.get("state") or "active") == "active":
            return {"manager": str(bot["id"])}
    if binding.get("seat") and _open_seat(conn, binding["seat"]):
        return {"manager": None, "manager_seat": str(binding["seat"])}
    if _open_seat(conn, current.get("manager_seat")):
        return {}
    manager = _manager(conn, spec.get("manager") or [])
    if manager:
        return {"manager": manager}
    hint = (spec.get("manager") or [f"{spec['name']} Pipeline Manager"])[0]
    unresolved.append(f"{spec['name']} has no active manager: name a bot {hint}")
    return {}


def seat_filled(conn: sqlite3.Connection, actor: str, seat_id: str, bot: str) -> None:
    """A seat's bot is hired (lifecycle.fill_seat, recruiting.hire). Each pipeline bound to the seat whose manager is not
    an active bot takes the bot as its manager, and the change is logged. An active manager is never replaced."""
    found = H.resolve_bot(conn, bot)
    bot_id = str(found["id"]) if found is not None else bot
    for row in _rows(conn, "SELECT id, manager FROM task_types WHERE manager_seat = ?", (seat_id,)):
        if _active_manager(conn, row["manager"]):
            continue
        conn.execute("UPDATE task_types SET manager = ?, updated_at = ? WHERE id = ?", (bot_id, H.now(), row["id"]))
        H.event(conn, actor, "pipeline.manager_seated", str(row["id"]), {"seat": seat_id, "bot": bot_id})


def _pipeline(
    conn: sqlite3.Connection,
    actor: str,
    spec: Row,
    factories: dict[str, str],
    replace: bool,
    made: list[str],
    unresolved: list[str],
    binding: Row | None = None,
) -> str:
    existing = _existing(conn, spec)
    binding = binding or {}
    if existing is None:
        row = H.type_create(conn, actor, spec["name"], _pipeline_stages(spec), mover=True, numbered=True)
        made.append(f"pipeline {spec['name']}")
    else:
        row = existing
        if replace and not existing.get("role"):
            _restage(conn, actor, existing, _pipeline_stages(spec), made)
        if str(row["name"]).lower() != str(spec["name"]).lower():
            H.type_update(conn, actor, row["id"], name=spec["name"], mover=True)
            made.append(f"renamed {row['name']} to {spec['name']}")
        if not row.get("numbered"):
            H.type_update(conn, actor, row["id"], numbered=True, mover=True)
    if existing is not None and spec.get("adopt"):
        _adopt(conn, actor, row, spec, made, unresolved)
    # A role board nobody has used yet takes the preset's stages in full (its boot stages keep their ids by name).
    if existing is not None and spec.get("restage_empty_roles") and not _has_cards(conn, str(row["id"])):
        _restage(conn, actor, row, _pipeline_stages(spec), made)
    ident = str(row["id"])
    _public_names(conn, ident, spec["name"], made)
    current = (
        _one(
            conn,
            "SELECT goal, prefix, factory_id, manager, manager_seat, pipeline_owner_id, pipeline_visibility FROM task_types WHERE id = ?",
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
    updates.update(_manager_updates(conn, spec, current, binding, unresolved))
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
    pipeline_links.create_link(
        conn, actor, str(stage["id"]), pipelines[spec["to"]], spec["mode"], fires=str(spec.get("fires", "leave"))
    )
    made.append(f"link {spec['from']} {spec['stage']} to {spec['to']}")
    return None


def _retire_obsolete(
    conn: sqlite3.Connection, actor: str, spec: Row, pipelines: dict[str, str], made: list[str]
) -> None:
    """With replace, a live link between two pipelines the preset manages that the preset does not declare is retired. Its
    handoffs stay on record: retiring only sets retired_at (Fix V-10, D65: no revenue links)."""
    from . import pipeline_links

    declared: set[str] = set()
    for link in spec["links"]:
        stage = _one(
            conn,
            "SELECT id FROM task_steps WHERE type_id = ? AND name = ? COLLATE NOCASE",
            (pipelines[link["from"]], link["stage"]),
        )
        if stage is not None:
            declared.add(str(stage["id"]))
    managed = set(pipelines.values())
    for live in pipeline_links.links(conn):
        if (
            str(live["from_pipeline_id"]) in managed
            and str(live["to_pipeline_id"]) in managed
            and str(live["from_pipeline_stage_id"]) not in declared
        ):
            pipeline_links.retire_link(conn, actor, str(live["id"]))
            made.append(f"retired link {live['from_pipeline_stage_id']} to {live['to_pipeline_id']}")


def completion_stage(c: sqlite3.Connection, pipeline_id: str) -> str | None:
    """A pipeline's completion stage (D56): its last stage whose status is done, else its last closed stage. A factory's
    output stage is its last pipeline's completion stage. The booted Engineering type has no done stage (its Done is
    closed), so it falls to the closed one. Used by the factory output migration, the setup and the overview."""
    for status in ("done", "closed"):
        row = c.execute(
            "SELECT id FROM task_steps WHERE type_id = ? AND status = ? ORDER BY position DESC, id DESC LIMIT 1",
            (pipeline_id, status),
        ).fetchone()
        if row:
            return str(row[0])
    return None


def apply_factory_setup(
    c: sqlite3.Connection,
    actor: str,
    preset: str = "tico_default",
    replace: bool = False,
    bindings: dict[str, Row] | None = None,
) -> dict[str, Any]:
    """Make the preset's factories, pipelines, stages, links and goals. Idempotent: what exists is adopted (and given a
    goal, prefix, factory or manager only where it has none), never duplicated or overwritten. A manager bot is set when
    one named like "<Name> Pipeline Manager" (or "<Name> Manager", "<Name> Factory Manager") exists. A manager that is not
    an active bot (archived, paused, missing) is replaced by such a match; if there is none, the pipeline is named under
    `unresolved`, as are the links left out. `replace=True` also closes the open cards of an existing, role-less pipeline
    and makes its stages the preset's. A link whose stage cannot be made is refused (422, nothing is written) with
    replace; without it the link is left out and named under `unresolved`. Every stage's public name follows its name.

    `bindings` ({pipeline name: {"bot": ref}} or {pipeline name: {"seat": seat id}}) names who runs a pipeline that has no
    active manager: an active bot (by id or slug, written as its bare id), or an open seat whose hire sets the manager
    (seat_filled). A binding replaces the name match for its pipeline, and an active manager is never replaced. A binding
    that names nothing usable falls back to the name match; a binding for a pipeline the preset does not make is reported.

    {"preset", "factories": {name: id}, "pipelines": {name: id}, "changes": [what was made or changed],
    "unresolved": [what could not be resolved]}."""
    from .store import Problem

    if preset not in PRESETS:
        H.refuse(c, actor, "kind", f"Unknown preset {preset}; the choices are {', '.join(PRESETS)}")
    spec = PRESETS[preset]
    made: list[str] = []
    unresolved: list[str] = []
    c.execute("SAVEPOINT pipeline_setup")
    try:
        factories = {f["name"]: _factory(c, actor, f, i, made) for i, f in enumerate(spec["factories"])}
        names = {str(p["name"]) for p in spec["pipelines"]}
        for name in bindings or {}:
            if name not in names:
                unresolved.append(f"{name} is not a pipeline of {preset}: its binding is ignored")
        pipelines = {
            p["name"]: _pipeline(c, actor, p, factories, replace, made, unresolved, (bindings or {}).get(p["name"]))
            for p in spec["pipelines"]
        }
        missing_links: list[str] = []
        for link in spec["links"]:
            missing = _link(c, actor, link, pipelines, made)
            if missing:
                missing_links.append(missing)
        unresolved.extend(missing_links)
        if replace:
            _retire_obsolete(c, actor, spec, pipelines, made)
        # A factory's output stage (D56): the completion stage of its last pipeline, unless one is already set.
        for f in spec["factories"]:
            in_factory = [p["name"] for p in spec["pipelines"] if p.get("factory") == f["name"]]
            if not in_factory:
                continue
            stage = completion_stage(c, pipelines[in_factory[-1]])
            if stage:
                c.execute(
                    "UPDATE factories SET output_stage_id = COALESCE(output_stage_id, ?) WHERE id = ?",
                    (stage, factories[f["name"]]),
                )
        for pipe in spec["pipelines"]:
            for req in pipe.get("requirements", []):
                _requirement(c, actor, pipelines[pipe["name"]], req, made, pipelines)
        if missing_links and replace:
            raise Problem(
                "unresolved",
                "These links name a stage the setup does not make: " + "; ".join(missing_links),
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
