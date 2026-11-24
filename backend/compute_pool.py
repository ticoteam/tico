# pyright: strict
"""One prepared Base turn at a time on an idle company Burst runner."""

import hashlib
from collections.abc import Callable, Iterable
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request

from clients import harness_manifests

from . import bot_settings as BS
from . import (
    gateway_connection,
    harnesses,
    models_preset,
    plan_exits,
    providers,
    readiness,
    runner_online,
    runner_versions,
    store,
    task_reads,
)
from .api_contract import api_v2, error
from .auth import Identity
from .store import H as _H
from .store import Problem, encode

if TYPE_CHECKING:
    from .agent_subscriptions import Payment
    from .config import Settings
    from .execution import Execution

Row = dict[str, Any]

# These modules work with dynamically shaped rows and configuration.
H: Any = _H
_gateway: Any = gateway_connection
_manifests: Any = harness_manifests
_presets: Any = models_preset
_provider_settings: Any = providers
_runner_readiness: Any = readiness
_harnesses: Any = harnesses
_store: Any = store
resolve_harness: Callable[[Row], str] = _harnesses.resolve_harness
bot_readiness: Callable[[Any, str], Row] = _store.bot_readiness
readiness_document: Callable[[Any], Row] = _store.readiness_document

SETTING = "burst_rehome"
AUTOSCALE = "burst_autoscale"
KEY = "_burst:"
PREPARATION = "burst_preparation_v1"
LEASE = "burst_lease_v1"
REVOKING = "burst-return:"
CHECKOUT_BLOCK = "burst-checkout:"


def enabled(c: Connection) -> bool:
    return getattr(c, "burst_rehome", False)


def autoscale(c: Connection) -> bool:
    flags = plan_exits.stored_flags(c, SETTING, AUTOSCALE)
    return flags.get(SETTING) == flags.get(AUTOSCALE) == "true"


def burst_key(key: str) -> bool:
    return key.startswith(KEY) and key.removeprefix(KEY).isascii() and key.removeprefix(KEY).isdecimal()


def target(c: Connection, runner: Any) -> bool:
    if not enabled(c) or not runner or runner["revoked_at"]:
        return False
    return any(
        burst_key(row[0])
        for row in c.execute("SELECT bot FROM cloud_machines WHERE runner_id=? AND state='running'", (runner["id"],))
    )


def allowed_payment(c: Connection, payment: "Payment | None") -> bool:
    # Cloud plans currently run directly. A selected personal exit would be bypassed there.
    return payment is None or (
        payment.subscription is not None and not plan_exits.routed(c, payment.subscription, require_ready=False)
    )


def idle_slots(c: Connection, executor: "Execution", runner_owner: str) -> Iterable[Any]:
    from . import execution

    work: Any = execution
    slots = c.execute(
        "SELECT m.bot AS machine_key,r.* FROM cloud_machines m JOIN runners_with_state r ON r.id=m.runner_id "
        "WHERE m.state='running' AND r.revoked_at IS NULL AND r.owner_id=? AND r.last_seen_at>? "
        "AND EXISTS (SELECT 1 FROM json_each(r.capabilities) WHERE value=?) "
        "AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.runner_id=r.id AND a.state IN ('leased','running')) "
        "AND NOT EXISTS (SELECT 1 FROM burst_guests g WHERE g.runner_id=r.id) "
        "ORDER BY CAST(substr(m.bot,8) AS INTEGER),m.bot",
        (runner_owner, runner_online.cutoff(), PREPARATION),
    )
    for runner in slots:
        if (
            burst_key(runner["machine_key"])
            and not any(work.claimed_busy(runner["id"]))
            and slot_admission(c, executor, runner, burst=True) is not None
        ):
            yield runner


def slot_admission(c: Connection, executor: "Execution", runner: Any, *, burst: bool = False) -> bool | None:
    from . import start_places

    claim: Any = executor
    places: Any = start_places
    rid = runner["id"]
    at = H.now()
    if (
        not runner_online.is_online(runner["last_seen_at"])
        or claim.claim_runner_status(c, rid)[0]
        or not claim.awake(claim.waking(runner, at), at, runner["platform"])
    ):
        return None
    people_only, refused = claim.held_starts(c, rid)
    if (
        refused
        or (burst and people_only)
        or sum(places.held(c, rid).values()) >= places.turn_limit(runner)
        or sum(places.live(c, rid).values()) >= places.live_limit(runner)
    ):
        return None
    return bool(people_only)


def preparation_slot(
    c: Connection, executor: "Execution", owner: str, job: Row | None = None
) -> tuple[str | None, Row | None]:
    from . import cloud_machines

    machines: Any = cloud_machines
    for runner in idle_slots(c, executor, owner):
        slot = runner["id"]
        if entry := candidate(c, executor, runner, job=job, demand=True, company_paid=machines.burst_managed(c, slot)):
            return slot, entry
    return None, None


def offer(
    c: Connection,
    executor: "Execution",
    runner: Any,
    *,
    only: str | None = None,
    skip: Iterable[str] = (),
    prepare: bool = False,
) -> Row | None:
    from . import cloud_machines, execution

    machines: Any = cloud_machines
    work: Any = execution
    if (
        (prepare and not target(c, runner))
        or PREPARATION not in _H._json(runner["capabilities"], [])  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage] - capabilities can already be decoded
        or slot_admission(c, executor, runner, burst=True) is None
    ):
        return None
    rid = runner["id"]
    if (
        any(work.claimed_busy(rid))
        or c.execute(
            "SELECT 1 FROM attempts WHERE runner_id=? AND state IN ('leased','running') LIMIT 1", (rid,)
        ).fetchone()
    ):
        return None
    # Preparing on one slot avoids cloning the same checkout across idle workers.
    if prepare:
        if autoscale(c):
            slot, entry = preparation_slot(c, executor, runner["owner_id"])
            return entry["burst"] if entry and slot == rid else None
        if rid != next((r["id"] for r in idle_slots(c, executor, runner["owner_id"])), None):
            return None
    return candidate(
        c, executor, runner, only=only, skip=skip, prepare=prepare, company_paid=machines.burst_managed(c, rid)
    )


def candidate(
    c: Connection,
    executor: "Execution",
    runner: Any,
    *,
    only: str | None = None,
    job: Row | None = None,
    skip: Iterable[str] = (),
    prepare: bool = True,
    demand: bool = False,
    company_paid: bool = False,
) -> Row | None:
    from . import cloud_machines, relationship_runs
    from .models import Claim
    from .worktrees import WORKTREE_OWNER

    base = c.execute(
        "SELECT r.* FROM cloud_machines m JOIN runners_with_state r ON r.id=m.runner_id "
        "WHERE m.bot=? AND r.revoked_at IS NULL",
        (cloud_machines.SHARED,),
    ).fetchone()
    if not base:
        return None
    auth: Any = executor.auth  # pyright: ignore[reportUnknownMemberType] - Execution receives the server's auth service
    if runner["owner_id"] != auth.owner_id(c):
        return None
    bots: Row = {
        row["bot"]: row
        for row in c.execute(
            f"""SELECT a.bot,a.runner_id,b.slug,{BS.CONFIG} AS config
        FROM assignments a
        JOIN cloud_machines p ON p.bot=a.bot AND p.mode='cloud' AND p.placement='shared'
        JOIN bots b ON b.id=a.bot AND b.state='active'
        JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN}
        WHERE a.runner_id=? AND a.bot<>? AND (? IS NULL OR a.bot=?)
          AND NOT EXISTS (SELECT 1 FROM assignment_pins x WHERE x.bot=a.bot)
          AND NOT EXISTS (SELECT 1 FROM burst_guests g WHERE g.bot=a.bot)
          AND NOT EXISTS (SELECT 1 FROM live_meeting_bots x JOIN jobs voice ON voice.id=x.voice_job
                          WHERE x.bot=a.bot AND voice.state IN ('queued','leased','running','input','uncertain'))
          AND NOT EXISTS (SELECT 1 FROM attempts x WHERE x.bot_id=a.bot AND x.state IN ('leased','running'))
          AND NOT EXISTS (SELECT 1 FROM jobs x WHERE x.bot_id=a.bot AND x.state='uncertain')
          AND NOT EXISTS (SELECT 1 FROM task_links l JOIN tasks t ON t.id=l.task_id
                          JOIN runners r ON r.id=l.computer_id AND r.revoked_at IS NULL
                          WHERE l.kind='worktree' AND coalesce(l.state,'unknown')<>'removed'
                          AND {WORKTREE_OWNER}='bot:'||a.bot)""",
            (base["id"], relationship_runs.BOT, job["bot_id"] if job else None, job["bot_id"] if job else None),
        )
    }
    document: Row = _runner_readiness.local_sign_ins(readiness_document(runner["readiness"]))
    prepare_after: str | None = None
    if prepare:
        # An idle slot reports the bot it just tried. Move past a checkout that cannot be claimed.
        reported: Row = document["bots"]
        for bot in reported:
            if bot not in bots:
                continue
            check = bot_readiness(document, bot)
            if (
                not check.get("burst_preparation")
                or check.get("configuration_valid") is not True
                or check.get("repository_present") is not True
            ):
                prepare_after = bot
                break
    elif not prepare:
        bots = {bot: row for bot, row in bots.items() if bot_readiness(document, bot).get("burst_preparation")}
    if not bots:
        return None

    def accept(row: Any) -> Row | None:
        executor_store: Any = executor.store  # pyright: ignore[reportUnknownMemberType] - Execution receives the server's store
        job: Row = dict(row)
        assignment: Row = dict(bots[row["bot_id"]])
        entry = _offer(c, executor_store.settings, runner, document, {**job, **assignment}, company_paid=company_paid)
        if entry and (prepare or ready(runner, entry)):
            if demand:
                return {**job, "burst": entry}
            return entry if prepare else {**job, "burst": entry}
        return None

    selector: Any = executor
    candidate: Callable[..., Row | None] = (
        selector._candidate
    )  # the shared selector accepts the Burst eligibility filter
    return candidate(
        c,
        Claim(bot=only),
        runner,
        str(job["id"]) if job else None,
        False,
        None,
        skip,
        base["id"],
        eligible=bots,
        accept=accept,
        preparing=prepare,
        prepare_after=prepare_after,
    )


def _offer(
    c: Connection, settings: "Settings", runner: Any, document: Row, job: Any, *, company_paid: bool = False
) -> Row | None:
    from . import agent_subscriptions, browser_commands, execution, inbox_isolation, shared_bots, views

    work: Any = execution
    isolation: Any = inbox_isolation
    shares: Any = shared_bots
    people: Any = views

    rid = runner["id"]
    bot, base = job["bot_id"], job["runner_id"]
    if (
        not work.source_idle(c, base, bot)
        or fresh_recovery(job)
        or work.continuation_run(c, job["id"])
        or H.folder_clash(c, bot, rid)
        or browser_commands.host_id(c) in (base, rid)
        or {base, rid} & isolation.inbox_only(c, people.roster(c))
    ):
        return None
    declared: Row = _H._json(job["config"], {}) or {}  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage] - uses the same stored JSON decoder as Base
    if shares.source_of(declared) or not (repo := work.runner_repository(c, settings, bot)):
        return None
    _, report = work._reported(c, base, bot)
    revision = report.get("burst_revision")
    if company_paid and (
        not revision or report.get("repository_present") is not True or report.get("configuration_valid") is not True
    ):
        return None
    if not revision and report.get("published") is not True:
        return None
    blocked = c.execute("SELECT value FROM registry_metadata WHERE key=?", (CHECKOUT_BLOCK + bot,)).fetchone()
    if blocked and (not revision or revision == blocked[0]):
        return None
    message = H.message(c, job["message_id"])
    room = H.conversation(c, message["conversation_id"]) if message else None
    if not room or not work.job_message_readable(c, bot, message):
        return None
    if (
        work.work_task_id(message, room)
        or {"live_meeting", "call_prep", "voice_call", "voice_reply"} & message["refs"].keys()
    ):
        return None  # Task lanes can create a local worktree.
    followed = shares.follow(c, bot, work.assignment_name(declared, bot, job["slug"]))
    record = _provider_settings.load(c, settings)
    checkout = runner_versions.wire_template(_provider_settings.fill(record, followed))
    config, payment = work.queued_payer(c, settings, runner, job, followed, message, room, None, None)
    if not allowed_payment(c, payment) or not config.get("runtime"):
        return None
    if payment is None:
        config = _presets.main_config(record, document, config)
    harness = resolve_harness(config)
    runtime = config["runtime"]
    gateway = _gateway.is_tico(config.get("model"))
    if company_paid and not (
        gateway
        or payment is not None
        and payment.subscription is not None
        and not payment.subscription["owner_id"]
        and agent_subscriptions.portable(c, payment.subscription)
    ):
        return None
    state = _runner_readiness.computer_login(document, runtime)
    login_ready = (
        _presets.runs_gateway(document)
        if gateway
        else (payment is not None or state.get("authenticated") in ("ready", True))
    )
    if not tools_ready(document, runtime, harness) or not login_ready:
        return None
    ticket = hashlib.sha256(
        encode([rid, base, job["generation"], bot, repo, checkout, config["runtime"], harness, revision]).encode()
    ).hexdigest()
    return {
        "bot": bot,
        "runner_id": rid,
        "generation": job["generation"],
        "repository": repo,
        "config": checkout,
        "runtime": config["runtime"],
        "harness": harness,
        "computer_logins_v1": True,
        "burst_preparation": ticket,
        "base_runner": base,
        **({"burst_revision": revision} if revision else {}),
    }


def ready(runner: Any, entry: Row, config: Row | None = None) -> bool:
    document = readiness_document(runner["readiness"])
    check = bot_readiness(document, entry["bot"])
    runtime, harness = entry["runtime"], entry["harness"]
    if config is not None and (config.get("runtime"), resolve_harness(config)) != (runtime, harness):
        return False
    checkout = entry["config"]
    return (
        check.get("burst_preparation") == entry["burst_preparation"]
        and (not entry.get("burst_revision") or check.get("burst_revision") == entry["burst_revision"])
        and check.get("repository_present") is True
        and check.get("configuration_valid") is True
        and (check.get("runtime"), check.get("harness")) == (checkout.get("runtime"), resolve_harness(checkout))
        and tools_ready(document, runtime, harness)
    )


def tools_ready(document: Row, runtime: str, harness: str) -> bool:
    empty: Row = {}
    manifest_row: Row = _manifests.get(harness) or empty
    manifest = manifest_row.get("manifest") or harness
    tool: Row = document.get("harnesses", empty).get(manifest) or empty
    return (
        tool.get("installed") is True
        and tool.get("state") not in ("installing", "updating")
        and (document.get("runtimes", empty).get(runtime) or empty).get("installed") is True
    )


def move(c: Connection, entry: Row, generation: int) -> bool:
    bot, runner, base = entry["bot"], entry["runner_id"], entry["base_runner"]
    if not c.execute(
        "UPDATE assignments SET runner_id=?,generation=?,updated_at=?,updated_by=? "
        "WHERE bot=? AND runner_id=? AND generation=?",
        (runner, generation, H.now(), H.TICO_ACTOR, bot, base, generation - 1),
    ).rowcount:
        return False
    c.execute(
        "INSERT INTO burst_guests(bot,runner_id,generation,base_runner) VALUES(?,?,?,?)",
        (bot, runner, generation, base),
    )
    changed(c, bot, runner, generation)
    return True


def changed(c: Connection, bot: str, runner: str, generation: int) -> None:
    from . import built_on

    built_on.forget(c, bot)  # pyright: ignore[reportUnknownMemberType] - the cache invalidator is untyped
    task_reads.status_moved(c, [bot])
    H.event(c, H.TICO_ACTOR, "bot.assigned", bot, {"runner": runner, "generation": generation})


def borrowed(attempt: Any) -> bool:
    wake: Row = _H._json(attempt["wake"], {}) or {}  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage] - wake can already be decoded
    return wake.get("burst") is True


def suppress_checkout(c: Connection, attempt: Any) -> None:
    wake: Row = _H._json(attempt["wake"], {}) or {}  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage] - wake can already be decoded
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (CHECKOUT_BLOCK + attempt["bot_id"], wake.get("burst_revision") or ""),
    )


def hold_for_recovery(c: Connection, attempt: Any, *, checkout: str | None = None, session: bool = False) -> bool:
    reason = {
        "mismatch": "The prepared Burst checkout changed before the turn could start.",
        "unpublished": "Burst could not publish all checkout changes. Recover them before retrying.",
        "lost": "Burst was lost before the checkout could be confirmed published.",
    }.get(checkout or "", "")
    if session:
        reason += (" " if reason else "") + (
            "The saved Burst session is unavailable on Base. After reviewing the run, its operator can retry in a fresh session."
        )
    H.event(
        c,
        H.TICO_ACTOR,
        "job.held",
        attempt["id"],
        {
            "job_id": attempt["job_id"],
            "tools": [],
            "reason": reason,
            "burst": "session" if session else "checkout",
        },
    )
    job = c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()
    message = H.message(c, job["message_id"]) if job else None
    if message and H.is_human(message["sender_id"]):
        H.attempt(
            c,
            H.say,
            H.TICO_ACTOR,
            message["sender_id"],
            reason,
            kind="notice",
            conversation_id=message["conversation_id"],
            refs={"turn_id": attempt["id"]},
        )
    return True


def recovery_hold(c: Connection, attempt_id: str) -> Any:
    return c.execute(
        "SELECT json_extract(detail,'$.burst') FROM events WHERE target=? AND action='job.held' "
        "AND json_extract(detail,'$.burst') IS NOT NULL LIMIT 1",
        (attempt_id,),
    ).fetchone()


def check_recovery(c: Connection, attempt_id: str, *, fresh_session: bool = False) -> None:
    if c.execute(
        "SELECT 1 FROM attempts a JOIN burst_guests g ON g.bot=a.bot_id AND g.runner_id=a.runner_id "
        "AND g.generation=a.generation WHERE a.id=?",
        (attempt_id,),
    ).fetchone():
        raise Problem("busy", "Wait for the Burst assignment to return to Base before retrying.", 409)
    hold = recovery_hold(c, attempt_id)
    session_held = bool(hold and hold[0] == "session")
    if session_held and not fresh_session:
        raise Problem("continuation_home", "Explicitly request a fresh session to recover this Burst job.", 409)
    if fresh_session and not session_held:
        raise Problem("continuation_home", "This job has no Burst session hold.", 409)


def fresh_recovery(job: Any) -> bool:
    release = _H._json(job["release"], {})  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage] - release can already be decoded
    return isinstance(release, dict) and cast(Row, release).get("burst_fresh_session") is True


def expire_guests(c: Connection, cutoff: str) -> None:
    # Match the borrowed generation and give its runner the full restore window.
    runners = c.execute(
        "UPDATE burst_guests AS g SET return_pending='expired' WHERE return_pending='' AND EXISTS "
        "(SELECT 1 FROM attempts a WHERE a.bot_id=g.bot AND a.runner_id=g.runner_id AND a.generation=g.generation "
        "AND a.state IN ('expired','failed') AND a.finished_at<=?) "
        "AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.bot_id=g.bot AND a.runner_id=g.runner_id "
        "AND a.state IN ('leased','running')) RETURNING runner_id",
        (cutoff,),
    ).fetchall()
    for runner in {row[0] for row in runners}:
        return_guests(c, runner)


def settle(c: Connection, attempt: Any) -> None:
    if borrowed(attempt):
        if c.execute(
            "UPDATE burst_guests SET return_pending='settled' WHERE bot=? AND runner_id=? AND generation=? "
            "AND return_pending=''",
            (attempt["bot_id"], attempt["runner_id"], attempt["generation"]),
        ).rowcount:
            return_guests(c, attempt["runner_id"])


def return_guests(c: Connection, runner: str) -> bool:
    guests = c.execute(
        """SELECT g.bot,coalesce(shared.id,base.id),g.return_pending,g.generation FROM burst_guests g
        LEFT JOIN cloud_machines m ON m.bot='_shared'
        LEFT JOIN runners shared ON shared.id=m.runner_id AND m.state NOT IN ('deleted','failed')
                                AND shared.revoked_at IS NULL AND shared.id<>g.runner_id
        LEFT JOIN runners base ON base.id=g.base_runner AND base.revoked_at IS NULL AND m.bot IS NULL
        WHERE g.runner_id=? AND g.return_pending<>''
          AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.bot_id=g.bot AND a.runner_id=g.runner_id
                          AND a.state IN ('leased','running'))""",
        (runner,),
    ).fetchall()
    for bot, home, pending, generation in guests:
        if not home:
            continue
        if pending in ("revoke", "expired"):
            lost = c.execute(
                "SELECT a.id,a.job_id,json_extract(a.result,'$.source_continuation') IS NOT NULL "
                "OR json_extract(a.result,'$.source_pending')=1 AS saved "
                "FROM attempts a JOIN jobs j ON j.attempt_id=a.id AND j.id=a.job_id "
                "WHERE a.bot_id=? AND a.runner_id=? AND a.generation=? AND a.state='expired' "
                "AND (a.started_at IS NOT NULL OR a.writes>0 OR EXISTS "
                "(SELECT 1 FROM attempt_events e WHERE e.attempt_id=a.id AND e.kind='tool')) "
                "AND j.state IN ('queued','uncertain')",
                (bot, runner, generation),
            ).fetchone()
            if lost and not recovery_hold(c, lost["id"]):
                hold_for_recovery(c, lost, checkout="lost", session=bool(lost["saved"]))
                c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (lost["job_id"],))
        updated = c.execute(
            "UPDATE assignments SET runner_id=?,generation=generation+1,updated_at=?,updated_by=? "
            "WHERE bot=? AND runner_id=? RETURNING generation",
            (home, H.now(), H.TICO_ACTOR, bot, runner),
        ).fetchone()
        if updated:
            changed(c, bot, home, updated[0])
        c.execute("DELETE FROM burst_guests WHERE bot=?", (bot,))
    return not c.execute("SELECT 1 FROM burst_guests WHERE runner_id=?", (runner,)).fetchone()


def resume_return(c: Connection, execution: "Execution | None", runner: str, pending: str) -> None:
    if pending == "revoke":
        from . import computers

        if computers.mark_revoked(c, runner, execution=execution):  # pyright: ignore[reportUnknownMemberType] - revocation is untyped
            H.event(c, H.TICO_ACTOR, "runner.revoked", runner)
    elif pending:
        return_guests(c, runner)


def install(app: FastAPI, store: Any, auth: Any, mutate: Any) -> None:
    from . import cloud_machines, events, model_subscriptions

    machines: Any = cloud_machines

    def register(setting: str, slug: str, name: str, operation: str) -> None:
        path = "/api/v2/settings/" + slug
        denied = f"Only a company admin may manage {name}"
        forbidden = {"403": error(denied)}

        def check(c: Connection, who: Identity) -> None:
            auth.sync_access(c)
            if not model_subscriptions._may_manage(auth, who):  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage] - uses the same company settings access rule as plan exits
                raise Problem("forbidden", denied, 403)

        @app.get(
            path,
            **api_v2(
                "Computers", "get" + operation, f"Read company {name}", plan_exits.ChangeRouting, responses=forbidden
            ),
        )
        def get_setting(request: Request) -> dict[str, bool]:
            with store.read() as c:
                check(c, request.state.identity)
                return {"enabled": plan_exits.stored_flags(c, setting).get(setting) == "true"}

        @app.put(
            path,
            **api_v2(
                "Computers",
                "set" + operation,
                f"Set company {name}",
                plan_exits.ChangeRouting,
                responses={
                    **forbidden,
                    **(
                        {"409": error("Burst re-home or the HQ daily budget is missing")}
                        if setting == AUTOSCALE
                        else {}
                    ),
                },
            ),
        )
        def put_setting(request: Request, body: plan_exits.ChangeRouting) -> dict[str, bool]:
            def work(c: Connection) -> dict[str, bool]:
                if setting == AUTOSCALE and body.enabled:
                    if plan_exits.stored_flags(c, SETTING).get(SETTING) != "true":
                        raise Problem("burst_rehome_required", "Enable Burst re-home before autoscaling.", 409)
                    if not machines.burst_budget(c):
                        raise Problem("daily_budget_required", "Configure a daily Burst budget in HQ first.", 409)
                c.execute(
                    "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (setting, "true" if body.enabled else "false"),
                )
                if setting == SETTING:
                    pooled: Any = c  # the store adds after-commit hooks to its pooled connections
                    after_commit: list[Callable[[], None]] = pooled.after_commit
                    after_commit.append(lambda: store.set_burst_rehome(body.enabled))
                    if not body.enabled:
                        c.execute("UPDATE registry_metadata SET value='false' WHERE key=?", (AUTOSCALE,))
                if not body.enabled:
                    machines.drain_managed_burst(c)
                events.tell_runners(  # pyright: ignore[reportUnknownMemberType] - the runner event dispatcher is untyped
                    c, "assignments", "SELECT runner_id AS rid FROM cloud_machines WHERE bot GLOB '_burst:[0-9]*'"
                )
                H.event(c, request.state.identity.actor, "settings." + setting, setting, {"enabled": body.enabled})
                return {"enabled": body.enabled}

            check_write: Callable[[Connection], None] = lambda c: check(c, request.state.identity)
            return mutate(request, body, work, check=check_write)

    register(SETTING, "burst-rehome", "Burst re-home", "BurstRehome")
    register(AUTOSCALE, "burst-autoscale", "Burst autoscaling", "BurstAutoscale")
