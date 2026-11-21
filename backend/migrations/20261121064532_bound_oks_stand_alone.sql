-- An OK bound to an action (task_asks.ask_ok: an email, a letter, a spend) is keyed by its subject and content, never
-- replaced by the bot's next ask on the task (task_engine._replace_open): it is marked bound in its settings and left
-- out of the one-ask-per-bot index, so a send's OK can wait on the case itself beside the bot's other asks.
UPDATE requirements SET settings = json_set(settings, '$.bound', json('true'))
WHERE id IN (SELECT requirement_id FROM ok_grants) AND json_extract(settings, '$.bound') IS NULL;
DROP INDEX IF EXISTS requirements_one_per_bot;
CREATE UNIQUE INDEX requirements_one_per_bot ON requirements(visit_id, added_by_id)
WHERE visit_id IS NOT NULL AND by_bot = 1 AND withdrawn_at IS NULL AND json_extract(settings, '$.bound') IS NULL;
