-- Tico's order of a stage's tasks for each person who lists it by priority (backend/stage_order.py): one row per
-- person and stage, read by its key alone.
CREATE TABLE stage_orders (
 person_id TEXT NOT NULL,
 stage_id TEXT NOT NULL,
 task_ids TEXT NOT NULL DEFAULT '[]',
 sorted_at TEXT,
 started_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY (person_id, stage_id)
);
