"""Per-pipeline caps and one-time notices/decision tasks (backend/pipeline_budgets.py)."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    columns = _columns(conn, "task_types")
    if columns:
        for name in ("daily_cap_nano", "monthly_cap_nano"):
            if name not in columns:
                conn.execute(f"ALTER TABLE task_types ADD COLUMN {name} INTEGER CHECK({name} IS NULL OR {name} > 0)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_budget_alerts ("
        "pipeline_id TEXT NOT NULL, period TEXT NOT NULL CHECK(period IN ('daily', 'monthly')), "
        "period_key TEXT NOT NULL, cap_nano INTEGER NOT NULL CHECK(cap_nano > 0), "
        "level INTEGER NOT NULL CHECK(level IN (80, 100)), decision_task_id TEXT, request_task_id TEXT, "
        "created_at TEXT NOT NULL, PRIMARY KEY(pipeline_id, period, period_key, cap_nano, level))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS pipeline_budget_alerts_decision ON pipeline_budget_alerts(decision_task_id) "
        "WHERE decision_task_id IS NOT NULL"
    )
