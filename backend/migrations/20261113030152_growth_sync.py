"""Growth fills its own numbers (backend/growth_sync.py): a source can sync, and each number knows where it came from.

- `gtm_sources.sync_kind`: what the source fetches with (`file`, `db_query`, `finance_ad_spend`, `bot_cost`); NULL is a
  source that only names a system, as before. `sync_map` (JSON) says which column holds the month, the channel and each
  number it fills. `refresh_hours`: how often a good run is due; twice that with none makes the source Stale.
  `last_run_at`, `last_good_run_at`, `last_error` and `failing_since` are its health; `last_payload` is the last file
  it was given, so a run can be started again after a remap.
- `growth_sync_runs`: one row per run, good or failed, with what it wrote, held and did not write.
- `growth_origins`: one row per cohort number: synced (by which source and run) or typed (by whom), actual or estimate,
  and a value a source brought that was held back because someone had typed an actual (`held_*`), or that the
  source's owner chose not to use (`declined_value`).
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


SOURCE_COLUMNS = (
    (
        "sync_kind",
        "TEXT CHECK (sync_kind IS NULL OR sync_kind IN ('file','db_query','finance_ad_spend','bot_cost'))",
    ),
    ("sync_map", "TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(sync_map))"),
    ("refresh_hours", "INTEGER"),
    ("last_run_at", "TEXT"),
    ("last_good_run_at", "TEXT"),
    ("last_error", "TEXT"),
    ("failing_since", "TEXT"),
    ("last_payload", "TEXT"),
)


def up(conn):
    have = _columns(conn, "gtm_sources")
    if have:
        for name, kind in SOURCE_COLUMNS:
            if name not in have:
                conn.execute(f"ALTER TABLE gtm_sources ADD COLUMN {name} {kind}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_sync_runs ("
        "id TEXT PRIMARY KEY, source_id TEXT NOT NULL, actor TEXT, started_at TEXT NOT NULL, "
        "ok INTEGER NOT NULL, error TEXT, written INTEGER NOT NULL DEFAULT 0, held INTEGER NOT NULL DEFAULT 0, "
        "not_written TEXT NOT NULL DEFAULT '[]')"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS growth_sync_runs_source ON growth_sync_runs(source_id, started_at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_origins ("
        "cohort_id TEXT NOT NULL, field TEXT NOT NULL, "
        "origin TEXT NOT NULL CHECK (origin IN ('synced','typed')), "
        "quality TEXT NOT NULL DEFAULT 'actual' CHECK (quality IN ('actual','estimate')), "
        "source_id TEXT, run_id TEXT, by TEXT, at TEXT NOT NULL, "
        "held_value REAL, held_source_id TEXT, held_run_id TEXT, held_at TEXT, declined_value REAL, "
        "PRIMARY KEY (cohort_id, field))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS growth_origins_source ON growth_origins(source_id)")
