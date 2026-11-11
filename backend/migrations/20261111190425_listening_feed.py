"""Listening is sources, rules and rate limits on an automation (backend/listening.py).

- `listening_sources`: one row per source in a listening's trigger, kept in step with the flow on save. `key` is the
  flow's source id. `snapshot` is a Page source's last text, for the next comparison. A source taken out of the flow
  keeps its row (`deleted_at`), so the items it sent still name it.
- `listening_items`: what the sources saved, and what became of each (`new`, `duplicate`, `kept`, `dropped`, `sent`,
  `failed`, `expired`). `cost` is what scoring it cost, charged to the run that sends it.
- The old model goes, with its posts: `listen_runs`, `listen_items`, `listen_judgments`, `intake_items`,
  `listening_destinations` and the `listening.market_seeded` marker.
- An automation whose trigger is the old `listening` (a destination) is deleted with its versions and runs. A flow
  with a Find step reading `listening` loses that step and is turned off: it would no longer pass the check on save.
"""

import json

TABLES = (
    "CREATE TABLE IF NOT EXISTS listening_sources ("
    "id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, "
    "site TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'never', "
    "last_read_at TEXT, last_ok_at TEXT, last_error TEXT, token_hash TEXT, snapshot TEXT, deleted_at TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (automation_id, key))",
    "CREATE UNIQUE INDEX IF NOT EXISTS listening_sources_token ON listening_sources(token_hash) "
    "WHERE token_hash IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS listening_items ("
    "id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, source_id TEXT NOT NULL, native_id TEXT NOT NULL, "
    "url TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
    "text TEXT NOT NULL DEFAULT '', posted_at TEXT, text_key TEXT NOT NULL DEFAULT '', "
    "status TEXT NOT NULL DEFAULT 'new', tag TEXT NOT NULL DEFAULT '', score REAL, "
    "copies INTEGER NOT NULL DEFAULT 0, duplicate_of_id TEXT, tries INTEGER NOT NULL DEFAULT 0, claimed_at TEXT, "
    "scored_at TEXT, run_id TEXT, sent_at TEXT, cost REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, UNIQUE (automation_id, native_id))",
    "CREATE INDEX IF NOT EXISTS listening_items_status ON listening_items(automation_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS listening_items_text_key ON listening_items(automation_id, text_key)",
)
OLD_TABLES = ("intake_items", "listen_judgments", "listen_items", "listen_runs", "listening_destinations")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _without_find(node):
    """Take every Find step reading `listening` out of `node`, at any depth (a path's steps, `otherwise`); True when
    one was there."""
    found = False
    if isinstance(node, list):
        for child in node:
            found = _without_find(child) or found
    elif isinstance(node, dict):
        for key, child in node.items():
            if key == "steps" and isinstance(child, list):
                kept = [
                    s
                    for s in child
                    if not (isinstance(s, dict) and s.get("type") == "find" and s.get("source") == "listening")
                ]
                if len(kept) != len(child):
                    child[:] = kept
                    found = True
            found = _without_find(child) or found
    return found


def _old_automations(conn):
    """Delete each automation on the old trigger; take the listening Find out of the others and turn them off."""
    rows = conn.execute("SELECT id, trigger_type, flow FROM automations").fetchall()
    for aid, trigger_type, raw in rows:
        try:
            flow = json.loads(raw)
        except (TypeError, ValueError):
            continue
        trigger = flow.get("trigger") if isinstance(flow, dict) else None
        if trigger_type == "listening" and isinstance(trigger, dict) and "sources" not in trigger:
            for table in ("automation_versions", "automation_runs", "automation_daily", "automation_holds"):
                conn.execute(f"DELETE FROM {table} WHERE automation_id=?", (aid,))
            conn.execute("DELETE FROM automations WHERE id=?", (aid,))
            continue
        if isinstance(flow, dict) and _without_find(flow):
            if "enabled" in flow:
                flow["enabled"] = False
            conn.execute("UPDATE automations SET flow=?, enabled=0 WHERE id=?", (json.dumps(flow), aid))


def up(conn):
    for statement in TABLES:
        conn.execute(statement)
    for table in OLD_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    tables = _tables(conn)
    if "registry_metadata" in tables:
        conn.execute("DELETE FROM registry_metadata WHERE key='listening.market_seeded'")
    # A new database has no automations yet: the module code makes that table after the migrations.
    if "automations" in tables:
        _old_automations(conn)
