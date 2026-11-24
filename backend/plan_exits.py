# pyright: strict
"""One exit computer for a person's saved plans."""

import json
from sqlite3 import Connection, OperationalError
from typing import Any, NamedTuple

from fastapi import Query, Request

from . import access, hubdb
from .api_contract import api_v2, error
from .auth import Identity
from .models import ID, Contract
from .read_cache import METADATA, one, readiness
from .store import Problem

H: Any = hubdb
Row = dict[str, Any]
# Every plan kind (model_subscriptions.POOL_KINDS, which imports this module, so written out; a test keeps them equal),
# as the model_subscriptions_plan_pool partial index names them.
PLAN_OWNERS = (
    "SELECT DISTINCT h.id AS person_id FROM model_subscriptions s JOIN humans h ON s.owner_id='human:' || h.id "
    "WHERE s.kind IN ('claude_token','claude_login','codex_login','antigravity_login')"
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
SETTING = "plan_exit_routing"
MODEL_CAPABILITY = "model_exit_v1"
CLAUDE_CAPABILITY = "model_exit_claude_v1"
_SELECTIONS = (
    "SELECT e.person_id,e.generation,r.id,r.label,r.platform,r.capabilities,r.readiness,r.last_seen_at,"
    "r.owner_id,r.accepts_member_bots FROM plan_exits e "
    "LEFT JOIN runners_with_state r ON r.id=e.runner_id AND r.revoked_at IS NULL"
)


class Route(NamedTuple):
    runner_id: str | None
    generation: int


def enabled(c: Connection) -> bool:
    if (policy := getattr(c, "plan_exit_policy", None)) is not None:
        return bool(policy[0])
    return stored_enabled(c)


def stored_enabled(c: Connection) -> bool:
    try:
        found = c.execute(METADATA, (SETTING,)).fetchone()
    except OperationalError as e:
        if str(e) == "no such table: registry_metadata":
            return False
        raise
    return bool(found and found[0] == "true")


def _advance_policy(c: Any, on: bool) -> None:
    if hasattr(c, "plan_exit_policy"):
        c.plan_exit_policy = (on, c.plan_exit_policy[1] + 1)


def selections(c: Connection) -> Row:
    return {row["person_id"]: row for row in c.execute(_SELECTIONS)} if enabled(c) else {}


def selected(c: Connection, sub: Any, exits: Row | None = None) -> Any:
    if not sub or sub["kind"] not in ("claude_token", "claude_login") or not sub["owner_id"].startswith("human:"):
        return None
    person = sub["owner_id"].removeprefix("human:")
    if exits is None:
        return one(c, _SELECTIONS + " WHERE e.person_id=?", (person,)) if enabled(c) else None
    return exits.get(person)


def routed(
    c: Connection, sub: Any, exits: Row | None = None, *, require_ready: bool = True, connect_kind: str | None = None
) -> Route | None:
    """A missing route uses legacy upkeep. A route without a runner waits for its exit."""
    exit_runner = selected(c, sub, exits)
    if exit_runner is None:
        return None
    runner_id, generation = exit_runner["id"], exit_runner["generation"]
    if runner_id is None:
        return Route(None, generation)
    if sub["kind"] == "claude_login":
        from .claude_login import platform_can_refresh

        if not platform_can_refresh(exit_runner):
            return None
    if not require_ready:
        return Route(runner_id, generation)
    if (connect_kind or sub["kind"]) == "claude_login":
        from .claude_login import takes_turns

        if not takes_turns(exit_runner):
            return Route(None, generation)
    ready = readiness(c, runner_id, exit_runner["readiness"])
    runtimes: Row = ready.get("runtimes") or {}
    claude: Row = runtimes.get("claude") or {}
    return Route(runner_id if claude.get("installed") else None, generation)


def matches(route: Route | None, runner_id: str | None, generation: int | None) -> bool:
    """Legacy jobs and generation-bound jobs each belong to their own routing mode."""
    if route is None:
        return generation is None
    return runner_id is not None and route == (runner_id, generation)


def for_turn(c: Connection, sub: Any, runner_id: str, exits: Row | None = None) -> Route | None:
    return _turn_route(c, routed(c, sub, exits, require_ready=False), runner_id)


def _turn_route(c: Connection, route: Route | None, runner_id: str) -> Route | None:
    if route and one(c, "SELECT 1 FROM runners r WHERE r.id=? AND " + PERSONAL_RUNNER, (runner_id,)):
        return route
    return None


def remotely_ready(c: Connection, sub: Any, route: Route) -> bool:
    if not route.runner_id:
        return False
    relay = getattr(c, "screen_sessions", None)
    if relay is None or not relay.model_online(route.runner_id):
        return False
    exit_runner = selected(c, sub)
    return bool(exit_runner and MODEL_CAPABILITY in (H._json(exit_runner["capabilities"], []) or []))


def _visible(row: Any, auth: Any, who: Identity | None) -> bool:
    return bool(
        row["accepts_member_bots"] or (who and (who.actor == f"human:{row['owner_id']}" or auth.bot_admin(who)))
    )


def waiting(exit_runner: Any, auth: Any = None, who: Identity | None = None) -> str:
    label = None
    if exit_runner and exit_runner["id"]:
        label = exit_runner["label"] if _visible(exit_runner, auth, who) else "Private computer"
    return f"Waiting for {label or 'a plan exit computer'}"


def wake_payers(c: Connection, owners: list[str]) -> None:
    c.execute(
        "WITH owners AS (SELECT 'human:'||value AS id FROM json_each(?)), "
        "picks AS (SELECT p.bot FROM agent_subscriptions p JOIN owners o ON p.person=o.id "
        "WHERE p.kind='pool' AND p.runtime='claude' "
        "UNION SELECT p.bot FROM agent_subscriptions p JOIN model_subscriptions s ON s.id=p.subscription_id "
        "JOIN owners o ON o.id=s.owner_id WHERE p.kind='plan' AND p.runtime='claude') "
        "INSERT INTO changes(topic,kind,subject_id,bot,created_at) "
        "SELECT DISTINCT 'runner','work',a.runner_id,a.bot,? FROM picks p JOIN assignments a USING(bot)",
        (json.dumps(owners), H.now()),
    )


def _notify_exit(c: Connection, runner_id: str | None) -> None:
    if runner_id:
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('runner','browser_exit',?,?)",
            (runner_id, H.now()),
        )


def supervised(c: Connection, runner_id: str) -> bool:
    if not enabled(c):
        return False
    from .claude_login import platform_can_refresh

    return any(
        row["has_setup_token"] or platform_can_refresh(row)
        for row in c.execute(
            "SELECT r.platform, max(s.kind='claude_token') AS has_setup_token "
            "FROM plan_exits e JOIN runners r ON r.id=e.runner_id "
            "JOIN model_subscriptions s ON s.owner_id='human:'||e.person_id "
            "WHERE e.runner_id=? AND s.kind IN ('claude_token','claude_login','codex_login') "
            "AND s.kind<>'codex_login' GROUP BY r.platform",
            (runner_id,),
        )
    )


def _clear_turn_usage(c: Connection, owner: str | None = None) -> None:
    """Drop cached turn readings at a cutover and mark when it happened: a reading taken before it is never used."""
    from datetime import datetime, timezone

    from .subscription_refresh import PLAN_PREFIX

    c.execute(
        "INSERT INTO registry_metadata(key,value) SELECT ? || id, json_object('turn_cutover_at', ?) "
        "FROM model_subscriptions WHERE kind IN ('claude_token','claude_login') AND owner_id GLOB 'human:*' "
        "AND (? IS NULL OR owner_id=?) "
        "ON CONFLICT(key) DO UPDATE SET value=json_set(json_remove(CASE WHEN json_valid(value) THEN value ELSE '{}' END, "
        "'$.turn_weekly', '$.turn_five_hour', '$.turn_weekly_exit', '$.turn_five_hour_exit'), "
        "'$.turn_cutover_at', json_extract(excluded.value, '$.turn_cutover_at'))",
        (PLAN_PREFIX, datetime.now(timezone.utc).isoformat(), owner, owner),
    )


def _fence(c: Connection, person_id: str, *, disabling: bool = False) -> None:
    from . import model_login, quarantine_stops

    owner = f"human:{person_id}"
    for sub in c.execute(
        "SELECT id,owner_id,kind,revision FROM model_subscriptions "
        "WHERE owner_id=? AND kind IN ('claude_token','claude_login')",
        (owner,),
    ).fetchall():
        route = None if disabling else routed(c, sub, require_ready=False)
        for attempt in c.execute(
            "SELECT a.*,s.delivery,e.exit_generation FROM attempts a JOIN attempt_subscriptions s ON s.attempt_id=a.id "
            "JOIN runners r ON r.id=a.runner_id "
            "LEFT JOIN plan_exit_bindings e ON e.kind='attempt' AND e.ref_id=a.id "
            "WHERE s.subscription_id=? AND a.state IN ('leased','running') AND a.stop_requested_at IS NULL "
            f"AND (s.delivery='model_exit' OR ({PERSONAL_RUNNER}))",
            (sub["id"],),
        ).fetchall():
            turn_route = _turn_route(c, route, attempt["runner_id"])
            remote = bool(turn_route and turn_route.runner_id != attempt["runner_id"])
            if remote != (attempt["delivery"] == "model_exit") or (
                (turn_route.generation if turn_route else None) != attempt["exit_generation"]
            ):
                quarantine_stops.restart(c, attempt, "The plan exit route changed")
        if sub["kind"] == "claude_login":
            # An outstanding refresh may already have spent its single-use token. Keep the issued receipt so
            # a late success can restore the login, but require reconnect if the result never arrives.
            c.execute(
                "UPDATE model_subscriptions SET status='needs_reconnect',status_detail=?,status_at=? "
                "WHERE id=? AND status<>'needs_reconnect' AND EXISTS (SELECT 1 FROM claude_logins l "
                "LEFT JOIN plan_exit_bindings e ON e.kind='lease' AND e.ref_id=l.lease_id "
                "WHERE l.subscription_id=? AND l.lease_id<>'' AND l.lease_revision=? "
                "AND (e.exit_generation IS NOT NULL OR ?))",
                (
                    "Refresh interrupted by a plan exit change. Reconnect this plan.",
                    H.now(),
                    sub["id"],
                    sub["id"],
                    sub["revision"],
                    route is not None,
                ),
            )
    if disabling:
        model_login.invalidate_routed_connects(c, owner)
    else:
        model_login.invalidate_connects(c, owner)
    wake_payers(c, [person_id])


def set_enabled(c: Connection, on: bool, actor: str) -> None:
    if enabled(c) == on:
        return
    exits = c.execute("SELECT person_id,runner_id FROM plan_exits").fetchall()
    # Both edges invalidate claims carried across the change, even if the selected computer stays the same.
    c.execute("UPDATE plan_exits SET generation=generation+1")
    _clear_turn_usage(c)
    _advance_policy(c, on)
    if on:
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES(?, 'true') ON CONFLICT(key) DO UPDATE SET value='true'",
            (SETTING,),
        )
    for person_id, runner_id in exits:
        _fence(c, person_id, disabling=not on)
        _notify_exit(c, runner_id)
    if not on:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (SETTING,))
    H.event(c, actor, "subscription.plan_exit_routing", SETTING, {"enabled": on})


class ChangeExit(Contract):
    runner_id: ID


class ChangeRouting(Contract):
    enabled: bool


def _choose(c: Connection, person_id: str) -> str | None:
    row = c.execute(
        "SELECT r.id FROM runners r LEFT JOIN runner_states s ON s.runner_id=r.id "
        "WHERE r.owner_id=? AND r.revoked_at IS NULL AND " + PERSONAL_RUNNER + " "
        "ORDER BY s.last_seen_at DESC,r.created_at DESC,r.id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    return row[0] if row else None


def _save(c: Connection, person_id: str, runner_id: str | None) -> bool:
    on = enabled(c)
    previous = one(c, "SELECT runner_id FROM plan_exits WHERE person_id=?", (person_id,)) if on else None
    changed = bool(
        c.execute(
            "INSERT INTO plan_exits(person_id,runner_id,generation) VALUES(?,?,1) "
            "ON CONFLICT(person_id) DO UPDATE SET runner_id=excluded.runner_id,generation=plan_exits.generation+1 "
            "WHERE plan_exits.runner_id IS NOT excluded.runner_id",
            (person_id, runner_id),
        ).rowcount
    )
    if not changed:
        return False
    if on:
        # With routing off nothing is exit-bound, and enabling clears every plan's readings anyway.
        _clear_turn_usage(c, f"human:{person_id}")
        _advance_policy(c, on)
        _fence(c, person_id)
        _notify_exit(c, previous[0] if previous else None)
        _notify_exit(c, runner_id)
    return True


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
    if c.execute("SELECT 1 FROM plan_exits WHERE person_id=? AND runner_id IS NOT NULL", (person_id,)).fetchone():
        _save(c, person_id, None)


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
    visible = _visible(row, auth, who)
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

    routing_path = "/api/v2/settings/plan-exit-routing"
    admin_only = {"403": error("Only a company admin may manage plan exit routing")}

    @app.get(
        routing_path,
        **api_v2(
            "Model subscriptions",
            "getPlanExitRouting",
            "Read company plan exit routing",
            ChangeRouting,
            responses=admin_only,
        ),
    )
    def get_routing(request: Request) -> Row:
        who = request.state.identity
        with store.read() as c:
            auth.sync_access(c)
            if not subs._may_manage(auth, who):
                raise Problem("forbidden", "Only a company admin may manage plan exit routing", 403)
            return {"enabled": enabled(c)}

    @app.put(
        routing_path,
        **api_v2(
            "Model subscriptions",
            "setPlanExitRouting",
            "Set company plan exit routing",
            ChangeRouting,
            responses={**admin_only, "503": error("The screen relay is unavailable")},
        ),
    )
    def put_routing(request: Request, body: ChangeRouting) -> Row:
        who = request.state.identity

        def check(c: Connection) -> None:
            auth.sync_access(c)
            if not subs._may_manage(auth, who):
                raise Problem("forbidden", "Only a company admin may manage plan exit routing", 403)

        def work(c: Connection) -> Row:
            if body.enabled and not enabled(c):
                relay = getattr(c, "screen_sessions", None)
                if not relay or not relay.healthy:
                    raise Problem(
                        "screen_relay_unavailable",
                        "Plan exit routing requires a healthy screen relay. Try again shortly.",
                        503,
                    )
            set_enabled(c, body.enabled, who.actor)
            return {"enabled": body.enabled}

        answer: Row = mutate(request, body, work, check=check)
        return answer

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
