"""Notices from Tico Team that arrived through the Gateway relay (backend/tico_notices.py); hubdb.MIGRATIONS applies it."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS tico_notices (
  notice_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
  sent_at TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL, delivery TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS tico_notices_received ON tico_notices(received_at);
"""
