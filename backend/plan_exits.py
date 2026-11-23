# pyright: strict
"""One exit computer for a person's saved plans."""

from sqlite3 import Connection
from typing import Any, NamedTuple

from fastapi import Query, Request

from . import access, hubdb
from .api_contract import api_v2, error
from .auth import Identity
from .models import ID, Contract
from .read_cache import one, readiness
from .store import Problem

H: Any = hubdb
Row = dict[str, Any]
# SQLite uses the partial index when the kind predicate matches model_subscriptions_pool.
PLAN_OWNERS = (
    "SELECT DISTINCT h.id AS person_id FROM model_subscriptions s JOIN humans h ON s.owner_id='human:' || h.id "
    "WHERE s.kind IN ('claude_token','claude_login','codex_login')"
)
# During replacement, the new runner has consumed its code before the machine row points to it.
PERSONAL_RUNNER = (
    "NOT EXISTS (SELECT 1 FROM cloud_machines m WHERE m.runner_id=r.id) "
    "AND NOT EXISTS (SELECT 1 FROM enrollments e JOIN cloud_machine_codes m USING(code_hash) WHERE e.runner_id=r.id) "
    "AND NOT EXISTS (SELECT 1 FROM enrollments e JOIN hosting_enrollments h USING(code_hash) WHERE e.runner_id=r.id)"
)
EXIT_ROWS = (
    "SELECT p.person_id,r.id AS runner_id,r.label AS runner_label,coalesce(e.generation,0) AS generation, "
    "r.owner_id,r.accepts_member_bots "
    f"FROM ({PLAN_OWNERS}) p LEFT JOIN plan_exits e USING(person_id) "
    "LEFT JOIN runners r ON r.id=e.runner_id AND r.revoked_at IS NULL"
)
PAGE_SIZE = 100
# Upkeep stays dark until turn routing is ready and legacy maintenance jobs have drained.
UPKEEP_ROUTING = False
_SELECTIONS = (
    "SELECT e.person_id,e.generation,r.id,r.platform,r.capabilities,r.readiness FROM plan_exits e "
    "LEFT JOIN runners_with_state r ON r.id=e.runner_id AND r.revoked_at IS NULL"
)


class Route(NamedTuple):
    runner_id: str | None
    generation: int


def selections(c: Connection) -> Row:
    return {row["person_id"]: row for row in c.execute(_SELECTIONS)} if UPKEEP_ROUTING else {}


def selected(c: Connection, sub: Any, exits: Row | None = None) -> Any:
    if (
        not UPKEEP_ROUTING
        or not sub
        or sub["kind"] not in ("claude_token", "claude_login")
        or not sub["owner_id"].startswith("human:")
    ):
        return None
    person = sub["owner_id"].removeprefix("human:")
    if exits is None:
        return one(c, _SELECTIONS + " WHERE e.person_id=?", (person,))
    return exits.get(person)


def routed(c: Connection, sub: Any, exits: Row | None = None, *, require_ready: bool = True) -> Route | None:
    """A missing route uses legacy upkeep. A route without a runner waits for its exit."""
    exit_runner = selected(c, sub, exits)
    if exit_runner is None:
        return None
    runner_id, generation = exit_runner["id"], exit_runner["generation"]
    if runner_id is None:
        return Route(None, generation)
    if sub["kind"] == "claude_login":
        from .claude_login import platform_can_refresh, takes_turns

        if not platform_can_refresh(exit_runner):
            return None
        if require_ready and not takes_turns(exit_runner):
            return Route(None, generation)
    if not require_ready:
        return Route(runner_id, generation)
    ready = readiness(c, runner_id, exit_runner["readiness"])
    runtimes: Row = ready.get("runtimes") or {}
    claude: Row = runtimes.get("claude") or {}
    return Route(runner_id if claude.get("installed") else None, generation)


def matches(route: Route | None, runner_id: str | None, generation: int | None) -> bool:
    """Legacy jobs and generation-bound jobs each belong to their own routing mode."""
    if route is None:
        return generation is None
    return runner_id is not None and route == (runner_id, generation)


class ChangeExit(Contract):
    runner_id: ID


def _choose(c: Connection, person_id: str) -> str | None:
    row = c.execute(
        "SELECT r.id FROM runners r LEFT JOIN runner_states s ON s.runner_id=r.id "
        "WHERE r.owner_id=? AND r.revoked_at IS NULL AND " + PERSONAL_RUNNER + " "
        "ORDER BY s.last_seen_at DESC,r.created_at DESC,r.id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    return row[0] if row else None


def _save(c: Connection, person_id: str, runner_id: str | None) -> bool:
    changed = bool(
        c.execute(
            "INSERT INTO plan_exits(person_id,runner_id,generation) VALUES(?,?,1) "
            "ON CONFLICT(person_id) DO UPDATE SET runner_id=excluded.runner_id,generation=plan_exits.generation+1 "
            "WHERE plan_exits.runner_id IS NOT excluded.runner_id",
            (person_id, runner_id),
        ).rowcount
    )
    if changed and UPKEEP_ROUTING:
        from .model_login import invalidate_connects

        invalidate_connects(c, f"human:{person_id}")
    return changed


def ensure_default(c: Connection, person_id: str) -> None:
    """Choose a computer when a plan or an enrollment first gives the owner an exit."""
    if c.execute(
        f"SELECT 1 FROM ({PLAN_OWNERS}) p LEFT JOIN plan_exits e USING(person_id) "
        "WHERE p.person_id=? AND e.runner_id IS NULL",
        (person_id,),
    ).fetchone() and (runner_id := _choose(c, person_id)):
        _save(c, person_id, runner_id)


def replace_retired(c: Connection, runner_id: str) -> None:
    """Keep the generation even when the last available exit is gone."""
    for (person_id,) in c.execute("SELECT person_id FROM plan_exits WHERE runner_id=?", (runner_id,)).fetchall():
        _save(c, person_id, _choose(c, person_id))


def clear(c: Connection, person_id: str) -> None:
    """Departure: an exit that was handed out is cleared and fenced; nobody else gets a row."""
    changed = c.execute(
        "UPDATE plan_exits SET runner_id=NULL,generation=generation+1 WHERE person_id=? AND runner_id IS NOT NULL",
        (person_id,),
    ).rowcount
    if changed and UPKEEP_ROUTING:
        from .model_login import invalidate_connects

        invalidate_connects(c, f"human:{person_id}")


def replace_closed(c: Connection, runner_id: str) -> None:
    admins = access.owner_and_admin_emails(c)
    for person_id, email in c.execute(
        "SELECT e.person_id,lower(h.email) FROM plan_exits e JOIN humans h ON h.id=e.person_id "
        "JOIN runners r ON r.id=e.runner_id WHERE e.runner_id=? AND e.person_id<>r.owner_id",
        (runner_id,),
    ).fetchall():
        if email not in admins:
            _save(c, person_id, _choose(c, person_id))


def initialize(c: Connection) -> None:
    """Backfill after migrations have settled the owners of existing plans."""
    for (runner_id,) in c.execute(
        "SELECT DISTINCT e.runner_id FROM plan_exits e JOIN runners r ON r.id=e.runner_id WHERE r.revoked_at IS NOT NULL"
    ).fetchall():
        replace_retired(c, runner_id)
    for (person_id,) in c.execute(
        f"SELECT p.person_id FROM ({PLAN_OWNERS}) p LEFT JOIN plan_exits e USING(person_id) WHERE e.runner_id IS NULL"
    ).fetchall():
        if chosen := _choose(c, person_id):
            _save(c, person_id, chosen)


def _may_change(auth: Any, who: Identity, person_id: str) -> bool:
    return not who.support and (who.actor == f"human:{person_id}" or auth.bot_admin(who))


def _view(row: Any, auth: Any, who: Identity) -> Row:
    person_id = row["person_id"]
    visible = row["accepts_member_bots"] or who.actor == f"human:{row['owner_id']}" or auth.bot_admin(who)
    return {
        "person_id": person_id,
        "runner_id": row["runner_id"] if visible else None,
        "runner_label": "Private computer" if row["runner_id"] and not visible else row["runner_label"],
        "generation": row["generation"],
        "can_change": _may_change(auth, who, person_id),
    }


def listed(c: Connection, auth: Any, who: Identity) -> list[Row]:
    return [_view(row, auth, who) for row in c.execute(EXIT_ROWS + " ORDER BY p.person_id")]


def _person(value: str) -> str:
    """A pool is addressed by its owner's id or `human:<id>`; a missing pool is a 404 from `_exit`."""
    return value.removeprefix("human:") if ":" not in value.removeprefix("human:") else ""


def _exit(c: Connection, person_id: str) -> Any:
    row = c.execute(EXIT_ROWS + " WHERE p.person_id=?", (person_id,)).fetchone()
    if not row:
        raise Problem("not_found", "Personal plan pool not found", 404)
    return row


def _eligible(c: Connection, person_id: str, who: Identity) -> tuple[str, tuple[str] | tuple[str, bool]]:
    if who.actor != f"human:{person_id}":
        return " FROM runners WHERE revoked_at IS NULL AND owner_id=?", (person_id,)
    # Apply the Computers access rule when the pool owner chooses their own exit.
    email = c.execute("SELECT lower(email) FROM humans WHERE id=?", (person_id,)).fetchone()[0]
    return (
        " FROM runners WHERE revoked_at IS NULL AND (owner_id=? OR accepts_member_bots=1 OR ?)",
        (person_id, email in access.owner_and_admin_emails(c)),
    )


def install(app: Any, store: Any, auth: Any, mutate: Any) -> None:
    from . import model_subscriptions

    subs: Any = model_subscriptions
    _reader = subs._reader

    path = "/api/v2/model-subscriptions/plan-exits/{person}"
    errors = {"404": error("Personal plan pool not found")}

    @app.get(
        path,
        **api_v2(
            "Model subscriptions",
            "getPlanExit",
            "Read the exit for a person's plan pool",
            "PlanExitSettings",
            responses=errors,
        ),
    )
    def get_exit(request: Request, person: ID, after: str = Query("", max_length=200)) -> Row:
        who = request.state.identity
        _reader(who)
        with store.read_transaction() as c:
            person_id = _person(person)
            result = _view(_exit(c, person_id), auth, who)
            computers: list[Row] = []
            if result["can_change"]:
                eligible, args = _eligible(c, person_id, who)
                computers = [
                    dict(row)
                    for row in c.execute(
                        "SELECT id,label" + eligible + " AND id>? ORDER BY id LIMIT ?", (*args, after, PAGE_SIZE + 1)
                    )
                ]
            return {
                **result,
                "computers": computers[:PAGE_SIZE],
                "next_after": computers[PAGE_SIZE - 1]["id"] if len(computers) > PAGE_SIZE else None,
            }

    @app.put(
        path,
        **api_v2(
            "Model subscriptions",
            "setPlanExit",
            "Change the exit computer for a person's plan pool",
            "PlanExit",
            responses={
                **errors,
                "403": error(
                    "The pool owner may choose a computer they can use. An admin may choose one the owner owns."
                ),
            },
        ),
    )
    def set_exit(request: Request, person: ID, body: ChangeExit) -> Row:
        who = request.state.identity
        _reader(who)

        def check(c: Connection) -> None:
            auth.sync_access(c)
            _exit(c, _person(person))  # a missing pool is a 404 for everyone
            if not _may_change(auth, who, _person(person)):
                raise Problem("forbidden", "Only the pool owner or an admin can change its exit", 403)

        def work(c: Connection) -> Row:
            person_id = _person(person)
            _exit(c, person_id)
            eligible, args = _eligible(c, person_id, who)
            if not c.execute("SELECT 1" + eligible + " AND id=?", (*args, body.runner_id)).fetchone():
                detail = (
                    "Choose a registered computer you can use"
                    if who.actor == f"human:{person_id}"
                    else "Choose a registered computer owned by the pool owner"
                )
                raise Problem("forbidden", detail, 403)
            if _save(c, person_id, body.runner_id):
                H.event(c, who.actor, "subscription.plan_exit", person_id, {"runner_id": body.runner_id})
            return _view(_exit(c, person_id), auth, who)

        result: Row = mutate(request, body, work, check=check)
        return result
