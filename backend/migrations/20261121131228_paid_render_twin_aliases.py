"""Each paid shot still unresolved from before the same-shot key (backend/paid_renders.py `_twin_key`), sent or of
unknown outcome on a card, gets that key as an alias, as `begin` writes it now: a fresh request for the same shot after
its card moved finds it by the events_action_target index instead of charging again. Only shots whose latest event is
sent or unknown, on a card. It reads only the events table, so it runs in any order, and running it again adds nothing.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

ACTIONS = (
    "paid_render.sent",
    "paid_render.started",
    "paid_render.refused",
    "paid_render.unknown",
    "paid_render.reconciled",
)
ALIAS = "paid_render.alias"


def twin_key(actor, task, digest, name):
    """paid_renders._twin_key, word for word: kept here so the migration never changes with the module."""
    return "twin_" + hashlib.sha256("\n".join((actor, task, digest, name)).encode()).hexdigest()[:32]


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    marks = ",".join("?" * len(ACTIONS))
    sent = conn.execute(
        "SELECT s.actor, s.target, s.detail FROM events s WHERE s.action='paid_render.sent' AND "
        f"(SELECT o.action FROM events o WHERE o.action IN ({marks}) AND o.target=s.target ORDER BY o.rowid DESC "
        "LIMIT 1) IN ('paid_render.sent', 'paid_render.unknown')",
        ACTIONS,
    ).fetchall()
    for actor, key, detail in sent:
        said = json.loads(detail or "{}")
        if not said.get("task"):
            continue
        twin = twin_key(str(actor), str(said["task"]), str(said.get("input") or ""), str(said.get("shot") or ""))
        kept = conn.execute(
            "SELECT 1 FROM events WHERE action=? AND target=? AND json_extract(detail, '$.key')=?", (ALIAS, twin, key)
        ).fetchone()
        if not kept:
            conn.execute(
                "INSERT INTO events (id, created_at, actor, action, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), now, actor, ALIAS, twin, json.dumps({"key": key})),
            )
