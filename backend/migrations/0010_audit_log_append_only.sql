-- The audit trail is append-only in the database (backend/audit_log.py): no statement can rewrite or remove a recorded
-- change. The table is migration 0001, which runs first on every database. Devcopy's history cut drops these triggers
-- while it prunes and puts them back from sqlite_master, so a dev copy keeps the same triggers.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
