"""Vendor identity: one vendor per merchant, so budgets, Watch, attribution rules and the bots' reads never parse bank
text.

A bank or card row's vendor key is core/merchant.merchant_key of its counterparty (stored on the row as vendor_key when
the row is taken in). Its vendor is the contact that key resolves to (fin_vendor_aliases), else the key itself: a read
resolves it each time, so an alias added later applies to rows already in. Watch groups charges by that vendor, and the
Transactions read (and so hub_finance_search) carries it as `vendor: {id, name}`.

Actions (both need finance.book; in a mirrored book only the aliases are written, never a contact or the outbox):
- vendor_merge {into, merge}: `into` is a vendor contact; `merge` is a list of name keys or vendor contact ids. Every
  name a merged contact went by, and its own id, becomes an alias of `into`; a plain name becomes one too. A native
  book archives each merged contact (it stops showing in pickers). Refused: merging a contact into itself, an unknown
  or non-vendor id, a name already aliased to another contact (named in the error), and a budget clash the merge would
  make (the same two-budgets refusal a rename makes). The result lists `keys` (every key it touched), `moved` (the keys
  it took from a contact, with that contact), `added` (names nobody had) and `archived`.
- vendor_unmerge {into, keys}: `keys` are the merge's keys. Each alias the merge added is removed; each one it moved goes
  back to its contact; a merged contact is unarchived. A key the merge did not add to `into` is refused.

Tico-side only: nothing here writes to a provider's books. A mirrored book's contacts are the provider's, so only
fin_vendor_aliases changes there.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from .context import Ctx
from .core import budgets as B
from .core import kinds as K
from .core import ledger as L
from .core.merchant import merchant_key
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, invalid
from .handlers import Plan, Result
from .store import ap as SAP

MAX_MERGE = 50  # keys or contacts in one merge
Row = dict[str, object]


def is_id(key: str) -> bool:
    """Whether a key is a contact id (a uuid) rather than a name key."""
    try:
        return len(key) == 36 and str(uuid.UUID(key)) == key.lower()
    except ValueError:
        return False


def _row_key(r: Any) -> str:
    """A feed row's vendor key: the one stored at sync, else the merchant key of its counterparty text (rows written
    before the key existed, or without it)."""
    return str(r["vendor_key"] or "") or merchant_key(str(r["counterparty"] or ""))


def feed_vendors(
    c: Any,
    book_id: str,
    rows: Sequence[Any],
    sees_kind: Callable[[str], bool],
    *,
    lines_shown: Callable[[Any], bool] | None = None,
) -> dict[str, Row]:
    """Each feed row's vendor, by row id: `id` is the contact its vendor key resolves to, else the key; `name` is that
    contact's name when `sees_kind` says the viewer sees a contact of that kind (else ""), or the row's own counterparty
    text when no contact has the key. Rows are read as the store returns them (vendor_key, counterparty, id).

    `lines_shown` (visible.py's rule) says a row's lines show to the viewer: its vendor's name shows then too, and a row
    whose vendor the viewer may not name shows no counterparty text (its label comes from its lines instead)."""
    keys = {str(r["id"]): _row_key(r) for r in rows}
    resolved: dict[str, str] = {}
    for key in keys.values():
        if key and key not in resolved:
            resolved[key] = SAP.vendor_resolved(c, book_id, key)
    contacts = SAP.contacts(c, sorted(set(resolved.values())))
    out: dict[str, Row] = {}
    for r in rows:
        key = keys[str(r["id"])]
        if not key:
            out[str(r["id"])] = {"id": "", "name": ""}
            continue
        vendor = resolved[key]
        contact = contacts.get(vendor)
        seen = lines_shown(r) if lines_shown is not None else False
        if contact is not None:
            name = str(contact["name"]) if sees_kind(str(contact["kind"])) or seen else ""
        elif lines_shown is None or seen or sees_kind("vendor"):
            name = str(r["counterparty"] or "").strip()
        else:
            name = ""
        out[str(r["id"])] = {"id": vendor, "name": name}
    return out


# ------------------------------------------------------------------------------------------------ actions
def _vendor_contact(ctx: Ctx, contact_id: str) -> Any:
    """A vendor contact of this book (a vendor or both), or the refusal that says why not."""
    row = SAP.get(ctx.c, "fin_contacts", contact_id)
    if row is None or str(row["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such contact")
    if str(row["kind"]) not in ("vendor", "both"):
        raise invalid(f"{row['name']} is a customer, not a vendor")
    return row


def _moves(ctx: Ctx, into: str, merge: Sequence[str]) -> list[tuple[str, str, str]]:
    """The keys a merge puts on `into`, in order, as (key, prior, contact): `prior` is the contact the key already went to
    ("" when no alias had it, which unmerge removes), `contact` the vendor contact it came from ("" for a plain name). A
    contact's keys are its own id, the names it went by and its name; a plain name is its name key. A key already on
    `into` is left out. A key on another contact is refused, and the refusal names that contact."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def take(key: str, contact: str, label: str) -> None:
        if key in seen:
            return
        seen.add(key)
        owner = SAP.alias_of(ctx.c, ctx.book.id, key)
        if owner == into:
            return
        if owner and owner != contact:
            held = SAP.get(ctx.c, "fin_contacts", owner)
            who = str(held["name"]) if held is not None else owner
            raise FinanceError(CONFLICT, f"{label} already goes by {who}")
        out.append((key, owner, contact))

    for item in merge:
        if is_id(item) or SAP.is_contact(ctx.c, item):
            if item == into:
                raise invalid("a vendor can't merge into itself")
            other = _vendor_contact(ctx, item)
            label = str(other["name"])
            take(item, item, label)
            for alias in SAP.aliases_of(ctx.c, item):
                take(alias, item, label)
            take(L.name_key(label), item, label)
        else:
            take(L.name_key(item), "", item)
            take(merchant_key(item), "", item)  # the key a bank row of that name gets
    return out


def _budget_clash(ctx: Ctx, into: str, keys: Sequence[str]) -> None:
    """A merge that makes two sibling budgets count the same spend is refused, as a rename to another's name is."""
    budgets = ctx.budgets()
    touched = set(keys)
    moved = {b.id: replace(b, vendor=into) for b in budgets.values() if b.vendor in touched}
    if not moved:
        return
    both = B.clash({**budgets, **moved}, ctx.accounts, ctx.org)
    if both is not None and (both[0].id in moved or both[1].id in moved):
        one, other = sorted((both[0].name, both[1].name))
        raise FinanceError(
            CONFLICT, f"budgets {one} and {other} would both count the same spend; end or change one first"
        )


def vendor_merge(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("into", "merge"))
    into = V.text(p, "into", 80)
    target = SAP.get(ctx.c, "fin_contacts", into)
    if target is None or str(target["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such contact")
    if str(target["kind"]) not in ("vendor", "both"):
        raise invalid("merge into a vendor")
    if not int(target["active"]):
        raise FinanceError(CONFLICT, "that contact is inactive")
    if "merge" not in p:
        raise invalid("merge is a list of vendor names or contact ids")
    merge = list(dict.fromkeys(V.texts(p, "merge", MAX_MERGE)))
    moves = _moves(ctx, into, merge)  # refuses a conflict now, before anything is asked
    _budget_clash(ctx, into, [k for k, _, _ in moves])

    def apply(aid: str) -> Result:
        now = _moves(ctx, into, merge)
        for key, prior, _ in now:
            SAP.alias_put(ctx.c, ctx.book.id, key, into, ctx.at, prior)
        archived: list[str] = []
        if ctx.book.mode != "mirror":  # a mirrored book's contacts are the provider's: only the aliases change
            for contact in sorted({src for _, _, src in now if src}):
                SAP.update(ctx.c, "fin_contacts", contact, {"active": 0, "updated_at": ctx.at})
                archived.append(contact)
        ctx.changed()
        return {
            "into": into,
            "keys": [k for k, _, _ in now],
            "moved": [{"key": k, "from": p} for k, p, _ in now if p],
            "added": [k for k, p, _ in now if not p],
            "archived": archived,
        }

    return Plan(
        K.default_gate(ctx.org, who, "vendor_merge"),
        f"Merge {len(moves)} name(s) into {target['name']}",
        apply,
    )


def vendor_unmerge(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("into", "keys"))
    into = V.text(p, "into", 80)
    _vendor_contact(ctx, into)
    keys = list(dict.fromkeys(k if is_id(k) else L.name_key(k) for k in V.texts(p, "keys", 2 * MAX_MERGE)))
    came: dict[str, str] = {}  # key -> merged_from: "" for a key the merge added, else the contact it moved from
    for key in keys:
        row = SAP.alias_row(ctx.c, ctx.book.id, key)
        if row is None or str(row[0]) != into or row[1] is None:
            raise invalid(f"{key} was not merged into this vendor")
        came[key] = str(row[1])
    # the merged-in contacts: a contact's own id is one of its keys, and a key moved from a contact names it
    merged_in = sorted({k for k in came if is_id(k)} | {v for v in came.values() if is_id(v)})
    mirrored = ctx.book.mode == "mirror"

    def apply(aid: str) -> Result:
        restored: list[str] = []
        for key, source in came.items():
            if source in ("", key):  # a name the merge added, or a contact's own id alias: removed
                SAP.alias_delete(ctx.c, ctx.book.id, key)
            else:
                SAP.alias_put(ctx.c, ctx.book.id, key, source, ctx.at, None)
                restored.append(key)
        unarchived: list[str] = []
        if not mirrored:
            for contact in merged_in:
                row = SAP.get(ctx.c, "fin_contacts", contact)
                if row is not None and not int(row["active"]):
                    SAP.update(ctx.c, "fin_contacts", contact, {"active": 1, "updated_at": ctx.at})
                    unarchived.append(contact)
        ctx.changed()
        return {"into": into, "keys": keys, "restored": restored, "unarchived": unarchived}

    return Plan(K.default_gate(ctx.org, who, "vendor_unmerge"), "Undo a vendor merge", apply)


PLANNERS: dict[str, Any] = {"vendor_merge": vendor_merge, "vendor_unmerge": vendor_unmerge}

__all__ = ["PLANNERS", "feed_vendors", "is_id", "vendor_merge", "vendor_unmerge"]
