"""One column for the incoming delivery log (backend/triggers.py); hubdb.MIGRATIONS applies it.

`trigger_deliveries.rule_results` is the JSON list of what the rules did with a call, one `{rule_id, outcome, target,
ok, detail}` each, so a rule that failed (a wake for a planned bot) shows why on the delivery. `rules` counts only the
ones that worked.
"""
SCHEMA = """
ALTER TABLE trigger_deliveries ADD COLUMN rule_results TEXT NOT NULL DEFAULT '';
"""
