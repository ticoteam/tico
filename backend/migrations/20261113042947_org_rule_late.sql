-- Org rule checks run after the write (backend/org_rule_check.py `late`): a write whose every matching rule only warns
-- is saved and answered first, and its one check runs right after. Each row is one such check still to run: what the
-- check reads (`verdict`: author, operation, audience, place; `output`: the words), and where the saved words are
-- (`target`, set by the route once it has written them), so a broken rule's warning line lands on them. Deleted once
-- the check ran; a row a restart left is picked up by the scheduler's tick.
CREATE TABLE IF NOT EXISTS org_rule_late(
 id TEXT PRIMARY KEY, verdict TEXT NOT NULL, output TEXT NOT NULL, target TEXT NOT NULL DEFAULT '{}',
 tries INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS org_rule_late_created ON org_rule_late(created_at);
