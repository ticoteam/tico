"""Team repositories and bot grants shared by local and cloud migrations."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories(
 id TEXT PRIMARY KEY, full_name TEXT UNIQUE COLLATE NOCASE,
 enabled INTEGER DEFAULT 0, bot_repo INTEGER DEFAULT 0,
 default_branch TEXT, setup_command TEXT, setup_source TEXT,
 reachable INTEGER DEFAULT 1, last_seen_at TEXT, added_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS bot_repo_access(
 bot TEXT, full_name TEXT COLLATE NOCASE,
 access TEXT CHECK(access IN ('read','write')), PRIMARY KEY(bot,full_name));
"""
