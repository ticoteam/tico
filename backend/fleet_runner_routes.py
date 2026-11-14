"""Computer, placement, runner, agent sync and attempt routes."""

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

    @app.post("/api/v2/runners/{rid}/turns-at-once")
    def runner_turns_at_once(request: Request, rid: str, body: M.RunnerTurnsAtOnce):
        """How many turns a computer runs at once; null is automatic, its own capacity. A computer that cannot take a
        raised number (no turns_at_once_v1) runs at most the capacity it reports. Owners and admins."""
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

    @app.post("/api/v2/runners/{rid}/label")
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

    @app.post("/api/v2/runners/{rid}/member-bots")
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

    @app.post("/api/v2/runners/{rid}/gateway-fallback")
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

    @app.get("/api/v2/bots/{bot}/access")
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
    @app.put("/api/v2/bots/{bot}/access")
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

    @app.get("/api/v2/people/{pid}/access")
    def person_access_read(request: Request, pid: str, on_behalf_of: str = ""):
        """Who may see, read and write to this person (docs/permissions.md). For whoever manages them."""
        return teammate_access_read(request, "human:" + pid, on_behalf_of)

    @app.post("/api/v2/people/{pid}/access")  # the same write for the tools, whose client only posts
    @app.put("/api/v2/people/{pid}/access")
    def person_access_write(request: Request, pid: str, body: M.TeammateAccess):
        return teammate_access_write(request, "human:" + pid, body)

    @app.get("/api/v2/groups/{gid}/access")
    def group_access_read(request: Request, gid: str, on_behalf_of: str = ""):
        """Who may see, read and write to this group (docs/permissions.md). For its leads and the admins."""
        return teammate_access_read(request, "group:" + gid, on_behalf_of)

    @app.post("/api/v2/groups/{gid}/access")
    @app.put("/api/v2/groups/{gid}/access")
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
                bot_id = H.resolve_bot(c, created["slug"])["id"]
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
