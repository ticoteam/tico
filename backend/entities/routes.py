# pyright: strict
"""The Directory's HTTP routes (`tico contacts`, track 4). Each route is a call on backend/entities/store.py, which holds the
read rule (visible.py) and the writer gate, so a route holds no SQL (the lint test keeps the entity tables to the store).
A read answers with what the caller may see; a write names the caller as its actor, and runs through `mutate` so a retry
with the same Idempotency-Key lands once.

Every path is under /api/v2/entities, not /api/v2/directory, which is the people directory's (SSO and SCIM)."""

import sqlite3
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request
from pydantic import Field

from backend.entities import store as ES
from backend.models import Contract
from backend.store import Problem

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from fastapi import FastAPI

    from backend.auth import Auth
    from backend.store import Store

SOURCE = "hub"  # what an entity value's source says when the Directory's own tools wrote it; the source_id is the actor


class DirectoryUpsert(Contract):
    type: str
    match: dict[str, str] = Field(default_factory=dict)  # one of: id, email, domain, name
    values: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None
    evidence_id: str | None = None
    confidence: float = Field(0.6, ge=0, le=1)


class DirectoryLinkCreate(Contract):
    from_ref: str
    link_key: str
    to_ref: str
    valid_from_on: str | None = None
    valid_until_on: str | None = None
    confidence: float = Field(0.6, ge=0, le=1)
    evidence_id: str | None = None


class DirectoryUnlink(Contract):
    link_id: str


class DirectoryMerge(Contract):
    keep: str
    drop: str


class DirectoryUnmerge(Contract):
    drop: str


class DirectoryNoteCreate(Contract):
    ref: str
    body: str
    audience: str = "company"
    evidence_id: str | None = None


class DirectoryOption(Contract):
    key: str
    label: str


class DirectoryTypeCreate(Contract):
    key: str
    name: str
    description: str
    plural_name: str = ""
    icon: str = ""


class DirectoryAttributeCreate(Contract):
    key: str
    name: str
    description: str
    value_type: str
    options: list[DirectoryOption] = Field(default_factory=list)
    multiple: bool = False
    unique_value: bool = False
    required: bool = False
    sensitive: bool = False
    owner: str | None = None


class DirectoryLinkTypeCreate(Contract):
    key: str
    name: str
    inverse_name: str
    description: str
    from_type: str | None = None
    to_type: str | None = None
    inherits_rules: bool = False
    symmetric: bool = False


def install(
    app: "FastAPI",
    store: "Store",
    auth: "Auth",
    mutate: "Callable[[Request, Contract, Callable[[sqlite3.Connection], Any]], Any]",
) -> None:
    def read() -> "AbstractContextManager[sqlite3.Connection]":
        # The store's read connection, typed for the Directory's functions (as heartbeat.py types its transaction).
        return cast("AbstractContextManager[sqlite3.Connection]", store.read())

    def caller(request: Request) -> str:
        who = request.state.identity
        auth.domain(who)  # pyright: ignore[reportUnknownMemberType]
        return who.actor

    def trace(request: Request, entity_id: str) -> None:
        """A bot run's lookup (`get` or `rules`): the run trace the linker links that run's text to."""
        attempt_id = getattr(request.state.identity, "attempt_id", "") or ""
        if attempt_id:
            with store.transaction() as c:
                ES.record_lookup(cast("sqlite3.Connection", c), attempt_id, entity_id)

    def resolve(c: sqlite3.Connection, actor: str, ref: str) -> dict[str, Any] | None:
        """The one visible entity a read's reference names: None when none shows, and the matches when several do."""
        found = ES.get(c, actor, ref)
        if not found:
            raise Problem("not_found", f"No entity {ref} that you can see", 404)
        return found[0] if len(found) == 1 else None

    def ambiguous(ref: str, c: sqlite3.Connection, actor: str) -> dict[str, Any]:
        return {"ambiguous": True, "ref": ref, "matches": [ES.brief(row) for row in ES.get(c, actor, ref)]}

    def where_we_operate(request: Request, link_key: str | None, touches_self: bool) -> None:
        """Where we operate switches laws on for the whole company, so a person changes it only as an owner or an admin.
        Bots keep the writer gate every Directory write has."""
        who = request.state.identity
        if link_key != "operates_in" or not touches_self or who.role not in ("owner", "human"):
            return
        if auth.company_role(who) not in ("owner", "admin"):  # pyright: ignore[reportUnknownMemberType]
            raise Problem("forbidden", "Only an owner or an admin changes where we operate", 403)

    def page_error(error: ValueError) -> Problem:
        return Problem("validation", str(error), 422)

    @app.get("/api/v2/entities/self")
    def directory_self(request: Request):
        """Who we are: our company, where we operate, and the obligations that bind us (none until rules arrive). Every
        bot's base context reads it."""
        actor = caller(request)
        with read() as c:
            body = ES.self_summary(c, actor)
        if body is None:
            raise Problem("not_found", "Our company is not in Contacts yet", 404)
        return body

    @app.get("/api/v2/entities/types")
    def directory_types(request: Request):
        """The entity types: built-in and added, each with its description and who added it."""
        caller(request)
        with read() as c:
            return {"types": ES.types(c)}

    @app.get("/api/v2/entities/counts")
    def directory_counts(request: Request):
        """How many entities of each type you can see, each stopping at 1,000 and saying so (`approximate`)."""
        actor = caller(request)
        with read() as c:
            return {"counts": ES.type_counts(c, actor)}

    @app.get("/api/v2/entities/types/{type_key}")
    def directory_describe(request: Request, type_key: str):
        """One type: its attributes (each with its owner and options) and the link types that may touch it."""
        caller(request)
        with read() as c:
            body = ES.describe_type(c, type_key)
        if body is None:
            raise Problem("not_found", f"No entity type {type_key}; see tico contacts types", 404)
        return body

    @app.get("/api/v2/entities/find/{type_key}")
    def directory_find(
        request: Request,
        type_key: str,
        name: str | None = None,
        status: str | None = None,
        link: str | None = None,
        after: str | None = None,
        limit: int | None = None,
        detail: bool = False,
    ):
        """A page of one type's entities you can see, by name, status or a link to us (customer_of). Page with `next`.
        `detail` adds each row's organization, owner and the sources you can see it is known from (the Directory list)."""
        actor = caller(request)
        with read() as c:
            if not ES.type_exists(c, type_key):
                raise Problem("not_found", f"No entity type {type_key}; see tico contacts types", 404)
            try:
                return ES.find(
                    c,
                    actor,
                    type_key,
                    name=name,
                    status=status,
                    link=link,
                    after=after,
                    limit=limit or 50,
                    detail=detail,
                )
            except ValueError as error:
                raise page_error(error) from None

    @app.get("/api/v2/entities/card")
    def directory_card(request: Request, ref: str):
        """One entity as you see it: its values with source and age, its links, external records, and your notes. A
        reference two entities match returns both, and says so."""
        actor = caller(request)
        with read() as c:
            found = ES.get(c, actor, ref)
            if not found:
                raise Problem("not_found", f"No entity {ref} that you can see", 404)
            if len(found) > 1:
                return ambiguous(ref, c, actor)
            body = ES.card(c, actor, found[0]["id"])
        if body is None:
            raise Problem("not_found", f"No entity {ref} that you can see", 404)
        trace(request, found[0]["id"])
        return {"ambiguous": False, **body}

    @app.get("/api/v2/entities/rules")
    def directory_rules(request: Request, ref: str, action: str | None = None, as_bot: str | None = None):
        """The rules that apply to an entity, with their paths. Rules arrive in track 5, so none apply yet."""
        actor = caller(request)
        with read() as c:
            found = ES.get(c, actor, ref)
            if not found:
                raise Problem("not_found", f"No entity {ref} that you can see", 404)
            if len(found) > 1:
                return ambiguous(ref, c, actor)
            applies = ES.rules(c, actor, found[0]["id"])
        trace(request, found[0]["id"])
        return {"entity": found[0]["id"], "action": action, "as_bot": as_bot, "rules": applies or []}

    @app.get("/api/v2/entities/history")
    def directory_history(
        request: Request, ref: str, attribute: str | None = None, after: str | None = None, limit: int | None = None
    ):
        """Past values of an entity's fields, newest first (one field with `attribute`). Page with `next`."""
        actor = caller(request)
        with read() as c:
            found = resolve(c, actor, ref)
            if found is None:
                raise Problem("ambiguous", f"More than one entity matches {ref}; name one by id", 409)
            try:
                body = ES.history(c, actor, found["id"], attribute, after=after, limit=limit or 50)
            except ValueError as error:
                raise page_error(error) from None
        if body is None:
            raise Problem("not_found", f"No history you can see for {ref}", 404)
        return body

    @app.post("/api/v2/entities/types")
    def directory_type_add(request: Request, body: DirectoryTypeCreate):
        """Add an entity type. Its description is required; a near-duplicate of a type that exists is refused with that one."""
        actor = caller(request)
        return mutate(
            request,
            body,
            lambda c: {
                "key": ES.add_type(
                    c, actor, body.key, body.name, body.description, plural_name=body.plural_name, icon=body.icon
                )
            },
        )

    @app.post("/api/v2/entities/types/{type_key}/attributes")
    def directory_attribute_add(request: Request, type_key: str, body: DirectoryAttributeCreate):
        """Add an attribute to a type. A select or status needs its options; an owner of connection:<id> makes it that
        connection's field."""
        actor = caller(request)
        options = [{"key": option.key, "label": option.label} for option in body.options]
        return mutate(
            request,
            body,
            lambda c: {
                "key": ES.add_attribute(
                    c,
                    actor,
                    type_key,
                    body.key,
                    body.name,
                    body.value_type,
                    body.description,
                    options=options,
                    multiple=body.multiple,
                    unique_value=body.unique_value,
                    required=body.required,
                    sensitive=body.sensitive,
                    owner=body.owner,
                ),
                "type": type_key,
            },
        )

    @app.post("/api/v2/entities/link-types")
    def directory_link_type_add(request: Request, body: DirectoryLinkTypeCreate):
        """Add a link type. Its description is required; `inherits_rules` says whether a rule toward one end reaches the other."""
        actor = caller(request)
        return mutate(
            request,
            body,
            lambda c: {
                "key": ES.add_link_type(
                    c,
                    actor,
                    body.key,
                    body.name,
                    body.inverse_name,
                    body.description,
                    from_type=body.from_type,
                    to_type=body.to_type,
                    inherits_rules=body.inherits_rules,
                    symmetric=body.symmetric,
                )
            },
        )

    @app.post("/api/v2/entities")
    def directory_upsert(request: Request, body: DirectoryUpsert):
        """Create or update one entity of a type. `match` names it by id, email, domain or name; several matches are refused."""
        actor = caller(request)
        return mutate(
            request,
            body,
            lambda c: ES.upsert(
                c,
                actor,
                body.type,
                match=body.match,
                values=body.values,
                name=body.name,
                evidence_id=body.evidence_id,
                source_type=SOURCE,
                source_id=actor,
                confidence=body.confidence,
            ),
        )

    @app.post("/api/v2/entities/links")
    def directory_link(request: Request, body: DirectoryLinkCreate):
        """A typed, dated link between two entities you can see. The link type says which ends it may join. Where we
        operate (our company operates_in a place) is an owner's or an admin's."""
        actor = caller(request)

        def work(c: sqlite3.Connection):
            from_id = ES.target(c, actor, body.from_ref)
            to_id = ES.target(c, actor, body.to_ref)
            where_we_operate(request, body.link_key, ES.touches_self(c, entity_ids=[from_id, to_id]))
            link_id = ES.link(
                c,
                actor,
                from_id,
                body.link_key,
                to_id,
                valid_from_on=body.valid_from_on,
                valid_until_on=body.valid_until_on,
                confidence=body.confidence,
                source_type=SOURCE,
                source_id=actor,
                evidence_id=body.evidence_id,
            )
            return {"id": link_id, "from": from_id, "link_key": body.link_key, "to": to_id}

        return mutate(request, body, work)

    @app.post("/api/v2/entities/unlink")
    def directory_unlink(request: Request, body: DirectoryUnlink):
        """End a link: it stops being current today. Its history stays. Where we operate is an owner's or an admin's."""
        actor = caller(request)

        def work(c: sqlite3.Connection):
            where_we_operate(request, ES.link_key_of(c, body.link_id), ES.touches_self(c, link_id=body.link_id))
            return {"id": ES.unlink(c, actor, body.link_id)}

        return mutate(request, body, work)

    @app.post("/api/v2/entities/merge")
    def directory_merge(request: Request, body: DirectoryMerge):
        """Merge one entity into another: the dropped one's rows move to the kept one, and `unmerge` reverses it exactly."""
        actor = caller(request)

        def work(c: sqlite3.Connection):
            keep = ES.target(c, actor, body.keep)
            drop = ES.target(c, actor, body.drop)
            return {"kept": ES.merge(c, actor, keep, drop), "merged": drop}

        return mutate(request, body, work)

    @app.post("/api/v2/entities/unmerge")
    def directory_unmerge(request: Request, body: DirectoryUnmerge):
        """Reverse the last merge of a dropped entity, named by its own id (a merged reference resolves to the kept one)."""
        actor = caller(request)
        return mutate(request, body, lambda c: {"unmerged": ES.unmerge(c, actor, body.drop)})

    @app.post("/api/v2/entities/notes")
    def directory_note(request: Request, body: DirectoryNoteCreate):
        """A note on an entity. `audience` me keeps it to you; company shows it to everyone who sees the entity."""
        actor = caller(request)

        def work(c: sqlite3.Connection):
            entity_id = ES.target(c, actor, body.ref)
            note_id = ES.note(c, actor, entity_id, body.body, audience=body.audience, evidence_id=body.evidence_id)
            return {"id": note_id, "entity": entity_id}

        return mutate(request, body, work)


__all__ = ["install"]
