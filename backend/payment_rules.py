# pyright: strict
"""The Directory's structured rules on an outgoing payment (finance `payment_create`), checked as a bot's email is
(backend/email_sending.py `rule_gate`, org_rule_check `mail_decide`): only a payment a bot makes is checked, alone or
for a person (the Assistant, BotOps), or one the system makes for a bot (a bill the bot scheduled). A person's own
payment is not, as a person's own mail is not.

The payee is found in the Directory from the payment's recipient (`fin_recipients`) and the Finance contact it belongs
to: their emails (an email identifier, or the organization whose domain it is), their names as identifiers scoped to the
book, their external refs, and the contact id itself once it is an entity. A payee the Directory does not know is
checked as an unknown email recipient is: with no target, so only rules reached from self apply.

A held payment is never made: nothing is written to Finance. Send anyway (a waiver by the rule's editor or an Admin)
lets that exact payment pass its next check once, and the bot is told to make it again, as with a held mail. A check
that cannot complete holds the payment (fail closed)."""

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any, cast

from . import hubdb as _hubdb
from . import org_rule_check as _org_rule_check
from .entities import rules as DR
from .entities import store as _entity_store

# hubdb, org_rule_check and the entity store's merge walk are not strict yet: they are reached through Any.
H: Any = _hubdb
OR: Any = _org_rule_check
ES: Any = _entity_store
Found = tuple[str, str, str]
Prep = dict[str, Any]

OPERATION = "payment"
FINANCE_SYSTEM = "system:finance"


def checked(actor: object, for_actor: object = "", route: Found | None = None) -> Found | None:
    """(author, sender, via) a payment is checked as, or None when it is not checked. `route` is the HTTP caller's
    `org_rule_check.checked_as`: a person's request through the Assistant or BotOps is sent by a bot, so it is checked.
    Without one, the Finance principal says: a bot (for a person when `for_actor` is one), or the system for a bot."""
    if route is not None:
        return route if H.is_bot(route[1]) else None
    actor, for_actor = str(actor or ""), str(for_actor or "")
    if H.is_bot(actor):
        return (for_actor if H.is_human(for_actor) else actor), actor, ""
    if actor == FINANCE_SYSTEM and H.is_bot(for_actor):
        return for_actor, for_actor, ""
    return None


def _chain(c: sqlite3.Connection, recipient_id: str) -> list[str]:
    """The recipient and every version it replaced: a contact's payment_ref may name an older one."""
    ids: list[str] = []
    at = recipient_id
    while at and at not in ids and len(ids) < 100:
        ids.append(at)
        row = c.execute("SELECT replaces_id FROM fin_recipients WHERE id=?", (at,)).fetchone()
        at = str(row[0]) if row and row[0] else ""
    return ids


def payee(c: sqlite3.Connection, recipient_id: str, book_id: str) -> tuple[list[str], list[str]]:
    """(targets, contacts): the entities the recipient is, and the addresses it has. Empty when the Directory does not
    know the payee."""
    rec = c.execute("SELECT * FROM fin_recipients WHERE id=?", (recipient_id,)).fetchone()
    if rec is None:
        return [], []
    chain = _chain(c, str(rec["id"]))
    contact_ids = {str(rec["contact_id"] or "")} - {""}
    contact_ids |= {
        str(r[0])
        for r in c.execute(
            "SELECT id FROM fin_contacts WHERE payment_ref IN (SELECT value FROM json_each(?))", (json.dumps(chain),)
        )
    }
    names: set[str] = {str(rec["name"])}
    emails: set[str] = set()
    targets: set[str] = set()
    for row in c.execute(
        "SELECT id, name, emails FROM fin_contacts WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(contact_ids)),),
    ):
        names.add(str(row["name"]))
        try:
            emails |= {str(e) for e in cast(list[object], json.loads(row["emails"] or "[]")) if e}
        except (TypeError, ValueError):
            pass
    for rid in chain:  # the address recipient_add sent Mercury (not on the recipient row itself)
        row = c.execute("SELECT payload FROM fin_outbox WHERE idempotency_key=?", (f"recipient-{rid}",)).fetchone()
        if row:
            found = cast(dict[str, object], json.loads(row[0] or "{}") or {}).get("email")
            if found:
                emails.add(str(found))
    # A contact already folded into the Directory is its own entity.
    targets |= {
        str(r[0])
        for r in c.execute(
            "SELECT id FROM entities WHERE id IN (SELECT value FROM json_each(?)) AND deleted_at IS NULL",
            (json.dumps(sorted(contact_ids)),),
        )
    }
    # A name is matched only as an identifier scoped to this book: two outside parties can share a name company-wide.
    keys = sorted({n.strip().lower() for n in names if n.strip()})
    targets |= {
        str(r[0])
        for r in c.execute(
            "SELECT entity_id FROM entity_identifiers WHERE kind='name' AND scope_id=? AND scope_type IS NOT NULL "
            "AND normalized_value IN (SELECT value FROM json_each(?))",
            (book_id, json.dumps(keys)),
        )
    }
    targets |= {
        str(r[0])
        for r in c.execute(
            "SELECT e.entity_id FROM fin_external_refs f JOIN entity_external_refs e "
            "ON e.connection_id=f.connection_id AND e.external_id=f.external_id AND e.status='active' "
            "WHERE f.local_table='fin_contacts' AND f.status='active' AND f.local_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(contact_ids)),),
        )
    }
    followed = cast(list[str], ES._follow(c, sorted(targets)))  # pyright: ignore[reportPrivateUsage]
    return sorted(followed), sorted({e.strip().lower() for e in emails if e.strip()})


def digest(body: Mapping[str, object]) -> str:
    """One payment's identity for a hold and its waiver: who is paid, how much, from where and for what."""
    keys = ("recipient_id", "recipient_version", "amount", "money_account_id", "obligation", "account_id", "memo")
    raw = json.dumps([str(body.get(k) or "") for k in keys], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _hold_row(c: sqlite3.Connection, author: str, key: str, state: str) -> dict[str, Any] | None:
    used = "AND json_extract(verdict,'$.used') IS NULL " if state == "sent" else ""
    row = c.execute(
        f"SELECT * FROM org_rule_holds WHERE author=? AND operation='{OPERATION}' AND state=? "
        f"AND json_extract(payload,'$.replay.payment_digest')=? {used}ORDER BY created_at DESC LIMIT 1",
        (author, state, key),
    ).fetchone()
    return dict(row) if row else None


def prepare(
    c: sqlite3.Connection, found: Found | None, body: Mapping[str, object], book_id: str, summary: str
) -> Prep | None:
    """Read only, inside the payment's write: what `settle` does. None to pay; else {"verdict", "key", "summary",
    "waiting", "approved"}. `found` is `checked`'s answer."""
    if found is None or not OR.in_force(c) or not DR.in_force(c):
        return None
    author, sender, via = found
    key = digest(body)
    try:
        targets, contacts = payee(c, str(body.get("recipient_id") or ""), book_id)
    except Exception as exc:  # noqa: BLE001  (fail closed: a payee that cannot be read holds the payment)
        return {"failed": type(exc).__name__, "found": found, "key": key, "summary": summary}
    verdict: dict[str, Any] | None = OR.structured_verdict(
        c,
        author,
        sender,
        via,
        OPERATION,
        [],
        targets=targets,
        contacts=contacts,
        context={"payment": {k: body.get(k) for k in ("recipient_id", "amount", "obligation") if body.get(k)}},
    )
    if verdict is None or not OR.holds(verdict):
        return None  # a Warn rule does not stop a payment: nobody reads a warning line on a bank transfer
    approved = _hold_row(c, author, key, "sent")
    if approved is not None:
        # A waived payment passes once, unless a rule it was not waived on holds it now (or the check failed).
        waived = {str(x) for x in cast(list[object], json.loads(approved["rule_ids"] or "[]"))}
        now = {str(r["id"]) for r in cast(list[dict[str, Any]], verdict["held"])}
        if not verdict["error"] and now <= waived:
            return {"approved": approved, "verdict": None, "key": key, "summary": summary, "waiting": None}
    return {
        "approved": approved,
        "verdict": verdict,
        "key": key,
        "summary": summary,
        "waiting": _hold_row(c, author, key, "held"),
    }


def settle(c: sqlite3.Connection, prep: Prep | None) -> dict[str, Any] | None:
    """Inside the write: None when the payment goes on (a waiver spent), else the hold's view. A payment held already
    answers with that hold, so one payment has one hold and one review."""
    if prep is None:
        return None
    if prep.get("failed"):
        return _hold_failed(c, prep)
    if prep["verdict"] is None:
        spent = c.execute(
            "UPDATE org_rule_holds SET verdict=json_set(verdict,'$.used',1) WHERE id=? "
            "AND json_extract(verdict,'$.used') IS NULL",
            (prep["approved"]["id"],),
        ).rowcount
        return None if spent == 1 else {"id": prep["approved"]["id"], "text": "This payment's waiver was already used."}
    if prep["approved"] is not None:  # a newer rule holds it: the old waiver is spent
        c.execute(
            "UPDATE org_rule_holds SET verdict=json_set(verdict,'$.used','superseded') WHERE id=?",
            (prep["approved"]["id"],),
        )
    if prep["waiting"] is not None:
        hold = prep["waiting"]
        return {
            "id": hold["id"],
            "outcome": "blocked",
            "operation": OPERATION,
            "text": "This payment is still held. " + OR.appeal_line(c, hold["id"], hold["reviewer"], hold["author"]),
        }
    gate: dict[str, Any] | None = OR.apply(c, prep["verdict"], prep["summary"], {"payment_digest": prep["key"]})
    return gate["held"] if gate and gate.get("held") else None


def _hold_failed(c: sqlite3.Connection, prep: Prep) -> dict[str, Any]:
    """The hold of a payment whose payee could not be read: the check did not complete, so it is held (fail closed),
    with the error's class as its reason. The owner and the Admins may waive it."""
    author, sender, via = prep["found"]
    name = prep["failed"]
    reason = f"The Directory rules could not be checked ({name}), so this payment is held until a person checks it."
    rule: dict[str, Any] = {
        "id": "",
        "text": reason,
        "if_broken": "block",
        "reviewer": None,
        "suggested_by": "",
        "version": 0,
    }
    verdict: dict[str, Any] = {
        "check": "structured",
        "author": author,
        "sender": sender or author,
        "via": via or "",
        "operation": OPERATION,
        "recipients": [],
        "rules": [rule],
        "broken": [],
        "matched": [],
        "digest": "",
        "cached": False,
        "call": None,
        "error": name,
    }
    gate: dict[str, Any] = OR._write_hold(  # pyright: ignore[reportPrivateUsage]
        c,
        verdict,
        [rule],
        rule,
        "block",
        False,
        False,
        prep["summary"],
        {"payment_digest": prep["key"]},
        False,
        check="structured",
        error=name,
    )
    return cast(dict[str, Any], gate["held"])


@OR.replayer(OPERATION)
def _replay_payment(
    c: sqlite3.Connection, bot: object, hold: Mapping[str, Any], replay: object, ingest: object, gate: object = None
) -> dict[str, bool]:
    """A held payment was never made: Send anyway lets that exact payment pass its next check, once, and tells the bot
    to make it again."""
    OR._tell_bot(  # pyright: ignore[reportPrivateUsage]
        c,
        hold["author"],
        f"The payment you were told was held may go now: make that exact payment again (hold {hold['id']}).",
    )
    return {"approved": True}
