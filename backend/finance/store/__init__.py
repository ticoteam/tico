"""Finance store: every statement that reads or writes a `fin_*` table. Portable SQL (SQLite now, Postgres later);
the one SQLite-only piece, the invariant triggers, is kept apart in schema.TRIGGERS."""
