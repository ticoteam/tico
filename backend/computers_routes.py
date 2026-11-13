"""Computer routes: enrollment, heartbeat, runner logins and harness actions, computer keys, revoke."""

import json
from fastapi import Request
from . import (
    bot_settings as BS,
    harness_actions,
    inbox_isolation,
    model_login,
    models as M,
    providers as Providers,
    runner_versions,
    views,
)
from .auth import Identity, validate_identity
from .execution import runner_repository
from .store import H, P, Problem


def register(app, auth, execution, mutate, settings, store, telemetry):
    @app.post("/api/v2/enrollments")
    def issue_enrollment(request: Request, body: M.EnrollmentRequest):
        return mutate(request, body, lambda c: execution.issue_enrollment(c, request.state.identity, body))

    @app.post("/api/v2/runners/enroll")
    def enroll(body: M.Enrollment):
        return execution.enroll(body)

    @app.post("/api/v2/runners/errors")
    def runner_errors(request: Request, body: M.RunnerErrors):
        """Where a runner panicked, for the install's opt-in error reporting; dropped when that is off."""
        if request.state.identity.role != "runner":
            raise Problem("identity", "A registered runner is required", 403)
        telemetry.capture_runner(request.state.identity.runner_id, body.version, body.panics)
        return {"ok": True}

    @app.post("/api/v2/runners/heartbeat")
    def heartbeat(request: Request, body: M.Heartbeat):
        from .worktrees import live_turn_cache

        # Keep the expensive, repeated provenance walks off the write lock. As for claim, a concurrent
        # commit invalidates this selection and the write falls back to current state under its lock.
        with store.read() as snapshot:
            validate_identity(snapshot, request.state.identity)
            execution.runner(snapshot, request.state.identity)
            version = snapshot.execute("PRAGMA data_version").fetchone()[0]
            snapshot.execute("BEGIN")
            try:
                selected = live_turn_cache(snapshot, request.state.identity.runner_id)
            finally:
                snapshot.rollback()

            def work(c):
                from . import provenance_clock

                clock = selected.get(("provenance_clock",))
                unchanged = (
                    provenance_clock.current(c) == clock
                    if clock is not None
                    else snapshot.execute("PRAGMA data_version").fetchone()[0] == version
                )
                return execution.heartbeat(c, request.state.identity, body, live_tasks=selected if unchanged else None)

            result = mutate(request, body, work)
        # No bot list: every runner the server accepts (0.4.0 on) reads its bots from GET runners/assignments when a
        # `runner` change of kind "assignments" tells it to, and never read this one (at 53 bots it was ~0.5 MB a beat).
        # Kept as an empty list for the 0.5.0 and 0.6.0 contracts, which require the field (contracts/runner/releases).
        result["assignments"] = []
        return result

    @app.get("/api/v2/runners/desired")
    def desired_runner_release(request: Request):
        with store.read() as c:
            execution.runner(c, request.state.identity)
        # `screen_v1`: this server takes the capability and `readiness.screen` (an older one refuses the heartbeat).
        # `limit_resets_at`: a run's result may say when its usage limit resets (an older one refuses the field).
        # `usage_requests`: a run's usage may carry each model request's tokens (an older one refuses the field).
        # `usage_request_main`: ... and a fourth count marking the bot's own requests (an older one refuses it).
        # `usage_cache_writes`: a run's usage may carry `cache_write_tokens` apart from `input_tokens` (an older one refuses
        # the field; the runner then counts cache writes in input, as before).
        # `run_pushes`: a run's result may list the pushes its task worktrees made (an older one refuses the field).
        # `run_rotation`: a run's result may say it started a fresh session and why (an older one refuses the field).
        # `run_session`: a run's result may say whether it resumed its session or started a new one, and why (an older
        # one refuses the field). The claim's config may carry `compact_first` for a runner that sends it.
        # `load_snapshot`: start and completion may carry an instantaneous load reading.
        # `attempt_thread`: a running attempt may name its thread after its start (`attempts/{aid}/thread`), once the
        # harness confirmed a new session (an older one has no such route).
        # `capacity_continue`: a run the model's capacity stopped after it acted may say its thread was kept
        # (`continues`), and a claim may name that thread (`continue_thread`) for the next turn to continue.
        return {
            **runner_versions.desired(),
            "features": {
                "task_files_multipart": True,
                "runner_commit": True,
                "screen_v1": True,
                "limit_resets_at": True,
                "usage_requests": True,
                "usage_request_main": True,
                "usage_cache_writes": True,
                "run_pushes": True,
                "run_rotation": True,
                "run_session": True,
                "run_diagnostics": True,
                "load_snapshot": True,
                "attempt_thread": True,
                "runner_drain": True,
                "capacity_continue": True,
                "source_continue": True,
            },
        }

    @app.get("/api/v2/runners/assignments")
    def assigned(request: Request):
        with store.read_transaction() as c:
            return execution.assigned(c, request.state.identity)

    @app.get("/api/v2/runners/eligible")
    def eligible(request: Request):
        with store.read() as c:
            runner = execution.runner(c, request.state.identity)
            rows = c.execute(
                f"SELECT bc.bot,bc.team,bc.owner_id,{BS.CONFIG} AS config,x.state,"
                "coalesce(a.generation,0) AS generation,a.runner_id "
                f"FROM bot_config bc {BS.JOIN} JOIN bots x ON x.id=bc.bot LEFT JOIN assignments a ON a.bot=bc.bot "
                "WHERE bc.owner_id=? ORDER BY bc.bot",
                (runner["owner_id"],),
            ).fetchall()
            result = []
            company = Providers.load(c, settings)
            from .repositories import Homes

            homes = Homes(c)
            for row in rows:
                value = dict(row)
                from .shared_bots import follow

                value["config"] = Providers.fill(company, follow(c, row["bot"], json.loads(value.pop("config"))))
                value["repository"] = runner_repository(c, settings, row["bot"], homes=homes)
                result.append(value)
            return result

    @app.post("/api/v2/runners/adopt")
    def adopt(request: Request, body: M.Adopt):
        who = request.state.identity

        def work(c):
            runner = execution.runner(c, who)
            bot = (H.resolve_bot(c, body.bot) or {}).get("id", body.bot)
            row = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if not row or row["owner_id"] != runner["owner_id"]:
                raise Problem("forbidden", "This bot is outside your enrolled operator's scope", 403)
            # Enrollment delegates hosting of the operator's assigned team, never human data access.
            operator = Identity("human:" + runner["owner_id"], "human")
            result = execution.assign(
                c, operator, bot, M.Assignment(runner_id=who.runner_id, expected_generation=body.expected_generation)
            )
            H.event(c, who.actor, "runner.adopt", bot)
            return result

        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/restart")
    def restart_runner(request: Request, rid: str, body: M.Empty):
        """Restart a computer's runner on the latest code. The runner pulls
        main when that is safe and restarts once no turn is running, so nothing is cut off."""
        who = request.state.identity

        def work(c):
            runner = c.execute("SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not runner or not (
                auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["owner_id"]
            ):
                raise Problem("forbidden", "You cannot restart this runner", 403)
            c.execute("UPDATE runners SET restart_requested_at=? WHERE id=?", (H.now(), rid))
            H.event(c, who.actor, "runner.restart", rid)
            return {"requested": True, "running": views.runner_busy(c, rid)}

        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/inbox-only")
    def inbox_only(request: Request, rid: str, body: M.InboxOnly):
        """Keep this computer for inbox bots, or stop (backend/inbox_isolation.py)."""
        who = request.state.identity

        def work(c):
            runner = c.execute("SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not runner or not (
                auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["owner_id"]
            ):
                raise Problem("forbidden", "You cannot change this computer", 403)
            people = views.roster(c)
            if body.enabled and (
                others := [
                    b
                    for b in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (rid,)).fetchall()
                    if not P.inbox_person(b[0], people)
                ]
            ):
                raise Problem(
                    "inbox_isolation",
                    "Move these bots to another computer first: " + ", ".join(b[0] for b in others),
                    409,
                )
            inbox_isolation.set_inbox_only(c, rid, body.enabled, people)
            H.event(c, who.actor, "runner.inbox_only", rid, {"enabled": body.enabled})
            return {"enabled": body.enabled}

        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/logins")
    def start_login(request: Request, rid: str, body: M.LoginStart):
        return mutate(
            request,
            body,
            lambda c: model_login.start(
                c, request.state.identity, rid, body.runtime, body.profile, auth=auth, switch=body.switch, to=body.to
            ),
        )

    # Sign a login out (the computer's own, or a named subscription's); it is read back like a sign-in.
    @app.post("/api/v2/runners/{rid}/logouts")
    def start_logout(request: Request, rid: str, body: M.LogoutStart):
        return mutate(
            request,
            body,
            lambda c: model_login.sign_out(c, request.state.identity, rid, body.runtime, body.profile, auth=auth),
        )

    # The bots a sign-out would stop, for the confirmation.
    @app.get("/api/v2/runners/{rid}/logouts/bots")
    def logout_bots(request: Request, rid: str, runtime: str, profile: str = ""):
        with store.read() as c:
            return model_login.affected_bots(
                c, request.state.identity, rid, runtime, profile, store.settings, auth=auth
            )

    # Take a named subscription off a computer (refused, naming them, while bots run on it); read back like a sign-in.
    @app.post("/api/v2/runners/{rid}/removals")
    def start_removal(request: Request, rid: str, body: M.RemovalStart):
        return mutate(
            request,
            body,
            lambda c: model_login.remove(
                c, request.state.identity, rid, body.runtime, body.profile, store.settings, auth=auth
            ),
        )

    # A signed-out computer login takes the company's shared key again.
    @app.post("/api/v2/runners/{rid}/shared-key")
    def use_shared_key(request: Request, rid: str, body: M.SharedKeyUse):
        if body.credential:
            return mutate(
                request,
                body,
                lambda c: model_login.attach_shared_key(
                    c, request.state.identity, rid, body.runtime, body.credential, auth=auth, vault=app.state.vault
                ),
            )
        return mutate(
            request, body, lambda c: model_login.use_shared_key(c, request.state.identity, rid, body.runtime, auth=auth)
        )

    # Clear key / Replace key (backend/computer_keys.py). What signs it in, and for the company's shared key every
    # computer it serves: the "this computer only / all of them" ask. Previews only.
    @app.get("/api/v2/runners/{rid}/keys")
    def computer_key_info(request: Request, rid: str, runtime: str):
        with store.read() as c:
            return model_login.key_info(c, request.state.identity, rid, runtime, auth=auth, vault=app.state.vault)

    @app.post("/api/v2/runners/{rid}/keys/clear")
    def clear_computer_key(request: Request, rid: str, body: M.KeyClear):
        return mutate(
            request,
            body,
            lambda c: model_login.clear_key(c, request.state.identity, rid, body.runtime, body.scope, auth=auth),
        )

    # The key goes to the vault, never into the login relay; the vault's own write path keeps it out of the request
    # receipt (a keyed fingerprint, as for Credentials).
    @app.post("/api/v2/runners/{rid}/keys")
    def replace_computer_key(request: Request, rid: str, body: M.KeyReplace):
        who = request.state.identity
        vault = app.state.vault
        if body.share and body.switch:
            raise Problem(
                "switch",
                "Switch to API key gives this computer a key of its own; share a key from Add connection instead",
                422,
            )
        return vault.change(
            who,
            request.url.path,
            request.headers.get("idempotency-key"),
            body,
            lambda c, w: None,
            lambda c: (
                model_login.share_key(
                    c,
                    who,
                    rid,
                    body.runtime,
                    body.secret.get_secret_value(),
                    body.env,
                    body.share,
                    auth=auth,
                    vault=vault,
                )
                if body.share
                else model_login.replace_key(
                    c,
                    who,
                    rid,
                    body.runtime,
                    body.secret.get_secret_value(),
                    body.env,
                    body.scope,
                    auth=auth,
                    vault=vault,
                    switch=body.switch,
                )
            ),
        )

    @app.get("/api/v2/runners/{rid}/logins/{lid}")
    def read_login(request: Request, rid: str, lid: str):
        # A read that also expires: a sign-in nobody finished does not stay open. Polled while a sign-in runs, so the
        # write lock is taken only when something has run out.
        with store.read() as c:
            if not model_login.sweep_due(c):
                return model_login.read(c, request.state.identity, rid, lid, auth=auth, swept=False)
        with store.transaction() as c:
            return model_login.read(c, request.state.identity, rid, lid, auth=auth)

    @app.post("/api/v2/runners/{rid}/logins/{lid}/code")
    def login_code(request: Request, rid: str, lid: str, body: M.LoginCode):
        return mutate(
            request, body, lambda c: model_login.submit_code(c, request.state.identity, rid, lid, body.code, auth=auth)
        )

    @app.post("/api/v2/runners/{rid}/logins/{lid}/cancel")
    def cancel_login(request: Request, rid: str, lid: str, body: M.Empty):
        return mutate(request, body, lambda c: model_login.cancel(c, request.state.identity, rid, lid, auth=auth))

    # The runner asks for work here and reports back; nothing listens on the runner.
    @app.get("/api/v2/runner-logins")
    def runner_logins(request: Request):
        who = request.state.identity
        with store.read() as c:
            execution.runner(c, who)
            if not model_login.sweep_due(c):
                return {"logins": model_login.pending(c, who.runner_id, swept=False)}
        with store.transaction() as c:
            execution.runner(c, who)
            return {"logins": model_login.pending(c, who.runner_id)}

    @app.post("/api/v2/runner-logins/{lid}/report")
    def report_login(request: Request, lid: str, body: M.LoginReport):
        who = request.state.identity
        return mutate(
            request,
            body,
            lambda c: (execution.runner(c, who), model_login.report(c, who.runner_id, lid, body, store.settings))[1],
        )

    @app.post("/api/v2/runners/{rid}/harness-actions")
    def harness_action(request: Request, rid: str, body: M.HarnessAction):
        return mutate(
            request, body, lambda c: harness_actions.request(c, request.state.identity, rid, body.harness, body.action)
        )

    @app.post("/api/v2/runners/{rid}/harness-choices")
    def harness_choice(request: Request, rid: str, body: M.HarnessChoice):
        return mutate(
            request,
            body,
            lambda c: harness_actions.choose(c, request.state.identity, rid, body.harness, body.enabled, settings),
        )

    @app.get("/api/v2/runner-harness-actions")
    def runner_harness_actions(request: Request):
        who = request.state.identity
        with store.read() as c:
            execution.runner(c, who)
            if not harness_actions.sweep_due(c):
                return {
                    "actions": harness_actions.pending(c, who.runner_id, swept=False),
                    "choices": harness_actions.choices(c, who.runner_id),
                }
        with store.transaction() as c:
            execution.runner(c, who)
            return {
                "actions": harness_actions.pending(c, who.runner_id),
                "choices": harness_actions.choices(c, who.runner_id),
            }

    @app.post("/api/v2/runner-harness-actions/{aid}/report")
    def report_harness_action(request: Request, aid: str, body: M.HarnessActionReport):
        who = request.state.identity
        return mutate(
            request, body, lambda c: (execution.runner(c, who), harness_actions.report(c, who.runner_id, aid, body))[1]
        )

    @app.post("/api/v2/runners/{rid}/revoke")
    def revoke(request: Request, rid: str, body: M.Empty):
        from . import computers

        who = request.state.identity

        def work(c):
            runner = c.execute("SELECT * FROM runners_with_state WHERE id=?", (rid,)).fetchone()
            if not runner or not (
                auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["owner_id"]
            ):
                raise Problem("forbidden", "You cannot revoke this runner", 403)
            computers.revoke(c, who.actor, rid)
            return {"revoked": True}

        result = mutate(request, body, work)
        computers.disconnect(store, rid)
        return result
