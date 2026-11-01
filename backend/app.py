"""Authenticated, deterministic API. No runtime launch or local-machine dependency."""

import asyncio
import logging
import time
import json
from dataclasses import replace
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlparse

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from clients.agent_skill import WHO_NEEDS_ME

from . import go_live as pending_go_lives
from . import repo_waits
from . import built_on
from . import agents, app_delivery, batch, external_sync, inbox_isolation, lifecycle, mcp_oauth, recruiting, harness_actions, model_login, oidc, personal_tokens, views
from . import outcome_stats
from . import start_places
from . import ask_ingest, delivery, strategy_bot, invites, owner_link
from . import org_rule_check
from .roadmaps import exact_approval
from . import login_holds
from . import cloud_access
from . import counsel
from . import step_age
from . import bot_stops, turn_budgets, proposals, task_caps
from . import (auto_tasks, check_models, profiles, run_triggers, task_models, task_run_models, team_rules, update_feeds,
               topic_change, usage_limits, bot_to_bot, wake_presets, visuals, wake_rules, quiet_work, milestone_waits)
from . import task_privacy as privacy
from . import task_relations as TR
from . import task_roles as TRo
from . import placement as placing
from .statuses import is_parked
from . import turns as turn_work
from . import goals as G
from . import models as M
from .auth import LOCAL_COOKIE, LOCAL_COOKIE_DAYS, LOCAL_SIGNIN_PATH, LOGOUT_PATH, Auth, Identity, validate_identity
from .config import Settings
from .observability import Observability, browser_config, staff_display_name, route as error_route
from .execution import (ClaimFaults, Execution, bot_repository, bump_bot_session_epoch, claim_scope, clear_limits,
                        flag_cut, lift_after_model_change, model_choice, own_login_hold)
from .onboarding import BOTOPS, Onboarding
from .recruit import Recruiter
from . import rooms
from . import fast_json
from . import names as actor_names
from .openapi_v2 import STABLE as STABLE_ROUTES
from .route_renames import old_paths as old_route_paths
from . import updates
from .files import raw_media
from clients import gateway as gateway_client
from . import company_billing, company_server, gateway_connection, gateway_page, gateway_tiers
from . import providers as Providers
from . import access as Access
from . import people_remove
from . import hosted_updates, releases, runner_versions, ui_bundle
from .bot_rows import BotRows
from .census import Census
from .harnesses import HARNESS_CATALOG, resolve_harness, runtime_of
from .providers import MODEL_BY_ID, MODEL_CATALOG
from . import report_managers
from .settings_admin import SettingsAdmin, compact_at as compact_at_of, repository_present
from .readiness import absolute as absolute_link, can_run
from . import fleet_check as fleet_check_module
from .getting_started import _online_runners
from .screen import listing as screen_listing
from .performance import listing as performance_listing
from .credential_cards import install_credential_cards, scrub_attempt
from .github import PATH as GITHUB_WEBHOOK_PATH
from .triggers import PATH as TRIGGER_HOOK_PATH
from .store import H, P, Problem, Store, bot_readiness, encode, message_page, refused, repo_url, task_message_page




# The stable v2 routes whose answers carry display names (backend/names.py); streams are left alone.
STABLE_PATH = re.compile("|".join(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(spelling))
                                  for path, *_ in STABLE_ROUTES if path != "/api/v2/events"
                                  for spelling in (path, *old_route_paths(path))))


# The inbox sends at most this many messages and this many tasks; the rest wait for a later read.
INBOX_LIMIT = 100
INBOX_PAGE = 500            # unread messages read per query while looking for the newest INBOX_LIMIT readable ones
# Gateway image calls in flight per process (each holds a thread up to 130 s); more answer 429 with Retry-After.
GATEWAY_IMAGE_SLOTS = 4


def _first_visible(items, visible):
    """The first `INBOX_LIMIT` of `items` that pass `visible`, and whether one more item passes it too: a hidden item
    never sets the flag. `items` may be a generator, read only as far as that."""
    kept = []
    for item in items:
        if visible(item):
            if len(kept) == INBOX_LIMIT:
                return kept, True
            kept.append(item)
    return kept, False


def reset_bot_sessions(c, bot):
    """Keep durable history while fencing local provider sessions for one bot."""
    bump_bot_session_epoch(c, bot)
    reset = 0
    for conversation in c.execute("SELECT id,participants_json FROM conversations").fetchall():
        if "bot:" + bot not in json.loads(conversation["participants_json"]):
            continue
        c.execute("INSERT INTO session_epochs VALUES(?,1,?) ON CONFLICT(conversation_id) DO UPDATE SET "
                  "epoch=epoch+1,updated=excluded.updated", (conversation["id"], H.now()))
        reset += 1
    return reset


def create_app(settings=None):
    settings = settings or Settings.from_env()
    telemetry = Observability(settings)
    from . import flight
    recorder = flight.Recorder() if settings.flight_recorder else None
    store = Store(settings, recorder)
    census = Census(store, settings)
    releases.CHECKER.bind(census)
    auth = Auth(store)
    execution = Execution(store, auth)
    settings_admin = SettingsAdmin(store, auth, execution, MODEL_BY_ID, reset_bot_sessions)
    onboarding = Onboarding(store, auth, settings_admin, execution, MODEL_BY_ID)
    recruiter = Recruiter(store, auth, settings)
    execution.runner_enrolled = onboarding.on_runner_enrolled

    @asynccontextmanager
    async def lifespan(app):
        # The default pool is min(32, cpus + 4) threads, about 6 on the server. Long waits (every
        # open chat stream's polls, uploads) filled it, and
        # every request's sign-in queued behind them for seconds. Give the loop a
        # roomier pool, and sign-in its own (AUTH_POOL below).
        from concurrent.futures import ThreadPoolExecutor
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=64, thread_name_prefix="tico"))
        store.initialize()
        # A cloud agent request a stopped process left filing is released before any request is served.
        from . import cloud_agents
        cloud_agents.release_abandoned(store)
        # Files go to the company's own bucket from the first request, when the owner set one.
        from . import company_storage
        company_storage.startup(store, app.state.vault, app.state.blobs)
        # Finance seals payees' bank details with this app's vault, timers or not (banking_start registers it again).
        from .finance import actions as finance_vault
        finance_vault.use_vault(app.state.vault)
        cloud_access.current(store)        # a lock kept from before the restart holds from the first request
        asyncio.get_running_loop().run_in_executor(None, auth.warm)   # Cloudflare's keys, before anyone signs in
        asyncio.get_running_loop().run_in_executor(None, app.state.vault.cipher.warm, store)   # the KMS data key
        # TICO_SAMPLE_COMPANY=on: a new company starts as the sample company, once (backend/sample_company.py).
        if settings.company_file_configured or settings.company_file_data:
            # A file that can't be applied is kept for the owner to see; never a sample company in its place.
            from . import company_import
            company_import.start(onboarding, store, telemetry)
        else:
            # The file setting was removed: a failure kept from it no longer describes this install.
            from . import company_import
            company_import.forget(store)
        if not (settings.company_file_configured or settings.company_file_data) and settings.sample_company:
            try:
                from . import sample_company
                with store.transaction() as c:
                    sample_company.load_on_start(onboarding, c)
            except Exception as exc:
                telemetry.capture("sample_company", exc)
        # A company from before the Librarian was built in gets it on update, once it can run it.
        try:
            with store.transaction() as c:
                onboarding.ensure_librarian(c)
                onboarding.ensure_goal_manager(c)
                onboarding.ensure_task_manager(c)
                onboarding.ensure_counsel(c)
        except Exception as exc:
            telemetry.capture("librarian", exc)
        # Finance on every install (backend/finance/setup.py): idempotent, after the migrations, one short transaction.
        try:
            from .finance import actions as finance
            with store.transaction() as c:
                finance.ensure_setup(c, settings.owner_email)
        except Exception as exc:
            telemetry.capture("finance_setup", exc)
        # The Bookkeeper once Finance is set up (backend/bookkeeper.py): built if missing, its rights, one view ask.
        try:
            from . import bookkeeper
            with store.transaction() as c:
                bookkeeper.ensure(c, onboarding, auth)
        except Exception as exc:
            telemetry.capture("bookkeeper", exc)
        # Physical mail filed before bills and tax letters were private: those tasks go private, once.
        try:
            app.state.postal_mail.backfill_private()
        except Exception as exc:
            telemetry.capture("postal_mail_backfill", exc)
        # The Librarian gets the market posts from Listening, once (Settings > Listening).
        try:
            from . import listening
            with store.transaction() as c:
                listening.seed_market(c, settings)
        except Exception as exc:
            telemetry.capture("listening", exc)
        # A strategy research run the last process was doing has no thread now: it stops, and setup shows the form.
        try:
            from . import strategy_setup
            with store.transaction() as c:
                strategy_setup.stop_running(c)
        except Exception as exc:
            telemetry.capture("strategy_setup", exc)
        # A lens run the last process was making has no thread now: it fails, and the run page says so.
        try:
            from . import lenses
            with store.transaction() as c:
                lenses.stop_running(c)
        except Exception as exc:
            telemetry.capture("lenses", exc)
        # An assistant still named after the company becomes "Assistant" (idempotent).
        try:
            with store.transaction() as c:
                onboarding.name_default_assistant(c)
        except Exception as exc:
            telemetry.capture("assistant_name", exc)
        # A message bot made before the server linked it to its person is linked now, when there is no doubt whose it is.
        try:
            from . import message_bots
            with store.transaction() as c:
                message_bots.backfill(c)
        except Exception as exc:
            telemetry.capture("message_bots", exc)
        # This release may contain merged pull requests attached to tasks: their links become shipped (backend/github.py).
        try:
            from .github import ship_deployed
            with store.transaction() as c:
                ship_deployed(c, settings)
        except Exception as exc:
            telemetry.capture("github", exc)
        # The runs the last process still held were cut by its stop, unless their computers carry on: flagged, they
        # resume on the next sweep if they lapse (execution.cut_by_update).
        try:
            with store.transaction() as c:
                flag_cut(c, "restart")
        except Exception as exc:
            telemetry.capture("flag_cut", exc)
        # A deploy keeps the API down longer than a lease; do not fail turns for that.
        execution.grace_leases()
        # A live support session picks its link to HQ back up (backend/support_access.py).
        try:
            from . import support_access
            support_access.started(app, asyncio.get_running_loop())
        except Exception as exc:
            telemetry.capture("support_access", exc)
        telemetry.start()
        stop = asyncio.Event()
        # Always runs: local 90-day retention must not depend on the inbound key or HQ (sync itself stays off).
        inbound_task = asyncio.create_task(app.state.inbound.loop(stop))
        screen_relay_task = None
        if settings.screen_relay_url:
            await asyncio.to_thread(screen_sessions.start)
            screen_relay_task = asyncio.create_task(screen_sessions.run(stop))
        from .timing import watch_loop
        timing_task = asyncio.create_task(watch_loop(app.state.timing, stop))
        recorder_thread = flight.Flight(recorder, store, settings) if recorder else None
        if recorder_thread:
            recorder_thread.start()
            # SIGUSR1 (stacks) and SIGUSR2 (memory top 50) for `python -m backend.flight stacks|memory`.
            from .flight_file import install_signals
            install_signals(recorder_thread.folder, recorder_thread.file)
        async def schedule_loop():
            from .scheduler import Scheduler
            scheduler = Scheduler(store, execution)
            def github_wakes():
                from .github import flush_wakes, reconcile_worktree_prs, refresh_deployed_tasks, refresh_pending_checks
                with store.transaction() as c:
                    flush_wakes(c)
                refresh_deployed_tasks(app.state.github_app)
                # Checks pending with no word from GitHub are read afresh: a suite that never starts sends none (Dev 646).
                refresh_pending_checks(app.state.github_app)
                # A worktree is released by its pull requests' state, so a missed webhook must heal unread.
                reconcile_worktree_prs(app.state.github_app)
                # A merge or release a task waits for whose webhook never came is asked of GitHub (Dev 256).
                milestone_waits.recover(app.state.github_app)
                # A review move refused for pending checks or a missing receipt, retried once that clears (Dev 534 C).
                from . import move_retry
                move_retry.tick(app.state.github_app)
                # A gate hold whose current head has every hosted check passing clears on a fresh GitHub read.
                from . import task_blocked
                task_blocked.fresh(app.state.github_app)
            from . import system_routines
            def recorded(key, work):
                # One system routine's run, recorded for Settings > Routines (backend/system_routines.py).
                with system_routines.recording(store, key) as run:
                    work()
                    run.did("Done", quiet=True)
            def repository_waits():
                from .repositories import daily
                daily(app.state.github_app)
                # A bot waiting for its GitHub repository finishes once a person has made it or granted the permission.
                from . import repo_waits
                repo_waits.tick(app.state.github_app)
                # A task waiting for room for a worktree whose freeing event was lost (backend/worktree_waits.py).
                from . import worktree_waits
                worktree_waits.tick(store, settings.github_owner)
                # A bot left in setup for a day: its person hears once (backend/setup_stuck.py).
                from . import setup_stuck
                setup_stuck.tick(store)
            def release_check():
                # The release check (and the anonymous count that rides on it) runs even when nobody has the
                # page open; it is a no-op until its six hours are up.
                releases.notice()
                # Subscribed updates for bots, as quiet notes; nothing is fetched while no bot subscribes.
                update_feeds.tick(store)
            while not stop.is_set():
                try:
                    await asyncio.to_thread(recorded, "github_sync", github_wakes)
                except Exception as exc:
                    telemetry.capture("github_wakes", exc)
                try:
                    await asyncio.to_thread(scheduler.tick)
                    await asyncio.to_thread(recorded, "release_check", release_check)
                    await asyncio.to_thread(recorded, "repository_waits", repository_waits)
                except Exception as exc:
                    telemetry.capture("scheduler", exc)
                    import logging
                    logging.getLogger("tico.scheduler").error("Scheduler tick failed: %s", type(exc).__name__)
                try:
                    # Live-meeting windows a lost routing thread (a restart) had not routed (backend/live_meetings.py).
                    await asyncio.to_thread(app.state.live_meeting_routing.sweep)
                except Exception as exc:
                    telemetry.capture("live_meeting_sweep", exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10)
                except TimeoutError:
                    pass
        async def directory_loop():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(app.state.directory.tick, True)
                except Exception as exc:
                    telemetry.capture("directory", exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except TimeoutError:
                    pass
        # A rehearsal never runs them, whatever else says so (docs/install.md, "Rehearse a migration").
        timers = settings.scheduler_enabled and not settings.rehearsal
        # Outgoing webhooks (backend/webhook_delivery.py): a rehearsal copy runs the workers too, only to mark what is
        # due as withheld, so nothing queued there ever waits for a send that cannot happen.
        webhooks_on = timers or settings.rehearsal
        if webhooks_on:
            from . import webhook_delivery
            webhook_delivery.start(store, app.state.vault, rehearsal=settings.rehearsal)
            # Finance's Gateway worker (backend/finance/gateway_sync.py): the key's self cap and its usage meter. A
            # rehearsal copy runs it only to record what it would send as withheld.
            from .finance import actions as finance
            finance.gateway_start(store, app.state.vault, settings, rehearsal=settings.rehearsal)
            # Finance's bank worker (backend/finance/sync.py): Mercury balances, feed, payments and card writes; a
            # rehearsal copy reads nothing and records its writes as withheld.
            finance.banking_start(store, app.state.vault, rehearsal=settings.rehearsal)
            # Finance's books worker (backend/finance/books_sync.py): QuickBooks writes out, backfill and change feed.
            from .finance import books_sync
            books_sync.start(store, app.state.vault, settings, rehearsal=settings.rehearsal)
        # Webhooks waiting at Tico Gateway; the loop does nothing without a relay or a key.
        hook_relay_task = asyncio.create_task(app.state.hook_relay.loop(stop)) if timers else None
        # Physical mail (backend/postal_mail.py): reads and routes the letters the relay and the mail-intake inbox kept.
        postal_mail_task = asyncio.create_task(app.state.postal_mail.loop(stop)) if timers else None
        # Sleep when idle (backend/sleep_idle.py): looks every five minutes (a cell: every minute), and only on a hosted server that asked for it.
        sleep_idle_task = asyncio.create_task(app.state.sleep_idle.loop(stop)) if timers else None
        # Bots' own cloud machines (backend/cloud_machines.py): started for work, stopped when idle; Tico Cloud only.
        cloud_machines_task = asyncio.create_task(app.state.cloud_machines.loop(stop)) if timers else None
        scheduler_task = asyncio.create_task(schedule_loop()) if timers else None
        directory_task = asyncio.create_task(directory_loop()) if timers else None
        granola_task = asyncio.create_task(app.state.granola.loop(stop)) if timers else None
        docs_task = asyncio.create_task(app.state.doc_sources.loop(stop)) if timers and not settings.demo else None
        # A demo runs no scheduler: nothing fires, and nothing waits for a bot that will never run.
        import threading
        copy_stop = threading.Event()
        copy_task = asyncio.create_task(asyncio.to_thread(app.state.blobs.storage_loop, store, copy_stop))
        # Answers an older server stored get their quoted-message index, beside the server (backend/idempotency_messages.py).
        from . import idempotency_messages
        index_stop = threading.Event()
        index_task = asyncio.create_task(asyncio.to_thread(idempotency_messages.backfill_store, store, index_stop))
        media_task = asyncio.create_task(asyncio.to_thread(app.state.file_metadata.loop))
        # History rows' read dependencies, settled beside the server (backend/read_deps.py); a read of a row not
        # settled yet walks its provenance as before.
        from . import read_deps
        reads_stop = threading.Event()
        reads_task = asyncio.create_task(asyncio.to_thread(read_deps.loop, store, reads_stop)) if timers else None
        # Library › Files: search text for files published before it existed, and bytes a hard delete still has to
        # remove from storage (backend/file_library.py).
        from . import file_library
        library_stop = threading.Event()
        library_task = (asyncio.create_task(asyncio.to_thread(file_library.loop, store, app.state.blobs, library_stop))
                        if timers else None)
        demo_task = None
        if settings.demo:
            from . import demo
            demo_task = asyncio.create_task(demo.keep_alive(store, stop, app.state.vault))
        try:
            yield
        finally:
            stop.set()
            app.state.live_meeting_routing.stop()
            if inbound_task:
                await inbound_task
            if hook_relay_task:
                await hook_relay_task
            if postal_mail_task:
                await postal_mail_task
            if sleep_idle_task:
                await sleep_idle_task
            if cloud_machines_task:
                await cloud_machines_task
            if screen_relay_task:
                await screen_relay_task
            else:
                screen_sessions.http.close()
            app.state.doc_sources.stop.set()
            copy_stop.set()
            index_stop.set()
            reads_stop.set()
            library_stop.set()
            app.state.file_metadata.stop.set()
            app.state.file_metadata.wake.set()
            # The storage probe observes copy_stop even while its bounded SDK call is in flight.
            await copy_task
            await index_task
            if reads_task:
                await reads_task
            if library_task:
                await library_task
            await media_task
            app.state.github_app.repository_stop.set()
            if demo_task:
                await demo_task
            # The lag probe sleeps half a second at a time; cancel it rather than wait out the sleep.
            timing_task.cancel()
            try:
                await timing_task
            except asyncio.CancelledError:
                pass
            if docs_task:
                await docs_task
            if scheduler_task:
                await scheduler_task
            if directory_task:
                await directory_task
            if granola_task:
                await granola_task
            else:
                await app.state.granola.close()
            from .repositories import stop_sync
            await asyncio.to_thread(stop_sync, app.state.github_app)
            if recorder_thread:
                await asyncio.to_thread(recorder_thread.stop)
            if webhooks_on:
                await asyncio.to_thread(webhook_delivery.stop)
                await asyncio.to_thread(finance.gateway_stop)
                await asyncio.to_thread(finance.banking_stop)
                from .finance import books_sync as books_worker
                await asyncio.to_thread(books_worker.stop)
            telemetry.close()

    app = FastAPI(title=settings.app_name + " API", version="2.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.auth, app.state.execution = store, auth, execution
    app.state.onboarding = onboarding      # tests build a picked team as bots, as the sample company does
    from .ui_compression import UiCompression
    app.add_middleware(UiCompression)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Field names and reasons are useful; invalid input can hold credentials and is never echoed.
        def reason(error):
            if (error["type"] == "extra_forbidden" and tuple(error["loc"])[-1:] == ("manager",)
                    and request.url.path.startswith("/api/v2/task-types")):
                return M.MANAGER_FIXED
            return error["msg"]
        detail = "; ".join(".".join(str(part) for part in error["loc"]) + ": " + reason(error)
                           for error in exc.errors())
        return JSONResponse({"error": {"code": "validation", "detail": detail, "retryable": False}}, status_code=422)

    app.state.observability = telemetry
    app.state.census = census

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        if getattr(exc, "payload", None) is not None:
            # A refusal whose body is its own flat payload (task_caps.Capped: CONTRACT §1), not the usual error envelope.
            return JSONResponse(exc.payload, status_code=exc.status)
        if exc.status >= 500:
            telemetry.capture("request", exc, exc.status, route=error_route(request))
            request.state.telemetry_captured = True
        caller = getattr(request.state, "privacy_source", None)
        if caller is not None and caller.actor == "bot:" + BOTOPS and exc.status == 403:
            # A bot handed BotOps a person's request that lends nothing: the person gets a Confirm card, not a refusal.
            card = await asyncio.get_running_loop().run_in_executor(None, confirm_for_carried, request, exc)
            if card:
                return JSONResponse(card)
        if (caller is not None and caller.actor == "bot:" + BOTOPS and exc.status in (403, 409)
                and "fix" not in getattr(exc, "extra", {})):
            # BotOps relays a refusal to a person: what to click, and where (botops_act.fix).
            acting = getattr(request.state, "identity", None) or caller
            def hint():
                parts = request.url.path.split("/")
                with store.read() as c:
                    names = actor_names.lookup(c, [acting.actor] + (["bot:" + parts[4]] if parts[3:4] == ["bots"]
                                                                     and len(parts) > 4 else []))
                return botops_act.fix(request.url.path, exc.code, exc.status, acting, names, settings.public_url)
            found = await asyncio.get_running_loop().run_in_executor(None, hint)
            if found:
                exc.extra = {**getattr(exc, "extra", {}), **found}
        if getattr(exc, "extra", None) and str(exc.extra.get("link") or "").startswith("#/"):
            exc.extra = absolute_link(exc.extra, settings.public_url)
        return JSONResponse({"error": {"code": exc.code, "detail": exc.detail,
                                       "retryable": exc.retryable, **getattr(exc, "extra", {})}},
                            status_code=exc.status)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_handler(request, exc):
        telemetry.capture("request", exc, 503, route=error_route(request))
        request.state.telemetry_captured = True
        # The write lock stayed taken past store.WRITE_WAIT: a load answer, not a fault. Clients retry with the same
        # Idempotency-Key (runner, clients/tico.py, ui/app/api.js), so the write lands once.
        if "locked" in str(exc) or "busy" in str(exc):
            return JSONResponse({"error": {"code": "busy", "detail": "Tico is busy; retry shortly", "retryable": True}},
                                status_code=503, headers={"Retry-After": "2"})
        # No SQL, database paths, or request bodies in client-facing diagnostics.
        return JSONResponse({"error": {"code": "storage_unavailable", "detail": "Please retry shortly",
                                       "retryable": True}}, status_code=503)

    # Bot text reaches the page as HTML; scripts run only from our own files (ui/app, ui/*.js), so a
    # tag that slips through a renderer cannot run. The page carries no inline script today; one that
    # is added to index.html is allowed by its hash, and any other inline script is refused.
    import base64
    import hashlib
    try:
        inline = re.findall(r"<script>(.*?)</script>", (settings.ui_dir / "index.html").read_text(), re.S)
    except OSError:
        inline = []
    hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(block.encode()).digest()).decode() + "'"
              for block in inline]
    PAGE_POLICY = ("script-src " + " ".join(["'self'", *hashes])
                   + "; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")

    from concurrent.futures import ThreadPoolExecutor
    from .timing import Timing
    AUTH_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="tico-auth")
    app.state.timing = Timing()
    app.state.timing.recorder = recorder
    app.state.flight = recorder

    from .assistant import write_allowed as assistant_writes, own_room as assistant_room, create_proposal
    from .assistant import valid_operation as assistant_valid
    from .assistant import describe as describe_action
    from .assistant import instructed_route as assistant_instructed, instruction as assistant_instruction
    from . import goal_read_back
    from . import assistant as assistant_mod
    from . import botops_act

    def assistant_owns(task_id, actor, shared=False):
        """The task is the person's alone: theirs, no bot on it and none delegated. With `shared`, bots may be on it
        (a comment on it still goes to no other person)."""
        with store.read() as c:
            row = H.task(c, task_id)
            if not row:
                return False
            people = (row["owner"], row["requester"], H.task_origin(c, row))
            if shared:
                return all(a == actor or not str(a).startswith("human:") for a in people)
            if row["owner"] != actor:
                return False
            if any(str(a).startswith("bot:") for a in people):
                return False
            return not c.execute("SELECT 1 FROM task_delegations WHERE task_id=? AND expires>?",
                                 (task_id, H.now())).fetchone()

    def assistant_to_bot(name):
        with store.read() as c:
            return str(H.resolve_actor(c, name) or "").startswith("bot:")

    def assistant_direct():
        with store.read() as c:
            return team_rules.load(c)["assistant_direct"]

    def assistant_decides(actor):
        with store.read() as c:
            return assistant_mod.decides(c, actor)

    @app.middleware("http")
    async def request_guard(request, call_next):
        via_reset = delegation_reset = run_reset = cause_reset = None
        try:
            # The sign-in exemptions below match on prefixes; a dot segment would carry one of
            # them onto the static files ("/download/../index.html").
            if any(part in (".", "..") for part in request.url.path.split("/")):
                raise Problem("not_found", "Not found", 404)
            if request.url.path.startswith('/public/roadmaps/'):
                from .public_roadmaps import guard as public_roadmap_guard
                return await public_roadmap_guard(request, call_next, store)
            if request.url.path == "/healthz":
                return await call_next(request)
            # A visual in a reply (backend/visuals.py): its signed token is the credential, and the app's cookies are
            # never read there. Its own headers stand: nothing below adds the app's framing or caching rules.
            if request.url.path.startswith("/visuals/") and request.method in ("GET", "HEAD"):
                return await call_next(request)
            if request.url.path == "/_tico/screen/v1/authorize":
                from .screen_relay import private_request
                if not private_request(request, settings):
                    raise Problem("forbidden", "Private relay key required", 403)
                chunks, total = [], 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > 16384:
                        raise Problem("too_large", "Relay authorization exceeds its limit", 413)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
                return await call_next(request)
            # Minting a loopback session is the one unauthenticated read: it carries its own
            # secret and is refused outright unless local sign-in is configured.
            if request.url.path in (LOCAL_SIGNIN_PATH, LOGOUT_PATH, *oidc.AUTH_PATHS) and request.method == "GET":
                return await call_next(request)
            # The hosted owner's sign-in link: the page with its button, and the press that spends it (backend/owner_link.py).
            if request.url.path == owner_link.LINK_PATH and request.method in ("GET", "POST"):
                return await call_next(request)
            if request.url.path == owner_link.SCRIPT_PATH and request.method == "GET":
                return await call_next(request)
            # A frontend's one-time sign-in code, exchanged with its PKCE verifier (backend/oidc.py).
            if request.url.path == oidc.TOKEN_PATH and request.method == "POST":
                return await call_next(request)
            # The desktop app's installers and update manifest (backend/downloads.py): the app's
            # updater has no browser session, and a build is nothing to protect.
            if request.url.path.startswith("/download/") and request.method == "GET":
                return await call_next(request)
            if request.url.path == "/api/v2/team/icon" and request.method == "GET":
                return await call_next(request)
            # Tico HQ's check that a self-run server is the one at its address (backend/company_server.py): only a
            # digest, and only while an owner's connect is pending.
            if request.url.path == company_server.PROOF_PATH and request.method == "GET":
                return await call_next(request)
            # The repository webhook (backend/github.py) carries its own HMAC signature.
            if request.url.path == GITHUB_WEBHOOK_PATH and request.method == "POST":
                return await call_next(request)
            # A webhook trigger's address carries its own secret (backend/triggers.py).
            if request.url.path.startswith(TRIGGER_HOOK_PATH) and request.method == "POST":
                return await call_next(request)
            # An agent signing in to the MCP server over OAuth (backend/mcp_oauth.py): the metadata it reads first, and
            # the registration, token and revocation endpoints, which carry their own codes and secrets.
            if request.url.path.startswith(("/api/v2/oauth/", "/.well-known/oauth-", "/.well-known/openid-configuration/")) \
                    and (request.method == "GET" or request.url.path.endswith(("/register", "/token", "/revoke"))):
                return await call_next(request)
            # SCIM carries its own bearer token, which an identity provider holds (backend/scim.py).
            if request.url.path.startswith("/scim/v2/"):
                return await call_next(request)
            # A Hermes profile with no credential pairs itself (backend/agents.py): it asks for a code, polls with its own
            # secret, and fetches the source-available connector it runs. The person's approval is the authenticated step.
            agent_door = (request.url.path == "/api/v2/agents/pairings" and request.method == "POST"
                          or request.url.path.startswith("/api/v2/agents/pairings/") and request.method == "GET"
                          or request.url.path in ("/api/v2/agents/setup-script", "/api/v2/agents/sync-skill")
                          and request.method == "GET")
            if request.url.path != "/api/v2/runners/enroll" and not agent_door:
                began = time.perf_counter()
                who = await asyncio.get_running_loop().run_in_executor(AUTH_POOL, auth.authenticate, request.headers,
                                                                       request.url.path, request.method)
                if who.support:
                    # Again on the path as sent, still percent-encoded: the gate then reads the address the router
                    # will, not a decoded one that could differ from it.
                    from . import support_access
                    raw = request.scope.get("raw_path") or b""
                    support_access.gate(who, raw.decode("latin-1") if raw else request.url.path, request.method)
                if who.via == "assistant" and not who.confirmed and not who.task_actor:
                    who = replace(who, task_actor="bot:" + settings.assistant_bot)
                request.state.auth_ms = (time.perf_counter() - began) * 1000
                request.state.identity = who
                app.state.sleep_idle.seen(who, request.url.path)
                # A locked Tico Cloud company (backend/cloud_access.py): people reach only billing and the download.
                cloud_access.gate(store, who, request.url.path)
                # Counsel sends nothing outside Tico (backend/counsel.py OUTWARD): refused on the server, not only in its tools.
                if counsel.is_counsel(who) and await asyncio.get_running_loop().run_in_executor(
                        None, counsel.refused, store, who, request.url.path, request.method):
                    raise Problem("forbidden", counsel.REFUSED, 403)
                # Counsel calls only its own routes (backend/counsel.py COUNSEL_ROUTES), and a Counsel run reads on them
                # only its own task, its read's document version and its own run (read_refused).
                if counsel.is_counsel(who) and (reason := await asyncio.get_running_loop().run_in_executor(
                        None, counsel.read_refused, store, auth, who, request.method, request.url.path,
                        dict(request.query_params))):
                    raise Problem("privacy", reason, 403)
                if census.person_due(who):
                    await asyncio.get_running_loop().run_in_executor(None, census.note_person, who)
                if who.via:
                    # The Assistant acting for a person (backend/assistant.py): what it writes is
                    # recorded via assistant, and unless the person just confirmed a proposal it may
                    # write only what is low-risk.
                    via_reset = H.VIA.set(who.via)
                if who.attempt_id and who.role != "runner" and not request.url.path.startswith(run_triggers.RUNNER_PATHS):
                    # A run's own tool call: what it writes counts toward whether the run acted.
                    run_reset = H.RUN.set(who.attempt_id)
                if who.role != "runner":
                    # Whose request this is: a bot's own writes never wake that bot (backend/wake_rules.py).
                    cause_reset = wake_rules.CAUSE.set(who.actor)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and not settings.allows_origin(origin):
                    raise Problem("origin", "Cross-site writes are not allowed", 403)
                size = request.headers.get("content-length", "0")
                if not size.isdigit():
                    raise Problem("content_length", "Invalid Content-Length", 400)
                upload = request.url.path in ("/api/notes", "/api/send", "/api/v2/uploads/tasks", "/api/v2/uploads/assistant/messages", "/api/v2/meetings/import") or re.fullmatch(r"/api/(?:meetings/[^/]+/send|v2/uploads/chat/[^/]+)", request.url.path)
                docs_import = (request.url.path in ("/api/v2/docs/import", "/api/v2/market/import")
                               and request.state.identity.role in ("human", "owner", "bot"))
                # A bot's computer publishes files up to 25 MB as raw bytes (backend/files.py).
                published = (request.url.path in ("/api/v2/files/uploads", "/api/v2/files/imports")
                             and request.state.identity.role in ("bot", "runner"))
                # A run's raw provider stream: up to 5 MB of frames, escaped once more as a JSON string.
                captured = (request.url.path.startswith("/api/v2/attempts/") and request.url.path.endswith("/capture")
                            and request.state.identity.role == "runner")
                memory_report = (bool(re.fullmatch(r"/api/v2/bots/[^/]+/memory/report", request.url.path))
                                 and request.state.identity.role == "runner")
                # A bot's video or audio file streams to disk up to the server's upload limit (backend/files.py).
                media_raw = published and raw_media(request, imported=request.url.path == "/api/v2/files/imports")
                streamed = media_raw or bool(re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path)
                                and request.headers.get("content-type", "").lower().startswith("multipart/form-data"))
                # The recorder's speech-to-text clip (backend/recorder_relay.py): up to 25 MB, from a person.
                recorder_audio = (request.url.path == "/api/v2/recorder/audio/transcriptions"
                                  and request.state.identity.role in ("human", "owner"))
                limit = (26_214_400 if recorder_audio else 1024 * 1024 if request.url.path == "/api/v2/team/icon" else settings.upload_max_bytes if media_raw else settings.upload_max_bytes + 11_000_000 if streamed else 14_500_000 if re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path) or request.url.path == "/api/v2/task-questions" else MAX_REPORT_BYTES if memory_report else 27_000_000 if published else 12_000_000 if captured else 20_000_000 if docs_import or upload and request.state.identity.role in ("human", "owner")
                         else 2_000_000)
                if int(size) > limit:
                    raise Problem("too_large", f"Upload exceeds the file limit of {settings.upload_max_bytes} bytes" if streamed else "Request exceeds the upload limit", 413)
                # Streaming/chunked requests also have a hard limit. Starlette caches body()
                # for downstream parsing; collect only a bounded amount before assigning it.
                if streamed:
                    receive = request._receive
                    total = 0
                    async def bounded_receive():
                        nonlocal total
                        message = await receive()
                        total += len(message.get("body", b""))
                        if total > limit:
                            raise Problem("too_large", f"Upload exceeds the file limit of {settings.upload_max_bytes} bytes", 413)
                        return message
                    request._receive = bounded_receive
                else:
                    chunks, total = [], 0
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > limit:
                            raise Problem("too_large", "Request exceeds the upload limit", 413)
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                # assistant_owns reads the database: off the event loop.
                if (getattr(request.state, "identity", None) and request.state.identity.via
                        and not request.state.identity.confirmed
                        and not await asyncio.to_thread(lambda: assistant_writes(
                            request.method, request.url.path, settings, getattr(request, "_body", b""),
                            request.state.identity.actor,
                            lambda tid, shared: assistant_owns(tid, request.state.identity.actor, shared),
                            assistant_direct(), assistant_to_bot))):
                    # A task change, comment or link, a decline or an answer the person asked for in their own Assistant
                    # chat: the turn cites their message and it runs as them, recorded via assistant for that message
                    # (backend/assistant.py).
                    asked = request.headers.get(botops_act.HEADER)
                    raw = getattr(request, "_body", b"")
                    # Approving on the person's words: only when they let their Assistant decide approvals (and the
                    # team allows it); otherwise the one sentence it tells them about the setting.
                    approves = (request.state.identity.via == "assistant"
                                and assistant_mod.approving(request.method, request.url.path, raw))
                    state = assistant_decides(request.state.identity.actor) if approves else None
                    if approves and not state["active"]:
                        raise Problem("approve_setting_off", assistant_mod.approve_hint(settings, state), 403)
                    # A direct setting from the catalogue the person asked for runs on their words the same way.
                    setting = (not approves and request.state.identity.via == "assistant"
                               and assistant_mod.instructed_setting(request.method, request.url.path, raw))
                    # An org change they asked for (a Strategy Bot suggestion they said to make) runs the same way.
                    org = (not approves and request.state.identity.via == "assistant"
                           and assistant_mod.instructed_org(request.method, request.url.path, raw))
                    # The owner's yes to a goal read-back in their own Assistant chat (backend/goal_read_back.py): the
                    # goal, measure and suggestion routes save exactly what it read back, or nothing (`not_agreed`).
                    goal_yes = (not approves and goal_read_back.strategy_turn(request.state.identity)
                                and goal_read_back.saving(request.method, request.url.path))
                    if goal_yes and not asked:
                        raise goal_read_back.not_agreed("A goal change is saved only on the owner's yes to its "
                                                        "read-back, citing it (`--on-behalf-of turn`)")
                    if not goal_yes and not (asked and request.state.identity.via == "assistant" and assistant_direct()
                            and (setting or org or assistant_instructed(request.method, request.url.path, raw,
                                                                 decides=bool(approves)))):
                        raise Problem("confirm_required", "The " + settings.assistant_name + " may not do this on its "
                                      "own. A task change, comment or link, declining an approval, answering a bot's "
                                      "question, a direct setting (`hub settings set`) or an org change (groups, leads, "
                                      "reports to) that the person clearly asked for in this chat runs when you cite their "
                                      "message (`--on-behalf-of turn`). Otherwise propose it with `hub assistant propose` "
                                      "(or POST /api/v2/assistant/actions), several actions on one card (path "
                                      "/api/v2/assistant/batch); approving is a card unless the person lets you "
                                      "decide approvals, then their cited words. The person confirms a card in "
                                      + settings.app_name, 403)
                    def cited():
                        with store.read() as c:
                            if goal_yes:
                                try:
                                    record = assistant_instruction(c, request.state.identity, asked)
                                except Problem as exc:
                                    raise goal_read_back.not_agreed(exc.detail.split(";")[0]) from None
                                # Recorded on every save as the owner, via their Assistant, citing their yes and the
                                # read-back it answered.
                                return {**record, "assistant": request.state.identity.task_actor,
                                        **goal_read_back.agreement(c, request.state.identity, record["cited"],
                                                                   request.method, request.url.path, raw, auth=auth)}
                            record = assistant_instruction(c, request.state.identity, asked)
                            assistant_mod.require_on_task(c, auth, request.state.identity, request.url.path)
                            if setting:
                                def trial(e, target, body):
                                    # The route's own rule (repositories.needs_card), tried and rolled back.
                                    from . import repositories
                                    with store.transaction() as tc:
                                        return repositories.card_words(tc, settings, auth, request.state.identity,
                                                                       target["bot"], body)[0]
                                assistant_mod.require_direct_setting(c, request.method, request.url.path, raw, trial,
                                                                    settings.public_url)
                            if approves:
                                # Recorded on the approval: decided by the Assistant for the person, their words quoted.
                                # The bot whose turn it is: the person's Assistant.
                                acting = request.state.identity.task_actor or "bot:" + settings.assistant_bot
                                record = {**record, "assistant": acting,
                                          **assistant_mod.approval_words(c, request.state.identity, record["cited"],
                                                                         H.actor_id(acting),
                                                                         assistant_mod.approval_id(request.url.path))}
                            return record
                    delegation_reset = H.DELEGATION.set(await asyncio.to_thread(cited))
            early = None
            caller = getattr(request.state, "identity", None)
            if caller:
                request.state.privacy_source = caller
            if caller and request.method not in ("GET", "HEAD", "OPTIONS"):
                # A bot caller walks its run's provenance here: in a thread, so the event loop keeps serving.
                def guard():
                    with store.read() as c:
                        privacy.guard_write(c, caller, request.url.path, auth)
                await asyncio.to_thread(guard)
            # The Assistant's citation was checked above; only BotOps borrows a requester's identity here.
            on_behalf = None if getattr(caller, "via", "") == "assistant" else request.headers.get(botops_act.HEADER)
            if (not on_behalf and getattr(request.state, "identity", None) is not None
                    and request.state.identity.actor == "bot:" + BOTOPS
                    and request.url.path.startswith("/api/v2/")
                    and request.url.path not in ("/api/v2/me", "/api/v2/mcp")):
                body = botops_act.parse_body(getattr(request, "_body", b""))
                on_behalf = ((body.get("on_behalf_of") if isinstance(body, dict) else None)
                             or request.query_params.get("on_behalf_of"))
                # The default applies only to what BotOps does for a person (the delegable routes). Its own run's
                # plumbing (credentials, attempts, jobs, its status) and its own messages stay BotOps': a run's
                # credential fetch as the person was refused, and every human-requested turn failed to start.
                if not on_behalf and botops_act.default_delegable(request.method, request.url.path, body,
                                                                       request.query_params):
                    on_behalf = "default"
            if (on_behalf and getattr(request.state, "identity", None) is not None
                    and request.state.identity.actor == "bot:" + BOTOPS
                    and botops_owns_task(request.url.path, botops_act.parse_body(getattr(request, "_body", b"")))):
                on_behalf = None                    # BotOps' own task: its own words, with its own rights
            if on_behalf and getattr(request.state, "identity", None) is not None:
                # BotOps doing what the person who asked it could do in the app (backend/botops_act.py).
                early, acted = await asyncio.get_running_loop().run_in_executor(
                    None, act_for_requester, request, on_behalf)
                if acted is not None:
                    request.state.identity = replace(acted, task_actor=caller.task_actor or caller.actor)
                    if via_reset is None:
                        via_reset = H.VIA.set("botops")
                    if getattr(request.state, "delegation", None):
                        delegation_reset = H.DELEGATION.set(request.state.delegation)
                    lent = (getattr(request.state, "delegation", None) or {}).get("for")
                    if H.is_human(lent) and cause_reset is not None:
                        # What BotOps does with a person's rights is that person's act: its notices wake as the same
                        # act from the app would (backend/wake_rules.py ALWAYS_WAKE "person", wake presets).
                        wake_rules.CAUSE.set(lent)
            response = early or await call_next(request)
            if response.status_code >= 500:
                from .diagnostics import request_failure
                request_failure(request, response.status_code)
            if response.status_code >= 500 and not getattr(request.state, "telemetry_captured", False):
                telemetry.capture("request", status=response.status_code, route=error_route(request))
            # The page and its files (StaticFiles sends an ETag) may be kept by the browser and
            # revalidated: an unchanged script or stylesheet (ui/app, ui/styles) comes back as a 304, not in
            # full on every load (slow loads on a phone). A deploy writes new files, so the
            # ETag changes and the new page arrives at once. Icons and images under /assets/ are
            # fresh for an hour. Everything else, the API above all, is never stored. Nothing
            # is kept by the CDN.
            # A route may say its answer keeps (a person's photo, a week): that stands, for the
            # browser only. The org tree asked for 26 photos on every load, 813 KB in all.
            route_keeps = (request.url.path.startswith("/api/") and request.method == "GET"
                           and response.status_code in (200, 302)
                           and str(response.headers.get("cache-control", "")).startswith("private, max-age="))
            file_bytes = (request.method in ("GET", "HEAD") and re.fullmatch(r"/api/v2/files/[^/]+(?:/(?:poster|thumb|versions/[0-9]+))?", request.url.path)
                          and response.status_code in (200, 206, 302, 304, 416)
                          and "cache-control" in response.headers)
            if route_keeps or file_bytes:
                pass                    # the route's own "private, max-age=…" stands
            elif request.url.path in ui_bundle.PATHS and response.status_code in (200, 304) \
                    and "immutable" in str(response.headers.get("cache-control", "")):
                pass                    # a hashed script or stylesheet (ui_bundle): kept for a year
            elif request.query_params.get("v") and response.status_code in (200, 304) \
                    and re.fullmatch(r"/(?:tico/ui/)?vendor/fonts/[\w.-]+\.woff2", request.url.path):
                # A font under its hashed URL (scripts/build-*-font.py): kept for a year. Inter's font-display is
                # optional, so a font that had to be revalidated first could miss the first paint.
                response.headers["Cache-Control"] = ui_bundle.IMMUTABLE
            elif not request.url.path.startswith("/api") and "etag" in response.headers:
                response.headers["Cache-Control"] = ("private, max-age=3600" if request.url.path.startswith("/assets/")
                                                     else "private, no-cache")
            else:
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            response.headers["CDN-Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            # The page frames its own PDF previews, and nothing else frames it.
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            if "content-security-policy" not in response.headers:
                page = (not request.url.path.startswith("/api")
                        and response.headers.get("content-type", "").startswith("text/html"))
                response.headers["Content-Security-Policy"] = PAGE_POLICY if page else "frame-ancestors 'self'"
            if settings.public_url.startswith("https://"):
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
            return response
        except Problem as exc:
            if exc.code == "bot_archived" and exc.extra.get("bot"):
                # The archived bot's agent is still using its credential: Health says so (views.operation_issues).
                await asyncio.get_running_loop().run_in_executor(None, agents.note_archived, store, exc.extra["bot"])
            # A browser opening a page goes to the login screen; API calls keep their 401.
            path = request.url.path
            if (exc.status == 401 and auth.proxy and auth.proxy.name == "oidc"
                    and request.method in ("GET", "HEAD") and not hasattr(request.state, "identity")
                    and not path.startswith(("/api", "/mcp")) and "authorization" not in request.headers
                    and "text/html" in request.headers.get("accept", "")):
                back = path + ("?" + request.url.query if request.url.query else "")
                return RedirectResponse(auth.proxy.sign_in_path + "?" + urlencode({"next": oidc.safe_next(back)}),
                                        status_code=302)
            if (exc.status == 401 and auth.proxy and auth.proxy.name == owner_link.KIND
                    and request.method in ("GET", "HEAD") and not hasattr(request.state, "identity")
                    and not path.startswith(("/api", "/mcp")) and "authorization" not in request.headers
                    and "text/html" in request.headers.get("accept", "")):
                return RedirectResponse(owner_link.LINK_PATH, status_code=302)
            response = await problem_handler(request, exc)
            if exc.status == 401 and path == mcp_oauth.MCP_PATH:
                # Where an agent with no token can sign in (backend/mcp_oauth.py, RFC 9728).
                response.headers["WWW-Authenticate"] = mcp_oauth.challenge_header(settings)
            return response
        except ClientDisconnect:
            # The caller hung up while its request body was read (a closed tab, a cancelled call): nothing failed here.
            # It ends as 499, client closed request, which the flight recorder and Sentry don't count as a server error.
            # The method only: some addresses carry their own secret (a webhook trigger's).
            logging.getLogger("tico.request").info("%s request: the client disconnected before its body was read",
                                                   request.method)
            return Response(status_code=499)
        except Exception as exc:
            from .diagnostics import request_failure
            request_failure(request, 500, exc)
            telemetry.capture("request", exc, 500, route=error_route(request))
            raise
        finally:
            if via_reset is not None:
                H.VIA.reset(via_reset)
            if delegation_reset is not None:
                H.DELEGATION.reset(delegation_reset)
            if run_reset is not None:
                H.RUN.reset(run_reset)
            if cause_reset is not None:
                wake_rules.CAUSE.reset(cause_reset)


    def may_edit_routines(c, who, bot):
        """The bot itself, or a person who manages it (its owners, its operator, an Admin, whoever it reports up
        to). A routine is the bot's standing work: the people who direct the bot set it, and the bot may set up
        its own. BotOps does it as the person whose chat message started its turn, checked with their rights;
        with no such person (a bot built during setup) only for a bot still being built from its template."""
        auth.domain(who)
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        if not H.bot(c, bot):
            raise Problem("not_found", "Unknown bot", 404)
        if who.actor == "bot:" + bot:
            return
        if who.actor == "bot:" + BOTOPS:
            try:
                person = delegated_identity(c, who, "turn")
            except Problem:
                person = None
            if person is not None and person.actor != who.actor:
                if auth.bot_manager(c, person, bot) or auth.operator(c, person, bot):
                    return
                raise Problem("forbidden", "The person BotOps is acting for may not change this bot's routines", 403)
            declared = H._json(row["config_json"], {}) if row else {}
            if (H.bot(c, bot) or {}).get("state") == "planned" and isinstance(declared, dict) and declared.get("template"):
                return
            raise Problem("forbidden", "BotOps sets routines only as the person who asked it, or on a bot it is building", 403)
        if auth.bot_manager(c, who, bot) or auth.operator(c, who, bot):
            return
        raise Problem("forbidden", "You cannot change this bot's routines", 403)

    @app.middleware("http")
    async def request_timing(request, call_next):
        started = time.perf_counter()
        request.state.auth_ms = 0.0
        app.state.timing.begin()
        # This request's SQL time and write-lock wait, added to by the threads it runs on (backend/flight.py).
        cost, cost_token = (flight.request_started(request.method, request.scope) if recorder is not None
                            else (None, None))
        route = request.url.path
        response = None
        try:
            response = await call_next(request)
            found = request.scope.get("route")
            route = getattr(found, "path", None) or ("static" if not route.startswith("/api") else "unmatched")
            total = (time.perf_counter() - started) * 1000
            response.headers["Server-Timing"] = (f"auth;dur={request.state.auth_ms:.1f}, app;dur={total:.1f}"
                                                + (f", sql;dur={cost[0]:.1f}, wait;dur={cost[1]:.1f}" if cost is not None else ""))
            return response
        finally:
            found = request.scope.get("route")
            key = request.method + " " + (getattr(found, "path", None) or ("static" if not request.url.path.startswith("/api") else "unmatched"))
            # The flight recorder's caller kind, status and size (backend/flight.py); a raised error counts as a 500.
            who = getattr(request.state, "identity", None)
            size = response.headers.get("content-length") if response is not None else None
            app.state.timing.end(key, request.state.auth_ms, (time.perf_counter() - started) * 1000,
                                 flight.caller_kind(who), response.status_code if response is not None else 500,
                                 int(size) if size and size.isdigit() else 0, getattr(who, "actor", "") or "", cost)
            if cost_token is not None:
                flight.request_finished(cost_token)

    async def display_names(request, response):
        """Stable v2 answers carry display names beside actor ids (backend/names.py)."""
        # Only API JSON is editable. FileResponse, StreamingResponse and raw byte Response
        # objects must keep their bodies, lengths and digests, whatever their content type.
        if (not isinstance(response, JSONResponse) or request.method == "HEAD" or response.status_code != 200
                or "content-disposition" in response.headers or "x-content-sha256" in response.headers
                or "count" in request.query_params or not STABLE_PATH.fullmatch(request.url.path)
                or "application/json" not in response.headers.get("content-type", "")):
            return response
        raw = response.body
        who = getattr(request.state, "identity", None)

        def work():
            with store.read() as c:
                return actor_names.annotate_json(c, raw, render_notices=bool(who and who.role in ("human", "owner")),
                                              actors=request.method == "GET")
        try:
            body = await asyncio.get_running_loop().run_in_executor(None, work)
        except sqlite3.Error:
            body = None
        if body is not None:
            response.body = body
            response.headers["Content-Length"] = str(len(body))
        return response

    class DisplayNameRoute(APIRoute):
        def __init__(self, path, endpoint, **options):
            # FastAPI calls the handler it was given; the JSON it answers is encoded by orjson (backend/fast_json.py).
            # `endpoint` stays the handler itself, for the callers that read its dicts (backend/route_renames.py).
            plain = isinstance(options.get("response_class", DefaultPlaceholder(None)), DefaultPlaceholder)
            super().__init__(path, fast_json.answering_json(endpoint, options.get("status_code")) if plain else endpoint,
                             **options)
            self.endpoint = endpoint

        def get_route_handler(self):
            handler = super().get_route_handler()

            async def named_response(request):
                from .json_response import compress
                return await compress(request, await display_names(request, await handler(request)))
            return named_response

    # HTTP middleware's call_next wraps every response as a stream, hiding its original
    # type. Annotate before that wrapping so actual file/stream responses stay untouched.
    app.router.route_class = DisplayNameRoute

    @app.get("/api/v2/ops/timing")
    def request_timings(request: Request):
        """Where requests spend their time (backend/timing.py): the owner, or BotOps."""
        who = request.state.identity
        if who.role != "owner" and who.actor != "bot:botops":
            raise Problem("forbidden", "Request timings are for the owner and BotOps", 403)
        return app.state.timing.summary()

    def mutate(request, body, fn, check=None):
        def current_access(c):
            # Privacy can narrow after middleware preflight, including before a cached retry.
            source = getattr(request.state, "privacy_source", request.state.identity)
            privacy.guard_write(c, source, request.url.path, auth)
            if check:
                check(c)
            batch_path = re.fullmatch(r"/api/v2/batch/([^/]+)/(next|respond|commit)", request.url.path)
            if batch_path:
                row = c.execute("SELECT * FROM batches WHERE id=? AND person=?",
                                (batch_path[1], request.state.identity.actor)).fetchone()
                privacy.require_batch(c, request.state.identity, row)
        def protected_write(c):
            source = getattr(request.state, "privacy_source", request.state.identity)
            token = H.PRIVATE_WRITE.set(H.PRIVATE_WRITE.get() or privacy.private_execution(c, source))
            path = org_rule_check.PATH.set(request.url.path)     # a hold keeps its route, for Send anyway's guard
            try:
                return fn(c)
            except H.Refused as exc:
                exc.private = getattr(exc, "private", False) or H.PRIVATE_WRITE.get()
                raise
            finally:
                org_rule_check.PATH.reset(path)
                H.PRIVATE_WRITE.reset(token)
        result = store.mutate(request.state.identity, request.url.path,
                              request.headers.get("idempotency-key"), body.model_dump(), protected_write, check=current_access)

        if isinstance(result, dict) and "_refusal" in result:
            refusal = result["_refusal"]
            raise Problem(refusal["code"], refusal["detail"], refusal["status"],
                          extra={"idempotency_refused": True})
        with store.read() as c:
            privacy.require_payload(c, request.state.identity, result)
        return result

    def task_view(row, c=None, parts=None, pipelines=None, visible_sql="1", who=None):
        if c is not None:
            H.hydrate_task_tags(c, [row])
        value = {"id": row["id"], "short_id": row["id"][:8], **row, "acceptance_criteria": json.loads(row.get("acceptance_json", "[]")),
                 "labels": H.task_labels(row), "lane": row.get("lane") or "company",
                 "next_run": bool(row.get("next_run")), "private": H.task_private(c, row), "cover": None}
        if c is not None and row.get("next_run"):
            value["next_run_waiting"] = H.next_run_waiting(c, row)
        if c is not None and (deferred := quiet_work.view(c, row)):
            # Deferred work: delivered (into which run, when; deferred until what), eligible or queued (quiet_work.view).
            # `quiet` is its older name, kept one more release for callers that read it.
            value["deferred"] = value["quiet"] = deferred
        # The stored condition is in `deferred.resume`; a board of 500 tasks does not carry the column besides.
        value.pop("resume_json", None)
        if not value.get("snoozed_note"):
            value.pop("snoozed_note", None)     # a null note is left out: a board of 500 tasks carries none
        if not value.get("occurrence"):
            value.pop("occurrence", None)       # only a due-date routine's task has one: a board of 500 carries none
        value.pop("labels_json", None)
        value.pop("occurrence_step", None)      # the server's: which step made an occurrence's task (never in a view)
        value.pop("rule_due_id", None)          # the server's: the rule due date its run was fired for (rule_due.py)
        value.pop("checkpoint_state", None)     # wake_rules.state_of, for the server only
        value.pop("blocker_key", None)          # blocker_asks' match key, for the server only
        value.pop("holds_cleared_at", None)     # the stranded sweep's clock (task_holds), for the server only
        hold_kind = value.pop("blocked_kind", None)
        for field in ("blocked_from_step", "blocked_from_status", "blocked_gate_json"):
            value.pop(field, None)
        # A review-gate or worktree hold says its own why, so the screen never folds it into a bot wait's line
        # (ui/app/tasks.js taskBotWait); other kinds stay server-only.
        if hold_kind in ("gate", "worktree"):
            value["blocked_kind"] = hold_kind
        # Most list rows have no hold or accumulated clock; omit empty optional fields.
        for field in ("blocked_since", "blocked_reason", "building_started_at", "building_seconds"):
            if not value.get(field):
                value.pop(field, None)
        if pipelines is None:
            if c is None:
                with store.read() as pipeline_conn:
                    pipelines = H.type_list(pipeline_conn)
            else:
                pipelines = H.type_list(c)
        typ = next((t for t in pipelines if t["id"] == row.get("type_id")), None)
        value["type"] = {"id": typ["id"], "name": typ["name"]} if typ else None
        step = next((s for s in typ["steps"] if s["id"] == row.get("step_id")), None) if typ else None
        # A step's check pool and flow are the type's settings (Settings > Tasks), not sent again on each of a board's tasks.
        value["step"] = {k: v for k, v in step.items() if k not in ("check_pool", "check_limit", "flow")} if step else None
        if c is not None:
            value["relations"] = TR.grouped(c, [row["id"]], visible_sql)[row["id"]]
            value["roles"] = TRo.roles_of(c, row["id"])
            from .roadmaps import stats, EMPTY_STATS
            value.update({**EMPTY_STATS, **stats(c, [row['id']]).get(row['id'], {})})
            routine = c.execute('SELECT schedule_id FROM schedule_occurrences WHERE task_id=?', (row['id'],)).fetchone()
            if routine:
                value.update(routine_id=routine['schedule_id'])
            asks = H.open_task_asks(c, row, actor=privacy.actor(who))
            value.update({"origin_actor": H.task_origin(c, row), "ask": next(iter(asks), None), "open_asks": len(asks)})
            if who is not None and visible_sql != "1":
                value["from_person"] = views.person_origin(c, row, visible_sql, who.actor)
            from .task_review import task_covers
            value["cover"] = task_covers(c, [row["id"]]).get(row["id"])
            from .blobs import brief
            value["attachments"] = [brief(r) for r in c.execute(
                "SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=?", (row["id"],))]
            value["links"] = H.task_links(c, row["id"])
            from .worktree_waits import for_task as worktree_waits_for
            value["worktree_waits"] = worktree_waits_for(c, row["id"])
            from .check_pools import for_task as check_waits_for
            value["check_waits"] = check_waits_for(c, row["id"])     # a check waiting for a free reviewer
            # What it waits for besides tasks: a pull request to merge, a release to be published (Dev 256).
            value["milestone_waits"] = milestone_waits.waits(c, row["id"])
            value["children_summary"] = H.children_summary(c, row["id"], visible_sql)
            value["pr_state"] = H.pr_state(value["links"])
            from .qa_waivers import waivers as qa_waivers_of
            # A person's QA waiver for an exact head: shown as a waiver, never as a QA verdict (backend/qa_waivers.py).
            value["qa_waivers"] = qa_waivers_of(c, row, value["links"])
            if parts is None:
                counts = c.execute("SELECT COUNT(*), SUM(status IN ('done','closed')) FROM tasks WHERE id IN ("
                                   + TR.CHILD_IDS + ") AND (" + visible_sql + ")", (row["id"],)).fetchone()
                parts = {"total": counts[0] or 0, "done": counts[1] or 0}
            value["parts"] = parts
            if since := step_age.since_map(c, [row]).get(row["id"]):
                value["step_since"] = since      # how long a Dev ticket has been at its working step (backend/step_age.py)
        return value

    def task_views(rows, c, who, visible_sql="1"):
        """Hydrate one task-list page with a fixed number of relation queries. `rows` were read on `c` under
        `visible_sql`, the reader's rule, as every list does: each row stands for that rule's answer for its task."""
        if not rows:
            return []
        H.hydrate_task_tags(c, rows)
        ids = [row["id"] for row in rows]
        from .roadmaps import stats, EMPTY_STATS
        requester_stats = stats(c, ids)
        for row in rows:
            row.update({**EMPTY_STATS, **requester_stats.get(row['id'], {})})
        marks = ",".join("?" * len(ids))
        from .task_review import task_covers
        covers = task_covers(c, ids)

        # From the page's ids, with visibility checked per child: an `IN (SELECT id FROM tasks WHERE ...)`
        # list made SQLite probe every visible task against the page (1.5 s at 30k tasks).
        parts = {row["parent_id"]: {"total": row["total"] or 0, "done": row["done"] or 0}
                 for row in c.execute(
                     f"SELECT r.to_task parent_id,COUNT(*) total,SUM(t.status IN ('done','closed')) done "
                     f"FROM task_relations r INDEXED BY task_relations_to JOIN tasks t ON t.id=r.from_task "
                     f"WHERE r.to_task IN ({marks}) AND r.kind='parent' "
                     f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.from_task AND ({visible_sql})) "
                     f"GROUP BY r.to_task", ids)}
        relations = TR.grouped(c, ids, visible_sql)
        roles = TRo.grouped(c, ids)
        routines_by_task = {row["task_id"]: row["schedule_id"] for row in c.execute(
            f"SELECT task_id,schedule_id FROM schedule_occurrences WHERE task_id IN ({marks})", ids)}

        links = {tid: [] for tid in ids}
        for link in c.execute(
                f"SELECT id,task_id,kind,url,title,state,added_by,created,pr_sha,pr_merged_at,repo,number,branch,"
                f"computer_id,path,checks,mergeable,review_state,pending_comments,updated "
                f"FROM task_links WHERE task_id IN ({marks}) ORDER BY created", ids):
            # Not a link's record, its empty columns or its own write time (a computer's heartbeat rewrites that every
            # minute): the task's read and its /links have them, and every open tab gets each list change.
            links[link["task_id"]].append({k: v for k, v in dict(link).items()
                                           if v is not None and k not in ("detail_json", "pr_sha", "updated")})

        attachments = {tid: [] for tid in ids}
        from .blobs import brief
        for asset in c.execute(
                f"SELECT a.task_id,b.* FROM task_assets a JOIN blobs b ON b.id=a.blob_id "
                f"WHERE a.task_id IN ({marks})", ids):
            attachments[asset["task_id"]].append(brief(asset))

        from .privacy_index import ReadIndex
        provenance = ReadIndex(c, who)
        asks, open_counts = {}, {}
        # Readability is checked here, not by a SQL function: installing one marks the pooled connection
        # as changed, so it was closed after every task list and the next read paid for a new one.
        open_asks = c.execute(
            "SELECT m.*,t.id AS ask_task_id FROM tasks t JOIN conversations cv ON cv.id=t.conversation_id "
            f"JOIN messages m ON m.conversation_id=t.conversation_id AND {H.MESSAGE_TASK_SQL}=t.id "
            f"WHERE t.id IN ({marks}) AND m.kind='ask' AND m.deleted_at IS NULL "
            f"AND {H.OPEN_ASK_SQL} ORDER BY m.rowid DESC", ids).fetchall()
        # Each ask's room is checked below; the rooms are read together first, not one room per task.
        provenance.meetings.prime_conversations({message["conversation_id"] for message in open_asks})
        for message in open_asks:
            if not provenance.message(message["id"], message["conversation_id"], message["refs_json"],
                                      message["in_reply_to"]):
                continue
            message = dict(message)
            tid = message.pop("ask_task_id")
            open_counts[tid] = open_counts.get(tid, 0) + 1
            if tid in asks:
                continue            # the newest readable open ask is the one shown
            message["refs"] = H._json(message.get("refs_json"), {}) or {}
            message["stale"] = H.ask_older(message)     # it stays until the weekday batch sends it back (still_needed)
            asks[tid] = message

        origins = {}
        for event in c.execute(
                f"SELECT target,detail_json FROM events WHERE actor=? AND action='task.origin' "
                f"AND target IN ({marks}) ORDER BY target,ts DESC", (H.KEEPER, *ids)):
            if event["target"] in origins:
                continue
            origin = (H._json(event["detail_json"], {}) or {}).get("bot")
            origins[event["target"]] = origin if H.is_bot(origin) else None

        legacy_owner = None
        if any(row.get("requester") == H.KEEPER and H.is_human(row.get("owner")) for row in rows):
            legacy_owner = H.human_actor(H.default_human(c))
        pipelines = H.type_list(c)
        summaries = H.children_summaries(c, ids, visible_sql)
        # The rows each row's from_person and deferred state read, read for the whole page first; each row is still
        # decided one at a time by the same code below.
        views.prime_list_origins(c, visible_sql, rows)
        quiet_work.prime(c, [row for row in rows if row.get("next_run")])
        step_since = step_age.since_map(c, rows)
        result = []
        for row in rows:
            value = task_view(row, pipelines=pipelines)
            value.pop("checkpoint", None)    # up to a page of text per task; the full task view carries it
            value.update({
                "relations": relations[row["id"]],
                "roles": roles[row["id"]],
                "parts": parts.get(row["id"], {"total": 0, "done": 0}),
                "links": links[row["id"]],
                "children_summary": summaries[row["id"]],
                "pr_state": H.pr_state(links[row["id"]]),
                "attachments": attachments[row["id"]], "cover": covers.get(row["id"]),
                "ask": asks.get(row["id"]), "open_asks": open_counts.get(row["id"], 0),
            })
            if row.get("next_run"):
                value["next_run_waiting"] = H.next_run_waiting(c, row)
                if deferred := quiet_work.view(c, row, history=False):
                    value["deferred"] = value["quiet"] = deferred     # `quiet`: the older name, one more release
            if row["id"] in step_since:
                value["step_since"] = step_since[row["id"]]
            if row["id"] in routines_by_task:
                value["routine_id"] = routines_by_task[row["id"]]
            if row["id"] in origins:
                value["origin_actor"] = origins[row["id"]]
            elif row.get("requester") == H.KEEPER and row.get("owner") == legacy_owner:
                # Only legacy keeper reviews need the refusal-window fallback.
                value["origin_actor"] = H.task_origin(c, row)
            else:
                value["origin_actor"] = None
            value["from_person"] = views.person_origin(c, row, visible_sql, who.actor)
            result.append(value)
        return result

    def mover(c, who):
        """Whether this person may move any task: the owner, or a person on a mover team."""
        return who.role == "owner" or who.role == "human" and H.can_move(c, who.actor)

    def visible_tasks(c, who, owner=None, requester=None, status=None, lane=None, label=None,
                      limit=500, offset=0, order="queue", visible_sql=None, **more):
        auth.domain(who)
        visible_sql = visible_sql or auth.task_sql(c, who)
        rows = H.tasks(c, owner=owner, requester=requester, status=status, lane=lane, label=label,
                       limit=limit + 1, offset=offset, order=order, visible=visible_sql, **more)
        page, has_more = rows[:limit], len(rows) > limit
        return task_views(page, c, who, visible_sql), offset + limit if has_more else None

    def visual_view(c, who, rows):
        """Messages with each visual as this reader sees it now: fresh addresses on /visuals (backend/visuals.py), on the
        visual address or, in the fallback, the app's own (pictures, video and audio only)."""
        return visuals.for_reader(app, settings, c, who, rows)

    def visual_key():
        return visuals.key_for(app, settings)

    def visual_ancestors():
        return visuals.ancestors(settings)

    @app.api_route("/visuals/{token}", methods=["GET", "HEAD"], include_in_schema=False)
    def visual(request: Request, token: str):
        """A visual for inline display, apart from the app's sign-in: the signed token is the only credential, and
        the reader it names is checked again on every request (each range request too)."""
        return visuals.serve(request, store, auth, app.state.files, app.state.blobs, visual_key(),
                             visual_ancestors(), token, full=visuals.mode(settings) == "full",
                             visual_origin=settings.visual_origin)

    def check_refs(c, who, refs):
        # Validate referenced objects rather than trusting an arbitrary ID in a payload.
        for kind, values in refs.items():
            if kind in ("task", "task_id"):
                for value in values if isinstance(values, list) else [values]:
                    if value:
                        auth.task(c, who, value)
            elif kind == "live":
                # A committed batch hands each bot its questions and instructions (backend/batch.py).
                if not isinstance(values, dict) or values.get("kind") != "batch":
                    raise Problem("reference", "A live reference on a chat message must be a batch", 422)
            elif kind == "voice":
                # Said aloud in a bot's voice mode: the runner asks for a short spoken answer first.
                if values is not True:
                    raise Problem("reference", "voice is true or absent", 422)
            elif kind == "quiet":
                # A batch's record for a bot (what was decided, nothing to act on) is read in the
                # room, not run: no job is queued for it.
                if values is not True or (refs.get("live") or {}).get("kind") != "batch":
                    raise Problem("reference", "Only a batch record may be quiet", 422)
            elif kind == "update":
                # A reply to a bot's update (backend/updates.py): the update must exist.
                if not isinstance(values, str) or not updates.one(c, values):
                    raise Problem("reference", "Unknown update", 422)
            elif kind == "automation":
                # A chat about one automation (BotOps edits the one a person has open): the caller must be able to see it.
                if not automations.may_reference(c, who, values):
                    raise Problem("reference", "Unknown automation", 422)
            elif kind == "automations_filter":
                # What the Automations page shows (range, tab, filters), so BotOps answers about that view; only words
                # the asker typed or picked, never another person's data.
                if not isinstance(values, dict) or len(json.dumps(values)) > 2000:
                    raise Problem("reference", "automations_filter is a small object", 422)
            elif kind not in ("depth", "turn_id"):
                raise Problem("reference", f"Unsupported reference kind: {kind}", 422)

    def send(c, who, body):
        in_assistant_room = False
        if str(body.to).lower() == "assistant":
            if who.role not in ("human", "owner") or who.via:
                raise Problem("forbidden", "Only a human writes to their private Assistant", 403)
            if body.conversation_id and not assistant_room(c, who, body.conversation_id):
                raise Problem("forbidden", "Use your own Assistant room", 403)
            info = assistant_mod.availability(c, settings, who)
            if not info["available"]:
                raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
            body.to = info["bot"]
            body.conversation_id = assistant_mod.chat_room(c, settings, who)["id"]
        to = auth.target(c, who, body.to)
        if not body.conversation_id:
            from .shared_bots import route
            to = auth.target(c, who, route(c, who.actor, to))
        if to.startswith("bot:"):
            docs = (who.role in ("human", "owner") and body.conversation_id and to == "bot:" + views.DOC_BOT
                    and views.docs_room(auth.conversation(c, who, body.conversation_id), who))
            if not docs:
                if who.role in ("human", "owner") and H.actor_id(to) == settings.assistant_bot:
                    # The assistant takes chat in the caller's own Assistant room and nowhere else.
                    if not assistant_room(c, who, body.conversation_id):
                        raise Problem("forbidden", settings.assistant_name + " chats only in your own Assistant "
                                      "(/api/v2/assistant); message one of your bots here", 403)
                    in_assistant_room = True
                elif who.role in ("human", "owner") and H.actor_id(to) == strategy_bot.SLUG:
                    # No person chats with the Strategy Bot: their Assistant asks it (backend/assistant.py HELPERS). A
                    # person's post with no room (the Goals page, `hub chat`) goes to their Assistant instead; the
                    # owner's earlier chat with it is history, read-only. Bots still message it: its goals session.
                    if body.conversation_id or who.via:
                        raise Problem("forbidden", "Nobody chats with the " + strategy_bot.name(c) + " directly; ask "
                                      "your " + settings.assistant_name + ", which hands it the work", 403)
                    info = assistant_mod.availability(c, settings, who)
                    if not info["available"]:
                        raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
                    to = "bot:" + info["bot"]
                    body.conversation_id = assistant_mod.chat_room(c, settings, who)["id"]
                    in_assistant_room = True
                auth.require_write(c, who, H.actor_id(to), missing="Unknown recipient")
        auth.require_bot_contact(c, who, to, body.conversation_id,
                                 (body.refs or {}).get("task") or (body.refs or {}).get("task_id"),
                                 kind="message")
        if body.conversation_id:
            conv = auth.conversation(c, who, body.conversation_id, write=True)
            if to not in conv["participants"]:
                raise Problem("forbidden", "Recipient is not a conversation participant", 403)
        if body.in_reply_to:
            msg = H.message(c, body.in_reply_to)
            if not msg:
                raise Problem("not_found", "Original message not found", 404)
            auth.conversation(c, who, msg["conversation_id"], write=True)
            privacy.require_message(c, who, msg)
            if body.conversation_id and msg["conversation_id"] != body.conversation_id:
                raise Problem("reference", "Reply belongs to a different conversation", 422)
        check_refs(c, who, body.refs)
        refs = dict(body.refs or {})
        if body.command:
            refs["command"] = True
        if body.needs_action:
            refs["needs_action"] = True
        if wrong := delivery.check(body.delivery, body.defer_for):
            raise Problem("delivery", wrong, 422)
        if body.delivery and body.kind == "ask":
            raise Problem("delivery", "A question always wakes the one it is for: send it without a delivery", 422)
        # How it reaches a bot (backend/delivery.py). With none named a message goes as it always has: a person's
        # steers, a bot's goes as bot-to-bot messages do (backend/bot_to_bot.py).
        way = body.delivery
        if to.startswith("bot:") and way in ("steer", "queue") and who.role == "bot":
            refs["needs_action"] = True
        if in_assistant_room:
            refs["assistant"] = True           # the server's mark: this turn acts for the person
        conversation_id = body.conversation_id
        if not conversation_id and body.kind == "steer" and who.role == "bot" and refs.get("task"):
            # A requester's steer goes to the task's own room, which its owner's running turn takes input from.
            task = H.task(c, refs["task"])
            if not task or task["requester"] != who.actor or task["owner"] != to or not task.get("conversation_id"):
                raise Problem("steer_target", "Steer a task you requested, sent to its owner", 422)
            conversation_id = task["conversation_id"]
        if not conversation_id and who.role in ("owner", "human") and to.startswith("bot:"):
            conversation_id = rooms.chat_room(c, auth, who, H.actor_id(to))["id"]
        # An onboarding bot only learns: it answers in chat and talks with its manager, and sends nothing else.
        lifecycle.refuse_send(c, who, to, H.conversation(c, conversation_id) if conversation_id else None)
        privacy.require_destination(c, who, to, conversation_id, refs,
                                    H.message(c, body.in_reply_to) if body.in_reply_to else None)
        # Ride-along: a defer while the bot has a turn going in this chat goes in right after that turn.
        if way == "defer" and to.startswith("bot:") and who.role != "bot" and conversation_id \
                and delivery.running_in_session(c, H.actor_id(to), [conversation_id]):
            way = "queue"
        if to.startswith("bot:") and way == "queue":
            refs["delivery"] = "queue"           # after the bot's running turn, not into it (execution.inputs)
        if to.startswith("bot:") and way == "defer":
            refs["quiet"] = True                 # no job: the session's next turn, or its deadline, takes it
        message = H.say(c, who.actor, to, body.text, conversation_id=conversation_id,
                        kind=body.kind, refs=refs, in_reply_to=body.in_reply_to, wait_s=body.wait_s)
        due = None
        if to.startswith("bot:") and way == "defer":
            if who.role == "bot":
                # A bot's deferred message rides with the recipient's next run of any kind, as a waiting
                # bot-to-bot message does: its own DM may not see another turn.
                task_ref = refs.get("task") if isinstance(refs.get("task"), str) else None
                held_note = bot_to_bot.hold(c, message, H.task(c, task_ref) if task_ref else None)
                due = delivery.defer(c, to, "note", held_note["id"], defer_for=body.defer_for)
            else:
                due = delivery.defer(c, to, "message", message["id"], conversation_id=message["conversation_id"],
                                     defer_for=body.defer_for)
        from .chat_goals import current as current_chat_goal
        goal = current_chat_goal(c, message["conversation_id"])
        # Added to the stored references, so the marks H.say set (a bot_to_bot hold, quiet) stay (Dev 328).
        if goal and goal["status"] == "active":
            stored = {**(message.get("refs") or {}), "goal_context": {"id": goal["id"], "updated_at": goal["updated_at"]}}
            c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(stored), message["id"]))
            message = H.message(c, message["id"])
        if body.attachments:
            # The visuals go on the message it was sent as: versions of files in this conversation (backend/visuals.py).
            attached, report = visuals.attach(c, who, app.state.files, app.state.blobs, body.attachments,
                                              conversation_id=message["conversation_id"])
            stored = {**(message.get("refs") or {}), "attachments": attached}
            c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(stored), message["id"]))
            message = {**visual_view(c, who, [H.message(c, message["id"])])[0], "visuals": report}
        if (goal and goal["status"] == "active") or body.attachments:
            H.held_queue_recheck(c, message["id"])
        if who.role == "bot":
            conv = H.conversation(c, message["conversation_id"])
            if who.attempt_id and all(p.startswith("bot:") for p in conv["participants"]):
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            auth.conversation(c, who, conv["id"])
        # An onboarding bot starts only a person's message or its manager's; anyone else's waits for it to be Active.
        if note := (H.held_note(c, to) or usage_limits.warning(c, to) or bot_stops.warning(c, to)
                    or (None if H.is_human(who.actor) else H.setup_hold(c, to, who.actor))):
            message = {**message, "warning": note}
        if held := bot_to_bot.held_reason(message):
            message = {**message, "woke": False, "held": held}
            way = "defer"
        if to.startswith("bot:"):
            message = {**message, "sent_as": delivery.sent_as(way or "steer", due)}
        return message

    # Liveness reads the database on its own two threads: when slow handlers hold every worker in the shared
    # pool, a probe queued behind them would time out and mark a working server unhealthy.
    health_threads = anyio.CapacityLimiter(2)

    def health_check():
        with store.read() as c:
            c.execute("SELECT 1 FROM cloud_migrations").fetchone()

    @app.get("/healthz")
    async def health():
        await anyio.to_thread.run_sync(health_check, limiter=health_threads)
        return {"ok": True, "service": "tico", "protocol": 2, "release": settings.release_id,
                "environment_id": settings.environment_id}

    @app.get("/api/v2/config")
    def environment(request: Request):
        """Everything a client needs to name this environment and reach its runner."""
        from .onboarding import config_view
        with store.read_transaction() as c:
            return {**config_view(c, settings, request.state.identity), "features": {"task_files_multipart": True, "file_media_stream": True}}

    def updater_only(who, what):
        """The owner, or an `update` service key (backend/service_keys.py) held by a release bot on another install."""
        if who.role == "service" and who.scope == "update":
            return
        owner_only(who, what)

    @app.get("/api/v2/system/update")
    def system_update_status(request: Request):
        updater_only(request.state.identity, "sees update progress")
        result = releases.status()
        if result.get("state") in ("healthy", "rolled_back", "failed"):
            record_update_outcome(result)
        # What this server really ran: the updater's `from` only knows in-app updates.
        with store.read() as c:
            ran = releases.history(c)
        result["running"] = releases.version()
        result["previous"] = ran[-2]["version"] if len(ran) > 1 else ""
        result["history"] = ran[-5:]
        # How the computers stand against this server's release, so a rollout can see them follow.
        result["computers"] = computer_releases()
        return result

    def computer_releases():
        """Online computers counted by state (backend/runner_versions.py): current, updating, needs_update, ..."""
        counts = {}
        with store.read() as c:
            cutoff = H.shift(H.now(), seconds=-views.AWAKE_GAP)
            known = runner_versions.load(c)
            for row in c.execute("SELECT id FROM runners WHERE revoked_at IS NULL AND last_seen>?", (cutoff,)):
                state = runner_versions.view(known.get(row["id"]))["state"]
                counts[state] = counts.get(state, 0) + 1
        return counts

    @app.get("/api/v2/system/usage-count")
    def usage_count_status(request: Request):
        owner_only(request.state.identity, "sees the anonymous usage count settings")
        with store.read() as c:
            return census.view(c)

    @app.put("/api/v2/system/usage-count")
    def usage_count_set(request: Request, body: M.UsageCount):
        owner_only(request.state.identity, "changes the anonymous usage count")
        return census.set_enabled(body.enabled, request.state.identity.actor)

    @app.post("/api/v2/system/usage-count/reset")
    def usage_count_reset(request: Request):
        owner_only(request.state.identity, "resets the install ID")
        return census.reset_id(request.state.identity.actor)

    @app.post("/api/v2/system/usage-count/notice")
    def usage_count_notice(request: Request, body: M.UsageCountNotice):
        owner_only(request.state.identity, "sees the anonymous usage count notice")
        census.set_notice(body.state)
        return {"ok": True}

    @app.post("/api/v2/system/update/check")
    def system_update_check(request: Request):
        updater_only(request.state.identity, "checks for updates")
        return releases.check_now()

    # The updater restarts this process, so the outcome is written by whichever process first
    # sees a final state after a start; a marker row keeps it to one event per update.
    def record_update_outcome(result):
        with store.transaction() as c:
            hosted_updates.record_outcome(c, result)

    @app.post("/api/v2/system/update")
    def system_update(request: Request, body: M.SystemUpdate):
        who = request.state.identity
        updater_only(who, "updates the installation")
        result = releases.start(body.version)
        target = str(body.version).lstrip("v")
        with store.transaction() as c:
            record = {"actor": who.actor, "from": releases.version(), "to": target}
            c.execute("INSERT INTO registry_metadata VALUES('update-pending',?) "
                      "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(record),))
            H.event(c, who.actor, "system.update.started", target, {"from": record["from"], "to": target})
            flag_cut(c, "update", target)    # the runs it cuts resume after it (execution.cut_by_update)
        return result

    @app.get("/api/v2/providers")
    def providers_view(request: Request):
        """Which AI providers this company uses and its default model; every signed-in
        identity reads it, the owner writes it."""
        auth.domain(request.state.identity)
        with store.read() as c:
            return Providers.view(Providers.load(c, settings))

    @app.put("/api/v2/providers")
    def providers_save(request: Request, body: M.ProvidersUpdate):
        who = request.state.identity
        auth.domain(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner may choose the company's AI providers", 403)

        def work(c):
            before, after = Providers.save(c, who.actor, body.model_dump(), H.now(), settings)
            model_login.hold_removals(c, settings)   # bots on the company's default may now run another tool
            H.event(c, who.actor, "providers.updated", "",
                    {"before": {k: before[k] for k in ("enabled", "runtime", "model", "revision", "auto_fallback", "check_models", "preset", "decide_level", "meeting_model")},
                     "after": {k: after[k] for k in ("enabled", "runtime", "model", "revision", "auto_fallback", "check_models", "preset", "decide_level", "meeting_model")}})
            return Providers.view(after)
        return mutate(request, body, work)

    @app.get("/api/v2/providers/gateway")
    def gateway_view(request: Request):
        """The Tico AI Gateway connection: whether it is set and where from. Its preview and the Gateway's last word on
        the key (working, revoked, the month's cap) only to the owner, who manages it. Never the key."""
        who = request.state.identity
        auth.domain(who)
        if who.role == "owner":
            gateway_connection.check_soon(store, app.state.vault, settings)   # the page's answer is the last check's
        with store.read() as c:
            return gateway_connection.view(c, settings, manage=who.role == "owner")

    @app.get("/api/v2/gateway")
    def gateway_overview(request: Request):
        """Settings > Gateway: the connection (with spend against the cap for the owner), the picks the bots use and the
        services."""
        who = request.state.identity
        auth.domain(who)
        if who.role == "owner":
            gateway_connection.check_soon(store, app.state.vault, settings)
        with store.read() as c:
            return gateway_page.overview(c, settings, owner=who.role == "owner")

    @app.get("/api/v2/gateway/catalog")
    def gateway_catalog(request: Request, query: str = ""):
        """The Gateway's lineup as an agent reads it: models, decisions and services, each with when to use it, its
        price and its ids (`hub_gateway_catalog`). `query` keeps the entries it matches."""
        auth.domain(request.state.identity)
        with store.read() as c:
            gateway_page.catalog(c, settings)
        return gateway_tiers.lineup(query[:200])

    @app.post("/api/v2/gateway/search")
    def gateway_search(request: Request, body: M.GatewaySearch):
        """Web search through the Tico AI Gateway (`hub_gateway_search`), asked with the company's gateway key and, for a
        bot, as that bot. Whoever may call the API may ask; the Gateway meters it."""
        who = request.state.identity
        auth.domain(who)
        if settings.rehearsal:
            raise Problem("rehearsal", "Search is off in rehearsal mode; nothing is sent to the Gateway", 503)
        with store.read() as c:
            key, base, _ = gateway_connection.target(c, app.state.vault, settings)
            header = gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
        if not key:
            raise Problem("gateway_not_connected", "The Tico AI Gateway is not connected; the owner saves a gateway key in "
                          "Settings > Computers", 409)
        status, answer, code = gateway_client.search(base, key, body.query, body.max_results, header)
        if status != 200:
            if status == 404:
                raise Problem("gateway_search_unavailable", "The Tico AI Gateway does not offer search yet", 503)
            raise Problem("gateway_search_failed", f"Search failed: {code or status}", 503 if status in (0, 429) or status >= 500 else 422)
        return answer

    @app.post("/api/v2/gateway/fetch")
    def gateway_fetch(request: Request, body: M.GatewayFetch):
        """Read a web page or PDF through the Tico AI Gateway (`hub_gateway_fetch`), asked with the company's gateway key
        and, for a bot, as that bot. Whoever may call the API may ask; the Gateway meters it."""
        who = request.state.identity
        auth.domain(who)
        if settings.rehearsal:
            raise Problem("rehearsal", "Fetch is off in rehearsal mode; nothing is sent to the Gateway", 503)
        with store.read() as c:
            key, base, _ = gateway_connection.target(c, app.state.vault, settings)
            header = gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
        if not key:
            raise Problem("gateway_not_connected", "The Tico AI Gateway is not connected; the owner saves a gateway key in "
                          "Settings > Computers", 409)
        status, answer, code = gateway_client.fetch(base, key, body.url, body.max_tokens, header)
        if status != 200:
            if status == 404:
                raise Problem("gateway_fetch_unavailable", "The Tico AI Gateway does not offer fetch yet", 503)
            raise Problem("gateway_fetch_failed", f"Fetch failed: {code or status}", 503 if status in (0, 429) or status >= 500 else 422)
        return answer

    app.state.image_slots = threading.BoundedSemaphore(GATEWAY_IMAGE_SLOTS)

    @app.post("/api/v2/gateway/images")
    def gateway_images(request: Request, body: M.GatewayImage):
        """Make an image through the Tico AI Gateway (`hub_gateway_image`), asked with the company's gateway key and, for a
        bot, as that bot. Whoever may call the API may ask; the Gateway meters it."""
        who = request.state.identity
        auth.domain(who)
        if settings.rehearsal:
            raise Problem("rehearsal", "Images are off in rehearsal mode; nothing is sent to the Gateway", 503)
        with store.read() as c:
            key, base, _ = gateway_connection.target(c, app.state.vault, settings)
            header = gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
        if not key:
            raise Problem("gateway_not_connected", "The Tico AI Gateway is not connected; the owner saves a gateway key in "
                          "Settings > Computers", 409)
        cut = None
        try:                                           # a fault in Finance refuses nothing (backend/finance/limits.py)
            from .finance import reads as finance_reads
            with store.read() as c:
                cut = finance_reads.video_cutoff(c, who.actor)
        except Exception as exc:
            telemetry.capture("finance_video_cutoff", exc)
        if cut is not None:
            raise Problem("video_budget_reached", f"{cut.name} budget reached: no new images until it is raised", 409)
        if not app.state.image_slots.acquire(blocking=False):
            return JSONResponse({"error": {"code": "gateway_images_busy", "detail": "Image making is busy; try again shortly",
                                           "retryable": True}}, status_code=429, headers={"Retry-After": "10"})
        try:
            status, answer, code = gateway_client.image(base, key, body.prompt, body.category, body.images, body.size,
                                                        body.count, header)
        finally:
            app.state.image_slots.release()
        if status != 200:
            if status == 404:
                raise Problem("gateway_image_unavailable", "The Tico AI Gateway does not make images yet", 503)
            raise Problem("gateway_image_failed", f"Image failed: {code or status}", 503 if status in (0, 429) or status >= 500 else 422)
        return answer

    @app.put("/api/v2/providers/gateway")
    def gateway_save(request: Request, body: M.GatewayConnectionSave):
        """Save the gateway key (and URL), then ask the Gateway about it. The owner only; the key is sealed at once and
        is never in a response, an event or the idempotency table (this route keeps no receipt)."""
        who = request.state.identity
        auth.domain(who)
        vault = app.state.vault
        with store.transaction() as c:
            gateway_connection.save(c, vault, who, {"key": body.key.get_secret_value() if body.key else "",
                                                    "url": body.url, "expected_revision": body.expected_revision,
                                                    "credentials": body.credentials},
                                    H.now(), settings)
        gateway_connection.check_now(store, vault, settings)
        if cloud_access.cell():
            cloud_access.sync_later(store, vault, settings)  # a Tico Cloud cell's new key: its access answer, now
        with store.read() as c:
            return gateway_connection.view(c, settings, manage=True)

    @app.delete("/api/v2/providers/gateway")
    def gateway_clear(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.transaction() as c:
            gateway_connection.clear(c, who, H.now(), settings)
            return gateway_connection.view(c, settings, manage=True)

    @app.post("/api/v2/providers/gateway/automatic")
    def gateway_automatic(request: Request):
        """Connect automatically again after the owner cleared the install's automatic key: the install enrolls with
        its own secret on the next check, which starts now."""
        who = request.state.identity
        auth.domain(who)
        with store.transaction() as c:
            gateway_connection.automatic_on(c, who, H.now())
        gateway_connection.check_later(store, app.state.vault, settings)
        with store.read() as c:
            return gateway_connection.view(c, settings, manage=True)

    @app.post("/api/v2/providers/gateway/check")
    def gateway_check(request: Request):
        """Check now: ask the Gateway whether the key works and how much of its cap is used."""
        who = request.state.identity
        auth.domain(who)
        gateway_connection.owner_only(who)
        gateway_connection.check_now(store, app.state.vault, settings)
        with store.read() as c:
            return gateway_connection.view(c, settings, manage=True)

    # ------------------------------------------------------------------ people and access
    def owner_only(who, what):
        auth.domain(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner " + what, 403)

    def admin_only(who, what):
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin " + what, 403)

    @app.get('/api/v2/settings/turn-levels')
    def turn_levels(request: Request):
        admin_only(request.state.identity, 'sees company turn settings')
        from . import org_turns
        with store.read_transaction() as c:
            # Settings only shows levels and in_use; a fleet snapshot would classify every
            # queued bot on every computer for fields this page never renders.
            return org_turns.admission(c)

    @app.post('/api/v2/settings/turn-levels')
    def turn_levels_set(request: Request, body: M.TurnLevels):
        from . import org_turns

        def permitted(c):
            auth.sync_access(c)
            who = request.state.identity
            # Rebuild human roles under the writer lock, including on an idempotent replay.
            current = auth.identity_for_actor(c, who.actor) if who.role in ('owner', 'human') and not who.support else who
            admin_only(current, 'changes company turn settings')

        return mutate(request, body, lambda c: org_turns.configure(c, request.state.identity.actor, body, settings),
                      check=permitted)

    # ------------------------------------------------------------------ billing (Settings > Billing)
    def billing_key(who, does="sees billing"):
        """The gateway key and Gateway URL Settings > Billing reads HQ with. Owners and admins only."""
        admin_only(who, does)
        with store.read() as c:
            return gateway_connection.target(c, app.state.vault, settings)[:2]

    @app.get("/api/v2/billing")
    def billing_view(request: Request, month: str = ""):
        """The company's balance and status, credits, usage by day and the month's statement, from Tico HQ; a Tico
        Cloud company's access (`cloud`) too, kept at once so the app and the page agree (backend/cloud_access.py)."""
        answer = company_billing.read(*billing_key(request.state.identity), month)
        cloud_access.from_billing(store, answer)
        return answer

    @app.get("/api/v2/cloud-access")
    def cloud_access_view(request: Request):
        """A Tico Cloud company's access for every page: open, frozen or locked, why, and where to pay. Null off
        cloud. Open to every signed-in person, also while locked."""
        views.human_only(request.state.identity)
        return {"cloud": cloud_access.view(cloud_access.current(store))}

    @app.get("/api/v2/billing/usage")
    def billing_day(request: Request, day: str = ""):
        """One day's usage by hour, from Tico HQ."""
        return company_billing.day(*billing_key(request.state.identity), day)

    @app.get("/api/v2/billing/statements/{month}.csv")
    def billing_statement_csv(request: Request, month: str):
        """The month's statement as CSV, the same lines and numbers as on screen."""
        body = company_billing.statement_csv(*billing_key(request.state.identity), month)
        return Response(body, media_type="text/csv; charset=utf-8", headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="tico-statement-%s.csv"' % month})

    # Settings > Computers: the company's hosted servers, and Start for a stopped one (HQ refuses while out of credit).
    @app.get("/api/v2/hosted-servers")
    def hosted_servers(request: Request):
        """The company's Tico server and bot computers HQ hosts, each on or stopped; `none` when HQ hosts nothing."""
        return company_billing.servers(*billing_key(request.state.identity, "sees hosted servers"))

    @app.post("/api/v2/hosted-servers/{server_id}/start")
    def hosted_server_start(request: Request, server_id: str):
        """Start one stopped hosted server; `paused` with the add-credit words while the company is out of credit."""
        if settings.rehearsal:      # a copy of real data sends nothing: never a real server's power
            raise Problem("rehearsal", "Hosted servers can't be started from a rehearsal", 409)
        return company_billing.start(*billing_key(request.state.identity, "starts hosted servers"), server_id)

    @app.get("/api/v2/access")
    def access_view(request: Request):
        """Who can sign in, the roles, what members may do and the company domain. Owners and admins."""
        who = request.state.identity
        admin_only(who, "sees who can sign in")
        with store.read() as c:
            return {**Access.view(c, settings, views.roster(c), settings.proxy_kind, auth.owner_id(c)),
                    "rules": team_rules.load(c), "you": auth.company_role(who),
                    # When each person was last sent an invite (backend/invites.py), and whether this install's
                    # sign-in could send one at all: only the built-in sign-in, through a Tico account issuer.
                    "invites": {pid: sent["at"] for pid, sent in invites.sent(c).items()},
                    "invite_email": invites.can_email(settings),
                    # Whether the issuer also makes accounts with a generated password (Create password).
                    "account_password": bool(invites.can_make(auth, settings))}

    @app.post("/api/v2/access/people")
    def access_person_add(request: Request, body: M.AccessPersonAdd):
        """Add a person to the roster and the sign-in allow list. An owner or admin may add anyone; a member only
        a coworker in the company's email domain (and only if `add_people` is on for them). BotOps adds a coworker in the
        Team's domain at once; an owner or admin may also add someone outside it."""
        caller = request.state.identity
        auth.domain(caller)

        def work(c):
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if who.role not in ("owner", "human"):
                raise Problem("forbidden", "Only people add people", 403)
            email = Access._valid_email(body.email)
            roster = views.roster(c)
            domains_ = Access.company_domains(c, settings, auth.owner_email)
            Access.check_may_add(c, settings, P.person(H.actor_id(who.actor), roster), auth.company_role(who), email, domains_)
            _, row = Access.add_person(c, who.actor, roster, name=body.name, email=email, title=body.title,
                                       team=body.team, reports_to=body.reports_to)
            return {"person": row["id"], "email": email, "name": row["name"]}
        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.post("/api/v2/access/people/{pid}/invite")
    def access_person_invite(request: Request, pid: str, body: M.Empty):
        """Owners and admins send someone on the roster an invite from Tico's own sign-in mail (backend/invites.py),
        never the company's mailbox; a refusal says why, so the owner can tell the person themselves."""
        who = request.state.identity
        admin_only(who, "sends invites")
        invites.request_key(request)
        with store.read() as c:
            person = P.person(pid, views.roster(c))
        if not person or person.get("hidden"):
            raise Problem("not_found", "Person not found", 404)
        invites.once(store, auth, settings, who, request, body, person)
        at = mutate(request, body, lambda c: invites.record(c, who.actor, pid))   # a repeat replays its receipt
        return {"person": pid, "invited_at": at}

    @app.post("/api/v2/access/people/{pid}/password")
    def access_person_password(request: Request, pid: str, body: M.Empty):
        """Owners and admins make someone on the roster a Tico account with a generated password (backend/invites.py
        `make_password`), answered once. Not `mutate`: its request cache would keep the password. Nothing else keeps
        it either; the event says only that an account was made."""
        who = request.state.identity
        admin_only(who, "makes accounts")
        invites.request_key(request)
        with store.read() as c:
            person = P.person(pid, views.roster(c))
        if not person or person.get("hidden"):
            raise Problem("not_found", "Person not found", 404)
        if pid == H.actor_id(who.actor):
            raise Problem("password_self", "You already sign in here", 422)
        password = invites.make_password(store, auth, settings, who, request, body, person)
        with store.transaction() as c:
            H.event(c, who.actor, "person.account_created", pid, {})
        return {"person": pid, "email": person["email"], "password": password}

    @app.post("/api/v2/access/people/{pid}")
    def access_person_edit(request: Request, pid: str, body: M.AccessPersonEdit):
        """Owners and admins edit a person, including what a member may do (create bots, add people). Only an
        owner makes or removes an admin. Through BotOps, a role or add_people change is a Confirm card."""
        caller = request.state.identity
        auth.domain(caller)

        def work(c):
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            admin_only(who, "edits people here")
            if body.left:
                raise Problem("forbidden", "Mark someone as left from their profile, which also ends their tokens", 422)
            roles = body.role is not None or body.bot_admin is not None
            if roles and who.role != "owner":
                raise Problem("forbidden", "Only an owner makes or removes admins", 403)
            if body.sign_in is not None:
                if pid == H.actor_id(who.actor):
                    raise Problem("forbidden", "You cannot turn off your own sign-in", 403)
                target = H.human(c, pid) or {}
                if who.role != "owner" and str(target.get("email") or "").lower() in auth.bot_admins:
                    raise Problem("forbidden", "Only an owner turns an admin's sign-in on or off", 403)
            # A role, add_people, sign-in or a message bot (it opens a mailbox) is authority; an email rewrites who is an Admin, and a team is an access
            # audience: each needs the requester's own click through BotOps.
            if (roles or any(getattr(body, k) is not None for k in ("add_people", "sign_in", "email", "team", "inbox_bot", "mailbox"))) and risky(who):
                changes = body.model_dump(exclude_none=True, exclude={"on_behalf_of"})
                return propose_card(c, who, "POST", "/api/v2/access/people/" + pid, changes,
                                    "Change " + ", ".join(changes) + " for "
                                    + ((H.human(c, pid) or {}).get("name") or pid))
            roster, lists = views.roster(c), Access.load_access(c, settings)
            row, admins, changed = Access.edit_person(c, who.actor, roster, pid, body, lists, auth.owner_email)
            if changed.get("sign_in") is False:
                c.execute("DELETE FROM oidc_sessions WHERE human=?", (pid,))    # open browsers are signed out now
            if changed.get("team"):
                from . import groups
                groups.leads_follow(c, "person:" + pid)        # a lead who left its group leads it no more
            if admins != lists["admins"]:
                Access.save_access(c, who.actor, {"expected_revision": lists["revision"]}, H.now(),
                                   bot_admins=admins, settings=settings)
            H.event(c, who.actor, "person.access_edited", pid, changed)
            return {"person": row["id"]}
        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.put("/api/v2/access/limits")
    def access_limits(request: Request, body: M.AccessLimits):
        """How many active bots one member may have. Owners and admins."""
        who = request.state.identity
        admin_only(who, "sets the bot limit")

        def work(c):
            stored = Access._load_json(c, Access.ACCESS) or {}
            before = Access.load_access(c, settings)
            Access._store(c, Access.ACCESS, {**before, **stored, "member_bot_limit": body.member_bot_limit})
            H.event(c, who.actor, "access.limits_updated", "",
                    {"before": before["member_bot_limit"], "after": body.member_bot_limit})
            return {"member_bot_limit": body.member_bot_limit}
        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.put("/api/v2/access/rules")
    def access_rules(request: Request, body: M.AccessRules):
        """Turn the team's fast defaults off, or back on (backend/team_rules.py). The owner; an admin may change only
        the rules in `team_rules.ADMIN_RULES` (Stale ticket review, Start fresh when the topic changes)."""
        who = request.state.identity
        sent = {name for name, value in body.model_dump().items() if value is not None}
        if sent and sent <= set(team_rules.ADMIN_RULES):
            admin_only(who, "changes " + ", ".join(sorted(sent)))
        else:
            owner_only(who, "changes the team's rules")
        result = mutate(request, body, lambda c: team_rules.save(c, who.actor, body.model_dump()))
        with store.read() as c:
            auth.sync_access(c)          # who is a credential administrator follows the rule
        return result

    @app.put("/api/v2/access/allow")
    def access_allow_save(request: Request, body: M.AccessAllowUpdate):
        who = request.state.identity
        owner_only(who, "changes who may sign in")

        def work(c):
            before, after = Access.save_access(c, who.actor, body.model_dump(), H.now(), settings=settings)
            H.event(c, who.actor, "access.allow_updated", "",
                    {"before": {k: before[k] for k in ("allowed", "allowed_domains", "revision")},
                     "after": {k: after[k] for k in ("allowed", "allowed_domains", "revision")}})
            return {"revision": after["revision"], "allowed": after["allowed"],
                    "allowed_domains": after["allowed_domains"]}
        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.post("/api/v2/access/owner")
    def access_owner_transfer(request: Request, body: M.OwnerTransfer):
        """Hand ownership to another active person. It takes effect with this commit."""
        who = request.state.identity
        owner_only(who, "transfers ownership")

        def work(c):
            owner = Access.load_owner(c, settings)
            record = Access.transfer(c, who.actor, views.roster(c), owner, body.person,
                                     expected_revision=body.expected_revision,
                                     previous_bot_admin=body.previous_owner_bot_admin,
                                     now=H.now(), settings=settings)
            return {"owner": record["email"], "revision": record["revision"]}
        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.post("/api/v2/access/owner/email")
    def access_owner_email(request: Request, body: M.OwnerEmailChange):
        """The owner changes their own sign-in address in place: the same person, every copy of the address at once.
        Signed in as themselves only: not through an API token, BotOps or the Assistant. From this commit on, the old
        address no longer signs in (docs/people.md, Change the owner's sign-in email)."""
        who = request.state.identity
        owner_only(who, "changes the owner's sign-in email")
        if who.via_token or who.via:
            raise Problem("forbidden", "Change the owner's sign-in email from a signed-in browser, as yourself", 403)
        if body.email.strip().lower() != body.confirm_email.strip().lower():
            raise Problem("email", "The two addresses do not match", 422)

        def work(c):
            record = Access.change_owner_email(c, who.actor, views.roster(c), Access.load_owner(c, settings), body.email,
                                               expected_revision=body.expected_revision, now=H.now(), settings=settings)
            return {"owner": record["email"], "revision": record["revision"], "person": H.actor_id(who.actor)}
        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.get("/api/v2/catalog")
    def catalog():
        """The bot templates this company may pick from, named for this company. Every signed-in
        identity reads it: runners and bots build repositories from the same cards."""
        with store.read() as c:
            return {"cards": onboarding.catalog(c)}

    # The /api/v2/ceo-bot path is the Strategy Bot's old one: clients built before the rename still call it, so it
    # stays a hidden alias of the route under it.
    @app.get("/api/v2/ceo-bot", include_in_schema=False)
    @app.get("/api/v2/strategy-bot")
    def strategy_bot_settings(request: Request):
        """The Strategy Bot's name, level and regular checks (backend/strategy_bot.py). How many items it put in each
        person's Needs you today is the owner's, an admin's and the Strategy Bot's to read; anyone else sees only their
        own count."""
        who = request.state.identity
        auth.domain(who)
        from . import strategy_bot
        with store.read() as c:
            value = strategy_bot.named(c, strategy_bot.view(c))
        if who.actor != "bot:" + strategy_bot.SLUG and auth.company_role(who) not in ("owner", "admin"):
            value["asked_today"] = {k: n for k, n in value["asked_today"].items() if k == who.actor}
        return value

    @app.post("/api/v2/goal-manager/turn-on")
    def turn_on_goal_manager(request: Request, body: M.Empty):
        who = request.state.identity
        if who.role != "owner":
            from . import strategy_bot
            with store.read() as c:
                raise Problem("forbidden", "Only the owner turns the " + strategy_bot.name(c) + " on", 403)
        return mutate(request, body, lambda c: onboarding.turn_on_goal_manager(c, who))

    @app.get("/api/v2/onboarding")
    def onboarding_record(request: Request):
        who = request.state.identity
        onboarding.require_reader(who)
        with store.read() as c:
            return onboarding.view(c, who)

    @app.put("/api/v2/onboarding")
    def onboarding_save(request: Request, body: M.OnboardingDraft):
        who = request.state.identity
        return mutate(request, body, lambda c: onboarding.save(c, who, body))

    @app.get("/api/v2/onboarding/departments")
    def onboarding_departments(request: Request):
        """The org builder's departments and cards, and whether this install may ask Tico HQ for suggestions."""
        return recruiter.departments(request.state.identity)

    @app.post("/api/v2/onboarding/recruit")
    def onboarding_recruit(request: Request, body: M.Recruit):
        """Suggested bots for one department. A sync handler, so the call to Tico HQ runs off the event loop; it
        writes nothing, so it is not a `mutate`."""
        return recruiter.recruit(request.state.identity, body)

    @app.post("/api/v2/onboarding/complete")
    def onboarding_complete(request: Request, body: M.Empty):
        who = request.state.identity
        return mutate(request, body, lambda c: onboarding.complete(c, who))

    @app.post("/api/v2/onboarding/company/preview")
    def onboarding_company_preview(request: Request, body: M.OnboardingCompanyFile):
        from . import company_import
        with store.read() as c:
            return company_import.preview(onboarding, c, request.state.identity, body.company_yaml)

    @app.post("/api/v2/onboarding/company/import")
    def onboarding_company_import(request: Request, body: M.OnboardingCompanyImport):
        from . import company_import
        return mutate(request, body, lambda c: company_import.apply(
            onboarding, c, request.state.identity, body.company_yaml, body.fingerprint))

    @app.post("/api/v2/onboarding/company/import-failure/dismiss")
    def onboarding_company_import_failure_dismiss(request: Request, body: M.Empty):
        """The owner leaves a kept setup-file failure they won't fix by importing (Health and the welcome page)."""
        from . import company_import
        return mutate(request, body, lambda c: company_import.dismiss(onboarding, c, request.state.identity))

    @app.post("/api/v2/onboarding/company/people/{key}/invite")
    def onboarding_company_invite(request: Request, key: str, body: M.OnboardingInvite):
        """Invite one person the company file from setup names: they go on the roster with this email (the seats that
        waited with the owner now report to them), then Tico's own sign-in mail invites them (backend/invites.py).
        Added but not sent is a Problem the owner sees; Invite again sends again."""
        from . import company_import
        who = request.state.identity
        owner_only(who, "invites the people from setup")
        row = mutate(request, body, lambda c: company_import.add_invitee(onboarding, c, who, key, body.email))
        sent = invites.once(store, auth, settings, who, request, body, row)
        with store.transaction() as c:                  # not a second `mutate`: one request has one idempotency key
            if sent or row["id"] not in invites.sent(c):      # a repeat keeps the first send's time
                invites.record(c, who.actor, row["id"])
            return onboarding.view(c, who)

    @app.post("/api/v2/onboarding/sample")
    def onboarding_sample(request: Request, body: M.Empty):
        """Start as Tico's sample company (backend/sample_company.py): five bots with short starter tasks. The owner,
        on a company not set up yet; it answers like `complete`."""
        who = request.state.identity
        return mutate(request, body, lambda c: onboarding.start_sample(c, who))

    @app.get(LOCAL_SIGNIN_PATH)
    def local_signin(token: str = "", next: str = "/"):
        """Turn the local owner secret into a browser session. Loopback deployments only."""
        with store.read() as c:
            if not auth.local_owner(token):
                raise Problem("identity", "This local sign-in link is not valid", 401)
            auth.owner_identity(c)
        # Only a same-origin path is returned to: never an absolute or protocol-relative URL.
        target = next if re.fullmatch(r"/[^/\\\s][^\s\\]*", next or "") else "/"
        response = RedirectResponse(target, status_code=302)
        # Lax, not Strict: the printed link is followed from a terminal or another page, and Chrome then
        # withholds a Strict cookie from the redirected request, which would land on the sign-in wall.
        # Writes are guarded by the Origin check. No Domain, so it stays with the host it was set on.
        response.set_cookie(auth.local_cookie(), token, httponly=True, samesite="lax", path="/",
                            max_age=LOCAL_COOKIE_DAYS * 86400, secure=settings.public_url.startswith("https://"))
        response.headers["Cache-Control"] = "no-store"
        return response

    oidc.register(app, auth)
    owner_link.register(app, auth)
    mcp_oauth.register(app, auth, store)

    @app.get(LOGOUT_PATH)
    def logout(request: Request):
        """End the browser session at whichever identity proxy fronts this server."""
        target, expire = auth.proxy.logout(request.headers) if auth.proxy else ("/", [])
        response = RedirectResponse(target, status_code=302)
        for name in expire:
            response.delete_cookie(name, path="/", secure=settings.public_url.startswith("https://") or not settings.loopback,
                                   httponly=True)
        if not auth.proxy:
            response.delete_cookie(auth.local_cookie(), path="/")
            response.delete_cookie(LOCAL_COOKIE, path="/")
        return response

    @app.get("/manifest.webmanifest")
    def manifest():
        """The installed app carries this environment's own name, not the product's."""
        try:
            data = json.loads((settings.ui_dir / "manifest.webmanifest").read_text())
        except (OSError, ValueError):
            data = {"start_url": "/", "scope": "/", "display": "standalone"}
        data.update(name=settings.app_name, short_name=settings.app_name,
                    description=settings.app_name + ", " + settings.company_name
                    + "'s AI COO and company hub.")
        return Response(encode(data), media_type="application/manifest+json")

    @app.get("/api/v2/openapi.json")
    def contract():
        """The stable v2 contract for a frontend (backend/openapi_v2.py); signed-in callers only."""
        from .openapi_v2 import spec
        return spec(app)

    @app.get("/api/v2/observability")
    def observability_config(request: Request):
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "This endpoint is available only to people", 403)
        config = browser_config(settings, who.actor)
        if config.get("posthog_key"):
            with store.read() as c:
                name = staff_display_name(c, who)
            if name:
                config["person_display_name"] = name
        return config

    @app.get("/api/v2/me")
    def me(request: Request):
        from .mcp import caller_kind
        who = request.state.identity
        return {"actor": who.actor, "role": who.role, "email": who.email,
                "runner_id": who.runner_id, "attempt_id": who.attempt_id, "agent": who.agent,
                "kind": caller_kind(auth, who)}

    # A person's own API tokens (backend/personal_tokens.py). Managed only from a browser
    # sign-in: a token is that person everywhere else, but it cannot mint or revoke tokens.
    @app.get("/api/v2/me/tokens")
    def my_tokens(request: Request):
        with store.read() as c:
            who = request.state.identity
            # can_create: the team chart offers Connect only to someone who may make a token (personal_tokens.create)
            return {"tokens": personal_tokens.listing(c, who), "can_create": personal_tokens.can_create(c, auth, who)}

    @app.post("/api/v2/me/tokens")
    def create_my_token(request: Request, body: M.PersonalTokenCreate):
        return mutate(request, body, lambda c: personal_tokens.create(c, auth, request.state.identity, body))

    @app.post("/api/v2/me/tokens/{token_id}/revoke")
    def revoke_my_token(request: Request, token_id: str, body: M.Empty):
        return mutate(request, body, lambda c: personal_tokens.revoke(c, auth, request.state.identity, token_id))

    # Everyone's tokens, for the owner and the Admins; they revoke one with the route above.
    @app.get("/api/v2/access/tokens")
    def all_tokens(request: Request):
        with store.read() as c:
            return {"tokens": personal_tokens.listing_all(c, auth, request.state.identity)}

    # "Connect an agent" (ui/connect-agent.js): the MCP address a person's own agent (Grok, Muse,
    # Claude, ...) is given beside a personal token. It is the runner hostname, where a bearer is let
    # through. When that is the one hostname and Cloudflare Access guards it, Access answers an
    # outside agent with its login page unless /api/v2/mcp is bypassed, so the dialog says so
    # (docs/connect-an-agent.md). The text is the MCP instructions too.
    @app.get("/api/v2/agent-skill")
    def agent_skill(request: Request):
        if request.state.identity.role not in ("human", "owner"):
            raise Problem("forbidden", "This endpoint is available only to people", 403)
        from .mcp import PATH as MCP_PATH
        behind_access = (settings.proxy_kind == "cloudflare"
                         and urlparse(settings.runner_url).hostname == urlparse(settings.public_url).hostname)
        return {"mcp_url": settings.runner_url + MCP_PATH, "text": WHO_NEEDS_ME, "access_bypass": behind_access}

    # What a caller who may only see a bot is told about it: its name, role, who runs it and who it
    # reports to. Its status, machine, queue and configuration are its activity, which is Read.
    SEE_ONLY = ("slug", "display_name", "state", "description", "team", "operator", "reports_to", "owners",
                "thread_mode", "temp", "access", "bot_owners", "onboarding_state", "private_tasks_default", "stage",
                "can_complete_onboarding")

    def bot_view(c, bot, level, access, registry_roster, registry_entries, who, rows):
        """One bot as the bot list and the bot detail show it: everything for a caller who may read
        it, only its profile for one who may only see it. `rows` (backend/bot_rows.py) holds what
        was read for every bot in the answer."""
        row = {k: v for k, v in bot.items() if k not in ("token_hash", "cwd", "thread_id")}
        config = rows.config(bot["slug"])
        assignment = rows.assignments.get(bot["slug"]) if level["read"] else None
        row["access"] = level
        row["team"] = config["team"] if config else None
        row["operator"] = config["operator"] if config else None
        row["revision"] = config["revision"] if config else None
        # `needs_setup` for a starter bot until it says its setup is done, then
        # `onboarded`; empty for every other bot (backend/onboarding.py).
        row["onboarding_state"] = (config["onboarding_state"] or "") if config else ""
        # Onboarding or Active (backend/lifecycle.py); a seat being recruited is not a bot and is never in this list.
        row["stage"] = lifecycle.stage(row["onboarding_state"])
        if row["stage"] == lifecycle.ONBOARDING:
            row["can_complete_onboarding"] = lifecycle.may_complete(auth, c, who, bot["slug"])
            row["onboarding_ready"] = lifecycle.ready_of(config["config_json"]) if config else None
        if config:
            repo = config["repo"] or ("emp-" + bot["slug"])
            from .shared_bots import follow
            declared = follow(c, bot["slug"], json.loads(config["config_json"]) if config["config_json"] else {},
                              rows.config)
            if declared.get("shared_from"):
                repo = declared.get("repo") or repo
                row.update({"model": declared.get("model") or "", "runtime": declared.get("runtime") or "",
                            "effort": declared.get("reasoning_effort") or "", "session": declared.get("session"),
                            "fallback": declared.get("fallback")})
            row.update({"description": config["description"] or "",
                        "harness": resolve_harness(declared, bot.get("runtime")),
                        "reports_to": config["reports_to"], "repo": repo,
                        "repo_url": repo_url(repo, settings.github_owner),
                        "bot_contact": declared.get("bot_contact") or "open",
                        "private_tasks_default": H.private_tasks_default(c, "bot:" + bot["slug"], rows.declared),
                        "template": declared.get("template") or "",
                        "template_version": declared.get("template_version") or "",
                        "update_subscriptions": update_feeds.chosen(declared),
                        "shared": bool(declared.get("shared")),
                        "shared_from": str(declared.get("shared_from") or ""),
                        "temp": bool(declared.get("temp")),
                        "compact_at": compact_at_of(declared),
                        # What its tasks run on: a model (empty: its own), effort and context length; null is all the same.
                        "task_model": task_models.stored(declared),
                        "keep_tasks_going": auto_tasks.of(json.loads(config["config_json"]) or {}),
                        "turns_at_once": start_places.bot_limit(json.loads(config["config_json"]) or {}),
                        # Plain words for a cheaper task model on a high-stakes bot, against its model now ("" else).
                        "task_model_warning": task_models.warning(c, settings, bot["slug"], declared,
                                                                  task_models.of(declared)),
                        # Whether it changes its direct reports (backend/report_managers.py): on unless turned off.
                        "manages_reports": report_managers.switch_on(declared),
                        "wake": wake_presets.effective(declared),
                        "profile": profiles.effective(declared),
                        "thread_mode": config["thread_mode"] or rooms.thread_mode(c, bot["slug"], config)})
        configured = json.loads(config["owner_ids_json"]) if config and config["owner_ids_json"] else None
        owner_rows = ([rows.human(owner) for owner in configured] if configured is not None
                      else P.primary_users(bot["slug"], registry_roster, registry_entries))
        row["owners"] = [P.brief(owner) for owner in owner_rows if owner]
        row["bot_owners"] = settings_admin.owner_rows(c, bot["slug"], config, registry_roster) if config else []
        if row.get("reports_to") and not str(row["reports_to"]).startswith("human:") \
                and not access.get(row["reports_to"], auth.FULL)["see"]:
            row["reports_to"] = ""      # a bot this caller may not see is not named to them
        if not level["read"]:
            return {k: v for k, v in row.items() if k in SEE_ONLY}
        slug = bot["slug"]
        row["task_flow"] = rows.task_flow.get(slug, {"going": 0, "waiting": 0})
        row["draining"] = slug in rows.draining
        # The one task its claims are limited to (POST /api/v2/bots/{bot}/control scope): its id when this reader
        # may read the task, or only that a limit is on.
        scope = rows.scopes.get(slug)
        scoped_task = H.task(c, scope) if scope else None
        row["claim_scoped"] = bool(scope)
        row["claim_task"] = scope if scoped_task and privacy.task_readable(c, who, scoped_task) else None
        status = rows.statuses.get(slug)
        row["status"] = privacy.status(c, who, dict(status), **rows.status_inputs[slug]) if status else status
        row["assignment"] = dict(assignment) if assignment else None
        row["online"] = bool(assignment and not assignment["revoked_at"] and assignment["last_seen"]
                             and assignment["last_seen"] > H.shift(H.now(), seconds=-60))
        row["queued"] = rows.queued[slug]
        row["next_run"] = sum(privacy.task_readable(c, who, t) for t in rows.next_run[slug])
        row["notes"] = rows.notes[slug]
        harness = rows.external_harness(slug)
        external = agents.presence(c, slug, harness) if harness else None
        row["agent"] = external["agent"] if external else None
        # Delivered through a desktop app (backend/app_delivery.py): where, and online is that computer's.
        # `app_delivery_app`: the desktop app the bot could be delivered through, for its settings.
        row["app_delivery"] = app_delivery.view(c, declared) if config and harness else None
        row["app_delivery_app"] = app_delivery.HARNESS_APP.get(row.get("harness"))
        if external and not (row["app_delivery"] or {}).get("active"):
            row["online"] = external["online"]
        return row

    @app.get("/api/v2/bots")
    def bots(request: Request, include_archived: str | None = None, can: str | None = None):
        """The bots the caller may see, each with `access` (their own see, read and write on it).
        `?can=read` or `?can=write` keeps only the bots they hold that level on."""
        who = request.state.identity
        auth.domain(who)
        if can not in (None, "", "read", "write"):
            raise Problem("can", "can is read or write", 422)
        # Archived bots are gone from every picker; an admin view that needs them asks with ?include_archived=1.
        with_archived = (include_archived or "").lower() in ("1", "true", "yes")
        from .chat_goals import readable_active
        with store.read() as c:
            privacy.snapshot(c)
            registry_roster, registry_entries = views.roster(c), views.entries(c, settings.github_owner)
            access = auth.bot_accesses(c, who)
            shown = []
            for bot in H.bots(c):
                level = access.get(bot["slug"], auth.FULL)
                if not level["see"] or (can and not level[can]):
                    continue
                if bot.get("state") == "archived" and not with_archived:
                    continue
                shown.append((bot, level))
            rows = BotRows(c, who, [bot["slug"] for bot, _ in shown],
                           {bot["slug"] for bot, level in shown if level["read"]})
            return [{**bot_view(c, bot, level, access, registry_roster, registry_entries, who, rows),
                     "goal_active": readable_active(c, auth, who, bot["slug"], rows.goals.get(bot["slug"], []))}
                    for bot, level in shown]

    @app.get("/api/v2/bots/{bot}")
    def bot_detail(request: Request, bot: str):
        """One bot: the profile a caller who can see it gets (name, role, who runs it, who it reports
        to, who it works for), and for one who can read it also its status, machine, queue and goals."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = H.bot(c, bot)
            if not row:
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            config = c.execute("SELECT reports_to,operator,goals FROM bot_config WHERE bot=?", (bot,)).fetchone()
            watched = [bot] + ([config["reports_to"]] if config and config["reports_to"] else [])
            access = auth.bot_accesses(c, who, watched)
            level = access[bot]
            value = bot_view(c, row, level, access, views.roster(c), views.entries(c, settings.github_owner), who,
                             BotRows(c, who, [bot], {bot} if level["read"] else set()))
            if config and level["read"]:
                value["goals"] = config["goals"] or ""
            reports = value.get("reports_to") or ""
            if reports.startswith("human:"):
                person = H.human(c, reports[6:])
                value["reports_to_name"] = (person or {}).get("name") or reports[6:]
            elif reports:
                value["reports_to_name"] = (H.bot(c, reports) or {}).get("display_name") or reports
            if value.get("operator"):
                value["operator_name"] = (H.human(c, value["operator"]) or {}).get("name") or value["operator"]
            return value

    @app.get("/api/v2/org")
    def org(request: Request, person: str | None = None, team: str | None = None, can: str | None = None):
        """The mixed people-and-bots org chart. Bots use this (and `hub team show` / `hub_team_show`) to
        find who handles a kind of work and how to reach them. Only the bots the caller may see,
        each with `access`, its `reports_to`, its `team` (its group's id), its `department` (its group's name)
        and its `template`; the groups come with their `parent`; `?can=read` or `?can=write` keeps those they hold that level on."""
        who = request.state.identity
        auth.domain(who)
        if can not in (None, "", "read", "write"):
            raise Problem("can", "can is read or write", 422)
        with store.read() as c:
            access = auth.bot_accesses(c, who)
            roster, configs = views.roster(c), views.entries(c)
            archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
            # The Strategy Bot heads the chart: bots move on the chart only; each keeps the `reports_to` it has
            # (backend/strategy_bot.py). A person who hid the Strategy Bot from their chart gets it without the Strategy
            # Bot, nothing moved under it.
            from . import strategy_bot
            hide_strategy = strategy_bot.hidden_for(c, who)
            chart = strategy_bot.chart_entries(c, settings, roster, configs, archived, hidden=hide_strategy)
            # A bot's bare person id in reports_to is that person (backend/people.py).
            view = P.org_view(roster, chart, archived, person_id=person or "", team=team or "", bare_people=True)
            view["org_groups"] = strategy_bot.chart_groups(roster, configs, chart, view["org_groups"], archived,
                                                      hidden=hide_strategy)
            for row in view["bots"]:
                row["reports_to"] = P._clean((configs.get(row["id"]) or {}).get("reports_to"))
            live = {row["slug"]: row for row in H.bots(c)}
            bots = []
            by_id = {row["id"]: row for row in view["bots"]}
            departments = P.bot_departments(configs, roster, archived)
            # On the chart: what the caller may see, less the Strategy Bot when they hid it (those under it hang from
            # the next thing up, as under a bot they may not see; their `reports_to` stays the stored one).
            def drawn(slug):
                return access.get(slug, auth.FULL)["see"] and not (hide_strategy and slug == strategy_bot.SLUG)
            for row in view["bots"]:
                level = access.get(row["id"], auth.FULL)
                if not drawn(row["id"]) or (can and not level[can]):
                    continue
                bot = live.get(row["id"]) or {}
                shown = {**row, "display_name": bot.get("display_name") or row["display_name"],
                         "department": departments.get(row["id"], ""),
                         "template": configs.get(row["id"], {}).get("template") or "",
                         "status": bot.get("state") or "", "access": level,
                         "onboarding_state": configs.get(row["id"], {}).get("onboarding_state") or ""}
                # A bot the caller may not see is not in the chart, so those under it hang from
                # the nearest thing above it that is: another bot, else its person or department.
                parent, seen = row["org_parent"], set()
                while parent.startswith("b:") and parent[2:] in by_id and parent not in seen and not drawn(parent[2:]):
                    seen.add(parent)
                    parent = by_id[parent[2:]]["org_parent"]
                shown["org_parent"] = parent
                if row["reports_to"] and not row["reports_to"].startswith("human:") \
                        and not access.get(row["reports_to"], auth.FULL)["see"]:
                    shown["reports_to"] = parent[2:] if parent.startswith("b:") else (
                        "human:" + parent[2:] if parent.startswith("p:") else "")
                bots.append(shown)
            # Recruiting seats hang where a bot in that place would; one under a bot the caller may not see hangs from
            # the nearest thing above it that they may, like that bot's own reports.
            placed = {"b:" + r["id"] for r in bots} | {"p:" + r["id"] for r in view["people"]} | {
                "g:" + r["id"] for r in view["org_groups"]}
            seats, every = [], lifecycle.all_seats(c)
            parents = {}
            for seat in c.execute("SELECT * FROM seats WHERE closed_at IS NULL ORDER BY created,id").fetchall():
                parent, seen = lifecycle.org_parent(seat, roster, chart, archived, every, True), set()
                while parent.startswith("b:") and parent[2:] in by_id and parent not in seen and not drawn(parent[2:]):
                    seen.add(parent)
                    parent = by_id[parent[2:]]["org_parent"]
                parents[seat["id"]] = parent

            def shown_here(parent, seen=()):
                """A seat under a seat is on this view while the top seat of its branch is."""
                if parent.startswith("s:") and parent[2:] in parents and parent not in seen:
                    return shown_here(parents[parent[2:]], (*seen, parent))
                return parent in placed
            for seat in c.execute("SELECT * FROM seats WHERE closed_at IS NULL ORDER BY created,id").fetchall():
                parent = parents[seat["id"]]
                if (person or team) and not shown_here(parent):
                    continue
                shown = {k: v for k, v in dict(seat).items()
                         if k in ("id", "title", "description", "team", "created", "agent", "agent_name", "request_state")}
                reports = seat["reports_to"]
                if reports and not reports.startswith("human:") and not access.get(reports, auth.FULL)["see"]:
                    reports = parent[2:] if parent.startswith("b:") else (
                        "human:" + parent[2:] if parent.startswith("p:") else "")
                seats.append({**shown, "kind": "seat", "stage": lifecycle.RECRUITING, "reports_to": reports,
                              "org_parent": parent})
            for row in bots:
                row["stage"] = lifecycle.stage(row["onboarding_state"])
            return {**view, "bots": bots, "seats": seats}

    def may_edit_person(c, who, pid):
        """Yourself, or someone who reports up to you (backend/people.py `manages`)."""
        return auth.manages(c, who, "person", pid)

    @app.post("/api/v2/people/{pid}")
    def person_update(request: Request, pid: str, body: M.PersonUpdate):
        caller = request.state.identity
        def work(c):
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            roster = views.roster(c)
            person = P.person(pid, roster)
            if person and person.get("hidden") and body.left:
                return P.profile(person)                   # already off the chart
            if not person or person.get("hidden"):
                raise Problem("not_found", "Person not found", 404)
            if body.left is not None and who.role != "owner":
                raise Problem("forbidden", "Only the owner takes someone off the org chart", 403)
            if body.left and pid == H.actor_id(who.actor):
                raise Problem("forbidden", "You cannot take yourself off the org chart", 403)
            if not may_edit_person(c, who, pid):
                raise Problem("forbidden", "You cannot edit this person's profile", 403)
            if body.reports_to is not None:
                boss = body.reports_to.strip()
                if boss == pid:
                    raise Problem("hierarchy", "A person cannot report to themselves", 422)
                if boss and (not P.person(boss, roster) or P.person(boss, roster).get("hidden")):
                    raise Problem("not_found", "Reports-to person was not found", 404)
                # Nobody moves under their own report: the chain from the new boss must not reach here.
                at, seen = boss, set()
                while at and at not in seen:
                    if at == pid:
                        raise Problem("hierarchy", "A person cannot report to someone below them", 422)
                    seen.add(at)
                    at = str((P.person(at, roster) or {}).get("reports_to") or "")
                # A manager may move their own reports, and may move them only under people they manage.
                if who.role != "owner" and boss and not auth.manages(c, who, "person", boss) and H.actor_id(who.actor) != boss:
                    raise Problem("forbidden", "You can only move people under yourself or your own reports", 403)
                if who.role != "owner" and not boss:
                    raise Problem("forbidden", "Only the owner puts someone at the top of the chart", 403)
            login = body.github.strip().lstrip("@").lower() if body.github is not None else None
            # A review request names one login (backend/github.py), so two people never share one.
            if login and any(row["id"] != pid and not row.get("hidden") and str(row.get("github") or "").lower() == login
                             for row in roster["people"]):
                raise Problem("github_taken", "Someone else on the team already has this GitHub login", 409)
            people = list(roster["people"])
            for i, row in enumerate(people):
                if row["id"] != pid:
                    continue
                if body.title is not None:
                    row = {**row, "title": body.title.strip()}
                if body.about is not None:
                    row = {**row, "about": body.about.strip()}
                if body.goals is not None:
                    row = {**row, "goals": body.goals}
                if body.notes is not None:
                    row = {**row, "notes": body.notes}
                if body.notify_slack_task_done is not None:
                    row = {**row, "notify_slack_task_done": body.notify_slack_task_done}
                if login is not None:
                    row = {**row, "github": login}
                if body.notify_slack_bot_messages is not None:
                    row = {**row, "notify_slack_bot_messages": body.notify_slack_bot_messages}
                if body.update_draft is not None:
                    row = {**row, "update_draft": body.update_draft}
                if body.slack_muted_bots is not None:
                    row = {**row, "slack_muted_bots": list(dict.fromkeys(body.slack_muted_bots))}
                if body.reports_to is not None:
                    row = {**row, "reports_to": body.reports_to.strip()}
                if body.left:
                    row = Access.leave(c, people, row)
                people[i] = row
                roster = {**roster, "people": people}
                from .report_order import roster_saved
                roster_saved(c, roster)                    # a new manager's report goes last there
                c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(roster),))
                from . import people_mirror
                people_mirror.changed()                    # `left` and sign-in changes reach the Tico account
                H.event(c, who.actor, "person.updated", pid, {"title": body.title is not None, "about": body.about is not None,
                                                              "goals": body.goals is not None, "notes": body.notes is not None,
                                                              "notify_slack_task_done": body.notify_slack_task_done,
                                                              "github": body.github is not None,
                                                              "notify_slack_bot_messages": body.notify_slack_bot_messages,
                                                              "update_draft": body.update_draft,
                                                              "slack_muted_bots": body.slack_muted_bots,
                                                              "reports_to": body.reports_to, "left": bool(body.left)})
                if caller is not who:
                    H.event(c, caller.actor, "person.update_delegated", pid,
                            {"on_behalf_of": who.actor, "message_id": body.on_behalf_of})
                return P.profile(row)
            raise Problem("not_found", "Person not found", 404)
        return mutate(request, body, work)

    @app.delete("/api/v2/people/{pid}")
    def person_remove(request: Request, pid: str):
        """The owner removes a left person who has no history, such as one added by mistake, for good
        (backend/people_remove.py). Anyone with history stays as left."""
        who = request.state.identity
        auth.domain(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner removes someone", 403)

        def person(c):
            found = P.person(pid, views.roster(c))
            if not found:
                raise Problem("not_found", "Person not found", 404)
            return found
        with store.read_transaction() as c:          # every table is read once, outside the write lock
            found = P.person(pid, views.roster(c))     # gone already: a retry's replay, or the 404 below
            if found and not people_remove.check(found) and people_remove.history(c, pid):
                raise Problem("has_history", people_remove.KEEPS, 409)

        def work(c):
            roster = views.roster(c)
            people_remove.remove(c, who.actor, roster, person(c))
            return {"person": pid, "removed": True}
        result = mutate(request, M.Empty(), work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.get("/api/v2/people/{pid}/slack")
    def person_slack(request: Request, pid: str):
        """Slack DMs between this person and Tico bots (one Tico app; each bot has its own hub thread)."""
        who = request.state.identity
        auth.domain(who)
        if who.role == "bot":
            raise Problem("forbidden", "Bots read Slack through their own conversations", 403)
        if not (who.role == "owner" or H.actor_id(who.actor) == pid):
            raise Problem("forbidden", "This Slack thread belongs to another person", 403)
        with store.read() as c:
            actor = "human:" + pid
            threads = []
            for row in c.execute(
                    "SELECT t.bot,t.channel,t.thread_ts,t.conversation_id,t.last_routed,t.created "
                    "FROM slack_threads t JOIN conversations v ON v.id=t.conversation_id "
                    "WHERE t.thread_ts='' AND v.participants_json LIKE ? "
                    "ORDER BY COALESCE(t.last_routed, t.created) DESC",
                    ('%"' + actor + '"%',)).fetchall():
                conv = H.conversation(c, row["conversation_id"])
                if not conv or conv.get("closed_at"):
                    continue
                try:
                    auth.conversation(c, who, conv["id"])
                except Problem:
                    continue
                messages = visual_view(c, who, privacy.page(c, who, conv["id"])["messages"])
                bot = H.bot(c, row["bot"]) or {}
                threads.append({"bot": row["bot"], "display_name": bot.get("display_name") or row["bot"],
                                "conversation_id": conv["id"], "channel": row["channel"],
                                "last_message_at": conv.get("last_message_at") or row["last_routed"],
                                "messages": messages})
            return {"person": pid, "threads": threads}

    @app.post("/api/v2/bots/{bot}/goals")
    def bot_goals(request: Request, bot: str, body: M.BotGoals):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if not H.bot(c, bot):
                raise Problem("not_found", "Unknown bot", 404)
            if not (auth.bot_manager(c, who, bot) or auth.operator(c, who, bot)):
                raise Problem("forbidden", "Only a person who manages this bot sets its goals", 403)
            c.execute("UPDATE bot_config SET goals=? WHERE bot=?", (body.goals, bot))
            if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (bot,)).fetchone():
                raise Problem("not_found", "Unknown bot", 404)
            who_name = (H.human(c, H.actor_id(who.actor)) or {}).get("name") or who.actor
            text = (f"{who_name} updated your goals.\n\n{body.goals}\n\n"
                    "Treat these as your current goals until they change again.")
            if not body.goals.strip():
                text = f"{who_name} cleared your goals. Work from your standing instructions until new goals are set."
            # The stored goals are the change: a bot manager's notice to a paused bot whose held queue is full is
            # only withheld, and the goals stand on the bot's settings.
            message, _ = H.unless_queue_full(c, lambda: H.say(c, who.actor, "bot:" + bot, text, kind="notice"))
            H.event(c, who.actor, "bot.goals", bot, {"chars": len(body.goals), **({} if message else {"notice": "queue_full"})})
            return {"bot": bot, "goals": body.goals, "message_id": message["id"] if message else None}
        return mutate(request, body, work)

    @app.get("/api/v2/models")
    def models(request: Request):
        auth.domain(request.state.identity)
        with store.read() as c:
            choice = Providers.load(c, settings)
            cat = gateway_page.catalog(c, settings)
        return {"models": list(MODEL_CATALOG), "harnesses": list(HARNESS_CATALOG),
                "gateway": gateway_tiers.view(cat), "enabled_providers": choice["enabled"],
                "default": {"runtime": choice["runtime"], "model": choice["model"]}}

    @app.get("/api/v2/conversations")
    def conversations(request: Request, bot: str | None = None, chat_with: str | None = None):
        """`chat_with=<bot>`: my open chats with that bot, however long ago the last message was
        (the bot page's chat). Otherwise my conversations, or the owner's view of a bot's."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if bot and who.role != "owner":
                raise Problem("forbidden", "Only the owner may inspect a bot's conversations", 403)
            if chat_with:
                from .shared_bots import route
                chat_with = H.actor_id(route(c, who.actor, "bot:" + chat_with))
            rows = (H.chats_with(c, who.actor, "bot:" + chat_with) if chat_with
                    else H.conversations_for(c, "bot:" + bot if bot else who.actor))
            visible = []
            for row in rows:
                try:
                    auth.conversation(c, who, row["id"])
                    visible.append(row)
                except Problem:
                    pass
            rows = visible
            for row in rows:
                # Room summaries may contain a task notice from a previous assignee.
                for key in ("last_message", "last_body", "preview"):
                    row.pop(key, None)
            return {"actor": who.actor, "conversations": rows}

    @app.post("/api/v2/conversations")
    def create_conversation(request: Request, body: M.ConversationCreate):
        who = request.state.identity
        def work(c):
            participants = [auth.target(c, who, p, need="write") for p in body.participants]
            if who.role == "bot" and any(p.startswith("human:") for p in participants):
                raise Problem("forbidden", "Use the current conversation to contact its human; use tasks for handoffs", 403)
            bots = [H.actor_id(p) for p in participants if p.startswith("bot:")]
            if who.role in ("owner", "human") and body.kind == "chat" and len(bots) == 1:
                if any(p not in ("bot:" + bots[0], who.actor) for p in participants):
                    raise Problem("participants", "Use the bot's canonical personal or shared room", 422)
                return rooms.chat_room(c, auth, who, bots[0], subject=body.subject)
            conv = H.open_conversation(c, who.actor, participants, kind=body.kind, subject=body.subject)
            if who.role == "bot" and who.attempt_id:
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            return conv
        return mutate(request, body, work)

    @app.get("/api/v2/conversations/{cid}/messages")
    def messages(request: Request, cid: str, since: str | None = None, before: str | None = None,
                 limit: int | None = None, max_bytes: int | None = None):
        with store.read() as c:
            auth.conversation(c, request.state.identity, cid)
            page = privacy.page(c, request.state.identity, cid, since=views.since_time(since), before=before,
                                **views.page_options(limit, max_bytes))
            turn_work.annotate(c, auth, request.state.identity, page["messages"])
            visual_view(c, request.state.identity, page["messages"])
            return {"conversation": H.conversation(c, cid), **page}

    @app.get("/api/v2/conversations/{cid}/outline")
    def outline(request: Request, cid: str):
        """The humans' prompts in a chat, oldest first, for the Outline list: id, time, a short excerpt and the task
        it names. It reaches past the loaded pages, so each row passes the same message and task checks a page does."""
        who = request.state.identity
        with store.read() as c:
            auth.conversation(c, who, cid)
            prompts = []
            for row in c.execute("SELECT * FROM messages WHERE conversation_id=? AND from_actor LIKE 'human:%' "
                                 "AND deleted_at IS NULL ORDER BY rowid DESC", (cid,)):
                refs = H._json(row["refs_json"], {}) or {}
                if refs.get("goal_action") or refs.get("maintenance") or not privacy.message_readable(c, privacy.actor(who), row):
                    continue
                task, tid = None, refs.get("task") or refs.get("task_id")
                tid = tid[0] if isinstance(tid, list) and tid else tid
                if isinstance(tid, str) and (t := H.task(c, tid)) and privacy.task_readable(c, who, t):
                    task = {"id": tid, "title": t["title"]}
                text = " ".join(str(row["body"] or "").split())
                prompts.append({"id": row["id"], "created": row["created"], "task": task,
                                "text": text if len(text) <= 140 else text[:139].rstrip() + "…"})
                if len(prompts) >= 500:
                    break
            return {"prompts": prompts[::-1]}

    # Org rule checks (backend/org_rule_check.py): a bot's output is checked before its write, outside the write's
    # transaction (`rule_check`); the verdict is applied inside it, and a held write writes nothing but its hold.
    def rule_target(c, who, ref):
        try:
            return auth.target(c, who, ref)
        except Problem:
            return H.resolve_actor(c, ref) or str(ref or "")

    def rule_task_audience(c, row, author, text="", *extra):
        """A task write's audience: owner, requester, the people a comment @mentions, and anyone else named."""
        people = H.mentioned_people(c, text, row, author) if text else []
        return [a for a in (row["owner"], row["requester"], *people, *extra) if a]

    def rule_message_audience(c, who, body):
        out = {rule_target(c, who, body.to)}
        if body.conversation_id and (conv := H.conversation(c, body.conversation_id)):
            out |= set(conv.get("participants") or [])
        return sorted(out)

    def rule_check(who, operation, output, audience, obj="", context=None):
        """The check for one bot write, or None (a person's write, nothing to judge, a write the caller cannot make:
        the write itself refuses that)."""
        if not output or not org_rule_check.author_of(who, settings.assistant_bot):
            return None
        with store.read() as c:
            if not org_rule_check.in_force(c):
                return None             # no rule in force: the audience (a roster read, @mentions) is never resolved
            try:
                recipients = audience(c)
            except Problem:
                return None
        return org_rule_check.preflight(store, app.state.judge, who, operation, output, recipients, context, obj)

    def rule_public(body):
        """Whether a task write makes the task public: the whole company reads it."""
        return getattr(body, "visibility", None) == "public" or bool(getattr(body, "public_title", None) or
                                                                        getattr(body, "public_summary", None))

    def rule_create_text(body):
        """What a new task says, as the rule check reads it: its title and body, and its public title and summary."""
        return "\n\n".join(filter(None, (body.title, body.body, getattr(body, "public_title", None),
                                          getattr(body, "public_summary", None))))

    def rule_warned_fields(gate, body, row=None, approved=False):
        """A warned task write's recipient copies: the warning on every multi-line text field the write sets. A title
        (and public title) is kept whole and unmarked: a new task carries the line on its body, and on its public
        summary when the store makes it public (its type's default included: H.task_create's `public_warning`). A
        change that publishes (a public title on a task that is or becomes public, or making it public) carries the
        line on the public summary the roadmap shows. Neither does when the public text goes out only as a person
        approved it (`approved`, roadmaps.exact_approval): that text is kept exactly as approved, the approver its
        safeguard. Any other change that sets no other text field (a title, a hand-off alone) puts the line before the
        task's note (`row`'s), which stays whole."""
        out = {}
        for key in ("body", "note") if approved or row is None else ("body", "note", "public_summary"):
            value = getattr(body, key, None)
            if value:
                out[key] = org_rule_check.warned_text(gate, value)
        if row is None:
            if "body" not in out:
                out["body"] = org_rule_check.warned_note(gate, getattr(body, "body", None) or "")
            return out
        if not approved and "public_summary" not in out and rule_shows_public(body, row):
            # The public roadmap shows the public title and summary, never the note: the line goes on the summary it
            # will show (the one kept, or the line alone when there is none), the title kept whole.
            summary = getattr(body, "public_summary", None)
            # A kept summary warned before carries only this line: the bot's own text stays within the cap.
            kept = summary if summary is not None else org_rule_check.unwarned(row.get("public_summary"))
            out["public_summary"] = org_rule_check.warned_note(gate, kept)
        if not out:
            out["note"] = org_rule_check.warned_note(gate, row.get("note") or "")
        return out

    def rule_summary_allowance(gate, warned):
        """How far a warned public summary may pass the cap: the length of the line the server put before it."""
        line = org_rule_check.warn_line(gate)
        return len(line) + 2 if line and "public_summary" in warned else 0

    def rule_newly_public(body, row):
        """Whether a task change makes a task public that was not: its public text, written before, now reaches
        everyone."""
        return getattr(body, "visibility", None) == "public" and row.get("visibility") != "public"

    def rule_shows_public(body, row):
        """Whether a task change puts public text before the public roadmap's readers: it makes the task public, or
        gives one that is or becomes public a new public title."""
        public = (getattr(body, "visibility", None) or row.get("visibility")) == "public"
        return public and (rule_newly_public(body, row) or bool(getattr(body, "public_title", None)))

    def rule_publishes(body, row):
        """Whether a task change puts text before the whole company: it writes public text, or makes the task
        public."""
        return bool(getattr(body, "public_title", None) or getattr(body, "public_summary", None)) or \
            rule_newly_public(body, row)

    def rule_update_audience(c, who, body, row):
        """A task change's audience: the task's people and a new owner, and the whole company when it publishes."""
        return rule_task_audience(c, row, who.actor, "", *([rule_target(c, who, body.owner)] if body.owner else []),
                                  *(org_rule_check.company(c) if rule_publishes(body, row) else []))

    def rule_update_text(body, row):
        """What a task change says, as the rule check reads it: its text fields, the task itself when it is handed to
        a new owner, and the public text already written when the change alone makes the task public."""
        parts = [f"{label}: {value}" for key, label in (("title", "Title"), ("body", "Body"), ("note", "Note"),
                                                        ("public_title", "Public title"),
                                                        ("public_summary", "Public summary"))
                 if (value := getattr(body, key, None))]
        if rule_newly_public(body, row):
            parts += [f"{label}: {row[key]}" for key, label in (("public_title", "Public title"),
                                                                ("public_summary", "Public summary"))
                      if row.get(key) and getattr(body, key, None) is None]   # an explicit "" clears it: no text
        if getattr(body, "owner", None):
            parts.append(f"Assigned: {row['title']}\n\n{row.get('body') or ''}".rstrip())
        return "\n\n".join(parts)

    def rule_update_check(who, tid, body, row=None):
        """The check for one task change (`POST /api/v2/tasks/{tid}`, each task of a bulk change)."""
        if not org_rule_check.author_of(who, settings.assistant_bot):
            return None
        if row is None:
            with store.read() as c:
                try:
                    row = auth.task(c, who, auth.resolve_task(c, who, tid))
                except Problem:
                    return None
        text = rule_update_text(body, row)
        if not text:
            return None
        only_owner = body.owner and not any(getattr(body, k, None) for k in ("title", "body", "note", "public_title",
                                                                              "public_summary"))
        return rule_check(who, "task_owner" if only_owner else "task_update", text,
                          lambda c: rule_update_audience(c, who, body, row),
                          obj=f"{row['id']}@{row['version']}", context={"task": row["title"]})

    def message_ingest(who, body):
        """Ask ingest (backend/ask_ingest.py): a bot's question to a person is judged before it is written, whatever
        task or message it refers to; sent back with the filter on, it is withdrawn as soon as it is asked. Outside
        the write, as the rule check is."""
        if body.kind != "ask":
            return None
        title = body.text.strip().split("\n")[0][:300]
        tref = (body.refs or {}).get("task")
        tref = tref[0] if isinstance(tref, list) and tref else tref
        if isinstance(tref, str) and ask_ingest.gated(who, settings.assistant_bot):
            # The task's title is judged only when the bot may read the task; the send refuses it otherwise.
            with store.read() as c:
                try:
                    title = auth.task(c, who, tref).get("title") or title
                except Problem:
                    pass
        return ask_ingest.preflight(store, app.state.judge, who, body.to, title, body.text,
                                    kind="question" if tref else "loose", asked=(body.refs or {}).get("questions"))

    def message_write(c, who, body, rc=None, ingest=None):
        """One message, as every route that sends one writes it (`/messages`, a conversation's `/messages`) and Send
        anyway replays it: the org rule check's verdict, then ask ingest's, then the send."""
        rule = org_rule_check.apply(c, rc, body.text, {"body": body.model_dump(mode="json", by_alias=True)})
        if rule and rule.get("held"):
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.answer(rule)
        sent = body.model_copy(update={"text": org_rule_check.warned_text(rule, body.text)}) if rule else body
        # Judged against the plate before the question is on it.
        gate = ask_ingest.apply(c, ingest) if ingest else None
        message = send(c, who, sent)
        if not ask_ingest.matches(ingest, message.get("to_actor")) or message.get("kind") != "ask":
            if ingest:
                ask_ingest.skipped(c, ingest, "target")
            return {**message, **org_rule_check.answer(rule)}
        tid = H.message_task_id(H.message(c, message["id"]), H.conversation(c, message["conversation_id"]))
        verdict = {**ingest, "kind": "question" if tid else "loose"}
        ask_ingest.record(c, verdict, gate, tid or message["id"], message["id"])
        if gate["acts"]:
            ask_ingest.withdraw_loose(c, who.actor, message["id"], ingest["person"])
        return {**message, **ask_ingest.reply(gate), **org_rule_check.answer(rule)}

    def message_checks(who, body, retry=False):
        """Both checks of one message, outside the write: the rule check, then ask ingest (a held one skips it, and so
        does a saved retry, `retry`: `mutate` replays its answer without the write)."""
        rc = rule_check(who, "message", body.text, lambda c: rule_message_audience(c, who, body))
        return rc, (None if org_rule_check.holds(rc) or retry else message_ingest(who, body))

    @app.post("/api/v2/messages")
    def say(request: Request, body: M.MessageCreate):
        who = request.state.identity
        rc, ingest = message_checks(who, body, retry=saved_retry(request, who))
        return ask_ingest.judged(store, ingest, lambda: mutate(request, body, lambda c: message_write(
            c, who, body, rc, ingest)))

    def rule_prepare_message(store_, bot, replay):
        return message_ingest(bot, M.MessageCreate.model_validate(replay["body"]))

    @org_rule_check.replayer("message", prepare=rule_prepare_message)
    def rule_replay_message(c, bot, hold, replay, ingest, gate=None):
        # `send` checks the destination as the bot and its run (task_privacy.require_destination).
        return message_write(c, bot, M.MessageCreate.model_validate(replay["body"]), org_rule_check.decided(gate),
                             ingest)

    def checked_write(request, body, who, operation, output, audience, write, replay, obj="", context=None):
        """A bot's write through the org rule check (backend/org_rule_check.py), for routes with no checks of their
        own: the check outside the transaction, its verdict inside. `write(c, gate)` is the normal write (a warned
        one puts `org_rule_check.warned_text(gate, text)` on the recipient's copy); held, nothing is written."""
        rc = rule_check(who, operation, output, audience, obj, context)

        def work(c):
            gate = org_rule_check.apply(c, rc, output, replay)
            if gate and gate.get("held"):
                return org_rule_check.answer(gate)
            out = write(c, gate)
            return {**out, **org_rule_check.answer(gate)} if isinstance(out, dict) else out
        return mutate(request, body, work)

    @app.get("/api/v2/messages/{mid}")
    def message(request: Request, mid: str):
        with store.read() as c:
            row = H.message(c, mid)
            if not row or row.get("deleted_at"):
                raise Problem("not_found", "Message not found", 404)
            who = request.state.identity
            privacy.require_message(c, who, row)
            if who.role == "bot" and (row.get("to_actor") == who.actor or row.get("from_actor") == who.actor):
                return visual_view(c, who, [row])[0]
            auth.conversation(c, who, row["conversation_id"])
            return visual_view(c, who, [row])[0]

    @app.post("/api/v2/chat/{bot}")
    def chat(request: Request, bot: str, body: M.ChatCreate):
        who = request.state.identity
        # A person's message in their direct chat: a new topic clears the session first (backend/topic_change.py).
        topic = topic_check(request, who, bot, body)
        sent = M.MessageCreate(to="bot:" + bot, text=body.text, refs=body.refs, command=body.command,
                               attachments=body.attachments, delivery=body.delivery, defer_for=body.defer_for)
        # A bot's message here is a message like any other: the same checks and write as `/messages`, held or warned
        # the same way, and replayed by Send anyway as one.
        rc, ingest = message_checks(who, sent, retry=saved_retry(request, who))

        def work(c):
            cleared = topic_change.apply(c, settings, who, bot, topic)
            message = message_write(c, who, sent, rc, ingest)
            if "id" not in message:
                return message              # held by an org rule: the answer is the hold alone, and nothing was sent
            # What the checks tell the sender (a warning, ask ingest's word) rides beside the message, not in it.
            extra = {k: message.pop(k) for k in ("rule_warning", "ingest") if k in message}
            if cleared:
                message = topic_change.record(c, who, message, topic)
            onboarding.start_setup(c, who, bot)
            return {"conversation": H.conversation(c, message["conversation_id"]), "message": message, **extra}
        return ask_ingest.judged(store, ingest, lambda: mutate(request, body, work))

    def topic_check(request, who, bot, body):
        """The topic check's outcome for a person's chat message, or None: not a person, a retried request (its first
        try already decided), a bot whose chat is another's (a shared or the Assistant's), or a send the check never
        looks at (`topic_change.considered`: a command, a task chat's, or one carrying files)."""
        if who.role not in ("owner", "human") or who.via:
            return None
        key, path = request.headers.get("idempotency-key"), request.url.path
        if not key:
            return None

        def admit(c):                         # read with the gate, inside the check's time budget
            from .shared_bots import route
            if c.execute("SELECT 1 FROM idempotency WHERE actor=? AND operation=? AND key=?",
                         (who.actor, path, key)).fetchone():
                return False
            target = H.actor_id(route(c, who.actor, "bot:" + bot))
            return (target == bot and bot not in (settings.assistant_bot, strategy_bot.SLUG)
                    and views.may_chat(c, auth, who, bot))
        return topic_change.check(store, app.state.judge, who, bot, body, admit, uploads=())

    # ------------------------------------------------------------------ updates (backend/updates.py)
    def update_visible(c, who, mine=False, scope=None):
        """The authors whose updates `who` may read (backend/updates.py visible_authors)."""
        return updates.visible_authors(c, auth, who, mine, scope)

    def update_scope(scope):
        if scope not in (None, "", "all", "direct"):
            raise Problem("scope", "scope is all or direct", 422)
        return scope or None

    def readable_update_ids(c, who, ids):
        """Validate the whole personal batch before a read/archive write can mutate any row."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return ids
        readable, meetings = update_visible(c, who), updates.meeting_reader(who)
        rows = c.execute("SELECT id, bot, meetings FROM updates WHERE id IN (" + ",".join("?" * len(ids)) + ")",
                          ids).fetchall()
        if len(rows) != len(ids) or any(row["bot"] not in readable or (row["meetings"] and not meetings)
                                        for row in rows):
            raise Problem("not_found", "Update not found", 404)
        return ids

    def require_update(c, who, row):
        """A bot's update takes the bot's own gate (404 unseen, 403 seen without Read); a person's
        is for everyone on the chart, so anyone else gets 404. One flagged `meetings` is not found for
        anyone but a meeting reader (`updates.meeting_reader`)."""
        if updates.withheld(c, who, row["id"]):
            raise Problem("not_found", "Update not found", 404)
        if not updates.is_person(row["bot"]):
            auth.require_read(c, who, row["bot"], "Update not found")
        elif row["bot"] not in update_visible(c, who):
            raise Problem("not_found", "Update not found", 404)

    @app.get("/api/v2/updates")
    def updates_list(request: Request, kind: str | None = None, bot: str | None = None, unread: bool = False,
                     before: str | None = None, limit: int = 40, mine: bool = False,
                     archive: bool = False, include_archive: bool = False, scope: str | None = None):
        who = request.state.identity
        auth.domain(who)
        if kind and kind not in updates.KINDS:
            raise Problem("kind", "kind is daily or weekly", 422)
        with store.read() as c:
            return updates.listing(c, who.actor, update_visible(c, who, mine, update_scope(scope)), kind=kind, bot=bot, unread=unread,
                                   before=before, limit=max(1, min(limit, 100)), archive=archive,
                                   include_archive=include_archive, may_read=update_visible(c, who),
                                   meetings=updates.meeting_reader(who))

    @app.get("/api/v2/updates/unread")
    def updates_unread(request: Request, mine: bool = False, scope: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            from .meetings import pending_count
            return {"unread": updates.count_unread(c, who.actor, update_visible(c, who, mine, update_scope(scope)),
                                                   meetings=updates.meeting_reader(who)),
                    "meetings_pending": pending_count(c, who)}

    @app.get("/api/v2/updates/tree")
    def updates_tree(request: Request, kind: str = "daily", day: str | None = None, scope: str | None = None):
        """One day's updates nested by the org chart: below you by default, or the whole chart."""
        who = request.state.identity
        auth.domain(who)
        if kind not in updates.KINDS:
            raise Problem("kind", "kind is daily or weekly", 422)
        if scope not in (None, "", "below", "all"):
            raise Problem("scope", "scope is below or all", 422)
        if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise Problem("day", "day is YYYY-MM-DD", 422)
        with store.read() as c:
            return updates.tree(c, who.actor, update_visible(c, who), kind, day or None, scope or None,
                                meetings=updates.meeting_reader(who))

    @app.get("/api/v2/updates/{uid}")
    def update_show(request: Request, uid: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = updates.one(c, uid)
            if not row:
                raise Problem("not_found", "Update not found", 404)
            require_update(c, who, row)
            read = c.execute("SELECT 1 FROM update_reads WHERE update_id=? AND actor=?", (uid, who.actor)).fetchone()
            # A reply lands in the replier's own room with the bot; show only rooms this caller may read.
            rooms_ok = {}
            def readable(message):
                cid = message["conversation_id"]
                if cid not in rooms_ok:
                    try:
                        auth.conversation(c, who, cid)
                        rooms_ok[cid] = True
                    except Problem:
                        rooms_ok[cid] = False
                return rooms_ok[cid]
            thread = [m for m in updates.thread(c, uid) if readable(m)]
            row = updates.with_inputs(c, row, update_visible(c, who), updates.meeting_reader(who))
            return {"update": {**row, "read": bool(read), "archived": updates.archived(c, who.actor, uid)},
                    "thread": thread}

    def update_kind(body):
        """The kind of update a post stores (updates.effective_kind): a daily drops any slides, a week in review its
        bullets."""
        return updates.effective_kind(body.kind, body.slides, body.day)

    def update_text(body):
        """What an update says, as the rule check reads it: what the post stores, a daily's bullets or every word of a
        week in review's slides."""
        if update_kind(body) == "weekly":
            slides = body.slides.model_dump(exclude_none=True) if body.slides else None
            return json.dumps(slides, sort_keys=True) if slides else ""
        return body.body.strip()

    def update_write(c, who, body, rc=None, replayed=False, named_day=True):
        """One update, as `POST /api/v2/updates` posts it and Send anyway replays it (`replayed`): a bot's is checked
        against the org rules for the whole company, which reads it. Held, nothing is posted; warned, the line leads
        the update (a week in review's goal, its headline). `body` names its day and kind (update_fixed), so a replay
        posts the update that was held, or nothing: a day that can no longer be posted is a 409, the hold kept. A live
        post that named no day (`named_day` False) is for the day it is written, since the rule check may have run past
        midnight; its kind stays as it arrived."""
        # A bot posts its own; a person posts their own, for today, as `human:<id>`. A person's
        # draft is written by the server (updates.write_draft), never posted through here.
        if who.role == "bot":
            author = H.actor_id(who.actor)
        elif who.role in ("owner", "human") and not who.via:
            author = who.actor
        else:
            raise Problem("identity", "A bot or a person posts their own update", 403)
        if not replayed and not named_day:
            body = body.model_copy(update={"day": updates.today()})
        slides = body.slides.model_dump(exclude_none=True) if body.slides else None
        try:
            updates.check_day(c, author, body.day)
        except Problem:
            if not replayed:
                raise
            raise Problem("changed", f"The {body.kind or 'held'} update for {body.day} can no longer be posted; "
                          "it stays held. Post it again for today to check it.", 409) from None
        rule = org_rule_check.apply(c, rc, update_text(body), {"body": body.model_dump(mode="json")})
        if rule and rule.get("held"):
            return org_rule_check.answer(rule)
        # The warn line goes on after the update's own words pass the writing checks (updates.post's `warning`).
        return {"update": updates.post(c, author, body.body, kind=body.kind, day=body.day, slides=slides,
                                       replace_draft=body.replace_draft, reader=updates.meeting_reader(who),
                                       warning=org_rule_check.warn_line(rule)),
                **org_rule_check.answer(rule)}

    def update_fixed(body):
        """The update a post means, its day and kind named, before anything is checked: a hold keeps them, so Send
        anyway posts that day's update of that kind, never today's or a week in review a Friday would make it."""
        from datetime import date
        day = body.day or updates.today()
        try:
            date.fromisoformat(day)
        except ValueError:
            raise Problem("day", "An update is for today, or for the day Tico asked you to redo", 422) from None
        return body.model_copy(update={"day": day, "kind": updates.effective_kind(body.kind, body.slides, day)})

    @app.post("/api/v2/updates")
    def update_post(request: Request, body: M.UpdatePost):
        who = request.state.identity
        fixed = update_fixed(body)
        rc = rule_check(who, "update", update_text(fixed), lambda c: org_rule_check.company(c))
        return mutate(request, body, lambda c: update_write(c, who, fixed, rc, named_day=body.day is not None))

    @org_rule_check.replayer("update", audience=lambda c, bot, replay: org_rule_check.company(c))
    def rule_replay_update_post(c, bot, hold, replay, ingest, gate=None):
        return update_write(c, bot, M.UpdatePost.model_validate(replay["body"]), org_rule_check.decided(gate),
                            replayed=True)

    @app.post("/api/v2/updates/redo")
    def update_redo(request: Request, body: M.UpdateRedo):
        """Ask the bots again for a past day's update in the current shape (a week in review as slides)."""
        who = request.state.identity
        owner_only(who, "asks the bots to redo an update")
        def work(c):
            return {"kind": body.kind, "day": body.day, "bots": updates.redo(c, body.kind, body.day, body.bots, body.managers)}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/read")
    def updates_read(request: Request, body: M.UpdateRead):
        who = request.state.identity
        def work(c):
            views.human_only(who)
            if body.all:
                visible = update_visible(c, who)
                ids = updates.inbox_ids(c, who.actor, visible, created_after=H.shift(H.now(), days=-30),
                                        meetings=updates.meeting_reader(who))
            else:
                ids = readable_update_ids(c, who, body.ids)
            return {"marked": updates.mark(c, who.actor, ids, read=body.read), "read": body.read}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/archive")
    def updates_archive(request: Request, body: M.UpdateArchive):
        who = request.state.identity
        auth.domain(who)
        def work(c):
            views.human_only(who)
            ids = readable_update_ids(c, who, body.ids)
            updates.set_archived(c, who.actor, ids, archived=body.archived)
            for uid in ids:
                H.event(c, who.actor, "update.archive" if body.archived else "update.restore", uid, {})
            return {"updated": len(ids), "archived": body.archived,
                    "unread": updates.count_unread(c, who.actor, update_visible(c, who),
                                                   meetings=updates.meeting_reader(who))}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/{uid}/reply")
    def update_reply(request: Request, uid: str, body: M.UpdateReply):
        who = request.state.identity
        def work(c):
            views.human_only(who)
            row = updates.one(c, uid)
            if not row:
                raise Problem("not_found", "Update not found", 404)
            require_update(c, who, row)
            if updates.is_person(row["bot"]):
                raise Problem("reply", "A person's update takes no replies here; message them instead", 422)
            # A reply is both a comment on the update and a message in the bot's
            # chat, so the bot's session gets it and it shows in both places.
            text = f"Re your update \"{row['headline']}\": {body.text}"
            message = send(c, who, M.MessageCreate(to="bot:" + row["bot"], text=text, refs={"update": uid}))
            updates.mark(c, who.actor, [uid], read=True)
            return {"message": message, "thread": updates.thread(c, uid)}
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/updates")
    def update_settings_show(request: Request, bot: str):
        auth.domain(request.state.identity)
        with store.read() as c:
            auth.require_read(c, request.state.identity, bot)
            return {"bot": bot, **updates.settings(c, bot)}

    @app.post("/api/v2/bots/{bot}/updates")
    def update_settings(request: Request, bot: str, body: M.UpdateSettings):
        who = request.state.identity
        def work(c):
            may_edit_routines(c, who, bot)
            return {"bot": bot, **updates.set_settings(c, bot, body.daily, body.weekly)}
        return mutate(request, body, work)

    @app.post("/api/v2/chat/{bot}/new")
    def new_chat(request: Request, bot: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            if who.role not in ("owner", "human"):
                raise Problem("identity", "Only a person can start a fresh chat", 403)
            from .shared_bots import route
            target_bot = H.actor_id(route(c, who.actor, "bot:" + bot))
            if rooms.thread_mode(c, target_bot) != rooms.PERSONAL:
                raise Problem("shared_room", "A shared bot room cannot be reset by one member", 409)
            views.require_chat(c, auth, who, target_bot)
            archived = rooms.archive_personal_room(c, who.actor, target_bot)
            return {"archived": archived["id"] if archived else None}
        return mutate(request, body, work)

    # A batch: what bots need from the person, frozen, walked one item at a time; responses are
    # collected and applied together on commit (backend/batch.py). Any front end runs it.
    def batch_send(c, who, text, refs, to):
        # Each bot hears about its own items in the person's chat with it.
        return send(c, who, M.MessageCreate(to=to, text=text, kind="ask", refs=refs))

    @app.post("/api/v2/batch")
    def batch_start(request: Request, body: M.BatchStart):
        who = request.state.identity
        views.human_only(who)
        def work(c):
            privacy.require_batch(c, who, batch.current(c, who.actor))
            items = views.needs_items(c, auth, who, task_view)
            snapshot = views.fleet_snapshot_cached(c, auth, who, task_view)
            return batch.start(c, who.actor, items, snapshot, scope=body.bot)
        return mutate(request, body, work)

    @app.get("/api/v2/live/brief")
    def live_brief(request: Request, since: str = ""):
        """One fast read for a person talking on the go (Live through Grok Bot).
        What is broken, who is waiting on them and how much, and what bots said to them since
        `since` (default: the last 12 hours). Reads only; consumes nothing."""
        who = request.state.identity
        views.human_only(who)
        if since and (not H.parse_ts(since) or H.parse_ts(since).tzinfo is None):
            raise Problem("date", "since must be an ISO-8601 date/time with a timezone", 422)
        start = views.since_time(since) or H.shift(H.now(), hours=-12)
        with store.read() as c:
            items = views.needs_items(c, auth, who, task_view)
            snapshot = views.fleet_snapshot_cached(c, auth, who, task_view)
            rows, notes, reports = batch.candidates(c, who.actor, items)
            said = []
            for m in c.execute("SELECT * FROM messages WHERE to_actor=? AND "
                               "from_actor LIKE 'bot:%' AND kind IN ('say','ask','answer') AND created>? "
                               "AND deleted_at IS NULL ORDER BY created DESC LIMIT 6", (who.actor, start)).fetchall():
                if not privacy.message_readable(c, privacy.actor(who), m):
                    continue
                said.append({"from": batch._name(c, m["from_actor"]), "kind": m["kind"],
                             "when": m["created"], "text": batch._clip(m["body"] or "", 240)})
            stuck = [t for t in H.stuck_tasks(c, hidden=auth.unreadable_bots(c, who))
                     if privacy.task_readable(c, who, H.task(c, t["id"]))]
            open_ = batch.current(c, who.actor)
            return {"now": H.now(), "since": start, "alerts": batch.alerts(snapshot),
                    "lineup": batch.lineup(c, who.actor, notes + rows),
                    "replies_waiting": len(reports), "said": said, "stuck": len(stuck),
                    "open_batch": bool(open_)}

    @app.get("/api/v2/live/stats")
    def live_stats(request: Request, days: int = 7, via: str = ""):
        """How an outside assistant's tool calls perform (backend/mcp.py record_call), per tool:
        calls, median and p90 server time, typical answer size, errors. People and BotOps read all callers;
        a bot granted outcome stats reads only bot calls. Delegated turns are refused."""
        who = request.state.identity
        with store.read() as c:
            granted = outcome_stats.may_read(c, who) and who.role == "bot"
        if (who.via or who.task_actor or
                (who.role not in ("human", "owner") and who.actor != H.bot_actor(H.FLEET_MAINTAINER) and not granted)):
            raise Problem("forbidden", "Tool-call stats are for people, BotOps and a bot the owner granted outcome stats", 403)
        if granted and via:
            raise Problem("forbidden", "A bot granted outcome stats reads the totals, not one assistant's calls", 403)
        since = H.shift(H.now(), days=-max(1, min(days, 90)))
        tools = {}
        with store.read() as c:
            for row in c.execute("SELECT actor, detail_json FROM events WHERE action='mcp.call' AND ts>?", (since,)):
                if granted and not (row["actor"] or "").startswith("bot:"):
                    continue
                d = json.loads(row["detail_json"] or "{}")
                if via and d.get("via") != via:
                    continue
                name = d.get("tool") or "?"
                # Unknown names are caller text; only registered tool names leave the event for a granted bot.
                if granted and name not in outcome_stats.TOOL_NAMES:
                    name = "other"
                t = tools.setdefault(name, {"ms": [], "bytes": [], "errors": 0})
                t["ms"].append(int(d.get("ms") or 0)); t["bytes"].append(int(d.get("bytes") or 0))
                t["errors"] += bool(d.get("error"))
        def pct(values, p):
            values = sorted(values)
            return values[min(len(values) - 1, int(len(values) * p))] if values else 0
        rows = [{"tool": name, "calls": len(t["ms"]), "median_ms": pct(t["ms"], .5), "p90_ms": pct(t["ms"], .9),
                 "median_bytes": pct(t["bytes"], .5), "max_bytes": max(t["bytes"] or [0]), "errors": t["errors"]}
                for name, t in tools.items()]
        rows.sort(key=lambda r: -r["calls"])
        return {"since": since, "via": via or None, "tools": rows}

    @app.get("/api/v2/batch")
    def batch_current(request: Request):
        who = request.state.identity
        views.human_only(who)
        with store.read() as c:
            row = batch.current(c, who.actor)
            privacy.require_batch(c, who, row)
            return {"batch": batch.view(c, row, resumed=True) if row else None}

    @app.post("/api/v2/batch/{bid}/next")
    def batch_next(request: Request, bid: str, body: M.Empty):
        who = request.state.identity
        views.human_only(who)
        return mutate(request, body, lambda c: batch.next_item(c, who.actor, bid))

    @app.post("/api/v2/batch/{bid}/respond")
    def batch_respond(request: Request, bid: str, body: M.BatchRespond):
        who = request.state.identity
        views.human_only(who)
        if body.until and (not H.parse_ts(body.until) or H.parse_ts(body.until).tzinfo is None):
            raise Problem("date", "until must be an ISO-8601 date/time with a timezone", 422)
        return mutate(request, body, lambda c: batch.respond(
            c, who.actor, bid, body.kind, body.text, n=body.item, decision=body.decision,
            heard=body.heard, until=body.until))

    @app.post("/api/v2/batch/{bid}/commit")
    def batch_commit(request: Request, bid: str, body: M.Empty):
        who = request.state.identity
        views.human_only(who)
        def work(c):
            out = batch.commit(c, auth, who, bid, batch_send)
            rows, notes, _ = batch.candidates(c, who.actor, views.needs_items(c, auth, who, task_view))
            line = batch.lineup(c, who.actor, notes + rows)
            return {**out, "lineup": line, "up_next": batch.up_next(line, out["scope"])}
        return mutate(request, body, work)

    @app.post("/api/v2/batch/{bid}/abandon")
    def batch_abandon(request: Request, bid: str, body: M.Empty):
        who = request.state.identity
        views.human_only(who)
        return mutate(request, body, lambda c: batch.abandon(c, who.actor, bid))

    def conversation_target(c, who, cid):
        conv = auth.conversation(c, who, cid, write=True)
        target = next((p for p in conv["participants"] if p != who.actor and p.startswith("bot:")), None)
        target = target or next((p for p in conv["participants"] if p != who.actor), None)
        if not target:
            raise Problem("recipient", "This conversation has no other recipient", 422)
        return target

    @app.post("/api/v2/conversations/{cid}/messages")
    def reply(request: Request, cid: str, body: M.ChatCreate):
        who = request.state.identity
        with store.read() as c:
            try:
                target = conversation_target(c, who, cid)
            except Problem:
                target = None               # the write refuses it, with its own reason
        message = M.MessageCreate(to=target or "-", text=body.text, conversation_id=cid, refs=body.refs,
                                  command=body.command, attachments=body.attachments, delivery=body.delivery,
                                  defer_for=body.defer_for)
        # The same checks as `/messages`: a bot's message to this conversation is one message, however it is posted.
        rc, ingest = message_checks(who, message) if target else (None, None)

        def work(c):
            sent = message.model_copy(update={"to": conversation_target(c, who, cid)})
            check = rc
            if sent.to != target:
                # The recipient changed after the check: delivered unchecked (no engine call in the write), recorded.
                author = org_rule_check.author_of(who, settings.assistant_bot)
                check = org_rule_check.changed(c, rc, author, "message", rule_message_audience(c, who, sent))
            if ingest and sent.to != target:
                ask_ingest.skipped(c, ingest, "target")      # judged for the first recipient, delivered to another
            out = message_write(c, who, sent, check, ingest if sent.to == target else None)
            if out.get("held"):
                return out
            extra = {k: out.pop(k) for k in ("rule_warning",) if k in out}
            return {"message": out, **extra}
        return ask_ingest.judged(store, ingest, lambda: mutate(request, body, work))

    def answer_write(c, who, mid, text, unknown=False, warn="", destination=False):
        """One answer, as `/messages/{mid}/answer` writes it and Send anyway replays it. The pick is resolved on the
        answer's own words; `warn` (an org rule's Warn) goes on the message its reader gets. `destination`: Send
        anyway's check, as the run the answer came from, that the question's readers may read what it carries."""
        msg = H.message(c, mid)
        if not msg:
            raise Problem("not_found", "Message not found", 404)
        conversation = auth.conversation(c, who, msg["conversation_id"], write=True)
        lifecycle.refuse_send(c, who, msg["from_actor"], conversation, what="an answer")
        if destination:
            privacy.require_destination(c, who, msg["from_actor"], msg["conversation_id"], {}, msg)
        if H.has_pick_options(msg):
            from .task_review import answer_text
            return answer_text(c, auth, who, msg, text, unknown=unknown, warn=warn)
        return H.answer(c, who.actor, mid, warn + "\n\n" + text if warn else text, unknown=unknown, classify_text=text)

    def answer_audience(c, who, mid):
        msg = H.message(c, mid)
        if not msg:
            raise Problem("not_found", "Message not found", 404)
        conv = H.conversation(c, msg["conversation_id"]) or {}
        return sorted({msg["from_actor"], *(conv.get("participants") or [])} - {None, ""})

    @app.post("/api/v2/messages/{mid}/answer")
    def answer(request: Request, mid: str, body: M.Answer):
        who = request.state.identity
        return checked_write(request, body, who, "answer", body.text, lambda c: answer_audience(c, who, mid),
                             lambda c, gate: answer_write(c, who, mid, body.text, body.unknown,
                                                          org_rule_check.warn_line(gate)),
                             {"message": mid, "body": body.model_dump(mode="json", by_alias=True)})

    @org_rule_check.replayer("answer")
    def rule_replay_answer(c, bot, hold, replay, ingest, gate=None):
        body = M.Answer.model_validate(replay["body"])
        return answer_write(c, bot, replay["message"], body.text, body.unknown, org_rule_check.warn_line(gate),
                            destination=True)

    @app.post("/api/v2/needs-you/clarify")
    def clarify_ask(request: Request, body: M.ClarifyAsk):
        """One tap on Clarify: ask the bot behind a stale question whether it still matters, in clearer words."""
        who = request.state.identity
        views.human_only(who)
        def work(c):
            from . import clarify_asks
            asked = H.message(c, body.ask)
            if not asked or asked["kind"] != "ask":
                raise Problem("not_found", "Question not found", 404)
            auth.conversation(c, who, asked["conversation_id"], write=True)
            privacy.require_message(c, who, asked)
            return clarify_asks.clarify(c, who.actor, body.ask)
        return mutate(request, body, work)

    @app.post("/api/v2/needs-you/recommended")
    def take_recommendation(request: Request, body: M.TakeRecommendation):
        """One tap on "Do what you recommend": answers the bot's ask with its recommendation and closes the card."""
        who = request.state.identity
        views.human_only(who)
        def work(c):
            asked = H.message(c, body.ask)
            if not asked or asked["kind"] != "ask":
                raise Problem("not_found", "Question not found", 404)
            auth.conversation(c, who, asked["conversation_id"], write=True)
            privacy.require_message(c, who, asked)
            return views.take_recommendation(c, auth, who, asked, body.version)
        return mutate(request, body, work)

    def task_filters(c, owner=None, requester=None, status=None, lane=None, label=None, type=None, step=None,
                     number=None, updated_since=None, member=None, role=None, snoozed=None, by_id=False, exact=False):
        """The filters `GET /api/v2/tasks` takes, checked and resolved: (owner, requester, H.tasks keywords). A bulk
        change (`POST /api/v2/task-bulk`) selects with the same ones. `exact` refuses an owner or requester that names
        nobody, where a list would quietly drop the filter: a bulk change must never widen to every task."""
        found_owner = H.resolve_actor(c, owner) if owner else None
        found_requester = H.resolve_actor(c, requester) if requester else None
        if exact and (owner and not found_owner or requester and not found_requester):
            raise Problem("owner" if owner and not found_owner else "requester",
                          "owner and requester are a person or a bot on the roster", 422)
        if member:
            member = H.resolve_actor(c, member)
            if not member:
                raise Problem("member", "member is a person or a bot on the roster", 422)
        if role and not TRo.role_name(role):
            raise Problem("role", "role is a short name in lower-case letters, digits, - or _", 422)
        if role and not member:
            raise Problem("role", "role filters member: pass both", 422)
        if lane and lane not in H.TASK_LANES:
            raise Problem("kind", "lane is company or product", 422)
        typ = H.type_get(c, type) if type else None
        if type and not typ:
            raise Problem("type", "No task type " + type, 422)
        step_ids = None
        if step:
            # By id or name: in the type when one is given, else every type's step of that name.
            steps = typ["steps"] if typ else H._rows(c.execute("SELECT id,name FROM task_steps"))
            step_ids = [s["id"] for s in steps if step in (s["id"], s["name"])]
            if not step_ids:
                raise Problem("step", "No step " + step + (" in " + typ["name"] if typ else ""), 422)
        if snoozed not in (None, "hide", "only", "all"):
            raise Problem("snoozed", "snoozed is hide, only or all", 422)
        if number is not None and not 1 <= number <= 999_999_999:
            raise Problem("number", "number is a task number, from 1", 422)
        since = H.parse_ts(updated_since) if updated_since else None
        if updated_since and (not since or since.tzinfo is None):
            raise Problem("date", "updated_since must be an ISO-8601 date/time with a timezone", 422)
        return found_owner, found_requester, dict(
            status=status.split(",") if status and status != "all" else None, lane=lane, label=label,
            type_id=typ["id"] if typ else None, step_ids=step_ids, number=number,
            updated_since=views.since_time(updated_since) if since else None,
            member=member, role=TRo.role_name(role) if role else None,
            snoozed=None if snoozed == "all" or (snoozed is None and by_id) else snoozed or "hide")

    def task_idents(c, who, ids, visible_sql):
        """`ids` (1 to 100 task ids or numbers, comma-separated) as typed, each with the task it names for this reader
        or None (unknown, ambiguous or not readable)."""
        typed = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
        if not typed or len(typed) > 100:
            raise Problem("ids", "ids is 1 to 100 task ids or numbers, comma-separated", 422)
        found = {}
        for ident in typed:
            try:
                found[ident] = auth.resolve_task(c, who, ident, visible=visible_sql)
            except Problem:
                found[ident] = None
        return typed, found

    @app.get("/api/v2/tasks")
    def tasks(request: Request, response: Response, owner: str | None = None, requester: str | None = None,
              status: str | None = None, lane: str | None = None, label: str | None = None, limit: int = 500,
              offset: int = 0, sort: str = "queue", type: str | None = None, step: str | None = None,
              number: int | None = None, updated_since: str | None = None, brief: bool = False,
              member: str | None = None, role: str | None = None, changed_after: str | None = None,
              ids: str | None = None, snoozed: str | None = None):
        from . import task_reads as reads
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            if limit < 1 or limit > 500:
                raise Problem("limit", "limit is between 1 and 500", 422)
            if offset < 0:
                raise Problem("offset", "offset is zero or greater", 422)
            if sort not in ("queue", "finished", "step"):
                raise Problem("sort", "sort is queue, finished or step", 422)
            owner, requester, filters = task_filters(
                c, owner=owner, requester=requester, status=status, lane=lane, label=label, type=type, step=step,
                number=number, updated_since=updated_since, member=member, role=role, snoozed=snoozed,
                # A snoozed task is off every list until its date unless asked for by id, number or filter.
                by_id=ids is not None or number is not None)
            # One read: the visibility, the rows, the cursor and the ETag agree (backend/task_reads.py).
            visible_sql = auth.task_sql(c, who)
            # Several tasks by id in one read (an agent's "show these"), instead of one GET each.
            typed = None
            if ids is not None:
                typed, found = task_idents(c, who, ids, visible_sql)
                if changed_after is not None:
                    raise Problem("ids", "ids and changed_after are separate reads", 422)
            filters.update(order=sort, visible_sql=visible_sql)
            if typed is not None:
                filters["ids"] = [tid for tid in found.values() if tid]
            def brief_rows(rows):
                # A board or a bot's task list polling many tasks needs neither their text nor their criteria.
                if brief:
                    for row in rows:
                        for field in ("body", "acceptance_criteria", "acceptance_json", "public_title", "public_summary",
                                      "requester_count", "external_requesters", "internal_requesters", "important_requesters", "organization", "size"):
                            row.pop(field, None)
                        for field in ("verdict", "verdict_head"):   # only a review or QA subtask has one
                            if row.get(field) is None:
                                row.pop(field, None)
                        if isinstance(row.get("step"), dict):
                            row["step"].pop("public_name", None)
                        # A snooze's note is left out of a list row; the task's own view keeps it whole.
                        row.pop("snoozed_note", None)
            if changed_after is not None:
                found = reads.changed(c, changed_after, visible_sql)
                ids = reads.around(c, found[0]) if found else []
                if found is None or len(ids) > reads.CAP:
                    return {"tasks": [], "gone": [], "reset": True, "cursor": reads.cursor(c, visible_sql),
                            "next_offset": None}
                was, cursor = found
                rows = visible_tasks(c, who, owner, requester, limit=len(ids), ids=ids, **filters)[0] if ids else []
                gone = reads.gone(c, visible_sql, was, {row["id"] for row in rows})
                result = {"tasks": rows, "gone": gone, "cursor": cursor, "next_offset": None}
            else:
                # Unchanged anywhere: a 304 without reading a row. Changed elsewhere but not in these rows: a 304
                # after reading them, so an idle list on a busy team still sends no body.
                reader = (reads.access(visible_sql), who.actor, who.task_actor, sorted(request.query_params.multi_items()))
                whole, held = reads.digest("tasks", reads.version(c), reads.names(c), *reader), reads.held(request)
                for was_whole, was_rows in held:
                    if was_whole == whole:
                        return reads.not_modified(reads.list_tag(whole, was_rows))
                rows, next_offset = visible_tasks(c, who, owner, requester, limit=limit, offset=offset, **filters)
                brief_rows(rows)
                part = reads.digest(rows, next_offset, *reader)
                tag = reads.list_tag(whole, part)
                if any(was_rows == part for _, was_rows in held):
                    return reads.not_modified(tag)
                response.headers["ETag"] = tag
                result = {"tasks": rows, "next_offset": next_offset, "cursor": reads.cursor(c, visible_sql)}
                if typed is not None:
                    # What was asked for and is not here: unknown, ambiguous, outside the filters or not readable.
                    returned = {row["id"] for row in rows}
                    result["missing"] = [ident for ident in typed if found[ident] not in returned]
            brief_rows(rows)
            return result

    # ------------------------------------------------------------------ quiet notes
    def note_view(row, c):
        waiting = H.note_waiting(c, row)
        return {"id": row["id"], "from": row["from_actor"], "to": row["to_actor"], "text": row["body"],
                "created": row["created"], "waiting": waiting,
                "carried": bool(row.get("carried_by")) and not waiting and not row.get("cancelled_at"),
                "carried_by": row.get("carried_by"), "cancelled_at": row.get("cancelled_at")}

    def note_visible(c, who, row):
        # A bot sees the notes it left and the ones left for it; a person, the ones they left and
        # every note between bots whose activity they may read.
        if who.role == "bot":
            return who.actor in (row["from_actor"], row["to_actor"])
        if who.actor == row["from_actor"]:
            return True
        bots = [H.actor_id(a) for a in (row["from_actor"], row["to_actor"]) if str(a).startswith("bot:")]
        return all(level["read"] for level in auth.bot_accesses(c, who, bots).values())

    def note_write(c, who, body, rc=None):
        to = auth.target(c, who, body.to, need="write")
        auth.require_bot_contact(c, who, to, kind="note")
        lifecycle.refuse_send(c, who, to, what="a note")
        rule = org_rule_check.apply(c, rc, body.text, {"body": body.model_dump(mode="json", by_alias=True)})
        if rule and rule.get("held"):
            return org_rule_check.answer(rule)
        # A note is a deferred message to the bot's next run of any kind; `defer_for` wakes it by then.
        made = H.note_create(c, who.actor, to, org_rule_check.warned_text(rule, body.text))
        due = delivery.defer(c, made["to_actor"], "note", made["id"], defer_for=body.defer_for)
        return {"note": {**note_view(made, c), "sent_as": delivery.sent_as("defer", due)}, **org_rule_check.answer(rule)}

    @app.post("/api/v2/notes")
    def create_note(request: Request, body: M.NoteCreate):
        who = request.state.identity
        rc = rule_check(who, "note", body.text, lambda c: [rule_target(c, who, body.to)])
        return mutate(request, body, lambda c: note_write(c, who, body, rc))

    @org_rule_check.replayer("note")
    def rule_replay_note(c, bot, hold, replay, ingest, gate=None):
        body = M.NoteCreate.model_validate(replay["body"])
        # The note's reader must read what the run it came from carries, as for any write to them.
        privacy.require_destination(c, bot, auth.target(c, bot, body.to, need="write"), None, {})
        return note_write(c, bot, body, org_rule_check.decided(gate))

    @app.get("/api/v2/notes")
    def notes(request: Request, to: str | None = None, sender: str | None = None, since: str | None = None,
              waiting: bool = False, limit: int = 200):
        who = request.state.identity
        auth.domain(who)
        if limit < 1 or limit > 500:
            raise Problem("limit", "limit is between 1 and 500", 422)
        with store.read() as c:
            where, args = [], []
            if to:
                where.append("n.to_actor=?"); args.append(H.resolve_actor(c, to) or to)
            if sender:
                where.append("n.from_actor=?"); args.append(H.resolve_actor(c, sender) or sender)
            if since:
                where.append("n.created>=?"); args.append(views.since_time(since))
            if waiting:
                where.append(H.NOTE_WAITING_SQL)
            if who.role == "bot":
                where.append("(n.from_actor=? OR n.to_actor=?)"); args += [who.actor, who.actor]
            elif who.role == "human":
                hidden = ["bot:" + slug for slug in sorted(auth.unreadable_bots(c, who))]
                if hidden:
                    marks = "(" + ",".join("?" * len(hidden)) + ")"
                    where.append(f"(n.from_actor=? OR NOT (n.from_actor IN {marks} OR n.to_actor IN {marks}))")
                    args += [who.actor, *hidden, *hidden]
            sql = ("SELECT n.* FROM notes n" + (" WHERE " + " AND ".join(where) if where else "")
                   + " ORDER BY n.created DESC, n.id DESC LIMIT ?")
            rows = [dict(r) for r in c.execute(sql, (*args, limit))]
            return {"notes": [note_view(r, c) for r in rows if note_visible(c, who, r)]}

    @app.post("/api/v2/notes/{nid}/cancel")
    def cancel_note(request: Request, nid: str, body: M.Empty):
        def work(c):
            who = request.state.identity
            row = H.note(c, nid)
            if not row or not note_visible(c, who, row):
                raise Problem("not_found", "Note not found", 404)
            return {"note": note_view(H.note_cancel(c, who.actor, nid, human=who.role in ("human", "owner")), c)}
        return mutate(request, body, work)

    @app.get("/api/v2/tasks/labels")
    def task_labels(request: Request, response: Response):
        from . import task_reads as reads
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            visible_sql = auth.task_sql(c, who)
            tag = reads.etag("labels", reads.version(c), reads.access(visible_sql), reads.names(c), who.actor)
            if reads.fresh(request, tag):
                return reads.not_modified(tag)
            keys = H.labels_in_use(c, visible_sql)
            response.headers["ETag"] = tag
            return {"labels": keys, "tags": [H.tag_by_key(c, key) for key in keys]}

    @app.get("/api/v2/tasks/stuck")
    def tasks_stuck(request: Request, hours: int = H.STUCK_HOURS):
        """Every bot's stuck work, for the fleet maintainer's sweep and for people."""
        who = request.state.identity
        if who.role not in ("human", "owner") and who.actor != H.bot_actor(H.FLEET_MAINTAINER):
            raise Problem("forbidden", "The stuck-task sweep is BotOps's and people's", 403)
        with store.read() as c:
            return {"tasks": [t for t in H.stuck_tasks(c, hours=max(1, hours), hidden=auth.unreadable_bots(c, who))
                              if privacy.task_readable(c, who, H.task(c, t["id"]))]}

    @app.get("/api/v2/tasks/flow")
    def tasks_flow(request: Request, days: int = 30, tz: int = 0, owner: str | None = None,
                   requester: str | None = None, owner_kind: str | None = None, label: str | None = None,
                   recurring: bool | None = None, type: str | None = None):
        """Status changes over time for the Tasks page's stats: counts by day, time in each stage."""
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "Task stats are for humans", 403)
        if days < 1 or days > 365:
            raise Problem("days", "days is between 1 and 365", 422)
        if owner_kind not in (None, "", "bot", "human"):
            raise Problem("owner_kind", "owner_kind is bot or human", 422)
        with store.read() as c:
            owner = H.resolve_actor(c, owner) if owner else None
            requester = H.resolve_actor(c, requester) if requester else None
            typ = H.type_get(c, type) if type else None
            if type and not typ:
                raise Problem("type", "No task type " + type, 422)
            tz = max(-840, min(840, tz))       # minutes east of UTC; days are the viewer's days
            today = H.shift(H.now(), minutes=tz)[:10] + "T00:00:00.000000Z"
            since = H.shift(today, days=1 - days, minutes=-tz)
            return {"since": since, **H.task_flow(c, since, tz_minutes=tz, visible=auth.task_sql(c, who),
                                                 owner=owner, requester=requester, owner_kind=owner_kind or None,
                                                 label=label, recurring=recurring, type_id=typ["id"] if typ else None)}

    @app.get("/api/v2/tasks/{tid}")
    def task(request: Request, tid: str):
        with store.read() as c:
            tid = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, tid)
        from .github import refresh_task_prs
        refresh_task_prs(app.state.github_app, tid)
        with store.read() as c:
            row = auth.task(c, request.state.identity, tid)
            # A bot task now lives in the bot's chat room. Only this task's messages
            # come back here; the rest of that room stays on Chat.
            who = request.state.identity
            children = [{"id": t["id"], "title": t["title"], "status": t["status"], "owner": t["owner"]}
                        for t in H._rows(c.execute("SELECT id,title,status,owner FROM tasks WHERE id IN (" + TR.CHILD_IDS + ") AND ("
                                                   + auth.task_sql(c, who) + ") ORDER BY (rank IS NULL), rank, created",
                                                   (tid,)))]
            from .task_review import comment_rights
            from .views import task_wait
            try:
                comment_rights(c, auth, who, tid)
                can_comment = True
            except Problem:
                can_comment = False
            return {"task": task_view(row, c, visible_sql=auth.task_sql(c, who), who=who),
                    "events": privacy.content_readable_rows(c, privacy.actor(who), H.task_history(c, tid)),
                    "can_comment": can_comment,
                    "children": children,
                    # The task page reads as its session's chat: each bot reply carries the run behind it (`run`,
                    # the steps one click away) and `execution` is the run it follows while it works: the owner's,
                    # or the one for a person's message, whichever bot that went to. `comment_to` is who the reader's
                    # next comment reaches, so the composer says before sending when no bot will hear it.
                    "comments": visual_view(c, who, turn_work.annotate(c, auth, who,
                                                                          H.task_comments(c, tid, actor=privacy.actor(who)))),
                    "execution": task_execution(c, who, row),
                    "comment_to": comment_to(c, who, row) if can_comment else None,
                    "comment_delivery": delivery_choices(c, who, row) if can_comment else None,
                    "mover": mover(c, request.state.identity),
                    "waiting_for": task_wait(c, tid, settings),
                    "runs": task_runs(c, who, tid),
                    # The model a bot's check subtask runs on (backend/check_models.py), or null.
                    "check_model": H.check_model(c, tid),
                    # The task's own model (backend/task_run_models.py), or null when its runs use the bot's task model.
                    "run_model": task_run_models.of(c, tid),
                    # Whether this reader may set that model: the answer POST /v2/tasks/{id}/model gives, so the task
                    # page offers its form to whoever the server would accept. A person's task has no model to set.
                    "can_set_run_model": H.is_bot(row["owner"]) and task_run_models.may_set(
                        c, auth, who, H.actor_id(row["owner"])),
                    **privacy.page(c, who, row["conversation_id"], task_id=tid)}

    def task_execution(c, who, row):
        """The run the task's page follows in its own room, as a conversation snapshot shows it, or None: the owning
        bot's newest run, or a newer one for a message a person sent there, whichever bot it was routed to (the bot
        that asked a person's task, say). Another bot's run for a bot's comment is not it. A task still in a chat
        room (filed into a chat before task rooms) shows none: that room's runs are the chat's, and reading the task
        does not let one read the chat."""
        cid, owner = row["conversation_id"], str(row["owner"] or "")
        room = H.conversation(c, cid) if cid else None
        if not room or room.get("task_id") != row["id"]:
            return None
        from .views import conversation_execution
        return conversation_execution(c, cid, who, settings, bot=owner.removeprefix("bot:") if H.is_bot(owner) else None,
                                      auth=auth, people=True)

    def comment_to(c, who, row):
        """Who a plain comment from this reader reaches (H.task_comment, H.comment_way): `bot`, the bot it goes to,
        or None when no bot will get it; `wakes`, whether it wakes that bot (queue) rather than waiting for its next
        turn on the task (defer); `mention`, the @names that would queue it anyway for this reader, each checked
        against the same rule. None for a bot reader."""
        if who.role == "bot":
            return None
        target = H.comment_target(c, who.actor, row)
        if not H.is_bot(target):
            return {"bot": None, "wakes": False, "mention": []}
        moves = mover(c, who)
        wakes = H.comment_wakes(c, who.actor, row, "", mover=moves)
        mention = [] if wakes else [n for n in H.bot_handles(c, row)
                                    if H.comment_wakes(c, who.actor, row, "@" + n, mover=moves)]
        return {"bot": H.actor_id(target), "wakes": wakes, "mention": mention}

    def delivery_choices(c, who, row):
        """How this reader's comment can reach the task's bot (docs/tasks.md, backend/delivery.py): `default`, the way
        a comment with none named goes (queue or defer; null when no bot is on the task) and `default_for`, a default
        defer's deadline (24h, never); `steer` and `queue`, whether the reader may wake the bot (chat_goals.may_direct;
        anyone may defer); `steers`, whether that bot's harness writes a steer into its running turn; `goal`, whether
        the reader may set the task session's goal (chat_goals.own_room and chat_goals.may_set). None for a bot
        reader."""
        if who.role == "bot":
            return None
        target = H.comment_target(c, who.actor, row)
        if not H.is_bot(target):
            return {"default": None, "default_for": None, "steer": False, "queue": False, "steers": False,
                    "goal": False}
        way, defer_for = H.comment_way(c, who.actor, row, "", mover=mover(c, who))
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (H.actor_id(target),)).fetchone()
        config = json.loads(config[0] or "{}") if config else {}
        from .harnesses import steers
        from .chat_goals import may_direct, may_set, own_room
        directs = may_direct(c, who, row)
        return {"default": way, "default_for": defer_for if way == "defer" else None, "steer": directs,
                "queue": directs, "steers": steers(config, config.get("runtime")),
                "goal": own_room(c, row) and may_set(c, auth, who, row["conversation_id"])}

    def task_runs(c, who, tid, limit=200):
        """The runs that worked this task, each with the model that actually ran it (`ran_on`). A review that
        weighs who built what reads this, never the bot's configured model: a fallback turn ran elsewhere."""
        from .usage_records import ran_on
        actor = privacy.actor(who)
        rows = H._rows(c.execute("SELECT id,bot,started,finished,exit FROM turns WHERE task_id=? "
                                 "ORDER BY started DESC LIMIT ?", (tid, limit)))
        privacy.prime_attempts(c, [r["id"] for r in rows])
        rows = [r for r in rows if privacy.attempt_readable(c, actor, r["id"])]
        made = ran_on(c, [r["id"] for r in rows])
        return [{"run": r["id"], "bot": r["bot"], "started": r["started"], "finished": r["finished"],
                 "outcome": r["exit"], "ran_on": made[r["id"]]} for r in rows]

    def check_choice(c, who, body, owner, parent_id):
        """The check model a bot's subtask asks for (backend/check_models.py), or Problem. Only a bot files one, as
        its own subtask under its own task, so the verdict comes back to the session that asked."""
        if who.role != "bot" or who.via or owner != who.actor:
            raise Problem("check_model", "Only a bot runs a check model, on a subtask it owns", 422)
        parent = H.task(c, parent_id) if parent_id else None
        if not parent or parent["owner"] != who.actor:
            raise Problem("check_model", "A check model runs a subtask: file it under a task you own", 422)
        try:
            return check_models.choose(c, settings, H.actor_id(owner), body.check_model,
                                       body.check_role or "check", check_models.authors_of(c, settings, parent_id))
        except check_models.CheckModelError as exc:
            raise Problem(exc.code, str(exc), 409 if exc.code == "runner_not_ready" else 422)

    def task_create(c, who, body, lint=True, asked=None, review_prs=None, ingest=None, same=None, rule=None):
        """`asked`: the person a bot's question is put to (backend/media.py task_question). When the task is
        private they are its requester, so its two parties are the bot and them."""
        source = who
        actual_bot = source.task_actor or (source.actor if source.role == "bot" else "")
        # A new task has no turn to steer. Defer is a deferred task (`next_run`, the older word for it); queue, as
        # omitted, wakes its bot.
        if body.delivery == "steer":
            raise Problem("delivery", "A new task has no running turn to steer: send it with queue or defer", 422)
        if wrong := delivery.check(body.delivery, body.defer_for):
            raise Problem("delivery", wrong, 422)
        if body.next_run and body.delivery == "queue":
            raise Problem("delivery", "next_run is delivery defer: send one or the other", 422)
        if body.delivery == "defer" and not body.next_run:
            body = body.model_copy(update={"next_run": True})
        if body.from_private:
            pass                        # company-visible, its words checked (task_privacy.from_private)
        elif privacy.private_execution(c, who):
            if body.private is False:
                raise Problem("privacy", privacy.COMPANY_REFUSAL, 403)
            body = body.model_copy(update={"private": True})
        request_id = body.request_id
        handoff = who.role == "bot" and who.actor != "bot:" + BOTOPS and not who.via
        if request_id and handoff:
            request_id = None          # a bot cites the person it is handing on; it never acts as them itself
        elif request_id and who.via == "assistant":
            pass                       # the person's own words to their Assistant, checked once the owner is known
        elif request_id:
            who = delegated_identity(c, who, request_id, new_task=True)
        parents = [r.task for r in body.relations if r.kind == "parent"]
        if len(parents) > 1:
            raise Problem("relations", "A task has one parent", 422)
        parent_id = auth.resolve_task(c, who, parents[0]) if parents else None
        others = [(auth.resolve_task(c, who, r.task), r.kind) for r in body.relations if r.kind != "parent"]
        from .shared_bots import route
        original = auth.target(c, who, body.owner, need="write")
        owner = auth.target(c, who, route(c, who.actor, original), need="write")
        task_caps.check(c, who.actor, owner)      # the first hub check on a send (CONTRACT §1): nothing is written
        asked_botops = None
        if request_id and who.via == "assistant":
            # The Assistant handing a helper (BotOps, the Strategy Bot, the Librarian; assistant.HELPERS) what the
            # person asked for in their own Assistant chat, citing that message (`--request-id turn`), only while the
            # owner lets the Assistant act without asking. BotOps then acts with the person's rights
            # (delegated_identity, `assistant`); the Strategy Bot and the Librarian work it with their own rights, so
            # what only the owner may change (strategy, goals) still reaches the owner as a proposal or a card.
            if H.actor_id(owner) not in assistant_mod.HELPERS or not H.is_bot(owner):
                raise Problem("request_id", "Only a task for BotOps, the " + strategy_bot.name(c)
                              + " or the Librarian carries the person's request", 422)
            asked_botops = assistant_instruction(c, who, request_id) if assistant_direct() else None
            request_id = None
        # The same checks the dry-run makes (task_privacy.require_create); a subtask for a person under a private
        # task with no person on it, filed by the bot that owns it, makes them its one guest.
        guest = privacy.require_create(c, source, body.private, owner, H.task(c, parent_id) if parent_id else None,
                                       actor=who.actor, title=body.title, body=body.body,
                                       from_private_work=body.from_private, texts=privacy.create_texts(body),
                                       related=[other for other, _ in others])
        # The private tasks a company-visible task from private work comes from: their history says so.
        kept = privacy.from_private(c, source, owner, H.task(c, parent_id) if parent_id else None) \
            if body.from_private else []
        # A private run's subtask for a bot that cannot read its parent: sealed, the history says so (Dev 632).
        sealed = (not guest and not kept and privacy.private_execution(c, source)
                  and privacy.sealed_for(c, source, owner, H.task(c, parent_id) if parent_id else None) is not None)
        auth.require_bot_contact(c, who, owner, task_id=parent_id, kind="task")
        # A subtask is the shape that parks the filer as `waiting` and makes it care when the
        # other one finishes. A bot set to `tasks` has opted out of that on both sides: it does
        # not wait, and its own completions are quiet, so parking on one would wait for ever.
        # Every other bot keeps the handoff flow in `policies/handoffs.md`.
        if (parent_id and who.role == "bot" and owner != who.actor
                and str(owner).startswith("bot:")
                and (H.tasks_only(c, who.actor) or H.tasks_only(c, owner))):
            raise Problem("subtask", "A bot set to tasks-only files work flat: give it its own "
                                     "task without a parent, and do not wait for it.", 422)
        if parent_id:
            auth.task(c, who, parent_id)
        check, unchecked = None, None
        if body.check_model is not None or body.check_role is not None:
            check = check_choice(c, who, body, owner, parent_id)
        else:
            check, unchecked = check_models.review_default(c, settings, owner, parent_id)
        # The task's own model, checked before anything is written (the same rights as the bot's model, a bot never).
        run_model = None
        if body.model is not None or body.effort or body.harness:
            if not H.is_bot(owner):
                raise Problem("owner", "Only a task a bot runs has a model to choose", 422)
            if body.model is None:
                raise Problem("model", "Choose a model to pick an effort or harness for it", 422)
            run_model = settings_admin.task_run_choice(c, who, H.actor_id(owner), "set the model a new task runs on",
                                                       body.model, body.effort, body.harness)
        for other, _ in others:
            auth.task(c, who, other)
        parent = H.task(c, parent_id) if parent_id else None
        # Delegation retains the bot's sensitive default and cannot use a person's
        # explicit-public choice to override either creator or assignee defaults.
        if actual_bot and body.private is False:
            body = body.model_copy(update={"private": None})
        if actual_bot and H.private_tasks_default(c, actual_bot):
            body = body.model_copy(update={"private": True})
        private = not kept and bool((body.private if body.private is not None else
                                     H.private_tasks_default(c, who.actor) or H.private_tasks_default(c, owner))
                                    or parent and H.task_private(c, parent))
        if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        from . import blocker_asks
        blocker_key, joins, asked_of = None, None, owner
        if body.blocker:
            try:
                blocker_key = blocker_asks.key(body.blocker.model_dump())
            except ValueError as exc:
                raise Problem("blocker", str(exc), 422)
            if who.role == "bot" and not private and owner != who.actor:
                # The same blocker already asked about: this task waits on that ask instead of reaching its owner.
                joins = blocker_asks.open_ask(c, blocker_key, owner, parent_id)
            if joins:
                owner, check, unchecked = who.actor, None, None
        # The org rule check (backend/org_rule_check.py), judged before the write: held, nothing is filed.
        rule_gate = org_rule_check.apply(c, rule, rule_create_text(body),
                                         {"body": body.model_dump(mode="json", by_alias=True)}) if rule else None
        if rule_gate and rule_gate.get("held"):
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.answer(rule_gate)
        public_warning = ""
        if rule_gate and rule_gate.get("warn"):
            approved = exact_approval(c, who.actor)
            body = body.model_copy(update=rule_warned_fields(rule_gate, body, approved=approved))
            # Whether the task is public is the store's to say (its type's default included): it adds the line there.
            public_warning = "" if approved else org_rule_check.warn_line(rule_gate)
        # Ask ingest (backend/ask_ingest.py): a bot's task for a person, judged before the write. Sent back or FYI
        # with the filter on, it stays the bot's own task; the reply says why.
        gate = None
        if ingest and not joins and owner == ingest["person"]:
            gate = ask_ingest.apply(c, ingest)
            if gate["acts"]:
                owner, check, unchecked = who.actor, None, None
        elif ingest:
            ask_ingest.skipped(c, ingest, "joined" if joins else "not_applied")
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        requester_actor = None
        if asked and private:
            if who.role != "bot" or owner != who.actor:
                raise Problem("identity", "Only a bot's own question names the person asked", 403)
            if not privacy.question_party(c, who, asked, parent):
                raise Problem("privacy", f"{H.actor_id(asked)} cannot read this run's private work or the parent task, "
                              "so a private question cannot go to them", 403)
            requester_actor = asked
        elif (who.role == "bot" and owner == who.actor and not parent_id and who.attempt_id
                and private):
            origin = c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                               "JOIN messages m ON m.id=j.message_id WHERE a.id=? AND a.bot=?",
                               (who.attempt_id, H.actor_id(who.actor))).fetchone()
            if origin and privacy.message_readable(c, privacy.actor(who), origin):
                if H.is_human(origin["from_actor"]):
                    requester_actor = origin["from_actor"]
                else:
                    message = H.message(c, origin["id"])
                    source_task = H.task(c, H.message_task_id(message, H.conversation(c, message["conversation_id"])))
                    if (source_task and H.task_private(c, source_task) and source_task["owner"] == who.actor
                            and H.is_human(source_task["requester"])):
                        requester_actor = source_task["requester"]
            if requester_actor and not privacy.readable(c, requester_actor, privacy.attempt_tasks(c, who.attempt_id)):
                raise Problem("privacy", "The verified requester cannot receive this private execution's other context", 403)
        if private and actual_bot and actual_bot not in (requester_actor or who.actor, owner):
            raise Problem("privacy", "The acting bot must be a current participant of the private task", 403)
        if guest:
            H.task_reach(c, who.actor, parent, owner)
        row = H.task_create(c, who.actor, body.title, body.body, owner, body.due, parent_id,
                            requester_actor=requester_actor, visibility=body.visibility,
                            public_title=body.public_title, public_summary=body.public_summary,
                            public_warning=public_warning,
                            private=private, conversation_id=rooms.task_conversation_id(c, auth, owner, who.actor),
                            lane=body.lane, labels=body.labels, top=body.top, lint=lint,
                            goal_id=body.goal_id, next_run=body.next_run, type=body.type, step=body.step,
                            number=body.number, mover=mover(c, who) or None,
                            own_room=H.actor_id(owner) != settings.assistant_bot, review_prs=review_prs,
                            receipt_owner=Access.load_owner(c, settings)["email"],
                            change_paths=body.change_paths, qa=body.qa,
                            qa_not_checkable_live=body.qa_not_checkable_live,
                            quiet_notice=bool(joins) or bool(gate and gate["acts"]), from_private=bool(kept),
                            same=same)
        c.execute("UPDATE tasks SET acceptance_json=? WHERE id=?", (encode(body.acceptance_criteria), row["id"]))
        due = None
        if body.next_run and H.is_bot(owner):
            # A deferred task waits for its bot's next run, and starts one by its deadline (24h unless named).
            due = delivery.defer(c, owner, "task", row["id"], task_id=row["id"], defer_for=body.defer_for)
        if run_model:
            task_run_models.set_choice(c, who.actor, row["id"], run_model, note="Set when the task was filed")
        task_caps.record(c, row["id"], who.actor, owner)     # a send, counted against the sender's cap for this owner
        if gate:
            ask_ingest.record(c, ingest, gate, row["id"])
        for source_row in kept:
            # The private task's readers see what was filed; the new task's, only that it came from private work.
            H._task_event(c, source_row["id"], who.actor, "filed_from_private", None, row["id"],
                          f"Filed a company-visible task from this private task: {body.title}")
        if kept:
            H._task_event(c, row["id"], who.actor, "from_private", None, who.actor, "Filed from a private task")
        if sealed:
            # The parent's readers see who it went to; the subtask's see who filed it, and nothing of the parent.
            H._task_event(c, parent_id, who.actor, "handed", None, owner,
                          f"Handed to {H.actor_id(owner)} in a private subtask")
            H._task_event(c, row["id"], who.actor, "sealed", None, who.actor,
                          f"Filed by {H.actor_id(who.actor)} from private work it does not share")
        joined = blocker_asks.record(c, who.actor, row, blocker_key, joins, asked_of) if blocker_key else None
        if check:
            # A check is its owner's (check_models.of): one asked for is filed by its owner (check_choice), and a
            # reviewer's default comes from its own setting.
            check_models.record(c, owner, row["id"], check)
        elif body.check_model is None and body.check_role is None and unchecked:
            H._task_event(c, row["id"], who.actor, "check_model", None, None, unchecked)
        if handoff and owner == "bot:" + BOTOPS:
            carried = carried_request(c, who, body.request_id)
            if carried:
                H.event(c, who.actor, "botops.request_carried", row["id"], carried)
                if carried["lends"]:
                    # The person's own words on the task, so BotOps does what they asked, not what the bot read into it.
                    words = (H.message(c, carried["message_id"]) or {}).get("body") or ""
                    name = (H.human(c, H.actor_id(carried["person"])) or {}).get("name") or H.actor_id(carried["person"])
                    H._write_message(c, H.KEEPER, owner, f"{name} asked {H.actor_id(who.actor)} for this, in their words:\n\n> "
                                     + "\n> ".join(words[:2000].splitlines() or [""]),
                                     H.conversation(c, row["conversation_id"]), "say",
                                     {"task": row["id"], "comment": True, "quiet": True, "carried": True}, None, None)
        elif handoff and body.request_id:
            raise Problem("request_id", "Only a task for BotOps carries a person's request", 422)
        if asked_botops:
            request_id = asked_botops["cited"]
            # BotOps' lending reads `botops.asked_via_assistant` (delegated_identity); the other helpers' record says
            # which helper, and lends nothing.
            if owner == "bot:" + BOTOPS:
                H.event(c, who.actor, "botops.asked_via_assistant", row["id"], asked_botops)
            else:
                H.event(c, who.actor, "assistant.asked_helper", row["id"], {**asked_botops, "helper": owner})
            words = (H.message(c, request_id) or {}).get("body") or ""
            name = (H.human(c, H.actor_id(who.actor)) or {}).get("name") or H.actor_id(who.actor)
            H._write_message(c, H.KEEPER, owner, f"{name} asked the {settings.assistant_name} for this, in their words:\n\n> "
                             + "\n> ".join(words[:2000].splitlines() or [""]),
                             H.conversation(c, row["conversation_id"]), "say",
                             {"task": row["id"], "comment": True, "quiet": True, "carried": True}, None, None)
        inherited_request = parent.get("request_id") if parent and H.is_human(who.actor) and who.actor == parent["requester"] else None
        if request_id in ("turn", "default"):
            # `turn` names the run's own request: store that message, which delegated_identity resolved, so the task's
            # own run (whose turn is the task notice) still finds the person's words.
            request_id = (H.DELEGATION.get() or {}).get("cited") or None
        if H.is_human(who.actor) and (request_id or inherited_request):
            c.execute("UPDATE tasks SET request_id=? WHERE id=?", (inherited_request or request_id, row["id"]))
        for url in body.links:
            H.task_link(c, who.actor, row["id"], url, mover=True)
        # The parent it was filed under is a relation written here too, and reads back first.
        filed_under = TR.parent_of(c, row["id"])
        readback = TR.readback(c, row["id"], filed_under, "parent") if filed_under else []
        own_subtask = []
        for other, kind in others:
            changed = TR.relate(c, who.actor, row["id"], other, kind, mover=mover(c, who) or None)
            readback += TR.readback(c, row["id"], other, kind, changed=changed)
            own_subtask.append(TR.own_subtask_warning(c, row["id"], other, kind))
        if body.roles:
            try:
                TRo.set_roles(c, who.actor, row["id"], body.roles, mover=mover(c, who) or None)
            except ValueError as exc:
                raise Problem("roles", str(exc), 422)
        note = " ".join(filter(None, (usage_limits.warning(c, owner) or bot_stops.warning(c, owner) or H.setup_hold(c, owner),
                                      *own_subtask, joined)))
        return {"task": task_view(H.task(c, row["id"]), c, visible_sql=auth.task_sql(c, who), who=who),
                **({"sent_as": delivery.sent_as("defer", due)} if body.next_run and H.is_bot(owner) else {}),
                **({"readback": readback} if readback else {}),
                **({"joined": {"task": joins["id"], "title": joins["title"], "owner": joins["owner"]}} if joins else {}),
                **ask_ingest.reply(gate), **org_rule_check.answer(rule_gate),
                **({"warning": note} if note else {}), **({"warnings": row["warnings"]} if row.get("warnings") else {})}

    def review_preflight(who, tid, step=None, child_title=None, status=None, task_type=None):
        from . import review_gate, step_flow
        # None is reserved for a genuinely unconfigured GitHub integration.
        # If a skipped preflight becomes applicable during mutation, fail closed.
        if child_title is not None:
            # A child titled for one of the parent board's review steps; a parent this caller cannot read is
            # refused by the create itself.
            with store.read() as c:
                try:
                    parent = auth.task(c, who, auth.resolve_task(c, who, tid))
                except Problem:
                    return {}
                if not review_gate.is_review(c, parent, child_title):
                    return {}
        if child_title is None and step is None and status is None and task_type is None:
            return {}
        with store.read() as c:
            row = auth.task(c, who, auth.resolve_task(c, who, tid))
            typ = H.type_get(c, task_type if task_type is not None else row.get("type_id") or H.GENERAL_TYPE)
            if not typ:
                return {}  # Mutation reports an invalid destination without trusting cached PR evidence.
            previous_type = row.get("type_id")
            row = {**row, "type_id": typ["id"]}
            if child_title is None:
                # Match _task_state's destination resolution, including a type-only
                # move whose retained status selects a review step on the new board.
                effective = status if status is not None else row.get("status") or "open"
                if (task_type is not None and typ['id'] != previous_type and row.get('blocked_kind') == 'gate'
                        and status is None and step is None):
                    effective = row.get('blocked_from_status') or effective
                current = next((s for s in typ["steps"] if s["id"] == row.get("step_id")), None)
                if step is not None:
                    named = next((s for s in typ["steps"] if s["id"] == step), None)
                    named = named or next((s for s in typ["steps"] if s["name"] == step), None)
                elif current and current["status"] == effective:
                    named = current
                else:
                    named = next((s for s in typ["steps"] if s["status"] == effective), None)
                if not named or not step_flow.gated(c, named["id"]):
                    return {}
            if not review_gate.engineering(c, row):
                return {}
            prs = review_gate.links(c, row)
        return review_gate.snapshot(app.state.github_app, prs) if prs else {}

    def cap_before_preflight(request, who, owner_ref):
        """The task cap (CONTRACT §1) before any LLM preflight: a refused send spends no judgment and writes nothing.
        A retry whose Idempotency-Key already has a saved answer is not checked: `mutate` replays that answer."""
        if not owner_ref:
            return
        from .shared_bots import route
        with store.read() as c:
            if store.saved(c, who, request.url.path, request.headers.get("idempotency-key")):
                return
            original = auth.target(c, who, owner_ref, need="write")
            task_caps.check(c, who.actor, auth.target(c, who, route(c, who.actor, original), need="write"))

    def saved_retry(request, who):
        """Whether this request's Idempotency-Key already has a saved answer: `mutate` replays it without the write, so
        ask ingest does not judge it again (a retry makes no judge call, and so writes no second row either)."""
        with store.read() as c:
            return store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))

    @app.post("/api/v2/tasks")
    def create_task(request: Request, body: M.TaskCreate):
        parents = [r.task for r in body.relations if r.kind == "parent"]
        cap_before_preflight(request, request.state.identity, body.owner)
        snapshots = review_preflight(request.state.identity, parents[0], child_title=body.title.strip()) if parents else None
        who = request.state.identity
        rc = rule_check(who, "task_create", rule_create_text(body),
                        lambda c: [rule_target(c, who, body.owner), *(org_rule_check.company(c) if rule_public(body)
                                                                     else [])])
        # A write the rule check holds, or a saved retry, skips ask ingest's call.
        ingest = None if org_rule_check.holds(rc) or saved_retry(request, who) else \
            ask_ingest.preflight(store, app.state.judge, request.state.identity, body.owner, body.title, body.body)
        return ask_ingest.judged(store, ingest, lambda: mutate(
            request, body, lambda c: task_create(c, request.state.identity, body, review_prs=snapshots,
                                                 ingest=ingest, rule=rc)))

    def rule_prepare_task(store_, bot, replay):
        body = replay.get("body") or {}
        return ask_ingest.preflight(store_, app.state.judge, bot, body.get("owner"), body.get("title"), body.get("body"))

    @org_rule_check.replayer("task_create", prepare=rule_prepare_task)
    def rule_replay_task(c, bot, hold, replay, ingest, gate=None):
        # The create's own privacy refusals run as the bot and its run (privacy.require_create).
        return task_create(c, bot, M.TaskCreate.model_validate(replay["body"]), ingest=ingest,
                           rule=org_rule_check.decided(gate))

    @app.post("/api/v2/tasks/dry-run")
    def task_dry_run(request: Request, body: M.TaskCreate):
        """The checks a create would fail, nothing written and no refusal counted."""
        from .mcp import task_dry_run as check
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return check(c, auth, who, body)

    def task_update_one(c, caller, tid, body, snapshots, view=True, ingest=None, rule=None):
        """One task's change, as `POST /api/v2/tasks/{tid}` makes it and each task of a bulk change
        (`POST /api/v2/task-bulk`, which needs no view of each task back): the same permissions, events and wakes."""
        # A person's request to BotOps can purge tasks that are not really needed, as that person.
        who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        if body.private is False and H.task_private(c, row) and (caller.role == "bot" or caller.task_actor):
            raise Problem("privacy", "Only the human requester can make a private task company-visible",
                          422 if who.role == "bot" else 403)
        if who is not caller:
            H.event(c, caller.actor, "task.update_delegated", task_id,
                    {"on_behalf_of": who.actor, "message_id": body.on_behalf_of,
                     "close": body.close, "status": body.status, "owner": body.owner})
        if row["version"] != body.version:
            raise Problem("version_conflict", "Task changed; fetch it and retry your update", 409)
        if body.owner:
            auth.require_bot_contact(c, who, auth.target(c, who, body.owner, need="write"), task_id=task_id, kind="task")
        if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        # The org rule check (backend/org_rule_check.py): held, the task stays as it is.
        rule_gate = org_rule_check.apply(c, rule, rule_update_text(body, row),
                                         {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)}) \
            if rule else None
        if rule_gate and rule_gate.get("held"):
            for verdict in ingest or ():
                ask_ingest.skipped(c, verdict, "held")
            return org_rule_check.answer(rule_gate)
        allowance = 0
        if rule_gate and rule_gate.get("warn"):
            # The warning line goes on the change itself, where the recipient reads it.
            warned = rule_warned_fields(rule_gate, body, row, approved=exact_approval(c, who.actor))
            body, allowance = body.model_copy(update=warned), rule_summary_allowance(rule_gate, warned)
        if body.close:
            if any(v is not None for v in (body.title, body.status, body.owner, body.due, body.body, body.lane,
                                           body.labels, body.waiting_on, body.rank, body.roles,
                                           body.goal_id, body.type, body.step, body.step_rank, body.number, body.private, body.visibility, body.public_title, body.public_summary, body.size,
                                           body.verdict, body.verdict_head)):
                raise Problem("close", "Close and edit are separate operations", 422)
            note = body.note or ""
            if (note.strip() and getattr(who, "via", "") == "botops" and who.actor != "bot:" + BOTOPS
                    and row["owner"] == "bot:" + BOTOPS):
                # The close is the person's (only the requester or a human closes), but the words are
                # BotOps' own report on its own task: they read as BotOps, like its other progress notes.
                H.task_comment(c, "bot:" + BOTOPS, task_id, note, wake=False)
                note = ""
            H.task_close(c, who.actor, task_id, note=note, quiet=body.quiet, mover=mover(c, who) or None)
            warnings, gates = None, []
        else:
            fields = body.model_dump(exclude={"version", "close", "on_behalf_of"})
            privacy.require_wait(c, caller, row, body.waiting_on)
            gates, waits = [], []
            for verdict in ingest or ():
                # Ask ingest (backend/ask_ingest.py), judged against the plate before the change: a bot handing its
                # own task to a person is filing it for them, and sent back or FYI with the filter on the task stays
                # the bot's; a wait sent back or FYI is not set. One request may do both, each judged on its own.
                if verdict["kind"] == "task" and row["owner"] == who.actor and body.owner and \
                        H.resolve_actor(c, body.owner) == verdict["person"]:
                    gate = ask_ingest.apply(c, verdict)
                    ask_ingest.record(c, verdict, gate, task_id)
                    if gate["acts"]:
                        fields["owner"] = None
                elif verdict["kind"] == "wait" and row["owner"] == who.actor and \
                        row.get("waiting_on") != verdict["person"] and \
                        H.resolve_actor(c, body.waiting_on) == verdict["person"]:
                    gate = ask_ingest.apply(c, verdict)
                    waits.append((verdict, gate))
                    if gate["acts"]:
                        fields["waiting_on"] = None
                        if fields.get("status") == "waiting":
                            fields["status"] = None
                else:
                    ask_ingest.skipped(c, verdict, "not_applied")
                    continue
                gates.append(gate)
            if "size" in body.model_fields_set and body.size is None:
                fields["clear_size"] = True
            if fields.get("owner") and body.owner:
                new_owner = auth.target(c, who, body.owner, need="write")
                if new_owner != row["owner"]:
                    task_caps.check(c, who.actor, new_owner)
            after = H.task_update(c, who.actor, task_id, **fields, mover=mover(c, who) or None, review_prs=snapshots,
                                  receipt_owner=Access.load_owner(c, settings)["email"], public_allowance=allowance)
            if fields.get("owner") and body.owner and new_owner != row["owner"]:
                task_caps.record(c, task_id, who.actor, new_owner)
            warnings = after.get("warnings")
            for verdict, gate in waits:
                # A wait on a person asks in a card of its own (hubdb._ask_in_subtask): that card is what reaches
                # their Needs you, so the gate's row names it (Hands needed reads it).
                card = after.get("asked_in") if not gate["acts"] else None
                ask_ingest.record(c, verdict, gate, card["id"] if card else task_id)
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))
        return {**({"task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who)} if view else {}),
                **({"warnings": warnings} if warnings else {}),
                **(ask_ingest.reply(*gates) if not body.close else {}), **org_rule_check.answer(rule_gate)}

    @app.post("/api/v2/tasks/{tid}")
    def update_task(request: Request, tid: str, body: M.TaskUpdate):
        caller = request.state.identity
        if body.owner:
            with store.read() as c:
                try:
                    current = auth.task(c, caller, auth.resolve_task(c, caller, tid))
                except Problem:
                    current = None
                if current and auth.target(c, caller, body.owner, need="write") != current["owner"] and not \
                        store.saved(c, caller, request.url.path, request.headers.get("idempotency-key")):
                    task_caps.check(c, caller.actor, auth.target(c, caller, body.owner, need="write"))
        snapshots = review_preflight(caller, tid, step=body.step, status=body.status, task_type=body.type)
        rc = rule_update_check(caller, tid, body)
        # A write the rule check holds skips ask ingest.
        ingest = [] if org_rule_check.holds(rc) or saved_retry(request, caller) else update_ingest(caller, tid, body)
        return ask_ingest.judged(store, ingest, lambda: mutate(request, body, lambda c: task_update_one(
            c, caller, tid, body, snapshots, ingest=ingest, rule=rc)))

    def update_ingest(caller, tid, body):
        """Ask ingest for a bot's change to its own task: the handoff and the wait, judged each on its own (a request
        may name both)."""
        if not (body.owner or body.waiting_on) or not ask_ingest.gated(caller, settings.assistant_bot):
            return []
        row = readable_task(caller, tid)
        if not row or row["owner"] != caller.actor:
            return []
        return [v for v in (
            handoff_preflight(caller, row, body.owner, body.note) if body.owner else None,
            ask_ingest.preflight(store, app.state.judge, caller, body.waiting_on, row["title"],
                                 body.note or row.get("note") or "", kind="wait") if body.waiting_on else None) if v]

    def rule_prepare_update(store_, bot, replay):
        return update_ingest(bot, replay["task"], M.TaskUpdate.model_validate(replay["body"]))

    @org_rule_check.replayer("task_update", prepare=rule_prepare_update)
    def rule_replay_update(c, bot, hold, replay, ingest, gate=None):
        body = M.TaskUpdate.model_validate(replay["body"])
        row = H.task(c, replay["task"])
        if row:
            # `send` refused it if the task changed since the hold (its version is the check's object), so this is
            # the version it was checked at; the route's guard ran there too, as the bot and its run.
            body = body.model_copy(update={"version": row["version"]})
        return task_update_one(c, bot, replay["task"], body, None, ingest=ingest or [],
                               rule=org_rule_check.decided(gate))

    org_rule_check.REPLAY["task_owner"] = rule_replay_update
    org_rule_check.REPLAY["bulk"] = rule_replay_update
    org_rule_check.PREPARE["task_owner"] = rule_prepare_update
    org_rule_check.PREPARE["bulk"] = rule_prepare_update

    def handoff_preflight(caller, row, owner, note=None):
        """Ask ingest for a bot handing its own task `row` to `owner`: judged like a task filed for them."""
        return ask_ingest.preflight(store, app.state.judge, caller, owner, row["title"],
                                    "\n\n".join(filter(None, (row.get("body"), note))), kind="task")

    # One bulk change holds the write lock for every task it changes, and answers with a line for each:
    # past this many matching tasks it is refused whole, and the caller narrows the filter.
    BULK_CAP = 200

    def bulk_select(c, who, f):
        """The ids of the tasks `f` (M.TaskFilter) selects that `who` may read, in the list's order. A filter that
        names nothing, or more than BULK_CAP matches, is refused."""
        named = (f.owner, f.requester, f.status not in (None, "", "all") or None, f.label, f.type, f.step, f.number,
                 f.updated_since, f.member, f.ids)
        if not any(named):
            raise Problem("filter", "Name at least one filter: a bulk change never applies to every task", 422)
        owner, requester, filters = task_filters(
            c, owner=f.owner, requester=f.requester, status=f.status, lane=f.lane, label=f.label, type=f.type,
            step=f.step, number=f.number, updated_since=f.updated_since, member=f.member, role=f.role,
            snoozed=f.snoozed, by_id=f.ids is not None or f.number is not None, exact=True)
        visible_sql = auth.task_sql(c, who)
        if f.ids is not None:
            _, found = task_idents(c, who, ",".join(f.ids), visible_sql)
            filters["ids"] = [tid for tid in found.values() if tid]
        rows = H.tasks(c, owner=owner, requester=requester, limit=BULK_CAP + 1, visible=visible_sql, **filters)
        if len(rows) > BULK_CAP:
            raise Problem("too_many", f"More than {BULK_CAP} tasks match; a bulk change takes at most {BULK_CAP}: "
                          "narrow the filter", 422, extra={"cap": BULK_CAP})
        return [row["id"] for row in rows]

    def bulk_change(c, row, body):
        """The single update (M.TaskUpdate) `body` (M.TaskBulk) makes to the task `row`, or None when the task already
        is that way."""
        base = {"version": row["version"], **({"note": body.note} if body.note else {})}
        if body.action == "close":
            return None if row["status"] == "closed" else M.TaskUpdate(**base, close=True)
        if body.action == "status":
            return None if row["status"] == body.status else M.TaskUpdate(**base, status=body.status)
        if body.action == "owner":
            return None if H.resolve_actor(c, body.owner) == row["owner"] else M.TaskUpdate(**base, owner=body.owner)
        if body.action == "step":
            # By id or name within the task's own type, as a single update reads it.
            steps = (H.type_get(c, row.get("type_id") or H.GENERAL_TYPE) or {}).get("steps") or []
            target = next((s for s in steps if s["id"] == body.step), None) or next(
                (s for s in steps if s["name"] == body.step), None)
            return None if target and target["id"] == row.get("step_id") else M.TaskUpdate(**base, step=body.step)
        labels = H.task_labels(row)
        add = [key for key in dict.fromkeys(str(t).strip().lower() for t in body.add) if key not in labels]
        drop = {str(t).strip().lower() for t in body.remove}
        changed = [key for key in labels if key not in drop] + add
        return None if changed == labels else M.TaskUpdate(**base, labels=changed)

    @app.post("/api/v2/task-bulk")
    def task_bulk(request: Request, body: M.TaskBulk):
        """One change to every task a filter selects (the filters of `GET /api/v2/tasks`), each made as its single
        update would be: a task the caller may not change is skipped with the reason, the rest still change. A dry
        run makes every change and rolls all of them back, so its counts are the real ones."""
        caller = request.state.identity
        auth.domain(caller)
        with store.read() as c:
            chosen = bulk_select(c, caller, body.filter)
        # A move into a review step reads each task's pull requests first, outside the write lock (review_preflight).
        snapshots, unchecked, ingests = {}, {}, {}
        # The org rule check, each task's change on its own (a few calls at a time): a held task is skipped, the rest
        # still change.
        rule_checks = {}
        if (body.note or body.action == "owner") and not body.dry_run and \
                org_rule_check.author_of(caller, settings.assistant_bot):
            with store.read() as c:
                pairs = []
                for tid in chosen:
                    r = H.task(c, tid)
                    try:
                        change = bulk_change(c, r, body) if r else None
                    except ValidationError:
                        change = None
                    if change is not None and rule_update_text(change, r):
                        pairs.append((r, change))
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as pool:
                for (r, _), verdict in zip(pairs, pool.map(lambda p: rule_update_check(caller, p[0]["id"], p[1], p[0]),
                                                          pairs)):
                    if verdict:
                        rule_checks[r["id"]] = {**verdict, "operation": "bulk"}
        if body.action == "owner" and body.owner and not body.dry_run and ask_ingest.gated(caller, settings.assistant_bot) \
                and not saved_retry(request, caller):
            # A bot handing its own tasks to a person: each is judged by ask ingest first, a few calls at a time.
            with store.read() as c:
                mine = [r for r in (H.task(c, tid) for tid in chosen) if r and r["owner"] == caller.actor
                        and not org_rule_check.holds(rule_checks.get(tid))]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as pool:
                for r, verdict in zip(mine, pool.map(lambda r: handoff_preflight(caller, r, body.owner, body.note), mine)):
                    if verdict:
                        ingests[r["id"]] = verdict
        if body.action in ("step", "status"):
            for tid in chosen:
                try:
                    snapshots[tid] = review_preflight(caller, tid, step=body.step, status=body.status)
                except Problem as exc:
                    unchecked[tid] = exc

        def work(c):
            # Every task this call skips keeps its refusal on record, and the call counts once toward quarantine.
            token = H.BULK_CALL.set(H.new_id())
            try:
                return bulk_work(c)
            finally:
                H.BULK_CALL.reset(token)

        def bulk_work(c):
            # Chosen again under the write lock, so each task is judged as it is now.
            ids = bulk_select(c, caller, body.filter)
            changed, unchanged, skipped, judged = [], [], [], {}
            # Work queued to run after commit (a webhook's doorbell) goes with the rows a rollback takes back.
            queued = len(c.after_commit)
            if body.dry_run:
                c.execute("SAVEPOINT bulk_dry_run")
            for tid in ids:
                row = H.task(c, tid)
                try:
                    change = bulk_change(c, row, body)
                except ValidationError as exc:
                    skipped.append((tid, "invalid", exc.errors()[0]["msg"]))
                    continue
                if change is None:
                    unchanged.append(tid)
                    continue
                c.execute("SAVEPOINT bulk_task")
                before = len(c.after_commit)
                try:
                    if tid in unchecked or tid not in snapshots and body.action in ("step", "status"):
                        raise unchecked.get(tid) or Problem("changed", "Task changed during the bulk change; try again", 409)
                    done = task_update_one(c, caller, tid, change, snapshots.get(tid), view=False,
                                           ingest=None if org_rule_check.holds(rule_checks.get(tid)) else
                                           [ingests[tid]] if tid in ingests else None, rule=rule_checks.get(tid))
                except H.Refused as exc:
                    # Audited and answered as the single update's refusal would be; the other tasks go on.
                    del c.after_commit[before:]
                    problem = refused(c, caller, exc, savepoint="bulk_task")
                    skipped.append((tid, problem.code, problem.detail))
                except Problem as exc:
                    c.execute("ROLLBACK TO bulk_task")
                    c.execute("RELEASE bulk_task")
                    del c.after_commit[before:]
                    skipped.append((tid, exc.code, exc.detail))
                else:
                    c.execute("RELEASE bulk_task")
                    if done.get("held"):
                        skipped.append((tid, "held", done["held"]["text"]))    # its hold stays; the task does not change
                        continue
                    changed.append(tid)
                    if done.get("ingest"):
                        judged[tid] = done["ingest"]      # what ask ingest did with this handoff, and why
            # A handoff judged for a task this change did not land names its call: refused, or unchanged.
            held_tids, refused_tids = {t for t, code, *_ in skipped if code == "held"}, {t for t, *_ in skipped}
            for tid, verdict in ingests.items():
                if tid not in changed and tid not in held_tids:
                    ask_ingest.skipped(c, verdict, "refused" if tid in refused_tids else "not_applied")
            if body.dry_run:
                c.execute("ROLLBACK TO bulk_dry_run")
                c.execute("RELEASE bulk_dry_run")
                del c.after_commit[queued:]
            # Each task the caller can still read is named; one a change took out of their sight is only counted.
            visible = auth.task_sql(c, caller)
            readable = {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) "
                                                f"AND ({visible})", (json.dumps(ids),))}
            def line(tid, code=None, reason=None):
                row = H.task(c, tid)
                return {"id": tid, "number": row.get("number"), "title": row["title"],
                        **({"code": code, "reason": reason} if code else {}),
                        **({"ingest": judged[tid]} if tid in judged else {})}
            return {"dry_run": body.dry_run, "matched": len(ids), "cap": BULK_CAP,
                    "changed": len(changed), "unchanged": len(unchanged), "skipped": len(skipped),
                    "tasks": {"changed": [line(t) for t in changed if t in readable],
                              "skipped": [line(t, code, reason) for t, code, reason in skipped if t in readable]}}
        return ask_ingest.judged(store, list(ingests.values()), lambda: mutate(request, body, work))

    @app.post("/api/v2/tasks/{tid}/run-now")
    def task_run_now(request: Request, tid: str, body: M.Empty):
        """Wake the task's bot for this task now, as the task (not a chat). A person, the task's
        requester, or the fleet maintainer's stuck-task sweep (never on a private bot's task)."""
        def work(c):
            who = request.state.identity
            maintainer = who.actor == H.bot_actor(H.FLEET_MAINTAINER)
            task_id = auth.resolve_task(c, who, tid, visible=auth.fleet_task_sql(c, who) if maintainer else None)
            if maintainer:
                row = H.task(c, task_id)
                if not row:
                    raise Problem("not_found", "Task not found", 404)
                if not privacy.task_readable(c, who, row):
                    raise Problem("not_found", "Task not found", 404)
                hidden = auth.unreadable_bots(c, who)
                if any(str(a).startswith("bot:") and H.actor_id(a) in hidden
                       for a in (row["owner"], row["requester"])):
                    raise Problem("forbidden", "This task involves a bot whose activity BotOps may not read", 403)
            else:
                row = auth.task(c, who, task_id)
                if (who.role not in ("human", "owner")
                        and not H.task_represents(c, who.actor, row["requester"])
                        and not (who.actor != row["owner"] and H.task_represents(c, who.actor, row["owner"]))):
                    raise Problem("forbidden", "Only a person, the task's requester, a party's manager or BotOps starts it early", 403)
                # Running it wakes the bot: that is a request to it.
                if str(row["owner"]).startswith("bot:") and who.actor != row["owner"]:
                    auth.require_write(c, who, H.actor_id(row["owner"]))
            after, queued = H.task_run_now(c, who.actor, task_id)
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who), "queued": queued}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/qa-waiver")
    def task_qa_waiver(request: Request, tid: str, body: M.TaskQaWaiver):
        """Waive a Dev ticket's current In QA step for one exact pull request head, with a reason: the ticket's owner
        or a manager, always a person (backend/qa_waivers.py). Recorded in task history as a waiver, never as a QA
        verdict; hosted checks and both reviews on that head stay required."""
        from . import qa_waivers, review_gate
        who = request.state.identity
        # BotOps or the Assistant acting for a person carries the person's actor; the waiver is still a bot's call.
        person_only(who, "waives a Dev ticket's QA step")
        with store.read() as c:
            row = auth.task(c, who, auth.resolve_task(c, who, tid))
            prs = review_gate.links(c, row) if review_gate.engineering(c, row) else []
        # A fresh GitHub read outside the write lock, as a review move takes (review_preflight).
        snapshots = review_gate.snapshot(app.state.github_app, prs) if prs else {}
        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            current = auth.task(c, who, task_id)
            owner = current["owner"]
            manager = mover(c, who) or who.role == "human" and auth.manages(
                c, who, "bot" if H.is_bot(owner) else "person", H.actor_id(owner))
            waiver = qa_waivers.waive(c, who.actor, task_id, body.head, body.reason, body.step, snapshots,
                                      manager=bool(manager))
            return {"waiver": waiver, "task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/model")
    def task_run_model(request: Request, tid: str, body: M.TaskRunModel):
        """The model this task's runs use, with its effort and harness (backend/task_run_models.py). An absent
        `model` keeps it and changes only what is named; a null `model` clears it, so the bot's task model runs
        the task again.
        Whoever may change the bot's model may change this, and a bot never does. `readiness` says whether the bot's
        computer can run it now; `warning` is plain words for a cheaper pick on a bot that sends, spends, publishes or
        writes. It still saves."""
        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            return settings_admin.set_task_run_model(c, who, task_id, body)
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/snooze")
    def task_snooze(request: Request, tid: str, body: M.TaskSnooze):
        """Snooze a task until a date: off the board, lists and Needs you, and quiet, until then
        (backend/task_snooze.py). The requester, the owner, anyone on it by role, or a mover."""
        def work(c):
            from . import task_snooze as TS
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            after = TS.snooze(c, who.actor, task_id, body.until, mover=mover(c, who), note=body.note)
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/later")
    @app.post("/api/v2/tasks/{tid}/defer")           # its older name, kept for clients from before Steer, Queue, Defer
    def task_defer(request: Request, tid: str, body: M.TaskDefer):
        """Say when a deferred task a run carried (or one still waiting for a run) is picked up again: after another
        task is done, at a time, or with the next run; or that the run handled it (backend/quiet_work.py). The task's
        own bot, or a person (`hub task later`)."""
        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            after = None
            if body.after:
                after = auth.resolve_task(c, who, body.after)
                auth.task(c, who, after)
            row = quiet_work.defer(c, who.actor, task_id, after=after, at=body.at, next_run=body.next_run,
                                   handled=body.handled)
            return {"task": task_view(row, c, visible_sql=auth.task_sql(c, who), who=who)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/unsnooze")
    def task_unsnooze(request: Request, tid: str, body: M.Empty):
        """End a snooze early: the task is back now and its owner bot is woken once."""
        def work(c):
            from . import task_snooze as TS
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            after = TS.unsnooze(c, who.actor, task_id, mover=mover(c, who))
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/ask")
    def task_ask(request: Request, tid: str, body: M.Answer):
        """The owner's one question to the task's requester (hubdb.task_ask). A bot's question to a person passes
        ask ingest first (backend/ask_ingest.py): sent back, it is withdrawn as soon as it is asked and the task does
        not wait on them."""
        who = request.state.identity
        rc = rule_check(who, "task_ask", body.text, lambda c: [auth.task(c, who, auth.resolve_task(c, who, tid))["requester"]])
        ingest = None if org_rule_check.holds(rc) or saved_retry(request, who) else task_ask_ingest(who, tid, body)
        return ask_ingest.judged(store, ingest, lambda: mutate(request, body, lambda c: task_ask_write(
            c, who, tid, body, ingest, rc)))

    def readable_task(who, tid):
        """The task `who` may read, or None: what an ingest preflight outside the write judges."""
        with store.read() as c:
            try:
                return auth.task(c, who, auth.resolve_task(c, who, tid))
            except Problem:
                return None

    def task_ask_ingest(who, tid, body):
        if not ask_ingest.gated(who, settings.assistant_bot) or not (row := readable_task(who, tid)):
            return None
        return ask_ingest.preflight(store, app.state.judge, who, row["requester"], row["title"], body.text,
                                    kind="task_ask")

    def rule_prepare_task_ask(store_, bot, replay):
        return task_ask_ingest(bot, replay["task"], M.Answer.model_validate(replay["body"]))

    @org_rule_check.replayer("task_ask", prepare=rule_prepare_task_ask)
    def rule_replay_task_ask(c, bot, hold, replay, ingest, gate=None):
        return task_ask_write(c, bot, replay["task"], M.Answer.model_validate(replay["body"]), ingest,
                              org_rule_check.decided(gate))

    def task_ask_write(c, who, tid, body, ingest=None, rc=None):
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        lifecycle.refuse_send(c, who, None, what="task questions")
        rule = org_rule_check.apply(c, rc, body.text, {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)})
        if rule and rule.get("held"):
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.answer(rule)
        body = body.model_copy(update={"text": org_rule_check.warned_text(rule, body.text)}) if rule else body
        gate = ask_ingest.apply(c, ingest) if ask_ingest.matches(ingest, row["requester"]) else None
        if ingest and not gate:
            ask_ingest.skipped(c, ingest, "target")
        # Not parked on a question that is not sent: the task keeps its status and the bot's own note.
        msg = H.task_ask(c, who.actor, task_id, body.text, park=not (gate and gate["acts"]))
        if gate:
            ask_ingest.record(c, ingest, gate, task_id, msg["id"])
            if gate["acts"]:
                H.task_ask_resolve(c, who.actor, task_id, msg["id"])
                # Why, on the task's record, quiet as the merge and let-through notes are.
                H.task_comment(c, H.KEEPER, task_id, gate["text"], wake=False)
        return {**msg, **ask_ingest.reply(gate), **org_rule_check.answer(rule)}

    def comment_write(c, who, tid, body, ingest=None, rc=None, reader=None):
        """One task comment, as `POST /api/v2/tasks/{tid}/comments` writes it and Send anyway replays it."""
        reader = reader or who
        mark = H.jobs_mark(c)
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        from .chat_goals import may_direct, own_room, set_goal
        from .task_review import comment_rights, check_ask
        comment_rights(c, auth, who, task_id)
        ask = check_ask(c, task_id, body.ask, auth, who)
        # The org rule check (backend/org_rule_check.py), applied before anything is written: held, nothing is.
        rule = org_rule_check.apply(c, rc, body.text, {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)})
        if rule and rule.get("held"):
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.answer(rule)
        text = org_rule_check.warned_text(rule, body.text)
        attached, report = visuals.attach(c, who, app.state.files, app.state.blobs, body.attachments, task_id=task_id)
        moves = mover(c, who)
        # A person @mentioned in a comment gets it as a question, so it reaches their Needs you.
        # The comment itself is the question to the first one, unless it would wake a bot, which
        # must hear it; everyone else mentioned gets their own copy. A task with no bot on it has
        # no one else to hear the comment, so a copy there would only show it twice.
        # A goal is the comment's text, so it is not turned into a question to anyone.
        people = [] if ask or who.role == "bot" or body.delivery == "goal" \
            else H.mentioned_people(c, body.text, row, who.actor)
        # Steer and queue wake the bot on the task (a bot's is its needs_action); with no bot on the task the
        # comment is plain. Defer and goal do not: a goal's own control does (set_goal below).
        target = H.comment_target(c, who.actor, row)
        to_bot = H.is_bot(target)
        if body.delivery in ("steer", "queue") and to_bot and who.role != "bot" and not may_direct(c, who, row):
            raise Problem("delivery", "Only someone on this task can wake its bot: send it with defer", 403)
        if body.delivery == "goal" and who.role != "bot" and not may_direct(c, who, row):
            raise Problem("delivery", "Only someone on this task can set its goal: send it with defer", 403)
        # A goal is set in the task's own room only. In a chat the task was filed into, the goal is the chat's
        # (chat_goals.own_room), so nothing is saved and no control is queued or cancelled there.
        if body.delivery == "goal" and not own_room(c, row):
            raise Problem("delivery", "This task has no room of its own: send it with steer or queue", 409)
        defer_for = body.defer_for
        if body.delivery in ("steer", "queue", "defer"):
            way = body.delivery
        elif body.delivery == "goal":
            way = None
        elif ask:
            way = "steer" if who.role == "bot" or H.task_participant(c, who.actor, row) or moves else "defer"
        elif who.role == "bot" and body.needs_action:
            way = "steer"
        else:
            way, defer_for = H.comment_way(c, who.actor, row, body.text, mover=moves)
        directs = who.role == "bot" or may_direct(c, who, row)
        if way == "defer" and to_bot and not ask and not directs:
            # A deadline wakes the bot as a steer would, so it takes the same right. Someone off the task defers
            # with none: their comment waits for the bot's next turn here.
            if body.defer_for not in (None, delivery.NEVER):
                raise Problem("delivery", "Only someone on this task can give its bot a deadline: send it with "
                              "defer and no defer_for", 403)
            defer_for = delivery.NEVER
        # Ride-along: a defer someone chose while the bot works this task goes in right after that turn, if they
        # may wake it. A default defer (a plain comment on a bot-filed task, or from outside it) keeps its deadline.
        rides = body.delivery == "defer" and directs
        if way == "defer" and to_bot and not ask and rides and delivery.running_in_session(
                c, H.actor_id(target), [row["conversation_id"]], task_id):
            way = "queue"
        if people and not (to_bot and way in ("steer", "queue")):
            ask, people = H.mention_ask(body.text, people[0]), people[1:]
        wake = way in ("steer", "queue")
        extra = {"attachments": attached, "files": [a["ref"] for a in attached]} if attached else {}
        if body.needs_action or (who.role == "bot" and body.delivery in ("steer", "queue")):
            extra["needs_action"] = True
        if way == "queue" and to_bot:
            extra["delivery"] = "queue"      # after the bot's running turn, not into it (execution.inputs)
        # Judged against the plate before the question is on it.
        gate = ask_ingest.apply(c, ingest) if ingest and ask else None
        msg = H.task_comment(c, who.actor, task_id, text, wake=wake, ask=ask, public=body.public, approval_id=body.approval_id,
                             extra_refs=extra or None)
        due = None
        if way == "defer" and to_bot and not ask:
            due = delivery.defer(c, target, "comment", msg["id"], conversation_id=msg["conversation_id"],
                                 task_id=task_id, defer_for=defer_for)
        goal = None
        if body.delivery == "goal":
            # The task session's goal, set as a chat's is (chat_goals.set_goal): its /goal control wakes the bot.
            goal = set_goal(c, auth, who, row["conversation_id"], "set", body.text, send)
        if gate and not ask_ingest.matches(ingest, msg.get("to_actor")):
            gate = None
            ask_ingest.skipped(c, ingest, "target")
        elif ingest and not gate:
            ask_ingest.skipped(c, ingest, "not_applied")
        if gate:
            ask_ingest.record(c, ingest, gate, task_id, msg["id"])
            if gate["acts"]:
                H.task_ask_resolve(c, who.actor, task_id, msg["id"])
        copies = [H.task_comment(c, who.actor, task_id, body.text, wake=False, ask=H.mention_ask(body.text, person))
                  for person in people]
        for item in attached:
            c.execute("INSERT OR IGNORE INTO task_file_reviews(file_id,version) VALUES(?,?)",
                      (item["id"], item["version"]))
            c.execute("UPDATE task_file_reviews SET comment_id=coalesce(comment_id,?) WHERE file_id=? AND version=?",
                      (msg["id"], item["id"], item["version"]))
        comments = visual_view(c, who, H.task_comments(c, task_id, actor=privacy.actor(reader)))
        msg = next(m for m in comments if m["id"] == msg["id"])
        held = H.held_note(c, msg.get("to_actor"))
        waits = bot_to_bot.held_reason(msg)
        if bot_to_bot.held_reason(msg):
            way, due = "defer", None             # a bot's plain comment to a bot waits for its next run
        return {"comment": msg, "comments": comments, **H.comment_delivery(c, msg, copies, since=mark),
                **({"sent_as": delivery.sent_as(way, due)} if to_bot and way else {}),
                **({"warning": held} if held else {}), **({"held": waits} if waits else {}),
                **({"visuals": report} if report else {}), **({"goal": goal} if goal else {}),
                **ask_ingest.reply(gate), **org_rule_check.answer(rule)}

    def comment_ingest(who, tid, body):
        if not body.ask or not ask_ingest.gated(who, settings.assistant_bot) or not (row := readable_task(who, tid)):
            return None
        asked = body.ask.who or (row["owner"] if who.actor == row["requester"] else row["requester"])
        return ask_ingest.preflight(store, app.state.judge, who, asked, row["title"], body.text, kind="question",
                                    asked=[q.model_dump() for q in body.ask.questions])

    def rule_prepare_comment(store_, bot, replay):
        return comment_ingest(bot, replay["task"], M.TaskComment.model_validate(replay["body"]))

    @org_rule_check.replayer("comment", prepare=rule_prepare_comment)
    def rule_replay_comment(c, bot, hold, replay, ingest, gate=None):
        return comment_write(c, bot, replay["task"], M.TaskComment.model_validate(replay["body"]), ingest,
                             org_rule_check.decided(gate))

    @app.post("/api/v2/tasks/{tid}/comments")
    def task_comment(request: Request, tid: str, body: M.TaskComment):
        """A comment on a task. `delivery` says how it reaches the task's bot (backend/delivery.py): steer into its
        running turn, queue right after it, defer to its next turn on the task (at the latest `defer_for`, 24h), or
        goal: the task session's goal becomes the text (chat_goals.set_goal). Omitted, H.comment_way decides. A bot's
        question to a person passes ask ingest first (backend/ask_ingest.py): sent back, it is withdrawn as soon as it
        is asked."""
        who = request.state.identity
        counsel.no_attachments(who, body.attachments)    # before the rule check holds anything (visuals.attach too)
        if body.delivery and (body.ask or (body.needs_action and body.delivery != "steer")):
            raise Problem("delivery", "A question always wakes the one it is for, and needs_action is steer: send it "
                          "without a delivery, or with steer", 422)
        if wrong := delivery.check(body.delivery, body.defer_for):
            raise Problem("delivery", wrong, 422)
        if body.delivery == "goal" and (body.attachments or body.public):
            raise Problem("delivery", "A goal is the comment's text: send it without files or as a public comment",
                          422)
        rc = rule_check(who, "comment", body.text, lambda c: rule_task_audience(
            c, auth.task(c, who, auth.resolve_task(c, who, tid)), who.actor, body.text,
            *([rule_target(c, who, body.ask.who)] if body.ask and body.ask.who else [])))
        ingest = None if org_rule_check.holds(rc) or saved_retry(request, who) else comment_ingest(who, tid, body)
        result = ask_ingest.judged(store, ingest, lambda: mutate(request, body, lambda c: comment_write(
            c, who, tid, body, ingest, rc, request.state.identity)))
        if body.attachments:
            app.state.file_metadata.wake.set()   # a chat file shared here is a new task copy: preview it now
        return result

    @app.get("/api/v2/tasks/{tid}/comments")
    def task_comments(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {"comments": visual_view(c, request.state.identity,
                                            H.task_comments(c, task_id, actor=privacy.actor(request.state.identity)))}

    @app.get("/api/v2/tasks/{tid}/answers")
    def task_answers(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {"answers": [m["answer"] for m in H.task_comments(c, task_id, actor=privacy.actor(request.state.identity)) if m.get("answer")]}

    def task_answer_write(c, who, tid, body, warn=""):
        from .task_review import answer_task
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        wake = who.role == "bot" or H.task_participant(c, who.actor, row) or mover(c, who)
        return answer_task(c, auth, who, task_id, body, wake=wake, warn=warn)

    def task_answer_text(body):
        """What a task answer says, as the rule check reads it: each question's picks and the free text."""
        if body.dismiss:
            return ""
        lines = [f"{q}: {', '.join(a)}" for q, a in sorted(body.answers.items()) if a]
        return "\n".join([*lines, *([body.other] if body.other else [])])

    @app.post("/api/v2/tasks/{tid}/answers")
    def task_answer(request: Request, tid: str, body: M.TaskAnswer):
        who = request.state.identity

        def write(c, gate):
            return task_answer_write(c, who, tid, body, org_rule_check.warn_line(gate))
        return checked_write(request, body, who, "task_answer", task_answer_text(body),
                             lambda c: rule_task_audience(c, auth.task(c, who, auth.resolve_task(c, who, tid)), who.actor),
                             write, {"task": tid, "body": body.model_dump(mode="json", by_alias=True)})

    @org_rule_check.replayer("task_answer")
    def rule_replay_task_answer(c, bot, hold, replay, ingest, gate=None):
        # Its route's guard (`/tasks/{tid}/answers`: the task's readers may read the run's private work) ran in send.
        return task_answer_write(c, bot, replay["task"], M.TaskAnswer.model_validate(replay["body"]),
                                 org_rule_check.warn_line(gate))

    @app.get("/api/v2/tasks/{tid}/tree")
    def task_tree(request: Request, tid: str):
        with store.read() as c:
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            ids = [r["id"] for r in H.descendants(c, task_id)]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            visible_ids = {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({marks}) AND ("
                                                  + auth.task_sql(c, who) + ")", ids)}
            return H.task_tree(c, task_id, visible_ids)

    @app.get("/api/v2/tasks/{tid}/links")
    def get_task_links(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {"links": H.task_links(c, task_id)}

    @app.delete("/api/v2/tasks/{tid}/links/{link_id}")
    def delete_task_link(request: Request, tid: str, link_id: M.ID):
        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            H.task_unlink(c, who.actor, task_id, link_id, mover=mover(c, who))
            return {"links": H.task_links(c, task_id)}
        return mutate(request, M.Empty(), work)

    def own_comment(c, who, tid, mid, allow_deleted=False):
        """The task that comment `mid` is on, when the caller wrote the comment; a refusal otherwise."""
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        msg = H.comment_on(c, row, mid)
        if not msg or msg.get("deleted_at") and not allow_deleted:
            raise Problem("not_found", "No such comment on this task", 404)
        if not msg.get("deleted_at"):
            privacy.require_message(c, who, msg)
        # The Assistant and BotOps act with a person's rights, but the words are the person's own.
        if who.via:
            raise Problem("forbidden", "Only a comment's author edits or deletes it, signed in as themselves", 403)
        if not H.is_comment(msg):
            raise Problem("forbidden", "Only a comment can be edited or deleted, not a question, an answer, "
                                       "a notice or a chat message", 403)
        if msg["from_actor"] != who.actor:
            raise Problem("forbidden", "Only the person or bot who wrote a comment can edit or delete it", 403)
        return row

    @app.post("/api/v2/tasks/{tid}/comments/{mid}")
    def task_comment_edit(request: Request, tid: str, mid: str, body: M.TaskComment):
        """The author changes a comment's text. It wakes nobody and is not sent again; the new text
        is new words to the bot on the task, so it needs what a new comment needs."""
        who = request.state.identity
        if body.ask is not None or body.attachments:
            raise Problem("validation", "An edit changes only a plain comment's text", 422)
        rc = rule_check(who, "comment_edit", body.text, lambda c: rule_task_audience(
            c, auth.task(c, who, auth.resolve_task(c, who, tid)), who.actor, body.text), obj=mid)
        return mutate(request, body, lambda c: comment_edit_write(c, who, tid, mid, body, rc),
                      check=lambda c: own_comment(c, who, tid, mid, allow_deleted=True))

    def comment_edit_write(c, who, tid, mid, body, rc=None):
        row = own_comment(c, who, tid, mid)
        from .task_review import comment_rights
        comment_rights(c, auth, who, row["id"])
        # Held, the comment keeps its old text.
        rule = org_rule_check.apply(c, rc, body.text, {"task": row["id"], "message": mid,
                                                       "body": body.model_dump(mode="json", by_alias=True)})
        if rule and rule.get("held"):
            return org_rule_check.answer(rule)
        msg = H.task_comment_edit(c, who.actor, row["id"], mid, org_rule_check.warned_text(rule, body.text),
                                  approval_id=body.approval_id)
        return {"comment": visual_view(c, who, [msg])[0],
                "comments": visual_view(c, who, H.task_comments(c, row["id"], actor=privacy.actor(who))), "woke": False,
                **org_rule_check.answer(rule)}

    @org_rule_check.replayer("comment_edit")
    def rule_replay_comment_edit(c, bot, hold, replay, ingest, gate=None):
        body = M.TaskComment.model_validate(replay["body"])
        return comment_edit_write(c, bot, replay["task"], replay["message"], body, org_rule_check.decided(gate))

    @app.post("/api/v2/tasks/{tid}/comments/{mid}/delete")
    def task_comment_delete(request: Request, tid: str, mid: str, body: M.Empty):
        """The author takes a comment back: never listed again, never handed to a bot."""
        who = request.state.identity
        def work(c):
            row = own_comment(c, who, tid, mid)
            msg = H.task_comment_delete(c, who.actor, row["id"], mid)
            return {"comment": visual_view(c, request.state.identity, [msg])[0],
                    "comments": visual_view(c, request.state.identity, H.task_comments(
                c, row["id"], actor=privacy.actor(request.state.identity))), "woke": False}
        return mutate(request, body, work, check=lambda c: own_comment(c, who, tid, mid, allow_deleted=True))

    @app.post("/api/v2/tasks/{tid}/comments/{mid}/resolve")
    def task_ask_resolve(request: Request, tid: str, mid: str, body: M.TaskAskResolve):
        """Settle an open question answered somewhere else (its asker or the task's owner bot, quoting the
        person), or withdraw it (its asker). It leaves every Needs you list; it wakes nobody."""
        who = request.state.identity
        def readable(c):
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            asked = H.comment_on(c, row, mid)
            if not asked or asked.get("deleted_at") or asked["kind"] != "ask":
                raise Problem("not_found", "No such question on this task", 404)
            privacy.require_message(c, who, asked)
            return row
        def work(c):
            row = readable(c)
            out = H.task_ask_resolve(c, who.actor, row["id"], mid, body.quote)
            return {"comment": visual_view(c, who, [out["answer"] or out["ask"]])[0],
                    "comments": visual_view(c, who, H.task_comments(c, row["id"], actor=privacy.actor(who))), "woke": False}
        return mutate(request, body, work, check=readable)

    @app.post("/api/v2/tasks/{tid}/delete")
    def task_delete(request: Request, tid: str, body: M.Empty):
        """A person deletes a task: its human requester, or anyone who may move any task. Bots and
        delegated sessions close instead. Closing reads as finished work, so a person can always delete,
        whatever bots did on the task: that work stays in place, unreadable while the task is gone
        (task_delete.delete_tasks keep_work). The task goes to the trash, from which it can be restored."""
        who = request.state.identity
        person_only(who, "deletes a task; close it instead")
        def work(c):
            from .task_delete import delete_tasks
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            if row["requester"] != who.actor and not mover(c, who):
                raise Problem("forbidden", "Only the task's requester, or someone who may move any task, deletes it", 403)
            report = delete_tasks(c, [task_id], apply=True, actor=who.actor, keep_work=True)
            if not report["applied"]:
                reasons = ", ".join(sorted(report["refused"])) or "unknown task"
                raise Problem("has_work", "This task carries work (" + reasons + "); close it instead", 409)
            return {"deleted": task_id}
        return mutate(request, body, work)

    def person_only(who, doing):
        if who.role == "bot" or getattr(who, "via", "") or getattr(who, "task_actor", None):
            raise Problem("forbidden", "Only a person, signed in as themselves, " + doing, 403)

    @app.get("/api/v2/deleted-tasks")
    def deleted_tasks(request: Request):
        """Deleted tasks, newest first: all of them for someone who may move any task, otherwise the
        ones this person deleted or asked for."""
        who = request.state.identity
        person_only(who, "sees deleted tasks")
        from .task_delete import trash
        with store.read() as c:
            return {"tasks": trash(c, None if mover(c, who) else who.actor, audience=auth.task_sql(c, who))}

    @app.post("/api/v2/tasks/{tid}/restore")
    def task_restore(request: Request, tid: str, body: M.Empty):
        """Put a deleted task back with its conversation, comments, links and number: whoever deleted
        it, its requester, or anyone who may move any task."""
        who = request.state.identity
        person_only(who, "restores a task")
        def work(c):
            from .task_delete import restore_tasks, trashed
            row = trashed(c, tid, audience=auth.task_sql(c, who))
            if not row or row["deleted_by"] != who.actor and row["requester"] != who.actor and not mover(c, who):
                raise Problem("not_found", "No deleted task " + str(tid) + " you may restore", 404)
            report = restore_tasks(c, [row["task_id"]], actor=who.actor)
            if report.get("conflict"):
                raise Problem("conflict", "This task cannot go back: something newer has taken its place", 409)
            return {"restored": row["task_id"], "unlinked": report["unlinked"].get(row["task_id"], []),
                    "skipped": report["skipped"]}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/links")
    def task_links(request: Request, tid: str, body: M.TaskLink):
        who = request.state.identity
        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            if body.remove:
                H.task_unlink(c, who.actor, task_id, body.remove, mover=mover(c, who))
            elif body.url:
                H.task_link(c, who.actor, task_id, body.url, body.title, kind=body.kind, mover=mover(c, who),
                            wake_once=body.wake_once, release_repo=settings.release_repo)
            else:
                raise Problem("kind", "Send a url to add or remove with a link id", 422)
            return {"links": H.task_links(c, task_id)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/relations")
    def task_relation(request: Request, tid: str, body: M.TaskRelation):
        """Relate another task to this one (`{id} kind {task}`), or take the relation off with `remove`."""
        who = request.state.identity
        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            other_id = auth.resolve_task(c, who, body.task)
            auth.task(c, who, task_id)
            auth.task(c, who, other_id)
            changed = TR.relate(c, who.actor, task_id, other_id, body.kind, remove=body.remove,
                                mover=mover(c, who) or None)
            # The stored direction in words, from both ends (Dev 256): a reversed write shows in the reply.
            note = None if body.remove else TR.own_subtask_warning(c, task_id, other_id, body.kind)
            return {"readback": TR.readback(c, task_id, other_id, body.kind, remove=body.remove, changed=changed),
                    "task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who),
                    **({"warning": note} if note else {})}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/waits")
    def task_wait_on(request: Request, tid: str, body: M.TaskWait):
        """Wait on `task:<id>` (blocked_by), `pr:<url|task>` (until it merges) or `release:<tag>` (until it is
        published); `remove` takes the wait off. The upstream task never moves (backend/milestone_waits.py)."""
        who = request.state.identity
        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            kind, _, value = body.on.partition(":")
            note = None
            if kind.strip().lower() == "task":
                if body.note and not body.remove:
                    H.refuse(c, who.actor, "note", "a note goes with a pr: or release: wait; a task: wait is a blocked_by "
                                                   "and says nothing of its own")
                other_id = auth.resolve_task(c, who, value.strip())
                auth.task(c, who, other_id)
                changed = TR.relate(c, who.actor, task_id, other_id, "blocked_by", remove=body.remove,
                                    mover=mover(c, who) or None)
                readback = TR.readback(c, task_id, other_id, "blocked_by", remove=body.remove, changed=changed)
                note = None if body.remove else TR.own_subtask_warning(c, task_id, other_id, "blocked_by")
            else:
                H._writer(c, who.actor)
                H._task_private_writer(c, who.actor, row)
                # The same gate as a blocked_by: a wait parks the task's news just as a blocker does.
                TR._blocker_gate(c, who.actor, row, TR._mover(c, who.actor, mover(c, who) or None))
                def resolve(value):
                    other = auth.resolve_task(c, who, value)
                    auth.task(c, who, other)
                    return other
                if body.remove:
                    gone = milestone_waits.remove(c, who.actor, row, body.on, release_repo=settings.release_repo,
                                                  resolve_task=resolve)
                    readback = [f"Removed: {TR._name(c, task_id)} waits for {gone}"]
                else:
                    wait = milestone_waits.add(c, who.actor, row, body.on, release_repo=settings.release_repo,
                                               resolve_task=resolve, note=body.note)
                    readback = [("" if wait["added"] else "Already: ") + f"{TR._name(c, task_id)} {wait['sentence']}"
                                + (" (already happened: woken once)" if wait["woke_at"] else "")]
            return {"readback": readback,
                    "task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who),
                    **({"warning": note} if note else {})}
        return mutate(request, body, work)

    @app.get("/api/v2/preferences/{key}")
    def preference(request: Request, key: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = c.execute("SELECT value_json FROM preferences WHERE actor=? AND key=?", (who.actor, key)).fetchone()
            return {"key": key, "value": json.loads(row[0]) if row else None}

    @app.post("/api/v2/preferences/{key}")
    def set_preference(request: Request, key: str, body: M.Preference):
        who = request.state.identity
        auth.domain(who)
        if key.startswith("assistant."):
            raise Problem("forbidden", "Your Assistant's settings are set in Settings > General", 403)
        if key == "meetings.auto_share":
            views.human_only(who)
            if not isinstance(body.value, bool):
                raise Problem("validation", "meetings.auto_share is a boolean", 422)
        # Hide from my chart on the Strategy Bot: a person's own view
        if key == strategy_bot.HIDE_PREF:
            views.human_only(who)
            if not isinstance(body.value, bool):
                raise Problem("validation", strategy_bot.HIDE_PREF + " is a boolean", 422)
        if not re.fullmatch(r"[a-z0-9_.-]{1,64}", key):
            raise Problem("kind", "A preference key is lower-case letters, digits, dots, dashes", 422)
        if not request.headers.get("idempotency-key"):
            # An unchanged value (a page re-saving what it read) answers from a read: no writer wait.
            with store.read() as c:
                same = c.execute("SELECT 1 FROM preferences WHERE actor=? AND key=? AND value_json=?",
                                 (who.actor, key, encode(body.value))).fetchone()
                if same:
                    privacy.guard_write(c, getattr(request.state, "privacy_source", who), request.url.path, auth)
            if same:
                return {"key": key, "value": body.value}
        def work(c):
            at = H.now()
            c.execute("INSERT INTO preferences(actor,key,value_json,updated) VALUES(?,?,?,?) "
                      "ON CONFLICT(actor,key) DO UPDATE SET value_json=excluded.value_json, updated=excluded.updated",
                      (who.actor, key, encode(body.value), at))
            if key == strategy_bot.HIDE_PREF:
                strategy_bot.mirror_hide(c, who.actor, encode(body.value), at)
            return {"key": key, "value": body.value}
        return mutate(request, body, work)

    @app.post("/api/v2/approvals")
    def approval_request(request: Request, body: M.ApprovalCreate):
        who = request.state.identity
        if body.kind == "support":
            from .support import require_on
            require_on(settings)            # nothing to draft where nothing can be sent (docs/support.md)
        def work(c):
            auth.domain(who)
            # What an approval clears is a send, a spend or a publication; an onboarding bot does none of them.
            lifecycle.refuse_send(c, who, None, what="anything")
            if body.task_id:
                auth.task(c, who, body.task_id)
            to = None
            if body.kind in proposals.KINDS:     # a redesign proposal: checked now, decided by the top of the org
                proposals.check(c, body.kind, body.payload)
                to = proposals.decider(c)
            row = H.approval_request(c, who.actor, body.kind, body.payload, body.task_id,
                                     supersedes=body.supersedes_approval, to=to)
            # A proposal answers as a bot's cap PUT does too: the approval's id and that it waits on a person.
            return {**row, "approval": row["id"], "status": "pending"} if body.kind in proposals.KINDS else row
        return mutate(request, body, work)

    @app.get("/api/v2/approvals/{aid}")
    def approval(request: Request, aid: str):
        with store.read() as c:
            row = auth.approval(c, request.state.identity, aid)
            privacy.require_payload(c, request.state.identity, row)
            privacy.require_message(c, request.state.identity, H.message(c, row["message_id"]))
            # Its history: asked, decided (by whom, and for whom when the person's Assistant decided), undone, withdrawn,
            # spent.
            history = [{"ts": e["ts"], "actor": e["actor"], "action": e["action"].removeprefix("approval."),
                        **{k: v for k, v in (H._json(e["detail_json"], {}) or {}).items()
                           if k in ("decision", "note", "via", "was", "kind")}}
                       for e in c.execute("SELECT ts,actor,action,detail_json FROM events WHERE target=? AND action IN "
                                          "('approval.request','approval.decide','approval.undo','approval.withdraw','approval.consume') "
                                          "ORDER BY ts", (aid,))]
            return {**row, "history": history}

    @app.post("/api/v2/approvals/{aid}")
    def decide(request: Request, aid: str, body: M.ApprovalDecision):
        who = request.state.identity
        def work(c):
            row = auth.approval(c, who, aid, decide=True)
            if row["kind"] == "support":
                # Sending off the install is a person's own tap: not their token or the Assistant (which may have drafted it).
                from .support import require_person
                require_person(who)
            privacy.require_payload(c, who, row)
            decided = H.approval_decide(c, who.actor, aid, body.decision, body.note)
            if row["kind"] in proposals.KINDS and body.decision == "approved":
                proposals.apply(c, auth, settings, who, row)     # the hub applies it itself (CONTRACT §5)
            if row["kind"] in ("finance", "spend"):
                from .finance import actions as finance
                finance.approval_decided(c, aid)                 # Finance acts on it in the same transaction
            return decided
        return mutate(request, body, work)

    @app.post("/api/v2/approvals/{aid}/undo")
    def undo_decision(request: Request, aid: str, body: M.Empty):
        """The person takes back what their Assistant decided for them, before anything acted on it. Their own click."""
        who = request.state.identity
        if who.via:
            raise Problem("forbidden", "Only the person undoes this, in Settings > General", 403)
        def work(c):
            auth.approval(c, who, aid, decide=True)
            return H.approval_undo(c, who.actor, aid)
        return mutate(request, body, work)

    @app.post("/api/v2/approvals/{aid}/consume")
    def consume(request: Request, aid: str, body: M.ApprovalConsume):
        who = request.state.identity
        def work(c):
            row = auth.approval(c, who, aid)
            if row["requested_by"] != who.actor or row["payload_hash"] != body.payload_hash:
                raise Problem("approval", "Consume requires the requester and exact approved payload", 403)
            return H.approval_consume(c, who.actor, aid)
        return mutate(request, body, work)

    @app.get("/api/v2/status")
    def statuses(request: Request, bot: str | None = None, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            from . import usage_limits
            default = usage_limits.company(c)

            from .events import status_line            # the same line live events send
            if bot:
                auth.target(c, who, bot, need="read")
                from .views import since_time
                inputs = privacy.status_inputs(c, [bot])[bot]
                return {"bot": bot, "status": status_line(c, who, bot, default, inputs, auth=auth),
                        "history": [privacy.status(c, who, r, **inputs) for r in H.status_history(c, bot, since=since_time(since))]}
            from .views import updating
            readable = auth.bot_accesses(c, who)
            # A first-turn waiter may not have a bot_status row until /started. Include live candidates;
            # status_line keeps only an actual wait when no stored status exists.
            statuses = [s for s in c.execute("SELECT bot FROM bot_status UNION SELECT bot FROM attempts "
                                             "WHERE state IN ('leased','running') ORDER BY bot")
                        if readable.get(s["bot"], auth.FULL)["read"]]
            inputs = privacy.status_inputs(c, [s["bot"] for s in statuses])
            from .events import prime_status_lines
            prime_status_lines(c, [s["bot"] for s in statuses])
            return {"bots": [line for s in statuses
                             if (line := status_line(c, who, s["bot"], default, inputs[s["bot"]], auth=auth)) is not None],
                    # Tico updating itself, shown beside the status line
                    "updating": updating(c)}

    def report_mutate(request, body, work):
        """`mutate` for a change to a manager bot's report: a refusal over the week's limit still files its Needs you ask
        for the person above, which the refusal's rollback would take with it (settings_admin._report_cap)."""
        try:
            return mutate(request, body, work)
        except Problem as exc:
            ask = getattr(exc, "ask", None)
            if exc.code == "report_cap" and ask:
                store.write(request.state.identity, lambda c: H.task_create(
                    c, ask["actor"], ask["title"], ask["body"], ask["person"]))
            raise

    @app.post("/api/v2/bots")
    def create_bot(request: Request, body: M.BotDefinitionCreate):
        who = request.state.identity
        def work(c):
            if who.role == "bot" and who.via == "botops":
                return settings_admin.create_report(c, who, body)     # a manager bot's report, asked of BotOps
            created = settings_admin.create_bot(c, who, body)
            if body.template:
                # Adding from the catalog after onboarding is the same handoff to BotOps.
                created.update(onboarding.attach_template(c, who, created["slug"], body.template,
                                                          body.instructions))
            return created
        return report_mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/onboarded")
    def bot_onboarded(request: Request, bot: str, body: M.OnboardingComplete):
        """Mark onboarding complete: a person above the bot on the org chart makes it Active (backend/lifecycle.py).
        The bot's own call (`hub bot ready`) is its "I'm ready", with `summary` saying what it learned, and leaves it
        Onboarding. Repeating either changes nothing more."""
        who = request.state.identity
        return mutate(request, body, lambda c: onboarding.onboarded(c, who, bot, summary=body.summary))

    @app.get("/api/v2/seats")
    def seat_list(request: Request):
        """The Recruiting seats: roles on the org chart with a job description and reporting line, and no bot yet."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            parents = views.seat_parents(c, settings, auth, who)
            return {"seats": [{**seat, "org_parent": parents.get(seat["id"], "")} for seat in lifecycle.seats(c)]}

    @app.get("/api/v2/org-models")
    def org_models(request: Request):
        """Published Tico starter org charts, the only chart models the Strategy Bot uses for re-org recommendations."""
        from . import org_models as published_models
        auth.domain(request.state.identity)
        return {"models": published_models.published()}

    @app.post("/api/v2/seats")
    def seat_create(request: Request, body: M.SeatCreate):
        """Add a Recruiting seat: the owner anywhere, a person under themselves or someone below them."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: lifecycle.create_seat(auth, c, who, body.title, body.description,
                                                                     body.reports_to, body.team))

    @app.post("/api/v2/seats/batch")
    def seat_batch_create(request: Request, body: M.SeatBatchCreate):
        """Add one confirmed group of Recruiting seats; the owner may place them anywhere, others only in their chain."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: lifecycle.create_seats(auth, c, who, body.seats))

    def _recover_seat(seat_id):
        """A cloud agent request a dead process left in flight on this seat is recovered in its own transaction first, so
        the action below goes ahead and a refusal of it cannot roll the recovery back (backend/cloud_agents.py)."""
        from . import cloud_agents
        cloud_agents.recover(store, seat_id)

    @app.patch("/api/v2/seats/{seat_id}")
    def seat_update(request: Request, seat_id: str, body: M.SeatUpdate):
        who = request.state.identity
        auth.domain(who)
        _recover_seat(seat_id)
        return mutate(request, body, lambda c: lifecycle.update_seat(auth, c, who, seat_id,
                                                                     body.model_dump(exclude_unset=True)))

    @app.post("/api/v2/seats/draft")
    def seat_draft(request: Request, body: M.SeatDraft):
        """A job description for a job not posted yet, from its title: composed from the template catalog at once, or
        written by the model when no catalog card is the job, or with `redraft` rewritten once by the model. The model
        is Gemini when the server has a key, else the Tico AI Gateway with the company's gateway key
        (backend/recruiting.py). Stores nothing."""
        who = request.state.identity
        auth.domain(who)
        recruiting.may_draft(who)
        boss = ""
        if body.redraft and body.reports_to:
            with store.read() as c:
                boss = recruiting.boss_name(auth, c, who, body.reports_to)

        def gateway():
            # Read only when the model is asked, inside its time bound: a catalog draft never waits on the vault.
            with store.read() as c:
                return gateway_connection.target(c, app.state.vault, settings)[:2]
        return recruiting.draft(onboarding, body.title, body.team, boss, body.redraft, gateway=gateway)

    @app.get("/api/v2/seats/{seat_id}")
    def seat_show(request: Request, seat_id: str):
        """One Recruiting seat, with whether the caller may edit it (backend/recruiting.py)."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return recruiting.show(auth, c, who, seat_id)

    @app.post("/api/v2/seats/{seat_id}/recruit")
    def seat_recruit(request: Request, seat_id: str, body: M.Empty):
        """Recruit: up to eight Tico templates for the seat at once, best fit first, each with one line, what it needs,
        how well it matches and a suggested model. Local: nothing leaves this server."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return recruiting.candidates(auth, onboarding, c, who, seat_id)

    @app.post("/api/v2/seats/{seat_id}/hire")
    def seat_hire(request: Request, seat_id: str, body: M.SeatHire):
        """Pick a candidate: the bot is created in Onboarding in the seat's place with its reporting line, and only
        then its repository (by its computer for a starter, else BotOps)."""
        who = request.state.identity
        auth.domain(who)
        _recover_seat(seat_id)
        return mutate(request, body, lambda c: recruiting.hire(auth, onboarding, c, who, seat_id, body))

    @app.post("/api/v2/seats/{seat_id}/close")
    def seat_close(request: Request, seat_id: str, body: M.Empty):
        """Take a seat off the chart; its row stays for the record."""
        who = request.state.identity
        auth.domain(who)
        _recover_seat(seat_id)
        return mutate(request, body, lambda c: lifecycle.close_seat(auth, c, who, seat_id))

    @app.post("/api/v2/bots/{bot}/archive")
    def archive_bot(request: Request, bot: str, body: M.BotArchive):
        who = request.state.identity
        def work(c):
            # A manager bot's ask is already BotOps acting as that bot (act_for_requester), not a person's request to find.
            acting = who if who.role == "bot" and who.via == "botops" else (
                delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who)
            result = settings_admin.archive(c, acting, bot, body)
            rooms.archive_shared_room(c, acting.actor, bot)
            if acting is not who:
                H.event(c, who.actor, "bot.archive_delegated", bot,
                        {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of, "successor": body.successor})
            return result
        return report_mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/restore")
    def restore_bot(request: Request, bot: str, body: M.Empty):
        """Bring an archived bot back: the owner, an admin or the bot's owner, to the status it had, else planned."""
        who = request.state.identity
        def work(c):
            result = settings_admin.restore(c, who, bot)
            if rooms.thread_mode(c, bot) == rooms.SHARED:
                rooms.sync_shared_room(c, auth, bot, actor=who.actor, create=True)
            return result
        return report_mutate(request, body, work)

    # A person's request lends their rights for as long as the task it created (or that cites it) is open, up to
    # botops_act.LEND_MAX_DAYS after they last spoke on it, and never again once someone else reopens it. A chat
    # message with no open task lends them for a week; a new task filed from a message also needs it within the week,
    # or one open task could renew another for ever.
    DELEGATION_DAYS = 7
    # Keeper wakes a follow-through run may start from: the daily update, and a task's due, stall or routine notice.
    FOLLOW_THROUGH_WAKES = ("update", "due", "stalled", "occurrence")

    def open_task(task):
        return bool(task) and task["status"] not in ("done", "closed", "declined")

    def lending_task(c, task):
        """An open task that still lends its requester's rights (`botops_act.lend_lapsed`)."""
        return open_task(task) and not botops_act.lend_lapsed(c, task)

    def week_old(created):
        return created < H.shift(H.now(), days=-DELEGATION_DAYS)

    def handed_over(c, task, botops):
        """Who gave BotOps a task a person asked for, when it was not that person: the actor of the last owner change, or
        "" when there is none to name. None when BotOps has owned it since it was filed, or the requester moved it to
        BotOps themselves (not through BotOps or the Assistant). Otherwise any bot that owns a person's task could
        reassign it to BotOps and borrow their rights for its next run."""
        moved = c.execute("SELECT * FROM task_events WHERE task_id=? AND field='owner' ORDER BY ts DESC, rowid DESC "
                          "LIMIT 1", (task["id"],)).fetchone()
        if not moved:
            made = c.execute("SELECT detail_json FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                             (task["id"],)).fetchone()
            return None if made and H._json(made["detail_json"], {}).get("owner") == botops else ""
        moved = dict(moved)
        if moved["new"] == botops and moved["actor"] == task["requester"] and not moved.get("via"):
            return None
        return moved["actor"] or ""

    def delegated_identity(c, who, ref, new_task=False, carry=False):
        """Resolve the run's requester, never a human named in its text. Human messages and tasks
        carry that human's rights; bot messages and tasks carry only that bot's rights. Fleet work
        with no requester keeps BotOps' own. Explicit message references must belong to this requester.
        `new_task`: the reference is the request a task is being filed from. `carry`: the call is one a bot's hand-off
        (carried_request) makes with the person's rights (botops_act.carried_lends); otherwise it keeps the bot's.
        """
        if who.via == "botops" and who.role == "bot" and not who.attempt_id:
            if ref in ("turn", "default"):
                return who
            raise Problem("on_behalf_of", "A bot request cannot borrow a human's rights", 403)
        if who.via == "botops" and who.attempt_id:
            who = Identity("bot:" + BOTOPS, "bot", runner_id=who.runner_id,
                           attempt_id=who.attempt_id, agent=who.agent)
        if who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "Only BotOps applies changes on a requester's behalf", 403)
        def acting_as(principal, cited="", run="live"):
            # Keep the actual bot's credential so writes revalidate its lease under the lock,
            # and private reads intersect the requester with the bot doing the work.
            # Never confirmed: only the person's own tap on a Confirm card is (auth.py, x-tico-assistant-action), so
            # every route's "always a click" rule (`risky`) asks them.
            if H.is_human(principal.actor):
                H.DELEGATION.set({"for": principal.actor, "cited": cited, "run": run})
            return replace(principal, via="botops", confirmed=False, task_actor=who.actor,
                           runner_id=who.runner_id, attempt_id=who.attempt_id, agent=who.agent)
        message_id = ref
        explicit = bool(ref) and ref not in ("turn", "default")
        turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                         (who.attempt_id,)).fetchone() if who.attempt_id else None
        if explicit and not turn:
            raise Problem("on_behalf_of", "Cite the request that started this run", 403)
        if explicit:
            cited = followed_task(c, ref)
            if cited:
                return acting_as(follow_through(c, who, cited), cited["id"], "follow_through")
        if not explicit:
            if not turn:
                return who
            message_id = turn["message_id"]
            initial = H.message(c, message_id)
            task_id = H.message_task_id(initial) if initial else None
            task = H.task(c, task_id) if task_id else None
            requester = task["requester"] if task else (initial or {}).get("from_actor", "")
            if task and H.is_human(requester):
                creator = c.execute("SELECT actor FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                                    (task_id,)).fetchone()
                if creator and H.is_bot(creator["actor"]):
                    requester = creator["actor"]
                requester = filed_for_bot(c, task_id, who) or requester
            handed = (handed_over(c, task, who.actor) if task and H.is_human(requester) and task["owner"] == who.actor
                      else None)
            if handed is not None:
                # Someone other than the person gave BotOps their task: it runs with the rights of the bot that moved
                # it (its refusals become the person's Confirm card), never the person's.
                if not H.is_bot(handed) or handed == who.actor:
                    raise Problem("on_behalf_of", "Someone other than the person who asked for this task gave it to BotOps, "
                                  "so it lends no one's rights; they can move it to BotOps themselves or ask in their "
                                  "BotOps chat", 403)
                requester = handed
            others_comment = False
            if task and initial:
                refs = initial.get("refs") or {}
                relayed = any(refs.get(key) for key in ("via", "assistant", "slack", "routing"))
                if (refs.get("comment") and not relayed and H.is_human(requester) and initial["from_actor"] == requester
                        and task["owner"] == who.actor and open_task(task)):
                    # The task's requester commenting on it, in their own words: that is them asking.
                    auth.conversation(c, who, initial["conversation_id"])
                    H.VIA.set("botops")
                    return acting_as(auth.identity_for_actor(c, requester), task_id, "comment")
                if refs.get("comment") and not relayed and handed is not None and initial["from_actor"] == task["requester"]:
                    raise Problem("on_behalf_of", "A bot gave this task to BotOps, so your comment doesn't lend your rights; "
                                  "move it to BotOps yourself or ask in your BotOps chat", 403)
                if (refs.get("comment") and not relayed and H.is_bot(requester) and H.is_human(initial["from_actor"])
                        and task["owner"] == who.actor and open_task(task)):
                    # A person's comment on a task a bot asked BotOps for lends nothing: BotOps keeps that bot's rights,
                    # and what they can't do becomes a Confirm card for the person who commented (confirm_for_carried).
                    others_comment = True
                elif refs.get("comment") or relayed:
                    raise Problem("on_behalf_of", "Only the task's requester's own comment lends their rights; "
                                  "other comments and routed messages lend nothing", 403)
            if initial and (task or str(requester).startswith("bot:")) and requester != H.KEEPER:
                auth.conversation(c, who, initial["conversation_id"])
                lapsed = botops_act.lend_lapsed(c, task) if task and H.is_human(requester) else ""
                if lapsed:
                    raise Problem("on_behalf_of", lapsed, 403)
                if not open_task(task) and week_old(initial["created"]):
                    raise Problem("on_behalf_of", "That request is more than a week old; ask the requester again", 403)
            if str(requester).startswith("bot:") and requester != who.actor:
                if (H.bot(c, H.actor_id(requester)) or {}).get("state") != "active":
                    raise Problem("forbidden", "The requesting bot is no longer active", 403)
                person = lending_carried(c, task, requester) if carry else None
                if person and others_comment and person["person"] != initial["from_actor"]:
                    person = None           # someone else's comment never lends the carried person's rights
                if person:
                    # The bot handed on a person's own request (carried_request): BotOps acts as that person.
                    H.VIA.set("botops")
                    return acting_as(auth.identity_for_actor(c, person["person"]), person["message_id"], "carried")
                return acting_as(Identity(requester, "bot"))
            if not initial or requester == H.KEEPER or requester == who.actor:
                return who
            if task and task["owner"] == who.actor and not task.get("request_id"):
                if str(task["requester"]).startswith("human:"):
                    made = c.execute("SELECT detail_json FROM events WHERE action='task.create' AND target=? "
                                     "AND actor=? LIMIT 1", (task_id, task["requester"])).fetchone()
                    if made and H._json(made["detail_json"], {}).get("via") != "assistant":
                        person = auth.identity_for_actor(c, task["requester"])
                        return acting_as(person, task_id)
                origin = c.execute("SELECT actor FROM events WHERE action='botops.task_requested' AND target=? "
                                   "AND actor=? LIMIT 1", (task_id, task["requester"])).fetchone()
                if origin and str(origin["actor"]).startswith("human:"):
                    auth.conversation(c, who, task["conversation_id"])
                    person = auth.identity_for_actor(c, origin["actor"])
                    H.VIA.set("botops")
                    return acting_as(person, task_id)
            asked = c.execute("SELECT detail_json FROM events WHERE action='botops.asked_via_assistant' AND target=? "
                              "AND actor=? LIMIT 1", (task_id, task["requester"])).fetchone() if task else None
            if asked and task["owner"] == who.actor and H.is_human(task["requester"]) and open_task(task):
                # The person's own words to their Assistant, checked when it filed this (task_create): BotOps acts
                # as them, as if they had asked it in their BotOps chat.
                origin = H.message(c, (H._json(asked["detail_json"], {}) or {}).get("cited") or "")
                if (not origin or origin.get("deleted_at") or origin["from_actor"] != task["requester"]
                        or week_old(origin["created"])):
                    raise Problem("on_behalf_of", "The request the Assistant handed on no longer stands; ask the "
                                  "person again", 403)
                H.VIA.set("botops")
                return acting_as(auth.identity_for_actor(c, task["requester"]), origin["id"], "assistant")
            if task and task.get("request_id") in ("turn", "default"):
                # Tasks filed before the request was stored by id: the person's message the filing cited.
                made = c.execute("SELECT detail_json FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                                 (task_id,)).fetchone()
                task = {**task, "request_id": ((H._json(made["detail_json"], {}) if made else {}).get("delegation")
                                               or {}).get("cited")}
            if task and task.get("request_id") and task["owner"] == who.actor:
                origin = H.message(c, task["request_id"])
                made = c.execute("SELECT 1 FROM events WHERE action='task.create' AND target=? AND actor=? LIMIT 1",
                                 (task_id, task["requester"])).fetchone()
                if made and origin and origin["from_actor"] == task["requester"]:
                    message_id = task["request_id"]
                    c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, origin["conversation_id"]))
        msg = H.message(c, message_id)
        started = (H.message(c, turn["message_id"]) or {}).get("from_actor", "") if turn else ""
        if explicit and started and msg and msg["from_actor"] != started:
            # A turn a person started acts for that person: another person's message id borrows nothing.
            raise Problem("on_behalf_of", "Cite the message of the person who asked you, not someone else's", 403)
        if (not msg or not str(msg["from_actor"]).startswith("human:")
                or msg["to_actor"] != "bot:" + BOTOPS or msg["kind"] not in ("say", "answer")):
            raise Problem("on_behalf_of", "Cite a message a person sent BotOps asking for this change", 403)
        refs = msg.get("refs") or {}
        if refs.get("via") or refs.get("assistant"):
            # The Assistant wrote this for the person: its writes never carry the person's authority on.
            raise Problem("on_behalf_of", "That message was written by the Assistant; ask the person to send it", 403)
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        if conversation.get("task_id") or conversation.get("scope") == "task" or refs.get("task"):
            raise Problem("on_behalf_of", "A person's words inside a task are not a request to BotOps; ask in chat", 403)
        slack = refs.get("slack") if isinstance(refs.get("slack"), dict) else {}
        own_dm = (slack.get("kind") == "im" and not slack.get("recorded_only")
                  and set(conversation.get("participants") or []) == {msg["from_actor"], "bot:" + BOTOPS})
        if (refs.get("slack") or refs.get("routing")) and not own_dm:
            # Anyone in a Slack channel or thread can put words in a routed message, so those never lend a person's
            # rights. A 1:1 Slack DM is different: the gateway verified the sender (workspace member, not a guest,
            # admitted, a Tico person) and nobody else writes in it, so it counts like their Tico chat (owner
            # decision, 2026-10-01). The message decides, not the room: what they type in Tico is always their own request.
            raise Problem("on_behalf_of", "This request arrived through a Slack channel, so BotOps can't act on it for "
                          "the person. They can DM it to the Tico app in Slack or send it in their Tico chat with BotOps",
                          403)
        if explicit and set(conversation.get("participants") or []) != {msg["from_actor"], "bot:" + BOTOPS}:
            # A message cited by id: the person's own, in their own room with BotOps (not a room another person spoke in).
            raise Problem("on_behalf_of", "Cite a message from the person's own chat with BotOps", 403)
        if week_old(msg["created"]) and (new_task or not any(lending_task(c, H.task(c, row["id"])) for row in c.execute(
                "SELECT id FROM tasks WHERE request_id=? AND requester=?", (message_id, msg["from_actor"])).fetchall())):
            raise Problem("on_behalf_of", "That request is more than a week old and no open task carries it; "
                          "ask the person again", 403)
        # Only a request in a conversation this run may read: otherwise any message a bot sends
        # BotOps could borrow the rights of whoever last asked it for something.
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("on_behalf_of", "Cite a request from the conversation you are working on", 403) from None
        person = auth.identity_for_actor(c, msg["from_actor"])
        H.VIA.set("botops")           # every event and history row from here says "via BotOps"
        return acting_as(person, message_id)

    def carried_request(c, who, ref):
        """The person whose request a bot is handing BotOps, recorded on the task it files (`botops.request_carried`):
        `{"person", "message_id", "lends", "why"}`, or None when no person is behind it.

        The bot cites the person's message (`--request-id`: its id, or `turn` for the message that started this run).
        It lends that person's rights to BotOps when they wrote it themselves to this bot, in their 1:1 chat with it
        (the app or a Slack DM) or as a comment on a task they asked this bot for, in the conversation this run is
        working on, within DELEGATION_DAYS. Anything else (words in a channel, written by the Assistant, from another
        conversation, too old) still names the person, and BotOps puts what it would do to them as a Confirm card
        (`confirm_for_carried`). With no citation, a run a person's own message started names them the same way."""
        turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot=?",
                         (who.attempt_id, H.actor_id(who.actor))).fetchone() if who.attempt_id else None
        start = H.message(c, turn["message_id"]) if turn else None
        if not ref:
            refs = (start or {}).get("refs") or {}
            slack = refs.get("slack") if isinstance(refs.get("slack"), dict) else {}
            if (start and H.is_human(start["from_actor"]) and start["to_actor"] == who.actor
                    and not any(refs.get(k) for k in ("via", "assistant", "routing"))
                    and (not refs.get("slack") or slack.get("kind") == "im")):
                return {"person": start["from_actor"], "message_id": start["id"], "lends": False,
                        "why": "No message was cited"}
            return None
        msg = start if ref == "turn" else H.message(c, ref)
        if not msg or not H.is_human(msg["from_actor"]) or msg.get("deleted_at"):
            raise Problem("request_id", "Cite a message a person wrote to you (its id, or `turn` for the one that "
                          "started this run)", 422)
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("request_id", "Cite a message from a conversation you are working on", 403) from None
        person = msg["from_actor"]
        refs = msg.get("refs") or {}
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        task = H.task(c, H.message_task_id(msg, conversation) or "")
        slack = refs.get("slack") if isinstance(refs.get("slack"), dict) else {}
        own_dm = slack.get("kind") == "im" and not slack.get("recorded_only")
        if msg["to_actor"] != who.actor or msg["kind"] not in ("say", "answer"):
            why = "That message was not sent to you"
        elif refs.get("via") or refs.get("assistant"):
            why = "The Assistant wrote that message"
        elif refs.get("routing") or (refs.get("slack") and not own_dm):
            why = "That message came from a channel others write in"
        elif not start or start["conversation_id"] != msg["conversation_id"]:
            why = "That message is not in the conversation this run is working on"
        elif task and not (refs.get("comment") and task["requester"] == person and task["owner"] == who.actor
                           and open_task(task)):
            why = "Only the person's own comment on a task they asked you for carries their request"
        elif not task and set(conversation.get("participants") or []) != {person, who.actor}:
            why = "That message is not from the person's own chat with you"
        elif week_old(msg["created"]):
            why = "That message is more than a week old"
        else:
            why = ""
        return {"person": person, "message_id": msg["id"], "lends": not why, "why": why}

    def carried(c, task, requester):
        """What `carried_request` recorded on this task, filed by `requester`, or None."""
        row = c.execute("SELECT detail_json FROM events WHERE action='botops.request_carried' AND target=? AND actor=? "
                        "ORDER BY ts DESC LIMIT 1", (task["id"], requester)).fetchone() if task else None
        return H._json(row["detail_json"], {}) if row else None

    def filed_for_bot(c, task_id, who):
        """The bot a task was filed for, when BotOps filed it with rights that bot's hand-off lent (carried_request):
        it lends what that bot's rights do, never the person's, or a carried call could file itself a task that lends
        everything. None for any other task."""
        made = c.execute("SELECT detail_json FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                         (task_id,)).fetchone()
        lent = (H._json(made["detail_json"], {}) if made else {}).get("delegation") or {}
        if not isinstance(lent, dict) or lent.get("run") != "carried":
            return None
        cited = H.message(c, lent.get("cited") or "")
        return cited["to_actor"] if cited and H.is_bot(cited["to_actor"]) else who.actor

    def lending_carried(c, task, requester):
        """The carried request when it still lends the person's rights: the task is open and the message still stands,
        from that person, within DELEGATION_DAYS."""
        found = carried(c, task, requester)
        if not found or not found.get("lends") or not open_task(task):
            return None
        if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='status' AND old IN (?,?,?) LIMIT 1",
                     (task["id"], *botops_act.TERMINAL)).fetchone():
            return None             # finished once: the request it carried was done, whoever reopened it
        msg = H.message(c, found.get("message_id") or "")
        if not msg or msg.get("deleted_at") or msg["from_actor"] != found.get("person") or week_old(msg["created"]):
            return None
        return found

    def confirm_for_carried(request, exc):
        """A Confirm card for the person behind a bot's hand-off when BotOps, with only that bot's rights, was refused:
        what BotOps tried, run as the person on their own click. None when there is no such person or no such card."""
        who, acting = getattr(request.state, "privacy_source", None), getattr(request.state, "identity", None)
        if (exc.status != 403 or exc.code != "forbidden" or not who or who.actor != "bot:" + BOTOPS or not acting
                or acting.role != "bot" or acting.actor == who.actor or request.method == "GET" or not who.attempt_id):
            return None
        path = botops_act.normalize(request.url.path)
        body = botops_act.parse_body(getattr(request, "_body", b"")) if request.method != "DELETE" else None
        body = body if isinstance(body, dict) else {}
        if not path or botops_act.secret_in_request(request.method, path, body) or not assistant_valid(request.method, path, settings, BOTOPS):
            return None
        with store.transaction() as c:
            turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                             (who.attempt_id,)).fetchone()
            start = H.message(c, turn["message_id"]) if turn else None
            task = H.task(c, H.message_task_id(start) or "") if start else None
            said = (start or {}).get("refs") or {}
            commented = (task and task["requester"] == acting.actor and task["owner"] == who.actor
                         and said.get("comment") and H.is_human(start["from_actor"])
                         and not any(said.get(k) for k in ("via", "assistant", "slack", "routing")))
            # The person whose comment started this run asked, ahead of whoever the bot's hand-off named.
            found = ({"person": start["from_actor"]} if commented
                     else carried(c, task, acting.actor) if task and task["requester"] == acting.actor else None)
            if (not found and task and H.is_human(task["requester"]) and task["owner"] == who.actor
                    and handed_over(c, task, who.actor) == acting.actor):
                found = {"person": task["requester"]}      # the bot gave BotOps a task this person asked for
            if not found or not open_task(task):
                return None
            try:
                person = auth.identity_for_actor(c, found["person"])
            except Problem:
                return None
            if botops_act.admin_only(request.method, path) and person.role != "owner" and not auth.bot_admin(person):
                return None
            asker = (H.bot(c, H.actor_id(acting.actor)) or {}).get("name") or H.actor_id(acting.actor)
            summary = (("BotOps needs your OK to do this on " + asker + "'s task: ") if commented
                       else (asker + " asked BotOps for this for you: ")) + task["title"]
            return propose_card(c, person, request.method, path, {k: v for k, v in body.items() if k != "on_behalf_of"},
                                summary[:300])

    def followed_task(c, ref):
        """The task `ref` names (its id, or an unambiguous prefix of eight or more), or None when it names none."""
        ref = str(ref or "")
        prefix = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = c.execute("SELECT id FROM tasks WHERE id=? OR (length(?)>=8 AND id LIKE ? || '%' ESCAPE '\\') LIMIT 2",
                         (ref, ref, prefix)).fetchall()
        exact = [r for r in rows if r["id"] == ref]
        return H.task(c, (exact or rows)[0]["id"]) if len(exact or rows) == 1 else None

    def follow_through(c, who, task):
        """The person whose open request BotOps is following through on, in a later run: a daily-update wake, a retry
        once a busy bot finished, a notice. The run that started it is gone, so BotOps cites the task the person asked
        for. Only a task BotOps owns, that the person filed themselves (not through the Assistant), for as long as it is
        open; and never from a run a bot or another person started, which keeps its own requester's
        rights."""
        requester = task["requester"]
        if task["owner"] != who.actor or not H.is_human(requester):
            raise Problem("on_behalf_of", "Cite a task a person asked BotOps for", 403)
        if task["status"] in ("done", "closed", "declined"):
            raise Problem("on_behalf_of", "That task is closed; ask the person again", 403)
        lapsed = botops_act.lend_lapsed(c, task)
        if lapsed:
            raise Problem("on_behalf_of", lapsed, 403)
        made = c.execute("SELECT actor,detail_json FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                         (task["id"],)).fetchone()
        via = H._json(made["detail_json"], {}).get("via") if made else None
        if not made or made["actor"] != requester or via == "assistant" or filed_for_bot(c, task["id"], who):
            raise Problem("on_behalf_of", "That task was not filed by the person who asked for it", 403)
        if handed_over(c, task, who.actor) is not None:
            raise Problem("on_behalf_of", "Someone other than the person who asked for that task gave it to BotOps", 403)
        if via and not task.get("request_id"):
            # Filed by BotOps as the person: only a continuation of their message (--request-id) counts, or a
            # follow-through run could file a fresh task as them and so lend their rights for ever.
            raise Problem("on_behalf_of", "BotOps filed that task without the person's message; ask the person again", 403)
        origin = H.message(c, task["request_id"]) if task.get("request_id") else None
        if task.get("request_id") and (not origin or origin["from_actor"] != requester):
            raise Problem("on_behalf_of", "That task's request is not the person's own", 403)
        current = delegated_identity(c, who, "turn")
        if current.actor not in (who.actor, requester):
            raise Problem("on_behalf_of", "This run is for " + current.actor + "; it keeps that requester's rights", 403)
        if current.actor == who.actor:
            # A run with no requester lends a person's rights only when the keeper started it on its own schedule. A
            # Slack digest, a live meeting, a watcher or a task comment also start such runs, with words anyone there
            # wrote; those never carry a person's rights (Slack and comments are refused the same way above).
            turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                             (who.attempt_id,)).fetchone()
            start = H.message(c, turn["message_id"]) if turn else None
            refs = (start or {}).get("refs") or {}
            if (not start or start["from_actor"] != H.KEEPER or start["kind"] != "notice"
                    or refs.get("wake") not in FOLLOW_THROUGH_WAKES
                    or any(refs.get(k) for k in ("slack", "routing", "via", "assistant", "comment", "live_meeting"))):
                raise Problem("on_behalf_of", "This run started from words others wrote; act on the person's task in "
                              "their chat or the next daily update", 403)
        H.VIA.set("botops")
        return auth.identity_for_actor(c, requester)

    def botops_owns_task(path, body=None):
        """A note, comment or status on a task BotOps owns is BotOps' own work: it needs no one's rights and is BotOps'
        words, so it is never recorded as the person who asked (their rights still apply to everyone else's tasks).
        Closing is the exception: only the requester or a human closes, so a close keeps the person's rights.
        Its own task's worktrees are its own work too: the route checks the owning bot's repository access, which no
        one's rights change. So is a question or a subtask for a person under its own task (one ask, one card): the
        ask grants nothing by itself, whoever asked for the task."""
        body = body if isinstance(body, dict) else {}
        if path == "/api/v2/task-questions":
            return True                     # always the bot's own task, put to the person (media.task_question)
        person = None
        if path in ("/api/v2/tasks", "/api/v2/tasks/dry-run"):
            person = body.get("owner")
            ref = next((r.get("task") for r in body.get("relations") or []
                        if isinstance(r, dict) and r.get("kind") == "parent"), None)
            # Only a plain ask: no roles, goals, links or other relations reached with BotOps' rights for whoever asked.
            if (not ref or not person or len(body.get("relations") or []) != 1
                    or set(body) - {"title", "body", "owner", "relations", "due", "private", "on_behalf_of"}):
                return False
        else:
            found = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(?:comments|ask|links|worktrees(?:/attach)?))?", path)
            if not found or body.get("close"):
                return False
            ref = found.group(1)
        with store.read() as c:
            row = followed_task(c, ref)
            if person is not None and not H.is_human(H.resolve_actor(c, person) or ""):
                return False
        return bool(row and row["owner"] == "bot:" + BOTOPS)

    def manager_asker(c, who):
        """The manager bot whose chat message to BotOps started this run, as the identity BotOps acts as for a report's
        create, archive or restore (`Auth.report_lifecycle`): its actor is the manager and `via` is "botops", so its own
        token, which has no `via`, is never mistaken for it. None when a bot did not ask."""
        turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                         (who.attempt_id,)).fetchone() if who.attempt_id else None
        msg = H.message(c, turn["message_id"]) if turn else None
        if (not msg or msg.get("deleted_at") or msg["to_actor"] != "bot:" + BOTOPS or msg["kind"] not in ("say", "answer")
                or not H.is_bot(msg["from_actor"])):
            return None
        return replace(who, actor=msg["from_actor"], role="bot", via="botops", task_actor="")

    def act_for_requester(request, ref):
        """Apply the requester's identity before the route checks its normal permissions."""
        who = request.state.identity
        if who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "Only BotOps acts on a person's behalf", 403)
        if botops_act.lifecycle_call(request.method, request.url.path) and ref in ("turn", "default", ""):
            # A manager bot's report: BotOps acts as that manager, which the lifecycle routes check (Auth.report_lifecycle).
            with store.read() as c:
                asker = manager_asker(c, who)
            if asker is not None:
                return None, asker
        # Runs on an executor thread: the delegation record is read back here and set again for the route.
        token = H.DELEGATION.set(None)
        try:
            with store.read() as c:
                acting = delegated_identity(c, who, ref, carry=botops_act.carried_lends(request.method,
                                                                                            request.url.path))
            request.state.delegation = H.DELEGATION.get()
        finally:
            H.DELEGATION.reset(token)
        with store.read() as c:
            if acting.actor == who.actor:
                return None, None
            body = botops_act.parse_body(getattr(request, "_body", b"")) if request.method != "GET" else None
            if botops_act.secret_in_request(request.method, request.url.path, body):
                raise Problem("secret_in_request", botops_act.DETAIL_SECRET, 422)
            if H.is_human(acting.actor):
                card = person_card(c, request, acting, body)
                if card is not None:
                    return card, None
            # The ordinary route checks the requester's rights, including its own outbound send switch.
            return None, acting

    def person_card(c, request, acting, body):
        """BotOps acting for a person: a refusal for a route that hands a secret back, or the Confirm card in their BotOps
        chat for one that always needs their own tap (`botops_act.card_for_person`). None when the route runs as them."""
        method, path = request.method, botops_act.normalize(request.url.path)
        why = botops_act.person_only(method, path)
        if why:
            raise Problem("person_only", why, 403, extra=botops_act.refusal_for_person(
                path, "Do this yourself in " + settings.app_name, settings.public_url))
        body = {k: v for k, v in body.items() if k != "on_behalf_of"} if isinstance(body, dict) else {}
        rules = team_rules.load(c)

        def lowers():
            rest = path[len(botops_act.API):]
            if rest.startswith("usage/pools/"):
                scope = rest[len("usage/pools/"):]
                return usage_limits.pool_only_lowers(c, "company" if scope == "company" else "group:" + scope.split("/", 1)[-1], body)
            return usage_limits.only_lowers(c, rest[len("usage/limits/"):] if rest.startswith("usage/limits/") else None, body)
        # A Gateway URL change sends the saved key to the proposed address as a connection check.
        # Require the owner's tap whenever a key is saved, even when botops_direct is on. A key the server's
        # environment sets can't be changed here at all, so that save is refused at once rather than carded.
        gateway_put = method == "PUT" and path == botops_act.API + "providers/gateway"
        moved, shown, what, refusal = destination_card(c, acting, method, path, body)
        gateway_key_address_change = gateway_put and (bool(body.get("url")) or bool(moved)) and (
            gateway_connection.connected(c, settings) and not gateway_connection.env_key(settings))
        if not path or not botops_act.card_for_person(method, path, body, rules, lowers,
                                                       force_confirm=gateway_key_address_change or bool(moved)):
            return None
        # Told at once, not handed a card that fails on the tap.
        who_may = ("the owner" if botops_act.owner_only(method, path, body) and acting.role != "owner"
                   else "an owner or an admin" if botops_act.admin_only(method, path) and acting.role != "owner"
                   and not auth.bot_admin(acting) else "")
        if who_may:
            raise Problem("forbidden", "Only " + who_may + " can do this", 403, extra=botops_act.refusal_for_person(
                path, "Your role can't change this: ask " + who_may, settings.public_url))
        if refusal:
            raise refusal
        if aid := botops_act.automation_deleted(method, path):
            from . import automations
            automations.deletable(c, acting, aid)          # refused at once (404, 403, a built-in 409)
        with store.transaction() as w:
            card = propose_card(w, acting, method, path, body, botops_act.card_summary(method, path, body),
                                what=what, shown=shown)
        return JSONResponse(card)

    def destination_card(c, acting, method, path, body):
        """For BotOps acting for a person: (change, card lines, card title, refusal) when `body` moves saved credentials
        to another service (backend/destinations.py). It always waits for the person's own tap; until BotOps
        says which (`credentials` "keep" or "clear") and names the revision it read, it is refused at once (`refusal`,
        raised once the person's role is checked) with what the person must decide, so no card keeps or clears
        anything without their word. An address edit that moves nothing saved gets no change and no refusal, but still
        its card lines and title, so no card for either integration shows an address as typed. Else Nones."""
        from . import destinations, webhooks
        from .assistant import spoken
        if method == "PUT" and path == botops_act.API + "providers/gateway":
            moved, labels = gateway_connection.destination_change(c, body, settings), [gateway_connection.LABEL_KEY]
            current, integration, name = gateway_connection.revision(c), "gateway", gateway_client.LABEL
            old_url, new_url = gateway_connection.url(c, settings), gateway_client.base_url(str(body.get("url") or ""))
        elif method == "POST" and (hook := webhooks.UPDATE_PATH.fullmatch(path)):
            row, moved, labels = webhooks.pending_change(c, acting, hook.group(1), body, app.state.vault)
            current, integration, name = row["revision"], "webhook", row["name"]
            old_url, new_url = row["url"], body.get("url") or row["url"]
        else:
            return None, None, None, None
        # Whatever else the same request changes runs at the tap too, so the card names it (the address is shown
        # only as its safe destination, never as typed).
        also = [k for k in body if k not in ("url", "credentials", "expected_revision")]
        and_also = f", and change {', '.join(spoken(k) for k in also)}" if also else ""
        others = [{"field": k, "new": shown_value(body[k])} for k in also]
        if not moved:
            if "url" not in body:
                return None, None, None, None
            seen = destinations.shown(old_url, new_url)
            old, new = seen["old"]["address"], seen["new"]["address"] or "an address that is not a URL"
            what = f"Change {name}'s address to {new} ({seen['path']}; nothing saved moves to another service)"
            return None, [{"field": "destination", "old": old, "new": new}, {"field": "path", "new": seen["path"]},
                          *others], (what + and_also)[:300], None
        option = destinations.choice(body.get("credentials"))
        if not option:
            return moved, None, None, destinations.held(integration, moved, labels, current, name)
        if body.get("expected_revision") is None:
            return moved, None, None, Problem("expected_revision", "Changing where saved credentials go needs "
                                              "expected_revision: the revision you read", 422)
        if not isinstance(body["expected_revision"], int) or body["expected_revision"] != int(current):
            return moved, None, None, Problem("version_conflict", name + " changed since you read it; read it again", 409)
        old, new, listed = moved["old"]["address"], moved["new"]["address"], ", ".join(labels)
        what = (f"Send {name}'s saved {listed} to {new} instead of {old}" if option == destinations.KEEP
                else f"Move {name} to {new} and clear its saved {listed}")
        shown = [{"field": "destination", "old": old, "new": new},
                 *([{"field": "path", "new": moved["path"]}] if "path" in moved else []),
                 {"field": "credentials", "new": ("keep: " if option == destinations.KEEP else "clear: ") + listed},
                 *others]
        return moved, shown, (what + and_also)[:300], None

    def shown_value(value):
        """A short card value for a field that changes alongside a destination: a scalar as itself, a header list as
        its names (a secret's value is refused before any card), anything else by size."""
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value[:120]
        if isinstance(value, list) and all(isinstance(h, dict) and "name" in h for h in value):
            return ", ".join(str(h["name"])[:60] for h in value) or "none"
        return f"{len(value)} item{'s' if len(value) != 1 else ''}" if isinstance(value, (list, dict)) else "changed"

    def botops_run_identity(c, attempt_id):
        """Whose rights this BotOps run carries (the GitHub token's scope, backend/github_app.py): the person's or bot's
        it acts for, BotOps' own, or None when its start lends nobody's (someone else's comment)."""
        import contextvars
        row = c.execute("SELECT runner_id FROM attempts WHERE id=? AND bot=?", (attempt_id, BOTOPS)).fetchone()
        if not row:
            return None
        own = Identity("bot:" + BOTOPS, "bot", runner_id=row["runner_id"], attempt_id=attempt_id)
        try:
            # Its own context: resolving the run here records nothing as delegated.
            # A bot's hand-off (carried_request) lends no repository access: the token keeps that bot's scope.
            return contextvars.copy_context().run(delegated_identity, c, own, "turn")
        except Problem:
            return None
    app.state.botops_run_identity = botops_run_identity

    def propose_card(c, acting, method, path, body, summary, what=None, shown=None):
        """What always needs the requesting person's own click: a Confirm card in their chat with BotOps,
        and the answer BotOps reports back instead of asking them to go to Settings. `what`: the card's title, when the
        route that asks already said in plain words what confirming does (never a bot's words)."""
        room = rooms.chat_room(c, auth, acting, BOTOPS)
        card = create_proposal(c, settings, acting, room, BOTOPS, summary, method, path, body, what=what,
                               routes=app.routes, shown=shown)
        return {"proposed": True, "needs_confirm": True, **card,
                "detail": "This needs " + ((H.human(c, H.actor_id(acting.actor)) or {}).get("name") or "the person")
                + "'s own OK. A Confirm card is in their chat with BotOps; nothing has changed yet."}
    app.state.botops_propose = propose_card      # a route outside this file asking for the card (backend/repositories.py)

    def risky(who):
        """A delegated identity that has not been confirmed: BotOps acting for a person, before their click."""
        return who.via == "botops" and not who.confirmed

    @app.post("/api/v2/bots/{bot}/definition")
    def update_bot(request: Request, bot: str, body: M.BotDefinitionUpdate):
        who = request.state.identity
        def work(c):
            acting = delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who
            result = settings_admin.update_bot(c, acting, bot, body)
            if acting is not who:
                H.event(c, who.actor, "bot.definition_delegated", bot,
                        {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of})
            before, after = result.pop("previous_thread_mode"), result["thread_mode"]
            if before != after:
                if after == rooms.SHARED:
                    rooms.sync_shared_room(c, auth, bot, actor=who.actor, create=True)
                else:
                    rooms.archive_shared_room(c, who.actor, bot)
                reset_bot_sessions(c, bot)
            return result
        return mutate(request, body, settings_admin.as_report_manager(who, bot, "definition", work))

    @app.post("/api/v2/bots/{bot}/status")
    def status_set(request: Request, bot: str, body: M.StatusUpdate):
        who = request.state.identity
        def work(c):
            if who.actor != "bot:" + bot and not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "You cannot manage this bot", 403)
            from .shared_bots import declared, source_of
            source = source_of(declared(c, bot))
            if source and (H.bot(c, source) or {}).get("state") == "archived" and body.state in ("active", "paused", "quarantined"):
                raise Problem("original_archived", "Restore the original before changing its branch's status", 409)
            if body.state == "active" and (H.bot(c, bot) or {}).get("state") == "quarantined":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            if body.task_id:
                auth.task(c, who, body.task_id)
            if privacy.private_execution(c, who):
                sources = [tid for tid in privacy.attempt_tasks(c, who.attempt_id) if H.task_private(c, H.task(c, tid))]
                if body.task_id and body.task_id not in sources:
                    raise Problem("privacy", "Private execution status must keep its task provenance", 403)
                if not body.task_id and sources:
                    body.task_id = sources[0]
            return H.status_set(c, who.actor, bot, state=body.state, focus=body.focus, task_id=body.task_id)
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/quarantine/clear")
    def quarantine_clear(request: Request, bot: str, body: M.QuarantineClear):
        caller = request.state.identity
        def work(c):
            # A person's request in chat ("why is my content not unblocked?") is
            # enough; BotOps lifts it as that person, so an escape still needs a person's say-so.
            # BotOps never clears one on its own authority: it acts as the person who asked, and that person must
            # manage the bot.
            botops = caller.actor == H.bot_actor(H.FLEET_MAINTAINER)
            if botops and not body.on_behalf_of:
                raise Problem("on_behalf_of", "BotOps clears a quarantine only as the person who asked it: cite their message", 403)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if who is not caller:
                H.event(c, caller.actor, "quarantine.clear_delegated", bot,
                        {"on_behalf_of": who.actor, "message_id": body.on_behalf_of})
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "Only a person who manages this bot can clear quarantine", 403)
            if (H.bot(c, bot) or {}).get("state") != "quarantined":
                raise Problem("quarantined", "This bot is not quarantined", 409)
            settings_admin.ensure_activation(c, who, bot)
            # Stopped runs settle on their own (execution.auto_reconcile); they no longer hold a resume.
            return {"status": H.status_set(c, who.actor, bot, state="active", focus="",
                                            reason=(body.note or "").strip() or "Resumed")}
        return mutate(request, body, work)

    @app.get("/api/v2/inbox")
    def inbox(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            privacy.snapshot(c)
            rooms = {}

            def room_open(message):
                """A bot reads a message only in a conversation it may read: checked once per room, before the message's
                own privacy, so a backlog in rooms this turn was never handed costs a lookup each, not a provenance walk."""
                if who.role != "bot":
                    return True
                if message["conversation_id"] not in rooms:
                    try:
                        auth.conversation(c, who, message["conversation_id"])
                        rooms[message["conversation_id"]] = True
                    except Problem:
                        rooms[message["conversation_id"]] = False
                return rooms[message["conversation_id"]]

            def newest():
                """The unread backlog newest first, a page at a time: each page's rooms read together, then its messages
                in open rooms read together INBOX_LIMIT at a time, only as far as they are checked one by one."""
                for page in H.inbox_pages(c, who.actor, size=INBOX_PAGE):
                    if who.role == "bot":
                        H.prime_conversations(c, {m["conversation_id"] for m in page} - set(rooms))
                    candidates = [m for m in page if room_open(m)]
                    for at in range(0, len(candidates), INBOX_LIMIT):
                        part = candidates[at:at + INBOX_LIMIT]
                        privacy.prime_queued(c, H.prime_messages(c, [m["id"] for m in part]).values())
                        yield from part

            # A bot that never marks messages read can hold thousands: the newest INBOX_LIMIT readable ones are
            # checked and sent, oldest first as before, and `more_messages` says older ones are waiting. Paging stops
            # there, so the rest of the backlog is never read.
            kept, more = _first_visible(newest(), lambda m: privacy.message_readable(c, privacy.actor(who), m))
            result = {"actor": who.actor, "messages": H.mark_answered(c, kept[::-1]), "more_messages": more}
            result["tasks"], result["more_tasks"] = _first_visible(H.tasks(c, owner=who.actor, status=H.ACTIVE_STATUSES),
                                                                   lambda t: privacy.task_readable(c, who, t))
            result["approvals"] = []
            result["notices"] = [m for m in result["messages"] if m["kind"] == "notice"]
            return result

    @app.get("/api/v2/answers")
    def answers(request: Request, ids: str):
        who = request.state.identity
        with store.read() as c:
            mids = ids.split(",")
            if len(mids) > 20:
                raise Problem("limit", "Read at most 20 correlated answers at a time", 422)
            for mid in mids:
                msg = H.message(c, mid)
                if not msg or msg["from_actor"] != who.actor:
                    raise Problem("forbidden", "You may read only answers to your own questions", 403)
                auth.conversation(c, who, msg["conversation_id"])
                privacy.require_message(c, who, msg)
            answers = H.answers_to(c, mids)
            privacy.require_payload(c, who, answers)
            return answers

    @app.post("/api/v2/messages/{mid}/ack")
    def acknowledge(request: Request, mid: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            msg = H.message(c, mid)
            if not msg or msg["to_actor"] != who.actor:
                raise Problem("forbidden", "You may acknowledge only messages addressed to you", 403)
            auth.conversation(c, who, msg["conversation_id"])
            privacy.require_message(c, who, msg)
            H.mark_read(c, who.actor, mid)
            H.mark_delivered(c, who.actor, mid)
            # A waiting CLI has already consumed the answer in its current turn.
            if msg["kind"] == "answer":
                c.execute("UPDATE jobs SET state='completed' WHERE message_id=? AND state='queued'", (mid,))
            return {"read": True}
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/history")
    def history(request: Request, bot: str, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            auth.require_read(c, who, bot)
            inputs = privacy.status_inputs(c, [bot])[bot]
            return [privacy.status(c, who, row, **inputs) for row in H.status_history(c, bot, since=views.since_time(since))]

    @app.get("/api/v2/bots/{bot}/turns")
    def turns(request: Request, bot: str, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        # A snapshot, so the page's messages and runs are read together and stay cached for each check.
        with store.read_transaction() as c:
            auth.require_read(c, who, bot)
            since_at = views.since_time(since)
            found = list(H.turns(c, bot, since=since_at))
            stored = H.prime_messages(c, [turn["message_id"] for turn in found])
            privacy.prime_messages(c, list(stored.values()))
            privacy.prime_attempts(c, [turn["id"] for turn in found])
            views.prime_attempt_details(c, [turn["id"] for turn in found])
            result = []
            for turn in found:
                msg = H.message(c, turn["message_id"])
                if not msg:
                    continue
                try:
                    auth.run_conversation(c, who, bot, msg)
                    privacy.require_message(c, who, msg)
                    privacy.require_attempt(c, who, turn["id"])
                except Problem:
                    continue
                result.append({**turn, **views.attempt_details(c, turn)})
            result.extend(views.failed_attempts(c, who, auth, bot, since_at))
            result.sort(key=lambda row: row.get("started") or "", reverse=True)
            from .usage_records import ran_on
            made = ran_on(c, [row["id"] for row in result])
            return [{**row, "ran_on": made[row["id"]]} for row in result]

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
                unchanged = (provenance_clock.current(c) == clock if clock is not None else
                             snapshot.execute("PRAGMA data_version").fetchone()[0] == version)
                return execution.heartbeat(c, request.state.identity, body,
                                           live_tasks=selected if unchanged else None)
            result = mutate(request, body, work)
        # The roster, providers and repositories behind the assignments are read from a snapshot after the commit,
        # not under the write lock: a heartbeat keeps no receipt (store.is_poll), so nothing replays them.
        with store.read_transaction() as c:
            result["assignments"] = execution.assigned(c, request.state.identity)
            privacy.require_payload(c, request.state.identity, {"assignments": result["assignments"]})
        return result

    @app.get("/api/v2/runners/desired")
    def desired_runner_release(request: Request):
        with store.read() as c:
            execution.runner(c, request.state.identity)
        # `screen_v1`: this server takes the capability and `readiness.screen` (an older one refuses the heartbeat).
        # `limit_resets_at`: a run's result may say when its usage limit resets (an older one refuses the field).
        # `usage_requests`: a run's usage may carry each model request's tokens (an older one refuses the field).
        # `usage_request_main`: ... and a fourth count marking the bot's own requests (an older one refuses it).
        # `run_pushes`: a run's result may list the pushes its task worktrees made (an older one refuses the field).
        # `run_rotation`: a run's result may say it started a fresh session and why (an older one refuses the field).
        # `run_session`: a run's result may say whether it resumed its session or started a new one, and why (an older
        # one refuses the field). The claim's config may carry `compact_first` for a runner that sends it.
        # `load_snapshot`: start and completion may carry an instantaneous load reading.
        # `attempt_thread`: a running attempt may name its thread after its start (`attempts/{aid}/thread`), once the
        # harness confirmed a new session (an older one has no such route).
        # `capacity_continue`: a run the model's capacity stopped after it acted may say its thread was kept
        # (`continues`), and a claim may name that thread (`continue_thread`) for the next turn to continue.
        return {**runner_versions.desired(), "features": {"task_files_multipart": True, "runner_commit": True,
                                                          "screen_v1": True, "limit_resets_at": True,
                                                          "usage_requests": True, "usage_request_main": True,
                                                          "run_pushes": True, "run_rotation": True, "run_session": True,
                                                          "run_diagnostics": True, "load_snapshot": True,
                                                          "attempt_thread": True, "runner_drain": True,
                                                          "capacity_continue": True, "source_continue": True}}

    @app.get("/api/v2/runners/assignments")
    def assigned(request: Request):
        with store.read_transaction() as c:
            return execution.assigned(c, request.state.identity)

    @app.get("/api/v2/runners/eligible")
    def eligible(request: Request):
        with store.read() as c:
            runner = execution.runner(c, request.state.identity)
            rows = c.execute(
                "SELECT b.bot,b.team,b.operator,b.config_json,x.state,"
                "coalesce(a.generation,0) AS generation,a.runner_id "
                "FROM bot_config b JOIN bots x ON x.slug=b.bot LEFT JOIN assignments a ON a.bot=b.bot "
                "WHERE b.operator=? ORDER BY b.bot", (runner["operator"],)).fetchall()
            result = []
            company = Providers.load(c, settings)
            for row in rows:
                value = dict(row)
                from .shared_bots import follow
                value["config"] = Providers.fill(company, follow(c, row["bot"], json.loads(value.pop("config_json"))))
                value["repository"] = bot_repository(c, settings, row["bot"])
                result.append(value)
            return result

    @app.post("/api/v2/runners/adopt")
    def adopt(request: Request, body: M.Adopt):
        who = request.state.identity
        def work(c):
            runner = execution.runner(c, who)
            row = c.execute("SELECT operator FROM bot_config WHERE bot=?", (body.bot,)).fetchone()
            if not row or row["operator"] != runner["operator"]:
                raise Problem("forbidden", "This bot is outside your enrolled operator's scope", 403)
            # Enrollment delegates hosting of the operator's assigned team, never human data access.
            operator = Identity("human:" + runner["operator"], "human")
            result = execution.assign(c, operator, body.bot,
                                      M.Assignment(runner_id=who.runner_id, expected_generation=body.expected_generation))
            H.event(c, who.actor, "runner.adopt", body.bot)
            return result
        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/restart")
    def restart_runner(request: Request, rid: str, body: M.Empty):
        """Restart a computer's runner on the latest code. The runner pulls
        main when that is safe and restarts once no turn is running, so nothing is cut off."""
        who = request.state.identity
        def work(c):
            runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not runner or not (auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["operator"]):
                raise Problem("forbidden", "You cannot restart this runner", 403)
            c.execute("UPDATE runners SET restart_requested=? WHERE id=?", (H.now(), rid))
            H.event(c, who.actor, "runner.restart", rid)
            return {"requested": True, "running": views.runner_busy(c, rid)}
        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/inbox-only")
    def inbox_only(request: Request, rid: str, body: M.InboxOnly):
        """Keep this computer for inbox bots, or stop (backend/inbox_isolation.py)."""
        who = request.state.identity
        def work(c):
            runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not runner or not (auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["operator"]):
                raise Problem("forbidden", "You cannot change this computer", 403)
            people = views.roster(c)
            if body.enabled and (others := [b for b in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (rid,))
                                           .fetchall() if not P.inbox_person(b[0], people)]):
                raise Problem("inbox_isolation", "Move these bots to another computer first: "
                              + ", ".join(b[0] for b in others), 409)
            inbox_isolation.set_inbox_only(c, rid, body.enabled, people)
            H.event(c, who.actor, "runner.inbox_only", rid, {"enabled": body.enabled})
            return {"enabled": body.enabled}
        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/logins")
    def start_login(request: Request, rid: str, body: M.LoginStart):
        return mutate(request, body, lambda c: model_login.start(
            c, request.state.identity, rid, body.runtime, body.profile, auth=auth, switch=body.switch, to=body.to))

    # Sign a login out (the computer's own, or a named subscription's); it is read back like a sign-in.
    @app.post("/api/v2/runners/{rid}/logouts")
    def start_logout(request: Request, rid: str, body: M.LogoutStart):
        return mutate(request, body, lambda c: model_login.sign_out(
            c, request.state.identity, rid, body.runtime, body.profile, auth=auth))

    # The bots a sign-out would stop, for the confirmation.
    @app.get("/api/v2/runners/{rid}/logouts/bots")
    def logout_bots(request: Request, rid: str, runtime: str, profile: str = ""):
        with store.read() as c:
            return model_login.affected_bots(c, request.state.identity, rid, runtime, profile, store.settings,
                                             auth=auth)

    # Take a named subscription off a computer (refused, naming them, while bots run on it); read back like a sign-in.
    @app.post("/api/v2/runners/{rid}/removals")
    def start_removal(request: Request, rid: str, body: M.RemovalStart):
        return mutate(request, body, lambda c: model_login.remove(
            c, request.state.identity, rid, body.runtime, body.profile, store.settings, auth=auth))

    # A signed-out computer login takes the company's shared key again.
    @app.post("/api/v2/runners/{rid}/shared-key")
    def use_shared_key(request: Request, rid: str, body: M.SharedKeyUse):
        if body.credential:
            return mutate(request, body, lambda c: model_login.attach_shared_key(
                c, request.state.identity, rid, body.runtime, body.credential, auth=auth, vault=app.state.vault))
        return mutate(request, body, lambda c: model_login.use_shared_key(
            c, request.state.identity, rid, body.runtime, auth=auth))

    # Clear key / Replace key (backend/computer_keys.py). What signs it in, and for the company's shared key every
    # computer it serves: the "this computer only / all of them" ask. Previews only.
    @app.get("/api/v2/runners/{rid}/keys")
    def computer_key_info(request: Request, rid: str, runtime: str):
        with store.read() as c:
            return model_login.key_info(c, request.state.identity, rid, runtime, auth=auth, vault=app.state.vault)

    @app.post("/api/v2/runners/{rid}/keys/clear")
    def clear_computer_key(request: Request, rid: str, body: M.KeyClear):
        return mutate(request, body, lambda c: model_login.clear_key(
            c, request.state.identity, rid, body.runtime, body.scope, auth=auth))

    # The key goes to the vault, never into the login relay; the vault's own write path keeps it out of the request
    # receipt (a keyed fingerprint, as for Credentials).
    @app.post("/api/v2/runners/{rid}/keys")
    def replace_computer_key(request: Request, rid: str, body: M.KeyReplace):
        who = request.state.identity
        vault = app.state.vault
        if body.share and body.switch:
            raise Problem("switch", "Switch to API key gives this computer a key of its own; share a key from Add "
                          "connection instead", 422)
        return vault.change(who, request.url.path, request.headers.get("idempotency-key"), body,
                            lambda c, w: None,
                            lambda c: model_login.share_key(c, who, rid, body.runtime, body.secret.get_secret_value(),
                                                            body.env, body.share, auth=auth, vault=vault)
                            if body.share else
                            model_login.replace_key(c, who, rid, body.runtime, body.secret.get_secret_value(),
                                                    body.env, body.scope, auth=auth, vault=vault, switch=body.switch))

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
        return mutate(request, body, lambda c: model_login.submit_code(
            c, request.state.identity, rid, lid, body.code, auth=auth))

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
        return mutate(request, body, lambda c: (execution.runner(c, who),
                                                model_login.report(c, who.runner_id, lid, body,
                                                                   store.settings))[1])

    @app.post("/api/v2/runners/{rid}/harness-actions")
    def harness_action(request: Request, rid: str, body: M.HarnessAction):
        return mutate(request, body, lambda c: harness_actions.request(
            c, request.state.identity, rid, body.harness, body.action))

    @app.post("/api/v2/runners/{rid}/harness-choices")
    def harness_choice(request: Request, rid: str, body: M.HarnessChoice):
        return mutate(request, body, lambda c: harness_actions.choose(
            c, request.state.identity, rid, body.harness, body.enabled, settings))

    @app.get("/api/v2/runner-harness-actions")
    def runner_harness_actions(request: Request):
        who = request.state.identity
        with store.read() as c:
            execution.runner(c, who)
            if not harness_actions.sweep_due(c):
                return {"actions": harness_actions.pending(c, who.runner_id, swept=False),
                        "choices": harness_actions.choices(c, who.runner_id)}
        with store.transaction() as c:
            execution.runner(c, who)
            return {"actions": harness_actions.pending(c, who.runner_id),
                    "choices": harness_actions.choices(c, who.runner_id)}

    @app.post("/api/v2/runner-harness-actions/{aid}/report")
    def report_harness_action(request: Request, aid: str, body: M.HarnessActionReport):
        who = request.state.identity
        return mutate(request, body, lambda c: (execution.runner(c, who),
                                                harness_actions.report(c, who.runner_id, aid, body))[1])

    @app.post("/api/v2/runners/{rid}/revoke")
    def revoke(request: Request, rid: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            runner = c.execute("SELECT * FROM runners WHERE id=?", (rid,)).fetchone()
            if not runner or not (auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["operator"]):
                raise Problem("forbidden", "You cannot revoke this runner", 403)
            c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), rid))
            c.execute('DELETE FROM registry_metadata WHERE key=?', ('computer-repositories:' + rid,))
            app_delivery.runner_revoked(c, who.actor, rid)
            H.event(c, who.actor, "runner.revoked", rid)
            return {"revoked": True}
        result = mutate(request, body, work)
        screen_sessions.set_mode(rid, "off", "computer removed")
        # A removed computer is no one's browser exit, and its bots' connections through others close.
        screen_sessions.push_exit_policy(rid)
        screen_sessions.exit_close(exit=rid, reason="computer removed")
        with store.read() as c:
            removed_bots = [r[0] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (rid,))]
        for bot in removed_bots:
            screen_sessions.push_bot_generation(bot, "computer removed")
        return result

    def closed_computer(c, who, bot, runner_id):
        """An admin placing a member's bot on a computer that is neither the member's own nor open to members'
        bots: allowed, but through BotOps only with the admin's own click."""
        runner = c.execute("SELECT operator,accepts_member_bots FROM runners WHERE id=? AND revoked_at IS NULL",
                           (runner_id,)).fetchone()
        config = c.execute("SELECT operator FROM bot_config WHERE bot=?", (bot,)).fetchone()
        return bool(runner and config and not runner["accepts_member_bots"] and runner["operator"] != config["operator"]
                    and auth.member_bot(c, bot) and auth.bot_admin(who))

    def placement_summary(c, bot, runner_id):
        runner = c.execute("SELECT label,accepts_member_bots FROM runners WHERE id=?", (runner_id,)).fetchone()
        return ("Place " + ((H.bot(c, bot) or {}).get("display_name") or bot) + " on "
                + (runner["label"] if runner else "a computer")
                + (", a computer that takes members' bots" if runner and runner["accepts_member_bots"]
                   else ", a computer that does not take members' bots"))

    @app.post("/api/v2/bots/{bot}/assignment")
    def assign(request: Request, bot: str, body: M.Assignment):
        caller = request.state.identity
        def work(c):
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if risky(who) and closed_computer(c, who, bot, body.runner_id):
                return propose_card(c, who, "POST", "/api/v2/bots/" + bot + "/assignment",
                                    body.model_dump(exclude={"on_behalf_of"}), placement_summary(c, bot, body.runner_id))
            return execution.assign(c, who, bot, body)
        return mutate(request, body, work)

    def find_computer(c, name):
        """A computer by id or label (any case), or a refusal that lists the ones there are."""
        rows = c.execute("SELECT id,label FROM runners WHERE revoked_at IS NULL ORDER BY label").fetchall()
        hit = [r for r in rows if r["id"] == name or r["label"].strip().lower() == str(name).strip().lower()]
        if len(hit) != 1:
            raise Problem("not_found", ("No computer is called " + name if not hit else "More than one computer is called " + name)
                          + ". The computers: " + (", ".join(r["label"] for r in rows) or "none yet"), 404)
        return hit[0]

    def history_on_github(bot):
        """Before the write (GitHub is never called while holding it): for a never-placed bot built on a computer,
        whether GitHub holds its history yet (True, False, or None when it cannot be learned). None for any other bot."""
        with store.read() as c:
            if not built_on.computer(c, bot):
                return None
            repository = bot_repository(c, settings, bot)
        service = getattr(app.state, "github_app", None)
        return service.repository_history(repository) if service and repository else None

    def place_now(c, who, bot, computer="", history=None):
        """`hub bot place`: on the computer named, or the one the company's own rule picks (backend/placement.py).
        A new bot whose history is only on the computer that built it goes there first (`history` is not True): that
        computer publishes it to GitHub, and from then on it can move like any other."""
        settings_admin._manager(c, who, bot)
        row = H.bot(c, bot)
        if not row:
            raise Problem("not_found", "Bot not found", 404)
        current = c.execute("SELECT a.*,r.label FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?",
                            (bot,)).fetchone()
        wanted = find_computer(c, computer) if computer else None
        if current and (not wanted or wanted["id"] == current["runner_id"]):
            return {"bot": bot, "computer": current["label"], "placed": False, "already": True}
        from .agents import external_harness
        if external_harness(c, bot):
            raise Problem("harness", "This bot is run by an external agent; it has no computer to place it on", 422)
        built = built_on.computer(c, bot) if history is not True else None
        if built and wanted and wanted["id"] != built["id"]:
            raise Problem("history_elsewhere",
                          f"{bot}'s repository was built on {built['label']} and "
                          + ("Tico could not check GitHub's copy" if history is None else "is not on GitHub yet")
                          + f", so {wanted['label']} could not get it. Put it on {built['label']} first (`hub bot go-live {bot} --computer "
                          f"\"{built['label']}\"`): {built['label']} publishes it to GitHub, and then it can move", 409)
        options = [wanted] if wanted else [built] if built else placing.candidates(c, auth, bot)
        if not options:
            raise Problem("no_computer", "No computer can take this bot yet: add one, or ask an admin to open one to members' bots", 409)
        generation = current["generation"] if current else 0
        refusal = None
        for runner in options:
            if risky(who) and closed_computer(c, who, bot, runner["id"]):
                return propose_card(c, who, "POST", "/api/v2/bots/" + bot + "/assignment",
                                    {"runner_id": runner["id"], "expected_generation": generation},
                                    placement_summary(c, bot, runner["id"]))
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
        raise refusal

    @app.get("/api/v2/fleet/check")
    def fleet_check(request: Request):
        """What is wrong with the bots this person may see, most urgent first, each with its one fix."""
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:      # one snapshot: company-wide reads are shared by every bot's row
            result = fleet_check_module.check(c, who, auth, settings)
            result["computers"] = computer_rows(c, who)
            result["services"] = views.team_services(c, who, auth)
            from . import health
            from .onboarding import config_view
            checks = health.view(c, who, settings, auth, getattr(app.state, "github_app", None), config_view(c, settings, who))["checks"]
            result["checks"] = checks
            for check in checks:
                if check["status"] not in ("warn", "bad", "unknown") and not check.get("items"):
                    continue
                items = check.get("items") or [{"bot": "", "name": check["label"], "text": check["summary"],
                                                "fixes": check["fixes"]}]
                for item in items:
                    fixes = item.get("fixes") or []
                    result["issues"].append({**item, "kind": check["id"],
                                             "severity": "high" if check["status"] == "bad" else "low" if check["status"] == "info" else "medium",
                                             "fix": item.get("fix") or "; ".join(
                                                 f["label"] + (": " + f["href"] if f["href"] else "") for f in fixes)})

            result["issues"].sort(key=lambda i: (fleet_check_module.ORDER[i["severity"]], i["name"].lower(), i["kind"]))
            result["counts"] = {level: sum(i["severity"] == level for i in result["issues"])
                                for level in fleet_check_module.ORDER}
            return result

    def computer_rows(c, who):
        from .performance import load_statuses
        from . import usage_limits
        from .start_places import summaries, describe as describe_turns
        online = {r["id"] for r in _online_runners(c)}
        everyone = who.role == "owner" or auth.bot_admin(who)
        mine = H.actor_id(who.actor)
        readable = auth.bot_accesses(c, who)
        # One pass for every computer's bots (not one SELECT per runner).
        placed, _ = views.assignments_by_runner(c)
        rows = []
        # One Default for the whole listing: each computer's summary reuses pools/groups/spend memos.
        limits = usage_limits.company(c)
        runners = list(c.execute("SELECT * FROM runners WHERE revoked_at IS NULL ORDER BY label"))
        # One pass for every computer's turn counts and load (not one summary and one load per computer).
        turn_counts = summaries(c, [r["id"] for r in runners], settings, limits)
        loads = load_statuses(c, [r["id"] for r in runners])
        for r in runners:
            counts = turn_counts[r["id"]]
            turns = {"turn_counts": counts, "turn_summary": describe_turns(counts)}
            detailed = everyone or r["operator"] == mine or r["accepts_member_bots"]
            if not detailed:
                if who.role == "bot":
                    # Discovery and load do not grant access to repositories, logs or operator identity.
                    rows.append({"id": r["id"], "label": r["label"], "online": r["id"] in online,
                                 "platform": r["platform"] or "", "accepts_member_bots": False,
                                 **loads[r["id"]], **turns})
                continue
            bots = [bot for bot in placed.get(r["id"]) or [] if (readable.get(bot) or {}).get("see")]
            rows.append({"id": r["id"], "label": r["label"], "online": r["id"] in online,
                         "platform": r["platform"] or "", "operator": r["operator"],
                         "accepts_member_bots": bool(r["accepts_member_bots"]), "bots": bots,
                         "turns_at_once": r["turns_at_once"], "turn_limit": start_places.turn_limit(r),
                         **screen_listing(r), **performance_listing(r), **views.computer_details(c, r, who, auth),
                         **loads[r["id"]], **turns})
        return rows

    @app.get("/api/v2/computers")
    def computers(request: Request):
        """The computers a person may put a bot on; company bots can discover every computer's load.
        Newly discoverable computers expose no repositories, logs or operator identity."""
        who = request.state.identity
        auth.domain(who)
        # One snapshot, so the per-computer lookups share read_cache across every computer.
        with store.read_transaction() as c:
            return {"computers": computer_rows(c, who), "services": views.team_services(c, who, auth),
                    "services_scope": "team"}

    @app.post("/api/v2/bots/{bot}/place")
    def place_bot(request: Request, bot: str, body: M.BotPlace):
        who = request.state.identity
        history = history_on_github(bot)
        return mutate(request, body, lambda c: place_now(c, who, bot, body.computer, history))

    GO_LIVE_PENDING = pending_go_lives.PREFIX

    def finish_go_live(c, who, bot, setup):
        """What going live does once the bot's computer has its repository: active, its setup started with the
        person, and its first routine on. Shared by the request itself and by the readiness report that completes a
        go-live that was waiting for the repository."""
        row = H.bot(c, bot)
        if row["state"] == "quarantined":
            raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
        activated = row["state"] != "active"
        if activated:
            settings_admin.update_bot(c, who, bot, M.BotDefinitionUpdate(
                status="active", expected_revision=settings_admin._config(c, bot)["revision"]))
        started = False
        config = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (bot,)).fetchone()
        if setup and config and is_parked(config["onboarding_state"]) and who.role in ("owner", "human"):
            send(c, who, M.MessageCreate(to="bot:" + bot, text="Let's set you up."))
            started = True
        # Going live turns its first routine on too: nobody approves it separately.
        armed = onboarding.arm_first_routine(c, who, bot)
        c.execute("DELETE FROM registry_metadata WHERE key=?", (GO_LIVE_PENDING + bot,))
        return {"activated": activated, "setup_started": started, "routine_armed": armed}

    def wait_for_repository(c, who, bot, placed, body, github_asked=True):
        """Keep the placement and remember the go-live: the computer is told about the bot on its next heartbeat,
        clones the repository, and the readiness report that says it has it finishes the go-live
        (`finish_pending_go_live`). It expires after backend/go_live.py EXPIRY_DAYS."""
        computer = placed.get("computer") or "its computer"
        runner = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                  (GO_LIVE_PENDING + bot, encode({
                      "bot": bot, "runner_id": runner["runner_id"] if runner else "", "computer": computer,
                      "actor": who.actor, "role": who.role, "email": who.email, "via": who.via,
                      "setup": body.setup,
                      "requested_at": H.now()})))
        H.event(c, who.actor, "bot.go_live_waiting", bot, {"computer": computer, "github_asked": github_asked})
        message = (f"Waiting for {computer} to get the repository. Tico finishes going live by itself "
                   f"when {computer} has it; nothing needs to run again")
        if not github_asked:
            message += ". GitHub could not be asked; if the repository doesn't exist, this won't finish"
        return {"bot": bot, "state": "waiting_for_repository", "bot_state": H.bot(c, bot)["state"],
                "computer": computer, "placed": placed.get("placed", False), "activated": False, "setup_started": False,
                "github_checked": github_asked,
                "message": message}

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
                    pending_go_lives.fail(c, key, bot, pending, "expired", pending_go_lives.expire_notice(bot, pending))
                    c.execute("RELEASE go_live_finish")
                    continue
                report = reports.get(bot)
                if (not isinstance(report, dict) or report.get("repository_present") is not True
                        or not c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                         (bot, runner_id)).fetchone()):
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
                who = Identity(pending.get("actor") or "", role, pending.get("email") or "", via=pending.get("via") or "")
                validate_identity(c, who)
                done = finish_go_live(c, who, bot, bool(pending.get("setup", True)))
                H.event(c, who.actor, "bot.go_live_finished", bot, {"computer": pending.get("computer") or "", **done})
                c.execute("RELEASE go_live_finish")
            except Exception as exc:
                c.execute("ROLLBACK TO go_live_finish")
                c.execute("RELEASE go_live_finish")
                where = pending.get("computer") or "its computer"
                if isinstance(exc, Problem):
                    code, notice = exc.code, f"{bot} has its repository on {where}, but going live could not finish: {str(exc.detail).rstrip('.')}."
                else:
                    # A fault in Tico, not something the person did: its text stays in the log.
                    logging.getLogger("tico.go_live").exception("Finishing the go-live of %s failed", bot)
                    telemetry.capture("go_live_finish", exc, 500)
                    code, notice = "finish_error", f"Going live for {bot} could not finish because of an error in Tico."
                c.execute("SAVEPOINT go_live_fail")
                try:
                    pending_go_lives.fail(c, key, bot, pending, code, notice)
                    c.execute("RELEASE go_live_fail")
                except Exception:
                    c.execute("ROLLBACK TO go_live_fail")
                    c.execute("RELEASE go_live_fail")
                    logging.getLogger("tico.go_live").exception("Recording the failed go-live of %s failed", bot)

    execution.readiness_reported = finish_pending_go_live

    def waiting_for_permission(bot, wait):
        """The refusal for a bot whose repository could not be created because the GitHub App may not create
        repositories (backend/repo_waits.py): it points at the owner's Needs-you task with the two fixes, not at
        `hub bot repo-create`, which would only hit the same missing permission. No placement is kept."""
        task_id = str(wait.get("task_id") or "")
        url = f"{settings.public_url}/#/task/{task_id}" if task_id and settings.public_url else ""
        where = (f"the owner has a Needs you task with the two fixes ({task_id}{', ' + url if url else ''})" if task_id
                 else "the owner is asked in Needs you to create it or let BotOps create repositories")
        return Problem("repository_waiting_for_permission",
                       f"{bot} is waiting for its GitHub repository" + (f" {wait['repository']}" if wait.get("repository") else "") + f": {where}. "
                       f"Once either fix is in, Tico creates or finds it; then run `hub bot go-live {bot}` again", 409,
                       extra={"task_id": task_id, **({"task_url": url} if url else {})})

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
        github, missing = repository_on_github(bot)
        history = history_on_github(bot)

        def work(c):
            # An external agent (a Hermes profile) has a credential, not a computer.
            placed = ({"computer": None, "placed": False} if agents.external_harness(c, bot)
                      else place_now(c, who, bot, body.computer, history))
            if placed.get("proposed"):
                return placed                     # a computer that needs the person's own click: nothing else yet
            row = H.bot(c, bot)
            if row["state"] == "quarantined":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            building = False
            if not agents.external_harness(c, bot):
                if not repository_present(c, bot):
                    declared = onboarding._declared(c, bot)
                    if settings_admin.computer_builds_repository(c, bot):
                        building = True
                    elif github == "present":
                        return wait_for_repository(c, who, bot, placed, body)
                    elif (permission_wait := repo_waits.waits(c).get(bot)):
                        raise waiting_for_permission(bot, permission_wait)
                    elif declared.get("template"):
                        if not H.bot(c, "botops"):
                            raise Problem("repository_missing", "Its repository is not built yet. Add BotOps to build it", 409)
                        build = onboarding.attach_template(c, who, bot, declared["template"],
                                                           declared.get("instructions") or "", queue_build=True)
                        return {"bot": bot, "state": row["state"], **placed, **build, "building": True,
                                "setup_started": False, "note": "BotOps is building its repository; watch setup_task_id"}
                    elif missing is not None:
                        raise Problem("repository_missing", missing.detail.rstrip(".")
                                      + f". Then run `hub bot go-live {bot}` again", 409)
                    elif bot_repository(c, settings, bot):
                        # GitHub could not be asked: the computer tries the clone and says why if it cannot.
                        return wait_for_repository(c, who, bot, placed, body, github_asked=False)
                    else:
                        raise Problem("repository_missing", "It has no repository yet. Ask BotOps to build it "
                                      f"(`hub bot repo-create {bot}`), then run `hub bot go-live {bot}` again", 409)
            done = finish_go_live(c, who, bot, body.setup)
            return {"bot": bot, "state": H.bot(c, bot)["state"], "computer": placed["computer"], "placed": placed["placed"],
                    **done,
                    **({"building": True, "note": "The computer is building its repository; queued work starts when it is ready"}
                       if building else {})}
        result = mutate(request, body, work)
        if isinstance(result, dict) and result.get("state") == "waiting_for_repository":
            return JSONResponse(result, status_code=202)
        return result

    @app.post("/api/v2/runners/{rid}/turns-at-once")
    def runner_turns_at_once(request: Request, rid: str, body: M.RunnerTurnsAtOnce):
        """How many turns a computer runs at once; null is automatic, its own capacity. A computer that cannot take a
        raised number (no turns_at_once_v1) runs at most the capacity it reports. Owners and admins."""
        who = request.state.identity
        def work(c):
            admin_only(who, "sets how many turns a computer runs at once")
            row = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not row:
                raise Problem("not_found", "Computer not found", 404)
            c.execute("UPDATE runners SET turns_at_once=? WHERE id=?", (body.turns_at_once, rid))
            # The computer's next heartbeat reads the new limit (runner-rs events.rs: a "capacity" change).
            c.execute("INSERT INTO changes(topic,kind,subject_id,at) VALUES('runner','capacity',?,?)", (rid, H.now()))
            H.event(c, who.actor, "runner.turns_at_once", rid,
                    {"before": row["turns_at_once"], "after": body.turns_at_once})
            return {"turns_at_once": body.turns_at_once,
                    "turn_limit": start_places.turn_limit({**dict(row), "turns_at_once": body.turns_at_once})}
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

    # An external agent (a Hermes profile) is a bot with a standing credential instead of a
    # computer: a person mints it here, the agent's box heartbeats with it, and the same token
    # is the bearer for the MCP endpoint and the `hub` CLI (docs/hermes-agents.md).
    @app.post("/api/v2/bots/{bot}/agent-credential")
    def agent_credential(request: Request, bot: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            personal_tokens.no_minting(who, "Create an agent credential")
            settings_admin._manager(c, who, bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            issued = agents.issue_credential(c, who, bot)
            if who.via == "botops":
                # A token never reaches BotOps's model. The old one has stopped; the profile pairs again and the
                # person's approval (BotOps can give it) hands the new one to the profile.
                return {"bot": bot, "harness": issued["harness"], "created": issued["created"], "rotated": True,
                        "detail": "The previous credential has stopped working. The token is not shown here: run "
                                  "`python3 hermes_agent.py pair` on the profile's computer and approve its code."}
            # Machine credentials bypass the person sign-in at the runner hostname, the same
            # address runners enroll at; the public one would answer with the login page.
            return {**issued, "setup": agents.setup_snippet(settings.runner_url, bot, issued["token"],
                                                             issued["harness"])}
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/agent-credential/revoke")
    def agent_credential_revoke(request: Request, bot: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            settings_admin._manager(c, who, bot)
            return agents.revoke_credential(c, who, bot)
        return mutate(request, body, work)

    # A person's bots on another platform (Grok Bot, Dots), synced in by the agent itself with the
    # person's own sign-in: created under them on the chart, transcripts copied, instructions kept.
    def external_sync_run(request, body):
        # Images are fetched before the write, never while holding it, and only for a sync that would be accepted.
        with store.read() as c:
            external_sync.precheck(c, settings_admin, request.state.identity, body)
        images = external_sync.prefetch_images(store, app.state.blobs, body,
                                               person=H.actor_id(request.state.identity.actor))
        return mutate(request, body, lambda c: external_sync.sync(c, auth, settings_admin,
                                                                  request.state.identity, body, images))

    @app.post("/api/v2/external/sync")
    def external_agent_sync(request: Request, body: external_sync.ExternalSync):
        return external_sync_run(request, body)

    @app.post("/api/v2/grokbot/sync")          # older Grok routines
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
        return mutate(request, body, lambda c: agents.approve_pairing(c, who, settings_admin._manager, body.code, body.bot))

    @app.post("/api/v2/agents/pairings/decline")
    def agent_pairing_decline(request: Request, body: M.AgentPairingDecline):
        who = request.state.identity
        return mutate(request, body, lambda c: agents.decline_pairing(c, who, body.code))

    @app.post("/api/v2/bots/{bot}/owners")
    def owners(request: Request, bot: str, body: M.BotOwners):
        who = request.state.identity
        def work(c):
            settings_admin._manager(c, who, bot)
            config = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if not config:
                raise Problem("not_found", "Bot not found", 404)
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before saving owners", 409)
            owner_ids = list(dict.fromkeys(body.owners))
            rows = [H.human(c, owner) for owner in owner_ids]
            missing = [owner for owner, row in zip(owner_ids, rows) if not row]
            if missing:
                raise Problem("not_found", "Unknown person: " + ", ".join(missing), 404)
            before = settings_admin.snapshot(c, bot, "owners")
            c.execute("UPDATE bot_config SET owner_ids_json=?,revision=revision+1 WHERE bot=?",
                      (encode(owner_ids), bot))
            if rooms.thread_mode(c, bot) == rooms.SHARED:
                rooms.sync_shared_room(c, auth, bot)
            settings_admin.record(c, who.actor, bot, "owners", before, owner_ids)
            H.event(c, who.actor, "bot.owners_changed", bot, {"owners": owner_ids})
            return {"bot": bot, "owners": [P.brief(row) for row in rows],
                    "revision": config["revision"] + 1}
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/access")
    def bot_access_read(request: Request, bot: str, on_behalf_of: str = ""):
        """Who may see, read and write to this bot (docs/permissions.md). For the people who manage it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if on_behalf_of:
                who = delegated_identity(c, who, on_behalf_of)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return settings_admin.access(c, who, bot)

    @app.post("/api/v2/bots/{bot}/access")     # the same write for the tools, whose client only posts
    @app.put("/api/v2/bots/{bot}/access")
    def bot_access_write(request: Request, bot: str, body: M.BotAccess):
        caller = request.state.identity
        def work(c):
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return settings_admin.set_access(c, who, bot, body)
        return mutate(request, body, work)

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
                current = onboarding._declared(c, created["slug"])
                if not created["created"] and current.get("template") != body.template:
                    raise Problem("template", "Change the bot's template in its definition before building it", 409)
                created.update(onboarding.attach_template(c, who, created["slug"], body.template, body.instructions,
                                                          queue_build=True, title_prefix=body.title_prefix))
            return created
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/co-owners")
    def bot_co_owners(request: Request, bot: str, body: M.BotCoOwners):
        """Add or remove people who own a bot. Any of its owners may; whoever it reports up to and the Admins
        are owners without being listed."""
        caller = request.state.identity
        def work(c):
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return settings_admin.co_owners(c, who, bot, body.add, body.remove)
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/placement")
    def placement(request: Request, bot: str, body: M.BotPlacement):
        caller = request.state.identity
        def work(c):
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            settings_admin._manager(c, who, bot)
            if risky(who) and closed_computer(c, who, bot, body.runner_id):
                return propose_card(c, who, "POST", "/api/v2/bots/" + bot + "/placement",
                                    body.model_dump(exclude={"on_behalf_of"}), placement_summary(c, bot, body.runner_id))
            config = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if not config:
                raise Problem("not_found", "Bot not found", 404)
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before moving it", 409)
            runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (body.runner_id,)).fetchone()
            if not runner:
                raise Problem("not_found", "Computer is not registered", 404)
            if (who.role != "owner" and not auth.bot_admin(who) and runner["operator"] != H.actor_id(who.actor)
                    and not (runner["accepts_member_bots"] and auth.member_bot(c, bot))):
                raise Problem("forbidden", "You may use only your own registered computers, or ones an admin has "
                              "opened to members' bots", 403)
            current = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
            generation = current["generation"] if current else 0
            if generation != body.expected_generation:
                raise Problem("version_conflict", "Assignment changed; refresh before moving it", 409)
            # A member's bot stays its member's whatever computer it runs on: placing it never hands it to the
            # computer's operator.
            operator = config["operator"] if auth.member_bot(c, bot) else runner["operator"]
            if current and current["runner_id"] == runner["id"] and config["operator"] == operator:
                return {"bot": bot, "operator": config["operator"], "revision": config["revision"],
                        "assignment": dict(current)}
            before = settings_admin.snapshot(c, bot, "placement")
            c.execute("UPDATE bot_config SET operator=?,revision=revision+1 WHERE bot=?", (operator, bot))
            assignment = execution.assign(c, who, bot,
                                          M.Assignment(runner_id=runner["id"], expected_generation=generation))
            reset = reset_bot_sessions(c, bot)
            settings_admin.record(c, who.actor, bot, "placement", before,
                                  settings_admin.snapshot(c, bot, "placement"))
            H.event(c, who.actor, "bot.placement_changed", bot,
                    {"runner": runner["id"], "operator": operator, "sessions_reset": reset})
            return {"bot": bot, "operator": operator,
                    "revision": config["revision"] + 1, "assignment": assignment,
                    "sessions_reset": reset}
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/model")
    def model(request: Request, bot: str, body: M.BotModel):
        who = request.state.identity
        def work(c):
            from . import strategy_acts
            # the owner's at every level
            strategy_acts.gate(c, who.actor, "cost", f"change {bot}'s model to {body.model}")
            settings_admin._manager(c, who, bot, report="model")
            from .shared_bots import refuse_copy
            refuse_copy(c, bot)
            choice = MODEL_BY_ID.get(body.model)
            if not choice:
                raise Problem("model", "Choose one of the supported models", 422)
            settings_admin.refuse_retired(choice)
            row = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if not row:
                raise Problem("not_found", "Bot not found", 404)
            if row["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before changing models", 409)
            config = json.loads(row["config_json"])
            old_model = config.get("model") or H.bot(c, bot).get("model")
            old_runtime = config.get("runtime") or H.bot(c, bot).get("runtime")
            old_effort = config.get("reasoning_effort") or H.bot(c, bot).get("effort")
            old_harness = resolve_harness(config, old_runtime)
            effort = settings_admin._effort(choice, body.effort, old_effort)
            harness = settings_admin._harness(choice, body.harness, old_harness)
            runtime = runtime_of(harness) or choice["runtime"]
            if (old_model == choice["id"] and old_runtime == runtime
                    and old_effort == effort and old_harness == harness):
                # Asking again for what is set is how a caller re-checks that the bot's computer runs it.
                return {"bot": bot, "model": choice["id"], "runtime": runtime, "harness": harness,
                        "effort": effort, "revision": row["revision"], "sessions_reset": 0,
                        "readiness": absolute_link(can_run(c, bot, runtime, harness=harness, model=choice["id"]), settings.public_url)}
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
            config.update({"model": choice["id"], "runtime": runtime, "harness": harness,
                           "reasoning_effort": effort,
                           "model_managed_by": "cloud"})
            c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?",
                      (encode(config), bot))
            c.execute("UPDATE bots SET model=?,runtime=?,effort=? WHERE slug=?",
                      (choice["id"], runtime, effort, bot))
            # Its shared copies follow it, so any bot may have moved onto another tool.
            model_login.hold_removals(c, settings)
            lift_after_model_change(c, settings, who.actor, bot, was)
            reset = reset_bot_sessions(c, bot)
            settings_admin.record(c, who.actor, bot, "model", before,
                                  settings_admin.snapshot(c, bot, "model"))
            H.event(c, who.actor, "bot.model_changed", bot,
                    {"from": old_model, "to": choice["id"], "runtime": runtime,
                     "harness": harness, "effort": effort,
                     "sessions_reset": reset})
            return {"bot": bot, "model": choice["id"], "runtime": runtime, "harness": harness,
                    "effort": effort, "revision": row["revision"] + 1, "sessions_reset": reset, "readiness": can}
        return mutate(request, body, settings_admin.as_report_manager(who, bot, "model", work))

    @app.post("/api/v2/bots/{bot}/fallback")
    def fallback(request: Request, bot: str, body: M.BotFallback):
        return mutate(request, body, lambda c: settings_admin.set_fallback(
            c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/keep-tasks-going")
    def bot_keep_tasks_going(request: Request, bot: str, body: M.BotKeepTasksGoing):
        return mutate(request, body, lambda c: settings_admin.set_keep_tasks_going(
            c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/turns-at-once")
    def bot_turns_at_once(request: Request, bot: str, body: M.BotTurnsAtOnce):
        """How many turns the bot runs at once across every computer; null is no limit (start_places.bot_limit)."""
        return mutate(request, body, lambda c: settings_admin.set_turns_at_once(
            c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/task-model")
    def bot_task_model(request: Request, bot: str, body: M.BotTaskModel):
        """The harness, model and effort the bot's tasks run on, or null for the same as the bot
        (backend/task_models.py). The bot's own model (POST .../model) never changes it, and this never changes that.
        `warning` is plain words for a cheaper pick on a bot that sends, spends, publishes or writes; it still saves."""
        who = request.state.identity
        def work(c):
            return settings_admin.set_task_model(c, who, bot, body)
        return mutate(request, body, settings_admin.as_report_manager(who, bot, "task_model", work))

    @app.post("/api/v2/bots/{bot}/manages-reports")
    def bot_manages_reports(request: Request, bot: str, body: M.BotManagesReports):
        """Whether this bot changes its direct reports (backend/report_managers.py). A person who manages this bot sets
        it; with the owner's team rule `managers_manage_reports` off, no bot does, whatever this says."""
        return mutate(request, body, lambda c: settings_admin.set_manages_reports(
            c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/compact-at")
    def bot_compact_at(request: Request, bot: str, body: M.BotCompactAt):
        return mutate(request, body, lambda c: settings_admin.set_compact_at(
            c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/wake-preset")
    def bot_wake_preset(request: Request, bot: str, body: M.BotWakePreset):
        """How soon the bot's nonblocking notices and enabled sweeps start a turn (backend/wake_presets.py)."""
        return mutate(request, body, lambda c: settings_admin.set_wake_preset(
            c, request.state.identity, bot, body))

    @app.get("/api/v2/wake-presets/measures")
    def wake_preset_measures(request: Request, since: str | None = None, until: str | None = None):
        """Per wake preset, over jobs queued in [since, until) (the last 7 days by default): turns and tokens per accepted
        ticket, pickup latency, blocked time and missed actions. Owners and admins."""
        admin_only(request.state.identity, "sees wake preset measures")
        with store.read() as c:
            return wake_presets.measures(c, since or H.shift(H.now(), days=-7), until)

    @app.post("/api/v2/bots/{bot}/transitions")
    def begin_transition(request: Request, bot: str, body: M.BotTransitionCreate):
        who = request.state.identity
        def work(c):
            acting = delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who
            result = settings_admin.begin(c, acting, bot, body)
            if acting is not who:
                H.event(c, who.actor, "bot.transition_delegated", bot,
                        {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of, "kind": body.kind})
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
            return settings_admin.history(c, request.state.identity, limit, bot=bot, before=before)

    @app.post("/api/v2/settings/history/{change_id}/undo")
    def undo_setting(request: Request, change_id: str, body: M.SettingsUndo):
        return mutate(request, body, lambda c: settings_admin.undo(c, request.state.identity, change_id, body))

    @app.post("/api/v2/bots/{bot}/limit/retry")
    def limit_retry(request: Request, bot: str, body: M.LimitRetry):
        """Try now: a person who renewed a plan need not wait out the usage-limit cooldown."""
        who = request.state.identity
        def work(c):
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "Only a person who manages this bot can retry it now", 403)
            # A hold already ended (by itself or an earlier Try now) waits only on its one probe turn.
            if ((H.status(c, bot) or {}).get("state") != "limited"
                    and (own_login_hold(c, settings, bot).get("until") or "") <= H.now()):
                raise Problem("not_limited", "This bot is not waiting on a usage limit", 409)
            if login_holds.probing(c, own_login_hold(c, settings, bot)):
                raise Problem("probing", "This bot's login is already trying one turn; its result decides the rest", 409)
            return {"cleared": clear_limits(c, settings, who.actor, bot)}
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/stop")
    def stop_turn(request: Request, bot: str, body: M.StopTurn):
        return mutate(request, body, lambda c: execution.stop(c, request.state.identity, bot, body.attempt_id, body.redirect))

    @app.get("/api/v2/bots/{bot}/pause-state")
    def bot_pause_state(request: Request, bot: str):
        """The public pause banner, without billing amounts or unreadable task counts."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            auth.require_see(c, who, bot)
            row = H.bot(c, bot)
            config = c.execute("SELECT operator,revision FROM bot_config WHERE bot=?", (bot,)).fetchone()
            operator = (H.human(c, config["operator"]) or {}) if config else {}
            record = c.execute("SELECT actor,ts,action FROM events WHERE target=? "
                               "AND action IN ('bot.pause','bot.resume') ORDER BY ts DESC,rowid DESC LIMIT 1", (bot,)).fetchone()
            paused_by = None
            if row["state"] == "paused" and record and record["action"] == "bot.pause":
                person = H.human(c, H.actor_id(record["actor"])) if record["actor"].startswith("human:") else None
                if person:
                    paused_by = {"name": person["name"], "at": record["ts"]}
            tasks = H._rows(c.execute("SELECT * FROM tasks WHERE owner=? AND status IN ('open','doing','waiting')",
                                     ('bot:' + bot,)))
            count = sum(1 for task in tasks if privacy.task_readable(c, who, task))
            value = {"state": row["state"], "revision": config["revision"] if config else None,
                     "can_control": bool(auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)),
                     "operator": operator.get("name") or (config["operator"] if config else "its operator"),
                     "paused_by": paused_by, "open_tasks": count}
            key, base = gateway_connection.target(c, app.state.vault, settings)[:2]
        # HQ calls must never hold a database transaction. Only the pause bit leaves this route.
        value["company_paused"] = company_billing.paused(key, base)
        return value

    @app.post("/api/v2/bots/{bot}/control")
    def control(request: Request, bot: str, body: M.BotControl):
        who = request.state.identity
        def work(c):
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "You do not manage this bot", 403)
            config = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before controlling it", 409)
            if body.action in ("scope", "unscope"):
                # A narrower claim, never a wider one: the bot's pause, drain and every other claim check stand, and
                # the work outside the task stays queued as it is (execution.scoped_sql).
                task = H.task(c, body.task_id) if body.action == "scope" and body.task_id else None
                if body.action == "scope" and not (task and privacy.task_readable(c, who, task)):
                    raise Problem("not_found", "No such task", 404)
                before = claim_scope(c, bot)
                c.execute("INSERT INTO bot_control(bot,claim_task) VALUES(?,?) "
                          "ON CONFLICT(bot) DO UPDATE SET claim_task=excluded.claim_task",
                          (bot, task["id"] if task else None))
                c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
                H.event(c, who.actor, "bot." + body.action, bot,
                        {"task_id": task["id"] if task else None, "previous": before})
                return {"bot": bot, "action": body.action, "task_id": task["id"] if task else None,
                        "revision": config["revision"] + 1}
            if H.bot(c, bot)["state"] == "quarantined" and body.action != "drain":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            if body.action == "resume":
                settings_admin.ensure_activation(c, who, bot)
            c.execute("INSERT INTO bot_control(bot,draining) VALUES(?,?) "
                      "ON CONFLICT(bot) DO UPDATE SET draining=excluded.draining",
                      (bot, int(body.action in ("drain", "pause"))))
            if body.action != "drain":
                c.execute("UPDATE bots SET state=? WHERE slug=?", ("active" if body.action == "resume" else "paused", bot))
                if body.action == "pause":
                    c.execute("UPDATE attempts SET lease_until=? WHERE bot=? AND state IN ('leased','running')", (H.now(), bot))
                    execution.expire(c)
                H.status_set(c, H.KEEPER, bot, state="paused" if body.action == "pause" else "idle")
            c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
            H.event(c, who.actor, "bot." + body.action, bot)
            return {"bot": bot, "action": body.action, "revision": config["revision"] + 1}
        return mutate(request, body, work)

    @app.post("/api/v2/jobs/claim")
    def claim(request: Request, body: M.Claim):
        # A read first: an idle runner asks every fraction of a second and must not take the write lock.
        with store.read() as c:
            selected, failed = [], ClaimFaults()
            idle = execution.idle_claim(c, request.state.identity, body,
                                        request.headers.get("idempotency-key"), selected, failed)
            if idle is not None:
                return idle
            # Only a registered computer's claim cleans up: anyone else is refused before any write.
            due = (execution.runner(c, request.state.identity) is not None
                   and execution.maintenance_due(c, request.state.identity))
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
                selected.append(execution.candidate(c, request.state.identity, body,
                                                    execution.runner(c, request.state.identity), failed=failed))
            # End any transaction opened during selection before taking the writer lock. The selected
            # job is checked against current policy in that transaction.
            if c.in_transaction:
                c.rollback()
            def work(write):
                if not selected:
                    return execution.claim(write, request.state.identity, body, maintained=True, failed=failed)
                return execution.claim(write, request.state.identity, body,
                                       selected=selected[0], prepared=True, maintained=True, failed=failed)
            return mutate(request, body, work)

    @app.post("/api/v2/attempts/{aid}/renew")
    def renew(request: Request, aid: str, body: M.Empty):
        with execution.calling(request.state.identity, aid) as arrived:
            if 0 < len(request.headers.get("idempotency-key", "")) <= 200:
                with store.read_transaction() as c:
                    validate_identity(c, request.state.identity)
                    fresh = execution.renew(c, request.state.identity, aid, if_fresh=True)
                    if fresh is not None:
                        return fresh
            return mutate(request, body, lambda c: execution.renew(c, request.state.identity, aid, arrived=arrived))

    @app.post("/api/v2/attempts/{aid}/start-place")
    def start_place(request: Request, aid: str, body: M.StartPlace):
        return mutate(request, body, lambda c: execution.start_place(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/started")
    def started(request: Request, aid: str, body: M.Started):
        from . import automations   # the turn's start acts for the turn (automations.for_attempt)

        def begin(c):
            with automations.for_attempt(c, aid):
                return execution.started(c, request.state.identity, aid, body)
        return mutate(request, body, begin)

    @app.post("/api/v2/attempts/{aid}/thread")
    def attempt_thread(request: Request, aid: str, body: M.AttemptThread):
        return mutate(request, body, lambda c: execution.thread(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/events")
    def events(request: Request, aid: str, body: M.EventBatch):
        with execution.calling(request.state.identity, aid):
            body = scrub_attempt(aid, body)          # a secret set in this run never reaches the record
            # Warm neutral provenance in a read snapshot. The transaction checks current
            # permissions again, and only reuses this graph while its source clock matches.
            with store.read_transaction() as c:
                execution.attempt(c, request.state.identity, aid, active=False)
            return mutate(request, body, lambda c: execution.events(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/capture")
    def keep_capture(request: Request, aid: str, body: M.StreamCapture):
        from . import stream_capture
        body = scrub_attempt(aid, body)          # a secret set in this run never reaches the record
        return mutate(request, body, lambda c: stream_capture.keep(
            c, execution.attempt(c, request.state.identity, aid, active=False), body))

    @app.post("/api/v2/attempts/{aid}/diagnostics")
    def attempt_diagnostics(request: Request, aid: str, body: M.AttemptStderr):
        from . import run_diagnostics
        body = scrub_attempt(aid, body)
        return mutate(request, body, lambda c: run_diagnostics.keep_stderr(
            c, execution.attempt(c, request.state.identity, aid, active=False), body.tail))

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
                if not c.execute("SELECT 1 FROM idempotency WHERE actor=? AND operation=? AND key=?",
                                 (request.state.identity.actor, request.url.path, key)).fetchone():
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
        body = scrub_attempt(aid, body)          # a secret set in this run never reaches the wake
        return mutate(request, body, lambda c: execution.background_done(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/complete")
    def complete(request: Request, aid: str, body: M.Completion):
        body = scrub_attempt(aid, body)
        from . import automations   # the turn's end acts for the turn (automations.for_attempt)
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
        tid = work_task_id(msg, conv)
        task = H.task(c, tid) if tid else None
        target = msg["from_actor"] if msg["kind"] == "ask" else reply_target(msg, conv, task)
        refs = {"turn_id": replay["attempt"], **({"task": tid} if task else {})}
        # The completion's own destination check, as the run it came from: a private task or meeting the reply drew
        # on that its reader lost since the hold keeps it from going there.
        privacy.require_destination(c, Identity(bot.actor, "bot", attempt_id=replay["attempt"]), target, conv["id"],
                                    refs, msg)
        if msg["kind"] == "ask":
            from .task_review import reply_answer
            sent = reply_answer(c, auth, Identity(bot.actor, "bot", attempt_id=replay["attempt"]), msg, replay["text"],
                                org_rule_check.warn_line(gate))
        else:
            text, _ = split_runner_notices(replay["text"])
            sent = H.say(c, bot.actor, target, org_rule_check.warned_text(gate, text), conversation_id=conv["id"],
                         in_reply_to=msg["id"], refs=refs)
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
            return rule_task_audience(c, task_row(c, bot, replay), bot.actor, body.text,
                                      *([rule_target(c, bot, body.ask.who)] if body.ask and body.ask.who else []))

        def comment_edit(c, bot, replay):
            body = M.TaskComment.model_validate(replay["body"])
            return rule_task_audience(c, task_row(c, bot, replay), bot.actor, body.text)

        return {"message": message, "note": note, "task_create": task_create, "task_update": task_update,
                "task_owner": task_update, "bulk": task_update, "comment": comment, "comment_edit": comment_edit,
                "answer": lambda c, bot, replay: answer_audience(c, bot, replay["message"]),
                "task_ask": lambda c, bot, replay: [task_row(c, bot, replay)["requester"]],
                "task_answer": lambda c, bot, replay: rule_task_audience(c, task_row(c, bot, replay), bot.actor),
                "reply": reply_audience}

    def completion_rule_check(request, aid, body):
        """The org rule check of a turn's reply, before the completion's write (backend/org_rule_check.py): the reply
        as the completion would post it, to whom it would post it. None when there is nothing to check."""
        if body.outcome != "completed" or not body.text:
            return None
        from .execution import reply_target, split_runner_notices, work_task_id
        with store.read() as c:
            if not org_rule_check.R.any_active(c):
                return None             # no rule in force: the completion reads nothing more for its check
            if store.saved(c, request.state.identity, request.url.path, request.headers.get("idempotency-key")):
                return None             # a retry answers from its saved result; nothing is checked again
            row = c.execute("SELECT a.bot, j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                            (aid,)).fetchone()
            msg = H.message(c, row["message_id"], include_deleted=True) if row else None
            if not msg or msg["deleted_at"] is not None:
                return None
            conv = H.conversation(c, msg["conversation_id"])
            tid = work_task_id(msg, conv)
            task = H.task(c, tid) if tid else None
            if not (msg["kind"] in ("say", "ask", "steer") or task):
                return None
            target = reply_target(msg, conv, task)
        bot = Identity("bot:" + row["bot"], "bot", attempt_id=aid)
        text, _ = split_runner_notices(body.text)
        return org_rule_check.preflight(store, app.state.judge, bot, "reply", text, [target],
                                        context={"task": task["title"]} if task else None)

    @app.get("/api/v2/bots/{bot}/execution-review")
    def execution_review(request: Request, bot: str):
        with store.read() as c:
            return execution.review_jobs(c, request.state.identity, bot)

    @app.post("/api/v2/jobs/{jid}/reconcile")
    def reconcile_job(request: Request, jid: str, body: M.ReconcileJob):
        return mutate(request, body, lambda c: execution.reconcile_job(c, request.state.identity, jid, body))

    @app.post("/api/v2/jobs/{jid}/retry")
    def retry(request: Request, jid: str, body: M.Retry):
        return mutate(request, body, lambda c: execution.retry(c, request.state.identity, jid, body))

    from .tags import install as install_tags
    install_tags(app, store, auth, mutate, task_views)
    # One stream of live events for people's pages (backend/events.py).
    from .events import install as install_events, install_runner as install_runner_events
    install_events(app, store, auth, task_views, task_view)
    app.state.task_view = task_view         # for the events' read in tests (backend/tests/test_payload_budgets.py)
    # And one for each computer, in place of its polls (runner-rs/src/events.rs).
    install_runner_events(app, store, execution)
    app_delivery.install(app, store, execution, mutate)

    from .views import install_views
    from .task_types import install_task_types
    install_task_types(app, store, auth, mutate, mover)
    from .pipeline_stats import install as install_pipeline_stats
    install_pipeline_stats(app, store, auth)
    from .roadmaps import install_internal
    install_internal(app, store, auth, mutate, mover)
    from .public_roadmaps import install as install_public_roadmaps
    install_public_roadmaps(app, store, auth, mutate)
    from .service_keys import install_service_keys
    install_service_keys(app, store, auth, mutate)
    install_views(app, store, auth, mutate, task_view)
    @app.get("/api/v2/attempts/{aid}/goal")
    def attempt_goal(request: Request, aid: str):
        with store.read() as c:
            attempt = execution.attempt(c, request.state.identity, aid)
            msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0])
            from .chat_goals import current
            return {"goal": current(c, msg["conversation_id"])}

    from .chat_goals import install as install_chat_goals
    install_chat_goals(app, store, auth, mutate, send)
    from .bot_task_tabs import install as install_bot_task_tabs
    install_bot_task_tabs(app, store, auth, mutate, settings, task_views)
    from .cards import install as install_cards
    install_cards(app, store, auth, mutate, settings, task_view)
    from . import memory_tidy_cards
    memory_tidy_cards.install(app, store, auth, mutate)
    turn_work.install(app, store, auth)
    from . import run_view
    run_view.install(app, store, auth)
    from .documents import install_documents
    install_documents(app, store, auth, mutate)
    from .docs import install_docs
    install_docs(app, store, auth, mutate)
    from .context_search import install_context_search
    install_context_search(app, store, auth)
    from .market import install as install_market
    install_market(app, store, auth, mutate)
    from .market_watch import install as install_market_watch
    install_market_watch(app, store, auth, mutate)
    from .market_briefing import install as install_market_briefing
    install_market_briefing(app, store, auth, mutate)
    from .market_spend import install as install_market_spend
    install_market_spend(app, store, auth, mutate)
    from .strategy_market import install as install_strategy_market
    install_strategy_market(app, store, auth, mutate)
    from .strategy import install as install_strategy
    install_strategy(app, store, auth, mutate)
    from .brand_gtm import install as install_brand_gtm
    install_brand_gtm(app, store, auth, mutate)          # the brand and go-to-market pages
    from .growth import install as install_growth
    install_growth(app, store, auth, mutate, task_create)   # channels, sources, ideas, cohorts, unit economics
    from .strategy_setup import install as install_strategy_setup
    install_strategy_setup(app, store, auth, mutate)
    from .lenses import install as install_lenses
    install_lenses(app, store, auth, mutate)
    from .listening import install as install_listening
    install_listening(app, store, auth, mutate)
    from .archive import install_archives
    install_archives(app, store)
    from .media import install_media
    install_media(app, store, auth, mutate, send, task_create, review_preflight, rule_check)
    from .downloads import install_downloads
    install_downloads(app, store)
    from .team_icon import install_team_icon
    install_team_icon(app, store)
    from .meeting_items import install_meeting_items
    install_meeting_items(app, store, mutate, task_create)
    from .connectors import install_connectors
    install_connectors(app, store, execution, mutate)
    from .imports import install_imports
    install_imports(app, store, auth, execution, mutate)
    from .granola_mcp import install_granola
    install_granola(app)
    from .meeting_review import install_meeting_review
    install_meeting_review(app, store, auth, mutate)
    from .credentials import install_credentials
    install_credentials(app, store, delegate=delegated_identity, propose=propose_card)
    from .finance import actions as finance_vault
    finance_vault.use_vault(app.state.vault)      # never one an earlier app left behind (the scheduler may be off)
    install_credential_cards(app, store, app.state.vault, auth, BOTOPS, delegated_identity, settings_admin._manager)
    from . import company_storage
    company_storage.install(app, store, auth)
    from . import tico_storage
    tico_storage.install(app, store, auth)
    from .sql import install_sql
    install_sql(app, store, auth)
    from .judge import install_judge
    install_judge(app, store, auth)
    ask_ingest.install(app, store, auth)
    from . import stale_review
    stale_review.install(app, store, auth, mutate)
    from . import system_routines
    system_routines.install(app, store, auth, mutate)
    from . import task_rules
    task_rules.install(app, store, auth, mutate)
    from . import rules as wake_rules_api
    wake_rules_api.install(app, store, auth)                # the event catalog; wake rules are automations now
    turn_budgets.install(app, store, auth, mutate, settings_admin)
    from . import automations
    automations.install(app, store, auth, mutate)
    from . import audit_log
    audit_log.install(app, store, auth)
    task_caps.install(app, store, auth, mutate)
    from . import org_rules
    # Org rule checks first: their /api/v2/rules/stats and /check come before org_rules' /api/v2/rules/{rule_id}.
    from .automations import replay_audience, replay_step
    org_rule_check.REPLAY["automation"] = replay_step
    org_rule_check.AUDIENCE.update(rule_audiences())
    org_rule_check.AUDIENCE["automation"] = replay_audience
    org_rule_check.install(app, store, auth, mutate)
    counsel.install(app, store, auth, mutate)      # before org_rules: /rules/read and /rules/ask come before /rules/{id}
    org_rules.install(app, store, auth, mutate)
    profiles.install(app, store, auth, mutate, settings_admin)
    from . import release_flow
    release_flow.install(app, store, auth, mutate)
    from . import screening
    screening.install(app, store, auth, mutate)    # prompt-injection screening at webhooks, mail, ideas and watchers
    from . import finance_routes
    finance_routes.install(app, store, auth, mutate)    # Finance: budgets, P&L, expenses, actions (backend/finance)
    from . import bills_address
    bills_address.install(app, store, auth)             # the bills address: emailed bills become draft bills
    outcome_stats.install(app, store, auth)
    from .live_meetings import install_live_meetings
    install_live_meetings(app, store, auth, mutate)
    from .recorder_relay import install_recorder_relay
    install_recorder_relay(app, store, auth, settings)
    from .mail import install_mail
    install_mail(app, store, auth)
    from .messaging import install_messaging
    install_messaging(app, store, auth)
    from .integrations import install_integrations
    install_integrations(app, store, auth, mutate)
    from .memory_history import MAX_REPORT_BYTES, install_memory_history
    install_memory_history(app, store, auth, mutate)
    from .learnings import install as install_learnings
    install_learnings(app, store, auth, mutate)
    from .learned import install as install_learned
    install_learned(app, store, auth, mutate)
    from .mcp import install_mcp
    install_mcp(app, settings)
    from .github import install_github
    install_github(app, settings, store)
    from . import triggers
    triggers.install(app, settings, store, auth, mutate)
    from . import webhooks
    webhooks.install(app, settings, store, auth, mutate)
    # GitHub App per company: manifest setup, installation tokens for runners (docs/github-app.md).
    from .github_app import install_github_app
    install_github_app(app, settings, store)
    from . import worktrees
    worktrees.install(app, store, auth, mutate)
    # Slack tokens for the gateway container/process: sealed here, opened only there (docs/slack.md).
    from .slack_app import install_slack_app
    install_slack_app(app, settings, store)
    from .slack_channels import install as install_slack_channels
    install_slack_channels(app, settings, store, auth)
    from .directory import install_directory
    install_directory(app, settings, store, mutate)
    from .scim import install_scim
    install_scim(app, settings, store)
    from .getting_started import install as install_getting_started
    install_getting_started(app, store, auth, mutate, settings)
    from .health import install as install_health
    install_health(app, store, auth, settings)
    flight.install(app, store, auth)
    from .assistant import install as install_assistant
    install_assistant(app, store, auth, mutate, onboarding)
    from .librarian import install as install_librarian
    install_librarian(app, store, auth, mutate, onboarding)
    from .librarian_setup import install as install_librarian_setup
    install_librarian_setup(app, store, auth, mutate)
    from .goal_routes import install as install_goal_routes
    install_goal_routes(app, store, auth, mutate, settings)
    from .replay import install as install_replay
    install_replay(app, store, auth)
    from .bot_hours import install as install_bot_hours
    install_bot_hours(app, store, auth)
    from .usage import install as install_usage
    install_usage(app, store, auth, mutate, settings)
    from .bot_tools import install as install_bot_tools
    def as_requester(c, who):
        """BotOps with no `X-Tico-On-Behalf-Of` header is still doing a person's errand in a turn their chat message
        started (an older client, a route's plain tool): check the person, never BotOps, who manages no one's bot."""
        if who.actor != "bot:" + BOTOPS:
            return who
        try:
            return delegated_identity(c, who, "turn")
        except Problem:
            return who
    install_bot_tools(app, store, auth, mutate, settings_admin, as_requester)
    from .mail_settings import install as install_mail_settings
    install_mail_settings(app, store, auth, mutate)
    from .shared_bots import install as install_branches
    install_branches(app, store, auth, mutate, settings_admin, execution)
    from .bot_copy import install as install_bot_copy
    install_bot_copy(app, store, auth, mutate, settings_admin, place_now)
    from .subscriptions import install as install_subscriptions
    install_subscriptions(app, store, auth, mutate, settings, computer_rows)
    from .model_subscriptions import install as install_model_subscriptions
    install_model_subscriptions(app, store, auth, mutate, settings)
    from .groups import install as install_groups
    install_groups(app, store, auth, mutate, settings)
    from .team_build import install as install_team_build
    install_team_build(app, store, auth, mutate, onboarding)
    from .report_order import install as install_report_order
    install_report_order(app, store, auth, mutate)
    from .settings_catalog import install as install_settings_catalog
    install_settings_catalog(app, store, auth, settings)
    from .support import install as install_support
    install_support(app, store, settings, census)
    from .cloud_agents import install as install_cloud_agents
    install_cloud_agents(app, store, auth, settings, mutate, settings_admin, onboarding)
    from .inbound import install as install_inbound
    install_inbound(app, store, settings, auth)
    from .hook_relay import install as install_hook_relay
    install_hook_relay(app, settings, store, auth)
    from .postal_mail import install as install_postal_mail
    install_postal_mail(app, settings, store, auth)
    from .sleep_idle import install as install_sleep_idle
    install_sleep_idle(app, settings, store, auth)
    from .cloud_machines import install as install_cloud_machines
    install_cloud_machines(app, settings, store, auth, execution, mutate)
    company_server.install(app, store, settings, auth)
    from .support_access import install as install_support_access
    install_support_access(app, store, settings, auth)
    from .watchers import install as install_watchers
    install_watchers(app, store, auth, execution, mutate)
    from .health_monitor import install as install_health_monitor
    install_health_monitor(app, store, auth, mutate, onboarding, settings_admin, settings)
    from .task_manager import install as install_task_manager
    install_task_manager(app, store, auth, mutate, onboarding, settings_admin, settings)
    # A computer's screen in the app (backend/screen.py); its websockets go before the static mounts below.
    from .screen import install as install_screen
    screen_sessions = install_screen(app, store, auth, mutate, execution, settings)
    from .performance import install as install_performance
    install_performance(app, store, auth, mutate, execution)
    # A bot's browser through another computer's internet (backend/browser_exit.py); the same relay as the screen.
    from .browser_exit import install as install_browser_exit
    install_browser_exit(app, store, auth, mutate, execution, screen_sessions)
    from .route_renames import install as install_route_renames
    install_route_renames(app)           # after every route: the new spellings canonical, the old ones deprecated

    # Only the frontend directory is served. No project root, runtime DB, or secrets.
    # The page loads its scripts and styles from /tico/ui/ (ui/index.html), so the same directory is
    # mounted there as well as at the root.
    # One script and one stylesheet instead of ~80 files (backend/ui_bundle.py): index.html is served with
    # its bundle regions replaced by the versioned bundle tags. TICO_UI_BUNDLE=off serves the files as listed.
    if ui_bundle.enabled():
        ui_bundles = ui_bundle.shared(settings.ui_dir)

        def ui_reply(request, body, media_type, etag, cache_control=None, gz=None, gz_etag=None):
            headers = {"ETag": etag}
            if cache_control:
                headers["Cache-Control"] = cache_control
            if gz is not None:
                headers["Vary"] = "Accept-Encoding"
                from .json_response import accepts_gzip
                if accepts_gzip(request.headers):
                    body, etag, headers["ETag"], headers["Content-Encoding"] = gz, gz_etag, gz_etag, "gzip"
            # A weak form of the tag (Cloudflare sends one) matches too: a 304 needs only the same content.
            if etag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
                return Response(status_code=304, headers=headers)
            return Response(body if request.method == "GET" else None, media_type=media_type, headers=headers)

        @app.api_route("/tico/ui/app.bundle.js", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/tico/ui/app.bundle.css", methods=["GET", "HEAD"], include_in_schema=False)
        async def ui_bundle_asset(request: Request):
            bundle = ui_bundles.get()
            if bundle is None:
                raise Problem("not_found", "Not found", 404)
            js = request.url.path == ui_bundle.JS_PATH
            return ui_reply(request, bundle.js if js else bundle.css,
                            "text/javascript; charset=utf-8" if js else "text/css; charset=utf-8",
                            bundle.etag[request.url.path], ui_bundle.IMMUTABLE,
                            bundle.gz[request.url.path], bundle.gz_etag[request.url.path])

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/tico/ui/", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/tico/ui/index.html", methods=["GET", "HEAD"], include_in_schema=False)
        async def ui_page(request: Request):
            bundle = ui_bundles.get()
            if bundle is None:                       # cannot be bundled: the files, as they are
                return FileResponse(settings.ui_dir / "index.html")
            return ui_reply(request, bundle.page, "text/html; charset=utf-8", bundle.page_etag)

    # three.js is ~790 KB of text and StaticFiles sends it as is; the Overview waits on it. Gzipped once in
    # memory it is ~200 KB.
    three_dir, three_cache = settings.ui_dir / "vendor" / "three", {}

    @app.api_route("/tico/ui/vendor/three/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/vendor/three/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui_three(request: Request, name: str):
        import gzip, hashlib
        from .json_response import accepts_gzip
        path = three_dir / name
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.js", name) or not path.is_file():
            raise Problem("not_found", "Not found", 404)
        stat = path.stat()
        entry = three_cache.get(name)
        if not entry or entry[0] != (stat.st_mtime_ns, stat.st_size):
            raw = path.read_bytes()
            tag = hashlib.sha1(raw).hexdigest()[:20]
            entry = three_cache[name] = ((stat.st_mtime_ns, stat.st_size), raw, gzip.compress(raw, 6, mtime=0), tag)
        _, raw, gz, tag = entry
        zipped = accepts_gzip(request.headers)
        etag = f'"{tag}{"-gz" if zipped else ""}"'
        headers = {"ETag": etag, "Vary": "Accept-Encoding"}
        if zipped:
            headers["Content-Encoding"] = "gzip"
        if etag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
            return Response(status_code=304, headers=headers)
        return Response((gz if zipped else raw) if request.method == "GET" else None,
                        media_type="text/javascript; charset=utf-8", headers=headers)

    app.mount("/tico/ui", StaticFiles(directory=settings.ui_dir, html=True), name="ui-prefixed")
    app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    if settings.demo:
        from . import demo
        demo.install(app, auth, settings)
    from . import cors
    cors.install(app, settings)          # last, so it is outermost: a preflight is answered before sign-in
    return app
