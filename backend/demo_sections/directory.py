"""Library › Directory: the people, organizations and places Acme Lights works with.

Places come from the shared places tree, as a hub writes the ones a record refers to (backend/entities/places). Everything
else goes through the Directory's API as Ana, the way a bot's `hub directory` calls would: a customer, a vendor and a law
firm, a person at each, the links between them, a title that changed (so its history shows) and a note. Nothing names a
real company or person.
"""
# pyright: strict

import sqlite3
from collections.abc import Callable
from typing import Any

from ..entities import store as ES
from ..entities.places import pack
from ..entities.places.adapter import directory_adapter

CALIFORNIA, TEXAS = "place:us-ca", "place:us-tx"

ORGANIZATIONS = (
    # (domain, name, link to us, located in)
    ("brighthomes.example", "Bright Homes Retail", "customer_of", "austin"),
    ("lumenparts.example", "Lumen Parts", "vendor_to", CALIFORNIA),
    ("lexpartners.example", "Lex & Partners", "advises", CALIFORNIA),
)
PEOPLE = (
    # (email, name, title then, title now, organization's domain, located in)
    ("jane@brighthomes.example", "Jane Ortiz", "Controller", "CFO", "brighthomes.example", "austin"),
    ("sam@brighthomes.example", "Sam Brooks", None, "Buyer", "brighthomes.example", None),
    ("raj@lumenparts.example", "Raj Patel", None, "Account manager", "lumenparts.example", None),
    ("mia@lexpartners.example", "Mia Chen", None, "Partner", "lexpartners.example", None),
)


Call = Callable[..., dict[str, Any]]  # the API as a person: (method, path relative to /api/v2/, body) -> its answer
Write = Callable[[Callable[[sqlite3.Connection], Any]], Any]  # runs work in one write transaction, returns its result


def _places(c: sqlite3.Connection) -> tuple[str, str]:
    austin = next(p.id for p in pack.find("Austin", kind="city", within=TEXAS))
    los_angeles = next(p.id for p in pack.find("Los Angeles", kind="city", within=CALIFORNIA))
    pack.ensure_places(c, [austin, los_angeles], directory_adapter())
    me = ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
    return me, austin


def seed(b: Any) -> None:  # backend.demo_seed.Builder
    b.at(days=5)
    fill(b.call, b.write, lambda: b.at(days=2))


def _noop() -> None:
    return None


def fill(call: Call, write: Write, later: Callable[[], None] = _noop) -> None:
    """The Directory's rows through `call(method, path, body)` (the API as a person) and `write(work)` (a transaction, for
    the shared places), so the demo and the browser tests' real hub (evals/redesign/seeds.py) start from the same rows.
    `later` moves the clock between a person's first title and the current one."""
    me, austin = write(_places)
    place: dict[str, str] = {"austin": austin}
    call(
        "POST",
        "entities/types/person/attributes",
        {"key": "title", "name": "Title", "description": "The job title the person holds.", "value_type": "text"},
    )
    for target in (CALIFORNIA, TEXAS):
        call("POST", "entities/links", {"from_ref": me, "link_key": "operates_in", "to_ref": target})
    orgs: dict[str, str] = {}
    for domain, name, relation, where in ORGANIZATIONS:
        orgs[domain] = call("POST", "entities", {"type": "organization", "match": {"domain": domain}, "name": name})[
            "id"
        ]
        call("POST", "entities/links", {"from_ref": orgs[domain], "link_key": relation, "to_ref": me})
        call(
            "POST",
            "entities/links",
            {"from_ref": orgs[domain], "link_key": "located_in", "to_ref": place.get(where, where)},
        )
    people: dict[str, str] = {}
    for email, name, then, now, domain, where in PEOPLE:
        if then:
            call(
                "POST",
                "entities",
                {"type": "person", "match": {"email": email}, "name": name, "values": {"title": then}},
            )
            later()
        people[email] = call(
            "POST", "entities", {"type": "person", "match": {"email": email}, "name": name, "values": {"title": now}}
        )["id"]
        call("POST", "entities/links", {"from_ref": people[email], "link_key": "works_at", "to_ref": orgs[domain]})
        if where:
            call(
                "POST", "entities/links", {"from_ref": people[email], "link_key": "located_in", "to_ref": place[where]}
            )
    call(
        "POST",
        "entities/notes",
        {
            "ref": people["jane@brighthomes.example"],
            "body": "Decides reorders with the CEO. Wants sell-through numbers first.",
        },
    )
