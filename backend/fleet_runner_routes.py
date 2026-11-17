"""Computer, placement, runner, agent sync and attempt routes."""

from . import built_on
from . import runner_online
from . import org_rule_check
from . import placement as placing
from .auth import Identity
from .execution import runner_repository, reset_bot_sessions
from . import fleet_check as fleet_check_module
from .screen import listing as screen_listing
from .performance import listing as performance_listing

from . import bot_settings as BS
from .execution import ClaimFaults
from .store import H
from . import models as M
from .store import P
from pathlib import Path
from fastapi.responses import PlainTextResponse
from .store import Problem
from fastapi import Request
from . import agents
from . import bot_settings_routes
from .store import encode
from . import external_sync
from . import go_live_routes
from . import personal_tokens
from . import task_privacy as privacy
from . import rooms
from .credential_cards import scrub_attempt
from . import start_places
from . import teammate_access
from .auth import validate_identity
from . import views
from .api_contract import api_v2


def register(
    app,
    admin_only,
    auth,
    closed_computer,
    computer_rows,
    delegated_identity,
    execution,
    history_on_github,
    mutate,
    onboarding,
    place_now,
    placement_summary,
    propose_card,
    risky,
    send,
    settings,
    settings_admin,
    store,
    telemetry,
    reset_bot_sessions,
):
    @app.get("/api/v2/computers")
    def computers(request: Request):
        """The computers a person may put a bot on; company bots can discover every computer's load.
        Newly discoverable computers expose no repositories, logs or operator identity."""
        who = request.state.identity
        auth.domain(who)
        # One snapshot, so the per-computer lookups share read_cache across every computer.
        with store.read_transaction() as c:
            return {
                "computers": computer_rows(c, who),
                "services": views.team_services(c, who, auth),
                "services_scope": "team",
            }

    @app.post("/api/v2/bots/{bot}/place")
    def place_bot(request: Request, bot: str, body: M.BotPlace):
        who = request.state.identity
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        history = history_on_github(bot)
        return mutate(request, body, lambda c: place_now(c, who, bot, body.computer, history))

    go_live_routes.register(
        app,
        auth,
        execution,
        history_on_github,
        mutate,
        onboarding,
        place_now,
        send,
        settings,
        settings_admin,
        store,
        telemetry,
    )

    @app.post(
        "/api/v2/computers/{rid}/turns-at-once",
        **api_v2(
            "Computers",
            "setComputerTurnsAtOnce",
            "Set the most turns a computer runs at once: null is automatic (its own capacity)",
        ),
    )
    def runner_turns_at_once(request: Request, rid: str, body: M.RunnerTurnsAtOnce):
        """How many turns a computer runs at once; null is automatic, its own capacity. Owners and admins."""
        who = request.state.identity

        def work(c):
            admin_only(who, "sets how many turns a computer runs at once")
            row = c.execute("SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not row:
                raise Problem("not_found", "Computer not found", 404)
            c.execute("UPDATE runners SET turns_at_once=? WHERE id=?", (body.turns_at_once, rid))
            # The computer's next heartbeat reads the new limit (runner-rs events.rs: a "capacity" change).
            c.execute(
                "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('runner','capacity',?,?)", (rid, H.now())
            )
            H.event(
                c, who.actor, "runner.turns_at_once", rid, {"before": row["turns_at_once"], "after": body.turns_at_once}
            )
            return {
                "turns_at_once": body.turns_at_once,
                "turn_limit": start_places.turn_limit({**dict(row), "turns_at_once": body.turns_at_once}),
            }

        return mutate(request, body, work)

    @app.post(
        "/api/v2/computers/{rid}/label",
        **api_v2("Computers", "renameComputer", "Rename a computer: 1 to 100 characters, trimmed; owners and admins"),
    )
    def runner_label(request: Request, rid: str, body: M.RunnerLabel):
        """Rename a computer. A cloud machine made again keeps the name. Owners and admins."""
        from . import cloud_machines
        from .execution import rename

        who = request.state.identity

        def work(c):
            admin_only(who, "renames a computer")
            row = c.execute("SELECT label FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not row:
                raise Problem("not_found", "Computer not found", 404)
            if row["label"] != body.label:
                rename(c, rid, row["label"], body.label)
                cloud_machines.named(c, rid, body.label)
                # Its bots' assignments carry the computer's name (computer_label): the runner reads them again.
                c.execute(
                    "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('runner','assignments',?,?)",
                    (rid, H.now()),
                )
                H.event(c, who.actor, "runner.renamed", rid, {"before": row["label"], "after": body.label})
            return {"label": body.label}

        return mutate(request, body, work)

    @app.post("/api/v2/computers/{rid}/member-bots")
    def runner_member_bots(request: Request, rid: str, body: M.RunnerMemberBots):
        """Whether a computer takes the bots members create. Owners and admins."""
        who = request.state.identity

        def work(c):
            admin_only(who, "says which computers take members' bots")
            if not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone():
                raise Problem("not_found", "Computer not found", 404)
            c.execute("UPDATE runners SET accepts_member_bots=? WHERE id=?", (int(body.accepts), rid))
            H.event(c, who.actor, "runner.member_bots", rid, {"accepts": body.accepts})
            return {"accepts_member_bots": body.accepts}

        return mutate(request, body, work)

    @app.post("/api/v2/computers/{rid}/gateway-fallback")
    def runner_gateway_fallback(request: Request, rid: str, body: M.RunnerGatewayFallback):
        """Whether this computer's turns run on Tico Gateway when its plans run out (backend/models_preset.py
        computer_falls_back). Off by default; owners and admins turn it on."""
        who = request.state.identity

        def work(c):
            admin_only(who, "says which computers fall back to Tico Gateway")
            if not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone():
                raise Problem("not_found", "Computer not found", 404)
            c.execute("UPDATE runners SET gateway_fallback=? WHERE id=?", (int(body.on), rid))
            H.event(c, who.actor, "runner.gateway_fallback", rid, {"on": body.on})
            return {"gateway_fallback": body.on}

        return mutate(request, body, work)

    # An external agent (a Hermes profile) is a bot with a standing credential instead of a
    # computer: a person mints it here, the agent's box heartbeats with it, and the same token
    # is the bearer for the MCP endpoint and the `tico` CLI (docs/hermes-agents.md).
    @app.post("/api/v2/bots/{bot}/agent-credential")
    def agent_credential(request: Request, bot: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            personal_tokens.no_minting(who, "Create an agent credential")
            settings_admin._manager(c, who, bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            issued = agents.issue_credential(c, who, bot)
            if who.via == "botops":
                # A token never reaches BotOps's model. The old one has stopped; the profile pairs again and the
                # person's approval (BotOps can give it) hands the new one to the profile.
                return {
                    "bot": bot,
                    "harness": issued["harness"],
                    "created_at": issued["created_at"],
                    "rotated": True,
                    "detail": "The previous credential has stopped working. The token is not shown here: run "
                    "`python3 hermes_agent.py pair` on the profile's computer and approve its code.",
                }
            # Machine credentials bypass the person sign-in at the runner hostname, the same
            # address runners enroll at; the public one would answer with the login page.
            return {
                **issued,
                "setup": agents.setup_snippet(settings.runner_url, bot, issued["token"], issued["harness"]),
            }

        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/agent-credential/revoke")
    def agent_credential_revoke(request: Request, bot: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            settings_admin._manager(c, who, bot)
            return agents.revoke_credential(c, who, bot)

        return mutate(request, body, work)

    # A person's bots on another platform (Grok Bot, Dots), synced in by the agent itself with the
    # person's own sign-in: created under them on the chart, transcripts copied, instructions kept.
    def external_sync_run(request, body):
        # Images are fetched before the write, never while holding it, and only for a sync that would be accepted.
        with store.read() as c:
            external_sync.precheck(c, settings_admin, request.state.identity, body)
        images = external_sync.prefetch_images(
            store, app.state.blobs, body, person=H.actor_id(request.state.identity.actor)
        )
        return mutate(
            request, body, lambda c: external_sync.sync(c, auth, settings_admin, request.state.identity, body, images)
        )

    @app.post("/api/v2/external/sync")
    def external_agent_sync(request: Request, body: external_sync.ExternalSync):
        return external_sync_run(request, body)

    @app.post("/api/v2/grokbot/sync")  # older Grok routines
    def grokbot_sync(request: Request, body: external_sync.ExternalSync):
        return external_sync_run(request, body.model_copy(update={"provider": "grokbot"}))

    @app.post("/api/v2/agents/heartbeat")
    def agent_heartbeat(request: Request, body: M.AgentHeartbeat):
        return mutate(request, body, lambda c: agents.heartbeat(c, request.state.identity, body))

    @app.get("/api/v2/agents/setup-script", response_class=PlainTextResponse)
    def agent_setup_script(request: Request):
        """The one-file connector the agent's box downloads (clients/hermes_agent.py). Source available, holds no
        secret, and needs no sign-in: a profile with no credential yet fetches it to pair."""
        return (Path(__file__).resolve().parents[1] / "clients" / "hermes_agent.py").read_text()

    @app.get("/api/v2/agents/sync-skill", response_class=PlainTextResponse)
    def agent_sync_skill():
        """The "Tico sync" skill (skills/tico-sync/SKILL.md) the connector installs next to the agent and refreshes on
        `update`. Source available, no secret, no sign-in: the same door as the connector."""
        return (Path(__file__).resolve().parents[1] / "skills" / "tico-sync" / "SKILL.md").read_text()

    def client_address(request):
        from .client_ip import client_ip, ip_bucket

        return ip_bucket(client_ip(request, settings))

    # Pairing (docs/hermes-agents.md): the first two calls need no sign-in; approving and declining are a person's,
    # or BotOps's as the person who asked it.
    @app.post("/api/v2/agents/pairings", status_code=201)
    def agent_pairing_create(request: Request, body: M.AgentPairingCreate):
        execution.refuse_in_rehearsal("Agent pairings")
        with store.transaction() as c:
            return agents.create_pairing(c, body, client_address(request))

    @app.get("/api/v2/agents/pairings/{pairing_id}")
    def agent_pairing_poll(request: Request, pairing_id: str):
        with store.transaction() as c:
            result = agents.poll_pairing(c, pairing_id, request.headers.get("x-pairing-secret", ""))
        return {**result, "url": settings.runner_url} if result["state"] == "approved" else result

    @app.get("/api/v2/agents/pairing-preview")
    def agent_pairing_show(request: Request, code: str):
        with store.read() as c:
            return agents.show_pairing(c, request.state.identity, code)

    @app.post("/api/v2/agents/pairings/approve")
    def agent_pairing_approve(request: Request, body: M.AgentPairingApprove):
        who = request.state.identity
        personal_tokens.no_minting(who, "Approve a pairing")
        execution.refuse_in_rehearsal("Agent pairings")
        return mutate(
            request, body, lambda c: agents.approve_pairing(c, who, settings_admin._manager, body.code, body.bot)
        )

    @app.post("/api/v2/agents/pairings/decline")
    def agent_pairing_decline(request: Request, body: M.AgentPairingDecline):
        who = request.state.identity
        return mutate(request, body, lambda c: agents.decline_pairing(c, who, body.code))

    @app.post("/api/v2/bots/{bot}/owners")
    def owners(request: Request, bot: str, body: M.BotOwners):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            settings_admin._manager(c, who, bot)
            config = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
            if not config:
                raise Problem("not_found", "Bot not found", 404)
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before saving owners", 409)
            owner_ids = list(dict.fromkeys(body.owners))
            rows = [H.human(c, owner) for owner in owner_ids]
            missing = [owner for owner, row in zip(owner_ids, rows, strict=False) if not row]
            if missing:
                raise Problem("not_found", "Unknown person: " + ", ".join(missing), 404)
            before = settings_admin.snapshot(c, bot, "owners")
            c.execute("UPDATE bot_config SET owner_ids=?,revision=revision+1 WHERE bot=?", (encode(owner_ids), bot))
            if rooms.thread_mode(c, bot) == rooms.SHARED:
                rooms.sync_shared_room(c, auth, bot)
            settings_admin.record(c, who.actor, bot, "owners", before, owner_ids)
            H.event(c, who.actor, "bot.owners_changed", bot, {"owners": owner_ids})
            return {"bot": bot, "owners": [P.brief(row) for row in rows], "revision": config["revision"] + 1}

        return mutate(request, body, work)

    @app.get(
        "/api/v2/bots/{bot}/access",
        **api_v2(
            "Bots",
            "getBotAccess",
            "Who may see, read and write to a bot (its managers only; docs/permissions.md)",
            "BotAccessView",
        ),
    )
    def bot_access_read(request: Request, bot: str, on_behalf_of: str = ""):
        """Who may see, read and write to this bot (docs/permissions.md). For the people who manage it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if on_behalf_of:
                who = delegated_identity(c, who, on_behalf_of)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return settings_admin.access(c, who, bot)

    @app.post("/api/v2/bots/{bot}/access")  # the same write for the tools, whose client only posts
    @app.put(
        "/api/v2/bots/{bot}/access",
        **api_v2(
            "Bots",
            "setBotAccess",
            "Set who may see, read and write to a bot; send the revision you read (409 version_conflict otherwise)",
            "BotAccessView",
        ),
    )
    def bot_access_write(request: Request, bot: str, body: M.BotAccess):
        caller = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return settings_admin.set_access(c, who, bot, body)

        return mutate(request, body, work)

    # A person's and a group's See, Read and Write, the bots' shape (backend/teammate_access.py, docs/permissions.md).
    # Whoever manages them reads and changes it: the person themselves and whoever they report up to, a group's leads,
    # the admins and the owner. BotOps does it as the person who asked.
    def teammate_access_read(request, target, on_behalf_of=""):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if on_behalf_of:
                who = delegated_identity(c, who, on_behalf_of)
            return teammate_access.view(c, who, target, auth)

    def teammate_access_write(request, target, body):
        caller = request.state.identity

        def work(c):
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            return teammate_access.save(
                c,
                who,
                target,
                body.model_dump(exclude={"on_behalf_of"}),
                auth,
                via="botops" if who.via == "botops" else None,
            )

        return mutate(request, body, work)

    @app.get(
        "/api/v2/humans/{pid}/access",
        **api_v2(
            "Team chart",
            "getPersonAccess",
            "Who may see, read and write to a person (themselves, whoever they report up to, admins; docs/permissions.md)",
            "TeammateAccessView",
        ),
    )
    def person_access_read(request: Request, pid: str, on_behalf_of: str = ""):
        """Who may see, read and write to this person (docs/permissions.md). For whoever manages them."""
        return teammate_access_read(request, "human:" + pid, on_behalf_of)

    @app.post("/api/v2/humans/{pid}/access")  # the same write for the tools, whose client only posts
    @app.put(
        "/api/v2/humans/{pid}/access",
        **api_v2(
            "Team chart",
            "setPersonAccess",
            "Set who may see, read and write to a person; send the revision you read (409 version_conflict otherwise)",
            "TeammateAccessView",
        ),
    )
    def person_access_write(request: Request, pid: str, body: M.TeammateAccess):
        return teammate_access_write(request, "human:" + pid, body)

    @app.get(
        "/api/v2/groups/{gid}/access",
        **api_v2(
            "Team chart",
            "getGroupAccess",
            "Who may see, read and write to a group (its leads and admins; docs/permissions.md)",
            "TeammateAccessView",
        ),
    )
    def group_access_read(request: Request, gid: str, on_behalf_of: str = ""):
        """Who may see, read and write to this group (docs/permissions.md). For its leads and the admins."""
        return teammate_access_read(request, "group:" + gid, on_behalf_of)

    @app.post("/api/v2/groups/{gid}/access")
    @app.put(
        "/api/v2/groups/{gid}/access",
        **api_v2(
            "Team chart",
            "setGroupAccess",
            "Set who may see, read and write to a group; send the revision you read (409 version_conflict otherwise)",
            "TeammateAccessView",
        ),
    )
    def group_access_write(request: Request, gid: str, body: M.TeammateAccess):
        return teammate_access_write(request, "group:" + gid, body)

    @app.post("/api/v2/bots/register")
    def bot_register(request: Request, body: M.BotRegister):
        """Register a bot with the server (planned), as the person asking: the record BotOps then builds the
        repository for. They become its owner. Needs `create_bots` (or Admin), and stays within the limit of
        active bots each member may have. Registering a slug you already own answers with it (`created: false`)."""
        caller = request.state.identity

        def work(c):
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if who.role not in ("owner", "human"):
                raise Problem("forbidden", "Only people register bots", 403)
            created = settings_admin.register(c, who, body)
            if body.build and body.template:
                made_bot = H.resolve_bot(c, created["slug"])
                assert made_bot is not None  # settings_admin.register just made it
                bot_id = made_bot["id"]
                current = onboarding._declared(c, bot_id)
                if not created["created"] and current.get("template") != body.template:
                    raise Problem("template", "Change the bot's template in its definition before building it", 409)
                created.update(
                    onboarding.attach_template(
                        c,
                        who,
                        bot_id,
                        body.template,
                        body.instructions,
                        queue_build=True,
                        title_prefix=body.title_prefix,
                    )
                )
            return created

        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/co-owners")
    def bot_co_owners(request: Request, bot: str, body: M.BotCoOwners):
        """Add or remove people who own a bot. Any of its owners may; whoever it reports up to and the Admins
        are owners without being listed."""
        caller = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return settings_admin.co_owners(c, who, bot, body.add, body.remove)

        return mutate(request, body, work)

    bot_settings_routes.register(
        app,
        admin_only,
        auth,
        closed_computer,
        delegated_identity,
        execution,
        mutate,
        placement_summary,
        propose_card,
        risky,
        settings,
        settings_admin,
        store,
        reset_bot_sessions,
    )

    @app.post("/api/v2/jobs/claim")
    def claim(request: Request, body: M.Claim):
        # A read first: an idle runner asks every fraction of a second and must not take the write lock.
        with store.read() as c:
            if body.bot:
                # The runner names a bot by its id; a slug (an older runner, or a person's command) means the same bot.
                body = body.model_copy(update={"bot": (H.resolve_bot(c, body.bot) or {}).get("id", body.bot)})
            selected, failed = [], ClaimFaults()
            idle = execution.idle_claim(
                c, request.state.identity, body, request.headers.get("idempotency-key"), selected, failed
            )
            if idle is not None:
                return idle
            # Only a registered computer's claim cleans up: anyone else is refused before any write.
            due = execution.runner(c, request.state.identity) is not None and execution.maintenance_due(
                c, request.state.identity
            )
            if c.in_transaction:
                c.rollback()
        if due:
            # Lapsed leases go back in the queue in a short write of their own, before the read below chooses: the
            # claim's write then rechecks one job instead of choosing again under the lock (TK-INSRN53V).
            store.write(request.state.identity, execution.maintain)
            selected.clear()
        with store.read() as c:
            # A job whose checks raised on this snapshot is held by the write below, which takes the next candidate.
            if not selected and not failed:
                selected.append(
                    execution.candidate(
                        c, request.state.identity, body, execution.runner(c, request.state.identity), failed=failed
                    )
                )
            # End any transaction opened during selection before taking the writer lock. The selected
            # job is checked against current policy in that transaction.
            if c.in_transaction:
                c.rollback()

            def work(write):
                if not selected:
                    return execution.claim(write, request.state.identity, body, maintained=True, failed=failed)
                return execution.claim(
                    write,
                    request.state.identity,
                    body,
                    selected=selected[0],
                    prepared=True,
                    maintained=True,
                    failed=failed,
                )

            return mutate(request, body, work)

    @app.post("/api/v2/attempts/{aid}/renew")
    def renew(request: Request, aid: str, body: M.Empty):
        with execution.calling(request.state.identity, aid) as arrived:
            if 0 < len(request.headers.get("idempotency-key", "")) <= 200:
                with store.read_transaction() as c:
                    validate_identity(c, request.state.identity)
                    fresh = (
                        None
                        if store.saved(
                            c, request.state.identity, request.url.path, request.headers.get("idempotency-key")
                        )
                        else execution.renew(c, request.state.identity, aid, if_fresh=True)
                    )
                    if fresh is not None:
                        return fresh
            return mutate(request, body, lambda c: execution.renew(c, request.state.identity, aid, arrived=arrived))

    @app.post("/api/v2/attempts/{aid}/start-place")
    def start_place(request: Request, aid: str, body: M.StartPlace):
        return mutate(request, body, lambda c: execution.start_place(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/started")
    def started(request: Request, aid: str, body: M.Started):
        from . import automations  # the turn's start acts for the turn (automations.for_attempt)

        def begin(c):
            with automations.for_attempt(c, aid):
                return execution.started(c, request.state.identity, aid, body)

        out = mutate(request, body, begin)
        # A refused start is kept with the write (execution.refuse_start), then answered as the wait it is.
        if isinstance(out, dict) and out.get("refused"):
            raise Problem(out["refused"]["code"], out["refused"]["detail"], 409)
        return out

    @app.post("/api/v2/attempts/{aid}/thread")
    def attempt_thread(request: Request, aid: str, body: M.AttemptThread):
        return mutate(request, body, lambda c: execution.thread(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/events")
    def events(request: Request, aid: str, body: M.EventBatch):
        with execution.calling(request.state.identity, aid):
            body = scrub_attempt(aid, body)  # a secret set in this run never reaches the record
            # Warm neutral provenance in a read snapshot. The transaction checks current
            # permissions again, and only reuses this graph while its source clock matches.
            with store.read_transaction() as c:
                if not store.saved(c, request.state.identity, request.url.path, request.headers.get("idempotency-key")):
                    execution.attempt(c, request.state.identity, aid, active=False)
            from . import skill_runtime

            def ingest(c):
                before = skill_runtime.last_seq(c, aid)  # events after it are new: a resent batch counts nothing
                acked = execution.events(c, request.state.identity, aid, body)
                skill_runtime.record_runner_usage(
                    c, aid, body.events, after_seq=before
                )  # a Skill call's name, scrubbed above
                return acked

            return mutate(request, body, ingest)

    @app.post("/api/v2/attempts/{aid}/capture")
    def keep_capture(request: Request, aid: str, body: M.StreamCapture):
        from . import stream_capture

        body = scrub_attempt(aid, body)  # a secret set in this run never reaches the record
        return mutate(
            request,
            body,
            lambda c: stream_capture.keep(c, execution.attempt(c, request.state.identity, aid, active=False), body),
        )

    @app.post("/api/v2/attempts/{aid}/diagnostics")
    def attempt_diagnostics(request: Request, aid: str, body: M.AttemptStderr):
        from . import run_diagnostics

        body = scrub_attempt(aid, body)
        return mutate(
            request,
            body,
            lambda c: run_diagnostics.keep(c, execution.attempt(c, request.state.identity, aid, active=False), body),
        )

    @app.get("/api/v2/attempts/{aid}/capture")
    def export_capture(request: Request, aid: str, full: bool = True):
        from . import stream_capture

        with store.read() as c:
            return stream_capture.export(c, request.state.identity, auth, aid, full=full)

    @app.post("/api/v2/attempts/{aid}/inputs")
    def inputs(request: Request, aid: str, body: M.Empty):
        key = request.headers.get("idempotency-key", "")
        if 0 < len(key) <= 200:
            with store.read_transaction() as c:
                validate_identity(c, request.state.identity)
                if not c.execute(
                    "SELECT 1 FROM idempotency WHERE binding IS NULL AND actor=? AND operation=? AND key=?",
                    (request.state.identity.actor, request.url.path, key),
                ).fetchone():
                    idle = execution.idle_inputs(c, request.state.identity, aid)
                    if idle is not None:
                        privacy.require_payload(c, request.state.identity, idle)
                        return idle
        return mutate(request, body, lambda c: execution.inputs(c, request.state.identity, aid))

    @app.post("/api/v2/attempts/{aid}/inputs/{mid}/ack")
    def input_ack(request: Request, aid: str, mid: str, body: M.Empty):
        return mutate(request, body, lambda c: execution.acknowledge_input(c, request.state.identity, aid, mid))

    @app.post("/api/v2/attempts/{aid}/background-done")
    def background_done(request: Request, aid: str, body: M.BackgroundDone):
        body = scrub_attempt(aid, body)  # a secret set in this run never reaches the wake
        return mutate(request, body, lambda c: execution.background_done(c, request.state.identity, aid, body))


def register_placement(
    admin_only,
    app,
    auth,
    delegated_identity,
    execution,
    mutate,
    onboarding,
    propose_card,
    risky,
    send,
    settings,
    settings_admin,
    store,
    task_view,
    telemetry,
):
    def closed_computer(c, who, bot, runner_id):
        """An admin placing a member's bot on a computer that is neither the member's own nor open to members'
        bots: allowed, but through BotOps only with the admin's own click."""
        runner = c.execute(
            "SELECT owner_id,accepts_member_bots FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)
        ).fetchone()
        config = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone()
        return bool(
            runner
            and config
            and not runner["accepts_member_bots"]
            and runner["owner_id"] != config["owner_id"]
            and auth.member_bot(c, bot)
            and auth.bot_admin(who)
        )

    def placement_summary(c, bot, runner_id):
        runner = c.execute("SELECT label,accepts_member_bots FROM runners WHERE id=?", (runner_id,)).fetchone()
        return (
            "Place "
            + ((H.bot(c, bot) or {}).get("display_name") or bot)
            + " on "
            + (runner["label"] if runner else "a computer")
            + (
                ", a computer that takes members' bots"
                if runner and runner["accepts_member_bots"]
                else ", a computer that does not take members' bots"
            )
        )

    @app.post("/api/v2/bots/{bot}/assignment")
    def assign(request: Request, bot: str, body: M.Assignment):
        caller = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if risky(who) and closed_computer(c, who, bot, body.runner_id):
                return propose_card(
                    c,
                    who,
                    "POST",
                    "/api/v2/bots/" + bot + "/assignment",
                    body.model_dump(exclude={"on_behalf_of"}),
                    placement_summary(c, bot, body.runner_id),
                )
            return execution.assign(c, who, bot, body)

        return mutate(request, body, work)

    def find_computer(c, name):
        """A computer by id or label (any case), or a refusal that lists the ones there are."""
        rows = c.execute("SELECT id,label FROM runners WHERE revoked_at IS NULL ORDER BY label").fetchall()
        hit = [r for r in rows if r["id"] == name or r["label"].strip().lower() == str(name).strip().lower()]
        if len(hit) != 1:
            raise Problem(
                "not_found",
                ("No computer is called " + name if not hit else "More than one computer is called " + name)
                + ". The computers: "
                + (", ".join(r["label"] for r in rows) or "none yet"),
                404,
            )
        return hit[0]

    def history_on_github(bot):
        """Before the write (GitHub is never called while holding it): for a never-placed bot built on a computer,
        whether GitHub holds its history yet (True, False, or None when it cannot be learned). None for any other bot."""
        with store.read() as c:
            if not built_on.computer(c, bot):
                return None
            repository = runner_repository(c, settings, bot)  # "" when Tico does not use it: history is only there
        service = getattr(app.state, "github_app", None)
        return service.repository_history(repository) if service and repository else None

    def place_now(c, who, bot, computer="", history=None):
        """`tico bot place`: on the computer named, or the one the company's own rule picks (backend/placement.py).
        A new bot whose history is only on the computer that built it goes there first (`history` is not True): that
        computer publishes it to GitHub, and from then on it can move like any other."""
        settings_admin._manager(c, who, bot)
        row = H.bot(c, bot)
        if not row:
            raise Problem("not_found", "Bot not found", 404)
        current = c.execute(
            "SELECT a.*,r.label FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?", (bot,)
        ).fetchone()
        wanted = find_computer(c, computer) if computer else None
        if current and (not wanted or wanted["id"] == current["runner_id"]):
            return {"bot": bot, "computer": current["label"], "placed": False, "already": True}
        from .agents import external_harness

        if external_harness(c, bot):
            raise Problem("harness", "This bot is run by an external agent; it has no computer to place it on", 422)
        built = built_on.computer(c, bot) if history is not True else None
        if built and wanted and wanted["id"] != built["id"]:
            raise Problem(
                "history_elsewhere",
                f"{row['slug']}'s repository was built on {built['label']} and "
                + ("Tico could not check GitHub's copy" if history is None else "is not on GitHub yet")
                + f", so {wanted['label']} could not get it. Put it on {built['label']} first (`tico bot go-live {row['slug']} --computer "
                f"\"{built['label']}\"`): {built['label']} publishes it to GitHub, and then it can move",
                409,
            )
        options = [wanted] if wanted else [built] if built else placing.candidates(c, auth, bot)
        from . import cloud_machines

        if not options and (first := cloud_machines.first_home(c, bot)):
            options = [first]  # a cloud-mode bot whose own machine is not made yet: the shared one first
        if not options and cloud_machines.offered(c, settings) and cloud_machines.adopt(c, bot, who.actor):
            # A Tico Cloud bot whose cloud machine has not enrolled yet: it is assigned there when it does
            # (cloud_machines.enrolled), so nothing is refused while the machine is on its way.
            machine = cloud_machines.coming(c, bot)
            if machine and machine["status"] == "out_of_credit":
                raise Problem("out_of_credit", f"Out of credit: {machine['label']} can't start. Add credit first", 409)
            if machine and machine["status"] == "failed":
                raise Problem(
                    "cloud_computer_failed",
                    f"{machine['label']} didn't start: {machine['words']}. See Settings > Computers",
                    409,
                )
            if machine:
                return {"bot": bot, "computer": machine["label"], "placed": False, "already": False, "waiting": True}
        if not options:
            raise Problem(
                "no_computer",
                "No computer can take this bot yet: add one, or ask an admin to open one to members' bots",
                409,
            )
        generation = current["generation"] if current else 0
        refusal = None
        for runner in options:
            if risky(who) and closed_computer(c, who, bot, runner["id"]):
                return propose_card(
                    c,
                    who,
                    "POST",
                    "/api/v2/bots/" + bot + "/assignment",
                    {"runner_id": runner["id"], "expected_generation": generation},
                    placement_summary(c, bot, runner["id"]),
                )
            c.execute("SAVEPOINT place_now")
            try:
                execution.assign(c, who, bot, M.Assignment(runner_id=runner["id"], expected_generation=generation))
            except Problem as exc:
                c.execute("ROLLBACK TO place_now")
                c.execute("RELEASE place_now")
                if wanted:
                    raise
                refusal = exc
                continue
            c.execute("RELEASE place_now")
            return {"bot": bot, "computer": runner["label"], "placed": True, "already": False}
        assert refusal is not None  # options is not empty, so a place that failed left its refusal
        raise refusal

    def health_checks(who):
        """What is wrong with the bots this person may see, most urgent first, each with its one fix."""
        auth.domain(who)
        with store.read_transaction() as c:  # one snapshot: company-wide reads are shared by every bot's row
            result = fleet_check_module.check(c, who, auth, settings)
            result["computers"] = computer_rows(c, who)
            result["services"] = views.team_services(c, who, auth)
            from . import health
            from .onboarding import config_view

            checks = health.view(
                c, who, settings, auth, getattr(app.state, "github_app", None), config_view(c, settings, who)
            )["checks"]
            result["checks"] = checks
            for check in checks:
                if check["status"] not in ("warn", "bad", "unknown") and not check.get("items"):
                    continue
                items = check.get("items") or [
                    {"bot": "", "name": check["label"], "text": check["summary"], "fixes": check["fixes"]}
                ]
                for item in items:
                    fixes = item.get("fixes") or []
                    result["issues"].append(
                        {
                            **item,
                            "kind": item.get("kind") or check["id"],
                            "severity": "high"
                            if check["status"] == "bad"
                            else "low"
                            if check["status"] == "info"
                            else "medium",
                            "fix": item.get("fix")
                            or "; ".join(f["label"] + (": " + f["href"] if f["href"] else "") for f in fixes),
                        }
                    )

            result["issues"].sort(key=lambda i: (fleet_check_module.ORDER[i["severity"]], i["name"].lower(), i["kind"]))
            result["counts"] = {
                level: sum(i["severity"] == level for i in result["issues"]) for level in fleet_check_module.ORDER
            }
            return result

    @app.get("/api/v2/health/issues")
    def health_issues(request: Request):
        """What is wrong with the bots this person may see, most urgent first, each with its one fix. The Assistant
        also gets the live team snapshot (views.fleet_snapshot) with it."""
        who = request.state.identity
        checked = health_checks(who)
        if getattr(who, "via", None) == "assistant":
            from .views import fleet_snapshot

            with store.read() as c:
                snapshot = fleet_snapshot(c, auth, who, task_view)
            return {**snapshot, **{key: checked[key] for key in ("checks", "issues", "services", "counts")}}
        return checked

    def computer_rows(c, who):
        from .performance import load_statuses
        from . import usage_limits
        from .start_places import summaries, describe as describe_turns

        online = {r["id"] for r in runner_online.online_rows(c)}
        everyone = who.role == "owner" or auth.bot_admin(who)
        mine = H.actor_id(who.actor)
        readable = auth.bot_accesses(c, who)
        # One pass for every computer's bots (not one SELECT per runner).
        placed, _ = views.assignments_by_runner(c)
        rows = []
        # One Default for the whole listing: each computer's summary reuses pools/groups/spend memos.
        limits = usage_limits.company(c)
        runners = list(c.execute("SELECT * FROM runners_with_state WHERE revoked_at IS NULL ORDER BY label"))
        # One pass for every computer's turn counts and load (not one summary and one load per computer).
        turn_counts = summaries(c, [r["id"] for r in runners], settings, limits)
        loads = load_statuses(c, [r["id"] for r in runners])
        for r in runners:
            counts = turn_counts[r["id"]]
            turns = {"turn_counts": counts, "turn_summary": describe_turns(counts)}
            detailed = everyone or r["owner_id"] == mine or r["accepts_member_bots"]
            if not detailed:
                if who.role == "bot":
                    # Discovery and load do not grant access to repositories, logs or operator identity.
                    rows.append(
                        {
                            "id": r["id"],
                            "label": r["label"],
                            "online": r["id"] in online,
                            "platform": r["platform"] or "",
                            "accepts_member_bots": False,
                            **loads[r["id"]],
                            **turns,
                        }
                    )
                continue
            bots = [bot for bot in placed.get(r["id"]) or [] if (readable.get(bot) or {}).get("see")]
            rows.append(
                {
                    "id": r["id"],
                    "label": r["label"],
                    "online": r["id"] in online,
                    "platform": r["platform"] or "",
                    "owner_id": r["owner_id"],
                    "accepts_member_bots": bool(r["accepts_member_bots"]),
                    "bots": bots,
                    "turns_at_once": r["turns_at_once"],
                    "turn_limit": start_places.turn_limit(r),
                    **screen_listing(r),
                    **performance_listing(r),
                    **views.computer_details(c, r, who, auth),
                    **loads[r["id"]],
                    **turns,
                }
            )
        return rows

    register(
        app,
        admin_only,
        auth,
        closed_computer,
        computer_rows,
        delegated_identity,
        execution,
        history_on_github,
        mutate,
        onboarding,
        place_now,
        placement_summary,
        propose_card,
        risky,
        send,
        settings,
        settings_admin,
        store,
        telemetry,
        reset_bot_sessions,
    )
    return computer_rows, place_now


def register_completion(
    app,
    auth,
    execution,
    mutate,
    rule_comment_audience,
    rule_message_audience,
    rule_public,
    rule_target,
    rule_task_audience,
    rule_update_audience,
    store,
):
    @app.post("/api/v2/attempts/{aid}/complete")
    def complete(request: Request, aid: str, body: M.Completion):
        body = scrub_attempt(aid, body)
        from . import automations  # the turn's end acts for the turn (automations.for_attempt)

        rc = completion_rule_check(request, aid, body)

        def finish(c):
            with automations.for_attempt(c, aid):
                return execution.complete(c, request.state.identity, aid, body, rule_check=rc)

        return mutate(request, body, finish)

    @org_rule_check.replayer("reply")
    def rule_replay_reply(c, bot, hold, replay, ingest, gate=None):
        """A held turn reply, posted as the completion would have posted it."""
        from .execution import reply_target, split_runner_notices, work_task_id

        msg = H.message(c, replay["message"])
        if not msg:
            raise Problem("not_found", "The message this reply answered is gone", 409)
        conv = H.conversation(c, msg["conversation_id"])
        assert conv is not None  # a reply goes to its message's conversation
        tid = work_task_id(msg, conv)
        task = H.task(c, tid) if tid else None
        target = msg["sender_id"] if msg["kind"] == "ask" else reply_target(msg, conv, task)
        refs = {"turn_id": replay["attempt"], **({"task": tid} if task else {})}
        # The completion's own destination check, as the run it came from: a private task or meeting the reply drew
        # on that its reader lost since the hold keeps it from going there.
        privacy.require_destination(
            c, Identity(bot.actor, "bot", attempt_id=replay["attempt"]), target, conv["id"], refs, msg
        )
        text, _ = split_runner_notices(replay["text"])
        sent = H.say(
            c,
            bot.actor,
            target,
            org_rule_check.warned_text(gate, text),
            conversation_id=conv["id"],
            in_reply_to=msg["id"],
            refs=refs,
        )
        return {"message": sent}

    def reply_audience(c, bot, replay):
        from .execution import reply_target, work_task_id

        msg = H.message(c, replay["message"])
        if not msg:
            raise Problem("not_found", "The message this reply answered is gone", 409)
        conv = H.conversation(c, msg["conversation_id"])
        tid = work_task_id(msg, conv)
        return [reply_target(msg, conv, H.task(c, tid) if tid else None)]

    def rule_audiences():
        """Who each held write would reach now, from its replay data, counted as its route's check counted them:
        Send anyway refuses a hold whose audience moved (org_rule_check._same_audience)."""

        def task_row(c, bot, replay):
            return auth.task(c, bot, auth.resolve_task(c, bot, replay["task"]))

        def message(c, bot, replay):
            return rule_message_audience(c, bot, M.MessageCreate.model_validate(replay["body"]))

        def note(c, bot, replay):
            return [rule_target(c, bot, M.NoteCreate.model_validate(replay["body"]).to)]

        def task_create(c, bot, replay):
            body = M.TaskCreate.model_validate(replay["body"])
            return [rule_target(c, bot, body.owner), *(org_rule_check.company(c) if rule_public(body) else [])]

        def task_update(c, bot, replay):
            return rule_update_audience(c, bot, M.TaskUpdate.model_validate(replay["body"]), task_row(c, bot, replay))

        def comment(c, bot, replay):
            body = M.TaskComment.model_validate(replay["body"])
            return rule_comment_audience(c, bot, task_row(c, bot, replay), body.text)

        def comment_edit(c, bot, replay):
            body = M.TaskComment.model_validate(replay["body"])
            return rule_task_audience(c, task_row(c, bot, replay), bot.actor, body.text)

        return {
            "message": message,
            "note": note,
            "task_create": task_create,
            "task_update": task_update,
            # A hand-off (task_owner) and the other v3 task writes count their own: backend/task_moves_routes.py.
            "bulk": task_update,
            "comment": comment,
            "comment_edit": comment_edit,
            "reply": reply_audience,
        }

    def completion_rule_check(request, aid, body):
        """The org rule check of a turn's reply, before the completion's write (backend/org_rule_check.py): the reply
        as the completion would post it, to whom it would post it. None when there is nothing to check."""
        if body.outcome != "completed" or not body.text:
            return None
        from .execution import reply_source, reply_target, split_runner_notices, work_task_id

        with store.read() as c:
            if not org_rule_check.R.any_active(c):
                return None  # no rule in force: the completion reads nothing more for its check
            if store.saved(c, request.state.identity, request.url.path, request.headers.get("idempotency-key")):
                return None  # a retry answers from its saved result; nothing is checked again
            row = c.execute(
                "SELECT a.bot_id, j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (aid,)
            ).fetchone()
            msg = H.message(c, row["message_id"], include_deleted=True) if row else None
            if not msg or msg["deleted_at"] is not None:
                return None
            # A carrier-started turn answers the message it carries, so its reply is checked as going to that sender.
            msg = reply_source(c, msg)
            if not msg or msg["deleted_at"] is not None:
                return None
            conv = H.conversation(c, msg["conversation_id"])
            tid = work_task_id(msg, conv)
            task = H.task(c, tid) if tid else None
            if not (msg["kind"] in ("say", "ask", "steer") or task):
                return None
            target = reply_target(msg, conv, task)
        bot = Identity("bot:" + row["bot_id"], "bot", attempt_id=aid)
        text, _ = split_runner_notices(body.text)
        return org_rule_check.preflight(
            store, app.state.judge, bot, "reply", text, [target], context={"task": task["title"]} if task else None
        )

    @app.get("/api/v2/bots/{bot}/execution-review")
    def execution_review(request: Request, bot: str):
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            return execution.review_jobs(c, request.state.identity, bot)

    @app.post("/api/v2/jobs/{jid}/reconcile")
    def reconcile_job(request: Request, jid: str, body: M.ReconcileJob):
        return mutate(request, body, lambda c: execution.reconcile_job(c, request.state.identity, jid, body))

    @app.post("/api/v2/jobs/{jid}/retry")
    def retry(request: Request, jid: str, body: M.Retry):
        return mutate(request, body, lambda c: execution.retry(c, request.state.identity, jid, body))

    return rule_audiences
