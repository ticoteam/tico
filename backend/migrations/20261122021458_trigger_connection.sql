-- A trigger bound to one connection (a `tools` row; '' for none): a Jira issue event it receives marks that
-- connection's linked copy of the issue to be read again (backend/item_sources/jira.py `webhook`), and no other.
ALTER TABLE triggers ADD COLUMN connection_id TEXT NOT NULL DEFAULT '';
