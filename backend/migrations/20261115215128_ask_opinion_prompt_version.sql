-- Which instructions wrote each of Flow's opinions (backend/flow_opinions.py PROMPT_VERSION; 0 for every row written
-- before it was kept): a ready line from earlier instructions that today's would answer differently is worked out
-- again. Kept apart from `ask_version`, which a person's snooze of the ask also holds.
ALTER TABLE ask_opinions ADD COLUMN prompt_version INTEGER NOT NULL DEFAULT 0;
