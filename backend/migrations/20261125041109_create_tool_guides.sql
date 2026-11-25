-- The Tools registry (backend/tool_guides.py): one row per tool the company keeps words on, a vendor's (its slug the
-- vendor id tool_services.vendor_of computes) or one built here. An empty field reads the shipped guide's
-- (templates/tool-guides/<slug>.md); `guidance` is the company's own. JSON lists: `commands` the program or MCP server
-- names that identify it in runs, `needs` [{tool, level}] a built tool's own tools. Never a credential value.
CREATE TABLE tool_guides (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'vendor' CHECK (origin IN ('vendor','built')),
  category TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '',
  guidance TEXT NOT NULL DEFAULT '',
  commands TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT '',
  needs TEXT NOT NULL DEFAULT '[]',
  owner TEXT,
  created_by TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);
-- The tools an agent lists as its own, each with what it uses it for. One row per agent and tool.
CREATE TABLE bot_tool_guides (
  id TEXT PRIMARY KEY,
  bot TEXT NOT NULL,
  tool_guide_id TEXT NOT NULL REFERENCES tool_guides(id),
  note TEXT NOT NULL DEFAULT '',
  added_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX index_bot_tool_guides_on_bot_and_tool_guide_id ON bot_tool_guides(bot, tool_guide_id);
CREATE INDEX index_bot_tool_guides_on_tool_guide_id ON bot_tool_guides(tool_guide_id);
