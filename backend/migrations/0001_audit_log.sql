-- The audit trail of the redesign's settings (backend/audit_log.py): one row per change, with who made it, the object it
-- changed and the whole object before and after. Rows are never edited or pruned.
CREATE TABLE IF NOT EXISTS audit_log(
 id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, object TEXT NOT NULL,
 before_json TEXT, after_json TEXT, via TEXT);
CREATE INDEX IF NOT EXISTS audit_log_object ON audit_log(object, at);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log(at);
