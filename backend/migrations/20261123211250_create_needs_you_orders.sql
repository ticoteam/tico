-- Tico's order of each person's Needs you (backend/needs_order.py): one row per person.
CREATE TABLE needs_you_orders (
 person_id TEXT PRIMARY KEY NOT NULL,
 item_ids TEXT NOT NULL DEFAULT '[]',
 sorted_at TEXT,
 started_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
