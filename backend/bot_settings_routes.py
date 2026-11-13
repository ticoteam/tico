"""Bot settings routes: placement, models, turn and task limits, transitions, pause and control."""

import json
from fastapi import Request
from . import (
    bot_settings as BS,
    company_billing,
    company_pause,
    gateway_connection,
    login_holds,
    model_login,
    models as M,
    task_privacy as privacy,
    wake_presets,
)
from .execution import claim_scope, clear_limits, lift_after_model_change, model_choice, own_login_hold
from .harnesses import resolve_harness, runtime_of
from .providers import MODEL_BY_ID
from .readiness import absolute as absolute_link, can_run
from .store import H, Problem


def register(
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
):
    @app.post("/api/v2/bots/{bot}/placement")
    def placement(request: Request, bot: str, body: M.BotPlacement):
        caller = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            settings_admin._manager(c, who, bot)
            if risky(who) and closed_computer(c, who, bot, body.runner_id):
                return propose_card(
                    c,
                    who,
                    "POST",
                    "/api/v2/bots/" + bot + "/placement",
                    body.model_dump(exclude={"on_behalf_of"}),
                    placement_summary(c, bot, body.runner_id),
                )
            config = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
            if not config:
                raise Problem("not_found", "Bot not found", 404)
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before moving it", 409)
            runner = c.execute(
                "SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (body.runner_id,)
            ).fetchone()
            if not runner:
                raise Problem("not_found", "Computer is not registered", 404)
            if (
                who.role != "owner"
                and not auth.bot_admin(who)
                and runner["owner_id"] != H.actor_id(who.actor)
                and not (runner["accepts_member_bots"] and auth.member_bot(c, bot))
            ):
                raise Problem(
                    "forbidden",
                    "You may use only your own registered computers, or ones an admin has opened to members' bots",
                    403,
                )
            current = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
            generation = current["generation"] if current else 0
            if generation != body.expected_generation:
                raise Problem("version_conflict", "Assignment changed; refresh before moving it", 409)
            # A member's bot stays its member's whatever computer it runs on: placing it never hands it to the
            # computer's operator.
            operator = config["owner_id"] if auth.member_bot(c, bot) else runner["owner_id"]
            if current and current["runner_id"] == runner["id"] and config["owner_id"] == operator:
                return {
                    "bot": bot,
                    "owner_id": config["owner_id"],
                    "revision": config["revision"],
                    "assignment": dict(current),
                }
            before = settings_admin.snapshot(c, bot, "placement")
            c.execute("UPDATE bot_config SET owner_id=?,revision=revision+1 WHERE bot=?", (operator, bot))
            assignment = execution.assign(
                c, who, bot, M.Assignment(runner_id=runner["id"], expected_generation=generation)
            )
            reset = reset_bot_sessions(c, bot)
            settings_admin.record(c, who.actor, bot, "placement", before, settings_admin.snapshot(c, bot, "placement"))
            H.event(
                c,
                who.actor,
                "bot.placement_changed",
                bot,
                {"runner": runner["id"], "operator": operator, "sessions_reset": reset},
            )
            return {
                "bot": bot,
                "owner_id": operator,
                "revision": config["revision"] + 1,
                "assignment": assignment,
                "sessions_reset": reset,
            }

        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/model")
    def model(request: Request, bot: str, body: M.BotModel):
        who = request.state.identity
        with store.read() as c:  # the report-manager check below runs before the write, on the id
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)

        def work(c):
            from . import strategy_acts

            # the owner's at every level
            strategy_acts.gate(
                c, who.actor, "cost", f"change {(H.bot(c, bot) or {}).get('slug') or bot}'s model to {body.model}"
            )
            settings_admin._manager(c, who, bot, report="model")
            from .shared_bots import refuse_copy

            refuse_copy(c, bot)
            choice = MODEL_BY_ID.get(body.model)
            if not choice:
                raise Problem("model", "Choose one of the supported models", 422)
            settings_admin.refuse_retired(choice)
            row = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
            if not row:
                raise Problem("not_found", "Bot not found", 404)
            if row["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before changing models", 409)
            config = json.loads(row["config"])
            old_model = config.get("model") or ""
            old_runtime = config.get("runtime") or ""
            old_effort = config.get("reasoning_effort") or H.bot(c, bot).get("effort")
            old_harness = resolve_harness(config, old_runtime)
            effort = settings_admin._effort(choice, body.effort, old_effort)
            harness = settings_admin._harness(choice, body.harness, old_harness)
            runtime = runtime_of(harness) or choice["runtime"]
            if old_model == choice["id"] and old_runtime == runtime and old_effort == effort and old_harness == harness:
                # Asking again for what is set is how a caller re-checks that the bot's computer runs it.
                return {
                    "bot": bot,
                    "model": choice["id"],
                    "runtime": runtime,
                    "harness": harness,
                    "effort": effort,
                    "revision": row["revision"],
                    "sessions_reset": 0,
                    "readiness": absolute_link(
                        can_run(c, bot, runtime, harness=harness, model=choice["id"]), settings.public_url
                    ),
                }
            before = settings_admin.snapshot(c, bot, "model")
            execution.expire(c)
            # A turn that is out finishes on the model its claim handed it and the next runs on the new one, as in
            # Settings (settings_admin._apply): a person must be able to move a rate-limited bot off its spent model
            # while a retry runs (Dev 353).
            # The same check a prepared change makes (settings_admin._target): a model the bot's computer reports it
            # cannot run is refused with the fix, not saved for a bot that then never runs.
            can = absolute_link(can_run(c, bot, runtime, harness=harness, model=choice["id"]), settings.public_url)
            if can["can_run"] is False:
                raise Problem("runner_not_ready", can["problem"], 409, extra={"fix": can["fix"], "link": can["link"]})
            was = model_choice(c, settings, bot)
            config.update(
                {
                    "model": choice["id"],
                    "runtime": runtime,
                    "harness": harness,
                    "reasoning_effort": effort,
                    "model_managed_by": "cloud",
                }
            )
            BS.write(c, bot, config)
            c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
            c.execute("UPDATE bots SET effort=? WHERE id=?", (effort, bot))
            # Its shared copies follow it, so any bot may have moved onto another tool.
            model_login.hold_removals(c, settings)
            lift_after_model_change(c, settings, who.actor, bot, was)
            reset = reset_bot_sessions(c, bot)
            settings_admin.record(c, who.actor, bot, "model", before, settings_admin.snapshot(c, bot, "model"))
            H.event(
                c,
                who.actor,
                "bot.model_changed",
                bot,
                {
                    "from": old_model,
                    "to": choice["id"],
                    "runtime": runtime,
                    "harness": harness,
                    "effort": effort,
                    "sessions_reset": reset,
                },
            )
            return {
                "bot": bot,
                "model": choice["id"],
                "runtime": runtime,
                "harness": harness,
                "effort": effort,
                "revision": row["revision"] + 1,
                "sessions_reset": reset,
                "readiness": can,
            }

        return mutate(request, body, settings_admin.as_report_manager(who, bot, "model", work))

    @app.post("/api/v2/bots/{bot}/fallback")
    def fallback(request: Request, bot: str, body: M.BotFallback):
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return mutate(request, body, lambda c: settings_admin.set_fallback(c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/turns-at-once")
    def bot_turns_at_once(request: Request, bot: str, body: M.BotTurnsAtOnce):
        """How many turns the bot runs at once across every computer; null is no limit (start_places.bot_limit)."""
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return mutate(request, body, lambda c: settings_admin.set_turns_at_once(c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/task-model")
    def bot_task_model(request: Request, bot: str, body: M.BotTaskModel):
        """The harness, model and effort the bot's tasks run on, or null for the same as the bot
        (backend/task_models.py). The bot's own model (POST .../model) never changes it, and this never changes that.
        `warning` is plain words for a cheaper pick on a bot that sends, spends, publishes or writes; it still saves."""
        who = request.state.identity
        with store.read() as c:  # the report-manager check below runs before the write, on the id
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)

        def work(c):
            return settings_admin.set_task_model(c, who, bot, body)

        return mutate(request, body, settings_admin.as_report_manager(who, bot, "task_model", work))

    @app.post("/api/v2/bots/{bot}/manages-reports")
    def bot_manages_reports(request: Request, bot: str, body: M.BotManagesReports):
        """Whether this bot changes its direct reports (backend/report_managers.py). A person who manages this bot sets
        it; with the owner's team rule `managers_manage_reports` off, no bot does, whatever this says."""
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return mutate(request, body, lambda c: settings_admin.set_manages_reports(c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/compact-at")
    def bot_compact_at(request: Request, bot: str, body: M.BotCompactAt):
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return mutate(request, body, lambda c: settings_admin.set_compact_at(c, request.state.identity, bot, body))

    @app.get("/api/v2/wake-presets/measures")
    def wake_preset_measures(request: Request, since: str | None = None, until: str | None = None):
        """Per wake preset a past job recorded, over jobs queued in [since, until) (the last 7 days by default): turns and
        tokens per accepted ticket, pickup latency, blocked time and missed actions. History only: the presets are
        retired and new jobs record none. Owners and admins."""
        admin_only(request.state.identity, "sees wake preset measures")
        with store.read() as c:
            return wake_presets.measures(c, since or H.shift(H.now(), days=-7), until)

    @app.post("/api/v2/bots/{bot}/transitions")
    def begin_transition(request: Request, bot: str, body: M.BotTransitionCreate):
        who = request.state.identity
        with store.read() as c:  # the report-manager check below runs before the write, on the id
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)

        def work(c):
            acting = delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who
            result = settings_admin.begin(c, acting, bot, body)
            if acting is not who:
                H.event(
                    c,
                    who.actor,
                    "bot.transition_delegated",
                    bot,
                    {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of, "kind": body.kind},
                )
            return result

        scope = "model" if body.kind == "model" else None
        return mutate(request, body, settings_admin.as_report_manager(who, bot, scope, work))

    @app.get("/api/v2/settings/transitions/{transition_id}")
    def transition(request: Request, transition_id: str):
        with store.read() as c:
            return settings_admin.get(c, request.state.identity, transition_id)

    @app.post("/api/v2/settings/transitions/{transition_id}/apply-without-checkpoint")
    def force_transition(request: Request, transition_id: str, body: M.BotTransitionApply):
        return mutate(request, body, lambda c: settings_admin.force(c, request.state.identity, transition_id))

    @app.post("/api/v2/settings/transitions/{transition_id}/cancel")
    def cancel_transition(request: Request, transition_id: str, body: M.Empty):
        return mutate(request, body, lambda c: settings_admin.cancel(c, request.state.identity, transition_id))

    @app.get("/api/v2/settings/history")
    def settings_history(request: Request, limit: int = 100, bot: str = "", before: str = ""):
        # One snapshot for the bots and the changes, so the people and bot rules it reads are computed once for it.
        with store.read_transaction() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot) if bot else bot
            return settings_admin.history(c, request.state.identity, limit, bot=bot, before=before)

    @app.post("/api/v2/settings/history/{change_id}/undo")
    def undo_setting(request: Request, change_id: str, body: M.SettingsUndo):
        return mutate(request, body, lambda c: settings_admin.undo(c, request.state.identity, change_id, body))

    @app.post("/api/v2/bots/{bot}/limit/retry")
    def limit_retry(request: Request, bot: str, body: M.LimitRetry):
        """Try now: a person who renewed a plan need not wait out the usage-limit cooldown."""
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "Only a person who manages this bot can retry it now", 403)
            # A hold already ended (by itself or an earlier Try now) waits only on its one probe turn.
            if (H.status(c, bot) or {}).get("state") != "limited" and (
                own_login_hold(c, settings, bot).get("until") or ""
            ) <= H.now():
                raise Problem("not_limited", "This bot is not waiting on a usage limit", 409)
            if login_holds.probing(c, own_login_hold(c, settings, bot)):
                raise Problem(
                    "probing", "This bot's login is already trying one turn; its result decides the rest", 409
                )
            return {"cleared": clear_limits(c, settings, who.actor, bot)}

        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/stop")
    def stop_turn(request: Request, bot: str, body: M.StopTurn):
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return mutate(
            request, body, lambda c: execution.stop(c, request.state.identity, bot, body.run_id, body.redirect)
        )

    @app.get("/api/v2/bots/{bot}/pause-state")
    def bot_pause_state(request: Request, bot: str):
        """The public pause banner, without billing amounts or unreadable task counts."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.require_see(c, who, bot)
            row = H.bot(c, bot)
            config = c.execute("SELECT owner_id,revision FROM bot_config WHERE bot=?", (bot,)).fetchone()
            operator = (H.human(c, config["owner_id"]) or {}) if config else {}
            record = c.execute(
                "SELECT actor,created_at,action FROM events WHERE target=? "
                "AND action IN ('bot.pause','bot.resume') ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (bot,),
            ).fetchone()
            paused_by = None
            if row["state"] == "paused" and record and record["action"] == "bot.pause":
                person = H.human(c, H.actor_id(record["actor"])) if record["actor"].startswith("human:") else None
                if person:
                    paused_by = {"name": person["name"], "at": record["created_at"]}
            tasks = H._rows(
                c.execute(
                    "SELECT * FROM tasks WHERE owner_id=? AND status='open'",
                    ('bot:' + bot,),
                )
            )
            count = sum(1 for task in tasks if privacy.task_readable(c, who, task))
            value = {
                "state": row["state"],
                "revision": config["revision"] if config else None,
                "can_control": bool(auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)),
                "owner_id": operator.get("name") or (config["owner_id"] if config else "its operator"),
                "paused_by": paused_by,
                "open_tasks": count,
            }
            key, base = gateway_connection.target(c, app.state.vault, settings)[:2]
        # HQ calls must never hold a database transaction. Only the pause bit leaves this route.
        value["company_paused"] = False if settings.demo else company_billing.paused(key, base)
        return value

    @app.get("/api/v2/company/pause")
    def company_pause_state(request: Request):
        """The company-wide pause: whether it is on, who paused and when, the bots that stay paused on their own, and
        whether the viewer may pause or resume it. People only."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human"):
            # The pause and who paused it are for people on the team; a bot or a service key does not read them.
            raise Problem("forbidden", "Only a person on Tico Team can see the company pause", 403)
        with store.read() as c:
            value: dict[str, object] = {**company_pause.read(c)}
            value["can_control"] = company_pause.may_control(auth, who)
            return value

    @app.post("/api/v2/company/pause")
    def company_pause_set(request: Request, body: M.CompanyPause):
        """Pause every bot on the team until someone resumes it. Only the owner and admins may; a refusal says why and
        changes nothing. Already paused: nothing changes and the first pause stays on record."""
        who = request.state.identity
        return mutate(request, body, lambda c: company_pause.pause(c, auth, who))

    @app.post("/api/v2/company/resume")
    def company_pause_clear(request: Request, body: M.CompanyPause):
        """Lift the company pause. Only the owner and admins may. Bots paused on their own stay paused. Nothing paused:
        nothing changes."""
        who = request.state.identity
        return mutate(request, body, lambda c: company_pause.resume(c, auth, who))

    @app.post("/api/v2/bots/{bot}/control")
    def control(request: Request, bot: str, body: M.BotControl):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "You do not manage this bot", 403)
            config = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before controlling it", 409)
            if body.action in ("scope", "unscope"):
                # A narrower claim, never a wider one: the bot's pause, drain and every other claim check stand, and
                # the work outside the task stays queued as it is (execution.scoped_sql).
                task = H.task(c, body.task_id) if body.action == "scope" and body.task_id else None
                if body.action == "scope" and not (task and privacy.task_readable(c, who, task)):
                    raise Problem("not_found", "No such task", 404)
                before = claim_scope(c, bot)
                c.execute(
                    "INSERT INTO bot_control(bot,claim_task) VALUES(?,?) "
                    "ON CONFLICT(bot) DO UPDATE SET claim_task=excluded.claim_task",
                    (bot, task["id"] if task else None),
                )
                c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
                H.event(
                    c,
                    who.actor,
                    "bot." + body.action,
                    bot,
                    {"task_id": task["id"] if task else None, "previous": before},
                )
                return {
                    "bot": bot,
                    "action": body.action,
                    "task_id": task["id"] if task else None,
                    "revision": config["revision"] + 1,
                }
            state = (H.bot(c, bot) or {}).get("state")
            if state == "quarantined" and body.action != "drain":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            if body.action == "resume" and state != "active":
                # Only a paused (or otherwise inactive) bot is activated again. An active bot that was only drained
                # just takes work again: its computer may not have reported its repository yet (a bot moved to a new
                # cloud machine), and refusing would leave it drained with nothing to wake that machine.
                settings_admin.ensure_activation(c, who, bot)
            c.execute(
                "INSERT INTO bot_control(bot,draining) VALUES(?,?) "
                "ON CONFLICT(bot) DO UPDATE SET draining=excluded.draining",
                (bot, int(body.action in ("drain", "pause"))),
            )
            if body.action != "drain":
                c.execute(
                    "UPDATE bots SET state=? WHERE id=?", ("active" if body.action == "resume" else "paused", bot)
                )
                if body.action == "pause":
                    c.execute(
                        "UPDATE attempts SET lease_until=? WHERE bot_id=? AND state IN ('leased','running')",
                        (H.now(), bot),
                    )
                    execution.expire(c)
                H.status_set(c, H.KEEPER, bot, state="paused" if body.action == "pause" else "idle")
            c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
            H.event(c, who.actor, "bot." + body.action, bot)
            return {"bot": bot, "action": body.action, "revision": config["revision"] + 1}

        return mutate(request, body, work)
