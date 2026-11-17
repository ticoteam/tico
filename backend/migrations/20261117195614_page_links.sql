-- Links describe a page's work without changing the file's read boundary.
CREATE TABLE IF NOT EXISTS page_links (
  id TEXT PRIMARY KEY,
  page_id TEXT NOT NULL REFERENCES bot_files(id) ON DELETE CASCADE,
  target TEXT NOT NULL,
  role TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(page_id, target, role)
);
CREATE INDEX IF NOT EXISTS page_links_target ON page_links(target, role);
CREATE INDEX IF NOT EXISTS page_links_page ON page_links(page_id);
CREATE INDEX IF NOT EXISTS page_links_role ON page_links(role, page_id);

INSERT OR IGNORE INTO page_links
SELECT lower(hex(randomblob(16))), p.file_id, 'bot:' || f.bot_id, 'writer',
       'bot:' || f.bot_id, p.created_at
FROM pages p JOIN bot_files f ON f.id=p.file_id;


INSERT OR IGNORE INTO page_links
SELECT lower(hex(randomblob(16))), p.file_id, pin.place, 'related', pin.made_by, pin.created_at
FROM pins pin JOIN pages p ON pin.item='page:' || p.file_id WHERE pin.place LIKE 'task:%';

INSERT OR IGNORE INTO pages(file_id,cadence,created_at,updated_at)
SELECT d.file_id, 'none', min(d.created_at), max(d.updated_at)
FROM task_documents d JOIN bot_files f ON f.id=d.file_id GROUP BY d.file_id;
INSERT OR IGNORE INTO page_links
SELECT lower(hex(randomblob(16))), d.file_id, 'task:' || d.task_id, d.name,
       'bot:' || f.bot_id, d.created_at
FROM task_documents d JOIN bot_files f ON f.id=d.file_id;
INSERT OR IGNORE INTO page_links
SELECT lower(hex(randomblob(16))), d.file_id, 'bot:' || f.bot_id, 'writer',
       'bot:' || f.bot_id, d.created_at
FROM task_documents d JOIN bot_files f ON f.id=d.file_id;

-- Include newly adopted documents in the filed-on-task backfill as well.
INSERT OR IGNORE INTO page_links
SELECT lower(hex(randomblob(16))), p.file_id, 'task:' || f.task_id,
       CASE WHEN p.spec IS NOT NULL AND p.spec<>'' THEN 'Spec' ELSE 'related' END,
       'bot:' || f.bot_id, p.created_at
FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id IS NOT NULL AND f.task_id<>'';

INSERT OR IGNORE INTO page_links
SELECT lower(hex(randomblob(16))), v.file_id, v.actor, 'writer', v.actor, min(v.created_at)
FROM bot_file_versions v JOIN pages p ON p.file_id=v.file_id
WHERE v.actor LIKE 'human:%' GROUP BY v.file_id,v.actor;

-- A requirement submits each of its linked pages independently.
DROP INDEX IF EXISTS requirement_pages_current;
CREATE UNIQUE INDEX IF NOT EXISTS requirement_pages_current_file
ON requirement_pages(visit_id, requirement_id, file_id) WHERE replaced_at IS NULL;
CREATE INDEX IF NOT EXISTS requirement_pages_history
ON requirement_pages(visit_id, requirement_id, created_at);
