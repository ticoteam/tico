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
import json
import math
import re
import uuid
from datetime import date, datetime

from backend import hubdb as H
from backend.entities import visible as V

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
# The tables a merge moves an entity's rows from. entity_sources skips a source the kept entity already has.
MOVES = {
    "entity_identifiers": "SELECT id FROM entity_identifiers WHERE entity_id = :drop",
    "entity_notes": "SELECT id FROM entity_notes WHERE entity_id = :drop",
    "entity_mentions": "SELECT id FROM entity_mentions WHERE entity_id = :drop",
    "entity_activities": "SELECT id FROM entity_activities WHERE entity_id = :drop",
    "entity_external_refs": "SELECT id FROM entity_external_refs WHERE entity_id = :drop",
    "entity_sources": (
        "SELECT s.id FROM entity_sources s WHERE s.entity_id = :drop AND NOT EXISTS (SELECT 1 FROM entity_sources k "
        "WHERE k.entity_id = :keep AND k.source_type = s.source_type AND k.source_id = s.source_id)"
    ),
}
# A single-value field the kept entity lacks moves; one it has stays on the merged row (which redirects to the kept one).
VALUE_MOVES = (
    "SELECT v.id FROM entity_values v WHERE v.entity_id = :drop AND (v.single_value = 0 OR NOT EXISTS "
    "(SELECT 1 FROM entity_values k WHERE k.entity_id = :keep AND k.entity_attribute_id = v.entity_attribute_id "
    "AND k.single_value = 1))"
)


# ---------------------------------------------------------------------------------------------------- small helpers
def _rows(c, sql, args=()):
    cur = c.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _one(c, sql, args=()):
    found = _rows(c, sql, args)
    return found[0] if found else None


def _count(c, sql, args=()):
    """The one number a count query returns."""
    return int(c.execute(sql, args).fetchone()[0])


def _id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _domain(value):
    return str(value or "").strip().lower()


def _limit(limit):
    return max(1, min(int(limit or LIMIT), LIMIT))


def _encode(parts):
    return base64.urlsafe_b64encode(json.dumps(parts).encode()).decode()


def _decode(token):
    try:
        return json.loads(base64.urlsafe_b64decode(str(token).encode()).decode())
    except (ValueError, TypeError) as error:
        raise ValueError("the cursor is not one this list returned") from error


def _key(c, actor, key, what):
    text = str(key or "").strip()
    if not KEY.match(text):
        H.refuse(c, actor, "kind", f"a {what} key is lowercase letters, digits and underscores, starting with a letter")
    return text


def _required(c, actor, value, what):
    text = str(value or "").strip()
    if not text:
        H.refuse(c, actor, "lint", f"give the {what}")
    return text


def _author(c, actor):
    """The author a note or a row carries: the actor's kind and id (`human`, `ana`), from the same key a reader has."""
    key = V.actor_key(c, actor) or str(actor)
    kind, _, ident = key.partition(":")
    return kind, ident


def _type(c, actor, key):
    row = _one(c, "SELECT * FROM entity_types WHERE key = ?", (key,))
    if row is None:
        H.refuse(c, actor, "kind", f"{key} is not an entity type; see hub directory types")
    if row["archived_at"]:
        H.refuse(c, actor, "kind", f"{key} is archived")
    return row


def _attribute(c, actor, type_key, key):
    row = _one(
        c, "SELECT * FROM entity_attributes WHERE entity_type = ? AND key = ? AND archived_at IS NULL", (type_key, key)
    )
    if row is None:
        H.refuse(c, actor, "kind", f"{type_key} has no attribute {key}; add it with hub directory attribute add")
    return row


def _live(c, actor, entity_id):
    row = _one(c, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if row is None:
        H.refuse(c, actor, "not_found", f"no entity {entity_id}")
    if row["status"] == "merged":
        H.refuse(c, actor, "merged", f"{entity_id} is merged into {row['merged_into_id']}; write to that one")
    return row


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "entity"


def _new_entity_id(c, type_key, name):
    base = f"{type_key}/{_slug(name)}"
    candidate, n = base, 2
    while c.execute("SELECT 1 FROM entities WHERE id = ?", (candidate,)).fetchone():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _visible_rows(c, r, ids):
    if not ids:
        return []
    cl, params = V.clause(r)
    return _rows(
        c,
        f"SELECT e.* FROM entities e WHERE e.id IN (SELECT value FROM json_each(?)) AND {cl} ORDER BY e.name_key, e.id",
        [json.dumps(ids), *params],
    )


def _follow(c, ids):
    """Each id, or the entity it was merged into (a merged row answers with the kept one, up to five merges)."""
    out = []
    for entity_id in ids:
        current = entity_id
        for _ in range(5):
            row = _one(c, "SELECT status, merged_into_id FROM entities WHERE id = ?", (current,))
            if not row or row["status"] != "merged" or not row["merged_into_id"]:
                break
            current = row["merged_into_id"]
        out.append(current)
    return list(dict.fromkeys(out))


def _today():
    return H.now()[:10]


def _refresh_self_keys(c, entity_id):
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
def self_id(c):
    """Our company's id. Market makes company/self at setup; the first read that finds it unflagged flags it self."""
    row = c.execute("SELECT id FROM entities WHERE self = 1").fetchone()
    if row:
        return row[0]
    if c.execute("SELECT 1 FROM entities WHERE id = ?", (SELF_ID,)).fetchone():
        c.execute("UPDATE entities SET self = 1 WHERE id = ?", (SELF_ID,))
        return SELF_ID
    return None


def _set_kind(c, entity_id, kind, source_type):
    """An organization's kind, when it has none. A kind already set is left alone."""
    c.execute(
        "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, source_id, "
        "confidence, updated_at) SELECT ?, ?, 'attr_organization_kind', 1, ?, ?, '', 0.9, ? WHERE NOT EXISTS "
        "(SELECT 1 FROM entity_values WHERE entity_id = ? AND entity_attribute_id = 'attr_organization_kind')",
        (_id("val"), entity_id, kind, source_type, H.now(), entity_id),
    )


def ensure_self(c, *, company_name, domain=""):
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
    if domain:
        c.execute(
            "UPDATE entities SET primary_domain = ? WHERE id = ? AND COALESCE(primary_domain, '') = ''",
            (domain, current),
        )
    _set_kind(c, current, "company", "system")
    return current


def backfill_kinds(c):
    """Organizations Market wrote before they had a kind are companies. Idempotent: a row with a kind is left alone."""
    c.execute(
        "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, source_id, "
        "confidence, updated_at) SELECT 'val_' || e.id, e.id, 'attr_organization_kind', 1, 'company', "
        "'market', '', 0.9, ? FROM entities e WHERE e.type = 'organization' AND NOT EXISTS (SELECT 1 FROM entity_values v "
        "WHERE v.entity_id = e.id AND v.entity_attribute_id = 'attr_organization_kind')",
        (H.now(),),
    )


def boot(c):
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
def _number(c, actor, attr, raw):
    try:
        number = float(raw)
    except (TypeError, ValueError):
        H.refuse(c, actor, "kind", f"{attr['key']} needs a number")
    if not math.isfinite(number):
        H.refuse(c, actor, "kind", f"{attr['key']} needs a finite number")
    return number


def _coerce(c, actor, attr, raw):
    """A raw value as the columns it is stored in, and its canonical form (what history and a read show)."""
    vt = attr["value_type"]
    if vt in TEXT_TYPES:
        text = "" if raw is None else str(raw).strip()
        if vt == "email":
            text = text.lower()
        if vt == "domain":
            text = _domain(text)
        if not text:
            H.refuse(c, actor, "kind", f"{attr['key']} needs a value")
        return {"cols": {"text_value": text}, "canonical": text}
    if vt in NUMBER_TYPES:
        number = _number(c, actor, attr, raw)
        return {"cols": {"number_value": number}, "canonical": number}
    if vt == "checkbox":
        flag = str(raw).strip().lower() in ("1", "true", "yes", "y") if isinstance(raw, str) else bool(raw)
        return {"cols": {"number_value": 1.0 if flag else 0.0}, "canonical": flag}
    if vt == "currency":
        amount, code = (raw.get("amount"), raw.get("currency")) if isinstance(raw, dict) else (raw, "USD")
        number = _number(c, actor, attr, amount)
        code = str(code or "USD").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            H.refuse(c, actor, "kind", f"{attr['key']} needs a three-letter currency code")
        return {"cols": {"number_value": number, "currency": code}, "canonical": {"amount": number, "currency": code}}
    if vt == "date":
        text = str(raw or "").strip()
        try:
            date.fromisoformat(text)
        except ValueError:
            H.refuse(c, actor, "kind", f"{attr['key']} needs a date as YYYY-MM-DD")
        return {"cols": {"date_value": text}, "canonical": text}
    if vt == "timestamp":
        try:
            stamp = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00")).isoformat()
        except ValueError:
            H.refuse(c, actor, "kind", f"{attr['key']} needs a timestamp")
        return {"cols": {"timestamp_value": stamp}, "canonical": stamp}
    if vt in ("select", "status"):
        key = str(raw or "").strip()
        options = {option["key"] for option in json.loads(attr["options"] or "[]")}
        if key not in options:
            H.refuse(c, actor, "kind", f"{key} is not an option of {attr['key']}")
        return {"cols": {"option_key": key}, "canonical": key}
    H.refuse(c, actor, "kind", f"{vt} is not a value type")


def _value_of(row, value_type):
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


def _cols(values):
    return tuple(values.get(name) for name in VALUE_COLUMNS)


def _set_value(c, actor, entity_id, attr, raw, *, source_type, source_id, evidence_id, confidence):
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
            H.refuse(c, actor, "duplicate", f"{attr['key']} {cols[column]} is already on {clash['entity_id']}")
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
            "INSERT INTO entity_value_history (id, entity_id, entity_attribute_id, value_json, source_type, source_id, "
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


def _check_evidence(c, actor, evidence_id):
    if evidence_id and not c.execute("SELECT 1 FROM market_evidence WHERE id = ?", (evidence_id,)).fetchone():
        H.refuse(c, actor, "evidence", f"no evidence {evidence_id}; cite a row that exists")


# ------------------------------------------------------------------------------------------------------- writes
def _match(c, type_key, match):
    """The live entities of one type a match names: by email, domain or name (an identifier), or by id."""
    if match.get("id"):
        rows = _rows(
            c, "SELECT id FROM entities WHERE id = ? AND type = ? AND status <> 'merged'", (match["id"], type_key)
        )
        return [row["id"] for row in rows]
    found = []
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
    c, actor, type_key, *, match, values=None, name=None, evidence_id=None, source_type="", source_id="", confidence=0.6
):
    """Create or update one entity of a type. `match` names it: {"id"}, {"email"}, {"domain"} or {"name"}. Several matches
    are refused as ambiguous, never guessed. A new entity needs a name (given, or the match value)."""
    H._writer(c, actor)
    _type(c, actor, type_key)
    _check_evidence(c, actor, evidence_id)
    match = {k: v for k, v in dict(match or {}).items() if v}
    values = dict(values or {})
    if "name" in values:
        name = name or values.pop("name")
    found = _match(c, type_key, match)
    if len(found) > 1:
        H.refuse(c, actor, "ambiguous", f"{', '.join(found)} all match; name one by id")
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
        kind_, ident = H.actor_kind(actor), H.actor_id(actor)
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
    changed = []
    for key, raw in values.items():
        attr = _attribute(c, actor, type_key, key)
        if attr["owner_type"] == "connection" and actor != f"connection:{attr['owner_id']}":
            H.refuse(c, actor, "owner", f"{key} comes from its connection; change it there")
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
    c,
    actor,
    from_id,
    link_key,
    to_id,
    *,
    valid_from_on=None,
    valid_until_on=None,
    confidence=0.6,
    source_type="",
    source_id="",
    evidence_id=None,
    properties=None,
):
    """A typed, dated link between two entities. The link type says which ends it may join. A symmetric link is stored
    once, with its ends in order. A link already current is returned, not written twice."""
    H._writer(c, actor)
    lt = _one(c, "SELECT * FROM link_types WHERE key = ? AND archived_at IS NULL", (link_key,))
    if lt is None:
        H.refuse(c, actor, "kind", f"{link_key} is not a link type; see hub directory link-type add")
    _check_evidence(c, actor, evidence_id)
    a, b = _live(c, actor, from_id), _live(c, actor, to_id)
    if a["id"] == b["id"]:
        H.refuse(c, actor, "kind", "an entity cannot link to itself")
    if lt["from_entity_type"] and a["type"] != lt["from_entity_type"]:
        H.refuse(c, actor, "kind", f"{link_key} starts at a {lt['from_entity_type']}, not a {a['type']}")
    if lt["to_entity_type"] and b["type"] != lt["to_entity_type"]:
        H.refuse(c, actor, "kind", f"{link_key} ends at a {lt['to_entity_type']}, not a {b['type']}")
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
            H.actor_kind(actor) or "",
            H.actor_id(actor) or "",
            now,
            now,
        ),
    )
    _refresh_self_keys(c, start)
    _refresh_self_keys(c, end)
    return link_id


def unlink(c, actor, link_id):
    """Ends a link: it stops being current today. The row stays, so its history reads."""
    H._writer(c, actor)
    row = _one(c, "SELECT * FROM entity_links WHERE id = ?", (link_id,))
    if row is None:
        H.refuse(c, actor, "not_found", f"no link {link_id}")
    today = _today()
    if row["valid_until_on"] is None or row["valid_until_on"] > today:
        c.execute("UPDATE entity_links SET valid_until_on = ?, updated_at = ? WHERE id = ?", (today, H.now(), link_id))
        _refresh_self_keys(c, row["from_entity_id"])
        _refresh_self_keys(c, row["to_entity_id"])
    return link_id


def note(c, actor, entity_id, body, *, audience="company", evidence_id=None):
    """A note on an entity. A `me` note is visible to its author only, and it is a restricted source the entity is known
    from, so an entity known only from one hides from everyone else."""
    H._writer(c, actor)
    text = _required(c, actor, body, "note")
    if audience not in ("me", "company"):
        H.refuse(c, actor, "kind", "a note's audience is me or company")
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
    if audience == "me":
        c.execute(
            "INSERT OR IGNORE INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
            "VALUES (?, ?, 'note_me', ?, 'restricted', ?)",
            (_id("src"), entity_id, key, now),
        )
    return note_id


def add_source(c, actor, entity_id, source_type, source_id, *, visibility="company"):
    """Records a source an entity is known from. `company` shows the entity to everyone; `restricted` shows it to readers
    the source's checker (visible.CHECKERS) names, so a restricted source needs a checker."""
    H._writer(c, actor)
    _live(c, actor, entity_id)
    if visibility not in ("company", "restricted"):
        H.refuse(c, actor, "kind", "a source's visibility is company or restricted")
    if visibility == "restricted" and source_type not in V.CHECKERS:
        H.refuse(c, actor, "kind", f"{source_type} has no read checker, so it cannot be restricted")
    source_id = _required(c, actor, source_id, "source id")
    c.execute(
        "INSERT OR IGNORE INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_id("src"), entity_id, source_type, source_id, visibility, H.now()),
    )
    return entity_id


def merge(c, actor, keep_id, drop_id):
    """Merges `drop` into `keep`: the drop's identifiers, links, values, notes, mentions, activities, external refs and
    sources move to the kept entity, and the drop redirects to it. The move is logged (entity_merges) so unmerge reverses
    it exactly. Self cannot be merged away, and the two must be one type."""
    H._writer(c, actor)
    if keep_id == drop_id:
        H.refuse(c, actor, "kind", "an entity cannot merge into itself")
    keep = _live(c, actor, keep_id)
    drop = _live(c, actor, drop_id)
    if drop["self"]:
        H.refuse(c, actor, "kind", "self cannot be merged away")
    if keep["type"] != drop["type"]:
        H.refuse(c, actor, "kind", f"a {drop['type']} merges only into a {drop['type']}")
    now = H.now()
    params = {"keep": keep_id, "drop": drop_id}
    tables = {}
    for table, sql in MOVES.items():
        ids = [row[0] for row in c.execute(sql, params)]
        if ids:
            c.executemany(
                f"UPDATE {table} SET entity_id = :keep WHERE id = :id", [{"keep": keep_id, "id": i} for i in ids]
            )
            tables[table] = ids
    values = [row[0] for row in c.execute(VALUE_MOVES, params)]
    if values:
        c.executemany(
            "UPDATE entity_values SET entity_id = :keep WHERE id = :id", [{"keep": keep_id, "id": i} for i in values]
        )
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
            json.dumps({"tables": tables, "values": values, "links": moved_links}, sort_keys=True),
            json.dumps(prior, sort_keys=True),
            H.actor_kind(actor) or "",
            H.actor_id(actor) or "",
            now,
        ),
    )
    _refresh_self_keys(c, keep_id)
    return keep_id


def unmerge(c, actor, drop_id):
    """Reverses the last merge of `drop_id` as logged: its rows go back, its status and counts are restored."""
    H._writer(c, actor)
    log = _one(
        c,
        "SELECT * FROM entity_merges WHERE entity_id = ? AND undone_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (drop_id,),
    )
    if log is None:
        H.refuse(c, actor, "not_found", f"{drop_id} has no merge to undo")
    keep_id = log["into_entity_id"]
    moved, prior = json.loads(log["moved"]), json.loads(log["prior"])
    now = H.now()
    for table, ids in moved["tables"].items():
        c.executemany(f"UPDATE {table} SET entity_id = :drop WHERE id = :id", [{"drop": drop_id, "id": i} for i in ids])
    if moved["values"]:
        c.executemany(
            "UPDATE entity_values SET entity_id = :drop WHERE id = :id",
            [{"drop": drop_id, "id": i} for i in moved["values"]],
        )
    c.executemany(
        "UPDATE entity_links SET from_entity_id = :from_id, to_entity_id = :to_id, updated_at = :now WHERE id = :id",
        [{"from_id": f, "to_id": t, "now": now, "id": i} for i, f, t in moved["links"]],
    )
    c.execute(
        "UPDATE entities SET mentions_count = mentions_count - ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
        (prior["drop"]["mentions_count"], prior["keep"]["last_activity_at"], now, keep_id),
    )
    c.execute(
        "UPDATE entities SET status = ?, merged_into_id = ?, updated_at = ? WHERE id = ?",
        (prior["drop"]["status"], prior["drop"]["merged_into_id"], now, drop_id),
    )
    c.execute("UPDATE entity_merges SET undone_at = ? WHERE id = ?", (now, log["id"]))
    _refresh_self_keys(c, keep_id)
    _refresh_self_keys(c, drop_id)
    return drop_id


def add_type(c, actor, key, name, description, *, plural_name="", icon=""):
    """A new entity type. Its description is required; a near-duplicate of an existing type is refused with that type."""
    H._writer(c, actor)
    key = _key(c, actor, key, "type")
    name = _required(c, actor, name, "name")
    description = _required(c, actor, description, "description")
    if _one(c, "SELECT key FROM entity_types WHERE key = ?", (key,)):
        H.refuse(c, actor, "duplicate", f"the type {key} exists")
    stem = key.replace("_", "").rstrip("s")
    for existing in _rows(c, "SELECT key FROM entity_types"):
        if existing["key"].replace("_", "").rstrip("s") == stem:
            H.refuse(c, actor, "duplicate", f"{existing['key']} is close to {key}; use it")
    kind_, ident = H.actor_kind(actor) or "", H.actor_id(actor) or ""
    c.execute(
        "INSERT INTO entity_types (id, key, name, plural_name, icon, built_in, mode, description, created_by_type, "
        "created_by_id, created_at) VALUES (?, ?, ?, ?, ?, 0, 'native', ?, ?, ?, ?)",
        (f"etype_{key}", key, name, plural_name or f"{name}s", icon, description, kind_, ident, H.now()),
    )
    return key


def add_attribute(
    c,
    actor,
    type_key,
    key,
    name,
    value_type,
    description,
    *,
    options=None,
    multiple=False,
    unique_value=False,
    required=False,
    sensitive=False,
    owner=None,
):
    """A new attribute of a type. A select or status needs its options; an owner of `connection:<id>` makes the field
    that connection's (only that connection writes it)."""
    H._writer(c, actor)
    _type(c, actor, type_key)
    key = _key(c, actor, key, "attribute")
    name = _required(c, actor, name, "name")
    description = _required(c, actor, description, "description")
    if value_type not in VALUE_TYPES:
        H.refuse(c, actor, "kind", f"a value type is {'|'.join(VALUE_TYPES)}, not {value_type}")
    opts = [dict(option) for option in (options or [])]
    if value_type in ("select", "status") and not opts:
        H.refuse(c, actor, "kind", f"a {value_type} needs options")
    if _one(c, "SELECT id FROM entity_attributes WHERE entity_type = ? AND key = ?", (type_key, key)):
        H.refuse(c, actor, "duplicate", f"{type_key} already has {key}")
    owner_type, owner_id = "tico", ""
    if owner:
        if not str(owner).startswith("connection:"):
            H.refuse(c, actor, "kind", "an owner is tico or connection:<id>")
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
            H.actor_kind(actor) or "",
            H.actor_id(actor) or "",
            H.now(),
        ),
    )
    return key


def add_link_type(
    c,
    actor,
    key,
    name,
    inverse_name,
    description,
    *,
    from_type=None,
    to_type=None,
    inherits_rules=False,
    symmetric=False,
):
    """A new link type. `inherits_rules` says whether a rule toward the far end reaches the near end (works_at does; a
    competitor does not). Its description is required."""
    H._writer(c, actor)
    key = _key(c, actor, key, "link type")
    name = _required(c, actor, name, "name")
    inverse_name = _required(c, actor, inverse_name, "inverse name")
    description = _required(c, actor, description, "description")
    for end in (from_type, to_type):
        if end:
            _type(c, actor, end)
    if _one(c, "SELECT key FROM link_types WHERE key = ?", (key,)):
        H.refuse(c, actor, "duplicate", f"the link type {key} exists")
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
            H.actor_kind(actor) or "",
            H.actor_id(actor) or "",
            H.now(),
        ),
    )
    return key


# ------------------------------------------------------------------------------------------------------- reads
def get(c, who, ref):
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
    return _visible_rows(c, r, _follow(c, list(dict.fromkeys(ids))))


def find(c, who, type_key, *, name=None, status=None, link=None, after=None, limit=LIMIT):
    """A page of one type's visible entities, by name (a prefix), status, or a self link key (customer_of). Keyset
    paged on (name_key, id): `after` is the `next` the last page gave. The count stops at COUNT_CAP and says so."""
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
    if link:
        where.append("(' ' || e.self_link_keys || ' ') LIKE ?")
        args.append(f"% {link} %")
    if after:
        name_key, row_id = _decode(after)
        where.append("(e.name_key, e.id) > (?, ?)")
        args += [name_key, row_id]
    condition = " AND ".join(where)
    rows = _rows(
        c, f"SELECT e.* FROM entities e WHERE {condition} ORDER BY e.name_key, e.id LIMIT ?", [*args, limit + 1]
    )
    more = len(rows) > limit
    rows = rows[:limit]
    total = _count(
        c, f"SELECT count(*) FROM (SELECT 1 FROM entities e WHERE {condition} LIMIT ?)", [*args, COUNT_CAP + 1]
    )
    return {
        "rows": rows,
        "next": _encode([rows[-1]["name_key"], rows[-1]["id"]]) if more and rows else None,
        "count": min(total, COUNT_CAP),
        "approximate": total > COUNT_CAP,
    }


def card(c, who, entity_id):
    """An entity as its reader sees it: its visible values (with source and age), its current links to visible entities,
    and its notes the reader may read. None when the entity does not show."""
    r = V.reader(c, who)
    found = _visible_rows(c, r, _follow(c, [entity_id]))
    if not found:
        return None
    ent = found[0]
    values = {}
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
        f"AND o.status <> 'merged' AND {cl} ORDER BY l.link_key, o.name",
        [me, me, me, today, *cparams],
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
    return {
        "id": ent["id"],
        "type": ent["type"],
        "name": ent["name"],
        "status": ent["status"],
        "self": bool(ent["self"]),
        "primary_email": ent["primary_email"],
        "primary_domain": ent["primary_domain"],
        "values": values,
        "links": links,
        "notes": notes,
    }


def history(c, who, entity_id, attribute_key, *, after=None, limit=LIMIT):
    """A field's past values, newest first, keyset paged on (active_until, id). None when the entity or the field does
    not show to the reader."""
    r = V.reader(c, who)
    if not _visible_rows(c, r, _follow(c, [entity_id])):
        return None
    attr = _one(
        c,
        "SELECT a.* FROM entity_attributes a JOIN entities e ON e.id = ? AND e.type = a.entity_type WHERE a.key = ?",
        (entity_id, attribute_key),
    )
    if attr is None or not V.attribute_visible(r, attr):
        return None
    limit = _limit(limit)
    where, args = "h.entity_id = ? AND h.entity_attribute_id = ?", [entity_id, attr["id"]]
    if after:
        until, row_id = _decode(after)
        where += " AND (h.active_until, h.id) < (?, ?)"
        args += [until, row_id]
    rows = _rows(
        c,
        f"SELECT h.* FROM entity_value_history h WHERE {where} ORDER BY h.active_until DESC, h.id DESC LIMIT ?",
        [*args, limit + 1],
    )
    more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row["value"] = json.loads(row.pop("value_json"))
    return {"rows": rows, "next": _encode([rows[-1]["active_until"], rows[-1]["id"]]) if more and rows else None}
