"""The editions routes for owners and admins (plan §10). A bot gets a 403 at every one of them, reads included. Each write
that changes an edition's text runs its checks once the write has committed, because the judge is a model call."""

from datetime import datetime, timedelta, timezone

from fastapi import Query, Request
from pydantic import Field

from ..models import Contract
from ..store import Problem
from . import editions as E
from . import schedule as S
from ..api_contract import api_v2

NOTE = Field(default="", max_length=E.NOTE_CHARS)


class DraftNow(Contract):
    audience_id: str = Field(min_length=1, max_length=100)
    author: str | None = Field(default=None, max_length=200)
    period_start: str | None = Field(default=None, max_length=10)
    period_end: str | None = Field(default=None, max_length=10)


class EditionBody(Contract):
    title: str | None = Field(default=None, max_length=E.TITLE_CHARS * 2)
    sections: list[dict] = Field(max_length=len(E.SECTIONS))


class Correction(EditionBody):
    note: str = NOTE


class Publish(Contract):
    approval_id: str | None = Field(default=None, max_length=100)


class Note(Contract):
    note: str = NOTE


class Empty(Contract):
    pass


def _source(c, audience_id, asked):
    """The source a Draft now is for: the one named, or the audience's only one, or the company's update if it has that."""
    sources = S._sources(c, audience_id)
    if asked:
        return asked
    if len(sources) == 1:
        return sources[0]
    if E.COMPANY in sources:
        return E.COMPANY
    raise Problem("validation", "Say which source to draft" if sources else "This audience has no sources yet", 422)


def _period(c, audience_id, author, start, end):
    """A Draft now's period when none is named: from just after the source's last edition, or the month's start, to
    today, in the company's zone. A scheduled edition for the whole month never collides with it."""
    today = datetime.now(timezone.utc).astimezone(S.company_zone()).date()
    end = end or today.isoformat()
    if start:
        return start, end
    row = c.execute(
        "SELECT max(period_end) FROM editions WHERE audience_id=? AND author=? AND period_end<?",
        (audience_id, author, end),
    ).fetchone()
    try:
        last = datetime.fromisoformat(row[0]).date() + timedelta(days=1) if row and row[0] else None
    except ValueError:
        last = None
    return (last.isoformat() if last else today.replace(day=1).isoformat()), end


def register(app, store, auth, mutate):
    def judge(request):
        return getattr(request.app.state, "judge", None)

    def admin(request):
        who = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only the owner and admins run the Company page", 403)
        return who

    def shown(edition_id):
        with store.read() as c:
            return E.full_view(c, edition_id)

    @app.get(
        "/api/v2/editions",
        **api_v2("Company page", "listEditions", "The editions, paged, newest first, filtered by audience or status"),
    )
    def editions_list(
        request: Request,
        audience: str | None = None,
        status: str | None = None,
        before: str | None = None,
        limit: int = Query(default=25, ge=1, le=E.LIST_MAX),
    ):
        admin(request)
        with store.read() as c:
            return {**E.listing(c, audience, status, before, limit), "schedule_problems": S.problems(c)}

    @app.post(
        "/api/v2/editions",
        **api_v2("Company page", "draftEdition", "Draft an edition now for one audience and period, out of cycle"),
    )
    def edition_draft_now(request: Request, body: DraftNow):
        """Draft now: an edition out of cycle. The model call runs after the row is committed, holding no lock."""
        who = admin(request)

        def work(c):
            E.require_writer(who)
            author = _source(c, body.audience_id, body.author)
            start, end = _period(c, body.audience_id, author, body.period_start, body.period_end)
            return E.claim(c, who.actor, body.audience_id, author, start, end)

        claimed = mutate(request, body, work)
        if claimed["fresh"]:
            return E.run_draft(store, claimed["edition_id"], judge(request))
        return shown(claimed["edition_id"])

    @app.get(
        "/api/v2/editions/{edition_id}",
        **api_v2("Company page", "getEdition", "One edition: its text, checks and status"),
    )
    def edition_get(request: Request, edition_id: str):
        admin(request)
        return shown(edition_id)

    @app.patch(
        "/api/v2/editions/{edition_id}",
        **api_v2(
            "Company page",
            "updateEdition",
            "Change an edition's title and sections; it is checked again before it can publish",
        ),
    )
    def edition_edit(request: Request, edition_id: str, body: EditionBody):
        who = admin(request)
        mutate(request, body, lambda c: E.edit(c, who, edition_id, body.model_dump(exclude_none=True)))
        return E.check(store, edition_id, judge(request))

    @app.post(
        "/api/v2/editions/{edition_id}/check",
        **api_v2("Company page", "checkEdition", "Run the checks on an edition's text now"),
    )
    def edition_check(request: Request, edition_id: str, body: Empty):
        admin(request)
        return E.check(store, edition_id, judge(request))

    @app.post(
        "/api/v2/editions/{edition_id}/approval",
        **api_v2(
            "Company page",
            "requestEditionApproval",
            "Ask the approver to approve an edition; a person's ask, never Agent Ops' own",
        ),
    )
    def edition_approval(request: Request, edition_id: str, body: Empty):
        who = admin(request)
        E.require_person(who)
        # Whatever moved since the last check (a rule, a disclosure change) is read now, so asking never needs two steps.
        with store.read() as c:
            row = E._edition(c, edition_id)
            again = row["status"] in ("held", "ready", "awaiting_approval") and not E._cleared(c, row)
        if again:
            E.check(store, edition_id, judge(request))
        return mutate(request, body, lambda c: E.request_approval(c, who, edition_id))

    @app.post(
        "/api/v2/editions/{edition_id}/publish",
        **api_v2(
            "Company page",
            "publishEdition",
            "Publish an approved edition: the person's own click in Tico, never a token, the Assistant or Agent Ops",
        ),
    )
    def edition_publish(request: Request, edition_id: str, body: Publish):
        who = admin(request)
        published = mutate(request, body, lambda c: E.publish(c, who, edition_id, body.approval_id))
        return E.release_published(store, published)

    @app.post(
        "/api/v2/editions/{edition_id}/retract",
        **api_v2(
            "Company page",
            "retractEdition",
            "Take a published edition back, with a note; a person in the app, never Agent Ops",
        ),
    )
    def edition_retract(request: Request, edition_id: str, body: Note):
        who = admin(request)
        return mutate(request, body, lambda c: E.retract(c, who, edition_id, body.note))

    @app.post(
        "/api/v2/editions/{edition_id}/correct",
        **api_v2(
            "Company page",
            "correctEdition",
            "The next version of a published or retracted edition, with what changed; a person in the app",
        ),
    )
    def edition_correct(request: Request, edition_id: str, body: Correction):
        who = admin(request)
        fields = body.model_dump(exclude_none=True, exclude={"note"})
        made = mutate(request, body, lambda c: E.correct(c, who, edition_id, fields, body.note))
        return E.check(store, made["id"], judge(request))

    @app.post(
        "/api/v2/editions/{edition_id}/override",
        **api_v2(
            "Company page", "overrideEdition", "Clear a held edition with a kept note: the person's own click in Tico"
        ),
    )
    def edition_override(request: Request, edition_id: str, body: Note):
        who = admin(request)
        return mutate(request, body, lambda c: E.override(c, who, edition_id, body.note))

    @app.get(
        "/api/v2/audiences/{audience_id}/stats/approval",
        **api_v2("Company page", "getBoardApproval", "The approval of which stats one audience sees, and in what form"),
    )
    def board_approval_get(request: Request, audience_id: str):
        admin(request)
        with store.read() as c:
            E._audience(c, audience_id)
            return {"approval": E.board_approval(c, audience_id)}

    @app.post(
        "/api/v2/audiences/{audience_id}/stats/approval",
        **api_v2(
            "Company page",
            "requestBoardApproval",
            "Ask the approver to approve which stats one audience sees; a person's ask, never Agent Ops' own",
        ),
    )
    def board_approval_request(request: Request, audience_id: str, body: Empty):
        who = admin(request)
        return mutate(request, body, lambda c: E.request_board_approval(c, who, audience_id))
