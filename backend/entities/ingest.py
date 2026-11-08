# pyright: strict
"""Directory ingest (track 6a): the people and organizations the sources a team already uses name, and the links between
them. Mail, meetings and Company page readers each call one function here. Every write goes through the
store (backend/entities/store.py), as the system's own actor, so the writer gate and the read rule hold.

What each source makes (spec §7):
- Mail: a person for each sender of an inbox that feeds the Directory, and the organization its domain names, with
  works_at between them. A sender from the company's own domains links to self. A free-mail domain names no organization.
  An automated sender (no-reply address, list mail, bulk mail) makes its organization only, never a person.
- Meetings: a person for each outside attendee of a live team meeting, with their organization. A private or
  participants-only meeting makes nothing.
- Counsel: none here. A rule's source is an organization entity itself (track 5, org_rules.source_entity_id).
- Company page: a reader on the board or investors list is a person who advises self or invests in it.

Each step is safe inside the action that caused it: safely() runs it in a savepoint, logs a failure, and returns None,
so the action goes on. Running a step again adds nothing: an entity found by its identifier is not written again, and a
link that is already current is not repeated.

Sources and who sees them: a mailbox source is restricted to that mailbox, so only its readers see the entity. Meeting
and Company page sources are company-wide. An entity with no source shows to everyone, and an entity known only
from a restricted source hides from everyone else, so _record never adds a source that would change who sees an entity.
"""

import logging
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from email.utils import parseaddr
from typing import TypeVar, cast

from backend import access as A
from backend import hubdb as H
from backend import mailbox_access as MA
from backend.entities import store as ES

log = logging.getLogger("tico.entities.ingest")

T = TypeVar("T")
ACTOR = H.KEEPER  # the system writes these rows; they are not any person's edit
HARD_CONFIDENCE = 0.9  # a sender's domain, a meeting's invite, an owner's list: evidence the link can carry rules on
NO_REPLY = ("noreply", "no-reply", "donotreply", "do-not-reply")
MACHINE_LOCAL = ("mailer-daemon", "postmaster")
BULK = ("bulk", "list", "junk")
SECOND_LEVEL = ("co", "com", "org", "net", "gov", "ac", "edu")  # acme.co.uk names Acme, not Co
ROLE_LINKS = {"board": "advises", "investors": "invests_in"}  # a Company page list to the link it makes toward self


# The access and mailbox modules predate the strict check, so their functions are untyped here. These wrappers make the same
# calls and keep the untyped access in one place (store.py does the same for hubdb's helpers).
def _domain(address: str) -> str:
    return str(A.domain_of(address))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def _company_domains(c: sqlite3.Connection) -> set[str]:
    return set(MA.company_domains(c))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]


def _row(c: sqlite3.Connection, mailbox_id: str) -> dict[str, object] | None:
    return MA.row_by_id(c, mailbox_id)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def _feeds(c: sqlite3.Connection, box: dict[str, object] | None) -> bool:
    return bool(MA.feeds_directory(c, box))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def safely(c: sqlite3.Connection, what: str, work: Callable[..., T], *args: object, **kwargs: object) -> T | None:
    """Runs one ingest step in a savepoint. When it works its writes stand and its result is returned. When it raises,
    its writes are undone, the failure is logged with `what`, and None is returned: the action that called it goes on."""
    c.execute("SAVEPOINT directory_ingest")
    try:
        result = work(*args, **kwargs)
    except Exception:  # noqa: BLE001 - any failed step is undone and logged; the mail, meeting or page action goes on
        c.execute("ROLLBACK TO directory_ingest")
        c.execute("RELEASE directory_ingest")
        log.warning("Directory ingest from %s failed; the action went on without it", what, exc_info=True)
        return None
    c.execute("RELEASE directory_ingest")
    return result


def _address(value: object) -> str:
    """The bare, lowercased address in a header or a plain address, or "" when it has no domain."""
    _, address = parseaddr(str(value or ""))
    address = address.strip().lower()
    return address if "@" in address and _domain(address) else ""


def _name(value: object) -> str | None:
    """The display name in a From header, or None when the header names no one."""
    name, _ = parseaddr(str(value or ""))
    return name.strip().strip('"').strip()[:200] or None


def _org_name(domain: str) -> str:
    """A name for the organization a domain belongs to: its registrable label, capitalized (mail.acme.example is Acme)."""
    labels = domain.split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2:
        base = labels[-3]
    else:
        base = labels[-2] if len(labels) >= 2 else labels[0]
    return base.replace("-", " ").strip().title() or domain


def _automated(address: str, headers: Mapping[str, object]) -> bool:
    """A sender that is a machine: a no-reply or postmaster address, list mail, or bulk mail its client marks as such."""
    local = address.partition("@")[0]
    if local.startswith(NO_REPLY) or local in MACHINE_LOCAL:
        return True
    if headers.get("list_id") or headers.get("has_unsubscribe"):
        return True
    facts: Mapping[str, object] = {}
    raw = headers.get("send_facts")
    if isinstance(raw, Mapping):
        facts = cast(Mapping[str, object], raw)
    auto = str(facts.get("auto-submitted") or "").strip().lower()
    precedence = str(facts.get("precedence") or "").strip().lower()
    return auto not in ("", "no") or precedence in BULK


def _entity(
    c: sqlite3.Connection,
    type_key: str,
    match: Mapping[str, object],
    name: str | None,
    values: Mapping[str, object] | None = None,
) -> tuple[str, bool] | None:
    """The entity of a type the match names, made when there is none: (id, created). A match that finds several is left
    alone (ingest never guesses), and an entity that exists is not written to, so its name and values stay as they are."""
    found = ES.matching(c, type_key, match)
    if len(found) > 1:
        return None
    if found:
        return found[0], False
    made = ES.upsert(c, ACTOR, type_key, match=match, name=name, values=values)
    return str(made["id"]), True


def _organization(c: sqlite3.Connection, domain: str) -> tuple[str, bool] | None:
    """The organization a domain names. None for a free-mail domain, which names no one's employer."""
    if not domain or domain in A.PUBLIC_MAIL:
        return None
    return _entity(c, "organization", {"domain": domain}, _org_name(domain))


def _record(
    c: sqlite3.Connection, entity_id: str, source_type: str, source_id: str, *, created: bool, visibility: str
) -> None:
    """Records where an entity is known from, without changing who can see it. A restricted source goes on an entity just
    made or one that already has sources: an entity with none shows to everyone, and a restricted source would hide it.
    A company source goes on an entity just made or one with none: an entity known only from restricted sources would
    show to everyone. Self shows to everyone and carries no sources."""
    if entity_id == ES.self_id(c):
        return
    sourced = ES.has_sources(c, entity_id)
    if visibility == "restricted" and (created or sourced):
        ES.add_source(c, ACTOR, entity_id, source_type, source_id, visibility="restricted")
    elif visibility == "company" and (created or not sourced):
        ES.add_source(c, ACTOR, entity_id, source_type, source_id, visibility="company")


def _works_at(c: sqlite3.Connection, person_id: str, org_id: str, source_type: str, source_id: str) -> str:
    return ES.link(
        c,
        ACTOR,
        person_id,
        "works_at",
        org_id,
        confidence=HARD_CONFIDENCE,
        source_type=source_type,
        source_id=source_id,
    )


def from_mail(c: sqlite3.Connection, mailbox_id: str, headers: Mapping[str, object]) -> dict[str, object]:
    """One new message in an inbox: its sender becomes a person (unless automated) with the organization their domain
    names, and the mailbox is a restricted source for both. Nothing is made for an inbox that does not feed the Directory.
    `headers` carries from_addr, from_header, list_id, has_unsubscribe and send_facts (as mail_publish receives them)."""
    box = _row(c, mailbox_id)
    if not _feeds(c, box):
        return {}
    address = _address(headers.get("from_addr")) or _address(headers.get("from_header"))
    if not address:
        return {}
    domain = _domain(address)
    internal = domain in _company_domains(c)
    org: tuple[str, bool] | None
    if internal:
        self_id = ES.self_id(c)
        org = (self_id, False) if self_id else None
    else:
        org = _organization(c, domain)
    automated = _automated(address, headers)
    person: tuple[str, bool] | None = None
    if not automated:
        person = _entity(c, "person", {"email": address}, _name(headers.get("from_header")))
    if org:
        _record(c, org[0], "mailbox", mailbox_id, created=org[1], visibility="restricted")
    if person:
        _record(c, person[0], "mailbox", mailbox_id, created=person[1], visibility="restricted")
        if org:
            _works_at(c, person[0], org[0], "mailbox", mailbox_id)
    made = [entity[0] for entity in (org, person) if entity and entity[1]]
    return {"person": person[0] if person else None, "organization": org[0] if org else None, "created": made}


def from_meeting(
    c: sqlite3.Connection,
    meeting_id: str,
    *,
    audience: str,
    review_state: str,
    participants: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """The outside attendees of a meeting: a person each, with their organization. Only a team meeting that is live makes
    anything. Attendees who are on the roster (matched by person_id) or on the company's own domains are teammates."""
    if audience != "team" or review_state != "live":
        return {}
    internal = _company_domains(c)
    made: list[str] = []
    people: list[str] = []
    for attendee in participants:
        address = _address(attendee.get("email"))
        if not address or attendee.get("person_id") or _domain(address) in internal:
            continue
        person = _entity(c, "person", {"email": address}, str(attendee.get("name") or "").strip() or None)
        if person is None:
            continue
        people.append(person[0])
        if person[1]:
            made.append(person[0])
        _record(c, person[0], "meeting", meeting_id, created=person[1], visibility="company")
        org = _organization(c, _domain(address))
        if org:
            _record(c, org[0], "meeting", meeting_id, created=org[1], visibility="company")
            _works_at(c, person[0], org[0], "meeting", meeting_id)
            if org[1]:
                made.append(org[0])
    return {"people": people, "created": made}


def from_company_page(
    c: sqlite3.Connection, *, email: str, name: str | None, kinds: Iterable[str]
) -> dict[str, object]:
    """A reader on the Company page's list. A board member advises self, and an investor invests in it: a person each,
    linked to self, with the organization their domain names. A reader on no such list (public, custom) makes nothing."""
    roles = sorted({ROLE_LINKS[kind] for kind in kinds if kind in ROLE_LINKS})
    address = _address(email)
    me = ES.self_id(c)
    if not address or not roles or me is None:
        return {}
    domain = _domain(address)
    internal = domain in _company_domains(c)
    org: tuple[str, bool] | None = (me, False) if internal else _organization(c, domain)
    person = _entity(c, "person", {"email": address}, name)
    if person is None:
        return {}
    _record(c, person[0], "company_page", address, created=person[1], visibility="company")
    made = [person[0]] if person[1] else []
    if org:
        _record(c, org[0], "company_page", domain, created=org[1], visibility="company")
        _works_at(c, person[0], org[0], "company_page", address)
        if org[1]:
            made.append(org[0])
    for role in roles:
        ES.link(
            c,
            ACTOR,
            person[0],
            role,
            me,
            confidence=HARD_CONFIDENCE,
            source_type="company_page",
            source_id=address,
        )
    return {"person": person[0], "created": made}
