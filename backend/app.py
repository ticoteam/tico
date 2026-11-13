"""Authenticated, deterministic API. No runtime launch or local-machine dependency."""

import asyncio
import logging
import os
import time
import json
from dataclasses import replace
import re
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from starlette.requests import ClientDisconnect


from . import built_on
from . import agents, app_delivery, app_view, mcp_oauth, oidc, views
from . import outcome_stats
from . import pipeline_judges
from . import recorder_installs
from . import assistant_changes, why_here
from . import teammate_access
from . import task_followers
from . import setup_failures
from . import sources as SRC
from . import start_places
from . import ask_ingest, delivery, strategy_bot, invites, owner_link, setup_handoff
from . import org_rule_check
from . import task_rule_gate
from . import bot_home_merge
from .roadmaps import exact_approval
from . import cloud_access
from . import counsel
from . import thread_api
from . import recipients as recipient_gate
from . import runs
from . import bot_stops, turn_budgets, task_caps, reach
from . import (
    check_models,
    profiles,
    run_triggers,
    task_run_models,
    team_rules,
    update_feeds,
    topic_change,
    usage_limits,
    bot_to_bot,
    visuals,
    wake_rules,
)
from . import task_privacy as privacy
from . import task_relations as TR
from . import task_roles as TRo
from . import placement as placing
from . import turns as turn_work
from . import goals as G
from . import models as M
from .auth import LOCAL_SIGNIN_PATH, LOGOUT_PATH, Auth, Identity
from .config import Settings
from .observability import Observability, route as error_route
from .execution import (
    AWAKE_GAP,
    Execution,
    runner_repository,
    flag_cut,
    reset_bot_sessions,
)
from .onboarding import BOTOPS, Onboarding
from .recruit import Recruiter
from . import rooms
from . import fast_json
from . import names as actor_names
from .openapi_v2 import STABLE as STABLE_ROUTES
from .route_renames import old_paths as old_route_paths
from . import settings_catalog
from . import updates
from .files import raw_media
from clients import gateway as gateway_client
from . import company_server, gateway_connection
from . import access as Access
from . import hosted_updates, releases, runner_versions, ui_bundle
from .census import Census
from .providers import MODEL_BY_ID
from .settings_admin import SettingsAdmin
from .readiness import absolute as absolute_link
from . import fleet_check as fleet_check_module
from .getting_started import _online_runners
from .screen import listing as screen_listing
from .performance import listing as performance_listing
from .credential_cards import install_credential_cards, scrub_attempt
from .github import PATH as GITHUB_WEBHOOK_PATH
from .github_app import CALLBACK_PATH as GITHUB_APP_CALLBACK_PATH
from .triggers import PATH as TRIGGER_HOOK_PATH
from .listening import PUSH_PATH as LISTENING_PUSH_PATH
from .tool_proxy import PREFIX as TOOL_PROXY_PATH
from .store import H, P, Problem, Store, encode
from . import bot_settings as BS
from . import bot_status_routes, computers_routes
from . import brand_icons
from .bot_status_routes import INBOX_LIMIT  # noqa: F401
from .bot_status_routes import INBOX_PAGE  # noqa: F401
from .bot_status_routes import _first_visible  # noqa: F401
from . import (
    providers_routes,
    billing_routes,
    access_routes,
    people_routes,
    updates_routes,
    task_list_routes,
    task_bulk_routes,
    task_actions_routes,
    task_moves_routes,
    task_pipelines_routes,
    task_factories_routes,
    preferences_routes,
    release_schedule_routes,
    task_comments_routes,
    task_followers_routes,
    task_links_routes,
    bot_seats_routes,
    fleet_runner_routes,
    ui_bundle_routes,
)


# The stable v2 routes whose answers carry display names (backend/names.py); streams are left alone.
STABLE_PATH = re.compile(
    "|".join(
        re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(spelling))
        for path, *_ in STABLE_ROUTES
        if path != "/api/v2/events"
        for spelling in (path, *old_route_paths(path))
    )
)


# Gateway image calls in flight per process (each holds a thread up to 130 s); more answer 429 with Retry-After.
GATEWAY_IMAGE_SLOTS = 4


def filed_for(c, who, person, message_id):
    """`--for human:X --from-message M` (Ideas: say it once, #227): a bot files a person's own ask, and the task is the
    person's. Only when X sent M and the bot reads M; anything else is refused, and nothing is filed."""
    if who.role != "bot":
        raise Problem("for", "Only a bot files a task for a person; a person files their own", 422)
    if not H.is_human(person) or not H.human(c, person):
        raise Problem("for", f"{person} is not a person on this team", 422)
    message = H.message(c, message_id)
    from . import threads

    if not message or not threads.readable(c, who, message):  # its conversation, and the message in it
        raise Problem("from_message", "No message you can read has that id", 403)
    if message.get("sender_id") != person:
        raise Problem("from_message", f"That message was not sent by {person}", 403)
    return person


def create_app(settings=None):
    settings = settings or Settings.from_env()
    SRC.set_assistant_name(settings.assistant_name)  # the labels of keeper lines (backend/sources.py)
    telemetry = Observability(settings)
    from . import flight

    recorder = flight.Recorder() if settings.flight_recorder else None
    store = Store(settings, recorder)
    # An upgraded database gets its pending migrations (the cleanup's column renames) before any install below opens a
    # transaction: install-time code and every commit's hooks use the new names. Startup's store.initialize does the rest.
    if settings.db_path.exists():
        store.migrate()
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
        # roomier pool, and sign-in its own (app.state.auth_pool, made below and shut down on the way out).
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
        cloud_access.current(store)  # a lock kept from before the restart holds from the first request
        asyncio.get_running_loop().run_in_executor(None, auth.warm)  # Cloudflare's keys, before anyone signs in
        asyncio.get_running_loop().run_in_executor(None, app.state.vault.cipher.warm, store)  # the KMS data key
        # TICO_SAMPLE_COMPANY=on: a new company starts as the sample company, once (backend/sample_company.py).
        # The company file and the sample company stay before serving: on a first start the first page shows the
        # company they make, and the owner's first answer must not race them; on any later start each is a quick check.
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
        # Finance on every install (backend/finance/setup.py): idempotent, after the migrations, one short transaction.
        try:
            from .finance import actions as finance

            with store.transaction() as c:
                finance.ensure_setup(c, settings.owner_email)
        except Exception as exc:
            telemetry.capture("finance_setup", exc)
        # The Assistant's routines it never had, the Finance Owner once Finance is set up and the rest of what completes a
        # company run with the start jobs, below (Onboarding.complete_company).
        # Physical mail filed before bills and tax letters were private: those tasks go private, once.
        try:
            app.state.postal_mail.backfill_private()
        except Exception as exc:
            telemetry.capture("postal_mail_backfill", exc)
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
        # The runs the last process still held were cut by its stop, unless their computers carry on: flagged, they
        # resume on the next sweep if they lapse (execution.cut_by_update).
        try:
            with store.transaction() as c:
                flag_cut(c, "restart")
        except Exception as exc:
            telemetry.capture("flag_cut", exc)
        # A deploy keeps the API down longer than a lease; do not fail turns for that.
        execution.grace_leases()
        # A live support session picks its link to HQ back up (backend/support_access.py). Before serving: it also gives
        # the link worker its loop, which a support session started by the first request needs, and it re-mints the
        # token of whichever session is live, so run later it could replace the token of a session started just now.
        try:
            from . import support_access

            support_access.started(app, asyncio.get_running_loop())
        except Exception as exc:
            telemetry.capture("support_access", exc)
        telemetry.start()

        # Work no request depends on runs beside the server; initialization and the restart repairs stay above.
        from . import start_jobs

        # TICO_START_JOBS=inline (the tests, conftest.py) runs them before serving, as a start did before.
        if os.environ.get("TICO_START_JOBS") == "inline":
            start_jobs.run(store, onboarding, telemetry)
            start_jobs_task = None
        else:
            start_jobs_task = asyncio.create_task(asyncio.to_thread(start_jobs.run, store, onboarding, telemetry))
        stop = asyncio.Event()
        # Always runs: local 90-day retention must not depend on the inbound key or HQ (sync itself stays off).
        inbound_task = asyncio.create_task(app.state.inbound.loop(stop))
        meeting_screen_task = asyncio.create_task(app.state.meeting_frames.run(stop))
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

            scheduler = Scheduler(store, execution, onboarding=onboarding, auth=auth, github_app=app.state.github_app)
            from . import system_routines

            def recorded(key, work):
                # One system routine's run, recorded for Settings > Routines (backend/system_routines.py).
                with system_routines.recording(store, key) as run:
                    work()
                    run.did("Done", quiet=True)

            def release_check():
                # The release check (and the anonymous count that rides on it) runs even when nobody has the
                # page open; it is a no-op until its six hours are up.
                releases.notice()
                # Subscribed updates for bots, as quiet notes; nothing is fetched while no bot subscribes.
                update_feeds.tick(store)

            # The automation queue, the email sends and the Workspace key check, each on a worker of its own beside
            # this loop (backend/drains.py): the beat's due times and recovery never wait for Gmail or a connector.
            from . import drains

            workers = drains.Drains(
                store,
                auth,
                on_error=lambda name, exc: telemetry.capture(drains.TELEMETRY[name], exc),
                stopping=stop.is_set,
            ).start()
            scheduler.drains = workers
            # The steps that call GitHub, and the release check and update feeds, on a worker of their own beside
            # this loop (backend/heartbeat.py `Outside`): a slow GitHub never holds up a due time or a recovery.
            from . import heartbeat

            outside = heartbeat.Outside(
                scheduler,
                app=app,
                telemetry=telemetry,
                also=lambda: recorded("release_check", release_check),
                stopping=stop.is_set,
            ).start()
            scheduler.outside = outside
            try:
                await run_passes(scheduler)
            finally:
                # No new claims or passes; a call already started finishes within its own timeout.
                outside.stop.set()
                await asyncio.to_thread(workers.stop, 75)
                await asyncio.to_thread(outside.halt, 15)

        async def run_passes(scheduler):
            from . import heartbeat

            rest = heartbeat.Rest()
            while not stop.is_set():
                # The Heartbeat (backend/heartbeat.py): personal alerts, the tick's due-time and recovery steps and the
                # call sweeps, in that order, each on its own (one failing never stops the next; each goes to
                # telemetry as before). The outside polls' worker is asked for its pass at the beat's time first, so
                # it runs beside the beat and never more than one pass at a time.
                ctx = heartbeat.Context(scheduler, app=app, telemetry=telemetry)
                if scheduler.outside is not None:
                    scheduler.outside.wake(ctx.at)
                try:
                    await asyncio.to_thread(heartbeat.beat, ctx)
                except Exception as exc:
                    telemetry.capture("heartbeat", exc)
                # The rest of the pass, at most every 10 s however short the beat (heartbeat.REST_FLOOR). A skipped
                # pass's beat failures ride on the next one, so they still reach the scheduler's health row.
                if rest.due(ctx):
                    try:
                        await asyncio.to_thread(scheduler.tick, ctx.at, ctx)
                        if scheduler.outside is not None:
                            # The release check, here only while the worker's thread is dead.
                            await asyncio.to_thread(scheduler.outside.rest_fallback)
                    except Exception as exc:
                        telemetry.capture("scheduler", exc)
                        import logging

                        logging.getLogger("tico.scheduler").error("Scheduler tick failed: %s", type(exc).__name__)
                try:
                    # Seconds between beats (Automations > Heartbeat), read each pass: 10 by default, 5 to 60.
                    await asyncio.wait_for(stop.wait(), timeout=await asyncio.to_thread(heartbeat.interval, store))
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
            # Finance's ad spend import (backend/finance/ad_import.py): Google Ads and Meta daily spend every 4 hours;
            # a rehearsal copy starts none.
            from .finance import ad_import

            ad_import.start(store, app.state.vault, rehearsal=settings.rehearsal)
        # Webhooks waiting at Tico Gateway; the loop does nothing without a relay or a key.
        hook_relay_task = asyncio.create_task(app.state.hook_relay.loop(stop)) if timers else None
        # Physical mail (backend/postal_mail.py): reads and routes the letters the relay and the mail-intake inbox kept.
        postal_mail_task = asyncio.create_task(app.state.postal_mail.loop(stop)) if timers else None
        letters_task = asyncio.create_task(app.state.letters.loop(stop)) if timers else None
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
        library_task = (
            asyncio.create_task(asyncio.to_thread(file_library.loop, store, app.state.blobs, library_stop))
            if timers
            else None
        )
        demo_task = None
        if settings.demo:
            from . import demo

            demo_task = asyncio.create_task(demo.keep_alive(store, stop, app.state.vault))
        # Sign-in's own threads, one pool per run of the app: an app built and served again and again (a demo build,
        # every test) would otherwise keep 16 idle threads, and the app they hold, for the life of the process.
        # A test can run the same app's lifespan inside another (two clients on one app), so each run keeps its own
        # pool and clears the shared slot only while that slot is still its own.
        auth_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="tico-auth")
        app.state.auth_pool = auth_pool
        from . import needs_lane

        lane = needs_lane.start(app)  # Needs you's own build threads (backend/needs_lane.py)
        try:
            yield
        finally:
            # First, so nothing below that raises can skip it; requests are done by the time the server shuts down.
            if app.state.auth_pool is auth_pool:
                app.state.auth_pool = None
            auth_pool.shutdown(wait=False, cancel_futures=True)
            needs_lane.stop(app, lane)
            stop.set()
            if start_jobs_task:
                await start_jobs_task  # short, and each in its own transaction: let it finish
            app.state.live_meeting_routing.stop()
            await meeting_screen_task
            if inbound_task:
                await inbound_task
            if hook_relay_task:
                await hook_relay_task
            if postal_mail_task:
                await postal_mail_task
            if letters_task:
                await letters_task
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
                from .finance import ad_import as ad_worker

                await asyncio.to_thread(ad_worker.stop)
            telemetry.close()
            # Every task above has stopped, so no read is in flight: the store's idle connections close with the server.
            store.close()

    app = FastAPI(
        title=settings.app_name + " API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.auth, app.state.execution = store, auth, execution
    app.state.onboarding = onboarding  # tests build a picked team as bots, as the sample company does
    from .ui_compression import UiCompression

    app.add_middleware(UiCompression)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Field names and reasons are useful; invalid input can hold credentials and is never echoed.
        detail = "; ".join(".".join(str(part) for part in error["loc"]) + ": " + error["msg"] for error in exc.errors())
        return JSONResponse({"error": {"code": "validation", "detail": detail, "retryable": False}}, status_code=422)

    app.state.observability = telemetry
    app.state.census = census

    def raw_who_may(c, request, acting):
        """The catalogue's who-may line for a setting's write its route refused this person (settings_catalog)."""
        return settings_catalog.raw_refusal(
            c,
            auth,
            acting,
            request.method,
            request.url.path,
            botops_act.parse_body(getattr(request, "_body", b"")),
            settings.public_url,
        )

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
        if (
            caller is not None
            and caller.actor == "bot:" + BOTOPS
            and exc.status in (403, 409)
            and "fix" not in getattr(exc, "extra", {})
        ):
            # BotOps relays a refusal to a person: what to click, and where (botops_act.fix).
            acting = getattr(request.state, "identity", None) or caller

            def hint():
                parts = request.url.path.split("/")
                with store.read() as c:
                    names = actor_names.lookup(
                        c,
                        [acting.actor]
                        + (
                            ["bot:" + (H.resolve_bot(c, parts[4]) or {}).get("id", parts[4])]
                            if parts[3:4] == ["bots"] and len(parts) > 4
                            else []
                        ),
                    )
                hint = botops_act.fix(request.url.path, exc.code, exc.status, acting, names, settings.public_url)
                if (
                    hint
                    and hint["fix"].startswith(botops_act.ROLE_FIX)
                    and exc.code == "forbidden"
                    and H.is_human(acting.actor)
                ):
                    # A setting's own route said no to a person it isn't theirs to change: rather than "ask an owner
                    # or admin", who may, as `tico settings` says it.
                    with store.read() as c:
                        hint = raw_who_may(c, request, acting) or hint
                return hint

            found = await asyncio.get_running_loop().run_in_executor(None, hint)
            if found:
                exc.extra = {**getattr(exc, "extra", {}), **found}
        acting = getattr(request.state, "identity", None)
        if (
            exc.status == 403
            and exc.code == "forbidden"
            and getattr(acting, "via", None) == "assistant"
            and H.is_human(acting.actor)
            and "fix" not in getattr(exc, "extra", {})
        ):
            # The person's Assistant wrote for them and the setting's route said no: who may, as `tico settings` says.
            def assistant_who_may():
                with store.read() as c:
                    return raw_who_may(c, request, acting)

            said = await asyncio.get_running_loop().run_in_executor(None, assistant_who_may)
            if said:
                exc.extra = {**getattr(exc, "extra", {}), **said}
        if getattr(exc, "extra", None) and str(exc.extra.get("link") or "").startswith("#/"):
            exc.extra = absolute_link(exc.extra, settings.public_url)
        if setup_failures.step(request.method, getattr(request.scope.get("route"), "path", "")):
            # A refused setup step leaves its category, status and ids on the server, and the answer carries the
            # row's reference (backend/setup_failures.py).
            ref = await asyncio.get_running_loop().run_in_executor(
                None, setup_failures.record, store, request, exc.code, exc.status, getattr(exc, "extra", None)
            )
            if ref:
                exc.extra = {**getattr(exc, "extra", {}), "ref": ref}
        if str(exc.code).startswith("task_"):
            # The engine's refusal, flat (docs/dev/pipelines-v3-cutover.md, "HTTP"): the rule, one line, what is
            # missing, the commands allowed next and the task's version.
            return JSONResponse(
                {"error": exc.code, "detail": exc.detail, "retryable": exc.retryable, **getattr(exc, "extra", {})},
                status_code=exc.status,
            )
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "detail": exc.detail,
                    "retryable": exc.retryable,
                    **getattr(exc, "extra", {}),
                }
            },
            status_code=exc.status,
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_handler(request, exc):
        telemetry.capture("request", exc, 503, route=error_route(request))
        request.state.telemetry_captured = True
        # The write lock stayed taken past store.WRITE_WAIT: a load answer, not a fault. Clients retry with the same
        # Idempotency-Key (runner, clients/tico.py, ui/app/api.ts), so the write lands once.
        if "locked" in str(exc) or "busy" in str(exc):
            return JSONResponse(
                {"error": {"code": "busy", "detail": "Tico is busy; retry shortly", "retryable": True}},
                status_code=503,
                headers={"Retry-After": "2"},
            )
        # No SQL, database paths, or request bodies in client-facing diagnostics.
        return JSONResponse(
            {"error": {"code": "storage_unavailable", "detail": "Please retry shortly", "retryable": True}},
            status_code=503,
        )

    # Bot text reaches the page as HTML; scripts run only from our own files (ui/app, ui/*.js), so a
    # tag that slips through a renderer cannot run. The page carries no inline script today; one that
    # is added to index.html is allowed by its hash, and any other inline script is refused.
    import base64
    import hashlib

    try:
        inline = re.findall(r"<script>(.*?)</script>", (settings.ui_dir / "index.html").read_text(), re.S)
    except OSError:
        inline = []
    hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(block.encode()).digest()).decode() + "'" for block in inline]
    PAGE_POLICY = (
        "script-src " + " ".join(["'self'", *hashes]) + "; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
    )

    from .timing import Timing

    app.state.auth_pool = None  # the lifespan's own, while it runs; outside it sign-in uses the loop's default pool
    app.state.needs_lane = None  # the same for Needs you's builds (backend/needs_lane.py)
    app.state.timing = Timing()
    app.state.timing.recorder = recorder
    app.state.flight = recorder

    from .assistant import write_allowed as assistant_writes, create_proposal
    from .assistant import valid_operation as assistant_valid
    from .assistant import instruction as assistant_instruction
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
            people = (row["owner_id"], row["requester_id"], H.task_origin(c, row))
            if shared:
                return all(a == actor or not str(a).startswith("human:") for a in people)
            if row["owner_id"] != actor:
                return False
            # Alone: no bot and no other person on it (a human requester is another person, who would see the comment).
            if any(str(a).startswith("bot:") or (str(a).startswith("human:") and a != actor) for a in people):
                return False
            return not c.execute(
                "SELECT 1 FROM task_delegations WHERE task_id=? AND expires_at>?", (task_id, H.now())
            ).fetchone()

    def assistant_to_bot(name):
        with store.read() as c:
            return str(H.resolve_actor(c, name) or "").startswith("bot:")

    def assistant_direct():
        with store.read() as c:
            return team_rules.load(c)["assistant_direct"]

    def assistant_confirm_hint():
        """What a write the person's Assistant may not make on its own is refused with (`confirm_required`)."""
        return (
            "The " + settings.assistant_name + " does not make this on its own: it is a credential, sign-in, owner, "
            "support or deletion change, one of its own switches, or the owner turned off \u201cAssistant acts without "
            "asking\u201d. Propose it with `tico assistant propose` (or POST /api/v2/assistant/actions) and the person "
            "confirms a card in " + settings.app_name + "."
        )

    @app.middleware("http")
    async def request_guard(request, call_next):
        via_reset = delegation_reset = run_reset = cause_reset = token_reset = None
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
            # The setup handoff's one trade: the owner's agent holds a one-time secret and no token yet (backend/setup_handoff.py).
            if request.url.path == setup_handoff.PATH and request.method == "POST":
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
            # The server's icons (backend/brand_icons.py): the two root icons and the brand files a page links, and nothing
            # else under /assets/.
            if request.method in ("GET", "HEAD") and brand_icons.public(request.url.path):
                return brand_icons.harden(await call_next(request))
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
            # A file source's signed address (backend/growth.py): its HMAC signature is the right to post.
            if request.url.path.startswith("/api/v2/growth/in/") and request.method == "POST":
                return await call_next(request)
            # A listening's push address carries its own token (backend/listening.py).
            if request.url.path.startswith(LISTENING_PUSH_PATH) and request.method == "POST":
                return await call_next(request)
            # A proxied tool call (backend/tool_proxy.py) carries its own signed token where the vendor's key goes, and
            # no Tico identity: the route refuses anything not signed for that tool and a live run.
            if request.url.path.startswith(TOOL_PROXY_PATH):
                return await call_next(request)
            # An agent signing in to the MCP server over OAuth (backend/mcp_oauth.py): the metadata it reads first, and
            # the registration, token and revocation endpoints, which carry their own codes and secrets.
            if request.url.path.startswith(
                ("/api/v2/oauth/", "/.well-known/oauth-", "/.well-known/openid-configuration/")
            ) and (request.method == "GET" or request.url.path.endswith(("/register", "/token", "/revoke"))):
                return await call_next(request)
            # SCIM carries its own bearer token, which an identity provider holds (backend/scim.py).
            if request.url.path.startswith("/scim/v2/"):
                return await call_next(request)
            # A Hermes profile with no credential pairs itself (backend/agents.py): it asks for a code, polls with its own
            # secret, and fetches the source-available connector it runs. The person's approval is the authenticated step.
            # Tools > Settings > Email's setup command (backend/google_workspace.py): the script, and the key upload and its keep
            # check when they carry the command's code. Nothing else passes without a session. The body is read here,
            # bounded, the way the relay's is, and the route reads it again from the buffer.
            from . import google_workspace as workspace_setup

            if workspace_setup.exempt(request):
                chunks, total = [], 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > workspace_setup.KEY_BYTES:
                        raise Problem("too_large", "The setup upload exceeds its limit", 413)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
                return await call_next(request)
            # A person's skills helper with no token yet (backend/skill_devices.py): it asks for a code and polls for its
            # token with its own secret, and its source is served to anyone. The person's approval is the signed-in step.
            from . import skill_devices

            if (
                skill_devices.public(request)
                or request.url.path == "/api/v2/skills/helper.mjs"
                and request.method == "GET"
            ):
                return await call_next(request)
            agent_door = (
                request.url.path == "/api/v2/agents/pairings"
                and request.method == "POST"
                or request.url.path.startswith("/api/v2/agents/pairings/")
                and request.method == "GET"
                or request.url.path in ("/api/v2/agents/setup-script", "/api/v2/agents/sync-skill")
                and request.method == "GET"
            )
            if request.url.path != "/api/v2/runners/enroll" and not agent_door:
                began = time.perf_counter()
                try:
                    who = await asyncio.get_running_loop().run_in_executor(
                        app.state.auth_pool, auth.authenticate, request.headers, request.url.path, request.method
                    )
                except Problem as exc:
                    # GitHub's return for the App's setup (backend/github_app.py) comes in the browser, which may hold no owner
                    # session: only the missing identity passes, and the route parks the code. Any other refusal stands.
                    if (
                        exc.code != "identity"
                        or request.url.path != GITHUB_APP_CALLBACK_PATH
                        or request.method != "GET"
                    ):
                        raise
                    return await call_next(request)
                if who.support:
                    # Again on the path as sent, still percent-encoded: the gate then reads the address the router
                    # will, not a decoded one that could differ from it.
                    from . import support_access

                    raw = request.scope.get("raw_path") or b""
                    support_access.gate(who, raw.decode("latin-1") if raw else request.url.path, request.method)
                if who.view:
                    # A bot's read-only view (backend/app_view.py), on the path as sent as well.
                    raw = request.scope.get("raw_path") or b""
                    app_view.gate(raw.decode("latin-1") if raw else request.url.path, request.method)
                if who.via == "assistant" and not who.confirmed and not who.task_actor and not who.attempt_id:
                    who = replace(who, task_actor="bot:" + settings.assistant_bot)
                request.state.auth_ms = (time.perf_counter() - began) * 1000
                request.state.identity = who
                app.state.sleep_idle.seen(who, request.url.path)
                # A locked Tico Cloud company (backend/cloud_access.py): people reach only billing and the download.
                cloud_access.gate(store, who, request.url.path)
                # The Rules Owner sends nothing outside Tico (backend/counsel.py OUTWARD): refused on the server, not only in its tools.
                if counsel.is_counsel(who) and await asyncio.get_running_loop().run_in_executor(
                    None, counsel.refused, store, who, request.url.path, request.method
                ):
                    raise Problem("forbidden", counsel.REFUSED, 403)
                # The Rules Owner calls only its own routes (backend/counsel.py COUNSEL_ROUTES), and a Rules Owner run reads on them
                # only its own task, its read's document version and its own run (read_refused).
                if counsel.is_counsel(who) and (
                    reason := await asyncio.get_running_loop().run_in_executor(
                        None,
                        counsel.read_refused,
                        store,
                        auth,
                        who,
                        request.method,
                        request.url.path,
                        dict(request.query_params),
                    )
                ):
                    raise Problem("privacy", reason, 403)
                if census.person_due(who):
                    await asyncio.get_running_loop().run_in_executor(None, census.note_person, who)
                # Which Recorder a person runs (backend/recorder_installs.py): a write only when one is due.
                if (recorder := app.state.recorder_seen.due(who, request.headers)) is not None:
                    await asyncio.get_running_loop().run_in_executor(None, app.state.recorder_seen.note, *recorder)
                if who.via:
                    # The Assistant acting for a person (backend/assistant.py): what it writes is
                    # recorded via assistant, and unless the person just confirmed a proposal it may
                    # write only what is low-risk.
                    via_reset = H.VIA.set(who.via)
                elif who.via_token:
                    # A personal token: what it writes in the person's name is not by their hand (H.delegated).
                    token_reset = H.BY_TOKEN.set(True)
                if (
                    who.attempt_id
                    and who.role != "runner"
                    and not request.url.path.startswith(run_triggers.RUNNER_PATHS)
                ):
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
                upload = request.url.path in (
                    "/api/notes",
                    "/api/send",
                    "/api/v2/uploads/tasks",
                    "/api/v2/uploads/assistant/messages",
                    "/api/v2/meetings/import",
                    "/api/v2/growth/customers",  # customer facts as CSV: 100,000 customers is about 7 MB
                ) or re.fullmatch(
                    r"/api/(?:meetings/[^/]+/send|v2/uploads/chat/[^/]+|v2/uploads/threads/[^/]+/messages)",
                    request.url.path,
                )
                docs_import = request.url.path in (
                    "/api/v2/docs/import",
                    "/api/v2/market/import",
                ) and request.state.identity.role in ("human", "owner", "bot")
                # A bot's computer publishes files up to 25 MB as raw bytes (backend/files.py).
                published = request.url.path in (
                    "/api/v2/files/uploads",
                    "/api/v2/files/imports",
                ) and request.state.identity.role in ("bot", "runner")
                # A run's raw provider stream: up to 5 MB of frames, escaped once more as a JSON string.
                captured = (
                    request.url.path.startswith("/api/v2/attempts/")
                    and request.url.path.endswith("/capture")
                    and request.state.identity.role == "runner"
                )
                memory_report = (
                    bool(re.fullmatch(r"/api/v2/bots/[^/]+/memory/report", request.url.path))
                    and request.state.identity.role == "runner"
                )
                # A bot's video or audio file streams to disk up to the server's upload limit (backend/files.py).
                media_raw = published and raw_media(request, imported=request.url.path == "/api/v2/files/imports")
                streamed = media_raw or bool(
                    re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path)
                    and request.headers.get("content-type", "").lower().startswith("multipart/form-data")
                )
                # The recorder's speech-to-text clip (backend/recorder_relay.py): up to 25 MB, from a person.
                recorder_audio = (
                    request.url.path == "/api/v2/recorder/audio/transcriptions"
                    and request.state.identity.role in ("human", "owner")
                )
                limit = (
                    1_000_000
                    if re.fullmatch(r"/api/v2/live-meetings/[^/]+/screen/frame", request.url.path)
                    else 26_214_400
                    if recorder_audio
                    else 1024 * 1024
                    if request.url.path == "/api/v2/team/icon"
                    else 2 * 1024 * 1024
                    if request.url.path == "/api/v2/company-page/logo"
                    else settings.upload_max_bytes
                    if media_raw
                    else settings.upload_max_bytes + 11_000_000
                    if streamed
                    else 14_500_000
                    if re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path)
                    else MAX_REPORT_BYTES
                    if memory_report
                    else 27_000_000
                    if published
                    else 12_000_000
                    if captured
                    else 20_000_000
                    if docs_import or upload and request.state.identity.role in ("human", "owner")
                    else 14_200_000  # a website tool's upload, 10 MB in base64 (backend/browser_commands.py)
                    if request.url.path == "/api/v2/browser/commands"
                    else 20_100_000  # a website tool's download, from the computer
                    if re.fullmatch(r"/api/v2/runner-browser-commands/[^/]+/file", request.url.path)
                    else 2_000_000
                )
                if int(size) > limit:
                    raise Problem(
                        "too_large",
                        f"Upload exceeds the file limit of {settings.upload_max_bytes} bytes"
                        if streamed
                        else "Request exceeds the upload limit",
                        413,
                    )
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
                            raise Problem(
                                "too_large", f"Upload exceeds the file limit of {settings.upload_max_bytes} bytes", 413
                            )
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
                if (
                    getattr(request.state, "identity", None)
                    and request.state.identity.via
                    and not request.state.identity.confirmed
                    and not await asyncio.to_thread(
                        lambda: assistant_writes(
                            request.method,
                            request.url.path,
                            settings,
                            getattr(request, "_body", b""),
                            request.state.identity.actor,
                            lambda tid, shared: assistant_owns(tid, request.state.identity.actor, shared),
                            assistant_direct(),
                            assistant_to_bot,
                        )
                    )
                ):
                    # A write the person's own Assistant run makes (backend/assistant.py): the run cites the message that
                    # started it (its turn), unless the request names another (`X-Tico-On-Behalf-Of`), and the write runs as
                    # the person, recorded via assistant for that message. Their rights are the ceiling: the route checks them.
                    header = request.headers.get(botops_act.HEADER)
                    asked = header or "turn"
                    raw = getattr(request, "_body", b"")
                    # A setting from the catalogue is refused here only when only the person may change it (class `person`,
                    # `require_direct_setting`).
                    setting = request.state.identity.via == "assistant" and assistant_mod.catalogue_setting(
                        request.method, request.url.path, raw
                    )
                    # The owner's yes to a goal read-back in their own Assistant chat (backend/goal_read_back.py): the
                    # goal, measure and suggestion routes save exactly what it read back, or nothing (`not_agreed`).
                    goal_yes = goal_read_back.strategy_turn(request.state.identity) and goal_read_back.saving(
                        request.method, request.url.path
                    )
                    # The owner's "no" to a suggestion in their chat (backend/goal_read_back.py `turns_down`): admitted on
                    # their cited turn, and not on the autonomous-action setting, which only gates the other writes.
                    goal_no = (
                        asked
                        and request.state.identity.via == "assistant"
                        and goal_read_back.turns_down(request.method, request.url.path)
                    )
                    if goal_yes and not header:
                        raise goal_read_back.not_agreed(
                            "A goal change is saved only on the owner's yes to its "
                            "read-back, citing it (`--on-behalf-of turn`)"
                        )
                    # Every write the turn makes is admitted on its cited words, except what the Assistant may not do as the
                    # person (`admits_on_turn`).
                    # A settings route's write that is not exactly one catalogue setting, and a change to an automation that
                    # sends outside, are cards whatever the rest of the turn may do (backend/assistant.py).
                    on_turn = request.state.identity.via == "assistant"
                    unmatched = on_turn and assistant_mod.settings_route_unmatched(
                        request.method, request.url.path, raw
                    )
                    outside = on_turn and await asyncio.to_thread(
                        assistant_mod.automation_needs_confirm, store, request.method, request.url.path, raw
                    )
                    admitted = (
                        request.state.identity.via == "assistant"
                        and assistant_direct()
                        and not unmatched
                        and not outside
                        and assistant_mod.admits_on_turn(request.method, request.url.path, raw)
                    )
                    if not goal_yes and not goal_no and not admitted:
                        raise Problem("confirm_required", assistant_confirm_hint(), 403)

                    def cited():
                        with store.read() as c:
                            if goal_yes:
                                try:
                                    record = assistant_instruction(c, request.state.identity, asked)
                                except Problem as exc:
                                    raise goal_read_back.not_agreed(exc.detail.split(";")[0]) from None
                                # Recorded on every save as the owner, via their Assistant, citing their yes and the
                                # read-back it answered.
                                return {
                                    **record,
                                    "assistant": request.state.identity.task_actor,
                                    **goal_read_back.agreement(
                                        c,
                                        request.state.identity,
                                        record["cited"],
                                        request.method,
                                        request.url.path,
                                        raw,
                                        auth=auth,
                                    ),
                                }
                            try:
                                record = assistant_instruction(c, request.state.identity, asked)
                            except Problem:
                                if header:
                                    raise
                                # No message of the person's own is behind this run, so it is a card, as it always was.
                                raise Problem("confirm_required", assistant_confirm_hint(), 403) from None
                            assistant_mod.require_on_task(c, auth, request.state.identity, request.url.path)
                            if setting:

                                def trial(e, target, body):
                                    # The route's own rule (repositories.needs_card), tried and rolled back.
                                    from . import repositories

                                    with store.transaction() as tc:
                                        return repositories.card_words(
                                            tc, settings, auth, request.state.identity, target["bot"], body
                                        )[0]

                                assistant_mod.require_direct_setting(
                                    c, request.method, request.url.path, raw, trial, settings.public_url
                                )
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
            if (
                not on_behalf
                and getattr(request.state, "identity", None) is not None
                and request.state.identity.actor == "bot:" + BOTOPS
                and request.url.path.startswith("/api/v2/")
                and request.url.path not in ("/api/v2/me", "/api/v2/mcp")
            ):
                body = botops_act.parse_body(getattr(request, "_body", b""))
                on_behalf = (body.get("on_behalf_of") if isinstance(body, dict) else None) or request.query_params.get(
                    "on_behalf_of"
                )
                # The default applies only to what BotOps does for a person (the delegable routes). Its own run's
                # plumbing (credentials, attempts, jobs, its status) and its own messages stay BotOps': a run's
                # credential fetch as the person was refused, and every human-requested turn failed to start.
                if not on_behalf and botops_act.default_delegable(
                    request.method, request.url.path, body, request.query_params
                ):
                    on_behalf = "default"
            if (
                on_behalf
                and getattr(request.state, "identity", None) is not None
                and request.state.identity.actor == "bot:" + BOTOPS
                and botops_owns_task(request.url.path, botops_act.parse_body(getattr(request, "_body", b"")))
            ):
                on_behalf = None  # BotOps' own task: its own words, with its own rights
            if on_behalf and getattr(request.state, "identity", None) is not None:
                # BotOps doing what the person who asked it could do in the app (backend/botops_act.py).
                early, acted = await asyncio.get_running_loop().run_in_executor(
                    None, act_for_requester, request, on_behalf
                )
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
            route_keeps = (
                request.url.path.startswith("/api/")
                and request.method == "GET"
                and response.status_code in (200, 302)
                and str(response.headers.get("cache-control", "")).startswith("private, max-age=")
            )
            file_bytes = (
                request.method in ("GET", "HEAD")
                and re.fullmatch(r"/api/v2/files/[^/]+(?:/(?:poster|thumb|versions/[0-9]+))?", request.url.path)
                and response.status_code in (200, 206, 302, 304, 416)
                and "cache-control" in response.headers
            )
            if route_keeps or file_bytes:
                pass  # the route's own "private, max-age=…" stands
            elif (
                ui_bundle.is_part(request.url.path)
                and response.status_code in (200, 304)
                and response.headers.get("cache-control") in (ui_bundle.IMMUTABLE, "no-store")
            ):
                pass  # the bundle route keeps current bytes, never another build under an old URL
            elif (
                request.query_params.get("v")
                and response.status_code in (200, 304)
                and re.fullmatch(r"/(?:tico/ui/)?vendor/fonts/[\w.-]+\.woff2", request.url.path)
            ):
                # A font under its hashed URL (scripts/build-*-font.py): kept for a year. Inter's font-display is
                # optional, so a font that had to be revalidated first could miss the first paint.
                response.headers["Cache-Control"] = ui_bundle.IMMUTABLE
            elif (
                request.query_params.get("v")
                and response.status_code in (200, 304)
                and ui_bundle.versioned(settings.ui_dir, request.url.path, request.query_params.get("v"))
            ):
                # theme.ts and marked under the `?v=` the page names them with (ui_bundle STANDALONE): kept for a
                # year, so an open needs no round trip for them; theme.ts holds up the first paint.
                response.headers["Cache-Control"] = ui_bundle.IMMUTABLE
            elif not request.url.path.startswith("/api") and "etag" in response.headers:
                response.headers["Cache-Control"] = (
                    "private, max-age=3600" if request.url.path.startswith("/assets/") else "private, no-cache"
                )
            else:
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            response.headers["CDN-Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            # The page frames its own PDF previews, and nothing else frames it.
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            if "content-security-policy" not in response.headers:
                page = not request.url.path.startswith("/api") and response.headers.get("content-type", "").startswith(
                    "text/html"
                )
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
            if (
                exc.status == 401
                and auth.proxy
                and auth.proxy.name == "oidc"
                and request.method in ("GET", "HEAD")
                and not hasattr(request.state, "identity")
                and not path.startswith(("/api", "/mcp"))
                and "authorization" not in request.headers
                and "text/html" in request.headers.get("accept", "")
            ):
                back = path + ("?" + request.url.query if request.url.query else "")
                return RedirectResponse(
                    auth.proxy.sign_in_path + "?" + urlencode({"next": oidc.safe_next(back)}), status_code=302
                )
            if (
                exc.status == 401
                and auth.proxy
                and auth.proxy.name == owner_link.KIND
                and request.method in ("GET", "HEAD")
                and not hasattr(request.state, "identity")
                and not path.startswith(("/api", "/mcp"))
                and "authorization" not in request.headers
                and "text/html" in request.headers.get("accept", "")
            ):
                return RedirectResponse(owner_link.LINK_PATH, status_code=302)
            response = await problem_handler(request, exc)
            if exc.status == 401 and path == mcp_oauth.MCP_PATH:
                # Where an agent with no token can sign in (backend/mcp_oauth.py, RFC 9728).
                response.headers["WWW-Authenticate"] = mcp_oauth.challenge_header(settings)
            return response
        except ClientDisconnect:
            # The caller hung up while its request body was read (a closed tab, a canceled call): nothing failed here.
            # It ends as 499, client closed request, which the flight recorder and Sentry don't count as a server error.
            # The method only: some addresses carry their own secret (a webhook trigger's).
            logging.getLogger("tico.request").info(
                "%s request: the client disconnected before its body was read", request.method
            )
            return Response(status_code=499)
        except Exception as exc:
            from .diagnostics import request_failure

            request_failure(request, 500, exc)
            telemetry.capture("request", exc, 500, route=error_route(request))
            if setup_failures.step(request.method, getattr(request.scope.get("route"), "path", "")):
                await asyncio.get_running_loop().run_in_executor(
                    None, setup_failures.record, store, request, "server_error", 500, None, exc
                )
            raise
        finally:
            if via_reset is not None:
                H.VIA.reset(via_reset)
            if token_reset is not None:
                H.BY_TOKEN.reset(token_reset)
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
        row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
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
            declared = H._json(row["config"], {}) if row else {}
            if (
                (H.bot(c, bot) or {}).get("state") == "planned"
                and isinstance(declared, dict)
                and declared.get("template")
            ):
                return
            raise Problem(
                "forbidden", "BotOps sets routines only as the person who asked it, or on a bot it is building", 403
            )
        if auth.bot_manager(c, who, bot) or auth.operator(c, who, bot):
            return
        raise Problem("forbidden", "You cannot change this bot's routines", 403)

    @app.middleware("http")
    async def request_timing(request, call_next):
        started = time.perf_counter()
        request.state.auth_ms = 0.0
        app.state.timing.begin()
        # This request's SQL time and write-lock wait, added to by the threads it runs on (backend/flight.py).
        cost, cost_token = (
            flight.request_started(request.method, request.scope) if recorder is not None else (None, None)
        )
        route = request.url.path
        response = None
        try:
            response = await call_next(request)
            found = request.scope.get("route")
            route = getattr(found, "path", None) or ("static" if not route.startswith("/api") else "unmatched")
            total = (time.perf_counter() - started) * 1000
            extra = getattr(request.state, "server_timing", "")
            response.headers["Server-Timing"] = (
                f"auth;dur={request.state.auth_ms:.1f}, app;dur={total:.1f}"
                + (f", sql;dur={cost[0]:.1f}, wait;dur={cost[1]:.1f}" if cost is not None else "")
                + (", " + extra if extra else "")
            )
            return response
        finally:
            found = request.scope.get("route")
            key = (
                request.method
                + " "
                + (
                    getattr(found, "path", None)
                    or ("static" if not request.url.path.startswith("/api") else "unmatched")
                )
            )
            # The flight recorder's caller kind, status and size (backend/flight.py); a raised error counts as a 500.
            who = getattr(request.state, "identity", None)
            size = response.headers.get("content-length") if response is not None else None
            # Screen long-polls wait for new pixels; retain SQL/lock/processing time, exclude only deliberate sleeps.
            active_ms = max(0, (time.perf_counter() - started) * 1000 - getattr(request.state, "idle_wait_ms", 0))
            app.state.timing.end(
                key,
                request.state.auth_ms,
                active_ms,
                flight.caller_kind(who),
                response.status_code if response is not None else 500,
                int(size) if size and size.isdigit() else 0,
                getattr(who, "actor", "") or "",
                cost,
            )
            if cost_token is not None:
                flight.request_finished(cost_token)

    async def display_names(request, response):
        """Stable v2 answers carry display names beside actor ids (backend/names.py)."""
        # Only API JSON is editable. FileResponse, StreamingResponse and raw byte Response
        # objects must keep their bodies, lengths and digests, whatever their content type.
        if (
            not isinstance(response, JSONResponse)
            or request.method == "HEAD"
            or response.status_code != 200
            or "content-disposition" in response.headers
            or "x-content-sha256" in response.headers
            or "count" in request.query_params
            or not STABLE_PATH.fullmatch(request.url.path)
            or "application/json" not in response.headers.get("content-type", "")
        ):
            return response
        raw = response.body
        who = getattr(request.state, "identity", None)

        def work():
            with store.read() as c:
                return actor_names.annotate_json(
                    c, raw, render_notices=bool(who and who.role in ("human", "owner")), actors=request.method == "GET"
                )

        try:
            body = await asyncio.get_running_loop().run_in_executor(None, work)
        except sqlite3.Error:
            body = None
        if body is not None:
            response.body = body
            response.headers["Content-Length"] = str(len(body))
        return response

    def run_names(request, response):
        """An answer's `attempt_id` keys read `run_id`: storage's attempt is the contract's run (backend/runs.py).
        File downloads and other raw bytes keep theirs."""
        body = getattr(response, "body", None)
        if (
            not body
            or "content-disposition" in response.headers
            or "x-content-sha256" in response.headers
            or "content-encoding" in response.headers
            or "application/json" not in response.headers.get("content-type", "")
        ):
            return response
        renamed = runs.public_json(body, request.url.path)
        if renamed is not body:
            response.body = renamed
            response.headers["Content-Length"] = str(len(renamed))
        return response

    class DisplayNameRoute(APIRoute):
        def __init__(self, path, endpoint, **options):
            # FastAPI calls the handler it was given; the JSON it answers is encoded by orjson (backend/fast_json.py).
            # `endpoint` stays the handler itself, for the callers that read its dicts (backend/route_renames.py).
            plain = isinstance(options.get("response_class", DefaultPlaceholder(None)), DefaultPlaceholder)
            super().__init__(
                path, fast_json.answering_json(endpoint, options.get("status_code")) if plain else endpoint, **options
            )
            self.endpoint = endpoint

        def get_route_handler(self):
            handler = super().get_route_handler()

            async def named_response(request):
                from .json_response import compress

                return await compress(request, run_names(request, await display_names(request, await handler(request))))

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

    def mutate(request, body, fn, check=None, durable=False, retry_body=None):
        def current_access(c):
            # Privacy can narrow after middleware preflight, including before a cached retry.
            source = getattr(request.state, "privacy_source", request.state.identity)
            privacy.guard_write(c, source, request.url.path, auth)
            if check:
                check(c)
            batch_path = re.fullmatch(r"/api/v2/batch/([^/]+)/(next|respond|commit)", request.url.path)
            if batch_path:
                row = c.execute(
                    "SELECT * FROM batches WHERE id=? AND person=?", (batch_path[1], request.state.identity.actor)
                ).fetchone()
                privacy.require_batch(c, request.state.identity, row)

        def protected_write(c):
            source = getattr(request.state, "privacy_source", request.state.identity)
            token = H.PRIVATE_WRITE.set(H.PRIVATE_WRITE.get() or privacy.private_execution(c, source))
            path = org_rule_check.PATH.set(request.url.path)  # a hold keeps its route, for Send anyway's guard
            try:
                # Only bot refusals count; human-only routes also address tasks in the trash.
                tid = request.path_params.get("tid") if H.is_bot(request.state.identity.actor) else None
                task_id = auth.resolve_task(c, request.state.identity, tid) if tid else None
                with H.refusal_task(task_id):
                    if task_id:
                        H._writer(c, request.state.identity.actor, task_id=task_id)
                    return fn(c)
            except H.Refused as exc:
                exc.private = getattr(exc, "private", False) or H.PRIVATE_WRITE.get()
                raise
            finally:
                org_rule_check.PATH.reset(path)
                H.PRIVATE_WRITE.reset(token)

        # Money writes and approval decisions pass `durable`; every other keyed write keeps the call it always made.
        money = {"durable": True} if durable else {}
        if retry_body:
            money["retry_body"] = retry_body
        result = store.mutate(
            request.state.identity,
            request.url.path,
            request.headers.get("idempotency-key"),
            body.model_dump(),
            protected_write,
            check=current_access,
            **money,
        )

        if isinstance(result, dict) and "_refusal" in result:
            refusal = result["_refusal"]
            raise Problem(
                refusal["code"],
                refusal["detail"],
                refusal["status"],
                extra={**(refusal.get("extra") or {}), "idempotency_refused": True},
            )
        with store.read() as c:
            privacy.require_payload(c, request.state.identity, result)
        return result

    # The columns of a task's row that are the server's own, never in a view.
    SERVER_COLUMNS = ("checkpoint_state", "work_revision", "occurrence_step", "rule_due_id")

    def plain_view(row, c=None):
        """A task's plain fields, as every view of it starts: the row, without the server's own columns."""
        value = {
            "id": row["id"],
            "short_id": row["id"][:8],
            **{k: v for k, v in row.items() if k not in SERVER_COLUMNS},
            "acceptance_criteria": json.loads(row.get("acceptance") or "[]"),
            "labels": H.task_labels(row),
            "lane": row.get("lane") or "company",
            "private": H.task_private(c, row),
            "cover": None,
        }
        value.pop("acceptance", None)
        value["readers"] = H.task_readers(row) if value["private"] else []
        if not value.get("occurrence"):
            value.pop("occurrence", None)  # only a due-date routine's task has one: a board of 500 carries none
        return value

    def task_view(row, c=None, visible_sql="1", who=None):
        """The task as JSON, full (docs/dev/pipelines-v3-cutover.md, "The task as JSON"): its plain fields with the
        engine's (H.task_view: where it is, whose turn it is, what it takes to leave its stage, for this reader) laid
        over them. Without a connection, the plain fields alone."""
        if c is not None:
            H.hydrate_task_tags(c, [row])
        value = plain_view(row, c)
        if c is None:
            return value
        viewer = (who.task_actor or who.actor) if who is not None else None
        # Another task it names is shown under the reader's whole rule, the one a direct read of that task uses.
        rule = auth.task_sql(c, who) if who is not None and visible_sql == "1" else visible_sql
        value.update(H.task_view(c, row["id"], viewer, rule, inputs=True) or {})
        # The task above it by id only to a reader of that task: anyone else sees "a private task" under relations.
        parent = TR.parent_of(c, row["id"])
        value["parent_id"] = (
            parent
            if parent and c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({rule})", (parent,)).fetchone()
            else None
        )
        value["relations"] = TR.grouped(c, [row["id"]], visible_sql)[row["id"]]
        value["roles"] = TRo.roles_of(c, row["id"])
        from .roadmaps import stats, EMPTY_STATS

        value.update({**EMPTY_STATS, **stats(c, [row["id"]]).get(row["id"], {})})
        routine = c.execute("SELECT schedule_id FROM schedule_occurrences WHERE task_id=?", (row["id"],)).fetchone()
        if routine:
            value.update(routine_id=routine["schedule_id"])
        value["origin_actor"] = H.task_origin(c, row)
        if filed := SRC.filers(c, [row["id"]]).get(row["id"]):
            value["filer"] = filed
        if who is not None and visible_sql != "1":
            value["from_person"] = views.person_origin(c, row, visible_sql, who.actor)
        from . import task_reads

        value["cover"] = task_reads.covers(c, [row["id"]]).get(row["id"])
        from .blobs import brief

        value["attachments"] = [
            brief(r)
            for r in c.execute(
                "SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=?", (row["id"],)
            )
        ]
        value["links"] = H.task_links(c, row["id"])
        value["children_summary"] = H.children_summary(c, row["id"], visible_sql)
        value["pr_state"] = H.pr_state(value["links"])
        counts = c.execute(
            "SELECT COUNT(*), SUM(status IN ('done','closed')) FROM tasks WHERE id IN ("
            + TR.CHILD_IDS
            + ") AND ("
            + visible_sql
            + ")",
            (row["id"],),
        ).fetchone()
        value["parts"] = {"total": counts[0] or 0, "done": counts[1] or 0}
        if who is not None:
            from .files import readable_page_refs

            value = readable_page_refs(c, auth, who, value)
            value = teammate_access.Known(c, who, auth).task(value)  # people the reader may not see
        return value

    def task_views(rows, c, who, visible_sql="1"):
        """One task-list page, brief (docs/dev/pipelines-v3-cutover.md, "The task as JSON"): each row's plain fields
        and the engine's brief ones (turn, late, snoozed), with a fixed number of reads for the whole page. `rows`
        were read on `c` under `visible_sql`, the reader's rule, as every list does."""
        if not rows:
            return []
        H.hydrate_task_tags(c, rows)
        ids = [row["id"] for row in rows]
        from .roadmaps import stats, EMPTY_STATS

        requester_stats = stats(c, ids)
        for row in rows:
            row.update({**EMPTY_STATS, **requester_stats.get(row["id"], {})})
        marks = ",".join("?" * len(ids))
        from . import task_reads

        covers = task_reads.covers(c, ids)
        # From the page's ids, with visibility checked per child: an `IN (SELECT id FROM tasks WHERE ...)`
        # list made SQLite probe every visible task against the page (1.5 s at 30k tasks).
        parts = {
            row["parent_id"]: {"total": row["total"] or 0, "done": row["done"] or 0}
            for row in c.execute(
                f"SELECT r.to_task parent_id,COUNT(*) total,SUM(t.status IN ('done','closed')) done "
                f"FROM task_relations r INDEXED BY task_relations_to JOIN tasks t ON t.id=r.from_task "
                f"WHERE r.to_task IN ({marks}) AND r.kind='parent' "
                f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.from_task AND ({visible_sql})) "
                f"GROUP BY r.to_task",
                ids,
            )
        }
        # The task above each row, by id only where this reader reads it (the rows' own rule).
        parents = {
            r[0]: r[1]
            for r in c.execute(
                f"SELECT r.from_task, r.to_task FROM task_relations r WHERE r.kind='parent' AND r.from_task IN ({marks}) "
                f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.to_task AND ({visible_sql}))",
                ids,
            )
        }
        relations = TR.grouped(c, ids, visible_sql)
        roles = TRo.grouped(c, ids)
        routines_by_task = {
            row["task_id"]: row["schedule_id"]
            for row in c.execute(
                f"SELECT task_id,schedule_id FROM schedule_occurrences WHERE task_id IN ({marks})", ids
            )
        }

        links = {tid: [] for tid in ids}
        for link in c.execute(
            f"SELECT id,task_id,kind,url,title,state,added_by,created_at,pr_sha,pr_merged_at,repo,number,branch,"
            f"computer_id,path,checks,mergeable,review_state,pending_comments,updated_at "
            f"FROM task_links WHERE task_id IN ({marks}) ORDER BY created_at",
            ids,
        ):
            # Not a link's record, its empty columns or its own write time (a computer's heartbeat rewrites that every
            # minute): the task's read and its /links have them, and every open tab gets each list change.
            links[link["task_id"]].append(
                {k: v for k, v in dict(link).items() if v is not None and k not in ("detail", "pr_sha", "updated_at")}
            )

        attachments = {tid: [] for tid in ids}
        from .blobs import brief

        for asset in c.execute(
            f"SELECT a.task_id,b.* FROM task_assets a JOIN blobs b ON b.id=a.blob_id WHERE a.task_id IN ({marks})", ids
        ):
            attachments[asset["task_id"]].append(brief(asset))

        origins = {}
        for event in c.execute(
            f"SELECT target,detail FROM events WHERE actor=? AND action='task.origin' "
            f"AND target IN ({marks}) ORDER BY target,created_at DESC",
            (H.KEEPER, *ids),
        ):
            if event["target"] in origins:
                continue
            origin = (H._json(event["detail"], {}) or {}).get("bot")
            origins[event["target"]] = origin if H.is_bot(origin) else None

        filed = SRC.filers(c, ids)
        summaries = H.children_summaries(c, ids, visible_sql)
        # The rows each row's from_person reads, read for the whole page first; each row is still decided one at a
        # time by the same code below.
        views.prime_list_origins(c, visible_sql, rows)
        task_reads.brief(c, rows, visible_sql)
        result = []
        for row in rows:
            value = plain_view(row, c)
            value.pop("checkpoint", None)  # up to a page of text per task; the full task view carries it
            value.update(
                {
                    "parent_id": parents.get(row["id"]),
                    "relations": relations[row["id"]],
                    "roles": roles[row["id"]],
                    "parts": parts.get(row["id"], {"total": 0, "done": 0}),
                    "links": links[row["id"]],
                    "children_summary": summaries[row["id"]],
                    "pr_state": H.pr_state(links[row["id"]]),
                    "attachments": attachments[row["id"]],
                    "cover": covers.get(row["id"]),
                    "origin_actor": origins.get(row["id"]),
                    "from_person": views.person_origin(c, row, visible_sql, who.actor),
                }
            )
            if row["id"] in routines_by_task:
                value["routine_id"] = routines_by_task[row["id"]]
            if row["id"] in filed:
                value["filer"] = filed[row["id"]]
            result.append(value)
        from .files import readable_page_refs

        result = readable_page_refs(c, auth, who, result)
        return teammate_access.Known(c, who, auth).tasks(result)  # people the reader may not see

    def mover(c, who):
        """Whether this person may move any task: the owner, or a person on a mover team."""
        return who.role == "owner" or who.role == "human" and H.can_move(c, who.actor)

    def visible_tasks(
        c,
        who,
        owner=None,
        requester=None,
        status=None,
        lane=None,
        label=None,
        limit=500,
        offset=0,
        order="queue",
        visible_sql=None,
        narrow=None,
        keyset=False,
        **more,
    ):
        """A page of the tasks `who` may read. `narrow` is a further WHERE fragment over the task's own columns (a
        list's pipeline, stage or pushed-past filter, task_list_routes.narrowing): it picks the rows only, while the
        rows are still read with the reader's own visibility. With `keyset`, the second value is the last row's sort
        key when there is more (the next page's `after`), not an offset."""
        auth.domain(who)
        visible_sql = visible_sql or auth.task_sql(c, who)
        rows = H.tasks(
            c,
            owner=owner,
            requester=requester,
            status=status,
            lane=lane,
            label=label,
            limit=limit + 1,
            offset=offset,
            order=order,
            visible=f"({visible_sql}) AND ({narrow})" if narrow else visible_sql,
            **more,
        )
        page, has_more = rows[:limit], len(rows) > limit
        if keyset:
            return task_views(page, c, who, visible_sql), H.task_sort_key(page[-1], order) if has_more else None
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
        return visuals.serve(
            request,
            store,
            auth,
            app.state.files,
            app.state.blobs,
            visual_key(),
            visual_ancestors(),
            token,
            full=visuals.mode(settings) == "full",
            visual_origin=settings.visual_origin,
        )

    def check_refs(c, who, refs):
        # Validate referenced objects rather than trusting an arbitrary ID in a payload.
        H.check_message_refs(refs)
        for kind, values in refs.items():
            if kind in ("task", "task_id"):
                # One task id, or a list of them (bots send `task:<id>` as a list); each must be one the caller can read.
                # Anything else is refused here, before a SQL bind of it could fail.
                items = values if isinstance(values, list) else [values]
                if any(value and not isinstance(value, str) for value in items):
                    raise Problem("reference", "A task reference is a task id or a list of task ids", 422)
                for value in items:
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
            elif kind != "turn_id":
                raise Problem("reference", f"Unsupported reference kind: {kind}", 422)

    def named_destination(c, who, body, create=True):
        return recipient_gate.named_destination(c, who, body, auth, create=create)

    def send(c, who, body):
        body = routed_message(c, who, body)
        check_refs(c, who, body.refs)
        body = recipient_gate.require_recipient(c, who, body.to, body=body, auth=auth)
        to, refs, conversation_id, way = body.to, body.refs, body.conversation_id, body.delivery
        # Ride-along: a defer while the bot has a turn going in this chat goes in right after that turn.
        if (
            way == "defer"
            and to.startswith("bot:")
            and who.role != "bot"
            and conversation_id
            and delivery.running_in_session(c, H.actor_id(to), [conversation_id])
        ):
            way = "queue"
        if to.startswith("bot:") and way == "queue":
            refs["delivery"] = "queue"  # after the bot's running turn, not into it (execution.inputs)
        if to.startswith("bot:") and way == "defer":
            refs["quiet"] = True  # no job: the session's next turn, or its deadline, takes it
        message = H.say(
            c,
            who.actor,
            to,
            body.text,
            conversation_id=conversation_id,
            kind=body.kind,
            refs=refs,
            in_reply_to=body.in_reply_to,
            wait_s=body.wait_s,
        )
        recipient_gate.require_recipient(c, who, to, body=body, auth=auth, delivered=message)
        due = None
        if to.startswith("bot:") and way == "defer":
            if who.role == "bot":
                # A bot's deferred message rides with the recipient's next run of any kind, as a waiting
                # bot-to-bot message does: its own DM may not see another turn.
                task_ref = H._task_ref(refs.get("task"))
                held_note = bot_to_bot.hold(c, message, H.task(c, task_ref) if task_ref else None)
                due = delivery.defer(c, to, "note", held_note["id"], defer_for=body.defer_for)
            else:
                due = delivery.defer(
                    c,
                    to,
                    "message",
                    message["id"],
                    conversation_id=message["conversation_id"],
                    defer_for=body.defer_for,
                )
        from .chat_goals import current as current_chat_goal

        goal = current_chat_goal(c, message["conversation_id"])
        # Added to the stored references, so the marks H.say set (a bot_to_bot hold, quiet) stay (Dev 328).
        if goal and goal["status"] == "active":
            stored = {
                **(message.get("refs") or {}),
                "goal_context": {"id": goal["id"], "updated_at": goal["updated_at"]},
            }
            c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(stored), message["id"]))
            message = H.message(c, message["id"])
        if body.attachments:
            # The visuals go on the message it was sent as: versions of files in this conversation (backend/visuals.py).
            attached, report = visuals.attach(
                c, who, app.state.files, app.state.blobs, body.attachments, conversation_id=message["conversation_id"]
            )
            stored = {**(message.get("refs") or {}), "attachments": attached}
            c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(stored), message["id"]))
            message = {**visual_view(c, who, [H.message(c, message["id"])])[0], "visuals": report}
        if (goal and goal["status"] == "active") or body.attachments:
            H.held_queue_recheck(c, message["id"])
        # An onboarding bot starts only a person's message or its manager's; anyone else's waits for it to be Active.
        if note := (
            H.held_note(c, to)
            or usage_limits.warning(c, to)
            or bot_stops.warning(c, to)
            or (None if H.is_human(who.actor) else H.setup_hold(c, to, who.actor))
        ):
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
        return {
            "ok": True,
            "service": "tico",
            "protocol": 2,
            "release": settings.release_id,
            "environment_id": settings.environment_id,
        }

    @app.get("/api/v2/config")
    def environment(request: Request):
        """Everything a client needs to name this environment and reach its runner."""
        from .onboarding import config_view

        with store.read_transaction() as c:
            return {
                **config_view(c, settings, request.state.identity),
                "features": {"task_files_multipart": True, "file_media_stream": True},
            }

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
            cutoff = H.shift(H.now(), seconds=-AWAKE_GAP)
            known = runner_versions.load(c)
            for row in c.execute(
                "SELECT id FROM runners_with_state WHERE revoked_at IS NULL AND last_seen_at>?", (cutoff,)
            ):
                state = runner_versions.view(known.get(row["id"]))["state"]
                counts[state] = counts.get(state, 0) + 1
        return counts

    @app.get("/api/v2/system/suggestions")
    def suggestion_settings(request: Request):
        from . import suggestions

        owner_only(request.state.identity, "sees suggestion privacy settings")
        with store.read() as c:
            return suggestions.privacy(c, settings)

    @app.put("/api/v2/system/suggestions")
    def suggestion_settings_save(request: Request, body: M.SuggestionSettings):
        from . import suggestions

        owner_only(request.state.identity, "changes suggestion privacy settings")
        if body.share_stats_for_suggestions is not None:
            person_only(request.state.identity, "consents to sharing suggestion statistics")
        return mutate(
            request,
            body,
            lambda c: suggestions.save_privacy(
                c, settings, request.state.identity.actor, body.model_dump(exclude_none=True)
            ),
        )

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
        found = releases.check_now()
        with store.read() as c:
            return releases.offered(c, found)

    # The updater restarts this process, so the outcome is written by whichever process first
    # sees a final state after a start; a marker row keeps it to one event per update.
    def record_update_outcome(result):
        with store.transaction() as c:
            hosted_updates.record_outcome(c, result)

    providers_routes.register(app, auth, mutate, settings, store, telemetry, updater_only, GATEWAY_IMAGE_SLOTS)

    # ------------------------------------------------------------------ people and access
    def owner_only(who, what):
        auth.domain(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner " + what, 403)

    def admin_only(who, what):
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin " + what, 403)

    billing_routes.register(app, admin_only, auth, mutate, settings, store)

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
            Access.check_may_add(
                c, settings, P.person(H.actor_id(who.actor), roster), auth.company_role(who), email, domains_
            )
            _, row = Access.add_person(
                c,
                who.actor,
                roster,
                name=body.name,
                email=email,
                title=body.title,
                team=body.team,
                reports_to=body.reports_to,
            )
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
        at = invites.record_send(store, who, request, pid)  # a repeat answers the time its own send recorded
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
            if (
                roles
                or any(
                    getattr(body, k) is not None
                    for k in ("add_people", "sign_in", "email", "team", "inbox_bot", "mailbox")
                )
            ) and risky(who):
                changes = body.model_dump(exclude_none=True, exclude={"on_behalf_of"})
                return propose_card(
                    c,
                    who,
                    "POST",
                    "/api/v2/access/people/" + pid,
                    changes,
                    "Change " + ", ".join(changes) + " for " + ((H.human(c, pid) or {}).get("name") or pid),
                )
            roster, lists = views.roster(c), Access.load_access(c, settings)
            row, admins, changed = Access.edit_person(c, who.actor, roster, pid, body, lists, auth.owner_email)
            if changed.get("sign_in") is False:
                c.execute("DELETE FROM oidc_sessions WHERE human=?", (pid,))  # open browsers are signed out now
            if changed.get("team"):
                from . import groups

                groups.leads_follow(c, "person:" + pid)  # a lead who left its group leads it no more
            if admins != lists["admins"]:
                Access.save_access(
                    c,
                    who.actor,
                    {"expected_revision": lists["revision"]},
                    H.now(),
                    bot_admins=admins,
                    settings=settings,
                )
            H.event(c, who.actor, "person.access_edited", pid, changed)
            return {"person": row["id"]}

        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    access_routes.register(
        app, admin_only, auth, mutate, onboarding, owner_only, recruiter, settings, settings_admin, store
    )

    def may_edit_person(c, who, pid):
        """Manage on this person: the same rule `my_access.manage` shows and the Access card's editor asks."""
        from . import teammate_access

        return teammate_access.can(c, who, "manage", "human:" + pid, auth=auth)

    @app.post("/api/v2/people/{pid}")
    def person_update(request: Request, pid: str, body: M.PersonUpdate):
        caller = request.state.identity

        def work(c):
            auth.domain(caller)
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            roster = views.roster(c)
            person = P.person(pid, roster)
            if person and person.get("hidden") and body.left:
                return P.profile(person)  # already off the chart
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
                if (
                    who.role != "owner"
                    and boss
                    and not auth.manages(c, who, "person", boss)
                    and H.actor_id(who.actor) != boss
                ):
                    raise Problem("forbidden", "You can only move people under yourself or your own reports", 403)
                if who.role != "owner" and not boss:
                    raise Problem("forbidden", "Only the owner puts someone at the top of the chart", 403)
            login = body.github.strip().lstrip("@").lower() if body.github is not None else None
            # A review request names one login (backend/github.py), so two people never share one.
            if login and any(
                row["id"] != pid and not row.get("hidden") and str(row.get("github") or "").lower() == login
                for row in roster["people"]
            ):
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
                if body.notify_browser is not None:
                    row = {**row, "notify_browser": body.notify_browser}
                if body.email_digest is not None:
                    row = {**row, "email_digest": body.email_digest}
                if body.slack_muted_bots is not None:
                    row = {**row, "slack_muted_bots": list(dict.fromkeys(body.slack_muted_bots))}
                if body.reports_to is not None:
                    row = {**row, "reports_to": body.reports_to.strip()}
                if body.left:
                    row = Access.leave(c, people, row)
                people[i] = row
                roster = {**roster, "people": people}
                from .report_order import roster_saved

                roster_saved(c, roster)  # a new manager's report goes last there
                c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
                from .finance import actions as FA

                FA.revalidate_org(c)  # Finance's view grants follow the org chart at once (spec 7.5)
                from . import people_mirror

                people_mirror.changed()  # `left` and sign-in changes reach the Tico account
                H.event(
                    c,
                    who.actor,
                    "person.updated",
                    pid,
                    {
                        "title": body.title is not None,
                        "about": body.about is not None,
                        "goals": body.goals is not None,
                        "notes": body.notes is not None,
                        "notify_slack_task_done": body.notify_slack_task_done,
                        "github": body.github is not None,
                        "notify_slack_bot_messages": body.notify_slack_bot_messages,
                        "update_draft": body.update_draft,
                        "notify_browser": body.notify_browser,
                        "email_digest": body.email_digest,
                        "slack_muted_bots": body.slack_muted_bots,
                        "reports_to": body.reports_to,
                        "left": bool(body.left),
                    },
                )
                if caller is not who:
                    H.event(
                        c,
                        caller.actor,
                        "person.update_delegated",
                        pid,
                        {"on_behalf_of": who.actor, "message_id": body.on_behalf_of},
                    )
                return P.profile(row)
            raise Problem("not_found", "Person not found", 404)

        return mutate(request, body, work)

    people_routes.register(app, auth, mutate, settings, store, visual_view)

    # Org rule checks (backend/org_rule_check.py): a bot's output is checked before its write, outside the write's
    # transaction (`rule_check`); the verdict is applied inside it, and a held write writes nothing but its hold.
    def rule_target(c, who, ref):
        try:
            return auth.target(c, who, ref)
        except Problem:
            return H.resolve_actor(c, ref) or str(ref or "")

    def rule_task_audience(c, row, author, text="", *extra):
        """A task write's audience: owner, requester, the people a comment @mentions (each of whom it reaches and may
        add to Who can see: threads.widen), and anyone else named."""
        from . import threads as T

        people = T.mentioned_people(c, text, author) if text else []
        return [a for a in dict.fromkeys((row["owner_id"], row["requester_id"], *people, *extra)) if a]

    def rule_comment_audience(c, who, row, text):
        return rule_task_audience(c, row, who.actor, text)

    def rule_message_to(c, who, body):
        """The actor a message goes to, as `send` writes it: `to: assistant` is the person's Assistant bot."""
        if str(body.to).lower() == "assistant" and who.role in ("human", "owner") and not who.via:
            return assistant_mod.availability(c, settings, who)["bot"]
        return body.to

    def rule_message_audience(c, who, body):
        """A message's audience as the check reads it: its target and its conversation's participants. Both are
        authorized first, as the write authorizes them: a caller who cannot name the target or read the conversation
        gets 404 here, so no check reads the participants and no hold names them."""
        body = routed_message(c, who, body)
        out = {auth.target(c, who, rule_message_to(c, who, body))}
        if body.conversation_id:
            out |= set(auth.conversation(c, who, body.conversation_id).get("participants") or [])
        return sorted(out)

    def rule_message_place(who, body):
        """A message's place, as `where:` reads it: its conversation (none for a direct chat, which is a DM). The
        conversation is authorized first, as `rule_message_audience` does."""

        def place(c, author, recipients):
            if body.conversation_id:
                auth.conversation(c, who, body.conversation_id)
            return org_rule_check.place_for(c, author, recipients, body.conversation_id or "")

        return place

    def chat_place(who, bot):
        """A person's chat with a bot is the direct chat it lands in: its conversation id is the thread `where:` names.
        The chat route names no conversation, so the check finds the open chat (none yet: a DM with no thread). Once the
        bot's chats have moved, that chat is its Home (backend/bot_home_merge.py home_for_chat)."""

        def place(c, author, recipients):
            home = bot_home_merge.home_for_chat(c, who.actor, bot, create=False)
            chat = (views.direct_chat(c, who.actor, bot) or {}).get("id") or ""
            return org_rule_check.place_for(c, author, recipients, home or chat)

        return place

    def home_place(home):
        """A place that is one Home: a write there is in `where: thread:<home>`, and its kind is a thread."""

        def place(c, author, recipients):
            return org_rule_check.place_for(c, author, recipients, home)

        return place

    def task_place(who, tid):
        """A task comment's place: its task's conversation, the thread its comments are in (`where: thread:<id>`)."""

        def place(c, author, recipients):
            row = H.task(c, auth.resolve_task(c, who, tid))
            return org_rule_check.place_for(c, author, recipients, (row or {}).get("conversation_id") or "")

        return place

    def rule_check(
        who, operation, output, audience, obj="", context=None, place=None, *, track_route=False, late=False
    ):
        """The check for one write by a person or a bot, or None (nothing to judge). An audience the caller may not name
        is `org_rule_check.refused`: never unchecked, the write fails with that error (`apply`). `place`: the write's
        place, or a function of (c, author, recipients) that reads it. `track_route` keeps the audience even when
        nothing matches, so a send cannot move to an unchecked recipient. `late`: when every rule it meets only warns,
        the check runs right after the write instead of before it (org_rule_check `check_as`)."""
        author = org_rule_check.author_of(who, settings.assistant_bot)
        if not track_route and (not output or not author):
            return None
        with store.read() as c:
            in_force = org_rule_check.in_force(c, org_rule_check.asking(place))
            if not track_route and not in_force:
                return None  # no rule in force: the audience (a roster read, @mentions) is never resolved
            try:
                recipients = audience(c)
            except Problem as exc:
                return org_rule_check.refused(exc)
        verdict = (
            org_rule_check.preflight(
                store, app.state.judge, who, operation, output, recipients, context, obj, place=place, late=late
            )
            if in_force and output and author
            else None
        )
        if track_route and verdict is None:
            return {
                "author": author or who.actor,
                "operation": operation,
                "outcome": "no_rules",
                "recipients": sorted({r for r in recipients if r and r != (author or who.actor)}),
            }
        return verdict

    def rule_public(body):
        """Whether a task write makes the task public: the whole company reads it."""
        return getattr(body, "visibility", None) == "public" or bool(
            getattr(body, "public_title", None) or getattr(body, "public_summary", None)
        )

    def rule_create_text(body):
        """What a new task says, as the rule check reads it: its title and body, and its public title and summary."""
        return "\n\n".join(
            filter(
                None,
                (body.title, body.body, getattr(body, "public_title", None), getattr(body, "public_summary", None)),
            )
        )

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
        return bool(getattr(body, "public_title", None) or getattr(body, "public_summary", None)) or rule_newly_public(
            body, row
        )

    def rule_update_audience(c, who, body, row):
        """A task change's audience: the task's people and a new owner, and the whole company when it publishes."""
        return rule_task_audience(
            c,
            row,
            who.actor,
            "",
            *([rule_target(c, who, body.owner)] if getattr(body, "owner", None) else []),
            *(org_rule_check.company(c) if rule_publishes(body, row) else []),
        )

    def rule_update_text(body, row):
        """What a task change says, as the rule check reads it: its text fields, the task itself when it is handed to
        a new owner, and the public text already written when the change alone makes the task public."""
        parts = [
            f"{label}: {value}"
            for key, label in (
                ("title", "Title"),
                ("body", "Body"),
                ("note", "Note"),
                ("public_title", "Public title"),
                ("public_summary", "Public summary"),
            )
            if (value := getattr(body, key, None))
        ]
        if rule_newly_public(body, row):
            parts += [
                f"{label}: {row[key]}"
                for key, label in (("public_title", "Public title"), ("public_summary", "Public summary"))
                if row.get(key) and getattr(body, key, None) is None
            ]  # an explicit "" clears it: no text
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
                except Problem as exc:
                    return org_rule_check.refused(exc)
        text = rule_update_text(body, row)
        if not text:
            return None
        only_owner = getattr(body, "owner", None) and not any(
            getattr(body, k, None) for k in ("title", "body", "note", "public_title", "public_summary")
        )
        return rule_check(
            who,
            "task_owner" if only_owner else "task_update",
            text,
            lambda c: rule_update_audience(c, who, body, row),
            obj=f"{row['id']}@{row['version']}",
            context={"task": row["title"]},
        )

    def message_ingest(who, body):
        """Ask ingest judges a task for a person (backend/ask_ingest.py), never a message: a message no longer asks
        anything (step 6c), so there is nothing to judge here."""
        return None

    def routed_message(c, who, body):
        return body.model_copy(deep=True)

    def message_write(c, who, body, rc=None, ingest=None, files=None):
        """One message, as every route that sends one writes it (`/messages`, a conversation's `/messages`) and Send
        anyway replays it: the org rule check's verdict, then ask ingest's, then the send. A hold names the audience the
        check read, so the audience is read again here, inside the write: a caller who lost access gets 404, and an
        audience that moved since the check is checked again (409)."""
        body = routed_message(c, who, body)
        body.conversation_id = named_destination(c, who, body)
        audience = rule_message_audience(c, who, body) if rc is not None else None
        replay = {"body": body.model_dump(mode="json", by_alias=True), **({"files": files} if files else {})}
        rule = org_rule_check.apply(c, rc, body.text, replay, audience_now=audience)
        if rule and rule.get("moved"):
            raise Problem(
                "moved", "The conversation changed while the message was checked; send it again", 409, retryable=True
            )
        if rule and rule.get("held"):
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.held_answer(c, who, rule, auth=auth)
        sent = body.model_copy(update={"text": org_rule_check.warned_text(rule, body.text)}) if rule else body
        message = send(c, who, sent)
        return {**message, **org_rule_check.answer(rule)}

    def message_checks(who, body, retry=False, place=None, operation="message"):
        """Both checks of one message, outside the write: the rule check, then ask ingest (a held one skips it, and so
        does a saved retry, `retry`: `mutate` replays its answer without the write). `place`: the message's place when
        the route does not name a conversation (`rule_message_place`)."""
        if retry:
            return None, None
        with store.read() as c:
            body = routed_message(c, who, body)
        if body.conversation_id:
            # A reply to a room that moved names its Home for every check below (backend/bot_home_merge.py); the sender's
            # attempt, a private run's, still decides what the Home allows. A room whose Home is archived gets the bot's
            # Home made here, before the checks, as the implicit path does, so the checks see where the words would land.
            with store.read() as c:
                make = bot_home_merge.home_missing(c, body.conversation_id)
                if not make:
                    body.conversation_id = named_destination(c, who, body, create=False)
            if make:
                with store.transaction() as c:
                    body.conversation_id = named_destination(c, who, body)
        elif not H.is_human(who.actor):
            # A bot's message to a person that names no room goes to the person's chat Home (hubdb.chat_home). The checks
            # must see that Home: it is made here if the bot has none open (an archived one), so a private run's words
            # cannot reach a Home the checks never named.
            with store.transaction() as c:
                body.conversation_id = named_destination(c, who, body)
        elif str(body.to).lower() != "assistant":
            # A person's message to a bot that names no room is written to the bot's personal room (rooms.chat_room), which
            # is the chat's Home once the bot's chats have moved (rooms.personal_room). The recipient is resolved as the
            # write resolves it (auth.target: `bot:<slug>`, a bare slug or an id), so every spelling of one bot gets the same
            # Home. The checks name that Home too, so `where: thread:<home>` sees it, and it is made first when archived, as
            # the write would make it. A shared bot's shared room is not a Home: a member's message there is checked where it
            # is written. The Assistant's own room is not a Home either (send routes it apart).
            from .shared_bots import route as shared_route

            with store.read() as c:
                to = auth.target(c, who, body.to)
                target = H.actor_id(shared_route(c, who.actor, to)) if to.startswith("bot:") else ""
                personal = (
                    bool(target)
                    and target != settings.assistant_bot
                    and not (
                        rooms.thread_mode(c, target) == rooms.SHARED and rooms.shared_member(c, auth, who.actor, target)
                    )
                )
                make = personal and bot_home_merge.chat_home_missing(c, who.actor, target)
                home = (
                    None if make or not personal else bot_home_merge.home_for_chat(c, who.actor, target, create=False)
                )
            if make:
                with store.transaction() as c:
                    home = bot_home_merge.home_for_chat(c, who.actor, target)
            if home and place is None:
                place = home_place(home)
        reach.precheck(
            store,
            who,
            lambda c: reach.check(
                c,
                who,
                auth.target(c, who, body.to),
                "chat",
                conversation_id=body.conversation_id,
                task_id=H.refs_task_id(body.refs),
                in_reply_to=body.in_reply_to,
            ),
        )
        rc = rule_check(
            who,
            operation,
            body.text,
            lambda c: rule_message_audience(c, who, body),
            place=place or rule_message_place(who, body),
        )
        return rc, (None if org_rule_check.holds(rc) or retry else message_ingest(who, body))

    @app.post("/api/v2/messages")
    def say(request: Request, body: M.MessageCreate):
        who = request.state.identity
        rc, ingest = message_checks(who, body, retry=saved_retry(request, who))

        def legacy_body(c, receipt):
            # Older checks hashed a bot's resolved Home. Use its saved destination:
            # a retry must not create a Home or follow today's recipient routing.
            if (
                (H.is_human(who.actor) and not body.conversation_id)
                or not isinstance(receipt, dict)
                or receipt.get("sender_id") != who.actor
                or not receipt.get("conversation_id")
            ):
                return None
            cid = receipt["conversation_id"]
            if body.conversation_id and named_destination(c, who, body, create=False) != cid:
                return None
            return {**body.model_dump(), "conversation_id": cid}

        return ask_ingest.judged(
            store,
            ingest,
            lambda: mutate(request, body, lambda c: message_write(c, who, body, rc, ingest), retry_body=legacy_body),
        )

    def rule_prepare_message(store_, bot, replay):
        return message_ingest(bot, M.MessageCreate.model_validate(replay["body"]))

    @org_rule_check.replayer("message", prepare=rule_prepare_message)
    def rule_replay_message(c, bot, hold, replay, ingest, gate=None):
        # `send` checks the destination as the bot and its run (task_privacy.require_destination).
        return message_write(
            c, bot, M.MessageCreate.model_validate(replay["body"]), org_rule_check.decided(gate), ingest
        )

    def chat_upload_checks(request, who, bot, sent):
        """The checks of a person's chat message with files, outside the write: `message_checks` under its own operation,
        so Send anyway replays the files with the message (`chat_upload_write`)."""
        return message_checks(
            who, sent, retry=saved_retry(request, who), place=chat_place(who, bot), operation="chat_upload"
        )

    def chat_upload_write(c, who, bot, sent, rc, ingest, uploads, titles):
        """One chat message with files, as the route and Send anyway write it: the message (`message_write`: the verdict,
        ask ingest, the send), then its files on it. Held, the message and its files wait in the hold; `uploads` are the
        files' digests, sizes and names, whose bytes were kept before the check."""
        message = message_write(c, who, sent, rc, ingest, {"uploads": uploads, "titles": list(titles or [])})
        if "id" not in message:
            return message
        extra = {k: message.pop(k) for k in ("rule_warning", "ingest") if k in message}
        return {
            "conversation": H.conversation(c, message["conversation_id"]),
            "message": app.state.uploads.attach(c, who, bot, message, uploads, titles),
            **extra,
        }

    def rule_prepare_chat_upload(store_, who, replay):
        return message_ingest(who, M.MessageCreate.model_validate(replay["body"]))

    def rule_chat_upload_audience(c, who, replay):
        return rule_message_audience(c, who, M.MessageCreate.model_validate(replay["body"]))

    @org_rule_check.replayer("chat_upload", prepare=rule_prepare_chat_upload, audience=rule_chat_upload_audience)
    def rule_replay_chat_upload(c, who, hold, replay, ingest, gate=None):
        sent = M.MessageCreate.model_validate(replay["body"])
        files = replay.get("files") or {}
        return chat_upload_write(
            c,
            who,
            sent.to.removeprefix("bot:"),
            sent,
            org_rule_check.decided(gate),
            ingest,
            files.get("uploads") or [],
            files.get("titles") or [],
        )

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
            # A native thread's readers decide first: a bot removed from it loses its addressed messages too. A legacy
            # conversation keeps the sender/addressee shortcut as it was.
            native = bool((H.conversation(c, row["conversation_id"]) or {}).get("audience"))
            party = row.get("recipient_id") == who.actor or row.get("sender_id") == who.actor
            if not (who.role == "bot" and party and not native):
                auth.conversation(c, who, row["conversation_id"])
            from .mail_settings import message_sender_can_approve_send

            row["sender_can_approve_send"] = message_sender_can_approve_send(c, auth, row)
            return visual_view(c, who, [row])[0]

    @app.post("/api/v2/chat/{bot}")
    def chat(request: Request, bot: str, body: M.ChatCreate):
        who = request.state.identity
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        # A person's message in their direct chat: a new topic clears the session first (backend/topic_change.py).
        topic = topic_check(request, who, bot, body)
        sent = M.MessageCreate(
            to="bot:" + bot,
            text=body.text,
            refs=body.refs,
            command=body.command,
            attachments=body.attachments,
            delivery=body.delivery,
            defer_for=body.defer_for,
        )
        # A bot's message here is a message like any other: the same checks and write as `/messages`, held or warned
        # the same way, and replayed by Send anyway as one.
        rc, ingest = message_checks(who, sent, retry=saved_retry(request, who), place=chat_place(who, bot))

        def work(c):
            cleared = topic_change.apply(c, settings, who, bot, topic)
            message = message_write(c, who, sent, rc, ingest)
            if "id" not in message:
                return message  # held by an org rule: the answer is the hold alone, and nothing was sent
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

        def admit(c):  # read with the gate, inside the check's time budget
            from .shared_bots import route

            if c.execute(
                "SELECT 1 FROM idempotency WHERE binding IS NULL AND actor=? AND operation=? AND key=?",
                (who.actor, path, key),
            ).fetchone():
                return False
            target = H.actor_id(route(c, who.actor, "bot:" + bot))
            return (
                target == bot
                and bot not in (settings.assistant_bot, strategy_bot.SLUG)
                and views.may_chat(c, auth, who, bot)
            )

        return topic_change.check(store, app.state.judge, who, bot, body, admit, uploads=())

    updates_routes.register(
        app,
        auth,
        may_edit_routines,
        message_checks,
        message_write,
        mutate,
        owner_only,
        rule_check,
        rule_message_audience,
        send,
        settings,
        store,
        task_view,
    )

    def task_filters(
        c,
        owner=None,
        requester=None,
        status=None,
        lane=None,
        label=None,
        number=None,
        updated_since=None,
        member=None,
        role=None,
        snoozed=None,
        by_id=False,
        exact=False,
    ):
        """The filters `GET /api/v2/tasks` takes, checked and resolved: (owner, requester, H.tasks keywords). A bulk
        change (`POST /api/v2/task-bulk`) selects with the same ones. `exact` refuses an owner or requester that names
        nobody, where a list would quietly drop the filter: a bulk change must never widen to every task."""
        found_owner = H.resolve_actor(c, owner) if owner else None
        found_requester = H.resolve_actor(c, requester) if requester else None
        if exact and (owner and not found_owner or requester and not found_requester):
            raise Problem(
                "owner" if owner and not found_owner else "requester",
                "owner and requester are a person or a bot on the roster",
                422,
            )
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
        statuses = status.split(",") if status and status != "all" else None
        if statuses and any(s not in ("open", "done", "closed") for s in statuses):
            raise Problem("status", "status is open, done or closed", 422)
        if snoozed not in (None, "hide", "only", "all"):
            raise Problem("snoozed", "snoozed is hide, only or all", 422)
        if status == "all" and snoozed is None:
            snoozed = "all"  # every task means the snoozed ones too: they are open, only set aside
        if number is not None and not 1 <= number <= 999_999_999:
            raise Problem("number", "number is a task number, from 1", 422)
        since = H.parse_ts(updated_since) if updated_since else None
        if updated_since and (not since or since.tzinfo is None):
            raise Problem("date", "updated_since must be an ISO-8601 date/time with a timezone", 422)
        return (
            found_owner,
            found_requester,
            dict(
                status=statuses,
                lane=lane,
                label=label,
                number=number,
                updated_since=views.since_time(updated_since) if since else None,
                member=member,
                role=TRo.role_name(role) if role else None,
                snoozed=None if snoozed == "all" or (snoozed is None and by_id) else snoozed or "hide",
            ),
        )

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

    task_list_routes.register(
        app, auth, mutate, rule_check, rule_target, store, task_filters, task_idents, visible_tasks
    )

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
            # Each related task is read under the reader's own rule: reading this one grants no other.
            related_sql = auth.task_sql(c, who)
            child_rows = H._rows(
                c.execute(
                    "SELECT id,title,status,owner_id FROM tasks WHERE id IN ("
                    + TR.CHILD_IDS
                    + ") AND ("
                    + related_sql
                    + ") ORDER BY (rank IS NULL), rank, created_at",
                    (tid,),
                )
            )
            child_refs = H.ticket_refs(c, [t["id"] for t in child_rows])
            children = [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "status": t["status"],
                    "owner_id": t["owner_id"],
                    **child_refs[t["id"]],
                }
                for t in child_rows
            ]
            # The parent ticket, when there is one. Its title is given only when the reader may read it: auth.task
            # refuses a task the reader cannot see, and then the parent is null.
            parent = None
            parent_id = TR.parent_of(c, tid)
            if parent_id:
                try:
                    parent_row = auth.task(c, who, parent_id)
                except Problem:
                    parent_row = None
                if parent_row:
                    parent = {"id": parent_id, "title": parent_row["title"], **H.ticket_refs(c, [parent_id])[parent_id]}
            from .task_reads import comment_rights
            from .views import task_wait

            try:
                comment_rights(c, auth, who, tid)
                can_comment = True
            except Problem:
                can_comment = False
            history = privacy.content_readable_rows(c, privacy.actor(who), H.task_history(c, tid, provenance=True))
            # An event that names a ticket the reader may not open (a hidden child, say) is no detail of this one: it goes.
            named = sorted({str(e[k]) for e in history for k in ("old", "new") if e.get(k)})
            if named:
                known = {
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(named),)
                    )
                }
                readable = {
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND (" + related_sql + ")",
                        (json.dumps(named),),
                    )
                }
                hidden_refs = known - readable
                history = [e for e in history if not ({str(e.get(k)) for k in ("old", "new")} & hidden_refs)]
            # A subtask summary's text names its subtasks only where the reader may open them (task_reads.children_text).
            from . import task_reads

            history = task_reads.children_text(c, related_sql, history)
            # What a bound OK is for is its parties' alone: its ask and answers keep no words for anyone else.
            from . import task_asks

            history = task_asks.private_history(c, who.actor, history)
            lines = SRC.lookup(c, SRC.actors_of(history))
            for event in history:
                event["source"] = SRC.source_of(lines, event)  # reads `data`, which the answer leaves out
                event.pop("data", None)
            # An ask's words come from the pages it showed (body_sources): a reader who may not open one reads none.
            from .files import readable_page_refs

            history = readable_page_refs(c, auth, who, history)
            for event in history:
                if isinstance(event, dict):
                    event.pop("body_sources", None)
            # Who follows it (backend/task_followers.py): null for a task with no thread of its own.
            return {
                "task": {
                    **task_view(row, c, visible_sql=related_sql, who=who),
                    "followers": task_followers_seen(c, who, row),
                },
                "events": history,
                "can_comment": can_comment,
                "children": children,
                "parent": parent,
                # The task page reads as its session's chat: each bot reply carries the run behind it (`run`,
                # the steps one click away) and `execution` is the run it follows while it works: the owner's,
                # or the one for a person's message, whichever bot that went to. `comment_to` is who the reader's
                # next comment reaches, so the composer says before sending when no bot will hear it.
                "comments": visual_view(
                    c, who, turn_work.annotate(c, auth, who, H.task_comments(c, tid, actor=privacy.actor(who)))
                ),
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
                "can_set_run_model": H.is_bot(row["owner_id"])
                and task_run_models.may_set(c, auth, who, H.actor_id(row["owner_id"])),
                **privacy.page(c, who, row["conversation_id"], task_id=tid),
            }

    def task_execution(c, who, row):
        """The run the task's page follows in its own room, as a conversation snapshot shows it, or None: the owning
        bot's newest run, or a newer one for a message a person sent there, whichever bot it was routed to (the bot
        that asked a person's task, say). Another bot's run for a bot's comment is not it. A task still in a chat
        room (filed into a chat before task rooms) shows none: that room's runs are the chat's, and reading the task
        does not let one read the chat."""
        cid, owner = row["conversation_id"], str(row["owner_id"] or "")
        room = H.conversation(c, cid) if cid else None
        if not room or room.get("task_id") != row["id"]:
            return None
        from .views import conversation_execution

        return conversation_execution(
            c, cid, who, settings, bot=owner.removeprefix("bot:") if H.is_bot(owner) else None, auth=auth, people=True
        )

    def comment_bot(c, row):
        """The bot a plain comment on the task gives a turn first (threads._task_primary, with no @mention): its first
        live bot follower who still reads it, or, for a task in a shared chat (no followers), its owner bot. One
        read. None when there is none."""
        from . import task_followers as TF

        tid = TF.thread_id(c, row)
        if not tid:
            return row["owner_id"] if H.is_bot(row["owner_id"]) else None
        bots = [
            r[0]
            for r in c.execute(
                "SELECT actor FROM thread_followers WHERE thread_id=? AND following=1 AND "
                "notify='live' AND actor LIKE 'bot:%' ORDER BY created_at, actor",
                (tid,),
            )
        ]
        private = bots and H.task_private(c, row)
        return next((b for b in bots if not private or H.task_private_readable(c, b, row)), None)

    def comment_way_for(c, who, row):
        """The way a reader's comment with none named goes: Steer (D13) for whoever may direct the task's bots
        (chat_goals.may_direct), else Defer never, so a reader never spends a bot's turns on a task that isn't
        theirs."""
        from .chat_goals import may_direct

        return ("steer", None) if who.role == "bot" or may_direct(c, who, row) else ("defer", delivery.NEVER)

    def comment_to(c, who, row):
        """Who a plain comment from this reader wakes first (comment_bot): `bot`, or None when no bot follows the
        task; `wakes`, whether its default way wakes that bot (steer) rather than waiting for its next turn on the task
        (defer); `mention`, kept empty: an @mention always reaches whoever it names. None for a bot reader."""
        if who.role == "bot":
            return None
        bot = comment_bot(c, row)
        if not bot:
            return {"bot": None, "wakes": False, "mention": []}
        return {"bot": H.actor_id(bot), "wakes": comment_way_for(c, who, row)[0] != "defer", "mention": []}

    def delivery_choices(c, who, row):
        """How this reader's comment can reach the task's bots (docs/tasks.md, backend/delivery.py): `default`, the way
        a comment with none named goes (steer or defer; null when no bot follows the task) and `default_for`, a default
        defer's deadline (never); `steer` and `queue`, whether the reader may wake them (chat_goals.may_direct;
        anyone may defer); `steers`, whether the first bot's harness writes a steer into its running turn; `goal`,
        whether the reader may set the task session's goal (chat_goals.own_room and chat_goals.may_set). None for a
        bot reader."""
        if who.role == "bot":
            return None
        bot = comment_bot(c, row)
        if not bot:
            return {
                "default": None,
                "default_for": None,
                "steer": False,
                "queue": False,
                "steers": False,
                "goal": False,
            }
        way, defer_for = comment_way_for(c, who, row)
        config = c.execute(BS.ONE_SQL, (H.actor_id(bot),)).fetchone()
        config = json.loads(config[0] or "{}") if config else {}
        from .harnesses import steers
        from .chat_goals import may_direct, may_set, own_room

        directs = may_direct(c, who, row)
        return {
            "default": way,
            "default_for": defer_for if way == "defer" else None,
            "steer": directs,
            "queue": directs,
            "steers": steers(config, config.get("runtime")),
            "goal": own_room(c, row) and may_set(c, auth, who, row["conversation_id"]),
        }

    def task_runs(c, who, tid, limit=200):
        """The runs that worked this task, each with the model that actually ran it (`ran_on`). A review that
        weighs who built what reads this, never the bot's configured model: a fallback turn ran elsewhere."""
        from .usage_records import ran_on

        actor = privacy.actor(who)
        rows = H._rows(
            c.execute(
                "SELECT id,bot_id AS bot,created_at AS started_at,settled_at AS finished_at,exit "
                "FROM attempts WHERE task_id=? AND (job_id IS NULL OR trigger IS NOT NULL) "
                "ORDER BY created_at DESC LIMIT ?",
                (tid, limit),
            )
        )
        privacy.prime_attempts(c, [r["id"] for r in rows])
        rows = [r for r in rows if privacy.attempt_readable(c, actor, r["id"])]
        made = ran_on(c, [r["id"] for r in rows])
        return [
            {
                "run": r["id"],
                "bot": r["bot"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "outcome": r["exit"],
                "ran_on": made[r["id"]],
            }
            for r in rows
        ]

    def check_choice(c, who, body, owner, parent_id):
        """The check model a bot's subtask asks for (backend/check_models.py), or Problem. Only a bot files one, as
        its own subtask under its own task, so the verdict comes back to the session that asked."""
        if who.role != "bot" or who.via or owner != who.actor:
            raise Problem("check_model", "Only a bot runs a check model, on a subtask it owns", 422)
        parent = H.task(c, parent_id) if parent_id else None
        if not parent or parent["owner_id"] != who.actor:
            raise Problem("check_model", "A check model runs a subtask: file it under a task you own", 422)
        try:
            return check_models.choose(
                c,
                settings,
                H.actor_id(owner),
                body.check_model,
                body.check_role or "check",
                check_models.authors_of(c, settings, parent_id),
            )
        except check_models.CheckModelError as exc:
            raise Problem(exc.code, str(exc), 409 if exc.code == "runner_not_ready" else 422)

    def task_create(c, who, body, lint=True, ingest=None, same=None, rule=None, wake_refs=None):
        """`POST /api/v2/tasks`: the task is made and put in the first stage of its pipeline (H.task_create). A task
        for a bot wakes it; `wake_refs` go on that wake (an auto dream's release policy, backend/dream_auto.py)."""
        source = who
        actual_bot = source.task_actor or (source.actor if source.role == "bot" else "")
        if body.from_private:
            pass  # company-visible, its words checked (task_privacy.from_private)
        elif privacy.private_execution(c, who):
            if body.private is False:
                raise Problem("privacy", privacy.COMPANY_REFUSAL, 403)
            body = body.model_copy(update={"private": True})
        request_id = body.request_id
        handoff = who.role == "bot" and who.actor != "bot:" + BOTOPS and not who.via
        if request_id and handoff:
            request_id = None  # a bot cites the person it is handing on; it never acts as them itself
        elif request_id and who.via == "assistant":
            pass  # the person's own words to their Assistant, checked once the owner is known
        elif request_id:
            who = delegated_identity(c, who, request_id, new_task=True)
        parent_id = auth.resolve_task(c, who, body.parent) if body.parent else None
        others = [(auth.resolve_task(c, who, r.task), r.kind) for r in body.relations]
        from .shared_bots import route

        # No owner: a first stage that picks from several picks one as the task arrives (backend/stage_pick.py). The
        # filer stands in as its requester and reach, as for a task a stage files, and it enters with no assignee.
        unassigned = not body.owner
        if unassigned:
            from . import stage_pick

            refused = stage_pick.no_owner_refusal(c, body.pipeline)
            if refused:
                raise Problem("owner", refused, 422)
            body = body.model_copy(update={"owner": who.actor})
        original = auth.target(c, who, body.owner, need="write")
        owner = auth.target(c, who, route(c, who.actor, original), need="write")
        from . import meeting_audience

        meeting_participants = (
            meeting_audience.check(c, auth, who, owner, live_id=body.live_meeting_id, roles=body.roles)
            if body.live_meeting_id
            else None
        )
        if meeting_participants is not None:
            if body.visibility == "public":
                raise Problem("meeting_audience", meeting_audience.REFUSAL, 409)
            body = body.model_copy(update={"private": True})
        task_caps.check(c, who.actor, owner)  # the first hub check on a send (CONTRACT §1): nothing is written
        # A bot's task for a teammate out of its reach, or its question to one (docs/reach.md), is refused next.
        reach.check(c, who, owner, "task", task_id=parent_id)
        asked_botops = None
        if request_id and who.via == "assistant":
            # The Assistant handing a helper (BotOps, the Strategy Owner, the Librarian; assistant.HELPERS) what the
            # person asked for in their own Assistant chat, citing that message (`--request-id turn`), only while the
            # owner lets the Assistant act without asking. BotOps then acts with the person's rights
            # (delegated_identity, `assistant`); the Strategy Owner and the Librarian work it with their own rights, so
            # what only the owner may change (strategy, goals) still reaches the owner as a proposal or a card.
            if H.actor_id(owner) not in assistant_mod.HELPERS or not H.is_bot(owner):
                raise Problem(
                    "request_id",
                    "Only a task for BotOps, the "
                    + strategy_bot.name(c)
                    + " or the Librarian carries the person's request",
                    422,
                )
            asked_botops = assistant_instruction(c, who, request_id) if assistant_direct() else None
            request_id = None
        # The same checks the dry-run makes (task_privacy.require_create); a subtask for a person under a private
        # task with no person on it, filed by the bot that owns it, makes them its reader.
        reach_to = privacy.require_create(
            c,
            source,
            body.private,
            owner,
            H.task(c, parent_id) if parent_id else None,
            actor=who.actor,
            title=body.title,
            body=body.body,
            from_private_work=body.from_private,
            texts=privacy.create_texts(body),
            related=[other for other, _ in others],
        )
        # The private tasks a company-visible task from private work comes from: their history says so.
        kept = (
            privacy.from_private(c, source, owner, H.task(c, parent_id) if parent_id else None)
            if body.from_private
            else []
        )
        # A private run's subtask for a bot that cannot read its parent: sealed, the history says so (Dev 632).
        sealed = (
            not reach_to
            and not kept
            and privacy.private_execution(c, source)
            and privacy.sealed_for(c, source, owner, H.task(c, parent_id) if parent_id else None) is not None
        )
        auth.require_bot_contact(c, who, owner, task_id=parent_id, kind="task")
        # A subtask is the shape that parks the filer as `waiting` and makes it care when the
        # other one finishes. A bot set to `tasks` has opted out of that on both sides: it does
        # not wait, and its own completions are quiet, so parking on one would wait for ever.
        # Every other bot keeps the handoff flow in `policies/handoffs.md`.
        if (
            parent_id
            and who.role == "bot"
            and owner != who.actor
            and str(owner).startswith("bot:")
            and (H.tasks_only(c, who.actor) or H.tasks_only(c, owner))
        ):
            raise Problem(
                "subtask",
                "A bot set to tasks-only files work flat: give it its own "
                "task without a parent, and do not wait for it.",
                422,
            )
        if parent_id:
            auth.task(c, who, parent_id)
        check, unchecked = None, None
        if body.check_model is not None or body.check_role not in (None, "review"):
            check = check_choice(c, who, body, owner, parent_id)
        else:
            check, unchecked = check_models.review_default(c, settings, owner, parent_id)
        # The task's own model, checked before anything is written (the same rights as the bot's model, a bot never).
        run_model = None
        if body.model is not None or body.effort or body.harness or body.compact_at:
            if not H.is_bot(owner):
                raise Problem("owner", "Only a task a bot runs has a model to choose", 422)
            if body.model is None and (body.effort or body.harness):
                raise Problem("model", "Choose a model to pick an effort or harness for it", 422)
            if body.model is None:
                # A window alone: its runs keep the bot's model (backend/task_run_models.py).
                run_model = settings_admin.task_run_window(
                    c, who, H.actor_id(owner), "set where a new task's runs compact", body.compact_at
                )
            else:
                run_model = settings_admin.task_run_choice(
                    c, who, H.actor_id(owner), "set the model a new task runs on", body.model, body.effort, body.harness
                )
                run_model["compact_at"] = body.compact_at
        for other, _ in others:
            auth.task(c, who, other)
        parent = H.task(c, parent_id) if parent_id else None
        # Delegation retains the bot's sensitive default and cannot use a person's
        # explicit-public choice to override either creator or assignee defaults.
        if actual_bot and body.private is False:
            body = body.model_copy(update={"private": None})
        if actual_bot and H.private_tasks_default(c, actual_bot):
            body = body.model_copy(update={"private": True})
        private = not kept and bool(
            (
                body.private
                if body.private is not None
                else H.private_tasks_default(c, who.actor) or H.private_tasks_default(c, owner)
            )
            or parent
            and H.task_private(c, parent)
        )
        if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        # The org rule check (backend/org_rule_check.py), judged before the write: held, nothing is filed.
        rule_gate = (
            org_rule_check.apply(c, rule, rule_create_text(body), {"body": body.model_dump(mode="json", by_alias=True)})
            if rule
            else None
        )
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
        # Ask ingest (backend/ask_ingest.py): a bot's task for a person, judged before the write. SENT_BACK and FYI
        # refuse the whole write; a merged ask stays on the bot's task.
        gate = None
        if ingest and owner == ingest["person"]:
            gate = ask_ingest.apply(c, ingest)
            if gate["acts"]:
                owner, check, unchecked = who.actor, None, None
        elif ingest:
            ask_ingest.skipped(c, ingest, "not_applied")
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        requester_actor = None
        if who.role == "bot" and owner == who.actor and not unassigned and not parent_id and who.attempt_id and private:
            origin = c.execute(
                "SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                "JOIN messages m ON m.id=j.message_id WHERE a.id=? AND a.bot_id=?",
                (who.attempt_id, H.actor_id(who.actor)),
            ).fetchone()
            if origin and privacy.message_readable(c, privacy.actor(who), origin):
                if H.is_human(origin["sender_id"]):
                    requester_actor = origin["sender_id"]
                else:
                    message = H.message(c, origin["id"])
                    source_task = H.task(c, H.message_task_id(message, H.conversation(c, message["conversation_id"])))
                    if (
                        source_task
                        and H.task_private(c, source_task)
                        and source_task["owner_id"] == who.actor
                        and H.is_human(source_task["requester_id"])
                    ):
                        requester_actor = source_task["requester_id"]
            if requester_actor and not privacy.readable(c, requester_actor, privacy.attempt_tasks(c, who.attempt_id)):
                raise Problem(
                    "privacy", "The verified requester cannot receive this private execution's other context", 403
                )
        if body.for_actor:
            # A bot files a person's own ask: the person asked for it, so it is theirs (Ideas: say it once, #227).
            requester_actor = filed_for(c, who, body.for_actor, body.from_message)
        if private and actual_bot and actual_bot not in (requester_actor or who.actor, owner):
            raise Problem("privacy", "The acting bot must be a current participant of the private task", 403)
        if reach_to:
            H.task_reach(c, who.actor, parent, owner)
        row = H.task_create(
            c,
            who.actor,
            body.title,
            body.body,
            owner,
            body.due,
            parent_id,
            requester_actor=requester_actor,
            requester_said=bool(body.for_actor),
            visibility=body.visibility,
            public_title=body.public_title,
            public_summary=body.public_summary,
            public_warning=public_warning,
            private=private,
            conversation_id=rooms.task_conversation_id(c, auth, owner, who.actor),
            lane=body.lane,
            labels=body.labels,
            top=body.top,
            lint=lint,
            goal_id=body.goal_id,
            pipeline=body.pipeline,
            number=body.number,
            mover=mover(c, who) or None,
            own_room=H.actor_id(owner) != settings.assistant_bot,
            from_private=bool(kept),
            same=same,
            follow=body.follow,
            # Who else sees it, before it enters its first stage and before a meeting's audience is checked against
            # everyone who will.
            readers=body.readers or (),
            readers_check=lambda task_id, added: privacy.require_readers(c, who, task_id, added, shared=parent_id),
            unassigned=unassigned,
            wake_refs=wake_refs,
            human_id=body.human,
        )
        c.execute("UPDATE tasks SET acceptance=? WHERE id=?", (encode(body.acceptance_criteria), row["id"]))
        if body.for_actor:
            H._task_event(
                c,
                row["id"],
                who.actor,
                "filed_for",
                None,
                body.for_actor,
                f"Filed for {H.actor_name(c, body.for_actor)}, from their message",
            )
        if run_model:
            task_run_models.set_choice(c, who.actor, row["id"], run_model, note="Set when the task was filed")
        # A send, counted against the sender's cap for this owner, at the moment the task was made.
        task_caps.record(c, row["id"], who.actor, owner, at=row["created_at"])
        if gate:
            ask_ingest.record(c, ingest, gate, row["id"])
        for source_row in kept:
            # The private task's readers see what was filed; the new task's, only that it came from private work.
            H._task_event(
                c,
                source_row["id"],
                who.actor,
                "filed_from_private",
                None,
                row["id"],
                f"Filed a company-visible task from this private task: {body.title}",
            )
        if kept:
            H._task_event(c, row["id"], who.actor, "from_private", None, who.actor, "Filed from a private task")
        if sealed:
            # The parent's readers see who it went to; the subtask's see who filed it, and nothing of the parent.
            H._task_event(
                c,
                parent_id,
                who.actor,
                "handed",
                None,
                owner,
                f"Handed to {(H.bot(c, H.actor_id(owner)) or {}).get('slug') or H.actor_id(owner)} in a private subtask",
            )
            H._task_event(
                c,
                row["id"],
                who.actor,
                "sealed",
                None,
                who.actor,
                f"Filed by {(H.bot(c, H.actor_id(who.actor)) or {}).get('slug') or H.actor_id(who.actor)} from private work it does not share",
            )
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
                    name = (H.human(c, H.actor_id(carried["person"])) or {}).get("name") or H.actor_id(
                        carried["person"]
                    )
                    H._write_message(
                        c,
                        H.KEEPER,
                        owner,
                        f"{name} asked {(H.bot(c, H.actor_id(who.actor)) or {}).get('slug') or H.actor_id(who.actor)} for this, in their words:\n\n> "
                        + "\n> ".join(words[:2000].splitlines() or [""]),
                        H.conversation(c, row["conversation_id"]),
                        "say",
                        {
                            "task": row["id"],
                            "comment": True,
                            "quiet": True,
                            "carried": True,
                            "source": SRC.ASSISTANT_FOR,
                            "for": carried["person"],
                        },
                        None,
                        None,
                    )
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
            H._write_message(
                c,
                H.KEEPER,
                owner,
                f"{name} asked the {settings.assistant_name} for this, in their words:\n\n> "
                + "\n> ".join(words[:2000].splitlines() or [""]),
                H.conversation(c, row["conversation_id"]),
                "say",
                {
                    "task": row["id"],
                    "comment": True,
                    "quiet": True,
                    "carried": True,
                    "source": SRC.ASSISTANT_FOR,
                    "for": who.actor,
                },
                None,
                None,
            )
        inherited_request = (
            parent.get("request_id")
            if parent and H.is_human(who.actor) and who.actor == parent["requester_id"]
            else None
        )
        if request_id in ("turn", "default"):
            # `turn` names the run's own request: store that message, which delegated_identity resolved, so the task's
            # own run (whose turn is the task notice) still finds the person's words.
            request_id = (H.DELEGATION.get() or {}).get("cited") or None
        if H.is_human(who.actor) and (request_id or inherited_request):
            c.execute("UPDATE tasks SET request_id=? WHERE id=?", (inherited_request or request_id, row["id"]))
        for url in body.links:
            H.task_link(c, who.actor, row["id"], url, mover=True)
        # A bot's finding on a pipeline task's own pull request is that task's review verdict, not a person's task.
        H.review_finding_route(c, who.actor, row["id"], row["owner_id"])
        # The parent it was filed under is a relation written here too, and reads back first.
        filed_under = TR.parent_of(c, row["id"])
        readback = TR.readback(c, row["id"], filed_under, "parent") if filed_under else []
        for other, kind in others:
            changed = TR.relate(c, who.actor, row["id"], other, kind, mover=mover(c, who) or None)
            readback += TR.readback(c, row["id"], other, kind, changed=changed)
        if body.live_meeting_id:
            meeting_audience.check(
                c,
                auth,
                who,
                owner,
                live_id=body.live_meeting_id,
                roles=body.roles,
                readers=H.task_readers(H.task(c, row["id"])),
                requester=H.task(c, row["id"])["requester_id"],
            )
            c.execute("UPDATE tasks SET live_meeting_id=? WHERE id=?", (body.live_meeting_id, row["id"]))
            meeting_audience.grant(c, row["id"], meeting_participants, who.actor)
        if body.roles:
            try:
                TRo.set_roles(c, who.actor, row["id"], body.roles, mover=mover(c, who) or None)
            except ValueError as exc:
                raise Problem("roles", str(exc), 422)
        note = " ".join(
            filter(
                None,
                (usage_limits.warning(c, owner) or bot_stops.warning(c, owner) or H.setup_hold(c, owner),),
            )
        )
        return {
            "task": task_view(H.task(c, row["id"]), c, visible_sql=auth.task_sql(c, who), who=who),
            **({"readback": readback} if readback else {}),
            **ask_ingest.reply(gate),
            **org_rule_check.answer(rule_gate),
            **({"warning": note} if note else {}),
            **({"warnings": row["warnings"]} if row.get("warnings") else {}),
        }

    def cap_before_preflight(request, who, owner_ref, parent_ref=None):
        """The task cap (CONTRACT §1) and reach (docs/reach.md) before any LLM preflight: a refused send spends no
        judgment and writes nothing. A retry whose Idempotency-Key already has a saved answer is not checked: `mutate`
        replays that answer."""
        if not owner_ref:
            return
        from .shared_bots import route

        with store.read() as c:
            if store.saved(c, who, request.url.path, request.headers.get("idempotency-key")):
                return
            original = auth.target(c, who, owner_ref, need="write")
            owner = auth.target(c, who, route(c, who.actor, original), need="write")
            task_caps.check(c, who.actor, owner)
        reach.precheck(
            store,
            who,
            lambda c: reach.check(
                c, who, owner, "task", task_id=auth.resolve_task(c, who, parent_ref) if parent_ref else None
            ),
        )

    def saved_retry(request, who):
        """Whether this request's Idempotency-Key already has a saved answer: `mutate` replays it without the write, so
        ask ingest does not judge it again (a retry makes no judge call, and so writes no second row either)."""
        with store.read() as c:
            return store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))

    @app.post("/api/v2/tasks")
    def create_task(request: Request, body: M.TaskCreate):
        who = request.state.identity
        if who.via == "botops" and who.role == "bot" and H.is_bot(who.task_actor) and who.task_actor != who.actor:
            # BotOps filing for a bot that asked it: a bot's request lends nothing, so BotOps files the task as itself,
            # with its own rights, and is its requester (who filed it).
            who = Identity(who.task_actor, "bot", runner_id=who.runner_id, attempt_id=who.attempt_id, agent=who.agent)
            request.state.identity = who
        cap_before_preflight(request, who, body.owner, body.parent)
        rc = rule_check(
            who,
            "task_create",
            rule_create_text(body),
            lambda c: [rule_target(c, who, body.owner), *(org_rule_check.company(c) if rule_public(body) else [])],
        )
        # A write the rule check holds, or a saved retry, skips ask ingest's call.
        ingest = (
            None
            if org_rule_check.holds(rc) or saved_retry(request, who)
            else ask_ingest.preflight(store, app.state.judge, request.state.identity, body.owner, body.title, body.body)
        )
        return ask_ingest.judged(
            store,
            ingest,
            lambda: mutate(
                request,
                body,
                lambda c: task_create(c, request.state.identity, body, ingest=ingest, rule=rc),
            ),
        )

    def rule_prepare_task(store_, bot, replay):
        body = replay.get("body") or {}
        return ask_ingest.preflight(
            store_, app.state.judge, bot, body.get("owner"), body.get("title"), body.get("body")
        )

    @org_rule_check.replayer("task_create", prepare=rule_prepare_task)
    def rule_replay_task(c, bot, hold, replay, ingest, gate=None):
        # The create's own privacy refusals run as the bot and its run (privacy.require_create).
        return task_create(
            c, bot, M.TaskCreate.model_validate(replay["body"]), ingest=ingest, rule=org_rule_check.decided(gate)
        )

    @app.post("/api/v2/tasks/dry-run")
    def task_dry_run(request: Request, body: M.TaskCreate):
        """The checks a create would fail, nothing written and no refusal counted."""
        from .mcp import task_dry_run as check

        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return check(c, auth, who, body)

    def task_update_one(c, caller, tid, body, rule=None):
        """`POST /api/v2/tasks/{tid}`: field edits only (H.task_update). Where the task is and who has it change by
        its own routes (task_moves_routes)."""
        # A person's request to BotOps can change a task they own or asked for, as that person.
        who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        if body.private is False and H.task_private(c, row) and (caller.role == "bot" or caller.task_actor):
            raise Problem(
                "privacy",
                "Only the human requester can make a private task company-visible",
                422 if who.role == "bot" else 403,
            )
        if who is not caller:
            H.event(
                c,
                caller.actor,
                "task.update_delegated",
                task_id,
                {"on_behalf_of": who.actor, "message_id": body.on_behalf_of},
            )
        # A bot's edit carries the version it was shown, refused as the engine refuses a stale one (task_changed, with
        # the version the task has now); a person may leave it out. H.task_update checks a given one again.
        from . import task_engine

        task_engine.check_version(c, who.actor, row, body.version)
        if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        # The org rule check (backend/org_rule_check.py): held, the task stays as it is.
        rule_gate = (
            org_rule_check.apply(
                c,
                rule,
                rule_update_text(body, row),
                {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)},
            )
            if rule
            else None
        )
        if rule_gate and rule_gate.get("held"):
            return org_rule_check.answer(rule_gate)
        allowance = 0
        if rule_gate and rule_gate.get("warn"):
            # The warning line goes on the change itself, where the recipient reads it.
            warned = rule_warned_fields(rule_gate, body, row, approved=exact_approval(c, who.actor))
            body, allowance = body.model_copy(update=warned), rule_summary_allowance(rule_gate, warned)
        fields = body.model_dump(exclude={"version", "on_behalf_of"})
        fields["human_id"] = fields.pop("human")  # the column's name: `human` is the API's, as `owner` is
        if "size" in body.model_fields_set and body.size is None:
            fields["clear_size"] = True
        after = H.task_update(
            c,
            who.actor,
            task_id,
            **fields,
            mover=mover(c, who) or None,
            public_allowance=allowance,
            seen_version=body.version,
        )
        warnings = after.get("warnings") if after else None
        return {
            "task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who),
            **({"warnings": warnings} if warnings else {}),
            **org_rule_check.answer(rule_gate),
        }

    @app.post("/api/v2/tasks/{tid}")
    def update_task(request: Request, tid: str, body: M.TaskUpdate):
        caller = request.state.identity
        rc = rule_update_check(caller, tid, body)
        return mutate(request, body, lambda c: task_update_one(c, caller, tid, body, rule=rc))

    @org_rule_check.replayer("task_update")
    def rule_replay_update(c, bot, hold, replay, ingest, gate=None):
        body = M.TaskUpdate.model_validate(replay["body"])
        row = H.task(c, replay["task"])
        if row:
            # `send` refused it if the task changed since the hold (its version is the check's object), so this is
            # the version it was checked at; the route's guard ran there too, as the bot and its run.
            body = body.model_copy(update={"version": row["version"]})
        return task_update_one(c, bot, replay["task"], body, rule=org_rule_check.decided(gate))

    def handoff_preflight(caller, row, owner, note=None):
        """Ask ingest for a bot handing its own task `row` to `owner`: judged like a task filed for them."""
        return ask_ingest.preflight(
            store,
            app.state.judge,
            caller,
            owner,
            row["title"],
            "\n\n".join(filter(None, (row.get("body"), note))),
            kind="task",
        )

    task_bulk_routes.register(app, auth, mover, mutate, store, task_filters, task_idents, task_view)

    task_actions_routes.register(app, auth, mutate, settings_admin)
    task_pipelines_routes.register(app, auth, mutate, store)
    task_factories_routes.register(app, auth, mutate, store)
    preferences_routes.register(app, auth, mutate, store)
    release_schedule_routes.register(app, auth, mutate, store)

    def readable_task(who, tid):
        """The task `who` may read, or None: what an ingest preflight outside the write judges."""
        with store.read() as c:
            try:
                return auth.task(c, who, auth.resolve_task(c, who, tid))
            except Problem:
                return None

    task_comments_routes.register(
        app,
        auth,
        comment_bot,
        comment_way_for,
        mover,
        mutate,
        rule_check,
        rule_comment_audience,
        send,
        settings,
        store,
        task_place,
        visual_view,
    )

    def task_followers_seen(c, who, row):
        """The task's followers this reader may be told of, or None for a task with no thread of its own."""
        tid = task_followers.thread_id(c, row)
        if not tid:
            return None
        rows = task_followers.listed(c, row)
        known = teammate_access.Known(c, who, auth)
        if rows and not known.member(tid):
            unseen = known.unseen_of(r["actor"] for r in rows)
            rows = [r for r in rows if r["actor"] not in unseen]
        return rows

    task_followers_routes.register(
        app, auth, mover, mutate, rule_check, rule_task_audience, store, task_followers_seen, task_place, visual_view
    )

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
            if row["requester_id"] != who.actor and not mover(c, who):
                raise Problem(
                    "forbidden", "Only the task's requester, or someone who may move any task, deletes it", 403
                )
            report = delete_tasks(c, [task_id], apply=True, actor=who.actor, keep_work=True)
            if not report["applied"]:
                reasons = ", ".join(sorted(report["refused"])) or "unknown task"
                raise Problem("has_work", "This task carries work (" + reasons + "); close it instead", 409)
            return {"deleted": task_id}

        return mutate(request, body, work)

    def person_only(who, doing):
        if who.role == "bot" or getattr(who, "via", "") or getattr(who, "task_actor", None):
            raise Problem("forbidden", "Only a person, signed in as themselves, " + doing, 403)

    task_links_routes.register(app, auth, mover, mutate, person_only, settings, store, task_view)

    def report_mutate(request, body, work):
        """`mutate` for a change to a manager bot's report: a refusal over the week's limit still files its Needs you ask
        for the person above, which the refusal's rollback would take with it (settings_admin._report_cap)."""
        try:
            return mutate(request, body, work)
        except Problem as exc:
            ask = getattr(exc, "ask", None)
            if exc.code == "report_cap" and ask:
                store.write(
                    request.state.identity,
                    lambda c: H.task_create(c, ask["actor"], ask["title"], ask["body"], ask["person"]),
                )
            raise

    bot_seats_routes.register(app, auth, mutate, onboarding, report_mutate, settings, settings_admin, store)

    @app.post("/api/v2/bots/{bot}/archive")
    def archive_bot(request: Request, bot: str, body: M.BotArchive):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            # A manager bot's ask is already BotOps acting as that bot (act_for_requester), not a person's request to find.
            acting = (
                who
                if who.role == "bot" and who.via == "botops"
                else (delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who)
            )
            result = settings_admin.archive(c, acting, bot, body)
            rooms.archive_shared_room(c, acting.actor, bot)
            if acting is not who:
                H.event(
                    c,
                    who.actor,
                    "bot.archive_delegated",
                    bot,
                    {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of, "successor": body.successor},
                )
            return result

        return report_mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/restore")
    def restore_bot(request: Request, bot: str, body: M.Empty):
        """Bring an archived bot back: the owner, an admin or the bot's owner, to the status it had, else planned."""
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
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
        return bool(task) and task["status"] not in ("done", "closed")

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
        moved = c.execute(
            "SELECT new, actor_id AS actor, via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='reassign' "
            "ORDER BY created_at DESC, rowid DESC "
            "LIMIT 1",
            (task["id"],),
        ).fetchone()
        if not moved:
            made = c.execute(
                "SELECT detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
                (task["id"],),
            ).fetchone()
            return None if made and H._json(made["detail"], {}).get("owner") == botops else ""
        moved = dict(moved)
        if moved["new"] == botops and moved["actor"] == task["requester_id"] and not moved.get("via"):
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
            who = Identity("bot:" + BOTOPS, "bot", runner_id=who.runner_id, attempt_id=who.attempt_id, agent=who.agent)
        if who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "Only BotOps applies changes on a requester's behalf", 403)

        def acting_as(principal, cited="", run="live"):
            # Keep the actual bot's credential so writes revalidate its lease under the lock,
            # and private reads intersect the requester with the bot doing the work.
            # Never confirmed: only the person's own tap on a Confirm card is (auth.py, x-tico-assistant-action), so
            # every route's "always a click" rule (`risky`) asks them.
            if H.is_human(principal.actor):
                H.DELEGATION.set({"for": principal.actor, "cited": cited, "run": run})
            return replace(
                principal,
                via="botops",
                confirmed=False,
                task_actor=who.actor,
                runner_id=who.runner_id,
                attempt_id=who.attempt_id,
                agent=who.agent,
            )

        message_id = ref
        explicit = bool(ref) and ref not in ("turn", "default")
        turn = (
            c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            if who.attempt_id
            else None
        )
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
            requester = task["requester_id"] if task else (initial or {}).get("sender_id", "")
            if task and H.is_human(requester):
                creator = c.execute(
                    "SELECT actor FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
                    (task_id,),
                ).fetchone()
                if creator and H.is_bot(creator["actor"]):
                    requester = creator["actor"]
                requester = filed_for_bot(c, task_id, who) or requester
            handed = (
                handed_over(c, task, who.actor)
                if task and H.is_human(requester) and task["owner_id"] == who.actor
                else None
            )
            if handed is not None:
                # Someone other than the person gave BotOps their task: it runs with the rights of the bot that moved
                # it (its refusals become the person's Confirm card), never the person's.
                if not H.is_bot(handed) or handed == who.actor:
                    raise Problem(
                        "on_behalf_of",
                        "Someone other than the person who asked for this task gave it to BotOps, "
                        "so it lends no one's rights; they can move it to BotOps themselves or ask in their "
                        "BotOps chat",
                        403,
                    )
                requester = handed
            others_comment = False
            if task and initial:
                refs = initial.get("refs") or {}
                relayed = any(refs.get(key) for key in ("via", "assistant", "slack", "routing"))
                if (
                    refs.get("comment")
                    and not relayed
                    and H.is_human(requester)
                    and initial["sender_id"] == requester
                    and task["owner_id"] == who.actor
                    and open_task(task)
                ):
                    # The task's requester commenting on it, in their own words: that is them asking.
                    auth.conversation(c, who, initial["conversation_id"])
                    H.VIA.set("botops")
                    return acting_as(auth.identity_for_actor(c, requester), task_id, "comment")
                if (
                    refs.get("comment")
                    and not relayed
                    and handed is not None
                    and initial["sender_id"] == task["requester_id"]
                ):
                    raise Problem(
                        "on_behalf_of",
                        "A bot gave this task to BotOps, so your comment doesn't lend your rights; "
                        "move it to BotOps yourself or ask in your BotOps chat",
                        403,
                    )
                if (
                    refs.get("comment")
                    and not relayed
                    and H.is_bot(requester)
                    and H.is_human(initial["sender_id"])
                    and task["owner_id"] == who.actor
                    and open_task(task)
                ):
                    # A person's comment on a task a bot asked BotOps for lends nothing: BotOps keeps that bot's rights,
                    # and what they can't do becomes a Confirm card for the person who commented (confirm_for_carried).
                    others_comment = True
                elif refs.get("comment") or relayed:
                    raise Problem(
                        "on_behalf_of",
                        "Only the task's requester's own comment lends their rights; "
                        "other comments and routed messages lend nothing",
                        403,
                    )
            if initial and (task or str(requester).startswith("bot:")) and requester != H.KEEPER:
                auth.conversation(c, who, initial["conversation_id"])
                lapsed = botops_act.lend_lapsed(c, task) if task and H.is_human(requester) else ""
                if lapsed:
                    raise Problem("on_behalf_of", lapsed, 403)
                if not open_task(task) and week_old(initial["created_at"]):
                    raise Problem("on_behalf_of", "That request is more than a week old; ask the requester again", 403)
            if str(requester).startswith("bot:") and requester != who.actor:
                if (H.bot(c, H.actor_id(requester)) or {}).get("state") != "active":
                    raise Problem("forbidden", "The requesting bot is no longer active", 403)
                person = lending_carried(c, task, requester) if carry else None
                if person and others_comment and person["person"] != initial["sender_id"]:
                    person = None  # someone else's comment never lends the carried person's rights
                if person:
                    # The bot handed on a person's own request (carried_request): BotOps acts as that person.
                    H.VIA.set("botops")
                    return acting_as(auth.identity_for_actor(c, person["person"]), person["message_id"], "carried")
                return acting_as(Identity(requester, "bot"))
            if not initial or requester == H.KEEPER or requester == who.actor:
                return who
            if task and task["owner_id"] == who.actor and not task.get("request_id"):
                if str(task["requester_id"]).startswith("human:"):
                    made = c.execute(
                        "SELECT detail FROM events WHERE action='task.create' AND target=? AND actor=? LIMIT 1",
                        (task_id, task["requester_id"]),
                    ).fetchone()
                    if made and H._json(made["detail"], {}).get("via") != "assistant":
                        person = auth.identity_for_actor(c, task["requester_id"])
                        return acting_as(person, task_id)
                origin = c.execute(
                    "SELECT actor FROM events WHERE action='botops.task_requested' AND target=? AND actor=? LIMIT 1",
                    (task_id, task["requester_id"]),
                ).fetchone()
                if origin and str(origin["actor"]).startswith("human:"):
                    auth.conversation(c, who, task["conversation_id"])
                    person = auth.identity_for_actor(c, origin["actor"])
                    H.VIA.set("botops")
                    return acting_as(person, task_id)
            asked = (
                c.execute(
                    "SELECT detail FROM events WHERE action='botops.asked_via_assistant' AND target=? "
                    "AND actor=? LIMIT 1",
                    (task_id, task["requester_id"]),
                ).fetchone()
                if task
                else None
            )
            if asked and task["owner_id"] == who.actor and H.is_human(task["requester_id"]) and open_task(task):
                # The person's own words to their Assistant, checked when it filed this (task_create): BotOps acts
                # as them, as if they had asked it in their BotOps chat.
                origin = H.message(c, (H._json(asked["detail"], {}) or {}).get("cited") or "")
                if (
                    not origin
                    or origin.get("deleted_at")
                    or origin["sender_id"] != task["requester_id"]
                    or week_old(origin["created_at"])
                ):
                    raise Problem(
                        "on_behalf_of",
                        "The request the Assistant handed on no longer stands; ask the person again",
                        403,
                    )
                H.VIA.set("botops")
                return acting_as(auth.identity_for_actor(c, task["requester_id"]), origin["id"], "assistant")
            if task and task.get("request_id") in ("turn", "default"):
                # Tasks filed before the request was stored by id: the person's message the filing cited.
                made = c.execute(
                    "SELECT detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
                    (task_id,),
                ).fetchone()
                task = {
                    **task,
                    "request_id": ((H._json(made["detail"], {}) if made else {}).get("delegation") or {}).get("cited"),
                }
            if task and task.get("request_id") and task["owner_id"] == who.actor:
                origin = H.message(c, task["request_id"])
                made = c.execute(
                    "SELECT 1 FROM events WHERE action='task.create' AND target=? AND actor=? LIMIT 1",
                    (task_id, task["requester_id"]),
                ).fetchone()
                if made and origin and origin["sender_id"] == task["requester_id"]:
                    message_id = task["request_id"]
                    c.execute(
                        "INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)",
                        (who.attempt_id, origin["conversation_id"]),
                    )
        msg = H.message(c, message_id)
        started = (H.message(c, turn["message_id"]) or {}).get("sender_id", "") if turn else ""
        if explicit and started and msg and msg["sender_id"] != started:
            # A turn a person started acts for that person: another person's message id borrows nothing.
            raise Problem("on_behalf_of", "Cite the message of the person who asked you, not someone else's", 403)
        if (
            not msg
            or not str(msg["sender_id"]).startswith("human:")
            or msg["recipient_id"] != "bot:" + BOTOPS
            or msg["kind"] not in ("say", "answer")
        ):
            raise Problem("on_behalf_of", "Cite a message a person sent BotOps asking for this change", 403)
        refs = msg.get("refs") or {}
        if refs.get("via") or refs.get("assistant"):
            # The Assistant wrote this for the person: its writes never carry the person's authority on.
            raise Problem("on_behalf_of", "That message was written by the Assistant; ask the person to send it", 403)
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        if conversation.get("task_id") or conversation.get("scope") == "task" or refs.get("task"):
            raise Problem(
                "on_behalf_of", "A person's words inside a task are not a request to BotOps; ask in chat", 403
            )
        slack = refs.get("slack") if isinstance(refs.get("slack"), dict) else {}
        own_dm = (
            slack.get("kind") == "im"
            and not slack.get("recorded_only")
            and set(conversation.get("participants") or []) == {msg["sender_id"], "bot:" + BOTOPS}
        )
        if (refs.get("slack") or refs.get("routing")) and not own_dm:
            # Anyone in a Slack channel or thread can put words in a routed message, so those never lend a person's
            # rights. A 1:1 Slack DM is different: the gateway verified the sender (workspace member, not a guest,
            # admitted, a Tico person) and nobody else writes in it, so it counts like their Tico chat (owner
            # decision, 2026-10-01). The message decides, not the room: what they type in Tico is always their own request.
            raise Problem(
                "on_behalf_of",
                "This request arrived through a Slack channel, so BotOps can't act on it for "
                "the person. They can DM it to the Tico app in Slack or send it in their Tico chat with BotOps",
                403,
            )
        if explicit and set(conversation.get("participants") or []) != {msg["sender_id"], "bot:" + BOTOPS}:
            # A message cited by id: the person's own, in their own room with BotOps (not a room another person spoke in).
            raise Problem("on_behalf_of", "Cite a message from the person's own chat with BotOps", 403)
        if week_old(msg["created_at"]) and (
            new_task
            or not any(
                lending_task(c, H.task(c, row["id"]))
                for row in c.execute(
                    "SELECT id FROM tasks WHERE request_id=? AND requester_id=?", (message_id, msg["sender_id"])
                ).fetchall()
            )
        ):
            raise Problem(
                "on_behalf_of",
                "That request is more than a week old and no open task carries it; ask the person again",
                403,
            )
        # Only a request in a conversation this run may read: otherwise any message a bot sends
        # BotOps could borrow the rights of whoever last asked it for something.
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("on_behalf_of", "Cite a request from the conversation you are working on", 403) from None
        person = auth.identity_for_actor(c, msg["sender_id"])
        H.VIA.set("botops")  # every event and history row from here says "via BotOps"
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
        turn = (
            c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot_id=?",
                (who.attempt_id, H.actor_id(who.actor)),
            ).fetchone()
            if who.attempt_id
            else None
        )
        start = H.message(c, turn["message_id"]) if turn else None
        if not ref:
            refs = (start or {}).get("refs") or {}
            slack = refs.get("slack") if isinstance(refs.get("slack"), dict) else {}
            if (
                start
                and H.is_human(start["sender_id"])
                and start["recipient_id"] == who.actor
                and not any(refs.get(k) for k in ("via", "assistant", "routing"))
                and (not refs.get("slack") or slack.get("kind") == "im")
            ):
                return {
                    "person": start["sender_id"],
                    "message_id": start["id"],
                    "lends": False,
                    "why": "No message was cited",
                }
            return None
        msg = start if ref == "turn" else H.message(c, ref)
        if not msg or not H.is_human(msg["sender_id"]) or msg.get("deleted_at"):
            raise Problem(
                "request_id",
                "Cite a message a person wrote to you (its id, or `turn` for the one that started this run)",
                422,
            )
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("request_id", "Cite a message from a conversation you are working on", 403) from None
        person = msg["sender_id"]
        refs = msg.get("refs") or {}
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        task = H.task(c, H.message_task_id(msg, conversation) or "")
        slack = refs.get("slack") if isinstance(refs.get("slack"), dict) else {}
        own_dm = slack.get("kind") == "im" and not slack.get("recorded_only")
        if msg["recipient_id"] != who.actor or msg["kind"] not in ("say", "answer"):
            why = "That message was not sent to you"
        elif refs.get("via") or refs.get("assistant"):
            why = "The Assistant wrote that message"
        elif refs.get("routing") or (refs.get("slack") and not own_dm):
            why = "That message came from a channel others write in"
        elif not start or start["conversation_id"] != msg["conversation_id"]:
            why = "That message is not in the conversation this run is working on"
        elif task and not (
            refs.get("comment") and task["requester_id"] == person and task["owner_id"] == who.actor and open_task(task)
        ):
            why = "Only the person's own comment on a task they asked you for carries their request"
        elif not task and set(conversation.get("participants") or []) != {person, who.actor}:
            why = "That message is not from the person's own chat with you"
        elif week_old(msg["created_at"]):
            why = "That message is more than a week old"
        else:
            why = ""
        return {"person": person, "message_id": msg["id"], "lends": not why, "why": why}

    def carried(c, task, requester):
        """What `carried_request` recorded on this task, filed by `requester`, or None."""
        row = (
            c.execute(
                "SELECT detail FROM events WHERE action='botops.request_carried' AND target=? AND actor=? "
                "ORDER BY created_at DESC LIMIT 1",
                (task["id"], requester),
            ).fetchone()
            if task
            else None
        )
        return H._json(row["detail"], {}) if row else None

    def filed_for_bot(c, task_id, who):
        """The bot a task was filed for, when BotOps filed it with rights that bot's hand-off lent (carried_request):
        it lends what that bot's rights do, never the person's, or a carried call could file itself a task that lends
        everything. None for any other task."""
        made = c.execute(
            "SELECT detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
            (task_id,),
        ).fetchone()
        lent = (H._json(made["detail"], {}) if made else {}).get("delegation") or {}
        if not isinstance(lent, dict) or lent.get("run") != "carried":
            return None
        cited = H.message(c, lent.get("cited") or "")
        return cited["recipient_id"] if cited and H.is_bot(cited["recipient_id"]) else who.actor

    def lending_carried(c, task, requester):
        """The carried request when it still lends the person's rights: the task is open and the message still stands,
        from that person, within DELEGATION_DAYS."""
        found = carried(c, task, requester)
        if not found or not found.get("lends") or not open_task(task):
            return None
        if botops_act.reopened_by(c, task["id"]):
            return None  # finished once: the request it carried was done, whoever reopened it
        msg = H.message(c, found.get("message_id") or "")
        if not msg or msg.get("deleted_at") or msg["sender_id"] != found.get("person") or week_old(msg["created_at"]):
            return None
        return found

    def confirm_for_carried(request, exc):
        """A Confirm card for the person behind a bot's hand-off when BotOps, with only that bot's rights, was refused:
        what BotOps tried, run as the person on their own click. None when there is no such person or no such card."""
        who, acting = getattr(request.state, "privacy_source", None), getattr(request.state, "identity", None)
        if (
            exc.status != 403
            or exc.code != "forbidden"
            or not who
            or who.actor != "bot:" + BOTOPS
            or not acting
            or acting.role != "bot"
            or acting.actor == who.actor
            or request.method == "GET"
            or not who.attempt_id
        ):
            return None
        path = botops_act.normalize(request.url.path)
        body = botops_act.parse_body(getattr(request, "_body", b"")) if request.method != "DELETE" else None
        body = body if isinstance(body, dict) else {}
        if (
            not path
            or botops_act.secret_in_request(request.method, path, body)
            or not assistant_valid(request.method, path, settings, BOTOPS)
        ):
            return None
        with store.transaction() as c:
            turn = c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            start = H.message(c, turn["message_id"]) if turn else None
            task = H.task(c, H.message_task_id(start) or "") if start else None
            said = (start or {}).get("refs") or {}
            commented = (
                task
                and task["requester_id"] == acting.actor
                and task["owner_id"] == who.actor
                and said.get("comment")
                and H.is_human(start["sender_id"])
                and not any(said.get(k) for k in ("via", "assistant", "slack", "routing"))
            )
            # The person whose comment started this run asked, ahead of whoever the bot's hand-off named.
            found = (
                {"person": start["sender_id"]}
                if commented
                else carried(c, task, acting.actor)
                if task and task["requester_id"] == acting.actor
                else None
            )
            if (
                not found
                and task
                and H.is_human(task["requester_id"])
                and task["owner_id"] == who.actor
                and handed_over(c, task, who.actor) == acting.actor
            ):
                found = {"person": task["requester_id"]}  # the bot gave BotOps a task this person asked for
            if not found or not open_task(task):
                return None
            try:
                person = auth.identity_for_actor(c, found["person"])
            except Problem:
                return None
            if botops_act.admin_only(request.method, path) and person.role != "owner" and not auth.bot_admin(person):
                return None
            asker = (H.bot(c, H.actor_id(acting.actor)) or {}).get("name") or H.actor_id(acting.actor)
            summary = (
                ("BotOps needs your OK to do this on " + asker + "'s task: ")
                if commented
                else (asker + " asked BotOps for this for you: ")
            ) + task["title"]
            return propose_card(
                c, person, request.method, path, {k: v for k, v in body.items() if k != "on_behalf_of"}, summary[:300]
            )

    def followed_task(c, ref):
        """The task `ref` names (its id, or an unambiguous prefix of eight or more), or None when it names none."""
        ref = str(ref or "")
        prefix = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = c.execute(
            "SELECT id FROM tasks WHERE id=? OR (length(?)>=8 AND id LIKE ? || '%' ESCAPE '\\') LIMIT 2",
            (ref, ref, prefix),
        ).fetchall()
        exact = [r for r in rows if r["id"] == ref]
        return H.task(c, (exact or rows)[0]["id"]) if len(exact or rows) == 1 else None

    def follow_through(c, who, task):
        """The person whose open request BotOps is following through on, in a later run: a daily-update wake, a retry
        once a busy bot finished, a notice. The run that started it is gone, so BotOps cites the task the person asked
        for. Only a task BotOps owns, that the person filed themselves (not through the Assistant), for as long as it is
        open; and never from a run a bot or another person started, which keeps its own requester's
        rights."""
        requester = task["requester_id"]
        if task["owner_id"] != who.actor or not H.is_human(requester):
            raise Problem("on_behalf_of", "Cite a task a person asked BotOps for", 403)
        if task["status"] in ("done", "closed"):
            raise Problem("on_behalf_of", "That task is closed; ask the person again", 403)
        lapsed = botops_act.lend_lapsed(c, task)
        if lapsed:
            raise Problem("on_behalf_of", lapsed, 403)
        made = c.execute(
            "SELECT actor,detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
            (task["id"],),
        ).fetchone()
        via = H._json(made["detail"], {}).get("via") if made else None
        if not made or made["actor"] != requester or via == "assistant" or filed_for_bot(c, task["id"], who):
            raise Problem("on_behalf_of", "That task was not filed by the person who asked for it", 403)
        if handed_over(c, task, who.actor) is not None:
            raise Problem(
                "on_behalf_of", "Someone other than the person who asked for that task gave it to BotOps", 403
            )
        if via and not task.get("request_id"):
            # Filed by BotOps as the person: only a continuation of their message (--request-id) counts, or a
            # follow-through run could file a fresh task as them and so lend their rights for ever.
            raise Problem(
                "on_behalf_of", "BotOps filed that task without the person's message; ask the person again", 403
            )
        origin = H.message(c, task["request_id"]) if task.get("request_id") else None
        if task.get("request_id") and (not origin or origin["sender_id"] != requester):
            raise Problem("on_behalf_of", "That task's request is not the person's own", 403)
        current = delegated_identity(c, who, "turn")
        if current.actor not in (who.actor, requester):
            raise Problem(
                "on_behalf_of", "This run is for " + current.actor + "; it keeps that requester's rights", 403
            )
        if current.actor == who.actor:
            # A run with no requester lends a person's rights only when the keeper started it on its own schedule. A
            # Slack digest, a live meeting, a watcher or a task comment also start such runs, with words anyone there
            # wrote; those never carry a person's rights (Slack and comments are refused the same way above).
            turn = c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            start = H.message(c, turn["message_id"]) if turn else None
            refs = (start or {}).get("refs") or {}
            if (
                not start
                or start["sender_id"] != H.KEEPER
                or start["kind"] != "notice"
                or refs.get("wake") not in FOLLOW_THROUGH_WAKES
                or any(refs.get(k) for k in ("slack", "routing", "via", "assistant", "comment", "live_meeting"))
            ):
                raise Problem(
                    "on_behalf_of",
                    "This run started from words others wrote; act on the person's task in "
                    "their chat or the next daily update",
                    403,
                )
        H.VIA.set("botops")
        return auth.identity_for_actor(c, requester)

    def botops_owns_task(path, body=None):
        """A note, comment or status on a task BotOps owns is BotOps' own work: it needs no one's rights and is BotOps'
        words, so it is never recorded as the person who asked (their rights still apply to everyone else's tasks).
        Closing is the exception: only the requester or a human closes, so a close (a move) keeps the person's rights.
        Its own task's worktrees are its own work too: the route checks the owning bot's repository access, which no
        one's rights change. So is an ask or a sub-task for a person under its own task: the ask grants nothing by
        itself, whoever asked for the task."""
        body = body if isinstance(body, dict) else {}
        person = None
        if path in ("/api/v2/tasks", "/api/v2/tasks/dry-run"):
            person = body.get("owner")
            ref = body.get("parent")
            # Only a plain ask: no roles, goals, links or relations reached with BotOps' rights for whoever asked.
            if (
                not ref
                or not person
                or set(body) - {"title", "body", "owner", "parent", "due", "private", "on_behalf_of"}
            ):
                return False
        else:
            found = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(?:comments|asks|links|worktrees(?:/attach)?))?", path)
            if not found:
                return False
            ref = found.group(1)
        with store.read() as c:
            row = followed_task(c, ref)
            if person is not None and not H.is_human(H.resolve_actor(c, person) or ""):
                return False
        return bool(row and row["owner_id"] == "bot:" + BOTOPS)

    def manager_asker(c, who):
        """The manager bot whose chat message to BotOps started this run, as the identity BotOps acts as for a report's
        create, archive or restore (`Auth.report_lifecycle`): its actor is the manager and `via` is "botops", so its own
        token, which has no `via`, is never mistaken for it. None when a bot did not ask."""
        turn = (
            c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            if who.attempt_id
            else None
        )
        msg = H.message(c, turn["message_id"]) if turn else None
        if (
            not msg
            or msg.get("deleted_at")
            or msg["recipient_id"] != "bot:" + BOTOPS
            or msg["kind"] not in ("say", "answer")
            or not H.is_bot(msg["sender_id"])
        ):
            return None
        return replace(who, actor=msg["sender_id"], role="bot", via="botops", task_actor="")

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
                acting = delegated_identity(
                    c, who, ref, carry=botops_act.carried_lends(request.method, request.url.path)
                )
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
            raise Problem(
                "person_only",
                why,
                403,
                extra=botops_act.refusal_for_person(
                    path, "Do this yourself in " + settings.app_name, settings.public_url
                ),
            )
        body = {k: v for k, v in body.items() if k != "on_behalf_of"} if isinstance(body, dict) else {}
        rules = team_rules.load(c)

        def lowers():
            rest = path[len(botops_act.API) :]
            if rest.startswith("usage/pools/"):
                scope = rest[len("usage/pools/") :]
                return usage_limits.pool_only_lowers(
                    c, "company" if scope == "company" else "group:" + scope.split("/", 1)[-1], body
                )
            return usage_limits.only_lowers(
                c, rest[len("usage/limits/") :] if rest.startswith("usage/limits/") else None, body
            )

        # A Gateway URL change sends the saved key to the proposed address as a connection check.
        # Require the owner's tap whenever a key is saved, even when botops_direct is on. A key the server's
        # environment sets can't be changed here at all, so that save is refused at once rather than carded.
        gateway_put = method == "PUT" and path == botops_act.API + "providers/gateway"
        moved, shown, what, refusal = destination_card(c, acting, method, path, body)
        gateway_key_address_change = (
            gateway_put
            and (bool(body.get("url")) or bool(moved))
            and (gateway_connection.connected(c, settings) and not gateway_connection.env_key(settings))
        )
        if not path or not botops_act.card_for_person(
            method, path, body, rules, lowers, force_confirm=gateway_key_address_change or bool(moved)
        ):
            return None
        # Told at once, not handed a card that fails on the tap: first in the catalogue's words, which know whose a
        # trigger, webhook or computer is (backend/settings_catalog.py).
        said = settings_catalog.raw_refusal(c, auth, acting, method, path, body, settings.public_url)
        if said:
            raise Problem("forbidden", said["fix"], 403, extra=said)
        who_may = (
            "the owner"
            if botops_act.owner_only(method, path, body) and acting.role != "owner"
            else "an owner or an admin"
            if botops_act.admin_only(method, path) and acting.role != "owner" and not auth.bot_admin(acting)
            else ""
        )
        if who_may:
            raise Problem(
                "forbidden",
                "Only " + who_may + " can do this",
                403,
                extra=botops_act.refusal_for_person(
                    path, "Your role can't change this: ask " + who_may, settings.public_url
                ),
            )
        if refusal:
            raise refusal
        if aid := botops_act.automation_deleted(method, path):
            from . import automations

            automations.deletable(c, acting, aid)  # refused at once (404, 403, a built-in 409)
        with store.transaction() as w:
            card = propose_card(
                w, acting, method, path, body, botops_act.card_summary(method, path, body), what=what, shown=shown
            )
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
            return (
                None,
                [{"field": "destination", "old": old, "new": new}, {"field": "path", "new": seen["path"]}, *others],
                (what + and_also)[:300],
                None,
            )
        option = destinations.choice(body.get("credentials"))
        if not option:
            return moved, None, None, destinations.held(integration, moved, labels, current, name)
        if body.get("expected_revision") is None:
            return (
                moved,
                None,
                None,
                Problem(
                    "expected_revision",
                    "Changing where saved credentials go needs expected_revision: the revision you read",
                    422,
                ),
            )
        if not isinstance(body["expected_revision"], int) or body["expected_revision"] != int(current):
            return (
                moved,
                None,
                None,
                Problem("version_conflict", name + " changed since you read it; read it again", 409),
            )
        old, new, listed = moved["old"]["address"], moved["new"]["address"], ", ".join(labels)
        what = (
            f"Send {name}'s saved {listed} to {new} instead of {old}"
            if option == destinations.KEEP
            else f"Move {name} to {new} and clear its saved {listed}"
        )
        shown = [
            {"field": "destination", "old": old, "new": new},
            *([{"field": "path", "new": moved["path"]}] if "path" in moved else []),
            {"field": "credentials", "new": ("keep: " if option == destinations.KEEP else "clear: ") + listed},
            *others,
        ]
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

        row = c.execute("SELECT runner_id FROM attempts WHERE id=? AND bot_id=?", (attempt_id, BOTOPS)).fetchone()
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
        card = create_proposal(
            c, settings, acting, room, BOTOPS, summary, method, path, body, what=what, routes=app.routes, shown=shown
        )
        return {
            "proposed": True,
            "needs_confirm": True,
            **card,
            "detail": "This needs "
            + ((H.human(c, H.actor_id(acting.actor)) or {}).get("name") or "the person")
            + "'s own OK. A Confirm card is in their chat with BotOps; nothing has changed yet.",
        }

    app.state.botops_propose = propose_card  # a route outside this file asking for the card (backend/repositories.py)

    def risky(who):
        """A delegated identity that has not been confirmed: BotOps acting for a person, before their click."""
        return who.via == "botops" and not who.confirmed

    # The task-write rule check, also for task writes outside the v3 routes (backend/goal_routes.py).
    task_rules = app.state.task_rules = task_rule_gate.TaskRules(
        auth, rule_check, rule_task_audience, rule_target, store
    )
    # After delegated_identity: a change BotOps makes for a person is made as them.
    task_moves_routes.register(
        app,
        auth,
        delegated_identity,
        handoff_preflight,
        mover,
        mutate,
        task_rules,
        store,
        task_view,
    )
    bot_status_routes.register(app, auth, delegated_identity, mutate, settings_admin, store, reset_bot_sessions)

    computers_routes.register(app, auth, execution, mutate, settings, store, telemetry)

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
        raise refusal

    @app.get("/api/v2/fleet/check")
    def fleet_check(request: Request):
        """What is wrong with the bots this person may see, most urgent first, each with its one fix."""
        who = request.state.identity
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
                            "kind": check["id"],
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

    fleet_runner_routes.register(
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
            "task_answer": lambda c, bot, replay: rule_task_audience(c, task_row(c, bot, replay), bot.actor),
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

    from .tags import install as install_tags

    install_tags(app, store, auth, mutate, task_views)
    # One stream of live events for people's pages (backend/events.py).
    from .events import install as install_events, install_runner as install_runner_events

    install_events(app, store, auth, task_views, task_view)
    app.state.task_view = task_view  # for the events' read in tests (backend/tests/test_payload_budgets.py)
    # And one for each computer, in place of its polls (runner-rs/src/events.rs).
    install_runner_events(app, store, execution)
    app_delivery.install(app, store, execution, mutate)

    from .views import install_views
    from .roadmaps import install_internal

    install_internal(app, store, auth, mutate, mover)
    from .public_roadmaps import install as install_public_roadmaps

    install_public_roadmaps(app, store, auth, mutate)
    from .company_page import api as company_page_api

    company_page_api.register(app, store, auth, mutate, settings)
    from .service_keys import install_service_keys

    install_service_keys(app, store, auth, mutate)
    # People's own Claude Code (docs/skills.md, People's Claude Code): the helper's devices, then its marketplace.
    from . import skill_devices, skill_marketplace

    skill_devices.install(app, store, auth, mutate, settings)
    skill_marketplace.install(app, store, auth, settings)
    install_views(app, store, auth, mutate, task_view)

    @app.get("/api/v2/attempts/{aid}/goal")
    def attempt_goal(request: Request, aid: str):
        with store.read() as c:
            attempt = execution.attempt(c, request.state.identity, aid)
            msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0])
            from .chat_goals import current

            return {"goal": current(c, msg["conversation_id"])}

    from . import skill_runtime

    skill_runtime.install(app, store, execution, mutate)  # company skills a runner stages, and their use
    from .chat_goals import install as install_chat_goals

    install_chat_goals(app, store, auth, mutate, send)
    from .bot_task_tabs import install as install_bot_task_tabs

    install_bot_task_tabs(app, store, auth, mutate, settings, task_views)
    from .cards import install as install_cards

    install_cards(app, store, auth, mutate, settings, task_view)

    @app.get("/api/v2/runners/me/memory-tidy-keeps")
    def retired_memory_tidy_keeps(request: Request):
        """The retired Nightly memory tidy pilot's Keep queue: always empty. runner-rs (memory_tidy_keep.rs) still asks
        every 15 seconds; remove this once no runner does."""
        if request.state.identity.role != "runner":
            raise Problem("identity", "A registered computer is required", 403)
        return {"keeps": []}

    turn_work.install(app, store, auth)
    from . import run_view

    run_view.install(app, store, auth)
    from .documents import install_documents

    install_documents(app, store, auth, mutate)
    from .docs import install_docs

    install_docs(app, store, auth, mutate)
    from .context_search import install_context_search

    install_context_search(app, store, auth)
    from . import task_context

    task_context.install(app, store, auth)
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
    from .brand import install as install_brand

    install_brand(app, store, auth, mutate)
    from .growth import install as install_growth

    install_growth(app, store, auth, mutate)  # channels, sources, cohorts, unit economics, KPIs
    from .growth_numbers import install as install_growth_numbers

    install_growth_numbers(app, store, auth)  # the Growth page's numbers (read-only)
    from .growth_finance import install as install_growth_finance

    install_growth_finance(app, store, auth)  # Finance ad spend source: the campaign mapping step
    from .strategy_setup import install as install_strategy_setup

    install_strategy_setup(app, store, auth, mutate)
    from .lenses import install as install_lenses

    install_lenses(app, store, auth, mutate)
    from .listening import install as install_listening

    install_listening(app, store, auth, mutate)
    from .archive import install_archives

    install_archives(app, store)
    from .media import install_media

    install_media(app, store, auth, mutate, send, task_create, chat_upload_checks, chat_upload_write)
    from .downloads import install_downloads

    install_downloads(app, store)
    from .team_icon import install_team_icon

    install_team_icon(app, store)
    from .meeting_items import install_meeting_items

    install_meeting_items(app, store, mutate, task_create, auth)
    pipeline_judges.install(app, store, mutate, auth)
    from . import stage_rules

    stage_rules.install(store)
    from . import presence

    presence.install(app, store, mutate, auth)
    from .meeting_task_links import install as install_meeting_task_links

    install_meeting_task_links(app, store, auth, mutate)
    from .connectors import install_connectors

    install_connectors(app, store, execution, mutate)
    from .calendar_api import install_calendar

    install_calendar(app, store, mutate)
    from .imports import install_imports

    install_imports(app, store, auth, execution, mutate)
    from .granola_mcp import install_granola

    install_granola(app)
    recorder_installs.install(app, store)  # before /api/v2/meetings/{id}
    from .meeting_review import install_meeting_review

    install_meeting_review(app, store, auth, mutate)
    from .credentials import install_credentials

    install_credentials(app, store, delegate=delegated_identity, propose=propose_card)
    from . import api_tools

    api_tools.install(app, store, auth, settings)  # Tools: API tools, their keys and levels, and the tool index
    from . import website_tools

    website_tools.install(app, store, auth, settings)  # Tools: website tools and their accounts
    from . import tool_proxy

    tool_proxy.install(app, store)
    from . import browser_commands

    browser_commands.install(app, store, auth, mutate)  # website tools' browser: bots' verbs, computers, live view
    from .finance import actions as finance_vault

    finance_vault.use_vault(app.state.vault)  # never one an earlier app left behind (the scheduler may be off)
    from .google_workspace import install as install_google_workspace

    install_google_workspace(app, store)
    from . import web_push

    web_push.install(app, store)  # after install_credentials: the VAPID key is sealed with the vault
    from .mailboxes import install as install_mailboxes

    install_mailboxes(app, store)
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
    from . import thread_catchup

    thread_catchup.install(app, store, auth, mutate)
    from . import maintenance, system_routines

    system_routines.install(app, store, auth, mutate)
    maintenance.install(app, store, auth, mutate)
    from . import heartbeat as heartbeat_screen

    heartbeat_screen.install(app, store, auth, mutate)
    from . import rules as wake_rules_api

    wake_rules_api.install(app, store, auth)  # the event catalog; wake rules are automations now
    turn_budgets.install(app, store, auth, mutate, settings_admin)
    from . import automations

    automations.install(app, store, auth, mutate)
    from . import audit_log

    audit_log.install(app, store, auth)
    task_caps.install(app, store, auth, mutate)
    from . import ci_runs

    ci_runs.install(app, store, auth, mutate)  # CI build time: a builder's remote-run permit, a ticket's extension
    from . import org_rules

    # Org rule checks first: their /api/v2/rules/stats and /check come before org_rules' /api/v2/rules/{rule_id}.
    from .automations import replay_audience, replay_step

    org_rule_check.REPLAY["automation"] = replay_step
    org_rule_check.AUDIENCE.update(rule_audiences())
    org_rule_check.AUDIENCE["automation"] = replay_audience
    org_rule_check.install(app, store, auth, mutate)
    thread_api.install(
        app, store, auth, mutate, rule_check
    )  # thread routes (§3.4); /threads/search before /threads/{id}
    from . import thread_tasks

    thread_tasks.install(app, store, auth, create_task, settings.public_url or "")  # Make task from a message
    counsel.install(app, store, auth, mutate)  # before org_rules: /rules/read and /rules/ask come before /rules/{id}
    org_rules.install(app, store, auth, mutate)
    from . import stage_rules_api

    stage_rules_api.install(app, store, auth, mutate)  # stage rules: the Rules button, the Rules page's groups
    why_here.install(app, store, auth)  # GET /api/v2/origins/{ref}: why an item is on the list
    assistant_changes.install(app, store, auth, mutate, settings_admin)  # the Assistant's changes, and undoing one
    reach.install(app, store, auth, mutate)  # Rules › Internal: who reaches whom (docs/reach.md)
    from . import feedback

    feedback.install(app, store, auth, mutate)  # 👍/👎 on bot work (backend/feedback.py)
    profiles.install(app, store, auth, mutate, settings_admin)
    from . import screening

    screening.install(app, store, auth, mutate)  # prompt-injection screening at webhooks, mail, ideas and watchers
    from . import finance_routes

    finance_routes.install(app, store, auth, mutate)  # Finance: budgets, P&L, expenses, actions (backend/finance)
    from .entities import routes as entity_routes

    entity_routes.install(app, store, auth, mutate)  # Contacts: `tico contacts` (backend/entities/routes.py)
    from . import finance_ads_routes

    finance_ads_routes.install(app, store, auth, mutate)  # Finance: ad spend import settings on ad tools' pages
    from .company_page import api_readers

    api_readers.register(app, store, auth, mutate)  # Company page readers, invites and messages, for owners and admins
    from .company_page import api_editions

    api_editions.register(app, store, auth, mutate)  # the Company page's editions and their approvals
    from . import bills_address

    bills_address.install(app, store, auth)  # the bills address: emailed bills become draft bills
    outcome_stats.install(app, store, auth)
    from .live_meetings import install_live_meetings

    install_live_meetings(app, store, auth, mutate)
    from .calls import install_calls

    install_calls(app, store, auth, mutate)  # Tico Meet calls in threads (backend/calls.py)
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
    from . import template_refresh

    template_refresh.install(app, store, auth, mutate)  # a bot follows its template (docs/updates.md)
    from . import dream

    dream.install(app, store, auth, mutate, task_create, cap_before_preflight)  # a bot's Dream (docs/learnings.md)
    from .learnings import install as install_learnings

    install_learnings(app, store, auth, mutate)
    from .learned import install as install_learned

    install_learned(app, store, auth, mutate)
    from . import assistant_share

    assistant_share.install(
        app, store, auth, mutate
    )  # bots a person lets view their Assistant chat (docs/assistant.md)
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
    from .slack_threads import install as install_slack_threads

    install_slack_threads(app, settings, store, auth)
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
    from . import history_log

    history_log.install(app, store, auth)
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
    from .email_sending import install as install_email_sending

    install_email_sending(app, store, auth, mutate)
    from . import personal_alerts

    personal_alerts.install(app, store, settings, auth, mutate)
    from .shared_bots import install as install_branches

    install_branches(app, store, auth, mutate, settings_admin, execution)
    from .bot_copy import install as install_bot_copy

    install_bot_copy(app, store, auth, mutate, settings_admin, place_now)
    # Usage reads and suggestions first: `skills/usage`, `skills/installed`, `skills/install-targets` and
    # `skills/suggestions` are not skill names.
    from .skill_usage import install as install_skill_usage

    install_skill_usage(app, store, auth)
    from .skill_suggestions import install as install_skill_suggestions

    install_skill_suggestions(app, store, auth, mutate)
    from .skills import install as install_skills

    install_skills(app, store, auth, mutate)
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
    from .letters import install as install_letters

    install_letters(app, settings, store, auth, mutate)
    from .sleep_idle import install as install_sleep_idle

    install_sleep_idle(app, settings, store, auth)
    from .cloud_machines import install as install_cloud_machines

    install_cloud_machines(app, settings, store, auth, execution, mutate)
    company_server.install(app, store, settings, auth)
    from .support_access import install as install_support_access

    install_support_access(app, store, settings, auth)
    from .watchers import install as install_watchers

    install_watchers(app, store, auth, execution, mutate)
    from .maintenance_health import install as install_health

    install_health(app, store, auth, mutate, settings)
    from .general_manager import install as install_general_manager

    install_general_manager(app, store, auth, mutate, onboarding, settings_admin, settings)
    # A computer's screen in the app (backend/screen.py); its websockets go before the static mounts below.
    from .screen import install as install_screen

    screen_sessions = install_screen(app, store, auth, mutate, execution, settings)
    from .performance import install as install_performance

    install_performance(app, store, auth, mutate, execution)
    # A bot's read-only view of the app, for its own screenshots (backend/app_view.py).
    app_view.install(app, store, auth)
    # A bot's browser through another computer's internet (backend/browser_exit.py); the same relay as the screen.
    from .browser_exit import install as install_browser_exit

    install_browser_exit(app, store, auth, mutate, execution, screen_sessions)

    ui_bundle_routes.register(app, auth, settings)
    from . import cors

    cors.install(app, settings)  # last, so it is outermost: a preflight is answered before sign-in
    org_rule_check.seal(app)  # every write registered above can be sent from a hold, whatever installed when
    from .company_page import reader_app

    reader_app.install(app, store, auth, settings)  # outside even that: the reader origin never reaches anything above
    return app
