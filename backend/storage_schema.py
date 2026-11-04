"""Migration 20 / cloud 53: derived media may change; version bytes never do."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_file_versions(
 file_id TEXT NOT NULL REFERENCES bot_files(id), version INTEGER NOT NULL,
 blob_id TEXT NOT NULL REFERENCES blobs(id), digest TEXT NOT NULL, source_digest TEXT,
 size INTEGER NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL, commit_sha TEXT, repo_path TEXT,
 attempt_id TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(file_id, version));
ALTER TABLE bot_file_versions ADD COLUMN width INTEGER;
ALTER TABLE bot_file_versions ADD COLUMN height INTEGER;
ALTER TABLE bot_file_versions ADD COLUMN duration_ms INTEGER;
ALTER TABLE bot_file_versions ADD COLUMN poster_blob_id TEXT REFERENCES blobs(id);
ALTER TABLE bot_file_versions ADD COLUMN thumb_blob_id TEXT REFERENCES blobs(id);
ALTER TABLE bot_file_versions ADD COLUMN media_state TEXT NOT NULL DEFAULT 'none'
 CHECK(media_state IN ('ready','pending','none'));
CREATE TABLE IF NOT EXISTS blob_locations(
 digest TEXT NOT NULL, bucket TEXT NOT NULL, verified_at TEXT NOT NULL,
 PRIMARY KEY(digest,bucket));
CREATE TABLE IF NOT EXISTS blob_media(
 blob_id TEXT PRIMARY KEY REFERENCES blobs(id), width INTEGER, height INTEGER, duration_ms INTEGER,
 poster_blob_id TEXT REFERENCES blobs(id), thumb_blob_id TEXT REFERENCES blobs(id),
 media_state TEXT NOT NULL DEFAULT 'pending' CHECK(media_state IN ('ready','pending','none')));
CREATE TABLE IF NOT EXISTS blob_media_retries(
 blob_id TEXT PRIMARY KEY REFERENCES blobs(id), attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0);
DROP TRIGGER IF EXISTS bot_file_versions_immutable;
CREATE TRIGGER bot_file_versions_immutable BEFORE UPDATE OF
 file_id,version,blob_id,digest,source_digest,size,name,mime,commit_sha,repo_path,attempt_id,actor,created_at
 ON bot_file_versions BEGIN SELECT RAISE(ABORT, 'file versions are immutable'); END;
"""
