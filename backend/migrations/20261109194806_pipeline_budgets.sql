-- Per-pipeline caps and one-time notices/decision tasks (backend/pipeline_budgets.py).
ALTER TABLE task_types ADD COLUMN daily_cap_nano INTEGER CHECK(daily_cap_nano IS NULL OR daily_cap_nano > 0);
ALTER TABLE task_types ADD COLUMN monthly_cap_nano INTEGER CHECK(monthly_cap_nano IS NULL OR monthly_cap_nano > 0);

CREATE TABLE pipeline_budget_alerts (
    pipeline_id TEXT NOT NULL,
    period TEXT NOT NULL CHECK(period IN ('daily', 'monthly')),
    period_key TEXT NOT NULL,
    cap_nano INTEGER NOT NULL CHECK(cap_nano > 0),
    level INTEGER NOT NULL CHECK(level IN (80, 100)),
    decision_task_id TEXT,
    request_task_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(pipeline_id, period, period_key, cap_nano, level)
);
CREATE INDEX pipeline_budget_alerts_decision ON pipeline_budget_alerts(decision_task_id)
    WHERE decision_task_id IS NOT NULL;
