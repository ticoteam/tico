-- BotOps is called Agent Ops (master plan W5, #4857). Its slug and id stay `botops`: the code, every actor string
-- (`bot:botops`), its repository and its label name it by that, and a built-in keeps its slug (settings_admin.rename).
-- Only the old default name changes, in both places it is kept; a name someone chose since is left alone.
UPDATE bots SET display_name = 'Agent Ops' WHERE id = 'botops' AND display_name = 'BotOps';
UPDATE bot_settings SET extra = json_set(extra, '$.display_name', 'Agent Ops')
  WHERE bot_id = 'botops' AND json_valid(extra) AND json_extract(extra, '$.display_name') = 'BotOps';
