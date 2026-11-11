"""Go-live routes: waiting for a repository, finishing a pending go-live, going live."""

import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from . import agents, go_live as pending_go_lives, models as M, routines, setup_live
from .auth import Identity
from .execution import bot_repository
from .settings_admin import repository_present
from .statuses import is_parked
from .store import H, Problem, encode


def register(
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
):
    GO_LIVE_PENDING = pending_go_lives.PREFIX

    def finish_go_live(c, who, bot, setup):
        """What going live does once the bot's computer has its repository: active, its setup started with the
        person, and the routines its template came with on. Shared by the request itself and by the readiness report that completes a
        go-live that was waiting for the repository."""
        row = H.bot(c, bot)
        if row["state"] == "quarantined":
            raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
        activated = row["state"] != "active"
        if activated:
            settings_admin.update_bot(
                c,
                who,
                bot,
                M.BotDefinitionUpdate(status="active", expected_revision=settings_admin._config(c, bot)["revision"]),
            )
        started = False
        config = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (bot,)).fetchone()
        if setup and config and is_parked(config["onboarding_state"]) and who.role in ("owner", "human"):
            send(c, who, M.MessageCreate(to="bot:" + bot, text="Let's set you up."))
            started = True
        # Going live turns its template's routines on too: nobody approves them separately. The answer and the
        # go-live event name the ones this turned on, and `routines` says which are on now, so a report of them
        # comes from this fact rather than from the template.
        armed = [routine["name"] for routine in onboarding.arm_routines(c, who, bot)]
        # BotOps' wait on this bot's setup task ends here, and BotOps is woken to say done.
        setup_live.go_live_ends_botops_wait(c, bot, who.actor)
        # A seat bot nobody chats with runs the same setup on its own, with no person (backend/setup_live.py).
        own = setup_live.start_setup(c, onboarding, bot)
        c.execute("DELETE FROM registry_metadata WHERE key=?", (GO_LIVE_PENDING + bot,))
        return {
            "activated": activated,
            "setup_started": started,
            "routine_armed": armed,
            "routines": routines.of_bot(c, bot),
            **({"own_setup_task_id": own} if own else {}),
        }

    def wait_for_repository(c, who, bot, placed, body, github_asked=True):
        """Keep the placement and remember the go-live: the computer is told about the bot on its next heartbeat,
        clones the repository, and the readiness report that says it has it finishes the go-live
        (`finish_pending_go_live`). It expires after backend/go_live.py EXPIRY_DAYS."""
        computer = placed.get("computer") or "its computer"
        runner = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (
                GO_LIVE_PENDING + bot,
                encode(
                    {
                        "bot": bot,
                        "runner_id": runner["runner_id"] if runner else "",
                        "computer": computer,
                        "actor": who.actor,
                        "role": who.role,
                        "email": who.email,
                        "via": who.via,
                        "setup": body.setup,
                        "requested_at": H.now(),
                    }
                ),
            ),
        )
        H.event(c, who.actor, "bot.go_live_waiting", bot, {"computer": computer, "github_asked": github_asked})
        # A cloud machine still on its way (place_now `waiting`) gets the bot when it enrolls, then the repository.
        first = (
            f"{computer} to start and get the repository"
            if placed.get("waiting")
            else f"{computer} to get the repository"
        )
        message = (
            f"Waiting for {first}. Tico finishes going live by itself "
            f"when {computer} has it; nothing needs to run again"
        )
        if not github_asked:
            message += ". GitHub could not be asked; if the repository doesn't exist, this won't finish"
        return {
            "bot": bot,
            "state": "waiting_for_repository",
            "bot_state": H.bot(c, bot)["state"],
            "computer": computer,
            "placed": placed.get("placed", False),
            "activated": False,
            "setup_started": False,
            "github_checked": github_asked,
            "message": message,
        }

    def finish_pending_go_live(c, runner_id, readiness):
        """A computer's readiness report completes the go-lives waiting for it to get a bot's repository. Each one
        finishes once: the record is removed when it completes, so later reports do nothing. One past its deadline
        is expired; one that can no longer finish is marked failed. Either way the person is told once, and nothing
        here fails the heartbeat."""
        from datetime import datetime, timezone
        from .auth import validate_identity

        reports = readiness.get("bots") or {}
        now = datetime.now(timezone.utc)
        for key, bot, pending in pending_go_lives.rows(c):
            if pending.get("failed"):
                continue
            c.execute("SAVEPOINT go_live_finish")
            try:
                if pending_go_lives.expired(pending, now):
                    pending_go_lives.fail(
                        c,
                        key,
                        bot,
                        pending,
                        "expired",
                        pending_go_lives.expire_notice((H.bot(c, bot) or {}).get("slug") or bot, pending),
                    )
                    c.execute("RELEASE go_live_finish")
                    continue
                report = reports.get(bot)
                if (
                    not isinstance(report, dict)
                    or report.get("repository_present") is not True
                    or not c.execute(
                        "SELECT 1 FROM assignments WHERE bot=? AND runner_id=?", (bot, runner_id)
                    ).fetchone()
                ):
                    c.execute("RELEASE go_live_finish")
                    continue
                row = H.bot(c, bot)
                if not row or row["state"] == "archived":
                    c.execute("DELETE FROM registry_metadata WHERE key=?", (key,))
                    c.execute("RELEASE go_live_finish")
                    continue
                role = pending.get("role") or "human"
                if role == "owner" and H.actor_id(pending.get("actor") or "") != auth.owner_id(c):
                    role = "human"
                who = Identity(
                    pending.get("actor") or "", role, pending.get("email") or "", via=pending.get("via") or ""
                )
                validate_identity(c, who)
                done = finish_go_live(c, who, bot, bool(pending.get("setup", True)))
                H.event(c, who.actor, "bot.go_live_finished", bot, {"computer": pending.get("computer") or "", **done})
                c.execute("RELEASE go_live_finish")
            except Exception as exc:
                c.execute("ROLLBACK TO go_live_finish")
                c.execute("RELEASE go_live_finish")
                where = pending.get("computer") or "its computer"
                if isinstance(exc, Problem):
                    code, notice = (
                        exc.code,
                        f"{(H.bot(c, bot) or {}).get('slug') or bot} has its repository on {where}, but going live could not finish: {str(exc.detail).rstrip('.')}.",
                    )
                else:
                    # A fault in Tico, not something the person did: its text stays in the log.
                    logging.getLogger("tico.go_live").exception("Finishing the go-live of %s failed", bot)
                    telemetry.capture("go_live_finish", exc, 500)
                    code, notice = (
                        "finish_error",
                        f"Going live for {(H.bot(c, bot) or {}).get('slug') or bot} could not finish because of an error in Tico.",
                    )
                c.execute("SAVEPOINT go_live_fail")
                try:
                    pending_go_lives.fail(c, key, bot, pending, code, notice)
                    c.execute("RELEASE go_live_fail")
                except Exception:
                    c.execute("ROLLBACK TO go_live_fail")
                    c.execute("RELEASE go_live_fail")
                    logging.getLogger("tico.go_live").exception("Recording the failed go-live of %s failed", bot)

    execution.readiness_reported = finish_pending_go_live

    def repository_on_github(bot):
        """Before the write (GitHub is never called while holding it): whether GitHub has the bot's repository, for a
        bot whose computer has not reported it yet. ("unknown", None) when there is nothing to ask."""
        with store.read() as c:
            if not H.bot(c, bot) or agents.external_harness(c, bot) or repository_present(c, bot):
                return "unknown", None
            repository = bot_repository(c, settings, bot)
        service = getattr(app.state, "github_app", None)
        return service.repository_state(repository) if service and repository else ("unknown", None)

    @app.post("/api/v2/bots/{bot}/go-live")
    def go_live(request: Request, bot: str, body: M.BotGoLive):
        """Everything between "built" and "working": a computer, active, and its setup started with the person.
        Each step is the same one the app's own buttons take, checked with the caller's rights. A bot whose
        computer does not have its repository yet keeps its placement and finishes when the computer reports it
        (202, state `waiting_for_repository`)."""
        who = request.state.identity
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        github, missing = repository_on_github(bot)
        history = history_on_github(bot)

        def work(c):
            # An external agent (a Hermes profile) has a credential, not a computer.
            placed = (
                {"computer": None, "placed": False}
                if agents.external_harness(c, bot)
                else place_now(c, who, bot, body.computer, history)
            )
            if placed.get("proposed"):
                return placed  # a computer that needs the person's own click: nothing else yet
            row = H.bot(c, bot)
            if row["state"] == "quarantined":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            building = False
            if not agents.external_harness(c, bot):
                if not repository_present(c, bot):
                    declared = onboarding._declared(c, bot)
                    if settings_admin.computer_builds_repository(c, bot):
                        building = True
                    elif github == "present" or (declared.get("template") and declared.get("setup_task_id")):
                        # GitHub has it, or BotOps has its build in hand (its setup task, often the very run asking):
                        # the computer gets it and its report finishes this go-live. Attaching the template again
                        # here would park a starter in Onboarding behind a person.
                        return wait_for_repository(c, who, bot, placed, body)
                    elif declared.get("template"):
                        if not H.bot(c, "botops"):
                            raise Problem(
                                "repository_missing", "Its repository is not built yet. Add BotOps to build it", 409
                            )
                        build = onboarding.attach_template(
                            c, who, bot, declared["template"], declared.get("instructions") or "", queue_build=True
                        )
                        if settings_admin.computer_builds_repository(c, bot):
                            building = True  # a starter: its computer builds it from the template, so it goes live now
                        else:
                            # BotOps builds it on its setup task; the computer's report of it finishes this go-live.
                            return {
                                **wait_for_repository(c, who, bot, placed, body),
                                **build,
                                "building": True,
                                "note": "BotOps is building its repository; it goes live by itself when it is built",
                            }
                    elif missing is not None:
                        raise Problem(
                            "repository_missing",
                            missing.detail.rstrip(".") + f". Then run `tico bot go-live {row['slug']}` again",
                            409,
                        )
                    elif bot_repository(c, settings, bot):
                        # GitHub could not be asked: the computer tries the clone and says why if it cannot.
                        return wait_for_repository(c, who, bot, placed, body, github_asked=False)
                    else:
                        raise Problem(
                            "repository_missing",
                            "It has no repository yet. Ask BotOps to build it "
                            f"(`tico bot repo-create {row['slug']}`), then run `tico bot go-live {row['slug']}` again",
                            409,
                        )
            done = finish_go_live(c, who, bot, body.setup)
            note = "The computer is building its repository; queued work starts when it is ready" if building else ""
            if placed.get("waiting"):
                # Its cloud machine is on its way: the bot is live, and its work starts once that computer is up.
                note = f"{placed['computer']} is starting; its work starts once it is up" + (
                    " and has built its repository" if building else ""
                )
            return {
                "bot": bot,
                "state": H.bot(c, bot)["state"],
                "computer": placed["computer"],
                "placed": placed["placed"],
                **done,
                **({"building": True} if building else {}),
                **({"waiting_for_computer": True} if placed.get("waiting") else {}),
                **({"note": note} if note else {}),
            }

        result = mutate(request, body, work)
        if isinstance(result, dict) and result.get("state") == "waiting_for_repository":
            return JSONResponse(result, status_code=202)
        return result
