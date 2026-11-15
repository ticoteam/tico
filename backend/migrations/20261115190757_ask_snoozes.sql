-- A person's snooze of one ask in their own Needs you (backend/ask_snoozes.py): the ask (its visit and requirement, as
-- waiting_on.ask_id names it) leaves that person's list, count and Flow until `snoozed_until`; everyone else's list, the
-- task and its bot are unchanged. `asked_at` is when the ask was asked as Needs you read it then (its `since`): once the
-- ask is asked again it no longer matches, and the ask is back. `ask_version` is what the ask asked then (its words,
-- choices and each page it shows at its version, waiting_on.ask_version): once it asks something else, it is back
-- too. One row per person and ask; an ended snooze is read as none, so nothing has to clear it.
CREATE TABLE IF NOT EXISTS ask_snoozes (
  id INTEGER PRIMARY KEY,
  person_id TEXT NOT NULL,
  visit_id TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  asked_at TEXT NOT NULL,
  ask_version TEXT NOT NULL,
  snoozed_until TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ask_snoozes_person_ask ON ask_snoozes(person_id, visit_id, requirement_id);
CREATE INDEX IF NOT EXISTS ask_snoozes_task ON ask_snoozes(task_id);
