# pyright: strict
"""The one read rule for the Directory (track 3): what a reader sees of an entity, a note and an attribute. Every read
of the entity tables goes through it (backend/entities/store.py), and a lint test keeps it that way
(backend/tests/test_entities_visible_lint.py), the way Finance's rule keeps vendor reads honest (backend/finance/visible.py).

An entity shows to a reader when the reader can see at least one thing it is known from:

- an entity with no source rows shows to everyone (an organization typed in by hand, and self);
- a source with visibility company shows to everyone (a sync, a Market seed, a mail sender at company level);
- a restricted source shows when its type's checker says the reader can read it (CHECKERS below): a mailbox the reader
  holds a level on (backend/mailbox_access.py) and that feeds the Directory, a Finance book contact the reader sees a name
  for (backend/finance/visible.py named_all_time), or the reader's own `me` note. A mailbox switched off stops counting, so
  what came only from it hides from everyone, its owner included, until it is switched back on.

Sensitive attributes (tax, payment) show to Finance's owner and to the system only. A note with audience `me` shows to its
author only. The system (jobs that run without a reader) sees everything; no other reader does.

Every read asks the SQL clause this module builds, so a list, a count, a search, a link's far end and a rule path agree.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from backend import mailbox_access as MA
from backend.hubdb_core import KEEPER
from backend.finance import context as FC
from backend.finance.visible import named_all_time

SYSTEM = "system"


@dataclass(frozen=True)
class Reader:
    actor: str  # the actor string the read names ("human:<id>", "bot:<id>") or SYSTEM
    key: str  # the form an author and a `me` source carry: "human:<person id>", "bot:<bot id>", SYSTEM, or ""
    system: bool  # the system reads everything
    sensitive: bool  # sensitive attributes show
    grants: dict[
        str, tuple[str, ...] | None
    ]  # source type -> None (every source of the type) or a tuple of the source ids the reader can read


# mailbox_access is not in the strict set, so its functions are untyped at the call. These wrappers make the same calls
# and give the result a type the strict checks can read.
def _mailbox_rows(c: sqlite3.Connection, who: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], MA.mailboxes_for(c, who))  # pyright: ignore[reportUnknownMemberType]


def actor_key(c: sqlite3.Connection, who: object) -> str:
    """The key a note's author and a `me` source carry. A bot's slug resolves to its id, so the same bot has one key."""
    text = str(who or "").strip()
    if text == SYSTEM:
        return SYSTEM
    p = MA.principal(c, text)  # pyright: ignore[reportUnknownMemberType]
    if p.kind == "person":
        return f"human:{p.id}"
    if p.kind == "bot":
        return f"bot:{p.id}"
    return ""


def _finance(c: sqlite3.Connection, actor: str) -> tuple[FC.Ctx, Any] | None:
    """(ctx, view) for this reader when Finance is set up, else None."""
    if not FC.is_set_up(c):
        return None
    ctx = FC.load(c)
    return ctx, ctx.view(actor)


def _feeds(c: sqlite3.Connection, mailbox_id: str) -> bool:
    return bool(MA.feeds_directory(c, MA.row_by_id(c, mailbox_id)))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def _mailboxes(c: sqlite3.Connection, r: Reader, fin: object) -> tuple[str, ...]:
    feeding = [str(m["id"]) for m in _mailbox_rows(c, r.actor) if m.get("id") and _feeds(c, str(m["id"]))]
    return tuple(sorted(feeding))


def _finance_book(c: sqlite3.Connection, r: Reader, fin: tuple[FC.Ctx, Any] | None) -> tuple[str, ...] | None:
    if fin is None:
        return ()
    ctx, view = fin
    if view.everything:
        return None
    ids = [
        row[0] for row in c.execute("SELECT DISTINCT source_id FROM entity_sources WHERE source_type = 'finance_book'")
    ]
    if not ids:
        return ()
    named = named_all_time(ctx, view, ids)
    return tuple(sorted(contact for contact, shown in named.items() if shown))


def _note_me(c: sqlite3.Connection, r: Reader, fin: object) -> tuple[str, ...]:
    return (r.key,) if r.key else ()


# A restricted source type, and who may read it. A type added here needs a checker and a clause the lint test expects.
CHECKERS: dict[str, Callable[..., tuple[str, ...] | None]] = {
    "mailbox": _mailboxes,
    "finance_book": _finance_book,
    "note_me": _note_me,
}


def reader(c: sqlite3.Connection, who: str) -> Reader:
    actor = str(who or "").strip()
    key = actor_key(c, actor)
    # The keeper is the system writing for no one (ingest, Heartbeat steps): it reads as the system does.
    if actor in (SYSTEM, KEEPER):
        return Reader(actor, key, system=True, sensitive=True, grants={t: None for t in CHECKERS})
    fin = _finance(c, actor)
    sensitive = bool(fin and fin[1].everything)
    grants = {
        source_type: check(c, Reader(actor, key, False, sensitive, {}), fin) for source_type, check in CHECKERS.items()
    }
    return Reader(actor, key, system=False, sensitive=sensitive, grants=grants)


def clause(r: Reader, alias: str = "e") -> tuple[str, list[object]]:
    """A WHERE fragment that keeps the entities of `alias` this reader may see, and its parameters."""
    if r.system:
        return "1 = 1", []
    eid = f"{alias}.id"
    parts = [
        f"NOT EXISTS (SELECT 1 FROM entity_sources s WHERE s.entity_id = {eid})",
        f"EXISTS (SELECT 1 FROM entity_sources s WHERE s.entity_id = {eid} AND s.visibility = 'company')",
    ]
    params: list[object] = []
    grants: list[str] = []
    for source_type, ids in r.grants.items():
        if ids is None:
            grants.append("s.source_type = ?")
            params.append(source_type)
        elif ids:
            grants.append("(s.source_type = ? AND s.source_id IN (SELECT value FROM json_each(?)))")
            params.extend([source_type, json.dumps(list(ids))])
    if grants:
        parts.append(
            f"EXISTS (SELECT 1 FROM entity_sources s WHERE s.entity_id = {eid} AND s.visibility = 'restricted' "
            f"AND ({' OR '.join(grants)}))"
        )
    return "(" + " OR ".join(parts) + ")", params


def visible(c: sqlite3.Connection, who: str | Reader, entity_id: str) -> bool:
    """Whether one entity shows to `who` (an actor string, SYSTEM, or a reader from reader())."""
    r = who if isinstance(who, Reader) else reader(c, who)
    cl, params = clause(r)
    return c.execute(f"SELECT 1 FROM entities e WHERE e.id = ? AND {cl}", [entity_id, *params]).fetchone() is not None


def note_visible(r: Reader, note: Mapping[str, Any]) -> bool:
    """A note shows to everyone who reads its entity when its audience is company; a `me` note to its author."""
    return r.system or note.get("audience") == "company" or (bool(r.key) and note.get("author_key") == r.key)


def attribute_visible(r: Reader, attribute: Mapping[str, Any]) -> bool:
    """A sensitive attribute shows to Finance's owner and the system; every other attribute shows to every reader who
    reads the entity."""
    return r.sensitive or not attribute.get("sensitive")
