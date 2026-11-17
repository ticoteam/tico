"""The /api/v2 routes for the switch, audiences, disclosure and the stats board. Owners and admins read and write, because
every one of these shows what a company keeps back. A bot is refused; BotOps comes through as the person it acts for, so
it can do what they can and no more."""

import asyncio
import base64
import hashlib
import json
from typing import Any

from fastapi import Request
from pydantic import Field
from starlette.responses import HTMLResponse, Response

from .. import blobs
from .. import hubdb as H
from ..models import Contract
from ..store import Problem
from . import audiences as A
from . import disclosure as D
from . import settings as S
from . import stats as ST
from ..api_contract import api_v2

PAGE_BYTES = 256 * 1024
PAGE_MOST = 200
# The preview is served on the app's own origin, so it runs sandboxed: an opaque origin can't touch the app's session.
PREVIEW_POLICY = (
    "default-src 'none'; img-src data: https:; font-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts"
)


class PageSettings(Contract):
    transparency: str | None = Field(default=None, max_length=20)
    slug: str | None = Field(default=None, max_length=63)
    accent: str | None = Field(default=None, max_length=7)
    logo_file_id: str | None = Field(default=None, max_length=64)


class AudienceCreate(Contract):
    name: str = Field(min_length=1, max_length=A.NAME_MAX)
    cadence: str | None = Field(default=None, max_length=20)
    due_rule: dict[str, Any] | None = None
    lag_days: int | None = Field(default=None, ge=0, le=365)
    approver: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class AudiencePatch(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=A.NAME_MAX)
    cadence: str | None = Field(default=None, max_length=20)
    due_rule: dict[str, Any] | None = None
    lag_days: int | None = Field(default=None, ge=0, le=365)
    approver: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class Rule(Contract):
    data_class: str = Field(max_length=40)
    ref_id: str = Field(default="", max_length=D.REF_MAX)
    mode: str = Field(max_length=20)
    lag_days: int = Field(default=0, ge=0, le=D.LAG_MAX)
    rounding: str | None = Field(default=None, max_length=60)


class Rules(Contract):
    rules: list[Rule] = Field(min_length=1, max_length=500)


class Preset(Contract):
    name: str = Field(max_length=20)


class Source(Contract):
    author: str = Field(min_length=1, max_length=200)


class StatCreate(Contract):
    source_kind: str = Field(max_length=20)
    ref_id: str = Field(min_length=1, max_length=200)
    data_class: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=ST.LABEL_MAX)
    definition: str | None = Field(default=None, max_length=ST.DEFINITION_MAX)
    chart: str = Field(default="number", max_length=10)
    position: int | None = Field(default=None, ge=0, le=100000)


class StatPatch(Contract):
    data_class: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=ST.LABEL_MAX)
    definition: str | None = Field(default=None, max_length=ST.DEFINITION_MAX)
    chart: str | None = Field(default=None, max_length=10)
    position: int | None = Field(default=None, ge=0, le=100000)


class Empty(Contract):
    pass


class LogoBody(Contract):
    """What a logo upload is recorded as: its digest, so the same key sent with other bytes is not replayed as the first."""

    digest: str = Field(max_length=64)


LOGO_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def logo_type(data):
    """PNG, JPEG or WebP by its first bytes, never by the name or type the client sent. SVG is refused: it can run script
    when a browser opens it."""
    from . import release

    if len(data) > release.MAX_LOGO_BYTES:
        raise Problem("too_large", "The logo must be 2 MB or smaller", 413)
    for mime, (_, magic) in release.LOGO_TYPES.items():
        if data.startswith(magic) and (mime != "image/webp" or data[8:12] == b"WEBP"):
            return mime
    raise Problem("logo_type", "Use a PNG, JPEG or WebP image", 422)


def page(items, key, limit, offset):
    """A page of a list, cut at PAGE_MOST rows or PAGE_BYTES, whichever comes first, and where to go next."""
    limit, offset = max(1, min(int(limit), PAGE_MOST)), max(0, int(offset))
    out, used = [], 0
    for item in items[offset : offset + limit]:
        used += len(json.dumps(item, default=str).encode())
        if out and used > PAGE_BYTES:
            break
        out.append(item)
    following = offset + len(out)
    return {key: out, "next_offset": following if following < len(items) else None}


def preview_view(c, settings, audience, at):
    """The view model for an audience as it stands, approved or not, so the owner sees what approving would publish. It is
    built by the same code the page is, so the two can't differ; the logo is inlined, since the preview has no release
    folder to load it from."""
    from . import editions, release

    badge = "private" if audience["kind"] != "public" else "open"
    view = release.view_model(
        c,
        audience,
        ST.project(c, audience["id"], at)["stats"],
        editions.stream(c, [audience["id"]]),
        None,
        badge,
        settings,
    )
    if view["company"]["logo_url"]:
        data = release._logo_bytes(c, settings, view)
        mime = {ext: mime for mime, (ext, _) in release.LOGO_TYPES.items()}[
            view["company"]["logo_url"].rsplit(".", 1)[1]
        ]
        view["company"]["logo_url"] = f"data:{mime};base64,{base64.b64encode(data).decode()}" if data else ""
    return view


def next_due(c, audiences):
    """Each audience with the day its next edition comes due, in the company's zone. None where the schedule won't write
    one: switched off, the page Closed, no sources, or Public while the page isn't Open."""
    from . import schedule

    shown = S.get(c)["transparency"]
    sourced = {r[0] for r in c.execute("SELECT DISTINCT audience_id FROM audience_sources")}
    zone, at = schedule.company_zone(), H.parse_ts(H.now())

    def due(audience):
        if (
            not audience["enabled"]
            or shown == "closed"
            or audience["id"] not in sourced
            or (audience["kind"] == "public" and shown != "open")
        ):
            return None
        moment = schedule.next_due(audience, at, zone)
        return moment.astimezone(zone).date().isoformat() if moment else None

    return [{**a, "next_due": due(a)} for a in audiences]


# The logo travels as its raw bytes, not JSON, and the preview is a page of HTML.
LOGO_CONTENT = {
    mime: {"schema": {"type": "string", "format": "binary"}}
    for mime in ("image/png", "image/jpeg", "image/webp")  # release.LOGO_TYPES, imported only when used
}


def register(app, store, auth, mutate, settings):
    seeded = {"done": False}

    def gate(request):
        """Owners and admins, on any route here. A runner, a service key, a member and every bot stop at this line."""
        who = request.state.identity
        auth.domain(who)
        if who.role == "bot":
            raise Problem("forbidden", "Bots don't change or read the Company page's rules", 403)
        if auth.company_role(who) not in ("owner", "admin"):
            raise Problem("forbidden", "Only owners and admins manage the Company page", 403)
        if not seeded["done"]:
            with store.transaction() as c:
                A.seed(c)
            seeded["done"] = True
        return who

    def write(request, body, work):
        """Through `mutate`, so Idempotency-Key, a retry's saved answer and the write checks all hold."""
        who = gate(request)
        return mutate(request, body, lambda c: work(c, who.actor))

    def page_view(c):
        from datetime import datetime, timezone
        from . import release

        current = S.get(c)
        # The reader address answers with a page only when something public is approved and a release is served.
        live = (
            bool(release.public_view(c, datetime.now(timezone.utc), settings))
            and release.current_dir(settings) is not None
        )
        return {
            **current,
            "company_name": settings.company_name,
            "reader_origin": settings.reader_origin,
            "page_domain": settings.page_domain,
            "address": S.address(current["slug"], settings),
            "public_live": live,
        }

    @app.get(
        "/api/v2/company-page",
        **api_v2("Company page", "getCompanyPage", "The page's settings, its address and whether it is live"),
    )
    def company_page_show(request: Request):
        gate(request)
        with store.read() as c:
            return page_view(c)

    @app.patch(
        "/api/v2/company-page",
        **api_v2(
            "Company page",
            "updateCompanyPage",
            "Change the page's transparency, address or accent; the owner's own tap when BotOps asks",
        ),
    )
    def company_page_patch(request: Request, body: PageSettings):
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            raise Problem("fields", "Say what to change", 422)
        if "transparency" in fields and fields["transparency"] is None:
            raise Problem("transparency", f"Transparency is {', '.join(S.TRANSPARENCY)}", 422)

        def work(c, actor):
            current = S.get(c)
            slug = fields.get("slug", current["slug"])
            # Open or Private with no address would publish to nowhere and look as if it had worked.
            if fields.get("transparency", current["transparency"]) in ("private", "open") and not S.address(
                slug, settings
            ):
                raise Problem("address", "Set the page's address first", 422)
            S.update(c, actor, **fields)
            return page_view(c)

        return write(request, body, work)

    # The logo is the one picture the page sends out, so it is uploaded here and kept apart from the library's files.
    @app.get(
        "/api/v2/company-page/logo",
        **api_v2(
            "Company page",
            "getCompanyLogo",
            "The logo's bytes, with its type",
            answer={"description": "The logo's bytes, sent as their image type", "content": LOGO_CONTENT},
        ),
    )
    def company_page_logo_show(request: Request):
        gate(request)
        with store.read() as c:
            row = c.execute(
                "SELECT b.digest, b.content_type FROM company_page_settings s JOIN blobs b ON b.id=s.logo_file_id "
                "WHERE s.id='company'"
            ).fetchone()
        if not row:
            raise Problem("not_found", "No logo is set", 404)
        headers = {
            "ETag": '"' + row[0] + '"',
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        }
        return Response(app.state.blobs.get(row[0]), media_type=row[1], headers=headers)

    @app.post(
        "/api/v2/company-page/logo",
        **api_v2(
            "Company page",
            "setCompanyLogo",
            "Upload the logo as the raw image body: PNG, JPEG, GIF or WebP, up to 2 MB",
            request_body={
                "required": True,
                "description": "The image itself, up to 2 MB. SVG is refused.",
                "content": LOGO_CONTENT,
            },
        ),
    )
    async def company_page_logo_set(request: Request):
        data = await request.body()

        def run():
            who = gate(request)
            mime = logo_type(data)
            digest = hashlib.sha256(data).hexdigest()

            def save(c):
                # The bytes are stored before the row names them, so the page never points at a file that isn't there.
                app.state.blobs.put(data, mime)
                picture = blobs.register(c, who, digest, len(data), S.LOGO_NAME + "." + LOGO_EXT[mime], mime)
                S.update(c, who.actor, logo_file_id=picture["id"])
                return page_view(c)

            return mutate(request, LogoBody(digest=digest), save)

        return await asyncio.to_thread(run)

    @app.delete(
        "/api/v2/company-page/logo", **api_v2("Company page", "removeCompanyLogo", "Take the logo off the page")
    )
    def company_page_logo_remove(request: Request):
        def work(c, actor):
            S.update(c, actor, logo_file_id=None)
            return page_view(c)

        return write(request, Empty(), work)

    @app.get(
        "/api/v2/company-page/preview",
        **api_v2(
            "Company page",
            "getCompanyPreview",
            "The page as it would read for one audience, as HTML that is never kept",
            answer={
                "description": "The page as HTML for one audience, never kept",
                "content": {"text/html": {"schema": {"type": "string"}}},
            },
        ),
    )
    def company_page_preview(request: Request, audience: str = "", theme: str = ""):
        gate(request)
        from . import render

        with store.read() as c:
            found = A.get(c, audience)
            if not found:
                raise Problem("not_found", "No such audience", 404)
            html = render.page(preview_view(c, settings, found, H.now()), preview=True, theme=theme)
        return HTMLResponse(html, headers={"Content-Security-Policy": PREVIEW_POLICY, "Cache-Control": "no-store"})

    # ------------------------------------------------------------------ audiences
    @app.get(
        "/api/v2/audiences",
        **api_v2("Company page", "listAudiences", "The audiences, paged, each with its next due date"),
    )
    def audiences_list(request: Request, limit: int = 100, offset: int = 0):
        gate(request)
        with store.read() as c:
            found = page(A.list_all(c), "audiences", limit, offset)
            return {**found, "audiences": next_due(c, found["audiences"])}

    @app.post(
        "/api/v2/audiences",
        **api_v2(
            "Company page", "createAudience", "Add an audience (the board, investors, the public or a custom one)"
        ),
    )
    def audiences_create(request: Request, body: AudienceCreate):
        fields = body.model_dump(exclude_unset=True)
        name = fields.pop("name")
        return write(request, body, lambda c, actor: A.create(c, actor, name, **fields))

    @app.patch(
        "/api/v2/audiences/{audience_id}",
        **api_v2(
            "Company page",
            "updateAudience",
            "Change an audience's name, cadence, due rule, lag, approver or whether it is on",
        ),
    )
    def audiences_patch(request: Request, audience_id: str, body: AudiencePatch):
        fields = body.model_dump(exclude_unset=True)
        return write(request, body, lambda c, actor: A.update(c, actor, audience_id, **fields))

    @app.delete(
        "/api/v2/audiences/{audience_id}",
        **api_v2(
            "Company page",
            "deleteAudience",
            "Delete an audience and what it reaches; the owner's own tap when BotOps asks",
        ),
    )
    def audiences_delete(request: Request, audience_id: str):
        return write(request, Empty(), lambda c, actor: A.delete(c, actor, audience_id))

    # ------------------------------------------------------------------ disclosure
    def grid(c, audience_id):
        A.need(c, audience_id)
        return {
            "rules": D.rules(c, audience_id),
            "revision": D.current_revision(c, audience_id),
            "data_classes": list(D.DATA_CLASSES),
            "modes": list(D.MODES),
            "presets": list(D.PRESETS),
        }

    @app.get(
        "/api/v2/audiences/{audience_id}/disclosure",
        **api_v2(
            "Company page",
            "getAudienceDisclosure",
            "The disclosure grid for one audience: each data class, the stat it shows and how",
        ),
    )
    def disclosure_show(request: Request, audience_id: str):
        gate(request)
        with store.read() as c:
            return grid(c, audience_id)

    def tightened(c, audience_id, change):
        """A tightening leaves published editions up: they are dated statements a person approved, and Retract or Closed
        take them back. So the response says how many there are, rather than changing the page in silence."""
        if not change["tightened"]:
            return {}
        count = c.execute(
            "SELECT count(*) FROM editions WHERE audience_id=? AND status='published' AND retracted_at IS NULL",
            (audience_id,),
        ).fetchone()[0]
        return {"published_editions": count}

    @app.put(
        "/api/v2/audiences/{audience_id}/disclosure",
        **api_v2(
            "Company page",
            "setAudienceDisclosure",
            "Replace the disclosure rules for one audience; the owner's own tap when BotOps asks",
        ),
    )
    def disclosure_put(request: Request, audience_id: str, body: Rules):
        rules = [r.model_dump() for r in body.rules]

        def work(c, actor):
            change = D.set_rules(c, actor, audience_id, rules)
            return {**grid(c, audience_id), **tightened(c, audience_id, change)}

        return write(request, body, work)

    @app.post(
        "/api/v2/audiences/{audience_id}/disclosure/preset",
        **api_v2("Company page", "applyDisclosurePreset", "Apply a preset to one audience's disclosure grid"),
    )
    def disclosure_preset(request: Request, audience_id: str, body: Preset):
        def work(c, actor):
            change = D.apply_preset(c, actor, audience_id, body.name)
            return {**grid(c, audience_id), **tightened(c, audience_id, change)}

        return write(request, body, work)

    # ------------------------------------------------------------------ sources
    @app.get(
        "/api/v2/audiences/{audience_id}/sources",
        **api_v2("Company page", "listAudienceSources", "The authors whose updates feed one audience"),
    )
    def sources_list(request: Request, audience_id: str, limit: int = 100, offset: int = 0):
        gate(request)
        with store.read() as c:
            A.need(c, audience_id)
            return page(A.sources(c, audience_id), "sources", limit, offset)

    @app.post(
        "/api/v2/audiences/{audience_id}/sources",
        **api_v2("Company page", "addAudienceSource", "Name an author whose updates feed one audience"),
    )
    def sources_add(request: Request, audience_id: str, body: Source):
        return write(request, body, lambda c, actor: A.add_source(c, actor, audience_id, body.author))

    @app.delete(
        "/api/v2/audiences/{audience_id}/sources/{source_id}",
        **api_v2("Company page", "removeAudienceSource", "Stop an author feeding one audience"),
    )
    def sources_remove(request: Request, audience_id: str, source_id: str):
        return write(request, Empty(), lambda c, actor: A.remove_source(c, actor, audience_id, source_id))

    # ------------------------------------------------------------------ the stats board
    @app.get(
        "/api/v2/company-page/stats", **api_v2("Company page", "listPageStats", "The stats the page can show, in order")
    )
    def stats_list(request: Request, limit: int = 100, offset: int = 0):
        gate(request)
        with store.read() as c:
            return {**page(ST.list_stats(c), "stats", limit, offset), "available": ST.available(c)}

    @app.post(
        "/api/v2/company-page/stats",
        **api_v2("Company page", "createPageStat", "Add a stat from a Finance or KPI source"),
    )
    def stats_create(request: Request, body: StatCreate):
        fields = body.model_dump()
        return write(request, body, lambda c, actor: ST.create_stat(c, actor, **fields))

    @app.patch(
        "/api/v2/company-page/stats/{stat_id}",
        **api_v2("Company page", "updatePageStat", "Change a stat's label, definition, chart or place"),
    )
    def stats_patch(request: Request, stat_id: str, body: StatPatch):
        fields = body.model_dump(exclude_unset=True)
        return write(request, body, lambda c, actor: ST.update_stat(c, actor, stat_id, **fields))

    @app.delete(
        "/api/v2/company-page/stats/{stat_id}",
        **api_v2("Company page", "deletePageStat", "Remove a stat from the page"),
    )
    def stats_delete(request: Request, stat_id: str):
        return write(request, Empty(), lambda c, actor: ST.delete_stat(c, actor, stat_id))

    @app.get(
        "/api/v2/audiences/{audience_id}/stats",
        **api_v2(
            "Company page",
            "getAudienceStats",
            "The stats one audience sees, with the values the disclosure grid lets through",
        ),
    )
    def audience_stats(request: Request, audience_id: str, limit: int = 100, offset: int = 0):
        """The projection for this audience, what is held and why, and what is absent."""
        gate(request)
        with store.read() as c:
            A.need(c, audience_id)
            found = ST.board(c, audience_id, H.now())
        return {**found, **page(found["stats"], "stats", limit, offset)}
