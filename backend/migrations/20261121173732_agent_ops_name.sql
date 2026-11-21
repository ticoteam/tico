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
-- A seed from before the catalog gave the built-in no template; it names its own now, so an older computer does not
-- fall back to the template it carries under the old name, and the Gateway's count and roles find it.
UPDATE bot_settings SET template = 'agent-ops' WHERE bot_id = 'botops' AND COALESCE(template, '') = '';
INSERT INTO bot_settings (bot_id, template, extra)
SELECT 'botops', 'agent-ops', '{}'
WHERE EXISTS (SELECT 1 FROM bots WHERE id = 'botops') AND NOT EXISTS (SELECT 1 FROM bot_settings WHERE bot_id = 'botops');
-- Setup keeps each pick under its bot's id: re-completing setup builds the built-in from its new template, under the
-- new name unless a person chose another (their name and instructions stay).
UPDATE registry_metadata SET value = json_set(value, '$.selected.botops.template', 'agent-ops')
WHERE key = 'onboarding' AND json_valid(value) AND json_extract(value, '$.selected.botops.template') = 'botops';
UPDATE registry_metadata SET value = json_set(value, '$.selected.botops.display_name', 'Agent Ops')
WHERE key = 'onboarding' AND json_valid(value) AND json_extract(value, '$.selected.botops.display_name') = 'BotOps';
-- A rule keeps its JSON true or false under its new name (json_extract alone would give 1 or 0).
UPDATE registry_metadata
SET value = json_remove(
  json_set(value, '$.agent_ops_direct', CASE json_type(value, '$.botops_direct')
    WHEN 'true' THEN json('true') WHEN 'false' THEN json('false') ELSE json_extract(value, '$.botops_direct') END),
  '$.botops_direct'
)
WHERE key = 'rules' AND json_valid(value) AND json_type(value, '$.botops_direct') IS NOT NULL;
UPDATE registry_metadata
SET value = json_remove(
  json_set(value, '$.agent_ops_manages_bots', CASE json_type(value, '$.botops_manages_bots')
    WHEN 'true' THEN json('true') WHEN 'false' THEN json('false') ELSE json_extract(value, '$.botops_manages_bots') END),
  '$.botops_manages_bots'
)
WHERE key = 'rules' AND json_valid(value) AND json_type(value, '$.botops_manages_bots') IS NOT NULL;
