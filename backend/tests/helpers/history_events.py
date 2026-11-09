"""Helpers that other tests share, moved out of test_history_events.py so they import no test file."""

import importlib.util
from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0064_history_events.py"


OLD = (
    "CREATE TABLE task_events (id TEXT PRIMARY KEY, task_id TEXT, created_at TEXT, actor TEXT, field TEXT, old TEXT, "
    "new TEXT, note TEXT, action_id TEXT, via TEXT)",
    "CREATE TABLE goal_events (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL, "
    "field TEXT NOT NULL, old TEXT, new TEXT, note TEXT NOT NULL DEFAULT '', status_by TEXT, status_source TEXT)",
    "CREATE TABLE market_events (id TEXT PRIMARY KEY, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, "
    "created_at TEXT NOT NULL, actor TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT, insight_id TEXT, "
    "note TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE company_events (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL, field TEXT NOT NULL, "
    "old TEXT NOT NULL DEFAULT '', new TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE strategy_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, "
    "payload TEXT NOT NULL DEFAULT '{}')",
)


def migration():
    spec = importlib.util.spec_from_file_location("history_events_0064", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
