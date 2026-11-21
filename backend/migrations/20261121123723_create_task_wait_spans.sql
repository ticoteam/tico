-- How long each task waited on what (backend/wait_spans.py): one row per stretch in which its headline wait
-- (backend/waiting_on.py) kept the same visit, kind and who. `kind` is the wait's state (person, bot, paused, source,
-- tasks, room, snoozed ...) and `who` its who: an actor, a task id, a stage id or a source, as waiting_on says it.
-- A task has at most one open row (closed_at NULL); a row is closed, never changed otherwise.
CREATE TABLE IF NOT EXISTS task_wait_spans (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  visit_id TEXT,
  kind TEXT NOT NULL,
  who TEXT,
  opened_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS index_task_wait_spans_on_task_id_open ON task_wait_spans(task_id)
  WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS index_task_wait_spans_on_task_id_and_opened_at ON task_wait_spans(task_id, opened_at);
