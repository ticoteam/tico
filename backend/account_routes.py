"""Account routes: onboarding, local sign-in, sign-out, the manifest, personal tokens, agent skill."""

import json
import re
from clients.agent_skill import WHO_NEEDS_ME
from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from urllib.parse import urlparse
from . import invites, mcp_oauth, models as M, oidc, owner_link, personal_tokens, setup_handoff
from .auth import LOCAL_COOKIE, LOCAL_COOKIE_DAYS, LOCAL_SIGNIN_PATH, LOGOUT_PATH
from .observability import browser_config, staff_display_name
from .store import H, Problem, encode


def register(app, auth, mutate, onboarding, owner_only, recruiter, settings, store):
    @app.get("/api/v2/catalog")
    def catalog():
        """The bot templates this company may pick from, named for this company. Every signed-in
        identity reads it: runners and bots build repositories from the same cards."""
        with store.read() as c:
            return {"cards": onboarding.catalog(c)}

    # The /api/v2/ceo-bot path is the Strategy Owner's old one: clients built before the rename still call it, so it
    # stays a hidden alias of the route under it.
    @app.get("/api/v2/ceo-bot", include_in_schema=False)
    @app.get("/api/v2/strategy-bot")
    def strategy_bot_settings(request: Request):
        """The Strategy Owner's name, level and regular checks (backend/strategy_bot.py). How many items it put in each
        person's Needs you today is the owner's, an admin's and the Strategy Owner's to read; anyone else sees only their
        own count."""
        who = request.state.identity
        auth.domain(who)
        from . import strategy_bot

        with store.read() as c:
            value = strategy_bot.named(c, strategy_bot.view(c))
        if who.actor != "bot:" + strategy_bot.SLUG and auth.company_role(who) not in ("owner", "admin"):
            value["asked_today"] = {k: n for k, n in value["asked_today"].items() if k == who.actor}
        return value

    @app.post("/api/v2/strategy-owner/turn-on")
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

        return mutate(
            request,
            body,
            lambda c: company_import.apply(onboarding, c, request.state.identity, body.company_yaml, body.fingerprint),
        )

    @app.post("/api/v2/onboarding/company/import-failure/dismiss")
    def onboarding_company_import_failure_dismiss(request: Request, body: M.Empty):
        """The owner leaves a kept setup-file failure they won't fix by importing (Health and setup)."""
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
        invites.request_key(request)  # checked before anything is written, as `mutate` checks it
        bound = invites.replay(store, who, request, body)  # a bound key answers before the roster is touched
        if bound is not None:
            with store.transaction() as c:
                # A repeat records a send whose record was lost, unless the person has since been removed.
                if bound not in invites.sent(c) and c.execute("SELECT 1 FROM humans WHERE id=?", (bound,)).fetchone():
                    invites.record(c, who.actor, bound)
                return onboarding.view(c, who)
        # Not `mutate`: the invite binding below is this request's record under the same key (one row per key).
        # add_invitee answers the same roster row again when the person is already added.
        row = store.write(who, lambda c: company_import.add_invitee(onboarding, c, who, key, body.email))
        sent = invites.once(store, auth, settings, who, request, body, row)
        with store.transaction() as c:  # not a second `mutate`: one request has one idempotency key
            if sent or row["id"] not in invites.sent(c):  # a repeat keeps the first send's time
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
        response.set_cookie(
            auth.local_cookie(),
            token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=LOCAL_COOKIE_DAYS * 86400,
            secure=settings.public_url.startswith("https://"),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    oidc.register(app, auth)
    owner_link.register(app, auth)
    setup_handoff.register(app, auth, store, mutate)
    mcp_oauth.register(app, auth, store)

    @app.get(LOGOUT_PATH)
    def logout(request: Request):
        """End the browser session at whichever identity proxy fronts this server."""
        target, expire = auth.proxy.logout(request.headers) if auth.proxy else ("/", [])
        response = RedirectResponse(target, status_code=302)
        for name in expire:
            response.delete_cookie(
                name,
                path="/",
                secure=settings.public_url.startswith("https://") or not settings.loopback,
                httponly=True,
            )
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
        data.update(
            name=settings.app_name,
            short_name=settings.app_name,
            description=settings.app_name + ", " + settings.company_name + "'s AI COO and company hub.",
        )
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
        from .mcp import caller_kind, tool_profile

        who = request.state.identity
        slug = None
        profile = ""
        if who.role == "bot":  # the bot's label beside its id (the actor), for a client that shows it
            with store.read() as c:
                slug = (H.bot(c, H.actor_id(who.actor)) or {}).get("slug")
                profile = tool_profile(c, auth, who)
        return {
            "actor": who.actor,
            "role": who.role,
            "email": who.email,
            "runner_id": who.runner_id,
            "run_id": who.attempt_id,
            "agent": who.agent,
            "kind": caller_kind(auth, who),
            **({"slug": slug} if slug else {}),
            # The tool list this caller's MCP server offers: `developer` for a code bot, `feature` for a Feature Owner
            # (backend/mcp.py tool_profile).
            **({"tool_profile": profile} if profile else {}),
        }

    # A person's own API tokens (backend/personal_tokens.py). Made only from a browser sign-in: a token is that
    # person everywhere else and may list and revoke its person's tokens, but it cannot mint one.
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

    # "Connect an agent" (ui/connect-agent.ts): the MCP address a person's own agent (Grok, Muse,
    # Claude, ...) is given beside a personal token. It is the runner hostname, where a bearer is let
    # through. When that is the one hostname and Cloudflare Access guards it, Access answers an
    # outside agent with its login page unless /api/v2/mcp is bypassed, so the dialog says so
    # (docs/connect-an-agent.md). The text is the MCP instructions too.
    @app.get("/api/v2/agent-skill")
    def agent_skill(request: Request):
        if request.state.identity.role not in ("human", "owner"):
            raise Problem("forbidden", "This endpoint is available only to people", 403)
        from .mcp import PATH as MCP_PATH

        behind_access = (
            settings.proxy_kind == "cloudflare"
            and urlparse(settings.runner_url).hostname == urlparse(settings.public_url).hostname
        )
        return {"mcp_url": settings.runner_url + MCP_PATH, "text": WHO_NEEDS_ME, "access_bypass": behind_access}
