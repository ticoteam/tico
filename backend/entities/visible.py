"""The one read rule for the Directory (track 3): what a reader sees of an entity, a note and an attribute. Every read
of the entity tables goes through it (backend/entities/store.py), and a lint test keeps it that way
(backend/tests/test_entities_visible_lint.py), the way Finance's rule keeps vendor reads honest (backend/finance/visible.py).

An entity shows to a reader when the reader can see at least one thing it is known from:

- an entity with no source rows shows to everyone (an organization typed in by hand, and self);
- a source with visibility company shows to everyone (a sync, a Market seed, a mail sender at company level);
- a restricted source shows when its type's checker says the reader can read it (CHECKERS below): a mailbox the reader
  holds a level on (backend/mailbox_access.py), a Finance book contact the reader sees a name for
  (backend/finance/visible.py named_all_time), or the reader's own `me` note.

Sensitive attributes (tax, payment) show to Finance's owner and to the system only. A note with audience `me` shows to its
author only. The system (jobs that run without a reader) sees everything; no other reader does.

Every read asks the SQL clause this module builds, so a list, a count, a search, a link's far end and a rule path agree.
"""

import json
from dataclasses import dataclass

from backend import mailbox_access as MA
from backend.finance import context as FC
from backend.finance.visible import named_all_time

SYSTEM = "system"


@dataclass(frozen=True)
class Reader:
    actor: str  # the actor string the read names ("human:<id>", "bot:<id>") or SYSTEM
    key: str  # the form an author and a `me` source carry: "human:<person id>", "bot:<bot id>", SYSTEM, or ""
    system: bool  # the system reads everything
    sensitive: bool  # sensitive attributes show
    grants: dict  # source type -> None (every source of the type) or a tuple of the source ids the reader can read


def actor_key(c, who):
    """The key a note's author and a `me` source carry. A bot's slug resolves to its id, so the same bot has one key."""
    text = str(who or "").strip()
    if text == SYSTEM:
        return SYSTEM
    p = MA.principal(c, text)
    if p.kind == "person":
        return f"human:{p.id}"
    if p.kind == "bot":
        return f"bot:{p.id}"
    return ""


def _finance(c, actor):
    """(ctx, view) for this reader when Finance is set up, else None."""
    if not FC.is_set_up(c):
        return None
    ctx = FC.load(c)
    return ctx, ctx.view(actor)


def _mailboxes(c, r, fin):
    return tuple(sorted(m["id"] for m in MA.mailboxes_for(c, r.actor) if m.get("id")))


def _finance_book(c, r, fin):
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


def _note_me(c, r, fin):
    return (r.key,) if r.key else ()


# A restricted source type, and who may read it. A type added here needs a checker and a clause the lint test expects.
CHECKERS = {"mailbox": _mailboxes, "finance_book": _finance_book, "note_me": _note_me}


def reader(c, who):
    actor = str(who or "").strip()
    key = actor_key(c, actor)
    if actor == SYSTEM:
        return Reader(actor, key, system=True, sensitive=True, grants={t: None for t in CHECKERS})
    fin = _finance(c, actor)
    sensitive = bool(fin and fin[1].everything)
    grants = {
        source_type: check(c, Reader(actor, key, False, sensitive, {}), fin) for source_type, check in CHECKERS.items()
    }
    return Reader(actor, key, system=False, sensitive=sensitive, grants=grants)


def clause(r, alias="e"):
    """A WHERE fragment that keeps the entities of `alias` this reader may see, and its parameters."""
    if r.system:
        return "1 = 1", []
    eid = f"{alias}.id"
    parts = [
        f"NOT EXISTS (SELECT 1 FROM entity_sources s WHERE s.entity_id = {eid})",
        f"EXISTS (SELECT 1 FROM entity_sources s WHERE s.entity_id = {eid} AND s.visibility = 'company')",
    ]
    params = []
    grants = []
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


def visible(c, who, entity_id):
    """Whether one entity shows to `who` (an actor string, SYSTEM, or a reader from reader())."""
    r = who if isinstance(who, Reader) else reader(c, who)
    cl, params = clause(r)
    return c.execute(f"SELECT 1 FROM entities e WHERE e.id = ? AND {cl}", [entity_id, *params]).fetchone() is not None


def note_visible(r, note):
    """A note shows to everyone who reads its entity when its audience is company; a `me` note to its author."""
    return r.system or note.get("audience") == "company" or (bool(r.key) and note.get("author_key") == r.key)


def attribute_visible(r, attribute):
    """A sensitive attribute shows to Finance's owner and the system; every other attribute shows to every reader who
    reads the entity."""
    return r.sensitive or not attribute.get("sensitive")
