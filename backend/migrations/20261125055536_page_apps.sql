-- Page apps (backend/page_apps.py, docs/pages.md#apps): what a page version may read through Tico's bridge, declared
-- at publish and approved with the version, and the page's own data rows, which belong to the page, not a version.
ALTER TABLE bot_file_versions ADD COLUMN app TEXT;
CREATE TABLE page_app_rows(
 page_id TEXT NOT NULL REFERENCES pages(file_id),
 collection TEXT NOT NULL,
 row_id TEXT NOT NULL,
 data TEXT NOT NULL,
 updated_by TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(page_id, collection, row_id)) WITHOUT ROWID;
