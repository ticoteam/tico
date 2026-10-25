"""Columns for how long an endpoint has been failing and whether a Gateway address wakes a sleeping server
(backend/webhooks.py, backend/hook_relay.py); hubdb.MIGRATIONS applies them.

`webhooks.failing_since` is the time of the first failed attempt of the current run: set when empty, cleared by any 2xx
answer and by turning the endpoint on. `trigger_relays.wake` is HQ's `wake` switch for the relay as last listed
(1, 0, or NULL while HQ has not said).
"""
SCHEMA = """
ALTER TABLE webhooks ADD COLUMN failing_since TEXT;
ALTER TABLE trigger_relays ADD COLUMN wake INTEGER;
"""
