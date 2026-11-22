# pyright: strict
"""The Directory's store (track 3): every write to the entity tables, and the reads, which all go through the one read rule
(backend/entities/visible.py).

Each function takes the caller's connection, as Market's do (backend/market.py). A write runs inside the writer gate the
caller holds (store.transaction()), and the caller commits. A write names its actor, and hubdb's writer checks it first:
someone off the roster, or a quarantined bot, is refused with the rule that held it. A read names its reader (an actor
string, or visible.SYSTEM) and returns only what that reader may see. A read that finds nothing returns None or [].

Ids are text. An entity keeps the id it has (Market's ids, company/self included); a new entity is type/slug, with a number
on the end when that is taken. Other rows get a prefixed random id.
"""

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, NoReturn, cast

from backend import hubdb as H
from backend.entities import visible as V
from backend.slugs import slugify

SELF_ID = "company/self"  # Market's id for our company (OWN_COMPANY_ID in backend/market.py); the self row keeps it
VALUE_TYPES = (
    "text",
    "number",
    "currency",
    "date",
    "timestamp",
    "checkbox",
    "select",
    "status",
    "email",
    "phone",
    "domain",
    "url",
    "personal_name",
    "rating",
    "actor",
)
TEXT_TYPES = ("text", "email", "phone", "domain", "url", "personal_name", "actor")
NUMBER_TYPES = ("number", "rating")
VALUE_COLUMNS = ("text_value", "number_value", "currency", "date_value", "timestamp_value", "option_key")
KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
LIMIT = 50  # the most rows a list returns (spec §9.1 item 9)
COUNT_CAP = 1000  # counts stop here and say they are approximate (spec §9.1 item 7)
CARD_LINKS = 30  # the links a card shows (spec §6 keeps `get` under about 2k tokens)
SHARED_PLACES_ACTOR = "places-pack"  # who wrote a place from the shared places tree (places/adapter.py ACTOR)
WORKS_AT = ("works_at", "member_of")  # the links a person's organization is read from, for a list's Organization column
# The tables a merge moves an entity's rows from. entity_sources skips a source the kept entity already has.
MOVES = {
    # What a Finance book row put on the entity stays where its book row points: the book's vendor names (scope_type
    # finance_book) and the billing emails it keeps current (source_type finance_book, finance/store/contacts.py).
    "entity_identifiers": (
        "SELECT id FROM entity_identifiers WHERE entity_id = :drop AND scope_type IS NOT 'finance_book' "
        "AND source_type IS NOT 'finance_book'"
    ),
    "entity_notes": "SELECT id FROM entity_notes WHERE entity_id = :drop",
    "entity_mentions": "SELECT id FROM entity_mentions WHERE entity_id = :drop",
    "entity_activities": "SELECT id FROM entity_activities WHERE entity_id = :drop",
    "conversations": "SELECT id FROM conversations WHERE entity_id = :drop",
    "entity_external_refs": "SELECT id FROM entity_external_refs WHERE entity_id = :drop",
    "entity_sources": (
        "SELECT s.id FROM entity_sources s WHERE s.entity_id = :drop AND NOT EXISTS (SELECT 1 FROM entity_sources k "
        "WHERE k.entity_id = :keep AND k.source_type = s.source_type AND k.source_id = s.source_id)"
    ),
    "tasks": "SELECT id FROM tasks WHERE entity_id = :drop",
}
# The pages linked to the dropped entity (page_links names it as a target, entity:<id>). A related link moves unless the
# kept entity links that page too; the memory link moves only when the kept entity has none (one memory page each).
# What stays, stays with the merged row, so unmerge puts back exactly what moved.
PAGE_LINK_MOVES = (
    "SELECT p.id FROM page_links p WHERE p.target = :drop_target AND ("
    "(p.role = 'related' AND NOT EXISTS (SELECT 1 FROM page_links k WHERE k.target = :keep_target "
    "AND k.role = 'related' AND k.page_id = p.page_id)) OR "
    "(p.role = 'memory' AND NOT EXISTS (SELECT 1 FROM page_links k WHERE k.target = :keep_target AND k.role = 'memory')))"
)
# A single-value field the kept entity lacks moves; one it has stays on the merged row (which redirects to the kept one).
Row = dict[str, Any]  # one row as a read returns it: column name to value


# hubdb's writer gate, refusal and actor helpers are not in the strict set, so their parameters are untyped. store.py
# calls them through these wrappers, which make the same calls and keep the untyped access in one place.
def _writer(c: sqlite3.Connection, actor: str) -> None:
    H._writer(c, actor)  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]


def _refuse(c: sqlite3.Connection, actor: str, rule: str, detail: str) -> NoReturn:
    H.refuse(c, actor, rule, detail)  # pyright: ignore[reportUnknownMemberType]


def _actor_kind(actor: str) -> str | None:
    return H.actor_kind(actor)  # pyright: ignore[reportUnknownMemberType]


def _actor_id(actor: str) -> str | None:
    return H.actor_id(actor)  # pyright: ignore[reportUnknownMemberType]


VALUE_MOVES = (
    "SELECT v.id FROM entity_values v WHERE v.entity_id = :drop AND (v.single_value = 0 OR NOT EXISTS "
    "(SELECT 1 FROM entity_values k WHERE k.entity_id = :keep AND k.entity_attribute_id = v.entity_attribute_id "
    "AND k.single_value = 1))"
)


# ---------------------------------------------------------------------------------------------------- small helpers
def _rows(c: sqlite3.Connection, sql: str, args: Sequence[object] | Mapping[str, object] = ()) -> list[Row]:
    cur = c.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _one(c: sqlite3.Connection, sql: str, args: Sequence[object] | Mapping[str, object] = ()) -> Row | None:
    found = _rows(c, sql, args)
    return found[0] if found else None


def _count(c: sqlite3.Connection, sql: str, args: Sequence[object] | Mapping[str, object] = ()) -> int:
    """The one number a count query returns."""
    return int(c.execute(sql, args).fetchone()[0])


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _domain(value: object) -> str:
    return str(value or "").strip().lower()


def _limit(limit: int | None) -> int:
    return max(1, min(int(limit or LIMIT), LIMIT))


def _encode(parts: Sequence[object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts).encode()).decode()


def _decode(token: object) -> Any:
    try:
        return json.loads(base64.urlsafe_b64decode(str(token).encode()).decode())
    except (ValueError, TypeError) as error:
        raise ValueError("the cursor is not one this list returned") from error


def _key(c: sqlite3.Connection, actor: str, key: object, what: str) -> str:
    text = str(key or "").strip()
    if not KEY.match(text):
        _refuse(c, actor, "kind", f"a {what} key is lowercase letters, digits and underscores, starting with a letter")
    return text


def _required(c: sqlite3.Connection, actor: str, value: object, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        _refuse(c, actor, "lint", f"give the {what}")
    return text


def _type(c: sqlite3.Connection, actor: str, key: str) -> Row:
    row = _one(c, "SELECT * FROM entity_types WHERE key = ?", (key,))
    if row is None:
        _refuse(c, actor, "kind", f"{key} is not an entity type; see tico contacts types")
    if row["archived_at"]:
        _refuse(c, actor, "kind", f"{key} is archived")
    return row


def _attribute(c: sqlite3.Connection, actor: str, type_key: str, key: str) -> Row:
    row = _one(
        c, "SELECT * FROM entity_attributes WHERE entity_type = ? AND key = ? AND archived_at IS NULL", (type_key, key)
    )
    if row is None:
        _refuse(c, actor, "kind", f"{type_key} has no attribute {key}; add it with tico contacts attribute add")
    return row


def _live(c: sqlite3.Connection, actor: str, entity_id: str, reader: str | None = None) -> Row:
    """The entity a write names, when the writer can see it (or `reader`, the request's effective reader, when the
    caller names one). One it cannot see is not found, the same as one that is not there, so a write never confirms that
    a hidden entity exists."""
    row = _one(c, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if row is None or not V.visible(c, reader or actor, entity_id):
        _refuse(c, actor, "not_found", f"no entity {entity_id}")
    if row["status"] == "merged":
        _refuse(c, actor, "merged", f"{entity_id} is merged into {row['merged_into_id']}; write to that one")
    return row


def _new_entity_id(c: sqlite3.Connection, type_key: str, name: str) -> str:
    base = f"{type_key}/{slugify(str(name), fallback='entity')}"
    candidate, n = base, 2
    while c.execute("SELECT 1 FROM entities WHERE id = ?", (candidate,)).fetchone():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _visible_rows(c: sqlite3.Connection, r: V.Reader, ids: Sequence[str]) -> list[Row]:
    if not ids:
        return []
    cl, params = V.clause(r)
    return _rows(
        c,
        f"SELECT e.* FROM entities e WHERE e.id IN (SELECT value FROM json_each(?)) AND {cl} ORDER BY e.name_key, e.id",
        [json.dumps(ids), *params],
    )


def follow_merges(c: sqlite3.Connection, ids: Iterable[str]) -> list[str]:
    """Each id, or the last kept entity in its merge chain."""
    out: list[str] = []
    for entity_id in ids:
        current = entity_id
        seen: set[str] = set()
        while current not in seen:
            seen.add(current)
            row = _one(c, "SELECT status, merged_into_id FROM entities WHERE id = ?", (current,))
            if not row or row["status"] != "merged" or not row["merged_into_id"]:
                break
            current = row["merged_into_id"]
        else:
            raise ValueError("An entity's merge chain contains a cycle")
        out.append(current)
    return list(dict.fromkeys(out))


def _today() -> str:
    return H.now()[:10]


def _refresh_self_keys(c: sqlite3.Connection, entity_id: str | None) -> None:
    """self_link_keys: every current link from this entity to self, e.g. "customer_of vendor_to"."""
    sid = self_id(c)
    if sid is None or not entity_id:
        return
    keys = [
        row[0]
        for row in c.execute(
            "SELECT DISTINCT link_key FROM entity_links WHERE from_entity_id = ? AND to_entity_id = ? "
            "AND (valid_until_on IS NULL OR valid_until_on > ?)",
            (entity_id, sid, _today()),
        )
    ]
    c.execute("UPDATE entities SET self_link_keys = ? WHERE id = ?", (" ".join(sorted(keys)), entity_id))


# ----------------------------------------------------------------------------------------------------------- self
def self_id(c: sqlite3.Connection) -> str | None:
    """Our company's id. Market makes company/self at setup; the first read that finds it unflagged flags it self."""
    row = c.execute("SELECT id FROM entities WHERE self = 1").fetchone()
    if row:
        return row[0]
    if c.execute("SELECT 1 FROM entities WHERE id = ?", (SELF_ID,)).fetchone():
        c.execute("UPDATE entities SET self = 1 WHERE id = ?", (SELF_ID,))
        _self_flagged(c)
        return SELF_ID
    return None


def _self_flagged(c: sqlite3.Connection) -> None:
    """Self was just made or flagged: Finance's contacts that came before it link to it now (vendor_to / customer_of)."""
    from backend.finance import directory as finance_directory

    finance_directory.link_self(c)


def touches_self(c: sqlite3.Connection, *, link_id: str = "", entity_ids: Sequence[str] = ()) -> bool:
    """Whether a link (by id) or a pair of ends has our company at one end: changing where we operate is an admin's."""
    if link_id:
        row = _one(c, "SELECT from_entity_id, to_entity_id FROM entity_links WHERE id = ?", (link_id,))
        entity_ids = [row["from_entity_id"], row["to_entity_id"]] if row else []
    ids = [i for i in entity_ids if i]
    if not ids:
        return False
    marks = ", ".join("?" * len(ids))
    return _one(c, f"SELECT 1 AS found FROM entities WHERE self = 1 AND id IN ({marks})", ids) is not None


def link_key_of(c: sqlite3.Connection, link_id: str) -> str | None:
    row = _one(c, "SELECT link_key FROM entity_links WHERE id = ?", (link_id,))
    return row["link_key"] if row else None


def _set_kind(c: sqlite3.Connection, entity_id: str, kind: str, source_type: str) -> None:
    """An organization's kind, when it has none. A kind already set is left alone."""
    c.execute(
        "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, source_id, "
        "confidence, updated_at) SELECT ?, ?, 'attr_organization_kind', 1, ?, ?, '', 0.9, ? WHERE NOT EXISTS "
        "(SELECT 1 FROM entity_values WHERE entity_id = ? AND entity_attribute_id = 'attr_organization_kind')",
        (_id("val"), entity_id, kind, source_type, H.now(), entity_id),
    )


def ensure_self(c: sqlite3.Connection, *, company_name: str | None, domain: str = "") -> str:
    """Our company: the one organization flagged self (a partial unique index). A row Market already holds under
    company/self is flagged, not replaced. Its name is set only when the row is new; its domain only when it has none."""
    domain = _domain(domain)
    current = self_id(c)
    if current is None:
        now = H.now()
        if c.execute("SELECT 1 FROM entities WHERE id = ?", (SELF_ID,)).fetchone():
            c.execute(
                "UPDATE entities SET self = 1, type = 'organization', updated_at = ? WHERE id = ?", (now, SELF_ID)
            )
        else:
            name = str(company_name or "").strip() or "Our company"
            c.execute(
                "INSERT INTO entities (id, type, name, name_key, self, audience, primary_domain, created_by, "
                "created_by_type, created_by_id, created_at, updated_at) "
                "VALUES (?, 'organization', ?, ?, 1, 'company', ?, 'system', 'system', '', ?, ?)",
                (SELF_ID, name, name.lower(), domain or None, now, now),
            )
        current = SELF_ID
        _self_flagged(c)
    if domain:
        c.execute(
            "UPDATE entities SET primary_domain = ? WHERE id = ? AND COALESCE(primary_domain, '') = ''",
            (domain, current),
        )
    # The company's primary domain is an identifier too, as upsert makes it for any entity: without one, a reference by
    # domain (get, an upsert's match) misses our own company and would make a duplicate of it.
    primary = c.execute("SELECT primary_domain FROM entities WHERE id = ?", (current,)).fetchone()[0]
    if (
        primary
        and not c.execute(
            "SELECT 1 FROM entity_identifiers WHERE entity_id = ? AND kind = 'domain' AND normalized_value = ?",
            (current, primary),
        ).fetchone()
    ):
        c.execute(
            "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
            "VALUES (?, 'domain', ?, ?, 1, 0)",
            (current, primary, primary),
        )
    _set_kind(c, current, "company", "system")
    return current


def backfill_kinds(c: sqlite3.Connection) -> None:
    """Organizations Market wrote before they had a kind are companies. Idempotent: a row with a kind is left alone."""
    c.execute(
        "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, source_id, "
        "confidence, updated_at) SELECT 'val_' || e.id, e.id, 'attr_organization_kind', 1, 'company', "
        "'market', '', 0.9, ? FROM entities e WHERE e.type = 'organization' AND NOT EXISTS (SELECT 1 FROM entity_values v "
        "WHERE v.entity_id = e.id AND v.entity_attribute_id = 'attr_organization_kind')",
        (H.now(),),
    )


def boot(c: sqlite3.Connection) -> None:
    """The Directory's boot step (called from Store.initialize after Market's seed). Idempotent. It makes no company: Market
    makes company/self at setup, and this flags it as self once it exists and no other row is self. The kind of each
    organization Market wrote without one is backfilled."""
    # A database the Directory's migration has not reached has no Directory tables yet (a test replays migrations from
    # an old baseline); there is nothing to flag or backfill until the migration has made them.
    if c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entity_values'").fetchone() is None:
        return
    backfill_kinds(c)
    self_id(c)


def seed_link(c: sqlite3.Connection, *, from_id: str, key: str, to_id: str, confidence: float, actor: str) -> bool:
    """A reference link a server job writes once, as the places pack writes located_in: no writer gate and no history row,
    because it is reference data. True when the link was added."""
    cursor = c.execute(
        "INSERT INTO entity_links (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on, "
        "confidence, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
        "SELECT :id, :src, :key, :dst, NULL, NULL, :confidence, '{}', :actor, 'system', :actor, :now, :now "
        "WHERE NOT EXISTS (SELECT 1 FROM entity_links WHERE from_entity_id = :src AND link_key = :key "
        "AND to_entity_id = :dst)",
        {
            "id": _id("lnk"),
            "src": from_id,
            "key": key,
            "dst": to_id,
            "confidence": confidence,
            "actor": actor,
            "now": H.now(),
        },
    )
    return cursor.rowcount == 1


# --------------------------------------------------------------------------------------------- values and kinds
def _number(c: sqlite3.Connection, actor: str, attr: Row, raw: object) -> float:
    try:
        if not isinstance(raw, (str, int, float)):
            raise TypeError(f"{type(raw).__name__} is not a number")
        number = float(raw)
    except (TypeError, ValueError):
        _refuse(c, actor, "kind", f"{attr['key']} needs a number")
    if not math.isfinite(number):
        _refuse(c, actor, "kind", f"{attr['key']} needs a finite number")
    return number


def _coerce(c: sqlite3.Connection, actor: str, attr: Row, raw: object) -> dict[str, Any]:
    """A raw value as the columns it is stored in, and its canonical form (what history and a read show)."""
    vt = attr["value_type"]
    if vt in TEXT_TYPES:
        text = "" if raw is None else str(raw).strip()
        if vt == "email":
            text = text.lower()
        if vt == "domain":
            text = _domain(text)
        if not text:
            _refuse(c, actor, "kind", f"{attr['key']} needs a value")
        return {"cols": {"text_value": text}, "canonical": text}
    if vt in NUMBER_TYPES:
        number = _number(c, actor, attr, raw)
        return {"cols": {"number_value": number}, "canonical": number}
    if vt == "checkbox":
        flag = str(raw).strip().lower() in ("1", "true", "yes", "y") if isinstance(raw, str) else bool(raw)
        return {"cols": {"number_value": 1.0 if flag else 0.0}, "canonical": flag}
    if vt == "currency":
        amount: object = raw
        code: object = "USD"
        if isinstance(raw, dict):
            fields = cast(dict[str, object], raw)
            amount, code = fields.get("amount"), fields.get("currency")
        number = _number(c, actor, attr, amount)
        code = str(code or "USD").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            _refuse(c, actor, "kind", f"{attr['key']} needs a three-letter currency code")
        return {"cols": {"number_value": number, "currency": code}, "canonical": {"amount": number, "currency": code}}
    if vt == "date":
        text = str(raw or "").strip()
        try:
            date.fromisoformat(text)
        except ValueError:
            _refuse(c, actor, "kind", f"{attr['key']} needs a date as YYYY-MM-DD")
        return {"cols": {"date_value": text}, "canonical": text}
    if vt == "timestamp":
        try:
            stamp = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00")).isoformat()
        except ValueError:
            _refuse(c, actor, "kind", f"{attr['key']} needs a timestamp")
        return {"cols": {"timestamp_value": stamp}, "canonical": stamp}
    if vt in ("select", "status"):
        key = str(raw or "").strip()
        options = {option["key"] for option in json.loads(attr["options"] or "[]")}
        if key not in options:
            _refuse(c, actor, "kind", f"{key} is not an option of {attr['key']}")
        return {"cols": {"option_key": key}, "canonical": key}
    _refuse(c, actor, "kind", f"{vt} is not a value type")


def _value_of(row: Row, value_type: str) -> object:
    """The canonical value a stored row holds (the inverse of _coerce)."""
    if value_type in TEXT_TYPES:
        return row["text_value"]
    if value_type in NUMBER_TYPES:
        return row["number_value"]
    if value_type == "checkbox":
        return bool(row["number_value"])
    if value_type == "currency":
        return {"amount": row["number_value"], "currency": row["currency"]}
    if value_type == "date":
        return row["date_value"]
    if value_type == "timestamp":
        return row["timestamp_value"]
    if value_type in ("select", "status"):
        return row["option_key"]
    return None


def _cols(values: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(values.get(name) for name in VALUE_COLUMNS)


def _set_value(
    c: sqlite3.Connection,
    actor: str,
    entity_id: str,
    attr: Row,
    raw: object,
    *,
    source_type: str,
    source_id: str,
    evidence_id: str | None,
    confidence: float,
) -> str:
    """Write one value. A single-value field keeps its current row (the new value replaces it) and its old value goes to
    history; a multiple field adds a row. An unchanged value only refreshes its source."""
    now = H.now()
    value = _coerce(c, actor, attr, raw)
    vt, canonical, cols = attr["value_type"], value["canonical"], value["cols"]
    if attr["unique_value"] and vt in TEXT_TYPES + NUMBER_TYPES:
        column = "text_value" if vt in TEXT_TYPES else "number_value"
        clash = _one(
            c,
            f"SELECT entity_id FROM entity_values WHERE entity_attribute_id = ? AND entity_id <> ? AND {column} = ? LIMIT 1",
            (attr["id"], entity_id, cols[column]),
        )
        if clash:
            _refuse(c, actor, "duplicate", f"{attr['key']} {cols[column]} is already on {clash['entity_id']}")
    if attr["multiple"]:
        for row in _rows(
            c,
            "SELECT * FROM entity_values WHERE entity_id = ? AND entity_attribute_id = ? AND single_value = 0",
            (entity_id, attr["id"]),
        ):
            if _value_of(row, vt) == canonical:
                return row["id"]
        row_id = _id("val")
        c.execute(
            "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, text_value, number_value, "
            "currency, date_value, timestamp_value, option_key, source_type, source_id, evidence_id, confidence, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row_id, entity_id, attr["id"], *_cols(cols), source_type, source_id, evidence_id, confidence, now),
        )
        return row_id
    current = _one(
        c,
        "SELECT * FROM entity_values WHERE entity_id = ? AND entity_attribute_id = ? AND single_value = 1",
        (entity_id, attr["id"]),
    )
    if current and _value_of(current, vt) == canonical:
        c.execute(
            "UPDATE entity_values SET source_type = ?, source_id = ?, evidence_id = ?, confidence = ? WHERE id = ?",
            (source_type, source_id, evidence_id, confidence, current["id"]),
        )
        return current["id"]
    if current:
        c.execute(
            "INSERT INTO entity_value_history (id, entity_id, entity_attribute_id, value, source_type, source_id, "
            "evidence_id, confidence, active_from, active_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _id("hist"),
                entity_id,
                attr["id"],
                json.dumps(_value_of(current, vt), sort_keys=True),
                current["source_type"],
                current["source_id"],
                current["evidence_id"],
                current["confidence"],
                current["updated_at"],
                now,
            ),
        )
    row_id = current["id"] if current else _id("val")
    c.execute(
        "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, text_value, number_value, currency, "
        "date_value, timestamp_value, option_key, source_type, source_id, evidence_id, confidence, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (entity_id, entity_attribute_id) WHERE single_value = 1 DO UPDATE SET "
        "text_value = excluded.text_value, number_value = excluded.number_value, currency = excluded.currency, "
        "date_value = excluded.date_value, timestamp_value = excluded.timestamp_value, "
        "option_key = excluded.option_key, source_type = excluded.source_type, source_id = excluded.source_id, "
        "evidence_id = excluded.evidence_id, confidence = excluded.confidence, updated_at = excluded.updated_at",
        (row_id, entity_id, attr["id"], *_cols(cols), source_type, source_id, evidence_id, confidence, now),
    )
    return row_id


def _check_evidence(c: sqlite3.Connection, actor: str, evidence_id: str | None) -> None:
    if evidence_id and not c.execute("SELECT 1 FROM market_evidence WHERE id = ?", (evidence_id,)).fetchone():
        _refuse(c, actor, "evidence", f"no evidence {evidence_id}; cite a row that exists")


# ------------------------------------------------------------------------------------------------------- writes
def _match(c: sqlite3.Connection, type_key: str, match: Mapping[str, object]) -> list[str]:
    """The live entities of one type a match names: by email, domain or name (an identifier), or by id."""
    if match.get("id"):
        rows = _rows(
            c, "SELECT id FROM entities WHERE id = ? AND type = ? AND status <> 'merged'", (match["id"], type_key)
        )
        return [row["id"] for row in rows]
    found: list[str] = []
    for kind in ("email", "domain", "name"):
        value = match.get(kind)
        if not value:
            continue
        text = str(value).strip().lower()
        if kind == "name":
            found += [
                row["entity_id"]
                for row in _rows(
                    c,
                    "SELECT DISTINCT i.entity_id FROM entity_identifiers i JOIN entities e ON e.id = i.entity_id "
                    "WHERE i.normalized_value = ? AND e.type = ? AND e.status <> 'merged'",
                    (text, type_key),
                )
            ]
        else:
            found += [
                row["entity_id"]
                for row in _rows(
                    c,
                    "SELECT DISTINCT i.entity_id FROM entity_identifiers i JOIN entities e ON e.id = i.entity_id "
                    "WHERE i.normalized_value = ? AND i.kind = ? AND e.type = ? AND e.status <> 'merged'",
                    (text, kind, type_key),
                )
            ]
        if found:
            break
    return list(dict.fromkeys(found))


def upsert(
    c: sqlite3.Connection,
    actor: str,
    type_key: str,
    *,
    match: Mapping[str, object],
    values: Mapping[str, object] | None = None,
    name: object = None,
    evidence_id: str | None = None,
    source_type: str = "",
    source_id: str = "",
    confidence: float = 0.6,
) -> dict[str, Any]:
    """Create or update one entity of a type. `match` names it: {"id"}, {"email"}, {"domain"} or {"name"}. Several matches
    are refused as ambiguous, never guessed. A new entity needs a name (given, or the match value)."""
    _writer(c, actor)
    _type(c, actor, type_key)
    _check_evidence(c, actor, evidence_id)
    match = {k: v for k, v in dict(match or {}).items() if v}
    values = dict(values or {})
    if "name" in values:
        name = name or values.pop("name")
    reader = V.reader(c, actor)
    matched = _match(c, type_key, match)
    found = [entity_id for entity_id in matched if V.visible(c, reader, entity_id)]
    if len(found) < len(matched):
        # A match the writer cannot see is refused without naming it: its id and name are not the writer's to read.
        _refuse(c, actor, "not_found", "a match exists that you cannot write to; a person can change it")
    if len(found) > 1:
        _refuse(c, actor, "ambiguous", f"{', '.join(found)} all match; name one by id")
    # An identifier erased at its owner's request is not written again: a new entity made from it is refused, and so is
    # a value that sets it. The refusal names no identifier, so the refusal audit keeps none.
    checks = _email_and_phone_values(c, type_key, values)
    if not found and match.get("email"):
        checks.append(("email", match["email"]))
    for kind, value in checks:
        if is_suppressed(c, kind, value):
            _refuse(c, actor, "suppressed", "That was erased at its owner's request; it is not created again")
    created = not found
    if found:
        entity_id = found[0]
        if name:
            label = _required(c, actor, name, "name")
            c.execute(
                "UPDATE entities SET name = ?, name_key = ?, updated_at = ? WHERE id = ?",
                (label, label.lower(), H.now(), entity_id),
            )
    else:
        label = _required(
            c, actor, name or match.get("name") or match.get("email") or match.get("domain") or "", "name"
        )
        entity_id = _new_entity_id(c, type_key, label)
        now = H.now()
        kind_, ident = _actor_kind(actor), _actor_id(actor)
        primary_email = str(match.get("email") or "").strip().lower() or None
        primary_domain = _domain(match.get("domain")) or None
        c.execute(
            "INSERT INTO entities (id, type, name, name_key, primary_email, primary_domain, audience, created_by, "
            "created_by_type, created_by_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'company', ?, ?, ?, ?, ?)",
            (
                entity_id,
                type_key,
                label,
                label.lower(),
                primary_email,
                primary_domain,
                actor,
                kind_ or "",
                ident or "",
                now,
                now,
            ),
        )
        for kind, value in (("email", primary_email), ("domain", primary_domain)):
            if value:
                c.execute(
                    "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
                    "VALUES (?, ?, ?, ?, 1, 0)",
                    (entity_id, kind, value, value),
                )
        if type_key == "organization" and "kind" not in values:
            _set_kind(c, entity_id, "company", "system")
    changed: list[str] = []
    for key, raw in values.items():
        attr = _attribute(c, actor, type_key, key)
        if attr["owner_type"] == "connection" and actor != f"connection:{attr['owner_id']}":
            _refuse(c, actor, "owner", f"{key} comes from its connection; change it there")
        _set_value(
            c,
            actor,
            entity_id,
            attr,
            raw,
            source_type=source_type,
            source_id=source_id,
            evidence_id=evidence_id,
            confidence=confidence,
        )
        changed.append(key)
    return {"id": entity_id, "created": created, "changed": sorted(changed)}


def link(
    c: sqlite3.Connection,
    actor: str,
    from_id: str,
    link_key: str,
    to_id: str,
    *,
    valid_from_on: str | None = None,
    valid_until_on: str | None = None,
    confidence: float = 0.6,
    source_type: str = "",
    source_id: str = "",
    evidence_id: str | None = None,
    properties: Mapping[str, object] | None = None,
) -> str:
    """A typed, dated link between two entities. The link type says which ends it may join. A symmetric link is stored
    once, with its ends in order. A link already current is returned, not written twice."""
    _writer(c, actor)
    lt = _one(c, "SELECT * FROM link_types WHERE key = ? AND archived_at IS NULL", (link_key,))
    if lt is None:
        _refuse(c, actor, "kind", f"{link_key} is not a link type; see tico contacts link-type add")
    _check_evidence(c, actor, evidence_id)
    a, b = _live(c, actor, from_id), _live(c, actor, to_id)
    if a["id"] == b["id"]:
        _refuse(c, actor, "kind", "an entity cannot link to itself")
    if lt["from_entity_type"] and a["type"] != lt["from_entity_type"]:
        _refuse(c, actor, "kind", f"{link_key} starts at a {lt['from_entity_type']}, not a {a['type']}")
    if lt["to_entity_type"] and b["type"] != lt["to_entity_type"]:
        _refuse(c, actor, "kind", f"{link_key} ends at a {lt['to_entity_type']}, not a {b['type']}")
    start, end = (a["id"], b["id"])
    if lt["symmetric"] and end < start:
        start, end = end, start
    current = _one(
        c,
        "SELECT id FROM entity_links WHERE from_entity_id = ? AND link_key = ? AND to_entity_id = ? "
        "AND valid_until_on IS NULL",
        (start, link_key, end),
    )
    if current:
        return current["id"]
    now = H.now()
    link_id = _id("lnk")
    c.execute(
        "INSERT INTO entity_links (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on, confidence, "
        "source_type, source_id, evidence_id, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            link_id,
            start,
            link_key,
            end,
            valid_from_on,
            valid_until_on,
            confidence,
            source_type,
            source_id,
            evidence_id,
            json.dumps(properties or {}, sort_keys=True),
            actor,
            _actor_kind(actor) or "",
            _actor_id(actor) or "",
            now,
            now,
        ),
    )
    _refresh_self_keys(c, start)
    _refresh_self_keys(c, end)
    return link_id


def unlink(c: sqlite3.Connection, actor: str, link_id: str) -> str:
    """Ends a link: it stops being current today. The row stays, so its history reads."""
    _writer(c, actor)
    row = _one(c, "SELECT * FROM entity_links WHERE id = ?", (link_id,))
    reader = V.reader(c, actor)
    if row is None or not (V.visible(c, reader, row["from_entity_id"]) and V.visible(c, reader, row["to_entity_id"])):
        _refuse(c, actor, "not_found", f"no link {link_id}")
    today = _today()
    if row["valid_until_on"] is None or row["valid_until_on"] > today:
        c.execute("UPDATE entity_links SET valid_until_on = ?, updated_at = ? WHERE id = ?", (today, H.now(), link_id))
        _refresh_self_keys(c, row["from_entity_id"])
        _refresh_self_keys(c, row["to_entity_id"])
    return link_id


def note(
    c: sqlite3.Connection,
    actor: str,
    entity_id: str,
    body: object,
    *,
    audience: str = "company",
    evidence_id: str | None = None,
) -> str:
    """A note on an entity. A `me` note is visible to its author only. It never changes who sees the entity: the entity
    exists before the note, and adding a restricted source to one with none would hide it from everyone else."""
    _writer(c, actor)
    text = _required(c, actor, body, "note")
    if audience not in ("me", "company"):
        _refuse(c, actor, "kind", "a note's audience is me or company")
    _live(c, actor, entity_id)
    _check_evidence(c, actor, evidence_id)
    key = V.actor_key(c, actor) or str(actor)
    author_type, _, author_id = key.partition(":")
    now = H.now()
    note_id = _id("note")
    c.execute(
        "INSERT INTO entity_notes (id, entity_id, author_type, author_id, body, audience, pinned_at, evidence_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (note_id, entity_id, author_type, author_id, text, audience, evidence_id, now),
    )
    return note_id


def add_source(
    c: sqlite3.Connection, actor: str, entity_id: str, source_type: str, source_id: str, *, visibility: str = "company"
) -> str:
    """Records a source an entity is known from. `company` shows the entity to everyone; `restricted` shows it to readers
    the source's checker (visible.CHECKERS) names, so a restricted source needs a checker."""
    _writer(c, actor)
    _live(c, actor, entity_id)
    if visibility not in ("company", "restricted"):
        _refuse(c, actor, "kind", "a source's visibility is company or restricted")
    if visibility == "restricted" and source_type not in V.CHECKERS:
        _refuse(c, actor, "kind", f"{source_type} has no read checker, so it cannot be restricted")
    source_id = _required(c, actor, source_id, "source id")
    c.execute(
        "INSERT OR IGNORE INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_id("src"), entity_id, source_type, source_id, visibility, H.now()),
    )
    return entity_id


EXTERNAL_REF_STATUSES = ("active", "gone")  # gone: the record no longer exists outside (a card shows active ones)


def external_ref(
    c: sqlite3.Connection,
    actor: str,
    entity_id: str,
    system: object,
    external_type: object,
    external_id: object,
    *,
    connection_id: str = "",
    url: str = "",
    revision: str = "",
    status: str = "active",
) -> Row:
    """Record that an entity is a record in another system: (connection, system, type, id), the connection being the
    tool it came through ('' for none; the caller checks its reader may see it). Saving the same record again updates its
    url, revision and status and answers the same row, so a retry or a resync lands once. A record already on another
    entity is refused without naming it (that entity may be hidden from the writer). A record erased at its owner's
    request is not written again. Two connections may hold the same id as two records."""
    _writer(c, actor)
    _live(c, actor, entity_id)
    system = _required(c, actor, system, "system")
    if system.lower() == "tico":
        # Tico's own pointers (a document's file, `document_files`) are written by Tico, never through this.
        _refuse(c, actor, "kind", "system tico is Tico's own; name the other system the record is in")
    kind = _required(c, actor, external_type, "record type")
    external = _required(c, actor, external_id, "record id")
    connection_id = str(connection_id or "").strip()
    if status not in EXTERNAL_REF_STATUSES:
        _refuse(c, actor, "kind", f"a record's status is {' or '.join(EXTERNAL_REF_STATUSES)}")
    if connection_id and not c.execute("SELECT 1 FROM tools WHERE id = ?", (connection_id,)).fetchone():
        _refuse(c, actor, "connection", f"no connection {connection_id}: a connection is a tool's id")
    key = (system, kind, external, connection_id)
    where = "system = ? AND external_type = ? AND external_id = ? AND connection_id = ?"
    have = _one(c, f"SELECT * FROM entity_external_refs WHERE {where}", key)
    now = H.now()
    if have is None:
        # Erased records are suppressed by (system, type, id, connection); one erased before records had connections
        # was hashed by (system, type, id) and stays suppressed on every connection.
        legacy = "\x1f".join((system, kind, external))
        scoped = external_suppression(system, kind, external, connection_id)
        if is_suppressed(c, "external", scoped) or is_suppressed(c, "external", legacy):
            _refuse(c, actor, "suppressed", "That was erased at its owner's request; it is not created again")
        c.execute(
            "INSERT INTO entity_external_refs (id, entity_id, connection_id, system, external_type, external_id, url, "
            "revision, synced_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_id("xref"), entity_id, connection_id, system, kind, external, url.strip(), revision.strip(), now, status),
        )
    elif have["entity_id"] != entity_id:
        _refuse(c, actor, "conflict", "That record is already another contact's; merge the two if they are one")
    else:
        c.execute(
            "UPDATE entity_external_refs SET url = ?, revision = ?, status = ?, synced_at = ? WHERE id = ?",
            (url.strip(), revision.strip(), status, now, have["id"]),
        )
    found = _one(c, f"SELECT * FROM entity_external_refs WHERE {where}", key)
    assert found is not None
    return found


# Rule sources: the organizations that impose external rules. rule_sources retired into entities (migration
# 20261108044824): a rule source is an organization whose properties.rule_source says what it was (its kind, creator
# and creation time, and the id rule_sources gave it, which is the entity's own id for a new one).

RULE_SOURCE_KINDS = ("vendor", "customer", "government", "other")
RULE_SOURCE_ORG_KIND = {"vendor": "company", "customer": "company", "government": "government", "other": "company"}
RULE_SOURCE_LINK = {"vendor": "vendor_to", "customer": "customer_of", "government": "governs"}
RULE_SOURCE_ACCEPTS: dict[str, tuple[str, ...] | None] = {
    "vendor": ("company", "platform"),
    "customer": ("company", "nonprofit", "school", "platform"),
    "government": ("government", "agency", "court"),
    "other": None,
}
WALK_DEPTH = 4
_SOURCE_SQL = (
    "SELECT id, name, json_extract(properties, '$.rule_source.kind') AS kind, "
    "json_extract(properties, '$.rule_source.created_by') AS created_by, "
    "json_extract(properties, '$.rule_source.created_at') AS created_at FROM entities "
    "WHERE type = 'organization' AND json_extract(properties, '$.rule_source') IS NOT NULL "
    "AND merged_into_id IS NULL AND deleted_at IS NULL"
)
_STEPS_SQL = (
    "SELECT l.id AS id, l.from_entity_id AS node, l.link_key AS link_key, l.to_entity_id AS other FROM entity_links l "
    "JOIN link_types t ON t.key = l.link_key WHERE l.from_entity_id = :node AND t.inherits_rules = 1 "
    "AND (l.valid_from_on IS NULL OR l.valid_from_on <= :today) "
    "AND (l.valid_until_on IS NULL OR l.valid_until_on >= :today) "
    "UNION ALL SELECT l.id, l.to_entity_id, l.link_key, l.from_entity_id FROM entity_links l "
    "JOIN link_types t ON t.key = l.link_key WHERE l.to_entity_id = :node AND t.symmetric = 1 AND t.inherits_rules = 1 "
    "AND (l.valid_from_on IS NULL OR l.valid_from_on <= :today) "
    "AND (l.valid_until_on IS NULL OR l.valid_until_on >= :today)"
)


def _kind_value(c: sqlite3.Connection, entity_id: str) -> str | None:
    row = _one(
        c,
        "SELECT option_key FROM entity_values WHERE entity_id = ? AND entity_attribute_id = 'attr_organization_kind'",
        (entity_id,),
    )
    return str(row["option_key"]) if row and row["option_key"] is not None else None


def rule_sources(c: sqlite3.Connection) -> list[Row]:
    """Every rule source, by name. Company metadata: not filtered by visibility, as rule_sources was not."""
    return _rows(c, _SOURCE_SQL + " ORDER BY lower(name), id")


def rule_source_named(c: sqlite3.Connection, value: object) -> list[Row]:
    """The rule sources an id or a name (case-insensitive) matches: none, one, or several."""
    text = str(value or "").strip()
    return [r for r in rule_sources(c) if r["id"] == text or str(r["name"]).strip().lower() == text.lower()]


def rule_source_add(c: sqlite3.Connection, actor: str, name: str, kind: str) -> Row:
    """A rule source by name, found or made. A name one live organization carries is that organization, which becomes
    a rule source with its visibility left alone; a new name makes an organization the company can see. Self, several
    matches and a kind the organization cannot be are refused."""
    _writer(c, actor)
    if kind not in RULE_SOURCE_KINDS:
        _refuse(c, actor, "kind", "a rule source's kind is " + ", ".join(RULE_SOURCE_KINDS))
    key = name.strip().lower()
    found = _rows(
        c,
        "SELECT id, self, json_extract(properties, '$.rule_source') AS source FROM entities "
        "WHERE type = 'organization' AND name_key = ? AND merged_into_id IS NULL AND deleted_at IS NULL ORDER BY id",
        (key,),
    )
    if any(int(r["self"]) for r in found):
        _refuse(c, actor, "conflict", f"{name} is the company itself, so it cannot be a rule source")
    if len(found) > 1:
        ids = ", ".join(str(r["id"]) for r in found)
        _refuse(c, actor, "conflict", f"{name} matches {len(found)} organizations ({ids}): choose one by id")
    now = H.now()
    if found:
        entity_id = str(found[0]["id"])
        known = [r for r in rule_sources(c) if r["id"] == entity_id]
        if known:
            if known[0]["kind"] != kind:
                _refuse(c, actor, "conflict", f"{name} is already a {known[0]['kind']} rule source")
            return known[0]
        accepts = RULE_SOURCE_ACCEPTS[kind]
        current = _kind_value(c, entity_id)
        if accepts is not None and current is not None and current not in accepts:
            _refuse(c, actor, "conflict", f"{name} is a {current}, which a {kind} rule source cannot be")
    else:
        entity_id = _new_entity_id(c, "organization", name)
        c.execute(
            "INSERT INTO entities (id, type, name, name_key, created_by, created_by_type, created_by_id, created_at, "
            "updated_at) VALUES (?, 'organization', ?, ?, ?, ?, ?, ?, ?)",
            (entity_id, name, key, actor, _actor_kind(actor) or "system", _actor_id(actor) or "", now, now),
        )
        add_source(c, actor, entity_id, "rule_source", entity_id)
    provenance = {"id": entity_id, "kind": kind, "created_by": actor, "created_at": now}
    c.execute(
        "UPDATE entities SET properties = json_set(CASE WHEN json_valid(properties) THEN properties ELSE '{}' END, "
        "'$.rule_source', json(?)), updated_at = ? WHERE id = ?",
        (json.dumps(provenance), now, entity_id),
    )
    _set_kind(c, entity_id, RULE_SOURCE_ORG_KIND[kind], "rule_source")
    me = self_id(c)
    if me is not None and kind in RULE_SOURCE_LINK:
        seed_link(c, from_id=entity_id, key=RULE_SOURCE_LINK[kind], to_id=me, confidence=0.9, actor=actor)
    found_row = _one(c, _SOURCE_SQL + " AND id = ?", (entity_id,))
    assert found_row is not None  # written just above
    return found_row


def walk(c: sqlite3.Connection, starts: Sequence[str], depth: int = WALK_DEPTH) -> tuple[dict[str, list[Row]], bool]:
    """The entities reached from `starts` along links whose type inherits rules, each with the steps that reach it
    (shortest first), and whether the walk was cut off: a link still leads from the last level to an unreached entity.
    A symmetric link is walked both ways. No visibility applies here; the caller decides what it shows."""
    today = _today()
    reached: dict[str, list[Row]] = {}
    for start in starts:
        reached.setdefault(start, [])
    frontier = list(reached)
    for _ in range(depth):
        nxt: list[str] = []
        for node in frontier:
            for step in _rows(c, _STEPS_SQL, {"node": node, "today": today}):
                other = str(step["other"])
                if other in reached:
                    continue
                reached[other] = [*reached[node], {"from": node, "key": step["link_key"], "to": other}]
                nxt.append(other)
        frontier = nxt
        if not frontier:
            return reached, False
    for node in frontier:
        for step in _rows(c, _STEPS_SQL, {"node": node, "today": today}):
            if str(step["other"]) not in reached:
                return reached, True
    return reached, False


NAME_CONTACT = "name:"  # a contact that is only a name (a letter's addressee), as `contact_entities` reads it
PHONE_CONTACT = re.compile(r"\+[0-9]{7,15}")  # an E.164 number once its formatting is stripped


def contact_entities(c: sqlite3.Connection, addresses: Sequence[str]) -> list[str]:
    """The entities a contact points at. An email address: a person or organization that has it as an email identifier,
    and the organization whose primary domain is its domain. A phone number (+ and digits, formatting ignored): one
    that has it as a phone identifier or phone field. `name:<name>` (a letter's addressee, which has no other identity):
    every entity with that name, so a name two entities share reaches both and a rule on either holds the send.
    Otherwise names are never matched."""
    ids: list[str] = []
    phones: set[str] = set()
    for address in addresses:
        text = address.strip().lower()
        if text.startswith(NAME_CONTACT):
            name = text[len(NAME_CONTACT) :].strip()
            if name:
                ids += [
                    str(r["entity_id"])
                    for r in _rows(
                        c,
                        "SELECT entity_id FROM entity_identifiers WHERE kind = 'name' AND normalized_value = ?",
                        (name,),
                    )
                ]
            continue
        phone = _normal_identifier("phone", text)
        if "@" not in text and PHONE_CONTACT.fullmatch(phone):
            phones.add(phone)
            continue
        if not text:
            continue
        ids += [
            str(r["entity_id"])
            for r in _rows(
                c, "SELECT entity_id FROM entity_identifiers WHERE kind = 'email' AND normalized_value = ?", (text,)
            )
        ]
        domain = text.rpartition("@")[2]
        if domain:
            ids += [
                str(r["id"])
                for r in _rows(
                    c,
                    "SELECT id FROM entities WHERE type = 'organization' AND primary_domain = ? "
                    "AND merged_into_id IS NULL AND deleted_at IS NULL",
                    (domain,),
                )
            ]
    if phones:
        ids.extend(
            str(entity_id)
            for entity_id, value in c.execute(
                "SELECT entity_id, normalized_value FROM entity_identifiers WHERE kind = 'phone' "
                "UNION ALL SELECT v.entity_id, v.text_value FROM entity_values v "
                "JOIN entity_attributes a ON a.id = v.entity_attribute_id WHERE a.value_type = 'phone'"
            )
            if _normal_identifier("phone", value) in phones
        )
    return sorted(set(ids))


def entity_names(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, str]:
    """The names of entities, read whole. Callers show a name only to a reader `visible_ids` names."""
    found = _rows(
        c, "SELECT id, name FROM entities WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(list(ids)),)
    )
    return {str(r["id"]): str(r["name"]) for r in found}


def linked_from(c: sqlite3.Connection, from_id: str, key: str) -> list[str]:
    """The entities `from_id` has a current link of this key to (the links a condition reads)."""
    today = _today()
    rows = _rows(
        c,
        "SELECT to_entity_id AS other FROM entity_links WHERE from_entity_id = ? AND link_key = ? "
        "AND (valid_from_on IS NULL OR valid_from_on <= ?) AND (valid_until_on IS NULL OR valid_until_on >= ?)",
        (from_id, key, today, today),
    )
    return sorted(str(r["other"]) for r in rows)


def linked_to(c: sqlite3.Connection, to_id: str, key: str) -> list[str]:
    """The entities with a current link of this key to `to_id`: customer_of self finds the customers."""
    today = _today()
    rows = _rows(
        c,
        "SELECT from_entity_id AS other FROM entity_links WHERE to_entity_id = ? AND link_key = ? "
        "AND (valid_from_on IS NULL OR valid_from_on <= ?) AND (valid_until_on IS NULL OR valid_until_on >= ?)",
        (to_id, key, today, today),
    )
    return sorted(str(r["other"]) for r in rows)


def links_from(c: sqlite3.Connection, from_ids: Sequence[str], key: str) -> list[Row]:
    """The links of one key that start at these entities and hold today: begun (a link set to start later is not yet
    current, so replacing today's links leaves it be) and not ended (a link ended today is gone, as a card reads it).
    Each with its id, from_entity_id and to_entity_id. Callers show the far end only to a reader who sees it."""
    if not from_ids:
        return []
    today = _today()
    return _rows(
        c,
        "SELECT id, from_entity_id, to_entity_id FROM entity_links WHERE from_entity_id IN (SELECT value FROM json_each(?)) "
        "AND link_key = ? AND (valid_from_on IS NULL OR valid_from_on <= ?) AND (valid_until_on IS NULL OR valid_until_on > ?) "
        "ORDER BY created_at, id",
        (json.dumps(list(from_ids)), key, today, today),
    )


def values_for(c: sqlite3.Connection, who: str, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The values of many entities at once, by entity id then attribute key: a single value, or a list for a multiple
    attribute. A list page reads this rather than a card per row. Sensitive attributes show only to who may read them;
    the caller checks that `who` sees each entity."""
    if not ids:
        return {}
    r = V.reader(c, who)
    out: dict[str, dict[str, Any]] = {}
    for row in _rows(
        c,
        "SELECT v.*, a.key AS attribute_key, a.value_type AS value_type, a.sensitive AS sensitive, a.multiple AS multiple "
        "FROM entity_values v JOIN entity_attributes a ON a.id = v.entity_attribute_id "
        "WHERE v.entity_id IN (SELECT value FROM json_each(?)) ORDER BY a.position, a.key, v.updated_at, v.id",
        (json.dumps(list(ids)),),
    ):
        if not V.attribute_visible(r, row):
            continue
        values = out.setdefault(str(row["entity_id"]), {})
        value = _value_of(row, row["value_type"])
        if row["multiple"]:
            values.setdefault(row["attribute_key"], []).append(value)
        else:
            values[row["attribute_key"]] = value
    return out


def clear_value(c: sqlite3.Connection, actor: str, entity_id: str, key: str, value: object = None) -> bool:
    """Remove a value: a single-value field's current value, or one value of a multiple field (all of them when `value` is
    None). What it held goes to history, as a replaced value does. True when something was removed."""
    _writer(c, actor)
    ent = _live(c, actor, entity_id)
    attr = _attribute(c, actor, ent["type"], key)
    if attr["owner_type"] == "connection" and actor != f"connection:{attr['owner_id']}":
        _refuse(c, actor, "owner", f"{key} comes from its connection; change it there")
    rows = _rows(
        c, "SELECT * FROM entity_values WHERE entity_id = ? AND entity_attribute_id = ?", (ent["id"], attr["id"])
    )
    if attr["multiple"] and value is not None:
        wanted = _coerce(c, actor, attr, value)["canonical"]
        rows = [row for row in rows if _value_of(row, attr["value_type"]) == wanted]
    now = H.now()
    for row in rows:
        c.execute(
            "INSERT INTO entity_value_history (id, entity_id, entity_attribute_id, value, source_type, source_id, "
            "evidence_id, confidence, active_from, active_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _id("hist"),
                ent["id"],
                attr["id"],
                json.dumps(_value_of(row, attr["value_type"]), sort_keys=True),
                row["source_type"],
                row["source_id"],
                row["evidence_id"],
                row["confidence"],
                row["updated_at"],
                now,
            ),
        )
        c.execute("DELETE FROM entity_values WHERE id = ?", (row["id"],))
    return bool(rows)


def kept(c: sqlite3.Connection, entity_id: str) -> str:
    """The entity `entity_id` is now: itself, or the one it was merged into (up to five merges)."""
    return follow_merges(c, [entity_id])[0]


def unmerged(c: sqlite3.Connection, actor: str, entity_id: str) -> str | None:
    """The entity `entity_id` was merged into, when it names a merged-away entity the reader sees through the kept one;
    else None. A write that names a merged-away id is refused with this, never carried over to the kept entity."""
    row = _one(c, "SELECT status FROM entities WHERE id = ?", (entity_id,))
    if row is None or row["status"] != "merged" or not visible_entity(c, actor, entity_id):
        return None
    return follow_merges(c, [entity_id])[0]


def set_status(
    c: sqlite3.Connection,
    actor: str,
    entity_id: str,
    version: int,
    *,
    status: str = "",
    next_step: str = "",
    next_step_due: str = "",
    flags: Sequence[str] = (),
    reader: str | None = None,
) -> Row | None:
    """Writes a relationship's status, next step, its due date and flags together, as `version` (the entity's
    status_version the writer read) becomes the next one. Each is replaced: an empty one is cleared. None when the
    version is not the entity's now (someone else wrote it since): nothing is written, and the caller reads it again.
    The entity is named by its own id; a merged-away one is refused (_live), never its kept entity. `reader` is whose
    Contacts grants see it (the effective reader); the writer gate is the actor's."""
    _writer(c, actor)
    ent = _live(c, actor, entity_id, reader)
    due = str(next_step_due or "").strip()
    if due:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                raise ValueError(due)
            due = date.fromisoformat(due).isoformat()  # sorted as text by what's next
        except ValueError:
            _refuse(c, actor, "kind", "next_step_due needs a date as YYYY-MM-DD")
    said = {
        "status": str(status or "").strip(),
        "next_step": str(next_step or "").strip(),
        "flags": list(dict.fromkeys(str(flag).strip() for flag in flags if str(flag).strip())),
    }
    if (
        c.execute(
            "UPDATE entities SET relationship_status = ?, next_step_due = ?, status_version = status_version + 1, "
            "updated_at = ? WHERE id = ? AND status_version = ?",
            (json.dumps(said, sort_keys=True), due or None, H.now(), ent["id"], int(version)),
        ).rowcount
        == 0
    ):
        return None
    return relationship(c, ent["id"])


def relationship(c: sqlite3.Connection, entity_id: str) -> Row:
    """An entity's relationship status as set_status keeps it, with the status_version a writer sends back."""
    row = _one(c, "SELECT relationship_status, next_step_due, status_version FROM entities WHERE id = ?", (entity_id,))
    assert row is not None  # the caller read the entity
    return _status_of(row)


def _status_of(row: Row) -> Row:
    try:
        said = json.loads(row["relationship_status"] or "{}")
    except ValueError:
        said = {}
    said = cast(dict[str, Any], said) if isinstance(said, dict) else {}
    return {
        "status": said.get("status") or None,
        "next_step": said.get("next_step") or None,
        "next_step_due": row["next_step_due"],
        "flags": list(said.get("flags") or []),
        "status_version": row["status_version"],
    }


def set_owner(c: sqlite3.Connection, actor: str, entity_id: str, owner: str, reader: str | None = None) -> str | None:
    """Names the teammate who owns an entity (a relationship's owner): human:<id> or bot:<id>, or '' for none. The
    caller resolved a bot by id as one it may see. The writer must see the entity, as for any Directory write, and a
    merged-away one is refused (_live). `reader` is whose Contacts grants see it. Returns the owner."""
    _writer(c, actor)
    ent = _live(c, actor, entity_id, reader)
    kind, _, ident = str(owner or "").strip().partition(":")
    if not kind:
        owner_type, owner_id = None, None
    elif kind == "human" and c.execute("SELECT 1 FROM humans WHERE id = ?", (ident,)).fetchone():
        owner_type, owner_id = "human", ident
    elif kind == "bot" and c.execute("SELECT 1 FROM bots WHERE id = ? AND state <> 'archived'", (ident,)).fetchone():
        owner_type, owner_id = "bot", ident
    else:
        _refuse(c, actor, "kind", "an owner is a person (human:<id>) or a bot (bot:<id>) on the team")
    c.execute(
        "UPDATE entities SET owner_type = ?, owner_id = ?, updated_at = ? WHERE id = ?",
        (owner_type, owner_id, H.now(), ent["id"]),
    )
    return _owner({"owner_type": owner_type, "owner_id": owner_id})


def owner_of(c: sqlite3.Connection, entity_id: str) -> str | None:
    """An entity's owner as stored (human:<id> or bot:<id>), or None. The caller checks the reader sees the entity."""
    row = _one(c, "SELECT owner_type, owner_id FROM entities WHERE id = ?", (entity_id,))
    return _owner(row) if row else None


def seen_entities(c: sqlite3.Connection, r: V.Reader, ids: Iterable[str]) -> dict[str, str]:
    """{id: name} of the entities among `ids` the reader sees that are not merged away: what a page link to an entity
    shows. A merged-away one is hidden until unmerge, whatever the kept entity shows. One read for all of them."""
    return {str(row["id"]): str(row["name"]) for row in _visible_rows(c, r, list(ids)) if row["status"] != "merged"}


def entity_facts(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, Row]:
    """Each entity's type and, for an organization, its kind (the scope terms type: and kind: read these)."""
    rows = _rows(
        c,
        "SELECT e.id AS id, e.type AS type, "
        "(SELECT v.option_key FROM entity_values v WHERE v.entity_id = e.id "
        "AND v.entity_attribute_id = 'attr_organization_kind') AS kind FROM entities e "
        "WHERE e.id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(ids)),),
    )
    return {str(r["id"]): {"type": r["type"], "kind": r["kind"]} for r in rows}


def visible_ids(c: sqlite3.Connection, who: str | V.Reader, ids: Iterable[str]) -> set[str]:
    """The ids of the entities a reader may see (the read rule, visible.clause)."""
    r = V.reader(c, who) if isinstance(who, str) else who
    return {str(row["id"]) for row in _visible_rows(c, r, list(ids))}


def remove_source(c: sqlite3.Connection, actor: str, entity_id: str, source_type: str, source_id: str) -> bool:
    """Forgets one source an entity is known from. True when a row was removed. The last source is refused: an entity
    with no sources shows to everyone, so removing it would publish what the source hid. Ending the last one is the
    deletion path's job, not this."""
    _writer(c, actor)
    _live(c, actor, entity_id)
    row = _one(
        c,
        "SELECT id FROM entity_sources WHERE entity_id = ? AND source_type = ? AND source_id = ?",
        (entity_id, source_type, source_id),
    )
    if row is None:
        return False
    if _count(c, "SELECT count(*) FROM entity_sources WHERE entity_id = ?", (entity_id,)) <= 1:
        _refuse(c, actor, "last_source", "that is the only source this entity is known from; it would show to everyone")
    c.execute("DELETE FROM entity_sources WHERE id = ?", (row["id"],))
    return True


def merge(c: sqlite3.Connection, actor: str, keep_id: str, drop_id: str) -> str:
    """Merges `drop` into `keep`: the drop's identifiers, links, values, notes, mentions, activities, external refs and
    sources move to the kept entity, and the drop redirects to it. The move is logged (entity_merges) so unmerge reverses
    it exactly. Self cannot be merged away, and the two must be one type."""
    _writer(c, actor)
    if keep_id == drop_id:
        _refuse(c, actor, "kind", "an entity cannot merge into itself")
    keep = _live(c, actor, keep_id)
    drop = _live(c, actor, drop_id)
    if drop["self"]:
        _refuse(c, actor, "kind", "self cannot be merged away")
    if keep["type"] != drop["type"]:
        _refuse(c, actor, "kind", f"a {drop['type']} merges only into a {drop['type']}")
    now = H.now()
    params = {"keep": keep_id, "drop": drop_id}
    tables: dict[str, list[Any]] = {}
    for table, sql in MOVES.items():
        ids = [row[0] for row in c.execute(sql, params)]
        if ids:
            c.executemany(
                f"UPDATE {table} SET entity_id = :keep WHERE id = :id", [{"keep": keep_id, "id": i} for i in ids]
            )
            tables[table] = ids
    _tasks_moved(c, tables.get("tasks", []), keep_id)
    values = [row[0] for row in c.execute(VALUE_MOVES, params)]
    if values:
        c.executemany(
            "UPDATE entity_values SET entity_id = :keep WHERE id = :id", [{"keep": keep_id, "id": i} for i in values]
        )
    targets = {"drop_target": f"entity:{drop_id}", "keep_target": f"entity:{keep_id}"}
    pages = [row[0] for row in c.execute(PAGE_LINK_MOVES, targets)]
    c.executemany("UPDATE page_links SET target = :keep_target WHERE id = :id", [{**targets, "id": i} for i in pages])
    links = _rows(
        c,
        "SELECT id, from_entity_id, to_entity_id FROM entity_links WHERE (from_entity_id = :drop OR to_entity_id = :drop) "
        "AND NOT (from_entity_id = :drop AND to_entity_id = :keep) AND NOT (from_entity_id = :keep AND to_entity_id = :drop)",
        params,
    )
    moved_links = [[row["id"], row["from_entity_id"], row["to_entity_id"]] for row in links]
    c.executemany(
        "UPDATE entity_links SET from_entity_id = :from_id, to_entity_id = :to_id, updated_at = :now WHERE id = :id",
        [
            {
                "from_id": keep_id if row["from_entity_id"] == drop_id else row["from_entity_id"],
                "to_id": keep_id if row["to_entity_id"] == drop_id else row["to_entity_id"],
                "now": now,
                "id": row["id"],
            }
            for row in links
        ],
    )
    prior = {
        "drop": {
            "status": drop["status"],
            "merged_into_id": drop["merged_into_id"],
            "mentions_count": drop["mentions_count"],
        },
        "keep": {"mentions_count": keep["mentions_count"], "last_activity_at": keep["last_activity_at"]},
    }
    activity = [v for v in (keep["last_activity_at"], drop["last_activity_at"]) if v]
    c.execute(
        "UPDATE entities SET mentions_count = mentions_count + ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
        (drop["mentions_count"], max(activity) if activity else None, now, keep_id),
    )
    c.execute(
        "UPDATE entities SET status = 'merged', merged_into_id = ?, updated_at = ? WHERE id = ?",
        (keep_id, now, drop_id),
    )
    c.execute(
        "INSERT INTO entity_merges (id, entity_id, into_entity_id, moved, prior, actor_type, actor_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _id("merge"),
            drop_id,
            keep_id,
            json.dumps({"tables": tables, "values": values, "links": moved_links, "pages": pages}, sort_keys=True),
            json.dumps(prior, sort_keys=True),
            _actor_kind(actor) or "",
            _actor_id(actor) or "",
            now,
        ),
    )
    _refresh_self_keys(c, keep_id)
    return keep_id


def _tasks_moved(c: sqlite3.Connection, ids: Sequence[str], entity_id: str) -> None:
    """A task a merge or unmerge moved is a changed task: its version and updated_at advance, so an edit read before is
    refused as stale and an updated_since reader sees it."""
    c.execute(
        "UPDATE tasks SET version = version + 1, updated_at = ? WHERE id IN (SELECT value FROM json_each(?)) "
        "AND entity_id = ?",
        (H.now(), json.dumps(list(ids)), entity_id),
    )


def unmerge(c: sqlite3.Connection, actor: str, drop_id: str) -> str:
    """Reverses the last merge of `drop_id` as logged: its rows go back, its status is restored and the counts of both
    are recounted. A row that left the kept entity since the merge stays where it is now. While the kept entity is itself
    merged into another, this is refused: that later merge carried these rows on and is undone first."""
    _writer(c, actor)
    reader = V.reader(c, actor)
    log = _one(
        c,
        "SELECT * FROM entity_merges WHERE entity_id = ? AND undone_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (drop_id,),
    )
    if log is None or not (V.visible(c, reader, drop_id) and V.visible(c, reader, log["into_entity_id"])):
        _refuse(c, actor, "not_found", f"{drop_id} has no merge to undo")
    keep_id = log["into_entity_id"]
    keep = _one(c, "SELECT status, merged_into_id FROM entities WHERE id = ?", (keep_id,))
    if keep is not None and keep["status"] == "merged":
        _refuse(c, actor, "merged", f"{keep_id} is merged into {keep['merged_into_id']} since; unmerge {keep_id} first")
    moved, prior = json.loads(log["moved"]), json.loads(log["prior"])
    now = H.now()
    back = {"drop": drop_id, "keep": keep_id}
    for table, ids in moved["tables"].items():
        c.executemany(
            f"UPDATE {table} SET entity_id = :drop WHERE id = :id AND entity_id = :keep",
            [{**back, "id": i} for i in ids],
        )
    _tasks_moved(c, moved["tables"].get("tasks", []), drop_id)
    if moved["values"]:
        c.executemany(
            "UPDATE entity_values SET entity_id = :drop WHERE id = :id AND entity_id = :keep",
            [{**back, "id": i} for i in moved["values"]],
        )
    # A page link goes back while it still names the kept entity (a merge logged before page links moved has none).
    c.executemany(
        "UPDATE OR IGNORE page_links SET target = :drop_target WHERE id = :id AND target = :keep_target",
        [
            {"drop_target": f"entity:{drop_id}", "keep_target": f"entity:{keep_id}", "id": i}
            for i in moved.get("pages", [])
        ],
    )
    # A link goes back only while its ends are still the ones the merge gave it.
    c.executemany(
        "UPDATE entity_links SET from_entity_id = :from_id, to_entity_id = :to_id, updated_at = :now "
        "WHERE id = :id AND from_entity_id = :from_now AND to_entity_id = :to_now",
        [
            {
                "from_id": f,
                "to_id": t,
                "from_now": keep_id if f == drop_id else f,
                "to_now": keep_id if t == drop_id else t,
                "now": now,
                "id": i,
            }
            for i, f, t in moved["links"]
        ],
    )
    # Counts come from the rows each entity holds now. The kept entity's last activity is its own from before the
    # merge, moved forward to the newest linked mention it holds now (one it gained since, or through a later merge);
    # the merge's snapshot alone would lose that.
    c.execute(
        "UPDATE entities SET mentions_count = (SELECT COUNT(*) FROM entity_mentions m WHERE m.entity_id = entities.id "
        "AND m.status = 'linked'), updated_at = ? WHERE id IN (?, ?)",
        (now, keep_id, drop_id),
    )
    held: str | None = c.execute(
        "SELECT MAX(created_at) FROM entity_mentions WHERE entity_id = ? AND status = 'linked'", (keep_id,)
    ).fetchone()[0]
    activity = [v for v in (prior["keep"]["last_activity_at"], held) if v]
    c.execute("UPDATE entities SET last_activity_at = ? WHERE id = ?", (max(activity) if activity else None, keep_id))
    c.execute(
        "UPDATE entities SET status = ?, merged_into_id = ?, updated_at = ? WHERE id = ?",
        (prior["drop"]["status"], prior["drop"]["merged_into_id"], now, drop_id),
    )
    c.execute("UPDATE entity_merges SET undone_at = ? WHERE id = ?", (now, log["id"]))
    _refresh_self_keys(c, keep_id)
    _refresh_self_keys(c, drop_id)
    return drop_id


# ------------------------------------------------------------------------------------------ erase and suppression
SALT_KEY = "directory_suppression_salt"  # the company's salt, in registry_metadata: a hash key, never an identifier
# The identifiers that name one person. A domain or a name is shared by many entities, so it is never suppressed.
SUPPRESSED_KINDS = ("email", "phone", "url", "external")
# Every table that names an entity by entity_id, and the Market tables that name one by subject. Each statement takes the
# erased entity ids (:ids) or the erased link ids (:links) as one JSON array.
ERASE_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("entity_values", "DELETE FROM entity_values WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_value_history", "DELETE FROM entity_value_history WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_identifiers", "DELETE FROM entity_identifiers WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_notes", "DELETE FROM entity_notes WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_mentions", "DELETE FROM entity_mentions WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_lookups", "DELETE FROM entity_lookups WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_activities", "DELETE FROM entity_activities WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_connections", "DELETE FROM entity_connections WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_external_refs", "DELETE FROM entity_external_refs WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_sources", "DELETE FROM entity_sources WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    ("entity_watches", "DELETE FROM entity_watches WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    # A task stays; it only stops naming the erased entity, and the entity's page links go (the pages stay).
    ("tasks", "UPDATE tasks SET entity_id = '' WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    (
        "conversations",
        "UPDATE conversations SET entity_id = NULL WHERE entity_id IN (SELECT value FROM json_each(:ids))",
    ),
    ("page_links", "DELETE FROM page_links WHERE target IN (SELECT 'entity:' || value FROM json_each(:ids))"),
    ("market_figures", "DELETE FROM market_figures WHERE entity_id IN (SELECT value FROM json_each(:ids))"),
    # A matter (backend/risk.py) stays with its task; it only stops naming the erased party, place or product.
    (
        "matters",
        "UPDATE matters SET "
        "party_entity_id = CASE WHEN party_entity_id IN (SELECT value FROM json_each(:ids)) THEN NULL "
        "ELSE party_entity_id END, "
        "place_id = CASE WHEN place_id IN (SELECT value FROM json_each(:ids)) THEN NULL ELSE place_id END, "
        "product_id = CASE WHEN product_id IN (SELECT value FROM json_each(:ids)) THEN NULL ELSE product_id END "
        "WHERE party_entity_id IN (SELECT value FROM json_each(:ids)) OR place_id IN (SELECT value FROM json_each(:ids)) "
        "OR product_id IN (SELECT value FROM json_each(:ids))",
    ),
    (
        "risk_links",
        "DELETE FROM risk_links WHERE linkable_type = 'entity' AND linkable_id IN (SELECT value FROM json_each(:ids))",
    ),
    (
        "entity_links",
        "DELETE FROM entity_links WHERE from_entity_id IN (SELECT value FROM json_each(:ids)) "
        "OR to_entity_id IN (SELECT value FROM json_each(:ids))",
    ),
    (
        "market_citations",
        "DELETE FROM market_citations WHERE (claim_kind = 'entity' AND claim_id IN (SELECT value FROM json_each(:ids))) "
        "OR (claim_kind = 'edge' AND claim_id IN (SELECT value FROM json_each(:links)))",
    ),
    (
        "market_events",
        "DELETE FROM market_events WHERE (subject_kind = 'entity' AND subject_id IN (SELECT value FROM json_each(:ids))) "
        "OR (subject_kind = 'edge' AND subject_id IN (SELECT value FROM json_each(:links)))",
    ),
    (
        "market_turn_sources",
        "DELETE FROM market_turn_sources WHERE (subject_kind = 'entity' AND subject_id IN (SELECT value FROM json_each(:ids))) "
        "OR (subject_kind = 'edge' AND subject_id IN (SELECT value FROM json_each(:links)))",
    ),
    (
        "entity_merges",
        "DELETE FROM entity_merges WHERE entity_id IN (SELECT value FROM json_each(:ids)) "
        "OR into_entity_id IN (SELECT value FROM json_each(:ids))",
    ),
)
# A task's history keeps the entity ids it was for (kind entity_id); an erased one is scrubbed from it, the row stays.
TASK_HISTORY_OF_ERASED = (
    "UPDATE history_events SET "
    "old = CASE WHEN old IN (SELECT value FROM json_each(:ids)) THEN NULL ELSE old END, "
    "new = CASE WHEN new IN (SELECT value FROM json_each(:ids)) THEN NULL ELSE new END "
    "WHERE subject_type = 'task' AND kind = 'entity_id' "
    "AND (old IN (SELECT value FROM json_each(:ids)) OR new IN (SELECT value FROM json_each(:ids)))"
)
# The history of the erased entities and of their links. Run after those rows are deleted (see erase_rows).
HISTORY_OF_ERASED = (
    "DELETE FROM history_events WHERE (subject_type = 'market_entity' AND subject_id IN (SELECT value FROM json_each(:ids))) "
    "OR (subject_type = 'market_edge' AND subject_id IN (SELECT value FROM json_each(:links)))"
)
# The evidence the erased rows rest on (a value, a note, a link, a citation of the entity or of its links).
EVIDENCE_OF = (
    "SELECT evidence_id FROM entity_values WHERE entity_id IN (SELECT value FROM json_each(:ids)) AND evidence_id IS NOT NULL "
    "UNION SELECT evidence_id FROM entity_value_history WHERE entity_id IN (SELECT value FROM json_each(:ids)) "
    "AND evidence_id IS NOT NULL "
    "UNION SELECT evidence_id FROM entity_notes WHERE entity_id IN (SELECT value FROM json_each(:ids)) AND evidence_id IS NOT NULL "
    "UNION SELECT evidence_id FROM entity_links WHERE id IN (SELECT value FROM json_each(:links)) AND evidence_id IS NOT NULL "
    "UNION SELECT evidence_id FROM market_citations WHERE (claim_kind = 'entity' AND claim_id IN (SELECT value FROM json_each(:ids))) "
    "OR (claim_kind = 'edge' AND claim_id IN (SELECT value FROM json_each(:links)))"
)


def _email_and_phone_values(
    c: sqlite3.Connection, type_key: str, values: Mapping[str, object]
) -> list[tuple[str, object]]:
    """The raw values in a values map that are emails or phones, by their attribute's type."""
    found: list[tuple[str, object]] = []
    for key, raw in values.items():
        attr = _one(
            c,
            "SELECT value_type FROM entity_attributes WHERE entity_type = ? AND key = ? AND archived_at IS NULL",
            (type_key, key),
        )
        if attr and attr["value_type"] in ("email", "phone") and raw:
            found.append((str(attr["value_type"]), raw))
    return found


def _normal_identifier(kind: str, value: object) -> str:
    text = str(value or "").strip()
    if kind == "phone":
        return re.sub(r"[^0-9+]", "", text)
    if kind == "external":
        return text  # system, external type and id joined by the unit separator; an external id's case is the system's
    return text.lower()


def external_suppression(system: str, external_type: str, external_id: str, connection_id: str) -> str:
    """What an erased record's suppression hashes: a versioned tuple, so a record with no connection ('') stays apart
    from each connection's and from a legacy (system, type, id) hash, which suppressed it on every connection."""
    return "v2:" + json.dumps([system, external_type, external_id, connection_id], separators=(",", ":"))


def _hash_identifier(kind: str, value: object, salt: str) -> str:
    return hmac.new(salt.encode(), f"{kind}\x1f{_normal_identifier(kind, value)}".encode(), hashlib.sha256).hexdigest()


def _existing_salt(c: sqlite3.Connection) -> str | None:
    row = _one(c, "SELECT value FROM registry_metadata WHERE key = ?", (SALT_KEY,))
    return str(json.loads(row["value"])) if row else None


def _salt(c: sqlite3.Connection) -> str:
    """The company's salt, made on the first erase."""
    salt = _existing_salt(c)
    if salt is None:
        c.execute(
            "INSERT OR IGNORE INTO registry_metadata (key, value) VALUES (?, ?)",
            (SALT_KEY, json.dumps(secrets.token_hex(32))),
        )
        salt = _existing_salt(c) or ""
    return salt


def is_suppressed(c: sqlite3.Connection, kind: str, value: object) -> bool:
    """Whether an identifier was erased. upsert refuses to create an entity from one, and the ingest paths check it before
    they create anyone from a sender or a contact. The table keeps only keyed hashes, so this compares hashes; a company
    that has erased no one has no salt and no suppressions."""
    if kind not in SUPPRESSED_KINDS or not str(value or "").strip():
        return False
    salt = _existing_salt(c)
    if salt is None:
        return False
    hit = _one(
        c,
        "SELECT 1 AS hit FROM entity_suppressions WHERE kind = ? AND value_hash = ?",
        (kind, _hash_identifier(kind, value, salt)),
    )
    return hit is not None


def _erased_ids(c: sqlite3.Connection, entity_id: str) -> list[str]:
    """The entity and every entity merged into it, and into those: a merged-away duplicate is the same person."""
    ids = [entity_id]
    frontier = [entity_id]
    while frontier:
        frontier = [
            row[0]
            for row in c.execute(
                "SELECT id FROM entities WHERE merged_into_id IN (SELECT value FROM json_each(?)) "
                "AND id NOT IN (SELECT value FROM json_each(?))",
                (json.dumps(frontier), json.dumps(ids)),
            )
        ]
        ids += frontier
    return ids


def _suppress_identifiers(c: sqlite3.Connection, ids: list[str], reason: str) -> int:
    """Hashes the erased entities' identifiers into entity_suppressions: their emails, phones and urls, and their external
    refs. Returns how many suppressions are new."""
    params = {"ids": json.dumps(ids)}
    pairs: list[tuple[str, object]] = [
        (str(row[0]), row[1])
        for row in c.execute(
            "SELECT kind, normalized_value FROM entity_identifiers WHERE entity_id IN (SELECT value FROM json_each(:ids)) "
            "AND kind IN ('email', 'phone', 'url')",
            params,
        )
    ]
    pairs += [
        ("email", row[0])
        for row in c.execute(
            "SELECT primary_email FROM entities WHERE id IN (SELECT value FROM json_each(:ids)) AND primary_email IS NOT NULL",
            params,
        )
    ]
    # A record is one connection's (system, type, id, connection): erasing it suppresses it on that connection only.
    pairs += [
        ("external", external_suppression(str(row[0]), str(row[1]), str(row[2]), str(row[3])))
        for row in c.execute(
            "SELECT system, external_type, external_id, connection_id FROM entity_external_refs "
            "WHERE entity_id IN (SELECT value FROM json_each(:ids))",
            params,
        )
    ]
    if not pairs:
        return 0
    salt, now, new = _salt(c), H.now(), 0
    for kind, value in pairs:
        new += c.execute(
            "INSERT OR IGNORE INTO entity_suppressions (id, kind, value_hash, created_at, reason) VALUES (?, ?, ?, ?, ?)",
            (_id("supp"), kind, _hash_identifier(kind, value, salt), now, reason),
        ).rowcount
    return new


def _evidence_cited(c: sqlite3.Connection, evidence_id: str) -> bool:
    return (
        _one(
            c,
            "SELECT 1 AS hit WHERE EXISTS (SELECT 1 FROM entity_values WHERE evidence_id = :ev) "
            "OR EXISTS (SELECT 1 FROM entity_value_history WHERE evidence_id = :ev) "
            "OR EXISTS (SELECT 1 FROM entity_links WHERE evidence_id = :ev) "
            "OR EXISTS (SELECT 1 FROM entity_notes WHERE evidence_id = :ev) "
            "OR EXISTS (SELECT 1 FROM market_citations WHERE evidence_id = :ev)",
            {"ev": evidence_id},
        )
        is not None
    )


def erase_rows(c: sqlite3.Connection, actor: str, entity_id: str, reason: str) -> dict[str, Any]:
    """Erases one entity, the entities merged into it, and every row that names them, in the caller's transaction (spec
    "Deleting a person"). Their identifiers (email, phone, url) and external refs are suppressed first, as salted hashes,
    so sync and ingest cannot create them again. Then each table that names them loses those rows, the evidence nothing
    else cites goes, and the full-text row goes with its entity. Returns the entity's type and the rows removed per table:
    never a name, an id or an identifier. The caller decides who may erase (backend/entities/erase.py)."""
    _writer(c, actor)
    root = _one(c, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if root is None:
        _refuse(c, actor, "not_found", "no such entity")
    if root["self"]:
        _refuse(c, actor, "kind", "self cannot be erased")
    ids = _erased_ids(c, entity_id)
    from backend.finance import directory as finance_directory

    kept = finance_directory.kept_for_books(c, ids, (table for table, _ in ERASE_STATEMENTS))
    if kept:
        # A ledger's counterparty (a contact, a bill's vendor, a project's customer) is kept for the accounting:
        # erasing it would break the books.
        from backend.store import Problem

        name = str(root["name"] or "").strip() or "This entity"
        raise Problem(
            "kept_for_books",
            f"Kept for the books: {name} has Finance records ({', '.join(kept)}). Erase them in Finance first.",
            409,
        )
    ids_json = json.dumps(ids)
    link_ids = [
        row[0]
        for row in c.execute(
            "SELECT id FROM entity_links WHERE from_entity_id IN (SELECT value FROM json_each(:ids)) "
            "OR to_entity_id IN (SELECT value FROM json_each(:ids))",
            {"ids": ids_json},
        )
    ]
    params = {"ids": ids_json, "links": json.dumps(link_ids)}
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    suppressed = _suppress_identifiers(c, ids, reason)
    evidence = [row[0] for row in c.execute(EVIDENCE_OF, params)]
    removed: dict[str, int] = {}
    # The full-text index keys an entity on its seq, so its row goes by that rowid.
    c.execute(
        "DELETE FROM market_fts WHERE rowid IN (SELECT seq FROM entities WHERE id IN (SELECT value FROM json_each(:ids)))",
        params,
    )
    for table, sql in ERASE_STATEMENTS:
        if table in tables:
            count = c.execute(sql, params).rowcount
            if count:
                removed[table] = count
    removed["entities"] = c.execute(
        "DELETE FROM entities WHERE id IN (SELECT value FROM json_each(:ids))", params
    ).rowcount
    # After the entities and links are gone: the history trigger (migration 20261108045025) allows a row of a subject that
    # no longer exists, and only those, so the history of live entities stays append-only.
    if "history_events" in tables:
        scrubbed = c.execute(TASK_HISTORY_OF_ERASED, params).rowcount
        if scrubbed:
            removed["task_history"] = scrubbed
        count = c.execute(HISTORY_OF_ERASED, params).rowcount
        if count:
            removed["history_events"] = count
    for evidence_id in evidence:
        if not _evidence_cited(c, evidence_id):
            count = c.execute("DELETE FROM market_evidence WHERE id = ?", (evidence_id,)).rowcount
            if count:
                removed["market_evidence"] = removed.get("market_evidence", 0) + count
    return {"type": root["type"], "removed": removed, "suppressed": suppressed}


def add_type(
    c: sqlite3.Connection, actor: str, key: str, name: str, description: str, *, plural_name: str = "", icon: str = ""
) -> str:
    """A new entity type. Its description is required; a near-duplicate of an existing type is refused with that type."""
    _writer(c, actor)
    key = _key(c, actor, key, "type")
    name = _required(c, actor, name, "name")
    description = _required(c, actor, description, "description")
    if _one(c, "SELECT key FROM entity_types WHERE key = ?", (key,)):
        _refuse(c, actor, "duplicate", f"the type {key} exists")
    stem = key.replace("_", "").rstrip("s")
    for existing in _rows(c, "SELECT key FROM entity_types"):
        if existing["key"].replace("_", "").rstrip("s") == stem:
            _refuse(c, actor, "duplicate", f"{existing['key']} is close to {key}; use it")
    kind_, ident = _actor_kind(actor) or "", _actor_id(actor) or ""
    c.execute(
        "INSERT INTO entity_types (id, key, name, plural_name, icon, built_in, mode, description, created_by_type, "
        "created_by_id, created_at) VALUES (?, ?, ?, ?, ?, 0, 'native', ?, ?, ?, ?)",
        (f"etype_{key}", key, name, plural_name or f"{name}s", icon, description, kind_, ident, H.now()),
    )
    return key


def add_attribute(
    c: sqlite3.Connection,
    actor: str,
    type_key: str,
    key: str,
    name: str,
    value_type: str,
    description: str,
    *,
    options: Sequence[Mapping[str, object]] | None = None,
    multiple: bool = False,
    unique_value: bool = False,
    required: bool = False,
    sensitive: bool = False,
    owner: str | None = None,
) -> str:
    """A new attribute of a type. A select or status needs its options; an owner of `connection:<id>` makes the field
    that connection's (only that connection writes it)."""
    _writer(c, actor)
    _type(c, actor, type_key)
    key = _key(c, actor, key, "attribute")
    name = _required(c, actor, name, "name")
    description = _required(c, actor, description, "description")
    if value_type not in VALUE_TYPES:
        _refuse(c, actor, "kind", f"a value type is {'|'.join(VALUE_TYPES)}, not {value_type}")
    opts = [dict(option) for option in (options or [])]
    if value_type in ("select", "status") and not opts:
        _refuse(c, actor, "kind", f"a {value_type} needs options")
    if _one(c, "SELECT id FROM entity_attributes WHERE entity_type = ? AND key = ?", (type_key, key)):
        _refuse(c, actor, "duplicate", f"{type_key} already has {key}")
    owner_type, owner_id = "tico", ""
    if owner:
        if not str(owner).startswith("connection:"):
            _refuse(c, actor, "kind", "an owner is tico or connection:<id>")
        owner_type, owner_id = "connection", str(owner).split(":", 1)[1]
    position = _count(c, "SELECT count(*) FROM entity_attributes WHERE entity_type = ?", (type_key,))
    c.execute(
        "INSERT INTO entity_attributes (id, entity_type, key, name, value_type, multiple, unique_value, required, options, "
        "owner_type, owner_id, description, sensitive, position, created_by_type, created_by_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _id("attr"),
            type_key,
            key,
            name,
            value_type,
            int(bool(multiple)),
            int(bool(unique_value)),
            int(bool(required)),
            json.dumps(opts, sort_keys=True),
            owner_type,
            owner_id,
            description,
            int(bool(sensitive)),
            position,
            _actor_kind(actor) or "",
            _actor_id(actor) or "",
            H.now(),
        ),
    )
    return key


def add_link_type(
    c: sqlite3.Connection,
    actor: str,
    key: str,
    name: str,
    inverse_name: str,
    description: str,
    *,
    from_type: str | None = None,
    to_type: str | None = None,
    inherits_rules: bool = False,
    symmetric: bool = False,
) -> str:
    """A new link type. `inherits_rules` says whether a rule toward the far end reaches the near end (works_at does; a
    competitor does not). Its description is required."""
    _writer(c, actor)
    key = _key(c, actor, key, "link type")
    name = _required(c, actor, name, "name")
    inverse_name = _required(c, actor, inverse_name, "inverse name")
    description = _required(c, actor, description, "description")
    for end in (from_type, to_type):
        if end:
            _type(c, actor, end)
    if _one(c, "SELECT key FROM link_types WHERE key = ?", (key,)):
        _refuse(c, actor, "duplicate", f"the link type {key} exists")
    c.execute(
        "INSERT INTO link_types (id, key, name, inverse_name, from_entity_type, to_entity_type, inherits_rules, symmetric, "
        "description, built_in, created_by_type, created_by_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (
            _id("ltype"),
            key,
            name,
            inverse_name,
            from_type,
            to_type,
            int(bool(inherits_rules)),
            int(bool(symmetric)),
            description,
            _actor_kind(actor) or "",
            _actor_id(actor) or "",
            H.now(),
        ),
    )
    return key


# ------------------------------------------------------------------------------------------------------- reads
def get(c: sqlite3.Connection, who: str, ref: object) -> list[Row]:
    """The visible entities a reference names: an id, an email, a domain, a name or an external id. More than one is
    ambiguous, and the caller says so. A merged entity answers with the one it was merged into."""
    r = V.reader(c, who)
    text = str(ref or "").strip()
    if not text:
        return []
    ids = [row["id"] for row in _rows(c, "SELECT id FROM entities WHERE id = ?", (text,))]
    if not ids:
        ids = [
            row[0]
            for row in c.execute(
                "SELECT DISTINCT entity_id FROM entity_identifiers WHERE normalized_value = ?", (text.lower(),)
            )
        ]
        ids += [
            row[0]
            for row in c.execute("SELECT DISTINCT entity_id FROM entity_external_refs WHERE external_id = ?", (text,))
        ]
    return _visible_rows(c, r, follow_merges(c, list(dict.fromkeys(ids))))


def find(
    c: sqlite3.Connection,
    who: str,
    type_key: str,
    *,
    name: str | None = None,
    status: str | None = None,
    link: str | None = None,
    after: str | None = None,
    limit: int = LIMIT,
    detail: bool = False,
    owner: str | None = None,
    sort: str = "name",
) -> dict[str, Any]:
    """A page of one type's visible entities, by name (a prefix), status, owner (human:<id> or bot:<id>), or a self link
    key (customer_of; a person also matches by the organization they work at). Keyset paged on (name_key, id), or with
    sort `next` on the next step's due date (none last), then name: `after` is the `next` the last page gave. A `next`
    row carries its relationship status (status, next step, due, flags, status_version). The count stops at COUNT_CAP
    and says so. `detail` adds what the Directory page's list shows: each row's organization, owner and the sources it
    is known from."""
    if sort not in ("name", "next"):
        raise ValueError("sort is name or next")
    r = V.reader(c, who)
    limit = _limit(limit)
    cl, cparams = V.clause(r)
    where = ["e.type = ?", "e.status <> 'merged'", cl]
    args = [type_key, *cparams]
    if name:
        low = str(name).strip().lower()
        where.append("e.name_key >= ? AND e.name_key < ?")
        args += [low, low + "￿"]
    if status:
        where.append("e.status = ?")
        args.append(status)
    if owner is not None:
        kind, _, ident = str(owner).partition(":")
        where.append("e.owner_type = ? AND e.owner_id = ?")
        args += [kind, ident]
    if link:
        # A person matches through the organization they work at, too: Jane at a customer is one of our customers' people.
        ocl, oparams = V.clause(r, "o")
        where.append(
            "((' ' || e.self_link_keys || ' ') LIKE ? OR (e.type = 'person' AND EXISTS (SELECT 1 FROM entity_links l "
            "JOIN entities o ON o.id = l.to_entity_id WHERE l.from_entity_id = e.id "
            "AND l.link_key IN (SELECT value FROM json_each(?)) AND (l.valid_until_on IS NULL OR l.valid_until_on > ?) "
            f"AND o.status <> 'merged' AND (' ' || o.self_link_keys || ' ') LIKE ? AND {ocl})))"
        )
        args += [f"% {link} %", json.dumps(list(WORKS_AT)), _today(), f"% {link} %", *oparams]
    # Sorted by the next step's due date, a row with none comes last (its key is past any date).
    keys = "COALESCE(e.next_step_due, '9999-12-31'), e.name_key, e.id" if sort == "next" else "e.name_key, e.id"
    if after:
        start = _decode(after)
        if not isinstance(start, list) or len(cast(list[Any], start)) != (3 if sort == "next" else 2):
            raise ValueError("after is the next a page of this sort gave")
        where.append(f"({keys}) > ({', '.join('?' * len(cast(list[Any], start)))})")
        args += cast(list[Any], start)
    condition = " AND ".join(where)
    rows = _rows(
        c,
        f"SELECT e.* FROM entities e WHERE {condition} ORDER BY {keys} LIMIT ?",
        [*args, limit + 1],
    )
    more = len(rows) > limit
    rows = rows[:limit]
    total = _count(
        c,
        f"SELECT count(*) FROM (SELECT 1 FROM entities e WHERE {condition} LIMIT ?)",
        [*args, COUNT_CAP + 1],
    )
    shown = [brief(row) for row in rows]
    if sort == "next":
        for row, item in zip(rows, shown, strict=True):
            item["relationship"] = _status_of(row)
    if detail:
        _list_detail(c, r, rows, shown)
    last = rows[-1] if rows else {}
    return {
        "rows": shown,
        "next": (
            _encode(
                [last["next_step_due"] or "9999-12-31", last["name_key"], last["id"]]
                if sort == "next"
                else [last["name_key"], last["id"]]
            )
            if more and rows
            else None
        ),
        "count": min(total, COUNT_CAP),
        "approximate": total > COUNT_CAP,
    }


def brief(row: Row) -> Row:
    """An entity as a list shows it: what a person scans for, and no more than the list needs to page."""
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "status": row["status"],
        "self": bool(row["self"]),
        "primary_email": row["primary_email"],
        "primary_domain": row["primary_domain"],
        "links_to_us": (row["self_link_keys"] or "").split(),
        "last_activity_at": row["last_activity_at"],
        "mentions_count": row["mentions_count"],
    }


def _owner(row: Row) -> str | None:
    return f"{row['owner_type']}:{row['owner_id']}" if row.get("owner_id") else None


def _sources_seen(c: sqlite3.Connection, r: V.Reader, ids: Sequence[str]) -> dict[str, list[str]]:
    """Each entity's source types the reader can see (company-wide ones, and the restricted ones the reader is granted),
    so a list never says an entity is known from a book or an inbox the reader cannot read."""
    out: dict[str, list[str]] = {}
    if not ids:
        return out
    for row in _rows(
        c,
        "SELECT entity_id, source_type, source_id, visibility FROM entity_sources "
        "WHERE entity_id IN (SELECT value FROM json_each(?)) ORDER BY entity_id, created_at, id",
        (json.dumps(list(ids)),),
    ):
        source_type = str(row["source_type"])
        if row["visibility"] != "company" and not r.system:
            granted = r.grants.get(source_type, ())
            if granted is not None and str(row["source_id"]) not in granted:
                continue
        kinds = out.setdefault(str(row["entity_id"]), [])
        if source_type not in kinds:
            kinds.append(source_type)
    return out


def _list_detail(c: sqlite3.Connection, r: V.Reader, rows: Sequence[Row], shown: list[Row]) -> None:
    """The list columns beyond brief(): a person's organization (a current works_at or member_of to an organization the
    reader sees), the owner, and the source types the reader may see. One query each for the page."""
    ids = [str(row["id"]) for row in rows]
    cl, cparams = V.clause(r, "o")
    orgs: dict[str, Row] = {}
    if ids:
        for link in _rows(
            c,
            "SELECT l.from_entity_id AS entity_id, o.id, o.name, o.self_link_keys FROM entity_links l "
            "JOIN entities o ON o.id = l.to_entity_id "
            "WHERE l.from_entity_id IN (SELECT value FROM json_each(?)) AND l.link_key IN (SELECT value FROM json_each(?)) "
            f"AND (l.valid_until_on IS NULL OR l.valid_until_on > ?) AND o.status <> 'merged' AND {cl} "
            "ORDER BY l.valid_from_on DESC, o.name_key, o.id",
            [json.dumps(ids), json.dumps(list(WORKS_AT)), _today(), *cparams],
        ):
            orgs.setdefault(
                str(link["entity_id"]),
                {"id": link["id"], "name": link["name"], "links_to_us": (link["self_link_keys"] or "").split()},
            )
    sources = _sources_seen(c, r, ids)
    for row, item in zip(rows, shown, strict=True):
        item["organization"] = orgs.get(str(row["id"]))
        item["owner"] = _owner(row)
        item["sources"] = sources.get(str(row["id"]), [])


def type_counts(c: sqlite3.Connection, who: str) -> list[Row]:
    """How many entities of each contact type the reader sees, each stopping at COUNT_CAP and saying so: the Contacts
    tabs and Settings › Contacts' types read it."""
    r = V.reader(c, who)
    cl, cparams = V.clause(r)
    out: list[Row] = []
    for kind in types(c):
        total = _count(
            c,
            f"SELECT count(*) FROM (SELECT 1 FROM entities e WHERE e.type = ? AND e.status <> 'merged' AND {cl} LIMIT ?)",
            [kind["key"], *cparams, COUNT_CAP + 1],
        )
        out.append({"type": kind["key"], "count": min(total, COUNT_CAP), "approximate": total > COUNT_CAP})
    return out


def target(c: sqlite3.Connection, actor: str, ref: object) -> str:
    """The one entity a write's reference names, as the writer sees it. One the writer cannot see is not found; two that
    match are ambiguous, and both are named (both are the writer's to see). A merged one answers with the kept one."""
    found = get(c, actor, ref)
    if not found:
        _refuse(c, actor, "not_found", f"no entity {ref} you can write to")
    if len(found) > 1:
        _refuse(c, actor, "ambiguous", f"{', '.join(row['id'] for row in found)} all match {ref}; name one by id")
    return found[0]["id"]


def type_exists(c: sqlite3.Connection, key: str) -> bool:
    return _one(c, "SELECT 1 AS found FROM entity_types WHERE key = ? AND archived_at IS NULL", (key,)) is not None


# Types that are not contacts. A document (a law, a contract, terms of service) is a file: it is never a Contacts tab,
# count or list, for people or bots. Its entity stays, for the rules that walk party_to and issued_by to it, and a
# contact's page links it to its file (document_files).
NOT_CONTACTS = ("document",)


def types(c: sqlite3.Connection) -> list[Row]:
    """The contact types a reader can list: the built-in ones, then the ones bots and people added (who added each
    shows). NOT_CONTACTS are left out."""
    return _rows(
        c,
        "SELECT key, name, plural_name, description, icon, built_in, mode, created_by_type, created_by_id "
        "FROM entity_types WHERE archived_at IS NULL AND key NOT IN (SELECT value FROM json_each(?)) "
        "ORDER BY built_in DESC, key",
        (json.dumps(NOT_CONTACTS),),
    )


def document_files(c: sqlite3.Connection, entity_ids: Sequence[str]) -> dict[str, str]:
    """The file each document is, by entity id: its active external ref in Tico (system 'tico', external_type 'file',
    external_id a bot_files id). A document with none has no file yet. Whether the reader may open the file is the
    caller's check (backend/files.py file_visible)."""
    if not entity_ids:
        return {}
    out: dict[str, str] = {}
    for row in _rows(
        c,
        "SELECT entity_id, external_id FROM entity_external_refs WHERE entity_id IN (SELECT value FROM json_each(?)) "
        "AND system = 'tico' AND external_type = 'file' AND status = 'active' ORDER BY entity_id, external_id",
        (json.dumps(list(entity_ids)),),
    ):
        out.setdefault(str(row["entity_id"]), str(row["external_id"]))
    return out


def describe_type(c: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """One type: its attributes (who owns each) and the link types that may touch it. A link type with no end type
    touches every type. None when there is no such type."""
    row = _one(c, "SELECT * FROM entity_types WHERE key = ? AND archived_at IS NULL", (key,))
    if row is None:
        return None
    attributes = _rows(
        c,
        "SELECT key, name, description, value_type, multiple, unique_value, required, sensitive, options, owner_type, "
        "owner_id FROM entity_attributes WHERE entity_type = ? AND archived_at IS NULL ORDER BY position, key",
        (key,),
    )
    for attr in attributes:
        owner_type, owner_id = attr.pop("owner_type"), attr.pop("owner_id")
        attr["owner"] = f"connection:{owner_id}" if owner_type == "connection" else "tico"
        attr["options"] = json.loads(attr["options"] or "[]")
        for flag in ("multiple", "unique_value", "required", "sensitive"):
            attr[flag] = bool(attr[flag])
    link_types = _rows(
        c,
        "SELECT key, name, inverse_name, description, from_entity_type, to_entity_type, inherits_rules, symmetric "
        "FROM link_types WHERE archived_at IS NULL AND (from_entity_type = ? OR to_entity_type = ? "
        "OR from_entity_type IS NULL OR to_entity_type IS NULL) ORDER BY key",
        (key, key),
    )
    for link_type in link_types:
        link_type["inherits_rules"] = bool(link_type["inherits_rules"])
        link_type["symmetric"] = bool(link_type["symmetric"])
    return {
        "type": {k: row[k] for k in ("key", "name", "plural_name", "description", "built_in", "mode", "icon")},
        "attributes": attributes,
        "link_types": link_types,
    }


def rules(c: sqlite3.Connection, who: str, entity_id: str) -> list[Row] | None:
    """The rules that apply to an entity, each with the path it comes by. Rules arrive in track 5 (spec §3, Rules), so
    until then none apply and the answer is the empty list. None when the entity does not show to the reader."""
    if not V.visible(c, who, entity_id):
        return None
    return []


def self_summary(c: sqlite3.Connection, who: str) -> dict[str, Any] | None:
    """Who we are, for every bot's base context: our company, the places we operate in, and the obligations that bind us
    (none until rules arrive). It reads the self flag boot set and writes nothing, so it runs on a read connection."""
    reader = V.reader(c, who)
    me = _one(c, "SELECT id FROM entities WHERE self = 1")
    if me is None or not _visible_rows(c, reader, [me["id"]]):
        return None
    company = card(c, who, me["id"])
    if company is None:
        return None
    cl, cparams = V.clause(reader, "o")
    places = _rows(
        c,
        "SELECT o.id, o.name, l.id AS link_id FROM entity_links l JOIN entities o "
        "ON o.id = CASE WHEN l.from_entity_id = ? THEN l.to_entity_id ELSE l.from_entity_id END "
        "WHERE (l.from_entity_id = ? OR l.to_entity_id = ?) AND l.link_key = 'operates_in' "
        f"AND (l.valid_until_on IS NULL OR l.valid_until_on > ?) AND o.status <> 'merged' AND {cl} "
        "ORDER BY o.name LIMIT 50",
        [me["id"], me["id"], me["id"], _today(), *cparams],
    )
    # Each field's value, or the list of values for a field that holds many: the card's source and age are left out.
    card_values = cast(dict[str, Any], company["values"])
    values: dict[str, Any] = {}
    for key, entry in card_values.items():
        if isinstance(entry, list):
            values[key] = [item["value"] for item in cast(list[dict[str, Any]], entry)]
        else:
            values[key] = entry["value"]
    return {
        "id": company["id"],
        "name": company["name"],
        "primary_domain": company["primary_domain"],
        "values": values,
        "operates_in": places,
        "obligations": [],
    }


def visible_entity(c: sqlite3.Connection, who: str, entity_id: str) -> bool:
    """Whether `who` sees the entity `entity_id` names, as `card` does: a merged one is the one it was merged into, an
    erased one is gone, a hidden one is not seen."""
    return bool(_visible_rows(c, V.reader(c, who), follow_merges(c, [entity_id])))


def card(c: sqlite3.Connection, who: str, entity_id: str) -> dict[str, Any] | None:
    """An entity as its reader sees it: its visible values (with source and age), its current links to visible entities,
    and its notes the reader may read. None when the entity does not show."""
    r = V.reader(c, who)
    found = _visible_rows(c, r, follow_merges(c, [entity_id]))
    if not found:
        return None
    ent = found[0]
    values: dict[str, Any] = {}
    for row in _rows(
        c,
        "SELECT v.*, a.key AS attribute_key, a.value_type AS value_type, a.sensitive AS sensitive, a.multiple AS multiple "
        "FROM entity_values v JOIN entity_attributes a ON a.id = v.entity_attribute_id WHERE v.entity_id = ? "
        "ORDER BY a.position, a.key",
        (ent["id"],),
    ):
        if not V.attribute_visible(r, row):
            continue
        item = {
            "value": _value_of(row, row["value_type"]),
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "confidence": row["confidence"],
            "updated_at": row["updated_at"],
        }
        if row["multiple"]:
            values.setdefault(row["attribute_key"], []).append(item)
        else:
            values[row["attribute_key"]] = item
    cl, cparams = V.clause(r, "o")
    me, today = ent["id"], _today()
    links = _rows(
        c,
        "SELECT l.id, l.link_key, lt.name AS link_name, lt.inverse_name, l.from_entity_id, l.to_entity_id, "
        "l.valid_from_on, l.valid_until_on, l.confidence, o.id AS other_id, o.name AS other_name, o.type AS other_type "
        "FROM entity_links l JOIN link_types lt ON lt.key = l.link_key "
        "JOIN entities o ON o.id = CASE WHEN l.from_entity_id = ? THEN l.to_entity_id ELSE l.from_entity_id END "
        "WHERE (l.from_entity_id = ? OR l.to_entity_id = ?) AND (l.valid_until_on IS NULL OR l.valid_until_on > ?) "
        f"AND o.status <> 'merged' AND {cl} ORDER BY l.link_key, o.name LIMIT {CARD_LINKS}",
        [me, me, me, today, *cparams],
    )
    # The records this entity is in other systems (a CRM record, a repository): a pointer, shown with the entity.
    refs = _rows(
        c,
        "SELECT system, external_type, external_id, connection_id, url, synced_at FROM entity_external_refs "
        "WHERE entity_id = ? AND status = 'active' ORDER BY system, external_type, external_id, connection_id LIMIT 50",
        (me,),
    )
    notes = [
        row
        for row in _rows(
            c,
            "SELECT n.id, n.author_type, n.author_id, n.body, n.audience, n.created_at, "
            "(n.author_type || ':' || n.author_id) AS author_key FROM entity_notes n WHERE n.entity_id = ? "
            "ORDER BY n.created_at DESC LIMIT 200",
            (me,),
        )
        if V.note_visible(r, row)
    ][:20]
    # The list's columns (organization, owner, sources), read the same way, so the page and its row agree.
    detail: dict[str, Any] = {}
    _list_detail(c, r, [ent], [detail])
    body: dict[str, Any] = {
        "id": ent["id"],
        "type": ent["type"],
        "name": ent["name"],
        "status": ent["status"],
        "self": bool(ent["self"]),
        "primary_email": ent["primary_email"],
        "primary_domain": ent["primary_domain"],
        "summary": ent["summary"],
        "links_to_us": (ent["self_link_keys"] or "").split(),
        **detail,
        "relationship": _status_of(ent),
        "values": values,
        "links": links,
        "refs": refs,
        "notes": notes,
    }
    if ent["type"] == "place":
        # A place from the shared places tree says so (the page marks it Shared), with its kind and governing body.
        try:
            props = json.loads(ent["properties"] or "{}")
        except ValueError:
            props = {}
        props = cast(dict[str, Any], props) if isinstance(props, dict) else {}
        body["place"] = {
            "kind": props.get("place_kind") or "",
            "governing_body": props.get("governing_body") or "",
            "shared": ent["created_by_id"] == SHARED_PLACES_ACTOR,
        }
    return body


def history(
    c: sqlite3.Connection,
    who: str,
    entity_id: str,
    attribute_key: str | None = None,
    *,
    after: str | None = None,
    limit: int = LIMIT,
) -> dict[str, Any] | None:
    """Past values, newest first, keyset paged on (active_until, id): one field's when an attribute is named, else every
    field's the reader may see. None when the entity, or the named field, does not show to the reader."""
    r = V.reader(c, who)
    shown = _visible_rows(c, r, follow_merges(c, [entity_id]))
    if not shown:
        return None
    me = shown[0]
    where = "h.entity_id = ? AND (a.sensitive = 0 OR ? = 1)"
    args: list[object] = [me["id"], int(r.sensitive)]
    if attribute_key:
        attr = _one(c, "SELECT * FROM entity_attributes WHERE entity_type = ? AND key = ?", (me["type"], attribute_key))
        if attr is None or not V.attribute_visible(r, attr):
            return None
        where += " AND h.entity_attribute_id = ?"
        args.append(attr["id"])
    limit = _limit(limit)
    if after:
        until, row_id = _decode(after)
        where += " AND (h.active_until, h.id) < (?, ?)"
        args += [until, row_id]
    rows = _rows(
        c,
        "SELECT h.*, a.key AS attribute_key FROM entity_value_history h "
        "JOIN entity_attributes a ON a.id = h.entity_attribute_id "
        f"WHERE {where} ORDER BY h.active_until DESC, h.id DESC LIMIT ?",
        [*args, limit + 1],
    )
    more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row["value"] = json.loads(row["value"])
    return {"rows": rows, "next": _encode([rows[-1]["active_until"], rows[-1]["id"]]) if more and rows else None}


def matching(c: sqlite3.Connection, type_key: str, match: Mapping[str, object]) -> list[str]:
    """The live entities of one type a match names (an email, a domain, a name or an id), all of them: ingest asks this
    before it creates, so an existing entity is found without its name or values being written again. It reads every
    entity, not what a reader may see, so only the system's ingest calls it."""
    return _match(c, type_key, match)


def verified_sender(
    c: sqlite3.Connection, actor: str, channel: str, connection_id: str, provider_id: str
) -> str | None:
    """Resolve a sender ID vouched for by the adapter, scoped to that exact connection."""
    if not provider_id:
        return None
    row = _one(
        c,
        "SELECT entity_id FROM entity_external_refs WHERE system=? AND connection_id=? "
        "AND external_type='sender' AND external_id=? AND status='active'",
        (channel, connection_id, provider_id),
    )
    if row is None:
        return None
    found = _visible_rows(c, V.reader(c, actor), _follow(c, [row["entity_id"]]))
    return str(found[0]["id"]) if found else None


def suggested_sender(c: sqlite3.Connection, actor: str, sender: str) -> str | None:
    """An exact visible contact, suggested in the triage task even when it is the only match."""
    sender = sender.strip().lower()
    phone = _normal_identifier("phone", sender)
    if "@" in sender:
        checks = [("email", sender), ("domain", sender.rpartition("@")[2])]
    elif PHONE_CONTACT.fullmatch(phone):
        checks = [("phone", phone)]
    else:
        checks = [("domain", sender)]
    matches = identifier_entities(c, checks)
    reader = V.reader(c, actor)
    for check in checks:
        ids = list(dict.fromkeys(matches.get(check, [])))
        if ids:
            return ids[0] if len(ids) == 1 and V.visible(c, reader, ids[0]) else None
    return None


def activity_history(
    c: sqlite3.Connection, entity_id: str, *, thread_scope: str, task_scope: str, after: str | None, limit: int
) -> dict[str, Any]:
    """Page the visible relationship history over a single ordering for conversations and activities."""
    args: list[object] = [entity_id, entity_id]
    limit = _limit(limit)
    cursor = ""
    if after:
        decoded: object = _decode(after)
        marker = cast("list[object]", decoded) if isinstance(decoded, list) else []
        if len(marker) != 3 or not all(isinstance(part, str) for part in marker):
            raise ValueError("the cursor is not one this list returned")
        cursor = "WHERE (occurred_at, type, id) < (?, ?, ?)"
        args.extend(marker)
    # An activity with provenance needs a reader check for that source. Threads and tasks have one here.
    rows = _rows(
        c,
        "WITH outside AS (SELECT th.id, coalesce(th.last_message_at, th.created_at) AS occurred_at, "
        "coalesce(th.subject, '') AS summary FROM conversations th "
        f"WHERE th.entity_id=? AND th.parent_id IS NULL AND th.audience='entity' AND ({thread_scope})), "
        "history AS ("
        "SELECT id, 'thread' AS type, 'conversation' AS kind, occurred_at, summary, NULL AS subject_id FROM outside "
        "UNION ALL "
        "SELECT a.id, 'activity', a.kind, a.occurred_at, substr(a.summary, 1, 1000), a.subject_id "
        "FROM entity_activities a WHERE a.entity_id=? AND coalesce(a.external_ref, '') = '' AND ("
        "(coalesce(a.subject_type, '') = '' AND coalesce(a.subject_id, '') = '') OR "
        f"(a.subject_type='task' AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=a.subject_id AND ({task_scope}))) OR "
        "(a.subject_type='thread' AND a.subject_id IN (SELECT id FROM outside))) "
        f") SELECT * FROM history {cursor} ORDER BY occurred_at DESC, type DESC, id DESC LIMIT ?",
        [*args, limit + 1],
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "rows": rows,
        "next": _encode([rows[-1][k] for k in ("occurred_at", "type", "id")]) if more else None,
    }


def has_sources(c: sqlite3.Connection, entity_id: str) -> bool:
    """Whether an entity records any source. One with none shows to everyone, so a restricted source added to it would
    narrow it. Ingest checks this before it adds one to an entity it did not just create."""
    return _count(c, "SELECT count(*) FROM entity_sources WHERE entity_id = ?", (entity_id,)) > 0


# ----------------------------------------------------------------------------------------- the linker (track 6b, §7.1)
# What the linker (backend/entities/linker.py) reads and writes. Matches come from entity_identifiers' index, a batch at a
# time, never from a scan of the entities. A linker write is a system write, as seed_link is: no roster actor, and only
# the mention rows the linker itself found (method run_trace or exact) are replaced.
LINKER_METHODS = ("run_trace", "exact")
IN_CHUNK = 400  # values per json_each lookup, so one statement never carries an unbounded list
# The linker links only to what a company member may see. Text is read by company members and by narrower audiences, so
# a company-visible entity is one every reader of the text may be shown. A restricted entity is never linked from text.
COMPANY_READER = V.Reader(actor="", key="", system=False, sensitive=False, grants={})


def record_lookup(c: sqlite3.Connection, attempt_id: str, entity_id: str) -> None:
    """The entity an attempt read (`tico contacts get` or `rules`): the run trace the linker links that attempt's text to.
    Once per attempt and entity."""
    c.execute(
        "INSERT OR IGNORE INTO entity_lookups (id, attempt_id, entity_id, created_at) VALUES (?, ?, ?, ?)",
        (_id("lkp"), attempt_id, entity_id, H.now()),
    )


def run_lookups(c: sqlite3.Connection, attempt_id: str) -> list[str]:
    """The entities an attempt read, merges followed, oldest first. Who may see them is the caller's question."""
    ids = [
        row["entity_id"]
        for row in _rows(
            c,
            "SELECT entity_id FROM entity_lookups WHERE attempt_id = ? ORDER BY created_at, id",
            (attempt_id,),
        )
    ]
    return follow_merges(c, ids)


def identifier_entities(c: sqlite3.Connection, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[str]]:
    """The kept entities each (kind, normalized value) names, merges followed. Every entity counts, whoever may see it: a
    name two entities share is ambiguous to the linker, and it stays ambiguous when one of them is hidden. A scoped
    identifier (a Finance book's alias, scope_type set) is not company-wide, so it is not matched at all."""
    by_kind: dict[str, list[str]] = {}
    for kind, value in dict.fromkeys(pairs):
        by_kind.setdefault(kind, []).append(value)
    found: dict[tuple[str, str], list[str]] = {}
    for kind, values in by_kind.items():
        for start in range(0, len(values), IN_CHUNK):
            chunk = values[start : start + IN_CHUNK]
            for row in _rows(
                c,
                "SELECT i.normalized_value, i.entity_id FROM entity_identifiers i "
                "JOIN entities e ON e.id = i.entity_id "
                "WHERE i.kind = ? AND i.normalized_value IN (SELECT value FROM json_each(?)) "
                "AND i.scope_type IS NULL AND e.deleted_at IS NULL",
                (kind, json.dumps(chunk)),
            ):
                found.setdefault((kind, row["normalized_value"]), []).append(row["entity_id"])
    return {key: follow_merges(c, ids) for key, ids in found.items()}


def company_visible(c: sqlite3.Connection, ids: Iterable[str]) -> set[str]:
    """The entities among `ids` that a company member sees whatever else they hold (the read rule for a reader with no
    grants). A merged or deleted entity is not one."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        return set()
    cl, params = V.clause(COMPANY_READER)
    rows = _rows(
        c,
        "SELECT e.id FROM entities e WHERE e.id IN (SELECT value FROM json_each(?)) "
        f"AND e.deleted_at IS NULL AND e.status <> 'merged' AND {cl}",
        [json.dumps(unique), *params],
    )
    return {row["id"] for row in rows}


def replace_link_mentions(
    c: sqlite3.Connection,
    mentionable_type: str,
    mentionable_id: str,
    revision: str,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Replaces the linker's mention rows for one text with `rows` (entity_id, role, quote, occurrence, confidence, method,
    status). Mentions another method wrote (an inline link, a person's) stay. The mention counts of every entity the old
    or new rows name are recounted, and a linked mention moves the entity's last activity forward."""
    before = [
        row["entity_id"]
        for row in _rows(
            c,
            "SELECT DISTINCT entity_id FROM entity_mentions WHERE mentionable_type = ? AND mentionable_id = ? "
            "AND method IN ('run_trace', 'exact')",
            (mentionable_type, mentionable_id),
        )
    ]
    c.execute(
        "DELETE FROM entity_mentions WHERE mentionable_type = ? AND mentionable_id = ? AND method IN ('run_trace', 'exact')",
        (mentionable_type, mentionable_id),
    )
    now = H.now()
    for row in rows:
        c.execute(
            "INSERT INTO entity_mentions (id, mentionable_type, mentionable_id, mentionable_revision, entity_id, role, "
            "quote, occurrence, confidence, method, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _id("men"),
                mentionable_type,
                mentionable_id,
                revision,
                row["entity_id"],
                row["role"],
                row["quote"],
                row["occurrence"],
                row["confidence"],
                row["method"],
                row["status"],
                now,
            ),
        )
    touched = list(dict.fromkeys([*before, *(row["entity_id"] for row in rows)]))
    if not touched:
        return
    c.execute(
        "UPDATE entities SET mentions_count = (SELECT COUNT(*) FROM entity_mentions m "
        "WHERE m.entity_id = entities.id AND m.status = 'linked') WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(touched),),
    )
    linked = list(dict.fromkeys(row["entity_id"] for row in rows if row["status"] == "linked"))
    if linked:
        c.execute(
            "UPDATE entities SET last_activity_at = ? WHERE id IN (SELECT value FROM json_each(?)) "
            "AND (last_activity_at IS NULL OR last_activity_at < ?)",
            (now, json.dumps(linked), now),
        )


def mentions(c: sqlite3.Connection, who: str, mentionable_type: str, mentionable_id: str) -> list[Row]:
    """The mentions of one text whose entity shows to the reader (the read rule). A suggested mention is returned too; the
    caller says it renders as plain text."""
    r = V.reader(c, who)
    cl, params = V.clause(r)
    return _rows(
        c,
        "SELECT m.*, e.name AS entity_name FROM entity_mentions m JOIN entities e ON e.id = m.entity_id "
        f"WHERE m.mentionable_type = ? AND m.mentionable_id = ? AND {cl} ORDER BY m.created_at, m.id",
        [mentionable_type, mentionable_id, *params],
    )
