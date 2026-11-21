-- BotOps is Agent Ops (#4863). Its id stays 'botops', so every actor string, reference and history row that names it
-- is unchanged; its slug, its default name, its template and the two team rules named after it follow the new name.
-- A name a person gave it is kept, and so is a slug another bot already holds.
UPDATE bots SET slug = 'agent-ops'
WHERE id = 'botops' AND slug = 'botops'
  AND NOT EXISTS (SELECT 1 FROM bots WHERE id = 'agent-ops' OR slug = 'agent-ops');
UPDATE bots SET display_name = 'Agent Ops' WHERE id = 'botops' AND display_name = 'BotOps';
UPDATE bot_settings SET extra = json_set(extra, '$.display_name', 'Agent Ops')
WHERE bot_id = 'botops' AND json_valid(extra) AND json_extract(extra, '$.display_name') = 'BotOps';
-- The catalog folder is templates/catalog/agent-ops now. A computer on an older release has no such folder: it leaves
-- the bot's files as they are and says so once (backend/template_refresh.py template_missing) until it updates.
UPDATE bot_settings SET template = 'agent-ops' WHERE template = 'botops';
UPDATE registry_metadata
SET value = json_remove(json_set(value, '$.agent_ops_direct', json_extract(value, '$.botops_direct')), '$.botops_direct')
WHERE key = 'rules' AND json_valid(value) AND json_type(value, '$.botops_direct') IS NOT NULL;
UPDATE registry_metadata
SET value = json_remove(
  json_set(value, '$.agent_ops_manages_bots', json_extract(value, '$.botops_manages_bots')), '$.botops_manages_bots'
)
WHERE key = 'rules' AND json_valid(value) AND json_type(value, '$.botops_manages_bots') IS NOT NULL;
