"""Next folds into Us: a segment the company meant to enter next (`next: true` on its properties) is one of the company's
segments now (backend/segments.py). It gets a current `operates_in` edge from company/self unless it has one, and loses
the mark. A segment that is already Us keeps its edge and loses the mark only.

A fresh fold lands last in the list (its `rank` follows the segments that were Us before, in their order; a Us segment
with no rank yet is ranked first, in its current order) and gets a color no other of the company's has (backend/
strategy_market.py). The folds are cited by one evidence row, "folded from Next", on each new edge, when the market
tables have it.

company/self is made from the onboarding company name when the graph has none yet (a Next segment can be marked before
the company is; the name is read only where registry_metadata exists). A file that is new has no market tables yet, so
there is nothing to fold."""

import json
import uuid
from datetime import datetime, timezone

OWN = "company/self"
COLORS = ("blue", "green", "amber", "red", "purple", "teal", "pink", "gray")
NOTE = "Folded from Next: a segment the company meant to enter next is one of its segments now."


def _company_name(conn, tables):
    row = None
    if "registry_metadata" in tables:
        row = conn.execute("SELECT value FROM registry_metadata WHERE key='onboarding'").fetchone()
    try:
        names = (json.loads(row[0]) if row else {}).get("names") or {}
    except (ValueError, TypeError, AttributeError):
        names = {}
    return str(names.get("company_name") or "").strip() or "Our company"


def _props(raw):
    try:
        props = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return None
    return props if isinstance(props, dict) else None


def _rank(props):
    rank = props.get("rank")
    return rank if isinstance(rank, int) and not isinstance(rank, bool) and rank >= 0 else None


def _color(stored, used, index):
    """The color a fold gets: its own when it is one of the colors and free, else the first free one."""
    if stored in COLORS and stored not in used:
        return stored
    free = [c for c in COLORS if c not in used]
    return free[0] if free else COLORS[index % len(COLORS)]


def _write(conn, seg_id, props, now):
    conn.execute(
        "UPDATE market_entities SET properties=?, updated_at=? WHERE id=?",
        (json.dumps(props, sort_keys=True, ensure_ascii=False), now, seg_id),
    )


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"market_entities", "market_edges"} <= tables:
        return
    marked = []
    for seg_id, _name, raw in conn.execute(
        "SELECT id, name, properties FROM market_entities WHERE type='segment' AND status='active' ORDER BY name, id"
    ).fetchall():
        props = _props(raw)
        if props and props.get("next"):
            marked.append((seg_id, props))
    if not marked:
        return
    # The same clock and day the market writes its rows by (backend/hubdb.py now).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    today = now[:10]
    if not conn.execute("SELECT 1 FROM market_entities WHERE id=?", (OWN,)).fetchone():
        name = _company_name(conn, tables)
        conn.execute(
            "INSERT INTO market_entities (id, type, name, aliases, external_ids, summary, properties, status, "
            "created_at, created_by, updated_at) VALUES (?, 'company', ?, '[]', '{}', ?, '{}', 'active', ?, "
            "'migration', ?)",
            (OWN, name, name + ".", now, now),
        )
    # The company's segments now, in list order: rank, then name, unranked last (backend/strategy_market.py).
    ours = []
    for seg_id, name, raw in conn.execute(
        "SELECT DISTINCT e.id, e.name, e.properties FROM market_entities e JOIN market_edges g ON g.dst=e.id "
        "WHERE e.type='segment' AND e.status='active' AND g.rel='operates_in' AND g.src=? "
        "AND (g.valid_from_on IS NULL OR substr(g.valid_from_on,1,10)<=?) "
        "AND (g.valid_until_on IS NULL OR substr(g.valid_until_on,1,10)>=?)",
        (OWN, today, today),
    ).fetchall():
        ours.append((seg_id, name, _props(raw) or {}))
    ours.sort(key=lambda row: (_rank(row[2]) if _rank(row[2]) is not None else 10**9, row[1], row[0]))
    touched: dict[str, dict] = {}
    if any(_rank(props) is None for _, _, props in ours):
        for index, (seg_id, _, props) in enumerate(ours):
            props["rank"] = index
            touched[seg_id] = props
    ours_ids = {seg_id for seg_id, _, _ in ours}
    used = {props.get("color") for _, _, props in ours if props.get("color") in COLORS}
    fresh = [(seg_id, props) for seg_id, props in marked if seg_id not in ours_ids]
    evidence = None
    if fresh and {"market_evidence", "market_citations"} <= tables:
        evidence = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO market_evidence (id, source_url, source_kind, captured_at, captured_by, quote, our_read, "
            "blob_id) VALUES (?, '', 'other', ?, 'migration', '', ?, NULL)",
            (evidence, now, NOTE),
        )
    for index, (seg_id, props) in enumerate(fresh):
        rank = len(ours) + index
        color = _color(props.get("color"), used, index)
        used.add(color)
        edge = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO market_edges (id, src, rel, dst, valid_from_on, valid_until_on, confidence, properties, "
            "created_at, created_by, updated_at) VALUES (?, ?, 'operates_in', ?, ?, NULL, 'medium', '{}', ?, "
            "'migration', ?)",
            (edge, OWN, seg_id, today, now, now),
        )
        if evidence:
            conn.execute(
                "INSERT INTO market_citations (claim_kind, claim_id, evidence_id) VALUES ('edge', ?, ?)",
                (edge, evidence),
            )
        touched[seg_id] = {**props, "rank": rank, "color": color}
    for seg_id, props in marked:
        if seg_id not in touched:  # a segment that is Us already: it loses the mark only
            touched[seg_id] = props
        touched[seg_id] = {key: value for key, value in touched[seg_id].items() if key != "next"}
    for seg_id, props in touched.items():
        _write(conn, seg_id, props, now)
