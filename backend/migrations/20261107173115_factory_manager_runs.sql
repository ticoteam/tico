-- Pipeline D: the Factory Manager's run log (one pass per factory every 30 minutes, backend/factory_manager.py).
-- Stands alone: it names only its own tables. The factory's manager_id and goal columns come from Track A's migration.
CREATE TABLE IF NOT EXISTS factory_manager_runs (
    id TEXT PRIMARY KEY,
    factory_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('ok','failed')),
    bottleneck_stage_id TEXT,
    judgment_count INTEGER NOT NULL DEFAULT 0,
    wake_key TEXT,
    woke INTEGER NOT NULL DEFAULT 0,
    model_called INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS factory_manager_runs_factory ON factory_manager_runs(factory_id, started_at);
CREATE TABLE IF NOT EXISTS factory_manager_actions (
    id TEXT PRIMARY KEY,
    factory_id TEXT NOT NULL,
    factory_manager_run_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('ask_manager','open_botops','propose_rule','post_note')),
    target_actor TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS factory_manager_actions_factory ON factory_manager_actions(factory_id, created_at);
