"""One prepared Base turn at a time on an idle company Burst runner."""

import hashlib

from fastapi import Request

from clients import harness_manifests

from . import bot_settings as BS
from . import (
    gateway_connection,
    models_preset,
    plan_exits,
    providers,
    readiness,
    runner_online,
    runner_versions,
    task_reads,
)
from .api_contract import api_v2, error
from .harnesses import resolve_harness
from .store import H, Problem, bot_readiness, encode, readiness_document

SETTING = "burst_rehome"
KEY = "_burst:"
PREPARATION = "burst_preparation_v1"
REVOKING = "burst-return:"


def enabled(c):
    return getattr(c, "burst_rehome", False)


def burst_key(key):
    return key.startswith(KEY) and key.removeprefix(KEY).isascii() and key.removeprefix(KEY).isdecimal()


def target(c, runner):
    if not enabled(c) or not runner or runner["revoked_at"]:
        return False
    return any(
        burst_key(row[0])
        for row in c.execute("SELECT bot FROM cloud_machines WHERE runner_id=? AND state='running'", (runner["id"],))
    )


def allowed_payment(c, payment):
    # Cloud plans currently run directly. A selected personal exit would be bypassed there.
    return payment is None or (
        payment.subscription is not None and not plan_exits.routed(c, payment.subscription, require_ready=False)
    )


def offer(c, executor, runner, *, only=None, skip=(), prepare=False):
    from . import cloud_machines, execution, relationship_runs
    from .models import Claim
    from .worktrees import WORKTREE_OWNER

    if (
        (prepare and not target(c, runner))
        or PREPARATION not in H._json(runner["capabilities"], [])
        or not runner_online.is_online(runner["last_seen_at"])
    ):
        return None
    rid = runner["id"]
    if (
        any(execution.claimed_busy(rid))
        or c.execute(
            "SELECT 1 FROM attempts WHERE runner_id=? AND state IN ('leased','running') LIMIT 1", (rid,)
        ).fetchone()
    ):
        return None
    if prepare:
        # Preparing on one slot avoids cloning the same checkout across idle workers.
        slots = c.execute(
            "SELECT m.bot,m.runner_id FROM cloud_machines m JOIN runners_with_state r ON r.id=m.runner_id "
            "WHERE m.state='running' AND r.revoked_at IS NULL AND r.owner_id=? AND r.last_seen_at>? "
            "AND EXISTS (SELECT 1 FROM json_each(r.capabilities) WHERE value=?) "
            "AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.runner_id=r.id AND a.state IN ('leased','running')) "
            "ORDER BY CAST(substr(m.bot,8) AS INTEGER),m.bot",
            (runner["owner_id"], runner_online.cutoff(), PREPARATION),
        )
        for key, slot in slots:
            if burst_key(key) and not any(execution.claimed_busy(slot)) and not cloud_machines.held(c, slot):
                if slot != rid:
                    return None
                break
        else:
            return None
    base = c.execute(
        "SELECT r.* FROM cloud_machines m JOIN runners_with_state r ON r.id=m.runner_id "
        "WHERE m.bot=? AND r.revoked_at IS NULL",
        (cloud_machines.SHARED,),
    ).fetchone()
    if not base or runner["owner_id"] != executor.auth.owner_id(c):
        return None
    bots = {
        row["bot"]: row
        for row in c.execute(
            f"""SELECT a.bot,a.runner_id,b.slug,{BS.CONFIG} AS config
        FROM assignments a
        JOIN cloud_machines p ON p.bot=a.bot AND p.mode='cloud' AND p.placement='shared'
        JOIN bots b ON b.id=a.bot AND b.state='active'
        JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN}
        WHERE a.runner_id=? AND a.bot<>?
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
            (base["id"], relationship_runs.BOT),
        )
    }
    document = readiness.local_sign_ins(readiness_document(runner["readiness"]))
    if not prepare:
        bots = {bot: row for bot, row in bots.items() if bot_readiness(document, bot).get("burst_preparation")}
    if not bots:
        return None

    def accept(row):
        entry = _offer(c, executor.store.settings, runner, document, {**dict(row), **dict(bots[row["bot_id"]])})
        if entry and (prepare or ready(runner, entry)):
            return entry if prepare else {**dict(row), "burst": entry}
        return None

    return executor._candidate(
        c,
        Claim(bot=only),
        runner,
        None,
        False,
        None,
        skip,
        base["id"],
        eligible=bots,
        accept=accept,
        preparing=prepare,
    )


def _offer(c, settings, runner, document, job):
    from . import browser_commands, execution, inbox_isolation
    from .shared_bots import follow, source_of
    from .views import roster

    rid = runner["id"]
    bot, base = job["bot_id"], job["runner_id"]
    if (
        not execution.source_idle(c, base, bot)
        or fresh_recovery(job)
        or execution.continuation_run(c, job["id"])
        or H.folder_clash(c, bot, rid)
        or browser_commands.host_id(c) in (base, rid)
        or {base, rid} & inbox_isolation.inbox_only(c, roster(c))
    ):
        return None
    declared = H._json(job["config"], {}) or {}
    if source_of(declared) or execution._reported(c, base, bot)[1].get("published") is not True:
        return None
    if not (repo := execution.runner_repository(c, settings, bot)):
        return None
    message = H.message(c, job["message_id"])
    room = H.conversation(c, message["conversation_id"]) if message else None
    if not room or not execution.job_message_readable(c, bot, message):
        return None
    if (
        execution.work_task_id(message, room)
        or {"live_meeting", "call_prep", "voice_call", "voice_reply"} & message["refs"].keys()
    ):
        return None  # Task lanes can create a local worktree.
    followed = follow(c, bot, execution.assignment_name(declared, bot, job["slug"]))
    record = providers.load(c, settings)
    checkout = runner_versions.wire_template(providers.fill(record, followed))
    config, payment = execution.queued_payer(c, settings, runner, job, followed, message, room, None, None)
    if not allowed_payment(c, payment) or not config.get("runtime"):
        return None
    if payment is None:
        config = models_preset.main_config(record, document, config)
    harness = resolve_harness(config)
    runtime = config["runtime"]
    gateway = gateway_connection.is_tico(config.get("model"))
    state = readiness.computer_login(document, runtime)
    login_ready = (
        models_preset.runs_gateway(document)
        if gateway
        else (payment is not None or state.get("authenticated") in ("ready", True))
    )
    if not tools_ready(document, runtime, harness) or not login_ready:
        return None
    ticket = hashlib.sha256(
        encode([rid, base, job["generation"], bot, repo, checkout, config["runtime"], harness]).encode()
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
    }


def ready(runner, entry, config=None):
    document = readiness_document(runner["readiness"])
    check = bot_readiness(document, entry["bot"])
    runtime, harness = entry["runtime"], entry["harness"]
    if config is not None and (config.get("runtime"), resolve_harness(config)) != (runtime, harness):
        return False
    checkout = entry["config"]
    return (
        check.get("burst_preparation") == entry["burst_preparation"]
        and check.get("repository_present") is True
        and check.get("configuration_valid") is True
        and (check.get("runtime"), check.get("harness")) == (checkout.get("runtime"), resolve_harness(checkout))
        and tools_ready(document, runtime, harness)
    )


def tools_ready(document, runtime, harness):
    manifest = (harness_manifests.get(harness) or {}).get("manifest") or harness
    tool = document.get("harnesses", {}).get(manifest) or {}
    return (
        tool.get("installed") is True
        and tool.get("state") not in ("installing", "updating")
        and (document.get("runtimes", {}).get(runtime) or {}).get("installed") is True
    )


def move(c, entry, generation):
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


def changed(c, bot, runner, generation):
    from . import built_on

    built_on.forget(c, bot)
    task_reads.status_moved(c, [bot])
    H.event(c, H.TICO_ACTOR, "bot.assigned", bot, {"runner": runner, "generation": generation})


def borrowed(attempt):
    return (H._json(attempt["wake"], {}) or {}).get("burst") is True


def hold_for_recovery(c, attempt, *, checkout=None, session=False):
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


def recovery_hold(c, attempt_id):
    return c.execute(
        "SELECT json_extract(detail,'$.burst') FROM events WHERE target=? AND action='job.held' "
        "AND json_extract(detail,'$.burst') IS NOT NULL LIMIT 1",
        (attempt_id,),
    ).fetchone()


def check_recovery(c, attempt_id, *, fresh_session=False):
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


def fresh_recovery(job):
    release = H._json(job["release"], {})
    return isinstance(release, dict) and release.get("burst_fresh_session") is True


def expire_guests(c, cutoff):
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


def settle(c, attempt):
    if borrowed(attempt):
        if c.execute(
            "UPDATE burst_guests SET return_pending='settled' WHERE bot=? AND runner_id=? AND generation=? "
            "AND return_pending=''",
            (attempt["bot_id"], attempt["runner_id"], attempt["generation"]),
        ).rowcount:
            return_guests(c, attempt["runner_id"])


def return_guests(c, runner):
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


def resume_return(c, execution, runner, pending):
    if pending == "revoke":
        from .computers import mark_revoked

        if mark_revoked(c, runner, execution=execution):
            H.event(c, H.TICO_ACTOR, "runner.revoked", runner)
    elif pending:
        return_guests(c, runner)


def install(app, store, auth, mutate):
    from .events import tell_runners
    from .model_subscriptions import _may_manage

    path = "/api/v2/settings/burst-rehome"
    forbidden = {"403": error("Only a company admin may manage Burst re-home")}

    def check(c, who):
        auth.sync_access(c)
        if not _may_manage(auth, who):
            raise Problem("forbidden", "Only a company admin may manage Burst re-home", 403)

    @app.get(
        path,
        **api_v2(
            "Computers", "getBurstRehome", "Read company Burst re-home", plan_exits.ChangeRouting, responses=forbidden
        ),
    )
    def get_setting(request: Request):
        with store.read() as c:
            check(c, request.state.identity)
            return {"enabled": plan_exits.stored_flags(c, SETTING).get(SETTING) == "true"}

    @app.put(
        path,
        **api_v2(
            "Computers", "setBurstRehome", "Set company Burst re-home", plan_exits.ChangeRouting, responses=forbidden
        ),
    )
    def put_setting(request: Request, body: plan_exits.ChangeRouting):
        def work(c):
            c.execute(
                "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SETTING, "true" if body.enabled else "false"),
            )
            c.after_commit.append(lambda: store.set_burst_rehome(body.enabled))
            tell_runners(c, "assignments", "SELECT runner_id AS rid FROM cloud_machines WHERE bot GLOB '_burst:[0-9]*'")
            H.event(c, request.state.identity.actor, "settings.burst_rehome", SETTING, {"enabled": body.enabled})
            return {"enabled": body.enabled}

        return mutate(request, body, work, check=lambda c: check(c, request.state.identity))
