"""The ledger behind rules for events without a task (backend/rules.py `judge_event`); hubdb.MIGRATIONS applies it."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_events_seen (
  event TEXT NOT NULL, trigger TEXT NOT NULL, delivery TEXT NOT NULL, seen_at TEXT NOT NULL,
  PRIMARY KEY(event, trigger, delivery));
CREATE INDEX IF NOT EXISTS rule_events_seen_at ON rule_events_seen(seen_at);
CREATE TABLE IF NOT EXISTS rule_task_runs (
  rule_id TEXT NOT NULL, task_id TEXT NOT NULL, event TEXT NOT NULL, revision TEXT NOT NULL,
  ran_at TEXT NOT NULL, fired INTEGER NOT NULL DEFAULT 0, webhook_event_id TEXT,
  PRIMARY KEY(rule_id,task_id,event,revision));
CREATE INDEX IF NOT EXISTS rule_task_runs_hour ON rule_task_runs(rule_id,fired,ran_at);
CREATE INDEX IF NOT EXISTS rule_task_runs_webhook ON rule_task_runs(webhook_event_id) WHERE webhook_event_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS rule_task_state (
  rule_id TEXT PRIMARY KEY, paused_at TEXT, pause_reason TEXT,
  last_error TEXT, last_error_at TEXT);
"""
