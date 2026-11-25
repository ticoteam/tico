-- Three general additions to Files (backend/file_details.py, docs/files.md): the company's tags on files, one JSON
-- object of a team's own fields per file (kept across versions), and the file a file was made from. Metadata has its
-- own table so the many `bot_files` reads (lists, visibility checks) never carry up to 16 KB a row. The tag index
-- serves the Library's `tag=` filter; the partial parent index serves a purge clearing its children's link.
CREATE TABLE file_tags (
  file_id TEXT NOT NULL REFERENCES bot_files(id),
  tag_id TEXT NOT NULL REFERENCES tags(id),
  PRIMARY KEY(file_id, tag_id));
CREATE INDEX file_tags_tag ON file_tags(tag_id, file_id);
CREATE TABLE file_metadata (
  file_id TEXT PRIMARY KEY REFERENCES bot_files(id),
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL);
ALTER TABLE bot_files ADD COLUMN parent_id TEXT REFERENCES bot_files(id);
CREATE INDEX bot_files_parent ON bot_files(parent_id) WHERE parent_id IS NOT NULL;
