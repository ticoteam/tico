"""Columns for recovering refused calls and for the outgoing endpoint's filter, headers and automatic switch-off
(backend/triggers.py, backend/webhooks.py); hubdb.MIGRATIONS applies them.

`trigger_bodies.outcome` is `accepted`, `refused` (a call that failed verification, kept so it can be recovered) or
`recovered`. `webhooks.filter` is `{"departments": [], "bots": [], "types": []}` (an empty list is all);
`headers` is a list of `{"name", "value", "secret"}` whose secret values are sealed with the credential vault;
`disabled_at` and `disabled_reason` say Tico switched the endpoint off after days of failures. `webhook_events.only_id`
names the one endpoint a targeted send (a rule, `hub webhook send`) was for, so "replay missing" never copies it elsewhere.
"""
SCHEMA = """
ALTER TABLE trigger_bodies ADD COLUMN outcome TEXT NOT NULL DEFAULT 'accepted';
ALTER TABLE webhooks ADD COLUMN filter TEXT NOT NULL DEFAULT '{}';
ALTER TABLE webhooks ADD COLUMN headers TEXT NOT NULL DEFAULT '[]';
ALTER TABLE webhooks ADD COLUMN disabled_at TEXT;
ALTER TABLE webhooks ADD COLUMN disabled_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE webhook_events ADD COLUMN only_id TEXT NOT NULL DEFAULT '';
"""
