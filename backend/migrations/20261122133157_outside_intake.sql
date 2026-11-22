ALTER TABLE conversations ADD COLUMN entity_id TEXT REFERENCES entities(id);
ALTER TABLE conversations ADD COLUMN connection_id TEXT NOT NULL DEFAULT '';

ALTER TABLE messages ADD COLUMN channel TEXT;
ALTER TABLE messages ADD COLUMN direction TEXT;
ALTER TABLE messages ADD COLUMN connection_id TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN source_message_id TEXT;
CREATE UNIQUE INDEX messages_outside_delivery ON messages(connection_id, source_message_id);
CREATE UNIQUE INDEX automation_runs_intake ON automation_runs(automation_id, trigger_ref)
    WHERE trigger_ref LIKE 'intake:%';
