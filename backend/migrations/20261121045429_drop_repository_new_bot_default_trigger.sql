-- A new bot's repository access is chosen in app code before its first write (settings_admin.create_bot, and
-- bot_settings.write for every other new row). The trigger copied New agents get straight into the mode, which its
-- `read` value (All repos in use with Read) would break.
DROP TRIGGER IF EXISTS repository_new_bot_default;
