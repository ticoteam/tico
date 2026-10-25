"""The ledger behind rules for events without a task (backend/rules.py `judge_event`); hubdb.MIGRATIONS applies it."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_events_seen (
  event TEXT NOT NULL, trigger TEXT NOT NULL, delivery TEXT NOT NULL, seen_at TEXT NOT NULL,
  PRIMARY KEY(event, trigger, delivery));
CREATE INDEX IF NOT EXISTS rule_events_seen_at ON rule_events_seen(seen_at);
"""
