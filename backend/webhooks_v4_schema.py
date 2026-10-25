"""One column for the incoming dedupe ledger (backend/triggers.py); hubdb.MIGRATIONS applies it.

`trigger_seen.replay` is 1 for a delivery id Tico made itself when a person ran a stored call again. The hourly cap
skips those rows by this flag, never by looking for `:replay:` in the id, which a sender can put in its own ids.
"""
SCHEMA = """
ALTER TABLE trigger_seen ADD COLUMN replay INTEGER NOT NULL DEFAULT 0;
"""
