"""The owner's side of Company page readers: the list, invite links and what readers sent. Owners and admins only.

Readers' addresses and messages are outside data about outside people, so a turn of the Assistant doesn't get them: only
the person's own click on a proposal counts. Bots aren't admins and never get here.

A new invite skips `mutate`: its answer holds the link's token, and the request cache would keep it in the clear, against
a table that otherwise holds only hashes. A repeat of the request makes a second link, which is harmless beside a stored
secret; the first expires unused.
"""

from typing import Annotated

from fastapi import Query, Request
from pydantic import Field

from .. import roadmaps as P
from ..models import Contract, Empty
from ..store import Problem
from . import readers as R
from ..api_contract import api_v2

Limit = Annotated[int, Query(ge=1, le=500)]
Cursor = Annotated[int, Query(ge=0)]  # the `next` of the page before: what the app sends back as is


class ReaderInput(Contract):
    email: str = Field(min_length=3, max_length=320)
    name: str | None = Field(default=None, max_length=200)
    audience_ids: list[str] = Field(default_factory=list, max_length=R.AUDIENCES_PER_READER)


class ReaderChange(Contract):
    name: str | None = Field(default=None, max_length=200)
    notify: bool | None = None
    audience_ids: list[str] | None = Field(default=None, max_length=R.AUDIENCES_PER_READER)


class ReaderImport(Contract):
    csv: str = Field(min_length=1, max_length=R.CSV_BYTES_MAX)
    audience_id: str | None = Field(default=None, max_length=100)
    audience_ids: list[str] = Field(default_factory=list, max_length=R.AUDIENCES_PER_READER)


class InviteInput(Contract):
    days: int = Field(default=R.INVITE_DAYS, ge=1, le=R.INVITE_DAYS_MAX)


def register(app, store, auth, mutate):
    settings = store.settings

    def company_admin(request):
        who = request.state.identity
        auth.domain(who)
        if auth.company_role(who) not in ("owner", "admin"):
            raise Problem("forbidden", "Only an owner or admin manages Company page readers", 403)
        if who.via == "assistant" and not who.confirmed:
            raise Problem("forbidden", "The Assistant doesn't handle Company page readers", 403)
        return who

    @app.get(
        "/api/v2/readers",
        **api_v2("Company page", "listReaders", "The readers, paged, with the audiences each may read"),
    )
    def readers_list(request: Request, limit: Limit = 100, cursor: Cursor = 0):
        company_admin(request)
        available, reason = R.availability(settings)
        with store.read() as c:
            items, after = P.page(R.list_rows(c, limit, cursor), limit, cursor)
        return {
            "readers": items,
            "next": after,
            "sign_in": {"available": available, "reason": reason},
            "email_available": R.notices_available(),
        }

    @app.post(
        "/api/v2/readers",
        **api_v2(
            "Company page",
            "addReader",
            "Give one person outside the company access to the audiences named; the owner's own tap when BotOps asks",
        ),
    )
    def readers_add(request: Request, body: ReaderInput):
        who = company_admin(request)
        R.require_available(settings)
        return mutate(request, body, lambda c: R.add(c, who.actor, body.email, body.audience_ids, body.name))

    @app.patch(
        "/api/v2/readers/{reader_id}",
        **api_v2(
            "Company page",
            "updateReader",
            "Change which audiences a reader may read, or their name; the owner's own tap when BotOps asks",
        ),
    )
    def readers_change(request: Request, reader_id: str, body: ReaderChange):
        """The way out of one audience without losing the rest: `audience_ids` replaces the whole set."""
        who = company_admin(request)
        return mutate(
            request, body, lambda c: R.update(c, who.actor, reader_id, body.audience_ids, body.notify, body.name)
        )

    @app.delete("/api/v2/readers/{reader_id}", **api_v2("Company page", "removeReader", "Take a reader's access away"))
    def readers_remove(request: Request, reader_id: str, body: Empty | None = None):
        who = company_admin(request)
        return mutate(request, body or Empty(), lambda c: R.remove(c, who.actor, reader_id))

    @app.post(
        "/api/v2/readers/import",
        **api_v2(
            "Company page", "importReaders", "Add readers from a CSV of addresses; the owner's own tap when BotOps asks"
        ),
    )
    def readers_import(request: Request, body: ReaderImport):
        who = company_admin(request)
        R.require_available(settings)
        ids = [*body.audience_ids, *([body.audience_id] if body.audience_id else [])]
        return mutate(request, body, lambda c: R.import_csv(c, who.actor, body.csv, ids))

    @app.get(
        "/api/v2/audiences/{audience_id}/invites",
        **api_v2("Company page", "listInvites", "The invites to one audience, paged, without their links"),
    )
    def invites_list(request: Request, audience_id: str, limit: Limit = 100, cursor: Cursor = 0):
        company_admin(request)
        with store.read() as c:
            items, after = P.page(R.invite_rows(c, audience_id, limit, cursor), limit, cursor)
        return {"invites": items, "next": after}

    @app.post(
        "/api/v2/audiences/{audience_id}/invites",
        **api_v2(
            "Company page",
            "createInvite",
            "A single-use link into one audience; the link is shown to a person in the app, never to BotOps",
        ),
    )
    def invites_create(request: Request, audience_id: str, body: InviteInput | None = None):
        from ..auth import validate_identity

        who = company_admin(request)
        R.require_available(settings)
        with store.transaction() as c:
            validate_identity(c, who)
            invite, token = R.create_invite(c, who.actor, audience_id, (body or InviteInput()).days)
        return {**invite, "url": settings.reader_origin + "/invite/" + token}

    @app.delete(
        "/api/v2/audiences/{audience_id}/invites/{invite_id}",
        **api_v2("Company page", "revokeInvite", "Revoke an invite that has not been used"),
    )
    def invites_revoke(request: Request, audience_id: str, invite_id: str, body: Empty | None = None):
        who = company_admin(request)
        return mutate(request, body or Empty(), lambda c: R.revoke_invite(c, who.actor, audience_id, invite_id))

    @app.get(
        "/api/v2/editions/{edition_id}/notices",
        **api_v2(
            "Company page", "listEditionNotices", "The reader notices on one edition (who was told, what was sent)"
        ),
    )
    def edition_notices(request: Request, edition_id: str):
        company_admin(request)
        with store.read() as c:
            return {**R.notices_of(c, edition_id), "email_available": R.notices_available()}

    @app.get(
        "/api/v2/reader-messages",
        **api_v2(
            "Company page",
            "listReaderMessages",
            "What readers sent the page (replies and requests), newest first; they never wake a bot",
        ),
    )
    def messages_list(request: Request, limit: Limit = 100, cursor: Cursor = 0):
        company_admin(request)
        with store.read() as c:
            items, after = P.page(R.message_rows(c, limit, cursor), limit, cursor)
        return {"messages": items, "next": after}
