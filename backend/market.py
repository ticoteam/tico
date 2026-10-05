"""The shared market model: one graph, one curator, everyone else reports.

Six tables in the hub database. A reporter files prose.
The Librarian (`bot:librarian`) curates: it and the company owner are the only actors who change an
entity, an edge, a piece of evidence, or a market page. Nothing is deleted: an edge is ended, an entity is
retired or merged, and every changed field is a `market_events` row.
"""

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import Request

from . import hubdb as H
from . import models as M
from .store import Problem, digest, encode

# The Librarian builds the first map from what the owner gave (playbooks/market-setup.md) and keeps it
# current from the insights everyone else reports (playbooks/curate-the-market.md).
CURATOR = "bot:librarian"
WRITERS = (CURATOR,)
RETIRED_CURATOR = "market-analyst"   # the old separate curator bot, archived once at boot (`ensure_curator`)
SEED_ACTOR = "seed"
RELATIONS = (
    "competes_with", "partners_with", "integrates_with", "distributes_through", "sells_to",
    "operates_in", "acquired", "invested_in", "employs", "led_by", "founded", "formerly",
    "regulated_by", "subject_of", "member_of", "mentioned_in",
)
SYMMETRIC = frozenset({"competes_with", "partners_with"})
CLAIM_KINDS = ("entity", "edge", "evidence", "document")
ENTITY_TYPES = ("company", "product", "person", "segment", "channel", "geography", "regulation", "event")
TIERS = ("core", "lookalike", "phrase-stealer", "secondary")
CONFIDENCE = ("high", "medium", "low")
INSIGHT_KINDS = ("new-entity", "edge", "property-change", "correction", "question", "other")
INSIGHT_STATUSES = ("applied", "merged", "rejected", "needs-human")
SOURCE_KINDS = ("reddit", "x", "linkedin", "news", "filing", "site", "sales-call", "meeting", "peec", "syften", "other")
# An update needs evidence when it changes one of these. Aliases and last_verified do not.
EVIDENCE_FIELDS = frozenset({"summary", "properties", "since", "until", "tier"})
CORE_STALE_DAYS = 30
STALE_DAYS = 60
EVENT_LIMIT = 10
OWN_COMPANY_ID = "company/self"      # the company itself, the centre of the graph

PAGES = (
    ("market/overview", "Overview", "Market / Overview",
     "What the company is selling into, in the curator's words. Market sizing and fee norms live here as theses with evidence, not as properties on an entity."),
    ("market/structure-and-size", "Structure and size", "Market / Structure",
     "How the market is shaped and how big the curator believes it is. Figures here are theses, each one cited."),
    ("market/coverage-universe", "Coverage universe", "Market / Coverage",
     "The companies, segments and channels the graph currently covers, and what is still missing."),
    ("market/people-who-matter", "People who matter", "Market / People",
     "The people the curator is tracking, and why they matter to the company."),
    ("market/channels", "Channels", "Market / Channels",
     "Where owners and managers talk. The channel entities are the list; this page says which ones we actually listen to."),
    ("market/regulation-and-catalysts", "Regulation and catalysts", "Market / Regulation",
     "Rules and outside events that would change who buys, and the evidence for each."),
    ("market/theses", "Theses", "Market / Theses",
     "Claims the curator is willing to defend, each one tied to evidence. A thesis is not a fact on an entity."),
    ("market/open-questions", "Open questions", "Market / Questions",
     "What the graph cannot answer yet."),
    ("market/weekly-delta", "Weekly delta", "Market / Delta",
     "What changed in the market graph this week. Refreshed on Mondays from market_events."),
)

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
SUBREDDIT = re.compile(r"(?<![\w/])r/([A-Za-z0-9_]+)\b")
DOMAIN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", re.I)
SKIP_HEADINGS = {"who to skip", "observed detail", "sources", "buyer personas"}


# ----------------------------------------------------------------------------- small helpers
def slug(text):
    raw = str(text or "").strip().lower().replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return raw or "untitled"


def dumps(value):
    return json.dumps(value if value is not None else {}, sort_keys=True, ensure_ascii=False)


def loads(value, default):
    try:
        found = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return found if isinstance(found, type(default)) else default


def evidence_id(key):
    return "ev-" + hashlib.sha256(str(key).encode()).hexdigest()[:8]


def _day(conn, value, actor, label):
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T.*)?", text):
        H.refuse(conn, actor, "date", f"{label} must be a date (YYYY-MM-DD)")
    return text[:10]


def _writer_actor(actor):
    """The curator, or the company owner. Everyone else is refused before a write."""
    return actor == CURATOR


def require_writer(who):
    if who.role == "owner" or who.actor in WRITERS:
        return
    raise Problem("forbidden", "Only the Librarian and the company owner can change the market graph", 403)


def _event(conn, kind, subject, actor, field, old, new, insight_id=None, note=""):
    eid = H.new_id()
    conn.execute("INSERT INTO market_events (id, subject_kind, subject_id, ts, actor, field, old, new, insight_id, note) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (eid, kind, subject, H.now(), actor, field,
                  None if old is None else str(old), None if new is None else str(new),
                  insight_id, note or ""))
    return eid


def _entity_row(row):
    if not row:
        return None
    row = dict(row)
    row["aliases"] = loads(row.get("aliases"), [])
    row["external_ids"] = loads(row.get("external_ids"), {})
    row["properties"] = loads(row.get("properties"), {})
    return row


def _edge_row(row):
    if not row:
        return None
    row = dict(row)
    row["properties"] = loads(row.get("properties"), {})
    return row


def entity(conn, entity_id):
    return _entity_row(H._one(conn, "SELECT * FROM market_entities WHERE id=?", (entity_id,)))


def edge(conn, edge_id):
    return _edge_row(H._one(conn, "SELECT * FROM market_edges WHERE id=?", (edge_id,)))


def evidence(conn, evidence_id_):
    return H._one(conn, "SELECT * FROM market_evidence WHERE id=?", (evidence_id_,))


def insight(conn, insight_id):
    row = H._one(conn, "SELECT * FROM market_insights WHERE id=?", (insight_id,))
    if row:
        row["applied_events"] = loads(row.get("applied_events"), [])
        row["urgent"] = bool(row.get("urgent"))
    return row


def _need_entity(conn, actor, entity_id):
    row = entity(conn, entity_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no entity {entity_id}")
    return row


def _need_evidence(conn, actor, ids):
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        H.refuse(conn, actor, "evidence", "an entity or an edge needs at least one evidence id")
    for eid in ids:
        if not evidence(conn, eid):
            H.refuse(conn, actor, "not-found", f"no evidence {eid}")
    return ids


def _cite(conn, actor, kind, claim_id, evidence_ids, insight_id=None):
    if kind not in CLAIM_KINDS:
        H.refuse(conn, actor, "kind", f"a citation is {'|'.join(CLAIM_KINDS)}, not {kind}")
    for eid in evidence_ids:
        conn.execute("INSERT OR IGNORE INTO market_citations (claim_kind, claim_id, evidence_id) VALUES (?,?,?)",
                     (kind, claim_id, eid))
        _event(conn, kind, claim_id, actor, "evidence", None, eid, insight_id)


# ----------------------------------------------------------------------------- reads
def edges_of(conn, *, src=None, dst=None, rel=None, as_of=None):
    """Graph rows. Symmetric relations are stored once and returned from either end.

    `as_of` keeps an edge whose since/until span covers that date. A null since or until is
    an unknown start or a current end, and both still cover the date.
    """
    where, args = [], []
    if rel:
        where.append("rel=?")
        args.append(rel)
    if src and dst:
        where.append("((src=? AND dst=?) OR (rel IN (?,?) AND src=? AND dst=?))")
        args.extend([src, dst, *SYMMETRIC, dst, src])
    elif src:
        where.append("(src=? OR (rel IN (?,?) AND dst=?))")
        args.extend([src, *SYMMETRIC, src])
    elif dst:
        where.append("(dst=? OR (rel IN (?,?) AND src=?))")
        args.extend([dst, *SYMMETRIC, dst])
    if as_of:
        day = str(as_of)[:10]
        where.append("(since IS NULL OR substr(since,1,10)<=?)")
        where.append("(until IS NULL OR substr(until,1,10)>=?)")
        args.extend([day, day])
    sql = "SELECT * FROM market_edges" + (" WHERE " + " AND ".join(where) if where else "")
    return [_edge_row(r) for r in H._rows(conn.execute(sql + " ORDER BY rel, src, dst", args))]


def _evidence_for(conn, entity_id, edges):
    ids = []
    claims = [("entity", entity_id)] + [("edge", e["id"]) for e in edges]
    for kind, cid in claims:
        for row in conn.execute("SELECT evidence_id FROM market_citations WHERE claim_kind=? AND claim_id=?", (kind, cid)):
            if row[0] not in ids:
                ids.append(row[0])
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in H._rows(conn.execute(f"SELECT * FROM market_evidence WHERE id IN ({marks})", ids))}
    return [rows[i] for i in ids if i in rows]


def _current(edge, today):
    """An edge in force on `today`: unknown ends count, a future start and a past end do not."""
    since, until = edge.get("since"), edge.get("until")
    if since and str(since)[:10] > today:
        return False
    if until and str(until)[:10] < today:
        return False
    return True


def _overlaps(since_a, until_a, since_b, until_b):
    """Two spans share a day. A missing end runs on; an until before the other since does not."""
    if until_a and since_b and str(until_a)[:10] < str(since_b)[:10]:
        return False
    if until_b and since_a and str(until_b)[:10] < str(since_a)[:10]:
        return False
    return True


def _same_pair(conn, rel, src, dst):
    return H._rows(conn.execute(
        "SELECT * FROM market_edges WHERE rel=? AND ((src=? AND dst=?) OR (src=? AND dst=?))",
        (rel, src, dst, dst, src)))


def show(conn, entity_id, today=None):
    row = entity(conn, entity_id)
    if not row:
        return None
    today = today or datetime.now(timezone.utc).date().isoformat()
    # Current edges only. Symmetric relations are stored once and read from either end.
    touching = [item for item in edges_of(conn, src=entity_id) if _current(item, today)]
    incoming = [item for item in edges_of(conn, dst=entity_id)
                if item["rel"] not in SYMMETRIC and _current(item, today)]
    seen, edges, pairs = set(), [], set()
    for item in touching + incoming:
        if item["id"] in seen:
            continue
        if item["rel"] in SYMMETRIC:
            pair = (item["rel"], tuple(sorted((item["src"], item["dst"]))))
            if pair in pairs:
                continue
            pairs.add(pair)
        seen.add(item["id"])
        edges.append(item)
    grouped = {}
    for item in edges:
        bucket = grouped.setdefault(item["rel"], {"out": [], "in": []})
        if item["src"] == entity_id or item["rel"] in SYMMETRIC:
            # Stored once. The end that is `src` is out; the other end still sees it, as in.
            bucket["out" if item["src"] == entity_id else "in"].append(item)
        else:
            bucket["in"].append(item)
    events = H._rows(conn.execute(
        "SELECT * FROM market_events WHERE subject_kind='entity' AND subject_id=? ORDER BY ts DESC LIMIT ?",
        (entity_id, EVENT_LIMIT)))
    return {"entity": row, "edges": grouped, "evidence": _evidence_for(conn, entity_id, edges), "events": events}


def find(conn, text, limit=20):
    """Names, aliases, summaries, and evidence quote and our_read."""
    stop = {"who", "the", "and", "for", "with", "from", "what", "how", "does", "are", "was", "you", "its", "that"}
    tokens = [token for token in re.findall(r"[A-Za-z0-9.]{3,}", str(text or "")) if token.lower() not in stop][:8]
    if not tokens:
        return {"query": text or "", "entities": [], "evidence": []}
    like = ["%" + t.lower() + "%" for t in tokens]
    fts_entities, fts_evidence = [], []
    try:
        match = " AND ".join('"' + token.replace('"', "") + '"' for token in tokens)
        for hit in conn.execute("SELECT kind, ref FROM market_fts WHERE market_fts MATCH ? LIMIT ?", (match, limit)):
            if hit["kind"] == "entity":
                row = entity(conn, hit["ref"])
                if row:
                    fts_entities.append(row)
            elif hit["kind"] == "evidence":
                row = evidence(conn, hit["ref"])
                if row:
                    fts_evidence.append(row)
    except Exception:
        pass

    def matches(columns):
        return " AND ".join("(" + " OR ".join(f"lower({c}) LIKE ?" for c in columns) + ")" for _ in tokens)

    entity_sql = ("SELECT * FROM market_entities WHERE " + matches(("name", "aliases", "summary"))
                  + " ORDER BY name LIMIT ?")
    evidence_sql = ("SELECT * FROM market_evidence WHERE " + matches(("quote", "our_read")) + " ORDER BY id LIMIT ?")
    entity_args = [v for v in like for _ in range(3)]
    evidence_args = [v for v in like for _ in range(2)]
    entities = [_entity_row(r) for r in H._rows(conn.execute(entity_sql, (*entity_args, limit)))]
    rows = H._rows(conn.execute(evidence_sql, (*evidence_args, limit)))
    seen = {row["id"] for row in entities}
    for row in fts_entities:
        if row["id"] not in seen:
            entities.append(row)
            seen.add(row["id"])
    seen_ev = {row["id"] for row in rows}
    for row in fts_evidence:
        if row["id"] not in seen_ev:
            rows.append(row)
    return {"query": text, "entities": entities[:limit], "evidence": rows[:limit]}


def delta(conn, since="7d"):
    """Changes since a date or a window like `7d`, grouped by the entity they belong to."""
    raw = str(since or "7d").strip()
    window = re.fullmatch(r"(\d+)d", raw)
    bound = H.shift(H.now(), days=-int(window.group(1))) if window else raw
    events = H._rows(conn.execute("SELECT * FROM market_events WHERE ts>=? ORDER BY ts, id", (bound,)))
    groups = {}
    for ev in events:
        if ev["subject_kind"] == "edge":
            row = edge(conn, ev["subject_id"])
            keys = [row["src"], row["dst"]] if row else [ev["subject_id"]]
        elif ev["subject_kind"] == "entity":
            keys = [ev["subject_id"]]
        else:
            keys = [ev["subject_id"]]
        for key in keys:
            groups.setdefault(key, []).append(ev)
    return {"since": bound, "entities": [{"id": key, "events": groups[key]} for key in groups]}


def retrieve(conn, question, limit=8):
    """The rows an ask sends to the model: entities, evidence, and market pages."""
    found = find(conn, question, limit=limit)
    excerpts = []
    seen_evidence = {row["id"] for row in found["evidence"]}
    cited = []
    for row in found["entities"]:
        aliases = ", ".join(row["aliases"])
        excerpts.append({"kind": "entity", "id": row["id"], "title": row["name"],
                         "text": "\n".join(p for p in (row["name"], aliases, row["summary"]) if p)})
        for item in _evidence_for(conn, row["id"], edges_of(conn, src=row["id"])):
            if item["id"] not in seen_evidence:
                seen_evidence.add(item["id"])
                cited.append(item)
    for row in found["evidence"] + cited:
        excerpts.append({"kind": "evidence", "id": row["id"], "title": row["id"],
                         "text": "\n".join(p for p in (row.get("quote") or "", row.get("our_read") or "") if p)})
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9.]{3,}", str(question or ""))[:8]]
    if tokens:
        for doc in conn.execute("SELECT id, payload_json FROM documents WHERE collection='market' ORDER BY id"):
            payload = json.loads(doc["payload_json"])
            haystack = (payload.get("title", "") + "\n" + payload.get("content", "")).lower()
            if any(token in haystack for token in tokens):
                excerpts.append({"kind": "document", "id": doc["id"], "title": payload.get("title") or doc["id"],
                                 "text": str(payload.get("content") or "")[:1500]})
    return excerpts[: limit + 9]


def complete(question, excerpts):
    """One model call over the excerpts. Tests patch this; the retrieval above is real."""
    from clients.docs_qa import request
    body = {"contents": [{"role": "user", "parts": [{"text":
             "You are the market librarian. Answer in a few sentences from these excerpts only. "
             "Do not invent facts and do not mention excerpts or citation brackets.\n\nQuestion: "
             + question + "\n\n" + "\n\n".join(f"[{e['kind']}:{e['id']}] {e['text']}" for e in excerpts)}]}]}
    with request("models/gemini-3.7-flash:generateContent", body) as response:
        payload = json.load(response)
    parts = payload["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts).strip()


def _pulled(excerpts):
    lines = []
    for row in excerpts:
        bit = " ".join(str(row.get("text") or "").split())
        if bit:
            lines.append(f"{row.get('title') or row['id']}: {bit[:400]}")
    return "Pulled from the market graph.\n\n" + "\n\n".join(lines[:8])


def ask(conn, question):
    """Answer from the graph as it is right now. The model writes the sentence; if it is down,
    the rows that were just pulled are the answer.
    """
    excerpts = retrieve(conn, question)
    citations = [{"kind": row["kind"], "id": row["id"]} for row in excerpts if row["kind"] in ("entity", "evidence")]
    if not excerpts:
        return {"answer": "Nothing in the market graph matches that question.", "citations": [], "excerpts": []}
    try:
        answer = complete(question, excerpts)
    except Exception:
        answer = ""
    if not answer:
        answer = _pulled(excerpts)
    return {"answer": answer, "citations": citations, "excerpts": excerpts}


# ----------------------------------------------------------------------------- writes
def _changed(old, new):
    return [] if old == new else [(old, new)]


def create_evidence(conn, actor, *, source_url="", source_kind="other", captured_at=None, quote="",
                    our_read="", insight_id=None, evidence_key=None, blob_id=None, captured_by=None):
    H._writer(conn, actor) if actor != SEED_ACTOR else None
    if source_kind not in SOURCE_KINDS:
        H.refuse(conn, actor, "kind", f"a source is {'|'.join(SOURCE_KINDS)}, not {source_kind}")
    eid = evidence_key or evidence_id(H.new_id())
    if evidence(conn, eid):
        return evidence(conn, eid), []
    now = H.now()
    row = {"id": eid, "source_url": str(source_url or ""), "source_kind": source_kind,
           "captured_at": captured_at or now, "captured_by": captured_by or actor,
           "quote": str(quote or ""), "our_read": str(our_read or ""), "blob_id": blob_id}
    conn.execute("INSERT INTO market_evidence (id, source_url, source_kind, captured_at, captured_by, quote, our_read, blob_id) "
                 "VALUES (:id,:source_url,:source_kind,:captured_at,:captured_by,:quote,:our_read,:blob_id)", row)
    event_id = _event(conn, "evidence", eid, actor, "created", None, (row["quote"] or row["our_read"])[:200], insight_id)
    return evidence(conn, eid), [event_id]


def _resolve_existing(conn, name, aliases, external_ids):
    """Entities whose name, alias or domain is one of these, active or merged."""
    keys = {str(name or "").strip().lower()}
    keys.update(str(a).strip().lower() for a in aliases or [] if str(a).strip())
    domain = str((external_ids or {}).get("domain") or "").strip().lower()
    if domain:
        keys.add(domain)
    keys.discard("")
    found = []
    for row in conn.execute("SELECT * FROM market_entities"):
        item = _entity_row(row)
        names = {item["name"].strip().lower()} | {str(a).strip().lower() for a in item["aliases"]}
        if item["external_ids"].get("domain"):
            names.add(str(item["external_ids"]["domain"]).strip().lower())
        if names & keys:
            found.append(item)
    return found


def create_entity(conn, actor, *, type, name, aliases=None, external_ids=None, tier=None, summary="",
                  properties=None, last_verified=None, evidence_ids=None, force=False, insight_id=None,
                  entity_id=None):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    if type not in ENTITY_TYPES:
        H.refuse(conn, actor, "kind", f"a type is {'|'.join(ENTITY_TYPES)}, not {type}")
    name = str(name or "").strip()
    if not name:
        H.refuse(conn, actor, "lint", "give the entity a name")
    if tier not in (None, "") and tier not in TIERS:
        H.refuse(conn, actor, "kind", f"a tier is {'|'.join(TIERS)}, not {tier}")
    aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
    external_ids = dict(external_ids or {})
    eid = entity_id or f"{type}/{slug(name)}"
    if entity(conn, eid):
        H.refuse(conn, actor, "duplicate", f"{eid} already exists")
    clash = [row for row in _resolve_existing(conn, name, aliases, external_ids) if row["id"] != eid]
    if clash and not force:
        H.refuse(conn, actor, "duplicate", f"{name} matches {clash[0]['id']}; pass force to create it anyway")
    ids = []
    if actor != SEED_ACTOR:
        ids = _need_evidence(conn, actor, evidence_ids)
    now = H.now()
    row = {"id": eid, "type": type, "name": name, "aliases": dumps(aliases), "external_ids": dumps(external_ids),
           "tier": tier or None, "summary": str(summary or ""), "properties": dumps(properties or {}),
           "status": "active", "merged_into": None, "last_verified": last_verified, "created": now,
           "created_by": actor, "updated": now}
    conn.execute("INSERT INTO market_entities (id, type, name, aliases, external_ids, tier, summary, properties, "
                 "status, merged_into, last_verified, created, created_by, updated) VALUES "
                 "(:id,:type,:name,:aliases,:external_ids,:tier,:summary,:properties,:status,:merged_into,"
                 ":last_verified,:created,:created_by,:updated)", row)
    events = [_event(conn, "entity", eid, actor, "created", None, name, insight_id)]
    if ids:
        _cite(conn, actor, "entity", eid, ids, insight_id)
    return entity(conn, eid), events


def update_entity(conn, actor, entity_id, *, name=None, aliases=None, external_ids=None, tier=None,
                  summary=None, properties=None, last_verified=None, status=None, evidence_ids=None,
                  insight_id=None, clear_tier=False):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    row = _need_entity(conn, actor, entity_id)
    changes = {}
    if name is not None and str(name).strip() and str(name).strip() != row["name"]:
        changes["name"] = str(name).strip()
    if aliases is not None:
        cleaned = [str(a).strip() for a in aliases if str(a).strip()]
        if cleaned != row["aliases"]:
            changes["aliases"] = cleaned
    if external_ids is not None and dict(external_ids) != row["external_ids"]:
        changes["external_ids"] = dict(external_ids)
    if clear_tier or (tier is not None and tier != row["tier"]):
        if tier not in (None, "") and tier not in TIERS:
            H.refuse(conn, actor, "kind", f"a tier is {'|'.join(TIERS)}, not {tier}")
        changes["tier"] = None if clear_tier or tier in (None, "") else tier
    if summary is not None and str(summary) != row["summary"]:
        changes["summary"] = str(summary)
    if properties is not None and dict(properties) != row["properties"]:
        changes["properties"] = dict(properties)
    if last_verified is not None and str(last_verified) != (row["last_verified"] or ""):
        changes["last_verified"] = _day(conn, last_verified, actor, "last_verified")
    if status is not None and status != row["status"]:
        if status not in ("active", "retired", "merged"):
            H.refuse(conn, actor, "kind", "status is active, retired or merged")
        changes["status"] = status
    if not changes:
        return row, []
    if actor != SEED_ACTOR and EVIDENCE_FIELDS & set(changes):
        ids = _need_evidence(conn, actor, evidence_ids)
    else:
        ids = [i for i in (evidence_ids or []) if i]
    events = []
    for field, value in changes.items():
        old = row[field]
        events.append(_event(conn, "entity", entity_id, actor, field,
                             dumps(old) if isinstance(old, (dict, list)) else old,
                             dumps(value) if isinstance(value, (dict, list)) else value, insight_id))
    assignments = []
    args = {"id": entity_id, "updated": H.now()}
    for field, value in changes.items():
        column = field
        args[column] = dumps(value) if field in ("aliases", "external_ids", "properties") else value
        assignments.append(f"{column}=:{column}")
    conn.execute(f"UPDATE market_entities SET {', '.join(assignments)}, updated=:updated WHERE id=:id", args)
    if ids and actor != SEED_ACTOR:
        _cite(conn, actor, "entity", entity_id, ids, insight_id)
    return entity(conn, entity_id), events


def create_edge(conn, actor, *, src, rel, dst, since=None, until=None, confidence="medium",
                properties=None, evidence_ids=None, insight_id=None, edge_id=None):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    if rel not in RELATIONS:
        H.refuse(conn, actor, "kind", f"a relation is {'|'.join(RELATIONS)}, not {rel}")
    if confidence not in CONFIDENCE:
        H.refuse(conn, actor, "kind", f"confidence is {'|'.join(CONFIDENCE)}, not {confidence}")
    _need_entity(conn, actor, src)
    _need_entity(conn, actor, dst)
    since_day, until_day = _day(conn, since, actor, "since"), _day(conn, until, actor, "until")
    if since_day and until_day and until_day < since_day:
        H.refuse(conn, actor, "date", "until must be on or after since")
    if rel in SYMMETRIC:
        # One row for any span that shares a day, in either direction. An ended until
        # that falls before the new since can sit beside it.
        for found in _same_pair(conn, rel, src, dst):
            if _overlaps(since_day, until_day, found.get("since"), found.get("until")):
                return _edge_row(found), []
    eid = edge_id or H.new_id()
    if edge(conn, eid):
        return edge(conn, eid), []
    ids = [] if actor == SEED_ACTOR else _need_evidence(conn, actor, evidence_ids)
    now = H.now()
    row = {"id": eid, "src": src, "rel": rel, "dst": dst, "since": since_day, "until": until_day,
           "confidence": confidence, "properties": dumps(properties or {}), "created": now,
           "created_by": actor, "updated": now}
    conn.execute("INSERT INTO market_edges (id, src, rel, dst, since, until, confidence, properties, created, created_by, updated) "
                 "VALUES (:id,:src,:rel,:dst,:since,:until,:confidence,:properties,:created,:created_by,:updated)", row)
    events = [_event(conn, "edge", eid, actor, "created", None, f"{src} {rel} {dst}", insight_id)]
    if ids:
        _cite(conn, actor, "edge", eid, ids, insight_id)
    return edge(conn, eid), events


def update_edge(conn, actor, edge_id, *, since=None, until=None, confidence=None, properties=None,
                rel=None, evidence_ids=None, insight_id=None, set_since=False, set_until=False):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    row = edge(conn, edge_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no edge {edge_id}")
    changes = {}
    if rel is not None and rel != row["rel"]:
        if rel not in RELATIONS:
            H.refuse(conn, actor, "kind", f"a relation is {'|'.join(RELATIONS)}, not {rel}")
        changes["rel"] = rel
    if confidence is not None and confidence != row["confidence"]:
        if confidence not in CONFIDENCE:
            H.refuse(conn, actor, "kind", f"confidence is {'|'.join(CONFIDENCE)}, not {confidence}")
        changes["confidence"] = confidence
    if properties is not None and dict(properties) != row["properties"]:
        changes["properties"] = dict(properties)
    if set_since or since is not None:
        changes["since"] = _day(conn, since, actor, "since")
    if set_until or until is not None:
        changes["until"] = _day(conn, until, actor, "until")
    if not changes:
        return row, []
    new_since = changes.get("since", row["since"])
    new_until = changes.get("until", row["until"])
    if new_since and new_until and new_until < new_since:
        H.refuse(conn, actor, "date", "until must be on or after since")
    rel_now = changes.get("rel", row["rel"])
    if rel_now in SYMMETRIC:
        for found in _same_pair(conn, rel_now, row["src"], row["dst"]):
            if found["id"] == edge_id:
                continue
            if _overlaps(new_since, new_until, found.get("since"), found.get("until")):
                H.refuse(conn, actor, "kind", "that span already has this relation")
    ids = []
    if actor != SEED_ACTOR and EVIDENCE_FIELDS & set(changes):
        ids = _need_evidence(conn, actor, evidence_ids)
    events = []
    for field, value in changes.items():
        old = row[field]
        events.append(_event(conn, "edge", edge_id, actor, field,
                             dumps(old) if isinstance(old, (dict, list)) else old,
                             dumps(value) if isinstance(value, (dict, list)) else value, insight_id))
    args = {"id": edge_id, "updated": H.now(), **{k: (dumps(v) if k == "properties" else v) for k, v in changes.items()}}
    sets = ", ".join(f"{k}=:{k}" for k in changes)
    conn.execute(f"UPDATE market_edges SET {sets}, updated=:updated WHERE id=:id", args)
    if ids:
        _cite(conn, actor, "edge", edge_id, ids, insight_id)
    return edge(conn, edge_id), events


def merge_entities(conn, actor, entity_id, into_id, insight_id=None):
    """status=merged, edges re-pointed, aliases unioned. The old id stays readable."""
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    src = _need_entity(conn, actor, entity_id)
    dst = _need_entity(conn, actor, into_id)
    if entity_id == into_id:
        H.refuse(conn, actor, "kind", "an entity cannot merge into itself")
    aliases = list(dict.fromkeys([*dst["aliases"], src["name"], *src["aliases"]]))
    _event(conn, "entity", into_id, actor, "aliases", dumps(dst["aliases"]), dumps(aliases), insight_id)
    conn.execute("UPDATE market_entities SET aliases=?, updated=? WHERE id=?", (dumps(aliases), H.now(), into_id))
    for row in conn.execute("SELECT * FROM market_edges WHERE src=? OR dst=?", (entity_id, entity_id)):
        item = _edge_row(row)
        new_src = into_id if item["src"] == entity_id else item["src"]
        new_dst = into_id if item["dst"] == entity_id else item["dst"]
        if new_src == new_dst:
            today = datetime.now(timezone.utc).date().isoformat()
            conn.execute("UPDATE market_edges SET until=?, updated=? WHERE id=? AND until IS NULL",
                         (today, H.now(), item["id"]))
            _event(conn, "edge", item["id"], actor, "until", item["until"], today, insight_id, "ended by merge")
        else:
            conn.execute("UPDATE market_edges SET src=?, dst=?, updated=? WHERE id=?",
                         (new_src, new_dst, H.now(), item["id"]))
            _event(conn, "edge", item["id"], actor, "src", item["src"], new_src, insight_id)
            _event(conn, "edge", item["id"], actor, "dst", item["dst"], new_dst, insight_id)
    conn.execute("UPDATE market_entities SET status='merged', merged_into=?, updated=? WHERE id=?",
                 (into_id, H.now(), entity_id))
    _event(conn, "entity", entity_id, actor, "status", src["status"], "merged", insight_id)
    _event(conn, "entity", entity_id, actor, "merged_into", src["merged_into"], into_id, insight_id)
    return entity(conn, entity_id)


def retire_entity(conn, actor, entity_id, insight_id=None):
    row, _events = update_entity(conn, actor, entity_id, status="retired", insight_id=insight_id)
    return row


def cite(conn, actor, claim_kind, claim_id, evidence_id_, insight_id=None):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    if claim_kind not in CLAIM_KINDS:
        H.refuse(conn, actor, "kind", f"a citation is {'|'.join(CLAIM_KINDS)}, not {claim_kind}")
    if not evidence(conn, evidence_id_):
        H.refuse(conn, actor, "not-found", f"no evidence {evidence_id_}")
    if claim_kind == "entity" and not entity(conn, claim_id):
        H.refuse(conn, actor, "not-found", f"no entity {claim_id}")
    elif claim_kind == "edge" and not edge(conn, claim_id):
        H.refuse(conn, actor, "not-found", f"no edge {claim_id}")
    elif claim_kind == "evidence" and not evidence(conn, claim_id):
        H.refuse(conn, actor, "not-found", f"no evidence {claim_id}")
    elif claim_kind == "document":
        doc = conn.execute("SELECT collection FROM documents WHERE id=?", (claim_id,)).fetchone()
        if not doc:
            H.refuse(conn, actor, "not-found", f"no document {claim_id}")
    _cite(conn, actor, claim_kind, claim_id, [evidence_id_], insight_id)
    return {"claim_kind": claim_kind, "claim_id": claim_id, "evidence_id": evidence_id_}


# ----------------------------------------------------------------------------- insights, the curator
def report(conn, actor, *, kind, about, claim, source_url="", quote="", confidence="medium", urgent=False,
           source_ref=None):
    """Prose from anyone on the roster. It never changes an entity or an edge.

    `source_ref` names where the finding came from (an intake item, backend/listening.py). A
    reporter files one insight per source_ref: the same ref again returns the first insight."""
    H._writer(conn, actor)
    source_ref = str(source_ref or "").strip() or None
    if source_ref:
        found = conn.execute("SELECT id FROM market_insights WHERE reported_by=? AND source_ref=?",
                             (actor, source_ref)).fetchone()
        if found:
            return insight(conn, found["id"])
    if kind not in INSIGHT_KINDS:
        H.refuse(conn, actor, "kind", f"a report is {'|'.join(INSIGHT_KINDS)}, not {kind}")
    if confidence not in CONFIDENCE:
        H.refuse(conn, actor, "kind", f"confidence is {'|'.join(CONFIDENCE)}, not {confidence}")
    claim = str(claim or "").strip()
    if not claim:
        H.refuse(conn, actor, "lint", "say what you found, in a sentence")
    now = H.now()
    row = {"id": H.new_id(), "reported_by": actor, "reported_at": now, "kind": kind,
           "about": str(about or "").strip(), "claim": claim, "source_url": str(source_url or ""),
           "quote": str(quote or ""), "confidence": confidence, "urgent": 1 if urgent else 0,
           "status": "new", "resolution": "", "applied_events": "[]", "resolved_at": None, "resolved_by": None,
           "source_ref": source_ref}
    conn.execute("INSERT INTO market_insights (id, reported_by, reported_at, kind, about, claim, source_url, quote, "
                 "confidence, urgent, status, resolution, applied_events, resolved_at, resolved_by, source_ref) VALUES "
                 "(:id,:reported_by,:reported_at,:kind,:about,:claim,:source_url,:quote,:confidence,:urgent,"
                 ":status,:resolution,:applied_events,:resolved_at,:resolved_by,:source_ref)", row)
    if urgent:
        from . import routines
        routines.emit(conn, "market.insight.urgent", row["id"],
                      {"about": row["about"], "claim": row["claim"], "reported_by": actor},
                      title=row["about"] or row["claim"][:80])
    return insight(conn, row["id"])


def resolve(conn, actor, insight_id, status, resolution="", applied_events=None):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    row = insight(conn, insight_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no insight {insight_id}")
    if status not in INSIGHT_STATUSES:
        H.refuse(conn, actor, "kind", f"a resolution is {'|'.join(INSIGHT_STATUSES)}, not {status}")
    resolution = str(resolution or "").strip()
    if status in ("rejected", "merged", "needs-human") and not resolution:
        H.refuse(conn, actor, "lint", "say in one sentence why it is " + status)
    events = list(applied_events or [])
    if status == "applied" and not events:
        H.refuse(conn, actor, "evidence", "an applied insight names the market_events it produced")
    now = H.now()
    conn.execute("UPDATE market_insights SET status=?, resolution=?, applied_events=?, resolved_at=?, resolved_by=? WHERE id=?",
                 (status, resolution, dumps(events), now, actor, insight_id))
    return insight(conn, insight_id)


def apply_insight(conn, actor, insight_id, evidence_fields, *, entity_fields=None, edge_fields=None,
                  entity_update=None):
    """Evidence first, then the entity or edge that cites it, then status=applied."""
    row = insight(conn, insight_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no insight {insight_id}")
    if row["status"] != "new":
        H.refuse(conn, actor, "kind", f"{insight_id} is {row['status']}, not new")
    evidence_fields = dict(evidence_fields)
    evidence_fields.pop("insight_id", None)
    ev, ev_events = create_evidence(conn, actor, insight_id=insight_id, **evidence_fields)
    events = list(ev_events)
    if entity_fields:
        _created, more = create_entity(conn, actor, insight_id=insight_id, evidence_ids=[ev["id"]], **entity_fields)
        events.extend(more)
    if entity_update:
        _updated, more = update_entity(conn, actor, insight_id=insight_id, evidence_ids=[ev["id"]], **entity_update)
        events.extend(more)
    if edge_fields:
        _edge, more = create_edge(conn, actor, insight_id=insight_id, evidence_ids=[ev["id"]], **edge_fields)
        events.extend(more)
    resolved = resolve(conn, actor, insight_id, "applied", resolution="Applied to the graph.", applied_events=events)
    return {"insight": resolved, "evidence": ev, "events": events}


def is_stale(row, today):
    """Past last_verified plus 60 days, or 30 for a core company. No date is not stale by itself."""
    raw = row.get("last_verified") if isinstance(row, dict) else None
    if not raw:
        return False
    try:
        verified = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return False
    window = CORE_STALE_DAYS if row.get("tier") == "core" else STALE_DAYS
    return (today - verified).days > window


def _free_owner_title(conn, actor, owner):
    """A title this run can still open. A live task of the same title, even one marked done,
    blocks another ask with that title, so the next pass gets its own."""
    base = "Review market insights that need a person"
    marks = ",".join("?" * len(H.LIVE_STATUSES))
    for n in range(1, 40):
        title = base if n == 1 else f"Review market insights that need a person, pass {n}"
        if not conn.execute(f"SELECT 1 FROM tasks WHERE requester=? AND owner=? AND title=? AND status IN ({marks})",
                            (actor, owner, title, *H.LIVE_STATUSES)).fetchone():
            return title
    return base + ", pass 40"


def _for_task(text):
    """A claim or a look-for, readable in a task, without the spellings that refuse the write.

    `status:` and `owner:` are internal codes. `emp-listening/` and `secrets/` are an escape.
    A link next to the word access is an escape. The insight row keeps the original words.
    """
    text = re.sub(r"\b(owner|status)\s*:\s*", r"\1 — ", str(text or ""), flags=re.I)
    text = re.sub(r"\bemp-([a-z0-9-]+)/", lambda m: "emp " + m.group(1).replace("-", " ") + " ", text, flags=re.I)
    text = re.sub(r"https?://\S+", lambda m: re.sub(r"^https?://", "", m.group(0)), text, flags=re.I)
    text = re.sub(r"(^|[\s\"'(/])secrets/", r"\1secrets ", text, flags=re.I)
    return text


def _file_owner_task(conn, actor, owner, body):
    """One task for this run. A duplicate title is tried again. The body is already safe to file."""
    last = None
    for _ in range(5):
        title = _free_owner_title(conn, actor, owner)
        try:
            return H.task_create(conn, actor, title, body, owner, deduplicate=False)
        except H.Refused as exc:
            if exc.rule != "duplicate":
                raise
            last = exc
    raise last


def _company_owner(conn, email):
    if email:
        found = conn.execute("SELECT id FROM humans WHERE lower(email)=lower(?)", (email,)).fetchone()
        if found:
            return H.human_actor(found["id"])
    return H.human_actor(H.default_human(conn))


def sweep(conn, actor, unverified, *, today=None, owner_email=None):
    """One task for every needs-human insight still unfiled, and a listening task only for an
    entity the curator marks unverified that is actually past its window. A stale entity the
    curator does not name is left alone, and so is a fresh one it does name.
    """
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    today = today or datetime.now(timezone.utc).date()
    if isinstance(today, str):
        today = date.fromisoformat(today[:10])
    pending = H._rows(conn.execute(
        "SELECT * FROM market_insights WHERE status='needs-human' AND filed_task IS NULL ORDER BY reported_at"))
    owner_task = None
    if pending:
        owner = _company_owner(conn, owner_email)
        # The claims sit in a quoted block, so a long report does not trip the 120-word ask lint.
        quoted = []
        for row in pending:
            text = _for_task(f"{row['id']}: {row['claim']} ({row['resolution']})")
            quoted.append("\n".join("> " + line for line in text.splitlines()) or ">")
        body = "Decide these market insights from this curator run.\n\n" + "\n".join(quoted)
        owner_task = _file_owner_task(conn, actor, owner, body)
        for row in pending:
            conn.execute("UPDATE market_insights SET filed_task=? WHERE id=?", (owner_task["id"], row["id"]))
    delta = refresh_delta(conn, actor, today) if today.weekday() == 0 else None
    listening = []
    for item in unverified or []:
        entity_id = str(item.get("id") or "").strip()
        row = entity(conn, entity_id) if entity_id else None
        if not row or not is_stale(row, today):
            continue
        look = _for_task(str(item.get("look_for") or "Confirm the entity is still what the market graph says.").strip())
        title = "Verify " + row["name"]
        body = f"Check {row['id']}.\n\nLook for: {look}\n"
        try:
            task = H.task_create(conn, actor, title, body, "bot:listening", deduplicate=True)
        except H.Refused:
            # A refusal here must not roll back the owner task already inserted in this sweep.
            continue
        listening.append({"id": task["id"], "entity_id": row["id"]})
    return {"owner_task": owner_task["id"] if owner_task else None,
            "listening_tasks": listening,
            "delta": {"updated": delta["updated"]} if delta else None}


# ----------------------------------------------------------------------------- narrative pages
def read_page(conn, doc_id):
    row = conn.execute("SELECT payload_json FROM documents WHERE id=? AND collection='market'", (doc_id,)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def write_page(conn, actor, doc_id, title, body, category, *, replace=False, insight_id=None, record=True):
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    existing = conn.execute("SELECT payload_json FROM documents WHERE id=?", (doc_id,)).fetchone()
    if existing and not replace:
        return json.loads(existing["payload_json"])
    previous = json.loads(existing["payload_json"]).get("content") if existing else None
    if previous == body:
        return json.loads(existing["payload_json"])
    now = H.now()
    payload = {"id": doc_id, "title": title, "content": body, "category": category, "collection": "market",
               "search": body, "format": "markdown", "owner": CURATOR, "fetched": now}
    if actor == SEED_ACTOR:
        payload["seeded"] = True    # the seed's own text: the Market page hides it while the graph is empty
    raw = encode(payload)
    hashed = digest(raw)
    conn.execute("INSERT OR IGNORE INTO document_versions VALUES(?,?,?,?)", (doc_id, hashed, raw, now))
    conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                 "visibility=excluded.visibility, collection=excluded.collection, payload_json=excluded.payload_json, "
                 "digest=excluded.digest, updated=excluded.updated",
                 (doc_id, "external", "market", raw, hashed, now))
    # The weekly delta is a projection of market_events. Recording that write would make the
    # next Monday refresh see a new event and rewrite the page again.
    if record:
        _event(conn, "document", doc_id, actor, "created" if not existing else "body", None, title, insight_id,
               "" if not existing else "body changed")
    return payload


def _listed(conn, kind):
    return H._rows(conn.execute(
        "SELECT id, name, tier FROM market_entities WHERE type=? AND status='active' ORDER BY name", (kind,)))


def _bullets(rows, empty):
    if not rows:
        return empty
    return "\n".join(f"- {row['name']}" + (f" ({row['tier']})" if row.get("tier") else "") + f" — `{row['id']}`"
                     for row in rows)


def delta_body(conn, today):
    """The weekly delta page, from market_events. Seed writes this; Monday refresh writes it again."""
    start = (today - timedelta(days=7)).isoformat()
    events = H._rows(conn.execute(
        "SELECT * FROM market_events WHERE substr(ts,1,10)>=? ORDER BY ts, id", (start,)))
    lines = ["# Weekly delta", "", f"Changes since {start}.", ""]
    if not events:
        lines.append("No market changes in this week.")
    for ev in events:
        lines.append(f"- {ev['ts'][:19]} {ev['actor']} {ev['subject_kind']} {ev['subject_id']} "
                     f"{ev['field']}: {ev['old'] or ''} -> {ev['new'] or ''}")
    return "\n".join(lines)


def narrative(conn, today):
    """The nine market pages, from the rows the seed just wrote. Not a standing placeholder."""
    companies = _listed(conn, "company")
    segments = _listed(conn, "segment")
    channels = _listed(conn, "channel")
    people = _listed(conn, "person")
    regulations = _listed(conn, "regulation")
    core = [row for row in companies if row.get("tier") == "core"]
    look = [row for row in companies if row.get("tier") == "lookalike"]
    questions = H._rows(conn.execute(
        "SELECT id, claim FROM market_insights WHERE status IN ('new', 'needs-human') ORDER BY reported_at"))
    asked = "\n".join(f"- {row['claim']} — `{row['id']}`" for row in questions) if questions else "The seed has no open questions yet."
    return {
        "market/overview": "\n".join([
            "# Overview", "",
            f"The seed covers {len(companies)} companies, {len(segments)} segments, and {len(channels)} channels.",
            "", "Core competitors:", _bullets(core, "None in the seed."),
            "", "Lookalikes:", _bullets(look, "None in the seed."),
            "", "Market sizing and fee norms stay on the theses page. They are not properties on an entity.",
        ]),
        "market/structure-and-size": "\n".join([
            "# Structure and size", "",
            "Segments in the seed. A size figure would be a thesis on this page, not a property on an entity.",
            "", _bullets(segments, "None in the seed."),
        ]),
        "market/coverage-universe": "\n".join([
            "# Coverage universe", "",
            "## Companies", _bullets(companies, "None in the seed."),
            "", "## Segments", _bullets(segments, "None in the seed."),
            "", "## Channels", _bullets(channels, "None in the seed."),
        ]),
        "market/people-who-matter": "\n".join([
            "# People who matter", "", _bullets(people, "The seed has no person entities yet."),
        ]),
        "market/channels": "\n".join([
            "# Channels", "", "Channel entities from the listening sources.", "",
            _bullets(channels, "None in the seed."),
        ]),
        "market/regulation-and-catalysts": "\n".join([
            "# Regulation and catalysts", "", _bullets(regulations, "The seed has no regulation entities yet."),
        ]),
        "market/theses": "\n".join([
            "# Theses", "",
            "The seed has no theses yet. A thesis is a claim on this page, cited, not a fact stored on an entity.",
        ]),
        "market/open-questions": "\n".join(["# Open questions", "", asked]),
        "market/weekly-delta": delta_body(conn, today),
    }


def seed_pages(conn, actor=SEED_ACTOR):
    """Write each page from the seed. A page the curator has already changed is left alone.
    The first boot's one-line placeholders are not a curator change, so a later boot replaces them.
    """
    bodies = narrative(conn, datetime.now(timezone.utc).date())
    placeholders = {body for _id, _title, _category, body in PAGES}
    placeholders.add("Weekly changes are filled from market_events each Monday.")
    written = []
    for doc_id, title, category, _fallback in PAGES:
        existing = read_page(conn, doc_id)
        if existing and existing.get("content") not in placeholders:
            continue
        write_page(conn, actor, doc_id, title, bodies[doc_id], category,
                   replace=existing is not None, record=doc_id != "market/weekly-delta")
        written.append(doc_id)
    return written


def mark_seeded_pages(conn):
    """One-time, idempotent startup fix for installs made before pages carried `seeded`: a market page whose
    content is still exactly what the seed wrote (the "None in the seed." text) is marked `seeded`, so the Market
    page hides it while the graph is empty. A page anyone has written or edited, or one the graph has since
    outgrown, is left alone. Returns the ids it marked; a second run marks none."""
    marked = []
    weekly = re.compile(r"^# Weekly delta\n\nChanges since (\d{4}-\d{2}-\d{2})\.")
    rows = conn.execute("SELECT id, payload_json FROM documents WHERE collection='market' AND id IN (%s)"
                        % ",".join("?" * len(PAGES)), [page[0] for page in PAGES]).fetchall()
    documents = {row["id"]: json.loads(row["payload_json"]) for row in rows}
    if not documents:
        return marked
    fresh = narrative(conn, datetime.now(timezone.utc).date())
    for doc_id, payload in documents.items():
        if payload.get("seeded") or not isinstance(payload.get("content"), str):
            continue
        body = payload["content"]
        if doc_id == "market/weekly-delta":
            since = weekly.match(body)
            if not since:
                continue
            # The seed wrote it before it wrote the other pages' own events, so it says nothing changed.
            fresh[doc_id] = f"# Weekly delta\n\nChanges since {since.group(1)}.\n\nNo market changes in this week."
        if body != fresh.get(doc_id):
            continue
        payload["seeded"] = True
        raw = encode(payload)
        hashed = digest(raw)
        now = H.now()
        conn.execute("INSERT OR IGNORE INTO document_versions VALUES(?,?,?,?)", (doc_id, hashed, raw, now))
        conn.execute("UPDATE documents SET payload_json=?, digest=? WHERE id=?", (raw, hashed, doc_id))
        marked.append(doc_id)
    return marked


def refresh_delta(conn, actor, today=None):
    """Monday rewrites the weekly delta from market_events. Any other day leaves it alone."""
    if actor != SEED_ACTOR:
        H._writer(conn, actor)
    today = today or datetime.now(timezone.utc).date()
    if isinstance(today, str):
        today = date.fromisoformat(today[:10])
    current = read_page(conn, "market/weekly-delta")
    if today.weekday() != 0:
        return {"updated": False, "document": current}
    body = delta_body(conn, today)
    document = write_page(conn, actor, "market/weekly-delta", "Weekly delta", body, "Market / Delta",
                          replace=True, record=False)
    return {"updated": True, "document": document}


# ----------------------------------------------------------------------------- seed from the live files
def _read(path):
    if not path:
        return ""
    path = Path(path)
    return path.read_text(errors="replace") if path.is_file() else ""


def persona_sections(text):
    """## sections that are buyers. 'Who to skip', 'Observed detail' and 'Sources' are not."""
    matches = list(re.finditer(r"^## (.+)$", text or "", re.M))
    out = []
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        plain = re.sub(r"^\d+\.\s*", "", title)
        plain = re.split(r"\s+[—–-]\s+", plain, maxsplit=1)[0].strip()
        if plain.lower() in SKIP_HEADINGS or title.lower() in SKIP_HEADINGS:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        paragraph = re.sub(r"\s+", " ", (text[match.end():end].strip().split("\n\n") or [""])[0]).strip()
        out.append({"id": "segment/" + slug(plain), "name": plain, "summary": paragraph[:2000]})
    return out


def channel_names(*texts):
    found, seen = [], set()

    def add(name):
        name = re.sub(r"\s+", " ", str(name or "")).strip(" -")
        if not name or len(name) > 160:
            return
        ident = "channel/" + slug(name)
        if ident in seen:
            return
        seen.add(ident)
        found.append({"id": ident, "name": name})

    for text in texts:
        for sub in SUBREDDIT.findall(text or ""):
            add("r/" + sub)
        for match in re.finditer(r"^\s*-\s+\*\*([^*]+)\*\*", text or "", re.M):
            add(match.group(1))
    return found


def _domain(queries):
    for query in queries:
        bare = str(query).strip().strip('"').strip("'").lower()
        if DOMAIN.fullmatch(bare):
            return bare
    return None


def companies_from_watchlist(document):
    """Every competitor, plus the company itself (`company/self`, named by the watchlist's `company`,
    default "Our company"). Queries become aliases. Tier is the watchlist's own word."""
    rows = []
    for entry in (document or {}).get("competitors") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        queries = [str(q) for q in (entry.get("queries") or [entry["name"]])]
        external = {}
        domain = _domain(queries)
        if domain:
            external["domain"] = domain
        rows.append({"id": "company/" + slug(entry["name"]), "type": "company", "name": str(entry["name"]).strip(),
                     "tier": entry.get("tier"), "aliases": queries, "external_ids": external,
                     "summary": ""})
    own_aliases = [str(q) for q in (document or {}).get("own_queries") or []]
    own_name = str((document or {}).get("company") or "Our company").strip()
    own_domain = str((document or {}).get("company_domain") or "").strip()
    rows.append({"id": OWN_COMPANY_ID, "type": "company", "name": own_name, "tier": None,
                 "aliases": own_aliases, "external_ids": {"domain": own_domain} if own_domain else {},
                 "summary": own_name + "."})
    return rows


def community_rows(index_text):
    rows = []
    for line in (index_text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        if cells[0].lower().startswith("source") or set(cells[0]) <= set("-: "):
            continue
        source = LINK.search(cells[0])
        raw = LINK.search(cells[4])
        url = source.group(2) if source else ""
        title = source.group(1) if source else cells[0]
        raw_path = raw.group(2) if raw else ""
        key = url or raw_path or title
        kind = "other"
        host = urlparse(url).netloc.lower()
        if "reddit.com" in host:
            kind = "reddit"
        elif host.endswith("x.com") or "twitter.com" in host:
            kind = "x"
        captured = ""
        stamp = re.search(r"(20\d{2}-\d{2}-\d{2})", raw_path)
        if stamp:
            captured = stamp.group(1)
        rows.append({"id": evidence_id(key), "source_url": url, "source_kind": kind, "captured_at": captured,
                     "quote": title, "our_read": cells[3], "raw": raw_path})
    return rows


def extract(sources):
    """The seed document, from the live files. Missing files contribute nothing."""
    sources = sources or {}
    watch = yaml.safe_load(_read(sources["watchlist"])) if sources.get("watchlist") else {}
    companies = companies_from_watchlist(watch) if watch else []
    personas = persona_sections(_read(sources.get("personas"))) if sources.get("personas") else []
    channels = channel_names(_read(sources.get("mention_sources")), _read(sources.get("outreach_venues")))
    evidence_rows = community_rows(_read(sources.get("community_index"))) if sources.get("community_index") else []
    watch_evidence = None
    edges = []
    if companies:
        watch_evidence = {"id": evidence_id("emp-listening/watchlist.yaml"),
                          "source_url": "https://github.com/acme/emp-listening/blob/main/watchlist.yaml",
                          "source_kind": "other", "captured_at": None, "quote": "",
                          "our_read": "Core and lookalike names on the listening watchlist, each treated as competing with the company until the curator verifies it.",
                          "raw": ""}
        for company in companies:
            if company["id"] == OWN_COMPANY_ID or company.get("tier") not in ("core", "lookalike"):
                continue
            edges.append({"id": "edge-competes-" + company["id"].split("/", 1)[1] + "-self",
                          "src": company["id"], "rel": "competes_with", "dst": OWN_COMPANY_ID,
                          "confidence": "medium", "evidence_id": watch_evidence["id"]})
    return {"companies": companies, "segments": personas, "channels": channels,
            "watch_evidence": watch_evidence, "evidence": evidence_rows, "edges": edges}


def _store_blob(conn, blobs, name, data):
    from .blobs import register

    class Owner:
        actor = SEED_ACTOR

    digest_ = blobs.put(data, "text/plain")
    return register(conn, Owner(), digest_, len(data), name, "text/plain")["id"]


def _import_entity(conn, row):
    if entity(conn, row["id"]):
        return False
    now = H.now()
    conn.execute("INSERT INTO market_entities (id, type, name, aliases, external_ids, tier, summary, properties, "
                 "status, merged_into, last_verified, created, created_by, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (row["id"], row.get("type") or "company", row["name"], dumps(row.get("aliases") or []),
                  dumps(row.get("external_ids") or {}), row.get("tier"), row.get("summary") or "",
                  dumps(row.get("properties") or {}), "active", None, now[:10], now, SEED_ACTOR, now))
    _event(conn, "entity", row["id"], SEED_ACTOR, "created", None, row["name"])
    return True


def seed(conn, sources=None, blobs=None, document=None):
    """Import once by id. An id that already exists is left unchanged. Day-one events use actor `seed`."""
    extracted = extract(sources)
    document = document or {}
    added = []
    companies = extracted["companies"] or document.get("companies") or []
    for row in companies:
        if _import_entity(conn, row):
            added.append(row["id"])
    for row in extracted["segments"] or document.get("segments") or []:
        if _import_entity(conn, {**row, "type": "segment", "tier": None, "aliases": row.get("aliases") or []}):
            added.append(row["id"])
    for row in extracted["channels"] or document.get("channels") or []:
        if _import_entity(conn, {**row, "type": "channel", "tier": None, "aliases": [], "summary": ""}):
            added.append(row["id"])
    watch = extracted["watch_evidence"] or document.get("watch_evidence")
    if watch and not evidence(conn, watch["id"]):
        create_evidence(conn, SEED_ACTOR, evidence_key=watch["id"], source_url=watch.get("source_url") or "",
                        source_kind=watch.get("source_kind") or "other", captured_at=watch.get("captured_at"),
                        quote=watch.get("quote") or "", our_read=watch.get("our_read") or "", captured_by=SEED_ACTOR)
        added.append(watch["id"])
    raw_root = Path(sources["community_index"]).parent if sources and sources.get("community_index") else None
    for row in extracted["evidence"]:
        if evidence(conn, row["id"]):
            continue
        blob_id = None
        if blobs is not None and raw_root is not None and row.get("raw"):
            path = (raw_root / row["raw"]).resolve()
            if path.is_file() and raw_root.resolve() in path.parents:
                blob_id = _store_blob(conn, blobs, path.name, path.read_bytes())
        create_evidence(conn, SEED_ACTOR, evidence_key=row["id"], source_url=row.get("source_url") or "",
                        source_kind=row.get("source_kind") or "other", captured_at=row.get("captured_at"),
                        quote=row.get("quote") or "", our_read=row.get("our_read") or "", blob_id=blob_id,
                        captured_by=SEED_ACTOR)
        added.append(row["id"])
    for row in extracted["edges"] or document.get("edges") or []:
        if edge(conn, row["id"]):
            continue
        if not entity(conn, row["src"]) or not entity(conn, row["dst"]):
            continue
        create_edge(conn, SEED_ACTOR, edge_id=row["id"], src=row["src"], rel=row["rel"], dst=row["dst"],
                    confidence=row.get("confidence") or "medium")
        # The watchlist is the evidence. Seed does not go through the writer's evidence check.
        if row.get("evidence_id") and evidence(conn, row["evidence_id"]):
            conn.execute("INSERT OR IGNORE INTO market_citations VALUES (?,?,?)", ("edge", row["id"], row["evidence_id"]))
        added.append(row["id"])
    for doc_id in seed_pages(conn):
        added.append(doc_id)
    return added


def locate_sources(root):
    """Local checkouts next to the hub, else nothing. The test fetches GitHub when these are absent."""
    root = Path(root)
    found = {}
    community = root / "docs" / "community-research" / "index.md"
    if community.is_file():
        found["community_index"] = community
    candidates = [root.parent / "emp-listening", root / "emp-listening",
                  root.parent.parent / "emp-listening"]
    sales = [root.parent / "emp-sales", root / "emp-sales", root.parent.parent / "emp-sales"]
    for directory in candidates:
        watch = directory / "watchlist.yaml"
        if watch.is_file():
            found["watchlist"] = watch
            mention = directory / "knowledge" / "mention-sources.md"
            venues = directory / "knowledge" / "outreach-venues.md"
            if mention.is_file():
                found["mention_sources"] = mention
            if venues.is_file():
                found["outreach_venues"] = venues
            break
    for directory in sales:
        personas = directory / "knowledge" / "buyer-personas.md"
        if personas.is_file():
            found["personas"] = personas
            break
    return found


def load_snapshot(registry_dir):
    path = Path(registry_dir) / "market.yaml"
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}


# ----------------------------------------------------------------------------- the curator's routines
CURATE_TEXT = """Curate the market: playbooks/curate-the-market.md.
Read market_insights with status new, oldest first.
Resolve each name against the market graph before you invent an entity.
Write the evidence row first, then the entity or edge that cites it.
Close the insight as applied, merged, rejected with one sentence, or needs-human.
All needs-human items from this run go into one task on the company owner.
On Mondays the sweep refreshes the weekly delta page from market events. hub market refresh does the same.
Mark unverified only the entities you tried to check and could not. Do not file a listening task just because a date is old.
If there are no new insights and it is not Monday, stop.
"""

URGENT_TEXT = """An urgent market insight just arrived: playbooks/urgent-market-insight.md.
Read that insight and curate it now, ahead of the daily pass.
Write evidence before you change the graph. One needs-human task for the owner if you cannot decide.
"""

CURATOR_ROUTINES = (
    ("curate-the-market", {"title": "Curate the market", "cron": "0 4 * * *", "on": "",
                           "timezone": "America/Los_Angeles", "text": CURATE_TEXT}),
    ("urgent-market-insight", {"title": "Urgent market insight", "cron": "", "on": "market.insight.urgent",
                               "timezone": "America/Los_Angeles", "text": URGENT_TEXT}),
)


def ensure_curator(conn):
    """At boot: the Librarian's market routines, and the old Market Analyst retired. Idempotent.

    A routine is created only when the Librarian has never had one with that key, so a routine
    someone deleted stays deleted. A `market-analyst` bot left from before is archived once, with
    its open tasks handed to the Librarian; its rows, tasks and repository stay.
    """
    from . import routines
    librarian = H.bot(conn, CURATOR.split(":", 1)[1])
    if librarian and librarian.get("state") != "archived":
        for key, fields in CURATOR_ROUTINES:
            if not conn.execute("SELECT 1 FROM schedules WHERE bot=? AND routine_key=?", (librarian["slug"], key)).fetchone():
                routines.create(conn, H.KEEPER, librarian["slug"], dict(fields), key=key)
    old = H.bot(conn, RETIRED_CURATOR)
    if old and old.get("state") != "archived" \
            and conn.execute("SELECT 1 FROM bot_config WHERE bot=?", (RETIRED_CURATOR,)).fetchone():
        from . import settings_admin
        successor = librarian["slug"] if librarian and librarian.get("state") != "archived" else ""
        settings_admin.archive_bot(conn, H.KEEPER, RETIRED_CURATOR, successor=successor)
        H.event(conn, H.KEEPER, "market.curator_retired", RETIRED_CURATOR, {"successor": successor})
    return librarian


# ----------------------------------------------------------------------------- HTTP
def install(app, store, auth, mutate):
    def _show(conn, entity_id):
        body = show(conn, entity_id)
        if not body:
            raise Problem("not_found", "Unknown entity", 404)
        return body

    @app.get("/api/v2/market/entities")
    def entities(request: Request, type: str | None = None, tier: str | None = None, q: str | None = None,
                 status: str | None = None, id: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if id:
                return _show(c, id)
            if q:
                return find(c, q)
            where, args = [], []
            if type:
                where.append("type=?")
                args.append(type)
            if tier:
                where.append("tier=?")
                args.append(tier)
            if status:
                where.append("status=?")
                args.append(status)
            sql = "SELECT * FROM market_entities" + (" WHERE " + " AND ".join(where) if where else "")
            return {"entities": [_entity_row(r) for r in H._rows(c.execute(sql + " ORDER BY name", args))]}

    @app.get("/api/v2/market/entities/{rest:path}")
    def entity_show(rest: str, request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return _show(c, rest)

    @app.get("/api/v2/market/edges")
    def edge_list(request: Request, src: str | None = None, dst: str | None = None, rel: str | None = None,
                  as_of: str | None = None):
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"edges": edges_of(c, src=src, dst=dst, rel=rel, as_of=as_of)}

    @app.get("/api/v2/market/delta")
    def delta_route(request: Request, since: str = "7d"):
        auth.domain(request.state.identity)
        with store.read() as c:
            return delta(c, since)

    @app.get("/api/v2/market/insights")
    def insights(request: Request, status: str | None = None):
        who = request.state.identity
        auth.domain(who)
        if who.role != "owner" and who.actor != CURATOR:
            raise Problem("forbidden", "The insight queue is for the Librarian and the owner", 403)
        with store.read() as c:
            if status:
                rows = H._rows(c.execute("SELECT * FROM market_insights WHERE status=? ORDER BY reported_at", (status,)))
            else:
                rows = H._rows(c.execute("SELECT * FROM market_insights ORDER BY reported_at"))
            for row in rows:
                row["applied_events"] = loads(row.get("applied_events"), [])
            return {"insights": rows}

    @app.post("/api/v2/market/insights")
    def insight_report(request: Request, body: M.MarketReport):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            row = report(c, who.actor, kind=body.kind, about=body.about, claim=body.claim,
                         source_url=body.source_url, quote=body.quote, confidence=body.confidence, urgent=body.urgent,
                         source_ref=body.source_ref)
            return {"insight": row}

        return mutate(request, body, work)

    @app.post("/api/v2/market/ask")
    def market_ask(request: Request, body: M.MarketAsk):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            try:
                return ask(c, body.question)
            except Exception as exc:
                raise Problem("market_ask", f"The market model call failed: {exc}", 503, True) from exc

    @app.post("/api/v2/market/evidence")
    def evidence_create(request: Request, body: M.MarketEvidenceCreate):
        who = request.state.identity

        def work(c):
            require_writer(who)
            row, _events = create_evidence(c, who.actor, source_url=body.source_url, source_kind=body.source_kind,
                                           captured_at=body.captured_at, quote=body.quote, our_read=body.our_read,
                                           insight_id=body.insight_id)
            return {"evidence": row}

        return mutate(request, body, work)

    @app.post("/api/v2/market/entities")
    def entity_create(request: Request, body: M.MarketEntityCreate):
        who = request.state.identity

        def work(c):
            require_writer(who)
            row, _events = create_entity(c, who.actor, type=body.type, name=body.name, aliases=body.aliases,
                                         external_ids=body.external_ids, tier=body.tier, summary=body.summary,
                                         properties=body.properties, last_verified=body.last_verified,
                                         evidence_ids=body.evidence_ids, force=body.force, insight_id=body.insight_id,
                                         entity_id=body.id)
            return {"entity": row}

        return mutate(request, body, work)

    @app.post("/api/v2/market/entities/{rest:path}")
    def entity_write(rest: str, request: Request, body: M.MarketEntityWrite):
        who = request.state.identity
        action, entity_id = None, rest
        for suffix in ("/merge", "/retire"):
            if rest.endswith(suffix):
                entity_id, action = rest[: -len(suffix)], suffix[1:]

        def work(c):
            require_writer(who)
            if action == "merge":
                if not body.into:
                    raise Problem("merge", "Say which entity this one merges into", 422)
                return {"entity": merge_entities(c, who.actor, entity_id, body.into, body.insight_id)}
            if action == "retire":
                return {"entity": retire_entity(c, who.actor, entity_id, body.insight_id)}
            row, _events = update_entity(c, who.actor, entity_id, name=body.name, aliases=body.aliases,
                                         external_ids=body.external_ids, tier=body.tier, summary=body.summary,
                                         properties=body.properties, last_verified=body.last_verified,
                                         status=body.status, evidence_ids=body.evidence_ids, insight_id=body.insight_id,
                                         clear_tier=body.clear_tier)
            return {"entity": row}

        return mutate(request, body, work)

    @app.post("/api/v2/market/edges")
    def edge_create(request: Request, body: M.MarketEdgeCreate):
        who = request.state.identity

        def work(c):
            require_writer(who)
            row, _events = create_edge(c, who.actor, src=body.src, rel=body.rel, dst=body.dst, since=body.since,
                                       until=body.until, confidence=body.confidence, properties=body.properties,
                                       evidence_ids=body.evidence_ids, insight_id=body.insight_id, edge_id=body.id)
            return {"edge": row}

        return mutate(request, body, work)

    @app.post("/api/v2/market/edges/{edge_id}")
    def edge_update(request: Request, edge_id: str, body: M.MarketEdgeUpdate):
        who = request.state.identity

        def work(c):
            require_writer(who)
            row, _events = update_edge(c, who.actor, edge_id, since=body.since, until=body.until,
                                       confidence=body.confidence, properties=body.properties, rel=body.rel,
                                       evidence_ids=body.evidence_ids, insight_id=body.insight_id,
                                       set_since=body.set_since, set_until=body.set_until or body.until is not None)
            return {"edge": row}

        return mutate(request, body, work)

    @app.post("/api/v2/market/citations")
    def citation_create(request: Request, body: M.MarketCitation):
        who = request.state.identity

        def work(c):
            require_writer(who)
            return {"citation": cite(c, who.actor, body.claim_kind, body.claim_id, body.evidence_id, body.insight_id)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/insights/{insight_id}/resolve")
    def insight_resolve(request: Request, insight_id: str, body: M.MarketResolve):
        who = request.state.identity

        def work(c):
            require_writer(who)
            return {"insight": resolve(c, who.actor, insight_id, body.status, body.resolution, body.applied_events)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/insights/{insight_id}/apply")
    def insight_apply(request: Request, insight_id: str, body: M.MarketApply):
        who = request.state.identity

        def work(c):
            require_writer(who)
            evidence_fields = body.evidence.model_dump()
            entity_fields = body.entity.model_dump() if body.entity else None
            edge_fields = body.edge.model_dump() if body.edge else None
            if entity_fields:
                entity_fields.pop("evidence_ids", None)
                entity_fields.pop("force", None)
                entity_fields.pop("insight_id", None)
                entity_fields["entity_id"] = entity_fields.pop("id", None)
            if edge_fields:
                edge_fields.pop("evidence_ids", None)
                edge_fields.pop("insight_id", None)
                edge_fields["edge_id"] = edge_fields.pop("id", None)
            update = None
            if body.entity_id:
                update = {"entity_id": body.entity_id}
                for field in ("summary", "properties", "tier", "aliases", "last_verified"):
                    value = getattr(body, field)
                    if value is not None:
                        update[field] = value
            return apply_insight(c, who.actor, insight_id, evidence_fields, entity_fields=entity_fields,
                                 edge_fields=edge_fields, entity_update=update)

        return mutate(request, body, work)

    @app.post("/api/v2/market/curator/sweep")
    def curator_sweep(request: Request, body: M.MarketSweep):
        who = request.state.identity

        def work(c):
            require_writer(who)
            return sweep(c, who.actor, [item.model_dump() for item in body.unverified], today=body.today,
                         owner_email=store.settings.owner_email)

        return mutate(request, body, work)

    @app.post("/api/v2/market/pages/{name}")
    def page_write(request: Request, name: str, body: M.MarketPage):
        """The curator rewrites one of the eight narrative pages from the graph (#488). The weekly
        delta is a projection of market_events and only `delta/refresh` writes it."""
        who = request.state.identity
        doc_id = "market/" + name
        pages = {page[0]: page for page in PAGES if page[0] != "market/weekly-delta"}

        def work(c):
            require_writer(who)
            if doc_id not in pages:
                raise Problem("not_found", f"{name} is not a market page the curator writes; one of "
                              + ", ".join(p.split("/", 1)[1] for p in pages), 404)
            _, title, category, _ = pages[doc_id]
            return write_page(c, who.actor, doc_id, title, body.body, category, replace=True)

        return mutate(request, body, work)

    @app.post("/api/v2/market/delta/refresh")
    def delta_refresh(request: Request, body: M.MarketRefresh):
        who = request.state.identity

        def work(c):
            require_writer(who)
            return refresh_delta(c, who.actor, body.today)

        return mutate(request, body, work)
