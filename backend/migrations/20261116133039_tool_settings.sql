-- A tool's Settings (backend/tool_settings.py): non-secret values such as a host, a region, a profile or a project id,
-- each an environment variable `name` with its plain `value`. Shown in the clear on the tool, changed by whoever manages
-- the tool, and delivered to every run that holds any level on the tool. Never a key: no ciphertext, no grant.
CREATE TABLE IF NOT EXISTS tool_settings (
  id TEXT PRIMARY KEY,
  tool_id TEXT NOT NULL REFERENCES tools(id),
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS tool_settings_tool_name ON tool_settings(tool_id, name);
