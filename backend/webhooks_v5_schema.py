"""Where Tico HQ is asked to knock when a webhook waits for a server that is off (backend/hook_relay.py); hubdb.MIGRATIONS
applies it.

`hook_relay_state.wake_url` is the address the owner gave HQ (contract C10), with who set it and when. HQ signs the call
and keeps the secret; this server never stores it.
"""
SCHEMA = """
ALTER TABLE hook_relay_state ADD COLUMN wake_url TEXT;
ALTER TABLE hook_relay_state ADD COLUMN wake_url_set_at TEXT;
ALTER TABLE hook_relay_state ADD COLUMN wake_url_set_by TEXT;
"""
