"""Profile names and computer sign-in state; credentials stay on computers."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS subscription_assignments(
    scope TEXT NOT NULL CHECK(scope IN ('group','bot')),
    target TEXT NOT NULL, profile TEXT NOT NULL, updated_at TEXT, updated_by TEXT,
    PRIMARY KEY(scope,target)
);
CREATE TABLE IF NOT EXISTS computer_profiles(
    runner_id TEXT NOT NULL, profile TEXT NOT NULL, runtimes TEXT, updated_at TEXT,
    PRIMARY KEY(runner_id,profile)
);
"""
