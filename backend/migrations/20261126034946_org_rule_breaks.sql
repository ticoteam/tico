-- A Warn rule's break of a bot's write (backend/rule_breaks.py): one row per rule, linked to the message it was on when
-- there is one, and the answer a person gave it ("Yes, block" is `block`, "Don't block" is `pass`), saved on the rule.
CREATE TABLE IF NOT EXISTS org_rule_breaks(
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL,
  author TEXT NOT NULL,
  operation TEXT NOT NULL,
  recipients TEXT NOT NULL DEFAULT '[]',
  first_line TEXT NOT NULL DEFAULT '',
  message_id TEXT NOT NULL DEFAULT '',
  task_id TEXT NOT NULL DEFAULT '',
  conversation_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  answer TEXT CHECK (answer IN ('block', 'pass')),
  answered_by TEXT,
  answered_at TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS org_rule_breaks_rule ON org_rule_breaks(rule_id, created_at);
CREATE INDEX IF NOT EXISTS org_rule_breaks_message ON org_rule_breaks(message_id) WHERE message_id <> '';
