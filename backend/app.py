"""Authenticated, deterministic API. No runtime launch or local-machine dependency."""

import asyncio
import logging
import os
import time
from dataclasses import replace
import re
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.exceptions import RequestValidationError
from starlette.requests import ClientDisconnect


from . import agents, app_view, mcp_oauth, oidc, runner_online, views
from . import outcome_stats
from . import pipeline_judges
from . import assistant_changes, why_here
from . import tools_delegation
from . import setup_failures
from . import sources as SRC
from . import ask_ingest, owner_link, setup_handoff
from . import org_rule_check
from . import task_rule_gate
from . import bot_home_merge
from . import cloud_access
from . import counsel
from . import thread_api
from . import turn_budgets, task_caps, reach
from . import (
    profiles,
    run_triggers,
    team_rules,
    update_feeds,
    visuals,
    wake_rules,
)
from . import task_privacy as privacy
from . import task_text_keys
from . import turns as turn_work
from . import models as M
from .auth import LOCAL_SIGNIN_PATH, LOGOUT_PATH, Auth
from .config import Settings
from .observability import Observability, route as error_route
from .execution import (
    Execution,
    flag_cut,
    reset_bot_sessions,
)
from .onboarding import AGENT_OPS, Onboarding
from .recruit import Recruiter
from . import names as actor_names
from . import settings_catalog
from .files import raw_media
from . import company_server
from . import hosted_updates, releases, runner_versions
from .census import Census
from .providers import MODEL_BY_ID
from .settings_admin import SettingsAdmin
from .readiness import absolute as absolute_link
from .credential_cards import install_credential_cards
from .github import PATH as GITHUB_WEBHOOK_PATH
from .github_app import CALLBACK_PATH as GITHUB_APP_CALLBACK_PATH
from .triggers import PATH as TRIGGER_HOOK_PATH
from .listening import PUSH_PATH as LISTENING_PUSH_PATH
from .tool_proxy import PREFIX as TOOL_PROXY_PATH
from .mcp_connect import CALLBACK_PATH as MCP_SIGN_IN_PATH
from .store import H, Problem, Store
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
    task_list_routes,
    task_moves_routes,
    task_links_routes,
    bot_seats_routes,
    fleet_runner_routes,
    web_routes,
)
from .api_contract import api_v2
from backend.task_actions_routes import register_core as register_task_routes


# Gateway image calls in flight per process (each holds a thread up to 130 s); more answer 429 with Retry-After.
GATEWAY_IMAGE_SLOTS = 4


def _vault_start(store, cipher):
    """The KMS data key, outside any request; then any secret an earlier start could not move into the vault (KMS was
    unreachable then: backend/migrations/20261117103033_one_encrypted_store.py)."""
    import importlib

    cipher.warm(store)
    importlib.import_module("backend.migrations.20261117103033_one_encrypted_store").settle(store, cipher)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    SRC.set_assistant_name(settings.assistant_name)  # the labels of source lines (backend/sources.py)
    telemetry = Observability(settings)
    from . import flight

    recorder = flight.Recorder() if settings.flight_recorder else None
    store = Store(settings, recorder)
    # The schema comes first, before any install below opens a transaction: a new file takes the baseline and an
    # upgraded one its pending migrations, so install-time code and every commit's hooks find the tables they name, and
    # a database below the floor is refused before anything writes to it. Startup's store.initialize does the rest.
    store.migrate()
    census = Census(store, settings)
    releases.CHECKER.bind(census)
    auth = Auth(store)
    execution = Execution(store, auth)
    settings_admin = SettingsAdmin(store, auth, execution, MODEL_BY_ID, reset_bot_sessions)
    onboarding = Onboarding(store, auth, settings_admin, execution, MODEL_BY_ID)
    recruiter = Recruiter(store, auth, settings)
    execution.runner_enrolled = onboarding.on_runner_enrolled

    lifespan = _lifespan(
        auth, execution, flight, onboarding, recorder, lambda: screen_sessions, settings, store, telemetry
    )

    app = FastAPI(
        title=settings.app_name + " API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.auth, app.state.execution = store, auth, execution
    app.state.onboarding = onboarding  # tests build a picked team as bots
    screen_sessions = _configure_app(
        app,
        settings,
        store,
        auth,
        execution,
        settings_admin,
        onboarding,
        recruiter,
        telemetry,
        census,
        recorder,
        flight,
    )
    return app


def _configure_app(
    app, settings, store, auth, execution, settings_admin, onboarding, recruiter, telemetry, census, recorder, flight
):
    assistant_direct, assistant_instruction, assistant_mod, assistant_valid, agent_ops_act, create_proposal = (
        _install_request_guard(
            lambda: MAX_REPORT_BYTES,
            lambda *args, **kwargs: act_for_requester(*args, **kwargs),
            app,
            auth,
            lambda *args, **kwargs: agent_ops_owns_task(*args, **kwargs),
            census,
            lambda *args, **kwargs: confirm_for_carried(*args, **kwargs),
            recorder,
            settings,
            store,
            telemetry,
        )
    )

    may_edit_routines = _routine_permissions(auth, lambda *args, **kwargs: delegated_identity(*args, **kwargs))

    flight.install_middleware(app, recorder)

    actor_names.install_route_class(app, store)

    flight.register_timing(app)

    mutate = _mutation(auth, store)

    mover, task_view, task_views, visible_tasks = task_list_routes.readers(auth)

    visual_view = visuals.register(app, auth, settings, store)

    named_destination, send = thread_api.message_sender(
        app,
        auth,
        lambda *args, **kwargs: automations.may_reference(*args, **kwargs),
        lambda *args, **kwargs: routed_message(*args, **kwargs),
        visual_view,
    )

    _register_environment(app, settings, store)

    updater_only = _register_updates(
        app,
        census,
        mutate,
        lambda *args, **kwargs: owner_only(*args, **kwargs),
        lambda *args, **kwargs: person_only(*args, **kwargs),
        settings,
        store,
    )

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

    access_routes.register_people(
        admin_only,
        app,
        auth,
        lambda *args, **kwargs: delegated_identity(*args, **kwargs),
        mutate,
        lambda *args, **kwargs: propose_card(*args, **kwargs),
        lambda *args, **kwargs: risky(*args, **kwargs),
        settings,
        store,
    )

    access_routes.register(
        app, admin_only, auth, mutate, onboarding, owner_only, recruiter, settings, settings_admin, store
    )

    people_routes.register_profile(
        app, auth, lambda *args, **kwargs: delegated_identity(*args, **kwargs), mutate, settings, store, visual_view
    )

    (
        chat_place,
        home_place,
        rule_check,
        rule_comment_audience,
        rule_create_text,
        rule_message_audience,
        rule_message_place,
        rule_public,
        rule_summary_allowance,
        rule_target,
        rule_task_audience,
        rule_update_audience,
        rule_update_check,
        rule_update_text,
        rule_warned_fields,
        task_place,
    ) = _rule_checks(app, assistant_mod, auth, lambda *args, **kwargs: routed_message(*args, **kwargs), settings, store)

    chat_upload_checks, chat_upload_write, checked_write, routed_message, say = thread_api.register_messages(
        app,
        auth,
        chat_place,
        home_place,
        may_edit_routines,
        mutate,
        named_destination,
        onboarding,
        owner_only,
        rule_check,
        rule_message_audience,
        rule_message_place,
        lambda *args, **kwargs: saved_retry(*args, **kwargs),
        send,
        settings,
        store,
        task_view,
        visual_view,
    )

    cap_before_preflight, create_task, handoff_preflight, saved_retry, task_create = register_task_routes(
        app,
        assistant_direct,
        assistant_instruction,
        assistant_mod,
        auth,
        lambda *args, **kwargs: carried_request(*args, **kwargs),
        lambda *args, **kwargs: delegated_identity(*args, **kwargs),
        mover,
        mutate,
        lambda *args, **kwargs: person_only(*args, **kwargs),
        rule_check,
        rule_comment_audience,
        rule_create_text,
        rule_public,
        rule_summary_allowance,
        rule_target,
        rule_task_audience,
        rule_update_check,
        rule_update_text,
        rule_warned_fields,
        send,
        settings,
        settings_admin,
        store,
        task_place,
        task_view,
        visible_tasks,
        visual_view,
    )

    def person_only(who, doing):
        if who.role == "bot" or getattr(who, "via", "") or getattr(who, "task_actor", None):
            raise Problem("forbidden", "Only a person, signed in as themselves, " + doing, 403)

    task_links_routes.register(app, auth, mover, mutate, person_only, settings, store, task_view)

    bot_seats_routes.register_lifecycle(
        app,
        auth,
        lambda *args, **kwargs: delegated_identity(*args, **kwargs),
        mutate,
        onboarding,
        settings,
        settings_admin,
        store,
    )

    (
        act_for_requester,
        agent_ops_owns_task,
        carried_request,
        confirm_for_carried,
        delegated_identity,
        propose_card,
        risky,
    ) = agent_ops_act.configure(app, assistant_valid, auth, create_proposal, settings, store)

    # The task-write rule check, also for task writes outside the v3 routes (backend/goal_routes.py).
    task_rules = app.state.task_rules = task_rule_gate.TaskRules(
        auth, rule_check, rule_task_audience, rule_target, store
    )
    # After delegated_identity: a change Agent Ops makes for a person is made as them.
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

    computer_rows, place_now = fleet_runner_routes.register_placement(
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
    )

    rule_audiences = fleet_runner_routes.register_completion(
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
    )

    MAX_REPORT_BYTES, automations, screen_sessions = _install_features(
        app,
        auth,
        cap_before_preflight,
        census,
        chat_upload_checks,
        chat_upload_write,
        checked_write,
        computer_rows,
        create_task,
        delegated_identity,
        execution,
        flight,
        mover,
        mutate,
        onboarding,
        place_now,
        propose_card,
        rule_audiences,
        rule_check,
        say,
        send,
        settings,
        settings_admin,
        store,
        task_create,
        task_view,
        task_views,
    )

    return screen_sessions


def _lifespan(auth, execution, flight, onboarding, recorder, get_screen_sessions, settings, store, telemetry):
    @asynccontextmanager
    async def lifespan(app):
        screen_sessions = get_screen_sessions()
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
        asyncio.get_running_loop().run_in_executor(None, _vault_start, store, app.state.vault.cipher)
        # The company file stays before serving: on a first start the first page shows the company it makes, and the
        # owner's first answer must not race it; on any later start it is a quick check.
        if settings.company_file_configured or settings.company_file_data:
            # A file that can't be applied is kept for the owner to see.
            from . import company_import

            company_import.start(onboarding, store, telemetry)
        else:
            # The file setting was removed: a failure kept from it no longer describes this install.
            from . import company_import

            company_import.forget(store)
        # Finance on every install (backend/finance/setup.py): idempotent, after the migrations, one short transaction.
        try:
            from .finance import actions as finance

            with store.transaction() as c:
                finance.ensure_setup(c, settings.owner_email)
        except Exception as exc:
            telemetry.capture("finance_setup", exc)
        # The Assistant's routines it never had, the Finance Owner once Finance is set up and the rest of what completes a
        # company run with the start jobs, below (Onboarding.complete_company).
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
            start_jobs.run(store, onboarding, telemetry, app.state.vault)
            start_jobs_task = None
        else:
            start_jobs_task = asyncio.create_task(
                asyncio.to_thread(start_jobs.run, store, onboarding, telemetry, app.state.vault)
            )
        stop = asyncio.Event()
        # Always runs: local 90-day retention must not depend on the inbound key or HQ (sync itself stays off).
        inbound_task = asyncio.create_task(app.state.inbound.loop(stop))
        screen_relay_task = None
        if settings.screen_relay_url:
            await asyncio.to_thread(screen_sessions.start)
            screen_relay_task = asyncio.create_task(screen_sessions.run(stop))
        timing_task = asyncio.create_task(flight.watch_loop(app.state.timing, stop))
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
            reads_stop.set()
            library_stop.set()
            app.state.file_metadata.stop.set()
            app.state.file_metadata.wake.set()
            # The storage probe observes copy_stop even while its bounded SDK call is in flight.
            await copy_task
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
                # The webhook deliveries and Finance's workers, all stopped together (backend/background.py).
                from . import background

                await asyncio.to_thread(background.stop_all)
            telemetry.close()
            # Every task above has stopped, so no read is in flight: the store's idle connections close with the server.
            store.close()

    return lifespan


def _install_request_guard(
    get_report_limit,
    act_for_requester,
    app,
    auth,
    agent_ops_owns_task,
    census,
    confirm_for_carried,
    recorder,
    settings,
    store,
    telemetry,
):
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Field names and reasons are useful; invalid input can hold credentials and is never echoed.
        detail = "; ".join(".".join(str(part) for part in error["loc"]) + ": " + error["msg"] for error in exc.errors())
        return JSONResponse({"error": {"code": "validation", "detail": detail, "retryable": False}}, status_code=422)

    app.state.observability = telemetry
    app.state.census = census
    from .fair_share import FairShare

    app.state.fair_share = FairShare.from_settings(settings)

    def raw_who_may(c, request, acting):
        """The catalogue's who-may line for a setting's write its route refused this person (settings_catalog)."""
        return settings_catalog.raw_refusal(
            c,
            auth,
            acting,
            request.method,
            request.url.path,
            agent_ops_act.parse_body(getattr(request, "_body", b"")),
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
        if caller is not None and caller.actor == "bot:" + AGENT_OPS and exc.status == 403:
            # A bot handed Agent Ops a person's request that lends nothing: the person gets a Confirm card, not a refusal.
            card = await asyncio.get_running_loop().run_in_executor(None, confirm_for_carried, request, exc)
            if card:
                return JSONResponse(card)
        if (
            caller is not None
            and caller.actor == "bot:" + AGENT_OPS
            and exc.status in (403, 409)
            and "fix" not in getattr(exc, "extra", {})
        ):
            # Agent Ops relays a refusal to a person: what to click, and where (agent_ops_act.fix).
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
                hint = agent_ops_act.fix(request.url.path, exc.code, exc.status, acting, names, settings.public_url)
                if (
                    hint
                    and hint["fix"].startswith(agent_ops_act.ROLE_FIX)
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
        extra = getattr(exc, "extra", None) or {}
        headers = {"Retry-After": str(extra["retry_after"])} if exc.status == 429 and extra.get("retry_after") else None
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "detail": exc.detail,
                    "retryable": exc.retryable,
                    **extra,
                }
            },
            status_code=exc.status,
            headers=headers,
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

    # Bot text reaches the page as HTML; scripts run only from our own files (the web app's build), so a tag that slips
    # through a renderer cannot run. The page carries no inline script, and any inline script is refused.
    PAGE_POLICY = "script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"

    from .flight import Timing

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
    from . import agent_ops_act

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
        via_reset = delegation_reset = run_reset = cause_reset = token_reset = share = None
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
                    # So does an MCP tool's sign-in return (backend/mcp_connect.py): its one-time state is its only right.
                    if (
                        exc.code != "identity"
                        or request.url.path not in (GITHUB_APP_CALLBACK_PATH, MCP_SIGN_IN_PATH)
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
                # One API client's burst waits its turn, not the company's (backend/fair_share.py): refused before it
                # reads anything else.
                share = app.state.fair_share.admit(who, request.url.path)
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
                limit = (
                    1024 * 1024
                    if request.url.path == "/api/v2/team/icon"
                    else 2 * 1024 * 1024
                    if request.url.path == "/api/v2/company-page/logo"
                    else settings.upload_max_bytes
                    if media_raw
                    else settings.upload_max_bytes + 11_000_000
                    if streamed
                    else 14_500_000
                    if re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path)
                    else get_report_limit()
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
                    # A key in task text is refused before any route reads the body (backend/task_text_keys.py).
                    if task_text_keys.scope(request.method, request.url.path):
                        await asyncio.to_thread(
                            task_text_keys.refusal,
                            store,
                            str(getattr(getattr(request.state, "identity", None), "actor", "") or ""),
                            request.method,
                            request.url.path,
                            request._body,
                            request.headers.get("content-type", ""),
                        )
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
                    header = request.headers.get(agent_ops_act.HEADER)
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
            # The Assistant's citation was checked above; only Agent Ops borrows a requester's identity here.
            on_behalf = None if getattr(caller, "via", "") == "assistant" else request.headers.get(agent_ops_act.HEADER)
            if (
                not on_behalf
                and getattr(request.state, "identity", None) is not None
                and request.state.identity.actor == "bot:" + AGENT_OPS
                and request.url.path.startswith("/api/v2/")
                and request.url.path not in ("/api/v2/me", "/api/v2/mcp")
            ):
                body = agent_ops_act.parse_body(getattr(request, "_body", b""))
                on_behalf = (body.get("on_behalf_of") if isinstance(body, dict) else None) or request.query_params.get(
                    "on_behalf_of"
                )

                # The default applies only to what Agent Ops does for a person (the delegable routes). Its own run's
                # plumbing (credentials, attempts, jobs, its status) and its own messages stay Agent Ops': a run's
                # credential fetch as the person was refused, and every human-requested turn failed to start.
                # A bot's route is read by the bot's id (agent_ops_act.by_id), looked up off the event loop and only
                # for a bot's route.
                route = agent_ops_act.BOT_ROUTE.fullmatch(agent_ops_act.normalize(request.url.path) or "")
                named = {}
                if route and not on_behalf:

                    def bot_id(ref):
                        with store.read() as c:
                            return (H.resolve_bot(c, ref) or {}).get("id")

                    named[route.group(1)] = await asyncio.to_thread(bot_id, route.group(1))
                if not on_behalf and agent_ops_act.default_delegable(
                    request.method, request.url.path, body, request.query_params, resolve=named.get
                ):
                    on_behalf = "default"
            elif (
                not on_behalf
                and getattr(request.state, "identity", None) is not None
                and request.state.identity.role == "bot"
                and not request.state.identity.via
                and tools_delegation.route(request.method, request.url.path)
            ):
                # A Tools change by a bot: the Tools Owner makes it with the rights of the person who asked it
                # (backend/tools_delegation.py); any other bot keeps its own (`act_for_requester`).
                body = agent_ops_act.parse_body(getattr(request, "_body", b""))
                on_behalf = (
                    (body.get("on_behalf_of") if isinstance(body, dict) else None)
                    or request.query_params.get("on_behalf_of")
                    or "default"
                )
            if (
                on_behalf
                and getattr(request.state, "identity", None) is not None
                and request.state.identity.actor == "bot:" + AGENT_OPS
                and agent_ops_owns_task(request.url.path, agent_ops_act.parse_body(getattr(request, "_body", b"")))
            ):
                on_behalf = None  # Agent Ops' own task: its own words, with its own rights
            if on_behalf and getattr(request.state, "identity", None) is not None:
                # Agent Ops doing what the person who asked it could do in the app (backend/agent_ops_act.py).
                early, acted = await asyncio.get_running_loop().run_in_executor(
                    None, act_for_requester, request, on_behalf
                )
                if acted is not None:
                    request.state.identity = replace(acted, task_actor=caller.task_actor or caller.actor)
                    if via_reset is None:
                        via_reset = H.VIA.set(acted.via if acted.via in tools_delegation.DELEGATE_VIAS else "botops")
                    if getattr(request.state, "delegation", None):
                        delegation_reset = H.DELEGATION.set(request.state.delegation)
                    lent = (getattr(request.state, "delegation", None) or {}).get("for")
                    if H.is_human(lent) and cause_reset is not None:
                        # What Agent Ops does with a person's rights is that person's act: its notices wake as the same
                        # act from the app would (backend/wake_rules.py ALWAYS_WAKE "person", wake presets).
                        wake_rules.CAUSE.set(lent)
            response = early or await call_next(request)
            if response.status_code >= 500:
                from .diagnostics import request_failure

                request_failure(request, response.status_code)
            if response.status_code >= 500 and not getattr(request.state, "telemetry_captured", False):
                telemetry.capture("request", status=response.status_code, route=error_route(request))
            # The page and its files (each sent with an ETag) may be kept by the browser and
            # revalidated: an unchanged file comes back as a 304, not in
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
            # A route's no-transform (file_delivery.serve, visuals._headers) survives the rewrite below.
            transform_off = "no-transform" in str(response.headers.get("cache-control", ""))
            if route_keeps or file_bytes:
                pass  # the route's own "private, max-age=…" stands
            elif (
                request.url.path.startswith(web_routes.ASSETS_PREFIX)
                and response.status_code in (200, 304)
                and response.headers.get("cache-control") == web_routes.IMMUTABLE
            ):
                pass  # a file of the web app's build is named by its content: kept for a year
            elif not request.url.path.startswith("/api") and "etag" in response.headers:
                response.headers["Cache-Control"] = (
                    "private, max-age=3600" if request.url.path.startswith("/assets/") else "private, no-cache"
                )
            else:
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            if transform_off and "no-transform" not in response.headers["Cache-Control"]:
                response.headers["Cache-Control"] += ", no-transform"
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
            app.state.fair_share.done(share)
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

    return assistant_direct, assistant_instruction, assistant_mod, assistant_valid, agent_ops_act, create_proposal


def _routine_permissions(auth, delegated_identity):
    def may_edit_routines(c, who, bot):
        """The bot itself, or a person who manages it (its owners, its operator, an Admin, whoever it reports up
        to). A routine is the bot's standing work: the people who direct the bot set it, and the bot may set up
        its own. Agent Ops does it as the person whose chat message started its turn, checked with their rights;
        with no such person (a bot built during setup) only for a bot still being built from its template."""
        auth.domain(who)
        row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        if not H.bot(c, bot):
            raise Problem("not_found", "Unknown bot", 404)
        if who.actor == "bot:" + bot:
            return
        if who.actor == "bot:" + AGENT_OPS:
            try:
                person = delegated_identity(c, who, "turn")
            except Problem:
                person = None
            if person is not None and person.actor != who.actor:
                if auth.bot_manager(c, person, bot) or auth.operator(c, person, bot):
                    return
                raise Problem("forbidden", "The person Agent Ops is acting for may not change this bot's routines", 403)
            declared = H._json(row["config"], {}) if row else {}
            if (
                (H.bot(c, bot) or {}).get("state") == "planned"
                and isinstance(declared, dict)
                and declared.get("template")
            ):
                return
            raise Problem(
                "forbidden", "Agent Ops sets routines only as the person who asked it, or on a bot it is building", 403
            )
        if auth.bot_manager(c, who, bot) or auth.operator(c, who, bot):
            return
        raise Problem("forbidden", "You cannot change this bot's routines", 403)

    return may_edit_routines


def _mutation(auth, store):
    def mutate(request, body, fn, check=None, durable=False, retry_body=None, receipt=None):
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
        if receipt:
            money["receipt"] = receipt
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
            privacy.require_payload(c, (receipt and receipt(result)) or request.state.identity, result)
        return result

    return mutate


def _register_environment(app, settings, store):
    # Liveness reads the database on its own two threads: when slow handlers hold every worker in the shared
    # pool, a probe queued behind them would time out and mark a working server unhealthy.
    health_threads = anyio.CapacityLimiter(2)

    def health_check():
        with store.read() as c:
            c.execute("SELECT 1 FROM schema_migrations").fetchone()

    @app.get("/healthz", **api_v2("Health", "getLiveness", "Is the server up (no sign-in)", security=[]))
    async def health():
        await anyio.to_thread.run_sync(health_check, limiter=health_threads)
        return {
            "ok": True,
            "service": "tico",
            "protocol": 2,
            "release": settings.release_id,
            "environment_id": settings.environment_id,
        }

    @app.get("/api/v2/config", **api_v2("Team", "getConfig", "Team and app names, version, setup state", "Config"))
    def environment(request: Request):
        """Everything a client needs to name this environment and reach its runner."""
        from .onboarding import client_features, config_view

        with store.read_transaction() as c:
            return {**config_view(c, settings, request.state.identity), "features": client_features(request.app)}


def _register_updates(app, census, mutate, owner_only, person_only, settings, store):
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
            cutoff = runner_online.cutoff()
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

    return updater_only


def _rule_checks(app, assistant_mod, auth, routed_message, settings, store):
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

    def rule_comment_audience(c, who, row, text, *extra):
        return rule_task_audience(c, row, who.actor, text, *extra)

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
        """What a new task says, as the rule check reads it: its title and first message, and its public title and
        summary."""
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

    def rule_update_text(c, body, row):
        """What a task change says, as the rule check reads it: its text fields, the task itself (its title and what it
        asks, `H.task_words`) when it is handed to a new owner, and the public text already written when the change
        alone makes the task public."""
        parts = [
            f"{label}: {value}"
            for key, label in (
                ("title", "Title"),
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
            parts.append(f"Assigned: {row['title']}\n\n{H.task_words(c, row['id'])}".rstrip())
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
        with store.read() as c:
            text = rule_update_text(c, body, row)
        if not text:
            return None
        only_owner = getattr(body, "owner", None) and not any(
            getattr(body, k, None) for k in ("title", "note", "public_title", "public_summary")
        )
        return rule_check(
            who,
            "task_owner" if only_owner else "task_update",
            text,
            lambda c: rule_update_audience(c, who, body, row),
            obj=f"{row['id']}@{row['version']}",
            context={"task": row["title"]},
        )

    return (
        chat_place,
        home_place,
        rule_check,
        rule_comment_audience,
        rule_create_text,
        rule_message_audience,
        rule_message_place,
        rule_public,
        rule_summary_allowance,
        rule_target,
        rule_task_audience,
        rule_update_audience,
        rule_update_check,
        rule_update_text,
        rule_warned_fields,
        task_place,
    )


def _install_features(
    app,
    auth,
    cap_before_preflight,
    census,
    chat_upload_checks,
    chat_upload_write,
    checked_write,
    computer_rows,
    create_task,
    delegated_identity,
    execution,
    flight,
    mover,
    mutate,
    onboarding,
    place_now,
    propose_card,
    rule_audiences,
    rule_check,
    say,
    send,
    settings,
    settings_admin,
    store,
    task_create,
    task_view,
    task_views,
):
    from .tags import install as install_tags

    install_tags(app, store, auth, mutate, task_views)
    # One stream of live events for people's pages (backend/events.py).
    from .events import install as install_events, install_runner as install_runner_events

    install_events(app, store, auth, task_views, task_view)
    app.state.task_view = task_view  # for the events' read in tests (backend/tests/test_payload_budgets.py)
    # And one for each computer, in place of its polls (runner-rs/src/events.rs).
    install_runner_events(app, store, execution)

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
        """The retired Nightly memory tidy pilot's Keep queue: always empty. Today's runner never asks, but 0.6.x
        runners ask every 15 seconds; remove this once 0.6.0 leaves the supported window."""
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

    install_growth_numbers(app, store, auth, mutate)  # the Growth page's numbers and the Growth line
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
    from . import screen_actions

    screen_actions.install(app, store)
    from .meeting_task_links import install as install_meeting_task_links

    install_meeting_task_links(app, store, auth, mutate)
    from . import connectors, google_connect

    google_connect.install(app, store, mutate)
    connectors.install(app, store, execution, mutate)
    from .calendar_api import install_calendar

    install_calendar(app, store, mutate)
    from .imports import install_imports

    install_imports(app, store, auth, execution, mutate)
    from .granola_mcp import install_granola

    install_granola(app)
    from .meeting_review import install_meeting_review

    install_meeting_review(app, store, auth, mutate)
    from .credentials import install_credentials

    install_credentials(app, store, delegate=delegated_identity, propose=propose_card)
    from . import api_tools

    api_tools.install(app, store, auth, settings)  # Tools: API tools, their keys and levels, and the tool index
    from . import access_requests

    access_requests.install(app, store, auth, mutate)  # a bot asks for a level on a tool or a repository
    from . import website_tools

    website_tools.install(app, store, auth, settings)  # Tools: website tools and their accounts
    from . import tool_settings, tool_templates

    tool_settings.install(app, store)  # Tools: an API tool's Settings, its non-secret values
    tool_templates.install(app, store)  # Tools: multi-secret templates, paste to classify, ungroup
    from . import tool_proxy

    tool_proxy.install(app, store)
    from . import rule_links

    rule_links.install(app, store, auth)  # Rules › Links: each way out and its checked writes
    from . import mcp_connect

    mcp_connect.install(app, store)  # Tools: remote MCP servers, by token or OAuth sign-in
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
    install_credential_cards(app, store, app.state.vault, auth, AGENT_OPS, delegated_identity, settings_admin._manager)
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
    from . import flow_opinions

    flow_opinions.install(app, store, auth)
    from . import flow_ping

    flow_ping.install(app, store, auth, mutate)  # Flow's "Ping your bots": bots ask the person their questions
    from . import setup_gaps

    setup_gaps.install(app, store, auth)  # what of the setup still waits on a person, and which bot asks for it
    from . import ask_snoozes

    ask_snoozes.install(app, store, auth, mutate)  # a person's snooze of one ask in their own Needs you
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
    from . import risk

    risk.install(app, store, auth, mutate)  # risks, matters and their links (Risk plan, slice 1)
    from . import stage_rules_api

    stage_rules_api.install(app, store, auth, mutate)  # stage rules: the Rules button, the Rules page's groups
    why_here.install(app, store, auth)  # GET /api/v2/origins/{ref}: why an item is on the list
    assistant_changes.install(app, store, auth, mutate, settings_admin)  # the Assistant's changes, and undoing one
    from . import page_history

    page_history.install(app, store, mutate)  # the pages a person opened, kept for Tico (backend/page_history.py)
    reach.install(app, store, auth, mutate)  # Rules › Internal: who reaches whom (docs/reach.md)
    from . import feedback

    feedback.install(app, store, auth, mutate)  # 👍/👎 on bot and person work (backend/feedback.py)
    profiles.install(app, store, auth, mutate, settings_admin)
    from . import screening

    screening.install(app, store, auth, mutate)  # prompt-injection screening at webhooks, mail, ideas and watchers
    from . import finance_routes

    finance_routes.install(app, store, auth, mutate)  # Finance: budgets, P&L, expenses, actions (backend/finance)
    from .entities import routes as entity_routes

    entity_routes.install(app, store, auth, mutate)  # Contacts: `tico contacts` (backend/entities/routes.py)
    from . import products

    products.install(app, store, auth, mutate)  # Company › Products: `tico products` (backend/products.py)
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
    from .call_notes import install_call_notes

    install_call_notes(app, store, auth, mutate)  # the meeting window's notes and ringing (backend/call_notes.py)
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
    # Bots read and post in Slack through the gateway, never with a token of their own (connectors/slack.py).
    from .slack_bots import install as install_slack_bots

    install_slack_bots(app, settings, store, auth, checked_write, say)
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
        """Agent Ops with no `X-Tico-On-Behalf-Of` header is still doing a person's errand in a turn their chat message
        started (an older client, a route's plain tool): check the person, never Agent Ops, who manages no one's bot."""
        if who.actor != "bot:" + AGENT_OPS:
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

    web_routes.register(app, settings)  # /next before anything else could answer it (backend/web_routes.py)
    brand_icons.register(app)
    if settings.demo:
        from . import demo

        demo.install(app, auth, settings)
    web_routes.register_page(app, settings)  # last: every address nothing above answered is the web app's page
    from . import cors

    cors.install(app, settings)  # last, so it is outermost: a preflight is answered before sign-in
    org_rule_check.seal(app)  # every write registered above can be sent from a hold, whatever installed when
    from .company_page import reader_app

    reader_app.install(app, store, auth, settings)  # outside even that: the reader origin never reaches anything above
    return MAX_REPORT_BYTES, automations, screen_sessions
