-- History pages seek by time and order ties by the stored id, without sorting the whole source.
CREATE INDEX IF NOT EXISTS index_approvals_on_created_at_and_id ON approvals(created_at, id);
CREATE INDEX IF NOT EXISTS index_approvals_on_decision_and_decided_at_and_id ON approvals(decision, decided_at, id);
CREATE INDEX IF NOT EXISTS index_messages_on_kind_and_created_at_and_id ON messages(kind, created_at, id);
