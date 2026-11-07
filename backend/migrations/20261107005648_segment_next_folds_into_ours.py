"""Next folds into Us: a segment the company meant to enter next (`next: true` on its properties) is one of the company's
segments now (backend/segments.py). It gets a current `operates_in` edge from company/self unless it has one, and loses
the mark. A segment that is already Us keeps its edge and loses the mark only.

company/self is made from the onboarding company name when the graph has none yet (a Next segment can be marked before
the company is). A file that is new has no market tables yet, so there is nothing to fold."""

import json
import uuid
from datetime import datetime, timezone

OWN = "company/self"


def _company_name(conn):
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='onboarding'").fetchone()
    try:
        names = (json.loads(row[0]) if row else {}).get("names") or {}
    except (ValueError, TypeError, AttributeError):
        names = {}
    return str(names.get("company_name") or "").strip() or "Our company"


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"market_entities", "market_edges"} <= tables:
        return
    marked = []
    for seg_id, raw in conn.execute(
        "SELECT id, properties FROM market_entities WHERE type='segment' AND status='active'"
    ).fetchall():
        try:
            props = json.loads(raw or "{}")
        except ValueError:
            continue
        if isinstance(props, dict) and props.get("next"):
            marked.append((seg_id, props))
    if not marked:
        return
    # The same clock and day the market writes its rows by (backend/hubdb.py now).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    today = now[:10]
    if not conn.execute("SELECT 1 FROM market_entities WHERE id=?", (OWN,)).fetchone():
        name = _company_name(conn)
        conn.execute(
            "INSERT INTO market_entities (id, type, name, aliases, external_ids, summary, properties, status, "
            "created_at, created_by, updated_at) VALUES (?, 'company', ?, '[]', '{}', ?, '{}', 'active', ?, "
            "'migration', ?)",
            (OWN, name, name + ".", now, now),
        )
    for seg_id, props in marked:
        held = conn.execute(
            "SELECT 1 FROM market_edges WHERE rel='operates_in' AND src=? AND dst=? "
            "AND (valid_from_on IS NULL OR substr(valid_from_on,1,10)<=?) "
            "AND (valid_until_on IS NULL OR substr(valid_until_on,1,10)>=?)",
            (OWN, seg_id, today, today),
        ).fetchone()
        if not held:
            conn.execute(
                "INSERT INTO market_edges (id, src, rel, dst, valid_from_on, valid_until_on, confidence, properties, "
                "created_at, created_by, updated_at) VALUES (?, ?, 'operates_in', ?, ?, NULL, 'medium', '{}', ?, "
                "'migration', ?)",
                (str(uuid.uuid4()), OWN, seg_id, today, now, now),
            )
        rest = {key: value for key, value in props.items() if key != "next"}
        conn.execute(
            "UPDATE market_entities SET properties=?, updated_at=? WHERE id=?",
            (json.dumps(rest, sort_keys=True, ensure_ascii=False), now, seg_id),
        )
