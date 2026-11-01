"""Rule suggestions were the old Rules page's "make this a note" cards and their weekly job. Wake rules are "What wakes"
automations now (backend/automations.py), so nothing suggests or applies one; the table stays for its history."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_suggestions(
 id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, kind TEXT NOT NULL, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '',
 event TEXT NOT NULL, runs INTEGER NOT NULL, no_op INTEGER NOT NULL, revision INTEGER, status TEXT NOT NULL DEFAULT 'open',
 created_at TEXT NOT NULL, decided_by TEXT, decided TEXT, made_rule_id TEXT, before TEXT);
CREATE INDEX IF NOT EXISTS rule_suggestions_rule ON rule_suggestions(rule_id, status);
"""
