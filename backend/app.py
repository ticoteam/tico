"""Authenticated, deterministic API. No runtime launch or local-machine dependency."""

import asyncio
import time
import json
from dataclasses import replace
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from clients.agent_skill import WHO_NEEDS_ME

from . import agents, batch, external_sync, inbox_isolation, harness_actions, model_login, oidc, personal_tokens, views
from . import team_rules, usage_limits
from . import task_privacy as privacy
from . import task_relations as TR
from . import task_roles as TRo
from . import placement as placing
from .statuses import is_parked
from . import turns as turn_work
from . import goals as G
from . import models as M
from .auth import LOCAL_COOKIE, LOCAL_COOKIE_DAYS, LOCAL_SIGNIN_PATH, LOGOUT_PATH, Auth, Identity
from .config import Settings
from .observability import Observability, browser_config, staff_display_name
from .execution import Execution, bot_repository
from .onboarding import BOTOPS, Onboarding
from .recruit import Recruiter
from . import rooms
from . import fast_json
from . import names as actor_names
from .openapi_v2 import STABLE as STABLE_ROUTES
from .route_renames import old_paths as old_route_paths
from . import updates
from . import providers as Providers
from . import access as Access
from . import releases, runner_versions, ui_bundle
from .bot_rows import BotRows
from .census import Census
from .harnesses import HARNESS_CATALOG, resolve_harness, runtime_of
from .providers import MODEL_BY_ID, MODEL_CATALOG
from .settings_admin import SettingsAdmin, repository_present
from .readiness import absolute as absolute_link, can_run
from . import fleet_check as fleet_check_module
from .getting_started import _online_runners
from .credential_cards import install_credential_cards, scrub_attempt
from .github import PATH as GITHUB_WEBHOOK_PATH
from .store import H, P, Problem, Store, bot_readiness, encode, message_page, repo_url, task_message_page




# The stable v2 routes whose answers carry display names (backend/names.py); streams are left alone.
STABLE_PATH = re.compile("|".join(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(spelling))
                                  for path, *_ in STABLE_ROUTES if path != "/api/v2/events"
                                  for spelling in (path, *old_route_paths(path))))


def reset_bot_sessions(c, bot):
    """Keep durable history while fencing local provider sessions for one bot."""
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
        asyncio.get_running_loop().run_in_executor(None, auth.warm)   # Cloudflare's keys, before anyone signs in
        # A company from before the Librarian was built in gets it on update, once it can run it.
        try:
            with store.transaction() as c:
                onboarding.ensure_librarian(c)
                onboarding.ensure_goal_manager(c)
        except Exception as exc:
            telemetry.capture("librarian", exc)
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
        # A deploy keeps the API down longer than a lease; do not fail turns for that.
        execution.grace_leases()
        telemetry.start()
        stop = asyncio.Event()
        from .timing import watch_loop
        timing_task = asyncio.create_task(watch_loop(app.state.timing, stop))
        recorder_thread = flight.Flight(recorder, store, settings) if recorder else None
        if recorder_thread:
            recorder_thread.start()
        async def schedule_loop():
            from .scheduler import Scheduler
            scheduler = Scheduler(store, execution)
            def github_wakes():
                from .github import flush_wakes, refresh_deployed_tasks
                with store.transaction() as c:
                    flush_wakes(c)
                refresh_deployed_tasks(app.state.github_app)
            while not stop.is_set():
                try:
                    await asyncio.to_thread(github_wakes)
                except Exception as exc:
                    telemetry.capture("github_wakes", exc)
                try:
                    await asyncio.to_thread(scheduler.tick)
                    # The release check (and the anonymous count that rides on it) runs even when nobody has the
                    # page open; it is a no-op until its six hours are up.
                    await asyncio.to_thread(releases.notice)
                    from .repositories import daily
                    daily(app.state.github_app)
                except Exception as exc:
                    telemetry.capture("scheduler", exc)
                    import logging
                    logging.getLogger("tico.scheduler").error("Scheduler tick failed: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10)
                except TimeoutError:
                    pass
        async def directory_loop():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(app.state.directory.tick)
                except Exception as exc:
                    telemetry.capture("directory", exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except TimeoutError:
                    pass
        # A rehearsal never runs them, whatever else says so (docs/install.md, "Rehearse a migration").
        timers = settings.scheduler_enabled and not settings.rehearsal
        scheduler_task = asyncio.create_task(schedule_loop()) if timers else None
        directory_task = asyncio.create_task(directory_loop()) if timers else None
        granola_task = asyncio.create_task(app.state.granola.loop(stop)) if timers else None
        # A demo runs no scheduler: nothing fires, and nothing waits for a bot that will never run.
        import threading
        copy_stop = threading.Event()
        copy_task = asyncio.create_task(asyncio.to_thread(app.state.blobs.storage_loop, store, copy_stop))
        media_task = asyncio.create_task(asyncio.to_thread(app.state.file_metadata.loop))
        demo_task = None
        if settings.demo:
            from . import demo
            demo_task = asyncio.create_task(demo.keep_alive(store, stop))
        try:
            yield
        finally:
            stop.set()
            copy_stop.set()
            app.state.file_metadata.stop.set()
            app.state.file_metadata.wake.set()
            # The storage probe observes copy_stop even while its bounded SDK call is in flight.
            await copy_task
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
            telemetry.close()

    app = FastAPI(title=settings.app_name + " API", version="2.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.auth, app.state.execution = store, auth, execution
    from .ui_compression import UiCompression
    app.add_middleware(UiCompression)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Field names and reasons are useful; invalid input can hold credentials and is never echoed.
        detail = "; ".join(".".join(str(part) for part in error["loc"]) + ": " + error["msg"]
                           for error in exc.errors())
        return JSONResponse({"error": {"code": "validation", "detail": detail, "retryable": False}}, status_code=422)

    app.state.observability = telemetry
    app.state.census = census

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        if exc.status >= 500:
            telemetry.capture("request", exc, exc.status)
            request.state.telemetry_captured = True
        caller = getattr(request.state, "privacy_source", None)
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
        telemetry.capture("request", exc, 503)
        request.state.telemetry_captured = True
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
    from .assistant import describe as describe_action
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

    @app.middleware("http")
    async def request_guard(request, call_next):
        via_reset = delegation_reset = None
        try:
            # The sign-in exemptions below match on prefixes; a dot segment would carry one of
            # them onto the static files ("/download/../index.html").
            if any(part in (".", "..") for part in request.url.path.split("/")):
                raise Problem("not_found", "Not found", 404)
            if request.url.path == "/healthz":
                return await call_next(request)
            # Minting a loopback session is the one unauthenticated read: it carries its own
            # secret and is refused outright unless local sign-in is configured.
            if request.url.path in (LOCAL_SIGNIN_PATH, LOGOUT_PATH, *oidc.AUTH_PATHS) and request.method == "GET":
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
            # The repository webhook (backend/github.py) carries its own HMAC signature.
            if request.url.path == GITHUB_WEBHOOK_PATH and request.method == "POST":
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
                if who.via == "assistant" and not who.confirmed and not who.task_actor:
                    who = replace(who, task_actor="bot:" + settings.assistant_bot)
                request.state.auth_ms = (time.perf_counter() - began) * 1000
                request.state.identity = who
                if census.person_due(who):
                    await asyncio.get_running_loop().run_in_executor(None, census.note_person, who)
                if who.via:
                    # The Assistant acting for a person (backend/assistant.py): what it writes is
                    # recorded via assistant, and unless the person just confirmed a proposal it may
                    # write only what is low-risk.
                    via_reset = H.VIA.set(who.via)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and not settings.allows_origin(origin):
                    raise Problem("origin", "Cross-site writes are not allowed", 403)
                size = request.headers.get("content-length", "0")
                if not size.isdigit():
                    raise Problem("content_length", "Invalid Content-Length", 400)
                upload = request.url.path in ("/api/notes", "/api/send", "/api/v2/uploads/tasks", "/api/v2/meetings/import") or re.fullmatch(r"/api/(?:meetings/[^/]+/send|v2/uploads/chat/[^/]+)", request.url.path)
                docs_import = (request.url.path == "/api/v2/docs/import"
                               and request.state.identity.role in ("human", "owner", "bot"))
                # A bot's computer publishes files up to 25 MB as raw bytes (backend/files.py).
                published = (request.url.path in ("/api/v2/files/uploads", "/api/v2/files/imports")
                             and request.state.identity.role in ("bot", "runner"))
                streamed = bool(re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path)
                                and request.headers.get("content-type", "").lower().startswith("multipart/form-data"))
                limit = (1024 * 1024 if request.url.path == "/api/v2/team/icon" else settings.upload_max_bytes + 11_000_000 if streamed else 14_500_000 if re.fullmatch(r"/api/v2/tasks/[^/]+/files", request.url.path) else 27_000_000 if published else 20_000_000 if docs_import or upload and request.state.identity.role in ("human", "owner")
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
                if (getattr(request.state, "identity", None) and request.state.identity.via
                        and not request.state.identity.confirmed
                        and not assistant_writes(request.method, request.url.path, settings, getattr(request, "_body", b""),
                                                 request.state.identity.actor,
                                                 lambda tid, shared: assistant_owns(tid, request.state.identity.actor, shared),
                                                 assistant_direct(), assistant_to_bot)):
                    raise Problem("confirm_required", "The " + settings.assistant_name + " may not do this on its "
                                  "own. Propose it with `hub assistant propose` (or POST /api/v2/assistant/actions); "
                                  "the person confirms it in " + settings.app_name, 403)
            early = None
            caller = getattr(request.state, "identity", None)
            if caller:
                request.state.privacy_source = caller
            if caller and request.method not in ("GET", "HEAD", "OPTIONS"):
                with store.read() as c:
                    privacy.guard_write(c, caller, request.url.path, auth)
            on_behalf = request.headers.get(botops_act.HEADER)
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
                if not on_behalf and botops_act.default_delegable(request.method, request.url.path, body):
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
            response = early or await call_next(request)
            if response.status_code >= 500:
                from .diagnostics import request_failure
                request_failure(request, response.status_code)
            if response.status_code >= 500 and not getattr(request.state, "telemetry_captured", False):
                telemetry.capture("request", status=response.status_code)
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
                return RedirectResponse(oidc.LOGIN_PATH + "?" + urlencode({"next": oidc.safe_next(back)}),
                                        status_code=302)
            return await problem_handler(request, exc)
        except Exception as exc:
            from .diagnostics import request_failure
            request_failure(request, 500, exc)
            telemetry.capture("request", exc, 500)
            raise
        finally:
            if via_reset is not None:
                H.VIA.reset(via_reset)
            if delegation_reset is not None:
                H.DELEGATION.reset(delegation_reset)

    from . import routines

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
        route = request.url.path
        response = None
        try:
            response = await call_next(request)
            found = request.scope.get("route")
            route = getattr(found, "path", None) or ("static" if not route.startswith("/api") else "unmatched")
            total = (time.perf_counter() - started) * 1000
            response.headers["Server-Timing"] = f"auth;dur={request.state.auth_ms:.1f}, app;dur={total:.1f}"
            return response
        finally:
            found = request.scope.get("route")
            key = request.method + " " + (getattr(found, "path", None) or ("static" if not request.url.path.startswith("/api") else "unmatched"))
            # The flight recorder's caller kind, status and size (backend/flight.py); a raised error counts as a 500.
            who = getattr(request.state, "identity", None)
            size = response.headers.get("content-length") if response is not None else None
            app.state.timing.end(key, request.state.auth_ms, (time.perf_counter() - started) * 1000,
                                 flight.caller_kind(who), response.status_code if response is not None else 500,
                                 int(size) if size and size.isdigit() else 0, getattr(who, "actor", "") or "")

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

    @app.get('/api/v2/bots/{bot}/routines')
    def bot_routines(bot: str, request: Request, include_deleted: bool = False):
        who = request.state.identity
        if not (who.actor == "bot:" + bot or who.role in ('human', 'owner')):
            raise Problem('forbidden', 'Only people and the bot itself read its routines', 403)
        with store.read() as c:
            auth.require_read(c, who, bot)
            return {'routines': routines.listing(c, bot, include_deleted)}

    @app.post('/api/v2/bots/{bot}/routines')
    def routine_create(request: Request, bot: str, body: M.RoutineCreate):
        who = request.state.identity
        def work(c):
            may_edit_routines(c, who, bot)
            try:
                return {'routine': routines.create(c, who.actor, bot, body.model_dump(), key=body.key)}
            except ValueError as exc:
                raise Problem('routine', str(exc), 422)
        return mutate(request, body, work)

    def routine_owner(c, who, sid):
        found = c.execute('SELECT bot FROM schedules WHERE id=?', (sid,)).fetchone()
        if not found:
            raise Problem('not_found', 'Routine not found', 404)
        may_edit_routines(c, who, found['bot'])
        return found['bot']

    @app.post('/api/v2/routines/{schedule_id}')
    def routine_update(request: Request, schedule_id: str, body: M.RoutineUpdate):
        who = request.state.identity
        def work(c):
            routine_owner(c, who, schedule_id)
            try:
                return {'routine': routines.update(c, who.actor, schedule_id, body.model_dump())}
            except ValueError as exc:
                raise Problem('routine', str(exc), 422)
        return mutate(request, body, work)

    @app.post('/api/v2/routines/{schedule_id}/delete')
    def routine_delete(request: Request, schedule_id: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            routine_owner(c, who, schedule_id)
            return {'routine': routines.remove(c, who.actor, schedule_id)}
        return mutate(request, body, work)

    @app.post('/api/v2/routines/{schedule_id}/run')
    def routine_run_now(request: Request, schedule_id: str, body: M.Empty):
        """Open one manual occurrence of a recurring routine for its bot."""
        who = request.state.identity
        def work(c):
            bot = routine_owner(c, who, schedule_id)
            schedule = routines.row(c, schedule_id)
            if schedule["deleted_at"] or schedule["event_name"]:
                raise Problem("routine", "Only an existing timed routine can run now", 422)
            if schedule["enabled"] != 1 or (H.bot(c, bot) or {}).get("state") != "active":
                raise Problem("routine", "Resume the routine and bot before running it", 409)
            current = routines.latest_task(c, schedule_id)
            if current and current["status"] in ("open", "doing", "waiting", "review", "ready"):
                raise Problem("routine", "This routine already has unfinished work", 409)
            task = routines.open_task(c, auth, schedule, schedule["title"], schedule["playbook"], H.now())
            occurrence = "manual:" + H.new_id()
            c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,?)",
                      (schedule_id, occurrence, task["id"], "created"))
            H.event(c, who.actor, "routine.run_now", schedule_id, {"task": task["id"]})
            return {"task_id": task["id"], "occurrence": occurrence}
        return mutate(request, body, work)

    @app.get('/api/v2/routines')
    def all_routines(request: Request, response: Response, include_deleted: bool = False):
        from . import task_reads as reads
        who = request.state.identity
        if who.role not in ('human', 'owner'):
            raise Problem('forbidden', 'This endpoint is available only to people', 403)
        with store.read() as c:
            readable = auth.bot_accesses(c, who)
            rows = [row for row in routines.listing(c, include_deleted=include_deleted, summary=True)
                    if readable.get(row['bot'], auth.FULL)['read']]
            # The rows are one small query; the answer's names and bytes are what a match saves.
            tag = reads.etag('routines', rows, reads.names(c))
            if reads.fresh(request, tag):
                return reads.not_modified(tag)
            response.headers['ETag'] = tag
            return {'routines': rows}

    @app.get('/api/v2/routines/{schedule_id}/occurrences')
    def routine_occurrences(schedule_id: str, request: Request, limit: int = 50):
        who = request.state.identity
        if who.role not in ('human', 'owner'):
            raise Problem('forbidden', 'This endpoint is available only to people', 403)
        with store.read() as c:
            row = c.execute('SELECT bot FROM schedules WHERE id=?', (schedule_id,)).fetchone()
            if not row:
                raise Problem('not_found', 'Routine not found', 404)
            auth.require_read(c, who, row['bot'], 'Routine not found')
            return {'occurrences': routines.occurrences(c, schedule_id, limit, who=who, auth=auth)}

    def mutate(request, body, fn, check=None):
        def current_access(c):
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
            try:
                return fn(c)
            except H.Refused as exc:
                exc.private = getattr(exc, "private", False) or H.PRIVATE_WRITE.get()
                raise
            finally:
                H.PRIVATE_WRITE.reset(token)
        result = store.mutate(request.state.identity, request.url.path,
                              request.headers.get("idempotency-key"), body.model_dump(), protected_write, check=current_access)

        if isinstance(result, dict) and "_refusal" in result:
            refusal = result["_refusal"]
            raise Problem(refusal["code"], refusal["detail"], refusal["status"])
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
        value.pop("labels_json", None)
        if pipelines is None:
            if c is None:
                with store.read() as pipeline_conn:
                    pipelines = H.type_list(pipeline_conn)
            else:
                pipelines = H.type_list(c)
        typ = next((t for t in pipelines if t["id"] == row.get("type_id")), None)
        value["type"] = {"id": typ["id"], "name": typ["name"]} if typ else None
        value["step"] = next((s for s in typ["steps"] if s["id"] == row.get("step_id")), None) if typ else None
        if c is not None:
            value["relations"] = TR.grouped(c, [row["id"]], visible_sql)[row["id"]]
            value["roles"] = TRo.roles_of(c, row["id"])
            routine = c.execute('SELECT schedule_id FROM schedule_occurrences WHERE task_id=?', (row['id'],)).fetchone()
            if routine:
                value.update(routine_id=routine['schedule_id'])
            asks = H.open_task_asks(c, row, actor=privacy.actor(who))
            value.update({"origin_actor": H.task_origin(c, row), "ask": next(iter(asks), None), "open_asks": len(asks)})
            from .task_review import task_covers
            value["cover"] = task_covers(c, [row["id"]]).get(row["id"])
            from .blobs import brief
            value["attachments"] = [brief(r) for r in c.execute(
                "SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=?", (row["id"],))]
            value["links"] = H.task_links(c, row["id"])
            value["children_summary"] = H.children_summary(c, row["id"], visible_sql)
            value["pr_state"] = H.pr_state(value["links"])
            if parts is None:
                counts = c.execute("SELECT COUNT(*), SUM(status IN ('done','closed')) FROM tasks WHERE id IN ("
                                   + TR.CHILD_IDS + ") AND (" + visible_sql + ")", (row["id"],)).fetchone()
                parts = {"total": counts[0] or 0, "done": counts[1] or 0}
            value["parts"] = parts
        return value

    def task_views(rows, c, who, visible_sql="1"):
        """Hydrate one task-list page with a fixed number of relation queries."""
        if not rows:
            return []
        H.hydrate_task_tags(c, rows)
        ids = [row["id"] for row in rows]
        marks = ",".join("?" * len(ids))
        from .task_review import task_covers
        covers = task_covers(c, ids)

        parts = {row["parent_id"]: {"total": row["total"] or 0, "done": row["done"] or 0}
                 for row in c.execute(
                     f"SELECT r.to_task parent_id,COUNT(*) total,SUM(t.status IN ('done','closed')) done "
                     f"FROM task_relations r JOIN tasks t ON t.id=r.from_task WHERE r.kind='parent' "
                     f"AND r.to_task IN ({marks}) AND t.id IN (SELECT id FROM tasks WHERE {visible_sql}) "
                     f"GROUP BY r.to_task", ids)}
        relations = TR.grouped(c, ids, visible_sql)
        roles = TRo.grouped(c, ids)
        routines_by_task = {row["task_id"]: row["schedule_id"] for row in c.execute(
            f"SELECT task_id,schedule_id FROM schedule_occurrences WHERE task_id IN ({marks})", ids)}

        links = {tid: [] for tid in ids}
        for link in c.execute(f"SELECT * FROM task_links WHERE task_id IN ({marks}) ORDER BY created", ids):
            links[link["task_id"]].append({k: v for k, v in dict(link).items() if k not in ("detail_json", "pr_sha")})

        attachments = {tid: [] for tid in ids}
        from .blobs import brief
        for asset in c.execute(
                f"SELECT a.task_id,b.* FROM task_assets a JOIN blobs b ON b.id=a.blob_id "
                f"WHERE a.task_id IN ({marks})", ids):
            attachments[asset["task_id"]].append(brief(asset))

        from .privacy_index import ReadIndex
        provenance = ReadIndex(c, who)
        c.create_function("task_ask_readable", 4, provenance.message)
        asks, open_counts = {}, {}
        for message in c.execute(
                "SELECT * FROM (SELECT m.*,t.id AS ask_task_id,"
                "COUNT(*) OVER (PARTITION BY t.id) AS open_count,"
                "ROW_NUMBER() OVER (PARTITION BY t.id ORDER BY m.rowid DESC) AS ask_rank "
                "FROM tasks t JOIN conversations cv ON cv.id=t.conversation_id "
                f"JOIN messages m ON m.conversation_id=t.conversation_id AND {H.MESSAGE_TASK_SQL}=t.id "
                f"WHERE t.id IN ({marks}) AND m.kind='ask' AND m.deleted_at IS NULL "
                "AND task_ask_readable(m.id,m.conversation_id,m.refs_json,m.in_reply_to) "
                f"AND {H.OPEN_ASK_SQL}) "
                "WHERE ask_rank=1", ids):
            message = dict(message)
            tid = message.pop("ask_task_id")
            open_counts[tid] = message.pop("open_count")
            message.pop("ask_rank")
            message["refs"] = H._json(message.get("refs_json"), {}) or {}
            message["older"] = H.ask_older(message)
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
        result = []
        for row in rows:
            value = task_view(row, pipelines=pipelines)
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
            if row["id"] in routines_by_task:
                value["routine_id"] = routines_by_task[row["id"]]
            if row["id"] in origins:
                value["origin_actor"] = origins[row["id"]]
            elif row.get("requester") == H.KEEPER and row.get("owner") == legacy_owner:
                # Only legacy keeper reviews need the refusal-window fallback.
                value["origin_actor"] = H.task_origin(c, row)
            else:
                value["origin_actor"] = None
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
            elif kind not in ("depth", "turn_id"):
                raise Problem("reference", f"Unsupported reference kind: {kind}", 422)

    def send(c, who, body):
        in_assistant_room = False
        if str(body.to).lower() == "assistant":
            if who.role not in ("human", "owner") or who.via:
                raise Problem("forbidden", "Only a human writes to their private Assistant", 403)
            if body.conversation_id and not assistant_room(c, who, body.conversation_id):
                raise Problem("forbidden", "Use your own Assistant room", 403)
            assistant_bot = H.bot(c, settings.assistant_bot)
            if not assistant_bot or assistant_bot["state"] != "active":
                raise Problem("assistant_off", "The " + settings.assistant_name + " is off", 409)
            from .assistant import ensure_room
            body.to = settings.assistant_bot
            body.conversation_id = ensure_room(c, who.actor, settings.assistant_bot)["id"]
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
                auth.require_write(c, who, H.actor_id(to), missing="Unknown recipient")
        auth.require_bot_contact(c, who, to, body.conversation_id,
                                 (body.refs or {}).get("task") or (body.refs or {}).get("task_id"),
                                 kind="message")
        if body.conversation_id:
            conv = auth.conversation(c, who, body.conversation_id)
            if to not in conv["participants"]:
                raise Problem("forbidden", "Recipient is not a conversation participant", 403)
        if body.in_reply_to:
            msg = H.message(c, body.in_reply_to)
            if not msg:
                raise Problem("not_found", "Original message not found", 404)
            auth.conversation(c, who, msg["conversation_id"])
            privacy.require_message(c, who, msg)
            if body.conversation_id and msg["conversation_id"] != body.conversation_id:
                raise Problem("reference", "Reply belongs to a different conversation", 422)
        check_refs(c, who, body.refs)
        refs = dict(body.refs or {})
        if body.command:
            refs["command"] = True
        if in_assistant_room:
            refs["assistant"] = True           # the server's mark: this turn acts for the person
        conversation_id = body.conversation_id
        if not conversation_id and who.role in ("owner", "human") and to.startswith("bot:"):
            conversation_id = rooms.chat_room(c, auth, who, H.actor_id(to))["id"]
        privacy.require_destination(c, who, to, conversation_id, refs,
                                    H.message(c, body.in_reply_to) if body.in_reply_to else None)
        message = H.say(c, who.actor, to, body.text, conversation_id=conversation_id,
                        kind=body.kind, refs=refs, in_reply_to=body.in_reply_to, wait_s=body.wait_s)
        from .chat_goals import current as current_chat_goal
        goal = current_chat_goal(c, message["conversation_id"])
        if goal and goal["status"] == "active":
            refs["goal_context"] = {"id": goal["id"], "updated_at": goal["updated_at"]}
            c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(refs), message["id"]))
            message = H.message(c, message["id"])
        if who.role == "bot":
            conv = H.conversation(c, message["conversation_id"])
            if who.attempt_id and all(p.startswith("bot:") for p in conv["participants"]):
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            auth.conversation(c, who, conv["id"])
        if note := usage_limits.warning(c, to):
            message = {**message, "warning": note}
        return message

    @app.get("/healthz")
    def health():
        with store.read() as c:
            c.execute("SELECT 1 FROM cloud_migrations").fetchone()
        return {"ok": True, "service": "tico", "protocol": 2, "release": settings.release_id,
                "environment_id": settings.environment_id}

    @app.get("/api/v2/config")
    def environment(request: Request):
        """Everything a client needs to name this environment and reach its runner."""
        from .onboarding import config_view
        with store.read_transaction() as c:
            return {**config_view(c, settings, request.state.identity), "features": {"task_files_multipart": True}}

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
            row = c.execute("SELECT value_json FROM registry_metadata WHERE key='update-pending'").fetchone()
            if not row:
                return
            pending = json.loads(row[0])
            c.execute("DELETE FROM registry_metadata WHERE key='update-pending'")
            H.event(c, pending["actor"], "system.update.finished", pending["to"],
                    {"outcome": result["state"], "from": pending["from"], "to": pending["to"],
                     "message": result.get("message", "")})

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
            before, after = Providers.save(c, who.actor, body.model_dump(), H.now())
            H.event(c, who.actor, "providers.updated", "",
                    {"before": {k: before[k] for k in ("enabled", "runtime", "model", "revision")},
                     "after": {k: after[k] for k in ("enabled", "runtime", "model", "revision")}})
            return Providers.view(after)
        return mutate(request, body, work)

    # ------------------------------------------------------------------ people and access
    def owner_only(who, what):
        auth.domain(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner " + what, 403)

    def admin_only(who, what):
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin " + what, 403)

    @app.get("/api/v2/access")
    def access_view(request: Request):
        """Who can sign in, the roles, what members may do and the company domain. Owners and admins."""
        who = request.state.identity
        admin_only(who, "sees who can sign in")
        with store.read() as c:
            return {**Access.view(c, settings, views.roster(c), settings.proxy_kind, auth.owner_id(c)),
                    "rules": team_rules.load(c), "you": auth.company_role(who)}

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
        """Turn the team's fast defaults off, or back on (backend/team_rules.py). The owner."""
        who = request.state.identity
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

    @app.get("/api/v2/catalog")
    def catalog():
        """The bot templates this company may pick from, named for this company. Every signed-in
        identity reads it: runners and bots build repositories from the same cards."""
        with store.read() as c:
            return {"cards": onboarding.catalog(c)}

    @app.post("/api/v2/goal-manager/turn-on")
    def turn_on_goal_manager(request: Request, body: M.Empty):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner turns the Goal Manager on", 403)
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
                "thread_mode", "temp", "access", "bot_owners", "onboarding_state", "private_tasks_default")

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
                        "shared": bool(declared.get("shared")),
                        "shared_from": str(declared.get("shared_from") or ""),
                        "temp": bool(declared.get("temp")),
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
        row["draining"] = slug in rows.draining
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
        if external:
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
            view = P.org_view(roster, configs, archived, person_id=person or "", team=team or "")
            live = {row["slug"]: row for row in H.bots(c)}
            bots = []
            by_id = {row["id"]: row for row in view["bots"]}
            departments = P.bot_departments(configs, roster, archived)
            for row in view["bots"]:
                level = access.get(row["id"], auth.FULL)
                if not level["see"] or (can and not level[can]):
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
                while parent.startswith("b:") and parent[2:] in by_id and parent not in seen \
                        and not access.get(parent[2:], auth.FULL)["see"]:
                    seen.add(parent)
                    parent = by_id[parent[2:]]["org_parent"]
                shown["org_parent"] = parent
                if row["reports_to"] and not row["reports_to"].startswith("human:") \
                        and not access.get(row["reports_to"], auth.FULL)["see"]:
                    shown["reports_to"] = parent[2:] if parent.startswith("b:") else (
                        "human:" + parent[2:] if parent.startswith("p:") else "")
                bots.append(shown)
            return {**view, "bots": bots}

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
                if body.slack_muted_bots is not None:
                    row = {**row, "slack_muted_bots": list(dict.fromkeys(body.slack_muted_bots))}
                if body.reports_to is not None:
                    row = {**row, "reports_to": body.reports_to.strip()}
                if body.left:
                    row = Access.leave(c, people, row)
                people[i] = row
                roster = {**roster, "people": people}
                c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(roster),))
                H.event(c, who.actor, "person.updated", pid, {"title": body.title is not None, "about": body.about is not None,
                                                              "goals": body.goals is not None, "notes": body.notes is not None,
                                                              "notify_slack_task_done": body.notify_slack_task_done,
                                                              "github": body.github is not None,
                                                              "notify_slack_bot_messages": body.notify_slack_bot_messages,
                                                              "slack_muted_bots": body.slack_muted_bots,
                                                              "reports_to": body.reports_to, "left": bool(body.left)})
                if caller is not who:
                    H.event(c, caller.actor, "person.update_delegated", pid,
                            {"on_behalf_of": who.actor, "message_id": body.on_behalf_of})
                return P.profile(row)
            raise Problem("not_found", "Person not found", 404)
        return mutate(request, body, work)

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
                messages = privacy.page(c, who, conv["id"])["messages"]
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
            message = H.say(c, who.actor, "bot:" + bot, text, kind="notice")
            H.event(c, who.actor, "bot.goals", bot, {"chars": len(body.goals)})
            return {"bot": bot, "goals": body.goals, "message_id": message["id"]}
        return mutate(request, body, work)

    @app.get("/api/v2/models")
    def models(request: Request):
        auth.domain(request.state.identity)
        with store.read() as c:
            choice = Providers.load(c, settings)
        return {"models": list(MODEL_CATALOG), "harnesses": list(HARNESS_CATALOG),
                "enabled_providers": choice["enabled"],
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
    def messages(request: Request, cid: str, since: str | None = None, before: str | None = None):
        with store.read() as c:
            auth.conversation(c, request.state.identity, cid)
            page = privacy.page(c, request.state.identity, cid, since=views.since_time(since), before=before)
            turn_work.annotate(c, auth, request.state.identity, page["messages"])
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

    @app.post("/api/v2/messages")
    def say(request: Request, body: M.MessageCreate):
        return mutate(request, body, lambda c: send(c, request.state.identity, body))

    @app.get("/api/v2/messages/{mid}")
    def message(request: Request, mid: str):
        with store.read() as c:
            row = H.message(c, mid)
            if not row or row.get("deleted_at"):
                raise Problem("not_found", "Message not found", 404)
            who = request.state.identity
            privacy.require_message(c, who, row)
            if who.role == "bot" and (row.get("to_actor") == who.actor or row.get("from_actor") == who.actor):
                return row
            auth.conversation(c, who, row["conversation_id"])
            return row

    @app.post("/api/v2/chat/{bot}")
    def chat(request: Request, bot: str, body: M.ChatCreate):
        def work(c):
            who = request.state.identity
            message = send(c, who, M.MessageCreate(to="bot:" + bot, text=body.text, refs=body.refs, command=body.command))
            onboarding.start_setup(c, who, bot)
            return {"conversation": H.conversation(c, message["conversation_id"]), "message": message}
        return mutate(request, body, work)

    # ------------------------------------------------------------------ updates (backend/updates.py)
    def update_visible(c, who, mine=False):
        """The bots whose updates `who` may read; with `mine`, only the bots they operate (for the
        owner, also bots with no operator), the Updates page's My bots filter (the count must match
        what the feed shows). Updates are a bot's activity, so this is Read."""
        readable = {slug for slug, level in auth.bot_accesses(c, who).items() if level["read"]}
        if not mine:
            return readable
        pid = H.actor_id(who.actor)
        operators = {r[0]: r[1] for r in c.execute("SELECT bot, operator FROM bot_config")}
        return {slug for slug in readable if operators.get(slug) == pid
                or (not operators.get(slug) and who.role == "owner")}

    def readable_update_ids(c, who, ids):
        """Validate the whole personal batch before a read/archive write can mutate any row."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return ids
        readable = update_visible(c, who)
        rows = c.execute("SELECT id, bot FROM updates WHERE id IN (" + ",".join("?" * len(ids)) + ")",
                          ids).fetchall()
        if len(rows) != len(ids) or any(row["bot"] not in readable for row in rows):
            raise Problem("not_found", "Update not found", 404)
        return ids

    @app.get("/api/v2/updates")
    def updates_list(request: Request, kind: str | None = None, bot: str | None = None, unread: bool = False,
                     before: str | None = None, limit: int = 40, mine: bool = False,
                     archive: bool = False, include_archive: bool = False):
        who = request.state.identity
        auth.domain(who)
        if kind and kind not in updates.KINDS:
            raise Problem("kind", "kind is daily or weekly", 422)
        with store.read() as c:
            return updates.listing(c, who.actor, update_visible(c, who, mine), kind=kind, bot=bot, unread=unread,
                                   before=before, limit=max(1, min(limit, 100)), archive=archive,
                                   include_archive=include_archive)

    @app.get("/api/v2/updates/unread")
    def updates_unread(request: Request, mine: bool = False):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            from .meetings import pending_count
            return {"unread": updates.count_unread(c, who.actor, update_visible(c, who, mine)),
                    "meetings_pending": pending_count(c, who)}

    @app.get("/api/v2/updates/{uid}")
    def update_show(request: Request, uid: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = updates.one(c, uid)
            if not row:
                raise Problem("not_found", "Update not found", 404)
            auth.require_read(c, who, row["bot"], "Update not found")
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
            return {"update": {**row, "read": bool(read), "archived": updates.archived(c, who.actor, uid)},
                    "thread": thread}

    @app.post("/api/v2/updates")
    def update_post(request: Request, body: M.UpdatePost):
        who = request.state.identity
        def work(c):
            if who.role != "bot":
                raise Problem("identity", "Only a bot posts an update", 403)
            slides = body.slides.model_dump(exclude_none=True) if body.slides else None
            updates.check_day(c, H.actor_id(who.actor), body.day)
            return {"update": updates.post(c, H.actor_id(who.actor), body.body, kind=body.kind, day=body.day,
                                           slides=slides)}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/redo")
    def update_redo(request: Request, body: M.UpdateRedo):
        """Ask the bots again for a past day's update in the current shape (a week in review as slides)."""
        who = request.state.identity
        owner_only(who, "asks the bots to redo an update")
        def work(c):
            return {"kind": body.kind, "day": body.day, "bots": updates.redo(c, body.kind, body.day, body.bots)}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/read")
    def updates_read(request: Request, body: M.UpdateRead):
        who = request.state.identity
        def work(c):
            views.human_only(who)
            if body.all:
                visible = update_visible(c, who)
                ids = updates.inbox_ids(c, who.actor, visible, created_after=H.shift(H.now(), days=-30))
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
                    "unread": updates.count_unread(c, who.actor, update_visible(c, who))}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/{uid}/reply")
    def update_reply(request: Request, uid: str, body: M.UpdateReply):
        who = request.state.identity
        def work(c):
            views.human_only(who)
            row = updates.one(c, uid)
            if not row:
                raise Problem("not_found", "Update not found", 404)
            auth.require_read(c, who, row["bot"], "Update not found")
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
        calls, median and p90 server time, typical answer size, errors. For people and BotOps, which
        tunes them."""
        who = request.state.identity
        if who.role not in ("human", "owner") and who.actor != H.bot_actor(H.FLEET_MAINTAINER):
            raise Problem("forbidden", "Tool-call stats are for people and BotOps", 403)
        since = H.shift(H.now(), days=-max(1, min(days, 90)))
        tools = {}
        with store.read() as c:
            for row in c.execute("SELECT detail_json FROM events WHERE action='mcp.call' AND ts>?", (since,)):
                d = json.loads(row["detail_json"] or "{}")
                if via and d.get("via") != via:
                    continue
                t = tools.setdefault(d.get("tool") or "?", {"ms": [], "bytes": [], "errors": 0})
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

    @app.post("/api/v2/conversations/{cid}/messages")
    def reply(request: Request, cid: str, body: M.ChatCreate):
        def work(c):
            who = request.state.identity
            conv = auth.conversation(c, who, cid)
            target = next((p for p in conv["participants"] if p != who.actor and p.startswith("bot:")), None)
            target = target or next((p for p in conv["participants"] if p != who.actor), None)
            if not target:
                raise Problem("recipient", "This conversation has no other recipient", 422)
            return {"message": send(c, who, M.MessageCreate(to=target, text=body.text, conversation_id=cid, refs=body.refs, command=body.command))}
        return mutate(request, body, work)

    @app.post("/api/v2/messages/{mid}/answer")
    def answer(request: Request, mid: str, body: M.Answer):
        who = request.state.identity
        def work(c):
            msg = H.message(c, mid)
            if not msg:
                raise Problem("not_found", "Message not found", 404)
            auth.conversation(c, who, msg["conversation_id"])
            return H.answer(c, who.actor, mid, body.text, unknown=body.unknown)
        return mutate(request, body, work)

    @app.get("/api/v2/tasks")
    def tasks(request: Request, response: Response, owner: str | None = None, requester: str | None = None,
              status: str | None = None, lane: str | None = None, label: str | None = None, limit: int = 500,
              offset: int = 0, sort: str = "queue", type: str | None = None, step: str | None = None,
              number: int | None = None, updated_since: str | None = None, brief: bool = False,
              member: str | None = None, role: str | None = None, changed_after: str | None = None,
              ids: str | None = None):
        from . import task_reads as reads
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            owner = H.resolve_actor(c, owner) if owner else None
            if member:
                member = H.resolve_actor(c, member)
                if not member:
                    raise Problem("member", "member is a person or a bot on the roster", 422)
            if role and not TRo.role_name(role):
                raise Problem("role", "role is a short name in lower-case letters, digits, - or _", 422)
            if role and not member:
                raise Problem("role", "role filters member: pass both", 422)
            requester = H.resolve_actor(c, requester) if requester else None
            if lane and lane not in H.TASK_LANES:
                raise Problem("kind", "lane is company or product", 422)
            if limit < 1 or limit > 500:
                raise Problem("limit", "limit is between 1 and 500", 422)
            if offset < 0:
                raise Problem("offset", "offset is zero or greater", 422)
            if sort not in ("queue", "finished", "step"):
                raise Problem("sort", "sort is queue, finished or step", 422)
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
            if number is not None and not 1 <= number <= 999_999_999:
                raise Problem("number", "number is a task number, from 1", 422)
            since = H.parse_ts(updated_since) if updated_since else None
            if updated_since and (not since or since.tzinfo is None):
                raise Problem("date", "updated_since must be an ISO-8601 date/time with a timezone", 422)
            # One read: the visibility, the rows, the cursor and the ETag agree (backend/task_reads.py).
            visible_sql = auth.task_sql(c, who)
            # Several tasks by id in one read (an agent's "show these"), instead of one GET each.
            typed = None
            if ids is not None:
                typed = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
                if not typed or len(typed) > 100:
                    raise Problem("ids", "ids is 1 to 100 task ids or numbers, comma-separated", 422)
                if changed_after is not None:
                    raise Problem("ids", "ids and changed_after are separate reads", 422)
                found = {}
                for ident in typed:
                    try:
                        found[ident] = auth.resolve_task(c, who, ident, visible=visible_sql)
                    except Problem:
                        found[ident] = None
            filters = dict(status=status.split(",") if status and status != "all" else None, lane=lane, label=label,
                           order=sort, type_id=typ["id"] if typ else None, step_ids=step_ids, number=number,
                           updated_since=views.since_time(updated_since) if since else None, visible_sql=visible_sql,
                           member=member, role=TRo.role_name(role) if role else None)
            if typed is not None:
                filters["ids"] = [tid for tid in found.values() if tid]
            def brief_rows(rows):
                # A board or a bot's task list polling many tasks needs neither their text nor their criteria.
                if brief:
                    for row in rows:
                        for field in ("body", "acceptance_criteria", "acceptance_json"):
                            row.pop(field, None)
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

    @app.post("/api/v2/notes")
    def create_note(request: Request, body: M.NoteCreate):
        def work(c):
            who = request.state.identity
            to = auth.target(c, who, body.to, need="write")
            auth.require_bot_contact(c, who, to, kind="note")
            return {"note": note_view(H.note_create(c, who.actor, to, body.text), c)}
        return mutate(request, body, work)

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
            try:
                comment_rights(c, auth, who, tid)
                can_comment = True
            except Problem:
                can_comment = False
            return {"task": task_view(row, c, visible_sql=auth.task_sql(c, who), who=who),
                    "events": [e for e in H.task_history(c, tid) if privacy.content_readable(c, privacy.actor(who), e)],
                    "can_comment": can_comment,
                    "children": children,
                    "comments": H.task_comments(c, tid, actor=privacy.actor(request.state.identity)),
                    "mover": mover(c, request.state.identity),
                    **privacy.page(c, who, row["conversation_id"], task_id=tid)}

    def task_create(c, who, body, lint=True):
        source = who
        actual_bot = source.task_actor or (source.actor if source.role == "bot" else "")
        if privacy.private_execution(c, who):
            if body.private is False:
                raise Problem("privacy", "Private task work cannot create company-visible tasks", 403)
            body = body.model_copy(update={"private": True})
        request_id = body.request_id
        if request_id:
            who = delegated_identity(c, who, request_id, new_task=True)
        parents = [r.task for r in body.relations if r.kind == "parent"]
        if len(parents) > 1:
            raise Problem("relations", "A task has one parent", 422)
        parent_id = auth.resolve_task(c, who, parents[0]) if parents else None
        others = [(auth.resolve_task(c, who, r.task), r.kind) for r in body.relations if r.kind != "parent"]
        from .shared_bots import route
        original = auth.target(c, who, body.owner, need="write")
        owner = auth.target(c, who, route(c, who.actor, original), need="write")
        if auth.assignment_task_for_bot(c, owner):
            raise Problem("assignment_scope", "A temporary assignment cannot receive another task; continue on its linked delivery task", 403)
        if privacy.private_execution(c, source):
            privacy.require_destination(c, source, owner, None, {})
            privacy.require_destination(c, source, who.actor, None, {})
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
        for other, _ in others:
            auth.task(c, who, other)
        parent = H.task(c, parent_id) if parent_id else None
        # Delegation retains the bot's sensitive default and cannot use a person's
        # explicit-public choice to override either creator or assignee defaults.
        if actual_bot and body.private is False:
            body = body.model_copy(update={"private": None})
        if actual_bot and H.private_tasks_default(c, actual_bot):
            body = body.model_copy(update={"private": True})
        private = bool((body.private if body.private is not None else
                        H.private_tasks_default(c, who.actor) or H.private_tasks_default(c, owner))
                       or parent and H.task_private(c, parent))
        if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        requester_actor = None
        if (who.role == "bot" and owner == who.actor and not parent_id and who.attempt_id
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
        row = H.task_create(c, who.actor, body.title, body.body, owner, body.due, parent_id,
                            requester_actor=requester_actor,
                            private=private, conversation_id=rooms.task_conversation_id(c, auth, owner,
                                parent["requester"] if parent and H.is_human(who.actor)
                                and H.is_human(parent["requester"]) else who.actor),
                            lane=body.lane, labels=body.labels, top=body.top, lint=lint,
                            goal_id=body.goal_id, next_run=body.next_run, type=body.type, step=body.step,
                            number=body.number, mover=mover(c, who) or None)
        c.execute("UPDATE tasks SET acceptance_json=? WHERE id=?", (encode(body.acceptance_criteria), row["id"]))
        inherited_request = parent.get("request_id") if parent and H.is_human(who.actor) and who.actor == parent["requester"] else None
        if H.is_human(who.actor) and (request_id or inherited_request):
            c.execute("UPDATE tasks SET request_id=? WHERE id=?", (inherited_request or request_id, row["id"]))
        for url in body.links:
            H.task_link(c, who.actor, row["id"], url, mover=True)
        for other, kind in others:
            TR.relate(c, who.actor, row["id"], other, kind, mover=mover(c, who) or None)
        if body.roles:
            try:
                TRo.set_roles(c, who.actor, row["id"], body.roles, mover=mover(c, who) or None)
            except ValueError as exc:
                raise Problem("roles", str(exc), 422)
        note = usage_limits.warning(c, owner)
        return {"task": task_view(H.task(c, row["id"]), c, visible_sql=auth.task_sql(c, who), who=who),
                **({"warning": note} if note else {})}

    @app.post("/api/v2/tasks")
    def create_task(request: Request, body: M.TaskCreate):
        return mutate(request, body, lambda c: task_create(c, request.state.identity, body))

    @app.post("/api/v2/tasks/dry-run")
    def task_dry_run(request: Request, body: M.TaskCreate):
        """The checks a create would fail, nothing written and no refusal counted."""
        from .mcp import task_dry_run as check
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return check(c, auth, who, body)

    @app.post("/api/v2/tasks/{tid}")
    def update_task(request: Request, tid: str, body: M.TaskUpdate):
        caller = request.state.identity
        def work(c):
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
            if body.close:
                if any(v is not None for v in (body.title, body.status, body.owner, body.due, body.body, body.lane,
                                               body.labels, body.waiting_on, body.rank, body.roles,
                                               body.goal_id, body.type, body.step, body.step_rank, body.number, body.private)):
                    raise Problem("close", "Close and edit are separate operations", 422)
                note = body.note or ""
                if (note.strip() and getattr(who, "via", "") == "botops" and who.actor != "bot:" + BOTOPS
                        and row["owner"] == "bot:" + BOTOPS):
                    # The close is the person's (only the requester or a human closes), but the words are
                    # BotOps' own report on its own task: they read as BotOps, like its other progress notes.
                    H.task_comment(c, "bot:" + BOTOPS, task_id, note, wake=False)
                    note = ""
                H.task_close(c, who.actor, task_id, note=note, quiet=body.quiet)
            else:
                fields = body.model_dump(exclude={"version", "close", "on_behalf_of"})
                H.task_update(c, who.actor, task_id, **fields, mover=mover(c, who) or None)
            c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))
            return {"task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who)}
        return mutate(request, body, work)

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
                if who.role not in ("human", "owner") and who.actor != row["requester"]:
                    raise Problem("forbidden", "Only a person, the task's requester or BotOps starts it early", 403)
                # Running it wakes the bot: that is a request to it.
                if str(row["owner"]).startswith("bot:") and who.actor != row["owner"]:
                    auth.require_write(c, who, H.actor_id(row["owner"]))
            after, queued = H.task_run_now(c, who.actor, task_id)
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who), "queued": queued}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/ask")
    def task_ask(request: Request, tid: str, body: M.Answer):
        def work(c):
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return H.task_ask(c, request.state.identity.actor, task_id, body.text)
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/comments")
    def task_comment(request: Request, tid: str, body: M.TaskComment):
        """A comment on a task. It wakes the bot on the task when a mover, the owner or the
        requester left it; anyone else's is saved for the bot's next turn on the task."""
        who = request.state.identity
        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            from .task_review import comment_rights, check_ask, file_version
            comment_rights(c, auth, who, task_id)
            ask = check_ask(c, task_id, body.ask, auth, who)
            attached = []
            for reference in body.attachments:
                fid, number = reference.rsplit("@", 1)
                version = file_version(c, task_id, fid, int(number))
                attached.append({"id": fid, "file_id": fid, "version": int(number), "ref": reference,
                                 "name": version["name"], "size": version["size"], "content_type": version["mime"],
                                 "url": f"/api/v2/files/{fid}?v={number}"})
            wake = who.role == "bot" or who.actor in (row["owner"], row["requester"]) or mover(c, who)
            msg = H.task_comment(c, who.actor, task_id, body.text, wake=wake, ask=ask,
                                 extra_refs={"attachments": attached, "files": body.attachments} if attached else None)
            for item in attached:
                c.execute("INSERT OR IGNORE INTO task_file_reviews(file_id,version) VALUES(?,?)",
                          (item["id"], item["version"]))
                c.execute("UPDATE task_file_reviews SET comment_id=coalesce(comment_id,?) WHERE file_id=? AND version=?",
                          (msg["id"], item["id"], item["version"]))
            msg = next(m for m in H.task_comments(c, task_id, actor=privacy.actor(request.state.identity)) if m["id"] == msg["id"])
            return {"comment": msg, "comments": H.task_comments(c, task_id, actor=privacy.actor(request.state.identity)), "woke": bool(wake)}
        return mutate(request, body, work)

    @app.get("/api/v2/tasks/{tid}/comments")
    def task_comments(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {"comments": H.task_comments(c, task_id, actor=privacy.actor(request.state.identity))}

    @app.get("/api/v2/tasks/{tid}/answers")
    def task_answers(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {"answers": [m["answer"] for m in H.task_comments(c, task_id, actor=privacy.actor(request.state.identity)) if m.get("answer")]}

    @app.post("/api/v2/tasks/{tid}/answers")
    def task_answer(request: Request, tid: str, body: M.TaskAnswer):
        who = request.state.identity
        def work(c):
            from .task_review import answer_task
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            wake = who.role == "bot" or who.actor in (row["owner"], row["requester"]) or mover(c, who)
            return answer_task(c, auth, who, task_id, body, wake=wake)
        return mutate(request, body, work)

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
        def work(c):
            row = own_comment(c, who, tid, mid)
            from .task_review import comment_rights
            comment_rights(c, auth, who, row["id"])
            msg = H.task_comment_edit(c, who.actor, row["id"], mid, body.text)
            return {"comment": msg, "comments": H.task_comments(c, row["id"], actor=privacy.actor(request.state.identity)), "woke": False}
        return mutate(request, body, work, check=lambda c: own_comment(c, who, tid, mid, allow_deleted=True))

    @app.post("/api/v2/tasks/{tid}/comments/{mid}/delete")
    def task_comment_delete(request: Request, tid: str, mid: str, body: M.Empty):
        """The author takes a comment back: never listed again, never handed to a bot."""
        who = request.state.identity
        def work(c):
            row = own_comment(c, who, tid, mid)
            msg = H.task_comment_delete(c, who.actor, row["id"], mid)
            return {"comment": msg, "comments": H.task_comments(c, row["id"], actor=privacy.actor(request.state.identity)), "woke": False}
        return mutate(request, body, work, check=lambda c: own_comment(c, who, tid, mid, allow_deleted=True))

    @app.post("/api/v2/tasks/{tid}/delete")
    def task_delete(request: Request, tid: str, body: M.Empty):
        """A person deletes a task made by mistake: its human requester, or anyone who may move any
        task. Bots and delegated sessions close instead. A task carrying work (a bot turn, a file,
        an approval, a subtask) is refused, so deleting never takes away what someone did. The task
        goes to the trash, from which it can be restored."""
        who = request.state.identity
        person_only(who, "deletes a task; close it instead")
        def work(c):
            from .task_delete import delete_tasks
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            if row["requester"] != who.actor and not mover(c, who):
                raise Problem("forbidden", "Only the task's requester, or someone who may move any task, deletes it", 403)
            report = delete_tasks(c, [task_id], apply=True, actor=who.actor)
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
                H.task_link(c, who.actor, task_id, body.url, body.title, mover=mover(c, who))
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
            TR.relate(c, who.actor, task_id, other_id, body.kind, remove=body.remove, mover=mover(c, who) or None)
            return {"task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who)}
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
        if key == "meetings.auto_share":
            views.human_only(who)
            if not isinstance(body.value, bool):
                raise Problem("validation", "meetings.auto_share is a boolean", 422)
        if not re.fullmatch(r"[a-z0-9_.-]{1,64}", key):
            raise Problem("kind", "A preference key is lower-case letters, digits, dots, dashes", 422)
        def work(c):
            c.execute("INSERT INTO preferences(actor,key,value_json,updated) VALUES(?,?,?,?) "
                      "ON CONFLICT(actor,key) DO UPDATE SET value_json=excluded.value_json, updated=excluded.updated",
                      (who.actor, key, encode(body.value), H.now()))
            return {"key": key, "value": body.value}
        return mutate(request, body, work)

    @app.post("/api/v2/approvals")
    def approval_request(request: Request, body: M.ApprovalCreate):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if body.task_id:
                auth.task(c, who, body.task_id)
            return H.approval_request(c, who.actor, body.kind, body.payload, body.task_id)
        return mutate(request, body, work)

    @app.get("/api/v2/approvals/{aid}")
    def approval(request: Request, aid: str):
        with store.read() as c:
            row = auth.approval(c, request.state.identity, aid)
            privacy.require_payload(c, request.state.identity, row)
            privacy.require_message(c, request.state.identity, H.message(c, row["message_id"]))
            return row

    @app.post("/api/v2/approvals/{aid}")
    def decide(request: Request, aid: str, body: M.ApprovalDecision):
        who = request.state.identity
        def work(c):
            row = auth.approval(c, who, aid, decide=True)
            privacy.require_payload(c, who, row)
            return H.approval_decide(c, who.actor, aid, body.decision, body.note)
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
            statuses = [s for s in H.status_all(c) if readable.get(s["bot"], auth.FULL)["read"]]
            inputs = privacy.status_inputs(c, [s["bot"] for s in statuses])
            return {"bots": [status_line(c, who, s["bot"], default, inputs[s["bot"]], auth=auth) for s in statuses],
                    # Tico updating itself, shown beside the status line
                    "updating": updating(c)}

    @app.post("/api/v2/bots")
    def create_bot(request: Request, body: M.BotDefinitionCreate):
        who = request.state.identity
        def work(c):
            created = settings_admin.create_bot(c, who, body)
            if body.template:
                # Adding from the catalog after onboarding is the same handoff to BotOps.
                created.update(onboarding.attach_template(c, who, body.slug, body.template,
                                                          body.instructions))
            return created
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/onboarded")
    def bot_onboarded(request: Request, bot: str, body: M.Empty):
        """A starter bot's own call (`hub bot setup-done`), or its manager's, once its setup is done: the bot
        stops being `needs_setup`. Repeating it changes nothing."""
        who = request.state.identity
        return mutate(request, body, lambda c: onboarding.onboarded(c, who, bot))

    @app.post("/api/v2/bots/{bot}/archive")
    def archive_bot(request: Request, bot: str, body: M.BotArchive):
        who = request.state.identity
        def work(c):
            acting = delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who
            result = settings_admin.archive(c, acting, bot, body)
            rooms.archive_shared_room(c, acting.actor, bot)
            if acting is not who:
                H.event(c, who.actor, "bot.archive_delegated", bot,
                        {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of, "successor": body.successor})
            return result
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/restore")
    def restore_bot(request: Request, bot: str, body: M.Empty):
        """Bring an archived bot back: the owner, an admin or the bot's owner, to the status it had, else planned."""
        who = request.state.identity
        def work(c):
            result = settings_admin.restore(c, who, bot)
            if rooms.thread_mode(c, bot) == rooms.SHARED:
                rooms.sync_shared_room(c, auth, bot, actor=who.actor, create=True)
            return result
        return mutate(request, body, work)

    # A person's request lends their rights for as long as the task it created (or that cites it) is open. A chat
    # message with no open task lends them for a week; a new task filed from a message also needs it within the week,
    # or one open task could renew another for ever.
    DELEGATION_DAYS = 7
    # Keeper wakes a follow-through run may start from: the daily update, and a task's due, stall or routine notice.
    FOLLOW_THROUGH_WAKES = ("update", "due", "stalled", "occurrence")

    def open_task(task):
        return bool(task) and task["status"] not in ("done", "closed", "declined")

    def week_old(created):
        return created < H.shift(H.now(), days=-DELEGATION_DAYS)

    def delegated_identity(c, who, ref, new_task=False):
        """Resolve the run's requester, never a human named in its text. Human messages and tasks
        carry that human's rights; bot messages and tasks carry only that bot's rights. Fleet work
        with no requester keeps BotOps' own. Explicit message references must belong to this requester.
        `new_task`: the reference is the request a task is being filed from.
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
            if H.is_human(principal.actor):
                H.DELEGATION.set({"for": principal.actor, "cited": cited, "run": run})
            return replace(principal, via="botops", confirmed=True, task_actor=who.actor,
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
            if task and initial:
                refs = initial.get("refs") or {}
                relayed = any(refs.get(key) for key in ("via", "assistant", "slack", "routing"))
                if (refs.get("comment") and not relayed and H.is_human(requester) and initial["from_actor"] == requester
                        and task["owner"] == who.actor and open_task(task)):
                    # The task's requester commenting on it, in their own words: that is them asking.
                    auth.conversation(c, who, initial["conversation_id"])
                    H.VIA.set("botops")
                    return acting_as(auth.identity_for_actor(c, requester), task_id, "comment")
                if refs.get("comment") or relayed:
                    raise Problem("on_behalf_of", "Only the task's requester's own comment lends their rights; "
                                  "other comments and routed messages lend nothing", 403)
            if initial and (task or str(requester).startswith("bot:")) and requester != H.KEEPER:
                auth.conversation(c, who, initial["conversation_id"])
                if not open_task(task) and week_old(initial["created"]):
                    raise Problem("on_behalf_of", "That request is more than a week old; ask the requester again", 403)
            if str(requester).startswith("bot:") and requester != who.actor:
                if (H.bot(c, H.actor_id(requester)) or {}).get("state") != "active":
                    raise Problem("forbidden", "The requesting bot is no longer active", 403)
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
        if week_old(msg["created"]) and (new_task or not c.execute(
                "SELECT 1 FROM tasks WHERE request_id=? AND requester=? AND status NOT IN ('done','closed','declined') "
                "LIMIT 1", (message_id, msg["from_actor"])).fetchone()):
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
        made = c.execute("SELECT actor,detail_json FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                         (task["id"],)).fetchone()
        via = H._json(made["detail_json"], {}).get("via") if made else None
        if not made or made["actor"] != requester or via == "assistant":
            raise Problem("on_behalf_of", "That task was not filed by the person who asked for it", 403)
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
        Closing is the exception: only the requester or a human closes, so a close keeps the person's rights."""
        found = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(?:comments|ask|links))?", path)
        if not found or (isinstance(body, dict) and body.get("close")):
            return False
        with store.read() as c:
            ref = found.group(1)
            row = c.execute("SELECT owner FROM tasks WHERE id=? OR (length(?)>=8 AND id LIKE ? || '%') LIMIT 1",
                            (ref, ref, ref)).fetchone()
        return bool(row and row["owner"] == "bot:" + BOTOPS)

    def act_for_requester(request, ref):
        """Apply the requester's identity before the route checks its normal permissions."""
        who = request.state.identity
        if who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "Only BotOps acts on a person's behalf", 403)
        # Runs on an executor thread: the delegation record is read back here and set again for the route.
        token = H.DELEGATION.set(None)
        try:
            with store.read() as c:
                acting = delegated_identity(c, who, ref)
            request.state.delegation = H.DELEGATION.get()
        finally:
            H.DELEGATION.reset(token)
        with store.read() as c:
            if acting.actor == who.actor:
                return None, None
            body = botops_act.parse_body(getattr(request, "_body", b"")) if request.method != "GET" else None
            if botops_act.secret_in(body):
                raise Problem("secret_in_request", botops_act.DETAIL_SECRET, 422)
            # The ordinary route checks the requester's rights, including its own outbound send switch.
            return None, acting

    def propose_card(c, acting, method, path, body, summary):
        """What always needs the requesting person's own click: a Confirm card in their chat with BotOps,
        and the answer BotOps reports back instead of asking them to go to Settings."""
        room = rooms.chat_room(c, auth, acting, BOTOPS)
        card = create_proposal(c, settings, acting, room, BOTOPS, summary, method, path, body)
        return {"proposed": True, "needs_confirm": True, **card,
                "detail": "This needs " + ((H.human(c, H.actor_id(acting.actor)) or {}).get("name") or "the person")
                + "'s own OK. A Confirm card is in their chat with BotOps; nothing has changed yet."}

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
        return mutate(request, body, work)

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
            result = H.inbox(c, who.actor)
            result["messages"] = [m for m in result["messages"] if privacy.message_readable(c, privacy.actor(who), m)]
            result["tasks"] = [t for t in result.get("tasks", []) if privacy.task_readable(c, who, t)]
            result["approvals"] = [a for a in result.get("approvals", [])
                                   if privacy.message_readable(c, privacy.actor(who), H.message(c, a["message_id"]))]
            if who.role == "bot":
                visible = []
                for message in result["messages"]:
                    try:
                        auth.conversation(c, who, message["conversation_id"])
                        visible.append(message)
                    except Problem:
                        pass
                result["messages"] = visible
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
        with store.read() as c:
            auth.require_read(c, who, bot)
            result = []
            for turn in H.turns(c, bot, since=views.since_time(since)):
                msg = H.message(c, turn["message_id"])
                if not msg:
                    continue
                try:
                    auth.conversation(c, who, msg["conversation_id"])
                    privacy.require_message(c, who, msg)
                    privacy.require_attempt(c, who, turn["id"])
                except Problem:
                    continue
                result.append({**turn, **views.attempt_details(c, turn)})
            result.extend(views.failed_attempts(c, who, auth, bot, views.since_time(since)))
            result.sort(key=lambda row: row.get("started") or "", reverse=True)
            return result

    @app.post("/api/v2/enrollments")
    def issue_enrollment(request: Request, body: M.EnrollmentRequest):
        return mutate(request, body, lambda c: execution.issue_enrollment(c, request.state.identity, body))

    @app.post("/api/v2/runners/enroll")
    def enroll(body: M.Enrollment):
        return execution.enroll(body)

    @app.post("/api/v2/runners/heartbeat")
    def heartbeat(request: Request, body: M.Heartbeat):
        return mutate(request, body, lambda c: execution.heartbeat(c, request.state.identity, body))

    @app.get("/api/v2/runners/desired")
    def desired_runner_release(request: Request):
        with store.read() as c:
            execution.runner(c, request.state.identity)
        return {**runner_versions.desired(), "features": {"task_files_multipart": True}}

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

    @app.post("/api/v2/runners/{rid}/inbox-sharing")
    def inbox_sharing(request: Request, rid: str, body: M.InboxSharing):
        """Let several inbox bots share this computer (they hold the same mail key anyway)."""
        who = request.state.identity
        def work(c):
            runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
            if not runner or not (auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["operator"]):
                raise Problem("forbidden", "You cannot change this computer", 403)
            if body.allowed and who.via == "botops" and not inbox_isolation.single_owner(c):
                raise Problem("forbidden", "BotOps turns inbox sharing on only where one owner runs every computer and bot",
                              403)
            inbox_isolation.allow_shared(c, rid, body.allowed)
            H.event(c, who.actor, "runner.inbox_sharing", rid, {"allowed": body.allowed})
            return {"allowed": body.allowed}
        return mutate(request, body, work)

    @app.post("/api/v2/runners/{rid}/logins")
    def start_login(request: Request, rid: str, body: M.LoginStart):
        return mutate(request, body, lambda c: model_login.start(
            c, request.state.identity, rid, body.runtime, body.profile, auth=auth))

    @app.get("/api/v2/runners/{rid}/logins/{lid}")
    def read_login(request: Request, rid: str, lid: str):
        # A read that also expires: a sign-in nobody finished does not stay open.
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
        with store.transaction() as c:
            execution.runner(c, who)
            return {"logins": model_login.pending(c, who.runner_id)}

    @app.post("/api/v2/runner-logins/{lid}/report")
    def report_login(request: Request, lid: str, body: M.LoginReport):
        who = request.state.identity
        return mutate(request, body, lambda c: (execution.runner(c, who),
                                                model_login.report(c, who.runner_id, lid, body))[1])

    @app.post("/api/v2/runners/{rid}/harness-actions")
    def harness_action(request: Request, rid: str, body: M.HarnessAction):
        return mutate(request, body, lambda c: harness_actions.request(
            c, request.state.identity, rid, body.harness, body.action))

    @app.get("/api/v2/runner-harness-actions")
    def runner_harness_actions(request: Request):
        who = request.state.identity
        with store.transaction() as c:
            execution.runner(c, who)
            return {"actions": harness_actions.pending(c, who.runner_id)}

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
            H.event(c, who.actor, "runner.revoked", rid)
            return {"revoked": True}
        return mutate(request, body, work)

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

    def place_now(c, who, bot, computer=""):
        """`hub bot place`: on the computer named, or the one the company's own rule picks (backend/placement.py)."""
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
        options = [wanted] if wanted else placing.candidates(c, auth, bot)
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
        with store.read() as c:
            result = fleet_check_module.check(c, who, auth, settings)
            result["computers"] = computer_rows(c, who)
            result["services"] = views.team_services(c, who, auth)
            from . import health
            from .onboarding import config_view
            checks = health.view(c, who, settings, auth, getattr(app.state, "github_app", None), config_view(c, settings, who))["checks"]
            result["checks"] = checks
            for check in checks:
                if check["status"] not in ("warn", "bad", "unknown"):
                    continue
                result["issues"].append({"bot": "", "name": check["label"], "kind": check["id"],
                                         "severity": "high" if check["status"] == "bad" else "medium",
                                         "text": check["summary"], "fixes": check["fixes"],
                                         "fix": "; ".join(f["label"] + (": " + f["href"] if f["href"] else "")
                                                          for f in check["fixes"])})
            result["issues"].sort(key=lambda i: (fleet_check_module.ORDER[i["severity"]], i["name"].lower(), i["kind"]))
            result["counts"] = {level: sum(i["severity"] == level for i in result["issues"])
                                for level in fleet_check_module.ORDER}
            return result

    def computer_rows(c, who):
        online = {r["id"] for r in _online_runners(c)}
        everyone = who.role == "owner" or auth.bot_admin(who)
        mine = H.actor_id(who.actor)
        readable = auth.bot_accesses(c, who)
        rows = []
        for r in c.execute("SELECT * FROM runners WHERE revoked_at IS NULL ORDER BY label"):
            if not (everyone or r["operator"] == mine or r["accepts_member_bots"]):
                continue
            bots = [x["bot"] for x in c.execute("SELECT bot FROM assignments WHERE runner_id=? ORDER BY bot", (r["id"],))
                    if (readable.get(x["bot"]) or {}).get("see")]
            rows.append({"id": r["id"], "label": r["label"], "online": r["id"] in online,
                         "platform": r["platform"] or "", "operator": r["operator"],
                         "accepts_member_bots": bool(r["accepts_member_bots"]), "bots": bots,
                         **views.computer_details(c, r, who, auth)})
        return rows

    @app.get("/api/v2/computers")
    def computers(request: Request):
        """The computers this person may put a bot on, with what runs on each: owners and admins see every one, a
        member their own and the ones opened to members' bots."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return {"computers": computer_rows(c, who), "services": views.team_services(c, who, auth),
                    "services_scope": "team"}

    @app.post("/api/v2/bots/{bot}/place")
    def place_bot(request: Request, bot: str, body: M.BotPlace):
        who = request.state.identity
        return mutate(request, body, lambda c: place_now(c, who, bot, body.computer))

    @app.post("/api/v2/bots/{bot}/go-live")
    def go_live(request: Request, bot: str, body: M.BotGoLive):
        """Everything between "built" and "working": a computer, active, and its setup started with the person.
        Each step is the same one the app's own buttons take, checked with the caller's rights."""
        who = request.state.identity

        def work(c):
            if body.routines is not None:
                settings_admin._manager(c, who, bot)
                live = {r["id"]: r for r in routines.listing(c, bot)}
                requested = {r.id for r in body.routines}
                for expected in body.routines:
                    row = live.get(expected.id)
                    if not row or any((row.get(key) if key == "enabled" else row.get(key) or "") != value for key, value in (
                            ("title", expected.title), ("cron", expected.cron), ("timezone", expected.timezone),
                            ("enabled", int(expected.enabled)), ("event_name", expected.on))):
                        raise Problem("routine_mismatch", "The live Routine does not match the requested schedule; verify it before activating", 409)
                if any(r["enabled"] and rid not in requested for rid, r in live.items()):
                    raise Problem("routine_mismatch", "An unrelated Routine is enabled; disable it before activating", 409)
            # An external agent (a Hermes profile) has a credential, not a computer.
            placed = ({"computer": None, "placed": False} if agents.external_harness(c, bot)
                      else place_now(c, who, bot, body.computer))
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
                    elif declared.get("template"):
                        if not H.bot(c, "botops"):
                            raise Problem("repository_missing", "Its repository is not built yet. Add BotOps to build it", 409)
                        build = onboarding.attach_template(c, who, bot, declared["template"],
                                                           declared.get("instructions") or "", queue_build=True)
                        return {"bot": bot, "state": row["state"], **placed, **build, "building": True,
                                "setup_started": False, "note": "BotOps is building its repository; watch setup_task_id"}
                    else:
                        raise Problem("repository_missing", "Its repository is not built yet. Ask BotOps to build it", 409)
            activated = row["state"] != "active"
            if activated:
                settings_admin.update_bot(c, who, bot, M.BotDefinitionUpdate(
                    status="active", expected_revision=settings_admin._config(c, bot)["revision"]))
            setup = False
            config = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if body.setup and config and is_parked(config["onboarding_state"]) and who.role in ("owner", "human"):
                send(c, who, M.MessageCreate(to="bot:" + bot, text="Let's set you up."))
                setup = True
            # Going live turns its first routine on too: nobody approves it separately.
            armed = onboarding.arm_first_routine(c, who, bot) if body.routines is None else None
            return {"bot": bot, "state": H.bot(c, bot)["state"], "computer": placed["computer"], "placed": placed["placed"],
                    "activated": activated, "setup_started": setup, "routine_armed": armed,
                    **({"building": True, "note": "The computer is building its repository; queued work starts when it is ready"}
                       if building else {})}
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
        """Who is asking, for the pairing rate limit: the address the front door saw, else the connection's."""
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[-1]
        return (forwarded or (request.client.host if request.client else "")).strip() or "unknown"

    # Pairing (docs/hermes-agents.md): the first two calls need no sign-in; approving and declining are a person's,
    # or BotOps's as the person who asked it.
    @app.post("/api/v2/agents/pairings", status_code=201)
    def agent_pairing_create(request: Request, body: M.AgentPairingCreate):
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
                current = onboarding._declared(c, body.slug)
                if not created["created"] and current.get("template") != body.template:
                    raise Problem("template", "Change the bot's template in its definition before building it", 409)
                created.update(onboarding.attach_template(c, who, body.slug, body.template, body.instructions,
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
            settings_admin._manager(c, who, bot)
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
            active = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (bot,)).fetchone()
            if active:
                raise Problem("busy", "Wait for the current run to finish before changing models", 409)
            # The same check a prepared change makes (settings_admin._target): a model the bot's computer reports it
            # cannot run is refused with the fix, not saved for a bot that then never runs.
            can = absolute_link(can_run(c, bot, runtime, harness=harness, model=choice["id"]), settings.public_url)
            if can["can_run"] is False:
                raise Problem("runner_not_ready", can["problem"], 409, extra={"fix": can["fix"], "link": can["link"]})
            config.update({"model": choice["id"], "runtime": runtime, "harness": harness,
                           "reasoning_effort": effort,
                           "model_managed_by": "cloud"})
            c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?",
                      (encode(config), bot))
            c.execute("UPDATE bots SET model=?,runtime=?,effort=? WHERE slug=?",
                      (choice["id"], runtime, effort, bot))
            reset = reset_bot_sessions(c, bot)
            settings_admin.record(c, who.actor, bot, "model", before,
                                  settings_admin.snapshot(c, bot, "model"))
            H.event(c, who.actor, "bot.model_changed", bot,
                    {"from": old_model, "to": choice["id"], "runtime": runtime,
                     "harness": harness, "effort": effort,
                     "sessions_reset": reset})
            return {"bot": bot, "model": choice["id"], "runtime": runtime, "harness": harness,
                    "effort": effort, "revision": row["revision"] + 1, "sessions_reset": reset, "readiness": can}
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/fallback")
    def fallback(request: Request, bot: str, body: M.BotFallback):
        return mutate(request, body, lambda c: settings_admin.set_fallback(
            c, request.state.identity, bot, body))

    @app.post("/api/v2/bots/{bot}/transitions")
    def begin_transition(request: Request, bot: str, body: M.BotTransitionCreate):
        def work(c):
            who = request.state.identity
            acting = delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who
            result = settings_admin.begin(c, acting, bot, body)
            if acting is not who:
                H.event(c, who.actor, "bot.transition_delegated", bot,
                        {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of, "kind": body.kind})
            return result
        return mutate(request, body, work)

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
    def settings_history(request: Request, limit: int = 100):
        with store.read() as c:
            return settings_admin.history(c, request.state.identity, limit)

    @app.post("/api/v2/settings/history/{change_id}/undo")
    def undo_setting(request: Request, change_id: str, body: M.SettingsUndo):
        return mutate(request, body, lambda c: settings_admin.undo(c, request.state.identity, change_id, body))

    @app.post("/api/v2/bots/{bot}/stop")
    def stop_turn(request: Request, bot: str, body: M.StopTurn):
        return mutate(request, body, lambda c: execution.stop(c, request.state.identity, bot, body.attempt_id))

    @app.post("/api/v2/bots/{bot}/control")
    def control(request: Request, bot: str, body: M.BotControl):
        who = request.state.identity
        def work(c):
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "You do not manage this bot", 403)
            config = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if config["revision"] != body.expected_revision:
                raise Problem("version_conflict", "Bot configuration changed; refresh before controlling it", 409)
            if H.bot(c, bot)["state"] == "quarantined" and body.action != "drain":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            if body.action == "resume":
                settings_admin.ensure_activation(c, who, bot)
            c.execute("INSERT INTO bot_control VALUES(?,?) ON CONFLICT(bot) DO UPDATE SET draining=excluded.draining",
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
            selected = []
            version = c.execute("PRAGMA data_version").fetchone()[0]
            idle = execution.idle_claim(c, request.state.identity, body,
                                        request.headers.get("idempotency-key"), selected)
            if idle is not None:
                return idle
            # Privacy checks open a coherent read snapshot. End it before data_version is
            # checked under the write lock, or a newer commit stays hidden by that snapshot.
            if c.in_transaction:
                c.rollback()
            # Under the write lock, reuse the read result only if no other connection
            # committed since that read began. A changed queue is selected afresh.
            def work(write):
                unchanged = c.execute("PRAGMA data_version").fetchone()[0] == version
                return execution.claim(write, request.state.identity, body,
                                       selected=selected[0] if unchanged and selected else None)
            return mutate(request, body, work)

    @app.post("/api/v2/attempts/{aid}/renew")
    def renew(request: Request, aid: str, body: M.Empty):
        return mutate(request, body, lambda c: execution.renew(c, request.state.identity, aid))

    @app.post("/api/v2/attempts/{aid}/started")
    def started(request: Request, aid: str, body: M.Started):
        return mutate(request, body, lambda c: execution.started(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/events")
    def events(request: Request, aid: str, body: M.EventBatch):
        body = scrub_attempt(aid, body)          # a secret set in this run never reaches the record
        return mutate(request, body, lambda c: execution.events(c, request.state.identity, aid, body))

    @app.post("/api/v2/attempts/{aid}/inputs")
    def inputs(request: Request, aid: str, body: M.Empty):
        return mutate(request, body, lambda c: execution.inputs(c, request.state.identity, aid))

    @app.post("/api/v2/attempts/{aid}/inputs/{mid}/ack")
    def input_ack(request: Request, aid: str, mid: str, body: M.Empty):
        return mutate(request, body, lambda c: execution.acknowledge_input(c, request.state.identity, aid, mid))

    @app.post("/api/v2/attempts/{aid}/complete")
    def complete(request: Request, aid: str, body: M.Completion):
        body = scrub_attempt(aid, body)
        return mutate(request, body, lambda c: execution.complete(c, request.state.identity, aid, body))

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
    # And one for each computer, in place of its polls (runner/runner_events.py).
    install_runner_events(app, store, execution)

    from .views import install_views
    from .task_types import install_task_types
    install_task_types(app, store, auth, mutate, mover)
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
    turn_work.install(app, store, auth)
    from .documents import install_documents
    install_documents(app, store, auth, mutate)
    from .docs import install_docs
    install_docs(app, store, auth, mutate)
    from .context_search import install_context_search
    install_context_search(app, store, auth)
    from .market import install as install_market
    install_market(app, store, auth, mutate)
    from .listening import install as install_listening
    install_listening(app, store, auth, mutate)
    from .archive import install_archives
    install_archives(app, store)
    from .media import install_media
    install_media(app, store, auth, mutate, send, task_create)
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
    install_credential_cards(app, store, app.state.vault, auth, BOTOPS, delegated_identity, settings_admin._manager)
    from .sql import install_sql
    install_sql(app, store, auth)
    from .judge import install_judge
    install_judge(app, store, auth)
    from .live_meetings import install_live_meetings
    install_live_meetings(app, store, auth, mutate)
    from .mail import install_mail
    install_mail(app, store, auth)
    from .messaging import install_messaging
    install_messaging(app, store, auth)
    from .integrations import install_integrations
    install_integrations(app, store, auth, mutate)
    from .memory_history import install_memory_history
    install_memory_history(app, store, auth, mutate)
    from .learnings import install as install_learnings
    install_learnings(app, store, auth, mutate)
    from .mcp import install_mcp
    install_mcp(app, settings)
    from .github import install_github
    install_github(app, settings, store)
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
    from .goal_routes import install as install_goal_routes
    install_goal_routes(app, store, auth, mutate, settings)
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
    from .shared_bots import install as install_branches
    install_branches(app, store, auth, mutate, settings_admin, execution)
    from .assignment_branches import install as install_assignment_branches
    install_assignment_branches(app, store, auth, mutate, settings_admin, execution)
    from .bot_copy import install as install_bot_copy
    install_bot_copy(app, store, auth, mutate, settings_admin, place_now)
    from .subscriptions import install as install_subscriptions
    install_subscriptions(app, store, auth, mutate, settings, computer_rows)
    from .groups import install as install_groups
    install_groups(app, store, auth, mutate, settings)
    from .support import install as install_support
    install_support(app, store, settings, census)
    from .watchers import install as install_watchers
    install_watchers(app, store, auth, execution, mutate)
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
