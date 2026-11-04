"""The Maintenance run: one built-in run with eight steps, in fixed order, replacing about 48 background timers
(backend/maintenance.py, docs/maintenance.md).

Five tables, with Rails names (`maintenance_run_id`, `updated_by_id`), foreign keys, CHECKs and the indexes pruning reads:

- `maintenance_steps`: one row per step key, its switch, "How often", its custom interval, "Most bots per run" and its
  cursor. Its cursor (`cursor_at`) moves only when the step returns `ran`.
- `maintenance_runs`: one row per run. `occurrence_key` is unique: a scheduled run claims its hour (or day) once, and a
  run that is live with no heartbeat for 30 minutes is recovered by renaming its key (backend/maintenance.py).
- `maintenance_run_steps`: one row per step of a run, with its status, reason and counts.
- `maintenance_listings`: Find stuck's dedupe, one row per task listed for one reason at one appointment.
- `maintenance_handoffs`: a quiet task waiting to ride along in its bot's Update turn (Update's track reads it).

The switches of the jobs that became steps are copied in, one control at a time, and only once: every row is inserted
with INSERT OR IGNORE, so a replay of this file changes nothing. An explicit choice carries over. An inherited default
takes the step's new default:
- Learn (`learnings` in Settings > Routines): Learn is off by default now, so an inherited Learn is off.
- Dream (`auto_dream`), Check health (`health_scan`) and Find stuck (`task_roundup`): an on or off the customer saved.
- The team rule `stale_review` and the `TICO_STALE_REVIEW` environment setting do not map to Find stuck. Stale review
  defaults off, so a saved off stale review says nothing about Find stuck, and it must not turn Find stuck off.

`stall_triage` is not copied: its switch is removed (team rule, environment, CONTROLS and the MCP tool).
"""
import json
from datetime import datetime, timezone

STEP_KEYS = ("collect", "clean_up", "check_health", "find_stuck", "learn", "suggest", "update", "dream")
# key, enabled by default, frequency by default, "Most bots per run" by default
DEFAULTS = (
    ("collect", 1, "run", None),
    ("clean_up", 1, "run", None),
    ("check_health", 1, "run", None),
    ("find_stuck", 1, "run", 10),
    ("learn", 0, "day", None),
    ("suggest", 1, "day", None),
    ("update", 1, "day", None),
    ("dream", 1, "day", 3),
)
KEY_LIST = ", ".join(f"'{key}'" for key in STEP_KEYS)
RUN_STATUSES = "'ran','skipped','failed','off','pending'"
LISTING_KINDS = "'blocked_by_bot','missed_start','quiet','due_follow_up','stale_review'"
HANDOFF_KINDS = "'quiet'"

SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS maintenance_steps(
 key TEXT PRIMARY KEY CHECK (key IN ({KEY_LIST})),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
 frequency TEXT NOT NULL DEFAULT 'run' CHECK (frequency IN ('run','day','week','custom')),
 custom_every INTEGER CHECK (custom_every IS NULL OR custom_every BETWEEN 1 AND 999),
 custom_unit TEXT CHECK (custom_unit IS NULL OR custom_unit IN ('hours','days')),
 max_bots INTEGER CHECK (max_bots IS NULL OR max_bots BETWEEN 0 AND 500),
 cursor_at TEXT, last_started_at TEXT, last_ran_at TEXT, updated_at TEXT, updated_by_id TEXT,
 CHECK (frequency <> 'custom' OR (custom_every IS NOT NULL AND custom_unit IS NOT NULL)))""",
    f"""CREATE TABLE IF NOT EXISTS maintenance_runs(
 id TEXT PRIMARY KEY,
 occurrence_key TEXT NOT NULL UNIQUE,
 trigger TEXT NOT NULL CHECK (trigger IN ('schedule','run_now')),
 step TEXT CHECK (step IS NULL OR step IN ({KEY_LIST})),
 started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, finished_at TEXT,
 ok INTEGER CHECK (ok IS NULL OR ok IN (0,1)),
 result TEXT NOT NULL DEFAULT '',
 cost_usd REAL NOT NULL DEFAULT 0)""",
    f"""CREATE TABLE IF NOT EXISTS maintenance_run_steps(
 id TEXT PRIMARY KEY,
 maintenance_run_id TEXT NOT NULL REFERENCES maintenance_runs(id) ON DELETE CASCADE,
 step TEXT NOT NULL REFERENCES maintenance_steps(key),
 status TEXT NOT NULL CHECK (status IN ({RUN_STATUSES})),
 reason TEXT NOT NULL DEFAULT '',
 checked INTEGER NOT NULL DEFAULT 0, found INTEGER NOT NULL DEFAULT 0, woke INTEGER NOT NULL DEFAULT 0,
 cost_usd REAL NOT NULL DEFAULT 0,
 started_at TEXT NOT NULL, finished_at TEXT,
 window_end TEXT,
 details TEXT NOT NULL DEFAULT '{{}}')""",
    f"""CREATE TABLE IF NOT EXISTS maintenance_listings(
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK (kind IN ({LISTING_KINDS})),
 appointment TEXT NOT NULL,
 bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
 listed_at TEXT NOT NULL,
 PRIMARY KEY (task_id, kind, appointment))""",
    f"""CREATE TABLE IF NOT EXISTS maintenance_handoffs(
 id TEXT PRIMARY KEY,
 bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK (kind IN ({HANDOFF_KINDS})),
 created_at TEXT NOT NULL,
 delivered_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS maintenance_runs_started ON maintenance_runs(started_at)",
    "CREATE INDEX IF NOT EXISTS maintenance_runs_live ON maintenance_runs(heartbeat_at) WHERE finished_at IS NULL",
    "CREATE INDEX IF NOT EXISTS maintenance_run_steps_run ON maintenance_run_steps(maintenance_run_id)",
    "CREATE INDEX IF NOT EXISTS maintenance_run_steps_started ON maintenance_run_steps(started_at)",
    "CREATE INDEX IF NOT EXISTS maintenance_listings_bot ON maintenance_listings(bot_id, listed_at)",
    "CREATE INDEX IF NOT EXISTS maintenance_handoffs_open ON maintenance_handoffs(bot_id) WHERE delivered_at IS NULL",
)


def _stored(conn, key):
    """A registry_metadata JSON object by key; {} when the table or the key is not there, or it is not an object."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_metadata'").fetchone():
        return {}
    row = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        stored = {}
    return stored if isinstance(stored, dict) else {}


def _flag(entry, name):
    """A saved on/off in a CONTROLS entry, or None when the customer never chose one."""
    value = entry.get(name) if isinstance(entry, dict) else None
    value = value.get("on") if isinstance(value, dict) else None
    return value if isinstance(value, bool) else None


def switches(registry):
    """{step key: on or off} for every step with an explicit choice. A step not in the result takes its default."""
    out = {}
    for control, step in (("learnings", "learn"), ("auto_dream", "dream"), ("health_scan", "check_health"),
                          ("task_roundup", "find_stuck")):
        value = _flag(registry, control)
        if value is not None:
            out[step] = value
    return out


def up(conn):
    for statement in SCHEMA:
        conn.execute(statement)
    chosen = switches(_stored(conn, "system_routines"))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    for key, enabled, frequency, max_bots in DEFAULTS:
        conn.execute(
            "INSERT OR IGNORE INTO maintenance_steps(key,enabled,frequency,max_bots,updated_at,updated_by_id) "
            "VALUES(?,?,?,?,?,?)",
            (key, int(chosen.get(key, bool(enabled))), frequency, max_bots, stamp, "migration"))
