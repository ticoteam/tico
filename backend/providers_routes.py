"""Provider and gateway routes, and the system update route."""

from .store import H
from fastapi.responses import JSONResponse
from . import models as M
from .store import Problem
from . import providers as Providers
from fastapi import Request
from . import cloud_access
from .execution import flag_cut
from clients import gateway as gateway_client
from . import gateway_connection
from . import gateway_page
from . import gateway_tiers
import json
from . import model_login
from . import releases
import threading


def register(app, auth, mutate, settings, store, telemetry, updater_only, GATEWAY_IMAGE_SLOTS):
    @app.post("/api/v2/system/update")
    def system_update(request: Request, body: M.SystemUpdate):
        who = request.state.identity
        updater_only(who, "updates the installation")
        result = releases.start(body.version)
        target = str(body.version).lstrip("v")
        with store.transaction() as c:
            record = {"actor": who.actor, "from": releases.version(), "to": target}
            c.execute(
                "INSERT INTO registry_metadata VALUES('update-pending',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(record),),
            )
            H.event(c, who.actor, "system.update.started", target, {"from": record["from"], "to": target})
            flag_cut(c, "update", target)  # the runs it cuts resume after it (execution.cut_by_update)
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
            model_login.hold_removals(c, settings)  # bots on the company's default may now run another tool
            H.event(
                c,
                who.actor,
                "providers.updated",
                "",
                {
                    "before": {
                        k: before[k]
                        for k in (
                            "enabled",
                            "runtime",
                            "model",
                            "revision",
                            "auto_fallback",
                            "check_models",
                            "preset",
                            "decide_level",
                            "meeting_model",
                        )
                    },
                    "after": {
                        k: after[k]
                        for k in (
                            "enabled",
                            "runtime",
                            "model",
                            "revision",
                            "auto_fallback",
                            "check_models",
                            "preset",
                            "decide_level",
                            "meeting_model",
                        )
                    },
                },
            )
            return Providers.view(after)

        return mutate(request, body, work)

    @app.get("/api/v2/providers/gateway")
    def gateway_view(request: Request):
        """The Tico AI Gateway connection: whether it is set and where from. Its preview and the Gateway's last word on
        the key (working, revoked, the month's cap) only to the owner, who manages it. Never the key."""
        who = request.state.identity
        auth.domain(who)
        if who.role == "owner":
            gateway_connection.check_soon(store, app.state.vault, settings)  # the page's answer is the last check's
        with store.read() as c:
            return gateway_connection.view(c, settings, manage=who.role == "owner")

    @app.get("/api/v2/gateway")
    def gateway_overview(request: Request):
        """The Tico Gateway card on Settings > Models: the connection (with spend against the cap for the owner), the picks the bots use, the
        models and the services."""
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
            header = (
                gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
            )
        if not key:
            raise Problem(
                "gateway_not_connected",
                "The Tico AI Gateway is not connected; the owner saves a gateway key in Settings > Computers",
                409,
            )
        status, answer, code = gateway_client.search(base, key, body.query, body.max_results, header)
        if status != 200:
            if status == 404:
                raise Problem("gateway_search_unavailable", "The Tico AI Gateway does not offer search yet", 503)
            raise Problem(
                "gateway_search_failed",
                f"Search failed: {code or status}",
                503 if status in (0, 429) or status >= 500 else 422,
            )
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
            header = (
                gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
            )
        if not key:
            raise Problem(
                "gateway_not_connected",
                "The Tico AI Gateway is not connected; the owner saves a gateway key in Settings > Computers",
                409,
            )
        status, answer, code = gateway_client.fetch(base, key, body.url, body.max_tokens, header)
        if status != 200:
            if status == 404:
                raise Problem("gateway_fetch_unavailable", "The Tico AI Gateway does not offer fetch yet", 503)
            raise Problem(
                "gateway_fetch_failed",
                f"Fetch failed: {code or status}",
                503 if status in (0, 429) or status >= 500 else 422,
            )
        return answer

    @app.post("/api/v2/gateway/digest")
    def gateway_digest(request: Request, body: M.GatewayDigest):
        """Group many items into one digest through the Tico AI Gateway (`hub_gateway_digest`), asked with the company's
        gateway key and, for a bot, as that bot. Whoever may call the API may ask; the Gateway meters it."""
        who = request.state.identity
        auth.domain(who)
        if settings.rehearsal:
            raise Problem("rehearsal", "Digest is off in rehearsal mode; nothing is sent to the Gateway", 503)
        with store.read() as c:
            key, base, _ = gateway_connection.target(c, app.state.vault, settings)
            header = (
                gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
            )
        if not key:
            raise Problem(
                "gateway_not_connected",
                "The Tico AI Gateway is not connected; the owner saves a gateway key in Settings > Computers",
                409,
            )
        status, answer, code = gateway_client.digest(base, key, body.items, body.focus, body.style, header)
        if status != 200:
            if status == 404:
                raise Problem("gateway_digest_unavailable", "The Tico AI Gateway does not offer digest yet", 503)
            raise Problem(
                "gateway_digest_failed",
                f"Digest failed: {code or status}",
                503 if status in (0, 429) or status >= 500 else 422,
            )
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
            header = (
                gateway_connection.bot_header(c, app.state.vault, H.actor_id(who.actor)) if H.is_bot(who.actor) else {}
            )
        if not key:
            raise Problem(
                "gateway_not_connected",
                "The Tico AI Gateway is not connected; the owner saves a gateway key in Settings > Computers",
                409,
            )
        cut = None
        try:  # a fault in Finance refuses nothing (backend/finance/limits.py)
            from .finance import reads as finance_reads

            with store.read() as c:
                cut = finance_reads.video_cutoff(c, who.actor)
        except Exception as exc:  # noqa: BLE001  a fault in Finance refuses nothing (backend/finance/limits.py)
            telemetry.capture("finance_video_cutoff", exc)
        if cut is not None:
            raise Problem("video_budget_reached", f"{cut.name} budget reached: no new images until it is raised", 409)
        if not app.state.image_slots.acquire(blocking=False):
            return JSONResponse(
                {
                    "error": {
                        "code": "gateway_images_busy",
                        "detail": "Image making is busy; try again shortly",
                        "retryable": True,
                    }
                },
                status_code=429,
                headers={"Retry-After": "10"},
            )
        try:
            status, answer, code = gateway_client.image(
                base, key, body.prompt, body.category, body.images, body.size, body.count, header
            )
        finally:
            app.state.image_slots.release()
        if status != 200:
            if status == 404:
                raise Problem("gateway_image_unavailable", "The Tico AI Gateway does not make images yet", 503)
            raise Problem(
                "gateway_image_failed",
                f"Image failed: {code or status}",
                503 if status in (0, 429) or status >= 500 else 422,
            )
        return answer

    @app.put("/api/v2/providers/gateway")
    def gateway_save(request: Request, body: M.GatewayConnectionSave):
        """Save the gateway key (and URL), then ask the Gateway about it. The owner only; the key is sealed at once and
        is never in a response, an event or the idempotency table (this route keeps no receipt)."""
        who = request.state.identity
        auth.domain(who)
        vault = app.state.vault
        with store.transaction() as c:
            gateway_connection.save(
                c,
                vault,
                who,
                {
                    "key": body.key.get_secret_value() if body.key else "",
                    "url": body.url,
                    "expected_revision": body.expected_revision,
                    "credentials": body.credentials,
                },
                H.now(),
                settings,
            )
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
