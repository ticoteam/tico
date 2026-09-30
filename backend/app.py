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
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

from clients.agent_skill import WHO_NEEDS_ME

from . import agents, batch, grokbot, inbox_isolation, harness_actions, model_login, oidc, personal_tokens, views
from . import team_rules, usage_limits
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
from . import names as actor_names
from .openapi_v2 import STABLE as STABLE_ROUTES
from .route_renames import old_paths as old_route_paths
from . import updates
from . import providers as Providers
from . import access as Access
from . import releases, runner_versions, ui_bundle
from .census import Census
from .harnesses import HARNESS_CATALOG, resolve_harness, runtime_of
from .providers import MODEL_BY_ID, MODEL_CATALOG
from .settings_admin import SettingsAdmin
from . import fleet_check as fleet_check_module
from .getting_started import _online_runners
from .credential_cards import install_credential_cards, scrub_attempt
from .github import PATH as GITHUB_WEBHOOK_PATH
from .store import H, P, Problem, Store, encode, message_page, repo_url, task_message_page




# The stable v2 routes whose answers carry display names (backend/names.py); streams are left alone.
STABLE_PATH = re.compile("|".join(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(spelling))
                                  for path, *_ in STABLE_ROUTES if not path.endswith(("/stream", "/watch"))
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
    store = Store(settings)
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
        # This release may ship merged product tasks that waited for it (backend/github.py).
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
        async def schedule_loop():
            from .scheduler import Scheduler
            scheduler = Scheduler(store, execution)
            while not stop.is_set():
                try:
                    await asyncio.to_thread(scheduler.tick)
                    # The release check (and the anonymous count that rides on it) runs even when nobody has the
                    # page open; it is a no-op until its six hours are up.
                    await asyncio.to_thread(releases.notice)
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
        # A demo runs no scheduler: nothing fires, and nothing waits for a bot that will never run.
        demo_task = None
        if settings.demo:
            from . import demo
            demo_task = asyncio.create_task(demo.keep_alive(store, stop))
        try:
            yield
        finally:
            stop.set()
            if demo_task:
                await demo_task
            await timing_task
            if scheduler_task:
                await scheduler_task
            if directory_task:
                await directory_task
            telemetry.close()

    app = FastAPI(title=settings.app_name + " API", version="2.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.auth, app.state.execution = store, auth, execution

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith('/api/v2/credentials'):
            # Default validation responses echo invalid input, which may contain a password.
            return JSONResponse({'error': {'code': 'validation', 'detail': 'Invalid credential input'}}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    app.state.observability = telemetry
    app.state.census = census

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        if exc.status >= 500:
            telemetry.capture("request", exc, exc.status)
            request.state.telemetry_captured = True
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
        via_reset = None
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
            # The repository webhook (backend/github.py) carries its own HMAC signature.
            if request.url.path == GITHUB_WEBHOOK_PATH and request.method == "POST":
                return await call_next(request)
            # SCIM carries its own bearer token, which an identity provider holds (backend/scim.py).
            if request.url.path.startswith("/scim/v2/"):
                return await call_next(request)
            if request.url.path != "/api/v2/runners/enroll":
                began = time.perf_counter()
                who = await asyncio.get_running_loop().run_in_executor(AUTH_POOL, auth.authenticate, request.headers,
                                                                       request.url.path, request.method)
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
                limit = (27_000_000 if published else 20_000_000 if docs_import or upload and request.state.identity.role in ("human", "owner")
                         else 2_000_000)
                if int(size) > limit:
                    raise Problem("too_large", "Request exceeds the upload limit", 413)
                # Streaming/chunked requests also have a hard limit. Starlette caches body()
                # for downstream parsing; collect only a bounded amount before assigning it.
                chunks, total = [], 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        raise Problem("too_large", "Request exceeds the upload limit", 413)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
                if (getattr(request.state, "identity", None) and request.state.identity.via
                        and not request.state.identity.confirmed
                        and not assistant_writes(request.method, request.url.path, settings, request._body,
                                                 request.state.identity.actor,
                                                 lambda tid, shared: assistant_owns(tid, request.state.identity.actor, shared),
                                                 assistant_direct(), assistant_to_bot)):
                    raise Problem("confirm_required", "The " + settings.assistant_name + " may not do this on its "
                                  "own. Propose it with `hub assistant propose` (or POST /api/v2/assistant/actions); "
                                  "the person confirms it in " + settings.app_name, 403)
            early = None
            on_behalf = request.headers.get(botops_act.HEADER)
            if on_behalf and getattr(request.state, "identity", None) is not None:
                # BotOps doing what the person who asked it could do in the app (backend/botops_act.py).
                early, acted = await asyncio.get_running_loop().run_in_executor(
                    None, act_for_requester, request, on_behalf)
                if acted is not None:
                    request.state.identity = acted
                    if via_reset is None:
                        via_reset = H.VIA.set("botops")
            response = early or await call_next(request)
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
            if route_keeps:
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
            telemetry.capture("request", exc, 500)
            raise
        finally:
            if via_reset is not None:
                H.VIA.reset(via_reset)

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
            if person is not None:
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
            app.state.timing.end(key, request.state.auth_ms, (time.perf_counter() - started) * 1000)

    @app.middleware("http")
    async def display_names(request, call_next):
        """Stable v2 answers carry display names beside actor ids (backend/names.py)."""
        response = await call_next(request)
        if (response.status_code != 200 or "count" in request.query_params or not STABLE_PATH.fullmatch(request.url.path)
                or "application/json" not in response.headers.get("content-type", "")):
            return response
        raw = b"".join([chunk async for chunk in response.body_iterator])
        who = getattr(request.state, "identity", None)

        def work():
            with store.read() as c:
                return actor_names.annotate_json(c, raw, render_notices=bool(who and who.role in ("human", "owner")),
                                              actors=request.method == "GET")
        try:
            body = await asyncio.get_running_loop().run_in_executor(None, work)
        except sqlite3.Error:
            body = None
        headers = [(k, v) for k, v in response.raw_headers if k.lower() != b"content-length"]
        out = Response(content=body if body is not None else raw, status_code=response.status_code)
        out.raw_headers = headers + [(b"content-length", str(len(body if body is not None else raw)).encode())]
        return out

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
    def all_routines(request: Request, include_deleted: bool = False):
        who = request.state.identity
        if who.role not in ('human', 'owner'):
            raise Problem('forbidden', 'This endpoint is available only to people', 403)
        with store.read() as c:
            readable = auth.bot_accesses(c, who)
            rows = [row for row in routines.listing(c, include_deleted=include_deleted, summary=True)
                    if readable.get(row['bot'], auth.FULL)['read']]
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
            return {'occurrences': routines.occurrences(c, schedule_id, limit)}

    def mutate(request, body, fn):
        result = store.mutate(request.state.identity, request.url.path,
                              request.headers.get("idempotency-key"), body.model_dump(), fn)
        if isinstance(result, dict) and "_refusal" in result:
            refusal = result["_refusal"]
            raise Problem(refusal["code"], refusal["detail"], refusal["status"])
        return result

    def task_view(row, c=None, parts=None):
        value = {**row, "acceptance_criteria": json.loads(row.get("acceptance_json", "[]")),
                 "labels": H.task_labels(row), "lane": row.get("lane") or "company",
                 "next_run": bool(row.get("next_run"))}
        if c is not None and row.get("next_run"):
            value["next_run_waiting"] = H.next_run_waiting(c, row)
        value.pop("labels_json", None)
        if c is not None:
            routine = c.execute('SELECT schedule_id FROM schedule_occurrences WHERE task_id=?', (row['id'],)).fetchone()
            if routine:
                value.update(routine_id=routine['schedule_id'])
            value.update({"origin_actor": H.task_origin(c, row), "ask": H.unanswered_ask(c, row)})
            from .blobs import brief
            value["attachments"] = [brief(r) for r in c.execute(
                "SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=?", (row["id"],))]
            value["links"] = H.task_links(c, row["id"])
            if row.get("blocked_by"):
                blocker = H.task(c, row["blocked_by"])
                value["blocker"] = {"id": blocker["id"], "title": blocker["title"], "status": blocker["status"]} if blocker else None
            if parts is None:
                counts = c.execute("SELECT COUNT(*), SUM(status IN ('done','closed')) FROM tasks WHERE parent_id=?",
                                   (row["id"],)).fetchone()
                parts = {"total": counts[0] or 0, "done": counts[1] or 0}
            value["parts"] = parts
        return value

    def task_views(rows, c):
        """Hydrate one task-list page with a fixed number of relation queries."""
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        marks = ",".join("?" * len(ids))

        parts = {row["parent_id"]: {"total": row["total"] or 0, "done": row["done"] or 0}
                 for row in c.execute(
                     f"SELECT parent_id,COUNT(*) total,SUM(status IN ('done','closed')) done FROM tasks "
                     f"WHERE parent_id IN ({marks}) GROUP BY parent_id", ids)}
        routines_by_task = {row["task_id"]: row["schedule_id"] for row in c.execute(
            f"SELECT task_id,schedule_id FROM schedule_occurrences WHERE task_id IN ({marks})", ids)}

        links = {tid: [] for tid in ids}
        for link in c.execute(f"SELECT * FROM task_links WHERE task_id IN ({marks}) ORDER BY created", ids):
            links[link["task_id"]].append(dict(link))

        attachments = {tid: [] for tid in ids}
        from .blobs import brief
        for asset in c.execute(
                f"SELECT a.task_id,b.* FROM task_assets a JOIN blobs b ON b.id=a.blob_id "
                f"WHERE a.task_id IN ({marks})", ids):
            attachments[asset["task_id"]].append(brief(asset))

        blocker_ids = list({row.get("blocked_by") for row in rows if row.get("blocked_by")})
        blockers = {}
        if blocker_ids:
            blocker_marks = ",".join("?" * len(blocker_ids))
            blockers = {row["id"]: dict(row) for row in c.execute(
                f"SELECT id,title,status FROM tasks WHERE id IN ({blocker_marks})", blocker_ids)}

        asks = {}
        conversations = list({row.get("conversation_id") for row in rows if row.get("conversation_id")})
        if conversations:
            conv_marks = ",".join("?" * len(conversations))
            latest = {}
            for message in c.execute(
                    f"SELECT * FROM messages WHERE kind='ask' AND conversation_id IN ({conv_marks}) "
                    "ORDER BY conversation_id,created DESC", conversations):
                latest.setdefault(message["conversation_id"], dict(message))
            ask_ids = [message["id"] for message in latest.values()]
            answered = set()
            if ask_ids:
                ask_marks = ",".join("?" * len(ask_ids))
                answered.update(row[0] for row in c.execute(
                    f"SELECT DISTINCT in_reply_to FROM messages WHERE kind='answer' "
                    f"AND in_reply_to IN ({ask_marks})", ask_ids))
            for conversation, message in latest.items():
                if message["id"] in answered or message.get("answered_by"):
                    continue
                message["refs"] = H._json(message.get("refs_json"), {}) or {}
                asks[conversation] = message

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
        result = []
        for row in rows:
            value = task_view(row)
            value.update({
                "parts": parts.get(row["id"], {"total": 0, "done": 0}),
                "links": links[row["id"]],
                "attachments": attachments[row["id"]],
                "ask": asks.get(row.get("conversation_id")),
            })
            if row.get("blocked_by"):
                value["blocker"] = blockers.get(row["blocked_by"])
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
                      limit=500, offset=0, order="queue"):
        auth.domain(who)
        rows = H.tasks(c, owner=owner, requester=requester, status=status, lane=lane, label=label,
                       limit=limit + 1, offset=offset, order=order, visible=auth.task_sql(c, who))
        page, has_more = rows[:limit], len(rows) > limit
        return task_views(page, c), offset + limit if has_more else None

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
        to = auth.target(c, who, body.to)
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
            if body.conversation_id and msg["conversation_id"] != body.conversation_id:
                raise Problem("reference", "Reply belongs to a different conversation", 422)
        check_refs(c, who, body.refs)
        refs = dict(body.refs or {})
        if in_assistant_room:
            refs["assistant"] = True           # the server's mark: this turn acts for the person
        conversation_id = body.conversation_id
        if not conversation_id and who.role in ("owner", "human") and to.startswith("bot:"):
            conversation_id = rooms.chat_room(c, auth, who, H.actor_id(to))["id"]
        message = H.say(c, who.actor, to, body.text, conversation_id=conversation_id,
                        kind=body.kind, refs=refs, in_reply_to=body.in_reply_to, wait_s=body.wait_s)
        if who.role == "bot":
            conv = H.conversation(c, message["conversation_id"])
            if who.attempt_id and all(p.startswith("bot:") for p in conv["participants"]):
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            auth.conversation(c, who, conv["id"])
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
        with store.read() as c:
            return config_view(c, settings, request.state.identity)

    @app.get("/api/v2/system/update")
    def system_update_status(request: Request):
        owner_only(request.state.identity, "sees update progress")
        result = releases.status()
        if result.get("state") in ("healthy", "rolled_back", "failed"):
            record_update_outcome(result)
        return result

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
        owner_only(request.state.identity, "checks for updates")
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
        owner_only(who, "updates the installation")
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
        company's domain at once; anyone outside it answers with a Confirm card first: it needs the requester's own click."""
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
            if risky(who) and Access.domain_of(email) not in domains_:
                return propose_card(c, who, "POST", "/api/v2/access/people",
                                    {"name": body.name, "email": email, "title": body.title, "team": body.team,
                                     "reports_to": body.reports_to},
                                    f"Add {body.name or email} ({email}) to the roster"
                                    + "".join(f", team {t}" for t in (body.team,) if t)
                                    + "".join(f", reporting to {r}" for r in (body.reports_to,) if r))
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
            return {"tokens": personal_tokens.listing(c, request.state.identity)}

    @app.post("/api/v2/me/tokens")
    def create_my_token(request: Request, body: M.PersonalTokenCreate):
        return mutate(request, body, lambda c: personal_tokens.create(c, auth, request.state.identity, body))

    @app.post("/api/v2/me/tokens/{token_id}/revoke")
    def revoke_my_token(request: Request, token_id: str, body: M.Empty):
        return mutate(request, body, lambda c: personal_tokens.revoke(c, request.state.identity, token_id))

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
                "thread_mode", "temp", "access", "bot_owners", "onboarding_state")

    def bot_view(c, bot, level, access, registry_roster, registry_entries):
        """One bot as the bot list and the bot detail show it: everything for a caller who may read
        it, only its profile for one who may only see it."""
        row = {k: v for k, v in bot.items() if k not in ("token_hash", "cwd", "thread_id")}
        config = c.execute("SELECT team,operator,owner_ids_json,revision,description,reports_to,repo,"
                           "thread_mode,config_json,onboarding_state FROM bot_config WHERE bot=?",
                           (bot["slug"],)).fetchone()
        assignment = c.execute("SELECT a.bot,a.runner_id,a.generation,r.label,r.operator,r.last_seen,"
                               "r.revoked_at FROM assignments a JOIN runners r ON r.id=a.runner_id "
                               "WHERE a.bot=?", (bot["slug"],)).fetchone() if level["read"] else None
        row["access"] = level
        row["team"] = config["team"] if config else None
        row["operator"] = config["operator"] if config else None
        row["revision"] = config["revision"] if config else None
        # `needs_setup` for a starter bot until it says its setup is done, then
        # `onboarded`; empty for every other bot (backend/onboarding.py).
        row["onboarding_state"] = (config["onboarding_state"] or "") if config else ""
        if config:
            repo = config["repo"] or ("emp-" + bot["slug"])
            declared = json.loads(config["config_json"]) if config["config_json"] else {}
            row.update({"description": config["description"] or "",
                        "reports_to": config["reports_to"], "repo": repo,
                        "repo_url": repo_url(repo, settings.github_owner),
                        "bot_contact": declared.get("bot_contact") or "open",
                        "template": declared.get("template") or "",
                        "template_version": declared.get("template_version") or "",
                        "temp": bool(declared.get("temp")),
                        "thread_mode": config["thread_mode"] or rooms.thread_mode(c, bot["slug"])})
        configured = json.loads(config["owner_ids_json"]) if config and config["owner_ids_json"] else None
        owner_rows = ([H.human(c, owner) for owner in configured] if configured is not None
                      else P.primary_users(bot["slug"], registry_roster, registry_entries))
        row["owners"] = [P.brief(owner) for owner in owner_rows if owner]
        row["bot_owners"] = settings_admin.owner_rows(c, bot["slug"]) if config else []
        if row.get("reports_to") and not str(row["reports_to"]).startswith("human:") \
                and not access.get(row["reports_to"], auth.FULL)["see"]:
            row["reports_to"] = ""      # a bot this caller may not see is not named to them
        if not level["read"]:
            return {k: v for k, v in row.items() if k in SEE_ONLY}
        row["draining"] = bool(c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot["slug"],)).fetchone())
        row["status"] = H.status(c, bot["slug"])
        row["assignment"] = dict(assignment) if assignment else None
        row["online"] = bool(assignment and not assignment["revoked_at"] and assignment["last_seen"]
                             and assignment["last_seen"] > H.shift(H.now(), seconds=-60))
        row["queued"] = c.execute("SELECT count(*) FROM jobs WHERE bot=? AND state='queued'", (bot["slug"],)).fetchone()[0]
        row["next_run"] = len(H.next_run_tasks(c, bot["slug"]))
        row["notes"] = len(H.notes_waiting(c, bot["slug"], limit=500))
        external = agents.presence(c, bot["slug"])
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
        with store.read() as c:
            result = []
            registry_roster, registry_entries = views.roster(c), views.entries(c, settings.github_owner)
            access = auth.bot_accesses(c, who)
            for bot in H.bots(c):
                level = access.get(bot["slug"], auth.FULL)
                if not level["see"] or (can and not level[can]):
                    continue
                if bot.get("state") == "archived" and not with_archived:
                    continue
                result.append(bot_view(c, bot, level, access, registry_roster, registry_entries))
            return result

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
            value = bot_view(c, row, level, access, views.roster(c), views.entries(c, settings.github_owner))
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
                if body.reports_to is not None:
                    row = {**row, "reports_to": body.reports_to.strip()}
                if body.left:
                    row = Access.leave(c, people, row)
                people[i] = row
                roster = {**roster, "people": people}
                c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(roster),))
                H.event(c, who.actor, "person.updated", pid, {"title": body.title is not None, "about": body.about is not None,
                                                              "goals": body.goals is not None, "notes": body.notes is not None,
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
                messages = H.messages(c, conv["id"], limit=200)
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
                return rooms.chat_room(c, auth, who, bots[0])
            conv = H.open_conversation(c, who.actor, participants, kind=body.kind, subject=body.subject)
            if who.role == "bot" and who.attempt_id:
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            return conv
        return mutate(request, body, work)

    @app.get("/api/v2/conversations/{cid}/messages")
    def messages(request: Request, cid: str, since: str | None = None, before: str | None = None):
        with store.read() as c:
            auth.conversation(c, request.state.identity, cid)
            page = message_page(c, cid, since=since, before=before)
            turn_work.annotate(c, auth, request.state.identity, page["messages"])
            return {"conversation": H.conversation(c, cid), **page}

    @app.post("/api/v2/messages")
    def say(request: Request, body: M.MessageCreate):
        return mutate(request, body, lambda c: send(c, request.state.identity, body))

    @app.get("/api/v2/messages/{mid}")
    def message(request: Request, mid: str):
        with store.read() as c:
            row = H.message(c, mid)
            if not row:
                raise Problem("not_found", "Message not found", 404)
            who = request.state.identity
            if who.role == "bot" and (row.get("to_actor") == who.actor or row.get("from_actor") == who.actor):
                return row
            auth.conversation(c, who, row["conversation_id"])
            return row

    @app.post("/api/v2/chat/{bot}")
    def chat(request: Request, bot: str, body: M.ChatCreate):
        def work(c):
            who = request.state.identity
            message = send(c, who, M.MessageCreate(to="bot:" + bot, text=body.text, refs=body.refs))
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

    @app.get("/api/v2/updates")
    def updates_list(request: Request, kind: str | None = None, bot: str | None = None, unread: bool = False,
                     before: str | None = None, limit: int = 40, mine: bool = False):
        who = request.state.identity
        auth.domain(who)
        if kind and kind not in updates.KINDS:
            raise Problem("kind", "kind is daily or weekly", 422)
        with store.read() as c:
            return updates.listing(c, who.actor, update_visible(c, who, mine), kind=kind, bot=bot, unread=unread,
                                   before=before, limit=max(1, min(limit, 100)))

    @app.get("/api/v2/updates/unread")
    def updates_unread(request: Request, mine: bool = False):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return {"unread": updates.count_unread(c, who.actor, update_visible(c, who, mine))}

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
            return {"update": {**row, "read": bool(read)}, "thread": thread}

    @app.post("/api/v2/updates")
    def update_post(request: Request, body: M.UpdatePost):
        who = request.state.identity
        def work(c):
            if who.role != "bot":
                raise Problem("identity", "Only a bot posts an update", 403)
            return {"update": updates.post(c, H.actor_id(who.actor), body.body, kind=body.kind)}
        return mutate(request, body, work)

    @app.post("/api/v2/updates/read")
    def updates_read(request: Request, body: M.UpdateRead):
        who = request.state.identity
        def work(c):
            views.human_only(who)
            ids = list(body.ids)
            if body.all:
                visible = update_visible(c, who)
                ids = [r["id"] for r in c.execute("SELECT id, bot FROM updates WHERE created>=?",
                                                   (H.shift(H.now(), days=-30),)) if r["bot"] in visible]
            return {"marked": updates.mark(c, who.actor, ids, read=body.read), "read": body.read}
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
            if rooms.thread_mode(c, bot) != rooms.PERSONAL:
                raise Problem("shared_room", "A shared bot room cannot be reset by one member", 409)
            views.require_chat(c, auth, who, bot)
            archived = rooms.archive_personal_room(c, who.actor, bot)
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
        start = since or H.shift(H.now(), hours=-12)
        with store.read() as c:
            items = views.needs_items(c, auth, who, task_view)
            snapshot = views.fleet_snapshot_cached(c, auth, who, task_view)
            rows, notes, reports = batch.candidates(c, who.actor, items)
            said = []
            for m in c.execute("SELECT from_actor,kind,body,created FROM messages WHERE to_actor=? AND "
                               "from_actor LIKE 'bot:%' AND kind IN ('say','ask','answer') AND created>? "
                               "ORDER BY created DESC LIMIT 6", (who.actor, start)).fetchall():
                said.append({"from": batch._name(c, m["from_actor"]), "kind": m["kind"],
                             "when": m["created"], "text": batch._clip(m["body"] or "", 240)})
            stuck = H.stuck_tasks(c, hidden=auth.unreadable_bots(c, who))
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
            return {"message": send(c, who, M.MessageCreate(to=target, text=body.text, conversation_id=cid, refs=body.refs))}
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
    def tasks(request: Request, owner: str | None = None, requester: str | None = None, status: str | None = None,
              lane: str | None = None, label: str | None = None, limit: int = 500,
              offset: int = 0, sort: str = "queue"):
        with store.read() as c:
            owner = H.resolve_actor(c, owner) if owner else None
            requester = H.resolve_actor(c, requester) if requester else None
            if lane and lane not in H.TASK_LANES:
                raise Problem("kind", "lane is company or product", 422)
            if limit < 1 or limit > 500:
                raise Problem("limit", "limit is between 1 and 500", 422)
            if offset < 0:
                raise Problem("offset", "offset is zero or greater", 422)
            if sort not in ("queue", "finished"):
                raise Problem("sort", "sort is queue or finished", 422)
            rows, next_offset = visible_tasks(c, request.state.identity, owner, requester,
                status.split(",") if status and status != "all" else None, lane=lane, label=label,
                limit=limit, offset=offset, order=sort)
            return {"tasks": rows, "next_offset": next_offset}

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
                where.append("n.created>=?"); args.append(since)
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
    def task_labels(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return {"labels": H.labels_in_use(c, auth.task_sql(c, who))}

    @app.get("/api/v2/tasks/stuck")
    def tasks_stuck(request: Request, hours: int = H.STUCK_HOURS):
        """Every bot's stuck work, for the fleet maintainer's sweep and for people."""
        who = request.state.identity
        if who.role not in ("human", "owner") and who.actor != H.bot_actor(H.FLEET_MAINTAINER):
            raise Problem("forbidden", "The stuck-task sweep is BotOps's and people's", 403)
        with store.read() as c:
            return {"tasks": H.stuck_tasks(c, hours=max(1, hours), hidden=auth.unreadable_bots(c, who))}

    @app.get("/api/v2/tasks/{tid}")
    def task(request: Request, tid: str):
        with store.read() as c:
            row = auth.task(c, request.state.identity, tid)
            # A bot task now lives in the bot's chat room. Only this task's messages
            # come back here; the rest of that room stays on Chat.
            who = request.state.identity
            children = [{"id": t["id"], "title": t["title"], "status": t["status"], "owner": t["owner"]}
                        for t in H._rows(c.execute("SELECT id,title,status,owner FROM tasks WHERE parent_id=? AND ("
                                                   + auth.task_sql(c, who) + ") ORDER BY (rank IS NULL), rank, created",
                                                   (tid,)))]
            parent = H.task(c, row["parent_id"]) if row.get("parent_id") else None
            if parent:
                try:
                    auth.task_row(c, who, parent)
                except Problem:
                    parent = None
            return {"task": task_view(row, c), "events": H.task_history(c, tid),
                    "children": children,
                    "parent": {"id": parent["id"], "title": parent["title"], "status": parent["status"]} if parent else None,
                    "comments": H.task_comments(c, tid),
                    "mover": mover(c, request.state.identity),
                    **task_message_page(c, row)}

    def task_create(c, who, body, lint=True):
        owner = auth.target(c, who, body.owner, need="write")
        auth.require_bot_contact(c, who, owner, task_id=body.parent_id, kind="task")
        # A subtask is the shape that parks the filer as `waiting` and makes it care when the
        # other one finishes. A bot set to `tasks` has opted out of that on both sides: it does
        # not wait, and its own completions are quiet, so parking on one would wait for ever.
        # Every other bot keeps the handoff flow in `policies/handoffs.md`.
        if (body.parent_id and who.role == "bot" and owner != who.actor
                and str(owner).startswith("bot:")
                and (H.tasks_only(c, who.actor) or H.tasks_only(c, owner))):
            raise Problem("subtask", "A bot set to tasks-only files work flat: give it its own "
                                     "task without a parent, and do not wait for it.", 422)
        if body.parent_id:
            auth.task(c, who, body.parent_id)
        if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        row = H.task_create(c, who.actor, body.title, body.body, owner, body.due, body.parent_id,
                            conversation_id=rooms.task_conversation_id(c, auth, owner, who.actor),
                            lane=body.lane, labels=body.labels, top=body.top, lint=lint,
                            goal_id=body.goal_id, next_run=body.next_run)
        c.execute("UPDATE tasks SET acceptance_json=? WHERE id=?", (encode(body.acceptance_criteria), row["id"]))
        for url in body.links:
            H.task_link(c, who.actor, row["id"], url)
        return {"task": task_view(H.task(c, row["id"]), c)}

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
            row = auth.task(c, who, tid)
            if who is not caller:
                H.event(c, caller.actor, "task.update_delegated", tid,
                        {"on_behalf_of": who.actor, "message_id": body.on_behalf_of,
                         "close": body.close, "status": body.status, "owner": body.owner})
            if row["version"] != body.version:
                raise Problem("version_conflict", "Task changed; fetch it and retry your update", 409)
            if body.owner:
                auth.require_bot_contact(c, who, auth.target(c, who, body.owner, need="write"), task_id=tid, kind="task")
            if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
                raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
            if body.goal_id and not G.goal(c, body.goal_id):
                raise Problem("not_found", "Unknown goal", 404)
            if body.close:
                if any(v is not None for v in (body.status, body.owner, body.due, body.body, body.lane,
                                               body.labels, body.blocked_by, body.parent_id, body.rank,
                                               body.goal_id)):
                    raise Problem("close", "Close and edit are separate operations", 422)
                H.task_close(c, who.actor, tid, note=body.note or "")
            else:
                if body.blocked_by:
                    auth.task(c, who, body.blocked_by)
                if body.parent_id:
                    auth.task(c, who, body.parent_id)
                H.task_update(c, who.actor, tid, **body.model_dump(exclude={"version", "close", "on_behalf_of"}),
                              mover=mover(c, who) or None)
                # A bot's own tasks sat 'done' for days because the reviewer is the
                # same bot. A task its owner asked for itself closes when that owner marks it done.
                if body.status == "done" and row["owner"] == row["requester"] == who.actor:
                    H.task_close(c, who.actor, tid, note=body.note or "")
            c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (tid,))
            return {"task": task_view(H.task(c, tid), c)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/run-now")
    def task_run_now(request: Request, tid: str, body: M.Empty):
        """Wake the task's bot for this task now, as the task (not a chat). A person, the task's
        requester, or the fleet maintainer's stuck-task sweep (never on a private bot's task)."""
        def work(c):
            who = request.state.identity
            if who.actor == H.bot_actor(H.FLEET_MAINTAINER):
                row = H.task(c, tid)
                if not row:
                    raise Problem("not_found", "Task not found", 404)
                hidden = auth.unreadable_bots(c, who)
                if any(str(a).startswith("bot:") and H.actor_id(a) in hidden
                       for a in (row["owner"], row["requester"])):
                    raise Problem("forbidden", "This task involves a bot whose activity BotOps may not read", 403)
            else:
                row = auth.task(c, who, tid)
                if who.role not in ("human", "owner") and who.actor != row["requester"]:
                    raise Problem("forbidden", "Only a person, the task's requester or BotOps starts it early", 403)
                # Running it wakes the bot: that is a request to it.
                if str(row["owner"]).startswith("bot:") and who.actor != row["owner"]:
                    auth.require_write(c, who, H.actor_id(row["owner"]))
            after, queued = H.task_run_now(c, who.actor, tid)
            return {"task": task_view(after, c), "queued": queued}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/ask")
    def task_ask(request: Request, tid: str, body: M.Answer):
        def work(c):
            auth.task(c, request.state.identity, tid)
            return H.task_ask(c, request.state.identity.actor, tid, body.text)
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/comments")
    def task_comment(request: Request, tid: str, body: M.TaskComment):
        """A comment on a task. It wakes the bot on the task when a mover, the owner or the
        requester left it; anyone else's is saved for the bot's next turn on the task."""
        who = request.state.identity
        def work(c):
            row = auth.task(c, who, tid)
            # The comment goes to the bot on the other side of the task (`H.task_comment`), so it is
            # a request to it: Write, and the same bot-to-bot limits a message has. The owner
            # answering whoever asked for the work is the reply path, which needs no Write.
            others = [a for a in dict.fromkeys([row["owner"], row["requester"], H.task_origin(c, row)])
                      if a and a not in (who.actor, H.KEEPER)]
            target = next((a for a in others if H.is_bot(a)), None)
            if target and who.actor != row["owner"]:
                auth.require_write(c, who, H.actor_id(target))
            if target:
                auth.require_bot_contact(c, who, target, task_id=tid, kind="comment")
            wake = who.role == "bot" or who.actor in (row["owner"], row["requester"]) or mover(c, who)
            msg = H.task_comment(c, who.actor, tid, body.text, wake=wake)
            return {"comment": msg, "comments": H.task_comments(c, tid), "woke": bool(wake)}
        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/links")
    def task_links(request: Request, tid: str, body: M.TaskLink):
        who = request.state.identity
        def work(c):
            auth.task(c, who, tid)
            if body.remove:
                H.task_unlink(c, who.actor, tid, body.remove)
            elif body.url:
                H.task_link(c, who.actor, tid, body.url, body.title)
            else:
                raise Problem("kind", "Send a url to add or remove with a link id", 422)
            return {"links": H.task_links(c, tid)}
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
            return auth.approval(c, request.state.identity, aid)

    @app.post("/api/v2/approvals/{aid}")
    def decide(request: Request, aid: str, body: M.ApprovalDecision):
        who = request.state.identity
        def work(c):
            auth.approval(c, who, aid, decide=True)
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
        with store.read() as c:
            from . import usage_limits
            default = usage_limits.company(c)

            def with_bot_state(row):
                if row:
                    row["bot_state"] = (H.bot(c, row["bot"]) or {}).get("state")
                    row = usage_limits.overlay(c, row, default)      # over a spend limit: paused, and why
                return row
            if bot:
                auth.target(c, who, bot, need="read")
                from .views import since_time
                return {"bot": bot, "status": with_bot_state(H.status(c, bot)),
                        "history": H.status_history(c, bot, since=since_time(since))}
            from .views import updating
            readable = auth.bot_accesses(c, who)
            return {"bots": [with_bot_state(s) for s in H.status_all(c)
                             if readable.get(s["bot"], auth.FULL)["read"]],
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

    DELEGATION_DAYS = 7               # the message that started the turn (a queued turn may wait)
    DELEGATION_CITED_HOURS = 24       # a message cited by id

    def delegated_identity(c, who, ref):
        """The person BotOps is acting for: the one whose direct chat message to BotOps started this turn
        (`ref` is "turn"), or whose message it cites by id.

        "Set the scribe's reports to Ben and move it there": a person's instruction to BotOps is enough;
        they should not have to click it themselves. The change is checked with that person's own
        permissions (no more), and recorded as theirs, "via BotOps". It never comes from a message a bot
        wrote, from one the Assistant wrote for a person (`refs.via`), from text inside a task or a
        document, or from a message more than a week old. What always needs the person's own click is
        proposed instead (`propose_card`).
        """
        if who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "Only BotOps applies changes on a person's behalf", 403)
        message_id = ref
        explicit = bool(ref) and ref != "turn"
        turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                         (who.attempt_id,)).fetchone() if who.attempt_id else None
        if not explicit:
            if not turn:
                raise Problem("on_behalf_of", "BotOps acts for a person only in a turn a person's chat message started", 403)
            message_id = turn["message_id"]
        msg = H.message(c, message_id)
        started = (H.message(c, turn["message_id"]) or {}).get("from_actor", "") if turn else ""
        if explicit and str(started).startswith("human:") and msg and msg["from_actor"] != started:
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
        if refs.get("slack") or refs.get("routing") or c.execute(
                "SELECT 1 FROM slack_threads WHERE conversation_id=?", (msg["conversation_id"],)).fetchone():
            # Anyone in a Slack thread can put words in a routed message; only a person's own message in Tico counts.
            raise Problem("on_behalf_of", "A Slack message is not a request to BotOps; ask in the chat room with BotOps", 403)
        if explicit:
            # A message cited by id: the person's own, in their own room with BotOps (not a room another person
            # spoke in), and recent.
            if set(conversation.get("participants") or []) != {msg["from_actor"], "bot:" + BOTOPS}:
                raise Problem("on_behalf_of", "Cite a message from the person's own chat with BotOps", 403)
            if msg["created"] < H.shift(H.now(), hours=-DELEGATION_CITED_HOURS):
                raise Problem("on_behalf_of", "That request is more than a day old; ask the person again", 403)
        elif msg["created"] < H.shift(H.now(), days=-DELEGATION_DAYS):
            raise Problem("on_behalf_of", "That request is more than a week old; ask the person again", 403)
        # Only a request in a conversation this run may read: otherwise any message a bot sends
        # BotOps could borrow the rights of whoever last asked it for something.
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("on_behalf_of", "Cite a request from the conversation you are working on", 403) from None
        person = auth.identity_for_actor(c, msg["from_actor"])
        H.VIA.set("botops")           # every event and history row from here says "via BotOps"
        return replace(person, via="botops")

    def act_for_requester(request, ref):
        """`(card, None)` when this request is one the person confirms with their own click, `(None, person)` when it
        runs at once as them (checked with their rights, recorded via BotOps), else a refusal."""
        who = request.state.identity
        if who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "Only BotOps acts on a person's behalf", 403)
        body = botops_act.parse_body(getattr(request, "_body", b"")) if request.method != "GET" else None
        with store.read() as c:
            rules = team_rules.load(c) if request.method != "GET" else None
            kind = botops_act.classify(request.method, request.url.path, body, rules,
                                       lambda name: str(H.resolve_actor(c, name) or "").startswith("bot:"))
            limits = botops_act.LIMITS.fullmatch(botops_act.normalize(request.url.path) or "")
            if kind == "confirm" and request.method == "PUT" and limits and usage_limits.only_lowers(
                    c, (limits.group(1) or "")[1:], body):
                kind = "do"            # a spending limit that goes down never needs a click
        if kind is None:
            raise Problem("not_delegable", botops_act.DETAIL_ROUTE, 403)
        if botops_act.secret_in(body):
            raise Problem("secret_in_request", botops_act.DETAIL_SECRET, 422)
        if kind == "confirm":
            with store.transaction() as c:
                person = delegated_identity(c, who, ref)
                path = botops_act.normalize(request.url.path)
                if botops_act.admin_only(request.method, path) and not (person.role == "owner" or auth.bot_admin(person)):
                    raise Problem("forbidden", "Only an owner or an admin can do that", 403)
                what = describe_action(c, request.method, path, body or {})[0]
                return JSONResponse(propose_card(c, person, request.method, path, body or {}, what)), None
        with store.read() as c:
            return None, delegated_identity(c, who, ref)

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
            if body.state == "active" and (H.bot(c, bot) or {}).get("state") == "quarantined":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            if body.task_id:
                auth.task(c, who, body.task_id)
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
            return H.answers_to(c, mids)

    @app.post("/api/v2/messages/{mid}/ack")
    def acknowledge(request: Request, mid: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            msg = H.message(c, mid)
            if not msg or msg["to_actor"] != who.actor:
                raise Problem("forbidden", "You may acknowledge only messages addressed to you", 403)
            auth.conversation(c, who, msg["conversation_id"])
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
            return H.status_history(c, bot, since=since)

    @app.get("/api/v2/bots/{bot}/turns")
    def turns(request: Request, bot: str, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            auth.require_read(c, who, bot)
            result = []
            for turn in H.turns(c, bot, since=since):
                msg = H.message(c, turn["message_id"])
                if not msg:
                    continue
                try:
                    auth.conversation(c, who, msg["conversation_id"])
                except Problem:
                    continue
                result.append(turn)
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
        return runner_versions.desired()

    @app.get("/api/v2/runners/assignments")
    def assigned(request: Request):
        with store.read() as c:
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
                value["config"] = Providers.fill(company, json.loads(value.pop("config_json")))
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
            c, request.state.identity, rid, body.runtime, body.profile))

    @app.get("/api/v2/runners/{rid}/logins/{lid}")
    def read_login(request: Request, rid: str, lid: str):
        # A read that also expires: a sign-in nobody finished does not stay open.
        with store.transaction() as c:
            return model_login.read(c, request.state.identity, rid, lid)

    @app.post("/api/v2/runners/{rid}/logins/{lid}/code")
    def login_code(request: Request, rid: str, lid: str, body: M.LoginCode):
        return mutate(request, body, lambda c: model_login.submit_code(
            c, request.state.identity, rid, lid, body.code))

    @app.post("/api/v2/runners/{rid}/logins/{lid}/cancel")
    def cancel_login(request: Request, rid: str, lid: str, body: M.Empty):
        return mutate(request, body, lambda c: model_login.cancel(c, request.state.identity, rid, lid))

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
            return fleet_check_module.check(c, who, auth, settings)

    @app.get("/api/v2/computers")
    def computers(request: Request):
        """The computers this person may put a bot on, with what runs on each: owners and admins see every one, a
        member their own and the ones opened to members' bots."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            online = {r["id"] for r in _online_runners(c)}
            everyone = who.role == "owner" or auth.bot_admin(who)
            mine = H.actor_id(who.actor)
            rows = []
            for r in c.execute("SELECT * FROM runners WHERE revoked_at IS NULL ORDER BY label"):
                if not (everyone or r["operator"] == mine or r["accepts_member_bots"]):
                    continue
                bots = [x["bot"] for x in c.execute("SELECT bot FROM assignments WHERE runner_id=? ORDER BY bot", (r["id"],))]
                rows.append({"id": r["id"], "label": r["label"], "online": r["id"] in online, "platform": r["platform"] or "",
                             "operator": r["operator"], "accepts_member_bots": bool(r["accepts_member_bots"]), "bots": bots})
            return {"computers": rows}

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
            placed = place_now(c, who, bot, body.computer)
            if placed.get("proposed"):
                return placed                     # a computer that needs the person's own click: nothing else yet
            row = H.bot(c, bot)
            if row["state"] == "quarantined":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
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
            armed = onboarding.arm_first_routine(c, who, bot)
            return {"bot": bot, "state": H.bot(c, bot)["state"], "computer": placed["computer"], "placed": placed["placed"],
                    "activated": activated, "setup_started": setup, "routine_armed": armed}
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
            settings_admin._manager(c, who, bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            issued = agents.issue_credential(c, who, bot)
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

    # A person's Grok Bots, synced in by a routine on their own Grok account with their own
    # token: created under them on the chart, transcripts copied, instructions kept.
    @app.post("/api/v2/grokbot/sync")
    def grokbot_sync(request: Request, body: grokbot.GrokSync):
        # Images are fetched before the write, never while holding it.
        grokbot.allowed(auth, request.state.identity)
        images = grokbot.prefetch_images(store, app.state.blobs, body)
        return mutate(request, body, lambda c: grokbot.sync(c, auth, settings_admin,
                                                            request.state.identity, body, images))

    @app.post("/api/v2/agents/heartbeat")
    def agent_heartbeat(request: Request, body: M.AgentHeartbeat):
        return mutate(request, body, lambda c: agents.heartbeat(c, request.state.identity, body))

    @app.get("/api/v2/agents/setup-script", response_class=PlainTextResponse)
    def agent_setup_script(request: Request):
        """The one-file installer the agent's box downloads (clients/hermes_agent.py). Any
        credential may fetch it; it holds no secret."""
        return (Path(__file__).resolve().parents[1] / "clients" / "hermes_agent.py").read_text()

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
            return settings_admin.register(c, who, body)
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
                return {"bot": bot, "model": choice["id"], "runtime": runtime, "harness": harness,
                        "effort": effort, "revision": row["revision"], "sessions_reset": 0}
            before = settings_admin.snapshot(c, bot, "model")
            execution.expire(c)
            active = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (bot,)).fetchone()
            if active:
                raise Problem("busy", "Wait for the current run to finish before changing models", 409)
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
                    "effort": effort, "revision": row["revision"] + 1, "sessions_reset": reset}
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
            if body.action == "resume":
                placing.auto_place(c, execution, bot, who.actor)
            return {"bot": bot, "action": body.action, "revision": config["revision"] + 1}
        return mutate(request, body, work)

    @app.post("/api/v2/jobs/claim")
    def claim(request: Request, body: M.Claim):
        # A read first: an idle runner asks every fraction of a second and must not take the write lock.
        with store.read() as c:
            idle = execution.idle_claim(c, request.state.identity, body,
                                        request.headers.get("idempotency-key"))
        if idle is not None:
            return idle
        return mutate(request, body, lambda c: execution.claim(c, request.state.identity, body))

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

    @app.get("/api/v2/conversations/{cid}/stream")
    async def stream(request: Request, cid: str, after: int = 0):
        who = request.state.identity
        def allowed():
            with store.read() as c:
                auth.conversation(c, who, cid)
        await asyncio.to_thread(allowed)
        # The reads run on a worker thread: on the event loop, every open stream held up every
        # other request for its query once a second.
        def poll(cursor):
            auth.authenticate(request.headers)
            with store.read() as c:
                conv = auth.conversation(c, who, cid)
                # The run's output is the bot's activity (Read); the messages are the conversation.
                bot = rooms.room_bot(conv)
                if bot and not auth.bot_access(c, who, bot)["read"]:
                    return [], message_page(c, cid)["messages"]
                rows = c.execute("SELECT e.* FROM attempt_events e JOIN attempts a ON a.id=e.attempt_id "
                                 "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                                 "WHERE m.conversation_id=? AND e.id>? ORDER BY e.id LIMIT 200",
                                 (cid, cursor)).fetchall()
                return [dict(r) for r in rows], message_page(c, cid)["messages"]
        async def generate():
            cursor = max(after, 0)
            # Bounded connection lifetime ensures periodic reauthentication.
            for _ in range(55):
                if await request.is_disconnected():
                    return
                rows, messages_now = await asyncio.to_thread(poll, cursor)
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: output\ndata: {encode(row)}\n\n"
                yield f"event: messages\ndata: {encode(messages_now)}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    from .views import install_views
    install_views(app, store, auth, mutate, task_view)
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
    from .meeting_items import install_meeting_items
    install_meeting_items(app, store, mutate, task_create)
    from .connectors import install_connectors
    install_connectors(app, store, execution, mutate)
    from .imports import install_imports
    install_imports(app, store, auth, execution, mutate)
    from .credentials import install_credentials
    install_credentials(app, store, delegate=delegated_identity, propose=propose_card)
    install_credential_cards(app, store, app.state.vault, auth, BOTOPS, delegated_identity, settings_admin._manager)
    from .mentions import install_mentions
    install_mentions(app, store, mutate)
    from .sql import install_sql
    install_sql(app, store, auth)
    from .judge import install_judge
    install_judge(app, store, auth)
    from .mail import install_mail
    install_mail(app, store, auth)
    from .messaging import install_messaging
    install_messaging(app, store, auth)
    from .integrations import install_integrations
    install_integrations(app, store, auth, mutate)
    from .mcp import install_mcp
    install_mcp(app, settings)
    from .github import install_github
    install_github(app, settings, store)
    # GitHub App per company: manifest setup, installation tokens for runners (docs/github-app.md).
    from .github_app import install_github_app
    install_github_app(app, settings, store)
    # Slack tokens for the gateway container/process: sealed here, opened only there (docs/slack.md).
    from .slack_app import install_slack_app
    install_slack_app(app, settings, store)
    from .directory import install_directory
    install_directory(app, settings, store, mutate)
    from .scim import install_scim
    install_scim(app, settings, store)
    from .getting_started import install as install_getting_started
    install_getting_started(app, store, auth, mutate, settings)
    from .health import install as install_health
    install_health(app, store, auth, settings)
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
                if "gzip" in request.headers.get("accept-encoding", ""):
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

    app.mount("/tico/ui", StaticFiles(directory=settings.ui_dir, html=True), name="ui-prefixed")
    app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    if settings.demo:
        from . import demo
        demo.install(app, auth, settings)
    from . import cors
    cors.install(app, settings)          # last, so it is outermost: a preflight is answered before sign-in
    return app
