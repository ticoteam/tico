"""The /api/v2 routes for the switch, audiences, disclosure and the stats board. Owners and admins read and write, because
every one of these shows what a company keeps back. A bot is refused; BotOps comes through as the person it acts for, so
it can do what they can and no more."""
import json
from typing import Any

from fastapi import Request
from pydantic import Field
from starlette.responses import HTMLResponse

from .. import hubdb as H
from ..models import Contract
from ..store import Problem
from . import audiences as A
from . import disclosure as D
from . import settings as S
from . import stats as ST

PAGE_BYTES = 256 * 1024
PAGE_MOST = 200
# The preview is served on the app's own origin, so it runs sandboxed: an opaque origin can't touch the app's session.
PREVIEW_POLICY = ("default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                  "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts")


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


def page(items, key, limit, offset):
    """A page of a list, cut at PAGE_MOST rows or PAGE_BYTES, whichever comes first, and where to go next."""
    limit, offset = max(1, min(int(limit), PAGE_MOST)), max(0, int(offset))
    out, used = [], 0
    for item in items[offset:offset + limit]:
        used += len(json.dumps(item, default=str).encode())
        if out and used > PAGE_BYTES:
            break
        out.append(item)
    following = offset + len(out)
    return {key: out, "next_offset": following if following < len(items) else None}


def preview_view(c, settings, audience, at):
    """The view model for an audience as it stands, approved or not, so the owner sees what approving would publish."""
    from . import editions
    current = S.get(c)
    badge = "private" if audience["kind"] != "public" else "open"
    return {"company": {"name": settings.company_name, "logo_url": None, "mission": S.mission_for(c, audience["id"]),
                        "badge": badge, "accent": current["accent"], "updated_at": at},
            "audience": {"kind": audience["kind"], "name": audience["name"]},
            "stats": ST.project(c, audience["id"], at)["stats"],
            "updates": editions.stream(c, [audience["id"]]), "next_before": None}


def next_due(c, audiences):
    """Each audience with the day its next edition comes due, in the company's zone. None where the schedule won't write
    one: switched off, the page Closed, no sources, or Public while the page isn't Open."""
    from . import schedule
    shown = S.get(c)["transparency"]
    sourced = {r[0] for r in c.execute("SELECT DISTINCT audience_id FROM audience_sources")}
    zone, at = schedule.company_zone(), H.parse_ts(H.now())

    def due(audience):
        if (not audience["enabled"] or shown == "closed" or audience["id"] not in sourced
                or (audience["kind"] == "public" and shown != "open")):
            return None
        moment = schedule.next_due(audience, at, zone)
        return moment.astimezone(zone).date().isoformat() if moment else None
    return [{**a, "next_due": due(a)} for a in audiences]


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
        current = S.get(c)
        return {**current, "company_name": settings.company_name, "reader_origin": settings.reader_origin,
                "page_domain": settings.page_domain, "address": S.address(current["slug"], settings)}

    @app.get("/api/v2/company-page")
    def company_page_show(request: Request):
        gate(request)
        with store.read() as c:
            return page_view(c)

    @app.patch("/api/v2/company-page")
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
            if fields.get("transparency", current["transparency"]) in ("private", "open") and not S.address(slug, settings):
                raise Problem("address", "Set the page's address first", 422)
            S.update(c, actor, **fields)
            return page_view(c)
        return write(request, body, work)

    @app.get("/api/v2/company-page/preview")
    def company_page_preview(request: Request, audience: str = ""):
        gate(request)
        from . import render
        with store.read() as c:
            found = A.get(c, audience)
            if not found:
                raise Problem("not_found", "No such audience", 404)
            html = render.page(preview_view(c, settings, found, H.now()))
        return HTMLResponse(html, headers={"Content-Security-Policy": PREVIEW_POLICY, "Cache-Control": "no-store"})

    # ------------------------------------------------------------------ audiences
    @app.get("/api/v2/audiences")
    def audiences_list(request: Request, limit: int = 100, offset: int = 0):
        gate(request)
        with store.read() as c:
            found = page(A.list_all(c), "audiences", limit, offset)
            return {**found, "audiences": next_due(c, found["audiences"])}

    @app.post("/api/v2/audiences")
    def audiences_create(request: Request, body: AudienceCreate):
        fields = body.model_dump(exclude_unset=True)
        name = fields.pop("name")
        return write(request, body, lambda c, actor: A.create(c, actor, name, **fields))

    @app.patch("/api/v2/audiences/{audience_id}")
    def audiences_patch(request: Request, audience_id: str, body: AudiencePatch):
        fields = body.model_dump(exclude_unset=True)
        return write(request, body, lambda c, actor: A.update(c, actor, audience_id, **fields))

    @app.delete("/api/v2/audiences/{audience_id}")
    def audiences_delete(request: Request, audience_id: str):
        return write(request, Empty(), lambda c, actor: A.delete(c, actor, audience_id))

    # ------------------------------------------------------------------ disclosure
    def grid(c, audience_id):
        A.need(c, audience_id)
        return {"rules": D.rules(c, audience_id), "revision": D.current_revision(c, audience_id),
                "data_classes": list(D.DATA_CLASSES), "modes": list(D.MODES), "presets": list(D.PRESETS)}

    @app.get("/api/v2/audiences/{audience_id}/disclosure")
    def disclosure_show(request: Request, audience_id: str):
        gate(request)
        with store.read() as c:
            return grid(c, audience_id)

    def tightened(c, audience_id, change):
        """A tightening leaves published editions up: they are dated statements a person approved, and Retract or Closed
        take them back. So the response says how many there are, rather than changing the page in silence."""
        if not change["tightened"]:
            return {}
        count = c.execute("SELECT count(*) FROM editions WHERE audience_id=? AND status='published' AND retracted_at IS NULL",
                          (audience_id,)).fetchone()[0]
        return {"published_editions": count}

    @app.put("/api/v2/audiences/{audience_id}/disclosure")
    def disclosure_put(request: Request, audience_id: str, body: Rules):
        rules = [r.model_dump() for r in body.rules]

        def work(c, actor):
            change = D.set_rules(c, actor, audience_id, rules)
            return {**grid(c, audience_id), **tightened(c, audience_id, change)}
        return write(request, body, work)

    @app.post("/api/v2/audiences/{audience_id}/disclosure/preset")
    def disclosure_preset(request: Request, audience_id: str, body: Preset):
        def work(c, actor):
            change = D.apply_preset(c, actor, audience_id, body.name)
            return {**grid(c, audience_id), **tightened(c, audience_id, change)}
        return write(request, body, work)

    # ------------------------------------------------------------------ sources
    @app.get("/api/v2/audiences/{audience_id}/sources")
    def sources_list(request: Request, audience_id: str, limit: int = 100, offset: int = 0):
        gate(request)
        with store.read() as c:
            A.need(c, audience_id)
            return page(A.sources(c, audience_id), "sources", limit, offset)

    @app.post("/api/v2/audiences/{audience_id}/sources")
    def sources_add(request: Request, audience_id: str, body: Source):
        return write(request, body, lambda c, actor: A.add_source(c, actor, audience_id, body.author))

    @app.delete("/api/v2/audiences/{audience_id}/sources/{source_id}")
    def sources_remove(request: Request, audience_id: str, source_id: str):
        return write(request, Empty(), lambda c, actor: A.remove_source(c, actor, audience_id, source_id))

    # ------------------------------------------------------------------ the stats board
    @app.get("/api/v2/company-page/stats")
    def stats_list(request: Request, limit: int = 100, offset: int = 0):
        gate(request)
        with store.read() as c:
            return {**page(ST.list_stats(c), "stats", limit, offset), "available": ST.available(c)}

    @app.post("/api/v2/company-page/stats")
    def stats_create(request: Request, body: StatCreate):
        fields = body.model_dump()
        return write(request, body, lambda c, actor: ST.create_stat(c, actor, **fields))

    @app.patch("/api/v2/company-page/stats/{stat_id}")
    def stats_patch(request: Request, stat_id: str, body: StatPatch):
        fields = body.model_dump(exclude_unset=True)
        return write(request, body, lambda c, actor: ST.update_stat(c, actor, stat_id, **fields))

    @app.delete("/api/v2/company-page/stats/{stat_id}")
    def stats_delete(request: Request, stat_id: str):
        return write(request, Empty(), lambda c, actor: ST.delete_stat(c, actor, stat_id))

    @app.get("/api/v2/audiences/{audience_id}/stats")
    def audience_stats(request: Request, audience_id: str, limit: int = 100, offset: int = 0):
        """The projection for this audience, what is held and why, and what is absent."""
        gate(request)
        with store.read() as c:
            A.need(c, audience_id)
            found = ST.board(c, audience_id, H.now())
        return {**found, **page(found["stats"], "stats", limit, offset)}
